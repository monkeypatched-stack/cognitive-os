#!/bin/zsh
# Fully detached launcher for the workspace dev server.
# Run as:  ./scripts/run_workspace_fe.sh
# Detaches from the calling shell entirely (double-fork via nohup + disown in a
# subshell) so it is NOT killed when the invoking terminal/command exits.
cd "$(dirname "$0")/.." || exit 1
cd apps/workspace || exit 1

export NEXT_PUBLIC_API_TARGET="${NEXT_PUBLIC_API_TARGET:-http://localhost:8031}"
export NEXT_PUBLIC_LIVEKIT_URL="${NEXT_PUBLIC_LIVEKIT_URL:-ws://localhost:7880}"

LOG=/tmp/workspace-dev.log
rm -f "$LOG"

nohup npm run dev > "$LOG" 2>&1 < /dev/null &
PID=$!
disown $PID 2>/dev/null || true

echo "started pid=$PID target=$NEXT_PUBLIC_API_TARGET livekit=$NEXT_PUBLIC_LIVEKIT_URL"
echo "log: $LOG"
