#!/usr/bin/env bash
# pre-commit-okf — fully automatic pipeline for git commits.
#
# Runs before every git commit:
#   1. memory-kb-sync — mirror Pi memory → raw/memory-sync/
#   2. okf-sync — regenerate OKF projection layer
#   3. Check for uncompiled sessions (but don't compile — that happens in Pi)
#
# The LLM-heavy compile step runs in the Pi session itself (compile.py).
# This hook handles the fast, model-free projection layer.

set -euo pipefail

find_project_root() {
  local d="$PWD"
  while [ "$d" != "/" ]; do
    if [ -d "$d/ai-knowledge-base" ]; then
      echo "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  d="$PWD"
  while [ "$d" != "/" ]; do
    if [ -d "$d/.git" ]; then
      echo "$d"
      return 0
    fi
    d="$(dirname "$d")"
  done
  return 1
}

PROJECT=$(find_project_root) || {
  echo "  ⏭️  No project root with ai-knowledge-base/ found"
  exit 0
}

echo "  🪝 pre-commit-okf: $(basename $PROJECT)"
cd "$PROJECT"

# ── 1. memory-kb-sync ──────────────────────────────────────────────
if command -v memory-kb-sync &>/dev/null; then
  memory-kb-sync "$PROJECT" 2>&1 | sed 's/^/    /'
fi

# ── 2. okf-sync — regenerate OKF layer ─────────────────────────────
OKF_SCRIPT="$PROJECT/scripts/okf-sync.py"
if [ -f "$OKF_SCRIPT" ]; then
  if command -v uv &>/dev/null && [ -f "$PROJECT/uv.lock" ]; then
    uv run python "$OKF_SCRIPT" --apply --quiet 2>&1 | sed 's/^/    /'
  elif command -v python3 &>/dev/null; then
    python3 "$OKF_SCRIPT" --apply --quiet 2>&1 | sed 's/^/    /'
  else
    python "$OKF_SCRIPT" --apply --quiet 2>&1 | sed 's/^/    /'
  fi
  echo "  ✅ OKF synced"
  # Re-stage OKF and llms.txt changes
  git add "$PROJECT/okf/" "$PROJECT/llms.txt" 2>/dev/null || true
fi

# ── 3. Check for uncompiled sessions (compile runs inside Pi) ──────
COMPILE_SCRIPT="$PROJECT/ai-knowledge-base/scripts/compile.py"
if [ -f "$COMPILE_SCRIPT" ]; then
  DRY_OUTPUT=$(
    if command -v uv &>/dev/null && [ -f "$PROJECT/uv.lock" ]; then
      uv run python "$COMPILE_SCRIPT" --dry-run 2>/dev/null
    else
      python3 "$COMPILE_SCRIPT" --dry-run 2>/dev/null || python "$COMPILE_SCRIPT" --dry-run 2>/dev/null
    fi
  )
  if echo "$DRY_OUTPUT" | grep -q "Files to compile"; then
    # Stage raw/sessions/ so they're committed
    git add "$PROJECT/ai-knowledge-base/raw/sessions/" 2>/dev/null || true
    echo "  📝 Uncompiled sessions detected (will compile in next Pi session)"
  fi
fi

# Re-stage memory sync
git add "$PROJECT/ai-knowledge-base/raw/memory-sync/" 2>/dev/null || true
