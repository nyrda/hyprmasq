with subtest("two hyprmasq-mcp servers take turns on one window"):
    other = Mcp("c")
    other.initialize()

    def typing_batch(letter):
        return {"window": probe, "actions": [{"action": "type", "text": letter * 60}], "wait": {"expect_change": False, "quiet_ms": 1000}}

    # Each call waits a second for quiet after its keys, and the other one's keys come only after that.
    start = len(probe_events("m-probe"))
    first = mcp.start("act", typing_batch("a"))
    second = other.start("act", typing_batch("b"))
    results = [mcp.result(first), other.result(second)]
    for result in results:
        assert not result.is_error and result.data["actions"]["delivery"] == "complete", result
    key_presses = [(t, int(e.split()[2])) for t, e in probe_input(start) if e.startswith("key ") and e.endswith(" 1")]
    assert [code for _, code in key_presses] in ([30] * 60 + [48] * 60, [48] * 60 + [30] * 60), key_presses
    # Both calls were made before the first one's keys were all typed, so the gap shows the window lock.
    assert max(result.sent for result in results) * 1000 < key_presses[59][0], (results, key_presses[59])
    assert key_presses[60][0] - key_presses[59][0] >= 1000, key_presses

    # A call that cannot get the window from the other within its deadline sends nothing.
    holding = mcp.start("act", {"window": probe, "wait": {"expect_ms": 6000}}, token="hm-m-holding")
    wait_for("the first call waits", lambda: any(p.get("message") == "waiting for the window to settle" for p in mcp.progress("hm-m-holding")), timeout=10)
    start = len(probe_events("m-probe"))
    # Input has to end 2250 ms before the deadline, so that a sender the deadline stops has reported before the
    # capture, and the call stops waiting for the lock then.
    late = other.call("act", {"window": probe, "actions": [{"action": "key", "keys": ["c"]}], "deadline_ms": 3000})
    refused(late, "deadline")
    assert re.fullmatch(
        r"waited \d+ ms for the window lock, held by another call on this window, until too little time was left before the deadline",
        late.data["error"]["message"],
    ), late
    assert late.received - late.sent < 3 and late.images, late
    held = mcp.result(holding)
    assert held.data["wait"]["ended"] == "no_redraw" and held.received > late.received, held
    assert probe_input(start) == [], probe_events("m-probe", start)
    assert other.stop() == 0

with subtest("hyprmasq-mcp ends a wait when the client cancels the call"):
    # The wait sends the hidden window frame callbacks, so it draws while the wait runs, and only then.
    drawn = window_state(busy_shell)["commits"]
    waiting = mcp.start("act", {"window": busy_shell, "wait": {"expect_ms": 29000}, "deadline_ms": 30000, "capture": "window"}, token="hm-m-cancel")
    wait_for("progress", lambda: any(p.get("message") == "waiting for the window to settle" for p in mcp.progress("hm-m-cancel")), timeout=10)
    assert all(p["total"] == 30000 and p["progress"] <= 30000 for p in mcp.progress("hm-m-cancel")), mcp.progress("hm-m-cancel")
    assert window_state(busy_shell)["commits"] > drawn
    cancelled_at = mcp.notify("notifications/cancelled", {"requestId": waiting, "reason": "hm-m test"})
    cancelled = mcp.result(waiting, timeout=10)
    assert not cancelled.is_error and cancelled.data["wait"]["ended"] == "cancelled" and cancelled.received - cancelled_at < 2, cancelled
    assert re.fullmatch(r"cancelled · shot s\d+ 640×480", cancelled.text), cancelled
    # The plugin's wait ended too: the window gets no more frame callbacks.
    time.sleep(1)
    drawn = window_state(busy_shell)["commits"]
    time.sleep(1.5)
    assert window_state(busy_shell)["commits"] == drawn
    assert snapshot() == before_focus


def progressed(server, token, message):
    return lambda: any(p.get("message") == message for p in server.progress(token))


with subtest("hyprmasq-mcp stops a batch whose window is resized while it is sent"):
    shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
    start = len(probe_events("m-probe"))
    actions = [{"action": "click", "x": 20, "y": 20}, {"action": "pause", "ms": 4000}, {"action": "click", "x": 40, "y": 40}]
    resizing = mcp.start("act", {"window": probe, "shot": shot_id, "actions": actions}, token="hm-m-resized")
    wait_for("sending", progressed(mcp, "hm-m-resized", "sending input"), timeout=10)
    float_window(probe, 100, 100, 320, 220)
    resized = mcp.result(resizing)
    failure(resized, "stale_shot")
    assert resized.data["error"]["message"] == "the window was resized from 300×200 to 320×220 during the batch, so the batch stopped", resized
    # The first click was sent; the pause stopped, and the second click never went out.
    assert resized.data["actions"] == {"done": 1, "total": 3, "delivery": "partial", "failed_index": 1}, resized
    assert resized.data["shot"]["width"] == 320 and resized.data["hint"].endswith("Look at the shot before repeating anything."), resized
    time.sleep(0.5)
    assert [e for _, e in probe_input(start)] == ["motion hm-m-probe 20 20", "button hm-m-probe 1", "button hm-m-probe 0"], probe_events("m-probe", start)
    float_window(probe, 100, 100, 300, 200)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp sends input to windows of different apps at the same time"):
    start = len(probe_events("m-probe"))
    # A long text into the shell takes about 9 s. The probe, another process, gets its keys meanwhile.
    long_line = "# " + "x" * 600
    typing = mcp.start("act", {"window": shell, "actions": [{"action": "type", "text": long_line}, {"action": "key", "keys": ["ctrl+u"]}]}, token="hm-m-long")
    wait_for("typing", progressed(mcp, "hm-m-long", "sending input"), timeout=10)
    beside = mcp.call("act", {"window": probe, "actions": [{"action": "type", "text": "a" * 20}], "wait": {"expect_change": False}, "capture": "none"})
    assert not beside.is_error and beside.data["actions"]["delivery"] == "complete", beside
    assert not any(p.get("message") == "waiting for input to other windows of the same app" for p in mcp.progress("hm-m-long")), mcp.progress("hm-m-long")
    typed = mcp.result(typing)
    assert not typed.is_error and typed.data["actions"]["delivery"] == "complete", typed
    # The probe's keys all came while the shell's text was still being typed.
    assert beside.received < typed.received, (beside, typed)
    key_presses = [e for _, e in probe_input(start) if e.startswith("key ") and e.endswith(" 1")]
    assert key_presses == ["key hm-m-probe 30 1"] * 20, probe_events("m-probe", start)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp checks a window again after waiting for input to another window of its app"):
    # Two toplevels of one probe share a Wayland connection, so input to them takes turns.
    focus_workspace(50)
    spawn("hyprmasq-focus-probe focus hm-m-pair-a hm-m-pair-b", "m-pair")
    wait_for("pair", lambda: client("hm-m-pair-a") is not None and client("hm-m-pair-b") is not None)
    focus_workspace(4)
    pair_a, pair_b = client("hm-m-pair-a")["address"], client("hm-m-pair-b")["address"]
    float_window(pair_b, 100, 100, 300, 200)
    wait_for("pair-b floats", lambda: client("hm-m-pair-b")["size"] == [300, 200])
    shot_id = mcp.call("screenshot", {"window": pair_b}).data["shot"]["id"]
    start = len(probe_events("m-pair"))
    # A long text into one toplevel holds its app's send lock, while a click on the other waits for it.
    typing = mcp.start("act", {"window": pair_a, "actions": [{"action": "type", "text": "x" * 400}], "wait": {"expect_change": False}}, token="hm-m-pair-long")
    wait_for("typing", progressed(mcp, "hm-m-pair-long", "sending input"), timeout=10)
    queued = mcp.start("act", {"window": pair_b, "shot": shot_id, "actions": [{"action": "click", "x": 20, "y": 20}]}, token="hm-m-queued")
    wait_for("queued", progressed(mcp, "hm-m-queued", "waiting for input to other windows of the same app"), timeout=10)
    float_window(pair_b, 100, 100, 320, 220)
    stale = mcp.result(queued)
    typed = mcp.result(typing)
    assert not typed.is_error and typed.data["actions"]["delivery"] == "complete", typed
    refused(stale, "stale_shot")
    assert stale.data["error"]["message"] == f"the window was resized from 300×200 to 320×220 since shot {shot_id}", stale
    time.sleep(0.5)
    events = [e for _, e in probe_events("m-pair", start)]
    assert not any(e.startswith(("motion hm-m-pair-b", "button hm-m-pair-b")) for e in events), events
    assert sum(e == "key hm-m-pair-a 45 1" for e in events) == 400, events
    hypr(f"hyprmasq eis close {pair_a}")
    hypr(f"hyprmasq eis close {pair_b}")
    machine.succeed("pkill -f '[h]yprmasq-focus-probe focus hm-m-pair-a'")
    wait_for("the pair closed", lambda: client("hm-m-pair-a") is None and client("hm-m-pair-b") is None)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp captures a region after the input"):
    shot_id = mcp.call("screenshot", {"window": probe}).data["shot"]["id"]
    start = len(probe_events("m-probe"))
    quiet = {"expect_change": False}
    zoomed = mcp.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 30, "y": 40}], "wait": quiet,
                              "capture": {"shot": shot_id, "x": 20, "y": 30, "width": 100, "height": 60}})
    # The zoom of 100×60 pixels comes enlarged 4 times.
    zoom_shot = zoomed.data["shot"]
    assert not zoomed.is_error and (zoom_shot["width"], zoom_shot["height"], zoom_shot["frame"]) == (400, 240, region(20, 30, 100, 60)), zoomed
    assert re.fullmatch(r"1/1 delivered · no redraw · quiet \d+ ms · \d+ ms · shot s\d+ 400×240 · lease \d+ s", zoomed.text), zoomed
    assert pixels(keep(zoomed, "act-region"))[-1] == (400, 240)
    # Its pixels are coordinates of their own. Without a capture there is no shot.
    again = mcp.call("act", {"window": probe, "shot": zoom_shot["id"], "actions": [{"action": "click", "x": 200, "y": 80}], "wait": quiet, "capture": "none"})
    assert not again.is_error and not again.images and "shot" not in again.data, again
    assert probe_motions(start) == [(30, 40), (70, 50)], probe_events("m-probe", start)
    # A region is window-local, so after a resize during the call it would show another part of the window. act
    # captures the whole window instead, and says why.
    waiting = mcp.start("act", {"window": probe, "wait": {"expect_ms": 5000}, "deadline_ms": 8000,
                                "capture": {"shot": shot_id, "x": 20, "y": 30, "width": 100, "height": 60}}, token="hm-m-zoom-resized")
    wait_for("waiting", progressed(mcp, "hm-m-zoom-resized", "waiting for the window to settle"), timeout=10)
    float_window(probe, 100, 100, 320, 220)
    resized = mcp.result(waiting)
    whole = resized.data["shot"]
    assert not resized.is_error and (whole["width"], whole["height"], whole["frame"]) == (320, 220, region(0, 0, 320, 220)), resized
    assert resized.data["hint"] == (
        f"The window was resized from 300×200 to 320×220 since shot {shot_id}, which the capture region was read from, "
        f"so shot {whole['id']} shows the whole window instead."
    ), resized
    assert pixels(keep(resized, "act-region-resized"))[-1] == (320, 220)
    float_window(probe, 100, 100, 300, 200)

# A foot window that prints each line of a FIFO through printf's %b, with its cursor hidden and no echo, so that the
# test decides what changes between two shots.
canvas_fifo = "/tmp/hm-m-canvas.fifo"
white = (0xFF, 0xFF, 0xFF)


def draw(*parts):
    """Writes escape sequences and text to the canvas."""
    line = "".join(parts).replace("\\", "\\\\").replace("\033", "\\033")
    machine.succeed(f"printf '%s\\n' {shlex.quote(line)} > {canvas_fifo}")


def cells(row, column, width=4):
    """White cells from the row and column on, counted from 1."""
    return f"\033[{row};{column}H\033[7m{' ' * width}\033[0m"


def zoom_line(shot, zoom):
    """The line of a result that lists a zoom of a changes shot."""
    return f"zoom {zoom['shot']} {zoom['image_width']}×{zoom['image_height']} of {shot['id']} at {zoom['x']},{zoom['y']} {zoom['width']}×{zoom['height']}"


with subtest("hyprmasq-mcp returns what changed since a shot, as a zoom into each changed region"):
    machine.succeed(f"rm -f {canvas_fifo} && mkfifo -m 666 {canvas_fifo}")
    script = f"stty -echo; printf '\\033[?25l'; exec 3<>{canvas_fifo}; while IFS= read -r line <&3; do printf '%b' \"$line\"; done"
    canvas_command = ["foot", "-T", "hm-m-canvas", "-o", "colors-dark.background=202020", "-o", "colors-dark.foreground=ffffff", "sh", "-c", script]
    launched = mcp.call("launch", {"command": canvas_command, "workspace": "hm-m-canvas"})
    assert not launched.is_error, launched
    canvas, base = launched.data["window"]["window"], launched.data["shot"]["id"]

    # Nothing changed since the launch's shot, the session's last of the window: no image, and for an empty batch
    # no hint. The changes shot has the pixels of the shot it compared with.
    same = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}})
    same_shot = same.data["shot"]
    assert not same.is_error and not same.images and same_shot["changes"] == {"base": base, "regions": []} and "hint" not in same.data, same
    assert (same_shot["width"], same_shot["height"], same_shot["frame"]) == (640, 480, region(0, 0, 640, 480)), same
    assert re.fullmatch(rf"(no redraw|redraw observed) · quiet \d+ ms · \d+ ms · shot {same_shot['id']} = {base}, no change", same.text), same
    # A key that changes nothing says so, and points at the shot the window still shows.
    pressed = mcp.call("act", {"window": canvas, "actions": [{"action": "key", "keys": ["a"]}]})
    pressed_id = pressed.data["shot"]["id"]
    assert not pressed.is_error and not pressed.images and pressed.data["shot"]["changes"] == {"base": same_shot["id"], "regions": []}, pressed
    assert re.search(rf"Look at {same_shot['id']} before repeating (it|them)\.$", pressed.data["hint"]), pressed

    # Four white cells are one change. Its zoom shows them with the pixel around them that the 3×3 average reaches
    # and a margin of 24 pixels, here at scale 1, and lists that box in the pixels of the shot.
    draw(cells(5, 10))
    one = mcp.call("act", {"window": canvas, "shot": pressed_id})
    one_shot = one.data["shot"]
    assert not one.is_error and one_shot["changes"]["base"] == pressed_id and len(one_shot["changes"]["regions"]) == 1 and len(one.images) == 1, one
    zoom = one_shot["changes"]["regions"][0]
    assert re.fullmatch(rf"redraw observed · quiet \d+ ms · \d+ ms · shot {one_shot['id']} = {pressed_id} \+ 1 change · lease \d+ s\n" + re.escape(zoom_line(one_shot, zoom)),
                        one.text), one
    block = color_box(mcp.call("screenshot", {"window": canvas}).images[0], white)
    assert [zoom["x"], zoom["y"], zoom["x"] + zoom["width"], zoom["y"] + zoom["height"]] == [block[0] - 25, block[1] - 25, block[2] + 25, block[3] + 25], (zoom, block)
    assert pixels(keep(one, "changes-one"))[-1] == (zoom["width"], zoom["height"]) == (zoom["image_width"], zoom["image_height"]), zoom
    assert color_box(one.images[0], white) == [block[0] - zoom["x"], block[1] - zoom["y"], block[2] - zoom["x"], block[3] - zoom["y"]], (zoom, block)

    # Changes far apart get a zoom each, top to bottom, in the order of the images.
    draw(cells(2, 3), cells(12, 30), cells(22, 55))
    several = mcp.call("act", {"window": canvas, "shot": one_shot["id"]})
    several_shot = several.data["shot"]
    regions = several_shot["changes"]["regions"]
    assert not several.is_error and len(regions) == 3 and len(several.images) == 3, several
    assert regions[0]["y"] < regions[1]["y"] < regions[2]["y"] and regions[0]["x"] < regions[1]["x"] < regions[2]["x"], several
    assert several.text.splitlines()[1:] == [zoom_line(several_shot, z) for z in regions], several
    for i, (image, z) in enumerate(zip(several.images, regions)):
        assert pixels(image)[-1] == (z["image_width"], z["image_height"]) and color_box(image, white), (i, z)
    keep(several, "changes-several")

    # A line one pixel wide, as a caret is, and a dot are tiny: they are left out, and counted.
    draw("\033[18;20H.", "\033[8;45H│")
    tiny = mcp.call("act", {"window": canvas, "shot": several_shot["id"]})
    tiny_shot = tiny.data["shot"]
    assert not tiny.is_error and not tiny.images and tiny_shot["changes"] == {"base": several_shot["id"], "regions": [], "left_out": 2}, tiny
    assert tiny.text.splitlines()[1:] == ["2 tiny changes left out"], tiny
    # A highlight 12 levels lighter than the background, as a row's hover in a dark theme, is no change.
    draw("\033[48;2;44;44;44m", *(f"\033[{row};1H{' ' * 40}" for row in range(20, 25)), "\033[0m")
    faint = mcp.call("act", {"window": canvas, "shot": tiny_shot["id"]})
    assert not faint.is_error and not faint.images and faint.data["shot"]["changes"] == {"base": tiny_shot["id"], "regions": []}, faint

    # Seven blocks, six of them in a row about 110 pixels apart, whose margins do not touch, are more than 5
    # regions: the neighbours that add the least area are joined until 5 are left, and every block is in a zoom,
    # the one far apart alone. The cell size comes from the block drawn at row 5 and column 10.
    cell_width, cell_height = (block[2] - block[0]) // 4, block[3] - block[1]
    pad_x, pad_y = block[0] - 9 * cell_width, block[1] - 4 * cell_height
    columns, rows = (640 - pad_x) // cell_width, (480 - pad_y) // cell_height

    def cell_box(row, column, width=4):
        """The box of white cells from the row and column on."""
        x, y = pad_x + (column - 1) * cell_width, pad_y + (row - 1) * cell_height
        return [x, y, x + width * cell_width, y + cell_height]

    step = -(-110 // cell_width)
    spots = [(15, 3 + step * i) for i in range(6)] + [(2, 3 + step * 5)]
    assert cell_box(*spots[-1])[2] <= 640, (cell_width, step)
    draw(*(cells(row, column) for row, column in spots))
    joined = mcp.call("act", {"window": canvas, "shot": faint.data["shot"]["id"]})
    regions = joined.data["shot"]["changes"]["regions"]
    assert not joined.is_error and len(regions) == 5 and len(joined.images) == 5, joined
    keep(joined, "changes-joined")
    inside = [[i for i, z in enumerate(regions) if z["x"] <= box[0] and z["y"] <= box[1] and box[2] <= z["x"] + z["width"] and box[3] <= z["y"] + z["height"]]
              for box in (cell_box(row, column) for row, column in spots)]
    assert all(len(found) == 1 for found in inside), (inside, regions)
    assert [found[0] for found in inside].count(inside[-1][0]) == 1, (inside, regions)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp takes coordinates from a changes shot and from its zooms"):
    # hm-m-popchange's popup, at 20,20 and 60x40, is open; SIGUSR1 closes and opens it. Its red has about the
    # luminance of the window's blue, so the color channels show the change.
    toggle = "pkill -USR1 -f 'hyprmasq-focus-probe [p]opup hm-m-popchange'"
    opened = mcp.call("screenshot", {"window": popchange})
    opened_id = opened.data["shot"]["id"]
    assert color_box(opened.images[0], popup_rgb) == [20, 20, 80, 60], opened
    offset = log_size("m-popchange")
    machine.succeed(toggle)
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'popup closed'", timeout=10)
    closed = mcp.call("act", {"window": popchange, "shot": opened_id, "wait": {"expect_ms": 5000}})
    closed_shot = closed.data["shot"]
    regions = closed_shot["changes"]["regions"]
    # The margin is cut at the window's edges.
    assert not closed.is_error and [(z["x"], z["y"], z["width"], z["height"]) for z in regions] == [(0, 0, 104, 84)], closed
    assert near(pixels(keep(closed, "changes-popup-closed"), (50, 40))[0], window_rgb)

    # The changes shot has the pixels of the shot it compared with, and its coordinates reach the window. The probe
    # answers a press on its window with a popup of its own, so the pointer only moves there.
    offset = log_size("m-popchange")
    moved = mcp.call("act", {"window": popchange, "shot": closed_shot["id"], "actions": [{"action": "move", "x": 300, "y": 200}], "wait": {"expect_change": False}})
    assert not moved.is_error and moved.data["shot"]["changes"] == {"base": closed_shot["id"], "regions": []}, moved
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'motion hm-m-popchange'", timeout=10)
    assert [line for line in log_since("m-popchange", offset).splitlines() if line.startswith("motion")] == ["motion hm-m-popchange 300 200"]

    # A zoom into a change takes coordinates in its own pixels: a click on the popup it shows reaches the popup.
    offset = log_size("m-popchange")
    machine.succeed(toggle)
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'configured popup'", timeout=10)
    reopened = mcp.call("act", {"window": popchange, "shot": moved.data["shot"]["id"], "wait": {"expect_ms": 5000}})
    regions = reopened.data["shot"]["changes"]["regions"]
    assert not reopened.is_error and len(regions) == 1, reopened
    zoom = regions[0]
    popup_box = color_box(keep(reopened, "changes-popup-opened"), popup_rgb)
    assert popup_box == [20 - zoom["x"], 20 - zoom["y"], 80 - zoom["x"], 60 - zoom["y"]], (zoom, popup_box)
    offset = log_size("m-popchange")
    clicked = mcp.call("act", {"window": popchange, "shot": zoom["shot"], "actions": [{"action": "click", "x": popup_box[0] + 7, "y": popup_box[1] + 5}],
                               "wait": {"expect_change": False}})
    assert not clicked.is_error, clicked
    machine.wait_until_succeeds(f"tail -c +{offset + 1} /tmp/hyprmasq-m-popchange.log | grep -q 'button popup 0'", timeout=10)
    assert [line for line in log_since("m-popchange", offset).splitlines() if line.startswith("motion")] == ["motion popup 7 5"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp returns a full shot in place of the changes, and says why"):
    def full_shot(result, reason):
        """Checks that the result has a full shot for the reason, and returns its id."""
        shot = result.data["shot"]
        assert not result.is_error and len(result.images) == 1 and "changes" not in shot and re.fullmatch(reason, shot["full_shot"]), result
        part = re.escape(f" · shot {shot['id']} {shot['width']}×{shot['height']} (full shot: {shot['full_shot']})")
        assert re.search(part + "( · |$)", result.text.splitlines()[0]), result
        assert pixels(result.images[0])[-1] == (shot["width"], shot["height"]), result
        return shot["id"]

    # More than 35% of the window changed.
    last = mcp.call("screenshot", {"window": canvas}).data["shot"]["id"]
    draw(*(cells(row, 1, 80) for row in range(1, 21)))
    last = full_shot(mcp.call("act", {"window": canvas, "shot": last}), rf"\d+% changed since {last}")
    # Clearing the canvas changes as much. The call waits until foot has drawn it, so its shot is the next base.
    draw("\033[2J")
    last = full_shot(mcp.call("act", {"window": canvas, "shot": last}), rf"\d+% changed since {last}")
    # More than 5 regions changed, which joined would cover more than 35% of the window: blocks about 72 pixels
    # wide and 125 tall in the corners and the middle of the top and bottom edges.
    wide, tall = round(72 / cell_width), round(125 / cell_height)
    draw(*(cells(row, column, wide) for column in (1, (columns - wide) // 2 + 1, columns - wide + 1)
           for row in [*range(1, tall + 1), *range(rows - tall + 1, rows + 1)]))
    last = full_shot(mcp.call("act", {"window": canvas, "shot": last}), f"6 changed regions since {last}")
    # The window was resized.
    float_window(canvas, 0, 0, 600, 440)
    last = full_shot(mcp.call("act", {"window": canvas, "shot": last, "wait": {"expect_change": False}}), f"resized since {last}")
    # Another session has no earlier shot to compare with, and compares later calls with the full shot it got.
    other = Mcp("g")
    other.initialize()
    unknown = full_shot(other.call("act", {"window": canvas, "actions": [{"action": "key", "keys": ["a"]}]}), "no earlier shot to compare with")
    assert other.call("act", {"window": canvas, "wait": {"expect_change": False}}).data["shot"]["changes"]["base"] == unknown
    assert other.stop() == 0
    # After 10 changes shots in a row, each compared with the one before, comes a full shot.
    for i in range(10):
        result = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}})
        assert result.data["shot"]["changes"]["base"] == last, (i, result)
        last = result.data["shot"]["id"]
    full_shot(mcp.call("act", {"window": canvas, "wait": {"expect_change": False}}), "10 changes shots in a row")

    # capture "window" and "none" are as they were.
    window = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}, "capture": "window"})
    assert set(window.data["shot"]) == {"id", "width", "height", "frame"} and len(window.images) == 1, window
    assert re.fullmatch(rf"(no redraw|redraw observed) · quiet \d+ ms · \d+ ms · shot {window.data['shot']['id']} 600×440( · lease \d+ s)?", window.text), window
    none = mcp.call("act", {"window": canvas, "wait": {"expect_change": False}, "capture": "none"})
    assert not none.images and "shot" not in none.data and " shot " not in none.text, none
    assert mcp.call("close", {"window": canvas}).data["closed"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp fits shots within --max-image-size"):
    small = Mcp("e", "--max-image-size", "150")
    small.initialize()
    fitted = small.call("screenshot", {"window": probe})
    fitted_id = fitted.data["shot"]["id"]
    assert fitted.data["shot"] == {"id": fitted_id, "width": 150, "height": 100, "frame": region(0, 0, 300, 200)}, fitted
    assert pixels(fitted.images[0])[-1] == (150, 100)
    # A shot pixel is two logical pixels.
    start = len(probe_events("m-probe"))
    clicked = small.call("act", {"window": probe, "shot": fitted_id, "actions": [{"action": "click", "x": 60, "y": 35}], "wait": {"expect_change": False}})
    assert not clicked.is_error and clicked.data["shot"]["width"] == 150, clicked
    assert probe_motions(start) == [(120, 70)], probe_events("m-probe", start)
    # Zooms are fitted too, and one that fits keeps the monitor's scale, also when it is small: enlarged twice, it
    # would not fit.
    whole = small.call("screenshot", {"window": probe, "region": {"shot": fitted_id, "x": 0, "y": 0, "width": 150, "height": 100}})
    assert (whole.data["shot"]["width"], whole.data["shot"]["height"], whole.data["shot"]["frame"]) == (150, 100, region(0, 0, 300, 200)), whole
    part = small.call("screenshot", {"window": probe, "region": {"shot": fitted_id, "x": 10, "y": 10, "width": 40, "height": 30}})
    assert (part.data["shot"]["width"], part.data["shot"]["height"], part.data["shot"]["frame"]) == (80, 60, region(20, 20, 80, 60)), part
    assert small.stop() == 0

with subtest("hyprmasq-mcp enlarges a small zoom, and takes coordinates between its captured pixels"):
    # focus-probe's shift mode draws a light button at 40,40 of 160×40, with a dark label at 60,55 of 80×10.
    command = "exec hyprmasq-focus-probe shift hm-m-zoom 0 > /tmp/hyprmasq-m-zoom.log 2>&1"
    launched = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-zoom"})
    assert not launched.is_error, launched
    window, shot = launched.data["window"]["window"], launched.data["shot"]["id"]
    # A zoom of 100×40 pixels at scale 1 comes enlarged by 4, the most, each captured pixel a block of 4×4: the label
    # at 10,15 of 80×10 in the region is at 40,60 of 320×40 in the zoom.
    asked = {"shot": shot, "x": 50, "y": 40, "width": 100, "height": 40}
    zoom = mcp.call("screenshot", {"window": window, "region": asked})
    zoom_shot = zoom.data["shot"]
    assert not zoom.is_error and (zoom_shot["width"], zoom_shot["height"], zoom_shot["frame"]) == (400, 160, region(50, 40, 100, 40)), zoom
    assert zoom.text.startswith(f"shot {zoom_shot['id']} 400×160 · zoom of {shot} at 50,40 100×40 · "), zoom
    path = keep(zoom, "zoom-enlarged")
    assert pixels(path)[-1] == (400, 160) and color_box(path, (0x22, 0x22, 0x22)) == [40, 60, 360, 100], zoom
    # A point of the zoom is a quarter of a pixel, and the stale-target check compares the window with the captured
    # pixels, which have not changed.
    start = len(probe_events("m-zoom"))
    clicked = mcp.call("act", {"window": window, "shot": zoom_shot["id"], "actions": [{"action": "click", "x": 42, "y": 62}],
                               "wait": {"expect_change": False}, "capture": asked})
    assert not clicked.is_error and clicked.data["actions"]["delivery"] == "complete", clicked
    wait_for("the click in the zoom", lambda: "button hm-m-zoom 0" in [e for _, e in probe_events("m-zoom", start)], timeout=10)
    assert "motion hm-m-zoom 60.5 55.5" in [e for _, e in probe_events("m-zoom", start)], probe_events("m-zoom", start)
    # act's capture region is enlarged alike.
    assert (clicked.data["shot"]["width"], clicked.data["shot"]["height"]) == (400, 160) and pixels(clicked.images[0])[-1] == (400, 160), clicked
    # --min-zoom-size 0 leaves zooms as captured.
    plain = Mcp("z", "--min-zoom-size", "0")
    plain.initialize()
    own = plain.call("screenshot", {"window": window}).data["shot"]["id"]
    kept = plain.call("screenshot", {"window": window, "region": {**asked, "shot": own}})
    assert (kept.data["shot"]["width"], kept.data["shot"]["height"]) == (100, 40) and pixels(kept.images[0])[-1] == (100, 40), kept
    assert plain.stop() == 0
    assert mcp.call("close", {"window": window}).data["closed"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp types through the Compose table of the window's locale"):
    # The server runs in pt_BR.UTF-8, whose Compose table has no ć on a German layout, and the windows in
    # en_US.UTF-8 and in a locale without a table, for which foot uses that of C.UTF-8, the table of en_US.UTF-8.
    hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "de" } })'))
    wait_for("German keymap", lambda: keymap("German"))
    focus_workspace(51)
    shell_command = "env LC_ALL=en_US.UTF-8 bash --noprofile --norc -i"
    en_window = foot("hm-m-compose-en", shell_command, executable="env LC_ALL=en_US.UTF-8 foot")
    xx_window = foot("hm-m-compose-xx", shell_command, executable="env LC_ALL=xx_XX.UTF-8 foot")
    wait_drawn(en_window, 1000)
    wait_drawn(xx_window, 1000)
    focus_workspace(4)
    pt_server = Mcp("f", env="LC_ALL=pt_BR.UTF-8")
    pt_server.initialize()

    def compose_act(window, name):
        line = f'printf "%s\\n" "ć" > /tmp/{name}'
        result = pt_server.call("act", {"window": window, "actions": [{"action": "type", "text": line}, {"action": "key", "keys": ["enter"]}]})
        assert not result.is_error and result.data["actions"]["delivery"] == "complete", result
        machine.wait_until_succeeds(f"test -s /tmp/{name}")
        assert machine.succeed(f"cat /tmp/{name}") == "ć\n", result
        return result.data.get("hint", "")

    assert "Compose" not in compose_act(en_window, "hm-m-compose-en"), en_window
    assert compose_act(xx_window, "hm-m-compose-xx").endswith(
        "Characters without a key of their own were typed through the Compose table of en_US.UTF-8, since there is none for the window's "
        "locale xx_XX.UTF-8. The application may compose them differently, so look at the shot to check that they arrived as typed."
    )
    assert pt_server.stop() == 0
    hypr("eval " + shlex.quote('hl.config({ input = { kb_layout = "us" } })'))
    wait_for("US keymap", lambda: keymap("English"))

with subtest("hyprmasq-mcp gives a window time to catch up with a resize, and takes no coordinates from an older frame"):
    # focus-probe's resize mode, as in the resizing subtest: from one SIGUSR1 to the next it holds back configures
    # with a new size, and draws its right edge green.
    edge_rgb = (0x22, 0xCC, 0x22)
    title = "hm-m-resize"
    toggle = f"pkill -USR1 -f 'hyprmasq-focus-probe [r]esize {title} '"
    focus_workspace(58)
    spawn(f"hyprmasq-focus-probe resize {title} hold", "m-resize")
    wait_for(title, lambda: client(title) is not None)
    catching = client(title)["address"]
    machine.wait_until_succeeds("grep -qx 'kept 640 480' /tmp/hyprmasq-m-resize.log", timeout=10)
    focus_workspace(4)
    # A neighbour launched beside it halves it, which launch names.
    launched = mcp.call("launch", {"command": ["foot", "-T", "hm-m-neighbour", "sleep", "900"], "workspace": "58"})
    assert not launched.is_error and launched.data["hint"] == (
        f"The new window resized the windows on its workspace ({catching}), so their earlier shots are stale: "
        "take a new screenshot before clicking in one."
    ) + " " + first_view_hint, launched
    neighbour = launched.data["window"]["window"]
    machine.wait_until_succeeds("grep -qx 'drew 320 480' /tmp/hyprmasq-m-resize.log", timeout=10)
    halved = mcp.call("screenshot", {"window": catching})
    assert (halved.data["shot"]["width"], halved.data["shot"]["height"]) == (320, 480) and halved.data["hint"] == first_view_hint, halved

    # Closing its neighbour on the hidden workspace gives it the whole workspace, which it holds back.
    machine.succeed(toggle)
    machine.wait_until_succeeds("grep -qx holding /tmp/hyprmasq-m-resize.log", timeout=10)
    closed = mcp.call("close", {"window": neighbour})
    assert not closed.is_error and closed.data["closed"], closed
    assert closed.data["hint"] == (
        f"Closing a tiled window resizes the tiled windows on its workspace ({catching}), so their earlier shots are stale: "
        "take a new screenshot before clicking in one."
    ), closed
    machine.wait_until_succeeds("grep -qx 'held 640 480' /tmp/hyprmasq-m-resize.log", timeout=10)
    entry, line = listed(mcp, catching)
    assert entry["resizing"] and re.search(r" · 640×480 · resizing \(not yet redrawn at its new size\)( · |$)", line), (entry, line)

    # A screenshot gives it 1.5 s to catch up, then returns the older frame and says so.
    stale = mcp.call("screenshot", {"window": catching})
    stale_shot = stale.data["shot"]
    assert stale_shot["resizing"] and (stale_shot["width"], stale_shot["height"]) == (640, 480), stale
    assert stale.received - stale.sent >= 1.4, stale
    assert stale.data["summary"] == f"shot {stale_shot['id']} 640×480 (stale: not yet redrawn at its new size) · window {catching} 640×480", stale
    assert stale.data["hint"].startswith(f"Shot {stale_shot['id']} shows an older frame"), stale
    inside, edge, _ = pixels(keep(stale, "resize-stale"), (160, 240), (316, 240))
    assert near(inside, window_rgb) and near(edge, edge_rgb), (inside, edge)

    # act takes no coordinates from it and sends nothing; the shot it returns still shows the older frame.
    start = log_size("m-resize")
    refusal = mcp.call("act", {"window": catching, "shot": stale_shot["id"], "actions": [{"action": "click", "x": 500, "y": 100}]})
    failure(refusal, "stale_shot")
    assert refusal.data["actions"]["delivery"] == "none", refusal
    assert refusal.data["error"]["message"] == (
        f"shot {stale_shot['id']} shows an older frame, since the window had not yet redrawn at its new size 640×480 when it was taken"
    ), refusal
    assert refusal.data["shot"]["resizing"] and refusal.data["hint"].startswith("Nothing was sent. Shot "), refusal

    # An act whose wait runs out before it catches up says so, and its hint is only the one about the older frame.
    timed = mcp.call("act", {"window": catching, "wait": {"expect_change": False}, "deadline_ms": 2000, "capture": "window"})
    wait, timed_shot = timed.data["wait"], timed.data["shot"]
    assert not timed.is_error and wait["ended"] == "timeout" and wait["summary"] == "resizing" and wait["resizing"], timed
    assert timed_shot["resizing"] and re.fullmatch(
        rf"timeout after \d+ ms, still resizing · shot {timed_shot['id']} 640×480 \(stale: not yet redrawn at its new size\)", timed.data["summary"]
    ), timed
    assert timed.data["hint"].startswith(f"Shot {timed_shot['id']} shows an older frame") and "drawing" not in timed.data["hint"], timed

    # A screenshot that is still waiting when the probe answers returns the frame at the new size.
    pending = mcp.start("screenshot", {"window": catching})
    time.sleep(0.3)
    answered_at = vm_now()
    machine.succeed(toggle)
    current = mcp.result(pending)
    assert not current.is_error and "resizing" not in current.data["shot"] and "hint" not in current.data, current
    assert current.received * 1000 > answered_at, (current, answered_at)
    machine.succeed("grep -qx 'drew 640 480' /tmp/hyprmasq-m-resize.log")
    inside, beyond, edge, _ = pixels(keep(current, "resize-current"), (160, 240), (480, 240), (636, 240))
    assert near(inside, window_rgb) and near(beyond, window_rgb) and near(edge, edge_rgb), (inside, beyond, edge)
    # Its coordinates reach the window.
    moved = mcp.call("act", {"window": catching, "shot": current.data["shot"]["id"], "actions": [{"action": "move", "x": 500, "y": 100}],
                             "wait": {"expect_change": False}})
    assert not moved.is_error, moved
    motions = [line for line in log_since("m-resize", start).splitlines() if line.startswith("motion ")]
    assert motions == [f"motion {title} 500 100"], log_since("m-resize", start)
    alone = mcp.call("close", {"window": catching})
    assert alone.data["closed"] and "hint" not in alone.data, alone
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp lets a starved window draw before its first shot, and says when it did not"):
    # focus-probe's frames mode, as in the starved subtest, started on hidden workspaces and never driven.
    starving = frames_probe("hm-m-frames", 3, "hm-m-frames")
    frozen = frames_probe("hm-m-frozen", 0, "hm-m-frozen")
    wait_for("both probes waiting for a frame callback", lambda: window_state(starving)["starved"] and window_state(frozen)["starved"], timeout=10)

    # The first shot sends the probe frame callbacks and waits for it to draw.
    first = mcp.call("screenshot", {"window": starving})
    assert not first.is_error and "may_be_stale" not in first.data["shot"] and first.data["hint"] == first_view_hint, first
    assert near(pixels(keep(first, "frames-first"), (100, 100))[0], content_rgb), (first, machine.succeed("tail -n 80 /tmp/hyprmasq-hm-m-frames.log"))
    assert not window_state(starving)["starved"]
    # Its socket has sent it frame callbacks, so the next shot does not wait for a frame that does not come.
    again = mcp.call("screenshot", {"window": starving})
    assert not again.is_error and "may_be_stale" not in again.data["shot"] and "hint" not in again.data, again

    # A window that does not draw is captured after a second, and its shot says that it may show an older frame.
    got = frames_got("hm-m-frozen")
    stale = mcp.call("screenshot", {"window": frozen})
    stale_shot = stale.data["shot"]
    assert not stale.is_error and stale_shot["may_be_stale"] and stale.received - stale.sent >= 1.0, stale
    assert stale.data["summary"] == f"shot {stale_shot['id']} 640×480 (may be stale: not redrawn within 1000 ms) · window {frozen} 640×480", stale
    assert stale.data["hint"].startswith(f"Shot {stale_shot['id']} may show an older frame") and "act with no actions" in stale.data["hint"], stale
    assert near(pixels(keep(stale, "frames-frozen"), (100, 100))[0], window_rgb), stale
    assert frames_got("hm-m-frozen") > got + 1
    again = mcp.call("screenshot", {"window": frozen})
    assert not again.is_error and "may_be_stale" not in again.data["shot"] and "hint" not in again.data, again

    # launch waits for its window with frame callbacks, so its shot shows the content too.
    launched = mcp.call("launch", {"command": ["hyprmasq-focus-probe", "frames", "hm-m-frames-launched", "3"], "workspace": "hm-m-frames-launched"})
    assert not launched.is_error and "may_be_stale" not in launched.data["shot"] and launched.data["hint"] == first_view_hint, launched
    assert near(pixels(keep(launched, "frames-launched"), (100, 100))[0], content_rgb), launched
    for window in [starving, frozen, launched.data["window"]["window"]]:
        assert mcp.call("close", {"window": window}).data["closed"]

with subtest("hyprmasq-mcp waits out a redraw that follows the input after the window settled"):
    # focus-probe's answer mode on hidden workspaces: a click draws it dark gray at once, and its answer, blue,
    # follows later, drawn only in a frame callback, as a page that loads or a search's results.
    pressed_rgb, answer_rgb = (0x33, 0x33, 0x33), (0x22, 0x55, 0xCC)

    def answer_probe(title, ms):
        command = f"exec hyprmasq-focus-probe answer {title} {ms} > /tmp/hyprmasq-{title}.log 2>&1"
        address = alice(f"{ipc} hyprmasq-launch --workspace {title} -- sh -c {shlex.quote(command)}").strip()
        return address, mcp.call("screenshot", {"window": address}).data["shot"]["id"]

    def click(address, shot):
        return mcp.call("act", {"window": address, "shot": shot, "actions": [{"action": "click", "x": 100, "y": 100}], "capture": "window"})

    # An answer 400 ms after the click comes after the wait has settled, within the 500 ms in which act watches for
    # new windows. act goes on waiting then, the window gets frame callbacks, and the shot shows the answer.
    soon, shot = answer_probe("hm-m-answer", 400)
    clicked = click(soon, shot)
    wait = clicked.data["wait"]
    assert not clicked.is_error and wait["ended"] == "quiet" and wait["again"]["quiet_ms"] >= 200, clicked
    assert f" · drew again, settled {wait['again']['elapsed_ms']} ms later · " in clicked.data["summary"], clicked
    assert near(pixels(keep(clicked, "answer-soon"), (100, 100))[0], answer_rgb), (clicked, machine.succeed("cat /tmp/hyprmasq-hm-m-answer.log"))
    machine.succeed("grep -q answered /tmp/hyprmasq-hm-m-answer.log")

    # One 1500 ms after it is not waited for: the shot shows the pressed button, and a wait without actions the answer.
    late, shot = answer_probe("hm-m-answer-late", 1500)
    clicked = click(late, shot)
    assert not clicked.is_error and "again" not in clicked.data["wait"] and "drew again" not in clicked.data["summary"], clicked
    assert near(pixels(keep(clicked, "answer-late"), (100, 100))[0], pressed_rgb), clicked
    time.sleep(1.5)
    waited = mcp.call("act", {"window": late, "capture": "window"})
    assert not waited.is_error and near(pixels(keep(waited, "answer-late-waited"), (100, 100))[0], answer_rgb), waited
    for window in [soon, late]:
        assert mcp.call("close", {"window": window}).data["closed"]

with subtest("hyprmasq-mcp waits for the window to answer a click before the actions after it"):
    # focus-probe's menu mode: a click draws the window dark gray at once, then the menu opens over 500 ms, drawn in
    # frame callbacks, and takes Down and Enter only once it is open, as a menu that animates open. Keys come at least
    # 300 ms after a click, too soon for it, so the batch picks the second entry only when it waits for the window
    # to be quiet after the click: on a hidden workspace, where the menu draws only while hyprmasq sends frame
    # callbacks, and on a visible one.
    pick = [{"action": "click", "x": 100, "y": 100}, {"action": "key", "keys": ["down"]}, {"action": "key", "keys": ["down"]}, {"action": "key", "keys": ["enter"]}]

    def pick_second(address, title):
        shot = mcp.call("screenshot", {"window": address}).data["shot"]["id"]
        start = len(probe_events(title))
        picked = mcp.call("act", {"window": address, "shot": shot, "actions": pick})
        assert not picked.is_error and picked.data["actions"] == {"done": 4, "total": 4, "delivery": "complete"}, picked
        events = probe_events(title, start)
        names = [e for _, e in events]
        assert "picked 2" in names and not any(e.startswith("ignored") for e in names), events
        stamps = {e: t for t, e in events}
        assert stamps[f"key {title} 108 1"] >= stamps["open"], events
        gap = stamps[f"key {title} 108 1"] - stamps[f"button {title} 0"]
        machine.log(f"a menu that opens over 500 ms: the first key {gap} ms after the click's release")
        # The bound of the wait, a second, and the round trips after it.
        assert 500 <= gap < 1300, events

    command = "exec hyprmasq-focus-probe menu hm-m-menu 500 > /tmp/hyprmasq-hm-m-menu.log 2>&1"
    hidden = alice(f"{ipc} hyprmasq-launch --workspace hm-m-menu -- sh -c {shlex.quote(command)}").strip()
    for _ in range(3):
        pick_second(hidden, "hm-m-menu")
    assert snapshot() == before_focus

    focus_workspace(65)
    spawn("hyprmasq-focus-probe menu hm-m-menu-shown 500", "hm-m-menu-shown")
    wait_for("shown menu probe", lambda: client("hm-m-menu-shown") is not None)
    shown = client("hm-m-menu-shown")["address"]
    time.sleep(0.5)
    pick_second(shown, "hm-m-menu-shown")
    focus_workspace(4)
    for window in [hidden, shown]:
        assert mcp.call("close", {"window": window}).data["closed"]

with subtest("hyprmasq-mcp sends no click at a target that moved since its shot, and one after a change elsewhere"):
    # focus-probe's shift mode draws a light button at 40,40 of 160×40, which a SIGUSR1 moves 60 pixels down 200 ms
    # later, as an upload that finished pushes a form down, and a SIGUSR2 draws a block far from it. A click at the
    # button in a shot from before the move would reach the background below it: act refuses it and sends nothing.
    command = "exec hyprmasq-focus-probe shift hm-m-shift 200 > /tmp/hyprmasq-m-shift.log 2>&1"
    shifting = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-shift"})
    assert not shifting.is_error, shifting
    window, shot = shifting.data["window"]["window"], shifting.data["shot"]["id"]
    light, background = (0xEE, 0xEE, 0xEE), (0x22, 0x44, 0x66)
    assert near(pixels(shifting.images[0], (170, 45))[0], light), shifting
    signal = "pkill -{} -f 'hyprmasq-focus-probe [s]hift hm-m-shift '"
    machine.succeed(signal.format("USR1"))
    machine.wait_until_succeeds("grep -q shifted /tmp/hyprmasq-m-shift.log", timeout=10)
    start = len(probe_events("m-shift"))
    moved = mcp.call("act", {"window": window, "shot": shot, "actions": [{"action": "click", "x": 120, "y": 60}]})
    machine.log(f"hyprmasq-mcp: a click at a button that moved: {moved}")
    refused(moved, "stale_shot")
    assert moved.data["error"]["message"] == f"the window changed since shot {shot} around actions[0] (click) at 120,60, so the input could reach something other than what the shot shows there", moved
    assert moved.data["hint"].startswith(f"The target of actions[0] (click) moved or changed since {shot}. Read the coordinates from the new shot in this result."), moved
    time.sleep(0.5)
    assert not [e for _, e in probe_events("m-shift", start) if e.startswith(("button ", "motion "))], probe_events("m-shift", start)
    # The new shot shows the button where it is now, and a click there reaches it.
    shot = moved.data["shot"]["id"]
    assert near(pixels(moved.images[0], (170, 105))[0], light) and near(pixels(moved.images[0], (170, 45))[0], background), moved
    clicked = mcp.call("act", {"window": window, "shot": shot, "actions": [{"action": "click", "x": 120, "y": 120}]})
    assert not clicked.is_error and clicked.data["actions"]["delivery"] == "complete", clicked
    machine.wait_until_succeeds("grep -q 'button hm-m-shift 0' /tmp/hyprmasq-m-shift.log", timeout=10)

    # A change far from the button does not keep a click at it from being sent, from the same shot.
    machine.succeed(signal.format("USR2"))
    machine.wait_until_succeeds("grep -q elsewhere /tmp/hyprmasq-m-shift.log", timeout=10)
    start = len(probe_events("m-shift"))
    clicked = mcp.call("act", {"window": window, "shot": shot, "actions": [{"action": "click", "x": 120, "y": 120}]})
    machine.log(f"hyprmasq-mcp: a click after a change elsewhere: {clicked}")
    assert not clicked.is_error and clicked.data["actions"]["delivery"] == "complete", clicked
    wait_for("the click after the change", lambda: "button hm-m-shift 0" in [e for _, e in probe_events("m-shift", start)], timeout=10)
    assert "motion hm-m-shift 120 120" in [e for _, e in probe_events("m-shift", start)], probe_events("m-shift", start)
    assert mcp.call("close", {"window": window}).data["closed"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp takes coordinates from shots taken while a window pops in"):
    # launch captures a window once it has gone quiet for 300 ms, well within the 10 seconds in which it pops in.
    animations(True)
    edge_rgb, black = (0x22, 0xCC, 0x22), (0, 0, 0)
    command = "exec hyprmasq-focus-probe resize hm-m-anim hold > /tmp/hyprmasq-m-anim.log 2>&1"
    popping = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-anim"})
    assert not popping.is_error and popping.received - popping.sent < 8, popping
    popping_window, popping_shot = popping.data["window"]["window"], popping.data["shot"]
    assert (popping_shot["width"], popping_shot["height"], popping_shot["frame"]) == (640, 480, region(0, 0, 640, 480)), popping
    assert "resizing" not in popping_shot and color_box(keep(popping, "anim"), edge_rgb) == [632, 0, 640, 480], popping
    # A click on the edge in the shot reaches the edge.
    clicked = mcp.call("act", {"window": popping_window, "shot": popping_shot["id"], "actions": [{"action": "click", "x": 636, "y": 240}],
                               "wait": {"expect_change": False}})
    assert not clicked.is_error, clicked
    machine.wait_until_succeeds("grep -q 'button hm-m-anim 0' /tmp/hyprmasq-m-anim.log", timeout=10)
    assert [line for line in log_since("m-anim", 0).splitlines() if line.startswith("motion ")] == ["motion hm-m-anim 636 240"]

    # An X11 window, which Hyprland scales on screen into the animated size: a click in the middle of xev's inner
    # window in the shot reaches it there.
    command = f"DISPLAY={x_display} exec stdbuf -oL xev -name hm-m-x-anim > /tmp/hyprmasq-m-x-anim.log 2>&1"
    popping_x = mcp.call("launch", {"command": ["sh", "-c", command], "workspace": "hm-m-x-anim"})
    assert not popping_x.is_error and popping_x.received - popping_x.sent < 8, popping_x
    x_window, x_shot = popping_x.data["window"]["window"], popping_x.data["shot"]
    assert (x_shot["width"], x_shot["height"], x_shot["frame"]) == (640, 480, region(0, 0, 640, 480)) and "resizing" not in x_shot, popping_x
    square = color_box(keep(popping_x, "x-anim"), black)
    # xev's inner window lies at 10,10, 50x50 with a border of 4.
    assert square == [10, 10, 68, 68], square
    middle = ((square[0] + square[2]) // 2, (square[1] + square[3]) // 2)
    clicked = mcp.call("act", {"window": x_window, "shot": x_shot["id"], "actions": [{"action": "click", "x": middle[0], "y": middle[1]}],
                               "wait": {"expect_change": False}})
    assert not clicked.is_error, clicked
    machine.wait_until_succeeds("grep -q ButtonRelease /tmp/hyprmasq-m-x-anim.log", timeout=10)
    xev_log = log_since("m-x-anim", 0)
    inner = re.search(r"inner window is (0x[0-9a-f]+)", xev_log)
    assert inner, xev_log
    press = xev_log[xev_log.index("ButtonPress event"):].split("\n\n")[0]
    assert f"subw {inner.group(1)}," in press and f"({middle[0]},{middle[1]})" in press, xev_log
    animations(False)
    for address in (popping_window, x_window):
        assert mcp.call("close", {"window": address}).data["closed"]
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp reports input that closes its own window"):
    # focus-probe closes its window on Escape, before the text after it is sent. The error names Escape as delivered,
    # unless the plugin dropped its release.
    focus_workspace(59)
    spawn("hyprmasq-focus-probe close hm-m-close", "m-close")
    wait_for("closing probe", lambda: client("hm-m-close") is not None)
    focus_workspace(4)
    closing = client("hm-m-close")["address"]
    time.sleep(0.5)
    closed = mcp.call("act", {"window": closing, "actions": [{"action": "key", "keys": ["esc"]}, {"action": "type", "text": "after"}]})
    machine.log(f"hyprmasq-mcp: input that closes its window: {closed}")
    failure(closed, "window_gone")
    assert closed.data["wait"]["ended"] == "window_closed", closed
    actions = closed.data["actions"]
    assert actions["delivery"] in ("partial", "uncertain") and actions["done"] == actions["failed_index"] <= 1, closed
    if actions["done"] == 1:
        assert closed.text.startswith(f"error window_gone: window {closing} closed after actions[0] (key) was delivered, while actions[1] (type) was sent\n"), closed
        assert "so do not send it again" in closed.data["hint"], closed
    else:
        assert closed.text.startswith(f"error window_gone: window {closing} closed while actions[0] (key) was sent\n"), closed
    assert "maybe because of it" in closed.data["hint"] and not closed.images and "shot" not in closed.data, closed
    assert client("hm-m-close") is None and "closed" in log_since("m-close", 0)
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp counts input that closes its window as delivered when only pauses follow"):
    # focus-probe closes its window as Escape or the left button goes down, while the key or the button is held, as
    # a dialog does on its button: Thunderbird's "Send Message" closed before the click's release in a benchmark.
    # A pause after it needs no window, so the call does not wait it out and is no error. A call whose release met
    # the close on a busy host reports it as not delivered, and is repeated.
    for closing_name in ("key", "click"):
        for attempt in range(3):
            closing_title = f"hm-m-closing-{closing_name}-{attempt}"
            focus_workspace(59)
            spawn(f"hyprmasq-focus-probe close {closing_title}", closing_title)
            wait_for(closing_title, lambda: client(closing_title) is not None)
            focus_workspace(4)
            closing = client(closing_title)["address"]
            time.sleep(0.5)
            closing_action = {"action": "key", "keys": ["esc"]}
            closing_arguments = {"window": closing}
            if closing_name == "click":
                closing_arguments["shot"] = mcp.call("screenshot", {"window": closing}).data["shot"]["id"]
                closing_action = {"action": "click", "x": 100, "y": 100}
            closing_arguments["actions"] = [closing_action, {"action": "pause", "ms": 3000}]
            started = time.monotonic()
            closed = mcp.call("act", closing_arguments)
            elapsed = time.monotonic() - started
            machine.log(f"hyprmasq-mcp: {closing_name} that closes its window, then a pause: {closed} in {elapsed:.2f} s")
            assert client(closing_title) is None and "closed" in log_since(closing_title, 0)
            if not closed.is_error:
                break
            failure(closed, "window_gone")
            actions = closed.data["actions"]
            assert actions["delivery"] in ("partial", "uncertain") and actions["done"] == actions["failed_index"] == 0, closed
        assert not closed.is_error and "error" not in closed.data and elapsed < 2.5, (closed, elapsed)
        assert closed.data["actions"] == {"done": 2, "total": 2, "delivery": "complete"}, closed
        assert closed.data["wait"]["ended"] == "window_closed" and closed.text.startswith("2/2 delivered · window closed"), closed
        assert not closed.images and "shot" not in closed.data, closed
        # It opened no window, so the grace for new windows changes nothing in the result.
        assert closed.data["new_windows"] == [] and closed.data["hint"] == "The window closed. Any new windows are listed; call windows for the rest.", closed
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp ends its calls and releases its leases on SIGTERM"):
    # The first server's lease on the shell stays.
    assert not mcp.call("act", {"window": shell, "actions": [{"action": "key", "keys": ["shift"]}], "wait": {"expect_change": False}}).is_error
    signalled = Mcp("d")
    signalled.initialize()
    shot_id = signalled.call("screenshot", {"window": probe}).data["shot"]["id"]
    leased = signalled.call("act", {"window": probe, "shot": shot_id, "actions": [{"action": "click", "x": 60, "y": 70}], "wait": {"expect_change": False}})
    assert leased.data["lease"]["held"] and window_state(probe)["lease"]["owner"] != window_state(shell)["lease"]["owner"], leased
    # A call is waiting when the signal comes.
    signalled.start("act", {"window": busy_shell, "wait": {"expect_ms": 29000}, "deadline_ms": 30000}, token="hm-m-term")
    wait_for("waiting", progressed(signalled, "hm-m-term", "waiting for the window to settle"), timeout=10)
    pid = signalled.command(op="pid")["pid"]
    shots = f"{instance_dir}/mcp/{pid}"
    machine.succeed(f"test -d {shots}")
    start = len(probe_events("m-probe"))
    killed_at = vm_now()
    machine.succeed(f"kill -TERM {pid}")
    stopped = signalled.command(op="stop", timeout=10)
    assert stopped["status"] == 0 and stopped["stopped"] * 1000 - killed_at < 4000, (stopped, killed_at)
    assert window_state(probe)["lease"] is None and window_state(shell)["lease"] is not None
    wait_for("leaves after SIGTERM", lambda: "pointer leave hm-m-probe" in probe_leaves(start), timeout=5)
    machine.succeed(f"test ! -e {shots}")
    # The plugin's wait ended with the call: the window gets no more frame callbacks.
    time.sleep(1)
    drawn = window_state(busy_shell)["commits"]
    time.sleep(1.5)
    assert window_state(busy_shell)["commits"] == drawn
    assert snapshot() == before_focus

with subtest("hyprmasq-mcp closes a launched window, and cleans up when it exits"):
    closed = mcp.call("close", {"window": shell})
    assert not closed.is_error and closed.data["closed"] and closed.data["new_windows"] == [], closed
    assert re.fullmatch(rf"closed {shell} after \d+ ms", closed.text) and client("hm-m-shell") is None, closed
    gone = mcp.call("act", {"window": shell, "actions": [{"action": "key", "keys": ["a"]}]})
    failure(gone, "window_gone")
    shots = f"{instance_dir}/mcp/{mcp.command(op='pid')['pid']}"
    machine.succeed(f"test -d {shots}")
    assert mcp.stop() == 0
    machine.succeed(f"test ! -e {shots}")
    for title in ["hm-m-probe", "hm-m-popleft", "hm-m-popchange", "hm-m-busy"]:
        dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
        wait_for(f"{title} closed", lambda: client(title) is None)
    assert snapshot() == before_focus
