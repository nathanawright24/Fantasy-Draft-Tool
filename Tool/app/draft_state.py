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

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import draft_engine as de  # noqa: E402

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
