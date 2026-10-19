"""Grades the board task from the guide's saved state, run as root in the VM. Prints one JSON object with passed,
failures, checks and events.

The task passes when each of the nine labels lies inside one of its boxes in regions.json. For each label, the grade
tells where it lies, in photo pixels, and what that is: its part (correct), another label's part, a distractor of
regions.json, elsewhere on the photo, or still in the tray. The milestones come from the guide's log of saves.

    check.py --state DIR [--since VM-TIME]
"""

import argparse
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REGIONS = json.load(open(os.path.join(HERE, "regions.json")))
LABELS = list(REGIONS["labels"])


def inside(box, point):
    x0, y0, x1, y1 = box
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


def classify(label, point):
    """Where a label lies: {"verdict": correct, other label, distractor, elsewhere or tray, "on": the part's name}."""
    if point is None:
        return {"verdict": "tray", "on": None}
    if any(inside(box, point) for box in REGIONS["labels"][label]):
        return {"verdict": "correct", "on": label}
    for other, boxes in REGIONS["labels"].items():
        if other != label and any(inside(box, point) for box in boxes):
            return {"verdict": "other label", "on": other}
    for name, boxes in REGIONS["distractors"].items():
        if any(inside(box, point) for box in boxes):
            return {"verdict": "distractor", "on": name}
    return {"verdict": "elsewhere", "on": None}


def describe(label, point, verdict):
    at = f"at ({point[0]:g}, {point[1]:g})" if point else ""
    if verdict["verdict"] == "tray":
        return f"{label} is still in the tray"
    if verdict["verdict"] == "other label":
        return f"{label} is on the part of {verdict['on']}, {at}"
    if verdict["verdict"] == "distractor":
        return f"{label} is on the {verdict['on']}, {at}"
    return f"{label} is {at}, on no part"


def grade(labels):
    """passed, failures and checks for the labels' points: {label: [x, y] or None}."""
    checks = {"labels": {}}
    failures = []
    for label in LABELS:
        point = labels.get(label)
        verdict = classify(label, point)
        checks["labels"][label] = {"point": point, **verdict}
        if verdict["verdict"] != "correct":
            failures.append(describe(label, point, verdict))
    checks["correct"] = sum(1 for v in checks["labels"].values() if v["verdict"] == "correct")
    return {"passed": not failures, "failures": failures, "checks": checks}


def events(places, since=0):
    """The milestones, in VM time: the first label placed, all labels placed and the last change."""
    first = complete = last = None
    labels = {label: None for label in LABELS}
    for place in places:
        if place["time"] < since:
            continue
        labels[place["label"]] = place["point"]
        if place["point"] is not None and first is None:
            first = place["time"]
        if all(p is not None for p in labels.values()) and complete is None:
            complete = place["time"]
        last = place["time"]
    names = ("first label placed", "all labels placed", "last change")
    return {name: t for name, t in zip(names, (first, complete, last)) if t is not None}


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", required=True, help="the guide's state directory")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    args = parser.parse_args()
    path = os.path.join(args.state, "board.json")
    state = json.load(open(path)) if os.path.exists(path) else {"labels": {}}
    places = load_jsonl(os.path.join(args.state, "places.jsonl"))
    result = grade(state["labels"])
    result["checks"]["variant"] = state.get("variant")
    result["checks"]["saves"] = sum(1 for p in places if p["time"] >= args.since)
    result["events"] = events(places, args.since)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
