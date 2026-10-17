# The board task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# Mara's Firefox shows her guide's figure of the motherboard. It is started like a user would, on the visible
# workspace, and moved to the hidden workspace `web`, not with hyprmasq's launch. The user stays on workspace 1 with
# the terminal. With HYPRMASQ_BENCH_WAYLAND_DEBUG set in the driver's environment, Firefox logs its Wayland
# protocol (WAYLAND_DEBUG=1) to vm-firefox.log, for checks by hand.

HYPRLAND_ENV = "MOZ_ENABLE_WAYLAND=1"

ETC = "/etc/hyprmasq-bench/board"
STATE = "/var/lib/hyprmasq-bench-guide"
PROFILE = "/home/alice/.mozilla/firefox/mara"
URL = "http://guide.example.test/figures/motherboard"


def window(predicate):
    return next((c for c in clients() if predicate(c)), None)


def move(address, workspace):
    hypr("dispatch " + shlex.quote(
        f'hl.dsp.window.move({{ window = "address:{address}", workspace = "name:{workspace}", follow = false }})'
    ))
    wait_for(f"{address} on {workspace}", lambda: window(
        lambda c: c["address"] == address and c["workspace"]["name"] == workspace) is not None)


def page_laid_out():
    return machine.execute(f"grep -q '\"type\": \"layout\"' {STATE}/events.jsonl")[0] == 0


def prepare():
    machine.wait_for_unit("hyprmasq-bench-guide.service")
    machine.wait_for_open_port(80)
    alice(f"mkdir -p {PROFILE} && install -m 0644 {ETC}/user.js {PROFILE}/")
    debug = "WAYLAND_DEBUG=1" if os.environ.get("HYPRMASQ_BENCH_WAYLAND_DEBUG") else ""
    alice(
        f"{client_env} {HYPRLAND_ENV} {debug} nohup firefox --profile {PROFILE} {shlex.quote(URL)} "
        "> /tmp/hyprmasq-bench-firefox.log 2>&1 &"
    )
    wait_for("Firefox", lambda: window(lambda c: c["class"] == "firefox") is not None, timeout=120)
    firefox = window(lambda c: c["class"] == "firefox")["address"]
    wait_for("the figure to load", page_laid_out, timeout=120)
    move(firefox, "web")

    terminal = window(lambda c: c["title"] == "user-terminal")["address"]
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{terminal}" }})'))

    def settled():
        state = [(c["address"], c["title"], c["workspace"]["name"], c["size"]) for c in clients()]
        time.sleep(5)
        return state == [(c["address"], c["title"], c["workspace"]["name"], c["size"]) for c in clients()]

    time.sleep(5)
    wait_for("the windows to settle", settled, timeout=120)
    return firefox


def collect():
    time.sleep(1)  # the page sends its events every 250 ms
    check = json.loads(machine.succeed(f"python3 {ETC}/check.py --state {STATE} --since {vm_time}"))
    for path, name in [
        (f"{STATE}/board.json", "vm-board.json"),
        (f"{STATE}/places.jsonl", "vm-board-places.jsonl"),
        (f"{STATE}/events.jsonl", "vm-board-events.jsonl"),
        ("/tmp/hyprmasq-bench-firefox.log", "vm-firefox.log"),
    ]:
        fetch(path, name)
    events = check.pop("events")
    return {"check": check, "events": events}
