import re, html, json, os, sys

UP = "/mnt/user-data/uploads"
FILES = ["2022main.html","2023main.html","2024main.html","2025main.html","2025alt.html"]

PICK_RE = re.compile(r"^(\d+)\.(\d+)$")
POS_RE  = re.compile(r"^(QB|RB|WR|TE|K|DEF|DL|LB|DB)\s*-\s*([A-Z]{2,3})?\s*\(?(\d+)?\)?$")

def visible_text(path):
    d = open(path, encoding="utf-8", errors="replace").read()
    d = re.sub(r"<script.*?</script>", "", d, flags=re.S|re.I)
    d = re.sub(r"<style.*?</style>", "", d, flags=re.S|re.I)
    t = re.sub(r"<[^>]+>", "|", d)
    t = html.unescape(t)
    return [p.strip() for p in t.split("|") if p.strip()]

def parse(path):
    parts = visible_text(path)
    header = " ".join(parts[:12])
    teams = rounds = None
    m = re.search(r"(\d+)\s*Teams", header)
    if m: teams = int(m.group(1))
    m = re.search(r"(\d+)\s*Rounds", header)
    if m: rounds = int(m.group(1))
    league = parts[1] if len(parts) > 1 else "?"

    picks = []
    managers = []
    cur = None
    i = 0
    while i < len(parts):
        p = parts[i]
        # A manager header is a token repeated twice in a row, not a pick label
        if (i+1 < len(parts) and parts[i+1] == p and not PICK_RE.match(p)
                and not POS_RE.match(p) and len(p) < 30):
            cur = p
            if cur not in managers: managers.append(cur)
            i += 2
            continue
        m = PICK_RE.match(p)
        if m and cur and i+2 < len(parts):
            rnd, slot = int(m.group(1)), int(m.group(2))
            name = parts[i+1]
            pm = POS_RE.match(parts[i+2])
            if pm:
                picks.append(dict(manager=cur, rnd=rnd, slot=slot, player=name,
                                  pos=pm.group(1), nfl=(pm.group(2) or "FA")))
                i += 3
                continue
        i += 1

    # overall pick number: snake order
    for pk in picks:
        r, s = pk["rnd"], pk["slot"]
        pk["overall"] = (r-1)*teams + s
    picks.sort(key=lambda x: x["overall"])
    return dict(file=os.path.basename(path), league=league, teams=teams,
                rounds=rounds, managers=managers, picks=picks)

out = {}
for f in FILES:
    d = parse(os.path.join(UP, f))
    out[f.replace(".html","")] = d
    exp = (d["teams"] or 0) * (d["rounds"] or 0)
    print(f"{f}: league={d['league']!r} {d['teams']}T x {d['rounds']}R "
          f"managers={len(d['managers'])} picks={len(d['picks'])} expected={exp}")
    # sanity: duplicate overall numbers?
    ov = [p['overall'] for p in d['picks']]
    dupes = [x for x in set(ov) if ov.count(x) > 1]
    missing = [x for x in range(1, exp+1) if x not in set(ov)]
    if dupes: print("   DUPES:", dupes[:20])
    if missing: print("   MISSING:", missing[:20])

json.dump(out, open("/home/claude/drafts.json","w"), indent=1)
