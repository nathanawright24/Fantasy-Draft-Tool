#!/usr/bin/env python3
"""
Backtest (spec Section 12.5 / R25): replay 2024 and 2025 pick-by-pick, ask the SAME
availability model the live tool uses (draft_engine.simulate_intervening_picks) for
survival probabilities at each of the owner's real historical picks, and score them
against what actually happened -- Brier score, plus a calibration curve in probability
deciles.

    python backtest.py

Two constraints on interpretation, both from the spec, both worth repeating every run:
  - Sanity check on calibration, not a tuning target -- two seasons of one league is a
    small sample, and overfitting to it is easy.
  - The scoring rules changed for 2026 (5pt passing TDs, two new per-game bonuses).
    A miss on QB/TE timing in 2024/2025 may reflect the OLD scoring rather than a
    broken model. Expect QB/TE calibration to look worse than RB/WR, and don't
    "correct" for it.

No period-accurate ADP exists for 2024/2025 (this repo only carries 2026 NFFC/Sleeper
snapshots), so each test season's market-ADP baseline is proxied from that same
player's real draft position in the OTHER main-league seasons on file (2022/2023 and
whichever of 2024/2025 isn't under test) -- non-circular (a season never uses its own
outcome as its own predictor), but necessarily thin for one-off rookies who only ever
appear in the season being tested. Those players simply have no proxy and are excluded
from that season's pool; the exclusion count is reported, not papered over.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_TOOL_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOL_ROOT))
sys.path.insert(0, str(_TOOL_ROOT / "app"))
import config  # noqa: E402
import draft_engine as de  # noqa: E402

TEST_SEASONS = [2024, 2025]
N_SIMS = 1000
REPORT_PATH = config.DATA_DERIVED / "backtest_report.txt"


def _build_adp_proxy(main_picks: pd.DataFrame, exclude_season: int) -> pd.DataFrame:
    """Per (name_key, position, nfl_team), the mean/min/max/n overall pick across every
    OTHER main-league season on file. Keyed on all three fields, not name alone --
    these are the same abbreviated names ("J. Taylor") the spec's join rule (Section 5
    rule #1) warns collide across players without position+team in the key.
    """
    other = main_picks[main_picks["season"] != exclude_season].copy()
    other["name_key"] = other["player"].apply(config.normalize_name)
    grouped = (
        other.groupby(["name_key", "pos", "nfl_team"])["overall"]
        .agg(adp_value="mean", adp_min="min", adp_max="max", adp_n="count")
        .reset_index()
        .rename(columns={"pos": "position"})
    )
    return grouped


def _roster_counts_at(season_log: pd.DataFrame, as_of_pick: int) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    done = season_log[season_log["overall"] <= as_of_pick]
    for _, r in done.iterrows():
        manager = r["manager"]
        if pd.isna(manager):
            continue
        counts.setdefault(manager, {}).setdefault(r["pos"], 0)
        counts[manager][r["pos"]] += 1
    return counts


def run_backtest(
    picks_path: Path = config.ALL_DRAFT_PICKS_PATH, n_sims: int = N_SIMS, seed: int = 0
) -> tuple[pd.DataFrame, list[str]]:
    """Returns (results, notes). `results` has one row per (season, owner pick-window,
    candidate player): season, as_of_pick, target_pick, position, predicted survival
    probability, and the actual 0/1 outcome from the real historical log."""
    picks = pd.read_csv(picks_path)
    main_picks = picks[picks["league"] == "main"].copy()
    manager_priors = pd.read_csv(config.MANAGER_PRIORS_PATH)
    team_bias = pd.read_csv(config.TEAM_BIAS_PATH)
    rng = np.random.default_rng(seed)

    rows: list[dict] = []
    notes: list[str] = []

    for season in TEST_SEASONS:
        season_log = main_picks[main_picks["season"] == season].sort_values("overall").reset_index(drop=True)
        if season_log.empty:
            notes.append(f"{season}: no main-league picks on file -- skipped.")
            continue
        season_log["name_key"] = season_log["player"].apply(config.normalize_name)

        proxy = _build_adp_proxy(main_picks, exclude_season=season)
        proxy = proxy[proxy["position"].isin(config.POSITIONS)]
        if proxy.empty:
            notes.append(f"{season}: no ADP proxy coverage from other seasons -- skipped.")
            continue

        proxy = proxy.merge(
            season_log[["name_key", "pos", "nfl_team", "overall"]].rename(
                columns={"pos": "position", "overall": "own_actual_pick"}
            ),
            on=["name_key", "position", "nfl_team"],
            how="left",
        )
        proxy["own_actual_pick"] = proxy["own_actual_pick"].fillna(np.inf)
        proxy["reference_adp_rank"] = proxy["adp_value"].rank(method="min")
        proxy["comparison_adp_usable"] = True
        proxy = proxy.rename(
            columns={"adp_value": "comparison_adp_value", "adp_min": "comparison_adp_min",
                     "adp_max": "comparison_adp_max", "adp_n": "comparison_adp_n"}
        )
        proxy["college"] = np.nan  # no college data available for this backtest

        nathan_picks = sorted(season_log.loc[season_log["manager"] == config.OWNER, "overall"].tolist())
        if len(nathan_picks) < 2:
            notes.append(f"{season}: fewer than 2 owner picks on file -- no pick-windows to test.")
            continue

        rookies_excluded = int((proxy["comparison_adp_n"] == 0).sum())
        n_no_proxy = len(
            set(zip(season_log["name_key"], season_log["pos"], season_log["nfl_team"]))
            - set(zip(proxy["name_key"], proxy["position"], proxy["nfl_team"]))
        )
        notes.append(
            f"{season}: {len(proxy)} skill-position players have an ADP proxy from other seasons; "
            f"{n_no_proxy} players drafted this season have none (one-off rookies, no other-season "
            f"appearance) and are excluded from this season's pool."
        )

        for as_of_pick, target_pick in zip(nathan_picks[:-1], nathan_picks[1:]):
            pool = proxy[proxy["own_actual_pick"] > as_of_pick].reset_index(drop=True)
            if pool.empty:
                continue
            intervening = [
                (int(r["overall"]), int(r["round"]), r["manager"])
                for _, r in season_log[
                    (season_log["overall"] > as_of_pick) & (season_log["overall"] < target_pick)
                ].iterrows()
                if r["manager"] != config.OWNER
            ]
            owner_roster_by_manager = _roster_counts_at(season_log, as_of_pick)

            # Monte Carlo (existing arm) and the lognormal baseline (work order
            # 2026-08-24 item 1 / R36 -- the lognormal has never been scored before this)
            # on the IDENTICAL observation set, so the bake-off is apples to apples.
            mc = np.clip(
                de.simulate_intervening_picks(
                    pool, intervening, manager_priors, team_bias, owner_roster_by_manager,
                    owner=config.OWNER, n_sims=n_sims, rng=rng,
                ),
                0.005, 0.995,
            )
            lognormal = np.clip(
                np.array([de._survival_baseline_row(r, as_of_pick, target_pick)[0] for _, r in pool.iterrows()]),
                0.005, 0.995,
            )
            blend = np.clip(0.5 * mc + 0.5 * lognormal, 0.005, 0.995)

            actual = (pool["own_actual_pick"].to_numpy() >= target_pick).astype(float)
            for i in range(len(pool)):
                rows.append(
                    {
                        "season": season, "as_of_pick": as_of_pick, "target_pick": target_pick,
                        "position": pool.iloc[i]["position"],
                        "predicted_montecarlo": float(mc[i]), "predicted_lognormal": float(lognormal[i]),
                        "predicted_blend": float(blend[i]), "actual": float(actual[i]),
                    }
                )

    return pd.DataFrame(rows), notes


METHODS = ["montecarlo", "lognormal", "blend"]
DECISION_BAND = (0.15, 0.85)


def _brier(predicted: pd.Series, actual: pd.Series) -> float:
    return float(((predicted - actual) ** 2).mean())


def _decile_calibration(predicted: pd.Series, actual: pd.Series) -> pd.DataFrame:
    r = pd.DataFrame({"predicted": predicted, "actual": actual})
    r["decile"] = pd.cut(r["predicted"], bins=np.arange(0, 1.01, 0.1), include_lowest=True)
    return r.groupby("decile").agg(n=("actual", "size"), mean_predicted=("predicted", "mean"),
                                    actual_survival_rate=("actual", "mean"))


def score_methods(results: pd.DataFrame) -> dict:
    """Overall Brier, decision-band Brier (predicted in DECISION_BAND -- more useful
    than the overall figure, which is close to meaningless when ~90% of observations
    are foregone conclusions, but NOT the thing that decides AVAILABILITY_METHOD --
    see decide_availability_method's docstring for why: band membership is defined by
    each method's own predictions, so decision_band_n differs across methods and their
    Briers are supporting evidence, not a sound head-to-head comparison), and
    calibration deciles for each of montecarlo / lognormal / blend."""
    out = {}
    for method in METHODS:
        col = f"predicted_{method}"
        pred = results[col]
        band_mask = pred.between(*DECISION_BAND)
        out[method] = {
            "overall_brier": _brier(pred, results["actual"]),
            "decision_band_brier": _brier(pred[band_mask], results.loc[band_mask, "actual"]) if band_mask.any() else float("nan"),
            "decision_band_n": int(band_mask.sum()),
            "calibration": _decile_calibration(pred, results["actual"]),
        }
    return out


def decide_availability_method(scores: dict) -> tuple[str, str]:
    """R36's decision, reasoned in the order work order 2026-08-24b item 6 asks for:
    CAPACITY leads, Brier is supporting evidence, never the reverse.

    The original version of this function opened with a mechanical rule -- 'if the
    decision-band Briers differ by more than ~25%, use the better one alone, otherwise
    blend' -- and only fell back on the capacity argument to override that rule's
    'otherwise blend' branch. That is backwards for a second reason beyond ordering:
    the ~25% figure the rule fired on is not a sound comparison in the first place.
    Decision-band membership (`predicted in DECISION_BAND`) is defined by EACH
    method's OWN predictions, so the three methods are scored on different
    observation counts -- montecarlo, lognormal, and blend routinely land in the
    hundreds apart (see the n column `_report` prints alongside this). Brier scores
    computed on different samples are not comparable, so "18% gap, under the 25%
    threshold" was never the kind of number that rule could safely act on.

    montecarlo is the only one of the three that is capacity-safe (R20/R21: exactly k
    players leave in k real picks, by construction). lognormal is marginal and
    uncapacitated by design; blend reintroduces roughly half of that defect by
    averaging it back in (measured on a real pick 44->53 window, k=8: montecarlo
    expects ~9 departures, blend ~34, lognormal alone ~59). That is a STRUCTURAL
    property of each method, independent of any Brier number, and it is decisive on
    its own -- which is why this function no longer branches on the Brier gap at all.

    The Brier scores below are reported as weak supporting evidence only: montecarlo
    has in fact won decision-band Brier in every run so far, which is consistent with
    (not proof of) the capacity argument, and a future run where it stopped winning
    would not by itself be a reason to reconsider -- the observation-count mismatch
    means that comparison was never strong enough to lean on either way.
    """
    mc = scores["montecarlo"]["decision_band_brier"]
    log = scores["lognormal"]["decision_band_brier"]
    blend = scores["blend"]["decision_band_brier"]
    mc_n = scores["montecarlo"]["decision_band_n"]
    log_n = scores["lognormal"]["decision_band_n"]
    blend_n = scores["blend"]["decision_band_n"]
    mc_str = "n/a" if pd.isna(mc) else f"{mc:.4f}"
    log_str = "n/a" if pd.isna(log) else f"{log:.4f}"
    blend_str = "n/a" if pd.isna(blend) else f"{blend:.4f}"
    return "montecarlo", (
        "montecarlo is the only capacity-safe method (R20/R21) -- decisive on its own, independent of Brier. "
        f"Decision-band Brier is supporting evidence only, and the three methods were scored on different "
        f"observation counts (montecarlo n={mc_n}, lognormal n={log_n}, blend n={blend_n} -- band membership "
        f"depends on each method's own predictions), so a gap between them is not a sound comparison. For "
        f"reference: montecarlo={mc_str}, lognormal={log_str}, blend={blend_str}."
    )


def _report(results: pd.DataFrame, notes: list[str]) -> str:
    lines = []
    lines.append("Availability-model backtest -- spec Section 12.5 / R25, method bake-off per work order 2026-08-24 item 1 / R36")
    lines.append("=" * 70)
    lines.extend(notes)
    lines.append("")
    if results.empty:
        lines.append("No backtest rows produced -- see notes above.")
        return "\n".join(lines)

    scores = score_methods(results)
    lines.append(f"{len(results)} (pick-window, player) observations feed all three methods, but see the")
    lines.append("decision-band n's below before comparing their Briers -- they are NOT the same sample.")
    lines.append("")

    # Work order 2026-08-24b item 6 (R39): decision leads, raw table follows as
    # supporting evidence -- the reverse of this report's original order, to match
    # decide_availability_method's own reasoning (capacity first, Brier second).
    chosen, reason = decide_availability_method(scores)
    lines.append(f"Decision: {reason}")
    lines.append(f"-> AVAILABILITY_METHOD = \"{chosen}\"")
    lines.append("")

    lines.append("Supporting evidence (Brier: 0 = perfect, 0.25 = always guessing 50%, 1.0 = confidently wrong every time):")
    lines.append(f"{'method':12s} {'overall_brier':>14s} {'decision_band_brier':>20s} {'decision_band_n':>16s}")
    for method in METHODS:
        s = scores[method]
        lines.append(f"{method:12s} {s['overall_brier']:14.4f} {s['decision_band_brier']:20.4f} {s['decision_band_n']:16d}")
    lines.append(f"(decision band = predicted in {DECISION_BAND}; the three decision_band_n's differ because band")
    lines.append(" membership is defined by each method's own predictions -- these Briers are not directly comparable)")
    lines.append("")

    for method in METHODS:
        lines.append(f"Calibration by probability decile -- {method} (well-calibrated means predicted ~= actual):")
        lines.append(scores[method]["calibration"].to_string())
        lines.append("")

    lines.append("By position, montecarlo (expect QB/TE worse -- 2026 scoring didn't exist when these drafts happened):")
    for pos, g in results.groupby("position"):
        b = _brier(g["predicted_montecarlo"], g["actual"])
        lines.append(
            f"  {pos:3s} n={len(g):5d}  Brier={b:.4f}  mean_predicted={g['predicted_montecarlo'].mean():.3f}  "
            f"actual_survival_rate={g['actual'].mean():.3f}"
        )
    lines.append("")
    lines.append(
        "Reminder: sanity check on calibration, not a tuning target. Two seasons of one league is a "
        "small sample -- overfitting to it is easy. A QB/TE miss above may reflect the OLD 4pt passing "
        "TD scoring rather than a broken model; do not retune BONUS_SIGMA_* or manager-priors weights "
        "to force this specific backtest closer to well-calibrated. This bake-off picks between two "
        "EXISTING methods -- it must not become a tuning loop for either one's internals."
    )
    return "\n".join(lines)


def main() -> int:
    results, notes = run_backtest()
    report = _report(results, notes)
    print(report)
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\nWritten to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
