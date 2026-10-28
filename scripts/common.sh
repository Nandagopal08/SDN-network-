# Shared settings for the Ubuntu-side scripts. Source it, do not execute it.
# Every value can be overridden from the environment, e.g.
#   RYU_VENV=/opt/ryu-venv ./scripts/run_tests.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RYU_VENV="${RYU_VENV:-$HOME/.venvs/ryu}"
CONTROLLER_IP="${CONTROLLER_IP:-127.0.0.1}"
CONTROLLER_PORT="${CONTROLLER_PORT:-6653}"
LOG_DIR="${LOG_DIR:-$PROJECT_ROOT/logs}"
REPORT_DIR="${REPORT_DIR:-$PROJECT_ROOT/reports}"

PROJECT_DIR="$PROJECT_ROOT/campus-access-control"
CONTROLLER_APP="campus_access_controller.py"
TOPOLOGY_SCRIPT="campus_topology.py"
TEST_SCRIPT="test_campus_access.py"
CONTROLLER_LOG="$LOG_DIR/campus_controller.log"

if [[ $EUID -eq 0 ]]; then SUDO=""; else SUDO="sudo"; fi

die() { echo "ERROR: $*" >&2; exit 1; }

controller_port_busy() {
    ss -ltnH "sport = :$CONTROLLER_PORT" | grep -q .
}

# Block until the controller is listening, or fail after $1 seconds.
wait_for_controller() {
    local timeout="${1:-15}" waited=0
    until controller_port_busy; do
        (( waited >= timeout * 10 )) && return 1
        sleep 0.1; waited=$((waited + 1))
    done
}
