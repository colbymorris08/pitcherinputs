#!/bin/bash
cd /Users/colbymorris/pitcherinputs || exit 1
LOG=data/build_release_lh_cf.log
PIDF=data/build.pid
echo "$(date -u +%Y-%m-%dT%H:%MZ) supervise_release start $$" >> "$LOG"
while true; do
  if grep -q 'DONE' "$LOG" 2>/dev/null && grep -q 'OWNER RELEASE COMPLETE' "$LOG" 2>/dev/null; then
    echo "$(date -u +%Y-%m-%dT%H:%MZ) supervise_release complete" >> "$LOG"
    exit 0
  fi
  # already running?
  if [[ -f "$PIDF" ]]; then
    opid=$(cat "$PIDF" 2>/dev/null || true)
    if [[ -n "$opid" ]] && kill -0 "$opid" 2>/dev/null; then
      sleep 20
      continue
    fi
  fi
  echo "$(date -u +%Y-%m-%dT%H:%MZ) supervise_release launch" >> "$LOG"
  nohup python3 -u scripts/rebuild_release_lh_cf_owner.py >> "$LOG" 2>&1 &
  echo $! > "$PIDF"
  wait $! || true
  code=$?
  echo "$(date -u +%Y-%m-%dT%H:%MZ) supervise_release exit=$code" >> "$LOG"
  # Detect clean finish via last catalog write + DONE from build_catalog
  if tail -n 30 "$LOG" | grep -q '^DONE$'; then
    echo "$(date -u +%Y-%m-%dT%H:%MZ) OWNER RELEASE COMPLETE" >> "$LOG"
    exit 0
  fi
  sleep 5
done
