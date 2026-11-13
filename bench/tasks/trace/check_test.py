"""Tests the trace task's grader and attribution on constructed drawings and logs: python3 check_test.py"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attribute  # noqa: E402
import check  # noqa: E402
import figure  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# The grade and the attribution are tested on a line of their own, testdata/line: a narrow line that curves and
# narrows towards the top of a 239×476 photo, measured for a figure that the task no longer has. The cases below are
# written for its geometry.
LINE = os.path.join(HERE, "testdata", "line")
check.load(LINE)
figure.WIDTH, figure.HEIGHT = check.TRACK["size"]

# The line's centre from the bottom edge up, as a drawing would follow it.
CENTRE = [[x, y] for x, y, _ in reversed(check.LINE)]


def chords(n):
    """The line's centre cut into n straight strokes, end to start, as chained drags draw it."""
    idx = [round(i * (len(CENTRE) - 1) / n) for i in range(n + 1)]
    return [{"kind": "brush", "points": [CENTRE[a], CENTRE[b]]} for a, b in zip(idx, idx[1:])]


class River(unittest.TestCase):
    """The river variant: its line is the waterline, required where the shadow lies on the bank."""

    def setUp(self):
        check.load(os.path.join(HERE, "river"))
        self.addCleanup(check.load, LINE)

    def test_the_waterline_passes_and_so_does_more_of_it(self):
        line = [[x, y] for x, y, _ in check.LINE]
        self.assertTrue(check.grade([{"kind": "brush", "points": line}])["passed"])
        shadow = [p for p in line if 230 <= p[1] <= 480]
        self.assertTrue(check.grade([{"kind": "brush", "points": shadow}])["passed"])

    def test_the_part_in_the_sun_alone_fails(self):
        result = check.grade([{"kind": "brush", "points": [[x, y] for x, y, _ in check.LINE if y < 230]}])
        self.assertFalse(result["passed"])
        self.assertTrue(result["failures"][0].startswith("the drawing covers 4% of the waterline; it leaves the waterline uncovered at y 240 to 480"))

    def test_on_the_stones_passes_and_on_the_path_fails(self):
        # The band reaches 10 pixels inland of the water's edge, over the stones of the embankment, and the slack 4.9
        # more: on the stones passes, on the path beyond them does not.
        def inland(d):
            return [[x - h + 10 - d, y] for x, y, h in check.LINE if 230 <= y <= 480]
        self.assertTrue(check.grade([{"kind": "brush", "points": inland(8)}])["passed"])
        self.assertFalse(check.grade([{"kind": "brush", "points": inland(18)}])["passed"])


class Grade(unittest.TestCase):
    def test_the_centre_line_passes(self):
        result = check.grade([{"kind": "brush", "points": CENTRE}])
        self.assertTrue(result["passed"])
        self.assertEqual(result["checks"]["coverage"], 1)
        self.assertEqual(result["checks"]["off_line_px"], 0)

    def test_one_straight_line_fails(self):
        result = check.grade([{"kind": "brush", "points": [CENTRE[0], CENTRE[-1]]}])
        self.assertFalse(result["passed"])
        self.assertTrue(result["failures"][0].startswith("the drawing covers 14% of the line"))
        self.assertIn("lie off the line", result["failures"][1])

    def test_enough_chords_follow_the_curve(self):
        self.assertFalse(check.grade(chords(5))["passed"])
        self.assertTrue(check.grade(chords(12))["passed"])

    def test_a_line_beside_it_fails(self):
        # 6 photo pixels to the right: on the line where it is wide, beside it where it is narrow.
        result = check.grade([{"kind": "brush", "points": [[x + 6, y] for x, y in CENTRE]}])
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"]["missed"][0][0], 0)

    def test_stopping_short_of_the_top_fails(self):
        result = check.grade([{"kind": "brush", "points": [p for p in CENTRE if p[1] >= 100]}])
        self.assertFalse(result["passed"])
        # Row 96 is within the tolerance of the line's start at row 100.
        self.assertEqual(result["checks"]["missed"], [(0, 94)])
        self.assertEqual(result["checks"]["off_line_px"], 0)

    def test_a_scribble_elsewhere_fails_a_complete_line(self):
        scribble = {"kind": "brush", "points": [[20, 300], [40, 300], [40, 330]]}
        result = check.grade([{"kind": "brush", "points": CENTRE}, scribble])
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"]["coverage"], 1)
        self.assertEqual(len(result["failures"]), 1)
        # 20 and 30 px off the line.
        self.assertTrue(result["failures"][0].startswith("50 px of the drawing lie off the line"))

    def test_a_dense_stroke_counts_its_length_once(self):
        # A stroke off the line with a point every half pixel, as close as Firefox records them, is 20 px long.
        dense = [[20 + i / 2, 300] for i in range(41)]
        self.assertEqual(check.grade([{"kind": "brush", "points": CENTRE}, {"kind": "brush", "points": dense}])["checks"]["off_line_px"], 20)

    def test_lines_and_dots_count(self):
        dots = [{"kind": "brush", "points": [p]} for p in CENTRE[::3]]
        lines = [{"kind": "line", "points": s["points"]} for s in chords(12)]
        self.assertEqual(check.grade(lines)["checks"]["kinds"], {"line": 12})
        self.assertEqual(check.grade(dots)["checks"]["off_line_px"], 0)

    def test_nothing_drawn(self):
        self.assertEqual(check.grade([])["failures"], ["nothing is drawn"])

    def test_milestones(self):
        saves = [{"time": 5, "change": "stroke", "strokes": chords(12)}]  # before the agent started
        saves += [{"time": 10, "change": "stroke", "strokes": chords(12)[:1]},
                  {"time": 11, "change": "clear", "strokes": []},
                  {"time": 12, "change": "stroke", "strokes": [{"kind": "brush", "points": CENTRE}]},
                  {"time": 13, "change": "undo", "strokes": []}]
        self.assertEqual(list(check.events(saves, since=8).items()),
                         [("first stroke", 10), ("line drawn", 12), ("last change", 13)])
        self.assertEqual(check.events([], since=8), {})

    def test_the_server_takes_only_drawings_the_page_makes(self):
        self.assertTrue(figure.valid_strokes(chords(12)))
        self.assertTrue(figure.valid_strokes([]))
        for bad in ([{"kind": "brush", "points": []}], [{"kind": "spray", "points": [[1, 1]]}],
                    [{"kind": "brush", "points": [[1, check.TRACK["size"][1] + 1]]}], {"strokes": []},
                    [{"kind": "brush", "points": [[True, 1]]}]):
            self.assertFalse(figure.valid_strokes(bad), bad)


# A page laid out as in the VM at 1920×1080: the content 85.5 logical pixels below the window's top, and the photo at
# 16,60 in it, scaled by 2.
OFFSET = (0, 85.5)
SCALE = 2
PHOTO = [16, 60, 16 + check.TRACK["size"][0] * SCALE, 60 + check.TRACK["size"][1] * SCALE]
UNDO = [600, 120, 660, 150]
WINDOW = "0x1234"
VM_TO_HOST = 4000.0


def client_of_photo(point):
    return [PHOTO[0] + point[0] * SCALE, PHOTO[1] + point[1] * SCALE]


def shot_of_client(point):
    """In s1, a 1280×720 shot of the 1920×1080 window."""
    return [(point[0] + OFFSET[0]) / 1.5, (point[1] + OFFSET[1]) / 1.5]


def shot_of_photo(point):
    return shot_of_client(client_of_photo(point))


class Run:
    """A run directory under construction: act calls in mcp.jsonl, and the page's events."""

    def __init__(self):
        self.tap, self.page, self.id, self.t = [], [], 1, 5000.0
        self.page_event(4999, "layout", screen=list(OFFSET), photo=PHOTO, panel=[1450, 60, 1690, 400], dpr=1,
                        scale=SCALE)

    def page_event(self, host, event, **fields):
        self.page.append({"t": (host - VM_TO_HOST) * 1000, "type": event, **fields})

    def act(self, actions, shot="s1", done=None, text=None, compact=False):
        """An act call. With text, its result has only that text, as hyprmasq-mcp answers Claude Code. compact sends
        the actions in act's array form and names only the shot, not the window, as agents do."""
        self.id += 1
        # The page's events for the actions come 0.5 s apart.
        start, end = self.t, self.t + 1 + 0.5 * len(actions)
        arguments = {"window": WINDOW, "shot": shot, "actions": actions}
        if compact:
            del arguments["window"]
            arguments["actions"] = [compact_action(a) for a in actions]
        self.tap.append({"t": start, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": "act", "arguments": arguments}}})
        sc = {"actions": {"done": len(actions) if done is None else done, "total": len(actions)}}
        result = {"content": [{"type": "text", "text": text}]} if text else {
            "content": [{"type": "text", "text": f"{len(actions)}/{len(actions)} delivered"}], "structuredContent": sc}
        self.tap.append({"t": end, "dir": "response", "msg": {"id": self.id, "result": result}})
        self.t = end + 2
        return start

    def call(self, name, arguments, text):
        """A call of another tool whose result has only text."""
        self.id += 1
        self.tap.append({"t": self.t, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": name, "arguments": arguments}}})
        self.tap.append({"t": self.t + 0.2, "dir": "response", "msg": {"id": self.id, "result": {
            "content": [{"type": "text", "text": text}, {"type": "image", "bytes": 1}]}}})
        self.t += 1

    def screenshot(self):
        self.id += 1
        self.tap.append({"t": self.t, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": "screenshot", "arguments": {"window": WINDOW}}}})
        self.tap.append({"t": self.t + 0.2, "dir": "response", "msg": {"id": self.id, "result": {
            "content": [], "structuredContent": {"shot": {"id": "s1", "width": 1280, "height": 720,
                                                          "frame": {"x": 0, "y": 0, "width": 1920, "height": 1080}}}}}})
        self.t += 1

    def drags(self, segments, landed=None, seen=None, done=None, text=None, compact=False):
        """One act call with a drag per segment of photo points, and the strokes the page draws for them. landed maps
        a segment's index to the client point where its stroke ended instead; seen leaves out the presses of the
        segments it does not hold."""
        actions = [{"action": "drag", "from": shot_of_photo(a), "to": shot_of_photo(b)} for a, b in segments]
        start = self.act(actions, done=done, text=text, compact=compact)
        for n, (a, b) in enumerate(segments[:len(segments) if done is None else done]):
            if seen is not None and n not in seen:
                continue
            t = start + 0.1 + 0.5 * n
            ca, cb = client_of_photo(a), (landed or {}).get(n) or client_of_photo(b)
            self.page_event(t, "pointerdown", x=ca[0], y=ca[1], over="photo", shift=False)
            self.page_event(t + 0.3, "pointerup", x=cb[0], y=cb[1], over="photo", shift=False)
            points = [a, [round((cb[0] - PHOTO[0]) / SCALE, 1), round((cb[1] - PHOTO[1]) / SCALE, 1)]]
            self.page_event(t + 0.3, "stroke", kind="brush", **{"from": ca}, to=cb, points=points)

    def clicks(self, points, shift=False, text=None, compact=False, modifiers=None):
        """One act call with a click per point, in photo pixels or, for a list of four, a button's box in client
        pixels, and what the page does: a dot, a line from the last stroke's end with shift, nothing on a button."""
        actions, clients = [], []
        for p in points:
            client = [(p[0] + p[2]) / 2, (p[1] + p[3]) / 2] if len(p) == 4 else client_of_photo(p)
            clients.append((client, len(p) == 4))
            action = {"action": "click", "x": shot_of_client(client)[0], "y": shot_of_client(client)[1]}
            if shift:
                action["modifiers"] = modifiers or ["shift"]
            actions.append(action)
        start = self.act(actions, text=text, compact=compact)
        for n, (client, button) in enumerate(clients):
            t = start + 0.1 + 0.5 * n
            self.page_event(t, "pointerdown", x=client[0], y=client[1], over="panel" if button else "photo",
                            shift=shift)
            if button:
                self.page_event(t + 0.05, "undo", strokes=0)
                continue
            point = [round((client[0] - PHOTO[0]) / SCALE, 1), round((client[1] - PHOTO[1]) / SCALE, 1)]
            self.page_event(t + 0.05, "stroke", kind="line" if shift else "brush", **{"from": client}, to=client,
                            points=[point, point])

    def write(self, d, final=None):
        if final is not None:
            with open(os.path.join(d, "vm-trace-strokes.json"), "w") as f:
                json.dump({"strokes": final}, f)
        with open(os.path.join(d, "run.json"), "w") as f:
            json.dump({"variant": os.path.join("testdata", "line")}, f)
        with open(os.path.join(d, "ready.json"), "w") as f:
            json.dump({"task_window": WINDOW, "vm_time": 1000.0, "host_time": 1000.0 + VM_TO_HOST}, f)
        for name, lines in (("mcp.jsonl", self.tap), (attribute.EVENTS, self.page)):
            with open(os.path.join(d, name), "w") as f:
                f.writelines(json.dumps(line) + "\n" for line in lines)
        return d


def compact_action(a):
    """An action in act's array form: a plain click as a point pair, others as their name and fields in order."""
    if a["action"] == "click" and "modifiers" not in a:
        return [a["x"], a["y"]]
    if a["action"] == "click":
        return ["click", a["x"], a["y"], "left", 1, a["modifiers"]]
    return ["drag", a["from"], a["to"]]


def segments_of(n):
    return [s["points"] for s in chords(n)]


class Attribute(unittest.TestCase):
    def run_of(self, build, first=Run.screenshot, final=None):
        run = Run()
        first(run)
        build(run)
        with tempfile.TemporaryDirectory(prefix="trace-attribution-") as d:
            return attribute.attribute(run.write(d, final))

    def test_chained_drags_in_one_call(self):
        out = self.run_of(lambda r: r.drags(segments_of(12)))
        self.assertEqual(out["calibration"]["offset"], [0, 85.5])
        self.assertEqual([s["outcome"] for s in out["strokes"]], ["on the line"] * 12)
        self.assertEqual(out["summary"]["strokes_per_call"], [12])
        self.assertEqual(out["summary"]["actions"], {"drag": 12})
        self.assertEqual(out["summary"]["max_landed_off"], 0)
        self.assertEqual(out["strokes"][0]["aimed_photo"], [CENTRE[0], segments_of(12)[0][1]])

    def test_a_chord_across_the_bend_is_the_models(self):
        # Segments 8 to 10 of 12, near the top, as one straight drag.
        segments = segments_of(12)[:8] + [[segments_of(12)[8][0], segments_of(12)[10][1]]] + segments_of(12)[11:]
        out = self.run_of(lambda r: r.drags(segments))
        stroke = out["strokes"][8]
        self.assertEqual((stroke["outcome"], stroke["blame"]), ("model", "model"))
        self.assertIn("off the line", stroke["why"])
        self.assertEqual(out["summary"]["model"], 1)

    def test_a_stroke_that_ends_far_from_where_aimed_is_hyprmasqs(self):
        segments = segments_of(12)
        end = client_of_photo(segments[3][1])
        out = self.run_of(lambda r: r.drags(segments, landed={3: [end[0] + 30, end[1]]}))
        stroke = out["strokes"][3]
        self.assertEqual((stroke["outcome"], stroke["blame"]), ("hyprmasq", "hyprmasq"))
        self.assertEqual(stroke["landed_off"], 30)
        self.assertEqual([s["outcome"] for s in out["strokes"][4:]], ["on the line"] * 8)

    def test_a_press_the_page_never_saw_is_hyprmasqs(self):
        out = self.run_of(lambda r: r.drags(segments_of(6), seen={0, 1, 3, 4, 5}))
        self.assertEqual(out["strokes"][2]["outcome"], "hyprmasq")
        self.assertEqual(out["strokes"][2]["why"], "the page saw no press where the action aimed")
        self.assertNotIn("hyprmasq", [s["blame"] for s in out["strokes"][3:]])

    def test_shift_clicks_and_undo(self):
        def build(r):
            r.clicks([CENTRE[0]])
            r.clicks([p for p in CENTRE[20::20]] + [CENTRE[-1]], shift=True)
            r.clicks([UNDO])
        out = self.run_of(build)
        self.assertEqual(out["summary"]["actions"], {"click": 1, "shift+click": len(CENTRE[20::20]) + 1})
        self.assertEqual(out["summary"]["strokes_per_call"], [1, len(CENTRE[20::20]) + 1])
        self.assertEqual(out["summary"]["undos"], 1)
        self.assertEqual({s["outcome"] for s in out["strokes"]}, {"on the line"})

    def test_a_stroke_taken_back_and_drawn_again_the_same_is_kept_once(self):
        segments = segments_of(12)

        def build(r):
            r.drags(segments)
            r.clicks([UNDO])
            r.drags(segments[-1:])
        final = [{"kind": "brush", "points": [a, b]} for a, b in segments]
        out = self.run_of(build, final=final)
        self.assertEqual([s["kept"] for s in out["strokes"]], [True] * 11 + [False, True])

    def test_a_shift_click_on_an_empty_drawing_is_a_dot(self):
        def build(r):
            start = r.act([{"action": "click", "modifiers": ["shift"], "x": shot_of_photo(CENTRE[0])[0],
                            "y": shot_of_photo(CENTRE[0])[1]}])
            c = client_of_photo(CENTRE[0])
            r.page_event(start + 0.1, "pointerdown", x=c[0], y=c[1], over="photo", shift=True)
            r.page_event(start + 0.2, "stroke", kind="brush", **{"from": c}, to=c, points=[CENTRE[0]])
        out = self.run_of(build)
        self.assertEqual([(s["outcome"], s["kind"]) for s in out["strokes"]], [("on the line", "brush")])

    def test_a_drag_with_shift_draws_a_line_to_its_press(self):
        def build(r):
            r.drags(segments_of(12)[:1])
            a, b = CENTRE[60], CENTRE[90]
            start = r.act([{"action": "drag", "from": shot_of_photo(a), "to": shot_of_photo(b), "modifiers": ["shift"]}])
            ca = client_of_photo(a)
            r.page_event(start + 0.1, "pointerdown", x=ca[0], y=ca[1], over="photo", shift=True)
            r.page_event(start + 0.1, "stroke", kind="line", **{"from": ca}, to=ca, points=[segments_of(12)[0][1], a])
        out = self.run_of(build)
        self.assertEqual([(s["kind"], s["outcome"]) for s in out["strokes"]], [("brush", "on the line"), ("line", "on the line")])

    def test_each_press_of_a_double_click_draws_a_dot(self):
        def build(r):
            c = client_of_photo(CENTRE[40])
            start = r.act([{"action": "double_click", "x": shot_of_client(c)[0], "y": shot_of_client(c)[1]}])
            for n in range(2):
                r.page_event(start + 0.1 + n / 10, "pointerdown", x=c[0], y=c[1], over="photo", shift=False)
                r.page_event(start + 0.15 + n / 10, "stroke", kind="brush", **{"from": c}, to=c, points=[CENTRE[40]])
        out = self.run_of(build)
        self.assertEqual([s["outcome"] for s in out["strokes"]], ["on the line", "on the line"])

    def test_a_refused_action_without_its_points_is_skipped(self):
        out = self.run_of(lambda r: r.act([{"action": "drag", "from": [10, 10]}, {"action": "click"}], done=0))
        self.assertEqual(out["strokes"], [])

    def test_other_buttons_draw_nothing(self):
        def build(r):
            r.drags(segments_of(12))
            r.act([{"action": "click", "x": shot_of_photo(CENTRE[50])[0], "y": shot_of_photo(CENTRE[50])[1], "button": "right"}])
        out = self.run_of(build)
        self.assertEqual(len(out["strokes"]), 12)
        self.assertEqual(out["summary"]["hyprmasq"], 0)

    def test_shift_in_any_case_draws_a_line(self):
        def build(r):
            r.clicks([CENTRE[0]])
            r.clicks([CENTRE[60]], shift=True, modifiers=["Shift"])
        out = self.run_of(build)
        self.assertEqual(out["summary"]["actions"], {"click": 1, "shift+click": 1})

    def test_strokes_taken_back_are_not_kept(self):
        # A chord across the bend, undone, and the bend drawn again in three drags.
        segments = segments_of(12)
        across = [segments[8][0], segments[10][1]]

        def build(r):
            r.drags(segments[:8] + [across])
            r.clicks([UNDO])
            r.drags(segments[8:])
        final = [{"kind": "brush", "points": [a, b]} for a, b in segments]
        out = self.run_of(build, final=final)
        self.assertEqual([s["kept"] for s in out["strokes"]], [True] * 8 + [False] + [True] * 4)
        self.assertEqual(out["summary"]["not_kept"], 1)
        self.assertEqual(out["summary"]["strokes_per_call"], [9, 4])

    def test_actions_in_array_form_on_the_shots_window(self):
        def build(r):
            r.drags(segments_of(12)[:6], compact=True)
            r.clicks([CENTRE[0]], compact=True)
            r.clicks([p for p in CENTRE[120::30]] + [CENTRE[-1]], shift=True, compact=True)
        out = self.run_of(build)
        self.assertEqual(out["summary"]["actions"], {"drag": 6, "click": 1, "shift+click": len(CENTRE[120::30]) + 1})
        self.assertEqual({s["outcome"] for s in out["strokes"]}, {"on the line"})

    def test_paths_draw_and_hover_paths_do_not(self):
        def build(r):
            # A spline through 8 points of the centre line, as one stroke, after a hover path along it.
            points = [CENTRE[i] for i in range(0, len(CENTRE), 34)] + [CENTRE[-1]]
            shot_points = [shot_of_photo(p) for p in points]
            start = r.act([{"action": "path", "points": shot_points, "button": "none"},
                           ["path", shot_points, "spline"]])
            first, last = client_of_photo(points[0]), client_of_photo(points[-1])
            r.page_event(start + 0.6, "pointerdown", x=first[0], y=first[1], over="photo", shift=False)
            r.page_event(start + 0.9, "pointerup", x=last[0], y=last[1], over="photo", shift=False)
            r.page_event(start + 0.9, "stroke", kind="brush", **{"from": first}, to=last, points=CENTRE)
        out = self.run_of(build)
        self.assertEqual(out["summary"]["actions"], {"spline path": 1})
        self.assertEqual([s["outcome"] for s in out["strokes"]], ["on the line"])

    def test_refused_batch(self):
        out = self.run_of(lambda r: r.drags(segments_of(4), done=0))
        self.assertEqual([s["outcome"] for s in out["strokes"]], ["not sent"] * 4)

    def test_drags_with_results_of_only_text(self):
        windows = (f'{WINDOW} · firefox "Figure 2: The line · A figure for the tests — Mozilla Firefox" · '
                   f"workspace web (hidden) · 1920×1080 · lease 60 s (this session)\nshot s1 1280×720 of window "
                   f"{WINDOW}")
        out = self.run_of(lambda r: r.drags(segments_of(12), text="12/12 delivered · redraw observed · quiet 200 ms · "
                                                                    "6100 ms · shot s2 = s1 + 1 change"),
                          first=lambda r: r.call("windows", {"match": "firefox"}, windows))
        self.assertEqual([s["outcome"] for s in out["strokes"]], ["on the line"] * 12)


if __name__ == "__main__":
    unittest.main()
