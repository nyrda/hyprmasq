with subtest("virtual cursor and click ripple on the active workspace"):
    # The default color, rgb(6ec6ff), and anything blended from it over foot's dark background. Checks stay near
    # the virtual cursor: Hyprland's own cursor, at the center of the screen, is cyan.
    light_blue = "abs(r - 0x6E) <= 40 and abs(g - 0xC6) <= 40 and b >= 0xD8"
    bluish = "b - r > 50"
    magenta = "r > 200 and g < 60 and b > 200"
    assert f"int: {0xFF6EC6FF}" in hypr("getoption plugin:hyprmasq:virtual_cursor_color")
    focus_workspace(17)
    pointed = foot("hm-cursor")
    pointed_path = hypr(f"hyprmasq eis open {pointed}")
    x, y = client("hm-cursor")["at"]
    x, y = x + 200, y + 150
    cursor_box = (x - 4, y - 4, x + 40, y + 40)
    around = (x - 30, y - 30, x + 50, y + 50)
    send(pointed_path, "move", 200, 150)
    pointing = capture_output("cursor")
    assert count_pixels(pointing, cursor_box, light_blue) > 20
    # The ripple rings the click point, also on the side away from the arrow, where the arrow itself draws nothing.
    ripple_box = (x - 24, y - 3, x - 6, y + 3)
    assert count_pixels(pointing, ripple_box, bluish) == 0
    # The ripple lasts 450 ms. It counts in ripple_box from about 35 ms after the press, once the ring has grown
    # into it, to about 300 ms, while it is still bright enough. grim starts 50 ms after the click, and a capture
    # that has ended within 250 ms of the click's start lies in that time, so it has to show the ripple. A busy
    # host can make grim take longer, and a later capture without the ripple proves nothing: the click is then
    # repeated once its ripple is gone, so that each capture can only show the ripple of its own click.
    click_path = "/tmp/hyprmasq-cursor-click.png"
    for attempt in range(1, 9):
        elapsed = int(alice(
            f"start=$(date +%s%N); hyprmasq-input --no-pace --socket {shlex.quote(pointed_path)} click && sleep 0.05 && "
            f"{client_env} grim {click_path} && echo $(( ($(date +%s%N) - start) / 1000000 ))"
        ).strip())
        machine.copy_from_machine(click_path)
        if count_pixels(click_path, ripple_box, bluish) > 0:
            break
        assert elapsed > 250, f"no click ripple in a capture that ended {elapsed} ms after the click started"
        machine.log(f"no click ripple in the capture after click {attempt}, which ended {elapsed} ms after it started")
        time.sleep(0.5)
    else:
        raise AssertionError("eight captures after a click all ended too late to show its ripple")
    # The cursor stays 3 seconds after the last input, then fades out over 0.4 seconds.
    time.sleep(4)
    assert count_pixels(capture_output("cursor-faded"), around, bluish) == 0
    # Input at the same point while the cursor fades out brings it back at full strength.
    send(pointed_path, "move", 200, 150)
    time.sleep(3.15)
    send(pointed_path, "move", 200, 150)
    time.sleep(0.2)
    assert count_pixels(capture_output("cursor-refreshed"), cursor_box, light_blue) > 20
    hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor_timeout = 500 } } })"))
    send(pointed_path, "move", 200, 150)
    time.sleep(1.5)
    assert count_pixels(capture_output("cursor-short"), around, bluish) == 0
    hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor_timeout = 3000 } } })"))
    # Nothing is drawn for a window on a hidden workspace.
    focus_workspace(4)
    send(pointed_path, "move", 200, 150)
    assert count_pixels(capture_output("cursor-hidden"), around, bluish) == 0
    focus_workspace(17)
    hypr("eval " + shlex.quote('hl.config({ plugin = { hyprmasq = { virtual_cursor_color = "rgb(ff00ff)" } } })'))
    send(pointed_path, "move", 200, 150)
    assert count_pixels(capture_output("cursor-magenta"), cursor_box, magenta) > 20
    hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor = false } } })"))
    time.sleep(0.5)
    send(pointed_path, "move", 200, 150)
    assert count_pixels(capture_output("cursor-off"), around, magenta) == 0
    hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { virtual_cursor = true } } })"))
    assert hypr(f"hyprmasq eis close {pointed}") == "ok"
