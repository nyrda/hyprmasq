# The slider probe in Qt 6 and GTK 3 on Wayland and in Tk on X11, each alone on a hidden workspace, takes 500 presses of
# Right from one act, as an agent moves a slider to its far end. A key without modifiers goes at the pace of typing.
focus_workspace(4)
dispatch(f'hl.dsp.focus({{ window = "address:{visible}" }})')
wait_for("foot focus", lambda: snapshot() == (4, visible))
pace_agent = Mcp("pace")
pace_agent.initialize()

with subtest("presses of a key without modifiers come at typing pace, and each moves a Qt 6, GTK 3 and Tk slider one step"):
    for pace_kit, pace_env, pace_workspace in [("qt", "", 96), ("gtk", "", 97), ("tk", x_env, 98)]:
        pace_title = f"hm-k-{pace_kit}"
        focus_workspace(pace_workspace)
        spawn(f"env {pace_env} hyprmasq-slider-probe {pace_kit} {pace_title}", pace_title)
        wait_for(pace_title, lambda: client(pace_title) is not None)
        pace_window = client(pace_title)["address"]
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
        pace_acted = pace_agent.call("act", {"window": pace_window, "actions": [{"action": "key", "keys": ["right"], "repeat": 500}], "capture": "none"})
        assert pace_acted.data["actions"]["delivery"] == "complete", pace_acted
        wait_for(f"the {pace_kit} slider at 500", lambda: [e for _, e in probe_events(pace_title)][-1:] == ["slider 500"], timeout=10)
        # The values since the slider stood at 0, each once: Tk also sets its variable, without a change, as it lays out.
        pace_moves = []
        for t, e in probe_events(pace_title):
            if e.startswith("slider ") and (not pace_moves or pace_moves[-1][1] != int(e.split()[1])):
                pace_moves.append((t, int(e.split()[1])))
        pace_moves = [move for move in pace_moves if move[1] > 0]
        # One step a press: none lost, none merged with the next.
        assert [value for _, value in pace_moves] == list(range(1, 501)), pace_moves
        # 499 gaps of 10 ms and holds of 5 ms between the first press and the last.
        assert pace_moves[-1][0] - pace_moves[0][0] >= 7000, pace_moves
        assert snapshot() == (4, visible)
    machine.succeed("pkill -f '[h]yprmasq-slider-probe.py'")
    wait_for("the slider probes closed", lambda: not any(c["title"].startswith("hm-k-") for c in clients()))

assert pace_agent.stop() == 0
