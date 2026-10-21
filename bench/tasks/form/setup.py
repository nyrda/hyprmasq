# The form task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# The questionnaire is started like a user would, on the visible workspace, and moved to the hidden workspace
# `form`, not with hyprmasq's launch. The user stays on workspace 1 with the terminal. collect() asks the app for
# its state (SIGUSR1) and grades it, with the windows that opened from root's own log of Hyprland's events.
#
# The app writes its state to a file that only root can open, which root opens and passes to the app as its file
# descriptor 3: no other process of the user's, such as one the agent started, can write it.

ETC = "/etc/hyprmasq-bench/form"
STATE = "/tmp/hyprmasq-bench-form"
# Hyprland's events, kept by root where the user's programs, which the agent can start, cannot change them.
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
        f"nohup /etc/hyprmasq-bench/events {RUNTIME}/hypr/{instance}/.socket2.sock {WINDOWS} > /dev/null 2>&1 &"
    )
    alice(f"mkdir -p {STATE}")
    machine.succeed(f"install -m 600 /dev/null {SNAPSHOT}")
    command = f"{client_env} exec hyprmasq-bench-form {STATE} {form_count} 3"
    machine.succeed(
        f"nohup su - alice -c {shlex.quote(command)} 3>{SNAPSHOT} > /tmp/hyprmasq-bench-form.log 2>&1 &"
    )
    wait_for("the questionnaire", lambda: window(lambda c: c["title"] == "Questionnaire") is not None, timeout=120)
    form = window(lambda c: c["title"] == "Questionnaire")
    form_window, form_pid = form["address"], form["pid"]
    move(form_window, "form")

    terminal = window(lambda c: c["title"] == "user-terminal")["address"]
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{terminal}" }})'))

    settle("the windows to settle", initial_delay=3, interval=3,
           fields=("address", "title", "workspace", "size"))
    return form_window


def collect():
    # The app's own process, if it still runs: an app that quit wrote no state, and the run fails.
    if machine.execute(f"grep -q form.py /proc/{form_pid}/cmdline")[0] == 0:
        machine.succeed(f"kill -USR1 {form_pid}")
        try:
            wait_for("the questionnaire's state", snapshot_written, timeout=30)
        except AssertionError:
            machine.log("the questionnaire wrote no state")
    check = json.loads(machine.succeed(
        f"python3 {ETC}/check.py --state {SNAPSHOT} --count {form_count} --window {form_window} "
        f"--events {WINDOWS} --since {vm_time}"
    ))
    for path, name in [
        (SNAPSHOT, "vm-form-state.json"),
        (f"{STATE}/events.jsonl", "vm-form-events.jsonl"),
        (f"{STATE}/submissions.jsonl", "vm-form-submissions.jsonl"),
        ("/tmp/hyprmasq-bench-form.log", "vm-form.log"),
    ]:
        fetch(path, name)
    events = check.pop("events")
    return {"check": check, "events": events}
