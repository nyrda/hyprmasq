"""What the attributions of the tasks read from a run's logs: the act calls in mcp.jsonl with the shots their
results define, and the presses of their actions, in window-local logical pixels, matched to the presses a page logged.

A task's attribute.py maps where the agent aimed, in shot pixels, to the window through the shot's frame, zooms
included, as hyprmasq maps them, and to the page's coordinates with the content's offset in the window, which Wayland
does not tell the page: calibrate() takes it from the presses that the page saw.

hyprmasq-mcp gives Claude Code results without structured content, so their shots and the actions act delivered come
from the text, and their frames from what made each shot (see text_content). A shot whose frame the text does not tell
is unknown, with why.

Cua Driver's runs have no act: cua_calls makes the same records of its pointer tools and of the steps of its
run_steps, their points in the pixels of the window's latest get_window_state screenshot (calls() picks the reader by
the run's tools). hypruse's and computer-use-linux's pointers act at points on the screen, as the user's own cursor:
hypruse_calls and cul_calls take a call's window to be the last one other than the user's that Hyprland focused
during the call, as a tool focuses a window before its pointer acts there, or else the one focused when the call
started, from the run's vm-events.log, and its frame to be where that window was on the screen.
"""

import json
import math
import os
import re
import statistics

from report import CUA_SCRIPT_READS, cua_step, server_backend

# The fields of act's array form of each action after its name, in order, as expandAction in
# mcp/internal/server/actions.go reads them.
ARRAY_FIELDS = {
    "click": ("x", "y", "button", "count", "modifiers"),
    "move": ("x", "y"),
    "drag": ("from", "to", "button", "modifiers"),
    "path": ("points", "curve", "button", "modifiers"),
    "scroll": ("x", "y", "dy", "unit", "dx"),
    "type": ("text",),
    "paste": ("text",),
    "key": ("keys", "repeat"),
    "pause": ("ms",),
}
# The click actions of Anthropic's computer use tool that act takes as clicks: their button and count (clickAliases).
CLICK_ALIASES = {"left_click": ("left", 1), "double_click": ("left", 2), "triple_click": ("left", 3),
                 "right_click": ("right", 1), "middle_click": ("middle", 1)}

# How far, in CSS pixels, a press may be from where the agent aimed and still count as where it aimed: the rounding of
# the page's coordinates and of the calibration.
TOLERANCE = 6


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


def action_object(raw):
    """An action of act as an object, {"action": kind, ...fields}, from any form act takes: an object, an array that
    starts with the action's name, or a point pair, which is a left click. A click alias becomes a click with its
    button and count. None for what act would refuse."""
    if isinstance(raw, list):
        if len(raw) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw):
            return {"action": "click", "x": raw[0], "y": raw[1]}
        if not raw or raw[0] not in ARRAY_FIELDS or len(raw) - 1 > len(ARRAY_FIELDS[raw[0]]):
            return None
        raw = {"action": raw[0], **dict(zip(ARRAY_FIELDS[raw[0]], raw[1:]))}
    if not isinstance(raw, dict):
        return None
    if raw.get("action") in CLICK_ALIASES:
        button, count = CLICK_ALIASES[raw["action"]]
        raw = {**raw, "action": "click", "button": button, "count": count}
    modifiers = raw.get("modifiers")
    if isinstance(modifiers, str):
        raw = {**raw, "modifiers": [modifiers]}
    return raw


def act_calls(tap):
    """The answered act calls in order, each with its window (the one it names or that of its shot), its actions as
    objects (see action_object), the shots that the results before it defined, and the shots whose frames their texts
    do not tell, with why."""
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
            # act and screenshot may name only the shot, whose window they act on.
            window = canonical(args.get("window")) or (shots.get(args.get("shot")) or {}).get("window")
            sc = result.get("structuredContent") or text_content(params.get("name"), {**args, "window": window}, text,
                                                                 shots, sizes)
            if params.get("name") == "act":
                actions = args.get("actions")
                calls.append({"start": start, "end": entry["t"], "args": args, "window": window,
                              "actions": [action_object(a) or {} for a in actions] if isinstance(actions, list) else None,
                              "result": sc, "summary": text.split("\n", 1)[0], "shots": dict(shots),
                              "unknown": dict(unknown)})
            shot_record(shots, unknown, sc.get("shot"), window)
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


def zoom(layouts, layout):
    """The page's zoom in a layout: its devicePixelRatio against the first layout's, at the zoom the page loaded at."""
    return (layout or {}).get("dpr", 1) / (layouts[0].get("dpr", 1) if layouts else 1)


def presses(call, window):
    """The presses of an act call's delivered actions, in order: (index, window-local point)."""
    out = []
    if call["window"] != window or call["actions"] is None:
        return out
    done = (call["result"].get("actions") or {}).get("done", 0)
    for i, action in enumerate(call["actions"][:done]):
        shot = action_shot(call, action)
        if not shot:
            continue
        if action.get("action") == "click":
            out += [(i, to_window(shot, (action["x"], action["y"])))] * int(action.get("count") or 1)
        elif action.get("action") == "drag":
            out.append((i, to_window(shot, action["from"])))
        elif action.get("action") == "path" and action.get("points") and action.get("button") != "none":
            # A path with button none only moves the pointer.
            out.append((i, to_window(shot, action["points"][0])))
    return out


def action_shot(call, action):
    """The shot an action's coordinates are in: the call's."""
    return call["shots"].get(call["args"].get("shot"))


def page_events(run_dir, name, offset):
    """A page's events from RUN-DIR/NAME, in the page's time order, each with its host time."""
    page = sorted(load_jsonl(os.path.join(run_dir, name)), key=lambda e: e["t"])
    for e in page:
        e["host"] = e["t"] / 1000 + offset
    return page


def calibrate(calls, page, window):
    """Pairs the presses of each act call on window with the pointerdowns that the page logged, and finds the
    content's offset in the window from them. Sets each call's presses, downs and matched (a pointerdown or None per
    press) and returns the offset, in logical pixels or None, and how it was found."""
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
    return content, calibration


def to_page(shot, point, content, z):
    """A point in a shot's pixels, in the page's CSS pixels at zoom z."""
    return [(v - c) / z for v, c in zip(to_window(shot, point), content)]


def calls(tap, run_dir=None):
    """The calls of a run that act on windows, by the run's server (report.server_backend): hyprmasq's act calls, Cua's
    (cua_calls), or hypruse's or computer-use-linux's (hypruse_calls, cul_calls), which need the run's directory for
    the focus and the windows' places."""
    backend = server_backend(tap)
    if backend == "cua":
        return cua_calls(tap)
    if backend == "hypruse":
        return hypruse_calls(tap, Screen(run_dir))
    if backend == "computer-use-linux":
        return cul_calls(tap, Screen(run_dir))
    return act_calls(tap)


# Cua Driver's pointer tools as act's actions: a click with its button and count, or a drag. Their points are in the
# pixels of the window's own get_window_state screenshot. mouse_button_down presses and releases in later calls: a
# press, which the attributions do not pair.
CUA_POINTER = {
    "click": ("click", None, None),
    "double_click": ("click", None, 2),
    "right_click": ("click", "right", None),
    "drag": ("drag", None, None),
    "mouse_button_down": ("press", None, None),
    "browser_pointer": ("browser_pointer", None, None),
}


# What Cua answers when a foreground action failed after its input may have reached the app, as a drag whose
# drag-and-drop the plugin then refused: the action counts as sent, and the page tells what it got.
UNKNOWN_DELIVERY = "final input delivery is unknown"


def cua_window(args, defaults=None):
    """The window a Cua call or step names, as hyprmasq writes addresses: Cua's window_id is Hyprland's address."""
    target = args.get("target") if isinstance(args.get("target"), dict) else {}
    for source in (args, target, defaults or {}):
        if isinstance(source.get("window_id"), int):
            return f"0x{source['window_id']:x}"
    return None


def cua_action(tool, args):
    """A step of Cua's as an action object, as action_object makes act's, with why its point cannot be mapped to the
    window's screenshot, or None: points in a zoom or on the desktop."""
    kind, button, count = CUA_POINTER.get(tool, (tool, None, None))
    if kind == "browser_pointer":
        # Its points are a tab's CSS pixels, or refs, with no window's screenshot to map them to: a drag that attribution
        # lists and does not place.
        gesture = args.get("action")
        action = {"action": "drag" if gesture == "drag" else f"browser_pointer {gesture}"}
        if gesture == "drag":
            action.update({"from": [args.get("x"), args.get("y")], "to": [args.get("to_x"), args.get("to_y")]})
        return action, "its points are in a browser tab"
    action = {"action": kind}
    if kind == "drag":
        action.update({"from": [args.get("from_x"), args.get("from_y")], "to": [args.get("to_x"), args.get("to_y")]})
    elif kind in ("click", "press"):
        if not isinstance(args.get("x"), (int, float)) or not isinstance(args.get("y"), (int, float)):
            # By element_token: Cua acts through the accessibility tree, with no point of the agent's.
            return {"action": f"{tool} by element"}, None
        action.update({"x": args["x"], "y": args["y"]})
    else:
        return action, None
    action["button"] = args.get("button") or button or "left"
    if count or args.get("count"):
        action["count"] = count or args.get("count")
    if args.get("modifier"):
        action["modifiers"] = args["modifier"]
    why = None
    if args.get("from_zoom"):
        why = "its points are in a zoom"
    elif args.get("scope") == "desktop" or args.get("coordinate_frame") == "desktop" or (
            (args.get("target") or {}).get("kind") == "desktop"):
        why = "its points are on the desktop"
    return action, why


def cua_frame(state):
    """The shot a get_window_state result defines, as to_window reads one: its screenshot's size and the window's
    logical size, at the window's origin."""
    bounds = state.get("window_bounds") or {}
    if not (state.get("screenshot_width") and state.get("screenshot_height") and bounds.get("width")):
        return None
    return {"frame": {"x": 0, "y": 0, "width": bounds["width"], "height": bounds["height"]},
            "width": state["screenshot_width"], "height": state["screenshot_height"]}


def cua_calls(tap):
    """The answered calls of Cua's tools that act, as act_calls gives act's: each with its window, its actions (one, or
    a step each of run_steps), how many of them ran (result.actions.done), counting one whose delivery Cua says is
    unknown, and the shot their points are in, the window's latest get_window_state screenshot before the call, under
    the call's own id in args.shot."""
    pending, out, frames = {}, [], {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            pending[msg.get("id")] = (entry["t"], msg["params"])
            continue
        if entry["dir"] != "response" or msg.get("id") not in pending:
            continue
        start, params = pending.pop(msg["id"])
        name, args = params.get("name"), params.get("arguments") or {}
        result = msg.get("result") or {}
        sc = result.get("structuredContent") or {}
        text = "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
        failed = bool(result.get("isError")) or "error" in msg
        if name == "run_steps":
            steps = [cua_step(step) for step in args.get("steps") or []]
            window = next((w for w in (cua_window(a, args) for _, a in steps) if w), None)
            failed_step = sc.get("failed_step")
            # The steps before the one that failed ran, and that one too where its input may have been delivered; a
            # batch refused as a whole ran none.
            if isinstance(failed_step, int):
                done = failed_step + (UNKNOWN_DELIVERY in text)
            else:
                done = 0 if failed and not sc.get("steps") else len(steps)
        elif name in CUA_POINTER:
            steps = [(name, args)]
            window = cua_window(args)
            done = 0 if failed and UNKNOWN_DELIVERY not in text else 1
        elif name == "run_script":
            # The script's own operations, which its result lists: no points of the agent's to map. A record of each,
            # marked as not placed, so that its actions are not left out.
            ops = [c for c in sc.get("calls") or [] if isinstance(c, dict) and c.get("op") not in CUA_SCRIPT_READS]
            if ops:
                shot_id = f"call-{msg['id']}"
                out.append({"start": start, "end": entry["t"], "args": {"shot": shot_id}, "window": None,
                            "actions": [{"action": f"run_script {c.get('op')}"} for c in ops],
                            "result": {"actions": {"done": sum(1 for c in ops if c.get("ok"))}},
                            "summary": text.split("\n", 1)[0], "shots": {},
                            "unknown": {shot_id: "its points are in a script"}})
            steps = None
        else:
            steps = None
        if steps is not None:
            shot_id = f"call-{msg['id']}"
            actions, why = [], None
            for tool, step_args in steps:
                action, unmapped = cua_action(tool, step_args)
                actions.append(action)
                why = why or unmapped
            frame = frames.get(window)
            unknown = {}
            if why:
                unknown[shot_id] = why
            elif not frame:
                unknown[shot_id] = "no get_window_state screenshot of the window before it"
            out.append({"start": start, "end": entry["t"], "args": {"shot": shot_id}, "window": window,
                        "actions": actions, "result": {"actions": {"done": done}},
                        "summary": text.split("\n", 1)[0],
                        "shots": {shot_id: frame} if frame and not why else {}, "unknown": unknown})
        # The frames that later calls' points are in: of a read, and of a batch's observation.
        for state in (sc, sc.get("observation") if isinstance(sc.get("observation"), dict) else {}):
            frame = cua_frame(state)
            if frame and isinstance(state.get("window_id"), int):
                frames[f"0x{state['window_id']:x}"] = frame
    return out


class Screen:
    """What a run's directory tells of the screen, for the tools whose points are on it: which window Hyprland had
    focused when (vm-events.log, in host time), where each window was (ready.json's clients, until a result tells
    otherwise) and the monitor's scale (the results directory's source.json)."""

    def __init__(self, run_dir):
        ready = load_json(os.path.join(run_dir, "ready.json"), {}) if run_dir else {}
        self.places = {c["address"]: (c["at"], c["size"]) for c in ready.get("clients") or []}
        self.user = (ready.get("user") or {}).get("window")
        self.focus = [(-math.inf, self.user)]
        offset = ready["host_time"] - ready["vm_time"] if "host_time" in ready else 0
        events = os.path.join(run_dir, "vm-events.log") if run_dir else ""
        if os.path.exists(events):
            with open(events) as f:
                for line in f:
                    t, _, event = line.rstrip("\n").partition(" ")
                    name, _, data = event.partition(">>")
                    if name == "activewindowv2":
                        self.focus.append((float(t) + offset, canonical("0x" + data.removeprefix("0x")) if data
                                           else None))
        source = load_json(os.path.join(os.path.dirname(os.path.normpath(run_dir)), "source.json"), {}) if run_dir \
            else {}
        m = re.search(r"@([0-9.]+)$", source.get("display") or "")
        self.scale = float(m.group(1)) if m else 1.0

    def focused(self, t):
        """The window that had the keyboard focus at host time t."""
        return [w for at, w in self.focus if at <= t][-1]

    def acted_on(self, start, end):
        """The window that a call from start to end acted on: the last window other than the user's that took the
        focus during it, as a tool focuses the window before its pointer acts, even where it gives the focus back
        afterwards, or else the window focused when it started."""
        taken = [w for at, w in self.focus if start < at <= end and w and w != self.user]
        return taken[-1] if taken else self.focused(start)

    def frame(self, window, scale=1.0):
        """The shot that points on the screen define for window: at scale screen pixels a logical pixel, the window
        where it is."""
        if window not in self.places:
            return None
        (x, y), (w, h) = self.places[window]
        return {"frame": {"x": -x, "y": -y, "width": w, "height": h}, "width": w * scale, "height": h * scale}


def screen_record(start, end, window, actions, done, summary, screen, why=None, scale=1.0, shot=None):
    """A call of a tool whose points are on the screen, as act_calls makes act's: its shot is the screen as its window
    was placed then (Screen.frame), or the shot given, under the call's own id."""
    shot_id = f"call-{start}"
    frame = shot if shot is not None else screen.frame(window, scale)
    unknown = {}
    if why:
        unknown[shot_id] = why
    elif not frame:
        unknown[shot_id] = "where its window was is not known"
    return {"start": start, "end": end, "args": {"shot": shot_id}, "window": window, "actions": actions,
            "result": {"actions": {"done": done}}, "summary": summary,
            "shots": {shot_id: frame} if frame and not why else {}, "unknown": unknown}


def tool_results(tap):
    """The answered tools/call requests of an MCP log: (start, end, tool, arguments, result, its text)."""
    pending = {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            pending[msg.get("id")] = (entry["t"], msg["params"])
        elif entry["dir"] == "response" and msg.get("id") in pending:
            start, params = pending.pop(msg["id"])
            result = msg.get("result") or {"isError": True}
            text = "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")
            yield start, entry["t"], params.get("name"), params.get("arguments") or {}, result, text


def hypruse_pointer(args):
    """A hypruse pointer call as an action object, as action_object makes act's: a click or a drag at global logical
    points, or a move or scroll, which press nothing."""
    action = args.get("action")
    if action == "click":
        if not isinstance(args.get("x"), (int, float)) or not isinstance(args.get("y"), (int, float)):
            return {"action": "click at the cursor"}
        out = {"action": "click", "x": args["x"], "y": args["y"], "button": args.get("button") or "left"}
        if args.get("double"):
            out["count"] = 2
        return out
    if action == "drag":
        return {"action": "drag", "from": [args.get("x"), args.get("y")], "to": [args.get("to_x"), args.get("to_y")],
                "button": args.get("button") or "left"}
    return {"action": action or "pointer"}


def hypruse_calls(tap, screen):
    """hypruse's calls that act, as act_calls gives act's: its pointer, and the steps of its sequence, each step's
    other tools as actions that press nothing (click_ui presses through the accessibility tree). The pointer's points
    are global logical pixels, on the window focused when the call started (Screen); its desktop results tell where
    the windows are."""
    out = []
    for start, end, name, args, result, text in tool_results(tap):
        failed = bool(result.get("isError"))
        if name == "pointer":
            steps, done = [("pointer", args)], 0 if failed else 1
        elif name == "sequence":
            steps = [(step.get("op"), step) for step in args.get("steps") or [] if isinstance(step, dict)]
            # Each step that ran has a line [N] of the result, from 0; the first that raised did not deliver.
            lines = re.findall(r"^\[(\d+)\] .*", text, re.M)
            errors = [int(n) for n in re.findall(r"^\[(\d+)\] \S+: ERROR", text, re.M)]
            done = min(errors) if errors else len(lines)
        else:
            steps = None
        if steps is not None:
            actions = [hypruse_pointer(a) if op == "pointer" else {"action": f"{op} by element" if op == "click_ui"
                                                                    else op or "step"} for op, a in steps]
            out.append(screen_record(start, end, screen.acted_on(start, end), actions, done, text.split("\n", 1)[0],
                                     screen))
        # Where the windows are: desktop's list, also after an action's then='desktop'.
        sc = result.get("structuredContent") or {}
        for w in sc.get("windows") or [] if isinstance(sc, dict) else []:
            if isinstance(w, dict) and w.get("address") and w.get("at") and w.get("size"):
                screen.places[canonical(w["address"])] = (w["at"], w["size"])
    return out


def cul_window(args):
    """The window a computer-use-linux call names by its window_id, which is Hyprland's address."""
    return f"0x{args['window_id']:x}" if isinstance(args.get("window_id"), int) else None


def cul_calls(tap, screen):
    """computer-use-linux's calls that act with its pointer, as act_calls gives act's: click and drag. Their points
    are the screen's capture pixels (coordinate_space capture), on the window focused when the call started (Screen),
    or with relative=true the capture pixels of the window's own crop. A click by element_index or a selector presses
    through the accessibility tree, at no point of the agent's. Its window lists tell where the windows are."""
    out = []
    for start, end, name, args, result, text in tool_results(tap):
        try:
            sc = json.loads(text) if text.startswith("{") else {}
        except ValueError:
            sc = {}
        failed = bool(result.get("isError")) or sc.get("ok") is False
        if name in ("click", "drag"):
            window, shot, why = cul_window(args) or screen.acted_on(start, end), None, None
            if name == "drag":
                action = {"action": "drag", "from": [args.get("start_x"), args.get("start_y")],
                          "to": [args.get("end_x"), args.get("end_y")], "button": "left"}
                window = screen.acted_on(start, end)
            elif isinstance(args.get("x"), (int, float)) and isinstance(args.get("y"), (int, float)):
                action = {"action": "click", "x": args["x"], "y": args["y"], "button": args.get("button") or "left"}
                if args.get("click_count"):
                    action["count"] = args["click_count"]
                if args.get("coordinate_space") == "atspi":
                    why = "its points are AT-SPI screen coordinates"
                elif args.get("relative") and window in screen.places:
                    w, h = screen.places[window][1]
                    shot = {"frame": {"x": 0, "y": 0, "width": w, "height": h}, "width": w * screen.scale,
                            "height": h * screen.scale}
            else:
                action = {"action": "click by element"}
            out.append(screen_record(start, end, window, [action], 0 if failed else 1,
                                     (sc.get("message") or text).split("\n", 1)[0][:300], screen, why,
                                     screen.scale, shot))
        for w in (sc.get("windows") or []) + [sc.get("focused_window"), sc.get("window_context")]:
            bounds = (w or {}).get("bounds") if isinstance(w, dict) else None
            if bounds and isinstance(w.get("window_id"), int):
                screen.places[f"0x{w['window_id']:x}"] = ([bounds["x"] / screen.scale, bounds["y"] / screen.scale],
                                                          [bounds["width"] / screen.scale,
                                                           bounds["height"] / screen.scale])
    return out
