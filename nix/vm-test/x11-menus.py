with subtest("a GTK 3 menu on X11 on a hidden workspace opens and picks by pointer"):
    # The combo probe on a hidden workspace, and a visible X11 window over it on the user's workspace, which input to
    # the probe puts below the probe and back.
    focus_workspace(94)
    spawn(f"env {x_env} GDK_BACKEND=x11 hyprmasq-combo-probe hm-x-combo", "x-combo")
    wait_for("combo probe", lambda: client("hm-x-combo") is not None)
    combo = client("hm-x-combo")["address"]
    wait_drawn(combo, 500)
    focus_workspace(4)
    menu_cover = xev("hm-x-menu-cover", "x-menu-cover")
    float_window(menu_cover, 0, 0, 640, 480)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("foot focus", lambda: snapshot() == (4, visible))
    before_focus = snapshot()
    combo_path = open_socket(combo)
    cover_offset = log_size("x-menu-cover")

    def combo_events(start):
        return [event for _, event in probe_events("x-combo", start)]

    def combo_menu():
        """The menu, a window of the probe's process other than the probe's."""
        pid = client("hm-x-combo")["pid"]
        return next((c for c in clients() if c["pid"] == pid and c["address"] != combo), None)

    def menu_items(start):
        """The items of the menu that opened after event start, by choice: X, Y, W and H in the menu window."""
        wait_for("menu items", lambda: any(e.startswith("menu ") for e in combo_events(start)), timeout=10)
        return {e.split()[1]: [int(n) for n in e.split()[2:]] for e in combo_events(start) if e.startswith("menu ")}

    def center(item):
        x, y, width, height = item
        return x + width // 2, y + height // 2

    # A click opens the menu, which stays open past the second after which the emulated focus of input ends.
    start = len(probe_events("x-combo"))
    send(combo_path, "move", 50, 215, "click")
    wait_for("the menu", lambda: combo_menu() is not None, timeout=10)
    items = menu_items(start)
    time.sleep(1.5)
    assert combo_menu() is not None and "popup hidden" not in combo_events(start), combo_events(start)

    # A click in the menu's own window picks an item, as an act of hyprmasq-mcp in the new window it reports does.
    start = len(probe_events("x-combo"))
    send(open_socket(combo_menu()["address"]), "move", *center(items["Two"]), "click")
    wait_for("Two", lambda: "changed Two" in combo_events(start), timeout=10)
    wait_for("the menu closed", lambda: combo_menu() is None)

    # So does a click in the probe's window where the menu lies over it, which GTK opens with Two over the combo box.
    start = len(probe_events("x-combo"))
    send(combo_path, "move", 50, 215, "click")
    wait_for("the menu", lambda: combo_menu() is not None, timeout=10)
    items = menu_items(start)
    (menu_x, menu_y), (combo_x, combo_y) = combo_menu()["at"], client("hm-x-combo")["at"]
    x, y = center(items["Four"])
    send(combo_path, "move", menu_x - combo_x + x, menu_y - combo_y + y, "click")
    wait_for("Four", lambda: "changed Four" in combo_events(start), timeout=10)
    wait_for("the menu closed", lambda: combo_menu() is None)

    # And a press on the combo box, a drag onto an item and the release, with Four over the combo box.
    start = len(probe_events("x-combo"))
    send(combo_path, "drag", 50, 215, 50, 215 + items["One"][1] - items["Four"][1])
    wait_for("One", lambda: "changed One" in combo_events(start), timeout=10)
    wait_for("the menu closed", lambda: combo_menu() is None)

    # The cover got none of it, and is above the probe again: the X server's own pointer, which goes by the stacking
    # order, reaches it there.
    assert "ButtonPress" not in log_since("x-menu-cover", cover_offset)
    start = len(probe_events("x-combo"))
    alice(f"env {x_env} xdotool mousemove 50 215 click 1")
    wait_for("a click on the cover", lambda: "ButtonPress" in log_since("x-menu-cover", cover_offset), timeout=10)
    assert not combo_events(start), combo_events(start)
    assert snapshot() == before_focus
    machine.succeed("pkill -f '[x]ev -name hm-x-menu-cover'; pkill -f '[h]yprmasq-combo-probe.py hm-x-combo'")
    wait_for("the cover and the probe closed", lambda: client("hm-x-menu-cover") is None and client("hm-x-combo") is None)

with subtest("a Chromium select on X11 on a hidden workspace opens and picks by pointer"):
    # Chromium closes the select's popup, a window of its own, once its window loses the X focus. The user has a second
    # Wayland window to move to.
    focus_workspace(91)
    menu_user = foot("hm-x-menu-user")
    focus_workspace(93)
    spawn(
        f"env {x_env} chromium --ozone-platform=x11 --user-data-dir=/tmp/hm-chromium --no-first-run "
        "--no-default-browser-check --disable-gpu --enable-logging=stderr --app=file:///etc/hyprmasq-test/select.html",
        "x-select",
    )
    wait_for("chromium", lambda: client("hm-x-select") is not None, timeout=120)
    machine.wait_until_succeeds("grep -q '\"probe ready\"' /tmp/hyprmasq-x-select.log", timeout=60)
    select = client("hm-x-select")["address"]
    focus_workspace(4)
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("foot focus", lambda: snapshot() == (4, visible))
    before_focus = snapshot()
    select_path = open_socket(select)

    def select_events():
        lines = machine.succeed("cat /tmp/hyprmasq-x-select.log").splitlines()
        return [line.split('"probe ', 1)[1].split('"', 1)[0] for line in lines if '"probe ' in line]

    def select_popup():
        """The popup, a window of Chromium's process other than the page's."""
        pid = client("hm-x-select")["pid"]
        return next((c for c in clients() if c["pid"] == pid and c["address"] != select), None)

    def option(n):
        """The middle of the nth of the popup's six options, which share its height, in the popup."""
        width, height = select_popup()["size"]
        return width // 2, int(height / 6 * (n + 0.5))

    # A click opens the popup, which stays open past the second after which the emulated focus of input ends, also
    # while the user moves between Wayland windows.
    start = len(select_events())
    send(select_path, "move", 60, 35, "click")
    wait_for("the popup", lambda: select_popup() is not None, timeout=10)
    dispatch(f'hl.dsp.focus({{ window = "address:{menu_user}" }})')
    wait_for("the user in another window", lambda: snapshot() == (91, menu_user))
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("the user back", lambda: snapshot() == before_focus)
    time.sleep(1.5)
    assert select_popup() is not None and "window-blur" not in select_events()[start:], select_events()[start:]

    # A click on an option in the popup's own window picks it, and the X focus goes back once the popup has closed.
    start = len(select_events())
    send(open_socket(select_popup()["address"]), "move", *option(2), "click")
    wait_for("Two", lambda: "changed Two" in select_events()[start:], timeout=10)
    wait_for("the popup closed", lambda: select_popup() is None)
    wait_for("the X focus back", lambda: "window-blur" in select_events()[start:], timeout=10)

    # So does a click in Chromium's window where the popup lies over it.
    start = len(select_events())
    send(select_path, "move", 60, 35, "click")
    wait_for("the popup", lambda: select_popup() is not None, timeout=10)
    (popup_x, popup_y), (select_x, select_y) = select_popup()["at"], client("hm-x-select")["at"]
    x, y = option(4)
    send(select_path, "move", popup_x - select_x + x, popup_y - select_y + y, "click")
    wait_for("Four", lambda: "changed Four" in select_events()[start:], timeout=10)
    wait_for("the popup closed", lambda: select_popup() is None)
    wait_for("the X focus back", lambda: "window-blur" in select_events()[start:], timeout=10)

    # Closing the socket while the popup is open gives the X focus back, and Chromium closes the popup.
    send(select_path, "move", 60, 35, "click")
    wait_for("the popup", lambda: select_popup() is not None, timeout=10)
    start = len(select_events())
    assert hypr(f"hyprmasq eis close {select}") == "ok"
    wait_for("the popup closed", lambda: select_popup() is None, timeout=10)
    assert "window-blur" in select_events()[start:], select_events()[start:]
    assert snapshot() == before_focus
    machine.succeed("pkill -f '[u]ser-data-dir=/tmp/hm-chromium'")
    dispatch(f'hl.dsp.window.close({{ window = "address:{menu_user}" }})')
    wait_for("chromium and the user's second window closed", lambda: client("hm-x-select") is None and client("hm-x-menu-user") is None)
