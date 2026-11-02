#!/usr/bin/env python3
"""Build pitcherinputs data/catalog.json — short Synergy CLIP URLs (stream, no local files).

Pitchers: runner on 1B → 1 fastball + 1 offspeed clip each (A–Z on site).
Catchers: SB/CS of 2B throw-down clips.

IMPORTANT: Prefer signed progressive clip URLs:
  https://asset.synergysportstech.com/clip/{uuid}/{token}/clip.mp4
Never store full-game HLS masters (.../{asset_id}/hls/master.m3u8).
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

import os as _os

MAX_EVENTS_P = int(_os.environ.get("PI_MAX_EVENTS_P", "400"))
MAX_EVENTS_C = int(_os.environ.get("PI_MAX_EVENTS_C", "600"))
SKIP_TEAMS = {
    a.strip().upper()
    for a in _os.environ.get("PI_SKIP_TEAMS", "SAN").split(",")
    if a.strip()
}


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


def normalize_media_url(path: str | None) -> str | None:
    if not path:
        return None
    u = str(path).strip().replace("http://", "https://")
    if not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = "https://asset.synergysportstech.com" + u
    return u


def is_short_clip_url(url: str | None) -> bool:
    """True for Synergy per-play progressive / clip streams (not full-game HLS)."""
    if not url:
        return False
    u = str(url).lower()
    # Signed progressive clip: .../clip/{uuid}/{token}/clip.mp4
    if "/clip/" in u and (u.endswith(".mp4") or "/clip.mp4" in u or u.endswith(".m3u8") or "/clip.m3u8" in u):
        return True
    # Never treat bare asset HLS master as a short clip — that is usually the full game.
    if re.search(r"asset\.synergysportstech\.com/[0-9a-f-]{36}/hls/master\.m3u8", u):
        return False
    return False


def track_playable(track: dict) -> tuple[str | None, str]:
    """Prefer signed short clip URLs from videoVariants; never fall back to game HLS."""
    for seg in track.get("segments") or []:
        video = seg.get("video") or {}
        asset_id = str(video.get("id") or "")
        short: str | None = None
        for var in video.get("videoVariants") or []:
            path = normalize_media_url(var.get("endpointRelativePath") or var.get("url"))
            if not path:
                continue
            # Prefer explicit clip paths; accept other progressive mp4 under /clip/.
            if is_short_clip_url(path):
                return path, asset_id
            if short is None and "/clip/" in path.lower():
                short = path
        if short:
            return short, asset_id
        # Do NOT use asset_id → /hls/master.m3u8 — that streams the entire game.
    return None, ""


def best_clip(ev: dict) -> dict | None:
    best = None
    for track in ((ev.get("videoGroup") or {}).get("videoTracks")) or []:
        angle = str(track.get("videoCameraAngle") or track.get("cameraAngle") or "").strip()
        url, asset_id = track_playable(track)
        if not url or not is_short_clip_url(url):
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
        "kind": "clip",
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


def fetch_pitcher_until_clips(token: str, sid: str) -> tuple[list[dict], int]:
    """Page pitcher events and stop as soon as FB+OS R1 clips are found."""
    page = 200
    cap = MAX_EVENTS_P
    out: list[dict] = []
    skip = 0
    scanned = 0
    while scanned < cap:
        body = {
            "loggingPhases": [bas.LOGGING_PHASE_PITCH],
            "skip": skip,
            "take": page,
            "pitcherId": sid,
        }
        try:
            doc = bas.api_post(bas.EVENTS_FILTER_URL, token, body, timeout_s=120)
        except Exception as exc:
            print(f"    page fail skip={skip}: {exc}", flush=True)
            break
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        scanned += len(rows)
        skip += len(rows)
        print(f"  fetched {len(out)} kept / {scanned} scanned (page)", flush=True)
        clips = pick_pitcher_clips(out)
        if len(clips) >= 2:
            return clips, scanned
        total = int((doc or {}).get("totalRecords") or 0)
        if skip >= total or len(rows) < page:
            break
        time.sleep(0.08)
    return pick_pitcher_clips(out), scanned


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
        "note": "Short Synergy clip URLs only (signed /clip/.../clip.mp4) — not full-game HLS.",
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

    # stable team order (SAN skipped by default via PI_SKIP_TEAMS)
    order = ["SAN", "CAG", "CAR", "MAY", "PON", "SJU"]
    if SKIP_TEAMS:
        print(f"Skipping teams: {', '.join(sorted(SKIP_TEAMS))}", flush=True)
    packs = []
    for key, pack in roster.items():
        abbr = pack.get("teamAbbrev")
        if abbr in order and abbr not in SKIP_TEAMS:
            packs.append(pack)
    packs.sort(key=lambda p: order.index(p.get("teamAbbrev")))

    for pack in packs:
        abbr = pack.get("teamAbbrev")
        if abbr in SKIP_TEAMS:
            continue
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
            if cached and cached.get("clips") is not None and cached.get("done"):
                row.update({k: cached[k] for k in ("synergy_id", "clips") if k in cached})
                print(f"  [P {i}/{len(pitchers)}] {name} cached clips={len(row['clips'])}", flush=True)
            else:
                try:
                    sid = synergy_id_for(token, name, mid)
                    row["synergy_id"] = sid
                    if not sid:
                        print(f"  [P {i}/{len(pitchers)}] {name} UNRESOLVED", flush=True)
                    else:
                        print(f"  [P {i}/{len(pitchers)}] {name} sid={sid}", flush=True)
                        clips, scanned = fetch_pitcher_until_clips(token, sid)
                        row["clips"] = clips
                        print(f"    scanned={scanned} clips={len(clips)}", flush=True)
                except Exception as exc:
                    print(f"  [P {i}/{len(pitchers)}] {name} ERROR: {exc}", flush=True)
                ckpt.setdefault("pitchers", {})[key] = {
                    "synergy_id": row["synergy_id"],
                    "clips": row["clips"],
                    "done": True,
                }
                save_ckpt(ckpt)
                # publish partial team so site updates during long runs
                preview = teams_out + [{
                    **team_row,
                    "pitchers": team_row["pitchers"] + [row],
                    "catchers": team_row["catchers"],
                }]
                write_catalog(preview)
                time.sleep(0.05)
            team_row["pitchers"].append(row)

        # One team-level event scan reused for all catchers
        team_events: list[dict] = []
        need_catcher = any(
            not ((ckpt.get("catchers") or {}).get(f"{abbr}|{(int(p.get('id') or 0) or p.get('name'))}") or {}).get("done")
            for p in catchers
        )
        if need_catcher and team_id and catchers:
            print(f"  team event scan for catchers (max={MAX_EVENTS_C})…", flush=True)
            try:
                team_events = bas.fetch_events_pages(token, team_id=team_id, max_events=MAX_EVENTS_C)
                print(f"  team events={len(team_events)}", flush=True)
            except Exception as exc:
                print(f"  team scan ERROR: {exc}", flush=True)

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
                try:
                    sid = synergy_id_for(token, name, mid)
                    row["synergy_id"] = sid
                    clips = []
                    if not sid:
                        print(f"  [C {i}/{len(catchers)}] {name} UNRESOLVED", flush=True)
                    else:
                        print(f"  [C {i}/{len(catchers)}] {name} sid={sid}", flush=True)
                        matched = []
                        for ev in team_events:
                            defense = ev.get("defense") or {}
                            lineup = defense.get("lineup") if isinstance(defense, dict) else {}
                            catch = (lineup or {}).get("catcher") if isinstance(lineup, dict) else None
                            cid = ""
                            if isinstance(catch, dict):
                                cid = str(catch.get("id") or "")
                            if cid and cid == str(sid):
                                matched.append(ev)
                            elif steal_of_second(ev):
                                matched.append(ev)
                        use = matched or team_events
                        clips = pick_catcher_clips(use)
                        print(f"    matched={len(matched)} clips={len(clips)}", flush=True)
                    row["clips"] = clips
                except Exception as exc:
                    print(f"  [C {i}/{len(catchers)}] {name} ERROR: {exc}", flush=True)
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
    try:
        main()
    except Exception as exc:
        import traceback
        print(f"FATAL: {exc}", flush=True)
        traceback.print_exc()
        sys.exit(1)
