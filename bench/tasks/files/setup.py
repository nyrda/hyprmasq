# The file manager task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# Mara's folder Project, START in check.py, is made fresh in her home, and Nautilus shows it. Nautilus is started like a
# user would, on the visible workspace, and moved to the hidden workspace `files`, not with hyprmasq's launch. The
# user stays on workspace 1 with the terminal. Root keeps a log of Hyprland's events, with the process of each window
# that opens (the form task's windows.py), and a record of the folder (watch.py) for the milestones, both where the
# user's programs, which the agent could start, cannot change them. collect() grades the folder.

# Nautilus registers its application on the session bus of alice's systemd user manager.
HYPRLAND_ENV = "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus"

ETC = "/etc/hyprmasq-bench/files"
ROOT = "/home/alice/Project"
CLASS = "org.gnome.Nautilus"
WINDOWS = "/var/log/hyprmasq-bench-files-windows.log"
WATCH = "/var/log/hyprmasq-bench-files-watch.jsonl"


def make_folder():
    import runpy
    for path, contents in runpy.run_path(os.path.join(TASK_DIR, "check.py"))["START"].items():
        full = shlex.quote(os.path.join(ROOT, path))
        if contents is None:
            alice(f"mkdir -p {full}")
        else:
            alice(f"printf %s {shlex.quote(contents)} > {full}")


def prepare():
    machine.succeed(
        f"nohup python3 /etc/hyprmasq-bench/files-windows.py {RUNTIME}/hypr/{instance}/.socket2.sock {WINDOWS} "
        "> /tmp/hyprmasq-bench-files-windows.log 2>&1 &"
    )
    make_folder()
    machine.succeed(f"nohup python3 {ETC}/watch.py {ROOT} {WATCH} > /tmp/hyprmasq-bench-files-watch.log 2>&1 &")
    alice(f"{client_env} {HYPRLAND_ENV} nohup nautilus --new-window {ROOT} > /tmp/hyprmasq-bench-nautilus.log 2>&1 &")
    wait_for("Nautilus", lambda: window(lambda c: c["class"] == CLASS) is not None, timeout=120)
    nautilus = window(lambda c: c["class"] == CLASS)["address"]
    move(nautilus, "files")

    terminal = window(lambda c: c["title"] == "user-terminal")["address"]
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{terminal}" }})'))

    settle("the windows to settle", initial_delay=3, interval=3,
           fields=("address", "title", "workspace", "size"))
    return nautilus


def collect():
    # Nautilus renames and moves through GIO at once, but give a move that is still on its way a moment.
    time.sleep(1)
    check = json.loads(machine.succeed(
        f"python3 {ETC}/check.py --root {ROOT} --class {CLASS} --events {WINDOWS} --since {vm_time} --watch {WATCH}"
    ))
    for path, name in [
        (WATCH, "vm-files-watch.jsonl"),
        (WINDOWS, "vm-files-windows.log"),
        ("/tmp/hyprmasq-bench-nautilus.log", "vm-nautilus.log"),
    ]:
        fetch(path, name)
    events = check.pop("events")
    return {"check": check, "events": events}
