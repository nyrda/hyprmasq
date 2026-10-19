with subtest("screenshots fit within a maximum size, rendered at that size"):
    green = windows[1][1]
    path, reply = shot(green, "max-half", "max 320")
    assert (reply["width"], reply["height"], reply["scale"]) == (320, 240, 0.5), reply
    assert reply["region"] == region(0, 0, 640, 480) and pixels(path)[-1] == (320, 240), reply
    assert_png(path, (0, 255, 0))
    # A quarter is halved twice; a fifth also gets a bilinear pass after the halving.
    for maximum, width, height in [(160, 160, 120), (128, 128, 96), (100, 100, 75)]:
        path, reply = shot(green, f"max-{maximum}", f"max {maximum}")
        assert (reply["width"], reply["height"]) == (width, height) and reply["scale"] == maximum / 640, reply
        assert reply["region"] == region(0, 0, 640, 480), reply
        assert near(pixels(path, (width // 2, height // 2))[0], (0, 255, 0)), maximum
    assert_png("/tmp/hyprmasq-p-max-160.png", (0, 255, 0))
    # Never above the monitor's scale, and an image that already fits is not touched.
    for maximum in [640, 4000]:
        _, reply = shot(green, f"max-full-{maximum}", f"max {maximum}")
        assert (reply["width"], reply["height"], reply["scale"]) == (640, 480, 1), reply
    # A region is fitted the same way. Its pixels start at its origin, and the size widens to whole image pixels.
    path, reply = shot(green, "max-region", "0 0 200 30 max 100")
    assert (reply["width"], reply["height"], reply["scale"]) == (100, 15, 0.5) and reply["region"] == region(0, 0, 200, 30), reply
    _, reply = shot(green, "max-odd", "3 5 101 51 max 50")
    assert (reply["width"], reply["height"]) == (50, 26) and reply["scale"] == 50 / 101, reply
    assert reply["region"]["x"] == 3 and reply["region"]["y"] == 5 and abs(reply["region"]["height"] - 26 * 101 / 50) < 1e-9, reply
    for arguments, message in [
        ("max 0", "max needs"),
        ("max x", "max needs"),
        ("max", "usage"),
        ("max 10 max 20", "usage"),
        ("popups popups", "usage"),
        ("0 0 10 10 max 10 later", "usage"),
    ]:
        reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-p-bad.png {arguments}"))
        assert not reply["ok"] and reply["code"] == "invalid_arguments" and message in reply["error"], (arguments, reply)
    machine.succeed("test ! -e /tmp/hm-p-bad.png")

    # scale is image pixels per logical pixel, below the monitor's when max shrank the image.
    monitor_scale(2, 320)
    _, reply = shot(green, "max-scale2", "max 640")
    assert (reply["width"], reply["height"], reply["scale"]) == (640, 480, 2), reply
    path, reply = shot(green, "max-scale2-shrunk", "max 320")
    assert (reply["width"], reply["height"], reply["scale"]) == (320, 240, 1), reply
    assert reply["region"] == region(0, 0, 320, 240), reply
    assert_png(path, (0, 255, 0))
    monitor_scale(1.25, 512)
    _, reply = shot(green, "max-fractional", "max 256")
    assert (reply["width"], reply["height"], reply["scale"]) == (256, 192, 0.5) and reply["region"] == region(0, 0, 512, 384), reply
    monitor_scale(1, 640)

with subtest("one render gives an image and its detail image, also as raw pixels"):
    green = windows[1][1]
    detail = "/tmp/hm-p-detail.raw"
    # A shrunk image gets a detail image of the same render at the monitor's pixels, where they are fewer than the
    # logical ones.
    reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-p-raw.raw max 320 popups raw detail {detail}"))
    assert reply["ok"] and (reply["width"], reply["height"], reply["scale"]) == (320, 240, 0.5), reply
    assert reply["detail"] == {"path": detail, "width": 640, "height": 480, "scale": 1, "region": region(0, 0, 640, 480), "max": None}, reply
    assert all(near(p, (0, 255, 0)) for p in raw_pixels("/tmp/hm-p-raw.raw", 320, 240, (160, 120), (319, 239)))
    assert all(near(p, (0, 255, 0)) for p in raw_pixels(detail, 640, 480, (320, 240), (639, 479)))
    # An image with as many pixels has none, and its PNG is the detail image.
    machine.succeed(f"rm -f {detail}")
    path, reply = shot(green, "detail-none", f"max 640 detail {detail}")
    assert reply["detail"] is None and (reply["width"], reply["scale"]) == (640, 1), reply
    machine.succeed(f"test ! -e {detail}")
    # Above scale 1, the detail image has a pixel per logical pixel.
    monitor_scale(2, 320)
    _, reply = shot(green, "detail-scale2", f"max 160 detail {detail}")
    assert (reply["width"], reply["scale"]) == (160, 0.5), reply
    assert reply["detail"] == {"path": detail, "width": 320, "height": 240, "scale": 1, "region": region(0, 0, 320, 240), "max": 320}, reply
    assert near(raw_pixels(detail, 320, 240, (160, 120))[0], (0, 255, 0))
    _, reply = shot(green, "detail-scale2-logical", f"max 320 detail {detail}")
    assert reply["detail"] is None and reply["scale"] == 1, reply
    monitor_scale(1, 640)
    for arguments, message in [
        ("detail", "usage"),
        ("raw raw", "usage"),
        ("detail relative.raw", "absolute"),
        ("detail /tmp/hm-p-bad.png", "a path of its own"),
    ]:
        reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-p-bad.png {arguments}"))
        assert not reply["ok"] and reply["code"] == "invalid_arguments" and message in reply["error"], (arguments, reply)
    machine.succeed("test ! -e /tmp/hm-p-bad.png")

with subtest("popups outside the window are captured and get pointer input"):
    # The popup covers 250,150 to 350,250 of the 300x200 window, so it reaches past its right and bottom edges.
    popout = popup_probe("hm-p-popout", 34, 250, 150, 100, 100)
    machine.succeed("grep -q 'popup at 250 150 100 100' /tmp/hyprmasq-p-popout.log")
    # Without popups, the box is the window, with the part of the popup over it, also on a hidden workspace.
    path, reply = shot(popout, "popout-window")
    assert (reply["width"], reply["height"]) == (300, 200) and reply["region"] == region(0, 0, 300, 200), reply
    inside, window_pixel, _ = pixels(path, (275, 175), (50, 50))
    assert near(inside, popup_rgb) and near(window_pixel, window_rgb), (inside, window_pixel)
    # With popups, the box grows to the popup. Pixels outside both stay transparent.
    path, reply = shot(popout, "popout-popups", "popups")
    assert (reply["width"], reply["height"]) == (350, 250) and reply["region"] == region(0, 0, 350, 250), reply
    outside, window_pixel, empty, size = pixels(path, (330, 230), (50, 50), (330, 50))
    assert near(outside, popup_rgb) and near(window_pixel, window_rgb) and empty[3] == 0 and size == (350, 250), (outside, window_pixel, empty)
    # A region can reach into the box, and is cut to it.
    path, reply = shot(popout, "popout-region", "280 180 100 100 popups")
    assert (reply["width"], reply["height"]) == (70, 70) and reply["region"] == region(280, 180, 70, 70), reply
    assert all(near(p, popup_rgb) for p in pixels(path, (5, 5), (65, 65), (35, 35))[:-1])
    _, reply = shot(popout, "popout-region-window", "280 180 100 100")
    assert reply["region"] == region(280, 180, 20, 20), reply
    _, reply = shot(popout, "popout-popups-max", "popups max 175")
    assert (reply["width"], reply["height"], reply["scale"]) == (175, 125, 0.5) and reply["region"] == region(0, 0, 350, 250), reply
    reply = json.loads(hypr(f"-j hyprmasq screenshot {popout} /tmp/hm-p-bad.png 400 300 10 10 popups"))
    assert not reply["ok"] and reply["code"] == "invalid_arguments" and "outside" in reply["error"], reply

    # The pointer region covers the window and the rest of its monitor right of and below it.
    popout_path = open_socket(popout)
    alice(f"hyprmasq-focus-probe regions {shlex.quote(popout_path)} 500 > /tmp/hyprmasq-p-regions.log")
    assert machine.succeed("cat /tmp/hyprmasq-p-regions.log") == "pointer 0 0 300 200, 300 0 240 200, 0 200 540 180\n"
    # A click on the part of the popup outside the window reaches the popup, at its own coordinates.
    offset = log_size("p-popout")
    send(popout_path, "move", 330, 220, "click")
    machine.wait_until_succeeds("grep -q 'button popup 0' /tmp/hyprmasq-p-popout.log", timeout=10)
    events = log_since("p-popout", offset)
    for line in ["pointer enter popup", "motion popup 80 70", "button popup 1", "button popup 0"]:
        assert line in events.splitlines(), events
    # The press gives the keyboard focus to the window's own surface, where keys go, before the button.
    assert events.splitlines().index("keyboard enter hm-p-popout") < events.splitlines().index("button popup 1"), events
    assert "button hm-p-popout" not in events, events
    # A point outside the window and its popups is clamped to the window, to the last 1/256 of a pixel before its edge.
    offset = log_size("p-popout")
    send(popout_path, "move", 500, 50, "click")
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-popout.log | grep -q 'button hm-p-popout 0'", timeout=10)
    events = log_since("p-popout", offset)
    assert "motion hm-p-popout 299.996 50" in events.splitlines() and "button popup" not in events, events

with subtest("a point in the last pixel of the right and bottom edges keeps its fraction"):
    # The last column and row of a native shot at scales 2 and 1.5 lie inside the window's last logical pixel, for an
    # even and an odd size, and so does the last of the 1280-pixel overview that hyprmasq-mcp takes by default of the
    # odd window at scale 2. Their points, region origin + shot pixel / scale, arrive to wl_fixed's 1/256 of a logical
    # pixel. A point beyond the window arrives on the last 1/256 before its edges, and one on a popup beyond the window
    # at the popup. A 1920x1080 monitor fits the window at both scales.
    def edge_monitor(scale):
        hypr("eval " + shlex.quote(
            f'hl.monitor({{ output = "HYPRMASQ-TEST", mode = "1920x1080@60", position = "0x0", scale = "{scale}" }})'
        ))
        wait_for(f"1920x1080 at scale {scale}", lambda: any(
            m["name"] == "HYPRMASQ-TEST" and m["width"] == 1920 and abs(m["scale"] - scale) < 1e-6 for m in json.loads(hypr("-j monitors"))
        ))
        time.sleep(0.5)

    def placed(address, width, height):
        float_window(address, 10, 10, width, height)
        wait_for(f"{address} drawn at {width}x{height}", lambda: not window_state(address)["resizing"])

    def received(log, offset, surface):
        """The last point that surface got in the log since offset, once a button went up there."""
        machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-{log}.log | grep -q 'button {surface} 0$'", timeout=10)
        events = log_since(log, offset)
        motions = [line.split()[-2:] for line in events.splitlines() if f"motion {surface} " in line]
        assert motions, events
        return tuple(float(v) for v in motions[-1])

    def arrives(got, expected):
        assert all(abs(g - e) <= 1 / 256 for g, e in zip(got, expected)), (got, expected)

    # focus-probe's focus mode requests nothing on a click, so each click only moves and presses.
    focus_workspace(96)
    spawn("hyprmasq-focus-probe focus hm-p-edge", "p-edge")
    wait_for("hm-p-edge", lambda: client("hm-p-edge") is not None)
    edge = client("hm-p-edge")["address"]
    # The popup covers 320,20 to 420,60 of the 300x200 window, past its right edge.
    edgepop = popup_probe("hm-p-edgepop", 97, 320, 20, 100, 40)
    edge_path, edgepop_path = open_socket(edge), open_socket(edgepop)
    for scale in [2, 1.5]:
        edge_monitor(scale)
        for width, height in [(640, 480), (641, 481)]:
            placed(edge, width, height)
            _, native = shot(edge, f"edge-{scale}-{width}")
            assert native["scale"] == scale, native
            replies = [native]
            if (scale, width) == (2, 641):
                _, overview = shot(edge, "edge-overview", "max 1280")
                assert overview["width"] == 1280 and overview["scale"] == 1280 / 641, overview
                replies.append(overview)
            for reply in replies:
                origin = reply["region"]
                point = (origin["x"] + (reply["width"] - 1) / reply["scale"], origin["y"] + (reply["height"] - 1) / reply["scale"])
                assert width - 1 < point[0] < width and height - 1 < point[1] < height, (point, reply)
                offset = log_size("p-edge")
                send(edge_path, "move", repr(point[0]), repr(point[1]), "click")
                arrives(received("p-edge", offset, "hm-p-edge"), point)
            offset = log_size("p-edge")
            send(edge_path, "move", width + 30, height + 20, "click")
            got = received("p-edge", offset, "hm-p-edge")
            arrives(got, (width - 1 / 256, height - 1 / 256))
            assert got[0] < width and got[1] < height, got
        offset = log_size("p-edgepop")
        send(edgepop_path, "move", 330.5, 30.25, "click")
        arrives(received("p-edgepop", offset, "popup"), (10.5, 10.25))
        assert "button hm-p-edgepop" not in log_since("p-edgepop", offset)
    machine.succeed("pkill -f 'hyprmasq-focus-probe [f]ocus hm-p-edge'; pkill -f 'hyprmasq-focus-probe [p]opup hm-p-edgepop'")
    wait_for("edge probes closed", lambda: client("hm-p-edge") is None and client("hm-p-edgepop") is None)
    monitor_scale(1, 640)

with subtest("a region with popups can start above and left of the window"):
    # The popup covers -40,-30 to 20,20 of the window.
    popleft = popup_probe("hm-p-popleft", 35, -40, -30, 60, 50)
    path, reply = shot(popleft, "popleft-popups", "popups")
    assert (reply["width"], reply["height"]) == (340, 230) and reply["region"] == region(-40, -30, 340, 230), reply
    popup_pixel, window_pixel, empty, _ = pixels(path, (5, 5), (90, 80), (300, 5))
    assert near(popup_pixel, popup_rgb) and near(window_pixel, window_rgb) and empty[3] == 0, (popup_pixel, window_pixel, empty)
    path, reply = shot(popleft, "popleft-region", "-40 -30 100 80 popups")
    assert (reply["width"], reply["height"]) == (100, 80) and reply["region"] == region(-40, -30, 100, 80), reply
    popup_pixel, window_pixel, _ = pixels(path, (5, 5), (90, 70))
    assert near(popup_pixel, popup_rgb) and near(window_pixel, window_rgb), (popup_pixel, window_pixel)
    # A region that starts before the box is cut to it.
    _, reply = shot(popleft, "popleft-cut", "-100 -100 100 100 popups")
    assert reply["region"] == region(-40, -30, 40, 30), reply
    # Without popups, a negative region is still an error.
    reply = json.loads(hypr(f"-j hyprmasq screenshot {popleft} /tmp/hm-p-bad.png -40 -30 100 80"))
    assert not reply["ok"] and reply["code"] == "invalid_arguments" and "non-negative" in reply["error"], reply
    machine.succeed("test ! -e /tmp/hm-p-bad.png")
    # The part of the popup over the window gets pointer input.
    offset = log_size("p-popleft")
    popleft_path = open_socket(popleft)
    send(popleft_path, "move", 10, 10, "click")
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-popleft.log | grep -q 'button popup 0'", timeout=10)
    assert "motion popup 50 40" in log_since("p-popleft", offset).splitlines()
    # The part above and left of the window cannot: libei region offsets are unsigned, so the sender refuses it.
    status, result = input_json(popleft_path, "move", -20, -10, "click")
    assert status == 2 and result["delivery"] == "none" and result["error"]["code"] == "invalid_arguments", (status, result)
    assert "-20,-10 is outside the pointer regions, which span 0,0 to " in result["error"]["message"], result

with subtest("the pointer device follows resizes and moves"):
    def regions_line(address):
        (x, y), (width, height) = geometry(address)
        right, bottom = max(width, 640 - x), max(height, 480 - y)
        parts = [f"0 0 {width} {height}"]
        if right > width:
            parts.append(f"{width} 0 {right - width} {height}")
        if bottom > height:
            parts.append(f"0 {height} {right} {bottom - height}")
        return "pointer " + ", ".join(parts)

    def configures():
        return int(machine.succeed("grep -c 'configured hm-p-popout' /tmp/hyprmasq-p-popout.log"))

    before, configured = regions_line(popout), configures()
    spawn(f"hyprmasq-focus-probe regions {shlex.quote(popout_path)} 5000", "p-regions-follow")
    machine.wait_until_succeeds("grep -q pointer /tmp/hyprmasq-p-regions-follow.log", timeout=10)
    window = f'window = "address:{popout}"'
    dispatch(f"hl.dsp.window.resize({{ x = 400, y = 300, {window} }})")
    wait_for("resize", lambda: client("hm-p-popout")["size"] == [400, 300])
    resized = regions_line(popout)
    time.sleep(0.3)
    dispatch(f"hl.dsp.window.move({{ x = 150, y = 120, {window} }})")
    wait_for("move", lambda: client("hm-p-popout")["at"] == [150, 120])
    moved = regions_line(popout)
    machine.wait_until_fails("pgrep -f 'hyprmasq-focus-probe [r]egions'", timeout=10)
    lines = machine.succeed("cat /tmp/hyprmasq-p-regions-follow.log").splitlines()
    assert lines == [before, "pointer removed", resized, "pointer removed", moved], (lines, before, resized, moved)
    # Input reaches the parts of the window that only exist since the resize, once the probe has drawn them.
    wait_for("the probe drawn at its new size", lambda: configures() > configured, timeout=10)
    time.sleep(0.5)
    offset = log_size("p-popout")
    send(popout_path, "move", 380, 280, "click")
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-popout.log | grep -q 'button hm-p-popout 0'", timeout=10)
    assert "motion hm-p-popout 380 280" in log_since("p-popout", offset).splitlines()
    for title in ["hm-p-popout", "hm-p-popleft"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
        wait_for(f"{title} closed", lambda: client(title) is None)

with subtest("a window that has not caught up with a resize says so in its state, shots and waits"):
    # focus-probe's resize mode draws its right edge green. On SIGUSR1 it holds back configures with a new size,
    # neither acking nor drawing them with hold, acking them but not drawing with ack, and answers them on the next.
    edge_rgb = (0x22, 0xCC, 0x22)

    def probe_lines(name):
        return machine.succeed(f"cat /tmp/hyprmasq-{name}.log").splitlines()

    for mode in ["hold", "ack"]:
        title, name = f"hm-p-resize-{mode}", f"p-resize-{mode}"
        toggle = f"pkill -USR1 -f 'hyprmasq-focus-probe [r]esize {title} '"
        focus_workspace(26)
        spawn(f"hyprmasq-focus-probe resize {title} {mode}", name)
        wait_for(title, lambda: client(title) is not None)
        resized = client(title)["address"]
        # Alone on its workspace, it drew at the size Hyprland predicted before mapping it, and acked the configure
        # of that size after the mapping without a commit. Its content has the window's size, so it is not resizing.
        machine.wait_until_succeeds(f"grep -qx 'kept 640 480' /tmp/hyprmasq-{name}.log", timeout=10)
        assert not window_state(resized)["resizing"], window_state(resized)
        # A neighbour halves it, which it draws at once.
        neighbour = foot(f"hm-p-neighbour-{mode}", "sleep 900")
        focus_workspace(4)
        wait_for("the probe drawn at half the width", lambda: "drew 320 480" in probe_lines(name), timeout=10)
        state = window_state(resized)
        assert state["size"] == {"width": 320, "height": 480} and not state["resizing"], state
        open_socket(resized)

        # Closing the neighbour on the hidden workspace gives the probe the whole workspace, which it holds back.
        machine.succeed(toggle)
        machine.wait_until_succeeds(f"grep -qx holding /tmp/hyprmasq-{name}.log", timeout=10)
        dispatch(f'hl.dsp.window.close({{ window = "address:{neighbour}" }})')
        wait_for("the neighbour closed", lambda: client(f"hm-p-neighbour-{mode}") is None)
        machine.wait_until_succeeds(f"grep -qx 'held 640 480' /tmp/hyprmasq-{name}.log", timeout=10)
        state = window_state(resized)
        assert state["size"] == {"width": 640, "height": 480} and state["resizing"], state
        assert "\nresizing: true\n" in hypr(f"hyprmasq state {resized}")
        # The shot has the window's new size and shows the frame of the old one: it ends at 320 with the green edge,
        # beyond which Hyprland either leaves the image transparent or repeats the edge.
        path, shot_reply = shot(resized, f"resize-{mode}-stale")
        assert shot_reply["resizing"] and (shot_reply["width"], shot_reply["height"]) == (640, 480), shot_reply
        inside, edge, beyond, _ = pixels(path, (160, 240), (316, 240), (480, 240))
        machine.log(f"stale frame with {mode}: {inside} {edge} {beyond}")
        assert near(inside, window_rgb) and near(edge, edge_rgb), (inside, edge)
        assert beyond[3] == 0 or near(beyond, edge_rgb), beyond
        # A wait does not end quiet while the window has not caught up.
        reply = json.loads(hypr(f"-j hyprmasq wait {resized} since {shot_reply['commits']} quiet 100 deadline 600"))
        assert reply["ended"] == "timeout" and reply["resizing"], reply

        # A wait that runs when the probe answers ends once it has drawn at the new size and gone quiet. Without
        # the resize, it would have ended 100 ms after it started.
        output = f"/tmp/hm-p-resize-{mode}-wait"
        hypr_background(f"-j hyprmasq wait {resized} since {shot_reply['commits']} quiet 100 deadline 4000", output)
        time.sleep(0.5)
        machine.succeed(f"test ! -s {output}")
        machine.succeed(toggle)
        reply = background_reply(output, timeout=10)
        assert reply["ended"] == "quiet" and not reply["resizing"] and reply["redrawObserved"], reply
        assert "drew 640 480" in probe_lines(name)
        state = window_state(resized)
        assert state["size"] == {"width": 640, "height": 480} and not state["resizing"], state
        path, shot_reply = shot(resized, f"resize-{mode}-current")
        assert not shot_reply["resizing"], shot_reply
        inside, beyond, edge, _ = pixels(path, (160, 240), (480, 240), (636, 240))
        assert near(inside, window_rgb) and near(beyond, window_rgb) and near(edge, edge_rgb), (inside, beyond, edge)
        dispatch(f'hl.dsp.window.close({{ window = "address:{resized}" }})')
        wait_for(f"{title} closed", lambda: client(title) is None)

with subtest("a hidden window that waits for frame callbacks is starved until hyprmasq sends it some"):
    starving = frames_probe("hm-p-frames", 3, "hm-p-frames")
    state = window_state(starving)
    assert state["starved"] and not state["resizing"], state
    assert "\nstarved: true\n" in hypr(f"hyprmasq state {starving}")
    # A screenshot answers the frame callback it waits for, once, which is not enough: it shows the blank frame,
    # and the probe asks for the next callback.
    got = frames_got("hm-p-frames")
    path, reply = shot(starving, "frames-blank")
    assert reply["starved"] and near(pixels(path, (100, 100))[0], window_rgb), reply
    wait_for("the probe answered by the screenshot", lambda: frames_got("hm-p-frames") > got, timeout=10)
    wait_for("the probe waiting for its next frame callback", lambda: window_state(starving)["starved"], timeout=10)
    probe_log = machine.succeed("cat /tmp/hyprmasq-hm-p-frames.log")
    assert "drew content" not in probe_log, probe_log
    # Opening the socket sends none either.
    open_socket(starving)
    state = window_state(starving)
    assert state["starved"], state
    # A wait sends frame callbacks every 16 ms, so the probe draws its content, and the window is no longer starved
    # once the wait is over, although its content asked for another frame callback.
    reply = json.loads(hypr(f"-j hyprmasq wait {starving} since {state['commits']} expect 1000 quiet 100 deadline 1500"))
    assert reply["ended"] == "quiet" and reply["redrawObserved"], (reply, window_state(starving), machine.succeed("tail -n 80 /tmp/hyprmasq-hm-p-frames.log"))
    machine.succeed("grep -q '^drew content' /tmp/hyprmasq-hm-p-frames.log")
    assert not window_state(starving)["starved"]
    path, reply = shot(starving, "frames-content")
    assert not reply["starved"] and near(pixels(path, (100, 100))[0], content_rgb), reply
    dispatch(f'hl.dsp.window.close({{ window = "address:{starving}" }})')
    wait_for("hm-p-frames closed", lambda: client("hm-p-frames") is None)

with subtest("a popup of a window with an xdg geometry offset gets input where shots show it"):
    # The window has a margin of 30x20 above and left of its xdg window geometry, as client-side shadows do, and its
    # popup lies at 100,50 of that geometry. A click where the shot shows the popup reaches it at the point the shot
    # shows, and a click beside it reaches the window.
    geometry_probe = popup_probe("hm-p-geometry", 22, 100, 50, 60, 40, 30, 20)
    path, reply = shot(geometry_probe, "geometry-popups", "popups")
    box = color_box(path, popup_rgb)
    machine.log(f"popup of a window with a geometry offset: {box} in {reply['region']}")
    assert box and box[2] - box[0] == 60 and box[3] - box[1] == 40, box
    left, top = reply["region"]["x"] + box[0], reply["region"]["y"] + box[1]
    geometry_path = open_socket(geometry_probe)
    offset = log_size("p-geometry")
    send(geometry_path, "move", left + 5, top + 7, "click")
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-geometry.log | grep -q 'button popup 0'", timeout=10)
    assert "motion popup 5 7" in log_since("p-geometry", offset).splitlines(), log_since("p-geometry", offset)
    offset = log_size("p-geometry")
    send(geometry_path, "move", left + 70, top + 30, "click")
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-geometry.log | grep -q 'button hm-p-geometry 0'", timeout=10)
    events = log_since("p-geometry", offset)
    assert f"motion hm-p-geometry {left + 70} {top + 30}" in events.splitlines() and "button popup" not in events, events
    dispatch(f'hl.dsp.window.close({{ window = "address:{geometry_probe}" }})')
    wait_for("hm-p-geometry closed", lambda: client("hm-p-geometry") is None)


def screen_box(name, rgb):
    """The box of the pixels near rgb on the monitor, captured with grim."""
    return color_box(capture_output(name), rgb)


with subtest("shots taken while Hyprland animates a window's size show the window at the size input uses"):
    # While a window pops in, Hyprland draws it on screen at the animated size, fitting its content to it.
    animations(True)
    edge_rgb, white, black = (0x22, 0xCC, 0x22), (255, 255, 255), (0, 0, 0)
    # A Wayland window, which draws its right edge green. Once it has drawn at its size, the shot has that size and
    # the edge where the probe drew it, state says so and is not resizing, and a wait ends quiet, while the screen
    # still shows the window smaller, with its edge well left of the monitor's.
    focus_workspace(27)
    spawn("hyprmasq-focus-probe resize hm-p-anim hold", "p-anim")
    wait_for("hm-p-anim", lambda: client("hm-p-anim") is not None)
    anim = client("hm-p-anim")["address"]
    machine.wait_until_succeeds("grep -qx 'kept 640 480' /tmp/hyprmasq-p-anim.log", timeout=10)
    anim_path = open_socket(anim)
    path, reply = shot(anim, "anim")
    state = window_state(anim)
    waited = json.loads(hypr(f"-j hyprmasq wait {anim} since {reply['commits']} quiet 100 deadline 2000"))
    on_screen = screen_box("p-anim-screen", edge_rgb)
    machine.log(f"Wayland window popping in: its edge on screen at {on_screen}, in the shot at {color_box(path, edge_rgb)}")
    assert on_screen and on_screen[2] < 630, on_screen
    assert (reply["width"], reply["height"]) == (640, 480) and reply["region"] == region(0, 0, 640, 480) and not reply["resizing"], reply
    assert color_box(path, edge_rgb) == [632, 0, 640, 480] and near(pixels(path, (600, 240))[0], window_rgb), path
    assert state["size"] == {"width": 640, "height": 480} and not state["resizing"], state
    assert waited["ended"] == "quiet" and not waited["resizing"], waited
    # The edge in the shot is where input reaches it.
    focus_workspace(4)
    offset = log_size("p-anim")
    send(anim_path, "move", 636, 240)
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-anim.log | grep -qx 'motion hm-p-anim 636 240'", timeout=10)

    # An X11 window, which Hyprland scales into the animated size. xev draws a white window with an inner square
    # that has a black border. Its shot during the animation is the shot after it.
    focus_workspace(28)
    x_anim = xev("hm-x-anim", "x-anim")
    wait_for("xev drawn", lambda: color_box(shot(x_anim, "x-anim")[0], black) is not None, timeout=10)
    path, reply = shot(x_anim, "x-anim")
    on_screen = screen_box("p-x-anim-screen", white)
    x_square = color_box(path, black)
    machine.log(f"X11 window popping in: on screen at {on_screen}, its square in the shot at {x_square}")
    assert on_screen and on_screen[2] - on_screen[0] < 620, on_screen
    assert reply["region"] == region(0, 0, 640, 480) and not reply["resizing"] and not window_state(x_anim)["resizing"], reply
    # xev's inner window lies at 10,10, 50x50 with a border of 4.
    assert x_square == [10, 10, 68, 68], x_square
    wait_for("the animation to end", lambda: screen_box("p-x-anim-screen-end", white) == [0, 0, 640, 480], timeout=20)
    path, reply = shot(x_anim, "x-anim-end")
    assert reply["region"] == region(0, 0, 640, 480) and color_box(path, black) == x_square, (reply, color_box(path, black))
    focus_workspace(4)
    animations(False)
    for address in (anim, x_anim):
        dispatch(f'hl.dsp.window.close({{ window = "address:{address}" }})')
    wait_for("animated windows closed", lambda: all(c["address"] not in (anim, x_anim) for c in clients()))

with subtest("input reaches the popups of a moving window where shots show them"):
    # Hyprland tests for popups at the window's animated position, while shots show them at the same place in the
    # window wherever the window is on its way. The window moves from 100,100 to 300,250 in 10 seconds, and its
    # popup lies at 150,100 of it, 100x60. Counted from where the window ends its move, the point 175,130 of the
    # window lies on the popup at its animated position only from 8 seconds into the move, so a click in the first 7
    # seconds tells the two apart.
    moving = popup_probe("hm-p-moving", 29, 150, 100, 100, 60)
    moving_path = open_socket(moving)
    animations(True)
    started = time.monotonic()
    dispatch(f'hl.dsp.window.move({{ x = 300, y = 250, window = "address:{moving}" }})')
    wait_for("move", lambda: geometry(moving)[0] == [300, 250])
    path, _ = shot(moving, "moving-popups", "popups")
    offset = log_size("p-moving")
    send(moving_path, "move", 175, 130, "click")
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-p-moving.log | grep -q 'button popup 0'", timeout=10)
    elapsed = time.monotonic() - started
    events = log_since("p-moving", offset)
    machine.log(f"a click {elapsed:.1f} s into the move of a window: {events}")
    assert elapsed < 7, elapsed
    assert color_box(path, popup_rgb) == [150, 100, 250, 160], color_box(path, popup_rgb)
    assert "motion popup 25 30" in events.splitlines() and "button hm-p-moving" not in events, events
    animations(False)
    dispatch(f'hl.dsp.window.close({{ window = "address:{moving}" }})')
    wait_for("hm-p-moving closed", lambda: client("hm-p-moving") is None)
