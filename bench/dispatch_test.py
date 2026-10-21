"""Tests dispatch.py's planning and scheduling, and run.py's choice of runs, without machines: python3 dispatch_test.py"""

import argparse
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dispatch  # noqa: E402
import run  # noqa: E402

AGENTS = [("codex", "gpt", None), ("claude", "opus", None)]


def finish(out, name, agent_started=True):
    os.makedirs(os.path.join(out, name), exist_ok=True)
    if agent_started:
        open(os.path.join(out, name, "agent.jsonl"), "w").close()
    open(os.path.join(out, name, "summary.json"), "w").close()


class Runs(unittest.TestCase):
    def options(self, *argv):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
        run.add_run_options(parser)
        return run.check_run_options(parser.parse_args(["--task", "forgejo", *argv]), parser)

    def test_rep_selects_repetitions(self):
        self.assertEqual(self.options("--rep", "3", "--rep", "1", "--rep", "3")[1], [1, 3])
        self.assertEqual(self.options("--reps", "2")[1], [1, 2])
        self.assertEqual(self.options("--agent", "codex:gpt", "--agent", "codex:gpt")[0], [("codex", "gpt", None)])
        with self.assertRaises(ValueError):
            self.options("--rep", "0")

    def test_a_task_without_variant_takes_its_default(self):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
        run.add_run_options(parser)
        args = parser.parse_args(["--task", "form"])
        run.check_run_options(args, parser)
        self.assertEqual(args.variant, "25")
        args = parser.parse_args(["--task", "form", "--variant", "10"])
        run.check_run_options(args, parser)
        self.assertEqual(args.variant, "10")
        with self.assertRaises(ValueError):
            run.check_run_options(parser.parse_args(["--task", "form", "--variant", "41"]), parser)
        with self.assertRaises(ValueError):
            run.check_run_options(parser.parse_args(["--task", "board"]), parser)

    def test_plan_skips_finished_runs_and_alternates_builds(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            finish(a, "codex-gpt-1")
            builds = [dispatch.Build(a, {"path": "/a", "commit": "a" * 40}),
                      dispatch.Build(b, {"path": "/b", "commit": "b" * 40})]
            units = dispatch.plan(builds, AGENTS, [1, 2])
            self.assertEqual(
                [[(j.build.out, j.name) for j in u.jobs] for u in units],
                [[(b, "codex-gpt-1")],
                 [(b, "claude-opus-1"), (a, "claude-opus-1")],
                 [(a, "codex-gpt-2"), (b, "codex-gpt-2")],
                 [(b, "claude-opus-2"), (a, "claude-opus-2")]],
            )

    def test_a_finished_run_counts_only_for_its_own_effort(self):
        with tempfile.TemporaryDirectory() as a:
            finish(a, "codex-gpt-1")
            build = dispatch.Build(a, {"path": "/a", "commit": "a" * 40})
            units = dispatch.plan([build], [("codex", "gpt", None), ("codex", "gpt", "high")], [1])
            self.assertEqual([(j.name, j.effort) for u in units for j in u.jobs], [("codex-gpt-high-1", "high")])


class Outcome(unittest.TestCase):
    def test_outcome_of_a_run_directory(self):
        with tempfile.TemporaryDirectory() as out:
            finish(out, "done")
            os.makedirs(os.path.join(out, "failed"))
            open(os.path.join(out, "failed", "agent.jsonl"), "w").close()
            os.makedirs(os.path.join(out, "vm-not-ready"))
            open(os.path.join(out, "vm-not-ready", "driver.log"), "w").close()
            self.assertEqual(dispatch.outcome_of(os.path.join(out, "done")), "done")
            self.assertEqual(dispatch.outcome_of(os.path.join(out, "failed")), "failed")
            self.assertEqual(dispatch.outcome_of(os.path.join(out, "vm-not-ready")), "not started")
            self.assertEqual(dispatch.outcome_of(os.path.join(out, "never-copied")), "not started")


class Hosts(unittest.TestCase):
    def test_host_spec(self):
        h = dispatch.Host("me@box,slots=2,lock=/tmp/a,local-lock=/tmp/b,cid=5000")
        self.assertEqual((h.target, h.slots, h.lock, h.local_lock, h.cid, h.local), ("me@box", 2, "/tmp/a", "/tmp/b", 5000, False))
        self.assertTrue(dispatch.Host("local").local)
        for bad in ("box,slots=0", "box,colour=red", "box,lock", ",slots=1", "box,agents=pi", "box,agents=codex+claude-api:x"):
            with self.assertRaises(ValueError, msg=bad):
                dispatch.Host(bad)

    def test_agents_a_host_takes(self):
        local, box, codex_box = dispatch.Host("local"), dispatch.Host("me@box"), dispatch.Host("me@nas,agents=codex+claude:*opus*")
        self.assertTrue(local.takes("pi", "tensorx/deepseek/x") and local.takes("claude-api", "haiku"))
        # The harnesses that use this machine's API keys run only here, also on a host that takes every agent.
        self.assertFalse(box.takes("pi", "tensorx/deepseek/x") or box.takes("claude-api", "haiku"))
        self.assertTrue(box.takes("claude", "sonnet"))
        self.assertEqual([codex_box.takes(*a) for a in (("codex", "gpt"), ("claude", "claude-opus-5-5"), ("claude", "sonnet"))],
                         [True, True, False])

    def test_agents_patterns_and_efforts(self):
        # A pattern without @ takes the agent at any effort; one with @ only at the efforts it matches.
        box = dispatch.Host("me@box,agents=codex:gpt+claude:*@high")
        self.assertEqual([box.takes(*a) for a in (("codex", "gpt", None), ("codex", "gpt", "high"),
                                                  ("claude", "opus", "high"), ("claude", "opus", "low"),
                                                  ("claude", "opus", None))],
                         [True, True, True, False, False])


class Scheduling(unittest.TestCase):
    def dispatch(self, hosts, outcomes, reps=3, ready=lambda host: True):
        """Runs codex-gpt's repetitions on the hosts; outcomes[host] is what each of its jobs ends with."""
        build = dispatch.Build("/nowhere", {"path": "/a", "commit": "a" * 40})
        units = [dispatch.Unit([dispatch.Job(build, "codex", "gpt", rep)]) for rep in range(1, reps + 1)]
        ran, lock = [], threading.Lock()

        def execute(host, slot, job):
            with lock:
                ran.append((str(host), job.rep))
            if isinstance(outcomes[str(host)], Exception):
                raise outcomes[str(host)]
            return outcomes[str(host)]

        queue = dispatch.Queue(units)
        dispatch.dispatch(hosts, queue, ready, execute)
        return ran, {u.jobs[0].rep: u.jobs[0].outcome for u in units}, queue.units

    def test_a_job_that_did_not_start_goes_once_to_another_host(self):
        broken, good = dispatch.Host("broken"), dispatch.Host("good")
        ran, outcomes, left = self.dispatch([broken, good], {"broken": "not started", "good": "done"})
        self.assertEqual(outcomes, {1: "done", 2: "done", 3: "done"})
        self.assertLessEqual(sum(1 for host, _ in ran if host == "broken"), 2)
        self.assertEqual(sorted(rep for host, rep in ran if host == "good"), [1, 2, 3])
        self.assertEqual(list(left), [])

    def test_a_host_where_two_jobs_in_a_row_did_not_start_takes_no_more(self):
        broken = dispatch.Host("broken")
        ran, outcomes, left = self.dispatch([broken], {"broken": "not started"})
        self.assertEqual(ran, [("broken", 1), ("broken", 2)])
        self.assertTrue(broken.dropped)
        self.assertEqual(outcomes, {1: "not started", 2: "not started", 3: "not run"})

    def test_no_job_runs_a_third_time_or_twice_on_one_host(self):
        a, b = dispatch.Host("a"), dispatch.Host("b")
        ran, outcomes, left = self.dispatch([a, b], {"a": "not started", "b": "not started"}, reps=1)
        self.assertEqual(sorted(ran), [("a", 1), ("b", 1)])
        self.assertEqual(outcomes, {1: "not started"})

    def test_a_run_that_failed_after_its_agent_started_is_not_retried(self):
        ran, outcomes, _ = self.dispatch([dispatch.Host("a"), dispatch.Host("b")], {"a": "failed", "b": "failed"})
        self.assertEqual(sorted(rep for _, rep in ran), [1, 2, 3])
        self.assertEqual(set(outcomes.values()), {"failed"})

    def test_an_error_in_a_job_fails_it_and_the_others_go_on(self):
        ran, outcomes, left = self.dispatch([dispatch.Host("a"), dispatch.Host("b")], {"a": OSError("disk"), "b": "done"})
        self.assertEqual(sorted(rep for _, rep in ran), [1, 2, 3])
        self.assertEqual({rep: o for rep, o in outcomes.items() if o != "done"},
                         {rep: "failed" for host, rep in ran if host == "a"})
        self.assertEqual(list(left), [])

    def test_local_only_agents_wait_for_the_local_host(self):
        build = dispatch.Build("/nowhere", {"path": "/a", "commit": "a" * 40})
        units = [dispatch.Unit([dispatch.Job(build, harness, "m", 1)]) for harness in ("pi", "codex", "claude-api", "codex")]
        ran, lock = [], threading.Lock()

        def execute(host, slot, job):
            with lock:
                ran.append((str(host), job.harness))
            return "done"

        dispatch.dispatch([dispatch.Host("me@box,slots=2"), dispatch.Host("local")], dispatch.Queue(units),
                          lambda host: True, execute)
        self.assertEqual(sorted(host for host, harness in ran if harness in ("pi", "claude-api")), ["local", "local"])
        self.assertEqual(len(ran), 4)

    def test_a_host_that_cannot_be_prepared_takes_no_jobs(self):
        ran, outcomes, _ = self.dispatch(
            [dispatch.Host("unready"), dispatch.Host("good")], {"good": "done"}, ready=lambda host: str(host) == "good")
        self.assertEqual(sorted(ran), [("good", 1), ("good", 2), ("good", 3)])


if __name__ == "__main__":
    unittest.main()
