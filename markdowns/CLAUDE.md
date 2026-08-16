# Fantasy Football Analysis — Workspace Index

Owner: **Nathan** (`nathanawright24`). Launch Claude Code from this directory.

## Current work

Building the **2026 live draft tool** in `Tool/`.

**Read `markdowns/SPEC-draft-tool-build.md` first.** It is the authoritative build spec —
folder layout, feature toggles, valuation model, availability model, guardrails, build order.
Start there before touching any code.

## Folder map

| Path | Contents | Write? |
|---|---|---|
| `2026/` | Player data — implied props, factor grids, O-line, all positions | **No — read-only** |
| `drafts/` | Historical draft boards, 2022–2025 + 2025 alt league | **No — read-only** |
| `markdowns/` | Handoff docs, guardrails, and the build spec | Docs only |
| `Templates/` | CSV templates for future-year refreshes | **No — read-only** |
| `scripts/` | Existing image-parsing pipeline (already run) | **No — do not re-implement** |
| `Tool/` | The draft tool. Git repo root. All new code goes here | Yes |

**Nothing outside `Tool/` is ever written to.** Corrections belong in the build layer, with the
reason recorded in `Tool/data/derived/join_report.txt`.

## Reference documents in `markdowns/`

| File | What it holds |
|---|---|
| `SPEC-draft-tool-build.md` | **The build spec. Start here.** |
| `HANDOFF-draft-tool-context.md` | League settings, 2026 scoring changes, per-manager priors, tool requirements |
| `GUARDRAILS-macro-roster-construction.md` | Owner's draft intent as warning rules W1–W15 |
| `HANDOFF-analyst-factor-grids.md` | Factor grids for QB/RB/TE/WR — what the scores are and are not |
| `HANDOFF-implied-props-context.md` | Vegas/Clay valuation layer, O-line blend, games-played convention |
| `league-draft-tendencies-2026.md` | Full behavioral analysis of all twelve managers |
| `SCHEMA-*.md` | Column-by-column data dictionaries |

## Rules that must not be softened

Full list in spec §5. The four that have already broken something:

1. **Key on `name + position + nfl_team`.** Abbreviated names collide — "B. Robinson" matched
   Bijan to Brian Robinson and produced a bogus 87-pick reach that corrupted an analysis.
2. **`nfl_team` is blank in every factor-grid file.** Backfill from the props files first. Never
   from model recall.
3. **`factor_score` is ordinal.** Never points, never blended with a `ppr_*` column, never
   compared across positions.
4. **Print every unmatched name** to `join_report.txt`. Silent join failure is the primary
   correctness risk in this project.

## Context worth knowing

- **2026 scoring is new**: 5-point passing TDs, +3 for 100+ scrimmage yards, +3 for 300+ passing
  yards. Every historical draft predates all three. Historical ADP is not a valid positional-value
  baseline.
- **The two per-game bonuses are absent from every `ppr_*` column** and cannot be recovered from
  season totals. Spec §6.2 covers the modelling approach.
- **Nathan drafts from slot 5 of 12.** The intervening-manager sets are deterministic — see
  spec §3.
- **The tool must run in other leagues too.** Every analysis layer is toggleable; see spec §4.2
  before hard-coding anything league-specific.
