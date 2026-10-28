with subtest("same-client popup, move, resize and drag protections"):
    focus_workspace(10)
    spawn("hyprmasq-focus-probe guard", "focus-probe")
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
    # The hidden window shares its connection with the focused one, so its drag cannot run privately either: the
    # cancel is counted for its socket, with the reason.
    drag = window_state(client("hm-guard-hidden")["address"])["drag"]
    assert drag == {"id": 1, "phase": "cancelled", "private": False, "reason": "shared_focus", "started": 0, "finished": 0, "cancelled": 1, "unconfirmed": 0,
                    "ended": [{"id": 1, "phase": "cancelled", "reason": "shared_focus"}]}, drag
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


def wait_dnd(log, event, start=0, timeout=30):
    try:
        wait_for(f"{log}: {event}", lambda: any(e == event or e.startswith(event + " ") for e in dnd_events(log, start)), timeout=timeout)
    except AssertionError:
        machine.log(machine.succeed(f"cat /tmp/hyprmasq-{log}.log"))
        raise
    return dnd_events(log, start)


def drag_record(address):
    return window_state(address)["drag"]


def finished_record(n, **counts):
    ended = [{"id": i, "phase": "finished", "reason": None} for i in range(1, n + 1)]
    return {"id": n, "phase": "finished", "private": True, "reason": None, "started": n, "finished": n, "cancelled": 0, "unconfirmed": 0, "ended": ended} | counts


def cancelled_record(n, reason, private=True, **counts):
    ended = [{"id": i, "phase": "cancelled", "reason": reason} for i in range(1, n + 1)]
    return {"id": n, "phase": "cancelled", "private": private, "reason": reason, "started": n if private else 0, "finished": 0, "cancelled": n,
            "unconfirmed": 0, "ended": ended} | counts


# A drag from the probe's surface at 50,100 over the subsurface, held at 300,100 until the sender's sleep ends.
dnd_path_to_sub = ["move", 50, 100, "down", "left", "move", 90, 100, "move", 150, 100, "move", 250, 100, "move", 300, 100]

with subtest("a hidden window's own drag-and-drop runs privately, and its data goes through file descriptors"):
    # The drag starts from an emulated press while the user's terminal keeps the focus, enters the subsurface with a
    # new offer, and drops there. The destination reads the data from a pipe whose other end the source writes to,
    # and the compositor only passes the descriptors on.
    address, path = dnd_probe("dnd-full", "hm-dnd-full", "icon")
    # Switching workspaces may move the pointer, which nothing does from here on.
    expected, pointer = snapshot(), cursor()
    state = window_state(address)
    assert state["capabilities"]["privateDnd"] and state["capabilities"]["hold"], state
    send(path, *dnd_path_to_sub, "up", "left")
    events = wait_dnd("dnd-full", "source finished")
    assert f"payload {dnd_payload}" in events and "finish" in events and "source cancelled" not in events, events
    # The press's serial started it, and the window's pointer left without a release of the button.
    start = next(i for i, e in enumerate(events) if e.startswith("drag start "))
    press = max(i for i, e in enumerate(events[:start]) if e == "button 272 1")
    assert "pointer leave hm-dnd-full" in events[start:], events
    assert "button 272 0" not in events[press:], events
    # An offer for each surface: the window's at the press's point, then the subsurface's after a leave.
    enters = [e.split() for e in events if e.startswith("drag enter ")]
    assert [e[2] for e in enters] == ["hm-dnd-full", "sub"] and enters[0][3] != enters[1][3], enters
    assert enters[0][4:] == ["50", "100"], enters
    sub_offer = enters[1][3]
    assert events.index("drag leave") < events.index(" ".join(enters[1])), events
    # Move, which the destination prefers, both sides allow, and both learn.
    assert f"offer {sub_offer} action 2" in events and "source action 2" in events and "source target text/plain" in events, events
    drop = events.index("drag drop")
    assert events[drop + 1] == f"receive {sub_offer}" and events.index("source send text/plain") > drop, events
    assert events.index("source drop performed") > drop and events.index("source finished") > events.index("finish"), events
    # The icon gets frame callbacks while it is hidden.
    assert events.count("icon frame") >= 2, events
    assert drag_record(address) == finished_record(1), drag_record(address)
    # The finish drew the window green, which a capture shows, and the user's focus and pointer stayed.
    image, _ = shot(address, "dnd-full")
    assert near(pixels(image, (100, 100))[0], (0x22, 0xAA, 0x44)), pixels(image, (100, 100))
    assert snapshot() == expected and cursor() == pointer, (snapshot(), cursor())
    # After the drop, the window's pointer takes motion again.
    start = len(probe_events("dnd-full"))
    send(path, "move", 60, 60)
    wait_dnd("dnd-full", "pointer enter hm-dnd-full", start)
    close_probe(address)

with subtest("drags in quick succession each run privately, also when the app starts each one at a frame callback"):
    # As Firefox, the probe starts each drag only at the first frame callback after the pointer moved 8 pixels from
    # its press, while the drag's motion comes every 10 ms, which must not keep the window from its frame callbacks.
    # It confirms each drop 300 ms after its data, when the next drag has already started, which a drop that waits
    # for its confirmation must not refuse.
    address, path = dnd_probe("dnd-burst", "hm-dnd-burst", "frame-first", "finish=soon")
    expected, pointer = snapshot(), cursor()
    burst = []
    for y in (30, 70, 110, 150):
        burst += ["path", 2, 50, y, 300, y]
    send(path, *burst)
    wait_for("four finishes", lambda: dnd_events("dnd-burst").count("source finished") == 4, timeout=30)
    events = dnd_events("dnd-burst")
    assert events.count("drag threshold") == 4 and events.count("press frame") == 4, events
    assert len([e for e in events if e.startswith("drag start ")]) == 4, events
    assert events.count(f"payload {dnd_payload}") == 4 and "source cancelled" not in events, events
    # A drag started while the drop before it still waited for its finish.
    starts = [i for i, e in enumerate(events) if e.startswith("drag start ")]
    finishes = [i for i, e in enumerate(events) if e == "finish"]
    assert any(start < finish for start, finish in zip(starts[1:], finishes)), events
    record = drag_record(address)
    assert {k: record[k] for k in ("id", "phase", "started", "finished", "cancelled", "unconfirmed")} == {
        "id": 4, "phase": "finished", "started": 4, "finished": 4, "cancelled": 0, "unconfirmed": 0}, record
    assert [(e["id"], e["phase"]) for e in record["ended"]] == [(1, "finished"), (2, "finished"), (3, "finished"), (4, "finished")], record
    assert snapshot() == expected and cursor() == pointer, (snapshot(), cursor())
    close_probe(address)

with subtest("a private drag that the destination refuses, or that ends outside the window, is cancelled"):
    expected = snapshot()
    for options, reason, end in [("accept=none", "rejected", 150), ("actions=ask", "rejected", 150), ("", "no_target", 500)]:
        address, path = dnd_probe("dnd-refused", "hm-dnd-refused", options)
        send(path, "move", 50, 100, "down", "left", "move", 90, 100, "move", end, 100, "up", "left")
        events = wait_dnd("dnd-refused", "source cancelled")
        assert "drag drop" not in events and "source finished" not in events and "payload" not in " ".join(events), (options, events)
        assert drag_record(address) == cancelled_record(1, reason), (options, drag_record(address))
        if options == "actions=ask":
            # Ask is not offered, so neither end hears of an action but none.
            assert not any(e.startswith("source action ") and e != "source action 0" for e in events), events
        close_probe(address)
    assert snapshot() == expected

with subtest("only the serial of a held emulated press starts a private drag"):
    # A drag request with the serial of the pointer's enter, of a press whose button is up, or a made-up one is
    # cancelled before anything is offered, and counted with its reason.
    for options in ["serial=enter", "serial=old", "serial=forged"]:
        address, path = dnd_probe("dnd-serial", "hm-dnd-serial", options)
        if options == "serial=old":
            send(path, "move", 50, 100, "click")
        send(path, "move", 50, 100, "down", "left", "move", 90, 100, "move", 150, 100, "up", "left")
        events = wait_dnd("dnd-serial", "source cancelled")
        assert not any(e.startswith(("drag enter", "data offer")) for e in events), (options, events)
        assert drag_record(address) == cancelled_record(1, "serial", private=False), (options, drag_record(address))
        close_probe(address)

with subtest("a drag without a source runs within its app without offers, from a held emulated press only"):
    # The protocol lets a client drag within itself without a source, passing its data itself: it gets enters
    # without an offer, motion and leaves, and no drop. Hyprland's own handler cannot take such a drag.
    address, path = dnd_probe("dnd-nosource", "hm-dnd-nosource", "source=none")
    expected, pointer = snapshot(), cursor()
    send(path, *dnd_path_to_sub, "up", "left")
    wait_for("the drag's end", lambda: drag_record(address)["phase"] == "finished", timeout=20)
    events = dnd_events("dnd-nosource")
    enters = [e.split() for e in events if e.startswith("drag enter ")]
    assert [e[2:4] for e in enters] == [["hm-dnd-nosource", "0"], ["sub", "0"]], events
    assert "drag leave" in events[events.index(" ".join(enters[1])) :] and "drag drop" not in events, events
    assert not any(e.startswith("data offer") for e in events), events
    assert drag_record(address) == finished_record(1), drag_record(address)
    assert snapshot() == expected and cursor() == pointer, (snapshot(), cursor())
    close_probe(address)
    # One with a made-up serial is refused and counted, though there is no source to tell.
    address, path = dnd_probe("dnd-nosource-forged", "hm-dnd-nosource-forged", "source=none", "serial=forged")
    send(path, "move", 50, 100, "down", "left", "move", 90, 100, "move", 150, 100, "up", "left")
    wait_for("the refusal", lambda: drag_record(address)["id"] == 1, timeout=20)
    assert drag_record(address) == cancelled_record(1, "serial", private=False), drag_record(address)
    assert not any(e.startswith("drag enter") for e in dnd_events("dnd-nosource-forged")), dnd_events("dnd-nosource-forged")
    close_probe(address)

with subtest("a private drag ends when its source, data device, window, socket or sender goes away"):
    expected = snapshot()

    def held_drag(log, title):
        address, path = dnd_probe(log, title)
        run_input_background(f"hm-{log}", path, *dnd_path_to_sub, "sleep", 6000, "up", "left")
        wait_dnd(log, "drag enter sub")
        return address, path

    for signal, reason in [("USR1", "source_gone"), ("USR2", "device_gone")]:
        address, _ = held_drag("dnd-gone", "hm-dnd-gone")
        machine.succeed(f"pkill -{signal} -f '[h]yprmasq-dnd-probe hm-dnd-gone'")
        wait_for(reason, lambda: drag_record(address)["phase"] == "cancelled", timeout=10)
        assert drag_record(address) == cancelled_record(1, reason), drag_record(address)
        events = dnd_events("dnd-gone")
        assert "drag drop" not in events, events
        if reason == "device_gone":
            assert "source cancelled" in events, events
        input_result("hm-dnd-gone")
        close_probe(address)
    # The sender's exit before the drop.
    address, _ = held_drag("dnd-gone", "hm-dnd-gone")
    machine.succeed("pkill -KILL -x hyprmasq-input")
    wait_for("sender_gone", lambda: drag_record(address)["phase"] == "cancelled", timeout=10)
    assert drag_record(address) == cancelled_record(1, "sender_gone"), drag_record(address)
    assert "source cancelled" in wait_dnd("dnd-gone", "source cancelled")
    close_probe(address)
    # The socket's and the window's close, after which there is no record to read.
    address, _ = held_drag("dnd-gone", "hm-dnd-gone")
    hypr(f"hyprmasq eis close {address}")
    events = wait_dnd("dnd-gone", "source cancelled")
    assert "drag leave" in events and "drag drop" not in events, events
    machine.succeed("pkill -x hyprmasq-input || true")
    close_probe(address)
    address, _ = held_drag("dnd-gone", "hm-dnd-gone")
    machine.succeed("pkill -HUP -f '[h]yprmasq-dnd-probe hm-dnd-gone'")
    events = wait_dnd("dnd-gone", "source cancelled")
    assert "window destroyed" in events and "drag drop" not in events, events
    machine.succeed("pkill -x hyprmasq-input || true")
    close_probe(address)
    assert snapshot() == expected

with subtest("a dropped private drag outlives its sender until the destination finishes, or it gives up"):
    # The destination finishes 1.5 s after the data, while the sender has long exited.
    address, path = dnd_probe("dnd-late", "hm-dnd-late", "finish=late")
    send(path, *dnd_path_to_sub, "up", "left")
    wait_dnd("dnd-late", "payload")
    assert drag_record(address)["phase"] == "dropped", drag_record(address)
    events = wait_dnd("dnd-late", "source finished")
    assert "source cancelled" not in events, events
    assert drag_record(address) == finished_record(1), drag_record(address)
    close_probe(address)
    # One that never finishes is given up after 10 s: its outcome is unknown, and the source keeps its data.
    address, path = dnd_probe("dnd-never", "hm-dnd-never", "finish=never")
    send(path, *dnd_path_to_sub, "up", "left")
    wait_dnd("dnd-never", "payload")
    assert drag_record(address)["phase"] == "dropped", drag_record(address)
    events = wait_dnd("dnd-never", "source cancelled", timeout=20)
    assert "source finished" not in events, events
    assert drag_record(address) == {"id": 1, "phase": "unconfirmed", "private": True, "reason": "timeout", "started": 1, "finished": 0, "cancelled": 0,
                                    "unconfirmed": 1, "ended": [{"id": 1, "phase": "unconfirmed", "reason": "timeout"}]}, drag_record(address)
    close_probe(address)

with subtest("the user's real input to the app of a private drag cancels the drag first"):
    # The probe's second toplevel is the user's, beside their terminal, without their focus or pointer. Their click on
    # it, and their focus moving to it, each end a private drag before the app gets the input.
    focus_workspace(4)
    expected, pointer = snapshot(), cursor()
    address, path = dnd_probe("dnd-user", "hm-dnd-user-agent", "user", "hm-dnd-user", workspace=82)
    dispatch('hl.dsp.window.move({ window = "title:^hm-dnd-user$", workspace = "4", follow = false })')
    dispatch(f'hl.dsp.focus({{ window = "address:{expected[1]}" }})')
    wait_for("user's terminal focused", lambda: snapshot() == expected)
    user = client("hm-dnd-user")
    assert user["workspace"]["id"] == 4, user
    # The user's pointer rests on their terminal, and goes to a point of the probe's surface, which is 400×200
    # whatever size its window gets.
    terminal = next(c for c in clients() if c["address"] == expected[1])
    pointer = (terminal["at"][0] + terminal["size"][0] // 2, terminal["at"][1] + terminal["size"][1] // 2)
    dispatch(f"hl.dsp.cursor.move({{ x = {pointer[0]}, y = {pointer[1]} }})")
    alice(f"{client_env} wlrctl pointer click left")
    wait_for("user's terminal focused", lambda: snapshot() == expected)
    center = (user["at"][0] + 50, user["at"][1] + 50)
    for n, take in enumerate(["pointer", "keyboard"], 1):
        start = len(probe_events("dnd-user"))
        run_input_background("hm-dnd-user", path, *dnd_path_to_sub, "sleep", 6000, "up", "left")
        wait_dnd("dnd-user", "drag enter sub", start)
        if take == "pointer":
            # The test's Hyprland moves no focus with motion alone, so the user clicks there, through the virtual
            # pointer, which is real input.
            dispatch(f"hl.dsp.cursor.move({{ x = {center[0]}, y = {center[1]} }})")
            alice(f"{client_env} wlrctl pointer click left")
            arrived = "pointer enter hm-dnd-user"
        else:
            dispatch('hl.dsp.focus({ window = "title:^hm-dnd-user$" })')
            arrived = "keyboard enter hm-dnd-user"
        events = wait_dnd("dnd-user", arrived, start)
        assert "source cancelled" in events and events.index("source cancelled") < events.index(arrived), events
        assert events.index("drag leave") < events.index(arrived) and "drag drop" not in events, events
        assert drag_record(address) == cancelled_record(n, "user_input"), drag_record(address)
        input_result("hm-dnd-user")
        # The user's input stays where it went: their click reaches the window, and its focus stays there.
        if take == "pointer":
            wait_for("the user's click", lambda: "button 272 1" in (e := dnd_events("dnd-user", start))[e.index(arrived) :])
            assert cursor() == center, cursor()
        wait_for("the user's window focused", lambda: snapshot()[1] == user["address"])
        dispatch(f'hl.dsp.focus({{ window = "address:{expected[1]}" }})')
        dispatch(f"hl.dsp.cursor.move({{ x = {pointer[0]}, y = {pointer[1]} }})")
        alice(f"{client_env} wlrctl pointer click left")
        wait_for("user's terminal focused", lambda: snapshot() == expected)
    close_probe(address)
    dispatch('hl.dsp.window.close({ window = "title:^hm-dnd-user$" })')
    wait_for("drag probe closed", lambda: client("hm-dnd-user") is None)

with subtest("the user's typing during a private drag arrives unchanged, with their focus and pointer"):
    # The user types through QEMU, a real keyboard, into a terminal that records it, while a drag in the probe on a
    # hidden workspace runs for seconds.
    focus_workspace(4)
    typist = foot("hm-dnd-typist", "sh -c 'cat > /tmp/hm-dnd-typed.txt'")
    dispatch(f'hl.dsp.focus({{ window = "address:{typist}" }})')
    wait_for("typist focused", lambda: snapshot() == (4, typist))
    address, path = dnd_probe("dnd-typing", "hm-dnd-typing")
    expected, pointer = snapshot(), cursor()
    assert expected == (4, typist), expected
    steps = []
    for x in range(60, 340, 4):
        steps += ["move", x, 100, "sleep", 250]
    run_input_background("hm-dnd-typing", path, "move", 50, 100, "down", "left", *steps, "up", "left")
    wait_dnd("dnd-typing", "drag enter hm-dnd-typing")
    typed_from = vm_now()
    # QEMU drops keys of the guest's terminal at 10 ms a key also without a drag, so they come slower.
    lines = [f"line {n} the quick brown fox jumps over the lazy dog" for n in range(6)]
    machine.send_chars("".join(line + "\n" for line in lines), delay=0.04)
    typed_until = vm_now()
    events = wait_dnd("dnd-typing", "source finished", timeout=90)
    assert f"payload {dnd_payload}" in events, events
    stamped = probe_events("dnd-typing")
    drop_at = next(t for t, e in stamped if e == "drag drop")
    assert drop_at > typed_until > typed_from, (drop_at, typed_from, typed_until)
    assert snapshot() == expected, snapshot()
    # cat writes what it reads at once.
    wait_for("the typing", lambda: machine.succeed("cat /tmp/hm-dnd-typed.txt") == "".join(line + "\n" for line in lines), timeout=20)
    dispatch(f'hl.dsp.window.close({{ window = "address:{typist}" }})')
    wait_for("typist closed", lambda: client("hm-dnd-typist") is None)
    assert cursor() == pointer, cursor()
    assert drag_record(address) == finished_record(1), drag_record(address)
    close_probe(address)
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("user's terminal focused", lambda: snapshot() == (4, visible))
