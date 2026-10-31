#!/usr/bin/env python3
"""Checks a benchmark VM of the Cua backend without an agent: what `cua-driver mcp` sees of the task's window.

    probe.py CID RUN-DIR

RUN-DIR is the directory that bench/smoke.sh booted the VM into, with its ready.json. Through mcp_tap.py, as an agent
would reach it, this lists the windows and reads each but the user's terminal with get_window_state: its
accessibility tree and its screenshot. Writes RUN-DIR/probe.json, with how many elements each tree had and the
screenshot's size, RUN-DIR/probe-N.png of the Nth window and the session's MCP log in RUN-DIR/probe-mcp.jsonl, and
prints the summary. Fails unless the task's window, found by its address, had a tree with elements.
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_probe import Session  # noqa: E402


def text_of(result):
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")


def probe(cid, run_dir):
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
            "windows": [],
        }
        for n, w in enumerate(x for x in windows if x.get("title") != "user-terminal"):
            state = session.call("get_window_state", {"pid": w["pid"], "window_id": w["window_id"]})
            sc = state.get("structuredContent") or {}
            summary["windows"].append({
                "task_window": w.get("window_id") == window_id,
                "title": w.get("title"), "app": w.get("app_name"), "on_screen": w.get("is_on_screen"),
                "error": bool(state.get("isError")), "elements": sc.get("element_count"),
                "degraded_reason": sc.get("degraded_reason"),
                "screenshot": [sc.get("screenshot_width"), sc.get("screenshot_height")],
                "screenshot_error": sc.get("screenshot_error"),
                "first_lines": text_of(state).splitlines()[:8],
            })
            image = next((c for c in state.get("content") or [] if c.get("type") == "image"), None)
            if image:
                with open(os.path.join(run_dir, f"probe-{n}.png"), "wb") as f:
                    f.write(base64.b64decode(image["data"]))
    finally:
        session.close()
    with open(os.path.join(run_dir, "probe.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    task = next((w for w in summary["windows"] if w["task_window"]), None)
    return bool(task and task["elements"] and not task["error"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"] or len(sys.argv) != 3:
        print(__doc__)
        sys.exit(0 if sys.argv[1:] == ["--help"] else 2)
    sys.exit(0 if probe(sys.argv[1], sys.argv[2]) else 1)
