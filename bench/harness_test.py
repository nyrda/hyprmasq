"""Tests run.py's harnesses without their APIs: python3 harness_test.py

pi's isolation, claude-api's key and its check, the names of runs, the efforts agents run at, and report.py's reading
of a recorded pi stream and of each harness's usage and context.
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
import export  # noqa: E402
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
                         [("pi", "tensorx/deepseek/x", None), ("claude-api", "claude-sonnet-5-5", None)])

    def test_an_effort_follows_the_last_at_and_names_the_directory(self):
        self.assertEqual(
            self.options("codex:gpt-6.1-sol@high", "pi:tensorx/deepseek/m:free@max", "claude:claude-opus-5-5"),
            [("codex", "gpt-6.1-sol", "high"), ("pi", "tensorx/deepseek/m:free", "max"),
             ("claude", "claude-opus-5-5", None)])
        self.assertEqual(run.run_name("codex", "gpt-6.1-sol", 1, "high"), "codex-gpt-6.1-sol-high-1")
        self.assertEqual(run.run_name("pi", "tensorx/deepseek/m", 2, "max"), "pi-tensorx_deepseek_m-max-2")
        # The same agent at two efforts is two agents, with a directory each.
        self.assertEqual(len(self.options("codex:gpt@low", "codex:gpt@high", "codex:gpt")), 3)

    def test_agents_that_would_share_a_directory_or_are_not_harness_model_are_refused(self):
        for agents in (["pi:a/b", "pi:a_b"], ["gemini:pro"], ["pi"], ["pi:"], ["codex:gpt-high", "codex:gpt@high"],
                       ["codex:@high"]):
            with self.assertRaises(ValueError, msg=agents):
                self.options(*agents)

    def test_a_resume_refuses_an_agent_whose_directory_holds_another_agents_run(self):
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "codex-gpt-high-1"))
            with open(os.path.join(out, "codex-gpt-high-1", "run.json"), "w") as f:
                json.dump({"harness": "codex", "model": "gpt-high", "rep": 1}, f)
            parser = argparse.ArgumentParser()
            parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
            config = {"commit": "c", "task": "forgejo", "variant": None, "display": "1920x1080@1"}
            run.check_resume(out, config, parser, [("codex", "gpt-high", None), ("codex", "gpt", "low")])
            with self.assertRaisesRegex(ValueError, "holds a run of codex:gpt-high, not of codex:gpt@high"):
                run.check_resume(out, config, parser, [("codex", "gpt", "high")])

    def test_an_effort_the_harness_does_not_take_is_refused(self):
        for agent in ("claude:opus@minimal", "claude-api:sonnet@none", "codex:gpt@off", "pi:p/m@extreme", "codex:gpt@"):
            with self.assertRaisesRegex(ValueError, "takes no effort", msg=agent):
                self.options(agent)


class ExportAgents(unittest.TestCase):
    def test_agent_patterns_pick_the_runs_of_a_dataset(self):
        def record(harness, model, effort):
            return {"harness": harness, "model": model, "effort": effort}
        sol, sol_high = record("codex", "gpt-6.1-sol", "medium"), record("codex", "gpt-6.1-sol", "high")
        opus, default = record("claude-api", "claude-opus-5-5", "medium"), record("claude", "claude-sonnet-5-5", "default")
        self.assertTrue(all(export.wanted(r, []) for r in (sol, sol_high, opus, default)))
        medium = ["*:*@medium"]
        self.assertEqual([export.wanted(r, medium) for r in (sol, sol_high, opus, default)], [True, False, True, False])
        self.assertEqual([export.wanted(r, ["codex:gpt-6.1-sol"]) for r in (sol, sol_high, opus)], [True, True, False])
        self.assertTrue(export.wanted(default, ["claude:claude-sonnet-5-5"]))


class Effort(unittest.TestCase):
    def flags(self, harness, effort, **kwargs):
        command = run.agent_command(harness, "m", "Do it.", ["tap"], sessions="/s", effort=effort, **kwargs)
        flag = {"codex": "-c", "pi": "--thinking"}.get(harness, "--effort")
        return [command[i + 1] for i, a in enumerate(command[:-1])
                if a == flag and (flag != "-c" or command[i + 1].startswith("model_reasoning_effort"))]

    def test_each_harness_gets_the_level_its_own_way_in_the_run_and_the_debrief(self):
        for harness, given in (("claude", "xhigh"), ("claude-api", "xhigh"),
                               ("codex", 'model_reasoning_effort="xhigh"'), ("pi", "xhigh")):
            self.assertEqual(self.flags(harness, "xhigh"), [given], harness)
            self.assertEqual(self.flags(harness, "xhigh", resume="session"), [given], harness)
            # Without an effort, the harness's default: no flag at all.
            self.assertEqual(self.flags(harness, None), [], harness)

    def test_the_effort_codex_and_claude_code_report_is_recorded(self):
        with tempfile.TemporaryDirectory() as run_dir, tempfile.TemporaryDirectory() as home:
            meta = {"x-codex-turn-metadata": {"reasoning_effort": "high", "model": "gpt"}}
            with open(os.path.join(run_dir, "mcp.jsonl"), "w") as f:
                for i in range(2):
                    f.write(json.dumps({"t": i, "dir": "request", "msg": {
                        "id": i, "method": "tools/call", "params": {"_meta": meta, "name": "windows"}}}) + "\n")
            self.assertEqual(run.ran_effort("codex", run_dir, "/tmp/x", "s", None),
                             ("high", "x-codex-turn-metadata of the tool calls in mcp.jsonl"))
            # The summary keeps where the effort came from.
            recorded = {"requested": "high", "ran_at": "high",
                        "reported_by": "x-codex-turn-metadata of the tool calls in mcp.jsonl"}
            self.assertEqual(report.run_effort({"harness": "codex", "effort": recorded}, run_dir, []), recorded)
            with mock.patch.dict(os.environ, {"HOME": home}):
                os.makedirs(run.claude_project("/tmp/x"))
                with open(os.path.join(run.claude_project("/tmp/x"), "s1.jsonl"), "w") as f:
                    for effort in ("max", "max"):
                        request = {"type": "api-request", "params": {"output_config": {"effort": effort}}}
                        f.write(json.dumps(request) + "\n")
                self.assertEqual(run.ran_effort("claude-api", run_dir, "/tmp/x", "s1", None)[0], "max")
                # A harness that says nothing records nothing, not a guess.
                self.assertEqual(run.ran_effort("claude", run_dir, "/tmp/x", "other", None), (None, None))
            self.assertEqual(run.ran_effort("pi", run_dir, "/tmp/x", "s", "high")[0], "high")

    def test_runs_at_different_efforts_are_rows_of_their_own(self):
        def summary(requested, ran_at):
            return {"harness": "codex", "model": "gpt", "effort": {"requested": requested, "ran_at": ran_at},
                    "passed": True, "wall_seconds": 1, "tool_seconds": 1, "outside_tool_seconds": 0, "calls": 1,
                    "actions": 1, "images": 1}
        rows = report.table([summary(None, "low"), summary("high", "high"), summary(None, "low")], [],
                            lambda r: f"{r['harness']} {r['model']}")[2:]
        self.assertEqual([row.split(" | ")[:3] for row in rows],
                         [["| codex gpt", "default, ran low", "2/2"], ["| codex gpt", "high", "1/1"]])


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

    def test_pi_receives_the_model_effort_and_mcp_tools_and_the_debrief_has_none(self):
        # Interpret the options that affect this contract, without fixing their order or unrelated flags.
        parser = argparse.ArgumentParser()
        parser.add_argument("--model")
        parser.add_argument("--thinking")
        parser.add_argument("--tools")
        parser.add_argument("--no-tools", action="store_true")
        parser.add_argument("-e", "--extension", action="append", default=[])
        parser.add_argument("--session")
        parser.add_argument("--session-dir")
        tap = ["python3", "mcp_tap.py", "4242", "5000", "log"]
        with tempfile.TemporaryDirectory() as work:
            sessions = os.path.join(work, "sessions")
            agent_dir = run.pi_agent_dir(tap, sessions)
            try:
                command = run.agent_command("pi", "tensorx/m", "Do it.", tap, sessions=sessions, effort="high")
                options, _ = parser.parse_known_args(command[1:])
                self.assertEqual((options.model, options.thinking), ("tensorx/m", "high"))
                self.assertEqual(options.tools, "mcp__hyprmasq__*")
                self.assertFalse(options.no_tools)
                self.assertIn("builtin:mcp", options.extension)
                self.assertEqual(options.session_dir, sessions)
                with open(os.path.join(agent_dir, "mcp.json")) as f:
                    servers = json.load(f)["mcpServers"]
                self.assertEqual(list(servers), ["hyprmasq"])
                self.assertEqual([servers["hyprmasq"]["command"], *servers["hyprmasq"]["args"]], tap)
                debrief = run.agent_command("pi", "tensorx/m", "Tell me.", None,
                                            resume="/s/x_id.jsonl", effort="high")
                options, _ = parser.parse_known_args(debrief[1:])
                self.assertEqual((options.model, options.thinking), ("tensorx/m", "high"))
                self.assertTrue(options.no_tools)
                self.assertIsNone(options.tools)
                self.assertEqual(options.extension, [])
                self.assertEqual(options.session, "/s/x_id.jsonl")
            finally:
                shutil.rmtree(agent_dir)
        with self.assertRaises(ValueError):
            run.agent_command("pi", "tensorx/m", "@file", tap, sessions="/s")


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
            "turns": 2, "input_tokens": 780 + 881, "cache_read_tokens": 0, "cache_write_tokens": 0,
            "output_tokens": 88 + 21, "thinking_tokens": 39 + 1, "cost_usd": 0.000994, "final": "**Result of `mcp__hyprmasq__ping`:** `pong`",
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

    def test_context_is_the_prompt_of_each_reply_and_the_compactions_that_ended(self):
        stream = report.load_jsonl(os.path.join(BENCH, "testdata", "pi-agent.jsonl"))
        compaction = {"type": "compaction_end", "aborted": False, "result": {"usage": {"input": 150000}}}
        aborted = {"type": "compaction_end", "aborted": True}
        # A request that failed before its prompt counted is no size.
        failed = {"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "usage": {
            "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}}}
        stream = stream[:-2] + [{"t": 0, "line": line} for line in (compaction, aborted, failed)] + stream[-2:]
        self.assertEqual(report.context_usage("pi", stream, [], report.agent_usage("pi", stream)), {
            "source": "pi stream assistant message_end usage", "request_count": 2, "first_input_tokens": 780,
            "peak_input_tokens": 881, "last_input_tokens": 881, "growth_input_tokens": 101, "compactions": 1,
        })
        self.assertIsNone(report.context_usage("pi", stream[:3], [], {}))


class CodexReport(unittest.TestCase):
    @staticmethod
    def token_count(total, last):
        return {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": total, "last_token_usage": last}}}

    def run_files(self, model="gpt-6.1-sol"):
        """A run of two requests, the second with a prompt over LONG_PROMPT, and its debrief's request after them."""
        first = {"input_tokens": 10_000, "cached_input_tokens": 8_000, "cache_write_input_tokens": 0, "output_tokens": 100}
        second = {"input_tokens": 300_000, "cached_input_tokens": 200_000, "cache_write_input_tokens": 50_000,
                  "output_tokens": 1_000}
        total = {"input_tokens": 310_000, "cached_input_tokens": 208_000, "cache_write_input_tokens": 50_000,
                 "output_tokens": 1_100, "reasoning_output_tokens": 400}
        debrief = {"input_tokens": 320_000, "cached_input_tokens": 300_000, "output_tokens": 500}
        transcript = [
            {"type": "turn_context", "payload": {"model": model}},
            self.token_count(first, first),
            # The same totals again, sent when only the rate limits changed.
            self.token_count(first, first),
            self.token_count(total, second),
            {"type": "turn_context", "payload": {"model": model}},
            self.token_count({"input_tokens": 630_000, "output_tokens": 1_600}, debrief),
        ]
        stream = [{"t": 0, "line": {"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}}},
                  {"t": 1, "line": {"type": "turn.completed", "usage": total}}]
        return stream, transcript

    def test_a_codex_run_costs_what_its_requests_would_cost_on_the_api(self):
        stream, transcript = self.run_files()
        # The short prompt at the base prices, the long one at the long-prompt prices; the debrief's not at all.
        cost = (2_000 * 2.00 + 8_000 * 0.10 + 100 * 10.00) / 1e6 \
            + (50_000 * 4.00 + 200_000 * 0.20 + 50_000 * 5.00 + 1_000 * 15.00) / 1e6
        self.assertEqual(report.agent_usage("codex", stream, transcript), {
            "input_tokens": 310_000, "cache_read_tokens": 208_000, "cache_write_tokens": 50_000,
            "output_tokens": 1_100, "thinking_tokens": 400, "cost_usd": round(cost, 6),
            "cost_basis": "API list prices for gpt-6.1-sol", "final": "Done",
        })

    def test_terra_uses_its_api_prices(self):
        stream, transcript = self.run_files(model="gpt-6.1-terra")
        cost = (2_000 * 1.00 + 8_000 * 0.05 + 100 * 5.00) / 1e6 \
            + (50_000 * 2.00 + 200_000 * 0.10 + 50_000 * 2.50 + 1_000 * 7.50) / 1e6
        usage = report.agent_usage("codex", stream, transcript)
        self.assertEqual(usage["cost_usd"], round(cost, 6))
        self.assertEqual(usage["cost_basis"], "API list prices for gpt-6.1-terra")

    def test_no_cost_without_the_prices_or_the_requests_of_the_run(self):
        stream, transcript = self.run_files(model="gpt-unpriced")
        self.assertIsNone(report.agent_usage("codex", stream, transcript)["cost_usd"])
        stream, transcript = self.run_files()
        # A transcript that ends before the requests add up to the run's total.
        self.assertIsNone(report.agent_usage("codex", stream, transcript[:3])["cost_usd"])
        # A transcript that misses a request, though its last totals match the run's.
        self.assertIsNone(report.agent_usage("codex", stream, transcript[:1] + transcript[3:])["cost_usd"])
        # Requests whose cache reads do not add up to the run's.
        stream[1]["line"]["usage"] = {**stream[1]["line"]["usage"], "cached_input_tokens": 0}
        self.assertIsNone(report.agent_usage("codex", stream, transcript)["cost_usd"])
        self.assertNotIn("cost_usd", report.agent_usage("codex", stream))

    def test_context_is_the_task_requests_and_compactions_without_the_debriefs(self):
        stream, transcript = self.run_files()
        compacted = {"type": "compacted", "payload": {"message": "summary", "replacement_history": []}}
        # A compaction during the task, and the totals Codex sends again after it, and one in the debrief.
        transcript[2:3] = [compacted, transcript[1]]
        transcript.insert(-1, compacted)
        context = report.context_usage("codex", stream, transcript, report.agent_usage("codex", stream, transcript))
        self.assertEqual(context, {
            "source": "Codex transcript token_count records", "request_count": 2, "first_input_tokens": 10_000,
            "peak_input_tokens": 300_000, "last_input_tokens": 300_000, "growth_input_tokens": 290_000,
            "compactions": 1,
        })

    def test_context_and_compactions_are_unknown_without_their_evidence(self):
        stream, transcript = self.run_files()
        usage = report.agent_usage("codex", stream, transcript)
        # Codex announced a compaction that it did not record.
        announced = {"type": "event_msg", "payload": {"type": "context_compacted"}}
        context = report.context_usage("codex", stream, transcript[:2] + [announced] + transcript[2:], usage)
        self.assertEqual((context["request_count"], context["compactions"]), (2, None))
        # A transcript that ends before the requests add up to the run's total, none, and a run without usage.
        self.assertIsNone(report.context_usage("codex", stream, transcript[:3], usage))
        self.assertIsNone(report.context_usage("codex", stream, [], usage))
        self.assertIsNone(report.context_usage("codex", stream[:1], transcript, report.agent_usage("codex", stream[:1])))


class ClaudeReport(unittest.TestCase):
    def test_the_cache_reads_and_writes_count_in_the_input(self):
        result = {"type": "result", "num_turns": 3, "total_cost_usd": 0.12, "result": "Done", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 40_000, "cache_creation_input_tokens": 14_000,
            "output_tokens": 2_000, "output_tokens_details": {"thinking_tokens": 1_000}}}
        usage = report.agent_usage("claude-api", [{"t": 0, "line": result}])
        self.assertEqual((usage["input_tokens"], usage["cache_read_tokens"], usage["cache_write_tokens"],
                          usage["output_tokens"], usage["cost_usd"]), (54_010, 40_000, 14_000, 2_000, 0.12))

    def test_context_is_the_prompt_of_each_message_once_and_the_compactions(self):
        def assistant(message_id, cache_read, block):
            return {"t": 0, "line": {"type": "assistant", "message": {"id": message_id, "content": [block], "usage": {
                "input_tokens": 5, "cache_read_input_tokens": cache_read, "cache_creation_input_tokens": 1_000}}}}
        # Each content block of a message comes as an event of its own, with the message's usage.
        stream = [assistant("m1", 20_000, {"type": "thinking"}), assistant("m1", 20_000, {"type": "tool_use"}),
                  {"t": 0, "line": {"type": "user", "message": {"content": [{"type": "tool_result"}]}}},
                  assistant("m2", 60_000, {"type": "tool_use"}),
                  {"t": 0, "line": {"type": "system", "subtype": "compact_boundary"}},
                  assistant("m3", 8_000, {"type": "text"})]
        self.assertEqual(report.context_usage("claude", stream, [], {}), {
            "source": "Claude Code stream-json assistant usage", "request_count": 3, "first_input_tokens": 21_005,
            "peak_input_tokens": 61_005, "last_input_tokens": 9_005, "growth_input_tokens": -12_000,
            "compactions": 1,
        })
        # A stream that ended before the first reply.
        self.assertIsNone(report.context_usage("claude-api", stream[4:5], [], {}))



class Pinning(unittest.TestCase):
    def test_the_vm_and_its_processes_run_on_the_slots_cpus(self):
        # A driver that starts a child, as it starts QEMU, which writes the CPUs it may run on; then gets ready and
        # waits for agent-done, as bench/vm.py does.
        before = os.sched_getaffinity(0)
        cpus = sorted(before)[-1:]
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": tmp}):
            driver = os.path.join(tmp, "driver")
            os.makedirs(os.path.join(driver, "bin"))
            executable(os.path.join(driver, "bin"), "nixos-test-driver", """
sh -c 'grep Cpus_allowed_list /proc/self/status > "$HYPRMASQ_BENCH_OUT/qemu-cpus"'
echo '{}' > "$HYPRMASQ_BENCH_OUT/ready.json"
while [ ! -e "$HYPRMASQ_BENCH_OUT/agent-done" ]; do sleep 0.1; done
""")
            run_dir = os.path.join(tmp, "run")
            os.makedirs(os.path.join(run_dir, "tmp"))
            os.makedirs(os.path.join(run_dir, "driver-out"))
            with run.booted_vm(driver, run_dir, 4999, 60, cpus) as proc:
                self.assertIsNone(proc.poll())
            self.assertEqual(proc.returncode, 0)
            with open(os.path.join(run_dir, "qemu-cpus")) as f:
                self.assertEqual(f.read().split(":")[1].strip(), run.format_cpus(cpus))
            # run.py, and so the agent it starts, stays on every CPU.
            self.assertEqual(os.sched_getaffinity(0), before)

if __name__ == "__main__":
    unittest.main()
