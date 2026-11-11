#!/usr/bin/env python3
"""capability.py's routes on a real desktop, in the session of the machine it runs on, and what both report of the desktop.

    capability_host.py --tool hyprmasq|cua --server COMMAND [--case NAME]... [--mode MODE]... --out DIR [--geometry FILE]
    capability_host.py --shots --server COMMAND --case NAME... --out DIR
    capability_host.py --environment --out DIR

Run it on the machine, over SSH or from a terminal, with Cua's plugin or hyprmasq's loaded: it starts each fixture's app
itself, the same app and page as in the VM, moves its window to a hidden numbered workspace, and leaves the user's
terminal in view with the focus, as vm.py does. COMMAND is the tool's MCP server. The routes and what they count as are
capability.py's. The machine's layout differs from the VM's: --shots saves each fixture's screenshot, from which
--geometry FILE, JSON of the same keys as capability.py's GEOMETRY in the window's logical pixels, gives the routes their
pixels. The Thunderbird fixture has no mail server here, and Forgejo no server at all: they stay the VM's.

--environment writes what the machine offers the tools (bench/capability.py environment), for setting it beside the VM's.
"""

import argparse
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

import capability
import mcp_probe

BENCH = os.path.dirname(os.path.abspath(__file__))
TASKS = os.path.join(BENCH, "tasks")
QT_PYTHON = os.environ.get("QT_PYTHON", os.path.expanduser("~/bench/qt/bin/python3"))
PARKED = "8"
PORT = 18420


def session_env():
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    instance = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or sorted(os.listdir(f"{runtime}/hypr"))[-1]
    lock = open(f"{runtime}/hypr/{instance}/hyprland.lock").read().splitlines()
    env = dict(os.environ, XDG_RUNTIME_DIR=runtime, HYPRLAND_INSTANCE_SIGNATURE=instance, WAYLAND_DISPLAY=lock[1],
               DBUS_SESSION_BUS_ADDRESS=f"unix:path={runtime}/bus", XDG_CURRENT_DESKTOP="Hyprland",
               XDG_SESSION_TYPE="wayland", CUA_DRIVER_RS_ENABLE_WAYLAND="1", CUA_DRIVER_RS_TELEMETRY_ENABLED="false")
    return env


class Host:
    """The machine as capability.Vm is the VM: a session with the tool's server, and a shell."""

    def __init__(self, server, directory, env):
        self.directory, self.env = directory, env
        self.session = mcp_probe.Session(None, os.path.join(directory, "capability-mcp.jsonl"), "hyprmasq-bench-capability",
                                         command=shlex.split(server), env=env)
        self.ready = {}
        self.height = 864
        self.paths = {}
        self.geometry = {}

    def shell(self, script, timeout=120):
        done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=self.env, timeout=timeout, check=False)
        return done.returncode, done.stdout + done.stderr

    def hypr(self, command):
        return self.shell(f"hyprctl {command}")[1]

    def focused(self):
        return json.loads(self.hypr("-j activewindow")).get("address")

    def workspace(self):
        return json.loads(self.hypr("-j activeworkspace")).get("id")

    def clients(self):
        return json.loads(self.hypr("-j clients"))

    def dispatch(self, lua):
        return self.hypr("dispatch " + shlex.quote(lua))

    def wait_window(self, predicate, timeout=120, exclude=()):
        """A window that matches, other than those in exclude: on a real desktop, the user's own."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = next((c for c in self.clients() if c["address"] not in exclude and predicate(c)), None)
            if found:
                return found
            time.sleep(0.5)
        raise TimeoutError("the fixture's window did not come")

    def park(self, address, workspace=PARKED):
        self.dispatch(f'hl.dsp.window.move({{ window = "address:{address}", workspace = "{workspace}", follow = false }})')


class Fixture:
    """Starts a fixture's app on the machine, as vm.py and the task's setup.py do in the VM, and ends it."""

    def __init__(self, name, host, spec):
        self.name, self.host, self.spec, self.processes = name, host, spec, []
        self.state = tempfile.mkdtemp(prefix=f"bench-capability-{name}-")
        self.window = None
        self.before = set()

    def spawn(self, command, **options):
        process = subprocess.Popen(command, env={**self.host.env, **options.pop("env", {})}, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
                                   cwd=self.state, **options)
        self.processes.append(process)
        return process

    def user_terminal(self):
        host = self.host
        host.dispatch('hl.dsp.focus({ workspace = "1" })')
        if not next((c for c in host.clients() if c["title"] == "user-terminal"), None):
            self.spawn(["foot", "--app-id", "user-terminal", "--title", "user-terminal", "sleep", "infinity"])
            host.wait_window(lambda c: c["title"] == "user-terminal")
        return next(c for c in host.clients() if c["title"] == "user-terminal")["address"]

    def start(self):
        terminal = self.user_terminal()
        host, name = self.host, self.name
        state = self.state
        # The windows already open, the user's among them, which no route may touch.
        self.before = {c["address"] for c in host.clients()}
        if name.startswith("form-"):
            directory = "/tmp/hyprmasq-bench-form"
            shutil.rmtree(directory, ignore_errors=True)
            os.makedirs(directory)
            host.paths["form"] = directory
            command = {"form-gtk4": ["python3", f"{TASKS}/form/form.py"], "form-gtk3": ["python3", f"{TASKS}/form/form_gtk3.py"],
                       "form-qt": [QT_PYTHON, f"{TASKS}/form/form_qt.py"]}[name]
            self.spawn([*command, directory, "25"])
            window = host.wait_window(lambda c: c["title"] == "Questionnaire", exclude=self.before)
        elif name == "files":
            root = os.path.join(state, "Project")
            start = capability.load_files_start()
            for path, contents in start.items():
                full = os.path.join(root, path)
                if contents is None:
                    os.makedirs(full, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(full), exist_ok=True)
                    with open(full, "w") as f:
                        f.write(contents)
            host.paths["files"] = root
            self.spawn(["nautilus", "--new-window", root])
            window = host.wait_window(lambda c: c["class"] == "org.gnome.Nautilus", exclude=self.before)
        elif name == "inkscape":
            drawing = os.path.join(state, "drawing.svg")
            shutil.copy(f"{TASKS}/inkscape/drawing.svg", drawing)
            host.paths["drawing"] = drawing
            self.spawn(["inkscape", drawing])
            window = host.wait_window(lambda c: "drawing.svg" in c["title"], timeout=180, exclude=self.before)
        elif name.startswith("board-") or name.startswith("trace-"):
            kind, variant, browser = name.split("-")
            url = self.serve_page(kind, variant)
            window = self.browser(browser, url)
        elif name.startswith("calc-"):
            window = self.calc(name.split("-")[1])
        elif name == "thunderbird":
            os.makedirs(os.path.join(state, "profile"))
            self.spawn(["thunderbird", "--profile", os.path.join(state, "profile")])
            window = host.wait_window(lambda c: c["class"].lower().startswith("thunderbird") or "thunderbird" in c["title"].lower(),
                                      timeout=180, exclude=self.before)
        else:
            raise ValueError(f"no fixture {name} on a real machine")
        host.park(window["address"])
        host.dispatch(f'hl.dsp.focus({{ window = "address:{terminal}" }})')
        time.sleep(5)
        host.ready = {"task_window": window["address"], "versions": {"hyprland": json.loads(host.hypr("-j version")).get("version")}}
        host.height = next(c["size"][1] for c in host.clients() if c["address"] == window["address"])
        self.window = window["address"]
        return window["address"]

    def serve_page(self, kind, variant):
        host = self.host
        directory = os.path.join(self.state, "server")
        os.makedirs(directory)
        env = {"PORT": str(PORT)}
        if kind == "board":
            self.spawn(["python3", f"{TASKS}/board/guide.py", directory, variant], env=env)
            host.paths["guide"] = directory
            page = "figures/motherboard"
        else:
            self.spawn(["python3", f"{TASKS}/trace/figure.py", directory, f"{TASKS}/trace/{variant}"], env=env)
            host.paths["blog"] = directory
            with open(f"{TASKS}/trace/{variant}/figure.json") as f:
                page = json.load(f)["path"].lstrip("/")
        time.sleep(1)
        return f"http://127.0.0.1:{PORT}/{page}"

    def browser(self, browser, *urls):
        host = self.host
        profile = os.path.join(self.state, "browser")
        os.makedirs(profile)
        if browser == "firefox":
            self.spawn(["firefox", "--profile", profile, "--new-instance", *urls], env={"MOZ_ENABLE_WAYLAND": "1"})
            return host.wait_window(lambda c: c["class"] == "firefox", timeout=120, exclude=self.before)
        self.spawn(["chromium", f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
                    "--password-store=basic", "--ozone-platform=wayland", "--force-renderer-accessibility", *urls])
        return host.wait_window(lambda c: c["class"].startswith("chromium"), timeout=120, exclude=self.before)

    def calc(self, browser):
        host = self.host
        directory = os.path.join(self.state, "calc-state")
        os.makedirs(directory)
        sheet = os.path.join(self.state, "sheet.ods")
        subprocess.run(["python3", f"{TASKS}/calc/sheet.py", sheet], env=host.env, check=False, cwd=self.state)
        pipe = f"bench-capability-{os.getpid()}"
        self.spawn(["soffice", "--nologo", f"--accept=pipe,name={pipe};urp;", sheet])
        window = host.wait_window(lambda c: "sheet.ods" in c["title"] or "LibreOffice" in c["title"], timeout=180,
                                  exclude=self.before)
        self.spawn(["python3", f"{TASKS}/calc/watch.py", pipe, sheet, os.path.join(directory, "watch.jsonl")])
        host.paths["calc"] = directory
        host.park(window["address"], "9")
        web = self.browser(browser, *self.intranet(directory))
        with open(os.path.join(directory, "windows.json"), "w") as f:
            json.dump({"browser": web["address"], "calc": window["address"]}, f)
        return web

    def intranet(self, directory):
        """The offices' reports, as the VM's intranet serves them, on a port of the fixture's own, signed in as Mara: the
        URLs of her five tabs."""
        state = os.path.join(directory, "intranet")
        os.makedirs(state)
        open(os.path.join(state, "sign-in-open"), "w").close()
        self.spawn(["python3", f"{TASKS}/calc/intranet.py", state], env={"PORT": str(PORT + 1)})
        base = f"http://127.0.0.1:{PORT + 1}"
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen(base + "/", timeout=2).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise TimeoutError("the intranet did not answer")
        sys.path.insert(0, f"{TASKS}/calc")
        import figures
        return [base + path for path, _ in figures.TABS]

    def stop(self):
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        time.sleep(2)
        shutil.rmtree(self.state, ignore_errors=True)


def run_host_case(name, tool_name, mode, args, env, geometry, only=None):
    spec = capability.FIXTURES[name]
    tag = "" if not only else "-alone-" + capability.only_tag(only)
    directory = os.path.abspath(os.path.join(args.out, f"{tool_name}-{mode}-{name}{tag}"))
    done = os.path.join(directory, "capability.json")
    if os.path.exists(done):
        with open(done) as f:
            return json.load(f)["routes"]
    os.makedirs(directory, exist_ok=True)
    host = Host(args.server, directory, env)
    host.geometry = geometry.get(name, {})
    fixture = Fixture(name, host, spec)
    routes = None
    print(f"{tool_name} {mode} {name}{tag}", flush=True)
    try:
        window = fixture.start()
        kind = {"cua": capability.Cua, "hyprmasq": capability.Hyprmasq}[tool_name] if mode != "desktop" else capability.Desktop
        tool = kind(host, window, "background" if mode in ("browser", "walks", "fresh") else mode)
        routes = capability.Routes(tool, name, directory, tuple(only) if only else None)
        if mode == "walks":
            capability.walk_case(routes, host, tool)
        elif mode == "fresh":
            capability.fresh_case(routes, host, tool, {"cua": capability.Cua, "hyprmasq": capability.Hyprmasq}[tool_name])
        else:
            spec["run"](routes, host, tool)
    except Exception as e:  # the case stops, and says why
        print(f"  {mode} {name}: stopped: {e!r}", flush=True)
        routes = routes or capability.Routes(None, name, directory)
        routes.results.append({"tool": tool_name, "app": name, "mode": mode, "action": "(the rest of the case)", "route": "",
                               "result": "error", "why": repr(e)})
    finally:
        with open(done, "w") as f:
            json.dump({"case": name, "tool": tool_name, "mode": mode, "only": only, "machine": "real",
                       "versions": host.ready.get("versions"), "routes": routes.results if routes else []}, f, indent=1)
        host.session.close()
        fixture.stop()
        host.shell('hyprctl dispatch "hl.dsp.focus({ workspace = \\"1\\" })"')
    return routes.results if routes else []


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", choices=("hyprmasq", "cua"))
    parser.add_argument("--server")
    parser.add_argument("--case", action="append")
    parser.add_argument("--mode", action="append")
    parser.add_argument("--out", default="capability-host")
    parser.add_argument("--geometry")
    parser.add_argument("--shots", action="store_true")
    parser.add_argument("--environment", action="store_true")
    args = parser.parse_args()
    env = session_env()
    os.makedirs(args.out, exist_ok=True)
    if args.environment:
        output = capability.environment(lambda script: subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, check=False))
        with open(os.path.join(args.out, "environment.json"), "w") as f:
            json.dump(output, f, indent=1)
        print(json.dumps(output, indent=1)[:4000])
        return
    geometry = json.load(open(args.geometry)) if args.geometry else {}
    if args.shots:
        for name in args.case:
            directory = os.path.abspath(os.path.join(args.out, f"shots-{name}"))
            os.makedirs(directory, exist_ok=True)
            host = Host(args.server, directory, env)
            fixture = Fixture(name, host, capability.FIXTURES[name])
            try:
                window = fixture.start()
                listed = host.session.call("windows", {})
                tool = capability.Hyprmasq(host, window)
                tool.act({"action": "move", "x": 300, "y": 300})
                time.sleep(3)
                capability.save_shot(host, window, os.path.join(directory, "shot.png"))
                if geometry.get(name, {}).get("dropdown"):
                    tool.click(geometry[name]["dropdown"])
                    time.sleep(1.5)
                    capability.save_shot(host, window, os.path.join(directory, "dropdown.png"))
                print(name, "shot in", directory, json.dumps([c["size"] for c in host.clients() if c["address"] == window]), flush=True)
            finally:
                host.session.close()
                fixture.stop()
        return
    modes = args.mode or (["background"] if args.tool == "hyprmasq" else ["background", "foreground", "desktop"])
    for name in args.case:
        for mode in modes:
            for record in run_host_case(name, args.tool, mode, args, env, geometry):
                if record["result"] != "works" and record["route"]:
                    run_host_case(name, args.tool, mode, args, env, geometry, only=[record["action"], record["route"]])


if __name__ == "__main__":
    main()
