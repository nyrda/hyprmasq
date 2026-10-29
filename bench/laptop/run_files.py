#!/usr/bin/env python3
"""One file manager run on a real Hyprland machine: hyprmasq or Cua Driver, each from its own agent home.

    run_files.py --backend cua|hyprmasq --rep N --out DIR [--harness codex|claude] [--model M] [--effort E]
                 [--timeout S] [--no-agent]

Nautilus, Omarchy's file manager, shows a folder named Project on a hidden workspace. The agent renames
report-draft.txt to report-final.txt and moves it into the folder Archive. The grade reads the folder: the report
in Archive under its new name with its contents unchanged, every other file and folder as it was, and no window of
another app opened while the agent worked. The setup, agent run and event log are run_form.py's.
"""

import argparse
import hashlib
import json
import os
import signal
import socket
import subprocess
import time

import run_form as rf

# The folder at the start: path relative to Project, and the file's contents, or None for a folder.
START = {
    "Archive": None,
    "Archive/report-2025.txt": "Quarterly report 2025, final.\n",
    "Drafts": None,
    "Drafts/ideas.txt": "Ideas for the spring campaign.\n",
    "budget.csv": "item,amount\nprinting,120\nvenue,800\n",
    "notes.txt": "Call the printer on Monday.\n",
    "report-draft.txt": "Quarterly report 2026, ready for review.\n",
}
EXPECTED = {("Archive/report-final.txt" if p == "report-draft.txt" else p): c for p, c in START.items()}

PROMPT = (
    "You are helping Mara Lindqvist on her Linux desktop. You can see and control it only through your desktop "
    "tools. Mara is working in another window. She has authorized foreground input and focus changes on this "
    "desktop if your tools need them, but prefer ways that leave her window alone.\n\n"
    "The file manager is open in a window showing the folder Project. In that folder, rename the file "
    "report-draft.txt to report-final.txt and move it into the folder Archive inside Project. Leave every other "
    "file and folder as it is. Work only in that window: do not start other programs. When you are done, say "
    "\"Done\"."
)


def tree(root):
    """The folder's files and folders, relative to root: a file's SHA-256, None for a folder."""
    found = {}
    for path, dirs, files in os.walk(root):
        for name in dirs:
            found[os.path.relpath(os.path.join(path, name), root)] = None
        for name in files:
            with open(os.path.join(path, name), "rb") as f:
                found[os.path.relpath(os.path.join(path, name), root)] = hashlib.sha256(f.read()).hexdigest()
    return found


def expected_tree():
    return {p: (hashlib.sha256(c.encode()).hexdigest() if c is not None else None) for p, c in EXPECTED.items()}


def grade(root):
    found, want = tree(root), expected_tree()
    failures = [f"missing: {p}" for p in sorted(want) if p not in found]
    failures += [f"unexpected: {p}" for p in sorted(found) if p not in want]
    failures += [f"changed: {p}" for p in sorted(want) if p in found and found[p] != want[p]]
    return failures, found


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
    target = None
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
        for path, contents in START.items():
            full = os.path.join(root, path)
            if contents is None:
                os.makedirs(full, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w") as f:
                    f.write(contents)
        # Nautilus runs as one process for all its windows, which may already be running: only the window is the
        # run's own.
        known = {c["address"] for c in rf.clients()}
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
        # As the prompt says to work only in the file manager, a window of another app fails the run. Nautilus's own
        # dialogs, such as a rename conflict, are of its class.
        others = [w for w in opened if (w.split(",", 3) + ["", "", ""])[2] != target["class"]]
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


if __name__ == "__main__":
    main()
