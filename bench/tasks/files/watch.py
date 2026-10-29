"""Watches Mara's folder while the agent works, for the task's milestones: watch.py ROOT OUT

Four times a second this reads the folder's files and folders as check.py does, and appends a line to OUT with the
time whenever they changed: {"time", "tree"}. Root runs it, where the user's programs cannot change OUT.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from check import tree  # noqa: E402


def main():
    root, path = sys.argv[1], sys.argv[2]
    out = open(path, "a", buffering=1)
    last = None
    while True:
        try:
            files = tree(root)
        except OSError:
            # A file that went away between listing and reading it, as in a move: read the folder again.
            files = None
        if files is not None and files != last:
            out.write(json.dumps({"time": time.time(), "tree": files}) + "\n")
            last = files
        time.sleep(0.25)


if __name__ == "__main__":
    main()
