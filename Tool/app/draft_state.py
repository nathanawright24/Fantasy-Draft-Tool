"""
Draft state persistence and roster-derivation helpers -- extracted out of app/main.py
(work order 2026-08-16 item 0b), a prerequisite for the item 0 fix and for keeping a
future real frontend cheap to build: this module has no UI-framework dependency at
all, so a grep for that framework's import across app/*.py returns only main.py.

One state file PER Sleeper draft_id (`draft_state_{draft_id}.json`), plus a separate
file for manual-only drafts (`draft_state_manual.json`) -- not a single global file.
That is the actual fix for the reported bug: the old single-file design meant
switching draft_id never cleared anything, so a live sync against a NEW draft_id
compared its pick numbers against the OLD draft's `known` overall-pick set and
silently discarded every one of them as "already seen." Keying by draft_id makes the
two draft's histories structurally incapable of cross-contaminating -- there is
nothing to "clear," because switching just means reading a different file.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import draft_engine as de  # noqa: E402


# ---------------------------------------------------------------------------
# Sleeper name resolution (work order 2026-08-24b item 1) -- the fix for Kenneth
# Walker staying on the board after being drafted. Sleeper's own pick payload spells a
# handful of names differently than player_master's name_key (build/pipeline.py already
# resolves this for the ADP file, then threw it away); this is the app-side lookup that
# uses what the build now keeps instead of re-deriving it.
# ---------------------------------------------------------------------------
_crosswalk_cache: dict[str, str] | None = None


def _sleeper_crosswalk() -> dict[str, str]:
    """sleeper_name_key -> master_name_key, loaded once per process from the build's
    output. Module-level cache keyed by nothing but process lifetime is safe here:
    unlike config.DRAFT_ORDER_2026 (mutated live by draft_setup.apply_setup), this file
    only changes on a full pipeline rebuild, which always restarts the app anyway."""
    global _crosswalk_cache
    if _crosswalk_cache is None:
        path = config.SLEEPER_NAME_CROSSWALK_PATH
        table: dict[str, str] = {}
        if path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    table[row["sleeper_name_key"]] = row["master_name_key"]
        _crosswalk_cache = table
    return _crosswalk_cache


def resolve_sleeper_name_key(raw_name: str) -> str:
    """The one function that turns a raw Sleeper pick name (e.g. "Kenneth Walker III")
    into the name_key player_master actually uses ("ken walker"). Primary path is the
    build-generated crosswalk (covers every name Sleeper's own ADP export carried at
    build time); config.NAME_ALIASES is the fallback for a name that crosswalk doesn't
    have a row for (most likely a very late addition to Sleeper's player pool)."""
    raw_key = config.normalize_name(raw_name)
    crosswalk = _sleeper_crosswalk()
    if raw_key in crosswalk:
        return crosswalk[raw_key]
    return config.NAME_ALIASES.get(raw_key, raw_key)


def log_unmatched_sync_name(draft_id: str | None, raw_name: str, position: str | None, nfl_team: str | None) -> None:
    """Work order 2026-08-24b item 1 task 2: 'an unmatched Sleeper pick should surface
    on screen and in a log, naming the raw string that failed.' This is the log half --
    append-only, never overwritten, so a run of unmatched names accumulates a visible
    trail instead of each one silently replacing the last."""
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    line = (
        f"{datetime.now().isoformat(timespec='seconds')}  draft={draft_id or 'manual'}  "
        f"raw_name={raw_name!r}  position={position}  nfl_team={nfl_team}\n"
    )
    with open(config.UNMATCHED_SYNC_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line)


def _active_draft_pointer_file() -> Path:
    return config.STATE_DIR / "active_draft.json"


def state_file_for(draft_id: str | None) -> Path:
    if draft_id:
        return config.STATE_DIR / f"draft_state_{draft_id}.json"
    return config.STATE_DIR / "draft_state_manual.json"


def load_active_draft_id() -> str | None:
    """Which draft_id (or None for manual-only) was active last time the app ran --
    read from a tiny pointer file so it survives a full process restart, not just a
    UI-level rerun within the same session."""
    path = _active_draft_pointer_file()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("draft_id")
    return None


def save_active_draft_id(draft_id: str | None) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _active_draft_pointer_file().write_text(json.dumps({"draft_id": draft_id}), encoding="utf-8")


def load_state(draft_id: str | None) -> dict:
    path = state_file_for(draft_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"picks": [], "sleeper_draft_id": draft_id or ""}


def save_state(state: dict, draft_id: str | None) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file_for(draft_id).write_text(json.dumps(state, indent=2), encoding="utf-8")


def reset_draft(draft_id: str | None) -> dict:
    """Explicit reset (work order item 0's third part -- the spec had undo, never
    reset). Clears picks but keeps the draft_id association; overwrites the file."""
    state = {"picks": [], "sleeper_draft_id": draft_id or ""}
    save_state(state, draft_id)
    return state


def next_overall_pick(state: dict) -> int:
    return len(state["picks"]) + 1


def manager_for_pick(overall: int) -> str:
    seq = config.full_draft_sequence()
    return seq[overall - 1][2] if 0 < overall <= len(seq) else "?"


def add_pick(state: dict, draft_id: str | None, player_row) -> None:
    overall = next_overall_pick(state)
    state["picks"].append(
        {
            "overall": overall,
            "round": config.round_of_pick(overall),
            "player": player_row["player"],
            "position": player_row["position"],
            "nfl_team": player_row["nfl_team"],
            "manager": manager_for_pick(overall),
        }
    )
    save_state(state, draft_id)


def undo_last_pick(state: dict, draft_id: str | None) -> None:
    if state["picks"]:
        state["picks"].pop()
        save_state(state, draft_id)


def drafted_name_keys(state: dict) -> set[str]:
    return {config.normalize_name(p["player"]) for p in state["picks"]}


def roster_counts_by_manager(state: dict) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for p in state["picks"]:
        counts.setdefault(p["manager"], {}).setdefault(p["position"], 0)
        counts[p["manager"]][p["position"]] += 1
    return counts


def owner_roster_state(state: dict) -> de.RosterState:
    owner_picks = [p for p in state["picks"] if p["manager"] == config.OWNER]
    current_overall = next_overall_pick(state)
    return de.RosterState(
        picks=owner_picks,
        current_round=config.round_of_pick(current_overall),
        current_overall_pick=current_overall,
    )


def owner_next_pick_number(state: dict) -> int:
    current = next_overall_pick(state)
    seq = config.full_draft_sequence()
    for overall, _, manager in seq:
        if overall >= current and manager == config.OWNER:
            return overall
    return current
