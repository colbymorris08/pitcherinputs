#!/usr/bin/env python3
"""Build pitcherinputs data/catalog.json — Synergy HLS URLs only (no local mp4).

Pitchers: runner on 1B → 1 fastball + 1 offspeed clip each (A–Z on site).
Catchers: SB/CS of 2B throw-down clips.
"""
from __future__ import annotations

import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SAN = Path("/Users/colbymorris/Santurce2026")
sys.path.insert(0, str(SAN))

import build_advance_synergy as bas  # noqa: E402

OUT = ROOT / "data" / "catalog.json"
CKPT = ROOT / "data" / "catalog_checkpoint.json"
ROSTER = SAN / "data" / "lbprc_2025_rosters.json"

TEAM_IDS = dict(bas.OPP_TEAMS)
TEAM_IDS["SAN"] = bas.SAN_TEAM

FB = {"FF", "FA", "SI", "FT", "FB", "FASTBALL", "SINKER", "FOUR-SEAM", "FOURSEAM", "2-SEAM", "TWOSEAM"}
OS = {"CH", "CU", "CB", "SL", "SW", "FS", "SPL", "KC", "EP", "SC", "CHANGEUP", "CURVE", "CURVEBALL",
      "SLIDER", "SWEEPER", "SPLITTER", "SPLIT", "KNUCKLE"}

MAX_EVENTS_P = int(__import__("os").environ.get("PI_MAX_EVENTS_P", "400"))
MAX_EVENTS_C = int(__import__("os").environ.get("PI_MAX_EVENTS_C", "600"))


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def last_name(name: str) -> str:
    parts = [p for p in str(name or "").split() if p]
    return parts[-1] if parts else ""


def norm_pitch(ev: dict) -> str:
    pitch = ev.get("pitch") or {}
    raw = (
        pitch.get("pitchKind")
        or pitch.get("pitchType")
        or pitch.get("type")
        or ev.get("pitchKind")
        or ""
    )
    return str(raw).strip().upper()


def is_fb(code: str) -> bool:
    c = code.upper().replace(" ", "")
    if c in FB:
        return True
    return any(x in c for x in ("FAST", "SINK", "FOUR", "2SEAM", "TWOSEAM"))


def is_os(code: str) -> bool:
    c = code.upper().replace(" ", "")
    if c in OS:
        return True
    return any(x in c for x in ("CHANGE", "CURVE", "SLIDE", "SWEEP", "SPLIT", "CUTTER" if False else "CHANGE"))


def runner_on_1b(ev: dict) -> bool:
    start = ((ev.get("runners") or {}).get("runnerConfigurationStart")) or {}
    if isinstance(start, dict):
        if start.get("runnerOn1b") or start.get("runnerOnFirst") or start.get("1b"):
            return True
    # alternate shapes
    runners = ev.get("runners") or {}
    if isinstance(runners, dict):
        for key in ("on1b", "first", "runnerOnFirstBase"):
            if runners.get(key):
                return True
    return False


def steal_of_second(ev: dict) -> bool:
    """True for SB/CS of 2B (throw-down context)."""
    blob = " ".join(
        str(x or "")
        for x in (
            ev.get("eventType"),
            ev.get("eventResult"),
            ev.get("playResult"),
            ev.get("plateAppearanceResult"),
            (ev.get("pitch") or {}).get("pitchResult"),
            (ev.get("runners") or {}).get("runnerPlayResult") if isinstance(ev.get("runners"), dict) else "",
            json.dumps(ev.get("runners") or {})[:800],
        )
    ).lower()
    tokens = (
        "stolenbase", "stolen base", "steal of second", "steal 2b", "sb2",
        "caughtstealing", "caught stealing", "cs2", "thrown out at second",
        "pickoff caught stealing", "stealing second",
    )
    if any(t in blob.replace("_", " ") for t in tokens):
        # Prefer 2B; reject obvious 3B/home if explicitly only those.
        if "third" in blob and "second" not in blob and "2b" not in blob:
            return False
        if "home" in blob and "second" not in blob and "2b" not in blob and "steal" in blob:
            return False
        return True
    return False


def angle_rank(angle: str) -> int:
    a = (angle or "").strip().lower()
    if a.startswith("lowhome"):
        return 0
    if a.startswith("highhome"):
        return 1
    if a:
        return 2
    return 9


def track_playable(track: dict) -> tuple[str | None, str]:
    for seg in track.get("segments") or []:
        video = seg.get("video") or {}
        asset_id = str(video.get("id") or "")
        for var in video.get("videoVariants") or []:
            path = var.get("endpointRelativePath") or var.get("url")
            if path:
                return str(path).replace("http://", "https://"), asset_id
        if (video.get("videoComponents") or []) and asset_id:
            return f"https://asset.synergysportstech.com/{asset_id}/hls/master.m3u8", asset_id
    return None, ""


def best_clip(ev: dict) -> dict | None:
    best = None
    for track in ((ev.get("videoGroup") or {}).get("videoTracks")) or []:
        angle = str(track.get("videoCameraAngle") or track.get("cameraAngle") or "").strip()
        url, asset_id = track_playable(track)
        if not url:
            continue
        rank = angle_rank(angle)
        cand = {"url": url, "asset_id": asset_id, "angle": angle, "rank": rank}
        if best is None or rank < best["rank"]:
            best = cand
            if rank == 0:
                break
    if not best:
        return None
    game = ev.get("game") or {}
    league = game.get("league") or {}
    pitch = norm_pitch(ev)
    return {
        "url": best["url"],
        "asset_id": best["asset_id"],
        "angle": best["angle"],
        "pitch_type": pitch,
        "game_date": game.get("gameDate") or game.get("date") or "",
        "league": league.get("nameAbbrev") or league.get("name") or "",
        "event_id": str(ev.get("id") or ""),
        "result": str(ev.get("plateAppearanceResult") or (ev.get("pitch") or {}).get("pitchResult") or ""),
    }


def synergy_id_for(token: str, name: str, mlb_id: int | None) -> str | None:
    try:
        import fill_missing_count_synergy as fmc
        return fmc.resolve_sid(token, name, mlb_id)
    except Exception:
        pass
    # fallback search
    try:
        return bas.resolve_synergy_player_id(token, name) if hasattr(bas, "resolve_synergy_player_id") else None
    except Exception:
        return None


def load_ckpt() -> dict:
    if CKPT.is_file():
        return json.loads(CKPT.read_text())
    return {"pitchers": {}, "catchers": {}}


def save_ckpt(ckpt: dict) -> None:
    CKPT.parent.mkdir(parents=True, exist_ok=True)
    CKPT.write_text(json.dumps(ckpt, indent=2) + "\n")


def pick_pitcher_clips(events: list[dict]) -> list[dict]:
    fb = None
    os_ = None
    for ev in events:
        if not runner_on_1b(ev):
            continue
        clip = best_clip(ev)
        if not clip:
            continue
        code = clip.get("pitch_type") or ""
        if fb is None and is_fb(code):
            fb = {**clip, "slot": "FB", "label": "Fastball · R1"}
        elif os_ is None and is_os(code):
            os_ = {**clip, "slot": "OS", "label": "Offspeed · R1"}
        if fb and os_:
            break
    # fallback: any R1 with video if typed buckets empty
    if not fb or not os_:
        for ev in events:
            if not runner_on_1b(ev):
                continue
            clip = best_clip(ev)
            if not clip:
                continue
            if not fb:
                fb = {**clip, "slot": "FB", "label": "Fastball · R1 (best available)"}
            elif not os_ and clip.get("event_id") != fb.get("event_id"):
                os_ = {**clip, "slot": "OS", "label": "Offspeed · R1 (best available)"}
            if fb and os_:
                break
    out = []
    if fb:
        out.append(fb)
    if os_:
        out.append(os_)
    return out


def pick_catcher_clips(events: list[dict], limit: int = 3) -> list[dict]:
    out = []
    for ev in events:
        if not steal_of_second(ev):
            continue
        clip = best_clip(ev)
        if not clip:
            continue
        clip = {**clip, "slot": "THROW2B", "label": f"Throw 2B · {clip.get('result') or 'SB/CS'}"}
        out.append(clip)
        if len(out) >= limit:
            break
    return out


def write_catalog(teams: list[dict]) -> None:
    doc = {
        "updated": now(),
        "note": "HLS URLs from Synergy CDN only — no local video files.",
        "teams": teams,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"WROTE {OUT}", flush=True)


def main() -> None:
    roster = json.loads(ROSTER.read_text())
    token = bas.login_token(headed=False)
    print("Synergy login ok", flush=True)
    ckpt = load_ckpt()
    teams_out: list[dict] = []

    # stable team order
    order = ["SAN", "CAG", "CAR", "MAY", "PON", "SJU"]
    packs = []
    for key, pack in roster.items():
        abbr = pack.get("teamAbbrev")
        if abbr in order:
            packs.append(pack)
    packs.sort(key=lambda p: order.index(p.get("teamAbbrev")))

    for pack in packs:
        abbr = pack.get("teamAbbrev")
        team_id = TEAM_IDS.get(abbr)
        players = pack.get("players") or []
        pitchers = [p for p in players if str(p.get("position") or "").upper() in {"P", "RHP", "LHP", "RHR", "LHR"}]
        catchers = [p for p in players if str(p.get("position") or "").upper() == "C"]
        pitchers.sort(key=lambda p: (last_name(p.get("name") or "").lower(), p.get("name") or ""))
        catchers.sort(key=lambda p: (last_name(p.get("name") or "").lower(), p.get("name") or ""))

        team_row = {
            "abbrev": abbr,
            "name": pack.get("teamName"),
            "synergy_team_id": team_id,
            "pitchers": [],
            "catchers": [],
        }
        print(f"\n=== {abbr} pitchers={len(pitchers)} catchers={len(catchers)} ===", flush=True)

        for i, p in enumerate(pitchers, 1):
            mid = int(p.get("id") or 0) or None
            name = p.get("name") or ""
            key = f"{abbr}|{mid or name}"
            row = {
                "id": mid or key,
                "mlb_id": mid,
                "name": name,
                "last": last_name(name),
                "synergy_id": None,
                "clips": [],
            }
            cached = ckpt.get("pitchers", {}).get(key)
            if cached and cached.get("clips"):
                row.update({k: cached[k] for k in ("synergy_id", "clips") if k in cached})
                print(f"  [P {i}/{len(pitchers)}] {name} cached clips={len(row['clips'])}", flush=True)
            else:
                sid = synergy_id_for(token, name, mid)
                row["synergy_id"] = sid
                if not sid:
                    print(f"  [P {i}/{len(pitchers)}] {name} UNRESOLVED", flush=True)
                else:
                    print(f"  [P {i}/{len(pitchers)}] {name} sid={sid}", flush=True)
                    raw = bas.fetch_events_pages(token, pitcher_id=sid, max_events=MAX_EVENTS_P)
                    clips = pick_pitcher_clips(raw)
                    row["clips"] = clips
                    print(f"    events={len(raw)} clips={len(clips)}", flush=True)
                ckpt.setdefault("pitchers", {})[key] = {
                    "synergy_id": row["synergy_id"],
                    "clips": row["clips"],
                }
                save_ckpt(ckpt)
                time.sleep(0.05)
            team_row["pitchers"].append(row)

        for i, p in enumerate(catchers, 1):
            mid = int(p.get("id") or 0) or None
            name = p.get("name") or ""
            key = f"{abbr}|{mid or name}"
            row = {
                "id": mid or key,
                "mlb_id": mid,
                "name": name,
                "last": last_name(name),
                "synergy_id": None,
                "clips": [],
            }
            cached = ckpt.get("catchers", {}).get(key)
            if cached and (cached.get("clips") is not None) and cached.get("done"):
                row.update({k: cached[k] for k in ("synergy_id", "clips") if k in cached})
                print(f"  [C {i}/{len(catchers)}] {name} cached clips={len(row['clips'])}", flush=True)
            else:
                sid = synergy_id_for(token, name, mid)
                row["synergy_id"] = sid
                clips = []
                if not sid:
                    print(f"  [C {i}/{len(catchers)}] {name} UNRESOLVED", flush=True)
                else:
                    print(f"  [C {i}/{len(catchers)}] {name} sid={sid}", flush=True)
                    # Catcher: team filter + client-side steal + catcher match is expensive;
                    # try batterId-style via player id if API accepts — else team events scan.
                    raw = bas.fetch_events_pages(token, team_id=team_id, max_events=MAX_EVENTS_C) if team_id else []
                    # Prefer events where this catcher is in defense lineup
                    matched = []
                    for ev in raw:
                        defense = ev.get("defense") or {}
                        lineup = defense.get("lineup") if isinstance(defense, dict) else {}
                        catch = (lineup or {}).get("catcher") if isinstance(lineup, dict) else None
                        cid = ""
                        if isinstance(catch, dict):
                            cid = str(catch.get("id") or "")
                        if cid and cid == str(sid):
                            matched.append(ev)
                        elif steal_of_second(ev):
                            # keep steal events for name match later
                            matched.append(ev)
                    use = matched or raw
                    clips = pick_catcher_clips(use)
                    print(f"    scanned={len(raw)} matched={len(matched)} clips={len(clips)}", flush=True)
                row["clips"] = clips
                ckpt.setdefault("catchers", {})[key] = {
                    "synergy_id": row["synergy_id"],
                    "clips": row["clips"],
                    "done": True,
                }
                save_ckpt(ckpt)
                time.sleep(0.05)
            team_row["catchers"].append(row)

        teams_out.append(team_row)
        write_catalog(teams_out)  # incremental publish after each team

    write_catalog(teams_out)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
