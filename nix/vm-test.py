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


def eis_entry(address):
    return next(e for e in json.loads(hypr("-j hyprmasq eis list")) if e["address"] == address)


# The plugin's directory for this Hyprland is its signature without the commit hash in front.
instance_dir = f"{runtime}/hyprmasq/{instance.split('_', 1)[1]}"


def open_socket(address):
    path = hypr(f"hyprmasq eis open {address}")
    assert path == f"{instance_dir}/{address}.eis", path
    # sun_path takes 107 bytes, and the path leaves 57 of them for XDG_RUNTIME_DIR.
    assert len(path) - len(runtime) <= 50, path
    machine.succeed(f"test -S {path}")
    assert machine.succeed(
        f"stat -c %a {runtime}/hyprmasq {instance_dir}"
    ).split() == ["700", "700"]
    assert machine.succeed(f"stat -c %a {path}").strip() == "600"
    entry = eis_entry(address)
    assert entry["path"] == path and not entry["force"] and entry["socketId"], entry
    assert json.loads(hypr(f"-j hyprmasq eis open {address}")) == {"ok": True, "path": path, "socketId": entry["socketId"]}
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
        # ^ is a dead key and Space here, so 40 of them are 80 keys, 1190 ms at their pace, which a budget that 40 keys
        # would fit refuses before anything is sent.
        status, output = machine.execute(
            "su - alice -c " + shlex.quote(f"hyprmasq-input --json --budget 900 --socket {shlex.quote(path)} text {shlex.quote('^' * 40)}")
        )
        budgeted = json.loads(output)
        assert status == 2 and budgeted["error"]["code"] == "too_long" and budgeted["paceMs"] == 1190, (status, output)
        assert (budgeted["done"], budgeted["failedIndex"], budgeted["delivery"], budgeted["progress"]) == (0, None, "none", None), budgeted
        send(path, "type", "echo clean > /tmp/typed-clean<enter>")
        machine.wait_until_succeeds("test -s /tmp/typed-clean")
        assert machine.succeed("cat /tmp/typed-clean") == "clean\n"
        machine.succeed("! ls /tmp/typed-partial*")
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {target}") == "ok"
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
        wait_for("US keymap", lambda: keymap("English"))

    with subtest("typing through the Compose table of the target's locale"):
        # On a German layout, ć is only the dead acute and c, which the Compose table of pt_BR.UTF-8 makes a ç. ç is also
        # the dead cedilla and c. foot applies the table of its own locale, and the shells run in UTF-8 either way.
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
        wait_for("German keymap", lambda: keymap("German"))
        focus_workspace(22)
        compose_shell = "env LC_ALL=en_US.UTF-8 bash --noprofile --norc -i"
        pt_target = foot("hm-compose-pt", compose_shell, executable="env LC_ALL=pt_BR.UTF-8 foot")
        en_target = foot("hm-compose-en", compose_shell, executable="env LC_ALL=en_US.UTF-8 foot")
        time.sleep(1)
        focus_workspace(4)
        before_focus = snapshot()

        def compose_send(path, text, sender_locale, *options):
            return machine.execute(
                "su - alice -c "
                + shlex.quote(f"env LC_ALL={sender_locale} hyprmasq-input --json --socket {shlex.quote(path)} {' '.join(options)} type {shlex.quote(text)}")
                + " 2>/tmp/hm-compose-stderr"
            )

        def compose_typed(path, name, text, sender_locale, *options):
            status, output = compose_send(path, f'printf "%s\\n" "{text}" > /tmp/{name}<enter>', sender_locale, *options)
            assert status == 0, (status, output)
            machine.wait_until_succeeds(f"test -s /tmp/{name}")
            return machine.succeed(f"cat /tmp/{name}").removesuffix("\n"), json.loads(output)

        pt_path, en_path = open_socket(pt_target), open_socket(en_target)
        # The sender's own table, en_US.UTF-8, differs from the target's: ć arrives as ç. This is why --locale exists.
        assert compose_typed(pt_path, "hm-compose-default", "ć", "en_US.UTF-8")[0] == "ç"
        # With the target's locale, ç arrives as it is, and ć cannot be typed, which is rejected before anything is sent.
        text, reply = compose_typed(pt_path, "hm-compose-pt", "çé", "en_US.UTF-8", "--locale", "pt_BR.UTF-8")
        assert text == "çé" and reply["compose"] == {"locale": "pt_BR.UTF-8", "requested": "pt_BR.UTF-8"}, (text, reply)
        status, output = compose_send(pt_path, "ć", "en_US.UTF-8", "--locale", "pt_BR.UTF-8")
        rejected = json.loads(output)
        assert status == 2 and rejected["delivery"] == "none" and rejected["compose"] is None, output
        assert rejected["error"] == {"code": "cannot_type", "message": "the keymap cannot type U+0107, also not through the Compose table of pt_BR.UTF-8"}, output
        # The other way round: a sender in pt_BR.UTF-8 cannot type ć with its own table, but can with the target's.
        status, output = compose_send(en_path, "ć", "pt_BR.UTF-8")
        assert status == 2 and json.loads(output)["error"]["code"] == "cannot_type", output
        text, reply = compose_typed(en_path, "hm-compose-en", "ć", "pt_BR.UTF-8", "--locale", "en_US.UTF-8")
        assert text == "ć" and reply["compose"] == {"locale": "en_US.UTF-8", "requested": "en_US.UTF-8"}, (text, reply)
        # Without a table for the locale, the sender uses that of en_US.UTF-8, and says so.
        text, reply = compose_typed(en_path, "hm-compose-fallback", "ć", "pt_BR.UTF-8", "--locale", "xx_XX.UTF-8")
        assert text == "ć" and reply["compose"] == {"locale": "en_US.UTF-8", "requested": "xx_XX.UTF-8"}, (text, reply)
        assert "no Compose table for the locale 'xx_XX.UTF-8', so the table of en_US.UTF-8 is used" in machine.succeed("cat /tmp/hm-compose-stderr")
        # Text without a character typed through Compose reports none.
        text, reply = compose_typed(en_path, "hm-compose-none", "ok", "pt_BR.UTF-8", "--locale", "en_US.UTF-8")
        assert text == "ok" and reply["compose"] is None, (text, reply)
        assert snapshot() == before_focus
        for target in (pt_target, en_target):
            assert hypr(f"hyprmasq eis close {target}") == "ok"
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
        wait_for("US keymap", lambda: keymap("English"))

    # hyprmasq-input's pacing, actions, JSON results and cleanup, on workspaces 40 to 49.
    import random
    import re
    import string

    def input_json(path, *args, options=""):
        """Runs hyprmasq-input with --json and returns its exit status and result."""
        status, output = machine.execute(
            "su - alice -c "
            + shlex.quote(
                f"hyprmasq-input --json {options} --socket {shlex.quote(path)} "
                + " ".join(shlex.quote(str(a)) for a in args)
            )
        )
        return status, json.loads(output)

    def complete(total):
        return {
            "ok": True,
            "done": total,
            "total": total,
            "failedIndex": None,
            "delivery": "complete",
            "progress": None,
            "compose": None,
            "error": None,
        }

    # wev prints the compositor's timestamps in milliseconds.
    wev_patterns = {
        "key": re.compile(r"\] key: serial: \d+; time: (\d+); key: (\d+); state: (\d)"),
        "button": re.compile(r"\] button: serial: \d+; time: (\d+); button: (\d+) \(\w+\), state: (\d)"),
        "motion": re.compile(r"\] motion: time: (\d+); x, y: (-?[\d.]+), (-?[\d.]+)"),
        "axis": re.compile(r"\] axis: time: (\d+); axis: (\d) \(\w+\), value: (-?[\d.]+)"),
        "value120": re.compile(r"\] axis_value120: axis: (\d) \(\w+\), value120: (-?\d+)"),
        "discrete": re.compile(r"\] axis_discrete: axis: (\d) \(\w+\), discrete: (-?\d+)"),
    }

    def wev_events(offset, kinds):
        """The events of the given kinds that wev logged since offset, as (kind, *numbers), with evdev key codes."""
        events: list = []
        for line in log_since("hm-i-wev", offset).splitlines():
            for kind in kinds:
                if match := wev_patterns[kind].search(line):
                    numbers = [float(g) for g in match.groups()]
                    if kind == "key":
                        # wev prints XKB keycodes, the evdev code plus 8.
                        numbers[1] -= 8
                    events.append((kind, *numbers))
        return events

    def wait_wev(offset, kinds, done):
        events: list = []

        def ready():
            nonlocal events
            events = wev_events(offset, kinds)
            return done(events)

        wait_for("wev events", ready, timeout=30)
        return events

    def run_input_background(name, path, *args):
        """Starts hyprmasq-input --json in the background; its result and status land in /tmp/{name}.json and .status."""
        machine.succeed(f"rm -f /tmp/{name}.json /tmp/{name}.status")
        command = (
            f"hyprmasq-input --json --socket {shlex.quote(path)} "
            + " ".join(shlex.quote(str(a)) for a in args)
            + f" > /tmp/{name}.json; echo $? > /tmp/{name}.status"
        )
        alice(f"nohup sh -c {shlex.quote(command)} > /dev/null 2>&1 &")

    def input_result(name):
        machine.wait_until_succeeds(f"test -s /tmp/{name}.status", timeout=120)
        status = int(machine.succeed(f"cat /tmp/{name}.status"))
        return status, json.loads(machine.succeed(f"cat /tmp/{name}.json"))

    with subtest("hyprmasq-input paces clicks and key chords and reports JSON"):
        focus_workspace(40)
        spawn("stdbuf -oL wev", "hm-i-wev")

        def find_wev():
            return next((c["address"] for c in clients() if c["class"] == "wev" and c["workspace"]["id"] == 40), None)

        wait_for("wev", lambda: find_wev() is not None)
        input_wev = find_wev()
        focus_workspace(4)
        before_focus = snapshot()
        input_path = open_socket(input_wev)

        # A click holds the button 50 ms. Its press comes 50 ms after the move, the gap between actions.
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "move", 100, 100, "click") == (0, complete(2))
        events = wait_wev(offset, {"motion", "button"}, lambda e: len(e) >= 3)
        assert [e[0] for e in events] == ["motion", "button", "button"], events
        assert events[0][2:] == (100, 100) and [e[3] for e in events[1:]] == [1, 0], events
        assert events[1][1] - events[0][1] >= 45 and 45 <= events[2][1] - events[1][1] <= 200, events

        # A double click: two clicks, 80 ms apart.
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "click", "left", 2) == (0, complete(1))
        times = [e[1] for e in wait_wev(offset, {"button"}, lambda e: len(e) >= 4)]
        assert all(45 <= times[i + 1] - times[i] <= 200 for i in (0, 2)) and 75 <= times[2] - times[1] <= 300, times

        # --no-pace sends the press and release at once.
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "click", options="--no-pace") == (0, complete(1))
        events = wait_wev(offset, {"button"}, lambda e: len(e) >= 2)
        assert [e[3] for e in events] == [1, 0] and events[1][1] - events[0][1] < 10, events

        # Modifiers go down first, the key 10 ms after them, held 15 ms, and everything comes up in reverse.
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "keys", "ctrl+shift+t") == (0, complete(1))
        events = wait_wev(offset, {"key"}, lambda e: len(e) >= 6)
        assert [e[2:] for e in events] == [(29, 1), (42, 1), (20, 1), (20, 0), (42, 0), (29, 0)], events
        assert 8 <= events[2][1] - events[1][1] <= 150 and 12 <= events[3][1] - events[2][1] <= 150, events

        # Aliases and keysym names ignore case, a letter is its key without Shift, and repeats are 15 ms apart. Actions
        # are 50 ms apart.
        offset = log_size("hm-i-wev")
        chords = ["ctrl+l", "Enter", "return", "esc", "ESCAPE", "del", "f5", "A"]
        args = [word for chord in chords for word in ("keys", chord)] + ["keys", "tab", 3]
        assert input_json(input_path, *args) == (0, complete(len(chords) + 1))
        events = wait_wev(offset, {"key"}, lambda e: len(e) >= 24)
        assert [int(e[2]) for e in events if e[3] == 1] == [29, 38, 28, 28, 1, 1, 111, 63, 30, 15, 15, 15], events
        assert events[4][1] - events[3][1] >= 45, events
        assert all(events[i + 1][1] - events[i][1] >= 12 for i in (19, 21)), events
        assert snapshot() == before_focus

    with subtest("hyprmasq-input scrolls in wheel notches and drags in steps"):
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "move", 200, 200, "wheel", 0, 3, "wheel", 0, -1, "wheel", 2, 0) == (0, complete(4))
        axes = wait_wev(offset, {"axis"}, lambda e: len(e) >= 6)
        assert [e[2:] for e in axes] == [(0, 10)] * 3 + [(0, -10)] + [(1, 10)] * 2, axes
        assert all(axes[i + 1][1] - axes[i][1] >= 15 for i in (0, 1)), axes
        notches = [(0, 1)] * 3 + [(0, -1)] + [(1, 1)] * 2
        value120 = [e[1:] for e in wev_events(offset, {"value120"})]
        discrete = [e[1:] for e in wev_events(offset, {"discrete"})]
        assert value120 == [(a, v * 120) for a, v in notches] or discrete == notches, (value120, discrete)

        # Press, then motion in steps of at most 10 pixels over at least 200 ms, then release. The press comes at least
        # 30 ms after the first motion, within the one action.
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "drag", 100, 100, 300, 150) == (0, complete(1))
        events = wait_wev(offset, {"motion", "button"}, lambda e: bool(e) and e[-1][0] == "button" and e[-1][3] == 0)
        kinds = [e[0] for e in events]
        assert kinds[:2] == ["motion", "button"] and kinds[2:-1] == ["motion"] * (len(kinds) - 3), kinds
        assert events[1][1] - events[0][1] >= 25, events
        steps = [e[2:] for e in events if e[0] == "motion"]
        assert len(steps) >= 21 and steps[0] == (100, 100) and steps[-1] == (300, 150), steps
        assert all(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 <= 10.5 for a, b in zip(steps, steps[1:])), steps
        assert events[-1][1] - events[1][1] >= 250, events
        assert snapshot() == before_focus

    with subtest("hyprmasq-input types 2000 characters of paced literal text exactly into a hidden shell"):

        def text_target(title, workspace, path):
            machine.succeed(f"rm -f {path}")
            focus_workspace(workspace)
            address = foot(title, "sh -c " + shlex.quote(f"stty -echo; exec cat > {path}"))
            machine.wait_until_succeeds(f"test -f {path}")
            focus_workspace(4)
            return address, open_socket(address)

        # Tags, escapes, quotes and backslashes are text. A tab and a line break press their keys, and cat writes each
        # line as it gets it.
        literal = 'say "hi" it\'s a \\back\\slash: <enter>, <tab>, \\< and <shortcut:ctrl+u> stay\tafter a tab\n'
        address, path = text_target("hm-i-literal", 41, "/tmp/hm-i-literal")
        assert input_json(path, "text", literal) == (0, complete(1))
        wait_for("literal text", lambda: machine.succeed("cat /tmp/hm-i-literal") == literal)
        dispatch('hl.dsp.window.close({ window = "address:' + address + '" })')

        rng = random.Random(2000)
        alphabet = string.ascii_letters + string.digits + string.punctuation + " "
        long_text = "\n".join("".join(rng.choice(alphabet) for _ in range(99)) for _ in range(20)) + "\n"
        assert len(long_text) == 2000
        address, path = text_target("hm-i-long", 42, "/tmp/hm-i-long")
        assert input_json(path, "text", long_text) == (0, complete(1))
        wait_for("long text", lambda: machine.succeed("cat /tmp/hm-i-long") == long_text)
        dispatch('hl.dsp.window.close({ window = "address:' + address + '" })')
        assert snapshot() == before_focus

    with subtest("hyprmasq-input sends nothing for an invalid batch"):
        offset = log_size("hm-i-wev")
        for args, code, index in [
            (["move", 10, 10, "keys", "ctrl+nosuchkey"], "invalid_arguments", 1),
            (["move", 10, 10, "text", "a☃"], "cannot_type", 1),
            (["move", 10, 10, "click", "left", 4], "invalid_arguments", 1),
            (["click", "move", 10, "x"], "invalid_arguments", 1),
            (["keys", "a", "move", 700, 10], "invalid_arguments", 1),
            # libei drops button codes below BTN_MOUSE, such as 1 for the left button.
            (["move", 10, 10, "down", 1], "invalid_arguments", 1),
            (["keys", "ctrl+ä"], "cannot_type", 0),
        ]:
            status, result = input_json(input_path, *args)
            assert status == 2 and not result["ok"] and result["delivery"] == "none", (args, status, result)
            assert (result["done"], result["failedIndex"], result["error"]["code"]) == (0, index, code), (args, result)
        time.sleep(1)
        assert wev_events(offset, ["key", "button", "motion", "axis"]) == []

    with subtest("hyprmasq-input releases what it holds at the end and on SIGTERM"):
        offset = log_size("hm-i-wev")
        assert input_json(input_path, "move", 50, 50, "down", "left") == (0, complete(2))
        events = wait_wev(offset, {"button"}, lambda e: len(e) >= 2)
        assert [e[2:] for e in events] == [(272, 1), (272, 0)], events

        offset = log_size("hm-i-wev")
        command = f"hyprmasq-input --json --socket {input_path} move 60 60 down right sleep 10000 > /tmp/hm-i-cancel.json"
        alice(f"nohup sh -c {shlex.quote(command + '; echo $? > /tmp/hm-i-cancel.status')} > /dev/null 2>&1 &")
        wait_wev(offset, {"button"}, lambda e: len(e) >= 1)
        time.sleep(0.5)
        machine.succeed("pkill -TERM -x hyprmasq-input")
        machine.wait_until_succeeds("test -s /tmp/hm-i-cancel.status", timeout=10)
        assert machine.succeed("cat /tmp/hm-i-cancel.status").strip() == "1"
        result = json.loads(machine.succeed("cat /tmp/hm-i-cancel.json"))
        assert (result["done"], result["failedIndex"], result["delivery"]) == (2, 2, "partial"), result
        assert result["error"]["code"] == "cancelled", result
        events = wait_wev(offset, {"button"}, lambda e: len(e) >= 2)
        assert [e[2:] for e in events] == [(273, 1), (273, 0)], events

        # SIGTERM during a drag with Ctrl lets go of the button before Ctrl, as the end of the drag would, so the
        # application still sees a drop with Ctrl.
        offset = log_size("hm-i-wev")
        run_input_background("hm-i-ctrl-drag", input_path, "drag", 10, 10, 630, 470, "left", "ctrl")
        drag_pressed = f"tail -c +{offset + 1} /tmp/hyprmasq-hm-i-wev.log | grep -q 'button: 272 (left), state: 1'"
        machine.succeed(f"timeout 30 sh -c {shlex.quote(f'until {drag_pressed}; do sleep 0.01; done')}; pkill -TERM -x hyprmasq-input")
        status, result = input_result("hm-i-ctrl-drag")
        assert status == 1 and result["error"]["code"] == "cancelled", (status, result)
        assert (result["done"], result["failedIndex"], result["delivery"]) == (0, 0, "partial"), result
        events = [(e[0], int(e[2]), int(e[3])) for e in wait_wev(offset, {"key", "button"}, lambda e: len(e) >= 4)]
        assert events[:2] == [("key", 29, 1), ("button", 272, 1)], events
        assert events.index(("button", 272, 0)) < events.index(("key", 29, 0)), events
        assert snapshot() == before_focus

    with subtest("hyprmasq-input reports within a second of SIGTERM while the plugin does not answer"):
        # With Hyprland stopped, nothing answers the round trip after the release that SIGTERM sends. The sender gives
        # it a second, then reports that it cannot tell what the plugin got, well before hyprmasq-mcp would kill it.
        offset = log_size("hm-i-wev")
        machine.succeed("rm -f /tmp/hm-i-stopped.json /tmp/hm-i-stopped.status")
        command = f"hyprmasq-input --json --socket {input_path} move 70 70 down left sleep 10000 > /tmp/hm-i-stopped.json"
        alice(f"nohup sh -c {shlex.quote(command + '; echo $? > /tmp/hm-i-stopped.status')} > /dev/null 2>&1 &")
        wait_wev(offset, {"button"}, lambda e: len(e) >= 1)
        # Hyprland stops once the sender sleeps.
        time.sleep(0.5)
        hyprland_pid = machine.succeed(f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock").strip()
        machine.succeed(f"kill -STOP {hyprland_pid}")
        try:
            elapsed_ms = int(machine.succeed(
                "start=$(date +%s%N); pkill -TERM -x hyprmasq-input; "
                "timeout 10 sh -c 'until [ -s /tmp/hm-i-stopped.status ]; do sleep 0.02; done'; "
                "echo $(( ($(date +%s%N) - start) / 1000000 ))"
            ))
        finally:
            machine.succeed(f"kill -CONT {hyprland_pid}")
        status, result = input_result("hm-i-stopped")
        assert status == 1 and result["error"]["code"] == "cancelled", (status, result)
        assert (result["done"], result["failedIndex"], result["delivery"]) == (2, 2, "uncertain"), result
        assert 900 <= elapsed_ms < 1800, elapsed_ms
        # Once Hyprland runs again, the plugin takes the release.
        events = wait_wev(offset, {"button"}, lambda e: len(e) >= 2)
        assert [e[2:] for e in events] == [(272, 1), (272, 0)], events
        assert snapshot() == before_focus

    with subtest("hyprmasq-input follows a replaced pointer"):
        # The plugin replaces the pointer when the window is resized, here during the sleep.
        offset = log_size("hm-i-wev")
        command = f"hyprmasq-input --json --socket {input_path} move 30 30 sleep 3000 move 20 25 click > /tmp/hm-i-replaced.json"
        alice(f"nohup sh -c {shlex.quote(command)} > /dev/null 2>&1 &")
        wait_wev(offset, {"motion"}, lambda e: len(e) >= 1)
        moved = time.monotonic()
        dispatch(f'hl.dsp.window.float({{ window = "address:{input_wev}" }})')
        dispatch(f'hl.dsp.window.resize({{ window = "address:{input_wev}", x = 300, y = 200 }})')
        wait_for("resized wev", lambda: next(c for c in clients() if c["address"] == input_wev)["size"] == [300, 200])
        assert time.monotonic() - moved < 2.5
        machine.wait_until_succeeds("test -s /tmp/hm-i-replaced.json", timeout=30)
        assert json.loads(machine.succeed("cat /tmp/hm-i-replaced.json")) == complete(4)
        # A sender that kept the old pointer would send these to a removed device, which drops them.
        events = wev_events(offset, {"motion", "button"})
        assert [e[0] for e in events[-3:]] == ["motion", "button", "button"] and events[-3][2:] == (20, 25), events
        assert snapshot() == before_focus

    # The evdev codes of the characters that the following subtests type, on the US layout.
    us_codes = {
        char: first + i
        for row, first in (("1234567890", 2), ("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44))
        for i, char in enumerate(row)
    }
    modifier_codes = {29, 42, 54, 56, 97, 100, 125, 126}

    with subtest("hyprmasq-input stops a long text on SIGTERM, reports its keys and leaves nothing pressed"):
        rng = random.Random(40)
        cancelled_text = "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(400))
        offset = log_size("hm-i-wev")
        run_input_background("hm-i-sigterm", input_path, "text", cancelled_text)

        def presses(events):
            return [e for e in events if e[3] == 1 and e[2] not in modifier_codes]

        wait_wev(offset, {"key"}, lambda e: len(presses(e)) >= 40)
        machine.succeed("pkill -TERM -x hyprmasq-input")
        status, result = input_result("hm-i-sigterm")
        assert status == 1, (status, result)
        assert (result["done"], result["failedIndex"], result["delivery"]) == (0, 0, "partial"), result
        assert result["error"]["code"] == "cancelled", result
        keys = result["progress"]["keys"]
        assert 40 <= keys < len(cancelled_text), result
        # The sender confirmed its releases before it exited, so wev has them all as key events already.
        events = wev_events(offset, {"key"})
        assert [int(e[2]) for e in presses(events)] == [us_codes[c.lower()] for c in cancelled_text[:keys]], events
        held: set = set()
        for _, _, code, state in events:
            (held.add if state == 1 else held.discard)(code)
        assert not held, (held, events)
        assert snapshot() == before_focus

    with subtest("hyprmasq-input follows a pointer replaced in the middle of a batch"):

        def sizes(width, height):
            dispatch(f'hl.dsp.window.resize({{ window = "address:{input_wev}", x = {width}, y = {height} }})')

        def wev_lines(offset):
            return log_since("hm-i-wev", offset).splitlines()

        points = [(20 + 8 * i, 20 + 5 * i) for i in range(30)]
        args = [word for x, y in points for word in ("move", x, y, "wheel", 0, 1)]
        offset = log_size("hm-i-wev")
        run_input_background("hm-i-midbatch", input_path, *args)
        wait_wev(offset, {"motion"}, lambda e: len(e) >= 3)
        # Each resize replaces the pointer while motion and wheel notches are being sent.
        for width, height in [(320, 220), (300, 200), (320, 220), (300, 200)]:
            sizes(width, height)
            time.sleep(0.3)
        status, result = input_result("hm-i-midbatch")
        wait_for("wev size", lambda: next(c for c in clients() if c["address"] == input_wev)["size"] == [300, 200])
        machine.log(f"pointer replaced mid-batch: {result}")
        lines = wev_lines(offset)
        motions = [i for i, line in enumerate(lines) if wev_patterns["motion"].search(line)]
        leaves = [i for i, line in enumerate(lines) if "wl_pointer] leave" in line]
        events = wev_events(offset, {"motion", "axis"})
        received = [tuple(e[2:]) for e in events if e[0] == "motion"]
        if status == 0:
            assert result == complete(len(args) // 3), result
            assert any(motions[0] < i < motions[-1] for i in leaves), "no pointer replacement during the batch"
            sent = points
            # The sender waits for the new pointer, which the plugin offers at once.
            gaps = [b[1] - a[1] for a, b in zip(events, events[1:])]
            assert max(gaps) < 1000, gaps
        else:
            # Input that reached the removed pointer is lost, which ends the batch.
            assert status == 1 and result["delivery"] == "partial", result
            assert result["error"]["code"] == "unconfirmed" and result["failedIndex"] == result["done"], result
            sent = points[: (result["done"] + 1) // 2]
        it = iter(received)
        assert all(point in it for point in sent), (sent, received)
        assert snapshot() == before_focus
        machine.succeed("pkill -x wev")
        wait_for("wev closed", lambda: all(c["address"] != input_wev for c in clients()))

    with subtest("hyprmasq-input reports input_paused for an X11 window while another X11 window is active"):
        machine.succeed("rm -f /tmp/hm-i-x-target")
        focus_workspace(43)
        command = "sh -c " + shlex.quote("stty -icanon -echo; exec cat > /tmp/hm-i-x-target")
        x_target = xterm("hm-i-x-target", command)
        machine.wait_until_succeeds("test -f /tmp/hm-i-x-target", timeout=30)
        focus_workspace(44)
        x_active = xev("hm-i-x-active", "hm-i-x-active")
        focus_workspace(4)
        before_focus = snapshot()
        x_path = open_socket(x_target)
        rng = random.Random(43)
        paused_text = "".join(rng.choice(string.ascii_lowercase) for _ in range(300))
        leaked = log_size("hm-i-x-active")
        run_input_background("hm-i-paused", x_path, "move", 10, 10, "text", paused_text)

        def typed():
            return machine.succeed("cat /tmp/hm-i-x-target")

        wait_for("typing into the hidden X11 window", lambda: len(typed()) >= 20)
        # Another X11 window of the same X server takes the focus, so the plugin drops input and pauses the devices.
        focus_workspace(44)
        wait_for("X11 window active", lambda: snapshot() == (44, x_active))
        status, result = input_result("hm-i-paused")
        assert status == 1 and result["error"]["code"] == "input_paused", (status, result)
        assert (result["done"], result["failedIndex"], result["delivery"]) == (1, 1, "partial"), result
        keys = result["progress"]["keys"]
        time.sleep(1)
        received = typed()
        assert received == paused_text[: len(received)] and len(received) < len(paused_text), received
        # Every key that the plugin delivered counts; X11 might hand one to the newly focused window.
        leaked_keys = log_since("hm-i-x-active", leaked).count("KeyPress event")
        machine.log(f"input_paused after {keys} keys, {len(received)} typed, {leaked_keys} in the active window")
        assert keys == len(received) + leaked_keys, (keys, received, leaked_keys)
        focus_workspace(4)
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {x_target}") == "ok"
        for address in (x_target, x_active):
            dispatch('hl.dsp.window.close({ window = "address:' + address + '" })')
        wait_for("X11 windows closed", lambda: all(c["address"] not in (x_target, x_active) for c in clients()))

    with subtest("hyprmasq-input confirms input that closes its own window"):
        # Ctrl+D ends cat, so foot closes the window, maybe before the plugin has taken the chord's releases. It answers
        # the pings it got before the window closed, and removes the devices before it answers any after input it
        # dropped. So the outcome is partial: the chord is done, also when foot closed while its key was held and only
        # its releases were left, or its key confirmed, and the sleep after it stops.
        # When foot closes within the 15 ms that the key is held, the releases sent after it never reach the plugin,
        # and the outcome is uncertain, with nothing confirmed. Several runs meet the close at different points.
        before_focus = snapshot()
        for attempt in range(3):
            focus_workspace(45)
            closing = foot(f"hm-i-close-{attempt}", "cat")
            focus_workspace(4)
            status, result = input_json(open_socket(closing), "keys", "ctrl+d", "sleep", 5000, "text", "after")
            machine.log(f"input that closes its window: {result}")
            wait_for("window closed", lambda: all(c["address"] != closing for c in clients()))
            assert status == 1 and result["delivery"] in ("partial", "uncertain"), (status, result)
            assert result["error"]["code"] == "unconfirmed" and "closed the connection" in result["error"]["message"], result
            assert result["done"] == result["failedIndex"] and result["done"] in (0, 1), result
            # An uncertain delivery claims nothing that the plugin did not take: not the chord, and only its key as sent.
            if result["delivery"] == "uncertain":
                assert result["done"] == 0, result
            if result["done"] == 0:
                assert result["progress"] == {"keys": 1}, result
        assert snapshot() == before_focus

    with subtest("hyprmasq-input counts an action cut off by its window's close with only releases left as done"):
        # focus-probe destroys its window as Escape or the left button goes down, so the window closes while the sender
        # holds the key or the button, as a dialog does on the button that closes it. The plugin confirmed the press, and
        # the release is the plugin's to send once the connection ends, so the action is done. The sleep after it stops
        # with the connection, and the run fails there. On a busy host the close can still meet the release as the hold
        # ends, and the plugin drops it, which the delivery reports at the closing action; such a run is repeated.
        before_focus = snapshot()
        # The input, the index of the sleep after it, and what the probe logs before it closes. Escape is evdev key 1.
        for closing_name, closing_args, closing_sleep, closing_logged in (
            ("key", ["keys", "esc"], 1, "key {} 1 1"),
            ("click", ["move", 100, 100, "click"], 2, "button {} 1"),
        ):
            for attempt in range(3):
                closing_title = f"hm-i-closing-{closing_name}-{attempt}"
                focus_workspace(48)
                spawn(f"hyprmasq-focus-probe close {closing_title}", closing_title)
                wait_for(closing_title, lambda: client(closing_title) is not None)
                closing = client(closing_title)["address"]
                focus_workspace(4)
                path = open_socket(closing)
                started = time.monotonic()
                status, result = input_json(path, *closing_args, "sleep", 5000)
                elapsed = time.monotonic() - started
                machine.log(f"{closing_name} that closes its window, then a sleep: {result} in {elapsed:.2f} s")
                wait_for("window closed", lambda: client(closing_title) is None)
                assert status == 1 and result["error"]["code"] == "unconfirmed" and "closed the connection" in result["error"]["message"], result
                if result["done"] == closing_sleep:
                    break
                assert result["delivery"] in ("partial", "uncertain") and result["done"] == result["failedIndex"] == closing_sleep - 1, result
            assert result["delivery"] == "partial" and result["done"] == result["failedIndex"] == closing_sleep and result["progress"] is None, result
            assert elapsed < 4, elapsed
            assert re.search(rf"^\d+ {closing_logged.format(closing_title)}\n\d+ closed$", log_since(closing_title, 0), re.M), log_since(closing_title, 0)
        assert snapshot() == before_focus

    with subtest("hyprmasq-input reports a connection that another sender takes over during a burst"):
        # A second sender replaces the first, and the plugin closes the first one's connection, maybe while it is still
        # writing the frames between two round trips. Its next send fails, and it still reports what it knows.
        rng = random.Random(46)
        burst_text = "".join(rng.choice(string.ascii_lowercase) + ("\n" if i % 80 == 79 else "") for i in range(8000)).strip()
        machine.succeed(f"cat > /tmp/hm-i-burst.txt <<'EOF'\n{burst_text}\nEOF")
        before_focus = snapshot()
        for attempt in range(3):
            received_path = f"/tmp/hm-i-burst-{attempt}"
            machine.succeed(f"rm -f {received_path} /tmp/hm-i-burst.json /tmp/hm-i-burst.status")
            focus_workspace(46)
            burst = foot(f"hm-i-burst-{attempt}", "sh -c " + shlex.quote(f"stty -icanon -echo; exec cat > {received_path}"))
            machine.wait_until_succeeds(f"test -f {received_path}")
            focus_workspace(4)
            burst_path = open_socket(burst)
            first = (
                f"hyprmasq-input --json --no-pace --socket {burst_path} text \"$(cat /tmp/hm-i-burst.txt)\" "
                "> /tmp/hm-i-burst.json 2> /tmp/hm-i-burst.err; echo $? > /tmp/hm-i-burst.status"
            )
            # The second sender connects once the first one's keys arrive.
            arrived = f"until [ -s {received_path} ]; do sleep 0.01; done"
            alice(
                f"nohup sh -c {shlex.quote(first)} > /dev/null 2>&1 & timeout 60 sh -c {shlex.quote(arrived)}; "
                f"hyprmasq-input --socket {burst_path} sleep 0"
            )
            status, result = input_result("hm-i-burst")
            machine.log(f"connection taken over: {result}")
            assert status == 1 and result["error"]["code"] == "unconfirmed" and "closed the connection" in result["error"]["message"], (status, result)
            assert (result["done"], result["failedIndex"]) == (0, 0) and result["delivery"] in ("partial", "uncertain"), result
            # Every key the plugin delivered was sent; a partial delivery counts exactly those.
            time.sleep(1)
            received = machine.succeed(f"cat {received_path}")
            assert received == burst_text[: len(received)] and len(received) < len(burst_text), len(received)
            if result["delivery"] == "partial":
                assert result["progress"]["keys"] == len(received), (result, len(received))
            else:
                assert result["progress"]["keys"] >= len(received), (result, len(received))
            dispatch(f'hl.dsp.window.close({{ window = "address:{burst}" }})')
        assert snapshot() == before_focus

    with subtest("hyprmasq-input never counts input that its closing window dropped"):
        # focus-probe closes its window on Escape. The keys sent right after it can still be in the socket when the
        # window closes, and the plugin drops them; the ones it delivered before reach the probe's log.
        for attempt in range(3):
            title = f"hm-i-esc-{attempt}"
            focus_workspace(47)
            spawn(f"hyprmasq-focus-probe close {title}", title)
            wait_for(title, lambda: client(title) is not None)
            closing = client(title)["address"]
            focus_workspace(4)
            status, result = input_json(open_socket(closing), "keys", "esc", "text", "x" * 300, options="--no-pace")
            machine.log(f"input after closing its window: {result}")
            wait_for("window closed", lambda: client(title) is None)
            time.sleep(0.5)
            delivered = len(re.findall(rf"^\d+ key \S+ {us_codes['x']} 1$", log_since(title, 0), re.M))
            assert status == 1 and result["error"]["code"] == "unconfirmed" and "closed the connection" in result["error"]["message"], result
            assert result["delivery"] in ("partial", "uncertain") and result["done"] == result["failedIndex"], result
            if result["failedIndex"] == 0:
                assert delivered == 0, (result, delivered)
            elif result["delivery"] == "partial":
                assert result["progress"]["keys"] == delivered, (result, delivered)
            else:
                assert result["progress"]["keys"] >= delivered, (result, delivered)
        assert snapshot() == before_focus

    with subtest("waiting until a window has settled"):
        focus_workspace(19)
        waited = foot("hm-wait")
        time.sleep(1)
        focus_workspace(4)
        before_focus = snapshot()
        path = open_socket(waited)
        # Typed keys redraw the hidden shell, often before wait is called, and wait answers once it has stopped.
        send(path, "type", "echo waited")
        reply = json.loads(hypr(f"-j hyprmasq wait {waited}"))
        assert reply["ok"] and reply["settled"] and reply["framesSinceInput"] > 0 and reply["waited"] >= 200, reply
        # An idle window settles after the quiet time, without drawing.
        time.sleep(1.5)
        reply = json.loads(hypr(f"-j hyprmasq wait {waited} 100 1000"))
        assert reply["settled"] and reply["frames"] == 0 and 100 <= reply["waited"] < 600, reply
        assert hypr(f"hyprmasq wait {waited} 50") == "settled"
        assert snapshot() == before_focus
        assert hypr(f"hyprmasq eis close {waited}") == "ok"
        # A visible window that keeps drawing never settles.
        focus_workspace(20)
        busy = foot("hm-busy", "sh -c 'while :; do date +%N; sleep 0.02; done'")
        time.sleep(0.5)
        reply = json.loads(hypr(f"-j hyprmasq wait {busy} 200 1000"))
        assert reply["ok"] and not reply["settled"] and reply["frames"] > 5 and reply["waited"] >= 1000, reply
        assert reply["framesSinceInput"] is None, reply
        assert hypr(f"hyprmasq wait {busy} 200 500") == "timeout"
        focus_workspace(4)
        for args, message in [
            (f"{busy} 0", "quiet-ms"),
            (f"{busy} 300 200", "quiet-ms"),
            (f"{busy} 200 5000", "at most 4500"),
            (f"{busy} soon", "quiet-ms"),
            ("0x1", "no mapped window"),
        ]:
            reply = json.loads(hypr(f"-j hyprmasq wait {args}"))
            assert not reply["ok"] and message in reply["error"], (args, reply)

    def window_state(address):
        return json.loads(hypr(f"-j hyprmasq state {address}"))

    # hyprctl gives up on a reply after 5 seconds. This client writes a request to Hyprland's socket itself and waits
    # as long as the reply takes, then moves it into place at once.
    socket_client = "/tmp/hm-p-request.py"
    machine.succeed(f"cat > {socket_client} <<'PY'\n" + """
import os, socket, sys
path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket.sock")
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.connect(path)
connection.sendall(sys.argv[1].encode())
reply = b""
while chunk := connection.recv(65536):
    reply += chunk
with open(sys.argv[2] + ".part", "wb") as output:
    output.write(reply)
os.rename(sys.argv[2] + ".part", sys.argv[2])
""" + "PY")

    def hypr_socket_background(request, output):
        alice(f"rm -f {output}; {ipc} nohup python3 {socket_client} {shlex.quote(request)} {output} > /dev/null 2>&1 &")

    def background_reply(output, timeout=30):
        machine.wait_until_succeeds(f"test -s {output}", timeout=timeout)
        return json.loads(machine.succeed(f"cat {output}"))

    with subtest("plugin version and window state"):
        assert json.loads(hypr("-j hyprmasq version")) == {"ok": True, "version": "0.3.0", "interface": 3}
        assert hypr("hyprmasq version") == "hyprmasq 0.3.0 interface 3"
        # The module installs the MCP server of the same version, which runs the package's own hyprmasq-input.
        package = plugin.removesuffix("/lib/hyprmasq.so")
        assert alice("hyprmasq-mcp --version").strip() == "0.3.0"
        assert alice("readlink -f $(command -v hyprmasq-mcp)").strip() == f"{package}/bin/hyprmasq-mcp"
        assert alice("readlink -f $(command -v hyprmasq-input)").strip() == f"{package}/bin/hyprmasq-input"
        machine.succeed(f"grep -q {shlex.quote(package + '/bin')} {package}/bin/hyprmasq-mcp")
        focus_workspace(30)
        stated = foot("hm-p-state")
        time.sleep(1)
        focus_workspace(4)
        reply = window_state(stated)
        window = client("hm-p-state")
        assert reply["ok"] and reply["address"] == stated and reply["stableId"] == window["stableId"], (reply, window)
        assert not reply["xwayland"] and [reply["size"]["width"], reply["size"]["height"]] == window["size"], (reply, window)
        assert reply["resizing"] is False, reply
        assert reply["scale"] == 1 and reply["socket"] is None and reply["commits"] is None and reply["lease"] is None, reply
        # A hidden window on a connection of its own, while another app has the real focus.
        assert reply["capabilities"] == {"lease": True, "hold": True, "dnd": False, "keyboard": "yes"}, reply
        stated_path = open_socket(stated)
        reply = window_state(stated)
        assert reply["socket"] == stated_path and isinstance(reply["commits"], int) and reply["commits"] >= 0, reply
        # The focused window has the real focus on its own connection.
        assert window_state(visible)["capabilities"] == {"lease": False, "hold": False, "dnd": True, "keyboard": "yes"}

    with subtest("errors carry stable codes"):
        green = windows[1][1]
        for command, code in [
            ("state 0x1", "window_gone"),
            ("screenshot 0x1 /tmp/hm-p-none.png", "window_gone"),
            ("wait 0x1 since 0", "window_gone"),
            ("eis open 0x1", "window_gone"),
            ("state", "invalid_arguments"),
            ("version now", "invalid_arguments"),
            ("cancel", "invalid_arguments"),
            ("frobnicate", "invalid_arguments"),
            (f"screenshot {green} relative.png", "invalid_arguments"),
            (f"screenshot {green} /tmp/hm-p-none.png 640 0 10 10", "invalid_arguments"),
            (f"wait {stated} since x1", "invalid_arguments"),
            (f"wait {stated} since 0 deadline 60001", "invalid_arguments"),
            (f"wait {stated} since 0 expect 0", "invalid_arguments"),
            (f"wait {stated} since 0 quiet", "invalid_arguments"),
            (f"wait {stated} since 0 since 0", "invalid_arguments"),
            (f"wait {stated} since 0 later 5", "invalid_arguments"),
            # The counter never falls, so a baseline above it is from another counter.
            (f"wait {stated} since 1000000000", "invalid_arguments"),
            (f"wait {green} since 0", "no_socket"),
            (f"eis close {green}", "no_socket"),
        ]:
            reply = json.loads(hypr(f"-j hyprmasq {command}"))
            assert not reply["ok"] and reply["error"] and reply["code"] == code, (command, reply)
        machine.succeed("test ! -e /tmp/hm-p-none.png")
        status, output = machine.execute("su - alice -c " + shlex.quote(f"{ipc} hyprctl hyprmasq state 0x1"))
        assert output.startswith("error: no mapped window"), (status, output)

    with subtest("the commit counter rises on redraw, never falls, and sees popups open"):
        before = window_state(stated)["commits"]
        send(stated_path, "type", "echo counted<enter>")
        wait_for("commits after typing", lambda: window_state(stated)["commits"] > before, timeout=10)
        readings = []
        for _ in range(5):
            readings.append(window_state(stated)["commits"])
            time.sleep(0.2)
        shot = json.loads(hypr(f"-j hyprmasq screenshot {stated} /tmp/hyprmasq-p-counted.png"))
        after = window_state(stated)["commits"]
        assert readings == sorted(readings) and readings[-1] <= shot["commits"] <= after, (before, readings, shot, after)
        assert json.loads(hypr(f"-j hyprmasq screenshot {windows[1][1]} /tmp/hm-p-green.png"))["commits"] is None
        # A popup that opens and closes without any input counts. Nothing reads the counter in between, so the popup
        # has to be watched as it appears.
        focus_workspace(31)
        spawn("hyprmasq-focus-probe popup hm-p-popup", "p-popup")
        wait_for("popup probe", lambda: client("hm-p-popup") is not None)
        focus_workspace(4)
        popup_owner = client("hm-p-popup")["address"]
        open_socket(popup_owner)
        time.sleep(0.5)
        before = window_state(popup_owner)["commits"]
        # The brackets keep the pattern from matching the shell that runs pkill.
        machine.succeed("pkill -USR1 -f 'hyprmasq-focus-probe [p]opup'")
        machine.wait_until_succeeds("grep -q 'configured popup' /tmp/hyprmasq-p-popup.log", timeout=10)
        time.sleep(0.5)
        # Its appearance and its first buffer: a scan only when the counter is read would miss the buffer.
        opened = window_state(popup_owner)["commits"]
        assert opened >= before + 2, (before, opened)
        machine.succeed("pkill -USR1 -f 'hyprmasq-focus-probe [p]opup'")
        machine.wait_until_succeeds("grep -q 'popup closed' /tmp/hyprmasq-p-popup.log", timeout=10)
        wait_for("popup gone", lambda: window_state(popup_owner)["commits"] > opened, timeout=10)

    with subtest("every socket has its own id, which the replies with its counter carry"):
        first = window_state(stated)["socketId"]
        assert first and eis_entry(stated)["socketId"] == first, first
        assert re.search(rf"^socketId: {re.escape(first)}$", hypr(f"hyprmasq state {stated}"), re.M)
        shot = json.loads(hypr(f"-j hyprmasq screenshot {stated} /tmp/hyprmasq-p-socket-id.png"))
        assert shot["socketId"] == first, shot
        reply = json.loads(hypr(f"-j hyprmasq wait {stated} since {shot['commits']} quiet 50 deadline 1000"))
        assert reply["socketId"] == first, reply
        assert json.loads(hypr(f"-j hyprmasq screenshot {windows[1][1]} /tmp/hm-p-green.png"))["socketId"] is None
        assert window_state(windows[1][1])["socketId"] is None
        # Opened again, the socket has another id, and its counter starts again.
        assert hypr(f"hyprmasq eis close {stated}") == "ok"
        reopened = json.loads(hypr(f"-j hyprmasq eis open {stated}"))
        assert reopened["path"] == stated_path and reopened["socketId"] not in (None, first), reopened
        state = window_state(stated)
        assert state["socketId"] == reopened["socketId"] and state["commits"] < shot["commits"], (state, shot)

    with subtest("waiting for a change since a commit count"):
        def wait_since(address, arguments):
            return json.loads(hypr(f"-j hyprmasq wait {address} {arguments}"))

        # Typing redraws the hidden shell, also before the wait starts.
        base = window_state(stated)["commits"]
        send(stated_path, "type", "echo changed<enter>")
        reply = wait_since(stated, f"since {base} expect 2000")
        assert reply["ok"] and reply["ended"] == "quiet" and reply["redrawObserved"], reply
        assert reply["commitsSince"] == reply["commits"] - base > 0 and reply["quietMs"] >= 200, reply
        # An idle window does not change within expect.
        time.sleep(1.5)
        base = window_state(stated)["commits"]
        reply = wait_since(stated, f"since {base} expect 300")
        assert reply["ended"] == "no_redraw" and not reply["redrawObserved"] and reply["commitsSince"] == 0, reply
        assert 300 <= reply["elapsedMs"] < 1000 and reply["commits"] == base, reply
        # A change before the wait ends the change phase at once.
        reply = wait_since(stated, f"since {base - 1} expect 4000 quiet 100")
        assert reply["ended"] == "quiet" and reply["redrawObserved"] and reply["commitsSince"] >= 1, reply
        assert reply["elapsedMs"] < 1000, reply
        # A hidden window that keeps drawing gets frame callbacks during the wait, and never goes quiet.
        focus_workspace(32)
        drawing = foot("hm-p-drawing", "sh -c 'while :; do date +%N; sleep 0.02; done'")
        focus_workspace(4)
        open_socket(drawing)
        base = window_state(drawing)["commits"]
        reply = wait_since(drawing, f"since {base} quiet 200 deadline 1000")
        assert reply["ended"] == "timeout" and reply["redrawObserved"] and reply["commitsSince"] > 10, reply
        assert 1000 <= reply["elapsedMs"] < 1500 and reply["quietMs"] < 200, reply
        dispatch('hl.dsp.window.close({ window = "address:' + drawing + '" })')

        # cancel ends a pending wait by its id, once.
        base = window_state(stated)["commits"]
        hypr_background(f"-j hyprmasq wait {stated} since {base} expect 4000 id hm-p-cancel", "/tmp/hm-p-cancel")
        wait_for("cancel", lambda: json.loads(hypr("-j hyprmasq cancel hm-p-cancel"))["cancelled"], timeout=3)
        reply = background_reply("/tmp/hm-p-cancel", timeout=10)
        assert reply["ok"] and reply["ended"] == "cancelled" and reply["elapsedMs"] < 4000, reply
        assert json.loads(hypr("-j hyprmasq cancel hm-p-cancel")) == {"ok": True, "cancelled": False}
        assert hypr("hyprmasq cancel hm-p-cancel") == "no pending wait"

        # plugin load answers the batch it is in with its own reply, so a wait in that batch cannot answer. Hyprland keeps
        # running instead of waiting for the wait, which still runs until it ends.
        base = window_state(stated)["commits"]
        reply = hypr("--batch " + shlex.quote(f"hyprmasq wait {stated} since {base} expect 4000 id hm-p-batched; plugin load {plugin}"))
        assert "twice" in reply, reply
        assert json.loads(hypr("-j hyprmasq cancel hm-p-batched")) == {"ok": True, "cancelled": True}

        # A window that closes during the wait ends it.
        focus_workspace(33)
        closing = foot("hm-p-closing")
        focus_workspace(4)
        open_socket(closing)
        base = window_state(closing)["commits"]
        hypr_background(f"-j hyprmasq wait {closing} since {base} expect 4000", "/tmp/hm-p-closed")
        time.sleep(1)
        dispatch('hl.dsp.window.close({ window = "address:' + closing + '" })')
        reply = background_reply("/tmp/hm-p-closed", timeout=10)
        assert reply["ok"] and reply["ended"] == "window_closed" and reply["elapsedMs"] < 4000, reply

    with subtest("a hidden window keeps drawing during a long wait"):
        # The shell prints for 3 seconds after the last key. Input alone gives a hidden window frame callbacks for a
        # second; the wait keeps them coming, past hyprctl's 5 seconds.
        base = window_state(stated)["commits"]
        send(stated_path, "type", "for i in $(seq 30); do echo line $i; sleep 0.1; done<enter>")
        hypr_socket_background(f"j/hyprmasq wait {stated} since {base} expect 2000 quiet 2000 deadline 15000", "/tmp/hm-p-long")
        time.sleep(2)
        middle = window_state(stated)["commits"]
        reply = background_reply("/tmp/hm-p-long")
        assert reply["ok"] and reply["ended"] == "quiet" and reply["commits"] > middle, (middle, reply)
        assert 4500 <= reply["elapsedMs"] < 15000 and reply["quietMs"] >= 2000, reply
        machine.copy_from_machine(screenshot(stated, "p-long"))

    def commits_over(address, seconds):
        before = window_state(address)["commits"]
        time.sleep(seconds)
        return window_state(address)["commits"] - before

    with subtest("a hidden window keeps drawing while a sender pauses between inputs"):
        # The shell keeps printing, which foot draws only with frame callbacks. Input gives them for a second; a sender
        # that is still connected keeps them coming, and they stop once it has gone.
        focus_workspace(21)
        painter = foot("hm-p-painter", "sh -c 'while :; do date +%N; sleep 0.02; done'")
        focus_workspace(4)
        painter_path = open_socket(painter)
        alice(f"nohup sh -c {shlex.quote(f'hyprmasq-input --socket {painter_path} move 10 10 sleep 3000; echo $? > /tmp/hm-p-paused.status')} > /dev/null 2>&1 &")
        time.sleep(1.5)
        assert commits_over(painter, 1) > 10
        machine.wait_until_succeeds("test -s /tmp/hm-p-paused.status", timeout=10)
        time.sleep(0.5)
        assert commits_over(painter, 1) <= 3

    with subtest("a wait keeps a window drawing that is hidden while it runs"):
        # Visible at the start of the wait, the window gets its frame callbacks from Hyprland. Once it is hidden, only
        # the wait sends them.
        focus_workspace(21)
        time.sleep(0.5)
        base = window_state(painter)["commits"]
        hypr_background(f"-j hyprmasq wait {painter} since {base} quiet 3000 deadline 4000", "/tmp/hm-p-hidden-wait")
        time.sleep(0.3)
        focus_workspace(4)
        time.sleep(0.3)
        assert commits_over(painter, 1) > 10
        reply = background_reply("/tmp/hm-p-hidden-wait")
        assert reply["ended"] == "timeout" and reply["quietMs"] < 3000, reply
        dispatch(f'hl.dsp.window.close({{ window = "address:{painter}" }})')

    with subtest("unloading answers pending waits with code unloaded"):
        base = window_state(stated)["commits"]
        hypr_background(f"-j hyprmasq wait {stated} since {base} expect 4000", "/tmp/hm-p-unload")
        time.sleep(1)
        assert hypr(f"plugin unload {plugin}") == "ok"
        reply = background_reply("/tmp/hm-p-unload", timeout=10)
        assert not reply["ok"] and reply["code"] == "unloaded" and "unloaded" in reply["error"], reply
        assert hypr(f"plugin load {plugin}") == "ok"
        assert json.loads(hypr("-j hyprmasq eis list")) == []
        assert window_state(stated)["commits"] is None

    def shot(address, name, arguments=""):
        path = f"/tmp/hyprmasq-p-{name}.png"
        reply = json.loads(hypr(f"-j hyprmasq screenshot {address} {path} {arguments}"))
        assert reply["ok"], (arguments, reply)
        machine.copy_from_machine(path)
        return path, reply

    def pixels(path, *points):
        """The RGBA values of the image at the points, and its size last."""
        script = f"""
import json
from PIL import Image
im = Image.open({path!r}).convert('RGBA')
print(json.dumps([im.getpixel(p) for p in {list(points)!r}] + [im.size]))
"""
        return [tuple(v) for v in json.loads(machine.succeed("python3 - <<'PY'\n" + script + "\nPY"))]

    def near(pixel, rgb):
        return pixel[3] > 245 and all(abs(a - b) <= 12 for a, b in zip(pixel, rgb))

    def region(x, y, width, height):
        return {"x": x, "y": y, "width": width, "height": height}

    def geometry(address):
        window = next(c for c in clients() if c["address"] == address)
        return window["at"], window["size"]

    def float_window(address, x, y, width, height):
        window = f'window = "address:{address}"'
        dispatch(f"hl.dsp.window.float({{ action = \"on\", {window} }})")
        dispatch(f"hl.dsp.window.resize({{ x = {width}, y = {height}, {window} }})")
        dispatch(f"hl.dsp.window.move({{ x = {x}, y = {y}, {window} }})")
        wait_for(f"{address} at {x},{y} sized {width}x{height}", lambda: geometry(address) == ([x, y], [width, height]))

    def popup_probe(title, workspace, *popup):
        """A floating 300x200 focus-probe window at 100,100 on a hidden workspace, with its popup open at popup."""
        focus_workspace(workspace)
        spawn(f"hyprmasq-focus-probe popup {title} " + " ".join(map(str, popup)), title.removeprefix("hm-"))
        wait_for(title, lambda: client(title) is not None)
        address = client(title)["address"]
        float_window(address, 100, 100, 300, 200)
        focus_workspace(4)
        machine.succeed(f"pkill -USR1 -f 'hyprmasq-focus-probe [p]opup {title} '")
        machine.wait_until_succeeds(f"grep -q 'configured popup' /tmp/hyprmasq-{title.removeprefix('hm-')}.log", timeout=10)
        time.sleep(0.5)
        return address

    # The probe's colors: the window is 224466, its popups cc2222.
    window_rgb, popup_rgb = (0x22, 0x44, 0x66), (0xCC, 0x22, 0x22)

    with subtest("screenshots fit within a maximum size, rendered at that size"):
        green = windows[1][1]
        path, reply = shot(green, "max-half", "max 320")
        assert (reply["width"], reply["height"], reply["scale"]) == (320, 240, 0.5), reply
        assert reply["region"] == region(0, 0, 640, 480) and pixels(path)[-1] == (320, 240), reply
        assert_png(path, (0, 255, 0))
        # A quarter is halved twice; a fifth also gets a bilinear pass after the halving.
        for maximum, width, height in [(160, 160, 120), (128, 128, 96), (100, 100, 75)]:
            path, reply = shot(green, f"max-{maximum}", f"max {maximum}")
            assert (reply["width"], reply["height"]) == (width, height) and reply["scale"] == maximum / 640, reply
            assert reply["region"] == region(0, 0, 640, 480), reply
            assert near(pixels(path, (width // 2, height // 2))[0], (0, 255, 0)), maximum
        assert_png("/tmp/hyprmasq-p-max-160.png", (0, 255, 0))
        # Never above the monitor's scale, and an image that already fits is not touched.
        for maximum in [640, 4000]:
            _, reply = shot(green, f"max-full-{maximum}", f"max {maximum}")
            assert (reply["width"], reply["height"], reply["scale"]) == (640, 480, 1), reply
        # A region is fitted the same way. Its pixels start at its origin, and the size widens to whole image pixels.
        path, reply = shot(green, "max-region", "0 0 200 30 max 100")
        assert (reply["width"], reply["height"], reply["scale"]) == (100, 15, 0.5) and reply["region"] == region(0, 0, 200, 30), reply
        _, reply = shot(green, "max-odd", "3 5 101 51 max 50")
        assert (reply["width"], reply["height"]) == (50, 26) and reply["scale"] == 50 / 101, reply
        assert reply["region"]["x"] == 3 and reply["region"]["y"] == 5 and abs(reply["region"]["height"] - 26 * 101 / 50) < 1e-9, reply
        for arguments, message in [
            ("max 0", "max needs"),
            ("max x", "max needs"),
            ("max", "usage"),
            ("max 10 max 20", "usage"),
            ("popups popups", "usage"),
            ("0 0 10 10 max 10 later", "usage"),
        ]:
            reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-p-bad.png {arguments}"))
            assert not reply["ok"] and reply["code"] == "invalid_arguments" and message in reply["error"], (arguments, reply)
        machine.succeed("test ! -e /tmp/hm-p-bad.png")

        # scale is image pixels per logical pixel, below the monitor's when max shrank the image.
        def monitor_scale(scale, width):
            hypr("eval " + shlex.quote(
                f'hl.monitor({{ output = "HYPRMASQ-TEST", mode = "640x480@60", position = "0x0", scale = "{scale}" }})'
            ))
            wait_for(f"scale {scale}", lambda: client("hm-green")["size"][0] == width)
            time.sleep(0.5)

        monitor_scale(2, 320)
        _, reply = shot(green, "max-scale2", "max 640")
        assert (reply["width"], reply["height"], reply["scale"]) == (640, 480, 2), reply
        path, reply = shot(green, "max-scale2-shrunk", "max 320")
        assert (reply["width"], reply["height"], reply["scale"]) == (320, 240, 1), reply
        assert reply["region"] == region(0, 0, 320, 240), reply
        assert_png(path, (0, 255, 0))
        monitor_scale(1.25, 512)
        _, reply = shot(green, "max-fractional", "max 256")
        assert (reply["width"], reply["height"], reply["scale"]) == (256, 192, 0.5) and reply["region"] == region(0, 0, 512, 384), reply
        monitor_scale(1, 640)

    with subtest("popups outside the window are captured and get pointer input"):
        # The popup covers 250,150 to 350,250 of the 300x200 window, so it reaches past its right and bottom edges.
        popout = popup_probe("hm-p-popout", 34, 250, 150, 100, 100)
        machine.succeed("grep -q 'popup at 250 150 100 100' /tmp/hyprmasq-p-popout.log")
        # Without popups, the box is the window, with the part of the popup over it, also on a hidden workspace.
        path, reply = shot(popout, "popout-window")
        assert (reply["width"], reply["height"]) == (300, 200) and reply["region"] == region(0, 0, 300, 200), reply
        inside, window_pixel, _ = pixels(path, (275, 175), (50, 50))
        assert near(inside, popup_rgb) and near(window_pixel, window_rgb), (inside, window_pixel)
        # With popups, the box grows to the popup. Pixels outside both stay transparent.
        path, reply = shot(popout, "popout-popups", "popups")
        assert (reply["width"], reply["height"]) == (350, 250) and reply["region"] == region(0, 0, 350, 250), reply
        outside, window_pixel, empty, size = pixels(path, (330, 230), (50, 50), (330, 50))
        assert near(outside, popup_rgb) and near(window_pixel, window_rgb) and empty[3] == 0 and size == (350, 250), (outside, window_pixel, empty)
        # A region can reach into the box, and is cut to it.
        path, reply = shot(popout, "popout-region", "280 180 100 100 popups")
        assert (reply["width"], reply["height"]) == (70, 70) and reply["region"] == region(280, 180, 70, 70), reply
        assert all(near(p, popup_rgb) for p in pixels(path, (5, 5), (65, 65), (35, 35))[:-1])
        _, reply = shot(popout, "popout-region-window", "280 180 100 100")
        assert reply["region"] == region(280, 180, 20, 20), reply
        _, reply = shot(popout, "popout-popups-max", "popups max 175")
        assert (reply["width"], reply["height"], reply["scale"]) == (175, 125, 0.5) and reply["region"] == region(0, 0, 350, 250), reply
        reply = json.loads(hypr(f"-j hyprmasq screenshot {popout} /tmp/hm-p-bad.png 400 300 10 10 popups"))
        assert not reply["ok"] and reply["code"] == "invalid_arguments" and "outside" in reply["error"], reply

        # The pointer region covers the window and the rest of its monitor right of and below it.
        popout_path = open_socket(popout)
        alice(f"hyprmasq-focus-probe regions {shlex.quote(popout_path)} 500 > /tmp/hyprmasq-p-regions.log")
        assert machine.succeed("cat /tmp/hyprmasq-p-regions.log") == "pointer 0 0 300 200, 300 0 240 200, 0 200 540 180\n"
        # A click on the part of the popup outside the window reaches the popup, at its own coordinates.
        offset = log_size("p-popout")
        send(popout_path, "move", 330, 220, "click")
        machine.wait_until_succeeds("grep -q 'button popup 0' /tmp/hyprmasq-p-popout.log", timeout=10)
        events = log_since("p-popout", offset)
        for line in ["pointer enter popup", "motion popup 80 70", "button popup 1", "button popup 0"]:
            assert line in events.splitlines(), events
        assert "button hm-p-popout" not in events, events
        # A point outside the window and its popups is clamped to the window.
        offset = log_size("p-popout")
        send(popout_path, "move", 500, 50, "click")
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-popout.log | grep -q 'button hm-p-popout 0'", timeout=10)
        events = log_since("p-popout", offset)
        assert "motion hm-p-popout 299 50" in events.splitlines() and "button popup" not in events, events

    with subtest("a region with popups can start above and left of the window"):
        # The popup covers -40,-30 to 20,20 of the window.
        popleft = popup_probe("hm-p-popleft", 35, -40, -30, 60, 50)
        path, reply = shot(popleft, "popleft-popups", "popups")
        assert (reply["width"], reply["height"]) == (340, 230) and reply["region"] == region(-40, -30, 340, 230), reply
        popup_pixel, window_pixel, empty, _ = pixels(path, (5, 5), (90, 80), (300, 5))
        assert near(popup_pixel, popup_rgb) and near(window_pixel, window_rgb) and empty[3] == 0, (popup_pixel, window_pixel, empty)
        path, reply = shot(popleft, "popleft-region", "-40 -30 100 80 popups")
        assert (reply["width"], reply["height"]) == (100, 80) and reply["region"] == region(-40, -30, 100, 80), reply
        popup_pixel, window_pixel, _ = pixels(path, (5, 5), (90, 70))
        assert near(popup_pixel, popup_rgb) and near(window_pixel, window_rgb), (popup_pixel, window_pixel)
        # A region that starts before the box is cut to it.
        _, reply = shot(popleft, "popleft-cut", "-100 -100 100 100 popups")
        assert reply["region"] == region(-40, -30, 40, 30), reply
        # Without popups, a negative region is still an error.
        reply = json.loads(hypr(f"-j hyprmasq screenshot {popleft} /tmp/hm-p-bad.png -40 -30 100 80"))
        assert not reply["ok"] and reply["code"] == "invalid_arguments" and "non-negative" in reply["error"], reply
        machine.succeed("test ! -e /tmp/hm-p-bad.png")
        # The part of the popup over the window gets pointer input.
        offset = log_size("p-popleft")
        popleft_path = open_socket(popleft)
        send(popleft_path, "move", 10, 10, "click")
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-popleft.log | grep -q 'button popup 0'", timeout=10)
        assert "motion popup 50 40" in log_since("p-popleft", offset).splitlines()
        # The part above and left of the window cannot: libei region offsets are unsigned, so the sender refuses it.
        status, result = input_json(popleft_path, "move", -20, -10, "click")
        assert status == 2 and result["delivery"] == "none" and result["error"]["code"] == "invalid_arguments", (status, result)
        assert "-20,-10 is outside the pointer regions, which span 0,0 to " in result["error"]["message"], result

    with subtest("the pointer device follows resizes and moves"):
        def regions_line(address):
            (x, y), (width, height) = geometry(address)
            right, bottom = max(width, 640 - x), max(height, 480 - y)
            parts = [f"0 0 {width} {height}"]
            if right > width:
                parts.append(f"{width} 0 {right - width} {height}")
            if bottom > height:
                parts.append(f"0 {height} {right} {bottom - height}")
            return "pointer " + ", ".join(parts)

        def configures():
            return int(machine.succeed("grep -c 'configured hm-p-popout' /tmp/hyprmasq-p-popout.log"))

        before, configured = regions_line(popout), configures()
        spawn(f"hyprmasq-focus-probe regions {shlex.quote(popout_path)} 5000", "p-regions-follow")
        machine.wait_until_succeeds("grep -q pointer /tmp/hyprmasq-p-regions-follow.log", timeout=10)
        window = f'window = "address:{popout}"'
        dispatch(f"hl.dsp.window.resize({{ x = 400, y = 300, {window} }})")
        wait_for("resize", lambda: client("hm-p-popout")["size"] == [400, 300])
        resized = regions_line(popout)
        time.sleep(0.3)
        dispatch(f"hl.dsp.window.move({{ x = 150, y = 120, {window} }})")
        wait_for("move", lambda: client("hm-p-popout")["at"] == [150, 120])
        moved = regions_line(popout)
        machine.wait_until_fails("pgrep -f 'hyprmasq-focus-probe [r]egions'", timeout=10)
        lines = machine.succeed("cat /tmp/hyprmasq-p-regions-follow.log").splitlines()
        assert lines == [before, "pointer removed", resized, "pointer removed", moved], (lines, before, resized, moved)
        # Input reaches the parts of the window that only exist since the resize, once the probe has drawn them.
        wait_for("the probe drawn at its new size", lambda: configures() > configured, timeout=10)
        time.sleep(0.5)
        offset = log_size("p-popout")
        send(popout_path, "move", 380, 280, "click")
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-popout.log | grep -q 'button hm-p-popout 0'", timeout=10)
        assert "motion hm-p-popout 380 280" in log_since("p-popout", offset).splitlines()
        for title in ["hm-p-popout", "hm-p-popleft"]:
            dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
            wait_for(f"{title} closed", lambda: client(title) is None)

    with subtest("a window that has not caught up with a resize says so in its state, shots and waits"):
        # focus-probe's resize mode draws its right edge green. On SIGUSR1 it holds back configures with a new size,
        # neither acking nor drawing them with hold, acking them but not drawing with ack, and answers them on the next.
        edge_rgb = (0x22, 0xCC, 0x22)

        def probe_lines(name):
            return machine.succeed(f"cat /tmp/hyprmasq-{name}.log").splitlines()

        for mode in ["hold", "ack"]:
            title, name = f"hm-p-resize-{mode}", f"p-resize-{mode}"
            toggle = f"pkill -USR1 -f 'hyprmasq-focus-probe [r]esize {title} '"
            focus_workspace(26)
            spawn(f"hyprmasq-focus-probe resize {title} {mode}", name)
            wait_for(title, lambda: client(title) is not None)
            resized = client(title)["address"]
            # Alone on its workspace, it drew at the size Hyprland predicted before mapping it, and acked the configure
            # of that size after the mapping without a commit. Its content has the window's size, so it is not resizing.
            machine.wait_until_succeeds(f"grep -qx 'kept 640 480' /tmp/hyprmasq-{name}.log", timeout=10)
            assert not window_state(resized)["resizing"], window_state(resized)
            # A neighbour halves it, which it draws at once.
            neighbour = foot(f"hm-p-neighbour-{mode}", "sleep 900")
            focus_workspace(4)
            wait_for("the probe drawn at half the width", lambda: "drew 320 480" in probe_lines(name), timeout=10)
            state = window_state(resized)
            assert state["size"] == {"width": 320, "height": 480} and not state["resizing"], state
            open_socket(resized)

            # Closing the neighbour on the hidden workspace gives the probe the whole workspace, which it holds back.
            machine.succeed(toggle)
            machine.wait_until_succeeds(f"grep -qx holding /tmp/hyprmasq-{name}.log", timeout=10)
            dispatch(f'hl.dsp.window.close({{ window = "address:{neighbour}" }})')
            wait_for("the neighbour closed", lambda: client(f"hm-p-neighbour-{mode}") is None)
            machine.wait_until_succeeds(f"grep -qx 'held 640 480' /tmp/hyprmasq-{name}.log", timeout=10)
            state = window_state(resized)
            assert state["size"] == {"width": 640, "height": 480} and state["resizing"], state
            assert "\nresizing: true\n" in hypr(f"hyprmasq state {resized}")
            # The shot has the window's new size and shows the frame of the old one: it ends at 320 with the green edge,
            # beyond which Hyprland either leaves the image transparent or repeats the edge.
            path, shot_reply = shot(resized, f"resize-{mode}-stale")
            assert shot_reply["resizing"] and (shot_reply["width"], shot_reply["height"]) == (640, 480), shot_reply
            inside, edge, beyond, _ = pixels(path, (160, 240), (316, 240), (480, 240))
            machine.log(f"stale frame with {mode}: {inside} {edge} {beyond}")
            assert near(inside, window_rgb) and near(edge, edge_rgb), (inside, edge)
            assert beyond[3] == 0 or near(beyond, edge_rgb), beyond
            # A wait does not end quiet while the window has not caught up.
            reply = json.loads(hypr(f"-j hyprmasq wait {resized} since {shot_reply['commits']} quiet 100 deadline 600"))
            assert reply["ended"] == "timeout" and reply["resizing"], reply

            # A wait that runs when the probe answers ends once it has drawn at the new size and gone quiet. Without
            # the resize, it would have ended 100 ms after it started.
            output = f"/tmp/hm-p-resize-{mode}-wait"
            hypr_background(f"-j hyprmasq wait {resized} since {shot_reply['commits']} quiet 100 deadline 4000", output)
            time.sleep(0.5)
            machine.succeed(f"test ! -s {output}")
            machine.succeed(toggle)
            reply = background_reply(output, timeout=10)
            assert reply["ended"] == "quiet" and not reply["resizing"] and reply["redrawObserved"], reply
            assert "drew 640 480" in probe_lines(name)
            state = window_state(resized)
            assert state["size"] == {"width": 640, "height": 480} and not state["resizing"], state
            path, shot_reply = shot(resized, f"resize-{mode}-current")
            assert not shot_reply["resizing"], shot_reply
            inside, beyond, edge, _ = pixels(path, (160, 240), (480, 240), (636, 240))
            assert near(inside, window_rgb) and near(beyond, window_rgb) and near(edge, edge_rgb), (inside, beyond, edge)
            dispatch(f'hl.dsp.window.close({{ window = "address:{resized}" }})')
            wait_for(f"{title} closed", lambda: client(title) is None)

    def color_box(path, rgb):
        """The bounding box (x0, y0, x1, y1) of the opaque pixels of the image near rgb, or None."""
        script = f"""
import json
from PIL import Image
im = Image.open({path!r}).convert('RGBA')
found = [(x, y) for y in range(im.height) for x in range(im.width)
         if im.getpixel((x, y))[3] > 245 and all(abs(a - b) <= 12 for a, b in zip(im.getpixel((x, y)), {rgb!r}))]
print(json.dumps([min(x for x, _ in found), min(y for _, y in found), max(x for x, _ in found) + 1, max(y for _, y in found) + 1] if found else None))
"""
        return json.loads(machine.succeed("python3 - <<'PY'\n" + script + "\nPY"))

    with subtest("a popup of a window with an xdg geometry offset gets input where shots show it"):
        # The window has a margin of 30x20 above and left of its xdg window geometry, as client-side shadows do, and its
        # popup lies at 100,50 of that geometry. A click where the shot shows the popup reaches it at the point the shot
        # shows, and a click beside it reaches the window.
        geometry_probe = popup_probe("hm-p-geometry", 22, 100, 50, 60, 40, 30, 20)
        path, reply = shot(geometry_probe, "geometry-popups", "popups")
        box = color_box(path, popup_rgb)
        machine.log(f"popup of a window with a geometry offset: {box} in {reply['region']}")
        assert box and box[2] - box[0] == 60 and box[3] - box[1] == 40, box
        left, top = reply["region"]["x"] + box[0], reply["region"]["y"] + box[1]
        geometry_path = open_socket(geometry_probe)
        offset = log_size("p-geometry")
        send(geometry_path, "move", left + 5, top + 7, "click")
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-geometry.log | grep -q 'button popup 0'", timeout=10)
        assert "motion popup 5 7" in log_since("p-geometry", offset).splitlines(), log_since("p-geometry", offset)
        offset = log_size("p-geometry")
        send(geometry_path, "move", left + 70, top + 30, "click")
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-geometry.log | grep -q 'button hm-p-geometry 0'", timeout=10)
        events = log_since("p-geometry", offset)
        assert f"motion hm-p-geometry {left + 70} {top + 30}" in events.splitlines() and "button popup" not in events, events
        dispatch(f'hl.dsp.window.close({{ window = "address:{geometry_probe}" }})')
        wait_for("hm-p-geometry closed", lambda: client("hm-p-geometry") is None)

    def animations(on):
        """Turns on slow window animations, 10 seconds and linear, in which windows pop in from half their size, with
        nothing else animated, or turns all animations off again, as the test's configuration has them."""
        if on:
            code = ('hl.config({ animations = { enabled = true } }) hl.animation({ leaf = "global", enabled = false }) '
                    'hl.animation({ leaf = "windows", enabled = true, speed = 100, bezier = "linear", style = "popin 50%" })')
        else:
            code = "hl.config({ animations = { enabled = false } })"
        assert hypr("eval " + shlex.quote(code)) == "ok"

    def screen_box(name, rgb):
        """The box of the pixels near rgb on the monitor, captured with grim."""
        return color_box(capture_output(name), rgb)

    with subtest("shots taken while Hyprland animates a window's size show the window at the size input uses"):
        # While a window pops in, Hyprland draws it on screen at the animated size, fitting its content to it.
        animations(True)
        edge_rgb, white, black = (0x22, 0xCC, 0x22), (255, 255, 255), (0, 0, 0)
        # A Wayland window, which draws its right edge green. Once it has drawn at its size, the shot has that size and
        # the edge where the probe drew it, state says so and is not resizing, and a wait ends quiet, while the screen
        # still shows the window smaller, with its edge well left of the monitor's.
        focus_workspace(27)
        spawn("hyprmasq-focus-probe resize hm-p-anim hold", "p-anim")
        wait_for("hm-p-anim", lambda: client("hm-p-anim") is not None)
        anim = client("hm-p-anim")["address"]
        machine.wait_until_succeeds("grep -qx 'kept 640 480' /tmp/hyprmasq-p-anim.log", timeout=10)
        anim_path = open_socket(anim)
        path, reply = shot(anim, "anim")
        state = window_state(anim)
        waited = json.loads(hypr(f"-j hyprmasq wait {anim} since {reply['commits']} quiet 100 deadline 2000"))
        on_screen = screen_box("p-anim-screen", edge_rgb)
        machine.log(f"Wayland window popping in: its edge on screen at {on_screen}, in the shot at {color_box(path, edge_rgb)}")
        assert on_screen and on_screen[2] < 630, on_screen
        assert (reply["width"], reply["height"]) == (640, 480) and reply["region"] == region(0, 0, 640, 480) and not reply["resizing"], reply
        assert color_box(path, edge_rgb) == [632, 0, 640, 480] and near(pixels(path, (600, 240))[0], window_rgb), path
        assert state["size"] == {"width": 640, "height": 480} and not state["resizing"], state
        assert waited["ended"] == "quiet" and not waited["resizing"], waited
        # The edge in the shot is where input reaches it.
        focus_workspace(4)
        offset = log_size("p-anim")
        send(anim_path, "move", 636, 240)
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-anim.log | grep -qx 'motion hm-p-anim 636 240'", timeout=10)

        # An X11 window, which Hyprland scales into the animated size. xev draws a white window with an inner square
        # that has a black border. Its shot during the animation is the shot after it.
        focus_workspace(28)
        x_anim = xev("hm-x-anim", "x-anim")
        wait_for("xev drawn", lambda: color_box(shot(x_anim, "x-anim")[0], black) is not None, timeout=10)
        path, reply = shot(x_anim, "x-anim")
        on_screen = screen_box("p-x-anim-screen", white)
        x_square = color_box(path, black)
        machine.log(f"X11 window popping in: on screen at {on_screen}, its square in the shot at {x_square}")
        assert on_screen and on_screen[2] - on_screen[0] < 620, on_screen
        assert reply["region"] == region(0, 0, 640, 480) and not reply["resizing"] and not window_state(x_anim)["resizing"], reply
        # xev's inner window lies at 10,10, 50x50 with a border of 4.
        assert x_square == [10, 10, 68, 68], x_square
        wait_for("the animation to end", lambda: screen_box("p-x-anim-screen-end", white) == [0, 0, 640, 480], timeout=20)
        path, reply = shot(x_anim, "x-anim-end")
        assert reply["region"] == region(0, 0, 640, 480) and color_box(path, black) == x_square, (reply, color_box(path, black))
        focus_workspace(4)
        animations(False)
        for address in (anim, x_anim):
            dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
        wait_for("animated windows closed", lambda: all(c["address"] not in (anim, x_anim) for c in clients()))

    with subtest("input reaches the popups of a moving window where shots show them"):
        # Hyprland tests for popups at the window's animated position, while shots show them at the same place in the
        # window wherever the window is on its way. The window moves from 100,100 to 300,250 in 10 seconds, and its
        # popup lies at 150,100 of it, 100x60. Counted from where the window ends its move, the point 175,130 of the
        # window lies on the popup at its animated position only from 8 seconds into the move, so a click in the first 7
        # seconds tells the two apart.
        moving = popup_probe("hm-p-moving", 29, 150, 100, 100, 60)
        moving_path = open_socket(moving)
        animations(True)
        started = time.monotonic()
        dispatch(f'hl.dsp.window.move({{ x = 300, y = 250, window = "address:{moving}" }})')
        wait_for("move", lambda: geometry(moving)[0] == [300, 250])
        path, _ = shot(moving, "moving-popups", "popups")
        offset = log_size("p-moving")
        send(moving_path, "move", 175, 130, "click")
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-moving.log | grep -q 'button popup 0'", timeout=10)
        elapsed = time.monotonic() - started
        events = log_since("p-moving", offset)
        machine.log(f"a click {elapsed:.1f} s into the move of a window: {events}")
        assert elapsed < 7, elapsed
        assert color_box(path, popup_rgb) == [150, 100, 250, 160], color_box(path, popup_rgb)
        assert "motion popup 25 30" in events.splitlines() and "button hm-p-moving" not in events, events
        animations(False)
        dispatch(f'hl.dsp.window.close({{ window = "address:{moving}" }})')
        wait_for("hm-p-moving closed", lambda: client("hm-p-moving") is None)

    # focus-probe's focus mode logs every input and focus event of its windows after a CLOCK_MONOTONIC timestamp.
    def probe_events(log, start=0):
        """The timestamped events of a focus-probe log from entry start on, as (ms, event) pairs."""
        lines = machine.succeed(f"cat /tmp/hyprmasq-{log}.log").splitlines()
        return [(int(t), event) for t, _, event in (line.partition(" ") for line in lines) if t.isdigit()][start:]

    def vm_now():
        """CLOCK_MONOTONIC in the VM, in milliseconds, as focus-probe stamps its events."""
        return int(machine.succeed("python3 -c 'import time; print(int(time.monotonic() * 1000))'"))

    def lease(address, *arguments):
        return json.loads(hypr(f"-j hyprmasq lease {address} " + " ".join(map(str, arguments))))

    def last_input(events):
        return max(t for t, e in events if e.startswith(("motion ", "key ", "button ")))

    lease_leaves = {"keyboard leave hm-p-lease", "pointer leave hm-p-lease"}

    def wait_for_leaves(start, expected=lease_leaves, timeout=10):
        wait_for("leaves", lambda: expected <= {e for _, e in probe_events("p-lease", start)}, timeout=timeout)
        return probe_events("p-lease", start)

    with subtest("a focus lease keeps the emulated focus between inputs"):
        import re

        focus_workspace(36)
        spawn("hyprmasq-focus-probe focus hm-p-lease hm-p-lease-sibling", "p-lease")
        wait_for("lease probe", lambda: client("hm-p-lease") and client("hm-p-lease-sibling"))
        # Two apps of their own, to move the real focus between.
        focus_workspace(37)
        lease_left = foot("hm-p-left")
        lease_right = foot("hm-p-right")
        focus_workspace(4)
        time.sleep(0.5)
        leased = client("hm-p-lease")["address"]
        lease_path = open_socket(leased)
        assert window_state(leased)["capabilities"]["lease"]

        # Without a lease, the focus ends a second after input while the sender stays connected,
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 50, 50, "key", 30, "sleep", 2000)
        events = wait_for_leaves(start)
        left = [t - last_input(events) for t, e in events if e in lease_leaves]
        assert len(left) == 2 and all(800 <= d <= 1800 for d in left), events
        # and at once when it stops.
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 50, 50, "key", 30)
        events = wait_for_leaves(start)
        left = [t - last_input(events) for t, e in events if e in lease_leaves]
        assert len(left) == 2 and all(0 <= d < 500 for d in left), events

        # With a lease, it stays after the sender has gone, and the pointer stays where the last motion put it.
        before = time.monotonic()
        reply = lease(leased, 10000, "hm-p-owner")
        after = time.monotonic()
        assert reply["ok"] and reply["held"] and reply["owner"] == "hm-p-owner" and 9000 < reply["remainingMs"] <= 10000, reply
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 60, 70, "key", 30, "sleep", 1500)
        time.sleep(2)
        events = probe_events("p-lease", start)
        assert ["pointer enter hm-p-lease", "motion hm-p-lease 60 70"] == [e for _, e in events][:2], events
        assert vm_now() - last_input(events) >= 3000 and not any("leave" in e for _, e in events), events
        # state reports the lease and its remaining time.
        state_before = time.monotonic()
        held = window_state(leased)["lease"]
        state_after = time.monotonic()
        assert held["held"] and held["owner"] == "hm-p-owner", held
        assert 10000 - (state_after - before) * 1000 - 50 <= held["remainingMs"] <= 10000 - (state_before - after) * 1000 + 50, held
        assert re.search(r"^lease: \d+ ms by hm-p-owner$", hypr(f"hyprmasq state {leased}"), re.M), hypr(f"hyprmasq state {leased}")

        # The real focus moving between other apps, and the real pointer between their windows, leave it alone.
        start = len(probe_events("p-lease"))
        focus_workspace(37)
        dispatch(f'hl.dsp.focus({{ window = "address:{lease_left}" }})')
        wait_for("left focus", lambda: snapshot()[1] == lease_left)
        dispatch("hl.dsp.cursor.move({ x = 100, y = 240 })")
        dispatch("hl.dsp.cursor.move({ x = 500, y = 240 })")
        dispatch(f'hl.dsp.focus({{ window = "address:{lease_right}" }})')
        wait_for("right focus", lambda: snapshot()[1] == lease_right)
        focus_workspace(4)
        time.sleep(0.5)
        assert probe_events("p-lease", start) == [], probe_events("p-lease", start)
        assert window_state(leased)["lease"]["owner"] == "hm-p-owner"

        # Only its owner releases it, and the focus ends then.
        assert lease(leased, "release", "someone-else") == {"ok": True, "released": False}
        assert window_state(leased)["lease"]["owner"] == "hm-p-owner"
        start, released = len(probe_events("p-lease")), vm_now()
        assert lease(leased, "release", "hm-p-owner") == {"ok": True, "released": True}
        events = wait_for_leaves(start, timeout=5)
        assert all(0 <= t - released < 1000 for t, e in events if e in lease_leaves), (released, events)
        assert window_state(leased)["lease"] is None
        assert lease(leased, "release") == {"ok": True, "released": False}

        # It ends when it expires, also later than a second after input. Renewing takes it over.
        assert lease(leased, 1000, "hm-p-owner")["held"]
        assert lease(leased, 3000, "hm-p-renewer")["owner"] == "hm-p-renewer"
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 50, 50)
        events = wait_for_leaves(start, {"pointer leave hm-p-lease"})
        left = [t - last_input(events) for t, e in events if e == "pointer leave hm-p-lease"]
        assert len(left) == 1 and 1500 <= left[0] <= 3500, events
        assert window_state(leased)["lease"] is None

        # Plain text.
        assert re.fullmatch(r"held \d+ ms by hm-p-text", hypr(f"hyprmasq lease {leased} 5000 hm-p-text")), hypr(f"hyprmasq state {leased}")
        assert hypr(f"hyprmasq lease {leased} release") == "released"
        assert hypr(f"hyprmasq lease {leased} release") == "no lease released"

    with subtest("a lease keeps only the keyboard while the real pointer is on its connection"):
        # The probe's second window shares its connection. Beside the apps that have the real focus, it gets the real
        # pointer once the pointer focus follows the cursor alone, as it does with follow_mouse = 2.
        sibling = client("hm-p-lease-sibling")["address"]
        dispatch(f'hl.dsp.window.move({{ window = "address:{sibling}", workspace = "37", follow = false }})')
        focus_workspace(37)
        dispatch(f'hl.dsp.focus({{ window = "address:{lease_left}" }})')
        wait_for("left focus", lambda: snapshot() == (37, lease_left))
        time.sleep(0.5)
        assert lease(leased, 30000, "hm-p-owner")["held"]
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 50, 50, "key", 30)
        hypr("eval " + shlex.quote("hl.config({ input = { follow_mouse = 2 } })"))
        (x, y), (width, height) = client("hm-p-lease-sibling")["at"], client("hm-p-lease-sibling")["size"]
        dispatch(f"hl.dsp.cursor.move({{ x = {x + width // 2}, y = {y + height // 2} }})")
        wait_for("real pointer on the sibling", lambda: "pointer enter hm-p-lease-sibling" in [e for _, e in probe_events("p-lease", start)], timeout=5)
        time.sleep(0.5)
        events = [e for _, e in probe_events("p-lease", start)]
        assert "pointer leave hm-p-lease" in events and "keyboard leave hm-p-lease" not in events, events
        assert window_state(leased)["lease"]["owner"] == "hm-p-owner"
        (x, y), (width, height) = client("hm-p-left")["at"], client("hm-p-left")["size"]
        dispatch(f"hl.dsp.cursor.move({{ x = {x + width // 2}, y = {y + height // 2} }})")
        hypr("eval " + shlex.quote("hl.config({ input = { follow_mouse = 0 } })"))
        dispatch(f'hl.dsp.window.move({{ window = "address:{sibling}", workspace = "36", follow = false }})')
        focus_workspace(4)

    with subtest("a lease ends when input reaches another window of its connection"):
        assert window_state(leased)["lease"]["owner"] == "hm-p-owner"
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 50, 50, "key", 30)
        sibling_path = open_socket(sibling)
        send(sibling_path, "move", 10, 10, "key", 30)
        events = wait_for_leaves(start)
        assert "keyboard enter hm-p-lease-sibling" in [e for _, e in events], events
        assert window_state(leased)["lease"] is None
        assert hypr(f"hyprmasq eis close {sibling}") == "ok"

    with subtest("a lease ends when the real focus moves to its connection"):
        # The probe's second window shares its connection.
        assert lease(leased, 30000, "hm-p-owner")["held"]
        start = len(probe_events("p-lease"))
        send(lease_path, "move", 50, 50, "key", 30)
        time.sleep(0.5)
        dispatch(f'hl.dsp.focus({{ window = "address:{sibling}" }})')
        wait_for("sibling focus", lambda: snapshot()[1] == sibling)
        assert window_state(leased)["lease"] is None
        events = wait_for_leaves(start, timeout=5)
        assert "keyboard enter hm-p-lease-sibling" in [e for _, e in events], events
        # While the user types into that connection, a new lease is refused.
        reply = lease(leased, 30000, "hm-p-owner")
        assert not reply["ok"] and reply["code"] == "unsupported" and "keyboard focus" in reply["error"], reply
        assert not window_state(leased)["capabilities"]["lease"]
        focus_workspace(4)

        # Two footclient windows of one foot server.
        spawn("foot --server=/tmp/hyprmasq-p-foot.sock", "p-foot-server")
        machine.wait_until_succeeds("test -S /tmp/hyprmasq-p-foot.sock")
        focus_workspace(38)
        shared_hidden = foot("hm-p-shared-hidden", executable="footclient -s /tmp/hyprmasq-p-foot.sock")
        focus_workspace(39)
        shared_focused = foot("hm-p-shared-focused", executable="footclient -s /tmp/hyprmasq-p-foot.sock")
        wait_for("shared focus", lambda: snapshot() == (39, shared_focused))
        shared_path = open_socket(shared_hidden)
        reply = lease(shared_hidden, 30000, "hm-p-owner")
        assert not reply["ok"] and reply["code"] == "unsupported" and "keyboard focus" in reply["error"], reply
        assert window_state(shared_hidden)["lease"] is None and not window_state(shared_hidden)["capabilities"]["lease"]
        # While another app has the focus, it holds, until the focus moves to the other footclient.
        focus_workspace(4)
        assert lease(shared_hidden, 30000, "hm-p-owner")["held"]
        send(shared_path, "move", 10, 10, "key", 42)
        assert window_state(shared_hidden)["lease"]["owner"] == "hm-p-owner"
        dispatch(f'hl.dsp.focus({{ window = "address:{shared_focused}" }})')
        wait_for("shared focus", lambda: snapshot()[1] == shared_focused)
        assert window_state(shared_hidden)["lease"] is None
        focus_workspace(4)

    with subtest("leases are refused for X11 windows and bad arguments"):
        focus_workspace(38)
        x_leased = xterm("hm-p-xterm")
        focus_workspace(4)
        open_socket(x_leased)
        reply = lease(x_leased, 30000, "hm-p-owner")
        assert not reply["ok"] and reply["code"] == "unsupported" and "X11" in reply["error"], reply
        assert window_state(x_leased)["lease"] is None and not window_state(x_leased)["capabilities"]["lease"]
        assert hypr(f"hyprmasq eis close {x_leased}") == "ok"
        dispatch(f'hl.dsp.window.close({{ window = "address:{x_leased}" }})')
        # The focused window shares its connection with itself.
        open_socket(visible)
        reply = lease(visible, 30000, "hm-p-owner")
        assert not reply["ok"] and reply["code"] == "unsupported", reply
        assert hypr(f"hyprmasq eis close {visible}") == "ok"
        green = windows[1][1]
        for arguments, code in [
            (f"{leased} 0 hm-p-owner", "invalid_arguments"),
            (f"{leased} 600001 hm-p-owner", "invalid_arguments"),
            (f"{leased} soon hm-p-owner", "invalid_arguments"),
            (f"{leased} 5000", "invalid_arguments"),
            (f"{leased} 5000 hm-p-owner more", "invalid_arguments"),
            (f"{leased} release hm-p-owner more", "invalid_arguments"),
            (f"{leased}", "invalid_arguments"),
            ("0x1 5000 hm-p-owner", "window_gone"),
            ("0x1 release", "window_gone"),
            (f"{green} 5000 hm-p-owner", "no_socket"),
        ]:
            reply = json.loads(hypr(f"-j hyprmasq lease {arguments}"))
            assert not reply["ok"] and reply["error"] and reply["code"] == code, (arguments, reply)
        assert lease(green, "release") == {"ok": True, "released": False}
        reply = lease(leased, 600000, "hm-p-owner")
        assert reply["held"] and 599000 < reply["remainingMs"] <= 600000, reply
        assert lease(leased, "release")["released"]

    with subtest("a sender's disconnect releases every key, modifier and button it held"):
        pressed = {"key hm-p-lease 42 1", "modifiers hm-p-lease 1", "key hm-p-lease 30 1", "button hm-p-lease 1"}
        released = {"key hm-p-lease 30 0", "key hm-p-lease 42 0", "modifiers hm-p-lease 0", "button hm-p-lease 0"}

        def after_presses(start, expected=released):
            """The events after every press of focus-probe's hold mode, once they include expected."""
            def ready():
                events = probe_events("p-lease", start)
                names = [e for _, e in events]
                rest = events[max(names.index(e) for e in pressed) + 1 :] if pressed <= set(names) else []
                return rest if expected <= {e for _, e in rest} else None

            wait_for("releases after the presses", ready, timeout=5)
            return ready()

        def hold(mode):
            """Runs focus-probe's hold sender, and returns when it held, stopped and disconnected."""
            output = alice(f"hyprmasq-focus-probe hold {shlex.quote(lease_path)} 30 30 {mode}")
            return {event: int(t) for t, _, event in (line.partition(" ") for line in output.splitlines())}

        # hyprmasq-input releases what it pressed itself, so focus-probe's hold mode, which does not, stands in for a
        # sender that leaves input held. With a lease, a sender that stops emulating gets its keys, modifiers and button
        # released then, before it disconnects.
        assert lease(leased, 60000, "hm-p-owner")["held"]
        start = len(probe_events("p-lease"))
        times = hold("stop")
        events = after_presses(start)
        assert all(times["held"] <= t < times["disconnecting"] for t, e in events if e in released), (times, events)
        time.sleep(0.5)
        assert not any("leave" in e for _, e in probe_events("p-lease", start)), probe_events("p-lease", start)
        # One that exits without stopping gets them released once it is gone, the button before Shift.
        start = len(probe_events("p-lease"))
        times = hold("disconnect")
        events = after_presses(start)
        assert all(t >= times["disconnecting"] for t, e in events if e in released), (times, events)
        names = [e for _, e in events]
        assert names.index("button hm-p-lease 0") < names.index("key hm-p-lease 42 0"), names
        time.sleep(0.5)
        assert not any("leave" in e for _, e in probe_events("p-lease", start)), probe_events("p-lease", start)
        # Without a lease, the pointer lets go of the button and leaves, and the keyboard leaves, which releases the
        # keys and modifiers.
        assert lease(leased, "release")["released"]
        wait_for_leaves(start)
        start = len(probe_events("p-lease"))
        hold("disconnect")
        names = [e for _, e in after_presses(start, {"button hm-p-lease 0"} | lease_leaves)]
        assert names.index("button hm-p-lease 0") < names.index("pointer leave hm-p-lease"), names

        # A sender whose frames reach the plugin only after it has exited: answering them fails, which makes libeis close
        # the connection without a disconnect event. The plugin's ping a second later fails too, which libeis reports,
        # so what the frames pressed is released.
        hyprland_pid = machine.succeed(f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock").strip()
        start = len(probe_events("p-lease"))
        spawn(f"hyprmasq-focus-probe hold {shlex.quote(lease_path)} 30 30 signal", "p-signal")
        machine.wait_until_succeeds("grep -q ready /tmp/hyprmasq-p-signal.log", timeout=10)
        machine.succeed(f"kill -STOP {hyprland_pid}")
        try:
            machine.succeed("pkill -USR1 -f 'hyprmasq-focus-probe [h]old'")
            machine.wait_until_fails("pgrep -f 'hyprmasq-focus-probe [h]old'", timeout=10)
        finally:
            machine.succeed(f"kill -CONT {hyprland_pid}")
        names = [e for _, e in after_presses(start, {"button hm-p-lease 0"} | lease_leaves)]
        assert names.index("button hm-p-lease 0") < names.index("pointer leave hm-p-lease"), names

    with subtest("the virtual cursor stays while a lease keeps the pointer"):
        light_blue = "abs(r - 0x6E) <= 40 and abs(g - 0xC6) <= 40 and b >= 0xD8"
        hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor_timeout = 500 } } })"))
        # The two foots share the monitor. The real pointer and focus are on hm-p-right, the lease on hm-p-left.
        focus_workspace(37)
        (left_x, left_y), (right_x, right_y), (right_width, right_height) = client("hm-p-left")["at"], client("hm-p-right")["at"], client("hm-p-right")["size"]
        dispatch(f'hl.dsp.focus({{ window = "address:{lease_right}" }})')
        dispatch(f"hl.dsp.cursor.move({{ x = {right_x + right_width // 2}, y = {right_y + right_height // 2} }})")
        wait_for("right focus", lambda: snapshot()[1] == lease_right)
        left_path = open_socket(lease_left)
        assert lease(lease_left, 30000, "hm-p-owner")["held"]
        send(left_path, "move", 100, 100)
        # Without the lease, it would fade out 0.9 seconds after the motion.
        time.sleep(2)
        x, y = left_x + 100, left_y + 100
        assert count_pixels(capture_output("p-lease-cursor"), (x - 4, y - 4, x + 40, y + 40), light_blue) > 20
        assert lease(lease_left, "release")["released"]
        time.sleep(1.5)
        assert count_pixels(capture_output("p-lease-cursor-released"), (x - 30, y - 30, x + 50, y + 50), "b - r > 50") == 0
        hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor_timeout = 3000 } } })"))
        focus_workspace(4)
        for title in ["hm-p-lease", "hm-p-lease-sibling", "hm-p-left", "hm-p-right", "hm-p-shared-hidden", "hm-p-shared-focused"]:
            if client(title):
                dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
            wait_for(f"{title} closed", lambda: client(title) is None)
        wait_for("focus back", lambda: snapshot() == (4, visible))

    with subtest("launching applications on a hidden workspace"):
        focus_workspace(4)
        before_focus = snapshot()

        def launched(address):
            return next(c for c in clients() if c["address"] == address)

        # The title passes through the shell and a Lua string unchanged. The shell in foot changes the title later.
        title = "hm-launched \"quoted\" it's \\ back"
        address = alice(f"{ipc} hyprmasq-launch -- foot -T {shlex.quote(title)}").strip()
        window = launched(address)
        assert window["initialTitle"] == title and window["workspace"]["name"] == "hyprmasq", window
        assert snapshot() == before_focus
        # A window opened by a child process is found, and each of two launches at once gets its own window.
        alice(
            f"{ipc} hyprmasq-launch --workspace hm-launch -- sh -c 'sleep 0.5; foot -T hm-child; true' > /tmp/hm-launch-a & "
            f"{ipc} hyprmasq-launch --workspace hm-launch -- foot -T hm-direct > /tmp/hm-launch-b & wait"
        )
        child = launched(machine.succeed("cat /tmp/hm-launch-a").strip())
        direct = launched(machine.succeed("cat /tmp/hm-launch-b").strip())
        assert child["initialTitle"] == "hm-child" and direct["initialTitle"] == "hm-direct", (child, direct)
        assert child["workspace"]["name"] == direct["workspace"]["name"] == "hm-launch", (child, direct)
        assert snapshot() == before_focus
        screenshot(direct["address"], "launched")
        status, output = machine.execute(
            "su - alice -c " + shlex.quote(f"{ipc} hyprmasq-launch --timeout 2 -- true 2>&1")
        )
        assert status == 1 and "no window" in output, (status, output)

        # Children whose environment lacks Hyprland's variables, as a browser's that its launcher script starts without
        # exec and that overwrites its environment: the plugin finds them by their parent, the launched process, and
        # places them before they map, without the focus or the visible workspace changing.
        cleared = "env -u HL_EXEC_RULE_TOKEN -u HL_INITIAL_WORKSPACE_TOKEN"
        orphans = {}
        for title, command in [("hm-cleared", f"{cleared} foot -T hm-cleared"), ("hm-cleared-x", f"{cleared} xterm -T hm-cleared-x")]:
            found = alice(f"{ipc} hyprmasq-launch --workspace hm-launch -- sh -c {shlex.quote(command + '; true')}").strip()
            orphans[title] = launched(found)
            assert orphans[title]["initialTitle"] == title and orphans[title]["workspace"]["name"] == "hm-launch", orphans[title]
            assert snapshot() == before_focus
        assert orphans["hm-cleared-x"]["xwayland"], orphans
        # So is the second window of an app, each window with a rule of its own.
        first = alice(
            f"{ipc} hyprmasq-launch --workspace hm-launch -- sh -c "
            + shlex.quote(f"foot -T hm-first & sleep 1; {cleared} foot -T hm-second; wait")
        ).strip()
        def second_window():
            return next((c for c in clients() if c["initialTitle"] == "hm-second"), None)

        wait_for("hm-second", lambda: second_window() is not None)
        second = second_window()
        assert launched(first)["initialTitle"] == "hm-first" and second["workspace"]["name"] == "hm-launch", second
        assert snapshot() == before_focus
        # Words that hyprctl and Hyprland look for anywhere in a request reach the application unchanged, in hex: a
        # title of /instances, rollinglog -f and /--batch;kill; would otherwise list instances, keep the connection open
        # or kill a window. A workspace that starts with - is no option of hyprctl's, and a request that fills
        # Hyprland's 1023-byte piece exactly gets padded: "/hyprmasq launch 2d64617368 <16 hex> " is 45 bytes, and the
        # line "exec 'foot' '-T' '<title>'" in hex twice its length, 38 bytes and twice the title's.
        started = time.monotonic()
        tricky = "/instances rollinglog -f /--batch;kill;"
        title = tricky + "x" * (470 - len(tricky))
        assert 45 + 38 + 2 * len(title) == 1023
        edge = launched(alice(f"{ipc} hyprmasq-launch --workspace -dash -- foot -T {shlex.quote(title)}").strip())
        assert edge["initialTitle"] == title and edge["workspace"]["name"] == "-dash" and time.monotonic() - started < 4, edge
        dispatch('hl.dsp.window.close({ window = "address:' + edge["address"] + '" })')
        tokens = {w["address"]: w["token"] for w in json.loads(hypr("-j hyprmasq launched"))["windows"]}
        assert tokens[first] == tokens[second["address"]] and tokens[first] != tokens[orphans["hm-cleared"]["address"]], tokens
        for window in [address, child["address"], direct["address"], first, second["address"], *(o["address"] for o in orphans.values())]:
            dispatch('hl.dsp.window.close({ window = "address:' + window + '" })')
        wait_for("no launched windows", lambda: hypr("hyprmasq launched") == "no launched windows")

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

    with subtest("a window that emulated input opens opens silently next to the driven window"):
        # The foot server opens the windows of footclients that the hidden shell starts: windows of the driven window's
        # process, as a browser's popups are, in an app that no launch started.
        # With bell, the window rings its bell that many seconds after it starts, which foot answers with an
        # xdg-activation request when it has no keyboard focus, and the test's focus_on_activate focuses it.
        machine.succeed("cat > /tmp/hm-bell.sh <<'EOF'\nsleep \"$1\"\nprintf '\\a'\nexec sleep 600\nEOF")

        def open_from_hidden(title, delay=0, bell=None):
            program = f"sh /tmp/hm-bell.sh {bell}" if bell else "sleep 600"
            command = f"sleep {delay}; footclient -s /tmp/hyprmasq-foot.sock -o bell.urgent=yes -T {title} {program}"
            send(path, "type", f"({command}) &", "key", 28)
            wait_for(title, lambda: client(title) is not None, timeout=delay + 30)
            return client(title)

        hidden_workspace = client("hm-shared-hidden")["workspace"]["id"]
        focus_workspace(60)
        other = foot("hm-opener-other")
        wait_for("other app focused", lambda: snapshot() == (60, other))
        # With the user's focus in another app, the window opens on the driven window's workspace, and its activation
        # request later marks it urgent without taking the focus.
        opened = open_from_hidden("hm-opened-other-app", bell=2)
        assert opened["workspace"]["id"] == hidden_workspace, opened
        assert snapshot() == (60, other)
        time.sleep(4)
        assert snapshot() == (60, other)
        # Placed windows of an app that no launch started are not listed as launched.
        assert hypr("hyprmasq launched") == "no launched windows"
        # On the workspace the user is on, where Hyprland drops a workspace rule with its silent, the focus stays too.
        focus_workspace(hidden_workspace)
        beside = foot("hm-opener-beside")
        wait_for("app beside focused", lambda: snapshot() == (hidden_workspace, beside))
        opened_beside = open_from_hidden("hm-opened-beside")
        assert opened_beside["workspace"]["id"] == hidden_workspace, opened_beside
        assert snapshot() == (hidden_workspace, beside)
        focus_workspace(60)
        wait_for("other app focused", lambda: snapshot() == (60, other))
        # Input more than 5 seconds before the window opens did not open it: it opens as usual, with the focus.
        late = open_from_hidden("hm-opened-late", delay=6, bell=3)
        assert late["workspace"]["id"] == 60, late
        wait_for("late window focused", lambda: snapshot() == (60, late["address"]))
        # Its activation request takes the focus back, as that of a window the plugin did not place does.
        dispatch(f'hl.dsp.focus({{ window = "address:{other}" }})')
        wait_for("other app focused", lambda: snapshot() == (60, other))
        wait_for("late window activated", lambda: snapshot() == (60, late["address"]), timeout=10)
        # With the user's focus in the same app and no key of theirs since the input, the input opened it.
        focus_workspace(8)
        wait_for("shared visible focus", lambda: snapshot() == (8, shared_visible))
        opened_same = open_from_hidden("hm-opened-same-app")
        assert opened_same["workspace"]["id"] == hidden_workspace, opened_same
        assert snapshot() == (8, shared_visible)
        # A real key after the input means the user's own input opened it, also in the same app.
        send(path, "type", "(sleep 2; footclient -s /tmp/hyprmasq-foot.sock -T hm-opened-user sleep 600) &", "key", 28)
        machine.send_key("shift")
        wait_for("hm-opened-user", lambda: client("hm-opened-user") is not None)
        user = client("hm-opened-user")
        assert user["workspace"]["id"] == 8, user
        wait_for("user window focused", lambda: snapshot() == (8, user["address"]))
        for title in [
            "hm-opened-other-app", "hm-opened-beside", "hm-opened-late", "hm-opened-same-app", "hm-opened-user", "hm-opener-other", "hm-opener-beside"
        ]:
            dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
        wait_for("opened windows closed", lambda: not any(c["title"].startswith(("hm-opened-", "hm-opener-")) for c in clients()))
        dispatch(f'hl.dsp.focus({{ window = "address:{shared_visible}" }})')
        wait_for("shared visible focus", lambda: snapshot() == (8, shared_visible))
        machine.succeed("test ! -s /tmp/hyprmasq-visible-input")

    with subtest("a click keeps its modifiers only where the window has hold"):
        # Input to a window of the connection that has the real keyboard focus hands that focus back, with the user's
        # modifiers, after every keyboard frame. A click then arrives without the modifiers held around it, which the
        # window's missing hold capability tells.
        focus_workspace(23)
        spawn("hyprmasq-focus-probe focus hm-p-mods-focused hm-p-mods", "p-mods")
        wait_for("modifier probe", lambda: client("hm-p-mods-focused") and client("hm-p-mods"))
        dispatch('hl.dsp.window.move({ window = "title:^hm-p-mods$", workspace = "24", follow = false })')
        mods_focused = client("hm-p-mods-focused")["address"]
        dispatch(f'hl.dsp.focus({{ window = "address:{mods_focused}" }})')
        wait_for("probe focus", lambda: snapshot() == (23, mods_focused))
        mods = client("hm-p-mods")["address"]
        mods_path = open_socket(mods)

        def modifiers_at_press(offset):
            """The modifiers that the probe's connection had last been sent when the button went down."""
            machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-mods.log | grep -q 'button hm-p-mods 0'", timeout=10)
            events = [line.split(" ", 1)[1] for line in log_since("p-mods", offset).splitlines() if line[:1].isdigit()]
            press = events.index("button hm-p-mods 1")
            return events, int(next(e for e in reversed(events[:press]) if e.startswith("modifiers ")).split()[-1])

        assert not window_state(mods)["capabilities"]["hold"]
        offset = log_size("p-mods")
        send(mods_path, "move", 30, 30, "click", "left", 1, "ctrl")
        events, modifiers = modifiers_at_press(offset)
        assert modifiers == 0 and "modifiers hm-p-mods 4" in events, events
        assert snapshot() == (23, mods_focused)
        # With the real focus in another app, the window has hold, and the click keeps Ctrl (mask 4).
        focus_workspace(4)
        assert window_state(mods)["capabilities"]["hold"]
        offset = log_size("p-mods")
        send(mods_path, "move", 30, 30, "click", "left", 1, "ctrl")
        events, modifiers = modifiers_at_press(offset)
        assert modifiers == 4, events
        for title in ["hm-p-mods", "hm-p-mods-focused"]:
            dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
        wait_for("modifier probe closed", lambda: client("hm-p-mods") is None and client("hm-p-mods-focused") is None)

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
        # A tiling resize while it is hidden: the wait gives XWayland frame callbacks, so the window catches up with
        # the resize, and is no longer resizing once the wait has ended.
        open_socket(x_red)
        focus_workspace(11)
        x_neighbour = xterm("hm-x-neighbour", "sleep 900")
        wait_for("the X11 window halved", lambda: client("hm-x-red")["size"] == [320, 480])
        focus_workspace(4)
        dispatch(f'hl.dsp.window.close({{ window = "address:{x_neighbour}" }})')
        wait_for("the X11 neighbour closed", lambda: client("hm-x-neighbour") is None)
        reply = json.loads(hypr(f"-j hyprmasq wait {x_red} since {window_state(x_red)['commits']} quiet 200 deadline 4000"))
        assert reply["ended"] == "quiet" and not reply["resizing"], reply
        state = window_state(x_red)
        assert state["size"] == {"width": 640, "height": 480} and not state["resizing"], state
        assert_png(screenshot(x_red, "x-red-resized"), (255, 0, 0))
        assert hypr(f"hyprmasq eis close {x_red}") == "ok"

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
        assert eis_entry(x_shell)["path"] == x_shell_path and eis_entry(x_shell)["force"]
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
        pointing = capture_output("cursor")
        assert count_pixels(pointing, cursor_box, light_blue) > 20
        # The ripple rings the click point, also on the side away from the arrow, where the arrow itself draws nothing.
        ripple_box = (x - 24, y - 3, x - 6, y + 3)
        assert count_pixels(pointing, ripple_box, bluish) == 0
        # The ripple lasts 450 ms. It counts in ripple_box from about 35 ms after the press, once the ring has grown
        # into it, to about 300 ms, while it is still bright enough. grim starts 50 ms after the click, and a capture
        # that has ended within 250 ms of the click's start lies in that time, so it has to show the ripple. A busy
        # host can make grim take longer, and a later capture without the ripple proves nothing: the click is then
        # repeated once its ripple is gone, so that each capture can only show the ripple of its own click.
        click_path = "/tmp/hyprmasq-cursor-click.png"
        for attempt in range(1, 9):
            elapsed = int(alice(
                f"start=$(date +%s%N); hyprmasq-input --no-pace --socket {shlex.quote(pointed_path)} click && sleep 0.05 && "
                f"{client_env} grim {click_path} && echo $(( ($(date +%s%N) - start) / 1000000 ))"
            ).strip())
            machine.copy_from_machine(click_path)
            if count_pixels(click_path, ripple_box, bluish) > 0:
                break
            assert elapsed > 250, f"no click ripple in a capture that ended {elapsed} ms after the click started"
            machine.log(f"no click ripple in the capture after click {attempt}, which ended {elapsed} ms after it started")
            time.sleep(0.5)
        else:
            raise AssertionError("eight captures after a click all ended too late to show its ripple")
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

    # hyprmasq-mcp end to end, on workspaces 50 to 59. A bridge runs each server in the VM as alice with Hyprland's
    # environment and passes JSON-RPC messages between its stdio and the test, which reaches the bridge through a Unix
    # socket. It stamps them with CLOCK_MONOTONIC, as focus-probe stamps its events, and saves the images of results.
    mcp_bridge, mcp_client = "/tmp/hm-m-bridge.py", "/tmp/hm-m-client.py"
    machine.succeed(f"cat > {mcp_bridge} <<'PY'\n" + r"""
import base64, json, os, socket, subprocess, sys, threading, time

name = sys.argv[1]
path = f"/tmp/hm-m-{name}.sock"
os.makedirs("/tmp/hm-m-images", exist_ok=True)
server = subprocess.Popen(["hyprmasq-mcp", *sys.argv[2:]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=open(f"/tmp/hyprmasq-m-{name}-server.log", "wb"))
changed = threading.Condition()
responses, notifications, sent = {}, [], {}
session = {"last": 0, "closed": False}


def read():
    for line in server.stdout:
        message = json.loads(line)
        message["received"] = time.monotonic()
        with changed:
            if "method" in message:
                notifications.append(message)
            else:
                for i, item in enumerate((message.get("result") or {}).get("content") or []):
                    if item.get("type") == "image":
                        item["path"] = f"/tmp/hm-m-images/{name}-{message['id']}-{i}.png"
                        with open(item["path"], "wb") as image:
                            image.write(base64.b64decode(item.pop("data")))
                message["sent"] = sent.get(message.get("id"))
                responses[message.get("id")] = message
            changed.notify_all()
    with changed:
        session["closed"] = True
        changed.notify_all()


def write(message):
    server.stdin.write(json.dumps(message).encode() + b"\n")
    server.stdin.flush()


def response(id, timeout):
    with changed:
        changed.wait_for(lambda: id in responses or session["closed"], timeout)
        return responses.get(id, {"missing": id, "closed": session["closed"]})


def handle(command):
    op, now = command["op"], time.monotonic()
    if op == "request":
        with changed:
            session["last"] += 1
            id = session["last"]
            sent[id] = now
            write({"jsonrpc": "2.0", "id": id, "method": command["method"], "params": command["params"]})
        return response(id, command["timeout"]) if command["wait"] else {"id": id, "sent": now}
    if op == "response":
        return response(command["id"], command["timeout"])
    if op == "notify":
        with changed:
            write({"jsonrpc": "2.0", "method": command["method"], "params": command["params"]})
        return {"sent": now}
    if op == "notifications":
        with changed:
            return {"notifications": notifications}
    if op == "pid":
        return {"pid": server.pid}
    if op == "stop":
        server.stdin.close()
        try:
            return {"status": server.wait(command["timeout"]), "stopped": time.monotonic()}
        except subprocess.TimeoutExpired:
            return {"status": None}
    raise ValueError(op)


def serve(connection):
    with connection, connection.makefile("rwb") as stream:
        command = json.loads(stream.readline())
        stream.write(json.dumps(handle(command)).encode() + b"\n")
        stream.flush()
    if command["op"] == "stop":
        os.unlink(path)
        os._exit(0)


threading.Thread(target=read, daemon=True).start()
listener = socket.socket(socket.AF_UNIX)
listener.bind(path)
listener.listen()
while True:
    threading.Thread(target=serve, args=(listener.accept()[0],), daemon=True).start()
""" + "PY")
    machine.succeed(f"cat > {mcp_client} <<'PY'\n" + r"""
import socket, sys

connection = socket.socket(socket.AF_UNIX)
connection.connect(f"/tmp/hm-m-{sys.argv[1]}.sock")
connection.sendall(sys.argv[2].encode() + b"\n")
reply = b""
while chunk := connection.recv(65536):
    reply += chunk
sys.stdout.write(reply.decode())
""" + "PY")

    class ToolResult:
        """A tools/call result: its text, structured content and saved images, and when it was sent and answered."""

        def __init__(self, reply):
            assert "result" in reply, reply
            self.reply = reply
            content = reply["result"]["content"]
            self.text = content[0]["text"]
            self.data = reply["result"].get("structuredContent")
            self.images = [c["path"] for c in content if c["type"] == "image"]
            self.is_error = reply["result"].get("isError", False)
            self.sent, self.received = reply["sent"], reply["received"]
            # Some clients pass the model only the structured content, so it carries all of the text: the error, the
            # summary and the hint. The windows tool lists every field of its lines.
            if self.data is not None and "windows" not in self.data:
                error = self.data.get("error")
                lines = [f"error {error['code']}: {error['message']}"] if error else []
                lines += [self.data[key] for key in ("summary", "hint") if self.data.get(key)]
                assert self.text == "\n".join(lines), reply

        def __repr__(self):
            return json.dumps(self.reply, ensure_ascii=False)

    class Mcp:
        """A hyprmasq-mcp server under the bridge, started with options and with the variables of env."""

        def __init__(self, name, *options, env=""):
            self.name = name
            alice(f"{ipc} {env} nohup python3 {mcp_bridge} {name} {' '.join(options)} > /tmp/hyprmasq-m-{name}-bridge.log 2>&1 &")
            machine.wait_until_succeeds(f"test -S /tmp/hm-m-{name}.sock", timeout=30)

        def command(self, **command):
            return json.loads(machine.succeed(f"python3 {mcp_client} {self.name} {shlex.quote(json.dumps(command))}"))

        def request(self, method, params=None):
            reply = self.command(op="request", method=method, params=params or {}, wait=True, timeout=60)
            assert "result" in reply, reply
            return reply["result"]

        def notify(self, method, params=None):
            """Sends a notification, and returns when."""
            return self.command(op="notify", method=method, params=params or {})["sent"]

        def initialize(self, client="hyprmasq-vm-test"):
            result = self.request(
                "initialize",
                {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": client, "version": "1"}},
            )
            self.notify("notifications/initialized")
            return result

        def start(self, tool, arguments, token=None):
            """Calls a tool without waiting for the result, and returns the request's id."""
            params = {"name": tool, "arguments": arguments}
            if token:
                params["_meta"] = {"progressToken": token}
            return self.command(op="request", method="tools/call", params=params, wait=False, timeout=0)["id"]

        def result(self, id, timeout=60):
            return ToolResult(self.command(op="response", id=id, timeout=timeout))

        def call(self, tool, arguments=None):
            params = {"name": tool, "arguments": arguments or {}}
            return ToolResult(self.command(op="request", method="tools/call", params=params, wait=True, timeout=60))

        def progress(self, token):
            notifications = self.command(op="notifications")["notifications"]
            return [n["params"] for n in notifications if n["method"] == "notifications/progress" and n["params"]["progressToken"] == token]

        def stop(self):
            """Closes the server's stdin, which ends the session, and returns its exit status."""
            return self.command(op="stop", timeout=10)["status"]

    def keep(result, name):
        """Copies the image of a tool result into the test's output as hyprmasq-m-NAME.png."""
        path = f"/tmp/hyprmasq-m-{name}.png"
        machine.succeed(f"cp {result.images[0]} {path}")
        machine.copy_from_machine(path)
        return path

    def listed(server, address):
        """The windows entry of a window, and the line that describes it."""
        listing = server.call("windows")
        entry = next(w for w in listing.data["windows"] if w["window"] == address)
        return entry, next(line for line in listing.text.splitlines() if line.startswith(address + " "))

    def failure(result, code):
        """Checks that the call failed with code, as a tool error only when its result has no shot."""
        assert result.data["error"]["code"] == code and result.is_error == ("shot" not in result.data), result

    def refused(result, code):
        """Checks that the call failed with code and sent nothing."""
        failure(result, code)
        assert result.data["actions"]["done"] == 0 and result.data["actions"]["delivery"] == "none", result
        assert result.data["hint"].endswith("Nothing was sent."), result

    def white_run(path):
        """The longest run of white pixels in a row of the image."""
        script = f"""
from PIL import Image
im = Image.open({path!r}).convert('RGB')
pixels, longest = im.load(), 0
for y in range(im.height):
    run = 0
    for x in range(im.width):
        run = run + 1 if min(pixels[x, y]) > 200 else 0
        longest = max(longest, run)
print(longest)
"""
        return int(machine.succeed("python3 - <<'PY'\n" + script + "\nPY").strip())

    def probe_input(start):
        """The input events that hm-m-probe got since entry start, without focus events."""
        return [(t, e) for t, e in probe_events("m-probe", start) if e.startswith(("motion ", "button ", "key "))]

    def probe_motions(start):
        return [tuple(float(v) for v in e.split()[2:]) for _, e in probe_input(start) if e.startswith("motion hm-m-probe ")]

    def probe_leaves(start):
        return {e for _, e in probe_events("m-probe", start) if " leave " in e}

    with subtest("hyprmasq-mcp introduces itself and its tools"):
        focus_workspace(4)
        wait_for("focus back", lambda: snapshot() == (4, visible))
        mcp = Mcp("a")
        init = mcp.initialize()
        assert init["protocolVersion"] == "2025-11-25" and "tools" in init["capabilities"], init
        assert (init["serverInfo"]["name"], init["serverInfo"]["version"]) == ("hyprmasq-mcp", "0.3.0"), init
        # The first line sums it all up, for clients that show only that.
        summary, blank = init["instructions"].splitlines()[:2]
        assert summary.startswith("hyprmasq: ") and summary.endswith(".") and blank == "", init["instructions"]
        assert all(word in summary for word in ["click", "type", "act", "screenshot"]), summary
        tools = {tool["name"]: tool for tool in mcp.request("tools/list")["tools"]}
        assert sorted(tools) == ["act", "close", "launch", "release", "screenshot", "windows"], tools
        for name, tool in tools.items():
            assert tool["description"] and tool["inputSchema"]["type"] == "object", tool
            assert tool.get("annotations", {}).get("readOnlyHint", False) == (name == "windows"), tool
        act_schema = tools["act"]["inputSchema"]
        assert act_schema["required"] == ["window"], act_schema
        assert set(act_schema["properties"]) == {"window", "shot", "actions", "wait", "capture", "deadline_ms"}, act_schema
        assert tools["screenshot"]["inputSchema"]["required"] == ["window"], tools["screenshot"]
        assert tools["launch"]["inputSchema"]["required"] == ["command"], tools["launch"]

    with subtest("hyprmasq-mcp launches a window on a hidden workspace"):
        before_focus = snapshot()
        launch_command = ["foot", "-T", "hm-m-shell", "-o", "colors-dark.foreground=ffffff", "bash", "--noprofile", "--norc", "-i"]
        launch_result = mcp.call("launch", {"command": launch_command, "workspace": "hm-m"})
        assert not launch_result.is_error, launch_result
        shell, shell_pid = launch_result.data["window"]["window"], launch_result.data["window"]["pid"]
        launch_shot = launch_result.data["shot"]
        assert launch_result.data["window"] == {
            "window": shell, "stable_id": client("hm-m-shell")["stableId"], "class": "foot", "title": "hm-m-shell", "pid": shell_pid, "workspace": "hm-m",
        }, launch_result
        assert launch_result.data["wait"]["ended"] == "quiet" and launch_result.data["new_windows"] == [], launch_result
        assert (launch_shot["width"], launch_shot["height"], launch_shot["frame"]) == (640, 480, region(0, 0, 640, 480)), launch_result
        assert re.fullmatch(
            rf'launched {shell} foot "hm-m-shell" · pid {shell_pid} · workspace hm-m · settled after \d+ ms · shot {launch_shot["id"]} 640×480', launch_result.text
        ), launch_result
        assert len(launch_result.images) == 1 and pixels(launch_result.images[0])[-1] == (640, 480), launch_result
        keep(launch_result, "launched")
        assert client("hm-m-shell")["workspace"]["name"] == "hm-m" and snapshot() == before_focus
        entry, line = listed(mcp, shell)
        assert entry == {
            "window": shell, "stable_id": launch_result.data["window"]["stable_id"], "class": "foot", "title": "hm-m-shell", "pid": shell_pid,
            "workspace": "hm-m", "visible": False, "size": {"width": 640, "height": 480}, "resizing": False, "xwayland": False, "launched": True, "lease": None,
            "capabilities": {"lease": True, "hold": True, "keyboard": "yes"},
        }, entry
        # The line leaves out the pid and the capabilities, which the window has all of.
        assert line == f'{shell} · foot "hm-m-shell" · workspace hm-m (hidden) · 640×480 · launched', line
        entry, _ = listed(mcp, visible)
        # The active window shares the user's keyboard focus, so act cannot drag in it, and no dnd is listed.
        assert entry["visible"] and not entry["launched"] and entry["capabilities"] == {"lease": False, "hold": False, "keyboard": "yes"}, entry

    with subtest("hyprmasq-mcp gives Claude Code the text in place of structured content"):
        # Claude Code passes the model a result's structured content in place of its text, so it gets none.
        claude = Mcp("claude")
        claude.initialize(client="claude-code")
        # The shell was launched by the other server, so this one does not list it as launched.
        matched = claude.call("windows", {"match": "HM-M-SHELL"})
        assert matched.data is None and matched.text == f'{shell} · foot "hm-m-shell" · workspace hm-m (hidden) · 640×480', matched
        none = claude.call("windows", {"match": "hm-m-nothing"})
        assert none.data is None and re.fullmatch(r'no window has "hm-m-nothing" in its class or title, of \d+ windows', none.text), none
        shot = claude.call("screenshot", {"window": shell})
        assert shot.data is None and len(shot.images) == 1 and re.fullmatch(rf"shot s\d+ 640×480 · window {shell} 640×480", shot.text), shot
        failed = claude.call("act", {"window": shell, "deadline_ms": 1})
        assert failed.is_error and failed.data is None and failed.text.startswith("error invalid_arguments: deadline_ms must be 1000 to 30000"), failed
        assert claude.stop() == 0
        # Other clients get it, and match there too. An empty list says why in it as well.
        matched = mcp.call("windows", {"match": "hm-m-shell"})
        assert [w["window"] for w in matched.data["windows"]] == [shell] and matched.text == line, matched
        none = mcp.call("windows", {"match": "hm-m-nothing"})
        assert none.data == {"windows": [], "summary": none.text}, none

    with subtest("hyprmasq-mcp types into a hidden shell, waits for its redraw and keeps a lease"):
        machine.succeed("rm -f /tmp/hm-m-ran")
        # The command prints a bar of 50 white cells.
        line = "printf '\\033[7m%50s\\033[0m\\n' ''; echo ran > /tmp/hm-m-ran"
        typed_result = mcp.call("act", {"window": shell, "actions": [{"action": "type", "text": line}, {"action": "key", "keys": ["enter"]}]})
        assert not typed_result.is_error and typed_result.data["actions"] == {"done": 2, "total": 2, "delivery": "complete"}, typed_result
        wait = typed_result.data["wait"]
        assert wait["ended"] == "quiet" and wait["summary"] == "settled" and wait["redraw_observed"] and wait["commits"] > 0, typed_result
        assert wait["quiet_ms"] >= 200 and typed_result.data["lease"]["held"] and typed_result.data["lease"]["remaining_s"] in (59, 60), typed_result
        # It shows what changed since the launch's shot, the session's last of the window: a zoom into the lines of the
        # command and its output.
        changed = typed_result.data["shot"]
        assert changed["changes"]["base"] == launch_shot["id"] and len(changed["changes"]["regions"]) == 1, typed_result
        zoom = changed["changes"]["regions"][0]
        assert re.fullmatch(
            rf"2/2 delivered · redraw observed · quiet \d+ ms · \d+ ms · shot {changed['id']} = {launch_shot['id']} \+ 1 change · lease (59|60) s\n"
            rf"zoom {zoom['shot']} {zoom['image_width']}×{zoom['image_height']} of {changed['id']} at {zoom['x']},{zoom['y']} {zoom['width']}×{zoom['height']}",
            typed_result.text,
        ), typed_result
        assert typed_result.data["new_windows"] == [] and len(typed_result.images) == 1, typed_result
        assert machine.succeed("cat /tmp/hm-m-ran") == "ran\n"
        assert white_run(keep(typed_result, "typed")) >= 200 > white_run(launch_result.images[0])
        assert window_state(shell)["lease"]["owner"].startswith("mcp-")
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp clicks at the shot pixel, also at scale 2 and in a zoom shot"):
        focus_workspace(50)
        spawn("hyprmasq-focus-probe focus hm-m-probe", "m-probe")
        wait_for("probe", lambda: client("hm-m-probe") is not None)
        focus_workspace(4)
        probe = client("hm-m-probe")["address"]
        time.sleep(0.5)
        # An address in upper case and with leading zeros names the same window, which the result names as Hyprland does.
        overview = mcp.call("screenshot", {"window": "0x00" + probe[2:].upper()})
        overview_id = overview.data["shot"]["id"]
        assert overview.data["shot"] == {"id": overview_id, "width": 640, "height": 480, "frame": region(0, 0, 640, 480)}, overview
        assert overview.text == f"shot {overview_id} 640×480 · window {probe} 640×480" and len(overview.images) == 1, overview
        # The probe draws nothing in answer to input, so the wait finds no redraw.
        start = len(probe_events("m-probe"))
        clicked = mcp.call("act", {"window": probe, "shot": overview_id, "actions": [{"action": "click", "x": 123, "y": 77}]})
        assert not clicked.is_error and clicked.data["wait"]["ended"] == "no_redraw" and clicked.data["wait"]["summary"] == "no_change", clicked
        # Nothing changed since the shot, so there is no image, and the hint points at the shot the window still shows.
        assert re.fullmatch(
            rf"1/1 delivered · no redraw within 1000 ms · shot s\d+ = {overview_id}, no change · lease (59|60) s\n"
            rf"The click may have missed or had no visible effect\. Look at {overview_id} before repeating it\.",
            clicked.text,
        ) and not clicked.images, clicked
        assert probe_motions(start) == [(123, 77)], probe_events("m-probe", start)
        assert [e for _, e in probe_input(start)][1:] == ["button hm-m-probe 1", "button hm-m-probe 0"], probe_events("m-probe", start)

        # At scale 2, a shot pixel is half a logical pixel.
        monitor_scale(2, 320)
        wait_for("probe at scale 2", lambda: client("hm-m-probe")["size"] == [320, 240])
        stale = mcp.call("act", {"window": probe, "shot": overview_id, "actions": [{"action": "click", "x": 123, "y": 77}]})
        refused(stale, "stale_shot")
        assert stale.data["error"]["message"] == f"the window was resized from 640×480 to 320×240 since shot {overview_id}", stale
        doubled = mcp.call("screenshot", {"window": probe})
        doubled_id = doubled.data["shot"]["id"]
        assert doubled.data["shot"] == {"id": doubled_id, "width": 640, "height": 480, "frame": region(0, 0, 320, 240)}, doubled
        start = len(probe_events("m-probe"))
        clicked = mcp.call("act", {"window": probe, "shot": doubled_id, "actions": [{"action": "click", "x": 201, "y": 151}], "wait": {"expect_change": False}})
        assert not clicked.is_error and clicked.data["actions"]["delivery"] == "complete", clicked
        assert probe_motions(start) == [(100.5, 75.5)], probe_events("m-probe", start)
        # A zoom shot is a region of an earlier shot, captured at the monitor's scale, with pixels of its own.
        zoom = mcp.call("screenshot", {"window": probe, "region": {"shot": doubled_id, "x": 200, "y": 140, "width": 100, "height": 60}})
        zoom_id = zoom.data["shot"]["id"]
        assert zoom.data["shot"] == {"id": zoom_id, "width": 100, "height": 60, "frame": region(100, 70, 50, 30)}, zoom
        assert zoom.text == f"shot {zoom_id} 100×60 · zoom of {doubled_id} at 200,140 100×60 · window {probe} 320×240", zoom
        assert pixels(keep(zoom, "zoom"))[-1] == (100, 60) and near(pixels(zoom.images[0], (50, 30))[0], window_rgb)
        start = len(probe_events("m-probe"))
        clicked = mcp.call("act", {"window": probe, "shot": zoom_id, "actions": [{"action": "click", "x": 31, "y": 17}], "wait": {"expect_change": False}})
        assert not clicked.is_error and clicked.data["shot"]["width"] == 640, clicked
        assert probe_motions(start) == [(115.5, 78.5)], probe_events("m-probe", start)
        monitor_scale(1, 640)
        wait_for("probe at scale 1", lambda: client("hm-m-probe")["size"] == [640, 480])
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp sends nothing for a batch it refuses"):
        # A resize makes the shot stale. The probe lets go of the lease's focus first, so that it gets no events at all.
        old = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        assert not mcp.call("release", {"window": probe}).is_error
        wait_for("lease released", lambda: window_state(probe)["lease"] is None, timeout=5)
        time.sleep(0.5)
        float_window(probe, 100, 100, 300, 200)
        time.sleep(0.5)
        start = len(probe_events("m-probe"))
        stale = mcp.call("act", {"window": probe, "shot": old, "actions": [{"action": "click", "x": 10, "y": 10}]})
        refused(stale, "stale_shot")
        fresh = stale.data["shot"]
        assert stale.data["error"]["message"] == f"the window was resized from 640×480 to 300×200 since shot {old}", stale
        assert stale.data["hint"] == "Read the coordinates from the new shot in this result. Nothing was sent.", stale
        assert (fresh["width"], fresh["height"]) == (300, 200) and pixels(stale.images[0])[-1] == (300, 200), stale
        assert stale.text.splitlines()[1] == f"0/1 delivered, nothing sent · shot {fresh['id']} 300×200", stale

        # Coordinates outside the shot, also the end of a drag after a key.
        outside = mcp.call("act", {"window": probe, "shot": fresh["id"], "actions": [{"action": "click", "x": 300, "y": 50}]})
        refused(outside, "out_of_bounds")
        assert outside.data["error"]["message"] == f"actions[0] (click): 300,50 lies outside shot {fresh['id']} (300×200)", outside
        assert outside.data["hint"] == f"Coordinates are pixels of shot {fresh['id']}, from 0,0 to 299,199. Nothing was sent.", outside
        actions = [{"action": "key", "keys": ["a"]}, {"action": "drag", "from": [10, 10], "to": [10, 250]}]
        outside = mcp.call("act", {"window": probe, "shot": fresh["id"], "actions": actions})
        refused(outside, "out_of_bounds")
        assert outside.data["error"]["message"] == f"actions[1] (drag): to 10,250 lies outside shot {fresh['id']} (300×200)", outside

        # Arguments are checked by name.
        for bad, message in [
            ({"actions": [{"action": "click", "x": 10, "y": 10, "count": 5}]}, "actions[0].count must be 1 to 3, not 5"),
            ({"actions": [{"action": "key", "keys": ["a"]}, {"action": "type", "text": "x", "speed": 2}]}, "actions[1].speed is not a field of type"),
            ({"wiat": {"quiet_ms": 100}}, 'unknown argument "wiat"'),
            ({"wait": {"quiet_ms": 0}}, "wait.quiet_ms must be 1 to 10000, not 0"),
            ({"actions": [{"action": "move", "x": 10, "y": 10}]}, "shot is missing: actions[0] (move) has coordinates"),
            ({"shot": fresh["id"], "actions": [{"action": "click", "x": None, "y": 10}]}, "actions[0].x must be a number, not null"),
            ({"shot": fresh["id"], "actions": [{"action": "drag", "from": [10, None], "to": [20, 20]}]}, "actions[0].from[1] must be a number, not null"),
        ]:
            result = mcp.call("act", {"window": probe, **bad})
            refused(result, "invalid_arguments")
            assert message in result.data["error"]["message"] and not result.images, (bad, result)
        time.sleep(0.5)
        assert probe_input(start) == [], probe_events("m-probe", start)

        # The part of a popup above and left of the window is in the shot, but pointer input cannot reach it.
        popleft = popup_probe("hm-m-popleft", 51, -40, -30, 60, 50)
        popped = mcp.call("screenshot", {"window": popleft})
        popped_id = popped.data["shot"]["id"]
        assert popped.data["shot"] == {"id": popped_id, "width": 340, "height": 230, "frame": region(-40, -30, 340, 230)}, popped
        assert near(pixels(keep(popped, "popleft"), (5, 5))[0], popup_rgb)
        offset = log_size("m-popleft")
        unreachable = mcp.call("act", {"window": popleft, "shot": popped_id, "actions": [{"action": "click", "x": 5, "y": 5}]})
        refused(unreachable, "out_of_bounds")
        assert unreachable.data["error"]["message"] == (
            f"actions[0] (click): 5,5 of shot {popped_id} lies left of or above the window, where pointer input cannot reach"
        ), unreachable
        assert unreachable.data["hint"].startswith("Pointer input reaches the window and the area right of and below it."), unreachable
        # Its part over the window takes the click.
        clicked = mcp.call("act", {"window": popleft, "shot": popped_id, "actions": [{"action": "click", "x": 50, "y": 40}], "wait": {"expect_change": False}})
        assert not clicked.is_error, clicked
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popleft.log | grep -q 'button popup 0'", timeout=10)
        assert [line for line in log_since("m-popleft", offset).splitlines() if line.startswith("motion")] == ["motion popup 50 40"]

        # A character that the keymap cannot type.
        snowman = mcp.call("act", {"window": shell, "actions": [{"action": "type", "text": "echo ☃ > /tmp/hm-m-snowman"}, {"action": "key", "keys": ["enter"]}]})
        refused(snowman, "cannot_type")
        assert snowman.data["error"]["message"].startswith("actions[0] (type): ") and "U+2603" in snowman.data["error"]["message"], snowman
        assert snowman.data["hint"] == "Leave the character out, or ask the user how to enter it. Nothing was sent.", snowman
        # On a German layout ^ is a dead key and Space, so 40 of them are 80 keys. The server counts a key for each
        # character, which deadline_ms 3200 fits, but hyprmasq-input knows the keys and refuses the batch before it
        # sends any.
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
        wait_for("German keymap", lambda: keymap("German"))
        carets = mcp.call("act", {"window": shell, "actions": [{"action": "type", "text": "^" * 40}], "deadline_ms": 3200})
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
        wait_for("US keymap", lambda: keymap("English"))
        refused(carets, "invalid_arguments")
        assert carets.data["error"]["message"] == (
            "the batch takes at least 1190 ms to send with the active keymap, about 1309 ms with round trips; deadline_ms 3200 leaves 950 ms for it, "
            "since its last 2250 ms are kept for stopping input in time and for the capture"
        ), carets
        assert carets.data["hint"] == "Give a deadline_ms of at least 5000, or split the batch over several calls. Nothing was sent.", carets
        # Nothing of them reached the shell, which runs the next line as it is.
        clean = mcp.call("act", {"window": shell, "actions": [{"action": "type", "text": "echo clean > /tmp/hm-m-clean"}, {"action": "key", "keys": ["enter"]}]})
        assert not clean.is_error, clean
        assert machine.succeed("cat /tmp/hm-m-clean") == "clean\n"
        machine.succeed("test ! -e /tmp/hm-m-snowman")

        # A drag needs held buttons, which a window that shares its Wayland connection with the focused one cannot hold.
        spawn("foot --server=/tmp/hyprmasq-m-foot.sock", "m-foot-server")
        machine.wait_until_succeeds("test -S /tmp/hyprmasq-m-foot.sock")
        focus_workspace(52)
        shared_hidden = foot("hm-m-shared-hidden", executable="footclient -s /tmp/hyprmasq-m-foot.sock")
        focus_workspace(53)
        shared_focused = foot("hm-m-shared-focused", executable="footclient -s /tmp/hyprmasq-m-foot.sock")
        wait_for("shared focus", lambda: snapshot() == (53, shared_focused))
        entry, _ = listed(mcp, shared_hidden)
        assert entry["capabilities"] == {"lease": False, "hold": False, "keyboard": "yes"}, entry
        shared_shot = mcp.call("screenshot", {"window": shared_hidden}).data["shot"]["id"]
        drag = mcp.call("act", {"window": shared_hidden, "shot": shared_shot, "actions": [{"action": "drag", "from": [20, 20], "to": [200, 20]}]})
        refused(drag, "unsupported")
        assert drag.data["error"]["message"] == f"actions[0] (drag) needs the hold capability, which window {shared_hidden} lacks", drag
        assert drag.data["hint"].startswith("This window has the user's keyboard or pointer focus, or shares its Wayland connection"), drag
        # A click would get the user's modifiers instead of its own.
        modified = mcp.call("act", {"window": shared_hidden, "shot": shared_shot, "actions": [{"action": "click", "x": 20, "y": 20, "modifiers": ["ctrl"]}]})
        refused(modified, "unsupported")
        assert modified.data["error"]["message"] == f"actions[0] (click) with modifiers needs the hold capability, which window {shared_hidden} lacks", modified
        # Of the keys of a chord, only modifiers stay pressed between frames there, so a and b would arrive one after
        # another.
        chord = mcp.call("act", {"window": shared_hidden, "actions": [{"action": "key", "keys": ["a", "b"]}]})
        refused(chord, "unsupported")
        assert chord.data["error"]["message"] == f"actions[0] (key) holding a while it presses b needs the hold capability, which window {shared_hidden} lacks", chord
        assert "Hold only modifiers, or press the keys in separate key actions." in chord.data["hint"], chord
        # A chord that holds only modifiers works.
        machine.succeed("rm -f /tmp/hm-m-chord")
        actions = [{"action": "type", "text": "echo "}, {"action": "key", "keys": ["shift", "x"]}, {"action": "type", "text": " > /tmp/hm-m-chord"}, {"action": "key", "keys": ["enter"]}]
        shifted = mcp.call("act", {"window": shared_hidden, "actions": actions})
        assert not shifted.is_error and shifted.data["actions"]["delivery"] == "complete", shifted
        machine.wait_until_succeeds("test -s /tmp/hm-m-chord", timeout=10)
        assert machine.succeed("cat /tmp/hm-m-chord") == "X\n"
        assert snapshot() == (53, shared_focused)
        focus_workspace(4)
        for address in (shared_hidden, shared_focused):
            dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
        wait_for("footclients closed", lambda: all(c["address"] not in (shared_hidden, shared_focused) for c in clients()))
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp reports partial delivery when X11 input is paused"):
        machine.succeed("rm -f /tmp/hm-m-x-target")
        focus_workspace(56)
        x_target = xterm("hm-m-x-target", "sh -c " + shlex.quote("stty -icanon -echo; exec cat > /tmp/hm-m-x-target"))
        machine.wait_until_succeeds("test -f /tmp/hm-m-x-target", timeout=30)
        focus_workspace(57)
        x_active = xev("hm-m-x-active", "m-x-active")
        focus_workspace(4)
        entry, _ = listed(mcp, x_target)
        assert entry["xwayland"] and entry["capabilities"]["keyboard"] == "x11-focus" and not entry["capabilities"]["lease"], entry
        rng = random.Random(56)
        paused_text = "".join(rng.choice(string.ascii_lowercase) for _ in range(400))
        leaked = log_size("m-x-active")
        typing = mcp.start("act", {"window": x_target, "actions": [{"action": "type", "text": paused_text}], "capture": "window"})
        wait_for("typing into the hidden X11 window", lambda: len(machine.succeed("cat /tmp/hm-m-x-target")) >= 20)
        # Another X11 window of the same X server takes the focus, so the plugin drops the input and pauses the devices.
        focus_workspace(57)
        wait_for("X11 window active", lambda: snapshot() == (57, x_active))
        paused = mcp.result(typing)
        failure(paused, "input_paused")
        actions = paused.data["actions"]
        keys = actions["progress"]["keys"]
        assert (actions["done"], actions["total"], actions["delivery"], actions["failed_index"]) == (0, 1, "partial", 0), paused
        # The hint does not suggest sending it again.
        hint = paused.data["hint"]
        assert "retry" not in hint.lower() and hint.endswith("Look at the shot before repeating anything."), paused
        assert re.fullmatch(
            rf"0/1 delivered, delivery partial: actions\[0\] \(type\) may be partly applied \({keys} keys sent\) · shot s\d+ \d+×\d+ · no lease",
            paused.text.splitlines()[1],
        ), paused
        keep(paused, "x-paused")
        time.sleep(1)
        typed_text = machine.succeed("cat /tmp/hm-m-x-target")
        assert typed_text == paused_text[: len(typed_text)] and len(typed_text) < len(paused_text), typed_text
        leaked_keys = log_since("m-x-active", leaked).count("KeyPress event")
        machine.log(f"hyprmasq-mcp: input_paused after {keys} keys, {len(typed_text)} typed, {leaked_keys} in the active window")
        assert keys == len(typed_text) + leaked_keys, (keys, typed_text, leaked_keys)
        focus_workspace(4)
        assert snapshot() == before_focus
        for address in (x_target, x_active):
            dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
        wait_for("X11 windows closed", lambda: all(c["address"] not in (x_target, x_active) for c in clients()))

    with subtest("hyprmasq-mcp waits for a redraw, for a change since a shot, and until the deadline"):
        # The shots here are full ones, whose summaries and pixels the checks read.
        full = {"capture": "window"}
        # Without expect_change, input that draws nothing settles after the quiet time.
        shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        hover = mcp.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "move", "x": 30, "y": 40}], "wait": {"expect_change": False, "quiet_ms": 300},
                                 **full})
        wait = hover.data["wait"]
        assert not hover.is_error and wait["ended"] == "quiet" and wait["summary"] == "settled", hover
        assert not wait["redraw_observed"] and wait["commits"] == 0 and wait["quiet_ms"] >= 300 and wait["elapsed_ms"] < 1000, hover
        assert re.fullmatch(r"1/1 delivered · no redraw · quiet \d+ ms · \d+ ms · shot s\d+ 300×200 · lease \d+ s", hover.text), hover

        # A popup opens after a shot, without any input. A wait for a change since that shot ends at once.
        focus_workspace(54)
        spawn("hyprmasq-focus-probe popup hm-m-popchange 20 20 60 40", "m-popchange")
        wait_for("popup probe", lambda: client("hm-m-popchange") is not None)
        focus_workspace(4)
        popchange = client("hm-m-popchange")["address"]
        time.sleep(0.5)
        unopened = mcp.call("screenshot", {"window": popchange})
        machine.succeed("pkill -USR1 -f 'hyprmasq-focus-probe [p]opup hm-m-popchange'")
        machine.wait_until_succeeds("grep -q 'configured popup' /tmp/hyprmasq-m-popchange.log", timeout=10)
        time.sleep(1)
        changed = mcp.call("act", {"window": popchange, "shot": unopened.data["shot"]["id"], "wait": {"expect_ms": 5000}, **full})
        wait = changed.data["wait"]
        assert not changed.is_error and wait["ended"] == "quiet" and wait["redraw_observed"] and wait["commits"] >= 2, changed
        assert wait["elapsed_ms"] < 1000 and changed.data["actions"] == {"done": 0, "total": 0, "delivery": "none"}, changed
        assert re.fullmatch(r"redraw observed · quiet \d+ ms · \d+ ms · shot s\d+ 640×480 · no lease", changed.text), changed
        assert near(pixels(keep(changed, "popup-opened"), (50, 40))[0], popup_rgb) and near(pixels(unopened.images[0], (50, 40))[0], window_rgb)
        # Since the shot that shows it, nothing changes.
        changed_id = changed.data["shot"]["id"]
        unchanged = mcp.call("act", {"window": popchange, "shot": changed_id, "wait": {"expect_ms": 500}, **full})
        assert unchanged.data["wait"]["ended"] == "no_redraw" and unchanged.data["wait"]["summary"] == "no_change", unchanged
        assert re.fullmatch(
            rf"no redraw within 500 ms · shot s\d+ 640×480 · no lease\nNothing was drawn since shot {changed_id}\. "
            r"If the window is still loading, wait again: act with no actions, a larger wait\.expect_ms, and a deadline_ms at least 1000 above it\.",
            unchanged.text,
        ), unchanged
        # A change phase that runs into the deadline times out, and the window drew nothing.
        late = mcp.call("act", {"window": popchange, "shot": changed_id, "wait": {"expect_ms": 2000}, "deadline_ms": 3000, **full})
        wait = late.data["wait"]
        assert not late.is_error and wait["ended"] == "timeout" and wait["summary"] == "no_change" and not wait["redraw_observed"], late
        assert re.fullmatch(
            r"timeout after \d+ ms, no redraw · shot s\d+ 640×480 · no lease\n"
            r"Nothing was drawn before the deadline\. Look at the shot; to wait longer for a first redraw, act with no actions and a larger deadline_ms \(at most 30000\)\.",
            late.text,
        ), late

        # A window that keeps drawing never settles. The wait ends before the deadline, leaving time for the capture.
        focus_workspace(55)
        busy_shell = foot("hm-m-busy", "sh -c 'while :; do date +%N; sleep 0.02; done'")
        focus_workspace(4)
        time.sleep(0.5)
        timed = mcp.call("act", {"window": busy_shell, "deadline_ms": 3000, **full})
        wait = timed.data["wait"]
        assert not timed.is_error and wait["ended"] == "timeout" and wait["summary"] == "busy" and wait["redraw_observed"], timed
        assert 1000 <= wait["elapsed_ms"] <= 2050 and wait["quiet_ms"] < 200 and 2 <= timed.received - timed.sent < 3, timed
        assert re.fullmatch(
            r"timeout after \d+ ms, still drawing · shot s\d+ 640×480 · no lease\n"
            r"The window kept drawing until the deadline, so it may be animating or still loading\. "
            r"Look at the shot; to wait longer, act with no actions and a larger deadline_ms \(at most 30000\)\.",
            timed.text,
        ), timed
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp does not wait for an animation, names it, and leaves its changes out"):
        # A digit at the top left of a foot window counts ten times a second, a small box that keeps redrawing. The
        # plugin takes it for an animation once it has redrawn for a second, so the wait settles long before the
        # deadline. The digit's changes since the shot are left out of the zooms.
        machine.succeed("printf '%s\\n' 'printf \"\\033[?25l\"; i=0' 'while :; do printf \"\\033[1;1H%d\" $((i % 10)); i=$((i + 1)); sleep 0.1; done' > /tmp/hm-tick.sh")
        focus_workspace(55)
        ticking = foot("hm-m-ticking", "sh /tmp/hm-tick.sh")
        focus_workspace(4)
        ticking_shot = mcp.call("screenshot", {"window": ticking}).data["shot"]["id"]
        settled = mcp.call("act", {"window": ticking, "shot": ticking_shot, "wait": {"expect_change": False}})
        wait = settled.data["wait"]
        assert not settled.is_error and wait["ended"] == "quiet" and wait["elapsed_ms"] < 5000, settled
        assert len(wait["animated"]) == 1, settled
        box = wait["animated"][0]
        assert box["x"] <= 2 and box["y"] <= 2 and box["height"] < 60, settled
        assert re.search(r"· quiet \d+ ms except animation at \d+,\d+ \d+×\d+ · ", settled.text), settled
        assert settled.data["shot"]["changes"]["regions"] == [], settled
        keep(mcp.call("screenshot", {"window": ticking}), "ticking")
        assert mcp.call("close", {"window": ticking}).data["closed"]
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp lets a click settle before the keys after it"):
        shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        start = len(probe_events("m-probe"))
        keys = [{"action": "key", "keys": ["a"]}, {"action": "key", "keys": ["b"]}]
        settled = mcp.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 60, "y": 70}, *keys], "wait": {"expect_change": False}})
        assert not settled.is_error and settled.data["actions"] == {"done": 3, "total": 3, "delivery": "complete"}, settled
        stamps = {e: t for t, e in probe_input(start)}
        # The first key comes at least 300 ms after the click's release. The sender promises no upper bound, so that
        # only the first key waits is left to the Go tests.
        assert stamps["key hm-m-probe 30 1"] - stamps["button hm-m-probe 0"] >= 290, probe_input(start)
        assert stamps["key hm-m-probe 48 1"] > stamps["key hm-m-probe 30 0"], probe_input(start)
        # The next subtest starts without the emulated focus that this input gave.
        assert not mcp.call("release", {"window": probe}).is_error
        wait_for("leaves after release", lambda: probe_leaves(start) == {"pointer leave hm-m-probe", "keyboard leave hm-m-probe"}, timeout=5)

    with subtest("hyprmasq-mcp keeps the emulated focus between calls, and releases it on release and on exit"):
        assert not mcp.call("release", {"window": probe}).is_error
        time.sleep(0.5)
        shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        start = len(probe_events("m-probe"))
        quiet = {"expect_change": False}
        first = mcp.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 60, "y": 70}, {"action": "key", "keys": ["a"]}], "wait": quiet})
        assert first.data["lease"]["held"] and first.data["lease"]["remaining_s"] in (59, 60), first
        owner = window_state(probe)["lease"]["owner"]
        time.sleep(4)
        second = mcp.call("act", {"window": probe, "actions": [{"action": "key", "keys": ["b"]}], "wait": quiet})
        assert second.data["lease"]["held"], second
        events = probe_events("m-probe", start)
        names = [e for _, e in events]
        assert {"pointer enter hm-m-probe", "keyboard enter hm-m-probe", "key hm-m-probe 30 1", "key hm-m-probe 48 1"} <= set(names), events
        assert not probe_leaves(start), events
        stamps = {e: t for t, e in events}
        assert stamps["key hm-m-probe 48 1"] - stamps["key hm-m-probe 30 1"] >= 4000, events
        entry, line = listed(mcp, probe)
        assert entry["lease"]["held"] and entry["lease"]["mine"] and entry["lease"]["remaining_s"] > 50, entry
        assert re.search(r" · lease \d+ s \(this session\)( · |$)", line), line
        release_result = mcp.call("release", {"window": probe})
        assert release_result.data == {"summary": f"lease released; {probe} stays open", "released": True}, release_result
        wait_for("leaves after release", lambda: probe_leaves(start) == {"pointer leave hm-m-probe", "keyboard leave hm-m-probe"}, timeout=5)
        assert window_state(probe)["lease"] is None
        assert mcp.call("release", {"window": probe}).data == {"summary": f"no lease was held on {probe}", "released": False}

        # A server that exits releases its own leases, and only those.
        other = Mcp("b")
        other.initialize()
        shot_id = other.call("screenshot", {"window": probe}).data["shot"]["id"]
        assert not other.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 60, "y": 70}], "wait": quiet}).is_error
        other_owner = window_state(probe)["lease"]["owner"]
        assert other_owner.startswith("mcp-") and other_owner != owner, (owner, other_owner)
        assert not mcp.call("act", {"window": shell, "actions": [{"action": "key", "keys": ["shift"]}], "wait": quiet}).is_error
        assert window_state(shell)["lease"]["owner"] == owner
        start = len(probe_events("m-probe"))
        assert other.stop() == 0
        wait_for("leave after the server exited", lambda: "pointer leave hm-m-probe" in probe_leaves(start), timeout=5)
        assert window_state(probe)["lease"] is None and window_state(shell)["lease"]["owner"] == owner

    with subtest("hyprmasq-mcp lists the windows that input opens"):
        # The launched shell starts another foot, which a window rule puts on a hidden workspace. The shell prints dots
        # until the window is there, so the wait ends after it appeared.
        hypr("eval " + shlex.quote('hl.window_rule({ name = "hm-m-child", match = { title = "^hm-m-child$" }, workspace = "58 silent" })'))
        line = "foot -T hm-m-child sleep 600 > /dev/null 2>&1 & until hyprctl clients | grep -q hm-m-child; do printf .; sleep 0.1; done"
        opened = mcp.call("act", {"window": shell, "actions": [{"action": "type", "text": line}, {"action": "key", "keys": ["enter"]}], "wait": {"quiet_ms": 500}})
        assert not opened.is_error, opened
        child = next(w for w in opened.data["new_windows"] if w["title"] == "hm-m-child")
        # It carries the launch token, and has a process of its own.
        assert child["class"] == "foot" and child["launched"] and not child["same_pid"] and child["pid"] != shell_pid, opened
        assert f'new window {child["window"]} (foot "hm-m-child", launched)' in opened.text.splitlines()[0], opened
        # Within a minute of the shell's launch, the plugin puts it on the launch's workspace, as Hyprland's exec rule
        # does, before the rule's workspace 58; later the rule decides. Either way it opens hidden.
        workspace = client("hm-m-child")["workspace"]["name"]
        assert workspace in ("58", "hm-m") and snapshot() == before_focus, client("hm-m-child")
        closed = mcp.call("close", {"window": child["window"]})
        assert not closed.is_error and closed.data["closed"] and closed.data["new_windows"] == [] and closed.data["elapsed_ms"] < 2000, closed
        # Next to the shell, closing it resizes the shell, which the hint names.
        resized = r"\nClosing a tiled window resizes the tiled windows on its workspace \(" + re.escape(shell) + r"\), .*" if workspace == "hm-m" else ""
        assert re.fullmatch(rf"closed {child['window']} after \d+ ms" + resized, closed.text), closed
        assert client("hm-m-child") is None

    with subtest("two hyprmasq-mcp servers take turns on one window"):
        other = Mcp("c")
        other.initialize()

        def typing_batch(letter):
            return {"window": probe, "actions": [{"action": "type", "text": letter * 60}], "wait": {"expect_change": False, "quiet_ms": 1000}}

        # Each call waits a second for quiet after its keys, and the other one's keys come only after that.
        start = len(probe_events("m-probe"))
        first = mcp.start("act", typing_batch("a"))
        second = other.start("act", typing_batch("b"))
        results = [mcp.result(first), other.result(second)]
        for result in results:
            assert not result.is_error and result.data["actions"]["delivery"] == "complete", result
        key_presses = [(t, int(e.split()[2])) for t, e in probe_input(start) if e.startswith("key ") and e.endswith(" 1")]
        assert [code for _, code in key_presses] in ([30] * 60 + [48] * 60, [48] * 60 + [30] * 60), key_presses
        # Both calls were made before the first one's keys were all typed, so the gap shows the window lock.
        assert max(result.sent for result in results) * 1000 < key_presses[59][0], (results, key_presses[59])
        assert key_presses[60][0] - key_presses[59][0] >= 1000, key_presses

        # A call that cannot get the window from the other within its deadline sends nothing.
        holding = mcp.start("act", {"window": probe, "wait": {"expect_ms": 6000}}, token="hm-m-holding")
        wait_for("the first call waits", lambda: any(p.get("message") == "waiting for the window to settle" for p in mcp.progress("hm-m-holding")), timeout=10)
        start = len(probe_events("m-probe"))
        # Input has to end 2250 ms before the deadline, so that a sender the deadline stops has reported before the
        # capture, and the call stops waiting for the lock then.
        late = other.call("act", {"window": probe, "actions": [{"action": "key", "keys": ["c"]}], "deadline_ms": 3000})
        refused(late, "deadline")
        assert re.fullmatch(
            r"waited \d+ ms for the window lock, held by another call on this window, until too little time was left before the deadline",
            late.data["error"]["message"],
        ), late
        assert late.received - late.sent < 3 and late.images, late
        held = mcp.result(holding)
        assert held.data["wait"]["ended"] == "no_redraw" and held.received > late.received, held
        assert probe_input(start) == [], probe_events("m-probe", start)
        assert other.stop() == 0

    with subtest("hyprmasq-mcp ends a wait when the client cancels the call"):
        # The wait sends the hidden window frame callbacks, so it draws while the wait runs, and only then.
        drawn = window_state(busy_shell)["commits"]
        waiting = mcp.start("act", {"window": busy_shell, "wait": {"expect_ms": 29000}, "deadline_ms": 30000, "capture": "window"}, token="hm-m-cancel")
        wait_for("progress", lambda: any(p.get("message") == "waiting for the window to settle" for p in mcp.progress("hm-m-cancel")), timeout=10)
        assert all(p["total"] == 30000 and p["progress"] <= 30000 for p in mcp.progress("hm-m-cancel")), mcp.progress("hm-m-cancel")
        assert window_state(busy_shell)["commits"] > drawn
        cancelled_at = mcp.notify("notifications/cancelled", {"requestId": waiting, "reason": "hm-m test"})
        cancelled = mcp.result(waiting, timeout=10)
        assert not cancelled.is_error and cancelled.data["wait"]["ended"] == "cancelled" and cancelled.received - cancelled_at < 2, cancelled
        assert re.fullmatch(r"cancelled · shot s\d+ 640×480 · no lease", cancelled.text), cancelled
        # The plugin's wait ended too: the window gets no more frame callbacks.
        time.sleep(1)
        drawn = window_state(busy_shell)["commits"]
        time.sleep(1.5)
        assert window_state(busy_shell)["commits"] == drawn
        assert snapshot() == before_focus

    def progressed(server, token, message):
        return lambda: any(p.get("message") == message for p in server.progress(token))

    with subtest("hyprmasq-mcp stops a batch whose window is resized while it is sent"):
        shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        start = len(probe_events("m-probe"))
        actions = [{"action": "click", "x": 20, "y": 20}, {"action": "pause", "ms": 4000}, {"action": "click", "x": 40, "y": 40}]
        resizing = mcp.start("act", {"window": probe, "shot": shot_id, "actions": actions}, token="hm-m-resized")
        wait_for("sending", progressed(mcp, "hm-m-resized", "sending input"), timeout=10)
        float_window(probe, 100, 100, 320, 220)
        resized = mcp.result(resizing)
        failure(resized, "stale_shot")
        assert resized.data["error"]["message"] == "the window was resized from 300×200 to 320×220 during the batch, so the batch stopped", resized
        # The first click was sent; the pause stopped, and the second click never went out.
        assert resized.data["actions"] == {"done": 1, "total": 3, "delivery": "partial", "failed_index": 1}, resized
        assert resized.data["shot"]["width"] == 320 and resized.data["hint"].endswith("Look at the shot before repeating anything."), resized
        time.sleep(0.5)
        assert [e for _, e in probe_input(start)] == ["motion hm-m-probe 20 20", "button hm-m-probe 1", "button hm-m-probe 0"], probe_events("m-probe", start)
        float_window(probe, 100, 100, 300, 200)
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp checks a window again after waiting for input to another window"):
        shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        start = len(probe_events("m-probe"))
        # A long text into the shell holds the send lock, while a click on the probe waits for it.
        long_line = "# " + "x" * 600
        typing = mcp.start("act", {"window": shell, "actions": [{"action": "type", "text": long_line}, {"action": "key", "keys": ["ctrl+u"]}]}, token="hm-m-long")
        wait_for("typing", progressed(mcp, "hm-m-long", "sending input"), timeout=10)
        queued = mcp.start("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 20, "y": 20}]}, token="hm-m-queued")
        wait_for("queued", progressed(mcp, "hm-m-queued", "waiting for input to other windows"), timeout=10)
        float_window(probe, 100, 100, 320, 220)
        stale = mcp.result(queued)
        typed = mcp.result(typing)
        assert not typed.is_error and typed.data["actions"]["delivery"] == "complete", typed
        refused(stale, "stale_shot")
        assert stale.data["error"]["message"] == f"the window was resized from 300×200 to 320×220 since shot {shot_id}", stale
        time.sleep(0.5)
        assert probe_input(start) == [], probe_events("m-probe", start)
        float_window(probe, 100, 100, 300, 200)

    with subtest("hyprmasq-mcp captures a region after the input"):
        shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
        start = len(probe_events("m-probe"))
        quiet = {"expect_change": False}
        zoomed = mcp.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 30, "y": 40}], "wait": quiet,
                                  "capture": {"shot": shot_id, "x": 20, "y": 30, "width": 100, "height": 60}})
        zoom_shot = zoomed.data["shot"]
        assert not zoomed.is_error and (zoom_shot["width"], zoom_shot["height"], zoom_shot["frame"]) == (100, 60, region(20, 30, 100, 60)), zoomed
        assert re.fullmatch(r"1/1 delivered · no redraw · quiet \d+ ms · \d+ ms · shot s\d+ 100×60 · lease \d+ s", zoomed.text), zoomed
        assert pixels(keep(zoomed, "act-region"))[-1] == (100, 60)
        # Its pixels are coordinates of their own. Without a capture there is no shot.
        again = mcp.call("act", {"window": probe, "shot": zoom_shot["id"], "actions": [{"action": "click", "x": 50, "y": 20}], "wait": quiet, "capture": "none"})
        assert not again.is_error and not again.images and "shot" not in again.data, again
        assert probe_motions(start) == [(30, 40), (70, 50)], probe_events("m-probe", start)
        # A region is window-local, so after a resize during the call it would show another part of the window. act
        # captures the whole window instead, and says why.
        waiting = mcp.start("act", {"window": probe, "wait": {"expect_ms": 5000}, "deadline_ms": 8000,
                                    "capture": {"shot": shot_id, "x": 20, "y": 30, "width": 100, "height": 60}}, token="hm-m-zoom-resized")
        wait_for("waiting", progressed(mcp, "hm-m-zoom-resized", "waiting for the window to settle"), timeout=10)
        float_window(probe, 100, 100, 320, 220)
        resized = mcp.result(waiting)
        whole = resized.data["shot"]
        assert not resized.is_error and (whole["width"], whole["height"], whole["frame"]) == (320, 220, region(0, 0, 320, 220)), resized
        assert resized.data["hint"] == (
            f"The window was resized from 300×200 to 320×220 since shot {shot_id}, which the capture region was read from, "
            f"so shot {whole['id']} shows the whole window instead."
        ), resized
        assert pixels(keep(resized, "act-region-resized"))[-1] == (320, 220)
        float_window(probe, 100, 100, 300, 200)

    # A foot window that prints each line of a FIFO through printf's %b, with its cursor hidden and no echo, so that the
    # test decides what changes between two shots.
    canvas_fifo = "/tmp/hm-m-canvas.fifo"
    white = (0xFF, 0xFF, 0xFF)

    def draw(*parts):
        """Writes escape sequences and text to the canvas."""
        line = "".join(parts).replace("\\", "\\\\").replace("\033", "\\033")
        machine.succeed(f"printf '%s\\n' {shlex.quote(line)} > {canvas_fifo}")

    def cells(row, column, width=4):
        """White cells from the row and column on, counted from 1."""
        return f"\033[{row};{column}H\033[7m{' ' * width}\033[0m"

    def zoom_line(shot, zoom):
        """The line of a result that lists a zoom of a changes shot."""
        return f"zoom {zoom['shot']} {zoom['image_width']}×{zoom['image_height']} of {shot['id']} at {zoom['x']},{zoom['y']} {zoom['width']}×{zoom['height']}"

    with subtest("hyprmasq-mcp returns what changed since a shot, as a zoom into each changed region"):
        machine.succeed(f"rm -f {canvas_fifo} && mkfifo -m 666 {canvas_fifo}")
        script = f"stty -echo; printf '\\033[?25l'; exec 3<>{canvas_fifo}; while IFS= read -r line <&3; do printf '%b' \"$line\"; done"
        canvas_command = ["foot", "-T", "hm-m-canvas", "-o", "colors-dark.background=202020", "-o", "colors-dark.foreground=ffffff", "sh", "-c", script]
        launched = mcp.call("launch", {"command": canvas_command, "workspace": "hm-m-canvas"})
        assert not launched.is_error, launched
        canvas, base = launched.data["window"]["window"], launched.data["shot"]["id"]

        # Nothing changed since the launch's shot, the session's last of the window: no image, and for an empty batch
        # no hint. The changes shot has the pixels of the shot it compared with.
        same = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}})
        same_shot = same.data["shot"]
        assert not same.is_error and not same.images and same_shot["changes"] == {"base": base, "regions": []} and "hint" not in same.data, same
        assert (same_shot["width"], same_shot["height"], same_shot["frame"]) == (640, 480, region(0, 0, 640, 480)), same
        assert re.fullmatch(rf"(no redraw|redraw observed) · quiet \d+ ms · \d+ ms · shot {same_shot['id']} = {base}, no change · no lease", same.text), same
        # A key that changes nothing says so, and points at the shot the window still shows.
        pressed = mcp.call("act", {"window": canvas, "actions": [{"action": "key", "keys": ["a"]}]})
        pressed_id = pressed.data["shot"]["id"]
        assert not pressed.is_error and not pressed.images and pressed.data["shot"]["changes"] == {"base": same_shot["id"], "regions": []}, pressed
        assert re.search(rf"Look at {same_shot['id']} before repeating (it|them)\.$", pressed.data["hint"]), pressed

        # Four white cells are one change. Its zoom shows them with the pixel around them that the 3×3 average reaches
        # and a margin of 24 pixels, here at scale 1, and lists that box in the pixels of the shot.
        draw(cells(5, 10))
        one = mcp.call("act", {"window": canvas, "shot": pressed_id})
        one_shot = one.data["shot"]
        assert not one.is_error and one_shot["changes"]["base"] == pressed_id and len(one_shot["changes"]["regions"]) == 1 and len(one.images) == 1, one
        zoom = one_shot["changes"]["regions"][0]
        assert re.fullmatch(rf"redraw observed · quiet \d+ ms · \d+ ms · shot {one_shot['id']} = {pressed_id} \+ 1 change · lease \d+ s\n" + re.escape(zoom_line(one_shot, zoom)),
                            one.text), one
        block = color_box(mcp.call("screenshot", {"window": canvas}).images[0], white)
        assert [zoom["x"], zoom["y"], zoom["x"] + zoom["width"], zoom["y"] + zoom["height"]] == [block[0] - 25, block[1] - 25, block[2] + 25, block[3] + 25], (zoom, block)
        assert pixels(keep(one, "changes-one"))[-1] == (zoom["width"], zoom["height"]) == (zoom["image_width"], zoom["image_height"]), zoom
        assert color_box(one.images[0], white) == [block[0] - zoom["x"], block[1] - zoom["y"], block[2] - zoom["x"], block[3] - zoom["y"]], (zoom, block)

        # Changes far apart get a zoom each, top to bottom, in the order of the images.
        draw(cells(2, 3), cells(12, 30), cells(22, 55))
        several = mcp.call("act", {"window": canvas, "shot": one_shot["id"]})
        several_shot = several.data["shot"]
        regions = several_shot["changes"]["regions"]
        assert not several.is_error and len(regions) == 3 and len(several.images) == 3, several
        assert regions[0]["y"] < regions[1]["y"] < regions[2]["y"] and regions[0]["x"] < regions[1]["x"] < regions[2]["x"], several
        assert several.text.splitlines()[1:] == [zoom_line(several_shot, z) for z in regions], several
        for i, (image, z) in enumerate(zip(several.images, regions)):
            assert pixels(image)[-1] == (z["image_width"], z["image_height"]) and color_box(image, white), (i, z)
        keep(several, "changes-several")

        # A line one pixel wide, as a caret is, and a dot are tiny: they are left out, and counted.
        draw("\033[18;20H.", "\033[8;45H│")
        tiny = mcp.call("act", {"window": canvas, "shot": several_shot["id"]})
        tiny_shot = tiny.data["shot"]
        assert not tiny.is_error and not tiny.images and tiny_shot["changes"] == {"base": several_shot["id"], "regions": [], "left_out": 2}, tiny
        assert tiny.text.splitlines()[1:] == ["2 tiny changes left out"], tiny
        # A highlight 12 levels lighter than the background, as a row's hover in a dark theme, is no change.
        draw("\033[48;2;44;44;44m", *(f"\033[{row};1H{' ' * 40}" for row in range(20, 25)), "\033[0m")
        faint = mcp.call("act", {"window": canvas, "shot": tiny_shot["id"]})
        assert not faint.is_error and not faint.images and faint.data["shot"]["changes"] == {"base": tiny_shot["id"], "regions": []}, faint

        # Seven blocks, six of them in a row about 110 pixels apart, whose margins do not touch, are more than 5
        # regions: the neighbours that add the least area are joined until 5 are left, and every block is in a zoom,
        # the one far apart alone. The cell size comes from the block drawn at row 5 and column 10.
        cell_width, cell_height = (block[2] - block[0]) // 4, block[3] - block[1]
        pad_x, pad_y = block[0] - 9 * cell_width, block[1] - 4 * cell_height
        columns, rows = (640 - pad_x) // cell_width, (480 - pad_y) // cell_height

        def cell_box(row, column, width=4):
            """The box of white cells from the row and column on."""
            x, y = pad_x + (column - 1) * cell_width, pad_y + (row - 1) * cell_height
            return [x, y, x + width * cell_width, y + cell_height]

        step = -(-110 // cell_width)
        spots = [(15, 3 + step * i) for i in range(6)] + [(2, 3 + step * 5)]
        assert cell_box(*spots[-1])[2] <= 640, (cell_width, step)
        draw(*(cells(row, column) for row, column in spots))
        joined = mcp.call("act", {"window": canvas, "shot": faint.data["shot"]["id"]})
        regions = joined.data["shot"]["changes"]["regions"]
        assert not joined.is_error and len(regions) == 5 and len(joined.images) == 5, joined
        keep(joined, "changes-joined")
        inside = [[i for i, z in enumerate(regions) if z["x"] <= box[0] and z["y"] <= box[1] and box[2] <= z["x"] + z["width"] and box[3] <= z["y"] + z["height"]]
                  for box in (cell_box(row, column) for row, column in spots)]
        assert all(len(found) == 1 for found in inside), (inside, regions)
        assert [found[0] for found in inside].count(inside[-1][0]) == 1, (inside, regions)
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp takes coordinates from a changes shot and from its zooms"):
        # hm-m-popchange's popup, at 20,20 and 60x40, is open; SIGUSR1 closes and opens it. Its red has about the
        # luminance of the window's blue, so the color channels show the change.
        toggle = "pkill -USR1 -f 'hyprmasq-focus-probe [p]opup hm-m-popchange'"
        opened = mcp.call("screenshot", {"window": popchange})
        opened_id = opened.data["shot"]["id"]
        assert color_box(opened.images[0], popup_rgb) == [20, 20, 80, 60], opened
        offset = log_size("m-popchange")
        machine.succeed(toggle)
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'popup closed'", timeout=10)
        closed = mcp.call("act", {"window": popchange, "shot": opened_id, "wait": {"expect_ms": 5000}})
        closed_shot = closed.data["shot"]
        regions = closed_shot["changes"]["regions"]
        # The margin is cut at the window's edges.
        assert not closed.is_error and [(z["x"], z["y"], z["width"], z["height"]) for z in regions] == [(0, 0, 104, 84)], closed
        assert near(pixels(keep(closed, "changes-popup-closed"), (50, 40))[0], window_rgb)

        # The changes shot has the pixels of the shot it compared with, and its coordinates reach the window. The probe
        # answers a press on its window with a popup of its own, so the pointer only moves there.
        offset = log_size("m-popchange")
        moved = mcp.call("act", {"window": popchange, "shot": closed_shot["id"], "actions": [{"action": "move", "x": 300, "y": 200}], "wait": {"expect_change": False}})
        assert not moved.is_error and moved.data["shot"]["changes"] == {"base": closed_shot["id"], "regions": []}, moved
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'motion hm-m-popchange'", timeout=10)
        assert [line for line in log_since("m-popchange", offset).splitlines() if line.startswith("motion")] == ["motion hm-m-popchange 300 200"]

        # A zoom into a change takes coordinates in its own pixels: a click on the popup it shows reaches the popup.
        offset = log_size("m-popchange")
        machine.succeed(toggle)
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'configured popup'", timeout=10)
        reopened = mcp.call("act", {"window": popchange, "shot": moved.data["shot"]["id"], "wait": {"expect_ms": 5000}})
        regions = reopened.data["shot"]["changes"]["regions"]
        assert not reopened.is_error and len(regions) == 1, reopened
        zoom = regions[0]
        popup_box = color_box(keep(reopened, "changes-popup-opened"), popup_rgb)
        assert popup_box == [20 - zoom["x"], 20 - zoom["y"], 80 - zoom["x"], 60 - zoom["y"]], (zoom, popup_box)
        offset = log_size("m-popchange")
        clicked = mcp.call("act", {"window": popchange, "shot": zoom["shot"], "actions": [{"action": "click", "x": popup_box[0] + 7, "y": popup_box[1] + 5}],
                                   "wait": {"expect_change": False}})
        assert not clicked.is_error, clicked
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'button popup 0'", timeout=10)
        assert [line for line in log_since("m-popchange", offset).splitlines() if line.startswith("motion")] == ["motion popup 7 5"]
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp returns a full shot in place of the changes, and says why"):
        def full_shot(result, reason):
            """Checks that the result has a full shot for the reason, and returns its id."""
            shot = result.data["shot"]
            assert not result.is_error and len(result.images) == 1 and "changes" not in shot and re.fullmatch(reason, shot["full_shot"]), result
            assert f" · shot {shot['id']} {shot['width']}×{shot['height']} (full shot: {shot['full_shot']}) · " in result.text.splitlines()[0], result
            assert pixels(result.images[0])[-1] == (shot["width"], shot["height"]), result
            return shot["id"]

        # More than 35% of the window changed.
        last = mcp.call("screenshot", {"window": canvas}).data["shot"]["id"]
        draw(*(cells(row, 1, 80) for row in range(1, 21)))
        last = full_shot(mcp.call("act", {"window": canvas, "shot": last}), rf"\d+% changed since {last}")
        # Clearing the canvas changes as much. The call waits until foot has drawn it, so its shot is the next base.
        draw("\033[2J")
        last = full_shot(mcp.call("act", {"window": canvas, "shot": last}), rf"\d+% changed since {last}")
        # More than 5 regions changed, which joined would cover more than 35% of the window: blocks about 72 pixels
        # wide and 125 tall in the corners and the middle of the top and bottom edges.
        wide, tall = round(72 / cell_width), round(125 / cell_height)
        draw(*(cells(row, column, wide) for column in (1, (columns - wide) // 2 + 1, columns - wide + 1)
               for row in [*range(1, tall + 1), *range(rows - tall + 1, rows + 1)]))
        last = full_shot(mcp.call("act", {"window": canvas, "shot": last}), f"6 changed regions since {last}")
        # The window was resized.
        float_window(canvas, 0, 0, 600, 440)
        last = full_shot(mcp.call("act", {"window": canvas, "shot": last, "wait": {"expect_change": False}}), f"resized since {last}")
        # Another session has no earlier shot to compare with, and compares later calls with the full shot it got.
        other = Mcp("g")
        other.initialize()
        unknown = full_shot(other.call("act", {"window": canvas, "actions": [{"action": "key", "keys": ["a"]}]}), "no earlier shot to compare with")
        assert other.call("act", {"window": canvas, "wait": {"expect_change": False}}).data["shot"]["changes"]["base"] == unknown
        assert other.stop() == 0
        # After 10 changes shots in a row, each compared with the one before, comes a full shot.
        for i in range(10):
            result = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}})
            assert result.data["shot"]["changes"]["base"] == last, (i, result)
            last = result.data["shot"]["id"]
        full_shot(mcp.call("act", {"window": canvas, "wait": {"expect_change": False}}), "10 changes shots in a row")

        # capture "window" and "none" are as they were.
        window = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}, "capture": "window"})
        assert set(window.data["shot"]) == {"id", "width", "height", "frame"} and len(window.images) == 1, window
        assert re.fullmatch(rf"(no redraw|redraw observed) · quiet \d+ ms · \d+ ms · shot {window.data['shot']['id']} 600×440 · (no lease|lease \d+ s)", window.text), window
        none = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}, "capture": "none"})
        assert not none.images and "shot" not in none.data and " shot " not in none.text, none
        assert mcp.call("close", {"window": canvas}).data["closed"]
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp fits shots within --max-image-size"):
        small = Mcp("e", "--max-image-size", "150")
        small.initialize()
        fitted = small.call("screenshot", {"window": probe})
        fitted_id = fitted.data["shot"]["id"]
        assert fitted.data["shot"] == {"id": fitted_id, "width": 150, "height": 100, "frame": region(0, 0, 300, 200)}, fitted
        assert pixels(fitted.images[0])[-1] == (150, 100)
        # A shot pixel is two logical pixels.
        start = len(probe_events("m-probe"))
        clicked = small.call("act", {"window": probe, "shot": fitted_id, "actions": [{"action": "click", "x": 60, "y": 35}], "wait": {"expect_change": False}})
        assert not clicked.is_error and clicked.data["shot"]["width"] == 150, clicked
        assert probe_motions(start) == [(120, 70)], probe_events("m-probe", start)
        # Zooms are fitted too, and one that fits keeps the monitor's scale.
        whole = small.call("screenshot", {"window": probe, "region": {"shot": fitted_id, "x": 0, "y": 0, "width": 150, "height": 100}})
        assert (whole.data["shot"]["width"], whole.data["shot"]["height"], whole.data["shot"]["frame"]) == (150, 100, region(0, 0, 300, 200)), whole
        part = small.call("screenshot", {"window": probe, "region": {"shot": fitted_id, "x": 10, "y": 10, "width": 40, "height": 30}})
        assert (part.data["shot"]["width"], part.data["shot"]["height"], part.data["shot"]["frame"]) == (80, 60, region(20, 20, 80, 60)), part
        assert small.stop() == 0

    with subtest("hyprmasq-mcp types through the Compose table of the window's locale"):
        # The server runs in pt_BR.UTF-8, whose Compose table has no ć on a German layout, and the windows in
        # en_US.UTF-8 and in a locale without a table, for which foot uses that of C.UTF-8, the table of en_US.UTF-8.
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
        wait_for("German keymap", lambda: keymap("German"))
        focus_workspace(51)
        shell_command = "env LC_ALL=en_US.UTF-8 bash --noprofile --norc -i"
        en_window = foot("hm-m-compose-en", shell_command, executable="env LC_ALL=en_US.UTF-8 foot")
        xx_window = foot("hm-m-compose-xx", shell_command, executable="env LC_ALL=xx_XX.UTF-8 foot")
        time.sleep(1)
        focus_workspace(4)
        pt_server = Mcp("f", env="LC_ALL=pt_BR.UTF-8")
        pt_server.initialize()

        def compose_act(window, name):
            line = f'printf "%s\\n" "ć" > /tmp/{name}'
            result = pt_server.call("act", {"window": window, "actions": [{"action": "type", "text": line}, {"action": "key", "keys": ["enter"]}]})
            assert not result.is_error and result.data["actions"]["delivery"] == "complete", result
            machine.wait_until_succeeds(f"test -s /tmp/{name}")
            assert machine.succeed(f"cat /tmp/{name}") == "ć\n", result
            return result.data.get("hint", "")

        assert "Compose" not in compose_act(en_window, "hm-m-compose-en"), en_window
        assert compose_act(xx_window, "hm-m-compose-xx").endswith(
            "Characters without a key of their own were typed through the Compose table of en_US.UTF-8, since there is none for the window's "
            "locale xx_XX.UTF-8. The application may compose them differently, so look at the shot to check that they arrived as typed."
        )
        assert pt_server.stop() == 0
        hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
        wait_for("US keymap", lambda: keymap("English"))

    with subtest("hyprmasq-mcp gives a window time to catch up with a resize, and takes no coordinates from an older frame"):
        # focus-probe's resize mode, as in the resizing subtest: from one SIGUSR1 to the next it holds back configures
        # with a new size, and draws its right edge green.
        edge_rgb = (0x22, 0xCC, 0x22)
        title = "hm-m-resize"
        toggle = f"pkill -USR1 -f 'hyprmasq-focus-probe [r]esize {title} '"
        focus_workspace(58)
        spawn(f"hyprmasq-focus-probe resize {title} hold", "m-resize")
        wait_for(title, lambda: client(title) is not None)
        catching = client(title)["address"]
        machine.wait_until_succeeds("grep -qx 'kept 640 480' /tmp/hyprmasq-m-resize.log", timeout=10)
        focus_workspace(4)
        # A neighbour launched beside it halves it, which launch names.
        launched = mcp.call("launch", {"command": ["foot", "-T", "hm-m-neighbour", "sleep", "900"], "workspace": "58"})
        assert not launched.is_error and launched.data["hint"] == (
            f"The new window resized the windows on its workspace ({catching}), so their earlier shots are stale: "
            "take a new screenshot before clicking in one."
        ), launched
        neighbour = launched.data["window"]["window"]
        machine.wait_until_succeeds("grep -qx 'drew 320 480' /tmp/hyprmasq-m-resize.log", timeout=10)
        halved = mcp.call("screenshot", {"window": catching})
        assert (halved.data["shot"]["width"], halved.data["shot"]["height"]) == (320, 480) and "hint" not in halved.data, halved

        # Closing its neighbour on the hidden workspace gives it the whole workspace, which it holds back.
        machine.succeed(toggle)
        machine.wait_until_succeeds("grep -qx holding /tmp/hyprmasq-m-resize.log", timeout=10)
        closed = mcp.call("close", {"window": neighbour})
        assert not closed.is_error and closed.data["closed"], closed
        assert closed.data["hint"] == (
            f"Closing a tiled window resizes the tiled windows on its workspace ({catching}), so their earlier shots are stale: "
            "take a new screenshot before clicking in one."
        ), closed
        machine.wait_until_succeeds("grep -qx 'held 640 480' /tmp/hyprmasq-m-resize.log", timeout=10)
        entry, line = listed(mcp, catching)
        assert entry["resizing"] and re.search(r" · 640×480 · resizing \(not yet redrawn at its new size\)( · |$)", line), (entry, line)

        # A screenshot gives it 1.5 s to catch up, then returns the older frame and says so.
        stale = mcp.call("screenshot", {"window": catching})
        stale_shot = stale.data["shot"]
        assert stale_shot["resizing"] and (stale_shot["width"], stale_shot["height"]) == (640, 480), stale
        assert stale.received - stale.sent >= 1.4, stale
        assert stale.data["summary"] == f"shot {stale_shot['id']} 640×480 (stale: not yet redrawn at its new size) · window {catching} 640×480", stale
        assert stale.data["hint"].startswith(f"Shot {stale_shot['id']} shows an older frame"), stale
        inside, edge, _ = pixels(keep(stale, "resize-stale"), (160, 240), (316, 240))
        assert near(inside, window_rgb) and near(edge, edge_rgb), (inside, edge)

        # act takes no coordinates from it and sends nothing; the shot it returns still shows the older frame.
        start = log_size("m-resize")
        refusal = mcp.call("act", {"window": catching, "shot": stale_shot["id"], "actions": [{"action": "click", "x": 500, "y": 100}]})
        failure(refusal, "stale_shot")
        assert refusal.data["actions"]["delivery"] == "none", refusal
        assert refusal.data["error"]["message"] == (
            f"shot {stale_shot['id']} shows an older frame, since the window had not yet redrawn at its new size 640×480 when it was taken"
        ), refusal
        assert refusal.data["shot"]["resizing"] and refusal.data["hint"].startswith("Nothing was sent. Shot "), refusal

        # An act whose wait runs out before it catches up says so, and its hint is only the one about the older frame.
        timed = mcp.call("act", {"window": catching, "wait": {"expect_change": False}, "deadline_ms": 2000, "capture": "window"})
        wait, timed_shot = timed.data["wait"], timed.data["shot"]
        assert not timed.is_error and wait["ended"] == "timeout" and wait["summary"] == "resizing" and wait["resizing"], timed
        assert timed_shot["resizing"] and re.fullmatch(
            rf"timeout after \d+ ms, still resizing · shot {timed_shot['id']} 640×480 \(stale: not yet redrawn at its new size\) · no lease", timed.data["summary"]
        ), timed
        assert timed.data["hint"].startswith(f"Shot {timed_shot['id']} shows an older frame") and "drawing" not in timed.data["hint"], timed

        # A screenshot that is still waiting when the probe answers returns the frame at the new size.
        pending = mcp.start("screenshot", {"window": catching})
        time.sleep(0.3)
        answered_at = vm_now()
        machine.succeed(toggle)
        current = mcp.result(pending)
        assert not current.is_error and "resizing" not in current.data["shot"] and "hint" not in current.data, current
        assert current.received * 1000 > answered_at, (current, answered_at)
        machine.succeed("grep -qx 'drew 640 480' /tmp/hyprmasq-m-resize.log")
        inside, beyond, edge, _ = pixels(keep(current, "resize-current"), (160, 240), (480, 240), (636, 240))
        assert near(inside, window_rgb) and near(beyond, window_rgb) and near(edge, edge_rgb), (inside, beyond, edge)
        # Its coordinates reach the window.
        moved = mcp.call("act", {"window": catching, "shot": current.data["shot"]["id"], "actions": [{"action": "move", "x": 500, "y": 100}],
                                 "wait": {"expect_change": False}})
        assert not moved.is_error, moved
        motions = [line for line in log_since("m-resize", start).splitlines() if line.startswith("motion ")]
        assert motions == [f"motion {title} 500 100"], log_since("m-resize", start)
        alone = mcp.call("close", {"window": catching})
        assert alone.data["closed"] and "hint" not in alone.data, alone
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp takes coordinates from shots taken while a window pops in"):
        # launch captures a window once it has gone quiet for 300 ms, well within the 10 seconds in which it pops in.
        animations(True)
        edge_rgb, black = (0x22, 0xCC, 0x22), (0, 0, 0)
        command = "exec hyprmasq-focus-probe resize hm-m-anim hold > /tmp/hyprmasq-m-anim.log 2>&1"
        popping = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-anim"})
        assert not popping.is_error and popping.received - popping.sent < 8, popping
        popping_window, popping_shot = popping.data["window"]["window"], popping.data["shot"]
        assert (popping_shot["width"], popping_shot["height"], popping_shot["frame"]) == (640, 480, region(0, 0, 640, 480)), popping
        assert "resizing" not in popping_shot and color_box(keep(popping, "anim"), edge_rgb) == [632, 0, 640, 480], popping
        # A click on the edge in the shot reaches the edge.
        clicked = mcp.call("act", {"window": popping_window, "shot": popping_shot["id"], "actions": [{"action": "click", "x": 636, "y": 240}],
                                   "wait": {"expect_change": False}})
        assert not clicked.is_error, clicked
        machine.wait_until_succeeds("grep -q 'button hm-m-anim 0' /tmp/hyprmasq-m-anim.log", timeout=10)
        assert [line for line in log_since("m-anim", 0).splitlines() if line.startswith("motion ")] == ["motion hm-m-anim 636 240"]

        # An X11 window, which Hyprland scales on screen into the animated size: a click in the middle of xev's inner
        # window in the shot reaches it there.
        command = f"DISPLAY={x_display} exec stdbuf -oL xev -name hm-m-x-anim > /tmp/hyprmasq-m-x-anim.log 2>&1"
        popping_x = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-x-anim"})
        assert not popping_x.is_error and popping_x.received - popping_x.sent < 8, popping_x
        x_window, x_shot = popping_x.data["window"]["window"], popping_x.data["shot"]
        assert (x_shot["width"], x_shot["height"], x_shot["frame"]) == (640, 480, region(0, 0, 640, 480)) and "resizing" not in x_shot, popping_x
        square = color_box(keep(popping_x, "x-anim"), black)
        assert square == x_square, square
        middle = ((square[0] + square[2]) // 2, (square[1] + square[3]) // 2)
        clicked = mcp.call("act", {"window": x_window, "shot": x_shot["id"], "actions": [{"action": "click", "x": middle[0], "y": middle[1]}],
                                   "wait": {"expect_change": False}})
        assert not clicked.is_error, clicked
        machine.wait_until_succeeds("grep -q ButtonRelease /tmp/hyprmasq-m-x-anim.log", timeout=10)
        xev_log = log_since("m-x-anim", 0)
        inner = re.search(r"inner window is (0x[0-9a-f]+)", xev_log)
        assert inner, xev_log
        press = xev_log[xev_log.index("ButtonPress event"):].split("\n\n")[0]
        assert f"subw {inner.group(1)}," in press and f"({middle[0]},{middle[1]})" in press, xev_log
        animations(False)
        for address in (popping_window, x_window):
            assert mcp.call("close", {"window": address}).data["closed"]
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp reports input that closes its own window"):
        # focus-probe closes its window on Escape, before the text after it is sent. The error names Escape as delivered,
        # unless the plugin dropped its release.
        focus_workspace(59)
        spawn("hyprmasq-focus-probe close hm-m-close", "m-close")
        wait_for("closing probe", lambda: client("hm-m-close") is not None)
        focus_workspace(4)
        closing = client("hm-m-close")["address"]
        time.sleep(0.5)
        closed = mcp.call("act", {"window": closing, "actions": [{"action": "key", "keys": ["esc"]}, {"action": "type", "text": "after"}]})
        machine.log(f"hyprmasq-mcp: input that closes its window: {closed}")
        failure(closed, "window_gone")
        assert closed.data["wait"]["ended"] == "window_closed", closed
        actions = closed.data["actions"]
        assert actions["delivery"] in ("partial", "uncertain") and actions["done"] == actions["failed_index"] <= 1, closed
        if actions["done"] == 1:
            assert closed.text.startswith(f"error window_gone: window {closing} closed after actions[0] (key) was delivered, while actions[1] (type) was sent\n"), closed
            assert "so do not send it again" in closed.data["hint"], closed
        else:
            assert closed.text.startswith(f"error window_gone: window {closing} closed while actions[0] (key) was sent\n"), closed
        assert "maybe because of it" in closed.data["hint"] and not closed.images and "shot" not in closed.data, closed
        assert client("hm-m-close") is None and "closed" in log_since("m-close", 0)
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp counts input that closes its window as delivered when only pauses follow"):
        # focus-probe closes its window as Escape or the left button goes down, while the key or the button is held, as
        # a dialog does on its button: Thunderbird's "Send Message" closed before the click's release in a benchmark.
        # A pause after it needs no window, so the call does not wait it out and is no error. A call whose release met
        # the close on a busy host reports it as not delivered, and is repeated.
        for closing_name in ("key", "click"):
            for attempt in range(3):
                closing_title = f"hm-m-closing-{closing_name}-{attempt}"
                focus_workspace(59)
                spawn(f"hyprmasq-focus-probe close {closing_title}", closing_title)
                wait_for(closing_title, lambda: client(closing_title) is not None)
                focus_workspace(4)
                closing = client(closing_title)["address"]
                time.sleep(0.5)
                closing_action = {"action": "key", "keys": ["esc"]}
                closing_arguments = {"window": closing}
                if closing_name == "click":
                    closing_arguments["shot"] = mcp.call("screenshot", {"window": closing}).data["shot"]["id"]
                    closing_action = {"action": "click", "x": 100, "y": 100}
                closing_arguments["actions"] = [closing_action, {"action": "pause", "ms": 3000}]
                started = time.monotonic()
                closed = mcp.call("act", closing_arguments)
                elapsed = time.monotonic() - started
                machine.log(f"hyprmasq-mcp: {closing_name} that closes its window, then a pause: {closed} in {elapsed:.2f} s")
                assert client(closing_title) is None and "closed" in log_since(closing_title, 0)
                if not closed.is_error:
                    break
                failure(closed, "window_gone")
                actions = closed.data["actions"]
                assert actions["delivery"] in ("partial", "uncertain") and actions["done"] == actions["failed_index"] == 0, closed
            assert not closed.is_error and "error" not in closed.data and elapsed < 2.5, (closed, elapsed)
            assert closed.data["actions"] == {"done": 2, "total": 2, "delivery": "complete"}, closed
            assert closed.data["wait"]["ended"] == "window_closed" and closed.text.startswith("2/2 delivered · window closed"), closed
            assert not closed.images and "shot" not in closed.data, closed
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp ends its calls and releases its leases on SIGTERM"):
        # The first server's lease on the shell stays.
        assert not mcp.call("act", {"window": shell, "actions": [{"action": "key", "keys": ["shift"]}], "wait": {"expect_change": False}}).is_error
        signalled = Mcp("d")
        signalled.initialize()
        shot_id = signalled.call("screenshot", {"window": probe}).data["shot"]["id"]
        leased = signalled.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 60, "y": 70}], "wait": {"expect_change": False}})
        assert leased.data["lease"]["held"] and window_state(probe)["lease"]["owner"] != window_state(shell)["lease"]["owner"], leased
        # A call is waiting when the signal comes.
        signalled.start("act", {"window": busy_shell, "wait": {"expect_ms": 29000}, "deadline_ms": 30000}, token="hm-m-term")
        wait_for("waiting", progressed(signalled, "hm-m-term", "waiting for the window to settle"), timeout=10)
        pid = signalled.command(op="pid")["pid"]
        shots = f"{instance_dir}/mcp/{pid}"
        machine.succeed(f"test -d {shots}")
        start = len(probe_events("m-probe"))
        killed_at = vm_now()
        machine.succeed(f"kill -TERM {pid}")
        stopped = signalled.command(op="stop", timeout=10)
        assert stopped["status"] == 0 and stopped["stopped"] * 1000 - killed_at < 4000, (stopped, killed_at)
        assert window_state(probe)["lease"] is None and window_state(shell)["lease"] is not None
        wait_for("leaves after SIGTERM", lambda: "pointer leave hm-m-probe" in probe_leaves(start), timeout=5)
        machine.succeed(f"test ! -e {shots}")
        # The plugin's wait ended with the call: the window gets no more frame callbacks.
        time.sleep(1)
        drawn = window_state(busy_shell)["commits"]
        time.sleep(1.5)
        assert window_state(busy_shell)["commits"] == drawn
        assert snapshot() == before_focus

    with subtest("hyprmasq-mcp closes a launched window, and cleans up when it exits"):
        closed = mcp.call("close", {"window": shell})
        assert not closed.is_error and closed.data["closed"] and closed.data["new_windows"] == [], closed
        assert re.fullmatch(rf"closed {shell} after \d+ ms", closed.text) and client("hm-m-shell") is None, closed
        gone = mcp.call("act", {"window": shell, "actions": [{"action": "key", "keys": ["a"]}]})
        failure(gone, "window_gone")
        shots = f"{instance_dir}/mcp/{mcp.command(op='pid')['pid']}"
        machine.succeed(f"test -d {shots}")
        assert mcp.stop() == 0
        machine.succeed(f"test ! -e {shots}")
        for title in ["hm-m-probe", "hm-m-popleft", "hm-m-popchange", "hm-m-busy"]:
            dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
            wait_for(f"{title} closed", lambda: client(title) is None)
        assert snapshot() == before_focus

    with subtest("unload removes all sockets and hooks, reload succeeds"):
        paths = [s["path"] for s in json.loads(hypr("-j hyprmasq eis list"))]
        assert paths
        # A wait on a window that keeps drawing is still pending at the unload, and gets an answer.
        focus_workspace(20)
        hypr_background(f"-j hyprmasq wait {busy} 200 4000", "/tmp/hm-wait-unload")
        time.sleep(0.5)
        assert hypr(f"plugin unload {plugin}") == "ok"
        machine.wait_until_succeeds("test -s /tmp/hm-wait-unload", timeout=10)
        reply = json.loads(machine.succeed("cat /tmp/hm-wait-unload"))
        assert not reply["ok"] and "unloaded" in reply["error"], reply
        focus_workspace(4)
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
        "/tmp/hyprmasq-hm-i-wev.log",
        "/tmp/hyprmasq-hm-i-x-active.log",
        "/tmp/hyprmasq-x-xev.log",
        "/tmp/hyprmasq-x-cover.log",
        "/tmp/hyprmasq-satellite.log",
        "/tmp/hyprmasq-m-probe.log",
        "/tmp/hyprmasq-m-a-server.log",
        "/tmp/hyprmasq-m-b-server.log",
        "/tmp/hyprmasq-m-c-server.log",
    ]:
        if machine.execute(f"test -e {artifact}")[0] == 0:
            machine.copy_from_machine(artifact)
