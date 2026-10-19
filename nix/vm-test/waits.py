with subtest("waiting until a window has settled"):
    focus_workspace(19)
    waited = foot("hm-wait")
    wait_drawn(waited, 1000)
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


def hypr_socket_background(request, output):
    alice(f"rm -f {output}; {ipc} nohup python3 {socket_client} {shlex.quote(request)} {output} > /dev/null 2>&1 &")


with subtest("input tool version"):
    assert alice("hyprmasq-input --version").strip() == f"hyprmasq-input {expected_version}"

with subtest("plugin version and window state"):
    assert json.loads(hypr("-j hyprmasq version")) == {"ok": True, "version": expected_version, "interface": 4}
    assert hypr("hyprmasq version") == f"hyprmasq {expected_version} interface 4"
    # The module installs the MCP server of the same version, which runs the package's own hyprmasq-input.
    package = plugin.removesuffix("/lib/hyprmasq.so")
    assert alice("hyprmasq-mcp --version").strip() == expected_version
    assert alice("readlink -f $(command -v hyprmasq-mcp)").strip() == f"{package}/bin/hyprmasq-mcp"
    assert alice("readlink -f $(command -v hyprmasq-input)").strip() == f"{package}/bin/hyprmasq-input"
    machine.succeed(f"grep -q {shlex.quote(package + '/bin')} {package}/bin/hyprmasq-mcp")
    focus_workspace(30)
    stated = foot("hm-p-state")
    wait_drawn(stated, 1000)
    focus_workspace(4)
    reply = window_state(stated)
    window = client("hm-p-state")
    assert reply["ok"] and reply["address"] == stated and reply["stableId"] == window["stableId"], (reply, window)
    assert not reply["xwayland"] and [reply["size"]["width"], reply["size"]["height"]] == window["size"], (reply, window)
    assert reply["resizing"] is False, reply
    assert reply["scale"] == 1 and reply["socket"] is None and reply["commits"] is None and reply["lease"] is None, reply
    # A hidden window on a connection of its own, while another app has the real focus.
    assert reply["capabilities"] == {"lease": True, "hold": True, "dnd": False, "privateDnd": True, "keyboard": "yes"}, reply
    stated_path = open_socket(stated)
    reply = window_state(stated)
    assert reply["socket"] == stated_path and isinstance(reply["commits"], int) and reply["commits"] >= 0, reply
    assert reply["drag"] == {"id": 0, "phase": "none", "private": False, "reason": None, "started": 0, "finished": 0, "cancelled": 0, "unconfirmed": 0,
                             "ended": []}, reply
    assert "\ndrag: 0 none started=0 finished=0 cancelled=0 unconfirmed=0\n" in hypr(f"hyprmasq state {stated}")
    # The focused window has the real focus on its own connection.
    assert window_state(visible)["capabilities"] == {"lease": False, "hold": False, "dnd": True, "privateDnd": False, "keyboard": "yes"}

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
        (f"wait {stated} since 0 ignore spinners", "invalid_arguments"),
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
    shot_reply = json.loads(hypr(f"-j hyprmasq screenshot {stated} /tmp/hyprmasq-p-counted.png"))
    after = window_state(stated)["commits"]
    assert readings == sorted(readings) and readings[-1] <= shot_reply["commits"] <= after, (before, readings, shot_reply, after)
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
    shot_reply = json.loads(hypr(f"-j hyprmasq screenshot {stated} /tmp/hyprmasq-p-socket-id.png"))
    assert shot_reply["socketId"] == first, shot_reply
    reply = json.loads(hypr(f"-j hyprmasq wait {stated} since {shot_reply['commits']} quiet 50 deadline 1000"))
    assert reply["socketId"] == first, reply
    assert json.loads(hypr(f"-j hyprmasq screenshot {windows[1][1]} /tmp/hm-p-green.png"))["socketId"] is None
    assert window_state(windows[1][1])["socketId"] is None
    # Opened again, the socket has another id, and its counter starts again.
    assert hypr(f"hyprmasq eis close {stated}") == "ok"
    reopened = json.loads(hypr(f"-j hyprmasq eis open {stated}"))
    assert reopened["path"] == stated_path and reopened["socketId"] not in (None, first), reopened
    state = window_state(stated)
    assert state["socketId"] == reopened["socketId"] and state["commits"] < shot_reply["commits"], (state, shot_reply)

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
    # So it does when the wait ignores animations, since the shell's redraw was none.
    reply = wait_since(stated, f"since {base - 1} expect 4000 quiet 100 ignore animations")
    assert reply["ended"] == "quiet" and reply["redrawObserved"] and reply["elapsedMs"] < 1000, reply
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

with subtest("a wait for a change without a deadline answers before hyprctl gives up"):
    # hyprctl stops reading after 5 seconds, so the default deadline is 4500 ms.
    time.sleep(1.5)
    base = window_state(stated)["commits"]
    started = time.monotonic()
    reply = json.loads(hypr(f"-j hyprmasq wait {stated} since {base} expect 10000"))
    took = time.monotonic() - started
    assert reply["ok"] and reply["ended"] == "timeout" and not reply["redrawObserved"], reply
    assert 4500 <= reply["elapsedMs"] < 4900 and took < 5, (reply, took)

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
    assert json.loads(hypr("-j hyprmasq eis list")) == {"ok": True, "sockets": []}
    assert window_state(stated)["commits"] is None

with subtest("unloading removes the socket directories that the plugin made, while nothing else is in them"):
    root = f"{runtime}/hyprmasq"
    # Another instance's directory keeps the shared one.
    path = open_socket(stated)
    alice(f"mkdir -p -m 700 {root}/hm-other-instance")
    assert hypr(f"plugin unload {plugin}") == "ok"
    machine.succeed(f"test ! -e {path} && test ! -e {path}.lock && test ! -e {instance_dir} && test -d {root}/hm-other-instance")
    assert hypr(f"plugin load {plugin}") == "ok"
    # Another program's file, as hyprmasq-mcp's locks, keeps the instance's directory, and the plugin's go.
    path = open_socket(stated)
    alice(f"touch {instance_dir}/hm-keep.lock")
    assert hypr(f"plugin unload {plugin}") == "ok"
    assert machine.succeed(f"ls -A {instance_dir}").split() == ["hm-keep.lock"]
    assert hypr(f"plugin load {plugin}") == "ok"
    # Made by the plugin and left empty, both go.
    alice(f"rm {instance_dir}/hm-keep.lock && rmdir {instance_dir} {root}/hm-other-instance {root}")
    path = open_socket(stated)
    assert hypr(f"plugin unload {plugin}") == "ok"
    machine.succeed(f"test ! -e {root}")
    assert hypr(f"plugin load {plugin}") == "ok"
    assert json.loads(hypr("-j hyprmasq eis list")) == {"ok": True, "sockets": []}
