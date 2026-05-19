#!/usr/bin/env bash
# sync-from-vps.sh — pull latest pokemir code from Linux VPS to local Windows test machine.
#
# Usage: run from Git Bash on Windows, from the local pokemir working directory.
#   $ cd /c/pokemir-test && bash tools/sync-from-vps.sh
#
# Configure via environment variables (or edit defaults below):
#   POKEMIR_VPS_HOST   — SSH host (e.g. my-vps.example.com)
#   POKEMIR_VPS_USER   — SSH user (e.g. alxe)
#   POKEMIR_VPS_PATH   — remote project path with trailing slash (e.g. /home/alxe/project/pokemir/)
#   POKEMIR_LOCAL_PATH — local destination (default: current directory)
#
# ⚠️ Read-only sync: any local edits in the destination will be overwritten.
#    Editing on the Windows side is forbidden by docs/dev-workflow.md §4.

set -euo pipefail

VPS_HOST="${POKEMIR_VPS_HOST:-}"
VPS_USER="${POKEMIR_VPS_USER:-}"
VPS_PATH="${POKEMIR_VPS_PATH:-}"
LOCAL_PATH="${POKEMIR_LOCAL_PATH:-./}"

if [ -z "$VPS_HOST" ] || [ -z "$VPS_USER" ] || [ -z "$VPS_PATH" ]; then
    cat >&2 <<EOF
ERROR: missing required environment variables.

Set these once in your Git Bash session (or in ~/.bashrc):
    export POKEMIR_VPS_HOST=your-vps-host
    export POKEMIR_VPS_USER=your-vps-user
    export POKEMIR_VPS_PATH=/home/your-vps-user/project/pokemir/

Then re-run this script.
EOF
    exit 1
fi

echo "Syncing from ${VPS_USER}@${VPS_HOST}:${VPS_PATH}"
echo "         → ${LOCAL_PATH}"
echo "⚠️  Local edits in ${LOCAL_PATH} will be OVERWRITTEN."
echo ""

rsync -avz --delete \
    --exclude '.git' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'tests/output/' \
    --exclude '.env' \
    --exclude 'models/' \
    --exclude '.venv/' \
    --exclude '.pytest_cache/' \
    --exclude '.docker-data/' \
    --exclude '.cache/' \
    "${VPS_USER}@${VPS_HOST}:${VPS_PATH}" \
    "${LOCAL_PATH}"

echo ""
echo "✅ Sync complete. Next steps:"
echo "   pytest tests/ -v          # run test suite"
echo "   python main.py pipeline   # run the capture pipeline"
