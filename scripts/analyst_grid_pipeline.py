#!/usr/bin/env python3
"""
Annual pipeline for the analyst's colour-coded factor grids (QB / RB / WR / TE).

WHY THIS IS NOT AN OCR SCRIPT
-----------------------------
These grids carry half their information in CELL FILL COLOUR. The text is a
three-value vocabulary (Yes / No / ?) that is fully determined by the colour, so
reading the colour gives you both the response AND the score weight, while OCR on a
150px cell gives you a coin flip on "?" vs "7". Read the fill; derive the text.

WHAT MAKES IT TRUSTWORTHY
-------------------------
The analyst publishes a per-player colour count and a weighted total. That is an
independent check on every single cell: count the colours yourself and require an
exact match plus 5G + 3Y - 1O - 3R = published total. In 2026 this caught 13 real
errors in the source and zero errors in the extraction.

Read failures by their shape:
  * MANY players fail in the same image  -> your row or column geometry is off.
  * ONE player fails                    -> the analyst miscounted. Keep both numbers.
  * Published cells != grid cells        -> analyst dropped a cell. Grid is right.

USAGE
-----
  python analyst_grid_pipeline.py --probe IMG_1234.jpeg
      Print detected column and row boundaries. Compare against the image by eye.

  python analyst_grid_pipeline.py --sample IMG_1234.jpeg --position WR
      Print the distinct fill colours found, with the palette name each maps to and
      the match distance. RUN THIS EVERY YEAR before trusting anything: the analyst
      changes his fills between positions and between seasons (2026 RB used a salmon
      red and a light green found nowhere else).

  python analyst_grid_pipeline.py --extract --position WR --config wr_2026.json
      Extract, validate against published counts, write colours_by_rank.json.

CONFIG SHAPE (one JSON per position per year)
---------------------------------------------
{
  "images": {
    "IMG_5346": {"rng": [1, 9],
                 "x":  [319, 479, 644, ...],     # label edge, Average edge, then one per player
                 "vy": [67, 91, 113, 136],       # volume row boundaries (n rows + 1)
                 "sy": [231, 255, ...]},         # situational row boundaries (n rows + 1)
    ...
  },
  "published": {"1": [9, 1, 0, 2, 42], ...}      # rank: [green, yellow, orange, red, total]
}
"""
import argparse, json, sys
from collections import Counter, defaultdict
import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Palette. Every variant observed across the 2026 QB/RB/WR/TE sheets is listed
# explicitly, because the analyst's fills differ per position and some variants
# are closer to the WRONG canonical colour than to the right one -- the 2026 RB
# salmon red (237,139,135) sits nearer to amber (244,195,67) than to pure red.
# Nearest-neighbour classification is only safe while every variant is enumerated.
# Add new variants rather than widening tolerances.
# ---------------------------------------------------------------------------
PALETTE_VARIANTS = {
    'green':  [(0, 176, 80), (84, 168, 44), (79, 171, 91), (159, 206, 98)],
    'yellow': [(255, 255, 0), (253, 254, 85), (255, 254, 85), (255, 255, 153)],
    'orange': [(244, 177, 131), (237, 125, 49), (243, 195, 67)],
    'red':    [(255, 0, 0), (230, 51, 36), (237, 139, 135)],
    'purple': [(112, 48, 160), (158, 109, 203), (103, 52, 153)],
    'white':  [(255, 255, 255)],
    'gray':   [(217, 217, 217)],
    'blue':   [(0, 176, 240), (140, 175, 215)],
}
_FLAT = [(name, rgb) for name, variants in PALETTE_VARIANTS.items() for rgb in variants]

def classify(rgb):
    best, bd = None, float('inf')
    for name, ref in _FLAT:
        d = sum((float(rgb[i]) - ref[i]) ** 2 for i in range(3))
        if d < bd:
            bd, best = d, name
    return best, bd

POINTS = {'green': 5, 'yellow': 3, 'orange': -1, 'red': -3}

# Response vocabularies. Colour -> text, per factor kind.
RESPONSE_MAPS = {
    'binary_vs_avg':   {'green': 'Yes', 'yellow': 'Yes', 'orange': '?', 'red': 'No'},
    'binary_vs_elite': {'green': 'Yes', 'yellow': 'Yes', 'orange': '?', 'red': 'No'},
    'more_less_same':  {'green': 'Less', 'yellow': 'Same', 'orange': '?', 'red': 'More'},
    'injury':          {'green': 'No Concern', 'yellow': 'Minimal Concern',
                        'orange': 'Some Concern', 'red': 'Concerned'},
    # Archetype vocabularies are position-specific and NOT stable year to year.
    # WR 2026: green=Prime WR1, yellow=Prime WR2, orange=Breakout Candidate, red=Trusty Veteran
    # RB 2026: purple=Prime Multi Time RB1, green=RB1s In Their Prime, red=Trusty Veteran,
    #          and Breakout Candidate appeared as BOTH yellow and orange.
    # Derive the mapping from observed pairs each year; never assume last year's.
}

# Per-position structure as of 2026. graded_cells is the count every player's column
# must contain -- the single most useful tripwire for a bad parse.
POSITION_SHAPE = {
    'QB': dict(volume=['Pass Attempts', 'Passing TDs', 'Rush Attempts', 'Rushing TDs'],
               situational=['Offensive Rank in PPG', 'Offensive Line Rank in Pass Blocking',
                            'Deep Ball Attempts/Game', 'Rank in QBR', 'RedZone Combined Attempts',
                            'Rank in Neutral Pace', 'Rank in Pass Offense DVOA'],
               graded_cells=11, has_archetype=False, has_injury=False, uncounted_colors=[]),
    'RB': dict(volume=['Carries', 'Targets', 'Receptions', 'Total Touches', 'Touchdowns'],
               situational=['Offensive Rank in PPG', 'PFF Run Blocking Grade', 'Teams Wins', 'Age',
                            'Efficiency (Yards per Carry)', 'Efficiency (Yards per Touch)',
                            'Archetype', 'Age & Injury'],
               graded_cells=13, has_archetype=True, has_injury=True,
               uncounted_colors=['purple']),
    'WR': dict(volume=['Targets', 'Receptions', 'Touchdowns'],
               situational=['Offensive Rank in PPG', 'Quarterbacks Rank in PFF Passing Grade',
                            'Team Pass Attempts', 'Highest Targeted Secondary Option',
                            'Offensive Line Rank in Pass Blocking', 'Rank in Yards per Route Run',
                            'Highest % Achieved in Reception Perception', 'Archetype',
                            'Injury/Suspension Concern?'],
               graded_cells=12, has_archetype=True, has_injury=True, uncounted_colors=[]),
    'TE': dict(volume=['Targets', 'Receptions', 'Touchdowns'],
               situational=['Offensive Rank in PPG', 'Quarterbacks Rank in QBR',
                            'Rank in Team Pass Attempts', 'Rank in Team Targets',
                            'Rank in Receiving Touchdown', 'Route Participation', 'In-line %',
                            'Rank in Yards per Route Run', 'Injury/Age Concerns?'],
               graded_cells=12, has_archetype=False, has_injury=True, uncounted_colors=[]),
}

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def _group(vals, gap=5):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return [int(np.mean(g)) for g in out]

def load(path):
    return np.asarray(Image.open(path).convert('RGB')).astype(int)

def column_boundaries(a, thr=25, min_votes_frac=0.45):
    """Vote across many horizontal scan lines. A boundary that only shows up on a few
    lines is a letter stroke; a real column edge shows up on almost all of them."""
    H = a.shape[0]
    votes, n = Counter(), 0
    for y in range(15, H - 5, 5):
        n += 1
        band = a[y - 1:y + 2].mean(axis=0).mean(axis=1)
        for g in _group([x for x in range(1, len(band)) if abs(band[x] - band[x - 1]) > thr]):
            votes[g] += 1
    return _group([k for k, v in votes.items() if v >= n * min_votes_frac], gap=6)

def row_boundaries_from_labels(a, label_x_end, thr=150, frac=0.5):
    """Read row edges off the LABEL column, whose gridlines are unbroken black. More
    reliable than scanning a data column, where two same-coloured neighbours leave no edge."""
    g = a[:, 2:max(10, label_x_end - 5)].mean(axis=2)
    return _group([y for y in range(g.shape[0]) if (g[y] < thr).mean() > frac])

def row_boundaries_voted(a, xb, thr=25, min_votes=None):
    """Fallback: vote across several offsets inside every player column."""
    votes, n = Counter(), 0
    for j in range(1, len(xb) - 1):
        for off in (6, 10, 14, 18, -10, -14):
            x = xb[j] + off
            if not (0 < x < a.shape[1] - 2):
                continue
            n += 1
            band = a[:, x - 1:x + 2].mean(axis=1).mean(axis=1)
            for g in _group([y for y in range(1, len(band)) if abs(band[y] - band[y - 1]) > thr]):
                votes[g] += 1
    mv = min_votes if min_votes else n * 0.45
    return _group([k for k, v in votes.items() if v >= mv], gap=5)

MAX_FILL_DISTANCE = 3000

def cell_color(a, x0, x1, y0, y1):
    """Modal colour of the cell interior, counting only pixels that are confidently a fill.

    Two details matter. First, the MODE rather than a mean or median: text pixels are a
    minority, and a mean would blend them into the fill while a median can be dragged into
    a neighbouring row by a few pixels of misalignment.

    Second, the distance gate. Anti-aliased text on a coloured fill produces a halo of
    intermediate pixels, and some of those blends sit closer to a *different* palette
    variant than to the fill they came from -- a yellow cell with long bold text
    ("Breakout Candidate", "Minimal Concerns") yields olive halo pixels that land nearer
    the light green used on the 2026 RB sheet than the yellow they belong to. In a
    text-heavy cell those halo pixels can outvote the fill. Discarding anything that is
    not a near-exact match to a known variant removes them; a real fill matches within
    a distance of a few dozen, so the gate is generous.
    """
    patch = a[y0 + 4:y1 - 3, x0 + 6:x1 - 6].reshape(-1, 3)
    if patch.size == 0:
        return 'none'
    votes = Counter()
    for p in patch[::4]:
        name, d = classify(p)
        if d <= MAX_FILL_DISTANCE:
            votes[name] += 1
    if not votes:
        return 'none'
    return votes.most_common(1)[0][0]

# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------
def probe(path):
    a = load(path)
    xb = column_boundaries(a)
    print(f'{path}  size={a.shape[1]}x{a.shape[0]}')
    print('  column boundaries :', xb)
    print('  rows (label col)  :', row_boundaries_from_labels(a, xb[0] if xb else 300))
    print('  rows (voted)      :', row_boundaries_voted(a, xb) if len(xb) > 2 else 'n/a')
    print('\n  Expect column boundaries = [label edge, Average edge, one per player].')
    print('  Expect row boundaries to split into a volume block and a situational block,')
    print('  each preceded by a header row and a names row that you should NOT grade.')

def sample(path, position):
    """Print every distinct fill in the image with its palette match and distance.
    A distance above ~2000 means the palette is missing that variant -- add it."""
    a = load(path)
    xb = column_boundaries(a)
    yb = row_boundaries_from_labels(a, xb[0] if xb else 300)
    seen = defaultdict(int)
    for i in range(len(yb) - 1):
        for j in range(1, len(xb) - 1):
            patch = a[yb[i] + 4:yb[i + 1] - 3, xb[j] + 6:xb[j + 1] - 6].reshape(-1, 3)
            if patch.size == 0:
                continue
            rgb = Counter(map(tuple, patch[::4])).most_common(1)[0][0]
            seen[tuple(int(v) for v in rgb)] += 1
    print(f'{path}  ({position})  distinct modal fills, most common first:')
    for rgb, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        name, d = classify(rgb)
        flag = '   <-- CHECK: no close palette variant' if d > 2000 else ''
        print(f'  {str(rgb):20s} x{n:<4d} -> {name:7s} (d={int(d)}){flag}')

def extract(cfg, position):
    shape = POSITION_SHAPE[position]
    factors = shape['volume'] + shape['situational']
    out = {}
    for stem, s in cfg['images'].items():
        a = load(s.get('path', f"{cfg.get('image_dir', '.')}/{stem}.jpeg"))
        xb, vy, sy = s['x'], s['vy'], s['sy']
        assert len(vy) == len(shape['volume']) + 1, f'{stem}: vy needs {len(shape["volume"])+1} bounds'
        assert len(sy) == len(shape['situational']) + 1, f'{stem}: sy needs {len(shape["situational"])+1} bounds'
        for k in range(len(xb) - 2):
            rank = s['rng'][0] + k
            if rank > s['rng'][1]:
                break
            col = {}
            for i, f in enumerate(shape['volume']):
                col[f] = cell_color(a, xb[k + 1], xb[k + 2], vy[i], vy[i + 1])
            for i, f in enumerate(shape['situational']):
                col[f] = cell_color(a, xb[k + 1], xb[k + 2], sy[i], sy[i + 1])
            if rank in out and out[rank] != col:
                print(f'  WARNING rank {rank} extracted twice with different results '
                      f'(duplicate screenshot?) -- inspect before continuing')
            out[rank] = col
    return out, factors

def validate(grid, published, position):
    shape = POSITION_SHAPE[position]
    uncounted = set(shape['uncounted_colors'])
    rows, failures = [], []
    for rank in sorted(grid):
        c = Counter(v for v in grid[rank].values() if v not in uncounted)
        got = (c['green'], c['yellow'], c['orange'], c['red'])
        total = sum(POINTS[k] * v for k, v in c.items() if k in POINTS)
        cells = sum(got)
        exp = published.get(str(rank)) or published.get(rank)
        ok = exp is not None and tuple(got) == tuple(exp[:4]) and total == exp[4]
        rows.append(dict(rank=rank, counts=got, score=total, cells=cells,
                         published=tuple(exp) if exp else None, ok=ok))
        if not ok and exp:
            kind = ('analyst dropped a cell' if sum(exp[:4]) != cells
                    else 'colour disagreement on one cell')
            failures.append((rank, got, tuple(exp[:4]), total, exp[4], cells, sum(exp[:4]), kind))
        if cells != shape['graded_cells'] and not uncounted:
            print(f'  NOTE rank {rank}: {cells} graded cells, expected {shape["graded_cells"]}')
    print(f'\nvalidated {len(rows) - len(failures)}/{len(rows)}')
    for r, got, exp, ts, te, gc, pc, kind in failures:
        print(f'  rank {r:<3} grid={got} score={ts:<4} | published={exp} score={te:<4} '
              f'| cells {gc} vs {pc}  -> {kind}')
    if len(failures) > max(3, 0.2 * len(rows)):
        print('\n  >20% failed. That is a GEOMETRY problem, not an analyst problem.'
              '\n  Re-probe the images and fix vy/sy before trusting any of this.')
    return rows, failures

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', metavar='IMAGE')
    ap.add_argument('--sample', metavar='IMAGE')
    ap.add_argument('--extract', action='store_true')
    ap.add_argument('--position', choices=list(POSITION_SHAPE))
    ap.add_argument('--config', metavar='JSON')
    ap.add_argument('--out', default='colours_by_rank.json')
    args = ap.parse_args()

    if args.probe:
        probe(args.probe)
    if args.sample:
        if not args.position:
            sys.exit('--sample needs --position')
        sample(args.sample, args.position)
    if args.extract:
        if not (args.position and args.config):
            sys.exit('--extract needs --position and --config')
        cfg = json.load(open(args.config))
        grid, factors = extract(cfg, args.position)
        validate(grid, cfg.get('published', {}), args.position)
        json.dump({str(k): v for k, v in grid.items()}, open(args.out, 'w'), indent=1)
        print(f'\nwrote {args.out}  ({len(grid)} player columns x {len(factors)} factors)')

if __name__ == '__main__':
    main()
