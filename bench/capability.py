#!/usr/bin/env python3
"""What each tool can do to each app, with no model: scripted actions, judged by the app's own state.

    capability.py --tool hyprmasq|cua [--case NAME]... [--source DIR] [--out DIR] [--cid CID] [--cores N]
                  [--lock FILE] [--list]
    capability.py --table DIR...

Each case boots one fresh VM of a task's fixture, with `--desktop omarchy`, and tries its routes one after the other, in
one delivery mode, as a fresh fixture for that mode: what one mode leaves behind, such as a popup or the focus, stays out
of the other's VM. A route counts as working when the app's own state shows it, read in the VM (vm.py's shell, with
HYPRMASQ_BENCH_SHELL): the questionnaire's events, the folder, the saved drawing, Calc's cells through UNO, the mail
server's log, the board's and the drawing's saved state. What the tool said goes beside it. No route's result rests on a
screenshot but a scroll's, which says so, as an app keeps no state of where it scrolled.

Each route's result is one of:
  works    the app's state changed as the action asked
  refused  the tool said it did not deliver, with its codes
  dropped  the tool said it delivered or could not tell, and the app's state did not change
  error    the script could not try it, with why

Results go to OUT/CASE/capability.json, with the VM's files, and --table draws the capability table of results
directories as Markdown. A failed route is that route's: a case proves "this route works" or "these routes refuse on
this build", not that an app cannot be automated. On a real machine, capability_host.py runs the same routes.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import sys
import time
import traceback

import mcp_probe
import report
import run

BENCH = os.path.dirname(os.path.abspath(__file__))
SETTLE = 1.5
# The display all fixtures run at, in logical pixels: the geometry below is of it.
DISPLAY = "1920x1080@1"
DESKTOP = {"kind": "desktop", "display_id": "primary"}

# Where each fixture's widgets are, in logical pixels of its window on the 1920x1080 VM, read from its screenshots.
GEOMETRY = {
    "form:25": {
        "amber": (600, 214), "beige": (697, 214), "slider": (630, 385), "slider_end": (830, 385), "text": (725, 564),
        "dropdown": (643, 709), "dropdown_item": (627, 566), "scroll": (960, 600),
    },
    "form:gtk3-25": {
        "amber": (602, 215), "beige": (698, 215), "slider": (627, 387), "slider_end": (830, 387), "text": (726, 567),
        "dropdown": (642, 714), "dropdown_item": (620, 928), "scroll": (960, 600),
    },
    "form:qt-25": {
        "amber": (601, 183), "beige": (687, 183), "slider": (625, 318), "slider_end": (820, 318), "text": (694, 467),
        "dropdown": (635, 585), "dropdown_item": (620, 740), "scroll": (960, 600),
    },
    "files": {"empty": (1500, 750), "report": (930, 120), "archive": (330, 120), "notes": (780, 120), "drafts": (480, 120)},
    "inkscape": {"empty": (1200, 600), "rect": (665, 488), "x": (563, 54)},
    "calc": {"B5": (376, 266), "B6": (376, 283)},
    "forgejo": {"empty": (1500, 700), "sign_in_link": (1850, 115)},
    "thunderbird": {"empty": (1000, 500), "name": (960, 567), "email": (960, 673), "continue": (1255, 760), "confirm": (1255, 747)},
}


class Outcome:
    """What a tool said of one call."""

    def __init__(self, ok, codes=(), said="", seconds=0.0):
        self.ok, self.codes, self.said, self.seconds = ok, list(codes), said, seconds


def result_text(result):
    return "\n".join(c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text")


class Vm:
    """A booted VM of one fixture: its MCP session, and its shell through the run's directory."""

    # The fixtures' places in the VM, which capability_host.py sets for its own machine; the window's height there; and
    # the geometry of its fixture where it is not GEOMETRY's.
    paths = {"form": "/tmp/hyprmasq-bench-form", "files": "/home/alice/Project", "drawing": "/home/alice/Documents/drawing.svg",
             "calc": "/var/lib/hyprmasq-bench-calc", "guide": "/var/lib/hyprmasq-bench-guide",
             "blog": "/var/lib/hyprmasq-bench-blog"}
    height = 1080
    geometry = None

    def __init__(self, directory, cid, session_client):
        self.directory, self.count = directory, 0
        self.session = mcp_probe.Session(cid, os.path.join(directory, "capability-mcp.jsonl"), session_client)
        with open(os.path.join(directory, "ready.json")) as f:
            self.ready = json.load(f)

    def shell(self, script, timeout=120):
        """Runs a script in the VM as root, and returns (status, output)."""
        self.count += 1
        path = os.path.join(self.directory, f"probe-{self.count}")
        with open(path + ".part", "w") as f:
            f.write(script)
        os.rename(path + ".part", path + ".sh")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(path + ".json"):
                with open(path + ".json") as f:
                    answer = json.load(f)
                return answer["status"], answer["output"]
            time.sleep(0.1)
        raise TimeoutError(f"the VM did not answer {script[:80]!r}")

    def hypr(self, command):
        return self.shell(
            'export XDG_RUNTIME_DIR=/run/user/1000 HYPRLAND_INSTANCE_SIGNATURE="$(cat /tmp/hyprmasq-bench-instance)"; '
            f"su alice -c {shlex.quote(f'XDG_RUNTIME_DIR=/run/user/1000 HYPRLAND_INSTANCE_SIGNATURE=$(cat /tmp/hyprmasq-bench-instance) hyprctl {command}')}"
        )[1]

    def focused(self):
        return json.loads(self.hypr("-j activewindow")).get("address")

    def workspace(self):
        return json.loads(self.hypr("-j activeworkspace")).get("id")


class Cua:
    """Cua Driver's calls on one window, in one delivery mode: coordinates in logical pixels of the window."""

    name = "cua"

    def __init__(self, vm, window, mode="background"):
        self.vm, self.window_id, self.mode = vm, int(window, 16), mode
        listed = vm.session.call("list_windows", {})["structuredContent"]["windows"]
        self.pid = next(w["pid"] for w in listed if w["window_id"] == self.window_id)
        self.scale = 1.0
        self.state()

    def call(self, tool, arguments):
        start = time.time()
        result = self.vm.session.call(tool, arguments)
        text = result_text(result)
        structured = result.get("structuredContent") or {}
        refused = report.cua_refused(tool, result, text)
        codes = report.call_refusals(text + json.dumps(structured), "cua", bool(result.get("isError"))) if refused else []
        return Outcome(not refused, codes, text[:400], time.time() - start), structured

    @property
    def target(self):
        return {"pid": self.pid, "window_id": self.window_id}

    @property
    def delivery(self):
        return {"delivery_mode": "foreground"} if self.mode == "foreground" else {}

    def state(self, tree="both", timeout_ms=10000):
        """Reads the window: its screenshot's scale for the pixels, and returns its elements."""
        _, structured = self.call("get_window_state", {**self.target, "tree_format": tree, "timeout_ms": timeout_ms})
        self.scale = structured.get("frame_scale") or self.scale
        return structured.get("elements") or []

    def element(self, role, label=None, nth=0):
        """The token of the nth element with that role, and label if given, in a fresh read."""
        found = [e for e in self.state() if e.get("role") == role and (label is None or e.get("label") == label)]
        return found[nth]["element_token"] if len(found) > nth else None

    def px(self, point):
        return round(point[0] * self.scale), round(point[1] * self.scale)

    def click(self, point, **options):
        x, y = self.px(point)
        return self.call("click", {**self.target, "x": x, "y": y, **self.delivery, **options})[0]

    def double_click(self, point):
        x, y = self.px(point)
        return self.call("double_click", {**self.target, "x": x, "y": y, **self.delivery})[0]

    def key(self, *keys):
        """A key, or a chord of them, such as "ctrl", "s"."""
        if len(keys) == 1:
            return self.call("press_key", {**self.target, "key": keys[0], **self.delivery})[0]
        return self.call("hotkey", {**self.target, "keys": list(keys), **self.delivery})[0]

    def text(self, text):
        return self.call("type_text", {**self.target, "text": text, **self.delivery})[0]

    def drag(self, start, end, steps=None):
        (x0, y0), (x1, y1) = self.px(start), self.px(end)
        extra = {"steps": steps} if steps else {}
        return self.call("drag", {**self.target, "from_x": x0, "from_y": y0, "to_x": x1, "to_y": y1, **self.delivery,
                                  **extra})[0]

    def scroll(self, point, direction="down", amount=5):
        x, y = self.px(point)
        return self.call("scroll", {**self.target, "x": x, "y": y, "direction": direction, "amount": amount,
                                    **self.delivery})[0]

    def path(self, points):
        """A stroke through several points: Cua's way of a curve, joined straight drags."""
        outcome = None
        for start, end in zip(points, points[1:]):
            outcome = self.drag(start, end)
            if not outcome.ok:
                return outcome
        return outcome

    def shot(self):
        result = self.vm.session.call("get_window_state", {**self.target, "include_accessibility_tree": False})
        image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
        return hashlib.sha256(base64.b64decode(image["data"])).hexdigest() if image else None

    # Cua's own routes, beside what both tools can do.
    def set_value(self, token, value):
        return self.call("set_value", {**self.target, "element_token": token, "value": value, **self.delivery})[0]

    def click_element(self, token):
        return self.call("click", {**self.target, "element_token": token, **self.delivery})[0]

    def desktop_hotkey(self, *keys):
        return self.call("hotkey", {"target": DESKTOP, "keys": list(keys)})[0]

    def desktop_click(self, point):
        """A desktop click, whose pixels are the screen's, the window's own where it fills the screen."""
        return self.call("click", {"target": DESKTOP, "x": point[0], "y": point[1]})[0]

    def desktop_text(self, text):
        return self.call("type_text", {"target": DESKTOP, "text": text})[0]

    def desktop_key(self, key):
        return self.call("press_key", {"target": DESKTOP, "key": key})[0]

    def desktop_drag(self, start, end):
        return self.call("drag", {"target": DESKTOP, "from_x": start[0], "from_y": start[1], "to_x": end[0],
                                  "to_y": end[1]})[0]


class Hyprmasq:
    """hyprmasq's calls on one window: coordinates in logical pixels of the window, turned into its shot's. It has one
    delivery mode, the background."""

    name = "hyprmasq"
    mode = "background"

    def __init__(self, vm, window, mode="background"):
        self.vm, self.window = vm, window
        self.shot_id, self.scale = None, 1.0
        self.fresh()

    def call(self, tool, arguments):
        start = time.time()
        result = self.vm.session.call(tool, arguments)
        text = result_text(result)
        structured = result.get("structuredContent") or {}
        failed = bool(result.get("isError"))
        return Outcome(not failed, [structured.get("code")] if failed and structured.get("code") else [], text[:400],
                       time.time() - start), result

    def fresh(self):
        """A new shot, which act's pixels are in."""
        _, result = self.call("screenshot", {"window": self.window})
        shot = (result.get("structuredContent") or {}).get("shot") or {}
        self.shot_id, frame = shot.get("id"), shot.get("frame") or {}
        width = frame.get("width") or 1920
        self.scale = (shot.get("width") or width) / width
        return shot

    def px(self, point):
        return round(point[0] * self.scale), round(point[1] * self.scale)

    def act(self, *actions):
        self.fresh()
        return self.call("act", {"window": self.window, "shot": self.shot_id, "actions": list(actions)})[0]

    def click(self, point, **options):
        x, y = self.px(point)
        return self.act({"action": "click", "x": x, "y": y, **options})

    def double_click(self, point):
        x, y = self.px(point)
        return self.act({"action": "click", "x": x, "y": y, "count": 2})

    def key(self, *keys):
        return self.call("act", {"window": self.window, "actions": [{"action": "key", "keys": [list(keys) if False else "+".join(keys)]}]})[0]

    def text(self, text):
        return self.call("act", {"window": self.window, "actions": [{"action": "type", "text": text}]})[0]

    def drag(self, start, end, steps=None):
        return self.path([start, end])

    def path(self, points):
        return self.act({"action": "path", "points": [list(self.px(p)) for p in points]})

    def scroll(self, point, direction="down", amount=5):
        x, y = self.px(point)
        return self.act({"action": "scroll", "x": x, "y": y, "dy": amount if direction == "down" else -amount,
                         "unit": "notch"})

    def shot(self):
        _, result = self.call("screenshot", {"window": self.window})
        image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
        return hashlib.sha256(base64.b64decode(image["data"])).hexdigest() if image else None


def reveal(tool):
    """Puts the tool's window in view, for the route that needs it there."""
    if hasattr(tool, "show"):
        tool.show()


class Desktop(Cua):
    """Cua Driver's desktop route: Omarchy's key bind reveals the window's workspace, and then input goes to the desktop,
    in screen pixels, where the window fills the screen. It is global input, the route its skill names for what the
    exact-window ones refuse."""

    mode = "desktop"

    def __init__(self, vm, window, mode="desktop"):
        super().__init__(vm, window, "foreground")
        self.mode, self.window = "desktop", window
        place = self.show()
        # The desktop's pixels are its screenshot's: on a scaled monitor not the logical ones of the window's place.
        text = result_text(vm.session.call("get_desktop_state", {}))
        found = re.search(r"Desktop screenshot (\d+)x(\d+)", text)
        monitor = json.loads(vm.hypr("-j monitors"))[0]
        logical = monitor["width"] / monitor["scale"]
        self.desktop_scale = int(found.group(1)) / logical if found else 1.0
        self.at = place["at"]

    def show(self):
        """Omarchy's key bind reveals the window's workspace."""
        place = next(c for c in json.loads(self.vm.hypr("-j clients")) if c["address"] == self.window)
        self.desktop_hotkey("super", place["workspace"]["name"])
        time.sleep(1)
        if str(self.vm.workspace()) != place["workspace"]["name"]:
            raise RuntimeError(f"the key bind did not reveal workspace {place['workspace']['name']}")
        return place

    def px(self, point):
        return round((point[0] + self.at[0]) * self.desktop_scale), round((point[1] + self.at[1]) * self.desktop_scale)

    def click(self, point, **options):
        x, y = self.px(point)
        return self.call("click", {"target": DESKTOP, "x": x, "y": y, **options})[0]

    def double_click(self, point):
        x, y = self.px(point)
        return self.call("double_click", {"target": DESKTOP, "x": x, "y": y})[0]

    def key(self, *keys):
        if len(keys) == 1:
            return self.desktop_key(keys[0])
        return self.desktop_hotkey(*keys)

    def text(self, text):
        return self.desktop_text(text)

    def drag(self, start, end, steps=None):
        return self.desktop_drag(self.px(start), self.px(end))

    def scroll(self, point, direction="down", amount=5):
        x, y = self.px(point)
        return self.call("scroll", {"target": DESKTOP, "x": x, "y": y, "direction": direction, "amount": amount})[0]

    def path(self, points):
        return super().path(points)

    def shot(self):
        result = self.vm.session.call("get_desktop_state", {})
        image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
        return hashlib.sha256(base64.b64decode(image["data"])).hexdigest() if image else None


class Routes:
    """One case's routes and what became of each."""

    def __init__(self, tool, app, directory, only=None):
        self.tool, self.app, self.directory, self.results, self.only = tool, app, directory, [], only

    def attempt(self, action, route, act, holds, settle=SETTLE, note=None, alone=False, baseline=True):
        """Tries a route. act() does it and returns the last Outcome; holds() says from the app's state whether it took
        effect. Records what became of it. A route with alone has the same end as another of its action, whose success
        would look like its own: it runs in a VM of its own. A route whose check already holds before it runs, as an
        earlier route left the app so, cannot show that it works: it is unmeasured, and runs again alone. baseline=False
        is for a route whose check is not of the app's state."""
        record = {"tool": self.tool.name, "app": self.app, "mode": self.tool.mode, "action": action, "route": route}
        if self.only and self.only != (action, route):
            return False
        if alone and not self.only:
            self.results.append({**record, "result": "alone"})
            return False
        try:
            if baseline and holds():
                record.update(result="unmeasured", why="the app's state met the route's check before it ran")
                self.results.append(record)
                return False
            outcome = act()
            deadline = time.time() + settle
            held = bool(holds())
            while not held and time.time() < deadline:
                time.sleep(0.25)
                held = bool(holds())
            record.update(said=outcome.said[:300] if outcome else None, codes=outcome.codes if outcome else [],
                          seconds=round(outcome.seconds, 2) if outcome else None)
            told_ok = bool(outcome and outcome.ok)
            record["result"] = "works" if held else ("dropped" if told_ok else "refused")
        except Exception as e:  # one route's failure says nothing of the next
            record.update(result="error", why=f"{e!r}", trace=traceback.format_exc()[-600:])
        if note:
            record["note"] = note
        self.results.append(record)
        print(f"  {record['mode']:10} {action:22} {route:26} {record['result']:8} {' '.join(record.get('codes') or [])}",
              flush=True)
        return record["result"] == "works"


# The questionnaire: the app's events, by question number.
def form_values(vm):
    _, out = vm.shell(f"cat {vm.paths['form']}/events.jsonl 2>/dev/null || true")
    values = {}
    for line in out.splitlines():
        with_time = json.loads(line)
        values[with_time["number"]] = with_time["value"]
    return values


def form_submitted(vm):
    return vm.shell(f"test -s {vm.paths['form']}/submissions.jsonl")[0] == 0


def contains(value, wanted):
    return wanted in value if isinstance(value, (list, tuple, set, str, dict)) else value == wanted


def form_case(routes, vm, tool, geometry):
    """Pointer, keys, text and dropdown on the questionnaire's first questions, each read back from the app's events. The
    routes that can leave a drag or a popup behind come last, and a route that did not work runs again alone."""
    g = geometry
    values = lambda: form_values(vm)
    # The first input makes a hidden window redraw at its size, and a click before it can miss.
    tool.click(g["scroll"])
    time.sleep(2)
    routes.attempt("type in field", "click, type", lambda: (tool.click(g["text"]), time.sleep(0.5), tool.text("khello"))[2],
                   lambda: contains(values().get(3, ""), "khello"))
    routes.attempt("click checkbox", "pixels", lambda: tool.click(g["amber"]), lambda: contains(values().get(1, ()), "Amber"))
    routes.attempt("key toggles checkbox", "Tab, space", lambda: (tool.key("Tab"), tool.key("space"))[1],
                   lambda: contains(values().get(1, ()), "Beige"))
    if tool.name == "cua" and tool.mode != "desktop":
        def by_elements():
            """As Cua's skill has an agent do it: the dropdown's own control by its token, then the item's in a new read."""
            toggle = tool.element("combo box") or tool.element("toggle button")
            opened = tool.click_element(toggle)
            time.sleep(1)
            elements = tool.state()
            items = [e for e in elements if e.get("label") == "Saturn"] or \
                [e for e in elements if e.get("role") == "list item" and e.get("frame", {}).get("h")]
            if not items:
                return opened
            return tool.click_element(items[0 if items[0].get("label") == "Saturn" else 6]["element_token"])

        routes.attempt("dropdown", "element tokens", by_elements, lambda: values().get(4) not in (None, "", "Choose…"), alone=True)
        slider = tool.element("slider")
        routes.attempt("set slider", "set_value", lambda: tool.set_value(slider, "37"), lambda: str(values().get(2)) == "37")
        field = tool.element("text box") or tool.element("text")
        routes.attempt("set field", "set_value", lambda: tool.set_value(field, "Northwind 42"),
                       lambda: values().get(3) == "Northwind 42")
        routes.attempt("click checkbox", "run_script", lambda: tool.call("run_script", {"script": (
            f"const app = await cua.getApp({{pid: {tool.pid}}}); await app.click([{tool.px(g['beige'])[0]}, {tool.px(g['beige'])[1]}]);"
        )})[0], lambda: contains(values().get(1, ()), "Beige"))
    routes.attempt("drag slider", "pixels", lambda: tool.drag(g["slider"], g["slider_end"]),
                   lambda: str(values().get(2, "0")) not in ("0", "0.0"))
    def by_keys():
        tool.click(g["dropdown"])
        time.sleep(1)
        for _ in range(6):
            outcome = tool.key("Down")
            if not outcome.ok:
                return outcome
        return tool.key("Return")

    routes.attempt("dropdown", "click, Down keys, Return", by_keys, lambda: values().get(4) not in (None, "", "Choose…"), alone=True)
    routes.attempt("dropdown", "click, click item by pixels",
                   lambda: (tool.click(g["dropdown"]), time.sleep(1), tool.click(g["dropdown_item"]))[2],
                   lambda: values().get(4) not in (None, "", "Choose…"), alone=True)
    before = tool.shot()
    routes.attempt("scroll", "pixels", lambda: tool.scroll(g["scroll"], amount=10), lambda: tool.shot() != before,
                   note="by the screenshot: the app keeps no state of where it scrolled")


FIXTURES = {}


def fixture(name, task, variant, browser="firefox", windows=None):
    """Registers a case's function under a fixture."""
    def register(function):
        FIXTURES[name] = {"task": task, "variant": variant, "browser": browser, "run": function}
        return function
    return register


@fixture("form-gtk4", "form", "25")
def form_gtk4(routes, vm, tool):
    form_case(routes, vm, tool, vm.geometry or GEOMETRY["form:25"])


@fixture("form-gtk3", "form", "gtk3-25")
def form_gtk3(routes, vm, tool):
    form_case(routes, vm, tool, vm.geometry or GEOMETRY["form:gtk3-25"])


@fixture("form-qt", "form", "qt-25")
def form_qt(routes, vm, tool):
    form_case(routes, vm, tool, vm.geometry or GEOMETRY["form:qt-25"])


@fixture("files", "files", None)
def files(routes, vm, tool):
    root = vm.paths["files"]
    exists = lambda path: vm.shell(f"test -e {root}/{path}")[0] == 0
    g = vm.geometry or GEOMETRY["files"]
    tool.click(g["empty"])  # the first input makes a hidden window redraw at its size
    time.sleep(2)

    def rename():
        tool.click(g["report"])
        time.sleep(0.5)
        tool.key("F2")
        time.sleep(0.5)
        tool.key("ctrl", "a")
        tool.text("report-final.txt")
        return tool.key("Return")

    routes.attempt("rename", "click, F2, type, Return", rename, lambda: exists("report-final.txt"), settle=4)

    def move():
        tool.click(g["report"])
        tool.key("ctrl", "x")
        tool.double_click(g["archive"])
        time.sleep(1.5)
        return tool.key("ctrl", "v")

    routes.attempt("move by keys", "click, cut, open, paste", move,
                   lambda: exists("Archive/report-final.txt") or exists("Archive/report-draft.txt"), settle=5)
    tool.key("alt", "Left")
    time.sleep(1.5)
    routes.attempt("drag into folder", "pixels", lambda: tool.drag(g["notes"], g["drafts"]),
                   lambda: exists("Drafts/notes.txt"), settle=4)
    if tool.name == "cua" and tool.mode != "desktop":
        def by_element():
            token = tool.element("grid cell", "budget.csv. File")
            outcome = tool.click_element(token)
            tool.key("F2")
            time.sleep(1)
            field = tool.element("text") or tool.element("text box") or tool.element("entry")
            outcome = tool.set_value(field, "budget-2.csv") if field else outcome
            return tool.key("Return") if outcome.ok else outcome

        routes.attempt("rename", "element token, F2, set_value", by_element, lambda: exists("budget-2.csv"), settle=4)

        def into_folder():
            tool.click_element(tool.element("grid cell", "notes.txt. File"))
            tool.key("ctrl", "x")
            tool.call("double_click", {**tool.target, "element_token": tool.element("grid cell", "Drafts. Folder"),
                                       **tool.delivery})
            time.sleep(1.5)
            return tool.key("ctrl", "v")

        routes.attempt("move by keys", "element tokens, cut, paste", into_folder, lambda: exists("Drafts/notes.txt"), settle=5)


@fixture("inkscape", "inkscape", None)
def inkscape(routes, vm, tool):
    g = vm.geometry or GEOMETRY["inkscape"]
    drawing = vm.paths["drawing"]

    def rect_x():
        _, out = vm.shell(f"python3 -c \"import re;print(re.search(r'<rect[^>]*?\\\\bx=.([0-9.]+)', open('{drawing}').read()).group(1))\"")
        return float(out.strip() or 0)

    tool.click(g["empty"])
    time.sleep(2)
    # A selection leaves nothing in the drawing to check, and each route below selects the rectangle itself, so selecting
    # is no route of its own.

    def x_field(value):
        def act():
            tool.click(g["rect"])
            time.sleep(0.7)
            tool.click(g["x"], count=2)
            tool.key("ctrl", "a")
            tool.text(value)
            tool.key("Tab")
            time.sleep(0.7)
            return tool.key("ctrl", "s")
        return act

    routes.attempt("move by X field, save", "click, type, Ctrl+S", x_field("122"), lambda: abs(rect_x() - 122) < 0.01, settle=4)
    if tool.name == "cua" and tool.mode != "desktop":
        def by_value():
            tool.click(g["rect"])
            time.sleep(1)
            field = next((e for e in tool.state() if e.get("role") == "spin button" and str(e.get("value", "")).startswith("12")), None)
            outcome = tool.set_value(field["element_token"], "130") if field else Outcome(False, said="no X field in the tree")
            time.sleep(0.7)
            return tool.key("ctrl", "s") if outcome.ok else outcome

        routes.attempt("move by X field, save", "element token, set_value, Ctrl+S", by_value, lambda: abs(rect_x() - 130) < 0.01, settle=4)
    # The drag moves the rectangle from wherever the routes before it left it: its check is against that place.
    began = {}

    def drag_on_canvas():
        began["x"] = rect_x()
        tool.drag(g["rect"], (g["rect"][0] + 60, g["rect"][1]))
        time.sleep(1)
        return tool.key("ctrl", "s")

    routes.attempt("drag on canvas, save", "pixels, Ctrl+S", drag_on_canvas,
                   lambda: "x" in began and abs(rect_x() - began["x"]) > 1, settle=4, baseline=False)


def calc_cells(vm):
    _, out = vm.shell(f"tail -n 40 {vm.paths['calc']}/watch.jsonl 2>/dev/null; true")
    cells = {}
    for line in out.splitlines():
        record = json.loads(line)
        cells = record.get("cells") or cells
    return cells


def calc_saved(vm):
    _, out = vm.shell(f"grep -c '\"file\"' {vm.paths['calc']}/watch.jsonl; true")
    return int(out.strip() or 0)


def calc_case(routes, vm, tool):
    """Calc's cells through UNO: a figure, a formula, saved. The window is the sheet's, from the fixture's own record."""
    g = vm.geometry or GEOMETRY["calc"]
    windows = json.loads(vm.shell(f"cat {vm.paths['calc']}/windows.json")[1])
    sheet = type(tool)(vm, windows["calc"], tool.mode)

    def enter(cell, value):
        def act():
            sheet.click(g[cell])
            time.sleep(0.5)
            sheet.text(value)
            return sheet.key("Return")
        return act

    routes.attempt("type a figure in a cell", "click, type, Return", enter("B5", "4711"),
                   lambda: "4711" in json.dumps(calc_cells(vm)), settle=5)
    routes.attempt("type a formula", "click, type, Return", enter("B6", "=B5*2"),
                   lambda: "=B5*2" in json.dumps(calc_cells(vm)), settle=5)
    saved = calc_saved(vm)
    routes.attempt("save", "Ctrl+S", lambda: sheet.key("ctrl", "s"), lambda: calc_saved(vm) > saved, settle=6)
    title = lambda: next((c["title"] for c in json.loads(vm.hypr("-j clients")) if c["address"] == vm.ready["task_window"]), "")
    before = title()
    reveal(tool)
    routes.attempt("key in the browser", "Ctrl+Tab", lambda: tool.key("ctrl", "Tab"), lambda: title() != before)
    routes.attempt("click a tab in the browser", "pixels", lambda: tool.click((340, 22)), lambda: title() != before)
    if tool.name == "cua" and tool.mode != "desktop":
        elements = tool.state()
        text = json.dumps(elements)
        # Reads change nothing in the app, so their checks are of what the read returned, and no baseline applies.
        routes.attempt("read the report tab", "accessibility tree", lambda: Outcome(True), lambda: "revenue" in text.lower(),
                       note="the browser's window, which holds the figures", baseline=False)
        got = {}

        def by_browser_tools():
            got["prepared"] = prepared_browser(tool)
            return got["prepared"][0]

        routes.attempt("read the report tab", "browser tools", by_browser_tools,
                       lambda: bool(got.get("prepared", (None, False))[1]), baseline=False)


def prepared_browser(tool):
    """The browser tools on the user's browser: get_browser_state, and the browser_prepare call it names."""
    _, state = tool.call("get_browser_state", tool.target)
    if not state.get("refusal"):
        return Outcome(True), True
    prepared, structured = tool.call("browser_prepare", {**tool.target, "session": "capability",
                                                         "strategy": {"kind": "existing_profile"}})
    return prepared, not structured.get("refusal")


@fixture("calc-firefox", "calc", None)
def calc_firefox(routes, vm, tool):
    calc_case(routes, vm, tool)


@fixture("calc-chromium", "calc", None, browser="chromium")
def calc_chromium(routes, vm, tool):
    calc_case(routes, vm, tool)


def mail_logged_in(vm):
    return vm.shell("journalctl -u dovecot --no-pager | grep -q 'Logged in: user=<mara>'")[0] == 0


@fixture("thunderbird", "thunderbird", None)
def thunderbird(routes, vm, tool):
    g = vm.geometry or GEOMETRY["thunderbird"]
    tool.click(g["empty"])
    time.sleep(3)
    if tool.name == "cua" and tool.mode != "desktop":
        tree = lambda: json.dumps(tool.state())
        name = tool.element("entry", "Full name*")
        routes.attempt("set the name field", "set_value", lambda: tool.set_value(name, "Mara Probe") if name else Outcome(False, said="no field"),
                       lambda: "Mara Probe" in tree())

    def sign_in():
        tool.click(g["name"])
        time.sleep(0.4)
        tool.key("ctrl", "a")
        tool.text("Mara Lindqvist")
        tool.click(g["email"])
        time.sleep(0.4)
        tool.key("ctrl", "a")
        tool.text("mara@example.test")
        time.sleep(0.5)
        outcome = tool.click(g["continue"])
        if not outcome.ok:
            return outcome
        # The offline lookup of the account's settings takes this long, for every tool.
        time.sleep(140)
        tool.click(g["confirm"])
        time.sleep(5)
        outcome = tool.text("Lindqvist-2026")
        time.sleep(0.5)
        return tool.key("Return") if outcome.ok else outcome

    if g.get("confirm"):
        routes.attempt("add the account", "pixels, type", sign_in, lambda: mail_logged_in(vm), settle=40)
    else:
        # A real machine has no mail server here: what its field holds, read by accessibility or else from the window.
        before = tool.shot()
        holds = (lambda: "Mara Lindqvist" in json.dumps(tool.state())) if tool.name == "cua" else (lambda: tool.shot() != before)
        routes.attempt("type in the name field", "click, type", lambda: (tool.click(g["name"]), time.sleep(0.4), tool.text("Mara Lindqvist"))[2],
                       holds, note=None if tool.name == "cua" else "by the screenshot")


def client_rect(rect, inner, height=1080):
    """A rectangle of the page's layout log, left top right bottom, as its middle in pixels of a window of that height."""
    left, top, right, bottom = rect
    return ((left + right) / 2, (top + bottom) / 2 + (height - inner[1]))


def page_layout(vm, state):
    _, out = vm.shell(f"grep '\"type\": \"layout\"' {state}/events.jsonl | tail -1")
    return json.loads(out)


def board_case(routes, vm, tool):
    """A label dragged from the tray onto the photo, read from the board's saved state."""
    state = vm.paths["guide"]

    def places():
        status, out = vm.shell(f"cat {state}/board.json 2>/dev/null")
        return json.loads(out).get("labels", {}) if status == 0 and out.strip() else {}

    layout = page_layout(vm, state)
    labels = list(layout["labels"])
    inner = layout["inner"]
    photo = client_rect(layout["photo"], inner, vm.height)
    moved = 0
    count = lambda: sum(1 for v in places().values() if v)
    # The labels placed when each drag began: a route works if it placed one more.
    began = {}

    def drag_label(move):
        def act():
            nonlocal moved
            began["placed"] = count()
            label = labels[moved]
            start = client_rect(layout["labels"][label][0], inner, vm.height)
            outcome = move(start, (photo[0] - 60 + 40 * moved, photo[1]))
            moved += 1
            return outcome
        return act

    def placed():
        return count() > began.get("placed", len(labels))

    # Each drag's check is against the count when it began, which the drag itself records: no baseline applies.
    routes.attempt("drag a label", "drag", drag_label(tool.drag), placed, settle=3, baseline=False)
    time.sleep(1)
    if tool.name == "cua":
        def stepped(start, end):
            return tool.drag(start, end, steps=12)
        routes.attempt("drag a label", "drag with steps", drag_label(stepped), placed, settle=3, baseline=False)
    routes.attempt("drag a label", "joined path", drag_label(
        lambda start, end: tool.path([start, ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2), end])), placed, settle=3,
        baseline=False)


def trace_case(routes, vm, tool):
    """A stroke on the figure, read from the drawing's saved state."""
    state = vm.paths["blog"]

    def strokes():
        status, out = vm.shell(f"cat {state}/strokes.json 2>/dev/null")
        return json.loads(out).get("strokes", []) if status == 0 and out.strip() else []

    layout = page_layout(vm, state)
    x, y = client_rect(layout["photo"], layout["inner"], vm.height)
    count = lambda: len(strokes())
    before = count()
    routes.attempt("draw a stroke", "drag", lambda: tool.drag((x - 100, y), (x + 100, y)), lambda: count() > before, settle=3)
    before2 = count()
    curve = [(x - 100 + 20 * i, y + 40 + (i % 4) * 8) for i in range(11)]
    routes.attempt("draw a curve", "path of many points" if tool.name == "hyprmasq" else "joined drags",
                   lambda: tool.path(curve), lambda: count() > before2, settle=3)
    if tool.name == "hyprmasq":
        routes.results[-1]["points"] = len((strokes() or [[]])[-1]) if strokes() else None
    else:
        before3 = count()

        # The desktop mode's calls go to the desktop, as its other routes do, and the others to the window in their mode.
        where = {"target": DESKTOP} if tool.mode == "desktop" else {**tool.target, **tool.delivery}

        def by_buttons():
            px = tool.px((x - 100, y + 120))
            tool.call("mouse_button_down", {**where, "x": px[0], "y": px[1]})
            for i in range(1, 6):
                p = tool.px((x - 100 + 30 * i, y + 120 + 5 * i))
                tool.call("mouse_drag", {**where, "x": p[0], "y": p[1]})
            end = tool.px((x + 50, y + 145))
            return tool.call("mouse_button_up", {**where, "x": end[0], "y": end[1]})[0]

        routes.attempt("draw a curve", "mouse_button_down, drag, up", by_buttons, lambda: count() > before3, settle=3)


for browser in ("firefox", "chromium"):
    for variant in ("dnd", "pointer"):
        FIXTURES[f"board-{variant}-{browser}"] = {"task": "board", "variant": variant, "browser": browser, "run": lambda r, v, t: board_case(r, v, t)}
    for variant in ("river", "rack"):
        FIXTURES[f"trace-{variant}-{browser}"] = {"task": "trace", "variant": variant, "browser": browser, "run": lambda r, v, t: trace_case(r, v, t)}


class Browser:
    """Cua's browser tools on one target: a tab of its isolated browser, or of the user's once attached."""

    def __init__(self, tool):
        self.tool, self.session = tool, "capability"
        self.target_id = self.tab_id = None

    def bind(self, pid, window_id):
        """Binds a browser's window: returns (Outcome, bound)."""
        outcome, state = self.tool.call("get_browser_state", {"pid": pid, "window_id": window_id, "session": self.session})
        self.target_id = state.get("target_id") or (state.get("target") or {}).get("target_id")
        tabs = state.get("tabs") or (state.get("target") or {}).get("tabs") or []
        self.tab_id = state.get("tab_id") or (tabs[0].get("tab_id") if tabs else None)
        return outcome, bool(self.target_id and self.tab_id)

    def isolated(self, with_pid=None):
        """browser_prepare for a browser of Cua's own, with or without the user's browser's pid, and binds its window."""
        args = {"session": self.session, "allow_launch": True, "profile": {"mode": "isolated_new"}}
        if with_pid:
            args["pid"] = with_pid
        outcome, prepared = self.tool.call("browser_prepare", args)
        pid = prepared.get("prepared_pid")
        if not pid:
            return outcome, False
        time.sleep(2)
        listed = self.tool.vm.session.call("list_windows", {})["structuredContent"]["windows"]
        window = next((w for w in listed if w["pid"] == pid), None)
        if not window:
            return Outcome(False, said="no window of the isolated browser"), False
        return self.bind(pid, window["window_id"])

    def where(self):
        return {"target_id": self.target_id, "tab_id": self.tab_id, "session": self.session}

    def navigate(self, url):
        return self.tool.call("browser_navigate", {**self.where(), "url": url})[0]

    def drag(self, start, end, foreground=True):
        return self.tool.call("browser_pointer", {
            **self.where(), "action": "drag", "x": start[0], "y": start[1], "to_x": end[0], "to_y": end[1],
            "delivery_mode": "foreground" if foreground else "background"})[0]

    def snapshot(self):
        _, state = self.tool.call("get_browser_state", {**self.where(), "snapshot_format": "semantic_v2"})
        return state


def browser_case(routes, vm, tool, url, state, page):
    """Cua's browser tools: both forms of its isolated browser and the attachment to the user's, whichever works going on
    to the page's own actions."""
    user_pid = tool.pid

    def isolated(with_pid):
        def act():
            browser = Browser(tool)
            outcome, bound = browser.isolated(with_pid)
            routes.browser = browser if bound else None
            return outcome if not bound else browser.navigate(url)
        return act

    def attach():
        return tool.call("browser_prepare", {"pid": tool.pid, "window_id": tool.window_id, "session": "attach",
                                             "strategy": {"kind": "existing_profile"}, "delivery_mode": "foreground"})
    routes.browser = None
    attached = {}

    def do_attach():
        outcome, prepared = attach()
        attached["ok"] = bool(prepared.get("prepared_pid") or prepared.get("target_id") or prepared.get("ok")) and not prepared.get("refusal")
        return outcome

    if routes.only and routes.only[0] == "drag a label":
        # A drag that runs alone needs a browser of the routes below: the first that binds, not recorded.
        isolated(user_pid)()
        if not routes.browser:
            isolated(None)()
        return routes.browser
    routes.attempt("attach to the user's browser", "existing_profile", do_attach, lambda: attached.get("ok"))
    routes.attempt("isolated browser", "allow_launch, with pid", isolated(user_pid),
                   lambda: routes.browser is not None and page_seen(vm, state), settle=8)
    if routes.browser:
        vm.shell("true")
    routes.attempt("isolated browser", "allow_launch, no pid", isolated(None),
                   lambda: routes.browser is not None and page_seen(vm, state), settle=8)
    return routes.browser


def label_drag(routes, vm, state, route, act):
    """A drag of a label in a browser's page, which works if it placed one more label than were placed when it began."""
    began = {}

    def count():
        status, out = vm.shell(f"cat {state}/board.json 2>/dev/null")
        return sum(1 for v in json.loads(out or "{}").get("labels", {}).values() if v) if status == 0 else 0

    def go():
        began["placed"] = count()
        return act()

    routes.attempt("drag a label", route, go, lambda: "placed" in began and count() > began["placed"], settle=3,
                   baseline=False)


def page_seen(vm, state):
    """Whether a second page load of the fixture reached its server after the setup's own."""
    _, out = vm.shell(f"grep -c '\"type\": \"layout\"' {state}/events.jsonl; true")
    return int(out.strip() or 0) > 1


def isolated_board(routes, vm, tool):
    state = "/var/lib/hyprmasq-bench-guide"
    browser = browser_case(routes, vm, tool, "http://guide.example.test/figures/motherboard", state, "board")
    if not browser:
        return
    time.sleep(2)
    layout = page_layout(vm, state)
    labels = list(layout["labels"])

    def rect(label):
        left, top, right, bottom = layout["labels"][label][0]
        return ((left + right) / 2, (top + bottom) / 2)

    left, top, right, bottom = layout["photo"]
    target = ((left + right) / 2, (top + bottom) / 2)

    label_drag(routes, vm, state, "browser_pointer, background", lambda: browser.drag(rect(labels[0]), target, False))
    label_drag(routes, vm, state, "browser_pointer, foreground", lambda: browser.drag(rect(labels[1]), target, True))


def attached_board(routes, vm, tool):
    """The user's browser with remote debugging on from its start, as Cua's refusal on Linux suggests: browser_prepare then
    has an endpoint to find, with no setup page to open."""
    state = vm.paths["guide"]
    browser = Browser(tool)
    browser.session = "attach"
    bound = {}

    def attach():
        outcome, prepared = tool.call("browser_prepare", {**tool.target, "session": "attach",
                                                          "strategy": {"kind": "existing_profile"}})
        bound["ok"] = not prepared.get("refusal")
        if bound["ok"]:
            bound["ok"] = browser.bind(tool.pid, tool.window_id)[1]
        return outcome

    if routes.only and routes.only[0] == "drag a label":
        # A drag that runs alone needs the attachment, done first and not recorded as a route of its own.
        attach()
    else:
        routes.attempt("attach to the user's browser", "existing_profile, debugging on", attach, lambda: bound.get("ok"))
    if not bound.get("ok"):
        return
    layout = page_layout(vm, state)
    labels = list(layout["labels"])

    def rect(label):
        left, top, right, bottom = layout["labels"][label][0]
        return ((left + right) / 2, (top + bottom) / 2)

    left, top, right, bottom = layout["photo"]
    target = ((left + right) / 2, (top + bottom) / 2)

    label_drag(routes, vm, state, "browser_pointer, background", lambda: browser.drag(rect(labels[0]), target, False))
    label_drag(routes, vm, state, "browser_pointer, foreground", lambda: browser.drag(rect(labels[1]), target, True))


FIXTURES["attach-debugging-board-pointer"] = {"task": "board", "variant": "pointer", "browser": "chromium",
                                              "run": attached_board, "modes": ("browser",),
                                              "env": {"HYPRMASQ_BENCH_CHROMIUM_OPTIONS": "--remote-debugging-port=0"}}

for browser_name in ("chromium",):
    for variant in ("dnd", "pointer"):
        FIXTURES[f"isolated-board-{variant}"] = {"task": "board", "variant": variant, "browser": browser_name,
                                                 "run": isolated_board, "modes": ("browser",)}


def forgejo_signed_in(vm):
    return vm.shell("journalctl -u forgejo --no-pager | grep -E 'POST /user/login.*303' -q")[0] == 0


def forgejo_case(routes, vm, tool):
    """The sign-in in the agent's window while the user types in theirs, read from Forgejo's log, and what became of the
    user's notes. The rest of the task, its file upload and OAuth, is left to the paid proof runs."""
    g = GEOMETRY["forgejo"]
    tool.click(g["empty"])
    time.sleep(3)

    def retried(act):
        """An action again while the user's keys hold it back, as Cua's refusal says, up to half a minute."""
        for _ in range(15):
            outcome = act()
            if outcome.ok or "foreground_physical_keys" not in outcome.codes:
                return outcome
            time.sleep(2)
        return outcome

    def sign_in():
        outcome = retried(lambda: tool.click(g["sign_in_link"]))
        if not outcome.ok:
            return outcome
        time.sleep(3)
        outcome = retried(lambda: tool.text("mara"))
        if not outcome.ok:
            return outcome
        time.sleep(0.3)
        retried(lambda: tool.key("Tab"))
        time.sleep(0.3)
        outcome = retried(lambda: tool.text("Forgejo-Mara-2026"))
        return retried(lambda: tool.key("Return")) if outcome.ok else outcome

    routes.attempt("sign in", "pixels, type", sign_in, lambda: forgejo_signed_in(vm), settle=10)
    _, saved = vm.shell("cat /var/lib/hyprmasq-bench-notes/notes.json 2>/dev/null; true")
    notes = json.loads(saved).get("text", "") if saved.strip() else ""
    routes.results.append({"tool": tool.name, "app": routes.app, "mode": tool.mode, "action": "the user's notes",
                           "route": "", "result": "read", "saved_chars": len(notes), "saved": notes[-200:]})


FIXTURES["forgejo-typing"] = {"task": "forgejo", "variant": None, "browser": "firefox", "run": forgejo_case}
FIXTURES["forgejo-reacting"] = {"task": "forgejo", "variant": "reacting", "browser": "firefox", "run": forgejo_case}


def as_user(script):
    """A script as the VM's user, in its session, for what a shell of the VM's root would not find."""
    env = ('XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus XDG_CURRENT_DESKTOP=Hyprland '
           'XDG_SESSION_TYPE=wayland HYPRLAND_INSTANCE_SIGNATURE=$(cat /tmp/hyprmasq-bench-instance) WAYLAND_DISPLAY=$(cat /tmp/hyprmasq-bench-display)')
    return f"su - alice -c {shlex.quote('export ' + env + '; ' + script)}"


def only_tag(only):
    return hashlib.sha1(json.dumps(only).encode()).hexdigest()[:8]


def load_files_start():
    """The file manager task's starting folder, from its checker: {path: contents, or None for a folder}."""
    import runpy
    return runpy.run_path(os.path.join(BENCH, "tasks", "files", "check.py"))["START"]


def save_shot(vm, window, path):
    """hyprmasq's screenshot of a window, to read a fixture's geometry from."""
    result = vm.session.call("screenshot", {"window": window})
    image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
    if image:
        with open(path, "wb") as f:
            f.write(base64.b64decode(image["data"]))


# What a desktop offers the tools, set beside the VM's: the commands they call, the session they find, the portals, the
# accessibility bus and how it runs, the compositor's input and its binds, and what runs.
ENVIRONMENT = {
    "commands": "for c in wtype grim slurp wl-copy wl-paste xdg-open gdbus busctl gsettings import ffprobe readelf ydotool "
                "wf-recorder hyprpicker notify-send jq; do printf '%s ' \"$c=$(command -v $c >/dev/null && echo yes || echo no)\"; done",
    "portals": "systemctl --user is-active xdg-desktop-portal xdg-desktop-portal-hyprland xdg-desktop-portal-gtk | tr '\\n' ' '",
    "a11y_status": "busctl --user get-property org.a11y.Bus /org/a11y/bus org.a11y.Status IsEnabled",
    "a11y_bus_process": "ps -eo args | grep -E 'dbus-(broker|daemon).*at-spi|at-spi2-registryd|at-spi-bus-launcher' | grep -v grep | sed 's#/nix/store/[^/]*/#/#' | cut -c1-90",
    "session_bus_process": "ps -eo args | grep -E 'dbus-(broker-launch|daemon).*--(scope user|session)' | grep -v grep | sed 's#/nix/store/[^/]*/#/#' | head -1 | cut -c1-90",
    "versions": "hyprctl version | head -1 | cut -c1-60; for p in at-spi2-core gtk4 gtk3 qt6-base firefox chromium thunderbird libreoffice-fresh inkscape nautilus xdg-desktop-portal xdg-desktop-portal-hyprland; do pacman -Q $p 2>/dev/null; done; firefox --version 2>/dev/null; chromium --version 2>/dev/null; thunderbird --version 2>/dev/null; inkscape --version 2>/dev/null | head -1; soffice --version 2>/dev/null | head -1; nautilus --version 2>/dev/null",
    "monitors": "hyprctl -j monitors | python3 -c \"import json,sys;print([(m['name'],m['width'],m['height'],m['scale']) for m in json.load(sys.stdin)])\"",
    "keyboard": "hyprctl -j devices | python3 -c \"import json,sys;print([(k['name'][:24],k['layout'],k['options'],k['active_keymap'],k['numLock'],k['capsLock']) for k in json.load(sys.stdin)['keyboards']][:4])\"",
    "binds": "hyprctl -j binds | python3 -c \"import json,sys;b=json.load(sys.stdin);print(len(b),sorted({x['description'] for x in b if 'orkspace' in x.get('description','') or 'window' in x.get('description','')})[:12])\"",
    "options": "for o in input:follow_mouse input:numlock_by_default general:gaps_out animations:enabled decoration:blur:enabled; do printf '%s=' $o; hyprctl -j getoption $o | python3 -c \"import json,sys;d=json.load(sys.stdin);print(d.get('int',d.get('custom',d.get('str'))),end=' ')\"; done",
    "cua_status": "hyprctl -j cua:status 2>/dev/null | head -c 1200",
    "session": "loginctl show-session $XDG_SESSION_ID -p Type -p Class -p Active 2>/dev/null | tr '\\n' ' '; echo $XDG_CURRENT_DESKTOP $XDG_SESSION_TYPE $WAYLAND_DISPLAY",
    "cpu": "lscpu | grep 'Model name' | cut -d: -f2 | xargs; nproc",
}


def environment(shell):
    """Runs ENVIRONMENT's commands through shell(script), which returns a CompletedProcess or (status, output)."""
    found = {}
    for key, script in ENVIRONMENT.items():
        done = shell(script)
        found[key] = (done.stdout + done.stderr if hasattr(done, "stdout") else done[1]).strip()
    return found


def walk_case(routes, vm, tool, rounds=20):
    """Cua's accessibility walks of the window, hidden and in view, at a budget they do not reach: what the VM costs."""
    walks = {}

    def measure(place, elements=None):
        times, counts = [], []
        for _ in range(rounds):
            result = vm.session.call("get_window_state", {**tool.target, "timeout_ms": 10000, "include_screenshot": False})
            text = result_text(result)
            found = report.WALK_MS.findall(text)
            if found:
                times.append(int(found[0]))
                counts.append((result.get("structuredContent") or {}).get("element_count") or 0)
        ordered = sorted(times)
        walks[place] = {"count": len(times), "median_ms": ordered[len(ordered) // 2] if ordered else None,
                        "p90_ms": ordered[int(0.9 * len(ordered))] if ordered else None,
                        "elements": sorted(counts)[len(counts) // 2] if counts else None}

    tool.click(GEOMETRY_NUDGE)
    time.sleep(2)
    measure("hidden")
    place = next(c for c in json.loads(vm.hypr("-j clients")) if c["address"] == vm.ready["task_window"])
    tool.call("hotkey", {"target": DESKTOP, "keys": ["super", place["workspace"]["name"]]})
    time.sleep(2)
    measure("in view")
    routes.results.append({"tool": tool.name, "app": routes.app, "mode": "walks", "action": "accessibility walk", "route": "",
                           "result": "read", "walks": walks})
    print(f"  walks {routes.app}: {walks}", flush=True)


GEOMETRY_NUDGE = (1000, 500)
# A page that changes by itself, as a clock: what a background change in a hidden browser window is. A file, as Firefox
# opens no data: page from its command line.
CLOCK_PAGE = ("<title>clock</title><body style='font:160px monospace'><script>"
              "setInterval(()=>document.body.textContent=new Date().toISOString().slice(11,22),250)</script>")
CLOCK_FILE = "/tmp/hyprmasq-bench-clock.html"
FRESH_WORKSPACE = "8"
FRESH_SECONDS = 3


def spawn_user(vm, command):
    """Starts a program in the desktop's session, as its user, and returns at once."""
    script = f"nohup {command} > /dev/null 2>&1 &"
    vm.shell(as_user(script) if isinstance(vm, Vm) else script)


def running_args(vm, name):
    """The command line of the fixture's running browser, to open another window of it: the VM's user's, or on a real
    machine the one the check started, with its own profile, and never the machine's user's own browser."""
    lines = [line.split() for line in vm.shell("ps -eo args=")[1].splitlines() if line.strip()]
    mine = [args[1:] for args in lines if name in os.path.basename(args[0]) and not any(a.startswith("--type=") for a in args)
            and any("bench-capability" in a or "/home/alice" in a for a in args)]
    return mine[0] if mine else []


def fresh_case(routes, vm, tool, kind):
    """Whether the tool's screenshot of a hidden window shows a change that the window's app made by itself: a terminal
    that prints the time, and in a browser's fixture a window of that browser with a page that shows it. Each window is
    moved to a hidden workspace; the tool takes a screenshot, and another FRESH_SECONDS later. They differ when the tool
    shows the window as it is; the same image twice is a stale frame. The same for every tool."""
    windows = [("terminal", "fresh-clock",
                "foot -T fresh-clock sh -c 'while :; do date +%T.%N; sleep 0.25; done'")]
    browser = next((b for b in ("firefox", "chromium") if routes.app.endswith(b)), None)
    if browser:
        args = running_args(vm, browser)
        profile = next((a for i, a in enumerate(args) if args[i - 1] == "--profile"), None) if browser == "firefox" else \
            next((a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir=")), None)
        if profile:
            same = f"--profile {profile}" if browser == "firefox" else f"--user-data-dir={profile}"
            page = f"printf %s {shlex.quote(CLOCK_PAGE)} > {CLOCK_FILE} && chmod 644 {CLOCK_FILE}"
            vm.shell(as_user(page) if isinstance(vm, Vm) else page)
            windows.append((browser, "clock", f"{browser} {same} --new-window file://{CLOCK_FILE}"))
        else:
            routes.results.append({"tool": tool.name, "app": routes.app, "mode": "fresh", "action": FRESH_ACTION,
                                   "route": browser, "result": "error", "why": "the fixture's browser was not found"})
    for name, title, command in windows:
        before = {c["address"] for c in json.loads(vm.hypr("-j clients"))}
        spawn_user(vm, command)
        deadline, window = time.time() + 60, None
        while time.time() < deadline and not window:
            window = next((c for c in json.loads(vm.hypr("-j clients"))
                           if c["address"] not in before and title in c["title"]), None)
            time.sleep(0.5)
        if not window:
            routes.results.append({"tool": tool.name, "app": routes.app, "mode": "fresh", "action": FRESH_ACTION,
                                   "route": name, "result": "error", "why": f"no {name} window"})
            continue
        vm.hypr("dispatch " + shlex.quote(f'hl.dsp.window.move({{ window = "address:{window["address"]}", '
                                          f'workspace = "{FRESH_WORKSPACE}", follow = false }})'))
        time.sleep(2)
        start = time.time()
        try:
            shooter = kind(vm, window["address"], "background")
            first = shooter.shot()
            time.sleep(FRESH_SECONDS)
            second = shooter.shot()
            result = "works" if first and second and first != second else ("dropped" if first else "error")
        except Exception as e:  # the route, not the case, says it
            result, first = "error", repr(e)
        finally:
            # The clock's terminal is a process of its own, which no fixture ends. A browser's clock is a window of the
            # fixture's browser, which ends with it.
            if name == "terminal" and window.get("pid"):
                vm.shell(f"kill {int(window['pid'])}")
        routes.results.append({"tool": tool.name, "app": routes.app, "mode": "fresh", "action": FRESH_ACTION,
                               "route": name, "result": result, "seconds": round(time.time() - start, 1)})
        print(f"  fresh {routes.app} {name}: {result}", flush=True)


FRESH_ACTION = "a screenshot shows a background change in a hidden window"


def run_case(name, tool_name, mode, args, only=None):
    """Boots a fresh VM of the case's fixture and runs its routes, in that mode, or just the one route in only."""
    spec = FIXTURES[name]
    tag = "" if not only else "-alone-" + only_tag(only)
    directory = os.path.abspath(os.path.join(args.out, f"{tool_name}-{mode}-{name}{tag}"))
    done = os.path.join(directory, "capability.json")
    if os.path.exists(done):
        print(f"{tool_name} {mode} {name}{tag}: done before", flush=True)
        with open(done) as f:
            return json.load(f)["routes"]
    os.makedirs(os.path.join(directory, "tmp"), exist_ok=True)
    os.makedirs(os.path.join(directory, "driver-out"), exist_ok=True)
    print(f"{tool_name} {mode} {name}{tag}: building", flush=True)
    driver = run.build_driver(directory, args.source, spec["task"], spec["variant"], DISPLAY, tool_name, spec["browser"],
                              "omarchy")
    cpus = run.slot_cpus(run.read_cores(), 1, args.cores)[0] if args.cores else None
    print(f"{tool_name} {mode} {name}{tag}: booting", flush=True)
    with run.vm_lock(args.lock):
        with run.booted_vm(driver, directory, args.cid, 3600, cpus, {"HYPRMASQ_BENCH_SHELL": "1", **spec.get("env", {})}):
            vm = Vm(directory, args.cid, "hyprmasq-bench-capability")
            kind = {"cua": Cua, "hyprmasq": Hyprmasq}[tool_name] if mode != "desktop" else Desktop
            mode_of_tool = "background" if mode in ("browser", "walks", "environment", "fresh") else mode
            routes = None
            try:
                tool = kind(vm, vm.ready["task_window"], mode_of_tool)
                routes = Routes(tool, name, directory, tuple(only) if only else None)
                if mode == "walks":
                    walk_case(routes, vm, tool)
                elif mode == "fresh":
                    fresh_case(routes, vm, tool, {"cua": Cua, "hyprmasq": Hyprmasq}[tool_name])
                elif mode == "environment":
                    routes.results.append({"tool": tool_name, "app": name, "mode": mode, "action": "the desktop", "route": "",
                                           "result": "read", "environment": environment(lambda script: vm.shell(as_user(script)))})
                else:
                    spec["run"](routes, vm, tool)
            except Exception as e:  # the case stops, and says why
                print(f"  {mode} {name}: stopped: {e!r}", flush=True)
                if routes:
                    routes.results.append({"tool": tool_name, "app": name, "mode": mode, "action": "(the rest of the case)",
                                           "route": "", "result": "error", "why": repr(e)})
                else:
                    routes = Routes(None, name, directory)
                    routes.results.append({"tool": tool_name, "app": name, "mode": mode, "action": "(the case)", "route": "",
                                           "result": "error", "why": repr(e)})
            finally:
                with open(done, "w") as f:
                    json.dump({"case": name, "tool": tool_name, "mode": mode, "task": spec["task"], "only": only,
                               "variant": spec["variant"], "browser": spec["browser"],
                               "versions": vm.ready.get("versions"), "routes": routes.results if routes else []}, f, indent=1)
                vm.session.close()
    return routes.results


def run_all(name, tool_name, mode, args):
    """A case's routes, then each that did not work again alone, in a VM of its own: a route that an earlier one spoilt
    is not a route that does not work. A VM that did not boot is said and not recorded: running again tries it again."""
    try:
        for record in run_case(name, tool_name, mode, args):
            if record["result"] != "works" and record["route"]:
                run_case(name, tool_name, mode, args, only=[record["action"], record["route"]])
    except RuntimeError as e:
        print(f"{tool_name} {mode} {name}: {e}", flush=True)


def table(directories):
    """The capability table of results directories, as Markdown: a row per app, action and route, and a column per tool and
    mode, each cell what became of the route, with the codes that say why. A route that ran alone says more than one
    that followed others, so that one counts."""
    best = {}
    for directory in directories:
        for name in sorted(os.listdir(directory)):
            path = os.path.join(directory, name, "capability.json")
            if not os.path.exists(path):
                continue
            with open(path) as f:
                result = json.load(f)
            for r in result["routes"]:
                if not r.get("route"):
                    continue
                key = (r["tool"], r["mode"], r["app"], r["action"], r["route"])
                if key not in best or result.get("only"):
                    best[key] = r
    columns = [("hyprmasq", "background"), ("cua", "background"), ("cua", "foreground"), ("cua", "desktop")]
    headers = {("hyprmasq", "background"): "hyprmasq", ("cua", "background"): "Cua background", ("cua", "foreground"): "Cua foreground",
               ("cua", "desktop"): "Cua desktop"}
    rows = {}
    # A code that only says the route is closed says less than the one beside it.
    plain = {"background_unavailable", "foreground_unavailable", "primary_target_busy"}
    for (tool, mode, app, action, route), r in best.items():
        codes = [c for c in r.get("codes") or [] if c not in plain] or (r.get("codes") or [])[:1]
        cell = r["result"] + (" " + " ".join(f"`{c}`" for c in codes) if codes and r["result"] != "works" else "")
        rows.setdefault((app, action, route), {})[(tool, mode)] = cell
    lines = ["| App | Action | Route | " + " | ".join(headers[c] for c in columns) + " |",
             "| --- | --- | --- | " + " | ".join("---" for _ in columns) + " |"]
    for (app, action, route) in sorted(rows):
        lines.append(f"| {app} | {action} | {route} | " + " | ".join(rows[(app, action, route)].get(c, "") for c in columns) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", choices=("hyprmasq", "cua"))
    parser.add_argument("--mode", action="append", choices=("background", "foreground", "desktop", "browser", "walks", "environment",
                                                            "fresh"))
    parser.add_argument("--case", action="append")
    parser.add_argument("--source")
    parser.add_argument("--out", default="capability")
    parser.add_argument("--cid", type=int, default=4442)
    parser.add_argument("--cores", type=int)
    parser.add_argument("--lock")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--table", nargs="*")
    args = parser.parse_args()
    if args.list:
        print("\n".join(FIXTURES))
        return
    if args.table is not None:
        sys.stdout.write(table(args.table))
        return
    if not args.tool:
        parser.error("--tool is needed")
    args.source = os.path.abspath(args.source) if args.source else None
    modes = args.mode or (["background"] if args.tool == "hyprmasq" else ["background", "foreground", "desktop", "browser"])
    for case in args.case or FIXTURES:
        for mode in modes:
            # A case that names its modes runs in those alone, and the browser tools' own run only in their mode.
            if mode not in (FIXTURES[case].get("modes") or ("background", "foreground", "desktop", "walks", "environment",
                                                             "fresh")):
                continue
            run_all(case, args.tool, mode, args)


if __name__ == "__main__":
    main()
