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

            survival = de.simulate_intervening_picks(
                pool, intervening, manager_priors, team_bias, owner_roster_by_manager,
                owner=config.OWNER, n_sims=n_sims, rng=rng,
            )
            survival = np.clip(survival, 0.005, 0.995)

            actual = (pool["own_actual_pick"].to_numpy() >= target_pick).astype(float)
            for i in range(len(pool)):
                rows.append(
                    {
                        "season": season, "as_of_pick": as_of_pick, "target_pick": target_pick,
                        "position": pool.iloc[i]["position"], "predicted": float(survival[i]), "actual": float(actual[i]),
                    }
                )

    return pd.DataFrame(rows), notes


def _report(results: pd.DataFrame, notes: list[str]) -> str:
    lines = []
    lines.append("Availability-model backtest -- spec Section 12.5 / R25")
    lines.append("=" * 70)
    lines.extend(notes)
    lines.append("")
    if results.empty:
        lines.append("No backtest rows produced -- see notes above.")
        return "\n".join(lines)

    brier = float(((results["predicted"] - results["actual"]) ** 2).mean())
    lines.append(f"Overall Brier score: {brier:.4f} over {len(results)} (pick-window, player) observations")
    lines.append("(0 = perfect, 0.25 = always guessing 50%, 1.0 = confidently wrong every time)")
    lines.append("")
    lines.append("By position (expect QB/TE worse -- 2026 scoring didn't exist when these drafts happened):")
    for pos, g in results.groupby("position"):
        b = float(((g["predicted"] - g["actual"]) ** 2).mean())
        lines.append(
            f"  {pos:3s} n={len(g):5d}  Brier={b:.4f}  mean_predicted={g['predicted'].mean():.3f}  "
            f"actual_survival_rate={g['actual'].mean():.3f}"
        )
    lines.append("")
    lines.append("Calibration by probability decile (well-calibrated means predicted ~= actual):")
    r = results.copy()
    r["decile"] = pd.cut(r["predicted"], bins=np.arange(0, 1.01, 0.1), include_lowest=True)
    calib = r.groupby("decile").agg(
        n=("actual", "size"), mean_predicted=("predicted", "mean"), actual_survival_rate=("actual", "mean")
    )
    lines.append(calib.to_string())
    lines.append("")
    lines.append(
        "Reminder: sanity check on calibration, not a tuning target. Two seasons of one league is a "
        "small sample -- overfitting to it is easy. A QB/TE miss above may reflect the OLD 4pt passing "
        "TD scoring rather than a broken model; do not retune BONUS_SIGMA_* or manager-priors weights "
        "to force this specific backtest closer to well-calibrated."
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
