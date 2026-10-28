# Project 1 — Campus access control with Ryu (OpenFlow 1.3)

## Topology and addressing

```mermaid
flowchart TB
    C["Ryu: campus_access_controller.py<br/>127.0.0.1:6653"]
    S3["s3 — server distribution<br/>dpid 0x1003"]
    S1["s1 — student access<br/>dpid 0x1001"]
    S2["s2 — faculty access<br/>dpid 0x1002"]
    H1["h1 student<br/>10.0.0.1"] -- "p1" --- S1
    H2["h2 student<br/>10.0.0.2"] -- "p2" --- S1
    H3["h3 faculty<br/>10.0.0.3"] -- "p1" --- S2
    H4["h4 faculty<br/>10.0.0.4"] -- "p2" --- S2
    S1 -- "s1:p3 ↔ s3:p3" --- S3
    S2 -- "s2:p3 ↔ s3:p4" --- S3
    S3 -- "p1" --- H5["h5 academic server<br/>10.0.0.5  TCP 8000 / UDP 9000"]
    S3 -- "p2" --- H6["h6 administrative server<br/>10.0.0.6  TCP 8000 / UDP 9000"]
    C -. OpenFlow 1.3 .- S1 & S2 & S3
```

| Host | Role | IP | MAC | Switch : port |
|---|---|---|---|---|
| h1 | Student | 10.0.0.1/24 | 00:00:00:00:00:01 | s1 : 1 |
| h2 | Student | 10.0.0.2/24 | 00:00:00:00:00:02 | s1 : 2 |
| h3 | Faculty | 10.0.0.3/24 | 00:00:00:00:00:03 | s2 : 1 |
| h4 | Faculty | 10.0.0.4/24 | 00:00:00:00:00:04 | s2 : 2 |
| h5 | Academic server | 10.0.0.5/24 | 00:00:00:00:00:05 | s3 : 1 |
| h6 | Administrative server | 10.0.0.6/24 | 00:00:00:00:00:06 | s3 : 2 |

The switches form a loop-free tree, so no spanning tree is needed. Port numbers
are pinned in `campus_topology.py` and mirrored in the controller's inventory.

## Policy

| Source | Destination | Expected |
|---|---|---|
| h1, h2 (students) | h5 academic | **Allowed** (ICMP, TCP, UDP) |
| h3, h4 (faculty) | h5 academic | **Allowed** |
| h3, h4 (faculty) | h6 admin | **Allowed** |
| h1, h2 (students) | h6 admin | **Blocked — every IP protocol, both directions** |
| all other host pairs | — | Allowed (Policy C) |

## How the controller enforces it

When a campus switch connects (`EventOFPSwitchFeatures`), the controller
deletes all existing flows and installs a three-table pipeline. Other DPIDs
are rejected.

```
table 0  PORT SECURITY
  p300  in_port=<host port>, arp, eth_src=<MAC>, arp_sha=<MAC>, arp_spa=<IP>  -> CONTROLLER
  p300  in_port=<host port>, ip,  eth_src=<MAC>, ipv4_src=<IP>                -> goto 1
  p250  in_port=<trunk port>, ip                                              -> goto 1
  p200  in_port=<host port>                                                   -> drop   (spoofed / IPv6 / raw L2)
  p0    *                                                                     -> drop
table 1  ACL (identical on every switch)
  p1000 ip, ipv4_src=10.0.0.{1,2}, ipv4_dst=10.0.0.6                          -> drop   (explicit DENY)
  p1000 ip, ipv4_src=10.0.0.6,     ipv4_dst=10.0.0.{1,2}                      -> drop
  p500  ip, ipv4_src=A, ipv4_dst=B   for each of the 26 permitted pairs       -> goto 2 (explicit ALLOW)
  p0    *                                                                     -> drop   (default deny)
table 2  FORWARDING
  p100  ip, eth_dst=<MAC>, ipv4_dst=<IP>                                      -> output:<port toward host>
  p0    *                                                                     -> drop
```

Design points, mapped to the requirements:

* **Explicit allow and deny rules, with priorities.** Deny (1000) always
  beats allow (500). Neither rule matches `ip_proto`, so ICMP, TCP, UDP and
  every other IP protocol get the same decision.
* **No unsafe fallback.** The ACL table-miss is *drop*. No rule anywhere uses
  `FLOOD`, `NORMAL` or `ALL`, and test CA-P01..03 checks this. Switches run with
  `fail_mode=secure`, so killing the controller does not open the network.
* **Consistent on the forwarding path.** The ACL is installed on all three
  switches. A student packet is dropped at s1, its ingress switch. An admin
  server packet toward a student is dropped at s3 (test CA-C01..03 checks
  deny-rule counters).
* **ARP handled safely: policy-aware proxy ARP.** Hosts' ARP goes only to the
  controller and is never flooded. The controller answers from its inventory
  only if the pair is permitted. A student's `who-has 10.0.0.6` gets no answer
  (`BLOCKED ARP` in the log). ARP is not the security boundary, though: if the
  student hard-codes h6's MAC (test CA-B01..03), the IPv4 deny rule still drops
  everything.
* **Identity binding and attachment learning.** Roles come from the
  controller's inventory (`LAB_HOSTS`), never from packet contents. Each
  identity is bound to one switch port. The controller learns and confirms
  attachment points from ARP packet-ins (`LEARN h1 ... seen at s1 port 1`). A
  packet-in that doesn't match the registered port is logged as `SECURITY` and
  ignored, and table 0 drops such frames anyway. Forwarding entries are
  computed proactively from the wiring plan with BFS over the switch graph, so
  the security decision never depends on what a host claims.
* **Logging.** `PERMITTED ARP` / `BLOCKED ARP` are logged for each ARP
  decision. Every 5 s the controller polls ACL rule counters and logs
  `PERMITTED IPv4 ...` / `BLOCKED IPv4 on s1: h1(...) -> h6(...) +N packets`.
  It never pulls data packets to the controller to do this.

### Why ICMP-only testing is not enough

`ping` exercises only ICMP. An ACL written as `icmp, nw_src=…, nw_dst=… → drop`
would pass a ping test while letting the student use SSH, HTTP or DNS. Ping
also stops at ARP here (no proxy reply), so it never tests the IPv4 rules at
all. The test suite therefore:

1. runs real TCP (port 8000) and UDP (port 9000) services on h5 and h6;
2. requires a blocked result to be confirmed on **both** sides: the client
   gets no banner **and** the server log shows no packet from that source;
3. bypasses ARP with a static neighbour entry so IPv4 reaches the switch;
4. forges a faculty IP, then a faculty MAC + IP, on the student port;
5. checks deny-rule counters to show the switch dropped the traffic.

## Run it (Ubuntu VM)

```bash
# Automated: 43 tests, report in ../reports/
cd ~/sdn-mac-m4 && ./scripts/run_tests.sh

# Interactive: terminal 1
cd ~/sdn-mac-m4 && ./scripts/run_controller.sh
# terminal 2 (services on h5/h6 start automatically)
cd ~/sdn-mac-m4 && ./scripts/run_topology.sh
```

Mininet CLI demonstrations:

```
mininet> h3 ping -c 2 h6                                         # faculty -> admin: replies
mininet> h1 ping -c 2 h6                                         # student -> admin: "Destination Host Unreachable"
mininet> h1 python3 lab_client.py tcp 10.0.0.6 8000              # RESULT=ERROR/TIMEOUT
mininet> h3 python3 lab_client.py tcp 10.0.0.6 8000              # RESULT=OK ADMIN TCP OK
mininet> h1 python3 lab_client.py udp 10.0.0.5 9000              # RESULT=OK ACADEMIC UDP OK
mininet> h1 ip neigh replace 10.0.0.6 lladdr 00:00:00:00:00:06 dev h1-eth0 nud permanent
mininet> h1 python3 lab_client.py tcp 10.0.0.6 8000              # still blocked: IPv4 deny rule
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1 cookie=0xca03/-1   # n_packets increased
mininet> pingall                                                 # 4 student<->h6 pairs show X
```

The `lab_client.py` paths are relative to the directory you started Mininet
from. `run_topology.sh` starts it in this directory.

Expected `pingall` matrix (X = dropped). Only student ↔ admin pairs fail:

```
h1 -> h2 h3 h4 h5 X
h2 -> h1 h3 h4 h5 X
h3 -> h1 h2 h4 h5 h6
h4 -> h1 h2 h3 h5 h6
h5 -> h1 h2 h3 h4 h6
h6 -> X X h3 h4 h5
*** Results: 13% dropped (26/30 received)
```

## Test report

`test_campus_access.py` writes `reports/campus_report_<timestamp>.md` and a
`.json` file with Test ID, source, destination/service, protocol, expected,
actual, PASS/FAIL and evidence. Evidence is the client output plus the
server-log check or rule counters. It also includes a controller log excerpt
and the final flow tables. See [../docs/expected-results.md](../docs/expected-results.md).

### Test limitations

* UDP has no handshake. "Blocked" means no reply **and** no datagram logged by
  the server, so a crashed service could look the same. That's why every
  blocked check is paired with an allowed check against the same service
  (faculty → h6), which proves the service is up.
* Tests cover IPv4 (ICMP/TCP/UDP). IPv6 is disabled on hosts and dropped at
  the edge by table 0 (anything not matching the IPv4/ARP identity rules).
* Identity is MAC + IP + port. This is a lab assumption; see the security
  limitations section of the main README (802.1X).
