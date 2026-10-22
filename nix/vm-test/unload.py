with subtest("unload removes all sockets and hooks, reload succeeds"):
    # The agent copies and selects in a hidden app, whose selections the unload cancels.
    user_copies("userclipboard", "userprimary")
    unload_probe, unload_path = clip_probe("hm-clip-unload", 87)
    focus_workspace(4)
    unload_start = len(probe_events("hm-clip-unload"))
    send(unload_path, "keys", "c", "keys", "s")
    wait_clip("hm-clip-unload", unload_start, "copy hm-clip-unload-copy1", "select hm-clip-unload-select1")
    # And it selects a word in a hidden xterm, whose X selection the unload gives back to Hyprland's window manager.
    focus_workspace(88)
    unload_x = xterm("hm-clip-unload-x", "sh -c " + shlex.quote("printf 'xunloadword\\n'; exec sleep 900"))
    focus_workspace(4)
    send(open_socket(unload_x), "move", 30, 8, "click", "left", 2)
    wait_for("the X primary selection", lambda: alice(f"{x_env} timeout 5 xclip -o -selection primary || true") == "xunloadword", timeout=10)
    time.sleep(0.5)
    assert user_selections() == ("userclipboard", "userprimary")
    paths = [s["path"] for s in json.loads(hypr("-j hyprmasq eis list"))["sockets"]]
    assert paths
    # A wait on a window that keeps drawing is still pending at the unload, and gets an answer.
    focus_workspace(20)
    hypr_background(f"-j hyprmasq wait {busy} 200 4000", "/tmp/hm-wait-unload")
    # So is one on the hidden Qt window of the suspended area, which the wait keeps from being told that it is
    # suspended, and the unload tells it again. The window redraws once it is shown, and the wait waits for quiet after.
    unload_qt = len(probe_events("s-qt"))
    hypr_background(f"-j hyprmasq wait {sus_qt} since {window_state(sus_qt)['commits']} quiet 4000 deadline 4400", "/tmp/hm-wait-unload-qt")
    wait_for("the Qt window shown", lambda: sus_configured(unload_qt)[-1:] == ["640 480 shown"], timeout=10)
    time.sleep(0.5)
    assert hypr(f"plugin unload {plugin}") == "ok"
    machine.wait_until_succeeds("test -s /tmp/hm-wait-unload", timeout=10)
    reply = json.loads(machine.succeed("cat /tmp/hm-wait-unload"))
    assert not reply["ok"] and "unloaded" in reply["error"], reply
    reply = background_reply("/tmp/hm-wait-unload-qt", timeout=10)
    assert not reply["ok"] and reply["code"] == "unloaded", reply
    wait_for("the Qt window suspended again", lambda: sus_configured(unload_qt)[-1:] == ["640 480 suspended"], timeout=10)
    wait_clip("hm-clip-unload", unload_start, "source cancelled hm-clip-unload-copy1", "source cancelled hm-clip-unload-select1")
    assert user_selections() == ("userclipboard", "userprimary")
    # The xterm's exit, after the unload, leaves the user's primary selection alone.
    dispatch(f'hl.dsp.window.close({{ window = "address:{unload_x}" }})')
    wait_for("the hidden xterm closed", lambda: client("hm-clip-unload-x") is None)
    time.sleep(1)
    assert user_selections() == ("userclipboard", "userprimary")
    # Hyprland's own handlers take the user's copy and selection in the app again.
    focus_workspace(87)
    dispatch(f'hl.dsp.focus({{ window = "address:{unload_probe}" }})')
    wait_for("the probe focused", lambda: snapshot() == (87, unload_probe))
    machine.send_chars("cs")
    wait_for("the user's copy", lambda: user_selections() == ("hm-clip-unload-copy2", "hm-clip-unload-select2"), timeout=10)
    focus_workspace(4)
    for path in paths:
        machine.succeed(f"test ! -e {path} && test ! -e {path}.lock")
    assert hypr(f"plugin load {plugin}") == "ok"
    assert json.loads(hypr("-j hyprmasq eis list")) == {"ok": True, "sockets": []}
    assert not hypr("configerrors")
    machine.succeed("pkill -f '[h]yprmasq-clip-probe' || true")
    wait_for("the clipboard probe closed", lambda: client("hm-clip-unload") is None)
    machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == sus_qt)}")
    wait_for("the Qt window closed", lambda: client("hm-s-qt") is None)

with subtest("unloading ends screenshot writes that wait for a pipe at once, and Hyprland keeps answering"):
    # The red window was closed by the socket lifecycle subtest.
    green = windows[1][1]
    size = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-unload-size.raw raw"))
    assert size["width"] * size["height"] * 4 > 1 << 20, size
    # How long hyprctl takes to answer, every 100 ms, throughout.
    machine.succeed("rm -f /tmp/hm-unload-probe-stop")
    probe = f"while [ ! -e /tmp/hm-unload-probe-stop ]; do s=$(date +%s%N); timeout 10 env {ipc} hyprctl version > /dev/null; echo $((($(date +%s%N) - s) / 1000000)); sleep 0.1; done"
    alice(f"nohup sh -c {shlex.quote(probe)} > /tmp/hm-unload-latency 2>&1 &")
    # One FIFO has no reader. The other has a reader that never reads, and the raw pixels, over a MiB, fill its pipe.
    fifo("/tmp/hm-unread.png")
    fifo("/tmp/hm-stuck.raw")
    hypr_background(f"-j hyprmasq screenshot {green} /tmp/hm-unread.png", "/tmp/hm-unread-reply")
    alice("nohup sh -c 'exec sleep 6001 < /tmp/hm-stuck.raw' > /dev/null 2>&1 &")
    hypr_background(f"-j hyprmasq screenshot {green} /tmp/hm-stuck.raw raw", "/tmp/hm-stuck-reply")
    pid = waiting_writers(2)
    machine.succeed("test ! -s /tmp/hm-unread-reply && test ! -s /tmp/hm-stuck-reply")
    machine.wait_until_succeeds("[ $(wc -l < /tmp/hm-unload-latency) -ge 2 ]", timeout=30)
    ms = int(alice(f"s=$(date +%s%N); {ipc} hyprctl plugin unload {plugin} > /tmp/hm-unload-reply; echo $((($(date +%s%N) - s) / 1000000))"))
    machine.log(f"the unload took {ms} ms")
    assert machine.succeed("cat /tmp/hm-unload-reply").strip() == "ok"
    assert ms < 2000, ms
    # Both get an answer that says why, and no thread of the plugin is left.
    for name, path in (("unread", "/tmp/hm-unread.png"), ("stuck", "/tmp/hm-stuck.raw")):
        reply = background_reply(f"/tmp/hm-{name}-reply", timeout=10)
        assert not reply["ok"] and reply["code"] == "unloaded" and path in reply["error"], reply
    machine.succeed(f"! grep -qx hyprmasq-job /proc/{pid}/task/*/comm")
    machine.wait_until_succeeds(f"! grep -q hyprmasq.so /proc/{pid}/maps", timeout=10)
    # The batch command works without the plugin.
    assert (
        hypr("--batch 'activeworkspace; activeworkspace'").count("workspace ID")
        == 2
    )
    # Two more answers, so that the probe spans the unload.
    after = int(machine.succeed("wc -l < /tmp/hm-unload-latency")) + 2
    machine.wait_until_succeeds(f"[ $(wc -l < /tmp/hm-unload-latency) -ge {after} ]", timeout=30)
    machine.succeed("pkill -f '^sleep 6001$'")
    machine.succeed("touch /tmp/hm-unload-probe-stop")
    machine.wait_until_fails("pgrep -f '^sh -c while \\[ ! -e /tmp/hm-unload-probe-stop'", timeout=30)
    latencies = [int(line) for line in machine.succeed("cat /tmp/hm-unload-latency").split()]
    machine.log(f"hyprctl answered in {latencies} ms")
    assert len(latencies) >= 4 and max(latencies) < 1000, latencies

    # Loaded again, screenshots reach a file, a FIFO that a reader opens later, and a pipe that is read.
    assert hypr(f"plugin load {plugin}") == "ok"
    assert_png(screenshot(green, "after-unload"), (0, 255, 0))
    fifo("/tmp/hm-late.png")
    hypr_background(f"-j hyprmasq screenshot {green} /tmp/hm-late.png", "/tmp/hm-late-reply")
    waiting_writers(1)
    machine.succeed("timeout 30 cat /tmp/hm-late.png > /tmp/hm-late-copy.png", timeout=60)
    reply = background_reply("/tmp/hm-late-reply")
    assert reply["ok"] and reply["path"] == "/tmp/hm-late.png", reply
    assert_png("/tmp/hm-late-copy.png", (0, 255, 0))
    fifo("/tmp/hm-read.raw")
    alice("nohup sh -c 'cat /tmp/hm-read.raw > /tmp/hm-read-copy.raw' > /dev/null 2>&1 &")
    reply = json.loads(hypr(f"-j hyprmasq screenshot {green} /tmp/hm-read.raw raw"))
    assert reply["ok"], reply
    machine.wait_until_fails("pgrep -f '^cat /tmp/hm-read.raw$'", timeout=30)
    width, height = reply["width"], reply["height"]
    assert near(raw_pixels("/tmp/hm-read-copy.raw", width, height, (width // 2, height // 2))[0], (0, 255, 0))

with subtest("the plugin can be unloaded from within a batch"):
    green = windows[1][1]
    batch(f"plugin unload {plugin}; activeworkspace")
    assert "hyprmasq" not in hypr("plugin list")
    # Once the batch has returned, the library is released and the batch command restored.
    pid = machine.succeed(
        f"sed -n 1p {runtime}/hypr/{instance}/hyprland.lock"
    ).strip()
    machine.wait_until_succeeds(
        f"! grep -q hyprmasq.so /proc/{pid}/maps", timeout=10
    )
    assert [p[:12] for p in batch("activeworkspace; activeworkspace")] == [
        "workspace ID"
    ] * 2
    assert hypr(f"plugin load {plugin}") == "ok"
    assert_png(screenshot(green, "after-reload"), (0, 255, 0))
    parts = batch(
        f"hyprmasq screenshot {green} /tmp/hm-reload-batch.png; activeworkspace"
    )
    assert parts[0] == "ok" and parts[1].startswith("workspace ID"), parts
