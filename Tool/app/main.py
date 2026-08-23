"""
Streamlit entry point. Board with expandable layers, manual pick entry with undo,
optional Sleeper sync, and an always-visible sidebar strip showing which analysis
layers are on/off (spec Section 4.2 requirement #2 -- "a visible strip, not a settings
page"). State persistence and undo live here directly rather than in a separate
state.py -- small enough that a dedicated file would just be another place to jump to
without earning it.

Run with:
    streamlit run app/main.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_DIR.parent))  # for `import config`
sys.path.insert(0, str(_APP_DIR))  # for sibling imports -- Streamlit's exec() doesn't put
# the script's own directory on sys.path the way a plain `python file.py` invocation does.
import config  # noqa: E402
import draft_engine as de  # noqa: E402
import sleeper_client  # noqa: E402

STATE_FILE = config.STATE_DIR / "draft_state.json"

st.set_page_config(page_title="2026 Live Draft Tool", layout="wide")


# ---------------------------------------------------------------------------
# Data loading (cached -- player_master.csv doesn't change during a session unless
# build/pipeline.py is rerun, which the sidebar's "Reload data" button accounts for)
# ---------------------------------------------------------------------------
@st.cache_data
def load_master() -> pd.DataFrame:
    return pd.read_csv(config.PLAYER_MASTER_PATH)


@st.cache_data
def load_manager_priors() -> pd.DataFrame:
    return pd.read_csv(config.MANAGER_PRIORS_PATH)


@st.cache_data
def load_team_bias() -> pd.DataFrame:
    return pd.read_csv(config.TEAM_BIAS_PATH)


@st.cache_data
def load_owner_drift() -> pd.DataFrame:
    path = config.DATA_DERIVED / "owner_drift.csv"
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


# ---------------------------------------------------------------------------
# Draft state persistence (spec Section 9: survive a crash at pick 90; undo; every
# recalculation re-runs on each pick -- Streamlit's own rerun-on-interaction model
# gives us the last part for free)
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"picks": [], "sleeper_draft_id": ""}


def save_state(state: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def next_overall_pick(state: dict) -> int:
    return len(state["picks"]) + 1


def manager_for_pick(overall: int) -> str:
    seq = config.full_draft_sequence()
    return seq[overall - 1][2] if 0 < overall <= len(seq) else "?"


def add_pick(state: dict, player_row: pd.Series) -> None:
    overall = next_overall_pick(state)
    state["picks"].append(
        {
            "overall": overall,
            "round": config.round_of_pick(overall),
            "player": player_row["player"],
            "position": player_row["position"],
            "nfl_team": player_row["nfl_team"],
            "manager": manager_for_pick(overall),
        }
    )
    save_state(state)


def undo_last_pick(state: dict) -> None:
    if state["picks"]:
        state["picks"].pop()
        save_state(state)


def drafted_name_keys(state: dict) -> set[str]:
    return {config.normalize_name(p["player"]) for p in state["picks"]}


def roster_counts_by_manager(state: dict) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for p in state["picks"]:
        counts.setdefault(p["manager"], {}).setdefault(p["position"], 0)
        counts[p["manager"]][p["position"]] += 1
    return counts


def owner_roster_state(state: dict) -> de.RosterState:
    owner_picks = [p for p in state["picks"] if p["manager"] == config.OWNER]
    current_overall = next_overall_pick(state)
    return de.RosterState(
        picks=owner_picks,
        current_round=config.round_of_pick(current_overall),
        current_overall_pick=current_overall,
    )


def owner_next_pick_number(state: dict) -> int:
    current = next_overall_pick(state)
    seq = config.full_draft_sequence()
    for overall, _, manager in seq:
        if overall >= current and manager == config.OWNER:
            return overall
    return current


# ---------------------------------------------------------------------------
# Sidebar -- always-visible layer status strip, ADP source, live-polling status,
# undo, and the dispersion/weight overrides.
# ---------------------------------------------------------------------------
def render_sidebar(state: dict) -> tuple[float, dict]:
    st.sidebar.header("Status")
    for name, flags in config.LAYERS.items():
        on = flags["available"] and flags["applies"]
        icon = "✅" if on else "❌"
        detail = "" if on else f" (available={flags['available']}, applies={flags['applies']})"
        st.sidebar.markdown(f"{icon} `{name}`{detail}")

    st.sidebar.divider()
    ref = config.REFERENCE_ADP
    st.sidebar.markdown(f"**Reference ADP:** {ref['source_name']}")
    if ref["is_sleeper"]:
        connected = sleeper_client.check_available()
        st.sidebar.markdown("\U0001f7e2 Sleeper reachable" if connected else "\U0001f534 Sleeper unreachable -- manual entry only")
    else:
        st.sidebar.markdown("⚠️ Live polling unavailable (`is_sleeper=False`) -- manual entry is the only path.")

    current = next_overall_pick(state)
    st.sidebar.markdown(f"**Pick {current}** (Round {config.round_of_pick(current)})")
    st.sidebar.markdown(f"**Nathan's next pick:** {owner_next_pick_number(state)}")

    if st.sidebar.button("Undo last pick", disabled=not state["picks"]):
        undo_last_pick(state)
        st.rerun()

    if st.sidebar.button("Reload data (after rerunning the build)"):
        load_master.clear()
        load_manager_priors.clear()
        load_team_bias.clear()
        load_owner_drift.clear()
        st.rerun()

    with st.sidebar.expander("Live overrides"):
        dispersion = st.slider("Bonus dispersion multiplier", 0.0, 2.0, 1.0, 0.05)
        st.caption("0 must visibly change rankings -- it zeroes the bonus layer outright.")
        weights = {
            "base": st.slider("Weight: base", 0.0, 1.0, config.COMPOSITE_WEIGHTS["base"], 0.05),
            "bonus": st.slider("Weight: bonus", 0.0, 1.0, config.COMPOSITE_WEIGHTS["bonus"], 0.05),
            "factor": st.slider("Weight: factor", 0.0, 1.0, config.COMPOSITE_WEIGHTS["factor"], 0.05),
            "market": st.slider("Weight: market", 0.0, 1.0, config.COMPOSITE_WEIGHTS["market"], 0.05),
        }
    return dispersion, weights


# ---------------------------------------------------------------------------
# Board tab
# ---------------------------------------------------------------------------
def render_board(board: pd.DataFrame, state: dict) -> None:
    st.subheader("Board")
    drafted = drafted_name_keys(state)
    available = board[~board["name_key"].isin(drafted)].copy()

    cols = st.columns(4)
    pos_filter = cols[0].multiselect("Position", config.POSITIONS, default=config.POSITIONS)
    sort_col = cols[1].selectbox("Sort by", ["composite_score", "survival_probability", "ppr_base", "bonus_est_ppr"])
    hide_low_survival = cols[2].checkbox("Hide < 5% survival", value=False)
    n_rows = cols[3].slider("Rows", 10, 200, 50)

    view = available[available["position"].isin(pos_filter)]
    if hide_low_survival:
        view = view[view["survival_probability"] >= 0.05]
    view = view.sort_values(sort_col, ascending=False).head(n_rows)

    display_cols = [
        "player", "position", "nfl_team", "composite_score", "survival_probability",
        "vorp", "ppr_base", "bonus_est_ppr", "factor_score_recomputed", "archetype",
        "reference_adp_rank", "comparison_adp_rank", "adp_rank_divergence",
        "intel_tag", "intel_windows", "intel_note",
    ]
    st.dataframe(
        view[display_cols].style.format(
            {"composite_score": "{:.1f}", "survival_probability": "{:.0%}", "vorp": "{:.1f}",
             "ppr_base": "{:.1f}", "bonus_est_ppr": "{:.1f}"}
        ),
        use_container_width=True,
        height=560,
    )

    with st.expander("Expand a player's layer contributions"):
        pick_name = st.selectbox("Player", view["player"].tolist())
        row = view[view["player"] == pick_name].iloc[0]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(
            "Base (VORP, pts)", f"{row['vorp']:.1f}",
            help=f"ppr_base={row['ppr_base']:.1f} ({row['ppr_base_source']}), replacement_level={row['replacement_level']:.1f}",
        )
        c2.metric("Bonus (pts)", f"{row['bonus_est_ppr']:.1f}", help=f"within-position percentile: {row['bonus_norm']:.0f}")
        c3.metric("Factor (norm)", "n/a" if pd.isna(row["factor_norm"]) else f"{row['factor_norm']:.0f}",
                   help=f"factor_score_recomputed={row.get('factor_score_recomputed')}, archetype={row.get('archetype')}")
        c4.metric("Market (norm)", "n/a" if pd.isna(row["market_norm"]) else f"{row['market_norm']:.0f}",
                   help=f"reference={row.get('reference_adp_rank')}, comparison={row.get('comparison_adp_rank')}")
        if pd.notna(row.get("intel_tag")):
            nudge = row.get("intel_nudge_pts", 0.0)
            st.metric(
                f"Intel: {row['intel_tag']}", f"{nudge:+.1f} pts" if nudge else "filtered from recommendations",
                help=f"pick window(s) {row.get('intel_windows')}, priority {row.get('intel_priority')} -- {row.get('intel_note') or 'no note'}",
            )
        anchor = row.get("availability_anchor")
        if anchor == "comparison":
            st.caption("Survival curve anchored on NFFC (comparison) -- this player has no Sleeper rank.")
        elif anchor == "none":
            st.caption("No ADP from either source for this player -- survival is an uninformed 0.5.")
        if row.get("availability_used_fallback"):
            st.caption("Manager-priors refinement did not apply (layer disabled) -- showing the baseline curve only.")
        if row.get("team_conflict"):
            st.warning("Reference and comparison ADP disagree on this player's NFL team.")


# ---------------------------------------------------------------------------
# My Team / Recommender tab
# ---------------------------------------------------------------------------
SEVERITY_ICON = {"High": "\U0001f534", "Medium": "\U0001f7e1", "Low": "⚪"}


def render_recommender(board: pd.DataFrame, state: dict, owner_drift: pd.DataFrame) -> None:
    st.subheader("My Team / Recommender")
    roster = owner_roster_state(state)
    laporta_available = config.normalize_name("Sam LaPorta") not in drafted_name_keys(state)

    result = de.evaluate_pick(board, roster, laporta_available, owner_drift)

    st.markdown("**Roster vs. target**")
    summary_df = pd.DataFrame(result["roster_summary"]).T
    st.dataframe(summary_df, use_container_width=True)

    st.markdown("**Pick-53 fork**")
    st.info(result["pick53_fork"]["note"])

    band = result["band_7_11"]
    st.markdown("**Rd 7-11 congestion band**")
    st.write(f"Remaining picks in band: {band['remaining_picks_in_band']} -- commitments: {', '.join(band['commitments']) or 'none'}")
    if band["congested"]:
        st.warning("Band is congested -- more commitments than remaining picks.")

    st.markdown("**Active warnings**")
    if not result["warnings"]:
        st.write("None right now.")
    for w in result["warnings"]:
        st.markdown(f"{SEVERITY_ICON.get(w.severity, '')} **{w.code}** ({w.severity}): {w.message}")

    st.markdown("**Top recommendations (need-adjusted)**")
    st.dataframe(
        result["top_recommendations"][["player", "position", "composite_score", "survival_probability", "need_adjusted_score"]],
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# Draft Room tab -- manual entry (always available) + optional Sleeper sync
# ---------------------------------------------------------------------------
def render_draft_room(board: pd.DataFrame, state: dict) -> None:
    st.subheader("Draft Room")
    drafted = drafted_name_keys(state)
    available = board[~board["name_key"].isin(drafted)].sort_values("composite_score", ascending=False)

    current = next_overall_pick(state)
    st.markdown(f"**On the clock:** pick {current} (Round {config.round_of_pick(current)}) -- {manager_for_pick(current)}")

    options = [f"{r.player} ({r.position}, {r.nfl_team})" for r in available.itertuples()]
    choice = st.selectbox("Type to search the available-player list", options) if options else None
    if st.button("Draft this player", disabled=choice is None):
        idx = options.index(choice)
        add_pick(state, available.iloc[idx])
        st.rerun()

    st.divider()
    st.markdown("**Sleeper sync**")
    if not config.REFERENCE_ADP.get("is_sleeper"):
        st.warning("Reference ADP source has is_sleeper=False -- manual entry is the only path this season.")
    else:
        draft_id = st.text_input("Sleeper draft_id", value=state.get("sleeper_draft_id", ""))
        state["sleeper_draft_id"] = draft_id
        if st.button("Sync new picks from Sleeper", disabled=not draft_id):
            known = {p["overall"] for p in state["picks"]}
            try:
                new_picks = sleeper_client.poll_new_picks(draft_id, known)
            except sleeper_client.SleeperUnavailable as exc:
                st.error(f"Sleeper sync failed -- use manual entry. ({exc})")
                new_picks = []
            for p in new_picks:
                key = config.normalize_name(p["player"])
                match = board[board["name_key"] == key]
                row = match.iloc[0] if len(match) else pd.Series({"player": p["player"], "position": p["position"], "nfl_team": p["nfl_team"]})
                add_pick(state, row)
            if new_picks:
                st.rerun()

    st.divider()
    st.markdown("**Pick log**")
    if state["picks"]:
        st.dataframe(pd.DataFrame(state["picks"]).sort_values("overall", ascending=False), use_container_width=True, height=300)
    else:
        st.write("No picks yet.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    st.title("2026 Live Draft Tool")

    if not config.PLAYER_MASTER_PATH.exists():
        st.error("player_master.csv not found -- run `python build/pipeline.py` first.")
        return

    state = load_state()
    dispersion, weights = render_sidebar(state)

    master = load_master()
    manager_priors = load_manager_priors()
    team_bias = load_team_bias()
    owner_drift = load_owner_drift()

    board = de.compute_composite(master, weights=weights, dispersion_multiplier=dispersion)
    board = de.compute_availability(
        board,
        as_of_pick=next_overall_pick(state) - 1,
        target_pick=owner_next_pick_number(state),
        manager_priors=manager_priors,
        team_bias=team_bias,
        drafted_name_keys=drafted_name_keys(state),
        owner_roster_by_manager=roster_counts_by_manager(state),
    )

    if board.attrs.get("disabled_composite_layers"):
        st.info(f"Composite layers currently disabled: {', '.join(board.attrs['disabled_composite_layers'])} -- weights renormalized over the rest.")

    tab_board, tab_team, tab_room = st.tabs(["Board", "My Team / Recommender", "Draft Room"])
    with tab_board:
        render_board(board, state)
    with tab_team:
        render_recommender(board, state, owner_drift)
    with tab_room:
        render_draft_room(board, state)


if __name__ == "__main__":
    main()
