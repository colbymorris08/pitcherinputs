# Pitcher Inputs

Separate LBPRC site for streaming Synergy clips while filling **Time to Home** / pitcher notes on Santurce.

- **ALL** tab → every league catcher, grouped by team (throw downs to 2B)
- Team tabs → pitchers (A–Z) + that team's catchers
- Pitchers: **LowHome + CF** release clips + runner-on-1B FB/OS for time-to-home
- Catchers: up to **5** SB/CS of 2B clips (Synergy `catcherId` — MLB/MiLB/LBPRC/…), labeled by field catcher
- **No local video** — browser streams Synergy short `/clip/` URLs only

## Build catalog

```bash
cd /Users/colbymorris/pitcherinputs
# Full rebuild (skips SAN by default)
python3 scripts/build_catalog.py

# Pitchers only — fill LowHome + CF release slots (reuse catchers)
PI_PITCHERS_ONLY=1 PI_RELEASE_FILL=1 python3 scripts/build_catalog.py

# Catchers only — 5 clips/catcher from any league
PI_CATCHERS_ONLY=1 PI_CATCHER_CLIPS=5 python3 scripts/build_catalog.py
```

Writes `data/catalog.json` (URLs + metadata). Resumes via `data/catalog_checkpoint.json`.

## Local preview

```bash
python3 -m http.server 8765
# open http://localhost:8765
```
