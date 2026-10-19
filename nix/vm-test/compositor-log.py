with subtest("compositor rejected the emulated move and resize serials"):
    # Hyprland buffers its log file and only flushes it reliably on exit.
    pid = machine.succeed(f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock").strip()
    dispatch("hl.dsp.exit()")
    machine.wait_until_fails(f"test -e /proc/{pid}", timeout=60)
    hyprland_log = machine.succeed(f"cat {runtime}/hypr/{instance}/hyprland.log")
    assert "Ignoring xdg_toplevel.move with an invalid serial" in hyprland_log
    assert "Ignoring xdg_toplevel.resize with an invalid serial" in hyprland_log
