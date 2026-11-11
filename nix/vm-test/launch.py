with subtest("launcher handles failed and invalid compositor replies"):
    # Mock only hyprctl. Run the installed launcher and jq, including the package wrapper.
    # This makes a compositor disappearing mid-wait deterministic without unloading the plugin.
    alice("python3 /etc/hyprmasq-test/launch-checks.py")

with subtest("launching applications on a hidden workspace"):
    focus_workspace(4)
    before_focus = snapshot()

    def launched(address):
        return next(c for c in clients() if c["address"] == address)

    # The title passes through the shell and a Lua string unchanged. The shell in foot changes the title later.
    title = "hm-launched \"quoted\" it's \\ back"
    address = alice(f"{ipc} hyprmasq-launch -- foot -T {shlex.quote(title)}").strip()
    window = launched(address)
    assert window["initialTitle"] == title and window["workspace"]["name"] == "hyprmasq", window
    assert snapshot() == before_focus
    # A window opened by a child process is found, and each of two launches at once gets its own window.
    alice(
        f"{ipc} hyprmasq-launch --workspace hm-launch -- sh -c 'sleep 0.5; foot -T hm-child; true' > /tmp/hm-launch-a & "
        f"{ipc} hyprmasq-launch --workspace hm-launch -- foot -T hm-direct > /tmp/hm-launch-b & wait"
    )
    child = launched(machine.succeed("cat /tmp/hm-launch-a").strip())
    direct = launched(machine.succeed("cat /tmp/hm-launch-b").strip())
    assert child["initialTitle"] == "hm-child" and direct["initialTitle"] == "hm-direct", (child, direct)
    assert child["workspace"]["name"] == direct["workspace"]["name"] == "hm-launch", (child, direct)
    assert snapshot() == before_focus
    screenshot(direct["address"], "launched")
    status, output = machine.execute(
        "su - alice -c " + shlex.quote(f"{ipc} hyprmasq-launch --timeout 2 -- true 2>&1")
    )
    assert status == 1 and "no window" in output, (status, output)

    # Children whose environment lacks Hyprland's variables, as a browser's that its launcher script starts without
    # exec and that overwrites its environment: the plugin finds them by their parent, the launched process, and
    # places them before they map, without the focus or the visible workspace changing.
    cleared = "env -u HL_EXEC_RULE_TOKEN -u HL_INITIAL_WORKSPACE_TOKEN"
    orphans = {}
    for title, command in [("hm-cleared", f"{cleared} foot -T hm-cleared"), ("hm-cleared-x", f"{cleared} xterm -T hm-cleared-x")]:
        found = alice(f"{ipc} hyprmasq-launch --workspace hm-launch -- sh -c {shlex.quote(command + '; true')}").strip()
        orphans[title] = launched(found)
        assert orphans[title]["initialTitle"] == title and orphans[title]["workspace"]["name"] == "hm-launch", orphans[title]
        assert snapshot() == before_focus
    assert orphans["hm-cleared-x"]["xwayland"], orphans
    # So is the second window of an app, each window with a rule of its own.
    first = alice(
        f"{ipc} hyprmasq-launch --workspace hm-launch -- sh -c "
        + shlex.quote(f"foot -T hm-first & sleep 1; {cleared} foot -T hm-second; wait")
    ).strip()
    def second_window():
        return next((c for c in clients() if c["initialTitle"] == "hm-second"), None)

    wait_for("hm-second", lambda: second_window() is not None)
    second = second_window()
    assert launched(first)["initialTitle"] == "hm-first" and second["workspace"]["name"] == "hm-launch", second
    assert snapshot() == before_focus
    # Words that hyprctl and Hyprland look for anywhere in a request reach the application unchanged, in hex: a
    # title of /instances, rollinglog -f and /--batch;kill; would otherwise list instances, keep the connection open
    # or kill a window. A workspace that starts with - is no option of hyprctl's.
    title = "/instances rollinglog -f /--batch;kill;"
    edge = launched(alice(f"{ipc} hyprmasq-launch --workspace -dash -- foot -T {shlex.quote(title)}").strip())
    assert edge["initialTitle"] == title and edge["workspace"]["name"] == "-dash", edge
    dispatch('hl.dsp.window.close({ window = "address:' + edge["address"] + '" })')
    tokens = {w["address"]: w["token"] for w in json.loads(hypr("-j hyprmasq launched"))["windows"]}
    assert tokens[first] == tokens[second["address"]] and tokens[first] != tokens[orphans["hm-cleared"]["address"]], tokens
    for window in [address, child["address"], direct["address"], first, second["address"], *(o["address"] for o in orphans.values())]:
        dispatch('hl.dsp.window.close({ window = "address:' + window + '" })')
    wait_for("no launched windows", lambda: hypr("hyprmasq launched") == "no launched windows")

with subtest("hidden wev pointer events"):
    focus_workspace(6)
    spawn("stdbuf -oL wev", "wev")
    wait_for("wev", lambda: any(c["class"] == "wev" for c in clients()))
    target = next(c["address"] for c in clients() if c["class"] == "wev")
    focus_workspace(4)
    before_focus = snapshot()
    # Discard events from mapping and the real focus leaving.
    machine.succeed("cp /tmp/hyprmasq-wev.log /tmp/hyprmasq-wev-initial.log")
    initial = len(machine.succeed("cat /tmp/hyprmasq-wev.log"))
    path = open_socket(target)
    send(path, "move", 50, 60, "click", "scroll", 0, 25)
    time.sleep(0.5)
    events = machine.succeed("cat /tmp/hyprmasq-wev.log")[initial:]
    for event in ["wl_pointer", "enter", "motion", "button", "axis"]:
        assert event in events, events
    assert snapshot() == before_focus

with subtest("two footclient windows on one Wayland connection"):
    spawn("foot --server=/tmp/hyprmasq-foot.sock", "foot-server")
    machine.wait_until_succeeds("test -S /tmp/hyprmasq-foot.sock")
    focus_workspace(7)
    shared_hidden = foot(
        "hm-shared-hidden", executable="footclient -s /tmp/hyprmasq-foot.sock"
    )
    focus_workspace(8)
    shared_visible = foot(
        "hm-shared-visible",
        "sh -c " + shlex.quote("stty -echo; cat > /tmp/hyprmasq-visible-input"),
        executable="footclient -s /tmp/hyprmasq-foot.sock",
    )
    # Both clients report the foot server's pid.
    assert client("hm-shared-hidden")["pid"] == client("hm-shared-visible")["pid"]
    wait_for("shared visible focus", lambda: snapshot() == (8, shared_visible))
    before_focus = snapshot()
    before = screenshot(shared_hidden, "shared-before")
    path = open_socket(shared_hidden)
    send(path, "type", "echo OK > /tmp/shared-marker", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/shared-marker)" = OK')
    time.sleep(1)
    assert snapshot() == before_focus
    machine.succeed(
        "test -f /tmp/hyprmasq-visible-input && test ! -s /tmp/hyprmasq-visible-input"
    )
    after = screenshot(shared_hidden, "shared-after")
    machine.fail(f"cmp -s {before} {after}")
    # Another connection must receive fresh modifier state, with lowercase intact.
    send(path, "type", "echo again > /tmp/shared-again", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/shared-again)" = again')
    assert snapshot() == before_focus
    machine.succeed("test ! -s /tmp/hyprmasq-visible-input")

watch_events("ipc-events")

with subtest("a window that emulated input opens opens silently next to the driven window"):
    # The foot server opens the windows of footclients that the hidden shell starts: windows of the driven window's
    # process, as a browser's popups are, in an app that no launch started.
    def open_from_hidden(title, delay=0):
        command = f"sleep {delay}; footclient -s /tmp/hyprmasq-foot.sock -T {title} sleep 600"
        send(path, "type", f"({command}) &", "key", 28)
        wait_for(title, lambda: client(title) is not None, timeout=delay + 30)
        return client(title)

    hidden_workspace = client("hm-shared-hidden")["workspace"]["id"]
    focus_workspace(60)
    other = foot("hm-opener-other")
    wait_for("other app focused", lambda: snapshot() == (60, other))
    # With the user's focus in another app, the window opens on the driven window's workspace without the focus.
    opened = open_from_hidden("hm-opened-other-app")
    assert opened["workspace"]["id"] == hidden_workspace, opened
    assert snapshot() == (60, other)
    # Placed windows of an app that no launch started are not listed as launched.
    assert hypr("hyprmasq launched") == "no launched windows"
    # On the workspace the user is on, where Hyprland drops a workspace rule with its silent, the focus stays too.
    focus_workspace(hidden_workspace)
    beside = foot("hm-opener-beside")
    wait_for("app beside focused", lambda: snapshot() == (hidden_workspace, beside))
    opened_beside = open_from_hidden("hm-opened-beside")
    assert opened_beside["workspace"]["id"] == hidden_workspace, opened_beside
    assert snapshot() == (hidden_workspace, beside)
    focus_workspace(60)
    wait_for("other app focused", lambda: snapshot() == (60, other))
    # Input more than 5 seconds before the window opens did not open it: it opens as usual, with the focus.
    late = open_from_hidden("hm-opened-late", delay=6)
    assert late["workspace"]["id"] == 60, late
    wait_for("late window focused", lambda: snapshot() == (60, late["address"]))
    # With the user's focus in the same app and no key of theirs since the input, the input opened it.
    focus_workspace(8)
    wait_for("shared visible focus", lambda: snapshot() == (8, shared_visible))
    opened_same = open_from_hidden("hm-opened-same-app")
    assert opened_same["workspace"]["id"] == hidden_workspace, opened_same
    assert snapshot() == (8, shared_visible)
    # A real click after the input means the user's own input opened it, also in the same app.
    send(path, "type", "(sleep 2; footclient -s /tmp/hyprmasq-foot.sock -T hm-opened-user sleep 600) &", "key", 28)
    (x, y), (width, height) = client("hm-shared-visible")["at"], client("hm-shared-visible")["size"]
    dispatch(f"hl.dsp.cursor.move({{ x = {x + width // 2}, y = {y + height // 2} }})")
    alice(f"{client_env} wlrctl pointer click left")
    wait_for("hm-opened-user", lambda: client("hm-opened-user") is not None)
    user = client("hm-opened-user")
    assert user["workspace"]["id"] == 8, user
    wait_for("user window focused", lambda: snapshot() == (8, user["address"]))
    for title in [
        "hm-opened-other-app", "hm-opened-beside", "hm-opened-late", "hm-opened-same-app", "hm-opened-user", "hm-opener-other", "hm-opener-beside"
    ]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
    wait_for("opened windows closed", lambda: not any(c["title"].startswith(("hm-opened-", "hm-opener-")) for c in clients()))
    dispatch(f'hl.dsp.focus({{ window = "address:{shared_visible}" }})')
    wait_for("shared visible focus", lambda: snapshot() == (8, shared_visible))
    machine.succeed("test ! -s /tmp/hyprmasq-visible-input")
