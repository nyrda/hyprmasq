#!/usr/bin/env python3
"""Checks a benchmark VM of the computer-use-linux backend without an agent: what its MCP server sees of the task's
window.

    probe.py CID RUN-DIR

RUN-DIR is the directory that bench/smoke.sh booted the VM into, with its ready.json. Through mcp_tap.py, as an agent
would reach it, this lists the windows, reads the task's window on its hidden workspace with get_app_state (its
accessibility tree and its screenshot), takes a screenshot of it with screenshot, presses Escape in it with press_key,
and asks for the focused window before and after. Writes RUN-DIR/probe.json with what each call gave,
RUN-DIR/probe-state.png and probe-window.png, the session's MCP log in RUN-DIR/probe-mcp.jsonl, and prints the
summary. Fails unless the task's window was listed, get_app_state read a tree with elements and screenshot gave an
image. get_app_state takes a screenshot of a targeted window only while it is focused.
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_probe import Session  # noqa: E402


def parsed(result):
    """A result's JSON text, its message and error, and whether it had an image, which it saves to path."""
    out = {}
    for c in result.get("content") or []:
        if c.get("type") == "text" and not out:
            try:
                out = json.loads(c["text"])
            except ValueError:
                out = {"text": c["text"][:400]}
    return out


def save_image(result, path):
    image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
    if image:
        with open(path, "wb") as f:
            f.write(base64.b64decode(image["data"]))
    return bool(image)


def focused(session):
    w = parsed(session.call("focused_window", {})).get("focused_window") or {}
    return w.get("title")


def probe(cid, run_dir):
    with open(os.path.join(run_dir, "ready.json")) as f:
        ready = json.load(f)
    window_id = int(ready["task_window"], 16)
    session = Session(cid, os.path.join(run_dir, "probe-mcp.jsonl"), client="hyprmasq-bench-cul-probe")
    summary = {"accessibility": ready.get("accessibility"),
               "readiness": ((ready.get("doctor") or {}).get("readiness") or {}).get("blockers")}
    try:
        summary["tools"] = len(session.request("tools/list", {})["result"]["tools"])
        summary["focused_before"] = focused(session)
        windows = parsed(session.call("list_windows", {})).get("windows") or []
        listed = next((w for w in windows if w.get("window_id") == window_id), None)
        summary["listed"] = listed and {k: listed.get(k) for k in ("app_id", "title", "workspace", "hidden", "bounds")}

        state = session.call("get_app_state", {"window_id": window_id})
        sc = parsed(state)
        shot = sc.get("screenshot") or {}
        summary["get_app_state"] = {
            "error": bool(state.get("isError")), "nodes": sc.get("accessibility_tree_raw_count"),
            "compacted": (sc.get("accessibility_tree_spill") or {}).get("node_count")
            or len(sc.get("accessibility_tree") or []),
            "truncated": sc.get("accessibility_tree_truncated"),
            "screenshot": [shot.get("width"), shot.get("height")] if shot else None,
            "screenshot_error": (sc.get("screenshot_error") or "")[:300] or None,
            "image": save_image(state, os.path.join(run_dir, "probe-state.png")),
        }
        summary["focused_after_read"] = focused(session)

        capture = session.call("screenshot", {"window_id": window_id})
        sc = parsed(capture)
        summary["screenshot"] = {"error": bool(capture.get("isError")),
                                 "size": [sc.get("width"), sc.get("height")],
                                 "message": (sc.get("message") or sc.get("text") or "")[:300],
                                 "image": save_image(capture, os.path.join(run_dir, "probe-window.png"))}
        summary["focused_after_screenshot"] = focused(session)

        key = session.call("press_key", {"key": "Escape", "window_id": window_id})
        sc = parsed(key)
        summary["action"] = {"call": "press_key Escape", "error": bool(key.get("isError")), "ok": sc.get("ok"),
                             "message": (sc.get("message") or sc.get("text") or "")[:400]}
        summary["focused_after_action"] = focused(session)
    finally:
        session.close()
    with open(os.path.join(run_dir, "probe.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    read = summary["get_app_state"]
    return bool(summary.get("listed") and read["nodes"] and summary["screenshot"]["image"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"] or len(sys.argv) != 3:
        print(__doc__)
        sys.exit(0 if sys.argv[1:] == ["--help"] else 2)
    sys.exit(0 if probe(sys.argv[1], sys.argv[2]) else 1)
