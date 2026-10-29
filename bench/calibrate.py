#!/usr/bin/env python3
"""Measures whether benchmark VMs that run at once on a machine slow each other down, without a model.

    calibrate.py [--task TASK] [--variant NAME] [--display WxH@S] [--source DIR] [--vms 1,2,3,4] [--cores N]
                 [--rounds N] [--lock FILE] [--cid CID] [--out DIR]

For each count of VMs N in --vms, it boots N VMs of the task at once, as N slots of dispatch.py would, each with
--cores physical cores of its own as dispatch.py's cores= gives them, or on any CPU without --cores. Once all are
ready, each runs the same script of tool calls on the task's window through hyprmasq-mcp, --rounds times: a shot,
a scroll down, a shot, a scroll back up, a zoom, and a Tab and a Shift+Tab. The calls go through mcp_tap.py, which
times them as it times an agent's. It reports for each N the median and the 90th percentile of the calls' seconds,
by kind of call, the peak memory in use in each VM and on the host, and QEMU's peak resident memory. N = 1 is the
baseline. --lock is held shared, as run.py --lock holds it, from the first count to the last. Results go to OUT/calibration.json and OUT/calibration.md,
and each VM's directory, OUT/N-vms/vm-K, holds its MCP log and the VM's files as a run's directory does.
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

    def run(self):
        while not self.stop.wait(HOST_SAMPLE_SECONDS):
            info = meminfo()
            self.peak_used_mib = max(self.peak_used_mib, (info["MemTotal"] - info["MemAvailable"]) // 1024)
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


def one_count(driver, out, n, cpus, rounds, cid):
    """Boots n VMs at once, runs the workload in each once all are ready, and returns what it measured."""
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
                    results[k] = workload(session, window, rounds)
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
    return {
        "vms": n, "started": workload_start["t"], "load_at_start": [round(x, 2) for x in workload_start["load"]],
        "all": stats(every), "by_kind": {kind: stats(by_kind[kind]) for kind in dict.fromkeys(ROUND)},
        "host_peak_used_mib": sampler.peak_used_mib, "per_vm": vms,
    }


def markdown(result):
    """Tables of a calibration: time per call by count of VMs, and memory."""
    counts = result["counts"]
    kinds = ["all"] + list(dict.fromkeys(ROUND))
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
    args = parser.parse_args()
    variants, default = run.task_variants(args.task)
    if not os.path.isdir(os.path.join(BENCH, "tasks", args.task)):
        parser.error(f"no task {args.task}")
    args.variant = args.variant or default
    if bool(variants) != bool(args.variant) or (variants and args.variant not in variants):
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
    driver = run.build_driver(out, source, args.task, args.variant, args.display)
    result = {"machine": run.machine(), "source": run.source_commit(source), "task": args.task,
              "variant": args.variant, "display": args.display, "cores": args.cores, "rounds": args.rounds,
              "counts": []}
    if args.lock:
        print(f"waiting for {args.lock}", flush=True)
    # Once for all counts: taken again for each, it would wait behind every VM test that queued meanwhile.
    with run.vm_lock(args.lock):
        for n in counts:
            print(f"{n} VMs: booting on CPUs " + "; ".join(run.format_cpus(c) if c else "any" for c in cpus[n]),
                  flush=True)
            measured = one_count(driver, out, n, cpus[n], args.rounds, args.cid)
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
