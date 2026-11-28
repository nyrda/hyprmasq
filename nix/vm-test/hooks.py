with subtest("an idle hyprmasq holds no input hooks, and input waits for a hook of another plugin that cannot be shared"):
    hk_dir = machine.succeed("cat /etc/hyprmasq-hook-taker").strip()

    def hk_taker(kind):
        """A plugin that hooks CSeatManager::setKeyboardFocus (seat), CXWM::activateSurface (xwm) or
        CIdleNotifyProtocol::setInhibit (idle), as another plugin would. With -opaque, its hook is one that no other
        hook can join, and with -chain, the plugin shares hooks as hyprmasq does."""
        return f"{hk_dir}/hyprmasq-hook-taker-{kind}.so"

    def hk_load(path):
        """Loads a plugin, and returns hyprctl's answer: ok, or why not."""
        return machine.execute("su - alice -c " + shlex.quote(f"{ipc} hyprctl plugin load {path}"))[1].strip()

    def hk_unload(path):
        assert hypr(f"plugin unload {path}") == "ok"

    def hk_groups():
        return {g["name"]: g for g in json.loads(hypr("-j hyprmasq hooks"))["groups"]}

    def hk_installed(name):
        return hk_groups()[name]["installed"]

    def hk_out(*names):
        """Waits until none of the groups is installed, and shows why one stays."""
        try:
            wait_for(f"{', '.join(names)} out", lambda: not any(hk_installed(name) for name in names), timeout=10)
        except AssertionError:
            machine.log(hypr("-j hyprmasq hooks"))
            for entry in json.loads(hypr("-j hyprmasq eis list"))["sockets"]:
                machine.log(json.dumps(window_state(entry["address"])))
            raise

    def hk_refused(path, function):
        """Input to the socket, which the plugin refuses before anything arrives, naming the function."""
        status, result = input_json(path, "type", "echo refused > /tmp/hm-hk-refused", "key", 28)
        assert status == 3, (status, result)
        error = result["error"]
        assert error["code"] == "hook_held" and function in error["message"] and "another plugin" in error["message"], result
        assert result["delivery"] == "none" and result["done"] == 0, result

    hk_pid = machine.succeed(f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock").strip()

    def hk_hook_pages():
        """The bytes of Hyprland's anonymous mappings that may be written and executed, where its hooks' code is."""
        total = 0
        for line in machine.succeed(f"cat /proc/{hk_pid}/maps").splitlines():
            fields = line.split()
            if fields[1] == "rwxp" and len(fields) == 5:
                start, end = (int(bound, 16) for bound in fields[0].split("-"))
                total += end - start
        return total

    hk_out_check = f"{ipc} hyprctl -j hyprmasq hooks | jq -e '.groups[] | select(.name == \"input\") | .installed | not' > /dev/null"

    focus_workspace(107)
    hk_shell = foot("hm-hk-shell")
    wait_drawn(hk_shell, 1000)
    focus_workspace(4)
    hk_path = open_socket(hk_shell)
    # An open socket holds nothing. Leases of earlier areas, such as hyprmasq-mcp's of a minute, hold the input hooks
    # until they end.
    for entry in json.loads(hypr("-j hyprmasq eis list"))["sockets"]:
        machine.execute("su - alice -c " + shlex.quote(f"{ipc} hyprctl hyprmasq lease {entry['address']} release"))
    hk_out("input", "x11-input")
    groups = hk_groups()
    assert all(groups[name]["installed"] and not groups[name]["onDemand"] for name in ("placement", "idle", "plugin-calls")), groups
    assert hk_load(hk_taker("seat-opaque")) == "ok"
    before = snapshot()
    commits = window_state(hk_shell)["commits"]
    hk_refused(hk_path, "CSeatManager::setKeyboardFocus")
    # Nothing reached the window, and no focus moved.
    time.sleep(1)
    assert window_state(hk_shell)["commits"] == commits and snapshot() == before
    machine.succeed("test ! -e /tmp/hm-hk-refused")
    refused = hk_groups()["input"]["refused"]
    assert refused["code"] == "hook_held" and "CSeatManager::setKeyboardFocus" in refused["message"], refused
    assert "input on demand, not installed, refused: another plugin hooks CSeatManager::setKeyboardFocus" in hypr("hyprmasq hooks")
    # So is a lease, in both formats.
    reply = json.loads(hypr(f"-j hyprmasq lease {hk_shell} 5000 hm-hk"))
    assert not reply["ok"] and reply["code"] == "hook_held" and "CSeatManager::setKeyboardFocus" in reply["error"], reply
    status, text = machine.execute("su - alice -c " + shlex.quote(f"{ipc} hyprctl hyprmasq lease {hk_shell} 5000 hm-hk"))
    assert status == 7 and text.startswith("error: another plugin hooks CSeatManager::setKeyboardFocus"), (status, text)
    assert window_state(hk_shell)["lease"] is None
    # Screenshots work on.
    assert_png(screenshot(windows[1][1], "hk-green"), (0, 255, 0))
    hk_unload(hk_taker("seat-opaque"))
    send(hk_path, "type", "echo hk-back > /tmp/hm-hk-back", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-back)" = hk-back')
    assert snapshot() == before and hk_groups()["input"]["refused"] is None

with subtest("hyprmasq loads while another plugin holds an input hook that cannot be shared, and only input waits for it"):
    assert hypr(f"plugin unload {plugin}") == "ok"
    assert hk_load(hk_taker("seat-opaque")) == "ok"
    assert hypr(f"plugin load {plugin}") == "ok"
    groups = hk_groups()
    assert not groups["input"]["installed"] and all(groups[name]["installed"] for name in ("placement", "idle", "plugin-calls")), groups
    assert_png(screenshot(windows[1][1], "hk-loaded"), (0, 255, 0))
    hk_path = open_socket(hk_shell)
    hk_refused(hk_path, "CSeatManager::setKeyboardFocus")
    assert window_state(hk_shell)["socket"] == hk_path
    hk_unload(hk_taker("seat-opaque"))
    send(hk_path, "type", "echo hk-loaded > /tmp/hm-hk-loaded", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-loaded)" = hk-loaded')

with subtest("the input hooks go once the last sender and lease end, so another plugin can take them"):
    run_input_background("hm-hk-a", hk_path, "move", 20, 20, "sleep", 2000)
    wait_for("the input hooks installed", lambda: hk_installed("input"), timeout=10)
    # Hyprland refuses the other plugin's hook while hyprmasq holds the function.
    assert hk_load(hk_taker("seat")) != "ok"
    status, result = input_result("hm-hk-a")
    assert status == 0, result
    hk_out("input")
    assert hk_load(hk_taker("seat")) == "ok"
    hk_unload(hk_taker("seat"))
    # A lease holds them until it ends, without input.
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} 60000 hm-hk"))["held"]
    assert hk_installed("input") and hk_load(hk_taker("seat")) != "ok"
    time.sleep(1.5)
    assert hk_installed("input")
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} release hm-hk"))["released"]
    hk_out("input")
    assert hk_load(hk_taker("seat")) == "ok"
    hk_unload(hk_taker("seat"))

with subtest("senders to windows of two apps share the input hooks, which go only after the last"):
    focus_workspace(108)
    hk_other = foot("hm-hk-other")
    wait_drawn(hk_other, 1000)
    focus_workspace(4)
    hk_other_path = open_socket(hk_other)
    # Two foot processes, two Wayland connections.
    assert client("hm-hk-other")["pid"] != client("hm-hk-shell")["pid"]
    hk_overlap_installs = hk_groups()["input"]["installs"]
    run_input_background("hm-hk-first", hk_path, "move", 20, 20, "sleep", 2500)
    run_input_background("hm-hk-second", hk_other_path, "move", 20, 20, "sleep", 9000)
    # Installed once, for both.
    wait_for("both sockets hold the input hooks", lambda: hk_groups()["input"]["claims"] == 2, timeout=10)
    assert hk_groups()["input"]["installs"] == hk_overlap_installs + 1, hk_groups()["input"]
    status, result = input_result("hm-hk-first")
    assert status == 0, result
    wait_for("the first socket's claim gone", lambda: hk_groups()["input"]["claims"] == 1, timeout=10)
    # Neither the second sender's arrival nor the first's end installed the group again.
    assert hk_groups()["input"]["installs"] == hk_overlap_installs + 1, hk_groups()["input"]
    assert hk_installed("input") and hk_load(hk_taker("seat")) != "ok"
    assert machine.execute("test -s /tmp/hm-hk-second.status")[0] != 0, "the second sender ended too soon"
    status, result = input_result("hm-hk-second")
    assert status == 0, result
    hk_out("input")
    assert hk_load(hk_taker("seat")) == "ok"
    hk_unload(hk_taker("seat"))

with subtest("hyprmasq's hooks join a function that another plugin hooked first, and calls run through both"):
    hk_focus = "CSeatManager::setKeyboardFocus"

    def hk_function(name, function):
        """How often hyprmasq's hook of a function ran, and how many hooks the function's calls run through."""
        group = hk_groups()[name]
        index = next(i for i, signature in enumerate(group["functions"]) if signature.startswith(function + "("))
        return group["calls"][index], group["chain"][index]

    def hk_taker_report(command="hmtaker"):
        """What a hook taker tells: its hook's calls, and for one that shares hooks, whether calls reach it and how
        many hooks they run through."""
        words = hypr(command).split()
        return dict(zip(words[0::2], words[1::2]))

    def hk_refocus():
        """Moves the user's keyboard focus to another window and back, which calls CSeatManager::setKeyboardFocus."""
        focus_workspace(108)
        focus_workspace(4)

    hk_out("input")
    assert hk_function("input", hk_focus)[1] == 0
    assert hk_load(hk_taker("seat")) == "ok"
    assert hk_function("input", hk_focus)[1] == 1 and not hk_groups()["input"]["shared"]
    before = snapshot()
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} 60000 hm-hk"))["held"]
    hk_joined = hk_groups()["input"]
    assert hk_joined["installed"] and hk_joined["shared"] and hk_joined["refused"] is None, hk_joined
    assert "input on demand, installed, shared with another plugin" in hypr("hyprmasq hooks")
    hk_calls, hk_chain = hk_function("input", hk_focus)
    assert hk_chain == 2, hk_chain
    hk_taker_calls = int(hk_taker_report()["calls"])
    hk_refocus()
    assert hk_function("input", hk_focus)[0] > hk_calls and int(hk_taker_report()["calls"]) > hk_taker_calls
    # Input arrives, and the lease keeps the emulated focus through the user's focus changes.
    send(hk_path, "type", "echo hk-joined > /tmp/hm-hk-joined", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-joined)" = hk-joined')
    assert snapshot() == before
    # Hyprland refuses a second hook of a plugin that knows nothing of shared hooks, whoever hooked the function.
    assert hk_load(hk_taker("seat-opaque")) != "ok"

with subtest("the unhook of a plugin that knows nothing of shared hooks takes hyprmasq's hook out, which goes in again at the next use"):
    # The taker removes its hook with CFunctionHook::unhook(), which puts the function's first bytes back.
    hk_unload(hk_taker("seat"))
    hk_calls, hk_chain = hk_function("input", hk_focus)
    assert hk_chain == 0 and hk_groups()["input"]["installed"], (hk_chain, hk_groups()["input"])
    hk_refocus()
    assert hk_function("input", hk_focus)[0] == hk_calls
    # A sender to the leased window finds the hook gone and installs it again, alone.
    send(hk_path, "type", "echo hk-again > /tmp/hm-hk-again", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-again)" = hk-again')
    hk_calls, hk_chain = hk_function("input", hk_focus)
    assert hk_chain == 1 and not hk_groups()["input"]["shared"], (hk_chain, hk_groups()["input"])
    hk_refocus()
    assert hk_function("input", hk_focus)[0] > hk_calls
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} release hm-hk"))["released"]
    hk_out("input")
    assert hk_function("input", hk_focus)[1] == 0

with subtest("a plugin that shares hooks joins hyprmasq's and hyprmasq joins its, and each leaves without the other"):
    # hyprmasq's hook is the function's first, and the other plugin's joins it.
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} 60000 hm-hk"))["held"]
    assert hk_function("input", hk_focus)[1] == 1
    assert hk_load(hk_taker("seat-chain")) == "ok"
    hk_report = hk_taker_report("hmchain")
    assert hk_report["linked"] == "yes" and hk_report["members"] == "2", hk_report
    assert hk_groups()["input"]["shared"]
    hk_calls = hk_function("input", hk_focus)[0]
    hk_refocus()
    assert hk_function("input", hk_focus)[0] > hk_calls and int(hk_taker_report("hmchain")["calls"]) > int(hk_report["calls"])
    # The first hook leaves, and the one that joined it stays.
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} release hm-hk"))["released"]
    hk_out("input")
    hk_report = hk_taker_report("hmchain")
    assert hk_report["linked"] == "yes" and hk_report["members"] == "1", hk_report
    hk_calls = hk_function("input", hk_focus)[0]
    hk_refocus()
    assert hk_function("input", hk_focus)[0] == hk_calls and int(hk_taker_report("hmchain")["calls"]) > int(hk_report["calls"])
    # hyprmasq's hook joins the other plugin's again and again, without taking memory of Hyprland's.
    # Three hundred times: a join that took trampolines of Hyprland's, 64 bytes each time, would take 19 KiB.
    hk_before = hk_hook_pages()
    hk_installs = hk_groups()["input"]["installs"]
    alice(f"for i in $(seq 300); do {ipc} hyprctl hyprmasq lease {hk_shell} 60000 hm-hk > /dev/null && {ipc} hyprctl hyprmasq lease {hk_shell} release hm-hk > /dev/null && until {hk_out_check}; do sleep 0.01; done || exit 1; done")
    assert hk_groups()["input"]["installs"] == hk_installs + 300, hk_groups()["input"]
    assert hk_hook_pages() - hk_before < 8192, (hk_before, hk_hook_pages())
    assert hk_taker_report("hmchain")["members"] == "1"
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} 60000 hm-hk"))["held"]
    hk_report = hk_taker_report("hmchain")
    assert hk_report["linked"] == "yes" and hk_report["members"] == "2" and hk_groups()["input"]["shared"], hk_report
    send(hk_path, "type", "echo hk-chained > /tmp/hm-hk-chained", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-chained)" = hk-chained')
    # The hook that was joined leaves, and hyprmasq's stays.
    hk_unload(hk_taker("seat-chain"))
    hk_calls, hk_chain = hk_function("input", hk_focus)
    assert hk_chain == 1 and not hk_groups()["input"]["shared"], hk_chain
    hk_refocus()
    assert hk_function("input", hk_focus)[0] > hk_calls
    # The hook that joined leaves first.
    assert hk_load(hk_taker("seat-chain")) == "ok"
    assert hk_taker_report("hmchain")["members"] == "2"
    hk_unload(hk_taker("seat-chain"))
    hk_calls, hk_chain = hk_function("input", hk_focus)
    assert hk_chain == 1, hk_chain
    hk_refocus()
    assert hk_function("input", hk_focus)[0] > hk_calls
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} release hm-hk"))["released"]
    hk_out("input")
    assert hk_function("input", hk_focus)[1] == 0

with subtest("the middle one of three hooks of a function leaves, and the plugins unload in any order"):
    # A hook of a plugin that knows nothing of shared hooks, then hyprmasq's, then one of a plugin that shares hooks.
    assert hk_load(hk_taker("seat")) == "ok"
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} 60000 hm-hk"))["held"]
    assert hk_load(hk_taker("seat-chain")) == "ok"
    assert hk_function("input", hk_focus)[1] == 3 and hk_taker_report("hmchain")["members"] == "3"
    hk_calls, hk_first, hk_last = hk_function("input", hk_focus)[0], int(hk_taker_report()["calls"]), int(hk_taker_report("hmchain")["calls"])
    hk_refocus()
    assert hk_function("input", hk_focus)[0] > hk_calls and int(hk_taker_report()["calls"]) > hk_first and int(hk_taker_report("hmchain")["calls"]) > hk_last
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} release hm-hk"))["released"]
    hk_out("input")
    hk_report = hk_taker_report("hmchain")
    assert hk_report["linked"] == "yes" and hk_report["members"] == "2", hk_report
    hk_calls, hk_first, hk_last = hk_function("input", hk_focus)[0], int(hk_taker_report()["calls"]), int(hk_report["calls"])
    hk_refocus()
    assert hk_function("input", hk_focus)[0] == hk_calls and int(hk_taker_report()["calls"]) > hk_first and int(hk_taker_report("hmchain")["calls"]) > hk_last
    # hyprmasq unloads while its hook shares the function with the two others, and loads again.
    assert json.loads(hypr(f"-j hyprmasq lease {hk_shell} 60000 hm-hk"))["held"]
    assert hk_taker_report("hmchain")["members"] == "3"
    assert hypr(f"plugin unload {plugin}") == "ok"
    hk_report = hk_taker_report("hmchain")
    assert hk_report["linked"] == "yes" and hk_report["members"] == "2", hk_report
    hk_first = int(hk_taker_report()["calls"])
    focus_workspace(108)
    focus_workspace(4)
    assert int(hk_taker_report()["calls"]) > hk_first
    assert hypr(f"plugin load {plugin}") == "ok"
    hk_path = open_socket(hk_shell)
    send(hk_path, "type", "echo hk-three > /tmp/hm-hk-three", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-three)" = hk-three')
    hk_out("input")
    # The plugin that shares hooks leaves the other's in, whose unhook then leaves the function as Hyprland built it.
    hk_unload(hk_taker("seat-chain"))
    assert hk_function("input", hk_focus)[1] == 1
    hk_unload(hk_taker("seat"))
    assert hk_function("input", hk_focus)[1] == 0
    hk_refocus()
    send(hk_path, "type", "echo hk-alone > /tmp/hm-hk-alone", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-alone)" = hk-alone')
    hk_out("input")

with subtest("input goes on after another plugin that drove the same window lets go of its keyboard and pointer"):
    # Two plugins that drive one window share its app's keyboard and pointer. The one that lets go sends them a leave,
    # after which Hyprland drops keys and buttons sent to them, so the plugin enters them on the window again.
    hk_call_probe = machine.succeed("cat /etc/hyprmasq-call-probe").strip()
    assert hk_load(hk_call_probe) == "ok"
    focus_workspace(111)
    spawn("hyprmasq-focus-probe focus hm-hk-left", "hk-left")
    wait_for("the probe", lambda: client("hm-hk-left") is not None)
    focus_workspace(4)
    hk_left = client("hm-hk-left")["address"]
    hk_left_path = open_socket(hk_left)

    def hk_left_events(start=0):
        return [event for _, event in probe_events("hk-left", start)]

    def hk_in_order(events, *wanted):
        """Whether the events have the wanted ones, in that order."""
        position = 0
        for event in wanted:
            if event not in events[position:]:
                return False
            position = events.index(event, position) + 1
        return True

    assert json.loads(hypr(f"-j hyprmasq lease {hk_left} 60000 hm-hk"))["held"]
    send(hk_left_path, "move", 20, 20, "click", "key", 30)
    wait_for("the probe's button and key", lambda: hk_in_order(hk_left_events(), "button hm-hk-left 1", "key hm-hk-left 30 1"))
    hk_left_start = len(probe_events("hk-left"))
    assert hypr(f"hmprobe leave {hk_left}").startswith("left "), hypr(f"hmprobe leave {hk_left}")
    wait_for("the probe's leaves", lambda: {"keyboard leave hm-hk-left", "pointer leave hm-hk-left"} <= set(hk_left_events(hk_left_start)))
    # The pointer alone first, then the keyboard: each is entered again before what is sent to it.
    hk_left_start = len(probe_events("hk-left"))
    status, result = input_json(hk_left_path, "move", 30, 30, "click")
    assert status == 0 and result["delivery"] == "complete", (status, result)
    wait_for("the probe's button after the leave", lambda: hk_in_order(hk_left_events(hk_left_start), "pointer enter hm-hk-left", "button hm-hk-left 1"))
    assert "keyboard enter hm-hk-left" not in hk_left_events(hk_left_start), hk_left_events(hk_left_start)
    status, result = input_json(hk_left_path, "key", 30)
    assert status == 0 and result["delivery"] == "complete", (status, result)
    wait_for("the probe's key after the leave", lambda: hk_in_order(hk_left_events(hk_left_start), "keyboard enter hm-hk-left", "key hm-hk-left 30 1"))
    assert json.loads(hypr(f"-j hyprmasq lease {hk_left} release hm-hk"))["released"]
    hk_out("input")
    hk_unload(hk_call_probe)
    dispatch(f'hl.dsp.window.close({{ window = "address:{hk_left}" }})')
    wait_for("the probe closed", lambda: client("hm-hk-left") is None)

with subtest("input to X11 windows needs hooks of its own, which go after it"):
    focus_workspace(109)
    hk_x = xterm("hm-hk-x")
    focus_workspace(4)
    hk_x_path = open_socket(hk_x)
    hk_out("x11-input")
    assert hk_load(hk_taker("xwm-opaque")) == "ok"
    hk_refused(hk_x_path, "CXWM::activateSurface")
    # Input to Wayland windows does not need them.
    send(hk_path, "type", "echo hk-wayland > /tmp/hm-hk-wayland", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-wayland)" = hk-wayland')
    assert not hk_installed("x11-input")
    hk_unload(hk_taker("xwm-opaque"))
    send(hk_x_path, "type", "echo hk-x > /tmp/hm-hk-x", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-x)" = hk-x')
    # After the X focus has gone back, and the window manager's grace for it.
    hk_out("x11-input", "input")
    assert hk_load(hk_taker("xwm")) == "ok"
    hk_unload(hk_taker("xwm"))

with subtest("an app's own copy that is the agent clipboard holds the touch hook until the user's input ends it"):
    # Hyprland finds the surface of a touch only after its touch event, so the hook on CSeatManager::sendTouchDown ends
    # such a copy before the user's touch reaches the app, also once the input hooks are out.
    hk_clip, hk_clip_path = clip_probe("hm-hk-clip", 110)
    focus_workspace(4)
    hk_out("input", "clipboard")
    hk_clip_start = len(probe_events("hm-hk-clip"))
    send(hk_clip_path, "keys", "c")
    wait_clip("hm-hk-clip", hk_clip_start, "copy hm-hk-clip-copy1")
    hk_out("input")
    assert hk_installed("clipboard"), hk_groups()["clipboard"]
    # The user's focus ends the copy there, and the hook goes with it.
    hk_clip_start = len(probe_events("hm-hk-clip"))
    focus_workspace(110)
    dispatch(f'hl.dsp.focus({{ window = "address:{hk_clip}" }})')
    wait_for("the probe focused", lambda: snapshot() == (110, hk_clip))
    wait_clip("hm-hk-clip", hk_clip_start, "source cancelled hm-hk-clip-copy1")
    hk_out("clipboard")
    focus_workspace(4)
    machine.succeed("pkill -f '[h]yprmasq-clip-probe hm-hk-clip' || true")
    wait_for("the clipboard probe closed", lambda: client("hm-hk-clip") is None)

with subtest("the input hooks go in and out a hundred times without taking memory each time"):
    # Hyprland keeps the code of every hook it installs in pages that it maps for reading, writing and executing, and
    # never frees or reuses it, so the plugin installs each hook again with the code of its first install.
    hk_out("input")
    hk_installs = hk_groups()["input"]["installs"]
    hk_before = hk_hook_pages()
    alice(
        f"for i in $(seq 100); do {ipc} hyprctl hyprmasq lease {hk_shell} 60000 hm-hk > /dev/null"
        f" && {ipc} hyprctl hyprmasq lease {hk_shell} release hm-hk > /dev/null"
        f" && until {hk_out_check}; do sleep 0.01; done; done"
    )
    assert hk_groups()["input"]["installs"] == hk_installs + 100, hk_groups()["input"]
    # Each install of the group's ten hooks would take 640 bytes: 64 KiB in all.
    hk_after = hk_hook_pages()
    machine.log(f"Hyprland's hook pages: {hk_before} bytes before, {hk_after} after")
    assert hk_after - hk_before < 8192, (hk_before, hk_after)

with subtest("hooks check installs every group and removes those not in use again"):
    hk_before = {name: group["installed"] for name, group in hk_groups().items()}
    hk_check = json.loads(hypr("-j hyprmasq hooks check"))
    assert hk_check["ok"] and hk_check["all"], hk_check
    hk_states = {group["name"]: group["state"] for group in hk_check["groups"]}
    assert hk_states == {name: "installed" if installed else "installs" for name, installed in hk_before.items()}, (hk_states, hk_before)
    assert {name: group["installed"] for name, group in hk_groups().items()} == hk_before
    assert "installs" in hypr("hyprmasq hooks check")

with subtest("a hook that another plugin holds at load and that cannot be shared turns off only its own feature"):
    assert hypr(f"plugin unload {plugin}") == "ok"
    assert hk_load(hk_taker("idle-opaque")) == "ok"
    assert hypr(f"plugin load {plugin}") == "ok"
    idle_hooks = hk_groups()["idle"]
    assert not idle_hooks["installed"] and idle_hooks["refused"]["code"] == "hook_held", idle_hooks
    assert "CIdleNotifyProtocol::setInhibit" in idle_hooks["refused"]["message"], idle_hooks
    hk_check = {group["name"]: group for group in json.loads(hypr("-j hyprmasq hooks check"))["groups"]}
    assert hk_check["idle"]["state"] == "refused" and hk_check["idle"]["refused"]["code"] == "hook_held", hk_check
    hk_path = open_socket(hk_shell)
    send(hk_path, "type", "echo hk-idle > /tmp/hm-hk-idle", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hm-hk-idle)" = hk-idle')
    assert_png(screenshot(windows[1][1], "hk-idle"), (0, 255, 0))
    assert hypr(f"plugin unload {plugin}") == "ok"
    hk_unload(hk_taker("idle-opaque"))
    assert hypr(f"plugin load {plugin}") == "ok"
    assert hk_installed("idle") and hk_groups()["idle"]["refused"] is None
    for title in ("hm-hk-shell", "hm-hk-other", "hm-hk-x"):
        dispatch(f'hl.dsp.window.close({{ window = "address:{client(title)["address"]}" }})')
        wait_for(f"{title} closed", lambda: client(title) is None)
