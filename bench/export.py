#!/usr/bin/env python3
"""The dataset of benchmark runs that docs/benchmarks.md is rendered from.

    export.py --out docs/benchmarks/data/NAME.json [--several-machines] [--fail RUN=REASON]... [--agent AGENT]...
              RESULTS-DIR...

computes each finished run of the results directories again from its raw files, as report.py does, and writes
the measures render.py draws: no transcripts, answers, paths, host names or accounts. RUN is a run's directory
name, such as codex-gpt-6.1-sol-medium-2, or RESULTS-DIR's name and it, such as calc/codex-gpt-6.1-sol-medium-2.
--fail counts a run that the VM's check passed as failed, with the reason the docs give, such as a provider's
limit that ended it before the agent finished. Each run records its task and its variant, so that a dataset can
hold several variants of a task, such as board's two or the form task in each toolkit, as long as they share a commit
and a display. render.py then names the task by its variant too, such as board-dnd. --several-machines takes runs
of several machines into one dataset, each run with its CPU, for runs that the dispatcher spread over them: their
times compare only where each build ran on the same machines. --agent keeps only the runs of the agents that match
one of its patterns, HARNESS, HARNESS:MODEL or HARNESS:MODEL@EFFORT with *, as dispatch.py's agents= takes them, such
as `--agent '*:*@medium'` for the runs at medium effort of a results directory that holds others too. Each source and
run records its backend, hyprmasq, cua, hypruse or computer-use-linux, and its prompt, the task's own or the
comparison's (run.py --backend and --prompt), and the user's browser (--browser): a dataset holds one of each per task.
A run of another backend than hyprmasq has no act_s, the split of hyprmasq's act calls, and records its tool's
refusals.
"""

import argparse
import datetime
import json
import os
import sys

import analyze
import dispatch
import report


def run_record(results_dir, name, fail, variant):
    run_dir = os.path.join(results_dir, name)
    with open(os.path.join(run_dir, "run.json")) as f:
        run = json.load(f)
    s = report.metrics(run_dir)
    usage = s.get("usage") or {}
    effort = s.get("effort") or {}
    parts = ("input", "wait", "watch", "pause", "rest")
    # A run that ended before any tool call has no act tool description for analyze.py to read, and the other backends
    # have no act.
    act = None
    if s["backend"] == "hyprmasq":
        act = analyze.analyze(run_dir) if s["calls"] else {part + "_s": 0.0 for part in parts}
    record = {
        "run": f"{os.path.basename(os.path.normpath(results_dir))}/{name}",
        "task": s["task"],
        "variant": variant,
        "backend": s["backend"],
        "prompt": s["prompt"],
        "browser": s["browser"],
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
        # How large the agent's context got, None where the harness's logs do not tell.
        "context": s.get("context"),
        # None where the run's events were not recorded.
        "user_undisturbed": (s.get("user") or {}).get("undisturbed"),
        # Seconds the focus was away from the user's window, which the comparison's figures count as disturbed.
        "focus_away_s": (s.get("user") or {}).get("focus_away_seconds"),
        "phases": s.get("phases") or {},
        "act_s": {part: act[part + "_s"] for part in parts} if act else None,
    }
    if s.get("refusals"):
        record["refusals"] = s["refusals"]
    kinds = (s.get("checks") or {}).get("kinds")
    # The form task's kinds of question, with their times; the trace task counts its kinds of stroke there.
    if kinds and all(isinstance(v, dict) for v in kinds.values()):
        record["kinds"] = {k: {"items": v["items"], "seconds": v["seconds"], "corrections": v["corrections"]}
                           for k, v in kinds.items()}
    reason = fail.get(record["run"]) or fail.get(name)
    if reason:
        record["vm_passed"] = record["passed"]
        record["passed"] = False
        record["counted_failed"] = reason
    machine = s.get("machine") or {}
    record["cpu"] = machine.get("cpu")
    return record, tuple(machine.get(k) for k in ("cpu", "threads", "memory_gib"))


def wanted(record, agents):
    """Whether a run's agent matches one of the patterns, or there are none."""
    effort = None if record["effort"] == "default" else record["effort"]
    return not agents or any(dispatch.agent_matches(p, record["harness"], record["model"], effort) for p in agents)


def export(results_dirs, fail, several_machines=False, agents=()):
    names = [os.path.basename(os.path.normpath(d)) for d in results_dirs]
    if len(set(names)) < len(names):
        sys.exit("export.py: the results directories' names must differ, as runs are named by them: "
                 + ", ".join(sorted({n for n in names if names.count(n) > 1})))
    runs, sources, machines = [], [], set()
    for results_dir in results_dirs:
        with open(os.path.join(results_dir, "source.json")) as f:
            source = json.load(f)
        sources.append({**{k: source.get(k) for k in ("commit", "dirty", "task", "variant", "display")},
                        "backend": source.get("backend", "hyprmasq"), "prompt": source.get("prompt", "task"),
                        "browser": source.get("browser", "firefox")})
    for results_dir, s in zip(results_dirs, sources):
        for name in sorted(os.listdir(results_dir)):
            if os.path.exists(os.path.join(results_dir, name, "summary.json")):
                record, machine = run_record(results_dir, name, fail, s["variant"])
                if not wanted(record, agents):
                    continue
                runs.append(record)
                machines.add(machine)
    # Times depend on the build and the machine: a dataset holds one of each per task. Its runs name their variant,
    # by which render.py tells a task's variants apart.
    builds = {}
    for s in sources:
        build = tuple(s[k] for k in ("commit", "dirty", "display", "backend", "prompt", "browser"))
        builds.setdefault(s["task"], set()).add(build)
    mixed = sorted(task for task, b in builds.items() if len(b) > 1)
    if mixed:
        sys.exit(f"export.py: the results directories hold several commits, displays, backends, prompts or browsers of "
                 f"{', '.join(mixed)}. Export each to its own dataset.")
    if len(machines) > 1 and not several_machines:
        sys.exit("export.py: the runs ran on several machines, whose times differ. Export each machine's runs to "
                 "its own dataset, or pass --several-machines.")
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
    parser.add_argument("--several-machines", action="store_true")
    parser.add_argument("--agent", action="append", default=[], metavar="AGENT",
                        help="keep only the runs of the agents that match, HARNESS[:MODEL[@EFFORT]] with *; repeatable")
    parser.add_argument("results", nargs="+")
    args = parser.parse_args(argv)
    fail = {}
    for item in args.fail:
        run, sep, reason = item.partition("=")
        if not sep or not reason:
            parser.error(f"--fail {item}: expected RUN=REASON")
        fail[run] = reason
    data = export(args.results, fail, args.several_machines, args.agent)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(dumps(data))
    print(f"{args.out}: {len(data['runs'])} runs")


if __name__ == "__main__":
    main(sys.argv[1:])
