#!/usr/bin/env python3
"""Summarizes a set of laptop runs: analyze.py RESULTS-DIR, with RESULTS-DIR copied from the machine. Writes
RESULTS-DIR/comparison.json, one entry a run, as docs/comparison/data holds them.

Usage, cost and context come from bench/report.py's parsers, as for the VM runs. Tool calls and their times
come from the agent's event stream, timed as each line arrived. Focus changes come from Hyprland's events: every
change of the active window away from the user's terminal, with the tool calls running at that moment.
"""

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import report  # noqa: E402


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def tool_calls(stream):
    """MCP tool calls from Codex's stream: name, arguments, start, end, error, images."""
    calls = {}
    for entry in stream:
        event, t = entry["event"], entry["time"]
        item = event.get("item") or {}
        if item.get("type") != "mcp_tool_call":
            continue
        call = calls.setdefault(item["id"], {"tool": item.get("tool"), "arguments": item.get("arguments"),
                                             "start": t, "end": None})
        if event.get("type") == "item.completed":
            call["end"] = t
            result = item.get("result") or {}
            content = result.get("content") or []
            call["images"] = sum(c.get("type") == "image" for c in content)
            call["error"] = bool(item.get("error")) or bool(result.get("is_error") or result.get("isError")) \
                or item.get("status") == "failed"
            text = " ".join(c.get("text", "") for c in content if c.get("type") == "text")
            call["text"] = text[:300]
    return sorted(calls.values(), key=lambda c: c["start"])


def claude_tool_calls(stream):
    """MCP tool calls from Claude Code's stream: tool_use in assistant messages, tool_result in user messages."""
    calls = {}
    for entry in stream:
        event, t = entry["event"], entry["time"]
        message = event.get("message") or {}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if event.get("type") == "assistant" and block.get("type") == "tool_use":
                name = block.get("name", "")
                calls[block["id"]] = {"tool": name.split("__")[-1], "full_name": name, "arguments": block.get("input"),
                                      "start": t, "end": None}
            elif event.get("type") == "user" and block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                call = calls[block["tool_use_id"]]
                call["end"] = t
                content = block.get("content") if isinstance(block.get("content"), list) else []
                call["images"] = sum(c.get("type") == "image" for c in content if isinstance(c, dict))
                call["error"] = bool(block.get("is_error"))
                call["text"] = " ".join(c.get("text", "") for c in content if isinstance(c, dict)
                                        and c.get("type") == "text")[:300] or str(block.get("content"))[:300]
    return sorted((c for c in calls.values() if c["full_name"].startswith("mcp__")), key=lambda c: c["start"])


def focus_changes(events_path, terminal, form, start, end, calls):
    """Changes of the active window and workspace while the agent ran, each with the calls running then."""
    terminal, form = terminal.removeprefix("0x"), form.removeprefix("0x")
    changes, away_since, away = [], None, 0.0
    with open(events_path) as f:
        for line in f:
            t, _, rest = line.rstrip("\n").partition(" ")
            t = float(t)
            if not (start <= t <= end):
                continue
            name, _, data = rest.partition(">>")
            if name not in ("activewindowv2", "workspacev2"):
                continue
            target = data.split(",")[0] if name == "activewindowv2" else data
            if name == "activewindowv2":
                if target != terminal and away_since is None:
                    away_since = t
                elif target == terminal and away_since is not None:
                    away += t - away_since
                    away_since = None
            running = [f'{c["tool"]}{" " + json.dumps(c["arguments"].get("delivery_mode")) if isinstance(c["arguments"], dict) and c["arguments"].get("delivery_mode") else ""}'
                       for c in calls if c["start"] - 0.5 <= t <= (c["end"] or end) + 0.5]
            changes.append({"at": round(t - start, 2), "event": name,
                            "to": "task window" if target == form else "user terminal" if target == terminal
                            else target, "during": running})
    if away_since is not None:
        away += end - away_since
    return changes, round(away, 1)


def desktop_calls(transcript, start):
    """Tool calls from a Codex session on the Mac, timed by the session's own timestamps. Code mode, which the
    ChatGPT app's Codex turns on, gives the model an MCP server's tools through a JavaScript tool, exec, whose calls
    the session records as custom tool calls. OpenAI's computer use opts out of it and is called directly."""
    calls = {}
    for entry in transcript:
        payload = entry.get("payload") or {}
        if payload.get("type") in ("function_call", "custom_tool_call"):
            arguments = (json.loads(payload.get("arguments") or "{}") if payload["type"] == "function_call"
                         else {"input": payload.get("input")})
            calls[payload["call_id"]] = {"tool": payload.get("name"), "arguments": arguments,
                                         "start": iso_time(entry["timestamp"]), "end": None}
        elif (payload.get("type") in ("function_call_output", "custom_tool_call_output")
              and payload.get("call_id") in calls):
            call = calls[payload["call_id"]]
            call["end"] = iso_time(entry["timestamp"])
            output = json.dumps(payload.get("output"))
            call["images"] = output.count("input_image")
            text = re.sub(r"data:image/[^\"]+", "", output)
            call["error"] = bool(re.search(r"server error|Accessibility error|Error:", text))
            call["text"] = text[:300]
    return sorted(calls.values(), key=lambda c: c["start"])


def iso_time(stamp):
    return datetime.strptime(stamp.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()


def mac_disturbance(run_dir, user_app, start, end):
    """The focus away from the user, and task windows drawn over the user's window.

    Old observations lack over_user and record only positive-layer windows reliably.
    """
    changes, away, since, obscured = [], 0.0, None, []
    with open(os.path.join(run_dir, "front.jsonl")) as f:
        fronts = [json.loads(line) for line in f if line.strip()]
    for row in fronts:
        if not start <= row["time"] <= end:
            continue
        changes.append({"at": round(row["time"] - start, 2), "to": row["front"]})
        if row["front"] != user_app and since is None:
            since = row["time"]
        elif row["front"] == user_app and since is not None:
            away += row["time"] - since
            obscured.append((since, row["time"]))
            since = None
    if since is not None:
        away += end - since
        obscured.append((since, end))
    with open(os.path.join(run_dir, "windows.jsonl")) as f:
        rows = [json.loads(line) for line in f if line.strip()]
    shown, visible, previous = 0, 0.0, 0
    for row, following in zip(rows, rows[1:] + [{"time": end, "windows": []}]):
        count = sum(1 for w in row["windows"] if w.get("over_user", False) or w["layer"] > 0)
        if count and row["time"] < end:
            interval = (max(row["time"], start), min(following["time"], end))
            if interval[1] > interval[0]:
                visible += interval[1] - interval[0]
                obscured.append(interval)
        if count > previous and row["time"] >= start:
            shown += count - previous
        previous = count
    return ({"changes": len(changes), "seconds_away_from_user": round(away, 1), "events": changes,
             "seconds_disturbed": round(report.union_seconds(obscured), 1)},
            {"shown": shown, "seconds_visible": round(visible, 1)})


def mac_run_metrics(run_dir, summary):
    transcript = load_jsonl(os.path.join(run_dir, "transcript.jsonl"))
    agent = summary["agent"]
    # A run whose session the runner never found has no transcript, and its model is unknown.
    model = next((e["payload"].get("model") for e in transcript if e.get("type") == "turn_context"), "unknown")
    counts = [e["payload"]["info"] for e in transcript
              if (e.get("payload") or {}).get("type") == "token_count" and e["payload"].get("info")]
    total = counts[-1]["total_token_usage"] if counts else {}
    usage = {"input_tokens": total.get("input_tokens"), "cache_read_tokens": total.get("cached_input_tokens"),
             "cache_write_tokens": total.get("cache_write_input_tokens", 0),
             "output_tokens": total.get("output_tokens"), "thinking_tokens": total.get("reasoning_output_tokens")}
    # A session that ended before its first token count has no usage.
    usage["uncached_input_tokens"] = (usage["input_tokens"] - (usage["cache_read_tokens"] or 0)
                                      if usage["input_tokens"] is not None else None)
    requests = report.codex_requests(transcript, {"input_tokens": usage["input_tokens"],
                                                  "cached_input_tokens": usage["cache_read_tokens"],
                                                  "cache_write_input_tokens": usage["cache_write_tokens"],
                                                  "output_tokens": usage["output_tokens"]})
    usage["cost_usd"] = report.api_cost(model, requests)
    usage["cost_basis"] = f"API list prices for {model}"
    context = report.codex_context(usage, transcript)
    calls = desktop_calls(transcript, agent["start"])
    focus, popups = mac_disturbance(run_dir, summary.get("user_app", "ChatGPT"), agent["start"], agent["end"])
    final = next((p.get("content") for p in (e.get("payload") or {} for e in reversed(transcript))
                  if p.get("type") == "message" and p.get("role") == "assistant"), None)
    return {
        "harness": summary["harness"], "model": model, "task": summary["task"], "toolkit": summary.get("toolkit"),
        "platform": "macos", "backend": summary["backend"], "rep": summary["rep"], "passed": summary["passed"],
        "passed_without_hyprland_dialogs": summary["passed"], "hyprland_dialogs": [],
        "correct": (summary["check"].get("checks") or {}).get("correct"),
        "failures": summary["failures"], "timed_out": agent["timed_out"], "wall_seconds": summary["wall_seconds"],
        "tool_seconds": round(report.union_seconds([(c["start"], c["end"] or agent["end"]) for c in calls]), 1),
        "calls": len(calls), "errors": sum(bool(c.get("error")) for c in calls),
        "images": sum(c.get("images", 0) for c in calls),
        "by_tool": {"js": {"calls": len(calls), "errors": sum(bool(c.get("error")) for c in calls)}},
        "first_prompt_tokens": context["first_input_tokens"] if context else None,
        "usage": usage, "final": json.dumps(final)[:300] if final else None, "context": context,
        "focus": focus, "popups": popups,
        "error_texts": [c.get("text", "")[:200] for c in calls if c.get("error")],
    }


def run_metrics(run_dir):
    summary = json.load(open(os.path.join(run_dir, "summary.json")))
    if summary.get("harness") in ("codex-desktop", "codex-exec"):
        return mac_run_metrics(run_dir, summary)
    before = json.load(open(os.path.join(run_dir, "before.json")))
    stream = load_jsonl(os.path.join(run_dir, "agent.jsonl"))
    transcript = load_jsonl(os.path.join(run_dir, "transcript.jsonl"))
    agent = summary["agent"]
    harness = summary.get("harness", "codex")
    lines = [{"line": e["event"]} for e in stream]
    usage = report.agent_usage("claude-api" if harness == "claude" else "codex", lines, transcript)
    # A harness that ended before its usage event reports none: the run's usage is unknown.
    for key in ("input_tokens", "cache_read_tokens", "output_tokens", "cost_usd"):
        usage.setdefault(key, None)
    usage["uncached_input_tokens"] = (usage["input_tokens"] - (usage["cache_read_tokens"] or 0)
                                      if usage["input_tokens"] is not None else None)
    context = report.context_usage(harness, lines, transcript, usage)
    calls = claude_tool_calls(stream) if harness == "claude" else tool_calls(stream)
    by_tool = {}
    for c in calls:
        b = by_tool.setdefault(c["tool"], {"calls": 0, "seconds": 0.0, "errors": 0})
        b["calls"] += 1
        b["seconds"] = round(b["seconds"] + ((c["end"] or agent["end"]) - c["start"]), 2)
        b["errors"] += bool(c.get("error"))
    changes, away = focus_changes(os.path.join(run_dir, "hyprland-events.log"), before["terminal"],
                                  before["form"], agent["start"], agent["end"], calls)
    first_prompt = context["first_input_tokens"] if harness == "claude" and context else None
    for entry in transcript if harness == "codex" else []:
        info = (entry.get("payload") or {}).get("info") or {}
        if (entry.get("payload") or {}).get("type") == "token_count" and info.get("last_token_usage"):
            first_prompt = info["last_token_usage"].get("input_tokens")
            break
    # Hyprland's ANR dialogs are the compositor's, not windows the agent opened: a second verdict leaves them out.
    # The form's checker and run_inkscape.py name the window, its class among the rest, after "opened: ".
    other = [f for f in summary["failures"]
             if not (f.startswith("a window other than ") and "opened: " in f
                     and "hyprland-dialog" in f.split("opened: ", 1)[1])]
    dialogs = []
    with open(os.path.join(run_dir, "hyprland-events.log")) as f:
        for line in f:
            t, _, rest = line.rstrip("\n").partition(" ")
            at = round(float(t) - agent["start"], 2)
            if not agent["start"] <= float(t) <= agent["end"]:
                continue
            if rest.startswith("openwindow>>") and "hyprland-dialog" in rest:
                dialogs.append({"at": at, "commands": None})
            elif rest.startswith("dialog>>") and dialogs and at - dialogs[-1]["at"] < 1:
                dialogs[-1]["commands"] = json.loads(rest[8:])
    return {
        "harness": harness, "model": summary.get("model"), "task": summary.get("task"), "platform": "hyprland",
        "toolkit": summary.get("toolkit", "gtk" if summary.get("task", "").startswith("form") else None),
        "backend": summary["backend"], "rep": summary["rep"], "passed": summary["passed"],
        "passed_without_hyprland_dialogs": not other, "hyprland_dialogs": dialogs,
        "correct": (summary["check"].get("checks") or {}).get("correct"),
        "failures": summary["failures"], "timed_out": agent["timed_out"],
        "wall_seconds": summary["wall_seconds"],
        "tool_seconds": round(report.union_seconds([(c["start"], c["end"] or agent["end"]) for c in calls]), 1),
        "calls": len(calls), "errors": sum(bool(c.get("error")) for c in calls),
        "images": sum(c.get("images", 0) for c in calls), "by_tool": by_tool,
        "first_prompt_tokens": first_prompt, "usage": {k: v for k, v in usage.items() if k != "final"},
        "final": usage.get("final"), "context": context,
        "focus": {"changes": len(changes), "seconds_away_from_user": away, "events": changes},
        "error_texts": [f'{c["tool"]}: {c.get("text", "")[:200]}' for c in calls if c.get("error")],
    }


def main():
    root = sys.argv[1]
    runs = [run_metrics(os.path.join(root, d)) for d in sorted(os.listdir(root))
            if os.path.exists(os.path.join(root, d, "summary.json"))]
    with open(os.path.join(root, "comparison.json"), "w") as f:
        json.dump(runs, f, indent=2)
    for r in runs:
        u, c = r["usage"], r["context"] or {}
        print(f'{r["harness"]:6} {r["task"]:8} {r["backend"]:9} rep {r["rep"]}  passed={r["passed"]}/{r["passed_without_hyprland_dialogs"]} correct={r["correct"]} dialogs={len(r["hyprland_dialogs"])}  {r["wall_seconds"]:6.1f}s  '
              f'calls={r["calls"]:3} errors={r["errors"]:2} images={r["images"]:3}  '
              f'input={u.get("input_tokens")} uncached={u.get("uncached_input_tokens")} '
              f'out={u.get("output_tokens")} cost=${u.get("cost_usd")}  '
              f'first={r["first_prompt_tokens"]} peak={c.get("peak_input_tokens")} '
              f'focus_changes={r["focus"]["changes"]} away={r["focus"]["seconds_away_from_user"]}s')


if __name__ == "__main__":
    main()
