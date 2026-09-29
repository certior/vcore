#!/usr/bin/env bash
# Fetch anthropics/claude-code-action at the pinned commit and install its locked
# production dependencies. The oracle runs this source unmodified.
set -euo pipefail

COMMIT=bf38e86e58df9ebf3420326d019f955bb3be64dd   # tag v1.0.225
SOURCE=${CLAUDE_CODE_ACTION_SOURCE:-https://github.com/anthropics/claude-code-action}
HERE=$(cd "$(dirname "$0")" && pwd)
DEST="$HERE/vendor/claude-code-action"
BUN=${BUN:-$(command -v bun || echo "$HOME/.bun/bin/bun")}

if [ ! -d "$DEST/.git" ]; then
  mkdir -p "$HERE/vendor"
  git clone --quiet "$SOURCE" "$DEST"
fi
git -C "$DEST" fetch --quiet origin "$COMMIT" 2>/dev/null || true
git -C "$DEST" -c advice.detachedHead=false checkout --quiet "$COMMIT"
test "$(git -C "$DEST" rev-parse HEAD)" = "$COMMIT"
if [ -n "$(git -C "$DEST" status --porcelain --untracked-files=no)" ]; then
  echo "vendor/claude-code-action has local modifications; refusing to run" >&2
  exit 1
fi

# action.yml installs with `bun install --production`; --frozen-lockfile pins bun.lock.
(cd "$DEST" && "$BUN" install --production --frozen-lockfile)
echo "claude-code-action $(git -C "$DEST" describe --tags --exact-match 2>/dev/null || true) @ $COMMIT ready; bun $("$BUN" --version)"
