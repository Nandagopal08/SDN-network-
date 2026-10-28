# Campus Access Control with SDN — Mininet + Open vSwitch + Ryu (OpenFlow 1.3)

A campus network with **three switches, four user hosts, two server hosts and
one SDN controller**:

* h1, h2 are **students**; h3, h4 are **faculty**
* h5 is the **academic server**: everyone may access it
* h6 is the **administrative server**: only faculty may access it

The policy is enforced by OpenFlow rules that the Ryu controller installs, and
it is verified with automated connectivity tests (ICMP, TCP and UDP).

Design details, flow tables and test descriptions are in
[campus-access-control/README.md](campus-access-control/README.md).

```mermaid
flowchart TB
    C["Ryu controller<br/>campus_access_controller.py<br/>127.0.0.1:6653"]
    S3["s3 — servers"]
    S1["s1 — students"]
    S2["s2 — faculty"]
    H1["h1 student 10.0.0.1"] --- S1
    H2["h2 student 10.0.0.2"] --- S1
    H3["h3 faculty 10.0.0.3"] --- S2
    H4["h4 faculty 10.0.0.4"] --- S2
    S1 --- S3
    S2 --- S3
    S3 --- H5["h5 academic server 10.0.0.5"]
    S3 --- H6["h6 admin server 10.0.0.6"]
    C -. OpenFlow 1.3 .- S1 & S2 & S3
```

| Source | Academic server h5 | Administrative server h6 |
|---|---|---|
| h1 Student | Allowed | **Blocked** |
| h2 Student | Allowed | **Blocked** |
| h3 Faculty | Allowed | Allowed |
| h4 Faculty | Allowed | Allowed |

## Verification status

| What | Status |
|---|---|
| Test suite on Ubuntu 22.04 ARM64 (Docker on this Mac, OVS userspace datapath) | **Executed: 43/43 PASS** — [report](docs/harness-reports/campus_report_20261027-131510.md) |
| Test suite on Ubuntu 26.04.1 ARM64 (Docker on this Mac, OVS userspace datapath) | **Executed: 43/43 PASS** — [report](docs/harness-reports/u2604_campus_report_20261027-135129.md) |
| `pingall` | **Executed**: `13% dropped (26/30 received)`: exactly the 4 student↔admin-server pairs |
| Inside **your UTM Ubuntu VM** (kernel datapath) | **Not yet executed — run `./scripts/run_tests.sh`** |

## Project layout

```
sdn-mac-m4/
├── README.md                        ← setup and run instructions (this file)
├── requirements-ryu.txt             ← pinned Ryu environment (Python 3.9)
├── campus-access-control/
│   ├── campus_topology.py           ← Mininet: 3 switches, 4 users, 2 servers
│   ├── campus_access_controller.py  ← Ryu app: access-control rules
│   ├── test_campus_access.py        ← automated policy tests + report
│   ├── lab_service.py               ← TCP/UDP service running on h5/h6
│   ├── lab_client.py                ← TCP/UDP probe
│   └── README.md                    ← design, flow tables, test details
├── scripts/
│   ├── mac/check_mac.sh             ← Mac: check architecture and tools
│   ├── mac/sync_to_vm.sh            ← Mac: copy the project into the VM
│   ├── setup_ubuntu.sh              ← Ubuntu: install everything
│   ├── check_environment.sh         ← Ubuntu: health check
│   ├── run_controller.sh            ← Ubuntu: start the controller
│   ├── run_topology.sh              ← Ubuntu: start the network + Mininet CLI
│   ├── run_tests.sh                 ← Ubuntu: controller + tests + report
│   ├── cleanup.sh                   ← Ubuntu: clear stale state
│   └── common.sh                    ← shared settings
└── docs/
    ├── architecture.md              ← Mac/VM layout and OpenFlow pipeline
    ├── expected-results.md          ← expected vs executed results, report template
    └── harness-reports/             ← actual reports from the test runs
```

## Why a VM

Mininet needs Linux network namespaces and Open vSwitch, so it cannot run on
macOS. The project runs in an **Ubuntu ARM64 VM in UTM**, with the controller
and Mininet in the same VM. The controller address is therefore
`127.0.0.1:6653`.

Ryu 4.34 needs `eventlet 0.30.2`, which breaks on Python 3.10 and newer, so
the setup script builds a separate **Python 3.9 environment with `uv`**.
Ubuntu's own Python is left alone.

---

## 1. One-time setup

### 1a. Create the VM (UTM, on the Mac)

1. UTM → **+** → **Virtualize** → **Linux**.
2. Boot ISO: `~/Downloads/ubuntu-26.04.1-desktop-arm64.iso`.
3. **8192 MB RAM, 4 CPU cores, 40 GB disk**. Skip Shared Directory. Save, then **▶**.
4. Install Ubuntu (Interactive installation → Default selection → Erase disk.
   That erases only the virtual disk.) Create your user.
5. After the restart, if asked to remove the installation medium, stop the
   VM, click **CD/DVD → Clear** in UTM, then press **▶** again.

### 1b. Enable SSH (Terminal *inside the Ubuntu window*)

The prompt must look like `user@ubuntu:~$`, **not** `...MacBook-Air...%`.

```bash
sudo apt update
sudo apt install -y openssh-server
ip -4 addr | grep 192.168.64        # note the IP, e.g. 192.168.64.5
```

### 1c. Copy the project (Mac Terminal)

```bash
cd "/Users/nandagopal/Desktop/untitled folder 3/sdn-mac-m4"
./scripts/mac/sync_to_vm.sh <ubuntu-user>@192.168.64.5
```

### 1d. Install Mininet, Open vSwitch and Ryu (Ubuntu)

```bash
cd ~/sdn-mac-m4
./scripts/setup_ubuntu.sh           # 5–10 min
./scripts/check_environment.sh      # every line should be OK
```

If "openvswitch kernel mod" shows FAIL, run `sudo modprobe openvswitch`. If
that fails too, add `--datapath user` to the run/test commands below.

---

## 2. Run the automated tests (Ubuntu)

```bash
cd ~/sdn-mac-m4
./scripts/run_tests.sh               # expect: SUMMARY: 43/43 passed
```

This starts the controller, builds the network, starts the services on h5/h6,
runs all tests, stops everything, and writes `reports/campus_report_<time>.md`
and `.json`.

## 3. Run it interactively (Ubuntu, two terminals)

**Terminal 1**: controller (leave it running; it prints every decision):
```bash
cd ~/sdn-mac-m4 && ./scripts/run_controller.sh
```

**Terminal 2**: network and Mininet CLI:
```bash
cd ~/sdn-mac-m4 && ./scripts/run_topology.sh
```

Connectivity checks at the `mininet>` prompt:

```
h1 ping -c 2 h5                               # student → academic: replies
h3 ping -c 2 h6                               # faculty → admin: replies
h1 ping -c 2 h6                               # student → admin: Destination Host Unreachable
h3 python3 lab_client.py tcp 10.0.0.6 8000    # faculty TCP → admin: RESULT=OK ADMIN TCP OK
h1 python3 lab_client.py tcp 10.0.0.6 8000    # student TCP → admin: RESULT=ERROR / TIMEOUT
h1 python3 lab_client.py udp 10.0.0.5 9000    # student UDP → academic: RESULT=OK
pingall                                       # 13% dropped (26/30 received)
sh ovs-ofctl -O OpenFlow13 dump-flows s1      # installed OpenFlow rules
exit
```

Expected `pingall` (X = blocked):

```
h1 -> h2 h3 h4 h5 X
h2 -> h1 h3 h4 h5 X
h3 -> h1 h2 h4 h5 h6
h4 -> h1 h2 h3 h5 h6
h5 -> h1 h2 h3 h4 h6
h6 -> X X h3 h4 h5
*** Results: 13% dropped (26/30 received)
```

Then press **Ctrl-C** in Terminal 1.

## 4. Inspect OpenFlow rules (Ubuntu)

```bash
sudo ovs-vsctl show                                          # switches, controller connection
sudo ovs-ofctl -O OpenFlow13 dump-flows s1                   # all rules on s1
sudo ovs-ofctl -O OpenFlow13 dump-flows s1 table=1           # access-control table
sudo ovs-ofctl -O OpenFlow13 dump-flows s1 cookie=0xca03/-1  # deny rules + packet counters
tail -f ~/sdn-mac-m4/logs/campus_controller.log              # PERMITTED / BLOCKED decisions
```

## 5. Stop and clean up (Ubuntu)

```bash
./scripts/cleanup.sh     # mn -c, stops controller + services, removes s1–s3, frees port 6653
```

---

## Troubleshooting

| Symptom | Fix (Ubuntu) |
|---|---|
| `sudo: apt: command not found` | You are in the **Mac** Terminal. Run it in the Ubuntu window |
| `Switches did not connect` | Start the controller first: `./scripts/run_controller.sh`; check `ss -ltn 'sport = :6653'` |
| `port 6653 already in use` | `./scripts/cleanup.sh` |
| `ovs-vsctl` hangs | `sudo systemctl restart openvswitch-switch`; check `pgrep -x ovs-vswitchd` |
| `Module openvswitch not found` | `sudo apt install linux-modules-extra-$(uname -r)` or use `--datapath user` |
| `File exists` / `Error creating interface pair` | `./scripts/cleanup.sh` |
| `REJECTED dpid=...` in controller log | A different topology is connected; `./scripts/cleanup.sh`, then `./scripts/run_topology.sh` |
| `ImportError: ... ALREADY_HANDLED` / `cannot set 'is_timeout'` | Ryu in the wrong Python; re-run `./scripts/setup_ubuntu.sh` |
| `OSError: protocol not found` | `sudo apt install netbase` |

Please send me the command, its output, and `tail -50 logs/campus_controller.log`
if something fails.

## Identity limitations

Roles are bound to fixed lab identities (MAC + IP + switch port). A student
forging a faculty IP, or a faculty MAC and IP, is still dropped (tests
CA-B04–B06). In a real campus, MAC and IP addresses can be spoofed and ports
are shared. Production networks authenticate users with **IEEE 802.1X** and
apply policy from that identity.
