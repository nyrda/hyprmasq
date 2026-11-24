#!/usr/bin/env python3
"""Runs a benchmark task: one fresh VM per run, an agent on the host, grading, and a debrief.

    run.py [--agent HARNESS:MODEL[@EFFORT]]... [--reps N | --rep N...] [--out DIR] [--timeout SECONDS] [--cid CID]
           [--cpus LIST] [--lock FILE] [--no-debrief] [--backend hyprmasq|cua|hypruse|computer-use-linux]
           [--prompt task|comparison]
           [--browser firefox|chromium]

HARNESS is claude (Claude Code), codex (Codex CLI) or agy (Antigravity CLI), which use the logins of the user running
this; claude-api (Claude Code billed to the user's Anthropic API key); or pi, with MODEL as PROVIDER/MODEL of the user's
pi setup.
EFFORT is the reasoning effort the harness runs the model at, one of the harness's levels in EFFORTS; without it,
the harness's default. Each run gets RESULTS/<harness>-<model>[-<effort>]-<rep>/, with any / in the model as _,
holding the agent's event stream, the MCP log, screenshots, the VM's logs, the grade, the debrief and summary.json.
RESULTS/report.md compares the runs. --backend gives the agent another desktop tool in place of hyprmasq, set up as its
documentation sets it up: cua, Cua Driver with its skill; hypruse; or computer-use-linux with its skill. --prompt
comparison gives the tasks' tool-neutral prompts (prompt-comparison.md), which every backend takes in a comparison.
--browser chromium gives the user Chromium in place of Firefox in the tasks that list it in tasks/TASK/browsers.
See bench/README.md.
"""

import argparse
import contextlib
import datetime
import fcntl
import functools
import glob
import hashlib
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
DEFAULT_AGENTS = ["codex:gpt-6.1-sol", "codex:gpt-6.1-terra", "codex:gpt-6-astra",
                  "claude:claude-opus-5-5", "claude:claude-sonnet-5-5"]

HARNESSES = ("agy", "claude", "claude-api", "codex", "pi")
# Harnesses that use API keys of this machine: dispatch.py sends their runs to another one only for claude-api, whose
# key it passes to a machine named with api-keys; pi reads its keys from its files.
LOCAL_ONLY = ("claude-api", "pi")
# The reasoning efforts each harness takes for @EFFORT: Claude Code's --effort, the levels that Codex passes on as
# model_reasoning_effort and the OpenAI API accepts, pi's --thinking, and agy's --effort, which picks the model's variant
# of that level, such as gemini-4-neon-low for gemini-4-neon.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORTS = {
    "claude": CLAUDE_EFFORTS, "claude-api": CLAUDE_EFFORTS,
    "codex": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "pi": ("off", "minimal", "low", "medium", "high", "xhigh", "max"),
    "agy": ("low", "medium", "high", "xhigh", "max"),
}
# Where claude-api's key and request headers come from when the environment does not give them.
API_KEY_ENV, API_HEADERS_ENV = "HYPRMASQ_BENCH_ANTHROPIC_API_KEY", "HYPRMASQ_BENCH_ANTHROPIC_HEADERS"

# The desktop tools an agent can get: hyprmasq, or another as its documentation sets it up: Cua Driver (bench/cua),
# hypruse (bench/hypruse) or computer-use-linux (bench/computer-use-linux).
BACKENDS = ("hyprmasq", "cua", "hypruse", "computer-use-linux")
# The MCP server's name each backend has for the agent, as its documentation registers it: Cua's table of harnesses
# names it cua-driver (docs/content/docs/cua-driver/guides/connect-your-agent.mdx), as the laptop's runs did; hypruse's
# README `claude mcp add -s user hypruse -- uvx hypruse`; computer-use-linux's README and plugin computer-use-linux.
SERVER = {"hyprmasq": "hyprmasq", "cua": "cua-driver", "hypruse": "hypruse", "computer-use-linux": "computer-use-linux"}
# What the debrief calls the tool.
TOOL_NAME = {"hyprmasq": "hyprmasq", "cua": "Cua Driver", "hypruse": "hypruse",
             "computer-use-linux": "computer-use-linux"}
# The backends whose agent skill the agent gets, the skill's name, and the Nix file under bench that builds it (its
# attribute skills). hypruse's skill teaches its shell verbs, to agents that run commands instead of MCP, and an agent
# here has its MCP server and no shell.
SKILLS = {"cua": ("cua-driver", "cua"), "computer-use-linux": ("computer-use-linux", "computer-use-linux")}
# The prompts of a task: its own, prompt.md, which names hyprmasq and asks not to disturb the user, or the
# tool-neutral one of a comparison, prompt-comparison.md, with the laptop's wording: the user has authorized foreground
# input and focus changes if the tools need them, and prefers ways that leave their window alone.
PROMPTS = {"task": "prompt.md", "comparison": "prompt-comparison.md"}
# The user's browser in the tasks that have one: Firefox, the default, or Chromium, for a tool that does more there, as
# Cua Driver, whose foreground input refuses Firefox on Hyprland. A task lists the browsers it takes in tasks/TASK/browsers.
BROWSERS = ("firefox", "chromium")
# The VM's desktop: bare, as the published benchmarks ran it, or omarchy, with Omarchy's key binds, its portals and the
# packages the tools reach for, and the task's windows on numbered workspaces (default.nix, vm.py).
DESKTOPS = ("bare", "omarchy")
# How long one tool call may take, in seconds, for every harness and backend: Codex's tool_timeout_sec, Claude Code's
# MCP_TOOL_TIMEOUT and pi's MCP timeout.
TOOL_TIMEOUT = 600
# How the agent is told to use the backend's skill at the start of the prompt, as the laptop's runs did with Cua's:
# Codex injects an installed skill's SKILL.md when the prompt names it, and Claude Code runs a skill named as a slash
# command.
SKILL_MENTION = {"codex": "${skill} ", "claude": "/{skill} "}
# pi and agy run only with hyprmasq: Cua documents pi with its CLI and computer-use-linux with pi's native tools, not
# their MCP servers, the other backends' skills are not set up for agy, and the comparisons run Claude Code and Codex.
HYPRMASQ_ONLY = ("agy", "pi")


def hyprmasq_only(harness):
    return f"{harness} runs only with hyprmasq: the comparisons with other tools run Claude Code and Codex"

# Codex features that would give the agent tools besides hyprmasq's, or load the user's own setup.
CODEX_DISABLED = [
    "shell_tool", "unified_exec", "hooks", "computer_use", "browser_use", "browser_use_external", "in_app_browser",
    "apps", "plugins", "multi_agent", "image_generation", "memories", "goals", "tool_suggest", "skill_search",
    "workspace_dependencies",
]


# What Claude Code gets beside a backend's server when that backend has a skill.
SKILL_TOOLS = ("Skill", "ListMcpResourcesTool", "ReadMcpResourceTool")
# What Claude Code brings of its own, turned off for every backend: the skills and plugins it bundles, which
# CLAUDE_CODE_DISABLE_BUNDLED_SKILLS (agent_env) leaves these of.
CLAUDE_SETTINGS = {
    "skillOverrides": {"design": "off", "doctor": "off", "plugin-authoring": "off"},
    "enabledPlugins": {f"cc-plugin-{name}@builtin": False
                       for name in ("agents-md", "telemetry", "privacy", "plugin-authoring")},
}


def build_driver(out, source, task, variant, display, backend="hyprmasq", browser="firefox", desktop="bare",
                 container=False):
    """Builds the VM's (or container's) test driver into OUT/driver, and with a backend that has a skill (SKILLS) the
    skill into OUT/BACKEND-skill."""
    link = os.path.join(out, "driver")
    command = ["nix-build", os.path.join(BENCH, "default.nix"), "-A", "driver", "-o", link, "--argstr", "task", task,
               "--argstr", "variant", variant or "", "--argstr", "display", display, "--argstr", "backend", backend,
               "--argstr", "browser", browser, "--argstr", "desktop", desktop]
    if container:
        command += ["--arg", "container", "true"]
    if source:
        command += ["--arg", "source", source]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, env=without_api_keys(os.environ))
    if backend in SKILLS:
        subprocess.run(["nix-build", os.path.join(BENCH, SKILLS[backend][1]), "-A", "skills", "-o",
                        skill_link(out, backend)], check=True, stdout=subprocess.DEVNULL,
                       env=without_api_keys(os.environ))
    return link


def skill_link(out, backend="cua"):
    return os.path.join(out, f"{backend}-skill")


def variants_optional(task):
    """Whether a task with variants also runs without one: a line of its variants file is `NAME optional`."""
    path = os.path.join(BENCH, "tasks", task, "variants")
    if not os.path.exists(path):
        return False
    with open(path) as f:
        return any(line.split()[1:] == ["optional"] for line in f)


def task_variants(task):
    """The variants a task has, from tasks/TASK/variants, one a line, and its default, the one whose line is
    `NAME default`, or None; no variants for most tasks."""
    path = os.path.join(BENCH, "tasks", task, "variants")
    if not os.path.exists(path):
        return [], None
    with open(path) as f:
        lines = [line.split() for line in f if line.strip()]
    return [line[0] for line in lines], next((line[0] for line in lines if line[1:] == ["default"]), None)


def source_commit(source):
    """The commit of the hyprmasq checkout the VM runs, and whether it had changes."""
    checkout = source or os.path.dirname(BENCH)
    def run(*args):
        return subprocess.run(["git", "-C", checkout, *args], capture_output=True, text=True).stdout.strip()

    return {"path": checkout, "commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def executable(harness):
    return "claude" if harness == "claude-api" else harness


def family(harness):
    """The harness whose event stream and transcripts a harness has: claude-api's are Claude Code's."""
    return "claude" if harness == "claude-api" else harness


def parse_agent(spec):
    """HARNESS:MODEL[@EFFORT] as (harness, model, effort), effort None for the harness's default. The effort follows
    the last @, since pi's models have / and : in them."""
    harness, _, rest = spec.partition(":")
    model, at, effort = rest.rpartition("@") if "@" in rest else (rest, "", None)
    if harness not in HARNESSES:
        raise ValueError(f"unknown harness {harness!r} in {spec}: one of {', '.join(HARNESSES)}")
    if not model:
        raise ValueError(f"{spec} is not HARNESS:MODEL[@EFFORT]")
    if at and effort not in EFFORTS[harness]:
        raise ValueError(f"{harness} takes no effort {effort!r} in {spec}: one of {', '.join(EFFORTS[harness])}")
    return harness, model, effort


def agent_spec(harness, model, effort=None):
    return f"{harness}:{model}" + (f"@{effort}" if effort else "")


def run_name(harness, model, rep, effort=None):
    """The run's directory: a pi model such as tensorx/deepseek/... has slashes, which a name cannot."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", f"{harness}-{model}-" + (f"{effort}-" if effort else "") + str(rep))


def version(command):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30,
                              env=without_api_keys(os.environ)).stdout.strip()
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


def parse_cpus(text):
    """The CPUs of a list such as 4-5,12-13, as taskset -c and the kernel write them."""
    cpus = set()
    for part in text.split(","):
        first, sep, last = part.partition("-")
        if not first.isdigit() or (sep and not last.isdigit()) or int(last if sep else first) < int(first):
            raise ValueError(f"{text!r} is not a list of CPUs such as 4-5,12-13")
        cpus.update(range(int(first), int(last if sep else first) + 1))
    return sorted(cpus)


def format_cpus(cpus):
    """A list of CPUs as parse_cpus reads it, with runs of CPUs as ranges."""
    parts, cpus = [], sorted(cpus)
    for cpu in cpus:
        if parts and parts[-1][1] == cpu - 1:
            parts[-1][1] = cpu
        else:
            parts.append([cpu, cpu])
    return ",".join(str(a) if a == b else f"{a}-{b}" for a, b in parts)


def physical_cores(siblings):
    """The machine's physical cores, each the tuple of its CPUs, from the lines of the CPUs'
    topology/thread_siblings_list: an SMT machine has two CPUs a core, such as 0,8."""
    return sorted({tuple(parse_cpus(line.strip())) for line in siblings if line.strip()})


def read_cores():
    """This machine's physical cores, as physical_cores gives them."""
    lines = []
    for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/topology/thread_siblings_list"):
        with open(path) as f:
            lines.append(f.read())
    return physical_cores(lines)


def slot_cpus(cores, slots, per_slot):
    """The CPUs of each of a machine's slots, given its physical cores: per_slot whole cores each, with their SMT
    siblings, from the last cores down, so that the first cores, and the ones no slot takes, stay to the agents and
    the host. Refuses slots that would take every core."""
    if per_slot < 1:
        raise ValueError("a slot needs at least one core")
    if slots * per_slot >= len(cores):
        raise ValueError(f"{slots} slots of {per_slot} cores take all {len(cores)} cores of the machine, leaving "
                         "none to the agents and the host: give fewer slots or cores")
    return [sorted(cpu for core in cores[len(cores) - (k + 1) * per_slot:len(cores) - k * per_slot] for cpu in core)
            for k in range(slots)]


def tap_command(cid, log, max_calls=0, spent=0):
    """The MCP tap between the agent and the VM's server, with a budget of tool calls when max_calls is above 0, of
    which spent were made before, through an earlier tap of the run."""
    return [sys.executable, os.path.join(BENCH, "mcp_tap.py"), str(cid), "5000", log] + (
        [str(max_calls)] + ([str(spent)] if spent else []) if max_calls > 0 else [])


# What the user answers, with --user-reply, an agent that ended its turn without saying "Done", which every task's
# prompt asks for at the end: such an agent stopped to ask the user something, such as whether to grant an access the
# task asks for, or gave up. The user answers once, as a user would who is there, and the run goes on in the agent's
# session with its tools.
USER_REPLY = "Yes, go ahead."
DONE = re.compile(r"\bDone\b")
NOT_DONE = re.compile(
    r"""(?:(?:not(?:\s+(?:yet|saying))?|isn['’]t(?:\s+yet)?|is\s+not(?:\s+yet)?|aren['’]t|are\s+not|wasn['’]t|haven['’]t(?:\s+said)?|(?:won['’]t|can['’]t|didn['’]t|cannot|(?:will|do|did)\s+not)\s+say|without\s+saying|before\s+saying|not\s+marking\s+[^.!?\n]*?)\s+["'“‘`*_]*Done\b|["'“‘`*_]*\bDone["'”’`*_]*\s+(?:doesn['’]t|does\s+not)\s+apply)""",
    re.IGNORECASE,
)


def needs_reply(agent, final):
    """Whether the user answers the agent: it ended its turn by itself, without saying "Done"."""
    cleaned = NOT_DONE.sub("", final or "")
    return (agent["exit"] == 0 and not agent["timed_out"] and not agent["budget_exhausted"] and not agent["refused"]
            and bool(agent["session"]) and not DONE.search(cleaned))


def claude_command(model, prompt, tap, resume=None, effort=None, backend="hyprmasq", read_dir=None, tap_env=None,
                   tool_search=True):
    server = SERVER[backend]
    # A backend's skill (Cua's or computer-use-linux's) runs through Claude Code's Skill tool, from its own agent home
    # (skill_home), when the prompt names it as a slash command, as on the laptop; the debrief gets no tools. Claude Code
    # reads a user's skills only with the user's settings, which in that fresh home are the skill alone.
    skill = backend in SKILLS and tap is not None
    read_only_dir = read_dir if tap is not None else None
    # ToolSearch is Claude Code's built-in deferred MCP tool loader (on by default under --tools default); omitting it
    # (--no-tool-search) forces Claude Code to send every MCP tool schema eagerly on every request.
    tools = ["ToolSearch"] if tap is not None and tool_search else []
    if skill:
        tools += list(SKILL_TOOLS)
    if read_only_dir and "Read" not in tools:
        tools.append("Read")
    command = [
        "claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
        # Only ToolSearch (unless --no-tool-search) and the backend's tools, and none of the user's CLAUDE.md,
        # settings, hooks, skills or MCP servers. With a skill, the resource tools as well, as a skill's guides are
        # files that a real agent reads: here they are the server's resources, for a server that has them. With
        # read_dir (hypruse file mode in a container), Read scoped strictly to the shared screenshot directory.
        "--tools", ",".join(tools), "--setting-sources", "user" if skill else "",
        "--strict-mcp-config", "--settings", json.dumps(CLAUDE_SETTINGS),
    ]
    if read_only_dir:
        command += ["--add-dir", read_only_dir]
    if not skill:
        command += ["--disable-slash-commands"]
    if effort:
        command += ["--effort", effort]
    if resume:
        command += ["--resume", resume]
    # The run's tools, also when the agent goes on in its session after the user's reply; the debrief gets none.
    if tap:
        srv = {"command": tap[0], "args": tap[1:]}
        if tap_env:
            srv["env"] = tap_env
        config = {"mcpServers": {server: srv}}
        allowed = ["ToolSearch"] if tool_search else []
        if skill:
            allowed += list(SKILL_TOOLS)
        if read_only_dir:
            allowed.append(f"Read({read_only_dir}/*)")
        allowed.append(f"mcp__{server}")
        command += ["--mcp-config", json.dumps(config), "--allowedTools", " ".join(allowed)]
    return command


def codex_command(model, prompt, tap, resume=None, effort=None, backend="hyprmasq"):
    server = SERVER[backend]
    command = ["codex", "exec"]
    if resume:
        command += ["resume", resume]
    command += [
        "--json", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-m", model,
        "-c", 'web_search="disabled"',
        # Its own system skills stay out, for every backend, and it looks for no newer version.
        "-c", "skills.bundled.enabled=false", "-c", "check_for_update_on_startup=false",
    ]
    # Without a backend's skill, no list of skills at all. With one, the agent home has that skill alone (agent_home).
    if backend not in SKILLS:
        command += ["-c", "skills.include_instructions=false"]
    if effort:
        command += ["-c", f"model_reasoning_effort={json.dumps(effort)}"]
    if not resume:
        command += ["--sandbox", "read-only"]
    elif tap:
        # exec resume takes no --sandbox: the same sandbox through the configuration.
        command += ["-c", 'sandbox_mode="read-only"']
    for feature in CODEX_DISABLED:
        command += ["--disable", feature]
    # The run's tools, also when the agent goes on in its session after the user's reply; the debrief gets none.
    if tap:
        command += [
            "-c", f"mcp_servers.{server}.command={json.dumps(tap[0])}",
            "-c", f"mcp_servers.{server}.args={json.dumps(tap[1:])}",
            "-c", f"mcp_servers.{server}.tool_timeout_sec={TOOL_TIMEOUT}",
            # In the read-only sandbox, Codex asks to approve every tool without readOnlyHint, and exec cannot ask.
            "-c", f'mcp_servers.{server}.default_tools_approval_mode="approve"',
        ]
    return command + [prompt]


def pi_command(model, prompt, tap, resume=None, sessions=None, effort=None):
    """pi with only hyprmasq's tools. Its agent directory, PI_CODING_AGENT_DIR, is the run's own (pi_agent_dir),
    so that none of the user's settings, MCP servers, extensions, skills or instructions load."""
    command = [
        "pi", "--mode", "json", "-p", "--model", model, "--offline", "--no-approve",
        # The built-in MCP extension alone: --no-extensions drops it with the others, and -e loads it again.
        "--no-extensions", "--no-skills", "--no-context-files", "--no-prompt-templates", "--no-themes",
    ]
    if effort:
        command += ["--thinking", effort]
    if resume and tap:
        # The agent going on in its session after the user's reply, with its tools.
        command += ["--session", resume, "-e", "builtin:mcp", "--tools", "mcp__hyprmasq__*"]
    elif resume:
        # The debrief: the session's file, no tools, and no MCP server to give any.
        command += ["--session", resume, "--no-tools"]
    else:
        command += ["-e", "builtin:mcp", "--tools", "mcp__hyprmasq__*", "--session-dir", sessions]
    if prompt.startswith("@"):
        raise ValueError("pi would take a prompt starting with @ for a file")
    return command + ["--", prompt]


def pi_agent_dir(tap, sessions):
    """A pi agent directory for one run: the user's credentials, models and model catalog, linked and not copied,
    and the tap as the only MCP server, its tools declared to the model directly. Without the catalog that
    `pi update` keeps, pi has only the one it was released with, which may lack the model asked for."""
    agent_dir = tempfile.mkdtemp(prefix="hyprmasq-bench-pi-")
    user = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")
    for name in ("auth.json", "models.json", "models-store.json"):
        if os.path.exists(os.path.join(user, name)):
            os.symlink(os.path.join(user, name), os.path.join(agent_dir, name))
    pi_mcp_config(agent_dir, tap)
    with open(os.path.join(agent_dir, "settings.json"), "w") as f:
        json.dump({"defaultProjectTrust": "never"}, f)
    os.makedirs(sessions)
    return agent_dir


def pi_mcp_config(agent_dir, tap):
    """The tap as pi's only MCP server, its tools declared to the model directly. pi starts its MCP servers from this
    file, not from its command, so the tap that the agent gets after the user's reply goes here too."""
    config = {
        "autoEnableCodemode": False,
        "mcpServers": {"hyprmasq": {"command": tap[0], "args": tap[1:], "exposure": "direct", "timeout": TOOL_TIMEOUT}},
    }
    with open(os.path.join(agent_dir, "mcp.json"), "w") as f:
        json.dump(config, f, indent=2)


def check_pi_model(agent_dir, model):
    """Refuses a model that pi does not list under exactly this PROVIDER/ID. pi matches --model loosely, and would
    run another model of the provider, such as one ID with a suffix, in place of one it does not have."""
    provider, _, ident = model.partition("/")
    listed = subprocess.run(["pi", "--list-models", ident], capture_output=True, text=True, timeout=60,
                            env={**os.environ, "PI_CODING_AGENT_DIR": agent_dir}, stdin=subprocess.DEVNULL)
    if not any(line.split()[:2] == [provider, ident] for line in listed.stdout.splitlines()):
        raise RuntimeError(f"pi lists no model {model}: run `pi update` for its catalog, or name another model")


# The Antigravity CLI's agent for a run: a custom agent of the run's agent home (agy_home), its main agent, with agy's
# default prompt and none of its built-in tools but read_file. agy hands an MCP tool's images to the model only as files
# it saves in the conversation's directory, "[Resource offloaded to file://...]", which the agent opens with read_file;
# without it the agent sees no screenshot. agy lets it read that directory and the run's empty working directory, and
# denies the rest in print mode, which ends the run. Of its other tools manage_task stays, which agy gives every agent
# that keeps its default prompt, for background tasks that it cannot start here. The server's tools are eager, given
# to the model directly as mcp_SERVER_TOOL, as Claude Code's and Codex's are; agy's default puts them behind its
# call_mcp_tool. Its MCP tool timeout is timeoutSeconds.
AGY_AGENT = "hyprmasq-bench"
AGY_AGENT_FILE = f"""---
name: {AGY_AGENT}
description: The hyprmasq benchmark's agent, with the desktop tool's MCP server and read_file for its images.
mainAgent: true
subagent: false
inheritMcp: true
tools: [read_file]
---
# {AGY_AGENT}
"""
# The login that `agy acp --login` keeps in a file, which a run's home links as Codex's links auth.json. agy reads
# the system keyring as well, through D-Bus, which a home of its own does not change.
AGY_DATA = os.path.join(".gemini", "antigravity-cli")
AGY_TOKEN = os.path.join(AGY_DATA, "antigravity-oauth-token")


@functools.lru_cache(maxsize=None)
def agy_executable():
    """agy's own binary. mise's shim would install agy again into the run's HOME, where mise keeps its tools, as
    Omarchy's mise setup does."""
    path = shutil.which("agy")
    if not path:
        return "agy"
    if os.path.basename(os.path.realpath(path)) == "mise":
        found = subprocess.run(["mise", "which", "agy"], capture_output=True, text=True, timeout=120,
                               stdin=subprocess.DEVNULL, cwd=os.path.expanduser("~"), env=without_api_keys(os.environ))
        path = found.stdout.strip() if found.returncode == 0 and found.stdout.strip() else path
    return os.path.realpath(path)


def agy_settings(server):
    """agy's settings for a run, given with --settings and never saved: the backend's tools allowed, which print mode
    would otherwise deny, as it cannot ask."""
    return {"permissions": {"allow": [f"mcp({server}/*)"]}}


def agy_command(model, prompt, resume=None, effort=None, backend="hyprmasq"):
    """agy in print mode as the run's agent (AGY_AGENT), from its agent home, with its events as stream-json. The debrief
    resumes the conversation, whose MCP server the home no longer has (agy_mcp_config)."""
    command = [agy_executable(), "--print", prompt, "--output-format", "stream-json", "--model", model,
               "--agent", AGY_AGENT, "--disable-slash-commands", "--settings", json.dumps(agy_settings(SERVER[backend]))]
    if effort:
        command += ["--effort", effort]
    if resume:
        command += ["--conversation", resume]
    return command


def agy_home(home):
    """The agent home of an agy run: its agent, the user's login linked if it is in a file, and no MCP server yet."""
    config = os.path.join(home, ".gemini", "config")
    os.makedirs(os.path.join(config, "agents"))
    with open(os.path.join(config, "agents", f"{AGY_AGENT}.md"), "w") as f:
        f.write(AGY_AGENT_FILE)
    agy_mcp_config(home)
    token = os.path.expanduser(os.path.join("~", AGY_TOKEN))
    if os.path.exists(token):
        os.makedirs(os.path.dirname(os.path.join(home, AGY_TOKEN)), exist_ok=True)
        os.symlink(token, os.path.join(home, AGY_TOKEN))
    # Nothing updates agy while it runs: a series keeps the version it checked.
    return {"HOME": home, "AGY_CLI_DISABLE_AUTO_UPDATE": "true"}


def agy_mcp_config(home, server=None, tap=None, tools=()):
    """The MCP servers of an agy home: the tap as the backend's server, its tools eager, or none, for the debrief."""
    servers = {}
    if server:
        servers[server] = {"command": tap[0], "args": tap[1:], "timeoutSeconds": TOOL_TIMEOUT,
                           "tools": {tool: {"eager": True} for tool in tools}}
    with open(os.path.join(home, ".gemini", "config", "mcp_config.json"), "w") as f:
        json.dump({"mcpServers": servers}, f, indent=2)


def agy_mcp_tap(home, tap):
    """The home's MCP server, with its eager tools, through a new tap: agy starts its servers from mcp_config.json,
    not from its command, so the tap that the agent gets after the user's reply goes there."""
    path = os.path.join(home, ".gemini", "config", "mcp_config.json")
    with open(path) as f:
        config = json.load(f)
    for server in config["mcpServers"].values():
        server.update({"command": tap[0], "args": tap[1:]})
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def server_tools(cid, log):
    """The names of the tools of the backend's server in the VM, listed in a session of their own through the tap,
    which logs it to log: agy gives the model only the tools named eager in its configuration."""
    import mcp_probe

    session = mcp_probe.Session(cid, log, client="hyprmasq-bench-tools")
    try:
        return [tool["name"] for tool in session.request("tools/list", {})["result"]["tools"]]
    finally:
        session.close()


AGY_EFFORT_SUFFIX = re.compile(r"-(?:" + "|".join(EFFORTS["agy"]) + r")$")


def check_agy_model(model, effort, env):
    """Refuses a model that agy does not list: as a model of its own, such as gemini-4-neon-low, which takes no effort,
    or with variants of each effort, such as gemini-4-neon, whose effort's variant it must list. agy would fail the run
    after the VM's boot."""
    listed = subprocess.run([agy_executable(), "models"], capture_output=True, text=True, timeout=120,
                            env=agent_env(env), stdin=subprocess.DEVNULL, cwd=env["HOME"])
    models = {line.split()[0] for line in listed.stdout.splitlines() if line.strip() and "\t" in line}
    if not models:
        raise RuntimeError(f"agy lists no models (exit {listed.returncode}): log in with `agy`, or `agy acp --login` "
                           "over SSH: " + (listed.stdout + listed.stderr).strip()[-300:])
    if model in models:
        if effort:
            raise RuntimeError(f"agy's model {model} has its effort in its name: give it without @{effort}, or give "
                               f"{AGY_EFFORT_SUFFIX.sub('', model)}@{effort}")
        return
    if effort and f"{model}-{effort}" not in models:
        raise RuntimeError(f"agy lists no {model}-{effort}: its models are {', '.join(sorted(models))}")
    if not any(AGY_EFFORT_SUFFIX.sub("", m) == model for m in models):
        raise RuntimeError(f"agy lists no model {model}: its models are {', '.join(sorted(models))}")


def agy_quota(env):
    """What is left of the login's quota, by bucket, as `agy --print /usage` reports it: {id: {"remaining", "reset",
    "group", "window"}}, such as gemini-5h, the Gemini models' 5-hour limit, and gemini-weekly. A plan's quota is spent
    in proportion to what the tokens cost, shared by every machine of the login. None where agy does not say."""
    try:
        got = subprocess.run([agy_executable(), "--print", "/usage", "--output-format", "json"], capture_output=True,
                             text=True, timeout=120, env=agent_env(env), stdin=subprocess.DEVNULL, cwd=env["HOME"])
        groups = json.loads(got.stdout)["command"]["data"]["groups"]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None
    return {b["id"]: {"remaining": b.get("remaining_fraction"), "reset": b.get("reset_time"), "group": g.get("name"),
                      "window": b.get("window")}
            for g in groups for b in g.get("buckets") or [] if b.get("id")}


def agent_command(harness, model, prompt, tap, resume=None, sessions=None, effort=None, backend="hyprmasq",
                  read_dir=None, tap_env=None, tool_search=True):
    if harness in HYPRMASQ_ONLY and backend != "hyprmasq":
        raise ValueError(hyprmasq_only(harness))
    if harness == "agy":
        return agy_command(model, prompt, resume, effort, backend)
    if harness == "pi":
        return pi_command(model, prompt, tap, resume, sessions, effort)
    if family(harness) == "claude":
        return claude_command(model, prompt, tap, resume, effort, backend, read_dir=read_dir, tap_env=tap_env,
                              tool_search=tool_search)
    return codex_command(model, prompt, tap, resume, effort, backend)


def task_prompt(harness, backend, text, browser="firefox"):
    """The prompt the agent gets: with a backend's skill, naming it first, as the laptop's runs did with Cua's, and with
    Chromium as the user's browser, naming it where the task's prompt names Firefox."""
    mention = SKILL_MENTION[family(harness)].format(skill=SKILLS[backend][0]) if backend in SKILLS else ""
    return mention + browser_text(text, browser)


def browser_text(text, browser):
    """A task's prompt or debrief for the user's browser: the tasks' texts name Firefox, their default."""
    return text.replace("Firefox", "Chromium") if browser == "chromium" else text


def agent_home(harness, skill=None, name="cua-driver"):
    """A fresh agent home for one run, for every backend, that holds the login and, with a backend's skill, that skill,
    named name, and nothing else of the user's, as `cua-driver skills install` links Cua's for each agent
    (docs/content/docs/cua-driver/guides/connect-your-agent.mdx, Install the agent skill) and computer-use-linux's plugin
    installs its own, and the environment that points the harness at it: Claude Code's configuration directory, with
    skills/NAME and for its subscription a copy of the user's login without its refresh token; or for Codex a HOME, with
    .agents/skills/NAME, and a CODEX_HOME in it with a link to the user's login, which also takes the run's sessions.
    Claude Code, tried at 2.1.319, replaces a linked credentials file with a regular one and leaves the link's target
    with a refresh token that it has spent, and it refreshes on any 401 as well as near the expiry. Without the refresh
    token it never refreshes and a run cannot touch the user's login, and renew_claude_login leaves each run enough time.
    Codex, tried at 0.167.0, writes a renewed login through the link, so that it reaches the user's. agy gets a HOME
    with its agent and its login (agy_home).
    Returns (directory, environment)."""
    home = tempfile.mkdtemp(prefix="hyprmasq-bench-agent-home-")
    if harness == "agy":
        return home, agy_home(home)
    skill = os.path.realpath(skill) if skill else None
    if family(harness) == "claude":
        config = os.path.join(home, "claude")
        os.makedirs(os.path.join(config, "skills") if skill else config)
        if skill:
            os.symlink(skill, os.path.join(config, "skills", name))
        credentials = os.path.expanduser("~/.claude/.credentials.json")
        if harness == "claude" and os.path.exists(credentials):
            with open(credentials) as f:
                login = json.load(f)
            (login.get("claudeAiOauth") or {}).pop("refreshToken", None)
            write_login(os.path.join(config, ".credentials.json"), login)
        return home, {"CLAUDE_CONFIG_DIR": config}
    codex_home = os.path.join(home, ".codex")
    os.makedirs(codex_home)
    if skill:
        os.makedirs(os.path.join(home, ".agents", "skills"))
        os.symlink(skill, os.path.join(home, ".agents", "skills", name))
    os.symlink(os.path.expanduser("~/.codex/auth.json"), os.path.join(codex_home, "auth.json"))
    return home, {"HOME": home, "CODEX_HOME": codex_home}


def agent_env(extra=None):
    # Nothing of a surrounding agent session: its effort, session ids and sockets would change the run. No API key
    # either, but the one a claude-api run gives in `extra`: the others use the user's logins. agy would take a Gemini
    # API key, Google's application credentials or a gateway in place of the login.
    env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("CLAUDE", "CODEX", "T3CODE", "AUTOREVIEW", "ANTHROPIC_", "OPENAI_", "PI_",
                             "HYPRMASQ_BENCH_ANTHROPIC_", "AGY_", "ANTIGRAVITY_", "GEMINI_", "GOOGLE_"))
        and k != "AI_AGENT"
    }
    env.update({"CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1", "MCP_TOOL_TIMEOUT": str(TOOL_TIMEOUT * 1000),
                # A run's harness stays the version it started as, and does what the pinned package does on every
                # machine: this machine's NixOS package sets these in its wrapper.
                "DISABLE_AUTOUPDATER": "1", "DISABLE_INSTALLATION_CHECKS": "1",
                "DISABLE_NON_ESSENTIAL_MODEL_CALLS": "1"})
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


# How long before it ends a run renews the subscription's login: more than a run can last, its VM's boot, agent and
# debrief, as the run gets the access token alone and cannot renew it (agent_home).
LOGIN_MARGIN_SECONDS = 3600
# What a run takes beyond its agent's timeout: the VM's boot and the debrief.
RUN_ALLOWANCE_SECONDS = 900


def login_margin(timeout):
    """How long the Claude login must still last when a run starts: the run's whole length, and at least an hour."""
    return max(LOGIN_MARGIN_SECONDS, timeout + RUN_ALLOWANCE_SECONDS)


def login_expiry(path):
    """When the Claude login in a credentials file ends, in seconds since the epoch, or None if it holds none that does."""
    with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
        with open(path) as f:
            return json.load(f)["claudeAiOauth"]["expiresAt"] / 1000
    return None


def write_login(path, login):
    """Writes a new credentials file that only its owner can read."""
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as f:
        json.dump(login, f)


def renew_claude_login(margin=LOGIN_MARGIN_SECONDS, now=time.time):
    """Renews the Claude subscription's login before a run that could outlive it, and fails the run if that did not work.
    Claude Code refreshes only in the login's last minutes, so a request with the user's own file would not renew it yet.
    Here one process at a time, under a lock of this file as the refresh token works once, has Claude Code refresh a copy
    marked as expired in a private directory, and puts the copy in the user's place only if its expiry moved forward. A
    refresh the endpoint refuses logs out the copy alone. A login without a file, or one that does not expire, needs
    nothing."""
    path = os.path.expanduser("~/.claude/.credentials.json")

    def enough():
        ends = login_expiry(path)
        return ends is None or ends - now() > margin

    if enough():
        return
    with open(path + ".bench-lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if enough():
            return
        ends = login_expiry(path)
        print(f"renewing the Claude login, which ends in {ends - now():.0f} s", flush=True)
        # Beside the user's file, so that the replacement below is atomic.
        with tempfile.TemporaryDirectory(prefix=".bench-renew-", dir=os.path.dirname(path)) as config:
            renewed = os.path.join(config, ".credentials.json")
            with open(path) as f:
                login = json.load(f)
            login["claudeAiOauth"]["expiresAt"] = int((now() - 60) * 1000)
            write_login(renewed, login)
            # A request that hangs after the refresh has still renewed the file.
            with contextlib.suppress(subprocess.TimeoutExpired):
                subprocess.run(["claude", "-p", "Reply with OK.", "--model", "claude-haiku-5-5", "--tools", "",
                                "--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands"],
                               cwd=tempfile.gettempdir(), env=agent_env({"CLAUDE_CONFIG_DIR": config}),
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=180)
            if (login_expiry(renewed) or 0) > ends:
                os.replace(renewed, path)
        if not enough():
            raise RuntimeError(f"the Claude login could not be renewed: it ends in {login_expiry(path) - now():.0f} s")


def session_of(line):
    """The session id in a line of the agent's events: Claude Code's, Codex's thread, pi's session header, or agy's
    conversation."""
    return (line.get("session_id") or line.get("thread_id") or (line.get("id") if line.get("type") == "session" else None)
            or line.get("conversation_id"))


def is_init(line):
    return line.get("type") == "system" and line.get("subtype") == "init"


def without_api_keys(environ):
    """An environment without Anthropic credentials, for the processes that need none: the VM driver, nix-build, the
    harnesses' --version, and ssh."""
    return {k: v for k, v in environ.items() if not k.startswith(("ANTHROPIC_", "HYPRMASQ_BENCH_ANTHROPIC_"))}


def run_agent(command, cwd, stream_path, stderr_path, timeout, env=None, api_key=False, budget=None):
    """Runs the agent, logging each line of its stdout with the host time it arrived. With api_key, it stops the
    agent at Claude Code's init event unless that names the API key as its credentials. With budget, the path of the
    tap's marker, it stops the agent once the tap has refused a call past the budget."""
    start = time.time()
    with open(stream_path, "a") as stream, open(stderr_path, "a") as stderr:
        proc = subprocess.Popen(
            command, cwd=cwd, env=agent_env(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr,
            text=True, start_new_session=True,
        )
        timer = threading.Timer(timeout, lambda: os.killpg(proc.pid, signal.SIGTERM))
        timer.start()
        spent = threading.Event()

        def watch_budget():
            while proc.poll() is None:
                if os.path.exists(budget):
                    spent.set()
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    return
                time.sleep(0.5)

        if budget:
            threading.Thread(target=watch_budget, daemon=True).start()
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
            "api_key_source": source, "refused": refused,
            # An agent that ends right after the tap spent the budget can exit before the watcher looks.
            "budget_exhausted": spent.is_set() or bool(budget) and os.path.exists(budget)}


def final_text(harness, stream_path):
    return report.agent_usage(harness, report.load_jsonl(stream_path)).get("final")


def claude_project(cwd, config=None):
    """Where Claude Code keeps the transcripts of sessions that ran in cwd, under its configuration directory."""
    return os.path.join(config or os.path.expanduser("~/.claude"), "projects", re.sub(r"[^A-Za-z0-9]", "-", cwd))


def move_transcript(harness, session, cwd, run_dir, sessions=None, config=None):
    """Moves the harness's own transcript of the session into the run's directory. config is the harness's directory,
    its agent home's, by default the user's."""
    if not session:
        return
    project = None
    if harness == "pi":
        paths = glob.glob(os.path.join(sessions, f"*_{session}.jsonl"))
    elif harness == "agy":
        # The conversation's database, with every request agy made, and its directory, with the images the agent read.
        paths = [os.path.join(config, AGY_DATA, "conversations", f"{session}.db"),
                 os.path.join(config, AGY_DATA, "brain", session)]
        paths = [p for p in paths if os.path.exists(p)]
    elif family(harness) == "claude":
        project = claude_project(cwd, config)
        # The transcript, and a directory of the same name with the session's tool results.
        paths = glob.glob(os.path.join(project, f"{session}*"))
    else:
        codex_home = config or os.path.expanduser("~/.codex")
        paths = glob.glob(os.path.join(codex_home, f"sessions/*/*/*/rollout-*{session}.jsonl"))
    for path in paths:
        shutil.move(path, os.path.join(run_dir, f"transcript-{os.path.basename(path)}"))
    if project and os.path.isdir(project) and not os.listdir(project):
        os.rmdir(project)


def _rmtree(path, container=False):
    shutil.rmtree(path, ignore_errors=True)
    if container and os.path.exists(path):
        subprocess.run(
            ["unshare", "--user", "--map-auto", "--map-root-user", "sh", "-c", 'chmod -R u+w "$1" 2>/dev/null; rm -rf "$1"', "_", path],
            check=False, env=without_api_keys(os.environ),
        )


def vm_runtime_dir(cid, container=False):
    """A directory of its own for the VM or container of the run on vsock CID cid. The test driver keeps the VM's disk
    and sockets in $XDG_RUNTIME_DIR/vm-state-machine, which runs on one machine would share, and the run that starts
    second would remove the first's. In container mode, driver.py mounts a fresh tmpfs over /run, so keep the runtime
    dir under /tmp instead of /run/user/..."""
    # /tmp directly (not tempfile.gettempdir()): nix-shell sets a long TMPDIR that exceeds the 108-byte AF_UNIX limit.
    base = "/tmp" if container else (os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
    path = os.path.join(base, f"hyprmasq-bench-{'c' if container else 'vm'}-{cid}")
    _rmtree(path, container=container)
    os.makedirs(path, mode=0o700)
    return path


def stop_driver(proc, run_dir, timeout=300, container=False):
    """Waits for the driver to grade and stop the VM; kills the VM if it does not."""
    deadline = time.time() + timeout
    while proc.poll() is None and time.time() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    _rmtree(os.path.join(run_dir, "tmp"), container=container)


def pi_session(sessions, session):
    """The session file of a pi session, and the thinking level it ran at: pi clamps its default to the model's."""
    path = next(iter(glob.glob(os.path.join(sessions, f"*_{session}.jsonl"))), None) if session else None
    levels = [e.get("thinkingLevel") for e in report.load_jsonl(path) if e.get("type") == "thinking_level_change"] if path else []
    return path, (levels[0] if levels else None)


def ran_effort(harness, run_dir, cwd, session, thinking, config=None, model=None):
    """The effort the harness says the agent ran at, and where it says so; None where it says nothing. Codex gives it
    in the metadata of each MCP tool call, Claude Code in the requests its transcript logs (its stream-json has
    none), pi in its session's thinking level, and agy in the model variant its conversation's database names."""
    if harness == "pi":
        found = [thinking] if thinking else []
    elif harness == "agy":
        path = os.path.join(config, AGY_DATA, "conversations", f"{session}.db")
        found = report.agy_efforts(path, model) if session else []
    elif family(harness) == "claude":
        path = os.path.join(claude_project(cwd, config), f"{session}.jsonl")
        found = report.claude_efforts(report.load_jsonl(path)) if session else []
    else:
        found = report.codex_efforts(report.load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
    return "/".join(found) or None, report.REPORTED_BY[family(harness)] if found else None


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


def task_file(task_dir, variant, name):
    """A task's prompt.md, prompt-comparison.md or debrief.md: a variant's own, in its directory, or else the task's."""
    own = os.path.join(task_dir, variant or "", name)
    return own if variant and os.path.exists(own) else os.path.join(task_dir, name)


@contextlib.contextmanager
def booted_vm(driver, run_dir, cid, timeout, cpus=None, env=None, container=False):
    """Boots the VM (or systemd-nspawn container) of a run directory that has tmp/ and driver-out/, and yields its
    driver once ready; then has it grade and stop. timeout is how long the guest waits for agent-done. With cpus, the
    driver and guest run on those CPUs alone; env adds to the driver's environment."""
    runtime = vm_runtime_dir(cid, container=container)
    env = dict(without_api_keys(os.environ), HYPRMASQ_BENCH_OUT=run_dir, HYPRMASQ_BENCH_CID=str(cid),
               TMPDIR=os.path.join(run_dir, "tmp"), HYPRMASQ_BENCH_TIMEOUT=str(timeout),
               **(env or {}))
    driver_cmd = [os.path.join(driver, "bin", "nixos-test-driver"), "-o", os.path.join(run_dir, "driver-out")]
    if container:
        # systemd-run --user --scope -p Delegate=yes creates a user-owned delegated cgroup2 subtree (required over SSH
        # where session-N.scope is root-owned), and needs the host XDG_RUNTIME_DIR before switching it to `runtime`.
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        driver_cmd = [
            "systemd-run", "--user", "--scope", "-p", "Delegate=yes", "--quiet",
            "unshare", "--user", "--mount", "--pid", "--fork", "--net", "--cgroup",
            "--map-auto", "--map-root-user", "--mount-proc",
            "sh", "-c",
            'mount -t cgroup2 none /sys/fs/cgroup && mount -t tmpfs none /run && export XDG_RUNTIME_DIR="$1" && shift && exec "$@"',
            "_", runtime, *driver_cmd,
        ]
    else:
        env["XDG_RUNTIME_DIR"] = runtime
    with open(os.path.join(run_dir, "driver.log"), "w") as log:
        # The affinity is set in the child before it runs the driver, so that QEMU / nspawn threads inherit it.
        driver_proc = subprocess.Popen(
            driver_cmd,
            cwd=run_dir, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            preexec_fn=(lambda: os.sched_setaffinity(0, cpus)) if cpus else None,
        )
    try:
        deadline = time.time() + 900
        while not os.path.exists(os.path.join(run_dir, "ready.json")):
            if driver_proc.poll() is not None or time.time() > deadline:
                raise RuntimeError(f"the {'container' if container else 'VM'} did not get ready, see {run_dir}/driver.log")
            time.sleep(1)
        yield driver_proc
    finally:
        open(os.path.join(run_dir, "agent-done"), "w").close()
        stop_driver(driver_proc, run_dir, container=container)
        # A driver that failed can leave its QEMU running; the run's own directory names it.
        if not container:
            subprocess.run(["pkill", "-KILL", "-f", f"{runtime}/vm-state-"], check=False, env=without_api_keys(os.environ))
        _rmtree(runtime, container=container)


def reply_as_user(harness, model, effort, backend, agent, run_dir, cwd, cid, sessions, agent_dir, agent_vars, timeout,
                  max_calls, label, read_dir=None, tap_env=None, tool_search=True):
    """The user's one reply to an agent that ended its turn without saying "Done" (needs_reply), and the agent going on
    in its session, with its tools, a tap that counts the calls made before against the budget, and the rest of its
    time: an agent with none left gets no reply. Its events follow the turn's in agent.jsonl, so that the run's time, usage and final answer are the two
    turns'. A new tap means a new hyprmasq-mcp in the VM, so the agent's shots from before are gone. Returns the agent
    of both turns and what the user replied after what, or the agent alone and None."""
    stream = os.path.join(run_dir, "agent.jsonl")
    final = final_text(harness, stream)
    resume = pi_session(sessions, agent["session"])[0] if harness == "pi" else agent["session"]
    left = timeout - (time.time() - agent["start"])
    if not needs_reply(agent, final) or not resume or left <= 0:
        return agent, None
    log = os.path.join(run_dir, "mcp.jsonl")
    spent = sum(1 for e in report.load_jsonl(log)
                if e.get("dir") == "request" and (e.get("msg") or {}).get("method") == "tools/call")
    tap = tap_command(cid, log, max_calls, spent)
    if agent_dir:
        pi_mcp_config(agent_dir, tap)
    if harness == "agy":
        agy_mcp_tap(agent_vars["HOME"], tap)
    print(f"{label} the agent ended without saying Done: the user replies", flush=True)
    command = agent_command(harness, model, USER_REPLY, tap, resume=resume, sessions=sessions, effort=effort,
                            backend=backend, read_dir=read_dir, tap_env=tap_env, tool_search=tool_search)
    more = run_agent(command, cwd, stream, os.path.join(run_dir, "agent.stderr"), left, agent_vars,
                     api_key=harness == "claude-api",
                     budget=os.path.join(run_dir, "mcp.jsonl.budget") if max_calls > 0 else None)
    both = {**more, "start": agent["start"], "api_key_source": agent["api_key_source"],
            "refused": agent["refused"] or more["refused"],
            # Claude Code may go on in a session of its own: the debrief resumes the last, and both transcripts move.
            "session": more["session"] or agent["session"],
            "sessions": list(dict.fromkeys(s for s in (agent["session"], more["session"]) if s))}
    return both, {"after": final, "reply": USER_REPLY, "at": more["start"], "tool_calls_before": spent}


def run_debrief(harness, model, effort, backend, browser, task_dir, variant, run_dir, cwd, agent_vars, summary, resume,
                label, run):
    """Asks the agent, in its own session, the task's debrief questions about its run, into debrief.md."""
    calls = report.tool_calls(report.load_jsonl(os.path.join(run_dir, "mcp.jsonl")))
    text = open(task_file(task_dir, variant, "debrief.md")).read().replace(
        "{report}", report.debrief_report(summary, calls, TOOL_NAME[backend]))
    # The debriefs name hyprmasq, the tool the run used: with another backend, that one.
    text = browser_text(text.replace("hyprmasq", TOOL_NAME[backend]), browser)
    with open(os.path.join(run_dir, "debrief-prompt.md"), "w") as f:
        f.write(text)
    print(f"{label} debrief", flush=True)
    if harness == "agy":
        # The debrief gets no tools: the home's MCP server goes, and the agent's read_file finds no new image.
        agy_mcp_config(agent_vars["HOME"])
    command = agent_command(harness, model, text, None, resume=resume, effort=effort, backend=backend)
    told = run_agent(command, cwd, os.path.join(run_dir, "debrief.jsonl"),
                     os.path.join(run_dir, "debrief.stderr"), 900, agent_vars,
                     api_key=harness == "claude-api")
    answer = None if told["refused"] else final_text(harness, os.path.join(run_dir, "debrief.jsonl"))
    with open(os.path.join(run_dir, "debrief.md"), "w") as f:
        f.write(answer or "(no answer)\n")
    if family(harness) == "claude":
        run["debrief_api_key_source"] = told["api_key_source"]
        with open(os.path.join(run_dir, "run.json"), "w") as f:
            json.dump(run, f, indent=2)
    try:
        check_api_key_used(told, "debrief")
    except RuntimeError as e:
        raise DebriefKeyError(str(e)) from e


class DebriefKeyError(RuntimeError):
    """A debrief that used other credentials than the run's key: the run fails, as for the agent."""


def run_one(driver, task, variant, harness, model, effort, rep, out, cid, timeout, debrief, cpus=None,
            dispatched=None, backend="hyprmasq", prompt_kind="task", browser="firefox", max_calls=0, desktop="bare",
            chromium_options="", mcp_args="", user_reply=False, container=False, tool_search=True):
    task_dir = os.path.join(BENCH, "tasks", task)
    run_dir = os.path.join(out, run_name(harness, model, rep, effort))
    label = f"[{harness} {model}{f' @{effort}' if effort else ''} #{rep}]"
    # claude-api: the key and the check that Claude Code bills it, before the VM boots, so that a run never falls
    # back to the subscription.
    api_env = anthropic_api_env() if harness == "claude-api" else None
    auth = check_api_auth(api_env) if api_env else None
    if harness == "claude":
        renew_claude_login(login_margin(timeout))
    os.makedirs(os.path.join(run_dir, "tmp"))
    os.makedirs(os.path.join(run_dir, "driver-out"))
    # A fresh directory outside any project: harnesses load instructions such as AGENTS.md from the directory
    # they run in and its parents, and a run directory inside this checkout would give the agent hyprmasq's.
    cwd = tempfile.mkdtemp(prefix="hyprmasq-bench-agent-")
    mcp_target = f"unix:{os.path.join(run_dir, 'mcp.sock')}" if container else cid
    tap = tap_command(mcp_target, os.path.join(run_dir, "mcp.jsonl"), max_calls)
    agent_dir = sessions = home = None
    if harness == "pi":
        sessions = os.path.join(tempfile.mkdtemp(prefix="hyprmasq-bench-pi-sessions-"), "sessions")
        agent_dir = pi_agent_dir(tap, sessions)
        check_pi_model(agent_dir, model)
        api_env = {"PI_CODING_AGENT_DIR": agent_dir}
    # The variables of the agent's process: claude-api's key or pi's directory, and the agent home, with a backend's
    # skill in it.
    agent_vars = dict(api_env or {})
    if harness != "pi":
        home, home_env = agent_home(harness, skill_link(out, backend) if backend in SKILLS else None,
                                    SKILLS[backend][0] if backend in SKILLS else "")
        agent_vars.update(home_env)
    config = agent_vars.get("CLAUDE_CONFIG_DIR") or agent_vars.get("CODEX_HOME") or (home if harness == "agy" else None)
    agent, leaked, eager, quota = {"session": None}, [], None, {}
    try:
        if harness == "agy":
            check_agy_model(model, effort, agent_vars)
            quota["before"] = agy_quota(agent_vars)
        print(f"{label} booting the {'container' if container else 'VM'}", flush=True)
        load = {}
        hypruse_file_mode = backend == "hypruse" and container and family(harness) == "claude"
        vm_env = {**({"HYPRMASQ_BENCH_CHROMIUM_OPTIONS": chromium_options} if chromium_options else {}),
                  **({"HYPRMASQ_BENCH_MCP_ARGS": mcp_args} if mcp_args else {}),
                  **({"HYPRMASQ_BENCH_HYPRUSE_MODE": "file"} if hypruse_file_mode else {})}
        with host_load(load), booted_vm(driver, run_dir, cid, timeout + 120, cpus, vm_env or None, container=container):
            read_dir = os.path.realpath(os.path.join(run_dir, "hypruse-shots")) if hypruse_file_mode else None
            tap_env = {"HYPRMASQ_BENCH_REWRITE_PATH": f"/run/user/1000/hypruse={read_dir}"} if read_dir else None
            prompt = task_prompt(harness, backend, open(task_file(task_dir, variant, PROMPTS[prompt_kind])).read(),
                                 browser)
            if harness == "agy":
                eager = server_tools(mcp_target, os.path.join(run_dir, "mcp-tools.jsonl"))
                agy_mcp_config(home, SERVER[backend], tap, eager)
            command = agent_command(harness, model, prompt, tap, sessions=sessions, effort=effort, backend=backend,
                                    read_dir=read_dir, tap_env=tap_env, tool_search=tool_search)
            print(f"{label} agent running", flush=True)
            agent = run_agent(command, cwd, os.path.join(run_dir, "agent.jsonl"),
                              os.path.join(run_dir, "agent.stderr"), timeout, agent_vars,
                              api_key=harness == "claude-api",
                              budget=os.path.join(run_dir, "mcp.jsonl.budget") if max_calls > 0 else None)
            reply = None
            if user_reply:
                check_api_key_used(agent, "agent")
                agent, reply = reply_as_user(harness, model, effort, backend, agent, run_dir, cwd, mcp_target, sessions,
                                             agent_dir, agent_vars, timeout, max_calls, label,
                                             read_dir=read_dir, tap_env=tap_env, tool_search=tool_search)

        run = {
            "task": task, "variant": variant, "harness": harness, "model": model, "rep": rep, "cid": cid,
            "backend": backend, "prompt": prompt_kind, "browser": browser, "desktop": desktop,
            "container": container, "tool_search": tool_search,
            # What built the run: the VM's driver, with the plugin and the server in it, and the skill.
            "identity": driver_identity(driver, out, backend),
            # The VM's session has the AT-SPI bus and toolkit-accessibility on (default.nix), which runs before
            # these fields had not.
            "accessibility": True,
            # How long the agent may take, and one tool call, in seconds: the same for both backends.
            "timeouts": {"agent_seconds": timeout, "tool_seconds": TOOL_TIMEOUT},
            # The budget of tool calls, 0 for none, and whether the agent was stopped at it.
            "max_calls": max_calls, "budget_exhausted": agent["budget_exhausted"],
            "agent_start": agent["start"], "agent_end": agent["end"], "agent_exit": agent["exit"],
            "timed_out": agent["timed_out"], "session": agent["session"],
            # With --user-reply, the user's one reply to an agent that ended without saying "Done", or None.
            "user_reply_enabled": user_reply, "user_reply": reply,
            "harness_version": version([agy_executable() if harness == "agy" else executable(harness), "--version"]),
            "machine": machine(),
            # The CPUs the VM ran on, or None where it could run on any.
            "cpus": format_cpus(cpus) if cpus else None,
            # The host's load while the VM was up: a run that shared the host with other VMs or another benchmark's
            # work runs again in a series (series.py).
            "host_load": load,
        }
        if dispatched:
            run["dispatch"] = dispatched
        if family(harness) == "claude":
            # Where Claude Code's init event says its credentials came from: ANTHROPIC_API_KEY, or none for a login.
            run["api_key_source"] = agent["api_key_source"]
        if auth:
            run["auth"] = auth
        if harness == "agy":
            # The agent agy ran, its file, and the server's tools it gave the model directly.
            # The quota before the VM's boot, and after the debrief, which run.json gets then (run_debrief).
            run["agy"] = {"agent": AGY_AGENT, "agent_file": AGY_AGENT_FILE, "eager_tools": eager,
                          "settings": agy_settings(SERVER[backend]), "quota": quota}
        session_file = None
        if harness == "pi":
            session_file, run["thinking"] = pi_session(sessions, agent["session"])
        ran_at, reported_by = ran_effort(harness, run_dir, cwd, agent["session"], run.get("thinking"), config, model)
        # The effort asked for, None for the harness's default, and the one the harness says it ran at.
        run["effort"] = {"requested": effort, "ran_at": ran_at, "reported_by": reported_by}
        with open(os.path.join(run_dir, "run.json"), "w") as f:
            json.dump(run, f, indent=2)
        check_api_key_used(agent, "agent")
        summary = report.metrics(run_dir)

        resume = session_file if harness == "pi" else agent["session"]
        # The debrief comes after the run is graded: a debrief that fails leaves the run's result, and says so.
        try:
            if debrief and resume:
                run_debrief(harness, model, effort, backend, browser, task_dir, variant, run_dir, cwd, agent_vars, summary,
                            resume, label, run)
        except DebriefKeyError:
            raise
        except Exception as e:  # noqa: BLE001: whatever else stopped it, the run stands
            print(f"{label} debrief failed: {e!r}", flush=True)
            run["debrief_error"] = repr(e)[:500]
            with open(os.path.join(run_dir, "run.json"), "w") as f:
                json.dump(run, f, indent=2)
        if harness == "agy":
            # What the run and its debrief spent of each bucket, which report.metrics computes again from run.json. Runs
            # of the same login on other machines at the same time spend from it too: dispatch.py's --quota-pace paces on
            # the quota itself (agy_quota), not on these.
            quota["after"] = agy_quota(agent_vars)
            summary["quota_spent"] = report.quota_spent(quota.get("before"), quota.get("after"))
            with open(os.path.join(run_dir, "run.json"), "w") as f:
                json.dump(run, f, indent=2)
    finally:
        for session in agent.get("sessions") or [agent["session"]]:
            move_transcript(harness, session, cwd, run_dir, sessions, config)
        shutil.rmtree(cwd, ignore_errors=True)
        if home:
            # The run's copy of the login, and links, which rmtree removes and does not follow.
            shutil.rmtree(home, ignore_errors=True)
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


LOCK_POLL_SECONDS = 5
# How often a run samples its host's load, and the file a guard on the host holds while another benchmark runs
# there and the series' runs wait (bench/README.md, The comparison series).
HOST_LOAD_SECONDS = 15
GUARD_FILE = "/tmp/hyprmasq-bench-guard"


def host_load_sample():
    """The host's load now: the 1-minute load average, the CPUs' mean clock in MHz, the QEMU processes of this
    benchmark's VMs and of anything else, and whether a guard says that another benchmark runs."""
    with open("/proc/loadavg") as f:
        load1 = float(f.read().split()[0])
    clocks = []
    for path in glob.glob("/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq"):
        with contextlib.suppress(OSError, ValueError), open(path) as f:
            clocks.append(int(f.read()) / 1000)
    own = other = 0
    for path in glob.glob("/proc/[0-9]*/cmdline"):
        with contextlib.suppress(OSError):
            with open(path, "rb") as f:
                argv = f.read().split(b"\0")
            if argv and b"qemu" in os.path.basename(argv[0]):
                if any(b"hyprmasq-bench-vm-" in a for a in argv):
                    own += 1
                else:
                    other += 1
    return {"t": round(time.time(), 1), "load1": load1, "mhz": round(sum(clocks) / len(clocks)) if clocks else None,
            "own_qemu": own, "other_qemu": other, "guard": os.path.exists(GUARD_FILE)}


@contextlib.contextmanager
def host_load(record):
    """Samples the host's load while a run's VM is up, into record: its samples and their worst."""
    samples, stop = [], threading.Event()

    def sample():
        while True:
            with contextlib.suppress(Exception):
                samples.append(host_load_sample())
            if stop.wait(HOST_LOAD_SECONDS):
                break

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        with contextlib.suppress(Exception):
            samples.append(host_load_sample())
        mhz = [s["mhz"] for s in samples if s["mhz"]]
        record.update({"samples": samples, "max_load1": max((s["load1"] for s in samples), default=None),
                       "min_mhz": min(mhz, default=None), "max_own_qemu": max((s["own_qemu"] for s in samples), default=0),
                       "max_other_qemu": max((s["other_qemu"] for s in samples), default=0),
                       "guard": any(s["guard"] for s in samples)})


def exclusive_waiting(path, locks="/proc/locks"):
    """Whether a process waits for an exclusive flock of the file, as `flock FILE` does: the kernel lists it in
    /proc/locks as a blocked WRITE, `-> FLOCK ADVISORY WRITE PID MAJOR:MINOR:INODE ...`, with the device in hex."""
    st = os.stat(path)
    device = f"{os.major(st.st_dev):02x}:{os.minor(st.st_dev):02x}:{st.st_ino}"
    with open(locks) as f:
        for line in f:
            fields = line.split()
            if fields[1:5] == ["->", "FLOCK", "ADVISORY", "WRITE"] and fields[6:7] == [device]:
                return True
    return False


@contextlib.contextmanager
def vm_lock(path, waiting=contextlib.nullcontext):
    """Holds the lock file shared while a run's VM is up: benchmark VMs share it, and other VMs on the machine, such
    as VM tests that take it exclusive with `flock FILE`, wait for them instead of running out of memory, and make
    them wait. flock lets a shared lock in beside another while an exclusive one waits, so slots whose runs overlap
    could hold it for ever: a run does not take it while an exclusive lock is waited for, and so the slots' runs end
    and the VM test gets its turn. The waiting context surrounds the wait."""
    if not path:
        yield
        return
    with open(path, "a") as f:
        with waiting():
            while exclusive_waiting(path):
                time.sleep(LOCK_POLL_SECONDS)
            fcntl.flock(f, fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def driver_identity(driver, out, backend):
    """The store paths that say what a run ran: the driver's holds the VM, its plugin and its server, and the skill's the
    skill the agent read."""
    skill = skill_link(out, backend)
    return {"driver": os.path.realpath(driver), "skill": os.path.realpath(skill) if os.path.exists(skill) else None}


def file_sha256(paths):
    digest = hashlib.sha256()
    for path in paths:
        with open(path, "rb") as f:
            digest.update(f.read())
    return digest.hexdigest()


def run_config(source, task, variant, args):
    """What a results directory holds one of, which check_resume keeps it to: the VM's source, this benchmark's commit,
    the task and how it ran, the hash of the prompt and the debrief the agent gets, and the settings of the measurement."""
    task_dir = os.path.join(BENCH, "tasks", task)
    bench = source_commit(None)
    return {**source_commit(source), "bench_commit": bench["commit"], "bench_dirty": bench["dirty"], "task": task,
            "variant": variant, "display": args.display, "backend": args.backend, "prompt": args.prompt,
            "browser": args.browser, "desktop": args.desktop, "container": bool(getattr(args, "container", False)),
            "chromium_options": args.chromium_options, "mcp_args": args.mcp_args,
            "prompt_sha256": file_sha256([task_file(task_dir, variant, PROMPTS[args.prompt]),
                                          task_file(task_dir, variant, "debrief.md")]),
            "timeout": args.timeout, "max_calls": args.max_calls, "debrief": not args.no_debrief,
            "user_reply": args.user_reply, "tool_search": not getattr(args, "no_tool_search", False)}


def check_identity(out, identity):
    """The finished runs of a results directory that another driver or skill built, as (run, what differs)."""
    differ = []
    for name in sorted(os.listdir(out)):
        with contextlib.suppress(OSError, ValueError), open(os.path.join(out, name, "run.json")) as f:
            before = json.load(f).get("identity") or {}
            if os.path.exists(os.path.join(out, name, "summary.json")):
                differ += [(name, k) for k, v in before.items() if identity.get(k) != v]
    return differ


def check_resume(out, config, parser, agents):
    """Refuse to add runs to a results directory whose finished runs had another commit, task, variant, display,
    backend, prompt, browser, desktop, benchmark commit, prompt text or setting of the measurement, so that one report
    never mixes them. Keys that an older source.json lacks are not compared, but for its backend, prompt, browser and
    desktop, which were hyprmasq, the task's own, Firefox and bare (DEFAULT_CONFIG). Refuse also an agent
    whose run directory holds another agent's run, such as codex:gpt-high's for codex:gpt@high: a resume would count
    that run as its own, or remove it."""
    runs = {}
    for name in os.listdir(out):
        with contextlib.suppress(OSError, ValueError), open(os.path.join(out, name, "run.json")) as f:
            runs[name] = json.load(f)
    for name, before in runs.items():
        ran = (before.get("harness"), before.get("model"), (before.get("effort") or {}).get("requested"))
        for harness, model, effort in agents:
            if (harness, model, effort) != ran and run_name(harness, model, before.get("rep"), effort) == name:
                parser.error(f"{os.path.join(out, name)} holds a run of {agent_spec(*ran)}, not of "
                             f"{agent_spec(harness, model, effort)}: use another --out")
    path = os.path.join(out, "source.json")
    finished = any(os.path.exists(os.path.join(out, name, "summary.json")) for name in os.listdir(out))
    if not finished or not os.path.exists(path):
        return
    with open(path) as f:
        before = {**DEFAULT_CONFIG, **json.load(f)}
    differ = [k for k in IDENTITY_KEYS if k in before and before[k] != config.get(k, DEFAULT_CONFIG.get(k))]
    if differ:
        parser.error(f"{out} has runs with another {', '.join(differ)}: "
                     + ", ".join(f"{k} {before[k]} against {config[k]}" for k in differ) + "; use another --out")


# What results directories from before the backend and prompt options ran with.
DEFAULT_CONFIG = {"backend": "hyprmasq", "prompt": "task", "browser": "firefox", "desktop": "bare", "container": False,
                  "chromium_options": "", "mcp_args": "", "user_reply": False}
# What must be the same for runs to go into one results directory. The driver and the skill are the run's own (run.json's
# identity, check_identity), and bench_dirty says a commit does not name the benchmark's code.
IDENTITY_KEYS = ("commit", "task", "variant", "display", "backend", "prompt", "browser", "desktop", "container",
                 "chromium_options", "mcp_args", "bench_commit", "bench_dirty", "prompt_sha256", "timeout", "max_calls",
                 "debrief", "user_reply")


def pending_runs(out, reps, agents):
    """The runs of the given repetitions that have no summary.json yet, in the order they run: repetitions go round
    the agents, so that a slow hour of an API does not hit one agent's runs only."""
    return [
        (rep, harness, model, effort) for rep in reps for harness, model, effort in agents
        if not os.path.exists(os.path.join(out, run_name(harness, model, rep, effort), "summary.json"))
    ]


def add_run_options(parser, tasks=False):
    """The options that say which runs to make, shared with dispatch.py; with tasks, --task is repeatable and takes
    TASK[:VARIANT], as dispatch.py takes it."""
    if tasks:
        parser.add_argument("--task", action="append", metavar="TASK[:VARIANT]",
                            help="a directory under bench/tasks, with one of its variants after :, repeatable: a "
                                 "results directory each; by default thunderbird")
    else:
        parser.add_argument("--task", default="thunderbird", help="a directory under bench/tasks")
    parser.add_argument("--variant", help="the task's variant, for a task that has them (bench/tasks/TASK/variants), "
                                           "by default its default if it has one")
    parser.add_argument("--display", default="1920x1080@1", help="the VM's monitor, WIDTHxHEIGHT@SCALE")
    parser.add_argument("--agent", action="append", help="HARNESS:MODEL[@EFFORT], repeatable")
    reps = parser.add_mutually_exclusive_group()
    reps.add_argument("--reps", type=int, default=3)
    reps.add_argument("--rep", type=int, action="append", help="run only this repetition, repeatable; instead of --reps")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds an agent may take")
    parser.add_argument("--max-calls", type=int, default=0,
                        help="tool calls an agent may make: the run stops and fails at the next one. 0 for no limit")
    parser.add_argument("--no-debrief", action="store_true")
    parser.add_argument("--no-tool-search", action="store_true",
                        help="omit Claude Code's built-in ToolSearch tool so MCP tool schemas load eagerly")
    parser.add_argument("--user-reply", action="store_true",
                        help=f'answer an agent that ends its turn without saying "Done" once, with "{USER_REPLY}", '
                             "and let it go on in its session with its tools before the VM grades the run")
    parser.add_argument("--backend", choices=BACKENDS, default="hyprmasq",
                        help="the desktop tool the agent gets: hyprmasq, from --source, or Cua Driver (bench/cua), "
                             "hypruse (bench/hypruse) or computer-use-linux (bench/computer-use-linux)")
    parser.add_argument("--prompt", choices=tuple(PROMPTS), default="task",
                        help="the task's own prompt, or the tool-neutral one of a comparison (prompt-comparison.md)")
    parser.add_argument("--browser", choices=BROWSERS, default="firefox",
                        help="the user's browser, in the tasks that list it in tasks/TASK/browsers")
    parser.add_argument("--desktop", choices=DESKTOPS, default="bare",
                        help="the VM's desktop: bare, or omarchy, with Omarchy's key binds, portals and packages")
    parser.add_argument("--container", action="store_true",
                        help="run the benchmark in a systemd-nspawn container on the host's GPU instead of a QEMU VM")
    parser.add_argument("--mcp-args", default="", help="options of hyprmasq-mcp in the VM, such as --max-image-size 1920")
    parser.add_argument("--chromium-options", default="",
                        help="more options of the user's Chromium, as the user started it, such as "
                             "--remote-debugging-port=0 for a browser whose remote debugging was on before the agent came")


def check_run_options(args, parser):
    """The agents and the repetitions of the runs, after checking the task and its variant."""
    agents, reps = check_runs(args, parser)
    args.variant = check_task(args.task, args.variant, parser, args.prompt, args.browser)
    return agents, reps


def check_runs(args, parser):
    """The agents and the repetitions of the runs."""
    try:
        # Once each: a run of the same name twice would share its directory.
        agents = list(dict.fromkeys(parse_agent(a) for a in args.agent or DEFAULT_AGENTS))
    except ValueError as e:
        parser.error(str(e))
    names = [run_name(harness, model, 1, effort) for harness, model, effort in agents]
    if len(set(names)) < len(names):
        parser.error("two agents have the same run directory: their names differ only in characters other than "
                     "letters, digits, ., _ and -, or in the @ of an effort")
    if any(rep < 1 for rep in args.rep or []):
        parser.error("--rep counts from 1")
    only = sorted({harness for harness, _, _ in agents if harness in HYPRMASQ_ONLY})
    if args.backend != "hyprmasq" and only:
        parser.error(hyprmasq_only(only[0]))
    reps = sorted(set(args.rep)) if args.rep else list(range(1, args.reps + 1))
    return agents, reps


def check_task(task, variant, parser, prompt="task", browser="firefox"):
    """The variant of a task after checking both: the task's default if it has variants and none is given. Checks also
    that the task has the prompt asked for, and takes the browser: Firefox, or one its browsers file lists."""
    if not os.path.isdir(os.path.join(BENCH, "tasks", task)):
        parser.error(f"no task {task} in {os.path.join(BENCH, 'tasks')}")
    variants, default = task_variants(task)
    if variants and variant is None:
        variant = default
    if variants and variant not in variants and not (variant is None and variants_optional(task)):
        parser.error(f"task {task} needs --variant, one of {', '.join(variants)}")
    if not variants and variant:
        parser.error(f"task {task} has no variants")
    if not os.path.exists(task_file(os.path.join(BENCH, "tasks", task), variant, PROMPTS[prompt])):
        parser.error(f"task {task} has no {PROMPTS[prompt]}" + (f" for variant {variant}" if variant else ""))
    if browser != "firefox" and browser not in task_browsers(task):
        parser.error(f"task {task} has no browser {browser}: its tasks/{task}/browsers lists "
                     + (", ".join(task_browsers(task)) or "none"))
    return variant


def task_browsers(task):
    """The browsers a task takes, from tasks/TASK/browsers, one a line; none for a task without the user's browser."""
    path = os.path.join(BENCH, "tasks", task, "browsers")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [line.split()[0] for line in f if line.strip()]


def results_name(task, variant):
    """The name of a task's results directory: TASK, or TASK-VARIANT for a variant."""
    return task + (f"-{variant}" if variant else "")


def default_out(task, variant, suffix=""):
    return results_dir(results_name(task, variant) + suffix)


def results_dir(name):
    return os.path.join(BENCH, "results", f"{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}-{name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_run_options(parser)
    parser.add_argument("--out")
    parser.add_argument("--cid", type=int, default=4242)
    parser.add_argument("--source", help="hyprmasq checkout to run in the VM, by default this one")
    parser.add_argument("--lock", help="lock file to hold shared during each run's VM, which VM tests take exclusive")
    parser.add_argument("--cpus", help="the CPUs to run each run's VM on, such as 4-5,12-13: by default any")
    parser.add_argument("--dispatch", type=json.loads, help=argparse.SUPPRESS)  # what dispatch.py adds to run.json
    args = parser.parse_args()
    agents, reps = check_run_options(args, parser)
    try:
        cpus = parse_cpus(args.cpus) if args.cpus else None
        if cpus and not set(cpus) <= os.sched_getaffinity(0):
            raise ValueError(f"CPUs {args.cpus} are not all this machine's: it has {format_cpus(os.sched_getaffinity(0))}")
    except ValueError as e:
        parser.error(str(e))
    out = os.path.abspath(args.out or default_out(args.task, args.variant))
    os.makedirs(out, exist_ok=True)
    source = os.path.abspath(args.source) if args.source else None
    config = run_config(source, args.task, args.variant, args)
    check_resume(out, config, parser, agents)
    driver = build_driver(out, source, args.task, args.variant, args.display, args.backend, args.browser, args.desktop,
                          container=args.container)
    differ = check_identity(out, driver_identity(driver, out, args.backend))
    if differ:
        parser.error(f"{out} has runs that another build ran: "
                     + ", ".join(f"{name} ({what})" for name, what in differ) + "; use another --out")
    with open(os.path.join(out, "source.json"), "w") as f:
        json.dump(config, f, indent=2)
    for rep, harness, model, effort in pending_runs(out, reps, agents):
        shutil.rmtree(os.path.join(out, run_name(harness, model, rep, effort)), ignore_errors=True)
        try:
            with vm_lock(args.lock):
                run_one(driver, args.task, args.variant, harness, model, effort, rep, out, args.cid, args.timeout,
                        not args.no_debrief, cpus, args.dispatch, args.backend, args.prompt, args.browser,
                        args.max_calls, args.desktop, args.chromium_options, args.mcp_args, args.user_reply,
                        container=args.container, tool_search=not args.no_tool_search)
        except Exception as e:  # one broken run does not end the benchmark
            print(f"[{harness} {model}{f' @{effort}' if effort else ''} #{rep}] error: {e}", flush=True)
    print(report.report(out))


if __name__ == "__main__":
    main()
