#!/usr/bin/env python3
"""Tests run.py's harnesses without their APIs: python3 harness_test.py

pi's isolation, claude-api's key and its check, the names of runs, and report.py's reading of a recorded pi stream.
Fake `pi` and `claude` executables on PATH stand in for the real ones."""

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
import report  # noqa: E402
import run  # noqa: E402

KEY = "sk-ant-test-0123456789abcdefghijklmnopqrstuvwxyz"


def executable(directory, name, script):
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + script)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class Names(unittest.TestCase):
    def options(self, *agents):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
        run.add_run_options(parser)
        argv = ["--task", "forgejo"] + [a for agent in agents for a in ("--agent", agent)]
        return run.check_run_options(parser.parse_args(argv), parser)[0]

    def test_a_model_with_slashes_gets_a_directory_name(self):
        self.assertEqual(run.run_name("pi", "tensorx/deepseek/deepseek-v4.1-flash-1017", 2),
                         "pi-tensorx_deepseek_deepseek-v4.1-flash-1017-2")
        self.assertEqual(run.run_name("claude-api", "claude-haiku-5-5", 1), "claude-api-claude-haiku-5-5-1")
        self.assertEqual(self.options("pi:tensorx/deepseek/x", "claude-api:claude-sonnet-5-5"),
                         [("pi", "tensorx/deepseek/x"), ("claude-api", "claude-sonnet-5-5")])

    def test_agents_that_would_share_a_directory_or_are_not_harness_model_are_refused(self):
        for agents in (["pi:a/b", "pi:a_b"], ["gemini:pro"], ["pi"], ["pi:"]):
            with self.assertRaises(ValueError, msg=agents):
                self.options(*agents)


class PiIsolation(unittest.TestCase):
    def test_the_agent_directory_has_the_users_credentials_and_models_and_only_the_tap(self):
        with tempfile.TemporaryDirectory() as user, tempfile.TemporaryDirectory() as work:
            for name, content in (("auth.json", '{"tensorx": {"type": "api_key", "key": "secret"}}'),
                                  ("models.json", '{"providers": {}}'),
                                  ("mcp.json", '{"mcpServers": {"own": {"command": "own-server"}}}'),
                                  ("settings.json", '{"defaultThinkingLevel": "max", "packages": ["npm:x"]}'),
                                  ("AGENTS.md", "the user's instructions")):
                with open(os.path.join(user, name), "w") as f:
                    f.write(content)
            os.makedirs(os.path.join(user, "extensions"))
            tap = ["python3", "mcp_tap.py", "4242", "5000", "log"]
            with mock.patch.dict(os.environ, {"PI_CODING_AGENT_DIR": user}):
                agent_dir = run.pi_agent_dir(tap, os.path.join(work, "sessions"))
            try:
                self.assertEqual(sorted(os.listdir(agent_dir)), ["auth.json", "mcp.json", "models.json", "settings.json"])
                for name in ("auth.json", "models.json"):  # linked, so that no copy of the keys is written
                    self.assertEqual(os.readlink(os.path.join(agent_dir, name)), os.path.join(user, name))
                with open(os.path.join(agent_dir, "mcp.json")) as f:
                    servers = json.load(f)["mcpServers"]
                self.assertEqual(list(servers), ["hyprmasq"])
                self.assertEqual((servers["hyprmasq"]["command"], servers["hyprmasq"]["args"]), (tap[0], tap[1:]))
                self.assertEqual(servers["hyprmasq"]["exposure"], "direct")
                with open(os.path.join(agent_dir, "settings.json")) as f:
                    self.assertNotIn("packages", json.load(f))
                self.assertTrue(os.path.isdir(os.path.join(work, "sessions")))
            finally:
                shutil.rmtree(agent_dir)
            self.assertEqual(sorted(os.listdir(user)),
                             ["AGENTS.md", "auth.json", "extensions", "mcp.json", "models.json", "settings.json"])

    def test_the_run_has_only_hyprmasqs_tools_and_the_debrief_none(self):
        command = run.agent_command("pi", "tensorx/m", "Do it.", ["tap"], sessions="/s")
        self.assertEqual(command[command.index("--tools") + 1], "mcp__hyprmasq__*")
        for flag in ("--no-extensions", "--no-skills", "--no-context-files", "--no-prompt-templates", "--no-approve"):
            self.assertIn(flag, command)
        # The one extension loaded again after --no-extensions is the built-in MCP client.
        self.assertEqual([command[i + 1] for i, a in enumerate(command) if a == "-e"], ["builtin:mcp"])
        self.assertEqual(command[-2:], ["--", "Do it."])
        debrief = run.agent_command("pi", "tensorx/m", "Tell me.", None, resume="/s/x_id.jsonl")
        self.assertIn("--no-tools", debrief)
        self.assertNotIn("-e", debrief)
        self.assertEqual(debrief[debrief.index("--session") + 1], "/s/x_id.jsonl")
        with self.assertRaises(ValueError):
            run.agent_command("pi", "tensorx/m", "@file", ["tap"], sessions="/s")


class ClaudeApi(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.TemporaryDirectory()
        self.user = tempfile.TemporaryDirectory()
        with open(os.path.join(self.user.name, "models.json"), "w") as f:
            json.dump({"providers": {"anthropic": {"headers": {"anthropic-workspace-id": "wrkspc_1"}}}}, f)
        clean = {k: v for k, v in os.environ.items() if k not in (run.API_KEY_ENV, run.API_HEADERS_ENV)}
        self.env = mock.patch.dict(os.environ, {**clean, "PATH": self.bin.name + os.pathsep + os.environ["PATH"],
                                                "PI_CODING_AGENT_DIR": self.user.name}, clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.bin.cleanup()
        self.user.cleanup()

    def test_the_key_comes_from_pi_or_the_environment_with_pis_headers(self):
        executable(self.bin.name, "pi", f'test "$*" = "auth print-api-key --provider anthropic" && echo {KEY}\n')
        self.assertEqual(run.anthropic_api_env(),
                         {"ANTHROPIC_API_KEY": KEY, "ANTHROPIC_CUSTOM_HEADERS": "anthropic-workspace-id: wrkspc_1"})
        with mock.patch.dict(os.environ, {run.API_KEY_ENV: "sk-ant-other", run.API_HEADERS_ENV: ""}):
            self.assertEqual(run.anthropic_api_env(), {"ANTHROPIC_API_KEY": "sk-ant-other"})
        executable(self.bin.name, "pi", "echo 'no credentials' >&2; exit 1\n")
        with self.assertRaises(RuntimeError):
            run.anthropic_api_env()

    def test_the_key_reaches_only_the_claude_api_process(self):
        with mock.patch.dict(os.environ, {run.API_KEY_ENV: KEY, "ANTHROPIC_API_KEY": KEY, "PI_CODING_AGENT_DIR": "/x"}):
            self.assertFalse({k: v for k, v in run.agent_env().items() if v == KEY or k.startswith("PI_")})
            self.assertEqual(run.agent_env({"ANTHROPIC_API_KEY": KEY})["ANTHROPIC_API_KEY"], KEY)

    def test_a_claude_code_that_would_use_the_subscription_is_refused_before_it_starts(self):
        status = os.path.join(self.bin.name, "status.json")
        executable(self.bin.name, "claude", f'test "$*" = "auth status --json" && cat {status}\n')
        with open(status, "w") as f:
            json.dump({"loggedIn": True, "authMethod": "claude.ai"}, f)
        with self.assertRaises(RuntimeError):
            run.check_api_auth({"ANTHROPIC_API_KEY": KEY})
        with open(status, "w") as f:
            json.dump({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"}, f)
        self.assertEqual(run.check_api_auth({"ANTHROPIC_API_KEY": KEY}),
                         {"auth_method": "api_key", "api_key_source": "ANTHROPIC_API_KEY"})

    def test_an_agent_whose_init_names_other_credentials_or_none_is_stopped(self):
        for source, recorded in (("none", "none"), (None, "(not given)")):
            init = {"type": "system", "subtype": "init", "session_id": "s1", "apiKeySource": source}
            agent = executable(self.bin.name, "agent", f"echo '{json.dumps(init)}'; sleep 30; echo late\n")
            with tempfile.TemporaryDirectory() as out:
                started = time.time()
                result = run.run_agent([agent], out, os.path.join(out, "a.jsonl"), os.path.join(out, "a.err"), 60,
                                       {"ANTHROPIC_API_KEY": KEY}, api_key=True)
                self.assertLess(time.time() - started, 10)
                self.assertEqual((result["refused"], result["api_key_source"], result["session"]), (True, recorded, "s1"))
                self.assertEqual([e["line"] for e in report.load_jsonl(os.path.join(out, "a.jsonl"))], [init])

    def test_a_run_or_debrief_stopped_for_other_credentials_fails(self):
        for what in ("agent", "debrief"):
            with self.assertRaises(RuntimeError, msg=what):
                run.check_api_key_used({"refused": True, "api_key_source": "none"}, what)
        run.check_api_key_used({"refused": False, "api_key_source": "ANTHROPIC_API_KEY"}, "debrief")

    def test_files_holding_the_key_are_found(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "sub"))
            for name, text in (("agent.jsonl", "clean"), ("sub/transcript.jsonl", f'{{"k": "{KEY}"}}')):
                with open(os.path.join(out, name), "w") as f:
                    f.write(text)
            self.assertEqual(run.files_holding(out, KEY), ["sub/transcript.jsonl"])


class PiReport(unittest.TestCase):
    def test_usage_and_final_answer_of_a_recorded_pi_stream(self):
        # pi 1.3.0's --mode json stream of a run with one MCP tool, ping, through the tap's configuration.
        stream = report.load_jsonl(os.path.join(BENCH, "testdata", "pi-agent.jsonl"))
        self.assertEqual(report.agent_usage("pi", stream), {
            "turns": 2, "input_tokens": 780 + 881, "output_tokens": 88 + 21, "thinking_tokens": 39 + 1,
            "cost_usd": 0.000994, "final": "**Result of `mcp__hyprmasq__ping`:** `pong`",
        })
        self.assertEqual(run.session_of(stream[0]["line"]), "01a14d58-6684-7320-9b05-7a5834be9c38")

    def test_compaction_requests_count_in_the_usage(self):
        stream = report.load_jsonl(os.path.join(BENCH, "testdata", "pi-agent.jsonl"))
        # A compaction_end as pi's JSON stream documents it (docs/json.md), with the usage of its summary request.
        compaction = {"type": "compaction_end", "reason": "threshold", "aborted": False, "willRetry": False, "result": {
            "summary": "…", "tokensBefore": 150000, "usage": {"input": 150000, "output": 2000, "cacheRead": 0,
                                                           "cacheWrite": 0, "cost": {"total": 0.078}}}}
        usage = report.agent_usage("pi", stream[:-2] + [{"t": 0, "line": compaction}] + stream[-2:])
        self.assertEqual((usage["turns"], usage["input_tokens"], usage["output_tokens"], usage["cost_usd"]),
                         (2, 1661 + 150000, 109 + 2000, 0.078994))


if __name__ == "__main__":
    unittest.main()
