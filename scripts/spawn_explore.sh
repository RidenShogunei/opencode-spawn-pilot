#!/bin/bash
# spawn_explore.sh — Wrapper for actual explore subagent calls
# Records every real spawn event to spawn_events.jsonl
#
# Usage:
#   ./spawn_explore.sh "<task_id>" "<run_id>" "<workdir>" "<exploration_task>"
#
# Writes: spawn_events.jsonl with one JSON object per real spawn call

set -e

TASK_ID="$1"
RUN_ID="$2"
WORKDIR="$3"
shift 3
EXPLORATION_TASK="$*"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
EVENTS_DIR="$PROJECT_DIR/outputs/opencode_spawn_pilot/spawn_events"
EVENTS_FILE="$EVENTS_DIR/spawn_events.jsonl"

if [ -z "$TASK_ID" ] || [ -z "$RUN_ID" ] || [ -z "$WORKDIR" ]; then
    echo "ERROR: Missing required arguments" >&2
    echo "Usage: spawn_explore.sh <task_id> <run_id> <workdir> <exploration_task>" >&2
    exit 1
fi

mkdir -p "$EVENTS_DIR"

# Temp files for child outputs
STDOUT_F=$(mktemp)
STDERR_F=$(mktemp)

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EXIT_CODE=0

OPENCODE_BIN="/home/jinxu/.opencode/bin/opencode"

$OPENCODE_BIN run \
    --agent explore \
    --dir "$WORKDIR" \
    --format json \
    --print-logs \
    "TASK: $EXPLORATION_TASK" \
    > "$STDOUT_F" 2> "$STDERR_F" \
    || EXIT_CODE=$?

# Read outputs (cap at 50KB)
STDOUT_CONTENT=$(cat "$STDOUT_F" 2>/dev/null | head -c 51200 || echo "")
STDERR_CONTENT=$(cat "$STDERR_F" 2>/dev/null | head -c 51200 || echo "")

# Write event to JSONL using Python for safe JSON encoding
python3 - "$TIMESTAMP" "$TASK_ID" "$RUN_ID" "$WORKDIR" "$EXPLORATION_TASK" "$EXIT_CODE" "$STDOUT_CONTENT" "$STDERR_CONTENT" "$STDOUT_F" "$STDERR_F" << 'PYEOF'
import sys, json, os

ts, task_id, run_id, workdir, exploration_task = sys.argv[1:6]
exit_code = int(sys.argv[6])
stdout_content = sys.argv[7]
stderr_content = sys.argv[8]
stdout_file = sys.argv[9]
stderr_file = sys.argv[10]

events_file = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "outputs", "opencode_spawn_pilot", "spawn_events", "spawn_events.jsonl"
)
os.makedirs(os.path.dirname(events_file), exist_ok=True)

event = {
    "timestamp": ts,
    "task_id": task_id,
    "run_id": run_id,
    "workdir": workdir,
    "exploration_task": exploration_task[:2000],
    "exit_code": exit_code,
    "child_stdout_lines": len(stdout_content.splitlines()) if stdout_content else 0,
    "child_stderr_lines": len(stderr_content.splitlines()) if stderr_content else 0,
    "child_stdout_preview": stdout_content[:500],
    "child_stderr_preview": stderr_content[:500],
}

with open(events_file, "a", encoding="utf-8") as f:
    f.write(json.dumps(event, ensure_ascii=False) + "\n")

# Echo child stdout to our stdout for parent agent
sys.stdout.write(stdout_content)
PYEOF

PY_EXIT=$?

rm -f "$STDOUT_F" "$STDERR_F"

if [ $PY_EXIT -ne 0 ]; then
    exit $PY_EXIT
fi
exit $EXIT_CODE
