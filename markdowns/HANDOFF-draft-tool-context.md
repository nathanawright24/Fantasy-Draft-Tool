# Handoff Context — Fantasy Draft Tool Build

**Purpose:** everything established in the analysis conversation, so the build chat starts with full context and doesn't re-litigate settled questions or re-derive the data.

**Companion files to upload alongside this doc:**
- `all_draft_picks_2022-2025.csv` — all 984 picks, cleaned and validated. **Use this instead of re-parsing anything.**
- `parse_sleeper_draft_html.py` — the parser, only needed if new draft HTML gets added.
- `league-draft-tendencies-2026.md` — the full tendency analysis. The build should treat its per-manager numbers as the behavioral priors.

---

## 1. The league

"We Made A League Mr. Stark" — 12-team redraft, snake, 16 rounds (was 17 in 2022–23).

**2026 settings (confirmed by owner):**
- Roster setup identical to 2025
- **5 points per passing TD** — ⚠️ NEW for 2026
- **3-point bonus for 100+ scrimmage yards** (rushing + receiving combined) — ⚠️ NEW for 2026
- **3-point bonus for 300+ passing yards** — ⚠️ NEW for 2026

All three scoring items are **new this season**. Historical drafts predate them. See the rule-change section below before using any historical ADP as a baseline.

**Starting lineup (confirmed by owner, and UNCHANGED across all five drafts analyzed):**

`1 QB / 2 RB / 2 WR / 1 TE / 2 FLEX` — 8 skill starters, plus a kicker slot (inferred: every team drafts exactly one K, and 7 of 11 went in round 13 of 2025). 16 roster spots, ~1.5 min per pick, so roughly 9 starters and 7 bench.

**This is analytically important.** Because roster construction held constant across 2022–2025, scoring is the *only* variable changing for 2026. The historical positional distributions are directly comparable year over year, and any year-to-year drift in the data reflects genuine behavioral change rather than a settings change. That materially strengthens the behavioral priors in §5.

**Weekly league-wide starter demand:** 12 QB, 24 RB, 24 WR, 12 TE, plus 24 flex-eligible slots on top of that. The 2-flex structure is what drives the league's WR-heavy drafting (6.3 WR per team) — flex spots get won with receiver depth.

Per-team position averages, 2025: 2.1 QB / 4.8 RB / 6.3 WR / 1.9 TE / 0.9 K.

**Still inferred, worth a 30-second confirmation:**
- Single QB, not superflex — consistent with first QB at pick 25 and 2.1 QB per team
- No team defenses (13 DEF drafted in 2022, 9 in 2023, **zero** in 2024 and 2025)
- Kicker slot count (assumed 1)
- **Flex eligibility** — assumed RB/WR/TE. If TE qualifies, TE valuation shifts meaningfully, since a second TE becomes a startable asset rather than pure insurance.

**Receiving scoring: full PPR** (confirmed). **Critically, PPR is the pre-existing baseline — it was in place for all five historical drafts.** Only the 5-point passing TD and the two big-game bonuses are new. This means the league's WR-heavy drafting is already a PPR-informed adaptation, not something the tool needs to discover or correct for.

**Flex eligibility: RB / WR / TE** (confirmed). Owner notes a TE in the flex is rare in practice — it happens only when a roster is in bad shape. Treat TE2 as **insurance rather than a startable flex asset**: 12 TE starters are needed league-wide against ~23 TEs drafted, so the second TE on a roster is a hedge against injury and bye weeks, not a lineup contributor. This is the correct lens for reading Dylan's TE stacking (2–3 per year) and Nick's early-TE habit — Dylan is buying insurance, Nick is buying a starter.

**🚨 CRITICAL: THE SCORING IS NEW FOR 2026.** The 5-point passing TD and both big-game bonuses are **brand new this season**. Every draft in the historical data was run under the previous scoring (presumably 4-point passing TDs, no bonuses). This is the single most important caveat in this entire handoff:

> **The observed tendencies describe how these twelve people behaved under scoring that no longer exists.**

The behavioral priors in §5 — who takes a QB when, how the TE market breaks — remain the best available evidence about these individuals' *habits and temperaments*. But the historical ADP is **not** a valid 2026 baseline for positional value, and any recommendation derived from it needs a rule-change adjustment layer.

### Estimated magnitude of the shift

Illustrative arithmetic, using plausible season lines rather than projections — treat as order-of-magnitude, not precision. Points gained versus the old scoring:

| Player archetype | Gain/season | Per game |
|---|---|---|
| Elite volume passer (38 TD, 7× 300yd) | +59 | +3.5 |
| Mid QB (26 TD, 3× 300yd) | +35 | +2.1 |
| Rushing QB (24 TD, 2× 300yd) | +30 | +1.8 |
| Low-end starting QB (18 TD, 1× 300yd) | +21 | +1.2 |
| Workhorse RB (10× 100+ scrimmage) | +30 | +1.8 |
| Alpha WR (6× 100+) | +18 | +1.1 |
| Mid RB (4× 100+) | +12 | +0.7 |
| WR3 (2× 100+) | +6 | +0.4 |

### Which position groups actually gain (PPR held as baseline)

Because PPR is unchanged, the right question is what the *new* scoring adds on top of it, by archetype:

| Archetype | Basis | Gain/season | Per game |
|---|---|---|---|
| Elite pocket passer QB | 38 TD, 7× 300yd | +59 | +3.5 |
| Elite dual-threat QB | 30 TD, 4× 300, 4× 100 scrim | +54 | +3.2 |
| Mid QB | 26 TD, 3× 300 | +35 | +2.1 |
| Workhorse dual-role RB | 10× 100+ scrimmage | +30 | +1.8 |
| Pure runner RB | 7× 100+ | +21 | +1.2 |
| Alpha WR | 6× 100+ | +18 | +1.1 |
| Mid RB / WR2 | 4× 100+ | +12 | +0.7 |
| Elite TE | 4× 100+ | +12 | +0.7 |
| Mid TE | 1× 100+ | +3 | +0.2 |

**Ordering of relative gain: QB >> RB > WR ≈ TE.**

Two consequences the build should encode:

- **Dual-role RBs gain twice.** In PPR a back clears 100 scrimmage yards through rushing *and* receiving combined, so a pass-catching back banks PPR receptions and hits the bonus more often than a pure runner (+30 vs +21). Receiving backs should get an explicit bump in the value model.
- **The change mildly deflates the league's WR-first orthodoxy.** Alpha WRs gain the least of the skill groups (+18). PPR plus two flex spots still makes WR depth correct — this is not an argument for abandoning it — but the marginal edge moves toward workhorse backs and quarterbacks. Nathan's four-year WR-max build is the strategy most exposed by the new rules.
- **Elite TE gains little** (+12), which is a mild argument against paying a round 2 price for one this year specifically.

Two further conclusions:

1. **QBs gain more than flex players, and the QB1-to-QB12 gap widens by roughly twice as much as the RB1-to-mid-RB gap** (~38 pts vs ~18 pts in this illustration). Since single-QB league value is driven by the spread over the replacement starter, not the raw total, this makes an early QB **more** defensible than the historical data implies.
2. **The 100+ scrimmage bonus rewards volume and week-to-week consistency**, which favors true workhorse backs and alpha receivers over TD-dependent or rotational players. It also lets pass-catching RBs combine rushing and receiving toward the threshold — a meaningful edge for dual-threat backs.

### ⚠️ Reversal of an earlier recommendation

The original analysis concluded: *"skip the round 3 QB run — picks 40–99 yielded only four QBs, so a round 7–9 QB costs you almost nothing."* **That advice was calibrated to the old scoring and should not be carried into 2026 unexamined.** With the QB spread widening, the mid-round QB desert becomes a genuine risk rather than an exploitable inefficiency.

### Resolved: QBs can earn both bonuses

**Confirmed by owner — a QB can hypothetically collect the 300-yard passing bonus and the 100-yard scrimmage bonus in the same game.** Revised arithmetic with scrimmage eligibility included:

| QB archetype | Gain/season | Per game |
|---|---|---|
| Elite pocket passer (38 TD, 7× 300yd, 0× 100 scrim) | +59 | +3.5 |
| Elite dual-threat (30 TD, 4× 300yd, 4× 100 scrim) | +54 | +3.2 |
| Mid dual-threat (24 TD, 2× 300yd, 3× 100 scrim) | +39 | +2.3 |
| Low-end starter (18 TD, 1× 300yd) | +21 | +1.2 |

The notable result: the double-dip **does not** vault rushing QBs past elite pocket passers, because 100+ *rushing* yards is a rarer single-game event for a QB than 300+ passing yards is for a volume thrower. What it does do is lift the mid-tier dual-threat closer to the elite tier (+39 vs +54), which **compresses the middle of the QB range**. Practical read: the cheap mobile QB in rounds 6–9 gains more proportionally than the round-3 elite arm — a mild argument *against* over-reacting at the top of the QB market.

This partly offsets the QB-inflation pressure described above. Both effects are real and they push in opposite directions.

### Behavioral question: will the league adjust?

History can't answer this, but it can identify who is *capable* of adjusting:
- **Kaiden demonstrably adapts** — he moved his QB from round 6 (three straight years) to round 3 in 2025.
- **Greg is rigid** at rounds 3–4 regardless; he was already there, so a rule change won't move him much.
- **Nathan drifted later every year** (4→5→9→10), following the market rather than leading it — he's the most likely to be caught out by the new scoring.
- **Ryan's round-7 metronome** is the tendency most at risk of breaking this season.

**Owner's expectation, to be used as the default:** no dramatic overcorrection — QBs shift up "a little bit." Combined with the mid-tier compression noted below, that argues for a modest adjustment rather than an aggressive one.

**Suggested default parameterization:**

| Metric | Historical (2025) | Suggested 2026 default |
|---|---|---|
| QB1 off the board | pick 25 | pick ~20–22 |
| 5 QBs gone by | pick 39 | pick ~33–36 |
| QBs gone by pick 39 | 5 | 6–7 |
| QBs in the picks 40–99 "desert" | 4 | 5–6 |

Expose this as a tunable slider rather than a constant, and have the tool re-estimate from observed picks once the draft is live — after roughly 20 picks, the actual QB pace is better evidence than any prior.

---

## 2. Members and username history

Twelve members, all carried from 2025 into 2026.

| Display name | Sleeper username | Stated biases | Drafts on file |
|---|---|---|---|
| Nick | npd312 | Chicago Bears, Illinois, Iowa State, Alabama | 4 |
| Dylan | itsssssDylan (**formerly `clappinyou`**) | Pittsburgh Steelers | 4 |
| Cailen | Cailenmh | Denver Broncos, Florida State | 2 |
| Jayden | Sawftwars1 | NY Giants, UCF | 2 (1 main + 1 alt) |
| Nathan | nathanawright24 | Jacksonville Jaguars, Ohio State | 4 |
| Ryan | RyanAnderson25 | Pittsburgh Steelers, Ohio State | 4 |
| Joseph | josekhgreen | Atlanta Falcons, Florida (owner notes: fairly unbiased) | 4 |
| Colin | cmmont13 | NY Giants | 2 (1 main + 1 alt) |
| Greg | gregdani612 | Chicago Bears, Alabama, Illinois, Iowa State | 4 |
| Asa | AsaArnold | Minnesota Vikings | 2 (1 main + 1 alt) |
| Kaiden | BigBallsKaiden | Denver Broncos, Florida | 4 |
| Tyler | tyleranderson8 | Ohio State | 1 |

**Critical join note:** `clappinyou` in 2022–2024 is Dylan. The CSV already maps this in the `manager` column. Departed members (`brianthebuck15`, `jrocka33`, `Harryschultz`, `hunterslosser`, `levibrad`, and the 2025-alt-only usernames) have a blank `manager` and `current_member = N` — **exclude them from behavioral modeling but keep them in the data**, because they consumed picks and therefore shaped the observed ADP.

---

## 3. Data provenance and pitfalls

Sleeper draft URLs (season → draft_id):
- 2025 main — `1209236378332712960`
- 2025 alt (BGA, partial member overlap) — `1241981499981438976`
- 2024 main — `1048309365154910209`
- 2023 main — `916453503533510657`
- 2022 main — `851584287596732416`

**How the data was obtained.** The draft pages are client-rendered JavaScript apps — fetching the URL server-side returns only page metadata, no picks. `api.sleeper.app` is **blocked at the network layer in the Claude sandbox**, so the API route was unavailable. The owner browser-saved each draft page (Ctrl+S), which captures the fully rendered DOM including the complete board. That worked; all 984 picks validated against expected slot counts with zero gaps or duplicates.

**Parsing pitfalls already solved** (relevant only if new HTML is added):
1. The DOM repeats each manager's username twice in a row as a column header. That doubling is how you detect a new team column.
2. Free agents at draft time render as `WR -` with no NFL team. A regex requiring a team silently drops these picks — three were lost initially (a 2022 O. Beckham, a 2023 K. Hunt, and a T. Tebow joke pick in the alt league). The CSV codes these as `FA`.
3. Cookie-banner text pollutes the extracted node list; `0` and `Sale of Personal Data` appear as phantom managers with zero picks.
4. **Player names are abbreviated as `F. Last`, which collides.** "B. Robinson" matched Bijan Robinson (ATL) to Brian Robinson (SF) across two drafts and produced a bogus 87-pick reach that briefly corrupted the analysis. **Always key on name + position + NFL team.** This will bite again when joining to any external rankings source — plan on fuzzy matching plus a manual override table.

**For the build:** a browser-based tool runs on the user's machine, not in the sandbox, so it may be able to call `api.sleeper.app` directly for live rankings and pick data. **This is untested** — it depends on Sleeper's CORS headers and the artifact sandbox's network policy. Test it early with a trivial fetch, because the answer determines the whole data-ingestion design: if it works the tool self-updates, if it doesn't the user pastes or uploads a rankings export each season.

---

## 4. League-wide draft structure (the ADP baseline)

Pooled position mix by round, four main-league drafts:

| Round | QB | RB | WR | TE |
|---|---|---|---|---|
| 1 | 0% | 52% | 46% | 2% |
| 2 | 4% | 42% | 48% | 6% |
| 3 | 15% | 40% | 35% | 10% |
| 4 | 19% | 21% | 44% | 15% |
| 5 | 15% | 21% | 44% | 19% |
| 6 | 17% | 29% | 44% | 10% |
| 7 | 8% | 23% | 54% | 15% |
| 8 | 12% | 35% | 42% | 10% |

Structural facts the availability model should encode:

1. **Round 1 is RB/WR only.** 48 first-round picks, four years: 25 RB, 22 WR, 1 TE (Kaiden's round-1 Kelce, 2023), zero QB. In 2025 the first 16 picks were pure RB/WR before the first TE.
2. **QB cliff then desert.** 2025: QB1–QB5 at picks 25, 27, 33, 35, 39 — five QBs in a 15-pick window at the round 3 turn. Then picks 40–99 produced only **four** QBs (55, 71, 78, 85). Same shape in prior years (QB1 at 17, 20, 28).
3. **TE has two cliffs.** 2025: TE1 at 17, TE2 at 31, pause, TE3–4 at 49–50, then TE5–TE10 all between picks 67 and 84 — six TEs in 18 picks.
4. **RB re-run in rounds 8–9:** 11 RBs in 2025. Last window for defined-role backs.
5. **Kickers cluster in round 13** (7 of 11 in 2025, picks 146–155). Only Cailen goes earlier (round 10, pick 118 — 28 picks ahead of the next kicker). Joseph drafted no kicker at all in 2025.

---

## 5. Per-manager priors for the availability model

Averages across each manager's available drafts. `QB rd` / `TE rd` = round of their *first* pick at that position.

| Manager | Drafts | QB rd | TE rd | RB in R1–8 | WR in R1–8 | K rd | Confidence |
|---|---|---|---|---|---|---|---|
| Greg | 4 | 3.5 | 7.8 | 2.5 | 3.5 | 12.5 | High |
| Asa | 2 | 3.5 | 6.0 | 2.0 | 4.5 | 15.5 | Medium |
| Cailen | 2 | 4.0 | 5.5 | 2.0 | 4.0 | 11.0 | Medium |
| Nick | 4 | 4.2 | 3.8 | 2.75 | 3.25 | 13.8 | High |
| Kaiden | 4 | 5.2 | 4.5 | 2.5 | 3.5 | 12.0 | High |
| Colin | 2 | 6.0 | 4.5 | 2.5 | 4.0 | 11.5 | Medium, **high variance** |
| Dylan | 4 | 6.0 | 4.75 | 3.0 | 2.75 | 13.0 | High |
| Joseph | 4 | 6.3 | 5.8 | 3.0 | 3.75 | 12.3 | High, **high variance** |
| Nathan | 4 | 7.0 | 6.5 | 2.25 | 4.5 | 15.5 | High |
| Ryan | 4 | 7.5 | 6.0 | 2.75 | 3.5 | 14.0 | High |
| Jayden | 2 | 7.5 | 8.5 | 3.0 | 4.0 | 13.0 | Medium |
| Tyler | 1 | 8.0 | 7.0 | 3.0 | 3.0 | 14.0 | **Low — one sample** |

**Model these as distributions, not point estimates.** Notes that should shape the variance term:

- **Ryan is the most predictable human in the dataset:** QB1 in rounds 7/7/7/9, and never a QB or TE in rounds 1–4 in any of four years. Tight distribution.
- **Greg is the QB anchor:** rounds 3/3/4/4, no deviation, and he takes 2–3 QBs per draft. If he picks ahead of you and you want a round-3 QB, assume it's gone.
- **Jayden is structurally rigid:** exactly 3 RB / 4 WR through eight rounds in both drafts, from different slots in different rooms.
- **Dylan always drafts exactly two QBs and stacks 2–3 TEs**, every year, four for four. His QB1 is mid-round so he isn't a round-3 competitor, but he removes two QBs from the pool.
- **Colin inverts himself.** Main league: TE at 2.8, QB punted to round 9. Alt league same season: QB in round 3, TE in round 7. Model as bimodal — he takes one premium position early and abandons the other, and which one is board-dependent.
- **Joseph is the least predictable.** Drafted **zero QBs in 17 rounds in 2022**. No kicker in 2025. TE1 has ranged round 3 to round 10. Largest non-kicker reach in the data (Mahomes 38 picks early).
- **Nathan is drifting later at both premium spots:** QB 4→5→9→10, TE 4→4→8→10. In 2025 he entered round 9 with neither.
- **Tyler is one sample.** Don't let the tool present his numbers with the same confidence as Ryan's.

### Alt-league caveat (important for weighting)
Asa, Jayden, and Colin each have one draft in a *different* league (2025 BGA). Round timing there reflects eleven different opponents, so it is **not** directly comparable to main-league timing. Use alt drafts as evidence of *preference and willingness* (will this person consider an early QB at all?) and down-weight them for calibrated timing. Where the two disagree sharply — Colin — treat as volatility rather than averaging into a misleading middle.

---

## 6. Home-team bias, quantified

Measurable from NFL team (present in the data). Real, but mostly late-round, with specific early exceptions.

| Manager | Team | Verdict |
|---|---|---|
| Nick | CHI | **Strongest in the league.** Took 4 Bears in 2023 vs ~1.2 expected. Odunze 6.12, C. Williams 13.1 in 2025. Takes the top Bear ~a round early, then hoards Chicago depth after round 10. |
| Cailen + Kaiden | DEN | **Most concentrated pair.** Took 5 of 8 Broncos drafted in 2024 vs ~1.3 expected. Cailen pays mid-round prices (Sutton 5.3, Engram 7.3); Kaiden pays round 9+. |
| Colin | NYG | Unambiguous when Giants are available: Nabers 1.8, Tracy 8.8 — 2 of the 4 Giants drafted. |
| Nathan | JAX | Front-loaded early years: Etienne 3.6 (2022), Ridley 3.4 (2023) were genuine reaches. Cooled since. Takes a Jaguar within a round of value. |
| Joseph | ATL | Inconsistent. Pitts 3.1 (2022), London 2.6 (2025), but zero Falcons in 2023 and 2024. |
| Greg | CHI | Present but mild and late (K. Allen R6, Kmet R11, Swift 6.4). Much weaker than Nick's. |
| Dylan + Ryan | PIT | Weak, confined to cheap assets — kickers, TEs, backup QBs (Boswell 13.2, Jonnu Smith R14, Pickett R12). Reads like Pittsburgh scarcity more than restraint. |
| Asa | MIN | **Effectively nonexistent.** Zero Vikings in the main league despite 7 going; one late Thielen pick in the alt. |
| Jayden | NYG | **Effectively nonexistent.** Zero Giants across 32 picks in two drafts. |

**Corollary for the tool's availability math:** players on Chicago, Denver, New York (G), and Jacksonville should have their survival probability discounted — there are two Bears fans, two Broncos fans, and two Giants fans in a 12-team league. Minnesota, Atlanta, and Pittsburgh players tend to fall past market price here.

**⚠️ College bias is NOT in this data.** Sleeper's board carries NFL team but not college, so the Ohio State / Alabama / Illinois / Iowa State / UCF / Florida / Florida State leans could not be measured. Four members have Ohio State ties (Nathan, Ryan, Tyler, plus Nick and Greg with Alabama/Illinois/Iowa State), and the owner expects those players to go above market. To model it, the build needs a player→college mapping joined in from an external source; deriving it from model recall is possible but should be labeled as such.

---

## 7. Reach vs. value baseline

The 2025 alt league drafted the same player pool in the same season, giving a rough independent market baseline. Positive = main league took the player earlier. Rounds 1–8 only, n=8 per manager — **directional only**, and four members played in both leagues, which contaminates it.

Aggressive: Joseph +4.1, Dylan +4.0, Tyler +3.4. Neutral: Colin +0.9, Kaiden +0.6, Asa +0.2, Nathan −1.0. Patient: Greg −1.4, Jayden −1.4, Nick −1.5, Ryan −1.6, Cailen −3.8.

Notable individual reaches: Boswell +42 (Dylan), McCarthy +40 (Colin), Mahomes +38 (Joseph), Pitts +32 (Dylan), Fields +32 (Dylan), Maye +28 (Colin), Kelce +27 (Dylan).

**Better approach for the build:** replace this proxy with real ADP once a rankings source is wired in, and compute reach as `actual_pick − consensus_ADP` per manager per season. That yields a per-manager aggressiveness coefficient with far more signal than n=8.

---

## 8. What the tool needs to do

Four components, in rough dependency order:

1. **Rankings comparison** — available players vs. Sleeper rankings, surfacing value gaps. *Blocked on a rankings data source; resolve the CORS test first.*
2. **Availability probability** — for each player, P(still available at my next pick). Formula shape: for each manager picking between now and my next turn, estimate P(they take a player of this type) from their positional-appetite-by-round priors in §5, then combine. Apply the bias discounts from §6. Widen intervals for low-confidence managers (Tyler) and bimodal ones (Colin, Joseph).
   - **Required: a scoring-regime adjustment layer.** The priors come from pre-2026 scoring. Expose a tunable "QB inflation" parameter (how many rounds earlier the QB run starts than history suggests) rather than hard-coding the historical curve. Default it to at least a modest shift earlier, and let the user revise it live as the actual draft reveals how the league responded.
3. **Decision tree / roster-path recommender** — given current roster state and remaining picks, surface viable build paths. Owner-selected guardrails:
   - **Positional minimums by round**
   - **Zero-RB / hero-RB style locks** (strategy commitment that constrains the tree)
   - **Value-over-need thresholds** (how large a value gap justifies drafting against need)
   - *(Bye-week stacking avoidance was offered and declined — don't build it.)*
4. **Live pick entry** — write in each pick as it happens; all numbers recalculate. Artifacts support persistent storage, so a draft in progress should survive a refresh.

---

## 9. Open items to resolve in the build chat

1. **CORS test** — can a browser-run artifact reach `api.sleeper.app`? Determines the entire ingestion design.
2. **Rankings source** — Sleeper's own rankings if the API works; otherwise a pasted or uploaded export.
3. **2026 draft slot** — unknown. The tool should accept it as an input, since slot drives everything about the availability math.
4. **Three unidentified 2024 managers** — "Team 3" (slot 2), "Team 7" (slot 4), "Team 8" (slot 12). Candidates: Jayden, Colin, Asa, Tyler. Weak hint: Team 7 took Nabers in round 5 and Jake Elliott as kicker in round 13, matching Colin's 2025 habits, but Team 7 also took Josh Allen in round 3, which contradicts Colin's round 9 punt. Identifying these would upgrade three profiles from Medium to High.
5. **College mapping** — needed if the college-bias effect is to be modeled at all.
6. **Confirm** the single-QB inference and the kicker slot count (the only settings still inferred rather than stated).
7. **Whether rankings sources reflect the custom scoring.** Sleeper's default rankings assume standard scoring; with 5-pt passing TDs and big-game bonuses, off-the-shelf rankings will systematically undervalue QBs and high-volume flex players in this league. The tool should either use custom-scoring projections or apply its own adjustment on top.
