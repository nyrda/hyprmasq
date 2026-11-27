#!/usr/bin/env python3
"""Replays the MCP messages of a recorded run against the server of another run, with no agent and no model.

    replay.py RECORDING [--gap none|recorded[:CAP]|SECONDS] -- SERVER [ARGS]...

RECORDING is a run directory with its mcp.jsonl, or that file. SERVER is the MCP server to replay it to, as an agent
would start it: run.py gives mcp_tap.py, which logs the replay to the new run's mcp.jsonl as it logs an agent's calls.
run.py runs this as the agent of the harness `replay`, see "Replaying a recorded run" in bench/README.md.

The client's messages go out as they were recorded, the handshake and the `_meta` of each request included, so that the
server answers the client it answered then: it gives Claude Code its results as text alone. What a recorded call names
of an earlier result is replaced by what the replay's own result gave in its place:

- a window's address, which is another in every session of Hyprland, by the window of the same class and title;
- a shot's id, which counts every shot the session got, by the shot with the same part in the replay's result: the
  result's shot, the one before a scroll, or the one of a window that the result names;
- coordinates in the pixels of a zoom into a change, by the same point of the window in the pixels of the shot that
  the zoom is a part of, since a replay need not get the same zooms: see Mapping.shot.

A replay that goes another way than the recording stops, and says where and why: a result that is an error where the
recording had none, input that was not delivered as far, a window that closed in one and not the other, a page scroll
that ended at another view, and a window, a shot or a point that the replay has nothing for. Such a replay is no sample of the tool's time, and no failure of the
task's checker either.

Each line of the output is an event, as an agent's: the start, each call, and the end with what was sent and whether
the replay diverged, which report.py reads into the run's summary.json as `replay`.
"""

import copy
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time

import actlog

# The requests that start a client's session with a server: MCP's handshake, or the discovery of the protocol version
# that needs none (2026-07-28), which Claude Code sends. A recording holds one for each server its agent started: one,
# and another when the agent went on after the user's reply (run.py --user-reply), with a server that knew no shot.
SESSION_START = ("initialize", "server/discover")
# How long a call may take, as run.py's TOOL_TIMEOUT gives every harness.
CALL_TIMEOUT = 600
# The gap between calls that run.py takes without --replay-gap: see "Replaying a recorded run" in bench/README.md.
DEFAULT_GAP = "recorded:5"

# A window as windows lists it (windowLine in mcp/internal/server/windows.go), as a result names one that opened or that
# a closed one came from (windowsText and sourceText in summary.go), and as launch names its own (launch.go). Go's %q
# writes the titles.
LISTED = re.compile(r'(0x[0-9a-f]+) · (.*?) "(.*)" · workspace .+? \((?:hidden|visible)\) · ')
NAMED = re.compile(r'(0x[0-9a-f]+) \((.*?) "((?:[^"\\]|\\.)*)"(?:, launched|, same pid)?\) on workspace ')
LAUNCHED = re.compile(r'launched (0x[0-9a-f]+) (\S+) "((?:[^"\\]|\\.)*)"')
ERROR = re.compile(r"error (\w+):")
# How a page scroll ended (pageScrollOut.text in page.go): by the view it moved, or the notches it stopped after and why.
PAGE = re.compile(r"^Page scroll (?:moved the view (\d+) logical px|stopped after (\d+) wheel notches \((\w+)\))", re.M)
SPLIT = ("before the scroll: ", "after the scroll: ")


class Divergence(Exception):
    """The replay cannot go on as the recording did: kind names why, as in KINDS."""

    def __init__(self, kind, detail):
        super().__init__(f"{kind}: {detail}")
        self.kind, self.detail = kind, detail


# Why a replay stops, for the report.
KINDS = {
    "error": "a result is an error where the recording's was none, or another error",
    "no_error": "a result is no error where the recording's was one",
    "delivery": "input was delivered up to another action than in the recording",
    "window_closed": "the window closed in one of the two and not in the other",
    "scroll": "a page scroll ended at another view than in the recording",
    "window": "a call names a window that the replay's results gave no window for",
    "shot": "a call names a shot that the replay's results gave no shot for",
    "coordinates": "a point could not be mapped to the replay's shot",
    "connection": "the server closed the connection, or did not answer in time",
}


def parse_gap(text):
    """A gap setting as (mode, seconds): none, the recorded gap up to a cap in seconds or without one, or a fixed time."""
    if text == "none":
        return "none", 0.0
    mode, sep, cap = text.partition(":")
    try:
        if mode == "recorded":
            seconds = float(cap) if sep else math.inf
        else:
            mode, seconds = "fixed", float(text)
    except ValueError:
        seconds = -1
    if not seconds >= 0 or mode == "recorded" and sep and not cap:
        raise ValueError(f"{text!r} is no gap: none, recorded, recorded:CAP or a number of seconds")
    return mode, seconds


def gap_seconds(gap, recorded, call=True):
    """How long the replay waits before a message that the recording's client sent `recorded` seconds after the message
    before it. A fixed time is the gap before a tool call: the client's other messages, its handshake, keep their own
    gap up to it."""
    mode, seconds = gap
    if mode == "none":
        return 0.0
    if mode == "fixed" and call:
        return seconds
    return max(0.0, min(recorded, seconds))


def zoom_frame(shot, box, size):
    """The frame of a zoom into a change, in its window's logical pixels, from the box of its shot's pixels that the
    result states for it, and whether the box gives it to the pixel.

    The server cuts a zoom to whole logical pixels and states the box of the shot's pixels that covers them (regionInfo
    in mcp/internal/server/changes.go): the pixel that holds the zoom's start, and the one that holds its end. A shot
    that was shrunk has fewer pixels than its window, so a box edge can stand for two logical pixels. The zoom's image
    has the same scale along both axes, which picks the edges that fit it. Where two starts still fit, the frame is
    put between them, half a logical pixel from either."""
    frame = shot["frame"]
    scales = (shot["width"] / frame["width"], shot["height"] / frame["height"])
    spans = []
    for axis, (key, extent) in enumerate((("x", "width"), ("y", "height"))):
        origin, scale, lo, hi = frame[key], scales[axis], box[axis], box[axis] + box[axis + 2]
        limit = (shot["width"], shot["height"])[axis]
        first, last = math.floor(origin + 1e-9), math.ceil(origin + frame[extent] - 1e-9)
        starts = [v for v in range(math.floor(origin + lo / scale) - 1, math.ceil(origin + (lo + 1) / scale) + 2)
                  if first <= v <= last and max(0, math.floor((v - origin) * scale + 1e-6)) == lo]
        ends = [v for v in range(math.floor(origin + (hi - 1) / scale) - 1, math.ceil(origin + hi / scale) + 2)
                if first <= v <= last and min(limit, math.ceil((v - origin) * scale - 1e-6)) == hi]
        spans.append([(a, b) for a in starts for b in ends if b > a])
    if not spans[0] or not spans[1]:
        # No whole logical pixels make this box, as at a fractional scale: the box itself, to a pixel of the shot.
        return {"x": frame["x"] + box[0] / scales[0], "y": frame["y"] + box[1] / scales[1],
                "width": box[2] / scales[0], "height": box[3] / scales[1]}, False

    def ratio(span, pixels):
        return pixels / (span[1] - span[0])

    best = min(abs(ratio(a, size[0]) - ratio(b, size[1])) for a in spans[0] for b in spans[1])
    fits = [[], []]
    for a in spans[0]:
        for b in spans[1]:
            if abs(ratio(a, size[0]) - ratio(b, size[1])) <= best + 1e-9:
                fits[0].append(a)
                fits[1].append(b)
    out, exact = {}, True
    for axis, (key, extent) in enumerate((("x", "width"), ("y", "height"))):
        found = sorted(set(fits[axis]))
        exact = exact and len(found) == 1
        out[key] = sum(a for a, _ in found) / len(found)
        out[extent] = sum(b - a for a, b in found) / len(found)
    return out, exact


class Side:
    """What the results of one side, the recording or the replay, told of its shots: each one's frame in its window,
    where the result gives it (actlog's shots), and the rest by its id."""

    def __init__(self):
        self.shots, self.unknown, self.sizes = {}, {}, {}
        # A shot's window and image size, and for a zoom into a change the shot it is a part of and its box there.
        self.window, self.size, self.parent, self.box = {}, {}, {}, {}
        # Whether a zoom's box gives its frame to the pixel (zoom_frame).
        self.exact = {}

    def record(self, shot, window):
        """Remembers a result's shot and its zooms, and returns its id."""
        if not shot or "id" not in shot:
            return None
        actlog.shot_record(self.shots, self.unknown, shot, window)
        self.window[shot["id"]] = window
        changes = shot.get("changes") or {}
        # A changes shot has the pixels of the shot it was compared with.
        size = (shot["width"], shot["height"]) if "width" in shot and "height" in shot else self.size.get(changes.get("base"))
        if size:
            self.size[shot["id"]] = size
        for region in changes.get("regions") or []:
            zoom = region["shot"]
            self.window[zoom], self.parent[zoom] = window, shot["id"]
            self.size[zoom] = (region["image_width"], region["image_height"])
            self.box[zoom] = tuple(region[k] for k in ("x", "y", "width", "height"))
            # actlog's frame of a zoom is good to a pixel or two, enough for its attributions: here it is zoom_frame's.
            self.shots.pop(zoom, None)
            if shot["id"] in self.shots:
                frame, self.exact[zoom] = zoom_frame(self.shots[shot["id"]], self.box[zoom], self.size[zoom])
                self.shots[zoom] = {"window": window, "frame": frame, "width": region["image_width"],
                                    "height": region["image_height"]}
        return shot["id"]

    def new_session(self):
        """A new server knows none of the shots: their ids start again."""
        self.__init__()


def result_text(result):
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")


def text_structure(tool, args, text, shots, sizes):
    """A result without structured content in the shape of one, as actlog.text_content reads it, and with the shots
    of an act that came in two parts, before and after its scroll, which have a line each after the summary."""
    sc = actlog.text_content(tool, args, text, shots, sizes)
    lines = text.split("\n")
    if tool != "act" or not any(line.startswith(SPLIT[1]) for line in lines):
        return sc
    head = next((i for i, line in enumerate(lines) if not line.startswith("error ")), 0)
    parts = {}
    for prefix in SPLIT:
        line = next((line for line in lines if line.startswith(prefix)), None)
        if line is None:
            continue
        # The line's wait and shot as if they were the summary's, with the lines of the zooms after it.
        merged = lines[:head] + [lines[head] + " · " + line[len(prefix):]] + lines[head + 1:]
        part = actlog.text_content(tool, args, "\n".join(merged), dict(shots), sizes).get("shot")
        parts[prefix] = part
        if prefix == SPLIT[0] and part:
            # The shot after the scroll may be compared with the one before it.
            unknown = {}
            shots = dict(shots)
            actlog.shot_record(shots, unknown, part, args.get("window"))
    if parts.get(SPLIT[0]):
        sc["before_scroll"] = {"shot": parts[SPLIT[0]]}
    if parts.get(SPLIT[1]):
        sc["shot"] = parts[SPLIT[1]]
    return sc


def text_windows(tool, text):
    """The windows a result's text names with their class and title, in order, as (address, class, title)."""
    found = []
    if tool == "windows":
        found = [m.groups() for m in map(LISTED.match, text.split("\n")) if m]
    found += [m.groups() for m in NAMED.finditer(text)]
    if tool == "launch":
        found += [m.groups() for m in LAUNCHED.finditer(text.split("\n", 1)[0])]
    return found


def read_result(side, tool, args, message):
    """What the response to a tools/call tells, from its structured content or else from its text, and remembers its
    shots in side:

    - error: the error's code, None for a result that is none;
    - delivered: (done, total) of an act's actions, or None;
    - closed: whether the window closed;
    - windows: the windows it names with their class and title, as (address, class, title), in order;
    - shots: the id of the result's shot as main, of the shot before a scroll as before, and of each shot of another
      window under that window's address;
    - kind: of the main shot, changes or full, and zooms: how many zooms it has;
    - timeout: whether its wait ran into its deadline;
    - scroll: how a page scroll ended, the logical pixels it moved the view or the notches and the reason it stopped
      at, or None.
    """
    out = {"error": None, "delivered": None, "closed": False, "windows": [], "shots": {}, "kind": None, "zooms": 0,
           "timeout": False, "scroll": None, "summary": ""}
    if "result" not in message:
        error = message.get("error") or {}
        out["error"] = f"rpc {error.get('code')}"
        out["summary"] = str(error.get("message"))[:300]
        return out
    result = message["result"] or {}
    text = result_text(result)
    lines = text.split("\n")
    summary = next((line for line in lines if not line.startswith("error ")), "")
    out["summary"] = lines[0][:300]
    window = actlog.canonical(args.get("window")) or side.window.get(args.get("shot"))
    sc = result.get("structuredContent")
    if sc:
        code = (sc.get("error") or {}).get("code")
        actions = sc.get("actions") or {}
        if actions.get("total"):
            out["delivered"] = (actions.get("done", 0), actions["total"])
        named = (sc.get("windows") or []) + (sc.get("new_windows") or []) + (sc.get("late_windows") or [])
        named += [w for w in (sc.get("source"), sc.get("window")) if isinstance(w, dict)]
        out["windows"] = [(w.get("window"), w.get("class"), json.dumps(w.get("title") or "", ensure_ascii=False)[1:-1]) for w in named
                          if w.get("window")]
        out["closed"] = bool(sc.get("window_closed"))
        if tool == "launch" and isinstance(sc.get("window"), dict):
            window = sc["window"].get("window")
    else:
        try:
            sc = text_structure(tool, {**args, "window": window}, text, side.shots, side.sizes)
        except ValueError as e:
            # A text in no form known here: what it defined is missed when a later call names it.
            sc, out["unread"] = {}, str(e)[:300]
        found = ERROR.match(text)
        code = found.group(1) if found else None
        delivered = re.match(r"(\d+)/(\d+) delivered", summary)
        if tool == "act" and delivered:
            out["delivered"] = (int(delivered.group(1)), int(delivered.group(2)))
        out["windows"] = text_windows(tool, text)
        out["closed"] = tool == "act" and bool(re.search(r"(?:^| · )window closed(?: · |$)", summary))
        if tool == "launch" and out["windows"]:
            window = out["windows"][-1][0]
    out["windows"] = [(actlog.canonical(address), cls, title) for address, cls, title in out["windows"]]
    # The text says it for every client.
    page = PAGE.search(text) if tool == "act" else None
    if page:
        out["scroll"] = f"moved the view {page.group(1)} px" if page.group(1) else (
            f"stopped after {page.group(2)} notches ({page.group(3)})")
    out["error"] = code or ("tool" if result.get("isError") else None)
    # An error that sent nothing says so in its text only when it has a shot: the error tells it either way.
    if out["error"] and out["delivered"] and not out["delivered"][0]:
        out["delivered"] = None
    out["timeout"] = "timeout after " in summary or any(line.startswith(SPLIT) and "timeout after " in line for line in lines)
    main = sc.get("shot")
    if main and "id" in main:
        out["shots"]["main"] = side.record(main, window)
        out["kind"] = "changes" if main.get("changes") is not None else "full"
        out["zooms"] = len((main.get("changes") or {}).get("regions") or [])
    before = (sc.get("before_scroll") or {}).get("shot")
    if before and "id" in before:
        out["shots"]["before"] = side.record(before, window)
    others = (sc.get("windows") or []) + (sc.get("new_windows") or []) + (sc.get("late_windows") or [])
    for other in others + ([sc["source"]] if isinstance(sc.get("source"), dict) else []):
        shot = other.get("shot")
        if shot and "id" in shot and other.get("window"):
            address = actlog.canonical(other["window"])
            out["shots"][address] = side.record(shot, address)
    return out


def same_frame(a, b):
    """Whether two shots show the same box of their windows in the same pixels."""
    return (a["width"], a["height"]) == (b["width"], b["height"]) and all(
        abs(a["frame"][k] - b["frame"][k]) < 1e-6 for k in ("x", "y", "width", "height"))


def to_shot(shot, point):
    """A point in window-local logical pixels, in a shot's pixels: actlog.to_window's inverse."""
    f = shot["frame"]
    return ((point[0] - f["x"]) * shot["width"] / f["width"], (point[1] - f["y"]) * shot["height"] / f["height"])


def carry(a, b, x, y):
    """A point in shot a's pixels as the same point of the window in shot b's. A point outside a, which act refused
    with out_of_bounds, stays outside b, as it may lie within a shot that shows more of the window."""
    if not (0 <= x < a["width"] and 0 <= y < a["height"]):
        return tidy(b["width"] + abs(x)), tidy(b["height"] + abs(y))
    return tuple(tidy(v) for v in to_shot(b, actlog.to_window(a, (x, y))))


def tidy(value):
    """A coordinate to send: the server rounds the window's point to thousandths of a logical pixel."""
    value = round(value, 4)
    return int(value) if value == int(value) else value


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_point(value):
    return isinstance(value, (list, tuple)) and len(value) == 2 and all(is_number(v) for v in value)


def map_action(raw, point):
    """An action of act with its coordinates through point, in the form it has: an object, an array that starts with
    the action's name, or one that starts with a click's point. An action in no form that act takes stays as it is,
    for the server to refuse as it refused it in the recording."""
    def pair(value):
        return list(point(value[0], value[1])) if is_point(value) else value

    def points(value):
        return [pair(p) for p in value] if isinstance(value, list) else value

    if isinstance(raw, list):
        if len(raw) >= 2 and is_number(raw[0]) and is_number(raw[1]):
            return [*point(raw[0], raw[1]), *raw[2:]]
        if not raw or not isinstance(raw[0], str):
            return raw
        name = "click" if raw[0] in actlog.CLICK_ALIASES else raw[0]
        out = list(raw)
        if name in ("click", "move", "scroll") and len(raw) >= 3 and is_number(raw[1]) and is_number(raw[2]):
            out[1:3] = point(raw[1], raw[2])
        elif name == "drag":
            out[1:3] = [pair(v) for v in raw[1:3]]
        elif name == "path" and len(raw) >= 2:
            out[1] = points(raw[1])
        return out
    if not isinstance(raw, dict):
        return raw
    out = dict(raw)
    if is_number(raw.get("x")) and is_number(raw.get("y")):
        out["x"], out["y"] = point(raw["x"], raw["y"])
    for key in ("from", "to"):
        if key in raw:
            out[key] = pair(raw[key])
    if "points" in raw:
        out["points"] = points(raw["points"])
    return out


class Mapping:
    """The windows and shots of the replay that stand for the recording's, from the results of the calls so far."""

    def __init__(self):
        self.recorded, self.replayed = Side(), Side()
        self.windows, self.shots, self.zooms = {}, {}, {}
        # How the calls' arguments were changed, counted for the report.
        self.counts = {"same_zoom": 0, "rebased": 0, "rebased_within_half_a_pixel": 0, "moved": 0, "assumed": 0}

    def new_session(self):
        """The windows stay, and the new server's shots start again."""
        self.recorded.new_session()
        self.replayed.new_session()
        self.shots, self.zooms = {}, {}

    def learn(self, recorded, replayed):
        """Pairs what the recording's result and the replay's result of the same call gave. Windows pair by class and
        title, in order, and what is left by class alone, as a title can hold a time. An address is another window's
        once its window has closed, so the latest pair holds. Shots pair by their part in the result."""
        taken, left = set(), list(dict.fromkeys(recorded["windows"]))
        for key in (lambda w: (w[1], w[2]), lambda w: w[1]):
            rest = []
            for window in left:
                match = next((w for w in replayed["windows"] if w[0] not in taken and key(w) == key(window)), None)
                if match:
                    self.windows[window[0]] = match[0]
                    taken.add(match[0])
                else:
                    rest.append(window)
            left = rest
        for part, shot in recorded["shots"].items():
            other = replayed["shots"].get(part if part in ("main", "before") else self.windows.get(part))
            if other:
                self.shots[shot] = other
                self.pair_zooms(shot, other)
        return left

    def pair_zooms(self, shot, other):
        """The zooms of two shots that are the same part of the same pixels: only they stand for each other."""
        mine = {box: zoom for zoom, box in self.replayed.box.items() if self.replayed.parent[zoom] == other}
        for zoom, box in self.recorded.box.items():
            if self.recorded.parent[zoom] != shot or box not in mine:
                continue
            if self.recorded.size[zoom] == self.replayed.size[mine[box]]:
                self.zooms[zoom] = mine[box]

    def window(self, address):
        """The replay's window for one of the recording's."""
        found = self.windows.get(actlog.canonical(address))
        if found is None:
            raise Divergence("window", f"the replay's results named no window for the recording's {address}")
        return found

    def shot(self, shot):
        """The replay's shot for one of the recording's, and how a point in the recording's pixels is one in its pixels,
        or None where the pixels are the same.

        A zoom into a change stands for a box of the changes shot it came with, and a replay need not get the same
        zooms: a caret that blinks, or a change that came a frame later, makes other boxes. Where the replay's result
        has a zoom of the same box and size, that one stands for it, with its pixels as they are. Otherwise its points
        go to the replay's shot for that changes shot, through the window's logical pixels: to the same point where
        the box gives the zoom's frame, and within half a logical pixel where it does not (zoom_frame). act compares
        with the same shot either way, as a zoom's changes are compared with the shot it is a part of (changesBase in
        mcp/internal/server/changes.go), and checks its targets against the same image (cutZoom). A zoom that the
        agent asked for with screenshot is a result's own shot and stands for the replay's.

        Results without structured content, Claude Code's, give no frames. actlog reads them from what made each shot,
        and where it cannot, as for a shot of a new window, whose size no text gives, the pixels count as the same when
        the two images have the same size."""
        base = self.recorded.parent.get(shot)
        if shot in self.zooms:
            self.counts["same_zoom"] += 1
            return self.zooms[shot], None
        other = self.shots.get(base or shot)
        if other is None:
            raise Divergence("shot", f"the replay's results gave no shot for the recording's {base or shot}"
                             + (f", which {shot} zooms into" if base else ""))
        a, b = self.recorded.shots.get(shot), self.replayed.shots.get(other)
        if a and b:
            if same_frame(a, b):
                return other, None
            self.counts["rebased" if base else "moved"] += 1
            if base and not self.recorded.exact.get(shot):
                self.counts["rebased_within_half_a_pixel"] += 1
            return other, lambda x, y: carry(a, b, x, y)
        if not base and self.recorded.size.get(shot) and self.recorded.size.get(shot) == self.replayed.size.get(other):
            self.counts["assumed"] += 1
            return other, None
        raise Divergence("coordinates", f"the frame of the recording's {shot} or of the replay's {other} is not known, "
                         "and their images differ: " + (self.recorded.unknown.get(shot) or self.replayed.unknown.get(other)
                                                        or "a zoom without the same zoom in the replay"))

    def region(self, region):
        """A rectangle in a shot's pixels, of act's capture or of screenshot's region."""
        if not isinstance(region, dict) or not isinstance(region.get("shot"), str):
            return region
        shot, point = self.shot(region["shot"])
        out = {**region, "shot": shot}
        if point and all(is_number(region.get(k)) for k in ("x", "y", "width", "height")):
            x0, y0 = point(region["x"], region["y"])
            x1, y1 = point(region["x"] + region["width"], region["y"] + region["height"])
            out.update(x=x0, y=y0, width=tidy(x1 - x0), height=tidy(y1 - y0))
        return out

    def arguments(self, tool, args):
        """A recorded call's arguments for the replay's server. Raises Divergence for what the replay has nothing for."""
        args = copy.deepcopy(args) if isinstance(args, dict) else args
        if not isinstance(args, dict):
            return args
        point = None
        if isinstance(args.get("shot"), str) and tool in ("act", "screenshot"):
            args["shot"], point = self.shot(args["shot"])
        if isinstance(args.get("window"), str):
            args["window"] = self.window(args["window"])
        if tool == "act":
            if point and isinstance(args.get("actions"), list):
                args["actions"] = [map_action(a, point) for a in args["actions"]]
            page = args.get("next_page")
            if point and isinstance(page, dict) and is_number(page.get("x")) and is_number(page.get("y")):
                page["x"], page["y"] = point(page["x"], page["y"])
            capture = args.get("capture")
            if isinstance(capture, dict):
                if isinstance(capture.get("region"), dict):
                    capture["region"] = self.region(capture["region"])
                else:
                    args["capture"] = self.region(capture)
        elif tool == "screenshot" and isinstance(args.get("region"), dict):
            args["region"] = self.region(args["region"])
        return args


def compare(recorded, replayed):
    """Why the replay's result of a call says that the replay went another way than the recording, as a Divergence, or
    None, and what else differs between them and need not matter, as a list."""
    notes = []
    if recorded["kind"] and replayed["kind"] and recorded["kind"] != replayed["kind"]:
        notes.append(f"{replayed['kind']} shot for a {recorded['kind']} shot")
    elif recorded["zooms"] != replayed["zooms"]:
        notes.append(f"{replayed['zooms']} zooms for {recorded['zooms']}")
    if recorded["timeout"] != replayed["timeout"]:
        notes.append("a wait timed out" if replayed["timeout"] else "no wait timed out")
    if len(recorded["windows"]) != len(replayed["windows"]):
        notes.append(f"{len(replayed['windows'])} windows named for {len(recorded['windows'])}")
    if set(recorded["shots"]) - {"main", "before"} and len(recorded["shots"]) != len(replayed["shots"]):
        notes.append(f"{len(replayed['shots'])} shots for {len(recorded['shots'])}")
    said = f"the replay: {replayed['summary']!r}, the recording: {recorded['summary']!r}"
    if recorded["error"] != replayed["error"]:
        kind = "no_error" if replayed["error"] is None else "error"
        return Divergence(kind, f"{replayed['error'] or 'no error'} for {recorded['error'] or 'no error'}; {said}"), notes
    if recorded["delivered"] != replayed["delivered"]:
        def text(d):
            return f"{d[0]}/{d[1]} delivered" if d else "no input"
        return Divergence("delivery", f"{text(replayed['delivered'])} for {text(recorded['delivered'])}; {said}"), notes
    if recorded["closed"] != replayed["closed"]:
        return Divergence("window_closed", ("the window closed only in the replay" if replayed["closed"] else
                                            "the window closed only in the recording") + f"; {said}"), notes
    if recorded["scroll"] != replayed["scroll"]:
        # The next calls' points are of the view that the recording's scroll ended at.
        return Divergence("scroll", f"the page scroll {replayed['scroll'] or 'did not run'}, and in the recording it "
                          f"{recorded['scroll'] or 'did not run'}"), notes
    return None, notes


class Connection:
    """A server's stdio, as an agent's MCP client has it: one JSON message a line."""

    def __init__(self, command, env=None):
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.inbox, self.answers = queue.Queue(), {}
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            # A response has an id and no method: the server's notifications and requests are none.
            if isinstance(message, dict) and "id" in message and "method" not in message:
                self.inbox.put(message)
        self.inbox.put(None)

    def send(self, message):
        try:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            self.proc.stdin.flush()
        except (BrokenPipeError, ValueError) as e:
            raise Divergence("connection", "the server closed the connection") from e

    def receive(self, ident, timeout=CALL_TIMEOUT):
        """The response to the request with the id, which may come after those to requests sent later."""
        key = json.dumps(ident)
        deadline = time.monotonic() + timeout
        while key not in self.answers:
            try:
                message = self.inbox.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                raise Divergence("connection", f"no answer within {timeout} s") from None
            if message is None:
                self.inbox.put(None)
                raise Divergence("connection", "the server closed the connection")
            self.answers[json.dumps(message.get("id"))] = message
        return self.answers.pop(key)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
            self.proc.wait()
        self.proc.stdout.close()


def client_of(entries):
    """The client of a recording, as `NAME VERSION`: from its handshake, or from the `_meta` of its first request."""
    for entry in entries:
        params = (entry.get("msg") or {}).get("params") or {} if entry.get("dir") == "request" else {}
        info = params.get("clientInfo") or (params.get("_meta") or {}).get("io.modelcontextprotocol/clientInfo")
        if info:
            return f"{info.get('name')} {info.get('version')}"
    return None


def load(recording):
    """The messages of a recording: a run directory's mcp.jsonl, or the file."""
    path = os.path.join(recording, "mcp.jsonl") if os.path.isdir(recording) else recording
    entries = actlog.load_jsonl(path)
    if not any(e.get("dir") == "request" and (e.get("msg") or {}).get("method") == "tools/call" for e in entries):
        raise ValueError(f"{path} holds no tool call to replay")
    return entries


def replay(entries, connect, gap=("none", 0.0), emit=lambda event: None, sleep=time.sleep, clock=time.monotonic,
           timeout=CALL_TIMEOUT):
    """Sends a recording's client messages to the server that connect() starts, in the recorded order: each request
    after the gap, and each response awaited where the recording has it, so that calls overlap only where they did.
    Returns the end event: the tool calls recorded and sent, how their arguments were changed, what else differed, and
    the divergence that stopped the replay, or None."""
    mapping = Mapping()
    connection, last, moment = None, None, None
    sent, calls, differences, diverged = {}, 0, {}, None
    total = sum(1 for e in entries if e.get("dir") == "request" and (e.get("msg") or {}).get("method") == "tools/call")
    try:
        for entry in entries:
            message = entry.get("msg")
            if not isinstance(message, dict):
                continue
            if entry["dir"] == "request" and "method" in message:
                method = message["method"]
                if method in SESSION_START and connection is not None:
                    connection.close()
                    connection, sent = None, {}
                    mapping.new_session()
                if connection is None:
                    connection = connect()
                if last is not None:
                    wait = gap_seconds(gap, entry["t"] - last, method == "tools/call") - (clock() - moment)
                    if wait > 0:
                        sleep(wait)
                out = message
                if method == "tools/call":
                    params = message.get("params") or {}
                    tool, recorded_args = params.get("name"), params.get("arguments") or {}
                    try:
                        args = mapping.arguments(tool, recorded_args)
                    except Divergence as d:
                        diverged = {"call": calls + 1, "tool": tool, "kind": d.kind, "detail": d.detail, "before": True}
                        break
                    out = {**message, "params": {**params, "arguments": args}}
                    calls += 1
                    sent[json.dumps(message.get("id"))] = (calls, tool, recorded_args, args, clock())
                elif "id" in message:
                    sent[json.dumps(message["id"])] = None
                connection.send(out)
                last, moment = entry["t"], clock()
            elif entry["dir"] == "response" and "id" in message and "method" not in message:
                key = json.dumps(message["id"])
                if key not in sent or connection is None:
                    continue
                call = sent.pop(key)
                answer = connection.receive(message["id"], timeout)
                last, moment = entry["t"], clock()
                if call is None:
                    continue
                number, tool, recorded_args, args, started = call
                recorded = read_result(mapping.recorded, tool, recorded_args, message)
                replayed = read_result(mapping.replayed, tool, args, answer)
                mapping.learn(recorded, replayed)
                divergence, notes = compare(recorded, replayed)
                for note in notes:
                    kind = re.sub(r"\d+", "N", note)
                    differences[kind] = differences.get(kind, 0) + 1
                emit({"type": "replay", "event": "call", "call": number, "tool": tool, "seconds": round(moment - started, 3),
                      "summary": replayed["summary"], "notes": notes,
                      **({"unread": replayed["unread"]} if "unread" in replayed else {})})
                if divergence:
                    diverged = {"call": number, "tool": tool, "kind": divergence.kind, "detail": divergence.detail,
                                "before": False}
                    break
    except Divergence as d:
        diverged = {"call": calls, "tool": None, "kind": d.kind, "detail": d.detail, "before": False}
    finally:
        if connection is not None:
            connection.close()
    return {"type": "replay", "event": "end", "calls_recorded": total, "calls_sent": calls, "diverged": diverged,
            "arguments": dict(mapping.counts), "differences": differences}


def main(argv):
    if "--" not in argv:
        raise SystemExit(__doc__)
    split = argv.index("--")
    options, server = argv[:split], argv[split + 1:]
    gap = DEFAULT_GAP
    if "--gap" in options:
        i = options.index("--gap")
        gap = options[i + 1]
        del options[i:i + 2]
    if len(options) != 1 or not server:
        raise SystemExit(__doc__)
    entries = load(options[0])

    def emit(event):
        print(json.dumps(event), flush=True)

    emit({"type": "replay", "event": "start", "recording": os.path.abspath(options[0]), "client": client_of(entries),
          "gap": gap})
    end = replay(entries, lambda: Connection(server), parse_gap(gap), emit)
    emit(end)


if __name__ == "__main__":
    if sys.argv[1:] in ([], ["--help"]):
        print(__doc__)
    else:
        main(sys.argv[1:])
