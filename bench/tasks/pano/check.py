"""Grades the pano task from the viewer's saved opening view, run as root in the VM. Prints one JSON object with passed,
failures, checks and events.

The opening view is a direction: the one at the centre of the view when it was saved, in degrees from the photo's
left edge, as the viewer's scale names them. The task passes when it lies within the variant's tolerance of the
target's direction (view.json), counted the shorter way round. The view as it is turned, without a save, is not
graded. The checks also tell how the view was turned, from the page's log: the turns, how long their keys were held,
and the holds of S that were let go too soon. The milestones come from the viewer's logs of views and saves.

    check.py --state DIR [--target DIR] [--since VM-TIME]

DIR of --target is a variant's directory, by default the default variant's (see the file variants).
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# A key that was down for less than this, in milliseconds, was pressed and not held: a tap. A key action holds its
# key for about 5 ms.
TAP_MS = 50


def default_variant():
    with open(os.path.join(HERE, "variants")) as f:
        for line in f:
            name, *rest = line.split()
            if rest == ["default"]:
                return name
    raise SystemExit("no default variant")


def load(target):
    """Makes the variant in the directory target the one graded: its view.json."""
    global TARGET, VIEW, HEADING, TOLERANCE, NAME
    TARGET = target
    with open(os.path.join(target, "view.json")) as f:
        VIEW = json.load(f)
    HEADING, TOLERANCE, NAME = VIEW["target_heading"], VIEW["tolerance"], VIEW["target"]


load(os.path.join(HERE, default_variant()))


def off(heading):
    """How far a direction lies from the target's, in degrees from -180 up to 180: positive to its right."""
    return round((heading - HEADING + 180) % 360 - 180, 3)


def on_target(heading):
    return heading is not None and abs(off(heading)) <= TOLERANCE


def turned(page):
    """How the view was turned, from the page's events: its turns and the keys held for them."""
    turns = [e for e in page if e.get("type") == "turn"]
    held = [e["ms"] for e in turns]
    return {
        "turns": len(turns),
        "fast": sum(1 for e in turns if e.get("fast")),
        "taps": sum(1 for ms in held if ms < TAP_MS),
        "held_ms": round(sum(held), 1),
        "longest_ms": max(held, default=None),
        "autorepeats": sum(1 for e in page if e.get("type") == "keydown" and e.get("repeat")),
        "early_saves": sum(1 for e in page if e.get("type") == "save_released"),
    }


def grade(state, page=()):
    """passed, failures and checks for the viewer's state, {"view", "saved"}, and the page's events."""
    saved, view = state.get("saved"), state.get("view")
    failures = []
    if saved is None:
        failures.append("no opening view is saved")
    elif not on_target(saved):
        side = "right" if off(saved) > 0 else "left"
        failures.append(f"the centre of the saved opening view is {abs(off(saved)):.1f}° to the {side} of {NAME}: "
                        f"it is at {saved:.1f}°, and {NAME} at {HEADING:.1f}° (within {TOLERANCE:g}° passes)")
    checks = {
        "saved": saved,
        "off_deg": None if saved is None else off(saved),
        "view": view,
        "view_off_deg": None if view is None else off(view),
        "target": HEADING,
        "tolerance": TOLERANCE,
        **turned(page),
    }
    return {"passed": not failures, "failures": failures, "checks": checks}


def events(views, saves, since=0):
    """The milestones, in VM time: the first turn, the target in the centre, the view saved there and the last
    change."""
    views = [v for v in views if v["time"] >= since]
    saves = [s for s in saves if s["time"] >= since]
    first = views[0]["time"] if views else None
    centred = next((r["time"] for r in sorted(views + saves, key=lambda r: r["time"]) if on_target(r["heading"])), None)
    saved = next((s["time"] for s in saves if on_target(s["heading"])), None)
    last = max((r["time"] for r in views + saves), default=None)
    names = ("first turn", "target centred", "view saved", "last change")
    return {name: t for name, t in zip(names, (first, centred, saved, last)) if t is not None}


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", required=True, help="the viewer's state directory")
    parser.add_argument("--target", help="the variant's directory, by default the default variant's")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    args = parser.parse_args()
    if args.target:
        load(args.target)
    path = os.path.join(args.state, "state.json")
    state = {"view": VIEW["start"], "saved": None}
    if os.path.exists(path):
        with open(path) as f:
            state = json.load(f)
    page = [e for e in load_jsonl(os.path.join(args.state, "events.jsonl")) if e.get("received", 0) >= args.since]
    saves = load_jsonl(os.path.join(args.state, "saves.jsonl"))
    result = grade(state, page)
    result["checks"]["saves"] = sum(1 for s in saves if s["time"] >= args.since)
    result["events"] = events(load_jsonl(os.path.join(args.state, "views.jsonl")), saves, args.since)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
