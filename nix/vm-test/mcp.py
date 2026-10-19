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
    assert (init["serverInfo"]["name"], init["serverInfo"]["version"]) == ("hyprmasq-mcp", expected_version), init
    # The first line sums it all up, for clients that show only that.
    summary, blank = init["instructions"].splitlines()[:2]
    assert summary.startswith("hyprmasq: ") and summary.endswith(".") and blank == "", init["instructions"]
    assert all(word in summary for word in ["click", "type", "act", "screenshot"]), summary
    tools = {tool["name"]: tool for tool in mcp.request("tools/list")["tools"]}
    assert sorted(tools) == ["act", "close", "launch", "release", "screenshot", "windows"], tools
    for name, tool in tools.items():
        assert tool["description"] and tool["inputSchema"]["type"] == "object", tool
        # windows takes a shot of the one window it lists, which opens the window's socket, as screenshot does.
        assert not tool.get("annotations", {}).get("readOnlyHint", False), tool
        assert tool.get("annotations", {}).get("destructiveHint", True) == (name not in ("windows", "screenshot", "launch", "release")), tool
    windows_schema = tools["windows"]["inputSchema"]
    assert set(windows_schema["properties"]) == {"match", "wait_ms"} and windows_schema["properties"]["wait_ms"]["maximum"] == 10000, windows_schema
    act_schema = tools["act"]["inputSchema"]
    assert act_schema["required"] == ["window"], act_schema
    assert set(act_schema["properties"]) == {"window", "shot", "actions", "wait", "capture", "deadline_ms"}, act_schema
    assert tools["screenshot"]["inputSchema"]["required"] == ["window"], tools["screenshot"]
    assert tools["launch"]["inputSchema"]["required"] == ["command"], tools["launch"]

with subtest("hyprmasq-mcp refuses a runtime directory with whitespace"):
    # The plugin splits a request into words at whitespace, and a screenshot request names a file in the directory.
    spaced = f"XDG_RUNTIME_DIR='/tmp/run time' HYPRLAND_INSTANCE_SIGNATURE={instance}"
    status, output = machine.execute("su - alice -c " + shlex.quote(f"{spaced} hyprmasq-mcp < /dev/null 2>&1"))
    assert status == 1 and "/tmp/run time/hyprmasq/" in output and "contains whitespace, which a request to the plugin cannot carry" in output, (status, output)

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
        "capabilities": {"lease": True, "hold": True, "dnd": True, "keyboard": "yes"},
    }, entry
    # The line leaves out the pid and the capabilities, which the window has all of.
    assert line == f'{shell} · foot "hm-m-shell" · workspace hm-m (hidden) · 640×480 · launched', line
    entry, _ = listed(mcp, visible)
    # The active window shares the user's keyboard focus, so act cannot drag in it, and its drags would run with the
    # user's pointer.
    assert entry["visible"] and not entry["launched"] and entry["capabilities"] == {"lease": False, "hold": False, "dnd": False, "keyboard": "yes"}, entry

with subtest("hyprmasq-mcp launches an app whose launcher forks it away and exits"):
    # As kate without -b and codium without --wait: the launched shell starts foot in a new session from a shell
    # that exits at once, and exits itself, so foot has lost its parent before its window maps. The window is still
    # the launch's, and opens hidden, while the user types into their terminal.
    typist = foot("hm-m-detach-typist", "sh -c 'cat > /tmp/hm-m-detach-typed.txt'")
    dispatch(f'hl.dsp.focus({{ window = "address:{typist}" }})')
    wait_for("typist focused", lambda: snapshot() == (4, typist))
    launcher = (
        "echo $$ > /tmp/hm-m-detach-pids; "
        "sh -c 'echo $$ >> /tmp/hm-m-detach-pids; setsid sh -c \"sleep 1; exec foot -T hm-m-detached sleep 600\" < /dev/null > /dev/null 2>&1 &'"
    )
    call = mcp.start("launch", {"command": ["sh", "-c", launcher], "workspace": "hm-m-detach", "timeout_ms": 20000})
    lines = [f"user line {n} typed during the launch" for n in range(3)]
    machine.send_chars("".join(line + "\n" for line in lines), delay=0.04)
    detached = mcp.result(call)
    assert not detached.is_error, detached
    keep(detached, "launched-detached")
    assert (detached.data["window"]["title"], detached.data["window"]["workspace"]) == ("hm-m-detached", "hm-m-detach"), detached
    assert client("hm-m-detached")["workspace"]["name"] == "hm-m-detach" and snapshot() == (4, typist), client("hm-m-detached")
    # Both shells of the launcher exited.
    for pid in machine.succeed("cat /tmp/hm-m-detach-pids").split():
        machine.fail(f"kill -0 {pid}")
    wait_for("the typing", lambda: machine.succeed("cat /tmp/hm-m-detach-typed.txt") == "".join(line + "\n" for line in lines), timeout=20)
    closed = mcp.call("close", {"window": detached.data["window"]["window"]})
    assert not closed.is_error and closed.data["closed"], closed
    dispatch(f'hl.dsp.window.close({{ window = "address:{typist}" }})')
    wait_for("typist closed", lambda: client("hm-m-detach-typist") is None)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("focus back", lambda: snapshot() == (4, visible))

with subtest("hyprmasq-mcp gives Claude Code the text in place of structured content"):
    # Claude Code passes the model a result's structured content in place of its text, so it gets none.
    claude = Mcp("claude")
    claude.initialize(client="claude-code")
    # The shell was launched by the other server, so this one does not list it as launched.
    # It lists one window, which comes with its shot.
    matched = claude.call("windows", {"match": "HM-M-SHELL"})
    assert matched.data is None and len(matched.images) == 1, matched
    assert re.fullmatch(rf'{shell} · foot "hm-m-shell" · workspace hm-m \(hidden\) · 640×480\nshot s\d+ 640×480 of window {shell}', matched.text), matched
    none = claude.call("windows", {"match": "hm-m-nothing"})
    assert none.data is None and re.fullmatch(r'no window has "hm-m-nothing" in its class or title, of \d+ windows', none.text), none
    shot = claude.call("screenshot", {"window": shell})
    assert shot.data is None and len(shot.images) == 1 and re.fullmatch(rf"shot s\d+ 640×480 · window {shell} 640×480", shot.text), shot
    failed = claude.call("act", {"window": shell, "deadline_ms": 1})
    assert failed.is_error and failed.data is None and failed.text.startswith("error invalid_arguments: deadline_ms must be 1000 to 30000"), failed
    assert claude.stop() == 0
    # Other clients get it, and match there too. An empty list says why in it as well.
    matched = mcp.call("windows", {"match": "hm-m-shell"})
    matched_shot = matched.data["windows"][0]["shot"]
    assert [w["window"] for w in matched.data["windows"]] == [shell] and (matched_shot["width"], matched_shot["height"]) == (640, 480), matched
    assert matched.text == f'{line}\nshot {matched_shot["id"]} 640×480 of window {shell}' and matched.data["summary"] == matched.text.splitlines()[1], matched
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
    # It shows what changed since the shot that windows returned with the match, the session's last of the window, as
    # a screenshot's would be: a zoom into the lines of the command and its output.
    changed = typed_result.data["shot"]
    assert changed["changes"]["base"] == matched_shot["id"] and len(changed["changes"]["regions"]) == 1, typed_result
    zoom = changed["changes"]["regions"][0]
    assert re.fullmatch(
        rf"2/2 delivered · redraw observed · quiet \d+ ms · \d+ ms · shot {changed['id']} = {matched_shot['id']} \+ 1 change · lease (59|60) s\n"
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
    # Nothing changed since the shot, so there is no image, and the hint points at the shot the window still shows. A
    # click may also have opened a window of another process that maps later, which the hint says too.
    later = "; it may also have opened a window that maps later, as a file chooser of another process can a second or two after the input: windows with wait_ms waits for it, and your next act or screenshot returns it"
    assert re.fullmatch(
        rf"1/1 delivered · no redraw within 1000 ms · shot s\d+ = {overview_id}, no change · lease (59|60) s\n"
        rf"The click may have missed or had no visible effect{re.escape(later)}\. Look at {overview_id} before repeating it\.",
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
    assert entry["capabilities"] == {"lease": False, "hold": False, "dnd": False, "keyboard": "yes"}, entry
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

with subtest("hyprmasq-mcp says when the user's typing took the shared keyboard focus back shortly after a shortcut"):
    # The shortcuts probe as a browser of the user's, with their window focused and the agent's hidden. A shortcut in
    # the hidden window takes effect while the user does not type into the app. Keys of theirs within a quarter of a
    # second of it take the keyboard back first, and act reports that event without saying whether the shortcut ran.
    m_user, m_agent = shortcut_probe("m-sc", "hm-m-sc-user", "hm-m-sc-agent", 67, 68)
    entry, _ = listed(mcp, m_agent)
    assert entry["capabilities"] == {"lease": False, "hold": False, "dnd": False, "keyboard": "yes"}, entry
    start = len(probe_events("m-sc"))
    alone = mcp.call("act", {"window": m_agent, "actions": [{"action": "key", "keys": ["ctrl", "l"]}]})
    assert not alone.is_error and "interrupted" not in alone.data["actions"] and "shared_focus_returned" not in alone.data["actions"], alone
    assert alone.text.startswith("1/1 delivered · redraw observed") and "may not have run" not in alone.text, alone
    assert [e for _, e in probe_events("m-sc", start) if e.startswith("shortcut ")] == ["shortcut hm-m-sc-agent 4 38"]
    user_text = "asdfghjklasdfghjkl"
    for attempt in range(5):
        start = len(probe_events("m-sc"))
        call = mcp.start("act", {"window": m_agent, "actions": [{"action": "pause", "ms": 300}, {"action": "key", "keys": ["ctrl", "l"]}]})
        machine.send_chars(user_text, delay=0.03)
        result = mcp.result(call)
        assert not result.is_error and result.data["actions"]["delivery"] == "complete", result
        wait_for("the user's keys", lambda: len(keys_of(probe_events("m-sc", start), "hm-m-sc-user")) == len(user_text), timeout=10)
        events = probe_events("m-sc", start)
        assert keys_of(events, "hm-m-sc-user") == [letter_codes[c] for c in user_text] and keys_of(events, "hm-m-sc-agent") == [29, 38], events
        shortcuts = [e for _, e in events if e.startswith("shortcut ")]
        assert shortcuts in (["shortcut hm-m-sc-agent 4 38"], ["shortcut hm-m-sc-agent 4 38 inactive"]), events
        if shortcuts[0].endswith(" inactive"):
            actions = result.data["actions"]
            assert actions["interrupted"] is True and actions["shared_focus_returned"] == {"effect": "unknown"}, result
            assert result.text.startswith("2/2 delivered · shared keyboard focus returned for user input · "), result
            assert result.data["hint"].startswith(
                "The user's input took this app's shared keyboard focus back from this window shortly after a shortcut"
                " or function key of the batch, so it may or may not have taken effect. Look at "
            ), result
            break
    else:
        raise AssertionError("the user's typing never came within a shortcut")
    assert snapshot() == (67, m_user)
    for address in (m_agent, m_user):
        dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
    wait_for("shortcut probe closed", lambda: all(c["address"] not in (m_agent, m_user) for c in clients()))
    focus_workspace(4)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp says that a shortcut beside the user's window may have run too late, and only there"):
    # The shortcuts probe as a browser of the user's whose shortcuts run 500 ms after their key, later than the quarter
    # of a second for which the agent's window keeps the connection's keyboard focus. The shortcut then finds the
    # user's window active and does nothing, so the agent's window draws nothing, and act says why the shortcut may
    # not have run. The user's focus and typing stay theirs.
    late_hint = ("This app shares its keyboard focus between this window and the user's, which gets it back about 250 ms after a shortcut or function key,"
                 " so one that the app handles later may not have run. Use a menu or a button instead, or launch a separate instance of the app with launch.")
    late_user, late_agent = shortcut_probe("m-late", "hm-m-late-user", "hm-m-late-agent", 78, 79, ms=500)
    entry, _ = listed(mcp, late_agent)
    assert entry["capabilities"] == {"lease": False, "hold": False, "dnd": False, "keyboard": "yes"}, entry
    start = len(probe_events("m-late"))
    late = mcp.call("act", {"window": late_agent, "actions": [{"action": "key", "keys": ["ctrl", "l"]}]})
    assert not late.is_error and late.data["actions"] == {"done": 1, "total": 1, "delivery": "complete"}, late
    assert late.data["wait"]["ended"] == "no_redraw" and late.data["hint"].endswith(" " + late_hint) and late_hint in late.text, late
    wait_for("the late shortcut", lambda: any(e.startswith("shortcut ") for _, e in probe_events("m-late", start)), timeout=10)
    machine.send_chars("ab", delay=0.05)
    wait_for("the user's keys", lambda: keys_of(probe_events("m-late", start), "hm-m-late-user") == [30, 48], timeout=10)
    events = probe_events("m-late", start)
    assert [e for _, e in events if e.startswith("shortcut ")] == ["shortcut hm-m-late-agent 4 38 inactive"], events
    assert keys_of(events, "hm-m-late-agent") == [29, 38], events
    assert snapshot() == (78, late_user)
    # With the user's focus in another app, the window gets a lease, which keeps the keyboard focus until the shortcut
    # has run: it redraws, and there is no such hint.
    focus_workspace(4)
    start = len(probe_events("m-late"))
    alone = mcp.call("act", {"window": late_agent, "actions": [{"action": "key", "keys": ["ctrl", "l"]}]})
    assert not alone.is_error and alone.data["wait"]["redraw_observed"] and late_hint not in alone.text, alone
    assert [e for _, e in probe_events("m-late", start) if e.startswith("shortcut ")] == ["shortcut hm-m-late-agent 4 38"]
    assert snapshot() == before_focus
    for address in (late_agent, late_user):
        dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
    wait_for("shortcut probe closed", lambda: all(c["address"] not in (late_agent, late_user) for c in clients()))
    # A window of its own connection that a shortcut leaves as it was gets only the hint about the missing redraw.
    focus_workspace(80)
    spawn("hyprmasq-focus-probe focus hm-m-own", "m-own")
    wait_for("own probe", lambda: client("hm-m-own") is not None)
    focus_workspace(4)
    own = client("hm-m-own")["address"]
    unseen = mcp.call("act", {"window": own, "actions": [{"action": "key", "keys": ["ctrl", "l"]}]})
    assert not unseen.is_error and unseen.data["wait"]["ended"] == "no_redraw", unseen
    assert unseen.data["hint"].startswith("The keys may have gone to another widget") and late_hint not in unseen.text, unseen
    dispatch(f'hl.dsp.window.close({{ window = "address:{own}" }})')
    wait_for("own probe closed", lambda: client("hm-m-own") is None)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp drags in a hidden app's own drag-and-drop, and says how it ended"):
    # The drag probe on a hidden workspace has the dnd capability. A drag from its surface onto its subsurface drops
    # there, and the app finishes the drop: act reports it finished, after the wait that the drag kept going, and the
    # user's focus and pointer stay.
    m_dnd, _ = dnd_probe("m-dnd", "hm-m-dnd", workspace=83)
    expected, pointer = snapshot(), cursor()
    entry, line = listed(mcp, m_dnd)
    assert entry["capabilities"] == {"lease": True, "hold": True, "dnd": True, "keyboard": "yes"} and "lacks" not in line, (entry, line)
    dnd_shot = mcp.call("screenshot", {"window": m_dnd}).data["shot"]["id"]
    dropped = mcp.call("act", {"window": m_dnd, "shot": dnd_shot, "actions": [{"action": "drag", "from": [50, 100], "to": [300, 100]}]})
    assert not dropped.is_error and dropped.data["actions"]["drag_and_drop"] == {"outcome": "finished", "private": True}, dropped
    assert dropped.text.startswith("1/1 delivered · drag-and-drop finished · redraw observed · "), dropped
    events = dnd_events("m-dnd")
    assert f"payload {dnd_payload}" in events and "source finished" in events, events
    assert snapshot() == expected and cursor() == pointer, (snapshot(), cursor())
    close_probe(m_dnd)
    # A drop that the app refuses is cancelled, and the hint says why.
    m_dnd, _ = dnd_probe("m-dnd-refused", "hm-m-dnd-refused", "accept=none", workspace=83)
    dnd_shot = mcp.call("screenshot", {"window": m_dnd}).data["shot"]["id"]
    refused_drop = mcp.call("act", {"window": m_dnd, "shot": dnd_shot, "actions": [{"action": "drag", "from": [50, 100], "to": [300, 100]}]})
    assert refused_drop.data["actions"]["drag_and_drop"] == {"outcome": "cancelled", "private": True, "reason": "rejected"}, refused_drop
    assert " · drag-and-drop cancelled (rejected) · " in refused_drop.text, refused_drop
    assert refused_drop.data["hint"].startswith("The app's drag-and-drop was cancelled, since it ended over a place that took none of its types or actions"), refused_drop
    close_probe(m_dnd)
    # A drag that the app starts with a serial that no held press of the socket gave is cancelled before it starts:
    # the summary says so, the hint says what to do instead, and the wait ends without waiting for a redraw.
    m_dnd, _ = dnd_probe("m-dnd-serial", "hm-m-dnd-serial", "serial=enter", workspace=83)
    dnd_shot = mcp.call("screenshot", {"window": m_dnd}).data["shot"]["id"]
    protected = mcp.call("act", {"window": m_dnd, "shot": dnd_shot, "actions": [{"action": "drag", "from": [50, 100], "to": [300, 100]}]})
    assert protected.data["actions"]["drag_and_drop"] == {"outcome": "cancelled", "private": False, "reason": "serial"}, protected
    assert protected.text.startswith("1/1 delivered · drag-and-drop cancelled to protect the user's input · no redraw · quiet "), protected
    assert protected.data["wait"]["ended"] == "quiet" and protected.data["wait"]["elapsed_ms"] < 900, protected
    assert protected.data["hint"].startswith(
        "The app started a drag-and-drop, which hyprmasq cancelled to protect the user's input: the app started it from a press that hyprmasq did not send"
    ), protected
    close_probe(m_dnd)
    assert snapshot() == expected

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
        rf"0/1 delivered, delivery partial: actions\[0\] \(type\) may be partly applied \({keys} keys sent\) · shot s\d+ \d+×\d+",
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
    # The summary names no lease, and the field says that none is held.
    assert re.fullmatch(r"redraw observed · quiet \d+ ms · \d+ ms · shot s\d+ 640×480", changed.text), changed
    assert changed.data["lease"] == {"held": False, "remaining_s": 0}, changed
    assert near(pixels(keep(changed, "popup-opened"), (50, 40))[0], popup_rgb) and near(pixels(unopened.images[0], (50, 40))[0], window_rgb)
    # Since the shot that shows it, nothing changes.
    changed_id = changed.data["shot"]["id"]
    unchanged = mcp.call("act", {"window": popchange, "shot": changed_id, "wait": {"expect_ms": 500}, **full})
    assert unchanged.data["wait"]["ended"] == "no_redraw" and unchanged.data["wait"]["summary"] == "no_change", unchanged
    assert re.fullmatch(
        rf"no redraw within 500 ms · shot s\d+ 640×480\nNothing was drawn since shot {changed_id}\. "
        r"If the window is still loading, wait again: act with no actions, a larger wait\.expect_ms, and a deadline_ms at least 1000 above it\.",
        unchanged.text,
    ), unchanged
    # Without a shot, the change counts from the call's start, and the hint speaks of the call.
    unshot = mcp.call("act", {"window": popchange, "wait": {"expect_ms": 500}, **full})
    assert unshot.data["wait"]["ended"] == "no_redraw", unshot
    assert re.fullmatch(
        r"no redraw within 500 ms · shot s\d+ 640×480\nNothing was drawn during this call\. "
        r"If the window is still loading, wait again: act with no actions, a larger wait\.expect_ms, and a deadline_ms at least 1000 above it\.",
        unshot.text,
    ), unshot
    # A change phase that runs into the deadline times out, and the window drew nothing.
    late = mcp.call("act", {"window": popchange, "shot": changed_id, "wait": {"expect_ms": 2000}, "deadline_ms": 3000, **full})
    wait = late.data["wait"]
    assert not late.is_error and wait["ended"] == "timeout" and wait["summary"] == "no_change" and not wait["redraw_observed"], late
    assert re.fullmatch(
        r"timeout after \d+ ms, no redraw · shot s\d+ 640×480\n"
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
        r"timeout after \d+ ms, still drawing · shot s\d+ 640×480\n"
        r"The window kept drawing until the deadline, so it may be animating or still loading\. "
        r"Look at the shot; to wait longer, act with no actions and a larger deadline_ms \(at most 30000\)\.",
        timed.text,
    ), timed
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp does not wait for an animation, and names it"):
    # A digit at the top left of a foot window counts ten times a second, a small box that keeps redrawing. The
    # plugin takes it for an animation once it has redrawn for a second, so the wait settles long before the
    # deadline. Whether the digit's changes are left out depends on the digits that the second capture sees: two of
    # them can differ by too little to show that the box goes on changing, so the result may keep them. That an
    # animation's changes are left out is the next subtest's.
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
    # The plugin's wait with ignore animations, as act's watch after input runs it, waits for a commit that is not
    # part of an animation, so the digit does not end its change phase, as it ends one without.
    ignoring = json.loads(hypr(f"-j hyprmasq wait {ticking} since {window_state(ticking)['commits']} expect 1000 quiet 100 deadline 3000 ignore animations"))
    assert ignoring["ok"] and ignoring["ended"] == "no_redraw" and ignoring["elapsedMs"] >= 1000 and ignoring["animated"], ignoring
    counting = json.loads(hypr(f"-j hyprmasq wait {ticking} since {window_state(ticking)['commits']} expect 1000 quiet 100 deadline 3000"))
    assert counting["ok"] and counting["ended"] == "quiet" and counting["redrawObserved"] and counting["elapsedMs"] < 1000, counting
    keep(mcp.call("screenshot", {"window": ticking}), "ticking")
    assert mcp.call("close", {"window": ticking}).data["closed"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp keeps what a window draws in the box of an animation, and leaves out what goes on changing"):
    # focus-probe's tiles mode redraws at every frame callback, damaging the tile at 40,40 of 256×128 whole, though
    # only a spinner at 48,48 of 24×24, noise that is new at every frame, changes in it, as Thunderbird's renderer
    # damages tiles of 1024×512: the plugin takes the tile for an animation. A click draws a light block at 180,120 of
    # 96×32 in the same tile, which stays. It comes as a change, and the spinner, which a second capture shows still
    # changing, stays out.
    command = "exec hyprmasq-focus-probe tiles hm-m-tiles > /tmp/hyprmasq-m-tiles.log 2>&1"
    launched = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-tiles"})
    assert not launched.is_error, launched
    window, shot = launched.data["window"]["window"], launched.data["shot"]["id"]
    clicked = mcp.call("act", {"window": window, "shot": shot, "actions": [{"action": "click", "x": 500, "y": 400}]})
    machine.log(f"hyprmasq-mcp: a block drawn in the tile of an animation: {clicked}")
    assert not clicked.is_error and clicked.data["actions"]["delivery"] == "complete", clicked
    assert "content" in [e for _, e in probe_events("m-tiles")], probe_events("m-tiles")
    tile = clicked.data["wait"].get("animated", [])
    assert any(a["x"] <= 40 and a["y"] <= 40 and a["x"] + a["width"] >= 296 and a["y"] + a["height"] >= 168 for a in tile), clicked
    changes = clicked.data["shot"]["changes"]
    assert changes["base"] == shot and changes.get("animated") == 1, clicked
    # One zoom, around the block, which shows it, and none of the spinner.
    assert len(changes["regions"]) == 1 and len(clicked.images) == 1, clicked
    zoom = changes["regions"][0]
    assert zoom["x"] <= 180 and zoom["y"] <= 120 and zoom["x"] + zoom["width"] >= 276 and zoom["y"] + zoom["height"] >= 152, clicked
    assert zoom["x"] > 72 or zoom["y"] > 72, clicked
    assert near(pixels(keep(clicked, "tiles"), (228 - zoom["x"], 136 - zoom["y"]))[0], (0xEE, 0xEE, 0xEE)), clicked
    assert clicked.text.endswith("\n1 change in an animation left out"), clicked
    assert mcp.call("close", {"window": window}).data["closed"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp lets a click settle before the keys after it"):
    shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
    start = len(probe_events("m-probe"))
    keys = [{"action": "key", "keys": ["a"]}, {"action": "key", "keys": ["b"]}]
    settled = mcp.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 60, "y": 70}, *keys], "wait": {"expect_change": False}})
    assert not settled.is_error and settled.data["actions"] == {"done": 3, "total": 3, "delivery": "complete"}, settled
    stamps = {e: t for t, e in probe_input(start)}
    # The first key comes at least 300 ms after the click's release. The probe draws nothing in answer to the click,
    # so the wait for its answer ends after its short change phase, long before its bound of a second. That only the
    # first key waits is left to the Go tests.
    gap = stamps["key hm-m-probe 30 1"] - stamps["button hm-m-probe 0"]
    machine.log(f"a click that draws nothing: the first key {gap} ms after its release")
    assert 290 <= gap < 700, probe_input(start)
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
