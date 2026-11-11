#!/usr/bin/env python3
"""Checks a benchmark VM of the Cua backend without an agent: what `cua-driver mcp` sees of the task's windows, and with
--input what its input does to them.

    probe.py CID RUN-DIR [--input] [--at X,Y] [--browser]

RUN-DIR is the directory that bench/smoke.sh booted the VM into, with its ready.json. Through mcp_tap.py, as an agent
would reach it, this lists the windows and reads each but the user's terminal with get_window_state: its
accessibility tree and its screenshot. Writes RUN-DIR/probe.json, with how many elements each tree had and the
screenshot's size, RUN-DIR/probe-N.png of the Nth window and the session's MCP log in RUN-DIR/probe-mcp.jsonl, and
prints the summary. Fails unless the task's window, found by its address, had a tree with elements.

--input sends each window a click, a key, text, a scroll and a drag, first in the background and then in the foreground,
at the middle of the window or at --at's pixel of the task's window, such as a text field, and for each records the
refusals as report.py counts them, the route the result names, and whether the window's screenshot changed: a hint that
the app got it, as a clock or a caret can change one too, which RUN-DIR/probe-N-MODE-ACTION.png lets a reader check. A
VM is disposable: these acts change the task's state, so run the probe in a VM booted for it, never with an agent.

--browser asks the browser tools for each window with get_browser_state, and on a request for setup makes the
browser_prepare call its refusal names, as Cua's guide has an agent do. It then starts Cua's isolated browser, as its
guide's Use an isolated browser does, and binds it.
"""

import argparse
import base64
import hashlib
import json
import os
import sys
import time

BENCH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BENCH)
from mcp_probe import Session  # noqa: E402
import report  # noqa: E402

MODES = ("background", "foreground")
# Long enough for an app to redraw what it was sent.
SETTLE_SECONDS = 1.0


def text_of(result):
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")


def image_of(result):
    return next((c for c in result.get("content") or [] if c.get("type") == "image"), None)


def actions(window_id, pid, width, height, at=None):
    """The acts --input sends a window of that screenshot size: at a pixel, by default in the middle of it, where a
    task's window has content. The click first, so that a field there has the key and the text, and the scroll and the
    drag last, as they can move it."""
    x, y = at or (width // 2, height // 2)
    target = {"pid": pid, "window_id": window_id}
    return [
        ("click", "click", {**target, "x": x, "y": y}),
        ("key", "press_key", {**target, "key": "k"}),
        ("text", "type_text", {**target, "text": "probe"}),
        ("scroll", "scroll", {**target, "x": x, "y": y, "direction": "down", "amount": 3}),
        ("drag", "drag", {**target, "from_x": x, "from_y": y, "to_x": x + width // 8, "to_y": y + height // 8}),
    ]


def outcome(result):
    """What an act's result says: an error, the codes report.py counts, and the driver's route and effect."""
    text = text_of(result)
    structured = result.get("structuredContent") or {}
    return {
        "error": bool(result.get("isError")),
        "refusals": report.call_refusals(text + json.dumps(structured), "cua", bool(result.get("isError"))),
        "route": structured.get("route"),
        "effect": structured.get("effect"),
        "delivery_mode": structured.get("delivery_mode"),
        "text": text[:400],
    }


def shot(session, pid, window_id):
    """The window's screenshot, its accessibility tree left out, or None."""
    state = session.call("get_window_state", {"pid": pid, "window_id": window_id})
    image = image_of(state)
    return base64.b64decode(image["data"]) if image else None


def probe_input(session, run_dir, n, window, at=None):
    """Sends a window every act in every mode, and returns the outcomes by mode and act."""
    pid, window_id = window["pid"], window["window_id"]
    first = shot(session, pid, window_id)
    state = session.call("get_window_state", {"pid": pid, "window_id": window_id}).get("structuredContent") or {}
    width, height = state.get("screenshot_width") or 800, state.get("screenshot_height") or 600
    results = {}
    before = first
    for mode in MODES:
        results[mode] = {}
        for name, tool, arguments in actions(window_id, pid, width, height, at):
            result = session.call(tool, {**arguments, "delivery_mode": mode})
            time.sleep(SETTLE_SECONDS)
            after = shot(session, pid, window_id)
            results[mode][name] = {
                **outcome(result),
                "screenshot_changed": (hashlib.sha256(before or b"").digest()
                                       != hashlib.sha256(after or b"").digest()),
            }
            if after:
                with open(os.path.join(run_dir, f"probe-{n}-{mode}-{name}.png"), "wb") as f:
                    f.write(after)
            before = after
    return results


def probe_browser(session, window):
    """What the browser tools say of a window: get_browser_state, and on a request for setup the browser_prepare call
    the refusal names, and get_browser_state again."""
    target = {"pid": window["pid"], "window_id": window["window_id"], "session": "probe"}
    steps = [("get_browser_state", outcome(session.call("get_browser_state", target)))]
    if set(steps[0][1]["refusals"]) & {"browser_requires_setup", "browser_consent_required"}:
        prepared = {**target, "strategy": {"kind": "existing_profile"}}
        steps.append(("browser_prepare", outcome(session.call("browser_prepare", prepared))))
        steps.append(("get_browser_state", outcome(session.call("get_browser_state", target))))
    return [{"tool": tool, **result} for tool, result in steps]


def probe_isolated(session):
    """Starts Cua's isolated browser, and binds its window: the steps, each with its result. VM-wide, not a window's."""
    prepared = session.call("browser_prepare", {"session": "probe", "allow_launch": True,
                                                "profile": {"mode": "isolated_new"}})
    steps = [{"tool": "browser_prepare", **outcome(prepared)}]
    pid = (prepared.get("structuredContent") or {}).get("prepared_pid")
    if pid:
        time.sleep(SETTLE_SECONDS * 3)
        windows = (session.call("list_windows", {}).get("structuredContent") or {}).get("windows") or []
        for window in (w for w in windows if w.get("pid") == pid):
            bound = session.call("get_browser_state", {"pid": pid, "window_id": window["window_id"],
                                                       "session": "probe"})
            structured = bound.get("structuredContent") or {}
            steps.append({"tool": "get_browser_state", **outcome(bound), "on_screen": window.get("is_on_screen"),
                          "binding_quality": structured.get("binding_quality"),
                          "mutation_allowed": structured.get("mutation_allowed")})
    return steps


def probe(cid, run_dir, send_input=False, browser=False, at=None):
    with open(os.path.join(run_dir, "ready.json")) as f:
        ready = json.load(f)
    window_id = int(ready["task_window"], 16)
    session = Session(cid, os.path.join(run_dir, "probe-mcp.jsonl"), client="hyprmasq-bench-cua-probe")
    try:
        tools = session.request("tools/list", {})["result"]["tools"]
        listed = session.call("list_windows", {})
        windows = (listed.get("structuredContent") or {}).get("windows") or []
        summary = {
            "tools": len(tools),
            "accessibility": ready.get("accessibility"),
            "cua_state": (ready.get("cua_status") or {}).get("state"),
            "list_windows_error": text_of(listed)[:400] if listed.get("isError") else None,
            "windows": [],
        }
        for n, w in enumerate(x for x in windows if x.get("title") != "user-terminal"):
            state = session.call("get_window_state", {"pid": w["pid"], "window_id": w["window_id"]})
            sc = state.get("structuredContent") or {}
            entry = {
                "task_window": w.get("window_id") == window_id,
                "title": w.get("title"), "app": w.get("app_name"), "on_screen": w.get("is_on_screen"),
                "error": bool(state.get("isError")), "elements": sc.get("element_count"),
                "degraded_reason": sc.get("degraded_reason"),
                "screenshot": [sc.get("screenshot_width"), sc.get("screenshot_height")],
                "screenshot_error": sc.get("screenshot_error"),
                "first_lines": text_of(state).splitlines()[:8],
            }
            image = image_of(state)
            if image:
                with open(os.path.join(run_dir, f"probe-{n}.png"), "wb") as f:
                    f.write(base64.b64decode(image["data"]))
            if browser:
                entry["browser"] = probe_browser(session, w)
            if send_input:
                entry["input"] = probe_input(session, run_dir, n, w, at if entry["task_window"] else None)
            summary["windows"].append(entry)
        if browser:
            summary["isolated_browser"] = probe_isolated(session)
    finally:
        session.close()
    with open(os.path.join(run_dir, "probe.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    task = next((w for w in summary["windows"] if w["task_window"]), None)
    return bool(task and task["elements"] and not task["error"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cid")
    parser.add_argument("run_dir")
    parser.add_argument("--input", action="store_true")
    parser.add_argument("--at", type=lambda v: tuple(int(c) for c in v.split(",")), metavar="X,Y")
    parser.add_argument("--browser", action="store_true")
    args = parser.parse_args()
    sys.exit(0 if probe(args.cid, args.run_dir, args.input, args.browser, args.at) else 1)
