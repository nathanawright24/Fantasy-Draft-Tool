# Build Spec — 2026 Live Draft Tool

**Owner:** Nathan (`nathanawright24`) — draft slot **5 of 12**
**League:** "We Made A League Mr. Stark" — 12-team snake redraft, 16 rounds, full PPR
**Target environment:** Positron + Claude Code, local repo, Python + Streamlit
**Runway:** 2–3 weeks from 2026-08-16

This file is the authoritative build spec. It supersedes conflicting guidance in the four
handoff/guardrail markdowns *only* where §2 (Resolved Decisions) says so. Everywhere else,
those documents remain in force — especially their data-integrity rules, which are
non-negotiable and listed again in §5.

---

## 1. Why local, not a chat artifact

Three reasons, recorded so this doesn't get relitigated:

1. **The data layer is the hard part.** ~15+ CSVs across four positions plus O-line, joined on
   `name + position + nfl_team`, where `nfl_team` is blank in *every* factor-grid file and must
   be backfilled from the props files first. That is an iterative build-and-verify loop with
   printed unmatched-name lists.
2. **`api.sleeper.app` is blocked in the Claude sandbox but not on Nathan's machine.** Local
   execution is the only way to test live ingestion at all.
3. **Server-side Python polling has no CORS layer**, which retires open item §9.1 of
   `HANDOFF-draft-tool-context.md` outright. This is the deciding argument for Streamlit over a
   single self-contained HTML file.

Corollary: a bug hit at pick 40 must be fixable in under a minute. Favour boring, debuggable
code over clever code throughout.

---

## 2. Resolved decisions

Settled in the scoping conversation. These override the open items they correspond to.

| # | Question | Resolution | Supersedes |
|---|---|---|---|
| R1 | Build environment | Local Streamlit app, Python | — |
| R2 | Live pick ingestion | Sleeper API polling, manual entry always available as fallback | draft-tool §9.1 |
| R3 | Draft order | Dictionary, edited annually (see §3) | draft-tool §9.3 |
| R4 | Sleeper rankings | Sleeper ADP *is* Sleeper rankings — one input. Paste-to-CSV if API fails | draft-tool §9.2 |
| R5 | NFFC ADP | Owner re-scrapes morning of draft; tool ingests a CSV | — |
| R6 | Missing per-game bonuses | Model per-player from projected volume, calibrated to the archetype table (§6.2) | props §9.1 |
| R7 | Output shape | Composite score, sortable, with the contributing layers on expand | — |
| R8 | QB1 timing | **Pace-driven, not calendar-driven.** Tool sets the window from observed QB pace | GUARDRAILS §5.2, §7.1 |
| R9 | Rd 1–2 RB shape | No default. Board decides | GUARDRAILS §7.2 |
| R10 | TE plan | LaPorta at pick 53 if he's there. **If gone: no early TE at all** — punt to a Kincaid + Andrews tier double-up in Rd 8–11 | GUARDRAILS §5.3, §7.3 |
| R11 | QB2 | Confirmed as the designated sacrifice if a Rd 11–15 RB dart is too good to pass | GUARDRAILS §7.5 |
| R12 | Late RB darts | 2 or 3, hard ceiling of 3 | GUARDRAILS §7.4 |
| R13 | College bias | Build the player→college mapping. **Label every row as model recall, not data** | draft-tool §9.5 |
| R14 | Portability | Every analysis layer toggleable via `available` / `applies` flag pair (§4.2). Composite renormalizes; warnings self-disable | — |
| R15 | Reference ADP | Selectable source, not Sleeper-assumed (§4.3). `is_sleeper=False` disables live polling | R4 |
| R16 | Composite unit | **Value over replacement in PPR points**, replacing within-position percentile entirely (§12.1) | §6.5 |
| R17 | Replacement level | Last starter at each position, flex-aware | — |
| R18 | Bonus layer | Separate weight, but **in points, not percentile** | §6.2 |
| R19 | Survival curve | Unbounded lognormal fit from the NFFC triple, min/max as extreme quantiles of *n* draws (§12.2) | §4.4 |
| R20 | Capacity | **Monte Carlo the intervening picks discretely** (§12.3) | §7 |
| R21 | Hazard combination | Per-pick Bernoulli — subsumed by R20's simulation step | §7 |
| R22 | Kickers | Dropped from `ROSTER_TARGET`. Take one by feel at 188 | §8 |
| R23 | Raw inputs | Committed to git — clean clone must test green | — |
| R24 | Sequencing | Pipeline correctness (R16–R21) **before any UI work** | §10 |
| R25 | Backtest | Replay 2024 + 2025 pick-by-pick and score availability calibration (§12.5) | — |
| R26 | Survival anchor | **Centre on Sleeper `reference_adp_rank`; NFFC `min/max/n` for dispersion only.** Sleeper is the draft population | R19, §7 |
| R27 | Source offsets | Computed at build time into `adp_source_offsets.csv`, never hard-coded | §4.4 |
| R28 | Intel layer | `2026/PLAYER-INTEL-<year>.md` → parsed. `target`/`fade` capped at ±10 VOR; `hard_avoid` is a filter | — |
| R29 | Join severity | Unmatched top-150 ADP player = **hard failure**, not a note | §5.3 |
| R30 | MC depth | Simulate **two owner picks deep** so the wait rule and board share one estimate | R20 |
| R31 | Reach model | Managers reach up to **15 picks** of ADP; centre on each manager's observed mean, widen on unfilled need | §7 |
| R32 | Draft setup | Setup screen writes `state/draft_setup.json` (order, owner slot, targets, ADP source, toggles, TE1 fork player). **No rebuild needed** | R3 |
| R33 | Toggle location | `config.LAYERS` stays the only definition; setup screen writes overrides; UI must show which layers are off | R14, §4.2 |
| R34 | UI fidelity | Match `Draft_Cockpit_v2_dc.html` within Streamlit. **No hosted frontend before the draft**; no Community Cloud (ephemeral state) | — |
| R35 | Kickers | Round 14 floor via the slide; add K rows with ADP only, excluded from VORP and routes | R22 |
| R36 | Availability method | Score lognormal vs Monte Carlo on the **decision band (0.15–0.85)**; >25% apart → use the better one, else 50/50 blend | R19, R20 |

---

## 3. Draft order and the slot-5 pick structure

```python
DRAFT_ORDER_2026 = [
    "Dylan", "Cailen", "Tyler", "Nick", "Nathan", "Asa",
    "Greg", "Ryan", "Jayden", "Colin", "Joseph", "Kaiden",
]
OWNER = "Nathan"   # slot 5
```

Annual maintenance is editing that list. Everything downstream derives from it.

### Nathan's picks

| Rd | Pick | Wait | Rd | Pick | Wait |
|---|---|---|---|---|---|
| 1 | 5 | 14 | 9 | 101 | 14 |
| 2 | 20 | 8 | 10 | 116 | 8 |
| 3 | 29 | 14 | 11 | 125 | 14 |
| 4 | 44 | 8 | 12 | 140 | 8 |
| 5 | 53 | 14 | 13 | 149 | 14 |
| 6 | 68 | 8 | 14 | 164 | 8 |
| 7 | 77 | 14 | 15 | 173 | 14 |
| 8 | 92 | 8 | 16 | 188 | — |

### 🚨 The fixed-window property — most useful fact in the whole model

From slot 5 the intervening managers are **always the same two groups**, all sixteen rounds:

- **Short waits (8 picks)** — from every **even-round** pick (20, 44, 68, 92, 116, 140, 164) into
  the next odd round. The intervening managers are the four to Nathan's **left**: Nick, Tyler,
  Cailen, Dylan — each exactly twice.
- **Long waits (14 picks)** — from every **odd-round** pick (5, 29, 53, 77, 101, 125, 149, 173)
  into the next even round. The intervening managers are the seven to Nathan's **right**: Asa,
  Greg, Ryan, Jayden, Colin, Joseph, Kaiden — each exactly twice.

Invariant to assert in code: for each consecutive pair of owner picks, the intervening manager
multiset equals one of exactly those two sets. Derive it programmatically from
`DRAFT_ORDER_2026`; never hand-transcribe it.

Assert this in a unit test. If the assertion fails, the snake logic is wrong.

**The TE consequence, already known:** the short-wait group contains the league's three most
TE-hungry managers — Nick (TE1 avg round **3.8**, earliest in the league), Dylan (**4.75**,
stacks 2–3 TEs every single year), Cailen (**5.5**) — each picking twice in the 44→53 window.
This is why R10 exists. Quantify LaPorta's survival probability rather than assuming it.

---

## 4. Folder layout and configuration

### 4.1 Layout — conforms to the owner's existing structure

The analysis folder already exists and is organized. The tool slots into it; nothing gets
reorganized.

```
Documents/Analysis/Fantasy/        <- Claude Code workspace root
  CLAUDE.md                        <- entry-point index (see below)
  2026/                            <- player data, all positions + O-line.  READ-ONLY
  drafts/                          <- historical draft boards.              READ-ONLY
  markdowns/                       <- handoffs + this spec.  Canonical home for docs
  Templates/                       <- CSV templates for future years.       READ-ONLY
  scripts/                         <- existing image-parsing pipeline.      READ-ONLY
  Tool/                            <- everything built here.  git init HERE
    config.py                      <- league config + feature toggles (§4.2)
    build/
      01_load_props.py
      02_load_factor_grids.py
      03_backfill_teams.py
      04_bonus_model.py
      05_join_adp.py
      06_college_map.py
      07_build_master.py
      validate.py
    app/
      main.py                      <- Streamlit entry
      availability.py
      recommender.py
      guardrails.py
      sleeper_client.py
      state.py
    data/
      external/                    <- reference_adp.csv, refreshed near draft day
      derived/
        player_master.csv          <- THE single joined table the app reads
        join_report.txt            <- unmatched names, every run, never suppressed
    state/                         <- live draft state, JSON, written every pick
    tests/
```

**Launch Claude Code from `Fantasy/`, not from `Tool/`.** It needs to read `2026/`, `markdowns/`
and `scripts/` to do the build, and it only auto-loads `CLAUDE.md` from its starting directory.
Open `Fantasy/` as the Positron workspace for the same reason.

**`git init` inside `Tool/` only.** The data folders are static inputs and don't need versioning;
the code is what you'll be changing under a 1.5-minute clock.

Three rules about this layout:

1. **Nothing outside `Tool/` is ever written to.** `2026/`, `drafts/`, `Templates/` and
   `scripts/` are inputs. If a value needs correcting, correct it in the build layer and record
   why in `join_report.txt`.
2. **Do not re-implement `scripts/`.** The image-parsing pipeline already ran; the build layer
   consumes its CSV output. Touch it again only if new screenshots arrive.
3. **The app reads only `player_master.csv` and live draft state.** No app-layer code reaches
   into `2026/`. That separation is what makes a mid-draft fix safe.

**Windows note:** use `pathlib` throughout, never string-concatenated paths. Resolve the root
once in `config.py` — `FANTASY_ROOT = Path(__file__).resolve().parent.parent` — and derive
everything from it, so the whole thing survives being moved or run from a different working
directory.

### 4.2 Feature toggles — the portability layer

The tool must run in Nathan's other leagues and in future years where some inputs don't exist.
Every analysis layer is therefore switchable from a single config, with **two independent flags
per layer**:

- `available` — does this data exist for this season?
- `applies` — is it meaningful for this league?

A layer is used only when **both** are true. Keeping them separate is the whole point: a year
from now, `available=False` tells you the data was missing, `applies=False` tells you the league
didn't need it. Collapsing them into one boolean destroys that distinction permanently.

```python
LAYERS = {
    "implied_props":     {"available": True,  "applies": True},
    "factor_grids":      {"available": True,  "applies": True},
    "bonus_model":       {"available": True,  "applies": True},   # off if no per-game bonuses
    "oline_rankings":    {"available": True,  "applies": True},   # context only, never a multiplier
    "manager_priors":    {"available": True,  "applies": True},   # THESE twelve people only
    "nfl_team_bias":     {"available": True,  "applies": True},
    "college_bias":      {"available": True,  "applies": True},   # model recall, not data
    "archetype_priors":  {"available": True,  "applies": True},   # WR/RB only; TE/QB have none
}
```

**Required behaviours, all three non-optional:**

1. **Composite weights renormalize over enabled layers.** Turning a layer off must not silently
   leave its weight unallocated or quietly hand it to a neighbour. Renormalize and show it.
2. **The UI states which layers are off, at all times.** A visible strip, not a settings page.
   A composite that looks identical with three layers disabled is a trap.
3. **Every warning rule declares its layer dependencies and self-disables.** W11 depends on
   `factor_grids`; with grids off it must go dormant, not evaluate against nulls. Same pattern
   for every rule in §8.

### 🚨 `manager_priors` and the bias layers fail together

The twelve behavioral profiles are twelve specific people, and the NFL-team and college bias
discounts are derived from *their* stated allegiances. In any other league all of
`manager_priors`, `nfl_team_bias` and `college_bias` go to `applies=False` simultaneously.

**The availability model must then fall back to a generic ADP-based survival curve with widened
variance**, and say so on screen. This is the single most important toggle interaction in the
tool: without the fallback, the model will produce confidently-shaped probabilities about
strangers whose tendencies it knows nothing about. Build the fallback path at the same time as
the primary path, not later.

Also note `archetype_priors` is **position-partial even in this league** — the analyst produced
back-tests for WR and RB only. TE and QB have no probabilities at all and their `p_*` columns are
deliberately blank. The layer must handle "enabled but absent for this position" without
imputing by analogy.

### 4.3 Reference ADP is a selectable source

Sleeper ADP and Sleeper rankings are the same input, and the tool must not assume Sleeper.

```python
REFERENCE_ADP = {
    "source_name": "Sleeper",     # free text, appears in the UI
    "is_sleeper": True,           # True enables API ingestion + live pick polling
    "csv_path": None,             # used when is_sleeper is False
}
COMPARISON_ADP = {"source_name": "NFFC", "csv_path": "data/external/nffc_adp.csv"}
```

When `is_sleeper=False`: ingest reference ADP from a pasted-and-converted CSV, and **live pick
polling is unavailable — manual entry becomes the only path.** The tool should say that at
startup rather than failing at pick one.

Two downstream effects worth stating, because they're easy to miss:

- **Reference ADP feeds two different things** — the value comparison *and* the baseline of the
  availability model. Switching sources moves both. Any divergence column
  (`reference_adp − comparison_adp`) is only meaningful when both sources are dated, so carry an
  `as_of` on each.
- **A different site means a different draft population.** NFFC ADP reflects high-stakes
  drafters; Sleeper reflects casual ones. Neither is wrong, but the availability model inherits
  whichever population the reference source came from, which is a second reason the
  `manager_priors` fallback needs widened variance.

### 4.4 ADP ingestion contract

Two real 2026 exports were inspected (NFFC `ADP.tsv`, 434 rows; `sleeper_adp_ppr_2026-08-16.csv`,
248 rows). They differ in **name format, team codes, position codes, and the meaning of the ADP
number itself.** Both normalize into one canonical schema:

```
player_key, player_display, first, last, suffix, position, nfl_team,
source, adp_value, adp_rank, adp_min, adp_max, adp_n, as_of
```

#### Name format — the largest difference

NFFC is `Last, First` **with the suffix attached to the surname half**; Sleeper is `First Last Suffix`.

| NFFC | Sleeper |
|---|---|
| `Gibbs, Jahmyr` | `Jahmyr Gibbs` |
| `St. Brown, Amon-Ra` | `Amon-Ra St. Brown` |
| `Walker III, Kenneth` | `Kenneth Walker III` |
| `Etienne Jr., Travis` | `Travis Etienne` |
| `Pitts Sr., Kyle` | `Kyle Pitts` |

Parse NFFC by splitting on `", "` → `(surname_part, given_part)`, then extracting the suffix from
`surname_part`. **Match suffixes against a whitelist of whole tokens** (`Jr`, `Sr`, `II`, `III`,
`IV`) — never by stripping periods, because `St. Brown` contains a period that is not a suffix.

#### 🚨 Suffix presence is inconsistent *between* sources for the same player

Verified, not hypothetical: NFFC carries `Etienne Jr.`, `Pitts Sr.`, `Jones Sr.`; Sleeper lists
those same three as `Travis Etienne`, `Kyle Pitts`, `Aaron Jones`. Any join that preserves
suffixes fails on all three. Suffix-stripped normalization is **mandatory**, and the stripped
suffix is retained in its own column for display only.

#### Join key — refinement of the §5 rule

The `name + position + nfl_team` rule exists because of abbreviated names (`B. Robinson`). **These
exports carry full first names, so `normalized_name + position` is a safe key for them**, and team
should *not* be part of the key. Reason: sources disagree on 2026 team assignments (the props
handoff documents Waddle as Denver per Clay and Miami per an August article). Making team part of
the key converts a known disagreement into a silent join failure.

**So: full-name sources join on `normalized_name + position`, with team disagreement recorded as a
conflict flag. Abbreviated sources — notably the Sleeper draft-board HTML — still require team in
the key.** Two different rules for two different situations; do not unify them.

#### Team codes — a three-way mapping, not two

The spec previously noted only Clay's convention. There are three:

| Canonical | NFFC | Sleeper | Clay/ESPN |
|---|---|---|---|
| ARI | **ARZ** | ARI | **ARZ** |
| BAL | BAL | BAL | **BLT** |
| CLE | CLE | CLE | **CLV** |
| HOU | HOU | HOU | **HST** |
| JAX | JAX | **JAC** | JAX |
| LAR | **LA** | LAR | LAR |
| LV | LV | **LVR** | LV |
| FA | FA | **UNS** | — |

All others are conventional. **Define one canonical set, map all three sources into it, and assert
zero unmapped codes on every build.** `LA` → `LAR` is the one most likely to silently mis-join,
since `LA` is a valid-looking prefix of both `LAR` and `LAC`.

#### Position codes and filtering

NFFC uses `TK` (kicker) and `TDSP` (team defense); Sleeper uses `K` and `DEF`. Map to canonical
`K` / `DEF`, then **drop `DEF` entirely** — this league dropped team defenses after 2023. Keep
`K`: one kicker slot, Rd 15–16.

#### 🚨 The ADP numbers are not the same kind of number

- **NFFC `ADP` is a continuous mean overall pick** — Gibbs 1.24, Bijan 1.94, Chase 2.90.
- **Sleeper `ADP` is a sequential integer rank** — 1, 2, 3 … 248.

Subtracting one from the other is comparing a mean-pick estimate against an ordinal. Near the top
they look similar and diverge structurally further down.

**Rule: compute all divergence in rank space** (rank NFFC by `adp_value` ascending), and keep
NFFC's raw mean pick separately, since that — not the rank — is what the availability model wants.

#### NFFC `Min Pick` / `Max Pick` / `# Picks` are the most valuable columns in either file

They are an **empirically observed range across 51 real drafts**, which is strictly better than a
modelled spread. Uses:

- Feed the availability model a **triangular or beta-PERT distribution** from
  (`adp_min`, `adp_value`, `adp_max`) rather than assuming a shape. If Nathan's next pick is past
  `adp_max`, survival probability is genuinely near zero — that player has never once lasted that
  long in 51 drafts.
- Treat a wide `adp_max − adp_min` as a volatility flag: contested or uncertain players.

**`# Picks` is a mandatory quality gate.** It varies from 51 down to single digits — Trevor
Etienne shows `adp_n = 6` with a range of 214–327, i.e. essentially noise. Set a minimum `adp_n`
below which NFFC ADP is displayed but not used in the survival math, and surface the count.

#### Vintage and structure

- **Sleeper carries `Date Pulled` natively.** Use it as `as_of`.
- **NFFC carries no date.** Capture it at ingestion; the divergence column is meaningless unless
  both sources are dated.
- **NFFC has two columns both named `Team`** (index 4 = NFL team, index 11 = drafting fantasy
  team, blank in an aggregate export). Select by **index, not by name** — pandas silently renames
  the second to `Team.1`.
- **Coverage is asymmetric:** ~368 skill players in NFFC against ~211 in Sleeper. Expect
  NFFC-only rows deep in the pool. That is coverage, not error — but it means the reference source
  determines how deep the board goes, which matters at picks 173 and 188.

---

## 5. Data-integrity rules carried forward — do not soften

From the handoffs, restated because violating any of these has already corrupted an analysis
once:

1. **Key on `name + position + nfl_team`.** Abbreviated names collide — "B. Robinson" matched
   Bijan Robinson to Brian Robinson and produced a bogus 87-pick reach.
2. **`nfl_team` is blank in every factor-grid file.** Backfill from the props files *before* any
   other join. Never fill from model recall.
3. **Normalize names** by stripping `Jr./Sr./II/III/IV`, lowercasing, dropping periods and
   apostrophes, collapsing hyphens to spaces. Then **print every unmatched name** to
   `join_report.txt`. Silent join failure is the primary correctness risk in this project.
4. **Clay/ESPN uses non-standard team abbreviations:** `ARZ BLT CLV HST JAX LV`. Mapping table
   required.
5. **`factor_score` is ordinal.** Never call it points, never blend it with a `ppr_*` column,
   never compare it across positions (different cell counts, different scales).
6. **Use `factor_score_recomputed`, not `factor_score_published`.** 13 source arithmetic errors
   were found; published figures skew high.
7. **Archetype probabilities are not player-specific** (`prob_is_player_specific = N`) and the
   outcome labels **do not partition** — 9–20% is unaccounted for in every column. Never
   normalise to 1.
8. **Do not multiply projections by the O-line blend.** Double-counts Clay's own O-line view,
   and the rushing-efficiency gradient across tiers is flat (4.39/4.45/4.38/4.32). Context only.
9. **Do not blend `factor_score` with `ppr_*` as if independent.** Volume factors are derived
   from projections; team pass attempts, offensive PPG rank and O-line rank all overlap the
   props files. Incremental signal lives in the *efficiency and role* factors, the archetype,
   and the injury flag.
10. **`mkt_*` and `proj_*` never mix.** Compare `ppr_market` to `ppr_*_propadj`, never to the
    raw 17-game figure.
11. **Bust rates past Rd 9 are undefined, not zero.** Never render a 0.0% cell as safety.

---

## 6. Valuation model

Four layers. The composite is the headline; every layer stays inspectable per R7.

### 6.1 Base — `ppr_base`
From `{pos}_implied_props.csv`, prop-adjusted (`ppr_proj_propadj`). Prefer `ppr_market` where
market coverage exists, but **never rank market-sourced against projection-sourced players**
without the games-played reconciliation already applied.

### 6.2 Bonus layer — `bonus_est_ppr`
The two new-for-2026 per-game bonuses (+3 at 300 pass yds, +3 at 100 scrimmage yds) are absent
from every `ppr_*` column and are not recoverable from season totals. Per R6:

- Fit a per-game distribution (lognormal, **one documented dispersion parameter per position**)
  to each player's projected per-game yards.
- Compute `P(≥100 scrimmage)` and `P(≥300 pass)` per game, multiply by `games_projected`.
- **Calibrate against the archetype table in `HANDOFF-draft-tool-context.md` §1**, treating it
  as the target rather than the output: an alpha WR should land near 6× 100-yard games, a
  workhorse dual-role RB near 10×. If it doesn't, the dispersion parameter is wrong.
- Expose dispersion as a slider. Dialling it to zero must visibly change rankings — if it
  doesn't, the layer isn't doing anything and that's a bug.

Rationale for choosing this over the flat archetype adder: a flat adder is constant within
archetype and therefore does **zero ranking work inside a position**. This version is monotone
in volume and falsifiable against numbers we already hold.

Keep `ppr_base` untouched. `bonus_est_ppr` is additive and separately displayed.

### 6.3 Factor layer — `factor_score_recomputed`, `p_boomed`, `p_busted`, `p_got_injured`
Enters as a **variance and divergence term, never as points.** Uses:
- `adp_minus_score_rank` as a divergence flag (positive = factors like him more than the market).
- Archetype probabilities as distribution shape, weighting injury **more heavily than the source
  does** — Underdog is best-ball with auto-optimised lineups; this league has no such safety net.
- TE and QB have **no back-test and therefore no `p_*` values**. Leave blank. Do not impute by
  analogy.

### 6.4 Market layer — `nffc_adp`, `sleeper_adp`
Two independent market reads. Surface `nffc_adp − sleeper_adp` as its own divergence column;
where two markets disagree, that's opportunity or a stale scrape, and the tool should say which
it can't distinguish.

### 6.5 Composite
Weighted, with **weights in a single editable config dict**, not scattered through the code.
Default weighting should be conservative and the UI must show the contribution of each layer on
expand. A composite nobody can decompose at pick 53 with 90 seconds on the clock is worthless.

---

## 7. Availability model

For each available player, `P(still there at my next pick)`.

**Method:** for each manager picking between now and Nathan's next turn, estimate
`P(takes a player of this type)` from their positional-appetite-by-round priors
(`league-draft-tendencies-2026.md` §2 and the per-manager profiles), then combine.

Required behaviours:

- **Exclude Nathan's own priors.** He is not competing with himself.
- **Use the fixed-window property (§3)** — the intervening manager set is deterministic, which
  makes this materially more precise than a generic ADP-based survival curve.
- **Widen intervals for low-confidence and bimodal managers.** Tyler is a single sample. Colin
  inverts himself (main league TE 2.8 / QB round 9; alt league QB round 3 / TE round 7) and must
  be modelled as bimodal, not averaged into a false middle. Joseph is the least predictable in
  the dataset — drafted zero QBs in seventeen rounds in 2022.
- **Down-weight alt-league timing.** Asa, Jayden and Colin each have one draft in a different
  league. Treat it as evidence of *preference and willingness*, not calibrated timing.
- **Apply NFL-team bias discounts** (`league-draft-tendencies` §6 / draft-tool §6): discount
  survival for CHI, DEN, NYG, JAX players — two Bears fans, two Broncos fans, two Giants fans in
  a twelve-man league. Inflate survival for MIN, ATL, PIT.
- **Apply college bias** per R13, from the model-recall mapping, **flagged as recall on every
  row.** Four members have Ohio State ties (Nathan, Ryan, Tyler) plus Alabama / Illinois /
  Iowa State leans (Nick, Greg) and Florida / FSU / UCF (Kaiden, Cailen, Jayden).
- **Re-estimate from live picks.** After ~20 selections, observed pace beats any prior. This is
  the mechanism behind R8.
- **Honour the layer toggles (§4.2).** With `manager_priors` off, fall back to a generic
  ADP-based survival curve with widened variance and display that state. The fallback is a
  first-class path, not an error branch.

### 🚨 The historical priors describe a scoring system that no longer exists

Every historical draft predates the 5-point passing TD and both bonuses. The priors remain the
best evidence about these twelve people's *habits and temperaments*; historical ADP is **not** a
valid 2026 positional-value baseline. Two opposing forces to keep visible rather than resolve:
the widening QB1-to-QB12 spread pushes QBs earlier, while the bonus double-dip compresses the
middle of the QB range and favours the cheap mid-round arm. Do not hard-code a resolution — R8
exists precisely because live pace is the better arbiter.

---

## 8. Decision tree / recommender

Given roster state and remaining picks, surface viable build paths against the roster target of
**6 WR / 5 RB / 2 TE / 2 QB / 1 K = exactly 16 picks, no slack.** Every deviation displaces
something nameable, and the recommender must name it.

### Branch structure

The tree's most important fork is at **pick 53**:

- **LaPorta available →** take him. TE2 follows in the Rd 10–11 window (late edge, per
  GUARDRAILS §5.3).
- **LaPorta gone → no early TE at all** (R10). Both TEs come from the Kincaid + Andrews tier in
  Rd 8–11.

**Flag the congestion in the second branch.** Rd 7–11 is picks 77, 92, 101, 116, 125 — five
picks that must absorb QB1, RB3, TE1, TE2 *and* any WR depth. GUARDRAILS §5.1 located the
squeeze at Rd 11–15; on this branch it moves up to Rd 7–11. The tool should track that band's
commitment count explicitly and warn when it becomes infeasible.

**Also known and worth encoding:** picks 53 and 68 both land inside the ADP ~48–72 deadzone,
where every WR projects 11.7–13.3 PPG and every RB 11.1–12.4. The band is flat. Two implications
the recommender should state out loud: a reach there is cheap (the 8th option ≈ the 1st), and a
flat flex band is the cheapest possible moment to spend a pick on QB — the position that gained
most from the new scoring. Under R8, W3 must evaluate against live pace rather than the round
number.

### Warning rules

Port W1–W15 from `GUARDRAILS-macro-roster-construction.md` §4. **Enforcement is loud warnings,
always overridable. No hard blocks anywhere.** Amendments:

| Rule | Change |
|---|---|
| W3 (QB before Rd 7) | Now **pace-driven**. Fires only if live QB pace suggests the pick is genuinely early. Informational, never a scold |
| W4 (no QB entering Rd 10) | May fire **earlier** if observed pace runs hot |
| W6 (TE Rd 2–4) | Unchanged, but under R10 the practical rule is: no TE before Rd 8 *unless* it's LaPorta at 53 |
| W10 (position caps) | Must name the displaced slot, not just report the overage |
| W13 (no QB2 by Rd 15) | Downgrade to Low — QB2 is the designated sacrifice per R11 |
| W15 (RB dart before Rd 11) | Unchanged; hard ceiling of 3 darts per R12 |

**New rules:**

| # | Trigger | Warning | Severity |
|---|---|---|---|
| W16 | TE1 not rostered entering Rd 8 on the LaPorta-miss branch | Kincaid tier is the plan; backup TEs fill from Rd 11 | Medium |
| W17 | QB1 or TE1 tracking later than Nathan's own historical drift | **Drift check.** QB1 has gone 4→5→9→10 and TE1 4→4→8→10 across four years; 2025 entered Rd 9 with neither. This is the documented failure mode | **High** |
| W18 | Rd 7–11 commitment count exceeds remaining picks in the band | Congestion. Name which commitment gets cut | **High** |

W17 is unusual for a draft tool and is deliberate. The behavioural analysis identifies drift at
premium positions as this owner's specific, repeated, four-year pattern, and identifies him as
the manager most exposed by the 2026 rule changes. A tool that models eleven opponents and not
its own user would be missing the largest single predictable error in the room.

### Recalibration before consuming third-party strategy data

Per GUARDRAILS §6, and required before any of these numbers are displayed:

1. **The league-winner bar isn't this league's bar.** `rb_leaguewinner_bar_by_pick.csv` assumes
   9.5 PPG replacement, 4.83 pts/win, 16 games. Two flex spots raise replacement level and
   therefore every required-PPG figure. Recompute, which also moves every `value` figure in
   `legendary_rb_candidates_2026.csv`.
2. **A half-PPR caveat is embedded in the WR strategy.** In full PPR the WR-in-flex case is
   stronger, which supports both-flex-WR.
3. **QB and TE league-winner rows are small-n with no published denominators.** The 33.3% QB
   Rd 5–6 figure must not drive anything.
4. **Weight receiving work heavily in late RB darts.** Two independent findings converge:
   targets per game ranked first in the legendary-season regression, and the 100-scrimmage
   bonus lets a pass-catching back reach the threshold through rushing *and* receiving
   (+30 vs +21). This sharpens "independent backs with standalone paths" into standalone
   *receiving* role — a checkable criterion.
5. **The analyst contradicts himself on O-line** (regression p=0.52 vs a full weighted grid
   cell). Lean toward the regression; do not let O-line enter twice.

---

## 9. Live pick entry

- **Primary (only when `REFERENCE_ADP["is_sleeper"]` is True):** poll `api.sleeper.app`.
  Server-side, so no CORS. Test on draft day *and* at least once during the dry run. When drafting
  elsewhere, say so at startup rather than failing at pick one.
- **Fallback:** manual entry tab, always visible, never gated behind an API-failure state.
  Typing a pick must take under five seconds — autocomplete on the available-player list.
- **Persistence:** write full draft state to `state/` as JSON after every pick. A crash at
  pick 90 must not lose the draft.
- **Undo.** Mis-entries will happen at 1.5 minutes per pick.
- Every recalculation (availability, composite, warnings, tree) re-runs on each pick.

---

## 10. Build order

Two–three weeks is comfortable. Suggested sequencing:

**⚠ Superseded for the current work block by §12.** The steps below describe the original
build, which is complete. The next block is §12.1 → §12.2 → §12.3 → §12.4 → §12.5, in that order,
with **no UI work until the backtest passes (R24)**. Steps 7 and 8 below still apply afterward.

0. **Day 1 — `config.py` first.** League config, layer toggles, reference-ADP selector, path
   resolution. Everything downstream reads from it, so writing it last means retrofitting.
1. **Days 1–3 — data layer.** Load props + factor grids, backfill `nfl_team`, normalize and
   join, produce `player_master.csv` with a clean `join_report.txt`. **Do not proceed until the
   unmatched-name list has been eyeballed and is empty or explained.**
2. **Days 3–5 — bonus model.** Fit, calibrate against the archetype table, verify the slider
   moves rankings.
3. **Days 5–7 — availability model**, plus the §3 fixed-window unit test **and the generic-ADP
   fallback path built alongside the primary path**, not after it.
4. **Days 7–10 — Streamlit shell**: board, composite with expandable layers, manual pick entry,
   state persistence.
5. **Days 10–12 — guardrails and recommender**, including W16–W18 and the pick-53 fork.
6. **Days 12–14 — Sleeper API integration** and the college mapping.
7. **One full dry-run mock draft against the tool before draft day.** Non-negotiable. Budget an
   evening. The failure modes that matter only appear under time pressure.
8. **Draft morning:** re-scrape NFFC ADP, refresh reference ADP, re-run the build layer, confirm
   `join_report.txt` is clean and that no team or position code came through unmapped (§4.4).

---

## 11. Open items still unresolved

Carried from the handoffs, none blocking:

1. **Analyst identity and date** — unknown for all four factor-grid sets and the strategy set.
   Every file has fields waiting.
2. **QB Rushing TDs threshold** — printed as per-game 0.32 where the paced value is 5.36. Which
   was applied?
3. **RB Yards per Touch** — 5.82 in the grids, 5.67 in the reference table.
4. **TE/QB colour tiers** — five tiers, no legend, not a function of the score.
5. **Is `Prime Multi Time RB1` a subset of the prime cohort?** Five of the top fifteen RBs are
   assigned to a tier with no back-test. Currently mapped with `archetype_cohort_mapping =
   ASSUMED`.
6. **Volume basis per position** — per-game vs paced should be stated, not inferred from
   magnitude.
7. **Three unidentified 2024 managers** — "Team 3" (slot 2), "Team 7" (slot 4), "Team 8"
   (slot 12). Identifying them upgrades three profiles from Medium to High confidence.
8. **RB market coverage is the weak link** — 10 players, stale, self-contradictory source.
9. **TE market coverage is zero.** Possibly a permanent constraint.
10. ~~Settings still inferred~~ — **RESOLVED 2026-08-16 by owner: single QB, one kicker slot.**
    Both prior inferences were correct. No valuation adjustment needed.

---

## 12. Post-review corrections

Repo reviewed 2026-08-16 at ~2,000 lines of Python. `join_report.txt` clean, 26/26 tests
passing, every spec section implemented rather than stubbed. Three defects found by running the
code against the real `player_master.csv`, in severity order. **These are the whole of the next
work block; nothing else ships first (R24).**

### 12.1 🚨 The composite is not cross-positionally comparable — fix first

Every `_norm` column is `groupby("position").rank(pct=True)`, so the best player at each position
scores ~100 regardless of value. Observed output:

| Player | Pos | composite | `ppr_base` |
|---|---|---|---|
| Ja'Marr Chase | WR | 99.7 | 304 |
| **Trey McBride** | TE | **99.4** | **212** |
| Jahmyr Gibbs | RB | 99.3 | 298 |
| **Josh Allen** | QB | 97.5 | **368** |
| **Colston Loveland** | TE | **97.2** | **182** |
| Justin Jefferson | WR | 96.2 | 252 |

Loveland at 182 projected points outranks Jefferson at 252; Josh Allen leads the board by 65
points and sits 7th. For a tool whose only job is "which position here?", this is fatal.

**Fix (R16/R17):** replace percentile with **value over replacement in PPR points**, replacement
= last starter at each position, flex-aware. Percentiles may remain as expandable display detail;
they must not drive the sort.

**Also kills the bonus layer as currently wired.** The §6.2 insight was a *cross-positional*
shift — QBs gain 8–13 points, WRs ~1. Within-position percentile erases exactly that and leaves
25% of the weight re-ranking QBs against each other by rushing volume. Per R18, `bonus_est_ppr`
keeps its own weight but enters **in points**.

### 12.2 🚨 The survival baseline is degenerate and the manager-priors layer is a no-op

A triangular distribution's support is `[adp_min, adp_max]`, so `survival_baseline` is exactly
1.0 or exactly 0.0 for any player whose target pick falls outside the observed range. Measured at
pick 44 targeting 53:

| Player | baseline | hazard | result |
|---|---|---|---|
| Sam LaPorta | 1.000 | 19.2 | 0.995 |
| Dalton Kincaid | 1.000 | 23.9 | 0.995 |
| Mark Andrews | 1.000 | 53.2 | 0.995 |
| Trey McBride | 0.000 | 182.4 | 0.005 |

`1.0 ** 19.2 == 1.0`. The entire league-specific edge — three TE-hungry managers picking twice
each in precisely that window — moves the number by **nothing**, and 0.995 is just the clip
ceiling. This is the single most decision-relevant defect in the build, because the pick-53 fork
depends on it.

**Fix (R19):** unbounded lognormal, not triangular and **not beta-PERT** — PERT's support is also
`[min, max]` and reproduces the identical hard clip. Treat `adp_min` / `adp_max` as the observed
extremes of *n* draws: expected quantiles ≈ `1/(n+1)` and `n/(n+1)`, so at n=51 they bracket
roughly 1.9% and 98.1%. Fit a lognormal whose median matches `adp_value` and whose implied
quantiles bracket the observed range.

Side benefit worth keeping: **small `adp_n` widens the curve automatically**, which turns the
`ADP_MIN_N = 15` cliff into a continuous function of sample size. Trevor Etienne's 6 drafts
produce an appropriately vague curve instead of a binary exclusion.

**Independent of distribution choice — condition on the present:**

```
P(survive to target | survived to now) = S(target) / S(as_of_pick)
```

Currently absent. That single division fixes a large share of §12.3 on its own.

### 12.3 🚨 No capacity constraint

At pick 44 with **8** intervening picks, the model expects **159.1** players to be taken — 48.7
of them TEs. Survival is computed marginally per player with nothing tying total departures to
the number of picks that actually occur.

**Fix (R20/R21): simulate the intervening picks discretely.** Each intervening manager takes
exactly one player — sampled from a softmax over available players weighted by ADP position,
that manager's positional appetite at that round, and their team/college bias — so exactly *k*
players leave in *k* picks, by construction.

The reason this beats a post-hoc rescale is **substitution**, which is the actual question at
pick 44: if Nick takes McBride, Dylan likely no longer needs a TE. A marginal model structurally
cannot represent "only one of these three TEs goes." Monte Carlo represents it for free, and it
subsumes R21 — the per-pick Bernoulli *is* the simulation step, not a second mechanism.

Cost is negligible: 8 picks × ~2,000 sims. Output is a distribution rather than a point estimate,
which also gives §12.5 a real scoring target.

### 12.4 Smaller items

1. **No kickers exist.** All 417 rows are QB/RB/WR/TE while `ROSTER_TARGET` includes `K: 1`.
   Per R22, drop K from the target — one gets taken by feel at 188. Update `ROSTER_TARGET`,
   `_w10`'s cap logic, and the §8 "exactly 16 picks" arithmetic accordingly.
2. **Raw inputs untracked (R23).** Without `ADP.tsv` and `sleeper_adp_ppr_*.csv`, 6 of 26 tests
   error on a clean clone. Commit them — ~40KB, nothing sensitive, and draft-morning re-scrapes
   become diffable.
3. **Path drift.** `all_draft_picks_2022-2025.csv` and the HTML boards sit at repo root while
   `config.ALL_DRAFT_PICKS_PATH` expects `drafts/`. Move the files or fix the constant; don't
   leave them disagreeing.
4. **The `adp_n` gate runs before the DEF drop**, so ~20 team-defense rows generate low-sample
   flags for rows that get discarded anyway. Reorder so the join report stays readable — its
   value is entirely in being eyeballed, and 20 lines of noise is 20 lines nobody reads.

### 12.5 Backtest before draft day (R25)

`all_draft_picks_2022-2025.csv` holds 611 real picks. Replay 2024 and 2025 pick-by-pick, asking
the availability model at each of the owner's picks for survival probabilities, and score them
against what actually happened — **Brier score, plus a calibration curve in probability
deciles.** Well-calibrated means players given 70% survived about 70% of the time.

Two constraints on interpretation:

- **Treat it as a sanity check on calibration, not a tuning target.** Two seasons of one league
  is a small sample and overfitting to it is easy.
- **The scoring rules changed.** A miss on QB timing in 2024 may reflect the old 4-point passing
  TD rather than a broken model. Expect QB and TE calibration to look worse than RB/WR, and do
  not "correct" for it.

What this genuinely tests is the *mechanism* — whether the manager-priors hazard, the substitution
logic, and the survival curve produce sane numbers against real behaviour. Given §12.2, the
current answer is almost certainly no, which is exactly why the backtest runs after the fixes and
before the dry run.

### 12.6 What the review found working

Recorded so it doesn't get refactored away: `managers_in_range` vs `managers_until_next_owner_pick`
kept deliberately separate; `team_bias.csv` computed from 611 historical picks rather than
hand-transcribed, so it ports to another league automatically; `NAME_ALIASES` catching
`Cam Ward` → `cameron ward` and `Chig Okonkwo` → `chigoziem okonkwo`, which suffix-stripping alone
would miss; survival clipped to [0.005, 0.995] so no cell ever renders false certainty; and the
`BONUS_SIGMA_*` comments stating plainly that QB and TE cannot reach their anchors rather than
fudging the sigmas to pretend otherwise.
