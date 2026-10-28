#!/usr/bin/env python3
"""Minimal TCP + UDP lab service for the campus servers (h5, h6).

Every accepted TCP connection and every received UDP datagram is appended to
--log as "<PROTO> <src_ip>:<src_port>".  The tests read this log to prove that
a blocked packet never *reached* the server, which is stronger evidence than
"the client got no reply".
"""

import argparse
import socket
import socketserver
import threading


class ServiceLog:
    def __init__(self, path):
        self._lock = threading.Lock()
        self._file = open(path, "a", buffering=1)

    def record(self, proto, address):
        with self._lock:
            self._file.write(f"{proto} {address[0]}:{address[1]}\n")


def make_tcp_handler(name, log):
    class TcpHandler(socketserver.BaseRequestHandler):
        def handle(self):
            log.record("TCP", self.client_address)
            self.request.sendall(f"{name} TCP OK\n".encode())
    return TcpHandler


def make_udp_handler(name, log):
    class UdpHandler(socketserver.BaseRequestHandler):
        def handle(self):
            _data, sock = self.request
            log.record("UDP", self.client_address)
            sock.sendto(f"{name} UDP OK\n".encode(), self.client_address)
    return UdpHandler


class ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", required=True, help="banner, e.g. ACADEMIC or ADMIN")
    parser.add_argument("--tcp-port", type=int, default=8000)
    parser.add_argument("--udp-port", type=int, default=9000)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()

    log = ServiceLog(args.log)
    tcp = ReusableTCPServer(("0.0.0.0", args.tcp_port), make_tcp_handler(args.name, log))
    udp = socketserver.ThreadingUDPServer(("0.0.0.0", args.udp_port), make_udp_handler(args.name, log))
    threading.Thread(target=udp.serve_forever, daemon=True).start()
    print(f"{args.name} service listening on TCP {args.tcp_port} / UDP {args.udp_port}", flush=True)
    try:
        tcp.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        tcp.server_close()
        udp.server_close()


if __name__ == "__main__":
    main()
