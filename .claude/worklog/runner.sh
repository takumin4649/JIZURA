#!/bin/sh
# Portable project hook; PC uses its shared global hook instead.
[ -f "$HOME/ai-rules/tools/worklog_auto.py" ] && exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
FILE="$ROOT/.claude/worklog/engine.py"
[ -f "$FILE" ] || exit 0
if command -v python3 >/dev/null 2>&1; then
  exec python3 -X utf8 "$FILE"
elif command -v python >/dev/null 2>&1; then
  exec python -X utf8 "$FILE"
fi
exit 0
