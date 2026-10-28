"""Campus access-control Ryu application (OpenFlow 1.3).

Policy
  A. every user (student + faculty) may reach the academic server h5
  B. only faculty may reach the administrative server h6; students are denied
     ALL IPv4 traffic to/from h6 (ICMP, TCP, UDP, anything else)
  C. all other lab hosts may talk to each other

Pipeline (installed on every campus switch when it connects)

  table 0  PORT SECURITY  - edge port: accept only the registered host's
                            MAC+IP.  ARP -> controller, IPv4 -> table 1,
                            anything else (spoofed, IPv6, raw L2) -> drop
                          - inter-switch (trunk) port: IPv4 -> table 1
  table 1  ACL            - prio 1000 explicit DENY  (student <-> admin server)
                          - prio  500 explicit ALLOW (every permitted pair)
                          - table-miss: drop  (default deny, no fallback)
  table 2  FORWARDING     - prio 100 per-destination output port
                          - table-miss: drop

ARP is never flooded: the controller answers ARP requests itself (proxy ARP)
and only for destinations the requester is permitted to reach.

Run:
    ryu-manager --ofp-listen-host 127.0.0.1 --ofp-tcp-listen-port 6653 \
        campus_access_controller.py
"""

from collections import deque, namedtuple

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, DEAD_DISPATCHER,
                                    MAIN_DISPATCHER, set_ev_cls)
from ryu.lib import hub
from ryu.lib.packet import arp, ether_types, ethernet, packet
from ryu.ofproto import ofproto_v1_3

# --------------------------------------------------------------------------
# Lab inventory.  Roles are bound to registered host identities (MAC + IP +
# switch port); nothing a host sends can change its role.  Must match
# campus_topology.py.
# --------------------------------------------------------------------------
LabHost = namedtuple("LabHost", "name mac ip role dpid port")

STUDENT, FACULTY = "student", "faculty"
ACADEMIC_SERVER, ADMIN_SERVER = "academic_server", "admin_server"

CAMPUS_SWITCHES = {0x1001: "s1", 0x1002: "s2", 0x1003: "s3"}

LAB_HOSTS = [
    LabHost("h1", "00:00:00:00:00:01", "10.0.0.1", STUDENT, 0x1001, 1),
    LabHost("h2", "00:00:00:00:00:02", "10.0.0.2", STUDENT, 0x1001, 2),
    LabHost("h3", "00:00:00:00:00:03", "10.0.0.3", FACULTY, 0x1002, 1),
    LabHost("h4", "00:00:00:00:00:04", "10.0.0.4", FACULTY, 0x1002, 2),
    LabHost("h5", "00:00:00:00:00:05", "10.0.0.5", ACADEMIC_SERVER, 0x1003, 1),
    LabHost("h6", "00:00:00:00:00:06", "10.0.0.6", ADMIN_SERVER, 0x1003, 2),
]

# (dpid_a, port_a, dpid_b, port_b) - the wiring plan of the loop-free tree
INTER_SWITCH_LINKS = [
    (0x1001, 3, 0x1003, 3),
    (0x1002, 3, 0x1003, 4),
]

# Role pairs that may not exchange traffic, enforced in BOTH directions.
DENIED_ROLE_PAIRS = {(STUDENT, ADMIN_SERVER)}

# Pipeline layout
TABLE_PORT_SECURITY, TABLE_ACL, TABLE_FORWARDING = 0, 1, 2

PRIO_HOST_ACCEPT = 300       # table 0: registered host on its own port
PRIO_TRUNK_ACCEPT = 250      # table 0: traffic arriving from another switch
PRIO_EDGE_DROP = 200         # table 0: anything else on an edge port
PRIO_ACL_DENY = 1000         # table 1: must beat every allow rule
PRIO_ACL_ALLOW = 500         # table 1
PRIO_FORWARD = 100           # table 2
PRIO_TABLE_MISS = 0          # all tables: explicit drop

# Cookies make our rules easy to find with `ovs-ofctl dump-flows ... cookie=`
COOKIE_TABLE_MISS = 0xCA00
COOKIE_PORT_SECURITY = 0xCA01
COOKIE_ACL_ALLOW = 0xCA02
COOKIE_ACL_DENY = 0xCA03
COOKIE_FORWARD = 0xCA04

STATS_INTERVAL_SECONDS = 5


def is_permitted(src, dst):
    """Policy decision for an ordered host pair."""
    if src.name == dst.name:
        return False
    return ((src.role, dst.role) not in DENIED_ROLE_PAIRS
            and (dst.role, src.role) not in DENIED_ROLE_PAIRS)


class CampusAccessController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.hosts_by_mac = {h.mac: h for h in LAB_HOSTS}
        self.hosts_by_ip = {h.ip: h for h in LAB_HOSTS}
        self.hosts_by_name = {h.name: h for h in LAB_HOSTS}
        self.adjacency = self._build_adjacency()
        self.datapaths = {}
        self.learned_attachments = {}   # mac -> (dpid, port), from packet-in
        self.acl_counters = {}          # (dpid, cookie, src_ip, dst_ip) -> packets
        self.monitor_thread = hub.spawn(self._monitor_acl_counters)

    # ------------------------------------------------------------------ setup
    @staticmethod
    def _build_adjacency():
        """dpid -> {neighbour dpid: local port toward that neighbour}."""
        adjacency = {dpid: {} for dpid in CAMPUS_SWITCHES}
        for dpid_a, port_a, dpid_b, port_b in INTER_SWITCH_LINKS:
            adjacency[dpid_a][dpid_b] = port_a
            adjacency[dpid_b][dpid_a] = port_b
        return adjacency

    def _output_port_toward(self, dpid, host):
        """Port on switch `dpid` that leads to `host` (BFS over the switch tree)."""
        if dpid == host.dpid:
            return host.port
        parents = {dpid: None}
        queue = deque([dpid])
        while queue:
            current = queue.popleft()
            if current == host.dpid:
                break
            for neighbour in self.adjacency[current]:
                if neighbour not in parents:
                    parents[neighbour] = current
                    queue.append(neighbour)
        if host.dpid not in parents:
            raise ValueError(f"no path from {dpid:#x} to {host.name}")
        hop = host.dpid
        while parents[hop] != dpid:
            hop = parents[hop]
        return self.adjacency[dpid][hop]

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER and datapath.id in CAMPUS_SWITCHES:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER and datapath.id in self.datapaths:
            self.logger.warning("SWITCH DOWN %s (dpid=%#x)", CAMPUS_SWITCHES[datapath.id], datapath.id)
            del self.datapaths[datapath.id]

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        dpid = datapath.id
        if dpid not in CAMPUS_SWITCHES:
            # Some other topology is connected to this controller.
            # Install nothing: with fail-mode=secure the switch drops everything.
            self.logger.error(
                "REJECTED dpid=%#x: not a campus switch (expected %s). "
                "Is a different topology connected to the campus controller?",
                dpid, ", ".join(f"{d:#x}" for d in CAMPUS_SWITCHES))
            return
        name = CAMPUS_SWITCHES[dpid]
        self.logger.info("SWITCH UP %s (dpid=%#x) - installing access-control pipeline", name, dpid)
        self._delete_all_flows(datapath)
        counts = {
            "port-security": self._install_port_security(datapath),
            "acl-deny": 0,
            "acl-allow": 0,
            "forwarding": self._install_forwarding(datapath),
        }
        counts["acl-deny"], counts["acl-allow"] = self._install_acl(datapath)
        self._install_table_miss_drops(datapath)
        datapath.send_msg(datapath.ofproto_parser.OFPBarrierRequest(datapath))
        self.logger.info("SWITCH READY %s rules: %s", name,
                         ", ".join(f"{k}={v}" for k, v in counts.items()))

    def _delete_all_flows(self, datapath):
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, table_id=ofproto.OFPTT_ALL, command=ofproto.OFPFC_DELETE,
            out_port=ofproto.OFPP_ANY, out_group=ofproto.OFPG_ANY, match=parser.OFPMatch()))

    def _add_flow(self, datapath, table_id, priority, match, instructions, cookie):
        parser = datapath.ofproto_parser
        datapath.send_msg(parser.OFPFlowMod(
            datapath=datapath, table_id=table_id, priority=priority, match=match,
            instructions=instructions, cookie=cookie))

    def _install_port_security(self, datapath):
        """Table 0: bind each registered host to its own edge port."""
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        to_controller = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS,
            [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)])]
        to_acl = [parser.OFPInstructionGotoTable(TABLE_ACL)]
        installed = 0
        for host in LAB_HOSTS:
            if host.dpid != datapath.id:
                continue
            # ARP whose Ethernet and ARP sender fields both match the
            # registered identity goes to the controller for a policy decision.
            self._add_flow(datapath, TABLE_PORT_SECURITY, PRIO_HOST_ACCEPT,
                           parser.OFPMatch(in_port=host.port, eth_type=ether_types.ETH_TYPE_ARP,
                                           eth_src=host.mac, arp_sha=host.mac, arp_spa=host.ip),
                           to_controller, COOKIE_PORT_SECURITY)
            self._add_flow(datapath, TABLE_PORT_SECURITY, PRIO_HOST_ACCEPT,
                           parser.OFPMatch(in_port=host.port, eth_type=ether_types.ETH_TYPE_IP,
                                           eth_src=host.mac, ipv4_src=host.ip),
                           to_acl, COOKIE_PORT_SECURITY)
            # Anything else on this edge port (spoofed MAC/IP, IPv6, ...) is dropped.
            self._add_flow(datapath, TABLE_PORT_SECURITY, PRIO_EDGE_DROP,
                           parser.OFPMatch(in_port=host.port), [], COOKIE_PORT_SECURITY)
            installed += 3
        for trunk_port in self.adjacency[datapath.id].values():
            # Frames from another campus switch were already validated at
            # their ingress edge port; only IPv4 ever crosses a trunk.
            self._add_flow(datapath, TABLE_PORT_SECURITY, PRIO_TRUNK_ACCEPT,
                           parser.OFPMatch(in_port=trunk_port, eth_type=ether_types.ETH_TYPE_IP),
                           to_acl, COOKIE_PORT_SECURITY)
            installed += 1
        return installed

    def _install_acl(self, datapath):
        """Table 1: explicit deny rules above explicit allow rules; miss = drop.

        The ACL is installed on EVERY switch, so the policy holds on any
        switch a packet traverses, not just the ingress switch.
        """
        parser = datapath.ofproto_parser
        to_forwarding = [parser.OFPInstructionGotoTable(TABLE_FORWARDING)]
        deny_count = allow_count = 0
        for src in LAB_HOSTS:
            for dst in LAB_HOSTS:
                if src.name == dst.name:
                    continue
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                        ipv4_src=src.ip, ipv4_dst=dst.ip)
                if is_permitted(src, dst):
                    self._add_flow(datapath, TABLE_ACL, PRIO_ACL_ALLOW, match,
                                   to_forwarding, COOKIE_ACL_ALLOW)
                    allow_count += 1
                else:
                    # No ip_proto in the match: ICMP, TCP, UDP and every other
                    # IP protocol are all dropped.  Empty instructions = drop.
                    self._add_flow(datapath, TABLE_ACL, PRIO_ACL_DENY, match, [], COOKIE_ACL_DENY)
                    deny_count += 1
        return deny_count, allow_count

    def _install_forwarding(self, datapath):
        """Table 2: one exact rule per registered destination."""
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        for host in LAB_HOSTS:
            out_port = self._output_port_toward(datapath.id, host)
            self._add_flow(datapath, TABLE_FORWARDING, PRIO_FORWARD,
                           parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                           eth_dst=host.mac, ipv4_dst=host.ip),
                           [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS,
                                                         [parser.OFPActionOutput(out_port)])],
                           COOKIE_FORWARD)
        return len(LAB_HOSTS)

    def _install_table_miss_drops(self, datapath):
        """Explicit lowest-priority drop in every table (visible in dump-flows).

        There is deliberately NO flood / NORMAL / send-to-controller fallback:
        a packet that matches no explicit allow path is dropped.
        """
        parser = datapath.ofproto_parser
        for table_id in (TABLE_PORT_SECURITY, TABLE_ACL, TABLE_FORWARDING):
            self._add_flow(datapath, table_id, PRIO_TABLE_MISS, parser.OFPMatch(), [],
                           COOKIE_TABLE_MISS)

    # -------------------------------------------------------------- packet-in
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        arp_pkt = pkt.get_protocol(arp.arp)
        switch_name = CAMPUS_SWITCHES.get(datapath.id, hex(datapath.id))
        if arp_pkt is None:
            eth = pkt.get_protocol(ethernet.ethernet)
            self.logger.warning("UNEXPECTED packet-in on %s port %s (ethertype=%s); ignored",
                                switch_name, in_port, hex(eth.ethertype) if eth else "?")
            return

        sender = self.hosts_by_mac.get(arp_pkt.src_mac)
        # Table 0 already enforces this; re-checking guards against a
        # mis-installed rule ever letting a spoofed ARP through.
        if (sender is None or sender.ip != arp_pkt.src_ip
                or (sender.dpid, sender.port) != (datapath.id, in_port)):
            self.logger.warning("SECURITY spoofed/unregistered ARP on %s port %s: %s/%s - dropped",
                                switch_name, in_port, arp_pkt.src_mac, arp_pkt.src_ip)
            return
        self._learn_attachment(sender, datapath.id, in_port)

        if arp_pkt.opcode != arp.ARP_REQUEST:
            return  # replies/gratuitous ARP only refresh the attachment above
        target = self.hosts_by_ip.get(arp_pkt.dst_ip)
        if target is None:
            self.logger.info("ARP %s asks for unknown %s - no reply", sender.name, arp_pkt.dst_ip)
            return
        if target.name == sender.name:
            return  # gratuitous ARP / duplicate address probe
        if not is_permitted(sender, target):
            self.logger.warning("BLOCKED ARP %s(%s,%s) -> %s(%s,%s): policy denies %s->%s, no reply sent",
                                sender.name, sender.ip, sender.role, target.name, target.ip,
                                target.role, sender.role, target.role)
            return
        self._send_proxy_arp_reply(datapath, in_port, sender, target)
        self.logger.info("PERMITTED ARP %s(%s) -> %s(%s): proxy reply %s is-at %s",
                         sender.name, sender.role, target.name, target.role, target.ip, target.mac)

    def _learn_attachment(self, host, dpid, port):
        previous = self.learned_attachments.get(host.mac)
        if previous != (dpid, port):
            self.learned_attachments[host.mac] = (dpid, port)
            self.logger.info("LEARN %s (%s, %s, %s) seen at %s port %s",
                             host.name, host.mac, host.ip, host.role, CAMPUS_SWITCHES[dpid], port)

    def _send_proxy_arp_reply(self, datapath, out_port, requester, target):
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(ethertype=ether_types.ETH_TYPE_ARP,
                                             dst=requester.mac, src=target.mac))
        reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY,
                                   src_mac=target.mac, src_ip=target.ip,
                                   dst_mac=requester.mac, dst_ip=requester.ip))
        reply.serialize()
        datapath.send_msg(parser.OFPPacketOut(
            datapath=datapath, buffer_id=ofproto.OFP_NO_BUFFER, in_port=ofproto.OFPP_CONTROLLER,
            actions=[parser.OFPActionOutput(out_port)], data=reply.data))

    # -------------------------------------------------------- traffic logging
    def _monitor_acl_counters(self):
        """Poll ACL rule counters so permitted and blocked IPv4 traffic is
        logged without ever sending data packets to the controller."""
        while True:
            for datapath in list(self.datapaths.values()):
                parser = datapath.ofproto_parser
                datapath.send_msg(parser.OFPFlowStatsRequest(datapath, table_id=TABLE_ACL))
            hub.sleep(STATS_INTERVAL_SECONDS)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        for stat in ev.msg.body:
            if stat.cookie not in (COOKIE_ACL_ALLOW, COOKIE_ACL_DENY):
                continue
            src_ip, dst_ip = stat.match.get("ipv4_src"), stat.match.get("ipv4_dst")
            key = (dpid, stat.cookie, src_ip, dst_ip)
            delta = stat.packet_count - self.acl_counters.get(key, 0)
            self.acl_counters[key] = stat.packet_count
            if delta <= 0:
                continue
            src, dst = self.hosts_by_ip[src_ip], self.hosts_by_ip[dst_ip]
            verdict = "BLOCKED" if stat.cookie == COOKIE_ACL_DENY else "PERMITTED"
            log = self.logger.warning if verdict == "BLOCKED" else self.logger.info
            log("%s IPv4 on %s: %s(%s,%s) -> %s(%s,%s) +%d packets (total %d)",
                verdict, CAMPUS_SWITCHES[dpid], src.name, src_ip, src.role,
                dst.name, dst_ip, dst.role, delta, stat.packet_count)
