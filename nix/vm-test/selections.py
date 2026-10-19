def focus_clip_user():
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{clip_user}" }})')
    wait_for("the user's window focused", lambda: snapshot() == (4, clip_user))


def cat_output(name):
    return machine.succeed(f"cat /tmp/hyprmasq-clip-{name}.out")


with subtest("a hidden app sees none of the user's selections"):
    # The user's window, which records what they paste into it, keeps the focus on workspace 4 throughout.
    focus_workspace(4)
    clip_user = foot("hm-clip-user", "sh -c 'exec cat > /tmp/hyprmasq-clip-user.out'")
    focus_clip_user()
    user_copies("userclipboard", "userprimary")
    clip_probe_address, clip_probe_path = clip_probe("hm-clip-probe", 85)
    focus_clip_user()
    # The agent pastes before it has copied anything: the app has no selection, not the user's.
    start = len(probe_events("hm-clip-probe"))
    send(clip_probe_path, "keys", "v", "keys", "m")
    events = wait_clip("hm-clip-probe", start, "paste clipboard none", "paste primary none")
    assert "clipboard none" in events and "primary none" in events and "clipboard offer" not in events, events
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, clip_user)

with subtest("the agent's selection and copy in a hidden app leave the user's selections alone, and paste in that app"):
    # The audit's case: a double-click selects a word in a hidden foot and Ctrl+Shift+C copies it. foot pastes into
    # its cat, which writes each line to a file.
    focus_workspace(86)
    clip_a = foot("hm-clip-a", "sh -c " + shlex.quote("printf 'agentword\\n'; exec cat > /tmp/hyprmasq-clip-a.out"))
    focus_clip_user()
    clip_a_path = open_socket(clip_a)
    send(clip_a_path, "move", 24, 8, "click", "left", 2)
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary")
    send(clip_a_path, "keys", "ctrl+shift+c")
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary")
    # Ctrl+Shift+V pastes the clipboard and Shift+Insert the primary selection.
    send(clip_a_path, "keys", "ctrl+shift+v", "sleep", 500, "keys", "Return", "keys", "shift+Insert", "sleep", 500, "keys", "Return")
    wait_for("the pastes in the same app", lambda: cat_output("a") == "agentword\nagentword\n", timeout=15)
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, clip_user)

with subtest("the agent's copy pastes into another hidden app, which the source sends its data"):
    start = len(probe_events("hm-clip-probe"))
    send(clip_probe_path, "keys", "v", "sleep", 300, "keys", "m")
    wait_clip("hm-clip-probe", start, "paste clipboard agentword", "paste primary agentword")
    # The other way: the probe copies and selects, and foot pastes both.
    start = len(probe_events("hm-clip-probe"))
    send(clip_probe_path, "keys", "c", "keys", "s")
    wait_clip("hm-clip-probe", start, "copy hm-clip-probe-copy1", "select hm-clip-probe-select1")
    send(clip_a_path, "keys", "ctrl+shift+v", "sleep", 500, "keys", "Return", "keys", "shift+Insert", "sleep", 500, "keys", "Return")
    wait_for("the pastes in the other app", lambda: cat_output("a") == "agentword\nagentword\nhm-clip-probe-copy1\nhm-clip-probe-select1\n", timeout=15)
    # The source wrote the data into the destination's pipe itself.
    events = clip_events("hm-clip-probe", start)
    assert "source send hm-clip-probe-copy1" in events and "source send hm-clip-probe-select1" in events, events
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, clip_user)

with subtest("the user's own copy and paste work, also in an app with a socket"):
    # The user pastes into their window through QEMU's keyboard, real input: their clipboard and primary selection.
    machine.send_key("ctrl-shift-v")
    machine.send_key("ret")
    machine.send_key("shift-insert")
    machine.send_key("ret")
    wait_for("the user's pastes", lambda: cat_output("user") == "userclipboard\nuserprimary\n", timeout=15)
    # The user focuses the probe, whose window has a socket, and copies there with a key, and selects with a click of
    # a virtual pointer: both are the user's.
    focus_workspace(85)
    dispatch(f'hl.dsp.focus({{ window = "address:{clip_probe_address}" }})')
    wait_for("the probe focused", lambda: snapshot() == (85, clip_probe_address))
    start = len(probe_events("hm-clip-probe"))
    machine.send_chars("c")
    probe_at = client("hm-clip-probe")
    dispatch(f"hl.dsp.cursor.move({{ x = {probe_at['at'][0] + 50}, y = {probe_at['at'][1] + 50} }})")
    alice(f"{client_env} wlrctl pointer click left")
    wait_clip("hm-clip-probe", start, "copy hm-clip-probe-copy2", "select hm-clip-probe-select2")
    wait_for("the user's copy", lambda: user_selections() == ("hm-clip-probe-copy2", "hm-clip-probe-select2"), timeout=10)
    # The agent's paste in the probe, which has the user's focus, gets the agent's clipboard, and the user's paste
    # right after it the user's.
    start = len(probe_events("hm-clip-probe"))
    send(clip_probe_path, "keys", "v")
    wait_clip("hm-clip-probe", start, "paste clipboard hm-clip-probe-copy1")
    machine.send_chars("v")
    wait_clip("hm-clip-probe", start, "paste clipboard hm-clip-probe-copy2")
    assert user_selections() == ("hm-clip-probe-copy2", "hm-clip-probe-select2") and snapshot() == (85, clip_probe_address)
    focus_clip_user()
    user_copies("userclipboard", "userprimary")

with subtest("the agent's selections go with the app that made them"):
    # The probe owns the agent's clipboard and primary selection. Once it is gone, foot pastes nothing.
    machine.succeed("pkill -f '[h]yprmasq-clip-probe hm-clip-probe' || true")
    wait_for("the probe closed", lambda: client("hm-clip-probe") is None)
    send(clip_a_path, "keys", "ctrl+shift+v", "sleep", 500, "keys", "Return", "keys", "shift+Insert", "sleep", 500, "keys", "Return")
    expected = "agentword\nagentword\nhm-clip-probe-copy1\nhm-clip-probe-select1\n\n\n"
    wait_for("the empty pastes", lambda: cat_output("a") == expected, timeout=15)
    time.sleep(1)
    assert cat_output("a") == expected
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, clip_user)

with subtest("an X11 app's selection that the agent makes stays in its X server, also when the app exits"):
    # The user works in an xterm of theirs, so Hyprland takes X selections to Wayland. The agent double-clicks a word
    # in a hidden xterm of the same X server, through a socket opened with force.
    # Hyprland drops a new primary selection while the pointer is over an XWayland window, which has no primary
    # selection device, so the user copies before their xterm opens.
    focus_workspace(4)
    user_copies("userclipboard", "userprimary")
    x_clip_user = xterm("hm-clip-x-user")
    dispatch(f'hl.dsp.focus({{ window = "address:{x_clip_user}" }})')
    wait_for("the user's xterm focused", lambda: snapshot() == (4, x_clip_user))
    assert user_selections() == ("userclipboard", "userprimary")
    focus_workspace(88)
    x_clip = xterm("hm-clip-x", "sh -c " + shlex.quote("printf 'xagentword\\n'; exec sleep 900"))
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{x_clip_user}" }})')
    wait_for("the user's xterm focused", lambda: snapshot() == (4, x_clip_user))
    x_clip_path = json.loads(hypr(f"-j hyprmasq eis open {x_clip} force"))["path"]
    send(x_clip_path, "move", 30, 8, "click", "left", 2)
    wait_for("the X primary selection", lambda: alice(f"{x_env} timeout 5 xclip -o -selection primary || true") == "xagentword", timeout=10)
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary")
    # The xterm exits, so its X selection has no owner, which would clear the user's.
    dispatch(f'hl.dsp.window.close({{ window = "address:{x_clip}" }})')
    wait_for("the hidden xterm closed", lambda: client("hm-clip-x") is None)
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, x_clip_user)
    dispatch(f'hl.dsp.window.close({{ window = "address:{x_clip_user}" }})')
    wait_for("the user's xterm closed", lambda: client("hm-clip-x-user") is None)
    for title in ["hm-clip-a", "hm-clip-user"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("clipboard windows closed", lambda: client("hm-clip-a") is None and client("hm-clip-user") is None)
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("user's terminal focused", lambda: snapshot() == (4, visible))
