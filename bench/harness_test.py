"""Tests run.py's harnesses without their APIs: python3 harness_test.py

pi's isolation, claude-api's key and its check, the budget of tool calls, the names of runs, the efforts agents run at, and report.py's reading
of a recorded pi stream and of each harness's usage and context.
Fake `pi` and `claude` executables on PATH stand in for the real ones."""

import argparse
import concurrent.futures
import contextlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
import export  # noqa: E402
import mcp_tap  # noqa: E402
import capability  # noqa: E402
import capability_host  # noqa: E402
import report  # noqa: E402
import review_failures  # noqa: E402
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


class CuaBackend(unittest.TestCase):
    """The Cua backend's agent: Cua's skill and server as its documentation sets them up, in a home of its own."""

    def parse(self, command):
        """The options of a Claude Code command line that this contract is about, in any order."""
        values = {}
        for flag in ("--tools", "--allowedTools", "--mcp-config", "--setting-sources"):
            if flag in command:
                values[flag] = command[command.index(flag) + 1]
        values["slash commands"] = "--disable-slash-commands" not in command
        return values

    def test_the_agent_gets_cuas_skill_and_server_and_the_debrief_neither(self):
        tap = ["python3", "mcp_tap.py", "4242", "5000", "log"]
        claude = self.parse(run.agent_command("claude-api", "m", "Do it.", tap, backend="cua"))
        # The skill and the user settings of its fresh home, where Claude Code finds the skill.
        self.assertEqual((claude["--tools"], claude["slash commands"], claude["--setting-sources"]),
                         ("Skill,ListMcpResourcesTool,ReadMcpResourceTool", True, "user"))
        self.assertEqual(set(claude["--allowedTools"].split()),
                         {"Skill", "ListMcpResourcesTool", "ReadMcpResourceTool", "mcp__cua-driver"})
        self.assertEqual(json.loads(claude["--mcp-config"])["mcpServers"],
                         {"cua-driver": {"command": tap[0], "args": tap[1:]}})
        debrief = self.parse(run.agent_command("claude-api", "m", "Why?", None, resume="s", backend="cua"))
        self.assertEqual((debrief["--tools"], debrief["slash commands"], debrief["--setting-sources"]), ("", False, ""))
        # hyprmasq's runs stay as they were: no tools but its server's, no slash commands.
        hyprmasq = self.parse(run.agent_command("claude-api", "m", "Do it.", tap))
        self.assertEqual((hyprmasq["--tools"], hyprmasq["slash commands"], hyprmasq["--allowedTools"],
                          hyprmasq["--setting-sources"]), ("", False, "mcp__hyprmasq", ""))

        codex = run.agent_command("codex", "m", "Do it.", tap, backend="cua")
        settings = [codex[i + 1] for i, a in enumerate(codex[:-1]) if a == "-c"]
        self.assertIn(f"mcp_servers.cua-driver.command={json.dumps(tap[0])}", settings)
        self.assertIn(f"mcp_servers.cua-driver.tool_timeout_sec={run.TOOL_TIMEOUT}", settings)
        self.assertIn('mcp_servers.cua-driver.default_tools_approval_mode="approve"', settings)
        self.assertNotIn("skills.include_instructions=false", settings)
        self.assertIn("skills.include_instructions=false", run.agent_command("codex", "m", "Do it.", tap))

        # The prompt names the skill as each harness takes it; hyprmasq's prompt is the task's alone.
        self.assertEqual(run.task_prompt("claude-api", "cua", "Do it."), "/cua-driver Do it.")
        self.assertEqual(run.task_prompt("codex", "cua", "Do it."), "$cua-driver Do it.")
        self.assertEqual(run.task_prompt("codex", "hyprmasq", "Do it."), "Do it.")
        with self.assertRaisesRegex(ValueError, "pi runs only with hyprmasq"):
            run.agent_command("pi", "p/m", "Do it.", tap, sessions="/s", backend="cua")

    def test_hypruse_gets_its_server_alone_and_computer_use_linux_its_skill_too(self):
        tap = ["python3", "mcp_tap.py", "4242", "5000", "log"]
        # hypruse: the server, named as its README registers it, and no skill, whose verbs need a shell.
        claude = self.parse(run.agent_command("claude-api", "m", "Do it.", tap, backend="hypruse"))
        self.assertEqual((claude["--tools"], claude["slash commands"], claude["--allowedTools"],
                          claude["--setting-sources"]), ("", False, "mcp__hypruse", ""))
        self.assertEqual(list(json.loads(claude["--mcp-config"])["mcpServers"]), ["hypruse"])
        codex = run.agent_command("codex", "m", "Do it.", tap, backend="hypruse")
        settings = [codex[i + 1] for i, a in enumerate(codex[:-1]) if a == "-c"]
        self.assertIn("skills.include_instructions=false", settings)
        self.assertIn(f"mcp_servers.hypruse.tool_timeout_sec={run.TOOL_TIMEOUT}", settings)
        self.assertEqual(run.task_prompt("codex", "hypruse", "Do it."), "Do it.")

        # computer-use-linux: its server and its plugin's skill, named first in the prompt as Cua's is.
        claude = self.parse(run.agent_command("claude-api", "m", "Do it.", tap, backend="computer-use-linux"))
        self.assertEqual((claude["--tools"], claude["slash commands"], claude["--setting-sources"]),
                         ("Skill,ListMcpResourcesTool,ReadMcpResourceTool", True, "user"))
        self.assertEqual(set(claude["--allowedTools"].split()),
                         {"Skill", "ListMcpResourcesTool", "ReadMcpResourceTool", "mcp__computer-use-linux"})
        codex = run.agent_command("codex", "m", "Do it.", tap, backend="computer-use-linux")
        settings = [codex[i + 1] for i, a in enumerate(codex[:-1]) if a == "-c"]
        self.assertNotIn("skills.include_instructions=false", settings)
        self.assertIn(f"mcp_servers.computer-use-linux.command={json.dumps(tap[0])}", settings)
        self.assertEqual(run.task_prompt("claude", "computer-use-linux", "Do it."), "/computer-use-linux Do it.")
        self.assertEqual(run.task_prompt("codex", "computer-use-linux", "Do it."), "$computer-use-linux Do it.")
        for backend in ("hypruse", "computer-use-linux"):
            with self.assertRaisesRegex(ValueError, "pi runs only with hyprmasq"):
                run.agent_command("pi", "p/m", "Do it.", tap, sessions="/s", backend=backend)

    def test_the_agent_home_holds_the_skill_and_only_the_login_it_needs(self):
        with tempfile.TemporaryDirectory() as user, tempfile.TemporaryDirectory() as skill:
            os.makedirs(os.path.join(user, ".claude"))
            with open(os.path.join(user, ".claude", ".credentials.json"), "w") as f:
                json.dump({"claudeAiOauth": {}}, f)
            with mock.patch.dict(os.environ, {"HOME": user}):
                homes = {h: run.agent_home(h, skill) for h in ("claude-api", "claude", "codex")}
                bare = run.agent_home("codex")
                bare_claude = run.agent_home("claude")
            try:
                for harness in ("claude-api", "claude"):
                    home, env = homes[harness]
                    config = env["CLAUDE_CONFIG_DIR"]
                    self.assertEqual(list(env), ["CLAUDE_CONFIG_DIR"])
                    self.assertEqual(os.readlink(os.path.join(config, "skills", "cua-driver")), skill)
                    # The subscription's login; an API key needs none.
                    expected = ["skills"] + ([".credentials.json"] if harness == "claude" else [])
                    self.assertEqual(sorted(os.listdir(config)), sorted(expected))
                home, env = homes["codex"]
                self.assertEqual(env, {"HOME": home, "CODEX_HOME": os.path.join(home, ".codex")})
                self.assertEqual(os.readlink(os.path.join(home, ".agents", "skills", "cua-driver")), skill)
                self.assertEqual(sorted(os.listdir(home)), [".agents", ".codex"])
                # The login is linked into a CODEX_HOME of the run's own, with no skill for a backend without one.
                self.assertEqual(os.readlink(os.path.join(home, ".codex", "auth.json")),
                                 os.path.join(user, ".codex", "auth.json"))
                self.assertEqual(os.listdir(bare[0]), [".codex"])
                self.assertEqual(os.listdir(bare_claude[1]["CLAUDE_CONFIG_DIR"]), [".credentials.json"])
            finally:
                for home, _ in [*homes.values(), bare, bare_claude]:
                    shutil.rmtree(home)
            self.assertEqual(os.listdir(skill), [])

    def test_every_backend_runs_without_what_the_harnesses_bundle(self):
        tap = ["python3", "mcp_tap.py", "4242", "5000", "log"]
        for backend in run.BACKENDS:
            claude = run.agent_command("claude", "m", "Do it.", tap, backend=backend)
            settings = json.loads(claude[claude.index("--settings") + 1])
            self.assertEqual(settings, run.CLAUDE_SETTINGS)
            codex = run.agent_command("codex", "m", "Do it.", tap, backend=backend)
            self.assertIn("skills.bundled.enabled=false", codex)
            self.assertIn("check_for_update_on_startup=false", codex)
        env = run.agent_env()
        self.assertEqual((env["CLAUDE_CODE_DISABLE_BUNDLED_SKILLS"], env["DISABLE_AUTOUPDATER"]), ("1", "1"))

    def test_a_results_directory_keeps_one_backend_and_prompt(self):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
        config = {"commit": "c", "task": "forgejo", "variant": None, "display": "1920x1080@1"}
        with tempfile.TemporaryDirectory() as out:
            os.makedirs(os.path.join(out, "codex-gpt-1"))
            open(os.path.join(out, "codex-gpt-1", "summary.json"), "w").close()
            # A source.json from before the options: its runs were hyprmasq's, with the task's prompt.
            with open(os.path.join(out, "source.json"), "w") as f:
                json.dump(config, f)
            run.check_resume(out, {**config, "backend": "hyprmasq", "prompt": "task"}, parser, [])
            for change in ({"backend": "cua"}, {"prompt": "comparison"}):
                with self.assertRaisesRegex(ValueError, f"another {list(change)[0]}"):
                    run.check_resume(out, {**config, "backend": "hyprmasq", "prompt": "task", **change}, parser, [])
            with open(os.path.join(out, "source.json"), "w") as f:
                json.dump({**config, "backend": "cua", "prompt": "comparison"}, f)
            run.check_resume(out, {**config, "backend": "cua", "prompt": "comparison"}, parser, [])
            with self.assertRaisesRegex(ValueError, "another backend"):
                run.check_resume(out, {**config, "backend": "hyprmasq", "prompt": "comparison"}, parser, [])
            with self.assertRaisesRegex(ValueError, "another browser"):
                run.check_resume(out, {**config, "backend": "cua", "prompt": "comparison", "browser": "chromium"},
                                 parser, [])
            # A browser started with remote debugging is another measurement, kept out of the plain runs' directory.
            with self.assertRaisesRegex(ValueError, "another chromium_options"):
                run.check_resume(out, {**config, "backend": "cua", "prompt": "comparison",
                                       "chromium_options": "--remote-debugging-port=0"}, parser, [])

    def test_chromium_stands_in_for_firefox_only_in_the_tasks_that_list_it_and_the_prompt_names_it(self):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(ValueError(message))
        for task, variant in (("board", "dnd"), ("trace", "river"), ("calc", None)):
            run.check_task(task, variant, parser, "comparison", "chromium")
        for task, variant in (("form", "25"), ("forgejo", None), ("thunderbird", None)):
            with self.assertRaisesRegex(ValueError, f"task {task} has no browser chromium"):
                run.check_task(task, variant, parser, "comparison", "chromium")
        with open(run.task_file(os.path.join(run.BENCH, "tasks", "board"), "dnd", "prompt-comparison.md")) as f:
            text = f.read()
        self.assertIn("Firefox", text)
        prompt = run.task_prompt("codex", "cua", text, "chromium")
        self.assertNotIn("Firefox", prompt)
        self.assertIn("Her browser, Chromium, is already running.", prompt)
        self.assertEqual(run.task_prompt("codex", "hyprmasq", text), text)

    def test_every_task_and_variant_has_a_tool_neutral_comparison_prompt(self):
        parser = argparse.ArgumentParser()
        parser.error = lambda message: (_ for _ in ()).throw(AssertionError(message))
        tasks = sorted(t for t in os.listdir(os.path.join(run.BENCH, "tasks"))
                       if os.path.isdir(os.path.join(run.BENCH, "tasks", t)))
        for task in tasks:
            for variant in run.task_variants(task)[0] or [None]:
                run.check_task(task, variant, parser, "comparison")
                path = run.task_file(os.path.join(run.BENCH, "tasks", task), variant, run.PROMPTS["comparison"])
                with open(path) as f:
                    text = f.read()
                self.assertNotIn("hyprmasq", text, path)
                self.assertIn("only through your desktop tools", text, path)
                self.assertIn("She has authorized foreground input and focus changes on this desktop if your tools "
                              "need them, but prefer ways that leave her window alone.", text, path)


class ExportBackends(unittest.TestCase):
    def test_a_dataset_holds_one_backend_prompt_and_browser_of_each_task(self):
        with tempfile.TemporaryDirectory() as root:
            def results(name, **source):
                path = os.path.join(root, name)
                os.makedirs(path)
                with open(os.path.join(path, "source.json"), "w") as f:
                    json.dump({"commit": "c", "dirty": False, "task": "form", "variant": "25",
                               "display": "1920x1080@1", **source}, f)
                return path
            old, cua = results("old"), results("cua", backend="cua", prompt="comparison")
            chromium = results("chromium", backend="cua", prompt="comparison", browser="chromium")
            self.assertEqual([(s["backend"], s["prompt"], s["browser"]) for s in export.export([old], {})["sources"]],
                             [("hyprmasq", "task", "firefox")])
            omarchy = results("omarchy", desktop="omarchy")
            port = results("port", chromium_options="--remote-debugging-port=0")
            limit = results("limit", mcp_args="--max-image-size 1920")
            for other in (omarchy, port, limit):
                with self.assertRaisesRegex(SystemExit, "desktops or launch options of form"):
                    export.export([old, other], {})
            for other in (cua, chromium):
                with self.assertRaisesRegex(SystemExit, "desktops or launch options of form"):
                    export.export([old if other is cua else cua, other], {})


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
                                  ("models-store.json", '{"providers": {}}'),
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
                self.assertEqual(sorted(os.listdir(agent_dir)),
                                 ["auth.json", "mcp.json", "models-store.json", "models.json", "settings.json"])
                for name in ("auth.json", "models.json", "models-store.json"):  # linked: no copy of the keys
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
            self.assertEqual(sorted(os.listdir(user)), ["AGENTS.md", "auth.json", "extensions", "mcp.json",
                                                        "models-store.json", "models.json", "settings.json"])

    def test_a_model_that_pi_lists_only_with_a_suffix_is_refused(self):
        # pi --list-models matches loosely, as --model does: asked for gemini-4-argon, it may list only a longer ID.
        listing = ("provider     model                        context  images\n"
                   "opencode-go  gemini-4-argon-contributor   1.0M     yes\n"
                   "openrouter   google/gemini-4-argon        1.0M     yes\n")
        with tempfile.TemporaryDirectory() as bin_dir:
            executable(bin_dir, "pi", f"printf '%s' '{listing}'\n")
            with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ["PATH"]}):
                with self.assertRaisesRegex(RuntimeError, "no model opencode-go/gemini-4-argon"):
                    run.check_pi_model(bin_dir, "opencode-go/gemini-4-argon")
                run.check_pi_model(bin_dir, "opencode-go/gemini-4-argon-contributor")
                run.check_pi_model(bin_dir, "openrouter/google/gemini-4-argon")

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


class Budget(unittest.TestCase):
    def test_the_tap_forwards_the_budgets_tool_calls_and_answers_the_next_itself(self):
        call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "act"}}
        for calls in (1, 3):
            self.assertIsNone(mcp_tap.budget_reply(call, calls, 3))
        reply = mcp_tap.budget_reply(call, 4, 3)
        self.assertEqual((reply["id"], reply["result"]["isError"]), (7, True))
        self.assertIn("budget of 3 tool calls", reply["result"]["content"][0]["text"])
        # Other requests, such as the listing of tools, still reach the server, and 0 sets no budget.
        self.assertIsNone(mcp_tap.budget_reply({"id": 8, "method": "tools/list"}, 4, 3))
        self.assertIsNone(mcp_tap.budget_reply(call, 100, 0))

    def test_an_agent_is_stopped_once_the_tap_has_spent_the_budget(self):
        with tempfile.TemporaryDirectory() as out:
            marker = os.path.join(out, "mcp.jsonl.budget")
            agent = executable(out, "agent", f"sleep 1; touch {marker}; sleep 30; echo late\n")
            started = time.time()
            result = run.run_agent([agent], out, os.path.join(out, "a.jsonl"), os.path.join(out, "a.err"), 60,
                                   budget=marker)
            self.assertLess(time.time() - started, 10)
            self.assertEqual((result["budget_exhausted"], result["timed_out"]), (True, False))
            self.assertEqual(report.load_jsonl(os.path.join(out, "a.jsonl")), [])

    def test_an_agent_that_ends_right_after_the_budget_is_spent_counts_as_stopped(self):
        with tempfile.TemporaryDirectory() as out:
            marker = os.path.join(out, "mcp.jsonl.budget")
            # The watcher must not see the marker while the agent runs, or it stops the agent, with an exit of -15.
            # kill -0 succeeds until the watcher's poll has reaped the agent, which also ends the watcher's loop, and
            # the child keeps the agent's stdout open, so run_agent looks for the marker after the child has made it.
            agent = executable(out, "agent", f"(while kill -0 $$ 2>/dev/null; do sleep 0.05; done; touch {marker}) &\n")
            result = run.run_agent([agent], out, os.path.join(out, "a.jsonl"), os.path.join(out, "a.err"), 60,
                                   budget=marker)
            self.assertEqual((result["budget_exhausted"], result["exit"]), (True, 0))


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



# What Claude Code 2.1.319 did against a stand-in for the token endpoint: it refreshes only in the last minutes of a login
# (and not with 8 minutes left), writes the result by replacing the file in its own directory, which turns a link into a
# regular file, and logs out when the endpoint refuses the refresh token, which works once. Without a refresh token it
# never tries. FAKE_CLAUDE_TOKEN is the endpoint: the one refresh token it would accept.
FAKE_CLAUDE = """
import json, os, sys, time
path = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), ".credentials.json")
with open(path) as f:
    login = json.load(f)
oauth = login["claudeAiOauth"]
if oauth["expiresAt"] / 1000 - time.time() > 300 or not oauth.get("refreshToken"):
    sys.exit(0)
with open(os.environ["FAKE_CLAUDE_TOKEN"]) as f:
    valid = f.read()
if oauth["refreshToken"] == valid:
    oauth.update(refreshToken=valid + "+", expiresAt=int((time.time() + 28800) * 1000))
    with open(os.environ["FAKE_CLAUDE_TOKEN"], "w") as f:
        f.write(oauth["refreshToken"])
else:
    login = {}
with open(path + ".tmp", "w") as f:
    json.dump(login, f)
os.replace(path + ".tmp", path)
"""


class ClaudeLogin(unittest.TestCase):
    """The user's login, in a fake home, against a fake Claude Code that behaves as the real one does."""

    def setUp(self):
        self.user = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.user, ignore_errors=True)
        bin_dir = os.path.join(self.user, "bin")
        os.makedirs(bin_dir)
        os.makedirs(os.path.join(self.user, ".claude"))
        with open(os.path.join(self.user, "claude.py"), "w") as f:
            f.write(FAKE_CLAUDE)
        executable(bin_dir, "claude", f'exec {sys.executable} {self.user}/claude.py "$@"\n')
        self.path = os.path.join(self.user, ".claude", ".credentials.json")
        self.token = os.path.join(self.user, "token")
        patch = mock.patch.dict(os.environ, {"HOME": self.user, "PATH": bin_dir + os.pathsep + os.environ["PATH"],
                                             "FAKE_CLAUDE_TOKEN": self.token})
        patch.start()
        self.addCleanup(patch.stop)

    def login(self, ends_in, endpoint_accepts="r1"):
        with open(self.token, "w") as f:
            f.write(endpoint_accepts)
        with open(self.path, "w") as f:
            json.dump({"claudeAiOauth": {"accessToken": "a1", "refreshToken": "r1",
                                         "expiresAt": int((time.time() + ends_in) * 1000)}}, f)
        os.chmod(self.path, 0o600)

    def read(self):
        with open(self.path) as f:
            return json.load(f)

    def endpoint(self):
        with open(self.token) as f:
            return f.read()

    def test_several_workers_renew_a_login_once_and_its_expiry_moves_forward(self):
        # 20 minutes left: the harness itself would not refresh yet, and a run could outlive the login.
        self.login(1200)
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            list(pool.map(lambda _: run.renew_claude_login(), range(4)))
        oauth = self.read()["claudeAiOauth"]
        self.assertGreater(oauth["expiresAt"] / 1000 - time.time(), run.LOGIN_MARGIN_SECONDS)
        self.assertEqual(oauth["refreshToken"], "r1+")
        self.assertEqual(self.endpoint(), "r1+", "one refresh, as the token works once")
        self.assertFalse(os.path.islink(self.path))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), [".credentials.json", ".credentials.json.bench-lock"])

    def test_a_run_with_an_expired_login_cannot_spend_the_users_refresh_token(self):
        self.login(-60)
        before = self.read()
        home, env = run.agent_home("claude")
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        subprocess.run(["claude", "-p", "Do it."], env=run.agent_env(env), check=True)
        self.assertEqual(self.endpoint(), "r1", "the run spent the refresh token")
        self.assertEqual(self.read(), before)

    def test_a_renewal_the_endpoint_refuses_leaves_the_users_login_and_fails_the_run(self):
        self.login(1200, endpoint_accepts="another")
        before = self.read()
        with self.assertRaisesRegex(RuntimeError, "could not be renewed"):
            run.renew_claude_login()
        self.assertEqual(self.read(), before)

    def test_a_login_with_time_left_is_not_renewed(self):
        self.login(run.LOGIN_MARGIN_SECONDS + 600)
        before = self.read()
        run.renew_claude_login()
        self.assertEqual(self.read(), before)
        self.assertEqual(self.endpoint(), "r1")


class FailureReview(unittest.TestCase):
    """The rule of comparison-series.json for one verdict from two reviewers, and what a bundle may hold."""

    def verdict(self, what="tool_problem", file="mcp.jsonl", line=1, reason="none"):
        return {"verdict": what, "unclear_reason": reason, "evidence": [{"file": file, "line": line, "what": "x"}]}

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        with open(os.path.join(self.dir.name, "mcp.jsonl"), "w") as f:
            f.write("{}\n{}\n")

    def test_the_same_verdict_stands_and_any_disagreement_is_unclear(self):
        tool, model = ("tool_problem", None), ("model_mistake", None)
        self.assertEqual(review_failures.resolve(tool, tool), tool)
        self.assertEqual(review_failures.resolve(tool, model), ("unclear", "reviewers_disagree"))
        self.assertEqual(review_failures.resolve(tool, ("unclear", "both_contributed")), ("unclear", "reviewers_disagree"))

    def test_a_verdict_counts_only_with_evidence_that_is_in_the_bundle(self):
        own = lambda v: review_failures.own_verdict(self.dir.name, v)
        self.assertEqual(own(self.verdict()), ("tool_problem", None))
        for cited in (self.verdict(line=3), self.verdict(file="absent.jsonl"), self.verdict(file="../mcp.jsonl"),
                      dict(self.verdict(), evidence=[])):
            self.assertEqual(own(cited), ("unclear", "evidence_insufficient"), cited)

    def test_a_verdict_counts_only_for_the_evidence_it_was_made_from(self):
        bundle = os.path.join(self.dir.name, "run")
        os.makedirs(bundle)
        with open(os.path.join(bundle, "mcp.jsonl"), "w") as f:
            f.write("{}\n")
        with open(review_failures.verdict_path(bundle, "codex"), "w") as f:
            json.dump({"verdict": "model_mistake", "evidence_sha256": review_failures.evidence_hash(bundle)}, f)
        self.assertTrue(review_failures.current_verdict(bundle, "codex"))
        with open(os.path.join(bundle, "mcp.jsonl"), "a") as f:
            f.write("{}\n")
        self.assertIsNone(review_failures.current_verdict(bundle, "codex"))

    def test_a_rebuild_removes_the_bundles_of_runs_that_no_longer_failed(self):
        for name in ("kept", "passed now"):
            os.makedirs(os.path.join(self.dir.name, name))
            with open(os.path.join(self.dir.name, name, "meta.json"), "w") as f:
                f.write("{}")
            for reviewer in review_failures.REVIEWERS:
                with open(review_failures.verdict_path(os.path.join(self.dir.name, name), reviewer), "w") as f:
                    f.write("{}")
        self.assertEqual(review_failures.prune(self.dir.name, {"kept"}), 1)
        self.assertEqual([os.path.basename(b) for b in review_failures.bundles_of(self.dir.name)], ["kept"])
        self.assertFalse(any(n.startswith("passed now") for n in os.listdir(self.dir.name)))

    def test_codex_reviews_a_bundle_given_by_a_relative_path_in_place(self):
        bundle = os.path.join(self.dir.name, "run")
        os.makedirs(bundle)
        seen = {}

        def fake(command, **options):
            seen.update(command=command, cwd=options["cwd"])
            raise RuntimeError("stop")

        with mock.patch.object(review_failures.subprocess, "run", fake), contextlib.chdir(self.dir.name):
            with self.assertRaises(RuntimeError):
                review_failures.codex_review("run")
        self.assertEqual(seen["command"][seen["command"].index("-C") + 1], bundle)
        self.assertEqual(seen["cwd"], bundle)

    def test_a_bundle_holds_the_prompt_the_agent_got(self):
        with open(os.path.join(self.dir.name, "run.json"), "w") as f:
            json.dump({"task": "board", "variant": "pointer", "prompt": "comparison", "harness": "codex", "backend": "cua",
                       "browser": "chromium"}, f)
        text = review_failures.prompt_text(self.dir.name)
        self.assertTrue(text.startswith("$cua-driver "))
        self.assertIn("In Chromium", text)
        self.assertNotIn("Firefox", text)

    def test_a_bundle_holds_no_machine_home_address_or_image(self):
        with mock.patch.object(review_failures, "hosts", return_value=["boxname"]):
            scrub = review_failures.scrubber()
        cleaned = json.dumps(scrub({"a": "ssh boxname", "b": os.path.expanduser("~/x"), "c": "A" * 3000,
                                    "d": "The user's email address is me@example.org. Use it only."}))
        for private in ("boxname", os.path.expanduser("~"), "me@example.org", "A" * 3000):
            self.assertNotIn(private, cleaned)


class CapabilityRoutes(unittest.TestCase):
    """A scripted route counts only by what it changed, and on a real desktop never touches the user's windows."""

    def routes(self):
        tool = mock.Mock()
        tool.name, tool.mode = "cua", "background"
        return capability.Routes(tool, "form-gtk4", tempfile.mkdtemp())

    def test_a_route_whose_check_already_held_is_unmeasured(self):
        routes = self.routes()
        state = {"slider": 37}
        # An earlier route left the slider moved: a drag that does nothing would otherwise read as one that works.
        routes.attempt("drag slider", "pixels", lambda: capability.Outcome(True), lambda: state["slider"] != 0, settle=0)
        self.assertEqual(routes.results[-1]["result"], "unmeasured")
        state["slider"] = 0
        routes.attempt("drag slider", "pixels", lambda: (state.update(slider=50), capability.Outcome(True))[1],
                       lambda: state["slider"] != 0, settle=0)
        self.assertEqual(routes.results[-1]["result"], "works")

    def test_a_label_drag_counts_by_the_label_it_placed(self):
        """Every drag runs and is judged by what it placed, from a board where no label is placed yet."""
        placed = {}
        layout = {"type": "layout", "inner": [1000, 900], "photo": [0, 0, 500, 400],
                  "labels": {"A": [[600, 10, 700, 30]], "B": [[600, 40, 700, 60]], "C": [[600, 70, 700, 90]]}}

        def shell(command):
            if "events.jsonl" in command:
                return 0, json.dumps(layout)
            return 0, json.dumps({"labels": placed})

        vm = mock.Mock(paths={"guide": "/state"}, height=1000, shell=shell)
        calls = []

        def place(*_, **__):
            calls.append(1)
            placed[f"label {len(calls)}"] = [1, 1]
            return capability.Outcome(True)

        tool = mock.Mock()
        tool.name, tool.mode, tool.drag, tool.path = "cua", "background", place, place
        routes = capability.Routes(tool, "board-pointer-firefox", tempfile.mkdtemp())
        with mock.patch.object(capability.time, "sleep", lambda _: None):
            capability.board_case(routes, vm, tool)
        self.assertEqual(len(calls), 3)
        self.assertEqual([r["result"] for r in routes.results], ["works"] * 3)

    def test_a_browser_drag_run_alone_attaches_first_and_counts_its_own_label(self):
        placed = {"Memory slots": [1, 1]}
        layout = {"type": "layout", "inner": [1000, 900], "photo": [0, 0, 500, 400],
                  "labels": {"A": [[600, 10, 700, 30]], "B": [[600, 40, 700, 60]]}}
        vm = mock.Mock(paths={"guide": "/state"}, height=1000,
                       shell=lambda c: (0, json.dumps(layout) if "events.jsonl" in c else json.dumps({"labels": placed})))
        tool = mock.Mock(pid=1, window_id=2, target={})
        tool.name, tool.mode = "cua", "browser"
        tool.call.return_value = (capability.Outcome(True), {})
        drags = []

        def drag(*_):
            drags.append(1)
            placed[f"label {len(drags)}"] = [1, 1]
            return capability.Outcome(True)

        routes = capability.Routes(tool, "attach-debugging-board-pointer", tempfile.mkdtemp(),
                                   only=("drag a label", "browser_pointer, background"))
        with mock.patch.object(capability.Browser, "bind", lambda *_: (capability.Outcome(True), True)), \
                mock.patch.object(capability.Browser, "drag", lambda self, *a: drag()), \
                mock.patch.object(capability.time, "sleep", lambda _: None):
            capability.attached_board(routes, vm, tool)
        # A label placed before the drag began does not count for it.
        self.assertEqual([(r["route"], r["result"]) for r in routes.results], [("browser_pointer, background", "works")])
        self.assertEqual(len(drags), 1)

    def test_the_desktop_curve_goes_to_the_desktop(self):
        layout = {"type": "layout", "inner": [1000, 900], "photo": [0, 0, 500, 400]}
        vm = mock.Mock(paths={"blog": "/state"}, height=1000,
                       shell=lambda c: (0, json.dumps(layout) if "events.jsonl" in c else json.dumps({"strokes": []})))
        sent = []
        tool = mock.Mock(target={"pid": 1, "window_id": 2}, delivery={})
        tool.name, tool.mode = "cua", "desktop"
        tool.px = lambda p: p
        tool.drag = tool.path = lambda *a, **k: capability.Outcome(True)
        tool.call = lambda name, args: (sent.append((name, args)), (capability.Outcome(True), {}))[1]
        routes = capability.Routes(tool, "trace-river-firefox", tempfile.mkdtemp())
        with mock.patch.object(capability.time, "sleep", lambda _: None):
            capability.trace_case(routes, vm, tool)
        buttons = [args for name, args in sent if name.startswith("mouse_")]
        self.assertEqual(len(buttons), 7)
        self.assertTrue(all(args.get("target") == capability.DESKTOP and "pid" not in args for args in buttons))

    def test_a_fixtures_window_is_never_one_the_user_had_open(self):
        host = capability_host.Host.__new__(capability_host.Host)
        clients = [{"address": "0x1", "class": "org.gnome.Nautilus"}]
        host.clients = lambda: clients
        clients_later = [{"address": "0x1", "class": "org.gnome.Nautilus"}, {"address": "0x2", "class": "org.gnome.Nautilus"}]
        with mock.patch.object(capability_host.time, "sleep", lambda _: clients.__setitem__(slice(None), clients_later)):
            found = host.wait_window(lambda c: c["class"] == "org.gnome.Nautilus", timeout=5, exclude={"0x1"})
        self.assertEqual(found["address"], "0x2")


class LoginMargin(unittest.TestCase):
    def test_the_login_lasts_the_whole_run(self):
        self.assertEqual(run.login_margin(1800), 3600)
        self.assertEqual(run.login_margin(7200), 7200 + run.RUN_ALLOWANCE_SECONDS)


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
