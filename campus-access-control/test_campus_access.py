#!/usr/bin/env python3
"""Automated access-policy tests for the campus SDN lab.

Prerequisite: campus_access_controller.py is running and reachable
(scripts/run_tests.sh starts it for you).

    sudo python3 test_campus_access.py [--controller-log ../logs/campus_controller.log]

What is tested
  1. Pipeline sanity     - each switch carries the campus rules (right controller),
                           explicit deny rules, explicit table-miss drops and
                           no FLOOD/NORMAL fallback.
  2. Policy matrix       - every user x both servers x ICMP, TCP, UDP.
                           TCP/UDP use real services on h5/h6; an "allowed"
                           result needs the banner AND a server-side log entry,
                           a "blocked" result needs a client failure AND no
                           server-side log entry.
  3. Bypass attempts     - static ARP entry, forged source IP, forged MAC+IP,
                           and the admin server initiating towards a student.
  4. General connectivity- same-switch and cross-switch host pairs.
  5. Data-plane evidence - deny-rule packet counters increased on the switches.

Exit status is 0 only if every test passes.  A Markdown and a JSON report are
written to --report-dir.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import campus_topology as topo  # noqa: E402
from mininet.log import setLogLevel  # noqa: E402

CLIENT_SCRIPT = os.path.join(topo.PROJECT_DIR, "lab_client.py")
ALLOWED, BLOCKED = "Allowed", "Blocked"
SERVER_NAMES = {"h5": "Academic server", "h6": "Administrative server"}


@dataclass
class TestResult:
    test_id: str
    source: str
    destination: str
    protocol: str
    expected: str
    actual: str
    passed: bool
    evidence: str


def host_label(name):
    return f"{name} ({topo.HOSTS[name]['role']})"


def host_ip(name):
    return topo.HOSTS[name]["ip"].split("/")[0]


def sh(command):
    """Run a command in the root namespace; return combined output (never raises)."""
    proc = subprocess.run(command, shell=True, capture_output=True, text=True)
    return (proc.stdout + proc.stderr).strip()


def condense(text, limit=240):
    text = " | ".join(line.strip() for line in text.strip().splitlines() if line.strip())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class CampusTester:
    def __init__(self, net):
        self.net = net
        self.results = []

    # ------------------------------------------------------------- recording
    def record(self, test_id, source, destination, protocol, expected, actual, evidence):
        result = TestResult(test_id, source, destination, protocol, expected, actual,
                            expected == actual, condense(evidence))
        self.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {test_id:<10} {source:<26} -> {destination:<30} {protocol:<5} "
              f"expected={expected:<8} actual={actual}", flush=True)
        if not result.passed:
            print(f"         evidence: {result.evidence}", flush=True)

    # ----------------------------------------------------------- primitives
    def ping(self, src, dst_ip, count=3):
        out = self.net.get(src).cmd(f"ping -c {count} -i 0.2 -W 1 {dst_ip}")
        match = re.search(r"(\d+) received", out)
        received = int(match.group(1)) if match else 0
        return received, f"ping -c {count} {dst_ip}: {received}/{count} received. " + out[-160:]

    def probe(self, src, proto, dst_ip):
        port = topo.SERVICE_TCP_PORT if proto == "TCP" else topo.SERVICE_UDP_PORT
        cmd = f"python3 {CLIENT_SCRIPT} {proto.lower()} {dst_ip} {port} --timeout 2"
        out = self.net.get(src).cmd(cmd).strip()
        return out.startswith("RESULT=OK"), f"{src}$ {cmd} -> {out}"

    @staticmethod
    def server_log_lines(server):
        try:
            with open(topo.service_log_path(server)) as handle:
                return handle.read().splitlines()
        except FileNotFoundError:
            return []

    def server_received_since(self, server, offset, proto, src_ip):
        new_lines = self.server_log_lines(server)[offset:]
        return [line for line in new_lines if line.startswith(f"{proto} {src_ip}:")]

    # ----------------------------------------------------------- test units
    def check_icmp(self, test_id, src, dst, expected, src_desc=None, dst_ip=None):
        received, evidence = self.ping(src, dst_ip or host_ip(dst))
        actual = ALLOWED if received > 0 else BLOCKED
        self.record(test_id, src_desc or host_label(src), self.dst_desc(dst), "ICMP",
                    expected, actual, evidence)

    def check_service(self, test_id, src, server, proto, expected, src_desc=None, claimed_ip=None):
        """TCP/UDP check corroborated by the server-side log."""
        offset = len(self.server_log_lines(server))
        client_ok, evidence = self.probe(src, proto, host_ip(server))
        seen = self.server_received_since(server, offset, proto, claimed_ip or host_ip(src))
        evidence += f" | server {server} log: " + (f"received {seen[0]}" if seen else "no packet from source")
        if client_ok and seen:
            actual = ALLOWED
        elif not client_ok and not seen:
            actual = BLOCKED
        else:
            # e.g. request reached the server but the reply was lost
            actual = "Inconsistent"
        self.record(test_id, src_desc or host_label(src), self.dst_desc(server, proto),
                    proto, expected, actual, evidence)

    @staticmethod
    def dst_desc(dst, proto=None):
        name = SERVER_NAMES.get(dst, topo.HOSTS[dst]["role"])
        if proto == "TCP":
            return f"{dst} {name} :{topo.SERVICE_TCP_PORT}"
        if proto == "UDP":
            return f"{dst} {name} :{topo.SERVICE_UDP_PORT}"
        return f"{dst} {name}"

    # ----------------------------------------------------------- test groups
    def test_pipeline(self):
        print("\n== 1. Pipeline sanity (flow tables) ==")
        for index, switch in enumerate(sorted(topo.SWITCHES), start=1):
            flows = sh(f"ovs-ofctl -O OpenFlow13 dump-flows {switch}")
            campus_rules = len(re.findall(r"cookie=0xca0[0-4]", flows))
            deny_rules = re.findall(r"cookie=0xca03.*priority=1000.*actions=drop", flows)
            unsafe = re.findall(r"actions=.*(FLOOD|NORMAL|ALL)\b", flows)
            misses = re.findall(r"cookie=0xca00.*priority=0 actions=drop", flows)
            ok = campus_rules > 0 and len(deny_rules) == 4 and not unsafe and len(misses) == 3
            evidence = (f"{campus_rules} campus rules, {len(deny_rules)} deny rules (expect 4), "
                        f"{len(misses)} table-miss drops (expect 3), "
                        f"{len(unsafe)} FLOOD/NORMAL rules (expect 0)")
            if campus_rules == 0:
                evidence += " - campus controller rules missing: wrong or no controller?"
            self.record(f"CA-P{index:02d}", "controller", switch, "OF1.3",
                        "Installed", "Installed" if ok else "Missing", evidence)

    def test_policy_matrix(self):
        print("\n== 2. Policy matrix: users x servers x ICMP/TCP/UDP ==")
        number = 0
        for user in ("h1", "h2", "h3", "h4"):
            for server in ("h5", "h6"):
                is_student = topo.HOSTS[user]["role"] == "student"
                expected = BLOCKED if (is_student and server == "h6") else ALLOWED
                number += 1
                self.check_icmp(f"CA-{number:02d}", user, server, expected)
                for proto in ("TCP", "UDP"):
                    number += 1
                    self.check_service(f"CA-{number:02d}", user, server, proto, expected)

    def test_bypass_attempts(self):
        print("\n== 3. Bypass attempts by student h1 against admin server h6 ==")
        h1 = self.net.get("h1")
        intf = h1.defaultIntf()
        h6_ip, h6_mac = host_ip("h6"), topo.HOSTS["h6"]["mac"]

        # 3a. Skip ARP entirely: the student hard-codes h6's MAC.
        h1.cmd(f"ip neigh replace {h6_ip} lladdr {h6_mac} dev {intf} nud permanent")
        desc = "h1 (student, static ARP)"
        self.check_icmp("CA-B01", "h1", "h6", BLOCKED, src_desc=desc)
        self.check_service("CA-B02", "h1", "h6", "TCP", BLOCKED, src_desc=desc)
        self.check_service("CA-B03", "h1", "h6", "UDP", BLOCKED, src_desc=desc)

        # 3b. Forge a faculty source IP (h3's) on the student port.
        fac_ip = host_ip("h3")
        h1.cmd(f"ip addr flush dev {intf} && ip addr add {fac_ip}/24 dev {intf}")
        h1.cmd(f"ip neigh replace {h6_ip} lladdr {h6_mac} dev {intf} nud permanent")
        desc = "h1 (student, forged IP 10.0.0.3)"
        self.check_service("CA-B04", "h1", "h6", "TCP", BLOCKED, src_desc=desc, claimed_ip=fac_ip)
        self.check_service("CA-B05", "h1", "h6", "UDP", BLOCKED, src_desc=desc, claimed_ip=fac_ip)

        # 3c. Forge the complete faculty identity (h3's MAC + IP).
        h1.cmd(f"ip link set {intf} address {topo.HOSTS['h3']['mac']}")
        h1.cmd(f"ip neigh replace {h6_ip} lladdr {h6_mac} dev {intf} nud permanent")
        desc = "h1 (student, forged MAC+IP of h3)"
        self.check_service("CA-B06", "h1", "h6", "TCP", BLOCKED, src_desc=desc, claimed_ip=fac_ip)

        # Restore h1's real identity.
        h1.cmd(f"ip link set {intf} address {topo.HOSTS['h1']['mac']}")
        h1.cmd(f"ip addr flush dev {intf} && ip addr add {topo.HOSTS['h1']['ip']} dev {intf}")
        h1.cmd(f"ip neigh flush dev {intf}")

        # 3d. The isolation is bidirectional: the admin server cannot reach
        # students.  The controller refuses h6's ARP for h1 ...
        self.check_icmp("CA-B07", "h6", "h1", BLOCKED, src_desc=host_label("h6"))
        # ... and with ARP bypassed, real IPv4 hits the deny rule on s3.
        h6 = self.net.get("h6")
        h6_intf = h6.defaultIntf()
        h6.cmd(f"ip neigh replace {host_ip('h1')} lladdr {topo.HOSTS['h1']['mac']} dev {h6_intf} nud permanent")
        self.check_icmp("CA-B08", "h6", "h1", BLOCKED, src_desc="h6 (admin_server, static ARP)")
        h6.cmd(f"ip neigh flush dev {h6_intf}")

    def test_general_connectivity(self):
        print("\n== 4. General connectivity (Policy C) ==")
        pairs = [("h1", "h2", "same switch s1"), ("h3", "h4", "same switch s2"),
                 ("h1", "h3", "s1-s3-s2"), ("h2", "h4", "s1-s3-s2"), ("h5", "h6", "same switch s3")]
        for index, (src, dst, path) in enumerate(pairs, start=1):
            self.check_icmp(f"CA-G{index:02d}", src, dst, ALLOWED,
                            src_desc=f"{host_label(src)} [{path}]")

    def test_deny_counters(self):
        print("\n== 5. Data-plane evidence: deny-rule counters ==")
        for index, switch in enumerate(sorted(topo.SWITCHES), start=1):
            flows = sh(f"ovs-ofctl -O OpenFlow13 dump-flows {switch} cookie=0xca03/-1")
            packets = sum(int(n) for n in re.findall(r"n_packets=(\d+)", flows))
            # Student traffic enters at s1 and is dropped there; s3 drops the
            # static-ARP h6->h1 attempt (CA-B08). s2 never carries
            # student<->admin traffic, so its deny rules stay at zero.
            expected = "Hits" if switch in ("s1", "s3") else "No hits"
            actual = "Hits" if packets > 0 else "No hits"
            self.record(f"CA-C{index:02d}", "student/admin traffic", switch, "OF1.3",
                        expected, actual, f"deny rules on {switch} dropped {packets} packets")


def wait_for_pipeline(timeout):
    """Readiness check: the controller installs the three table-miss drops last,
    so once every switch shows them the pipeline is complete."""
    deadline = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
    while datetime.datetime.now() < deadline:
        ready = all(
            len(re.findall(r"cookie=0xca00", sh(f"ovs-ofctl -O OpenFlow13 dump-flows {s} cookie=0xca00/-1"))) == 3
            for s in topo.SWITCHES)
        if ready:
            return True
        time.sleep(0.2)
    return False


def environment_info(datapath):
    return {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "kernel": sh("uname -srm"),
        "os": sh(". /etc/os-release && echo $PRETTY_NAME"),
        "mininet": sh("mn --version"),
        "ovs": sh("ovs-vsctl --version | head -1"),
        "datapath": datapath,
    }


def controller_log_excerpt(path, limit=40):
    if not path:
        return "(controller log path not given; pass --controller-log)"
    try:
        with open(path) as handle:
            lines = [line.rstrip() for line in handle
                     if re.search(r"BLOCKED|PERMITTED ARP|LEARN|SWITCH|REJECTED|SECURITY", line)]
    except OSError as exc:
        return f"(could not read {path}: {exc})"
    return "\n".join(lines[-limit:])


def write_reports(results, env, report_dir, controller_log, flows):
    os.makedirs(report_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = os.path.join(report_dir, f"campus_report_{stamp}")
    passed = sum(r.passed for r in results)
    with open(base + ".json", "w") as handle:
        json.dump({"environment": env, "summary": {"passed": passed, "total": len(results)},
                   "results": [asdict(r) for r in results]}, handle, indent=2)
    with open(base + ".md", "w") as handle:
        handle.write("# Campus access-control test report\n\n")
        handle.write("Generated by `test_campus_access.py`; these are actual results from this run.\n\n")
        for key, value in env.items():
            handle.write(f"- **{key}**: {value}\n")
        handle.write(f"\n**Summary: {passed}/{len(results)} passed**\n\n")
        handle.write("| Test ID | Source | Destination / service | Protocol | Expected | Actual | Result | Evidence |\n")
        handle.write("|---|---|---|---|---|---|---|---|\n")
        for r in results:
            evidence = r.evidence.replace("|", "\\|")
            handle.write(f"| {r.test_id} | {r.source} | {r.destination} | {r.protocol} | {r.expected} "
                         f"| {r.actual} | {'PASS' if r.passed else '**FAIL**'} | {evidence} |\n")
        handle.write("\n## Controller log excerpt\n\n```\n" + controller_log + "\n```\n")
        handle.write("\n## Flow tables at end of run\n\n```\n" + flows + "\n```\n")
    return base + ".md", base + ".json"


def parse_args():
    parser = argparse.ArgumentParser(description="Campus SDN access-control tests")
    parser.add_argument("--controller-ip", default=topo.DEFAULT_CONTROLLER_IP)
    parser.add_argument("--controller-port", type=int, default=topo.DEFAULT_CONTROLLER_PORT)
    parser.add_argument("--datapath", choices=("kernel", "user"), default="kernel")
    parser.add_argument("--controller-log", help="Ryu log file to excerpt into the report")
    parser.add_argument("--report-dir", default=os.path.join(topo.PROJECT_DIR, "..", "reports"))
    return parser.parse_args()


def main():
    args = parse_args()
    if os.geteuid() != 0:
        sys.exit("Mininet needs root: run with sudo")
    setLogLevel("warning")
    net = topo.build_network(args.controller_ip, args.controller_port, args.datapath)
    tester = CampusTester(net)
    pids = {}
    flows = ""
    try:
        net.start()
        if not net.waitConnected(timeout=15):
            print(f"ERROR: switches did not connect to {args.controller_ip}:{args.controller_port}. "
                  "Start campus_access_controller.py first.", file=sys.stderr)
            return 2
        if not wait_for_pipeline(timeout=10):
            print("WARNING: campus pipeline not fully installed; pipeline tests will show details",
                  file=sys.stderr)
        pids = topo.start_services(net)
        topo.wait_for_services(net)
        tester.test_pipeline()
        tester.test_policy_matrix()
        tester.test_bypass_attempts()
        tester.test_general_connectivity()
        tester.test_deny_counters()
        flows = "\n\n".join(f"### {s}\n" + sh(f"ovs-ofctl -O OpenFlow13 dump-flows {s}")
                            for s in sorted(topo.SWITCHES))
    finally:
        topo.stop_services(net, pids)
        net.stop()

    env = environment_info(args.datapath)
    md_path, json_path = write_reports(tester.results, env, args.report_dir,
                                       controller_log_excerpt(args.controller_log), flows)
    failed = [r for r in tester.results if not r.passed]
    print(f"\nSUMMARY: {len(tester.results) - len(failed)}/{len(tester.results)} passed")
    for r in failed:
        print(f"  FAILED {r.test_id}: {r.source} -> {r.destination} {r.protocol}: {r.evidence}")
    print(f"Reports: {md_path}\n         {json_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
