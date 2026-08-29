#!/usr/bin/env python3
"""
Convert a pasted fantasy ADP table into the standard ADP template CSV.

    python parse_adp.py raw_paste.txt out.csv --date 2026-08-16
    python parse_adp.py raw_paste.txt out.csv --date 2026-08-16 --adp-col 3

Two paste layouts are auto-detected.

LAYOUT A — rank / logo / name / team / DS pos rank / "ADP POSRANK" / trend:

    2
    DET logo
    Jahmyr Gibbs
    DET
    RB1            <- site's own positional rank        (DISCARDED)
    1   RB1        <- ADP overall + ADP positional rank (KEPT)
    -1             <- trend                             (DISCARDED)

LAYOUT B — rank+pos / pos number / name / bullet / team / tags / numeric row:

    1   RB
    1

    Jahmyr Gibbs
    ●
    DET
    Three Down Workhorse
    +4
    1.8  1.4  1.0  3.0    <- --adp-col picks which column is the ADP (default 2)

Output columns:
    ADP Rank, ADP, Player, Team, Pos, Pos Rank, Match Key, Source, Format, Date Pulled

ADP Rank is the draft-order position (1 = first off the board), ADP is the raw
value the site reports. Layout A only gives ranks, so the two match. Layout B
gives decimals, so ADP Rank is derived by sorting on ADP.

Match Key is a normalized "name|pos" for joining pulls to each other: lowercased,
punctuation and generational suffixes stripped, common nicknames mapped to one
spelling. Team codes are normalized too (WSH->WAS, LVR->LV, JAC->JAX, UNS->FA),
because sites disagree and an unnormalized join silently drops those players.

Players the site lists with no ADP ("-" or an em dash) are kept, with ADP Rank and
ADP left blank, so a later pull can tell "undrafted" apart from "not in the paste."

Prints an audit afterward: player count, duplicate and missing ADP slots,
positional-rank gaps, and which positions are absent entirely.
"""

import argparse, csv, re, sys
from collections import Counter

HEADER = ["ADP Rank", "ADP", "Player", "Team", "Pos", "Pos Rank",
          "Match Key", "Source", "Format", "Date Pulled"]

POSITIONS = ("RB", "WR", "QB", "TE", "K", "DEF", "DST")
TEAM_ALIASES = {"WSH": "WAS", "LVR": "LV", "JAC": "JAX", "UNS": "FA", "": "FA"}
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
NAME_ALIASES = {
    "cam skattebo": "cameron skattebo",
    "tank dell": "nathaniel dell",
    "chig okonkwo": "chigoziem okonkwo",
    "mike williams": "michael williams",
}
DASHES = {"-", "--", "\u2013", "\u2014"}
BULLETS = {"\u25cf", "\u2022", "\u25aa"}
TEAMS = set("""ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX JAC KC LV LVR LAC
LAR MIA MIN NE NO NYG NYJ PHI PIT SF SEA TB TEN WAS WSH FA UNS""".split())

POS_RANK = re.compile(r"^(%s)(\d+)$" % "|".join(POSITIONS))
A_ADP_LINE = re.compile(r"^(\d+)\s+((?:%s)\d+)\s*$" % "|".join(POSITIONS))
B_HEAD = re.compile(r"^(\d+)\s+(%s)$" % "|".join(POSITIONS))


def norm_team(t):
    t = re.sub(r"[^A-Za-z]", "", t).upper()
    return TEAM_ALIASES.get(t, t)


def match_key(name, pos):
    n = re.sub(r"[^a-z ]", "", name.lower())
    n = " ".join(p for p in n.split() if p not in SUFFIXES)
    return f"{NAME_ALIASES.get(n, n)}|{pos}"


def numeric_row(line):
    """Return the fields of a tab/space separated row of numbers and dashes."""
    fields = [f.strip() for f in re.split(r"\t+|\s{2,}", line.strip()) if f.strip()]
    if len(fields) < 2:
        return None
    for f in fields:
        if f not in DASHES and not re.fullmatch(r"\d+(\.\d+)?", f):
            return None
    return fields


def parse_a(lines):
    rows, i = [], 0
    while i < len(lines):
        if lines[i].isdigit() and i + 5 < len(lines) and lines[i + 1].endswith("logo"):
            name, team = lines[i + 2], lines[i + 3]
            for j in range(i + 4, min(i + 9, len(lines))):
                m = A_ADP_LINE.match(lines[j])
                if m:
                    pos_rank = m.group(2)
                    pos = POS_RANK.match(pos_rank).group(1)
                    rows.append([float(m.group(1)), name, norm_team(team), pos, pos_rank])
                    i = j
                    break
        i += 1
    return rows


def parse_b(lines, adp_col):
    rows, i = [], 0
    while i < len(lines):
        m = B_HEAD.match(lines[i])
        if not m or i + 1 >= len(lines) or not lines[i + 1].isdigit():
            i += 1
            continue
        pos, pos_rank = m.group(2), m.group(2) + lines[i + 1]
        name = team = None
        adp = ""
        j = i + 2
        while j < min(i + 14, len(lines)):
            # The team code marks the end of the identity block; the player's name is
            # the line above it, skipping the bullet glyph some exports insert.
            if name is None and lines[j] in TEAMS:
                back = j - 1
                while back > i and (lines[back] in BULLETS or not lines[back]):
                    back -= 1
                name, team = lines[back], lines[j]
                j += 1
                continue
            fields = numeric_row(lines[j]) if name else None
            if fields:
                val = fields[adp_col - 1] if len(fields) >= adp_col else ""
                adp = "" if val in DASHES else float(val)
                break
            j += 1
        if name:
            rows.append([adp, name, norm_team(team), pos, pos_rank])
        i = j + 1
    return rows


def audit(rows):
    ranked = [r for r in rows if r[0] != ""]
    out = [f"parsed {len(rows)} players ({len(rows) - len(ranked)} with no ADP)"]
    counts = Counter(r[3] for r in rows)
    out.append("by position: " + ", ".join(f"{p}={counts[p]}" for p in POSITIONS if counts[p]))
    absent = [p for p in ("QB", "RB", "WR", "TE", "K", "DEF") if not counts[p] and not (p == "DEF" and counts["DST"])]
    if absent:
        out.append(f"NOTE: no {', '.join(absent)} in this paste")
    seen = Counter(r[0] for r in ranked)
    dupes = sorted(v for v, c in seen.items() if c > 1)
    if dupes:
        out.append(f"tied/duplicate ADP values: {dupes}")
    for pos in POSITIONS:
        nums = sorted(int(r[4][len(pos):]) for r in rows if r[3] == pos)
        if not nums:
            continue
        gaps = [n for n in range(1, max(nums) + 1) if n not in nums]
        if gaps:
            out.append(f"{pos} positional-rank gaps: {gaps}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("infile")
    ap.add_argument("outfile")
    ap.add_argument("--source", default="Sleeper")
    ap.add_argument("--format", dest="fmt", default="PPR",
                    help="PPR, Half-PPR, Standard, 2QB, ...")
    ap.add_argument("--date", default="", help="date pulled, YYYY-MM-DD")
    ap.add_argument("--adp-col", type=int, default=2,
                    help="layout B only: which numeric column holds the ADP (default 2)")
    args = ap.parse_args()

    lines = [ln.strip() for ln in open(args.infile, encoding="utf-8").read().splitlines()]
    rows = parse_a(lines)
    layout = "A"
    if not rows:
        rows = parse_b(lines, args.adp_col)
        layout = "B"
    if not rows:
        sys.exit("No player blocks found — check that the paste kept its line breaks.")

    rows.sort(key=lambda r: (r[0] == "", r[0]))
    with open(args.outfile, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        rank = 0
        for adp, name, team, pos, pos_rank in rows:
            if adp == "":
                out_rank = ""
            else:
                rank += 1
                out_rank = rank
                adp = int(adp) if float(adp).is_integer() and layout == "A" else adp
            w.writerow([out_rank, adp, name, team, pos, pos_rank,
                        match_key(name, pos), args.source, args.fmt, args.date])

    print(f"layout {layout}")
    for note in audit(rows):
        print(note)


if __name__ == "__main__":
    main()
