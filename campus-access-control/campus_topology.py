#!/usr/bin/env python3
"""Campus access-control topology (Mininet, OpenFlow 1.3, remote Ryu controller).

                 +-----------------------------+
                 |  s3  (server distribution)  |
                 |  dpid 0x1003                |
                 +--p1-----p2-----p3-----p4----+
                    |      |      |      |
                   h5     h6      |      |
              academic   admin    |      |
                                  |      |
            +--------p3-----------+      +----------p3--------+
            |  s1  (student access)  |   |  s2  (faculty access) |
            |  dpid 0x1001           |   |  dpid 0x1002          |
            +--p1-------p2-----------+   +--p1-------p2---------+
               |        |                   |        |
              h1       h2                  h3       h4
            student  student             faculty  faculty

The topology is a loop-free tree, so no spanning tree protocol is needed.
Port numbers are pinned explicitly because the controller binds every lab host
identity (MAC + IP) to one (switch, port) attachment point.  If you change the
wiring here you MUST change LAB_HOSTS / INTER_SWITCH_LINKS in
campus_access_controller.py as well.

Run (Ubuntu VM, controller already running):
    sudo python3 campus_topology.py
"""

import argparse
import os
import sys
from functools import partial

from mininet.cli import CLI
from mininet.log import error, info, setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

DEFAULT_CONTROLLER_IP = "127.0.0.1"
DEFAULT_CONTROLLER_PORT = 6653

# Datapath IDs in the 0x100x range identify the campus lab.  The campus
# controller refuses any other DPID, so it never programs a switch from some
# other Mininet topology by mistake.
SWITCHES = {
    "s1": "0000000000001001",  # student access switch
    "s2": "0000000000001002",  # faculty access switch
    "s3": "0000000000001003",  # server distribution switch
}

# name -> addressing, role and attachment point (switch, switch port)
HOSTS = {
    "h1": {"ip": "10.0.0.1/24", "mac": "00:00:00:00:00:01", "role": "student", "switch": "s1", "port": 1},
    "h2": {"ip": "10.0.0.2/24", "mac": "00:00:00:00:00:02", "role": "student", "switch": "s1", "port": 2},
    "h3": {"ip": "10.0.0.3/24", "mac": "00:00:00:00:00:03", "role": "faculty", "switch": "s2", "port": 1},
    "h4": {"ip": "10.0.0.4/24", "mac": "00:00:00:00:00:04", "role": "faculty", "switch": "s2", "port": 2},
    "h5": {"ip": "10.0.0.5/24", "mac": "00:00:00:00:00:05", "role": "academic_server", "switch": "s3", "port": 1},
    "h6": {"ip": "10.0.0.6/24", "mac": "00:00:00:00:00:06", "role": "admin_server", "switch": "s3", "port": 2},
}

# (switch_a, port_a, switch_b, port_b)
UPLINKS = [
    ("s1", 3, "s3", 3),
    ("s2", 3, "s3", 4),
]

SERVICE_HOSTS = {"h5": "ACADEMIC", "h6": "ADMIN"}
SERVICE_TCP_PORT = 8000
SERVICE_UDP_PORT = 9000
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SERVICE_SCRIPT = os.path.join(PROJECT_DIR, "lab_service.py")


class CampusTopo(Topo):
    """Three OpenFlow 1.3 switches, four users and two servers."""

    def build(self):
        for name, dpid in SWITCHES.items():
            # failMode='secure': if the controller is unreachable the switch
            # does NOT fall back to normal L2 learning, so the access policy
            # cannot be bypassed by killing the controller.
            self.addSwitch(name, dpid=dpid, protocols="OpenFlow13", failMode="secure")
        for name, spec in HOSTS.items():
            self.addHost(name, ip=spec["ip"], mac=spec["mac"])
            self.addLink(name, spec["switch"], port1=0, port2=spec["port"])
        for sw_a, port_a, sw_b, port_b in UPLINKS:
            self.addLink(sw_a, sw_b, port1=port_a, port2=port_b)


def build_network(controller_ip=DEFAULT_CONTROLLER_IP,
                  controller_port=DEFAULT_CONTROLLER_PORT,
                  datapath="kernel"):
    """Create (but do not start) the campus Mininet network."""
    net = Mininet(
        topo=CampusTopo(),
        switch=partial(OVSSwitch, datapath=datapath),
        controller=None,
        autoSetMacs=False,
        autoStaticArp=False,  # ARP must go through the controller's policy
        waitConnected=False,
    )
    net.addController("c0", controller=RemoteController,
                      ip=controller_ip, port=controller_port)
    for host in net.hosts:
        prepare_host(host, datapath)
    return net


def prepare_host(host, datapath):
    """Disable IPv6 (keeps logs/flow counters free of ND/MLD noise) and, for the
    userspace datapath, disable TX checksum offload so TCP/UDP checksums are
    computed before frames reach ovs-vswitchd."""
    for key in ("all", "default", "lo"):
        host.cmd(f"sysctl -qw net.ipv6.conf.{key}.disable_ipv6=1")
    if datapath == "user":
        out = host.cmd(f"ethtool -K {host.defaultIntf()} tx off 2>&1")
        if "not found" in out:
            error(f"*** ethtool missing; TCP may fail on the userspace datapath: {out}\n")


def start_services(net):
    """Start the TCP/UDP lab services on the two servers.  Returns {host: pid}."""
    pids = {}
    for name, label in SERVICE_HOSTS.items():
        host = net.get(name)
        log_file = service_log_path(name)
        host.cmd(f"rm -f {log_file}")
        pid = host.cmd(
            f"python3 {SERVICE_SCRIPT} --name {label} --tcp-port {SERVICE_TCP_PORT} "
            f"--udp-port {SERVICE_UDP_PORT} --log {log_file} "
            f"> /tmp/campus_{name}_service.out 2>&1 & echo $!"
        )
        # The host shell is interactive, so job control prints "[1] <pid>"
        # before $!; the last token is the PID.
        pids[name] = int(pid.split()[-1])
    return pids


def wait_for_services(net, timeout=10.0):
    """Block until every service reports it is listening (readiness, not a fixed sleep)."""
    for name in SERVICE_HOSTS:
        host = net.get(name)
        out = host.cmd(
            f"timeout {timeout} sh -c 'until ss -ltn | grep -q :{SERVICE_TCP_PORT} && "
            f"ss -lun | grep -q :{SERVICE_UDP_PORT}; do sleep 0.1; done' && echo READY"
        )
        if "READY" not in out:
            raise RuntimeError(f"lab service on {name} did not start; see /tmp/campus_{name}_service.out")


def stop_services(net, pids):
    for name, pid in pids.items():
        net.get(name).cmd(f"kill {pid} 2>/dev/null")


def service_log_path(host_name):
    return f"/tmp/campus_{host_name}_service.log"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--controller-ip", default=DEFAULT_CONTROLLER_IP)
    parser.add_argument("--controller-port", type=int, default=DEFAULT_CONTROLLER_PORT)
    parser.add_argument("--datapath", choices=("kernel", "user"), default="kernel",
                        help="OVS datapath; 'user' is a fallback when the openvswitch kernel module is unavailable")
    parser.add_argument("--no-services", action="store_true", help="do not start TCP/UDP services on h5/h6")
    return parser.parse_args()


def main():
    args = parse_args()
    if os.geteuid() != 0:
        sys.exit("Mininet needs root: run with sudo")
    setLogLevel("info")
    net = build_network(args.controller_ip, args.controller_port, args.datapath)
    pids = {}
    try:
        net.start()
        info(f"*** Waiting for switches to connect to {args.controller_ip}:{args.controller_port}\n")
        if not net.waitConnected(timeout=15):
            error("*** Switches did not connect. Is campus_access_controller.py running?\n")
        if not args.no_services:
            pids = start_services(net)
            wait_for_services(net)
            info(f"*** Services: h5/h6 TCP {SERVICE_TCP_PORT}, UDP {SERVICE_UDP_PORT}\n")
            info(f"*** Try: h1 python3 {PROJECT_DIR}/lab_client.py tcp 10.0.0.6 {SERVICE_TCP_PORT}\n")
        CLI(net)
    finally:
        stop_services(net, pids)
        net.stop()


if __name__ == "__main__":
    main()
