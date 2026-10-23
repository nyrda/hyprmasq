def settle_answers(start=0):
    """The answers that the questionnaire logged from entry start of its events on, as (number, value) pairs."""
    lines = machine.succeed("cat /tmp/hm-t-form/events.jsonl 2>/dev/null || true").splitlines()[start:]
    return [(event["number"], event["value"]) for event in map(json.loads, lines)]


def settle_covers(result, x, y):
    """Whether a zoom of a changes shot shows the point x,y of the shot that the changes are measured from."""
    return any(r["x"] <= x < r["x"] + r["width"] and r["y"] <= y < r["y"] + r["height"] for r in result.data["shot"]["changes"]["regions"])


# GTK fades the overlay scrollbar of a scrolled window in over a second at the pointer's first motion, and out two
# seconds after it, and redraws the scrollbar, a strip a few pixels wide along the window's right edge, at every frame
# meanwhile. The form task's GTK 4 questionnaire scrolls its questions so, and the transitions of its check buttons,
# entry and dropdown take up to 300 ms. The area drives it as the benchmark's agents did, a click in most acts and the
# acts seconds apart, so that the scrollbar has faded out by the next act, which fades it in again. The output is as
# large as a laptop's, as the questionnaire needs: Hyprland keeps popups on their monitor.
settle_before = snapshot()
output_mode(1280, 800)
focus_workspace(93)
settle_user = foot("hm-t-user")
wait_drawn(settle_user, 500)
settle_agent = Mcp("settle")
settle_agent.initialize()

with subtest("a hidden GTK 4 questionnaire settles once its widgets have drawn, while its overlay scrollbar fades in"):
    machine.succeed("rm -rf /tmp/hm-t-form")
    settle_launched = settle_agent.call("launch", {"command": ["hyprmasq-bench-form", "/tmp/hm-t-form", "25"], "workspace": "hm-t-form"})
    assert not settle_launched.is_error, settle_launched
    settle_form, settle_shot = settle_launched.data["window"]["window"], settle_launched.data["shot"]["id"]
    assert (settle_launched.data["shot"]["width"], settle_launched.data["shot"]["height"]) == (1280, 800), settle_launched
    # Each act with what it does, its actions in the shot's pixels, which are logical pixels, the answer that the
    # questionnaire logs for it, and a point of the change that shows it. A click on the margin beside the questions
    # changes nothing but the scrollbar, and the first one gives the window the emulated focus. The dropdown's list
    # opens in a popup above it, and keys choose from it.
    settle_acts = [
        ("a click on the margin", [{"action": "click", "x": 100, "y": 400}], None, None),
        ("a click on the margin", [{"action": "click", "x": 100, "y": 400}], None, None),
        ("Amber ticked", [{"action": "click", "x": 288, "y": 180}], (1, ["Amber"]), (288, 180)),
        ("Cobalt ticked", [{"action": "click", "x": 471, "y": 180}], (1, ["Amber", "Cobalt"]), (471, 180)),
        ("the dropdown opened", [{"action": "click", "x": 328, "y": 625}], None, (330, 450)),
        ("Saturn chosen", [{"action": "key", "keys": ["down"], "repeat": 6}, {"action": "key", "keys": ["enter"]}], (4, "Saturn"), (310, 625)),
        ("text typed", [{"action": "click", "x": 412, "y": 493}, {"action": "type", "text": "Northwind 42"}], (3, "Northwind 42"), (380, 493)),
    ]
    for settle_what, settle_actions, settle_answer, settle_point in settle_acts:
        time.sleep(2.5)
        settle_start = len(settle_answers())
        settle_acted = settle_agent.call("act", {"window": settle_form, "shot": settle_shot, "actions": settle_actions})
        machine.log(f"the questionnaire after {settle_what}: {settle_acted.text}")
        if settle_acted.images:
            keep(settle_acted, "t-" + settle_what.replace(" ", "-"))
        settle_wait = settle_acted.data["wait"]
        assert settle_acted.data["actions"]["delivery"] == "complete", settle_acted
        assert settle_wait["ended"] == "quiet" and settle_wait["redraw_observed"], settle_acted
        # The scrollbar alone, which fades in for about 800 ms, kept such an act waiting for about a second, while the
        # questionnaire's widgets are done within about 300 ms, a Qt app's in a frame or two.
        if any(a["action"] == "click" for a in settle_actions):
            assert settle_wait["elapsed_ms"] <= 600, settle_acted
        # With nothing else to draw, the strip at the right edge is an animation, which the summary names.
        if settle_what == "a click on the margin":
            assert any(a["x"] + a["width"] >= 1278 and a["width"] <= 8 for a in settle_wait.get("animated", [])), settle_acted
            assert re.search(r"· quiet \d+ ms except animation at 127\d,\d+ \d×\d+ · ", settle_acted.text), settle_acted
        if settle_answer:
            wait_for(f"the questionnaire's answer after {settle_what}", lambda: settle_answer in settle_answers(settle_start), timeout=5)
        if settle_point:
            assert settle_covers(settle_acted, *settle_point), settle_acted
        settle_shot = settle_acted.data["shot"]["id"]

    assert settle_agent.call("close", {"window": settle_form}).data["closed"]
    assert snapshot() == (93, settle_user), snapshot()

assert settle_agent.stop() == 0
machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == settle_user)}")
wait_for("hm-t-user closed", lambda: client("hm-t-user") is None)
output_mode(640, 480)
focus_workspace(settle_before[0])
