#!/usr/bin/env bash
# setup-mcps.sh — install the 5 MCP servers used by pokemir (idempotent).
#
# MCPs installed:
#   - context7  (local scope) — OSS library docs
#   - github    (user scope)  — repo / issue / PR  [intentional: account-level token, cross-project]
#   - semgrep   (local scope) — Python static analysis (requires semgrep in PATH)
#   - filesystem(local scope) — read pokemir project dir
#   - postgres  (local scope) — DB query (DSN with rotated password)
#
# NOT installed (intentional — not relevant to pokemir):
#   - cloudbase / chrome-devtools / Google Drive  (used in flordate project, not here)
#
# Usage:
#   bash tools/setup-mcps.sh
#
# Environment variables (optional):
#   POKEMIR_FS_PATH   — filesystem MCP allowed dir (default: $(pwd))
#   POKEMIR_PG_DSN    — postgres DSN (default: prompts interactively via read -s)
#
# Authorization: requirement-discussions/2026-05-17_20-30-00_*.md §阶段 8.4 (confirmed)
# Change-log:    change-logs/2026-05-18_<ts>_R10立项与setup-mcps脚本.md

set -euo pipefail

FS_PATH="${POKEMIR_FS_PATH:-$(pwd)}"

# ── Helpers ────────────────────────────────────────────────

already_installed() {
    local name="$1"
    claude mcp list 2>/dev/null | grep -q "^${name}:"
}

skip_or_install() {
    local name="$1"; shift
    if already_installed "$name"; then
        echo "  ⏭  $name already installed, skipping"
        return 0
    fi
    echo "  +  installing $name..."
    claude mcp add "$name" "$@"
}

# ── Sanity ─────────────────────────────────────────────────

if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: claude CLI not found in PATH" >&2
    echo "  Install Claude Code first: https://docs.anthropic.com/claude/docs/claude-code" >&2
    exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
    echo "ERROR: npx not found in PATH (need Node.js + npm)" >&2
    exit 1
fi

echo "════════════════════════════════════════════════════"
echo "  pokemir MCP setup"
echo "  filesystem path: $FS_PATH"
echo "════════════════════════════════════════════════════"
echo ""

# ── github: user scope (account-level token, cross-project sharing OK) ────

echo "▶ [user scope] github — repo / issue / PR"
skip_or_install github -s user -- npx -y @modelcontextprotocol/server-github
echo ""

# ── local scope: project-specific (no cross-project pollution) ──────────

echo "▶ [local scope] context7 — OSS library docs"
skip_or_install context7 -s local -- npx -y @upstash/context7-mcp
echo ""

echo "▶ [local scope] semgrep — Python static analysis"
if ! command -v semgrep >/dev/null 2>&1; then
    echo "  ⚠️  semgrep not in PATH — skipping."
    echo "      install via: .venv/bin/pip install semgrep  (R-10 forbids pipx)"
else
    skip_or_install semgrep -s local -- semgrep mcp
fi
echo ""

echo "▶ [local scope] filesystem — read $FS_PATH"
skip_or_install filesystem -s local -- \
    npx -y @modelcontextprotocol/server-filesystem "$FS_PATH"
echo ""

echo "▶ [local scope] postgres — query poker_assistant DB"
if already_installed postgres; then
    echo "  ⏭  postgres already installed, skipping"
else
    DSN="${POKEMIR_PG_DSN:-}"
    if [ -z "$DSN" ]; then
        echo "  Enter PostgreSQL DSN (input hidden; format: postgresql://user:pw@host:port/db)"
        echo "  Or press Enter to skip postgres install."
        read -rsp "  DSN> " DSN
        echo ""
    fi
    if [ -z "$DSN" ]; then
        echo "  ⏭  no DSN provided, skipping postgres"
    else
        echo "  +  installing postgres..."
        claude mcp add postgres -s local -- \
            npx -y @modelcontextprotocol/server-postgres "$DSN"
        unset DSN
    fi
fi
echo ""

# ── Done ────────────────────────────────────────────────────

echo "════════════════════════════════════════════════════"
echo "✅ Setup complete."
echo ""
echo "Next steps:"
echo "  1. Verify: claude mcp list"
echo "  2. Restart Claude Code session (Ctrl+D / close terminal) for new MCPs to load"
echo "  3. Check ~/.claude.json permissions: stat -c %a ~/.claude.json  → should be 600"
echo "════════════════════════════════════════════════════"
