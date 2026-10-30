with subtest("windows that map and unmap on a hidden workspace and the agent's input end neither idle nor DPMS, and real input does"):
    # hypridle with a timeout of a second logs its on-timeout and on-resume commands, as the user's would turn the
    # monitors off and on again. Hyprland restarts idle timers whenever it checks its idle inhibitors, as a window maps
    # or unmaps, which hypridle takes for activity; the plugin lets that through only when an inhibitor comes or goes.
    # Hyprland turns the monitors on for keys and pointer motion of its input devices, which the agent's input never
    # passes through.
    assert hypr("eval " + shlex.quote("hl.config({ misc = { key_press_enables_dpms = true, mouse_move_enables_dpms = true } })")) == "ok"
    idle_log = "/tmp/hyprmasq-idle-events.log"
    alice(
        "cat > /tmp/hyprmasq-hypridle.conf <<'EOF'\n"
        "general {\n    ignore_dbus_inhibit = true\n    ignore_systemd_inhibit = true\n}\n"
        f"listener {{\n    timeout = 1\n    on-timeout = echo timeout >> {idle_log}\n    on-resume = echo resume >> {idle_log}\n}}\n"
        "EOF"
    )
    alice(f"rm -f {idle_log}; {client_env} nohup hypridle -c /tmp/hyprmasq-hypridle.conf > /tmp/hyprmasq-hypridle.log 2>&1 &")

    def idle_events():
        return machine.succeed(f"cat {idle_log} 2>/dev/null || true").split()

    def dpms_on():
        return all(m["dpmsStatus"] for m in json.loads(hypr("-j monitors")))

    wait_for("hypridle idle", lambda: idle_events() == ["timeout"], timeout=30)
    dispatch('hl.dsp.dpms({ action = "disable" })')
    wait_for("monitors off", lambda: not dpms_on(), timeout=10)
    launched = alice(f"{ipc} hyprmasq-launch --workspace hm-idle -- foot -T hm-idle").strip()
    time.sleep(1.5)
    assert idle_events() == ["timeout"], (idle_events(), machine.succeed("cat /tmp/hyprmasq-hypridle.log"))
    idle_path = open_socket(launched)
    send(idle_path, "type", "echo agent", "key", 28, "move", 50, 50, "click")
    time.sleep(1.5)
    assert idle_events() == ["timeout"] and not dpms_on(), (idle_events(), json.loads(hypr("-j monitors")))
    dispatch(f'hl.dsp.window.close({{ window = "address:{launched}" }})')
    wait_for("hm-idle closed", lambda: client("hm-idle") is None)
    time.sleep(1.5)
    assert idle_events() == ["timeout"] and not dpms_on(), idle_events()
    # A move of a virtual pointer is real input to Hyprland.
    alice(f"{client_env} wlrctl pointer move 1 0")
    wait_for("hypridle resumed", lambda: idle_events()[:2] == ["timeout", "resume"], timeout=10)
    wait_for("monitors on", dpms_on, timeout=10)
    machine.succeed("pkill -x hypridle")
    assert hypr("eval " + shlex.quote("hl.config({ misc = { key_press_enables_dpms = false, mouse_move_enables_dpms = false } })")) == "ok"
