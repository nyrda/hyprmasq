# The test script of one benchmark run. bench/run.py starts the test driver with HYPRMASQ_BENCH_OUT set to the
# run's directory on the host, and the two talk through files there:
#
#   ready.json      written here once the task is set up and the backend's MCP server listens on vsock port 5000
#   agent-done      written by run.py when the agent has finished
#   vm-result.json  written here after grading; the driver then stops the VM
#
# TASK_DIR, VARIANT, the task's variant or "", BACKEND, hyprmasq, cua, hypruse or computer-use-linux, and BROWSER, the
# user's browser in the tasks that have one, firefox or chromium (start_browser), are set by default.nix before this file
# runs. With cua, Cua's plugin is loaded and enabled as its README does it, and `cua-driver mcp` serves the port; with
# hypruse or computer-use-linux, its MCP server does, and with computer-use-linux the desktop portal's session starts.
# ready.json keeps the readiness report of the tool's own doctor command, for the backends that have one.
# The task's setup.py defines prepare() and collect(), and may define HYPRLAND_ENV, variables for Hyprland's
# environment and so for every app it starts, and during(stop), which runs in a thread while the agent works, until
# the threading.Event stop is set: the user's own activity.
# With HYPRMASQ_BENCH_MEMORY_SECONDS, as calibrate.py sets it, the VM's memory in use is sampled that often while the
# agent works, and vm-result.json holds its peak; not for a task with during(), since the driver's shell takes one
# command at a time.
import base64
import json
import os
import shlex
import threading
import time

OUT = os.environ["HYPRMASQ_BENCH_OUT"]
AGENT_TIMEOUT = float(os.environ.get("HYPRMASQ_BENCH_TIMEOUT", "2400"))
MEMORY_SECONDS = float(os.environ.get("HYPRMASQ_BENCH_MEMORY_SECONDS", "0"))
RUNTIME = "/run/user/1000"
MCP_PORT = 5000
# The user's session bus, which the autologin's user manager starts and the accessibility bus is reached through:
# Hyprland and every app get it, as in a desktop session.
SESSION_BUS = f"unix:path={RUNTIME}/bus"
# Where default.nix's hyprland.lua looks for Cua's line of the configuration.
CUA_CONFIG = "/tmp/hyprmasq-bench-cua.lua"
# Whether the user's Chromium starts with --force-renderer-accessibility, without which it exposes no accessibility
# tree: for Cua, whose browser guide asks for it (docs/content/docs/cua-driver/guides/browsers.mdx), for hypruse, whose
# README says Chrome without it has none, and for computer-use-linux, whose results ask to relaunch Chromium with it.
# hyprmasq reads no accessibility, so its runs keep the browser as it was.
CHROMIUM_ACCESSIBILITY = BACKEND in ("cua", "hypruse", "computer-use-linux")


def out_path(name):
    return os.path.join(OUT, name)


def write_json(name, data):
    with open(out_path(name), "w") as f:
        json.dump(data, f, indent=2)


def alice(command):
    return machine.succeed("su - alice -c " + shlex.quote(command))


def fetch(path, name):
    """Copies a file from the VM into the run's directory, if it exists."""
    status, data = machine.execute(f"test -f {shlex.quote(path)} && base64 -w0 {shlex.quote(path)}")
    if status == 0:
        with open(out_path(name), "wb") as f:
            f.write(base64.b64decode(data))


exec(open(os.path.join(TASK_DIR, "setup.py")).read())

machine.start()
machine.wait_for_unit("multi-user.target")
# The autologin starts alice's manager some time after multi-user.target: wait_for_unit would fail on a unit that has
# not been queued yet.
machine.wait_until_succeeds("systemctl is-active user@1000.service", timeout=300)
machine.succeed(f"install -d -o alice -g users -m 700 {RUNTIME}")
alice(
    f"XDG_RUNTIME_DIR={RUNTIME} DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} HYPRLAND_HEADLESS_ONLY=1 "
    f"{globals().get('HYPRLAND_ENV', '')} "
    "nohup Hyprland --config /etc/hyprmasq-bench/hyprland.lua > /tmp/hyprmasq-bench-hyprland.log 2>&1 &"
)
machine.wait_until_succeeds(f"test -f {RUNTIME}/hypr/*/hyprland.lock", timeout=120)
instance = machine.succeed(f"basename $(dirname $(ls {RUNTIME}/hypr/*/hyprland.lock | head -1))").strip()
display = machine.succeed(f"sed -n '2p' {RUNTIME}/hypr/{instance}/hyprland.lock").strip()
for name, value in (("instance", instance), ("display", display)):
    machine.succeed(f"echo {value} > /tmp/hyprmasq-bench-{name} && chmod 644 /tmp/hyprmasq-bench-{name}")
ipc = f"XDG_RUNTIME_DIR={RUNTIME} HYPRLAND_INSTANCE_SIGNATURE={instance}"
client_env = f"XDG_RUNTIME_DIR={RUNTIME} WAYLAND_DISPLAY={display} DBUS_SESSION_BUS_ADDRESS={SESSION_BUS}"


def hypr(command):
    return alice(f"{ipc} hyprctl {command}").strip()


# The user's browser in the tasks that have one, by BROWSER: its window class and Chromium's options. Chromium runs on
# Wayland with a profile of its own, without first-run screens, and with --force-renderer-accessibility for the backends
# that ask for it (CHROMIUM_ACCESSIBILITY).
BROWSER_CLASS = {"firefox": "firefox", "chromium": "chromium-browser"}
CHROMIUM_PROFILE = "/home/alice/.config/chromium-mara"
CHROMIUM_OPTIONS = ("--no-first-run --no-default-browser-check --password-store=basic "
                    "--disable-search-engine-choice-screen --ozone-platform=wayland")


def start_browser(urls, firefox_profile, firefox_prefs, env=""):
    """Starts the user's browser with the pages urls, as a user would, on the visible workspace, and returns its
    window's address once it has mapped: Firefox with the task's profile and its prefs, a user.js, or Chromium. env
    adds to the browser's environment. Its output goes to /tmp/hyprmasq-bench-BROWSER.log, and with
    HYPRMASQ_BENCH_WAYLAND_DEBUG set in the driver's environment its Wayland protocol too (WAYLAND_DEBUG=1), for
    checks by hand."""
    pages = " ".join(shlex.quote(u) for u in urls)
    debug = "WAYLAND_DEBUG=1" if os.environ.get("HYPRMASQ_BENCH_WAYLAND_DEBUG") else ""
    log = f"/tmp/hyprmasq-bench-{BROWSER}.log"
    if BROWSER == "chromium":
        options = CHROMIUM_OPTIONS + (" --force-renderer-accessibility" if CHROMIUM_ACCESSIBILITY else "")
        alice(f"{client_env} {env} {debug} nohup chromium --user-data-dir={CHROMIUM_PROFILE} {options} {pages} "
              f"> {log} 2>&1 &")
    else:
        alice(f"mkdir -p {firefox_profile} && install -m 0644 {firefox_prefs} {firefox_profile}/")
        alice(f"{client_env} {env} {debug} nohup firefox --profile {firefox_profile} {pages} > {log} 2>&1 &")
    browser = BROWSER_CLASS[BROWSER]
    wait_for(BROWSER, lambda: window(lambda c: c["class"] == browser) is not None, timeout=120)
    return window(lambda c: c["class"] == browser)["address"]


def clients():
    return json.loads(hypr("-j clients"))


def window(predicate):
    return next((c for c in clients() if predicate(c)), None)


def move(address, workspace):
    hypr("dispatch " + shlex.quote(
        f'hl.dsp.window.move({{ window = "address:{address}", workspace = "name:{workspace}", follow = false }})'
    ))
    wait_for(f"{address} on {workspace}", lambda: window(
        lambda c: c["address"] == address and c["workspace"]["name"] == workspace) is not None)


def settle(description, *, initial_delay, interval, fields, predicate=lambda c: True, timeout=120):
    """Wait for two matching client snapshots, preserving the task's delays and fields."""
    def state():
        return [tuple(c[field]["name"] if field == "workspace" else c[field] for field in fields)
                for c in clients() if predicate(c)]

    def settled():
        before = state()
        time.sleep(interval)
        return before == state()

    time.sleep(initial_delay)
    wait_for(description, settled, timeout=timeout)


def snapshot():
    """The user's workspace and window. Hyprland 0.57.0 gives a named workspace, such as a task's, no id: its name
    stands for it."""
    workspace = json.loads(hypr("-j activeworkspace"))
    return {
        "workspace": workspace.get("id", workspace.get("name")),
        "window": json.loads(hypr("-j activewindow")).get("address"),
    }


def wait_for(description, predicate, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.5)
    raise AssertionError("Timed out: " + description)


hypr("output create headless HYPRMASQ-BENCH")
wait_for("headless monitor", lambda: any(m["name"] == "HYPRMASQ-BENCH" for m in json.loads(hypr("-j monitors"))))


def cua_status():
    status, out = machine.execute(f"su - alice -c {shlex.quote(f'{ipc} hyprctl -j cua:status')}")
    try:
        return json.loads(out) if status == 0 else None
    except ValueError:
        return None


# The session's variables, as default.nix's MCP servers get them.
session_env = (f"{client_env} HYPRLAND_INSTANCE_SIGNATURE={instance} XDG_SESSION_TYPE=wayland "
               "XDG_CURRENT_DESKTOP=Hyprland")
# The portal's permission store, where xdg-desktop-portal keeps a user's answer to its access dialogs.
PERMISSION_STORE = ("org.freedesktop.impl.portal.PermissionStore /org/freedesktop/impl/portal/PermissionStore "
                    "org.freedesktop.impl.portal.PermissionStore")
if BACKEND == "computer-use-linux":
    # Its screenshots come from the desktop portal, whose services start from alice's systemd user manager and need the
    # session's variables and graphical-session.target, as in the Forgejo task's setup.py.
    alice(f"export {session_env}; systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP "
          "HYPRLAND_INSTANCE_SIGNATURE XDG_SESSION_TYPE && systemctl --user start hyprmasq-bench-session.target")
    # The consent its README says the Screenshot portal asks for on the first capture: xdg-desktop-portal asks once
    # "Allow Apps to Take Screenshots?" for the apps it knows no ID of, such as a server an agent starts, and keeps the
    # answer. Here the user has answered yes, as they would have before the agent's first screenshot.
    alice(f"DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} busctl --user call {PERMISSION_STORE} SetPermission sbssas "
          "screenshot true screenshot '' 1 yes")


def doctor():
    """The tool's own readiness report, `hypruse doctor` or `computer-use-linux doctor`, with the session's variables,
    as its README has a user check the setup with it; None for the other backends. hypruse prints lines, and
    computer-use-linux JSON, of which its readiness is kept."""
    if BACKEND not in ("hypruse", "computer-use-linux"):
        return None
    merge = " 2>&1" if BACKEND == "hypruse" else ""
    status, out = machine.execute(f"su - alice -c {shlex.quote(f'{session_env} {BACKEND} doctor')}{merge}")
    if BACKEND == "hypruse":
        return {"exit": status, "lines": out.strip().splitlines()}
    try:
        return {"exit": status, **{k: v for k, v in json.loads(out).items() if k in ("readiness", "input", "windowing")}}
    except ValueError:
        return {"exit": status, "lines": out.strip().splitlines()[-20:]}


cua = None
if BACKEND == "cua":
    # As Cua's plugin README has it: load the module explicitly, then enable its transport in the configuration
    # (`hl.config({plugin = {cua = {enabled = true}}})`) and run `hyprctl reload`. hyprctl -j cua:status then has to
    # report the input transport ready.
    hypr(f"plugin load {machine.succeed('cat /etc/hyprmasq-bench/cua-plugin').strip()}")
    alice(f"echo 'hl.config({{plugin = {{cua = {{enabled = true}}}}}})' > {CUA_CONFIG}")
    hypr("reload")
    wait_for("Cua's input transport", lambda: ((cua_status() or {}).get("input") or {}).get("transport_ready"),
             timeout=30)
    cua = cua_status()

# Whether apps are told to expose their accessibility: the bus launcher's org.a11y.Status IsEnabled, which follows
# toolkit-accessibility.
A11Y_STATUS = "org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled"
accessibility = machine.execute(
    f"su - alice -c {shlex.quote(f'DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} busctl --user get-property {A11Y_STATUS}')}"
)[1].strip()


def shoot_windows(prefix):
    """Screenshots of the screen into the run's directory, and of every window where hyprmasq's plugin can take them,
    hidden ones too."""
    for c in clients() if BACKEND == "hyprmasq" else []:
        path = f"/tmp/hyprmasq-bench-shot-{c['address']}.png"
        reply = json.loads(hypr(f"-j hyprmasq screenshot {c['address']} {path}"))
        if reply.get("ok"):
            fetch(path, f"{prefix}-{c['class'] or 'window'}-{c['address']}.png")
    alice(f"{client_env} grim /tmp/hyprmasq-bench-screen.png")
    fetch("/tmp/hyprmasq-bench-screen.png", f"{prefix}-screen.png")


alice(
    f"nohup /etc/hyprmasq-bench/events {RUNTIME}/hypr/{instance}/.socket2.sock /tmp/hyprmasq-bench-events.log "
    "> /dev/null 2>&1 &"
)

# The user's own work stays on the visible workspace for the whole run: a terminal that redraws every second.
alice(f"{client_env} nohup foot -T user-terminal top -d 1 > /tmp/hyprmasq-bench-foot.log 2>&1 &")
wait_for("user terminal", lambda: any(c["title"] == "user-terminal" for c in clients()))

task_window = prepare()

alice(
    f"nohup socat VSOCK-LISTEN:{MCP_PORT},fork,reuseaddr EXEC:/etc/hyprmasq-bench/mcp "
    "> /tmp/hyprmasq-bench-socat.log 2>&1 &"
)
machine.wait_until_succeeds(f"ss -H -l --vsock | grep -q ':{MCP_PORT}'", timeout=30)

before = snapshot()
shoot_windows("start")
vm_time = float(machine.succeed("date +%s.%N"))
write_json("ready.json", {
    "vm_time": vm_time,
    "host_time": time.time(),
    "user": before,
    "task_window": task_window,
    "clients": clients(),
    "backend": BACKEND,
    "accessibility": accessibility,
    "cua_status": cua,
    "doctor": doctor(),
})

stop_user = threading.Event()
user_thread = None
if "during" in globals():
    user_thread = threading.Thread(target=during, args=(stop_user,), daemon=True)
    user_thread.start()



def memory_in_use():
    """The VM's memory in use, in MiB: what it could not free for an app, page cache and buffers aside."""
    info = {line.split(":")[0]: int(line.split()[1]) for line in machine.succeed("cat /proc/meminfo").splitlines()}
    return info["MemTotal"] // 1024, (info["MemTotal"] - info["MemAvailable"]) // 1024


memory = []
next_sample = 0
deadline = time.monotonic() + AGENT_TIMEOUT
while not os.path.exists(out_path("agent-done")):
    if time.monotonic() > deadline:
        machine.log("the agent did not finish in time")
        break
    if MEMORY_SECONDS and not user_thread and time.monotonic() >= next_sample:
        memory.append(memory_in_use())
        next_sample = time.monotonic() + MEMORY_SECONDS
    time.sleep(0.5)

stop_user.set()
if user_thread:
    user_thread.join(timeout=30)
after = snapshot()
shoot_windows("end")
result = {
    "user_before": before,
    "user_after": after,
    "user_same_at_end": before == after,
    "clients": clients(),
    "task": collect(),
}
if memory:
    result["memory"] = {"total_mib": memory[0][0], "peak_used_mib": max(used for _, used in memory),
                        "samples": len(memory)}
if BACKEND == "computer-use-linux":
    # The user services it relies on, the desktop portal's and ydotoold, log to alice's journal.
    machine.execute("journalctl _UID=1000 -b --no-pager -o short-monotonic > /tmp/hyprmasq-bench-user-journal.log; "
                    "systemctl --user -M alice@ status ydotoold xdg-desktop-portal xdg-desktop-portal-hyprland "
                    ">> /tmp/hyprmasq-bench-user-journal.log 2>&1")
for path, name in [
    ("/tmp/hyprmasq-bench-user-journal.log", "vm-user-journal.log"),
    ("/tmp/hyprmasq-bench-hyprland.log", "vm-hyprland.log"),
    ("/tmp/hyprmasq-bench-mcp.log", "vm-mcp.log"),
    ("/tmp/hyprmasq-bench-socat.log", "vm-socat.log"),
    ("/tmp/hyprmasq-bench-events.log", "vm-events.log"),
]:
    fetch(path, name)
write_json("vm-result.json", result)
