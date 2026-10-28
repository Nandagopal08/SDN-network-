#!/usr/bin/env bash
# Test launcher: starts the campus controller in the background, runs the
# automated policy tests against a fresh topology, then stops everything.
#   ./scripts/run_tests.sh
#   ./scripts/run_tests.sh --datapath user   # no OVS kernel module
set -euo pipefail
source "$(dirname "$0")/common.sh"

[[ -x "$RYU_VENV/bin/ryu-manager" ]] || die "ryu-manager not found in $RYU_VENV; run scripts/setup_ubuntu.sh"
controller_port_busy && die "port $CONTROLLER_PORT already in use; run scripts/cleanup.sh first"
$SUDO mn -c >/dev/null 2>&1 || echo "warning: 'mn -c' reported errors (continuing)"

mkdir -p "$LOG_DIR" "$REPORT_DIR"
echo "== Starting $CONTROLLER_APP (log: $CONTROLLER_LOG)"
(cd "$PROJECT_DIR" && exec "$RYU_VENV/bin/ryu-manager" --ofp-listen-host "$CONTROLLER_IP" \
    --ofp-tcp-listen-port "$CONTROLLER_PORT" "$CONTROLLER_APP") >"$CONTROLLER_LOG" 2>&1 &
CONTROLLER_PID=$!
trap 'kill $CONTROLLER_PID 2>/dev/null || true; wait $CONTROLLER_PID 2>/dev/null || true' EXIT

if ! wait_for_controller 20; then
    echo "Controller failed to start. Last log lines:" >&2
    tail -n 30 "$CONTROLLER_LOG" >&2
    exit 1
fi
echo "== Controller listening on $CONTROLLER_IP:$CONTROLLER_PORT (pid $CONTROLLER_PID)"

set +e
(cd "$PROJECT_DIR" && $SUDO python3 "$TEST_SCRIPT" --controller-ip "$CONTROLLER_IP" \
    --controller-port "$CONTROLLER_PORT" --controller-log "$CONTROLLER_LOG" --report-dir "$REPORT_DIR" "$@")
STATUS=$?
set -e
echo "== Controller log: $CONTROLLER_LOG"
exit $STATUS
