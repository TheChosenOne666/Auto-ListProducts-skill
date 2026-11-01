#!/usr/bin/env bash
# Symlink the skill into agent skill directories so `git pull` updates it in place.
# Usage: ./install.sh [--qoder] [--claude] [--all-agents]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_SRC="$ROOT/skills/auto-list-products"
SKILL_NAME="auto-list-products"

[ -f "$SKILL_SRC/SKILL.md" ] || { echo "error: $SKILL_SRC/SKILL.md not found" >&2; exit 1; }

TARGET="all"
for arg in "$@"; do
  case "$arg" in
    --qoder) TARGET="qoder" ;;
    --claude) TARGET="claude" ;;
    --all-agents) TARGET="all" ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done

link_into() {
  local dir="$1"
  mkdir -p "$dir"
  local dest="$dir/$SKILL_NAME"
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    echo "skip $dest (exists and is not a symlink)"
    return
  fi
  ln -sfn "$SKILL_SRC" "$dest"
  echo "linked $dest -> $SKILL_SRC"
}

case "$TARGET" in
  qoder) link_into "$HOME/.qoder-cn/skills" ;;
  claude) link_into "$HOME/.claude/skills" ;;
  all)
    link_into "$HOME/.qoder-cn/skills"
    link_into "$HOME/.claude/skills"
    ;;
esac

echo "done. reload skills in your agent, then invoke /$SKILL_NAME"
