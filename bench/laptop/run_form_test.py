"""Select only this run's questionnaire and stop the desktop agent on every exit."""

import os
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_form


class QuestionnaireSelection(unittest.TestCase):
    def setUp(self):
        self.existing = {"address": "0xold", "title": "Questionnaire", "pid": 100,
                         "workspace": {"name": "form"}}
        self.owned = {"address": "0xnew", "title": "Questionnaire", "pid": 200,
                      "workspace": {"name": "form"}}

    def test_existing_questionnaire_is_not_selected(self):
        with patch.object(run_form, "clients", return_value=[self.existing, self.owned]):
            self.assertEqual(run_form.questionnaire_window(200), self.owned)

    def test_waits_until_the_owned_window_maps(self):
        with patch.object(run_form, "clients", side_effect=[[self.existing], [self.existing, self.owned]]), \
                patch.object(run_form.time, "sleep"):
            selected = run_form.wait_for("the questionnaire", lambda: run_form.questionnaire_window(200))
        self.assertEqual(selected, self.owned)

    def test_matching_process_must_have_questionnaire_title(self):
        with patch.object(run_form, "clients", return_value=[self.existing, dict(self.owned, title="Starting")]):
            self.assertIsNone(run_form.questionnaire_window(200))

    def test_setup_rejects_a_different_questionnaire_process(self):
        before = {"active_workspace": {"name": run_form.USER_WORKSPACE}, "clients": [self.existing],
                  "form": self.existing["address"], "form_pid": 200}
        with self.assertRaisesRegex(SystemExit, "does not belong"):
            run_form.check_setup(before)

    def test_setup_accepts_the_owned_questionnaire(self):
        before = {"active_workspace": {"name": run_form.USER_WORKSPACE}, "clients": [self.owned],
                  "form": self.owned["address"], "form_pid": 200}
        run_form.check_setup(before)


class FailedStream:
    def __init__(self, stream, error):
        self.stream, self.error = stream, error

    def __iter__(self):
        raise self.error

    def close(self):
        self.stream.close()


class LinuxAgentCleanup(unittest.TestCase):
    def stubborn_agent(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             "print('ready',flush=True); time.sleep(60)"],
            stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(proc.stdout.close)
        self.assertEqual(proc.stdout.readline().strip(), "ready")
        return proc

    def run_with(self, proc, out, timeout):
        with patch.object(run_form.subprocess, "Popen", return_value=proc), \
                patch.object(run_form, "HOMES", out), patch.object(run_form, "ENV", dict(os.environ)):
            return run_form.run_agent("hyprmasq", "test-model", "medium", out, timeout)

    def test_interruption_cleans_up_and_propagates(self):
        proc = self.stubborn_agent()
        proc.stdout = FailedStream(proc.stdout, KeyboardInterrupt())
        with tempfile.TemporaryDirectory() as out, self.assertRaises(KeyboardInterrupt):
            self.run_with(proc, out, 60)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_stream_failure_cleans_up_and_propagates(self):
        proc = self.stubborn_agent()
        proc.stdout = FailedStream(proc.stdout, OSError("stream failed"))
        with tempfile.TemporaryDirectory() as out, self.assertRaisesRegex(OSError, "stream failed"):
            self.run_with(proc, out, 60)
        self.assertEqual(proc.returncode, -signal.SIGKILL)

    def test_timeout_cleans_up_and_is_reported(self):
        proc = self.stubborn_agent()
        with tempfile.TemporaryDirectory() as out:
            result = self.run_with(proc, out, 0.01)
        self.assertTrue(result["timed_out"])
        self.assertEqual(proc.returncode, -signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
