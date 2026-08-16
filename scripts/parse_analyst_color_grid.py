#!/usr/bin/env python3
"""
Parse a colour-coded analyst factor grid from screenshots into tidy rows.

WHY THIS EXISTS
---------------
These grids encode their content in CELL FILL COLOUR as much as in text. OCR reads the
text and throws away half the information. This reads the fill colour directly, which is
both more complete and more reliable than OCR on a 160px-wide cell.

The text is then DERIVED from the colour, because in this analyst's grids the mapping is
1:1 within a row type (see RESPONSE_MAPS). That means the only thing that has to be right
is the grid geometry -- and geometry is checkable.

VALIDATION IS THE WHOLE POINT
-----------------------------
The scoring tables print a per-player colour count (green/yellow/orange/red) and a total.
Extract the grid, count the colours yourself, and require an exact match on all 45 players
plus the arithmetic 5G + 3Y - 1O - 3R = published total. If a player fails, either the
geometry is off by a row or the analyst made a mistake -- and you can tell which, because
a geometry error fails in clusters while an analyst error fails alone.

USAGE
-----
1. python parse_analyst_color_grid.py --probe path/to/image.jpeg
   Prints detected column and row boundaries. Read them against the image.
2. Fill in SPEC below: x boundaries, volume-row y boundaries, situational-row y boundaries.
3. python parse_analyst_color_grid.py --extract   (writes colors_by_rank.json)
4. Validate against the published counts before building anything downstream.
"""
import argparse, json
from collections import Counter
import numpy as np
from PIL import Image

PALETTE = {
    'green': (0, 176, 80), 'green_dk': (84, 168, 44), 'yellow': (255, 255, 0),
    'lightyellow': (255, 255, 153), 'orange': (244, 177, 131), 'orange_dk': (237, 125, 49),
    'red': (255, 0, 0), 'white': (255, 255, 255), 'blue': (0, 176, 240),
    'navy': (47, 85, 151), 'purple': (112, 48, 160), 'gray': (217, 217, 217),
}
CANON = {'green': 'green', 'green_dk': 'green', 'yellow': 'yellow', 'lightyellow': 'yellow',
         'orange': 'orange', 'orange_dk': 'orange', 'red': 'red', 'white': 'white',
         'blue': 'blue', 'navy': 'navy', 'purple': 'purple', 'gray': 'gray'}

POINTS = {'green': 5, 'yellow': 3, 'orange': -1, 'red': -3}
RESPONSE_MAPS = {
    'binary_vs_avg':  {'green': 'Yes', 'yellow': 'Yes', 'orange': '?', 'red': 'No'},
    'more_less_same': {'green': 'Less', 'yellow': 'Same', 'orange': '?', 'red': 'More'},
    'archetype':      {'green': 'Prime WR1', 'yellow': 'Prime WR2',
                       'orange': 'Breakout Candidate', 'red': 'Trusty Veteran'},
    'injury':         {'yellow': 'Minimal Concern', 'orange': 'Some Concern',
                       'red': 'Concerned', 'green': 'No Concern'},
}

def classify(rgb):
    best, bd = None, float('inf')
    for name, ref in PALETTE.items():
        d = sum((float(rgb[i]) - ref[i]) ** 2 for i in range(3))
        if d < bd:
            bd, best = d, name
    return CANON[best]

def _group(vals, gap=5):
    out = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= gap:
            out[-1].append(v)
        else:
            out.append([v])
    return [int(np.mean(g)) for g in out]

def x_boundaries(a, y, thr=25):
    """Scan a horizontal strip that sits ABOVE the text baseline, so only fills and borders appear."""
    band = a[y - 1:y + 2].mean(axis=0).mean(axis=1)
    return _group([x for x in range(1, len(band)) if abs(band[x] - band[x - 1]) > thr])

def y_boundaries(a, xb, thr=25, min_votes=25):
    """Vote across many vertical scan lines, one set per column, so a row whose neighbours
    happen to share its colour is still found by some other column."""
    votes = Counter()
    for j in range(1, len(xb) - 1):
        for off in (6, 10, 14, 18, -10, -14):
            x = xb[j] + off
            if not (0 < x < a.shape[1] - 2):
                continue
            band = a[:, x - 1:x + 2].mean(axis=1).mean(axis=1)
            for g in _group([y for y in range(1, len(band)) if abs(band[y] - band[y - 1]) > thr]):
                votes[g] += 1
    return _group([k for k, v in votes.items() if v >= min_votes])

def cell_color(a, x0, x1, y0, y1):
    """Modal colour over the cell interior. Text pixels are a minority, so the mode is the fill,
    and unlike a median it is not dragged toward a neighbouring row by a few pixels of misalignment."""
    patch = a[y0 + 4:y1 - 3, x0 + 5:x1 - 5].reshape(-1, 3)
    if patch.size == 0:
        return 'none'
    return Counter(classify(p) for p in patch[::3]).most_common(1)[0][0]

def load(path):
    return np.asarray(Image.open(path).convert('RGB')).astype(int)

# ---- fill this in per image set -------------------------------------------------------
VOLUME_FACTORS = ['Targets', 'Receptions', 'Touchdowns']
SITUATIONAL_FACTORS = [
    'Offensive Rank in PPG', 'Quarterbacks Rank in PFF Passing Grade', 'Team Pass Attempts',
    'Highest Targeted Secondary Option', 'Offensive Line Rank in Pass Blocking',
    'Rank in Yards per Route Run', 'Highest % Achieved in Reception Perception',
    'Archetype', 'Injury/Suspension Concern?']
FACTOR_KIND = {f: 'binary_vs_avg' for f in VOLUME_FACTORS + SITUATIONAL_FACTORS}
FACTOR_KIND.update({'Highest Targeted Secondary Option': 'more_less_same',
                    'Archetype': 'archetype', 'Injury/Suspension Concern?': 'injury'})

# 2026 WR grid, as parsed. rng = (first, last) positional rank in the image.
SPEC = {
    'IMG_5346': dict(rng=(1, 9),  x=[319, 479, 644, 811, 977, 1134, 1294, 1453, 1615, 1776, 1926],
                     vy=[67, 91, 113, 136], sy=[231, 255, 277, 300, 323, 345, 368, 391, 413, 436]),
    'IMG_5348': dict(rng=(10, 18), x=[323, 483, 648, 815, 981, 1138, 1298, 1457, 1619, 1780, 1929],
                     vy=[67, 91, 113, 136], sy=[231, 255, 277, 300, 323, 345, 368, 391, 413, 436]),
    'IMG_5349': dict(rng=(19, 27), x=[321, 481, 646, 813, 979, 1136, 1295, 1455, 1616, 1778, 1923],
                     vy=[71, 95, 117, 141], sy=[235, 259, 282, 304, 327, 350, 372, 395, 417, 437]),
    'IMG_5350': dict(rng=(28, 36), x=[330, 490, 655, 822, 988, 1145, 1304, 1464, 1626, 1787, 1936],
                     vy=[68, 92, 115, 138], sy=[233, 256, 279, 301, 324, 347, 369, 392, 415, 437]),
    'IMG_5351': dict(rng=(37, 45), x=[326, 486, 651, 818, 984, 1141, 1300, 1460, 1622, 1783, 1928],
                     vy=[68, 91, 115, 138], sy=[233, 256, 279, 301, 324, 347, 369, 392, 414, 436]),
}

def extract(image_dir):
    out = {}
    for stem, s in SPEC.items():
        a = load(f'{image_dir}/{stem}.jpeg')
        xb = s['x']
        for k in range(len(xb) - 2):
            rank = s['rng'][0] + k
            col = {}
            for i, f in enumerate(VOLUME_FACTORS):
                col[f] = cell_color(a, xb[k + 1], xb[k + 2], s['vy'][i], s['vy'][i + 1])
            for i, f in enumerate(SITUATIONAL_FACTORS):
                col[f] = cell_color(a, xb[k + 1], xb[k + 2], s['sy'][i], s['sy'][i + 1])
            out[rank] = col
    return out

def validate(grid, published):
    """published: {rank: (green, yellow, orange, red, total)}"""
    failures = []
    for rank, cols in grid.items():
        c = Counter(cols.values())
        got = (c['green'], c['yellow'], c['orange'], c['red'])
        total = sum(POINTS[k] * v for k, v in c.items() if k in POINTS)
        if rank in published and (got + (total,)) != tuple(published[rank]):
            failures.append((rank, got + (total,), tuple(published[rank])))
    return failures

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', help='image path: print detected grid boundaries')
    ap.add_argument('--probe-y', type=int, default=71,
                    help='y of a text-free strip inside a data row, for column detection')
    ap.add_argument('--extract', metavar='DIR', help='directory holding the images in SPEC')
    args = ap.parse_args()
    if args.probe:
        a = load(args.probe)
        xb = x_boundaries(a, args.probe_y)
        print('x boundaries:', xb)
        print('y boundaries:', y_boundaries(a, xb))
    if args.extract:
        grid = extract(args.extract)
        json.dump(grid, open('colors_by_rank.json', 'w'), indent=1)
        print(f'extracted {len(grid)} player columns -> colors_by_rank.json')
