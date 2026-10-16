#!/usr/bin/env python3
"""Counts the frictions of the Thunderbird runs that docs/benchmarks.md discusses.

    analyze.py RESULTS-DIR

Prints one table row per run and writes RESULTS-DIR/friction.json. Each count comes from the MCP log and
Hyprland's events of a run:

- wait_acts: act calls without actions, made only to wait for the window.
- pause_s: pause actions the agent added, in seconds.
- quiet_overrides: act calls that set wait.quiet_ms or wait.expect_ms.
- new_windows: windows that mapped during the run after the start.
- missed: those that mapped after the act that caused them had returned, so no result reported them.
- followups: windows or screenshot calls right after a new window mapped, before the agent acted on it.
- user_ws: new windows that mapped on the user's visible workspace.
- closing_errors: window_gone errors from input to a window that closed, as a dialog does on its OK button.
- timeouts: act calls whose wait timed out.
- overhead_s: time in act calls beyond the window's wait, the requested pauses and typing at 30 ms a
  character: sending, capturing and the server.
"""

import json
import os
import re
import sys

import report

WAIT = re.compile(r"(?:quiet \d+ ms(?: except [^·]*)?|no redraw[^·]*) · (\d+) ms|timeout after (\d+) ms")


def events(run_dir, offset):
    path = os.path.join(run_dir, "vm-events.log")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            t, _, event = line.rstrip("\n").partition(" ")
            name, _, data = event.partition(">>")
            out.append((float(t) + offset, name, data))
    return out


def analyze(run_dir):
    run = json.load(open(os.path.join(run_dir, "run.json")))
    ready = json.load(open(os.path.join(run_dir, "ready.json")))
    offset = ready["host_time"] - ready["vm_time"]
    start = run["agent_start"]
    tap = report.load_jsonl(os.path.join(run_dir, "mcp.jsonl"))
    calls = report.tool_calls(tap)

    args = {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            args[entry["t"]] = msg["params"].get("arguments") or {}

    wait_acts = pause_ms = overrides = closing_errors = timeouts = 0
    overhead = 0.0
    for c in calls:
        if c["tool"] != "act" or c.get("end") is None:
            continue
        a = args.get(c["start"], {})
        actions = a.get("actions") or []
        wait_acts += not actions
        pauses = sum(x.get("ms", 0) for x in actions if x.get("action") == "pause")
        pause_ms += pauses
        wait = a.get("wait") or {}
        overrides += bool(wait.get("quiet_ms") or wait.get("expect_ms"))
        closing_errors += "window_gone" in c["summary"] and "closed while" in c["summary"]
        timeouts += "timeout after" in c["summary"]
        typed = sum(len(x.get("text", "")) for x in actions if x.get("action") == "type")
        m = WAIT.search(c["summary"])
        waited = int(m.group(1) or m.group(2)) / 1000 if m else 0
        overhead += max(0.0, c["seconds"] - waited - pauses / 1000 - typed * 0.03)

    user_ws = str(ready["user"]["workspace"])
    opened = [(t, data) for t, name, data in events(run_dir, offset) if name == "openwindow" and t > start]
    missed = followups = on_user = 0
    for t, data in opened:
        on_user += data.split(",", 3)[1] == user_ws
        cause = [c for c in calls if c["start"] <= t and c["tool"] == "act"]
        if cause and cause[-1].get("end") and cause[-1]["end"] < t:
            missed += 1
        for c in [c for c in calls if c["start"] > t][:3]:
            if c["tool"] not in ("windows", "screenshot"):
                break
            followups += 1

    return {
        "run": os.path.basename(run_dir),
        "wait_acts": wait_acts,
        "pause_s": round(pause_ms / 1000, 1),
        "quiet_overrides": overrides,
        "new_windows": len(opened),
        "missed": missed,
        "followups": followups,
        "user_ws": on_user,
        "closing_errors": closing_errors,
        "timeouts": timeouts,
        "overhead_s": round(overhead, 1),
        "acts": sum(1 for c in calls if c["tool"] == "act"),
    }


def main(results_dir):
    rows = []
    for name in sorted(os.listdir(results_dir)):
        path = os.path.join(results_dir, name)
        if os.path.exists(os.path.join(path, "summary.json")):
            rows.append(analyze(path))
    keys = list(rows[0].keys())
    print("| " + " | ".join(keys) + " |")
    print("| " + " | ".join("---" for _ in keys) + " |")
    for r in rows:
        print("| " + " | ".join(str(r[k]) for k in keys) + " |")
    with open(os.path.join(results_dir, "friction.json"), "w") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    main(sys.argv[1])
