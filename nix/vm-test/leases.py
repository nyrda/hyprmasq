def last_input(events):
    return max(t for t, e in events if e.startswith(("motion ", "key ", "button ")))


lease_leaves = {"keyboard leave hm-p-lease", "pointer leave hm-p-lease"}


def wait_for_leaves(start, expected=lease_leaves, timeout=10):
    wait_for("leaves", lambda: expected <= {e for _, e in probe_events("p-lease", start)}, timeout=timeout)
    return probe_events("p-lease", start)


with subtest("a focus lease keeps the emulated focus between inputs"):
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
