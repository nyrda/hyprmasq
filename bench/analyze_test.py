"""Tests that analyze.py splits an act call's time from its result's text, as hyprmasq-mcp answers Claude Code, as it
does from the structured content: python3 analyze_test.py"""

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402


def both(actions, text, structured):
    """The split of one act call from its structured content, and from its text alone."""
    lines = text.split("\n")
    call = {"summary": lines[0], "hint": "\n".join(lines[1:]), "seconds": 2.0}
    args = {"actions": actions}
    return (analyze.split(call, args, structured, 500), analyze.split(call, args, {}, 500))


class SplitFromText(unittest.TestCase):
    def test_compact_actions_keep_the_report_and_pacing(self):
        compact = [["click", 10, 20], ["key", "right", 100]]
        named = [{"action": "click", "x": 10, "y": 20},
                 {"action": "key", "keys": ["right"], "repeat": 100}]
        summary = "2/2 delivered · redraw observed · quiet 200 ms · 200 ms · shot s2 640×400"
        structured = {"actions": {"done": 2, "total": 2, "delivery": "complete"},
                      "wait": {"ended": "quiet", "elapsed_ms": 200}}
        self.assertEqual(both(compact, summary, structured), both(named, summary, structured))
        points = [[10, 20], ["key", "right", 100]]
        self.assertEqual(both(points, summary, structured), both(named, summary, structured))
        calls = analyze.report.tool_calls([
            {"dir": "request", "t": 1, "msg": {"id": 1, "method": "tools/call", "params": {
                "name": "act", "arguments": {"shot": "s1", "actions": points, "next_page": {"x": 100, "y": 200}}}}},
            {"dir": "response", "t": 3, "msg": {"id": 1, "result": {"content": []}}},
        ])
        self.assertEqual(calls[0]["actions"], ["click", "key", "scroll"])

    def test_keys_of_a_partly_typed_action(self):
        actions = [{"action": "type", "text": "hello world"}]
        from_structured, from_text = both(
            actions,
            "error window_gone: window 0x1 closed while actions[0] (type) was sent\n0/1 delivered, delivery partial: "
            "actions[0] (type \"hello world\") may be partly applied (4 keys sent) · window closed",
            {"actions": {"done": 0, "total": 1, "delivery": "partial", "progress": {"keys": 4}},
             "wait": {"ended": "window_closed", "elapsed_ms": 0}})
        self.assertEqual(from_text["input"], from_structured["input"])
        self.assertGreater(from_text["input"], 0)

    def test_wait_only_call_has_no_delivery_prefix(self):
        from_structured, from_text = both(
            [], "redraw observed · quiet 200 ms · 123 ms · no change",
            {"actions": {"done": 0, "total": 0}, "wait": {"ended": "quiet", "elapsed_ms": 123}})
        self.assertEqual(from_text, from_structured)
        self.assertEqual(from_text["wait"], 0.123)
        self.assertEqual(from_text["input"], 0)

    def test_no_watch_after_a_cancelled_call(self):
        actions = [{"action": "click", "x": 1, "y": 1}]
        from_structured, from_text = both(
            actions,
            "error cancelled: the client cancelled the call\n0/1 delivered, delivery partial · cancelled",
            {"actions": {"done": 0, "total": 1, "delivery": "partial"}, "wait": {"ended": "cancelled"}})
        self.assertEqual(from_text["watch"], 0)
        self.assertEqual(from_text, from_structured)

    def test_a_batch_that_ends_with_a_scroll_waits_twice(self):
        actions = [{"action": "click", "x": 1, "y": 1}, {"action": "scroll", "x": 1, "y": 1, "dy": 3}]
        from_structured, from_text = both(
            actions,
            "2/2 delivered · lease 60 s\nbefore the scroll: redraw observed · quiet 200 ms · 412 ms · shot s2 = s1 + 1 change\n"
            "zoom s3 140×120 of s2 at 150,50 140×120\nafter the scroll: redraw observed · quiet 200 ms · 230 ms · shot s4 640×400 "
            "(full shot: 58% changed since s2)",
            {"actions": {"done": 2, "total": 2, "delivery": "complete"}, "wait": {"ended": "quiet", "elapsed_ms": 230},
             "before_scroll": {"wait": {"ended": "quiet", "elapsed_ms": 412}}})
        self.assertEqual(from_text, from_structured)
        self.assertEqual(from_text["wait"], 0.642)

    def test_the_watch_after_a_split_batch_counts_from_its_last_wait(self):
        # Two waits of 800 ms, neither of which took the watch's 500 ms in.
        actions = [{"action": "click", "x": 1, "y": 1}, {"action": "scroll", "x": 1, "y": 1, "dy": 3}]
        from_structured, from_text = both(
            actions,
            "2/2 delivered · lease 60 s\nbefore the scroll: redraw observed · quiet 200 ms · 800 ms · shot s2 = s1 + 1 change\n"
            "zoom s3 140×120 of s2 at 150,50 140×120\nafter the scroll: redraw observed · quiet 200 ms · 800 ms · shot s4 640×400 "
            "(full shot: 58% changed since s2)",
            {"actions": {"done": 2, "total": 2, "delivery": "complete"}, "wait": {"ended": "quiet", "elapsed_ms": 800},
             "before_scroll": {"wait": {"ended": "quiet", "elapsed_ms": 800}}})
        self.assertEqual(from_text, from_structured)
        self.assertEqual(from_text["watch"], 0.5)


class CurrentProtocol(unittest.TestCase):
    def test_sender_and_server_pacing_constants_have_not_drifted(self):
        root = Path(__file__).resolve().parents[1]
        sender = (root / "tools/hyprmasq-input.c").read_text()
        names = ("ACTION_GAP_MS", "MOTION_PRESS_MS", "CLICK_HOLD_MS", "CLICK_GAP_MS", "CHORD_MOD_MS",
                 "CHORD_HOLD_MS", "CHORD_GAP_MS", "TYPE_HOLD_MS", "TYPE_GAP_MS", "WHEEL_GAP_MS",
                 "DRAG_HOLD_MS", "DRAG_STEP_MS", "DRAG_STEP_PX", "DRAG_MIN_STEPS")
        constants = dict(re.findall(r"^#define\s+(\w+)\s+(\d+)\b", sender, re.MULTILINE))
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(getattr(analyze, name), int(constants[name]))
        self.assertEqual(analyze.DRAG_MAX_STEPS, int(constants["MAX_DRAG_STEPS"]))
        server = (root / "mcp/internal/server/batch.go").read_text()
        for name, copied in (("clickSettleMs", analyze.CLICK_SETTLE_MS),
                             ("answerExpectMs", analyze.ANSWER_MS), ("answerQuietMs", analyze.ANSWER_MS)):
            with self.subTest(name=name):
                match = re.search(r"\b" + name + r"\s*=\s*(\d+)\b", server)
                self.assertIsNotNone(match, f"Missing server constant {name}")
                self.assertEqual(copied, int(match.group(1)))

    def test_current_watch_description_is_required(self):
        tool = {"name": "act", "description": "a redraw that starts within 500 ms after it settled"}
        tap = [{"msg": {"result": {"tools": [tool]}}}]
        self.assertEqual(analyze.act_tool(tap), 500)
        tool["description"] = "changed wording"
        with self.assertRaisesRegex(ValueError, "Unparseable act watch description"):
            analyze.act_tool(tap)
        with self.assertRaisesRegex(ValueError, "Missing act tool description"):
            analyze.act_tool([])

    def test_unparseable_delivery_and_wait_are_reported(self):
        for summary in ("new delivery wording", "1/1 delivered · new wait wording"):
            with self.subTest(summary=summary), self.assertRaisesRegex(ValueError, "Unparseable act"):
                analyze.split({"summary": summary, "hint": "", "seconds": 2},
                              {"actions": [{"action": "click"}]}, {}, 500)

    def test_no_time_left_to_wait_is_a_known_summary(self):
        result = analyze.split({"summary": "1/1 delivered · no time left to wait", "hint": "", "seconds": 2},
                               {"actions": [{"action": "type", "text": "a"}]}, {}, 500)
        self.assertEqual(result["wait"], 0)

    def test_plain_repeats_have_their_own_pace(self):
        self.assertEqual(analyze.pacing([{"action": "key", "keys": ["right"], "repeat": 100}]), (99, 0))
        self.assertEqual(analyze.pacing([{"action": "key", "keys": ["ctrl", "right"], "repeat": 2}]), (65, 0))

    def test_clicks_always_wait_for_the_answer(self):
        # move, click, sync, move, click: two normal gaps and the sync's 150 ms minimum.
        self.assertEqual(analyze.pacing([{"action": "click"}, {"action": "click"}]), (350, 0))


if __name__ == "__main__":
    unittest.main()
