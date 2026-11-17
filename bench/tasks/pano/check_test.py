"""Tests the pano task's grader, its viewer's server and its attribution on constructed states and logs:
python3 check_test.py"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attribute  # noqa: E402
import check  # noqa: E402
import viewer  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BORKUM = os.path.join(HERE, "borkum")


def variant(**changes):
    """A variant's directory with the default variant's view.json, changed."""
    d = tempfile.mkdtemp()
    with open(os.path.join(BORKUM, "view.json")) as f:
        view = json.load(f)
    with open(os.path.join(d, "view.json"), "w") as f:
        json.dump({**view, **changes}, f)
    return d


class Grade(unittest.TestCase):
    """The default variant: the cargo ship at 73.1°, within 3°."""

    def setUp(self):
        check.load(BORKUM)

    def test_the_ship_in_the_centre_passes(self):
        for saved in (73.1, 70.1, 76.1, 72.0):
            result = check.grade({"view": saved, "saved": saved})
            self.assertTrue(result["passed"], result)
            self.assertEqual(result["failures"], [])
        self.assertEqual(check.grade({"view": 10, "saved": 74.6})["checks"]["off_deg"], 1.5)

    def test_beside_the_ship_fails_and_says_which_side(self):
        result = check.grade({"view": 76.3, "saved": 76.3})
        self.assertFalse(result["passed"])
        self.assertEqual(result["failures"], ["the centre of the saved opening view is 3.2° to the right of the cargo "
                                              "ship: it is at 76.3°, and the cargo ship at 73.1° (within 3° passes)"])
        result = check.grade({"view": 250, "saved": 64.5})
        self.assertIn("8.6° to the left of the cargo ship", result["failures"][0])
        self.assertEqual(result["checks"]["off_deg"], -8.6)

    def test_a_view_that_is_not_saved_fails(self):
        result = check.grade({"view": 73.1, "saved": None})
        self.assertEqual((result["passed"], result["failures"]), (False, ["no opening view is saved"]))
        self.assertEqual((result["checks"]["off_deg"], result["checks"]["view_off_deg"]), (None, 0))

    def test_the_view_is_not_graded_once_another_is_saved(self):
        self.assertTrue(check.grade({"view": 200, "saved": 73})["passed"])
        self.assertFalse(check.grade({"view": 73, "saved": 200})["passed"])

    def test_directions_count_the_shorter_way_round(self):
        self.addCleanup(check.load, BORKUM)
        check.load(variant(target_heading=359, tolerance=3))
        self.assertTrue(check.grade({"view": 1.5, "saved": 1.5})["passed"])
        self.assertEqual(check.off(1.5), 2.5)
        self.assertEqual(check.off(356.5), -2.5)
        self.assertFalse(check.grade({"view": 2.5, "saved": 2.5})["passed"])
        self.assertEqual(check.off(179), -180)

    def test_how_the_view_was_turned(self):
        page = [{"type": "keydown", "key": "ArrowLeft", "repeat": False}, {"type": "keydown", "key": "ArrowLeft", "repeat": True},
                {"type": "keydown", "key": "ArrowLeft", "repeat": True},
                {"type": "turn", "key": "left", "fast": True, "ms": 1960.0, "from": 250, "to": 73.6},
                {"type": "turn", "key": "left", "fast": False, "ms": 5.0, "from": 73.6, "to": 73.45},
                {"type": "save_released", "held_ms": 1200.0}, {"type": "save", "heading": 73.45, "held_ms": 2000}]
        checks = check.grade({"view": 73.45, "saved": 73.45}, page)["checks"]
        self.assertEqual({k: checks[k] for k in ("turns", "fast", "taps", "held_ms", "longest_ms", "autorepeats", "early_saves")},
                         {"turns": 2, "fast": 1, "taps": 1, "held_ms": 1965.0, "longest_ms": 1960.0, "autorepeats": 2,
                          "early_saves": 1})
        self.assertEqual(check.grade({"view": 250, "saved": None})["checks"]["longest_ms"], None)

    def test_milestones(self):
        views = [{"time": 5, "heading": 73}]  # before the agent started
        views += [{"time": 10, "heading": 160}, {"time": 12, "heading": 74}, {"time": 20, "heading": 90}]
        saves = [{"time": 11, "heading": 160}, {"time": 15, "heading": 74}, {"time": 21, "heading": 90}]
        self.assertEqual(list(check.events(views, saves, since=8).items()),
                         [("first turn", 10), ("target centred", 12), ("view saved", 15), ("last change", 21)])
        self.assertEqual(list(check.events(views[:2], saves[:1], since=8).items()), [("first turn", 10), ("last change", 11)])
        self.assertEqual(check.events([], [], since=8), {})


class Server(unittest.TestCase):
    def setUp(self):
        viewer.load(BORKUM)
        viewer.STATE_DIR = tempfile.mkdtemp()

    def test_it_starts_at_the_variants_view_with_nothing_saved(self):
        self.assertEqual(viewer.load_state(), {"view": 250, "saved": None})
        check.load(BORKUM)
        self.assertFalse(check.on_target(viewer.load_state()["view"]))
        # The target is out of the view that the viewer starts at.
        self.assertGreater(abs(check.off(viewer.VIEW["start"])), viewer.FOV / 2)

    def test_a_turn_moves_the_view_and_a_save_keeps_it(self):
        self.assertEqual(viewer.record("view", 120.5, now=10), {"view": 120.5, "saved": None})
        self.assertEqual(viewer.record("save", 73.25, now=11), {"view": 73.25, "saved": 73.25})
        self.assertEqual(viewer.record("view", 80, now=12), {"view": 80, "saved": 73.25})
        self.assertEqual(viewer.load_state(), {"view": 80, "saved": 73.25})
        read = lambda name: check.load_jsonl(os.path.join(viewer.STATE_DIR, name))  # noqa: E731
        self.assertEqual(read("views.jsonl"), [{"time": 10, "heading": 120.5}, {"time": 12, "heading": 80}])
        self.assertEqual(read("saves.jsonl"), [{"time": 11, "heading": 73.25}])

    def test_it_takes_only_directions_the_page_sends(self):
        for good in (0, 73.1, 359.999):
            self.assertTrue(viewer.valid_heading(good), good)
        for bad in (360, -0.1, "73", None, True, [73]):
            self.assertFalse(viewer.valid_heading(bad), bad)

    def test_the_page_names_its_keys_and_speeds(self):
        page = viewer.page_html()
        self.assertIn("30° a second, or 90° with <kbd>Shift</kbd>", page)
        self.assertIn("Hold <kbd>S</kbd> for 2 seconds", page)
        config = json.loads(page.split("const CONFIG = ", 1)[1].split(";\n", 1)[0])
        self.assertEqual(config, {"width": 4800, "height": 601, "fov": 100, "speed": 30, "fast": 90, "saveMs": 2000,
                                  "start": 250, "photo": "/posts/borkum-from-the-lighthouse/photo.jpg"})

    def test_the_photo_is_the_size_the_variant_says(self):
        # A JPEG's size is in its first frame header, SOF0 or SOF2: height and width after the sample precision.
        with open(os.path.join(BORKUM, viewer.VIEW["image"]), "rb") as f:
            data = f.read()
        at = next(i for i in range(len(data) - 9) if data[i] == 0xFF and data[i + 1] in (0xC0, 0xC2))
        height, width = int.from_bytes(data[at + 5:at + 7], "big"), int.from_bytes(data[at + 7:at + 9], "big")
        self.assertEqual([width, height], viewer.VIEW["size"])


WINDOW = "0x1234"
VM_TO_HOST = 4000.0


class Run:
    """A run directory under construction: act calls in mcp.jsonl, and the page's key events."""

    def __init__(self):
        self.tap, self.page, self.id, self.t = [], [], 1, 5000.0

    def press(self, host, key, ms, repeats=0):
        """The page sees a key go down at a host time and come up ms later, or not at all with ms None."""
        t = (host - VM_TO_HOST) * 1000
        self.page.append({"t": t, "type": "keydown", "key": key, "repeat": False})
        self.page += [{"t": t + 600 + 40 * n, "type": "keydown", "key": key, "repeat": True} for n in range(repeats)]
        if ms is not None:
            self.page.append({"t": t + ms, "type": "keyup", "key": key})

    def act(self, actions, done=None, text=None, window=WINDOW, seconds=3.0):
        """An act call. With text, its result has only that text, as hyprmasq-mcp answers Claude Code."""
        self.id += 1
        start, end = self.t, self.t + seconds
        self.tap.append({"t": start, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": "act", "arguments": {"window": window, "actions": actions}}}})
        sc = {"actions": {"done": len(actions) if done is None else done, "total": len(actions)}}
        result = {"content": [{"type": "text", "text": text}]} if text else {
            "content": [{"type": "text", "text": f"{len(actions)}/{len(actions)} delivered"}], "structuredContent": sc}
        self.tap.append({"t": end, "dir": "response", "msg": {"id": self.id, "result": result}})
        self.t = end + 2
        return start

    def write(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "run.json"), "w") as f:
            json.dump({"variant": "borkum"}, f)
        with open(os.path.join(d, "ready.json"), "w") as f:
            json.dump({"task_window": WINDOW, "vm_time": 1000.0, "host_time": 1000.0 + VM_TO_HOST}, f)
        for name, lines in (("mcp.jsonl", self.tap), (attribute.EVENTS, self.page)):
            with open(os.path.join(d, name), "w") as f:
                f.writelines(json.dumps(line) + "\n" for line in lines)
        return d


class Attribute(unittest.TestCase):
    def run_of(self, build):
        r = Run()
        build(r)
        d = r.write()
        out = attribute.attribute(d)
        with open(os.path.join(d, "attribution.json")) as f:
            self.assertEqual(json.load(f), out)
        # The table has a row for every key.
        table = io.StringIO()
        with contextlib.redirect_stdout(table):
            attribute.show(d, out)
        self.assertEqual(table.getvalue().count("\n| "), 2 + len(out["keys"]), table.getvalue())
        return out

    def test_holds_that_the_page_saw_for_their_time(self):
        def build(r):
            start = r.act([["hold", ["shift", "left"], 1960], ["hold", "left", 150]])
            r.press(start + 0.1, "Shift", 1975)
            r.press(start + 0.11, "ArrowLeft", 1961, repeats=30)
            r.press(start + 2.2, "ArrowLeft", 148.5)
            start = r.act([{"action": "hold", "keys": ["s"], "ms": 2200}])
            r.press(start + 0.1, "s", 2203, repeats=40)

        out = self.run_of(build)
        self.assertEqual([(k["keys"], k["asked_ms"], k["seen_ms"], k["off_ms"], k["outcome"], k["blame"]) for k in out["keys"]],
                         [("shift+left", 1960, 1961, 1, "held", None), ("left", 150, 148.5, -1.5, "held", None),
                          ("s", 2200, 2203, 3, "held", None)])
        self.assertEqual(out["keys"][0]["repeats"], 30)
        self.assertEqual(out["summary"], {"holds": 3, "holds_sent": 3, "held": 3, "chords": 1, "asked_ms": 4310,
                                          "key_actions": 0, "key_presses": 0, "act_calls_holding": 2, "hyprmasq": 0,
                                          "median_off_ms": 1.5, "max_off_ms": 3,
                                          "other_presses": 0})

    def test_a_hold_that_the_page_saw_for_another_time_or_not_at_all_is_hyprmasqs(self):
        def build(r):
            start = r.act([["hold", "right", 1000]])
            r.press(start + 0.1, "ArrowRight", 620)
            r.act([["hold", "right", 500]])
            start = r.act([["hold", "right", 800]])
            r.press(start + 0.1, "ArrowRight", None)

        out = self.run_of(build)
        self.assertEqual([(k["outcome"], k["blame"]) for k in out["keys"]],
                         [("held another time", "hyprmasq"), ("not seen", "hyprmasq"), ("not released", "hyprmasq")])
        self.assertEqual(out["keys"][0]["why"], "the page saw the key down for 620 ms, not 1000 ms")
        self.assertEqual((out["summary"]["hyprmasq"], out["summary"]["held"], out["summary"]["max_off_ms"]), (3, 0, 380))

    def test_holds_of_a_refused_batch_were_not_sent(self):
        def build(r):
            r.act([["hold", "right", 5000], ["hold", "right", 5000]], done=0,
                  text="0/2 delivered, nothing sent\nerror invalid_arguments: the batch takes at least 10050 ms to send")
            # Only the first action of a batch that stopped went out.
            start = r.act([["hold", "left", 300], ["key", "enter"], ["hold", "left", 300]], done=1,
                          text="1/3 delivered, delivery partial: actions[1] (key) may be partly applied")
            r.press(start + 0.1, "ArrowLeft", 301)

        out = self.run_of(build)
        self.assertEqual([k["outcome"] for k in out["keys"]], ["not sent", "not sent", "held", "not sent", "not sent"])
        self.assertEqual(out["keys"][0]["why"], "0/2 delivered, nothing sent")
        self.assertEqual((out["summary"]["holds"], out["summary"]["holds_sent"], out["summary"]["asked_ms"]), (4, 1, 300))

    def test_keys_that_are_pressed_ask_for_no_time(self):
        def build(r):
            start = r.act([["key", "right", 3], {"action": "key", "keys": ["shift+Right"]}])
            for n in range(3):
                r.press(start + 0.1 + n * 0.01, "ArrowRight", n)
            r.press(start + 0.5, "Shift", 40)
            r.press(start + 0.51, "ArrowRight", 15)
            r.act([["key", "left"]])
            # What the user types into the page is no action's.
            r.press(r.t, "x", 80)

        out = self.run_of(build)
        self.assertEqual([(k["keys"], k["asked_ms"], k.get("seen_ms"), k["outcome"], k["blame"]) for k in out["keys"]],
                         [("right", None, [0, 1, 2], "pressed", None), ("shift+right", None, [15], "pressed", None),
                          ("left", None, [], "not seen", "hyprmasq")])
        self.assertEqual(out["keys"][0]["presses"], 3)
        self.assertEqual((out["summary"]["holds"], out["summary"]["key_actions"], out["summary"]["key_presses"]), (0, 3, 4))
        self.assertEqual(out["summary"]["other_presses"], 1)

    def test_the_hold_of_anthropics_tool_and_the_keys_of_other_windows(self):
        def build(r):
            start = r.act([{"action": "hold_key", "text": "shift+Left", "duration": 1.5}])
            r.press(start + 0.1, "ArrowLeft", 1502)
            # Keys to another window are not the task's, and the page's press then is no action's.
            start = r.act([["hold", "left", 400]], window="0x9999")
            r.press(start + 0.1, "ArrowLeft", 400)

        out = self.run_of(build)
        self.assertEqual([(k["action"], k["keys"], k["asked_ms"], k["off_ms"], k["outcome"]) for k in out["keys"]],
                         [("hold_key", "shift+left", 1500, 2, "held")])
        self.assertEqual(out["summary"]["other_presses"], 1)

    def test_repeats_that_the_browser_does_not_mark_are_no_presses(self):
        def build(r):
            start = r.act([["hold", "left", 1000]])
            r.press(start + 0.1, "ArrowLeft", 1002, repeats=10)
            for e in r.page:
                e.pop("repeat", None)

        out = self.run_of(build)
        self.assertEqual([(k["seen_ms"], k["repeats"], k["outcome"]) for k in out["keys"]], [(1002, 10, "held")])
        self.assertEqual(out["summary"]["other_presses"], 0)

    def test_a_hold_that_the_page_never_saw_takes_no_press_of_the_next_call(self):
        def build(r):
            r.act([["hold", "left", 400]])
            # The next call starts half a second after the first one's end, within the slack of both.
            r.t -= 1.5
            start = r.act([["hold", "left", 400]])
            r.press(start + 0.1, "ArrowLeft", 401)

        out = self.run_of(build)
        self.assertEqual([(k["outcome"], k["blame"]) for k in out["keys"]], [("not seen", "hyprmasq"), ("held", None)])

    def test_a_key_that_was_down_when_the_window_lost_the_keyboard_is_pressed_anew(self):
        def build(r):
            start = r.act([["hold", "left", 800]])
            r.press(start + 0.1, "ArrowLeft", None)
            r.page.append({"t": (start + 0.5 - VM_TO_HOST) * 1000, "type": "blur"})
            start = r.act([["hold", "left", 300]])
            r.press(start + 0.1, "ArrowLeft", 300)

        out = self.run_of(build)
        self.assertEqual([(k["outcome"], k.get("seen_ms")) for k in out["keys"]], [("not released", None), ("held", 300)])

    def test_actions_that_are_no_actions_are_skipped(self):
        def build(r):
            r.act([{"action": []}, [["hold"], "left", 1], {"action": {"hold": 1}}, "hold", 5, None, ["hold", 7, "x"],
                   {"action": "hold", "keys": [1, 2], "ms": "long"}], done=0, text="0/8 delivered, nothing sent")
            start = r.act([["hold", "left", 400]])
            r.press(start + 0.1, "ArrowLeft", 400)

        self.assertEqual([k["outcome"] for k in self.run_of(build)["keys"]], ["held"])

    def test_presses_outside_the_call_are_not_its_actions(self):
        def build(r):
            start = r.act([["hold", "left", 400]])
            r.press(start - 3, "ArrowLeft", 400)

        self.assertEqual(self.run_of(build)["keys"][0]["outcome"], "not seen")


if __name__ == "__main__":
    unittest.main()
