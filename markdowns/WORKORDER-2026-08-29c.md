# Work Order — 2026-08-29c (roster-aware suggestions + market sharpness)

Findings from the owner's second mock draft. **Items 1 and 2 are the same root cause and
account for four of his reports.** `WORKORDER-2026-08-29.md` item 5 (Render) is withdrawn —
not being done.

Every acceptance check produces a number or a diff. Report the value.

---

## 1. 🚨 `remaining` is passed to `candidates_for_pick` and never used

**Owner's reports:** *"It doesn't seem as if the suggestions/top 3 players factor what I have
drafted so far"*, *"suggestions and roadmaps should factor what I have already drafted"*, and
the QB/TE-repeats-in-top-3 complaint.

**Verified cause.** `board_model.py:509-511`:

```python
remaining = {pos: cap - roster_counts.get(pos, 0) for pos, cap in config.ROSTER_TARGET.items()}
anchors = candidates_for_pick(board, this_pick, this_pick, next_after, remaining, set(), min_availability=0.0)
```

`remaining` appears nowhere inside `candidates_for_pick`'s body. Roster state is threaded
through the signature and discarded. It survives only into `build_route`'s `shape` dict, which
feeds the "you still owe two receivers" display line.

**This is the same failure pattern as `vorp` being computed and omitted from the sort
(24b item 2). Second occurrence.** Worth a test that asserts every parameter a ranking
function accepts actually influences its output.

**Task.** Make candidate ranking need-aware, and see item 2 for what "need" must mean.

**Acceptance:** with a roster holding 1 QB and 1 TE, report the top 10 at pick 77 before and
after. Confirm QB and TE fall. Then set the roster to empty and confirm they return.

---

## 2. 🚨 Need must be marginal and starter-based, not cap-based

`remaining = cap - held` uses `ROSTER_TARGET` (6 WR / 5 RB / 2 TE / 2 QB). After TE1 is taken,
`remaining["TE"] = 1`, so a second tight end still reads as fully needed — and TE `edge` is
already inflated because TE fallback pools are thin. Measured at pick 44: **TE is 10 of the
top 30 by edge.**

**Task.** Discount by starting-slot status:

- **Starting slots unfilled** (no QB1, no TE1, fewer than 2 RB, fewer than 2 WR) → full weight.
  The scarcity is real.
- **Starting slots filled** → the marginal player at that position competes for a **flex** spot,
  so his fallback comparison is the best flex-eligible alternative (RB/WR/TE), not the best at
  his own position. QB has no flex path at all, so QB2 should fall off a cliff — consistent
  with R11 naming QB2 the designated sacrifice.

This is the same flex-aware fallback correction raised earlier, now with a reproducible
symptom rather than a synthetic one.

**Acceptance:** report `edge` for the best available TE with 0 TEs rostered vs 1 TE rostered.
The second must be materially lower. Report the position mix of the top 30 by edge in both
states.

---

## 3. 🚨 Missing ADP makes a player look free — the Jayden Higgins class

**Owner's report:** *"In round 6 of a mock draft I'm having Jayden Higgins suggested 100 picks
before his adp (he also tore his acl)."*

**Verified.** Higgins has **no ADP from either source** — `reference_adp_rank` and
`comparison_adp_value` both null. That is the footprint of an injured player: Sleeper delisted
him, NFFC drafters stopped taking him. Three things then compound:

1. `ppr_base = 124.95`, from Clay's **2026-08-13** projections — before the injury, full season.
2. Per 29.md item 1, a null NFFC ADP means **the reach penalty is zero**.
3. No market data means survival falls back to something permissive.

**Absence from both markets is strong negative information — injury, suspension, camp
casualty, retirement — and the tool currently reads it as "no opinion."**

**Task.** A player missing from **both** markets is excluded from suggestions and routes, and
shown on the full board with an explicit "no market" flag. Missing from **one** market is a
flag and a widened survival interval, not an exclusion. **Null reach penalty must never be
zero** — treat it as the worst observed penalty for that position, or exclude.

Also add a `status` / `is_out` column if any source supplies one, so a known injury is data
rather than an inference from absence.

**Acceptance:** report the count of players missing both markets, missing only Sleeper, and
missing only NFFC. Confirm Higgins no longer appears in suggestions at any pick.

---

## 4. Route ordering ignores `edge`

```python
routes.sort(key=lambda r: r["vorp_sum"] + r["composite_sum"] - 2 * r["reach_penalty_sum"])
```

**Owner's report:** *"The 3 suggestions should be where the edge/over replacement says to go."*
Anchors are selected by `edge`, then the three routes are re-sorted by a key that does not
contain it. Make the route sort consistent with the anchor sort.

**Acceptance:** report the three route anchors and their route-sort scores before and after.

---

## 5. Setup screen: blank slots and hiding the fork

**Owner's reports:** *"I need a toggle for whether or not I want names or at least should be
able to leave a draft slot blank if someone from the main league isn't in that slot. There is
some slight carry over in the alt league."* And: *"The tight end fork still appears, I want to
be able to toggle that away from even being visible."*

**Task.**
- Any of the 12 draft slots may be left blank. A blank slot is an **unmodelled manager**: no
  behavioural prior, no team or college bias, and the availability model uses the generic-ADP
  fallback for that seat. Show which seats are unmodelled — the same requirement as the layers
  indicator (spec §4.2).
- Add a "show TE1 fork" toggle. When off, the fork is hidden from the setup screen entirely
  and W16 is dormant. `DEFAULT_TE1_FORK_PLAYER = ""` already exists; this is the visibility
  control on top of it.

**Acceptance:** boot with 4 blank slots and confirm no crash, that unmodelled seats are
labelled, and that survival still computes. Toggle the fork off and confirm it disappears from
setup and W16 stays quiet.

---

## 6. Pace targets: five receivers by round 9–10

**Owner's report:** *"we need to still worry about 5 WRs by round 9 or 10."*

Existing rules are threshold checks that fire once a target is already missed. Add a **pace
term**: for each position with a round-stamped target, compare held count against the number
implied by the current round, and surface the gap as urgency in the need weighting from item 2.

Round-stamped targets to encode: 5 WR by round 9–10; RB volume across rounds 9–13 (item 7);
QB1 pace-driven per R8; TE per the fork setting.

**Acceptance:** at pick 101 with 3 WRs rostered, confirm WR need weighting rises and report the
gap figure shown.

---

## 7. RB market shape — measured, and it partly contradicts the assumption

**Owner's request:** *"Assume a running back hungry market, feel free to scrape the old drafts
to prove this fact."* Done. Main league, 4 seasons, 792 picks:

| Round band | QB | **RB** | WR | TE |
|---|---|---|---|---|
| R1–3 | 6.2 | **44.4** | 43.1 | 6.2 |
| R4–6 | 16.7 | 23.6 | 43.8 | 14.6 |
| **R7–9** | 11.8 | **34.7** | 41.0 | 11.1 |
| R10–13 | 12.5 | 25.0 | 32.3 | 14.1 |

RB share of the first three rounds by season: 53%, 39%, 42%, 44%.

### 🚨 The tilt is not where it was assumed

The **current Sleeper top 36 is 47.2% RB.** So in rounds 1–3 this league drafts RBs slightly
*below* the rate ADP implies. The market is RB-heavy this year; the league is not tilting beyond
it. Do **not** add a blanket RB-hunger multiplier — it would double-count what ADP already
prices.

**Where the league is distinctive is a second RB wave in rounds 7–9**, 34.7% against 23.6% in
rounds 4–6. That is exactly where the owner plans RB2 and RB3. By rounds 10–13 competition
eases to 25.0%.

**Task.** Encode the round-band positional appetite empirically from
`drafts/all_draft_picks_2022-2025.csv` rather than as a hand-set constant, and let the
manager-priors layer carry it. Recompute each season; it is a property of the league, not a
number to paste.

**Acceptance:** report the per-band position shares the model derives and confirm they match
the table above within rounding.

---

## 8. Assume a market slightly sharper than ADP

**Owner's request:** *"Assume slightly sharper market than adp, if there's glaring values in
pockets of the draft vs sleeper adp: assume that managers will reach on them and adjust my
roadmaps to 'assume the worst.'"*

The reach model (R31) already lets managers reach up to 15 picks, centred on each manager's
observed mean. This adds a **value-seeking** component: a player whose `market_reach_gap`
marks him as clearly cheap should have elevated reach probability, not just his position's
base rate.

**Task.**
- Reach probability rises with the player's position-adjusted value gap.
- Roadmap legs are shown at a **pessimistic** quantile of the survival distribution rather than
  the mean, so plans assume the value gets taken. Label it as such on screen.
- Keep the optimistic figure available, because a roadmap that is always worst-case will read
  as noise within two rounds.

**Do not stack this with item 7.** If both a blanket RB multiplier and a value-seeking reach
term were added, glaring RB values would be double-penalised.

**Acceptance:** report survival for the three largest positive `market_reach_gap` players
before and after. They must fall. Confirm the capacity invariant holds.

---

## 9. Pre-existing: `_w16`'s dead flag

Reported by the previous session, not introduced by it: `fork_miss_branch` is checked alongside
`TE count == 0`, but the flag's own formula requires `TE count > 0`, so it is structurally
always false whenever the surrounding check fires. W16 can never fire. Fix or delete — a rule
that cannot fire is worse than no rule, because the timeline displays it as armed.

**Acceptance:** state which you did and show the condition that now fires.

---

## Awaiting owner confirmation

The report *"once I've selected a tight end or qb I should have it as one of the top 3
suggestions for the rest of the draft"* reads as the opposite of the surrounding bullets. Items
1 and 2 are written on the assumption he meant **should not**. **Confirm before building.**

---

## Do not do

- Do not add a blanket RB-hunger multiplier (item 7).
- Do not stack item 8's value-seeking reach with an RB multiplier.
- Do not change the `edge` formula, its fallback, or `n_sims`. Item 2 changes the *need
  weighting*, not the metric.
- Do not raise the ±10 intel cap; `hard_avoid` stays a hard filter.
- Do not let coverage, intel, or `pick_window` affect eligibility or ordering.
- Do not tune to the backtest. Do not touch `scripts/`.
- Render deployment is withdrawn.

## Ask before doing

- Anything changing `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level.
- Committing to git.
