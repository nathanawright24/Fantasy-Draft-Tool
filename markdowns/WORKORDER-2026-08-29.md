# Work Order — 2026-08-29 (portability and reach calibration)

**Sequencing:** `WORKORDER-2026-08-24b.md` has not been started and comes first — it fixes
wrong advice (route sorter) and unusability (15s sync). This work order is everything after
that, plus **item 0 below, which is urgent regardless and takes two minutes.**

Every acceptance check produces a number or a diff. Report the value.

---

## 0. 🚨 New ADP files — glob mismatch and a schema change

Fresh files dated 2026-08-29 go in `Tool/data/raw/`.

**Filename.** `config.SLEEPER_ADP_RAW_GLOB` is `sleeper_adp_ppr_*.csv`. The new export is named
`sleeper_adp_2026-08-29.csv` — no `ppr_`. The glob will not match, so
`latest_sleeper_adp_raw_path()` silently returns the **August 16 file**, the build succeeds,
and every number is two weeks stale with nothing on screen saying so.

Do both: widen the glob to `sleeper_adp*.csv`, **and** assert the resolved file's
`Date Pulled` is within N days of today, failing loudly otherwise. A stale-data build must
never succeed quietly.

**Schema change.** The Sleeper export went from 8 columns to 10. It now separates `ADP Rank`
from `ADP`, and they diverge deeper in the board (row 201 is rank 201, ADP 204).
`build/pipeline.py:323-324` assigns both from the same column:

```python
"adp_value": r["ADP"],
"adp_rank":  r["ADP"],     # must become r["ADP Rank"]
```

Also new: a `Match Key` column (`jahmyr gibbs|RB`) — pre-normalized and position-qualified.
Use it as a first-pass join key for the crosswalk in 24b item 1, falling back to the existing
normalization when absent, since other sites will not supply it.

**Note for the record:** NFFC `# Picks` rose from 51 to 97, roughly doubling the sample behind
every `min`/`max`/`n`. The lognormal dispersion fit should tighten accordingly. NFFC row count
433 → 461, Sleeper 247 → 261.

**Acceptance:** report the resolved raw filenames and their `Date Pulled` / vintage; report
row counts and the count of rows where `ADP != ADP Rank`.

---

## 1. 🚨 Position-adjusted reach penalty (R37)

**Owner ruling (question 2, option b):** a soft penalty scaling with how far ahead of NFFC ADP
a pick is — **but position-adjusted.**

**Why adjustment is mandatory.** Measured Sleeper-minus-NFFC offsets: QB −13.2, TE −23.2,
RB −3.2, WR +9.0. A raw `nffc_adp − sleeper_rank` therefore marks **every QB and TE as poor
value automatically** — LaPorta −22, Maye −16 — not because those players are overpriced but
because they are onesies in a market that discounts onesies. Ranking on the raw gap
structurally steers the board toward WR, which is precisely the strategy weakness documented
across four seasons.

Owner's words: *"this requires becoming a less negative reach rather than scanning for value
vs Sleeper ADP."*

**Task.**

```
adjusted_gap = (nffc_adp - sleeper_rank) - position_offset
```

- `position_offset` comes from `data/derived/adp_source_offsets.csv` (R27). **Recompute each
  build; never hard-code.** The numbers above are from the 08-16 files and will move with the
  08-29 refresh.
- The soft reach penalty scales off `adjusted_gap`, not the raw gap.
- **Rename the displayed column.** "Sharp edge" now means something different from what it
  meant; leaving the label makes the old and new numbers indistinguishable in screenshots.
- Where `nffc_adp` is null, the penalty is zero and the row is flagged, not assumed neutral.

**Acceptance:** report `adjusted_gap` for the top 5 at each of QB/RB/WR/TE. A QB sitting at
exactly the position offset must score 0. Confirm no position is systematically negative.

---

## 2. Objective function: VORP plus shortlist coverage (R38)

**Owner ruling (question 1, options b and c):** maximize total VORP **and** maximize the
probability of landing at least one name from each pick window's shortlist.

**These are separate quantities and must stay separate.**

- **Ranking** is the drop-off adjusted edge from 24b item 4, less the reach penalty from item 1
  above. VORP-driven.
- **Coverage** is displayed: `P(at least one shortlist name for this window survives to my
  next pick)`, computed from the chosen availability method.

### 🚨 Coverage is informational only

It must never become a sort key, a filter, or a route gate. That is exactly the mechanism that
made Ja'Marr Chase invisible at pick 5 — intel gating eligibility rather than nudging score
(24b item 2). The ±10 cap governs score; nothing may govern eligibility except `hard_avoid`.

Coverage answers "how urgent is this position", not "who should I take."

**Acceptance:** show the coverage figure for pick 5 and pick 53. Confirm that setting coverage
to zero for every window leaves the candidate ordering unchanged.

---

## 3. Remove the LaPorta fork, keep it parameterized (R39)

**Owner ruling (question 3, option b):** keep the fork, driven by the setup-screen field.

`draft_setup.py` already carries `te1_fork_player`. Finish the job:

- No player name appears in `guardrails.py`, `board_model.py`, `draft_engine.py`, or
  `main_cockpit.py`. Every reference reads from setup.
- The fork **disables itself cleanly** when the field is blank — that is the default for any
  other league, and TE then falls through to generic value logic.
- W16's wording must not assume a specific player.

**Acceptance:** `grep -rn "LaPorta\|Kincaid" Tool/app Tool/build` returns nothing outside
comments. With a blank fork field, TE recommendations still work and no warning misfires.

---

## 4. Format-agnostic ADP ingestion (R40)

**Owner ruling (question 5, option c):** any ADP file format; Sleeper remains the only live
sync.

Add a declarative column-mapping layer so a new site's export needs a mapping, not code:

```
data/raw/adp_sources/<source>.yml   ->  column -> canonical field
canonical fields: player, position, team, adp_value, adp_rank,
                  adp_min, adp_max, adp_n, as_of
```

- Missing optional fields (`min`/`max`/`n`) must degrade gracefully — the survival fit already
  has a small-sample path; a source with no dispersion data should widen, not crash.
- Ship two working mappings as reference: the current Sleeper and NFFC formats.
- `REFERENCE_ADP` / `COMPARISON_ADP` select by source name.

**Acceptance:** ingest both current files through the mapping layer and reproduce
`player_master.csv` byte-identically against the direct parse. That equivalence is the proof
the refactor is safe.

---

## 5. Render deployment — not for draft day (R41)

**Owner ruling (question 4, options b, c, d):** portability, showing it to people generally,
not being tied to one machine. **Explicitly not draft-day and not for leaguemates.**

That makes Render viable, with constraints stated up front:

- **Free tier spins down on inactivity and has an ephemeral filesystem**, so draft state does
  not survive. Acceptable for demonstration; disqualifying for live use. Persistent disks are
  a paid tier.
- **Run live drafts locally.** Keep it in the README so future-you does not discover this at
  pick 40.
- The repo is public, so anything deployed is public. No secrets are required — the Sleeper
  API is unauthenticated — so the exposure is the analysis itself, which is the owner's to
  share.
- Add `render.yaml` plus a documented start command. Pin `streamlit` in `requirements.txt`;
  `st.fragment(run_every=...)` needs 1.37+.
- Consider a `DEMO_MODE` flag that disables live sync and loads a canned draft state, so a
  visitor sees a populated board instead of an empty setup screen.

**Acceptance:** deployed URL renders the cockpit with a canned state, and the README states
plainly that live drafts run locally.

---

## Do not do

- Do not let coverage, intel, or `pick_window` affect candidate eligibility or ordering.
  Only `hard_avoid` filters.
- Do not hard-code the position offsets — read `adp_source_offsets.csv`.
- Do not raise the ±10 intel cap.
- Do not deploy anything intended for draft-day use.
- Do not tune to the backtest.

## Ask before doing

- Anything changing `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level.
- Committing to git.
