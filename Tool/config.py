"""
League config, feature toggles, path resolution, and small shared pure-python helpers
(name normalization, team-code mapping, snake-draft math) used by both build/pipeline.py
and everything under app/.

Nothing in this file touches the filesystem beyond resolving paths -- it's safe to import
from anywhere without side effects.
"""
from __future__ import annotations

import re
from pathlib import Path

# ===========================================================================
# FEATURE TOGGLES -- THE ONLY DEFINITION (work order 2026-08-24 item 5 / R33).
#
# build/pipeline.py and every app/ module read THIS dict, live, at call time --
# never a second copy. The setup screen (app/draft_setup.py) writes per-session
# overrides into state/draft_setup.json and applies them by mutating these
# `applies` values in place (draft_setup.apply_setup()); it does not maintain its
# own toggle list. If you're looking for where to add a tenth layer, or wondering
# why main_cockpit.py doesn't have its own LAYERS dict: this is deliberate --
# two copies drift, and manager_priors/nfl_team_bias/college_bias failing
# together in another league (no drafts/all_draft_picks_*.csv there) is exactly
# the kind of thing a second copy would forget to keep in sync.
# ===========================================================================
LAYERS = {
    "implied_props":    {"available": True, "applies": True},
    "factor_grids":     {"available": True, "applies": True},
    "bonus_model":      {"available": True, "applies": True},
    "oline_rankings":   {"available": True, "applies": True},
    "manager_priors":   {"available": True, "applies": True},
    "nfl_team_bias":    {"available": True, "applies": True},
    "college_bias":     {"available": True, "applies": True},
    "archetype_priors": {"available": True, "applies": True},
    "player_intel":     {"available": True, "applies": True},
}


def layer_on(name: str) -> bool:
    layer = LAYERS.get(name)
    return bool(layer and layer["available"] and layer["applies"])


# ---------------------------------------------------------------------------
# Paths -- resolve once, derive everything else. Survives being moved or run
# from a different working directory (Windows note in the spec).
# ---------------------------------------------------------------------------
TOOL_ROOT = Path(__file__).resolve().parent
FANTASY_ROOT = TOOL_ROOT.parent

DATA_2026 = FANTASY_ROOT / "2026"
DATA_DRAFTS = FANTASY_ROOT / "drafts"
DATA_MARKDOWNS = FANTASY_ROOT / "markdowns"

DATA_EXTERNAL = TOOL_ROOT / "data" / "external"
DATA_DERIVED = TOOL_ROOT / "data" / "derived"
STATE_DIR = TOOL_ROOT / "state"

JOIN_REPORT_PATH = DATA_DERIVED / "join_report.txt"
PLAYER_MASTER_PATH = DATA_DERIVED / "player_master.csv"
MANAGER_PRIORS_PATH = DATA_DERIVED / "manager_priors.csv"
TEAM_BIAS_PATH = DATA_DERIVED / "team_bias.csv"
ADP_SOURCE_OFFSETS_PATH = DATA_DERIVED / "adp_source_offsets.csv"
PLAYER_INTEL_PATH = DATA_DERIVED / "player_intel.csv"
REFERENCE_ADP_PATH = DATA_EXTERNAL / "reference_adp.csv"
COMPARISON_ADP_PATH = DATA_EXTERNAL / "comparison_adp.csv"
COLLEGE_BIAS_PATH = DATA_EXTERNAL / "college_bias_recall.csv"

ALL_DRAFT_PICKS_PATH = DATA_DRAFTS / "all_draft_picks_2022-2025.csv"

# Raw ADP exports live inside Tool/ (not FANTASY_ROOT) specifically so they can be
# committed to the Tool/ git repo (R23) -- a repo can't track files outside its own
# tree. Re-scrape into this folder on draft morning; the old location (loose at
# FANTASY_ROOT) is retired.
DATA_RAW = TOOL_ROOT / "data" / "raw"
NFFC_ADP_RAW_PATH = DATA_RAW / "ADP.tsv"
SLEEPER_ADP_RAW_GLOB = "sleeper_adp_ppr_*.csv"

# Owner-authored intel, refreshed each season; template for future years documented in
# its own header (work order 2026-08-16 item 3).
PLAYER_INTEL_MD_PATH = DATA_2026 / "PLAYER-INTEL-2026.md"

POSITIONS = ["QB", "RB", "WR", "TE"]


def props_path(position: str) -> Path:
    return DATA_2026 / position / f"{position.lower()}_implied_props.csv"


def priors_path(position: str) -> Path:
    return DATA_2026 / position / f"{position.lower()}_player_priors.csv"


def archetype_hit_rates_path(position: str) -> Path | None:
    # Only RB and WR have a back-tested archetype table. TE/QB genuinely have none --
    # do not invent a path that will silently 404 into an empty frame.
    if position not in ("RB", "WR"):
        return None
    return DATA_2026 / position / f"{position.lower()}_archetype_hit_rates.csv"


def oline_blend_path() -> Path:
    return DATA_2026 / "oline_blend_rankings.csv"


def latest_sleeper_adp_raw_path() -> Path:
    candidates = sorted(DATA_RAW.glob(SLEEPER_ADP_RAW_GLOB))
    if not candidates:
        raise FileNotFoundError(f"No file matching {SLEEPER_ADP_RAW_GLOB} in {DATA_RAW}")
    # Filenames embed a date (sleeper_adp_ppr_2026-08-16.csv) -- lexical sort == date sort.
    return candidates[-1]


# ---------------------------------------------------------------------------
# Draft order and slot-5 pick structure (spec Section 3)
# ---------------------------------------------------------------------------
DRAFT_ORDER_2026 = [
    "Dylan", "Cailen", "Tyler", "Nick", "Nathan", "Asa",
    "Greg", "Ryan", "Jayden", "Colin", "Joseph", "Kaiden",
]
OWNER = "Nathan"
N_TEAMS = len(DRAFT_ORDER_2026)
N_ROUNDS = 16

# Immutable snapshots for the setup screen's "reset to factory" (app/draft_setup.py) --
# DRAFT_ORDER_2026/OWNER/ROSTER_TARGET below are meant to be mutated in place at
# runtime once a draft_setup.json exists, so the setup screen needs a copy that never
# changes to offer as the starting point / reset target.
DRAFT_ORDER_2026_FACTORY = tuple(DRAFT_ORDER_2026)
OWNER_FACTORY = OWNER


def snake_order(round_num: int, draft_order: list[str] = DRAFT_ORDER_2026) -> list[str]:
    """Pick order within a single round. Odd rounds forward, even rounds reversed."""
    return list(draft_order) if round_num % 2 == 1 else list(reversed(draft_order))


def full_draft_sequence(
    draft_order: list[str] = DRAFT_ORDER_2026, rounds: int = N_ROUNDS
) -> list[tuple[int, int, str]]:
    """Every (overall_pick, round, manager) in the whole draft, 1-indexed overall pick."""
    seq = []
    overall = 1
    for r in range(1, rounds + 1):
        for manager in snake_order(r, draft_order):
            seq.append((overall, r, manager))
            overall += 1
    return seq


def owner_pick_windows(
    owner: str | None = None, draft_order: list[str] = DRAFT_ORDER_2026, rounds: int = N_ROUNDS
) -> list[dict]:
    """
    For every pick the owner makes, the round, overall pick number, and the ordered
    list of managers who pick between this pick and the owner's next one (None on the
    final pick, since there is no "next").

    This is the only place the fixed-window property (spec Section 3) is computed --
    derived from DRAFT_ORDER_2026, never hand-transcribed. tests/test_core.py asserts
    the invariant against this function's output.

    `owner` defaults via a None sentinel, re-read from `OWNER` at CALL time rather than
    bound into the signature at IMPORT time -- a plain `owner: str = OWNER` default
    would freeze whatever OWNER equalled when this module first loaded, so the setup
    screen's "change the owner slot, no rebuild" (R32) would silently stop working for
    every caller that relies on this default instead of passing owner explicitly.
    """
    owner = owner if owner is not None else OWNER
    seq = full_draft_sequence(draft_order, rounds)
    owner_positions = [i for i, (_, __, m) in enumerate(seq) if m == owner]
    windows = []
    for k, i in enumerate(owner_positions):
        overall, rnd, _ = seq[i]
        if k + 1 < len(owner_positions):
            next_i = owner_positions[k + 1]
            intervening = [seq[j][2] for j in range(i + 1, next_i)]
        else:
            intervening = None
        windows.append({"round": rnd, "overall": overall, "intervening": intervening})
    return windows


def managers_in_range(
    start_exclusive: int,
    end_exclusive: int,
    draft_order: list[str] = DRAFT_ORDER_2026,
    rounds: int = N_ROUNDS,
) -> list[str]:
    """Managers picking at overall positions strictly between the two bounds, in pick
    order. Used by the availability model with start_exclusive = the last pick actually
    made in the live draft and end_exclusive = the pick we want survival probability AT
    (usually the owner's upcoming pick) -- two distinct concepts that must not collapse
    into a single "current pick" parameter, or "am I on the clock right now" and "will
    this survive to my next turn" silently become the same question."""
    seq = full_draft_sequence(draft_order, rounds)
    return [m for overall, _, m in seq if start_exclusive < overall < end_exclusive]


def managers_until_next_owner_pick(
    current_overall_pick: int,
    owner: str | None = None,
    draft_order: list[str] = DRAFT_ORDER_2026,
    rounds: int = N_ROUNDS,
) -> list[str]:
    """Live-draft version of the above: managers picking between right now and the
    owner's next turn, driven by the actual current pick rather than the precomputed
    table. Used by the availability model during a live draft.

    See `owner_pick_windows`'s docstring for why `owner` defaults via a None sentinel
    rather than `= OWNER` directly."""
    owner = owner if owner is not None else OWNER
    seq = full_draft_sequence(draft_order, rounds)
    managers = []
    for overall, _, manager in seq:
        if overall <= current_overall_pick:
            continue
        if manager == owner:
            break
        managers.append(manager)
    return managers


def round_of_pick(overall_pick: int, n_teams: int = N_TEAMS) -> int:
    return (overall_pick - 1) // n_teams + 1


def round_start_pick(round_num: int, n_teams: int = N_TEAMS) -> int:
    """Inverse of round_of_pick: the first overall pick IN that round."""
    return (round_num - 1) * n_teams + 1


# Work order 2026-08-24 item 7 (R35): nobody in this league drafts a kicker before
# round 14 (GUARDRAILS W14). Shared by build/pipeline.py's kicker-row addition,
# app/board_model.py's kicker slide, and app/draft_engine.py's single kicker
# suggestion, so the three can't drift out of sync with each other.
KICKER_ROUND = 14



# ---------------------------------------------------------------------------
# Reference / comparison ADP source selection (spec Section 4.3)
# ---------------------------------------------------------------------------
REFERENCE_ADP = {
    "source_name": "Sleeper",
    "is_sleeper": True,
    "csv_path": None,  # used when is_sleeper is False
}
REFERENCE_ADP_FACTORY = dict(REFERENCE_ADP)  # setup screen's reset target
COMPARISON_ADP = {
    "source_name": "NFFC",
    "csv_path": str(NFFC_ADP_RAW_PATH),
}

ADP_MIN_N = 15  # NFFC rows below this sample count are shown but excluded from survival math

# ---------------------------------------------------------------------------
# Availability model (spec Section 7, rewritten per 12.2/12.3 -- R19-R21).
# ---------------------------------------------------------------------------
GENERIC_LOGNORMAL_SIGMA = 0.35  # fallback dispersion when adp_value exists but min/max/n don't
GENERIC_ADP_SPREAD_PICKS = 24  # fallback normal-curve spread when there's no ADP mean pick, just a rank
AVAILABILITY_N_SIMS = 2000  # spec 12.3: "cost is negligible: 8 picks x ~2,000 sims"

# Work order 2026-08-16 item 1: the owner drafts on Sleeper, and Sleeper vs NFFC diverge
# systematically (measured: Sleeper ranks TE ~23 and QB ~13 picks earlier than NFFC's
# mean pick, WR ~9 later -- see build/pipeline.py's compute_adp_source_offsets). The
# survival curve's CENTER must reflect the population the owner actually drafts against;
# its DISPERSION should still come from wherever real observed-range data exists.
# "reference" = Sleeper, "comparison" = NFFC -- matching REFERENCE_ADP/COMPARISON_ADP's
# own naming above. In practice only NFFC's ingestion populates min/max/n at all
# (Sleeper's ADP is a bare sequential rank), so setting SURVIVAL_DISPERSION_SOURCE to
# "reference" degrades to GENERIC_LOGNORMAL_SIGMA rather than erroring -- documented
# behavior, not a bug, if this ever gets flipped.
SURVIVAL_ANCHOR = "reference"
SURVIVAL_DISPERSION_SOURCE = "comparison"

# Work order 2026-08-24 item 1 (R36): which survival estimate compute_availability
# returns as `survival_probability`. backtest.py's decision-band Brier bake-off, run
# 2026-08-24 (see data/derived/backtest_report.txt for the full numbers): decision-band
# (predicted in 0.15-0.85) Brier was montecarlo=0.1467 (n=568) vs lognormal=0.1798
# (n=1275) -- an 18% relative gap, under the owner's 25% threshold for picking one
# outright, so the LETTER of the rule fires the blend branch.
#
# Overridden to "montecarlo" by the owner after seeing that blend (a) scores WORSE than
# plain montecarlo on the very metric that was supposed to justify it (blend
# decision_band_brier=0.1722 vs montecarlo=0.1467), and (b) drags in the capacity-
# invariant regression R20/R21 exists to prevent: the lognormal is marginal/uncapacitated
# by design (the original "159 departures over 8 picks" defect), so blending it back in
# reintroduces roughly half of that defect. Measured on a real pick 44->53 window
# (k=8 real intervening picks): montecarlo expects ~9 departures, blend ~34, lognormal
# alone ~59. Do not flip this without re-running the bake-off AND re-checking the
# capacity invariant; it is a measured choice, not a preference. "montecarlo" |
# "lognormal" | "blend" are the only valid values.
AVAILABILITY_METHOD = "montecarlo"

# Work order 2026-08-24 item 3 (R31): reach model. Managers don't draft strictly off
# survival hazard -- they will jump up to ~15 picks of ADP for "their guy," more so
# when they still need a thinning position. Modeled as a per-(manager, pick, position)
# shift applied to the overall pick number BEFORE evaluating that position's hazard
# curve in simulate_intervening_picks: a manager with mean reach +4 evaluates a
# candidate's hazard as though the pick were 4 slots later in that player's own timeline
# (more "due"), and a patient manager (negative mean) evaluates it as though earlier
# (less due). Drawn fresh per simulated pick, not fixed, per the owner's "a distribution,
# not a point" instruction.
#
# MANAGER_MEAN_REACH is transcribed from league-draft-tendencies-2026.md Section 4 (the
# "Reach vs. value, measured against an independent market" table) -- unlike
# team_bias.csv/adp_source_offsets.csv, this can't be recomputed from
# drafts/all_draft_picks_2022-2025.csv in this repo: the alt-league comparison needs
# alt-league username -> real-manager identity, which the raw file only carries for 5 of
# the 12 (Asa, Jayden, Nathan, Colin, and one unmatched). Same category of hand
# transcription as MANAGER_COLLEGE_AFFINITY below, for the same reason.
MANAGER_MEAN_REACH = {
    "Joseph": 4.1, "Dylan": 4.0, "Tyler": 3.4, "Colin": 0.9, "Kaiden": 0.6, "Asa": 0.2,
    "Nathan": -1.0, "Greg": -1.4, "Jayden": -1.4, "Nick": -1.5, "Ryan": -1.6, "Cailen": -3.8,
}
REACH_MAX_PICKS = 15.0  # owner ruling: the hard cap on how far a manager reaches
REACH_STD_BASE = 5.5  # modeling choice (not owner-specified): spreads the per-pick draw
                        # so +-15 sits within the plausible tail for a near-zero-mean manager
REACH_NEED_WIDEN_MEAN_BONUS = 5.0  # added to the mean reach for a position the manager still
                                    # needs, when that position is thinning in the pool (below)
REACH_NEED_WIDEN_STD_MULT = 1.5
# "Thinning" is evaluated once per intervening-pick window (not re-checked every simulated
# step -- see simulate_intervening_picks's docstring): fewer than this many STARTABLE
# (vorp > 0) players of the position remain in the pool passed to the simulation. Counting
# every row at the position instead of just the startable ones was tried first and never
# fired -- player_master carries 70+ TE rows including deep bench chaff, so a raw row
# count doesn't thin out inside any realistic in-draft window even when the startable tier
# genuinely has (measured at the pick 44 window that the whole TE-squeeze narrative is
# about: QB 10, RB 9, WR 25, TE 9 players left with vorp > 0).
THINNING_POOL_THRESHOLD = {"QB": 10, "RB": 12, "WR": 15, "TE": 10}

# ---------------------------------------------------------------------------
# Composite scoring weights (spec Section 6.5) -- single editable dict,
# renormalized at runtime over whichever layers are actually enabled.
# ---------------------------------------------------------------------------
COMPOSITE_WEIGHTS = {
    "base": 0.45,     # ppr_base_propadj -- the primary valuation, dominant by design
    "bonus": 0.25,     # bonus_est_ppr -- who gains most from the new 2026 scoring
    "factor": 0.15,    # analyst factor/archetype signal, normalized, never raw ordinal
    "market": 0.15,    # blended reference-ADP percentile
}
INJURY_WEIGHT = 1.5  # weight injury more heavily than the source (best-ball has no safety net)

# ---------------------------------------------------------------------------
# Player intel layer (work order 2026-08-16 item 3). `target`/`fade` are a BOUNDED
# nudge, hard-capped at +/-10 VOR points -- roughly one tier, enough to break a tie or
# jump a near neighbour, not enough to overrule the projection. Do not raise this cap
# and do not expose it as a UI slider (explicit instruction): an unbounded intel column
# would let a hunch quietly rebuild the board, defeating the point of having a
# projection system at all. `hard_avoid` is a filter, not a nudge -- it has no points
# value because it never reaches the arithmetic.
# ---------------------------------------------------------------------------
INTEL_NUDGE_CAP = 10.0
INTEL_TAG_SIGN = {"target": 1.0, "fade": -1.0}
INTEL_FILTER_TAGS = {"hard_avoid"}
# "watch" (added 2026-08-24, e.g. LaPorta's target tag pulled the same day): neither a
# nudge nor a filter -- the note stays visible and the row stays in recommendations, but
# it contributes 0 to composite_score. For a player the owner is actively reconsidering,
# that is the correct default: no thumb on the scale until he re-tags target or fade.
INTEL_NOTE_ONLY_TAGS = {"watch"}
INTEL_VALID_TAGS = set(INTEL_TAG_SIGN) | INTEL_FILTER_TAGS | INTEL_NOTE_ONLY_TAGS

# ---------------------------------------------------------------------------
# Roster target (spec Section 8 / R22). No K here: the props/factor-grid pipeline
# carries zero individual-kicker data (see DROP_POSITIONS below), so there is nothing
# for the tool to rank at that position -- the 16th and final pick (188) is a kicker
# taken by feel, outside the model. 15 tracked picks + 1 untracked K pick = 16.
# ---------------------------------------------------------------------------
ROSTER_TARGET = {"QB": 2, "RB": 5, "WR": 6, "TE": 2}
ROSTER_TARGET_FACTORY = dict(ROSTER_TARGET)  # setup screen's reset target -- see DRAFT_ORDER_2026_FACTORY
STARTING_LINEUP = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}

# ---------------------------------------------------------------------------
# Games-played haircut (spec Section 6.1 / SCHEMA doc) -- N games removed per position
# ---------------------------------------------------------------------------
GAMES_HAIRCUT = {"QB": 2, "WR": 2, "TE": 2, "RB": 3}

# ---------------------------------------------------------------------------
# Bonus model dispersion defaults (spec Section 6.2) -- one lognormal sigma per
# position, per stat. Exposed in the UI as a multiplier slider, default 1.0.
#
# Calibrated numerically against the real 2026 per-game rates and the archetype
# anchors in HANDOFF-draft-tool-context.md Section 1 (alpha WR ~6x 100-yard
# games/season, workhorse RB ~10x, elite QB ~7x 300-yard games, elite TE ~4x 100-yard
# games). RB and WR land close to their anchors because their projected per-game means
# sit near or above the bonus threshold, where lower sigma concentrates more mass past
# it. QB and TE do NOT reach their anchors: their means sit well below threshold (best
# 2026 passer projects ~252 pass yds/game vs. a 300 threshold; best TE ~59 combined
# yds/game vs. 100), and a lognormal moment-matched to a below-threshold mean has a
# mathematical ceiling on P(X >= threshold) as sigma varies -- past a peak (found
# numerically below), MORE dispersion makes the ceiling ceiling *lower*, not higher,
# because pushing sigma up also drags the fitted mu down. Both sigmas here are set at
# that peak, which is the best any lognormal fit can do for these positions this year;
# it still ranks players correctly (monotone in projected volume), it just tops out
# below the illustrative anchor. Worth re-checking with build/pipeline.py's calibration
# printout every refresh -- if the anchor becomes reachable in a future season with
# richer QB/TE projections, these sigmas should move back down toward WR/RB territory.
# ---------------------------------------------------------------------------
BONUS_SIGMA_SCRIMMAGE = {"QB": 0.80, "RB": 0.22, "WR": 0.40, "TE": 0.95}
BONUS_SIGMA_PASSING = {"QB": 0.60}
SCRIMMAGE_BONUS_THRESHOLD = 100
PASSING_BONUS_THRESHOLD = 300
BONUS_POINTS_PER_GAME = 3

# ---------------------------------------------------------------------------
# Availability model re-estimation (spec Section 7 / R8)
# ---------------------------------------------------------------------------
PACE_REESTIMATE_AFTER_PICKS = 20

# ---------------------------------------------------------------------------
# NFL-team and college bias -- manager-level affinities.
#
# NFL-team bias magnitudes are COMPUTED from drafts/all_draft_picks_2022-2025.csv by
# build/pipeline.py (data/derived/team_bias.csv) rather than hand-transcribed, so this
# stays portable to another league automatically.
#
# College bias cannot be computed -- Sleeper's board carries no college field. The
# manager -> college AFFINITY below is owner-stated data (HANDOFF-draft-tool-context.md
# Section 2, the members table), not recall. The PLAYER -> college mapping that this
# joins against (data/external/college_bias_recall.csv) *is* model recall and is
# labeled as such on every row.
# ---------------------------------------------------------------------------
MANAGER_COLLEGE_AFFINITY = {
    "Nathan": ["Ohio State"],
    "Ryan": ["Ohio State"],
    "Tyler": ["Ohio State"],
    "Nick": ["Alabama", "Illinois", "Iowa State"],
    "Greg": ["Alabama", "Illinois", "Iowa State"],
    "Cailen": ["Florida State"],
    "Jayden": ["UCF"],
    "Joseph": ["Florida"],
    "Kaiden": ["Florida"],
    # Dylan, Colin, Asa: no college lean stated.
}
COLLEGE_BIAS_DISCOUNT = 0.85  # multiplier on survival probability when a bias applies
NFL_TEAM_BIAS_CAP = 0.6  # floor/ceiling multiplier clamp so a computed bias can't zero out survival

# ---------------------------------------------------------------------------
# Team-code canonical mapping -- three-way (NFFC / Sleeper / Clay-ESPN), spec Section 4.4.
# Canonical set matches standard NFL abbreviations. Only exceptions are listed; every
# other team is assumed identical across all three sources.
# ---------------------------------------------------------------------------
CANONICAL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
    "HOU", "IND", "JAX", "KC", "LAC", "LAR", "LV", "MIA", "MIN", "NE", "NO", "NYG",
    "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS", "FA",
}

# canonical -> {source: source_code}, exceptions only
TEAM_MAP_EXCEPTIONS = {
    "ARI": {"nffc": "ARZ", "sleeper": "ARI", "clay": "ARZ"},
    "BAL": {"nffc": "BAL", "sleeper": "BAL", "clay": "BLT"},
    "CLE": {"nffc": "CLE", "sleeper": "CLE", "clay": "CLV"},
    "HOU": {"nffc": "HOU", "sleeper": "HOU", "clay": "HST"},
    "JAX": {"nffc": "JAX", "sleeper": "JAC", "clay": "JAX"},
    "LAR": {"nffc": "LA", "sleeper": "LAR", "clay": "LAR"},
    "LV": {"nffc": "LV", "sleeper": "LVR", "clay": "LV"},
    "FA": {"nffc": "FA", "sleeper": "UNS", "clay": None},
}


def team_lookup(source: str) -> dict[str, str]:
    """source_code -> canonical, for the given source ('nffc' | 'sleeper' | 'clay')."""
    lookup = {team: team for team in CANONICAL_TEAMS}
    for canonical, sources in TEAM_MAP_EXCEPTIONS.items():
        code = sources.get(source)
        if code:
            lookup[code] = canonical
    return lookup


def canonical_team(raw_code: str, source: str) -> str | None:
    if raw_code is None:
        return None
    code = str(raw_code).strip().upper()
    if not code or code in {"NONE", "NAN", ""}:
        return None
    return team_lookup(source).get(code)  # None means unmapped -- caller must flag it, never guess


# ---------------------------------------------------------------------------
# Position-code mapping. DEF is dropped entirely (league has no team defenses since
# 2023).
#
# K is dropped from NFFC specifically, but not from Sleeper -- the two sources are NOT
# symmetric here. Verified against the raw NFFC export (12.4.4): every single "TK" row
# is a team-level placeholder ("Falcons, Atlanta", "Bills, Buffalo", ...) plus two joke
# entries ("Holder, Jeff", "Kicker, Butt") -- NFFC carries zero real per-kicker ADP data
# this year. Sleeper's "K" rows ARE real, individually named kickers (Brandon Aubrey,
# Cameron Dicker, ...). Since no props/factor-grid file covers K at all (R22 -- the
# tool doesn't rank kickers), Sleeper's K rows are harmless orphans kept for reference
# display; NFFC's are dropped at the source so they stop generating low-adp_n noise
# notes for team names, not players.
# ---------------------------------------------------------------------------
POSITION_MAP_EXCEPTIONS = {
    "nffc": {"TK": "K", "TDSP": "DEF"},
    "sleeper": {"DEF": "DEF"},
}
DROP_POSITIONS = {
    "nffc": {"DEF", "K"},
    "sleeper": {"DEF"},
}


def canonical_position(raw_pos: str, source: str) -> str:
    pos = str(raw_pos).strip().upper()
    return POSITION_MAP_EXCEPTIONS.get(source, {}).get(pos, pos)


# ---------------------------------------------------------------------------
# Name normalization (spec Section 5 rule #3 / Section 4.4).
# Suffix matched as a WHOLE TOKEN only -- never by stripping periods, because
# "St. Brown" contains a period that is not a suffix.
# ---------------------------------------------------------------------------
SUFFIX_WHITELIST = {"JR", "SR", "II", "III", "IV"}
_SUFFIX_TOKEN_RE = re.compile(r"^(JR|SR|II|III|IV)\.?$")
_PUNCT_RE = re.compile(r"[.\']")
_HYPHEN_RE = re.compile(r"-")
_WS_RE = re.compile(r"\s+")


def split_suffix(full_name: str) -> tuple[str, str | None]:
    """('Travis Etienne Jr.', ) -> ('Travis Etienne', 'Jr')"""
    tokens = full_name.strip().split()
    if tokens and _SUFFIX_TOKEN_RE.match(tokens[-1].upper()):
        suffix = tokens[-1].upper().rstrip(".")
        return " ".join(tokens[:-1]), suffix
    return full_name.strip(), None


def normalize_name(full_name: str) -> str:
    """Strip suffix, lowercase, drop periods/apostrophes, collapse hyphens to spaces."""
    if not full_name:
        return ""
    base, _ = split_suffix(full_name)
    base = _PUNCT_RE.sub("", base)
    base = _HYPHEN_RE.sub(" ", base)
    base = _WS_RE.sub(" ", base).strip().lower()
    return base


def parse_nffc_name(raw: str) -> tuple[str, str, str | None]:
    """
    NFFC format: 'Last, First' with the suffix attached to the surname half, e.g.
    'Etienne Jr., Travis' -> (first='Travis', last='Etienne', suffix='Jr').
    Split on ', ' first -- never strip periods before splitting, or 'St. Brown, Amon-Ra'
    breaks.
    """
    if ", " not in raw:
        # Defensive fallback for a malformed row; treat whole string as last name.
        surname_part, given_part = raw.strip(), ""
    else:
        surname_part, given_part = raw.split(", ", 1)
    last, suffix = split_suffix(surname_part)
    first = given_part.strip()
    return first, last, suffix


def parse_sleeper_name(raw: str) -> tuple[str, str, str | None]:
    """Sleeper format: 'First Last Suffix', e.g. 'Kenneth Walker III'."""
    base, suffix = split_suffix(raw)
    tokens = base.strip().split()
    if len(tokens) < 2:
        return base.strip(), "", suffix
    first = tokens[0]
    last = " ".join(tokens[1:])
    return first, last, suffix
