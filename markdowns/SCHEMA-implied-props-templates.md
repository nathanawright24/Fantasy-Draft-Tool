# Schema & Refresh Runbook — Implied Props Templates

Companion to `HANDOFF-implied-props-context.md`. Read that first for *why*; this doc is *what goes in each column*.

**Files:** `TEMPLATE_qb_implied_props.csv` (41 cols), `TEMPLATE_rb_implied_props.csv` (46), `TEMPLATE_wr_implied_props.csv` (43), `TEMPLATE_te_implied_props.csv` (39), `TEMPLATE_oline_blend_rankings.csv` (18).

Each template ships with **one example row** using real 2026 values, with `<-- EXAMPLE ROW, DELETE BEFORE USE` appended to the name field. Delete it before writing real data.

---

## Column families — the naming convention is load-bearing

| Prefix | Meaning | Blank means |
|---|---|---|
| `mkt_` | Real sportsbook over/under line | No book offers it, or none found |
| `proj_` | Analyst projection, full-season basis, no injury discount | Player absent from source |
| `adj_` | `proj_` after games-played haircut | — |
| `ppr_` | Fantasy points at league scoring, computed by us | Inputs unavailable |
| `ol_` | Team-level O-line context, joined on `team` | Team unmatched — investigate |

**Never populate a `mkt_` column with a projection, or vice versa.** The entire analytical value of the file depends on that boundary holding. If a value is derived or estimated, it goes in `proj_`/`adj_` and gets a `data_quality_note`.

---

## Shared columns (all four position files)

| Column | Type | Notes |
|---|---|---|
| `rank` | int | Position rank by `ppr_proj_propadj` descending. Recompute after any edit. |
| `player` | str | As printed by the projection source. Do not "correct" spellings — it breaks joins. |
| `team` | str | **Source's abbreviation, not standard.** Clay uses `ARZ BLT CLV HST JAX LV`. See mapping below. |
| `bye` | int | Bye week. Optional; blank if source omits it. |
| `games_projected` | float | Games the projection assumes. Usually 17; **lower for known absences.** Drives the haircut. |
| `mkt_book` | str | Book(s) behind the `mkt_` values. `NONE - <reason>` if uncovered. |
| `mkt_as_of` | date | `YYYY-MM-DD` the line was observed. **Required whenever any `mkt_` is populated** — lines move. |
| `proj_source` | str | e.g. `Mike Clay / ESPN, 17-game` |
| `proj_as_of` | date | Publication/update date of the projection source. |
| `propadj_method` | str | Human-readable haircut, e.g. `minus 2 games (WR)`. |
| `data_quality_note` | str | Per-row caveats: stale line, self-contradictory source, derived value, team-assignment conflict. |
| `ol_pff_rank` / `ol_pff_tier` | int | From the O-line file. |
| `ol_second_grade` / `ol_second_rank` | num | Second source's raw grade and its tie-averaged rank. |
| `ol_blend_score` / `ol_blend_rank` / `ol_blend_tier` | num | Blend outputs. |
| `ol_source_gap` | float | `pff_rank − second_rank`. Large magnitude = the two sources disagree = treat as uncertainty. |

### Team abbreviation mapping (Clay/ESPN → full name)

Non-obvious ones only: `ARZ`=Arizona, `BLT`=Baltimore, `CLV`=Cleveland, `HST`=Houston, `JAX`=Jacksonville, `LV`=Las Vegas. All others are conventional (`GB`, `KC`, `LAC`, `LAR`, `NE`, `NO`, `NYG`, `NYJ`, `SF`, `TB`, `WAS`, etc.).

---

## Position-specific stat columns

Owner's requested stats per position are all present. Columns marked **⚠ no market exists** will be permanently blank on the `mkt_` side — that is expected, not a gap to chase.

### QB
- `mkt_pass_yds` — the one QB market reliably retrievable.
- `mkt_pass_tds`, `mkt_rush_yds`, `mkt_rush_tds` — ⚠ rarely offered season-long; expect blank.
- `proj_pass_att`, `proj_comp`, `proj_pass_yds`, `proj_pass_tds`, `proj_int`, `proj_sacked`, `proj_rush_att`, `proj_rush_yds`, `proj_rush_tds`
- `adj_pass_yds`, `adj_pass_tds`, `adj_rush_yds`, `adj_rush_tds`
- `ppr_market_partial` — **named "partial" deliberately.** With only passing yards from the market, this is `pass_yds × 0.04` and nothing else. It is *not* comparable to `ppr_proj_propadj`. Do not rank on it.
- `mkt_minus_proj_propadj_pass_yds` — the QB-specific gap. Expect **positive** values; the −2 game haircut overshoots for QBs.

### RB
- `mkt_rush_yds`, `mkt_rush_tds`, `mkt_rec_yds` — occasionally available.
- `mkt_rush_att`, `mkt_receptions`, `mkt_rec_tds` — ⚠ effectively never offered.
- `proj_rush_att`, `proj_rush_yds`, `proj_rush_tds`, `proj_targets`, `proj_receptions`, `proj_rec_yds`, `proj_rec_tds`, `proj_carry_share`, `proj_target_share`
- Shares are decimals 0–1 (0.61 = 61% of team designed runs). Useful for role stability.
- **Haircut is 3 games for RB, not 2.**

### WR / TE
- `mkt_receptions`, `mkt_rec_yds`, `mkt_rec_tds` — all three available for WR; **none historically for TE.**
- `mkt_targets` — ⚠ **no sportsbook offers targets.** Permanently projection-only.
- `proj_targets`, `proj_receptions`, `proj_rec_yds`, `proj_rec_tds`, `proj_target_share`, plus WR rushing (`proj_rush_att/yds/tds` — small but real for gadget receivers).
- `ppr_market` — for WR this is a **complete** market PPR total, because PPR scores exactly receptions + yards + TDs. This is the cleanest apples-to-apples number in the whole dataset.
- `mkt_minus_proj_full17` and `mkt_minus_proj_propadj` — keep **both**. The first shows the games-convention artifact, the second shows real disagreement. Their difference is the diagnostic.

---

## Fantasy point formulas (league scoring)

```
QB   ppr = pass_yds*0.04 + pass_tds*5 + rush_yds*0.1 + rush_tds*6
RB   ppr = rush_yds*0.1 + rush_tds*6 + receptions*1.0 + rec_yds*0.1 + rec_tds*6
WR   ppr = receptions*1.0 + rec_yds*0.1 + rec_tds*6 + rush_yds*0.1 + rush_tds*6
TE   ppr = receptions*1.0 + rec_yds*0.1 + rec_tds*6
```

**No interception or fumble penalty is applied** — the owner's stated scoring didn't include one. If the league has them, add and re-document.

**🚨 Neither the 300-yard passing bonus nor the 100-yard scrimmage bonus is included, and neither can be computed from season totals.** See §1 of the handoff. These files understate QBs and boom-profile flex players against true league scoring.

### Games-played haircut

```
factor = max(games_projected - N, 0) / games_projected      # N = 3 for RB, 2 for QB/WR/TE
adj_<stat> = proj_<stat> * factor
```

Compute per player, not as a flat 15/17 — players with sub-17 projections (injury holdouts, suspensions) must scale off their own base or the haircut is wrong for them.

**Verify the haircut each season:** mean `mkt_minus_proj_propadj` across covered WRs should land near zero. In 2026 it was **+0.5 PPR (ratio 1.006)** across 50 receivers. If a new season comes out at ±15, the source changed its convention — investigate before shipping.

---

## O-line template

| Column | Notes |
|---|---|
| `ol_blend_rank` | 1–32, from `ol_blend_score`, ties get equal rank |
| `team` / `team_full` | Abbrev must match the player files' convention |
| `source_a_name` / `source_a_rank` / `source_a_tier` | The ordinal source (2026: PFF, 1–32) |
| `source_b_name` / `source_b_grade` / `source_b_grade_scale` | The graded source (2026: Clay, 1–10) |
| `source_b_rank` | Grade converted to rank position, **ties averaged** |
| `ol_blend_score` | Mean of `source_a_rank` and `source_b_rank` at the stated weights |
| `ol_blend_tier` | Quartiles of 8 |
| `ol_source_gap_a_minus_b` | The useful column. Large = disagreement = uncertainty flag |
| `blend_weights` / `tie_handling` | Document the method **in the data**, so nobody reverse-engineers it later |
| `source_a_as_of` / `source_b_as_of` | Vintage of each ranking |
| `notes` | Cross-source agreement, coaching changes, personnel not yet reflected |

**Method:** convert both sources to rank positions before averaging. Do **not** average a 1–32 ordinal against a 1–10 grade numerically — the scales are incommensurate and the ordinal will dominate.

**⚠ Coarse-grade warning:** if the graded source uses few distinct values (2026: only four), it sorts the league into buckets and the ordinal source decides all ordering *within* each bucket. Record this in `notes` rather than presenting the blend as more precise than it is.

**⚠ Do not multiply projections by O-line score.** Double-counts the projection source's own O-line view, and in 2026 the source's rushing efficiency showed no gradient across tiers (4.39/4.45/4.38/4.32 yds/att). Context only.

---

## Refresh runbook

1. **Ask the owner to upload** the current projection guide PDF and an O-line rankings digest (paste is fine). This is by far the highest-leverage step — the sandbox cannot reach any sports data host.
2. **Confirm scoring and the book list** haven't changed. Both changed for 2026.
3. Extract with `pdftotext -layout`; parse the leaderboard section per position.
4. **Validate the parse:** recompute the source's own fantasy-point column under *its* scoring assumptions (ESPN default = 4-pt pass TD, −2 INT, −2 fumble). Match within rounding ⇒ trust it. No match ⇒ stop.
5. Recompute points at league scoring from raw stats. **Ignore the source's point column** in output.
6. Read the guide's front matter for a stated prop-adjustment; apply per player.
7. Hunt article-format prop roundups (`"2026 <position> prop totals over under"`). Transcribe into name-keyed dicts.
8. Merge on normalized names: strip `Jr./Sr./II/III/IV`, lowercase, drop periods and apostrophes, collapse hyphens to spaces. **Print unmatched names and eyeball them** — silent join failures are the main correctness risk.
9. Build the O-line blend; join to player files on `team`. Assert zero unmatched teams.
10. Sanity-check the market-vs-adjusted mean gap (step in the haircut section above).
11. Fill `data_quality_note` for every stale, contradictory, or derived value. Future-you will not remember.
