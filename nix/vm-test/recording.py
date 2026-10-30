def rec_files():
    """The files in the directory of recordings, which the VM's programs.hyprmasq.mcpSettings names."""
    return sorted(machine.succeed(f"ls -A {rec_dir} 2>/dev/null || true").split())


def rec_probe(path, entries):
    """Properties of the video stream of a video or GIF that ffprobe reads, counting its frames."""
    reply = machine.succeed(f"ffprobe -v error -count_frames -select_streams v:0 -show_entries stream={entries} -of json {path}")
    return json.loads(reply)["streams"][0]


def rec_shell(title):
    """Launches a shell through the recording server on a hidden workspace of its own, so that it fills the output,
    and returns its address."""
    launched = rec_agent.call("launch", {"command": ["foot", "-T", title, "-o", "colors-dark.foreground=ffffff", "bash", "--noprofile", "--norc", "-i"], "workspace": title})
    assert not launched.is_error, launched
    return launched.data["window"]["window"]


def rec_typed(text):
    return [{"action": "type", "text": text}, {"action": "key", "keys": ["enter"]}]


def rec_saved(result, mode_steps):
    """The path of a stopped recording without its suffix, after checking the line that names the video and the GIF."""
    saved = re.fullmatch(rf"recording saved: ({rec_dir}/\d{{8}}-\d{{6}})\.mp4 \(0:0\d, {mode_steps}, \d+ kB\), GIF \1\.gif \(\d+ kB\)", result.data["recording"])
    assert saved and result.text.endswith("\n" + result.data["recording"]), result
    for suffix in (".mp4", ".gif"):
        machine.copy_from_machine(saved[1] + suffix)
    return saved[1]


# The VM's programs.hyprmasq.mcpSettings sends recordings to rec_dir, with a GIF of each, so the server here runs with
# the settings that the NixOS module writes to /etc/xdg/hyprmasq/mcp.toml.
rec_dir = "/tmp/hm-recordings"
rec_before = snapshot()
focus_workspace(94)
rec_user = foot("hm-r-user")
wait_drawn(rec_user, 500)
rec_agent = Mcp("rec")
rec_agent.initialize()
rec_one, rec_two = rec_shell("hm-r-one"), rec_shell("hm-r-two")

with subtest("hyprmasq-mcp records slides of acts in two hidden windows, with the settings of the NixOS module"):
    assert rec_dir in machine.succeed("cat /etc/xdg/hyprmasq/mcp.toml") and rec_files() == []
    rec_started = rec_agent.call("act", {"window": rec_one, "actions": rec_typed("echo one"), "record": "slides", "caption": "Type in the first shell"})
    assert not rec_started.is_error and rec_started.data["actions"]["delivery"] == "complete", rec_started
    assert re.fullmatch(rf'recording slides to {rec_dir}/\d{{8}}-\d{{6}}\.mp4; an act with record "stop" saves it', rec_started.data["recording"]), rec_started
    rec_second = rec_agent.call("act", {"window": rec_two, "actions": rec_typed("echo two"), "caption": "Then in the second"})
    assert not rec_second.is_error and "recording" not in rec_second.data, rec_second
    rec_stopped = rec_agent.call("act", {"window": rec_two, "record": "stop"})
    assert rec_stopped.data["wait"]["ended"] == "quiet", rec_stopped
    rec_slides = rec_saved(rec_stopped, "2 steps")
    # The shells of 640×480 over the bar of 40 pixels: each act's input for a second and its result for 1.5, at 4
    # frames a second, and the last frame for a second more.
    rec_video = rec_probe(rec_slides + ".mp4", "codec_name,pix_fmt,width,height,duration,nb_read_frames")
    assert (rec_video["codec_name"], rec_video["pix_fmt"], rec_video["width"], rec_video["height"]) == ("h264", "yuv420p", 640, 520), rec_video
    assert rec_video["nb_read_frames"] == "24" and abs(float(rec_video["duration"]) - 6) < 0.05, rec_video
    assert rec_probe(rec_slides + ".gif", "nb_read_frames")["nb_read_frames"] == "4"
    rec_name = rec_slides.rsplit("/", 1)[1]
    assert rec_files() == [rec_name + ".gif", rec_name + ".mp4"], rec_files()
    assert snapshot() == (94, rec_user), snapshot()

with subtest("hyprmasq-mcp records video of an act at real speed, and the act settles as soon as without"):
    # The shell prints a line every 250 ms for 1.5 s, and the wait ends 600 ms after the last one.
    rec_loop = "for i in 1 2 3 4 5 6; do echo line $i; sleep 0.25; done"
    rec_plain = rec_agent.call("act", {"window": rec_one, "actions": rec_typed(rec_loop), "wait": {"quiet_ms": 600}})
    rec_filmed = rec_agent.call("act", {"window": rec_one, "actions": rec_typed(rec_loop), "wait": {"quiet_ms": 600}, "record": "video"})
    for rec_result in (rec_plain, rec_filmed):
        assert rec_result.data["wait"]["ended"] == "quiet" and rec_result.data["wait"]["elapsed_ms"] >= 1800, rec_result
    # The frames that the recording renders during the wait do not delay its end.
    assert rec_filmed.data["wait"]["elapsed_ms"] <= rec_plain.data["wait"]["elapsed_ms"] + 500, (rec_plain, rec_filmed)
    rec_movie = rec_saved(rec_agent.call("act", {"window": rec_one, "record": "stop"}), "1 step")
    # The act's 2 s and more, at up to 10 frames a second, its result for a second, and the last frame for a second.
    rec_video = rec_probe(rec_movie + ".mp4", "width,height,duration,nb_read_frames")
    assert (rec_video["width"], rec_video["height"]) == (640, 520) and float(rec_video["duration"]) >= 3.8 and int(rec_video["nb_read_frames"]) >= 38, rec_video
    # The frames show the lines as they came: the rows with text grow between the first frame and the last, with
    # counts in between, where a video of only the frames before and after the act would have two. The sampler takes
    # fewer frames when rendering is slow, so the count of steps between depends on the machine.
    machine.succeed(f"ffmpeg -v error -i {rec_movie}.mp4 -vf crop=640:480:0:0 -f rawvideo -pix_fmt gray -y /tmp/hm-r-frames.gray")
    rec_rows = json.loads(image_script("\n".join([
        "data, size = open(path, 'rb').read(), 640 * 480",
        "frames = [data[start:start + size] for start in range(0, len(data), size)]",
        "print(json.dumps([sum(max(frame[y * 640:(y + 1) * 640]) > 150 for y in range(480)) for frame in frames]))",
    ]), path="/tmp/hm-r-frames.gray"))
    assert len(set(rec_rows)) >= 3 and rec_rows[0] < rec_rows[-1], rec_rows
    assert snapshot() == (94, rec_user), snapshot()

with subtest("hyprmasq-mcp saves the recording when the session ends"):
    rec_count = len(rec_files())
    rec_ended = rec_agent.call("act", {"window": rec_two, "actions": rec_typed("echo end"), "record": "slides"})
    assert not rec_ended.is_error, rec_ended
    for rec_window in (rec_one, rec_two):
        assert rec_agent.call("close", {"window": rec_window}).data["closed"]
    assert rec_agent.stop() == 0
    rec_left = rec_files()
    assert len(rec_left) == rec_count + 2 and not any(name.endswith(".part.mp4") or name.startswith(".") for name in rec_left), rec_left
    rec_last = sorted(name for name in rec_left if name.endswith(".mp4"))[-1]
    assert abs(float(rec_probe(f"{rec_dir}/{rec_last}", "duration")["duration"]) - 3.5) < 0.05
    assert "the session ended, and its recording was saved: " + rec_dir in machine.succeed("cat /tmp/hyprmasq-m-rec-server.log")

machine.succeed(f"kill {next(c['pid'] for c in clients() if c['address'] == rec_user)}")
wait_for("hm-r-user closed", lambda: client("hm-r-user") is None)
focus_workspace(rec_before[0])
