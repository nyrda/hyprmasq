#!/usr/bin/env python3
"""The tests of series.py's plan and of what it lets run again, with no machine: python3 series_test.py"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import series


def rules():
    sets = {"apps": {"tasks": ["x", "z"]}, "web": {"browser": "chromium", "tasks": ["y:v"]}}
    return {
        "prompt": "comparison", "desktop": "omarchy", "display": "1920x1080@1", "agent_timeout_seconds": 1800,
        "agents": ["codex:gpt@medium", "claude:son@medium"], "sources": {"s": {"path": "/s", "commit": "abc"}},
        "machines": {"a": {"host": "a,slots=2,bin=.b", "workers": 2, "max_concurrent": {"codex": 1}, "time_factor": 1.0},
                     "b": {"host": "b,slots=1", "workers": 1, "time_factor": 1.0}},
        "series": {"main": {"tools": ["one", "two"], "machine": "a", "reps": 2, "results": "test-main"},
                   "cross": {"tools": ["one", "two"], "machine": "b", "reps": 1, "results": "test-cross"}},
        "tools": {"one": {"backend": "cua", "source": "s", "sets": sets}, "two": {"backend": "hyprmasq", "source": "s", "sets": sets},
                  "three": {"backend": "hypruse", "source": "s", "sets": sets}},
    }


class Plan(unittest.TestCase):
    def test_a_series_runs_on_its_machine_and_into_its_own_directory(self):
        r = rules()
        main = series.dispatch_command(r, r["series"]["main"], "one", "apps", r["tools"]["one"]["sets"]["apps"], [1])
        cross = series.dispatch_command(r, r["series"]["cross"], "one", "apps", r["tools"]["one"]["sets"]["apps"], [1])
        self.assertTrue(main[main.index("--host") + 1].startswith("a,"))
        self.assertTrue(cross[cross.index("--host") + 1].startswith("b,"))
        self.assertNotEqual(main[main.index("--out") + 1], cross[cross.index("--out") + 1])
        self.assertEqual([main[i + 1] for i, a in enumerate(main) if a == "--task"], ["x", "z"])

    def test_a_set_of_one_task_still_writes_into_the_tasks_directory(self):
        # dispatch.py writes a single --task into --out itself, where a set's other tasks would lie under it.
        r = rules()
        command = series.dispatch_command(r, r["series"]["main"], "one", "web", r["tools"]["one"]["sets"]["web"], [1])
        self.assertEqual(command[command.index("--out") + 1], os.path.join(series.out_of(r["series"]["main"], "one", "web"), "y-v"))

    def test_the_order_of_the_tools_rotates_from_round_to_round(self):
        r = rules()
        firsts = [series.round_order(r, r["series"]["main"], ["one", "two"], n)[0][0] for n in (1, 2, 3)]
        self.assertEqual(firsts, ["one", "two", "one"])

    def test_a_slice_runs_its_own_sets_with_its_browser_options(self):
        r = rules()
        r["series"]["attach"] = {"tools": ["one"], "machine": "a", "reps": 1, "results": "test-attach",
                                 "chromium_options": "--remote-debugging-port=0",
                                 "sets": {"one": {"chromium": {"browser": "chromium", "tasks": ["y:v", "w"]}}}}
        s = r["series"]["attach"]
        (tool, name, spec), = series.round_order(r, s, ["one"], 1)
        command = series.dispatch_command(r, s, tool, name, spec, [1])
        self.assertIn("--chromium-options=--remote-debugging-port=0", command)
        self.assertEqual([command[i + 1] for i, a in enumerate(command) if a == "--task"], ["y:v", "w"])
        self.assertEqual(sum(1 for _ in series.planned_runs(r, s, ["one"], [1])), 2 * 2)
        main = series.dispatch_command(r, r["series"]["main"], "one", "web", r["tools"]["one"]["sets"]["web"], [1])
        self.assertFalse([a for a in main if a.startswith("--chromium-options")])

    def test_a_tool_on_a_machine_of_its_own_dispatches_there_and_is_estimated_there(self):
        r = rules()
        r["estimate"] = {"overhead_seconds": 0, "agent_seconds": {t: {"x": 3600, "z": 3600, "y:v": 3600} for t in ("one", "two")}}
        s = dict(r["series"]["main"], tool_machines={"two": "b"})
        one = series.dispatch_command(r, s, "one", "apps", r["tools"]["one"]["sets"]["apps"], [1])
        two = series.dispatch_command(r, s, "two", "apps", r["tools"]["two"]["sets"]["apps"], [1])
        self.assertTrue(one[one.index("--host") + 1].startswith("a,"))
        self.assertTrue(two[two.index("--host") + 1].startswith("b,"))
        self.assertEqual({e[1]: e[6] for e in series.planned_runs(r, s, ["one", "two"], [1])}, {"one": "a", "two": "b"})
        with contextlib.redirect_stdout(io.StringIO()):
            found = series.estimate(r, s, ["one", "two"])
        # 2 rounds of 3 tasks and 2 agents, an hour each: on a's 2 workers 6 h, on b's 1 worker 12 h.
        self.assertEqual(found, {"a": (12, 6.0), "b": (12, 12.0)})

    def test_a_series_that_continues_another_runs_its_own_rounds_as_its_repetitions(self):
        r = rules()
        s = dict(r["series"]["main"], rounds=[3, 4])
        found = {(e[0], os.path.basename(e[5])) for e in series.planned_runs(r, s, ["one"], series.rounds_of(s, None))}
        self.assertEqual({rnd for rnd, _ in found}, {3, 4})
        self.assertTrue(all(name.endswith(f"-{rnd}") for rnd, name in found))

    def test_the_local_file_gives_hosts_and_results_and_a_missing_host_names_its_key(self):
        r = rules()
        r["machines"]["a"] = {"options": "slots=2,bin=.b", "workers": 2, "time_factor": 1.0}
        r["machines"]["b"] = {"target": "local", "options": "slots=1", "workers": 1, "time_factor": 1.0}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "machines.local.json")
            with open(path, "w") as f:
                json.dump({"hosts": {"a": "me@a"}, "results": {"test-main": "test-main-here"}}, f)
            series.with_local(r, path)
        command = series.dispatch_command(r, r["series"]["main"], "one", "apps", r["tools"]["one"]["sets"]["apps"], [1])
        self.assertEqual(command[command.index("--host") + 1], "me@a,slots=2,bin=.b")
        self.assertEqual(series.machine_host(r, "b"), "local,slots=1")
        self.assertIn("test-main-here", command[command.index("--out") + 1])
        r = series.with_local(dict(rules(), machines={"a": {"options": "slots=2"}}), os.path.join(os.sep, "none.json"))
        with self.assertRaisesRegex(SystemExit, "machine a has no host"):
            series.machine_host(r, "a")

    def test_the_series_hold_the_runs_their_repetitions_say(self):
        r = rules()
        for name, count in (("main", 2 * 2 * 2 * 3), ("cross", 2 * 2 * 1 * 3)):
            s = r["series"][name]
            self.assertEqual(sum(1 for _ in series.planned_runs(r, s, s["tools"], series.rounds_of(s, None))), count)


class Reruns(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patcher = mock.patch.object(series, "BENCH", self.dir.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rules = rules()
        self.series = self.rules["series"]["main"]

    def run_dir(self, summary):
        entry = next(series.planned_runs(self.rules, self.series, ["one"], [1]))
        os.makedirs(entry[5])
        if summary is not None:
            with open(os.path.join(entry[5], "summary.json"), "w") as f:
                json.dump(summary, f)
        return entry

    def test_a_run_the_agent_failed_stands(self):
        entry = self.run_dir({"passed": False, "infrastructure": None})
        self.assertIsNone(series.infrastructure(entry[5]))

    def test_a_limit_and_a_missing_summary_are_excluded_with_why_and_moved_aside(self):
        entry = self.run_dir({"passed": False, "infrastructure": {"kind": "rate_limit", "detail": "seven_day"}})
        why = series.infrastructure(entry[5])
        self.assertEqual(why["kind"], "rate_limit")
        series.exclude(self.series, entry, why)
        self.assertFalse(os.path.exists(entry[5]))
        with open(series.log_path(self.series)) as f:
            logged = json.loads(f.readline())
        self.assertEqual((logged["kind"], logged["machine"], logged["round"]), ("rate_limit", "a", 1))
        moved = os.path.join(self.dir.name, "results", "test-main", "excluded", "round-1", "one", "apps", "x",
                             os.path.basename(entry[5]) + ".0")
        self.assertTrue(os.path.exists(os.path.join(moved, "summary.json")))

    def test_a_limit_never_ends_a_round_however_often_it_stops_runs(self):
        passes = []

        def round_with_limits(rules, series_, tools, round_number, log):
            # Each pass leaves every run done, but for one that a limit stopped, until MAX_PASSES + 2 passes are over.
            passes.append(round_number)
            limited = len(passes) <= series.MAX_PASSES + 2
            for i, entry in enumerate(series.planned_runs(rules, series_, tools, [round_number])):
                if os.path.exists(os.path.join(entry[5], "summary.json")):
                    continue
                os.makedirs(entry[5], exist_ok=True)
                why = {"kind": "rate_limit", "detail": "five_hour", "resets_at": 0} if limited and i == 0 else None
                with open(os.path.join(entry[5], "summary.json"), "w") as f:
                    json.dump({"passed": True, "infrastructure": why}, f)

        with mock.patch.object(series, "check_sources", return_value=[]), \
                mock.patch.object(series, "check_versions", return_value=[]), \
                mock.patch.object(series, "run_round", round_with_limits), \
                mock.patch.object(series.time, "sleep") as slept, contextlib.redirect_stdout(io.StringIO()):
            series.main_run(self.rules, self.series, ["one"], [1])
        self.assertEqual(len(passes), series.MAX_PASSES + 3)
        self.assertEqual(slept.call_count, series.MAX_PASSES + 2)
        with open(series.log_path(self.series)) as f:
            self.assertEqual(sum(1 for _ in f), series.MAX_PASSES + 2)

    def test_a_run_that_shared_its_host_runs_again_whatever_its_tool(self):
        for load, kind in (({"max_other_qemu": 1, "guard": False}, "other_load"), ({"max_other_qemu": 0, "guard": True}, "other_load"),
                           ({"max_other_qemu": 0, "guard": False}, None)):
            entry = self.run_dir({"passed": True, "infrastructure": None})
            with open(os.path.join(entry[5], "run.json"), "w") as f:
                json.dump({"host_load": load}, f)
            self.assertEqual((series.infrastructure(entry[5]) or {}).get("kind"), kind)
            shutil.rmtree(entry[5])

    def test_a_paced_tool_starts_a_round_only_after_its_leader_did(self):
        s = dict(self.series, pace={"two": "one"})
        waited = []

        def leader_starts(seconds):
            waited.append(seconds)
            open(series.started(s, 1, "one"), "w").close()

        os.makedirs(os.path.dirname(series.started(s, 1, "one")), exist_ok=True)
        with mock.patch.object(series.time, "sleep", leader_starts), contextlib.redirect_stdout(io.StringIO()):
            series.pace(s, ["two"], 1)
        self.assertEqual(waited, [series.PACE_POLL_SECONDS])
        self.assertTrue(os.path.exists(series.started(s, 1, "two")))
        # The leader itself, and a process that runs both, never wait.
        with mock.patch.object(series.time, "sleep", side_effect=AssertionError("waited")):
            series.pace(s, ["one", "two"], 2)

    def test_every_round_checks_the_harness_versions_before_it_runs(self):
        checked = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(series, "BENCH", tmp), \
                mock.patch.object(series, "check_sources", lambda rules: []), \
                mock.patch.object(series, "check_versions", lambda rules, machine: checked.append(machine) or []), \
                mock.patch.object(series, "run_gate", lambda *a: None), mock.patch.object(series, "pace", lambda *a: None), \
                mock.patch.object(series, "run_round", lambda *a: None), mock.patch.object(series, "planned_runs", lambda *a: []), \
                contextlib.redirect_stdout(io.StringIO()):
            r = rules()
            series.main_run(r, r["series"]["main"], ["one", "two"], [1, 2])
        # Once before the series, and once before each round.
        self.assertEqual(checked, ["a", "a", "a"])

    def test_the_gate_allows_the_workers_only_within_the_slowdown(self):
        counts = [{"vms": 1, "all": {"median": 0.80}}, {"vms": 3, "all": {"median": 0.83}}]
        self.assertTrue(series.gate_passes(counts, 3, 0.05))
        self.assertFalse(series.gate_passes(counts, 3, 0.03))

    def test_a_calibration_beside_other_vms_does_not_count(self):
        counts = [{"vms": 1, "all": {"median": 1.0}, "other_vms": []}, {"vms": 3, "all": {"median": 0.9}, "other_vms": [4242]}]
        self.assertEqual(series.gate_overlaps(counts), [4242])
        self.assertEqual(series.gate_overlaps([{"vms": 1, "all": {"median": 1.0}}]), [])

    def test_a_run_that_left_no_summary_runs_again(self):
        entry = self.run_dir(None)
        self.assertEqual(series.infrastructure(entry[5])["kind"], "no_summary")


if __name__ == "__main__":
    unittest.main()
