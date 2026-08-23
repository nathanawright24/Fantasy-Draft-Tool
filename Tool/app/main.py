"""
Streamlit entry point. Board with expandable layers, manual pick entry with undo,
optional Sleeper sync, and an always-visible sidebar strip showing which analysis
layers are on/off (spec Section 4.2 requirement #2 -- "a visible strip, not a settings
page"). Draft state persistence and roster-derivation logic live in app/draft_state.py
(work order 2026-08-16 item 0b) -- this file is UI only, so `grep -l streamlit
app/*.py` returns just this file.

Run with:
    streamlit run app/main.py
"""
from __future__ import annotations

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
import draft_state  # noqa: E402
import sleeper_client  # noqa: E402

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
# Active-draft-id tracking (work order 2026-08-16 item 0). st.session_state persists
# across reruns within one running server process; the pointer file underneath it
# (draft_state.save_active_draft_id) persists across a full restart too (spec Section
# 9: survive a crash at pick 90). This is the ONLY thing that decides which draft's
# state file gets loaded -- switching draft_id switches files, so there is nothing to
# "clear": drafts A and B simply cannot cross-contaminate.
# ---------------------------------------------------------------------------
def get_active_draft_id() -> str | None:
    if "active_draft_id" not in st.session_state:
        st.session_state["active_draft_id"] = draft_state.load_active_draft_id()
    return st.session_state["active_draft_id"]


def set_active_draft_id(draft_id: str | None) -> None:
    st.session_state["active_draft_id"] = draft_id
    draft_state.save_active_draft_id(draft_id)


# ---------------------------------------------------------------------------
# Sidebar -- always-visible layer status strip, ADP source, live-polling status,
# undo, and the dispersion/weight overrides.
# ---------------------------------------------------------------------------
def render_sidebar(state: dict, draft_id: str | None) -> tuple[float, dict]:
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
        # Work order item 0: "the owner mocked in standard scoring against a PPR-derived
        # board" -- the tool can't read Sleeper's scoring format, but showing exactly
        # which file was loaded makes a stale/mismatched scrape visible immediately.
        try:
            st.sidebar.caption(f"ADP file: `{config.latest_sleeper_adp_raw_path().name}`")
        except FileNotFoundError:
            st.sidebar.caption("⚠️ No sleeper_adp_ppr_*.csv found in data/raw/")
    else:
        st.sidebar.markdown("⚠️ Live polling unavailable (`is_sleeper=False`) -- manual entry is the only path.")

    current = draft_state.next_overall_pick(state)
    st.sidebar.markdown(f"**Pick {current}** (Round {config.round_of_pick(current)})")
    st.sidebar.markdown(f"**Nathan's next pick:** {draft_state.owner_next_pick_number(state)}")
    st.sidebar.caption(f"Active draft: {draft_id or 'manual entry (no Sleeper draft_id)'}")

    if st.sidebar.button("Undo last pick", disabled=not state["picks"]):
        draft_state.undo_last_pick(state, draft_id)
        st.rerun()

    confirm_reset = st.sidebar.checkbox("Confirm reset (clears every pick logged under this draft)")
    if st.sidebar.button("Reset draft", disabled=not confirm_reset):
        draft_state.reset_draft(draft_id)
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
    drafted = draft_state.drafted_name_keys(state)
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
    roster = draft_state.owner_roster_state(state)
    laporta_available = config.normalize_name("Sam LaPorta") not in draft_state.drafted_name_keys(state)

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
def render_draft_room(board: pd.DataFrame, state: dict, draft_id: str | None) -> None:
    st.subheader("Draft Room")
    drafted = draft_state.drafted_name_keys(state)
    available = board[~board["name_key"].isin(drafted)].sort_values("composite_score", ascending=False)

    current = draft_state.next_overall_pick(state)
    st.markdown(f"**On the clock:** pick {current} (Round {config.round_of_pick(current)}) -- {draft_state.manager_for_pick(current)}")

    options = [f"{r.player} ({r.position}, {r.nfl_team})" for r in available.itertuples()]
    choice = st.selectbox("Type to search the available-player list", options) if options else None
    if st.button("Draft this player", disabled=choice is None):
        idx = options.index(choice)
        draft_state.add_pick(state, draft_id, available.iloc[idx])
        st.rerun()

    st.divider()
    st.markdown("**Sleeper sync**")
    if not config.REFERENCE_ADP.get("is_sleeper"):
        st.warning("Reference ADP source has is_sleeper=False -- manual entry is the only path this season.")
    else:
        # Work order 2026-08-16 item 0: COMPARE the typed value against the currently
        # active draft_id FIRST, and only THEN switch -- the original bug assigned
        # `state["sleeper_draft_id"] = draft_id` unconditionally before any comparison
        # was possible, so a new draft_id's picks got polled against the OLD draft's
        # `known` overall-pick set and silently discarded as "already seen." Switching
        # here means reloading a different per-draft state file (via st.rerun()), not
        # mutating the current one -- so drafts A and B never share mutable state at all.
        typed_draft_id = st.text_input("Sleeper draft_id", value=draft_id or "")
        new_draft_id = typed_draft_id or None
        if new_draft_id != draft_id:
            set_active_draft_id(new_draft_id)
            st.rerun()

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
                draft_state.add_pick(state, draft_id, row)
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

    active_draft_id = get_active_draft_id()
    state = draft_state.load_state(active_draft_id)
    dispersion, weights = render_sidebar(state, active_draft_id)

    master = load_master()
    manager_priors = load_manager_priors()
    team_bias = load_team_bias()
    owner_drift = load_owner_drift()

    board = de.compute_composite(master, weights=weights, dispersion_multiplier=dispersion)
    board = de.compute_availability(
        board,
        as_of_pick=draft_state.next_overall_pick(state) - 1,
        target_pick=draft_state.owner_next_pick_number(state),
        manager_priors=manager_priors,
        team_bias=team_bias,
        drafted_name_keys=draft_state.drafted_name_keys(state),
        owner_roster_by_manager=draft_state.roster_counts_by_manager(state),
    )

    if board.attrs.get("disabled_composite_layers"):
        st.info(f"Composite layers currently disabled: {', '.join(board.attrs['disabled_composite_layers'])} -- weights renormalized over the rest.")

    tab_board, tab_team, tab_room = st.tabs(["Board", "My Team / Recommender", "Draft Room"])
    with tab_board:
        render_board(board, state)
    with tab_team:
        render_recommender(board, state, owner_drift)
    with tab_room:
        render_draft_room(board, state, active_draft_id)


if __name__ == "__main__":
    main()
