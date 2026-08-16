# Macro Roster-Construction Guardrails — 2026

**Purpose:** the owner's stated draft intent, converted into warning rules the draft tool can evaluate at each pick. Enforcement level per owner: **loud warnings, always overridable.** No hard blocks anywhere.

**Inputs:** owner's answers to nine strategy questions (§1), the third-party strategy data in this folder, the four positional factor grids, and the league settings in `HANDOFF-draft-tool-context.md`.

---

## 1. Stated intent

| Dimension | Owner's position |
|---|---|
| RB shape | Board-driven, but **1–2 trustworthy-with-upside RBs by Rd 4**; skip the deadzone; **RB3 of RB2 quality by Rd 8/9**; then volume shots **from Rd 11 onward** |
| Flex | **Both flex = WR.** Reasoning: bigger game-breaking potential, and WR25–40 outscores RB25+ |
| Rd 1–4 risk | **Maximize league-winner upside, accept the bust tail** |
| QB | **Wait to the Lawrence tier, ~Rd 8/9.** Passing on Josh Allen — the elite skill-player cliff is too sharp to spend a Rd 2 pick on a QB |
| TE | **Target Sam LaPorta in Rd 5.** Then **double up** with a serviceable TE2 in the **early double-digit rounds** (Kincaid tier) |
| Late RB shots | **Independent backs with standalone paths**, not handcuffs |
| Rd 8–11 | **Not a squeeze.** Owner reads this band as QB-rich with a TE double-up available. RB3 also lands here |
| Roster target | **6 WR / 5 RB / 2 TE / 2 QB / 1 K = exactly 16.** QB2 late; TE2 serviceable in early double digits; K Rd 15–16 |
| Tool strictness | Loud warnings, overridable |

---

## 2. League settings this is built against

12-team snake redraft, **16 rounds**. Starters: `1 QB / 2 RB / 2 WR / 1 TE / 2 FLEX` plus a kicker — 9 starting slots, 7 bench. Flex eligibility RB/WR/TE, but a TE in the flex happens only when a roster is in trouble. No team defenses. **Full PPR** (pre-existing), plus three items new for 2026: **5-point passing TDs**, **+3 for 100+ scrimmage yards**, **+3 for 300+ passing yards**.

### The roster math works exactly

| | Drafted | Starting | Bench |
|---|---|---|---|
| QB | 2 | 1 | 1 |
| RB | 5 | 2 | 3 |
| WR | 6 | 2 + **2 flex** | 2 |
| TE | 2 | 1 | 1 |
| K | 1 | 1 | 0 |
| **Total** | **16** | **9** | **7** |

**Exactly 16 picks, no slack.** Every pick is committed, which means any deviation displaces something named — that is what the warning rules in §4 exist to surface.

**Market timing the owner is drafting against:** backup QBs and TEs start filling from **Rd 11 onward**; kickers do not run until **at least Rd 14**. So K at 15–16 is safe, and the QB2/TE2 windows are contested from 11.

---

## 3. Round-by-round plan

**Rd 1–2 — Upside, and accept the tail.** Owner wants maximum league-winner equity here. Both positions sit near 12.5–12.7% league-winner in this band; the difference is the downside. RB busts at 21.8% and carries the catastrophic tail almost exclusively; WR busts at 18.8%. Take the upside swing, but know which position is supplying the variance.

**Rd 3–4 — The dead spot. Second trustworthy RB lands here if it lands at all.** RB 5.3% league-winner / 18.4% bust, WR 5.6% / 14.8%. All of the risk, none of the upside — the worst risk-adjusted band in the draft for both positions. Do not reach here; take the RB2 only if the board hands one over.

**Rd 5 — TE1 (LaPorta), or WR.** This is the one round where the owner has a specific target. If LaPorta is gone, this becomes a WR pick, not a reach at TE.

**Rd 6 — WR. No RBs.** The deadzone window (ADP ~48–72). Every WR in it projects 11.7–13.3 PPG; every RB projects 11.1–12.4. WR here is 10.7% league-winner against **5.4%** bust — the only band in the draft where upside doubles downside.

**Rd 7–9 — RB pivot, plus QB1. This is the hinge.** WR league-winner collapses to 3.1%; RB holds at 11.1%, nearly its Rd 1–2 rate. The positions have completely swapped. RB3 and QB1 both belong here, and the owner reads the band as deep enough at QB to fit both. Four purple-tier arms sit in range — Herbert 39, Bo Nix 39, Stafford 37, Lawrence 33 — which is what makes that read defensible.

**Rd 10–11 — TE2 and the last WR before the cliff.** Owner wants a *serviceable* TE2 here, not a corpse, and the Kincaid tier is the target. The WR cliff sits at ADP ~137 (~WR50); take WR depth through the flex plus one or two more before it, then stop. Note the competition: backup TEs and QBs start coming off the board from Rd 11, so a TE2 in Rd 12+ is a materially worse TE2.

**Rd 11–14 — Independent RB darts.** This is what "late" means. Two to three slots depending on how many RBs went by Rd 4.

**Rd 13–15 — QB2.** Late by design. The band is contested from 11 onward, so this is the pick most likely to get squeezed by an RB dart.

**Rd 15–16 — K.** Kickers do not run until Rd 14 at the earliest, so waiting is a small real edge.

---

## 4. Warning rules for the tool

Each fires as a visible warning at the pick, never a block.

| # | Trigger | Warning | Severity |
|---|---|---|---|
| W1 | RB selected in Rd 5–6 | Deadzone. Every WR in this ADP window out-projects every RB in it. Owner's plan is WR-only here. | **High** |
| W2 | 3rd RB taken before Rd 7 | Plan is 1–2 RBs by Rd 4, next at Rd 7+. A 3rd early RB means catching up at WR/QB/TE. | **High** |
| W3 | QB taken before Rd 7 | Owner passed on Allen deliberately. But see §5.2 — the new scoring cuts against waiting, so this warning is *informational*, not a scold. | Medium |
| W4 | No QB rostered entering Rd 10 | QB1 window closing. Owner's plan puts QB1 in the Rd 7–9 band. | **High** |
| W5 | Entering Rd 12 with only 1 TE | TE2 window is Rd 10–11 while serviceable options remain. Backup TEs fill from Rd 11 onward. | Medium |
| W6 | TE taken in Rd 2–4 | Elite TE gains only ~+12 pts from the new scoring — the weakest gain of any group. Rd 5 target price is right; earlier is not. | Medium |
| W7 | Fewer than 4 WR rostered entering Rd 8 | Two flex spots need WR bodies. Weekly league-wide demand is 24 WR starters plus 24 flex slots. | **High** |
| W8 | WR taken after ADP ~137 (the cliff) | Past the last WR projected for 10+ PPG. Spend on RB upside or a 2nd TE instead. | Medium |
| W9 | RB taken as a handcuff to own RB1/RB2 | Owner specified independent backs with standalone paths. | Low |
| W10 | Position count would exceed target (>6 WR, >5 RB, >2 TE, >2 QB) | Over target. The build is exactly 16 picks, so name the slot this displaces. | Medium |
| W13 | Entering Rd 15 with no QB2 | QB2 is contested from Rd 11. Last realistic window. | Medium |
| W14 | K taken before Rd 14 | Kicker run does not start until Rd 14+. Pick is better spent on an RB dart. | Medium |
| W15 | RB dart taken before Rd 11 that isn't the RB3 | Owner's dart window is Rd 11+. Earlier RB volume competes with TE2 and WR6. | Low |
| W11 | Player's `factor_score` bottom-quartile at position AND ADP-vs-score divergence negative | Both the market and the analyst's factors dislike this pick. | Medium |
| W12 | Rd 7–9 pick spent on WR when no RB3 rostered | Last RB league-winner window (11.1% vs WR 3.1%). | **High** |

**Deliberately not a rule:** nothing enforces taking an RB in Rd 1–2. Owner's shape is board-driven and an elite WR start is compatible with every downstream guardrail here.

---

## 5. Where the plan needs a decision

### 5.1 Rounds 8–11 are not the squeeze — Rounds 11–15 are

An earlier draft of this document treated 8–11 as oversubscribed. The owner's read is that the band is QB-rich and also supports the TE double-up, and the factor grids back that: four purple-tier QBs sit in range, so QB1 is genuinely replaceable *within* the band rather than a one-shot. RB3 and QB1 both fit.

**The actual congestion is Rounds 11–15**, where five commitments chase five picks: two to three RB darts, QB2, and the tail of WR depth, with the kicker landing at 15–16. Since the build is exactly 16 picks with no slack, the tool should track this window explicitly. **The pick most likely to get squeezed is QB2**, and the cost of losing it is streaming a bye week rather than losing a starter — so it is the correct thing to sacrifice if a dart is too good to pass.

### 5.2 🚨 The QB timing plan predates this league's scoring

This is the most important flag in the document, and it comes from the owner's own prior analysis rather than from me. `HANDOFF-draft-tool-context.md` concluded:

> The original advice — *"skip the round 3 QB run; a round 7–9 QB costs you almost nothing"* — **was calibrated to the old scoring and should not be carried into 2026 unexamined.** With the QB spread widening, the mid-round QB desert becomes a genuine risk rather than an exploitable inefficiency.

Relative gain from the new scoring runs **QB >> RB > WR ≈ TE**, and the QB1-to-QB12 gap widens roughly twice as much as the RB1-to-mid-RB gap. The suggested 2026 defaults have **5–6 QBs going in picks 40–99** — exactly the stretch the owner intends to sit through — against 4 historically.

**Counterweight, also from the handoff:** the double-dip does *not* vault rushing QBs past elite pocket passers, and it **compresses the middle of the QB range**, which favors the cheap mid-round QB proportionally more than the Rd 3 elite arm. So the two effects genuinely oppose each other.

**Net read: the Rd 8/9 target is defensible but has less margin than it did last year.** The owner's own reasoning — the elite skill cliff is too sharp to spend a Rd 2 pick — is sound and independently supported: alpha WRs gain the least from the new rules, but PPR plus two flex spots still makes WR depth correct. The recommendation is not to change the plan but to **move the trigger earlier**: treat Rd 7 as the QB window opening rather than Rd 8/9, and let W4 fire a round sooner if the observed QB pace is running hot. The tool should re-estimate QB pace from live picks after ~20 selections; that beats any prior.

### 5.3 The TE double-up is a deliberate choice, and it needs a timing rule more than a challenge

Owner's plan: LaPorta in Rd 5, then a **serviceable** TE2 in the early double-digit rounds — the Kincaid tier, mirroring last year's build. Both targets check out independently against the TE grid: LaPorta is purple tier and scored **38** once the analyst's own arithmetic error is corrected (third of 32), and Kincaid is purple tier with the largest positive ADP-vs-factors divergence at the position, TE12 by ADP against 5th by factors.

The consideration is not whether to double up but **when**. Backup TEs start filling from Rd 11 onward, and the gap between a Rd 10 TE2 and a Rd 13 TE2 is the difference between serviceable and unusable. The relevant background: ~23 TEs get drafted against 12 weekly starter slots, and a TE in the flex only happens when a roster is in trouble — so TE2's job is bye-week and injury coverage. That argues for taking him at the *late* edge of the serviceable window (Rd 11) rather than the early edge (Rd 10), buying one more round of RB or WR while the TE tier is still intact.

### 5.4 The named late-round backs, checked against the grid

The owner's Rd 11+ targets, with the caveat that this is exactly where the owner said ball knowledge takes over from external analysis:

| Player | In the grid? | Score | ADP-vs-score | Read |
|---|---|---|---|---|
| **Blake Corum** | RB34 | **+13** | +18 | Best score of the RB31–36 block. Breakout Candidate, minimal injury concern |
| **Kyle Monangai** | RB36 | **+9** | +18 | Second-best of the block, same profile |
| **Jonathon Brooks** | RB33 | **−7** | +1 | ⚠️ Grid is bearish — see below |
| Tyjae Spears | not in set | — | — | Outside the analyst's top 36 |
| Jonah Coleman | not in set | — | — | Outside the analyst's top 36 |

**Corum and Monangai are the two best-scoring backs in the bottom block**, both +18 against ADP — the largest positive divergences down there. The grid likes these picks more than the market does.

**Brooks is the one to look at squarely.** He scores −7, 32nd of 36. The cells: red on targets, receptions, total touches, and touchdowns; red on team offensive PPG rank; **Concerned** on age & injury. Green only on run blocking. Two things are worth separating, though. First, the volume reds are a *projection* of role, and for a back coming off a major knee injury a projection of no volume is close to circular — it encodes the injury twice, once in the volume cells and again in the injury cell. Second, the analyst himself names Brooks as a Rd 7+ league-winner target in his own strategy recap, directly contradicting his own grid. So the grid's bearishness is not independent confirmation of anything; it is one input that disagrees with its own author.

Given the all-caps, the read here is conviction that the grid's volume projections are stale. That is a legitimate position and it is precisely the ball-knowledge zone. The tool should surface the −7 and the Concerned flag at the pick, then get out of the way — which is what W11 already does.

**Spears and Coleman have no factor data at all.** Neither is in the analyst's top-36 set, so there is nothing to validate against and nothing to contradict. Worth knowing that silence is absence of coverage, not a negative signal.

---

## 6. Recalibration needed before the tool uses the third-party data

1. **The league-winner bar is not our bar.** `rb_leaguewinner_bar_by_pick.csv` uses 9.5 PPG as RB flex-line replacement, 4.83 points per win, and a 16-game season. We are 12-team full PPR with **two** flex spots, which raises replacement level and therefore raises every required-PPG figure. Recompute before quoting any "Legendary PPG Needed" number — and note that recomputing also moves every `value` figure in `legendary_rb_candidates_2026.csv`, where only four of twelve candidates currently clear their bar.
2. **A half-PPR note is embedded in the WR strategy.** The analyst's line about an RB counting toward flex once he's your RB3 is explicitly a half-PPR caveat. In full PPR the WR-in-flex case is stronger, not weaker — which supports the owner's flex answer.
3. **Bust rates past Rd 9 are undefined, not zero.** The 0.0% cells are a measurement boundary. Do not display them as safety.
4. **QB and TE league-winner rows are small-n.** No denominators published anywhere. The 33.3% QB Rd 5–6 figure should not drive anything.
5. **Two convergent findings worth acting on.** The legendary-season regression puts **targets per game** first at 100% evidence strength, and the new 100-scrimmage-yard bonus lets a pass-catching back reach the threshold through rushing *and* receiving combined (+30 vs +21 for a pure runner). Both point the same way: **weight receiving work heavily in the late-round RB darts.** That sharpens the owner's "independent backs with standalone paths" into something checkable — standalone *receiving* role, not just standalone carries.
6. **The analyst contradicts himself on O-line.** His regression finds OL rank statistically indistinguishable from noise (p=0.52), while his RB factor grid gives PFF Run Blocking a full weighted cell. Don't let O-line enter the model twice, and lean toward the regression.

---

## 7. Open items

1. **Does the owner want the QB1 window moved to Rd 7?** §5.2 recommends it; not yet agreed. This is the only substantive disagreement left in the document.
2. **Which word wins at RB in Rd 1–2, "trustworthy" or "upside"?** Round 1–2 RB is the highest-bust band on the board (21.8%) and supplies the catastrophic tail almost alone. Owner chose max upside, so the tool defaults there — but it should be a conscious call, not a collision on the clock.
3. **TE2 at Rd 10 or Rd 11?** §5.3 argues for 11 — the late edge of the serviceable window.
4. **Two to three RB darts?** Depends on whether 1 or 2 RBs go by Rd 4. Five named candidates against two or three slots.
5. **Is QB2 the designated sacrifice** if a Rd 11–15 dart is too good to pass? §5.1 assumes yes.
6. **Analyst identity and date** for the strategy set. The FantasyData ADP table is credited to Ryan Heath; everything else is uncredited.
