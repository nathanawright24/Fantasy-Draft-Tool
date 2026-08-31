# Work Order — 2026-08-31 (overnight queue)

**The owner is asleep and cannot answer questions.** Every previously open question is resolved
below. If something genuinely new comes up, **stop and leave a note rather than guessing** — an
unattended wrong guess costs more than an unfinished item.

## Queue order

Two earlier work orders are unstarted. Do them in this order, then the new items here.

| # | Source | Item | Risk |
|---|---|---|---|
| 1 | 29c | Items 1+2 — roster-aware need (one block) | med |
| 2 | 29c | Item 3 — missing-ADP guard, Higgins class | low |
| 3 | 29c | Item 4 — route sort consistency | low |
| 4 | 29c | Item 5 — blank draft slots, hide TE fork | low |
| 5 | 29c | Item 9 — `_w16` dead flag | low |
| 6 | 08-30 | Item 2 — positional run detector | med |
| 7 | **new** | Item A — cost-of-waiting panel | low |
| 8 | **new** | Item B — board-assumption toggle | low |
| 9 | 08-30 | Items 1, 3, 4 — pacing priors, QB calibration | med |
| 10 | 29c | Items 6, 7, 8 — pace targets, RB shape, market sharpness | med |
| 11 | **new** | Item C — waiver-level second baseline | med |

**Verify first, before anything else:** two fixes were handed over as chat prompts and their
status is unknown. Confirm both, and report which needed work:
- `cockpit_html.render_route_card` rendering a literal `</div>` in the worst-case-odds box.
- `build_routes` returning zero routes when the next owner pick is ~3 picks away. Must never
  return empty; fall back to unfiltered top-by-edge labelled "short gap, nothing is urgent."

---

## 🚨 Resolved: the blocked question from 29c

29c items 1 and 2 were blocked on an ambiguous report. **Resolved: the owner meant "should
NOT."** Once QB1 and TE1 are filled, those positions must stop occupying the top three
suggestions. Everything in the strategy work since confirms it. **Build items 1 and 2 as
written.**

---

## Item A — Cost-of-waiting panel (new, R44)

Owner answered "both" when asked what belongs on the cockpit permanently. This is the first half.

For each position, show **best-available VORP now** and **expected best-available at the owner's
next pick**, with the difference. Derived from the same availability pass as `edge` — no extra
simulation.

Why it matters, measured on the current board from slot 5:

| Turn | VORP lost across the turn |
|---|---|
| 20 → 29 | **101.6** |
| 44 → 53 | 49.3 |
| 53 → 68 | 53.8 |
| 68 → 77 | 4.3 |
| 77 → 92 | 3.6 |
| 92 → 101 | 2.5 |

And the per-position cliffs it would expose: RB goes **28.9 at pick 53 to 3.6 at 68**, while WR
holds flat at 24.0 from 68 through 92 and TE sits at 9.4 from 77 through 116.

This is the single most useful artifact the strategy analysis produced, and it currently exists
only as an ad-hoc script. On screen at pick 53 it would say: RB drops 25 points before your next
turn, WR drops nothing.

**Acceptance:** paste the rendered panel at picks 29, 53 and 92. The figures must reproduce the
table above within rounding on the ADP-order assumption.

---

## Item B — Board-assumption toggle (new, R45)

Owner answered "directionally, but I want the tool to show a pessimistic version."

Add a toggle with three states, affecting **availability only** — never VORP, never the edge
formula:

1. **ADP order** — current behaviour.
2. **Observed reach** — apply the measured per-position deltas from the alt-league draft
   (RB +5.5, WR +8.0, TE −4.0, QB −18.5 median picks versus Sleeper rank). Derive these from
   `drafts/alt_league_official_2026.csv`; **do not paste them.**
3. **Pessimistic** — the worst-case quantile of the survival distribution.

### 🚨 "Observed reach" is not uniformly pessimistic

RB and WR went earlier than ADP, so those get worse. **QB went 18.5 picks later, so QB gets
better.** Under mode 2 a top QB's effective availability extends by nearly two rounds. Label the
modes accurately — calling mode 2 "pessimistic" would be wrong and would mislead on the one
position where the market is loosest.

Measured impact of mode 2 on the owner's full 15-pick sequence: **238.3 total VORP falls to
172.9**, and almost all of the 65.4-point loss lands at picks 5, 29, 44 and 53. Rounds 7 onward
barely move.

**Acceptance:** report best-available by position at picks 5, 29, 44, 53 in all three modes.
Confirm QB improves under mode 2 while RB and WR worsen.

---

## Item C — Waiver-level second baseline (new, R46)

VORP is measured against the **last startable player** (R17), which is right for lineup decisions
and wrong for late-round roster decisions. In a 12-team league where every manager targets five
backs, roughly 60 RBs get rostered — so the running back actually obtainable in week 3 is far
worse than the replacement the model uses.

Consequence: every RB past pick 92 scores at or below zero (Aaron Jones at ADP 122 is exactly
0.00), which makes the owner's rounds 9–13 dart strategy look strictly irrational. It isn't — it
is an injury hedge and a lottery ticket, scored against the wrong alternative.

**Task.** Add a second baseline — value over *waiver* level, set at roughly
`12 × ROSTER_TARGET[pos]` deep — and display it alongside VORP **for picks past round 8 only**.
Do not replace VORP; do not fold it into `edge`.

**Acceptance:** report both baselines for the top 10 RBs with ADP > 100. The waiver figure should
be positive for several of them.

---

## Standing constraints — unchanged and not up for revision overnight

- Do not change the `edge` formula, its fallback, or `AVAILABILITY_N_SIMS = 500`. Measured: max
  |Δedge| 0.6 across seeds at both 500 and 2000.
- `AVAILABILITY_METHOD = "montecarlo"`. The capacity invariant is the argument; Brier is weak
  supporting evidence computed on mismatched samples.
- Do not raise the ±10 intel cap. `hard_avoid` stays a hard filter. **Note the intel file now
  uses `hard_avoid` for Rashee Rice, Jayden Higgins and Jonathon Brooks** — confirm the filter
  actually excludes them from suggestions and routes.
- Do not let coverage, intel, or `pick_window` affect eligibility or ordering.
- Do not add a blanket RB-hunger multiplier — the league drafts RBs at 44.4% in rounds 1–3
  against a 47.2% ADP baseline, so it is already priced.
- Do not weight the alt draft equally with main-league history. 3 of 12 managers overlap.
- Do not tune anything to the backtest. Do not touch `scripts/`.
- Render deployment is withdrawn.

## Ask before doing — leave a note, do not proceed

- Anything changing `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level.
- Committing to git.
- Any refactor of working ingestion or pipeline code.

## Also refreshed

`2026/PLAYER-INTEL-2026.md` has been rewritten with the revised plan: pick 20 is now a tight end
(Bowers/McBride), McCaffrey and Rashee Rice and Jeremiyah Love are faded, Breece Hall is the
pick-29 target and Skattebo the pick-44 target. Re-run the build so `player_intel.csv` picks it
up, and report how many intel rows joined.
