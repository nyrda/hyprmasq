#!/usr/bin/env python3
"""Attributes each drag of a board run to the model or to hyprmasq: attribute.py RUN-DIR | RESULTS-DIR

For every act drag on the guide's window in mcp.jsonl, it maps the drag's start and end from shot pixels to the
page's coordinates, finds the drag the page logged for it (vm-board-events.jsonl), and classifies what happened:

- correct: the label landed where the agent dropped it, on its part;
- model: it landed where the agent dropped it, on another part or none; or the drag grabbed no label, or dropped it
  off the photo and the tray, where the page keeps it where it was;
- hyprmasq: the page saw no press, the press or the drop came more than TOLERANCE CSS pixels from where the agent
  aimed, or the page saw the drag start on a label and the label did not move;
- tray: the label went back to the tray where the agent dropped it; not sent: act refused the batch before the drag.

Shot pixels map to window-local logical pixels through the shot's frame, as hyprmasq maps them. Wayland tells the
page no screen coordinates, so the content's offset in the window is calibrated: it is the median difference between
where the agent's presses (clicks and drag starts) went in the window and where the page saw them, paired in order
within each act call. Each drag is then judged by its own start and end against that offset. Prints a table per run
and writes RUN-DIR/attribution.json.
"""

import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from check import classify  # noqa: E402

# How far, in CSS pixels, a press or a drop may be from where the agent aimed and still count as where it aimed: the
# rounding of the page's coordinates and of the calibration.
TOLERANCE = 6
# A pointer drag shorter than this is a click to the page (guide.py's SLOP).
SLOP = 4
PHOTO_WIDTH = 2000


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def shot_record(shots, shot, window):
    """Remembers a shot of a result, and the zooms into its changes."""
    if not shot or "id" not in shot or "frame" not in shot:
        return
    shots[shot["id"]] = {"window": window, "frame": shot["frame"], "width": shot["width"], "height": shot["height"]}
    frame = shot["frame"]
    sx, sy = shot["width"] / frame["width"], shot["height"] / frame["height"]
    for region in (shot.get("changes") or {}).get("regions") or []:
        # The server cuts a change's zoom to the whole logical pixels that cover its box in the base shot.
        x0 = math.floor(frame["x"] + region["x"] / sx + 1e-6)
        y0 = math.floor(frame["y"] + region["y"] / sy + 1e-6)
        x1 = math.ceil(frame["x"] + (region["x"] + region["width"]) / sx - 1e-6)
        y1 = math.ceil(frame["y"] + (region["y"] + region["height"]) / sy - 1e-6)
        shots[region["shot"]] = {"window": window, "frame": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
                                 "width": region["image_width"], "height": region["image_height"]}


def to_window(shot, point):
    """A point in a shot's pixels, in window-local logical pixels."""
    f = shot["frame"]
    return (f["x"] + point[0] * f["width"] / shot["width"], f["y"] + point[1] * f["height"] / shot["height"])


def act_calls(tap):
    """The answered tools/call requests in order, with the shots their results define."""
    pending, calls, shots = {}, [], {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            pending[msg.get("id")] = (entry["t"], msg["params"])
        elif entry["dir"] == "response" and msg.get("id") in pending:
            start, params = pending.pop(msg["id"])
            args = params.get("arguments") or {}
            result = msg.get("result") or {}
            sc = result.get("structuredContent") or {}
            text = "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
            if params.get("name") == "act":
                calls.append({"start": start, "end": entry["t"], "args": args, "result": sc,
                              "summary": text.split("\n", 1)[0], "shots": dict(shots)})
            shot_record(shots, sc.get("shot"), args.get("window"))
            # windows gives a shot of each window it matched, and act one of each new window.
            for other in (sc.get("windows") or []) + (sc.get("new_windows") or []):
                shot_record(shots, other.get("shot"), other.get("window"))
    return calls


def layout_at(layouts, t):
    """The page's layout at page time t: the last one logged before it."""
    before = [lay for lay in layouts if lay["t"] <= t]
    return before[-1] if before else (layouts[0] if layouts else None)


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


def zoom(layouts, layout):
    """The page's zoom in a layout: its devicePixelRatio against the first layout's, at the zoom the page loaded at."""
    return (layout or {}).get("dpr", 1) / (layouts[0].get("dpr", 1) if layouts else 1)


def photo_point(layout, client):
    r = layout["photo"]
    scale = (r[2] - r[0]) / PHOTO_WIDTH
    return [round((client[0] - r[0]) / scale, 1), round((client[1] - r[1]) / scale, 1)]


def presses(call, window):
    """The presses of an act call's delivered actions, in order: (index, window-local point)."""
    args, out = call["args"], []
    shot = call["shots"].get(args.get("shot"))
    if args.get("window") != window or not shot or not isinstance(args.get("actions"), list):
        return out
    done = (call["result"].get("actions") or {}).get("done", 0)
    for i, action in enumerate(args["actions"][:done]):
        if action.get("action") == "click":
            out += [(i, to_window(shot, (action["x"], action["y"])))] * int(action.get("count") or 1)
        elif action.get("action") == "drag":
            out.append((i, to_window(shot, action["from"])))
    return out


def attribute(run_dir):
    ready = load_json(os.path.join(run_dir, "ready.json"))
    window = ready["task_window"]
    offset = ready["host_time"] - ready["vm_time"]  # VM time + offset = host time
    calls = act_calls(load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
    page = sorted(load_jsonl(os.path.join(run_dir, "vm-board-events.jsonl")), key=lambda e: e["t"])
    for e in page:
        e["host"] = e["t"] / 1000 + offset
    layouts = [e for e in page if e["type"] == "layout"]
    downs = [e for e in page if e["type"] == "pointerdown"]

    # Each call's page events: from its request until the next call's request, at most 2 s after its answer.
    for i, call in enumerate(calls):
        until = min(call["end"] + 2, calls[i + 1]["start"] if i + 1 < len(calls) else math.inf)
        call["downs"] = [e for e in downs if call["start"] - 0.2 <= e["host"] < until]
        call["presses"] = presses(call, window)

    # The content's offset in the window, in logical pixels, from the presses that the page saw, paired in order
    # within a call. A page zoomed with ctrl+plus or ctrl+minus has CSS pixels of another size.
    pairs = []
    for call in calls:
        if call["presses"] and len(call["presses"]) == len(call["downs"]):
            for (_, local), down in zip(call["presses"], call["downs"]):
                z = zoom(layouts, layout_at(layouts, down["t"]))
                pairs.append((local[0] - down["x"] * z, local[1] - down["y"] * z))
    if pairs:
        content = (statistics.median(p[0] for p in pairs), statistics.median(p[1] for p in pairs))
        spread = max(math.hypot(p[0] - content[0], p[1] - content[1]) for p in pairs)
        calibration = {"offset": [round(v, 1) for v in content], "pairs": len(pairs), "spread": round(spread, 1)}
    else:
        # No call whose presses the page all saw: Firefox's own idea of the content's offset, mozInnerScreenX/Y.
        layout = next((lay for lay in layouts if all(isinstance(v, (int, float)) for v in lay.get("screen") or [None])),
                      None)
        z = zoom(layouts, layout)
        content = (layout["screen"][0] * z, layout["screen"][1] * z) if layout else None
        calibration = {"offset": [round(v, 1) for v in content] if content else None, "pairs": 0}

    # Each press's pointerdown. When the page saw them all, they pair in order; when it missed some, each press
    # takes the next one near where it was aimed, so that a lost press does not shift the others.
    for call in calls:
        if len(call["presses"]) == len(call["downs"]) or content is None:
            call["matched"] = call["downs"] + [None] * (len(call["presses"]) - len(call["downs"]))
            continue
        call["matched"], j = [], 0
        for _, local in call["presses"]:
            found = None
            for n in range(j, len(call["downs"])):
                down = call["downs"][n]
                z = zoom(layouts, layout_at(layouts, down["t"]))
                aimed = [(v - c) / z for v, c in zip(local, content)]
                if math.hypot(down["x"] - aimed[0], down["y"] - aimed[1]) <= TOLERANCE:
                    found, j = down, n + 1
                    break
            call["matched"].append(found)

    drags = []
    for call in calls:
        args = call["args"]
        if args.get("window") != window or not isinstance(args.get("actions"), list):
            continue
        shot = call["shots"].get(args.get("shot"))
        done = (call["result"].get("actions") or {}).get("done", 0)
        for i, action in enumerate(args["actions"]):
            if action.get("action") != "drag":
                continue
            drag = {"time": round(call["start"], 3), "shot": args.get("shot"), "from": action.get("from"),
                    "to": action.get("to")}
            drags.append(drag)
            if i >= done:
                drag.update(outcome="not sent", blame=None, why=call["summary"])
                continue
            if not shot or content is None:
                drag.update(outcome="unmapped", blame=None,
                            why="unknown shot" if not shot else "no press to calibrate the content's offset with")
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
                if end == "page":
                    drag.update(outcome="model", blame="model", why="dropped off the photo and the tray")
                elif math.hypot(aim_to[0] - aim_from[0], aim_to[1] - aim_from[1]) < SLOP:
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
    main(sys.argv[1])
