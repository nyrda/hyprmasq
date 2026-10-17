#!/usr/bin/env python3
"""Runs the Thunderbird benchmark: one fresh VM per run, an agent on the host, grading, and a debrief.

    run.py [--agent HARNESS:MODEL]... [--reps N] [--out DIR] [--timeout SECONDS] [--cid CID] [--no-debrief]

HARNESS is claude (Claude Code) or codex (Codex CLI); both use the logins of the user running this. Each run
gets RESULTS/<harness>-<model>-<rep>/ with the agent's event stream, the MCP log, screenshots, the VM's logs,
the grade, the debrief and summary.json. RESULTS/report.md compares the runs. See bench/README.md.
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
import subprocess
import sys
import tempfile
import threading
import time

import report

BENCH = os.path.dirname(os.path.abspath(__file__))
DEFAULT_AGENTS = ["codex:gpt-6.1-sol", "codex:gpt-6-astra", "claude:claude-opus-5-5", "claude:claude-sonnet-5-5"]

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


def version(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30).stdout.strip()
    except OSError:
        return None


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


def agent_env():
    # Nothing of a surrounding agent session: its effort, session ids and sockets would change the run.
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("CLAUDE", "CODEX", "T3CODE", "AUTOREVIEW", "ANTHROPIC_", "OPENAI_"))
    }
    env.update({"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"})
    return env


def run_agent(command, cwd, stream_path, stderr_path, timeout):
    """Runs the agent, logging each line of its stdout with the host time it arrived."""
    start = time.time()
    with open(stream_path, "a") as stream, open(stderr_path, "a") as stderr:
        proc = subprocess.Popen(
            command, cwd=cwd, env=agent_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
            text=True, start_new_session=True,
        )
        timer = threading.Timer(timeout, lambda: os.killpg(proc.pid, signal.SIGTERM))
        timer.start()
        session = None
        for raw in proc.stdout:
            now = time.time()
            try:
                line = json.loads(raw)
            except ValueError:
                line = {"raw": raw.rstrip("\n")}
            session = session or line.get("session_id") or line.get("thread_id")
            stream.write(json.dumps({"t": now, "line": line}) + "\n")
            stream.flush()
        proc.wait()
        timed_out = not timer.is_alive() and proc.returncode != 0
        timer.cancel()
    return {"start": start, "end": time.time(), "exit": proc.returncode, "timed_out": timed_out, "session": session}


def final_text(harness, stream_path):
    text = None
    for entry in report.load_jsonl(stream_path):
        line = entry["line"]
        if harness == "claude" and line.get("type") == "result":
            text = line.get("result")
        item = line.get("item") or {}
        if harness == "codex" and line.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
    return text


def move_transcript(harness, session, cwd, run_dir):
    """Moves the harness's own transcript of the session into the run's directory."""
    if not session:
        return
    project = None
    if harness == "claude":
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


def run_one(driver, task, variant, harness, model, rep, out, cid, timeout, debrief):
    task_dir = os.path.join(BENCH, "tasks", task)
    run_dir = os.path.join(out, f"{harness}-{model}-{rep}")
    os.makedirs(os.path.join(run_dir, "tmp"))
    os.makedirs(os.path.join(run_dir, "driver-out"))
    # A fresh directory outside any project: harnesses load instructions such as AGENTS.md from the directory
    # they run in and its parents, and a run directory inside this checkout would give the agent hyprmasq's.
    cwd = tempfile.mkdtemp(prefix="hyprmasq-bench-agent-")
    env = dict(os.environ, HYPRMASQ_BENCH_OUT=run_dir, HYPRMASQ_BENCH_CID=str(cid), TMPDIR=os.path.join(run_dir, "tmp"),
               HYPRMASQ_BENCH_TIMEOUT=str(timeout + 120))
    log = open(os.path.join(run_dir, "driver.log"), "w")
    print(f"[{harness} {model} #{rep}] booting the VM", flush=True)
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
        tap = tap_command(cid, os.path.join(run_dir, "mcp.jsonl"))
        command = (claude_command if harness == "claude" else codex_command)(model, prompt, tap)
        print(f"[{harness} {model} #{rep}] agent running", flush=True)
        agent = run_agent(command, cwd, os.path.join(run_dir, "agent.jsonl"), os.path.join(run_dir, "agent.stderr"), timeout)
    finally:
        open(os.path.join(run_dir, "agent-done"), "w").close()
        stop_driver(driver_proc, run_dir)

    run = {
        "task": task, "variant": variant, "harness": harness, "model": model, "rep": rep, "cid": cid,
        "agent_start": agent["start"], "agent_end": agent["end"], "agent_exit": agent["exit"],
        "timed_out": agent["timed_out"], "session": agent["session"],
        "harness_version": version([harness, "--version"]),
    }
    with open(os.path.join(run_dir, "run.json"), "w") as f:
        json.dump(run, f, indent=2)
    summary = report.metrics(run_dir)

    if debrief and agent["session"]:
        calls = report.tool_calls(report.load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
        text = open(os.path.join(task_dir, "debrief.md")).read().replace("{report}", report.debrief_report(summary, calls))
        with open(os.path.join(run_dir, "debrief-prompt.md"), "w") as f:
            f.write(text)
        print(f"[{harness} {model} #{rep}] debrief", flush=True)
        command = (claude_command if harness == "claude" else codex_command)(model, text, None, resume=agent["session"])
        run_agent(command, cwd, os.path.join(run_dir, "debrief.jsonl"), os.path.join(run_dir, "debrief.stderr"), 900)
        answer = final_text(harness, os.path.join(run_dir, "debrief.jsonl"))
        with open(os.path.join(run_dir, "debrief.md"), "w") as f:
            f.write(answer or "(no answer)\n")
    move_transcript(harness, agent["session"], cwd, run_dir)
    shutil.rmtree(cwd, ignore_errors=True)

    with open(os.path.join(run_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(
        f"[{harness} {model} #{rep}] {'passed' if summary['passed'] else 'FAILED'} in {summary['wall_seconds']} s, "
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", default="thunderbird", help="a directory under bench/tasks")
    parser.add_argument("--agent", action="append", help="HARNESS:MODEL, repeatable")
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--out")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds an agent may take")
    parser.add_argument("--cid", type=int, default=4242)
    parser.add_argument("--no-debrief", action="store_true")
    parser.add_argument("--source", help="hyprmasq checkout to run in the VM, by default this one")
    parser.add_argument("--lock", help="lock file to hold during each run, shared with other VM users")
    parser.add_argument("--display", default="1920x1080@1", help="the VM's monitor, WIDTHxHEIGHT@SCALE")
    parser.add_argument("--variant", help="the task's variant, for a task that has them (bench/tasks/TASK/variants)")
    args = parser.parse_args()

    agents = [a.split(":", 1) for a in (args.agent or DEFAULT_AGENTS)]
    for harness, _ in agents:
        if harness not in ("claude", "codex"):
            parser.error(f"unknown harness {harness}")
    if not os.path.isdir(os.path.join(BENCH, "tasks", args.task)):
        parser.error(f"no task {args.task} in {os.path.join(BENCH, 'tasks')}")
    variants = task_variants(args.task)
    if variants and args.variant not in variants:
        parser.error(f"task {args.task} needs --variant, one of {', '.join(variants)}")
    if not variants and args.variant:
        parser.error(f"task {args.task} has no variants")
    name = args.task + (f"-{args.variant}" if args.variant else "")
    out = os.path.abspath(args.out or os.path.join(
        BENCH, "results", f"{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{name}"))
    os.makedirs(out, exist_ok=True)
    source = os.path.abspath(args.source) if args.source else None
    config = {**source_commit(source), "task": args.task, "variant": args.variant, "display": args.display}
    check_resume(out, config, parser)
    driver = build_driver(out, source, args.task, args.variant, args.display)
    with open(os.path.join(out, "source.json"), "w") as f:
        json.dump(config, f, indent=2)
    # Repetitions go round the agents, so that a slow hour of an API does not hit one agent's runs only.
    for rep in range(1, args.reps + 1):
        for harness, model in agents:
            if os.path.exists(os.path.join(out, f"{harness}-{model}-{rep}", "summary.json")):
                continue
            shutil.rmtree(os.path.join(out, f"{harness}-{model}-{rep}"), ignore_errors=True)
            try:
                with vm_lock(args.lock):
                    run_one(driver, args.task, args.variant, harness, model, rep, out, args.cid, args.timeout, not args.no_debrief)
            except Exception as e:  # one broken run does not end the benchmark
                print(f"[{harness} {model} #{rep}] error: {e}", flush=True)
    print(report.report(out))


if __name__ == "__main__":
    main()
