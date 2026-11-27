#!/usr/bin/env python3
"""The tests of replay.py, of the replay harness in run.py and report.py and of replay_report.py, on recorded results
and a fake server, with no VM: python3 replay_test.py"""

import json
import math
import os
import random
import sys
import tempfile
import textwrap
import unittest

import replay
import replay_report
import report
import run

TESTDATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "testdata")
WINDOW = {"x": 0, "y": 0, "width": 1920, "height": 1080}


class Server:
    """What a server gives a session, in hyprmasq-mcp's own words: results with structured content, as Codex gets
    them, or as text alone, as Claude Code does."""

    def __init__(self, structured):
        self.structured = structured

    def result(self, lines, content, error=False):
        out = {"content": [{"type": "text", "text": "\n".join(lines)}]}
        if error:
            out["isError"] = True
        if self.structured:
            out["structuredContent"] = content
        return out

    def windows(self, *windows, shot=None):
        """windows lists (address, class, title), with a shot of the window when it lists one."""
        lines = [f'{a} · {c} "{t}" · workspace web (hidden) · 1920×1080' for a, c, t in windows]
        listed = [{"window": a, "class": c, "title": t, "size": {"width": 1920, "height": 1080}} for a, c, t in windows]
        if shot:
            lines.append(f"shot {shot} 1280×720 of window {windows[0][0]}")
            listed[0]["shot"] = {"id": shot, "width": 1280, "height": 720, "frame": WINDOW}
        return self.result(lines, {"windows": listed})

    def act(self, shot, base=None, zooms=(), done=(1, 1), size=(1280, 720), frame=WINDOW, closed=False, new=(),
            source=None, before=None, scroll=None):
        """act's result: a changes shot of base with its zooms, each (id, image width, image height, x, y, width,
        height), or a full shot; new windows as (address, class, title, shot, width, height); the window a closed one
        came from as (address, class, title, shot); and the changes shot before a scroll as (shot, base, zooms)."""
        def changes(shot, base, zooms):
            text = f"shot {shot} = {base}" + (f" + {len(zooms)} change" + ("s" if len(zooms) > 1 else "") if zooms else ", no change")
            regions = [dict(zip(("shot", "image_width", "image_height", "x", "y", "width", "height"), z)) for z in zooms]
            lines = [f"zoom {z[0]} {z[1]}×{z[2]} of {shot} at {z[3]},{z[4]} {z[5]}×{z[6]}" for z in zooms]
            return text, lines, {"id": shot, "width": size[0], "height": size[1], "frame": frame,
                                 "changes": {"base": base, "regions": regions}}

        if base:
            text, zoom_lines, shown = changes(shot, base, zooms)
        else:
            text, zoom_lines = f"shot {shot} {size[0]}×{size[1]}", []
            shown = {"id": shot, "width": size[0], "height": size[1], "frame": frame}
        wait = "redraw observed · quiet 200 ms · 210 ms"
        content = {"actions": {"done": done[0], "total": done[1], "delivery": "complete" if done[0] == done[1] else "partial"},
                   "shot": shown, "new_windows": []}
        parts = [f"{done[0]}/{done[1]} delivered" + ("" if done[0] == done[1] else ", delivery partial")]
        lines = []
        if before:
            before_text, before_lines, before_shot = changes(*before)
            content["before_scroll"] = {"shot": before_shot}
            lines += [f"before the scroll: {wait} · {before_text}", *before_lines, f"after the scroll: {wait} · {text}"]
        else:
            parts += [wait]
        if closed:
            parts.append("window closed")
            content["window_closed"] = True
        if not before and not (closed and source):
            parts.append(text)
        if closed and source:
            del content["shot"]
        if source:
            address, cls, title, source_shot = source
            parts.append(f'back to {address} ({cls} "{title}") on workspace web (hidden)')
            content["source"] = {"window": address, "class": cls, "title": title,
                                 "shot": {"id": source_shot, "width": 1280, "height": 720, "frame": WINDOW}}
        for address, cls, title, *_ in new:
            parts.append(f'new window {address} ({cls} "{title}", same pid) on workspace web (hidden)')
        lines = [" · ".join(parts)] + lines + (zoom_lines if "shot" in content else [])
        if scroll:
            lines.append(f"Page scroll {scroll}")
        if source:
            lines.append(f"shot {source[3]} 1280×720 of {source[0]}, the window it came from")
        for address, cls, title, new_shot, width, height in new:
            lines.append(f"shot {new_shot} {width}×{height} of new window {address}")
            content["new_windows"].append({"window": address, "class": cls, "title": title, "shot": {
                "id": new_shot, "width": width, "height": height, "frame": {"x": 0, "y": 0, "width": width, "height": height}}})
        return self.result(lines, content)

    def screenshot(self, shot, window, of=None, region=None, size=(1280, 720), frame=WINDOW):
        text = f"shot {shot} {size[0]}×{size[1]}"
        if of:
            text += f" · zoom of {of} at {region[0]},{region[1]} {region[2]}×{region[3]}"
        return self.result([text + f" · window {window} 1920×1080"],
                           {"shot": {"id": shot, "width": size[0], "height": size[1], "frame": frame}})

    def error(self, code, message):
        return self.result([f"error {code}: {message}", "Nothing was sent."], {"error": {"code": code, "message": message}},
                           error=True)

    def done(self, text):
        return self.result([text], {"summary": text})


CLIENTS = {
    # Codex shakes hands, and gets structured content.
    True: [{"jsonrpc": "2.0", "id": 0, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "clientInfo": {"name": "codex-mcp-client", "version": "0.170.0"}}},
           {"jsonrpc": "2.0", "method": "notifications/initialized"}],
    # Claude Code names itself in each request, and gets text.
    False: [{"jsonrpc": "2.0", "id": "probe", "method": "server/discover",
             "params": {"_meta": {"io.modelcontextprotocol/clientInfo": {"name": "claude-code", "version": "2.1.330"}}}}],
}


def recording(calls, structured=True, gaps=None, meta=None):
    """The MCP log of a session: the client's handshake, then each call as (tool, arguments, result), answered 0.25 s
    after it was sent and sent `gaps` seconds after the answer before it, by default 2 s."""
    entries, t = [], 1000.0
    for message in CLIENTS[structured]:
        t += 0.01
        entries.append({"t": t, "dir": "request", "msg": message})
        if "id" in message:
            t += 0.01
            entries.append({"t": t, "dir": "response", "msg": {"jsonrpc": "2.0", "id": message["id"], "result": {}}})
    for n, (tool, args, result) in enumerate(calls):
        t += gaps[n] if gaps else 2.0
        params = {"name": tool, "arguments": args, **({"_meta": meta} if meta else {})}
        entries.append({"t": t, "dir": "request", "msg": {"jsonrpc": "2.0", "id": n + 1, "method": "tools/call", "params": params}})
        entries.append({"t": t + 0.1, "dir": "response", "msg": {"jsonrpc": "2.0", "method": "notifications/progress",
                                                                  "params": {"progressToken": n}}})
        t += 0.25
        entries.append({"t": t, "dir": "response", "msg": {"jsonrpc": "2.0", "id": n + 1, "result": result}})
    return entries


class Fake:
    """A server that answers the tool calls it gets with the results given, in order, and keeps what it got."""

    def __init__(self, results):
        self.results, self.got, self.connections, self.closed = list(results), [], 0, 0

    def connect(self):
        self.connections += 1
        return self

    def send(self, message):
        self.got.append(message)

    def receive(self, ident, timeout=None):
        request = next(m for m in self.got if m.get("id") == ident)
        if request["method"] != "tools/call":
            return {"jsonrpc": "2.0", "id": ident, "result": {}}
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    def close(self):
        self.closed += 1

    def calls(self):
        """The arguments of the tool calls it got."""
        return [m["params"]["arguments"] for m in self.got if m.get("method") == "tools/call"]


def play(calls, results, structured=True, **options):
    """Replays a recording of the calls to a server that answers with results: the end event and the server."""
    fake = Fake(results)
    end = replay.replay(recording(calls, structured, options.pop("gaps", None)), fake.connect, **options)
    return end, fake


A, B = ("0xaaa0", "firefox", "Build guide"), ("0xbbb0", "firefox", "Build guide")
# A zoom of 478×250 logical pixels at 0,51 of the window, as the server states it in a shot of two thirds the size.
ZOOM = ("s3", 478, 250, 0, 34, 319, 167)


class Mapping(unittest.TestCase):
    def both(self):
        """Each case with the results that Codex gets, with structured content, and with Claude Code's, text alone."""
        for structured in (True, False):
            with self.subTest(structured=structured):
                yield Server(structured), structured

    def test_calls_name_the_replays_window_and_shot_for_the_recordings(self):
        for s, structured in self.both():
            calls = [
                ("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                ("act", {"shot": "s1", "actions": [[100, 100]]}, s.act("s2", "s1", [ZOOM, ("s4", 60, 30, 600, 300, 40, 20)])),
                ("act", {"shot": "s2", "actions": [[640, 360], ["type", "a"]], "capture": "window"}, s.act("s5")),
                ("screenshot", {"window": A[0], "region": {"shot": "s5", "x": 10, "y": 20, "width": 300, "height": 200}},
                 s.screenshot("s6", A[0], "s5", (10, 20, 300, 200), (900, 600), {"x": 15, "y": 30, "width": 450, "height": 300})),
                ("act", {"shot": "s6", "actions": [[450, 300]]}, s.act("s7", "s5", [])),
                ("release", {"window": A[0]}, s.done("released")),
            ]
            # The replay's window has another address, and its first act found one change where the recording had
            # two, so every shot after it has another id.
            end, fake = play(calls, [
                s.windows(B, shot="s1"), s.act("s2", "s1", [ZOOM]), s.act("s4"),
                s.screenshot("s5", B[0], "s4", (10, 20, 300, 200), (900, 600), {"x": 15, "y": 30, "width": 450, "height": 300}),
                s.act("s6", "s4", []), s.done("released"),
            ], structured)
            self.assertIsNone(end["diverged"])
            self.assertEqual((end["calls_sent"], end["calls_recorded"]), (6, 6))
            self.assertEqual(fake.calls(), [
                {"match": "firefox"},
                {"shot": "s1", "actions": [[100, 100]]},
                {"shot": "s2", "actions": [[640, 360], ["type", "a"]], "capture": "window"},
                {"window": B[0], "region": {"shot": "s4", "x": 10, "y": 20, "width": 300, "height": 200}},
                {"shot": "s5", "actions": [[450, 300]]},
                {"window": B[0]},
            ])

    def test_a_point_in_a_zoom_goes_to_the_same_point_of_the_window_in_the_shot_it_is_part_of(self):
        for s, structured in self.both():
            calls = [
                ("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                ("act", {"shot": "s1", "actions": [[100, 100]]}, s.act("s2", "s1", [ZOOM])),
                ("act", {"shot": "s3", "actions": [[344, 100], ["scroll", 20, 30, 2], ["path", [[0, 0], [477, 249]]],
                                                   {"action": "click", "x": 3, "y": 6, "button": "right"}, ["key", "enter"]],
                         "next_page": {"x": 30, "y": 60}}, s.act("s4", "s2", [])),
            ]
            # The replay's act found the change in another box, so the recording's zoom has none of its own: its
            # points go to the changes shot. The zoom covers 0,51 478×250 of the window, and the shot has two thirds
            # of the window's pixels.
            other = ("s3", 480, 240, 2, 40, 320, 160)
            end, fake = play(calls, [s.windows(B, shot="s1"), s.act("s2", "s1", [other]), s.act("s4", "s2", [])], structured)
            self.assertIsNone(end["diverged"])
            sent = fake.calls()[2]
            self.assertEqual(sent["shot"], "s2")
            expected = [[344 * 2 / 3, (51 + 100) * 2 / 3], ["scroll", 20 * 2 / 3, (51 + 30) * 2 / 3, 2],
                        ["path", [[0, 51 * 2 / 3], [477 * 2 / 3, (51 + 249) * 2 / 3]]],
                        {"action": "click", "x": 2, "y": (51 + 6) * 2 / 3, "button": "right"}, ["key", "enter"]]
            self.assertPoints(sent["actions"], expected)
            self.assertPoints([sent["next_page"]], [{"x": 20, "y": (51 + 60) * 2 / 3}])
            self.assertEqual(end["arguments"]["rebased"], 1)

    def assertPoints(self, sent, expected):
        """The same actions, each number within a ten-thousandth of a pixel of the shot."""
        def flat(value):
            if isinstance(value, dict):
                return [x for key in sorted(value) for x in [key] + flat(value[key])]
            if isinstance(value, list):
                return [x for v in value for x in flat(v)]
            return [value]

        a, b = flat(sent), flat(expected)
        self.assertEqual(len(a), len(b))
        for x, y in zip(a, b):
            if isinstance(y, float):
                self.assertAlmostEqual(x, y, delta=1e-4)
            else:
                self.assertEqual(x, y)

    def test_a_zoom_the_replay_got_again_keeps_its_pixels_under_the_replays_id(self):
        for s, structured in self.both():
            calls = [
                ("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                ("act", {"shot": "s1", "actions": [[100, 100]]}, s.act("s2", "s1", [ZOOM])),
                ("act", {"shot": "s3", "actions": [[344, 100]],
                         "capture": {"shot": "s3", "x": 5, "y": 6, "width": 70, "height": 80}}, s.act("s4", "s2", [])),
            ]
            first = ("s3", 60, 30, 600, 300, 40, 20)
            end, fake = play(calls, [s.windows(B, shot="s1"), s.act("s2", "s1", [first, ("s4",) + ZOOM[1:]]),
                                     s.act("s5", "s2", [])], structured)
            self.assertIsNone(end["diverged"])
            self.assertEqual(fake.calls()[2], {"shot": "s4", "actions": [[344, 100]],
                                               "capture": {"shot": "s4", "x": 5, "y": 6, "width": 70, "height": 80}})

    def test_shots_pair_by_their_part_in_the_result(self):
        """The shot before a scroll, the shot of a window that input opened, and the shot of the window that a closed
        one came from are each the replay's shot of the same part, whatever ids the results before them took."""
        for s, structured in self.both():
            dialog = ("0xddd0", "soffice", "Text Import")
            calls = [
                ("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                ("act", {"shot": "s1", "actions": [[5, 5], ["scroll", 9, 9, 3]]},
                 s.act("s4", before=("s2", "s1", [("s3", 60, 30, 600, 300, 40, 20)]))),
                ("act", {"shot": "s2", "actions": [[7, 7]]}, s.act("s5", "s4", [], new=[(*dialog, "s6", 718, 780)])),
                ("act", {"shot": "s6", "actions": [[300, 400], ["key", "enter"]]},
                 s.act("s7", closed=True, source=(*A, "s7"), done=(2, 2))),
                ("act", {"shot": "s7", "actions": [[8, 8]]}, s.act("s8", "s7", [])),
            ]
            theirs = ("0xeee0", "soffice", "Text Import")
            end, fake = play(calls, [
                s.windows(B, shot="s1"),
                s.act("s5", before=("s2", "s1", [("s3", 60, 30, 600, 300, 40, 20), ("s4", 30, 30, 0, 0, 20, 20)])),
                s.act("s6", "s5", [("s7", 30, 30, 0, 0, 20, 20)], new=[(*theirs, "s8", 718, 780)]),
                s.act("s9", closed=True, source=(*B, "s9"), done=(2, 2)),
                s.act("s10", "s9", []),
            ], structured)
            self.assertIsNone(end["diverged"])
            self.assertEqual([c["shot"] for c in fake.calls()[1:]], ["s1", "s2", "s8", "s9"])
            self.assertEqual(fake.calls()[3]["actions"], [[300, 400], ["key", "enter"]])

    def test_windows_pair_by_class_and_title_then_by_class_and_an_address_by_its_latest_window(self):
        s = Server(True)
        terminal, mail = ("0x1000", "foot", "user-terminal"), ("0x2000", "thunderbird", "Inbox - 09:31")
        first, second = ("0x3000", "soffice", "Save"), ("0x3000", "soffice", "Export")
        calls = [
            ("windows", {}, s.windows(terminal, mail, A)),
            ("release", {"window": mail[0]}, s.done("released")),
            ("act", {"window": A[0], "actions": [["key", "s"]]}, s.act("s1", new=[(*first, "s2", 400, 300)])),
            ("close", {"window": "0x3000"}, s.done("closed")),
            # The address of the window that closed is another window's now.
            ("act", {"window": A[0], "actions": [["key", "e"]]}, s.act("s3", new=[(*second, "s4", 400, 300)])),
            ("close", {"window": "0X3000"}, s.done("closed")),
        ]
        end, fake = play(calls, [
            # In another order, and the mail window's title holds another time.
            s.windows(B, ("0x7000", "thunderbird", "Inbox - 14:02"), ("0x8000", "foot", "user-terminal")),
            s.done("released"),
            s.act("s1", new=[("0x9100", "soffice", "Save", "s2", 400, 300)]), s.done("closed"),
            s.act("s3", new=[("0x9200", "soffice", "Export", "s4", 400, 300)]), s.done("closed"),
        ])
        self.assertIsNone(end["diverged"])
        self.assertEqual([c.get("window") for c in fake.calls()], [None, "0x7000", B[0], "0x9100", B[0], "0x9200"])

    def test_a_shot_of_another_frame_takes_the_point_through_the_window(self):
        """A recorded shot of the whole window against a replay's shot that reaches a popup above the window: the same
        point of the window is 30 logical pixels further down in the replay's shot."""
        s = Server(True)
        calls = [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[10, 10]], "capture": "window"}, s.act("s2")),
                 ("act", {"shot": "s2", "actions": [[300, 150]]}, s.act("s3", "s2", []))]
        popup = {"x": 0, "y": -30, "width": 1920, "height": 1110}
        end, fake = play(calls, [s.windows(B, shot="s1"), s.act("s2", size=(1280, 740), frame=popup), s.act("s3", "s2", [])])
        self.assertIsNone(end["diverged"])
        self.assertPoints(fake.calls()[2]["actions"], [[300.0, (150 * 1.5 + 30) / 1.5]])
        self.assertEqual(end["arguments"]["moved"], 1)


class ZoomFrame(unittest.TestCase):
    def stated(self, zoom, shot):
        """The box of a shot's pixels that the server states for a zoom of the frame given, as regionInfo in
        mcp/internal/server/changes.go computes it."""
        f, sx, sy = shot["frame"], shot["width"] / shot["frame"]["width"], shot["height"] / shot["frame"]["height"]
        x0 = max(0, math.floor((zoom["x"] - f["x"]) * sx + 1e-6))
        y0 = max(0, math.floor((zoom["y"] - f["y"]) * sy + 1e-6))
        x1 = min(shot["width"], math.ceil((zoom["x"] + zoom["width"] - f["x"]) * sx - 1e-6))
        y1 = min(shot["height"], math.ceil((zoom["y"] + zoom["height"] - f["y"]) * sy - 1e-6))
        return (x0, y0, x1 - x0, y1 - y0)

    def test_the_box_gives_the_zooms_frame_to_half_a_logical_pixel_and_says_when_to_the_pixel(self):
        rng = random.Random(7)
        shots = [{"frame": WINDOW, "width": 1280, "height": 720},                      # shrunk to two thirds
                 {"frame": {"x": 0, "y": 0, "width": 718, "height": 780}, "width": 718, "height": 780},  # as captured
                 {"frame": {"x": -12, "y": -30, "width": 1932, "height": 1110}, "width": 1280, "height": 735}]  # a popup
        exact = 0
        for case in range(3000):
            shot = shots[case % len(shots)]
            f = shot["frame"]
            width, height = rng.randint(4, 600), rng.randint(4, 600)
            zoom = {"x": f["x"] + rng.randint(0, f["width"] - width), "y": f["y"] + rng.randint(0, f["height"] - height),
                    "width": width, "height": height}
            # The zoom's image has the monitor's pixels: one a logical pixel, or two at a scale of 2.
            scale = 1 + case % 2
            frame, sure = replay.zoom_frame(shot, self.stated(zoom, shot), (width * scale, height * scale))
            for key in ("x", "y"):
                self.assertLessEqual(abs(frame[key] - zoom[key]), 0.5, msg=(case, zoom, frame))
            if sure:
                exact += 1
                self.assertEqual(frame, zoom, msg=case)
        # A shot at the window's own size always gives the frame, a shrunk one often.
        self.assertGreater(exact, 1500)

    def test_a_recorded_zoom(self):
        """zoom s20 478×250 of s19 at 0,34 319×167, from a recording: the only whole pixels that make this box."""
        frame, sure = replay.zoom_frame({"frame": WINDOW, "width": 1280, "height": 720}, (0, 34, 319, 167), (478, 250))
        self.assertEqual((frame, sure), ({"x": 0, "y": 51, "width": 478, "height": 250}, True))


class Results(unittest.TestCase):
    """Results that hyprmasq-mcp gave Codex in recorded runs, which hold the text and the structured content."""

    def setUp(self):
        with open(os.path.join(TESTDATA, "replay-results.jsonl")) as f:
            self.recorded = {r["name"]: r for r in map(json.loads, f)}

    def read(self, name, structured):
        r = self.recorded[name]
        result = dict(r["result"])
        if not structured:
            del result["structuredContent"]
        out = replay.read_result(replay.Side(), r["tool"], r["arguments"], {"result": result})
        return {k: out[k] for k in ("error", "delivered", "closed", "windows", "shots", "kind", "zooms", "timeout", "scroll")}

    def test_the_text_tells_what_the_structured_content_tells(self):
        for name in self.recorded:
            with self.subTest(result=name):
                self.assertEqual(self.read(name, False), self.read(name, True))

    def test_what_each_result_gives(self):
        calc, dialog = "0x59cba6953ff0", "0x59cba69982c0"
        expected = {
            "windows": {"shots": {"0x5a1c40b6ceb0": "s1"}, "kind": None},
            "windows-all": {"shots": {}, "windows": 3},
            "changes": {"shots": {"main": "s2"}, "kind": "changes", "zooms": 2, "delivered": (2, 2)},
            "split": {"shots": {"main": "s4", "before": "s2"}, "kind": "full", "delivered": (18, 18),
                      "scroll": "stopped after 5 notches (reference_changed)"},
            "new-window": {"shots": {"main": "s19", dialog: "s21"}, "zooms": 1,
                           "windows": [(dialog, "soffice", "Text Import")]},
            "source": {"shots": {calc: "s22"}, "closed": True,
                       "windows": [(calc, "libreoffice-calc", "Office revenue Q4 2025.ods — LibreOffice Calc")]},
            "region": {"shots": {"main": "s8"}, "kind": "full"},
            "error-shot": {"error": "out_of_bounds", "shots": {"main": "s32"}, "delivered": None},
            "error": {"error": "invalid_arguments", "shots": {}, "delivered": None},
            "full": {"shots": {"main": "s10"}, "kind": "full", "error": None, "closed": False},
        }
        self.assertEqual(set(expected), set(self.recorded))
        for name, facts in expected.items():
            for structured in (True, False):
                with self.subTest(result=name, structured=structured):
                    got = self.read(name, structured)
                    for key, value in facts.items():
                        self.assertEqual(len(got[key]) if key == "windows" and isinstance(value, int) else got[key], value, msg=key)


class Divergence(unittest.TestCase):
    def diverged(self, calls, results, structured=True):
        end, fake = play(calls, results, structured)
        self.assertIsNotNone(end["diverged"], msg="the replay went on")
        return end["diverged"], fake

    def test_a_result_that_differs_stops_the_replay_before_the_next_call(self):
        s = Server(True)
        first = ("windows", {"match": "firefox"}, s.windows(A, shot="s1"))
        last = ("act", {"shot": "s2", "actions": [[1, 1]]}, s.act("s3", "s2", []))
        cases = {
            "error": (s.act("s2", "s1", []), s.error("stale_shot", "the window changed")),
            "no_error": (s.error("stale_shot", "the window changed"), s.act("s2", "s1", [])),
            "delivery": (s.act("s2", "s1", [], done=(3, 3)), s.act("s2", "s1", [], done=(2, 3))),
            "window_closed": (s.act("s2", "s1", []), s.act("s2", closed=True, source=(*B, "s2"))),
            # The same call, but the view stopped elsewhere: the next call's points are of the recording's view.
            "scroll": (s.act("s2", before=("s9", "s1", []), scroll="moved the view 842 logical px, leaving overlap."),
                       s.act("s2", before=("s9", "s1", []), scroll="stopped after 9 wheel notches (reference_changed).")),
        }
        for kind, (recorded, replayed) in cases.items():
            with self.subTest(kind=kind):
                calls = [first, ("act", {"shot": "s1", "actions": [[5, 5], [6, 6], [7, 7]]}, recorded), last]
                diverged, fake = self.diverged(calls, [s.windows(B, shot="s1"), replayed, s.act("s3", "s2", [])])
                self.assertEqual((diverged["kind"], diverged["call"], diverged["before"]), (kind, 2, False))
                self.assertEqual(len(fake.calls()), 2)
                self.assertEqual(fake.closed, 1)

    def test_another_error_than_the_recordings_is_a_divergence_and_the_same_error_is_none(self):
        s = Server(True)
        calls = [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[5000, 5]]}, s.error("out_of_bounds", "5000,5 lies outside shot s1")),
                 ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", []))]
        end, fake = play(calls, [s.windows(B, shot="s1"), s.error("out_of_bounds", "5000,5 lies outside shot s1"),
                                 s.act("s2", "s1", [])])
        self.assertIsNone(end["diverged"])
        diverged, _ = self.diverged(calls, [s.windows(B, shot="s1"), s.error("window_gone", "no window"), s.act("s2")])
        self.assertEqual(diverged["kind"], "error")

    def test_a_call_that_names_what_the_replay_has_nothing_for_is_not_sent(self):
        text, s = Server(False), Server(True)
        dialog = ("0xddd0", "soffice", "Text Import")
        cases = {
            # The replay's listing matched two windows, so it came without the shot that the next call names.
            "shot": (s, [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                         ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", []))],
                     [s.windows(B, ("0xccc0", "firefox", "Library"))]),
            # The recording's input opened a dialog, and the replay's did not.
            "window": (s, [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                           ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", [], new=[(*dialog, "s3", 718, 780)])),
                           ("close", {"window": dialog[0]}, s.done("closed"))],
                       [s.windows(B, shot="s1"), s.act("s2", "s1", [])]),
            # Claude Code's results give no frame of a new window's shot, and the replay's dialog has another size.
            "coordinates": (text, [("windows", {"match": "firefox"}, text.windows(A, shot="s1")),
                                   ("act", {"shot": "s1", "actions": [[5, 5]]},
                                    text.act("s2", "s1", [], new=[(*dialog, "s3", 718, 780)])),
                                   ("act", {"shot": "s3", "actions": [[300, 400]]}, text.act("s4", "s3", []))],
                            [text.windows(B, shot="s1"), text.act("s2", "s1", [], new=[("0xeee0", *dialog[1:], "s3", 700, 780)])]),
        }
        for kind, (server, calls, results) in cases.items():
            with self.subTest(kind=kind):
                diverged, fake = self.diverged(calls, results, server.structured)
                self.assertEqual((diverged["kind"], diverged["call"], diverged["before"]), (kind, len(calls), True))
                self.assertEqual(len(fake.calls()), len(calls) - 1)

    def test_a_point_outside_a_zoom_stays_refused_in_the_shot_it_is_part_of(self):
        """act refused a point outside the zoom it was given in. The shot that the zoom is a part of holds that point
        of the window, so the replay sends one outside it, for the same refusal."""
        s = Server(True)
        refused = s.error("out_of_bounds", "actions[0] (click): 500,300 lies outside shot s3 (478×250)")
        calls = [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[100, 100]]}, s.act("s2", "s1", [ZOOM])),
                 ("act", {"shot": "s3", "actions": [[500, 300]]}, refused)]
        end, fake = play(calls, [s.windows(B, shot="s1"), s.act("s2", "s1", [("s3", 480, 240, 2, 40, 320, 160)]), refused])
        self.assertIsNone(end["diverged"])
        sent = fake.calls()[2]
        self.assertEqual(sent["shot"], "s2")
        x, y = sent["actions"][0]
        self.assertTrue(x >= 1280 or y >= 720, msg=sent)

    def test_a_new_windows_shot_of_the_same_size_keeps_its_pixels_without_a_frame(self):
        text = Server(False)
        dialog = ("0xddd0", "soffice", "Text Import")
        calls = [("windows", {"match": "firefox"}, text.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[5, 5]]}, text.act("s2", "s1", [], new=[(*dialog, "s3", 718, 780)])),
                 ("act", {"shot": "s3", "actions": [[300, 400]]}, text.act("s4", "s3", []))]
        end, fake = play(calls, [text.windows(B, shot="s1"), text.act("s2", "s1", [], new=[("0xeee0", *dialog[1:], "s3", 718, 780)]),
                                 text.act("s4", "s3", [])], structured=False)
        self.assertIsNone(end["diverged"])
        self.assertEqual(fake.calls()[2], {"shot": "s3", "actions": [[300, 400]]})
        self.assertEqual(end["arguments"]["assumed"], 1)

    def test_a_server_that_goes_away_ends_the_replay_as_diverged(self):
        s = Server(True)
        calls = [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", []))]
        diverged, _ = self.diverged(calls, [s.windows(B, shot="s1"), replay.Divergence("connection", "closed")])
        self.assertEqual(diverged["kind"], "connection")

    def test_differences_that_leave_the_recordings_way_open_are_counted_and_do_not_stop(self):
        s = Server(True)
        calls = [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", [ZOOM])),
                 ("act", {"shot": "s2", "actions": [[6, 6]]}, s.act("s4", "s2", []))]
        end, fake = play(calls, [s.windows(B, shot="s1"), s.act("s2"), s.act("s3", "s2", [])])
        self.assertIsNone(end["diverged"])
        self.assertEqual(end["differences"], {"full shot for a changes shot": 1})
        self.assertEqual(fake.calls()[2]["shot"], "s2")


class Session(unittest.TestCase):
    def test_the_clients_messages_go_out_as_recorded(self):
        """The server answers a client by its name: the handshake and the metadata of each request are the recording's."""
        for structured in (True, False):
            with self.subTest(structured=structured):
                s = Server(structured)
                meta = {"io.modelcontextprotocol/clientInfo": {"name": "claude-code"}, "progressToken": 4}
                entries = recording([("windows", {"match": "firefox"}, s.windows(A, shot="s1"))], structured, meta=meta)
                fake = Fake([s.windows(B, shot="s1")])
                replay.replay(entries, fake.connect)
                self.assertEqual(fake.got, [e["msg"] for e in entries if e["dir"] == "request"])
                self.assertEqual(replay.client_of(entries), "codex-mcp-client 0.170.0" if structured else "claude-code 2.1.330")

    def test_a_second_handshake_is_a_new_server_whose_shots_start_again(self):
        s = Server(True)
        one = recording([("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                         ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", [ZOOM]))])
        two = recording([("act", {"window": A[0], "actions": [], "capture": "window"}, s.act("s1")),
                         ("act", {"shot": "s1", "actions": [[6, 6]]}, s.act("s2", "s1", []))])
        for entry in two:
            entry["t"] += 100
            if "id" in entry["msg"] and entry["msg"].get("method") != "initialize" and "result" not in entry["msg"]:
                entry["msg"]["id"] += 10
            elif "id" in entry["msg"] and entry["msg"]["id"] != 0:
                entry["msg"]["id"] += 10
        fake = Fake([s.windows(B, shot="s1"), s.act("s2", "s1", [ZOOM]), s.act("s1"), s.act("s2", "s1", [])])
        end = replay.replay(one + two, fake.connect)
        self.assertIsNone(end["diverged"])
        self.assertEqual((fake.connections, fake.closed), (2, 2))
        self.assertEqual(fake.calls()[2:], [{"window": B[0], "actions": [], "capture": "window"},
                                           {"shot": "s1", "actions": [[6, 6]]}])

    def test_a_server_over_stdio_is_answered_by_id_and_its_end_is_a_divergence(self):
        script = textwrap.dedent("""
            import json, sys
            held = None
            for line in sys.stdin:
                message = json.loads(line)
                if message.get("method") == "quit":
                    break
                print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}), flush=True)
                answer = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"echo": message["params"]}})
                if message["params"].get("hold"):
                    held = answer
                else:
                    print(answer, flush=True)
                    if held:
                        print(held, flush=True)
                        held = None
        """)
        connection = replay.Connection([sys.executable, "-c", script])
        try:
            connection.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"hold": True}})
            connection.send({"jsonrpc": "2.0", "id": "b", "method": "tools/call", "params": {"n": 2}})
            # The first request's answer comes after the second's.
            self.assertEqual(connection.receive(1, timeout=20)["result"], {"echo": {"hold": True}})
            self.assertEqual(connection.receive("b", timeout=20)["result"], {"echo": {"n": 2}})
            connection.send({"jsonrpc": "2.0", "method": "quit"})
            with self.assertRaises(replay.Divergence) as raised:
                connection.receive(3, timeout=20)
            self.assertEqual(raised.exception.kind, "connection")
        finally:
            connection.close()


class Gap(unittest.TestCase):
    def test_settings(self):
        self.assertEqual(replay.parse_gap("none"), ("none", 0.0))
        self.assertEqual(replay.parse_gap("recorded"), ("recorded", math.inf))
        self.assertEqual(replay.parse_gap("recorded:5"), ("recorded", 5.0))
        self.assertEqual(replay.parse_gap("0.5"), ("fixed", 0.5))
        for text in ("", "fast", "recorded:", "recorded:x", "-1", "recorded:-2", "nan"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                replay.parse_gap(text)

    def waited(self, gap):
        """How long a replay with the gap setting waits before each of three calls, which the recording's agent sent
        0.5 s, 3 s and 20 s after the answer before: on a clock that only the waits move."""
        s = Server(True)
        calls = [("windows", {"match": "firefox"}, s.windows(A, shot="s1")),
                 ("act", {"shot": "s1", "actions": [[5, 5]]}, s.act("s2", "s1", [])),
                 ("act", {"shot": "s2", "actions": [[6, 6]]}, s.act("s3", "s2", []))]
        now, waits = [0.0], []

        def sleep(seconds):
            waits.append(round(seconds, 6))
            now[0] += seconds

        end, _ = play(calls, [s.windows(B, shot="s1"), s.act("s2", "s1", []), s.act("s3", "s2", [])], gaps=[0.5, 3.0, 20.0],
                      gap=replay.parse_gap(gap), sleep=sleep, clock=lambda: now[0])
        self.assertIsNone(end["diverged"])
        # Without the handshake's own hundredths of a second.
        return [w for w in waits if w >= 0.1]

    def test_the_gap_between_calls_is_the_settings(self):
        self.assertEqual(self.waited("none"), [])
        self.assertEqual(self.waited("recorded"), [0.5, 3.0, 20.0])
        self.assertEqual(self.waited("recorded:5"), [0.5, 3.0, 5.0])
        self.assertEqual(self.waited("1.5"), [1.5, 1.5, 1.5])


class Harness(unittest.TestCase):
    def test_the_agent_names_a_recording_and_takes_no_effort(self):
        self.assertEqual(run.parse_agent("replay:codex-gpt-1"), ("replay", "codex-gpt-1", None))
        with self.assertRaises(ValueError):
            run.parse_agent("replay:codex-gpt-1@high")

    def test_a_recording_is_found_under_its_tasks_results_and_refused_for_another_task(self):
        with tempfile.TemporaryDirectory() as root:
            def record(path, task, variant, backend="hyprmasq"):
                os.makedirs(path)
                open(os.path.join(path, "mcp.jsonl"), "w").close()
                with open(os.path.join(path, "run.json"), "w") as f:
                    json.dump({"task": task, "variant": variant, "backend": backend}, f)

            suite = os.path.join(root, "form-25", "codex-1")
            record(suite, "form", "25")
            record(os.path.join(root, "calc", "codex-1"), "calc", None)
            record(os.path.join(root, "calc", "cua-1"), "calc", None, "cua")
            # The directory of the tasks' results, a task's own results, and a path of its own.
            self.assertEqual(run.find_recording(root, "form", "25", "codex-1"), suite)
            self.assertEqual(run.find_recording(os.path.join(root, "form-25"), "form", "25", "codex-1"), suite)
            self.assertEqual(run.find_recording(None, "form", "25", suite), suite)
            for recordings, task, variant, model in ((root, "form", "25", "codex-2"), (os.path.join(root, "calc"), "form", "25", "codex-1"),
                                                     (root, "calc", None, "cua-1"), (None, "form", "25", "codex-1")):
                with self.subTest(model=model, task=task, recordings=recordings), self.assertRaises(RuntimeError):
                    run.find_recording(recordings, task, variant, model)

    def test_the_replay_is_started_with_its_recording_gap_and_the_tap(self):
        command = run.replay_command("/r/run", ["python3", "mcp_tap.py", "unix:/s", "5000", "/out/mcp.jsonl"], "none")
        self.assertEqual(command[1:], [os.path.join(run.BENCH, "replay.py"), "/r/run", "--gap", "none", "--",
                                       "python3", "mcp_tap.py", "unix:/s", "5000", "/out/mcp.jsonl"])

    def test_a_summary_tells_a_divergence_apart_and_counts_a_replay_without_an_end_as_one(self):
        start = {"type": "replay", "event": "start", "client": "claude-code 2.1.330"}
        call = {"type": "replay", "event": "call", "call": 1}
        diverged = {"call": 2, "tool": "act", "kind": "error", "detail": "stale_shot for no error"}
        end = {"type": "replay", "event": "end", "calls_recorded": 5, "calls_sent": 2, "diverged": diverged,
               "arguments": {"rebased": 1}, "differences": {}}
        stream = [{"t": 1, "line": line} for line in (start, call, end)]
        outcome = report.replay_outcome(stream, {"replay": {"recording": "/r", "gap": "none"}})
        self.assertEqual((outcome["recording"], outcome["client"], outcome["calls_sent"], outcome["diverged"]),
                         ("/r", "claude-code 2.1.330", 2, diverged))
        self.assertIn("diverged at call 2 of 5 (error)", report.agent_usage("replay", stream)["final"])
        # Stopped at its timeout, or broken: it never said how it went.
        cut = report.replay_outcome(stream[:2], {})
        self.assertEqual((cut["diverged"]["kind"], cut["calls_sent"]), ("stopped", 1))
        whole = [{"t": 1, "line": {**end, "calls_sent": 5, "diverged": None}}]
        self.assertEqual(report.agent_usage("replay", whole)["final"], "Replayed 5 of 5 calls.")


def sample(recording, rep, tool, valid=True, diverged=None, task="form-25", client="codex-mcp-client", **parts):
    return {"task": task, "recording": recording, "rep": rep, "client": client, "valid": valid, "diverged": diverged,
            "passed": valid, "tool": tool, "watched_acts": parts.pop("watched_acts", 4),
            **{p: parts.get(p, 0.0) for p in replay_report.PARTS}}


class Report(unittest.TestCase):
    def test_coverage_tells_diverged_replays_from_those_the_checker_failed(self):
        samples = [sample("a", 1, 10), sample("a", 2, 10), sample("b", 1, 10), sample("b", 2, 0, False, "shot"),
                   sample("c", 1, 0, False), sample("c", 2, 0, False, "error"),
                   sample("e", 1, 10, client="claude-code"), sample("d", 1, 10, task="calc")]
        rows = replay_report.coverage(samples)
        self.assertEqual(rows[("form-25", "codex-mcp-client")],
                         {"recordings": 3, "all": 1, "some": 1, "none": 1, "runs": 6, "passed": 3,
                          "diverged": {"shot": 1, "error": 1}, "failed": 1})
        self.assertEqual(rows[("form-25", "claude-code")]["all"], 1)
        self.assertEqual(rows[("calc", "codex-mcp-client")]["recordings"], 1)

    def test_repeatability_is_the_spread_within_a_recording_over_its_samples(self):
        samples = [sample("a", 1, 10.0), sample("a", 2, 10.2), sample("a", 3, 10.4),
                   sample("b", 1, 20.0), sample("b", 2, 20.0), sample("b", 3, 20.6),
                   # A repetition that diverged is no sample, and a recording with one sample has no spread.
                   sample("c", 1, 30.0), sample("c", 2, 30.5), sample("c", 3, 99.0, False, "error"),
                   sample("d", 1, 40.0), sample("d", 2, 0, False)]
        r = replay_report.repeatability(samples, ("tool",))["tool"]
        self.assertEqual(r["recordings"], 3)
        # The variances 0.04, 0.12 and 0.125, weighted by their 2, 2 and 1 degrees of freedom.
        self.assertAlmostEqual(r["pooled_sd"], math.sqrt((2 * 0.04 + 2 * 0.12 + 0.125) / 5))
        self.assertAlmostEqual(r["max_range"], 0.6)
        self.assertAlmostEqual(r["median_range"], 0.5)
        # The suite's sum is over the recordings with a sample in every repetition: a and b.
        self.assertEqual(r["suite_recordings"], 2)
        self.assertEqual([round(v, 1) for v in r["suite_sums"]], [30.0, 30.2, 31.0])

    def test_the_difference_pairs_recordings_by_the_mean_of_their_samples(self):
        base = [sample("a", 1, 10.0, watch=2.0), sample("a", 2, 10.4, watch=2.0), sample("b", 1, 20.0, watch=1.0),
                sample("c", 1, 5.0), sample("d", 1, 0, False, "shot")]
        ours = [sample("a", 1, 9.2, watch=1.0), sample("b", 1, 19.4, watch=0.5), sample("b", 2, 19.6, watch=0.5),
                sample("c", 1, 0, False), sample("d", 1, 7.0)]
        d = replay_report.difference(ours, base, ("tool", "watch"))
        # a: 9.2 against 10.2, b: 19.5 against 20: c and d are a sample in one of the two only.
        self.assertEqual(d["tool"]["pairs"], 2)
        self.assertAlmostEqual(d["tool"]["mean"], -0.75)
        self.assertAlmostEqual(d["tool"]["base_mean"], 15.1)
        self.assertAlmostEqual(d["watch"]["mean"], -0.75)
        self.assertLessEqual(d["tool"]["low"], d["tool"]["mean"])
        self.assertLessEqual(d["tool"]["mean"], d["tool"]["high"])
        self.assertGreaterEqual(d["tool"]["low"], -1.0)
        self.assertLessEqual(d["tool"]["high"], -0.5)


if __name__ == "__main__":
    unittest.main()
