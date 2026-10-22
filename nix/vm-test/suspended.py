def sus_configured(start=0):
    """The configures that the Qt probe applied from entry start of its log on, as "W H suspended" or "W H shown"."""
    return [event.removeprefix("configured ") for _, event in probe_events("s-qt", start) if event.startswith("configured ")]


def sus_got(start, event):
    """Whether the Qt probe logged event from entry start of its log on."""
    return any(e == event for _, e in probe_events("s-qt", start))


def sus_told(log, start=0):
    """What a probe was told from entry start of its log on, the Qt probe's applied configures or suspend-probe's, as
    (ms, "suspended" or "shown") pairs."""
    return [(t, e.split()[3]) for t, e in probe_events(log, start) if e.split()[0] in ("configured", "configure")]


def sus_now(log, start=0):
    """What a probe was told last from entry start of its log on, or None."""
    told = sus_told(log, start)
    return told[-1][1] if told else None


def sus_wait(address, arguments, output=None):
    """A wait since the window's commit counter, in the background with output, otherwise its reply."""
    command = f"-j hyprmasq wait {address} since {window_state(address)['commits']} {arguments}"
    if output:
        hypr_background(command, output)
        return None
    return json.loads(hypr(command))


# Hyprland tells the windows of hidden workspaces that they are suspended, and Qt's Wayland backend takes a suspended
# window for one that is not exposed, for which it commits no frame. The probe draws the form task's Qt questionnaire's
# widgets, and the area drives it as an agent drove that questionnaire on a laptop, where its window went on taking the
# input and showed none of it. The output is as large as a laptop's, so that the probe maps beside the user's window
# and gets twice the width on the hidden workspace, and the user's foot draws nothing, as on an idle desktop.
sus_before = snapshot()
output_mode(1280, 800)
focus_workspace(92)
sus_user = foot("hm-s-user")
wait_drawn(sus_user, 500)
sus_agent = Mcp("suspended")
sus_agent.initialize()

with subtest("a hidden Qt 6 window shows the input of each act in a fresh frame"):
    spawn("hyprmasq-qt-probe hm-s-qt", "s-qt")
    wait_for("hm-s-qt", lambda: client("hm-s-qt") is not None)
    sus_qt = client("hm-s-qt")["address"]
    wait_for("the probe beside the user's window", lambda: geometry(sus_qt)[1] == [640, 800])
    wait_drawn(sus_qt, 500)
    dispatch(f'hl.dsp.window.move({{ window = "address:{sus_qt}", workspace = "name:hm-s", follow = false }})')
    dispatch(f'hl.dsp.focus({{ window = "address:{sus_user}" }})')
    wait_for("the probe suspended at the output's size", lambda: sus_configured()[-1:] == ["1280 800 suspended"])
    assert snapshot() == (92, sus_user), snapshot()
    # Qt draws at the new size while it applies the configure, before it takes the window for one that is not exposed.
    assert not window_state(sus_qt)["resizing"], window_state(sus_qt)
    sus_shot = sus_agent.call("screenshot", {"window": sus_qt})
    assert (sus_shot.data["shot"]["width"], sus_shot.data["shot"]["height"]) == (1280, 800), sus_shot
    # The acts of the agent, each a while after the one before, with the event that the probe logs for it and the box
    # of the widget that shows it, in logical pixels, which are the shot's. The dropdown's list opens in a popup, which
    # draws although its window does not, and the choice shows in the window once the popup has closed.
    sus_acts = [
        ([{"action": "click", "x": 40, "y": 35}], "checked 1", (20, 20, 180, 50)),
        ([{"action": "click", "x": 26, "y": 85}, {"action": "key", "keys": ["home"]}, {"action": "key", "keys": ["right"], "repeat": 5}], "slider 5", (20, 70, 320, 100)),
        ([{"action": "click", "x": 170, "y": 135}, {"action": "type", "text": "Northwind 42"}], "text Northwind 42", (20, 120, 320, 150)),
        ([{"action": "click", "x": 120, "y": 185}], None, None),
        ([{"action": "key", "keys": ["down"], "repeat": 3}, {"action": "key", "keys": ["enter"]}], "choice Mars", (20, 170, 220, 200)),
        ([{"action": "click", "x": 40, "y": 35}], "checked 0", (20, 20, 180, 50)),
    ]
    sus_image = sus_shot.images[0]
    for sus_actions, sus_event, sus_box in sus_acts:
        sus_start = len(probe_events("s-qt"))
        sus_acted = sus_agent.call("act", {"window": sus_qt, "shot": sus_shot.data["shot"]["id"], "actions": sus_actions, "capture": "window"})
        assert sus_acted.data["actions"]["delivery"] == "complete", sus_acted
        assert sus_acted.data["wait"]["redraw_observed"] and sus_acted.data["wait"]["ended"] == "quiet", sus_acted
        if sus_event:
            wait_for(f"the probe's {sus_event}", lambda: sus_got(sus_start, sus_event), timeout=5)
            assert changed_pixels(sus_image, sus_acted.images[0], sus_box) > 0, (sus_event, sus_acted)
            sus_image = sus_acted.images[0]
        sus_shot = sus_acted
        # Shown while driven, and suspended again once the input has stopped for a second, as frame callbacks stop.
        time.sleep(2)
        assert "1280 800 shown" in sus_configured(sus_start) and sus_configured(sus_start)[-1] == "1280 800 suspended", probe_events("s-qt", sus_start)
    assert snapshot() == (92, sus_user), snapshot()

with subtest("a suspended Qt 6 window that Hyprland resizes draws at its new size once act waits for it"):
    # A second window on its workspace halves the probe while it is suspended. Qt acks the configure, and draws nothing
    # at the new size, as the form task's questionnaire with Qt 6.8 did not after it was moved.
    sus_start = len(probe_events("s-qt"))
    sus_beside = alice(f"{ipc} hyprmasq-launch --workspace hm-s -- foot -T hm-s-beside").strip()
    wait_for("the probe at half the output", lambda: geometry(sus_qt)[1] == [640, 800])
    wait_for("the probe's configure", lambda: sus_configured(sus_start)[-1:] == ["640 800 suspended"])
    sus_drawn = window_state(sus_qt)["commits"]
    time.sleep(2)
    assert window_state(sus_qt)["commits"] == sus_drawn, window_state(sus_qt)
    sus_waited = sus_agent.call("act", {"window": sus_qt, "actions": [], "wait": {"expect_change": False}, "capture": "window"})
    assert sus_waited.data["wait"]["ended"] == "quiet" and sus_waited.data["wait"]["commits"] > 0, sus_waited
    assert not sus_waited.data["wait"].get("resizing") and not sus_waited.data["shot"].get("resizing"), sus_waited
    assert (sus_waited.data["shot"]["width"], sus_waited.data["shot"]["height"]) == (640, 800), sus_waited
    assert not window_state(sus_qt)["resizing"], window_state(sus_qt)
    time.sleep(2)
    assert "640 800 shown" in sus_configured(sus_start) and sus_configured(sus_start)[-1] == "640 800 suspended", probe_events("s-qt", sus_start)
    assert snapshot() == (92, sus_user), snapshot()

with subtest("the Qt window is told that it is suspended again when its socket closes, its sender dies, or its wait is cancelled or times out"):
    # A sender that holds its devices keeps the window from being told for as long as it is connected.
    sus_path = open_socket(sus_qt)
    sus_start = len(probe_events("s-qt"))
    run_input_background("s-closed", sus_path, "move", 10, 10, "sleep", 20000)
    wait_for("the window shown for the sender", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    sus_since = vm_now()
    hypr(f"hyprmasq eis close {sus_qt}")
    wait_for("the window suspended once its socket closed", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    assert sus_told("s-qt", sus_start)[-1][0] - sus_since < 1500, probe_events("s-qt", sus_start)
    machine.wait_until_succeeds("test -s /tmp/s-closed.status", timeout=30)
    # A sender that dies without a word.
    sus_path = open_socket(sus_qt)
    sus_start = len(probe_events("s-qt"))
    run_input_background("s-killed", sus_path, "move", 10, 10, "sleep", 20000)
    wait_for("the window shown for the sender", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    sus_since = vm_now()
    machine.succeed("pkill -KILL -f '[h]yprmasq-input --json .* sleep 20000'")
    wait_for("the window suspended once its sender died", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    assert sus_told("s-qt", sus_start)[-1][0] - sus_since < 5000, probe_events("s-qt", sus_start)
    # A wait that would wait 4 s for quiet, cancelled at once, and one that times out.
    sus_start = len(probe_events("s-qt"))
    sus_wait(sus_qt, "quiet 4000 deadline 4400 id hm-s-cancel", "/tmp/hm-s-cancel")
    wait_for("the window shown for the wait", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    sus_since = vm_now()
    assert json.loads(hypr("-j hyprmasq cancel hm-s-cancel"))["cancelled"]
    assert background_reply("/tmp/hm-s-cancel", timeout=10)["ended"] == "cancelled"
    wait_for("the window suspended once its wait was cancelled", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    assert sus_told("s-qt", sus_start)[-1][0] - sus_since < 1500, probe_events("s-qt", sus_start)
    sus_start = len(probe_events("s-qt"))
    assert sus_wait(sus_qt, "quiet 4000 deadline 1500")["ended"] == "timeout"
    wait_for("the window suspended once its wait timed out", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    assert [state for _, state in sus_told("s-qt", sus_start)] == ["shown", "suspended"], probe_events("s-qt", sus_start)

with subtest("a hold keeps the Qt window from being told while its workspace is shown and hidden, and gives it Hyprland's state after"):
    # The hold starts while the window is hidden, and the workspace is shown and hidden again meanwhile.
    sus_start = len(probe_events("s-qt"))
    sus_wait(sus_qt, "quiet 4000 deadline 4400 id hm-s-shown", "/tmp/hm-s-shown")
    wait_for("the window shown for the wait", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    dispatch('hl.dsp.focus({ workspace = "name:hm-s" })')
    wait_for("its workspace shown", lambda: json.loads(hypr("-j activeworkspace"))["name"] == "hm-s")
    focus_workspace(92)
    time.sleep(0.5)
    assert "suspended" not in [state for _, state in sus_told("s-qt", sus_start)], probe_events("s-qt", sus_start)
    assert json.loads(hypr("-j hyprmasq cancel hm-s-shown"))["cancelled"]
    wait_for("the window suspended once the hold ended", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    # The hold starts while the window's workspace is shown, which Hyprland hides during it.
    sus_start = len(probe_events("s-qt"))
    dispatch('hl.dsp.focus({ workspace = "name:hm-s" })')
    wait_for("the window shown with its workspace", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    sus_wait(sus_qt, "quiet 4000 deadline 4400 id hm-s-hidden", "/tmp/hm-s-hidden")
    time.sleep(0.5)
    focus_workspace(92)
    time.sleep(0.5)
    assert "suspended" not in [state for _, state in sus_told("s-qt", sus_start)], probe_events("s-qt", sus_start)
    assert json.loads(hypr("-j hyprmasq cancel hm-s-hidden"))["cancelled"]
    wait_for("the window suspended once the hold ended", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    assert snapshot() == (92, sus_user), snapshot()

with subtest("the holds of a session and a wait on the Qt window end in either order before it is told that it is suspended"):
    # The wait outlives the session's second after input.
    sus_path = open_socket(sus_qt)
    sus_start = len(probe_events("s-qt"))
    sus_wait(sus_qt, "quiet 4000 deadline 4400", "/tmp/hm-s-long")
    wait_for("the window shown for the wait", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    send(sus_path, "move", 20, 20)
    time.sleep(2)
    assert "suspended" not in [state for _, state in sus_told("s-qt", sus_start)], probe_events("s-qt", sus_start)
    background_reply("/tmp/hm-s-long", timeout=10)
    wait_for("the window suspended once the wait ended", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)
    # The session, whose sender holds its devices, outlives a wait.
    sus_start = len(probe_events("s-qt"))
    run_input_background("s-sender", sus_path, "move", 30, 30, "sleep", 4000)
    wait_for("the window shown for the sender", lambda: sus_now("s-qt", sus_start) == "shown", timeout=10)
    assert sus_wait(sus_qt, "quiet 4000 deadline 500")["ended"] == "timeout"
    time.sleep(0.5)
    assert "suspended" not in [state for _, state in sus_told("s-qt", sus_start)], probe_events("s-qt", sus_start)
    machine.wait_until_succeeds("test -s /tmp/s-sender.status", timeout=15)
    wait_for("the window suspended once the sender ended", lambda: sus_now("s-qt", sus_start) == "suspended", timeout=10)

with subtest("a held toplevel that unmaps gets Hyprland's state as it maps again, and an older one is never told that it is suspended"):
    # suspend-probe keeps its xdg_toplevel when it unmaps. Its socket closes with the unmap, and so does the hold of its
    # sender, which sends no configure to the unmapped toplevel.
    sus_raw = alice(f"{ipc} hyprmasq-launch --workspace hm-s-raw -- sh -c {shlex.quote('exec hyprmasq-suspend-probe hm-s-raw 6 > /tmp/hyprmasq-s-raw.log 2>&1')}").strip()
    # Hyprland tells a window that maps on a hidden workspace that it is suspended once the workspaces change.
    dispatch('hl.dsp.focus({ workspace = "name:hm-s-raw" })')
    wait_for("its workspace shown", lambda: json.loads(hypr("-j activeworkspace"))["name"] == "hm-s-raw")
    focus_workspace(92)
    wait_for("the probe suspended", lambda: sus_now("s-raw") == "suspended", timeout=10)
    sus_start = len(probe_events("s-raw"))
    run_input_background("s-raw", open_socket(sus_raw), "move", 10, 10, "sleep", 20000)
    wait_for("the probe shown for the sender", lambda: sus_now("s-raw", sus_start) == "shown", timeout=10)
    machine.succeed("pkill -USR1 -f '^hyprmasq-suspend-probe hm-s-raw '")
    wait_for("the probe unmapped", lambda: any(e == "unmapped" for _, e in probe_events("s-raw", sus_start)), timeout=10)
    time.sleep(1)
    assert not any(e.endswith(" unmapped") and e.startswith("configure ") for _, e in probe_events("s-raw", sus_start)), probe_events("s-raw", sus_start)
    sus_start = len(probe_events("s-raw"))
    machine.succeed("pkill -USR2 -f '^hyprmasq-suspend-probe hm-s-raw '")
    wait_for("a configure as it maps again", lambda: sus_now("s-raw", sus_start) is not None, timeout=10)
    # It maps with the state that Hyprland told it last, also when it maps on the visible workspace, where Hyprland
    # updates it only once the workspaces change. Moved to a hidden workspace, which updates nothing for a window
    # that Hyprland already took for suspended, it is told that it is suspended: the hold left nothing behind.
    dispatch(f'hl.dsp.window.move({{ window = "address:{sus_raw}", workspace = "name:hm-s-raw", follow = false }})')
    wait_for("the probe hidden", lambda: next(c for c in clients() if c["title"] == "hm-s-raw")["workspace"]["name"] == "hm-s-raw")
    time.sleep(0.5)
    assert sus_now("s-raw") == "suspended", probe_events("s-raw", sus_start)
    # An xdg_wm_base of version 5 has no suspended state. A hold on such a toplevel while it is shown, which Hyprland
    # hides during it, and that ends after the toplevel unmapped, tells it nothing either.
    sus_old = alice(f"{ipc} hyprmasq-launch --workspace hm-s-old -- sh -c {shlex.quote('exec hyprmasq-suspend-probe hm-s-old 5 > /tmp/hyprmasq-s-old.log 2>&1')}").strip()
    wait_for("the old probe mapped", lambda: any(e == "mapped" for _, e in probe_events("s-old")), timeout=10)
    open_socket(sus_old)
    dispatch('hl.dsp.focus({ workspace = "name:hm-s-old" })')
    wait_for("its workspace shown", lambda: json.loads(hypr("-j activeworkspace"))["name"] == "hm-s-old")
    sus_wait(sus_old, "quiet 4000 deadline 4400", "/tmp/hm-s-old")
    time.sleep(0.5)
    focus_workspace(92)
    machine.succeed("pkill -USR1 -f '^hyprmasq-suspend-probe hm-s-old '")
    assert background_reply("/tmp/hm-s-old", timeout=10)["ended"] == "window_closed"
    machine.succeed("pkill -USR2 -f '^hyprmasq-suspend-probe hm-s-old '")
    wait_for("the old probe mapped again", lambda: [e for _, e in probe_events("s-old")].count("mapped") == 2, timeout=10)
    time.sleep(1)
    assert sus_told("s-old") and "suspended" not in [state for _, state in sus_told("s-old")], probe_events("s-old")
    machine.succeed("pkill -f '^hyprmasq-suspend-probe hm-s-'")
    wait_for("the probes closed", lambda: client("hm-s-raw") is None and client("hm-s-old") is None)
    focus_workspace(92)
    assert snapshot() == (92, sus_user), snapshot()

assert sus_agent.stop() == 0
dispatch(f'hl.dsp.window.close({{ window = "address:{sus_beside}" }})')
wait_for("hm-s-beside closed", lambda: client("hm-s-beside") is None)
machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == sus_user)}")
wait_for("hm-s-user closed", lambda: client("hm-s-user") is None)
output_mode(640, 480)
focus_workspace(sus_before[0])
