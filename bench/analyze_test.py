#!/usr/bin/env python3
"""Tests that analyze.py splits an act call's time from its result's text, as hyprmasq-mcp answers Claude Code, as it
does from the structured content: python3 analyze_test.py"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze  # noqa: E402


def both(actions, text, structured):
    """The split of one act call from its structured content, and from its text alone."""
    lines = text.split("\n")
    call = {"summary": lines[0], "hint": "\n".join(lines[1:]), "seconds": 2.0}
    args = {"actions": actions}
    return (analyze.split(call, args, structured, 500, True), analyze.split(call, args, {}, 500, True))


class SplitFromText(unittest.TestCase):
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

    def test_no_watch_after_a_cancelled_call(self):
        actions = [{"action": "click", "x": 1, "y": 1}]
        from_structured, from_text = both(
            actions,
            "error cancelled: the client cancelled the call\n0/1 delivered, delivery partial · cancelled",
            {"actions": {"done": 0, "total": 1, "delivery": "partial"}, "wait": {"ended": "cancelled"}})
        self.assertEqual(from_text["watch"], 0)
        self.assertEqual(from_text, from_structured)


if __name__ == "__main__":
    unittest.main()
