#!/usr/bin/env python3
"""One questionnaire run on a Mac: run_mac_form.py --tool openai-cu|cua --rep N --out DIR [--via app|exec]

With --via app, the default, the run is a session that a person starts by hand in the ChatGPT app. With --via exec,
the runner starts the agent itself: the Codex CLI that the ChatGPT app bundles, with OpenAI's computer use from the
user's ~/.codex, which the app's Codex uses too, or with Cua Driver from its own Codex home, and a TextEdit note in
front as the user's window.

Opens form_qt.py as the app Questionnaire (questionnaire.sh, which runs it with a copy of Homebrew's Python.app
renamed to Questionnaire.app), brings ChatGPT back to the front, as the user's window, and logs the frontmost app.
It then waits for the next session of the ChatGPT app's Codex in ~/.codex/sessions whose prompt names the
questionnaire, until that session's task completes, has the app write its state and grades it with check.py. macOS
keeps no log of the windows that open, as Hyprland does, so in place of check.py's rule against other windows, a run
fails when an app other than ChatGPT and the questionnaire comes to the front while the agent works.

Run it in a terminal of the user's desktop session, such as tmux started from Terminal: macOS keeps a process
started over SSH out of the desktop, where neither the app nor the computer-use tools could see each other.
"""

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import threading
import time

from agent_process import wait_agent

HOME = os.path.expanduser("~")
BENCH = os.path.join(HOME, "hyprmasq-bench")
FORM = os.path.join(BENCH, "form")
SESSIONS = os.path.join(HOME, ".codex", "sessions")
USER_APP = "ChatGPT"
# The Codex CLI in the ChatGPT app, which its own Codex runs as an app server with features.code_mode_host on.
CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
# The agent's own tools that the runs turn off, as run_form.py does, but plugins, which bring OpenAI's computer use.
CODEX_DISABLED = ["shell_tool", "unified_exec", "hooks", "browser_use", "browser_use_external", "in_app_browser",
                  "multi_agent", "image_generation", "memories", "goals", "tool_suggest", "workspace_dependencies"]
# Each tool's Codex home: the user's for OpenAI's computer use, whose plugin config names it, and Cua's own, with
# cua-driver's MCP server and skill and nothing of OpenAI's.
HOMES = {"openai-cu": HOME, "cua": os.path.join(BENCH, "homes", "cua")}
SKILL_MENTION = {"openai-cu": "", "cua": "$cua-driver "}
# run_form.py's prompt, for the user's computer rather than their Linux desktop.
PROMPT = (
    "You are helping Mara Lindqvist on her computer. You can see and control it only through your desktop tools. "
    "Mara is working in another window. She has authorized foreground input and focus changes on this desktop if "
    "your tools need them, but prefer ways that leave her window alone.\n\n"
    "A questionnaire is open in a window titled \"Questionnaire\". Answer every question in it exactly as the "
    "question says, then press Submit at its end. Work only in that window: do not start other programs. When you "
    "are done, say \"Done\"."
)


def front():
    """The frontmost app's name, from lsappinfo."""
    asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["lsappinfo", "info", "-only", "name", asn], capture_output=True, text=True).stdout
    return out.split("=", 1)[1].strip().strip('"') if "=" in out else out.strip()


class FrontLog(threading.Thread):
    def __init__(self, path):
        super().__init__(daemon=True)
        self.out = open(path, "a", buffering=1)
        self.stop = False

    def run(self):
        last = None
        while not self.stop:
            name = front()
            if name != last:
                self.out.write(json.dumps({"time": time.time(), "front": name}) + "\n")
                last = name
            time.sleep(0.2)


def rollouts():
    return set(glob.glob(os.path.join(SESSIONS, "*", "*", "*", "rollout-*.jsonl")))


def read_jsonl(path):
    """The records of a JSONL file. The ChatGPT app appends to its rollout while the runner polls it, so a record that
    is not complete yet is left for the next read."""
    records = []
    with open(path) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except ValueError:
                pass
    return records


def matching_session(known, since):
    """A new ChatGPT app session whose first user message names the questionnaire."""
    for path in sorted(rollouts() - known):
        if os.path.getmtime(path) < since:
            continue
        entries = read_jsonl(path)
        meta = next((e["payload"] for e in entries if e.get("type") == "session_meta"), {})
        # The ChatGPT app records the prompt as a user message item, codex exec also as a user_message event.
        text = json.dumps([e for e in entries if (e.get("payload") or {}).get("type") == "user_message"
                           or (e.get("payload") or {}).get("role") == "user"])
        if meta.get("originator") == "Codex Desktop" and "Questionnaire" in text:
            return path
    return None


def other_apps(path, start, end, user_app=USER_APP):
    """The apps other than the user's and the questionnaire that were frontmost between start and end, from the log
    of the frontmost app, which records each change: the one in front at start, and those that came to the front."""
    entries = sorted(read_jsonl(path), key=lambda e: e["time"])
    before = [e["front"] for e in entries if e["time"] <= start]
    fronts = before[-1:] + [e["front"] for e in entries if start < e["time"] <= end]
    return sorted({f for f in fronts if f not in (user_app, "Questionnaire")})


def run_exec(tool, out, timeout):
    """The agent run by the bundled Codex CLI from the tool's home: its rollout's path, whether its task completed,
    and the rollout's entries."""
    home = HOMES[tool]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CLAUDE", "T3CODE"))}
    env.update(HOME=home, CODEX_HOME=os.path.join(home, ".codex"))
    command = [CODEX, "-c", "features.code_mode_host=true", "exec", "--json", "--skip-git-repo-check",
               "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="medium"']
    # Computer Use asks Codex before it acts in an app, which codex exec declines in a sandbox. The ChatGPT app's
    # Codex runs without one, as the user's config sets it. The agent's shell and other tools stay off.
    command.append("--dangerously-bypass-approvals-and-sandbox")
    for feature in CODEX_DISABLED:
        command += ["--disable", feature]
    command.append(SKILL_MENTION[tool] + PROMPT)
    workdir = os.path.join(out, "workdir")
    os.makedirs(workdir)
    with open(os.path.join(out, "agent.jsonl"), "w") as stream, open(os.path.join(out, "agent.stderr"), "w") as err:
        proc = subprocess.Popen(command, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=err,
                                start_new_session=True)
        wait_agent(proc, timeout)
    thread = next((e.get("thread_id") for e in read_jsonl(os.path.join(out, "agent.jsonl"))
                   if e.get("type") == "thread.started"), None)
    paths = glob.glob(os.path.join(env["CODEX_HOME"], "sessions", "*", "*", "*", f"rollout-*{thread}.jsonl")) if thread else []
    if not paths:
        return None, False, []
    done, entries = finished(paths[0])
    return paths[0], done, entries


def finished(path):
    entries = read_jsonl(path)
    return any((e.get("payload") or {}).get("type") == "task_complete" for e in entries), entries


def task_times(entries, done):
    """The rollout's task bounds, when it recorded them. A failed startup may have only session metadata."""
    stamps = [e for e in entries if (e.get("payload") or {}).get("type") in ("task_started", "task_complete")]
    start = next((e["timestamp"] for e in stamps if e["payload"]["type"] == "task_started"), None)
    end = next((e["timestamp"] for e in reversed(stamps) if e["payload"]["type"] == "task_complete"), None) \
        if done else None
    return start, end


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, help="the computer-use tool the session uses, for the record")
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--via", choices=["app", "exec"], default="app")
    parser.add_argument("--no-dropdowns", action="store_true",
                        help="ask only checkboxes, sliders and text, as the task form-no-dropdowns")
    args = parser.parse_args()
    user_app = USER_APP if args.via == "app" else "TextEdit"
    if args.no_dropdowns:
        # The questionnaire and check.py inherit it.
        os.environ["HYPRMASQ_FORM_KINDS"] = "checkbox,slider,text"
    task = "form-no-dropdowns" if args.no_dropdowns else "form"

    out = os.path.join(args.out, f"{args.tool}-{args.rep}")
    os.makedirs(out)
    state_dir = os.path.join(out, "form-state")
    os.makedirs(state_dir)
    known = rollouts()

    app = subprocess.Popen([os.path.join(BENCH, "questionnaire.sh"), state_dir, str(args.count)],
                           stdout=open(os.path.join(out, "form.log"), "w"), stderr=subprocess.STDOUT,
                           start_new_session=True)
    windows = None
    try:
        deadline = time.time() + 60
        while not subprocess.run(["lsappinfo", "find", "bundleid=org.example.Questionnaire"],
                                 capture_output=True, text=True).stdout.strip():
            if time.time() > deadline:
                raise SystemExit("the questionnaire did not start")
            time.sleep(0.3)
        # Qt draws its window a moment after the app registers.
        time.sleep(3)
        if args.via == "exec":
            # The user's window: a note in TextEdit, which stays in front.
            note = os.path.join(BENCH, "user-notes.txt")
            open(note, "a").close()
            subprocess.run(["open", "-a", "TextEdit", note], check=True)
        else:
            subprocess.run(["open", "-a", USER_APP], check=True)
        # The questionnaire, launched last, may hold the front for a moment: the run starts with the user's app there.
        deadline = time.time() + 20
        while front() != user_app:
            if time.time() > deadline:
                raise SystemExit(f"{user_app} did not come to the front")
            subprocess.run(["osascript", "-e", f'tell application "{user_app}" to activate'], capture_output=True)
            time.sleep(1)
        time.sleep(2)
        ready = time.time()
        log = FrontLog(os.path.join(out, "front.jsonl"))
        log.start()
        # The questionnaire's windows on screen, popups too, which the frontmost app alone does not show.
        windows = subprocess.Popen([os.path.join(BENCH, "qt-macos", "bin", "python"),
                                    os.path.join(FORM, "mac_windows.py"), "Questionnaire", user_app,
                                    os.path.join(out, "windows.jsonl")])
        if args.via == "exec":
            session, done, entries = run_exec(args.tool, out, args.timeout)
        else:
            print(f"Ready: the questionnaire is open behind {USER_APP}. Start a new session in the ChatGPT app "
                  f"with this prompt:\n\n{PROMPT}\n", flush=True)
            session = None
            deadline = ready + args.timeout
            while time.time() < deadline:
                session = session or matching_session(known, ready)
                if session and finished(session)[0]:
                    break
                time.sleep(2)
            done, entries = finished(session) if session else (False, [])
        log.stop = True
        windows.terminate()
        print(f"Session: {session}, finished: {done}", flush=True)

        if app.poll() is None:
            os.kill(app.pid, signal.SIGUSR1)
            for _ in range(50):
                if os.path.exists(os.path.join(state_dir, "state.json")):
                    break
                time.sleep(0.2)
            time.sleep(0.5)
        start = end = None
        if entries:
            start, end = task_times(entries, done)
            with open(os.path.join(out, "transcript.jsonl"), "w") as f:
                f.writelines(json.dumps(e) + "\n" for e in entries)
        since = parse_time(start) if start else ready
        check = json.loads(subprocess.run(
            [sys.executable, os.path.join(FORM, "check.py"), "--state",
             os.path.join(state_dir, "state.json"), "--count", str(args.count), "--window", "-",
             "--events", "/dev/null", "--since", str(since)],
            capture_output=True, text=True, check=True).stdout)
        agent_end = parse_time(end) if end else time.time()
        others = other_apps(os.path.join(out, "front.jsonl"), since, agent_end, user_app)
        failures = check["failures"] + [f"an app other than the questionnaire came to the front: {a}" for a in others]
        summary = {
            "backend": args.tool, "rep": args.rep, "task": task, "toolkit": "qt", "variant": args.count,
            "harness": "codex-desktop" if args.via == "app" else "codex-exec", "session": session, "finished": done,
            "agent": {"start": since, "end": agent_end, "timed_out": not done},
            "user_app": user_app, "passed": check["passed"] and not others, "failures": failures, "check": check,
        }
        summary["wall_seconds"] = round(summary["agent"]["end"] - summary["agent"]["start"], 1)
        with open(os.path.join(out, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps({k: summary[k] for k in ("backend", "rep", "wall_seconds", "passed", "failures")}),
              flush=True)
    finally:
        # Also when the wait for the session is interrupted, which leaves the window log running otherwise.
        if windows and windows.poll() is None:
            windows.terminate()
        if app.poll() is None:
            os.killpg(app.pid, signal.SIGTERM)


def parse_time(stamp):
    """Codex's ISO timestamps, such as 2026-10-22T03:12:32.559Z, as Unix time."""
    from datetime import datetime, timezone
    return datetime.strptime(stamp.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc).timestamp()


if __name__ == "__main__":
    main()
