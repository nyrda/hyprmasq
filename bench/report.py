#!/usr/bin/env python3
"""Metrics of benchmark runs, and the report of a results directory.

    report.py RESULTS-DIR

computes summary.json of every finished run in it again from the run's raw files, and writes
RESULTS-DIR/report.md and RESULTS-DIR/runs.json. run.py calls metrics() for each run when it finishes.
"""

import json
import os
import statistics
import sys


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# Readable names for the Thunderbird task's events, whose runs predate the names.
PHASE_NAMES = {"first_imap_login": "account verified", "first_submission": "reply sent"}


def tool_calls(tap):
    """The tools/call requests in the MCP log with their responses."""
    pending, calls = {}, []
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            pending[msg.get("id")] = (entry["t"], msg["params"])
        elif entry["dir"] == "response" and msg.get("id") in pending:
            start, params = pending.pop(msg["id"])
            result = msg.get("result") or {}
            content = result.get("content") or []
            text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
            images = [c for c in content if c.get("type") == "image"]
            args = params.get("arguments") or {}
            calls.append({
                "tool": params.get("name"),
                "start": start,
                "end": entry["t"],
                "seconds": entry["t"] - start,
                "actions": [a.get("action") for a in args.get("actions", [])] if isinstance(args.get("actions"), list) else None,
                "error": bool(result.get("isError")) or "error" in msg,
                "summary": text.split("\n", 1)[0][:300],
                "hint": "\n".join(text.split("\n")[1:])[:600],
                "images": len(images),
                "image_bytes": sum(i.get("bytes", 0) for i in images),
                "no_change": "no change" in text.split("\n", 1)[0],
                "no_redraw": "no redraw within" in text,
            })
    for start, params in pending.values():
        calls.append({"tool": params.get("name"), "start": start, "end": None, "seconds": None, "unanswered": True})
    return calls


def union_seconds(intervals):
    total, last_end = 0.0, None
    for start, end in sorted(intervals):
        if last_end is None or start > last_end:
            total += end - start
            last_end = end
        elif end > last_end:
            total += end - last_end
            last_end = end
    return total


def agent_usage(harness, stream):
    """Tokens and turns from the agent's own event stream."""
    usage = {}
    if harness == "claude":
        for e in stream:
            line = e["line"]
            if line.get("type") == "result":
                u = line.get("usage") or {}
                usage = {
                    "turns": line.get("num_turns"),
                    "input_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0),
                    "output_tokens": u.get("output_tokens"),
                    "thinking_tokens": (u.get("output_tokens_details") or {}).get("thinking_tokens"),
                    "api_seconds": (line.get("duration_api_ms") or 0) / 1000,
                    "final": line.get("result"),
                }
    else:
        final = None
        for e in stream:
            line = e["line"]
            if line.get("type") == "turn.completed":
                u = line.get("usage") or {}
                usage = {
                    "input_tokens": u.get("input_tokens"),
                    "output_tokens": u.get("output_tokens"),
                    "thinking_tokens": u.get("reasoning_output_tokens"),
                }
            item = line.get("item") or {}
            if line.get("type") == "item.completed" and item.get("type") == "agent_message":
                final = item.get("text")
        usage["final"] = final
    return usage


def user_disturbance(run_dir, ready, start, end, offset):
    """What the run did to the user's visible workspace, from Hyprland's events in the VM: windows that opened
    on it, how long the keyboard focus was away from the user's window, and switches of the active workspace."""
    path = os.path.join(run_dir, "vm-events.log")
    if not os.path.exists(path):
        return None
    user_ws = str(ready["user"]["workspace"])
    user_window = (ready["user"]["window"] or "").removeprefix("0x")
    events = []
    with open(path) as f:
        for line in f:
            t, _, event = line.rstrip("\n").partition(" ")
            name, _, data = event.partition(">>")
            events.append((float(t) + offset, name, data))
    opened, workspaces = [], []
    focus, away_since, away = user_window, None, 0.0
    for t, name, data in events:
        if t < start or t > end:
            if name == "activewindowv2" and t < start:
                focus = data.removeprefix("0x")
            continue
        if name == "openwindow":
            address, ws, cls, title = (data.split(",", 3) + ["", "", ""])[:4]
            if ws == user_ws:
                opened.append({"at": round(t - start, 1), "class": cls, "title": title})
        elif name == "workspace":
            workspaces.append({"at": round(t - start, 1), "workspace": data})
        elif name == "activewindowv2":
            focus = data.removeprefix("0x")
            if focus != user_window and away_since is None:
                away_since = t
            elif focus == user_window and away_since is not None:
                away += t - away_since
                away_since = None
    if away_since is not None:
        away += end - away_since
    return {
        "undisturbed": not opened and not workspaces and away == 0,
        "windows_opened_on_user_workspace": opened,
        "focus_away_seconds": round(away, 1),
        "workspace_switches": workspaces,
    }


def model_gaps(calls, start, end):
    """The time outside tool calls between the agent's start and end, split at each call: how long the model
    and the harness took before each call, and after the last one."""
    gaps, cursor = [], start
    for c in sorted((c for c in calls if c.get("end")), key=lambda c: c["start"]):
        gaps.append(max(0.0, c["start"] - cursor))
        cursor = max(cursor, c["end"])
    gaps.append(max(0.0, end - cursor))
    return gaps


def metrics(run_dir):
    """The summary of one finished run directory."""
    with open(os.path.join(run_dir, "run.json")) as f:
        run = json.load(f)
    ready = json.load(open(os.path.join(run_dir, "ready.json")))
    vm = json.load(open(os.path.join(run_dir, "vm-result.json"))) if os.path.exists(os.path.join(run_dir, "vm-result.json")) else {}
    tap = load_jsonl(os.path.join(run_dir, "mcp.jsonl"))
    stream = load_jsonl(os.path.join(run_dir, "agent.jsonl"))
    calls = tool_calls(tap)
    start, end = run["agent_start"], run["agent_end"]
    wall = end - start
    finished = [c for c in calls if c.get("end")]
    # A call still unanswered when the agent ended, as in a timeout, ran until then: its time is the tools'.
    timed = [{**c, "end": c["end"] if c.get("end") else max(c["start"], end)} for c in calls]
    tool_seconds = union_seconds([(c["start"], c["end"]) for c in timed])
    offset = ready["host_time"] - ready["vm_time"]
    events = (vm.get("task") or {}).get("events") or {}

    # When the task's milestones happened, from the VM's logs, in seconds after the agent's start.
    phases = {
        PHASE_NAMES.get(name, name): round(t + offset - start, 1)
        for name, t in events.items()
        if isinstance(t, (int, float))
    }

    by_tool = {}
    for c in finished:
        b = by_tool.setdefault(c["tool"], {"calls": 0, "seconds": 0.0})
        b["calls"] += 1
        b["seconds"] = round(b["seconds"] + c["seconds"], 2)
    gaps = model_gaps(timed, start, end)
    check = (vm.get("task") or {}).get("check") or {}
    return {
        "harness": run["harness"],
        "model": run["model"],
        "rep": run["rep"],
        "passed": bool(check.get("passed")),
        "failures": check.get("failures", ["no grading: the VM gave no result"] if not vm else []),
        "agent_exit": run.get("agent_exit"),
        "timed_out": run.get("timed_out", False),
        "wall_seconds": round(wall, 1),
        "tool_seconds": round(tool_seconds, 1),
        "outside_tool_seconds": round(wall - tool_seconds, 1),
        # The earliest request: responses to concurrent calls can come in another order.
        "first_call_seconds": round(min(c["start"] for c in calls) - start, 1) if calls else None,
        "longest_gap_seconds": round(max(gaps), 1) if gaps else None,
        # The model's and harness's time per turn; it varies with the API's load more than anything else.
        "median_gap_seconds": round(statistics.median(gaps), 1) if gaps else None,
        "calls": len(calls),
        "by_tool": by_tool,
        "actions": sum(len(c["actions"] or []) for c in finished if c.get("actions")),
        "errors": sum(1 for c in finished if c.get("error")),
        "no_change": sum(1 for c in finished if c.get("no_change")),
        "images": sum(c.get("images", 0) for c in finished),
        "image_kib": round(sum(c.get("image_bytes", 0) for c in finished) / 1024),
        "task": run.get("task", "thunderbird"),
        "phases": phases,
        "checks": check.get("checks", {}),
        "user": user_disturbance(run_dir, ready, start, end, offset),
        "usage": agent_usage(run["harness"], stream),
        "harness_version": run.get("harness_version"),
        "machine": run.get("machine"),
    }


def debrief_report(summary, calls):
    """What the agent is told about its run before the debrief questions."""
    s = summary
    lines = [
        f"- Result: {'passed' if s['passed'] else 'failed'}"
        + (f" ({'; '.join(s['failures'])})" if s["failures"] else "") + ".",
        f"- Time: {s['wall_seconds']} s in total, {s['tool_seconds']} s inside hyprmasq tool calls, "
        f"{s['outside_tool_seconds']} s outside them (your thinking and output, and the harness).",
        f"- Calls: {s['calls']} ("
        + ", ".join(f"{k} {v['calls']}" for k, v in sorted(s["by_tool"].items())) + f"), {s['actions']} actions, "
        f"{s['images']} images, {s['errors']} errors, {s['no_change']} results with no visible change.",
        "- Milestones: " + (", ".join(f"{k} at {v} s" for k, v in s["phases"].items()) or "none reached") + ".",
    ]
    user = s.get("user")
    if user:
        opened = "; ".join(f"{w['class']} \"{w['title']}\" at {w['at']} s" for w in user["windows_opened_on_user_workspace"])
        lines.append(
            "- The user's visible workspace, where they were working: "
            + (f"windows opened on it: {opened}" if opened else "no window opened on it")
            + f"; their keyboard focus was elsewhere for {user['focus_away_seconds']} s"
            + (f"; the visible workspace switched {len(user['workspace_switches'])} times" if user["workspace_switches"] else "")
            + "."
        )
    slow = sorted((c for c in calls if c.get("seconds")), key=lambda c: -c["seconds"])[:3]
    if slow:
        lines.append("- Slowest calls: " + "; ".join(f"{c['tool']} {c['seconds']:.1f} s ({c['summary']})" for c in slow) + ".")
    return "\n".join(lines)


def fmt(values, digits=0):
    values = [v for v in values if v is not None]
    if not values:
        return "–"
    med = statistics.median(values)
    text = f"{med:.{digits}f}"
    if len(values) > 1:
        text += f" ({min(values):.{digits}f}–{max(values):.{digits}f})"
    return text


def host(run):
    return (run.get("machine") or {}).get("host") or "an unrecorded machine"


def table(runs, phase_names, group):
    """The table of the runs' medians, a row per group."""
    groups = {}
    for r in runs:
        groups.setdefault(group(r), []).append(r)
    columns = ["Agent", "Passed", "Wall s", "In tools s", "Outside tools s", "Turn s", "Calls", "Actions",
               "Images"] + [f"{name.capitalize()} at s" for name in phase_names] + ["User undisturbed"]
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for name, rs in groups.items():
        passed = [r for r in rs if r["passed"]]
        out.append(
            f"| {name} | {len(passed)}/{len(rs)} | {fmt([r['wall_seconds'] for r in rs])} | "
            f"{fmt([r['tool_seconds'] for r in rs])} | {fmt([r['outside_tool_seconds'] for r in rs])} | "
            f"{fmt([r.get('median_gap_seconds') for r in rs], 1)} | "
            f"{fmt([r['calls'] for r in rs])} | {fmt([r['actions'] for r in rs])} | {fmt([r['images'] for r in rs])} | "
            + "".join(f"{fmt([r.get('phases', {}).get(name) for r in rs])} | " for name in phase_names)
            + f"{sum(1 for r in rs if (r.get('user') or {}).get('undisturbed'))}/{len(rs)} |"
        )
    return out


def report(results_dir):
    runs = []
    for name in sorted(os.listdir(results_dir)):
        path = os.path.join(results_dir, name, "summary.json")
        if os.path.exists(path):
            # Computed again from the run's raw files, so that the report follows changes to the measures.
            summary = metrics(os.path.join(results_dir, name))
            with open(path, "w") as f:
                json.dump(summary, f, indent=2)
            runs.append(summary)
    with open(os.path.join(results_dir, "runs.json"), "w") as f:
        json.dump(runs, f, indent=2)

    phase_names = list(dict.fromkeys(name for r in runs for name in r.get("phases", {})))
    out = table(runs, phase_names, lambda r: f"{r['harness']} {r['model']}")
    out += [
        "",
        "Medians, with the range over the runs in brackets. Turn s is the median time between tool calls: the "
        "model's and the harness's time per step. The milestones are the task's, in seconds after the agent's start.",
        "",
    ]
    if len({host(r) for r in runs}) > 1:
        out += [
            "## By machine",
            "",
            "Wall times and the time in tools depend on the machine: compare builds on the same machines, or here.",
            "",
            *table(runs, phase_names, lambda r: f"{r['harness']} {r['model']} on {host(r)}"),
            "",
        ]
    out += ["## Runs", ""]
    for r in runs:
        machine = r.get("machine") or {}
        out.append(
            f"- {r['harness']} {r['model']} #{r['rep']}: {'passed' if r['passed'] else 'FAILED'} in {r['wall_seconds']} s, "
            f"{r['calls']} calls"
            + (f", on {machine.get('host')} ({machine.get('cpu')})" if machine else "")
            + (f", {r['harness_version']}" if r.get("harness_version") else "")
            + (f" — {'; '.join(r['failures'])}" if r["failures"] else "")
        )
    with open(os.path.join(results_dir, "report.md"), "w") as f:
        f.write("# Benchmark report\n\n" + "\n".join(out) + "\n")
    return "\n".join(out)


if __name__ == "__main__":
    print(report(sys.argv[1]))
