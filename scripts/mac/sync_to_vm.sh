#!/usr/bin/env bash
# macOS side: copy the project into the Ubuntu VM over SSH.
#   ./scripts/mac/sync_to_vm.sh <vm-user>@<vm-ip> [remote-dir]
# Example:
#   ./scripts/mac/sync_to_vm.sh ubuntu@192.168.64.5
# Re-run after each edit in VS Code (or use VS Code Remote-SSH instead, see README).
set -euo pipefail
TARGET="${1:?usage: $0 <vm-user>@<vm-ip> [remote-dir]}"
REMOTE_DIR="${2:-~/sdn-mac-m4}"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

rsync -av --delete \
    --exclude 'logs/' --exclude 'reports/' --exclude '__pycache__/' --exclude '.DS_Store' \
    "$PROJECT_ROOT/" "$TARGET:$REMOTE_DIR/"
ssh "$TARGET" "chmod +x $REMOTE_DIR/scripts/*.sh $REMOTE_DIR/scripts/mac/*.sh"
echo "Synced to $TARGET:$REMOTE_DIR"
