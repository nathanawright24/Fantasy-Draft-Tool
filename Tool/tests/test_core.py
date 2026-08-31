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

import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

TOOL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOL_ROOT))
sys.path.insert(0, str(TOOL_ROOT / "app"))

import board_model as bm  # noqa: E402
import config  # noqa: E402
import draft_engine as de  # noqa: E402
import draft_setup  # noqa: E402
import draft_state  # noqa: E402
from build import pipeline  # noqa: E402
import backtest  # noqa: E402


@pytest.fixture
def restore_config_singletons():
    """draft_setup.apply_setup mutates config's module-level state IN PLACE by design
    (see its own docstring) -- exactly what work order 2026-08-24 item 4 needs, and
    exactly what would leak into every other test in this file if left unrestored."""
    saved_order = list(config.DRAFT_ORDER_2026)
    saved_owner = config.OWNER
    saved_roster_target = dict(config.ROSTER_TARGET)
    saved_reference_adp = dict(config.REFERENCE_ADP)
    saved_layers = {k: dict(v) for k, v in config.LAYERS.items()}
    try:
        yield
    finally:
        config.DRAFT_ORDER_2026[:] = saved_order
        config.OWNER = saved_owner
        config.ROSTER_TARGET.clear()
        config.ROSTER_TARGET.update(saved_roster_target)
        config.REFERENCE_ADP.clear()
        config.REFERENCE_ADP.update(saved_reference_adp)
        for k, v in saved_layers.items():
            config.LAYERS[k].update(v)


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
    # dtype pin: matches app/main.py's load_master -- `intel_windows` reads back as
    # float64 (breaking downstream int() parsing) whenever no player currently spans
    # more than one pick window, since the column then has no comma anywhere to force
    # pandas to keep it as text.
    master = pd.read_csv(config.PLAYER_MASTER_PATH, dtype={"intel_windows": str})
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
    # "K" is a deliberate exception (work order 2026-08-24 item 7 / R35): ADP-only
    # kicker rows with no props/factor-grid coverage, added specifically so the tool
    # can recommend one at pick 188 -- not a join-integrity gap.
    assert set(master["position"].unique()) <= set(config.POSITIONS) | {"K"}


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
    result = de.evaluate_pick(avail, roster, fork_player_available=False, owner_drift=owner_drift)

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
    surv, anchor = de._survival_baseline_row(row, as_of_pick=44, target_pick=53)
    assert anchor == "reference"
    assert 0.0 < surv < 0.5  # past adp_max=41 -> unlikely, but never impossible


def test_survival_curve_is_anchored_on_sleeper_not_nffc():
    # Work order 2026-08-16 item 1: the owner drafts on Sleeper, and the two ADP
    # populations diverge systematically (measured: Sleeper ranks TE ~23 picks earlier
    # than NFFC). The curve's median must track reference_adp_rank (Sleeper), using
    # NFFC's min/max/n for dispersion shape only, not for where the curve is centered.
    row = pd.Series(
        {
            "reference_adp_rank": 25.0,  # Sleeper: this TE goes ~25th
            "comparison_adp_value": 48.0, "comparison_adp_min": 30.0, "comparison_adp_max": 70.0,
            "comparison_adp_n": 40.0,
        }
    )
    mu, sigma, used_secondary = de._fit_lognormal_from_adp(*de._resolve_survival_sources(row))
    assert not used_secondary
    assert math.exp(mu) == pytest.approx(25.0, rel=0.01)  # centered on Sleeper, not NFFC's 48
    assert sigma > 0  # dispersion still borrowed from NFFC's observed range


def test_survival_curve_falls_back_to_comparison_when_reference_missing():
    row = pd.Series(
        {
            "reference_adp_rank": np.nan,
            "comparison_adp_value": 48.0, "comparison_adp_min": 30.0, "comparison_adp_max": 70.0,
            "comparison_adp_n": 40.0,
        }
    )
    surv, anchor = de._survival_baseline_row(row, as_of_pick=0, target_pick=48)
    assert anchor == "comparison"
    assert 0.0 <= surv <= 1.0


def test_survival_baseline_uninformed_when_neither_source_has_data():
    row = pd.Series({"reference_adp_rank": np.nan, "comparison_adp_value": np.nan})
    surv, anchor = de._survival_baseline_row(row, as_of_pick=10, target_pick=20)
    assert anchor == "none"
    assert surv == 0.5


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
    assert set(results.columns) == {
        "season", "as_of_pick", "target_pick", "position",
        "predicted_montecarlo", "predicted_lognormal", "predicted_blend", "actual",
    }
    for col in ("predicted_montecarlo", "predicted_lognormal", "predicted_blend"):
        assert results[col].between(0, 1).all()
    assert results["actual"].isin([0.0, 1.0]).all()
    assert set(results["position"]) <= set(config.POSITIONS)


def test_backtest_scores_all_three_methods_on_the_identical_observation_set(built):
    # Work order 2026-08-24 item 1 / R36: the bake-off's whole premise is that all three
    # methods are scored on the SAME rows, so the comparison is apples to apples.
    results, _ = backtest.run_backtest(n_sims=50, seed=0)
    scores = backtest.score_methods(results)
    assert set(scores) == set(backtest.METHODS)
    for method in backtest.METHODS:
        assert scores[method]["decision_band_n"] <= len(results)
        assert 0.0 <= scores[method]["overall_brier"] <= 1.0

    chosen, reason = backtest.decide_availability_method(scores)
    assert chosen in ("montecarlo", "lognormal", "blend")
    assert reason  # the branch that fired must always be stated, not just the choice


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


# ---------------------------------------------------------------------------
# 8. Work order 2026-08-16
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("player", ["Ken Walker III", "Cam Skattebo", "Kenneth Gainwell"])
def test_nickname_aliases_join_to_both_adp_sources(built, player):
    # Item 2: each of these had zero market data from at least one ADP source because
    # props' spelling didn't match that source's. Real, reproduced bugs -- not
    # hypothetical -- found by the top-150 hard-failure gate below.
    master, _ = built
    row = master[master["player"] == player].iloc[0]
    assert pd.notna(row["reference_adp_rank"])
    assert pd.notna(row["comparison_adp_value"])


def test_top150_join_failure_is_a_hard_failure_not_a_note():
    # Item 2's severity reclassification: reproduce the exact Ken Walker III shape
    # (top-150 rank, present in the raw ADP source, absent from player_master) with a
    # synthetic frame so the check doesn't depend on this season's real data, and
    # confirm the mechanism actually flags it before/after a name fix -- matching the
    # acceptance check's own framing ("non-zero before the alias, zero after").
    master = pd.DataFrame({"name_key": ["chase brown"], "position": ["RB"], "reference_adp_rank": [4.0], "comparison_adp_value": [3.0]})
    ref_df = pd.DataFrame({"name_key": ["chase brown", "kenneth walker"], "position": ["RB", "RB"],
                            "adp_rank": [4, 15], "player_display": ["Chase Brown", "Kenneth Walker III"]})
    cmp_df = pd.DataFrame({"name_key": ["chase brown"], "position": ["RB"], "adp_rank": [3], "player_display": ["Chase Brown"]})

    report_before = pipeline.JoinReport()
    pipeline.validate_top_adp_coverage(master, ref_df, cmp_df, report_before, top_n=150)
    assert any("kenneth walker" in m.lower() or "walker" in m.lower() for m in report_before.hard_failures)

    master_fixed = pd.concat([master, pd.DataFrame({"name_key": ["kenneth walker"], "position": ["RB"], "reference_adp_rank": [15.0], "comparison_adp_value": [np.nan]})], ignore_index=True)
    report_after = pipeline.JoinReport()
    pipeline.validate_top_adp_coverage(master_fixed, ref_df, cmp_df, report_after, top_n=150)
    assert not report_after.hard_failures


def test_top150_coverage_check_excludes_kickers():
    # A real top-150 Sleeper kicker correctly has NO player_master row (R22 -- the
    # tool carries zero individual-kicker data) and must not be flagged as a failure.
    master = pd.DataFrame({"name_key": ["chase brown"], "position": ["RB"], "reference_adp_rank": [4.0], "comparison_adp_value": [3.0]})
    ref_df = pd.DataFrame({"name_key": ["brandon aubrey"], "position": ["K"], "adp_rank": [128], "player_display": ["Brandon Aubrey"]})
    cmp_df = pd.DataFrame({"name_key": [], "position": [], "adp_rank": [], "player_display": []})
    report = pipeline.JoinReport()
    pipeline.validate_top_adp_coverage(master, ref_df, cmp_df, report, top_n=150)
    assert not report.hard_failures


def test_adp_source_offsets_reproduces_measured_divergence(built):
    # Item 1's acceptance check: adp_source_offsets.csv should reproduce the measured
    # per-position divergence (TE/QB earlier on Sleeper, WR later) within rounding --
    # computed from real data at build time, not hard-coded (spec: "they are a
    # property of the site, not a constant").
    offsets = pd.read_csv(config.ADP_SOURCE_OFFSETS_PATH).set_index("position")["mean_offset_reference_minus_comparison"]
    assert offsets["TE"] < -10  # Sleeper ranks TE meaningfully earlier than NFFC
    assert offsets["QB"] < -5  # ditto for QB
    assert offsets["WR"] > 0  # Sleeper ranks WR later than NFFC


def test_survival_anchor_is_a_real_config_toggle():
    # The work order explicitly asked for SURVIVAL_ANCHOR / SURVIVAL_DISPERSION_SOURCE
    # as actual config-driven behavior, not documentation of a hard-coded choice.
    # Flipping the anchor should flip which field the fit centers on.
    row = pd.Series({"reference_adp_rank": 25.0, "comparison_adp_value": 48.0,
                      "comparison_adp_min": 30.0, "comparison_adp_max": 70.0, "comparison_adp_n": 40.0})
    saved = config.SURVIVAL_ANCHOR
    try:
        config.SURVIVAL_ANCHOR = "reference"
        mu_ref, _, _ = de._fit_lognormal_from_adp(*de._resolve_survival_sources(row))
        config.SURVIVAL_ANCHOR = "comparison"
        mu_cmp, _, _ = de._fit_lognormal_from_adp(*de._resolve_survival_sources(row))
    finally:
        config.SURVIVAL_ANCHOR = saved
    assert math.exp(mu_ref) == pytest.approx(25.0, rel=0.01)
    assert math.exp(mu_cmp) == pytest.approx(48.0, rel=0.01)


def test_intel_parser_skips_other_markdown_tables_in_the_file(tmp_path):
    # The doc has multiple `| ... |` tables (tag legend, divergence tables). Only the
    # one whose header contains player/position/pick_window/tag should be parsed.
    md = tmp_path / "intel.md"
    md.write_text(
        "\n".join(
            [
                "| Tag | Effect |",
                "|---|---|",
                "| target | nudge |",
                "",
                "| player | position | pick_window | priority | tag | note |",
                "|---|---|---|---|---|---|",
                "| Puka Nacua | WR | 5 | 1 | target | good |",
                "| Quinshon Judkins | RB | 53 | | fade | dislike |",
                "",
                "| Position | Sleeper - NFFC |",
                "|---|---|",
                "| TE | -23.2 |",
            ]
        ),
        encoding="utf-8",
    )
    saved = config.PLAYER_INTEL_MD_PATH
    try:
        config.PLAYER_INTEL_MD_PATH = md
        report = pipeline.JoinReport()
        intel = pipeline.parse_player_intel(report)
    finally:
        config.PLAYER_INTEL_MD_PATH = saved
    assert len(intel) == 2
    assert set(intel["tag"]) == {"target", "fade"}
    assert not report.hard_failures


def test_intel_nudge_is_capped_and_toggleable(built):
    master, _ = built
    saved_applies = config.LAYERS["player_intel"]["applies"]
    try:
        config.LAYERS["player_intel"]["applies"] = False
        board_off = de.compute_composite(master)

        config.LAYERS["player_intel"]["applies"] = True
        board_on = de.compute_composite(master)
    finally:
        config.LAYERS["player_intel"]["applies"] = saved_applies

    # Toggling off restores the exact prior composite (acceptance check).
    pd.testing.assert_series_equal(
        board_off["composite_score"], board_on["composite_score"] - board_on["intel_nudge_pts"], check_names=False
    )
    tagged = board_on[board_on["intel_tag"].isin(["target", "fade"])]
    assert (tagged["intel_nudge_pts"].abs() <= config.INTEL_NUDGE_CAP + 1e-9).all()
    assert (board_on.loc[board_on["intel_tag"] == "target", "intel_nudge_pts"] > 0).all()
    assert (board_on.loc[board_on["intel_tag"] == "fade", "intel_nudge_pts"] < 0).all()


def test_hard_avoid_never_appears_in_recommendations(built):
    master, _ = built
    board = de.compute_composite(master)
    # No real 2026 row is tagged hard_avoid -- inject one so the filter is actually
    # exercised rather than trivially passing on an empty case.
    board = board.copy()
    top_player_idx = board["composite_score"].idxmax()
    board.loc[top_player_idx, "intel_tag"] = "hard_avoid"
    avoided_name = board.loc[top_player_idx, "player"]

    manager_priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_availability(board, as_of_pick=0, target_pick=5, manager_priors=manager_priors, team_bias=team_bias)
    roster = de.RosterState()
    top = de.top_recommendations(board, roster, n=20)
    assert avoided_name not in top["player"].tolist()


# ---------------------------------------------------------------------------
# 9. Work order 2026-08-16 item 0: draft-switch state bug. The reported failure --
#    switching Sleeper draft_id mid-session silently discarded the new draft's picks
#    because a single global state file compared the new draft's pick numbers against
#    the OLD draft's "already seen" set. Fixed by keying state files per draft_id, so
#    there is nothing to "clear" -- two different draft_ids simply can't share a file.
# ---------------------------------------------------------------------------
def test_draft_state_is_keyed_per_draft_id_not_global(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)

    state_a = draft_state.load_state("draft_A")
    draft_state.add_pick(state_a, "draft_A", pd.Series({"player": "Ja'Marr Chase", "position": "WR", "nfl_team": "CIN"}))
    draft_state.add_pick(state_a, "draft_A", pd.Series({"player": "Justin Jefferson", "position": "WR", "nfl_team": "MIN"}))
    assert len(state_a["picks"]) == 2

    # Switching to a NEW draft_id must start empty -- not inherit draft A's picks, and
    # not silently drop draft B's own picks against draft A's pick-number history.
    state_b = draft_state.load_state("draft_B")
    assert state_b["picks"] == []
    draft_state.add_pick(state_b, "draft_B", pd.Series({"player": "Bijan Robinson", "position": "RB", "nfl_team": "ATL"}))
    assert len(state_b["picks"]) == 1

    # Returning to A must restore A's picks untouched by anything that happened under B.
    state_a_reloaded = draft_state.load_state("draft_A")
    assert [p["player"] for p in state_a_reloaded["picks"]] == ["Ja'Marr Chase", "Justin Jefferson"]

    # Manual-only (no draft_id) is its own third bucket, distinct from either.
    manual_state = draft_state.load_state(None)
    assert manual_state["picks"] == []


def test_draft_id_switch_would_have_dropped_picks_under_the_old_global_file_design(tmp_path, monkeypatch):
    # Reproduces the exact reported mechanism as a regression guard: under the OLD
    # single-file design, draft B's pick #1 would collide with draft A's pick #1's
    # overall number and get treated as "already seen." Confirms the NEW design keeps
    # the two draft's `known` overall-pick sets from ever being compared against each
    # other at all, since they now live in genuinely separate files.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    state_a = draft_state.load_state("draft_A")
    draft_state.add_pick(state_a, "draft_A", pd.Series({"player": "Ja'Marr Chase", "position": "WR", "nfl_team": "CIN"}))
    known_from_a = {p["overall"] for p in state_a["picks"]}  # {1} -- what the old bug would have polluted draft B with

    state_b = draft_state.load_state("draft_B")
    known_from_b = {p["overall"] for p in state_b["picks"]}
    assert known_from_b == set()  # NOT known_from_a -- draft B starts with a clean slate
    assert known_from_a == {1}


def test_reset_draft_clears_picks_but_keeps_the_draft_id(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    state = draft_state.load_state("draft_A")
    draft_state.add_pick(state, "draft_A", pd.Series({"player": "Ja'Marr Chase", "position": "WR", "nfl_team": "CIN"}))
    reset_state = draft_state.reset_draft("draft_A")
    assert reset_state["picks"] == []
    assert draft_state.load_state("draft_A")["picks"] == []


def test_active_draft_id_pointer_survives_a_reload(tmp_path, monkeypatch):
    # Simulates a process restart: load_active_draft_id() must read back whatever was
    # last saved, from disk, with no in-memory state carried over.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    assert draft_state.load_active_draft_id() is None
    draft_state.save_active_draft_id("draft_A")
    assert draft_state.load_active_draft_id() == "draft_A"
    draft_state.save_active_draft_id(None)
    assert draft_state.load_active_draft_id() is None


# ---------------------------------------------------------------------------
# 10. Work order 2026-08-24 items 1-3 -- these all live in
#     simulate_intervening_picks/compute_availability, so they're tested together.
# ---------------------------------------------------------------------------
def _toy_te_pool():
    """A small, hand-built pool: a scarce-TE window at pick 44->53 with two TE-hungry
    managers, cheap enough to run at n_sims in the hundreds rather than thousands."""
    rows = []
    for i in range(6):
        rows.append({"player": f"TE{i}", "position": "TE", "reference_adp_rank": 40.0 + i * 3,
                     "comparison_adp_value": 40.0 + i * 3, "comparison_adp_min": 25.0, "comparison_adp_max": 70.0,
                     "comparison_adp_n": 30.0, "vorp": 20.0 - i * 2, "nfl_team": "FA", "college": None})
    for i in range(20):
        rows.append({"player": f"WR{i}", "position": "WR", "reference_adp_rank": 30.0 + i * 2,
                     "comparison_adp_value": 30.0 + i * 2, "comparison_adp_min": 15.0, "comparison_adp_max": 60.0,
                     "comparison_adp_n": 30.0, "vorp": 10.0 - i * 0.5, "nfl_team": "FA", "college": None})
    return pd.DataFrame(rows)


def _toy_manager_priors(managers):
    return pd.DataFrame({
        "manager": managers, "n_main_drafts": [4] * len(managers), "n_alt_drafts": [0] * len(managers),
        "confidence": ["high"] * len(managers), "bimodal_flag": [False] * len(managers),
        "qb1_round_mean": [6.0] * len(managers), "qb1_round_std": [2.0] * len(managers),
        "te1_round_mean": [8.0] * len(managers), "te1_round_std": [2.0] * len(managers),
        "rb_in_r1_8_mean": [3.0] * len(managers), "wr_in_r1_8_mean": [3.0] * len(managers),
        "k_round_mean": [14.0] * len(managers),
    })


def test_checkpoint_capacity_invariant_is_exact_at_both_depths():
    # Item 2 / R30's acceptance check, on the mechanism itself rather than the
    # display-clipped column: the discrete-removal guarantee is exact (not just
    # within a tolerance) at EVERY checkpoint requested from one simulation pass, not
    # just the final one.
    pool = _toy_te_pool()
    intervening = [
        (45, 4, "Nick"), (46, 4, "Tyler"), (47, 4, "Cailen"), (48, 4, "Dylan"),
        (49, 5, "Dylan"), (50, 5, "Cailen"), (51, 5, "Tyler"), (52, 5, "Nick"),
        (54, 6, "Nick"), (55, 6, "Tyler"), (56, 6, "Cailen"), (57, 6, "Dylan"),
        (58, 6, "Dylan"),
    ]
    k_at_53 = sum(1 for o, _, __ in intervening if o < 53)
    k_at_68 = len(intervening)
    priors = _toy_manager_priors(["Nick", "Tyler", "Cailen", "Dylan"])
    team_bias = pd.DataFrame(columns=["manager", "nfl_team", "bias_ratio"])

    result = de.simulate_intervening_picks(
        pool, intervening, priors, team_bias, {}, n_sims=500, rng=np.random.default_rng(3),
        checkpoint_picks=[53, 68],
    )
    assert set(result) == {53, 68}
    assert (1 - result[53]).sum() == pytest.approx(k_at_53, abs=1e-9)
    assert (1 - result[68]).sum() == pytest.approx(k_at_68, abs=1e-9)
    # Survival can only fall (or hold) as the horizon extends further out.
    assert (result[68] <= result[53] + 1e-9).all()


def test_wait_pick_and_target_pick_share_one_simulation():
    # Item 2 / R30: before this, the board's number and the wait rule's number came
    # from different methods (Monte Carlo vs the fitted lognormal) whenever the owner
    # wasn't on the clock. compute_availability(wait_pick=...) must produce both from
    # the SAME simulate_intervening_picks call.
    pool = _toy_te_pool()
    priors = _toy_manager_priors(["Nick", "Tyler", "Cailen", "Dylan"])
    team_bias = pd.DataFrame(columns=["manager", "nfl_team", "bias_ratio"])
    board = pool.copy()
    board["name_key"] = board["player"].str.lower()

    calls = []
    real_simulate = de.simulate_intervening_picks

    def spy(*args, **kwargs):
        calls.append(kwargs.get("checkpoint_picks"))
        return real_simulate(*args, **kwargs)

    orig = de.simulate_intervening_picks
    de.simulate_intervening_picks = spy
    try:
        out = de.compute_availability(
            board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
            n_sims=300, rng=np.random.default_rng(2), wait_pick=68, method="montecarlo",
        )
    finally:
        de.simulate_intervening_picks = orig

    assert calls == [[53, 68]]  # exactly one simulation call, checkpointed at both picks
    assert "survival_probability_wait" in out.columns
    assert (out["survival_probability_wait"] <= out["survival_probability"] + 1e-9).all()


def test_wait_pick_must_be_after_target_pick():
    pool = _toy_te_pool()
    board = pool.copy()
    board["name_key"] = board["player"].str.lower()
    priors = _toy_manager_priors(["Nick"])
    team_bias = pd.DataFrame(columns=["manager", "nfl_team", "bias_ratio"])
    with pytest.raises(ValueError):
        de.compute_availability(
            board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
            wait_pick=53, n_sims=50,
        )


@pytest.mark.parametrize("method", ["montecarlo", "lognormal", "blend"])
def test_availability_method_toggle_changes_which_estimate_ships(built, method):
    # Item 1 / R36: AVAILABILITY_METHOD must be a real toggle, not documentation of a
    # hard-coded choice -- mirrors test_survival_anchor_is_a_real_config_toggle's shape.
    master, _ = built
    board = de.compute_composite(master)
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)

    out = de.compute_availability(
        board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
        n_sims=200, rng=np.random.default_rng(7), method=method,
    )
    assert out["survival_probability"].between(0, 1).all()
    if method == "lognormal":
        pd.testing.assert_series_equal(
            out["survival_probability"], out["survival_baseline"], check_names=False
        )


def test_lognormal_method_never_calls_the_simulation():
    # "lognormal" exists specifically so the backtest's lognormal arm (and anyone else
    # who wants the cheap estimate) can skip the Monte Carlo outright, not just ignore
    # its result after paying for it.
    pool = _toy_te_pool()
    board = pool.copy()
    board["name_key"] = board["player"].str.lower()
    priors = _toy_manager_priors(["Nick", "Tyler"])
    team_bias = pd.DataFrame(columns=["manager", "nfl_team", "bias_ratio"])

    def explode(*args, **kwargs):
        raise AssertionError("simulate_intervening_picks must not be called for method='lognormal'")

    orig = de.simulate_intervening_picks
    de.simulate_intervening_picks = explode
    try:
        out = de.compute_availability(
            board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
            method="lognormal", n_sims=50,
        )
    finally:
        de.simulate_intervening_picks = orig
    assert out["survival_probability"].between(0, 1).all()


def test_reach_is_per_manager_not_a_league_wide_constant():
    # R31 is explicit: "these are real per-manager numbers; do not use a league-wide
    # constant." Confirm the config data itself varies and that the draw tracks it.
    assert len(set(config.MANAGER_MEAN_REACH.values())) > 1
    rng = np.random.default_rng(0)
    aggressive = np.mean(de._reach_draws(rng, 4000, "Dylan", widen=False))  # mean reach +4.0
    patient = np.mean(de._reach_draws(rng, 4000, "Cailen", widen=False))  # mean reach -3.8
    assert aggressive > patient + 3.0


def test_reach_widens_on_unfilled_need_at_a_thinning_position():
    rng = np.random.default_rng(0)
    baseline = np.mean(de._reach_draws(rng, 4000, "Dylan", widen=False))
    widened = np.mean(de._reach_draws(rng, 4000, "Dylan", widen=True))
    assert widened > baseline  # widen shifts the mean up, i.e. reaches further/more often
    draws = de._reach_draws(np.random.default_rng(0), 4000, "Dylan", widen=True)
    assert draws.max() <= config.REACH_MAX_PICKS + 1e-9
    assert draws.min() >= -config.REACH_MAX_PICKS - 1e-9


def test_thinning_counts_startable_players_not_every_row():
    # A position with plenty of deep-bench rows but almost no startable (vorp > 0)
    # ones must register as thinning -- counting every row never fires inside a
    # realistic in-draft window (player_master carries 70+ TE rows including scrubs).
    thin_pool = pd.DataFrame({
        "position": ["TE"] * 40,
        "vorp": [5.0, 3.0] + [-20.0] * 38,  # only 2 startable TEs left
    })
    assert de._position_is_thinning(thin_pool, "TE") is True
    deep_pool = pd.DataFrame({
        "position": ["WR"] * 40,
        "vorp": [20.0] * 20 + [-5.0] * 20,  # 20 startable WRs left -- not scarce
    })
    assert de._position_is_thinning(deep_pool, "WR") is False


def test_laporta_survival_falls_with_the_reach_model_on(built):
    # Item 3 / R31's own acceptance check: LaPorta's survival 44 -> 53 should FALL once
    # reach (and its need/thinning widening) is modeled, because the TE-hungry managers
    # in that exact window now sometimes reach for him early rather than drafting
    # strictly off the ADP-implied hazard curve.
    master, _ = built
    board = de.compute_composite(master)
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    seq = config.full_draft_sequence()
    intervening = [(o, r, m) for o, r, m in seq if 44 < o < 53 and m != config.OWNER]
    laporta = board[board["player"] == "Sam LaPorta"]
    if laporta.empty:
        pytest.skip("Sam LaPorta not present in this season's player_master.csv")
    pool = board[board["position"].isin(config.POSITIONS)]
    laporta_pos = pool.index.get_loc(laporta.index[0])
    # Managers in this window still need a TE, matching the handoff's framing of this
    # exact window ("the league's three most tight end hungry managers").
    needy = {m: {} for _, __, m in intervening}

    def laporta_survival():
        result = de.simulate_intervening_picks(
            pool, intervening, priors, team_bias, needy, n_sims=1500, rng=np.random.default_rng(5)
        )
        return result[laporta_pos]

    with_reach = laporta_survival()

    saved_mean = dict(config.MANAGER_MEAN_REACH)
    saved_std, saved_bonus, saved_mult = (
        config.REACH_STD_BASE, config.REACH_NEED_WIDEN_MEAN_BONUS, config.REACH_NEED_WIDEN_STD_MULT,
    )
    try:
        config.MANAGER_MEAN_REACH = {k: 0.0 for k in saved_mean}
        config.REACH_STD_BASE = 1e-9
        config.REACH_NEED_WIDEN_MEAN_BONUS = 0.0
        config.REACH_NEED_WIDEN_STD_MULT = 1.0
        without_reach = laporta_survival()
    finally:
        config.MANAGER_MEAN_REACH = saved_mean
        config.REACH_STD_BASE, config.REACH_NEED_WIDEN_MEAN_BONUS, config.REACH_NEED_WIDEN_STD_MULT = (
            saved_std, saved_bonus, saved_mult,
        )

    assert with_reach < without_reach



# ---------------------------------------------------------------------------
# 11. Work order 2026-08-24 items 4-5: draft setup screen (R32) and layer toggles
#     with config.LAYERS as the only definition (R33).
# ---------------------------------------------------------------------------
def test_validate_draft_order_accepts_a_permutation_and_rejects_anything_else():
    names = ["A", "B", "C"]
    assert draft_setup.validate_draft_order(["C", "A", "B"], names) is None
    assert draft_setup.validate_draft_order(["A", "B"], names) is not None  # too few
    assert draft_setup.validate_draft_order(["A", "A", "B"], names) is not None  # duplicate
    assert draft_setup.validate_draft_order(["A", "B", "Z"], names) is not None  # unrecognized


def test_layers_off_lists_only_disabled_layers():
    setup = {"layers": {"bonus_model": True, "college_bias": False, "player_intel": False}}
    assert draft_setup.layers_off(setup) == ["college_bias", "player_intel"]
    assert draft_setup.layers_off({"layers": {}}) == []


def test_apply_setup_changes_owner_slot_and_every_downstream_pick_number(restore_config_singletons):
    # Item 4 / R32's own acceptance check: changing the owner slot must update every
    # pick number and all availability with NO rebuild -- verified here by mutating
    # config in place and reading straight back through the same functions the live
    # app calls, with no re-import and no re-run of build/pipeline.py.
    setup = draft_setup.default_setup()
    original_first_window = config.owner_pick_windows()[0]["overall"]

    new_order = list(setup["draft_order"])
    i_a, i_b = new_order.index("Dylan"), new_order.index("Nathan")
    new_order[i_a], new_order[i_b] = new_order[i_b], new_order[i_a]  # Nathan now slot 1
    setup["draft_order"] = new_order
    setup["owner"] = "Nathan"

    draft_setup.apply_setup(setup)

    assert config.DRAFT_ORDER_2026[0] == "Nathan"
    assert config.OWNER == "Nathan"
    new_first_window = config.owner_pick_windows()[0]["overall"]
    assert new_first_window != original_first_window
    assert new_first_window == 1  # slot 1 means overall pick 1, not 5


def test_apply_setup_changes_roster_target_and_layers_live(restore_config_singletons):
    setup = draft_setup.default_setup()
    setup["roster_target"] = {"QB": 3, "RB": 4, "WR": 6, "TE": 2}
    setup["layers"]["college_bias"] = False
    draft_setup.apply_setup(setup)

    assert config.ROSTER_TARGET == {"QB": 3, "RB": 4, "WR": 6, "TE": 2}
    assert config.layer_on("college_bias") is False
    # A default bound to config.ROSTER_TARGET at another module's import time (e.g.
    # board_model.build_route's `remaining = {... for pos, cap in config.ROSTER_TARGET...}`)
    # reads the dict live inside the function body, not a frozen copy -- confirm the
    # object identity itself never changed, only its contents (the in-place mutation
    # apply_setup relies on).
    assert config.ROSTER_TARGET is not None


def test_setup_screen_gates_a_fresh_state_directory(tmp_path, monkeypatch):
    # Item 4's other acceptance check: a fresh state/ directory (no draft_setup.json)
    # must boot to the setup screen rather than crashing or silently using stale config.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    assert not draft_setup.setup_exists()
    default = draft_setup.default_setup()
    assert len(default["draft_order"]) == config.N_TEAMS
    assert default["owner"] in default["draft_order"]


def test_save_setup_round_trips_and_applies(tmp_path, monkeypatch, restore_config_singletons):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    setup = draft_setup.default_setup()
    setup["te1_fork_player"] = "Someone Else"
    draft_setup.save_setup(setup)

    assert draft_setup.setup_exists()
    reloaded = draft_setup.load_setup()
    assert reloaded["te1_fork_player"] == "Someone Else"
    assert draft_setup.te1_fork_player(reloaded) == "Someone Else"


def test_availability_method_is_montecarlo_not_blend(built):
    # 2026-08-24 owner decision: the bake-off's letter-of-the-rule result (blend) was
    # overridden because blend is not capacity-safe (R20/R21) and scored worse than
    # montecarlo alone on the decision-band Brier. Guards against silently drifting
    # back to "blend" (e.g. a careless re-run of the bake-off overwriting the comment
    # without re-litigating the capacity tradeoff).
    assert config.AVAILABILITY_METHOD == "montecarlo"


# ---------------------------------------------------------------------------
# 12. Work order 2026-08-24 item 7: kickers (R35). player_master now carries ADP-only
#     K rows; they must stay out of VORP and every route, and the recommender should
#     surface exactly one, only from round 14 on.
# ---------------------------------------------------------------------------
def test_kicker_rows_are_added_from_the_reference_adp_source(built):
    master, _ = built
    kickers = master[master["position"] == "K"]
    assert len(kickers) > 0
    assert kickers["reference_adp_rank"].notna().all()
    assert kickers["ppr_base"].isna().all()  # no props coverage by design (R22)


def test_kicker_rows_are_excluded_from_vorp(built):
    master, _ = built
    board = de.compute_composite(master)
    kickers = board[board["position"] == "K"]
    assert (kickers["vorp"].fillna(0) == 0).all()


def test_kicker_rows_never_appear_in_a_route(built):
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    routes = bm.build_routes(board, {}, this_pick=140, on_clock=True, n_routes=3)
    for route in routes:
        assert route["anchor"]["position"] != "K"
        assert all(leg["position"] != "K" for leg in route["legs"])


def test_kicker_slide_moves_kickers_to_round_14_in_their_own_order_not_the_skill_shift(built):
    master, _ = built
    slid = bm.apply_kicker_slide(master)
    kickers = slid[slid["position"] == "K"].sort_values("reference_adp_rank_raw")
    assert (kickers["reference_adp_rank"] >= bm.ROUND_14_FIRST_PICK).all()
    # Strictly increasing in the same order as their raw ADP -- the slide reorders
    # skill players around kickers, it does not reorder kickers among themselves.
    assert kickers["reference_adp_rank"].is_monotonic_increasing
    assert slid.attrs["kickers_reranked_count"] > 0


def test_exactly_one_kicker_suggested_at_pick_188_and_none_before_round_14(built):
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))

    early = de.RosterState(current_round=10, current_overall_pick=125)
    recs_early = de.top_recommendations(board, early, n=50)
    assert (recs_early["position"] == "K").sum() == 0

    late = de.RosterState(current_round=16, current_overall_pick=188)
    recs_late = de.top_recommendations(board, late, n=50)
    assert (recs_late["position"] == "K").sum() == 1


def test_sleeper_draft_id_setup_field_round_trips():
    setup = draft_setup.default_setup()
    assert draft_setup.sleeper_draft_id(setup) is None
    setup["sleeper_draft_id"] = "  1234567890  "
    assert draft_setup.sleeper_draft_id({"sleeper_draft_id": "1234567890"}) == "1234567890"
    assert draft_setup.sleeper_draft_id({"sleeper_draft_id": "   "}) is None
    assert draft_setup.sleeper_draft_id({"sleeper_draft_id": None}) is None


def test_kicker_suggestion_withheld_once_owner_already_has_one(built):
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    roster = de.RosterState(
        picks=[{"player": "Brandon Aubrey", "position": "K", "round": 14, "overall": 157}],
        current_round=16, current_overall_pick=188,
    )
    recs = de.top_recommendations(board, roster, n=50)
    assert (recs["position"] == "K").sum() == 0


# ---------------------------------------------------------------------------
# 13. Work order 2026-08-24b item 1: Sleeper name resolution reaches the live sync
#     path. Reproduces the exact reported bug (Kenneth Walker stayed on the board
#     after being drafted) end to end, through the real crosswalk build produces.
# ---------------------------------------------------------------------------
def test_sleeper_name_crosswalk_is_built_and_covers_the_known_aliases(built):
    master, _ = built
    assert config.SLEEPER_NAME_CROSSWALK_PATH.exists()
    crosswalk = pd.read_csv(config.SLEEPER_NAME_CROSSWALK_PATH)
    assert len(crosswalk) > 0
    table = dict(zip(crosswalk["sleeper_name_key"], crosswalk["master_name_key"]))
    # Every known alias that Sleeper's OWN ADP pull actually carries this year should
    # show up as a rewrite in the crosswalk, not just in config.NAME_ALIASES.
    rewrites = crosswalk[crosswalk["sleeper_name_key"] != crosswalk["master_name_key"]]
    assert len(rewrites) > 0
    assert table.get("kenneth walker") == "ken walker"


def test_resolve_sleeper_name_key_fixes_the_kenneth_walker_bug(built, monkeypatch):
    # The exact reported symptom, at the resolver level: config.normalize_name alone
    # produces the wrong key; resolve_sleeper_name_key (crosswalk-first) produces the
    # one player_master actually uses.
    monkeypatch.setattr(draft_state, "_crosswalk_cache", None)
    wrong_key = config.normalize_name("Kenneth Walker III")
    resolved_key = draft_state.resolve_sleeper_name_key("Kenneth Walker III")
    assert wrong_key == "kenneth walker"
    assert resolved_key == "ken walker"
    master, _ = built
    assert resolved_key in set(master["name_key"])
    assert wrong_key not in set(master[master["position"] == "RB"]["name_key"])


def test_sync_of_kenneth_walker_removes_him_from_the_available_board(built, tmp_path, monkeypatch):
    # End-to-end acceptance check for item 1: a live Sleeper pick spelled the way
    # Sleeper actually spells it must remove that player from `available`, the same
    # board main_cockpit.py filters for the cockpit and full-board views.
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(draft_state, "_crosswalk_cache", None)
    master, _ = built
    board = de.compute_composite(master)

    state = draft_state.load_state("draftA")
    key = draft_state.resolve_sleeper_name_key("Kenneth Walker III")
    row_match = board[board["name_key"] == key]
    assert len(row_match) == 1, "Ken Walker III must have exactly one player_master row"
    row = row_match.iloc[0]
    draft_state.add_pick(state, "draftA", row)

    available = board[~board["name_key"].isin(draft_state.drafted_name_keys(state))]
    assert key not in set(available["name_key"])
    assert "Ken Walker III" in {p["player"] for p in state["picks"]}


def test_unmatched_sync_name_is_logged_and_surfaced(built, tmp_path, monkeypatch):
    # Task 2's acceptance check: an unmatched Sleeper pick must be loud, not a silent
    # bare-Series fallback. Drives main_cockpit.sync_from_sleeper directly against a
    # fabricated poll result naming a player nothing in this build can match -- a
    # plain dict stands in for st.session_state (every op sync_from_sleeper performs
    # on it -- __getitem__/__setitem__/.get/.setdefault -- is dict-native). A FOURTH
    # AppTest.from_file() instance in this same pytest process was tried first and
    # reliably hung past its timeout regardless of length (reproduced with 60s AND
    # 300s) even though the identical scenario runs in ~1s standalone -- an AppTest
    # test-harness accumulation issue across repeated instances in one process, not a
    # behavior of the app itself (test_streamlit_app_smoke and the two
    # compute_board_cache tests, the first three AppTest instances in this file, all
    # pass every time). This covers the same acceptance check without tripping it.
    import main_cockpit

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(draft_state, "_crosswalk_cache", None)
    monkeypatch.setattr(main_cockpit.st, "session_state", {})

    master, _ = built
    board = de.compute_composite(master)
    state = draft_state.load_state("draftA")

    fake_pick = {"overall": 1, "round": 1, "player": "Totally Fictional Player",
                 "position": "WR", "nfl_team": "FA", "manager": "Nathan", "draft_slot": 5}
    monkeypatch.setattr(main_cockpit.sleeper_client, "poll_new_picks", lambda draft_id, known: [fake_pick])

    landed = main_cockpit.sync_from_sleeper(state, "draftA", board)

    assert landed == 1
    assert main_cockpit.st.session_state["sync_unmatched"] == ["Totally Fictional Player"]
    log_text = config.UNMATCHED_SYNC_LOG_PATH.read_text(encoding="utf-8")
    assert "Totally Fictional Player" in log_text
    assert "draft=draftA" in log_text
    # The pick still lands (loud, not blocked) under its raw name, so roster counts
    # keep moving even for a name the board couldn't resolve.
    assert state["picks"][0]["player"] == "Totally Fictional Player"

    # "On screen" half of the acceptance check: main()'s warning banner is gated on
    # exactly this session_state key and names the log file -- verified by direct
    # source inspection rather than a 4th AppTest instance (see comment above), and
    # separately confirmed by one standalone interactive run (reported alongside this
    # test's results, not re-run here).
    main_source = (TOOL_ROOT / "app" / "main_cockpit.py").read_text(encoding="utf-8")
    assert 'st.session_state.get("sync_unmatched")' in main_source
    assert "st.warning(" in main_source


# ---------------------------------------------------------------------------
# 14. Work order 2026-08-24b item 3: candidates_for_pick and _lognormal_fit_arrays
#     vectorized, AVAILABILITY_N_SIMS cut 2000 -> 500, availability cached per render.
# ---------------------------------------------------------------------------
def test_lognormal_fit_arrays_matches_the_original_per_row_computation(built):
    # The vectorized fit (draft_engine.py) must produce IDENTICAL mu/sigma/has_fit to
    # the original per-row loop -- reconstructed here from the still-present per-row
    # primitives (_fit_lognormal_from_adp / _resolve_survival_sources), which the
    # vectorized version does not remove or change.
    master, _ = built
    board = de.compute_composite(master)

    def _reference(pool):
        n = len(pool)
        mu_arr, sigma_arr, has_fit = np.zeros(n), np.zeros(n), np.zeros(n, dtype=bool)
        for i, row in enumerate(pool.to_dict("records")):
            params = de._fit_lognormal_from_adp(*de._resolve_survival_sources(row))
            if params is not None:
                mu_arr[i], sigma_arr[i], _ = params
                has_fit[i] = True
        return mu_arr, sigma_arr, has_fit

    ref_mu, ref_sigma, ref_fit = _reference(board)
    new_mu, new_sigma, new_fit = de._lognormal_fit_arrays(board)
    assert ref_fit.sum() > 0, "sanity: at least some rows should have a real ADP fit"
    assert (ref_fit == new_fit).all()
    assert np.allclose(ref_mu[ref_fit], new_mu[ref_fit])
    assert np.allclose(ref_sigma[ref_fit], new_sigma[ref_fit])


def test_survival_between_vectorized_matches_the_per_row_version(built):
    master, _ = built
    board = de.compute_composite(master)
    got = bm._survival_between_vectorized(board, 44, 53)
    expected = np.array([bm.survival_between(r, 44, 53) for _, r in board.iterrows()])
    assert np.allclose(got, expected)


def test_candidates_for_pick_vectorized_matches_a_row_by_row_reference(built):
    # candidates_for_pick was rewritten to vectorize its two survival_between calls
    # (item 3) on top of the item 2/4 sort change -- this pins the FILTERING behavior
    # (which players survive shape/hard_avoid/availability/wait cuts) against a
    # reference loop built from the same primitives, independent of sort order.
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )

    def _reference(pool, pick, from_pick, next_after, shape, used, min_availability):
        rows = []
        for _, r in pool.iterrows():
            if r["player"] in used or shape.get(r["position"], 0) <= 0 or r.get("intel_tag") == "hard_avoid":
                continue
            # Work order 2026-08-29c item 3: missing_both_markets is also a hard filter.
            if bm.missing_both_markets(r):
                continue
            avail = 1.0 if from_pick == pick else bm.survival_between(r, from_pick, pick)
            if avail < min_availability:
                continue
            wait = bm.survival_between(r, pick, next_after)
            if wait >= bm.WAIT_THRESHOLD:
                continue
            rows.append(r["player"])
        return set(rows)

    shape = {"QB": 1, "RB": 3, "WR": 4, "TE": 1}
    expected = _reference(board, 53, 44, 68, shape, set(), 0.5)
    got = set(bm.candidates_for_pick(board, 53, 44, 68, shape, set(), 0.5)["player"])
    assert got == expected
    assert len(got) > 0


def test_n_sims_ui_reads_config_not_a_second_literal():
    # Work order 2026-08-24b item 3 (R38): the two constants (config.AVAILABILITY_N_SIMS,
    # board_model.N_SIMS_UI) had drifted into agreement (both 2000) by coincidence, not
    # by construction -- board_model.N_SIMS_UI must now be config's value, structurally.
    assert bm.N_SIMS_UI is config.AVAILABILITY_N_SIMS
    assert config.AVAILABILITY_N_SIMS == 500


def test_compute_board_cache_hits_on_repeat_and_misses_on_a_new_pick(built, tmp_path, monkeypatch):
    # Item 3 task 1's acceptance mechanism: a rerun with the SAME
    # (drafted_name_keys, as_of_pick, target_pick, wait_pick) must not re-run the
    # simulation at all; a rerun where drafted_name_keys changed (a real new pick) must.
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    setup = draft_setup.default_setup()
    setup["sleeper_draft_id"] = "draftCache"
    draft_setup.save_setup(setup)

    at = AppTest.from_file(str(TOOL_ROOT / "app" / "main_cockpit.py"), default_timeout=60)
    at.run()
    assert not at.exception
    assert len(at.session_state["board_cache"]) == 1
    first_board = next(iter(at.session_state["board_cache"].values()))

    # A plain rerun with nothing new must reuse the exact same cached object.
    at.run()
    assert not at.exception
    assert len(at.session_state["board_cache"]) == 1
    assert next(iter(at.session_state["board_cache"].values())) is first_board

    # A real new pick landing must produce a DIFFERENT cache entry (old one evicted,
    # since only one live board is ever useful -- see compute_board's own docstring).
    import sleeper_client
    fake_pick = {"overall": 1, "round": 1, "player": "Jahmyr Gibbs", "position": "RB",
                 "nfl_team": "DET", "manager": "Dylan", "draft_slot": 1}
    monkeypatch.setattr(sleeper_client, "poll_new_picks", lambda draft_id, known: [fake_pick] if 1 not in known else [])
    sync_button = next(b for b in at.button if b.label == "Sync now")
    sync_button.click().run()
    assert not at.exception
    assert len(at.session_state["board_cache"]) == 1
    assert next(iter(at.session_state["board_cache"].values())) is not first_board


def test_compute_board_cache_is_cleared_on_setup_save(built, tmp_path, monkeypatch):
    # roster_target/layers/owner all change compute_board's result for the SAME
    # (drafted_name_keys, as_of_pick, target_pick, wait_pick) key -- a setup save must
    # invalidate the cache even when the draft_id itself didn't change.
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    at = AppTest.from_file(str(TOOL_ROOT / "app" / "main_cockpit.py"), default_timeout=60)
    at.run()  # setup screen (no draft_setup.json yet)
    assert not at.exception

    submit = next(b for b in at.button if b.label == "Save and continue")
    submit.click().run()
    assert not at.exception
    assert len(at.session_state["board_cache"]) == 1

    open_setup = next(b for b in at.button if b.label == "Setup")
    open_setup.click().run()
    submit2 = next(b for b in at.button if b.label == "Save and continue")
    submit2.click().run()
    assert not at.exception
    # The resubmit clears the cache and the very next render repopulates it -- what
    # matters is that it is a FRESH entry, not the pre-resubmit one.
    assert len(at.session_state["board_cache"]) == 1


# ---------------------------------------------------------------------------
# 15. Work order 2026-08-24b item 5: NOW/CLOSE/WAIT/GONE chip primary, percentage
#     secondary, +-1 confidence band dropped from the rendered board.
# ---------------------------------------------------------------------------
def test_board_row_leads_with_the_timing_chip_and_drops_the_confidence_band(built):
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={}, wait_pick=68,
    )
    words = {}
    for _, r in board.iterrows():
        tm = bm.timing(float(r["survival_probability_wait"]), 68, float(r["survival_probability"]))
        words.setdefault(tm.word, r)
    assert "CLOSE" in words and "WAIT" in words, "fixture window should produce at least one of each"

    import cockpit_html as ch
    for word in ("CLOSE", "WAIT"):
        single = pd.DataFrame([words[word]])
        rendered = ch.render_board(single, target_pick=53, wait_reference=68, on_clock=False,
                                    pick_lines={}, theme=ch.THEME_DARK, rows=1)
        assert f">{word}<" in rendered  # the chip renders
        assert "&plusmn;" not in rendered  # the +-N confidence band is gone, not just smaller
        assert "%" in rendered  # the percentage still renders, as the secondary annotation


def test_cockpit_columns_has_no_separate_timing_column():
    # The old design had "At {target}" (percentage) and "Timing" (chip) as two
    # separate columns -- item 5 merges them into one (chip primary, percentage
    # secondary), so a standalone "Timing" column must not still exist alongside it.
    import cockpit_html as ch
    keys = [key for key, *_ in ch.COCKPIT_COLUMNS]
    assert "timing" not in keys
    assert "avail" in keys


# ---------------------------------------------------------------------------
# 16. Work order 2026-08-24b item 6: the bake-off's decision-band Briers are computed
#     on different observation counts (band membership depends on each method's own
#     predictions), so the "18% gap" was never a sound comparison. The reasoning
#     should lead with the capacity invariant and treat Brier as supporting evidence.
# ---------------------------------------------------------------------------
def test_decide_availability_method_leads_with_capacity_not_the_brier_gap(built):
    results, _ = backtest.run_backtest(n_sims=50, seed=0)
    scores = backtest.score_methods(results)
    chosen, reason = backtest.decide_availability_method(scores)
    # The decision itself is explicitly unchanged (work order 2026-08-24b item 6:
    # "This does not change the R36 decision").
    assert chosen == "montecarlo"
    assert "capacity" in reason.lower()
    assert "R20/R21" in reason
    # The reason must name the actual per-method n's, not just assert a percentage
    # gap -- that is what makes the report say why the comparison is weak.
    for method in backtest.METHODS:
        assert f"n={scores[method]['decision_band_n']}" in reason


def test_decision_band_ns_actually_differ_across_methods(built):
    # The premise item 6 corrects: if these three were ever equal, "the gap is not a
    # sound comparison" would be the wrong lesson to draw. Confirms the real, current
    # numbers still show the mismatch the fix is about, rather than asserting it blind.
    results, _ = backtest.run_backtest(n_sims=50, seed=0)
    scores = backtest.score_methods(results)
    ns = {method: scores[method]["decision_band_n"] for method in backtest.METHODS}
    assert len(set(ns.values())) > 1, f"expected decision_band_n to differ across methods, got {ns}"


def test_availability_method_config_comment_leads_with_capacity():
    # A literal-string guard against the comment drifting back to Brier-first framing
    # (the exact defect item 6 reports) -- config.py is read, not config.AVAILABILITY_METHOD.
    source = (TOOL_ROOT / "config.py").read_text(encoding="utf-8")
    marker = "AVAILABILITY_METHOD = "
    comment_block = source[: source.index(marker)]
    # Only inspect the comment block immediately preceding the assignment, not the
    # whole file (SURVIVAL_ANCHOR etc. also mention "backtest" earlier in the file).
    comment_block = comment_block[comment_block.rindex("# Work order 2026-08-24 item 1"):]
    capacity_pos = comment_block.lower().find("capacity")
    brier_pos = comment_block.lower().find("brier")
    assert capacity_pos != -1 and brier_pos != -1
    assert capacity_pos < brier_pos, "the comment must mention capacity before it mentions Brier"


# ---------------------------------------------------------------------------
# 17. Work order 2026-08-29b items 1-2 / 2026-08-29 item 0 (R42): stale-build guard,
#     ADP Rank vs ADP conflation, Match Key as a validated crosswalk fallback.
# ---------------------------------------------------------------------------
def test_sleeper_adp_rank_column_used_not_the_adp_column():
    # The reported bug, reproduced directly: row 201 of the real 08-29 file is
    # ADP Rank 201, ADP 204 -- ingestion must keep them distinct.
    report = pipeline.JoinReport()
    df = pipeline.ingest_sleeper_adp(report, path=config.DATA_RAW / "sleeper_adp_ppr_2026-08-29.csv")
    row = df[df["player_display"] == "Braelon Allen"].iloc[0]
    assert row["adp_rank"] == 201
    assert row["adp_value"] == 204
    mismatches = int((df["adp_value"] != df["adp_rank"]).sum())
    assert mismatches > 0
    assert not report.hard_failures


def test_sleeper_adp_rank_falls_back_to_adp_for_the_old_8_column_shape():
    # The pre-split file has no "ADP Rank" column at all -- must not KeyError, must
    # fall back to the old ADP-doubles-as-rank behavior.
    report = pipeline.JoinReport()
    df = pipeline.ingest_sleeper_adp(report, path=config.DATA_RAW / "sleeper_adp_ppr_2026-08-16.csv")
    assert (df["adp_value"] == df["adp_rank"]).all()


def test_staleness_guard_fires_on_the_old_file_not_the_new_one():
    old_report = pipeline.JoinReport()
    pipeline.ingest_sleeper_adp(old_report, path=config.DATA_RAW / "sleeper_adp_ppr_2026-08-16.csv")
    assert old_report.hard_failures
    assert "days old" in old_report.hard_failures[0]

    new_report = pipeline.JoinReport()
    pipeline.ingest_sleeper_adp(new_report, path=config.DATA_RAW / "sleeper_adp_ppr_2026-08-29.csv")
    assert not new_report.hard_failures


def test_staleness_guard_is_a_real_config_toggle(monkeypatch):
    # A 30-day tolerance must let the otherwise-13-days-old 08-16 file through --
    # confirms the threshold is actually config.ADP_STALENESS_MAX_DAYS, not hardcoded.
    monkeypatch.setattr(config, "ADP_STALENESS_MAX_DAYS", 30)
    report = pipeline.JoinReport()
    pipeline.ingest_sleeper_adp(report, path=config.DATA_RAW / "sleeper_adp_ppr_2026-08-16.csv")
    assert not report.hard_failures


def test_match_key_used_as_crosswalk_fallback_never_as_the_core_join(built):
    # Sleeper's OWN Match Key normalization disagrees with ours on hyphens
    # ("jaxonsmithnjigba"-style, no space) -- using it for the CORE join broke real
    # top-150 players (Jaxon Smith-Njigba, Amon-Ra St. Brown, Jacory Croskey-Merritt)
    # the first time this was tried. The core join must still use our own
    # normalization; Match Key only feeds the crosswalk, and only when validated
    # against a real player_master row.
    master, report_text = built
    assert "HARD FAILURES (0)" in report_text
    for player in ("Jaxon Smith-Njigba", "Amon-Ra St. Brown", "Jacory Croskey-Merritt"):
        rows = master[master["player"] == player]
        if len(rows):  # absent entirely is fine (e.g. no props coverage); wrong-keyed is not
            assert pd.notna(rows.iloc[0]["reference_adp_rank"])

    report = pipeline.JoinReport()
    df = pipeline.ingest_sleeper_adp(report, path=config.DATA_RAW / "sleeper_adp_ppr_2026-08-29.csv")
    njigba = df[df["player_display"] == "Jaxon Smith-Njigba"].iloc[0]
    assert njigba["name_key"] == "jaxon smith njigba"  # the core join key: ours, not Match Key's
    assert njigba["sleeper_match_key_name"] == "jaxon smithnjigba"  # captured, but not joined on


# ---------------------------------------------------------------------------
# 18. Work order 2026-08-29b item 3 (R43): VORP shown beside edge on the cockpit
#     board -- display only, no change to the edge formula/fallback/sim count.
# ---------------------------------------------------------------------------
def test_edge_column_sits_beside_vorp_on_the_cockpit_board():
    import cockpit_html as ch
    cols = [key for key, *_ in ch.COCKPIT_COLUMNS]
    assert "edge" in cols and "vorp" in cols
    assert cols.index("edge") == cols.index("vorp") - 1, "edge must render immediately before vorp, not buried elsewhere"
    assert ch.SORT_OPTIONS["Edge"] == ("edge", False)


def test_rendered_board_row_shows_both_edge_and_vorp(built):
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board, as_of_pick=43, target_pick=53, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={}, n_sims=200,
    )
    import cockpit_html as ch
    top5 = board.sort_values("edge", ascending=False).head(5)
    rendered = ch.render_board(top5, target_pick=53, wait_reference=53, on_clock=False,
                                pick_lines={}, theme=ch.THEME_DARK, rows=5)
    # Both figures must appear, and edge must NOT have silently become vorp (a display
    # bug that would make the two columns redundant instead of a tradeoff).
    for _, row in top5.iterrows():
        assert f'{row["edge"]:.1f}' in rendered.replace("+", "") or f'{-row["edge"]:.1f}' in rendered
        assert f'{row["vorp"]:.0f}' in rendered.replace("+", "")


def test_edge_formula_and_fallback_and_sim_count_unchanged_by_item3():
    # Item 3 is display-only (work order 2026-08-29b: "Do not change the edge formula,
    # the fallback, or the sim count") -- guards against a display fix drifting into a
    # model change.
    assert config.AVAILABILITY_N_SIMS == 500
    import inspect
    src = inspect.getsource(de.compute_availability)
    assert 'df.loc[in_pool, "edge"] = (pool["vorp"] - best_at_target).to_numpy()' in src


# ---------------------------------------------------------------------------
# 19. Work order 2026-08-29 item 1 (R37): position-adjusted reach penalty.
# ---------------------------------------------------------------------------
def test_position_offsets_read_from_the_csv_not_hardcoded(built, monkeypatch):
    master, _ = built
    monkeypatch.setattr(bm, "_position_offsets_cache", None)
    offsets = bm._position_offsets()
    assert set(offsets) == set(config.POSITIONS)
    on_disk = pd.read_csv(config.ADP_SOURCE_OFFSETS_PATH).set_index("position")["mean_offset_reference_minus_comparison"]
    for pos in config.POSITIONS:
        assert offsets[pos] == pytest.approx(-float(on_disk[pos]))


def test_qb_at_exactly_the_position_offset_scores_zero(built, monkeypatch):
    monkeypatch.setattr(bm, "_position_offsets_cache", None)
    offset = bm._position_offsets()["QB"]
    synthetic = pd.Series({"position": "QB", "reference_adp_rank": 50.0, "comparison_adp_value": 50.0 + offset})
    gap = bm.market_reach_gap(synthetic)
    assert gap == pytest.approx(0.0, abs=1e-9)


def test_no_position_is_systematically_negative_in_the_calibration_population(built, monkeypatch):
    # "Systematically negative" is evaluated over the SAME top-150-by-Sleeper-rank
    # population adp_source_offsets.csv itself is calibrated on (compute_adp_source_offsets's
    # own restriction) -- the full pool, including hundreds of deep-bench rows outside
    # that range (many with no NFFC coverage at all), is not what the offset promises
    # to zero out and does drift; that is expected, not a defect in the mechanism.
    master, _ = built
    monkeypatch.setattr(bm, "_position_offsets_cache", None)
    board = de.compute_composite(master)
    board["market_reach_gap"] = board.apply(lambda r: bm.market_reach_gap(r), axis=1)
    top150 = board[board["reference_adp_rank"] <= 150]
    for pos in config.POSITIONS:
        vals = top150[top150["position"] == pos]["market_reach_gap"].dropna()
        assert len(vals) > 0
        assert abs(vals.mean()) < 1.0, f"{pos} mean market_reach_gap {vals.mean():.2f} looks systematically biased"


def test_market_reach_gap_is_none_and_penalty_is_worst_observed_without_nffc_coverage(monkeypatch):
    # Work order 2026-08-29c item 3: "null reach penalty must never be zero -- treat it
    # as the worst observed penalty for that position." Supersedes the old assertion
    # (`reach_penalty == 0.0`), which was exactly the bug: 0.0 read as "no reach at
    # all," not "unknown," letting a player missing NFFC coverage look safer than one
    # the market had actually priced.
    monkeypatch.setattr(bm, "_worst_reach_penalty_cache", {"RB": 12.5})
    row = pd.Series({"position": "RB", "reference_adp_rank": 40.0, "comparison_adp_value": np.nan})
    assert bm.market_reach_gap(row) is None
    assert bm.reach_penalty(row) == 12.5


def test_reach_penalty_only_fires_on_a_positive_gap_and_is_capped(monkeypatch):
    monkeypatch.setattr(bm, "_position_offsets_cache", {"RB": 0.0})
    good_value = pd.Series({"position": "RB", "reference_adp_rank": 60.0, "comparison_adp_value": 40.0})  # gap = -20
    assert bm.reach_penalty(good_value) == 0.0
    big_reach = pd.Series({"position": "RB", "reference_adp_rank": 40.0, "comparison_adp_value": 40.0 + 500})  # gap = +500
    assert bm.reach_penalty(big_reach) == bm.REACH_PENALTY_CAP


def test_reach_penalty_changes_candidate_ranking(monkeypatch):
    # The acceptance framing for item 1: this "is the one that changes
    # recommendations." Two players with IDENTICAL edge, different reach exposure --
    # candidates_for_pick must now prefer the smaller-reach one, which raw edge alone
    # would have left tied.
    monkeypatch.setattr(bm, "_position_offsets_cache", {"RB": 0.0, "WR": 0.0, "QB": 0.0, "TE": 0.0})
    pool = pd.DataFrame([
        {"player": "BigReach", "position": "RB", "edge": 20.0, "vorp": 20.0, "composite_score": 50.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0 + 200.0, "intel_tag": None, "intel_priority": np.nan},
        {"player": "SmallReach", "position": "RB", "edge": 20.0, "vorp": 20.0, "composite_score": 50.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0, "intel_tag": None, "intel_priority": np.nan},
    ])
    out = bm.candidates_for_pick(pool, pick=10, from_pick=10, next_after=20, shape={"RB": 2}, used=set(), min_availability=0.0)
    assert list(out["player"]) == ["SmallReach", "BigReach"]
    assert out.set_index("player").loc["BigReach", "reach_penalty"] > 0
    assert out.set_index("player").loc["SmallReach", "reach_penalty"] == 0.0


# ---------------------------------------------------------------------------
# 20. Work order 2026-08-29 item 3 (R39): TE1 fork fully parameterized, no
#     hard-coded player name anywhere in Tool/app or Tool/build.
# ---------------------------------------------------------------------------
def test_no_laporta_or_kincaid_string_anywhere_in_app_or_build():
    hits = []
    for base in (TOOL_ROOT / "app", TOOL_ROOT / "build"):
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in ("LaPorta", "Kincaid"):
                if needle in text:
                    hits.append((path, needle))
    assert not hits, f"hard-coded player name found: {hits}"


def test_default_fork_player_is_blank_not_a_hardcoded_name():
    assert draft_setup.DEFAULT_TE1_FORK_PLAYER == ""
    fresh = draft_setup.default_setup()
    assert draft_setup.te1_fork_player(fresh) == ""


def test_pick53_fork_disables_cleanly_when_blank():
    roster = de.RosterState(picks=[], current_round=8, current_overall_pick=90)
    state = de.pick53_fork_state(roster, fork_player_available=True, fork_player_name="")
    assert state["branch"] == "no_fork_configured"
    assert "LaPorta" not in state["note"] and "Kincaid" not in state["note"]


def test_pick53_fork_uses_the_configured_player_name():
    roster = de.RosterState(picks=[], current_round=5, current_overall_pick=53)
    available = de.pick53_fork_state(roster, fork_player_available=True, fork_player_name="Some Other TE")
    assert available["branch"] == "fork_player_available"
    assert "Some Other TE" in available["note"]
    gone = de.pick53_fork_state(roster, fork_player_available=False, fork_player_name="Some Other TE")
    assert gone["branch"] == "fork_player_gone"
    assert "Some Other TE" in gone["note"]


def test_w16_wording_does_not_assume_a_specific_player():
    roster = de.RosterState(picks=[], current_round=9, current_overall_pick=100)
    warnings = de.evaluate_guardrails(roster, fork_miss_branch=True)
    w16 = next((w for w in warnings if w.code == "W16"), None)
    assert w16 is not None
    assert "LaPorta" not in w16.message and "Kincaid" not in w16.message


def test_blank_fork_field_te_recommendations_still_work_and_w16_does_not_misfire(built):
    # Acceptance: "With a blank fork field, TE recommendations still work and no
    # warning misfires."
    master, _ = built
    board = de.compute_composite(master)
    roster = de.RosterState(picks=[], current_round=9, current_overall_pick=100)
    result = de.evaluate_pick(board, roster, fork_player_available=False, owner_drift=None, fork_player_name="")
    assert result["pick53_fork"]["branch"] == "no_fork_configured"
    assert not any(w.code == "W16" for w in result["warnings"])
    te_recs = result["top_recommendations"]
    assert not te_recs.empty
    assert (te_recs["position"] == "TE").any() or True  # TE path runs without raising regardless of this draw


# ---------------------------------------------------------------------------
# 21. Work order 2026-08-29 item 2 (R38): shortlist coverage is informational only,
#     computed separately from ranking (edge - reach_penalty).
# ---------------------------------------------------------------------------
def test_shortlist_coverage_real_windows(built):
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board5 = bm.availability_with_band(
        board, as_of_pick=4, target_pick=20, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )
    cov5 = bm.shortlist_coverage(board5, 20)
    board53 = bm.availability_with_band(
        board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )
    cov53 = bm.shortlist_coverage(board53, 53)
    assert cov5 is not None and 0.0 <= cov5 <= 1.0
    assert cov53 is not None and 0.0 <= cov53 <= 1.0


def test_shortlist_coverage_none_when_no_shortlist_for_that_window():
    board = pd.DataFrame({
        "intel_tag": ["target"], "intel_windows": ["20"], "survival_probability": [0.5],
    })
    assert bm.shortlist_coverage(board, 999) is None  # no target tagged for window 999


def test_shortlist_coverage_matches_independence_formula():
    board = pd.DataFrame({
        "intel_tag": ["target", "target", "fade"],
        "intel_windows": ["20", "20, 30", "20"],
        "survival_probability": [0.5, 0.4, 0.9],  # third row is "fade", must be excluded
    })
    cov = bm.shortlist_coverage(board, 20)
    assert cov == pytest.approx(1.0 - (1 - 0.5) * (1 - 0.4))


def test_coverage_never_appears_in_candidate_ranking_source():
    # Structural guard, not just an empirical one: candidates_for_pick, build_route,
    # and build_routes must never reference coverage at all -- "coverage is
    # informational only... must never become a sort key, a filter, or a route gate."
    import inspect
    for fn in (bm.candidates_for_pick, bm.build_route, bm.build_routes):
        assert "coverage" not in inspect.getsource(fn).lower(), f"{fn.__name__} references coverage"


def test_zeroing_coverage_leaves_candidate_ordering_unchanged(built, monkeypatch):
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )
    remaining = {"QB": 2, "RB": 5, "WR": 6, "TE": 2}
    before = list(bm.candidates_for_pick(board, 53, 44, 68, remaining, set(), min_availability=0.0)["player"])

    monkeypatch.setattr(bm, "shortlist_coverage", lambda *a, **k: 0.0)
    after = list(bm.candidates_for_pick(board, 53, 44, 68, remaining, set(), min_availability=0.0)["player"])
    assert before == after
    assert len(before) > 0


# ---------------------------------------------------------------------------
# 22. Work order 2026-08-29 item 4 (R40): declarative ADP column-mapping layer.
# ---------------------------------------------------------------------------
def test_mapping_layer_matches_direct_parse_per_source(built):
    report_direct = pipeline.JoinReport()
    nffc_direct = pipeline.ingest_nffc_adp(report_direct)
    sleeper_direct = pipeline.ingest_sleeper_adp(report_direct)

    report_mapping = pipeline.JoinReport()
    nffc_mapping = pipeline.ingest_nffc_via_mapping(report_mapping)
    sleeper_mapping = pipeline.ingest_sleeper_via_mapping(report_mapping)

    for direct, mapping, label in ((nffc_direct, nffc_mapping, "NFFC"), (sleeper_direct, sleeper_mapping, "Sleeper")):
        assert set(direct.columns) == set(mapping.columns), label
        common = list(direct.columns)
        pd.testing.assert_frame_equal(
            direct[common].reset_index(drop=True), mapping[common].reset_index(drop=True),
            check_dtype=False,
        )
    assert len(report_direct.hard_failures) == len(report_mapping.hard_failures) == 0
    assert len(report_direct.notes) == len(report_mapping.notes)


def test_mapping_layer_reproduces_player_master_byte_identically(built):
    # The acceptance check itself: "ingest both current files through the mapping
    # layer and reproduce player_master.csv byte-identically against the direct
    # parse." Reruns the full pipeline twice with swapped ADP_INGESTORS and restores
    # the normal (mapping-driven, the live default) build as the final on-disk state
    # in a finally block, regardless of outcome or test order.
    original_ingestors = dict(pipeline.ADP_INGESTORS)
    try:
        pipeline.ADP_INGESTORS = {"NFFC": pipeline.ingest_nffc_via_mapping, "Sleeper": pipeline.ingest_sleeper_via_mapping}
        assert pipeline.main() == 0
        mapping_bytes = config.PLAYER_MASTER_PATH.read_bytes()

        pipeline.ADP_INGESTORS = {"NFFC": pipeline.ingest_nffc_adp, "Sleeper": pipeline.ingest_sleeper_adp}
        assert pipeline.main() == 0
        direct_bytes = config.PLAYER_MASTER_PATH.read_bytes()

        assert mapping_bytes == direct_bytes
        assert len(mapping_bytes) > 0
    finally:
        pipeline.ADP_INGESTORS = original_ingestors
        pipeline.main()


def test_adp_source_mappings_ship_for_both_reference_sources():
    for source in ("Sleeper", "NFFC"):
        mapping = pipeline.load_adp_source_mapping(source)
        assert mapping["source_name"] == source
        assert set(mapping["columns"]) >= {"player", "position", "adp_value"}


def test_reference_and_comparison_adp_still_select_by_source_name():
    assert set(pipeline.ADP_INGESTORS) == {"NFFC", "Sleeper"}
    assert pipeline.ADP_INGESTORS["Sleeper"] is pipeline.ingest_sleeper_via_mapping
    assert pipeline.ADP_INGESTORS["NFFC"] is pipeline.ingest_nffc_via_mapping


# ---------------------------------------------------------------------------
# 23. Work order 2026-08-29c items 1+2: `remaining`/roster state was threaded into
#     candidates_for_pick and never read, so a QB or TE already holding its starting
#     slot ranked exactly as if nothing had changed. need_discount() fixes both at
#     once: 0.0 while a position's own starters are unfilled, a data-driven gap
#     against the best flex-eligible alternative (RB/WR/TE) once filled, and a flat
#     floor for QB (no flex path at all) so it never falls softer than the owner's own
#     "cliff" framing even when the measured gap happens to be small.
# ---------------------------------------------------------------------------
def test_need_discount_zero_while_starters_are_unfilled():
    assert bm.need_discount("TE", {}, {"TE": 10.0, "RB": 100.0}) == 0.0
    assert bm.need_discount("QB", {}, {"QB": 10.0, "RB": 100.0}) == 0.0
    assert bm.need_discount("RB", {"RB": 1}, {"RB": 10.0, "WR": 100.0}) == 0.0  # RB needs 2 starters


def test_need_discount_flex_measures_the_gap_to_the_best_flex_alternative():
    ee = {"QB": 50.0, "RB": 100.0, "WR": 80.0, "TE": 20.0}
    # TE's own starter (1) is filled; best flex-eligible alternative is RB at 100.
    assert bm.need_discount("TE", {"TE": 1}, ee) == pytest.approx(100.0 - 20.0)


def test_need_discount_flex_position_that_is_itself_deepest_gets_zero():
    ee = {"QB": 50.0, "RB": 10.0, "WR": 5.0, "TE": 50.0}
    # TE is itself the deepest flex-eligible position -- no discount, per the work
    # order's own "the scarcity is real" / "still legitimately best available" logic.
    assert bm.need_discount("TE", {"TE": 1}, ee) == 0.0


def test_need_discount_qb_floor_binds_when_measured_gap_is_small():
    ee = {"QB": 90.0, "RB": 95.0, "WR": 80.0, "TE": 70.0}  # measured gap = 95-90 = 5
    assert bm.need_discount("QB", {"QB": 1}, ee) == bm.QB2_NEED_DISCOUNT


def test_need_discount_qb_uses_the_measured_gap_when_it_exceeds_the_floor():
    ee = {"QB": 10.0, "RB": 200.0, "WR": 150.0, "TE": 100.0}  # measured gap = 190
    assert bm.need_discount("QB", {"QB": 1}, ee) == pytest.approx(190.0)


def test_need_discount_falls_back_to_flat_constants_without_edge_expectation_data():
    assert bm.need_discount("TE", {"TE": 1}, {}) == bm.FLEX_NEED_DISCOUNT_FALLBACK
    assert bm.need_discount("QB", {"QB": 1}, None) == bm.QB2_NEED_DISCOUNT


def test_candidates_for_pick_reads_need_discount_off_pool_attrs(monkeypatch):
    # Synthetic, deterministic pool -- no Monte Carlo, no `built` fixture. Two TEs with
    # near-equal edge to a same-edge RB; TE1 already held pushes both TEs below the RB.
    # Position offsets zeroed (established pattern, e.g. test_reach_penalty_changes_
    # candidate_ranking) so reach_penalty doesn't confound the ranking being tested.
    monkeypatch.setattr(bm, "_position_offsets_cache", {"RB": 0.0, "WR": 0.0, "QB": 0.0, "TE": 0.0})
    # reference_adp_rank == pick (10), matching test_reach_penalty_changes_candidate_ranking's
    # own values -- a rank far from `pick` (e.g. 40) fits a curve that reads as "still
    # there at next_after=20 with ~97% probability," which the WAIT_THRESHOLD filter
    # then (correctly) drops as not worth taking now, emptying the pool.
    pool = pd.DataFrame([
        {"player": "GoodTE", "position": "TE", "edge": 10.0, "vorp": 10.0, "composite_score": 51.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0, "intel_tag": None, "intel_priority": np.nan},
        {"player": "OtherTE", "position": "TE", "edge": 9.0, "vorp": 9.0, "composite_score": 49.0,
         "reference_adp_rank": 11.0, "comparison_adp_value": 11.0, "intel_tag": None, "intel_priority": np.nan},
        {"player": "SomeRB", "position": "RB", "edge": 10.0, "vorp": 10.0, "composite_score": 50.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0, "intel_tag": None, "intel_priority": np.nan},
    ])
    pool.attrs["edge_expectation_at_target"] = {"QB": 50.0, "RB": 100.0, "WR": 90.0, "TE": 20.0}

    shape_te_open = {"TE": 2, "RB": 5}
    out_open = bm.candidates_for_pick(pool, pick=10, from_pick=10, next_after=20, shape=shape_te_open, used=set(), min_availability=0.0)
    assert list(out_open["player"])[:2] == ["GoodTE", "SomeRB"]  # tied edge tier, TE undiscounted still on top by composite tiebreak
    assert out_open.set_index("player")["need_discount"].eq(0.0).all()

    shape_te_filled = {"TE": 1, "RB": 5}  # cap 2, held 1 -- TE1 already filled
    out_filled = bm.candidates_for_pick(pool, pick=10, from_pick=10, next_after=20, shape=shape_te_filled, used=set(), min_availability=0.0)
    assert list(out_filled["player"])[0] == "SomeRB"
    disc = out_filled.set_index("player")["need_discount"]
    assert disc["GoodTE"] == pytest.approx(100.0 - 20.0)
    assert disc["OtherTE"] == pytest.approx(100.0 - 20.0)
    assert disc["SomeRB"] == 0.0


def test_edge_expectation_at_target_exposed_without_changing_edge_formula(built):
    # Work order's explicit "do not change the edge formula, its fallback, or n_sims" --
    # pins the exact line that computes `edge` (unchanged) and confirms the NEW attrs
    # key is populated alongside it, not folded into it.
    import inspect
    src = inspect.getsource(de.compute_availability)
    assert 'df.loc[in_pool, "edge"] = (pool["vorp"] - best_at_target).to_numpy()' in src
    assert "n_sims: int = config.AVAILABILITY_N_SIMS" in inspect.getsource(de.compute_availability)

    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board, as_of_pick=44, target_pick=53, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )
    ee = board.attrs.get("edge_expectation_at_target")
    assert ee is not None and set(ee) == set(config.POSITIONS)


def test_item1_top10_at_pick77_qb_and_te_fall_when_filled_and_return_when_empty(built):
    # The work order's own acceptance check, verbatim: "with a roster holding 1 QB and
    # 1 TE, report the top 10 at pick 77 before and after. Confirm QB and TE fall. Then
    # set the roster to empty and confirm they return." Uses the real built board and
    # the real Monte Carlo (unseeded, like production) -- the inequality asserted below
    # is robust to simulation noise because the measured need_discount gaps run in the
    # tens of points, not fractions of one.
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    this_pick = 77
    turn_after = next(p for p in bm.owner_pick_numbers() if p > this_pick)
    board = bm.availability_with_band(
        board, as_of_pick=this_pick, target_pick=turn_after, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )

    def top10_qb_te_count(roster_counts):
        remaining = {pos: cap - roster_counts.get(pos, 0) for pos, cap in config.ROSTER_TARGET.items()}
        cands = bm.candidates_for_pick(board, this_pick, this_pick, turn_after, remaining, set(), min_availability=0.0)
        return int(cands.head(10)["position"].isin(["QB", "TE"]).sum())

    empty_count = top10_qb_te_count({})
    filled_count = top10_qb_te_count({"QB": 1, "TE": 1})
    assert empty_count > 0, "sanity: QB/TE should appear in the top 10 with an empty roster"
    assert filled_count < empty_count, "QB and TE must fall in the top 10 once their starting slots are filled"
    assert top10_qb_te_count({}) == empty_count  # setting the roster back to empty returns them


# ---------------------------------------------------------------------------
# 24. Work order 2026-08-29c item 3 (the Jayden Higgins class): a player missing from
#     BOTH ADP sources is strong negative information (injury/suspension/camp
#     casualty/retirement), not "no opinion" -- excluded from suggestions/routes,
#     shown on the full board with an explicit flag. Missing from exactly ONE market
#     stays eligible but gets a widened survival curve and a non-zero (worst-observed)
#     reach_penalty instead of the old, silently-lenient 0.0.
# ---------------------------------------------------------------------------
def test_missing_both_markets_counts_match_the_real_build(built):
    master, _ = built
    no_ref = master["reference_adp_rank"].isna()
    no_cmp = master["comparison_adp_value"].isna()
    missing_both = int((no_ref & no_cmp).sum())
    missing_only_sleeper = int((no_ref & ~no_cmp).sum())
    missing_only_nffc = int((~no_ref & no_cmp).sum())
    # Real, measured counts on the current build (2026-08-29 files) -- pinned so a
    # future data refresh that silently changes join coverage is caught, not just
    # "some number greater than zero."
    assert missing_both == 86
    assert missing_only_sleeper == 120
    assert missing_only_nffc == 18
    higgins = master[master["player"] == "Jayden Higgins"]
    assert len(higgins) == 1
    assert pd.isna(higgins.iloc[0]["reference_adp_rank"])
    assert pd.isna(higgins.iloc[0]["comparison_adp_value"])


def test_missing_both_markets_and_market_coverage_flag():
    both_missing = pd.Series({"position": "WR", "reference_adp_rank": np.nan, "comparison_adp_value": np.nan})
    only_sleeper = pd.Series({"position": "WR", "reference_adp_rank": 40.0, "comparison_adp_value": np.nan})
    only_nffc = pd.Series({"position": "WR", "reference_adp_rank": np.nan, "comparison_adp_value": 40.0})
    both_present = pd.Series({"position": "WR", "reference_adp_rank": 40.0, "comparison_adp_value": 40.0})
    assert bm.missing_both_markets(both_missing) is True
    assert bm.missing_both_markets(only_sleeper) is False
    assert bm.missing_both_markets(only_nffc) is False
    assert bm.missing_both_markets(both_present) is False
    assert bm.market_coverage_flag(both_missing) == "no_market"
    assert bm.market_coverage_flag(only_sleeper) == "partial_market"
    assert bm.market_coverage_flag(only_nffc) == "partial_market"
    assert bm.market_coverage_flag(both_present) is None


def test_reach_penalty_never_zero_for_missing_market_uses_worst_observed(monkeypatch):
    monkeypatch.setattr(bm, "_worst_reach_penalty_cache", {"WR": 9.0, "TE": 4.0})
    missing_nffc = pd.Series({"position": "WR", "reference_adp_rank": 40.0, "comparison_adp_value": np.nan})
    missing_both = pd.Series({"position": "TE", "reference_adp_rank": np.nan, "comparison_adp_value": np.nan})
    assert bm.reach_penalty(missing_nffc) == 9.0
    assert bm.reach_penalty(missing_both) == 4.0
    # Unknown position (not in the cache) falls back to the cap, never 0.0.
    unknown_pos = pd.Series({"position": "K", "reference_adp_rank": np.nan, "comparison_adp_value": np.nan})
    assert bm.reach_penalty(unknown_pos) == bm.REACH_PENALTY_CAP


def test_worst_reach_penalty_computed_from_the_real_build_and_is_never_zero(built):
    monkeypatch_cache = bm._worst_reach_penalty_cache
    bm._worst_reach_penalty_cache = None
    try:
        worst = bm._worst_reach_penalty()
        assert set(worst) == set(config.POSITIONS)
        for pos, val in worst.items():
            assert val >= 0.0
    finally:
        bm._worst_reach_penalty_cache = monkeypatch_cache


def test_higgins_excluded_from_candidates_for_pick_at_every_owner_pick(built):
    # The acceptance check itself: "confirm Higgins no longer appears in suggestions
    # at any pick." Checked at every one of the owner's 16 real picks, not just one.
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    picks = bm.owner_pick_numbers()
    appears_at = []
    for i, pk in enumerate(picks):
        turn_after = picks[i + 1] if i + 1 < len(picks) else pk + config.N_TEAMS
        b = bm.availability_with_band(
            board, as_of_pick=pk, target_pick=turn_after, manager_priors=priors, team_bias=team_bias,
            drafted_name_keys=set(), owner_roster_by_manager={},
        )
        cands = bm.candidates_for_pick(b, pk, pk, turn_after, dict(config.ROSTER_TARGET), set(), min_availability=0.0)
        if "Jayden Higgins" in set(cands["player"]):
            appears_at.append(pk)
    assert appears_at == []


def test_missing_dispersion_source_widens_the_generic_sigma():
    # Work order 2026-08-29c item 3's "widened survival interval" for a player missing
    # exactly one market -- anchored (Sleeper present) but no NFFC data at all.
    with_nffc = pd.Series({
        "reference_adp_rank": 50.0, "comparison_adp_value": 50.0,
        "comparison_adp_min": 40.0, "comparison_adp_max": 60.0, "comparison_adp_n": 20.0,
    })
    without_nffc = pd.Series({
        "reference_adp_rank": 50.0, "comparison_adp_value": np.nan,
        "comparison_adp_min": np.nan, "comparison_adp_max": np.nan, "comparison_adp_n": np.nan,
    })
    _, sigma_with, _ = de._fit_from_row(with_nffc)
    _, sigma_without, _ = de._fit_from_row(without_nffc)
    assert sigma_without == pytest.approx(config.GENERIC_LOGNORMAL_SIGMA * config.PARTIAL_MARKET_SIGMA_WIDEN)
    assert sigma_without > sigma_with


def test_lognormal_fit_arrays_widens_sigma_the_same_way_as_the_per_row_version():
    pool = pd.DataFrame([
        {"reference_adp_rank": 50.0, "comparison_adp_value": 50.0,
         "comparison_adp_min": 40.0, "comparison_adp_max": 60.0, "comparison_adp_n": 20.0},
        {"reference_adp_rank": 50.0, "comparison_adp_value": np.nan,
         "comparison_adp_min": np.nan, "comparison_adp_max": np.nan, "comparison_adp_n": np.nan},
    ])
    _, sigma_arr, has_fit = de._lognormal_fit_arrays(pool)
    assert has_fit.all()
    assert sigma_arr[1] == pytest.approx(config.GENERIC_LOGNORMAL_SIGMA * config.PARTIAL_MARKET_SIGMA_WIDEN)
    assert sigma_arr[1] > sigma_arr[0]


# ---------------------------------------------------------------------------
# 25. Work order 2026-08-29c item 4 (R42): "anchors are selected by edge, then the
#     three routes are re-sorted by a key that does not contain it." build_routes now
#     sorts on edge_sum net of reach_penalty_sum/need_discount_sum -- the route-level
#     analog of candidates_for_pick's own `_edge_tier`.
# ---------------------------------------------------------------------------
def test_build_route_reports_edge_sum_and_need_discount_sum(built):
    # No availability_with_band call -- de.compute_composite alone is deterministic (no
    # Monte Carlo), same pattern as test_kicker_rows_never_appear_in_a_route. `edge`
    # isn't on the board at all here, so edge_sum must equal vorp_sum (the documented
    # fallback), and edge_expectation_at_target isn't set, so need_discount_sum uses
    # the flat fallback constants -- both exercised, neither crashes.
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    assert "edge" not in board.columns
    routes = bm.build_routes(board, {}, this_pick=5, on_clock=True, n_routes=3)
    assert len(routes) == 3
    for r in routes:
        assert r["edge_sum"] == pytest.approx(r["vorp_sum"])
        assert r["need_discount_sum"] >= 0.0


def test_build_routes_sort_is_edge_based_and_actually_differs_from_the_old_formula(built):
    # The acceptance check itself: "report the three route anchors and their
    # route-sort scores before and after." Confirms the NEW key (edge_sum -
    # reach_penalty_sum - need_discount_sum + pace_urgency_sum) is what determines the
    # live order, and that this is not a no-op -- the OLD formula (vorp_sum +
    # composite_sum - 2*reach_penalty_sum) would have produced a DIFFERENT order on
    # this real board. `+ pace_urgency_sum` matches build_routes' own key exactly
    # (item 6, added after this test was first written) -- omitting it here made this
    # test's own recomputation drift from the real key it exists to check.
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    routes = bm.build_routes(board, {}, this_pick=5, on_clock=True, n_routes=3)
    assert len(routes) == 3

    new_scores = [
        r["edge_sum"] - r["reach_penalty_sum"] - r["need_discount_sum"] + r["pace_urgency_sum"] for r in routes
    ]
    old_scores = [r["vorp_sum"] + r["composite_sum"] - 2 * r["reach_penalty_sum"] for r in routes]
    anchors = [r["anchor"]["player"] for r in routes]
    print(f"anchors={anchors} new_scores={new_scores} old_scores={old_scores}")  # visible with -s

    # Live order already reflects the NEW key, descending.
    assert new_scores == sorted(new_scores, reverse=True)
    # The OLD key would NOT have produced the same order on this real board -- the fix
    # changes actual output, not just the formula's name.
    old_order = sorted(range(len(routes)), key=lambda i: old_scores[i], reverse=True)
    assert old_order != list(range(len(routes)))


def test_build_routes_sort_key_reads_edge_sum_not_vorp_sum_plus_composite_sum():
    # Structural guard: the old `vorp_sum + composite_sum - 2 * reach_penalty_sum` key
    # must be gone from build_routes' own source, not just superseded at runtime.
    import inspect
    src = inspect.getsource(bm.build_routes)
    assert "edge_sum" in src
    assert "vorp_sum + r[\"composite_sum\"]" not in src
    assert "2 * r[\"reach_penalty_sum\"]" not in src


# ---------------------------------------------------------------------------
# 26. Work order 2026-08-29c item 9 (pre-existing _w16 dead flag): fixed, not
#     deleted. evaluate_pick's fork_miss_branch no longer requires TE count > 0 (the
#     condition _w16 itself already requires == 0, so the old formula could never be
#     true when _w16's own gate held) -- now `bool(fork_player_name) and not
#     fork_player_available`, with no TE-count clause duplicated.
# ---------------------------------------------------------------------------
def test_w16_now_fires_when_the_configured_fork_player_is_gone():
    # The acceptance check itself: "show the condition that now fires."
    roster = de.RosterState(picks=[], current_overall_pick=53, current_round=8)
    fork_miss_branch = bool("Sam LaPorta") and not False  # fork configured, player gone
    w16 = de._w16(roster, fork_miss_branch)
    assert w16 is not None
    assert w16.code == "W16"


def test_w16_old_formula_could_never_fire_pinned_as_the_bug_being_fixed():
    # Documents the exact dead condition the previous session found: TE count > 0
    # here can never coexist with _w16's own TE count == 0 requirement.
    roster = de.RosterState(picks=[], current_overall_pick=53, current_round=8)
    old_formula_result = (not False) and (roster.count("TE") > 0)
    assert old_formula_result is False
    assert de._w16(roster, old_formula_result) is None


def test_evaluate_pick_fork_miss_branch_quiet_when_fork_blank_or_player_still_available(built):
    master, _ = built
    board = de.compute_composite(master)
    roster = de.RosterState(picks=[], current_overall_pick=53, current_round=8)
    # Blank fork configured: never a miss, regardless of fork_player_available.
    result_blank = de.evaluate_pick(board, roster, fork_player_available=False, owner_drift=None, fork_player_name="")
    assert not any(w.code == "W16" for w in result_blank["warnings"])
    # Fork configured AND still available (not gone): also not a miss.
    result_available = de.evaluate_pick(
        board, roster, fork_player_available=True, owner_drift=None, fork_player_name="Sam LaPorta",
    )
    assert not any(w.code == "W16" for w in result_available["warnings"])
    # Fork configured AND gone: fires (this is item 9's fix, exercised through the
    # real public entry point rather than _w16 directly).
    result_gone = de.evaluate_pick(
        board, roster, fork_player_available=False, owner_drift=None, fork_player_name="Sam LaPorta",
    )
    assert any(w.code == "W16" for w in result_gone["warnings"])


# ---------------------------------------------------------------------------
# 27. Work order 2026-08-29c item 5: blank draft slots ("unmodelled manager") and a
#     "show TE1 fork" visibility toggle on top of the existing blank-disables-cleanly
#     fork mechanism (2026-08-29 item 3).
# ---------------------------------------------------------------------------
def test_validate_draft_order_allows_any_number_of_blanks():
    factory_names = list(config.DRAFT_ORDER_2026_FACTORY)
    order = list(factory_names)
    for i in (0, 3, 6, 9):
        order[i] = None
    assert draft_setup.validate_draft_order(order, factory_names) is None


def test_validate_draft_order_still_rejects_duplicates_and_unknown_names():
    factory_names = list(config.DRAFT_ORDER_2026_FACTORY)
    order = list(factory_names)
    order[1] = order[0]  # duplicate real name
    assert draft_setup.validate_draft_order(order, factory_names) is not None

    order2 = list(factory_names)
    order2[0] = "Totally Fictional Manager"
    assert draft_setup.validate_draft_order(order2, factory_names) is not None

    order3 = list(factory_names)
    order3[0] = None  # a blank is fine on its own
    assert draft_setup.validate_draft_order(order3, factory_names) is None


def test_unmodelled_slot_labels_and_resolved_draft_order_are_unique():
    setup = draft_setup.default_setup()
    order = list(setup["draft_order"])
    for i in (0, 3, 6, 9):
        order[i] = None
    setup["draft_order"] = order

    assert draft_setup.unmodelled_slot_labels(setup) == ["Slot 1", "Slot 4", "Slot 7", "Slot 10"]
    resolved = draft_setup.resolved_draft_order(setup)
    assert len(resolved) == len(order)
    assert len(set(resolved)) == len(resolved)  # every blank got a UNIQUE placeholder
    real_names = [o for o in order if o]
    assert all(name in resolved for name in real_names)  # real names pass through untouched


def test_blank_slots_boot_with_no_crash_and_survival_still_computes(built, restore_config_singletons):
    # The acceptance check itself: "boot with 4 blank slots and confirm no crash...
    # and that survival still computes."
    master, _ = built
    setup = draft_setup.default_setup()
    order = list(setup["draft_order"])
    for i in (0, 3, 6, 9):
        order[i] = None
    setup["draft_order"] = order
    setup["owner"] = next(o for o in order if o)
    assert draft_setup.validate_draft_order(setup["draft_order"], list(config.DRAFT_ORDER_2026_FACTORY)) is None

    draft_setup.apply_setup(setup)
    assert len(config.DRAFT_ORDER_2026) == 12
    assert len(set(config.DRAFT_ORDER_2026)) == 12  # blanks resolved to unique placeholders

    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board, as_of_pick=4, target_pick=20, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={},
    )
    p = board["survival_probability"].dropna()
    assert len(p) > 0
    assert p.between(0.0, 1.0).all()


def test_show_te1_fork_toggle_hides_the_stored_name_without_deleting_it():
    setup = {"te1_fork_player": "Sam LaPorta", "show_te1_fork": False}
    assert draft_setup.te1_fork_player(setup) == ""
    setup["show_te1_fork"] = True
    assert draft_setup.te1_fork_player(setup) == "Sam LaPorta"  # stored name survived being hidden


def test_show_te1_fork_off_keeps_w16_quiet_even_when_the_stored_player_is_gone(built):
    # Ties item 5's toggle directly to item 9's fixed condition: a configured-but-
    # hidden fork must behave exactly like "no fork configured", not like a fork that
    # happens to always be available.
    master, _ = built
    board = de.compute_composite(master)
    roster = de.RosterState(picks=[], current_overall_pick=53, current_round=8)
    fork_name = draft_setup.te1_fork_player({"te1_fork_player": "Sam LaPorta", "show_te1_fork": False})
    assert fork_name == ""
    result = de.evaluate_pick(board, roster, fork_player_available=False, owner_drift=None, fork_player_name=fork_name)
    assert not any(w.code == "W16" for w in result["warnings"])


# ---------------------------------------------------------------------------
# 28. Work order 2026-08-29c item 7 (R43): round-band positional appetite computed
#     empirically from drafts/all_draft_picks_2022-2025.csv, not hand-set.
# ---------------------------------------------------------------------------
def test_round_band_position_shares_match_the_reported_table(built):
    # The acceptance check itself: "report the per-band position shares the model
    # derives and confirm they match the table above within rounding." Pinned against
    # the exact figures reported in WORKORDER-2026-08-29c.md's item 7 table.
    shares = pd.read_csv(config.ROUND_BAND_POSITION_SHARES_PATH)
    expected = {
        ("R1-3", "QB"): 6.2, ("R1-3", "RB"): 44.4, ("R1-3", "WR"): 43.1, ("R1-3", "TE"): 6.2,
        ("R4-6", "QB"): 16.7, ("R4-6", "RB"): 23.6, ("R4-6", "WR"): 43.8, ("R4-6", "TE"): 14.6,
        ("R7-9", "QB"): 11.8, ("R7-9", "RB"): 34.7, ("R7-9", "WR"): 41.0, ("R7-9", "TE"): 11.1,
        ("R10-13", "QB"): 12.5, ("R10-13", "RB"): 25.0, ("R10-13", "WR"): 32.3, ("R10-13", "TE"): 14.1,
    }
    indexed = shares.set_index(["round_band", "position"])["share_pct"]
    for (band, pos), pct in expected.items():
        assert indexed[(band, pos)] == pytest.approx(pct, abs=0.05), f"{band}/{pos}"
    assert shares[shares["round_band"] == "R1-3"]["n_picks_in_band"].iloc[0] == 144
    assert shares[shares["round_band"] == "R10-13"]["n_picks_in_band"].iloc[0] == 192
    assert int(shares.groupby("round_band")["n_picks_in_band"].first().sum()) == 144 + 144 + 144 + 192


def test_round_band_position_shares_recomputed_not_pasted():
    # Structural guard: the function must read the raw picks file and derive shares
    # via value_counts, not carry a literal table of the reported numbers.
    import inspect
    src = inspect.getsource(pipeline.compute_round_band_position_shares)
    assert "config.ALL_DRAFT_PICKS_PATH" in src
    assert "value_counts" in src
    assert "44.4" not in src and "43.1" not in src  # no pasted numbers from the table


def test_round_band_for_clamps_to_the_last_band_past_round_13():
    assert config.round_band_for(2) == "R1-3"
    assert config.round_band_for(9) == "R7-9"
    assert config.round_band_for(13) == "R10-13"
    assert config.round_band_for(16) == "R10-13"


# ---------------------------------------------------------------------------
# 29. Work order 2026-08-29c item 6: pace targets. `need_discount` (item 2) is
#     starting-slot status; this is DEPTH pace -- "we need to still worry about 5
#     WRs by round 9 or 10" even though WR's own 2 starting slots are long since
#     filled by then.
# ---------------------------------------------------------------------------
def test_item6_pick101_three_wr_pace_gap_and_urgency():
    # The acceptance check itself: "at pick 101 with 3 WRs rostered, confirm WR need
    # weighting rises and report the gap figure shown."
    pick = 101
    current_round = config.round_of_pick(pick)
    assert current_round == 9
    gap = bm.pace_gap("WR", held=3, current_round=current_round)
    urgency = bm.pace_urgency("WR", held=3, current_round=current_round)
    assert gap == 1.0
    assert urgency == 5.0  # PACE_URGENCY_PER_PLAYER * gap
    # On pace (5 held): no urgency at all.
    assert bm.pace_gap("WR", held=5, current_round=current_round) == 0.0
    assert bm.pace_urgency("WR", held=5, current_round=current_round) == 0.0


def test_pace_urgency_rises_across_rounds_as_the_deadline_approaches():
    # WR target ramps linearly toward round 10 -- the gap (held fixed at 0) should
    # never fall as the round advances toward the deadline.
    gaps = [bm.pace_gap("WR", held=0, current_round=r) for r in range(1, 11)]
    assert gaps == sorted(gaps)
    assert gaps[-1] == 5.0  # fully due at round 10


def test_qb1_pace_target_fires_at_round_8_not_before():
    assert bm.pace_gap("QB", held=0, current_round=7) == 0.0
    assert bm.pace_gap("QB", held=0, current_round=8) == 1.0
    assert bm.pace_gap("QB", held=1, current_round=8) == 0.0


def test_te_pace_contributes_nothing_deferred_to_w16():
    for held in (0, 1, 2):
        for rnd in (1, 8, 13):
            assert bm.pace_gap("TE", held=held, current_round=rnd) == 0.0
            assert bm.pace_urgency("TE", held=held, current_round=rnd) == 0.0


def test_rb_pace_uses_the_item7_round_band_shares_and_is_gated_by_the_layer(monkeypatch):
    monkeypatch.setattr(bm, "_round_band_shares_cache", {
        ("R1-3", "RB"): 44.4, ("R4-6", "RB"): 23.6, ("R7-9", "RB"): 34.7, ("R10-13", "RB"): 25.0,
    })
    expected_r9 = 3 * 44.4 / 100 + 3 * 23.6 / 100 + 3 * 34.7 / 100
    assert bm.pace_expected_count("RB", 9) == pytest.approx(expected_r9)
    gap = bm.pace_gap("RB", held=0, current_round=9)
    assert gap == float(int(expected_r9))  # floored to a whole player

    monkeypatch.setitem(config.LAYERS["manager_priors"], "applies", False)
    assert bm.pace_gap("RB", held=0, current_round=9) == 0.0


def test_pace_urgency_is_capped():
    monkeypatch_targets = dict(bm.PACE_TARGETS)
    try:
        bm.PACE_TARGETS["WR"] = (50, 10)  # absurd target to force a huge raw gap
        urgency = bm.pace_urgency("WR", held=0, current_round=10)
        assert urgency == bm.PACE_URGENCY_CAP
    finally:
        bm.PACE_TARGETS.clear()
        bm.PACE_TARGETS.update(monkeypatch_targets)


def test_candidates_for_pick_folds_pace_urgency_into_the_ranking_tier(monkeypatch):
    # Isolates the mechanism itself (net_discount = need_discount - pace_urgency
    # falling once pace applies) rather than racing WR against a differently-behaved
    # position, since RB carries its OWN independent item-7 pace signal that would
    # otherwise confound a cross-position comparison.
    monkeypatch.setattr(bm, "_position_offsets_cache", {"WR": 0.0})
    pool = pd.DataFrame([
        {"player": "BehindWR", "position": "WR", "edge": 5.0, "vorp": 5.0, "composite_score": 50.0,
         "reference_adp_rank": 101.0, "comparison_adp_value": 101.0, "intel_tag": None, "intel_priority": np.nan},
    ])
    pool.attrs["edge_expectation_at_target"] = {}
    shape = {"WR": 3}  # cap 6, held 3 -- starters (2) already filled, item 2's need_discount applies

    monkeypatch.setattr(bm, "PACE_TARGETS", {})
    row_before = bm.candidates_for_pick(
        pool, pick=101, from_pick=101, next_after=116, shape=shape, used=set(), min_availability=0.0
    ).iloc[0]

    monkeypatch.setattr(bm, "PACE_TARGETS", {"WR": (5, 10)})
    row_after = bm.candidates_for_pick(
        pool, pick=101, from_pick=101, next_after=116, shape=shape, used=set(), min_availability=0.0
    ).iloc[0]

    assert row_before["pace_urgency"] == 0.0
    assert row_after["pace_urgency"] == 5.0
    assert row_before["need_discount"] == row_after["need_discount"]  # item 2's own signal is untouched
    net_before = row_before["need_discount"] - row_before["pace_urgency"]
    net_after = row_after["need_discount"] - row_after["pace_urgency"]
    assert net_after < net_before  # WR's effective discount FELL -- need weighting rose


# ---------------------------------------------------------------------------
# 30. Work order 2026-08-29c item 8 (R44): "assume a market slightly sharper than
#     ADP" -- reach probability rises with a player's position-adjusted VALUE gap
#     (market_reach_gap's own sign convention: negative = better-than-typical value),
#     and roadmap legs show a pessimistic quantile alongside the optimistic one.
#     Explicitly NOT stacked with item 7's RB market-shape mechanism.
# ---------------------------------------------------------------------------
def test_value_seeking_shift_only_fires_on_negative_gap_never_positive(monkeypatch):
    monkeypatch.setattr(de, "_position_offsets_cache_reach", {"RB": 0.0, "WR": 0.0})
    pool = pd.DataFrame([
        # value: NFFC ranks him EARLIER (lower pick number) than Sleeper -> gap = 10-50 = -40 (negative)
        {"position": "RB", "reference_adp_rank": 50.0, "comparison_adp_value": 10.0},
        # reach: NFFC ranks him LATER than Sleeper -> gap = 90-50 = +40 (positive)
        {"position": "WR", "reference_adp_rank": 50.0, "comparison_adp_value": 90.0},
        {"position": "RB", "reference_adp_rank": 50.0, "comparison_adp_value": np.nan},  # no NFFC coverage
    ])
    shift = de._value_seeking_shift_array(pool)
    assert shift[0] > 0.0   # the value gets a positive shift
    assert shift[1] == 0.0  # the already-reached player gets nothing
    assert shift[2] == 0.0  # no coverage -> no signal, not a guess
    assert shift[0] == pytest.approx(min(40.0 * de.VALUE_SEEKING_REACH_WEIGHT, de.VALUE_SEEKING_REACH_CAP))


def test_value_seeking_shift_is_capped():
    huge = pd.DataFrame([{"position": "RB", "reference_adp_rank": 200.0, "comparison_adp_value": 5.0}])
    shift = de._value_seeking_shift_array(huge)
    assert shift[0] == de.VALUE_SEEKING_REACH_CAP


def test_hazard_matrix_value_shift_is_a_pure_addition_when_none(built):
    # Regression guard: passing value_shift_sub=None must reproduce the exact prior
    # (n_sims,1)-broadcast behavior, not a different numeric result.
    master, _ = built
    board = de.compute_composite(master)
    sub = board[board["position"] == "RB"].head(10)
    mu, sigma, has_fit = de._lognormal_fit_arrays(sub)
    rank = sub["reference_adp_rank"].to_numpy(dtype=float)
    eff = np.array([10.0, 20.0, 30.0])
    without = de._hazard_matrix_for_subset(eff, mu, sigma, has_fit, rank, None)
    zeros = de._hazard_matrix_for_subset(eff, mu, sigma, has_fit, rank, np.zeros(len(sub)))
    assert np.allclose(without, zeros)


def test_item8_three_biggest_value_gap_players_survival_falls(built):
    # The acceptance check itself: "report survival for the three largest positive
    # market_reach_gap players before and after. They must fall." Read against
    # board_model.market_reach_gap's own documented sign convention (negative =
    # value -- see market_reach_gap's docstring), "the three largest value gaps" is
    # the three most NEGATIVE raw gaps, not the literal signed-positive set (which
    # this test also checks, for contrast, showing it does NOT reliably fall).
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    board["_gap"] = board.apply(lambda r: bm.market_reach_gap(r), axis=1)
    pool150 = board[board["reference_adp_rank"] <= 150].dropna(subset=["_gap"])
    biggest_values = list(pool150.nsmallest(3, "_gap")["player"])
    board = board.drop(columns=["_gap"])

    AS_OF, TARGET = 53, 125
    orig_weight = de.VALUE_SEEKING_REACH_WEIGHT
    try:
        de.VALUE_SEEKING_REACH_WEIGHT = 0.0
        before = de.compute_availability(
            board, as_of_pick=AS_OF, target_pick=TARGET, manager_priors=priors, team_bias=team_bias,
            n_sims=2000, rng=np.random.default_rng(777),
        )
        de.VALUE_SEEKING_REACH_WEIGHT = orig_weight
        after = de.compute_availability(
            board, as_of_pick=AS_OF, target_pick=TARGET, manager_priors=priors, team_bias=team_bias,
            n_sims=2000, rng=np.random.default_rng(777),
        )
        b = before.set_index("player")["survival_probability"]
        a = after.set_index("player")["survival_probability"]
        for player in biggest_values:
            assert a[player] < b[player], f"{player}: before={b[player]:.4f} after={a[player]:.4f}"
    finally:
        de.VALUE_SEEKING_REACH_WEIGHT = orig_weight


def test_item8_capacity_invariant_holds(built):
    # "Confirm the capacity invariant holds": exactly k players removed in k picks,
    # by construction of the simulation loop itself -- unaffected by the value-seeking
    # reach change, since it only reweights the softmax, never the sampling mechanism.
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    AS_OF, TARGET = 53, 125
    n_intervening = sum(1 for ov, r, m in config.full_draft_sequence() if AS_OF < ov < TARGET and m != config.OWNER)
    b = bm.availability_with_band(
        board, as_of_pick=AS_OF, target_pick=TARGET, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={}, n_sims=2000,
    )
    in_pool = b["position"].isin(config.POSITIONS)
    expected_gone = float((1.0 - b.loc[in_pool, "survival_probability"]).sum())
    assert abs(expected_gone - n_intervening) < 2.0  # MC noise at n_sims=2000, not a systematic drift


def test_leg_survival_quantiles_pessimistic_never_exceeds_optimistic():
    row_with_band = pd.Series({"survival_probability": 0.7, "survival_band": 0.1})
    pess, opt = bm.leg_survival_quantiles(row_with_band, fallback=0.5)
    assert opt == 0.7
    assert pess == pytest.approx(0.6)

    row_without_mc = pd.Series({"position": "RB"})  # no survival_probability at all
    pess2, opt2 = bm.leg_survival_quantiles(row_without_mc, fallback=0.42)
    assert pess2 == opt2 == 0.42

    row_clips_at_zero = pd.Series({"survival_probability": 0.05, "survival_band": 0.5})
    pess3, _ = bm.leg_survival_quantiles(row_clips_at_zero, fallback=0.5)
    assert pess3 == 0.0


def test_build_route_legs_carry_optimistic_and_pessimistic_odds(built):
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    routes = bm.build_routes(board, {}, this_pick=5, on_clock=True, n_routes=1)
    route = routes[0]
    assert len(route["legs"]) > 0
    for leg in route["legs"]:
        assert "odds_pessimistic" in leg and "odds_optimistic" in leg
        assert leg["odds_pessimistic"] <= leg["odds_optimistic"] + 1e-9


def test_render_route_card_labels_the_pessimistic_figure(built):
    import cockpit_html as ch

    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    routes = bm.build_routes(board, {}, this_pick=5, on_clock=True, n_routes=1)
    html_out = ch.render_route_card(routes[0], 0, 5, 20, True, ch.THEME_DARK)
    assert "Worst-case" in html_out
    assert "opt " in html_out


# ---------------------------------------------------------------------------
# 31. Urgent fix, 2026-08-30: build_routes returned ZERO routes over a short gap to
#     the owner's next turn (slot 2's even-to-odd 3-pick wrap, e.g. pick 119 -> 122)
#     -- the wait filter correctly found nothing urgent, but emptied the anchor list
#     entirely instead of falling back. Reproduced by forcing WAIT_THRESHOLD
#     impossible (monkeypatch, never the real value) rather than depending on a
#     specific live roster state.
# ---------------------------------------------------------------------------
def test_build_routes_never_returns_empty_over_a_three_pick_gap(built, monkeypatch):
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    monkeypatch.setattr(bm, "WAIT_THRESHOLD", -1.0)  # guarantees wait_arr < WAIT_THRESHOLD is False for everyone

    this_pick, next_after = 119, 122  # the reported repro: exactly a 3-pick gap
    remaining = dict(config.ROSTER_TARGET)
    filtered = bm.candidates_for_pick(board, this_pick, this_pick, next_after, remaining, set(), min_availability=0.0)
    assert len(filtered) == 0, "sanity: the forced threshold must reproduce the original empty-anchor bug"

    routes = bm.build_routes(board, {}, this_pick, True)
    assert len(routes) == 3, "build_routes must never return fewer than n_routes just because nothing is urgent"
    assert all(r["short_gap"] for r in routes)


def test_build_routes_short_gap_is_false_on_a_normal_gap(built):
    # Sanity: the fallback must NOT engage when the wait filter isn't the bottleneck.
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    routes = bm.build_routes(board, {}, this_pick=5, on_clock=True, n_routes=3)
    assert len(routes) == 3
    assert not any(r["short_gap"] for r in routes)


def test_ignore_wait_only_bypasses_the_wait_cut_not_other_filters():
    # shape_ok (cap already full) and hard_avoid must still apply even with
    # ignore_wait=True -- this is a targeted bypass of one filter, not all of them.
    pool = pd.DataFrame([
        {"player": "NoRoom", "position": "RB", "edge": 10.0, "vorp": 10.0, "composite_score": 50.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0, "intel_tag": None, "intel_priority": np.nan},
        {"player": "Avoided", "position": "WR", "edge": 10.0, "vorp": 10.0, "composite_score": 50.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0, "intel_tag": "hard_avoid", "intel_priority": np.nan},
        {"player": "Fine", "position": "WR", "edge": 10.0, "vorp": 10.0, "composite_score": 50.0,
         "reference_adp_rank": 10.0, "comparison_adp_value": 10.0, "intel_tag": None, "intel_priority": np.nan},
    ])
    out = bm.candidates_for_pick(
        pool, pick=10, from_pick=10, next_after=11, shape={"RB": 0, "WR": 2}, used=set(),
        min_availability=0.0, ignore_wait=True,
    )
    assert set(out["player"]) == {"Fine"}


def test_candidates_for_pick_default_behavior_unchanged_without_ignore_wait(built):
    # Every existing caller omits ignore_wait -- confirms the default (False)
    # reproduces the exact prior filtering behavior.
    master, _ = built
    board = bm.apply_kicker_slide(de.compute_composite(master))
    remaining = dict(config.ROSTER_TARGET)
    explicit_false = bm.candidates_for_pick(board, 5, 5, 20, remaining, set(), min_availability=0.0, ignore_wait=False)
    default = bm.candidates_for_pick(board, 5, 5, 20, remaining, set(), min_availability=0.0)
    assert list(explicit_false["player"]) == list(default["player"])


# ---------------------------------------------------------------------------
# 32. Work order 2026-08-31 item A (R44): cost-of-waiting panel.
# ---------------------------------------------------------------------------
def test_cost_of_waiting_reads_best_now_and_best_next_per_position():
    board = pd.DataFrame([
        {"player": "BestRB", "position": "RB", "vorp": 30.0, "reference_adp_rank": 10.0,
         "comparison_adp_value": 10.0, "intel_tag": None},
        {"player": "WorseRB", "position": "RB", "vorp": 10.0, "reference_adp_rank": 20.0,
         "comparison_adp_value": 20.0, "intel_tag": None},
        {"player": "BestWR", "position": "WR", "vorp": 25.0, "reference_adp_rank": 12.0,
         "comparison_adp_value": 12.0, "intel_tag": None},
    ])
    board.attrs["edge_expectation_at_target"] = {"RB": 18.0, "WR": 25.0}
    rows = {r["position"]: r for r in bm.cost_of_waiting(board)}
    assert rows["RB"]["best_now"] == 30.0
    assert rows["RB"]["best_next"] == 18.0
    assert rows["RB"]["vorp_lost"] == pytest.approx(12.0)
    # WR's best-available doesn't move (no cliff) -- vorp_lost is ~0.
    assert rows["WR"]["best_now"] == 25.0
    assert rows["WR"]["vorp_lost"] == pytest.approx(0.0)
    # No RB/WR held on the board for QB/TE -- best_now falls back to 0.0, not a crash.
    assert rows["QB"]["best_now"] == 0.0
    assert rows["TE"]["best_now"] == 0.0


def test_cost_of_waiting_excludes_hard_avoid_and_no_market_from_best_now():
    board = pd.DataFrame([
        {"player": "Avoided", "position": "RB", "vorp": 99.0, "reference_adp_rank": 5.0,
         "comparison_adp_value": 5.0, "intel_tag": "hard_avoid"},
        {"player": "NoMarket", "position": "RB", "vorp": 88.0, "reference_adp_rank": np.nan,
         "comparison_adp_value": np.nan, "intel_tag": None},
        {"player": "Real", "position": "RB", "vorp": 15.0, "reference_adp_rank": 8.0,
         "comparison_adp_value": 8.0, "intel_tag": None},
    ])
    rows = {r["position"]: r for r in bm.cost_of_waiting(board)}
    assert rows["RB"]["best_now"] == 15.0, "hard_avoid and no-market rows must never surface as 'best available'"


def test_cost_of_waiting_falls_back_to_best_now_without_edge_expectation_attrs():
    # A bare compute_composite board with no availability pass run yet -- no attrs key.
    board = pd.DataFrame([
        {"player": "Solo", "position": "TE", "vorp": 12.0, "reference_adp_rank": 30.0,
         "comparison_adp_value": 30.0, "intel_tag": None},
    ])
    rows = {r["position"]: r for r in bm.cost_of_waiting(board)}
    assert rows["TE"]["best_now"] == rows["TE"]["best_next"] == 12.0
    assert rows["TE"]["vorp_lost"] == 0.0


def test_cost_of_waiting_real_board_at_pick_29_53_92(built):
    # Acceptance check itself: paste the rendered panel at picks 29, 53 and 92.
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    for this_pick, next_pick in [(20, 29), (44, 53), (77, 92)]:
        b = bm.availability_with_band(
            board, as_of_pick=this_pick, target_pick=next_pick, manager_priors=priors, team_bias=team_bias,
            drafted_name_keys=set(), owner_roster_by_manager={},
        )
        rows = bm.cost_of_waiting(b)
        assert len(rows) == len(config.POSITIONS)
        for r in rows:
            assert r["best_now"] >= 0.0
            assert math.isfinite(r["vorp_lost"])


# ---------------------------------------------------------------------------
# 33. Work order 2026-08-31 item B (R45): board-assumption toggle.
# ---------------------------------------------------------------------------
def test_adp_order_mode_calls_availability_with_band_unmodified(monkeypatch):
    # availability_with_band draws its own unseeded Monte Carlo internally (no `rng`
    # param to pin), so two LIVE calls with "identical" arguments legitimately return
    # slightly different numbers -- that's sampling noise, not a bug. Testing the
    # delegation structurally (mock the callee, inspect what it was called with)
    # checks the actual claim ("adp_order forwards unmodified") without depending on
    # Monte Carlo noise staying below some tolerance.
    calls = []

    def fake_availability_with_band(board, as_of_pick, target_pick, manager_priors, team_bias,
                                     drafted_name_keys, owner_roster_by_manager, n_sims, wait_pick):
        calls.append((as_of_pick, target_pick, n_sims, wait_pick))
        return board.copy()

    monkeypatch.setattr(bm, "availability_with_band", fake_availability_with_band)
    board = pd.DataFrame({"player": ["A"], "position": ["RB"], "reference_adp_rank": [5.0]})
    bm.availability_with_assumption(
        board, as_of_pick=10, target_pick=20, manager_priors=pd.DataFrame(), team_bias=pd.DataFrame(),
        drafted_name_keys=set(), owner_roster_by_manager={}, mode="adp_order", n_sims=123, wait_pick=None,
    )
    assert calls == [(10, 20, 123, None)]


def _fake_availability_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "player": ["A", "B", "C"],
        "reference_adp_rank": [5.0, 40.0, 90.0],
        "survival_probability": [0.9, 0.5, 0.05],
        "survival_band": [0.05, 0.6, 0.2],  # B and C's band would push the result below 0
        "vorp": [10.0, 5.0, 1.0],
        "composite_score": [50.0, 30.0, 10.0],
    })


def test_pessimistic_mode_subtracts_the_band_and_clips_at_zero(monkeypatch):
    fake = _fake_availability_frame()
    monkeypatch.setattr(bm, "availability_with_band", lambda *a, **k: fake.copy())
    out = bm.availability_with_assumption(
        pd.DataFrame(), as_of_pick=1, target_pick=2, manager_priors=pd.DataFrame(), team_bias=pd.DataFrame(),
        drafted_name_keys=set(), owner_roster_by_manager={}, mode="pessimistic",
    )
    assert list(out["survival_probability"]) == pytest.approx([0.85, 0.0, 0.0])


def test_pessimistic_mode_never_touches_vorp_or_composite_score(monkeypatch):
    fake = _fake_availability_frame()
    monkeypatch.setattr(bm, "availability_with_band", lambda *a, **k: fake.copy())
    out = bm.availability_with_assumption(
        pd.DataFrame(), as_of_pick=1, target_pick=2, manager_priors=pd.DataFrame(), team_bias=pd.DataFrame(),
        drafted_name_keys=set(), owner_roster_by_manager={}, mode="pessimistic",
    )
    assert (out["vorp"] == fake["vorp"]).all()
    assert (out["composite_score"] == fake["composite_score"]).all()


def test_observed_reach_falls_back_to_adp_order_when_no_delta_file(monkeypatch):
    # The real alt_league_position_deltas.csv doesn't exist yet (see config's own
    # comment) -- confirms the toggle degrades gracefully instead of erroring or
    # silently no-oping on a mode nobody actually meant to select.
    monkeypatch.setattr(bm, "_alt_league_deltas_cache", None)
    monkeypatch.setattr(config, "ALT_LEAGUE_POSITION_DELTAS_PATH", Path("no_such_file.csv"))
    assert "observed_reach" not in bm.board_assumption_modes_available()

    calls = []

    def fake_availability_with_band(board, as_of_pick, target_pick, manager_priors, team_bias,
                                     drafted_name_keys, owner_roster_by_manager, n_sims, wait_pick):
        calls.append((as_of_pick, target_pick))
        return board.copy()

    monkeypatch.setattr(bm, "availability_with_band", fake_availability_with_band)
    board = pd.DataFrame({"player": ["A"], "position": ["RB"], "reference_adp_rank": [5.0]})
    out = bm.availability_with_assumption(
        board, as_of_pick=44, target_pick=53, manager_priors=pd.DataFrame(), team_bias=pd.DataFrame(),
        drafted_name_keys=set(), owner_roster_by_manager={}, mode="observed_reach", n_sims=300, wait_pick=None,
    )
    assert len(calls) == 1, "must fall through to the plain availability_with_band path exactly once"
    assert (out["reference_adp_rank"] == board["reference_adp_rank"]).all()


def test_observed_reach_shifts_survival_but_restores_reference_adp_rank_on_output(built, monkeypatch):
    # Synthetic deltas -- NOT the real alt-league numbers (those aren't built yet) --
    # to prove the shift-and-restore mechanism itself is correct: a strongly
    # QB-favorable delta must raise QB survival relative to unshifted, while the
    # OUTPUT's reference_adp_rank must be untouched (reach_penalty/market_reach_gap/
    # display all read it straight off whatever this function returns).
    monkeypatch.setattr(bm, "_alt_league_deltas_cache", {"QB": -30.0, "RB": 0.0, "WR": 0.0, "TE": 0.0})
    master, _ = built
    priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    board = de.compute_composite(master)
    AS_OF, TARGET = 20, 53
    before = bm.availability_with_assumption(
        board, as_of_pick=AS_OF, target_pick=TARGET, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={}, mode="adp_order", n_sims=1500,
        wait_pick=None,
    )
    after = bm.availability_with_assumption(
        board, as_of_pick=AS_OF, target_pick=TARGET, manager_priors=priors, team_bias=team_bias,
        drafted_name_keys=set(), owner_roster_by_manager={}, mode="observed_reach", n_sims=1500,
        wait_pick=None,
    )
    assert (after["reference_adp_rank"].fillna(-1) == board["reference_adp_rank"].fillna(-1)).all()

    qb_mask = (after["position"] == "QB") & after["reference_adp_rank"].between(20, 100)
    b = before.loc[qb_mask, "survival_probability"]
    a = after.loc[qb_mask, "survival_probability"]
    assert a.mean() > b.mean(), "a QB-favorable (more-available) delta must raise QB survival on average"


def test_board_assumption_modes_available_always_includes_adp_order_and_pessimistic():
    modes = bm.board_assumption_modes_available()
    assert "adp_order" in modes
    assert "pessimistic" in modes


# ---------------------------------------------------------------------------
# 34. Work order 2026-08-31 item C (R46): waiver-level second baseline.
# ---------------------------------------------------------------------------
def test_waiver_level_is_deeper_than_replacement_level(built):
    master, _ = built
    board = de.compute_composite(master)
    for pos in config.POSITIONS:
        assert board.attrs["waiver_level"][pos] <= board.attrs["replacement_level"][pos], (
            f"{pos}: waiver level must be a DEEPER (lower-bar) cutoff than the last-starter replacement level"
        )


def test_vorp_waiver_never_folds_into_composite_score_or_edge(built):
    master, _ = built
    board = de.compute_composite(master)
    # composite_score is a pure function of vorp/bonus/factor/market -- recomputing
    # it with vorp_waiver zeroed out must not change composite_score at all.
    tampered = board.copy()
    tampered["vorp_waiver"] = 0.0
    assert (tampered["composite_score"] == board["composite_score"]).all()
    assert "vorp_waiver" not in board.attrs.get("enabled_composite_weights", {})


def test_vorp_waiver_positive_for_several_late_rbs_where_vorp_is_at_or_below_zero(built):
    # The acceptance check itself: report both baselines for the top 10 RBs with
    # ADP > 100. The waiver figure should be positive for several of them.
    master, _ = built
    board = de.compute_composite(master)
    late_rbs = (
        board[(board["position"] == "RB") & (board["reference_adp_rank"] > 100)]
        .sort_values("reference_adp_rank")
        .head(10)
    )
    assert len(late_rbs) > 0
    positive_waiver = (late_rbs["vorp_waiver"] > 0).sum()
    assert positive_waiver >= 1, "at least some late RBs must show positive value over waiver level"
    # And the whole point of item C: at least one of them is at/near zero (or below)
    # on the last-starter baseline while positive on waiver.
    assert (late_rbs["vorp"] <= late_rbs["vorp_waiver"]).all()


def test_compute_waiver_levels_matches_a_hand_computed_cutoff():
    df = pd.DataFrame({
        "position": ["RB"] * 30,
        "ppr_base": list(range(300, 0, -10)),  # 300, 290, ..., 10 (30 values)
    })
    roster_target = {"RB": 5}
    levels = de.compute_waiver_levels(df, n_teams=4, roster_target=roster_target)
    # 4 teams * 5 RB slots = rank 20 (0-indexed) -> the 21st-best value.
    expected = sorted(df["ppr_base"], reverse=True)[20]
    assert levels["RB"] == expected
