#!/usr/bin/env python3
"""The detached CLI must stop when a run times out or its caller is interrupted, a Claude app session ends with
the reply that ends its turn and fails on a tool other than computer use, and Finder's .DS_Store files do not fail
the file manager's grade, and mac_privacy.py lists what of the Mac a dataset holds: python3 run_mac_test.py"""

import json
import os
import signal
import select
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

import files_task
import mac_privacy
import run_mac
from run_mac import claude_finished, foreign_tools, other_apps, service_tier, task_times, wait_agent


class AgentCleanupTest(unittest.TestCase):
    def stubborn_agent(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             "print('ready',flush=True); time.sleep(60)"],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(proc.stdout.close)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        return proc

    def test_timeout_kills_agent_that_ignores_term(self):
        proc = self.stubborn_agent()
        wait_agent(proc, 0.01)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_interrupt_cleans_up_and_propagates(self):
        proc = self.stubborn_agent()
        original_wait = proc.wait
        first = True

        def interrupt_once(*args, **kwargs):
            nonlocal first
            if first:
                first = False
                raise KeyboardInterrupt
            return original_wait(*args, **kwargs)

        with patch.object(proc, "wait", side_effect=interrupt_once):
            with self.assertRaises(KeyboardInterrupt):
                wait_agent(proc, 60)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_success_reaped(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
        wait_agent(proc, 10)
        self.assertEqual(proc.returncode, 0)

    def test_descendant_stopped_after_leader_exits(self):
        child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
        parent = "import subprocess,sys; subprocess.Popen([sys.executable,'-c',sys.argv[1]])"
        proc = subprocess.Popen([sys.executable, "-c", parent, child], stdout=subprocess.PIPE,
                                text=True, start_new_session=True)
        self.addCleanup(proc.stdout.close)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        proc.wait(timeout=10)
        wait_agent(proc, 10)
        self.assertTrue(select.select([proc.stdout], [], [], 5)[0], "descendant kept its output pipe open")
        self.assertEqual(proc.stdout.read(), "")


class RolloutTimingTest(unittest.TestCase):
    def test_metadata_only_rollout_has_no_task_bounds(self):
        entries = [{"type": "session_meta", "timestamp": "2026-10-25T20:00:00Z", "payload": {}}]
        self.assertEqual(task_times(entries, False), (None, None))


def assistant(stop_reason, *blocks):
    return {"type": "assistant", "message": {"stop_reason": stop_reason, "content": list(blocks)}}


class ClaudeSessionTest(unittest.TestCase):
    def transcript(self, entries):
        f = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
        self.addCleanup(os.unlink, f.name)
        f.write("\n".join(map(json.dumps, entries)) + "\n")
        f.close()
        return f.name

    def test_finished_only_when_the_last_reply_ends_the_turn(self):
        call = {"type": "tool_use", "name": "mcp__computer-use__app_click"}
        self.assertFalse(claude_finished(self.transcript([assistant("tool_use", call)]))[0])
        self.assertTrue(claude_finished(self.transcript([assistant("tool_use", call),
                                                         assistant("end_turn", {"type": "text"})]))[0])

    def test_tools_other_than_computer_use_are_named(self):
        entries = [assistant("tool_use", {"type": "tool_use", "name": "ToolSearch"},
                             {"type": "tool_use", "name": "mcp__computer-use__app_screenshot"},
                             {"type": "tool_use", "name": "Skill", "input": {"skill": "anthropic-skills:computer-use"}},
                             {"type": "tool_use", "name": "Skill", "input": {"skill": "anthropic-skills:pdf"}},
                             {"type": "tool_use", "name": "Bash"})]
        self.assertEqual(foreign_tools(entries), ["Bash", "Skill anthropic-skills:pdf"])


class FrontmostTest(unittest.TestCase):
    def test_apps_in_front_during_the_run_other_than_the_allowed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for t, app in ((0, "Notes"), (1, "Claude"), (5, "Finder"), (6, "Mail"), (12, "Calendar")):
                f.write(json.dumps({"time": t, "front": app}) + "\n")
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(other_apps(f.name, 2, 10, ("Claude", "Finder")), ["Mail"])


class FinderGradeTest(unittest.TestCase):
    def test_ds_store_is_left_out(self):
        with tempfile.TemporaryDirectory() as root:
            files_task.make(root)
            os.rename(os.path.join(root, "report-draft.txt"), os.path.join(root, "Archive", "report-final.txt"))
            for folder in (root, os.path.join(root, "Archive")):
                open(os.path.join(folder, ".DS_Store"), "w").close()
            self.assertEqual(files_task.grade(root, ignore={".DS_Store"})[0], [])
            self.assertEqual(files_task.grade(root)[0], ["unexpected: .DS_Store", "unexpected: Archive/.DS_Store"])


class ServiceTierTest(unittest.TestCase):
    def tier(self, line):
        with tempfile.TemporaryDirectory() as out:
            with open(os.path.join(out, "agent.stderr"), "w") as f:
                f.write("Reading additional input from stdin...\n" + line + "\n")
            return service_tier(out)

    def test_reads_the_session_configuration(self):
        event = ('INFO codex_exec: Codex initialized with event: SessionConfiguredEvent { model: "gpt-6.1-sol", '
                 'model_provider_id: "openai", service_tier: TIER, approval_policy: Never }')
        self.assertEqual(self.tier(event.replace("TIER", "None")), "standard")
        self.assertEqual(self.tier(event.replace("TIER", 'Some("priority")')), "priority")
        self.assertIsNone(self.tier("INFO codex_exec: something else"))


class CodexLoginTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.root)
        self.user = os.path.join(self.root, "auth.json")
        self.home = os.path.join(self.root, "home")
        os.makedirs(os.path.join(self.home, ".codex"))
        with open(self.user, "w") as f:
            f.write("login 1")
        patcher = unittest.mock.patch.object(run_mac, "USER_AUTH", self.user)
        patcher.start()
        self.addCleanup(patcher.stop)

    def renew(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def read(self, path):
        with open(path) as f:
            return f.read()

    def test_a_renewed_login_goes_back_to_the_user(self):
        copied = run_mac.auth_in(self.home)
        self.renew(os.path.join(self.home, ".codex", "auth.json"), "login 2")
        run_mac.auth_out(self.home, copied)
        self.assertEqual(self.read(self.user), "login 2")

    def test_never_over_a_login_the_user_renewed_meanwhile(self):
        copied = run_mac.auth_in(self.home)
        self.renew(os.path.join(self.home, ".codex", "auth.json"), "login 2")
        self.renew(self.user, "login 3")
        run_mac.auth_out(self.home, copied)
        self.assertEqual(self.read(self.user), "login 3")


class InkscapeOwnershipTest(unittest.TestCase):
    def test_a_run_that_started_no_inkscape_ends_none(self):
        task = run_mac.Inkscape("inkscape", tempfile.mkdtemp(), 25)
        with unittest.mock.patch.object(run_mac.os, "kill") as kill, \
                unittest.mock.patch.object(run_mac.subprocess, "run") as run:
            task.stop()
        kill.assert_not_called()
        run.assert_not_called()

    def test_only_the_runs_own_inkscape_is_ended(self):
        task = run_mac.Inkscape("inkscape", tempfile.mkdtemp(), 25)
        task.pid = 4242
        with unittest.mock.patch.object(run_mac.os, "kill", side_effect=[None, ProcessLookupError()]) as kill:
            task.stop()
        self.assertEqual([c.args for c in kill.call_args_list], [(4242, signal.SIGTERM), (4242, 0)])


class InkscapeGradeTest(unittest.TestCase):
    """The Mac grades the Inkscape task with the VM's checker: a moved rectangle passes, a changed style fails."""

    def grade(self, edit):
        out = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, out)
        task = run_mac.Inkscape("inkscape", out, 25)
        os.makedirs(os.path.dirname(task.drawing))
        with open(os.path.join(run_mac.TASKS_DIR, "inkscape", "drawing.svg")) as f:
            svg = f.read()
        with open(task.drawing, "w") as f:
            f.write(edit(svg))
        with unittest.mock.patch.object(run_mac.Inkscape, "geometry", return_value=[122.0, 120.0, 200.0, 100.0]):
            return task.grade(0)

    def test_moved_and_saved(self):
        self.assertEqual(self.grade(lambda svg: svg.replace('x="120"', 'x="122"'))["failures"], [])

    def test_a_changed_style_fails_as_in_the_vm(self):
        failures = self.grade(lambda svg: svg.replace('x="120"', 'x="122"').replace("#3478c6", "#d62f2f"))["failures"]
        self.assertTrue(any("fill" in f for f in failures), failures)


class PublishedDataTest(unittest.TestCase):
    def test_lists_personal_strings_and_other_apps(self):
        run = {"task": "files", "backend": "cua", "rep": 1, "platform": "macos",
               "final": "Done in /Users/mara/Project, see https://intranet.example.org",
               "failures": ["an app other than Finder came to the front: Messages"],
               "focus": {"events": [{"to": "Finder"}, {"to": "Mail"}, {"to": "TextEdit"}]}}
        found = {(kind, match) for _, kind, match, _ in mac_privacy.audit([run], ["Project"])}
        self.assertEqual(found, {("home path", "/Users/mara"), ("web address", "https://intranet.example.org"),
                                 ("watched", "Project"), ("app", "Mail"), ("app", "Messages")})

    def test_ipv6_addresses_and_tilde_paths(self):
        run = {"task": "files", "backend": "cua", "rep": 1, "platform": "macos",
               "final": "Synced to fd7a:115c:a1e0::6 and 2001:db8:0:0:0:0:0:1 from ~/Documents/notes at 10:00:00",
               "failures": [], "focus": {"events": []}}
        found = {(kind, match) for _, kind, match, _ in mac_privacy.audit([run], [])}
        self.assertEqual(found, {("IPv6 address", "fd7a:115c:a1e0::6"), ("IPv6 address", "2001:db8:0:0:0:0:0:1"),
                                 ("home path", "~/Documents/notes")})

    def test_task_apps_alone_pass(self):
        run = {"task": "form", "backend": "openai-cu", "rep": 1, "platform": "macos", "final": "Done",
               "failures": [], "focus": {"events": [{"to": "Questionnaire"}, {"to": "TextEdit"}]}}
        self.assertEqual(mac_privacy.audit([run], []), [])


if __name__ == "__main__":
    unittest.main()
