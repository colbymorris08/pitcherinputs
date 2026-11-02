# Pitcher Inputs

Separate LBPRC site for streaming Synergy clips while filling **Time to Home** / pitcher notes on Santurce.

- Team tabs → pitchers (A–Z) + catchers
- Pitchers: runner on 1B · 1 fastball + 1 offspeed
- Catchers: throw downs to 2B (SB / CS)
- **No local video** — browser streams Synergy HLS only

## Build catalog

```bash
cd /Users/colbymorris/pitcherinputs
python3 scripts/build_catalog.py
```

Writes `data/catalog.json` (URLs + metadata). Resumes via `data/catalog_checkpoint.json`.

## Local preview

```bash
python3 -m http.server 8765
# open http://localhost:8765
```
