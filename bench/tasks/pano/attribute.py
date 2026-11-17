"""What each key that an agent held came to in the pano task's viewer: attribute.py RUN-DIR | RESULTS-DIR

It reads a run's act calls from mcp.jsonl and the page's key events from vm-pano-events.jsonl. For each hold, and each
key pressed with a key action, that an act call sent to the task's window, it finds the page's press of that key during
the call, in order, and compares the time the agent asked for with the time between the press and the release that
the page got. A hold that the page saw for the time asked, within TOLERANCE_MS, is `held`. One that the page saw for
another time, or not at all, is the tool's: `hyprmasq`, or the run's other backend. A hold that was not sent, as in a
batch that was refused or stopped before it, is `not sent`. Keys pressed with a key action are listed with the time
the page saw them down, without a verdict: they ask for no time.

It also counts how the agent turned the view: the holds, how many were chords, the keys it tapped, the act calls
that held keys, and the largest difference between a time asked and a time seen. It prints a table per run and writes
attribution.json.
"""

import json
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))
import actlog  # noqa: E402
from actlog import load_json, load_jsonl, page_events  # noqa: E402

EVENTS = "vm-pano-events.jsonl"
# How far, in milliseconds, the time the page saw a key down may be from the time asked and still be that time: the
# compositor's and the browser's event times are whole milliseconds, and the sender's wait ends within a few more.
TOLERANCE_MS = 50
# How long before a call's start and after its end, in seconds, the page's press for one of its actions may lie: the
# clocks of the host and the VM are read once, when the run starts.
SLACK = 1.0
# The page's names of the keys that the viewer takes, as act names them.
PAGE_KEYS = {"arrowleft": "left", "arrowright": "right", "arrowup": "up", "arrowdown": "down", " ": "space",
             "control": "ctrl", "escape": "esc"}
ACT_KEYS = {"return": "enter", "escape": "esc", "control": "ctrl", "del": "delete", "meta": "super", "win": "super",
            "cmd": "super"}


def page_key(name):
    lower = name.lower()
    return PAGE_KEYS.get(lower, lower)


def chord(keys):
    """The keys of an action's chord as act splits and names them, in lower case."""
    if isinstance(keys, str):
        keys = [keys]
    if not isinstance(keys, list):
        return []
    names = []
    for element in keys:
        if not isinstance(element, str):
            return []
        names += ["plus"] if element == "+" else element.split("+")
    return [ACT_KEYS.get(n.lower(), n.lower()) for n in names]


def asked(action):
    """What an action asks of the keyboard: its chord, the milliseconds of a hold or None, and its presses. None for an
    action that presses no key. Anthropic's hold_key names its chord text and its seconds duration."""
    kind = action.get("action")
    if kind == "hold_key":
        seconds = action.get("duration")
        ms = action.get("ms") if "ms" in action else (round(seconds * 1000) if isinstance(seconds, (int, float)) else None)
        return {"keys": chord(action.get("keys", action.get("text"))), "ms": ms, "presses": 1}
    if kind == "hold":
        return {"keys": chord(action.get("keys")), "ms": action.get("ms"), "presses": 1}
    if kind == "key":
        repeat = action.get("repeat")
        return {"keys": chord(action.get("keys")), "ms": None, "presses": repeat if isinstance(repeat, int) else 1}
    return None


def page_presses(page):
    """The keys the page saw go down and come up again, in the order they went down: their key, the host time of the
    press and how long they were down, in milliseconds. A key that never came up has no time."""
    presses, down = [], {}
    for e in page:
        # A press of a key that is down is its autorepeat, also where the browser does not mark it, as Firefox on
        # Wayland does not.
        if e["type"] == "keydown" and page_key(e["key"]) in down:
            down[page_key(e["key"])]["repeats"] += 1
        elif e["type"] == "keydown" and not e.get("repeat"):
            press = {"key": page_key(e["key"]), "host": e["host"], "t": e["t"], "ms": None, "repeats": 0}
            down[press["key"]] = press
            presses.append(press)
        elif e["type"] == "keyup" and page_key(e["key"]) in down:
            press = down.pop(page_key(e["key"]))
            press["ms"] = round(e["t"] - press["t"], 1)
        elif e["type"] == "blur":
            # A window that loses the keyboard gets no release, and the viewer takes its keys for let go: the next
            # press of one of them is a new one.
            down.clear()
    return presses


def attribute(run_dir):
    ready = load_json(os.path.join(run_dir, "ready.json"))
    window = ready["task_window"]
    offset = ready["host_time"] - ready["vm_time"]  # VM time + offset = host time
    calls = actlog.calls(load_jsonl(os.path.join(run_dir, "mcp.jsonl")), run_dir)
    # The tool the run used, which takes the blame: hyprmasq, or the run's other backend.
    tool = (load_json(os.path.join(run_dir, "run.json")) or {}).get("backend", "hyprmasq")
    presses = page_presses(page_events(run_dir, EVENTS, offset))
    used = set()

    keys, holding_calls = [], 0
    for n, call in enumerate(calls):
        # The page's presses of a call lie between its start and its end, give or take SLACK, and no further than
        # half-way to the calls before and after it, whose presses are theirs.
        first, last = call["start"] - SLACK, call["end"] + SLACK
        if n:
            first = max(first, (calls[n - 1]["end"] + call["start"]) / 2)
        if n + 1 < len(calls):
            last = min(last, (call["end"] + calls[n + 1]["start"]) / 2)
        # An act on another window is not the task's. One whose window the log does not tell counts by what it delivered.
        if call["actions"] is None or call["window"] not in (window, None):
            continue
        done = (call["result"].get("actions") or {}).get("done", 0)
        held = False
        for i, action in enumerate(call["actions"]):
            ask = asked(action)
            if not ask or not ask["keys"]:
                continue
            entry = {"time": round(call["start"], 3), "action": action.get("action"), "keys": "+".join(ask["keys"]),
                     "asked_ms": ask["ms"]}
            if ask["presses"] > 1:
                entry["presses"] = ask["presses"]
            if i >= done:
                entry.update(outcome="not sent", blame=None, why=call["summary"])
                keys.append(entry)
                continue
            held = held or ask["ms"] is not None
            # The page's presses of the chord's last key during the call, in order, that no action before took.
            seen = [m for m, p in enumerate(presses) if m not in used and p["key"] == ask["keys"][-1]
                    and first <= p["host"] <= last][:ask["presses"]]
            used.update(seen)
            # The keys that the chord holds around its last one are the action's too.
            for key in ask["keys"][:-1] if seen else []:
                used.update([m for m, p in enumerate(presses) if m not in used and p["key"] == key
                             and first <= p["host"] <= last][:1])
            times = [presses[m]["ms"] for m in seen]
            entry["seen"] = len(seen)
            if ask["ms"] is None:
                # A key action asks for no time: what the page saw is told, not judged.
                entry.update(seen_ms=[t for t in times if t is not None], outcome="pressed" if seen else "not seen",
                             blame=None if seen else tool, why="" if seen else "the page saw no press of the key")
            elif not seen:
                entry.update(outcome="not seen", blame=tool, why="the page saw no press of the key")
            elif times[0] is None:
                entry.update(outcome="not released", blame=tool, why="the page saw the key go down and not come up")
            else:
                entry.update(seen_ms=times[0], off_ms=round(times[0] - ask["ms"], 1), repeats=presses[seen[0]]["repeats"])
                if abs(entry["off_ms"]) <= TOLERANCE_MS:
                    entry.update(outcome="held", blame=None, why="")
                else:
                    entry.update(outcome="held another time", blame=tool,
                                 why=f"the page saw the key down for {times[0]:g} ms, not {ask['ms']} ms")
            keys.append(entry)
        holding_calls += held

    holds = [k for k in keys if k["asked_ms"] is not None]
    offs = [abs(k["off_ms"]) for k in holds if "off_ms" in k]
    summary = {
        "holds": len(holds),
        "holds_sent": sum(1 for k in holds if k["outcome"] != "not sent"),
        "held": sum(1 for k in holds if k["outcome"] == "held"),
        "chords": sum(1 for k in holds if "+" in k["keys"]),
        "asked_ms": sum(k["asked_ms"] for k in holds if k["outcome"] != "not sent" and isinstance(k["asked_ms"], (int, float))),
        "key_actions": sum(1 for k in keys if k["asked_ms"] is None),
        "key_presses": sum(k.get("seen", 0) for k in keys if k["asked_ms"] is None),
        "act_calls_holding": holding_calls,
        tool: sum(1 for k in keys if k.get("blame") == tool),
        "median_off_ms": round(statistics.median(offs), 1) if offs else None,
        "max_off_ms": max(offs, default=None),
        # What the page saw that no action asked for, such as keys of another tool or of the user.
        "other_presses": len(presses) - len(used),
    }
    out = {"summary": summary, "keys": keys}
    with open(os.path.join(run_dir, "attribution.json"), "w") as f:
        json.dump(out, f, indent=2)
    return out


def show(run_dir, out):
    s = out["summary"]
    print(f"## {os.path.basename(os.path.normpath(run_dir))}\n")
    print(f"{s['holds_sent']} of {s['holds']} holds sent in {s['act_calls_holding']} act calls, {s['chords']} of them "
          f"chords, for {s['asked_ms']} ms; {s['held']} held for the time asked, median {s['median_off_ms']} ms and "
          f"at most {s['max_off_ms']} ms off; {s['key_actions']} key actions with {s['key_presses']} presses; "
          f"{s['other_presses']} presses that no action asked for.")
    print("\n| # | Action | Keys | Asked ms | Seen ms | Off ms | Repeats | Outcome | Why |")
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for n, k in enumerate(out["keys"], 1):
        seen = k.get("seen_ms", "–")
        if isinstance(seen, list):
            seen = ", ".join(f"{t:g}" for t in seen[:6]) + (" …" if len(seen) > 6 else "") or "–"
        asked_ms = "–" if k["asked_ms"] is None else k["asked_ms"]
        print(f"| {n} | {k['action']}{' ×' + str(k['presses']) if 'presses' in k else ''} | {k['keys']} | {asked_ms} | "
              f"{seen} | {k.get('off_ms', '–')} | {k.get('repeats', '–')} | {k['outcome']} | {k['why']} |")
    print()


def main(path):
    runs = [path] if os.path.exists(os.path.join(path, "ready.json")) else [
        os.path.join(path, name) for name in sorted(os.listdir(path))
        if os.path.exists(os.path.join(path, name, EVENTS))]
    for run in runs:
        show(run, attribute(run))


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main(sys.argv[1])
