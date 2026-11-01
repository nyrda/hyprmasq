#!/usr/bin/env python3
"""One file manager run on a real Hyprland machine: hyprmasq or Cua Driver, each from its own agent home.

    run_files.py --backend cua|hyprmasq --rep N --out DIR [--harness codex|claude] [--model M] [--effort E]
                 [--timeout S] [--claude-auth api-key|login] [--no-agent]

Nautilus, Omarchy's file manager, shows a folder named Project on a hidden workspace. The agent renames
report-draft.txt to report-final.txt and moves it into the folder Archive. The grade reads the folder: the report
in Archive under its new name with its contents unchanged, every other file and folder as it was, and no window of
another app opened while the agent worked. A dialog of xdg-desktop-portal, such as the folder chooser of Nautilus's
Move to…, counts as the file manager's own. The setup, agent run and event log are run_form.py's.
"""

import argparse
import json
import os
import signal
import socket
import subprocess
import time

import run_form as rf
from files_task import grade, make, tree

PROMPT = (
    "You are helping Mara Lindqvist on her Linux desktop. You can see and control it only through your desktop "
    "tools. Mara is working in another window. She has authorized foreground input and focus changes on this "
    "desktop if your tools need them, but prefer ways that leave her window alone.\n\n"
    "The file manager is open in a window showing the folder Project. In that folder, rename the file "
    "report-draft.txt to report-final.txt and move it into the folder Archive inside Project. Leave every other "
    "file and folder as it is. Work only in that window: do not start other programs. When you are done, say "
    "\"Done\"."
)


def own_window(opened, app_class):
    """Whether a window that opened, as socket2's openwindow gives it, is the file manager's: of its class, as its own
    dialogs such as a rename conflict are, or of xdg-desktop-portal, which draws the folder chooser of Move to…
    for it."""
    window_class = (opened.split(",", 3) + ["", "", ""])[2]
    return window_class == app_class or window_class.startswith("xdg-desktop-portal")


def running_nautilus():
    return [int(p) for p in subprocess.run(["pgrep", "-x", "nautilus"], capture_output=True,
                                           text=True).stdout.split()]


def stop_nautilus():
    """Ends every Nautilus process: SIGTERM, then SIGKILL to one that has not ended within 10 s."""
    for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        for pid in running_nautilus():
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass
        deadline = time.time() + wait
        while running_nautilus() and time.time() < deadline:
            time.sleep(0.2)
        if not running_nautilus():
            return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["cua", "hyprmasq"], required=True)
    parser.add_argument("--rep", type=int, required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--harness", choices=["codex", "claude"], default="codex")
    parser.add_argument("--claude-auth", choices=["api-key", "login"], default="api-key",
                        help="Claude Code billed to $ANTHROPIC_API_KEY, or to the login in its agent home")
    parser.add_argument("--no-agent", action="store_true")
    args = parser.parse_args()
    rf.CLAUDE_AUTH = args.claude_auth

    out = os.path.join(args.out, f"{args.backend}-{args.rep}")
    os.makedirs(out)
    events_path = f"{out}/hyprland-events.log"
    rf.EventLog(events_path).start()

    procs = []
    target = None
    # Whether this run started Nautilus: only then does it end Nautilus, never one that the user runs.
    started_nautilus = False
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

        # A fresh folder in the run's directory, named as in the task.
        root = f"{out}/files/Project"
        make(root)
        # Nautilus runs as one process for all its windows, and outlives them: a run gets a process of its own,
        # which it ends, so that a drag or dialog an agent left in it does not carry into the next run.
        if running_nautilus():
            raise SystemExit("Nautilus already runs: a run needs a Nautilus process of its own")
        known = {c["address"] for c in rf.clients()}
        started_nautilus = True
        subprocess.Popen(["nautilus", "--new-window", root], env=dict(rf.ENV, GDK_BACKEND="wayland"),
                         stdout=open(f"{out}/nautilus.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
        target = rf.wait_for("Nautilus", lambda: rf.window(
            lambda c: c["class"] == "org.gnome.Nautilus" and c["address"] not in known), timeout=60)
        rf.dispatch(f'hl.dsp.window.move({{ window = "address:{target["address"]}", workspace = "name:files", '
                    f'follow = false }})')
        rf.dispatch(f'hl.dsp.focus({{ window = "address:{terminal["address"]}" }})')
        time.sleep(3)
        before = {"active_window": json.loads(rf.hypr("-j", "activewindow")),
                  "active_workspace": json.loads(rf.hypr("-j", "activeworkspace")),
                  "clients": rf.clients(), "terminal": terminal["address"], "form": target["address"],
                  "tree": tree(root)}
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

        # Nautilus renames and moves through GIO at once, but give a move that is still on its way a moment.
        time.sleep(1)
        failures, found = grade(root)
        opened = []
        with open(events_path) as f:
            for line in f:
                t, _, rest = line.rstrip("\n").partition(" ")
                if rest.startswith("openwindow>>") and float(t) >= agent["start"]:
                    opened.append(rest.split(">>", 1)[1])
        # As the prompt says to work only in the file manager, a window of another app fails the run.
        others = [w for w in opened if not own_window(w, target["class"])]
        failures += [f"a window other than the file manager's opened: {w}" for w in others]
        passed = not failures
        check = {"passed": passed, "failures": failures, "checks": {"tree": found, "windows_opened": opened}}
        summary = {
            "backend": args.backend, "rep": args.rep, "task": "files", "variant": None,
            "harness": args.harness, "model": args.model, "effort": args.effort, "agent": agent,
            "wall_seconds": round(agent["end"] - agent["start"], 1),
            "passed": passed, "failures": failures, "check": check,
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
            if target:
                rf.dispatch(f'hl.dsp.window.close({{ window = "address:{target["address"]}" }})')
            rf.to_user_workspace()
        except Exception:
            pass
        if started_nautilus:
            stop_nautilus()


if __name__ == "__main__":
    main()
