import json
import shlex
import time

machine.wait_for_unit("multi-user.target")
runtime = "/run/user/1000"
machine.succeed(f"install -d -o alice -g users -m 700 {runtime}")
machine.succeed(
    "su - alice -c "
    + shlex.quote(
        f"XDG_RUNTIME_DIR={runtime} HYPRLAND_HEADLESS_ONLY=1 "
        "nohup Hyprland --config /etc/hyprmasq-test.lua > /tmp/hyprmasq-hyprland.log 2>&1 &"
    )
)
machine.wait_until_succeeds(f"test -f {runtime}/hypr/*/hyprland.lock", timeout=120)
instance = machine.succeed(
    f"basename $(dirname $(ls {runtime}/hypr/*/hyprland.lock | head -1))"
).strip()
display = machine.succeed(
    f"sed -n '2p' {runtime}/hypr/{instance}/hyprland.lock"
).strip()
ipc = f"XDG_RUNTIME_DIR={runtime} HYPRLAND_INSTANCE_SIGNATURE={instance}"
client_env = f"XDG_RUNTIME_DIR={runtime} WAYLAND_DISPLAY={display}"
plugin = machine.succeed("cat /etc/hyprmasq-plugin").strip()


def alice(command):
    return machine.succeed("su - alice -c " + shlex.quote(command))


def hypr(command):
    return alice(f"{ipc} hyprctl {command}").strip()


def dispatch(expression):
    return hypr("dispatch " + shlex.quote(expression))


def clients():
    return json.loads(hypr("-j clients"))


def client(title):
    return next((c for c in clients() if c["title"] == title), None)


def wait_for(description, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    machine.log(hypr("-j clients"))
    machine.succeed("cat /tmp/hyprmasq-hyprland.log >&2")
    raise AssertionError("Timed out: " + description)


def focus_workspace(number):
    dispatch(f'hl.dsp.focus({{ workspace = "{number}" }})')
    wait_for(
        f"workspace {number}",
        lambda: json.loads(hypr("-j activeworkspace"))["id"] == number,
    )


def spawn(command, log):
    alice(f"{client_env} nohup {command} > /tmp/hyprmasq-{log}.log 2>&1 &")


def foot(title, command="bash --noprofile --norc -i", options="", executable="foot"):
    spawn(f"{executable} -T {title} {options} {command}", title)
    wait_for(title, lambda: client(title) is not None)
    return client(title)["address"]


def snapshot():
    return (
        json.loads(hypr("-j activeworkspace"))["id"],
        json.loads(hypr("-j activewindow")).get("address"),
    )


def screenshot(address, name):
    path = f"/tmp/hyprmasq-{name}.png"
    reply = json.loads(hypr(f"-j hyprmasq screenshot {address} {path}"))
    assert reply["ok"], reply
    machine.succeed(f"test -s {path}")
    return path


def open_socket(address):
    path = hypr(f"hyprmasq eis open {address}")
    assert path == f"{runtime}/hyprmasq/{instance}/{address}.eis", path
    machine.succeed(f"test -S {path}")
    assert machine.succeed(
        f"stat -c %a {runtime}/hyprmasq {runtime}/hyprmasq/{instance}"
    ).split() == ["700", "700"]
    assert machine.succeed(f"stat -c %a {path}").strip() == "600"
    assert {"address": address, "path": path} in json.loads(
        hypr("-j hyprmasq eis list")
    )
    assert json.loads(hypr(f"-j hyprmasq eis open {address}"))["path"] == path
    return path


def send(path, *args):
    alice(
        "hyprmasq-input --socket "
        + shlex.quote(path)
        + " "
        + " ".join(shlex.quote(str(a)) for a in args)
    )


def assert_png(path, color):
    script = f"""
from PIL import Image
im = Image.open({path!r}).convert('RGB')
w, h = im.size
assert w >= 100 and h >= 100, im.size
expected = {color!r}
def background(pixel):
    return all(abs(a - b) <= 10 for a, b in zip(pixel, expected))
assert background(im.getpixel((w // 2, h // 2))), im.getpixel((w // 2, h // 2))
# The terminal prints a white banner in its first row. A flipped screenshot fails both checks.
rows = [y for y in range(h) if any(not background(im.getpixel((x, y))) for x in range(0, w, 2))]
assert rows and max(rows) < h // 4, f"text rows {{min(rows, default=None)}}..{{max(rows, default=None)}} of {{h}}"
"""
    machine.copy_from_machine(path)
    machine.succeed("python3 - <<'PY'\n" + script + "\nPY")


try:
    machine.wait_until_succeeds(
        "su - alice -c " + shlex.quote(f"{ipc} hyprctl version"), timeout=120
    )
    # Output creation can exceed hyprctl's timeout on a slow host.
    machine.execute(
        "su - alice -c "
        + shlex.quote(f"{ipc} hyprctl output create headless HYPRMASQ-TEST")
    )
    wait_for(
        "headless monitor",
        lambda: any(
            m["name"] == "HYPRMASQ-TEST" for m in json.loads(hypr("-j monitors"))
        ),
        timeout=120,
    )
    assert "hyprmasq" in hypr("plugin list")
    assert not hypr("configerrors")

    with subtest("screenshots on hidden workspaces, colors and orientation"):
        colors = [
            ("red", "ff0000", (255, 0, 0)),
            ("green", "00ff00", (0, 255, 0)),
            ("blue", "0000ff", (0, 0, 255)),
        ]
        windows = []
        for n, (name, color, rgb) in enumerate(colors, 1):
            focus_workspace(n)
            command = "sh -c " + shlex.quote(
                "printf '\\033[?25lTOP OF WINDOW\\n'; sleep 900"
            )
            address = foot(
                "hm-" + name,
                command,
                f"-o colors-dark.background={color} -o colors-dark.foreground=ffffff",
            )
            time.sleep(0.5)
            windows.append((name, address, rgb))
        focus_workspace(4)
        visible = foot("hm-visible")
        for name, address, rgb in windows:
            assert_png(screenshot(address, name), rgb)
        assert snapshot() == (4, visible)

    with subtest("socket lifecycle"):
        doomed = windows[0][1]
        path = open_socket(doomed)
        assert hypr(f"hyprmasq eis close {doomed}") == "ok"
        machine.succeed(f"test ! -e {path} && test ! -e {path}.lock")
        path = open_socket(doomed)
        dispatch('hl.dsp.window.close({ window = "address:' + doomed + '" })')
        wait_for("window teardown", lambda: client("hm-red") is None)
        machine.wait_until_succeeds(f"test ! -e {path} && test ! -e {path}.lock")
        assert not json.loads(hypr("-j hyprmasq eis list"))

    with subtest("hidden shell input, focus stability and frame callbacks"):
        focus_workspace(5)
        target = foot("hm-shell")
        time.sleep(1)
        focus_workspace(4)
        before_focus = snapshot()
        before = screenshot(target, "shell-before")
        path = open_socket(target)
        send(path, "type", "echo ok > /tmp/marker", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/marker)" = ok')
        assert snapshot() == before_focus
        time.sleep(1)
        after = screenshot(target, "shell-after")
        machine.fail(f"cmp -s {before} {after}")
        assert snapshot() == before_focus

    with subtest("hidden wev pointer events"):
        focus_workspace(6)
        spawn("stdbuf -oL wev", "wev")
        wait_for("wev", lambda: any(c["class"] == "wev" for c in clients()))
        target = next(c["address"] for c in clients() if c["class"] == "wev")
        focus_workspace(4)
        before_focus = snapshot()
        # Discard events from mapping and the real focus leaving.
        machine.succeed("cp /tmp/hyprmasq-wev.log /tmp/hyprmasq-wev-initial.log")
        initial = len(machine.succeed("cat /tmp/hyprmasq-wev.log"))
        path = open_socket(target)
        send(path, "move", 50, 60, "click", "scroll", 0, 25)
        time.sleep(0.5)
        events = machine.succeed("cat /tmp/hyprmasq-wev.log")[initial:]
        for event in ["wl_pointer", "enter", "motion", "button", "axis"]:
            assert event in events, events
        assert snapshot() == before_focus

    with subtest("two footclient windows on one Wayland connection"):
        spawn("foot --server=/tmp/hyprmasq-foot.sock", "foot-server")
        machine.wait_until_succeeds("test -S /tmp/hyprmasq-foot.sock")
        focus_workspace(7)
        shared_hidden = foot(
            "hm-shared-hidden", executable="footclient -s /tmp/hyprmasq-foot.sock"
        )
        focus_workspace(8)
        shared_visible = foot(
            "hm-shared-visible",
            "sh -c " + shlex.quote("stty -echo; cat > /tmp/hyprmasq-visible-input"),
            executable="footclient -s /tmp/hyprmasq-foot.sock",
        )
        # Both clients report the foot server's pid.
        assert client("hm-shared-hidden")["pid"] == client("hm-shared-visible")["pid"]
        wait_for("shared visible focus", lambda: snapshot() == (8, shared_visible))
        before_focus = snapshot()
        before = screenshot(shared_hidden, "shared-before")
        path = open_socket(shared_hidden)
        send(path, "type", "echo OK > /tmp/shared-marker", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/shared-marker)" = OK')
        time.sleep(1)
        assert snapshot() == before_focus
        machine.succeed(
            "test -f /tmp/hyprmasq-visible-input && test ! -s /tmp/hyprmasq-visible-input"
        )
        after = screenshot(shared_hidden, "shared-after")
        machine.fail(f"cmp -s {before} {after}")
        # Another connection must receive fresh modifier state, with lowercase intact.
        send(path, "type", "echo again > /tmp/shared-again", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/shared-again)" = again')
        assert snapshot() == before_focus
        machine.succeed("test ! -s /tmp/hyprmasq-visible-input")

    with subtest("same-client popup, move, resize and drag protections"):
        focus_workspace(10)
        spawn("hyprmasq-focus-probe", "focus-probe")
        wait_for(
            "probe windows",
            lambda: client("hm-guard-hidden") and client("hm-guard-visible"),
        )
        dispatch(
            'hl.dsp.window.move({ window = "title:^hm-guard-hidden$", workspace = "9", follow = false })'
        )
        dispatch('hl.dsp.focus({ window = "title:^hm-guard-visible$" })')
        dispatch("hl.dsp.cursor.move({ x = 200, y = 200 })")
        time.sleep(0.5)
        assert client("hm-guard-hidden")["workspace"]["id"] == 9
        expected = snapshot()
        original_geometry = (
            client("hm-guard-hidden")["at"],
            client("hm-guard-hidden")["size"],
        )
        path = open_socket(client("hm-guard-hidden")["address"])
        initial = len(machine.succeed("cat /tmp/hyprmasq-focus-probe.log"))
        send(path, "move", 100, 100, "click", "key", 30)
        machine.wait_until_succeeds(
            "grep -q 'drag cancelled' /tmp/hyprmasq-focus-probe.log"
        )
        time.sleep(1)
        events = machine.succeed("cat /tmp/hyprmasq-focus-probe.log")[initial:]
        for request in [
            "requested move and resize",
            "requested popup grab",
            "requested drag",
            "configured popup",
            "key hm-guard-hidden 30 1",
        ]:
            assert request in events, events
        assert "key hm-guard-visible" not in events, events
        assert "unexpected drag enter" not in events, events
        keyboard_enters = [
            line for line in events.splitlines() if line.startswith("keyboard enter")
        ]
        pointer_enters = [
            line for line in events.splitlines() if line.startswith("pointer enter")
        ]
        assert keyboard_enters[-1] == "keyboard enter hm-guard-visible", events
        assert pointer_enters[-1] == "pointer enter hm-guard-visible", events
        assert snapshot() == expected
        assert (
            client("hm-guard-hidden")["at"],
            client("hm-guard-hidden")["size"],
        ) == original_geometry

    with subtest("unload removes all sockets and hooks, reload succeeds"):
        paths = [s["path"] for s in json.loads(hypr("-j hyprmasq eis list"))]
        assert paths
        assert hypr(f"plugin unload {plugin}") == "ok"
        for path in paths:
            machine.succeed(f"test ! -e {path} && test ! -e {path}.lock")
        assert hypr(f"plugin load {plugin}") == "ok"
        assert json.loads(hypr("-j hyprmasq eis list")) == []
        assert not hypr("configerrors")

    with subtest("compositor rejected the emulated move and resize serials"):
        # Hyprland buffers its log file and only flushes it reliably on exit.
        pid = machine.succeed(f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock").strip()
        dispatch("hl.dsp.exit()")
        machine.wait_until_fails(f"test -e /proc/{pid}", timeout=60)
        hyprland_log = machine.succeed(f"cat {runtime}/hypr/{instance}/hyprland.log")
        assert "Ignoring xdg_toplevel.move with an invalid serial" in hyprland_log
        assert "Ignoring xdg_toplevel.resize with an invalid serial" in hyprland_log
finally:
    machine.succeed("cat /tmp/hyprmasq-hyprland.log >&2")
    machine.execute(f"cp {runtime}/hypr/{instance}/hyprland.log /tmp/hyprmasq-hyprland-full.log")
    for artifact in ["/tmp/hyprmasq-hyprland.log", "/tmp/hyprmasq-hyprland-full.log", "/tmp/hyprmasq-wev.log"]:
        if machine.execute(f"test -e {artifact}")[0] == 0:
            machine.copy_from_machine(artifact)
