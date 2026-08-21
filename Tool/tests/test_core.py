"""
Four things worth locking in before trusting this tool mid-draft:

1. The fixed-window invariant (spec Section 3) -- explicitly required by the spec to be
   a unit test, not just an assertion buried in a script.
2. ADP normalization against the literal examples in spec Section 4.4.
3. Join integrity of the built player_master.csv -- fails loudly if a future data
   refresh breaks a join silently.
4. A draft-engine smoke test -- feeds a short fabricated pick sequence through the
   recompute path and asserts it doesn't raise. Distinct from the human dry-run mock
   draft the spec requires separately (Section 10 step 7); this is cheap regression
   coverage, not a substitute for that.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_ROOT))
sys.path.insert(0, str(TOOL_ROOT / "app"))

import config  # noqa: E402
import draft_engine as de  # noqa: E402
from build import pipeline  # noqa: E402
import backtest  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Fixed-window invariant
# ---------------------------------------------------------------------------
def test_fixed_window_invariant():
    windows = config.owner_pick_windows()
    by_length: dict[int, list[Counter]] = {}
    for w in windows:
        if w["intervening"] is None:  # final pick, no next
            continue
        by_length.setdefault(len(w["intervening"]), []).append(Counter(w["intervening"]))

    assert set(by_length) == {8, 14}, f"expected exactly two wait lengths, got {sorted(by_length)}"

    for length, multisets in by_length.items():
        expected = multisets[0]
        for m in multisets[1:]:
            assert m == expected, (
                f"intervening-manager multiset for a {length}-pick wait was not identical "
                f"across every occurrence: {m} != {expected}"
            )

    short_managers = set(by_length[8][0])
    long_managers = set(by_length[14][0])
    assert short_managers.isdisjoint(long_managers)
    assert short_managers | long_managers | {config.OWNER} == set(config.DRAFT_ORDER_2026)


def test_owner_picks_alternate_short_and_long_waits():
    windows = config.owner_pick_windows()
    waits = [len(w["intervening"]) for w in windows if w["intervening"] is not None]
    # odd-round owner picks (5, 29, 53, ...) get the long wait; even-round (20, 44, ...)
    # get the short wait, per spec Section 3.
    for w in windows:
        if w["intervening"] is None:
            continue
        expected_len = 14 if w["round"] % 2 == 1 else 8
        assert len(w["intervening"]) == expected_len, f"round {w['round']}: expected wait {expected_len}"


# ---------------------------------------------------------------------------
# 2. ADP normalization (spec Section 4.4's literal examples)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected_first,expected_last,expected_suffix",
    [
        ("Gibbs, Jahmyr", "Jahmyr", "Gibbs", None),
        ("St. Brown, Amon-Ra", "Amon-Ra", "St. Brown", None),
        ("Walker III, Kenneth", "Kenneth", "Walker", "III"),
        ("Etienne Jr., Travis", "Travis", "Etienne", "JR"),
        ("Pitts Sr., Kyle", "Kyle", "Pitts", "SR"),
    ],
)
def test_parse_nffc_name(raw, expected_first, expected_last, expected_suffix):
    first, last, suffix = config.parse_nffc_name(raw)
    assert first == expected_first
    assert last == expected_last
    assert suffix == expected_suffix


def test_parse_sleeper_name_suffix():
    first, last, suffix = config.parse_sleeper_name("Kenneth Walker III")
    assert (first, last, suffix) == ("Kenneth", "Walker", "III")


def test_normalize_name_matches_across_suffix_styles():
    # NFFC's "Etienne Jr., Travis" and Sleeper's "Travis Etienne" must normalize identically.
    nffc_first, nffc_last, _ = config.parse_nffc_name("Etienne Jr., Travis")
    assert config.normalize_name(f"{nffc_first} {nffc_last}") == config.normalize_name("Travis Etienne")


def test_st_brown_period_is_not_treated_as_a_suffix():
    base, suffix = config.split_suffix("Amon-Ra St. Brown")
    assert suffix is None
    assert config.normalize_name("Amon-Ra St. Brown") == "amon ra st brown"


@pytest.mark.parametrize(
    "canonical,source,raw",
    [
        ("ARI", "nffc", "ARZ"),
        ("ARI", "clay", "ARZ"),
        ("BAL", "clay", "BLT"),
        ("CLE", "clay", "CLV"),
        ("HOU", "clay", "HST"),
        ("JAX", "sleeper", "JAC"),
        ("LAR", "nffc", "LA"),
        ("LV", "sleeper", "LVR"),
        ("FA", "sleeper", "UNS"),
    ],
)
def test_team_code_mapping(canonical, source, raw):
    assert config.canonical_team(raw, source) == canonical


def test_unmapped_team_code_returns_none_not_a_guess():
    assert config.canonical_team("ZZZ", "nffc") is None


# ---------------------------------------------------------------------------
# 3. Join integrity of the built player_master.csv
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def built():
    exit_code = pipeline.main()
    assert exit_code == 0, "pipeline.main() reported hard failures -- see join_report.txt"
    master = pd.read_csv(config.PLAYER_MASTER_PATH)
    report_text = config.JOIN_REPORT_PATH.read_text(encoding="utf-8")
    return master, report_text


def test_join_report_has_zero_hard_failures(built):
    _, report_text = built
    assert "HARD FAILURES (0)" in report_text


def test_player_master_has_no_unmapped_teams(built):
    master, _ = built
    bad = master[master["nfl_team"].notna() & ~master["nfl_team"].isin(config.CANONICAL_TEAMS)]
    assert bad.empty, f"non-canonical team codes leaked into player_master: {bad['nfl_team'].unique()}"


def test_player_master_positions_are_all_valid(built):
    master, _ = built
    assert set(master["position"].unique()) <= set(config.POSITIONS)


def test_player_master_ppr_base_present_for_most_rows(built):
    master, _ = built
    # A handful of priors-only orphans are expected and logged; the vast majority of
    # rows must carry a base valuation.
    assert master["ppr_base"].notna().mean() > 0.95


# ---------------------------------------------------------------------------
# 4. Draft-engine smoke test
# ---------------------------------------------------------------------------
def test_draft_engine_smoke(built):
    master, _ = built
    manager_priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    owner_drift = pd.read_csv(config.DATA_DERIVED / "owner_drift.csv")

    board = de.compute_composite(master)
    assert board["composite_score"].notna().any()

    avail = de.compute_availability(
        board, as_of_pick=4, target_pick=20, manager_priors=manager_priors, team_bias=team_bias,
        n_sims=300, rng=np.random.default_rng(0),
    )
    assert avail["survival_probability"].between(0, 1).all()

    fabricated_picks = [
        {"player": "Ja'Marr Chase", "position": "WR", "round": 1, "overall": 5},
        {"player": "Justin Jefferson", "position": "WR", "round": 2, "overall": 20},
        {"player": "Brock Bowers", "position": "TE", "round": 3, "overall": 29},
    ]
    roster = de.RosterState(picks=fabricated_picks, current_round=4, current_overall_pick=44)
    result = de.evaluate_pick(avail, roster, laporta_available=False, owner_drift=owner_drift)

    assert isinstance(result["warnings"], list)
    assert set(result["roster_summary"]) == set(config.ROSTER_TARGET)
    assert result["pick53_fork"]["branch"] == "resolved"  # already has a TE
    assert not result["top_recommendations"].empty

    # Dial dispersion to zero -- must visibly zero out bonus_norm's contribution (spec 6.2).
    board_zero = de.compute_composite(master, dispersion_multiplier=0.0)
    assert (board_zero["bonus_norm"].fillna(0) == 0).all() or board_zero["bonus_est_ppr"].eq(0).all()


def test_composite_renormalizes_when_a_layer_is_disabled(built):
    master, _ = built
    saved = config.LAYERS["bonus_model"]["applies"]
    try:
        config.LAYERS["bonus_model"]["applies"] = False
        board = de.compute_composite(master)
        assert "bonus" in board.attrs["disabled_composite_layers"]
        assert "bonus" not in board.attrs["enabled_composite_weights"]
    finally:
        config.LAYERS["bonus_model"]["applies"] = saved


# ---------------------------------------------------------------------------
# 5. Post-review corrections (spec Section 12): VORP composite, lognormal survival,
#    Monte Carlo capacity constraint.
# ---------------------------------------------------------------------------
def test_replacement_level_is_flex_aware():
    # 2-team toy league, 1 dedicated starter per position + 1 shared FLEX slot, so the
    # arithmetic is checkable by hand. RB/WR/TE overflow (everyone past their 2 dedicated
    # starters) pools together; the two best overflow players (RB=40, WR=38) take the
    # two FLEX slots, so RB and WR each absorb one extra startable player and TE doesn't.
    df = pd.DataFrame(
        {
            "position": (["QB"] * 5) + (["RB"] * 10) + (["WR"] * 10) + (["TE"] * 10),
            "ppr_base": (
                [50, 40, 30, 20, 10]
                + [50, 45, 40, 35, 30, 25, 20, 15, 10, 5]
                + [48, 44, 38, 34, 28, 24, 18, 14, 8, 4]
                + [46, 42, 36, 32, 26, 22, 16, 12, 6, 2]
            ),
        }
    )
    levels = de.compute_replacement_levels(
        df, n_teams=2, starting_lineup={"QB": 1, "RB": 1, "WR": 1, "TE": 1, "FLEX": 1}, flex_eligible={"RB", "WR", "TE"}
    )
    assert levels == {"QB": 30.0, "RB": 35.0, "WR": 34.0, "TE": 36.0}


def test_composite_score_is_points_not_a_percentile(built):
    # The defect (spec 12.1): every position's best player scored ~99-100 regardless of
    # real value (Loveland at 182 proj. points outranking Jefferson at 252). A points-
    # denominated composite has no reason to cluster every position's ceiling near 100 --
    # real per-position value differs by more than a percentile scale ever could show.
    master, _ = built
    board = de.compute_composite(master)
    top_by_position = board.groupby("position")["composite_score"].max()
    assert top_by_position.max() - top_by_position.min() > 15
    # A real VORP scale routinely goes negative (below-replacement bench players) and
    # above 100 (a true positional-scarcity standout) -- a 0-100 percentile never could.
    assert board["composite_score"].min() < 0 or board["composite_score"].max() > 100


def test_bonus_dispersion_zero_moves_composite_score_itself(built):
    # Not just bonus_norm (display) -- bonus_est_ppr must be wired into composite_score
    # IN POINTS per R18, so zeroing it changes the actual sort, not just a side column.
    master, _ = built
    full = de.compute_composite(master, dispersion_multiplier=1.0)
    zero = de.compute_composite(master, dispersion_multiplier=0.0)
    qb_drop = full.loc[full["position"] == "QB", "composite_score"].mean() - zero.loc[zero["position"] == "QB", "composite_score"].mean()
    assert qb_drop > 0.5


def test_survival_baseline_is_not_degenerate_past_adp_max():
    # The defect (spec 12.2): a triangular/PERT curve's support is exactly
    # [adp_min, adp_max], so a target past adp_max clipped to a hard 0.0 -- no signal,
    # no matter how strong the manager-priors evidence was. The lognormal fit (R19) is
    # unbounded, so this must land strictly between 0 and 1, not at the old hard floor.
    row = pd.Series(
        {
            "comparison_adp_usable": True, "comparison_adp_value": 30.0,
            "comparison_adp_min": 17.0, "comparison_adp_max": 41.0, "comparison_adp_n": 51.0,
            "reference_adp_rank": 30.0,
        }
    )
    surv, used_fallback = de._survival_baseline_row(row, as_of_pick=44, target_pick=53)
    assert not used_fallback
    assert 0.0 < surv < 0.5  # past adp_max=41 -> unlikely, but never impossible


def test_survival_conditions_on_present():
    # R19's "independent of distribution choice" fix: P(survive to target | survived to
    # as_of) = S(target)/S(as_of). Since S is monotone decreasing, conditioning on
    # having already survived to a later as_of_pick can only raise (or match) the
    # unconditioned probability, never lower it.
    row = pd.Series(
        {
            "comparison_adp_usable": True, "comparison_adp_value": 60.0,
            "comparison_adp_min": 40.0, "comparison_adp_max": 90.0, "comparison_adp_n": 40.0,
            "reference_adp_rank": 60.0,
        }
    )
    unconditioned, _ = de._survival_baseline_row(row, as_of_pick=0, target_pick=70)
    conditioned, _ = de._survival_baseline_row(row, as_of_pick=44, target_pick=70)
    assert conditioned >= unconditioned


def test_capacity_constraint_bounds_expected_departures(built):
    # The defect (spec 12.3): a marginal per-player model expected 159.1 total
    # departures and 48.7 TEs across 8 real picks, because nothing tied total departures
    # to the number of picks that actually occur. Monte Carlo removes exactly one
    # player per simulated pick by construction, so the expected total across the pool
    # must track the real pick count, not blow up by 20x.
    master, _ = built
    manager_priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)

    as_of_pick, target_pick = 44, 53
    k = len([1 for o, _, m in config.full_draft_sequence() if as_of_pick < o < target_pick and m != config.OWNER])
    avail = de.compute_availability(
        board, as_of_pick=as_of_pick, target_pick=target_pick, manager_priors=manager_priors, team_bias=team_bias,
        n_sims=500, rng=np.random.default_rng(1),
    )
    pool = avail[avail["position"].isin(config.POSITIONS)]
    expected_departed = (1 - pool["survival_probability"]).sum()
    te_departed = (1 - pool.loc[pool["position"] == "TE", "survival_probability"]).sum()

    assert k * 0.5 <= expected_departed <= k * 3  # was ~159 for k=8 before the fix
    assert te_departed < 10  # was 48.7 (more TEs than picks even existed) before the fix


# ---------------------------------------------------------------------------
# 6. Backtest smoke test (spec 12.5 / R25) -- the human-facing calibration report is
#    read by eye before draft day, not asserted on; this just guards the mechanism
#    against a silent crash or a malformed-output regression.
# ---------------------------------------------------------------------------
def test_backtest_smoke(built):
    results, notes = backtest.run_backtest(n_sims=50, seed=0)
    assert notes  # every season should produce at least a coverage note
    assert not results.empty
    assert set(results.columns) == {"season", "as_of_pick", "target_pick", "position", "predicted", "actual"}
    assert results["predicted"].between(0, 1).all()
    assert results["actual"].isin([0.0, 1.0]).all()
    assert set(results["position"]) <= set(config.POSITIONS)


# ---------------------------------------------------------------------------
# 7. Streamlit app smoke test -- guards main.py's call sites against drifting out of
#    sync with draft_engine's signatures (e.g. compute_availability's new
#    drafted_name_keys parameter, added alongside the 12.1-12.3 rewrite).
# ---------------------------------------------------------------------------
def test_streamlit_app_smoke(built):
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(TOOL_ROOT / "app" / "main.py"), default_timeout=60)
    at.run()
    assert not at.exception
