#!/usr/bin/env python3
"""What of the user's machine a dataset would put into the repository: mac_privacy.py FILE... [--watch WORDS]

The comparison's runs on a laptop or a Mac work on the user's own machine. Their transcripts and screenshots stay on
it, but analyze.py's comparison.json, which docs/comparison/data publishes, keeps text from the runs: the agent's
final message, the text of failed tool calls, the failures, and the apps that took the focus. This reads each FILE,
a comparison.json or a dataset of docs/comparison/data, and lists what in it may be personal:

- paths in a home folder, ~/ paths, e-mail addresses, web addresses and IPv4 and IPv6 addresses, in any of its
  strings,
- on the Mac, apps in the focus changes and failures other than the users' and the tasks' apps of the runs,
- each line of WORDS, such as names, hosts or projects, wherever it occurs.

It exits with 1 when it lists anything, so that a dataset is looked at before it is committed. A hit is something
to look at, not proof of a leak. It reads only the files and sends nothing anywhere.
"""

import argparse
import json
import re
import sys

PATTERNS = {
    "home path": re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|(?<![\w/])~/[^\s\"'<>)\]]+"),
    "e-mail": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "web address": re.compile(r"https?://[^\s\"'<>)\]]+"),
    "IP address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    # IPv6: a :: abbreviation, or four groups and more, so that a time such as 10:00:00 is none.
    "IPv6 address": re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){1,7}:(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?"
                               r"(?![\w:])|(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}(?![\w:])"),
}
# The apps of the Mac's runs: the users' windows and the tasks'. Another app's name in a run is the user's.
MAC_APPS = {"TextEdit", "ChatGPT", "Claude", "Questionnaire", "Finder", "Inkscape"}
OTHER_APP = re.compile(r"an app other than \S+ came to the front: (.+)$")


def strings(value, path=""):
    """Every string of a JSON value, with where it is."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from strings(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v, f"{path}[]")
    elif isinstance(value, str):
        yield path, value


def run_name(run):
    return f'{run.get("task")} {run.get("backend")}-{run.get("rep")}'


def audit(runs, watch):
    hits = []
    for run in runs:
        for where, text in strings(run):
            for kind, pattern in PATTERNS.items():
                hits += [(run_name(run), kind, m.group(0), where) for m in pattern.finditer(text)]
            hits += [(run_name(run), "watched", w, where) for w in watch if w.lower() in text.lower()]
        if run.get("platform") != "macos":
            continue
        apps = {e.get("to") for e in (run.get("focus") or {}).get("events", []) if isinstance(e, dict)}
        apps |= {m.group(1) for f in run.get("failures", []) for m in [OTHER_APP.search(f)] if m}
        hits += [(run_name(run), "app", a, "focus, failures") for a in sorted(apps - MAC_APPS - {None})]
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--watch", help="a file of words to look for, one a line, such as names or hosts")
    args = parser.parse_args()
    watch = [w.strip() for w in open(args.watch)] if args.watch else []
    watch = [w for w in watch if w and not w.startswith("#")]
    found = False
    for path in args.files:
        data = json.load(open(path))
        # A list of runs, or an object of a dataset whose runs are under "runs" and whose other fields are read too.
        runs = data if isinstance(data, list) else data.get("runs", []) + [{k: v for k, v in data.items() if k != "runs"}]
        for name, kind, match, where in audit(runs, watch):
            print(f"{path}: {name}: {kind} {match!r} in {where}")
            found = True
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
