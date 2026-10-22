import json
import random
import re
import shlex
import string
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


def wait_drawn(address, quiet_ms):
    """Waits until a window that just opened on the active workspace has drawn nothing for quiet_ms, as a terminal once
    its shell is up. The plugin's wait takes at least quiet_ms, and fails the test if the window draws on for 4.5 s."""
    reply = json.loads(hypr(f"-j hyprmasq wait {address} {quiet_ms} 4500"))
    assert reply["ok"] and reply["settled"], (address, reply)


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


def waiting_writers(count):
    """Waits until count screenshot writers wait for their pipes, and returns Hyprland's pid. A writer waits for a FIFO's
    reader, or for room in a pipe, in poll, on a thread of Hyprland named hyprmasq-job; waits on such threads are in
    futex. A fixed sleep can end before a slow guest gets there."""
    pid = machine.succeed(f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock").strip()
    polling = f"for t in /proc/{pid}/task/*; do [ \"$(cat $t/comm)\" = hyprmasq-job ] && read -r nr _ < $t/syscall && case $nr in 7|271) echo ${{t##*/}};; esac; done; true"
    deadline = time.monotonic() + 30
    while len(threads := machine.succeed(polling).split()) != count:
        if time.monotonic() > deadline:
            # hyprctl gets a bound, in case Hyprland is stuck.
            answers = shlex.quote(f"{ipc} hyprctl version")
            state = machine.succeed(f"grep . /proc/{pid}/task/*/comm; timeout 5 su - alice -c {answers} | head -1 || echo hyprctl got no answer")
            raise AssertionError(f"Timed out: {len(threads)} writers wait for a pipe, not {count}: {state}")
        time.sleep(0.25)
    return pid


def eis_entry(address):
    return next(e for e in json.loads(hypr("-j hyprmasq eis list"))["sockets"] if e["address"] == address)


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


def image_script(body, **values):
    """Runs body, Python with PIL's Image, in the VM with values as variables of those names, and returns what it prints."""
    script = "import json\nfrom PIL import Image\n" + "".join(f"{name} = {value!r}\n" for name, value in values.items()) + body
    return machine.succeed("python3 - <<'PY'\n" + script + "\nPY")


def assert_png(path, color):
    machine.copy_from_machine(path)
    image_script("""
im = Image.open(path).convert('RGB')
w, h = im.size
assert w >= 100 and h >= 100, im.size
def background(pixel):
    return all(abs(a - b) <= 10 for a, b in zip(pixel, expected))
assert background(im.getpixel((w // 2, h // 2))), im.getpixel((w // 2, h // 2))
# The terminal prints a white banner in its first row. A flipped screenshot fails both checks.
rows = [y for y in range(h) if any(not background(im.getpixel((x, y))) for x in range(0, w, 2))]
assert rows and max(rows) < h // 4, f"text rows {min(rows, default=None)}..{max(rows, default=None)} of {h}"
""", path=path, expected=color)


def capture_output(name, before=""):
    """Captures the whole monitor with grim, after the shell command `before`."""
    path = f"/tmp/hyprmasq-{name}.png"
    alice(f"{before} {client_env} grim {path}")
    machine.copy_from_machine(path)
    return path


def count_pixels(path, box, condition):
    """Counts pixels within box (x0, y0, x1, y1) whose r, g, b satisfy the Python expression condition."""
    return int(image_script(f"""
x0, y0, x1, y1 = box
im = Image.open(path).convert('RGB')
def matches(r, g, b):
    return {condition}
print(sum(
    matches(*im.getpixel((x, y)))
    for x in range(max(0, x0), min(im.width, x1))
    for y in range(max(0, y0), min(im.height, y1))
))
""", path=path, box=box).strip())


def changed_pixels(before, after, box):
    """How many pixels within box (x0, y0, x1, y1) differ between the images before and after."""
    return int(image_script("""
x0, y0, x1, y1 = box
a, b = Image.open(before).convert('RGB'), Image.open(after).convert('RGB')
print(sum(a.getpixel((x, y)) != b.getpixel((x, y)) for x in range(x0, x1) for y in range(y0, y1)))
""", before=before, after=after, box=box).strip())


def pixels(path, *points, raw=None):
    """The RGBA values of the image at the points, and its size last. With raw, a width and height, the file holds raw
    RGBA pixels, which must be that many."""
    return [tuple(v) for v in json.loads(image_script("""
if raw:
    data = open(path, 'rb').read()
    assert len(data) == raw[0] * raw[1] * 4, len(data)
    im = Image.frombytes('RGBA', raw, data)
else:
    im = Image.open(path).convert('RGBA')
print(json.dumps([im.getpixel(p) for p in points] + [im.size]))
""", path=path, points=list(points), raw=raw))]


def raw_pixels(path, width, height, *points):
    """The RGBA values of a raw image at the points, after checking that the file holds width x height pixels."""
    return pixels(path, *points, raw=(width, height))[:-1]


def color_box(path, rgb):
    """The bounding box (x0, y0, x1, y1) of the opaque pixels of the image near rgb, or None."""
    return json.loads(image_script("""
im = Image.open(path).convert('RGBA')
found = [(x, y) for y in range(im.height) for x in range(im.width)
         if im.getpixel((x, y))[3] > 245 and all(abs(a - b) <= 12 for a, b in zip(im.getpixel((x, y)), rgb))]
print(json.dumps([min(x for x, _ in found), min(y for _, y in found), max(x for x, _ in found) + 1, max(y for _, y in found) + 1] if found else None))
""", path=path, rgb=rgb))


def white_run(path):
    """The longest run of white pixels in a row of the image."""
    return int(image_script("""
im = Image.open(path).convert('RGB')
pixels, longest = im.load(), 0
for y in range(im.height):
    run = 0
    for x in range(im.width):
        run = run + 1 if min(pixels[x, y]) > 200 else 0
        longest = max(longest, run)
print(longest)
""", path=path).strip())


def keymap(name):
    return any(name in k["active_keymap"] for k in json.loads(hypr("-j devices"))["keyboards"])


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


def window_state(address):
    return json.loads(hypr(f"-j hyprmasq state {address}"))


# hyprctl gives up on a reply after 5 seconds. This client writes a request to Hyprland's socket itself and waits
# as long as the reply takes, then moves it into place at once.
socket_client = "/etc/hyprmasq-test/hypr-request.py"


def background_reply(output, timeout=30):
    machine.wait_until_succeeds(f"test -s {output}", timeout=timeout)
    return json.loads(machine.succeed(f"cat {output}"))


def shot(address, name, arguments=""):
    path = f"/tmp/hyprmasq-p-{name}.png"
    reply = json.loads(hypr(f"-j hyprmasq screenshot {address} {path} {arguments}"))
    assert reply["ok"], (arguments, reply)
    machine.copy_from_machine(path)
    return path, reply


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


def output_mode(width, height):
    """Gives the test's output another size, at scale 1."""
    hypr("eval " + shlex.quote(f'hl.monitor({{ output = "HYPRMASQ-TEST", mode = "{width}x{height}@60", position = "0x0", scale = "1" }})'))
    wait_for(f"a {width}x{height} output", lambda: [(m["width"], m["height"]) for m in json.loads(hypr("-j monitors"))] == [(width, height)])


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


def monitor_scale(scale, width):
    hypr("eval " + shlex.quote(
        f'hl.monitor({{ output = "HYPRMASQ-TEST", mode = "640x480@60", position = "0x0", scale = "{scale}" }})'
    ))
    wait_for(f"scale {scale}", lambda: client("hm-green")["size"][0] == width)
    time.sleep(0.5)


def frames_probe(title, frames, workspace):
    """Starts focus-probe's frames mode on a hidden workspace, never shown, and returns its address once it drew its
    first frame. It draws only in answer to frame callbacks, as Firefox and Thunderbird do on a hidden workspace: a
    blank frame in the color of the other modes, then its orange content at the frames-th callback, or never with 0.
    Hyprland may answer its first frame callback as it maps the window."""
    command = f"WAYLAND_DEBUG=client exec hyprmasq-focus-probe frames {title} {frames} > /tmp/hyprmasq-{title}.log 2>&1"
    address = alice(f"{ipc} hyprmasq-launch --workspace {workspace} -- sh -c {shlex.quote(command)}").strip()
    machine.wait_until_succeeds(f"grep -q '^drew blank' /tmp/hyprmasq-{title}.log", timeout=10)
    return address


def frames_got(title):
    """How many frame callbacks the frames probe got."""
    return int(machine.succeed(f"grep -c '^frame ' /tmp/hyprmasq-{title}.log || true").strip())


content_rgb = (0xDD, 0x77, 0x11)


def animations(on):
    """Turns on slow window animations, 10 seconds and linear, in which windows pop in from half their size, with
    nothing else animated, or turns all animations off again, as the test's configuration has them."""
    if on:
        code = ('hl.config({ animations = { enabled = true } }) hl.animation({ leaf = "global", enabled = false }) '
                'hl.animation({ leaf = "windows", enabled = true, speed = 100, bezier = "linear", style = "popin 50%" })')
    else:
        code = "hl.config({ animations = { enabled = false } })"
    assert hypr("eval " + shlex.quote(code)) == "ok"


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


def watch_events(log):
    """Logs Hyprland's IPC events, one per line, to /tmp/hyprmasq-{log}.log until the test ends."""
    machine.succeed(
        "cat > /tmp/hm-events.py <<'EOF'\nimport socket, sys\ns = socket.socket(socket.AF_UNIX)\ns.connect(sys.argv[1])\n"
        "for line in s.makefile():\n    print(line.strip(), flush=True)\nEOF"
    )
    spawn(f"python3 /tmp/hm-events.py {runtime}/hypr/{instance}/.socket2.sock", log)


def urgent(log, address):
    return f"urgent>>{address.removeprefix('0x')}" in machine.succeed(f"cat /tmp/hyprmasq-{log}.log").splitlines()


letter_codes = dict(zip("qwertyuiopasdfghjklzxcvbnm", [*range(16, 26), *range(30, 39), *range(44, 51)]))


def keys_of(events, title):
    """The keys that a focus probe's toplevel got pressed, in order."""
    return [int(e.split()[2]) for _, e in events if e.startswith(f"key {title} ") and e.endswith(" 1")]


def shortcut_probe(log, user_title, agent_title, user_workspace, agent_workspace, ms=150):
    """A shortcuts probe as a browser of the user's: their window focused on user_workspace, and the agent's on
    agent_workspace, hidden, with a socket. Its shortcuts run ms, by default 150 ms, after their key, as Chromium's do
    once the page has let the key go by."""
    focus_workspace(user_workspace)
    spawn(f"hyprmasq-focus-probe shortcuts {user_title} {agent_title} {ms}", log)
    wait_for("shortcut probe", lambda: client(user_title) and client(agent_title))
    dispatch(f'hl.dsp.window.move({{ window = "title:^{agent_title}$", workspace = "{agent_workspace}", follow = false }})')
    user, agent = client(user_title)["address"], client(agent_title)["address"]
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (user_workspace, user))
    return user, agent


# The drag probe: one toplevel, blue, with a subsurface over its right half, which drags text/plain from itself to
# itself. See nix/dnd-probe.c.
dnd_payload = "hyprmasq-dnd-payload"


def dnd_probe(log, title, *options, workspace=81):
    """A drag probe on a hidden workspace with a socket, while the user's terminal on workspace 4 keeps the focus."""
    focus_workspace(workspace)
    spawn(f"hyprmasq-dnd-probe {title} " + " ".join(options), log)
    wait_for(title, lambda: client(title) is not None)
    machine.wait_until_succeeds(f"grep -q ' ready$' /tmp/hyprmasq-{log}.log", timeout=30)
    focus_workspace(4)
    address = client(title)["address"]
    return address, open_socket(address)


def dnd_events(log, start=0):
    return [e for _, e in probe_events(log, start)]


def cursor():
    position = json.loads(hypr("-j cursorpos"))
    return position["x"], position["y"]


def close_probe(address):
    machine.succeed("pkill -f '[h]yprmasq-dnd-probe' || true")
    wait_for("drag probe closed", lambda: all(c["address"] != address for c in clients()))


def clip_probe(title, workspace):
    """A clipboard probe on a hidden workspace with a socket. It had the focus while it started there."""
    focus_workspace(workspace)
    spawn(f"hyprmasq-clip-probe {title}", title)
    wait_for(title, lambda: client(title) is not None)
    machine.wait_until_succeeds(f"grep -q ' ready$' /tmp/hyprmasq-{title}.log", timeout=30)
    address = client(title)["address"]
    return address, open_socket(address)


def clip_events(title, start=0):
    return [e for _, e in probe_events(title, start)]


def wait_clip(title, start, *expected, timeout=15):
    wait_for(f"{title}: {expected}", lambda: all(e in clip_events(title, start) for e in expected), timeout=timeout)
    return clip_events(title, start)


def user_selections():
    """The user's clipboard and primary selection, as a clipboard manager reads them through data-control."""
    paste = lambda flag="": alice(f"{client_env} timeout 5 wl-paste -n {flag} 2>&1 || true").strip()
    return paste(), paste("--primary")


def user_copies(clipboard, primary):
    """The user copies and selects through data-control, as wl-copy and clipboard managers do."""
    alice(f"{client_env} wl-copy {clipboard}")
    alice(f"{client_env} wl-copy --primary {primary}")
    wait_for("the user's selections", lambda: user_selections() == (clipboard, primary), timeout=10)


# hyprmasq-mcp end to end, on workspaces 50 to 59. A bridge runs each server in the VM as alice with Hyprland's
# environment and passes JSON-RPC messages between its stdio and the test, which reaches the bridge through a Unix
# socket. It stamps them with CLOCK_MONOTONIC, as focus-probe stamps its events, and saves the images of results.
mcp_bridge, mcp_client = "/etc/hyprmasq-test/mcp-bridge.py", "/etc/hyprmasq-test/mcp-client.py"


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


def wait_for_hyprland():
    """Waits for Hyprland to answer, with its headless output and the plugin loaded."""
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


def save_logs():
    """Prints Hyprland's log and copies the logs of the areas that ran into the test's output, also after a failure."""
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
        "/tmp/hyprmasq-x-combo.log",
        "/tmp/hyprmasq-x-menu-cover.log",
        "/tmp/hyprmasq-x-select.log",
        "/tmp/hyprmasq-m-probe.log",
        "/tmp/hyprmasq-o-agent.log",
        "/tmp/hyprmasq-o-browser.log",
        "/tmp/hyprmasq-m-a-server.log",
        "/tmp/hyprmasq-m-b-server.log",
        "/tmp/hyprmasq-m-c-server.log",
        "/tmp/hyprmasq-m-o-server.log",
        "/tmp/hyprmasq-s-qt.log",
        "/tmp/hyprmasq-s-raw.log",
        "/tmp/hyprmasq-s-old.log",
    ]:
        if machine.execute(f"test -e {artifact}")[0] == 0:
            machine.copy_from_machine(artifact)
