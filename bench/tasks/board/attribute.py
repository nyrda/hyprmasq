#!/usr/bin/env python3
"""Attributes each drag of a board run to the model or to hyprmasq: attribute.py RUN-DIR | RESULTS-DIR

For every act drag on the guide's window in mcp.jsonl, it maps the drag's start and end from shot pixels to the
page's coordinates, finds the drag the page logged for it (vm-board-events.jsonl), and classifies what happened:

- correct: the label landed where the agent dropped it, on its part;
- model: it landed where the agent dropped it, on another part or none; or the drag grabbed no label, or dropped it
  off the photo and the tray, where the page keeps it where it was; or it was too short to start a drag-and-drop;
- hyprmasq: the page saw no press, the press or the drop came more than TOLERANCE CSS pixels from where the agent
  aimed, or the page saw the drag start on a label and the label did not move;
- tray: the label went back to the tray where the agent dropped it; not sent: act refused the batch before the drag.

Shot pixels map to window-local logical pixels through the shot's frame, as hyprmasq maps them. Wayland tells the
page no screen coordinates, so the content's offset in the window is calibrated: it is the median difference between
where the agent's presses (clicks and drag starts) went in the window and where the page saw them, paired in order
within each act call. Each drag is then judged by its own start and end against that offset. Prints a table per run
and writes RUN-DIR/attribution.json.

hyprmasq-mcp gives Claude Code results without structured content, so their shots and the actions act delivered come
from the text, and their frames from what made each shot (see text_content). A drag in a shot whose frame the text
does not tell is unmapped, with why.
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from actlog import (TOLERANCE, act_calls, calibrate, layout_at, load_json, load_jsonl, page_events,  # noqa: E402
                    to_window, zoom)
from check import classify  # noqa: E402

# TOLERANCE, from actlog, is how far in CSS pixels a press or a drop may be from where the agent aimed and still count
# as where it aimed.
# A pointer drag shorter than this is a click to the page (guide.py's SLOP).
SLOP = 4
# Firefox starts a drag-and-drop only once the pointer has moved more than this many device pixels from the press
# along either axis (EventStateManager::IsEventOutsideDragThreshold). On GTK it is gtk-dnd-drag-threshold, whose
# default of 8 the VM leaves.
DND_THRESHOLD = 8
PHOTO_WIDTH = 2000


def inside(rect, point):
    return rect[0] <= point[0] <= rect[2] and rect[1] <= point[1] <= rect[3]


def where(layout, point):
    if inside(layout["photo"], point):
        return "photo"
    if inside(layout["tray"], point):
        return "tray"
    return "page"


def label_at(layout, point):
    for label, rects in layout.get("labels", {}).items():
        if any(inside(r, point) for r in rects):
            return label
    return None


def photo_point(layout, client):
    r = layout["photo"]
    scale = (r[2] - r[0]) / PHOTO_WIDTH
    return [round((client[0] - r[0]) / scale, 1), round((client[1] - r[1]) / scale, 1)]


def device_move(layout, a, b):
    """How far b is from a along the axis it is furthest, in device pixels, as Firefox measures a drag."""
    return max(abs(b[0] - a[0]), abs(b[1] - a[1])) * layout.get("dpr", 1)


def too_short_for_dnd(layout, down, gesture, aim_from, aim_to):
    """Whether a drag of the dnd variant was too short for Firefox to start a drag-and-drop, and hyprmasq delivered it
    as aimed: the agent aimed it within DND_THRESHOLD, the page saw the pointer stay within it until the release, saw
    the release where the agent aimed, and saw no dragstart. guide.py's SLOP is the pointer variant's."""
    up = next((e for e in gesture if e["type"] == "pointerup"), None)
    if up is None or any(e["type"] == "dragstart" for e in gesture):
        return False
    moves = [e for e in gesture[:gesture.index(up) + 1] if e["type"] in ("pointermove", "pointerup")]
    press = (down["x"], down["y"])
    return (math.hypot(up["x"] - aim_to[0], up["y"] - aim_to[1]) <= TOLERANCE
            and device_move(layout, aim_from, aim_to) <= DND_THRESHOLD
            and all(device_move(layout, press, (e["x"], e["y"])) <= DND_THRESHOLD for e in moves))


def attribute(run_dir):
    ready = load_json(os.path.join(run_dir, "ready.json"))
    window = ready["task_window"]
    offset = ready["host_time"] - ready["vm_time"]  # VM time + offset = host time
    calls = act_calls(load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
    page = page_events(run_dir, "vm-board-events.jsonl", offset)
    layouts = [e for e in page if e["type"] == "layout"]
    content, calibration = calibrate(calls, page, window)

    drags = []
    for call in calls:
        args = call["args"]
        if call["window"] != window or call["actions"] is None:
            continue
        shot = call["shots"].get(args.get("shot"))
        done = (call["result"].get("actions") or {}).get("done", 0)
        for i, action in enumerate(call["actions"]):
            # A path with a button moves a label as a drag does, from its first point to its last.
            if action.get("action") == "path" and len(action.get("points") or []) >= 2 and action.get("button") != "none":
                action = {**action, "action": "drag", "from": action["points"][0], "to": action["points"][-1]}
            if action.get("action") != "drag":
                continue
            drag = {"time": round(call["start"], 3), "shot": args.get("shot"), "from": action.get("from"),
                    "to": action.get("to")}
            drags.append(drag)
            if i >= done:
                drag.update(outcome="not sent", blame=None, why=call["summary"])
                continue
            if not shot or content is None:
                why = "no press to calibrate the content's offset with"
                if not shot:
                    known = call["unknown"].get(args.get("shot"))
                    why = f"unknown shot: {known}" if known else "unknown shot"
                drag.update(outcome="unmapped", blame=None, why=why)
                continue
            # The page's press for this drag: the one in its place among the call's presses.
            k = next(n for n, (index, _) in enumerate(call["presses"]) if index == i)
            down = call["matched"][k] if k < len(call["matched"]) else None
            layout = layout_at(layouts, down["t"] if down else (call["start"] - offset) * 1000)
            z = zoom(layouts, layout)
            aim_from = [(v - c) / z for v, c in zip(to_window(shot, action["from"]), content)]
            aim_to = [(v - c) / z for v, c in zip(to_window(shot, action["to"]), content)]
            drag.update(aim_from=[round(v, 1) for v in aim_from], aim_to=[round(v, 1) for v in aim_to])
            if layout:
                drag["aim_label"] = label_at(layout, aim_from)
                if where(layout, aim_to) == "photo":
                    drag["aim_point"] = photo_point(layout, aim_to)
            if down is None:
                why = "the page saw no press" if not call["downs"] else "the page saw no press where the drag started"
                drag.update(outcome="hyprmasq", blame="hyprmasq", why=why)
                continue
            drag["label"] = down.get("label")
            drag["press"] = [down["x"], down["y"]]
            pressed_off = math.hypot(down["x"] - aim_from[0], down["y"] - aim_from[1])
            later = [e for e in page if e["t"] > down["t"]]
            nxt = next((e["t"] for e in later if e["type"] == "pointerdown"), math.inf)
            gesture = [e for e in later if e["t"] < nxt and e["host"] < call["end"] + 2]
            drag["page"] = [e["type"] for e in gesture if e["type"] in ("dragstart", "drop", "dragend", "pointerup",
                                                                         "pointercancel", "place")]
            place = next((e for e in gesture if e["type"] == "place"), None)
            if pressed_off > TOLERANCE:
                drag.update(outcome="hyprmasq", blame="hyprmasq",
                            why=f"pressed {pressed_off:.0f} px from where the drag started")
            elif not down.get("label"):
                drag.update(outcome="model", blame="model", why="the drag started on no label")
            elif place is None:
                end = where(layout, aim_to) if layout else None
                dnd = bool(layout) and layout.get("variant") == "dnd"
                if end == "page":
                    drag.update(outcome="model", blame="model", why="dropped off the photo and the tray")
                elif dnd and too_short_for_dnd(layout, down, gesture, aim_from, aim_to):
                    drag.update(outcome="model", blame="model", why="too short to start a drag-and-drop")
                elif not dnd and math.hypot(aim_to[0] - aim_from[0], aim_to[1] - aim_from[1]) < SLOP:
                    drag.update(outcome="model", blame="model", why=f"moved less than {SLOP} px")
                else:
                    seen = ", ".join(dict.fromkeys(drag["page"])) or "only the press"
                    drag.update(outcome="hyprmasq", blame="hyprmasq", why=f"the label did not move; the page saw {seen}")
            else:
                landed_off = math.hypot(place["x"] - aim_to[0], place["y"] - aim_to[1])
                drag.update(landed=[place["x"], place["y"]], point=place.get("point"), off=round(landed_off, 1))
                if landed_off > TOLERANCE:
                    drag.update(outcome="hyprmasq", blame="hyprmasq",
                                why=f"landed {landed_off:.0f} px from where the agent dropped it")
                elif place["to"] == "page":
                    drag.update(outcome="model", blame="model", why="dropped off the photo and the tray")
                elif place["to"] == "tray":
                    drag.update(outcome="tray", blame=None, why="back to the tray")
                else:
                    verdict = classify(place["label"], place["point"])
                    if verdict["verdict"] == "correct":
                        drag.update(outcome="correct", blame=None, why="on its part")
                    else:
                        on = f"on {verdict['on']}" if verdict["on"] else "on no part"
                        drag.update(outcome="model", blame="model", why=f"where aimed, {on}")

    # Each label at the end, and who put it there: its last drag that moved it, or the drags that failed to.
    result = load_json(os.path.join(run_dir, "vm-result.json"), {})
    final = ((result.get("task") or {}).get("check") or {}).get("checks", {}).get("labels", {})
    labels = {}
    for label, verdict in final.items():
        mine = [d for d in drags if d.get("label") == label or (d.get("label") is None and d.get("aim_label") == label)]
        moved = [d for d in mine if d.get("outcome") in ("correct", "model", "tray") and d.get("landed")]
        last = moved[-1] if moved else None
        blame = None
        if verdict["verdict"] != "correct":
            if last:
                blame = last["blame"] or "model"
            elif any(d.get("blame") == "hyprmasq" for d in mine):
                blame = "hyprmasq"
            else:
                blame = "model"
        labels[label] = {**verdict, "drags": len(mine), "blame": blame,
                         "failed_drags": sum(1 for d in mine if d.get("blame") == "hyprmasq")}
    out = {"calibration": calibration, "drags": drags, "labels": labels}
    with open(os.path.join(run_dir, "attribution.json"), "w") as f:
        json.dump(out, f, indent=2)
    return out


def show(run_dir, out):
    c = out["calibration"]
    print(f"## {os.path.basename(os.path.normpath(run_dir))}\n")
    print(f"Content offset {c['offset']} from {c['pairs']} presses" + (f", spread {c['spread']} px" if c["pairs"] else ""))
    print("\n| # | Label | From → to (shot px) | Aimed (photo px) | Landed (photo px) | Off px | Outcome | Why |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for n, d in enumerate(out["drags"], 1):
        fmt = lambda p: f"{p[0]:g},{p[1]:g}" if p else "–"  # noqa: E731
        print(f"| {n} | {d.get('label') or d.get('aim_label') or '–'} | {fmt(d['from'])} → {fmt(d['to'])} | "
              f"{fmt(d.get('aim_point'))} | {fmt(d.get('point'))} | {d.get('off', '–')} | {d['outcome']} | {d['why']} |")
    print("\n| Label | At the end | Drags | Failed drags | Blame |")
    print("| --- | --- | --- | --- | --- |")
    for label, v in out["labels"].items():
        at = v["verdict"] + (f" ({v['on']})" if v["on"] and v["verdict"] != "correct" else "")
        print(f"| {label} | {at} | {v['drags']} | {v['failed_drags']} | {v['blame'] or '–'} |")
    print()


def main(path):
    runs = [path] if os.path.exists(os.path.join(path, "ready.json")) else [
        os.path.join(path, name) for name in sorted(os.listdir(path))
        if os.path.exists(os.path.join(path, name, "vm-board-events.jsonl"))]
    for run in runs:
        show(run, attribute(run))


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main(sys.argv[1])
