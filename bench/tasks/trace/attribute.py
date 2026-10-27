#!/usr/bin/env python3
"""Attributes each stroke of a trace run to the model or to hyprmasq: attribute.py RUN-DIR | RESULTS-DIR

For every act action on the figure's window in mcp.jsonl that draws (a drag, a click, a Shift+click line, a path), it
maps where the agent aimed from shot pixels to the page's coordinates, finds the stroke the page drew for it
(vm-trace-events.jsonl), and classifies what happened:

- on the line: the stroke started and ended where the agent aimed, and lies on the target's line;
- model: it started and ended where the agent aimed, and some of it lies off the line;
- hyprmasq: the page saw no press, or the stroke started or ended more than TOLERANCE CSS pixels from where the agent
  aimed;
- not sent: act refused the batch before the action; unmapped: the shot's frame is not known, or no press calibrated
  the content's offset in the window.

The mapping and the calibration are those of the board task's attribution, in bench/actlog.py. A stroke is measured
against the line of the run's variant as check.py measures the drawing: how far its points are from it, in photo
pixels, and how much of it lies off it. The per-run summary counts how the agent drew: actions per act call,
strokes of each kind, undos and clears, and how many strokes did not last to the end (vm-trace-strokes.json), which
is the work the agent redid or took back. Prints a table per run and writes RUN-DIR/attribution.json.
"""

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from actlog import (TOLERANCE, act_calls, action_shot, calibrate, layout_at, load_json, load_jsonl,  # noqa: E402
                    page_events, to_page, zoom)
import check  # noqa: E402
from check import samples, to_line  # noqa: E402

EVENTS = "vm-trace-events.jsonl"


def photo_point(layout, client):
    r = layout["photo"]
    scale = (r[2] - r[0]) / check.TRACK["size"][0]
    return [round((client[0] - r[0]) / scale, 1), round((client[1] - r[1]) / scale, 1)]


def on_photo(layout, client):
    r = layout["photo"]
    return r[0] <= client[0] <= r[2] and r[1] <= client[1] <= r[3]


def aims(action):
    """The points an action aims at, in shot pixels, from its first to its last, and the stroke it draws: a line for
    a press with Shift, which the page draws from the last stroke's end to the press, a brush stroke otherwise. The
    page draws only with the left button, so a press of another one, or a path without a button, draws nothing."""
    kind = action.get("action")
    if (action.get("button") or "left").lower() != "left":
        return None, None
    if kind == "drag":
        points = [action.get("from"), action.get("to")]
    elif kind == "path":
        points = action.get("points") or []
    elif kind == "click":
        points = [[action.get("x"), action.get("y")]]
    else:
        return None, None
    # An action that act refused for its arguments may lack its points; it drew nothing.
    if not points or not all(isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(v, (int, float)) and
                                                                                  not isinstance(v, bool) for v in p)
                             for p in points):
        return None, None
    # act takes modifiers in any case, as Shift or SHIFT.
    return points, "line" if "shift" in (m.lower() for m in action.get("modifiers") or []) else "brush"


def stroke_key(stroke):
    """A stroke as its kind and points, equal for equal numbers, 106 as 106.0."""
    return stroke["kind"], tuple(tuple(float(v) for v in p) for p in stroke["points"])


def off_line(points):
    """How much of a stroke along points lies off the line, in photo pixels, and the farthest it gets from it."""
    measured = [(w, *to_line(p)) for p, w in samples([{"points": points}])]
    return round(sum(w for w, d, tol in measured if d > tol), 1), max((d for _, d, _ in measured), default=0)


def attribute(run_dir):
    # The run's variant, which the trace was graded against.
    variant = (load_json(os.path.join(run_dir, "run.json")) or {}).get("variant") or check.default_variant()
    check.load(os.path.join(HERE, variant))
    ready = load_json(os.path.join(run_dir, "ready.json"))
    window = ready["task_window"]
    offset = ready["host_time"] - ready["vm_time"]  # VM time + offset = host time
    calls = act_calls(load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
    page = page_events(run_dir, EVENTS, offset)
    layouts = [e for e in page if e["type"] == "layout"]
    content, calibration = calibrate(calls, page, window)
    final = (load_json(os.path.join(run_dir, "vm-trace-strokes.json")) or {}).get("strokes")

    strokes, per_call = [], []
    for call in calls:
        args = call["args"]
        if call["window"] != window or call["actions"] is None:
            continue
        shot = call["shots"].get(args.get("shot"))
        done = (call["result"].get("actions") or {}).get("done", 0)
        drawing = 0
        for i, action in enumerate(call["actions"]):
            shot = action_shot(call, action)
            aimed, kind = aims(action)
            if aimed is None:
                continue
            base = {"time": round(call["start"], 3), "shot": args.get("shot"), "action": action.get("action"),
                    "kind": kind, "aimed_shot": aimed}
            if action.get("action") == "path":
                base["curve"] = action.get("curve") or "line"
            stroke = dict(base)
            if i >= done:
                stroke.update(outcome="not sent", blame=None, why=call["summary"])
                strokes.append(stroke)
                continue
            if not shot or content is None:
                why = "no press to calibrate the content's offset with"
                if not shot:
                    known = call["unknown"].get(args.get("shot"))
                    why = f"unknown shot: {known}" if known else "unknown shot"
                stroke.update(outcome="unmapped", blame=None, why=why)
                strokes.append(stroke)
                drawing += 1
                continue
            # The page's presses for this action: those in its place among the call's presses. A click with a count
            # presses more than once, and each press draws a dot of its own.
            for k in [n for n, (index, _) in enumerate(call["presses"]) if index == i]:
                stroke, skind = dict(base), kind
                down = call["matched"][k] if k < len(call["matched"]) else None
                layout = layout_at(layouts, down["t"] if down else (call["start"] - offset) * 1000)
                z = zoom(layouts, layout)
                client = [to_page(shot, p, content, z) for p in aimed]
                # A click off the photo, such as on Undo, draws nothing and is no stroke.
                if action.get("action") == "click" and layout and not on_photo(layout, client[0]):
                    continue
                strokes.append(stroke)
                drawing += 1
                stroke["aimed"] = [[round(v, 1) for v in p] for p in client]
                if layout:
                    stroke["aimed_photo"] = [photo_point(layout, p) for p in client]
                if down is None:
                    why = "the page saw no press" if not call["downs"] else "the page saw no press where the action aimed"
                    stroke.update(outcome="hyprmasq", blame="hyprmasq", why=why)
                    continue
                later = [e for e in page if e["t"] >= down["t"]]
                nxt = next((e["t"] for e in later if e["type"] == "pointerdown" and e is not down), math.inf)
                drawn = next((e for e in later if e["type"] == "stroke" and e["t"] < nxt
                              and e["host"] < call["end"] + 2), None)
                if drawn is None:
                    where = down.get("over")
                    if where != "photo":
                        stroke.update(outcome="model", blame="model", why=f"pressed on the {where}, not the photo")
                    else:
                        stroke.update(outcome="hyprmasq", blame="hyprmasq", why="the page drew no stroke for the press")
                    continue
                # A press with Shift and no stroke to go on from, on an empty drawing or after Clear or an Undo of the last
                # one, draws as a press without it does: a dot, or a stroke along the drag or path.
                if skind == "line" and drawn["kind"] == "brush":
                    skind = stroke["kind"] = "brush"
                # Where the stroke started and ended against where the agent aimed. A Shift+click line starts where the
                # last stroke ended, so only its end is the agent's.
                # A line ends at the press, the first point aimed at, also for a drag or path with Shift.
                ends = [(drawn["to"], client[0])] if skind == "line" else [(drawn["from"], client[0]),
                                                                         (drawn["to"], client[-1])]
                landed_off = max(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in ends)
                off, farthest = off_line(drawn["points"])
                stroke.update(drawn=drawn["kind"], points=len(drawn["points"]), landed_off=round(landed_off, 1),
                              off_line_px=off, farthest_px=round(farthest, 1))
                stroke["_drawn"] = stroke_key(drawn)
                if drawn["kind"] != skind:
                    stroke.update(outcome="hyprmasq", blame="hyprmasq",
                                  why=f"the page drew a {drawn['kind']} stroke for a {skind} one")
                elif landed_off > TOLERANCE:
                    stroke.update(outcome="hyprmasq", blame="hyprmasq",
                                  why=f"drawn {landed_off:.0f} px from where the agent aimed")
                elif off > 0:
                    stroke.update(outcome="model", blame="model",
                                  why=f"where aimed, {off:g} px off the line, up to {farthest:.1f} px from it")
                else:
                    stroke.update(outcome="on the line", blame=None, why="where aimed, on the line")
        if drawing:
            per_call.append(drawing)

    # Which strokes lasted to the end: each stroke of the final drawing is the latest one drawn with its points, so
    # that a stroke taken back and drawn again the same counts once, as the one drawn again.
    if final is not None:
        left = {}
        for f in final:
            key = stroke_key(f)
            left[key] = left.get(key, 0) + 1
        for s in reversed(strokes):
            if "_drawn" in s:
                s["kept"] = left.get(s["_drawn"], 0) > 0
                if s["kept"]:
                    left[s["_drawn"]] -= 1
    for s in strokes:
        s.pop("_drawn", None)
    kinds = {}
    for s in strokes:
        name = "shift+click" if s["kind"] == "line" else f"{s['curve']} path" if s.get("curve") else s["action"]
        kinds[name] = kinds.get(name, 0) + 1
    summary = {
        "act_calls_drawing": len(per_call),
        "strokes_per_call": per_call,
        "actions": kinds,
        "undos": sum(1 for e in page if e["type"] == "undo"),
        "clears": sum(1 for e in page if e["type"] == "clear"),
        "hyprmasq": sum(1 for s in strokes if s.get("blame") == "hyprmasq"),
        "model": sum(1 for s in strokes if s.get("blame") == "model"),
        "max_landed_off": max((s["landed_off"] for s in strokes if "landed_off" in s), default=None),
        "not_kept": sum(1 for s in strokes if s.get("kept") is False),
    }
    out = {"calibration": calibration, "summary": summary, "strokes": strokes}
    with open(os.path.join(run_dir, "attribution.json"), "w") as f:
        json.dump(out, f, indent=2)
    return out


def show(run_dir, out):
    c, s = out["calibration"], out["summary"]
    print(f"## {os.path.basename(os.path.normpath(run_dir))}\n")
    print(f"Content offset {c['offset']} from {c['pairs']} presses" + (f", spread {c['spread']} px" if c["pairs"] else ""))
    print(f"\n{s['act_calls_drawing']} act calls drew, with {s['strokes_per_call']} drawing actions; "
          f"actions {s['actions']}, {s['undos']} undos, {s['clears']} clears, {s['not_kept']} strokes not kept; "
          f"largest distance from aim {s['max_landed_off']} CSS px.")
    print("\n| # | Action | Aimed (photo px) | Off aim px | Off line px | Farthest px | Kept | Outcome | Why |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    fmt = lambda p: f"{p[0]:g},{p[1]:g}"  # noqa: E731
    for n, st in enumerate(out["strokes"], 1):
        aimed = " → ".join(fmt(p) for p in st.get("aimed_photo") or []) or "–"
        print(f"| {n} | {st['action']}{' (line)' if st['kind'] == 'line' else ''} | {aimed} | "
              f"{st.get('landed_off', '–')} | {st.get('off_line_px', '–')} | {st.get('farthest_px', '–')} | "
              f"{ {True: 'yes', False: 'no'}.get(st.get('kept'), '–')} | {st['outcome']} | {st['why']} |")
    print()


def main(path):
    runs = [path] if os.path.exists(os.path.join(path, "ready.json")) else [
        os.path.join(path, name) for name in sorted(os.listdir(path))
        if os.path.exists(os.path.join(path, name, EVENTS))]
    for run in runs:
        show(run, attribute(run))


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main(sys.argv[1])
