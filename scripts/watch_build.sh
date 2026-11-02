#!/bin/bash
cd /Users/colbymorris/pitcherinputs || exit 1
export PI_SKIP_TEAMS="${PI_SKIP_TEAMS:-SAN}"
export PYTHONUNBUFFERED=1
LOG=data/build.log
echo "WATCH: start $$ $(date -u +%H:%M:%SZ) skip=$PI_SKIP_TEAMS" >> "$LOG"
while true; do
  if grep -qx 'DONE' "$LOG" 2>/dev/null; then
    echo "WATCH: complete $(date -u +%H:%M:%SZ)" >> "$LOG"
    exit 0
  fi
  echo "WATCH: launch $(date -u +%H:%M:%SZ)" >> "$LOG"
  /usr/bin/env python3 -u scripts/build_catalog.py >> "$LOG" 2>&1
  code=$?
  echo "WATCH: exit=$code $(date -u +%H:%M:%SZ)" >> "$LOG"
  if grep -qx 'DONE' "$LOG" 2>/dev/null; then
    echo "WATCH: complete $(date -u +%H:%M:%SZ)" >> "$LOG"
    exit 0
  fi
  sleep 8
done
