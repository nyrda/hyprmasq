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
