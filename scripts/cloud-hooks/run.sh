#!/bin/sh
# Cloud Context Guard runner. No cost or execution for local Claude Code.
[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0
[ -n "$1" ] || exit 0
DIR=$(dirname "$0")
SCRIPT=$1
shift
case "$SCRIPT" in *.py) ;; *) exit 0 ;; esac
[ -f "$DIR/$SCRIPT" ] || exit 0
exec python3 "$DIR/$SCRIPT" "$@"
