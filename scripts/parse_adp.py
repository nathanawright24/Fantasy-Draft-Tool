#!/usr/bin/env python3
"""
Convert a pasted Draft Sharks ADP table into the standard ADP template CSV.

Usage:
    python parse_adp.py raw_paste.txt out.csv --source Sleeper --format PPR --date 2026-08-16

Expected input: paste the table straight from the site into a text file. Each player
is a block of lines like this (blank line between blocks, header lines at the top are
ignored automatically):

    2
    DET logo
    Jahmyr Gibbs
    DET
    RB1            <- Draft Sharks positional rank  (DISCARDED)
    1   RB1        <- ADP overall + ADP positional rank  (KEPT)
    -1             <- trend  (DISCARDED)

Output columns: ADP, Player, Team, Pos, Pos Rank, Source, Format, Date Pulled
Rows are sorted by ADP. The script reports duplicate ADPs, gaps in ADP, and gaps in
positional ranks so you can tell whether the paste captured the full board.
"""

import argparse, csv, re, sys

HEADER = ["ADP", "Player", "Team", "Pos", "Pos Rank", "Source", "Format", "Date Pulled"]
POS_RANK = re.compile(r"^(RB|WR|QB|TE|K|DEF|DST|IDP)(\d+)$")
ADP_LINE = re.compile(r"^(\d+)\s+((?:RB|WR|QB|TE|K|DEF|DST|IDP)\d+)\s*$")


def parse(text):
    lines = [ln.strip() for ln in text.splitlines()]
    rows, i = [], 0
    while i < len(lines):
        # A player block always starts with a bare number followed by "<TEAM> logo".
        if (
            lines[i].isdigit()
            and i + 5 < len(lines)
            and lines[i + 1].endswith("logo")
        ):
            name = lines[i + 2]
            team = lines[i + 3]
            # Walk forward to the "ADP  POSRANK" line (skips the DS positional rank).
            j, adp_match = i + 4, None
            while j < min(i + 9, len(lines)):
                m = ADP_LINE.match(lines[j])
                if m:
                    adp_match = m
                    break
                j += 1
            if adp_match:
                adp = int(adp_match.group(1))
                pos_rank = adp_match.group(2)
                pos = POS_RANK.match(pos_rank).group(1)
                rows.append([adp, name, team, pos, pos_rank])
                i = j + 1
                continue
        i += 1
    rows.sort(key=lambda r: r[0])
    return rows


def audit(rows):
    seen, dupes = set(), []
    for r in rows:
        (dupes.append(r[0]) if r[0] in seen else seen.add(r[0]))
    notes = [f"parsed {len(rows)} players"]
    if dupes:
        notes.append(f"duplicate ADPs: {sorted(set(dupes))}")
    if rows:
        gaps = [n for n in range(1, rows[-1][0] + 1) if n not in seen]
        if gaps:
            notes.append(f"missing ADP slots: {gaps}")
    for pos in ["QB", "RB", "WR", "TE", "K", "DEF", "DST"]:
        nums = sorted(int(r[4][len(pos):]) for r in rows if r[3] == pos)
        if not nums:
            continue
        missing = [n for n in range(1, len(nums) + 1) if n not in nums]
        notes.append(
            f"{pos}: {len(nums)}" + (f" (gaps at {missing})" if missing else "")
        )
    return notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--source", default="Sleeper")
    ap.add_argument("--format", dest="fmt", default="PPR",
                    help="PPR, Half-PPR, Standard, 2QB, etc.")
    ap.add_argument("--date", default="", help="date pulled, YYYY-MM-DD")
    args = ap.parse_args()

    with open(args.infile, encoding="utf-8") as f:
        rows = parse(f.read())
    if not rows:
        sys.exit("No player blocks found — check that the paste kept its line breaks.")

    with open(args.outfile, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r + [args.source, args.fmt, args.date])

    for note in audit(rows):
        print(note)


if __name__ == "__main__":
    main()
