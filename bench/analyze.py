#!/usr/bin/env python3
"""Counts friction and splits tool time for benchmark runs.

    analyze.py RESULTS-DIR

Prints one table row per run and writes RESULTS-DIR/friction.json. Each count comes from the MCP log and
Hyprland's events of a run:

- wait_acts: act calls without actions, made only to wait for the window.
- pause_s: pause actions the agent added, in seconds.
- quiet_overrides: act calls that set wait.quiet_ms or wait.expect_ms.
- new_windows: windows that mapped during the run after the start.
- missed: those that mapped after the act that caused them had returned, so no result reported them.
- followups: windows or screenshot calls right after a new window mapped, before the agent acted on it.
- user_ws: new windows that mapped on the user's visible workspace.
- closing_errors: window_gone errors from input to a window that closed, as a dialog does on its OK button.
- timeouts: act calls whose wait timed out.

The time of the act calls, in seconds, split into what the results and the sender's pacing account for:

- wait_s: the wait for the window to settle, as each result reports it.
- watch_s: the watch for a second redraw and for new windows after the wait, which input that can open a window
  gets: the time the act tool's description gives it, 500 ms, or longer when the result says the window drew again.
  When a redraw in the watch made the result one wait, after a first wait without redraw or ending in a timeout, the
  wait has that time.
- input_s: the sender's pacing of the actions sent, without pauses: gaps, holds, typing at 15 ms a key, the 300 ms
  from a click to the keys after it, and at least 150 ms for the window's answer to a click.
- rest_s: the rest of the calls' time, not clamped: the captures and their comparison, the check of targets, the
  shots of new windows and of the window a closed one came from with their waits, a click's answer beyond its
  least, the sender's start and round trips, locks and transport. The results do not time these apart.
"""

import json
import math
import os
import re
import sys

import report

WAIT = re.compile(r"quiet \d+ ms(?: except [^·]*)? · (\d+) ms|no redraw within (\d+) ms|timeout after (\d+) ms")
DELIVERED = re.compile(r"(\d+)/\d+ delivered")
# Input that was sent though the sender could not confirm all of it, and the keys of the action it stopped in.
PARTIAL = re.compile(r"\d+/\d+ delivered, delivery (?:partial|uncertain)")
KEYS = re.compile(r"may be partly applied \((\d+) keys sent\)")
# A wait that the client's cancellation ended, which act watches nothing after.
CANCELLED = re.compile(r"(?:^| · )cancelled(?: · |$)", re.M)
AGAIN = re.compile(r"drew again, settled (\d+) ms later")
WATCH = re.compile(r"starts within (\d+) ms after it settled")

# The sender's pacing, from tools/hyprmasq-input.c, and how act turns its actions into the sender's
# (mcp/internal/server/act.go and input.go).
ACTION_GAP_MS = 50
CLICK_HOLD_MS, CLICK_GAP_MS = 50, 80
CHORD_MOD_MS, CHORD_HOLD_MS, CHORD_GAP_MS = 10, 15, 15
TYPE_HOLD_MS, TYPE_GAP_MS = 5, 10
WHEEL_GAP_MS = 20
MOTION_PRESS_MS, DRAG_HOLD_MS, DRAG_STEP_MS, DRAG_STEP_PX, DRAG_MIN_STEPS, DRAG_MAX_STEPS = 30, 50, 10, 10, 21, 2000
CLICK_SETTLE_MS = 300
# A click that more actions follow waits for the window's answer: for a redraw up to 150 ms and then
# for 150 ms of quiet, so at least 150 ms.
ANSWER_MS = 150


def events(run_dir, offset):
    path = os.path.join(run_dir, "vm-events.log")
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            t, _, event = line.rstrip("\n").partition(" ")
            name, _, data = event.partition(">>")
            out.append((float(t) + offset, name, data))
    return out


def sender_steps(actions):
    """The sender's steps of a batch, as act's translateBatch makes them: a click or drag that more actions follow
    gets a sync after it, which lasts until keys or text come CLICK_SETTLE_MS after it."""
    steps, sync, since = [], None, 0
    for i, a in enumerate(actions):
        kind = a.get("action")
        if kind == "click":
            seq = [["move"], ["click", a.get("count", 1), bool(a.get("modifiers"))]]
        elif kind == "move":
            seq = [["move"]]
        elif kind == "drag":
            # In the shot's pixels, where the sender steps through logical ones: a guess at a scale other than 1. Its
            # modifiers go down while the press waits for the motion, which takes no longer.
            (x1, y1), (x2, y2) = a.get("from", (0, 0)), a.get("to", (0, 0))
            seq = [["drag", math.hypot(x2 - x1, y2 - y1)]]
        elif kind == "scroll":
            notches = int(abs(a.get("dx", 0)) + abs(a.get("dy", 0))) if a.get("unit", "notch") == "notch" else 0
            seq = [["move"], ["wheel", notches]]
        elif kind == "key":
            # act joins the keys with "+", as in ["ctrl", "l"] or ["ctrl+l"], and a "+" means modifiers.
            seq = [["keys", "+" in "+".join(a.get("keys", [])), a.get("repeat", 1)]]
        elif kind == "type":
            seq = [["text", a.get("text", "")]]
        elif kind == "pause":
            seq = [["sleep", a.get("ms", 0)]]
        else:
            seq = []
        for step in seq:
            if sync is not None:
                since += ACTION_GAP_MS
                if step[0] in ("text", "keys"):
                    steps[sync][1] = max(0, CLICK_SETTLE_MS - since + ACTION_GAP_MS)
                    sync = None
                elif step[0] == "sleep":
                    since += step[1]
            steps.append(step)
        if kind in ("click", "drag") and i < len(actions) - 1:
            steps.append(["sync", 0])
            sync, since = len(steps) - 1, 0
    return steps


def pacing(actions):
    """The least time in ms that the sender takes for the actions at its pace, without the pauses, and the pauses.
    A click waits for the window's answer before the actions after it."""
    steps = sender_steps(actions)
    paced = pauses = 0
    for i, step in enumerate(steps):
        verb = step[0]
        if verb == "sync":
            continue
        if i:
            # A sync takes the place of the gap after the click: the next action comes when both have passed.
            prev = steps[i - 1]
            paced += max(ACTION_GAP_MS, prev[1], ANSWER_MS) if prev[0] == "sync" else ACTION_GAP_MS
        if verb == "click":
            paced += step[1] * CLICK_HOLD_MS + (step[1] - 1) * CLICK_GAP_MS + (CHORD_MOD_MS if step[2] else 0)
        elif verb == "drag":
            moves = min(max(DRAG_MIN_STEPS, math.ceil(step[1] / DRAG_STEP_PX)), DRAG_MAX_STEPS)
            paced += MOTION_PRESS_MS + 2 * DRAG_HOLD_MS + (moves - 1) * DRAG_STEP_MS
        elif verb == "wheel":
            paced += max(0, step[1] - 1) * WHEEL_GAP_MS
        elif verb == "keys":
            press = CHORD_HOLD_MS + (CHORD_MOD_MS if step[1] else 0)
            paced += step[2] * press + (step[2] - 1) * CHORD_GAP_MS
        elif verb == "text":
            # Every character is a key, and a CR LF one Enter; one typed through Compose takes more.
            keys = len(step[1]) - step[1].count("\r\n")
            paced += keys * TYPE_HOLD_MS + (keys - 1) * TYPE_GAP_MS if keys > 0 else 0
        elif verb == "sleep":
            pauses += step[1]
    return paced, pauses


def opens_windows(actions):
    """Whether act watches for a redraw and new windows after the batch: a click, drag or key, or text with a line
    break."""
    for a in actions:
        if a.get("action") in ("click", "drag", "key") or a.get("action") == "type" and re.search("[\r\n]", a.get("text", "")):
            return True
    return False


def act_tool(tap):
    """The watch after the wait in ms, from the current act tool's description."""
    for entry in tap:
        for tool in ((entry.get("msg") or {}).get("result") or {}).get("tools") or []:
            if tool.get("name") == "act":
                match = WATCH.search(json.dumps(tool))
                if not match:
                    raise ValueError("Unparseable act watch description: " + json.dumps(tool))
                return int(match.group(1))
    raise ValueError("Missing act tool description in MCP log")


def split(call, a, structured, watch_ms):
    """The seconds of an act call: its wait, its watch, the sender's pacing, the pauses and the rest."""
    actions = a.get("actions") or []
    wait = structured.get("wait") or {}
    if structured:
        done = structured.get("actions", {})
        delivered = done.get("done", len(actions))
        # act treats input as applied, and watches after it, unless nothing was sent: also when the sender could not
        # confirm it.
        applied = done.get("delivery", "complete") != "none"
        sent = actions[:delivered]
        keys = (done.get("progress") or {}).get("keys", 0)
    else:
        # Results for Claude Code say it only in their text, and errors name the actions delivered on
        # their second line.
        text = call["summary"] + "\n" + call["hint"]
        m = DELIVERED.search(text)
        if not m and actions and not call["summary"].startswith("error"):
            raise ValueError("Unparseable act delivery summary: " + text)
        delivered = int(m.group(1)) if m else 0
        sent = actions[:delivered]
        applied = bool(sent) or bool(PARTIAL.search(text))
        keys = int(m.group(1)) if (m := KEYS.search(text)) else 0
        if CANCELLED.search(text):
            wait = {"ended": "cancelled"}
    # The keys that an interrupted type or key action sent before it stopped.
    if keys and delivered < len(actions):
        partial = actions[delivered]
        if partial.get("action") == "type":
            sent = sent + [{**partial, "text": partial.get("text", "")[:keys]}]
        elif partial.get("action") == "key":
            # Modifiers do not count, so a key is a press of the chord.
            sent = sent + [{**partial, "repeat": keys}]
    # Pauses left when the input closed its window during the send count as delivered (#16), but the sender did not
    # wait them out. A window that closed during the wait or later had them. The text does not tell when it closed.
    if structured:
        closed_sending = wait.get("ended") == "window_closed" and not wait.get("elapsed_ms")
    else:
        closed_sending = "window closed" in call["summary"]
    if closed_sending:
        while sent and sent[-1].get("action") == "pause":
            sent = sent[:-1]
    if "elapsed_ms" in wait:
        waited = wait["elapsed_ms"]
        again = (wait.get("again") or {}).get("elapsed_ms", 0)
    else:
        m, again = WAIT.search(call["summary"]), AGAIN.search(call["summary"])
        if not m and not (call["summary"].startswith("error") or "window closed" in call["summary"]
                          or "no time left to wait" in call["summary"]
                          or wait.get("ended") in ("cancelled", "window_closed")):
            raise ValueError("Unparseable act wait summary: " + call["summary"])
        waited = int(next(g for g in m.groups() if g)) if m else 0
        again = int(again.group(1)) if again else 0
    watched = watch_ms if applied and opens_windows(actions) and wait.get("ended") != "cancelled" else 0
    if watched and "timeout after" in call["summary"]:
        # A watch that ran into the deadline is in the wait's time. A wait that did before it leaves at most the grace,
        # which then stays in the rest.
        watched = 0
    elif watched and not again:
        # After a wait that saw no redraw, a redraw in the watch makes the result one wait, of its expect_ms and the
        # watch: a wait without a second wave that took at least expect_ms and the quiet time. The grace for new
        # windows lasts the watch's time from the end of the first wait.
        settings = a.get("wait") or {}
        expect = settings.get("expect_ms", 1000)
        if waited >= expect + settings.get("quiet_ms", 200):
            watched = max(0, watched - (waited - expect))
    elif watched:
        watched = max(watched, again)
    paced, pauses = pacing(sent)
    # The grace ends at the latest 250 ms before the call's deadline, which a short deadline_ms brings forward, and
    # starts after at least the input and the wait.
    if watched and not again:
        watched = max(0, min(watched, a.get("deadline_ms", 15000) - 250 - waited - paced - pauses))
    parts = {"wait": waited / 1000, "watch": watched / 1000, "input": paced / 1000, "pauses": pauses / 1000}
    parts["rest"] = call["seconds"] - sum(parts.values())
    return parts


def analyze(run_dir):
    run = json.load(open(os.path.join(run_dir, "run.json")))
    ready = json.load(open(os.path.join(run_dir, "ready.json")))
    offset = ready["host_time"] - ready["vm_time"]
    start = run["agent_start"]
    tap = report.load_jsonl(os.path.join(run_dir, "mcp.jsonl"))
    calls = report.tool_calls(tap)

    args, structured, started = {}, {}, {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            args[entry["t"]] = msg["params"].get("arguments") or {}
            started[msg.get("id")] = entry["t"]
        elif entry["dir"] == "response" and msg.get("id") in started:
            structured[started.pop(msg["id"])] = (msg.get("result") or {}).get("structuredContent") or {}
    watch_ms = act_tool(tap)

    wait_acts = pause_ms = overrides = closing_errors = timeouts = 0
    time = {"wait": 0.0, "watch": 0.0, "input": 0.0, "rest": 0.0}
    for c in calls:
        if c["tool"] != "act" or c.get("end") is None:
            continue
        a = args.get(c["start"], {})
        actions = a.get("actions") or []
        wait_acts += not actions
        pauses = sum(x.get("ms", 0) for x in actions if x.get("action") == "pause")
        pause_ms += pauses
        wait = a.get("wait") or {}
        overrides += bool(wait.get("quiet_ms") or wait.get("expect_ms"))
        closing_errors += "window_gone" in c["summary"] and "closed while" in c["summary"]
        timeouts += "timeout after" in c["summary"]
        for part, seconds in split(c, a, structured.get(c["start"], {}), watch_ms).items():
            if part in time:
                time[part] += seconds

    user_ws = str(ready["user"]["workspace"])
    opened = [(t, data) for t, name, data in events(run_dir, offset) if name == "openwindow" and t > start]
    missed = followups = on_user = 0
    for t, data in opened:
        on_user += data.split(",", 3)[1] == user_ws
        cause = [c for c in calls if c["start"] <= t and c["tool"] == "act"]
        if cause and cause[-1].get("end") and cause[-1]["end"] < t:
            missed += 1
        for c in [c for c in calls if c["start"] > t][:3]:
            if c["tool"] not in ("windows", "screenshot"):
                break
            followups += 1

    return {
        "run": os.path.basename(run_dir),
        "wait_acts": wait_acts,
        "pause_s": round(pause_ms / 1000, 1),
        "quiet_overrides": overrides,
        "new_windows": len(opened),
        "missed": missed,
        "followups": followups,
        "user_ws": on_user,
        "closing_errors": closing_errors,
        "timeouts": timeouts,
        **{part + "_s": round(seconds, 1) for part, seconds in time.items()},
        "acts": sum(1 for c in calls if c["tool"] == "act"),
    }


def main(results_dir):
    rows = []
    for name in sorted(os.listdir(results_dir)):
        path = os.path.join(results_dir, name)
        if os.path.exists(os.path.join(path, "summary.json")):
            rows.append(analyze(path))
    keys = list(rows[0].keys())
    print("| " + " | ".join(keys) + " |")
    print("| " + " | ".join("---" for _ in keys) + " |")
    for r in rows:
        print("| " + " | ".join(str(r[k]) for k in keys) + " |")
    with open(os.path.join(results_dir, "friction.json"), "w") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    if sys.argv[1:] == ["--help"]:
        print(__doc__)
    else:
        main(sys.argv[1])
