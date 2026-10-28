# Architecture

## Mac / VM / controller layout

```mermaid
flowchart TB
    subgraph MAC["macOS — MacBook Air M4 (arm64)"]
        direction LR
        CODE["VS Code"]
        TERM["Terminal<br/>ssh / rsync"]
    end
    subgraph UTM["UTM — Shared Network 192.168.64.0/24"]
        subgraph VM["Ubuntu ARM64 VM"]
            direction TB
            RYU["ryu-manager (Python 3.9 venv)<br/>campus_access_controller.py<br/>127.0.0.1:6653"]
            NS["Mininet hosts h1..h6<br/>(network namespaces + veth pairs)"]
            OVS["Open vSwitch: s1 s2 s3<br/>OpenFlow13, fail_mode=secure"]
            NS --- OVS
            OVS -- "OpenFlow 1.3 over TCP (loopback)" --> RYU
        end
    end
    CODE -- "SSH :22" --> VM
    TERM -- "SSH :22" --> VM
```

Only SSH crosses the Mac ↔ VM boundary. The OpenFlow connection stays on the
VM's loopback interface, so UTM network settings cannot affect it.

The campus switches use datapath IDs `0x1001`–`0x1003`. The controller
refuses any other switch (`REJECTED` in its log) and installs nothing on it.
Because of `fail_mode=secure`, a switch without its controller forwards
nothing.

## OpenFlow pipeline (installed on every switch)

```mermaid
flowchart LR
    IN([packet in]) --> T0{"table 0<br/>port security"}
    T0 -- "ARP from registered MAC+IP+port" --> CTRL["controller<br/>policy-aware proxy ARP"]
    T0 -- "IPv4 from registered MAC+IP+port<br/>or IPv4 from another switch" --> T1{"table 1<br/>ACL"}
    T0 -- "anything else on a host port" --> D0([drop])
    T1 -- "p1000 student ↔ admin server" --> D1([drop])
    T1 -- "p500 permitted pair" --> T2{"table 2<br/>forwarding"}
    T1 -- "no match" --> D2([drop])
    T2 -- "p100 eth_dst + ipv4_dst" --> OUT([output toward host])
    T2 -- "no match" --> D3([drop])
```
