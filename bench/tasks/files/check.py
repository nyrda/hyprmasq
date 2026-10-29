"""Grades the file manager task from the folder itself, run as root in the VM. Prints one JSON object with passed,
failures, checks and events.

    check.py --root DIR --class CLASS --events HYPRLAND-EVENTS [--since VM-TIME] [--watch WATCH]

The task and its grade are bench/laptop/run_files.py's: rename report-draft.txt in the folder Project to
report-final.txt and move it into the folder Archive. The task passes when the report is in Archive under its new
name with its contents unchanged, every other file and folder is as it was, and no window of another app than the
file manager opened while the agent worked. The file manager's own dialogs, such as a rename conflict, are of its
class, CLASS. The windows come from a log of Hyprland's events that root keeps (the form task's windows.py), and
without it the run fails.

Its milestones come from watch.py's record of the folder, WATCH: the first change, the report renamed (a file
report-final.txt with its contents anywhere in the folder), the report moved (its contents in Archive under any name),
and the folder right, the first time it was as the grade wants it.
"""

import argparse
import hashlib
import json
import os

# The folder at the start, as bench/laptop/run_files.py has it: path relative to Project, and the file's contents,
# or None for a folder.
START = {
    "Archive": None,
    "Archive/report-2025.txt": "Quarterly report 2025, final.\n",
    "Drafts": None,
    "Drafts/ideas.txt": "Ideas for the spring campaign.\n",
    "budget.csv": "item,amount\nprinting,120\nvenue,800\n",
    "notes.txt": "Call the printer on Monday.\n",
    "report-draft.txt": "Quarterly report 2026, ready for review.\n",
}
EXPECTED = {("Archive/report-final.txt" if p == "report-draft.txt" else p): c for p, c in START.items()}


def digest(contents):
    return hashlib.sha256(contents.encode()).hexdigest() if contents is not None else None


REPORT = digest(START["report-draft.txt"])


def tree(root):
    """The folder's files and folders, relative to root: a file's SHA-256, None for a folder."""
    found = {}
    for path, dirs, files in os.walk(root):
        for name in dirs:
            found[os.path.relpath(os.path.join(path, name), root)] = None
        for name in files:
            with open(os.path.join(path, name), "rb") as f:
                found[os.path.relpath(os.path.join(path, name), root)] = hashlib.sha256(f.read()).hexdigest()
    return found


def expected_tree():
    return {p: digest(c) for p, c in EXPECTED.items()}


def tree_failures(found):
    want = expected_tree()
    failures = [f"missing: {p}" for p in sorted(want) if p not in found]
    failures += [f"unexpected: {p}" for p in sorted(found) if p not in want]
    failures += [f"changed: {p}" for p in sorted(want) if p in found and found[p] != want[p]]
    return failures


def other_windows(path, cls, since):
    """The windows of a class other than the file manager's, CLASS, that opened while the agent worked, from
    Hyprland's events; None without the log."""
    if not path or not os.path.exists(path):
        return None
    opened = []
    with open(path) as f:
        for line in f:
            t, _, event = line.rstrip("\n").partition(" ")
            name, _, data = event.partition(">>")
            if name != "openwindow" or float(t) < since:
                continue
            _, workspace, window_class, title = (data.split(",", 3) + ["", "", ""])[:4]
            if window_class != cls:
                opened.append({"class": window_class, "title": title, "workspace": workspace})
    return opened


def milestones(watch, since):
    """When the folder first changed, the report was renamed and moved, and the folder was right, in VM time, from
    watch.py's records of the folder."""
    start, want = {p: digest(c) for p, c in START.items()}, expected_tree()
    found = {}
    for record in watch:
        t, files = record["time"], record["tree"]
        if t < since:
            continue
        if files != start:
            found.setdefault("first change", t)
        if any(os.path.basename(p) == "report-final.txt" and h == REPORT for p, h in files.items()):
            found.setdefault("report renamed", t)
        if any(p.startswith("Archive/") and h == REPORT for p, h in files.items()):
            found.setdefault("report moved", t)
        if files == want:
            found.setdefault("folder right", t)
    return found


def grade(found, opened, watch=(), since=0):
    """The grade of the folder as found; opened is the other windows, None when their log is missing."""
    failures = tree_failures(found)
    if opened is None:
        failures.append("no record of the windows that opened")
    for w in opened or ():
        failures.append(f"a window other than the file manager's opened: {w['class']} {w['title']!r}")
    return {"passed": not failures, "failures": failures, "checks": {"tree": found, "other_windows": opened},
            "events": milestones(watch, since)}


def load_watch(path):
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, help="the folder Project")
    parser.add_argument("--class", required=True, dest="cls", help="the file manager's window class")
    parser.add_argument("--events", required=True, help="Hyprland's events, each line with its VM time")
    parser.add_argument("--since", type=float, default=0, help="VM time of the agent's start")
    parser.add_argument("--watch", help="watch.py's record of the folder, for the milestones")
    args = parser.parse_args()
    opened = other_windows(args.events, args.cls, args.since)
    print(json.dumps(grade(tree(args.root), opened, load_watch(args.watch), args.since), indent=2))


if __name__ == "__main__":
    main()
