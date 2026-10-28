#!/usr/bin/env bash
# One-time setup INSIDE the Ubuntu ARM64 VM (22.04, 24.04 or 26.04).
#   ./scripts/setup_ubuntu.sh
# Installs Mininet + Open vSwitch + test tools from apt, and Ryu into a
# Python 3.9 virtualenv at $RYU_VENV (default ~/.venvs/ryu).
set -euo pipefail
source "$(dirname "$0")/common.sh"

[[ "$(uname -s)" == "Linux" ]] || die "run this inside the Ubuntu VM, not on macOS"
. /etc/os-release
echo "== Detected $PRETTY_NAME on $(uname -m)"
case "$VERSION_ID" in
    22.04|24.04|26.04) ;;
    *) echo "WARNING: only Ubuntu 22.04/24.04/26.04 were checked; continuing" ;;
esac

echo "== Installing system packages"
$SUDO apt-get update
$SUDO apt-get install -y mininet openvswitch-switch openvswitch-common ethtool \
    netbase iproute2 iputils-ping net-tools tcpdump curl ca-certificates python3 psmisc
$SUDO systemctl enable --now openvswitch-switch

echo "== Loading the openvswitch kernel module"
if ! $SUDO modprobe openvswitch; then
    echo "WARNING: openvswitch kernel module unavailable. Use --datapath user with the topology/test scripts."
fi

echo "== Installing uv (Python version manager) for a Python 3.9 Ryu environment"
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/0.4.30/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi
command -v uv >/dev/null || die "uv installation failed"

echo "== Creating Ryu virtualenv at $RYU_VENV"
mkdir -p "$(dirname "$RYU_VENV")"
uv venv --python 3.9 "$RYU_VENV"
uv pip install --python "$RYU_VENV/bin/python" "setuptools==58.2.0" wheel "pbr==7.1.3" pip
"$RYU_VENV/bin/pip" install --no-build-isolation -r "$PROJECT_ROOT/requirements-ryu.txt"

echo "== Versions"
"$RYU_VENV/bin/ryu-manager" --version
mn --version
ovs-vsctl --version | head -n 1

chmod +x "$PROJECT_ROOT"/scripts/*.sh "$PROJECT_ROOT"/scripts/mac/*.sh
echo "== Done. Next: ./scripts/check_environment.sh"
