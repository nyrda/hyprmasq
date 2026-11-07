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

# Arch's Hyprland, built with LTO, drops its reference to a popup after xdg_popup.grab through the pointer it held
# before the call, as a build may for a function that it knows leaves its argument alone. A hook that moved out of the
# argument released that reference a second time: Hyprland freed the popup at its grab, and the app's destroy of it, as
# the list closed, was a protocol error that ended GTK 4 and Qt 6 apps. The VM's Hyprland reads the argument again
# after the call, so a test plugin calls two hooked functions as Arch's does and says what the hook left in it.
with subtest("hooks leave the arguments that Hyprland passes them by value as they came, and the list closes"):
    qpp_call_probe = machine.succeed("cat /etc/hyprmasq-call-probe").strip()
    assert hypr(f"plugin load {qpp_call_probe}") == "ok"
    qpp_args = qpp_probe("hm-qp-args", "")
    qpp_shot = qpp_agent.call("screenshot", {"window": qpp_args})
    qpp_acted = qpp_agent.call("act", {"window": qpp_args, "shot": qpp_shot.data["shot"]["id"], "actions": qpp_batch[:1]})
    assert qpp_acted.data["actions"] == {"done": 1, "total": 1, "delivery": "complete"}, qpp_acted
    wait_for("the probe's list", lambda: len(window_state(qpp_args)["popups"]) == 1, timeout=10)
    assert window_state(qpp_args)["popups"][0]["grab"], window_state(qpp_args)
    # The act's lease holds the input hooks: the calls reach them. hyprmasq refuses the grab of a hidden driven
    # window's popup, and the keyboard focus is the user's foot already, so neither call changes anything.
    qpp_groups = {g["name"]: g for g in json.loads(hypr("-j hyprmasq hooks"))["groups"]}
    assert qpp_groups["input"]["installed"], qpp_groups
    qpp_kept = (hypr(f"hmprobe grab {qpp_args}"), hypr("hmprobe focus"))
    assert qpp_kept == ("kept", "kept"), qpp_kept
    assert snapshot() == (89, qpp_user), snapshot()
    # The list closes on Enter after the choice, and the probe goes on.
    qpp_acted = qpp_agent.call("act", {"window": qpp_args, "actions": qpp_batch[1:3]})
    assert qpp_acted.data["actions"] == {"done": 2, "total": 2, "delivery": "complete"}, qpp_acted
    wait_for("the probe's choice", lambda: "choice Venus" in qpp_events("hm-qp-args", 0), timeout=10)
    assert window_state(qpp_args)["popups"] == [], window_state(qpp_args)
    time.sleep(1)
    assert client("hm-qp-args") is not None
    assert snapshot() == (89, qpp_user), snapshot()
    assert hypr(f"plugin unload {qpp_call_probe}") == "ok"

for qpp_window in [qpp_plain, qpp_gtk, qpp_args]:
    dispatch(f'hl.dsp.window.close({{ window = "address:{qpp_window}" }})')
