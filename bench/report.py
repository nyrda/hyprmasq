#!/usr/bin/env python3
"""Metrics of benchmark runs, and the report of a results directory.

    report.py RESULTS-DIR

computes summary.json of every finished run in it again from the run's raw files, and writes
RESULTS-DIR/report.md and RESULTS-DIR/runs.json. run.py calls metrics() for each run when it finishes.
"""

import glob
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


def named_actions(arguments):
    """Read both spellings of act's actions, including its final next_page."""
    fields = {
        "click": ("x", "y", "button", "count", "modifiers"), "move": ("x", "y"),
        "drag": ("from", "to", "button", "modifiers"), "path": ("points", "curve", "button", "modifiers"),
        "scroll": ("x", "y", "dy", "unit", "dx"), "type": ("text",),
        "key": ("keys", "repeat"), "pause": ("ms",),
    }
    out = []
    for action in arguments.get("actions") or []:
        if isinstance(action, list) and len(action) == 2 and isinstance(action[0], (int, float)) and not isinstance(action[0], bool):
            action = dict(action="click", x=action[0], y=action[1])
        elif isinstance(action, list) and action and isinstance(action[0], str):
            action = dict(zip(fields.get(action[0], ()), action[1:]), action=action[0])
            if action["action"] == "key" and isinstance(action.get("keys"), str):
                action["keys"] = [action["keys"]]
        if isinstance(action, dict):
            out.append(action)
    if isinstance(arguments.get("next_page"), dict):
        next_page = arguments["next_page"]
        out.append(dict(action="scroll", x=next_page.get("x"), y=next_page.get("y"),
                        dy=-1 if next_page.get("direction") == "up" else 1, unit="page"))
    return out


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
                "actions": [a.get("action") for a in named_actions(args)],
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


def distinct(values):
    return list(dict.fromkeys(v for v in values if v))


def codex_efforts(tap):
    """The reasoning efforts Codex names in the metadata of its tool calls in the MCP log, in order."""
    return distinct(
        ((((entry.get("msg") or {}).get("params") or {}).get("_meta") or {}).get("x-codex-turn-metadata") or {})
        .get("reasoning_effort")
        for entry in tap if entry.get("dir") == "request" and (entry.get("msg") or {}).get("method") == "tools/call"
    )


def claude_efforts(transcript):
    """The efforts of the requests that Claude Code's transcript logs, in order."""
    return distinct(
        ((e.get("params") or {}).get("output_config") or {}).get("effort")
        for e in transcript if e.get("type") == "api-request"
    )


# Where each harness says the effort it ran at.
REPORTED_BY = {
    "codex": "x-codex-turn-metadata of the tool calls in mcp.jsonl",
    "claude": "output_config of the requests in Claude Code's transcript",
    "pi": "thinking_level_change in pi's session",
}


def run_effort(run, run_dir, tap):
    """The effort a run asked for, None for the harness's default, the one the harness said it ran at, and where it
    said so. For runs from before run.json recorded it: pi's thinking level, and what Codex's MCP log or Claude Code's
    transcript says."""
    if "effort" in run:
        return {k: run["effort"].get(k) for k in ("requested", "ran_at", "reported_by")}
    family = "claude" if run["harness"] == "claude-api" else run["harness"]
    ran_at = run.get("thinking")
    if family == "codex":
        ran_at = "/".join(codex_efforts(tap)) or None
    elif family == "claude" and run.get("session"):
        transcript = load_jsonl(os.path.join(run_dir, f"transcript-{run['session']}.jsonl"))
        ran_at = "/".join(claude_efforts(transcript)) or None
    return {"requested": None, "ran_at": ran_at, "reported_by": REPORTED_BY.get(family) if ran_at else None}


def effort_text(efforts):
    """An effort for the report: the level asked for, or default, and what the harness ran at where that differs."""
    asked = distinct(e.get("requested") for e in efforts) or ["default"]
    ran = distinct(e.get("ran_at") for e in efforts)
    text = "/".join(asked)
    return text + (f", ran {'/'.join(ran)}" if ran and ran != asked else "")


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


def pi_text(message):
    return "".join(c.get("text", "") for c in message.get("content") or [] if c.get("type") == "text") or None


# OpenAI's API list prices in USD per million tokens, from https://developers.openai.com/api/docs/pricing on
# 2026-10-26: input, cached input, cache writes and output, and the prices for a request whose prompt is over
# LONG_PROMPT tokens. Codex runs on a ChatGPT login report no cost, so a run's cost is what its requests would cost
# on the API.
OPENAI_PRICES = {
    "gpt-6-astra": ((10.00, 1.00, 12.50, 50.00), (20.00, 2.00, 25.00, 75.00)),
    "gpt-6.1-sol": ((2.00, 0.10, 2.50, 10.00), (4.00, 0.20, 5.00, 15.00)),
    "gpt-6.1-terra": ((1.00, 0.05, 1.25, 5.00), (2.00, 0.10, 2.50, 7.50)),
    "gpt-6-sol": ((2.00, 0.20, 2.50, 10.00), (4.00, 0.40, 5.00, 15.00)),
    "gpt-6-luna": ((0.10, 0.01, 0.125, 0.50), (0.20, 0.02, 0.25, 0.75)),
}
LONG_PROMPT = 272_000


# Codex's counters of a request's usage, and the names agent_usage() gives them.
CODEX_COUNTERS = {"input_tokens": "input_tokens", "cached_input_tokens": "cache_read_tokens",
                  "cache_write_input_tokens": "cache_write_tokens", "output_tokens": "output_tokens"}


def codex_request_entries(transcript, total):
    """The index in Codex's transcript and the usage of each request of the run: the requests until their sum reaches
    the run's total, since the debrief resumes the session and adds its own requests to the same transcript. None
    when they do not add up to it."""
    requests, seen = [], None
    summed = lambda key: sum(u.get(key, 0) for _, u in requests)
    for index, entry in enumerate(transcript):
        payload = entry.get("payload") or {}
        info = payload.get("info") or {}
        if payload.get("type") != "token_count" or not info:
            continue
        if requests and all(summed(key) == total.get(key) for key in total):
            break
        # Codex sends the totals again without a new request, when the rate limits change and after a compaction.
        if info.get("total_token_usage") == seen:
            continue
        seen = info.get("total_token_usage")
        requests.append((index, info.get("last_token_usage") or {}))
    if not requests or any(summed(key) != total.get(key) for key in total):
        return None
    return requests


def codex_requests(transcript, total):
    """The usage of each request of the run in Codex's transcript, as codex_request_entries() finds them."""
    requests = codex_request_entries(transcript, total)
    return [u for _, u in requests] if requests else None


def request_context(source, sizes, compactions):
    """How large the agent's context got: the prompt tokens of each of the run's requests, cache reads and writes
    included, in order, and the compactions of its history. None without the size of every request."""
    if not sizes or None in sizes:
        return None
    return {"source": source, "request_count": len(sizes), "first_input_tokens": sizes[0],
            "peak_input_tokens": max(sizes), "last_input_tokens": sizes[-1],
            "growth_input_tokens": sizes[-1] - sizes[0], "compactions": compactions}


def codex_context(usage, transcript):
    """The context of a Codex run: its requests in the transcript, until they add up to the run's totals, and the
    compactions it recorded up to the last of them, since the debrief's requests and compactions follow in the same
    transcript. The compactions are unknown when Codex announced more of them than it recorded."""
    if not usage or usage.get("input_tokens") is None:
        return None
    requests = codex_request_entries(transcript, {k: usage.get(ours) or 0 for k, ours in CODEX_COUNTERS.items()})
    if not requests:
        return None
    task = transcript[:requests[-1][0] + 1]
    recorded = sum(e.get("type") == "compacted" for e in task)
    announced = sum((e.get("payload") or {}).get("type") == "context_compacted" for e in task)
    return request_context("Codex transcript token_count records", [u.get("input_tokens") for _, u in requests],
                           recorded if announced <= recorded else None)


def claude_context(stream):
    """The context of a Claude Code run: the usage of each assistant message in its stream, once a message, since
    each of its content blocks comes as an event of its own, and its compactions. The debrief writes a stream of its
    own."""
    sizes, seen, compactions = [], set(), 0
    for e in stream:
        line = e["line"]
        if line.get("type") == "system" and line.get("subtype") == "compact_boundary":
            compactions += 1
        message = line.get("message") or {}
        if line.get("type") != "assistant" or message.get("id") in seen or not message.get("usage"):
            continue
        seen.add(message.get("id"))
        u = message["usage"]
        sizes.append(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                     + u.get("cache_creation_input_tokens", 0))
    return request_context("Claude Code stream-json assistant usage", sizes, compactions)


def pi_context(stream):
    """The context of a pi run: the usage of each assistant message's request in its stream, and its compactions,
    whose summary requests are not the agent's. A request that failed before its prompt counted is left out. The
    debrief writes a stream of its own."""
    sizes, compactions = [], 0
    for e in stream:
        line = e["line"]
        message = line.get("message") or {}
        if line.get("type") == "compaction_end" and not line.get("aborted"):
            compactions += 1
        elif line.get("type") == "message_end" and message.get("role") == "assistant" and message.get("usage"):
            u = message["usage"]
            size = u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0)
            if size:
                sizes.append(size)
    return request_context("pi stream assistant message_end usage", sizes, compactions)


def context_usage(harness, stream, transcript, usage):
    """How large the agent's context got in a run, from the harness's own logs, the debrief's requests left out.
    None where they do not give the size of every request."""
    if harness in ("claude", "claude-api"):
        return claude_context(stream)
    if harness == "pi":
        return pi_context(stream)
    return codex_context(usage, transcript)


def api_cost(model, requests):
    """What the requests would cost at OPENAI_PRICES, each at the prices for its prompt's length."""
    prices = OPENAI_PRICES.get(model)
    if prices is None or requests is None:
        return None
    cost = 0.0
    for u in requests:
        prompt, cached, written = u.get("input_tokens", 0), u.get("cached_input_tokens", 0), u.get("cache_write_input_tokens", 0)
        p = prices[1 if prompt > LONG_PROMPT else 0]
        cost += ((prompt - cached - written) * p[0] + cached * p[1] + written * p[2] + u.get("output_tokens", 0) * p[3]) / 1e6
    return round(cost, 6)


def agent_usage(harness, stream, transcript=()):
    """Tokens and turns from the agent's own event stream, and its final answer. input_tokens counts every prompt
    token, cache reads and writes included. cost_usd is the harness's own figure, except for Codex, whose transcript
    gives the requests to price at the API's list prices."""
    usage = {}
    if harness in ("claude", "claude-api"):
        for e in stream:
            line = e["line"]
            if line.get("type") == "result":
                u = line.get("usage") or {}
                usage = {
                    "turns": line.get("num_turns"),
                    "input_tokens": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0),
                    "cache_read_tokens": u.get("cache_read_input_tokens", 0),
                    "cache_write_tokens": u.get("cache_creation_input_tokens", 0),
                    "output_tokens": u.get("output_tokens"),
                    "thinking_tokens": (u.get("output_tokens_details") or {}).get("thinking_tokens"),
                    "api_seconds": (line.get("duration_api_ms") or 0) / 1000,
                    "cost_usd": line.get("total_cost_usd"),
                    "final": line.get("result"),
                }
    elif harness == "pi":
        # Each assistant message's message_end carries the usage of its request; a turn is one of them.
        replies = [e["line"]["message"] for e in stream
                   if e["line"].get("type") == "message_end" and (e["line"].get("message") or {}).get("role") == "assistant"]
        # Compaction summarizes the session with requests of its own, outside the turns.
        compactions = [(e["line"].get("result") or {}).get("usage") or {} for e in stream
                       if e["line"].get("type") == "compaction_end"]
        usages = [m.get("usage") or {} for m in replies] + compactions
        usage = {
            "turns": len(replies),
            "input_tokens": sum(u.get("input", 0) + u.get("cacheRead", 0) + u.get("cacheWrite", 0) for u in usages),
            "cache_read_tokens": sum(u.get("cacheRead", 0) for u in usages),
            "cache_write_tokens": sum(u.get("cacheWrite", 0) for u in usages),
            "output_tokens": sum(u.get("output", 0) for u in usages),
            "thinking_tokens": sum(u.get("reasoning", 0) for u in usages),
            "cost_usd": round(sum((u.get("cost") or {}).get("total", 0) for u in usages), 6),
            "final": next((pi_text(m) for m in reversed(replies) if pi_text(m)), None),
        }
        errors = [m.get("errorMessage") for m in replies if m.get("stopReason") == "error"]
        if errors:
            usage["error"] = errors[-1]
    else:
        final, model = None, None
        for e in stream:
            line = e["line"]
            if line.get("type") == "turn.completed":
                u = line.get("usage") or {}
                usage = {
                    "input_tokens": u.get("input_tokens"),
                    "cache_read_tokens": u.get("cached_input_tokens"),
                    "cache_write_tokens": u.get("cache_write_input_tokens"),
                    "output_tokens": u.get("output_tokens"),
                    "thinking_tokens": u.get("reasoning_output_tokens"),
                }
            item = line.get("item") or {}
            if line.get("type") == "item.completed" and item.get("type") == "agent_message":
                final = item.get("text")
        for entry in transcript:
            payload = entry.get("payload") or {}
            if entry.get("type") == "turn_context" and payload.get("model"):
                model = payload["model"]
                break
        if usage and model:
            requests = codex_requests(transcript, {k: usage[ours] or 0 for k, ours in CODEX_COUNTERS.items()})
            usage["cost_usd"] = api_cost(model, requests)
            usage["cost_basis"] = f"API list prices for {model}"
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
    rollouts = sorted(glob.glob(os.path.join(run_dir, "transcript-rollout-*.jsonl")))
    transcript = load_jsonl(rollouts[0]) if rollouts else []
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
    usage = agent_usage(run["harness"], stream, transcript)
    return {
        "harness": run["harness"],
        "model": run["model"],
        "effort": run_effort(run, run_dir, tap),
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
        "task": run["task"],
        "phases": phases,
        "checks": check.get("checks", {}),
        "user": user_disturbance(run_dir, ready, start, end, offset),
        "usage": usage,
        "context": context_usage(run["harness"], stream, transcript, usage),
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


def thousands(run, key):
    tokens = (run.get("usage") or {}).get(key)
    return tokens / 1000 if tokens is not None else None


def cached_percent(run):
    usage = run.get("usage") or {}
    if not usage.get("input_tokens") or usage.get("cache_read_tokens") is None:
        return None
    return 100 * usage["cache_read_tokens"] / usage["input_tokens"]


def table(runs, phase_names, group):
    """The table of the runs' medians, a row per group and effort asked for: runs at different efforts never share
    one."""
    groups = {}
    for r in runs:
        groups.setdefault((group(r), (r.get("effort") or {}).get("requested")), []).append(r)
    columns = ["Agent", "Effort", "Passed", "Wall s", "In tools s", "Outside tools s", "Turn s", "Calls", "Actions",
               "Images", "Input k", "Cached %", "Output k", "Cost $"] \
        + [f"{name.capitalize()} at s" for name in phase_names] + ["User undisturbed"]
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for (name, _), rs in groups.items():
        passed = [r for r in rs if r["passed"]]
        out.append(
            f"| {name} | {effort_text([r.get('effort') or {} for r in rs])} | {len(passed)}/{len(rs)} | "
            f"{fmt([r['wall_seconds'] for r in rs])} | "
            f"{fmt([r['tool_seconds'] for r in rs])} | {fmt([r['outside_tool_seconds'] for r in rs])} | "
            f"{fmt([r.get('median_gap_seconds') for r in rs], 1)} | "
            f"{fmt([r['calls'] for r in rs])} | {fmt([r['actions'] for r in rs])} | {fmt([r['images'] for r in rs])} | "
            f"{fmt([thousands(r, 'input_tokens') for r in rs])} | {fmt([cached_percent(r) for r in rs])} | "
            f"{fmt([thousands(r, 'output_tokens') for r in rs])} | "
            f"{fmt([(r.get('usage') or {}).get('cost_usd') for r in rs], 3)} | "
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
        "model's and the harness's time per step. Input k counts the prompt tokens of all the run's requests in "
        "thousands, Cached % the share of them read from the provider's cache, and Output k the output tokens, "
        "thinking included. Cost $ is the cost the harness reports, and for Codex, what its requests would cost at "
        "OpenAI's API list prices. The milestones are the task's, in seconds after the agent's start.",
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
            f"- {r['harness']} {r['model']} #{r['rep']}, effort {effort_text([r.get('effort') or {}])}: "
            f"{'passed' if r['passed'] else 'FAILED'} in {r['wall_seconds']} s, "
            f"{r['calls']} calls"
            + (f", on {machine.get('host')} ({machine.get('cpu')})" if machine else "")
            + (f", {r['harness_version']}" if r.get("harness_version") else "")
            + (f" — {'; '.join(r['failures'])}" if r["failures"] else "")
        )
    with open(os.path.join(results_dir, "report.md"), "w") as f:
        f.write("# Benchmark report\n\n" + "\n".join(out) + "\n")
    return "\n".join(out)


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        print(report(sys.argv[1]))
