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
import re
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
# Firefox starts a drag-and-drop only once the pointer has moved more than this many device pixels from the press
# along either axis (EventStateManager::IsEventOutsideDragThreshold). On GTK it is gtk-dnd-drag-threshold, whose
# default of 8 the VM leaves.
DND_THRESHOLD = 8
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


def shot_record(shots, unknown, shot, window):
    """Remembers a shot of a result, and the zooms into its changes. A shot whose frame the result's text does not
    tell goes to unknown, with why."""
    if shot and "id" in shot and shot.get("unknown"):
        unknown[shot["id"]] = shot["unknown"]
        for region in (shot.get("changes") or {}).get("regions") or []:
            unknown[region["shot"]] = f"a zoom of {shot['id']}, whose frame is not known"
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


NUM = r"-?\d+(?:\.\d+)?"
# A shot as shotText in mcp/internal/server/summary.go names it: a full one with its image size and notes, or a
# changes shot by the shot it was compared with.
FULL_SHOT = r"shot (s\d+) (\d+)×(\d+)(?: \((.*)\))?"
CHANGES_SHOT = r"shot (s\d+) = (s\d+)(?:, no change(?: outside animations)?| \+ \d+ changes?)"
# A line of windows (windowLine in windows.go), which gives the window's logical size.
WINDOW_LINE = re.compile(rf"(0x[0-9a-f]+) · .* · workspace .+ \((?:hidden|visible)\) · ({NUM})×({NUM})(?: · .*)?")
# The line of a shot of another window: of the one window that windows listed, of a new or a late window, or of the
# window that a closed target came from.
OTHER_SHOT = re.compile(rf"{FULL_SHOT} of (?:(window|new window|late window) (\S+)|(\S+), the window it came from)")
ZOOM_LINE = re.compile(r"zoom (s\d+) (\d+)×(\d+) of (s\d+) at (\d+),(\d+) (\d+)×(\d+)")
SCREENSHOT = re.compile(rf"{FULL_SHOT}(?: · zoom of (s\d+) at ({NUM}),({NUM}) ({NUM})×({NUM}))? · window (\S+) "
                        rf"({NUM})×({NUM})(?: · .*)?")
DELIVERED = re.compile(r"(\d+)/(\d+) delivered\b.*")


def canonical(window):
    """A window's address as the server writes it, as canonicalWindow in server.go makes it."""
    m = re.fullmatch(r"0[xX]([0-9a-fA-F]+)", window or "")
    return f"0x{int(m.group(1), 16):x}" if m else window


def window_size(sizes, window, size):
    """Remembers a window's size that a text gave, and keeps the image size of its last shot while it stays."""
    if window not in sizes or sizes[window][0] != size:
        sizes[window] = (size, None)


def full_frame(shot_id, width, height, window, sizes, now):
    """A shot of the whole window, whose frame is the window when popups reach outside it in neither direction: then its
    image has the window's aspect ratio, within the rounding of its size. now tells that the shot's text gives the
    window's size; otherwise it comes from an earlier text, and holds only for a shot with the image size of the
    shot that text came with, since the window may have been resized since."""
    shot = {"id": shot_id, "width": width, "height": height}
    if window not in sizes:
        return {**shot, "unknown": "the text gives no size of its window"}
    size, image = sizes[window]
    if not now and image != (width, height):
        # The window's size is not known again until a text gives it.
        del sizes[window]
        return {**shot, "unknown": "no text gave its window's size with a shot of its image size, so the window may "
                                   "have been resized"}
    if abs(height - size[1] * width / size[0]) > 1 and abs(width - size[0] * height / size[1]) > 1:
        return {**shot, "unknown": f"its frame is not its window's {size[0]:g}×{size[1]:g}, as when popups reach "
                                   "outside the window"}
    sizes[window] = (size, (width, height))
    return {**shot, "frame": {"x": 0, "y": 0, "width": size[0], "height": size[1]}}


def region_frame(shot_id, width, height, of, x, y, w, h):
    """A zoom into a region of shot of: the whole logical pixels that cover the region cut to of, as resolveRegion and
    logicalRegion in capture.go and shots.go map it."""
    shot = {"id": shot_id, "width": width, "height": height}
    if of is None:
        return {**shot, "unknown": "a zoom of a shot whose frame is not known"}
    w, h = min(w, of["width"] - x), min(h, of["height"] - y)
    x0, y0 = to_window(of, (x, y))
    x1, y1 = to_window(of, (x + w, y + h))
    left, top = math.floor(x0 + 1e-9), math.floor(y0 + 1e-9)
    right, bottom = math.ceil(x1 - 1e-9), math.ceil(y1 - 1e-9)
    return {**shot, "frame": {"x": left, "y": top, "width": max(1, right - left), "height": max(1, bottom - top)}}


def capture_region(args):
    """act's capture region, or None for another capture."""
    capture = args.get("capture")
    if isinstance(capture, dict) and isinstance(capture.get("region"), dict):
        capture = capture["region"]
    if not isinstance(capture, dict):
        return None
    return capture if all(isinstance(capture.get(k), (int, float)) for k in ("x", "y", "width", "height")) else None


def text_content(tool, args, text, shots, sizes):
    """What the attribution needs of a result without structured content, as Claude Code's are, read from its text:
    the actions act delivered and the shots, in the shape of the structured content. The text gives no frames, so they
    come from what made the shot: the window's size for a shot of the whole window, the region of an earlier shot for a
    zoom, and for a changes shot the shot it was compared with. sizes keeps the windows' sizes that the texts gave, and
    the image size of the shot that came with each."""
    lines = text.split("\n")
    # Errors lead with their error line, and the summary of a call that sent nothing and has no shot is left out.
    summary = next((line for line in lines if not line.startswith("error ")), "")
    sc = {"windows": [], "new_windows": [], "late_windows": []}
    window = canonical(args.get("window"))
    # The windows whose sizes this text gives.
    listed = set()

    def other_shots(start):
        for line in lines[start:]:
            m = OTHER_SHOT.fullmatch(line)
            if not m:
                continue
            kind, address = (m.group(5), m.group(6)) if m.group(5) else ("source", m.group(7))
            shot = full_frame(m.group(1), int(m.group(2)), int(m.group(3)), address, sizes, address in listed)
            if kind == "source":
                sc["source"] = {"window": address, "shot": shot}
            else:
                key = {"window": "windows", "new window": "new_windows", "late window": "late_windows"}[kind]
                sc[key].append({"window": address, "shot": shot})

    if tool == "windows":
        for line in lines:
            m = WINDOW_LINE.fullmatch(line)
            if m:
                window_size(sizes, m.group(1), (float(m.group(2)), float(m.group(3))))
                listed.add(m.group(1))
            elif line.startswith("0x"):
                raise ValueError("Unparseable window summary: " + line)
        if not listed and not text.startswith(("error ", "no windows", "no window has ")):
            raise ValueError("Unparseable windows summary: " + text)
        other_shots(0)
    elif tool == "screenshot":
        m = SCREENSHOT.fullmatch(lines[0])
        if m:
            shot_id, width, height = m.group(1), int(m.group(2)), int(m.group(3))
            window_size(sizes, m.group(10), (float(m.group(11)), float(m.group(12))))
            if m.group(5):
                x, y, w, h = (float(m.group(n)) for n in range(6, 10))
                sc["shot"] = region_frame(shot_id, width, height, shots.get(m.group(5)), x, y, w, h)
            else:
                sc["shot"] = full_frame(shot_id, width, height, m.group(10), sizes, True)
        elif not text.startswith("error "):
            raise ValueError("Unparseable screenshot summary: " + lines[0])
        other_shots(1)
    elif tool == "act":
        parts = summary.split(" · ")
        m = DELIVERED.fullmatch(parts[0])
        if not m and args.get("actions") and not text.startswith("error "):
            raise ValueError("Unparseable act delivery summary: " + summary)
        sc["actions"] = {"done": int(m.group(1)) if m else 0}
        for part in parts:
            full, changes = re.fullmatch(FULL_SHOT, part), re.fullmatch(CHANGES_SHOT, part)
            if changes:
                base = shots.get(changes.group(2))
                shot = {"id": changes.group(1), "changes": {"base": changes.group(2), "regions": []}}
                if base:
                    shot.update(width=base["width"], height=base["height"], frame=base["frame"])
                else:
                    shot["unknown"] = f"a changes shot of {changes.group(2)}, whose frame is not known"
                for line in lines:
                    z = ZOOM_LINE.fullmatch(line)
                    if z and z.group(4) == shot["id"]:
                        shot["changes"]["regions"].append(dict(zip(
                            ("shot", "image_width", "image_height", "x", "y", "width", "height"),
                            [z.group(1)] + [int(v) for v in z.groups()[1:3] + z.groups()[4:]])))
                sc["shot"] = shot
            elif full:
                shot_id, width, height, notes = full.group(1), int(full.group(2)), int(full.group(3)), full.group(4)
                region = capture_region(args)
                if region and "shows the whole window instead" not in text:
                    sc["shot"] = region_frame(shot_id, width, height, shots.get(region.get("shot")), region["x"],
                                              region["y"], region["width"], region["height"])
                elif notes and any(n in notes for n in ("full shot: resized", "full shot: popups",
                                                        "full shot: the window is resizing", "stale: not yet redrawn")):
                    # The window has another size than the texts gave, or popups outside it.
                    sizes.pop(window, None)
                    sc["shot"] = {"id": shot_id, "width": width, "height": height, "unknown": notes}
                else:
                    sc["shot"] = full_frame(shot_id, width, height, window, sizes, False)
            elif re.match(r"shot s\d+ ", part):
                sc["shot"] = {"id": part.split()[1], "unknown": f"its text is in no form known here: {part}"}
            else:
                continue
            break
        other_shots(1)
    elif tool == "launch":
        m = re.search(rf"· {FULL_SHOT}(?: · |$)", summary)
        if m:
            sc["shot"] = {"id": m.group(1), "width": int(m.group(2)), "height": int(m.group(3)),
                          "unknown": "launch gives no size of its window"}
    return sc


def act_calls(tap):
    """The answered tools/call requests in order, with the shots their results define, and the shots whose frames
    their texts do not tell, with why."""
    pending, calls, shots, unknown, sizes = {}, [], {}, {}, {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            pending[msg.get("id")] = (entry["t"], msg["params"])
        elif entry["dir"] == "response" and msg.get("id") in pending:
            start, params = pending.pop(msg["id"])
            args = params.get("arguments") or {}
            result = msg.get("result") or {}
            text = "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
            # hyprmasq-mcp leaves the structured content out for Claude Code, which would pass it to the model in
            # place of the text, so its results tell the same in their text.
            sc = result.get("structuredContent") or text_content(params.get("name"), args, text, shots, sizes)
            if params.get("name") == "act":
                calls.append({"start": start, "end": entry["t"], "args": args, "result": sc,
                              "summary": text.split("\n", 1)[0], "shots": dict(shots), "unknown": dict(unknown)})
            shot_record(shots, unknown, sc.get("shot"), args.get("window"))
            # windows gives a shot of each window it matched, act one of each new window and of the window a closed
            # one came from, and each tool one of each late window.
            others = (sc.get("windows") or []) + (sc.get("new_windows") or []) + (sc.get("late_windows") or [])
            for other in others + ([sc["source"]] if sc.get("source") else []):
                shot_record(shots, unknown, other.get("shot"), other.get("window"))
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
