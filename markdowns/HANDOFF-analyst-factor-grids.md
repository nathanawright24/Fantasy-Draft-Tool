# Handoff Context — Analyst Colour-Coded Factor Grids (QB / RB / WR / TE)

**Purpose:** everything established while parsing the 2026 sets, so next year's refresh is a one-hour job instead of a one-day job, and so the draft tool consumes these numbers without misreading what they are.

**Sibling docs:** `HANDOFF-draft-tool-context.md` (league settings, manager behaviour, tool spec) and `HANDOFF-implied-props-context.md` (Vegas/Clay valuation layer). This is a **third, independent input layer**: qualitative categoricals and cohort base rates. It is not a projection source and must not be blended with one — see §7.

**Supersedes** `parse_analyst_color_grid.py` and `SCHEMA-analyst-factor-grid.md` (both WR-only, deleted).

---

## 1. What these analyses are

An analyst takes the top N players at a position by **Underdog Fantasy ADP** and grades each on a fixed set of factors, one cell per factor, shaded green / yellow / orange / red. Each cell is judged against a printed threshold in an "Average" column. The colours are then counted and weighted **5 / 3 / −1 / −3** into a total he labels "Ceiling" (or, in three of the four 2026 sheets, "Legendary").

Two positions also carry an **archetype back-test**: the same buckets applied to a decade of historical ADP, with outcome rates. Those rates are the most valuable output of the whole exercise, because they are real probabilities rather than a ranking.

### 🚨 The composite score is not points

The "Ceiling" label invites exactly the wrong reading. It is a weighted count of colour judgments. Observed 2026 ranges:

| Position | Range | Cells graded |
|---|---|---|
| QB | −21 to +41 | 11 |
| RB | −27 to +54 | 12 or 13 |
| WR | −12 to +42 | 12 |
| TE | −14 to +40 | 12 |

Every output file calls it `factor_score` and carries the caveat in a column. **It is also not comparable across positions** — different factor counts, different cell totals, different scales.

### 🚨 The ADP basis is not your league

Underdog is best-ball: no waivers, no trades, 18 rounds, auto-optimised lineups. Your league is a 12-team full-PPR redraft with two flex spots and, new for 2026, 5-point passing TDs plus per-game bonuses at 300 passing yards and 100 scrimmage yards. Underdog ADP is a *market signal*, not a proxy for how your twelve managers behave. Use `adp_position_rank` as the analyst's frame of reference only.

The best-ball difference bites hardest on **injury risk**. Best-ball absorbs missed weeks automatically; your lineup does not. Any archetype with a high injury rate is worse for you than for the audience this analysis was written for.

---

## 2. File inventory

Per position: `{pos}_factor_grid_long.csv` (atomic, one row per player × factor), `{pos}_factor_scores.csv` (wide, with published-vs-recomputed validation), `{pos}_player_priors.csv` (**the table the draft tool reads**), `{pos}_factor_reference.csv` (thresholds, colour legend, tier legend). Plus `wr_archetype_hit_rates.csv` and `rb_archetype_hit_rates.csv` — TE and QB have none.

Tooling: `analyst_grid_pipeline.py` (extractor + validator), `configs_2026/{pos}_2026.json` (worked geometry and published counts for all four 2026 sets), and four `TEMPLATE_*.csv` schemas.

Row counts: QB 32 players × 11 factors, RB 36 × 13, WR 45 × 12, TE 32 × 12.

---

## 3. The four positions are structurally different

Do not assume one year's structure carries to the next, or one position's to another. 2026:

| | QB | RB | WR | TE |
|---|---|---|---|---|
| Players | 32 | 36 | 45 | 32 |
| Volume factors | 4 | 5 | 3 | 3 |
| Situational factors | 7 | 6 | 7 | 8 |
| Archetype row | ✗ | ✓ | ✓ | ✗ |
| Injury row | ✗ | ✓ | ✓ | ✓ |
| Graded cells | 11 | 13 (12 if purple) | 12 | 12 |
| Colour tier on headers | ✓ 5 tiers | ✗ | ✗ | ✓ 5 tiers |
| Archetype back-test | ✗ | ✓ 3 buckets | ✓ 4 buckets | ✗ |
| Threshold basis | **elite QBs** | cohort average | cohort average | cohort average |
| Total row labelled | "Legendary" | "Ceiling" | "Ceiling" | "Ceiling" then "Legendary" |

**The QB thresholds are elite benchmarks, not cohort averages** — the grids are titled "Based on Elite QBs." So a "No" means "not elite," not "below average." This is why the QB sheet turns red so fast below QB16, and it makes the QB score a different kind of measurement from the other three. Recorded as `threshold_basis` on every QB row.

### Volume thresholds: per-game vs season-paced

The analyst keeps two reference tables, a per-game "Average" and a season "Paced Average," and the grids do not use them consistently:

- **WR** uses per-game (10.7 targets, 7.2 receptions, 0.8 TD).
- **RB** uses paced (293.79 carries, 92.49 targets, 72.22 receptions, 365.90 touches, 16.73 TD).
- **TE** uses season totals (137.67 targets, 97.02 receptions, 9.49 TD).
- **QB** uses paced for three volume rows (576.50 attempts, 44.63 passing TD, 97.62 rush attempts) but prints the **per-game figure for Rushing TDs (0.32)** where the paced value is 5.36. A 0.32 threshold would make nearly every QB a "Yes," and the grid does not behave that way, so the printed number is probably a display error rather than the number applied. **Unresolved — ask.**

Other reference-table mismatches worth knowing: the RB grids print Yards per Touch as **5.82** while the reference table says **5.67**; the QB reference table includes an **ADP** row (average 8.22) that appears in no grid.

---

## 4. Colour legend and response vocabularies

| Colour | Points | Binary factors | WR secondary-option | Injury |
|---|---|---|---|---|
| green | +5 | Yes | Less | (unused) |
| yellow | +3 | Yes | Same | Minimal Concern |
| orange | −1 | ? | ? | Some Concern |
| red | −3 | No | More | Concerned |
| purple | **not counted** | — | — | — |

Asymmetries that shape the output, all worth understanding before quoting a score:

- **Green and yellow both mean "Yes."** The split is the analyst's confidence, not a different answer. A player can be all-yellow and still score well (Trevor Lawrence: eleven yellows, zero of anything else, score 33).
- **"?" is penalised (−1).** Unknown is treated as mildly bad, which systematically sinks rookies and players in new situations. This is the single biggest driver of divergence from ADP.
- **Green is never used for injury**, so the best available injury cell is worth +3.
- **The weights are steep and asymmetric** — green is +5 but orange only −1 — so score spread is driven mostly by green count.

### Archetype vocabularies are position-specific and not stable

- **WR 2026:** green = Prime WR1, yellow = Prime WR2, orange = Breakout Candidate, red = Trusty Veteran.
- **RB 2026:** purple = Prime Multi Time RB1, green = RB1s In Their Prime, red = Trusty Veteran, and **Breakout Candidate appeared as both yellow and orange** — six players one way, seven the other, a 4-point swing on an identical label.

Derive the mapping from observed colour/text pairs each year. Never carry one forward.

### The colour tiers (TE and QB only)

Both sheets colour the scoring-table header cells into five tiers with **no printed legend anywhere**. Captured as `tier_color` / `tier_rank` (purple 1, green 2, yellow 3, gold 4, red 5).

The tiers are *not* a clean function of the score — TE Warren at 26 is purple while TE Pitts at 26 is green; QB Stroud at −3 is gold while QB Dart at −3 is red. Something else is driving them, possibly a judgment the analyst didn't write down. Ask what they mean before leaning on them.

---

## 5. Validation, and what it caught

The scoring tables print per-player colour counts and a weighted total, which makes every cell checkable. Pass rates:

| Position | Validated | Failures |
|---|---|---|
| WR | 44 / 45 | 1 |
| RB | 33 / 36 | 3 |
| TE | 28 / 32 | 4 |
| QB | 27 / 32 | 5 |

**13 discrepancies, zero extraction errors.** The QB set included a duplicate screenshot of QB9–16, extracted independently: byte-identical. Failures split into two shapes, and the cell totals tell you which:

**Analyst dropped a cell** (published counts total fewer cells than the grid) — 8 cases. WR28 Watson (a phantom 13th yellow), RB12 Achane, RB23 Judkins, TE23 Hockenson, TE29 Freiermuth, TE31 Stowers, QB11 Mahomes, QB23 Darnold, QB24 Stroud, QB26 Young. In the TE set all three dropped cells were oranges, which looks like one repeated slip rather than three independent ones.

**One colour counted wrong** (cell totals match, one colour differs) — 5 cases. RB19 Skattebo, RB25 Irving, TE6 LaPorta, TE22 Schultz, QB8 Prescott. Each verified by cropping and enlarging the column.

Net effect on scores: mostly the published figure is **too high** (Achane 21→16, Judkins 2→−1, Stroud 0→−3, Young −14→−11, Prescott 29→27), occasionally too low (LaPorta 36→38, Skattebo 5→7). Every file keeps both numbers plus `factor_score_matches_published` and a `note` explaining the gap. **Use `factor_score_recomputed`.**

**One systematic table error:** in both hit-rate tables the pooled "Boomed" average fails to reconcile with its own columns (WR 23.33% published vs 24.44% pooled; RB 24.09% vs 23.64%). Every other row reconciles exactly. Use the per-archetype rates.

---

## 6. Archetype hit rates

### WR — 180 WRs in the top 36 of ADP, 2020 onward

| Outcome | Breakout (n=44) | Trusty Vet (n=36) | Prime WR1 (n=71) | Prime WR2 (n=29) |
|---|---|---|---|---|
| Returned on ADP (17+ PPR PPG) | 27.27% | 27.78% | **53.52%** | 37.9% |
| Got injured | 15.91% | **30.56%** | 11.27% | 13.8% |
| Boomed | 18.18% | 8.33% | **33.80%** | 31.0% |
| Busted (lost 12+) | 29.55% | 16.67% | **12.68%** | 31.0% |
| Fine (lost 1–11) | 27.27% | 25.00% | 22.54% | 17.2% |

### RB — 220 RBs in the top 20 of ADP, 2015 onward

| Outcome | Breakout (n=56) | Trusty Vet (n=60) | RBs in their Prime (n=104) |
|---|---|---|---|
| Returned on ADP | 42.86% | 33.33% | **46.15%** |
| Got injured | 17.86% | **21.67%** | 15.38% |
| Boomed | 19.64% | 20.00% | **27.88%** |
| Busted (lost 10+) | 19.64% | 16.67% | 20.19% |
| Fine (lost 1–9) | 19.64% | 28.33% | 18.27% |

Four things to hold onto:

1. **Cohort sizes are recoverable even though they aren't printed.** The smallest denominator making every percentage exact gives WR 44/36/71/29 = 180 and RB 56/60/104 = 220, both matching the stated populations. Strong confirmation the tables are internally consistent. Stored as `archetype_n_inferred`.
2. **The outcomes do not partition.** Boomed + busted + fine + injured leaves 9–20% unaccounted for in every WR column. They are overlapping labels, not a distribution. **Never normalise them to 1.**
3. **Definitions moved between positions.** WR busted = lost 12+, RB busted = lost 10+; WR fine = 1–11, RB fine = 1–9; the WR return threshold names 17+ PPR PPG, the RB one names no threshold. Cross-position comparison of these rates is invalid without adjusting. `outcome_definition` carries the verbatim text.
4. **RB has a fourth archetype with no base rate.** The grid assigns five RBs to *Prime Multi Time RB1* — Gibbs, Robinson, Cook, Achane, Kyren Williams, i.e. five of the top fifteen — but the back-test has only three buckets. Mapped to "RBs in their Prime" with `archetype_cohort_mapping = ASSUMED` on every affected row. Ask whether that tier is a subset of the prime cohort or something he never tested.

**TE and QB have no back-test at all.** Their `p_*` columns are deliberately blank, with the reason in `prob_basis`. Do not fill them by analogy to another position.

---

## 7. Integration rules for the draft tool

**Do:**
- Join on `player_name_normalized` + `position` + `nfl_team`. **`nfl_team` is blank in every file** — the grids never print team. Fill it from the implied-props files first, then join everything else.
- Use `p_boomed` / `p_busted` / `p_got_injured` as a **variance term**, not a point estimate. Two players with the same projection but different archetypes have genuinely different distributions, and that is what these numbers are for.
- Use `adp_minus_score_rank` as a divergence flag: positive means the factors like the player more than Underdog does.
- Weight injury flags more heavily than the source does, per the best-ball point in §1.

**Do not:**
- Treat `factor_score` as points, blend it with `ppr_*` columns, or compare it across positions.
- **Treat the factor score as independent of Clay's projections.** Volume factors are three to five of the twelve cells and are presumably derived from projections; team pass attempts, offensive PPG rank, and O-line rank overlap with what the props files already carry. Blending double-counts. The non-overlapping content is the efficiency and role factors (YPRR, Reception Perception, route participation, in-line %, deep-ball rate, neutral pace, DVOA), the archetype, and the injury flag — that is where the incremental signal lives.
- Apply archetype probabilities as if player-specific (`prob_is_player_specific = N` everywhere).
- Assume the O-line factors here agree with `oline_blend_rankings.csv`. Different constructs. Disagreement is an uncertainty flag, not an error.
- Read the colour tiers as a score bucketing. They aren't (§4).

### Largest 2026 divergences, by position

*Factors above ADP:* **WR** — Waddle (WR21 → 6th), M. Wilson, Pittman, B. Thomas. **RB** — Bucky Irving (RB25 → 10th), Corum, Monangai, R.J. Harvey. **TE** — Freiermuth (TE29 → 14th), T. Ferguson, Kincaid, Gadsden. **QB** — Bo Nix (QB15 → 4th), Stafford, D. Jones, Tua.

*ADP above factors:* **WR** — McMillan (WR19 → 35th), A.J. Brown, Olave. **RB** — Derrick Henry (RB11 → 26th), Josh Jacobs, Etienne, J. Taylor. **TE** — Strange, Goedert, Kittle. **QB** — Jayden Daniels (QB4 → 18th), Jaxson Dart, Lamar Jackson.

Read these as hypotheses, not conclusions. Much of it is mechanical: the "?" penalty sinks rookies and new situations, the RB sheet has an explicit Age factor with no WR equivalent, and the QB sheet grades against elite benchmarks so anyone short of elite volume gets buried. Daniels and Lamar falling is the model docking rushing quarterbacks on passing-volume factors — which, given your **new 5-point passing TD and 300-yard bonus**, may actually be pointing at something real for your league specifically. That is worth a closer look rather than a dismissal.

---

## 8. Annual runbook

The whole thing is roughly an hour if the images arrive complete.

1. **Ask the owner for:** the analyst's name, the date of the analysis, and the Underdog ADP snapshot date. These are invisible in a screenshot and every file has fields waiting for them.
2. **Probe geometry per image.** `python analyst_grid_pipeline.py --probe IMG_xxxx.jpeg`. Column boundaries should read `[label edge, Average edge, one per player]`. Row boundaries split into a volume block and a situational block, each preceded by a header row and a names row that must **not** be graded. The label-column scan is more reliable than the voted scan, because two same-coloured neighbouring cells leave no detectable edge in a data column.
3. **Sample the palette.** `--sample IMG_xxxx.jpeg --position RB`. The analyst changes his fills between positions and between years — 2026 RB used a salmon red and a light green found nowhere else, and that salmon sits *closer to amber than to pure red*, so nearest-neighbour classification is only safe while every variant is enumerated. Any fill reported with distance > 2000 means add the variant; do not widen tolerances.
4. **Write the config.** Copy `configs_2026/{pos}_2026.json` and edit. `vy` needs one more boundary than there are volume rows; `sy` likewise. Transcribe the published counts and totals from the scoring table by eye — that is the only hand transcription in the process, and it is self-checking because the arithmetic must close.
5. **Extract and validate.** `--extract --position WR --config wr_2027.json`. Read failures by shape: clustered = your geometry is off by a row, fix it; isolated = the analyst erred, keep both numbers and note it. The script warns when failures exceed 20%.
6. **Crop and eyeball every isolated failure** before writing a note. Three of the five 2026 colour disagreements were only resolvable by enlarging the column.
7. **Derive the archetype colour mapping** from observed pairs, then build the four CSVs into the templates.
8. **Recover archetype denominators** from the percentages and confirm they sum to the stated population.
9. **Leave `nfl_team` blank.** Join it downstream from the props files. Do not fill it from model recall — the Bijan/Brian Robinson collision documented in the sibling handoffs corrupted an earlier analysis exactly this way.

### Reusable findings

- **Read fill colour, not text.** These grids carry half their information in colour, the text vocabulary is three values fully determined by colour, and OCR on a 150px cell is a coin flip between "?" and "7". Colour gives you the response and the score weight in one read.
- **Take the mode over cell pixels, and gate it on match distance.** The mode ignores text (a minority of pixels) and, unlike a median, is not dragged into a neighbouring row by slight misalignment. But anti-aliased text produces halo pixels, and in a cell with long bold text ("Breakout Candidate," "Minimal Concerns") those halos can outvote the fill and land on the *wrong* palette variant. Discarding pixels that aren't near-exact matches fixes it. This bug appeared only after merging four positions' palettes into one, and only in the long-label cells — a good reminder that a passing test on one position doesn't generalise.
- **Published counts are a per-cell audit, and they are worth more than they look.** They caught 13 source errors and confirmed 141 of 145 player columns. Never skip step 5.
- **When a duplicate screenshot appears, extract it separately and diff.** Free verification.

---

## 9. Open items

1. **Analyst identity and date** — unknown for all four sets.
2. **QB Rushing TDs threshold** — printed as the per-game 0.32 where the paced value is 5.36. Which was applied?
3. **RB Yards per Touch** — 5.82 in the grids, 5.67 in the reference table.
4. **What do the TE and QB colour tiers mean?** Five tiers, no legend, not a function of the score.
5. **Is Prime Multi Time RB1 a subset of "RBs in their Prime,"** or an untested tier?
6. **Why does "Ceiling" become "Legendary"** in three of the four sheets? Cosmetic, or a different intended metric?
7. **Are TE/QB back-tests coming?** Without them those positions have no probabilities at all.
8. **Per-game vs season basis** for the volume rows should be stated per position rather than inferred from magnitude.
9. **What sources feed each factor?** PFF, Reception Perception, QBR, and DVOA are named; the projection source behind the volume rows is not, and that determines how much this double-counts Clay.
10. **Coverage stops where it should.** These sets cover the top 32–45 per position, which runs past where your league's decisions are actually made at TE and QB and stops short of nothing that matters. No action needed — just don't expect the files to answer late-round questions.

---

## 10. Prompt template for next year

```
I'm uploading screenshots of the annual colour-coded analyst factor grids for
[QB/RB/WR/TE]: one image per block of players, a scoring-summary table, an averages
reference table, and possibly an archetype hit-rate table. Treat all images for a
position as ONE analysis.

Context: HANDOFF-analyst-factor-grids.md, analyst_grid_pipeline.py, configs_2026/,
and the four TEMPLATE_*.csv files. Parse into those templates exactly - don't invent
columns. Read §8 (runbook) and follow it.

Non-negotiables:
1. Read cell fill colour PROGRAMMATICALLY with the pipeline. Not OCR, not by eye.
   Run --sample first: the palette changes between positions and years.
2. VALIDATE against the published colour counts and the 5G+3Y-1O-3R arithmetic before
   building anything. Report the pass rate. Clustered failures = my geometry is wrong;
   isolated failures = the analyst erred, so keep BOTH numbers and flag it. Crop and
   enlarge every isolated failure before you write the note.
3. Don't assume last year's structure. Check the factor list, cell count, whether an
   archetype or injury row exists, whether thresholds are cohort averages or elite
   benchmarks, and whether volume rows are per-game or paced.
4. Derive the archetype colour mapping from observed pairs this year.
5. Recover archetype cohort sizes from the exact percentages; confirm they sum to the
   stated population.
6. Leave nfl_team blank - the grids don't print it and I don't want it guessed.
7. The composite score is ORDINAL. Never call it points, never blend it with ppr_*.
8. Flag any factor overlapping what Clay's projections already contain.
9. Ask me for the analyst name, the analysis date, and the ADP snapshot date.

Deliver: the filled templates, a config JSON for the pipeline, a list of what failed
validation with the resolution for each, and anything you had to assume.
```
