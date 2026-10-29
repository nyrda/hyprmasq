#!/usr/bin/env python3
"""One Inkscape run on a real Hyprland machine: hyprmasq or Cua Driver, each from its own agent home.

    run_inkscape.py --backend cua|hyprmasq --rep N --out DIR [--harness codex|claude] [--model M] [--effort E]
                    [--timeout S] [--no-agent]

Port of the Inkscape task of the 2026-10-19 Cua comparison (bench/results/cua-task-comparison-20261019) from the
NixOS VM to a live Hyprland session: move the blue rectangle from x=120 to x=122 px and save. Inkscape 1.4.4-6 from
Arch at /usr/bin/inkscape is one of the two builds Cua's Hyprland input plugin accepts for background input, so this
is Cua's best case on Omarchy. The setup, agent run and event log are run_form.py's.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import time

import run_form as rf

TASK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inkscape")
EXPECTED = [122.0, 120.0, 200.0, 100.0]

PROMPT = (
    "You are helping Mara Lindqvist on her Linux desktop. You can see and control it only through your desktop "
    "tools. Mara is working in another window. She has authorized foreground input and focus changes on this "
    "desktop if your tools need them, but prefer ways that leave her window alone.\n\n"
    "Inkscape is open in a window showing drawing.svg. In that drawing, move the blue rectangle exactly 2 px to "
    "the right, from x=120 px to x=122 px. Keep y=120 px, width=200 px and height=100 px. Save the existing SVG "
    "with Ctrl+S. Work only in that window: do not start other programs. When you are done, say \"Done\"."
)


def geometry(path):
    """The saved rectangle's x, y, width and height, from Inkscape run headless on the file."""
    env = {k: v for k, v in rf.ENV.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    rows = subprocess.run(["inkscape", "--query-all", path], env=env, capture_output=True, text=True).stdout
    for row in rows.splitlines():
        fields = row.split(",")
        if fields[0] == "bench-rectangle" and len(fields) == 5:
            return list(map(float, fields[1:]))
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["cua", "hyprmasq"], required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--harness", choices=["codex", "claude"], default="codex")
    parser.add_argument("--no-agent", action="store_true")
    args = parser.parse_args()

    out = os.path.join(args.out, f"{args.backend}-{args.rep}")
    os.makedirs(out)
    events_path = f"{out}/hyprland-events.log"
    rf.EventLog(events_path).start()

    procs = []
    original = json.loads(rf.hypr("-j", "activewindow"))
    rf.dispatch('hl.dsp.dpms({ action = "on" })')
    rf.wait_for("the display to power on",
                lambda: all(m["dpmsStatus"] for m in json.loads(rf.hypr("-j", "monitors"))))
    if subprocess.run(["/usr/share/omarchy/bin/omarchy-hyprland-session-locked"], env=rf.ENV).returncode == 0:
        raise SystemExit("the session is locked")
    try:
        rf.to_user_workspace()
        procs.append(subprocess.Popen(["foot", "--app-id", "user-terminal", "--title", "user-terminal", "sleep",
                                       "infinity"], env=rf.ENV, stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL, start_new_session=True))
        terminal = rf.wait_for("the user terminal", lambda: rf.window(lambda c: c["class"] == "user-terminal"))

        # A fresh copy of the drawing in its own directory, named as in the task.
        drawing = f"{out}/drawing/drawing.svg"
        os.makedirs(os.path.dirname(drawing))
        shutil.copy(f"{TASK}/drawing.svg", drawing)
        os.chmod(drawing, 0o644)
        known = {c["address"] for c in rf.clients()}
        inkscape = subprocess.Popen(["inkscape", drawing], env=dict(rf.ENV, GDK_BACKEND="wayland"),
                                    stdout=open(f"{out}/inkscape.log", "w"), stderr=subprocess.STDOUT,
                                    start_new_session=True)
        procs.append(inkscape)
        target = rf.wait_for("Inkscape", lambda: rf.window(
            lambda c: c["address"] not in known and c["class"] in ("org.inkscape.Inkscape", "Inkscape")
            and "drawing.svg" in c["title"]), timeout=120)
        rf.dispatch(f'hl.dsp.window.move({{ window = "address:{target["address"]}", workspace = "name:drawing", '
                    f'follow = false }})')
        rf.dispatch(f'hl.dsp.focus({{ window = "address:{terminal["address"]}" }})')
        # Inkscape draws its canvas and panels for a few seconds after mapping.
        time.sleep(8)
        before = {"active_window": json.loads(rf.hypr("-j", "activewindow")),
                  "active_workspace": json.loads(rf.hypr("-j", "activeworkspace")),
                  "clients": rf.clients(), "terminal": terminal["address"], "form": target["address"],
                  "target_pid": inkscape.pid, "exe": os.readlink(f"/proc/{inkscape.pid}/exe")}
        with open(f"{out}/before.json", "w") as f:
            json.dump(before, f, indent=2)
        rf.check_setup(before)

        if args.no_agent:
            agent = {"start": time.time(), "end": time.time(), "exit": None, "timed_out": False}
        else:
            rf.PROMPT = PROMPT
            agent = rf.run_agent(args.backend, args.model, args.effort, out, args.timeout, args.harness)

        after = {"active_window": json.loads(rf.hypr("-j", "activewindow")),
                 "active_workspace": json.loads(rf.hypr("-j", "activeworkspace")), "clients": rf.clients()}
        with open(f"{out}/after.json", "w") as f:
            json.dump(after, f, indent=2)

        found = geometry(drawing)
        opened = []
        with open(events_path) as f:
            for line in f:
                t, _, rest = line.rstrip("\n").partition(" ")
                if rest.startswith("openwindow>>") and float(t) >= agent["start"]:
                    opened.append(rest.split(">>", 1)[1])
        # As the prompt says to work only in Inkscape's window, a window of another app fails the run. Inkscape's
        # own dialogs are of its class.
        others = [w for w in opened if (w.split(",", 3) + ["", "", ""])[2] != target["class"]]
        failures = ([] if found is not None and all(abs(x - y) <= 0.01 for x, y in zip(found, EXPECTED))
                    else [f"saved rectangle geometry {found}, expected {EXPECTED}"])
        failures += [f"a window other than Inkscape's opened: {w}" for w in others]
        passed = not failures
        check = {"passed": passed, "failures": failures, "checks": {"geometry": found, "windows_opened": opened}}
        summary = {
            "backend": args.backend, "rep": args.rep, "task": "inkscape", "variant": None,
            "harness": args.harness, "model": args.model, "effort": args.effort, "agent": agent,
            "wall_seconds": round(agent["end"] - agent["start"], 1),
            "passed": passed, "failures": check["failures"], "check": check,
            "machine": {"host": socket.gethostname(), "hyprland": rf.hypr("version").splitlines()[0]},
        }
        with open(f"{out}/summary.json", "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps({k: summary[k] for k in ("backend", "rep", "wall_seconds", "passed", "failures")}))
    finally:
        for proc in procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        try:
            rf.to_user_workspace()
        except Exception:
            pass


if __name__ == "__main__":
    main()
