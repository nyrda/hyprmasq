#!/usr/bin/env python3
"""One questionnaire run on a real Hyprland machine: hyprmasq or Cua Driver, each from its own agent home.

    run_form.py --backend cua|hyprmasq --rep N --out DIR [--count 25] [--harness codex|claude] [--model M]
                [--effort E] [--timeout S] [--no-agent]

Port of bench/tasks/form/setup.py from the NixOS VM to a live Hyprland session, with the same app, items and
checker from this checkout. See README.md for the agent homes in $BENCH_HOMES, default ~/bench/homes. A foot terminal titled user-terminal stands in for the user's
window: it has focus on the visible workspace, and the questionnaire is moved to the hidden workspace `form`.
Every Hyprland event is logged with the time, for the checker's window list and for the focus changes, which are
recorded and not disqualifying. --no-agent checks the plumbing without a model.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time

from agent_process import stop_agent

HOME = os.path.expanduser("~")
CHECKOUT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FORM = f"{CHECKOUT}/bench/tasks/form"
HOMES = os.environ.get("BENCH_HOMES", f"{HOME}/bench/homes")
CODEX = f"{HOME}/.local/share/mise/installs/codex/latest/bin/codex"
CLAUDE = f"{HOME}/.local/share/mise/installs/claude/latest/claude"
# The MCP server each backend registered in its Claude Code home.
CLAUDE_SERVER = {"cua": "cua-driver", "hyprmasq": "hyprmasq"}

# As bench/run.py, minus skill and tool discovery: the shell would let an agent bypass its desktop tool.
CODEX_DISABLED = [
    "shell_tool", "unified_exec", "hooks", "computer_use", "browser_use", "browser_use_external", "in_app_browser",
    "apps", "plugins", "multi_agent", "image_generation", "memories", "goals", "tool_suggest", "workspace_dependencies",
]

PROMPT = (
    "You are helping Mara Lindqvist on her Linux desktop. You can see and control it only through your desktop "
    "tools. Mara is working in another window. She has authorized foreground input and focus changes on this "
    "desktop if your tools need them, but prefer ways that leave her window alone.\n\n"
    "A questionnaire is open in a window titled \"Questionnaire\". Answer every question in it exactly as the "
    "question says, then press Submit at its end. Work only in that window: do not start other programs. "
    "When you are done, say \"Done\"."
)
# Codex injects an installed skill's SKILL.md when the prompt names it; the shell it would read it with is off.
SKILL_MENTION = {"cua": "$cua-driver ", "hyprmasq": ""}
# Claude Code runs a skill named as a slash command at the start of the prompt.
CLAUDE_SKILL = {"cua": "/cua-driver ", "hyprmasq": ""}


def session_env():
    env = dict(os.environ)
    out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        key, _, value = line.partition("=")
        if key in ("WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "DBUS_SESSION_BUS_ADDRESS") \
                or key.startswith("XDG_"):
            env[key] = value
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    env["PATH"] = f"{HOME}/.local/bin:" + env.get("PATH", "")
    return env


ENV = session_env()


def hypr(*args):
    return subprocess.run(["hyprctl", *args], env=ENV, capture_output=True, text=True, check=True).stdout


def clients():
    return json.loads(hypr("-j", "clients"))


def window(predicate):
    return next((c for c in clients() if predicate(c)), None)


def questionnaire_window(pid):
    """The window of the process whose state this run grades, once it has mapped."""
    return window(lambda c: c["title"] == "Questionnaire" and c.get("pid") == pid)


def dispatch(lua):
    out = hypr("dispatch", lua)
    if out.strip() not in ("ok", ""):
        raise RuntimeError(f"dispatch {lua}: {out}")


# The user's workspace, where their window is and which stays in view.
USER_WORKSPACE = "1"


def to_user_workspace():
    """Puts the user's workspace in view. A run starts from it, and a tool that left the task's workspace in view, as
    Cua's foreground input does, would otherwise start the next run there, with the task window in sight."""
    dispatch(f'hl.dsp.focus({{ workspace = "{USER_WORKSPACE}" }})')
    wait_for("the user's workspace", lambda: json.loads(hypr("-j", "activeworkspace"))["name"] == USER_WORKSPACE,
             timeout=10)


def check_setup(before):
    """Stops a run whose user is not on their workspace, or whose task window is not hidden, before the agent starts."""
    if before["active_workspace"]["name"] != USER_WORKSPACE:
        raise SystemExit(f"the user is on workspace {before['active_workspace']['name']}, not {USER_WORKSPACE}")
    task = next((c for c in before["clients"] if c["address"] == before["form"]), None)
    if not task or task["workspace"]["name"] == USER_WORKSPACE:
        raise SystemExit("the task window is not on a hidden workspace")
    if "form_pid" in before and task.get("pid") != before["form_pid"]:
        raise SystemExit("the task window does not belong to this run's questionnaire process")


def wait_for(description, predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise RuntimeError(f"timed out waiting for {description}")


class EventLog(threading.Thread):
    """Hyprland's socket2 events, each line prefixed with the time, as the VM's event logger writes them, with the
    process of each window that opens, as the form task's windows.py adds it: check.py counts the windows of the
    questionnaire's own process as its own."""

    def __init__(self, path):
        super().__init__(daemon=True)
        sock = f"{ENV['XDG_RUNTIME_DIR']}/hypr/{ENV['HYPRLAND_INSTANCE_SIGNATURE']}/.socket2.sock"
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(sock)
        self.out = open(path, "a", buffering=1)

    def run(self):
        for line in self.sock.makefile("r", errors="replace"):
            self.out.write(f"{time.time():.3f} {line}")
            # Hyprland's own dialogs, such as its ANR prompt, name the app in their arguments; they can close
            # within milliseconds, so read them at once.
            if line.startswith("openwindow>>") and "hyprland-dialog" in line:
                self.out.write(f"{time.time():.3f} dialog>>{json.dumps(dialog_commands())}\n")
            if line.startswith("openwindow>>"):
                address = "0x" + line[12:].split(",", 1)[0].removeprefix("0x")
                try:
                    pid = next((c["pid"] for c in clients() if c["address"] == address), None)
                except (subprocess.CalledProcessError, ValueError):
                    pid = None
                if pid is not None:
                    self.out.write(f"{time.time():.3f} windowpid>>{address},{pid}\n")


def dialog_commands():
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args and args[0].endswith(b"hyprland-dialog"):
            found.append([a.decode(errors="replace") for a in args if a])
    return found


def codex_command(backend, model, effort):
    command = [CODEX, "exec", "--json", "--skip-git-repo-check", "-m", model, "--sandbox", "read-only",
               "-c", 'web_search="disabled"', "-c", f"model_reasoning_effort={json.dumps(effort)}"]
    for feature in CODEX_DISABLED:
        command += ["--disable", feature]
    return command + [SKILL_MENTION[backend] + PROMPT]


def claude_command(backend, model, effort):
    """Claude Code from the backend's home: its MCP server and skills from there, no built-in tool but Skill."""
    return [CLAUDE, "-p", CLAUDE_SKILL[backend] + PROMPT, "--model", model, "--effort", effort,
            "--output-format", "stream-json", "--verbose", "--tools", "Skill",
            "--allowedTools", f"Skill mcp__{CLAUDE_SERVER[backend]}"]


def run_agent(backend, model, effort, out, timeout, harness="codex"):
    home = f"{HOMES}/{backend}" if harness == "codex" else f"{HOMES}/claude-{backend}"
    env = dict(ENV, HOME=home)
    if harness == "codex":
        env["CODEX_HOME"] = f"{home}/.codex"
    # Claude Code bills to the API key and headers the series was started with; nothing else of a surrounding
    # agent session.
    keep = ("CODEX_HOME", "ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS") if harness == "claude" else ("CODEX_HOME",)
    for key in [k for k in env if k.startswith(("CLAUDE", "CODEX_", "OPENAI_", "ANTHROPIC_", "T3CODE"))]:
        if key not in keep:
            del env[key]
    if harness == "claude":
        env.update({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "DISABLE_AUTOUPDATER": "1"})
        if "ANTHROPIC_API_KEY" not in env:
            raise SystemExit("no ANTHROPIC_API_KEY for Claude Code")
    workdir = f"{out}/workdir"
    os.makedirs(workdir)
    command = codex_command(backend, model, effort) if harness == "codex" else claude_command(backend, model, effort)
    with open(f"{out}/command.json", "w") as f:
        json.dump({"command": command, "home": home}, f, indent=2)
    stream = open(f"{out}/agent.jsonl", "w")
    stderr = open(f"{out}/agent.stderr", "w")
    start = time.time()
    proc = subprocess.Popen(command, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=stderr, text=True, start_new_session=True)
    timed_out = False

    def kill():
        nonlocal timed_out
        timed_out = True
        stop_agent(proc)

    timer = threading.Timer(timeout, kill)
    timer.start()
    thread_id = None
    try:
        for line in proc.stdout:
            now = time.time()
            try:
                event = json.loads(line)
            except ValueError:
                event = {"raw": line.rstrip("\n")}
            if event.get("type") == "thread.started":
                thread_id = event.get("thread_id")
            elif event.get("type") == "system" and event.get("subtype") == "init":
                thread_id = event.get("session_id")
            stream.write(json.dumps({"time": now, "event": event}) + "\n")
            stream.flush()
        proc.wait()
    finally:
        timer.cancel()
        stop_agent(proc)
        timer.join()
        proc.stdout.close()
        stream.close()
        stderr.close()
    end = time.time()
    transcript = None
    if thread_id:
        for root, _, files in os.walk(f"{home}/.codex/sessions" if harness == "codex" else f"{home}/.claude/projects"):
            for name in files:
                if thread_id in name:
                    transcript = os.path.join(root, name)
        if transcript:
            shutil.copy(transcript, f"{out}/transcript.jsonl")
    return {"start": start, "end": end, "exit": proc.returncode, "timed_out": timed_out, "thread_id": thread_id,
            "transcript": transcript, "harness": harness}


def app_command(toolkit, state_dir, count):
    if toolkit == "qt":
        return [os.environ.get("QT_PYTHON", "python3"), f"{FORM}/form_qt.py", state_dir, str(count)]
    return ["python3", f"{FORM}/form.py", state_dir, str(count)]


def app_env(toolkit):
    # Native Wayland, whatever the caller's environment says: Omarchy's GDK_BACKEND and QT_QPA_PLATFORM list X11 as a
    # fallback, and as systemd prints them, with their quotes, no backend at all.
    if toolkit != "qt":
        return dict(ENV, GDK_BACKEND="wayland")
    # Qt's AT-SPI bridge on from the start: GTK's is always on, and Qt's otherwise waits for an accessibility client
    # to ask for it.
    return dict(ENV, QT_QPA_PLATFORM="wayland", QT_LINUX_ACCESSIBILITY_ALWAYS_ON="1")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["cua", "hyprmasq"], required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--no-dropdowns", action="store_true",
                        help="ask only checkboxes, sliders and text, as the task form-no-dropdowns")
    parser.add_argument("--toolkit", choices=["gtk", "qt"], default="gtk",
                        help="form.py, the GTK 4 app, or form_qt.py, run by $QT_PYTHON, a Python with PySide6")
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--harness", choices=["codex", "claude"], default="codex")
    parser.add_argument("--no-agent", action="store_true")
    args = parser.parse_args()
    if args.no_dropdowns:
        # The app and check.py read it, through ENV and this process's environment.
        os.environ["HYPRMASQ_FORM_KINDS"] = ENV["HYPRMASQ_FORM_KINDS"] = "checkbox,slider,text"
    task = "form-no-dropdowns" if args.no_dropdowns else "form"

    out = os.path.join(args.out, f"{args.backend}-{args.rep}")
    os.makedirs(out)
    events_path = f"{out}/hyprland-events.log"
    EventLog(events_path).start()

    procs = []
    # A powered-down output skips frames and focus changes: keep the laptop's display on for the run.
    dispatch('hl.dsp.dpms({ action = "on" })')
    wait_for("the display to power on", lambda: all(m["dpmsStatus"] for m in json.loads(hypr("-j", "monitors"))))
    # A locked session takes no focus changes and shows the lock screen instead of the desktop.
    if subprocess.run(["/usr/share/omarchy/bin/omarchy-hyprland-session-locked"], env=ENV).returncode == 0:
        raise SystemExit("the session is locked")
    original = json.loads(hypr("-j", "activewindow"))
    try:
        to_user_workspace()
        # The user's window, focused on the visible workspace.
        procs.append(subprocess.Popen(["foot", "--app-id", "user-terminal", "--title", "user-terminal", "sleep", "infinity"], env=ENV, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True))
        terminal = wait_for("the user terminal", lambda: window(lambda c: c["class"] == "user-terminal"))

        state_dir = f"{out}/form-state"
        os.makedirs(state_dir)
        # Without a state descriptor, form.py writes its state to STATE-DIR/state.json on SIGUSR1. The VM keeps it
        # where only root can write; here the agents have no shell to reach it.
        snapshot = f"{state_dir}/state.json"
        form_proc = subprocess.Popen(
            app_command(args.toolkit, state_dir, args.count), env=app_env(args.toolkit),
            stdout=open(f"{out}/form.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
        procs.append(form_proc)
        form = wait_for("the questionnaire", lambda: questionnaire_window(form_proc.pid), timeout=120)
        dispatch(f'hl.dsp.window.move({{ window = "address:{form["address"]}", workspace = "name:form", '
                 f'follow = false }})')
        dispatch(f'hl.dsp.focus({{ window = "address:{terminal["address"]}" }})')
        time.sleep(3)
        before = {"active_window": json.loads(hypr("-j", "activewindow")),
                  "active_workspace": json.loads(hypr("-j", "activeworkspace")),
                  "clients": clients(), "terminal": terminal["address"], "form": form["address"],
                  "form_pid": form_proc.pid}
        with open(f"{out}/before.json", "w") as f:
            json.dump(before, f, indent=2)
        check_setup(before)

        if args.no_agent:
            agent = {"start": time.time(), "end": time.time(), "exit": None, "timed_out": False}
        else:
            agent = run_agent(args.backend, args.model, args.effort, out, args.timeout, args.harness)

        after = {"active_window": json.loads(hypr("-j", "activewindow")),
                 "active_workspace": json.loads(hypr("-j", "activeworkspace")), "clients": clients()}
        with open(f"{out}/after.json", "w") as f:
            json.dump(after, f, indent=2)

        if form_proc.poll() is None:
            form_proc.send_signal(signal.SIGUSR1)
            wait_for("the questionnaire's state", lambda: os.path.exists(snapshot) and os.path.getsize(snapshot) > 0,
                     timeout=30)
            time.sleep(0.5)
        check = json.loads(subprocess.run(
            ["python3", f"{FORM}/check.py", "--state", snapshot, "--count", str(args.count),
             "--window", form["address"], "--events", events_path, "--since", str(agent["start"])],
            capture_output=True, text=True, check=True).stdout)
        summary = {
            "backend": args.backend, "rep": args.rep, "task": task, "variant": args.count, "toolkit": args.toolkit,
            "harness": args.harness, "model": args.model, "effort": args.effort, "agent": agent,
            "wall_seconds": round(agent["end"] - agent["start"], 1),
            "passed": check["passed"], "failures": check["failures"], "check": check,
            "machine": {"host": socket.gethostname(), "hyprland": hypr("version").splitlines()[0]},
        }
        with open(f"{out}/summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps({k: summary[k] for k in ("backend", "rep", "wall_seconds", "passed", "failures")}))
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        # Back to the user's workspace, should an agent have left another one active.
        try:
            to_user_workspace()
        except Exception:
            pass


if __name__ == "__main__":
    main()
