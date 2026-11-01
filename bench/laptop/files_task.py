"""The file manager task's folder and grade, shared by run_files.py on Hyprland and run_mac.py on a Mac.

The agent renames Project/report-draft.txt to report-final.txt and moves it into Project/Archive. The grade compares
every file's contents and place with the folder the task expects.
"""

import hashlib
import os

# The folder at the start: path relative to Project, and the file's contents, or None for a folder.
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


def make(root):
    """A fresh Project folder at root, as START gives it."""
    for path, contents in START.items():
        full = os.path.join(root, path)
        if contents is None:
            os.makedirs(full, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(contents)


def tree(root, ignore=()):
    """The folder's files and folders, relative to root: a file's SHA-256, None for a folder. Files named in ignore,
    such as the .DS_Store that Finder writes, are left out."""
    found = {}
    for path, dirs, files in os.walk(root):
        for name in dirs:
            found[os.path.relpath(os.path.join(path, name), root)] = None
        for name in files:
            if name in ignore:
                continue
            with open(os.path.join(path, name), "rb") as f:
                found[os.path.relpath(os.path.join(path, name), root)] = hashlib.sha256(f.read()).hexdigest()
    return found


def expected_tree():
    return {p: (hashlib.sha256(c.encode()).hexdigest() if c is not None else None) for p, c in EXPECTED.items()}


def grade(root, ignore=()):
    found, want = tree(root, ignore), expected_tree()
    failures = [f"missing: {p}" for p in sorted(want) if p not in found]
    failures += [f"unexpected: {p}" for p in sorted(found) if p not in want]
    failures += [f"changed: {p}" for p in sorted(want) if p in found and found[p] != want[p]]
    return failures, found
