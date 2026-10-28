#!/usr/bin/env bash
# macOS side: confirm the host is Apple Silicon and the required apps exist.
#   ./scripts/mac/check_mac.sh
set -uo pipefail
[[ "$(uname -s)" == "Darwin" ]] || { echo "Run this on the Mac, not in the VM."; exit 1; }

echo "== Mac"
echo "  Architecture : $(uname -m)   (expect arm64)"
echo "  CPU          : $(sysctl -n machdep.cpu.brand_string)"
echo "  Memory       : $(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 )) GB"
sw_vers | sed 's/^/  /'

echo "== Tools"
for app in UTM "Visual Studio Code"; do
    if [[ -d "/Applications/$app.app" ]]; then echo "  OK    $app.app"; else echo "  MISSING $app.app"; fi
done
for cmd in code ssh rsync; do
    if command -v "$cmd" >/dev/null; then echo "  OK    $cmd -> $(command -v "$cmd")"; else echo "  MISSING $cmd"; fi
done
if [[ "$(uname -m)" != "arm64" ]]; then
    echo "WARNING: not Apple Silicon - use an amd64 Ubuntu image instead of ARM64."
fi
