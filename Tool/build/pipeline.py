#!/usr/bin/env python3
"""
The entire data build for the 2026 draft tool, in one file, safe to rerun any time.

    python build/pipeline.py

Every stage below reads only from the read-only input folders (2026/, drafts/,
the root ADP exports) and writes only into Tool/data/. Rerunning is always safe --
nothing here is stateful across runs. Exits nonzero if a hard-failure assertion
trips (an unmapped team/position code), so a bad build is visible immediately.

Stages, in order:
  1. load_props                -- {pos}_implied_props.csv x4, standardize columns, map team
  2. compute_bonus_model         -- lognormal per-game bonus estimate (spec Section 6.2)
  3. load_priors                  -- {pos}_player_priors.csv x4, backfill nfl_team from props
  4. load_oline                     -- oline_blend_rankings.csv, map team, assert zero unmapped
  5. ingest_adp                       -- NFFC + Sleeper -> canonical schema (spec Section 4.4)
  6. load_college_bias                  -- recall-based player -> college table (spec R13)
  7. parse_player_intel                   -- PLAYER-INTEL-2026.md -> structured table (work order item 3)
  8. compute_manager_priors                 -- from historical picks, not hand-transcribed
  9. compute_team_bias                        -- ditto
  10. build_player_master                       -- final join -> player_master.csv
  11. compute_adp_source_offsets                  -- Sleeper vs NFFC divergence by position (work order item 1)
  12. validate_top_adp_coverage                     -- top-150-by-either-source join is a hard failure (work order item 2)
  13. validate_and_report                             -- join_report.txt, pass/fail summary
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import lognorm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

ALLOWED_POSITIONS = {"QB", "RB", "WR", "TE", "K"}


class JoinReport:
    """Everything printed to join_report.txt. `fail` is a hard build-breaking problem
    (unmapped team/position code); `note` is informational -- unmatched names, coverage
    boundaries, low-sample flags -- visible always, but not fatal."""

    def __init__(self):
        self.hard_failures: list[str] = []
        self.notes: list[str] = []

    def fail(self, msg: str) -> None:
        self.hard_failures.append(msg)

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"Join report -- generated {datetime.now().isoformat(timespec='seconds')}\n")
            f.write("=" * 78 + "\n\n")
            f.write(f"HARD FAILURES ({len(self.hard_failures)}) -- these must be fixed before shipping\n")
            f.write("-" * 78 + "\n")
            for m in self.hard_failures:
                f.write(f"  ! {m}\n")
            f.write(f"\nNOTES / FLAGGED ({len(self.notes)}) -- unmatched names, coverage boundaries,\n")
            f.write("low-sample flags. Never suppressed; eyeball before trusting a build.\n")
            f.write("-" * 78 + "\n")
            for m in self.notes:
                f.write(f"  - {m}\n")


def _prob_at_least(mean_per_game: float, threshold: float, sigma: float) -> float:
    """P(per-game stat >= threshold), stat ~ Lognormal moment-matched to mean_per_game."""
    if pd.isna(mean_per_game) or mean_per_game <= 0:
        return 0.0
    mu = math.log(mean_per_game) - 0.5 * sigma ** 2
    return float(lognorm.sf(threshold, s=sigma, scale=math.exp(mu)))


# ---------------------------------------------------------------------------
# Stage 1: implied props
# ---------------------------------------------------------------------------
def load_props(position: str, report: JoinReport) -> pd.DataFrame:
    df = pd.read_csv(config.props_path(position))
    df["position"] = position

    mapped_team = df["team"].apply(lambda t: config.canonical_team(t, "clay"))
    for raw, player in zip(df["team"][mapped_team.isna()], df["player"][mapped_team.isna()]):
        report.fail(f"props/{position}: unmapped team '{raw}' for player '{player}'")
    df["nfl_team"] = mapped_team

    df["name_key"] = df["player"].apply(config.normalize_name)
    df["games_projected"] = df["clay_games"]

    if "ppr_market" in df.columns:
        df["ppr_base"] = df["ppr_market"].where(df["ppr_market"].notna(), df["ppr_clay_propadj"])
        df["ppr_base_source"] = np.where(df["ppr_market"].notna(), "market", "projection_propadj")
    else:
        df["ppr_base"] = df["ppr_clay_propadj"]
        df["ppr_base_source"] = "projection_propadj"

    rush_yds = df.get("clay_rush_yds", pd.Series(0.0, index=df.index)).fillna(0.0)
    rec_yds = df.get("clay_rec_yds", pd.Series(0.0, index=df.index)).fillna(0.0)
    games = df["clay_games"].replace(0, np.nan)
    df["scrimmage_yds_per_game"] = (rush_yds + rec_yds) / games
    df["pass_yds_per_game"] = (df["clay_pass_yds"] / games) if position == "QB" else 0.0

    return df


# ---------------------------------------------------------------------------
# Stage 2: bonus model (spec Section 6.2)
# ---------------------------------------------------------------------------
def compute_bonus_model(df: pd.DataFrame, position: str) -> pd.DataFrame:
    games_effective = (df["clay_games"] - config.GAMES_HAIRCUT[position]).clip(lower=0)

    sigma_scrim = config.BONUS_SIGMA_SCRIMMAGE[position]
    p_scrim = df["scrimmage_yds_per_game"].apply(
        lambda m: _prob_at_least(m, config.SCRIMMAGE_BONUS_THRESHOLD, sigma_scrim)
    )
    df["bonus_scrimmage_games_est"] = games_effective * p_scrim
    bonus_scrim_pts = df["bonus_scrimmage_games_est"] * config.BONUS_POINTS_PER_GAME

    if position == "QB":
        sigma_pass = config.BONUS_SIGMA_PASSING["QB"]
        p_pass = df["pass_yds_per_game"].apply(
            lambda m: _prob_at_least(m, config.PASSING_BONUS_THRESHOLD, sigma_pass)
        )
        df["bonus_pass_games_est"] = games_effective * p_pass
    else:
        df["bonus_pass_games_est"] = 0.0
    bonus_pass_pts = df["bonus_pass_games_est"] * config.BONUS_POINTS_PER_GAME

    df["bonus_est_ppr"] = bonus_scrim_pts + bonus_pass_pts
    return df


def print_bonus_calibration(props_all: pd.DataFrame) -> None:
    """Eyeball check against the archetype anchors in HANDOFF-draft-tool-context.md
    Section 1 (alpha WR ~6x 100-yard games/season, workhorse RB ~10x). If this drifts
    far from those anchors after a data refresh, retune BONUS_SIGMA_* in config.py."""
    print("\nBonus-model calibration (top 5 by ppr_base per position):")
    for pos in config.POSITIONS:
        top = props_all[props_all["position"] == pos].nlargest(5, "ppr_base")
        for _, r in top.iterrows():
            extra = f", pass_bonus_games={r['bonus_pass_games_est']:.1f}" if pos == "QB" else ""
            print(
                f"  {pos:2s} {r['player']:<24s} scrimmage_bonus_games={r['bonus_scrimmage_games_est']:.1f}"
                f"{extra}  (+{r['bonus_est_ppr']:.1f} ppr)"
            )


# ---------------------------------------------------------------------------
# Stage 3: factor-grid priors, team backfill (spec Section 5 rule #2)
# ---------------------------------------------------------------------------
# Known nickname-vs-full-name mismatches between other sources and the props files
# (which set the canonical name_key everything else joins onto). Every entry maps the
# OTHER source's normalized spelling -> props' spelling. Verified against both source
# rows before adding -- both sides of each mapping were confirmed to be the same real
# person, same team, same position. Not a guess: a bounded, documented build-layer
# correction (spec/CLAUDE.md's own stated escape hatch for exactly this situation).
#
# "chig okonkwo"/"cam ward": factor-grid priors use a nickname, props use the full name.
# "kenneth walker" / "cameron skattebo" / "kenny gainwell": ADP-side nickname
# mismatches against props' spelling, found via work order 2026-08-16 item 2's
# reclassified severity (any top-150-by-either-source player with no market join is
# now a hard failure, not a note -- this audit is what surfaced all three). Each is a
# single source disagreeing with props + the OTHER source: Sleeper writes "Kenneth
# Walker III" and "Cameron Skattebo" where NFFC agrees with props' "Ken Walker III" /
# "Cam Skattebo"; NFFC writes "Gainwell, Kenny" where props and Sleeper agree on
# "Kenneth Gainwell". Applied in `ingest_nffc_adp` / `ingest_sleeper_adp` (not just
# `load_priors`, where the first two entries below are used), since these are
# ADP-side mismatches, not priors-side ones.
NAME_ALIASES = {
    "chig okonkwo": "chigoziem okonkwo",
    "cam ward": "cameron ward",
    "kenneth walker": "ken walker",
    "cameron skattebo": "cam skattebo",
    "kenny gainwell": "kenneth gainwell",
}


def _apply_name_alias(name_key: str, report: JoinReport, context: str) -> str:
    alias = NAME_ALIASES.get(name_key)
    if alias is None:
        return name_key
    report.note(f"{context}: rekeyed '{name_key}' to '{alias}' via NAME_ALIASES")
    return alias


def load_priors(position: str, props_df: pd.DataFrame, report: JoinReport) -> pd.DataFrame:
    df = pd.read_csv(config.priors_path(position))
    # player_priors.csv's own "factor_score" column already holds the recomputed value, not
    # the published one (verified against wr_factor_scores.csv: Christian Watson reads 8 here,
    # matching factor_score_recomputed=8, not factor_score_published=11). Renamed on load so
    # the provenance guarantee from spec Section 5 rule #6 is visible in the column name itself.
    df = df.rename(columns={"factor_score": "factor_score_recomputed"})
    df["name_key"] = df["player_name_source"].apply(config.normalize_name)

    props_keys = set(props_df["name_key"])
    unresolved = df["name_key"].isin(props_keys) == False  # noqa: E712 (Series identity, not None check)
    for idx in df.index[unresolved]:
        key = df.at[idx, "name_key"]
        alias = NAME_ALIASES.get(key)
        if alias and alias in props_keys:
            report.note(
                f"priors/{position}: rekeyed '{df.at[idx, 'player_name_source']}' to props' "
                f"'{alias}' via NAME_ALIASES -- same real join key from here on, so both the "
                f"team backfill and the player_master merge pick up the props row"
            )
            df.at[idx, "name_key"] = alias

    team_by_key = props_df.set_index("name_key")["nfl_team"].to_dict()
    df["nfl_team"] = df["name_key"].map(team_by_key)

    missing = df[df["nfl_team"].isna()]
    for _, row in missing.iterrows():
        report.note(
            f"priors/{position}: no team backfill match for '{row['player_name_source']}' "
            f"(adp_position_rank {row.get('adp_position_rank')}) -- left blank, not guessed"
        )
    return df


# ---------------------------------------------------------------------------
# Stage 4: O-line blend
# ---------------------------------------------------------------------------
def load_oline(report: JoinReport) -> pd.DataFrame:
    df = pd.read_csv(config.oline_blend_path())
    mapped = df["team"].apply(lambda t: config.canonical_team(t, "clay"))
    for raw in df["team"][mapped.isna()]:
        report.fail(f"oline_blend_rankings: unmapped team code '{raw}'")
    df["nfl_team"] = mapped
    return df


# ---------------------------------------------------------------------------
# Stage 5: ADP ingestion (spec Section 4.4, the full contract)
# ---------------------------------------------------------------------------
def _validate_position(pos: str, source: str, raw: str, player: str, report: JoinReport) -> bool:
    """Returns True if this row should be kept."""
    if pos in config.DROP_POSITIONS.get(source, set()):
        return False
    if pos not in ALLOWED_POSITIONS:
        report.fail(f"{source}_adp: unrecognized position code '{raw}' for player '{player}'")
        return False
    return True


def ingest_nffc_adp(report: JoinReport) -> pd.DataFrame:
    raw = pd.read_csv(config.NFFC_ADP_RAW_PATH, sep="\t")
    cols = list(raw.columns)
    nfl_team_col = cols[3]  # index 3 (0-based) = 4th column = NFL team. Selected BY INDEX --
    # pandas silently renames the second "Team" column (drafting fantasy team) to "Team.1".

    for c in ("ADP", "Min Pick", "Max Pick", "# Picks"):
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    rows = []
    for _, r in raw.iterrows():
        pos = config.canonical_position(r["Position(s)"], "nffc")
        if not _validate_position(pos, "nffc", r["Position(s)"], r["Player"], report):
            continue
        first, last, suffix = config.parse_nffc_name(str(r["Player"]))
        team = config.canonical_team(r[nfl_team_col], "nffc")
        if team is None:
            report.fail(f"nffc_adp: unmapped team '{r[nfl_team_col]}' for player '{r['Player']}'")
        name_key = _apply_name_alias(config.normalize_name(f"{first} {last}"), report, "nffc_adp")
        rows.append(
            {
                "player_display": r["Player"],
                "first": first,
                "last": last,
                "suffix": suffix,
                "position": pos,
                "nfl_team": team,
                "name_key": name_key,
                "source": "NFFC",
                "adp_value": r["ADP"],
                "adp_min": r["Min Pick"],
                "adp_max": r["Max Pick"],
                "adp_n": r["# Picks"],
                "as_of": date.today().isoformat(),  # NFFC carries no date -- captured at ingestion
            }
        )
    df = pd.DataFrame(rows)
    df["adp_rank"] = df["adp_value"].rank(method="min")
    df["adp_usable_for_survival"] = df["adp_n"].fillna(0) >= config.ADP_MIN_N
    for _, row in df[~df["adp_usable_for_survival"]].iterrows():
        report.note(
            f"nffc_adp: '{row['player_display']}' has adp_n={row['adp_n']} "
            f"(< {config.ADP_MIN_N}) -- shown but excluded from survival math"
        )
    return df


def ingest_sleeper_adp(report: JoinReport) -> pd.DataFrame:
    path = config.latest_sleeper_adp_raw_path()
    raw = pd.read_csv(path)
    rows = []
    for _, r in raw.iterrows():
        pos = config.canonical_position(r["Pos"], "sleeper")
        if not _validate_position(pos, "sleeper", r["Pos"], r["Player"], report):
            continue
        first, last, suffix = config.parse_sleeper_name(str(r["Player"]))
        team = config.canonical_team(r["Team"], "sleeper")
        if team is None:
            report.fail(f"sleeper_adp: unmapped team '{r['Team']}' for player '{r['Player']}'")
        name_key = _apply_name_alias(config.normalize_name(f"{first} {last}"), report, "sleeper_adp")
        rows.append(
            {
                "player_display": r["Player"],
                "first": first,
                "last": last,
                "suffix": suffix,
                "position": pos,
                "nfl_team": team,
                "name_key": name_key,
                "source": "Sleeper",
                "adp_value": r["ADP"],
                "adp_rank": r["ADP"],
                "adp_min": np.nan,
                "adp_max": np.nan,
                "adp_n": np.nan,
                "as_of": r["Date Pulled"],
            }
        )
    df = pd.DataFrame(rows)
    df["adp_usable_for_survival"] = True
    return df


ADP_INGESTORS = {"NFFC": ingest_nffc_adp, "Sleeper": ingest_sleeper_adp}


def ingest_reference_and_comparison_adp(report: JoinReport) -> tuple[pd.DataFrame, pd.DataFrame]:
    ref_name = config.REFERENCE_ADP["source_name"]
    cmp_name = config.COMPARISON_ADP["source_name"]
    ref_df = ADP_INGESTORS[ref_name](report)
    cmp_df = ADP_INGESTORS[cmp_name](report)
    config.DATA_EXTERNAL.mkdir(parents=True, exist_ok=True)
    ref_df.to_csv(config.REFERENCE_ADP_PATH, index=False)
    cmp_df.to_csv(config.COMPARISON_ADP_PATH, index=False)
    return ref_df, cmp_df


# ---------------------------------------------------------------------------
# Stage 6: college bias -- the one genuinely hand-built, recall-labeled table (R13).
#
# Every entry below was checked against the real 2026 props files (grep-confirmed
# present) before inclusion. Players I could not confidently place, or who did not
# appear in this year's pool, are left out entirely rather than guessed -- a missing
# row means no signal, which is the correct behavior for a discount layer.
# ---------------------------------------------------------------------------
COLLEGE_RECALL = [
    ("Marvin Harrison Jr.", "WR", "Ohio State"),
    ("Justin Fields", "QB", "Ohio State"),
    ("Chris Olave", "WR", "Ohio State"),
    ("Garrett Wilson", "WR", "Ohio State"),
    ("Jaxon Smith-Njigba", "WR", "Ohio State"),
    ("Emeka Egbuka", "WR", "Ohio State"),
    ("TreVeyon Henderson", "RB", "Ohio State"),
    ("Terry McLaurin", "WR", "Ohio State"),
    ("J.K. Dobbins", "RB", "Ohio State"),
    ("Jeremy Ruckert", "TE", "Ohio State"),
    ("Cade Stover", "TE", "Ohio State"),
    ("C.J. Stroud", "QB", "Ohio State"),
    ("Jaylen Waddle", "WR", "Alabama"),
    ("Tua Tagovailoa", "QB", "Alabama"),
    ("Brian Robinson Jr.", "RB", "Alabama"),
    ("Jameson Williams", "WR", "Alabama"),
    ("Josh Jacobs", "RB", "Alabama"),
    ("Bryce Young", "QB", "Alabama"),
    ("Calvin Ridley", "WR", "Alabama"),
    ("DeVonta Smith", "WR", "Alabama"),
    ("Chase Brown", "RB", "Illinois"),
    ("Breece Hall", "RB", "Iowa State"),
    ("Brock Purdy", "QB", "Iowa State"),
    ("Charlie Kolar", "TE", "Iowa State"),
    ("Kyle Pitts", "TE", "Florida"),
    ("Malik Davis", "RB", "Florida"),
]


def load_college_bias(report: JoinReport) -> pd.DataFrame:
    rows = [
        {
            "player_display": p,
            "position": pos,
            "college": college,
            "name_key": config.normalize_name(p),
            "is_model_recall": True,
        }
        for p, pos, college in COLLEGE_RECALL
    ]
    df = pd.DataFrame(rows)
    config.DATA_EXTERNAL.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.COLLEGE_BIAS_PATH, index=False)
    schools = sorted(set(c for _, _, c in COLLEGE_RECALL))
    report.note(
        f"college_bias_recall: {len(df)} players mapped via model recall (not sourced from any "
        f"data file -- Sleeper's board carries no college field). Schools covered: {schools}. "
        f"No Florida State or UCF players in the current pool could be confidently matched, so "
        f"Cailen's and Jayden's college-bias discount has zero eligible targets this year -- a "
        f"real coverage gap, not a bug."
    )
    return df


# ---------------------------------------------------------------------------
# Stage 6.5: player intel (work order 2026-08-16 item 3)
# ---------------------------------------------------------------------------
INTEL_REQUIRED_COLUMNS = {"player", "position", "pick_window", "tag"}


def parse_player_intel(report: JoinReport) -> pd.DataFrame:
    """Parses the "Intel table" markdown table out of `2026/PLAYER-INTEL-2026.md` into
    a small structured frame, joined onto player_master later on name_key + position --
    same normalization + NAME_ALIASES path as every other source, reused rather than
    reimplemented.

    Only the ONE markdown table whose header contains all of INTEL_REQUIRED_COLUMNS is
    parsed; the file's other tables (tag legend, the "flagged for owner review"
    divergence tables) share the same `| ... |` shape but not that header, so they're
    skipped automatically rather than needing a hand-picked line range that would go
    stale the moment the doc is edited. `priority` and `note` are optional per the
    file's own "template for future years" footer.
    """
    empty = pd.DataFrame(columns=["name_key", "position", "player", "pick_window", "priority", "tag", "note"])
    path = config.PLAYER_INTEL_MD_PATH
    if not path.exists():
        report.note(f"player_intel: {path} not found -- layer produces no rows this build")
        return empty

    header: list[str] | None = None
    records: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if all(c.replace("-", "") == "" for c in cells):
            continue  # markdown header-separator row (---|---|---)
        if header is None:
            if INTEL_REQUIRED_COLUMNS <= set(cells):
                header = cells
            continue
        records.append(dict(zip(header, cells)))

    if not records:
        report.fail(
            f"player_intel: no table in {path} has a header containing "
            f"{sorted(INTEL_REQUIRED_COLUMNS)} -- expected the 'Intel table' section"
        )
        return empty

    df = pd.DataFrame(records)
    missing_cols = INTEL_REQUIRED_COLUMNS - set(df.columns)
    if missing_cols:
        report.fail(f"player_intel: table is missing required column(s) {sorted(missing_cols)}")
        return empty

    df["name_key"] = df["player"].apply(config.normalize_name)
    df["name_key"] = df["name_key"].apply(lambda k: _apply_name_alias(k, report, "player_intel"))
    df["pick_window"] = pd.to_numeric(df["pick_window"], errors="coerce")
    df["priority"] = pd.to_numeric(df["priority"], errors="coerce") if "priority" in df.columns else np.nan
    if "note" not in df.columns:
        df["note"] = ""

    allowed_tags = set(config.INTEL_TAG_SIGN) | config.INTEL_FILTER_TAGS
    for bad_tag in sorted(set(df["tag"]) - allowed_tags):
        report.fail(f"player_intel: unrecognized tag '{bad_tag}' -- expected one of {sorted(allowed_tags)}")

    keep = df[["name_key", "position", "player", "pick_window", "priority", "tag", "note"]]
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    keep.to_csv(config.PLAYER_INTEL_PATH, index=False)
    report.note(f"player_intel: {len(keep)} rows parsed from {path.name} ({keep['tag'].value_counts().to_dict()})")
    return keep


# ---------------------------------------------------------------------------
# Stage 7 & 8: manager priors and NFL-team bias, computed from historical picks
# rather than hand-transcribed (portability, spec R14).
# ---------------------------------------------------------------------------
def compute_manager_priors(report: JoinReport) -> tuple[pd.DataFrame, pd.DataFrame]:
    picks = pd.read_csv(config.ALL_DRAFT_PICKS_PATH)
    current = picks[(picks["current_member"] == "Y") & picks["manager"].notna() & (picks["manager"] != "")]

    per_draft = []
    for (manager, season, league), g in current.groupby(["manager", "season", "league"]):
        qb = g[g["pos"] == "QB"]
        te = g[g["pos"] == "TE"]
        k = g[g["pos"] == "K"]
        per_draft.append(
            {
                "manager": manager,
                "season": season,
                "league": league,
                "qb1_round": qb["round"].min() if len(qb) else np.nan,
                "te1_round": te["round"].min() if len(te) else np.nan,
                "k_round": k["round"].min() if len(k) else np.nan,
                "rb_in_r1_8": int(len(g[(g["pos"] == "RB") & (g["round"] <= 8)])),
                "wr_in_r1_8": int(len(g[(g["pos"] == "WR") & (g["round"] <= 8)])),
                "n_qb_drafted": len(qb),
                "n_te_drafted": len(te),
            }
        )
    per_draft_df = pd.DataFrame(per_draft)

    summaries = []
    for manager, g in per_draft_df.groupby("manager"):
        main = g[g["league"] == "main"]
        alt = g[g["league"] != "main"]
        n_main = len(main)
        qb1_alt_mean = alt["qb1_round"].mean() if len(alt) else np.nan
        te1_alt_mean = alt["te1_round"].mean() if len(alt) else np.nan
        qb1_main_mean = main["qb1_round"].mean()
        te1_main_mean = main["te1_round"].mean()
        bimodal = bool(
            len(alt)
            and (
                (pd.notna(qb1_alt_mean) and pd.notna(qb1_main_mean) and abs(qb1_alt_mean - qb1_main_mean) >= 3)
                or (pd.notna(te1_alt_mean) and pd.notna(te1_main_mean) and abs(te1_alt_mean - te1_main_mean) >= 3)
            )
        )
        confidence = "low" if n_main <= 1 else ("medium" if n_main <= 2 else "high")
        summaries.append(
            {
                "manager": manager,
                "n_main_drafts": n_main,
                "n_alt_drafts": len(alt),
                "confidence": confidence,
                "bimodal_flag": bimodal,
                "qb1_round_mean": qb1_main_mean,
                "qb1_round_std": main["qb1_round"].std(ddof=0) if n_main > 1 else np.nan,
                "te1_round_mean": te1_main_mean,
                "te1_round_std": main["te1_round"].std(ddof=0) if n_main > 1 else np.nan,
                "rb_in_r1_8_mean": main["rb_in_r1_8"].mean(),
                "wr_in_r1_8_mean": main["wr_in_r1_8"].mean(),
                "k_round_mean": main["k_round"].mean(),
                "qb1_round_alt_mean": qb1_alt_mean,
                "te1_round_alt_mean": te1_alt_mean,
            }
        )
        if confidence == "low":
            report.note(f"manager_priors: '{manager}' has only {n_main} main-league draft on file -- widen variance")
        if bimodal:
            report.note(
                f"manager_priors: '{manager}' shows a >=3-round main-vs-alt divergence at QB or TE "
                f"-- model as bimodal, do not average into a false middle"
            )

    summary_df = pd.DataFrame(summaries)
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    summary_df.to_csv(config.MANAGER_PRIORS_PATH, index=False)

    owner_drift = per_draft_df[per_draft_df["manager"] == config.OWNER].sort_values(["season", "league"])
    owner_drift[["season", "league", "qb1_round", "te1_round"]].to_csv(
        config.DATA_DERIVED / "owner_drift.csv", index=False
    )
    return summary_df, per_draft_df


def compute_team_bias(report: JoinReport) -> pd.DataFrame:
    picks = pd.read_csv(config.ALL_DRAFT_PICKS_PATH)
    picks = picks[picks["pos"] != "DEF"]
    current = picks[(picks["current_member"] == "Y") & picks["manager"].notna() & (picks["manager"] != "")]

    league_team_rate = picks["nfl_team"].value_counts(normalize=True)
    total_by_manager = current.groupby("manager").size()

    records = []
    for (manager, team), n_picks in current.groupby(["manager", "nfl_team"]).size().items():
        expected = league_team_rate.get(team, 0.0) * total_by_manager[manager]
        if expected < 1.0:
            continue  # too little expected volume for a ratio to mean anything
        records.append(
            {
                "manager": manager,
                "nfl_team": team,
                "picks": int(n_picks),
                "expected_picks": round(expected, 2),
                "bias_ratio": round(n_picks / expected, 2),
            }
        )
    tb = pd.DataFrame(records).sort_values(["manager", "bias_ratio"], ascending=[True, False])
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    tb.to_csv(config.TEAM_BIAS_PATH, index=False)
    strong = tb[(tb["bias_ratio"] >= 1.5) | (tb["bias_ratio"] <= 0.5)]
    report.note(
        f"team_bias: computed from {len(current)} historical picks; {len(strong)} manager/team pairs "
        f"show a >=1.5x or <=0.5x bias ratio against league-wide expectation."
    )
    return tb


# ---------------------------------------------------------------------------
# Stage 9: build player_master.csv
# ---------------------------------------------------------------------------
CORE_COLUMN_ORDER = [
    "player", "position", "nfl_team", "name_key", "college", "college_is_model_recall",
    "ppr_base", "ppr_base_source", "games_projected", "proj_source", "proj_as_of", "propadj_method",
    "bonus_est_ppr", "bonus_scrimmage_games_est", "bonus_pass_games_est",
    "factor_score_recomputed", "factor_score_rank", "factor_score_normalized", "adp_minus_score_rank",
    "archetype", "archetype_cohort_mapping", "injury_concern",
    "p_returned_on_adp", "p_got_injured", "p_boomed", "p_busted", "p_fine",
    "prob_basis", "prob_is_player_specific", "tier_color", "tier_rank",
    "ol_blend_rank", "ol_blend_tier", "ol_source_gap_pff_minus_clay",
    "reference_source", "reference_adp_value", "reference_adp_rank", "reference_as_of",
    "comparison_source", "comparison_adp_value", "comparison_adp_rank", "comparison_adp_min",
    "comparison_adp_max", "comparison_adp_n", "comparison_adp_usable", "comparison_as_of",
    "adp_rank_divergence", "team_conflict", "data_quality_note", "join_coverage",
    "intel_windows", "intel_priority", "intel_tag", "intel_note",
]


def _prep_adp_side(adp_df: pd.DataFrame, label: str) -> pd.DataFrame:
    keep = adp_df[["name_key", "position", "nfl_team", "source", "adp_value", "adp_rank",
                    "adp_min", "adp_max", "adp_n", "adp_usable_for_survival", "as_of"]].copy()
    keep = keep.rename(
        columns={
            "nfl_team": f"{label}_nfl_team",
            "source": f"{label}_source",
            "adp_value": f"{label}_adp_value",
            "adp_rank": f"{label}_adp_rank",
            "adp_min": f"{label}_adp_min",
            "adp_max": f"{label}_adp_max",
            "adp_n": f"{label}_adp_n",
            "adp_usable_for_survival": f"{label}_adp_usable",
            "as_of": f"{label}_as_of",
        }
    )
    return keep


def _dedupe_intel_for_master(intel_df: pd.DataFrame, report: JoinReport) -> pd.DataFrame:
    """`player_intel.csv` is one row per (player, pick_window) -- a player can be a
    target across several windows (A.J. Brown: pick 20 AND pick 29 in the 2026 table).
    `player_master` is one row per PLAYER, so this collapses to a single
    tag/priority/note before the merge. Merging the un-deduped table directly would
    silently multiply that player's player_master row for every extra window -- a
    one-to-many join, the same class of bug as an unmatched name (spec/CLAUDE.md rule
    #3), just inflating row count instead of losing rows.

    `intel_windows` keeps the full set of tagged pick numbers as a display string so
    that information isn't lost, just moved out of the row-count-sensitive columns.
    """
    rows = []
    for (name_key, position), g in intel_df.groupby(["name_key", "position"]):
        tags = set(g["tag"])
        if len(tags) > 1:
            report.note(
                f"player_intel: '{g['player'].iloc[0]}' ({position}) has conflicting tags "
                f"across pick windows: {sorted(tags)} -- using the highest-priority row"
            )
        chosen = g.sort_values("priority", na_position="last").iloc[0]
        windows = ", ".join(str(int(w)) for w in sorted(g["pick_window"].dropna().unique()))
        rows.append(
            {
                "name_key": name_key, "position": position, "intel_tag": chosen["tag"],
                "intel_priority": chosen["priority"], "intel_windows": windows, "intel_note": chosen["note"],
            }
        )
    return pd.DataFrame(rows, columns=["name_key", "position", "intel_tag", "intel_priority", "intel_windows", "intel_note"])


def build_player_master(
    props_all: pd.DataFrame,
    priors_all: pd.DataFrame,
    oline_df: pd.DataFrame,
    ref_df: pd.DataFrame,
    cmp_df: pd.DataFrame,
    college_df: pd.DataFrame,
    intel_df: pd.DataFrame,
    report: JoinReport,
) -> pd.DataFrame:
    priors_slim = priors_all.drop(columns=["nfl_team"])  # already backfilled FROM props; avoid dup
    merged = props_all.merge(
        priors_slim, on=["name_key", "position"], how="outer", suffixes=("", "_priors"), indicator=True
    )
    merged["join_coverage"] = merged["_merge"].map(
        {"left_only": "props_only", "right_only": "priors_only", "both": "both"}
    )
    merged = merged.drop(columns=["_merge"])

    priors_only = merged[merged["join_coverage"] == "priors_only"]
    for _, row in priors_only.iterrows():
        report.note(
            f"{row['position']}: '{row.get('player_name_source')}' has factor-grid data but no "
            f"implied-props row -- no ppr_base valuation for this player"
        )
    props_only_counts = merged[merged["join_coverage"] == "props_only"].groupby("position").size()
    for pos, n in props_only_counts.items():
        report.note(
            f"{pos}: {n} players have implied-props coverage but no factor-grid row -- expected "
            f"(analyst covers only the top tier per position), not a join failure"
        )

    if "player_name_source" in merged.columns:
        merged["player"] = merged["player"].fillna(merged["player_name_source"])
    # priors_slim's own nfl_team was dropped before the merge (it's just the props-backfilled
    # value duplicated), so "nfl_team" here is already props' column, correctly NaN only for
    # priors_only rows (which have no props row to backfill a team from in the first place).

    oline_slim = oline_df[["nfl_team", "ol_blend_rank", "ol_blend_tier", "ol_source_gap_pff_minus_clay"]]
    merged = merged.merge(oline_slim, on="nfl_team", how="left")

    ref_slim = _prep_adp_side(ref_df, "reference")
    cmp_slim = _prep_adp_side(cmp_df, "comparison")
    merged = merged.merge(ref_slim, on=["name_key", "position"], how="left")
    merged = merged.merge(cmp_slim, on=["name_key", "position"], how="left")

    def _conflict(row, other_team_col):
        other = row.get(other_team_col)
        return bool(pd.notna(row.get("nfl_team")) and pd.notna(other) and row["nfl_team"] != other)

    merged["team_conflict"] = merged.apply(
        lambda r: _conflict(r, "reference_nfl_team") or _conflict(r, "comparison_nfl_team"), axis=1
    )
    merged["adp_rank_divergence"] = merged["comparison_adp_rank"] - merged["reference_adp_rank"]

    college_slim = college_df[["name_key", "position", "college", "is_model_recall"]].rename(
        columns={"is_model_recall": "college_is_model_recall"}
    )
    merged = merged.merge(college_slim, on=["name_key", "position"], how="left")

    if len(intel_df):
        intel_slim = _dedupe_intel_for_master(intel_df, report)
        merged = merged.merge(intel_slim, on=["name_key", "position"], how="left")
        unmatched = set(zip(intel_df["name_key"], intel_df["position"])) - set(zip(merged["name_key"], merged["position"]))
        for name_key, position in unmatched:
            display = intel_df.loc[(intel_df["name_key"] == name_key) & (intel_df["position"] == position), "player"].iloc[0]
            report.note(f"player_intel: '{display}' ({position}) has no matching player_master row -- intel row will not join")

    merged["data_quality_note"] = ""
    merged.loc[merged["team_conflict"], "data_quality_note"] += (
        "reference/comparison ADP disagree on NFL team; "
    )
    merged.loc[merged["join_coverage"] == "priors_only", "data_quality_note"] += (
        "no implied-props row, ppr_base is blank; "
    )

    for col in CORE_COLUMN_ORDER:
        if col not in merged.columns:
            merged[col] = np.nan
    ordered = CORE_COLUMN_ORDER + [c for c in merged.columns if c not in CORE_COLUMN_ORDER]
    merged = merged[ordered]

    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    merged.to_csv(config.PLAYER_MASTER_PATH, index=False)
    return merged


# ---------------------------------------------------------------------------
# Stage 10: ADP source offsets + top-150 join-coverage validation
# (work order 2026-08-16 items 1 and 2)
# ---------------------------------------------------------------------------
def compute_adp_source_offsets(master: pd.DataFrame, report: JoinReport, top_n: int = 150) -> pd.DataFrame:
    """Measures how far Sleeper (reference) and NFFC (comparison) actually diverge,
    per position, among the players who matter -- computed at build time from real
    data, not hard-coded, so next season's numbers are next season's rather than a
    constant that silently goes stale the moment either site's population shifts.

    mean(reference_adp_rank - comparison_adp_value) among players ranked <= top_n by
    Sleeper and present in both sources. Positive means Sleeper ranks that position
    LATER than NFFC's mean pick (a model centered on NFFC would overestimate how early
    that position leaves); negative means Sleeper ranks it EARLIER.
    """
    top = master[
        master["reference_adp_rank"].notna()
        & (master["reference_adp_rank"] <= top_n)
        & master["comparison_adp_value"].notna()
    ]
    rows = [
        {
            "position": pos,
            "mean_offset_reference_minus_comparison": round(float((g["reference_adp_rank"] - g["comparison_adp_value"]).mean()), 1),
            "n": len(g),
        }
        for pos, g in top.groupby("position")
    ]
    offsets = pd.DataFrame(rows).sort_values("position").reset_index(drop=True)
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    offsets.to_csv(config.ADP_SOURCE_OFFSETS_PATH, index=False)
    report.note(
        "adp_source_offsets (top "
        f"{top_n} by Sleeper rank): "
        + "; ".join(
            f"{r.position} {r.mean_offset_reference_minus_comparison:+.1f} (n={r.n})" for r in offsets.itertuples()
        )
        + " -- positive means Sleeper ranks that position later than NFFC's mean pick"
    )
    return offsets


def validate_top_adp_coverage(
    master: pd.DataFrame, ref_df: pd.DataFrame, cmp_df: pd.DataFrame, report: JoinReport, top_n: int = 150
) -> None:
    """Any player inside the top `top_n` of EITHER ADP source that fails to join into
    player_master is a HARD FAILURE, not a note. join_report previously reported "0
    hard failures" while a top-20 player (Ken Walker III / Kenneth Walker III) had no
    market data from either source at all -- the report itself was miscalibrated,
    which is worse than the underlying join bug (spec/CLAUDE.md rule #3: silent join
    failure is the primary correctness risk in this project; a report that says
    everything's fine while hiding one is the same failure one level up).

    Restricted to `config.POSITIONS` (QB/RB/WR/TE): K is out of scope for
    player_master by design (R22 -- no props/factor-grid data exists for kickers), so
    a real, well-ranked Sleeper kicker (e.g. Brandon Aubrey) correctly has no
    player_master row and must not be flagged as a join failure.
    """
    master_lookup = master.set_index(["name_key", "position"])
    for label, adp_df, value_col in (
        ("reference", ref_df, "reference_adp_rank"),
        ("comparison", cmp_df, "comparison_adp_value"),
    ):
        top = adp_df[(adp_df["adp_rank"] <= top_n) & adp_df["position"].isin(config.POSITIONS)]
        for _, row in top.iterrows():
            key = (row["name_key"], row["position"])
            if key not in master_lookup.index:
                report.fail(
                    f"{label}_adp: top-{top_n} player '{row['player_display']}' (rank "
                    f"{row['adp_rank']:.0f}) has no player_master row at all -- join failed"
                )
                continue
            mrow = master_lookup.loc[key]
            if isinstance(mrow, pd.DataFrame):  # duplicate key -- check all instances
                found = mrow[value_col].notna().any()
            else:
                found = pd.notna(mrow[value_col])
            if not found:
                report.fail(
                    f"{label}_adp: top-{top_n} player '{row['player_display']}' (rank "
                    f"{row['adp_rank']:.0f}) joined to player_master but {value_col} is null"
                )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    report = JoinReport()

    print("Loading implied props + computing bonus model...")
    props_frames = []
    for pos in config.POSITIONS:
        df = load_props(pos, report)
        df = compute_bonus_model(df, pos)
        props_frames.append(df)
    props_all = pd.concat(props_frames, ignore_index=True)
    print_bonus_calibration(props_all)

    print("\nLoading factor-grid priors and backfilling nfl_team...")
    priors_frames = [
        load_priors(pos, props_all[props_all["position"] == pos], report) for pos in config.POSITIONS
    ]
    priors_all = pd.concat(priors_frames, ignore_index=True)

    print("Loading O-line blend...")
    oline_df = load_oline(report)

    print("Ingesting reference/comparison ADP...")
    ref_df, cmp_df = ingest_reference_and_comparison_adp(report)

    print("Loading college-bias recall table...")
    college_df = load_college_bias(report)

    print("Parsing player intel...")
    intel_df = parse_player_intel(report)

    print("Computing manager priors and team bias from historical picks...")
    compute_manager_priors(report)
    compute_team_bias(report)

    print("Building player_master.csv...")
    master = build_player_master(props_all, priors_all, oline_df, ref_df, cmp_df, college_df, intel_df, report)

    print("Computing ADP source offsets and validating top-150 join coverage...")
    compute_adp_source_offsets(master, report)
    validate_top_adp_coverage(master, ref_df, cmp_df, report)

    report.write(config.JOIN_REPORT_PATH)

    print(f"\n{'=' * 60}")
    print(f"player_master.csv: {len(master)} rows -> {config.PLAYER_MASTER_PATH}")
    print(f"join_report.txt: {len(report.hard_failures)} hard failures, {len(report.notes)} notes")
    print(f"{'=' * 60}")

    if report.hard_failures:
        print("\nBUILD FAILED -- hard failures present. See join_report.txt.")
        for m in report.hard_failures:
            print(f"  ! {m}")
        return 1

    print("Build OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
