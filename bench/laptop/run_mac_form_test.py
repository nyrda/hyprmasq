#!/usr/bin/env python3
"""The detached CLI must stop when a run times out or its caller is interrupted."""

import signal
import select
import subprocess
import sys
import unittest
from unittest.mock import patch

from run_mac_form import task_times, wait_agent


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


if __name__ == "__main__":
    unittest.main()
