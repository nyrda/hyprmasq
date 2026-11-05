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
    send_key("ctrl-shift-v")
    send_key("ret")
    send_key("shift-insert")
    send_key("ret")
    wait_for("the user's pastes", lambda: cat_output("user") == "userclipboard\nuserprimary\n", timeout=15)
    # The user focuses the probe, whose window has a socket, and copies there with a key, and selects with a click of
    # a virtual pointer: both are the user's.
    # The probe made the agent's clipboard, so the user's focus, which the workspace gives it, ends that copy there with
    # a cancel, and leaves the agent's primary selection, which the probe also made, alone.
    start = len(probe_events("hm-clip-probe"))
    focus_workspace(85)
    dispatch(f'hl.dsp.focus({{ window = "address:{clip_probe_address}" }})')
    wait_for("the probe focused", lambda: snapshot() == (85, clip_probe_address))
    events = wait_clip("hm-clip-probe", start, "source cancelled hm-clip-probe-copy1")
    assert "source cancelled hm-clip-probe-select1" not in events, events
    start = len(probe_events("hm-clip-probe"))
    send_chars("c")
    probe_at = client("hm-clip-probe")
    dispatch(f"hl.dsp.cursor.move({{ x = {probe_at['at'][0] + 50}, y = {probe_at['at'][1] + 50} }})")
    alice(f"{client_env} wlrctl pointer click left")
    wait_clip("hm-clip-probe", start, "copy hm-clip-probe-copy2", "select hm-clip-probe-select2")
    wait_for("the user's copy", lambda: user_selections() == ("hm-clip-probe-copy2", "hm-clip-probe-select2"), timeout=10)
    # The agent copies in the probe, which has the user's focus, and its paste there gets that copy. The user's paste
    # right after it ends the agent's copy, before the key arrives, and gets the user's.
    start = len(probe_events("hm-clip-probe"))
    send(clip_probe_path, "keys", "c")
    wait_clip("hm-clip-probe", start, "copy hm-clip-probe-copy3")
    send(clip_probe_path, "keys", "v")
    wait_clip("hm-clip-probe", start, "paste clipboard hm-clip-probe-copy3")
    assert user_selections() == ("hm-clip-probe-copy2", "hm-clip-probe-select2")
    send_chars("v")
    events = wait_clip("hm-clip-probe", start, "source cancelled hm-clip-probe-copy3", "paste clipboard hm-clip-probe-copy2")
    assert events.index("source cancelled hm-clip-probe-copy3") < events.index("paste clipboard hm-clip-probe-copy2"), events
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

with subtest("an app that pastes its own copy pastes the user's clipboard once the user's input reaches it"):
    # Chromium, as Brave, Electron and GTK, pastes its own copy while it takes itself for the clipboard's owner, not the
    # offer it is sent. The agent copies in a hidden window of a Chromium whose other window the user works in: the
    # user's paste there, and in the agent's window once they focus it, must paste the user's clipboard.
    def cc_pastes(name):
        return re.findall(rf'"probe {name} paste ([^"]*)"', machine.succeed("cat /tmp/hyprmasq-clip-chromium.log"))

    cc_command = ("chromium --ozone-platform=wayland --user-data-dir=/tmp/hm-clip-chromium --no-first-run --no-default-browser-check --disable-gpu --enable-logging=stderr --app=file:///etc/hyprmasq-test/clip.html#")
    focus_workspace(4)
    user_copies("userclipboard", "userprimary")
    focus_workspace(89)
    spawn(f"env {client_env} {cc_command}agent", "clip-chromium")
    wait_for("the agent's Chromium window", lambda: client("hm-cc-agent") is not None, timeout=120)
    cc_agent = client("hm-cc-agent")["address"]
    machine.wait_until_succeeds("grep -q '\"probe agent ready\"' /tmp/hyprmasq-clip-chromium.log", timeout=60)
    wait_drawn(cc_agent, 500)
    # The user's window of the same Chromium, on the visible workspace with the user's focus.
    focus_workspace(4)
    spawn(f"env {client_env} {cc_command}user", "clip-chromium-user")
    wait_for("the user's Chromium window", lambda: client("hm-cc-user") is not None, timeout=60)
    cc_user = client("hm-cc-user")["address"]
    # Its page logs in the log of the first Chromium, which opened the window.
    machine.wait_until_succeeds("grep -q '\"probe user ready\"' /tmp/hyprmasq-clip-chromium.log", timeout=60)
    dispatch(f'hl.dsp.focus({{ window = "address:{cc_user}" }})')
    wait_for("the user's Chromium window focused", lambda: snapshot() == (4, cc_user))
    # The agent copies its textarea's text, and pastes it in the same window.
    cc_path = open_socket(cc_agent)
    send(cc_path, "keys", "ctrl+a", "keys", "ctrl+c", "sleep", 500, "keys", "ctrl+v")
    wait_for("the agent's paste", lambda: cc_pastes("agent") == ["agentcopy"], timeout=15)
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, cc_user)
    # The user pastes in their window of the same Chromium: their clipboard, not the agent's copy.
    send_key("ctrl-v")
    wait_for("the user's paste", lambda: cc_pastes("user") != [], timeout=15)
    assert cc_pastes("user") == ["userclipboard"], cc_pastes("user")
    # The agent's copy has ended there: the agent's paste gets nothing, and never the user's clipboard.
    send(cc_path, "keys", "ctrl+v")
    time.sleep(2)
    assert cc_pastes("agent") in (["agentcopy"], ["agentcopy", ""]), cc_pastes("agent")
    # The agent copies again, and the user focuses the agent's window and pastes there: their clipboard again.
    send(cc_path, "keys", "ctrl+a", "keys", "ctrl+c", "sleep", 500, "keys", "ctrl+v")
    wait_for("the agent's second paste", lambda: cc_pastes("agent")[-1:] == ["agentcopy"] and len(cc_pastes("agent")) >= 2, timeout=15)
    cc_agent_pastes = len(cc_pastes("agent"))
    assert user_selections() == ("userclipboard", "userprimary")
    focus_workspace(89)
    dispatch(f'hl.dsp.focus({{ window = "address:{cc_agent}" }})')
    wait_for("the agent's Chromium window focused", lambda: snapshot() == (89, cc_agent))
    send_key("ctrl-v")
    wait_for("the user's paste in the agent's window", lambda: len(cc_pastes("agent")) > cc_agent_pastes, timeout=15)
    assert cc_pastes("agent")[cc_agent_pastes:] == ["userclipboard"], cc_pastes("agent")
    assert user_selections() == ("userclipboard", "userprimary")
    hypr(f"hyprmasq eis close {cc_agent}")
    machine.succeed("pkill -f '[u]ser-data-dir=/tmp/hm-clip-chromium'")
    wait_for("Chromium closed", lambda: client("hm-cc-agent") is None and client("hm-cc-user") is None)
    focus_workspace(4)

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

with subtest("an X11 app's selection that the agent's input makes once the input hooks have gone stays in its X server"):
    # An X11 app takes a selection when it gets to the input that makes it, which can be after the socket and its input
    # hooks have gone idle. The hooks of X selections stay while the agent's input decides whose new X selections are.
    # A stopped xterm gets to the agent's double-click only once it continues.
    focus_workspace(4)
    user_copies("userclipboard", "userprimary")
    x_late_user = xterm("hm-clip-x-late-user")
    dispatch(f'hl.dsp.focus({{ window = "address:{x_late_user}" }})')
    wait_for("the user's xterm focused", lambda: snapshot() == (4, x_late_user))
    assert user_selections() == ("userclipboard", "userprimary")
    focus_workspace(88)
    x_late = xterm("hm-clip-x-late", "sh -c " + shlex.quote("printf 'xlateword\\n'; exec sleep 900"))
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{x_late_user}" }})')
    wait_for("the user's xterm focused", lambda: snapshot() == (4, x_late_user))
    x_late_path = json.loads(hypr(f"-j hyprmasq eis open {x_late} force"))["path"]
    x_late_pid = client("hm-clip-x-late")["pid"]
    machine.succeed(f"kill -STOP {x_late_pid}")
    send(x_late_path, "move", 30, 8, "click", "left", 2)
    wait_for("the input hooks gone", lambda: not hooks_installed("input") and not hooks_installed("x11-input"), timeout=10)
    assert hooks_installed("x11-selection")
    machine.succeed(f"kill -CONT {x_late_pid}")
    wait_for("the X primary selection", lambda: alice(f"{x_env} timeout 5 xclip -o -selection primary || true") == "xlateword", timeout=10)
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary")
    dispatch(f'hl.dsp.window.close({{ window = "address:{x_late}" }})')
    wait_for("the hidden xterm closed", lambda: client("hm-clip-x-late") is None)
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary")
    dispatch(f'hl.dsp.window.close({{ window = "address:{x_late_user}" }})')
    wait_for("the user's xterm closed", lambda: client("hm-clip-x-late-user") is None)
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("user's terminal focused", lambda: snapshot() == (4, visible))

with subtest("the agent's paste pastes its text through the agent clipboard, a long one in parts, and leaves every selection alone"):
    # The probe selects a text of its own as the agent's primary selection, as a text field's selection is, and the
    # agent pastes over it with act. The probe gets the text on Shift+Insert, its selection gets no cancel, which in GTK
    # would drop the selection that the paste replaces, and the user's selections stay.
    focus_workspace(4)
    user_copies("userclipboard", "userprimary")
    paste_probe, paste_path = clip_probe("hm-paste-probe", 87)
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("user's terminal focused", lambda: snapshot() == (4, visible))
    start = len(probe_events("hm-paste-probe"))
    send(paste_path, "keys", "s")
    wait_clip("hm-paste-probe", start, "select hm-paste-probe-select1")
    paste_agent = Mcp("paste")
    paste_agent.initialize()
    paste_result = paste_agent.call("act", {"window": paste_probe, "actions": [["paste", "agentpaste"]], "capture": "none"})
    assert not paste_result.is_error and paste_result.data["actions"]["delivery"] == "complete", paste_result
    assert "unread_pastes" not in paste_result.data["actions"], paste_result
    wait_clip("hm-paste-probe", start, "paste clipboard agentpaste")
    # A paste of 40000 bytes goes in parts of 16000, each once the probe has read the one before.
    paste_result = paste_agent.call("act", {"window": paste_probe, "actions": [["paste", "a" * 16000 + "b" * 16000 + "c" * 8000]], "capture": "none"})
    assert not paste_result.is_error and "unread_pastes" not in paste_result.data["actions"], paste_result
    events = wait_clip("hm-paste-probe", start, "paste clipboard bytes 8000")
    pasted = [e for e in events if e.startswith("paste clipboard ")]
    assert pasted == ["paste clipboard agentpaste", "paste clipboard " + "a" * 255, "paste clipboard bytes 16000", "paste clipboard " + "b" * 255, "paste clipboard bytes 16000", "paste clipboard " + "c" * 255, "paste clipboard bytes 8000"], pasted
    assert "source cancelled hm-paste-probe-select1" not in events, events
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, visible)
    assert paste_agent.stop() == 0
    hypr(f"hyprmasq eis close {paste_probe}")
    machine.succeed("pkill -f '[h]yprmasq-clip-probe hm-paste-probe'")
    wait_for("the paste probe closed", lambda: client("hm-paste-probe") is None)

with subtest("pastes into windows of different apps take turns, and each app gets its own text"):
    # The plugin holds one agent clipboard, so a paste waits while another one's batch runs, also in another app.
    paste_a, _ = clip_probe("hm-paste-a", 88)
    paste_b, _ = clip_probe("hm-paste-b", 89)
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("user's terminal focused", lambda: snapshot() == (4, visible))
    user_copies("userclipboard", "userprimary")
    start_a, start_b = len(probe_events("hm-paste-a")), len(probe_events("hm-paste-b"))
    agent_a, agent_b = Mcp("paste-a"), Mcp("paste-b")
    agent_a.initialize()
    agent_b.initialize()
    # The first batch holds the clipboard lock through the pause after its paste.
    first = agent_a.start("act", {"window": paste_a, "actions": [["paste", "textfora"], ["pause", 3000]], "capture": "none"}, token="hm-paste-first")
    wait_for("the first paste is sent", lambda: any(p.get("message") == "sending input" for p in agent_a.progress("hm-paste-first")), timeout=10)
    second = agent_b.start("act", {"window": paste_b, "actions": [["paste", "textforb"]], "capture": "none"}, token="hm-paste-second")
    wait_for("the second paste waits", lambda: any(p.get("message") == "waiting for a paste into another window" for p in agent_b.progress("hm-paste-second")), timeout=10)
    first_result, second_result = agent_a.result(first), agent_b.result(second)
    for result in (first_result, second_result):
        assert not result.is_error and result.data["actions"]["delivery"] == "complete" and "unread_pastes" not in result.data["actions"], result
    events_a = wait_clip("hm-paste-a", start_a, "paste clipboard textfora")
    events_b = wait_clip("hm-paste-b", start_b, "paste clipboard textforb")
    assert "paste clipboard textforb" not in events_a and "paste clipboard textfora" not in events_b, (events_a, events_b)
    assert user_selections() == ("userclipboard", "userprimary") and snapshot() == (4, visible)
    assert agent_a.stop() == 0 and agent_b.stop() == 0
    for address, title in ((paste_a, "hm-paste-a"), (paste_b, "hm-paste-b")):
        hypr(f"hyprmasq eis close {address}")
        machine.succeed(f"pkill -f '[h]yprmasq-clip-probe {title}'")
        wait_for(f"{title} closed", lambda: client(title) is None)
