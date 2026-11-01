#!/usr/bin/env python3
"""One task run on a Mac: run_mac.py --task TASK --tool openai-cu|cua|claude-cu --rep N --out DIR
                                     [--via app|exec] [--harness codex|claude] [--model M] [--effort E]

The tasks are bench/laptop's, in the Mac's apps: form and form-no-dropdowns, the Qt questionnaire as the app
Questionnaire, files, a fresh folder Project in a Finder window, and inkscape, the drawing in Inkscape. The runner
opens the task's window behind the user's, logs the frontmost app and, with mac_windows.py, the task app's windows
on screen, runs the agent or waits for its session, and grades the task as the laptop's runners do. macOS keeps no
log of the windows that open, as Hyprland does, so in place of the rule against other windows, a run fails when an
app other than the user's and the task's comes to the front while the agent works.

The tools and their agents:

- openai-cu: OpenAI's computer use. With --via exec, the Codex CLI that the ChatGPT app bundles runs it from its own
  Codex home, with a TextEdit note in front as the user's window. With --via app, a person starts the session by hand
  in the ChatGPT app, which is the user's window, and the app's Codex runs with the user's ~/.codex.
- cua: Cua Driver, run with --via exec. With --harness codex, the bundled Codex from Cua's own Codex home. With
  --harness claude, Claude Code from Cua's own Claude Code home, billed to $ANTHROPIC_API_KEY or, without one, to
  the user's Claude subscription.
- claude-cu: Claude's computer use in the Claude desktop app, which is the user's window. With --via drive, the
  default, the runner opens a new Code session in ~/hyprmasq-bench/claude-cu with `claude --desktop`, pastes the
  prompt and sends it, and grants the task's app when Claude asks, through Cua Driver's MCP server (ClaudeApp). With
  --via app, a person does that by hand.

Run it in a terminal of the user's desktop session, such as tmux started from Terminal: macOS keeps a process
started over SSH out of the desktop, where neither the app nor the computer-use tools could see each other.
"""

import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

from agent_process import wait_agent
import files_task

HOME = os.path.expanduser("~")
BENCH = os.path.join(HOME, "hyprmasq-bench")
FORM = os.path.join(BENCH, "form")
QT_PYTHON = os.path.join(BENCH, "qt-macos", "bin", "python")
SESSIONS = os.path.join(HOME, ".codex", "sessions")
# Where Claude Code, the desktop app's Code sessions too, keeps its transcripts.
CLAUDE_PROJECTS = os.path.join(HOME, ".claude", "projects")
# The Codex CLI in the ChatGPT app, which its own Codex runs as an app server with features.code_mode_host on.
CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
CLAUDE = shutil.which("claude") or "/opt/homebrew/bin/claude"
INKSCAPE = "/Applications/Inkscape.app/Contents/MacOS/inkscape"
TASKS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tasks")
# The agent's own tools that the runs turn off, as run_form.py does, but plugins, which bring OpenAI's computer use.
CODEX_DISABLED = ["shell_tool", "unified_exec", "hooks", "browser_use", "browser_use_external", "in_app_browser",
                  "multi_agent", "image_generation", "memories", "goals", "tool_suggest", "workspace_dependencies"]
# Each tool's agent home, made by mac_setup.sh: OpenAI's computer use's with the user's login and of their Codex
# config only the plugin's, and Cua's with cua-driver's MCP server and skill and nothing of OpenAI's or Anthropic's.
# Neither holds the user's own instructions, memories or settings.
HOMES = {("openai-cu", "codex"): os.path.join(BENCH, "homes", "openai-cu"),
         ("cua", "codex"): os.path.join(BENCH, "homes", "cua"),
         ("cua", "claude"): os.path.join(BENCH, "homes", "claude-cua")}
# Codex injects an installed skill's SKILL.md when the prompt names it, Claude Code runs it as a slash command.
SKILL_MENTION = {("openai-cu", "codex"): "", ("cua", "codex"): "$cua-driver ", ("cua", "claude"): "/cua-driver "}
API_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_CUSTOM_HEADERS")
# The user's app for each way of running: the app the session runs in, or a note in TextEdit.
USER_APPS = {("openai-cu", "app"): "ChatGPT", ("claude-cu", "app"): "Claude", ("claude-cu", "drive"): "Claude"}
# The Claude app's names of the models, as its model menu shows them with the effort, such as "Sonnet 5.5 Medium".
CLAUDE_APP_MODELS = {"claude-sonnet-5-5": "Sonnet 5.5", "claude-opus-5-5": "Opus 5.5", "claude-haiku-5-5": "Haiku 5.5"}
# The buttons of the Claude app's own prompts that the driven runs answer: the grant of the task's app to computer use,
# which the measured time leaves out, and the trust of the bench's folder on its first session.
CLAUDE_APP_GRANT = r'^AXButton "(Allow|Allow for this session|Always allow|Approve|Grant access)"'
CLAUDE_APP_TRUST = r'^AXButton "Trust workspace"'
# Claude's computer-use tools, the ToolSearch that loads them, and the app's computer-use skill are the only tools
# a claude-cu run may use, as Cua's runs load Cua's skill.
CLAUDE_CU_TOOLS = ("mcp__computer-use__", "ToolSearch")
CLAUDE_CU_SKILL = "anthropic-skills:computer-use"

INTRO = (
    "You are helping Mara Lindqvist on her computer. You can see and control it only through your desktop tools. "
    "Mara is working in another window. She has authorized foreground input and focus changes on this desktop if "
    "your tools need them, but prefer ways that leave her window alone.\n\n"
)
# The laptop's prompts, for the Mac's apps.
PROMPTS = {
    "form": (
        "A questionnaire is open in a window titled \"Questionnaire\". Answer every question in it exactly as the "
        "question says, then press Submit at its end. Work only in that window: do not start other programs. When "
        "you are done, say \"Done\"."
    ),
    "files": (
        "Finder is open in a window showing the folder Project. In that folder, rename the file report-draft.txt to "
        "report-final.txt and move it into the folder Archive inside Project. Leave every other file and folder as "
        "it is. Work only in that window: do not start other programs. When you are done, say \"Done\"."
    ),
    "inkscape": (
        "Inkscape is open in a window showing drawing.svg. In that drawing, move the blue rectangle exactly 2 px to "
        "the right, from x=120 px to x=122 px. Keep y=120 px, width=200 px and height=100 px. Save the existing SVG "
        "with Cmd+S. Work only in that window: do not start other programs. When you are done, say \"Done\"."
    ),
}
PROMPTS["form-no-dropdowns"] = PROMPTS["form"]


def prompt(task):
    return INTRO + PROMPTS[task]


def front():
    """The frontmost app's name, from lsappinfo."""
    asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip()
    out = subprocess.run(["lsappinfo", "info", "-only", "name", asn], capture_output=True, text=True).stdout
    return out.split("=", 1)[1].strip().strip('"') if "=" in out else out.strip()


def running(bundle):
    return bool(subprocess.run(["lsappinfo", "find", f"bundleid={bundle}"], capture_output=True,
                               text=True).stdout.strip())


def wait_for(what, predicate, timeout=60, step=0.3):
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if time.time() > deadline:
            raise SystemExit(f"timed out waiting for {what}")
        time.sleep(step)


def app_windows(app):
    """The frames of the app's normal-layer windows on screen, from Quartz in the questionnaire's Python."""
    code = ("import json,sys,Quartz\n"
            "ws=Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly,Quartz.kCGNullWindowID)\n"
            "print(json.dumps([dict(w.get('kCGWindowBounds') or {}) for w in ws\n"
            "  if w.get('kCGWindowOwnerName')==sys.argv[1] and int(w.get('kCGWindowLayer',0))==0]))")
    out = subprocess.run([QT_PYTHON, "-c", code, app], capture_output=True, text=True).stdout
    return json.loads(out or "[]")


def osascript(script):
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True)


def finder_windows():
    """The folder of each Finder window, front to back, as POSIX paths. Finder lists its windows' targets as
    aliases, whose paths AppleScript gives outside Finder's own commands."""
    result = osascript('tell application "Finder" to set targets to (target of every Finder window as alias list)\n'
                       'set paths to ""\nrepeat with t in targets\n  set paths to paths & POSIX path of t & linefeed\n'
                       'end repeat\nreturn paths')
    return [line for line in result.stdout.splitlines() if line]


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


class Form:
    """The Qt questionnaire as the app Questionnaire, graded from the state it writes on SIGUSR1."""
    app = "Questionnaire"
    bundle = "org.example.Questionnaire"

    def __init__(self, task, out, count):
        self.task, self.out, self.count = task, out, count
        self.state_dir = os.path.join(out, "form-state")
        self.proc = None
        if task == "form-no-dropdowns":
            # The questionnaire and check.py inherit it.
            os.environ["HYPRMASQ_FORM_KINDS"] = "checkbox,slider,text"

    def start(self):
        if running(self.bundle):
            raise SystemExit("Questionnaire already runs")
        os.makedirs(self.state_dir)
        self.proc = subprocess.Popen([os.path.join(BENCH, "questionnaire.sh"), self.state_dir, str(self.count)],
                                     stdout=open(os.path.join(self.out, "form.log"), "w"),
                                     stderr=subprocess.STDOUT, start_new_session=True)
        wait_for("the questionnaire", lambda: running(self.bundle))
        # Qt draws its window a moment after the app registers.
        time.sleep(3)

    def grade(self, since):
        state = os.path.join(self.state_dir, "state.json")
        if self.proc.poll() is None:
            os.kill(self.proc.pid, signal.SIGUSR1)
            for _ in range(50):
                if os.path.exists(state):
                    break
                time.sleep(0.2)
            time.sleep(0.5)
        return json.loads(subprocess.run(
            [sys.executable, os.path.join(FORM, "check.py"), "--state", state, "--count", str(self.count),
             "--window", "-", "--events", "/dev/null", "--since", str(since)],
            capture_output=True, text=True, check=True).stdout)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)

    def record(self):
        return {"toolkit": "qt", "variant": self.count}


class Files:
    """A fresh folder Project in a Finder window, graded by files_task.grade. Finder writes .DS_Store files, which
    the grade leaves out."""
    app = "Finder"

    def __init__(self, task, out, count):
        self.root = os.path.join(out, "files", "Project")

    def start(self):
        files_task.make(self.root)
        subprocess.run(["open", self.root], check=True)
        wait_for("Finder's window of Project", lambda: self.window_count() > 0, timeout=20)
        time.sleep(1)

    def window_count(self):
        return sum(path == self.root + "/" for path in finder_windows())

    def grade(self, since):
        failures, found = files_task.grade(self.root, ignore={".DS_Store"})
        return {"passed": not failures, "failures": failures, "checks": {"tree": found}}

    def stop(self):
        # Every Finder window inside the run's folder, where the agent may have gone, and none of the user's: by
        # index from the back, as closing one renumbers those after it.
        parent = os.path.dirname(self.root) + "/"
        for index in reversed([i for i, path in enumerate(finder_windows(), 1) if path.startswith(parent)]):
            osascript(f'tell application "Finder" to close Finder window {index}')

    def record(self):
        return {"variant": None}


def inkscape_check():
    """The VM's grader of the Inkscape task, bench/tasks/inkscape/check.py, which the Mac's runs share."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("inkscape_check", os.path.join(TASKS_DIR, "inkscape", "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Inkscape:
    """The task's drawing in Inkscape, graded as in the VM by bench/tasks/inkscape/check.py: saved over itself, the
    rectangle at x=122, y=120, 200 by 100 px in the file and in Inkscape's own reading, and nothing else changed. The
    VM's rule against other windows is the runner's rule against other apps in front."""
    app = "Inkscape"
    bundle = "org.inkscape.Inkscape"

    def __init__(self, task, out, count):
        self.drawing = os.path.join(out, "drawing", "drawing.svg")
        # The process of the Inkscape this run started, the only one it ends.
        self.pid = None

    def start(self):
        # An Inkscape of an earlier run may still be quitting. One that does not end is the user's: the run stops.
        wait_for("no Inkscape to run", lambda: not running(self.bundle), timeout=20)
        os.makedirs(os.path.dirname(self.drawing))
        shutil.copy(os.path.join(TASKS_DIR, "inkscape", "drawing.svg"), self.drawing)
        os.chmod(self.drawing, 0o644)
        subprocess.run(["open", "-a", "Inkscape", self.drawing], check=True)
        self.pid = int(wait_for("Inkscape's process", lambda: subprocess.run(
            ["pgrep", "-x", "inkscape"], capture_output=True, text=True).stdout.split()[:1], timeout=30)[0])
        wait_for("Inkscape's window", lambda: any(w.get("Height", 0) > 300 for w in app_windows(self.app)),
                 timeout=120, step=1)
        # Inkscape draws its canvas and panels for a few seconds after mapping.
        time.sleep(8)

    def geometry(self):
        rows = subprocess.run([INKSCAPE, "--query-all", self.drawing], capture_output=True, text=True).stdout
        for row in rows.splitlines():
            fields = row.split(",")
            if fields[0] == "bench-rectangle" and len(fields) == 5:
                return list(map(float, fields[1:]))
        return None

    def grade(self, since):
        with open(os.path.join(TASKS_DIR, "inkscape", "drawing.svg"), "rb") as f:
            original = f.read()
        saved = None
        if os.path.exists(self.drawing):
            with open(self.drawing, "rb") as f:
                saved = f.read()
        return inkscape_check().grade(original, saved, opened=[], queried=self.geometry(), inkscape=True)

    def stop(self):
        # Inkscape asks before it quits with unsaved changes, which nobody is there to answer: the run's Inkscape is
        # ended, and killed when it has not gone within 10 s, so that the next run finds no Inkscape. A run that did
        # not start one ends none.
        if self.pid is None:
            return
        for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
            try:
                os.kill(self.pid, sig)
            except ProcessLookupError:
                return
            deadline = time.time() + wait
            while time.time() < deadline:
                try:
                    os.kill(self.pid, 0)
                except ProcessLookupError:
                    return
                time.sleep(0.3)

    def record(self):
        return {"variant": None}


TASKS = {"form": Form, "form-no-dropdowns": Form, "files": Files, "inkscape": Inkscape}


def read_jsonl(path):
    """The records of a JSONL file. An app appends to its transcript while the runner polls it, so a record that is
    not complete yet is left for the next read."""
    records = []
    with open(path) as f:
        for line in f:
            try:
                records.append(json.loads(line))
            except ValueError:
                pass
    return records


def rollouts():
    return set(glob.glob(os.path.join(SESSIONS, "*", "*", "*", "rollout-*.jsonl")))


def claude_transcripts():
    return set(glob.glob(os.path.join(CLAUDE_PROJECTS, "*", "*.jsonl")))


def codex_session(known, since):
    """A new ChatGPT app session whose first user message is the task's prompt."""
    for path in sorted(rollouts() - known):
        if os.path.getmtime(path) < since:
            continue
        entries = read_jsonl(path)
        meta = next((e["payload"] for e in entries if e.get("type") == "session_meta"), {})
        # The ChatGPT app records the prompt as a user message item, codex exec also as a user_message event.
        text = json.dumps([e for e in entries if (e.get("payload") or {}).get("type") == "user_message"
                           or (e.get("payload") or {}).get("role") == "user"])
        if meta.get("originator") == "Codex Desktop" and "Mara Lindqvist" in text:
            return path
    return None


def codex_finished(path):
    entries = read_jsonl(path)
    return any((e.get("payload") or {}).get("type") == "task_complete" for e in entries), entries


def user_text(entry):
    """The text of a person's message in a Claude Code transcript, not a tool result."""
    if entry.get("type") != "user":
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    texts = [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    return " ".join(texts) or None


def claude_session(known, since):
    """A new Claude desktop session whose first message is the task's prompt."""
    for path in sorted(claude_transcripts() - known):
        if os.path.getmtime(path) < since:
            continue
        first = next((t for t in map(user_text, read_jsonl(path)) if t), "")
        if "Mara Lindqvist" in first:
            return path
    return None


def claude_finished(path):
    """Whether the session's last reply ended its turn, rather than calling a tool, and its entries."""
    entries = read_jsonl(path)
    replies = [e for e in entries if e.get("type") == "assistant"]
    return bool(replies) and (replies[-1].get("message") or {}).get("stop_reason") == "end_turn", entries


def other_apps(path, start, end, allowed):
    """The apps other than the allowed ones that were frontmost between start and end, from the log of the
    frontmost app, which records each change: the one in front at start, and those that came to the front."""
    entries = sorted(read_jsonl(path), key=lambda e: e["time"])
    before = [e["front"] for e in entries if e["time"] <= start]
    fronts = before[-1:] + [e["front"] for e in entries if start < e["time"] <= end]
    return sorted({f for f in fronts if f not in allowed})


def agent_env(home, harness):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CODEX_", "OPENAI_", "CLAUDE", "ANTHROPIC_",
                                                                     "T3CODE"))}
    env["HOME"] = home
    # Codex loads OpenAI's bundled plugins, computer use among them, only from its default home, $HOME/.codex.
    if harness == "codex":
        if home != HOMES[("openai-cu", "codex")]:
            env["CODEX_HOME"] = os.path.join(home, ".codex")
    else:
        # Claude Code bills to the API key and headers the series was started with, or without a key to the user's
        # Claude subscription, whose login it finds in the keychain that the home links.
        env.update({k: os.environ[k] for k in API_KEYS if k in os.environ})
        env.update({"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "DISABLE_AUTOUPDATER": "1"})
    return env


USER_AUTH = os.path.join(HOME, ".codex", "auth.json")


def auth_in(home):
    """The user's Codex login, copied into the agent home before a run, as the copy a home kept from an earlier one
    may hold a refresh token that another Codex has used since. Returns the login as copied."""
    with open(USER_AUTH, "rb") as f:
        login = f.read()
    path = os.path.join(home, ".codex", "auth.json")
    with open(path + ".new", "wb") as f:
        f.write(login)
    os.chmod(path + ".new", 0o600)
    os.replace(path + ".new", path)
    return login


def auth_out(home, copied):
    """The login that a run renewed, back to the user's Codex, so that the refresh token it used up is not the
    user's next one: only when the user's login is still the one copied in, never over one renewed meanwhile. Codex
    takes no lock that the runner could share, so a renewal by the user's own Codex between this read and the
    replace would be lost: nobody may use Codex on the Mac during a series, as nobody may use the Mac."""
    with open(os.path.join(home, ".codex", "auth.json"), "rb") as f:
        renewed = f.read()
    with open(USER_AUTH, "rb") as f:
        current = f.read()
    if renewed != copied and current == copied:
        with open(USER_AUTH + ".new", "wb") as f:
            f.write(renewed)
        os.chmod(USER_AUTH + ".new", 0o600)
        os.replace(USER_AUTH + ".new", USER_AUTH)


def run_exec(tool, harness, task, model, effort, out, timeout):
    """The agent run by the bundled Codex CLI or Claude Code from the tool's home: its transcript's path, whether
    its task completed, and the transcript's entries."""
    home = HOMES[(tool, harness)]
    env = agent_env(home, harness)
    if harness == "codex":
        # The session's configuration, with its service tier, which service_tier() reads from the stderr log.
        env["RUST_LOG"] = "codex_exec=info"
    text = SKILL_MENTION[(tool, harness)] + prompt(task)
    if harness == "codex":
        # This Codex runs Sol and Terra in code mode only, as the ChatGPT app does: the model calls an MCP server's
        # tools from JavaScript in its exec tool, and sees a tool's image when its script passes it to image().
        command = [CODEX, "-c", "features.code_mode_host=true", "exec", "--json", "--skip-git-repo-check",
                   "-m", model, "-c", f"model_reasoning_effort={json.dumps(effort)}"]
        # Computer Use asks Codex before it acts in an app, which codex exec declines in a sandbox. The ChatGPT
        # app's Codex runs without one, as the user's config sets it. The agent's shell and other tools stay off.
        command.append("--dangerously-bypass-approvals-and-sandbox")
        for feature in CODEX_DISABLED:
            command += ["--disable", feature]
        command.append(text)
    else:
        # As run_form.py: Cua's MCP server and skill from the home, no built-in tool but Skill.
        command = [CLAUDE, "-p", text, "--model", model, "--effort", effort, "--output-format", "stream-json",
                   "--verbose", "--tools", "Skill", "--allowedTools", "Skill mcp__cua-driver"]
    workdir = os.path.join(out, "workdir")
    os.makedirs(workdir)
    with open(os.path.join(out, "command.json"), "w") as f:
        json.dump({"command": command, "home": home}, f, indent=2)
    stream_path = os.path.join(out, "agent.jsonl")
    login = auth_in(home) if harness == "codex" else None
    with open(stream_path, "w") as stream, open(os.path.join(out, "agent.stderr"), "w") as err:
        proc = subprocess.Popen(command, cwd=workdir, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=err, text=True, start_new_session=True)
        def read():
            # Each line of the agent's stream with the time it arrived, as the laptop's runners keep it.
            for line in proc.stdout:
                stream.write(json.dumps({"time": time.time(), "event": parse(line)}) + "\n")
                stream.flush()

        reader = threading.Thread(target=read, daemon=True)
        reader.start()
        try:
            wait_agent(proc, timeout)
        finally:
            reader.join(5)
            if login is not None:
                auth_out(home, login)
    events = [e["event"] for e in read_jsonl(stream_path)]
    if harness == "codex":
        thread = next((e.get("thread_id") for e in events if e.get("type") == "thread.started"), None)
        pattern = os.path.join(home, ".codex", "sessions", "*", "*", "*", f"rollout-*{thread}.jsonl")
    else:
        thread = next((e.get("session_id") for e in events
                       if e.get("type") == "system" and e.get("subtype") == "init"), None)
        pattern = os.path.join(home, ".claude", "projects", "*", f"{thread}.jsonl")
    paths = glob.glob(pattern) if thread else []
    if not paths:
        return None, False, []
    if harness == "codex":
        done, entries = codex_finished(paths[0])
    else:
        entries = read_jsonl(paths[0])
        done = any(e.get("type") == "result" and not e.get("is_error") for e in events)
    return paths[0], done, entries


def service_tier(out):
    """The service tier of a Codex run, from the session configuration that codex_exec logs: "standard" when the
    session asked for none, None when the log does not say."""
    path = os.path.join(out, "agent.stderr")
    if not os.path.exists(path):
        return None
    for line in open(path, errors="replace"):
        if "SessionConfiguredEvent" in line:
            found = re.search(r"service_tier: (None|Some\(\"([^\"]*)\"\))", line)
            if found:
                return found.group(2) or "standard"
    return None


def parse(line):
    try:
        return json.loads(line)
    except ValueError:
        return {"raw": line.rstrip("\n")}


class ClaudeApp:
    """Claude's computer use in the Claude app, driven without a person: a new Code session in the bench's folder,
    the prompt pasted into it and sent, and the app's grant of the task's app answered, through the app's
    accessibility tree with macOS's own API (AXApp). No computer-use tool takes part: Cua Driver's daemons keep a
    window of their own over the screen, which Claude's full control cannot hide, so they do not run during these
    runs. Every prompt of the app that the runner answers is logged to claude-app.log."""

    BUNDLE = "com.anthropic.claudefordesktop"

    def __init__(self, out):
        self.log = open(os.path.join(out, "claude-app.log"), "a", buffering=1)
        self.ax = None

    def note(self, text):
        self.log.write(f"{time.time():.2f} {text}\n")

    def open_session(self, model, effort):
        """A new Code session in the bench's folder, with the model and effort the run asks for, or SystemExit."""
        # macOS's accessibility API, from PyObjC in the questionnaire's Python, only where a run drives the app.
        from ax_app import AXApp
        folder = os.path.join(BENCH, "claude-cu")
        subprocess.run(["open", "-a", "Claude"], check=True)
        self.ax = wait_for("the Claude app", lambda: AXApp(self.BUNDLE), timeout=30)
        # The sidebar's new session in the bench's folder, once the app knows the folder. claude --desktop, which
        # makes it known, opens its new session only from a terminal, which script gives it.
        if not self.ax.press(r"^AXButton \(New session in claude-cu\)", timeout=5):
            subprocess.run(["script", "-q", "/dev/null", CLAUDE, "--desktop"], cwd=folder, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        deadline = time.time() + 30
        while True:
            # The window of the new session: its folder is the bench's and it has a prompt field. All the runner
            # reads, pastes and grants after this stays within it, never in another window of the app.
            self.ax.scope = None
            tree = ""
            for window in self.ax.windows():
                self.ax.scope = window
                tree = self.ax.tree()
                if re.search(r"AXPopUpButton \(Folder: ~/hyprmasq-bench/claude-cu\)", tree) \
                        and re.search(r"AXTextArea .*\(Prompt\)", tree):
                    break
            else:
                self.ax.scope = None
            if self.ax.scope is not None:
                break
            # The app asks to trust a folder new to it. The runner trusts only in a window whose prompt names the
            # bench's folder, never another workspace.
            for window in self.ax.windows():
                self.ax.scope = window
                tree = self.ax.tree()
                if re.search(CLAUDE_APP_TRUST[1:], tree) and f'"{folder}"' in tree:
                    self.note("trusted the bench's folder")
                    self.ax.press(CLAUDE_APP_TRUST)
                    break
            self.ax.scope = None
            if time.time() > deadline:
                raise SystemExit("no new session of the Claude app in the bench's folder")
            time.sleep(1)
        shown = (re.search(r"AXPopUpButton \(Model: ([^)]*)\)", tree) or [None, None])[1]
        wanted = f"{CLAUDE_APP_MODELS.get(model, model)} {effort.capitalize()}"
        if shown != wanted:
            raise SystemExit(f"the Claude app's session runs {shown!r}, not {wanted!r}: choose it in the app")
        self.note(f"new session, model {shown}")
        return shown

    def send(self, text):
        """Pastes the prompt into the session's prompt field and sends it: typed, its blank lines would send it
        early."""
        subprocess.run(["pbcopy"], input=text, text=True, check=True)
        if not self.ax.focus(r"^AXTextArea .*\(Prompt\)"):
            raise SystemExit("no prompt field in the Claude app")
        time.sleep(0.3)
        self.ax.key("v", command=True)
        wait_for("the prompt in the Claude app's field", lambda: "Mara Lindqvist" in self.ax.tree(), timeout=10)
        self.ax.key("return")
        self.note("sent the prompt")

    def cancel(self):
        """Stops a session that did not finish, before the task's app closes under it: its stop button where the app
        shows one, and Escape, which interrupts Claude Code, in the session's window."""
        if self.ax is None or self.ax.scope is None:
            return
        stop = r'^AXButton ("Stop"|\((Stop|Interrupt)[^)]*\))'
        if self.ax.press(stop, timeout=1):
            self.note("pressed stop")
        elif self.ax.raise_scope():
            # Keys go to the app's process, and so to its main window: the session's, raised.
            self.ax.key("escape")
            self.note("sent escape to the session's window")
        else:
            self.note("could not raise the session's window to stop it")
        # The session has stopped once its window shows no stop button, within 10 s.
        deadline = time.time() + 10
        while self.ax.find(stop) is not None and time.time() < deadline:
            time.sleep(0.5)
        self.note("session stopped" if self.ax.find(stop) is None else "session still shows its stop button")

    def answer(self):
        """Grants the task's app, or full control of the screen, when Claude asks in the session's window, as the
        person at the app would."""
        element = self.ax.find(CLAUDE_APP_GRANT)
        if element is not None:
            self.note(f"granted: {next(row for row, e in self.ax.elements() if e == element)}")
            self.ax.press(CLAUDE_APP_GRANT, timeout=2)

    def close(self):
        self.log.close()


def wait_app_session(tool, known, ready, timeout, while_waiting=None):
    """The session a person starts by hand in the tool's app, or the runner in the Claude app: its transcript's
    path, whether it finished, and its entries. while_waiting runs every 2 s until then."""
    find, finished = (codex_session, codex_finished) if tool == "openai-cu" else (claude_session, claude_finished)
    session = None
    deadline = ready + timeout
    while time.time() < deadline:
        session = session or find(known, ready)
        if session and finished(session)[0]:
            break
        if while_waiting:
            while_waiting()
        time.sleep(2)
    done, entries = finished(session) if session else (False, [])
    return session, done, entries


def task_times(entries, done):
    """The rollout's task bounds, when it recorded them. A failed startup may have only session metadata."""
    stamps = [e for e in entries if (e.get("payload") or {}).get("type") in ("task_started", "task_complete")]
    start = next((e["timestamp"] for e in stamps if e["payload"]["type"] == "task_started"), None)
    end = next((e["timestamp"] for e in reversed(stamps) if e["payload"]["type"] == "task_complete"), None) \
        if done else None
    return start, end


def claude_times(entries, done):
    """A Claude Code session's bounds: its first message from the person, and the reply that ended its turn."""
    start = next((e["timestamp"] for e in entries if user_text(e)), None)
    end = next((e["timestamp"] for e in reversed(entries) if e.get("type") == "assistant"), None) if done else None
    return start, end


def foreign_tools(entries):
    """The tools other than Claude's computer use that a claude-cu session called, which fail its run as the
    other tools' shells are off."""
    names = set()
    for e in entries:
        if e.get("type") != "assistant":
            continue
        for block in (e.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name", "")
            if name == "Skill" and (block.get("input") or {}).get("skill") == CLAUDE_CU_SKILL:
                continue
            if not name.startswith(CLAUDE_CU_TOOLS):
                names.add(name if name != "Skill" else f'Skill {(block.get("input") or {}).get("skill")}')
    return sorted(names)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=sorted(TASKS), default="form")
    parser.add_argument("--tool", choices=["openai-cu", "cua", "claude-cu"], required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--count", type=int, default=25, help="the questionnaire's number of questions")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--via", choices=["app", "exec", "drive"], default=None,
                        help="exec: the runner starts the agent; app: a person starts it in the tool's app. "
                             "Default: exec for cua, app for the others")
    parser.add_argument("--harness", choices=["codex", "claude"], default="codex",
                        help="Cua's agent with --via exec")
    parser.add_argument("--model", default=None, help="default gpt-6.1-sol, or claude-sonnet-5-5 for Claude Code")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--no-dropdowns", action="store_true", help="the task form-no-dropdowns")
    parser.add_argument("--no-agent", action="store_true",
                        help="set the task up, grade it untouched and clean up, without an agent: a dry run")
    args = parser.parse_args()
    task_name = "form-no-dropdowns" if args.no_dropdowns else args.task
    via = args.via or {"cua": "exec", "claude-cu": "drive"}.get(args.tool, "app")
    if (via in ("app", "drive") and (args.tool, via) not in USER_APPS) \
            or (via == "exec" and (args.tool, args.harness) not in HOMES):
        raise SystemExit(f"{args.tool} does not run with --via {via} --harness {args.harness}")
    harness = args.harness if via == "exec" else ("codex" if args.tool == "openai-cu" else "claude")
    model = args.model or ("claude-sonnet-5-5" if harness == "claude" else "gpt-6.1-sol")
    user_app = USER_APPS.get((args.tool, via), "TextEdit")

    # Absolute, as Finder and Inkscape name the run's files by their full paths.
    out = os.path.abspath(os.path.join(args.out, f"{args.tool}-{args.rep}"))
    os.makedirs(out)
    task = TASKS[task_name](task_name, out, args.count)
    known = rollouts() | claude_transcripts()
    windows = None
    claude_app = None
    try:
        task.start()
        if user_app == "TextEdit":
            # The user's window: a note in TextEdit, which stays in front.
            note = os.path.join(BENCH, "user-notes.txt")
            open(note, "a").close()
            subprocess.run(["open", "-a", "TextEdit", note], check=True)
        elif via == "drive":
            # Cua Driver's daemons each keep a window over the whole screen for its cursor, which Claude's full control
            # of the screen cannot hide: Claude stops at it, so its runs need them stopped.
            if app_windows("Cua Driver"):
                raise SystemExit("Cua Driver's daemons run, whose window over the screen blocks Claude's full control: "
                                 "stop them with pkill -f 'CuaDriver.app/Contents/MacOS/cua-driver serve'")
            claude_app = ClaudeApp(out)
            claude_app.open_session(model, args.effort)
        else:
            subprocess.run(["open", "-a", user_app], check=True)
        # The task app, opened last, may hold the front for a moment: the run starts with the user's app there.
        deadline = time.time() + 20
        while front() != user_app:
            if time.time() > deadline:
                raise SystemExit(f"{user_app} did not come to the front")
            osascript(f'tell application "{user_app}" to activate')
            time.sleep(1)
        time.sleep(2)
        ready = time.time()
        log = FrontLog(os.path.join(out, "front.jsonl"))
        log.start()
        # The task app's windows on screen, popups too, which the frontmost app alone does not show.
        windows = subprocess.Popen([QT_PYTHON, os.path.join(FORM, "mac_windows.py"), task.app, user_app,
                                    os.path.join(out, "windows.jsonl")])
        if args.no_agent:
            time.sleep(3)
            session, done, entries = None, True, []
        elif via == "exec":
            session, done, entries = run_exec(args.tool, harness, task_name, model, args.effort, out, args.timeout)
        elif via == "drive":
            claude_app.send(prompt(task_name))
            session, done, entries = wait_app_session(args.tool, known, ready, args.timeout, claude_app.answer)
            if not done:
                claude_app.cancel()
        else:
            app_name = "the ChatGPT app" if args.tool == "openai-cu" else \
                f"a Code session of the Claude app in {os.path.join(BENCH, 'claude-cu')}"
            print(f"Ready: {task.app} is open behind {user_app}. Start a new session in {app_name} with this "
                  f"prompt:\n\n{prompt(task_name)}\n", flush=True)
            session, done, entries = wait_app_session(args.tool, known, ready, args.timeout)
        log.stop = True
        windows.terminate()
        print(f"Session: {session}, finished: {done}", flush=True)

        start = end = None
        if entries:
            start, end = (task_times if harness == "codex" else claude_times)(entries, done)
            with open(os.path.join(out, "transcript.jsonl"), "w") as f:
                f.writelines(json.dumps(e) + "\n" for e in entries)
        since = parse_time(start) if start else ready
        check = task.grade(since)
        agent_end = parse_time(end) if end else time.time()
        others = other_apps(os.path.join(out, "front.jsonl"), since, agent_end, (user_app, task.app))
        failures = check["failures"] + [f"an app other than {task.app} came to the front: {a}" for a in others]
        if args.tool == "claude-cu":
            failures += [f"a tool other than computer use: {t}" for t in foreign_tools(entries)]
        # The OpenAI runs use the standard tier, as the comparison's other runs do: priority processing is faster.
        tier = service_tier(out) if via == "exec" and harness == "codex" and not args.no_agent else None
        # A Codex run that the runner starts passes only on the standard tier in its log. A session started by hand
        # in the ChatGPT app leaves no such log: its tier is not checked.
        if via == "exec" and harness == "codex" and not args.no_agent and tier != "standard":
            failures.append(f"service tier {tier or 'not in the log'}, not the standard tier")
        harness_name = {("openai-cu", "app"): "codex-desktop", ("claude-cu", "app"): "claude-desktop",
                        ("claude-cu", "drive"): "claude-desktop"}.get(
            (args.tool, via), f"{harness}-exec")
        summary = {
            "backend": args.tool, "rep": args.rep, "task": task_name, **task.record(), "platform": "macos",
            "harness": harness_name, "via": via, "model": model, "effort": args.effort, "service_tier": tier,
            "session": session, "finished": done,
            "agent": {"start": since, "end": agent_end, "timed_out": not done},
            "user_app": user_app, "task_app": task.app, "passed": check["passed"] and not failures,
            "failures": failures, "check": check,
        }
        summary["wall_seconds"] = round(agent_end - since, 1)
        with open(os.path.join(out, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps({k: summary[k] for k in ("backend", "task", "rep", "wall_seconds", "passed", "failures")}),
              flush=True)
    finally:
        # Also when the wait for the session is interrupted, which leaves the window log running otherwise.
        if windows and windows.poll() is None:
            windows.terminate()
        if claude_app:
            # Also when the run was interrupted while the session ran: it stops before the task's app closes.
            if sys.exc_info()[0] is not None:
                claude_app.cancel()
            claude_app.close()
        task.stop()


def parse_time(stamp):
    """ISO timestamps, such as 2026-10-22T03:12:32.559Z, as Unix time."""
    from datetime import datetime, timezone
    return datetime.strptime(stamp.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").astimezone(timezone.utc).timestamp()


if __name__ == "__main__":
    main()
