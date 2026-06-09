#!/usr/bin/env bash
# Wait for canvas_iterate.py to finish, then PATCH the Ghost draft with the
# best-scoring canvas. Usage:
#   scripts/patch_after_loop.sh <background-task-pid-file-or-grep-pattern>

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Poll until no canvas_iterate process is running.
while pgrep -f canvas_iterate.py >/dev/null 2>&1; do
  sleep 15
done

if [ ! -f ".data/canvas-preview.json" ]; then
  echo "canvas-preview.json missing — loop produced no successful canvas" >&2
  exit 1
fi

SCORE=$(python3 -c "
import json, sys
from pathlib import Path
log = Path('.data/canvas-iteration-log.jsonl')
best = -1
if log.exists():
    for line in log.read_text().splitlines():
        try: entry = json.loads(line)
        except: continue
        if 'score' in entry and entry['score'] > best:
            best = entry['score']
print(best)
")
echo "[patch] best score from loop: $SCORE"

python3 scripts/regenerate_book_draft.py \
  --isbn 9791167740984 \
  --title "도둑맞은 집중력" --author "요한 하리" \
  --tier A \
  --ghost-site retn.kr \
  --post-id 69e3791490b39c0422d64c9a \
  --reuse
