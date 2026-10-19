# The Calc task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# Mara's Firefox shows her five reports as tabs, and Calc her sheet. Both are started like a user would, on the
# visible workspace, and moved to hidden ones, not with hyprmasq's launch: side by side on `work` when the monitor
# is 2560 logical pixels wide or more, else Firefox on `web` and Calc on `calc`. The user stays on workspace 1 with
# the terminal. Calc gets a UNO pipe, through which watch.py follows the sheet for the milestones.

HYPRLAND_ENV = "MOZ_ENABLE_WAYLAND=1 SAL_USE_VCLPLUGIN=gtk3"

ETC = "/etc/hyprmasq-bench/calc"
STATE = "/var/lib/hyprmasq-bench-calc"
INTRANET_STATE = "/var/lib/hyprmasq-bench-intranet"
PROFILE = "/home/alice/.mozilla/firefox/mara"
UNO_PIPE = "hyprmasq-bench-calc"

FIGURES = {}
exec(open(os.path.join(TASK_DIR, "figures.py")).read(), FIGURES)
SHEET = f"/home/alice/Documents/{FIGURES['FILE_NAME']}"
ORIGINAL = f"/etc/hyprmasq-bench/calc-sheet/{FIGURES['FILE_NAME']}"
URLS = [FIGURES["INTRANET"] + path for path, _ in FIGURES["TABS"]]


def tabs_loaded():
    """Whether every tab has fetched its figures from the intranet, signed in."""
    status, log = machine.execute(f"cat {INTRANET_STATE}/requests.jsonl")
    fetched = {r["path"] for r in map(json.loads, log.splitlines() if status == 0 else [])
               if r["status"] == 200 and r["signed_in"]}
    return all("/api" + path in fetched for path, _ in FIGURES["TABS"])


def prepare():
    machine.wait_for_unit("hyprmasq-bench-intranet.service")
    machine.wait_for_open_port(80)
    machine.succeed(f"install -d -o alice -g users -m 0755 {STATE}")
    q = shlex.quote
    alice(
        f"mkdir -p ~/Documents ~/Downloads ~/.config/libreoffice/4/user {PROFILE} && "
        f"install -m 0644 {q(ORIGINAL)} ~/Documents/ && "
        f"install -m 0644 {ETC}/registrymodifications.xcu ~/.config/libreoffice/4/user/ && "
        f"install -m 0644 {ETC}/user.js {PROFILE}/"
    )
    monitor = json.loads(hypr("-j monitors"))[0]
    wide = monitor["width"] / monitor["scale"] >= 2560
    web, calc = ("work", "work") if wide else ("web", "calc")

    # Firefox with Mara's tabs, signed in to the intranet while sign-in-open exists.
    alice(
        f"{client_env} {HYPRLAND_ENV} nohup firefox --profile {PROFILE} {' '.join(map(q, URLS))} "
        "> /tmp/hyprmasq-bench-firefox.log 2>&1 &"
    )
    wait_for("Firefox", lambda: window(lambda c: c["class"] == "firefox") is not None, timeout=120)
    firefox = window(lambda c: c["class"] == "firefox")["address"]
    wait_for("Mara's tabs to load", tabs_loaded, timeout=120)
    machine.succeed(f"rm {INTRANET_STATE}/sign-in-open")
    move(firefox, web)

    # Calc with Mara's sheet. LibreOffice asks for a maximized window, which would cover Firefox on a shared
    # workspace; Hyprland's example configuration ignores such requests from every app, as this rule does for Calc.
    hypr("eval " + q('hl.window_rule({ name = "hyprmasq-bench-calc", match = { class = "(libreoffice|soffice).*" }, '
                     'suppress_event = "maximize" })'))
    alice(
        f"{client_env} {HYPRLAND_ENV} nohup soffice --nologo --accept={q(f'pipe,name={UNO_PIPE};urp;')} {q(SHEET)} "
        "> /tmp/hyprmasq-bench-calc.log 2>&1 &"
    )
    def is_calc(c):
        return c["title"].startswith(FIGURES["FILE_NAME"])

    wait_for("Calc", lambda: window(is_calc) is not None, timeout=180)
    sheet_window = window(is_calc)["address"]
    move(sheet_window, calc)
    alice(
        f"nohup /etc/hyprmasq-bench/calc-watch {UNO_PIPE} {q(SHEET)} {STATE}/watch.jsonl "
        "> /tmp/hyprmasq-bench-calc-watch.log 2>&1 &"
    )
    wait_for("the watch of the sheet", lambda: machine.execute(f"grep -q cells {STATE}/watch.jsonl")[0] == 0,
             timeout=60)

    terminal = window(lambda c: c["title"] == "user-terminal")["address"]
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{terminal}" }})'))

    settle("the windows to settle", initial_delay=5, interval=5,
           fields=("address", "title", "workspace", "size"))
    machine.succeed(f"python3 {ETC}/check.py --manifest /home/alice > {STATE}/home-before.json")
    machine.succeed(f"echo {q(json.dumps({'firefox': firefox, 'calc': sheet_window}))} > {STATE}/windows.json")
    return firefox


def collect():
    time.sleep(3)  # Firefox writes its session every 2 s
    check = json.loads(machine.succeed(
        f"python3 {ETC}/check.py --home /home/alice --state {STATE} --requests {INTRANET_STATE}/requests.jsonl "
        f"--original {shlex.quote(ORIGINAL)} --profile {PROFILE} --events /tmp/hyprmasq-bench-events.log "
        f"--since {vm_time}"
    ))
    fetch(SHEET, "sheet.ods")
    for path, name in [
        (f"{STATE}/watch.jsonl", "vm-calc-watch.jsonl"),
        (f"{INTRANET_STATE}/requests.jsonl", "vm-intranet.jsonl"),
        ("/tmp/hyprmasq-bench-firefox.log", "vm-firefox.log"),
        ("/tmp/hyprmasq-bench-calc.log", "vm-calc.log"),
        ("/tmp/hyprmasq-bench-calc-watch.log", "vm-calc-watch.log"),
    ]:
        fetch(path, name)
    events = check.pop("events")
    return {"check": check, "events": events}
