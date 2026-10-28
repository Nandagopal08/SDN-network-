#!/usr/bin/env bash
# Read-only health check INSIDE the Ubuntu VM. Prints OK/FAIL per item.
#   ./scripts/check_environment.sh
set -uo pipefail
source "$(dirname "$0")/common.sh"
FAILURES=0

check() {  # check "<description>" <command...>
    local description="$1"; shift
    local output
    if output="$("$@" 2>&1)"; then
        printf "  OK    %-38s %s\n" "$description" "$(echo "$output" | head -n 1)"
    else
        printf "  FAIL  %-38s %s\n" "$description" "$(echo "$output" | head -n 1)"
        FAILURES=$((FAILURES + 1))
    fi
}

echo "== System"
check "Linux kernel"            uname -srm
check "Ubuntu release"          bash -c '. /etc/os-release && echo "$PRETTY_NAME"'
check "ARM64 architecture"      bash -c '[[ $(uname -m) == aarch64 ]] && echo aarch64 || { echo "got $(uname -m)"; false; }'

echo "== Mininet / Open vSwitch"
check "mininet installed"       mn --version
check "mininet python module"   python3 -c "import mininet.net; print('import ok')"
check "ovs-vsctl present"       bash -c 'ovs-vsctl --version | head -n 1'
check "ovs service active"      systemctl is-active openvswitch-switch
check "ovs-vswitchd running"     pgrep -x ovs-vswitchd
check "ovsdb reachable"         $SUDO ovs-vsctl --timeout=5 show
check "openvswitch kernel mod"  bash -c 'lsmod | grep -q "^openvswitch" && echo loaded || { echo "not loaded (sudo modprobe openvswitch, or use --datapath user)"; false; }'
check "ethtool present"         bash -c 'command -v ethtool'

echo "== Ryu controller environment ($RYU_VENV)"
check "venv python"             "$RYU_VENV/bin/python" --version
check "ryu-manager"             "$RYU_VENV/bin/ryu-manager" --version
check "ryu OpenFlow 1.3 import" "$RYU_VENV/bin/python" -c "from ryu.ofproto import ofproto_v1_3; import eventlet; print('eventlet', eventlet.__version__)"

echo "== Controller endpoint $CONTROLLER_IP:$CONTROLLER_PORT"
if controller_port_busy; then
    echo "  INFO  port $CONTROLLER_PORT in use (a controller is running):"
    ss -ltnH "sport = :$CONTROLLER_PORT" | sed 's/^/        /'
    pgrep -af ryu-manager | sed 's/^/        /'
else
    echo "  INFO  port $CONTROLLER_PORT free (no controller running)"
fi

echo
if (( FAILURES )); then
    echo "$FAILURES check(s) failed - see README.md > Troubleshooting"
    exit 1
fi
echo "All checks passed."
