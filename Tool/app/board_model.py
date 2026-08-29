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


def sharp_edge(row: pd.Series) -> float | None:
    """How many picks EARLIER the high stakes market takes him than Sleeper does.

    `adp_rank_divergence` is comparison minus reference, so a bigger number means the
    sharps take him LATER. Negate it, so positive means the sharps are higher on him,
    which is the side value lives on. Cross-check from the owner's own intel file:
    Jonathon Brooks, Sleeper 128 against NFFC 66, reads +62.
    """
    d = row.get("adp_rank_divergence")
    return None if pd.isna(d) else -float(d)


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
) -> pd.DataFrame:
    """Who is worth spending `pick` on.

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
    mask = (
        ~pool["player"].isin(used).to_numpy()
        & shape_ok
        & (intel_tag != "hard_avoid").to_numpy()
        & (avail_arr >= min_availability)
        & (wait_arr < WAIT_THRESHOLD)
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
    divergence = sub.get("adp_rank_divergence", pd.Series(np.nan, index=sub.index))
    sharp = np.where(divergence.notna(), -divergence.astype(float), 0.0)

    out = pd.DataFrame({
        "player": sub["player"].to_numpy(),
        "position": sub["position"].to_numpy(),
        "on_list": on_list.to_numpy(),
        "priority": priority,
        "composite_score": sub["composite_score"].astype(float).to_numpy(),
        "edge": edge,
        "vorp": vorp.to_numpy(),
        "sharp_edge": sharp,
        "availability": avail_arr[mask],
        "wait": wait_arr[mask],
        "row": [sub.iloc[i] for i in range(len(sub))],
    })
    # Coarser than `edge` itself: two candidates within one band are "near-equal" on
    # value, so intel/sharp-market breaks the tie; a wider gap is decided on edge alone.
    out["_edge_tier"] = np.floor(out["edge"] / EDGE_TIE_BAND_POINTS)
    return out.sort_values(
        by=["_edge_tier", "on_list", "priority", "sharp_edge", "composite_score"],
        ascending=[False, False, True, False, False],
    ).drop(columns=["_edge_tier"]).reset_index(drop=True)


def build_route(anchor: pd.Series, remaining: dict, pool: pd.DataFrame, schedule: list[int], this_pick: int) -> dict:
    """One whole path: the anchor plus a pick at each of the next `ROUTE_LEGS` turns.

    Plan gating comes straight from the strategy docs. One quarterback until the very
    late rounds, because QB2 is the designated sacrifice. The second tight end fills
    from 101. Running back darts open at the back of the draft.
    """
    picks = owner_pick_numbers()
    shape = dict(remaining)
    shape[anchor["position"]] = shape.get(anchor["position"], 0) - 1
    used = {anchor["player"]}
    taken = {"QB": 1 if anchor["position"] == "QB" else 0, "TE": 1 if anchor["position"] == "TE" else 0}
    legs = []
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
        legs.append({"pick": pk, "player": chosen["player"], "position": chosen["position"], "odds": chosen["availability"]})
        used.add(chosen["player"])
        shape[chosen["position"]] -= 1
        taken[chosen["position"]] = taken.get(chosen["position"], 0) + 1
    members = [anchor] + [pool.loc[pool["player"] == l["player"]].iloc[0] for l in legs]
    return {
        "anchor": anchor,
        "legs": legs,
        "shape_left": shape,
        "vorp_sum": float(sum(float(m.get("vorp") or 0.0) for m in members)),
        "composite_sum": float(sum(float(m["composite_score"]) for m in members)),
        "sharp_sum": float(sum(max(sharp_edge(m) or 0.0, 0.0) for m in members)),
    }


def build_routes(board: pd.DataFrame, roster_counts: dict, this_pick: int, on_clock: bool, n_routes: int = 3) -> list[dict]:
    """Up to three whole paths, recomputed every pick."""
    picks = owner_pick_numbers()
    survives_to = this_pick if on_clock else this_pick
    next_after = next((p for p in picks if p > this_pick), this_pick + config.N_TEAMS)
    remaining = {pos: cap - roster_counts.get(pos, 0) for pos, cap in config.ROSTER_TARGET.items()}
    schedule = [p for p in picks if p > this_pick][:ROUTE_LEGS]
    anchors = candidates_for_pick(board, this_pick, this_pick, next_after, remaining, set(), min_availability=0.0)
    routes = [build_route(a["row"], remaining, board, schedule, this_pick) for _, a in anchors.head(n_routes).iterrows()]
    routes.sort(key=lambda r: r["vorp_sum"] + r["composite_sum"] + 2 * r["sharp_sum"], reverse=True)
    return routes


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
