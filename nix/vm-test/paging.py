# Page movement is measured against ordinary toolkit lists and a browser page.
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


def scr_settled(positions):
    """Where the page rests once it has not moved for a second, or 0 if it never moved."""
    logged, since = len(positions()), time.monotonic()
    while time.monotonic() - since < 1:
        time.sleep(0.2)
        if len(positions()) != logged:
            logged, since = len(positions()), time.monotonic()
    return (positions() or [0])[-1]


output_mode(1280, 800)
focus_workspace(99)
scr_user = foot("hm-pg-user")
wait_drawn(scr_user, 500)
scr_agent = Mcp("paging")
scr_agent.initialize()

with subtest("page scrolling keeps overlap on Qt, GTK and browser pages at different window heights"):
    for scr_width, scr_height in [(1280, 800), (800, 600)]:
        output_mode(scr_width, scr_height)
        for scr_toolkit in ["qt", "gtk", "gtk4", "browser"]:
            scr_log = f"page-{scr_toolkit}-{scr_height}"
            scr_title = "hm-x-scroll" if scr_toolkit == "browser" else f"hm-{scr_log}"
            if scr_toolkit == "qt":
                scr_command = f"hyprmasq-qt-probe --scroll {scr_title}"
            elif scr_toolkit == "gtk":
                scr_command = f"env GDK_BACKEND=wayland hyprmasq-combo-probe --scroll {scr_title}"
            elif scr_toolkit == "gtk4":
                scr_command = f"env GDK_BACKEND=wayland hyprmasq-scroll-probe {scr_title}"
            else:
                scr_command = (f"env {x_env} chromium --ozone-platform=x11 --user-data-dir=/tmp/hm-{scr_log} "
                               "--no-first-run --no-default-browser-check --disable-gpu --enable-logging=stderr "
                               "--app=file:///etc/hyprmasq-test/scroll.html")
            scr_address = scr_hidden(scr_command, scr_log, scr_title, 104)

            def scr_page_positions():
                if scr_toolkit == "browser":
                    lines = machine.succeed(f"cat /tmp/hyprmasq-{scr_log}.log").splitlines()
                    return [int(line.split('"probe scroll ', 1)[1].split('"', 1)[0])
                            for line in lines if '"probe scroll ' in line]
                return [int(e.split()[1]) for _, e in probe_events(scr_log) if e.startswith("scroll ")]

            scr_initial = scr_agent.call("screenshot", {"window": scr_address})
            keep(scr_initial, f"sc-{scr_log}-initial")
            scr_request = {"window": scr_address, "shot": scr_initial.data["shot"]["id"],
                           "next_page": {"x": 100, "y": scr_height // 2}}
            if scr_toolkit == "qt":
                # This check box stays above the scrolling viewport. Its new state
                # must appear before the page moves, in the same call.
                scr_request["actions"] = [{"action": "click", "x": 20, "y": 20}]
            elif scr_toolkit == "gtk4":
                scr_request["actions"] = [[20, 20], ["type", "verified edit"], ["key", "Tab"],
                                          ["move", 100, scr_height // 2]]
                del scr_request["window"]
            scr_page = scr_agent.call("act", scr_request)
            scr_edit, scr_origin = scr_page, 0
            keep(scr_page, f"sc-{scr_log}")
            if scr_toolkit == "gtk4" and scr_page.data.get("scroll", {}).get("ended") == "reference_changed":
                # Unknown redraws must stop the first request. The returned shot
                # can then carry the observed scrollbar-only animation into the
                # next request, without ignoring animation in the content.
                assert scr_page.data["scroll"]["notches"] == 1, scr_page
                assert "moved_px" not in scr_page.data["scroll"], scr_page
                scr_animation = scr_page.data["wait"].get("animated", [])
                assert scr_animation and all(box["x"] >= scr_width - 16 for box in scr_animation), scr_page
                scr_origin = scr_settled(scr_page_positions)
                scr_page = scr_agent.call("act", {"shot": scr_page.data["shot"]["id"],
                                                  "next_page": {"x": 100, "y": scr_height // 2}})
                keep(scr_page, f"sc-{scr_log}-known-animation")
            scr_true = scr_settled(scr_page_positions) - scr_origin
            machine.log(f"{scr_log}: true {scr_true}, result {json.dumps(scr_page.data)}")
            assert not scr_page.is_error and scr_page.data["scroll"]["ended"] == "page", scr_page
            scr_window_height = next(c["size"][1] for c in clients() if c["address"] == scr_address)
            assert scr_window_height * 0.5 < scr_true < scr_window_height, (scr_log, scr_true)
            assert scr_true < scr_page.data["scroll"]["viewport_px"] <= scr_window_height, scr_page
            assert abs(scr_true - scr_page.data["scroll"]["moved_px"]) <= 3, (scr_true, scr_page)
            assert "changes" not in scr_page.data["shot"], scr_page
            assert scr_page.data["wait"]["ended"] == "quiet", scr_page
            if scr_toolkit == "qt":
                scr_viewport = [int(e.split()[2]) for _, e in probe_events(scr_log) if e.startswith("scroll ")][-1]
                assert abs(scr_viewport - scr_page.data["scroll"]["viewport_px"]) <= 3, scr_page
                assert "checked 1" in [event for _, event in probe_events(scr_log)], scr_page
                assert scr_page.data["before_scroll"]["wait"]["ended"] == "quiet", scr_page
                assert len(scr_page.images) >= 2, scr_page
            elif scr_toolkit == "gtk4":
                assert "entry verified edit" in [event for _, event in probe_events(scr_log)], scr_edit
                assert scr_edit.data["before_scroll"]["wait"]["ended"] == "quiet", scr_edit
                assert len(scr_edit.images) >= 2, scr_edit
            scr_back = scr_agent.call("act", {"window": scr_address, "shot": scr_page.data["shot"]["id"],
                                               "next_page": {"x": 100, "y": scr_height // 2, "direction": "up"}})
            assert not scr_back.is_error and scr_settled(scr_page_positions) == scr_origin, scr_back
            assert abs(scr_back.data["scroll"]["moved_px"] + scr_true) <= 3, scr_back
            assert snapshot() == (99, scr_user), snapshot()
            machine.succeed(f"kill {client(scr_title)['pid']}")
            wait_for("the page probe closed", lambda: client(scr_title) is None)

with subtest("a page scroll stops when a control takes the wheel without claiming the page ended"):
    output_mode(1280, 800)
    scr_qt = scr_hidden("hyprmasq-qt-probe hm-page-control", "page-control", "hm-page-control", 104)
    scr_page = scr_agent.call("act", {"window": scr_qt, "shot": scr_shot_of(scr_qt),
                                       "next_page": {"x": 120, "y": 185}})
    assert not scr_page.is_error, scr_page
    assert scr_page.data["scroll"]["notches"] == 1 and scr_page.data["scroll"]["ended"] != "page", scr_page
    time.sleep(0.5)
    assert [event for _, event in probe_events("page-control") if event.startswith("choice ")] == ["choice Venus"], scr_page
    assert "at its end" not in scr_page.text, scr_page
    assert snapshot() == (99, scr_user), snapshot()
    machine.succeed(f"kill {client('hm-page-control')['pid']}")
    wait_for("the page control closed", lambda: client("hm-page-control") is None)

assert scr_agent.stop() == 0
machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == scr_user)}")
wait_for("hm-pg-user closed", lambda: client("hm-pg-user") is None)
output_mode(640, 480)
focus_workspace(4)
