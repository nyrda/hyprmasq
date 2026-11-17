# The slider probe in Qt 6 and GTK 3 on Wayland and in Tk on X11, each alone on a hidden workspace, takes 500 presses of
# Right from one act, as an agent moves a slider to its far end. Repeated keys without modifiers are sent with 1 ms gaps.
# A key that is held for a time is pressed once, and the toolkit repeats it until it is let go.
pace_windows = {}


def pace_steps(title, start, value):
    """The values that a slider probe took since its log's entry start, from value on, with their times."""
    # Tk may set its variable to the same value during layout. Count
    # transitions, so a jump over even one required step still fails.
    moves = []
    for at, event in probe_events(title, start):
        if event.startswith("slider "):
            following = int(event.split()[1])
            if following != value:
                moves.append((at, following))
                value = following
    return moves


focus_workspace(4)
dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
wait_for("foot focus", lambda: snapshot() == (4, visible))
pace_agent = Mcp("pace")
pace_agent.initialize()

with subtest("repeated plain keys are sent with 1 ms gaps, and each moves a Qt 6, GTK 3 and Tk slider one step"):
    for pace_kit, pace_env, pace_workspace in [("qt", "", 96), ("gtk", "", 97), ("tk", x_env, 98)]:
        pace_title = f"hm-k-{pace_kit}"
        focus_workspace(pace_workspace)
        spawn(f"env {pace_env} hyprmasq-slider-probe {pace_kit} {pace_title}", pace_title)
        wait_for(pace_title, lambda: client(pace_title) is not None)
        pace_window = pace_windows[pace_kit] = client(pace_title)["address"]
        wait_drawn(pace_window, 500)
        wait_for(f"the {pace_kit} probe ready", lambda: "ready" in [e for _, e in probe_events(pace_title)])
        focus_workspace(4)
        dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
        wait_for("foot focus", lambda: snapshot() == (4, visible))
        if pace_kit == "qt":
            # 500 presses of a chord with modifiers take 40 ms each, 20 s, which the default deadline does not leave.
            pace_refused = pace_agent.call("act", {"window": pace_window, "actions": [{"action": "key", "keys": ["shift", "right"], "repeat": 500}]})
            refused(pace_refused, "invalid_arguments")
            assert pace_refused.data["error"]["message"].startswith("the batch takes at least 19985 ms to send, "), pace_refused
            assert pace_refused.data["hint"] == "Give a deadline_ms of at least 29000, or split the batch over several calls. Nothing was sent.", pace_refused
        for pace_key, pace_expected in [("right", list(range(1, 501))),
                                         ("left", list(range(499, -1, -1))),
                                         ("right", list(range(1, 501)))]:
            pace_start = len(probe_events(pace_title))
            pace_acted = pace_agent.call("act", {"window": pace_window,
                "actions": [{"action": "key", "keys": [pace_key], "repeat": 500}], "capture": "none"})
            assert pace_acted.data["actions"]["delivery"] == "complete", pace_acted
            wait_for(f"the {pace_kit} slider at {pace_expected[-1]}",
                     lambda: [e for _, e in probe_events(pace_title)][-1:] == [f"slider {pace_expected[-1]}"], timeout=10)
            pace_moves = pace_steps(pace_title, pace_start, 0 if pace_key == "right" else 500)
            # Every press appears as one step, including the trip back to zero.
            assert [value for _, value in pace_moves] == pace_expected, pace_moves
            assert pace_moves[-1][0] - pace_moves[0][0] >= 450, pace_moves
            assert snapshot() == (4, visible)

with subtest("a held key repeats in a Qt 6, GTK 3 and Tk slider until it is let go"):
    # Each slider is at 500. A hold presses Left once, which moves it a step, and the toolkit, or the X server for Tk,
    # repeats the key after the keyboard's delay, 600 ms, at its rate, 25 a second, until the release 1500 ms after the
    # press: about 23 more steps, the last one within the hold's time.
    for pace_kit, pace_window in pace_windows.items():
        pace_title = f"hm-k-{pace_kit}"
        pace_start = len(probe_events(pace_title))
        pace_acted = pace_agent.call("act", {"window": pace_window, "actions": [["hold", "left", 1500]], "capture": "none"})
        assert pace_acted.data["actions"]["delivery"] == "complete" and "hold_disturbed" not in pace_acted.data["actions"], pace_acted
        time.sleep(1)
        pace_moves = pace_steps(pace_title, pace_start, 500)
        assert [value for _, value in pace_moves] == list(range(499, 499 - len(pace_moves), -1)), (pace_kit, pace_moves)
        assert 10 <= len(pace_moves) <= 40, (pace_kit, pace_moves)
        assert 550 <= pace_moves[1][0] - pace_moves[0][0] <= 900, (pace_kit, pace_moves)
        assert 1100 <= pace_moves[-1][0] - pace_moves[0][0] <= 1700, (pace_kit, pace_moves)
        # Nothing repeats once the key is up.
        time.sleep(1)
        assert pace_steps(pace_title, pace_start, 500) == pace_moves, (pace_kit, pace_moves)
        # A hold of no time is one press.
        if pace_kit == "qt":
            pace_start = len(probe_events(pace_title))
            pace_value = pace_moves[-1][1]
            pace_acted = pace_agent.call("act", {"window": pace_window, "actions": [{"action": "hold", "keys": ["right"], "ms": 0}], "capture": "none"})
            assert pace_acted.data["actions"]["delivery"] == "complete", pace_acted
            time.sleep(1)
            assert [value for _, value in pace_steps(pace_title, pace_start, pace_value)] == [pace_value + 1], pace_acted
        assert snapshot() == (4, visible)
    machine.succeed("pkill -f '[h]yprmasq-slider-probe.py'")
    wait_for("the slider probes closed", lambda: not any(c["title"].startswith("hm-k-") for c in clients()))

with subtest("repeated plain keys remain distinct presses in Wayland and X11 Chromium, where a held key repeats"):
    for pace_backend in ["wayland", "x11"]:
        pace_log = f"keys-{pace_backend}"
        focus_workspace(106)
        spawn(f"env {x_env} chromium --ozone-platform={pace_backend} --user-data-dir=/tmp/hm-{pace_log} "
              "--no-first-run --no-default-browser-check --disable-gpu --enable-logging=stderr "
              "--app=file:///etc/hyprmasq-test/scroll.html", pace_log)
        wait_for("the browser", lambda: client("hm-x-scroll") is not None, timeout=120)
        pace_window = client("hm-x-scroll")["address"]
        wait_drawn(pace_window, 500)
        machine.wait_until_succeeds(f"grep -q '\"probe ready\"' /tmp/hyprmasq-{pace_log}.log")
        focus_workspace(4)
        dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
        for pace_trial, pace_key in enumerate(["right", "left", "right"], 1):
            pace_result = pace_agent.call("act", {"window": pace_window,
                "actions": [["key", pace_key, 500]], "capture": "none"})
            assert not pace_result.is_error and pace_result.data["actions"]["delivery"] == "complete", pace_result
            time.sleep(0.25)
            pace_text = machine.succeed(f"cat /tmp/hyprmasq-{pace_log}.log")
            pace_counts = [tuple(map(int, m)) for m in re.findall(r"probe keys (\d+) (\d+) (\d+)", pace_text)]
            assert pace_counts[-1:] == [(500 * pace_trial, 500 * pace_trial, 0)], pace_counts
            assert snapshot() == (4, visible), snapshot()
        # A hold is one press and one release, between which the page gets the key again as autorepeat.
        pace_result = pace_agent.call("act", {"window": pace_window, "actions": [["hold", "right", 1500]], "capture": "none"})
        assert not pace_result.is_error and pace_result.data["actions"]["delivery"] == "complete", pace_result
        time.sleep(0.5)
        pace_text = machine.succeed(f"cat /tmp/hyprmasq-{pace_log}.log")
        pace_downs, pace_ups, pace_repeats = [tuple(map(int, m)) for m in re.findall(r"probe keys (\d+) (\d+) (\d+)", pace_text)][-1]
        assert (pace_downs, pace_ups) == (1501 + pace_repeats, 1501) and 10 <= pace_repeats <= 40, (pace_downs, pace_ups, pace_repeats)
        assert snapshot() == (4, visible), snapshot()
        machine.succeed(f"kill {client('hm-x-scroll')['pid']}")
        wait_for("the browser closed", lambda: client("hm-x-scroll") is None)

assert pace_agent.stop() == 0
