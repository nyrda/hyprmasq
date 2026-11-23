# The test script of one benchmark run. bench/run.py starts the test driver with HYPRMASQ_BENCH_OUT set to the
# run's directory on the host, and the two talk through files there:
#
#   ready.json      written here once the task is set up and the backend's MCP server listens on vsock port 5000
#   agent-done      written by run.py when the agent has finished
#   vm-result.json  written here after grading; the driver then stops the VM
#
# TASK_DIR, VARIANT, the task's variant or "", BACKEND, hyprmasq, cua, hypruse or computer-use-linux, and BROWSER, the
# user's browser in the tasks that have one, firefox or chromium (start_browser), and DESKTOP, bare or omarchy, are set by
# default.nix before this file runs. With cua, Cua's plugin is loaded and enabled as its README does it, and `cua-driver mcp` serves the port; with
# hypruse or computer-use-linux, its MCP server does, and with computer-use-linux the desktop portal's session starts.
# ready.json keeps the readiness report of the tool's own doctor command, for the backends that have one, and the
# versions of Hyprland and of Cua Driver.
# The task's setup.py defines prepare() and collect(), and may define HYPRLAND_ENV, variables for Hyprland's
# environment and so for every app it starts, and during(stop), which runs in a thread while the agent works, until
# the threading.Event stop is set: the user's own activity.
# With HYPRMASQ_BENCH_MEMORY_SECONDS, as calibrate.py sets it, the VM's memory in use is sampled that often while the
# agent works, and vm-result.json holds its peak; not for a task with during(), since the driver's shell takes one
# command at a time.
# With HYPRMASQ_BENCH_SHELL set, as capability.py sets it, a file probe-N.sh that the host writes to the run's directory
# runs in the VM as root, and its status and output come back as probe-N.json, so that a check without an agent can read
# an app's state between its actions.
import base64
import json
import os
import shlex
import threading
import time

from test_driver.machine import CHAR_TO_KEY

CONTAINER = globals().get("CONTAINER", False)
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
# hyprmasq reads no accessibility, so its runs keep the browser as it was, but on Omarchy's desktop, a comparison's, where
# every backend runs the same browser.
CHROMIUM_ACCESSIBILITY = BACKEND in ("cua", "hypruse", "computer-use-linux") or DESKTOP == "omarchy"


def out_path(name):
    return os.path.join(OUT, name)


def write_json(name, data):
    path = out_path(name)
    part = path + ".part"
    with open(part, "w") as f:
        json.dump(data, f, indent=2)
    os.rename(part, path)


def alice(command):
    return machine.succeed("su - alice -c " + shlex.quote(command))


def fetch(path, name):
    """Copies a file from the VM into the run's directory, if it exists."""
    status, data = machine.execute(f"test -f {shlex.quote(path)} && base64 -w0 {shlex.quote(path)}")
    if status == 0:
        with open(out_path(name), "wb") as f:
            f.write(base64.b64decode(data))


# One command at a time in the driver's shell, for what uses it from two threads: a check's shell (answer_shell) and a
# task's user, in during().
SHELL_LOCK = threading.Lock()

exec(open(os.path.join(TASK_DIR, "setup.py")).read())

machine.start()
machine.wait_for_unit("multi-user.target")
# The autologin starts alice's manager some time after multi-user.target: wait_for_unit would fail on a unit that has
# not been queued yet. In a container, getty@tty1 does not run, so start user@1000.service explicitly if needed.
if CONTAINER:
    machine.succeed("systemctl start user@1000.service")
machine.wait_until_succeeds("systemctl is-active user@1000.service", timeout=300)
if CONTAINER:
    # Ensure systemd --user has dconf/gsettings paths from alice's profile before at-spi-dbus-bus activates.
    alice(f"XDG_RUNTIME_DIR={RUNTIME} DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} "
          "systemctl --user import-environment GIO_EXTRA_MODULES XDG_DATA_DIRS XDG_CONFIG_DIRS")
# Enable org.a11y.Status before prepare() starts apps: at-spi2-core 2.62 starts with IsEnabled=false until enabled or a
# listener connects, which would otherwise happen after Chromium has already launched.
alice(f"XDG_RUNTIME_DIR={RUNTIME} DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} "
      "busctl --user set-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled b true")
machine.succeed(f"install -d -o alice -g users -m 700 {RUNTIME}")
parent_env = "HYPRLAND_HEADLESS_ONLY=1 "
if CONTAINER:
    # In a container, Hyprland runs nested in labwc on the host's GPU render node, with hyprmasq-parent-seat
    # providing a virtual keyboard/pointer for user input (such as Forgejo's during()).
    parent_fifo = f"{RUNTIME}/parent-seat.fifo"
    alice(
        f"XDG_RUNTIME_DIR={RUNTIME} WLR_BACKENDS=headless WLR_HEADLESS_OUTPUTS=1 WLR_RENDERER=gles2 "
        "WLR_RENDER_DRM_DEVICE=$(ls /dev/dri/renderD* | head -1) WLR_LIBINPUT_NO_DEVICES=1 "
        "LABWC_UPDATE_ACTIVATION_ENV=0 nohup labwc > /tmp/hyprmasq-bench-labwc.log 2>&1 &"
    )
    machine.wait_until_succeeds(f"test -S {RUNTIME}/wayland-0", timeout=60)
    alice(f"mkfifo -m 600 {parent_fifo}")
    alice(
        f"XDG_RUNTIME_DIR={RUNTIME} WAYLAND_DISPLAY=wayland-0 "
        f"nohup hyprmasq-parent-seat {parent_fifo} > /tmp/hyprmasq-bench-parent-seat.log 2>&1 &"
    )
    machine.wait_until_succeeds("grep -q ' ready$' /tmp/hyprmasq-bench-parent-seat.log", timeout=60)
    parent_env = "WAYLAND_DISPLAY=wayland-0 "

    _qemu_key_codes = {
        **{letter: code for row, first in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44))
           for code, letter in enumerate(row, first)},
        **{digit: code for code, digit in enumerate("1234567890", 2)},
        "esc": 1, "backspace": 14, "tab": 15, "ret": 28, "ctrl": 29, "shift": 42, "shift_r": 54, "alt": 56, "spc": 57,
        "kp_8": 72, "ctrl_r": 97, "alt_r": 100, "up": 103, "left": 105, "right": 106, "down": 108, "insert": 110,
        "delete": 111, "meta_l": 125, "meta_r": 126,
    }

    def _container_send_key(key, delay=0.01):
        mapped = CHAR_TO_KEY.get(key, key)
        codes = " ".join(
            str(int(part, 16) if part.startswith("0x") else _qemu_key_codes[part])
            for part in mapped.split("-")
        )
        with SHELL_LOCK:
            machine.succeed(f"echo 'sendkey 0 {codes}' > {parent_fifo}")
        if delay:
            time.sleep(delay)

    def _container_send_chars(chars, delay=0.01):
        for ch in chars:
            _container_send_key(ch, delay=delay)

    machine.send_key = _container_send_key
    machine.send_chars = _container_send_chars

alice(
    f"XDG_RUNTIME_DIR={RUNTIME} DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} {parent_env}"
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
if DESKTOP == "omarchy":
    # What apps get in Omarchy's session, whose portal settings and file chooser go by it.
    client_env += " XDG_CURRENT_DESKTOP=Hyprland XDG_SESSION_TYPE=wayland"
# For a check without an agent: what apps get beside it.
client_env += " " + os.environ.get("HYPRMASQ_BENCH_APP_ENV", "")


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
        # A browser whose user had turned on remote debugging before the agent came (capability.py, run.py's
        # --chromium-options).
        options += " " + os.environ.get("HYPRMASQ_BENCH_CHROMIUM_OPTIONS", "")
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


# The workspaces of Omarchy's desktop that the task's named ones became, for `--desktop omarchy`: numbered, from 2 up,
# which its binds reach, as the user is on 1. What the task calls them does not matter to it or to the measures.
NUMBERED = {}


def move(address, workspace):
    """Parks a window on one of the task's hidden workspaces. On Omarchy's desktop it gets a numbered one in its place."""
    if DESKTOP == "omarchy":
        workspace = NUMBERED.setdefault(workspace, str(len(NUMBERED) + 2))
    target = f'"{workspace}"' if workspace.isdigit() else f'"name:{workspace}"'
    hypr("dispatch " + shlex.quote(
        f'hl.dsp.window.move({{ window = "address:{address}", workspace = {target}, follow = false }})'
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
if CONTAINER:
    # labwc sends keys from hyprmasq-parent-seat to Hyprland's window, which exists only while WAYLAND-1 is enabled.
    # Mirroring HYPRMASQ-BENCH keeps WAYLAND-1 out of Hyprland's layout, active monitors and workspaces.
    hypr("eval " + shlex.quote(
        'hl.monitor({ output = "WAYLAND-1", mode = "640x480", position = "0x0", scale = "1", mirror = "HYPRMASQ-BENCH" })'
    ))
    wait_for(
        "the mirror in labwc",
        lambda: any(
            m["name"] == "WAYLAND-1" and m["mirrorOf"] != "none" for m in json.loads(hypr("-j monitors all"))
        ),
        timeout=30,
    )


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
if BACKEND == "computer-use-linux" or DESKTOP == "omarchy":
    # The desktop portal's services start from alice's systemd user manager and need the session's variables and
    # graphical-session.target, as in the Forgejo task's setup.py.
    alice(f"export {session_env}; systemctl --user import-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP "
          "HYPRLAND_INSTANCE_SIGNATURE XDG_SESSION_TYPE && systemctl --user start hyprmasq-bench-session.target")
if BACKEND == "computer-use-linux":
    # The consent its README says the Screenshot portal asks for on the first capture: xdg-desktop-portal asks once
    # "Allow Apps to Take Screenshots?" for the apps it knows no ID of, such as a server an agent starts, and keeps the
    # answer. Here the user has answered yes, as they would have before the agent's first screenshot.
    alice(f"DBUS_SESSION_BUS_ADDRESS={SESSION_BUS} busctl --user call {PERMISSION_STORE} SetPermission sbssas "
          "screenshot true screenshot '' 1 yes")


def doctor():
    """The tool's own readiness report, `hypruse doctor`, `computer-use-linux doctor` or `cua-driver doctor`, with the
    session's variables, as its documentation has a user check the setup with it; None for hyprmasq. hypruse and Cua print
    lines, and computer-use-linux JSON, of which its readiness is kept."""
    if BACKEND == "hyprmasq":
        return None
    merge = " 2>&1" if BACKEND != "computer-use-linux" else ""
    tool = "cua-driver" if BACKEND == "cua" else BACKEND
    status, out = machine.execute(
        f"su - alice -c {shlex.quote(f'{session_env} CUA_DRIVER_RS_ENABLE_WAYLAND=1 CUA_DRIVER_RS_TELEMETRY_ENABLED=false {tool} doctor')}{merge}"
    )
    if BACKEND != "computer-use-linux":
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


def cursor_position():
    """Where the user's pointer is: the disturbance's measures hold the focus and the windows, and this says whether a
    tool's input moved the pointer, which vm-result.json keeps from before and after the agent."""
    status, out = machine.execute(f"su - alice -c {shlex.quote(f'{ipc} hyprctl -j cursorpos')}")
    return json.loads(out) if status == 0 else None


def shoot_windows(prefix, windows=True):
    """Screenshots of the screen into the run's directory, and with windows of every window where hyprmasq's plugin can
    take them, hidden ones too. A screenshot of a hidden window lets it draw, so the start takes the screen alone, for
    every backend (show_task_workspaces)."""
    for c in clients() if BACKEND == "hyprmasq" and windows else []:
        path = f"/tmp/hyprmasq-bench-shot-{c['address']}.png"
        reply = json.loads(hypr(f"-j hyprmasq screenshot {c['address']} {path}"))
        if reply.get("ok"):
            fetch(path, f"{prefix}-{c['class'] or 'window'}-{c['address']}.png")
    alice(f"{client_env} grim /tmp/hyprmasq-bench-screen.png")
    fetch("/tmp/hyprmasq-bench-screen.png", f"{prefix}-screen.png")


def workspace_target(name):
    return f'"{name}"' if name.isdigit() else f'"name:{name}"'


def show_task_workspaces(timeout=30, interval=0.5):
    """Shows each workspace that holds a task window until the screen stops changing, then the user's workspace again,
    with the focus back on the window that had it, the same for every backend: the terminal, or the window the task
    gave the user, as Forgejo's notes, where its user types. A window moved away right after it mapped
    waits for a frame callback that a hidden workspace does not send, so it would start blank or at the size it had
    before it moved, until some input or a capture let it draw; a user's windows on other workspaces have been shown.
    The screen is stable when two captures `interval` apart are the same. Returns what each workspace took, for
    ready.json. Hyprland's events of these switches come before the agent starts, which the measures leave out."""
    user = json.loads(hypr("-j activeworkspace"))
    focused = json.loads(hypr("-j activewindow")).get("address")
    names = []
    for c in clients():
        name = c["workspace"]["name"]
        if name != user["name"] and not name.startswith("special") and name not in names:
            names.append(name)
    shown = []
    for name in names:
        hypr("dispatch " + shlex.quote(f"hl.dsp.focus({{ workspace = {workspace_target(name)} }})"))
        wait_for(f"workspace {name} shown", lambda: json.loads(hypr("-j activeworkspace"))["name"] == name)
        started, stable = time.monotonic(), False
        alice(f"{client_env} grim /tmp/hyprmasq-bench-settle-a.png")
        while time.monotonic() - started < timeout:
            time.sleep(interval)
            alice(f"{client_env} grim /tmp/hyprmasq-bench-settle-b.png")
            if machine.execute("cmp -s /tmp/hyprmasq-bench-settle-a.png /tmp/hyprmasq-bench-settle-b.png")[0] == 0:
                stable = True
                break
            alice("mv /tmp/hyprmasq-bench-settle-b.png /tmp/hyprmasq-bench-settle-a.png")
        shown.append({"workspace": name, "seconds": round(time.monotonic() - started, 1), "stable": stable})
    hypr("dispatch " + shlex.quote(f"hl.dsp.focus({{ workspace = {workspace_target(user['name'])} }})"))
    wait_for("the user's workspace shown", lambda: json.loads(hypr("-j activeworkspace"))["name"] == user["name"])
    if focused:
        hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{focused}" }})'))
        wait_for("the user's window focused", lambda: json.loads(hypr("-j activewindow")).get("address") == focused)
    return shown


alice(
    f"nohup /etc/hyprmasq-bench/events {RUNTIME}/hypr/{instance}/.socket2.sock /tmp/hyprmasq-bench-events.log "
    "> /dev/null 2>&1 &"
)

# The user's own work stays on the visible workspace for the whole run: a terminal that redraws every second.
alice(f"{client_env} nohup foot -T user-terminal top -d 1 > /tmp/hyprmasq-bench-foot.log 2>&1 &")
wait_for("user terminal", lambda: any(c["title"] == "user-terminal" for c in clients()))

task_window = prepare()

# Options of hyprmasq-mcp for this run (run.py's --mcp-args), which its launcher reads.
if os.environ.get("HYPRMASQ_BENCH_MCP_ARGS"):
    machine.succeed("cat > /tmp/hyprmasq-bench-mcp-args <<'EOF'\n" + os.environ["HYPRMASQ_BENCH_MCP_ARGS"] + "\nEOF")
if os.environ.get("HYPRMASQ_BENCH_HYPRUSE_MODE"):
    machine.succeed(
        "cat > /tmp/hyprmasq-bench-hypruse-mode <<'EOF'\n" + os.environ["HYPRMASQ_BENCH_HYPRUSE_MODE"] + "\nEOF"
    )
mcp_socket = None
if CONTAINER:
    machine.succeed("chmod 755 /tmp/shared && install -d -o alice -g users -m 755 /tmp/shared/mcp")
    if BACKEND == "hypruse":
        # Share hypruse's file-mode screenshot directory with the host so Claude Code can use Read natively.
        machine.succeed(
            f"install -d -o alice -g users -m 755 /tmp/shared/hypruse && "
            f"rm -rf {RUNTIME}/hypruse && ln -s /tmp/shared/hypruse {RUNTIME}/hypruse"
        )
        shots_link = out_path("hypruse-shots")
        if os.path.lexists(shots_link):
            os.unlink(shots_link)
        os.symlink(os.path.join(str(machine.shared_dir), "hypruse"), shots_link)
    alice(
        "nohup socat UNIX-LISTEN:/tmp/shared/mcp/mcp.sock,fork,reuseaddr,mode=0666 EXEC:/etc/hyprmasq-bench/mcp "
        "> /tmp/hyprmasq-bench-socat.log 2>&1 &"
    )
    machine.wait_until_succeeds("test -S /tmp/shared/mcp/mcp.sock", timeout=30)
    mcp_socket = out_path("mcp.sock")
    if os.path.lexists(mcp_socket):
        os.unlink(mcp_socket)
    os.symlink(os.path.join(str(machine.shared_dir), "mcp", "mcp.sock"), mcp_socket)
else:
    alice(
        f"nohup socat VSOCK-LISTEN:{MCP_PORT},fork,reuseaddr EXEC:/etc/hyprmasq-bench/mcp "
        "> /tmp/hyprmasq-bench-socat.log 2>&1 &"
    )
    machine.wait_until_succeeds(f"ss -H -l --vsock | grep -q ':{MCP_PORT}'", timeout=30)

# What ran, which the source commit in source.json does not tell of Cua: the commit gives Hyprland, and the bench's
# own checkout the driver.
versions = {"hyprland": json.loads(hypr("-j version")).get("version")}
if BACKEND == "cua":
    versions["cua_driver"] = alice("cua-driver --version").strip()
# What the VM holds of its driver: the store paths of the server and of Cua's plugin.
identity = {"server": machine.succeed("readlink -f /etc/hyprmasq-bench/mcp").strip(),
            "cua_plugin": machine.succeed("cat /etc/hyprmasq-bench/cua-plugin").strip() or None}
start_state = show_task_workspaces()
before = snapshot()
cursor_before = cursor_position()
shoot_windows("start", windows=False)
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
    "versions": versions,
    "identity": identity,
    "desktop": DESKTOP,
    "container": CONTAINER,
    "mcp_socket": mcp_socket,
    "start_state": start_state,
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


def answer_shell(answered):
    """Runs the scripts the host wrote for a check without an agent, each once."""
    for name in sorted(os.listdir(OUT)):
        if name.startswith("probe-") and name.endswith(".sh") and name not in answered:
            answered.add(name)
            with open(out_path(name)) as f:
                with SHELL_LOCK:
                    status, output = machine.execute(f.read(), timeout=600)
            result = out_path(name.removesuffix(".sh") + ".json")
            with open(result + ".part", "w") as f:
                json.dump({"status": status, "output": output}, f)
            os.rename(result + ".part", result)


SHELL = bool(os.environ.get("HYPRMASQ_BENCH_SHELL"))
answered = set()
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
    if SHELL:
        answer_shell(answered)
    time.sleep(0.1 if SHELL else 0.5)

stop_user.set()
if user_thread:
    user_thread.join(timeout=30)
after = snapshot()
shoot_windows("end")
result = {
    "user_before": before,
    "user_after": after,
    "user_same_at_end": before == after,
    "cursor_before": cursor_before,
    "cursor_after": cursor_position(),
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
