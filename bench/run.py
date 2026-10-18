#!/usr/bin/env python3
"""Runs the Thunderbird benchmark: one fresh VM per run, an agent on the host, grading, and a debrief.

    run.py [--agent HARNESS:MODEL]... [--reps N | --rep N...] [--out DIR] [--timeout SECONDS] [--cid CID] [--no-debrief]

HARNESS is claude (Claude Code) or codex (Codex CLI), which use the logins of the user running this; claude-api
(Claude Code billed to the user's Anthropic API key); or pi, with MODEL as PROVIDER/MODEL of the user's pi setup.
Each run gets RESULTS/<harness>-<model>-<rep>/, with any / in the model as _, holding the agent's event stream, the
MCP log, screenshots, the VM's logs, the grade, the debrief and summary.json. RESULTS/report.md compares the runs.
See bench/README.md.
"""

import argparse
import contextlib
import datetime
import fcntl
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

import report

BENCH = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AGENTS = ["codex:gpt-6.1-sol", "codex:gpt-6-astra", "claude:claude-opus-5-5", "claude:claude-sonnet-5-5"]

HARNESSES = ("claude", "claude-api", "codex", "pi")
# Harnesses that use API keys of this machine: dispatch.py never sends their runs to another one.
LOCAL_ONLY = ("claude-api", "pi")
# Where claude-api's key and request headers come from when the environment does not give them.
API_KEY_ENV, API_HEADERS_ENV = "HYPRMASQ_BENCH_ANTHROPIC_API_KEY", "HYPRMASQ_BENCH_ANTHROPIC_HEADERS"

# Codex features that would give the agent tools besides hyprmasq's, or load the user's own setup.
CODEX_DISABLED = [
    "shell_tool", "unified_exec", "hooks", "computer_use", "browser_use", "browser_use_external", "in_app_browser",
    "apps", "plugins", "multi_agent", "image_generation", "memories", "goals", "tool_suggest", "skill_search",
    "workspace_dependencies",
]


def build_driver(out, source, task, variant, display):
    link = os.path.join(out, "driver")
    command = ["nix-build", os.path.join(BENCH, "default.nix"), "-A", "driver", "-o", link, "--argstr", "task", task,
               "--argstr", "variant", variant or "", "--argstr", "display", display]
    if source:
        command += ["--arg", "source", source]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    return link


def task_variants(task):
    """The variants a task has, from tasks/TASK/variants, one a line; none for most tasks."""
    path = os.path.join(BENCH, "tasks", task, "variants")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.strip() for line in f if line.strip()]


def source_commit(source):
    """The commit of the hyprmasq checkout the VM runs, and whether it had changes."""
    checkout = source or os.path.dirname(BENCH)
    run = lambda *args: subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True).stdout.strip()
    return {"path": checkout, "commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def executable(harness):
    return "claude" if harness == "claude-api" else harness


def family(harness):
    """The harness whose event stream and transcripts a harness has: claude-api's are Claude Code's."""
    return "claude" if harness == "claude-api" else harness


def run_name(harness, model, rep):
    """The run's directory: a pi model such as tensorx/deepseek/... has slashes, which a name cannot."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{harness}-{model}-{rep}")


def version(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout.strip()
    except OSError:
        return None


def machine():
    """The machine a run ran on: wall times and hyprmasq's own time depend on it."""
    cpu = None
    with contextlib.suppress(OSError), open("/proc/cpuinfo") as f:
        cpu = next((line.split(":", 1)[1].strip() for line in f if line.startswith("model name")), None)
    memory = None
    with contextlib.suppress(OSError, ValueError), open("/proc/meminfo") as f:
        memory = round(int(f.readline().split()[1]) / 1024 ** 2)
    return {"host": socket.gethostname(), "cpu": cpu, "threads": os.cpu_count(), "memory_gib": memory}


def tap_command(cid, log):
    return [sys.executable, os.path.join(BENCH, "mcp_tap.py"), str(cid), "5000", log]


def claude_command(model, prompt, tap, resume=None):
    command = [
        "claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
        # Only hyprmasq's tools, and none of the user's CLAUDE.md, settings, hooks, skills or MCP servers.
        "--tools", "", "--setting-sources", "", "--disable-slash-commands", "--strict-mcp-config",
    ]
    if resume:
        command += ["--resume", resume]
    else:
        config = {"mcpServers": {"hyprmasq": {"command": tap[0], "args": tap[1:]}}}
        command += ["--mcp-config", json.dumps(config), "--allowedTools", "mcp__hyprmasq"]
    return command


def codex_command(model, prompt, tap, resume=None):
    command = ["codex", "exec"]
    if resume:
        command += ["resume", resume]
    command += [
        "--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-m", model,
        "-c", 'web_search="disabled"',
        # The user's skills would be listed to the model.
        "-c", "skills.include_instructions=false",
    ]
    if not resume:
        command += ["--sandbox", "read-only"]
    for feature in CODEX_DISABLED:
        command += ["--disable", feature]
    if not resume:
        command += [
            "-c", f"mcp_servers.hyprmasq.command={json.dumps(tap[0])}",
            "-c", f"mcp_servers.hyprmasq.args={json.dumps(tap[1:])}",
            "-c", "mcp_servers.hyprmasq.tool_timeout_sec=600",
            # In the read-only sandbox, Codex asks to approve every tool without readOnlyHint, and exec cannot ask.
            "-c", 'mcp_servers.hyprmasq.default_tools_approval_mode="approve"',
        ]
    return command + [prompt]


def pi_command(model, prompt, tap, resume=None, sessions=None):
    """pi with only hyprmasq's tools. Its agent directory, PI_CODING_AGENT_DIR, is the run's own (pi_agent_dir),
    so that none of the user's settings, MCP servers, extensions, skills or instructions load."""
    command = [
        "pi", "--mode", "json", "-p", "--model", model, "--offline", "--no-approve",
        # The built-in MCP extension alone: --no-extensions drops it with the others, and -e loads it again.
        "--no-extensions", "--no-skills", "--no-context-files", "--no-prompt-templates", "--no-themes",
    ]
    if resume:
        # The debrief: the session's file, no tools, and no MCP server to give any.
        command += ["--session", resume, "--no-tools"]
    else:
        command += ["-e", "builtin:mcp", "--tools", "mcp__hyprmasq__*", "--session-dir", sessions]
    if prompt.startswith("@"):
        raise ValueError("pi would take a prompt starting with @ for a file")
    return command + ["--", prompt]


def pi_agent_dir(tap, sessions):
    """A pi agent directory for one run: the user's credentials and models, linked and not copied, and the tap as
    the only MCP server, its tools declared to the model directly."""
    agent_dir = tempfile.mkdtemp(prefix="hyprmasq-bench-pi-")
    user = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")
    for name in ("auth.json", "models.json"):
        if os.path.exists(os.path.join(user, name)):
            os.symlink(os.path.join(user, name), os.path.join(agent_dir, name))
    config = {
        "autoEnableCodemode": False,
        "mcpServers": {"hyprmasq": {"command": tap[0], "args": tap[1:], "exposure": "direct", "timeout": 600}},
    }
    with open(os.path.join(agent_dir, "mcp.json"), "w") as f:
        json.dump(config, f, indent=2)
    with open(os.path.join(agent_dir, "settings.json"), "w") as f:
        json.dump({"defaultProjectTrust": "never"}, f)
    os.makedirs(sessions)
    return agent_dir


def agent_command(harness, model, prompt, tap, resume=None, sessions=None):
    if harness == "pi":
        return pi_command(model, prompt, tap, resume, sessions)
    if family(harness) == "claude":
        return claude_command(model, prompt, tap, resume)
    return codex_command(model, prompt, tap, resume)


def agent_env(extra=None):
    # Nothing of a surrounding agent session: its effort, session ids and sockets would change the run. No API key
    # either, but the one a claude-api run gives in `extra`: the others use the user's logins.
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("CLAUDE", "CODEX", "T3CODE", "AUTOREVIEW", "ANTHROPIC_", "OPENAI_", "PI_",
                             "HYPRMASQ_BENCH_ANTHROPIC_"))
        and k != "AI_AGENT"
    }
    env.update({"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    env.update(extra or {})
    return env


def anthropic_api_env():
    """The variables that bill Claude Code to an Anthropic API key, for its process alone: the key from
    $HYPRMASQ_BENCH_ANTHROPIC_API_KEY or else from pi (`pi auth print-api-key --provider anthropic`), and the request
    headers, such as a workspace id, from $HYPRMASQ_BENCH_ANTHROPIC_HEADERS (`Name: value` lines) or else from pi's
    models.json. The key is never written anywhere."""
    key = os.environ.get(API_KEY_ENV)
    if not key:
        try:
            got = subprocess.run(["pi", "auth", "print-api-key", "--provider", "anthropic"], capture_output=True,
                                 text=True, timeout=60, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(f"no Anthropic API key: set {API_KEY_ENV}, or give pi one ({e.__class__.__name__})")
        key = got.stdout.strip() if got.returncode == 0 else ""
        if not key or len(key.split()) != 1:
            raise RuntimeError(f"no Anthropic API key: set {API_KEY_ENV}, or give pi one (pi auth exited {got.returncode})")
    headers = os.environ.get(API_HEADERS_ENV)
    if headers is None:
        user = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")
        models = {}
        with contextlib.suppress(OSError, ValueError), open(os.path.join(user, "models.json")) as f:
            models = json.load(f)
        given = ((models.get("providers") or {}).get("anthropic") or {}).get("headers") or {}
        if any(str(v).startswith("!") or "${" in str(v) for v in given.values()):
            raise RuntimeError(f"pi's anthropic headers run commands or read variables: set {API_HEADERS_ENV} instead")
        headers = "\n".join(f"{k}: {v}" for k, v in given.items())
    env = {"ANTHROPIC_API_KEY": key}
    if headers:
        env["ANTHROPIC_CUSTOM_HEADERS"] = headers
    return env


def check_api_auth(env):
    """Fails unless Claude Code, given env, uses the API key and not the user's subscription."""
    try:
        got = subprocess.run(["claude", "auth", "status", "--json"], capture_output=True, text=True, timeout=60,
                             env=agent_env(env), stdin=subprocess.DEVNULL)
        status = json.loads(got.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        raise RuntimeError(f"claude auth status failed: {e.__class__.__name__}")
    if status.get("authMethod") != "api_key" or status.get("apiKeySource") != "ANTHROPIC_API_KEY":
        raise RuntimeError(f"Claude Code would not use the API key: auth method {status.get('authMethod')}, "
                           f"key source {status.get('apiKeySource')}")
    return {"auth_method": status["authMethod"], "api_key_source": status["apiKeySource"]}


def session_of(line):
    """The session id in a line of the agent's events: Claude Code's, Codex's thread, or pi's session header."""
    return line.get("session_id") or line.get("thread_id") or (line.get("id") if line.get("type") == "session" else None)


def is_init(line):
    return line.get("type") == "system" and line.get("subtype") == "init"


def without_api_keys(environ):
    """An environment without Anthropic credentials, for the processes that need none: the VM driver, and ssh."""
    return {k: v for k, v in environ.items() if not k.startswith(("ANTHROPIC_", "HYPRMASQ_BENCH_ANTHROPIC_"))}


def run_agent(command, cwd, stream_path, stderr_path, timeout, env=None, api_key=False):
    """Runs the agent, logging each line of its stdout with the host time it arrived. With api_key, it stops the
    agent at Claude Code's init event unless that names the API key as its credentials."""
    start = time.time()
    with open(stream_path, "a") as stream, open(stderr_path, "a") as stderr:
        proc = subprocess.Popen(
            command, cwd=cwd, env=agent_env(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
            text=True, start_new_session=True,
        )
        timer = threading.Timer(timeout, lambda: os.killpg(proc.pid, signal.SIGTERM))
        timer.start()
        session, source, refused = None, None, False
        try:
            for raw in proc.stdout:
                now = time.time()
                try:
                    line = json.loads(raw)
                except ValueError:
                    line = {"raw": raw.rstrip("\n")}
                session = session or session_of(line)
                if is_init(line):
                    # Where Claude Code says its credentials come from: ANTHROPIC_API_KEY, or none for a login.
                    source = source or line.get("apiKeySource") or "(not given)"
                    if api_key and not refused and line.get("apiKeySource") != "ANTHROPIC_API_KEY":
                        refused = True
                        os.killpg(proc.pid, signal.SIGKILL)
                stream.write(json.dumps({"t": now, "line": line}) + "\n")
                stream.flush()
            proc.wait()
        except BaseException:  # interrupted, as by dispatch.py: the agent does not go on without its VM
            timer.cancel()
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGTERM)
            raise
        timed_out = not timer.is_alive() and proc.returncode != 0
        timer.cancel()
    return {"start": start, "end": time.time(), "exit": proc.returncode, "timed_out": timed_out, "session": session,
            "api_key_source": source, "refused": refused}


def final_text(harness, stream_path):
    return report.agent_usage(harness, report.load_jsonl(stream_path)).get("final")


def move_transcript(harness, session, cwd, run_dir, sessions=None):
    """Moves the harness's own transcript of the session into the run's directory."""
    if not session:
        return
    project = None
    if harness == "pi":
        paths = glob.glob(os.path.join(sessions, f"*_{session}.jsonl"))
    elif family(harness) == "claude":
        project = os.path.expanduser(f"~/.claude/projects/{re.sub(r'[^A-Za-z0-9]', '-', cwd)}")
        # The transcript, and a directory of the same name with the session's tool results.
        paths = glob.glob(os.path.join(project, f"{session}*"))
    else:
        paths = glob.glob(os.path.expanduser(f"~/.codex/sessions/*/*/*/rollout-*{session}.jsonl"))
    for path in paths:
        shutil.move(path, os.path.join(run_dir, f"transcript-{os.path.basename(path)}"))
    if project and os.path.isdir(project) and not os.listdir(project):
        os.rmdir(project)


def stop_driver(proc, run_dir, timeout=300):
    """Waits for the driver to grade and stop the VM; kills the VM if it does not."""
    deadline = time.time() + timeout
    while proc.poll() is None and time.time() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    shutil.rmtree(os.path.join(run_dir, "tmp"), ignore_errors=True)


def pi_session(sessions, session):
    """The session file of a pi session, and the thinking level it ran at: pi clamps its default to the model's."""
    path = next(iter(glob.glob(os.path.join(sessions, f"*_{session}.jsonl"))), None) if session else None
    levels = [e.get("thinkingLevel") for e in report.load_jsonl(path) if e.get("type") == "thinking_level_change"] if path else []
    return path, (levels[0] if levels else None)


def check_api_key_used(result, what):
    """Fails a claude-api run whose agent or debrief run_agent stopped for naming other credentials than the key."""
    if result["refused"]:
        raise RuntimeError(f"Claude Code's {what} did not use the API key but {result['api_key_source']}: stopped at "
                           "its start; the run has no summary.json")


def files_holding(run_dir, secret):
    """The files under run_dir that hold secret."""
    needle = secret.encode()
    found = []
    for root, _, names in os.walk(run_dir):
        for name in names:
            path = os.path.join(root, name)
            with contextlib.suppress(OSError):
                if not os.path.islink(path):
                    with open(path, "rb") as f:
                        if needle in f.read():
                            found.append(os.path.relpath(path, run_dir))
    return found


def run_one(driver, task, variant, harness, model, rep, out, cid, timeout, debrief):
    task_dir = os.path.join(BENCH, "tasks", task)
    run_dir = os.path.join(out, run_name(harness, model, rep))
    label = f"[{harness} {model} #{rep}]"
    # claude-api: the key and the check that Claude Code bills it, before the VM boots, so that a run never falls
    # back to the subscription.
    api_env = anthropic_api_env() if harness == "claude-api" else None
    auth = check_api_auth(api_env) if api_env else None
    os.makedirs(os.path.join(run_dir, "tmp"))
    os.makedirs(os.path.join(run_dir, "driver-out"))
    # A fresh directory outside any project: harnesses load instructions such as AGENTS.md from the directory
    # they run in and its parents, and a run directory inside this checkout would give the agent hyprmasq's.
    cwd = tempfile.mkdtemp(prefix="hyprmasq-bench-agent-")
    tap = tap_command(cid, os.path.join(run_dir, "mcp.jsonl"))
    agent_dir = sessions = None
    if harness == "pi":
        sessions = os.path.join(tempfile.mkdtemp(prefix="hyprmasq-bench-pi-sessions-"), "sessions")
        agent_dir = pi_agent_dir(tap, sessions)
        api_env = {"PI_CODING_AGENT_DIR": agent_dir}
    agent, leaked = {"session": None}, []
    try:
        env = dict(without_api_keys(os.environ), HYPRMASQ_BENCH_OUT=run_dir, HYPRMASQ_BENCH_CID=str(cid),
                   TMPDIR=os.path.join(run_dir, "tmp"), HYPRMASQ_BENCH_TIMEOUT=str(timeout + 120))
        log = open(os.path.join(run_dir, "driver.log"), "w")
        print(f"{label} booting the VM", flush=True)
        driver_proc = subprocess.Popen(
            [os.path.join(driver, "bin", "nixos-test-driver"), "-o", os.path.join(run_dir, "driver-out")],
            cwd=run_dir, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        try:
            deadline = time.time() + 900
            while not os.path.exists(os.path.join(run_dir, "ready.json")):
                if driver_proc.poll() is not None or time.time() > deadline:
                    raise RuntimeError(f"the VM did not get ready, see {run_dir}/driver.log")
                time.sleep(1)

            prompt = open(os.path.join(task_dir, "prompt.md")).read()
            command = agent_command(harness, model, prompt, tap, sessions=sessions)
            print(f"{label} agent running", flush=True)
            agent = run_agent(command, cwd, os.path.join(run_dir, "agent.jsonl"),
                              os.path.join(run_dir, "agent.stderr"), timeout, api_env, api_key=harness == "claude-api")
        finally:
            open(os.path.join(run_dir, "agent-done"), "w").close()
            stop_driver(driver_proc, run_dir)

        run = {
            "task": task, "variant": variant, "harness": harness, "model": model, "rep": rep, "cid": cid,
            "agent_start": agent["start"], "agent_end": agent["end"], "agent_exit": agent["exit"],
            "timed_out": agent["timed_out"], "session": agent["session"],
            "harness_version": version([executable(harness), "--version"]), "machine": machine(),
        }
        if family(harness) == "claude":
            # Where Claude Code's init event says its credentials came from: ANTHROPIC_API_KEY, or none for a login.
            run["api_key_source"] = agent["api_key_source"]
        if auth:
            run["auth"] = auth
        session_file = None
        if harness == "pi":
            session_file, run["thinking"] = pi_session(sessions, agent["session"])
        with open(os.path.join(run_dir, "run.json"), "w") as f:
            json.dump(run, f, indent=2)
        check_api_key_used(agent, "agent")
        summary = report.metrics(run_dir)

        resume = session_file if harness == "pi" else agent["session"]
        if debrief and resume:
            calls = report.tool_calls(report.load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
            text = open(os.path.join(task_dir, "debrief.md")).read().replace(
                "{report}", report.debrief_report(summary, calls))
            with open(os.path.join(run_dir, "debrief-prompt.md"), "w") as f:
                f.write(text)
            print(f"{label} debrief", flush=True)
            command = agent_command(harness, model, text, None, resume=resume)
            told = run_agent(command, cwd, os.path.join(run_dir, "debrief.jsonl"),
                             os.path.join(run_dir, "debrief.stderr"), 900, api_env, api_key=harness == "claude-api")
            answer = None if told["refused"] else final_text(harness, os.path.join(run_dir, "debrief.jsonl"))
            with open(os.path.join(run_dir, "debrief.md"), "w") as f:
                f.write(answer or "(no answer)\n")
            if family(harness) == "claude":
                run["debrief_api_key_source"] = told["api_key_source"]
                with open(os.path.join(run_dir, "run.json"), "w") as f:
                    json.dump(run, f, indent=2)
            check_api_key_used(told, "debrief")
    finally:
        move_transcript(harness, agent["session"], cwd, run_dir, sessions)
        shutil.rmtree(cwd, ignore_errors=True)
        if agent_dir:
            if os.path.exists(os.path.join(agent_dir, "mcp.log")):
                shutil.move(os.path.join(agent_dir, "mcp.log"), os.path.join(run_dir, "pi-mcp.log"))
            # Links to the user's files, which rmtree removes and does not follow.
            shutil.rmtree(agent_dir, ignore_errors=True)
            shutil.rmtree(os.path.dirname(sessions), ignore_errors=True)
        if harness == "claude-api":
            leaked = files_holding(run_dir, api_env["ANTHROPIC_API_KEY"])
            if leaked:
                print(f"{label} error: the API key is in {', '.join(leaked)} of {run_dir}: remove them", flush=True)
    if leaked:
        raise RuntimeError("the API key is in the run's files; the run has no summary.json")

    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(
        f"{label} {'passed' if summary['passed'] else 'FAILED'} in {summary['wall_seconds']} s, "
        f"{summary['calls']} calls, {summary['tool_seconds']} s in tools"
        + (f": {'; '.join(summary['failures'])}" if summary["failures"] else ""),
        flush=True,
    )


@contextlib.contextmanager
def vm_lock(path):
    """Holds the lock file while a run's VM is up, so that other VMs on the machine, such as VM tests that
    take the same lock, wait instead of running out of memory."""
    if not path:
        yield
        return
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def check_resume(out, config, parser):
    """Refuse to add runs to a results directory whose finished runs had another commit, task, variant or display,
    so that one report never mixes them. Keys that an older source.json lacks are not compared."""
    path = os.path.join(out, "source.json")
    finished = any(os.path.exists(os.path.join(out, name, "summary.json")) for name in os.listdir(out))
    if not finished or not os.path.exists(path):
        return
    with open(path) as f:
        before = json.load(f)
    differ = [k for k in ("commit", "task", "variant", "display") if k in before and before[k] != config[k]]
    if differ:
        parser.error(f"{out} has runs with another {', '.join(differ)}: "
                     + ", ".join(f"{k} {before[k]} against {config[k]}" for k in differ) + "; use another --out")


def pending_runs(out, reps, agents):
    """The runs of the given repetitions that have no summary.json yet, in the order they run: repetitions go round
    the agents, so that a slow hour of an API does not hit one agent's runs only."""
    return [
        (rep, harness, model) for rep in reps for harness, model in agents
        if not os.path.exists(os.path.join(out, run_name(harness, model, rep), "summary.json"))
    ]


def add_run_options(parser):
    """The options that say which runs to make, shared with dispatch.py."""
    parser.add_argument("--task", default="thunderbird", help="a directory under bench/tasks")
    parser.add_argument("--variant", help="the task's variant, for a task that has them (bench/tasks/TASK/variants)")
    parser.add_argument("--display", default="1920x1080@1", help="the VM's monitor, WIDTHxHEIGHT@SCALE")
    parser.add_argument("--agent", action="append", help="HARNESS:MODEL, repeatable")
    reps = parser.add_mutually_exclusive_group()
    reps.add_argument("--reps", type=int, default=3)
    reps.add_argument("--rep", type=int, action="append", help="run only this repetition, repeatable; instead of --reps")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds an agent may take")
    parser.add_argument("--no-debrief", action="store_true")


def check_run_options(args, parser):
    """The agents and the repetitions of the runs, after checking the task and its variant."""
    # Once each: a run of the same name twice would share its directory.
    agents = [tuple(a.split(":", 1)) for a in dict.fromkeys(args.agent or DEFAULT_AGENTS)]
    for agent in agents:
        if len(agent) != 2 or not agent[1]:
            parser.error(f"{':'.join(agent)} is not HARNESS:MODEL")
        if agent[0] not in HARNESSES:
            parser.error(f"unknown harness {agent[0]}: one of {', '.join(HARNESSES)}")
    names = [run_name(harness, model, 1) for harness, model in agents]
    if len(set(names)) < len(names):
        parser.error("two agents have the same run directory: their names differ only in characters other than "
                     "letters, digits, ., _ and -")
    if any(rep < 1 for rep in args.rep or []):
        parser.error("--rep counts from 1")
    if not os.path.isdir(os.path.join(BENCH, "tasks", args.task)):
        parser.error(f"no task {args.task} in {os.path.join(BENCH, 'tasks')}")
    variants = task_variants(args.task)
    if variants and args.variant not in variants:
        parser.error(f"task {args.task} needs --variant, one of {', '.join(variants)}")
    if not variants and args.variant:
        parser.error(f"task {args.task} has no variants")
    reps = sorted(set(args.rep)) if args.rep else list(range(1, args.reps + 1))
    return agents, reps


def default_out(task, variant, suffix=""):
    name = task + (f"-{variant}" if variant else "")
    return os.path.join(BENCH, "results", f"{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{name}{suffix}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_run_options(parser)
    parser.add_argument("--out")
    parser.add_argument("--cid", type=int, default=4242)
    parser.add_argument("--source", help="hyprmasq checkout to run in the VM, by default this one")
    parser.add_argument("--lock", help="lock file to hold during each run, shared with other VM users")
    args = parser.parse_args()
    agents, reps = check_run_options(args, parser)
    out = os.path.abspath(args.out or default_out(args.task, args.variant))
    os.makedirs(out, exist_ok=True)
    source = os.path.abspath(args.source) if args.source else None
    config = {**source_commit(source), "task": args.task, "variant": args.variant, "display": args.display}
    check_resume(out, config, parser)
    driver = build_driver(out, source, args.task, args.variant, args.display)
    with open(os.path.join(out, "source.json"), "w") as f:
        json.dump(config, f, indent=2)
    for rep, harness, model in pending_runs(out, reps, agents):
        shutil.rmtree(os.path.join(out, run_name(harness, model, rep)), ignore_errors=True)
        try:
            with vm_lock(args.lock):
                run_one(driver, args.task, args.variant, harness, model, rep, out, args.cid, args.timeout, not args.no_debrief)
        except Exception as e:  # one broken run does not end the benchmark
            print(f"[{harness} {model} #{rep}] error: {e}", flush=True)
    print(report.report(out))


if __name__ == "__main__":
    main()
