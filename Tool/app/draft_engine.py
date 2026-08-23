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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import lognorm, norm

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
# Composite scoring (spec Section 6.5, rewritten per 12.1 / R16-R18)
#
# Composite unit is now PPR points, not a within-position percentile average (R16).
# `vorp` (value over a flex-aware replacement level, R17) and `bonus_est_ppr` (R18)
# both enter as real points, which is what makes the composite comparable ACROSS
# positions -- a QB's bonus gain of 8-13 points and a WR's ~1 point now move the
# composite by their actual relative size instead of being separately re-ranked to
# ~100 within each position and erased. `factor_norm`/`market_norm` stay as
# within-position percentiles (spec Section 5 rule #5: factor_score is ordinal, never
# points, never cross-position) and enter as small additive nudges -- their existing
# 0-100 scale times their (small, ~0.15) weight caps them at roughly +/-15 points,
# appropriately modest next to a real point-denominated vorp+bonus term.
# ---------------------------------------------------------------------------
_COMPONENT_LAYER = {"base": "implied_props", "bonus": "bonus_model", "factor": "factor_grids"}
# "market" has no available/applies pair in LAYERS (spec Section 4.2 doesn't define one) --
# it's controlled purely by its own COMPOSITE_WEIGHTS entry.


def compute_replacement_levels(
    df: pd.DataFrame,
    n_teams: int = config.N_TEAMS,
    starting_lineup: dict | None = None,
    flex_eligible: set[str] | None = None,
) -> dict[str, float]:
    """Flex-aware replacement level per position (R17): the `ppr_base` of the first
    player at that position who would NOT start in a league of `n_teams` teams.

    Two-stage rank, because a flex spot's occupant isn't determined by position alone:
    dedicated starters (QB x1, RB x2, WR x2, TE x1, per team) are locked in first, then
    every remaining flex-eligible player (RB/WR/TE only -- QB has no flex path) is
    pooled league-wide and the best `ppr_base` players fill the shared FLEX slots
    regardless of position. Whichever position a flex slot came from absorbs one more
    startable player before hitting replacement level there. This is what makes McBride
    at 212 points able to out-value Loveland at 182 without a hard-coded position
    hierarchy -- it falls out of where each of them actually ranks against a real
    12-team, 2-flex bench cutoff.
    """
    starting_lineup = starting_lineup or config.STARTING_LINEUP
    flex_eligible = flex_eligible or config.FLEX_ELIGIBLE

    ranked = {
        pos: df.loc[df["position"] == pos, "ppr_base"].dropna().sort_values(ascending=False).to_numpy()
        for pos in config.POSITIONS
    }
    dedicated = {pos: starting_lineup.get(pos, 0) * n_teams for pos in config.POSITIONS}

    flex_pool = []
    for pos in flex_eligible & set(config.POSITIONS):
        overflow = ranked.get(pos, np.array([]))[dedicated[pos]:]
        flex_pool.extend((value, pos) for value in overflow)
    flex_pool.sort(key=lambda t: t[0], reverse=True)

    n_flex_slots = starting_lineup.get("FLEX", 0) * n_teams
    flex_count_by_pos = Counter(pos for _, pos in flex_pool[:n_flex_slots])

    replacement_level = {}
    for pos in config.POSITIONS:
        values = ranked.get(pos, np.array([]))
        rank = dedicated[pos] + flex_count_by_pos.get(pos, 0)  # 0-indexed: rank-th player is the LAST starter
        if len(values) == 0:
            replacement_level[pos] = 0.0
        elif rank < len(values):
            replacement_level[pos] = float(values[rank])  # first player who does NOT start
        else:
            replacement_level[pos] = float(values[-1])  # pool thinner than the roster target; use the last we have
    return replacement_level


def compute_composite(
    master: pd.DataFrame,
    weights: dict | None = None,
    dispersion_multiplier: float = 1.0,
    injury_weight: float | None = None,
) -> pd.DataFrame:
    """Adds `vorp`, `base_norm`/`bonus_norm` (display-only percentiles, spec: "percentiles
    may remain as expandable display detail; they must not drive the sort"),
    `factor_norm`/`market_norm`, and `composite_score` (points) to a copy of `master`.

    composite_score = w_base*vorp + w_bonus*bonus_est_ppr + w_factor*factor_norm +
    w_market*market_norm, with weights renormalized to sum to 1 over whichever layers
    are enabled (spec Section 4.2's "renormalize and show it") -- turning a layer off
    scales the survivors up to fill the gap rather than leaving it unallocated or
    (worse) silently inflating just one neighbour. A player missing a component (e.g.
    no ADP match) contributes 0 for that term rather than imputing or reweighting the
    rest -- correct here in a way it wasn't for the old percentile average, because a
    real point value simply isn't observed rather than needing to be estimated from
    what is.
    """
    df = master.copy()
    weights = dict(weights if weights is not None else config.COMPOSITE_WEIGHTS)
    injury_weight = config.INJURY_WEIGHT if injury_weight is None else injury_weight

    enabled_weights = {
        k: w for k, w in weights.items() if config.layer_on(_COMPONENT_LAYER.get(k, "__always_on__")) or k not in _COMPONENT_LAYER
    }
    # layer_on() only knows real LAYERS keys; "market" isn't one, so the `or` above keeps it.
    disabled = sorted(set(weights) - set(enabled_weights))
    weight_total = sum(enabled_weights.values())
    norm_weights = {k: (w / weight_total if weight_total > 0 else 0.0) for k, w in enabled_weights.items()}

    if dispersion_multiplier != 1.0 and config.layer_on("bonus_model"):
        df["bonus_est_ppr"] = recompute_bonus_est_ppr(df, dispersion_multiplier)

    replacement_level = compute_replacement_levels(df)
    df["replacement_level"] = df["position"].map(replacement_level)
    df["vorp"] = df["ppr_base"] - df["replacement_level"]

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

    df["composite_score"] = (
        norm_weights.get("base", 0) * df["vorp"].fillna(0)
        + norm_weights.get("bonus", 0) * df["bonus_est_ppr"].fillna(0)
        + norm_weights.get("factor", 0) * df["factor_norm"].fillna(0)
        + norm_weights.get("market", 0) * df["market_norm"].fillna(0)
    )

    # Player intel (work order 2026-08-16 item 3): target/fade are a bounded nudge,
    # hard-capped at config.INTEL_NUDGE_CAP VOR points -- not weighted, not scaled by
    # anything else, so toggling the layer off restores the exact prior composite and
    # the cap can never be silently raised by another layer's math. hard_avoid gets NO
    # points nudge here (INTEL_TAG_SIGN has no entry for it) -- it is a filter applied
    # in top_recommendations, never arithmetic on the score.
    if config.layer_on("player_intel") and "intel_tag" in df.columns:
        df["intel_nudge_pts"] = df["intel_tag"].map(config.INTEL_TAG_SIGN).fillna(0.0) * config.INTEL_NUDGE_CAP
        df["composite_score"] = df["composite_score"] + df["intel_nudge_pts"]
    else:
        df["intel_nudge_pts"] = 0.0

    df.attrs["disabled_composite_layers"] = disabled
    df.attrs["enabled_composite_weights"] = norm_weights
    df.attrs["replacement_level"] = replacement_level
    return df


# ---------------------------------------------------------------------------
# Availability model (spec Section 7, survival curve rewritten per 12.2 / R19)
# ---------------------------------------------------------------------------
_ADP_SOURCE_FIELDS = {
    # source -> (anchor_field, min_field, max_field, n_field). Sleeper's ingestion never
    # populates min/max/n (its ADP is a bare sequential rank, not an observed-range
    # export), which is exactly why it can anchor but not disperse.
    "reference": ("reference_adp_rank", None, None, None),
    "comparison": ("comparison_adp_value", "comparison_adp_min", "comparison_adp_max", "comparison_adp_n"),
}


def _resolve_survival_sources(row: pd.Series) -> tuple[float, float, float, float, float]:
    """Routes config.SURVIVAL_ANCHOR / config.SURVIVAL_DISPERSION_SOURCE to actual
    row fields. Returns (primary_anchor, secondary_anchor, dispersion_value,
    dispersion_min, dispersion_max, dispersion_n)."""
    primary_src = config.SURVIVAL_ANCHOR
    secondary_src = "comparison" if primary_src == "reference" else "reference"
    disp_src = config.SURVIVAL_DISPERSION_SOURCE

    primary_anchor = row.get(_ADP_SOURCE_FIELDS[primary_src][0])
    secondary_anchor = row.get(_ADP_SOURCE_FIELDS[secondary_src][0])
    disp_anchor_field, disp_min_field, disp_max_field, disp_n_field = _ADP_SOURCE_FIELDS[disp_src]
    dispersion_value = row.get(disp_anchor_field)
    dispersion_min = row.get(disp_min_field) if disp_min_field else np.nan
    dispersion_max = row.get(disp_max_field) if disp_max_field else np.nan
    dispersion_n = row.get(disp_n_field) if disp_n_field else np.nan
    return primary_anchor, secondary_anchor, dispersion_value, dispersion_min, dispersion_max, dispersion_n


def _fit_lognormal_from_adp(
    primary_anchor, secondary_anchor, dispersion_value, dispersion_min, dispersion_max, dispersion_n
) -> tuple[float, float, bool] | None:
    """Unbounded lognormal fit (R19), replacing the old triangular/PERT curve -- both
    of those have support exactly [adp_min, adp_max], which is why a target past
    adp_max used to clip to a hard 0.0.

    Re-anchored per the 2026-08-16 work order item 1: the owner drafts on Sleeper, and
    the two ADP populations diverge systematically (measured: Sleeper ranks TE ~23 and
    QB ~13 picks earlier than NFFC's mean pick, WR ~9 later -- see
    `compute_adp_source_offsets` in build/pipeline.py). Centering on NFFC was therefore
    calibrating the model to the wrong draft population. The curve's CENTER (median) is
    `primary_anchor` (`config.SURVIVAL_ANCHOR`, "reference" i.e. Sleeper by default);
    dispersion comes from `config.SURVIVAL_DISPERSION_SOURCE` ("comparison" i.e. NFFC by
    default) -- 51 real observed drafts is still the only empirical spread data
    available, and that spread describes draft variance generally, not anything
    specific to NFFC's level.

    A rank (Sleeper) used as `primary_anchor` is an ordinal, not a mean pick number --
    rank N approx pick N only holds if the field drafts close to consensus. Accepted
    here and stated, not hidden.

    Sigma is fit the same way as before (quantile-bracket algebra against the
    dispersion source's OWN center, since that's the distribution it was actually
    measured from: the curve's [1/(n+1), n/(n+1)] quantiles bracket
    [dispersion_min, dispersion_max]) and then carried over as a pure dispersion
    magnitude onto the RELOCATED (primary-anchor) mu -- "relocate," not "re-fit."

    Falls back to `secondary_anchor` when `primary_anchor` is missing, and to a fixed
    generic sigma when the dispersion source's empirical range is also missing,
    degenerate, or (per `_ADP_SOURCE_FIELDS`) simply doesn't exist for that source.
    Returns (mu, sigma, used_secondary_anchor) -- the third element is the "which
    anchor did this row use" flag the UI surfaces.
    """
    used_secondary_anchor = False
    if pd.notna(primary_anchor) and primary_anchor > 0:
        anchor_value = primary_anchor
    elif pd.notna(secondary_anchor) and secondary_anchor > 0:
        anchor_value = secondary_anchor
        used_secondary_anchor = True
    else:
        return None
    mu = math.log(anchor_value)

    sigma = config.GENERIC_LOGNORMAL_SIGMA
    have_range = (
        pd.notna(dispersion_n) and dispersion_n >= 2
        and pd.notna(dispersion_min) and pd.notna(dispersion_max)
        and dispersion_max > dispersion_min > 0
        and pd.notna(dispersion_value) and dispersion_value > 0
    )
    if have_range:
        dispersion_mu = math.log(dispersion_value)
        lo_q, hi_q = 1.0 / (dispersion_n + 1), dispersion_n / (dispersion_n + 1)
        z_lo, z_hi = norm.ppf(lo_q), norm.ppf(hi_q)
        candidates = []
        if z_lo < -1e-6:
            candidates.append((math.log(dispersion_min) - dispersion_mu) / z_lo)
        if z_hi > 1e-6:
            candidates.append((math.log(dispersion_max) - dispersion_mu) / z_hi)
        if candidates:
            sigma = max(float(np.mean(candidates)), 0.05)
    return mu, sigma, used_secondary_anchor


def _fit_from_row(row: pd.Series) -> tuple[float, float, bool] | None:
    return _fit_lognormal_from_adp(*_resolve_survival_sources(row))


def _survival_baseline_row(row: pd.Series, as_of_pick: int, target_pick: int) -> tuple[float, str]:
    """P(survive to target_pick | survived to as_of_pick) = S(target)/S(as_of) (R19's
    "independent of distribution choice" conditioning) from the fitted lognormal, or,
    when NEITHER ADP source has anything for this player, a coin-flip 0.5 (spec Section
    4.2's 'built alongside the primary path, not after' fallback carried to its limit).

    Returns (probability, anchor) where anchor is "reference" (Sleeper, the normal
    case), "comparison" (NFFC, used only when Sleeper has no rank for this player), or
    "none" (neither source had anything).
    """
    params = _fit_from_row(row)
    if params is None:
        return 0.5, "none"
    mu, sigma, used_secondary_anchor = params
    s_target = float(lognorm.sf(max(target_pick, 1e-6), s=sigma, scale=math.exp(mu)))
    s_asof = float(lognorm.sf(max(as_of_pick, 1e-6), s=sigma, scale=math.exp(mu))) if as_of_pick > 0 else 1.0
    surv = s_target / s_asof if s_asof > 1e-9 else 0.0
    secondary_src = "comparison" if config.SURVIVAL_ANCHOR == "reference" else "reference"
    anchor = secondary_src if used_secondary_anchor else config.SURVIVAL_ANCHOR
    return float(np.clip(surv, 0.0, 1.0)), anchor


def _lognormal_fit_arrays(pool: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-player (mu, sigma, has_fit) arrays, computed once and reused across every
    intervening pick's hazard evaluation -- the fit itself doesn't depend on the pick
    number, only its survival/hazard VALUE does."""
    n = len(pool)
    mu_arr, sigma_arr, has_fit = np.zeros(n), np.zeros(n), np.zeros(n, dtype=bool)
    for i, row in enumerate(pool.to_dict("records")):
        params = _fit_lognormal_from_adp(*_resolve_survival_sources(row))
        if params is not None:
            mu_arr[i], sigma_arr[i], _ = params
            has_fit[i] = True
    return mu_arr, sigma_arr, has_fit


def _pick_hazard_vector(pool_size: int, overall_pick: int, mu_arr, sigma_arr, has_fit, rank_arr) -> np.ndarray:
    """Vectorized 'about to be taken right around this pick' hazard (pdf/sf) for every
    player in the pool at once -- the ADP-position term the Monte Carlo softmax weights
    on (spec 12.3: 'sampled from a softmax over available players weighted by ADP
    position'). Fitted-lognormal players and generic-fallback players are each handled
    with one vectorized scipy call rather than a per-player Python loop."""
    hz = np.zeros(pool_size)
    x = max(overall_pick, 1e-6)
    if has_fit.any():
        sf = np.clip(lognorm.sf(x, s=sigma_arr[has_fit], scale=np.exp(mu_arr[has_fit])), 1e-9, None)
        pdf = lognorm.pdf(x, s=sigma_arr[has_fit], scale=np.exp(mu_arr[has_fit]))
        hz[has_fit] = pdf / sf
    generic = ~has_fit
    if generic.any():
        rk = rank_arr[generic]
        has_rank = ~np.isnan(rk)
        out = np.full(rk.shape, 1.0 / (config.N_TEAMS * config.N_ROUNDS))
        sf = np.clip(norm.sf(x, loc=rk[has_rank], scale=config.GENERIC_ADP_SPREAD_PICKS), 1e-9, None)
        pdf = norm.pdf(x, loc=rk[has_rank], scale=config.GENERIC_ADP_SPREAD_PICKS)
        out[has_rank] = pdf / sf
        hz[generic] = out
    return np.clip(hz, 1e-9, 50.0)


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


def _bias_multiplier_array(
    pool: pd.DataFrame, manager: str, team_bias_lookup: dict[tuple[str, str], float], owner_colleges: dict[str, list[str]]
) -> np.ndarray:
    """Per-player team/college bias multiplier for one manager (spec: applied to
    survival before; here it's the same ratio repurposed as a pick-weight multiplier --
    a manager more likely to draft a team/school gets a proportionally higher chance of
    being the one who takes that player in the simulation)."""
    n = len(pool)
    mult = np.ones(n)
    if config.layer_on("nfl_team_bias"):
        teams = pool["nfl_team"].to_numpy()
        for i in range(n):
            ratio = team_bias_lookup.get((manager, teams[i]))
            if ratio is not None:
                mult[i] *= min(max(ratio, config.NFL_TEAM_BIAS_CAP), 1.0 / config.NFL_TEAM_BIAS_CAP)
    if config.layer_on("college_bias"):
        affinity = owner_colleges.get(manager, [])
        if affinity:
            colleges = pool["college"].to_numpy()
            for i in range(n):
                if colleges[i] in affinity:
                    mult[i] /= config.COLLEGE_BIAS_DISCOUNT
    return mult


def simulate_intervening_picks(
    pool: pd.DataFrame,
    intervening: list[tuple[int, int, str]],
    manager_priors: pd.DataFrame,
    team_bias: pd.DataFrame,
    owner_roster_by_manager: dict[str, dict[str, int]],
    owner: str = config.OWNER,
    n_sims: int = config.AVAILABILITY_N_SIMS,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Monte Carlo capacity constraint (spec 12.3, R20/R21): discretely simulates every
    intervening pick so exactly k players leave in k picks, by construction -- fixing
    the marginal model's "159 players expected gone in 8 picks" defect. Each pick is
    drawn from a softmax over the still-available pool, weighted by that player's own
    ADP-implied hazard (R19's curve, at that exact pick number) x the picking manager's
    positional appetite at that round vs. league average x team/college bias. Because
    every pick removes a real player from the SAME shared pool, substitution is free:
    if a simulated draw takes McBride, every other TE's odds improve in that draw and
    every other manager's TE need can no longer be filled by him specifically -- a
    marginal per-player model cannot represent that "only one of these TEs goes."

    `intervening` is `[(overall_pick, round, manager), ...]` for every pick strictly
    between the current state and the target, in order (each manager appears once per
    pick they make -- twice within one owner-to-owner window, per the fixed-window
    property). Returns survival fraction per row of `pool`, in `pool`'s original order.
    """
    rng = rng or np.random.default_rng()
    n = len(pool)
    if n == 0 or not intervening:
        return np.ones(n)

    positions = pool["position"].to_numpy()
    pos_index = {p: i for i, p in enumerate(config.POSITIONS)}
    pos_codes = np.array([pos_index.get(p, -1) for p in positions])
    rank_arr = pool.get("reference_adp_rank", pd.Series(np.nan, index=pool.index)).to_numpy(dtype=float)
    mu_arr, sigma_arr, has_fit = _lognormal_fit_arrays(pool)

    priors_by_manager = manager_priors.set_index("manager")
    league_avg_row = pd.Series(_league_average_rates(manager_priors, owner))
    team_bias_lookup = {
        (r["manager"], r["nfl_team"]): r["bias_ratio"] for _, r in team_bias.iterrows()
    } if config.layer_on("nfl_team_bias") else {}
    owner_colleges = config.MANAGER_COLLEGE_AFFINITY if config.layer_on("college_bias") else {}

    n_picks = len(intervening)
    base_weight = np.ones((n_picks, n))
    # appetite[k, has_already, pos_code] -- the only piece that can vary within a
    # simulation (has_already depends on what THIS manager already took, in-sim).
    appetite = np.ones((n_picks, 2, len(config.POSITIONS)))
    valid_manager = np.zeros(n_picks, dtype=bool)
    bias_cache: dict[str, np.ndarray] = {}

    for k, (overall, round_num, manager) in enumerate(intervening):
        hz = _pick_hazard_vector(n, overall, mu_arr, sigma_arr, has_fit, rank_arr)
        if manager not in bias_cache:
            bias_cache[manager] = _bias_multiplier_array(pool, manager, team_bias_lookup, owner_colleges)
        base_weight[k] = hz * bias_cache[manager]

        if manager in priors_by_manager.index:
            valid_manager[k] = True
            mrow = priors_by_manager.loc[manager]
            widen = _confidence_widen(mrow)
            for has_already in (False, True):
                for pos in config.POSITIONS:
                    manager_rate = _position_pick_rate(mrow, pos, round_num, has_already)
                    league_rate = _position_pick_rate(league_avg_row, pos, round_num, has_position_already=False)
                    ratio = manager_rate / league_rate if league_rate > 0 else 1.0
                    appetite[k, int(has_already), pos_index[pos]] = max(1.0 + (ratio - 1.0) / widen, 1e-6)

    survived_count = np.zeros(n, dtype=np.int64)
    for _ in range(n_sims):
        available = np.ones(n, dtype=bool)
        has_pos: dict[str, np.ndarray] = {}
        for k, (overall, round_num, manager) in enumerate(intervening):
            idx = np.flatnonzero(available)
            if len(idx) == 0:
                break
            if valid_manager[k]:
                if manager not in has_pos:
                    real = owner_roster_by_manager.get(manager, {})
                    has_pos[manager] = np.array([real.get(pos, 0) > 0 for pos in config.POSITIONS])
                already = has_pos[manager]
                pc_idx = pos_codes[idx]
                mult = np.where(already[np.clip(pc_idx, 0, None)], appetite[k, 1, np.clip(pc_idx, 0, None)], appetite[k, 0, np.clip(pc_idx, 0, None)])
                w = base_weight[k, idx] * mult
            else:
                w = base_weight[k, idx]
            w = np.clip(w, 1e-12, None)
            choice = rng.choice(idx, p=w / w.sum())
            available[choice] = False
            pc = pos_codes[choice]
            if pc >= 0 and manager in has_pos:
                has_pos[manager][pc] = True
        survived_count += available.astype(np.int64)

    return survived_count / n_sims


def compute_availability(
    board: pd.DataFrame,
    as_of_pick: int,
    target_pick: int,
    manager_priors: pd.DataFrame,
    team_bias: pd.DataFrame,
    drafted_name_keys: set[str] | None = None,
    owner_roster_by_manager: dict[str, dict[str, int]] | None = None,
    owner: str = config.OWNER,
    draft_order: list[str] = config.DRAFT_ORDER_2026,
    n_sims: int = config.AVAILABILITY_N_SIMS,
    rng: np.random.Generator | None = None,
) -> pd.DataFrame:
    """Adds `survival_baseline`, `availability_used_fallback`, and `survival_probability`
    to a copy of `board`.

    `as_of_pick` is the last pick actually made in the live draft (0 if none yet).
    `target_pick` is the pick we want survival probability AT -- normally the owner's
    upcoming pick. These are kept as two separate arguments deliberately: collapsing
    them into one "current pick" parameter conflates "am I on the clock right now"
    (as_of_pick == target_pick - 1, zero intervening managers, survival trivially high
    for anyone left) with "will this survive to my next turn" (as_of_pick is well
    before target_pick, many intervening managers) -- two different questions that a
    single parameter would silently answer identically.

    Baseline (always computed, every row, regardless of draft state): the R19
    lognormal-or-generic curve, conditioned on survival to `as_of_pick`. This baseline
    IS the fallback path -- when manager_priors doesn't apply, this function returns it
    untouched, which is what makes the fallback "built alongside the primary path"
    rather than a separate branch.

    Primary refinement (only when manager_priors applies AND there are intervening
    picks): `drafted_name_keys` filters `board` down to the pool of players actually
    still on the clock, and `simulate_intervening_picks` (R20/R21) discretely simulates
    every pick between `as_of_pick` and `target_pick` from that pool. Rows not in the
    pool (already drafted, or outside `config.POSITIONS`) get `survival_probability` 0.
    """
    df = board.copy()
    owner_roster_by_manager = owner_roster_by_manager or {}
    drafted_name_keys = drafted_name_keys or set()

    baseline, anchor = [], []
    for _, row in df.iterrows():
        surv, a = _survival_baseline_row(row, as_of_pick, target_pick)
        baseline.append(surv)
        anchor.append(a)
    df["survival_baseline"] = baseline
    # "reference" (Sleeper, the normal case) | "comparison" (NFFC, only when Sleeper has
    # no rank for this player) | "none" (neither source had anything) -- work order item
    # 1's "flag column so the UI can show which anchor a row used."
    df["availability_anchor"] = anchor
    # Floor/ceiling, not just [0, 1]: a literal 0.0% reads as "impossible" in the UI, and
    # spec Section 5 rule #11 makes the same point about bust rates -- never render false
    # certainty in either direction, even when the model's point estimate is extreme.
    df["survival_baseline"] = df["survival_baseline"].clip(0.005, 0.995)
    df["availability_used_fallback"] = False  # whether the manager-priors refinement below applied at all

    if not config.layer_on("manager_priors"):
        df["survival_probability"] = df["survival_baseline"]
        df["availability_used_fallback"] = True
        return df

    seq = config.full_draft_sequence(draft_order)
    intervening = [
        (overall, rnd, mgr) for overall, rnd, mgr in seq if as_of_pick < overall < target_pick and mgr != owner
    ]  # spec Section 7: "Exclude Nathan's own priors -- he is not competing with himself"
    if not intervening:
        df["survival_probability"] = np.where(df["name_key"].isin(drafted_name_keys), 0.0, 1.0)
        return df

    in_pool = df["position"].isin(config.POSITIONS) & ~df["name_key"].isin(drafted_name_keys)
    pool = df[in_pool]
    survival = simulate_intervening_picks(
        pool, intervening, manager_priors, team_bias, owner_roster_by_manager, owner, n_sims=n_sims, rng=rng
    )

    df["survival_probability"] = 0.0
    df.loc[in_pool, "survival_probability"] = np.clip(survival, 0.005, 0.995)
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


def _intel_window_boost(df: pd.DataFrame, current_pick: int, tolerance: int = 6) -> pd.Series:
    """Work order 2026-08-16 item 3: 'a player tagged for pick 53 should surface as a
    target at 53, not at 20.' A modest ranking boost -- NOT a composite_score change,
    that's the separate, hard-capped `intel_nudge_pts` -- for target-tagged players
    whose `intel_windows` includes a pick near the current one, so the recommender
    surfaces them right when they're actionable instead of uniformly all draft long."""
    boost = pd.Series(1.0, index=df.index)
    if not config.layer_on("player_intel") or "intel_windows" not in df.columns:
        return boost

    def _near_current(windows) -> bool:
        if pd.isna(windows) or not str(windows).strip():
            return False
        return any(abs(int(w.strip()) - current_pick) <= tolerance for w in str(windows).split(","))

    is_target = df.get("intel_tag") == "target"
    near = df["intel_windows"].apply(_near_current)
    boost.loc[is_target & near] = 1.15
    return boost


def top_recommendations(board_with_composite_and_availability: pd.DataFrame, roster: RosterState, n: int = 10) -> pd.DataFrame:
    df = board_with_composite_and_availability
    if config.layer_on("player_intel") and "intel_tag" in df.columns:
        # hard_avoid is a FILTER, not a nudge (work order item 3) -- never surfaced as
        # a recommendation regardless of composite value, and never given arithmetic.
        df = df[df["intel_tag"] != "hard_avoid"]
    needs = {pos: t - roster.count(pos) for pos, t in config.ROSTER_TARGET.items()}
    df = df.copy()
    df["roster_need"] = df["position"].map(needs).fillna(0).clip(lower=0)
    df["need_adjusted_score"] = (
        df["composite_score"]
        * (1 + 0.05 * df["roster_need"].clip(upper=3))
        * _intel_window_boost(df, roster.current_overall_pick)
    )
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
