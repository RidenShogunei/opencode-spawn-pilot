#!/bin/bash
# Spawns OpenCode explore subagent for a given task
# Args: <task_id> <run_id> <workdir> "<exploration_task>"

set -e

TASK_ID="$1"
RUN_ID="$2"
WORKDIR="$3"
EXPLORATION_TASK="$4"
OPENCODE="/home/jinxu/.opencode/bin/opencode"
MODEL="local/qwen35-9b"
EVENTS_FILE="$WORKDIR/spawn_events.jsonl"

mkdir -p "$WORKDIR"

# Run explore agent
START=$(date +%s.%N)
OUTPUT=$(mktemp)
ERRORS=$(mktemp)

timeout 60 bash "$OPENCODE" run \
    --agent explore \
    --model "$MODEL" \
    --format json \
    --title "explore-$TASK_ID" \
    -- "$EXPLORATION_TASK" \
    > "$OUTPUT" 2> "$ERRORS" \
    || EXIT_CODE=$?

END=$(date +%s.%N)
DURATION=$(echo "$END - $START" | bc)

# Extract text output from JSON lines
TEXT_OUTPUT=$(grep '"type":"text"' "$OUTPUT" 2>/dev/null | \
    python3 -c "
import sys, json
texts = []
for line in sys.stdin:
    try:
        obj = json.loads(line)
        if 'part' in obj and 'text' in obj['part']:
            texts.append(obj['part']['text'])
    except: pass
print('\n'.join(texts))
" 2>/dev/null || echo "(no output)")

# Write spawn event
python3 -c "
import json, datetime, sys

event = {
    'timestamp': datetime.datetime.now().isoformat(),
    'task_id': '$TASK_ID',
    'run_id': '$RUN_ID',
    'exploration_task': '''$EXPLORATION_TASK''',
    'exit_code': ${EXIT_CODE:-0},
    'duration': $DURATION,
    'child_stdout_preview': '''$TEXT_OUTPUT'''[:1000],
    'child_stderr_preview': open('$ERRORS').read()[:500] if True else '',
}

with open('$EVENTS_FILE', 'a') as f:
    f.write(json.dumps(event) + '\n')
"

rm -f "$OUTPUT" "$ERRORS"
