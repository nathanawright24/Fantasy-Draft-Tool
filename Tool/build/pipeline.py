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
  10.5. compute_sleeper_name_crosswalk            -- sleeper_name_key -> master name_key (work order 2026-08-24b item 1)
  11. compute_adp_source_offsets                  -- Sleeper vs NFFC divergence by position (work order item 1)
  12. validate_top_adp_coverage                     -- top-150-by-either-source join is a hard failure (work order item 2)
  12.5. validate_fade_effectiveness                   -- warn when a `fade` tag can't change a pick (work order 2026-09-05 item 4)
  13. validate_and_report                             -- join_report.txt, pass/fail summary
"""
from __future__ import annotations

import math
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import lognorm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import config  # noqa: E402
# draft_engine, for validate_fade_effectiveness only (work order 2026-09-05 item 4).
# The build layer normally stays clear of app/, but the alternative here is a second
# copy of the VORP/composite math living in this file purely to answer "would this
# fade have moved him?" -- and a second copy of the valuation is exactly the drift
# risk config.LAYERS' own comment warns about. draft_engine imports nothing but
# config, numpy/pandas/scipy and the stdlib, so this stays acyclic and pulls in no
# UI framework (the repo's `grep -l streamlit app/*.py` rule is unaffected).
import draft_engine as de  # noqa: E402

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
# Nickname-vs-full-name mismatches (work order 2026-08-24b item 1): the table itself now
# lives in config.py as config.NAME_ALIASES, shared with app/ as the FALLBACK path for a
# name the sleeper_name_crosswalk.csv this module emits (see
# compute_sleeper_name_crosswalk below) doesn't cover. See config.py's own comment for
# the full per-entry justification.
def _apply_name_alias(name_key: str, report: JoinReport, context: str) -> str:
    alias = config.NAME_ALIASES.get(name_key)
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
        alias = config.NAME_ALIASES.get(key)
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


def _assert_sleeper_freshness(raw: pd.DataFrame, path: Path, report: JoinReport) -> None:
    """Work order 2026-08-29b item 2 / 2026-08-29 item 0 (R42): a build against a stale
    Sleeper pull must fail loudly. Reads `Date Pulled` straight off the resolved file
    (not a filename guess) and hard-fails if it is more than
    config.ADP_STALENESS_MAX_DAYS old -- this is the guard that was missing when the
    08-16 file rode along, unnoticed, for two weeks next to a correct 08-29 file."""
    if "Date Pulled" not in raw.columns or raw.empty:
        report.fail(f"sleeper_adp: {path.name} has no 'Date Pulled' column -- cannot verify freshness")
        return
    pulled = pd.to_datetime(raw["Date Pulled"], errors="coerce").dropna()
    if pulled.empty:
        report.fail(f"sleeper_adp: {path.name}'s 'Date Pulled' column has no parseable dates")
        return
    as_of = pulled.max().date()
    age_days = (date.today() - as_of).days
    if age_days > config.ADP_STALENESS_MAX_DAYS:
        report.fail(
            f"sleeper_adp: {path.name} is {age_days} days old (Date Pulled={as_of.isoformat()}, "
            f"today={date.today().isoformat()}) -- exceeds ADP_STALENESS_MAX_DAYS="
            f"{config.ADP_STALENESS_MAX_DAYS}. Re-scrape before trusting this build."
        )


def ingest_sleeper_adp(report: JoinReport, path: Path | None = None) -> pd.DataFrame:
    """`path` overrides config.latest_sleeper_adp_raw_path() -- only ever used by tests
    verifying the staleness guard against a deliberately old file; production always
    resolves the latest one."""
    path = path or config.latest_sleeper_adp_raw_path()
    raw = pd.read_csv(path)
    _assert_sleeper_freshness(raw, path, report)
    # Schema change (work order 2026-08-29b item 2 / 2026-08-29 item 0, R42): the export
    # grew from 8 columns to 10, splitting "ADP Rank" out from "ADP" -- they diverge
    # deeper in the board (row 201: rank 201, ADP 204). The two used to be assigned
    # from the same column; that was silently wrong the moment the split appeared, not
    # before. Falls back to "ADP" for the pre-split file shape (defensive, not expected
    # to matter going forward since the build always resolves the LATEST file).
    has_adp_rank = "ADP Rank" in raw.columns
    # "Match Key" (new column, e.g. "jahmyr gibbs|RB") is Sleeper's own pre-normalized,
    # position-qualified key. NOT used for the core join below: measured against the
    # real 08-29 file, Sleeper's own normalization strips hyphens with no space
    # ("jaxon smithnjigba"), where ours replaces them with one ("jaxon smith njigba",
    # which matches props' spelling) -- substituting it broke exactly the hyphenated
    # names it was supposed to help with (Smith-Njigba, St.-Brown-style names,
    # Croskey-Merritt; 3 top-150 hard-failures on first try). Captured here as its own
    # column instead, so compute_sleeper_name_crosswalk can try it as a FALLBACK
    # resolution path -- validated against real player_master rows there, which this
    # function can't do (player_master doesn't exist yet at ingestion time). Other ADP
    # sources (NFFC) will not supply this column -- ingest_nffc_adp is unaffected.
    has_match_key = "Match Key" in raw.columns
    rows = []
    for _, r in raw.iterrows():
        pos = config.canonical_position(r["Pos"], "sleeper")
        if not _validate_position(pos, "sleeper", r["Pos"], r["Player"], report):
            continue
        first, last, suffix = config.parse_sleeper_name(str(r["Player"]))
        team = config.canonical_team(r["Team"], "sleeper")
        if team is None:
            report.fail(f"sleeper_adp: unmapped team '{r['Team']}' for player '{r['Player']}'")
        raw_name_key = config.normalize_name(f"{first} {last}")
        name_key = _apply_name_alias(raw_name_key, report, "sleeper_adp")
        match_key_raw = r.get("Match Key") if has_match_key else None
        match_key_name = None
        if pd.notna(match_key_raw) and "|" in str(match_key_raw):
            candidate = str(match_key_raw).rsplit("|", 1)[0].strip()
            match_key_name = candidate or None
        rows.append(
            {
                "player_display": r["Player"],
                "first": first,
                "last": last,
                "suffix": suffix,
                "position": pos,
                "nfl_team": team,
                "name_key": name_key,
                # Pre-alias key, e.g. "kenneth walker" where name_key (post-alias) is "ken
                # walker" -- item 1's crosswalk needs BOTH: this is the spelling the live
                # Sleeper draft-picks API will actually send, name_key is what player_master
                # keys on. Only meaningful for source=="Sleeper"; harmless on NFFC rows,
                # which never feed the crosswalk (see compute_sleeper_name_crosswalk).
                "sleeper_raw_name_key": raw_name_key,
                "sleeper_match_key_name": match_key_name,
                "source": "Sleeper",
                "adp_value": r["ADP"],
                "adp_rank": r["ADP Rank"] if has_adp_rank else r["ADP"],
                "adp_min": np.nan,
                "adp_max": np.nan,
                "adp_n": np.nan,
                "as_of": r["Date Pulled"],
            }
        )
    df = pd.DataFrame(rows)
    df["adp_usable_for_survival"] = True
    return df


# ---------------------------------------------------------------------------
# Declarative ADP ingestion (work order 2026-08-29 item 4 / R40): a new source needs
# a data/raw/adp_sources/<name>.yml mapping, not a new Python function. The two
# functions above (ingest_nffc_adp / ingest_sleeper_adp) are kept, UNCHANGED, as the
# "direct parse" this layer is proven equivalent against -- see
# test_core.py::test_mapping_layer_reproduces_player_master_byte_identically and
# ADP_SOURCE_MAPPINGS_DIR's own YAML files for the two reference mappings shipped.
# ---------------------------------------------------------------------------
_NAME_PARSERS = {
    "first_last": config.parse_sleeper_name,
    "last_first_comma": config.parse_nffc_name,
}


def load_adp_source_mapping(source_name: str) -> dict:
    path = config.ADP_SOURCE_MAPPINGS_DIR / f"{source_name.lower()}.yml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def ingest_via_mapping(mapping: dict, path: Path, report: JoinReport) -> pd.DataFrame:
    """Canonical fields: player, position, team, adp_value, adp_rank, adp_min, adp_max,
    adp_n, as_of -- every ADP source, however it spells its own columns, produces a
    frame with this same shape. Reproduces ingest_nffc_adp/ingest_sleeper_adp exactly
    for the two mappings this repo ships; see those functions' own comments for the
    per-quirk reasoning (NFFC's team-by-index selection, Sleeper's ADP Rank / Match Key
    / staleness handling) this function's mapping-driven branches mirror.
    """
    source_name = mapping["source_name"]
    source_key = source_name.lower()  # matches config.POSITION_MAP_EXCEPTIONS / TEAM_MAP_EXCEPTIONS keys
    raw = pd.read_csv(path, sep=mapping["delimiter"])

    if mapping.get("staleness_check"):
        as_of_col = mapping["as_of"]["column"]
        _assert_sleeper_freshness(raw.rename(columns={as_of_col: "Date Pulled"}), path, report)

    cols = mapping["columns"]
    numeric_cols = [cols[f] for f in ("adp_value", "adp_min", "adp_max", "adp_n") if cols.get(f)]
    for c in numeric_cols:
        raw[c] = pd.to_numeric(raw[c], errors="coerce")

    if "team_by_index" in mapping:
        team_col = list(raw.columns)[mapping["team_by_index"]]
    else:
        team_col = cols["team"]

    parse_name = _NAME_PARSERS[mapping["name_format"]]
    has_adp_rank_col = mapping["adp_rank"]["mode"] == "column_with_fallback" and mapping["adp_rank"]["column"] in raw.columns
    match_key_col = mapping.get("match_key_column")
    has_match_key = bool(match_key_col) and match_key_col in raw.columns
    as_of_mode = mapping["as_of"]["mode"]

    rows = []
    for _, r in raw.iterrows():
        pos = config.canonical_position(r[cols["position"]], mapping["position_source_code"])
        if not _validate_position(pos, source_key, r[cols["position"]], r[cols["player"]], report):
            continue
        first, last, suffix = parse_name(str(r[cols["player"]]))
        team = config.canonical_team(r[team_col], mapping["team_source_code"])
        if team is None:
            report.fail(f"{source_key}_adp: unmapped team '{r[team_col]}' for player '{r[cols['player']]}'")
        raw_name_key = config.normalize_name(f"{first} {last}")
        name_key = _apply_name_alias(raw_name_key, report, f"{source_key}_adp")

        row = {
            "player_display": r[cols["player"]],
            "first": first,
            "last": last,
            "suffix": suffix,
            "position": pos,
            "nfl_team": team,
            "name_key": name_key,
            "source": source_name,
            "adp_value": r[cols["adp_value"]],
            "adp_min": r[cols["adp_min"]] if cols.get("adp_min") else np.nan,
            "adp_max": r[cols["adp_max"]] if cols.get("adp_max") else np.nan,
            "adp_n": r[cols["adp_n"]] if cols.get("adp_n") else np.nan,
        }
        if has_adp_rank_col:
            row["adp_rank"] = r[mapping["adp_rank"]["column"]]
        elif mapping["adp_rank"]["mode"] == "column_with_fallback":
            row["adp_rank"] = row["adp_value"]
        # "computed_rank_from_value" mode is filled in after the loop (needs the whole column).
        row["as_of"] = r[mapping["as_of"]["column"]] if as_of_mode == "column" else date.today().isoformat()
        if source_key == "sleeper":
            row["sleeper_raw_name_key"] = raw_name_key
            match_key_name = None
            if has_match_key:
                raw_match = r.get(match_key_col)
                if pd.notna(raw_match) and "|" in str(raw_match):
                    candidate = str(raw_match).rsplit("|", 1)[0].strip()
                    match_key_name = candidate or None
            row["sleeper_match_key_name"] = match_key_name
        rows.append(row)

    df = pd.DataFrame(rows)
    if mapping["adp_rank"]["mode"] == "computed_rank_from_value":
        df["adp_rank"] = df["adp_value"].rank(method="min")

    if mapping["usable_for_survival"] == "always_true":
        df["adp_usable_for_survival"] = True
    else:
        df["adp_usable_for_survival"] = df["adp_n"].fillna(0) >= config.ADP_MIN_N
        for _, row in df[~df["adp_usable_for_survival"]].iterrows():
            report.note(
                f"{source_key}_adp: '{row['player_display']}' has adp_n={row['adp_n']} "
                f"(< {config.ADP_MIN_N}) -- shown but excluded from survival math"
            )
    return df


def ingest_nffc_via_mapping(report: JoinReport, path: Path | None = None) -> pd.DataFrame:
    path = path or config.NFFC_ADP_RAW_PATH
    return ingest_via_mapping(load_adp_source_mapping("NFFC"), path, report)


def ingest_sleeper_via_mapping(report: JoinReport, path: Path | None = None) -> pd.DataFrame:
    path = path or config.latest_sleeper_adp_raw_path()
    return ingest_via_mapping(load_adp_source_mapping("Sleeper"), path, report)


# The live path (work order 2026-08-29 item 4): REFERENCE_ADP / COMPARISON_ADP select
# by source name, same as before -- the two functions behind those names are now the
# generic, mapping-driven ones, not one bespoke function per source.
ADP_INGESTORS = {"NFFC": ingest_nffc_via_mapping, "Sleeper": ingest_sleeper_via_mapping}


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


def _align_intel_row_cells(header: list[str], cells: list[str], report: JoinReport) -> list[str]:
    """Self-heals one specific malformed-row shape in the intel table: a `hard_avoid`
    row that gives `pick_window` its own "--" placeholder but drops the optional
    `priority` cell entirely instead of also placing "--" there (Rashee Rice's
    hard_avoid row does this correctly; Jayden Higgins' and Jonathon Brooks' rows in
    the 2026-08-30 rewrite do not). Every cell from `tag` onward then shifts left by
    one, so `tag` ends up holding the `note` prose and `note` disappears -- the exact
    "unrecognized tag" failure this produced.

    Only engages when the row is short by exactly one cell AND a straight positional
    zip would produce a `tag` outside `config.INTEL_VALID_TAGS` -- a validly-shaped
    short row (the file's own template allows omitting the trailing `note`) zips
    correctly already and is returned untouched. If inserting a blank `priority`
    still doesn't yield a recognized tag, the row doesn't match this known pattern;
    fall through unchanged and let the existing tag-validation `report.fail` below
    catch it, rather than silently guessing further.

    `2026/` is read-only (CLAUDE.md) -- this is the build-layer correction that policy
    calls for, not an edit to the source file.
    """
    if len(cells) != len(header) - 1 or "priority" not in header:
        return cells
    if dict(zip(header, cells)).get("tag") in config.INTEL_VALID_TAGS:
        return cells
    fixed = cells[:]
    fixed.insert(header.index("priority"), "")
    if dict(zip(header, fixed)).get("tag") in config.INTEL_VALID_TAGS:
        label = cells[header.index("player")] if "player" in header else "<unknown row>"
        report.note(
            f"player_intel: '{label}' row was missing its `priority` cell (pick_window's own "
            f"'--' placeholder wasn't duplicated) -- inferred a blank priority so tag/note land "
            f"correctly instead of shifting left"
        )
        return fixed
    return cells


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
        cells = _align_intel_row_cells(header, cells, report)
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

    for bad_tag in sorted(set(df["tag"]) - config.INTEL_VALID_TAGS):
        report.fail(f"player_intel: unrecognized tag '{bad_tag}' -- expected one of {sorted(config.INTEL_VALID_TAGS)}")

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


def compute_round_band_position_shares(report: JoinReport) -> pd.DataFrame:
    """Work order 2026-08-29c item 7 (R43): "encode the round-band positional appetite
    empirically from drafts/all_draft_picks_2022-2025.csv rather than as a hand-set
    constant... recompute each season; it is a property of the league, not a number
    to paste." Main league only (the owner's own request: "assume a running back
    hungry market, feel free to scrape the old drafts to prove this fact" was scoped
    to the main league he actually drafts in, matching manager_priors/team_bias's own
    `league == "main"` convention elsewhere in this function's neighbors).

    One row per (round_band, position): the position's share of picks made inside
    that band, across all 4 seasons on file, recomputed from the raw picks every
    build rather than pasted once and left to drift. `config.ROUND_BANDS` is the
    single source of truth for the band boundaries.
    """
    picks = pd.read_csv(config.ALL_DRAFT_PICKS_PATH)
    main = picks[picks["league"] == "main"]

    rows = []
    for label, lo, hi in config.ROUND_BANDS:
        band = main[(main["round"] >= lo) & (main["round"] <= hi)]
        n_band = len(band)
        shares = band["pos"].value_counts(normalize=True) * 100.0 if n_band else pd.Series(dtype=float)
        for pos in config.POSITIONS:
            rows.append({
                "round_band": label, "round_lo": lo, "round_hi": hi, "position": pos,
                "share_pct": round(float(shares.get(pos, 0.0)), 1), "n_picks_in_band": n_band,
            })
    df = pd.DataFrame(rows)
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    df.to_csv(config.ROUND_BAND_POSITION_SHARES_PATH, index=False)
    report.note(
        f"round_band_position_shares: {len(main)} main-league picks across "
        f"{main['season'].nunique()} seasons, {len(config.ROUND_BANDS)} bands."
    )
    return df


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


def _kicker_rows_from_reference(ref_df: pd.DataFrame, report: JoinReport) -> pd.DataFrame:
    """Work order 2026-08-24 item 7 (R35): player_master carries zero K rows today
    (R22 -- no props/factor-grid coverage exists for kickers), so the tool has nothing
    to recommend at the owner's actual pick 188. Adds K rows sourced from the
    REFERENCE ADP ingestion only (Sleeper by default) -- name, team, and ADP, nothing
    else. Every props/factor/bonus/intel column is left NaN by the caller's merge,
    which is what keeps a kicker out of VORP (draft_engine's replacement-level loop
    only ever runs over config.POSITIONS, which has no "K") and out of every route
    (config.ROSTER_TARGET has no "K" key either, so every route filter that reads it
    already excludes K for free)."""
    k = ref_df[ref_df["position"] == "K"].drop_duplicates(subset=["name_key"])
    cols = ["name_key", "position", "player", "nfl_team", "reference_source",
            "reference_adp_value", "reference_adp_rank", "reference_as_of"]
    if k.empty:
        report.note("kickers: reference ADP source has no K rows -- no kicker rows added to player_master")
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame({
        "name_key": k["name_key"], "position": "K", "player": k["player_display"],
        "nfl_team": k["nfl_team"], "reference_source": k["source"],
        "reference_adp_value": k["adp_value"], "reference_adp_rank": k["adp_rank"],
        "reference_as_of": k["as_of"],
    }).reset_index(drop=True)
    report.note(
        f"kickers: {len(out)} K rows added from {k['source'].iloc[0]} ADP, no props/factor-grid "
        f"coverage by design (R22) -- excluded from VORP and every route by construction"
    )
    return out


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

    kicker_rows = _kicker_rows_from_reference(ref_df, report)
    if len(kicker_rows):
        merged = pd.concat([merged, kicker_rows], ignore_index=True)

    for col in CORE_COLUMN_ORDER:
        if col not in merged.columns:
            merged[col] = np.nan
    ordered = CORE_COLUMN_ORDER + [c for c in merged.columns if c not in CORE_COLUMN_ORDER]
    merged = merged[ordered]

    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    merged.to_csv(config.PLAYER_MASTER_PATH, index=False)
    return merged


# ---------------------------------------------------------------------------
# Stage 9.5: Sleeper name crosswalk (work order 2026-08-24b item 1)
# ---------------------------------------------------------------------------
def compute_sleeper_name_crosswalk(ref_df: pd.DataFrame, cmp_df: pd.DataFrame, master: pd.DataFrame, report: JoinReport) -> pd.DataFrame:
    """sleeper_name_key -> player_master name_key, for every Sleeper ADP row that
    actually joined a player_master row -- the crosswalk the live sync path
    (app/draft_state.py) reads BEFORE falling back to config.NAME_ALIASES.

    Sleeper's own ADP ingestion (whichever of ref_df/cmp_df has source=="Sleeper" --
    normally ref_df, since COMPARISON_ADP is hardcoded to NFFC) already resolves each
    row's pre-alias key (`sleeper_raw_name_key`) to the post-alias `name_key` that
    joins onto player_master. This function just captures that resolution instead of
    throwing it away once player_master.csv is written -- "the ADP file is already an
    authoritative Sleeper-to-master crosswalk," per the work order.

    Resolution order per row (work order 2026-08-29b item 2 / 2026-08-29 item 0, R42):
    Sleeper's own `sleeper_match_key_name` FIRST, our constructed+aliased `name_key`
    as FALLBACK, keeping whichever one actually matches a real player_master row. Not
    "Match Key always wins": measured against the 08-29 file, Match Key's own hyphen
    handling ("jaxon smithnjigba", no space) disagrees with props' spelling ("jaxon
    smith njigba") for every hyphenated name, so trying it unconditionally in
    ingest_sleeper_adp's own join broke 3 real top-150 players. Validating each
    candidate against master here (which ingest_sleeper_adp can't do -- player_master
    doesn't exist yet at ingestion time) is what makes "first pass, with fallback"
    actually safe.

    Only built when Sleeper is actually one of the two ingested sources: if the league
    ever runs with an all-NFFC configuration, live Sleeper polling is disabled anyway
    (main_cockpit.sync_from_sleeper's own is_sleeper gate), so there is nothing for a
    crosswalk to serve.
    """
    cols = ["sleeper_name_key", "master_name_key", "player_display", "position"]
    sleeper_df = None
    for candidate in (ref_df, cmp_df):
        if "source" in candidate.columns and (candidate["source"] == "Sleeper").any():
            sleeper_df = candidate[candidate["source"] == "Sleeper"]
            break
    if sleeper_df is None:
        report.note("sleeper_name_crosswalk: Sleeper is not an ingested ADP source this build -- crosswalk not built (live Sleeper sync is disabled in this configuration anyway)")
        return pd.DataFrame(columns=cols)

    master_keys = set(zip(master["name_key"], master["position"]))
    rows = []
    rescued_by_match_key = 0
    for _, r in sleeper_df.iterrows():
        position = r["position"]
        constructed_ok = (r["name_key"], position) in master_keys
        match_key_name = r.get("sleeper_match_key_name")
        match_key_ok = pd.notna(match_key_name) and (match_key_name, position) in master_keys
        if match_key_ok:
            resolved = match_key_name
            if not constructed_ok:
                rescued_by_match_key += 1
        elif constructed_ok:
            resolved = r["name_key"]
        else:
            continue
        rows.append({
            "sleeper_name_key": r["sleeper_raw_name_key"],
            "master_name_key": resolved,
            "player_display": r["player_display"],
            "position": position,
        })
    out = pd.DataFrame(rows, columns=cols).drop_duplicates(subset=["sleeper_name_key"])
    config.DATA_DERIVED.mkdir(parents=True, exist_ok=True)
    out.to_csv(config.SLEEPER_NAME_CROSSWALK_PATH, index=False)
    rewritten = out[out["sleeper_name_key"] != out["master_name_key"]]
    report.note(
        f"sleeper_name_crosswalk: {len(out)} Sleeper names mapped to a player_master row "
        f"({len(sleeper_df) - len(out)} Sleeper rows had no player_master match, e.g. players "
        f"outside config.POSITIONS coverage); {len(rewritten)} of the {len(out)} required a "
        f"rewrite (normalization or alias difference) rather than an identity match; "
        f"{rescued_by_match_key} resolved via Match Key only, after our own construction "
        f"failed to match player_master"
    )
    return out


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


def validate_fade_effectiveness(master: pd.DataFrame, report: JoinReport) -> pd.DataFrame:
    """Warns when a `fade` tag cannot plausibly change which player gets drafted
    (work order 2026-09-05 item 4).

    This is the class of bug the 2026-09-05 intel rewrite was fixing, and the reason
    it is worth a build-time check rather than a comment: `fade` is a BOUNDED nudge,
    hard-capped at config.INTEL_NUDGE_CAP points on composite_score, so on a player
    the projection already ranks at the top of his position it can only reorder him
    against near neighbours -- it can never remove him. Measured on this build,
    Christian McCaffrey's fade moved him ZERO places (RB3 before the nudge, RB3
    after) and Josh Allen's left him at QB1 both ways. Neither tag was malformed,
    neither was ignored, and neither did anything; the build said "0 hard failures"
    and the tool kept suggesting both. `hard_avoid` is the tag with actual removal
    power (a filter in top_recommendations/candidates_for_pick, never arithmetic),
    and that is what the message points at.

    The test is the OUTCOME, not the arithmetic: apply the fade, then ask whether the
    player is still inside the top config.FADE_INERT_TIER_DEPTH[pos] of his own
    position by composite_score -- i.e. still one of the players the owner intends to
    draft there, so still an active recommendation. Position rank, not overall rank,
    because that's the comparison the pick actually turns on: Allen's fade cost him
    12 overall places and still left him the best quarterback available, which is
    precisely the "stop suggesting him in round 1" complaint.

    A note, not a `fail`: an inert fade is a mismatch between what a tag was meant to
    express and what the tag can do, not a broken input. The build is still correct
    and the board is still usable -- but nobody should have to re-derive the nudge
    cap against a vorp number by hand to find out the tag is decorative.

    Returns the per-fade-row diagnostic frame (empty when nothing is tagged `fade`),
    so tests/test_core.py can assert on the numbers rather than parse the prose.
    """
    cols = ["player", "position", "vorp", "pos_rank_before", "pos_rank_after",
            "overall_rank_before", "overall_rank_after", "inert"]
    if "intel_tag" not in master.columns or not (master["intel_tag"] == "fade").any():
        return pd.DataFrame(columns=cols)
    if not config.layer_on("player_intel"):
        # Bail BEFORE measuring rather than after. With the layer off compute_composite
        # sets intel_nudge_pts to a flat 0.0, so "before" and "after" are the same
        # frame and every top-of-position fade would measure as a zero-place move --
        # technically true (nothing is applied at all) but it would report an inert
        # TAG when the real story is a disabled LAYER, and it would fire for fades
        # that are perfectly effective whenever the layer is switched back on.
        report.note(
            "player_intel: layer is off, so fade-effectiveness was not checked -- no intel "
            "nudge reaches composite_score at all in this configuration"
        )
        return pd.DataFrame(columns=cols)

    scored = de.compute_composite(master)
    skill = scored[scored["position"].isin(config.POSITIONS)].copy()

    # The counterfactual is "same board, this row's nudge removed" -- subtract the
    # nudge back out rather than recomputing with the tag stripped, so both rankings
    # come from one compute_composite call and can't disagree for any other reason.
    before = skill["composite_score"] - skill["intel_nudge_pts"]
    skill["pos_rank_before"] = before.groupby(skill["position"]).rank(ascending=False, method="min")
    skill["pos_rank_after"] = skill.groupby("position")["composite_score"].rank(ascending=False, method="min")
    skill["overall_rank_before"] = before.rank(ascending=False, method="min")
    skill["overall_rank_after"] = skill["composite_score"].rank(ascending=False, method="min")

    fades = skill[skill["intel_tag"] == "fade"].copy()
    depth = fades["position"].map(config.FADE_INERT_TIER_DEPTH)
    fades["inert"] = fades["pos_rank_after"] <= depth

    for r in fades[fades["inert"]].itertuples():
        pos_depth = config.FADE_INERT_TIER_DEPTH.get(r.position)
        moved = (
            "moves him 0 places"
            if r.pos_rank_after == r.pos_rank_before
            else f"moves him {int(r.pos_rank_after - r.pos_rank_before)} place(s)"
        )
        report.note(
            f"player_intel: `fade` on '{r.player}' ({r.position}, vorp {r.vorp:.1f}) looks inert -- "
            f"the +/-{config.INTEL_NUDGE_CAP:.0f}-point cap {moved} in the {r.position} composite "
            f"ranking ({r.position}{int(r.pos_rank_before)} -> {r.position}{int(r.pos_rank_after)}), "
            f"still inside the top {pos_depth} the roster targets at that position, so he stays an "
            f"active recommendation. Overall rank {int(r.overall_rank_before)} -> "
            f"{int(r.overall_rank_after)}. A bounded nudge cannot remove a player this highly "
            f"projected; use `hard_avoid` if the intent is to stop the tool suggesting him"
        )

    return fades[cols].sort_values("pos_rank_after").reset_index(drop=True)


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
    compute_round_band_position_shares(report)

    print("Building player_master.csv...")
    master = build_player_master(props_all, priors_all, oline_df, ref_df, cmp_df, college_df, intel_df, report)

    print("Building Sleeper name crosswalk...")
    compute_sleeper_name_crosswalk(ref_df, cmp_df, master, report)

    print("Computing ADP source offsets and validating top-150 join coverage...")
    compute_adp_source_offsets(master, report)
    validate_top_adp_coverage(master, ref_df, cmp_df, report)

    print("Checking whether every `fade` tag can actually change a pick...")
    inert = validate_fade_effectiveness(master, report)
    if len(inert):
        n_inert = int(inert["inert"].sum())
        print(f"  {n_inert} of {len(inert)} fade tag(s) look inert -- see join_report.txt")

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
