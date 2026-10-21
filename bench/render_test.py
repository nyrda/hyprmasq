"""Tests that render.py fills a page's blocks from a dataset and that --check catches a page or figure out of step
with it: python3 render_test.py"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402


def run(model, rep, passed, wall, tools):
    return {"run": f"r/{model}-{rep}", "task": "board", "harness": "codex", "model": model, "effort": "medium",
            "ran_at": "medium", "rep": rep, "date": "2026-10-19", "harness_version": "x", "passed": passed,
            "wall_s": wall, "tools_s": tools, "turn_s": 3.0, "calls": 5, "actions": 9, "images": 7, "errors": 0,
            "input_tokens": 100000, "cache_read_tokens": 90000, "output_tokens": 1000, "cost_usd": 0.05,
            "user_undisturbed": True, "phases": {}, "act_s": {"input": 4, "wait": 2, "watch": 1, "rest": 3}}


PAGE = """# Results

Handwritten text stays.

<!-- render set scoreboard -->
stale content
<!-- /render -->

<!-- render set table board -->
<!-- /render -->
"""


def main(*args):
    with contextlib.redirect_stdout(io.StringIO()):
        render.main(list(args))


class Page(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.dir.name, "benchmarks", "data"))
        runs = [run("gpt-6.1-sol", 1, True, 50, 12), run("gpt-6.1-sol", 2, False, 60, 13),
                run("claude-opus-5-5", 1, True, 40, 11)]
        with open(os.path.join(self.dir.name, "benchmarks", "data", "set.json"), "w") as f:
            json.dump({"sources": [], "runs": runs}, f)
        self.page = os.path.join(self.dir.name, "results.md")
        with open(self.page, "w") as f:
            f.write(PAGE)

    def tearDown(self):
        self.dir.cleanup()

    def read(self, *path):
        with open(os.path.join(self.dir.name, *path)) as f:
            return f.read()

    def check_fails(self):
        with self.assertRaises(SystemExit) as cm:
            main("--check", self.page)
        self.assertIn("out of date", str(cm.exception))

    def test_blocks_are_filled_and_check_follows_the_files(self):
        self.check_fails()
        main(self.page)
        page = self.read("results.md")
        self.assertTrue(page.startswith("# Results\n\nHandwritten text stays.\n\n<!-- render set scoreboard -->\n"))
        self.assertNotIn("stale content", page)
        self.assertIn('srcset="benchmarks/set-scoreboard-dark.svg"', page)
        self.assertIn('src="benchmarks/set-scoreboard.svg"', page)
        self.assertIn("| gpt-6.1-sol | medium | 1/2 | 55 (50–60) |", page)
        self.assertIn("<svg", self.read("benchmarks", "set-scoreboard-dark.svg"))
        main("--check", self.page)

        with open(os.path.join(self.dir.name, "benchmarks", "set-scoreboard.svg"), "a") as f:
            f.write("edited")
        self.check_fails()
        main(self.page)
        main("--check", self.page)

        with open(os.path.join(self.dir.name, "benchmarks", "set-time.svg"), "w") as f:
            f.write("<svg/>")
        self.check_fails()
        main(self.page)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "benchmarks", "set-time.svg")))
        main("--check", self.page)


if __name__ == "__main__":
    unittest.main()
