def anr_dialogs(start=0):
    """The not-responding dialogs that Hyprland opened after entry start of the log of nix/hyprmasq-vm-test.nix's
    hyprland-dialog, which names the app."""
    lines = machine.succeed("cat /tmp/hyprmasq-dialogs.log 2>/dev/null || true").splitlines()[start:]
    return [line for line in lines if "--title Application Not Responding " in line]


def anr_mark():
    """The entry of the dialog log that comes next."""
    return len(machine.succeed("cat /tmp/hyprmasq-dialogs.log 2>/dev/null || true").splitlines())


def anr_dialog_open():
    return any(c["title"] == "Application Not Responding" for c in clients())


def anr_launch(title, command):
    """Starts command on the hidden workspace hm-a-TITLE, with its output in /tmp/hyprmasq-a-TITLE.log, and returns
    the address of its window."""
    command = f"exec {command} > /tmp/hyprmasq-a-{title}.log 2>&1"
    status, output = machine.execute("su - alice -c " + shlex.quote(f"{ipc} hyprmasq-launch --workspace hm-a-{title} -- sh -c {shlex.quote(command)}"))
    assert status == 0, (output, machine.succeed(f"cat /tmp/hyprmasq-a-{title}.log"))
    return output.strip()


def anr_close(address):
    machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == address)}")
    wait_for(f"{address} to close", lambda: all(c["address"] != address for c in clients()))


# Hyprland pings every app every 1.5 s, and shows its "not responding" dialog once an app missed
# misc:anr_missed_pings of them in a row, 5 by default. Here one is enough: an app that has not answered a ping by the
# next one gets the dialog. The user's window on the visible workspace draws nothing, as on an idle desktop, where no
# frame of the monitor clears what a hidden window waits for. The output is as large as a laptop's, as the
# questionnaire needs: Hyprland keeps popups on their monitor.
anr_before = snapshot()
assert hypr("eval " + shlex.quote("hl.config({ misc = { anr_missed_pings = 1 } })")) == "ok"
output_mode(1280, 800)
focus_workspace(90)
anr_user = foot("hm-a-user")
wait_drawn(anr_user, 500)

with subtest("Hyprland's not-responding dialog opens for a hidden app that stops answering pings"):
    anr_start = anr_mark()
    anr_stopped = anr_launch("stopped", "foot -T hm-a-stopped sleep infinity")
    anr_pid = next(c["pid"] for c in clients() if c["address"] == anr_stopped)
    machine.succeed(f"kill -STOP {anr_pid}")
    wait_for("the not-responding dialog", lambda: anr_dialogs(anr_start) and anr_dialog_open(), timeout=30)
    assert "An application hm-a-stopped - foot is not responding." in anr_dialogs(anr_start)[0], anr_dialogs(anr_start)
    machine.succeed(f"kill -CONT {anr_pid}")
    wait_for("the dialog to close", lambda: not anr_dialog_open(), timeout=30)
    anr_close(anr_stopped)

with subtest("a capture gives a hidden app its dmabuf buffers back"):
    anr_start = anr_mark()
    anr_probe = anr_launch("swapchain", "hyprmasq-swapchain-probe hm-a-swapchain")
    machine.wait_until_succeeds("grep -q ' ready$' /tmp/hyprmasq-a-swapchain.log", timeout=20)
    anr_agent = Mcp("anr")
    anr_agent.initialize()
    # Each act ends with a capture, which draws the probe's current buffer, and the next act's frames replace it.
    for _ in range(8):
        anr_pressed = anr_agent.call("act", {"window": anr_probe, "actions": [{"action": "key", "keys": ["space"]}], "capture": "window"})
        assert anr_pressed.data["actions"]["done"] == 1, anr_pressed
        time.sleep(1.5)
    anr_probe_log = machine.succeed("cat /tmp/hyprmasq-a-swapchain.log").splitlines()
    anr_waits = [int(line.split()[2]) for line in anr_probe_log if line.split()[1:2] == ["waited"]]
    anr_frames = sum(line.split()[1:2] == ["frame"] for line in anr_probe_log)
    assert not anr_dialogs(anr_start), anr_dialogs(anr_start)
    assert max(anr_waits, default=0) < 500, anr_probe_log
    assert anr_frames == 1 + 8 * 4, anr_probe_log
    anr_close(anr_probe)

with subtest("a hidden GTK 4 questionnaire answers every ping while driven as the benchmark's agents drive it"):
    anr_start = anr_mark()
    machine.succeed("rm -rf /tmp/hm-a-form")
    anr_form = anr_launch("form", "hyprmasq-bench-form /tmp/hm-a-form 25")
    float_window(anr_form, 0, 0, 1000, 800)
    wait_drawn(anr_form, 500)
    # From the first box of question 1, which has the focus, each round ticks it, moves the slider below it, types
    # into the field below that and selects the text, which the primary selection gets, chooses the third entry of
    # the dropdown below that in its popover, and scrolls on to the first box of the next question's group of four.
    # Each act waits a second and more after the last, as the benchmark's agents did, so frame callbacks stop between.
    anr_rounds = [(1, 6, "Northwind 42"), (5, 6, "blue-green-07"), (9, 5, "Ada Lovelace")]
    for anr_first, anr_options, anr_text in anr_rounds:
        anr_agent.call("act", {"window": anr_form, "actions": [
            {"action": "key", "keys": ["space"]},
            {"action": "key", "keys": ["tab"], "repeat": anr_options},
            {"action": "key", "keys": ["home"]},
            {"action": "key", "keys": ["right"], "repeat": 5},
            {"action": "key", "keys": ["tab"]},
            {"action": "type", "text": anr_text},
            {"action": "key", "keys": ["shift", "home"]},
            {"action": "key", "keys": ["end"]},
        ]})
        time.sleep(2)
        anr_agent.call("act", {"window": anr_form, "actions": [{"action": "key", "keys": ["tab"]}, {"action": "key", "keys": ["space"]}]})
        time.sleep(1)
        anr_agent.call("act", {"window": anr_form, "actions": [{"action": "key", "keys": ["down"], "repeat": 2}, {"action": "key", "keys": ["enter"]}]})
        anr_shot = anr_agent.call("screenshot", {"window": anr_form}).data["shot"]
        anr_agent.call("act", {"window": anr_form, "shot": anr_shot["id"], "actions": [
            {"action": "scroll", "x": anr_shot["width"] // 2, "y": anr_shot["height"] // 2, "dy": 3, "unit": "notch"},
            {"action": "key", "keys": ["tab"]},
        ], "capture": "window"})
        time.sleep(2)
    # Pings that a stall holds up come within 1.5 s of its end.
    time.sleep(3)
    anr_answers = {}
    for anr_line in machine.succeed("cat /tmp/hm-a-form/events.jsonl").splitlines():
        anr_event = json.loads(anr_line)
        anr_answers[anr_event["number"]] = anr_event["value"]
    for anr_first, anr_options, anr_text in anr_rounds:
        assert anr_answers.get(anr_first + 2) == anr_text and anr_answers.get(anr_first + 3, "Choose…") != "Choose…", anr_answers
    assert not anr_dialogs(anr_start), anr_dialogs(anr_start)
    anr_close(anr_form)

assert anr_agent.stop() == 0
anr_close(anr_user)
assert hypr("eval " + shlex.quote("hl.config({ misc = { anr_missed_pings = 5 } })")) == "ok"
output_mode(640, 480)
focus_workspace(anr_before[0])
