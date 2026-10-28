#!/usr/bin/env bash
# Start the campus Ryu controller in the foreground (Ctrl-C to stop).
#   ./scripts/run_controller.sh
# Output is shown and also written to logs/campus_controller.log.
set -euo pipefail
source "$(dirname "$0")/common.sh"

[[ -x "$RYU_VENV/bin/ryu-manager" ]] || die "ryu-manager not found in $RYU_VENV; run scripts/setup_ubuntu.sh"
if controller_port_busy; then
    die "port $CONTROLLER_PORT is already in use (another controller?). Stop it or run scripts/cleanup.sh"
fi
mkdir -p "$LOG_DIR"
echo "Starting $CONTROLLER_APP on $CONTROLLER_IP:$CONTROLLER_PORT (log: $CONTROLLER_LOG)"
cd "$PROJECT_DIR"
"$RYU_VENV/bin/ryu-manager" --ofp-listen-host "$CONTROLLER_IP" --ofp-tcp-listen-port "$CONTROLLER_PORT" \
    "$CONTROLLER_APP" 2>&1 | tee "$CONTROLLER_LOG"
