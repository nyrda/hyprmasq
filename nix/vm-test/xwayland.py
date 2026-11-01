with subtest("XWayland screenshots on a hidden workspace"):
    focus_workspace(11)
    command = "sh -c " + shlex.quote("printf '\\033[?25lTOP OF WINDOW\\n'; sleep 900")
    x_red = xterm("hm-x-red", command, "-bg '#ff0000' -fg '#ffffff'")
    assert client("hm-x-red")["xwayland"], client("hm-x-red")
    wait_drawn(x_red, 500)
    focus_workspace(4)
    assert_png(screenshot(x_red, "x-red"), (255, 0, 0))
    assert snapshot() == (4, visible)
    # A tiling resize while it is hidden: the wait gives XWayland frame callbacks, so the window catches up with
    # the resize, and is no longer resizing once the wait has ended.
    open_socket(x_red)
    focus_workspace(11)
    x_neighbour = xterm("hm-x-neighbour", "sleep 900")
    wait_for("the X11 window halved", lambda: client("hm-x-red")["size"] == [320, 480])
    focus_workspace(4)
    dispatch(f'hl.dsp.window.close({{ window = "address:{x_neighbour}" }})')
    wait_for("the X11 neighbour closed", lambda: client("hm-x-neighbour") is None)
    reply = json.loads(hypr(f"-j hyprmasq wait {x_red} since {window_state(x_red)['commits']} quiet 200 deadline 4000"))
    assert reply["ended"] == "quiet" and not reply["resizing"], reply
    state = window_state(x_red)
    assert state["size"] == {"width": 640, "height": 480} and not state["resizing"], state
    assert_png(screenshot(x_red, "x-red-resized"), (255, 0, 0))
    assert hypr(f"hyprmasq eis close {x_red}") == "ok"

with subtest("XWayland hidden shell input while a Wayland window is active"):
    focus_workspace(12)
    x_shell = xterm("hm-x-shell")
    wait_drawn(x_shell, 1000)
    focus_workspace(4)
    before_focus = snapshot()
    before = screenshot(x_shell, "x-shell-before")
    x_shell_path = open_socket(x_shell)
    send(x_shell_path, "type", "echo x11 > /tmp/x11-marker", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/x11-marker)" = x11', timeout=30)
    assert snapshot() == before_focus
    time.sleep(1)
    after = screenshot(x_shell, "x-shell-after")
    machine.fail(f"cmp -s {before} {after}")
    assert snapshot() == before_focus

with subtest("XWayland pointer events reach a hidden window under a visible one"):
    focus_workspace(13)
    hidden_xev = xev("hm-x-xev", "x-xev")
    # A visible X11 window beside the active foot, stacked above the hidden one where they overlap.
    focus_workspace(4)
    xev("hm-x-cover", "x-cover")
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("foot focus", lambda: snapshot() == (4, visible))
    cover = client("hm-x-cover")
    before_focus = snapshot()
    hidden_offset, cover_offset = log_size("x-xev"), log_size("x-cover")
    x, y = cover["at"][0] + 20, cover["at"][1] + 40
    path = open_socket(hidden_xev)
    send(path, "move", x, y, "click", "scroll", 0, 30)
    time.sleep(0.5)
    events = log_since("x-xev", hidden_offset)
    for event in ["MotionNotify", f"({x},{y})", "ButtonPress", "ButtonRelease", "button 5"]:
        assert event in events, events
    covered = log_since("x-cover", cover_offset)
    assert "ButtonPress" not in covered, covered + "\n----\n" + events
    assert snapshot() == before_focus
    assert hypr(f"hyprmasq eis close {hidden_xev}") == "ok"

with subtest("XWayland input is refused while another X11 window is active"):
    focus_workspace(14)
    x_active = xterm(
        "hm-x-active",
        "sh -c " + shlex.quote("stty -echo; cat > /tmp/hyprmasq-x-active-input"),
    )
    wait_for("xterm focus", lambda: snapshot() == (14, x_active))
    machine.wait_until_succeeds("test -f /tmp/hyprmasq-x-active-input", timeout=30)
    before_focus = snapshot()
    send_refused(x_shell_path, "type", "echo refused > /tmp/x11-refused", "key", 28)
    time.sleep(1)
    machine.succeed("test ! -e /tmp/x11-refused && test ! -s /tmp/hyprmasq-x-active-input")
    assert snapshot() == before_focus

    # The active window itself may always be driven.
    path = open_socket(x_active)
    send(path, "type", "same", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/hyprmasq-x-active-input)" = same', timeout=30)
    assert snapshot() == before_focus

    # force drives the hidden window anyway, and the active one still gets nothing.
    assert hypr(f"hyprmasq eis open {x_shell} force") == x_shell_path
    assert eis_entry(x_shell)["path"] == x_shell_path and eis_entry(x_shell)["force"]
    send(x_shell_path, "type", "echo forced > /tmp/x11-forced", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/x11-forced)" = forced', timeout=30)
    time.sleep(1)
    assert machine.succeed("cat /tmp/hyprmasq-x-active-input") == "same\n"
    assert snapshot() == before_focus
    assert hypr(f"hyprmasq eis open {x_shell}") == x_shell_path
    assert hypr(f"hyprmasq eis close {x_active}") == "ok"

with subtest("a driven X11 app cannot activate itself"):
    focus_workspace(4)
    before_focus = snapshot()
    xid = alice(f"env {x_env} xdotool search --name '^hm-x-shell$'").split()[0]
    alice(f"env {x_env} xdotool windowactivate {xid} || true")
    time.sleep(1)
    assert snapshot() == before_focus
    # The same request works once nothing drives the app.
    assert hypr(f"hyprmasq eis close {x_shell}") == "ok"
    alice(f"env {x_env} xdotool windowactivate {xid} || true")
    wait_for("activation", lambda: snapshot()[1] == x_shell)

with subtest("X11 apps learn they are not active after input"):
    # Wine keeps its window in the foreground until _NET_ACTIVE_WINDOW changes,
    # so the plugin writes it again when it gives X focus back and when it
    # refuses an activation request.
    focus_workspace(4)
    before_focus = snapshot()
    spawn(
        f"env {x_env} stdbuf -oL xprop -spy -root _NET_ACTIVE_WINDOW",
        "x-active-spy",
    )
    machine.wait_until_succeeds(
        "grep -q _NET_ACTIVE_WINDOW /tmp/hyprmasq-x-active-spy.log"
    )

    def announcements():
        return int(machine.succeed("wc -l < /tmp/hyprmasq-x-active-spy.log"))

    count = announcements()
    path = open_socket(x_shell)
    send(path, "type", "true", "key", 28)
    wait_for(
        "announcement after input", lambda: announcements() > count, timeout=10
    )
    assert snapshot() == before_focus

    # A click takes X focus as typing does, and gives it back the same way.
    count = announcements()
    send(path, "move", 20, 20, "click")
    wait_for(
        "announcement after a click", lambda: announcements() > count, timeout=10
    )
    assert snapshot() == before_focus

    count = announcements()
    xid = alice(f"env {x_env} xdotool search --name '^hm-x-shell$'").split()[0]
    alice(f"env {x_env} xdotool windowactivate {xid} || true")
    wait_for(
        "announcement after activation", lambda: announcements() > count, timeout=10
    )
    assert snapshot() == before_focus
    assert hypr(f"hyprmasq eis close {x_shell}") == "ok"
    machine.execute("pkill -f 'xprop -spy'")

with subtest("an X server that does not answer holds Hyprland up for 25 ms per input frame at most"):
    focus_workspace(15)
    stuck_windows = [xterm(f"hm-x-stuck-{n}", "sh -c " + shlex.quote(f"stty -echo; cat > /tmp/hm-x-stuck-{n}")) for n in (1, 2)]
    focus_workspace(4)
    before_focus = snapshot()
    first, second = [open_socket(w) for w in stuck_windows]
    send(first, "type", "a", "key", 28)
    machine.wait_until_succeeds("test \"$(cat /tmp/hm-x-stuck-1)\" = a", timeout=30)
    xwayland = machine.succeed("pgrep -x Xwayland").strip()

    def stuck_input(text):
        # The frame of the second window first gives back the X focus that the first holds, which takes several
        # round trips, and pauses once one runs out of time.
        script = (
            f"s=$(date +%s%N); hyprmasq-input --socket {shlex.quote(second)} type {text} > /tmp/hm-x-stuck-out 2>&1; "
            "echo $? $((($(date +%s%N) - s) / 1000000))"
        )
        status, ms = alice(script).split()
        output = machine.succeed("cat /tmp/hm-x-stuck-out")
        assert status == "1" and "paused input" in output, (status, output)
        return int(ms)

    # X focus stays with the first window for a second after its input, the second's input comes before that.
    send(first, "type", "b")
    machine.succeed(f"kill -STOP {xwayland}")
    try:
        held_ms = stuck_input("c")
        again_ms = stuck_input("d")
        latency = int(alice(f"s=$(date +%s%N); {ipc} hyprctl version > /dev/null; echo $((($(date +%s%N) - s) / 1000000))"))
    finally:
        machine.succeed(f"kill -CONT {xwayland}")
    machine.log(f"input with a stuck X server took {held_ms} and {again_ms} ms, hyprctl {latency} ms")
    # Starting hyprmasq-input and its pause take about 15 ms more, and a budget of 100 ms took 113 to 118 ms here.
    assert held_ms < 100 and again_ms < 100 and latency < 300, (held_ms, again_ms, latency)
    assert snapshot() == before_focus

    # Once the X server answers again, so does input.
    def delivered():
        status, _ = machine.execute(f"su - alice -c {shlex.quote(f'hyprmasq-input --socket {shlex.quote(second)} type e key 28')}")
        return status == 0 and "e" in machine.succeed("cat /tmp/hm-x-stuck-2")

    wait_for("X11 input after the X server answers again", delivered, timeout=30)
    assert snapshot() == before_focus
    for w in stuck_windows:
        assert hypr(f"hyprmasq eis close {w}") == "ok"
        dispatch(f'hl.dsp.window.close({{ window = "address:{w}" }})')
    wait_for("the stuck xterms closed", lambda: client("hm-x-stuck-1") is None and client("hm-x-stuck-2") is None)

with subtest("a stopped XWayland never holds Hyprland up, however many requests it leaves unread"):
    # Each pointer frame to an X11 window asks the X server for its stacking order. A stopped XWayland reads none of
    # these requests, and once they fill a quarter of the socket's buffer, about 70 of them, a flush would wait until it
    # reads again. Four windows, each paused for half a second after its frame, take 96 frames in turn.
    focus_workspace(15)
    unread_windows = [xterm(f"hm-x-unread-{n}", "sh -c " + shlex.quote(f"stty -echo; cat > /tmp/hm-x-unread-{n}")) for n in range(1, 5)]
    focus_workspace(4)
    before_focus = snapshot()
    unread_paths = [open_socket(w) for w in unread_windows]
    # Hyprland's own XWayland, the only one so far.
    xwayland = machine.succeed("pgrep -o -x Xwayland").strip()
    frames = (
        f"set -- {' '.join(shlex.quote(p) for p in unread_paths)}; worst=0; "
        "for i in $(seq 0 95); do "
        'eval "socket=\\${$((i % 4 + 1))}"; '
        'timeout 5 hyprmasq-input --socket "$socket" move $((20 + i % 40)) 30 > /dev/null 2>&1; '
        f"s=$(date +%s%N); timeout 5 env {ipc} hyprctl version > /dev/null || {{ echo blocked $i; exit 0; }}; "
        "ms=$((($(date +%s%N) - s) / 1000000)); [ $ms -gt $worst ] && worst=$ms; "
        "sleep 0.15; done; echo answered $worst"
    )
    machine.succeed(f"kill -STOP {xwayland}")
    try:
        outcome = alice(frames).split()
    finally:
        machine.succeed(f"kill -CONT {xwayland}")
    machine.log(f"96 pointer frames with XWayland stopped: {' '.join(outcome)} ms")
    assert outcome[0] == "answered" and int(outcome[1]) < 500, outcome
    assert snapshot() == before_focus

    def unread_input_arrives():
        status, _ = machine.execute(f"su - alice -c {shlex.quote(f'hyprmasq-input --socket {shlex.quote(unread_paths[0])} type e key 28')}")
        return status == 0 and "e" in machine.succeed("cat /tmp/hm-x-unread-1")

    wait_for("X11 input after XWayland reads again", unread_input_arrives, timeout=30)
    assert snapshot() == before_focus
    for w in unread_windows:
        assert hypr(f"hyprmasq eis close {w}") == "ok"
        dispatch(f'hl.dsp.window.close({{ window = "address:{w}" }})')
    wait_for("the unread xterms closed", lambda: all(client(f"hm-x-unread-{n}") is None for n in range(1, 5)))

with subtest("xwayland-satellite windows"):
    spawn("xwayland-satellite :5", "satellite")
    machine.wait_until_succeeds("test -S /tmp/.X11-unix/X5", timeout=60)
    focus_workspace(15)
    sat_shell = xterm("hm-sat-shell", env=satellite_env)
    assert not client("hm-sat-shell")["xwayland"], client("hm-sat-shell")
    wait_drawn(sat_shell, 1000)
    focus_workspace(4)
    before_focus = snapshot()
    before = screenshot(sat_shell, "sat-before")
    sat_path = open_socket(sat_shell)
    send(sat_path, "type", "echo satellite > /tmp/sat-marker", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/sat-marker)" = satellite', timeout=30)
    time.sleep(1)
    assert snapshot() == before_focus
    after = screenshot(sat_shell, "sat-after")
    machine.fail(f"cmp -s {before} {after}")

    focus_workspace(16)
    sat_active = xterm(
        "hm-sat-active",
        "sh -c " + shlex.quote("stty -echo; cat > /tmp/hyprmasq-sat-active-input"),
        env=satellite_env,
    )
    wait_for("satellite focus", lambda: snapshot() == (16, sat_active))
    machine.wait_until_succeeds("test -f /tmp/hyprmasq-sat-active-input", timeout=30)
    before_focus = snapshot()
    send_refused(sat_path, "type", "echo refused > /tmp/sat-refused", "key", 28)
    time.sleep(1)
    machine.succeed("test ! -e /tmp/sat-refused && test ! -s /tmp/hyprmasq-sat-active-input")
    # A window of Hyprland's own XWayland is on another X server, so it is not blocked.
    send(open_socket(x_active), "type", "other", "key", 28)
    machine.wait_until_succeeds("grep -qx other /tmp/hyprmasq-x-active-input", timeout=30)

    hypr(f"hyprmasq eis open {sat_shell} force")
    send(sat_path, "type", "echo forced > /tmp/sat-forced", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/sat-forced)" = forced', timeout=30)
    time.sleep(1)
    machine.succeed("test ! -s /tmp/hyprmasq-sat-active-input")
    assert snapshot() == before_focus

    # xwayland-satellite forwards activation as xdg-activation. Every satellite app counts as the driven one.
    focus_workspace(4)
    before_focus = snapshot()
    xid = alice(f"env {satellite_env} xdotool search --name '^hm-sat-active$'").split()[0]
    alice(f"env {satellite_env} xdotool windowactivate {xid} || true")
    time.sleep(1)
    assert snapshot() == before_focus
    assert hypr(f"hyprmasq eis close {sat_shell}") == "ok"
    alice(f"env {satellite_env} xdotool windowactivate {xid} || true")
    wait_for("satellite activation", lambda: snapshot()[1] == sat_active)
