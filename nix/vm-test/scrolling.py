def scr_covers(shot, x, y):
    """Whether a zoom of a changes shot shows the point x,y of the shot that the changes are measured from."""
    return any(r["x"] <= x < r["x"] + r["width"] and r["y"] <= y < r["y"] + r["height"] for r in shot["changes"]["regions"])


def scr_overlaps(shot, box):
    """Whether a zoom of a changes shot shows part of the box x0, y0, x1, y1 of the shot it is measured from."""
    x0, y0, x1, y1 = box
    return any(r["x"] < x1 and x0 < r["x"] + r["width"] and r["y"] < y1 and y0 < r["y"] + r["height"] for r in shot["changes"]["regions"])


def scr_answers():
    """The answers that the questionnaire logged, as (number, value) pairs."""
    lines = machine.succeed("cat /tmp/hm-sc-form/events.jsonl 2>/dev/null || true").splitlines()
    return [(event["number"], event["value"]) for event in map(json.loads, lines)]


def scr_shot_of(address):
    return scr_agent.call("screenshot", {"window": address}).data["shot"]["id"]


def scr_hidden(command, log, title, workspace):
    """Starts a window on workspace, waits until it has drawn, and gives the user's foot the focus again."""
    focus_workspace(workspace)
    spawn(command, log)
    wait_for(title, lambda: client(title) is not None, timeout=120)
    address = client(title)["address"]
    wait_drawn(address, 500)
    focus_workspace(99)
    dispatch(f'hl.dsp.focus({{ window = "address:{scr_user}" }})')
    wait_for("the user's focus", lambda: snapshot() == (99, scr_user))
    return address


# The output is as large as a laptop's, as the form task's questionnaire needs, and the user works in a foot on the
# visible workspace.
output_mode(1280, 800)
focus_workspace(99)
scr_user = foot("hm-sc-user")
wait_drawn(scr_user, 500)
scr_agent = Mcp("scrolling")
scr_agent.initialize()

with subtest("a scroll that ends a batch comes after the changes of the input before it, in a reply of two parts"):
    machine.succeed("rm -rf /tmp/hm-sc-form")
    scr_launched = scr_agent.call("launch", {"command": ["hyprmasq-bench-form", "/tmp/hm-sc-form", "25"], "workspace": "hm-sc-form"})
    assert not scr_launched.is_error, scr_launched
    scr_form, scr_shot = scr_launched.data["window"]["window"], scr_launched.data["shot"]["id"]
    # A click on the margin beside the questions gives the window the emulated focus, as in the settle area.
    scr_focused = scr_agent.call("act", {"window": scr_form, "shot": scr_shot, "actions": [{"action": "click", "x": 100, "y": 400}]})
    assert scr_focused.data["actions"]["delivery"] == "complete", scr_focused
    scr_shot = scr_focused.data["shot"]["id"]
    time.sleep(1)
    # Amber ticked, and three notches over the margin, which scroll the page.
    scr_split = scr_agent.call(
        "act",
        {
            "window": scr_form,
            "shot": scr_shot,
            "actions": [{"action": "click", "x": 288, "y": 180}, {"action": "scroll", "x": 100, "y": 400, "dy": 3, "unit": "notch"}],
        },
    )
    machine.log(f"the questionnaire after a click and a scroll: {json.dumps(scr_split.data)}, answers {scr_answers()}")
    keep(scr_split, "sc-split")
    assert scr_split.data["actions"] == {"done": 2, "total": 2, "delivery": "complete"}, scr_split
    scr_first = scr_split.data["before_scroll"]
    assert scr_first["wait"]["ended"] == "quiet" and scr_first["wait"]["redraw_observed"], scr_split
    assert scr_first["shot"]["changes"]["base"] == scr_shot and scr_covers(scr_first["shot"], 288, 180), scr_split
    assert scr_split.data["wait"]["ended"] == "quiet" and scr_split.data["wait"]["redraw_observed"], scr_split
    scr_after = scr_split.data["shot"]
    assert scr_after["full_shot"] and (scr_after["width"], scr_after["height"]) == (1280, 800), scr_split
    assert re.fullmatch(
        rf"2/2 delivered · lease \d+ s\nbefore the scroll: redraw observed · .* · shot {scr_first['shot']['id']} = {scr_shot} \+ \d+ changes?\n((zoom .*|\d+ tiny changes? left out)\n)+"
        rf"after the scroll: redraw observed · .* · shot {scr_after['id']} 1280×800 \(full shot: \d+% changed since {scr_first['shot']['id']}\)",
        scr_split.data["summary"],
    ), scr_split
    assert len(scr_split.images) == len(scr_first["shot"]["changes"]["regions"]) + 1, scr_split
    assert (1, ["Amber"]) in scr_answers(), scr_split
    assert snapshot() == (99, scr_user), snapshot()
    assert scr_agent.call("close", {"window": scr_form}).data["closed"]

with subtest("a scroll after input that drew nothing or opened a window is held back"):
    # focus-probe draws nothing in answer to a click, and logs the scrolls it gets.
    scr_probe = scr_hidden("hyprmasq-focus-probe focus hm-sc-probe", "sc-probe", "hm-sc-probe", 100)
    scr_start = len(probe_events("sc-probe"))
    scr_held = scr_agent.call("act", {"window": scr_probe, "shot": scr_shot_of(scr_probe), "actions": [{"action": "click", "x": 50, "y": 50}, {"action": "scroll", "x": 50, "y": 50, "dy": 2}]})
    scr_reason = "the actions before it drew nothing, so they may have missed"
    assert scr_held.data["actions"] == {"done": 1, "total": 2, "delivery": "partial", "failed_index": 1, "held_back": scr_reason}, scr_held
    assert scr_held.data["summary"].startswith("1/2 delivered, scroll held back · no redraw"), scr_held
    assert scr_held.data["hint"].startswith(f"The scroll was not sent: {scr_reason}. The page has not moved"), scr_held
    assert "before_scroll" not in scr_held.data, scr_held
    time.sleep(0.5)
    scr_got = [e.split()[0] for _, e in probe_events("sc-probe", scr_start)]
    assert "button" in scr_got and "axis" not in scr_got, probe_events("sc-probe", scr_start)
    # The opener mode answers a right click with a dialog, and redraws in another color.
    scr_opener = scr_hidden("hyprmasq-focus-probe opener hm-sc-opener", "sc-opener", "hm-sc-opener", 101)
    scr_start = len(probe_events("sc-opener"))
    scr_opened = scr_agent.call(
        "act", {"window": scr_opener, "shot": scr_shot_of(scr_opener), "actions": [{"action": "click", "x": 50, "y": 50, "button": "right"}, {"action": "scroll", "x": 50, "y": 50, "dy": 2}]}
    )
    scr_dialog = scr_opened.data["new_windows"][0]["window"]
    assert scr_opened.data["actions"]["held_back"] == f"the actions before it opened window {scr_dialog}", scr_opened
    assert scr_opened.data["actions"]["done"] == 1 and scr_opened.data["actions"]["delivery"] == "partial", scr_opened
    time.sleep(0.5)
    assert "axis" not in [e.split()[0] for _, e in probe_events("sc-opener", scr_start)], probe_events("sc-opener", scr_start)
    assert snapshot() == (99, scr_user), snapshot()
    machine.succeed("pkill -f '^hyprmasq-focus-probe (focus|opener) hm-sc-'")
    wait_for("the probes closed", lambda: not any(c["title"].startswith(("hm-sc-probe", "hm-sc-opener")) for c in clients()))

with subtest("a dropdown under the pointer that takes the wheel shows as a change after the scroll"):
    # The Qt probe's dropdown, which a notch moves from Mercury to Venus.
    scr_qt = scr_hidden("hyprmasq-qt-probe hm-sc-qt", "sc-qt", "hm-sc-qt", 102)
    scr_shot = scr_shot_of(scr_qt)
    scr_start = len(probe_events("sc-qt"))
    scr_wheel = scr_agent.call(
        "act",
        {
            "window": scr_qt,
            "shot": scr_shot,
            "actions": [{"action": "click", "x": 40, "y": 35}, {"action": "scroll", "x": 120, "y": 185, "dy": 1, "unit": "notch"}],
        },
    )
    machine.log(f"the Qt probe after a click and a scroll over its dropdown: {scr_wheel.text}")
    assert scr_wheel.data["actions"]["delivery"] == "complete", scr_wheel
    wait_for("the box ticked and Venus chosen", lambda: {"checked 1", "choice Venus"} <= {e for _, e in probe_events("sc-qt", scr_start)}, timeout=5)
    scr_first = scr_wheel.data["before_scroll"]["shot"]
    # The boxes of the check box and the dropdown, in the probe's logical pixels.
    assert scr_overlaps(scr_first, (20, 20, 180, 50)) and not scr_overlaps(scr_first, (20, 170, 220, 200)), scr_wheel
    scr_after = scr_wheel.data["shot"]
    assert "full_shot" not in scr_after and scr_after["changes"]["base"] == scr_first["id"] and scr_overlaps(scr_after, (20, 170, 220, 200)), scr_wheel
    assert "a control under the pointer, such as a dropdown, took the wheel" in scr_wheel.data["hint"], scr_wheel
    machine.succeed(f"kill {client('hm-sc-qt')['pid']}")
    wait_for("the Qt probe closed", lambda: client("hm-sc-qt") is None)

with subtest("scrolls on X11 move GTK 3 and Chromium by every notch, and px scrolls move them"):

    def scr_gtk_positions():
        return [int(e.split()[1]) for _, e in probe_events("sc-gtk") if e.startswith("scroll ")]

    def scr_chromium_positions():
        lines = machine.succeed("cat /tmp/hyprmasq-sc-chromium.log").splitlines()
        return [int(line.split('"probe scroll ', 1)[1].split('"', 1)[0]) for line in lines if '"probe scroll ' in line]

    def scr_settled(positions):
        """Where the page rests once it has not moved for a second, or 0 if it never moved."""
        logged, since = len(positions()), time.monotonic()
        while time.monotonic() - since < 1:
            time.sleep(0.2)
            if len(positions()) != logged:
                logged, since = len(positions()), time.monotonic()
        return (positions() or [0])[-1]

    # One at a time: XWayland stacks the windows of all workspaces in one tree, and an X11 window over the point of the
    # scroll, which the restacking for each frame moves, starts every scroll anew.
    scr_probes = [
        (f"env {x_env} GDK_BACKEND=x11 hyprmasq-combo-probe --scroll hm-sc-gtk", "sc-gtk", "hm-sc-gtk", scr_gtk_positions,
         "pkill -f '[h]yprmasq-combo-probe.py --scroll hm-sc-gtk'"),
        (f"env {x_env} chromium --ozone-platform=x11 --user-data-dir=/tmp/hm-sc-chromium --no-first-run --no-default-browser-check "
         "--disable-gpu --enable-logging=stderr --app=file:///etc/hyprmasq-test/scroll.html", "sc-chromium", "hm-x-scroll", scr_chromium_positions,
         "pkill -f '[u]ser-data-dir=/tmp/hm-sc-chromium'"),
    ]
    # A notch moves GTK 3 by a step that its page's height gives, and Chromium by 120 pixels: the first scroll measures
    # it. Each scroll comes after motion, and one after a click, after which XWayland sends the scroll from another
    # device than the motion: GTK 3 and Chromium took the first notch of each such scroll for where to count from, and
    # a px scroll, a single event, moved nothing.
    for scr_command, scr_log, scr_title, scr_positions, scr_kill in scr_probes:
        scr_window = scr_hidden(scr_command, scr_log, scr_title, 103)
        if scr_log == "sc-chromium":
            machine.wait_until_succeeds("grep -q '\"probe ready\"' /tmp/hyprmasq-sc-chromium.log", timeout=60)
        scr_path = open_socket(scr_window)
        scr_total, scr_notch = 0, None
        for scr_actions, scr_notches in [
            (["move", 300, 400, "wheel", 0, 1], 1),
            (["move", 300, 400, "wheel", 0, 3], 3),
            (["move", 300, 400, "click", "wheel", 0, 2], 2),
        ]:
            send(scr_path, *scr_actions)
            scr_total += scr_notches
            scr_at = scr_settled(scr_positions)
            scr_notch = scr_notch or scr_at
            assert scr_notch > 0 and abs(scr_at - round(scr_total * scr_notch)) <= 3, (scr_title, scr_actions, scr_total, scr_notch, scr_positions())
        send(scr_path, "move", 300, 400, "scroll", 0, 50)
        assert scr_settled(scr_positions) > scr_at, (scr_title, scr_positions())
        assert snapshot() == (99, scr_user), snapshot()
        machine.succeed(scr_kill)
        wait_for(f"{scr_title} closed", lambda: client(scr_title) is None)

assert scr_agent.stop() == 0
machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == scr_user)}")
wait_for("hm-sc-user closed", lambda: client("hm-sc-user") is None)
output_mode(640, 480)
focus_workspace(4)
