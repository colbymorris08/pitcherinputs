#!/usr/bin/env python3
"""Build pitcherinputs data/catalog.json — short Synergy CLIP URLs (stream, no local files).

Pitchers (A–Z on site):
  - Timing: runner on 1B → 1 fastball + 1 offspeed (slots FB / OS)
  - Release pics: 1 LowHome/front + 1 Centerfield (slots LH / CF) — any pitch
Catchers: up to 5 SB/CS of 2B throw-down clips via Synergy ``catcherId``
(all leagues — MLB / MiLB / LBPRC / … — not Puerto Rico only).

IMPORTANT: Prefer signed progressive clip URLs:
  https://asset.synergysportstech.com/clip/{uuid}/{token}/clip.mp4
Never store full-game HLS masters (.../{asset_id}/hls/master.m3u8).

Env:
  PI_SKIP_TEAMS=SAN          (default)
  PI_CATCHERS_ONLY=1         reuse pitcher rows; rebuild catchers only
  PI_PITCHERS_ONLY=1         rebuild pitchers only; reuse catchers from catalog
  PI_RELEASE_FILL=1          only re-fetch pitchers missing LH and/or CF slots
  PI_CATCHER_CLIPS=5         clips per catcher
  PI_MAX_SCAN_CATCHER=5000   catcherId pages walked per catcher
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

MAX_EVENTS_P = int(_os.environ.get("PI_MAX_EVENTS_P", "800"))
MAX_EVENTS_C = int(_os.environ.get("PI_MAX_EVENTS_C", "1500"))
MAX_SCAN_C = int(_os.environ.get("PI_MAX_SCAN_C", "4000"))
# Per-catcher Synergy catcherId scan (any league: MLB/MiLB/LBPRC/…).
MAX_SCAN_CATCHER = int(_os.environ.get("PI_MAX_SCAN_CATCHER", "5000"))
CATCHER_CLIPS = int(_os.environ.get("PI_CATCHER_CLIPS", "5"))
CATCHER_CACHE_V = 3
# v2 = FB/OS timing + LowHome + CF release slots.
PITCHER_CACHE_V = 2
CATCHERS_ONLY = _os.environ.get("PI_CATCHERS_ONLY", "").strip().lower() in {"1", "true", "yes"}
PITCHERS_ONLY = _os.environ.get("PI_PITCHERS_ONLY", "").strip().lower() in {"1", "true", "yes"}
RELEASE_FILL = _os.environ.get("PI_RELEASE_FILL", "").strip().lower() in {"1", "true", "yes"}
SKIP_TEAMS = {
    a.strip().upper()
    for a in _os.environ.get("PI_SKIP_TEAMS", "SAN").split(",")
    if a.strip()
}

# Synergy runner.how / runKind for steal attempts of second.
STEAL_HOW = {"stolenbase", "caughtstealing", "pickoffcaughtstealing"}
STEAL_KIND = {"steal", "caughtstealing"}


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


def _runner_moves(ev: dict) -> list[dict]:
    """Flatten Synergy runners.* movement objects (skip occupancy snapshots)."""
    runners = ev.get("runners") or {}
    if not isinstance(runners, dict):
        return []
    out: list[dict] = []
    for key, val in runners.items():
        if str(key).startswith("runnerConfiguration"):
            continue
        if isinstance(val, dict):
            out.append(val)
        elif isinstance(val, list):
            out.extend(x for x in val if isinstance(x, dict))
    return out


def steal_attempt_of_second(ev: dict) -> dict | None:
    """Return the 1B→2B steal/CS movement dict if this event is a throw-down to 2B."""
    for move in _runner_moves(ev):
        how = str(move.get("how") or "").replace("_", "").replace(" ", "").lower()
        kind = str(move.get("runKind") or "").replace("_", "").replace(" ", "").lower()
        if how not in STEAL_HOW and kind not in STEAL_KIND:
            continue
        start = str(move.get("locationStart") or "").lower()
        end = str(move.get("locationEnd") or "").lower()
        # Throw down to second: started at first, ended at/toward second.
        if "first" in start and "second" in end:
            return move
    return None


def steal_of_second(ev: dict) -> bool:
    """True for SB/CS of 2B (throw-down context). Uses runner how/runKind — not URL tokens."""
    return steal_attempt_of_second(ev) is not None


def event_catcher(ev: dict) -> dict:
    defense = ev.get("defense") or {}
    lineup = defense.get("lineup") if isinstance(defense, dict) else {}
    catch = (lineup or {}).get("catcher") if isinstance(lineup, dict) else None
    return catch if isinstance(catch, dict) else {}


def catcher_display_name(catch: dict) -> str:
    first = str(catch.get("nameFirst") or "").strip()
    last = str(catch.get("nameLast") or "").strip()
    return f"{first} {last}".strip() or str(catch.get("name") or "").strip()


def names_match(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        s = unicodedata.normalize("NFKD", str(s or ""))
        s = "".join(ch for ch in s if not unicodedata.combining(ch))
        return "".join(ch for ch in s.lower() if ch.isalnum())

    na, nb = norm(a), norm(b)
    if not na or not nb:
        return False
    return na == nb or na.endswith(nb) or nb.endswith(na)


def is_lowhome_angle(angle: str) -> bool:
    a = (angle or "").strip().lower().replace(" ", "")
    return a.startswith("lowhome")


def is_highhome_angle(angle: str) -> bool:
    a = (angle or "").strip().lower().replace(" ", "")
    return a.startswith("highhome")


def is_cf_angle(angle: str) -> bool:
    """Centerfield / CF camera (release from behind pitcher)."""
    a = (angle or "").strip().lower().replace(" ", "").replace("_", "")
    if not a:
        return False
    if a.startswith("centerfield") or a.startswith("centrefield"):
        return True
    if a in {"cf", "center", "centre"}:
        return True
    if "centerfield" in a or "centrefield" in a:
        return True
    # Synergy sometimes labels as HighCF / LowCF / CFBroadcast
    if a.startswith("highcf") or a.startswith("lowcf") or a.startswith("cf"):
        return True
    return False


def angle_rank(angle: str) -> int:
    a = (angle or "").strip().lower()
    if a.startswith("lowhome"):
        return 0
    if a.startswith("highhome"):
        return 1
    if is_cf_angle(angle):
        return 2
    if a:
        return 3
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


def _clip_meta(ev: dict, url: str, asset_id: str, angle: str) -> dict:
    game = ev.get("game") or {}
    league = game.get("league") or {}
    pitch = norm_pitch(ev)
    return {
        "url": url,
        "asset_id": asset_id,
        "angle": angle,
        "pitch_type": pitch,
        "game_date": game.get("gameDate") or game.get("date") or "",
        "league": league.get("nameAbbrev") or league.get("name") or "",
        "event_id": str(ev.get("id") or ""),
        "result": str(ev.get("plateAppearanceResult") or (ev.get("pitch") or {}).get("pitchResult") or ""),
        "kind": "clip",
    }


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
    return _clip_meta(ev, best["url"], best["asset_id"], best["angle"])


def is_broadcast_angle(angle: str) -> bool:
    a = (angle or "").strip().lower().replace(" ", "")
    return a.startswith("broadcast") or a == "tv"


def angle_clips_from_event(ev: dict) -> dict[str, dict]:
    """Playable short clips from one event keyed by family: LH / HH / CF / BC."""
    out: dict[str, dict] = {}
    for track in ((ev.get("videoGroup") or {}).get("videoTracks")) or []:
        angle = str(track.get("videoCameraAngle") or track.get("cameraAngle") or "").strip()
        url, asset_id = track_playable(track)
        if not url or not is_short_clip_url(url):
            continue
        clip = _clip_meta(ev, url, asset_id, angle)
        if is_lowhome_angle(angle) and "LH" not in out:
            out["LH"] = clip
        elif is_highhome_angle(angle) and "HH" not in out:
            out["HH"] = clip
        elif is_cf_angle(angle) and "CF" not in out:
            out["CF"] = clip
        elif is_broadcast_angle(angle) and "BC" not in out:
            out["BC"] = clip
    return out


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


def pick_release_clips(events: list[dict]) -> tuple[dict | None, dict | None]:
    """One LowHome (or HighHome fallback) + one CF (or Broadcast fallback) for release pics."""
    lh = None
    cf = None
    hh_fallback = None
    bc_fallback = None
    for ev in events:
        by = angle_clips_from_event(ev)
        if lh is None and by.get("LH"):
            lh = {
                **by["LH"],
                "slot": "LH",
                "label": f"LowHome · front · {by['LH'].get('pitch_type') or 'pitch'}",
            }
        if hh_fallback is None and by.get("HH"):
            hh_fallback = by["HH"]
        if cf is None and by.get("CF"):
            cf = {
                **by["CF"],
                "slot": "CF",
                "label": f"CF · {by['CF'].get('pitch_type') or 'pitch'}",
            }
        if bc_fallback is None and by.get("BC"):
            bc_fallback = by["BC"]
        if lh and cf:
            break
    if lh is None and hh_fallback is not None:
        lh = {
            **hh_fallback,
            "slot": "LH",
            "label": f"HighHome · front fallback · {hh_fallback.get('pitch_type') or 'pitch'}",
        }
    if cf is None and bc_fallback is not None:
        cf = {
            **bc_fallback,
            "slot": "CF",
            "label": f"Broadcast · CF fallback · {bc_fallback.get('pitch_type') or 'pitch'}",
        }
    return lh, cf


def pick_timing_clips(events: list[dict]) -> tuple[dict | None, dict | None]:
    """Runner-on-1B fastball + offspeed for time-to-home timing."""
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
    return fb, os_


def pick_pitcher_clips(events: list[dict]) -> list[dict]:
    """FB + OS (timing) and LH + CF (release)."""
    fb, os_ = pick_timing_clips(events)
    lh, cf = pick_release_clips(events)
    out: list[dict] = []
    for clip in (fb, os_, lh, cf):
        if clip:
            out.append(clip)
    return out


def pitcher_slots(clips: list[dict] | None) -> set[str]:
    return {str(c.get("slot") or "") for c in (clips or []) if c and c.get("url")}


def pitcher_cache_complete(clips: list[dict] | None) -> bool:
    slots = pitcher_slots(clips)
    # Release views are the new requirement; keep timing when available.
    return "LH" in slots and "CF" in slots


def merge_pitcher_clips(prior: list[dict] | None, fresh: list[dict] | None) -> list[dict]:
    """Prefer fresh slots; keep prior FB/OS/LH/CF when fresh lacks them."""
    by_slot: dict[str, dict] = {}
    for src in (prior or [], fresh or []):
        for clip in src:
            if not clip or not clip.get("url"):
                continue
            slot = str(clip.get("slot") or "")
            if not slot:
                continue
            # Fresh pass overwrites; first list is prior so second wins when both present.
            by_slot[slot] = clip
    # Rebuild in stable order
    out: list[dict] = []
    for slot in ("FB", "OS", "LH", "CF"):
        if slot in by_slot:
            out.append(by_slot[slot])
    return out


def _events_have_release_angles(events: list[dict]) -> bool:
    """True if any event has LowHome / HighHome / CF short clips."""
    for ev in events:
        by = angle_clips_from_event(ev)
        if by.get("LH") or by.get("HH") or by.get("CF"):
            return True
    return False


def _fetch_pitcher_pages(
    token: str,
    sid: str,
    *,
    cap: int,
    league_ids: list[str] | None = None,
    label: str = "",
    early_stop_no_angles: bool = True,
) -> tuple[list[dict], int]:
    """Page pitcher events; return (clips, scanned)."""
    page = 200
    out: list[dict] = []
    skip = 0
    scanned = 0
    tag = f"{label} " if label else ""
    while scanned < cap:
        body: dict[str, Any] = {
            "loggingPhases": [bas.LOGGING_PHASE_PITCH],
            "skip": skip,
            "take": page,
            "pitcherId": sid,
        }
        if league_ids:
            body["leagueIds"] = league_ids
        try:
            doc = bas.api_post(bas.EVENTS_FILTER_URL, token, body, timeout_s=120)
        except Exception as exc:
            print(f"    {tag}page fail skip={skip}: {exc}", flush=True)
            break
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        scanned += len(rows)
        skip += len(rows)
        clips = pick_pitcher_clips(out)
        slots = pitcher_slots(clips)
        print(
            f"  {tag}fetched {len(out)} kept / {scanned} scanned · slots={sorted(slots)}",
            flush=True,
        )
        if "LH" in slots and "CF" in slots and "FB" in slots and "OS" in slots:
            return clips, scanned
        if "LH" in slots and "CF" in slots and scanned >= min(cap, 400):
            return clips, scanned
        # MiLB AsLogged-only: no home/CF after 400 → don't burn the full cap.
        if (
            early_stop_no_angles
            and scanned >= min(cap, 400)
            and "LH" not in slots
            and "CF" not in slots
            and not _events_have_release_angles(out)
        ):
            print(f"    {tag}no LowHome/CF angles in scan — stop early", flush=True)
            return clips, scanned
        total = int((doc or {}).get("totalRecords") or 0)
        if skip >= total or len(rows) < page:
            break
        time.sleep(0.08)
    return pick_pitcher_clips(out), scanned


def fetch_pitcher_until_clips(token: str, sid: str) -> tuple[list[dict], int]:
    """Prefer LBPRC (multi-angle), then fall back to all-league scan."""
    # Winter LBPRC almost always has LowHome + CF when the arm pitched there.
    lbprc_cap = min(400, MAX_EVENTS_P)
    clips, scanned = _fetch_pitcher_pages(
        token,
        sid,
        cap=lbprc_cap,
        league_ids=[bas.LEAGUE_LBPRC],
        label="LBPRC",
        early_stop_no_angles=False,
    )
    slots = pitcher_slots(clips)
    if "LH" in slots and "CF" in slots:
        return clips, scanned
    # All leagues (newest-first) — early-stop if still AsLogged-only.
    more, scanned2 = _fetch_pitcher_pages(
        token,
        sid,
        cap=MAX_EVENTS_P,
        league_ids=None,
        label="",
        early_stop_no_angles=True,
    )
    return merge_pitcher_clips(clips, more), scanned + scanned2


def steal_result_label(move: dict | None) -> str:
    if not move:
        return "SB/CS"
    how = str(move.get("how") or "")
    kind = str(move.get("runKind") or "")
    blob = f"{how} {kind}".lower()
    if "caught" in blob:
        return "CS"
    if "stolen" in blob or kind.lower() == "steal":
        return "SB"
    return how or kind or "SB/CS"


def pick_catcher_clips(
    events: list[dict], catcher_name: str = "", limit: int | None = None
) -> list[dict]:
    """Clips for throw-downs to 2B. Caller must already filter to this catcher's events."""
    limit = CATCHER_CLIPS if limit is None else limit
    out = []
    seen_urls: set[str] = set()
    for ev in events:
        move = steal_attempt_of_second(ev)
        if not move:
            continue
        clip = best_clip(ev)
        if not clip:
            continue
        url = str(clip.get("url") or "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        outcome = steal_result_label(move)
        who = catcher_name or catcher_display_name(event_catcher(ev)) or "Catcher"
        league = str(clip.get("league") or "").strip()
        league_bit = f" · {league}" if league else ""
        clip = {
            **clip,
            "slot": "THROW2B",
            "catcher_name": who,
            "steal_result": outcome,
            "label": f"{who} · Throw 2B · {outcome}{league_bit}",
            # Prefer steal outcome over PA result (PA is often unrelated Single/K).
            "result": outcome,
        }
        out.append(clip)
        if len(out) >= limit:
            break
    return out


CATCHER_PAGE = int(_os.environ.get("PI_CATCHER_PAGE", "100"))
CATCHER_WALL_S = float(_os.environ.get("PI_CATCHER_WALL_S", "240"))


def fetch_catcher_until_clips(
    token: str, sid: str, catcher_name: str
) -> tuple[list[dict], int, bool]:
    """Page Synergy catcherId events (all leagues) until CATCHER_CLIPS short /clip/ URLs exist.

    catcherId is not league-filtered — returns MLB / MiLB / LBPRC / etc. newest-first.
    Streams clips page-by-page (does not retain raw events). Soft wall-clock via
    PI_CATCHER_WALL_S. Returns (clips, scanned, complete).
    """
    page = CATCHER_PAGE
    cap = MAX_SCAN_CATCHER
    clips: list[dict] = []
    seen_urls: set[str] = set()
    skip = 0
    scanned = 0
    steals = 0
    t0 = time.time()
    while scanned < cap:
        if time.time() - t0 > CATCHER_WALL_S:
            print(
                f"    catcherId wall {CATCHER_WALL_S:.0f}s hit scanned={scanned} clips={len(clips)}",
                flush=True,
            )
            return clips[:CATCHER_CLIPS], scanned, True
        body = {
            "loggingPhases": [bas.LOGGING_PHASE_PITCH],
            "skip": skip,
            "take": page,
            "catcherId": sid,
        }
        try:
            doc = bas.api_post(bas.EVENTS_FILTER_URL, token, body, timeout_s=90)
        except Exception as exc:
            print(f"    catcherId page fail skip={skip}: {exc}", flush=True)
            return clips[:CATCHER_CLIPS], scanned, False
        rows = doc.get("result") if isinstance(doc, dict) else None
        if not isinstance(rows, list) or not rows:
            return clips[:CATCHER_CLIPS], scanned, True
        for ev in rows:
            move = steal_attempt_of_second(ev)
            if not move:
                continue
            steals += 1
            clip = best_clip(ev)
            if not clip:
                continue
            url = str(clip.get("url") or "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            outcome = steal_result_label(move)
            who = catcher_name or catcher_display_name(event_catcher(ev)) or "Catcher"
            league = str(clip.get("league") or "").strip()
            league_bit = f" · {league}" if league else ""
            clips.append({
                **clip,
                "slot": "THROW2B",
                "catcher_name": who,
                "steal_result": outcome,
                "label": f"{who} · Throw 2B · {outcome}{league_bit}",
                "result": outcome,
            })
            if len(clips) >= CATCHER_CLIPS:
                break
        scanned += len(rows)
        skip += len(rows)
        print(
            f"    catcherId scanned={scanned} steals={steals} clips={len(clips)}",
            flush=True,
        )
        if len(clips) >= CATCHER_CLIPS:
            return clips[:CATCHER_CLIPS], scanned, True
        total = int((doc or {}).get("totalRecords") or 0)
        if skip >= total or len(rows) < page:
            return clips[:CATCHER_CLIPS], scanned, True
        time.sleep(0.05)
    return clips[:CATCHER_CLIPS], scanned, True


def events_for_catcher(events: list[dict], *, synergy_id: str | None, name: str) -> list[dict]:
    """Only steal-of-2B events where this catcher was in the field."""
    matched: list[dict] = []
    sid = str(synergy_id or "")
    for ev in events:
        if not steal_of_second(ev):
            continue
        catch = event_catcher(ev)
        cid = str(catch.get("id") or "")
        cname = catcher_display_name(catch)
        if sid and cid and cid == sid:
            matched.append(ev)
        elif (not sid or not cid) and names_match(cname, name):
            matched.append(ev)
    return matched


def discover_field_catchers(events: list[dict]) -> list[dict]:
    """Unique catchers who appear on 1B→2B steal/CS throws in the team event scan."""
    by_id: dict[str, dict] = {}
    for ev in events:
        if not steal_of_second(ev):
            continue
        catch = event_catcher(ev)
        cid = str(catch.get("id") or "")
        name = catcher_display_name(catch)
        if not cid and not name:
            continue
        key = cid or name
        if key in by_id:
            continue
        by_id[key] = {
            "id": None,
            "mlb_id": None,
            "name": name,
            "last": last_name(name),
            "synergy_id": cid or None,
            "source": "field",
            "clips": [],
        }
    return list(by_id.values())


# Snapshot of catalog teams at process start — used so partial writes never drop clubs.
_CATALOG_BASELINE: list[dict] = []


def _merge_team_catchers(partial: dict, prior: dict | None) -> dict:
    """Keep not-yet-processed catchers from prior/baseline when writing a partial team."""
    if not prior:
        return partial
    out = dict(partial)
    have_names = {norm_key(c.get("name") or "") for c in (out.get("catchers") or [])}
    have_sids = {str(c.get("synergy_id") or "") for c in (out.get("catchers") or []) if c.get("synergy_id")}
    extras = []
    for c in prior.get("catchers") or []:
        sid = str(c.get("synergy_id") or "")
        if sid and sid in have_sids:
            continue
        if norm_key(c.get("name") or "") in have_names:
            continue
        extras.append(c)
    if extras:
        catchers = list(out.get("catchers") or []) + extras
        catchers.sort(key=lambda r: (str(r.get("last") or "").lower(), str(r.get("name") or "").lower()))
        out["catchers"] = catchers
    # Preserve pitchers if partial omitted them
    if not out.get("pitchers") and prior.get("pitchers"):
        out["pitchers"] = prior["pitchers"]
    return out


def norm_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return "".join(ch for ch in s.lower() if ch.isalnum())


def write_catalog(teams: list[dict], *, preserve_other_teams: bool = True) -> None:
    """Publish catalog. Merge in baseline/disk teams not present in ``teams``."""
    prior_by = {t.get("abbrev"): t for t in _CATALOG_BASELINE if t.get("abbrev")}
    if OUT.is_file():
        try:
            for t in (json.loads(OUT.read_text()).get("teams") or []):
                abbr = t.get("abbrev")
                if abbr and abbr not in prior_by:
                    prior_by[abbr] = t
        except Exception:
            pass
    merged = [_merge_team_catchers(t, prior_by.get(t.get("abbrev"))) for t in teams]
    if preserve_other_teams:
        have = {t.get("abbrev") for t in merged}
        for abbr, t in prior_by.items():
            if abbr and abbr not in have:
                merged.append(t)
                have.add(abbr)
        order = ["SAN", "CAG", "CAR", "MAY", "PON", "SJU"]
        merged.sort(
            key=lambda t: order.index(t.get("abbrev"))
            if t.get("abbrev") in order
            else 99
        )
    doc = {
        "updated": now(),
        "note": "Short Synergy clip URLs only (signed /clip/.../clip.mp4) — not full-game HLS. "
        "Pitchers: FB+OS (R1 timing) + LowHome+CF (release). "
        f"Catchers: up to {CATCHER_CLIPS} SB/CS of 2B via catcherId (any league).",
        "teams": merged,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2) + "\n")
    print(f"WROTE {OUT} teams={[t.get('abbrev') for t in merged]}", flush=True)


def existing_team_pack(abbr: str) -> dict | None:
    """Load one team row from the last catalog (catchers-only rebuilds)."""
    if not OUT.is_file():
        return None
    try:
        doc = json.loads(OUT.read_text())
    except Exception:
        return None
    for t in doc.get("teams") or []:
        if t.get("abbrev") == abbr:
            return t
    return None


def existing_team_pitchers(abbr: str) -> list[dict]:
    """Reuse pitcher rows from the last catalog when PI_CATCHERS_ONLY=1."""
    pack = existing_team_pack(abbr)
    return list((pack or {}).get("pitchers") or [])


def existing_team_catchers(abbr: str) -> list[dict]:
    """Reuse catcher rows from the last catalog when PI_PITCHERS_ONLY=1."""
    pack = existing_team_pack(abbr)
    return list((pack or {}).get("catchers") or [])


def existing_team_catcher_seeds(abbr: str) -> list[dict]:
    """Prior catalog catchers (roster + field) so catchers-only can skip team scan."""
    pack = existing_team_pack(abbr)
    seeds: list[dict] = []
    for c in (pack or {}).get("catchers") or []:
        seeds.append({
            "id": c.get("id"),
            "mlb_id": c.get("mlb_id"),
            "name": c.get("name") or "",
            "last": c.get("last") or last_name(c.get("name") or ""),
            "synergy_id": c.get("synergy_id"),
            "source": c.get("source") or "catalog",
            "clips": [],
        })
    return seeds


def main() -> None:
    global _CATALOG_BASELINE
    roster = json.loads(ROSTER.read_text())
    if OUT.is_file():
        try:
            _CATALOG_BASELINE = list((json.loads(OUT.read_text()).get("teams")) or [])
        except Exception:
            _CATALOG_BASELINE = []
    else:
        _CATALOG_BASELINE = []
    token = bas.login_token(headed=False)
    print("Synergy login ok", flush=True)
    print(
        f"catcher target={CATCHER_CLIPS} clips · catcherId scan≤{MAX_SCAN_CATCHER} "
        f"· catchers_only={CATCHERS_ONLY} · pitchers_only={PITCHERS_ONLY} "
        f"· release_fill={RELEASE_FILL} · pitcher_cache_v={PITCHER_CACHE_V} "
        f"· catcher_cache_v={CATCHER_CACHE_V} "
        f"· baseline_teams={[t.get('abbrev') for t in _CATALOG_BASELINE]}",
        flush=True,
    )
    ckpt = load_ckpt()
    sid_clip_cache: dict[str, list[dict]] = {}
    for _k, ent in (ckpt.get("catcher_by_sid") or {}).items():
        if not isinstance(ent, dict) or ent.get("v") != CATCHER_CACHE_V:
            continue
        clips0 = ent.get("clips")
        if not isinstance(clips0, list):
            continue
        if len(clips0) >= CATCHER_CLIPS or ent.get("complete"):
            sid_clip_cache[str(_k)] = clips0
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

        if CATCHERS_ONLY:
            reused = existing_team_pitchers(abbr)
            if reused:
                team_row["pitchers"] = reused
                print(f"  reusing {len(reused)} pitchers from catalog (catchers-only)", flush=True)
            else:
                print("  WARN: catchers-only but no prior pitchers in catalog", flush=True)
        else:
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
                prior_clips = list((cached or {}).get("clips") or [])
                # Also keep catalog clips for this pitcher when merging release slots.
                for prev_p in existing_team_pitchers(abbr):
                    if (
                        (mid and str(prev_p.get("mlb_id") or prev_p.get("id")) == str(mid))
                        or names_match(prev_p.get("name") or "", name)
                    ):
                        prior_clips = merge_pitcher_clips(prior_clips, prev_p.get("clips") or [])
                        if not row.get("synergy_id") and prev_p.get("synergy_id"):
                            row["synergy_id"] = prev_p.get("synergy_id")
                        break
                cache_ok = (
                    cached
                    and cached.get("done")
                    and cached.get("clips") is not None
                    and cached.get("v") == PITCHER_CACHE_V
                    and pitcher_cache_complete(cached.get("clips"))
                )
                # RELEASE_FILL: skip pitchers that already have LH+CF (even old cache v).
                if RELEASE_FILL and pitcher_cache_complete(prior_clips):
                    row["synergy_id"] = (cached or {}).get("synergy_id") or row.get("synergy_id")
                    row["clips"] = prior_clips
                    print(
                        f"  [P {i}/{len(pitchers)}] {name} release ok "
                        f"slots={sorted(pitcher_slots(prior_clips))}",
                        flush=True,
                    )
                elif RELEASE_FILL and cached and cached.get("release_exhausted") and not pitcher_cache_complete(prior_clips):
                    # Already scanned with no LowHome/CF available in Synergy.
                    row["synergy_id"] = cached.get("synergy_id") or row.get("synergy_id")
                    row["clips"] = prior_clips or list(cached.get("clips") or [])
                    print(
                        f"  [P {i}/{len(pitchers)}] {name} release exhausted "
                        f"slots={sorted(pitcher_slots(row['clips']))}",
                        flush=True,
                    )
                elif cache_ok:
                    row.update({k: cached[k] for k in ("synergy_id", "clips") if k in cached})
                    print(
                        f"  [P {i}/{len(pitchers)}] {name} cached "
                        f"slots={sorted(pitcher_slots(row['clips']))}",
                        flush=True,
                    )
                else:
                    try:
                        sid = row.get("synergy_id") or synergy_id_for(token, name, mid)
                        row["synergy_id"] = sid
                        if not sid:
                            print(f"  [P {i}/{len(pitchers)}] {name} UNRESOLVED", flush=True)
                            row["clips"] = prior_clips
                        else:
                            print(f"  [P {i}/{len(pitchers)}] {name} sid={sid}", flush=True)
                            clips, scanned = fetch_pitcher_until_clips(token, sid)
                            row["clips"] = merge_pitcher_clips(prior_clips, clips)
                            print(
                                f"    scanned={scanned} clips={len(row['clips'])} "
                                f"slots={sorted(pitcher_slots(row['clips']))}",
                                flush=True,
                            )
                    except Exception as exc:
                        print(f"  [P {i}/{len(pitchers)}] {name} ERROR: {exc}", flush=True)
                        row["clips"] = prior_clips
                    complete = pitcher_cache_complete(row["clips"])
                    ckpt.setdefault("pitchers", {})[key] = {
                        "synergy_id": row["synergy_id"],
                        "clips": row["clips"],
                        "done": True,
                        "v": PITCHER_CACHE_V,
                        "complete": complete,
                        # Mark exhausted when we scanned but Synergy has no LH+CF.
                        "release_exhausted": (not complete) and bool(row.get("synergy_id")),
                    }
                    save_ckpt(ckpt)
                    # publish partial team so site updates during long runs
                    preview = teams_out + [{
                        **team_row,
                        "pitchers": team_row["pitchers"] + [row],
                        "catchers": team_row["catchers"] or existing_team_catchers(abbr),
                    }]
                    write_catalog(preview)
                    time.sleep(0.05)
                team_row["pitchers"].append(row)

        if PITCHERS_ONLY:
            reused_c = existing_team_catchers(abbr)
            team_row["catchers"] = reused_c
            teams_out.append(team_row)
            write_catalog(teams_out)
            print(
                f"=== {abbr} DONE pitchers={len(team_row['pitchers'])} "
                f"(reused {len(reused_c)} catchers) ===",
                flush=True,
            )
            continue

        # Team steal scan discovers field catchers + tops up clips. Skip on
        # catchers-only rebuilds (reuse prior catalog catcher list; catcherId is enough).
        team_events: list[dict] = []
        if team_id and not CATCHERS_ONLY:
            scan_cap = max(MAX_EVENTS_C * 4, MAX_SCAN_C)
            print(
                f"  team steal-of-2B scan (keep≤{MAX_EVENTS_C}, scan≤{scan_cap})…",
                flush=True,
            )
            try:
                scan_out: dict = {}
                team_events = bas.fetch_events_pages(
                    token,
                    team_id=team_id,
                    max_events=MAX_EVENTS_C,
                    max_scan=scan_cap,
                    retain=steal_of_second,
                    scan_out=scan_out,
                )
                print(
                    f"  steal-of-2B kept={len(team_events)} scanned={scan_out.get('scanned')}",
                    flush=True,
                )
            except Exception as exc:
                print(f"  team scan ERROR: {exc}", flush=True)
        elif CATCHERS_ONLY:
            print("  skipping team steal scan (catchers-only · catcherId all leagues)", flush=True)

        # Merge roster catchers with field catchers (team scan) or prior catalog seeds.
        catcher_rows: list[dict] = []
        seen_keys: set[str] = set()
        for p in catchers:
            mid = int(p.get("id") or 0) or None
            name = p.get("name") or ""
            key = f"{abbr}|{mid or name}"
            seen_keys.add(key)
            catcher_rows.append({
                "id": mid or key,
                "mlb_id": mid,
                "name": name,
                "last": last_name(name),
                "synergy_id": None,
                "source": "roster",
                "clips": [],
                "_key": key,
            })
        extras = (
            existing_team_catcher_seeds(abbr)
            if CATCHERS_ONLY
            else discover_field_catchers(team_events)
        )
        for field in extras:
            sid = field.get("synergy_id")
            name = field.get("name") or ""
            # Skip if already on roster (match by synergy id or name).
            dup = False
            for row in catcher_rows:
                if sid and row.get("synergy_id") and str(row["synergy_id"]) == str(sid):
                    dup = True
                    break
                if names_match(row.get("name") or "", name):
                    # Prefer attaching field synergy id onto roster row.
                    if sid and not row.get("synergy_id"):
                        row["synergy_id"] = sid
                    dup = True
                    break
            if dup:
                continue
            key = f"{abbr}|field|{sid or name}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            catcher_rows.append({
                **field,
                "id": field.get("id") or key,
                "_key": key,
            })

        catcher_rows.sort(key=lambda r: (str(r.get("last") or "").lower(), str(r.get("name") or "").lower()))

        for i, row in enumerate(catcher_rows, 1):
            key = row.pop("_key")
            name = row.get("name") or ""
            mid = row.get("mlb_id")
            cached = ckpt.get("catchers", {}).get(key)
            # v3 = catcherId multi-league fetch, 5-clip target.
            n_cached = len((cached or {}).get("clips") or [])
            cache_ok = (
                cached
                and cached.get("done")
                and cached.get("clips") is not None
                and cached.get("v") == CATCHER_CACHE_V
                and (n_cached >= CATCHER_CLIPS or cached.get("complete"))
            )
            if cache_ok:
                row.update({k: cached[k] for k in ("synergy_id", "clips", "source") if k in cached})
                print(f"  [C {i}/{len(catcher_rows)}] {name} cached clips={len(row['clips'])}", flush=True)
            else:
                complete = False
                try:
                    sid = row.get("synergy_id") or synergy_id_for(token, name, int(mid) if mid else None)
                    row["synergy_id"] = sid
                    clips: list[dict] = []
                    if sid and sid in sid_clip_cache and (
                        len(sid_clip_cache[sid]) >= CATCHER_CLIPS
                        or (ckpt.get("catcher_by_sid") or {}).get(sid, {}).get("complete")
                    ):
                        # Relabel for this team entry; same underlying /clip/ URLs.
                        clips = []
                        for raw in sid_clip_cache[sid]:
                            who = name or raw.get("catcher_name") or "Catcher"
                            league = str(raw.get("league") or "").strip()
                            outcome = raw.get("steal_result") or raw.get("result") or "SB/CS"
                            league_bit = f" · {league}" if league else ""
                            clips.append({
                                **raw,
                                "catcher_name": who,
                                "label": f"{who} · Throw 2B · {outcome}{league_bit}",
                            })
                            if len(clips) >= CATCHER_CLIPS:
                                break
                        complete = True
                        print(
                            f"  [C {i}/{len(catcher_rows)}] {name} sid={sid} "
                            f"reused_sid_clips={len(clips)}",
                            flush=True,
                        )
                    elif sid:
                        print(f"  [C {i}/{len(catcher_rows)}] {name} sid={sid} (catcherId all leagues)", flush=True)
                        clips, scanned, complete = fetch_catcher_until_clips(token, sid, name)
                        print(
                            f"    catcherId done scanned={scanned} clips={len(clips)} complete={complete}",
                            flush=True,
                        )
                        sid_clip_cache[sid] = clips
                        ckpt.setdefault("catcher_by_sid", {})[sid] = {
                            "clips": clips,
                            "name": name,
                            "v": CATCHER_CACHE_V,
                            "complete": complete,
                        }
                    else:
                        print(f"  [C {i}/{len(catcher_rows)}] {name} UNRESOLVED — team-scan fallback", flush=True)
                        matched = events_for_catcher(team_events, synergy_id=None, name=name)
                        clips = pick_catcher_clips(matched, catcher_name=name)
                        complete = True
                        print(f"    matched_steals={len(matched)} clips={len(clips)}", flush=True)
                    # If catcherId yielded fewer than target, top up from LBPRC team scan.
                    if sid and len(clips) < CATCHER_CLIPS:
                        matched = events_for_catcher(team_events, synergy_id=sid, name=name)
                        have = {c.get("url") for c in clips}
                        extra = pick_catcher_clips(matched, catcher_name=name, limit=CATCHER_CLIPS)
                        for ex in extra:
                            if ex.get("url") in have:
                                continue
                            clips.append(ex)
                            have.add(ex.get("url"))
                            if len(clips) >= CATCHER_CLIPS:
                                break
                        if matched:
                            print(f"    topped up from team scan → {len(clips)} clips", flush=True)
                    # Keep prior catalog clips if fetch returned fewer.
                    prior = []
                    for prev_t in _CATALOG_BASELINE:
                        if prev_t.get("abbrev") != abbr:
                            continue
                        for pc in prev_t.get("catchers") or []:
                            if names_match(pc.get("name") or "", name) or (
                                sid and str(pc.get("synergy_id") or "") == str(sid)
                            ):
                                prior = list(pc.get("clips") or [])
                                break
                    if len(clips) < len(prior):
                        have = {c.get("url") for c in clips}
                        for ex in prior:
                            if ex.get("url") in have:
                                continue
                            clips.append(ex)
                            have.add(ex.get("url"))
                            if len(clips) >= CATCHER_CLIPS:
                                break
                        print(f"    kept prior catalog clips → {len(clips)}", flush=True)
                    row["clips"] = clips[:CATCHER_CLIPS]
                except Exception as exc:
                    print(f"  [C {i}/{len(catcher_rows)}] {name} ERROR: {exc}", flush=True)
                ckpt.setdefault("catchers", {})[key] = {
                    "synergy_id": row.get("synergy_id"),
                    "clips": row.get("clips") or [],
                    "source": row.get("source") or "roster",
                    "done": True,
                    "complete": complete or len(row.get("clips") or []) >= CATCHER_CLIPS,
                    "v": CATCHER_CACHE_V,
                }
                save_ckpt(ckpt)
                # Publish progress but keep not-yet-processed catchers from this team
                # so a mid-team crash does not truncate the roster.
                pending = []
                for later in catcher_rows[i:]:
                    pending.append({k: v for k, v in later.items() if k != "_key"})
                write_catalog(teams_out + [{
                    **team_row,
                    "catchers": team_row["catchers"] + [row] + pending,
                }])
                time.sleep(0.05)
            team_row["catchers"].append(row)

        teams_out.append(team_row)
        write_catalog(teams_out)  # incremental publish after each team
        print(f"=== {abbr} DONE catchers={len(team_row['catchers'])} ===", flush=True)

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
