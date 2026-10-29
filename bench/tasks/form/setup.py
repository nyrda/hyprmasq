# The form task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# The questionnaire, in the toolkit of the variant (vm.nix), is started like a user would, on the visible workspace,
# and moved to the hidden workspace `form`, not with hyprmasq's launch. The user stays on workspace 1 with the
# terminal. collect() asks the app for its state (SIGUSR1 to the process its launcher recorded in app.pid) and
# grades it, with the windows that opened from root's own log of Hyprland's events, but for the X11 classes of the
# app's own popups, which vm.nix names.
#
# The app writes its state to a file that only root can open, which root opens and passes to the app as its file
# descriptor 3: no other process of the user's, such as one the agent started, can write it.

ETC = "/etc/hyprmasq-bench/form"
STATE = "/tmp/hyprmasq-bench-form"
# Hyprland's events, with the process of each window that opens (windows.py), kept by root where the user's
# programs, which the agent can start, cannot change them.
WINDOWS = "/var/log/hyprmasq-bench-form-windows.log"
SNAPSHOT = "/var/log/hyprmasq-bench-form-state.json"
form_count = form_window = form_pid = None


def snapshot_written():
    status, data = machine.execute(f"cat {SNAPSHOT}")
    try:
        return status == 0 and json.loads(data) is not None
    except ValueError:
        return False


def prepare():
    global form_count, form_window, form_pid
    # The variant, from vm.nix.
    form_count = int(machine.succeed("cat /etc/hyprmasq-bench/form-count"))
    machine.succeed(
        f"nohup python3 {ETC}/windows.py {RUNTIME}/hypr/{instance}/.socket2.sock {WINDOWS} "
        "> /tmp/hyprmasq-bench-form-windows.log 2>&1 &"
    )
    alice(f"mkdir -p {STATE}")
    machine.succeed(f"install -m 600 /dev/null {SNAPSHOT}")
    command = f"{client_env} exec hyprmasq-bench-form {STATE} {form_count} 3"
    machine.succeed(
        f"nohup su - alice -c {shlex.quote(command)} 3>{SNAPSHOT} > /tmp/hyprmasq-bench-form.log 2>&1 &"
    )
    wait_for("the questionnaire", lambda: window(lambda c: c["title"] == "Questionnaire") is not None, timeout=120)
    form = window(lambda c: c["title"] == "Questionnaire")
    # The app's process, which its launcher recorded: the window's own process can be a child of it, as a
    # browser's is.
    form_window = form["address"]
    form_pid = int(machine.succeed(f"cat {STATE}/app.pid").strip())
    move(form_window, "form")

    terminal = window(lambda c: c["title"] == "user-terminal")["address"]
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{terminal}" }})'))

    settle("the windows to settle", initial_delay=3, interval=3,
           fields=("address", "title", "workspace", "size"))
    return form_window


def collect():
    # The app's own process, if it still runs: an app that quit wrote no state, and the run fails.
    if machine.execute(f"kill -0 {form_pid}")[0] == 0:
        machine.succeed(f"kill -USR1 {form_pid}")
        try:
            wait_for("the questionnaire's state", snapshot_written, timeout=30)
        except AssertionError:
            machine.log("the questionnaire wrote no state")
    popups = "".join(f" --popup {shlex.quote(c)}"
                     for c in machine.succeed("cat /etc/hyprmasq-bench/form-popups").split())
    check = json.loads(machine.succeed(
        f"python3 {ETC}/check.py --state {SNAPSHOT} --count {form_count} --window {form_window} "
        f"--events {WINDOWS} --since {vm_time}{popups}"
    ))
    for path, name in [
        (SNAPSHOT, "vm-form-state.json"),
        (f"{STATE}/events.jsonl", "vm-form-events.jsonl"),
        (f"{STATE}/submissions.jsonl", "vm-form-submissions.jsonl"),
        ("/tmp/hyprmasq-bench-form.log", "vm-form.log"),
        (WINDOWS, "vm-form-windows.log"),
    ]:
        fetch(path, name)
    events = check.pop("events")
    return {"check": check, "events": events}
