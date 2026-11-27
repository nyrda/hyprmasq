"""Tests compare.py on small results directories, without runs: python3 compare_test.py"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compare  # noqa: E402


def table(text, agent, title):
    """A section of an agent's comparison: its table's rows by measure, each a row's cells by column, and its text."""
    agent_part = text.split(f"## {agent}\n")[1].split("\n## ")[0]
    part = agent_part.split(f"### {title}\n")[1].split("\n### ")[0]
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in part.splitlines() if line.startswith("|")]
    return {row[0]: dict(zip(rows[0][1:], row[1:])) for row in rows[2:]}, part


class Compare(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def path(self, build):
        return os.path.join(self.dir.name, build)

    def run_of(self, build, task, rep, harness="codex", wall=10.0, calls=4, passed=True, failures=(), cost=0.02,
               infrastructure=None, dispatch=None, commit=None):
        """Writes a finished run as run.py and report.py leave it, as far as compare.py reads it: into the build's
        directory of the task, or with task None into the build's directory itself, as a dispatch of one task does."""
        results = os.path.join(self.path(build), task) if task else self.path(build)
        run_dir = os.path.join(results, f"{harness}-gpt-{rep}")
        os.makedirs(run_dir)
        with open(os.path.join(results, "source.json"), "w") as f:
            json.dump({"task": task or "calc", "variant": None, "commit": (commit or build[0]) * 40}, f)
        with open(os.path.join(run_dir, "summary.json"), "w") as f:
            json.dump({"harness": harness, "model": "gpt", "effort": {"requested": None}, "rep": rep, "passed": passed,
                       "failures": list(failures), "wall_seconds": wall, "tool_seconds": wall / 2, "calls": calls,
                       "context": {"request_count": calls + 1, "growth_input_tokens": 1000 * calls},
                       "usage": {"cost_usd": cost, "input_tokens": 10000 * calls},
                       "infrastructure": infrastructure}, f)
        with open(os.path.join(run_dir, "run.json"), "w") as f:
            json.dump({"harness": harness, "model": "gpt", "rep": rep, **({"dispatch": dispatch} if dispatch else {})}, f)

    def compare(self, *others, control=None):
        return compare.compare(self.path("main"), [self.path(o) for o in others], self.path(control) if control else None)

    def test_a_run_pairs_with_the_baselines_run_of_its_task_agent_and_repetition(self):
        # Codex's runs of the feature take 2 s more than their partners, each of another length, and one call fewer.
        # Claude's take 5 s less. A mean over runs paired any other way would not be 2 s.
        for task, rep, wall in (("calc", 1, 10.0), ("calc", 2, 20.0), ("form", 1, 40.0), ("form", 2, 50.0)):
            self.run_of("main", task, rep, wall=wall, calls=5)
            self.run_of("feature", task, rep, wall=wall + 2, calls=4)
            self.run_of("main", task, rep, harness="claude", wall=wall)
            self.run_of("feature", task, rep, harness="claude", wall=wall - 5)
        self.run_of("feature", "form", 3, wall=500.0)  # the baseline has no third repetition
        self.run_of("main", "pano", 1, wall=900.0)  # nor the feature this task
        text = self.compare("feature")
        rows, part = table(text, "codex:gpt", "feature")
        self.assertEqual(rows["Wall s"], {"Pairs": "4", "Baseline": "30.0", "feature": "32.0", "Difference": "+2.0",
                                          "95 % interval": "+2.0 to +2.0", "Of baseline": "+6.7 %", "Resolves": "±0.0 %"})
        self.assertEqual((rows["Calls"]["Difference"], rows["Calls"]["Of baseline"]), ("-1.00", "-20.0 %"))
        self.assertEqual((rows["Input k"]["Baseline"], rows["Input k"]["Difference"]), ("50.0", "-10.0"))
        self.assertEqual((rows["Context growth k"]["Baseline"], rows["Context growth k"]["Difference"]), ("5.00", "-1.00"))
        self.assertIn("4 pairs.", part)
        self.assertIn("Without a partner, and left out: 1 runs of the baseline and 1 of feature.", part)
        self.assertEqual(table(text, "claude:gpt", "feature")[0]["Wall s"]["Difference"], "-5.0")

    def test_the_interval_is_over_the_pairs_and_needs_three(self):
        for rep, difference in enumerate((4.0, 5.0, 6.0, 5.0, 4.0, 6.0), 1):
            self.run_of("main", "calc", rep, wall=100.0)
            self.run_of("feature", "calc", rep, wall=100.0 + difference)
            if rep <= 2:
                self.run_of("main", "calc", rep, harness="claude")
                self.run_of("feature", "calc", rep, harness="claude", wall=11.0)
        text = self.compare("feature")
        row = table(text, "codex:gpt", "feature")[0]["Wall s"]
        low, high = (float(end) for end in row["95 % interval"].split(" to "))
        # It lies around the mean, within the pairs' own differences, and half its width is what Resolves says.
        self.assertTrue(4.0 <= low < 5.0 < high <= 6.0, row)
        self.assertAlmostEqual(float(row["Resolves"].strip("±% ")), 100 * (high - low) / 2 / 100.0, delta=0.06)
        self.assertEqual(self.compare("feature"), text)  # the same runs give the same interval
        two = table(text, "claude:gpt", "feature")[0]["Wall s"]
        self.assertEqual((two["Difference"], two["95 % interval"], two["Resolves"]), ("+1.0", "–", "–"))

    def test_passes_lost_and_gained_come_with_what_the_checker_said(self):
        self.run_of("main", "calc", 1)
        self.run_of("feature", "calc", 1, passed=False, failures=["B2 is 12; expected 21", "the sheet is not saved"])
        self.run_of("main", "calc", 2, passed=False, failures=["no totals"])
        self.run_of("feature", "calc", 2)
        self.run_of("main", "calc", 3, passed=False, failures=["no totals"])
        self.run_of("feature", "calc", 3, passed=False, failures=["no totals"])
        part = table(self.compare("feature"), "codex:gpt", "feature")[1]
        self.assertIn("3 pairs. Passed: baseline 1, feature 1 of 3. Passes lost 1, gained 1.", part)
        self.assertIn("- lost calc #1: B2 is 12; expected 21; the sheet is not saved", part)
        self.assertIn("- gained calc #2, where the baseline failed: no totals", part)
        self.assertNotIn("calc #3", part)

    def test_runs_that_the_infrastructure_stopped_stay_out_and_are_counted(self):
        for rep in (1, 2, 3):
            self.run_of("main", "calc", rep)
            self.run_of("feature", "calc", rep, wall=12.0)
        self.run_of("main", "calc", 4)
        self.run_of("feature", "calc", 4, wall=900.0, passed=False, failures=["nothing entered"],
                    infrastructure={"kind": "rate_limit", "detail": "429"})
        text = self.compare("feature")
        self.assertIn("3 runs, and 1 left out that the infrastructure stopped (1 rate_limit): calc/codex-gpt-4", text)
        rows, part = table(text, "codex:gpt", "feature")
        self.assertEqual((rows["Wall s"]["Pairs"], rows["Wall s"]["Difference"]), ("3", "+2.0"))
        self.assertIn("Passes lost 0, gained 0.", part)

    def test_a_measure_that_no_run_has_is_left_empty(self):
        for rep in (1, 2, 3):
            self.run_of("main", "calc", rep, cost=None)
            self.run_of("feature", "calc", rep, cost=None, calls=6)
        rows = table(self.compare("feature"), "codex:gpt", "feature")[0]
        self.assertEqual((rows["Cost $"]["Pairs"], rows["Cost $"]["Difference"]), ("0", "–"))
        self.assertEqual(rows["Calls"]["Difference"], "+2.00")

    def control(self):
        """A baseline, its control, whose runs differ from it by up to 3 s and half a call either way, and a feature
        that takes 10 s more and as many calls as the control."""
        for rep, (wall, calls) in enumerate(((-3.0, 1), (1.0, -1), (2.0, 1), (0.0, 1)), 1):
            self.run_of("main", "calc", rep, wall=50.0, calls=10)
            self.run_of("again", "calc", rep, wall=50.0 + wall, calls=10 + calls, commit="m")
            self.run_of("feature", "calc", rep, wall=60.0, calls=10 + calls, passed=rep != 1, failures=["no totals"])

    def test_the_control_comes_first_and_differences_are_held_against_its_range(self):
        self.control()
        self.run_of("again", "calc", 5, passed=False, commit="m")  # no partner: the control lost no pass
        text = self.compare("feature", control="again")
        self.assertLess(text.index("### Control: again, the noise floor"), text.index("### feature"))
        floor, _ = table(text, "codex:gpt", "Control: again, the noise floor")
        self.assertNotIn("Control's range", floor["Wall s"])
        low, high = (float(end) for end in floor["Wall s"]["95 % interval"].split(" to "))
        reach = max(abs(low), abs(high))
        self.assertTrue(0 < reach <= 3.0, floor)
        rows, part = table(text, "codex:gpt", "feature")
        self.assertEqual(rows["Wall s"]["Control's range"], f"outside ±{reach:.1f}")
        self.assertEqual(rows["Calls"]["Control's range"].split()[0], "inside")
        self.assertIn("Passes lost 1, gained 0: outside what the control shows, which lost 0 and gained 0.", part)
        self.assertNotIn("is not the baseline's build", text)
        self.assertIn("A difference inside that range is not evidence of an effect.", text)

    def test_a_control_of_another_commit_is_named(self):
        self.control()
        self.run_of("again", "calc", 5, commit="x")
        self.assertIn("The control is not the baseline's build: commit xxxxxxxxxx against mmmmmmmmmm.",
                      self.compare("feature", control="again"))

    def test_without_a_control_it_says_that_nothing_shows_the_noise(self):
        self.control()
        text = self.compare("feature")
        self.assertIn("No control was given", text)
        self.assertNotIn("Control's range", text)

    def test_means_by_position_where_the_runs_record_it(self):
        # Each build runs twice first and twice second, and a first run takes 10 s more whatever the build.
        for rep in (1, 2, 3, 4):
            first = "main" if rep % 2 else "feature"
            for build in ("main", "feature"):
                position = 1 if build == first else 2
                self.run_of(build, "calc", rep, wall=30.0 if position == 1 else 20.0,
                            dispatch={"position": position, "builds": 2, "ran_after": position - 1})
        # A run that a resume ran alone, though second in its unit's order, and one of an older dispatch.
        self.run_of("main", "calc", 5, wall=500.0, dispatch={"position": 2, "builds": 2, "ran_after": 0})
        self.run_of("feature", "calc", 5, wall=500.0, dispatch={"slot": 0})
        rows, part = table(self.compare("feature"), "codex:gpt", "By position in the unit")
        self.assertEqual({position: (row["Runs"], row["Passed"], row["Wall s"], row["main"], row["feature"])
                          for position, row in rows.items()},
                         {"1 of 2": ("4", "4", "30.0", "2", "2"), "2 of 2": ("4", "4", "20.0", "2", "2")})
        self.assertIn("Left out: 1 runs that record no position, and 1 that a resume or a retry ran", part)

    def test_runs_without_a_position_are_compared_and_said_to_have_none(self):
        for rep in (1, 2, 3):
            self.run_of("main", "calc", rep, dispatch={"slot": 1, "balance": False})
            self.run_of("feature", "calc", rep, wall=11.0)
        text = self.compare("feature")
        self.assertEqual(table(text, "codex:gpt", "feature")[0]["Wall s"]["Difference"], "+1.0")
        self.assertNotIn("By position", text)
        self.assertIn("The runs record no position in their unit", text)

    def test_the_results_of_one_task_pair_as_those_of_several(self):
        for rep in (1, 2, 3):
            self.run_of("main", None, rep)
            self.run_of("feature", None, rep, wall=13.0, passed=rep != 2, failures=["no totals"])
        rows, part = table(self.compare("feature"), "codex:gpt", "feature")
        self.assertEqual((rows["Wall s"]["Pairs"], rows["Wall s"]["Difference"]), ("3", "+3.0"))
        self.assertIn("- lost calc #2: no totals", part)

    def test_a_directory_without_finished_runs_is_refused(self):
        self.run_of("main", "calc", 1)
        os.makedirs(self.path("empty"))
        for argv in ([self.path("main")], [self.path("main"), self.path("empty")],
                     [self.path("main"), "--control", self.path("missing")]):
            with mock.patch.object(sys, "argv", ["compare.py", *argv]), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit, msg=argv):
                compare.main()


if __name__ == "__main__":
    unittest.main()
