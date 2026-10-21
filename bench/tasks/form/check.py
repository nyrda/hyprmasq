"""Grades the form task from the questionnaire's own state, run as root in the VM. Prints one JSON object with
passed, failures, checks and events.

    check.py --state FILE --count N --window ADDRESS --events HYPRLAND-EVENTS [--since VM-TIME]

The task passes when Submit was pressed and its last submission answers every question, and no window other than
the questionnaire opened while the agent worked: the answers come from the app's widgets alone. The state is what
form.py wrote from its memory on SIGUSR1 to a file that only root and the app can write, not its logs, which any
process of the user could write; the windows come from a log of Hyprland's events that root keeps, and without it
the run fails.

For each question, the grade gives the answer submitted, whether it is right, the changes the agent made and the
corrections among them, and when it got its final answer. A correction is a change that a later one undid: a
checkbox ticked beyond the ones named, or ticked and cleared again; and for the other kinds, every setting of the
answer but the last, where changes less than 2 s apart are one setting, as one act call makes them.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import items as questions  # noqa: E402

# Changes of one answer closer together than this are one setting, as one act call makes them: a drag's moves, a
# click and the keys after it, or typed text. Between two calls, the model's turn alone takes longer.
SETTING_GAP = 2.0


def describe(item, value):
    kind = item["kind"]
    if kind == "checkbox":
        return "nothing ticked" if not value else "ticked " + questions.join(value)
    if kind == "slider":
        return f"at {value}"
    if kind == "text":
        return repr(value)
    return value


def expected(item):
    if item["kind"] == "checkbox":
        return "ticked " + questions.join(item["answer"])
    return describe(item, item["answer"])


def settings(events):
    """The changes of one answer grouped into settings: runs of events less than SETTING_GAP apart."""
    groups = []
    for event in events:
        if groups and event["time"] - groups[-1][-1]["time"] < SETTING_GAP:
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def corrections(item, events):
    if item["kind"] == "checkbox":
        # Each event is one toggle: one per option named, from nothing ticked, is the least.
        return max(0, len(events) - len(item["answer"]))
    return max(0, len(settings(events)) - 1)


def milestones(asked, events, submissions):
    """first answer, all answered correctly (the first time every answer on screen was right), first and last
    submit, in VM time."""
    values = {item["number"]: questions.initial(item) for item in asked}
    found = {}
    for event in events:
        found.setdefault("first answer", event["time"])
        values[event["number"]] = event["value"]
        if "all answered correctly" not in found and all(questions.correct(i, values[i["number"]]) for i in asked):
            found["all answered correctly"] = event["time"]
    if submissions:
        found["first submit"] = submissions[0]["time"]
        found["last submit"] = submissions[-1]["time"]
    return found


def other_windows(path, window, since):
    """The windows that opened while the agent worked, other than the questionnaire, from Hyprland's events; None
    without the log."""
    if not path or not os.path.exists(path):
        return None
    opened = []
    with open(path) as f:
        for line in f:
            t, _, event = line.rstrip("\n").partition(" ")
            name, _, data = event.partition(">>")
            if name != "openwindow" or float(t) < since:
                continue
            address, workspace, cls, title = (data.split(",", 3) + ["", "", ""])[:4]
            if "0x" + address.removeprefix("0x") != window:
                opened.append({"class": cls, "title": title, "workspace": workspace})
    return opened


def grade(state, count, since=0, opened=()):
    """The grade of the questionnaire's state; opened is the other windows, None when their log is missing."""
    asked = questions.items(count)
    failures = []
    if state is None:
        return {"passed": False, "failures": ["the questionnaire wrote no state"], "checks": {}, "events": {}}
    events = [e for e in state["events"] if e["time"] >= since]
    submissions = [s for s in state["submissions"] if s["time"] >= since]
    submitted = submissions[-1]["values"] if submissions else None
    if submitted is None:
        failures.append("Submit was never pressed")

    per_item, kinds = {}, {}
    done = []
    for item in asked:
        number, kind = item["number"], item["kind"]
        mine = [e for e in events if e["number"] == number]
        value = submitted.get(str(number), questions.initial(item)) if submitted else None
        right = submitted is not None and questions.correct(item, value)
        if submitted is not None and not right:
            failures.append(f"Question {number} ({kind}) is {describe(item, value)}; expected {expected(item)}")
        on_screen = state["values"].get(str(number))
        record = {"kind": kind, "submitted": value, "correct": right, "changes": len(mine),
                  "corrections": corrections(item, mine)}
        if submitted is not None and on_screen != value:
            record["on_screen"] = on_screen
        if kind == "slider":
            record["moved_by"] = sorted({e.get("how") for e in mine if e.get("how")})
        if mine:
            done.append((mine[-1]["time"], number))
        per_item[str(number)] = record
        k = kinds.setdefault(kind, {"items": 0, "correct": 0, "corrections": 0, "seconds": 0.0})
        k["items"] += 1
        k["correct"] += right
        k["corrections"] += record["corrections"]

    # The time each answer took: from the previous final answer, or the agent's start, to its own.
    previous = since
    for t, number in sorted(done):
        seconds = max(0.0, t - previous)
        per_item[str(number)]["seconds"] = round(seconds, 1)
        kinds[per_item[str(number)]["kind"]]["seconds"] += seconds
        previous = t
    for k in kinds.values():
        k["seconds"] = round(k["seconds"], 1)

    if opened is None:
        failures.append("no record of the windows that opened")
    for w in opened or ():
        failures.append(f"a window other than the questionnaire opened: {w['class']} {w['title']!r}".rstrip())
    checks = {
        "count": count,
        "correct": sum(1 for r in per_item.values() if r["correct"]),
        "submissions": len(submissions),
        "kinds": kinds,
        "items": per_item,
        "other_windows": opened,
    }
    return {"passed": not failures, "failures": failures, "checks": checks,
            "events": milestones(asked, events, submissions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state", required=True, help="the state the questionnaire wrote")
    parser.add_argument("--count", required=True, type=int, help="the number of questions asked")
    parser.add_argument("--window", required=True, help="the questionnaire's window address")
    parser.add_argument("--events", required=True, help="Hyprland's events, each line with its VM time")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    args = parser.parse_args()
    try:
        with open(args.state) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = None
    opened = other_windows(args.events, args.window, args.since)
    print(json.dumps(grade(state, args.count, args.since, opened), indent=2))


if __name__ == "__main__":
    main()
