#!/usr/bin/env bash
# Remove everything a crashed or interrupted run can leave behind:
# Mininet namespaces/veths, OVS bridges, controller processes, lab services.
set -uo pipefail
source "$(dirname "$0")/common.sh"

echo "== Stopping the Ryu controller"
pkill -f "ryu-manager.*campus_access_controller" && echo "stopped" || echo "none running"

echo "== Stopping lab services"
$SUDO pkill -f "campus-access-control/lab_service.py" && echo "stopped" || echo "none running"

echo "== Mininet cleanup (mn -c)"
$SUDO mn -c 2>&1 | tail -n 3

echo "== Removing leftover lab bridges"
for bridge in s1 s2 s3; do
    if $SUDO ovs-vsctl br-exists "$bridge" 2>/dev/null; then
        $SUDO ovs-vsctl del-br "$bridge" && echo "deleted $bridge"
    fi
done

$SUDO rm -f /tmp/campus_h5_service.* /tmp/campus_h6_service.* /tmp/campus_h5_service.out /tmp/campus_h6_service.out

if controller_port_busy; then
    echo "WARNING: something still listens on port $CONTROLLER_PORT:"
    $SUDO ss -ltnp "sport = :$CONTROLLER_PORT"
else
    echo "== Port $CONTROLLER_PORT is free. Clean."
fi
