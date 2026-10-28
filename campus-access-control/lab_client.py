#!/usr/bin/env python3
"""Probe a lab service over TCP or UDP.

    python3 lab_client.py tcp 10.0.0.6 8000
    python3 lab_client.py udp 10.0.0.5 9000 --timeout 2

Prints one line "RESULT=<OK|TIMEOUT|REFUSED|ERROR> ..." and exits 0 only when
the server's banner was received.
"""

import argparse
import socket
import sys


def probe_tcp(ip, port, timeout):
    with socket.create_connection((ip, port), timeout=timeout) as sock:
        return sock.recv(128).decode(errors="replace").strip()


def probe_udp(ip, port, timeout):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(b"probe", (ip, port))
        data, _addr = sock.recvfrom(128)
        return data.decode(errors="replace").strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("proto", choices=("tcp", "udp"))
    parser.add_argument("ip")
    parser.add_argument("port", type=int)
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    probe = probe_tcp if args.proto == "tcp" else probe_udp
    try:
        banner = probe(args.ip, args.port, args.timeout)
    except socket.timeout:
        print(f"RESULT=TIMEOUT no {args.proto.upper()} reply from {args.ip}:{args.port} within {args.timeout}s")
        return 1
    except ConnectionRefusedError:
        print(f"RESULT=REFUSED {args.ip}:{args.port}")
        return 1
    except OSError as exc:
        # e.g. "No route to host" when ARP for the target is never answered
        print(f"RESULT=ERROR {type(exc).__name__}: {exc}")
        return 1
    print(f"RESULT=OK {banner}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
