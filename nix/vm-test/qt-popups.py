def qpp_events(log, start):
    """The events that a Qt probe logged from entry start of its log on."""
    return [event for _, event in probe_events(log, start)]


def qpp_probe(title, env):
    """A Qt probe with the environment env on a hidden workspace of its own, while the user's foot keeps the focus."""
    focus_workspace(97)
    spawn(f"env {env} hyprmasq-qt-probe {title}", title)
    wait_for(title, lambda: client(title) is not None)
    address = client(title)["address"]
    wait_drawn(address, 500)
    dispatch(f'hl.dsp.window.move({{ window = "address:{address}", workspace = "name:{title}", follow = false }})')
    focus_workspace(89)
    dispatch(f'hl.dsp.focus({{ window = "address:{qpp_user}" }})')
    wait_for("the user's focus", lambda: snapshot() == (89, qpp_user))
    return address


# The form task's Qt questionnaire, driven on a laptop, lost the click that followed the Enter that picked an item of a
# dropdown, while act reported it delivered. With the platform theme of GTK, Qt takes Enter for a key that presses a
# button: the dropdown's list closes on Enter, and the press of the same key opens it again on the dropdown. A click
# outside the list only closes it, as it does for the user's own click. The probe's dropdown is at 20,170 200×30 and
# its check box at 20,20 160×30, above the list, which opens below the dropdown.
qpp_before = snapshot()
focus_workspace(89)
qpp_user = foot("hm-qp-user")
wait_drawn(qpp_user, 500)
qpp_agent = Mcp("qt-popups")
qpp_agent.initialize()
qpp_batch = [{"action": "click", "x": 120, "y": 185}, {"action": "key", "keys": ["down"]}, {"action": "key", "keys": ["enter"]}, {"action": "click", "x": 40, "y": 35}]

with subtest("a click after the Enter that closed a Qt dropdown's list reaches the window"):
    qpp_plain = qpp_probe("hm-qp-plain", "")
    qpp_shot = qpp_agent.call("screenshot", {"window": qpp_plain})
    qpp_acted = qpp_agent.call("act", {"window": qpp_plain, "shot": qpp_shot.data["shot"]["id"], "actions": qpp_batch})
    assert qpp_acted.data["actions"] == {"done": 4, "total": 4, "delivery": "complete"}, qpp_acted
    wait_for("the probe's choice and check", lambda: {"choice Venus", "checked 1"} <= set(qpp_events("hm-qp-plain", 0)), timeout=10)
    assert window_state(qpp_plain)["popups"] == [], window_state(qpp_plain)
    assert snapshot() == (89, qpp_user), snapshot()

with subtest("a click after the Enter that opened a Qt dropdown's list again is held back, and lands once the list is closed"):
    qpp_gtk = qpp_probe("hm-qp-gtk", "QT_QPA_PLATFORMTHEME=gtk3")
    qpp_shot = qpp_agent.call("screenshot", {"window": qpp_gtk})
    qpp_acted = qpp_agent.call("act", {"window": qpp_gtk, "shot": qpp_shot.data["shot"]["id"], "actions": qpp_batch, "capture": "window"})
    qpp_held = "the window had a popup open, such as a menu or a dropdown's list, and the press lay outside it, where it would only close the popup"
    assert qpp_acted.data["actions"] == {"done": 3, "total": 4, "delivery": "partial", "failed_index": 3, "held_back": qpp_held}, qpp_acted
    assert qpp_acted.data["summary"].startswith("3/4 delivered, actions[3] (click) and after held back: popup open · "), qpp_acted
    assert qpp_acted.data["hint"].startswith("actions[3] (click) and the actions after it were not sent: "), qpp_acted
    wait_for("the probe's choice", lambda: "choice Venus" in qpp_events("hm-qp-gtk", 0), timeout=10)
    # The list that Enter opened again is open, as the plugin reports it, and the shot shows it below the dropdown.
    qpp_popups = window_state(qpp_gtk)["popups"]
    assert len(qpp_popups) == 1 and qpp_popups[0]["grab"] and qpp_popups[0]["y"] >= 170, qpp_popups
    assert qpp_acted.data["shot"]["height"] > 480 or changed_pixels(qpp_shot.images[0], qpp_acted.images[0], (20, 200, 220, 300)) > 0, qpp_acted
    assert not any(e.startswith("checked") for e in qpp_events("hm-qp-gtk", 0)), qpp_events("hm-qp-gtk", 0)
    # Escape closes the list, and the click goes out and lands.
    qpp_acted = qpp_agent.call("act", {"window": qpp_gtk, "shot": qpp_acted.data["shot"]["id"], "actions": [{"action": "key", "keys": ["escape"]}, {"action": "click", "x": 40, "y": 35}]})
    assert qpp_acted.data["actions"] == {"done": 2, "total": 2, "delivery": "complete"}, qpp_acted
    wait_for("the probe's check", lambda: "checked 1" in qpp_events("hm-qp-gtk", 0), timeout=10)
    assert window_state(qpp_gtk)["popups"] == [], window_state(qpp_gtk)
    assert snapshot() == (89, qpp_user), snapshot()

for qpp_window in [qpp_plain, qpp_gtk]:
    dispatch(f'hl.dsp.window.close({{ window = "address:{qpp_window}" }})')
