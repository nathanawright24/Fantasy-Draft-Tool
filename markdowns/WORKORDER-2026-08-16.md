# Work Order — 2026-08-16 (post-strategy session)

Read `markdowns/SPEC-draft-tool-build.md` §12 first for context. This is the next block after it.
Items are ordered by priority. **Do not reorder.** Each has an acceptance check — run it and
report the result rather than asserting the change works.

New file to place: `2026/PLAYER-INTEL-2026.md` (provided separately).

---

## 1. 🚨 Re-anchor the survival curve to Sleeper — highest priority

**Problem.** `app/draft_engine.py` (~lines 253-256, 280-284) builds every survival curve from
`comparison_adp_*` — NFFC. But the owner drafts on Sleeper, and the two populations diverge
systematically. Measured across the top 150 by Sleeper rank:

| Position | mean(Sleeper rank − NFFC ADP) | n |
|---|---|---|
| TE | **−23.2** | 19 |
| QB | **−13.2** | 18 |
| RB | −3.2 | 41 |
| WR | **+9.0** | 56 |

The model therefore overestimates TE availability by ~23 picks and QB by ~13, and underestimates
WR by ~9. Every error runs in the direction that makes the owner's hardest decisions look safer
than they are.

**Fix.** Anchor the lognormal's **centre** on `reference_adp_rank` (Sleeper). Keep NFFC's
`min` / `max` / `n` for **dispersion only** — 51 observed drafts remain the only empirical spread
data available, and that spread is a property of draft variance generally, not of NFFC's level.

Add to `config.py`:

```python
SURVIVAL_ANCHOR = "reference"            # centre of the curve
SURVIVAL_DISPERSION_SOURCE = "comparison"  # min/max/n for spread only
```

Implementation notes:

- Derive sigma from NFFC's `min`/`max`/`n` as already implemented (extreme quantiles of *n*
  draws), then **relocate** the distribution so its median equals the Sleeper rank.
- **Document the rank-as-pick assumption in a comment.** `reference_adp_rank` is an ordinal, not a
  mean pick; rank *N* ≈ pick *N* only if the field drafts near consensus. That is acceptable here
  and needs to be stated, not hidden.
- **Fall back to the comparison anchor when `reference_adp_rank` is null**, and set a flag column
  so the UI can show which anchor a row used.
- **Do not hard-code the offsets in the table above.** Compute them at build time into a small
  diagnostic (`data/derived/adp_source_offsets.csv`) so next season's numbers are next season's.
  They are a property of the site, not a constant.

**Acceptance:**
- `Sam LaPorta` survival from `as_of_pick=53` to `target_pick=68` drops materially versus the
  current NFFC-anchored value. Report both numbers.
- `Jalen Hurts` survival to `target_pick=68` is low, consistent with Sleeper rank 64.
- Expected departures still ≈ intervening pick count (the §12.3 invariant must not regress).
- `adp_source_offsets.csv` reproduces the four position offsets within rounding.

---

## 2. Ken Walker III — join failure, and the report severity that hid it

**Problem.**

```
player: Ken Walker III   name_key: ken walker
reference_adp_rank: NaN   comparison_adp_value: NaN
```

Clay writes "Ken Walker III"; both ADP sources write "Kenneth". Suffix-stripping does not catch
nickname mismatches. He is NFFC's #15 and a named round-2 target, and he currently has **no market
data from either source**, so the availability model cannot speak about him at all.

**Fix.**
1. Add `ken walker` → `kenneth walker` to `NAME_ALIASES`.
2. **Reclassify severity:** any player inside the top 150 of *either* ADP source that fails to
   join is a **hard failure**, not a note. `join_report.txt` reported "0 hard failures" while a
   top-20 player had no market data — the report was miscalibrated, which is worse than the bug.
3. Audit for other nickname mismatches in the same class and alias them.

**Acceptance:** Walker has non-null `reference_adp_rank` and `comparison_adp_value`; the top-150
unmatched hard-failure count is non-zero before the alias and zero after.

---

## 3. Intel layer — `build/08_parse_intel.py`

Parse `2026/PLAYER-INTEL-2026.md` into `data/derived/player_intel.csv` and join to
`player_master` on `name_key + position`.

Required columns: `player`, `position`, `pick_window`, `tag`. Optional: `priority`, `note`.
Same normalization and alias path as every other join — reuse it, don't reimplement.

Add to `config.LAYERS`:

```python
"player_intel": {"available": True, "applies": True},
```

**Two tag behaviours, deliberately different:**

- `target` / `fade` — a **bounded nudge, hard-capped at ±10 VOR points**. Roughly one tier: enough
  to break a tie or jump a near neighbour, not enough to overrule a projection. Surface the nudge
  as its own column in the expand view so it is always visible separately from the math.
- `hard_avoid` — a **filter**. Excluded from recommendations entirely, no arithmetic.

**Do not raise the cap, and do not add a weight slider for it.** The cap is the entire point: an
unbounded intel column lets a hunch quietly rebuild the board, which would waste the projection
system it is supposed to inform.

`pick_window` feeds the recommender: a player tagged for pick 53 should surface as a target at 53,
not at 20. `priority` orders players within a window.

**Acceptance:** LaPorta's composite moves by ≤10 with the layer on; toggling
`applies=False` restores the exact prior composite; a `hard_avoid` row never appears in
recommender output.

---

## 4. Recommender corrections from the strategy session

These are findings to encode, not opinions to apply automatically.

- **Picks 77 and 92 are a QB dead zone on Sleeper.** Nothing above 27.9 composite. The real QB
  windows are pick 53 (Maye 44.5 / Burrow 38.8) and pick 101 (Stafford 33.1). If the recommender
  suggests a QB at 77 or 92, it should say that both neighbouring windows are better.
- **The pick-53 fork is value-neutral.** LaPorta 35.5 + Stafford 33.1 = 68.6 against
  Maye 44.5 + Kelce 23.8 = 68.3. The tiebreak is fallback cost: Stafford missing → Purdy
  (−6.2); Kelce missing → Andrews (−8.1). Present it that way rather than picking for the owner.
- **W18 congestion still applies** to picks 77–125, which now must absorb QB1, RB2/RB3, TE1/TE2
  and a fifth WR.

---

## 5. Housekeeping

- **`.gitignore`** — `__pycache__/`, `*.pyc`, `state/`. Committed `.pyc` files are on GitHub now.
- **Reconcile the two copies.** GitHub has §12 via browser upload (commit `6102eb6`); the local
  tree is uncommitted. Pick one as authoritative before the dry run and make the other match.
- **`README.md` is empty.** One paragraph pointing at `markdowns/SPEC-draft-tool-build.md`.
- **Re-run the backtest after item 1.** Anchor changes will move calibration; the existing Brier
  ~0.03 is no longer valid. Report the new figure and keep the "sanity check, not a tuning target"
  framing from R25.

---

## Do not do

- Do not tune anything to the backtest. Two seasons of one league, and the scoring rules changed.
- Do not fold `bonus_est_ppr` back into a percentile. It enters in points (R18) so the
  cross-positional shift survives into the ranking.
- Do not raise the intel cap or make it configurable from the UI.
- Do not hard-code the ADP source offsets.
- Do not touch `scripts/` — the image pipeline already ran.

## Ask before doing

- Anything that changes `ROSTER_TARGET`, `COMPOSITE_WEIGHTS` defaults, or replacement-level
  definitions. Those are owner decisions (R16/R17/R22), already settled.
- Committing to git. The owner declined once already.
