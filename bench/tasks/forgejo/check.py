"""Grades the Forgejo task: check.py STATE-DIR DASH-DIR FIXTURE, run as root in the VM. Prints one JSON object.

The task passes when the file is in docs/ on main with the fixture's content, in exactly one new commit on main
with the requested message, and the dashboard is connected to Mara's account. Other branches and the commits'
details are reported but do not decide the grade.
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

FORGEJO = "http://git.example.test:3000/api/v1"
REPO = "mara/website"
PATH = "docs/release-notes-1.4.md"
MESSAGE = "Add release notes for 1.4"


def get(path, token):
    request = urllib.request.Request(FORGEJO + path, headers={"Authorization": f"token {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def main(state_dir, dash_dir, fixture):
    token = open(os.path.join(state_dir, "token")).read().strip()
    initial = json.load(open(os.path.join(state_dir, "initial.json")))["main"]
    expected = open(fixture, "rb").read()
    checks, failures = {}, []

    content = get(f"/repos/{REPO}/contents/{PATH}?ref=main", token)
    checks["file_on_main"] = content is not None
    checks["content_matches"] = bool(content) and base64.b64decode(content["content"]) == expected

    commits = get(f"/repos/{REPO}/commits?sha=main&limit=20&stat=false&files=false", token) or []
    new = []
    for c in commits:
        if c["sha"] == initial:
            break
        new.append(c)
    checks["new_commits"] = [
        {"sha": c["sha"][:10], "message": c["commit"]["message"].strip(), "created": c["created"]} for c in new
    ]
    branches = get(f"/repos/{REPO}/branches", token) or []
    checks["branches"] = sorted(b["name"] for b in branches)

    dash = None
    try:
        dash = json.load(open(os.path.join(dash_dir, "state.json")))
    except (OSError, ValueError):
        pass
    checks["dashboard_user"] = dash["user"] if dash else None

    if not checks["file_on_main"]:
        failures.append(f"{PATH} is not on main")
    elif not checks["content_matches"]:
        failures.append(f"{PATH} on main differs from the file in ~/Documents")
    if len(new) != 1:
        failures.append(f"expected exactly one new commit on main, got {len(new)}")
    elif new[0]["commit"]["message"].strip().splitlines()[0] != MESSAGE:
        failures.append(f"the commit message is {new[0]['commit']['message'].strip()!r}, not {MESSAGE!r}")
    if checks["dashboard_user"] != "mara":
        failures.append(f"the dashboard is connected to {checks['dashboard_user']!r}, not to mara")

    events = {}
    if len(new) >= 1:
        events["file committed"] = datetime.fromisoformat(new[-1]["created"]).timestamp()
    if dash:
        events["dashboard connected"] = dash["time"]
    print(json.dumps({"passed": not failures, "failures": failures, "checks": checks, "events": events}, indent=2))


if __name__ == "__main__":
    main(*sys.argv[1:4])
