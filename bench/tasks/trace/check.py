"""Grades the trace task from the figure's saved drawing, run as root in the VM. Prints one JSON object with passed,
failures, checks and events.

The drawing is measured against the target's line in the variant's track.json, in photo pixels: the waterline of a river
bank. A point is on the target when it lies within the line's half-width there
plus the variant's slack of it (figure.json). The task passes when the drawing covers at least COVERAGE of the line's required rows, a point of
the line counting as covered when a stroke passes within that distance of it, and at most OFF_LINE of the drawing's
length, relative to the required part's length, lies off the line. Drawing along the line outside the required rows
is neither required nor off it. The milestones come from the figure's log of saves.

    check.py --state DIR [--target DIR] [--since VM-TIME]

DIR of --target is a variant's directory, by default the default variant's (see the file variants).
"""

import argparse
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# How far beyond the line's half-width the centre of a stroke may lie and still be on it, in photo pixels, by default:
# half the brush's 3 pixels and a pixel of blur. A variant sets its own in figure.json, "slack": 5 pixels of the
# agent's screenshot at the default display, 1920×1080, where the page scales the photo to fit, since an agent aims
# in the screenshot's pixels.
SLACK = 2.5
COVERAGE = 0.95
OFF_LINE = 0.10
# Drawn strokes are measured every this many photo pixels along them.
SAMPLE = 1.0


def default_variant():
    for line in open(os.path.join(HERE, "variants")):
        name, *rest = line.split()
        if rest == ["default"]:
            return name
    raise SystemExit("no default variant")


def load(target):
    """Makes the variant in the directory target the one graded: its track.json and figure.json."""
    global TARGET, TRACK, FIGURE, LINE, REQUIRED, NAME, SLACK
    TARGET = target
    TRACK = json.load(open(os.path.join(target, "track.json")))
    FIGURE = json.load(open(os.path.join(target, "figure.json")))
    # The line from the top of the photo down: (x, y, half-width).
    LINE = sorted((tuple(p) for p in TRACK["line"]), key=lambda p: p[1])
    lo, hi = TRACK["required"]
    REQUIRED = [p for p in LINE if lo <= p[1] <= hi]
    NAME = FIGURE["target"]
    SLACK = FIGURE.get("slack", 2.5)


load(os.path.join(HERE, default_variant()))


def segment_distance(p, a, b):
    """The distance from p to the segment a-b, and where along it the nearest point is, from 0 at a to 1 at b."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / length2))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy), t


def to_line(p):
    """How far p is from the line, and the tolerance there: the half-width there plus SLACK."""
    best = None
    for a, b in zip(LINE, LINE[1:]):
        d, t = segment_distance(p, a, b)
        if best is None or d < best[0]:
            best = (d, a[2] + t * (b[2] - a[2]) + SLACK)
    return best


def segments(strokes):
    """The segments of the strokes' paths, a dot as a segment of no length."""
    out = []
    for stroke in strokes:
        points = stroke["points"]
        if len(points) == 1:
            out.append((points[0], points[0]))
        out += list(zip(points, points[1:]))
    return out


def samples(strokes):
    """Points along the strokes, each with the length of the drawing it stands for: the middles of pieces at most
    SAMPLE long, which add up to the segment's length however close the points of a stroke lie, and every segment's
    ends with no length, so that the farthest point counts. A dot stands for SAMPLE."""
    out = []
    for a, b in segments(strokes):
        if a == b:
            out.append((tuple(a), SAMPLE))
            continue
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        n = math.ceil(length / SAMPLE)
        out += [((a[0] + (b[0] - a[0]) * (i + 0.5) / n, a[1] + (b[1] - a[1]) * (i + 0.5) / n), length / n)
                for i in range(n)]
        out += [(tuple(a), 0.0), (tuple(b), 0.0)]
    return out


def required_length():
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(REQUIRED, REQUIRED[1:]))


def runs(rows):
    """Contiguous runs of the rows, as (first, last)."""
    out = []
    for y in rows:
        if out and y - out[-1][1] <= 2:
            out[-1][1] = y
        else:
            out.append([y, y])
    return [tuple(r) for r in out]


def grade(strokes):
    """passed, failures and checks for a drawing: a list of strokes, each {"kind", "points"}."""
    segs = segments(strokes)
    covered = []
    for x, y, half in REQUIRED:
        near = min((segment_distance((x, y), a, b)[0] for a, b in segs), default=math.inf)
        covered.append(near <= half + SLACK)
    coverage = sum(covered) / len(REQUIRED)
    missed = runs([p[1] for p, c in zip(REQUIRED, covered) if not c])

    measured = [(p, w, *to_line(p)) for p, w in samples(strokes)]
    off = [(p, d) for p, _, d, tol in measured if d > tol]
    drawn = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in segs)
    length = required_length()
    off_length = round(sum(w for _, w, d, tol in measured if d > tol), 1)
    worst = max(off, key=lambda o: o[1], default=None)

    failures = []
    if not strokes:
        failures.append("nothing is drawn")
    elif coverage < COVERAGE:
        where = ", ".join(f"y {a:g} to {b:g}" if a != b else f"y {a:g}" for a, b in missed)
        failures.append(f"the drawing covers {coverage:.0%} of {NAME}; it leaves {NAME} uncovered at {where} "
                        f"(the photo is {TRACK['size'][1]} pixels high, 0 at the top)")
    if off_length > OFF_LINE * length:
        failures.append(f"{off_length:g} px of the drawing lie off {NAME}, up to {worst[1]:.1f} px from it, "
                        f"at ({worst[0][0]:.1f}, {worst[0][1]:.1f})")
    kinds = {}
    for stroke in strokes:
        kinds[stroke["kind"]] = kinds.get(stroke["kind"], 0) + 1
    deviations = [d for _, _, d, _ in measured]
    checks = {
        "coverage": round(coverage, 3),
        "missed": missed,
        "off_line_px": off_length,
        "drawn_px": round(drawn, 1),
        "required_px": round(length, 1),
        "mean_deviation_px": round(sum(deviations) / len(deviations), 2) if deviations else None,
        "max_deviation_px": round(max(deviations), 2) if deviations else None,
        "worst": [round(v, 1) for v in worst[0]] if worst else None,
        "strokes": len(strokes),
        "kinds": kinds,
    }
    return {"passed": not failures, "failures": failures, "checks": checks}


def events(saves, since=0):
    """The milestones, in VM time: the first stroke, the drawing passing and the last change."""
    first = passing = last = None
    for save in saves:
        if save["time"] < since:
            continue
        if save["change"] == "stroke" and first is None:
            first = save["time"]
        if passing is None and grade(save["strokes"])["passed"]:
            passing = save["time"]
        last = save["time"]
    names = ("first stroke", "line drawn", "last change")
    return {name: t for name, t in zip(names, (first, passing, last)) if t is not None}


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", required=True, help="the figure's state directory")
    parser.add_argument("--target", help="the variant's directory, by default the default variant's")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    args = parser.parse_args()
    if args.target:
        load(args.target)
    path = os.path.join(args.state, "strokes.json")
    state = json.load(open(path)) if os.path.exists(path) else {"strokes": []}
    saves = [s for s in load_jsonl(os.path.join(args.state, "saves.jsonl")) if s["time"] >= args.since]
    result = grade(state["strokes"])
    changes = {}
    for save in saves:
        changes[save["change"]] = changes.get(save["change"], 0) + 1
    result["checks"]["changes"] = changes
    result["events"] = events(saves, args.since)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
