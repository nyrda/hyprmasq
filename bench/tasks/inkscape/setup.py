# The Inkscape task's hooks for bench/vm.py, which runs this file in its own namespace before it starts Hyprland.
#
# Inkscape shows Mara's drawing, ~/Documents/drawing.svg, a copy of the task's drawing.svg. It is started like a
# user would, on the visible workspace, and moved to the hidden workspace `drawing`, not with hyprmasq's launch, as
# bench/laptop/run_inkscape.py does on a real machine. The user stays on workspace 1 with the terminal. collect()
# grades the drawing as saved against the task's copy, with the windows that opened from Hyprland's events and
# Inkscape's own reading of the rectangle.

ETC = "/etc/hyprmasq-bench/inkscape"
DRAWING = "/home/alice/Documents/drawing.svg"
INKSCAPE_CLASS = "org.inkscape.Inkscape"
inkscape_window = None


def prepare():
    global inkscape_window
    alice(f"mkdir -p ~/Documents && install -m 0644 {ETC}/drawing.svg {DRAWING}")
    alice(f"{client_env} nohup inkscape {DRAWING} > /tmp/hyprmasq-bench-inkscape.log 2>&1 &")

    def is_inkscape(c):
        return c["class"] == INKSCAPE_CLASS and "drawing.svg" in c["title"]

    wait_for("Inkscape", lambda: window(is_inkscape) is not None, timeout=180)
    inkscape_window = window(is_inkscape)["address"]
    move(inkscape_window, "drawing")

    terminal = window(lambda c: c["title"] == "user-terminal")["address"]
    hypr("dispatch " + shlex.quote(f'hl.dsp.focus({{ window = "address:{terminal}" }})'))

    # Inkscape draws its canvas and panels for a few seconds after it maps its window.
    settle("the windows to settle", initial_delay=5, interval=5,
           fields=("address", "title", "workspace", "size"))
    return inkscape_window


def collect():
    end = window(lambda c: c["address"] == inkscape_window)
    title = f" --title {shlex.quote(end['title'])}" if end else ""
    check = json.loads(machine.succeed(
        f"python3 {ETC}/check.py --drawing {DRAWING} --original {ETC}/drawing.svg "
        f"--events /tmp/hyprmasq-bench-events.log --class {INKSCAPE_CLASS} --since {vm_time}{title} "
        "--inkscape inkscape"
    ))
    fetch(DRAWING, "drawing.svg")
    fetch("/tmp/hyprmasq-bench-inkscape.log", "vm-inkscape.log")
    events = check.pop("events")
    return {"check": check, "events": events}
