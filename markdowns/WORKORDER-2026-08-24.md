# Work Order — 2026-08-24 (post UI review)

Successor to `WORKORDER-2026-08-16.md`, which is complete. Read `HANDOFF-draft-tool-ui.md`
for interface rationale and `SPEC-draft-tool-build.md` §12 for the model.

Ordered by priority. Every item has an acceptance check that produces a **number or a
diff** — report it rather than asserting the change works.

---

## 1. 🚨 Survival method bake-off — measure before blending

**Owner ruling:** if one method is clearly more accurate, use it. If they are close, make
availability a **50/50 blend** of the two.

**The problem with answering that today:** `backtest.py` has only ever scored the Monte
Carlo (`simulate_intervening_picks`). The lognormal (`_fit_lognormal_from_adp` /
`_survival_baseline_row`) has never been scored at all. And the MC's own calibration table
shows a directional miss precisely where decisions live:

| Predicted | Actual | n |
|---|---|---|
| 0.07 | **0.80** | 15 |
| 0.16 | **0.86** | 14 |
| 0.25 | **0.83** | 12 |
| 0.35 | **0.86** | 29 |
| 0.46 | **1.00** | 36 |
| 0.98 | 0.98 | 7255 |

The overall Brier of 0.0351 is flattered by the 7,255 of 8,096 observations sitting in the
top decile. **In the 0.1–0.6 band the model is badly overconfident that players disappear.**

**Task.** Extend `backtest.py` to score **both** methods over the identical observation set:

1. Overall Brier for each.
2. **Brier restricted to `predicted` between 0.15 and 0.85** — the decision band. This is
   the number that decides the ruling; the overall figure is close to meaningless when 90%
   of observations are foregone conclusions.
3. Calibration deciles for each, side by side.
4. Same for a 50/50 blend.

**Then apply the owner's rule:** if the decision-band Briers differ by more than ~25%, use
the better one alone. Otherwise implement the blend as
`AVAILABILITY_METHOD = "blend"` in `config.py`, with `"montecarlo"` and `"lognormal"` as
the other options.

**Carry the existing caveats.** The backtest uses an ADP *proxy* built from other seasons,
not contemporaneous ADP, so it is a weak instrument. Two seasons of one league. Scoring
changed. **Do not tune any parameter to improve these numbers** — the point is to choose
between two existing methods, not to fit either.

**Acceptance:** report all four decision-band Brier scores and state which branch of the
owner's rule fired.

---

## 2. 🚨 Extend the Monte Carlo two owner picks deep (R30)

Currently the MC simulates only to the owner's next pick, so the **wait rule runs off the
lognormal instead** — and the two disagree by better than 2× on the most consequential
number in the draft:

| Method | LaPorta survival, 53 → 68 |
|---|---|
| Lognormal (what the wait rule uses) | **21%** |
| Monte Carlo (what the board shows) | **45%** |

Both are on screen simultaneously, labelled the same way.

**Task.** Simulate through the owner's next-but-one pick so the wait rule and the board
draw on the same estimate, and so the wait number inherits substitution logic (spec §12.3).
This matters most in the 44 → 53 window, where the three most TE-hungry managers each pick
twice.

**Same code path as item 3** — do them together.

**Acceptance:** wait rule and board report the same survival for the same horizon; the
capacity invariant holds at both depths (expected departures = intervening picks, ±0.1).

---

## 3. 🚨 Reach model — managers will go ~15 picks early for their guy (R31)

**Owner ruling:** assume managers will reach up to **15 picks of ADP** to get their guy, or
someone who fits their build, tendencies, or bias at that moment.

Today the MC choice model is close to ADP-ordered, which is why scarce-position players
look more available than they are. The owner's own example: a manager reaching on LaPorta
because he will not like the tight ends left when he is next up.

**Task.** Give each intervening manager a reach distribution rather than a point:

- **Centre on that manager's observed mean reach** from `league-draft-tendencies-2026.md` —
  Dylan +4.0, Tyler +3.4, Nick −1.5, Cailen −3.8, etc. These are real per-manager numbers;
  do not use a league-wide constant.
- **Tail out to ~15 picks**, per the ruling.
- **Widen when that manager has an unfilled need at a thinning position.** This is the
  mechanism that produces the LaPorta case and it is the whole point of the item.

Expected effect: survival **falls** for scarce-position players, which is the correction the
TE squeeze needs. That is the sign to check.

**Acceptance:** report LaPorta's survival 44 → 53 before and after. It should fall. Capacity
invariant must not regress.

---

## 4. Draft setup screen (R32)

**Owner ruling:** set the pick and the configuration when loading the tool.

**No pipeline rebuild is needed for any of this.** `player_master.csv` is slot-independent;
the draft slot only affects pick numbers and availability, both computed at render. Say so
in the UI so nobody re-runs the build unnecessarily.

Setup screen writes `state/draft_setup.json`:

- **Draft order** — 12 reorderable names, replacing the hardcoded `DRAFT_ORDER_2026`
- **Owner name** — which slot is mine
- **Roster targets** per position
- **Reference ADP source** and whether it is Sleeper (gates live polling)
- **Layer toggles** — see item 5
- **TE1 fork player** — replaces the hardcoded `"Sam LaPorta"` in `main_cockpit.py`
  (owner chose the setup-screen option over a config constant)

**Acceptance:** changing the owner slot updates every pick number and all availability with
no rebuild; a fresh `state/` directory boots to the setup screen rather than crashing.

---

## 5. Layer toggles: one source of truth, surfaced in the UI (R33)

**Owner ruling:** toggles should live where the app is launched, for easy setup.

**Do not duplicate `LAYERS` into `main_cockpit.py`.** `build/pipeline.py` reads it too, and
two copies will drift. Instead:

1. Hoist `LAYERS` to the very top of `config.py` under a loud header, as the defaults.
2. The setup screen writes per-session overrides into `state/draft_setup.json`.
3. **Add the missing layers indicator** — handoff §3.2 promised it and spec §4.2
   requirement 2 mandates it: *"the UI states which layers are off, at all times."*
   `grep -c layers app/main_cockpit.py` currently returns 0. A rail footer plus a line on
   the setup screen satisfies it.

Remember `manager_priors`, `nfl_team_bias` and `college_bias` **fail together** in any other
league, and the generic-ADP fallback must engage and be visible (spec §4.2).

**Acceptance:** `grep -c layers app/main_cockpit.py` > 0; disabling a layer changes both the
board and the indicator; `config.LAYERS` remains the only definition.

---

## 6. UI fidelity to the design (R34)

Push the Streamlit build closer to `Draft_Cockpit_v2_dc.html`. Already achievable because
`cockpit_html.py` hand-renders and hands off to `st.markdown` — extend that pattern.

What resists and is not worth fighting: Streamlit's own header and block padding, and the
styling of native widgets (`st.radio`, `st.multiselect`, `st.dataframe`). Replace those with
HTML equivalents where the design calls for it; leave `st.dataframe` on the Full board tab,
where native click-to-sort is worth more than pixel fidelity (handoff U21, §9).

**Do not migrate to a hosted HTML frontend before the draft.** Revisit in the offseason —
`draft_engine.py` has no Streamlit dependency, so that door stays open.

**Acceptance:** cockpit renders with no native widget visible in the top two bands; full
suite green; `AppTest` boots clean.

---

## 7. Kickers (R35)

The slide already assumes round 14 and works — 82 rows shift, max −7, consistent with the
seven kickers at Sleeper ranks 128–153.

**Remaining gap:** `player_master.csv` has **no K rows at all** (R22 dropped them), so the
tool cannot recommend a kicker at 188. Add K rows sourced from the Sleeper file with ADP
only and no projections, excluded from VORP and from every route.

**Acceptance:** exactly one K appears as a suggestion at pick 188 and none before round 14.

---

## 8. Streamlit Community Cloud — not for draft day

The filesystem is **ephemeral**, so `state/` is lost on restart and a mid-draft restart
loses the draft. The repo is public, so the data would be public too. Run locally on draft
day; tunnel it if phone access is wanted. Cloud is fine for post-draft viewing.

---

## 9. Housekeeping carried forward

- **`.gitignore`** still absent; `__pycache__` and `.pyc` still committed.
- **Delete `state/draft_state.json`** — the old-format standard-scoring test draft. Inert
  now, confusing next August.
- **Kicker slide count:** handoff §4 says 62 players re-ranked; actual is 82. Fix the
  number **and** have the pipeline emit the count so it cannot drift again (owner chose
  both).

---

## Do not do

- Do not tune anything to the backtest.
- Do not duplicate `LAYERS`.
- Do not raise the ±10 intel cap or expose it in the UI.
- Do not hard-code the ADP source offsets or the kicker ranks.
- Do not touch `scripts/`.

## Ask before doing

- Anything changing `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level (R16/R17/R22).
- Committing to git.
