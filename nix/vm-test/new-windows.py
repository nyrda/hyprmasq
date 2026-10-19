def opened_window(title):
    """The client entry of a window that an opener probe opened, once it is mapped."""
    wait_for(title, lambda: client(title) is not None, timeout=20)
    return client(title)


def opened_since(log, start, pattern):
    """The client entry of the first window whose title matches pattern that an opener probe opened since entry start
    of its log, once it is mapped."""
    def title():
        return next((e.split(" ", 1)[1] for _, e in probe_events(log, start) if e.startswith("opened ") and re.fullmatch(pattern, e.split(" ", 1)[1])), None)
    wait_for(pattern, lambda: title() is not None, timeout=20)
    return opened_window(title())


def real_click(address, button="left"):
    """Clicks in the middle of a window through a virtual pointer, which is real input to Hyprland."""
    (x, y), (width, height) = client_at(address)
    dispatch(f"hl.dsp.cursor.move({{ x = {x + width // 2}, y = {y + height // 2} }})")
    alice(f"{client_env} wlrctl pointer click {button}")


def client_at(address):
    c = next(c for c in clients() if c["address"] == address)
    return c["at"], c["size"]


def close_titled(*titles):
    for title in titles:
        if client(title):
            dispatch(f'hl.dsp.window.close({{ window = "title:^{title}$" }})')
        wait_for(f"{title} closed", lambda: client(title) is None)


green_window = (0x22, 0xAA, 0x44)

with subtest("windows that input opens open on the workspace of the window that got it, without the focus"):
    # focus-probe's opener mode opens a toplevel on N and a dialog of the window that got the key on D, and on F
    # another process opens a dialog of it through xdg-foreign, as a portal's file chooser does, naming the parent
    # after Hyprland has created the window.
    focus_workspace(61)
    spawn("hyprmasq-focus-probe opener hm-o-agent", "o-agent")
    agent = opened_window("hm-o-agent")["address"]
    agent_size = client("hm-o-agent")["size"]
    assert agent_size == [640, 480], agent_size
    focus_workspace(4)
    wait_for("focus back", lambda: snapshot() == (4, visible))
    before_focus = snapshot()
    opener = Mcp("o")
    opener.initialize()
    actions = [{"action": "key", "keys": [k]} for k in ["f", "n", "d"]]
    both = opener.call("act", {"window": agent, "actions": actions})
    assert not both.is_error and both.data["actions"]["delivery"] == "complete", both
    new, dialog, foreign = opened_window("hm-o-agent-new1"), opened_window("hm-o-agent-dialog2"), opened_window("hm-o-agent-foreign1")
    assert new["workspace"]["id"] == dialog["workspace"]["id"] == foreign["workspace"]["id"] == 61 and dialog["floating"] and foreign["floating"], (new, dialog, foreign)
    assert foreign["pid"] != new["pid"], (new, foreign)
    assert snapshot() == before_focus
    # They float at the size they ask for, so the driven window keeps its size and its shots stay valid.
    assert new["floating"] and new["size"] == [250, 200], new
    assert client("hm-o-agent")["size"] == agent_size, client("hm-o-agent")
    for c in [new, dialog, foreign]:
        # The source names the window's stable_id too, while it lives.
        assert window_state(c["address"])["source"] == {"window": agent, "stableId": window_state(agent)["stableId"], "placed": True}, window_state(c["address"])
    # The act returns all three, with a shot of each after the one of the agent's window, in the order of the list.
    # The toplevel is blank until it gets a frame callback, which a hidden workspace does not send, so its shot
    # shows it green only when the capture fed it callbacks first, as for any starved window.
    assert "content hm-o-agent-new1" in [e for _, e in probe_events("o-agent")], probe_events("o-agent")
    entries = {w["title"]: w for w in both.data["new_windows"]}
    assert sorted(entries) == ["hm-o-agent-dialog2", "hm-o-agent-foreign1", "hm-o-agent-new1"] and len(both.images) == 4, both
    lines = both.text.splitlines()
    for entry, image in zip(both.data["new_windows"], both.images[1:]):
        c = next(c for c in [new, dialog, foreign] if c["title"] == entry["title"])
        assert (entry["window"], entry["workspace"], entry["visible"], entry["focused"], entry["placed"], entry["source"], entry["same_pid"]) == (
            c["address"], "61", False, False, True, agent, c is not foreign,
        ), entry
        shot = entry["shot"]
        assert [shot["width"], shot["height"]] == c["size"] and "may_be_stale" not in shot, (shot, c)
        relation = "" if c is foreign else ", same pid"
        assert f'{c["address"]} ({c["class"]} "{c["title"]}"{relation}) on workspace 61 (hidden)' in lines[0], both
        assert f'shot {shot["id"]} {shot["width"]}×{shot["height"]} of new window {c["address"]}' in lines[1:], both
        assert near(pixels(image, (5, 5))[0], green_window), (c, pixels(image, (5, 5)))
    assert "New window" not in both.data.get("hint", ""), both
    keep(both, "o-both")
    # The dialog's shot takes coordinates at once.
    start = len(probe_events("o-agent"))
    clicked = opener.call("act", {"window": dialog["address"], "shot": entries[dialog["title"]]["shot"]["id"], "actions": [{"action": "click", "x": 20, "y": 30}],
                                  "wait": {"expect_change": False}})
    assert not clicked.is_error and clicked.data["new_windows"] == [], clicked
    assert "motion hm-o-agent-dialog2 20 30" in [e for _, e in probe_events("o-agent", start)], probe_events("o-agent", start)
    assert snapshot() == before_focus

with subtest("a window that maps after the wait has ended is still reported by that act"):
    # L opens a toplevel 300 ms after the key, and the wait ends 100 ms after the window that got it redrew, so
    # usually before. The Go tests cover a window that maps after the wait.
    start = len(probe_events("o-agent"))
    late = opener.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["l"]}], "wait": {"quiet_ms": 100}})
    assert not late.is_error and late.data["wait"]["ended"] == "quiet", late
    events = probe_events("o-agent", start)
    pressed = next(t for t, e in events if e == "key hm-o-agent 38 1")
    mapped = next(t for t, e in events if e == "mapped hm-o-agent-late3")
    assert mapped - pressed >= 300, events
    entry = next(w for w in late.data["new_windows"] if w["title"] == "hm-o-agent-late3")
    assert entry["placed"] and entry["workspace"] == "61" and entry["shot"], late
    assert opened_window("hm-o-agent-late3")["workspace"]["id"] == 61 and snapshot() == before_focus
    close_titled("hm-o-agent-new1", "hm-o-agent-dialog2", "hm-o-agent-foreign1", "hm-o-agent-late3")

with subtest("a window that an app opens once the dialog that got the input has closed opens next to it"):
    # As Thunderbird's progress window after Send in its confirmation dialog: S in a dialog of the probe closes the
    # dialog, and 300 ms later the probe opens a toplevel, when the window that got the input is gone. No window of
    # the probe that is still open got input within the last 5 seconds.
    assert not opener.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["d"]}]}).is_error
    dialog = opened_window("hm-o-agent-dialog4")
    assert dialog["workspace"]["id"] == 61, dialog
    time.sleep(5.5)
    sent = opener.call("act", {"window": dialog["address"], "actions": [{"action": "key", "keys": ["s"]}]})
    after = opened_window("hm-o-agent-dialog4-after")
    assert client("hm-o-agent-dialog4") is None
    assert after["workspace"]["id"] == 61 and after["floating"], after
    # The dialog is gone, or about to be, and then its stable_id with it.
    after_source = window_state(after["address"])["source"]
    assert after_source.pop("stableId") in (None, dialog["stableId"]) and after_source == {"window": dialog["address"], "placed": True}, window_state(after["address"])
    assert snapshot() == before_focus
    entry = next(w for w in sent.data["new_windows"] if w["title"] == "hm-o-agent-dialog4-after")
    assert entry["placed"] and entry["source"] == dialog["address"] and entry["shot"], sent
    close_titled("hm-o-agent-dialog4-after")

agent_colors = [(0x22, 0x44, 0x66), (0x66, 0x44, 0x22)]

with subtest("a window that input opens after its act has returned comes with the next windows, screenshot or act"):
    # A middle click opens a dialog of the clicked window 1.5 s later, after the act has returned, as the portal's
    # file chooser mapped 0.7 s after the click's act in a benchmark. The session's next result reports it, once.
    late_titles = []
    latest = {"shot": opener.call("screenshot", {"window": agent}).data["shot"]["id"]}

    def middle_click():
        """Clicks so that the probe opens a dialog 1.5 s later, and returns the act's result. The click redraws the
        window in another color, so the next one takes its coordinates from this one's shot, which shows it."""
        clicked = opener.call("act", {"window": agent, "shot": latest["shot"], "actions": [{"action": "click", "x": 320, "y": 240, "button": "middle"}],
                                      "wait": {"quiet_ms": 100}})
        assert not clicked.is_error and clicked.data["actions"]["delivery"] == "complete", clicked
        latest["shot"] = clicked.data["shot"]["id"]
        return clicked

    def click_late():
        """Clicks so that the probe opens a dialog 1.5 s later, until the act has returned before it mapped."""
        for attempt in range(3):
            start = len(probe_events("o-agent"))
            clicked = middle_click()
            late = opened_since("o-agent", start, r"hm-o-agent-late-dialog\d+")
            late_titles.append(late["title"])
            mapped = next(t for t, e in probe_events("o-agent", start) if e == f"mapped {late['title']}")
            machine.log(f"hyprmasq-mcp: late dialog mapped {mapped - clicked.received * 1000:.0f} ms after the click's act returned: {clicked}")
            if mapped > clicked.received * 1000:
                break
        assert mapped > clicked.received * 1000, (mapped, clicked)
        assert late["address"] not in [w["window"] for w in clicked.data["new_windows"]] and "late_windows" not in clicked.data, clicked
        assert late["workspace"]["id"] == 61 and late["floating"] and snapshot() == before_focus, late
        return late

    def check_late(result, late, image):
        """Checks the entry of the late window in a result, and the image of its shot, and returns its shot."""
        entry = result.data["late_windows"][0]
        assert (entry["workspace"], entry["visible"], entry["focused"], entry["placed"], entry["source"], entry["same_pid"]) == ("61", False, False, True, agent, True), entry
        assert [entry["shot"]["width"], entry["shot"]["height"]] == late["size"] and near(pixels(image, (5, 5))[0], green_window), (entry, late)
        shot = entry["shot"]
        assert f'shot {shot["id"]} {shot["width"]}×{shot["height"]} of late window {late["address"]}' in result.text.splitlines(), result
        assert result.data["hint"] == f'Your input to {agent} in an earlier act opened window {late["address"]} "{late["title"]}" after that act had returned; it is in shot {shot["id"]}.', result
        return shot

    # windows lists it among the others, which come without shots, and returns its shot.
    late = click_late()
    listing = opener.call("windows")
    assert not listing.is_error and [w["window"] for w in listing.data.get("late_windows", [])] == [late["address"]] and len(listing.images) == 1, listing
    late_shot = check_late(listing, late, listing.images[0])
    entry, line = next((w, l) for w, l in zip(listing.data["windows"], listing.text.splitlines()) if w["window"] == late["address"])
    assert entry["opened_by_input"] == agent and entry["shot"] == late_shot and line.endswith(f" · opened by your input to {agent} · lacks dnd"), (entry, line)
    assert [w["window"] for w in listing.data["windows"] if "shot" in w] == [late["address"]], listing
    keep(listing, "late-windows")
    # It is reported once.
    assert "late_windows" not in opener.call("screenshot", {"window": agent}).data
    assert "opened_by_input" not in listed(opener, late["address"])[0]

    # The next screenshot reports one too.
    late = click_late()
    shown = opener.call("screenshot", {"window": agent})
    assert not shown.is_error and [w["window"] for w in shown.data.get("late_windows", [])] == [late["address"]] and len(shown.images) == 2, shown
    check_late(shown, late, shown.images[1])
    assert shown.text.splitlines()[0].endswith(f' · late window {late["address"]} ({late["class"]} "{late["title"]}", same pid) on workspace 61 (hidden)'), shown
    keep(shown, "late-screenshot")
    assert "late_windows" not in opener.call("screenshot", {"window": agent}).data
    assert "late_windows" not in opener.call("windows").data

    # The next act reports one too, here an act that only waits.
    late = click_late()
    waited = opener.call("act", {"window": agent, "actions": [], "wait": {"expect_change": False}})
    assert not waited.is_error and [w["window"] for w in waited.data.get("late_windows", [])] == [late["address"]] and waited.data["late_windows"][0]["shot"], waited
    assert f' · late window {late["address"]} ({late["class"]} "{late["title"]}", same pid) on workspace 61 (hidden)' in waited.text.splitlines()[0], waited
    assert snapshot() == before_focus
    close_titled(*late_titles)

with subtest("windows waits for a window that input opens after its act, and returns the shot of the one window it lists"):
    late_pattern = r"hm-o-agent-late-dialog\d+"

    def late_dialogs(result):
        return [w for w in result.data["windows"] if re.fullmatch(late_pattern, w["title"])]

    # Right after the click, the dialog has not mapped yet, so windows does not list it; or once it has mapped, windows
    # lists it with its shot. Then windows with wait_ms returns it, at once or once it maps.
    start = len(probe_events("o-agent"))
    middle_click()
    quick = opener.call("windows")
    if early := late_dialogs(quick):
        machine.log(f"hyprmasq-mcp: the late dialog mapped before windows listed the windows: {quick}")
        assert [w["window"] for w in quick.data["late_windows"]] == [early[0]["window"]] and early[0]["shot"] and len(quick.images) == 1, quick
    else:
        assert "late_windows" not in quick.data and not quick.images, quick
        waited = opener.call("windows", {"wait_ms": 5000})
        late = opened_since("o-agent", start, late_pattern)
        assert waited.data["wait"]["ended"] == "found" and [w["window"] for w in waited.data["late_windows"]] == [late["address"]], waited
        check_late(waited, late, waited.images[0])
    late_titles = [opened_since("o-agent", start, late_pattern)["title"]]
    close_titled(*late_titles)

    # windows with wait_ms right after the click waits until the dialog maps, about 1.5 s after the click, and returns
    # it with its shot then, long before its limit.
    for attempt in range(3):
        start = len(probe_events("o-agent"))
        clicked = middle_click()
        waited = opener.call("windows", {"wait_ms": 5000})
        late = opened_since("o-agent", start, late_pattern)
        late_titles.append(late["title"])
        mapped = next(t for t, e in probe_events("o-agent", start) if e == f"mapped {late['title']}")
        machine.log(f"hyprmasq-mcp: late dialog mapped {mapped - clicked.sent * 1000:.0f} ms after the click's act started, "
                    f"windows with wait_ms returned {waited.received * 1000 - mapped:.0f} ms after that: {waited}")
        if waited.data["wait"]["elapsed_ms"] > 0:
            break
        # It had mapped before windows started to wait.
        close_titled(late["title"])
    wait = waited.data["wait"]
    assert not waited.is_error and wait["ended"] == "found" and 0 < wait["elapsed_ms"] < 4000, waited
    assert [w["window"] for w in waited.data["late_windows"]] == [late["address"]] and len(waited.images) == 1, waited
    check_late(waited, late, waited.images[0])
    assert f"found after waiting {wait['elapsed_ms']} ms" in waited.text.splitlines(), waited
    assert 1400 <= mapped - clicked.sent * 1000 and mapped <= waited.received * 1000 < mapped + 2500, (clicked, mapped, waited)
    keep(waited, "late-waited")
    close_titled(*late_titles)

    # With match, it waits for a window that matches, here the next dialog, which comes with its shot as the one
    # window listed and as a late window.
    start = len(probe_events("o-agent"))
    middle_click()
    matched = opener.call("windows", {"match": "late-dialog", "wait_ms": 5000})
    late = opened_since("o-agent", start, late_pattern)
    assert matched.data["wait"]["ended"] == "found" and [w["window"] for w in matched.data["windows"]] == [late["address"]] and len(matched.images) == 1, matched
    assert matched.data["windows"][0]["shot"] == matched.data["late_windows"][0]["shot"], matched
    check_late(matched, late, matched.images[0])

    # match with one result returns the shot that screenshot would: act takes its coordinates, and the next changes
    # are compared with it.
    one = opener.call("windows", {"match": late["title"]})
    shot = one.data["windows"][0]["shot"]
    assert len(one.images) == 1 and "late_windows" not in one.data and [shot["width"], shot["height"]] == late["size"], one
    assert one.text.splitlines()[1:] == [f'shot {shot["id"]} {shot["width"]}×{shot["height"]} of window {late["address"]}'], one
    assert near(pixels(one.images[0], (5, 5))[0], green_window), one
    start = len(probe_events("o-agent"))
    clicked = opener.call("act", {"window": late["address"], "shot": shot["id"], "actions": [{"action": "click", "x": 20, "y": 30}], "wait": {"expect_change": False}})
    assert not clicked.is_error and clicked.data["new_windows"] == [] and shot["id"] in clicked.text.splitlines()[0], clicked
    assert f"motion {late['title']} 20 30" in [e for _, e in probe_events("o-agent", start)], probe_events("o-agent", start)
    assert snapshot() == before_focus
    close_titled(late["title"])

with subtest("input that closes its window after the wait has ended is delivered, and returns the window it came from"):
    # Enter in a dialog of the probe redraws it and closes it 300 ms later, after the wait has ended quiet, as the
    # portal's file chooser did in a benchmark, before the act had captured it. The act names the window that the
    # dialog came from, its parent, and returns its shot.
    for attempt in range(3):
        start = len(probe_events("o-agent"))
        opened = opener.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["d"]}]})
        dialog = opened_since("o-agent", start, r"hm-o-agent-dialog\d+")
        assert dialog["address"] in [w["window"] for w in opened.data["new_windows"]], opened
        entered = opener.call("act", {"window": dialog["address"], "actions": [{"action": "key", "keys": ["enter"]}], "wait": {"quiet_ms": 100}})
        machine.log(f"hyprmasq-mcp: Enter that closes its window after the wait: {entered}")
        wait_for("dialog closed", lambda: client(dialog["title"]) is None)
        # On a busy host the dialog can close before the wait has ended, or only once the act has returned.
        if entered.data["wait"]["ended"] == "quiet" and entered.data.get("window_closed"):
            break
    assert not entered.is_error and "error" not in entered.data and "capture_error" not in entered.data and "shot" not in entered.data, entered
    assert entered.data["wait"]["ended"] == "quiet" and entered.data["window_closed"] and entered.data["actions"] == {"done": 1, "total": 1, "delivery": "complete"}, entered
    events = probe_events("o-agent", start)
    assert f"destroyed {dialog['title']}" in [e for _, e in events], events
    agent_class = client("hm-o-agent")["class"]
    lines = entered.text.splitlines()
    assert re.fullmatch(rf'1/1 delivered · redraw observed · quiet \d+ ms · \d+ ms · window closed · back to {agent} \({re.escape(agent_class)} "hm-o-agent"\) on workspace 61 \(hidden\)', lines[0]), entered
    assert "capture failed" not in entered.text, entered
    source = entered.data["source"]
    assert (source["window"], source["workspace"], source["visible"]) == (agent, "61", False) and [source["shot"]["width"], source["shot"]["height"]] == [640, 480], entered
    assert lines[1] == f'shot {source["shot"]["id"]} 640×480 of {agent}, the window it came from' and len(entered.images) == 1, entered
    assert entered.data["hint"] == (f"The window closed. It came from window {agent}, which shot {source['shot']['id']} shows now: go on there with that shot's pixels. "
                                    "Any new windows are listed; call windows for the rest."), entered
    assert any(near(pixels(entered.images[0], (320, 240))[0], color) for color in agent_colors), pixels(entered.images[0], (320, 240))
    keep(entered, "closed-after-wait")
    # Its shot takes coordinates at once.
    assert not opener.call("act", {"window": agent, "shot": source["shot"]["id"], "actions": [{"action": "move", "x": 10, "y": 10}], "wait": {"expect_change": False}}).is_error
    assert snapshot() == before_focus

with subtest("a dialog that a click closes returns the window it came from, unless that is gone or the user's"):
    # A right click in a dialog of the probe closes it at once, as a dialog's button does. A click whose release meets
    # the close on a busy host is not delivered, and is repeated.
    for attempt in range(3):
        start = len(probe_events("o-agent"))
        opened = opener.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["d"]}]})
        dialog = opened_since("o-agent", start, r"hm-o-agent-dialog\d+")
        dialog_shot = next(w for w in opened.data["new_windows"] if w["window"] == dialog["address"])["shot"]["id"]
        clicked = opener.call("act", {"window": dialog["address"], "shot": dialog_shot, "actions": [{"action": "click", "x": 20, "y": 30, "button": "right"}]})
        machine.log(f"hyprmasq-mcp: a click that closes a dialog: {clicked}")
        wait_for("dialog closed", lambda: client(dialog["title"]) is None)
        if not clicked.is_error and "error" not in clicked.data:
            break
        # The failure has the shot of the window it came from too.
        assert clicked.data["source"]["window"] == agent and clicked.data["source"]["shot"], clicked
    assert clicked.data["wait"]["ended"] == "window_closed" and clicked.data["window_closed"] and clicked.data["source"]["window"] == agent, clicked
    lines = clicked.text.splitlines()
    assert lines[0] == f'1/1 delivered · window closed · back to {agent} ({agent_class} "hm-o-agent") on workspace 61 (hidden)', clicked
    assert lines[1] == f'shot {clicked.data["source"]["shot"]["id"]} 640×480 of {agent}, the window it came from' and len(clicked.images) == 1, clicked

    # A dialog that names a window as its parent through xdg-foreign outlives that window, whose process ends.
    focus_workspace(64)
    spawn("hyprmasq-focus-probe opener hm-o-gone", "o-gone")
    gone = opened_window("hm-o-gone")["address"]
    focus_workspace(4)
    wait_for("focus back", lambda: snapshot() == before_focus)
    opened = opener.call("act", {"window": gone, "actions": [{"action": "key", "keys": ["f"]}]})
    orphan = opened_window("hm-o-gone-foreign1")
    # Another process opens it, which can take until after the act.
    orphan_shot = next((w["shot"]["id"] for w in opened.data["new_windows"] if w["window"] == orphan["address"] and "shot" in w), None)
    orphan_shot = orphan_shot or opener.call("screenshot", {"window": orphan["address"]}).data["shot"]["id"]
    assert orphan["workspace"]["id"] == 64 and window_state(orphan["address"])["source"]["window"] == gone, (orphan, window_state(orphan["address"]))
    machine.succeed("pkill -f 'hyprmasq-focus-probe [o]pener hm-o-gone'")
    wait_for("hm-o-gone closed", lambda: client("hm-o-gone") is None)
    for attempt in range(3):
        clicked = opener.call("act", {"window": orphan["address"], "shot": orphan_shot, "actions": [{"action": "click", "x": 20, "y": 30, "button": "right"}]})
        machine.log(f"hyprmasq-mcp: a click that closes a dialog whose parent is gone: {clicked}")
        if client("hm-o-gone-foreign1") is None:
            break
        orphan_shot = opener.call("screenshot", {"window": orphan["address"]}).data["shot"]["id"]
    wait_for("orphan closed", lambda: client("hm-o-gone-foreign1") is None)
    assert clicked.data["window_closed"] and "source" not in clicked.data and not clicked.images, clicked
    assert gone not in clicked.text, clicked
    machine.execute("pkill -f 'hyprmasq-focus-probe [i]mported'")
    assert snapshot() == before_focus

with subtest("a dialog whose parent redraws 300 ms after the close returns the parent's shot with the redraw"):
    # In opener mode, A opens a dialog whose parent answers its close: it asks for a frame callback, and redraws in
    # orange 300 ms after the first one, as a hidden page lays out the file that a file chooser gave it once it can
    # draw. On its hidden workspace the parent gets that callback from act's wait for it before its shot, so the shot
    # shows the redraw only because the wait waits for one, and not only for 200 ms of quiet.
    orange = (0xCC, 0x88, 0x00)
    for attempt in range(3):
        start = len(probe_events("o-agent"))
        opener.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["a"]}]})
        dialog = opened_since("o-agent", start, r"hm-o-agent-answer\d+")
        dialog_shot = opener.call("screenshot", {"window": dialog["address"]}).data["shot"]["id"]
        assert not near(pixels(keep(opener.call("screenshot", {"window": agent}), "answer-before"), (320, 240))[0], orange)
        # The parent gets frame callbacks for a second after the key that opened the dialog, which would start its
        # answer before act waits for it.
        time.sleep(1.2)
        clicked = opener.call("act", {"window": dialog["address"], "shot": dialog_shot, "actions": [{"action": "click", "x": 20, "y": 30, "button": "right"}]})
        machine.log(f"hyprmasq-mcp: a dialog whose parent answers its close: {clicked}")
        wait_for("dialog closed", lambda: client(dialog["title"]) is None)
        if not clicked.is_error and "error" not in clicked.data:
            break
    assert clicked.data["window_closed"] and clicked.data["source"]["window"] == agent and len(clicked.images) == 1, clicked
    assert "answered hm-o-agent" in [e for _, e in probe_events("o-agent", start)], probe_events("o-agent", start)
    assert near(pixels(clicked.images[0], (320, 240))[0], orange), pixels(clicked.images[0], (320, 240))
    assert snapshot() == before_focus

with subtest("with place_new_windows off, a window that input opens opens as Hyprland places it, with the focus"):
    hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { place_new_windows = false } } })"))
    start = len(probe_events("o-agent"))
    off = opener.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["n"]}]})
    assert not off.is_error, off
    taken = opened_since("o-agent", start, r"hm-o-agent-new\d+")
    wait_for("new window focus", lambda: snapshot() == (4, taken["address"]))
    assert window_state(taken["address"])["source"] == {"window": agent, "stableId": window_state(agent)["stableId"], "placed": False, "reason": "disabled"}
    entry = next(w for w in off.data["new_windows"] if w["title"] == taken["title"])
    assert (entry["workspace"], entry["visible"], entry["focused"], entry["placed"], entry["not_placed"]) == ("4", True, True, False, "disabled"), off
    assert f'{taken["address"]} ({taken["class"]} "{taken["title"]}", same pid) on workspace 4 (visible, focused)' in off.text.splitlines()[0], off
    assert (
        f"New window {taken['address']} opened on the user's visible workspace 4 and has their keyboard focus, so what they type goes there, "
        "since the plugin's place_new_windows option is off. Tell the user."
    ) in off.data["hint"], off
    hypr("eval " + shlex.quote("hl.config({ plugin = { hyprmasq = { place_new_windows = true } } })"))
    close_titled(taken["title"])
    dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
    wait_for("focus back", lambda: snapshot() == (4, visible))
    assert opener.stop() == 0

machine.execute("pkill -f 'hyprmasq-focus-probe [o]pener hm-o-agent'")
machine.execute("pkill -f 'hyprmasq-focus-probe [i]mported'")

with subtest("a dialog of the user's own window opens as usual, also when input reached another window of its app since"):
    # As a browser with the user's window on their workspace and the agent's on a hidden one. A middle click opens a
    # dialog of the clicked window 1.5 s later; meanwhile, input reaches the hidden window. By the process alone, as
    # when Hyprland creates the window, the input would have opened it; its parent, known when it maps, says the
    # user did.
    focus_workspace(62)
    spawn("hyprmasq-focus-probe opener hm-o-user hm-o-hidden", "o-browser")
    user, hidden = opened_window("hm-o-user")["address"], opened_window("hm-o-hidden")["address"]
    dispatch(f'hl.dsp.window.move({{ window = "address:{hidden}", workspace = "63", follow = false }})')
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (62, user))
    hidden_path = open_socket(hidden)
    start = len(probe_events("o-browser"))
    real_click(user, "middle")
    send(hidden_path, "move", 5, 5)
    child = opened_window("hm-o-user-late-dialog1")
    events = probe_events("o-browser", start)
    clicked = next(t for t, e in events if e == "button hm-o-user 1")
    driven = next(t for t, e in events if e == "motion hm-o-hidden 5 5")
    mapped = next(t for t, e in events if e == "mapped hm-o-user-late-dialog1")
    assert clicked < driven < mapped, events
    wait_for("dialog focus", lambda: snapshot() == (62, child["address"]))
    assert child["workspace"]["id"] == 62 and window_state(child["address"])["source"] is None, child

with subtest("the user's typing into one window of an app does not take a window that input to another opens"):
    # K in the hidden window opens a toplevel without a parent 1.5 s later, while the user types into a window of the
    # same app on their workspace. Typing into another window opens no window, unlike a click, a shortcut, or Enter
    # that does not end a line the user typed.
    start = len(probe_events("o-browser"))
    send(hidden_path, "key", 37)
    machine.send_chars("abc", delay=0.05)
    later = opened_window("hm-o-hidden-later2")
    events = probe_events("o-browser", start)
    driven = next(t for t, e in events if e == "key hm-o-hidden 37 1")
    user_keys = [t for t, e in events if e.startswith("key hm-o-user") and e.endswith(" 1")]
    mapped = next(t for t, e in events if e == "mapped hm-o-hidden-later2")
    assert len(user_keys) == 3 and driven < min(user_keys) and max(user_keys) < mapped, events
    assert later["workspace"]["id"] == 63 and later["floating"], later
    assert window_state(later["address"])["source"] == {"window": hidden, "stableId": window_state(hidden)["stableId"], "placed": True}, window_state(later["address"])
    assert snapshot() == (62, child["address"])

with subtest("a dialog of the user's window that a click closes returns no window"):
    # The agent closes a dialog that the user's right click opened in their window, which the agent does not work in.
    # The click goes beside the dialog that floats over the middle of their window.
    start = len(probe_events("o-browser"))
    (x, y), _ = client_at(user)
    dispatch(f"hl.dsp.cursor.move({{ x = {x + 20}, y = {y + 20} }})")
    alice(f"{client_env} wlrctl pointer click right")
    theirs = opened_since("o-browser", start, r"hm-o-user-dialog\d+")
    assert theirs["workspace"]["id"] == 62 and window_state(theirs["address"])["source"] is None, theirs
    closer = Mcp("u")
    closer.initialize()
    theirs_shot = closer.call("screenshot", {"window": theirs["address"]}).data["shot"]["id"]
    clicked = closer.call("act", {"window": theirs["address"], "shot": theirs_shot, "actions": [{"action": "click", "x": 20, "y": 30, "button": "right"}]})
    machine.log(f"hyprmasq-mcp: a click that closes the user's dialog: {clicked}")
    wait_for("their dialog closed", lambda: client(theirs["title"]) is None)
    assert clicked.data["window_closed"] and "source" not in clicked.data and not clicked.images and user not in clicked.text, clicked
    assert not window_state(user)["socket"], window_state(user)
    assert closer.stop() == 0
    machine.execute("pkill -f 'hyprmasq-focus-probe [o]pener hm-o-user'")
    wait_for("browser probe closed", lambda: client("hm-o-user") is None)
    focus_workspace(4)
    wait_for("focus back", lambda: snapshot() == (4, visible))


def browser_probe(create_ms, map_ms, log):
    """focus-probe's browser mode as a browser that the user types in on workspace 66, with the agent's window on hidden
    workspace 67: a left click opens a toplevel without a parent create_ms after the press, and draws it map_ms after
    creating it. Returns the user's window, the agent's and the agent's socket."""
    machine.execute("pkill -f 'hyprmasq-focus-probe [b]rowser'")
    wait_for("earlier browser probe closed", lambda: client("hm-p-user") is None)
    focus_workspace(66)
    spawn(f"hyprmasq-focus-probe browser {create_ms} {map_ms} hm-p-user hm-p-agent", log)
    user, agent = opened_window("hm-p-user")["address"], opened_window("hm-p-agent")["address"]
    dispatch(f'hl.dsp.window.move({{ window = "address:{agent}", workspace = "67", follow = false }})')
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (66, user))
    return user, agent, open_socket(agent)


def popup_events(log, start, popup):
    """When the agent's click went down, and when the popup it opened was created and drew its first frame."""
    events = probe_events(log, start)
    clicked = next(t for t, e in events if e == "button hm-p-agent 1")
    created = next(t for t, e in events if e == f"opened {popup}")
    mapped = next(t for t, e in events if e == f"mapped {popup}")
    return events, clicked, created, mapped


def popup_created(log, start):
    """The title of the first popup created since entry start of the log, once there is one."""
    def title():
        return next((e.split(" ", 1)[1] for _, e in probe_events(log, start) if re.fullmatch(r"opened hm-p-agent-popup\d+", e)), None)
    wait_for("popup created", lambda: title() is not None, timeout=20)
    return title()


with subtest("a line break that the user types does not take a popup that the agent's click opens"):
    # As Chromium in the benchmark: the agent's click opens a popup that the app creates 60 ms later and draws 300 ms
    # after that, while the user ends a line of notes in their window of the same app, which has no text-input. The
    # probe's keys open nothing in the user's window but those of the opener mode, which "quote" avoids.
    user, agent, agent_path = browser_probe(60, 300, "p-browser")
    machine.send_chars("quote", delay=0.05)
    start = len(probe_events("p-browser"))
    send(agent_path, "move", 20, 20, "click")
    machine.send_key("ret")
    popup = opened_since("p-browser", start, r"hm-p-agent-popup\d+")
    events, clicked, created, mapped = popup_events("p-browser", start, popup["title"])
    enter = next(t for t, e in events if e == "key hm-p-user 28 1")
    machine.log(f"popup mode 60 300: click {clicked}, Enter {enter}, created {created}, mapped {mapped}")
    assert clicked < enter < mapped, events
    assert popup["workspace"]["id"] == 67 and popup["floating"], popup
    assert window_state(popup["address"])["source"] == {"window": agent, "stableId": window_state(agent)["stableId"], "placed": True}, window_state(popup["address"])
    assert snapshot() == (66, user)
    close_titled(popup["title"])

with subtest("neither a line break before the app creates a window nor other input after it takes the window"):
    # The app creates the popup 2.5 s after the click and draws it 1.5 s after that. The user ends a line before the app
    # creates it: a line break opens no window. Their shortcut after the app created it cannot have opened it.
    user, agent, agent_path = browser_probe(2500, 1500, "p-slow")
    start = len(probe_events("p-slow"))
    send(agent_path, "move", 20, 20, "click")
    machine.send_chars("quote\n", delay=0.05)
    popup = opened_since("p-slow", start, r"hm-p-agent-popup\d+")
    events, clicked, created, mapped = popup_events("p-slow", start, popup["title"])
    enter = next(t for t, e in events if e == "key hm-p-user 28 1")
    assert clicked < enter < created < mapped, events
    assert popup["workspace"]["id"] == 67 and snapshot() == (66, user), popup
    assert window_state(popup["address"])["source"]["placed"], window_state(popup["address"])
    close_titled(popup["title"])

    start = len(probe_events("p-slow"))
    send(agent_path, "move", 20, 20, "click")
    title = popup_created("p-slow", start)
    machine.send_key("ctrl-x")
    popup = opened_window(title)
    events, clicked, created, mapped = popup_events("p-slow", start, title)
    shortcut = next(t for t, e in events if e == "key hm-p-user 45 1")
    assert clicked < created < shortcut < mapped, events
    assert popup["workspace"]["id"] == 67 and snapshot() == (66, user), popup
    assert window_state(popup["address"])["source"]["placed"], window_state(popup["address"])
    close_titled(title)

    # A key that the user holds types on in the app until they let it go, so Enter less than 2 s after that is a line
    # break too, also more than 2 s after the press. QEMU presses Q, lets it go 1.5 s later, and holds back the Enter
    # another 1.5 s.
    start = len(probe_events("p-slow"))
    machine.send_monitor_command("sendkey q 1500")
    machine.send_key("ret")
    time.sleep(1)
    send(agent_path, "move", 20, 20, "click")
    popup = opened_since("p-slow", start, r"hm-p-agent-popup\d+")
    events, clicked, created, mapped = popup_events("p-slow", start, popup["title"])
    held = next(t for t, e in events if e == "key hm-p-user 16 1")
    let_go = next(t for t, e in events if e == "key hm-p-user 16 0")
    enter = next(t for t, e in events if e == "key hm-p-user 28 1")
    assert held < clicked < enter < created and enter - held > 2000 and enter - let_go < 2000, events
    assert popup["workspace"]["id"] == 67 and snapshot() == (66, user), popup
    assert window_state(popup["address"])["source"]["placed"], window_state(popup["address"])
    close_titled(popup["title"])

with subtest("the user's shortcut or click in their window of the app after the agent's click takes the window that it opens"):
    # Before the app creates the window, either could have opened it, and the newer one decides: the user's.
    def taken_by_user(start, user_input):
        title = popup_created("p-slow", start)
        popup = opened_window(title)
        assert popup["workspace"]["id"] == 66, popup
        assert window_state(popup["address"])["source"] == {
            "window": agent, "stableId": window_state(agent)["stableId"], "placed": False, "reason": "user_input",
        }, window_state(popup["address"])
        events, clicked, created, _ = popup_events("p-slow", start, title)
        at = next(t for t, e in events if e == user_input)
        assert clicked < at < created, events
        return popup

    start = len(probe_events("p-slow"))
    send(agent_path, "move", 20, 20, "click")
    machine.send_key("ctrl-x")
    popup = taken_by_user(start, "key hm-p-user 45 1")
    wait_for("popup focus", lambda: snapshot() == (66, popup["address"]))
    close_titled(popup["title"])
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (66, user))

    # With Num Lock off, the keypad's 8 moves, so the Enter after it ends no line and counts.
    start = len(probe_events("p-slow"))
    send(agent_path, "move", 20, 20, "click")
    machine.send_key("kp_8")
    machine.send_key("ret")
    popup = taken_by_user(start, "key hm-p-user 28 1")
    assert "key hm-p-user 72 1" in [e for _, e in probe_events("p-slow", start)], probe_events("p-slow", start)
    wait_for("popup focus", lambda: snapshot() == (66, popup["address"]))
    close_titled(popup["title"])
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (66, user))

    # A Y that a keybind takes types nothing in the app, so the Enter after it ends no line either.
    hypr("eval " + shlex.quote('hl.bind("Y", function() end)'))
    start = len(probe_events("p-slow"))
    send(agent_path, "move", 20, 20, "click")
    machine.send_key("y")
    machine.send_key("ret")
    popup = taken_by_user(start, "key hm-p-user 28 1")
    assert not any(e.startswith("key hm-p-user 21 ") for _, e in probe_events("p-slow", start)), probe_events("p-slow", start)
    hypr("eval " + shlex.quote('hl.unbind("Y")'))
    wait_for("popup focus", lambda: snapshot() == (66, popup["address"]))
    close_titled(popup["title"])
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (66, user))

    # The user's left click opens a popup of their own too, which maps last and takes the focus.
    start = len(probe_events("p-slow"))
    send(agent_path, "move", 20, 20, "click")
    real_click(user)
    popup = taken_by_user(start, "button hm-p-user 1")
    own = opened_since("p-slow", start, r"hm-p-user-popup\d+")
    assert own["workspace"]["id"] == 66 and window_state(own["address"])["source"]["reason"] == "user_input", window_state(own["address"])
    wait_for("own popup focus", lambda: snapshot() == (66, own["address"]))
    close_titled(popup["title"], own["title"])

with subtest("a window that the app created ahead and draws on the agent's click opens next to the agent's window"):
    # Some apps create a window ahead and draw it only on input. No input had reached the app before its first commit,
    # so the input up to its map decides.
    user, agent, agent_path = browser_probe(0, 300, "p-ahead")
    start = len(probe_events("p-ahead"))
    machine.succeed("pkill -USR2 -f 'hyprmasq-focus-probe [b]rowser'")
    wait_for("window created ahead", lambda: "opened hm-p-agent-ahead1" in [e for _, e in probe_events("p-ahead", start)])
    send(agent_path, "move", 20, 20, "click")
    ahead = opened_window("hm-p-agent-ahead1")
    events = probe_events("p-ahead", start)
    created = next(t for t, e in events if e == "opened hm-p-agent-ahead1")
    clicked = next(t for t, e in events if e == "button hm-p-agent 1")
    mapped = next(t for t, e in events if e == "mapped hm-p-agent-ahead1")
    assert created < clicked < mapped, events
    assert ahead["workspace"]["id"] == 67 and ahead["floating"] and snapshot() == (66, user), ahead
    assert window_state(ahead["address"])["source"] == {"window": agent, "stableId": window_state(agent)["stableId"], "placed": True}, window_state(ahead["address"])
    close_titled(ahead["title"])
    machine.execute("pkill -f 'hyprmasq-focus-probe [b]rowser'")
    wait_for("browser probe closed", lambda: client("hm-p-user") is None)
    focus_workspace(4)
    wait_for("focus back", lambda: snapshot() == (4, visible))

with subtest("a dialog that another process opens for a launched window opens next to it"):
    # A launch's window is one the agent works in before any input reaches it. A dialog that a process outside the
    # launch names it the parent of, as a portal does, opens floating on the launch's workspace.
    launched_probe = alice(f"{ipc} hyprmasq-launch --workspace hm-o-launch -- hyprmasq-focus-probe opener hm-o-launched").strip()
    before_focus = snapshot()
    machine.succeed("pkill -USR1 -f 'hyprmasq-focus-probe [o]pener hm-o-launched'")
    foreign = opened_window("hm-o-launched-foreign1")
    assert foreign["workspace"]["name"] == "hm-o-launch" and foreign["floating"], foreign
    assert window_state(foreign["address"])["source"] == {"window": launched_probe, "stableId": window_state(launched_probe)["stableId"], "placed": True}, window_state(foreign["address"])
    assert snapshot() == before_focus
    # A toplevel that N opens in the launch's first minute is the launch's to place, and floats there at its size, as
    # any window that input opens, so the launched window keeps its size.
    launched_size = client("hm-o-launched")["size"]
    send(open_socket(launched_probe), "key", 49)
    launched_new = opened_window("hm-o-launched-new1")
    assert launched_new["workspace"]["name"] == "hm-o-launch" and launched_new["floating"] and launched_new["size"] == [250, 200], launched_new
    assert client("hm-o-launched")["size"] == launched_size, client("hm-o-launched")
    assert window_state(launched_new["address"])["source"] == {"window": launched_probe, "stableId": window_state(launched_probe)["stableId"], "placed": True}, window_state(launched_new["address"])
    assert snapshot() == before_focus
    machine.execute("pkill -f 'hyprmasq-focus-probe [o]pener hm-o-launched'")
    machine.execute("pkill -f 'hyprmasq-focus-probe [i]mported'")
    wait_for("launched probe closed", lambda: client("hm-o-launched") is None and client("hm-o-launched-foreign1") is None and client("hm-o-launched-new1") is None)

with subtest("a window that an app opens on another Wayland connection of its process opens next to the agent's window"):
    # As Electron opens GTK's file chooser on a connection of its own, which names its parent only after the window has
    # mapped: G opens a toplevel without a parent 1.5 s later on a second connection of the probe's process. A window
    # of another process that opens meanwhile is not the agent's.
    focus_workspace(74)
    spawn("hyprmasq-focus-probe opener hm-c-user hm-c-agent", "c-electron")
    user, agent = opened_window("hm-c-user")["address"], opened_window("hm-c-agent")["address"]
    dispatch(f'hl.dsp.window.move({{ window = "address:{agent}", workspace = "75", follow = false }})')
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (74, user))
    electron = Mcp("s")
    electron.initialize()
    start = len(probe_events("c-electron"))
    pressed = electron.call("act", {"window": agent, "actions": [{"action": "key", "keys": ["g"]}], "wait": {"quiet_ms": 100}})
    assert not pressed.is_error and pressed.data["actions"]["delivery"] == "complete", pressed
    spawn("hyprmasq-focus-probe focus hm-c-stranger", "c-stranger")
    stranger = opened_window("hm-c-stranger")
    key_at = next(t for t, e in probe_events("c-electron", start) if e == "key hm-c-agent 34 1")
    assert vm_now() - key_at < 5000, key_at
    assert stranger["workspace"]["id"] == 74 and window_state(stranger["address"])["source"] is None, stranger
    wait_for("stranger focus", lambda: snapshot() == (74, stranger["address"]))
    other = opened_since("c-electron", start, r"hm-c-agent-other\d+")
    assert other["pid"] == client("hm-c-agent")["pid"] == client("hm-c-user")["pid"] != stranger["pid"], (other, stranger)
    assert other["workspace"]["id"] == 75 and other["floating"] and snapshot() == (74, stranger["address"]), other
    assert window_state(other["address"])["source"] == {"window": agent, "stableId": window_state(agent)["stableId"], "placed": True}, window_state(other["address"])
    # The act usually returns before the window maps, and then the next windows call reports it.
    reported = [w for w in pressed.data["new_windows"] if w["window"] == other["address"]]
    listing = electron.call("windows")
    assert not listing.is_error, listing
    if not reported:
        reported = [w for w in listing.data["late_windows"] if w["window"] == other["address"]]
        assert next(w for w in listing.data["windows"] if w["window"] == other["address"])["opened_by_input"] == agent, listing
    assert len(reported) == 1, (pressed, listing)
    entry = reported[0]
    assert (entry["workspace"], entry["visible"], entry["focused"], entry["placed"], entry["source"], entry["same_pid"]) == ("75", False, False, True, agent, True), entry
    assert "opened_by_input" not in next(w for w in listing.data["windows"] if w["window"] == stranger["address"]), listing
    assert electron.stop() == 0
    close_titled(other["title"], "hm-c-stranger")
    dispatch(f'hl.dsp.focus({{ window = "address:{user}" }})')
    wait_for("user window focus", lambda: snapshot() == (74, user))

    # The user's shortcut in their window of the app, on the first connection, after the agent's G and before the app
    # created the window takes it.
    agent_path = open_socket(agent)
    start = len(probe_events("c-electron"))
    send(agent_path, "key", 34)
    machine.send_key("ctrl-x")
    taken = opened_since("c-electron", start, r"hm-c-agent-other\d+")
    events = probe_events("c-electron", start)
    driven = next(t for t, e in events if e == "key hm-c-agent 34 1")
    shortcut = next(t for t, e in events if e == "key hm-c-user 45 1")
    created = next(t for t, e in events if e == f"opened {taken['title']}")
    assert driven < shortcut < created, events
    assert taken["workspace"]["id"] == 74, taken
    assert window_state(taken["address"])["source"] == {
        "window": agent, "stableId": window_state(agent)["stableId"], "placed": False, "reason": "user_input",
    }, window_state(taken["address"])
    wait_for("taken window focus", lambda: snapshot() == (74, taken["address"]))
    machine.execute("pkill -f 'hyprmasq-focus-probe [o]pener hm-c-user'")
    wait_for("electron probe closed", lambda: client("hm-c-user") is None and client(taken["title"]) is None)
    focus_workspace(4)
    wait_for("focus back", lambda: snapshot() == (4, visible))
