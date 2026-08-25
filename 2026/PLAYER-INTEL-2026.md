# Player Intel — 2026

**Author:** Nathan · **as_of:** 2026-08-16 · **Source:** owner shortlist from Sleeper mock drafts

Hand-written each season. Parsed by `build/08_parse_intel.py` into
`Tool/data/derived/player_intel.csv`, joined to `player_master` on `name_key + position`,
toggled via `LAYERS["player_intel"]`.

## How tags behave

| Tag | Effect |
|---|---|
| `target` | Bounded nudge, **capped at ±10 VOR points**. One tier's worth — enough to break a tie, not enough to overrule the projection |
| `fade` | Same magnitude, negative |
| `hard_avoid` | **Filter, not a nudge.** Never surfaced as a recommendation regardless of value |

`priority` orders players *within* a pick window. 1 = most wanted.

## Standing strategy

- Deviate to QB or TE early only if forced. Ideally QB in the Rd 7–8 pocket.
- TE1 = LaPorta or Kincaid; otherwise take **two** of Kittle / Andrews / Kelce.
- 5 WRs by the Rd 9 pick (101).
- Late rounds: maximize RB count — hardest position to fill.
- Tier-maximizing, not elite-maximizing: prefer several upper-tier players to one best-in-class.

---

## Intel table

| player | position | pick_window | priority | tag | note |
|---|---|---|---|---|---|
| Puka Nacua | WR | 5 | 1 | target | Top 4 (Bijan/Gibbs/Chase/JSN) expected gone in some order |
| Christian McCaffrey | RB | 5 | 2 | target | Only elite RB1 realistically reachable |
| Amon-Ra St. Brown | WR | 5 | 3 | target | |
| Chase Brown | RB | 20 | 1 | target | Doubted to last to 20 |
| Omarion Hampton | RB | 20 | 2 | target | |
| Ken Walker III | RB | 20 | 3 | target | **Nickname alias needed — no ADP joined from either source** |
| A.J. Brown | WR | 20 | 4 | target | Trusty Veteran archetype: 30.6% injury rate |
| Nico Collins | WR | 20 | 5 | target | |
| A.J. Brown | WR | 29 | 1 | target | Hoped fallback if he slides |
| Nico Collins | WR | 29 | 2 | target | |
| Kyren Williams | RB | 29 | 3 | target | |
| Chris Olave | WR | 29 | 4 | target | ADP above factors per grid divergence |
| Josh Allen | QB | 29 | 5 | target | Only QB worth taking this early |
| Brock Bowers | TE | 29 | 6 | target | |
| Trey McBride | TE | 29 | 7 | target | |
| Ladd McConkey | WR | 44 | 1 | target | |
| Emeka Egbuka | WR | 44 | 2 | target | |
| Jaylen Waddle | WR | 44 | 3 | target | Largest positive factor-grid divergence at WR |
| DeVonta Smith | WR | 44 | 4 | target | |
| Cam Skattebo | RB | 44 | 5 | target | No Sleeper ADP — verify status |
| Garrett Wilson | WR | 44 | 6 | target | |
| Zay Flowers | WR | 44 | 7 | target | |
| Sam LaPorta | TE | 53 | — | watch | **Target tag removed 2026-08-24.** 16th by VORP in the Sleeper 48-72 band (15.59) and sharp edge -22, i.e. expensive here. Owner read: a top-6 TE needs to be the number 1 or 2 target on his own offence, and he is third behind Gibbs and Jameson Williams. Injury history. Pick 53 reopened to best available |
| D'Andre Swift | RB | 53 | 3 | target | Preferred if available |
| DJ Moore | WR | 53 | 2 | target | VORP 21.11, sharp +13. Best of the WR options once age fades are respected |
| Rome Odunze | WR | 53 | 1 | target | VORP 22.51, sharp +13, Breakout Candidate. Best combined VORP-plus-sharp play in the band that is not age-faded |
| Quinshon Judkins | RB | 53 | — | fade | Explicit dislike |
| TreVeyon Henderson | RB | 53 | — | fade | Vrabel usage doubt |
| Bhayshul Tuten | RB | 53 | — | fade | Workload uncertainty |
| Terry McLaurin | WR | 53 | — | watch | **Highest VORP in the whole 48-72 band: 39.51, sharp +12.** Faded for age. Owner will reconsider. If the age fade is a reflex rather than a read, he is the pick at 53 |
| Mike Evans | WR | 53 | — | fade | Age. VORP 26.51 but sharp only +4 |
| Davante Adams | WR | 53 | — | watch | VORP 37.51, second highest in the band, but sharp -1 so no market edge. Same age question as McLaurin |
| Jameson Williams | WR | 53 | — | fade | |
| Carnell Tate | WR | 53 | — | fade | |
| Jadarian Price | RB | 68 | 1 | target | If RB2 still empty |
| Christian Watson | WR | 68 | 2 | target | Source arithmetic error — use recomputed score of 8 |
| Marvin Harrison Jr. | WR | 68 | 3 | target | |
| Parker Washington | WR | 68 | 4 | target | Only if Brian Thomas unavailable at 77 |
| RJ Harvey | RB | 77 | 1 | target | If RB2 still empty |
| Brian Thomas Jr. | WR | 77 | 2 | target | Positive factor divergence |
| Rome Odunze | WR | 77 | 3 | target | |
| Trevor Lawrence | QB | 92 | 1 | target | ⚠ Bottom of his ADP band — see Hurts/Stafford |
| Dalton Kincaid | TE | 92 | 2 | target | TE1 fallback if LaPorta missed |
| Michael Pittman Jr. | WR | 92 | 3 | target | |
| Josh Downs | WR | 92 | 4 | target | Wants 5th WR by here |
| George Kittle | TE | 101 | 1 | target | Take 2 of Kittle/Andrews/Kelce if no TE yet |
| Mark Andrews | TE | 101 | 2 | target | |
| Travis Kelce | TE | 101 | 3 | target | |
| Josh Downs | WR | 101 | 4 | target | |
| Xavier Worthy | WR | 116 | 1 | target | |
| Jonathon Brooks | RB | 116 | 2 | target | ⚠ −2.1 composite; Warren/Stevenson better at same cost |
| Rachaad White | RB | 116 | 3 | target | ⚠ −0.6 composite |
| Brock Purdy | QB | 116 | 4 | target | QB2 — designated sacrifice per R11 |
| Jared Goff | QB | 125 | 1 | target | |
| Jalen Coker | WR | 125 | 2 | target | |
| Rashid Shaheed | WR | 125 | 3 | target | |
| Tyjae Spears | RB | 140 | 1 | target | ⚠ −10.0 composite |
| Jordan Love | QB | 140 | 2 | target | |
| Emmett Johnson | RB | 149 | 1 | target | |
| Tank Dell | WR | 149 | 2 | target | ⚠ −28.6; no Sleeper ADP |
| Kyler Murray | QB | 149 | 3 | target | |
| Tyler Allgeier | RB | 164 | 1 | target | ⚠ −33.4 composite, worst on list |
| Jalen Nailor | WR | 164 | 2 | target | |
| Sam Darnold | QB | 164 | 3 | target | |
| Oronde Gadsden II | TE | 173 | 1 | target | Super-late TE |
| Brenton Strange | TE | 173 | 2 | target | |

---

## Flagged for owner review

**Baseline is Sleeper ADP** — that is the draft population. NFFC is the comparison source only.

### 🚨 Availability model is calibrated to the wrong population

`draft_engine.py` runs survival curves off NFFC `min/max/n`. Measured divergence, top-150 Sleeper:

| Position | Sleeper − NFFC | Effect on the model |
|---|---|---|
| **TE** | **−23.2** | Overestimates TE availability by ~23 picks |
| **QB** | **−13.2** | Overestimates QB availability by ~13 picks |
| RB | −3.2 | Roughly correct |
| **WR** | **+9.0** | Underestimates WR availability by ~9 picks |

Both errors favour waiting at exactly the two positions under debate, and understate how cheap the
WR-heavy plan actually is. **Fix:** anchor the lognormal centre on `reference_adp_rank` (Sleeper),
keep NFFC `min/max/n` for dispersion only — it is still the only empirical spread data available.
Recompute the offsets each season; they are a property of the site, not a constant.

### QB windows on Sleeper

Top tier goes 24–64 (Allen 24 / Lamar 33 / **Maye 49** / Burrow 54 / Daniels 61 / Hurts 64), then a
cliff to ~22–28 for picks 73–97, then **Stafford at 102 (33.1)**.

- **Picks 77 and 92 are the worst QB window on the board.** Nothing above 27.9.
- The two real windows are **53** and **101**.
- Stafford beats Lawrence by 5.2 and Herbert by 6.4. If a QB gets taken in the Rd 8–9 area, it
  should be Stafford, not the two names originally shortlisted.

### The pick-53 fork is a coin flip

| Path | Pick 53 | Pick 101 | Total |
|---|---|---|---|
| A — TE first | LaPorta 35.5 | Stafford 33.1 | **68.6** |
| B — QB first | Maye 44.5 | Kelce 23.8 | **68.3** |

Decide on fallback cost, not value. Stafford missing → Purdy at 116 (**−6.2**). Kelce missing →
Andrews at 125 (**−8.1**). **Slight edge to A**, which is the original plan.

### Other items

1. **Rd 2 ordering.** Model has A.J. Brown (59.1) best of that group, not fourth. The
   injury-based fade is independently supported — 30.6% archetype injury rate vs 11.3% for
   Prime WR1. Decide which you are weighting.
2. **Late-RB substitutions.** Warren (+13.3), Stevenson (+13.7), Pollard (+9.1), Dowdle (+7.5)
   all outscore Brooks / White / Spears / Allgeier at comparable cost. **Note Brooks is the
   largest Sleeper-later gap on the board (+61.3)** — Sleeper has him at 130 vs NFFC 69, which
   usually means an injury the high-stakes market has not repriced. Verify before targeting.
3. **One RB through pick 53.** Plan starts RB2 from a near-replacement pool where RB replacement
   is 157 points. Longest-standing structural exposure in the build.
4. **Ken Walker III has no ADP from either source** — `ken walker` vs `kenneth walker`. Alias
   needed, and unmatched top-150 ADP should be a hard failure rather than a note.
5. **Cam Skattebo and Tank Dell have no Sleeper ADP.** Confirm both are active.
6. **WR is cheaper on Sleeper than NFFC (+9.0).** The WR-heavy build is better supported here
   than the NFFC-based numbers suggested. Notable given the four-year critique of this exact
   strategy — the population matters.

## Template for future years

Copy this file to `<season>/PLAYER-INTEL-<season>.md`, clear the table, keep the header and the
tag semantics. The parser requires the four columns `player`, `position`, `pick_window`, `tag`;
`priority` and `note` are optional.
