"""
board_model.py — pure model layer for the cockpit UI.

No UI-framework import anywhere in this file, deliberately: the work order's rule that
a check for that import across app/*.py returns only the UI entry points still holds,
and it is what keeps a real frontend cheap to build later.

Everything here sits ON TOP of the existing pipeline. It reads the same
data/derived/player_master.csv the current app reads, and it calls the existing
draft_engine functions rather than reimplementing them. Four things are new:

  1. The kicker slide (Sleeper ranks kickers inside rounds 1 to 13; nobody drafts a
     kicker there, so they move to round 14 and everyone above them comes up).
  2. The wait rule, which is the reason the board stopped suggesting a quarterback in
     round 1 and Trevor Lawrence in round 6.
  3. Route construction, three picks deep, anchored on the owner's own intel names.
  4. The rule timeline: every warning and the round it starts watching.

Drop this in Tool/app/.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import lognorm, norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402
import draft_engine as de  # noqa: E402

# ---------------------------------------------------------------------------
# Tunables that belong to the UI layer, not to the valuation model.
# ---------------------------------------------------------------------------
ROUND_14_FIRST_PICK = config.round_start_pick(config.KICKER_ROUND)  # 157
WAIT_THRESHOLD = 0.70    # at or above this, taking him now wastes the pick
CLOSE_THRESHOLD = 0.40   # between the two, it is a coin flip
GONE_THRESHOLD = 0.15    # below this he will not reach you at all
ROUTE_LEGS = 3           # owner's answer: project three picks ahead
BOARD_ROWS = 36          # three rounds of players visible
# Work order 2026-08-24b item 3 (R38): reads config.AVAILABILITY_N_SIMS rather than a
# second hardcoded literal -- this and config's copy had drifted into agreement (both
# 2000) by coincidence, not by construction, before this file existed. "Keep the count
# in config.py" per the work order: this is that, made structural instead of just true
# today.
N_SIMS_UI = config.AVAILABILITY_N_SIMS  # the band on the odds is the spread across these runs
# Work order 2026-08-24b item 2: candidates_for_pick's near-equal tiebreak band, in
# whole PPR points -- the unit `edge`/`vorp` are already denominated in, not a fitted
# constant. Two candidates within one point of each other are treated as tied on
# value and broken by intel/sharp-market signal; anything wider is decided on edge
# alone, which is what stops a window-mismatched intel tag from outranking a
# materially better player (the reported Chase Brown / Ja'Marr Chase bug).
EDGE_TIE_BAND_POINTS = 1.0

# Sleeper's own position colours, so the board matches the draft room.
POSITION_COLORS = {"QB": "#fc2b6d", "RB": "#73c3a6", "WR": "#46a2ca", "TE": "#cc8c4a"}
POSITION_WORDS = {"QB": "Quarterback", "RB": "Running back", "WR": "Receiver", "TE": "Tight end"}


# ===========================================================================
# 1. Kicker slide
# ===========================================================================
def kickers_inside_the_draft(sleeper_raw_path: Path | None = None) -> list[int]:
    """Sleeper ADP ranks of every kicker who appears before round 14.

    Measured on sleeper_adp_ppr_2026-08-16.csv: 128, 129, 131, 139, 141, 150, 153.
    Read from the file rather than hard-coded, because next season's pull will differ.
    """
    path = sleeper_raw_path or config.latest_sleeper_adp_raw_path()
    raw = pd.read_csv(path)
    ks = raw.loc[raw["Pos"].astype(str).str.upper() == "K", "ADP"]
    return sorted(int(r) for r in ks.dropna() if int(r) < ROUND_14_FIRST_PICK)


def apply_kicker_slide(master: pd.DataFrame, kicker_ranks: list[int] | None = None) -> pd.DataFrame:
    """Move every early kicker to round 14 and pull the skill players above them up.

    Overwrites `reference_adp_rank` in place on a copy, and keeps the untouched value
    in `reference_adp_rank_raw` so the adjustment stays auditable. Overwriting is
    deliberate: draft_engine anchors its survival curve on reference_adp_rank, so this
    one assignment makes the whole availability model kicker agnostic rather than just
    the display.

    `kickers_reranked_count` in `df.attrs` (work order 2026-08-24 item 9: "have the
    pipeline emit the count so it cannot drift again") replaces the hand-typed "62
    players re-ranked" figure that turned out to be stale (measured 82) -- read this
    instead of retyping a number into a doc that will go stale again next August.

    K rows themselves (work order item 7 / R35, added upstream in build/pipeline.py)
    are excluded from the skill-player shift formula and instead placed at round 14
    onward IN THEIR OWN RELATIVE ORDER -- the shift formula describes what happens to
    players ABOVE a kicker, not what happens to kickers relative to each other, and
    applying it to a kicker's own rank would be meaningless.
    """
    df = master.copy()
    ranks = kicker_ranks if kicker_ranks is not None else kickers_inside_the_draft()
    arr = np.asarray(ranks, dtype=float)
    df["reference_adp_rank_raw"] = df["reference_adp_rank"]
    is_kicker = df["position"] == "K"

    def shift(r):
        if pd.isna(r):
            return r
        return r - int((arr < r).sum())

    skill = ~is_kicker
    df.loc[skill, "reference_adp_rank"] = df.loc[skill, "reference_adp_rank"].apply(shift)
    if is_kicker.any():
        order = df.loc[is_kicker, "reference_adp_rank_raw"].rank(method="first")
        df.loc[is_kicker, "reference_adp_rank"] = ROUND_14_FIRST_PICK + order - 1

    reranked = skill & df["reference_adp_rank_raw"].notna() & (df["reference_adp_rank"] != df["reference_adp_rank_raw"])
    df.attrs["kickers_moved"] = ranks
    df.attrs["kickers_reranked_count"] = int(reranked.sum())
    return df


# ===========================================================================
# 2. Survival helpers and the wait rule
# ===========================================================================
def _fit(row: pd.Series):
    """Reuse draft_engine's own lognormal fit so the UI and the engine can never
    disagree about a player's curve."""
    return de._fit_from_row(row)


def survival_between(row: pd.Series, from_pick: int, to_pick: int) -> float:
    """P(still there at to_pick | still there at from_pick), off the fitted curve."""
    params = _fit(row)
    if params is None:
        return 0.5
    mu, sigma, _ = params
    s_from = float(lognorm.sf(max(from_pick, 1e-6), s=sigma, scale=math.exp(mu)))
    s_to = float(lognorm.sf(max(to_pick, 1e-6), s=sigma, scale=math.exp(mu)))
    if s_from <= 1e-9:
        return 0.005
    return float(np.clip(s_to / s_from, 0.005, 0.995))


def _survival_between_vectorized(
    pool: pd.DataFrame, from_pick: int, to_pick: int, fit: tuple | None = None
) -> np.ndarray:
    """Work order 2026-08-24b item 3: the same probability as `survival_between`, for
    every row of `pool` at once -- two scipy calls total (one per pick number) instead
    of two scipy calls PER ROW, which is what made `candidates_for_pick`'s
    `pool.iterrows()` loop the dominant cost of a route rebuild (~430 rows x 2 calls,
    invoked once per route plus once per leg, ~10 times a render). `fit`, if given, is
    the (mu_arr, sigma_arr, has_fit) triple from `de._lognormal_fit_arrays(pool)`,
    reused so a caller needing both an availability AND a wait probability from the
    same pool fits each player's curve only once instead of twice.
    """
    mu_arr, sigma_arr, has_fit = fit if fit is not None else de._lognormal_fit_arrays(pool)
    out = np.full(len(pool), 0.5)
    if has_fit.any():
        mu, sigma = mu_arr[has_fit], sigma_arr[has_fit]
        scale = np.exp(mu)
        s_from = lognorm.sf(max(from_pick, 1e-6), s=sigma, scale=scale)
        s_to = lognorm.sf(max(to_pick, 1e-6), s=sigma, scale=scale)
        safe_from = np.where(s_from <= 1e-9, 1.0, s_from)
        ratio = np.clip(s_to / safe_from, 0.005, 0.995)
        out[has_fit] = np.where(s_from <= 1e-9, 0.005, ratio)
    return out


@dataclass
class Timing:
    word: str      # NOW | CLOSE | WAIT | GONE
    sentence: str
    worth_the_pick: bool


def timing(wait_odds: float, reference_pick: int, availability: float = 1.0) -> Timing:
    """The rule that stopped the board recommending players you can simply have later.

    The pick in front of you is only well spent on someone who will not be there next
    time. A quarterback who is 81 percent to reach pick 20 is not a round 1 pick, and
    Trevor Lawrence at 95 percent to reach 68 is not a pick 53. Both were real
    complaints about the earlier build.
    """
    if availability < GONE_THRESHOLD:
        return Timing("GONE", "Gone before your turn", False)
    if wait_odds >= WAIT_THRESHOLD:
        return Timing("WAIT", f"He is still there at {reference_pick}", False)
    if wait_odds >= CLOSE_THRESHOLD:
        return Timing("CLOSE", f"Coin flip to reach {reference_pick}", True)
    return Timing("NOW", f"Gone before {reference_pick}", True)


_position_offsets_cache: dict[str, float] | None = None


def _position_offsets() -> dict[str, float]:
    """position -> mean(comparison_adp_value - reference_adp_rank) for that position,
    i.e. the position's OWN average "how far ahead of NFFC ADP" gap -- the number
    `market_reach_gap` below subtracts out. Read fresh from
    data/derived/adp_source_offsets.csv (R27, recomputed every build by
    build/pipeline.py's compute_adp_source_offsets) rather than hard-coded, per work
    order 2026-08-29 item 1's explicit instruction. That file stores the OTHER
    orientation (`mean_offset_reference_minus_comparison`, i.e. sleeper_rank minus
    nffc_adp -- the quantity the 2026-08-16 offset work already needed); negated here
    because market_reach_gap is defined as nffc_adp minus sleeper_rank, the orientation
    the owner's ruling specified ("how far ahead of NFFC ADP a pick is").

    Cached at module level: this file only changes on a full pipeline rebuild, which
    always restarts the app process anyway (same reasoning as
    draft_state._sleeper_crosswalk's cache)."""
    global _position_offsets_cache
    if _position_offsets_cache is None:
        path = config.ADP_SOURCE_OFFSETS_PATH
        if path.exists():
            df = pd.read_csv(path)
            _position_offsets_cache = {
                row["position"]: -float(row["mean_offset_reference_minus_comparison"])
                for _, row in df.iterrows()
            }
        else:
            _position_offsets_cache = {}
    return _position_offsets_cache


def market_reach_gap(row: pd.Series) -> float | None:
    """Work order 2026-08-29 item 1 (R37): position-adjusted replacement for the old
    `sharp_edge`. Positive means THIS player is being drafted further ahead of his own
    NFFC ADP than is typical for his position -- a bigger-than-normal reach; negative
    means better value than his position's own norm.

    Why position-adjusted: measured position offsets (data/derived/adp_source_offsets.csv)
    show QB and TE both run structurally positive under the raw gap (Sleeper leagues
    draft the position everyone needs one of earlier than a high-stakes market that can
    punt on a weak QB2), and WR runs structurally negative -- not because any individual
    QB or TE is overpriced, but because the position itself is a market-wide onesie
    premium. Subtracting the position's own average gap is what turns "every QB and TE
    reads as a reach" into "THIS QB is more or less of a reach than a typical QB,"
    which is the quantity actually worth penalizing.

    Returns None (not 0.0) when NFFC has no coverage for this player at all -- callers
    must treat that as unknown, not neutral (work order's explicit instruction);
    `reach_penalty` below returns a zero penalty in that case, but the None is what
    lets a caller flag the row instead of silently assuming average.
    """
    nffc = row.get("comparison_adp_value")
    sleeper = row.get("reference_adp_rank")
    if pd.isna(nffc) or pd.isna(sleeper):
        return None
    raw_gap = float(nffc) - float(sleeper)
    offset = _position_offsets().get(row.get("position"), 0.0)
    return raw_gap - offset


# Modeling choices, not owner-specified and not fit to the backtest (work order's own
# "do not tune to the backtest") -- bounded and soft, matching the owner's own framing
# ("a SOFT penalty") and the existing INTEL_NUDGE_CAP's precedent for a bounded nudge
# rather than an unbounded one. REACH_PENALTY_WEIGHT: an 8-pick bigger-than-typical
# reach costs 8 * 0.15 = 1.2 edge points -- roughly EDGE_TIE_BAND_POINTS's own scale,
# enough to matter among near-equal candidates without being able to flip a real
# multi-tier edge gap on its own (REACH_PENALTY_CAP bounds that further).
REACH_PENALTY_WEIGHT = 0.15
REACH_PENALTY_CAP = 15.0


_worst_reach_penalty_cache: dict[str, float] | None = None


def _worst_reach_penalty() -> dict[str, float]:
    """Work order 2026-08-29c item 3: the worst (highest) `reach_penalty` actually
    observed for each position, among players with BOTH ADP sources present -- what a
    player missing exactly one market gets instead of 0.0 (see `reach_penalty`'s own
    docstring for why 0.0 is the bug: "null reach penalty must never be zero... treat
    it as the worst observed penalty for that position"). Computed once from the built
    player_master.csv and cached at module level, same reasoning as
    `_position_offsets_cache`; unlike that cache, this needs the full per-player
    penalty distribution (not a precomputed mean), so it reads player_master.csv
    directly rather than adp_source_offsets.csv.
    """
    global _worst_reach_penalty_cache
    if _worst_reach_penalty_cache is None:
        if not config.PLAYER_MASTER_PATH.exists():
            _worst_reach_penalty_cache = {}
        else:
            master = pd.read_csv(config.PLAYER_MASTER_PATH)
            has_both = master["reference_adp_rank"].notna() & master["comparison_adp_value"].notna()
            covered = master[has_both]
            penalties = covered.apply(reach_penalty, axis=1)
            _worst_reach_penalty_cache = {
                pos: float(penalties[covered["position"] == pos].max())
                for pos in config.POSITIONS
                if (covered["position"] == pos).any()
            }
    return _worst_reach_penalty_cache


def missing_both_markets(row: pd.Series) -> bool:
    """Work order 2026-08-29c item 3 (the Jayden Higgins class): true when NEITHER
    ADP source has this player at all. That combination is strong negative
    information on its own -- injury, suspension, camp casualty, retirement -- not
    "no opinion," and is what let a pre-injury `ppr_base` (Higgins' 124.95, still from
    his last healthy projection) read as a free top-30-at-his-position value 100+
    picks before a now-nonexistent ADP. `candidates_for_pick` excludes these rows
    entirely; `market_coverage_flag` below is what the full board shows instead."""
    return pd.isna(row.get("reference_adp_rank")) and pd.isna(row.get("comparison_adp_value"))


def market_coverage_flag(row: pd.Series) -> str | None:
    """Work order 2026-08-29c item 3: "shown on the full board with an explicit
    'no market' flag" for a player missing both sources, and a lighter flag for a
    player missing exactly one -- display-only, never a ranking input (candidates_for_
    pick's own exclusion of `missing_both_markets` rows is the ranking-side half of
    this fix, done separately). None when both sources have him -- the normal case,
    nothing to flag."""
    no_reference = pd.isna(row.get("reference_adp_rank"))
    no_comparison = pd.isna(row.get("comparison_adp_value"))
    if no_reference and no_comparison:
        return "no_market"
    if no_reference or no_comparison:
        return "partial_market"
    return None


def reach_penalty(row: pd.Series) -> float:
    """Soft, ONE-DIRECTIONAL points penalty (work order 2026-08-29 item 1): only a
    bigger-than-typical-for-position reach (positive market_reach_gap) costs anything;
    being under the position's own norm costs nothing (the owner asked for a penalty,
    not a symmetric bonus).

    Work order 2026-08-29c item 3: a player missing EITHER ADP source is unknown, not
    average -- returning 0.0 here (the original behavior) silently treated him as a
    typical, unremarkable reach, which is exactly how Jayden Higgins (missing from
    both markets, injured) looked like free value instead of a red flag. Falls back to
    `_worst_reach_penalty`'s measured worst-observed-for-position value instead of
    0.0. Players missing BOTH sources are excluded from ranking entirely elsewhere
    (`missing_both_markets`, enforced in `candidates_for_pick`); this fallback value
    only matters in practice for a player missing exactly one market, who stays
    eligible, or for the full board's own display of an excluded player.
    """
    gap = market_reach_gap(row)
    if gap is not None:
        return 0.0 if gap <= 0 else float(min(gap * REACH_PENALTY_WEIGHT, REACH_PENALTY_CAP))
    return _worst_reach_penalty().get(row.get("position"), REACH_PENALTY_CAP)


# Work order 2026-08-29c items 1+2 (roster-need discount). `remaining` was threaded
# into candidates_for_pick's signature and never read inside its body -- it only
# survived into build_route's own `shape` dict for the "you still owe two receivers"
# display line. The result: a QB or TE already holding its starting slot kept scoring
# exactly as well as before it was drafted, because nothing downstream of the pool
# filter ever looked at roster state. Confirmed by the owner as the intended reading
# of his own, seemingly contradictory report ("should NOT keep suggesting once
# filled" -- see "Awaiting owner confirmation" in the work order).
#
# Bounded, documented -- not fit to the backtest. Both branches below measure the
# ACTUAL gap between positions' thinness (data-driven, see need_discount's own
# docstring) rather than guessing a constant; the flat constants here are FLOORS --
# they only bind when the measured gap is small or unavailable, they never soften a
# larger measured gap.
QB2_NEED_DISCOUNT = 30.0        # QB is not in config.FLEX_ELIGIBLE -- once QB1 is
                                 # filled, a second QB competes for nothing but a bench
                                 # slot, so this is a FLOOR under the measured gap, not
                                 # just a fallback: even where the data says the gap is
                                 # small, QB still never falls softer than a genuine
                                 # cliff (work order's phrase, consistent with R11
                                 # naming QB2 the designated sacrifice).
FLEX_NEED_DISCOUNT_FALLBACK = 10.0  # RB/WR/TE only, and only when
                                 # `edge_expectation_at_target` isn't available at all
                                 # (config.AVAILABILITY_METHOD == "lognormal", or no
                                 # intervening picks to simulate) -- no floor otherwise,
                                 # since a real flex path means a small measured gap
                                 # (or none) is the correct, literal answer.


def need_discount(
    position: str, roster_counts: dict[str, int], edge_expectation: dict[str, float] | None = None
) -> float:
    """Points subtracted from a candidate's RANKING value (never from `edge`/`vorp`
    themselves -- the work order's explicit "do not change the edge formula" line)
    once `position`'s own dedicated starting slot(s) are already filled on the roster
    described by `roster_counts`.

    `roster_counts` here may be the owner's real, currently-drafted counts (the
    anchor-selection call in build_routes) or a hypothetical count that already
    includes legs chosen earlier in the SAME route (build_route recomputes it fresh
    from `shape`, which it decrements after each leg) -- both read the same way,
    which is what makes a route stop offering a second QB against its own first leg,
    not just against the roster the owner already holds.

    The work order's own instruction: "his fallback comparison is the best
    flex-eligible alternative (RB/WR/TE), not the best at his own position."
    `edge_expectation` (draft_engine.compute_availability's `edge_expectation_at_target`
    attrs, the SAME E[max vorp available at the target pick] per position that `edge`
    itself already nets out) makes that literal for RB/WR/TE: the discount is the gap
    between the position with the deepest expected leftover value at the target pick
    (the real flex fallback) and THIS position's own -- a data-driven number, not a
    guess, and exactly why a thin position (TE, per the work order's own "TE is 10 of
    the top 30 by edge" measurement) falls hard while a deep one barely moves.

    QB has no flex path at all -- the SAME comparison against the best flex-eligible
    alternative still applies (a second QB's real opportunity cost is whatever
    RB/WR/TE value is left on the board, since a backup QB is nearly unplayable), with
    QB2_NEED_DISCOUNT as a documented FLOOR under it so QB never falls softer than the
    owner's own "cliff" framing even in a draft state where the measured gap happens
    to be small.

    0.0 whenever the position's own starting slot(s) are not yet filled ("the
    scarcity is real", work order's words) or the position has no STARTING_LINEUP
    entry at all (e.g. K, handled outside this model).
    """
    held = roster_counts.get(position, 0)
    starters = config.STARTING_LINEUP.get(position, 0)
    if starters <= 0 or held < starters:
        return 0.0
    edge_expectation = edge_expectation or {}
    flex_dropoffs = [edge_expectation[p] for p in config.FLEX_ELIGIBLE if p in edge_expectation]
    is_flex = position in config.FLEX_ELIGIBLE
    if not flex_dropoffs or position not in edge_expectation:
        return FLEX_NEED_DISCOUNT_FALLBACK if is_flex else QB2_NEED_DISCOUNT
    measured = max(0.0, max(flex_dropoffs) - edge_expectation[position])
    return float(measured if is_flex else max(measured, QB2_NEED_DISCOUNT))


# ---------------------------------------------------------------------------
# Work order 2026-08-29c item 6: pace targets. `need_discount` above is about
# STARTING-SLOT status ("no QB1" vs "QB1 filled"); this is a different question --
# accumulating enough DEPTH by the round the plan actually expects it, e.g. "we need
# to still worry about 5 WRs by round 9 or 10" (owner's own words) even though a
# WR's own starting slots (2, config.STARTING_LINEUP) are long since filled by then.
# "Existing rules are threshold checks that fire once a target is already missed" --
# this is the graduated version, feeding `candidates_for_pick`'s ranking directly
# instead of only a one-shot warning banner once round 9/10 has already passed.
#
# Explicit owner-named targets where the owner named one; the league's OWN
# empirically-measured pace (config.ROUND_BANDS / build/pipeline.py's
# compute_round_band_position_shares, item 7) for RB, the position item 6 explicitly
# ties to item 7's round-band data ("RB volume across rounds 9-13 (item 7)") rather
# than a second hand-picked number. TE deliberately contributes NOTHING here --
# "TE per the fork setting" is already exactly what pick53_fork_state/W16 track
# (round-8 threshold against the configured fork player), and duplicating that as a
# second, independent pace number here would double-count the same signal.
PACE_TARGETS = {
    "WR": (5, 10),  # "5 WRs by round 9 or 10"
    # QB1's OWN live-pace-adjusted trigger round already exists (_w3/_w4's
    # `qb_pace_picks_before_round7`), but that signal lives on RosterState, which
    # candidates_for_pick's roster_counts dict doesn't carry. Round 8 here is
    # deliberately the SIMPLER, fixed version of the same "QB1 pace-driven" idea --
    # not a replacement for _w3/_w4, which stay the authoritative warning.
    "QB": (1, 8),
}
PACE_URGENCY_PER_PLAYER = 5.0  # ranking points per whole player behind pace
PACE_URGENCY_CAP = 20.0

_round_band_shares_cache: dict[tuple[str, str], float] | None = None


def _round_band_shares() -> dict[tuple[str, str], float]:
    """(round_band, position) -> share_pct, read once from
    data/derived/round_band_position_shares.csv (item 7) and cached at module level,
    same reasoning as `_position_offsets_cache`."""
    global _round_band_shares_cache
    if _round_band_shares_cache is None:
        path = config.ROUND_BAND_POSITION_SHARES_PATH
        if path.exists():
            df = pd.read_csv(path)
            _round_band_shares_cache = {
                (row["round_band"], row["position"]): float(row["share_pct"]) for _, row in df.iterrows()
            }
        else:
            _round_band_shares_cache = {}
    return _round_band_shares_cache


def pace_expected_count(position: str, current_round: int) -> float:
    """Cumulative picks at `position` a TYPICAL manager in this league has made by
    `current_round` -- one pick per round times that round's band's own measured
    share (item 7), summed band by band up to (and partway through) the current
    one. This is a LEAGUE-AVERAGE pace, not a target the owner is required to hit;
    `pace_gap` is what turns "behind the league average" into a signed gap."""
    shares = _round_band_shares()
    total = 0.0
    for label, lo, hi in config.ROUND_BANDS:
        if lo > current_round:
            break
        band_rounds = min(hi, current_round) - lo + 1
        total += band_rounds * shares.get((label, position), 0.0) / 100.0
    return total


def pace_gap(position: str, held: int, current_round: int) -> float:
    """Whole players BEHIND pace at `position` right now -- 0.0 if on pace or ahead
    of pace. Positive means behind; never negative (being ahead of pace is not
    something item 6 asks to reward, only "surface the gap [when behind] as
    urgency").

    Explicit targets (`PACE_TARGETS`) ramp LINEARLY from 0 at round 1 to `target` at
    `by_round` -- "5 WR by round 9 or 10" is a deadline the concern builds toward,
    not a step function that is silent through round 9 and only speaks at round 10;
    the owner's own report names round 9 as already worth worrying about."""
    if position in PACE_TARGETS:
        target, by_round = PACE_TARGETS[position]
        implied = target * min(current_round, by_round) / by_round
        return float(max(0.0, math.floor(implied) - held))
    if position == "TE":
        return 0.0  # deferred entirely to the fork setting's own W16 signal -- see module docstring above
    if not config.layer_on("manager_priors"):
        return 0.0  # item 7's own data comes from the manager-priors layer's source file
    # floor(), not the raw fraction -- "behind by 0.44 of a player" at pick 1 (any
    # held count starts at 0 against a nonzero fractional expectation) would
    # otherwise manufacture a small phantom bonus from the very first pick of the
    # draft, before any real gap exists. Whole-player gaps only.
    return float(max(0.0, math.floor(pace_expected_count(position, current_round)) - held))


def pace_urgency(position: str, held: int, current_round: int) -> float:
    """Ranking-value BONUS (points) for being behind pace -- subtracted from
    `need_discount` (not added as a separate signed term) inside candidates_for_pick,
    so a position that is already fully need-weighted (need_discount == 0) can still
    gain EXTRA priority on top once the owner is visibly behind his own plan."""
    return float(min(pace_gap(position, held, current_round) * PACE_URGENCY_PER_PLAYER, PACE_URGENCY_CAP))


# ===========================================================================
# 3. Availability with a confidence band
# ===========================================================================
def availability_with_band(
    board: pd.DataFrame,
    as_of_pick: int,
    target_pick: int,
    manager_priors: pd.DataFrame,
    team_bias: pd.DataFrame,
    drafted_name_keys: set[str],
    owner_roster_by_manager: dict,
    n_sims: int = N_SIMS_UI,
    wait_pick: int | None = None,
) -> pd.DataFrame:
    """draft_engine.compute_availability plus the spread across the simulation runs.

    The band is the standard error of the survival fraction itself, sqrt(p(1-p)/n).
    At two thousand runs that is about one point at the middle of the range, which is
    small on purpose: it says the simulation has converged, and it stops a 3 percent
    reading being mistaken for precision it does not have.

    `wait_pick` (work order 2026-08-24 item 2 / R30), when given, is a second checkpoint
    computed from the SAME simulation run -- exposed as `survival_probability_wait` plus
    its own band -- so the wait rule and the primary board number can never disagree
    about which method produced them, only about which pick they're asking about.

    Always requests `edge` too (work order 2026-08-24b items 2+4) -- from the SAME
    simulation pass, so this never doubles the montecarlo cost. `candidates_for_pick`
    reads it off the board this function returns instead of raw `vorp`.
    """
    out = de.compute_availability(
        board,
        as_of_pick=as_of_pick,
        target_pick=target_pick,
        manager_priors=manager_priors,
        team_bias=team_bias,
        drafted_name_keys=drafted_name_keys,
        owner_roster_by_manager=owner_roster_by_manager,
        n_sims=n_sims,
        wait_pick=wait_pick,
        want_edge=True,
    )
    p = out["survival_probability"].clip(0, 1)
    out["survival_band"] = np.sqrt((p * (1 - p)) / n_sims) * 1.96
    out["survival_band_pts"] = (out["survival_band"] * 100).round().astype(int)
    if wait_pick is not None:
        pw = out["survival_probability_wait"].clip(0, 1)
        out["survival_band_wait"] = np.sqrt((pw * (1 - pw)) / n_sims) * 1.96
        out["survival_band_wait_pts"] = (out["survival_band_wait"] * 100).round().astype(int)
    return out


# ===========================================================================
# 4. Routes
# ===========================================================================
def owner_pick_numbers() -> list[int]:
    return [w["overall"] for w in config.owner_pick_windows()]


def _intel_windows(row: pd.Series) -> list[int]:
    raw = row.get("intel_windows")
    if pd.isna(raw) or not str(raw).strip():
        return []
    out = []
    for part in str(raw).split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def candidates_for_pick(
    pool: pd.DataFrame,
    pick: int,
    from_pick: int,
    next_after: int,
    shape: dict[str, int],
    used: set[str],
    min_availability: float = 0.5,
    ignore_wait: bool = False,
) -> pd.DataFrame:
    """Who is worth spending `pick` on.

    `ignore_wait` (urgent fix, 2026-08-30): skips ONLY the wait-threshold cut below --
    every other filter (shape/hard_avoid/no_market/min_availability) still applies.
    Default False, so every existing caller is unaffected; build_routes' own
    short-gap fallback is the only caller that passes True.

    Sorted on VALUE first (work order 2026-08-24b item 2, fixing "the route sorter
    never looks at value"): `edge` (item 4's drop-off-adjusted vorp -- vorp net of how
    much of that position's value is expected to survive to the owner's own next turn
    anyway), read straight off `pool`'s `edge` column rather than recomputed here. The
    old sort put `on_list`/`priority` FIRST, which meant a player absent from the intel
    table (on_list=False) sorted below every intel-tagged name regardless of being the
    best player on the board, and a name tagged for a DIFFERENT pick window still
    carried its priority number everywhere else (Chase Brown, tagged priority 1 for
    window 20, outranking Ja'Marr Chase at pick 5) -- the intel cap's whole point,
    bounding intel's influence, defeated through a side door that never touched
    composite_score at all.

    Intel now only matters as a TIEBREAK among near-equal `edge` values (within
    EDGE_TIE_BAND_POINTS of each other) and only when `pick` is inside the row's own
    `intel_windows` -- outside that window, `priority` reads exactly like a non-intel
    player's (99.0), never as a boost for a pick it wasn't written for. `on_list`
    itself still reflects the window check for display ("Your note" in cockpit_html).

    `hard_avoid` remains a hard filter, unrelated to any of the above. Anyone at or
    above WAIT_THRESHOLD to survive to `next_after` is dropped, and anyone below
    `min_availability` to even reach `pick` is dropped.

    Work order 2026-08-29c item 3: `missing_both_markets` is ALSO a hard filter here --
    a player with no ADP from either source is excluded from suggestions and routes
    entirely (both are built on top of this one function), not merely penalized. He
    still appears on the full board, flagged for missing market data, display-only.

    `shape` (`cap - held` per position) does double duty: the pre-existing hard
    filter below (never suggest a position already at its max roster cap) and, as of
    work order 2026-08-29c items 1+2, `need_discount`'s roster-state input -- `shape`
    was previously threaded through and read ONLY for the cap filter, which is why a
    QB or TE already holding its starting slot kept ranking as if nothing had
    changed. See `need_discount`'s own docstring.

    Vectorized (work order 2026-08-24b item 3): the two `survival_between` calls per
    row -- ~430 rows x 2 scipy calls, invoked once per route plus once per leg (~10
    times a render) -- were the dominant cost of a route rebuild. Both are now one
    scipy call each over the WHOLE pool (`_survival_between_vectorized`, curve-fit
    shared between the two calls), with only the small SURVIVING subset ever touched
    row-by-row again (intel-window parsing, attaching each match's full board row for
    `build_routes`' anchor path). Equivalence with the original per-row loop is
    covered by tests/test_core.py.
    """
    if pool.empty:
        return pd.DataFrame(columns=["player", "position", "on_list", "priority", "edge"])

    fit = de._lognormal_fit_arrays(pool)
    avail_arr = np.ones(len(pool)) if from_pick == pick else _survival_between_vectorized(pool, from_pick, pick, fit)
    wait_arr = _survival_between_vectorized(pool, pick, next_after, fit)

    positions = pool["position"].to_numpy()
    intel_tag = pool["intel_tag"] if "intel_tag" in pool.columns else pd.Series(np.nan, index=pool.index)
    shape_ok = np.array([shape.get(p, 0) > 0 for p in positions])
    no_market = pool["reference_adp_rank"].isna().to_numpy() & pool["comparison_adp_value"].isna().to_numpy()
    mask = (
        ~pool["player"].isin(used).to_numpy()
        & shape_ok
        & (intel_tag != "hard_avoid").to_numpy()
        & ~no_market
        & (avail_arr >= min_availability)
        & ((wait_arr < WAIT_THRESHOLD) | ignore_wait)
    )
    if not mask.any():
        return pd.DataFrame(columns=["player", "position", "on_list", "priority", "edge"])

    sub = pool[mask]
    sub_intel_tag = intel_tag[mask]
    sub_intel_priority = sub["intel_priority"] if "intel_priority" in sub.columns else pd.Series(np.nan, index=sub.index)
    in_window = sub.apply(lambda r: pick in _intel_windows(r), axis=1)
    on_list = (sub_intel_tag == "target") & in_window
    priority = np.where(in_window & sub_intel_priority.notna(), sub_intel_priority.astype(float), 99.0)
    vorp = sub.get("vorp", pd.Series(0.0, index=sub.index)).fillna(0.0)
    edge_col = sub["edge"] if "edge" in sub.columns else pd.Series(np.nan, index=sub.index)
    edge = edge_col.where(edge_col.notna(), vorp).to_numpy()

    # Work order 2026-08-29 item 1 (R37): position-adjusted reach penalty, vectorized
    # (no scipy, just a per-position dict lookup + arithmetic -- cheap enough to keep
    # candidates_for_pick's item-3 performance work intact). Folded straight into the
    # tier used for the primary sort, not kept as a separate tiebreak field the way the
    # old `sharp_edge` was -- this is what makes it "change recommendations" rather
    # than just annotate them.
    sub_positions = sub["position"].to_numpy()
    nffc = sub.get("comparison_adp_value", pd.Series(np.nan, index=sub.index)).to_numpy(dtype=float)
    sleeper_rank = sub.get("reference_adp_rank", pd.Series(np.nan, index=sub.index)).to_numpy(dtype=float)
    offsets_map = _position_offsets()
    offset_arr = np.array([offsets_map.get(p, 0.0) for p in sub_positions])
    has_nffc = ~np.isnan(nffc) & ~np.isnan(sleeper_rank)
    reach_gap = np.where(has_nffc, nffc - sleeper_rank - offset_arr, np.nan)
    penalty = np.where(has_nffc & (reach_gap > 0), np.minimum(reach_gap * REACH_PENALTY_WEIGHT, REACH_PENALTY_CAP), 0.0)

    # Work order 2026-08-29c items 1+2: `shape` is `cap - held` for every position
    # (build_routes' own construction, unchanged by this fix), so `cap - shape[pos]`
    # recovers `held` -- the owner's real roster count on the first call of a route,
    # and the hypothetical count including this route's own already-chosen legs on
    # every call after (build_route decrements `shape` after each leg). No new
    # parameter needed; this is the same `shape` the old cap-only `shape_ok` filter
    # above already reads, just also read for what it actually holds.
    cap_map = config.ROSTER_TARGET
    held_arr = np.array([cap_map.get(p, 0) - shape.get(p, cap_map.get(p, 0)) for p in sub_positions])
    edge_expectation = pool.attrs.get("edge_expectation_at_target", {})
    need_disc = np.array([
        need_discount(p, {p: h}, edge_expectation) for p, h in zip(sub_positions, held_arr)
    ])
    # Work order 2026-08-29c item 6: pace urgency, subtracted from need_discount (a
    # BONUS, not a separate signed term) so a position already fully need-weighted
    # can still gain extra priority once the owner is visibly behind his own plan.
    current_round = config.round_of_pick(pick)
    pace_urg = np.array([pace_urgency(p, h, current_round) for p, h in zip(sub_positions, held_arr)])
    net_discount = need_disc - pace_urg

    out = pd.DataFrame({
        "player": sub["player"].to_numpy(),
        "position": sub["position"].to_numpy(),
        "on_list": on_list.to_numpy(),
        "priority": priority,
        "composite_score": sub["composite_score"].astype(float).to_numpy(),
        "edge": edge,
        "vorp": vorp.to_numpy(),
        "market_reach_gap": reach_gap,
        "reach_penalty": penalty,
        "need_discount": need_disc,
        "pace_urgency": pace_urg,
        "availability": avail_arr[mask],
        "wait": wait_arr[mask],
        "row": [sub.iloc[i] for i in range(len(sub))],
    })
    # Coarser than `edge - reach_penalty - need_discount + pace_urgency` itself: two
    # candidates within one band are "near-equal" on value, so intel breaks the tie;
    # a wider gap is decided on (penalized, need-discounted, pace-boosted) edge alone.
    out["_edge_tier"] = np.floor(
        (out["edge"] - out["reach_penalty"] - net_discount) / EDGE_TIE_BAND_POINTS
    )
    return out.sort_values(
        by=["_edge_tier", "on_list", "priority", "composite_score"],
        ascending=[False, False, True, False],
    ).drop(columns=["_edge_tier"]).reset_index(drop=True)


def leg_survival_quantiles(row: pd.Series, fallback: float) -> tuple[float, float]:
    """Work order 2026-08-29c item 8 (R44): (pessimistic, optimistic) survival for a
    roadmap leg. Pessimistic = the Monte Carlo point estimate (`survival_probability`)
    minus its own margin (`survival_band`, the 95% CI half-width `availability_with_
    band` already computes) -- "assume the value gets taken." The point estimate
    itself is kept as the optimistic figure, not thrown away -- "a roadmap that is
    always worst-case will read as noise within two rounds." Falls back to
    `fallback` (the single lognormal-curve estimate `_survival_between_vectorized`
    already computes) for BOTH when the Monte Carlo band isn't on this row at all
    (e.g. build_routes called on a bare compute_composite board, no availability_
    with_band pass)."""
    if "survival_probability" in row.index and pd.notna(row.get("survival_probability")):
        optimistic = float(row["survival_probability"])
        has_band = "survival_band" in row.index and pd.notna(row.get("survival_band"))
        band = float(row["survival_band"]) if has_band else 0.0
        pessimistic = float(np.clip(optimistic - band, 0.0, 1.0))
        return pessimistic, optimistic
    return float(fallback), float(fallback)


def build_route(anchor: pd.Series, remaining: dict, pool: pd.DataFrame, schedule: list[int], this_pick: int) -> dict:
    """One whole path: the anchor plus a pick at each of the next `ROUTE_LEGS` turns.

    Plan gating comes straight from the strategy docs. One quarterback until the very
    late rounds, because QB2 is the designated sacrifice. The second tight end fills
    from 101. Running back darts open at the back of the draft.
    """
    picks = owner_pick_numbers()
    shape = dict(remaining)
    cap_map = config.ROSTER_TARGET
    edge_expectation = pool.attrs.get("edge_expectation_at_target", {})

    def _held(pos: str) -> int:
        return cap_map.get(pos, 0) - shape.get(pos, cap_map.get(pos, 0))

    # Work order 2026-08-29c item 4 (R42): `edge_sum`/`need_discount_sum`, captured
    # per member AT THE ROSTER STATE THAT WAS ACTUALLY ON THE CLOCK when each pick was
    # chosen -- the anchor's own values recomputed here (candidates_for_pick's `_edge_
    # tier` used the identical `remaining`/`edge_expectation` when IT was selected as
    # an anchor in build_routes), each leg's read straight off `cands` rather than
    # recomputed after the fact, since `chosen`'s roster state (`shape`, decremented
    # after every earlier leg) keeps moving as the route unfolds.
    anchor_edge_col = anchor.get("edge")
    anchor_edge = float(anchor_edge_col) if pd.notna(anchor_edge_col) else float(anchor.get("vorp") or 0.0)
    anchor_reach_penalty = reach_penalty(anchor)
    anchor_need_discount = need_discount(
        anchor["position"], {anchor["position"]: _held(anchor["position"])}, edge_expectation
    )
    # Work order 2026-08-29c item 6: same pace-urgency bonus candidates_for_pick's own
    # `_edge_tier` applies, at the round THIS anchor pick actually falls in.
    anchor_pace_urgency = pace_urgency(
        anchor["position"], _held(anchor["position"]), config.round_of_pick(this_pick)
    )

    shape[anchor["position"]] = shape.get(anchor["position"], 0) - 1
    used = {anchor["player"]}
    taken = {"QB": 1 if anchor["position"] == "QB" else 0, "TE": 1 if anchor["position"] == "TE" else 0}
    legs = []
    edge_sum = anchor_edge
    reach_penalty_sum = anchor_reach_penalty
    need_discount_sum = anchor_need_discount
    pace_urgency_sum = anchor_pace_urgency
    for pk in schedule:
        after = next((p for p in picks if p > pk), pk + config.N_TEAMS)
        cands = candidates_for_pick(pool, pk, this_pick, after, shape, used)
        chosen = None
        for _, c in cands.iterrows():
            if c["position"] == "QB" and taken["QB"] >= 1 and pk < 116:
                continue
            if c["position"] == "TE" and taken["TE"] >= 1 and pk < 101:
                continue
            chosen = c
            break
        if chosen is None:
            break
        pess, opt = leg_survival_quantiles(chosen["row"], chosen["availability"])
        legs.append({
            "pick": pk, "player": chosen["player"], "position": chosen["position"],
            "odds": chosen["availability"], "odds_pessimistic": pess, "odds_optimistic": opt,
        })
        used.add(chosen["player"])
        edge_sum += float(chosen["edge"])
        reach_penalty_sum += float(chosen["reach_penalty"])
        need_discount_sum += float(chosen["need_discount"])
        pace_urgency_sum += float(chosen["pace_urgency"])
        shape[chosen["position"]] -= 1
        taken[chosen["position"]] = taken.get(chosen["position"], 0) + 1
    members = [anchor] + [pool.loc[pool["player"] == l["player"]].iloc[0] for l in legs]
    return {
        "anchor": anchor,
        "legs": legs,
        "shape_left": shape,
        "vorp_sum": float(sum(float(m.get("vorp") or 0.0) for m in members)),
        "composite_sum": float(sum(float(m["composite_score"]) for m in members)),
        # Work order 2026-08-29 item 1 (R37): a COST, not a bonus -- replaces the old
        # sharp_sum (which rewarded a positive un-adjusted rank divergence).
        "reach_penalty_sum": float(reach_penalty_sum),
        # Work order 2026-08-29c item 4 (R42): the SAME edge-net-of-reach-penalty-net-
        # of-need-discount quantity candidates_for_pick's `_edge_tier` sorts anchors on,
        # summed across the whole route -- this is what makes build_routes' own sort
        # (below) "consistent with the anchor sort" instead of a disconnected key.
        "edge_sum": float(edge_sum),
        "need_discount_sum": float(need_discount_sum),
        # Work order 2026-08-29c item 6: pace urgency, summed the same way -- a bonus,
        # so build_routes' sort key ADDS this rather than subtracting it.
        "pace_urgency_sum": float(pace_urgency_sum),
    }


def build_routes(board: pd.DataFrame, roster_counts: dict, this_pick: int, on_clock: bool, n_routes: int = 3) -> list[dict]:
    """Up to three whole paths, recomputed every pick."""
    picks = owner_pick_numbers()
    survives_to = this_pick if on_clock else this_pick
    next_after = next((p for p in picks if p > this_pick), this_pick + config.N_TEAMS)
    remaining = {pos: cap - roster_counts.get(pos, 0) for pos, cap in config.ROSTER_TARGET.items()}
    schedule = [p for p in picks if p > this_pick][:ROUTE_LEGS]
    anchors = candidates_for_pick(board, this_pick, this_pick, next_after, remaining, set(), min_availability=0.0)
    # Urgent fix, 2026-08-30: a short gap to the owner's next turn (e.g. slot 2's
    # even-to-odd 3-pick wrap) puts every real candidate's wait odds above
    # WAIT_THRESHOLD -- nothing is "urgent" over a 3-pick gap, so the normal filter
    # correctly empties out, but build_routes must never return zero cards. Falls
    # back to the SAME candidates, wait filter only, ranked by the same edge-based
    # tier -- edge formula and WAIT_THRESHOLD itself untouched.
    short_gap = len(anchors) < n_routes
    if short_gap:
        anchors = candidates_for_pick(
            board, this_pick, this_pick, next_after, remaining, set(), min_availability=0.0, ignore_wait=True
        )
    routes = [build_route(a["row"], remaining, board, schedule, this_pick) for _, a in anchors.head(n_routes).iterrows()]
    for r in routes:
        r["short_gap"] = short_gap
    # Work order 2026-08-29c item 4 (R42): "anchors are selected by edge, then the
    # three routes are re-sorted by a key that does not contain it." Primary key is
    # now edge_sum net of reach_penalty_sum/need_discount_sum, PLUS pace_urgency_sum
    # (item 6 -- a bonus, added rather than subtracted) -- the route-level analog of
    # candidates_for_pick's own `_edge_tier` -- with composite_sum as a tiebreak only,
    # mirroring candidates_for_pick's own tier-then-composite_score order.
    routes.sort(
        key=lambda r: (
            r["edge_sum"] - r["reach_penalty_sum"] - r["need_discount_sum"] + r["pace_urgency_sum"],
            r["composite_sum"],
        ),
        reverse=True,
    )
    return routes


# ---------------------------------------------------------------------------
# Work order 2026-08-31 item B (R45): board-assumption toggle. Three states, all
# affecting AVAILABILITY ONLY -- vorp/composite_score are computed upstream in
# compute_composite and never touch reference_adp_rank or a survival curve at all;
# `edge`'s own FORMULA (vorp minus the E[max vorp at target] mechanism) is identical
# in every mode, only the survival numbers feeding that expectation move.
# ---------------------------------------------------------------------------
BOARD_ASSUMPTION_MODES = ["adp_order", "observed_reach", "pessimistic"]
BOARD_ASSUMPTION_LABELS = {
    "adp_order": "ADP order",
    "observed_reach": "Observed reach",
    "pessimistic": "Pessimistic",
}

_alt_league_deltas_cache: dict[str, float] | None = None


def _alt_league_position_deltas() -> dict[str, float]:
    """position -> median(sleeper_rank - alt_pick) from the alt-league draft.
    Positive means the alt league drafts that position EARLIER than Sleeper rank
    predicts (worse availability under "observed_reach"); negative means later
    (better) -- e.g. the owner's own measurement, QB -18.5 (goes much later, so QB
    gets BETTER), RB/WR positive (go earlier, so worse).

    Empty until data/derived/alt_league_position_deltas.csv exists, which it does not
    yet -- see config.ALT_LEAGUE_POSITION_DELTAS_PATH's own comment for why this is
    deliberately left as a reviewed build step rather than guessed at here. Empty
    means `board_assumption_modes_available` simply never offers "observed_reach",
    and `availability_with_assumption` falls back to "adp_order" if asked for it
    anyway -- the toggle degrades gracefully, it doesn't guess.

    Cached at module level, same reasoning as `_position_offsets_cache`.
    """
    global _alt_league_deltas_cache
    if _alt_league_deltas_cache is None:
        path = config.ALT_LEAGUE_POSITION_DELTAS_PATH
        if path.exists():
            df = pd.read_csv(path)
            _alt_league_deltas_cache = {row["position"]: float(row["delta"]) for _, row in df.iterrows()}
        else:
            _alt_league_deltas_cache = {}
    return _alt_league_deltas_cache


def board_assumption_modes_available() -> list[str]:
    """Which of BOARD_ASSUMPTION_MODES are actually usable right now. "observed_reach"
    only appears once `_alt_league_position_deltas` has real data -- an unpopulated
    toggle that silently no-ops is worse than one that honestly isn't offered yet
    (same principle as the layers-off / unmodelled-seats indicators)."""
    modes = ["adp_order", "pessimistic"]
    if _alt_league_position_deltas():
        modes.insert(1, "observed_reach")
    return modes


def availability_with_assumption(
    board: pd.DataFrame,
    as_of_pick: int,
    target_pick: int,
    manager_priors: pd.DataFrame,
    team_bias: pd.DataFrame,
    drafted_name_keys: set[str],
    owner_roster_by_manager: dict,
    mode: str = "adp_order",
    n_sims: int = N_SIMS_UI,
    wait_pick: int | None = None,
) -> pd.DataFrame:
    """The same `availability_with_band` pass, under one of three assumptions about
    how the FIELD drafts (work order 2026-08-31 item B / R45).

    "adp_order": unmodified -- current behavior, exactly `availability_with_band`.

    "observed_reach": shifts `reference_adp_rank` by the alt-league's measured
    per-position delta on a COPY of `board` before running the normal pass, then
    restores the ORIGINAL rank on the returned frame -- reach_penalty/
    market_reach_gap/display (which read reference_adp_rank straight off whatever
    this function returns) never see the shifted value, only the survival curve's
    own fit does, for exactly this one call. Falls back to "adp_order" when the
    alt-league deltas aren't built yet (see `_alt_league_position_deltas`).

    "pessimistic": the worst-case quantile of the SAME survival distribution --
    survival_probability minus its own 95%-CI band, the identical mechanism item 8 /
    R44 already uses for route-leg odds (`leg_survival_quantiles`), generalized here
    to the primary number instead of a route-leg-only figure.
    """
    if mode == "observed_reach":
        deltas = _alt_league_position_deltas()
        if deltas:
            shifted = board.copy()
            shift = shifted["position"].map(deltas).fillna(0.0)
            shifted["reference_adp_rank"] = shifted["reference_adp_rank"] - shift
            out = availability_with_band(
                shifted, as_of_pick, target_pick, manager_priors, team_bias,
                drafted_name_keys, owner_roster_by_manager, n_sims, wait_pick,
            )
            out["reference_adp_rank"] = board["reference_adp_rank"].to_numpy()
            return out
        mode = "adp_order"  # graceful fallback -- see board_assumption_modes_available

    out = availability_with_band(
        board, as_of_pick, target_pick, manager_priors, team_bias,
        drafted_name_keys, owner_roster_by_manager, n_sims, wait_pick,
    )
    if mode == "pessimistic":
        out["survival_probability"] = (out["survival_probability"] - out["survival_band"]).clip(0.0, 1.0)
        if "survival_probability_wait" in out.columns and "survival_band_wait" in out.columns:
            out["survival_probability_wait"] = (
                out["survival_probability_wait"] - out["survival_band_wait"]
            ).clip(0.0, 1.0)
    return out


def cost_of_waiting(board: pd.DataFrame) -> list[dict]:
    """Work order 2026-08-31 item A (R44): for each position, best-available VORP
    RIGHT NOW vs the expected best-available at the owner's own next pick, with the
    difference -- "the single most useful artifact the strategy analysis produced,"
    previously only an ad-hoc script. `board` must already be the output of an
    availability_with_band (or availability_with_assumption) call made FOR this pick
    -- `best_next` reads `board.attrs["edge_expectation_at_target"]`, the SAME E[max
    vorp at target] `edge` itself already nets out (work order 2026-08-29c item 2),
    so this is derived from the SAME availability pass as `edge` and runs no second
    simulation. Falls back to `best_now` (zero apparent cost of waiting) when that
    attrs key is missing, e.g. a bare compute_composite board with no availability
    pass run yet.

    Eligibility mirrors candidates_for_pick's own filters (hard_avoid/no_market) --
    `board` is expected to already be drafted-player-filtered (the same `available`
    frame every other cockpit panel reads), so there is no separate `used` set here.
    """
    intel_tag = board["intel_tag"] if "intel_tag" in board.columns else pd.Series(np.nan, index=board.index)
    no_market = board["reference_adp_rank"].isna().to_numpy() & board["comparison_adp_value"].isna().to_numpy()
    eligible = board[(intel_tag != "hard_avoid").to_numpy() & ~no_market]
    edge_expectation = board.attrs.get("edge_expectation_at_target", {})
    out = []
    for pos in config.POSITIONS:
        sub = eligible[eligible["position"] == pos]
        best_now = float(sub["vorp"].max()) if len(sub) else 0.0
        best_next = float(edge_expectation.get(pos, best_now))
        out.append({"position": pos, "best_now": best_now, "best_next": best_next, "vorp_lost": best_now - best_next})
    return out


# ===========================================================================
# 5. Rule timeline
# ===========================================================================
RULE_SCHEDULE = [
    ("W3", 1, "Quarterback early", "Medium"),
    ("W11", 1, "Factors and market both cold", "Medium"),
    ("W6", 2, "Tight end too early", "Medium"),
    ("W2", 3, "Third back too early", "High"),
    ("W10", 3, "Over a position target", "Medium"),
    ("W1", 5, "Back in the dead zone", "High"),
    ("W17", 6, "Your own drift at premium spots", "High"),
    ("W12", 7, "Receiver with no third back", "High"),
    ("W18", 7, "Band oversubscribed", "High"),
    ("W7", 8, "Fewer than four receivers", "High"),
    ("W16", 8, "No tight end on the miss path", "Medium"),
    ("W4", 10, "No quarterback yet", "High"),
    ("W15", 11, "Back dart too early", "Low"),
    ("W5", 12, "Only one tight end", "Medium"),
    ("W8", 12, "Receiver past the cliff", "Medium"),
    ("W14", 14, "Kicker before the run", "Medium"),
    ("W13", 15, "No second quarterback", "Low"),
]


def rule_timeline(current_round: int, active_codes: set[str]) -> list[dict]:
    """One entry per round, with the rules that start watching there and whether each
    is armed by what the roster currently holds."""
    out = []
    for rnd in range(1, config.N_ROUNDS + 1):
        rules = [
            {"code": c, "label": lab, "severity": sev, "armed": current_round >= frm, "firing": c in active_codes}
            for c, frm, lab, sev in RULE_SCHEDULE
            if frm == rnd
        ]
        out.append({"round": rnd, "is_now": rnd == current_round, "rules": rules})
    return out


# ===========================================================================
# 6. Context strip (handoff Section 3.2 band 1) -- who's on the clock, the intervening
#    managers' tells, and what's firing/arming. Missing entirely from the first cut of
#    main_cockpit.py; this is what work order 2026-08-24 item 6 / R34 closes.
# ===========================================================================
def manager_tell(row: pd.Series) -> str:
    """One computed line per manager, not a hand-transcribed narrative -- derived from
    manager_priors.csv (already computed from real draft history, spec R14) and the
    reach model's own MANAGER_MEAN_REACH (work order item 3 / R31), so it can never
    drift out of sync with the numbers the model actually uses."""
    te_mean, qb_mean = row.get("te1_round_mean"), row.get("qb1_round_mean")
    reach = config.MANAGER_MEAN_REACH.get(row.get("manager"), 0.0)
    if pd.notna(te_mean) and te_mean <= 6:
        return f"TE early, rd {te_mean:.0f}"
    if pd.notna(qb_mean) and qb_mean <= 5:
        return f"QB early, rd {qb_mean:.0f}"
    if reach >= 3:
        return f"reaches, +{reach:.1f}"
    if reach <= -3:
        return f"patient, {reach:.1f}"
    return "no strong lean"


def intervening_chips(intervening: list[tuple[int, int, str]], manager_priors: pd.DataFrame) -> list[dict]:
    """[{'name', 'tell'}, ...] for the context strip's window cell. A manager who picks
    twice in the same window (the fixed-window property, spec Section 3) is marked
    'again' on the repeat rather than duplicating the tell -- handoff Section 3.2."""
    priors_by_manager = manager_priors.set_index("manager") if len(manager_priors) else manager_priors
    seen: set[str] = set()
    chips = []
    for _, __, manager in intervening:
        if manager in seen:
            chips.append({"name": manager, "tell": "again"})
            continue
        seen.add(manager)
        tell = "no strong lean"
        if len(manager_priors) and manager in priors_by_manager.index:
            row = priors_by_manager.loc[manager]
            row = row if isinstance(row, pd.Series) else row.iloc[0]
            tell = manager_tell({**row.to_dict(), "manager": manager})
        chips.append({"name": manager, "tell": tell})
    return chips


def warn_chips(warnings: list, current_round: int, rule_schedule: list[tuple] = RULE_SCHEDULE) -> list[dict]:
    """Up to two chips: how many rules are firing right now, and how many newly arm
    next round (handoff Section 3.2's 'what fires next round' foresight, U14). Only
    High severity warnings count as 'firing' here -- U13's own rule that low/medium
    collapse to a count, not individual chips."""
    firing = [w for w in warnings if w.severity == "High"]
    arms_next = [code for code, frm, _, __ in rule_schedule if frm == current_round + 1]
    chips = []
    if firing:
        chips.append({"label": f"{len(firing)} firing now", "kind": "bad"})
    if arms_next:
        chips.append({"label": f"{len(arms_next)} arms next round", "kind": "warn"})
    if not chips:
        chips.append({"label": "nothing firing", "kind": "good"})
    return chips


def shortlist_coverage(board: pd.DataFrame, target_pick: int) -> float | None:
    """Work order 2026-08-29 item 2 (R38): P(at least one shortlist name for
    `target_pick`'s window survives to it) -- the owner's second, SEPARATE objective
    ("maximize total VORP AND maximize the probability of landing at least one name
    from each pick window's shortlist"). Shortlist = intel_tag=="target" rows whose
    intel_windows includes target_pick.

    Combines each shortlisted player's own `survival_probability` (already the
    chosen availability method's output, R36 -- montecarlo by default) under an
    independence approximation: P(>=1 survives) = 1 - prod(1 - p_i). A simplification,
    not the full joint capacity-aware treatment `edge` gets -- deliberately: this
    number is INFORMATIONAL ONLY (task's own instruction; see the "do not" list), so it
    does not carry the same bar as a ranking input. If a future need promotes it to a
    real decision input, it should get the same per-simulation joint treatment `edge`
    already has (draft_engine.compute_availability's `want_edge` mechanism), not this
    approximation.

    Returns None, not 0.0, when no shortlist exists for this window at all -- there is
    nothing to have a coverage OPINION about, which reads differently than "certain to
    miss."
    """
    if "intel_tag" not in board.columns or "intel_windows" not in board.columns:
        return None
    is_target = board["intel_tag"] == "target"
    if not is_target.any():
        return None
    in_window = board[is_target].apply(lambda r: target_pick in _intel_windows(r), axis=1)
    shortlist = board[is_target][in_window]
    if shortlist.empty:
        return None
    survival = shortlist["survival_probability"].clip(0, 1).fillna(0.0)
    return float(1.0 - (1.0 - survival).prod())


def pick_line_offsets(on_clock_pick: int, owner_next: int, rows: int = BOARD_ROWS) -> dict[int, str]:
    """Row offsets where the board should draw a 'your pick' line, the way the Sleeper
    board does. Only meaningful while the list is in Sleeper order, because that is
    when a row's position stands in for a pick number.
    """
    picks = [p for p in owner_pick_numbers() if p >= owner_next]
    out = {}
    for i, yp in enumerate(picks):
        offset = yp - on_clock_pick - i
        if offset > rows:
            break
        if offset >= 0:
            out[offset] = f"Your pick {yp}"
    return out
