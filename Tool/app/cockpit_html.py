"""
cockpit_html.py — the board table, rendered as HTML.

The UI framework's own st.dataframe cannot draw the 'your pick' line between two rows,
and that line is the single most useful piece of chrome on the Sleeper board, so the
cockpit table is hand rendered and handed to st.markdown(unsafe_allow_html=True).
The Full board tab uses st.dataframe instead, which buys native click to sort.

No UI-framework import here either. These functions return strings.

Drop this in Tool/app/.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import board_model as bm  # noqa: E402

# ---------------------------------------------------------------------------
# Theme. Values come from the Nocturne token sheet plus Sleeper's position hues.
# ---------------------------------------------------------------------------
THEME_DARK = {
    "bg": "#161826", "surface": "#232532", "surface2": "#1c1e2b", "text": "#e9e9ed",
    "muted": "#9397ab", "faint": "#75798c", "line": "rgba(233,233,237,0.16)",
    "line_soft": "rgba(233,233,237,0.08)", "accent": "#9184d9",
    "accent_soft": "rgba(145,132,217,0.14)",
    "good": "#4fbf8b", "warn": "#d9a445", "bad": "#e2604f",
}
THEME_LIGHT = {
    "bg": "#e4e7f5", "surface": "#f3f5fe", "surface2": "#eceff9", "text": "#292b31",
    "muted": "#595d6c", "faint": "#75798c", "line": "rgba(41,43,49,0.16)",
    "line_soft": "rgba(41,43,49,0.08)", "accent": "#5d5294",
    "accent_soft": "rgba(93,82,148,0.10)",
    "good": "#2f7a58", "warn": "#8a6516", "bad": "#9c3a2c",
}

COCKPIT_COLUMNS = [
    ("sleeper", "Sleeper", "right", 52),
    ("player", "Player", "left", None),
    # Work order 2026-08-29 item 1 (R37): renamed from "Sharp" -- the old column was a
    # raw, un-adjusted comparison-vs-reference RANK divergence; this one is
    # `market_reach_gap`, a position-adjusted VALUE-vs-rank comparison with the
    # opposite sign convention (positive now means a BIGGER reach, not a better one).
    # Leaving the old label would make the two numbers indistinguishable in a
    # screenshot despite meaning something different.
    ("reach", "Reach", "right", 60),
    ("value", "Value", "right", 66),
    # Work order 2026-08-24b item 5 (R39): merged with the old separate "Timing"
    # column. The chip is now the primary reading and the percentage a small
    # secondary annotation -- see the cell-building code below for why.
    ("avail", "At {target}", "right", 96),
    # Work order 2026-08-29b item 3 (R43): `edge` (24b items 2+4's drop-off-adjusted
    # vorp -- what candidates_for_pick and every route actually sort on) had no
    # on-screen home at all before this; `vorp` was already a first-class column here,
    # so the fix is adding edge NEXT TO it, not promoting edge at vorp's expense.
    # "edge is a two-pick optimizer" (item 3's own framing): a 31-vorp TE at +14.7 edge
    # is ranked above a 50-vorp WR at -6.6 because the TE probably won't survive and
    # the WR probably will -- locally correct, and only legible with both numbers
    # showing. Display only: no change to the edge formula, the fallback, or n_sims.
    ("edge", "Edge", "right", 64),
    ("vorp", "Over repl", "right", 68),
    ("factors", "Factors", "right", 62),
    ("note", "Your note", "left", None),
]

SORT_OPTIONS = {
    "Sleeper order": ("reference_adp_rank", True),
    "Board value": ("composite_score", False),
    # Ascending, not descending like the rest: positive market_reach_gap means a
    # BIGGER reach (worse), so the best values sort first at the smallest (most
    # negative) number -- the opposite convention from the old "Sharp edge" sort,
    # which is exactly why the label changed too.
    "Market reach": ("_reach_gap", True),
    "Odds at my pick": ("survival_probability", False),
    "Edge": ("edge", False),
    "Over replacement": ("vorp", False),
    "Factor score": ("factor_score_recomputed", False),
}


def page_css(theme: dict) -> str:
    """One style block, injected once. Streamlit's own chrome is toned down so the
    board reads as an application rather than a report."""
    t = theme
    return f"""
<style>
  .stApp {{ background:
      radial-gradient(1200px 760px at 86% -16%, {t['accent_soft']}, transparent 66%),
      repeating-linear-gradient(60deg, rgba(233,233,237,0.03) 0 1px, transparent 1px 38px),
      repeating-linear-gradient(-60deg, rgba(233,233,237,0.03) 0 1px, transparent 1px 38px),
      {t['bg']};
      color: {t['text']};
      font-family: Inter, system-ui, sans-serif; }}
  header[data-testid="stHeader"] {{ background: transparent; }}
  .block-container {{ padding: 1rem 1.5rem 2rem; max-width: 100%; }}
  .nk-card {{ background:{t['surface']}; border:1px solid {t['line']};
      border-radius:8px; padding:12px 16px; }}
  .nk-kicker {{ font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:{t['faint']}; }}
  .nk-num {{ font-variant-numeric: tabular-nums; }}
  .nk-table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  .nk-table thead th {{ position:sticky; top:0; z-index:2; background:{t['surface2']};
      font-size:10px; letter-spacing:.06em; text-transform:uppercase; color:{t['faint']};
      font-weight:500; padding:6px 8px; border-bottom:1px solid {t['line']}; white-space:nowrap; }}
  .nk-table tbody td {{ padding:0 8px; height:32px; border-bottom:1px solid {t['line_soft']};
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
  .nk-scroll {{ max-height:520px; overflow-y:auto; border:1px solid {t['line']};
      border-radius:8px; background:{t['surface']}; }}
  .nk-pickline td {{ background:{t['accent_soft']}; border-top:1px solid {t['accent']};
      border-bottom:1px solid {t['accent']}; height:26px; font-size:11px; font-weight:600;
      letter-spacing:.08em; text-transform:uppercase; color:{t['accent']}; }}
  .nk-chip {{ display:inline-flex; align-items:center; height:18px; padding:0 6px;
      border-radius:4px; font-size:10px; font-weight:600; letter-spacing:.04em; }}
  .nk-bar {{ display:inline-block; width:3px; height:18px; border-radius:2px; vertical-align:middle; }}
  .nk-strip {{ display:flex; align-items:stretch; gap:1px; background:{t['line_soft']};
      border:1px solid {t['line']}; border-radius:8px; overflow:hidden; }}
  .nk-strip-cell {{ background:{t['surface']}; padding:10px 18px; display:flex;
      flex-direction:column; gap:1px; }}
  div[data-testid="stMetricValue"] {{ font-size:22px; }}
</style>
"""


def _heat(value, low, high, theme) -> str:
    if value is None or pd.isna(value):
        return theme["faint"]
    if value >= high:
        return theme["good"]
    if value <= low:
        return theme["bad"]
    return theme["text"]


def _timing_chip(t: bm.Timing, theme: dict) -> str:
    colors = {
        "NOW": (theme["good"], "rgba(79,191,139,.16)"),
        "CLOSE": (theme["text"], theme["surface2"]),
        "WAIT": (theme["warn"], "rgba(217,164,69,.16)"),
        "GONE": (theme["bad"], "rgba(226,96,79,.16)"),
    }
    fg, bg = colors[t.word]
    return f'<span class="nk-chip" style="color:{fg};background:{bg}" title="{html.escape(t.sentence)}">{t.word}</span>'


def plain(text) -> str:
    """The owner asked for no dashes anywhere in the interface. The source notes carry
    them, so they are converted here rather than edited in the read only 2026 folder."""
    if text is None or pd.isna(text):
        return ""
    s = str(text).replace("\u2014", ",").replace("\u2013", ",")
    s = s.replace(" - ", ", ").replace("\u26a0", "").replace("\ufe0f", "")
    while ", ," in s:
        s = s.replace(", ,", ",")
    return " ".join(s.split()).strip()


def render_board(
    view: pd.DataFrame,
    target_pick: int,
    wait_reference: int,
    on_clock: bool,
    pick_lines: dict[int, str],
    theme: dict,
    rows: int = bm.BOARD_ROWS,
) -> str:
    """The cockpit board. Sleeper order by default, pick lines drawn between rows,
    scrollable to three rounds of players."""
    t = theme
    head = "".join(
        f'<th style="text-align:{align}'
        + (f';width:{width}px' if width else '')
        + f'">{label.format(target=target_pick)}</th>'
        for _, label, align, width in COCKPIT_COLUMNS
    )
    body = []
    for i, (_, r) in enumerate(view.head(rows).iterrows()):
        if i in pick_lines:
            body.append(
                f'<tr class="nk-pickline"><td colspan="{len(COCKPIT_COLUMNS)}">'
                f'{html.escape(pick_lines[i])}'
                f'<span style="color:{t["muted"]};font-weight:400;text-transform:none;'
                f'letter-spacing:0"> &nbsp; everything above here is likely gone</span>'
                f'</td></tr>'
            )
        pos = r["position"]
        pos_color = bm.POSITION_COLORS.get(pos, t["muted"])
        avail = float(r["survival_probability"])
        # Same simulation run as `avail`, not a separate lognormal estimate (work order
        # 2026-08-24 item 2 / R30) -- falls back to the lognormal helper only if the
        # caller never asked compute_availability for a wait_pick checkpoint at all.
        wait = float(r["survival_probability_wait"]) if "survival_probability_wait" in r.index else (
            avail if on_clock else bm.survival_between(r, target_pick, wait_reference)
        )
        tm = bm.timing(wait, wait_reference, 1.0 if on_clock else avail)
        gone = (not on_clock) and avail < bm.GONE_THRESHOLD
        name_color = t["faint"] if gone else t["text"]
        # Work order 2026-08-29 item 1 (R37): position-adjusted, opposite sign
        # convention from the old sharp_edge -- positive now means a BIGGER reach
        # (worse), so the heat map below is flipped (negated) relative to the old cell.
        reach_gap = bm.market_reach_gap(r)
        # 24b items 2+4's drop-off-adjusted vorp (candidates_for_pick's actual sort
        # key) -- NOT the same number as `reach_gap` above. Falls back to raw vorp if
        # the caller's board never asked compute_availability for want_edge=True
        # (matches draft_engine's own fallback).
        player_edge = float(r["edge"]) if "edge" in r.index and pd.notna(r.get("edge")) else float(r.get("vorp") or 0.0)
        note = plain(r.get("intel_note")) or (
            f"Your list, pick {r.get('intel_windows')}" if r.get("intel_tag") == "target"
            else ("You faded him" if r.get("intel_tag") == "fade" else "")
        )
        note_color = t["bad"] if r.get("intel_tag") == "fade" else (
            t["accent"] if r.get("intel_tag") == "target" else t["faint"])
        cells = [
            f'<td style="text-align:right;color:{t["muted"] if not gone else t["faint"]};'
            f'font-weight:500" class="nk-num">{"" if pd.isna(r["reference_adp_rank"]) else int(r["reference_adp_rank"])}</td>',
            f'<td><span class="nk-bar" style="background:{t["line"] if gone else pos_color}"></span>'
            f'&nbsp;<span style="font-weight:500;color:{name_color}">{html.escape(str(r["player"]))}</span>'
            f' <span style="font-size:11px;font-weight:600;color:{pos_color}">{pos}</span>'
            f' <span style="font-size:11px;color:{t["faint"]}">{html.escape(str(r["nfl_team"]))}</span></td>',
            # Negated in the _heat call: positive market_reach_gap is a BIGGER reach
            # (worse), so it must map to the "bad" color, opposite of the old
            # sharp_edge cell's polarity.
            f'<td style="text-align:right;color:{_heat(-reach_gap if reach_gap is not None else None, -8, 8, t)}" class="nk-num">'
            f'{"" if reach_gap is None else ("+" if reach_gap > 0 else "") + str(int(reach_gap))}</td>',
            f'<td style="text-align:right;font-weight:600;color:{_heat(r["composite_score"], 15, 45, t)}" '
            f'class="nk-num">{r["composite_score"]:.1f}</td>',
            # Work order 2026-08-24b item 5 (R39): the chip is the primary reading here,
            # the percentage a small secondary annotation -- decision-band Brier says
            # NOW/CLOSE/WAIT/GONE is roughly the resolution this model has earned, and a
            # bare "45%" invited reading precision into it the model doesn't have. The
            # +-1-point simulation-convergence band that used to sit next to the
            # percentage is dropped entirely, not just de-emphasized: it measured
            # whether the simulation converged, not whether the model is right, and
            # printing it next to the real decision-band error claimed a precision that
            # does not exist.
            f'<td style="text-align:right" class="nk-num">{_timing_chip(tm, t)}'
            f'<span style="color:{t["faint"]};font-size:10px;margin-left:5px">{avail*100:.0f}%</span></td>',
            # Work order 2026-08-29b item 3 (R43): edge beside vorp, both first-class --
            # "+14.7" alone reads as a ranking; "+14.7" next to "31.4" reads as the
            # tradeoff it actually is (a 31-vorp player edge-ranked above a 50-vorp one
            # because the bigger player probably survives to the next turn anyway).
            f'<td style="text-align:right;color:{_heat(player_edge, -10, 10, t)};font-weight:600" class="nk-num">'
            f'{("+" if player_edge >= 0 else "") + f"{player_edge:.1f}"}</td>',
            f'<td style="text-align:right;color:{_heat(r.get("vorp"), 0, 30, t)}" class="nk-num">'
            f'{"" if pd.isna(r.get("vorp")) else ("+" if r["vorp"] >= 0 else "") + f"{r['vorp']:.0f}"}</td>',
            f'<td style="text-align:right;color:{_heat(r.get("factor_score_recomputed"), 0, 20, t)}" '
            f'class="nk-num">{"none" if pd.isna(r.get("factor_score_recomputed")) else int(r["factor_score_recomputed"])}</td>',
            f'<td style="color:{note_color};font-size:12px">{html.escape(note)}</td>',
        ]
        body.append("<tr>" + "".join(cells) + "</tr>")
    return (
        f'<div class="nk-scroll"><table class="nk-table"><thead><tr>{head}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )


def render_context_strip(
    clock_label: str, on_clock_pick: int, clock_sub: str,
    next_label: str, next_pick: int, next_sub: str,
    intervening: list[dict], warn_chips: list[dict], theme: dict,
    coverage: float | None = None,
) -> str:
    """Handoff Section 3.2 band 1 -- four hairline-divided cells: on the clock, the
    owner's next turn, the intervening managers' tells, and what's firing/arming.
    Replaces the ad hoc pair of markdown blocks the first cut used for cells 1-2 only;
    cells 3-4 (manager tells, warning chips) didn't exist before work order 2026-08-24
    item 6 / R34.

    `coverage` (work order 2026-08-29 item 2 / R38): P(at least one shortlist name for
    this window survives to `next_pick`) -- board_model.shortlist_coverage's own
    return value, rendered here PURELY as a display annotation on the "next pick" cell.
    Deliberately not styled as a decision signal (no chip, no color-coding by
    threshold): coverage answers "how urgent is this position," never "who should I
    take" -- it must never look like something to sort or filter on. `None` (no
    shortlist tagged for this window at all) renders nothing, not a "0%" that would
    misread as "definitely gone."
    """
    t = theme
    coverage_line = (
        f'<div style="font-size:11px;color:{t["faint"]};margin-top:2px">'
        f'Shortlist coverage {coverage * 100:.0f}%</div>'
        if coverage is not None else ""
    )
    chip_colors = {"bad": (t["bad"], "rgba(226,96,79,.16)"), "warn": (t["warn"], "rgba(217,164,69,.16)"),
                   "good": (t["good"], "rgba(79,191,139,.16)")}
    tell_chips = "".join(
        f'<span class="nk-chip" style="background:{t["surface2"]};border:1px solid {t["line_soft"]};'
        f'color:{t["muted"]};font-weight:400;height:21px">{html.escape(m["name"])}'
        f'<span style="color:{t["accent"] if m["tell"] != "again" else t["faint"]};margin-left:4px">'
        f'{html.escape(m["tell"])}</span></span>'
        for m in intervening
    ) or f'<span style="font-size:12px;color:{t["faint"]}">none -- you are on the clock</span>'
    warn_html = "".join(
        f'<span class="nk-chip" style="background:{chip_colors[w["kind"]][1]};'
        f'color:{chip_colors[w["kind"]][0]};height:26px;padding:0 10px">{html.escape(w["label"])}</span>'
        for w in warn_chips
    )
    return f"""
<div class="nk-strip">
  <div class="nk-strip-cell" style="min-width:200px">
    <div class="nk-kicker">{html.escape(clock_label)}</div>
    <div style="display:flex;align-items:baseline;gap:8px">
      <span style="font-size:22px;font-weight:500" class="nk-num">Pick {on_clock_pick}</span>
      <span style="font-size:13px;color:{t['muted']}">{html.escape(clock_sub)}</span>
    </div>
  </div>
  <div class="nk-strip-cell" style="min-width:220px">
    <div class="nk-kicker">{html.escape(next_label)}</div>
    <div style="display:flex;align-items:baseline;gap:8px">
      <span style="font-size:22px;font-weight:500;color:{t['accent']}" class="nk-num">{next_pick}</span>
      <span style="font-size:13px;color:{t['muted']}">{html.escape(next_sub)}</span>
    </div>
    {coverage_line}
  </div>
  <div class="nk-strip-cell" style="flex:1;min-width:0">
    <div class="nk-kicker">Who's in between</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap">{tell_chips}</div>
  </div>
  <div class="nk-strip-cell" style="flex-direction:row;align-items:center;gap:8px">{warn_html}</div>
</div>
"""


def render_route_card(route: dict, index: int, this_pick: int, wait_reference: int, on_clock: bool, theme: dict) -> str:
    t = theme
    a = route["anchor"]
    pos = a["position"]
    color = bm.POSITION_COLORS.get(pos, t["accent"])
    avail = float(a.get("survival_probability") or 1.0)
    wait = float(a["survival_probability_wait"]) if "survival_probability_wait" in a.index else (
        avail if on_clock else bm.survival_between(a, this_pick, wait_reference)
    )
    tm = bm.timing(wait, wait_reference, 1.0 if on_clock else avail)
    # Work order 2026-08-29c item 8 (R44): pessimistic quantile PRIMARY ("assume the
    # value gets taken"), optimistic (the point estimate) kept alongside in a smaller,
    # muted figure -- "a roadmap that is always worst-case will read as noise within
    # two rounds." Explicitly labeled ("worst-case") rather than a bare percentage,
    # per the work order's own "label it as such on screen."
    legs = "".join(
        f'<div style="display:flex;gap:8px;font-size:12px;line-height:1.4">'
        f'<span style="width:30px;color:{t["faint"]}" class="nk-num">{l["pick"]}</span>'
        f'<span style="width:6px;height:6px;border-radius:50%;margin-top:6px;'
        f'background:{bm.POSITION_COLORS.get(l["position"], t["muted"])}"></span>'
        f'<span style="flex:1">{html.escape(l["player"])}</span>'
        f'<span style="color:{t["muted"]}" class="nk-num">'
        f'{l.get("odds_pessimistic", l["odds"]) * 100:.0f}%'
        f'<span style="font-size:9px;color:{t["faint"]}" title="optimistic (point estimate)">'
        f' (opt {l.get("odds_optimistic", l["odds"]) * 100:.0f}%)</span></span></div>'
        for l in route["legs"]
    )
    short = {k: v for k, v in route["shape_left"].items() if v > 0}
    cost = (
        "After "
        + str(route["legs"][-1]["pick"] if route["legs"] else this_pick)
        + " you still owe "
        + " and ".join(f"{v} {bm.POSITION_WORDS[k].lower()}{'s' if v > 1 else ''}" for k, v in list(short.items())[:2])
        + "."
    ) if short else "This path fills every slot you planned for."
    tag = ["ROUTE A", "ROUTE B", "ROUTE C"][index]
    return f"""
<div class="nk-card" style="border-top:2px solid {color}">
  <div style="display:flex;justify-content:space-between;align-items:baseline">
    <div><span style="font-size:11px;font-weight:600;letter-spacing:.1em;color:{color}">{tag}</span>
    <span style="font-size:14px;font-weight:500;margin-left:8px">{bm.POSITION_WORDS[pos]} now</span></div>
    <span style="font-size:10px;color:{tm.worth_the_pick and t['good'] or t['warn']};font-weight:600">{html.escape(tm.sentence)}</span>
  </div>
  <div style="display:flex;gap:10px;margin-top:8px">
    <div style="min-width:184px;border:1px solid {color};border-radius:8px;padding:8px 10px;
         background:{color}22">
      <div class="nk-kicker">Pick {this_pick}</div>
      <div style="font-size:16px;font-weight:500">{html.escape(str(a['player']))}</div>
      <div style="font-size:11px;color:{t['muted']}">{bm.POSITION_WORDS[pos]} &nbsp; {html.escape(str(a['nfl_team']))}
        &nbsp; Sleeper {'' if pd.isna(a['reference_adp_rank']) else int(a['reference_adp_rank'])}</div>
    </div>
    <div style="flex:1">
      <div class="nk-kicker" style="font-size:9px">Worst-case odds still there</div>
      {legs}
    </div>
  </div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px;
       padding-top:8px;border-top:1px solid {t['line_soft']}">
    <div><div class="nk-kicker">Over replacement</div>
      <div style="font-size:17px;font-weight:500;color:{color}" class="nk-num">{route['vorp_sum']:+.0f}</div></div>
    <div><div class="nk-kicker">Board value</div>
      <div style="font-size:17px;font-weight:500" class="nk-num">{route['composite_sum']:.0f}</div></div>
    <div><div class="nk-kicker">Reach cost</div>
      <div style="font-size:17px;font-weight:500;color:{t['bad'] if route['reach_penalty_sum'] > 0 else t['muted']}" class="nk-num">
      -{route['reach_penalty_sum']:.0f}</div></div>
  </div>
  <div style="font-size:11px;color:{t['muted']};margin-top:8px;border-left:2px solid {color};
       padding-left:8px">{html.escape(cost)}</div>
</div>
"""


def render_rule_timeline(timeline: list[dict], theme: dict) -> str:
    t = theme
    sev = {"High": t["bad"], "Medium": t["warn"], "Low": t["muted"]}
    cells = []
    for entry in timeline:
        tick = t["accent"] if entry["is_now"] else t["line"]
        chips = "".join(
            f'<span class="nk-chip" style="font-size:9px;height:15px;'
            f'background:{sev[r["severity"]] if r["firing"] else "transparent"};'
            f'border:1px solid {sev[r["severity"]] if r["armed"] else t["line"]};'
            f'color:{t["bg"] if r["firing"] else (sev[r["severity"]] if r["armed"] else t["faint"])}" '
            f'title="{html.escape(r["label"])}">{r["code"]}</span>'
            for r in entry["rules"]
        )
        cells.append(
            f'<div style="border-top:2px solid {tick};padding-top:4px;min-height:44px">'
            f'<div style="font-size:10px;color:{t["accent"] if entry["is_now"] else t["faint"]};'
            f'font-weight:{600 if entry["is_now"] else 400}">R{entry["round"]}</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:2px">{chips}</div></div>'
        )
    return (
        f'<div class="nk-card"><div style="display:grid;grid-template-columns:repeat(16,1fr);gap:3px">'
        f'{"".join(cells)}</div></div>'
    )
