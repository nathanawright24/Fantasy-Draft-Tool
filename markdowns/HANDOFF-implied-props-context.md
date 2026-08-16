# Handoff Context — Vegas-Implied Fantasy Points & O-Line Blend

**Purpose:** everything established in the props/projections conversation, so a future chat can refresh the data for a new league year without re-deriving the method, re-testing dead sources, or re-discovering the scoring traps.

**Sibling doc:** `HANDOFF-draft-tool-context.md` (league settings, manager behavior, draft tool spec). This doc is the *player valuation* input layer to that tool. Read §1 of this doc against §1 of that one — there is a scoring mismatch that matters.

**Companion files:**
- `qb_implied_props.csv`, `rb_implied_props.csv`, `wr_implied_props.csv`, `te_implied_props.csv` — the built output for 2026. **Use these rather than re-pulling anything.**
- `oline_blend_rankings.csv` — 32-team blended O-line ranking.
- `TEMPLATE_*.csv` (5 files) — empty schemas for next season's refresh.
- `SCHEMA-implied-props-templates.md` — column-by-column data dictionary and refresh runbook.

---

## 1. The objective and the scoring

Build market-implied fantasy point totals per player from **season-long** sportsbook props, in the owner's league scoring, so that Vegas can be used as an independent signal against analyst projections. **This is NFL analysis, not betting advice** — the owner stated that explicitly and it should be taken at face value.

**Scoring applied (owner-specified):**

| Item | Value |
|---|---|
| Reception | 1.0 |
| Rushing yard | 0.1 |
| Receiving yard | 0.1 |
| Rushing TD | 6 |
| Receiving TD | 6 |
| Passing yard | 0.04 |
| Passing TD | 5 |

### 🚨 The bonuses are missing, and they cannot be added from season-long props

The sibling handoff documents two **new-for-2026** scoring items that are **absent from every number in these files**:

- **3-point bonus for 300+ passing yards** (per game)
- **3-point bonus for 100+ scrimmage yards** (per game)

These are *per-game threshold* bonuses. Season-long prop totals and season-long projections carry no game-by-game distribution, so the bonus count is **not recoverable** from this data. A 1,200-yard rusher who gains it in eight 150-yard games and one who spreads it evenly across 17 produce identical season totals and wildly different bonus counts.

**Consequence:** these files systematically **understate** QBs and high-volume, boom-profile flex players relative to true league scoring. Per the sibling doc's estimates the gap is roughly **+21 to +59 points for QBs** and **+6 to +30 for flex**, i.e. large enough to change rankings. Anyone using these files for draft valuation must add a bonus-estimation layer. Options, in rough order of quality:

1. Per-game simulation from a distribution (needs game-level projections, which Clay's team pages partially support).
2. Historical bonus-hit rates by archetype, applied to projected volume.
3. The sibling doc's flat archetype table (§1 there) as a crude additive.

Nothing in the current files does any of this. Treat `ppr_*` columns as **base PPR only** and name them that way in any downstream tool.

---

## 2. What the data actually is

Two fundamentally different kinds of number live side by side in every file. Keeping them straight is the single most important discipline here.

| Prefix | Meaning |
|---|---|
| `mkt_*` | A real sportsbook over/under line. Market consensus. |
| `proj_*` | An analyst projection, 17-game basis, no injury discount. |
| `adj_*` | The projection after a games-played haircut (see §4). |

**Never rank across the two.** A market-sourced player will look worse than a projection-sourced one for reasons that have nothing to do with the player — see §4.

### Coverage reality for 2026 (will be similar next year)

| Position | Rows | Market coverage | Notes |
|---|---|---|---|
| QB | 40 | 24 players, passing yards only | No market for pass TDs, rush yds, rush TDs |
| RB | 111 | 10 players, partial, **2 months stale** | Weakest link in the whole build |
| WR | 186 | 50 players, rec/yds/TD complete | The only genuinely well-covered position |
| TE | 80 | **zero** | No season-long TE props found anywhere |

### 🚨 "Targets" is not a sportsbook market

No traditional book offers a targets over/under, season-long or weekly. It appears occasionally on pick'em apps (Underdog, PrizePicks) and sparsely even there. **The targets column will always be projection-sourced.** Do not spend time hunting for it. Same is largely true of rush attempts.

### Weekly vs season-long TD markets differ in kind

Season-long **total TDs** exist as real over/unders. Weekly TDs are priced as **anytime-TD-scorer moneylines**, which must be converted to expected TDs via implied probability (and de-vigged) before they mean anything. If a future refresh moves to weekly slates, this conversion is required work, not an afterthought. Also note "lead the league in X" markets are useless for projection — they are not totals.

---

## 3. Sources: what worked, what is a dead end

### ✅ Worked

| Source | What it gave | How |
|---|---|---|
| **Mike Clay / ESPN Projection Guide** (owner-uploaded PDF) | 417 players, every requested stat incl. targets & carries, dated 8/13 | `pdftotext -layout`, parse the leaderboard section |
| FOX Sports season-passing-yards roundup | 24 QB DraftKings lines, 8/10 | Static article HTML |
| Fantasy Alarm WR prop-totals article | 50 WR × rec/yds/TD, DK primary + BetMGM/Caesars fallback, 8/14 | Static article HTML |
| SI running-back-odds article | 10 RB Caesars lines, 6/6 | Static, partial, self-contradictory |
| **PFF O-line rankings** | 32-team ordinal | **Owner pasted/uploaded a digest CSV** — the only way in |

**The pattern:** article-format roundups are retrievable; live odds tables are not. Search for *"2026 [position] prop totals over under"* style article titles rather than trying to reach a book or an odds aggregator.

### ❌ Dead ends — do not re-attempt without new information

| Source | Failure mode |
|---|---|
| BettingPros, RotoWire, FTA, Fantasy Points odds tables | JavaScript-rendered; fetch returns nav chrome, zero numbers |
| DraftKings, FanDuel, BetMGM, Caesars, Hard Rock, ESPN BET direct | Geo-gated, auth-walled, JS-rendered, ToS |
| **Establish The Run (Thorn O-line)** | **Paywalled.** Retrieved methodology + Tier 1 only (4 of 32 teams), then hard subscribe wall |
| PFF site directly | Login required |
| Underdog, Sleeper projections | Login / JS |
| `api.sleeper.app` | **Blocked at the sandbox network layer** — matches the finding in the sibling handoff |

**Sandbox network allowlist** is narrow (PyPI, npm, GitHub, Ubuntu, `api.anthropic.com`, Sumo Logic). No sports data host is reachable from `bash`. Everything must arrive via `web_fetch` on a static page or as an owner upload.

### The reliable play for next year

**Ask the owner to upload two things at the start:** (1) the current Clay/ESPN projection guide PDF, (2) a pasted digest of whichever O-line rankings they want. That alone reproduces ~90% of this build in a fraction of the effort. Then hunt article-format prop roundups for the market layer.

An **odds API** (The Odds API, OddsJam) would solve market coverage properly, including RB rush attempts and TE receptions. The owner stated they will not have API keys — revisit only if that changes.

---

## 4. 🚨 The games-played convention — the biggest trap in this dataset

Market lines sit **systematically below** 17-game projections. This is not disagreement about players. Books price in the probability a player misses time and collect the under when they do; analyst projections typically assume a full season.

Measured on the 50 WRs with both numbers:

| Comparison | Mean gap | Ratio |
|---|---|---|
| Market vs Clay **raw 17-game** | −24.8 PPR | 0.887 |
| Market vs Clay **prop-adjusted** | **+0.5 PPR** | **1.006** |

**Clay states his own adjustment on the guide's cover page:** remove roughly **2 games** for QB/WR/TE and **3 games** for RB before comparing to props. Applying exactly that collapses an 11.3% systematic gap to essentially zero across 50 receivers.

**That convergence is the most valuable single result in this conversation.** Two independent methodologies — a books' consensus and one analyst's film-and-model work — agree within half a point once the games convention is reconciled. It validates both, and it means the *residual* after adjustment is real signal rather than noise.

**Position asymmetry, do not over-generalize:** the −2 game haircut **overshoots on QBs**. Market passing yards run 375 below Clay's raw figure but **87 above** his adjusted figure. Books discount QB availability less harshly than −2 games. Clay's guidance is a rule of thumb, not a per-position fit. If a future refresh has enough market coverage, fit the haircut per position empirically instead.

**Practical rule:** compare `ppr_market` to `ppr_*_propadj`, never to the raw 17-game figure.

---

## 5. 🚨 Projection-source scoring is not league scoring

**Clay's `FF Pt` column uses ESPN default scoring: 4-point passing TDs, −2 per interception, −2 per fumble lost.** It is *not* the owner's league scoring and must not be used as a fantasy point total.

Every `ppr_*` column in the output files was **recomputed from raw stat lines** at the owner's values. QB totals therefore run materially higher than Clay's published `FF Pt`. Do not cross-reference the two columns or reconcile them; they answer different questions.

**Validation method used, worth repeating:** recompute the source's own point column from its raw stats under *its* assumed scoring, and confirm it reproduces. It matched within rounding across all four positions (max deviation 10 for QB, ≤6 for flex — consistent with unmodeled fumbles and Clay's stated rounding). This is how you prove a parse is clean before trusting 417 rows. **Do this every refresh.**

---

## 6. Parsing pitfalls already solved

1. **Team abbreviations are non-standard.** Clay uses `ARZ, BLT, CLV, HST, JAX, LV` — not ARI/BAL/CLE/HOU/JAC/LVR. A mapping table is required to join anything. Full-name-to-abbrev map for the O-line join is in `oline.py` logic and reproduced in the schema doc.
2. **Leaderboard tables carry 12 numeric columns** for every position (QB and flex alike, different meanings). Off-by-one silently drops every row — symptom is a parse returning zero rows, not wrong rows.
3. **Name matching.** Suffixes (`Jr.`, `III`), apostrophes (`Ja'Marr`, `Wan'Dale`), and periods (`A.J.`, `T.J.`) all break naive joins. Normalize by stripping suffixes, lowercasing, removing punctuation, and collapsing hyphens. **The sibling handoff's warning applies with equal force here:** abbreviated names collide (Bijan vs. Brian Robinson). Key on name + position + team where possible.
4. **Sources disagree on 2026 team assignments.** Jaylen Waddle appears in Denver per Clay and FOX, but was discussed as a Dolphin in an August prop article. Left as-sourced rather than resolved. Expect a handful of these; do not silently pick a winner.
5. **The SI running-back article contradicts itself** — calls two different numbers "tied for the highest," and describes one back's line only in prose ("slightly higher than Robinson's") with no figure. Flagged per-row rather than guessed.
6. **Read the projection guide's cover page.** The prop-adjustment guidance in §4 was sitting there in plain text and is the key to the whole reconciliation.

---

## 7. O-line blend

**Inputs:** PFF 1–32 ordinal (owner-supplied digest) and Clay/ESPN O-line unit grade, 1–10 scale.

**Method:** Clay's grade only takes **four distinct values** in practice (one 8, seven 7s, seventeen 6s, seven 5s). A raw numeric average would let PFF's fine-grained ranks dominate. So Clay's grades were converted to **rank positions with ties averaged** (all seventeen 6s → 17.0), then the two rank positions were averaged 50/50 and re-ranked into tiers of 8.

**⚠️ Method limitation, stated plainly:** because of those ties, Clay effectively sorts the league into four buckets. **Within the large middle bucket, blended order is decided entirely by PFF.** That is inherent to a 50/50 blend when one input is this coarse.

**Agreement:** Spearman 0.75 between the two sources — the blend does real work rather than reshuffling.

**Top tier (blend):** DEN, PHI, TB, CHI, BUF, LAC, SF, LAR. Cross-check: Thorn's ETR Tier 1 was DEN, PHI, TB, BUF — all four land in the blended top five, and all three sources put **Denver first**. Three-way convergence.

**Largest source disagreements** — these are the uncertainty flags, and the most useful thing in the file:

| Team | PFF | Clay rank | Gap | Fantasy relevance |
|---|---|---|---|---|
| PIT | 14 | 29 | −15 | Warren / Dowdle |
| IND | 4 | 17 | −13 | Jonathan Taylor |
| JAX | 30 | 17 | +13 | Tuten |
| GB | 28 | 17 | +11 | Jacobs |
| CAR | 26 | 17 | +9 | Hubbard |
| KC | 8 | 17 | −9 | Ken Walker |

### 🚨 Do not apply the O-line blend as a multiplier on projections

Two independent reasons:

1. **Double-counting.** Clay built his rushing projections already holding a view of each offensive line. Multiplying his output by a score that is 50% his own grade applies that view twice.
2. **The gradient isn't in the data.** Clay's implied rushing yards per attempt for RBs with 150+ projected carries, by blended O-line tier: **4.39 / 4.45 / 4.38 / 4.32** — flat, with tier 2 highest. Applying a multiplier would impose a relationship his numbers do not contain.

The columns are therefore attached as **context only**, no projection was altered. Correct uses: filtering, tie-breaking between similarly-projected backs, and treating high-disagreement teams as elevated uncertainty. If the owner later wants a mechanical adjustment, apply it to a **copy** and keep unadjusted originals.

---

## 8. Method summary — how to rebuild in one pass

1. Owner uploads the season's projection guide PDF + O-line digest.
2. `pdftotext -layout`, locate the leaderboard section, parse per position on a name/team/N-numeric-columns regex. De-dupe on (name, team).
3. **Validate:** recompute the source's own point column under *its* scoring; confirm match within rounding. Abort if it doesn't.
4. Recompute all fantasy points from raw stats at league scoring. Ignore the source's point column.
5. Apply the source's own stated games-played haircut per player, scaled to each player's projected games (so partial-season players scale correctly).
6. Hunt article-format prop roundups; transcribe market lines into dicts keyed by player name; merge on normalized names.
7. Blend O-line sources via tie-averaged ranks; attach as context columns.
8. Sanity-check: market-vs-adjusted mean gap should be near zero. If it isn't, the haircut is wrong for that season.

---

## 9. Open items for the next refresh

1. **Bonus scoring is unmodeled** (§1). This is the largest known gap and the one most likely to distort draft valuation. Decide on an approach before the tool consumes these files.
2. **RB market coverage is the weak link** — 10 players, June, self-contradictory. Prioritize finding a better RB source; an August article grid like the WR one would fix it.
3. **TE market coverage is zero.** Confirm whether season-long TE props exist at all at any book; if they genuinely don't, stop looking and document it as a permanent constraint.
4. **Fit the games-played haircut per position** rather than using −2/−3, if market coverage ever supports it. Known to overshoot on QB.
5. **Owner named six books** (Hard Rock, FanDuel, ESPN BET, Underdog, BetMGM, Caesars) but the retrievable lines came from **DraftKings** and Caesars. Owner approved DraftKings retroactively. Confirm the book list again next year rather than assuming.
6. **PFF O-line provenance is a hand-pasted digest.** No way to verify it against source. Note it as owner-supplied, and ask for the article URL/date next time so the vintage is recorded.
7. **ETR remains inaccessible.** If the owner has a subscription, the fastest path is for them to paste the article text — Thorn's tiering is genuinely additive to PFF and Clay, and his methodology (third-down pass-pro weighted heaviest) is a different lens than either.
8. **Consider per-game columns** for weekly/DFS use. Present as `ppr_per_game_propadj` but not otherwise exploited.
