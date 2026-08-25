# Handoff — 2026 Draft Tool UI

**Author of this document:** UI design pass, August 2026
**Owner:** Nathan (`nathanawright24`), slot 5 of 12
**League:** "We Made A League Mr. Stark", 12 team snake redraft, 16 rounds, full PPR
**Status:** design complete and validated against the real board; Python written but not yet run against a live draft

This file is the transfer record for the draft tool's interface. It documents what was
decided, why, and what still needs doing. It sits alongside
`markdowns/SPEC-draft-tool-build.md`, which remains authoritative for the data layer and
the valuation model. **Where this document and the spec disagree about the interface,
this one wins. Where they disagree about the model, the spec wins.**

Read `SPEC-draft-tool-build.md` §12 and `WORKORDER-2026-08-16.md` first for the state of
the pipeline. Nothing here changes the pipeline.

---

## 1. What the tool is for

One sentence: **at 90 seconds per pick, tell the owner which position to take and which
specific player, in a form someone who has never seen the model could act on.**

That framing came directly from the owner and it is the single most useful constraint in
this document. His words: make it *"almost coach facing, as if someone who didn't
understand the analysis on the back end could make a decision they feel good about at a
given draft slot."*

Three consequences that shape every decision below:

1. **Plain language over model vocabulary.** The interface says "points over a startable
   replacement", not `vorp`. It says "still there at 53", not `survival_probability`. The
   technical column name is available on hover or in the full board, never as the primary
   label.
2. **No dashes anywhere in the interface.** Explicit owner instruction. The source data
   carries em dashes in `intel_note`; they are converted at render time
   (`cockpit_html.plain()`), never edited in the read only `2026/` folder.
3. **The tool never picks.** It presents paths and says what separates them. This was
   already in the spec (§8, "loud warnings, always overridable") and the interface holds
   to it: every route card ends with what that path costs, and the head to head ends with
   what genuinely separates the candidates rather than a recommendation.

### The problem being solved

The previous Streamlit board was correct and unusable under time pressure. The owner's
own diagnosis, verbatim: *"Primarily it was a lot to scroll through."* A 17 column
`st.dataframe` sorted by composite score put Trey McBride at the top of the board with a
5 percent chance of reaching pick 53. Everything the model knew was on screen and none of
it was a decision.

---

## 2. Decisions, with rationale

Numbered so they can be cited and overturned individually. These came out of two rounds
of questions with the owner.

| # | Decision | Rationale |
|---|---|---|
| U1 | **Streamlit buildable.** Every element maps to a real Streamlit widget or to `st.markdown(unsafe_allow_html=True)` | The tool has to be fixable in under a minute mid draft (spec §1 corollary). A custom React frontend is not that |
| U2 | **1920 by 1080, one screen, minimal scroll** | Owner's draft night machine |
| U3 | **Two tabs: Cockpit, then Full board** | Cockpit answers "what do I do right now". Full board is for browsing, filtering and settling arguments |
| U4 | **Board sorted by Sleeper ADP by default** | Owner: *"I'm drafting on sleeper and the players will be sorted by adp in the draft room. Therefore, the board needs to be sorted by sleeper adp."* Board value stays as a column, not the sort. This is the single most important UI decision in the document, because it makes the tool's board and the real draft room readable side by side |
| U5 | **Kickers slide to round 14** | See §4. Owner: *"Board needs to be kicker agnostic"* |
| U6 | **The wait rule** | See §5. The biggest functional change. Owner's example: *"I would never take a qb in the first round. Trevor Lawrence was suggested in the 6th in a mock up since he can be drafted in the 8th"* |
| U7 | **Routes anchored on the owner's own intel names for that exact pick, in his priority order** | He wrote the list; the model's job is to price it, not to replace it |
| U8 | **Route fallback when the intel list is thin: whoever the sharp market takes earliest relative to Sleeper** | The intel table has 3 names at pick 5 and 3 at pick 53, but thins out badly from round 12. The fallback picks up the same signal the owner said routes should maximize |
| U9 | **Routes project three picks ahead** | Owner's choice over 2, 5, or all the way. Deeper is more speculation; the fourth leg was already fiction |
| U10 | **Up to three routes, recomputed every pick** | Owner: *"the permutations of routes to accumulate a roster I will like need to be run in the background with up to 3 route options visible at each pick"* |
| U11 | **Route scoring: points over replacement, plus board value, plus sharp market edge** | Directly from the owner: *"A route should maximize for vorp, NFFC vs Sleeper ADP value (NFFC picking higher than sleeper = value), and composite score."* Sharp edge is weighted double in the sort because it is the only one of the three the market has not already priced |
| U12 | **Positions colour coded to Sleeper's own palette** | QB `#fc2b6d`, RB `#73c3a6`, WR `#46a2ca`, TE `#cc8c4a`. Muscle memory transfers between the two screens |
| U13 | **Only High severity warnings surface themselves; Medium and Low go to a count** | Owner's pick. Under a clock, a list of five warnings is the same as no warnings |
| U14 | **Warnings look forward, not just at now** | Owner picked both "what fires next round" and "a timeline of every rule and the round it fires". See §7 |
| U15 | **Odds carry a confidence band from the spread across 2000 simulated drafts** | Owner's pick over data thinness or manager unpredictability. Honest caveat in §6: this band is small by construction and says less than the alternatives would have |
| U16 | **Sharp market edge shown as a pick difference** (`+9`, `-11`) | Owner's pick over words or a marker |
| U17 | **Notes column shows the owner's own note text, verbatim** | His pick over a model generated read. Dashes stripped, nothing else changed |
| U18 | **Automatic polling AND a manual Sync button, recomputing on every pick** | Owner: *"for sync behavior I want the option I picked + a sync button"*, and *"Draft board sync button should be readily available"* |
| U19 | **Players unlikely to reach the owner are dimmed, not hidden** | Owner's pick. They still carry information about what the room is doing |
| U20 | **Board scrolls to 36 players, three rounds ahead** | Owner's number |
| U21 | **Every board column is sortable** | Owner request. Cockpit board uses an explicit sort control; full board uses native `st.dataframe` header clicks. See §9 for why they differ |
| U22 | **A "your pick" line drawn between rows, like Sleeper's** | Owner request. Only drawn while the board is in Sleeper order; see §8 |
| U23 | **Light and dark both supported** | Owner request. Two token sets, one attribute switch |
| U24 | **Geometric background pattern** | Owner request. A low contrast diagonal lattice plus one soft accent glow, per the design system's "accent as a line and a glow, never a flood" |

### Things deliberately NOT done

- **No hard blocks.** Carried forward from GUARDRAILS. Every warning is overridable.
- **No intel weight slider.** The ±10 VOR cap on `target`/`fade` is not exposed and must
  not be. Work order item 3 is explicit: *"an unbounded intel column lets a hunch quietly
  rebuild the board."*
- **No changes to `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level.** Those are
  settled owner decisions (R16, R17, R22).
- **No new data sources.** Everything on screen traces to `player_master.csv`, the two
  raw ADP files, or `PLAYER-INTEL-2026.md`.
- **No head to head tab.** It was built and reviewed, and the owner chose the full board
  for the second tab instead. The design is preserved in `Draft Cockpit.dc.html` option
  1b if it is ever wanted back.

---

## 3. Screen by screen

### 3.1 Chrome, always visible

A single row across the top, 34px tall:

- Product name and league identity
- **Tab switch:** Cockpit, Full board
- **Live indicator:** a pulsing dot, "Watching Sleeper. Recomputing on every pick. Last
  pick read N seconds ago." This is the honest statement of what the automatic polling is
  doing, and it doubles as a health check: if the timestamp stops moving, polling is dead
- **Sync now button**, outlined in the accent, always in the same place

### 3.2 Cockpit tab

Four bands, top to bottom.

**Band 1, context strip (about 60px).** Four cells divided by hairlines:

1. Who is on the clock, and the round
2. Your next turn, how many picks away, and which group of managers is in between. From
   slot 5 this is deterministic and worth stating in words: short waits of 8 picks bring
   the four managers on your left twice each; long waits of 14 bring the seven on your
   right twice each (spec §3, the fixed window property)
3. Named chips for every intervening manager with their one line tell, drawn from
   `manager_priors.csv`. Repeats are marked "again" rather than duplicating the tell
4. Warning chips: what is firing now, what arms next round

**Band 2, routes (about 230px).** Three cards side by side. Each card carries:

- Route tag and a plain language plan ("Tight end now, quarterback at 101")
- A verdict earned from the numbers, never assigned by rank order: "Most points",
  "Best price", "Surest to be there", "Even on value". Each label is claimed once
- The anchor player in a tinted box, with his Sleeper rank and a timing sentence
- The three legs, each with its pick number, position dot, and the odds he reaches it
- Three totals: points over replacement, board value, sharp edge
- One sentence naming what the path leaves unfilled

**Band 3, board plus rail (fills the rest).**

Board, left, roughly 1330px wide:
- Sleeper pick number first, then player, then the analysis columns
- Position colour on a 3px left bar and on the position abbreviation
- Sortable headers, caret on the active column only
- Pick lines between rows
- Timing chip per row: NOW, CLOSE, WAIT, GONE
- Owner's note, verbatim
- Scrolls to 36 rows

Rail, right, 520px:
- Roster against the 6 WR / 5 RB / 2 TE / 2 QB target, as filled and empty slot pips
- The one High item, with its source named
- "Still here at N": the wait list, which is the same list telling you what not to spend
  this pick on
- A footer stating how many analysis layers are on. Spec §4.2 requirement 2: *"the UI
  states which layers are off, at all times"*

**Band 4, rule timeline (about 90px).** All 16 rounds across, each rule sitting in the
round it starts watching. Filled means armed by what the roster holds; hollow means
waiting for a later round. See §7.

### 3.3 Full board tab

- Filter bar: search, position filter, sort control, and toggles for hiding the gone,
  showing only noted players, and value gaps
- A count: showing N of M
- Thirteen columns, native click to sort
- Legend footer explaining position colours and the sharp market sign

---

## 4. The kicker slide

**Problem.** Sleeper's ADP export ranks kickers among the skill players. On the
2026-08-16 pull, seven kickers sit inside rounds 1 to 13: ranks 128, 129, 131, 139, 141,
150, 153. Nobody in this league drafts a kicker before round 14; the guardrails put the
kicker run at round 14 at the earliest and the owner takes his at 188.

**Effect if left alone.** Every skill player below rank 128 reads later than he will
actually go, by the number of kickers above him. That error feeds the survival curve,
because `draft_engine` anchors its lognormal on `reference_adp_rank`. It makes players
look more available than they are, in the deep rounds where the roster is being finished.

**Fix.** Every kicker ranked before pick 157 moves to round 14. Every player above one
comes up by the count of kickers ahead of him.

```
adjusted_rank(r) = r - count(kicker_ranks < r)
```

Verified: raw 130 becomes 128 with two kickers ahead; raw 132 becomes 129 with three; 82
players re-ranked in total (corrected 2026-08-24 -- the original 62 was stale; the actual
count is now emitted by `board_model.apply_kicker_slide()` itself as
`df.attrs["kickers_reranked_count"]`, so this number can't drift out of sync again).

**Two implementation notes that matter.**

1. The adjustment **overwrites `reference_adp_rank`** and keeps the original in
   `reference_adp_rank_raw`. That is deliberate. `draft_engine` reads
   `reference_adp_rank`, so one assignment makes the entire availability model kicker
   agnostic rather than just the display. Adjusting only the displayed number would leave
   the model and the screen disagreeing, which is worse than either being wrong.
2. The kicker ranks are **read from the raw Sleeper file every run**, never hard coded.
   Next season's pull will place kickers differently. Same principle as the ADP source
   offsets in work order item 1: a property of the site, not a constant.

Function: `board_model.apply_kicker_slide()`.

---

## 5. The wait rule — the most important functional change

**Problem, in the owner's own words.** The model was recommending players he could simply
have later. A quarterback in round 1. Trevor Lawrence in round 6 when Lawrence goes in
round 8.

**Why it happened.** Suggestions were ranked on `composite_score` times availability.
Availability is high for a player nobody else wants yet, so a mid round quarterback with
a decent composite scores well at every pick from round 1 onward. The model was answering
"who is good and available" when the actual question is "who is good and *will not be
here next time*".

**The rule.** For every candidate, compute the odds he survives from the pick under
consideration to the owner's *next* turn:

| Odds he reaches your next turn | Label | Meaning |
|---|---|---|
| below 15% to even reach *this* pick | **GONE** | Not a decision. He will not be there |
| 70% or higher | **WAIT** | Taking him now wastes the pick. Excluded from every suggestion |
| 40% to 70% | **CLOSE** | Genuine coin flip. Eligible, flagged |
| below 40% | **NOW** | Take him here or lose him |

Anything labelled WAIT is dropped from route anchors and from route legs. Everything
still appears on the board, with its label, because the wait list is itself decision
relevant: it is the list of what you do *not* need to spend this pick on.

**Validated against the owner's two examples.**

- Josh Allen at pick 5: **80.7 percent** to reach pick 20. Excluded. No quarterback is
  suggested in round 1, which is what he asked for, and the reason given is a number
  rather than a rule of thumb.
- Trevor Lawrence at pick 53: **94.6 percent** to reach pick 68. Excluded. Matches his
  observation that Lawrence can be had in round 8.
- Sam LaPorta at pick 53: **21 percent** to reach 68. Kept, and correctly the anchor of a
  route. This is the fork the whole strategy document is about.
- Matthew Stafford at 53: 96 percent to reach 68. Excluded, which is consistent with the
  intel file's own finding that the real quarterback windows are 53 and 101 and that
  Stafford is the 101 play.

**Caveat to carry forward.** The 0.70 threshold is a judgment, not a fitted parameter.
It is a single constant (`board_model.WAIT_THRESHOLD`) precisely so it can be moved in
one place. Worth revisiting after the dry run: if the board feels like it is passing on
players who then disappear, the threshold is too low.

**Second caveat.** The wait rule is computed off the fitted lognormal, not the Monte
Carlo, because the Monte Carlo only simulates as far as the next owner pick. That means
the wait number does not carry the substitution logic (the "only one of these three tight
ends goes" effect from spec §12.3). For the tight end window at 44 to 53, where three
tight end hungry managers pick twice, the wait number is therefore the weaker of the two
estimates available. Extending the simulation two owner picks deep would fix it and is
the highest value model change left on the table.

---

## 6. Odds and the confidence band

The number on the board is `survival_probability` from
`draft_engine.compute_availability`, which is the Monte Carlo over the intervening picks
(spec §12.3, R20 and R21). The capacity invariant is asserted on every state built during
this work: **14 expected departures over 14 intervening picks at pick 5, and 8 over 8 at
pick 53.**

The band is the standard error of that survival fraction, `sqrt(p(1-p)/n) * 1.96` at
n = 2000. Rendered as `97% ±1`.

**Be honest about what this band is.** It is the spread of the simulation, so it measures
whether the simulation has converged, not whether the model is right. At 2000 runs it is
about one point at the middle of the range. The owner chose it over two alternatives that
would have said more:

- **Data thinness.** `comparison_adp_n` runs from 51 down to single digits. Trevor
  Etienne has 6 drafts with a range of 214 to 327. The lognormal already widens
  automatically for small n (spec §12.2, the side benefit), so this information exists in
  the curve but is not surfaced as a band.
- **Manager unpredictability.** Tyler is a single sample. Colin inverts himself between
  leagues and is modelled as bimodal. Joseph drafted zero quarterbacks in seventeen rounds
  in 2022. `_confidence_widen` already widens the hazard for these managers, again without
  surfacing it.

**Recommendation for a later pass:** show all three as a stacked band, or let the band
switch source from a control. The plumbing for the other two already exists in
`draft_engine`.

---

## 7. Warnings and the rule timeline

Three layers, per U13 and U14.

1. **Firing now.** High severity only, one card, with the rule code and the source of the
   claim named. Medium and Low collapse to a count.
2. **Arms next round.** A chip. This is the cheap version of foresight and it earns its
   place: at pick 53 the owner is one round from W12 (receiver spent with no third back)
   and two from W7 (fewer than four receivers).
3. **The timeline.** All 16 rounds, each rule placed in the round it starts watching,
   filled when armed by the current roster. Rule placements are in
   `board_model.RULE_SCHEDULE`, ported from GUARDRAILS §4 plus W16 to W18 from spec §8.

**W17 deserves its own note** because it is unusual and it is the point of the whole
exercise. It checks the owner against his own history: quarterback 1 has gone round
4, 5, 10, 9 across four years and tight end 1 has gone 4, 4, 10; 2025 entered round 9
with neither. From `owner_drift.csv`, computed, not transcribed. The spec's justification
stands: *"A tool that models eleven opponents and not its own user would be missing the
largest single predictable error in the room."*

**Known gap.** The timeline shows when a rule *starts watching*, not a prediction of when
it will fire given the current path. The routes and the rules do not talk to each other
yet. A route that leaves the roster with no quarterback entering round 10 should light W4
on the timeline in advance. That is the most obviously missing connection in the design.

---

## 8. The pick line

Sleeper draws a line on its board showing where the user's next pick falls. Replicated,
with one constraint the owner should know about.

The line is drawn after N rows, where N is the number of picks between now and the owner's
turn. At pick 45 with the owner up at 53, the line sits after 8 rows and reads "Your pick
53, everything above here is likely gone." Subsequent lines are drawn for pick 68 and 77
where they fall inside the visible 36 rows.

**The constraint.** A row's position only stands in for a pick number while the board is
in Sleeper order. Sort by board value and row 8 is no longer "the eighth player likely to
go". **So the pick lines disappear on any sort other than Sleeper ascending.** That is a
deliberate refusal rather than an omission: a line in the wrong place is worse than no
line, because it looks authoritative.

Function: `board_model.pick_line_offsets()`.

---

## 9. Sorting, and why the two tables differ

Both boards are sortable. They get there differently, and this is a genuine Streamlit
constraint rather than an inconsistency for its own sake.

- **Cockpit board** is hand rendered HTML, because `st.dataframe` cannot draw a row
  between two rows and the pick line is worth more than native header clicks. Sorting is a
  horizontal `st.radio` above the table with six named orders.
- **Full board** is `st.dataframe` with a `column_config`, which gives native click to
  sort on all thirteen columns for free, plus progress bars on the odds column.

**If the cockpit board's sort control feels worse than clicking a header in practice,**
the fix is a row of small `st.button` elements styled as headers above the HTML table.
It is more code and it costs a rerun per click. Worth trying only if the radio annoys.

---

## 10. Data contract

The UI reads these and nothing else. The separation from spec §4.1 holds: no UI code
reaches into `2026/`.

| Source | Used for |
|---|---|
| `data/derived/player_master.csv` | Everything on the board |
| `data/raw/sleeper_adp_ppr_*.csv` | Kicker ranks only, for the slide |
| `data/derived/manager_priors.csv` | Intervening manager tells, and the hazard |
| `data/derived/team_bias.csv` | The hazard |
| `data/derived/owner_drift.csv` | W17 |
| `state/draft_state_*.json` | Picks made, roster, whose turn |

Columns the interface depends on by name: `player`, `position`, `nfl_team`, `name_key`,
`composite_score`, `vorp`, `survival_probability`, `reference_adp_rank`,
`comparison_adp_rank`, `adp_rank_divergence`, `factor_score_recomputed`,
`bonus_est_ppr`, `p_got_injured`, `archetype`, `tier_color`, `intel_tag`,
`intel_windows`, `intel_priority`, `intel_note`.

### The sharp market sign — read this before touching it

`adp_rank_divergence` is `comparison_adp_rank - reference_adp_rank`, so a **larger** value
means the sharp market takes him **later**. The interface shows the negation, so that
positive means the sharps are higher on him, which is the side value lives on. This was
implemented backwards once and shipped a green "value" flag on Trey McBride, whom the
sharps rate eleven picks lower than Sleeper does.

The check that catches it: Jonathon Brooks, Sleeper 128 against NFFC 66, must read
**+62**. `PLAYER-INTEL-2026.md` records the same gap as "+61.3, the largest Sleeper-later
gap on the board". If Brooks reads negative, the sign is wrong.

---

## 11. Files

Design, in this project:

| File | What it is |
|---|---|
| `Draft Cockpit v2.dc.html` | The built out design. Two tabs, both draft states, light and dark |
| `Draft Cockpit.dc.html` | The three original options, kept for reference. 1b is the head to head |
| `Current Streamlit App.dc.html` | The existing app, recreated, as the before picture |
| `board_data.js` | Precomputed board for both draft states, generated from the real CSVs |

Python, to be dropped into `Tool/app/`:

| File | What it is |
|---|---|
| `python/board_model.py` | Kicker slide, wait rule, routes, rule timeline, pick lines. **No streamlit import** |
| `python/cockpit_html.py` | Theme, CSS, board and route card renderers. Returns strings. **No streamlit import** |
| `python/main_cockpit.py` | The Streamlit page. Run with `streamlit run app/main_cockpit.py` |

`main_cockpit.py` sits **alongside** the existing `main.py` rather than replacing it, so
the old board stays available while this one is shaken out. The work order's rule becomes:
`grep -l streamlit app/*.py` returns `main.py` and `main_cockpit.py`, and nothing else.

Requires **streamlit 1.37 or newer** for `st.fragment(run_every=...)`, which does the
automatic polling. On an older version, delete the decorator; the manual Sync button still
works and the tool is fully usable.

---

## 12. Two draft states, and why they are both in the design

The design carries a switch between two moments, because they exercise different halves
of the interface and both need to look right.

**Pick 5, the owner's first turn.** Bijan Robinson, Jahmyr Gibbs, Ja'Marr Chase and Jaxon
Smith-Njigba are gone in that order, which the owner states as a certainty. He is on the
clock, so availability is not a question; the question is the fourteen pick wait to 20.
The board's finding: his three written names are the top three by value with nothing
close, and **none of them survives to 20** (Puka Nacua 1 percent, Amon-Ra St. Brown 7,
Christian McCaffrey 12). So the decision is not who is best. It is which position he is
willing to be thin at for fourteen picks.

**Pick 53, the tight end fork.** Forty four picks in, LaPorta still on the board, the
eight intervening picks belonging to the four managers on his left, twice each, and those
four include the league's three most tight end hungry managers. This is the moment spec
§12.2 was written about.

The two states live in `board_data.js` for the design. In the Python they are just
whatever `draft_state` currently holds.

---

## 13. Open items

Ordered by how much they would change the tool.

1. **Extend the Monte Carlo two owner picks deep.** §5's second caveat. The wait rule
   currently runs off the fitted curve and therefore loses the substitution logic, which
   is exactly the effect that matters most in the tight end window. Highest value model
   change remaining.
2. **Connect routes to the rule timeline.** §7's known gap. A route that leaves no
   quarterback entering round 10 should light W4 in advance.
3. **The confidence band should probably measure something else.** §6. The plumbing for
   data thinness and manager confidence already exists.
4. **The 0.70 wait threshold is unvalidated.** Revisit after the dry run.
5. **Route legs converge.** At pick 5 all three routes reach for similar names at 20 and
   29, because the follow on board genuinely is similar. Honest, but it looks like a bug.
   Consider showing legs only where they differ between routes.
6. **Cockpit sort is a radio, not header clicks.** §9.
7. **No kicker is ever recommended.** By design (R22, there is no per kicker data in the
   pipeline), but the owner takes one at 188 and the interface says nothing about it. A
   single line at round 14 would close the loop.
8. **One running back through pick 53** is flagged as the standing High item on the pick
   53 state. That is a real structural exposure from the owner's own review, not a model
   artefact, and it is worth deciding about before draft night rather than on the clock.
9. **The dry run has not happened.** Spec §10 step 7 calls it non negotiable and it is
   the only thing that will surface the failure modes that appear under time pressure.
   Budget an evening. Nothing in this document substitutes for it.

---

## 14. If you are picking this up cold

1. Read `SPEC-draft-tool-build.md` §12 and `WORKORDER-2026-08-16.md`.
2. Read §5 of this document. The wait rule is the thing most likely to be
   misunderstood and accidentally removed.
3. Read the sharp market sign warning in §10 before touching any divergence column.
4. Open `Draft Cockpit v2.dc.html` and flip between pick 5 and pick 53. That is the
   target.
5. Copy the three files from `python/` into `Tool/app/` and run
   `streamlit run app/main_cockpit.py`.
