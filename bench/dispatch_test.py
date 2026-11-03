"""Tests dispatch.py's planning and scheduling, and run.py's choice of runs, without machines: python3 dispatch_test.py"""

import argparse
import collections
import contextlib
import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

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

    def tasks(self, *argv):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
        run.add_run_options(parser, tasks=True)
        return dispatch.parse_tasks(parser.parse_args(list(argv)), parser)

    def test_several_tasks_with_their_variants(self):
        self.assertEqual(self.tasks(), [("thunderbird", None)])
        self.assertEqual(self.tasks("--task", "board", "--variant", "pointer"), [("board", "pointer")])
        self.assertEqual(self.tasks("--task", "forgejo", "--task", "board:dnd", "--task", "form", "--task", "trace:rack"),
                         [("forgejo", None), ("board", "dnd"), ("form", "25"), ("trace", "rack")])
        for bad in (["--task", "board:dnd", "--task", "forgejo", "--variant", "dnd"], ["--task", "board:dnd", "--variant", "dnd"],
                    ["--task", "form", "--task", "form:25"], ["--task", "forgejo:x"], ["--task", "board"], ["--task", "nope"]):
            with self.assertRaises(ValueError, msg=bad):
                self.tasks(*bad)

    def test_one_task_writes_into_out_and_several_into_a_directory_each(self):
        self.assertEqual(dispatch.results_out("/r/one", "board", "dnd", 1), "/r/one")
        self.assertEqual(dispatch.results_out("/r/all", "board", "dnd", 2), "/r/all/board-dnd")
        self.assertEqual(dispatch.results_out("/r/all", "forgejo", None, 2), "/r/all/forgejo")
        self.assertTrue(dispatch.results_out(None, "board", "dnd", 1).endswith("-board-dnd"))

    def test_plan_interleaves_tasks_and_agents(self):
        with tempfile.TemporaryDirectory() as root:
            builds = [dispatch.Build(os.path.join(root, f"{task}-{commit}"),
                                     {"path": "/" + commit, "commit": commit * 40, "task": task, "variant": None})
                      for task in ("forgejo", "calc") for commit in "ab"]
            for b in builds:
                os.makedirs(b.out)
            finish(builds[2].out, "codex-gpt-2")  # calc on a, rep 2
            units = dispatch.plan(builds, AGENTS, [1, 2])
            self.assertEqual(
                [(u.jobs[0].build.task, u.jobs[0].name, [j.build.commit[0] for j in u.jobs]) for u in units],
                [("forgejo", "codex-gpt-1", ["a", "b"]), ("forgejo", "claude-opus-1", ["b", "a"]),
                 ("calc", "codex-gpt-1", ["a", "b"]), ("calc", "claude-opus-1", ["b", "a"]),
                 ("forgejo", "codex-gpt-2", ["a", "b"]), ("forgejo", "claude-opus-2", ["b", "a"]),
                 ("calc", "codex-gpt-2", ["b"]), ("calc", "claude-opus-2", ["b", "a"])],
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
        self.assertEqual(dispatch.Host("box,slots=3,cores=2").cores, 2)
        for bad in ("box,slots=0", "box,colour=red", "box,lock", ",slots=1", "box,agents=pi", "box,agents=codex+claude-api:x",
                    "box,cores=0", "box,agents=pi,api-keys", "box,api-keys=yes"):
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

    def test_a_host_with_api_keys_takes_claude_api_but_not_pi(self):
        for spec in ("me@box,api-keys", "me@box,agents=claude-api+codex,api-keys"):
            box = dispatch.Host(spec)
            self.assertTrue(box.takes("claude-api", "sonnet") and box.takes("codex", "gpt"), spec)
            self.assertFalse(box.takes("pi", "tensorx/deepseek/x"), spec)
        self.assertFalse(dispatch.Host("me@box,agents=claude-api+codex,api-keys").takes("claude", "sonnet"))
        with self.assertRaises(ValueError):
            dispatch.Host("me@box,agents=claude-api")

    def test_agents_patterns_and_efforts(self):
        # A pattern without @ takes the agent at any effort; one with @ only at the efforts it matches.
        box = dispatch.Host("me@box,agents=codex:gpt+claude:*@high")
        self.assertEqual([box.takes(*a) for a in (("codex", "gpt", None), ("codex", "gpt", "high"),
                                                  ("claude", "opus", "high"), ("claude", "opus", "low"),
                                                  ("claude", "opus", None))],
                         [True, True, True, False, False])


# The thread_siblings_list of each CPU of an 8-core SMT machine, such as a Ryzen 7 5800X3D: core k is CPUs k and k+8.
EIGHT_CORES = [f"{cpu % 8},{cpu % 8 + 8}\n" for cpu in range(16)]


class Cores(unittest.TestCase):
    def test_slots_get_whole_cores_of_their_own_from_the_last_down(self):
        cores = run.physical_cores(EIGHT_CORES)
        self.assertEqual(len(cores), 8)
        self.assertEqual([run.format_cpus(c) for c in run.slot_cpus(cores, 3, 2)],
                         ["6-7,14-15", "4-5,12-13", "2-3,10-11"])
        self.assertEqual(run.parse_cpus("6-7,14-15"), [6, 7, 14, 15])
        # Without SMT, a core is one CPU.
        self.assertEqual(run.slot_cpus(run.physical_cores([f"{c}\n" for c in range(4)]), 1, 2), [[2, 3]])

    def test_slots_that_would_take_every_core_are_refused(self):
        cores = run.physical_cores(EIGHT_CORES)
        for slots, per_slot in ((4, 2), (2, 4), (1, 0)):
            with self.assertRaises(ValueError, msg=(slots, per_slot)):
                run.slot_cpus(cores, slots, per_slot)
        for bad in ("", "a", "3-1", "1,"):
            with self.assertRaises(ValueError, msg=bad):
                run.parse_cpus(bad)


def hold(path, operation, hold_seconds, done):
    """A process that takes a flock of path, as `flock FILE` does, holds it, and writes the time it let go to done."""
    return subprocess.Popen([sys.executable, "-c", f"""
import fcntl, sys, time
f = open({path!r}, "a")
fcntl.flock(f, {operation})
time.sleep({hold_seconds})
open({done!r}, "w").write(str(time.time()))
"""])


class Lock(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "vm.lock")
        patch = mock.patch.object(run, "LOCK_POLL_SECONDS", 0.05)
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.dir.cleanup)

    def wait_until(self, predicate):
        deadline = time.time() + 10
        while not predicate():
            self.assertLess(time.time(), deadline)
            time.sleep(0.02)

    def test_runs_share_the_lock_and_an_exclusive_holder_keeps_them_out(self):
        with run.vm_lock(self.path), run.vm_lock(self.path):  # two slots at once
            with open(self.path) as f:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        done = os.path.join(self.dir.name, "done")
        vm_test = hold(self.path, "fcntl.LOCK_EX", 0.3, done)
        self.addCleanup(vm_test.wait)
        self.wait_until(lambda: os.path.exists(self.path) and self.locked())
        with run.vm_lock(self.path):
            self.assertTrue(os.path.exists(done))
            took = time.time()
        self.assertGreaterEqual(took, float(open(done).read()))

    def locked(self):
        with open(self.path) as f:
            try:
                fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(f, fcntl.LOCK_UN)
            return False

    def test_a_waiting_exclusive_lock_goes_before_new_runs(self):
        # A slot holds the lock shared; a VM test then waits for it exclusive. Another slot's run, which flock alone
        # would let in beside the first, waits until the VM test has had its turn.
        done = os.path.join(self.dir.name, "done")
        with open(self.path, "a") as slot:
            fcntl.flock(slot, fcntl.LOCK_SH)
            vm_test = hold(self.path, "fcntl.LOCK_EX", 0.2, done)
            self.addCleanup(vm_test.wait)
            self.wait_until(lambda: run.exclusive_waiting(self.path))
            took = []

            def second_slot():
                with run.vm_lock(self.path):
                    took.append(time.time())

            second = threading.Thread(target=second_slot)
            second.start()
            time.sleep(0.3)
            self.assertEqual(took, [], "a run took the lock while a VM test waited for it")
            fcntl.flock(slot, fcntl.LOCK_UN)
        second.join(10)
        self.assertEqual(len(took), 1)
        self.assertGreaterEqual(took[0], float(open(done).read()))
        self.assertFalse(run.exclusive_waiting(self.path))


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

    def schedule(self, hosts, agents, reps, balance=False, limits=(), seconds=None):
        """Runs the agents' repetitions on the hosts; seconds[host] is how long each of its jobs takes. Returns the
        jobs by host and agent, the most jobs of each limit's pattern that ran at once, and the jobs."""
        build = dispatch.Build("/nowhere", {"path": "/a", "commit": "a" * 40})
        units = [dispatch.Unit([dispatch.Job(build, h, m, rep)]) for rep in range(1, reps + 1) for h, m in agents]
        ran, lock = collections.Counter(), threading.Lock()
        running, most = collections.Counter(), collections.Counter()

        def execute(host, slot, job):
            matching = [p for p, n in limits if dispatch.agent_matches(p, job.harness, job.model)]
            with lock:
                ran[str(host), job.harness] += 1
                for p in matching:
                    running[p] += 1
                    most[p] = max(most[p], running[p])
            time.sleep((seconds or {}).get(str(host), 0.01))
            with lock:
                for p in matching:
                    running[p] -= 1
            return "done"

        dispatch.dispatch(hosts, dispatch.Queue(units, hosts, balance, limits), lambda host: True, execute)
        return ran, most, [j for u in units for j in u.jobs]

    def test_balance_gives_each_host_an_equal_share_of_each_agent(self):
        hosts = [dispatch.Host("fast,slots=2"), dispatch.Host("slow")]
        agents = [("codex", "gpt"), ("claude", "opus")]
        seconds = {"fast": 0.01, "slow": 0.1}
        ran, _, _ = self.schedule(hosts, agents, 6, seconds=seconds)
        self.assertGreater(ran["fast", "codex"], ran["slow", "codex"] + 1)  # whichever is free takes the next
        ran, _, jobs = self.schedule(hosts, agents, 5, balance=True, seconds=seconds)
        for harness in ("codex", "claude"):
            self.assertEqual(sorted([ran["fast", harness], ran["slow", harness]]), [2, 3], harness)
        self.assertEqual({j.outcome for j in jobs}, {"done"})

    def test_balance_shares_a_dropped_hosts_part_among_the_others(self):
        build = dispatch.Build("/nowhere", {"path": "/a", "commit": "a" * 40})
        units = [dispatch.Unit([dispatch.Job(build, "codex", "gpt", rep)]) for rep in range(1, 7)]
        hosts = [dispatch.Host("a"), dispatch.Host("b"), dispatch.Host("unready")]
        ran = collections.Counter()
        dispatch.dispatch(hosts, dispatch.Queue(units, hosts, balance=True), lambda host: str(host) != "unready",
                          lambda host, slot, job: ran.update([str(host)]) or "done")
        self.assertEqual(ran, {"a": 3, "b": 3})

    def test_balance_shares_each_tasks_runs_of_an_agent(self):
        # Two tasks of one agent on a fast and a slow host: each host runs half of each task's runs, not all of one.
        builds = [dispatch.Build(f"/{task}", {"path": "/a", "commit": "a" * 40, "task": task}) for task in ("x", "y")]
        units = [dispatch.Unit([dispatch.Job(b, "codex", "gpt", rep)]) for rep in range(1, 5) for b in builds]
        hosts = [dispatch.Host("fast,slots=2"), dispatch.Host("slow")]
        ran, lock = collections.Counter(), threading.Lock()

        def execute(host, slot, job):
            with lock:
                ran[str(host), job.build.task] += 1
            time.sleep(0.01 if str(host) == "fast" else 0.05)
            return "done"

        dispatch.dispatch(hosts, dispatch.Queue(units, hosts, balance=True), lambda host: True, execute)
        self.assertEqual(ran, {("fast", "x"): 2, ("fast", "y"): 2, ("slow", "x"): 2, ("slow", "y"): 2})

    def test_max_concurrent_limits_an_agents_runs_over_all_hosts(self):
        hosts = [dispatch.Host("a,slots=2"), dispatch.Host("b,slots=2")]
        ran, most, jobs = self.schedule(hosts, [("codex", "gpt"), ("claude", "opus")], 4,
                                        limits=[("codex", 1)], seconds={"a": 0.05, "b": 0.05})
        self.assertEqual(most["codex"], 1)
        self.assertEqual(sum(n for (host, harness), n in ran.items()), 8)
        # The codex runs that free slots came for while another ran waited, and say so.
        self.assertTrue(any(j.waited > 0 for j in jobs if j.harness == "codex"))
        self.assertTrue(all(j.waited == 0 for j in jobs if j.harness == "claude"))

    def test_max_concurrent_patterns(self):
        agents = [("codex", "gpt", None), ("claude-api", "sonnet", None)]
        self.assertEqual(dispatch.parse_limits(["codex=4,claude-api=3", "codex:gpt=2"], agents),
                         [("codex", 4), ("claude-api", 3), ("codex:gpt", 2)])
        for bad in ("codex", "codex=0", "=2", "codex=x", "pi=2", "claude=1"):
            with self.assertRaises(ValueError, msg=bad):
                dispatch.parse_limits([bad], agents)

    def test_a_host_that_cannot_be_prepared_takes_no_jobs(self):
        ran, outcomes, _ = self.dispatch(
            [dispatch.Host("unready"), dispatch.Host("good")], {"good": "done"}, ready=lambda host: str(host) == "good")
        self.assertEqual(sorted(ran), [("good", 1), ("good", 2), ("good", 3)])


KEY = "sk-ant-api03-dispatch-test-key"
HEADERS = "anthropic-workspace-id: wrkspc_1\nx-other: 2"

# Stand-ins for ssh, rsync and git, which log their arguments, environment and stdin to $FAKE_LOG and run the remote
# command here; and for the remote run.py, which writes a run directory with what it saw of the key.
FAKE_SSH = """
import json, os, subprocess, sys
args, data = sys.argv[1:], sys.stdin.buffer.read()
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"tool": "ssh", "argv": args, "env": dict(os.environ), "stdin": data.decode()}) + "\\n")
if "-O" in args:
    sys.exit(0)
i = 0
while args[i].startswith("-"):
    i += 2
sys.exit(subprocess.run(["sh", "-c", " ".join(args[i + 1:])], input=data).returncode)
"""
FAKE_RSYNC = """
import json, os, shutil, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"tool": "rsync", "argv": sys.argv[1:], "env": dict(os.environ)}) + "\\n")
shutil.copytree(sys.argv[-2].split(":", 1)[1], sys.argv[-1], dirs_exist_ok=True)
"""
FAKE_GIT = """
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps({"tool": "git", "argv": sys.argv[1:], "env": dict(os.environ)}) + "\\n")
"""
FAKE_RUN = """
import glob, hashlib, json, os, sys
sys.path.insert(0, os.environ["FAKE_BENCH"])
import run
args = sys.argv[1:]
harness, model, effort = run.parse_agent(args[args.index("--agent") + 1])
d = os.path.join(args[args.index("--out") + 1], run.run_name(harness, model, int(args[args.index("--rep") + 1]), effort))
os.makedirs(d)
key = os.environ.get(run.API_KEY_ENV, "")
in_ps = []
for path in glob.glob("/proc/[0-9]*/cmdline"):
    try:
        if key and key.encode() in open(path, "rb").read():
            in_ps.append(path)
    except OSError:
        pass
seen = {"key": hashlib.sha256(key.encode()).hexdigest(),
        "headers": hashlib.sha256(os.environ.get(run.API_HEADERS_ENV, "").encode()).hexdigest(), "in_ps": in_ps,
        "backend": args[args.index("--backend") + 1], "prompt": args[args.index("--prompt") + 1],
        "browser": args[args.index("--browser") + 1]}
json.dump(seen, open(os.path.join(d, "seen.json"), "w"))
open(os.path.join(d, "agent.jsonl"), "w").write(key if os.environ.get("FAKE_LEAK") else "{}")
if not os.environ.get("FAKE_LEAK"):
    json.dump({"passed": True, "wall_seconds": 1, "calls": 1, "tool_seconds": 1}, open(os.path.join(d, "summary.json"), "w"))
print("[fake] done", flush=True)
"""


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class ApiKeys(unittest.TestCase):
    """claude-api's key on a host with api-keys: through a run's stdin into its environment, and nowhere else."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = self.dir.name
        self.bin, self.base, self.out, self.log = (os.path.join(root, n) for n in ("bin", "base", "out", "fake.log"))
        os.makedirs(self.bin)
        for name, code in (("ssh", FAKE_SSH), ("rsync", FAKE_RSYNC), ("git", FAKE_GIT)):
            with open(os.path.join(self.bin, name), "w") as f:
                f.write(f"#!{sys.executable}\n{code}")
            os.chmod(os.path.join(self.bin, name), 0o755)
        self.harness = {"path": root, "commit": "h" * 40}
        fake_run = os.path.join(self.base, "trees", "h" * 40, "bench", "run.py")
        os.makedirs(os.path.dirname(fake_run))
        with open(fake_run, "w") as f:
            f.write(FAKE_RUN)
        # The dispatcher's own environment holds the key, as the user's may: ssh and the others must not get it.
        env = mock.patch.dict(os.environ, {"PATH": self.bin + os.pathsep + os.environ["PATH"], "FAKE_LOG": self.log,
                                           "FAKE_BENCH": os.path.dirname(os.path.abspath(__file__)),
                                           run.API_KEY_ENV: KEY, "ANTHROPIC_API_KEY": KEY})
        env.start()
        self.addCleanup(env.stop)
        poll = mock.patch.object(dispatch, "POLL_SECONDS", 0.05)
        poll.start()
        self.addCleanup(poll.stop)

    def execute(self, spec, harness, backend="hyprmasq", prompt="task", browser="firefox"):
        """Runs one job of the harness on the host; returns its outcome, the dispatcher's output and the fakes' log."""
        host = dispatch.Host(spec)
        host.base = self.base
        build = dispatch.Build(self.out, {"path": self.dir.name, "commit": "b" * 40, "task": "forgejo", "variant": None})
        args = argparse.Namespace(display="1920x1080@1", timeout=60, max_calls=0, no_debrief=True, backend=backend, prompt=prompt,
                                  browser=browser)
        keys = {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_CUSTOM_HEADERS": HEADERS}
        remote = dispatch.Remote(self.harness, [build], [(harness, "m", None)], args, self.dir.name, dispatch.Queue([]), keys)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            outcome = remote.execute(host, 0, dispatch.Job(build, harness, "m", 1))
            remote.git("version")
        with open(self.log) as f:
            calls = [json.loads(line) for line in f]
        return outcome, printed.getvalue(), calls

    def assertNowhereBut(self, calls, printed):
        """The key and the headers are in no argument or environment of ssh, rsync and git, in nothing the dispatcher
        printed, and in no file that it or the run left, here or on the host."""
        for call in calls:
            for secret in (KEY, HEADERS):
                self.assertFalse([a for a in call["argv"] if secret in a], call["tool"])
                self.assertFalse([k for k, v in call["env"].items() if secret in v], call["tool"])
            self.assertFalse([k for k in call["env"] if k.startswith(("ANTHROPIC_", "HYPRMASQ_BENCH_ANTHROPIC_"))])
        self.assertEqual({c["tool"] for c in calls}, {"ssh", "rsync", "git"})
        self.assertNotIn(KEY, printed)
        self.assertFalse(run.files_holding(self.out, KEY))
        self.assertFalse(run.files_holding(self.base, KEY))

    def test_the_key_reaches_the_run_through_stdin_alone(self):
        outcome, printed, calls = self.execute("me@box,api-keys", "claude-api")
        self.assertEqual(outcome, "done")
        # One ssh call, the run's start, gets the key and headers on its stdin; the rest get nothing.
        given = [c for c in calls if c["tool"] == "ssh" and c["stdin"]]
        self.assertEqual([c["stdin"] for c in given], [f"{KEY}\0{HEADERS}\0", KEY + "\n"])
        self.assertTrue(any("setsid" in a for a in given[0]["argv"]))
        self.assertTrue(any("grep" in a for a in given[1]["argv"]))
        with open(os.path.join(self.out, "claude-api-m-1", "seen.json")) as f:
            seen = json.load(f)
        self.assertEqual(seen, {"key": sha(KEY), "headers": sha(HEADERS), "in_ps": [], "backend": "hyprmasq",
                                "prompt": "task", "browser": "firefox"})
        self.assertNowhereBut(calls, printed)

    def test_files_that_hold_the_key_stay_on_the_host_and_are_removed(self):
        with mock.patch.dict(os.environ, {"FAKE_LEAK": "1"}):
            outcome, printed, calls = self.execute("me@box,api-keys", "claude-api")
        self.assertEqual(outcome, "failed")
        self.assertIn("the API key was in out/claude-api-m-1/agent.jsonl", printed)
        self.assertEqual(sorted(os.listdir(os.path.join(self.out, "claude-api-m-1"))), ["seen.json"])
        self.assertNowhereBut(calls, printed)

    def test_the_run_gets_the_dispatchs_backend_prompt_and_browser(self):
        outcome, printed, calls = self.execute("me@box", "codex", backend="cua", prompt="comparison",
                                               browser="chromium")
        self.assertEqual(outcome, "done")
        with open(os.path.join(self.out, "codex-m-1", "seen.json")) as f:
            self.assertEqual({k: v for k, v in json.load(f).items() if k in ("backend", "prompt", "browser")},
                             {"backend": "cua", "prompt": "comparison", "browser": "chromium"})

    def test_other_runs_get_no_key(self):
        outcome, printed, calls = self.execute("me@box,api-keys", "codex")
        self.assertEqual(outcome, "done")
        self.assertFalse([c for c in calls if c.get("stdin")])
        with open(os.path.join(self.out, "codex-m-1", "seen.json")) as f:
            self.assertEqual(json.load(f)["key"], sha(""))
        self.assertNowhereBut(calls, printed)


if __name__ == "__main__":
    unittest.main()
