with subtest("a click keeps its modifiers, and a window of the connection with the real focus gives them back after"):
    # Input to a window of the connection that has the real keyboard focus keeps that focus while the sender holds
    # keys, and then hands it back with the user's modifiers. The user's own input to the connection takes it back at
    # once, which would leave a click with the user's modifiers: the window's missing hold capability tells that.
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
    assert modifiers == 4, events
    # The focused window gets the keyboard back, with the user's modifiers.
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-mods.log | grep -q 'keyboard enter hm-p-mods-focused'", timeout=10)
    events = [line.split(" ", 1)[1] for line in log_since("p-mods", offset).splitlines() if line[:1].isdigit()]
    back = events.index("keyboard enter hm-p-mods-focused")
    assert events[back + 1] == "modifiers hm-p-mods-focused 0", events
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

with subtest("a shortcut takes effect in a hidden window of the connection with the real focus, and not in the user's"):
    # As Chromium, the probe takes its toplevel with the keyboard focus for the active one, and runs a shortcut only if
    # that is still active when its time comes. The hidden window keeps the connection's keyboard focus until the
    # shortcut has settled, and then gives it back to the user's window, whose keys before and after reach it alone.
    sc_user, sc_agent = shortcut_probe("sc", "hm-sc-user", "hm-sc-agent", 65, 66)
    sc_path = open_socket(sc_agent)
    state = window_state(sc_agent)
    assert not state["capabilities"]["hold"] and state["interrupted"] == 0, state
    start = len(probe_events("sc"))
    machine.send_chars("ab", delay=0.05)
    wait_for("the user's keys", lambda: keys_of(probe_events("sc", start), "hm-sc-user") == [30, 48], timeout=10)
    send(sc_path, "keys", "ctrl+l")
    wait_for("the shortcut", lambda: any(e.startswith("shortcut ") for _, e in probe_events("sc", start)), timeout=10)

    def returned():
        events = probe_events("sc", start)
        last = max(t for t, e in events if e.startswith("key hm-sc-agent "))
        return next((t - last for t, e in events if e == "keyboard enter hm-sc-user" and t > last), None)

    wait_for("the keyboard back in the user's window", lambda: returned() is not None, timeout=10)
    # It stays a quarter of a second after the last key.
    assert returned() >= 200, probe_events("sc", start)
    machine.send_chars("cd", delay=0.05)
    wait_for("the user's keys after", lambda: len(keys_of(probe_events("sc", start), "hm-sc-user")) == 4, timeout=10)
    events = probe_events("sc", start)
    assert [e for _, e in events if e.startswith("shortcut ")] == ["shortcut hm-sc-agent 4 38"], events
    assert keys_of(events, "hm-sc-agent") == [29, 38] and keys_of(events, "hm-sc-user") == [30, 48, 46, 32], events
    assert window_state(sc_agent)["interrupted"] == 0
    assert snapshot() == (65, sc_user)

with subtest("the user's typing beside a shortcut takes the keyboard back first, and the plugin counts it"):
    # The user types into their window while the hidden window's shortcut is underway. Each of their keys takes the
    # connection's keyboard focus back before Hyprland sends it, so it reaches their window. A shortcut whose toplevel
    # is no longer active when its time comes does nothing, and the plugin counts the interruption. Whether a key of
    # the user's comes within the shortcut's 150 ms depends on timing, so the chord is sent until one does.
    # The user's text has an l, which must not reach their window with the hidden window's Ctrl.
    user_text = "qwertyuioplzxcvbnm"
    codes = [letter_codes[c] for c in user_text]
    for attempt in range(5):
        before = window_state(sc_agent)["interrupted"]
        start = len(probe_events("sc"))
        spawn("sh -c " + shlex.quote(f"sleep 0.3; hyprmasq-input --socket {sc_path} keys ctrl+l"), "sc-sender")
        machine.send_chars(user_text, delay=0.03)
        wait_for("the shortcut", lambda: any(e.startswith("shortcut ") for _, e in probe_events("sc", start)), timeout=10)
        wait_for("the user's keys", lambda: len(keys_of(probe_events("sc", start), "hm-sc-user")) == len(codes), timeout=10)
        events = probe_events("sc", start)
        assert keys_of(events, "hm-sc-user") == codes and keys_of(events, "hm-sc-agent") == [29, 38], events
        shortcuts = [e for _, e in events if e.startswith("shortcut ")]
        assert shortcuts in (["shortcut hm-sc-agent 4 38"], ["shortcut hm-sc-agent 4 38 inactive"]), events
        if shortcuts[0].endswith(" inactive"):
            wait_for("the count", lambda: window_state(sc_agent)["interrupted"] > before, timeout=5)
            break
    else:
        raise AssertionError("the user's typing never came within a shortcut")
    assert snapshot() == (65, sc_user)
    for title in ["hm-sc-agent", "hm-sc-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("shortcut probe closed", lambda: client("hm-sc-agent") is None and client("hm-sc-user") is None)

with subtest("an emulated click gives a hidden window the keyboard focus before its button, and a lease keeps it"):
    # As Hyprland's click to focus does for a real click: apps such as LibreOffice act on the keyboard focus that a
    # click brings, and would otherwise get it only with the first key, after the click.
    focus_workspace(69)
    spawn("hyprmasq-focus-probe focus hm-cf-hidden", "cf-hidden")
    wait_for("click focus probe", lambda: client("hm-cf-hidden") is not None)
    focus_workspace(4)
    before_focus = snapshot()
    cf = client("hm-cf-hidden")["address"]
    cf_path = open_socket(cf)

    def cf_events(start, until):
        wait_for(until, lambda: until in [e for _, e in probe_events("cf-hidden", start)], timeout=10)
        return [e for _, e in probe_events("cf-hidden", start)]

    start = len(probe_events("cf-hidden"))
    send(cf_path, "move", 30, 30, "click")
    events = cf_events(start, "keyboard leave hm-cf-hidden")
    # The motion before the press brings none, and without a lease the focus ends with the sender.
    motion, enter = events.index("motion hm-cf-hidden 30 30"), events.index("keyboard enter hm-cf-hidden")
    assert motion < enter and events[enter + 1] == "modifiers hm-cf-hidden 0" and events[enter + 2] == "button hm-cf-hidden 1", events
    assert events.count("keyboard enter hm-cf-hidden") == 1 and events.index("button hm-cf-hidden 0") < events.index("keyboard leave hm-cf-hidden"), events
    # A lease keeps it until the keys of a later sender, which need no enter of their own.
    assert lease(cf, 10000, "hm-cf-owner")["held"]
    start = len(probe_events("cf-hidden"))
    send(cf_path, "move", 40, 40, "click")
    time.sleep(1.5)
    send(cf_path, "key", 48)
    events = cf_events(start, "key hm-cf-hidden 48 0")
    assert events.index("keyboard enter hm-cf-hidden") < events.index("button hm-cf-hidden 1") < events.index("key hm-cf-hidden 48 1"), events
    assert events.count("keyboard enter hm-cf-hidden") == 1 and not any("leave" in e for e in events), events
    assert lease(cf, "release", "hm-cf-owner")["released"]
    cf_events(start, "keyboard leave hm-cf-hidden")
    assert snapshot() == before_focus
    dispatch('hl.dsp.window.close({ window = "title:^hm-cf-hidden$" })')
    wait_for("click focus probe closed", lambda: client("hm-cf-hidden") is None)

with subtest("an emulated click leaves the keyboard focus of the connection with the user's focused window alone"):
    # The agent's window and the user's focused one share a connection, which has its keyboard focus on one surface
    # at a time, so a click that brought it would take it from the user's window. Only the agent's keys take it, and
    # the user's typing during the agent's clicks reaches the user's window alone.
    focus_workspace(70)
    spawn("hyprmasq-focus-probe focus hm-cfs-user hm-cfs-agent", "cfs")
    wait_for("shared click focus probe", lambda: client("hm-cfs-user") and client("hm-cfs-agent"))
    dispatch('hl.dsp.window.move({ window = "title:^hm-cfs-agent$", workspace = "71", follow = false })')
    cfs_user, cfs_agent = client("hm-cfs-user")["address"], client("hm-cfs-agent")["address"]
    dispatch(f'hl.dsp.focus({{ window = "address:{cfs_user}" }})')
    wait_for("user window focus", lambda: snapshot() == (70, cfs_user))
    cfs_path = open_socket(cfs_agent)
    user_text = "qwertyuiopasdfghjkl"
    codes = [letter_codes[c] for c in user_text]
    start = len(probe_events("cfs"))
    clicks = " ".join(["click sleep 60"] * 8)
    spawn("sh -c " + shlex.quote(f"sleep 0.2; hyprmasq-input --socket {cfs_path} move 30 30 {clicks}"), "cfs-sender")
    machine.send_chars(user_text, delay=0.04)
    wait_for("the agent's clicks", lambda: [e for _, e in probe_events("cfs", start)].count("button hm-cfs-agent 0") == 8, timeout=10)
    wait_for("the user's keys", lambda: len(keys_of(probe_events("cfs", start), "hm-cfs-user")) == len(codes), timeout=10)
    events = probe_events("cfs", start)
    assert keys_of(events, "hm-cfs-user") == codes and keys_of(events, "hm-cfs-agent") == [], events
    assert not any(e.startswith("keyboard ") for _, e in events), events
    # The agent's typing after its click still reaches its window, and the user's after it theirs.
    start = len(probe_events("cfs"))
    send(cfs_path, "click", "text", "z")
    machine.send_chars("x")
    wait_for("the user's key after", lambda: keys_of(probe_events("cfs", start), "hm-cfs-user") == [letter_codes["x"]], timeout=10)
    events = probe_events("cfs", start)
    assert keys_of(events, "hm-cfs-agent") == [letter_codes["z"]], events
    assert next(i for i, (_, e) in enumerate(events) if e == "button hm-cfs-agent 0") < next(i for i, (_, e) in enumerate(events) if e == "keyboard enter hm-cfs-agent"), events
    assert snapshot() == (70, cfs_user)
    for title in ["hm-cfs-agent", "hm-cfs-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("shared click focus probe closed", lambda: client("hm-cfs-agent") is None and client("hm-cfs-user") is None)


def hold_probe(log, user_title, agent_title, user_workspace, agent_workspace):
    """A focus probe as a browser of the user's: their window focused on user_workspace, and the agent's on
    agent_workspace, hidden, with a socket. Returns the addresses, the socket and the center of the user's window."""
    focus_workspace(user_workspace)
    spawn(f"hyprmasq-focus-probe focus {user_title} {agent_title}", log)
    wait_for("hold probe", lambda: client(user_title) and client(agent_title))
    dispatch(f'hl.dsp.window.move({{ window = "title:^{agent_title}$", workspace = "{agent_workspace}", follow = false }})')
    user, agent = client(user_title)["address"], client(agent_title)["address"]
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (user_workspace, user))
    (x, y), (width, height) = client(user_title)["at"], client(user_title)["size"]
    return user, agent, open_socket(agent), (x + width // 2, y + height // 2)


def index_of(events, predicate):
    return next((i for i, (_, e) in enumerate(events) if predicate(e)), None)


with subtest("the user's held button in a window of the agent's connection survives the agent's pointer input"):
    # Pointer input to the agent's window moves the connection's pointer away from the user's window and back,
    # and the leave would release the button the user holds there, in a selection or a slider. The agent's frame
    # waits until the user lets go, and a hold longer than a second pauses the input instead, which tells why.
    hb_user, hb_agent, hb_path, (cx, cy) = hold_probe("hb", "hm-hb-user", "hm-hb-agent", 72, 73)

    def hold_button(ms, log):
        alice(f"{client_env} nohup hyprmasq-user-input button {cx} {cy} {ms} > /tmp/hyprmasq-{log}.log 2>&1 &")
        wait_for("the user's press", lambda: "pressed" in machine.succeed(f"cat /tmp/hyprmasq-{log}.log"), timeout=10)

    def released(log):
        machine.wait_until_succeeds(f"grep -q released /tmp/hyprmasq-{log}.log", timeout=15)

    start = len(probe_events("hb"))
    hold_button(700, "hb-short")
    wait_for("the press in the user's window", lambda: index_of(probe_events("hb", start), lambda e: e == "button hm-hb-user 1") is not None, timeout=10)
    run_input_background("hb-move", hb_path, "move", 30, 30)
    status, result = input_result("hb-move")
    assert status == 0 and result["ok"], result
    released("hb-short")
    events = probe_events("hb", start)
    press, release = index_of(events, lambda e: e == "button hm-hb-user 1"), index_of(events, lambda e: e == "button hm-hb-user 0")
    motion = index_of(events, lambda e: e.startswith("motion hm-hb-agent "))
    assert press is not None and release is not None and motion is not None, events
    # The user's own release ends their hold, and only then does the agent's motion arrive.
    assert events[release][0] - events[press][0] >= 600, events
    assert release < motion and not any(e == "pointer leave hm-hb-user" for _, e in events[press:release]), events
    assert [e for _, e in events].count("button hm-hb-user 0") == 1, events

    start = len(probe_events("hb"))
    hold_button(2500, "hb-long")
    wait_for("the press in the user's window", lambda: index_of(probe_events("hb", start), lambda e: e == "button hm-hb-user 1") is not None, timeout=10)
    run_input_background("hb-paused", hb_path, "move", 30, 30)
    status, result = input_result("hb-paused")
    # The plugin dropped the frame that waited, the first one, so nothing arrived.
    assert status != 0 and result["error"]["code"] == "input_paused" and result["delivery"] == "none", result
    assert window_state(hb_agent)["lastPause"] == "user_button"
    released("hb-long")
    wait_for("the user's release", lambda: index_of(probe_events("hb", start), lambda e: e == "button hm-hb-user 0") is not None, timeout=10)
    events = probe_events("hb", start)
    press, release = index_of(events, lambda e: e == "button hm-hb-user 1"), index_of(events, lambda e: e == "button hm-hb-user 0")
    assert events[release][0] - events[press][0] >= 2400, events
    assert not any(e.startswith(("pointer leave hm-hb-user", "motion hm-hb-agent ")) for _, e in events), events
    # Once the user has let go, the agent's input goes through again.
    start = len(probe_events("hb"))
    send(hb_path, "move", 40, 40)
    wait_for("the agent's motion", lambda: index_of(probe_events("hb", start), lambda e: e.startswith("motion hm-hb-agent ")) is not None, timeout=10)
    assert snapshot() == (72, hb_user)

with subtest("the user's held key in a window of the agent's connection survives the agent's keys"):
    # A key to the agent's window moves the connection's keyboard focus away from the user's window and back, and
    # the user's window would take the leave for the release of the key they hold, which then stops repeating.
    start = len(probe_events("hb"))
    machine.send_monitor_command("sendkey a 700")
    wait_for("the user's key", lambda: keys_of(probe_events("hb", start), "hm-hb-user") == [letter_codes["a"]], timeout=10)
    run_input_background("hb-key", hb_path, "key", letter_codes["b"])
    status, result = input_result("hb-key")
    assert status == 0 and result["ok"], result
    events = probe_events("hb", start)
    down, up = index_of(events, lambda e: e == "key hm-hb-user 30 1"), index_of(events, lambda e: e == "key hm-hb-user 30 0")
    agent_key = index_of(events, lambda e: e == "key hm-hb-agent 48 1")
    assert down is not None and up is not None and agent_key is not None, events
    assert events[up][0] - events[down][0] >= 600, events
    assert up < agent_key and not any(e == "keyboard leave hm-hb-user" for _, e in events[down:up]), events

    start = len(probe_events("hb"))
    machine.send_monitor_command("sendkey a 2500")
    wait_for("the user's key", lambda: keys_of(probe_events("hb", start), "hm-hb-user") == [letter_codes["a"]], timeout=10)
    run_input_background("hb-key-paused", hb_path, "key", letter_codes["b"])
    status, result = input_result("hb-key-paused")
    # The plugin dropped the frame that waited, the first one, so nothing arrived.
    assert status != 0 and result["error"]["code"] == "input_paused" and result["delivery"] == "none", result
    assert window_state(hb_agent)["lastPause"] == "user_key"
    wait_for("the user's release", lambda: index_of(probe_events("hb", start), lambda e: e == "key hm-hb-user 30 0") is not None, timeout=10)
    events = probe_events("hb", start)
    down, up = index_of(events, lambda e: e == "key hm-hb-user 30 1"), index_of(events, lambda e: e == "key hm-hb-user 30 0")
    assert events[up][0] - events[down][0] >= 2400, events
    assert not any(e == "keyboard leave hm-hb-user" or e.startswith("key hm-hb-agent ") for _, e in events), events
    assert snapshot() == (72, hb_user)
    for title in ["hm-hb-agent", "hm-hb-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("hold probe closed", lambda: client("hm-hb-agent") is None and client("hm-hb-user") is None)

with subtest("the agent's pointer input beside the user's window has the agent's modifiers, not the ones the user holds"):
    # A connection has one set of modifiers, the user's while their window has its keyboard focus, and apps apply them
    # to all of the connection's pointer input: a Shift held for a capital would make the agent's click a Shift-click,
    # which in Chromium extends the page's selection into another field, and a Ctrl would make it a Ctrl-click and its
    # scroll a zoom. The connection gets the agent's modifiers right before each frame of its pointer input and the
    # user's right after it, while the keyboard focus stays in the user's window.
    pm_user, pm_agent, pm_path, _ = hold_probe("pm", "hm-pm-user", "hm-pm-agent", 101, 102)

    def modifiers_before(events, index):
        """The modifiers that the probe's connection had last been sent before the event at index."""
        return next(int(e.split()[-1]) for _, e in reversed(events[:index]) if e.startswith("modifiers "))

    def while_held(key, code, *input):
        """The probe's events while the user holds key, whose evdev code is code, through QEMU's keyboard and the
        agent's input goes meanwhile, up to the release of the key."""
        start = len(probe_events("pm"))
        machine.send_monitor_command(f"sendkey {key} 1500")
        wait_for(f"the user's {key}", lambda: f"key hm-pm-user {code} 1" in [e for _, e in probe_events("pm", start)], timeout=10)
        send(pm_path, *input)
        wait_for(f"the release of {key}", lambda: f"key hm-pm-user {code} 0" in [e for _, e in probe_events("pm", start)], timeout=10)
        return probe_events("pm", start)

    def agent_events(events, prefix):
        return [i for i, (_, e) in enumerate(events) if e.startswith(prefix)]

    # Shift, mask 1: the agent's plain click comes without it, and the user's window has it back right after.
    events = while_held("shift", 42, "move", 30, 30, "click")
    press, release = index_of(events, lambda e: e == "button hm-pm-agent 1"), index_of(events, lambda e: e == "button hm-pm-agent 0")
    assert press is not None and release is not None and release < index_of(events, lambda e: e == "key hm-pm-user 42 0"), events
    assert modifiers_before(events, press) == 0 and modifiers_before(events, release) == 0, events
    assert next(e for _, e in events[release:] if e.startswith("modifiers ")) == "modifiers hm-pm-user 1", events
    assert not any(e.startswith("keyboard ") for _, e in events), events
    # Ctrl, mask 4: neither the click nor a wheel or smooth scroll comes with it.
    events = while_held("ctrl", 29, "move", 30, 30, "click", "wheel", 0, 2, "scroll", 0, 30)
    buttons, axes = agent_events(events, "button hm-pm-agent "), agent_events(events, "axis hm-pm-agent ")
    assert len(buttons) == 2 and axes, events
    assert all(modifiers_before(events, i) == 0 for i in buttons + axes), events
    assert next(e for _, e in events[max(buttons + axes) :] if e.startswith("modifiers ")) == "modifiers hm-pm-user 4", events
    assert not any(e.startswith("keyboard ") for _, e in events), events

    # The user types capitals into their window during the agent's clicks: their keys reach their window alone, each
    # letter with Shift exactly when it is a capital, and no click of the agent's has Shift.
    user_text = "QwErTyUiOpAsDfGh"
    start = len(probe_events("pm"))
    clicks = " ".join(["click sleep 30"] * 16)
    spawn("sh -c " + shlex.quote(f"sleep 0.2; hyprmasq-input --socket {pm_path} move 30 30 {clicks}"), "pm-sender")
    machine.send_chars(user_text, delay=0.04)
    wait_for("the agent's clicks", lambda: [e for _, e in probe_events("pm", start)].count("button hm-pm-agent 0") == 16, timeout=15)
    events = probe_events("pm", start)
    letters = [(i, int(e.split()[2])) for i, (_, e) in enumerate(events) if e.startswith("key hm-pm-user ") and e.endswith(" 1") and e.split()[2] != "42"]
    assert [code for _, code in letters] == [letter_codes[c.lower()] for c in user_text], events
    assert [modifiers_before(events, i) & 1 for i, _ in letters] == [int(c.isupper()) for c in user_text], events
    assert keys_of(events, "hm-pm-agent") == [] and not any(e.startswith("keyboard ") for _, e in events), events
    assert all(modifiers_before(events, i) == 0 for i in agent_events(events, "button hm-pm-agent ")), events

    # The agent's own Ctrl-click has Ctrl, alone: its Ctrl waits until the user lets go of their Shift, and the user's
    # window gets its modifiers back with the keyboard focus.
    start = len(probe_events("pm"))
    machine.send_monitor_command("sendkey shift 800")
    wait_for("the user's shift", lambda: "key hm-pm-user 42 1" in [e for _, e in probe_events("pm", start)], timeout=10)
    send(pm_path, "move", 30, 30, "click", "left", 1, "ctrl")
    wait_for("the keyboard back in the user's window", lambda: "keyboard enter hm-pm-user" in [e for _, e in probe_events("pm", start)], timeout=10)
    events = probe_events("pm", start)
    press, back = index_of(events, lambda e: e == "button hm-pm-agent 1"), index_of(events, lambda e: e == "keyboard enter hm-pm-user")
    assert index_of(events, lambda e: e == "key hm-pm-user 42 0") < press < back and modifiers_before(events, press) == 4, events
    assert events[back + 1][1] == "modifiers hm-pm-user 0", events
    assert snapshot() == (101, pm_user)
    for title in ["hm-pm-agent", "hm-pm-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("modifier probe closed", lambda: client("hm-pm-agent") is None and client("hm-pm-user") is None)

with subtest("the user's keys take the keyboard back from a window that keeps it also through an input method"):
    # With an input method's keyboard grab, as fcitx5 or ibus have, Hyprland sends the user's keys to the input
    # method instead of the seat, which then commits text to the window with the real focus. The keys still take
    # the connection's keyboard focus back from the agent's window while its shortcut settles, so the shortcut
    # does not take effect if the user's key comes within its 150 ms, and the plugin counts it.
    ime_user, ime_agent = shortcut_probe("ime", "hm-ime-user", "hm-ime-agent", 74, 75)
    ime_path = open_socket(ime_agent)
    spawn("hyprmasq-user-input ime", "ime-grab")
    wait_for("the input method's grab", lambda: "grabbed" in machine.succeed("cat /tmp/hyprmasq-ime-grab.log"), timeout=10)
    user_text = "qwertyuioplzxcvbnm"
    for attempt in range(5):
        before = window_state(ime_agent)["interrupted"]
        start, grabbed = len(probe_events("ime")), len(machine.succeed("cat /tmp/hyprmasq-ime-grab.log").splitlines())
        spawn("sh -c " + shlex.quote(f"sleep 0.3; hyprmasq-input --socket {ime_path} keys ctrl+l"), "ime-sender")
        machine.send_chars(user_text, delay=0.03)
        wait_for("the shortcut", lambda: any(e.startswith("shortcut ") for _, e in probe_events("ime", start)), timeout=10)
        got = lambda: [line for line in machine.succeed("cat /tmp/hyprmasq-ime-grab.log").splitlines()[grabbed:] if line.startswith("key ") and line.endswith(" 1")]
        wait_for("the user's keys at the input method", lambda: len(got()) == len(user_text), timeout=10)
        events = probe_events("ime", start)
        # The input method took the user's keys, and the agent's reached its window.
        assert keys_of(events, "hm-ime-user") == [] and keys_of(events, "hm-ime-agent") == [29, 38], events
        shortcuts = [e for _, e in events if e.startswith("shortcut ")]
        assert shortcuts in (["shortcut hm-ime-agent 4 38"], ["shortcut hm-ime-agent 4 38 inactive"]), events
        if shortcuts[0].endswith(" inactive"):
            wait_for("the count", lambda: window_state(ime_agent)["interrupted"] > before, timeout=5)
            break
    else:
        raise AssertionError("the user's keys through the input method never took the keyboard back")
    assert snapshot() == (74, ime_user)
    # A key that the user holds through the input method holds the agent's keys back too: the toolkit of the
    # user's window may tie its input method context to the keyboard focus, which the agent's key would move.
    time.sleep(1)
    start, grabbed = len(probe_events("ime")), len(machine.succeed("cat /tmp/hyprmasq-ime-grab.log").splitlines())
    ime_keys = lambda: [line.split() for line in machine.succeed("cat /tmp/hyprmasq-ime-grab.log").splitlines()[grabbed:] if line.startswith("key ")]
    machine.send_monitor_command("sendkey a 700")
    wait_for("the held key at the input method", lambda: any(k[2:] == ["30", "1"] for k in ime_keys()), timeout=10)
    run_input_background("ime-key", ime_path, "key", letter_codes["b"])
    status, result = input_result("ime-key")
    assert status == 0 and result["ok"], result
    wait_for("the release at the input method", lambda: any(k[2:] == ["30", "0"] for k in ime_keys()), timeout=10)
    down = next(int(k[1]) for k in ime_keys() if k[2:] == ["30", "1"])
    up = next(int(k[1]) for k in ime_keys() if k[2:] == ["30", "0"])
    events = probe_events("ime", start)
    agent_key = next((t for t, e in events if e == "key hm-ime-agent 48 1"), -1)
    # Stamps of two processes: the agent's key comes at the earliest with the release, 700 ms after the press.
    assert up - down >= 600 and agent_key >= down + 600, (down, up, events)
    assert not any(e == "keyboard leave hm-ime-user" for t, e in events if t < down + 600), events
    machine.succeed("pkill -f '[h]yprmasq-user-input ime'")
    # Without the grab, the user's keys reach their window again.
    start = len(probe_events("ime"))
    wait_for("keys without the input method", lambda: machine.send_chars("x") or keys_of(probe_events("ime", start), "hm-ime-user"), timeout=10)
    for title in ["hm-ime-agent", "hm-ime-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("input method probe closed", lambda: client("hm-ime-agent") is None and client("hm-ime-user") is None)

with subtest("a driven Wayland window that asks for activation is marked urgent and leaves the user's focus alone"):
    # As a page does through window.focus() a second after the agent's key, in a window that no launch started and the
    # plugin did not place, and for another window of its app while the user is in another app. The test's
    # focus_on_activate would focus it and switch the user's workspace. Hyprland refuses a request without the serial of
    # input that the app got, as foot sends for its bell, so the probe sends the key's.
    focus_workspace(76)
    spawn("hyprmasq-focus-probe activate hm-act-bell hm-act-sibling 1000", "act")
    wait_for("activation probe", lambda: client("hm-act-bell") and client("hm-act-sibling"))
    act_bell, act_sibling = client("hm-act-bell")["address"], client("hm-act-sibling")["address"]
    focus_workspace(77)
    act_user = foot("hm-act-user")
    wait_for("user window focus", lambda: snapshot() == (77, act_user))
    act_path = open_socket(act_bell)
    send(act_path, "key", 48)  # B: the window itself
    wait_for("the window's activation request", lambda: urgent("ipc-events", act_bell), timeout=15)
    time.sleep(1)
    assert snapshot() == (77, act_user)
    send(act_path, "key", 24)  # O: the other window of its app
    wait_for("the sibling's activation request", lambda: urgent("ipc-events", act_sibling), timeout=15)
    time.sleep(1)
    assert snapshot() == (77, act_user)
    events = [e for _, e in probe_events("act")]
    assert "activate hm-act-bell" in events and "activate hm-act-sibling" in events, events
    # Once nothing drives the window, its request focuses it, as without the plugin.
    send(act_path, "key", 48)
    assert hypr(f"hyprmasq eis close {act_bell}") == "ok"
    wait_for("window activated", lambda: snapshot() == (76, act_bell), timeout=15)
    for title in ["hm-act-bell", "hm-act-sibling", "hm-act-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("activation windows closed", lambda: all(client(t) is None for t in ["hm-act-bell", "hm-act-sibling", "hm-act-user"]))
