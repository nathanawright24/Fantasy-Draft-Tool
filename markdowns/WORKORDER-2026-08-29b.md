# Work Order — 2026-08-29b (stale build + edge display)

Small and urgent. `WORKORDER-2026-08-24b.md` is complete and verified. This supersedes
**item 0 only** of `WORKORDER-2026-08-29.md`; items 1–5 of that order still stand and follow
after these four.

---

## 1. 🚨 `player_master.csv` is built from the August 16 ADP files

The new raw files are staged correctly and the glob resolves to the right one. **The pipeline
simply has not been re-run.** Verified on the pushed repo:

```
reference_as_of                = 2026-08-16     <- Sleeper data inside master
config.latest_sleeper_adp_raw_path()            -> sleeper_adp_ppr_2026-08-29.csv
comparison_adp_n (max)         = 51             <- old NFFC sample; new file carries 97
data/raw/ADP.tsv               = 461 rows       <- new file, never read
```

This is the dangerous shape: nothing looks wrong on inspection, and the app will show
two-week-old ADP with current files sitting beside it on disk.

**Do item 2 before rebuilding**, or the rebuild bakes in a wrong rank column.

**Acceptance:** after rebuild, report `reference_as_of`, `comparison_as_of`, max
`comparison_adp_n`, and row count. Expect `2026-08-29` and 97.

---

## 2. 🚨 Sleeper export gained a column; the pipeline conflates two

The Sleeper CSV went from 8 columns to 10, separating `ADP Rank` from `ADP`. They diverge
deeper in the board — row 201 is rank 201, ADP 204. `"ADP Rank"` appears nowhere in
`build/pipeline.py`, which still does:

```python
"adp_value": r["ADP"],
"adp_rank":  r["ADP"],     # must become r["ADP Rank"]
```

Also add the staleness guard from the original item 0: **assert the resolved file's
`Date Pulled` is within N days of today and fail loudly otherwise.** A build against stale
data must never succeed quietly — that is what happened here.

Also new and useful: a `Match Key` column (`jahmyr gibbs|RB`), pre-normalized and
position-qualified. Use it as a first-pass crosswalk key with fallback to existing
normalization, since other sites will not supply it.

**Acceptance:** report the count of rows where `ADP != ADP Rank`, and confirm the staleness
assertion fires on the 08-16 file.

---

## 3. Show VORP beside edge on the cockpit board

Measured at pick 44 on the real board (43 drafted, n_sims=500):

| Player | Pos | edge | vorp | survival to 53 |
|---|---|---|---|---|
| Tyler Warren | TE | **+14.7** | 31.4 | 0.09 |
| Cam Skattebo | RB | +7.6 | 43.7 | 0.63 |
| Zay Flowers | WR | +3.9 | 50.0 | 0.64 |
| Drake Maye | QB | +0.8 | 31.7 | 0.87 |
| Terry McLaurin | WR | −6.6 | 39.5 | 0.86 |

**`edge` is a two-pick optimizer.** It is locally correct and can be globally wrong: it ranks
a 31-VORP tight end above a 50-VORP receiver because the receiver will probably still be
there. Correct in expectation, and still spending a premium pick on a much smaller player.

Position mix of the top 30 by edge: **TE 10, WR 8, QB 7, RB 5.** Thin fallback pools
systematically inflate TE edge — the same onesie distortion the owner identified in NFFC ADP,
arriving through a different channel.

**Task:** surface `vorp` as a first-class column next to `edge`, not in the expand. "+14.7 /
31.4" reads as a tradeoff; "+14.7" alone reads as a ranking. **Do not change the edge
formula or the fallback** — the metric is behaving as designed and the fix is display.

**Acceptance:** paste the rendered top-5 rows showing both figures.

---

## 4. Leave these alone — measured, no action needed

Recorded so they are not revisited:

- **`AVAILABILITY_N_SIMS = 500` is correct for `edge`.** Concern was that edge contains an
  extreme-value statistic that converges slower than survival's mean. Measured across three
  seeds at pick 44: max |Δedge| = **0.6 at both 500 and 2000**, and the top-30 set is
  identical across seeds at both. 2000 halves median noise (0.5 → 0.2) and changes nothing
  actionable. **Keep 500.**
- **The same-position `edge` fallback is fine.** The +94.2 figure in the 24b report came from
  a synthetic stress test that drained the top 15 RBs; on the real board edges are small and
  mostly negative. No unbounded-fallback problem occurs in practice.
- **The poller does not rebuild routes.** Verified.

---

## Do not do

- Do not change the `edge` formula, the fallback, or the sim count.
- Do not raise the ±10 intel cap; `hard_avoid` stays a hard filter.
- Do not let coverage, intel, or `pick_window` affect eligibility or ordering.
- Do not tune to the backtest. Do not touch `scripts/`.

## Ask before doing

- Anything changing `ROSTER_TARGET`, `COMPOSITE_WEIGHTS`, or replacement level.
- Committing to git.
