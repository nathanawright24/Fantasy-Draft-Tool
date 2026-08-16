"""
Availability model, composite scoring, guardrails (W1-W18), and the roster-path
recommender -- combined deliberately, because at pick time you need availability, the
composite ranking, and the active warnings together, and one file means one place to
look when something's wrong mid-draft.

Pure functions over player_master.csv-shaped DataFrames and plain-python roster state.
No file I/O in this module except for reading the two small derived CSVs
(manager_priors.csv, team_bias.csv) that back the availability model.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import lognorm, norm, triang

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

# ---------------------------------------------------------------------------
# Bonus-model recompute (mirrors build/pipeline.py's model exactly, so the UI's
# dispersion slider can rescale sigma live without rerunning the whole data build).
# Deliberately duplicated rather than imported from build/ -- keeps app/ runnable
# without any dependency on the build layer's internals, per the spec's "app reads
# only player_master.csv and live draft state" separation.
# ---------------------------------------------------------------------------
def _prob_at_least(mean_per_game: float, threshold: float, sigma: float) -> float:
    if pd.isna(mean_per_game) or mean_per_game <= 0 or sigma <= 0:
        return 0.0
    mu = math.log(mean_per_game) - 0.5 * sigma ** 2
    return float(lognorm.sf(threshold, s=sigma, scale=math.exp(mu)))


def recompute_bonus_est_ppr(df: pd.DataFrame, dispersion_multiplier: float = 1.0) -> pd.Series:
    """Dialling dispersion_multiplier to 0 must visibly change rankings (spec Section 6.2) --
    it zeroes bonus_est_ppr outright, which it does here exactly."""
    out = pd.Series(0.0, index=df.index)
    if dispersion_multiplier <= 0:
        return out
    for pos in config.POSITIONS:
        mask = df["position"] == pos
        if not mask.any():
            continue
        games_eff = (df.loc[mask, "games_projected"] - config.GAMES_HAIRCUT[pos]).clip(lower=0)
        sigma_scrim = config.BONUS_SIGMA_SCRIMMAGE[pos] * dispersion_multiplier
        p_scrim = df.loc[mask, "scrimmage_yds_per_game"].apply(
            lambda m: _prob_at_least(m, config.SCRIMMAGE_BONUS_THRESHOLD, sigma_scrim)
        )
        pts = games_eff * p_scrim * config.BONUS_POINTS_PER_GAME
        if pos == "QB":
            sigma_pass = config.BONUS_SIGMA_PASSING["QB"] * dispersion_multiplier
            p_pass = df.loc[mask, "pass_yds_per_game"].apply(
                lambda m: _prob_at_least(m, config.PASSING_BONUS_THRESHOLD, sigma_pass)
            )
            pts = pts + games_eff * p_pass * config.BONUS_POINTS_PER_GAME
        out.loc[mask] = pts
    return out


# ---------------------------------------------------------------------------
# Composite scoring (spec Section 6.5)
# ---------------------------------------------------------------------------
_COMPONENT_LAYER = {"base": "implied_props", "bonus": "bonus_model", "factor": "factor_grids"}
# "market" has no available/applies pair in LAYERS (spec Section 4.2 doesn't define one) --
# it's controlled purely by its own COMPOSITE_WEIGHTS entry.


def _weighted_row_average(df: pd.DataFrame, col_weights: dict[str, float]) -> np.ndarray:
    """Weighted average where a NaN component is excluded from THAT ROW's denominator --
    distinct from a whole layer being off (which is excluded from every row's numerator
    and denominator via a zero weight passed in)."""
    values = np.zeros(len(df))
    weight_sum = np.zeros(len(df))
    for col, w in col_weights.items():
        if w <= 0:
            continue
        v = df[col].to_numpy(dtype=float)
        mask = ~np.isnan(v)
        values[mask] += v[mask] * w
        weight_sum[mask] += w
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(weight_sum > 0, values / weight_sum, np.nan)


def compute_composite(
    master: pd.DataFrame,
    weights: dict | None = None,
    dispersion_multiplier: float = 1.0,
    injury_weight: float | None = None,
) -> pd.DataFrame:
    """Adds base_norm/bonus_norm/factor_norm/market_norm (0-100, within-position) and
    composite_score (their renormalized weighted average) to a copy of `master`.

    Renormalization happens at two levels, deliberately kept separate:
      - Layer-level (spec Section 4.2): a disabled layer's weight is excluded from
        every row via `enabled_weights`, computed once before the loop.
      - Row-level: a player missing a component (e.g. no ADP match, no factor grade)
        has that component excluded from just their own weighted average, never
        imputed and never silently zeroed into the average.
    """
    df = master.copy()
    weights = dict(weights if weights is not None else config.COMPOSITE_WEIGHTS)
    injury_weight = config.INJURY_WEIGHT if injury_weight is None else injury_weight

    enabled_weights = {
        k: w for k, w in weights.items() if config.layer_on(_COMPONENT_LAYER.get(k, "__always_on__")) or k not in _COMPONENT_LAYER
    }
    # layer_on() only knows real LAYERS keys; "market" isn't one, so the `or` above keeps it.
    disabled = sorted(set(weights) - set(enabled_weights))

    if dispersion_multiplier != 1.0 and config.layer_on("bonus_model"):
        df["bonus_est_ppr"] = recompute_bonus_est_ppr(df, dispersion_multiplier)

    df["base_norm"] = df.groupby("position")["ppr_base"].rank(pct=True) * 100
    df["bonus_norm"] = df.groupby("position")["bonus_est_ppr"].rank(pct=True) * 100

    risk_adj = pd.Series(0.0, index=df.index)
    has_boom_bust = df["p_boomed"].notna() & df["p_busted"].notna()
    risk_adj.loc[has_boom_bust] = 20 * (df.loc[has_boom_bust, "p_boomed"] - df.loc[has_boom_bust, "p_busted"])
    has_injury = df["p_got_injured"].notna()
    injury_scale = injury_weight / config.INJURY_WEIGHT  # 1.0 at the default weight
    risk_adj.loc[has_injury] -= 30 * injury_scale * df.loc[has_injury, "p_got_injured"]

    df["factor_norm"] = np.nan
    has_factor = df["factor_score_normalized"].notna()
    df.loc[has_factor, "factor_norm"] = (
        df.loc[has_factor, "factor_score_normalized"] * 100 + risk_adj.loc[has_factor]
    ).clip(0, 100)
    # TE/QB have no archetype back-test (p_* deliberately blank) -- risk_adj is 0 for them
    # automatically via the has_boom_bust/has_injury masks, so factor_norm there is pure
    # factor_score_normalized, never imputed by analogy to WR/RB.

    df["market_norm"] = df.groupby("position")["reference_adp_rank"].rank(pct=True, ascending=False) * 100

    df["composite_score"] = _weighted_row_average(
        df,
        {
            "base_norm": enabled_weights.get("base", 0),
            "bonus_norm": enabled_weights.get("bonus", 0),
            "factor_norm": enabled_weights.get("factor", 0),
            "market_norm": enabled_weights.get("market", 0),
        },
    )
    df.attrs["disabled_composite_layers"] = disabled
    df.attrs["enabled_composite_weights"] = enabled_weights
    return df


# ---------------------------------------------------------------------------
# Availability model (spec Section 7)
# ---------------------------------------------------------------------------
def _triangular_survival(adp_min, adp_mode, adp_max, target_pick) -> float | None:
    if pd.isna(adp_min) or pd.isna(adp_max) or adp_max <= adp_min:
        return None
    mode = min(max(adp_mode, adp_min), adp_max)
    c = (mode - adp_min) / (adp_max - adp_min)
    return float(triang.sf(target_pick, c=c, loc=adp_min, scale=adp_max - adp_min))


def _generic_survival(adp_rank, target_pick, spread_picks=24) -> float:
    """The widened-variance fallback (spec Section 4.2's mandated 'built alongside the
    primary path, not after'). Used whenever NFFC's own min/max is unusable for a player
    AND whenever manager_priors doesn't apply at all."""
    if pd.isna(adp_rank):
        return 0.5
    return float(norm.sf(target_pick, loc=adp_rank, scale=spread_picks))


def _confidence_widen(row: pd.Series) -> float:
    widen = {"high": 1.0, "medium": 1.3, "low": 1.6}.get(row.get("confidence", "medium"), 1.3)
    if row.get("bimodal_flag"):
        widen *= 1.4
    return widen


def _league_average_rates(manager_priors: pd.DataFrame, owner: str) -> dict:
    """League-average positional appetite, excluding the owner (spec: 'exclude Nathan's
    own priors, he is not competing with himself') -- used as the denominator that each
    intervening manager's own rate is compared against."""
    others = manager_priors[manager_priors["manager"] != owner]
    return {
        "qb1_round_mean": others["qb1_round_mean"].mean(),
        "te1_round_mean": others["te1_round_mean"].mean(),
        "rb_in_r1_8_mean": others["rb_in_r1_8_mean"].mean(),
        "wr_in_r1_8_mean": others["wr_in_r1_8_mean"].mean(),
        "k_round_mean": others["k_round_mean"].mean(),
    }


def _position_pick_rate(manager_row: pd.Series, position: str, round_num: int, has_position_already: bool) -> float:
    """P(this manager takes a player of `position` on a pick landing in `round_num`).

    A first-order heuristic, not a full simulator: QB/TE use a normal curve around each
    manager's own historical QB1/TE1 round (widened for low n / bimodal managers by the
    caller); RB/WR use their R1-8 count as a flat per-round rate, halved past round 8
    since the source priors table only covers rounds 1-8 and a flat continuation is the
    most defensible extrapolation available. Documented here so a mid-draft number is
    traceable back to a specific, inspectable assumption.
    """
    if position == "QB":
        mean, std = manager_row.get("qb1_round_mean"), manager_row.get("qb1_round_std")
        if pd.isna(mean):
            return 0.06 if has_position_already else 0.15
        std = std if pd.notna(std) and std > 0.5 else 2.5
        if has_position_already:
            return 0.07  # QB2 -- flat, low-signal residual
        p = norm.cdf(round_num + 0.5, loc=mean, scale=std) - norm.cdf(round_num - 0.5, loc=mean, scale=std)
        return float(max(p, 0.02))
    if position == "TE":
        mean, std = manager_row.get("te1_round_mean"), manager_row.get("te1_round_std")
        if pd.isna(mean):
            return 0.05 if has_position_already else 0.12
        std = std if pd.notna(std) and std > 0.5 else 2.5
        if has_position_already:
            return 0.06
        p = norm.cdf(round_num + 0.5, loc=mean, scale=std) - norm.cdf(round_num - 0.5, loc=mean, scale=std)
        return float(max(p, 0.02))
    if position in ("RB", "WR"):
        per8 = manager_row.get(f"{position.lower()}_in_r1_8_mean")
        base = (per8 / 8.0) if pd.notna(per8) else 0.30
        return float(base if round_num <= 8 else base * 0.5)
    if position == "K":
        mean = manager_row.get("k_round_mean")
        return 0.55 if pd.notna(mean) and round_num >= mean - 1 else 0.03
    return 0.10


def compute_availability(
    board: pd.DataFrame,
    as_of_pick: int,
    target_pick: int,
    manager_priors: pd.DataFrame,
    team_bias: pd.DataFrame,
    owner_roster_by_manager: dict[str, dict[str, int]] | None = None,
    owner: str = config.OWNER,
    draft_order: list[str] = config.DRAFT_ORDER_2026,
) -> pd.DataFrame:
    """Adds `survival_probability` and `availability_used_fallback` to a copy of `board`.

    `as_of_pick` is the last pick actually made in the live draft (0 if none yet).
    `target_pick` is the pick we want survival probability AT -- normally the owner's
    upcoming pick. These are kept as two separate arguments deliberately: collapsing
    them into one "current pick" parameter conflates "am I on the clock right now"
    (as_of_pick == target_pick - 1, zero intervening managers, survival trivially high
    for anyone left) with "will this survive to my next turn" (as_of_pick is well
    before target_pick, many intervening managers) -- two different questions that a
    single parameter would silently answer identically.

    Baseline (always computed): a triangular/PERT survival curve from NFFC's own
    adp_min/adp_value/adp_max (spec Section 4.4), or the widened generic curve when
    those are unusable for a player. This baseline IS the fallback path -- when
    manager_priors doesn't apply, this function returns it untouched, which is what
    makes the fallback "built alongside the primary path" rather than a separate branch.

    Primary refinement (only when manager_priors applies): each intervening manager's
    position-taking rate at their upcoming round is compared against the league-average
    rate (excluding the owner) for that position/round, turned into a hazard multiplier,
    and applied as `survival ** hazard` -- which stays in [0, 1] automatically and
    collapses exactly to the baseline when every manager's rate equals the league
    average (hazard == 1).
    """
    df = board.copy()
    owner_roster_by_manager = owner_roster_by_manager or {}

    baseline = []
    used_fallback = []
    for _, row in df.iterrows():
        surv = None
        if row.get("comparison_adp_usable") and pd.notna(row.get("comparison_adp_min")):
            surv = _triangular_survival(row.get("comparison_adp_min"), row.get("comparison_adp_value"), row.get("comparison_adp_max"), target_pick)
        if surv is None:
            surv = _generic_survival(row.get("reference_adp_rank"), target_pick)
            used_fallback.append(True)
        else:
            used_fallback.append(False)
        baseline.append(surv)
    df["survival_baseline"] = baseline
    df["availability_used_fallback"] = used_fallback

    if not config.layer_on("manager_priors"):
        df["survival_probability"] = df["survival_baseline"]
        df["availability_used_fallback"] = True
        return df

    managers = config.managers_in_range(as_of_pick, target_pick, draft_order)
    if not managers:
        df["survival_probability"] = 1.0
        return df

    # The "league average" manager (excluding the owner) is a synthetic prior row built
    # from _league_average_rates, run through the exact same _position_pick_rate() used
    # for real managers -- so "average appetite" is defined by the identical formula
    # rather than a separately hand-picked constant per position.
    league_avg_row = pd.Series(_league_average_rates(manager_priors, owner))
    priors_by_manager = manager_priors.set_index("manager")

    # One hazard multiplier per (manager, position) pair -- reused across every player
    # of that position, since the manager-level term doesn't depend on the individual
    # player (team/college bias is applied per-player below).
    hazards_by_position: dict[str, list[float]] = {pos: [] for pos in config.POSITIONS + ["K"]}
    for i, manager in enumerate(managers):
        pick_num = as_of_pick + i + 1
        round_num = config.round_of_pick(pick_num)
        if manager not in priors_by_manager.index:
            continue
        mrow = priors_by_manager.loc[manager]
        widen = _confidence_widen(mrow)
        for pos in config.POSITIONS + ["K"]:
            has_already = owner_roster_by_manager.get(manager, {}).get(pos, 0) > 0
            manager_rate = _position_pick_rate(mrow, pos, round_num, has_already)
            league_rate = _position_pick_rate(league_avg_row, pos, round_num, has_position_already=False)
            ratio = manager_rate / league_rate if league_rate > 0 else 1.0
            dampened = 1.0 + (ratio - 1.0) / widen
            hazards_by_position[pos].append((manager, dampened))

    def _player_hazard(row) -> float:
        pos = row["position"]
        hazard = 1.0
        for manager, base_hazard in hazards_by_position.get(pos, []):
            h = base_hazard
            if config.layer_on("nfl_team_bias") and pd.notna(row.get("nfl_team")):
                tb = team_bias[(team_bias["manager"] == manager) & (team_bias["nfl_team"] == row["nfl_team"])]
                if len(tb):
                    ratio = float(tb.iloc[0]["bias_ratio"])
                    ratio = min(max(ratio, config.NFL_TEAM_BIAS_CAP), 1.0 / config.NFL_TEAM_BIAS_CAP)
                    h *= ratio
            if config.layer_on("college_bias") and pd.notna(row.get("college")):
                affinity = config.MANAGER_COLLEGE_AFFINITY.get(manager, [])
                if row["college"] in affinity:
                    h /= config.COLLEGE_BIAS_DISCOUNT
            hazard *= h
        return hazard

    df["hazard_exponent"] = df.apply(_player_hazard, axis=1)
    df["survival_probability"] = (
        df["survival_baseline"].clip(1e-6, 1 - 1e-6) ** df["hazard_exponent"]
    ).clip(0.005, 0.995)
    # Floor/ceiling, not just [0, 1]: a literal 0.0% reads as "impossible" in the UI, and
    # spec Section 5 rule #11 makes the same point about bust rates -- never render false
    # certainty in either direction, even when the model's point estimate is extreme.
    return df


def blend_with_live_pace(prior_rate: float, observed_rate: float, n_observed_picks: int) -> float:
    """Re-estimation from live picks (R8): after PACE_REESTIMATE_AFTER_PICKS, observed
    pace should start outweighing the static historical prior."""
    if n_observed_picks <= 0:
        return prior_rate
    weight = min(n_observed_picks / config.PACE_REESTIMATE_AFTER_PICKS, 1.0) * 0.7
    return prior_rate * (1 - weight) + observed_rate * weight


# ---------------------------------------------------------------------------
# Guardrails: W1-W18 (GUARDRAILS Section 4 + spec Section 8 amendments)
# ---------------------------------------------------------------------------
@dataclass
class Warning_:
    code: str
    message: str
    severity: str  # "Low" | "Medium" | "High"


@dataclass
class RosterState:
    picks: list[dict] = field(default_factory=list)  # [{player, position, round, overall}]
    current_round: int = 1
    current_overall_pick: int = 1
    qb_pace_picks_before_round7: int = 0  # observed league-wide QB picks before round 7, for W3/W4 pace-driven firing

    def count(self, position: str) -> int:
        return sum(1 for p in self.picks if p["position"] == position)

    def rounds_with(self, position: str) -> list[int]:
        return [p["round"] for p in self.picks if p["position"] == position]


def _dormant_if(layer: str):
    def decorator(fn):
        fn._layer_dep = layer
        return fn
    return decorator


def _w1(roster: RosterState) -> Warning_ | None:
    last = roster.picks[-1] if roster.picks else None
    if last and last["position"] == "RB" and last["round"] in (5, 6):
        return Warning_("W1", "RB in the Rd 5-6 deadzone -- every WR here out-projects every RB in it.", "High")
    return None


def _w2(roster: RosterState) -> Warning_ | None:
    if roster.count("RB") >= 3 and any(p["round"] < 7 for p in roster.picks if p["position"] == "RB"):
        rb_rounds = roster.rounds_with("RB")
        if len(rb_rounds) == 3 and max(rb_rounds) < 7:
            return Warning_("W2", "3rd RB taken before Rd 7 -- catching up at WR/QB/TE now.", "High")
    return None


def _w3(roster: RosterState) -> Warning_ | None:
    """Pace-driven (spec Section 8 amendment): fires only if live QB pace is running hot."""
    last = roster.picks[-1] if roster.picks else None
    if last and last["position"] == "QB" and last["round"] < 7 and roster.qb_pace_picks_before_round7 < 5:
        return Warning_("W3", "QB before Rd 7 -- informational only; live pace doesn't (yet) justify urgency.", "Medium")
    return None


def _w4(roster: RosterState) -> Warning_ | None:
    trigger_round = 10 if roster.qb_pace_picks_before_round7 < 6 else 9  # may fire earlier if pace runs hot
    if roster.current_round >= trigger_round and roster.count("QB") == 0:
        return Warning_("W4", "No QB rostered entering the window close -- QB1 plan was Rd 7-9.", "High")
    return None


def _w5(roster: RosterState) -> Warning_ | None:
    if roster.current_round >= 12 and roster.count("TE") == 1:
        return Warning_("W5", "Entering Rd 12 with only 1 TE -- TE2 window (Rd 10-11) is closing.", "Medium")
    return None


def _w6(roster: RosterState) -> Warning_ | None:
    last = roster.picks[-1] if roster.picks else None
    if last and last["position"] == "TE" and last["round"] in (2, 3, 4):
        return Warning_("W6", "TE in Rd 2-4 -- elite TE gains the least of any group from the new scoring.", "Medium")
    return None


def _w7(roster: RosterState) -> Warning_ | None:
    if roster.current_round >= 8 and roster.count("WR") < 4:
        return Warning_("W7", "Fewer than 4 WR entering Rd 8 -- two flex spots need WR bodies.", "High")
    return None


# W8 (WR cliff) and W9 (handcuff) need the specific player just picked, not just roster
# counts -- they're evaluated per-player in evaluate_w8()/evaluate_w9(), called from
# evaluate_pick() below, rather than living in the roster-only rule list.


def _w10(roster: RosterState) -> Warning_ | None:
    for pos, cap in config.ROSTER_TARGET.items():
        if roster.count(pos) > cap:
            displaced = _name_displaced_slot(roster, pos)
            return Warning_("W10", f"{pos} count exceeds target ({cap}) -- displaces {displaced}.", "Medium")
    return None


def _name_displaced_slot(roster: RosterState, overfull_pos: str) -> str:
    under = [p for p, cap in config.ROSTER_TARGET.items() if roster.count(p) < cap and p != overfull_pos]
    return under[0] if under else "bench depth"


def _w13(roster: RosterState) -> Warning_ | None:
    if roster.current_round >= 15 and roster.count("QB") < 2:
        return Warning_("W13", "No QB2 entering Rd 15 -- last realistic window (contested from Rd 11).", "Low")
    return None


def _w14(roster: RosterState) -> Warning_ | None:
    last = roster.picks[-1] if roster.picks else None
    if last and last["position"] == "K" and last["round"] < 14:
        return Warning_("W14", "Kicker before Rd 14 -- the run doesn't start until Rd 14+.", "Medium")
    return None


def _w15(roster: RosterState) -> Warning_ | None:
    rb_rounds = sorted(roster.rounds_with("RB"))
    darts = [r for r in rb_rounds[2:]] if len(rb_rounds) > 2 else []
    early_darts = [r for r in darts if r < 11]
    if early_darts:
        return Warning_("W15", f"RB dart in Rd {early_darts[-1]} before Rd 11 -- competes with TE2/WR6.", "Low")
    if len(darts) > 3:
        return Warning_("W15", "More than 3 late RB darts -- hard ceiling per plan is 3.", "Low")
    return None


def _w16(roster: RosterState, laporta_taken_at_pick53: bool) -> Warning_ | None:
    if not laporta_taken_at_pick53 and roster.current_round >= 8 and roster.count("TE") == 0:
        return Warning_("W16", "LaPorta-miss branch, no TE1 entering Rd 8 -- Kincaid tier is the plan.", "Medium")
    return None


def _w17(roster: RosterState, owner_drift: pd.DataFrame) -> list[Warning_]:
    """Drift check (spec Section 8): compares live QB1/TE1 timing against the OWNER's own
    computed historical rounds (data/derived/owner_drift.csv), not a hardcoded '4-5-9-10'."""
    warnings = []
    if owner_drift is None or owner_drift.empty:
        return warnings
    last_known_qb1 = owner_drift["qb1_round"].dropna()
    last_known_te1 = owner_drift["te1_round"].dropna()
    if len(last_known_qb1) and roster.count("QB") == 0 and roster.current_round > last_known_qb1.iloc[-1]:
        warnings.append(
            Warning_("W17", f"QB1 tracking later than last year's {int(last_known_qb1.iloc[-1])} -- documented drift pattern.", "High")
        )
    if len(last_known_te1) and roster.count("TE") == 0 and roster.current_round > last_known_te1.iloc[-1]:
        warnings.append(
            Warning_("W17", f"TE1 tracking later than last year's {int(last_known_te1.iloc[-1])} -- documented drift pattern.", "High")
        )
    return warnings


def _w18(roster: RosterState) -> Warning_ | None:
    band_picks = [p for p in config.owner_pick_windows() if 7 <= config.round_of_pick(p["overall"]) <= 11]
    remaining_in_band = sum(1 for p in band_picks if p["overall"] >= roster.current_overall_pick)
    commitments = 0
    if roster.count("QB") == 0:
        commitments += 1
    if roster.count("RB") < 3:
        commitments += 1
    if roster.count("TE") == 0:
        commitments += 1
    if roster.count("TE") == 1:
        commitments += 1
    if commitments > remaining_in_band and 7 <= roster.current_round <= 11:
        return Warning_(
            "W18", f"Rd 7-11 commitment count ({commitments}) exceeds remaining picks in the band ({remaining_in_band}).", "High"
        )
    return None


LAYER_DEPENDENT_RULES = {
    "W11": "factor_grids",
}


def evaluate_guardrails(
    roster: RosterState,
    laporta_taken_at_pick53: bool = False,
    owner_drift: pd.DataFrame | None = None,
) -> list[Warning_]:
    warnings = []
    for fn in (_w1, _w2, _w3, _w4, _w5, _w6, _w7, _w10, _w13, _w14, _w15):
        w = fn(roster)
        if w:
            warnings.append(w)
    w16 = _w16(roster, laporta_taken_at_pick53)
    if w16:
        warnings.append(w16)
    warnings.extend(_w17(roster, owner_drift))
    w18 = _w18(roster)
    if w18:
        warnings.append(w18)
    return warnings


def evaluate_w11(player_row: pd.Series) -> Warning_ | None:
    """Player's factor_score bottom-quartile at position AND ADP-vs-score divergence
    negative (both market and analyst dislike the pick). Dormant without factor_grids."""
    if not config.layer_on("factor_grids"):
        return None
    if pd.isna(player_row.get("factor_score_normalized")) or pd.isna(player_row.get("adp_minus_score_rank")):
        return None
    if player_row["factor_score_normalized"] <= 0.25 and player_row["adp_minus_score_rank"] < 0:
        return Warning_("W11", f"{player_row['player']}: bottom-quartile factor score AND negative ADP-vs-score divergence.", "Medium")
    return None


def evaluate_w8(player_row: pd.Series, wr_cliff_adp: int = 137) -> Warning_ | None:
    if player_row["position"] == "WR" and pd.notna(player_row.get("reference_adp_rank")) and player_row["reference_adp_rank"] > wr_cliff_adp:
        return Warning_("W8", f"{player_row['player']}: past the WR cliff (~ADP {wr_cliff_adp}).", "Medium")
    return None


def evaluate_w9(player_row: pd.Series, owner_rb_teams: set[str]) -> Warning_ | None:
    if player_row["position"] == "RB" and player_row.get("nfl_team") in owner_rb_teams:
        return Warning_("W9", f"{player_row['player']}: shares an NFL team with a rostered RB -- handcuff, not an independent path.", "Low")
    return None


def evaluate_w12(player_row: pd.Series, roster: RosterState) -> Warning_ | None:
    if 7 <= roster.current_round <= 9 and player_row["position"] == "WR" and roster.count("RB") < 3:
        return Warning_("W12", "WR spent in Rd 7-9 with no RB3 rostered -- last strong RB league-winner window.", "High")
    return None


# ---------------------------------------------------------------------------
# Recommender: pick-53 fork + Rd 7-11 congestion + top-N by need (spec Section 8)
# ---------------------------------------------------------------------------
def pick53_fork_state(roster: RosterState, laporta_available: bool) -> dict:
    has_te = roster.count("TE") > 0
    if has_te:
        return {"branch": "resolved", "note": "TE1 already rostered."}
    if roster.current_overall_pick <= 53 and laporta_available:
        return {"branch": "laporta_available", "note": "Take LaPorta at 53; TE2 follows in Rd 10-11."}
    return {
        "branch": "laporta_gone",
        "note": "No early TE -- double up Kincaid + Andrews tier in Rd 8-11. "
        "Congestion band moves up to Rd 7-11 on this branch (W18).",
    }


def band_7_11_status(roster: RosterState) -> dict:
    band_picks = [p for p in config.owner_pick_windows() if 7 <= config.round_of_pick(p["overall"]) <= 11]
    remaining = [p for p in band_picks if p["overall"] >= roster.current_overall_pick]
    commitments = []
    if roster.count("QB") == 0:
        commitments.append("QB1")
    if roster.count("RB") < 3:
        commitments.append("RB3")
    if roster.count("TE") == 0:
        commitments.append("TE1")
    if roster.count("TE") <= 1:
        commitments.append("TE2")
    return {"remaining_picks_in_band": len(remaining), "commitments": commitments, "congested": len(commitments) > len(remaining)}


def roster_summary(roster: RosterState) -> dict:
    return {
        pos: {"drafted": roster.count(pos), "target": target, "remaining": target - roster.count(pos)}
        for pos, target in config.ROSTER_TARGET.items()
    }


def top_recommendations(board_with_composite_and_availability: pd.DataFrame, roster: RosterState, n: int = 10) -> pd.DataFrame:
    df = board_with_composite_and_availability
    needs = {pos: t - roster.count(pos) for pos, t in config.ROSTER_TARGET.items()}
    df = df.copy()
    df["roster_need"] = df["position"].map(needs).fillna(0).clip(lower=0)
    df["need_adjusted_score"] = df["composite_score"] * (1 + 0.05 * df["roster_need"].clip(upper=3))
    return df.sort_values("need_adjusted_score", ascending=False).head(n)


def evaluate_pick(
    board: pd.DataFrame,
    roster: RosterState,
    laporta_available: bool,
    owner_drift: pd.DataFrame | None,
    owner_rb_teams: set[str] | None = None,
) -> dict:
    """The one function app/main.py calls per pick to get everything the UI needs:
    warnings, roster summary, the pick-53 fork, the Rd 7-11 band status, and a
    need-adjusted top-10. `board` must already have composite_score and
    survival_probability computed (compute_composite + compute_availability)."""
    warnings = evaluate_guardrails(roster, laporta_taken_at_pick53=not laporta_available and roster.count("TE") > 0, owner_drift=owner_drift)
    last_row = None
    if roster.picks:
        matches = board[board["player"] == roster.picks[-1]["player"]]
        if len(matches):
            last_row = matches.iloc[0]
    if last_row is not None:
        for w in (evaluate_w11(last_row), evaluate_w8(last_row), evaluate_w12(last_row, roster)):
            if w:
                warnings.append(w)
        if owner_rb_teams:
            w9 = evaluate_w9(last_row, owner_rb_teams)
            if w9:
                warnings.append(w9)
    return {
        "warnings": warnings,
        "roster_summary": roster_summary(roster),
        "pick53_fork": pick53_fork_state(roster, laporta_available),
        "band_7_11": band_7_11_status(roster),
        "top_recommendations": top_recommendations(board, roster),
    }
