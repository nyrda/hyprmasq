#!/usr/bin/env python3
"""Measures whether benchmark VMs that run at once on a machine slow each other down, without a model.

    calibrate.py [--task TASK] [--variant NAME] [--display WxH@S] [--source DIR] [--vms 1,2,3,4] [--cores N]
                 [--rounds N] [--lock FILE] [--cid CID] [--out DIR] [--backend hyprmasq|cua|hypruse|computer-use-linux] [--desktop bare|omarchy]

For each count of VMs N in --vms, it boots N VMs of the task at once, as N slots of dispatch.py would, each with
--cores physical cores of its own as dispatch.py's cores= gives them, or on any CPU without --cores. Once all are
ready, each runs the same script of tool calls on the task's window through hyprmasq-mcp, --rounds times: a shot,
a scroll down, a shot, a scroll back up, a zoom, and a Tab and a Shift+Tab. The calls go through mcp_tap.py, which
times them as it times an agent's. It reports for each N the median and the 90th percentile of the calls' seconds,
by kind of call, the peak memory in use in each VM and on the host, and QEMU's peak resident memory. N = 1 is the
baseline. --lock is held shared, as run.py --lock holds it, from the first count to the last. Results go to OUT/calibration.json and OUT/calibration.md,
and each VM's directory, OUT/N-vms/vm-K, holds its MCP log and the VM's files as a run's directory does.
With --backend hypruse, the script is a screenshot of the window, its tree of elements and a key, and with
computer-use-linux the app's state, a screenshot and a key, each after a first call that is not timed.
With --backend cua, the script is a walk of the accessibility tree with Cua's get_window_state, a shot without a walk, a
scroll down and up in the background, and a key, and the report adds what the walks themselves took, the driver's own
walk_ms, since Cua's reads depend on them. Other VMs on the machine skew all of this: it says so if it saw any.
See bench/README.md.
"""

import argparse
import contextlib
import datetime
import json
import os
import statistics
import threading
import time

import mcp_probe
import report
import run

BENCH = os.path.dirname(os.path.abspath(__file__))
# The kinds of the script's calls, in order, for one round.
ROUND = ["shot", "scroll", "shot", "scroll", "zoom", "key", "key"]
CUA_ROUND = ["walk", "shot", "scroll", "scroll", "key"]
HOST_SAMPLE_SECONDS = 1
GUEST_SAMPLE_SECONDS = 2


def workload(session, window, rounds):
    """The script's calls in one VM: kinds of call in order, after a first `windows`, which is not counted."""
    session.call("windows", {})
    kinds = []

    def shot():
        result = session.call("screenshot", {"window": window})
        kinds.append("shot")
        return result["structuredContent"]["shot"]

    for _ in range(rounds):
        for dy in (3, -3):
            s = shot()
            session.call("act", {"window": window, "shot": s["id"], "actions": [
                {"action": "scroll", "x": s["width"] // 2, "y": s["height"] // 2, "dy": dy, "unit": "notch"}]})
            kinds.append("scroll")
        session.call("screenshot", {"window": window, "region": {
            "shot": s["id"], "x": s["width"] // 4, "y": s["height"] // 4,
            "width": s["width"] // 2, "height": s["height"] // 2}})
        kinds.append("zoom")
        for keys in (["Tab"], ["shift+Tab"]):
            session.call("act", {"window": window, "actions": [{"action": "key", "keys": keys}]})
            kinds.append("key")
    return kinds


def cua_workload(session, window, rounds):
    """Cua's calls in one VM, as workload's are hyprmasq's: kinds of call in order, after a first `list_windows`. A walk
    gets a budget it never reaches, as what it takes is the measure, and what it read is kept from the agent's reads'
    default budget. window is Hyprland's address, which Cua takes as window_id."""
    window_id = int(window, 16)
    session.request("tools/list", {})  # so that the log says whose server this is, for its walks
    listed = session.call("list_windows", {})["structuredContent"]["windows"]
    pid = next(w["pid"] for w in listed if w["window_id"] == window_id)
    target = {"pid": pid, "window_id": window_id}
    kinds = []
    for _ in range(rounds):
        state = session.call("get_window_state", {**target, "timeout_ms": 10000})
        kinds.append("walk")
        shot = session.call("get_window_state", {**target, "include_accessibility_tree": False})
        kinds.append("shot")
        sc = shot.get("structuredContent") or state.get("structuredContent") or {}
        x, y = (sc.get("screenshot_width") or 1000) // 2, (sc.get("screenshot_height") or 800) // 2
        for direction in ("down", "up"):
            session.call("scroll", {**target, "x": x, "y": y, "direction": direction, "amount": 3})
            kinds.append("scroll")
        session.call("press_key", {**target, "key": "Tab"})
        kinds.append("key")
    return kinds


def hypruse_workload(session, window, rounds):
    """hypruse's calls in one VM, after a first `desktop`: a screenshot of the window, its tree of elements, and a key."""
    session.call("desktop", {})
    kinds = []
    for _ in range(rounds):
        session.call("screenshot", {"window": window})
        kinds.append("shot")
        session.call("ui", {"window": window})
        kinds.append("tree")
        session.call("keyboard", {"action": "key", "keys": "tab", "window": window})
        kinds.append("key")
    return kinds


def cul_workload(session, window, rounds):
    """computer-use-linux's calls in one VM, after a first `list_windows`: the app's state, its tree with a screenshot, a
    screenshot alone, and a key. window is Hyprland's address, which it takes as window_id."""
    window_id = int(window, 16)
    session.call("list_windows", {})
    kinds = []
    for _ in range(rounds):
        session.call("get_app_state", {"window_id": window_id})
        kinds.append("state")
        session.call("screenshot", {"window_id": window_id})
        kinds.append("shot")
        session.call("press_key", {"key": "Tab", "window_id": window_id})
        kinds.append("key")
    return kinds


WORKLOADS = {"hyprmasq": (None, None), "cua": (None, None), "hypruse": (hypruse_workload, ["shot", "tree", "key"]),
             "computer-use-linux": (cul_workload, ["state", "shot", "key"])}


def walks_ms(path):
    """The driver's own time of each walk in an MCP log, in ms."""
    return [ms for c in report.tool_calls(report.load_jsonl(path)) for ms in c.get("walk_ms") or []]


def other_vms(cids):
    """QEMU processes on the machine that are not the calibration's own."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        with contextlib.suppress(OSError):
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode(errors="replace")
            if "qemu-system" in cmdline and not any(f"hyprmasq-bench-vm-{cid}/" in cmdline for cid in cids):
                found.append(int(pid))
    return found


def meminfo():
    with open("/proc/meminfo") as f:
        return {line.split(":")[0]: int(line.split()[1]) for line in f}


def qemu_processes(cids):
    """The QEMU process of each VM, by CID: the one whose command line has the VM's runtime directory."""
    found = {}
    for pid in filter(str.isdigit, os.listdir("/proc")):
        with contextlib.suppress(OSError):
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmdline = f.read().decode(errors="replace")
            if "qemu" not in cmdline:
                continue
            for cid in cids:
                if f"hyprmasq-bench-vm-{cid}/" in cmdline:
                    found[cid] = int(pid)
    return found


def status_kib(pid, field):
    with open(f"/proc/{pid}/status") as f:
        return next(int(line.split()[1]) for line in f if line.startswith(field + ":"))


def thread_cpus(pid):
    """The CPUs the threads of a process may run on, each distinct list once."""
    lists = set()
    for tid in os.listdir(f"/proc/{pid}/task"):
        with contextlib.suppress(OSError), open(f"/proc/{pid}/task/{tid}/status") as f:
            lists.update(line.split(":")[1].strip() for line in f if line.startswith("Cpus_allowed_list:"))
    return sorted(lists)


class HostSampler(threading.Thread):
    """Samples the host's memory in use, and the VMs' QEMU, until stopped."""

    def __init__(self, cids):
        super().__init__(daemon=True)
        self.cids, self.stop = cids, threading.Event()
        self.peak_used_mib, self.qemu_peak_mib, self.qemu_cpus = 0, {}, {}
        self.other_vms = set()

    def run(self):
        while not self.stop.wait(HOST_SAMPLE_SECONDS):
            info = meminfo()
            self.peak_used_mib = max(self.peak_used_mib, (info["MemTotal"] - info["MemAvailable"]) // 1024)
            self.other_vms.update(other_vms(self.cids))
            for cid, pid in qemu_processes(self.cids).items():
                with contextlib.suppress(OSError, StopIteration):
                    self.qemu_peak_mib[cid] = max(self.qemu_peak_mib.get(cid, 0), status_kib(pid, "VmHWM") // 1024)
                    self.qemu_cpus.setdefault(cid, thread_cpus(pid))


def stats(seconds):
    if not seconds:
        return None
    ordered = sorted(seconds)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]
    return {"calls": len(ordered), "median": round(statistics.median(ordered), 3), "p90": round(p90, 3),
            "mean": round(statistics.fmean(ordered), 3)}


def one_count(driver, out, n, cpus, rounds, cid, backend="hyprmasq"):
    """Boots n VMs at once, runs the workload in each once all are ready, and returns what it measured."""
    script = cua_workload if backend == "cua" else WORKLOADS[backend][0] or workload
    round_kinds = CUA_ROUND if backend == "cua" else WORKLOADS[backend][1] or ROUND
    cids = [cid + k for k in range(n)]
    dirs = [os.path.join(out, f"{n}-vms", f"vm-{k}") for k in range(n)]
    for d in dirs:
        os.makedirs(os.path.join(d, "tmp"))
        os.makedirs(os.path.join(d, "driver-out"))
    barrier = threading.Barrier(n)
    results, errors = [None] * n, []
    workload_start = {}

    def vm(k):
        try:
            with run.booted_vm(driver, dirs[k], cids[k], 3600, cpus[k],
                               env={"HYPRMASQ_BENCH_MEMORY_SECONDS": str(GUEST_SAMPLE_SECONDS)}):
                with open(os.path.join(dirs[k], "ready.json")) as f:
                    window = json.load(f)["task_window"]
                barrier.wait(timeout=1800)
                workload_start.setdefault("t", time.time())
                workload_start.setdefault("load", os.getloadavg())
                session = mcp_probe.Session(cids[k], os.path.join(dirs[k], "mcp.jsonl"), "hyprmasq-bench-calibrate")
                try:
                    results[k] = script(session, window, rounds)
                finally:
                    session.close()
        except Exception as e:  # the others stop as well
            barrier.abort()
            errors.append(f"VM {k}: {e!r}")

    sampler = HostSampler(cids)
    sampler.start()
    threads = [threading.Thread(target=vm, args=(k,)) for k in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    sampler.stop.set()
    sampler.join()
    if errors:
        raise RuntimeError("; ".join(errors))

    by_kind, every, vms = {}, [], []
    for k, (d, kinds) in enumerate(zip(dirs, results)):
        calls = [c for c in report.tool_calls(report.load_jsonl(os.path.join(d, "mcp.jsonl")))][1:]  # windows
        if len(calls) != len(kinds):
            raise RuntimeError(f"{d}: {len(calls)} calls in mcp.jsonl for {len(kinds)} in the script")
        for kind, call in zip(kinds, calls):
            by_kind.setdefault(kind, []).append(call["seconds"])
            every.append(call["seconds"])
        with open(os.path.join(d, "vm-result.json")) as f:
            memory = json.load(f).get("memory") or {}
        vms.append({
            "cpus": run.format_cpus(cpus[k]) if cpus[k] else None,
            "qemu_threads_cpus": sampler.qemu_cpus.get(cids[k]),
            "seconds": round(calls[-1]["end"] - calls[0]["start"], 1),
            "errors": sum(c["error"] for c in calls), "no_change": sum(c["no_change"] for c in calls),
            "guest_total_mib": memory.get("total_mib"), "guest_peak_used_mib": memory.get("peak_used_mib"),
            "qemu_peak_rss_mib": sampler.qemu_peak_mib.get(cids[k]),
        })
    measured = {
        "vms": n, "started": workload_start["t"], "load_at_start": [round(x, 2) for x in workload_start["load"]],
        "all": stats(every), "by_kind": {kind: stats(by_kind[kind]) for kind in dict.fromkeys(round_kinds)},
        "host_peak_used_mib": sampler.peak_used_mib, "per_vm": vms, "other_vms": sorted(sampler.other_vms),
    }
    if backend == "cua":
        walks = [ms for d in dirs for ms in walks_ms(os.path.join(d, "mcp.jsonl"))]
        measured["walk_ms"] = {"count": len(walks), "median": round(statistics.median(walks)),
                               "p90": sorted(walks)[int(0.9 * len(walks))]} if walks else None
    return measured


def markdown(result):
    """Tables of a calibration: time per call by count of VMs, and memory."""
    counts = result["counts"]
    kinds = ["all"] + list(counts[0]["by_kind"])
    lines = [f"Calibration on {result['machine']['host']} ({result['machine']['cpu']}, {result['machine']['threads']} "
             f"threads, {result['machine']['memory_gib']} GiB), task {result['task']}"
             + (f" {result['variant']}" if result["variant"] else "") + f", {result['rounds']} rounds a VM, "
             + (f"{result['cores']} cores a VM" if result["cores"] else "VMs on any CPU") + ".", "",
             "Seconds per call, median / p90 over all VMs' calls:", "",
             "| VMs | " + " | ".join(kinds) + " | errors | no change |",
             "| --- |" + " --- |" * (len(kinds) + 2)]
    for c in counts:
        cells = [c["all"]] + [c["by_kind"][k] for k in kinds[1:]]
        lines.append(f"| {c['vms']} | " + " | ".join(f"{s['median']:.3f} / {s['p90']:.3f}" for s in cells)
                     + f" | {sum(v['errors'] for v in c['per_vm'])} | {sum(v['no_change'] for v in c['per_vm'])} |")
    lines += ["", "Memory, MiB: the most in use in any VM (of its total), QEMU's peak resident memory of any VM, and "
              "the host's peak in use:", "", "| VMs | guest peak | QEMU peak | host peak | CPUs of the VMs | load at start |",
              "| --- | --- | --- | --- | --- | --- |"]
    for c in counts:
        v = c["per_vm"]
        guest = max((x["guest_peak_used_mib"] or 0) for x in v)
        lines.append(f"| {c['vms']} | {guest} of {v[0]['guest_total_mib']} | "
                     f"{max((x['qemu_peak_rss_mib'] or 0) for x in v)} | {c['host_peak_used_mib']} | "
                     + "; ".join(x["cpus"] or "any" for x in v) + f" | {' '.join(map(str, c['load_at_start']))} |")
    if any(c.get("walk_ms") for c in counts):
        lines += ["", "The driver's own time of its accessibility walks, ms:", "", "| VMs | walks | median | p90 |",
                  "| --- | --- | --- | --- |"]
        lines += [f"| {c['vms']} | {c['walk_ms']['count']} | {c['walk_ms']['median']} | {c['walk_ms']['p90']} |"
                  for c in counts if c.get("walk_ms")]
    others = sorted({pid for c in counts for pid in c.get("other_vms") or []})
    if others:
        lines += ["", f"**Do not trust these numbers: other VMs ran on the machine meanwhile, QEMU processes {others}.**"]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", default="form", help="a directory under bench/tasks")
    parser.add_argument("--variant", help="the task's variant, by default its default")
    parser.add_argument("--display", default="1920x1080@1", help="the VM's monitor, WIDTHxHEIGHT@SCALE")
    parser.add_argument("--source", help="hyprmasq checkout to run in the VM, by default this one")
    parser.add_argument("--vms", default="1,2,3,4", help="the counts of VMs at once, comma-separated")
    parser.add_argument("--cores", type=int, help="physical cores of each VM's own, as dispatch.py's cores=")
    parser.add_argument("--rounds", type=int, default=20, help="rounds of the script in each VM")
    parser.add_argument("--lock", help="lock file to hold shared while the VMs run, as run.py --lock")
    parser.add_argument("--cid", type=int, default=4342, help="the vsock CID of the first VM, the next one more")
    parser.add_argument("--out")
    parser.add_argument("--backend", choices=tuple(WORKLOADS), default="hyprmasq", help="the tool whose calls it times")
    parser.add_argument("--browser", choices=run.BROWSERS, default="firefox")
    parser.add_argument("--desktop", choices=run.DESKTOPS, default="bare")
    args = parser.parse_args()
    variants, default = run.task_variants(args.task)
    if not os.path.isdir(os.path.join(BENCH, "tasks", args.task)):
        parser.error(f"no task {args.task}")
    args.variant = args.variant or default
    optional = run.variants_optional(args.task) and not args.variant
    if not optional and (bool(variants) != bool(args.variant) or (variants and args.variant not in variants)):
        parser.error(f"task {args.task} takes " + (f"--variant, one of {', '.join(variants)}" if variants else "no variant"))
    try:
        counts = [int(n) for n in args.vms.split(",")]
        if any(n < 1 for n in counts):
            raise ValueError
    except ValueError:
        parser.error("--vms is counts of 1 or more, such as 1,2,3,4")
    cores = run.read_cores()
    try:
        cpus = {n: run.slot_cpus(cores, n, args.cores) if args.cores else [None] * n for n in counts}
    except ValueError as e:
        parser.error(str(e))
    out = os.path.abspath(args.out or os.path.join(
        BENCH, "results", f"{datetime.datetime.now().strftime('%Y-%m-%d-%H%M%S')}-calibrate-{args.task}"))
    os.makedirs(out)
    source = os.path.abspath(args.source) if args.source else None
    print(f"building the driver of {args.task}", flush=True)
    driver = run.build_driver(out, source, args.task, args.variant, args.display, args.backend, args.browser, args.desktop)
    result = {"machine": run.machine(), "source": run.source_commit(source), "task": args.task,
              "variant": args.variant, "display": args.display, "cores": args.cores, "rounds": args.rounds,
              "backend": args.backend, "browser": args.browser, "desktop": args.desktop, "counts": []}
    if args.lock:
        print(f"waiting for {args.lock}", flush=True)
    # Once for all counts: taken again for each, it would wait behind every VM test that queued meanwhile.
    with run.vm_lock(args.lock):
        for n in counts:
            print(f"{n} VMs: booting on CPUs " + "; ".join(run.format_cpus(c) if c else "any" for c in cpus[n]),
                  flush=True)
            measured = one_count(driver, out, n, cpus[n], args.rounds, args.cid, args.backend)
            result["counts"].append(measured)
            print(f"{n} VMs: median {measured['all']['median']} s, p90 {measured['all']['p90']} s a call", flush=True)
            with open(os.path.join(out, "calibration.json"), "w") as f:
                json.dump(result, f, indent=2)
    text = markdown(result)
    with open(os.path.join(out, "calibration.md"), "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
