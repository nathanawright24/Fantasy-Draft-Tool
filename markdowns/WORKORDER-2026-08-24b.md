# Work Order — 2026-08-24b (post mock draft)

Five issues from the owner's first mock draft. **Items 2 and 4 are the same bug.**
Priority order below is deliberate: item 1 corrupts draft state, item 2 makes the tool give
wrong advice, item 3 makes it unusable under the clock.

Every acceptance check produces a number or a diff. Report the value; do not assert.

---

## 1. 🚨 `NAME_ALIASES` never reaches the live sync path

**Symptom.** Kenneth Walker stayed on the board for multiple rounds after being drafted.

**Cause.** The alias table lives in `build/pipeline.py`; `config.normalize_name` does not
apply it.

```
config.normalize_name("Kenneth Walker III")  ->  "kenneth walker"
player_master.name_key for that player       ->  "ken walker"
```

`main_cockpit.sync_from_sleeper` looks up `board[board["name_key"] == key]`, matches nothing,
and falls through to its bare-`pd.Series` fallback. The pick is logged under the wrong key,
`draft_state.drafted_name_keys()` never matches the board row, and the player is never
removed.

**This is a class of bug.** Every alias fails the same way in the live path: `cam ward`,
`chig okonkwo`, `kenneth walker`, `cameron skattebo`, `kenny gainwell`.

**Task.**
1. Move `NAME_ALIASES` into `config.py` and apply it inside `normalize_name` — or add a
   `config.canonical_key()` that both the pipeline and the app call. One code path, not two.
2. **Make an unmatched sync pick loud.** The silent bare-Series fallback is why this read as
   a display glitch instead of a join failure. An unmatched Sleeper pick should surface on
   screen and in a log, naming the raw string that failed.

**Acceptance:** simulate a sync of "Kenneth Walker III" and confirm he leaves the board.
Report the count of aliases now shared between pipeline and app. Confirm an unmatched name
raises something visible.

---

## 2. 🚨 The route sorter never looks at value (also fixes item 4)

**Symptoms.** Ja'Marr Chase was available at pick 5 and never suggested. Chase Brown, a
second-round player, was offered at pick 5.

**Cause.** `board_model.candidates_for_pick` sorts by:

```python
by=["on_list", "priority", "sharp_edge", "composite_score"]
```

`vorp` is computed three lines earlier and **never used in the sort.** Consequences:

- Ja'Marr Chase is absent from the intel table (the owner assumed the top four would be
  gone), so `on_list=False` and he sorts below every tagged name regardless of being the
  best player available.
- Chase Brown is tagged priority 1 for window 20. Non-intel players receive
  `priority = 99`. So a priority-1 name tagged for a *different pick* outranks the best
  player in the draft.

**The framing that matters:** the ±10 intel cap is intact and irrelevant. It bounds intel's
effect on `composite_score`, but route eligibility and ordering bypass scoring entirely.
Intel currently has **unbounded influence through a side door** — precisely what R28's cap
was written to prevent.

**Task.** Sort by value, with intel as a tiebreak and an annotation, never as the gate:

1. Primary sort on the **drop-off adjusted edge** from item 4 below.
2. `on_list` becomes a tiebreak among near-equal candidates, plus the "Your note" display
   already in `cockpit_html`.
3. `priority` only orders players **within the same pick window**. A name tagged for window
   20 must never outrank anything at pick 5. Fix `_intel_windows` matching so
   window mismatch means no priority boost at all.
4. **Always include the top N by edge regardless of intel status**, so the best available
   player can never be invisible.

`hard_avoid` remains a hard filter — that one is intended.

**Acceptance:** at pick 5 with a full board, Ja'Marr Chase appears as a route anchor and
Chase Brown does not. Report the top five candidates at pick 5 before and after.

---

## 3. 🚨 Sync latency — 15 of 90 seconds

**Symptom.** Roughly 15 seconds to repopulate after pressing Sync.

**Task, in payoff order. Measure first and report a per-stage breakdown before optimising.**

1. **Cache availability** on `(frozenset(drafted_name_keys), as_of_pick, target_pick)` so a
   rerun with no new picks costs nothing.
2. **Cut simulations from 2000 to 500.** Decision-band Brier is 0.1467; simulation standard
   error at n=500 is about ±2 points. Paying 4× runtime to shrink a ±2 noise term sitting
   underneath a ±37 model error is not a trade worth making. Keep the count in `config.py`.
3. **Vectorise `candidates_for_pick`.** It currently uses `pool.iterrows()` over ~430 rows
   calling `survival_between` twice per row, and is invoked once per route plus once per leg
   — roughly ten full passes and ~8,000 scipy-touching calls per rerun. This is the likely
   dominant cost.
4. **Stop rebuilding routes inside the 6-second poller.** The poller should detect new picks
   and trigger one app rerun, nothing more.
5. Time the Sleeper HTTP call separately — it has a 5s timeout and may be a large share.

**Acceptance:** report per-stage timings before and after. Target under 2 seconds end to end
from button press to repainted board.

---

## 4. Positional scarcity relative to what reaches the owner's pick

**Owner's diagnosis:** *"positional vorp scarcity relative to what is likely to get to my
pick is not being factored enough. The league is very RB-hungry."* Correct — nothing in the
candidate sort measures it.

**Task.** Replace raw VORP with a drop-off adjusted edge:

```
edge(p) = vorp(p) - E[ max vorp among same-position players available at owner's next pick ]
```

with the expectation taken over survival probabilities from the chosen availability method
(montecarlo, per R36).

Why this is the right shape:

- It encodes RB hunger automatically. If backs evaporate before the next turn,
  `E[best RB next turn]` is low, so an RB now scores high — no hand-tuned position weights.
- **It subsumes the wait rule.** A player almost certain to survive has a near-zero edge by
  construction, which replaces the crude `WAIT_THRESHOLD = 0.70` cutoff with a continuous
  quantity. Keep the NOW/CLOSE/WAIT/GONE chips as display; derive them from edge.
- It fixes item 2 independently: Chase Brown's VORP minus the expected best RB at 20 is
  small; Ja'Marr Chase's minus the expected best WR at 20 is very large.

**Acceptance:** report `edge` for the top 10 at pick 5 and at pick 53. Confirm RB edges rise
relative to WR when RB survival is low. Confirm a high-survival QB has near-zero edge without
any explicit threshold.

---

## 5. Display honesty follow-up (from the bake-off result)

Decision-band Brier of 0.1467 against 0.25 for always guessing 50% means the model is
better than a coin flip and nowhere near precise. Two consequences:

1. **Make the NOW/CLOSE/WAIT/GONE chip the primary display and the percentage secondary.**
   Four buckets is roughly the resolution the model has earned. A bare "45%" invites the
   owner to distinguish it from 52%, which the Brier says he cannot.
2. **Drop the ±1 confidence band, or replace it.** It is the standard error across
   simulations, so it measures whether the simulation converged, not whether the model is
   right. Printing "45% ±1" next to a decision-band Brier of 0.1467 claims precision that
   does not exist, and it is exactly the number the owner would trust at pick 53 with
   90 seconds left. Handoff §6 already flagged the band as saying less than the
   alternatives; this result upgrades that from caveat to defect.

**Acceptance:** show the rendered row for one CLOSE player and one WAIT player.

---

## 6. Bake-off record correction

The three decision-band Brier scores were computed on **different observation counts**
(montecarlo n=568, lognormal n=1275, blend n=1071), because band membership is defined by
each method's own predictions. Brier is not comparable across different samples, so the
"18% gap" is not a sound comparison.

**This does not change the R36 decision** — montecarlo wins on the capacity invariant, which
is a structural argument independent of Brier. But the reasoning recorded in `config.py` and
`backtest.py` should **lead with capacity** and treat the Brier figures as weak supporting
evidence, so that a different gap next August does not produce a confident wrong conclusion.

**Optional, not before the draft:** re-score both methods on an identical externally-defined
observation set (for example, all picks where the player's ADP is within 20 of the target
pick).

---

## Do not do

- Do not raise the ±10 intel cap. Item 2 is about removing an unintended *bypass* of it, not
  loosening it.
- Do not remove `hard_avoid` as a filter — that one is intended.
- Do not tune anything to the backtest.
- Do not touch `scripts/`.

## Ask before doing

- Anything changing `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level.
- Committing to git.
