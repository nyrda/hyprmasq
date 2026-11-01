#!/usr/bin/env python3
"""The Mac's series as one dataset of docs/comparison/data: mac_dataset.py RESULTS-DIR OUT

Joins the comparison.json that analyze.py wrote in each set's directory of RESULTS-DIR, such as
results/mac-2026-10-31/files-cua-gpt-6.1-sol, into OUT, the runs sorted by task, tool, model and rep. OUT keeps the
`about` and `sources` it holds, which describe the series for a reader of the data alone: write them by hand once,
then run this again after every change of the runs or of analyze.py.
"""

import glob
import json
import os
import sys


def main():
    results, out = sys.argv[1:3]
    runs = [r for path in sorted(glob.glob(os.path.join(results, "*", "comparison.json"))) for r in json.load(open(path))]
    if not runs:
        raise SystemExit(f"mac_dataset.py: no comparison.json in the directories of {results}: run analyze.py first")
    runs.sort(key=lambda r: (r["task"], r["backend"], r["model"], r["rep"]))
    header = {}
    if os.path.exists(out):
        old = json.load(open(out))
        if isinstance(old, dict):
            header = {k: old[k] for k in ("about", "sources") if k in old}
    with open(out, "w") as f:
        json.dump({**header, "runs": runs}, f, indent=1)
        f.write("\n")
    print(f"{out}: {len(runs)} runs")


if __name__ == "__main__":
    main()
