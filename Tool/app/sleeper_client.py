"""
Live pick polling against api.sleeper.app. Only active when
config.REFERENCE_ADP["is_sleeper"] is True.

Kept isolated on purpose: the spec's build rationale (Section 1) says api.sleeper.app is
blocked in the sandbox that produced the source data, but reachable from Nathan's own
machine. Verified directly during this build -- `requests.get("https://api.sleeper.app
/v1/state/nfl")` returned a real 2026-season payload from this environment, so basic
connectivity is confirmed, not just claimed. What is NOT yet verified: the actual
pick-polling flow (fetch_draft_picks / poll_new_picks / draft_slot -> manager mapping)
against a real draft_id, because no 2026 draft exists yet to poll. Test that end-to-end
before relying on it live -- spec Section 10 step 6 and the mandatory dry-run in step 7
both cover this. If it doesn't work, app/main.py's manual entry tab is a complete,
always-available substitute, not a degraded fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

BASE_URL = "https://api.sleeper.app/v1"


class SleeperUnavailable(Exception):
    """Raised for any network/API failure. app/main.py catches this and shows a
    'drafting elsewhere, use manual entry' banner -- never a crash (spec Section 9)."""


def check_available(timeout: float = 3.0) -> bool:
    """Cheap connectivity probe for the startup banner. Spec Section 9: 'say so at
    startup rather than failing at pick one.'"""
    if not config.REFERENCE_ADP.get("is_sleeper"):
        return False
    try:
        resp = requests.get(f"{BASE_URL}/state/nfl", timeout=timeout)
        resp.raise_for_status()
        return True
    except requests.RequestException:
        return False


def fetch_draft_picks(draft_id: str, timeout: float = 5.0) -> list[dict]:
    try:
        resp = requests.get(f"{BASE_URL}/draft/{draft_id}/picks", timeout=timeout)
        resp.raise_for_status()
        return resp.json() or []
    except requests.RequestException as exc:
        raise SleeperUnavailable(f"Could not reach Sleeper for draft {draft_id}: {exc}") from exc
    except ValueError as exc:  # malformed JSON
        raise SleeperUnavailable(f"Sleeper returned an unparseable response for draft {draft_id}: {exc}") from exc


def manager_by_slot(draft_order: list[str] = config.DRAFT_ORDER_2026) -> dict[int, str]:
    """Sleeper picks carry a 1-indexed `draft_slot`. Since DRAFT_ORDER_2026 IS the slot
    sequence (config.py's own docstring: 'slot 5 of 12'), slot -> manager needs no
    Sleeper user-id/username lookup at all -- one less thing that can silently drift out
    of sync with a Sleeper account rename (the historical data already hit this once,
    with Dylan's clappinyou -> itsssssDylan rename)."""
    return {i + 1: name for i, name in enumerate(draft_order)}


def normalize_pick(raw_pick: dict, slot_map: dict[int, str]) -> dict:
    """Sleeper's pick payload -> the shape app/main.py's roster state expects."""
    meta = raw_pick.get("metadata") or {}
    slot = raw_pick.get("draft_slot")
    return {
        "overall": raw_pick.get("pick_no"),
        "round": raw_pick.get("round"),
        "player": f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip(),
        "position": meta.get("position"),
        "nfl_team": meta.get("team"),
        "manager": slot_map.get(slot),
        "draft_slot": slot,
    }


def poll_new_picks(draft_id: str, known_overall_picks: set[int]) -> list[dict]:
    """Every pick not already in `known_overall_picks`, in pick order. app/main.py is
    expected to pass the set of overall-pick numbers already written to draft state, so
    a restart never double-applies a pick."""
    raw = fetch_draft_picks(draft_id)
    slot_map = manager_by_slot()
    picks = [normalize_pick(p, slot_map) for p in raw]
    picks = [p for p in picks if p["overall"] is not None and p["overall"] not in known_overall_picks]
    return sorted(picks, key=lambda p: p["overall"])
