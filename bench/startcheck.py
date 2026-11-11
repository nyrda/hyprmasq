#!/usr/bin/env python3
"""Whether a tool's first screenshot of a task's window shows the window as it is drawn, with no model.

    startcheck.py --tool cua|hyprmasq|hypruse|computer-use-linux [--rules FILE --series S | --task TASK[:VARIANT][@BROWSER]...]
                  --source DIR --out DIR [--cid N] [--cores N] [--lock FILE]

For each task it boots a fresh VM of the task, as a run does (vm.py), and before anything else asks the tool for a
screenshot of the task's window, as an agent's first look would: Cua's get_window_state, hyprmasq's and hypruse's
screenshot, computer-use-linux's get_app_state, and then its screenshot too, which raises the window it captures. Then it
shows that window's workspace until the screen stops changing and captures the window with grim, which is what the
window looks like when drawn. The two are compared at grim's size: the mean absolute difference of their pixels, from 0
to 255, and `matches` when it is below MAX_DIFFERENCE. A window that a hidden workspace kept from drawing gives a blank
frame or one at another size, and a far larger difference.

It also records the user's window at the start, which the start step gives the focus back to: the terminal, or in
Forgejo the notes window where its user types. With --hold SECONDS it waits that long after the first screenshot, before
it moves the focus, and records what the task's user typed in that time from the VM's grade (Forgejo's user_notes).

Results go to OUT/TOOL-TASK/startcheck.json, with both images, and OUT/startcheck.md has the table. It needs Pillow.
"""

import argparse
import base64
import io
import json
import os
import sys
import time

from PIL import Image, ImageChops, ImageStat

import capability
import run

BENCH = os.path.dirname(os.path.abspath(__file__))
# Two captures of the same drawn window differ by little: fonts and colours are the same, and only a scaled screenshot
# blurs edges. A blank frame or a frame at half the width differs by tens.
MAX_DIFFERENCE = 8.0


def series_tasks(rules_path, series_name, tool):
    """The (task, variant, browser) of a series' sets for one tool."""
    with open(rules_path) as f:
        rules = json.load(f)
    series = rules["series"][series_name]
    sets = series.get("sets", {}).get(tool) or rules["tools"][tool]["sets"]
    found = []
    for spec in sets.values():
        for task in spec["tasks"]:
            name, _, variant = task.partition(":")
            found.append((name, variant or None, spec.get("browser", "firefox")))
    return found


def parse_task(text):
    task, _, browser = text.partition("@")
    name, _, variant = task.partition(":")
    return name, variant or None, browser or "firefox"


def first_shot(vm, tool, address):
    """The tool's first screenshot of the window, as PNG bytes: nothing else has touched the window."""
    if tool == "cua":
        listed = vm.session.call("list_windows", {})["structuredContent"]["windows"]
        window_id = int(address, 16)
        pid = next(w["pid"] for w in listed if w["window_id"] == window_id)
        result = vm.session.call("get_window_state", {"pid": pid, "window_id": window_id,
                                                      "include_accessibility_tree": False})
    elif tool == "computer-use-linux":
        result = vm.session.call("get_app_state", {"window_id": int(address, 16)})
    else:
        result = vm.session.call("screenshot", {"window": address})
    return image_of(result)


def image_of(result):
    image = next((c for c in result.get("content") or [] if c.get("type") == "image"), None)
    return base64.b64decode(image["data"]) if image else None


def drawn(vm, address, directory):
    """The window as drawn: its workspace shown until the screen stops changing, then the window captured with grim."""
    client = next(c for c in json.loads(vm.hypr("-j clients")) if c["address"] == address)
    name = client["workspace"]["name"]
    target = f'"{name}"' if name.isdigit() else f'"name:{name}"'
    env = 'XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=$(cat /tmp/hyprmasq-bench-display)'
    vm.hypr("dispatch " + f"'hl.dsp.focus({{ workspace = {target} }})'")
    script = (f"for i in $(seq 1 60); do su alice -c \"{env} grim /tmp/startcheck-a.png\"; sleep 0.5; "
              f"su alice -c \"{env} grim /tmp/startcheck-b.png\"; cmp -s /tmp/startcheck-a.png /tmp/startcheck-b.png && break; "
              "done; echo $i")
    vm.shell(script, timeout=120)
    client = next(c for c in json.loads(vm.hypr("-j clients")) if c["address"] == address)
    (x, y), (w, h) = client["at"], client["size"]
    # The VM's shell can garble so long an output: a capture that does not decode is taken again.
    for attempt in range(3):
        status, out = vm.shell(f"su alice -c \"{env} grim -g '{x},{y} {w}x{h}' /tmp/startcheck-window.png\" && "
                               "base64 -w0 /tmp/startcheck-window.png", timeout=60)
        if status != 0:
            raise RuntimeError(f"grim failed: {out[-300:]}")
        png = base64.b64decode(out.strip().splitlines()[-1] + "==")
        try:
            Image.open(io.BytesIO(png)).load()
            return png
        except OSError:
            if attempt == 2:
                raise
    return None


def difference(shot, reference):
    """The mean absolute difference of two images' pixels at the reference's size, 0 to 255, and their sizes."""
    a = Image.open(io.BytesIO(shot)).convert("RGB")
    b = Image.open(io.BytesIO(reference)).convert("RGB")
    sizes = {"shot": list(a.size), "drawn": list(b.size)}
    if a.size != b.size:
        a = a.resize(b.size, Image.Resampling.BILINEAR)
    return round(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3, 2), sizes


def check(tool, task, variant, browser, args):
    label = f"{task}{'-' + variant if variant else ''}{'-' + browser if browser != 'firefox' else ''}"
    directory = os.path.abspath(os.path.join(args.out, f"{tool}-{label}"))
    done = os.path.join(directory, "startcheck.json")
    if os.path.exists(done):
        with open(done) as f:
            return json.load(f)
    os.makedirs(os.path.join(directory, "tmp"), exist_ok=True)
    os.makedirs(os.path.join(directory, "driver-out"), exist_ok=True)
    print(f"{tool} {label}: building", flush=True)
    driver = run.build_driver(directory, args.source, task, variant, "1920x1080@1", tool, browser, "omarchy")
    cpus = run.slot_cpus(run.read_cores(), 1, args.cores)[0] if args.cores else None
    print(f"{tool} {label}: booting", flush=True)
    record = {"tool": tool, "task": task, "variant": variant, "browser": browser}
    with run.vm_lock(args.lock):
        with run.booted_vm(driver, directory, args.cid, 1800, cpus, {"HYPRMASQ_BENCH_SHELL": "1"}):
            vm = capability.Vm(directory, args.cid, "hyprmasq-bench-startcheck")
            try:
                address = vm.ready["task_window"]
                record["start_state"] = vm.ready.get("start_state")
                user = (vm.ready.get("user") or {}).get("window")
                window = next((c for c in vm.ready.get("clients") or [] if c["address"] == user), {})
                record["user_window"] = {"class": window.get("class"), "title": window.get("title"),
                                         "workspace": (vm.ready.get("user") or {}).get("workspace")}
                shot = first_shot(vm, tool, address)
                second = image_of(vm.session.call("screenshot", {"window_id": int(address, 16)})) if tool == "computer-use-linux" else None
                if args.hold:
                    time.sleep(args.hold)
                reference = drawn(vm, address, directory)
                with open(os.path.join(directory, "first-shot.png"), "wb") as f:
                    f.write(shot or b"")
                with open(os.path.join(directory, "drawn.png"), "wb") as f:
                    f.write(reference)
                if shot is None:
                    record.update(result="no image")
                else:
                    value, sizes = difference(shot, reference)
                    record.update(difference=value, sizes=sizes,
                                  result="matches" if value < MAX_DIFFERENCE else "differs")
                if second:
                    with open(os.path.join(directory, "second-shot.png"), "wb") as f:
                        f.write(second)
                    value, sizes = difference(second, reference)
                    record.update(screenshot_difference=value, screenshot_sizes=sizes)
            except Exception as e:  # the task stops, and says why
                record.update(result="error", why=repr(e))
            finally:
                vm.session.close()
    graded = os.path.join(directory, "vm-result.json")
    if os.path.exists(graded):
        with open(graded) as f:
            notes = ((json.load(f).get("task") or {}).get("check") or {}).get("checks", {}).get("user_notes")
        if notes is not None:
            record["user_notes"] = notes
    with open(done, "w") as f:
        json.dump(record, f, indent=1)
    print(f"{tool} {label}: {record['result']} {record.get('difference', '')} {record.get('sizes', '')}", flush=True)
    return record


def table(records):
    lines = ["| Tool | Task | Browser | First shot | Drawn | Difference | Result |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in records:
        sizes = r.get("sizes") or {}
        task = r["task"] + (f":{r['variant']}" if r.get("variant") else "")
        lines.append(f"| {r['tool']} | {task} | {r['browser']} | {'×'.join(map(str, sizes.get('shot', [])))} | "
                     f"{'×'.join(map(str, sizes.get('drawn', [])))} | {r.get('difference', '')} | {r['result']} |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tool", choices=("cua", "hyprmasq", "hypruse", "computer-use-linux"), required=True)
    parser.add_argument("--rules", default=os.path.join(BENCH, "comparison-series.json"))
    parser.add_argument("--series")
    parser.add_argument("--task", action="append", help="TASK[:VARIANT][@BROWSER], repeatable, instead of a series'")
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--cid", type=int, default=4901)
    parser.add_argument("--cores", type=int)
    parser.add_argument("--lock")
    parser.add_argument("--hold", type=int, default=0, help="seconds to wait after the first screenshot")
    args = parser.parse_args()
    if not args.series and not args.task:
        parser.error("give --series or --task")
    tasks = [parse_task(t) for t in args.task] if args.task else series_tasks(args.rules, args.series, args.tool)
    records = [check(args.tool, *t, args) for t in tasks]
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"startcheck-{args.tool}.md"), "w") as f:
        f.write(table(records))
    print(table(records))
    sys.exit(0 if all(r["result"] == "matches" for r in records) else 1)


if __name__ == "__main__":
    main()
