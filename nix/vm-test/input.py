# hyprmasq-input's pacing, actions, JSON results and cleanup, on workspaces 40 to 49.


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

    # Aliases and keysym names ignore case, and a letter is its key without Shift. A key without modifiers is held 5 ms
    # and repeats 10 ms apart, as typed keys do, and a chord with modifiers repeats 15 ms apart. Actions are 50 ms
    # apart.
    offset = log_size("hm-i-wev")
    chords = ["ctrl+l", "Enter", "return", "esc", "ESCAPE", "del", "f5", "A"]
    args = [word for chord in chords for word in ("keys", chord)] + ["keys", "tab", 3, "keys", "shift+tab", 2]
    assert input_json(input_path, *args) == (0, complete(len(chords) + 2))
    events = wait_wev(offset, {"key"}, lambda e: len(e) >= 32)
    assert [int(e[2]) for e in events if e[3] == 1] == [29, 38, 28, 28, 1, 1, 111, 63, 30, 15, 15, 15, 42, 15, 42, 15], events
    assert events[4][1] - events[3][1] >= 45, events
    assert all(events[i + 1][1] - events[i][1] >= 3 for i in (18, 20, 22)), events
    assert all(events[i + 1][1] - events[i][1] >= 8 for i in (19, 21)), events
    assert events[28][1] - events[27][1] >= 12, events
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

with subtest("hyprmasq-input waits at a sync for its caller's answer, at least MIN and at most MAX"):

    def synced(feed, *sync):
        """Sends a, a sync and b with feed piped into the sender, and returns its stdout lines and the ms from the press
        of a to that of b."""
        offset = log_size("hm-i-wev")
        command = f"{feed} | hyprmasq-input --json --socket {shlex.quote(input_path)} keys a sync {' '.join(map(str, sync))} keys b"
        lines = alice(command).splitlines()
        presses = [e[1] for e in wait_wev(offset, {"key"}, lambda e: len(e) >= 4) if e[3] == 1]
        return lines, presses[1] - presses[0]

    # The sync prints the number of actions before it, which the plugin has processed, before the result. With its
    # stdin closed, it waits for no answer, only its MIN.
    lines, gap = synced("true", 300, 2000)
    assert lines[0] == '{"sync":1}' and json.loads(lines[1]) == complete(3), lines
    assert 300 <= gap < 600, gap
    # Without an answer, it goes on after MAX.
    lines, gap = synced("sleep 3", 0, 400)
    assert json.loads(lines[-1])["delivery"] == "complete" and 400 <= gap < 800, (lines, gap)
    # An answer with another number, as one to an earlier sync that came late, does not count.
    lines, gap = synced("(sleep 0.3; echo 0; sleep 0.3; echo 1; sleep 3)", 0, 3000)
    assert json.loads(lines[-1])["delivery"] == "complete" and 450 <= gap < 1500, (lines, gap)
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

with subtest("CLI help, versions and argument errors"):
    # Run installed tools without a compositor connection. Capture streams separately so that
    # help/version and errors cannot accidentally move between stdout and stderr.
    alice(f"python3 /etc/hyprmasq-test/cli-checks.py {shlex.quote(expected_version)}")

with subtest("input refuses held-button overflow and releases its presses"):
    before = snapshot()
    held_window = alice(f"{ipc} hyprmasq-launch --workspace hm-cli-held -- foot -T hm-cli-held sleep 900").strip()
    held_path = open_socket(held_window)

    def held_input(buttons):
        return input_json(held_path, *(word for code in buttons for word in ("down", code)), options="--no-pace")

    # Repeated presses at capacity need no new tracking entry. A new 65th button fails.
    status, reply = held_input([*range(0x110, 0x150), 0x110])
    assert status == 0 and reply["done"] == 65, (status, reply)
    status, reply = held_input(range(0x110, 0x151))
    assert status == 1 and reply["error"]["code"] == "too_many_held", (status, reply)
    assert reply["done"] == 64 and reply["failedIndex"] == 64 and reply["delivery"] == "partial", reply
    # All tracked presses were released on failure; a later batch can use the socket.
    status, reply = held_input([0x110])
    assert status == 0 and reply["done"] == 1, (status, reply)
    assert snapshot() == before
    dispatch(f'hl.dsp.window.close({{ window = "address:{held_window}" }})')
    wait_for("held-button test window closed", lambda: client("hm-cli-held") is None)
