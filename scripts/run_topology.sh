#!/usr/bin/env bash
# Start the campus Mininet topology with its interactive CLI.
# Start the controller first (scripts/run_controller.sh).
#   ./scripts/run_topology.sh [--datapath user]
set -euo pipefail
source "$(dirname "$0")/common.sh"

controller_port_busy || die "no controller listening on $CONTROLLER_PORT; start it first: scripts/run_controller.sh"
cd "$PROJECT_DIR"
exec $SUDO python3 "$TOPOLOGY_SCRIPT" --controller-ip "$CONTROLLER_IP" --controller-port "$CONTROLLER_PORT" "$@"
