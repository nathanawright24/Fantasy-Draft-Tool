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

**Revised 2026-08-30 after VORP / cost-of-waiting analysis.** Three players were faded on
grounds the model could not see, and the pick-20 slot changed position entirely.

| player | position | pick_window | priority | tag | note |
|---|---|---|---|---|---|
| Amon-Ra St. Brown | WR | 5 | 1 | target | 128.0 vorp, Prime WR1, 11.3% injury, 33.8% boom, factor 38 |
| Puka Nacua | WR | 5 | 2 | target | 135.1 vorp if he falls. ADP 4, so roughly a coin flip at 5 |
| Jaxon Smith-Njigba | WR | 5 | 3 | target | 127.5 vorp, ADP 5, same cohort rates as Amon-Ra |
| Christian McCaffrey | RB | — | — | hard_avoid | **Hard avoid 09-05 (promoted from fade).** Same vorp as the WRs (126.3) but injury flag `Concerned`, 21.7% injury vs 11.3%, 20.0% boom vs 33.8%, factor 25 vs 38. The 3-game RB haircut is positional, so his own flag never reaches `ppr_base` |
| Brock Bowers | TE | 20 | 1 | target | **63.9 vorp, factor 36 — beats every WR available at 20.** Solves TE for the whole draft |
| Trey McBride | TE | 20 | 2 | target | 63.0 vorp, factor 40. Interchangeable with Bowers |
| DeVonta Smith | WR | 20 | 3 | target | 60.0 vorp, factor 32, Minimal Concern. Best WR at 20 on factors plus injury |
| Rashee Rice | WR | 20 | — | hard_avoid | **Owner read: aDOT too low, KC running more after the Ken Walker signing.** Corroborated independently — Breakout Candidate archetype at 29.6% bust / 18.2% boom vs 12.7% / 33.8% for Prime WR1, and only 5.88 bonus estimate despite 32 more vorp than DeVonta, which is the reception-heavy signature |
| Derrick Henry | RB | 20 | — | fade | 68.3 vorp but factor −1 and injury `Concerned` |
| Breece Hall | RB | 29 | 1 | target | **71.7 vorp, factor 21, 27.9% boom.** Jets risk acknowledged and accepted; Clay's projections already assume a bad offence and he still out-projects backs on better teams |
| Javonte Williams | RB | 29 | 2 | target | 58.7 vorp. The pessimistic-board fallback if Hall is reached |
| Jeremiyah Love | RB | — | — | hard_avoid | **Hard avoid 09-05 (promoted from fade).** 73.8 vorp but factor score 5 — the grid, which reads offensive PPG rank and QB grade, agrees with the owner about Arizona |
| Josh Jacobs | RB | 29 | — | fade | 58.8 vorp but factor −7 |
| Josh Allen | QB | — | — | hard_avoid | **Hard avoid 09-05 (promoted from fade). Owner: stop suggesting him in round 1.** A ±10 fade cannot remove a 68.6-vorp player, and the old row was window-scoped to 29 so it never applied at pick 5. Original ruling 08-30: cannot justify at 29 given the RB/WR disadvantage in rounds 2–3. Prefers 2 RB + 1 WR early with QB in the 8th or 9th |
| Cam Skattebo | RB | 44 | 1 | target | **43.7 vorp — the one back that clears the round 4–6 dead zone** (band mean 17.6 RB vs 32.0 WR). ADP 43 against pick 44, so a coin flip; budget for the fallback |
| Travis Etienne | RB | 44 | 2 | target | 41.9 vorp, ADP 44. The realistic fallback |
| Quinshon Judkins | RB | 44 | 3 | target | 28.9 vorp. Previously faded; reinstated as the pessimistic-board fallback since RB2 must land by 44 |
| Garrett Wilson | WR | 44 | 4 | target | 54.0 vorp if the RB slot is already filled |
| Terry McLaurin | WR | 53 | 1 | target | **39.5 vorp, highest in the 48–72 band.** Age fade reconsidered; see review notes |
| Rome Odunze | WR | 53 | 2 | target | Reach +13, Breakout Candidate |
| DJ Moore | WR | 53 | 3 | target | Reach +13 |
| Sam LaPorta | TE | 53 | — | watch | Target tag removed 08-24. **Moot if Bowers or McBride is taken at 20** |
| Matthew Stafford | QB | 101 | 1 | target | 14.7 vorp. Best QB after the cliff; ADP 95 lands at pick 101 |
| Brock Purdy | QB | 101 | 2 | target | 12.2 vorp, ADP 120 |
| Jalen Hurts | QB | 101 | 3 | target | Only if he slides; alt league saw QBs go 18.5 picks later than Sleeper rank |
| Trevor Lawrence | QB | 92 | — | fade | **Faded 08-30.** 27.9 composite, the weakest QB in his ADP neighbourhood. Stafford beats him by 5.2 |
| Justin Herbert | QB | 92 | — | fade | Same reason, 26.7 |
| Mark Andrews | TE | 125 | 1 | target | 2.3 vorp. TE2 is a bye-week body regardless of path |
| Dallas Goedert | TE | 125 | 2 | target | 9.4 vorp, ADP 121 |
| Jayden Higgins | WR | — | — | hard_avoid | **Torn ACL.** No ADP from either source; projections predate the injury |
| Jonathon Brooks | RB | — | — | hard_avoid | Largest Sleeper-later gap on the board (+61). Verify status before ever reinstating |

## Standing plan as of 2026-08-30

Picks are 5, 20, 29, 44, 53, 68, 77, 92, 101, 116, 125, 140, 149, 164, 173, 188.

**Shape:** WR at 5, TE1 at 20, RB1 at 29, RB2 at 44, WR at 53, WR at 68/77/92, QB1 at 101,
WR at 116, TE2 at 125, RB darts at 140/149/164, QB2 at 173, K at 188.

- **Five receivers land by pick 92**, meeting the round 9–10 target with a round in hand. That
  slack is what pays for a round-2 tight end.
- **Two real backs only**, at 29 and 44. Everything after is a dart at −30 vorp or worse
  against last-starter replacement. Accepting that is the same decision as taking Bowers.
- **W6 will fire** on a round-2 tight end. It was written for a plan being deliberately
  abandoned — turn it off in setup rather than overriding it sixteen times.
- **The decisive band is picks 20–68.** A turn costs 49–102 vorp there and 2.5–4.3 in rounds 7–8.
- **The pessimistic board costs 65.4 vorp**, almost all of it at picks 5, 29, 44 and 53.

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
