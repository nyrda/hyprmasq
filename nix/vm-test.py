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
# Hyprland starts XWayland on the first free display, the only one at this point.
machine.wait_until_succeeds("ls /tmp/.X11-unix/X*", timeout=120)
x_display = ":" + machine.succeed("ls /tmp/.X11-unix").split()[0].removeprefix("X")
x_env = f"{client_env} DISPLAY={x_display}"
satellite_env = f"{client_env} DISPLAY=:5"
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


def xterm(title, command="bash --noprofile --norc -i", options="", env=None):
    spawn(f"env {env or x_env} xterm -T {title} {options} -e {command}", title)
    wait_for(title, lambda: client(title) is not None)
    return client(title)["address"]


def xev(title, log, env=None):
    spawn(f"env {env or x_env} stdbuf -oL xev -name {title}", log)
    wait_for(title, lambda: client(title) is not None)
    return client(title)["address"]


def log_since(log, offset):
    return machine.succeed(f"cat /tmp/hyprmasq-{log}.log")[offset:]


def log_size(log):
    return len(machine.succeed(f"cat /tmp/hyprmasq-{log}.log"))


def send_refused(path, *args):
    status, output = machine.execute(
        "su - alice -c "
        + shlex.quote(
            "hyprmasq-input --socket "
            + shlex.quote(path)
            + " "
            + " ".join(shlex.quote(str(a)) for a in args)
            + " 2>&1"
        )
    )
    assert status == 1 and "paused input" in output, (status, output)


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


def hypr_background(command, output):
    alice(f"rm -f {output}; {ipc} nohup hyprctl {command} > {output} 2>&1 &")


def batch(commands, flags=""):
    # Some replies end in blank lines, which run into the separator.
    reply = hypr(f"{flags} --batch {shlex.quote(commands)}")
    return [part.strip() for part in reply.split("\n\n\n")]


def fifo(path):
    machine.succeed(f"rm -f {path}")
    alice(f"mkfifo {path}")


def open_socket(address):
    path = hypr(f"hyprmasq eis open {address}")
    assert path == f"{runtime}/hyprmasq/{instance}/{address}.eis", path
    machine.succeed(f"test -S {path}")
    assert machine.succeed(
        f"stat -c %a {runtime}/hyprmasq {runtime}/hyprmasq/{instance}"
    ).split() == ["700", "700"]
    assert machine.succeed(f"stat -c %a {path}").strip() == "600"
    assert {"address": address, "path": path, "force": False} in json.loads(
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


def capture_output(name, before=""):
    """Captures the whole monitor with grim, after the shell command `before`."""
    path = f"/tmp/hyprmasq-{name}.png"
    alice(f"{before} {client_env} grim {path}")
    machine.copy_from_machine(path)
    return path


def count_pixels(path, box, condition):
    """Counts pixels within box (x0, y0, x1, y1) whose r, g, b satisfy the Python expression condition."""
    script = f"""
from PIL import Image
im = Image.open({path!r}).convert('RGB')
x0, y0, x1, y1 = {box!r}
def matches(r, g, b):
    return {condition}
print(sum(
    matches(*im.getpixel((x, y)))
    for x in range(max(0, x0), min(im.width, x1))
    for y in range(max(0, y0), min(im.height, y1))
))
"""
    return int(machine.succeed("python3 - <<'PY'\n" + script + "\nPY").strip())


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

    with subtest("screenshots are encoded while Hyprland keeps running"):
        red, green = windows[0][1], windows[1][1]
        # Writing to a FIFO holds the encoder until the FIFO is read.
        fifo("/tmp/hm-fifo.png")
        hypr_background(
            f"-j hyprmasq screenshot {red} /tmp/hm-fifo.png", "/tmp/hm-fifo-reply"
        )
        time.sleep(1)
        assert json.loads(hypr("-j activeworkspace"))["id"] == 4
        machine.succeed("test ! -s /tmp/hm-fifo-reply")
        machine.succeed("cat /tmp/hm-fifo.png > /tmp/hm-fifo-copy.png")
        machine.wait_until_succeeds("test -s /tmp/hm-fifo-reply", timeout=30)
        reply = json.loads(machine.succeed("cat /tmp/hm-fifo-reply"))
        assert reply["ok"] and reply["path"] == "/tmp/hm-fifo.png", reply
        assert_png("/tmp/hm-fifo-copy.png", (255, 0, 0))
        # A full disk shows up when the file is closed.
        reply = json.loads(hypr(f"-j hyprmasq screenshot {red} /dev/full"))
        assert not reply["ok"] and "/dev/full" in reply["error"], reply

        # A batch answers once every screenshot in it is written, with the other replies in place.
        parts = batch(
            f"hyprmasq screenshot {red} /tmp/hm-batch-red.png; activeworkspace; "
            f"hyprmasq screenshot {green} /tmp/hm-batch-green.png"
        )
        assert len(parts) == 3 and parts[0] == "ok" and parts[2] == "ok", parts
        assert parts[1].startswith("workspace ID 4 "), parts
        assert_png("/tmp/hm-batch-red.png", (255, 0, 0))
        assert_png("/tmp/hm-batch-green.png", (0, 255, 0))
        parts = [
            json.loads(part)
            for part in batch(
                f"hyprmasq screenshot {green} /tmp/hm-batch-json.png; activeworkspace",
                "-j",
            )
        ]
        assert parts[0]["ok"] and parts[0]["path"] == "/tmp/hm-batch-json.png", parts
        assert parts[1]["id"] == 4, parts
        assert_png("/tmp/hm-batch-json.png", (0, 255, 0))

    with subtest("screenshot regions in logical pixels"):
        green = windows[1][1]
        white = "r > 200 and g > 200 and b > 200"

        def region_shot(name, *region):
            path = f"/tmp/hyprmasq-{name}.png"
            reply = json.loads(hypr(f"-j hyprmasq screenshot {green} {path} " + " ".join(map(str, region))))
            assert reply["ok"], reply
            machine.copy_from_machine(path)
            return path, reply

        def region(x, y, width, height):
            return {"x": x, "y": y, "width": width, "height": height}

        _, reply = region_shot("region-full")
        assert reply["scale"] == 1 and reply["region"] == region(0, 0, 640, 480), reply
        assert (reply["width"], reply["height"]) == (640, 480), reply
        # The white text is at the left of the first row. A readback from another part of the window finds it in the
        # other regions instead.
        top, reply = region_shot("region-top", 0, 0, 200, 30)
        assert (reply["width"], reply["height"]) == (200, 30) and reply["region"] == region(0, 0, 200, 30), reply
        assert count_pixels(top, (0, 0, 200, 30), white) > 20
        for name, x, y in [("region-bottom", 0, 440), ("region-right", 440, 0)]:
            path, _ = region_shot(name, x, y, 200, 30)
            assert count_pixels(path, (0, 0, 200, 30), "g > 200 and r < 60 and b < 60") == 200 * 30
        _, reply = region_shot("region-clamped", 600, 450, 100, 100)
        assert (reply["width"], reply["height"]) == (40, 30) and reply["region"] == region(600, 450, 40, 30), reply
        for args, message in [
            ("640 0 10 10", "outside"),
            ("0 0 0 10", "positive size"),
            ("0 0 1.5 10", "whole logical pixels"),
            ("0 0 10", "usage"),
        ]:
            reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-bad-region.png {args}"))
            assert not reply["ok"] and message in reply["error"], (args, reply)
        machine.succeed("test ! -e /tmp/hm-bad-region.png")

        # At scale 2 the same logical region has twice the pixels.
        def set_scale(scale):
            hypr("eval " + shlex.quote(
                f'hl.monitor({{ output = "HYPRMASQ-TEST", mode = "640x480@60", position = "0x0", scale = "{scale}" }})'
            ))
            wait_for(f"scale {scale}", lambda: client("hm-green")["size"] == [640 // scale, 480 // scale])
            time.sleep(0.5)

        set_scale(2)
        top, reply = region_shot("region-scale2", 0, 0, 100, 15)
        assert reply["scale"] == 2 and (reply["width"], reply["height"]) == (200, 30), reply
        assert reply["region"] == region(0, 0, 100, 15), reply
        assert count_pixels(top, (0, 0, 200, 30), white) > 20
        # At a fractional scale the region widens to whole device pixels, and the reply names what was captured:
        # device pixels 1 to 13 of 1.25 per logical pixel.
        set_scale(1.25)
        _, reply = region_shot("region-fractional", 1, 1, 10, 10)
        assert reply["scale"] == 1.25 and (reply["width"], reply["height"]) == (13, 13), reply
        assert all(abs(reply["region"][k] - v) < 1e-9 for k, v in region(0.8, 0.8, 10.4, 10.4).items()), reply
        set_scale(1)

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

    with subtest("typing tags and characters with the active keymap"):
        def keymap(name):
            return any(name in k["active_keymap"] for k in json.loads(hypr("-j devices"))["keyboards"])

        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
        wait_for("German keymap", lambda: keymap("German"))
        # Started after the switch, so the shell reads the German keymap from the start.
        focus_workspace(18)
        target = foot("hm-typed", "env LC_ALL=C.UTF-8 bash --noprofile --norc -i")
        time.sleep(1)
        focus_workspace(4)
        before_focus = snapshot()
        path = open_socket(target)
        # Y and Z swap places, AltGr and the ISO key between Shift and Y give @ € | { >, ^ is a dead key followed by
        # Space. Ctrl+U cuts the line and Ctrl+Y pastes it back, which only works if the Y key of the layout is used.
        send(path, "type", 'printf "%s\\n" "zy@€äß|{^" > /tmp/typed-layout<shortcut:ctrl+u><shortcut:ctrl+y><enter>')
        machine.wait_until_succeeds("test -s /tmp/typed-layout")
        assert machine.succeed("cat /tmp/typed-layout") == "zy@€äß|{^\n"
        # Escaped and unknown tags are typed as text. Named keys, keysyms and repeat counts edit the line.
        send(path, "type", 'xecho "\\<tab> <div></b>" ok<left:2>X<End> > /tmp/typed-keys<home><delete><settle><Return>')
        machine.wait_until_succeeds("test -s /tmp/typed-keys")
        assert machine.succeed("cat /tmp/typed-keys") == "<tab> <div></b> Xok\n"
        # Invalid text is rejected before anything is sent.
        for text, error in [
            ("echo partial > /tmp/typed-partial☃<enter>", "cannot type U+2603"),
            ("echo partial > /tmp/typed-partial<wait:soon><enter>", "<wait:MS>"),
            ("echo partial > /tmp/typed-partial<tab:0>", "repeat count"),
            ("<shortcut:hyper+x>", "unknown shortcut modifier"),
        ]:
            status, output = machine.execute(
                "su - alice -c " + shlex.quote(f"hyprmasq-input --socket {shlex.quote(path)} type {shlex.quote(text)} 2>&1")
            )
            assert status == 2 and error in output, (text, status, output)
        send(path, "type", "echo clean > /tmp/typed-clean<enter>")
        machine.wait_until_succeeds("test -s /tmp/typed-clean")
        assert machine.succeed("cat /tmp/typed-clean") == "clean\n"
        machine.succeed("! ls /tmp/typed-partial*")
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {target}") == "ok"
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
        wait_for("US keymap", lambda: keymap("English"))

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

    with subtest("XWayland screenshots on a hidden workspace"):
        focus_workspace(11)
        command = "sh -c " + shlex.quote("printf '\\033[?25lTOP OF WINDOW\\n'; sleep 900")
        x_red = xterm("hm-x-red", command, "-bg '#ff0000' -fg '#ffffff'")
        assert client("hm-x-red")["xwayland"], client("hm-x-red")
        time.sleep(0.5)
        focus_workspace(4)
        assert_png(screenshot(x_red, "x-red"), (255, 0, 0))
        assert snapshot() == (4, visible)

    with subtest("XWayland hidden shell input while a Wayland window is active"):
        focus_workspace(12)
        x_shell = xterm("hm-x-shell")
        time.sleep(1)
        focus_workspace(4)
        before_focus = snapshot()
        before = screenshot(x_shell, "x-shell-before")
        x_shell_path = open_socket(x_shell)
        send(x_shell_path, "type", "echo x11 > /tmp/x11-marker", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/x11-marker)" = x11', timeout=30)
        assert snapshot() == before_focus
        time.sleep(1)
        after = screenshot(x_shell, "x-shell-after")
        machine.fail(f"cmp -s {before} {after}")
        assert snapshot() == before_focus

    with subtest("XWayland pointer events reach a hidden window under a visible one"):
        focus_workspace(13)
        hidden_xev = xev("hm-x-xev", "x-xev")
        # A visible X11 window beside the active foot, stacked above the hidden one where they overlap.
        focus_workspace(4)
        xev("hm-x-cover", "x-cover")
        dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
        wait_for("foot focus", lambda: snapshot() == (4, visible))
        cover = client("hm-x-cover")
        before_focus = snapshot()
        hidden_offset, cover_offset = log_size("x-xev"), log_size("x-cover")
        x, y = cover["at"][0] + 20, cover["at"][1] + 40
        path = open_socket(hidden_xev)
        send(path, "move", x, y, "click", "scroll", 0, 30)
        time.sleep(0.5)
        events = log_since("x-xev", hidden_offset)
        for event in ["MotionNotify", f"({x},{y})", "ButtonPress", "ButtonRelease", "button 5"]:
            assert event in events, events
        covered = log_since("x-cover", cover_offset)
        assert "ButtonPress" not in covered, covered + "\n----\n" + events
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {hidden_xev}") == "ok"

    with subtest("XWayland input is refused while another X11 window is active"):
        focus_workspace(14)
        x_active = xterm(
            "hm-x-active",
            "sh -c " + shlex.quote("stty -echo; cat > /tmp/hyprmasq-x-active-input"),
        )
        wait_for("xterm focus", lambda: snapshot() == (14, x_active))
        machine.wait_until_succeeds("test -f /tmp/hyprmasq-x-active-input", timeout=30)
        before_focus = snapshot()
        send_refused(x_shell_path, "type", "echo refused > /tmp/x11-refused", "key", 28)
        time.sleep(1)
        machine.succeed("test ! -e /tmp/x11-refused && test ! -s /tmp/hyprmasq-x-active-input")
        assert snapshot() == before_focus

        # The active window itself may always be driven.
        path = open_socket(x_active)
        send(path, "type", "same", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/hyprmasq-x-active-input)" = same', timeout=30)
        assert snapshot() == before_focus

        # force drives the hidden window anyway, and the active one still gets nothing.
        assert hypr(f"hyprmasq eis open {x_shell} force") == x_shell_path
        assert {"address": x_shell, "path": x_shell_path, "force": True} in json.loads(
            hypr("-j hyprmasq eis list")
        )
        send(x_shell_path, "type", "echo forced > /tmp/x11-forced", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/x11-forced)" = forced', timeout=30)
        time.sleep(1)
        assert machine.succeed("cat /tmp/hyprmasq-x-active-input") == "same\n"
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis open {x_shell}") == x_shell_path
        assert hypr(f"hyprmasq eis close {x_active}") == "ok"

    with subtest("a driven X11 app cannot activate itself"):
        focus_workspace(4)
        before_focus = snapshot()
        xid = alice(f"env {x_env} xdotool search --name '^hm-x-shell$'").split()[0]
        alice(f"env {x_env} xdotool windowactivate {xid} || true")
        time.sleep(1)
        assert snapshot() == before_focus
        # The same request works once nothing drives the app.
        assert hypr(f"hyprmasq eis close {x_shell}") == "ok"
        alice(f"env {x_env} xdotool windowactivate {xid} || true")
        wait_for("activation", lambda: snapshot()[1] == x_shell)

    with subtest("X11 apps learn they are not active after input"):
        # Wine keeps its window in the foreground until _NET_ACTIVE_WINDOW changes,
        # so the plugin writes it again when it gives X focus back and when it
        # refuses an activation request.
        focus_workspace(4)
        before_focus = snapshot()
        spawn(
            f"env {x_env} stdbuf -oL xprop -spy -root _NET_ACTIVE_WINDOW",
            "x-active-spy",
        )
        machine.wait_until_succeeds(
            "grep -q _NET_ACTIVE_WINDOW /tmp/hyprmasq-x-active-spy.log"
        )

        def announcements():
            return int(machine.succeed("wc -l < /tmp/hyprmasq-x-active-spy.log"))

        count = announcements()
        path = open_socket(x_shell)
        send(path, "type", "true", "key", 28)
        wait_for(
            "announcement after input", lambda: announcements() > count, timeout=10
        )
        assert snapshot() == before_focus

        # A click takes X focus as typing does, and gives it back the same way.
        count = announcements()
        send(path, "move", 20, 20, "click")
        wait_for(
            "announcement after a click", lambda: announcements() > count, timeout=10
        )
        assert snapshot() == before_focus

        count = announcements()
        xid = alice(f"env {x_env} xdotool search --name '^hm-x-shell$'").split()[0]
        alice(f"env {x_env} xdotool windowactivate {xid} || true")
        wait_for(
            "announcement after activation", lambda: announcements() > count, timeout=10
        )
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {x_shell}") == "ok"
        machine.execute("pkill -f 'xprop -spy'")

    with subtest("xwayland-satellite windows"):
        spawn("xwayland-satellite :5", "satellite")
        machine.wait_until_succeeds("test -S /tmp/.X11-unix/X5", timeout=60)
        focus_workspace(15)
        sat_shell = xterm("hm-sat-shell", env=satellite_env)
        assert not client("hm-sat-shell")["xwayland"], client("hm-sat-shell")
        time.sleep(1)
        focus_workspace(4)
        before_focus = snapshot()
        before = screenshot(sat_shell, "sat-before")
        sat_path = open_socket(sat_shell)
        send(sat_path, "type", "echo satellite > /tmp/sat-marker", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/sat-marker)" = satellite', timeout=30)
        time.sleep(1)
        assert snapshot() == before_focus
        after = screenshot(sat_shell, "sat-after")
        machine.fail(f"cmp -s {before} {after}")

        focus_workspace(16)
        sat_active = xterm(
            "hm-sat-active",
            "sh -c " + shlex.quote("stty -echo; cat > /tmp/hyprmasq-sat-active-input"),
            env=satellite_env,
        )
        wait_for("satellite focus", lambda: snapshot() == (16, sat_active))
        machine.wait_until_succeeds("test -f /tmp/hyprmasq-sat-active-input", timeout=30)
        before_focus = snapshot()
        send_refused(sat_path, "type", "echo refused > /tmp/sat-refused", "key", 28)
        time.sleep(1)
        machine.succeed("test ! -e /tmp/sat-refused && test ! -s /tmp/hyprmasq-sat-active-input")
        # A window of Hyprland's own XWayland is on another X server, so it is not blocked.
        send(open_socket(x_active), "type", "other", "key", 28)
        machine.wait_until_succeeds("grep -qx other /tmp/hyprmasq-x-active-input", timeout=30)

        hypr(f"hyprmasq eis open {sat_shell} force")
        send(sat_path, "type", "echo forced > /tmp/sat-forced", "key", 28)
        machine.wait_until_succeeds('test "$(cat /tmp/sat-forced)" = forced', timeout=30)
        time.sleep(1)
        machine.succeed("test ! -s /tmp/hyprmasq-sat-active-input")
        assert snapshot() == before_focus

        # xwayland-satellite forwards activation as xdg-activation. Every satellite app counts as the driven one.
        focus_workspace(4)
        before_focus = snapshot()
        xid = alice(f"env {satellite_env} xdotool search --name '^hm-sat-active$'").split()[0]
        alice(f"env {satellite_env} xdotool windowactivate {xid} || true")
        time.sleep(1)
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {sat_shell}") == "ok"
        alice(f"env {satellite_env} xdotool windowactivate {xid} || true")
        wait_for("satellite activation", lambda: snapshot()[1] == sat_active)

    with subtest("virtual cursor and click ripple on the active workspace"):
        # The default color, rgb(6ec6ff), and anything blended from it over foot's dark background. Checks stay near
        # the virtual cursor: Hyprland's own cursor, at the center of the screen, is cyan.
        light_blue = "abs(r - 0x6E) <= 40 and abs(g - 0xC6) <= 40 and b >= 0xD8"
        bluish = "b - r > 50"
        magenta = "r > 200 and g < 60 and b > 200"
        assert f"int: {0xFF6EC6FF}" in hypr("getoption plugin:hyprmasq:virtual_cursor_color")
        focus_workspace(17)
        pointed = foot("hm-cursor")
        pointed_path = hypr(f"hyprmasq eis open {pointed}")
        x, y = client("hm-cursor")["at"]
        x, y = x + 200, y + 150
        cursor_box = (x - 4, y - 4, x + 40, y + 40)
        around = (x - 30, y - 30, x + 50, y + 50)
        send(pointed_path, "move", 200, 150)
        assert count_pixels(capture_output("cursor"), cursor_box, light_blue) > 20
        # The ripple rings the click point, also on the side away from the arrow. It lasts under half a second.
        clicked = capture_output(
            "cursor-click",
            f"hyprmasq-input --socket {shlex.quote(pointed_path)} click &&",
        )
        assert count_pixels(clicked, (x - 24, y - 3, x - 6, y + 3), bluish) > 0
        # The cursor stays 3 seconds after the last input, then fades out over 0.4 seconds.
        time.sleep(4)
        assert count_pixels(capture_output("cursor-faded"), around, bluish) == 0
        # Input at the same point while the cursor fades out brings it back at full strength.
        send(pointed_path, "move", 200, 150)
        time.sleep(3.15)
        send(pointed_path, "move", 200, 150)
        time.sleep(0.2)
        assert count_pixels(capture_output("cursor-refreshed"), cursor_box, light_blue) > 20
        hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor_timeout = 500 } } })"))
        send(pointed_path, "move", 200, 150)
        time.sleep(1.5)
        assert count_pixels(capture_output("cursor-short"), around, bluish) == 0
        hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor_timeout = 3000 } } })"))
        # Nothing is drawn for a window on a hidden workspace.
        focus_workspace(4)
        send(pointed_path, "move", 200, 150)
        assert count_pixels(capture_output("cursor-hidden"), around, bluish) == 0
        focus_workspace(17)
        hypr("eval " + shlex.quote('hl.config({ plugin = { hyprmasq = { virtual_cursor_color = "rgb(ff00ff)" } } })'))
        send(pointed_path, "move", 200, 150)
        assert count_pixels(capture_output("cursor-magenta"), cursor_box, magenta) > 20
        hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor = false } } })"))
        time.sleep(0.5)
        send(pointed_path, "move", 200, 150)
        assert count_pixels(capture_output("cursor-off"), around, magenta) == 0
        hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor = true } } })"))
        assert hypr(f"hyprmasq eis close {pointed}") == "ok"

    with subtest("unload removes all sockets and hooks, reload succeeds"):
        paths = [s["path"] for s in json.loads(hypr("-j hyprmasq eis list"))]
        assert paths
        assert hypr(f"plugin unload {plugin}") == "ok"
        for path in paths:
            machine.succeed(f"test ! -e {path} && test ! -e {path}.lock")
        assert hypr(f"plugin load {plugin}") == "ok"
        assert json.loads(hypr("-j hyprmasq eis list")) == []
        assert not hypr("configerrors")

    with subtest("unloading answers screenshots still being encoded"):
        # The red window was closed by the socket lifecycle subtest.
        green = windows[1][1]
        fifo("/tmp/hm-fifo.png")
        hypr_background(
            f"hyprmasq screenshot {green} /tmp/hm-fifo.png", "/tmp/hm-fifo-reply"
        )
        time.sleep(1)
        hypr_background(f"plugin unload {plugin}", "/tmp/hm-unload-reply")
        time.sleep(1)
        machine.succeed("cat /tmp/hm-fifo.png > /tmp/hm-fifo-copy.png")
        machine.wait_until_succeeds("grep -qx ok /tmp/hm-unload-reply", timeout=30)
        machine.wait_until_succeeds("grep -qx ok /tmp/hm-fifo-reply", timeout=30)
        assert_png("/tmp/hm-fifo-copy.png", (0, 255, 0))
        # The batch command works without the plugin, and the plugin can be unloaded from within a batch.
        assert (
            hypr("--batch 'activeworkspace; activeworkspace'").count("workspace ID")
            == 2
        )
        assert hypr(f"plugin load {plugin}") == "ok"
        batch(f"plugin unload {plugin}; activeworkspace")
        assert "hyprmasq" not in hypr("plugin list")
        # Once the batch has returned, the library is released and the batch command restored.
        pid = machine.succeed(
            f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock"
        ).strip()
        machine.wait_until_succeeds(
            f"! grep -q hyprmasq.so /proc/{pid}/maps", timeout=10
        )
        assert [p[:12] for p in batch("activeworkspace; activeworkspace")] == [
            "workspace ID"
        ] * 2
        assert hypr(f"plugin load {plugin}") == "ok"
        assert_png(screenshot(green, "after-reload"), (0, 255, 0))
        parts = batch(
            f"hyprmasq screenshot {green} /tmp/hm-reload-batch.png; activeworkspace"
        )
        assert parts[0] == "ok" and parts[1].startswith("workspace ID"), parts

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
    machine.execute("cat /tmp/hyprland/hyprlandCrashReport*.txt >&2")
    machine.execute(f"cp {runtime}/hypr/{instance}/hyprland.log /tmp/hyprmasq-hyprland-full.log")
    for artifact in [
        "/tmp/hyprmasq-hyprland.log",
        "/tmp/hyprmasq-hyprland-full.log",
        "/tmp/hyprmasq-wev.log",
        "/tmp/hyprmasq-x-xev.log",
        "/tmp/hyprmasq-x-cover.log",
        "/tmp/hyprmasq-satellite.log",
    ]:
        if machine.execute(f"test -e {artifact}")[0] == 0:
            machine.copy_from_machine(artifact)
