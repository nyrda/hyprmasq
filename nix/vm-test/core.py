with subtest("screenshots on hidden workspaces, colors and orientation"):
    colors = [
        ("red", "ff0000", (255, 0, 0)),
        ("green", "00ff00", (0, 255, 0)),
        ("blue", "0000ff", (0, 0, 255)),
    ]
    windows = []
    for n, (name, color, rgb) in enumerate(colors, 1):
        focus_workspace(n)
        # Later subtests use these windows until the end, which a slow host reaches more than 15 minutes later.
        command = "sh -c " + shlex.quote(
            "printf '\\033[?25lTOP OF WINDOW\\n'; sleep infinity"
        )
        address = foot(
            "hm-" + name,
            command,
            f"-o colors-dark.background={color} -o colors-dark.foreground=ffffff",
        )
        wait_drawn(address, 500)
        windows.append((name, address, rgb))
    focus_workspace(4)
    visible = foot("hm-visible")
    for name, address, rgb in windows:
        assert_png(screenshot(address, name), rgb)
    assert snapshot() == (4, visible)

with subtest("screenshots are encoded while Hyprland keeps running"):
    red, green = windows[0][1], windows[1][1]
    # A screenshot to a FIFO waits until the FIFO is read.
    fifo("/tmp/hm-fifo.png")
    hypr_background(
        f"-j hyprmasq screenshot {red} /tmp/hm-fifo.png", "/tmp/hm-fifo-reply"
    )
    waiting_writers(1)
    assert json.loads(hypr("-j activeworkspace"))["id"] == 4
    machine.succeed("test ! -s /tmp/hm-fifo-reply")
    machine.succeed("timeout 30 cat /tmp/hm-fifo.png > /tmp/hm-fifo-copy.png", timeout=60)
    machine.wait_until_succeeds("test -s /tmp/hm-fifo-reply", timeout=30)
    reply = json.loads(machine.succeed("cat /tmp/hm-fifo-reply"))
    assert reply["ok"] and reply["path"] == "/tmp/hm-fifo.png", reply
    assert_png("/tmp/hm-fifo-copy.png", (255, 0, 0))
    # A full disk shows up when the file is closed.
    reply = json.loads(hypr(f"-j hyprmasq screenshot {red} /dev/full"))
    assert not reply["ok"] and "/dev/full" in reply["error"], reply

    # A batch answers once every screenshot in it is written, with the other replies in place.
    parts = batch(
        f"hyprmasq screenshot {red} /tmp/hm-batch-red.png; activeworkspace; "
        f"hyprmasq screenshot {green} /tmp/hm-batch-green.png"
    )
    assert len(parts) == 3 and parts[0] == "ok" and parts[2] == "ok", parts
    assert parts[1].startswith("workspace ID 4 "), parts
    assert_png("/tmp/hm-batch-red.png", (255, 0, 0))
    assert_png("/tmp/hm-batch-green.png", (0, 255, 0))
    parts = [
        json.loads(part)
        for part in batch(
            f"hyprmasq screenshot {green} /tmp/hm-batch-json.png; activeworkspace",
            "-j",
        )
    ]
    assert parts[0]["ok"] and parts[0]["path"] == "/tmp/hm-batch-json.png", parts
    assert parts[1]["id"] == 4, parts
    assert_png("/tmp/hm-batch-json.png", (0, 255, 0))

with subtest("screenshot regions in logical pixels"):
    green = windows[1][1]
    white = "r > 200 and g > 200 and b > 200"

    def region_shot(name, *region):
        path = f"/tmp/hyprmasq-{name}.png"
        reply = json.loads(hypr(f"-j hyprmasq screenshot {green} {path} " + " ".join(map(str, region))))
        assert reply["ok"], reply
        machine.copy_from_machine(path)
        return path, reply

    _, reply = region_shot("region-full")
    assert reply["scale"] == 1 and reply["region"] == region(0, 0, 640, 480), reply
    assert (reply["width"], reply["height"]) == (640, 480), reply
    # The white text is at the left of the first row. A readback from another part of the window finds it in the
    # other regions instead.
    top, reply = region_shot("region-top", 0, 0, 200, 30)
    assert (reply["width"], reply["height"]) == (200, 30) and reply["region"] == region(0, 0, 200, 30), reply
    assert count_pixels(top, (0, 0, 200, 30), white) > 20
    for name, x, y in [("region-bottom", 0, 440), ("region-right", 440, 0)]:
        path, _ = region_shot(name, x, y, 200, 30)
        assert count_pixels(path, (0, 0, 200, 30), "g > 200 and r < 60 and b < 60") == 200 * 30
    _, reply = region_shot("region-clamped", 600, 450, 100, 100)
    assert (reply["width"], reply["height"]) == (40, 30) and reply["region"] == region(600, 450, 40, 30), reply
    for args, message in [
        ("640 0 10 10", "outside"),
        ("0 0 0 10", "positive size"),
        ("0 0 1.5 10", "whole logical pixels"),
        ("0 0 10", "usage"),
    ]:
        reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-bad-region.png {args}"))
        assert not reply["ok"] and message in reply["error"], (args, reply)
    machine.succeed("test ! -e /tmp/hm-bad-region.png")

    # At scale 2 the same logical region has twice the pixels.
    def set_scale(scale):
        hypr("eval " + shlex.quote(
            f'hl.monitor({{ output = "HYPRMASQ-TEST", mode = "640x480@60", position = "0x0", scale = "{scale}" }})'
        ))
        wait_for(f"scale {scale}", lambda: client("hm-green")["size"] == [640 // scale, 480 // scale])
        time.sleep(0.5)

    set_scale(2)
    top, reply = region_shot("region-scale2", 0, 0, 100, 15)
    assert reply["scale"] == 2 and (reply["width"], reply["height"]) == (200, 30), reply
    assert reply["region"] == region(0, 0, 100, 15), reply
    assert count_pixels(top, (0, 0, 200, 30), white) > 20
    # At a fractional scale the region widens to whole device pixels, and the reply names what was captured:
    # device pixels 1 to 13 of 1.25 per logical pixel.
    set_scale(1.25)
    _, reply = region_shot("region-fractional", 1, 1, 10, 10)
    assert reply["scale"] == 1.25 and (reply["width"], reply["height"]) == (13, 13), reply
    assert all(abs(reply["region"][k] - v) < 1e-9 for k, v in region(0.8, 0.8, 10.4, 10.4).items()), reply
    set_scale(1)

with subtest("socket lifecycle"):
    doomed = windows[0][1]
    path = open_socket(doomed)
    assert hypr(f"hyprmasq eis close {doomed}") == "ok"
    machine.succeed(f"test ! -e {path} && test ! -e {path}.lock")
    path = open_socket(doomed)
    dispatch('hl.dsp.window.close({ window = "address:' + doomed + '" })')
    wait_for("window teardown", lambda: client("hm-red") is None)
    machine.wait_until_succeeds(f"test ! -e {path} && test ! -e {path}.lock")
    assert not json.loads(hypr("-j hyprmasq eis list"))

with subtest("hidden shell input, focus stability and frame callbacks"):
    focus_workspace(5)
    target = foot("hm-shell")
    wait_drawn(target, 1000)
    focus_workspace(4)
    before_focus = snapshot()
    before = screenshot(target, "shell-before")
    path = open_socket(target)
    send(path, "type", "echo ok > /tmp/marker", "key", 28)
    machine.wait_until_succeeds('test "$(cat /tmp/marker)" = ok')
    assert snapshot() == before_focus
    time.sleep(1)
    after = screenshot(target, "shell-after")
    machine.fail(f"cmp -s {before} {after}")
    assert snapshot() == before_focus
