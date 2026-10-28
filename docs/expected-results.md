# Expected results, executed results, and report template

Three kinds of result appear here; keep them apart:

1. **Expected**: what the design should produce, defined before running.
2. **Executed (Docker on this Mac)**: results actually obtained on
   2026-10-27 in privileged Ubuntu 22.04.5 and 26.04.1 aarch64 containers,
   using the Open vSwitch **userspace** datapath. Full reports:
   [harness-reports/](harness-reports/).
3. **Still to run by you**: the same test suite inside the UTM Ubuntu VM. Not
   yet executed.

## Expected

| Source | Destination | ICMP | TCP 8000 | UDP 9000 |
|---|---|---|---|---|
| h1 Student | h5 Academic | Allowed | Allowed | Allowed |
| h2 Student | h5 Academic | Allowed | Allowed | Allowed |
| h3 Faculty | h5 Academic | Allowed | Allowed | Allowed |
| h4 Faculty | h5 Academic | Allowed | Allowed | Allowed |
| h1 Student | h6 Admin | **Blocked** | **Blocked** | **Blocked** |
| h2 Student | h6 Admin | **Blocked** | **Blocked** | **Blocked** |
| h3 Faculty | h6 Admin | Allowed | Allowed | Allowed |
| h4 Faculty | h6 Admin | Allowed | Allowed | Allowed |

Bypass attempts, all expected **Blocked**:
* h1 with a static ARP entry for h6 (ICMP/TCP/UDP)
* h1 forging IP 10.0.0.3 (TCP/UDP)
* h1 forging h3's MAC + IP (TCP)
* h6 → h1 (ICMP, with and without static ARP)

Expected `pingall`: `13% dropped (26/30 received)`. Only h1↔h6 and h2↔h6 fail.

## Executed: 43 / 43 PASS (Ubuntu 22.04 and Ubuntu 26.04)

| Group | Tests | Ubuntu 22.04 | Ubuntu 26.04 |
|---|---|---|---|
| Rules installed on every switch (4 deny rules, 3 drop-by-default rules, no flooding fallback) | CA-P01–03 | 3/3 PASS | 3/3 PASS |
| 4 users × 2 servers × ICMP/TCP/UDP | CA-01–24 | 24/24 PASS | 24/24 PASS |
| Bypass attempts | CA-B01–08 | 8/8 PASS | 8/8 PASS |
| General connectivity between hosts | CA-G01–05 | 5/5 PASS | 5/5 PASS |
| Deny-rule packet counters (s1 hits, s2 none, s3 hits) | CA-C01–03 | 3/3 PASS | 3/3 PASS |

Reports:
* [campus_report_20261027-131510.md](harness-reports/campus_report_20261027-131510.md) (22.04)
* [u2604_campus_report_20261027-135129.md](harness-reports/u2604_campus_report_20261027-135129.md) (26.04)

`pingall` was also executed and printed `*** Results: 13% dropped (26/30
received)`, matching the expected matrix exactly.

History: the first full run was 41/42. CA-C03 failed because the test wrongly
expected s3's deny counter to increase from an h6 → h1 ping. The controller
already stops that ping at ARP, so no IPv4 packet reaches the rule. CA-B08 was
added: a static ARP entry on h6 forces real IPv4 into the s3 deny rule. After
that, all 43 passed.

## Still to run by you (UTM VM)

```bash
# Ubuntu VM
cd ~/sdn-mac-m4
./scripts/check_environment.sh
./scripts/run_tests.sh
```

Send me the generated `reports/campus_report_*.md`.

## Report template (manual runs in the Mininet CLI)

| Test ID | Source host | Destination / service | Protocol | Expected | Actual | PASS/FAIL | Command output |
|---|---|---|---|---|---|---|---|
| M-01 | h1 | h5 | ICMP | Allowed | | | `h1 ping -c 3 h5` → |
| M-02 | h3 | h6 | ICMP | Allowed | | | `h3 ping -c 3 h6` → |
| M-03 | h1 | h6 | ICMP | Blocked | | | `h1 ping -c 3 h6` → |
| M-04 | h3 | h6 :8000 | TCP | Allowed | | | `h3 python3 lab_client.py tcp 10.0.0.6 8000` → |
| M-05 | h1 | h6 :8000 | TCP | Blocked | | | `h1 python3 lab_client.py tcp 10.0.0.6 8000` → |
| M-06 | all | all | ICMP | 26/30 | | | `pingall` → |
| M-07 | — | s1 deny rules | OpenFlow | n_packets > 0 after M-05 | | | `sh ovs-ofctl -O OpenFlow13 dump-flows s1 cookie=0xca03/-1` → |
