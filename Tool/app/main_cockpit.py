"""
main_cockpit.py — Streamlit entry point for the cockpit UI.

Run with:
    streamlit run app/main_cockpit.py

This sits alongside the existing app/main.py rather than replacing it, so the old
board stays available while this one is shaken out. The work order's rule was that
`grep -l streamlit app/*.py` returns only the UI file; it now returns main.py and
main_cockpit.py, and nothing else. board_model.py and cockpit_html.py are both pure.

Requires streamlit 1.37 or newer for st.fragment(run_every=...), which is what does
the automatic polling. On an older version, delete the decorator and the manual Sync
button still works.
"""
from __future__ import annotations

import html
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

_APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_APP_DIR.parent))
sys.path.insert(0, str(_APP_DIR))
import board_model as bm  # noqa: E402
import cockpit_html as ch  # noqa: E402
import config  # noqa: E402
import draft_engine as de  # noqa: E402
import draft_setup  # noqa: E402
import draft_state  # noqa: E402
import sleeper_client  # noqa: E402

st.set_page_config(page_title="2026 Draft Room", layout="wide", initial_sidebar_state="collapsed")

POLL_SECONDS = 6

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@st.cache_data
def load_master() -> pd.DataFrame:
    """player_master.csv with the kicker slide already applied, so every consumer of
    this frame is kicker agnostic and nobody has to remember to call it."""
    raw = pd.read_csv(config.PLAYER_MASTER_PATH)
    return bm.apply_kicker_slide(raw)


@st.cache_data
def load_priors() -> pd.DataFrame:
    return pd.read_csv(config.MANAGER_PRIORS_PATH)

@st.cache_data
def load_team_bias() -> pd.DataFrame:
    return pd.read_csv(config.TEAM_BIAS_PATH)

@st.cache_data
def load_owner_drift() -> pd.DataFrame:
    p = config.DATA_DERIVED / "owner_drift.csv"
    return pd.read_csv(p) if p.exists() else pd.DataFrame()

def sync_from_sleeper(state: dict, draft_id, board: pd.DataFrame) -> int:
    """Pull any picks we have not seen and log them. Returns how many landed.

    Also updates the sync-health state the live indicator reads (follow-up to work
    order 2026-08-24 item 6): `last_sync_at` advances on every successful POLL, not
    just when a pick is actually found -- most 6-second cycles find nothing new, and a
    timestamp that only moved when a pick landed would look "stuck" for most of a
    90-second-per-pick window even while polling is working perfectly. `sync_error`
    holds the most recent failure message, or None once a poll succeeds again.

    Name resolution (work order 2026-08-24b item 1) goes through
    draft_state.resolve_sleeper_name_key -- crosswalk first, config.NAME_ALIASES
    fallback second -- instead of a bare config.normalize_name(...) lookup, which is
    what let Kenneth Walker (and every other Sleeper/master spelling mismatch) stay on
    the board after being drafted: the pick landed under the WRONG key, so
    drafted_name_keys() never matched the board row. If resolution still comes up
    empty, that is now loud rather than a silent bare-Series fallback: logged to
    config.UNMATCHED_SYNC_LOG_PATH and collected in st.session_state["sync_unmatched"]
    for main() to render as an on-screen warning.
    """
    if not draft_id or not config.REFERENCE_ADP.get("is_sleeper"):
        return 0
    known = {p["overall"] for p in state["picks"]}
    try:
        new = sleeper_client.poll_new_picks(draft_id, known)
    except sleeper_client.SleeperUnavailable as exc:
        st.session_state["sync_error"] = str(exc)
        return 0
    st.session_state["sync_error"] = None
    st.session_state["last_sync_at"] = time.time()
    unmatched = st.session_state.setdefault("sync_unmatched", [])
    for p in new:
        key = draft_state.resolve_sleeper_name_key(p["player"])
        match = board[board["name_key"] == key]
        if len(match):
            row = match.iloc[0]
        else:
            row = pd.Series({"player": p["player"], "position": p["position"], "nfl_team": p["nfl_team"]})
            draft_state.log_unmatched_sync_name(draft_id, p["player"], p["position"], p["nfl_team"])
            if p["player"] not in unmatched:
                unmatched.append(p["player"])
        draft_state.add_pick(state, draft_id, row)
    return len(new)

def live_status(draft_id: str | None) -> tuple[str, str]:
    """Which of three states polling is actually in, and the exact sentence to show
    (handoff Section 3.1: the timestamp doubles as a health check, so a static string
    that reads the same whether or not anything is happening is worse than none).
    Returns (state, text) where state is "none" | "error" | "waiting" | "ok"."""
    if not draft_id:
        return "none", "No Sleeper draft_id set -- add one on the setup screen to enable live polling."
    error = st.session_state.get("sync_error")
    if error:
        return "error", f"Sync failing: {error}"
    last = st.session_state.get("last_sync_at")
    if last is None:
        return "waiting", "Watching Sleeper. No successful sync yet -- click Sync now or wait for the next poll."
    return "ok", f"Watching Sleeper. Recomputing on every pick. Last pick read {int(time.time() - last)} seconds ago."

def compute_board(
    master: pd.DataFrame, state: dict, priors: pd.DataFrame, team_bias: pd.DataFrame,
    as_of_pick: int, target_pick: int, wait_pick: int | None,
) -> pd.DataFrame:
    """de.compute_composite + bm.availability_with_band, cached (work order 2026-08-24b
    item 3 / R38) on (frozenset(drafted_name_keys), as_of_pick, target_pick, wait_pick)
    -- measured at ~5.5s per call (2000-sim montecarlo, before this work order's other
    fixes) on the real 433-row board, the dominant cost of a render by a wide margin.
    A rerun where none of those four have changed -- every idle 6-second poll that
    finds no new pick, or any other widget interaction that triggers a full rerun
    without one -- is the common case, and now costs nothing instead of paying for the
    simulation again. Cache lives in st.session_state (board_model.py itself must stay
    UI-framework-free, per this repo's own grep check), and is cleared on every setup
    save (render_setup_screen's submit handler), since roster_target/layers/owner all
    change the result for the SAME four key fields.
    """
    drafted_name_keys = draft_state.drafted_name_keys(state)
    key = (frozenset(drafted_name_keys), as_of_pick, target_pick, wait_pick)
    cache = st.session_state.setdefault("board_cache", {})
    if key in cache:
        return cache[key]
    board = de.compute_composite(master)
    board = bm.availability_with_band(
        board,
        as_of_pick=as_of_pick,
        target_pick=target_pick,
        manager_priors=priors,
        team_bias=team_bias,
        drafted_name_keys=drafted_name_keys,
        owner_roster_by_manager=draft_state.roster_counts_by_manager(state),
        wait_pick=wait_pick,
    )
    if "survival_probability_wait" not in board.columns:
        board["survival_probability_wait"] = board["survival_probability"]
        board["survival_band_wait_pts"] = board["survival_band_pts"]
    # Work order 2026-08-29 item 1 (R37): +999 (not -999) for missing NFFC coverage --
    # the "Market reach" sort is now ASCENDING (best value first, opposite of the old
    # "Sharp edge" sort), so a row with no NFFC data must sort to the BOTTOM as an
    # unknown-quantity worst case, not float to the top the way -999 used to.
    board["_reach_gap"] = board.apply(lambda r: bm.market_reach_gap(r), axis=1)
    board["_reach_gap"] = board["_reach_gap"].fillna(999)
    cache.clear()  # only one live entry is ever useful -- the moment the key changes, the old board is stale anyway
    cache[key] = board
    return board

# ---------------------------------------------------------------------------
# Setup screen (work order 2026-08-24 item 4 / R32) -- set the pick and the
# configuration when loading the tool, before the cockpit renders at all. No pipeline
# rebuild needed for anything here: player_master.csv is slot-independent, only pick
# numbers and availability move, and both are computed at render time.
# ---------------------------------------------------------------------------
def render_setup_screen(current: dict) -> None:
    st.title("Draft setup")
    st.caption(
        "Set once before the draft. Changing the owner slot or draft order updates every "
        "pick number and every availability number immediately -- nothing here requires "
        "re-running the data build."
    )
    factory_names = list(config.DRAFT_ORDER_2026_FACTORY)

    with st.form("draft_setup_form"):
        st.subheader("Draft order")
        st.caption("Twelve slots, snake order. Pick who sits in each.")
        order = []
        for row_start in (0, 6):
            cols = st.columns(6)
            for i, col in enumerate(cols):
                slot = row_start + i
                default = current["draft_order"][slot] if slot < len(current["draft_order"]) else factory_names[slot]
                order.append(col.selectbox(f"Slot {slot + 1}", factory_names,
                                            index=factory_names.index(default) if default in factory_names else slot,
                                            key=f"slot_{slot}"))

        st.subheader("Owner")
        owner = st.selectbox("Which slot is mine", order, index=order.index(current["owner"]) if current["owner"] in order else 0)

        st.subheader("Roster targets")
        rcols = st.columns(4)
        roster_target = {}
        for i, pos in enumerate(config.POSITIONS):
            roster_target[pos] = rcols[i].number_input(pos, min_value=0, max_value=10,
                                                         value=int(current["roster_target"].get(pos, 0)))

        st.subheader("Reference ADP source")
        c1, c2, c3 = st.columns(3)
        reference_source_name = c1.text_input("Source name", value=current["reference_source_name"])
        reference_is_sleeper = c2.checkbox("This is Sleeper (enables live draft-room polling)",
                                            value=current["reference_is_sleeper"])
        sleeper_draft_id = c3.text_input(
            "Sleeper draft_id",
            value=draft_setup.sleeper_draft_id(current) or "",
            help="Paste this once the real draft room exists. Editable mid-draft -- "
                 "switching it swaps to that draft_id's own state file (work order item "
                 "0's per-draft-id keying), it never overwrites another draft's picks.",
        )

        st.subheader("Analysis layers")
        st.caption("Turning a layer off is visible everywhere the board shows it -- see the rail footer.")
        layers = {}
        lcols = st.columns(3)
        for i, name in enumerate(config.LAYERS):
            layers[name] = lcols[i % 3].checkbox(name.replace("_", " "), value=current["layers"].get(name, True))

        st.subheader("Tight end fork player")
        te1_fork_player = st.text_input(
            "The named player the pick-53-style TE fork is built around",
            value=current.get("te1_fork_player", draft_setup.DEFAULT_TE1_FORK_PLAYER),
        )

        submitted = st.form_submit_button("Save and continue", use_container_width=True)

    if submitted:
        error = draft_setup.validate_draft_order(order, factory_names)
        if error:
            st.error(error)
            return
        new_setup = {
            "draft_order": order, "owner": owner, "roster_target": roster_target,
            "reference_source_name": reference_source_name, "reference_is_sleeper": reference_is_sleeper,
            "layers": layers, "te1_fork_player": te1_fork_player,
            "sleeper_draft_id": sleeper_draft_id.strip() or None,
        }
        if new_setup["sleeper_draft_id"] != draft_setup.sleeper_draft_id(current):
            # A different draft_id means a different state file (item 0's per-draft-id
            # keying) -- any sync error/timestamp on screen belonged to the OLD one and
            # would otherwise read as stale, misleading status for the new draft_id.
            st.session_state.pop("sync_error", None)
            st.session_state.pop("last_sync_at", None)
        # roster_target/layers/owner all change compute_board's result for the SAME
        # (drafted_name_keys, as_of_pick, target_pick, wait_pick) cache key (work order
        # 2026-08-24b item 3 / R38) -- any of them changing must invalidate it, not just
        # a draft_id swap.
        st.session_state.pop("board_cache", None)
        draft_setup.save_setup(new_setup)
        st.rerun()

# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
def main() -> None:
    if not config.PLAYER_MASTER_PATH.exists():
        st.error("player_master.csv not found. Run the build first.")
        return

    theme = ch.THEME_DARK if st.session_state.get("theme", "dark") == "dark" else ch.THEME_LIGHT
    st.markdown(ch.page_css(theme), unsafe_allow_html=True)

    if not draft_setup.setup_exists():
        render_setup_screen(draft_setup.default_setup())
        return
    setup = draft_setup.load_setup()
    draft_setup.apply_setup(setup)  # re-applied every boot -- a fresh process re-imports
                                     # config with factory defaults, so this isn't a no-op.

    # draft_setup.json is the only place a draft_id is persisted -- read fresh every
    # run, so editing it on the (re-openable) setup screen takes effect on the very
    # next rerun with no separate session_state/pointer-file bookkeeping required.
    draft_id = draft_setup.sleeper_draft_id(setup)
    state = draft_state.load_state(draft_id)
    master, priors, team_bias, drift = load_master(), load_priors(), load_team_bias(), load_owner_drift()

    on_clock = draft_state.next_overall_pick(state)
    owner_next = draft_state.owner_next_pick_number(state)
    owner_on_clock = owner_next == on_clock
    picks = bm.owner_pick_numbers()
    turn_after = next((p for p in picks if p > owner_next), owner_next)
    # When you are on the clock, the number that matters is whether he lasts to your
    # NEXT turn. When you are waiting, it is whether he lasts to this one.
    survival_target = turn_after if owner_on_clock else owner_next
    wait_reference = turn_after
    window_as_of = owner_next if owner_on_clock else on_clock - 1
    intervening_this_window = [
        (o, r, m) for o, r, m in config.full_draft_sequence()
        if window_as_of < o < survival_target and m != config.OWNER
    ]

    # wait_pick asks the SAME simulation run for a second, further checkpoint (work
    # order 2026-08-24 item 2 / R30) -- when on the clock, survival_target already IS
    # wait_reference, so there is nothing further to ask for. compute_board caches this
    # whole call (work order 2026-08-24b item 3 / R38) on the pick numbers and drafted
    # set, so a rerun with no new picks skips the montecarlo simulation entirely.
    board = compute_board(
        master, state, priors, team_bias,
        as_of_pick=owner_next if owner_on_clock else on_clock - 1,
        target_pick=survival_target,
        wait_pick=wait_reference if wait_reference != survival_target else None,
    )
    available = board[~board["name_key"].isin(draft_state.drafted_name_keys(state))]

    roster = draft_state.owner_roster_state(state)
    roster_counts = {pos: roster.count(pos) for pos in config.POSITIONS}
    fork_player_name = draft_setup.te1_fork_player(setup)
    result = de.evaluate_pick(
        board, roster,
        bool(fork_player_name) and config.normalize_name(fork_player_name) not in draft_state.drafted_name_keys(state),
        drift,
        drafted_name_keys=draft_state.drafted_name_keys(state),
        fork_player_name=fork_player_name,
    )

    # ---------- chrome ----------
    owner_slot = config.DRAFT_ORDER_2026.index(config.OWNER) + 1 if config.OWNER in config.DRAFT_ORDER_2026 else "?"
    head = st.columns([3, 2, 3, 1, 1])
    head[0].markdown(
        f"<div style='font-size:16px;font-weight:500'>2026 Draft Room</div>"
        f"<div style='font-size:12px;color:{theme['faint']}'>"
        f"slot {owner_slot} of {config.N_TEAMS}, full point per reception</div>",
        unsafe_allow_html=True,
    )
    head[1].markdown("", unsafe_allow_html=True)
    # Three states, not one static string (follow-up to work order 2026-08-24 item 6):
    # no draft_id entered, watching and healthy, or failing. handoff Section 3.1 calls
    # the timestamp a health check; a health check that reads green while doing
    # nothing is worse than none.
    live_state, live_text = live_status(draft_id)
    live_color = {"none": theme["faint"], "error": theme["bad"], "waiting": theme["warn"], "ok": theme["good"]}[live_state]
    head[2].markdown(
        f"<div class='nk-kicker'>Live</div>"
        f"<div style='display:flex;align-items:baseline;gap:6px'>"
        f"<span style='width:7px;height:7px;border-radius:50%;background:{live_color};flex:0 0 auto'></span>"
        f"<span style='font-size:12px;color:{theme['muted']}'>{html.escape(live_text)}</span></div>",
        unsafe_allow_html=True,
    )
    if head[3].button("Setup", use_container_width=True):
        st.session_state["show_setup"] = True
        st.rerun()
    if head[4].button("Sync now", use_container_width=True):
        landed = sync_from_sleeper(state, draft_id, board)
        st.toast(f"{landed} new pick{'s' if landed != 1 else ''}" if landed else "Nothing new")
        st.rerun()

    unmatched_names = st.session_state.get("sync_unmatched")
    if unmatched_names:
        # Work order 2026-08-24b item 1 task 2: a Sleeper pick that didn't resolve to
        # any player_master row used to disappear into a bare-Series fallback -- this
        # is the "on screen" half of making that loud (log_unmatched_sync_name in
        # sync_from_sleeper is the "in a log" half). Names stay listed until the
        # session ends; they name real join gaps, not something to auto-dismiss.
        st.warning(
            "Sleeper sent a pick name that didn't match any player on the board -- logged to "
            f"{config.UNMATCHED_SYNC_LOG_PATH.name}, roster counts for that pick may be off: "
            + ", ".join(unmatched_names)
        )

    if st.session_state.get("show_setup"):
        if st.button("Close setup"):
            st.session_state["show_setup"] = False
            st.rerun()
        render_setup_screen(setup)
        return

    # Automatic polling. The recompute is the rerun, so it happens on every pick.
    @st.fragment(run_every=POLL_SECONDS)
    def poller():
        if sync_from_sleeper(state, draft_id, board):
            st.rerun()

    poller()

    tab_cockpit, tab_board = st.tabs(["Cockpit", "Full board"])

    # ================= COCKPIT =================
    with tab_cockpit:
        # Band 1, context strip (handoff Section 3.2) -- on the clock, the owner's next
        # turn, the intervening managers' tells, and what's firing/arming next round.
        st.markdown(
            ch.render_context_strip(
                clock_label="You are on the clock" if owner_on_clock else "On the clock",
                on_clock_pick=on_clock,
                clock_sub=f"round {config.round_of_pick(on_clock)}, {draft_state.manager_for_pick(on_clock)}",
                next_label="Then you wait until" if owner_on_clock else "You are up next at",
                next_pick=survival_target,
                next_sub=f"{abs(survival_target - on_clock)} picks away",
                intervening=bm.intervening_chips(intervening_this_window, priors),
                warn_chips=bm.warn_chips(result["warnings"], config.round_of_pick(on_clock)),
                theme=theme,
                coverage=bm.shortlist_coverage(board, survival_target),
            ),
            unsafe_allow_html=True,
        )
        st.markdown(
            f"<div style='font-size:16px;font-weight:500;margin:8px 0 2px'>"
            f"Three ways to {'start' if on_clock < 20 else 'finish'}</div>"
            f"<div style='font-size:12px;color:{theme['muted']};margin-bottom:8px'>"
            f"Anchored on your own names for pick {owner_next}, in the priority order you wrote, "
            f"topped up with whoever the sharp market takes earliest. Anyone still there at "
            f"{wait_reference} is left out, because this pick would be wasted on him.</div>",
            unsafe_allow_html=True,
        )
        routes = bm.build_routes(available, roster_counts, owner_next, owner_on_clock)
        cols = st.columns(3)
        for i, r in enumerate(routes[:3]):
            cols[i].markdown(
                ch.render_route_card(r, i, owner_next, wait_reference, owner_on_clock, theme),
                unsafe_allow_html=True,
            )

        left, right = st.columns([2.4, 1])
        with left:
            sort_label = st.radio(
                "Sort the board",
                list(ch.SORT_OPTIONS.keys()),
                horizontal=True,
                label_visibility="collapsed",
                key="cockpit_sort",
            )
            col, ascending = ch.SORT_OPTIONS[sort_label]
            view = available.sort_values(col, ascending=ascending, na_position="last")
            # Pick lines only mean anything while the list is in Sleeper order, because
            # that is when a row's position stands in for a pick number.
            lines = ch.SORT_OPTIONS[sort_label][0] == "reference_adp_rank" and bm.pick_line_offsets(
                on_clock, survival_target) or {}
            st.markdown(
                ch.render_board(view, survival_target, wait_reference, owner_on_clock, lines, theme),
                unsafe_allow_html=True,
            )
        with right:
            st.markdown("<div class='nk-kicker'>Your roster against the plan</div>", unsafe_allow_html=True)
            rcols = st.columns(4)
            for i, pos in enumerate(["QB", "RB", "WR", "TE"]):
                rcols[i].markdown(
                    f"<div style='font-size:11px;font-weight:600;"
                    f"color:{bm.POSITION_COLORS[pos]}'>{pos}</div>"
                    f"<div style='font-size:17px'>{roster_counts.get(pos, 0)}"
                    f"<span style='color:{theme['faint']};font-size:12px'>"
                    f"/{config.ROSTER_TARGET[pos]}</span></div>",
                    unsafe_allow_html=True,
                )
            high = [w for w in result["warnings"] if w.severity == "High"]
            lower = [w for w in result["warnings"] if w.severity != "High"]
            if high:
                st.markdown(
                    f"<div class='nk-card' style='border-left:2px solid {theme['bad']}'>"
                    f"<div style='font-weight:500'>Read this one</div>"
                    f"<div style='font-size:12px;margin-top:4px'>{ch.plain(high[0].message)}</div>"
                    f"<div style='font-size:10px;color:{theme['faint']};margin-top:6px'>"
                    f"{high[0].code}, plus {len(lower)} lower priority</div></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div class='nk-card' style='border-left:2px solid {theme['good']}'>"
                    f"<div style='font-weight:500'>Nothing firing</div>"
                    f"<div style='font-size:12px;color:{theme['muted']};margin-top:4px'>"
                    f"{len(lower)} rules are armed but quiet.</div></div>",
                    unsafe_allow_html=True,
                )

            st.markdown(f"<div class='nk-kicker' style='margin-top:10px'>"
                        f"Still here at {wait_reference}</div>", unsafe_allow_html=True)
            waiters = available.copy()
            waiters["_wait"] = waiters["survival_probability_wait"]
            waiters = waiters[waiters["_wait"] >= bm.WAIT_THRESHOLD].head(6)
            for _, w in waiters.iterrows():
                st.markdown(
                    f"<div style='display:flex;gap:8px;font-size:12px;padding:2px 0'>"
                    f"<span style='color:{bm.POSITION_COLORS.get(w['position'])};font-weight:600'>"
                    f"{w['position']}</span><span style='flex:1'>{w['player']}</span>"
                    f"<span style='color:{theme['muted']}'>{w['_wait']*100:.0f}%</span></div>",
                    unsafe_allow_html=True,
                )

            # Layers indicator (work order 2026-08-24 item 5 / R33; spec 4.2 requirement
            # 2: "the UI states which layers are off, at all times"). config.LAYERS
            # stays the only definition -- this just reads it, live, every render.
            off = draft_setup.layers_off(setup)
            layers_line = "All analysis layers on." if not off else (
                f"{len(off)} layer{'s' if len(off) != 1 else ''} off: {', '.join(n.replace('_', ' ') for n in off)}."
            )
            st.markdown(
                f"<div style='font-size:11px;color:{theme['faint'] if not off else theme['warn']};"
                f"margin-top:10px;padding-top:6px;border-top:1px solid {theme['line_soft']}'>{layers_line}</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='nk-kicker' style='margin-top:10px'>"
                    "Every rule, and the round it starts watching</div>", unsafe_allow_html=True)
        active = {w.code for w in result["warnings"]}
        st.markdown(
            ch.render_rule_timeline(bm.rule_timeline(config.round_of_pick(survival_target), active), theme),
            unsafe_allow_html=True,
        )

    # ================= FULL BOARD =================
    with tab_board:
        # st.dataframe gives native click to sort on every column, which is the cheapest
        # honest way to satisfy "make it sortable" without hand rolling a sort control
        # for thirteen columns.
        f1, f2, f3 = st.columns([2, 3, 2])
        query = f1.text_input("Search", placeholder="Type a name", label_visibility="collapsed")
        positions = f2.multiselect("Position", config.POSITIONS, default=config.POSITIONS,
                                   label_visibility="collapsed")
        hide_gone = f3.toggle("Hide the gone", value=False)

        full = available[available["position"].isin(positions)].copy()
        if query:
            full = full[full["player"].str.contains(query, case=False, na=False)]
        if hide_gone:
            full = full[full["survival_probability"] >= bm.GONE_THRESHOLD]
        # Work order 2026-08-29 item 1 (R37): renamed from "Sharp" -- same reasoning as
        # the cockpit board's own column rename in cockpit_html.py.
        full["Reach"] = full.apply(lambda r: bm.market_reach_gap(r), axis=1)
        full["Timing"] = full.apply(
            lambda r: bm.timing(r["survival_probability_wait"], wait_reference, r["survival_probability"]).word,
            axis=1,
        )
        full["Note"] = full["intel_note"].apply(ch.plain)
        show = full.rename(columns={
            "reference_adp_rank": "Sleeper", "player": "Player", "position": "Pos",
            "nfl_team": "Team", "composite_score": "Value", "vorp": "Over repl",
            "survival_probability": f"At {survival_target}", "bonus_est_ppr": "Bonus",
            "factor_score_recomputed": "Factors", "p_got_injured": "Injury",
            "archetype": "Archetype",
        })[[
            "Sleeper", "Player", "Pos", "Team", "Reach", "Value", "Over repl",
            f"At {survival_target}", "Bonus", "Factors", "Injury", "Timing", "Archetype", "Note",
        ]]
        st.caption(f"Showing {len(show)} of {len(available)}. Click any column heading to sort.")
        st.dataframe(
            show,
            use_container_width=True,
            height=760,
            hide_index=True,
            column_config={
                "Sleeper": st.column_config.NumberColumn(format="%d", help="Kickers moved to round 14"),
                "Value": st.column_config.NumberColumn(format="%.1f"),
                "Over repl": st.column_config.NumberColumn(format="%+.0f"),
                f"At {survival_target}": st.column_config.ProgressColumn(
                    format="%.0f%%", min_value=0, max_value=1),
                "Reach": st.column_config.NumberColumn(
                    format="%+.0f", help="Position-adjusted picks ahead of NFFC ADP. "
                    "Positive means a bigger reach than typical for his position."),
                "Bonus": st.column_config.NumberColumn(format="%+.1f"),
                "Injury": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )

if __name__ == "__main__":
    main()
