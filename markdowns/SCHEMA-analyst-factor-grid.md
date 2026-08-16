# Schema & Runbook — Analyst Colour-Coded Factor Grid

**What this covers:** the 2026 WR analysis delivered as eight screenshots (five factor grids covering WR1–WR45, one archetype outcome table, two scoring-summary tables), parsed into tidy CSVs.

**Sibling docs:** `HANDOFF-draft-tool-context.md` (league settings, manager behaviour, tool spec) and `HANDOFF-implied-props-context.md` (Vegas/Clay valuation layer). This analysis is a **third input layer**: qualitative categoricals and cohort hit rates, structurally different from both.

---

## 1. What the source analysis actually is

An analyst took the top 45 WRs by **Underdog Fantasy ADP** and scored each on twelve colour-coded cells: three volume factors, seven situational factors, plus an archetype label and an injury flag. Each cell is judged against a stated cohort average (the "Average" column) and shaded green / yellow / orange / red. The colours are then counted and weighted **5 / 3 / −1 / −3** into a total the analyst labels "Ceiling."

Separately, the analyst back-tested the four archetypes against 180 WRs drafted inside the top 36 WRs of ADP since 2020, producing hit rates for five outcomes.

**The archetype table is the genuinely valuable half.** It supplies real base-rate probabilities. The factor grid supplies an ordinal score.

### 🚨 "Ceiling" is not a point projection

The label invites exactly the wrong reading. The number is a weighted count of colour judgments; its observed range is **−12 to +42**, and a WR45 can score below zero. It cannot be compared to, added to, or blended with any `ppr_*` column in the props files. Every output file here calls it `factor_score` and carries that caveat in a column.

### 🚨 The ADP basis is not your league

Underdog is **best-ball**, half-PPR-ish scoring conventions aside, with no waivers, no trades, and 18-round drafts. Your league is a 12-team full-PPR redraft with 2 flex spots and, new for 2026, 5-point passing TDs and two per-game bonuses. Underdog ADP is a *market* signal, not a proxy for how your twelve managers will draft. Use `adp_position_rank` as the analyst's frame of reference, not as your league's ADP.

---

## 2. Files

| File | Grain | Rows |
|---|---|---|
| `wr_factor_grid_long.csv` | player × factor | 540 |
| `wr_factor_scores.csv` | player, wide, with published-vs-recomputed validation | 45 |
| `wr_player_priors.csv` | **the consumable table** — categoricals + probabilities | 45 |
| `wr_archetype_hit_rates.csv` | archetype × outcome, plus pooled averages | 25 |
| `wr_factor_reference.csv` | factor definitions, thresholds, colour legend | 12 + legend |
| `parse_analyst_color_grid.py` | reusable extractor | — |
| `TEMPLATE_*.csv` (4) | empty schemas for the next refresh | header + example |

`wr_player_priors.csv` is what the draft tool should read. The others are provenance and audit.

---

## 3. Colour legend and response vocabulary

| Colour | Points | binary factors | Secondary-option factor | Archetype | Injury |
|---|---|---|---|---|---|
| green | +5 | Yes | Less | Prime WR1 | *(unused)* |
| yellow | +3 | Yes | Same | Prime WR2 | Minimal Concern |
| orange | −1 | ? | ? | Breakout Candidate | Some Concern |
| red | −3 | No | More | Trusty Veteran | Concerned |

Note the asymmetries, because they drive the score's behaviour:

- **Green and yellow both mean "Yes."** The distinction is the analyst's confidence or margin, not a different answer. A player can be all-yellow and still score 36.
- **"?" is penalised (−1).** Unknown is treated as mildly bad, which systematically punishes rookies and players in new situations. Eight of the eleven Breakout Candidates carry three or more orange cells.
- **The weights are steep and asymmetric.** Green is worth 5 but orange only −1, so the spread between a good and a bad player is driven mostly by how many greens they collect.
- **Green is never used for injury**, so the best available injury cell is worth +3, not +5.

### The twelve factors and their thresholds

| Factor | Group | Cohort average |
|---|---|---|
| Targets | volume | 10.7 |
| Receptions | volume | 7.2 |
| Touchdowns | volume | 0.8 |
| Offensive Rank in PPG | situational | 8.94 |
| Quarterbacks Rank in PFF Passing Grade | situational | 10.36 |
| Team Pass Attempts | situational | 594.94 |
| Highest Targeted Secondary Option | situational | 103.31 |
| Offensive Line Rank in Pass Blocking | situational | 10.75 |
| Rank in Yards per Route Run | situational | 4.81 |
| Highest % Achieved in Reception Perception | situational | 90th |

Targets / receptions / touchdowns read as per-game figures (10.7 targets and 7.2 receptions per game are plausible top-36 averages; 10.7 targets per season is not). **Not stated in the source — confirm with the analyst.**

---

## 4. Archetype hit rates

Population: 180 WRs drafted inside the top 36 WRs of ADP, 2020 onward. Rates as published:

| Outcome | Breakout Candidate | Trusty Veteran | Prime WR1 | Prime WR2 |
|---|---|---|---|---|
| Returned on ADP (17+ PPR PPG) | 27.27% | 27.78% | **53.52%** | 37.9% |
| Got injured | 15.91% | **30.56%** | 11.27% | 13.8% |
| Boomed (18+ PPG or beat ADP by 10) | 18.18% | 8.33% | **33.80%** | 31.0% |
| Busted (lost 12+ spots) | 29.55% | 16.67% | **12.68%** | 31.0% |
| Fine (lost 1–11 spots) | 27.27% | 25.00% | 22.54% | 17.2% |

**Cohort sizes are not printed but are exactly recoverable** from the percentages: Breakout 44, Trusty Veteran 36, Prime WR1 71, Prime WR2 29 — summing to 180, which confirms both the reverse-engineering and the stated population. Those denominators are in `archetype_n_inferred`.

Three things to hold onto:

1. **The outcomes do not partition.** Boomed + busted + fine + injured leaves 9–20% unaccounted for in every column. They are overlapping labels, not a probability distribution. Do not normalise them to 1.
2. **Prime WR1 dominates on every axis** — highest return rate, highest boom rate, lowest bust rate, near-lowest injury rate. The archetype is doing a lot of work, and it is assigned by the same analyst who assigns the colours, so it is not an independent signal from the factor score.
3. **Trusty Veteran is an injury story.** 30.56% injury rate against 11.27% for Prime WR1. Its low boom rate (8.33%) follows from that. In your league this matters more than in best-ball, because you have no automatic best-lineup mechanism to absorb missed weeks.

The four archetypes are unevenly represented in this year's top 45 — 18 Prime WR1, 11 Breakout Candidate, 9 Prime WR2, 7 Trusty Veteran — a different mix from the historical cohort (71/36/29/44 scaled). Worth noting before assuming the base rates transfer cleanly.

---

## 5. Validation

The scoring tables print each player's colour counts and total, which makes the extraction fully checkable. **44 of 45 player columns reconcile exactly** on all four counts and on the arithmetic 5G + 3Y − 1O − 3R.

**Two source inconsistencies found, both preserved rather than silently fixed:**

1. **Christian Watson (WR28).** The scoring table reports 7 yellows, but his grid column contains 12 graded cells totalling 0 green / 6 yellow / 4 orange / 2 red. Seven yellows would make 13. His published score of 11 is consistent with the erroneous count of 7; the grid yields **8**. Both numbers are in `wr_factor_scores.csv` (`factor_score_published` = 11, `factor_score_recomputed` = 8, `factor_score_matches_published` = N). The recomputed figure is the defensible one. Every other player has exactly 12 cells, so this is an isolated arithmetic slip, not a systematic one.
2. **The "Boomed" average.** Published as 23.33% (42/180), but the four archetype columns pool to 44/180 = 24.44%. Every other row's average reconciles exactly. Flagged in the `note` column. Use the per-archetype rates, which are internally consistent, and disregard the pooled boom average.

Neither undermines the analysis. They are the kind of thing worth knowing before the numbers get quoted back at you in a draft room.

---

## 6. Integration rules for the draft tool

**Do:**
- Join on `player_name_normalized` + `position` + `nfl_team`. `nfl_team` is **blank in these files** — the grids do not print team. Fill it from `wr_implied_props.csv` before joining anything else.
- Use `p_boomed` / `p_busted` / `p_got_injured` as archetype-level priors — a variance term, not a point estimate. Two players with the same projection but different archetypes have genuinely different distributions.
- Use `adp_minus_score_rank` as a divergence flag: positive means the factors like the player more than Underdog ADP does.
- Treat `injury_concern` as a modifier on availability, and remember your league has no best-ball safety net.

**Do not:**
- Blend `factor_score` with `ppr_*` columns, or treat it as points. Different unit, different meaning.
- Treat the factor score as independent of Clay's projections. Targets, receptions, and touchdowns are three of the twelve cells and are presumably derived from projections; team pass attempts, offensive PPG rank, and O-line rank overlap with what the props files already carry. **Blending the two double-counts.** The non-overlapping content is YPRR, Reception Perception, the secondary-option factor, the archetype, and the injury flag — that is where the incremental signal lives.
- Apply the archetype probabilities as if player-specific. `prob_is_player_specific = N` is in every row for a reason.
- Assume the O-line factor here agrees with `oline_blend_rankings.csv`. This analyst uses a pass-blocking rank with a threshold of 10.75; the blend file is a different construct. Where they disagree, that is an uncertainty flag, not an error.

### Where the two frameworks disagree most

Largest positive divergences — factors above ADP: **Jaylen Waddle** (WR21, score 36, 6th by factors), Michael Wilson (WR44 → 28th), Michael Pittman Jr. (WR45 → 30th), Brian Thomas Jr. (WR31 → 18th), Quentin Johnston (WR36 → 27th).

Largest negative — ADP above factors: **Tetairoa McMillan** (WR19, score 4, 35th by factors), A.J. Brown (WR7 → 16th, driven by the Trusty Veteran red), Chris Olave (WR13 → 21st), Terry McLaurin (WR22 → 29th), Jordyn Tyson (WR32 → 41st).

Note how much of this is mechanical: the model punishes "?" cells, so rookies and players in new offences sink, and A.J. Brown's drop is almost entirely his archetype cell. Read these as hypotheses to check, not conclusions.

---

## 7. Open items

1. **Analyst identity and date are unknown.** Every file carries `analyst` and `source_date` as `UNKNOWN — owner to supply`. Fill these in; the props handoff already learned this lesson with the hand-pasted PFF digest.
2. **Are targets/receptions/TDs per game or per season?** Assumed per game. Confirm.
3. **What is the source of each factor?** PFF, Reception Perception, and team stats are named; the projection source behind targets/receptions/TDs is not. This determines how much the factor score double-counts Clay.
4. **Is the Underdog ADP snapshot dated?** ADP moves through August. Without a date, `adp_minus_score_rank` has an unknown vintage.
5. **Only WRs so far.** RB, TE, and QB versions of this grid would slot into the same schema unchanged. Ask whether they exist.
6. **The 2026 scoring changes are invisible here.** The factor grid was built for a best-ball market under standard scoring. It contains nothing about 100+ scrimmage-yard bonus frequency, which is exactly the per-game volatility the archetype table's boom/bust framing gestures at without measuring. A high-target alpha WR gains more from your new bonuses than the factor score can see.

---

## 8. Prompt template for future chats

Paste this, with the images, into a fresh chat to reproduce this pipeline for another position or season.

```
I'm uploading screenshots of a colour-coded analyst factor grid (one image per block of
players, plus a scoring-summary table and possibly an archetype hit-rate table). Treat all
images as ONE analysis.

Context files: HANDOFF-draft-tool-context.md, HANDOFF-implied-props-context.md,
SCHEMA-analyst-factor-grid.md, and the four TEMPLATE_*.csv files. Parse into those
templates exactly — do not invent columns.

Method, in this order:
1. Read the colour of each cell PROGRAMMATICALLY, not by eye. Use
   parse_analyst_color_grid.py: probe each image for grid boundaries, fill in SPEC, extract.
   Colour carries as much information as text in these grids and OCR discards it.
2. Derive the text response from the colour using RESPONSE_MAPS. Verify the mapping holds
   on a few cells you can read, then trust it.
3. VALIDATE before building anything: the scoring table prints per-player colour counts and
   a total. Require an exact match on every player, and on 5G + 3Y - 1O - 3R = total.
   Report the pass rate. Clustered failures mean the geometry is off by a row; an isolated
   failure means the analyst made an arithmetic error -- preserve both numbers and flag it,
   don't silently pick one.
4. Reverse-engineer any missing denominators (archetype cohort sizes) from the exact
   percentages, and cross-check that they sum to the stated population.
5. Key every row on name + position + NFL team. If the grid does not print team, leave it
   blank and say so -- do not guess from memory.

Hard rules:
- The composite score is ORDINAL. Never call it points, never blend it with ppr_* columns.
- Note the ADP basis (Underdog is best-ball) and don't treat it as our league's ADP.
- Flag any factor that overlaps what Clay's projections already contain, so the draft tool
  doesn't double-count.
- Carry analyst name and source date on every row; ask me for them if not visible.

Deliver: the filled templates, plus a short list of what failed validation and what you had
to assume.
```
