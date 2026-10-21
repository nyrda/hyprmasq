#!/usr/bin/env python3
"""The dataset of benchmark runs that docs/benchmarks.md is rendered from.

    export.py --out docs/benchmarks/data/NAME.json [--fail RUN=REASON]... RESULTS-DIR...

computes each finished run of the results directories again from its raw files, as report.py does, and writes
the measures render.py draws: no transcripts, answers, paths, host names or accounts. RUN is a run's directory
name, such as codex-gpt-6.1-sol-medium-2, or RESULTS-DIR's name and it, such as calc/codex-gpt-6.1-sol-medium-2.
--fail counts a run that the VM's check passed as failed, with the reason the docs give, such as a provider's
limit that ended it before the agent finished.
"""

import argparse
import datetime
import json
import os
import sys

import analyze
import report


def run_record(results_dir, name, fail):
    run_dir = os.path.join(results_dir, name)
    with open(os.path.join(run_dir, "run.json")) as f:
        run = json.load(f)
    s = report.metrics(run_dir)
    usage = s.get("usage") or {}
    effort = s.get("effort") or {}
    # A run that ended before any tool call has no act tool description for analyze.py to read.
    act = analyze.analyze(run_dir) if s["calls"] else {part + "_s": 0.0 for part in ("input", "wait", "watch", "pause", "rest")}
    record = {
        "run": f"{os.path.basename(os.path.normpath(results_dir))}/{name}",
        "task": s["task"],
        "harness": s["harness"],
        "model": s["model"],
        # The harness's default when the run asked for none.
        "effort": effort.get("requested") or "default",
        "ran_at": effort.get("ran_at"),
        "rep": s["rep"],
        "date": datetime.datetime.fromtimestamp(run["agent_start"], datetime.timezone.utc).date().isoformat(),
        "harness_version": s.get("harness_version"),
        "passed": s["passed"],
        "wall_s": s["wall_seconds"],
        "tools_s": s["tool_seconds"],
        "turn_s": s["median_gap_seconds"],
        "calls": s["calls"],
        "actions": s["actions"],
        "images": s["images"],
        "errors": s["errors"],
        "input_tokens": usage.get("input_tokens"),
        "cache_read_tokens": usage.get("cache_read_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": round(usage["cost_usd"], 6) if usage.get("cost_usd") is not None else None,
        # None where the run's events were not recorded.
        "user_undisturbed": (s.get("user") or {}).get("undisturbed"),
        "phases": s.get("phases") or {},
        "act_s": {part: act[part + "_s"] for part in ("input", "wait", "watch", "pause", "rest")},
    }
    kinds = (s.get("checks") or {}).get("kinds")
    if kinds:
        record["kinds"] = {k: {"items": v["items"], "seconds": v["seconds"], "corrections": v["corrections"]}
                           for k, v in kinds.items()}
    reason = fail.get(record["run"]) or fail.get(name)
    if reason:
        record["vm_passed"] = record["passed"]
        record["passed"] = False
        record["counted_failed"] = reason
    machine = s.get("machine") or {}
    return record, tuple(machine.get(k) for k in ("cpu", "threads", "memory_gib"))


def export(results_dirs, fail):
    names = [os.path.basename(os.path.normpath(d)) for d in results_dirs]
    if len(set(names)) < len(names):
        sys.exit("export.py: the results directories' names must differ, as runs are named by them: "
                 + ", ".join(sorted({n for n in names if names.count(n) > 1})))
    runs, sources, machines = [], [], set()
    for results_dir in results_dirs:
        with open(os.path.join(results_dir, "source.json")) as f:
            source = json.load(f)
        sources.append({k: source.get(k) for k in ("commit", "dirty", "task", "variant", "display")})
        for name in sorted(os.listdir(results_dir)):
            if os.path.exists(os.path.join(results_dir, name, "summary.json")):
                record, machine = run_record(results_dir, name, fail)
                runs.append(record)
                machines.add(machine)
    # The figures take medians by task, agent and effort, and times depend on the build and the machine: a dataset
    # holds one of each per task.
    builds = {}
    for s in sources:
        builds.setdefault(s["task"], set()).add(tuple(s[k] for k in ("variant", "commit", "dirty", "display")))
    mixed = sorted(task for task, b in builds.items() if len(b) > 1)
    if mixed:
        sys.exit(f"export.py: the results directories hold several variants, commits or displays of "
                 f"{', '.join(mixed)}. Export each to its own dataset.")
    if len(machines) > 1:
        sys.exit("export.py: the runs ran on several machines, whose times differ. Export each machine's runs to "
                 "its own dataset.")
    used = {r["run"] for r in runs} | {r["run"].split("/", 1)[1] for r in runs}
    unknown = sorted(set(fail) - used)
    if unknown:
        sys.exit(f"export.py: --fail names no finished run: {', '.join(unknown)}")
    names = [r["run"].split("/", 1)[1] for r in runs]
    ambiguous = sorted(name for name in fail if names.count(name) > 1)
    if ambiguous:
        sys.exit(f"export.py: --fail names a run of several results directories, name its directory too: "
                 f"{', '.join(ambiguous)}")
    return {"sources": sources, "runs": runs}


def dumps(data):
    """The dataset with one run a line, so that a diff shows which runs changed."""
    sources = ",\n  ".join(json.dumps(s) for s in data["sources"])
    runs = ",\n  ".join(json.dumps(r) for r in data["runs"])
    return f'{{\n "sources": [\n  {sources}\n ],\n "runs": [\n  {runs}\n ]\n}}\n'


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True)
    parser.add_argument("--fail", action="append", default=[], metavar="RUN=REASON")
    parser.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    fail = {}
    for item in args.fail:
        run, sep, reason = item.partition("=")
        if not sep or not reason:
            parser.error(f"--fail {item}: expected RUN=REASON")
        fail[run] = reason
    data = export(args.results, fail)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(dumps(data))
    print(f"{args.out}: {len(data['runs'])} runs")


if __name__ == "__main__":
    main(sys.argv[1:])
