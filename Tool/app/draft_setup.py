"""
draft_setup.py -- persistence and application of the draft-day setup screen (work
order 2026-08-24 item 4 / R32; item 5 / R33's "toggles live where the app is
launched").

No UI-framework import: this module only reads/writes state/draft_setup.json and
mutates config's module-level state IN PLACE so every function across config.py and
draft_engine.py that reads config.DRAFT_ORDER_2026 / config.OWNER / config.ROSTER_TARGET
/ config.LAYERS / config.REFERENCE_ADP picks the change up immediately. No rebuild is
needed for any of this -- player_master.csv is slot-independent; the draft slot only
affects pick numbers and availability, both computed at render time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

SETUP_PATH_NAME = "draft_setup.json"
DEFAULT_TE1_FORK_PLAYER = "Sam LaPorta"


def setup_path() -> Path:
    return config.STATE_DIR / SETUP_PATH_NAME


def setup_exists() -> bool:
    return setup_path().exists()


def default_setup() -> dict:
    """Factory defaults -- the 2026 main league, exactly as hard-coded before this
    feature existed. Read from config's `*_FACTORY` snapshots (frozen at import time),
    not the live `config.DRAFT_ORDER_2026` etc., so this stays correct even after a
    setup has already been applied once in this process."""
    return {
        "draft_order": list(config.DRAFT_ORDER_2026_FACTORY),
        "owner": config.OWNER_FACTORY,
        "roster_target": dict(config.ROSTER_TARGET_FACTORY),
        "reference_source_name": config.REFERENCE_ADP_FACTORY["source_name"],
        "reference_is_sleeper": config.REFERENCE_ADP_FACTORY["is_sleeper"],
        "layers": {name: layer["applies"] for name, layer in config.LAYERS.items()},
        "te1_fork_player": DEFAULT_TE1_FORK_PLAYER,
        "sleeper_draft_id": None,
    }


def load_setup() -> dict:
    """Factory defaults, overlaid with whatever was actually saved -- so a setup
    written before a layer existed still merges cleanly with today's full layer set."""
    merged = default_setup()
    path = setup_path()
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        merged.update(saved)
        if "layers" in saved:
            merged["layers"] = {**merged["layers"], **saved["layers"]}
    return merged


def save_setup(setup: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    setup_path().write_text(json.dumps(setup, indent=2), encoding="utf-8")
    apply_setup(setup)


def apply_setup(setup: dict) -> None:
    """Mutates config's shared, mutable containers IN PLACE rather than reassigning the
    module attribute. This matters: several functions in config.py and
    draft_engine.py bind these as DEFAULT ARGUMENT VALUES at import time, which
    captures a live reference to the same list/dict object, not a frozen copy.
    Reassigning `config.DRAFT_ORDER_2026 = [...]` would silently orphan every one of
    those defaults; mutating the list's contents in place (`[:] = ...`) is what those
    defaults actually see. `config.OWNER` (a string) can't be mutated in place, which
    is why every function that takes an `owner` parameter in this codebase resolves it
    from a `None` sentinel at call time instead of a frozen `= config.OWNER` default --
    see `config.owner_pick_windows`'s docstring for the full explanation."""
    config.DRAFT_ORDER_2026[:] = setup["draft_order"]
    config.OWNER = setup["owner"]
    config.ROSTER_TARGET.clear()
    config.ROSTER_TARGET.update(setup["roster_target"])
    config.REFERENCE_ADP["source_name"] = setup["reference_source_name"]
    config.REFERENCE_ADP["is_sleeper"] = setup["reference_is_sleeper"]
    for name, applies in setup.get("layers", {}).items():
        if name in config.LAYERS:
            config.LAYERS[name]["applies"] = applies


def te1_fork_player(setup: dict) -> str:
    return setup.get("te1_fork_player") or DEFAULT_TE1_FORK_PLAYER


def sleeper_draft_id(setup: dict) -> str | None:
    """The single source of truth for which draft_id is active -- main_cockpit.py
    reads this directly rather than keeping a second, parallel pointer (the
    session_state + active_draft.json mechanism app/main.py uses). draft_setup.json is
    already reloaded at the top of every run, so there is nothing extra to persist:
    typing a new value here and saving is the whole switch. Blank/whitespace-only
    normalizes to None (manual-entry mode), matching draft_state.state_file_for's own
    None-means-manual convention."""
    raw = setup.get("sleeper_draft_id")
    return raw.strip() or None if isinstance(raw, str) else raw


def validate_draft_order(order: list[str], expected_names: list[str]) -> str | None:
    """None if `order` is a valid permutation of `expected_names`; otherwise a
    plain-language reason, for the setup form to show instead of crashing on a
    half-edited list."""
    if len(order) != len(expected_names):
        return f"Expected {len(expected_names)} names, got {len(order)}."
    if sorted(order) != sorted(expected_names):
        missing = sorted(set(expected_names) - set(order))
        extra = sorted(set(order) - set(expected_names))
        parts = []
        if missing:
            parts.append(f"missing {missing}")
        if extra:
            parts.append(f"unrecognized {extra}")
        return "Draft order must contain exactly these names, each once: " + "; ".join(parts)
    return None


def layers_off(setup: dict) -> list[str]:
    """Names of every layer this setup has switched off -- the "which layers are off"
    indicator spec 4.2 requirement 2 and work order item 5 both call for."""
    return sorted(name for name, applies in setup.get("layers", {}).items() if not applies)
