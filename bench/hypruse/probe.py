#!/usr/bin/env python3
"""Checks a benchmark VM of the hypruse backend without an agent: what hypruse's MCP server sees of the task's window.

    probe.py CID RUN-DIR

RUN-DIR is the directory that bench/smoke.sh booted the VM into, with its ready.json. Through mcp_tap.py, as an agent
would reach it, this lists the desktop, takes a screenshot of the task's window on its hidden workspace, reads its
accessibility tree (ui) and its numbered marks, presses Escape in it with keyboard, hypruse's background path for a
window that is not the user's, and lists the desktop again. Writes RUN-DIR/probe.json with what each call gave,
RUN-DIR/probe-window.png and probe-marks.png, the session's MCP log in RUN-DIR/probe-mcp.jsonl, and prints the summary.
Fails unless the task's window was listed, its screenshot came from the window itself and its tree had elements.
"""

import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mcp_probe import Session  # noqa: E402


def text_of(result):
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")


def save_image(result, path):
    image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
    if image:
        with open(path, "wb") as f:
            f.write(base64.b64decode(image["data"]))
    return bool(image)


def structured(result):
    """A result's structured content without FastMCP's wrapper of a list."""
    sc = result.get("structuredContent")
    return sc.get("result") if isinstance(sc, dict) and set(sc) == {"result"} else sc


def probe(cid, run_dir):
    with open(os.path.join(run_dir, "ready.json")) as f:
        ready = json.load(f)
    window = ready["task_window"]
    session = Session(cid, os.path.join(run_dir, "probe-mcp.jsonl"), client="hyprmasq-bench-hypruse-probe")
    summary = {"accessibility": ready.get("accessibility"), "doctor": (ready.get("doctor") or {}).get("lines")}
    try:
        summary["tools"] = len(session.request("tools/list", {})["result"]["tools"])
        desktop = structured(session.call("desktop", {}))
        listed = next((w for w in desktop.get("windows") or [] if w.get("address") == window), None)
        summary["listed"] = listed and {k: listed.get(k) for k in ("workspace", "class", "title", "at", "size")}
        summary["user"] = {"active_window": desktop.get("active_window"),
                           "workspaces": [w["name"] for w in desktop.get("workspaces") or [] if w.get("visible")]}

        shot = session.call("screenshot", {"window": window})
        meta = next((json.loads(c["text"]) for c in shot.get("content") or [] if c.get("type") == "text"
                     and c["text"].startswith("{")), {})
        summary["screenshot"] = {"error": bool(shot.get("isError")), "image": meta.get("image"),
                                 "source": meta.get("source"), "visible": meta.get("visible"),
                                 "saved": save_image(shot, os.path.join(run_dir, "probe-window.png")),
                                 "text": None if meta else text_of(shot)[:300]}

        tree = session.call("ui", {"window": window})
        elements = [e for e in structured(tree) or [] if isinstance(e, dict) and "role" in e]
        summary["ui"] = {"error": bool(tree.get("isError")), "elements": len(elements),
                         "roles": sorted({e["role"] for e in elements}),
                         "first": [f"{e['role']} {e.get('name')!r}" for e in elements[:6]],
                         "text": text_of(tree)[:300] if not elements else None}

        marks = session.call("marks", {"window": window})
        summary["marks"] = {"error": bool(marks.get("isError")),
                            "image": save_image(marks, os.path.join(run_dir, "probe-marks.png")),
                            "text": text_of(marks)[:200]}

        key = session.call("keyboard", {"action": "key", "keys": "esc", "window": window})
        summary["action"] = {"call": "keyboard key esc", "error": bool(key.get("isError")), "text": text_of(key)[:400]}

        after = structured(session.call("desktop", {}))
        summary["user_after"] = {"active_window": after.get("active_window"),
                                 "workspaces": [w["name"] for w in after.get("workspaces") or [] if w.get("visible")]}
    finally:
        session.close()
    with open(os.path.join(run_dir, "probe.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return bool(summary.get("listed") and summary["screenshot"]["source"] == "window" and summary["ui"]["elements"])


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"] or len(sys.argv) != 3:
        print(__doc__)
        sys.exit(0 if sys.argv[1:] == ["--help"] else 2)
    sys.exit(0 if probe(sys.argv[1], sys.argv[2]) else 1)
