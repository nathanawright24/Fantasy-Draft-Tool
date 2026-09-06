#!/usr/bin/env python3
"""
parse_sleeper_adp_paste.py — turn a pasted Sleeper/market ADP page into the CSV the
pipeline expects.

Usage:
    python parse_sleeper_adp_paste.py <paste.txt> [YYYY-MM-DD]

Writes Tool/data/raw/sleeper_adp_ppr_<date>.csv with exactly the ten columns the
pipeline reads:
    ADP Rank, ADP, Player, Team, Pos, Pos Rank, Match Key, Source, Format, Date Pulled

Why a parser instead of a hand-built CSV: this project's documented primary
correctness risk is name-join failure, and transcribing ~300 rows by hand is where
that starts. Save the paste to a text file and run this instead.

The paste repeats a seven-line block per player:

    2                <- the site's own rank. NOT the ADP. Deliberately unused.
    LAR logo
    Puka Nacua
    LAR
    WR1              <- position rank by the site's rank
    4    WR2         <- ADP, then position rank by market. TAB separated.
    +2               <- trend, or N/A

`ADP Rank` is derived by ranking the ADP column, not taken from the leading number,
because the two disagree (Puka: rank 2, ADP 4).
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date
from pathlib import Path

POS_RE = re.compile(r"^([A-Z]{1,3})(\d+)$")


def normalize_match_key(name: str, pos: str) -> str:
    s = name.lower().replace(".", "").replace("'", "")
    s = re.sub(r"\s+", " ", s).strip()
    return f"{s}|{pos}"


def parse(path: Path) -> list[dict]:
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8", errors="replace").splitlines()]
    rows: list[dict] = []
    skipped: list[str] = []

    for i, ln in enumerate(lines):
        if not ln.endswith(" logo"):
            continue
        try:
            name = lines[i + 1]
            team = lines[i + 2]
            ds_pos = lines[i + 3]
            adp_line = lines[i + 4]
        except IndexError:
            skipped.append(f"truncated block at line {i}")
            continue

        m = POS_RE.match(ds_pos)
        if not m:
            skipped.append(f"{name}: unparsed position cell {ds_pos!r}")
            continue
        pos = m.group(1)

        fields = [f for f in re.split(r"[\t ]+", adp_line) if f]
        if not fields:
            skipped.append(f"{name}: empty ADP line")
            continue
        try:
            adp = float(fields[0])
        except ValueError:
            skipped.append(f"{name}: non-numeric ADP {fields[0]!r}")
            continue
        pos_rank = fields[1] if len(fields) > 1 else f"{pos}?"

        rows.append({
            "ADP": adp,
            "Player": name,
            "Team": team,
            "Pos": pos,
            "Pos Rank": pos_rank,
            "Match Key": normalize_match_key(name, pos),
        })

    # Derive ADP Rank from ADP. Ties get the same rank; the leading column in the
    # paste is the site's own ranking and disagrees with ADP, so it is not used.
    for r in rows:
        r["ADP Rank"] = 1 + sum(1 for o in rows if o["ADP"] < r["ADP"])

    if skipped:
        print(f"  {len(skipped)} block(s) skipped:")
        for s in skipped[:15]:
            print(f"    - {s}")
    return rows


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"not found: {src}")
        return 1
    as_of = sys.argv[2] if len(sys.argv) > 2 else date.today().isoformat()

    rows = parse(src)
    if not rows:
        print("No player blocks found. Does the paste keep one field per line?")
        return 1

    out_dir = Path("Tool/data/raw") if Path("Tool/data/raw").exists() else Path(".")
    out = out_dir / f"sleeper_adp_ppr_{as_of}.csv"
    cols = ["ADP Rank", "ADP", "Player", "Team", "Pos", "Pos Rank",
            "Match Key", "Source", "Format", "Date Pulled"]
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["ADP"]):
            r.update({"Source": "Sleeper", "Format": "PPR", "Date Pulled": as_of})
            w.writerow({c: r[c] for c in cols})

    by_pos: dict[str, int] = {}
    for r in rows:
        by_pos[r["Pos"]] = by_pos.get(r["Pos"], 0) + 1
    print(f"\nwrote {out}  ({len(rows)} players)")
    print(f"  by position: {dict(sorted(by_pos.items()))}")
    print(f"  ADP range: {min(r['ADP'] for r in rows):.0f} to {max(r['ADP'] for r in rows):.0f}")
    dupes = len(rows) - len({r["Match Key"] for r in rows})
    print(f"  duplicate match keys: {dupes}")
    print("\nSanity check the top of the file before building. Then:")
    print("  python Tool/build/pipeline.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
