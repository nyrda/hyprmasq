#!/usr/bin/env python3
"""Runs a comparison's series by its committed rules, one repetition round at a time.

    series.py list    SERIES [--rules FILE] [--tool T]... [--round N]...     what would run, as commands, and how many runs
    series.py check   SERIES [--rules FILE]                                  what the rules pin, on the series' machine
    series.py build   SERIES [--rules FILE] [--tool T]...                    ships the commits and builds every driver
    series.py run     SERIES [--rules FILE] [--tool T]... [--round N]...     runs, and resumes where it stopped
    series.py status  SERIES [--rules FILE]                                  finished, passed and excluded runs per tool
    series.py estimate SERIES [--rules FILE]                                 expected time, from the published series

The rules file (bench/comparison-series.json) is committed before any result exists and fixes the tools, models, efforts,
prompt, the sets of tasks, and for each series its tools, its one machine, its repetitions and its results directory, the
limits per subscription, the order of the rounds and what allows a rerun. This script only carries them out. The series are
kept apart: a series runs each tool on one machine, as times compare only there, and its results are never pooled with
another's. A series may name another machine for a tool (tool_machines), and then both run at once.

A round is one repetition of every selected tool: `dispatch.py --rep N` per set, as a set holds one backend and one browser,
the sets one after the other, in an order that rotates from round to round, so that no tool is always first. The next round
starts when this one ended, so that the tools share the same period.

Before a round's dispatches it checks the machine's harness versions against the rules, and stops on a difference. After
them it looks at each run of the round. A run with no summary.json, or whose harness stopped for a limit, a login
or the provider's failure (report.infrastructure_failure), is moved to OUT/excluded/ROUND-N/, logged in
OUT/exclusions.jsonl with why, and runs again on the same machine, after a subscription's limit only once it has reset.
Nothing else reruns: a run that the agent failed stays as it is. Running the same command again resumes, as dispatch.py
skips the runs that have a summary.json. See bench/README.md.
"""

import argparse
import datetime
import glob
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

import report
import run

BENCH = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(BENCH, "comparison-series.json")
# How long a round waits when a limit gives no time of its own, and how many passes of a round before it gives up, with
# the runs still missing named.
LIMIT_WAIT_SECONDS = 1800
MAX_PASSES = 8
# How often a tool paced by another looks whether the other has started the round.
PACE_POLL_SECONDS = 60


# Where a machine of the rules is reached and where a series' results lie on this machine, which the rules do not name:
# {"hosts": {MACHINE: SSH-TARGET}, "results": {SERIES-RESULTS: DIRECTORY}}, untracked (machines.example.json).
LOCAL_FILE = os.path.join(BENCH, "machines.local.json")


def with_local(rules, path=None):
    """The rules with this machine's local names: each machine's host, its target from the local file, or its own
    target such as local, before its options, and a series' results directory where the local file maps it. A
    machine that has neither keeps no host, and running on it says which key to add."""
    path = path or os.environ.get("HYPRMASQ_BENCH_MACHINES") or LOCAL_FILE
    local = load(path) if os.path.exists(path) else {}
    for name, machine in rules.get("machines", {}).items():
        target = machine.get("target") or local.get("hosts", {}).get(name)
        if "host" not in machine:
            machine["host"] = f"{target},{machine['options']}" if target else None
    for series in rules.get("series", {}).values():
        series["results"] = local.get("results", {}).get(series["results"], series["results"])
    return rules


def machine_host(rules, name):
    """A machine's host, as dispatch.py takes it, or the error that names the key the local file lacks."""
    host = rules["machines"][name].get("host")
    if not host:
        sys.exit(f"machine {name} has no host: add \"{name}\": \"SSH-TARGET\" to \"hosts\" in "
                 f"{os.path.relpath(LOCAL_FILE)} (see machines.example.json), or set HYPRMASQ_BENCH_MACHINES")
    return host


def load(path):
    with open(path) as f:
        return json.load(f)


def task_spec(task):
    """A task of the rules, TASK or TASK:VARIANT, as (task, variant)."""
    name, _, variant = task.partition(":")
    return name, variant or None


def machine_of(series, tool):
    """The machine a tool runs on in a series: the series' machine, or the one it names for that tool."""
    return series.get("tool_machines", {}).get(tool, series["machine"])


def machines_of(series, tools):
    return sorted({machine_of(series, tool) for tool in tools})


def tool_sets(rules, series, tool):
    """A tool's sets in a series: the series' own, for a slice that runs other tasks, or else the tool's."""
    return series.get("sets", {}).get(tool) or rules["tools"][tool]["sets"]


def sets_of(rules, series, tools):
    """The selected tools' sets: (tool, set name, set), in the rules' order."""
    return [(tool, name, spec) for tool in rules["tools"] if tool in tools
            for name, spec in tool_sets(rules, series, tool).items()]


def round_order(rules, series, tools, round_number):
    """The sets of a round, rotated by the round, so that each tool's sets come at every place in the order over the
    rounds: a tool's sets stay together, as they share a tool's setup."""
    names = [t for t in rules["tools"] if t in tools]
    shift = (round_number - 1) % len(names)
    ordered = names[shift:] + names[:shift]
    return [(tool, name, spec) for tool in ordered for name, spec in tool_sets(rules, series, tool).items()]


def source_path(source):
    """A source's checkout: its path in the rules, relative to this checkout's root unless it is absolute."""
    return os.path.normpath(os.path.join(os.path.dirname(BENCH), source["path"]))


def out_of(series, tool, set_name):
    return os.path.join(BENCH, "results", series["results"], tool, set_name)


def dispatch_command(rules, series, tool, set_name, spec, reps, build_only=False):
    """The dispatch.py command of one set of a series."""
    tasks = spec["tasks"]
    host = rules["machines"][machine_of(series, tool)]
    out = out_of(series, tool, set_name)
    # A single --task writes into --out itself, and these must lie where the other machines' tasks of the set lie.
    if len(tasks) == 1:
        out = os.path.join(out, run.results_name(*task_spec(tasks[0])))
    source = rules["sources"][rules["tools"][tool]["source"]]
    command = [sys.executable, os.path.join(BENCH, "dispatch.py"), "--host", machine_host(rules, machine_of(series, tool)),
               "--source", source_path(source),
               "--out", out, "--backend", rules["tools"][tool]["backend"], "--prompt", rules["prompt"],
               "--desktop", rules["desktop"], "--display", rules["display"], "--browser", spec.get("browser", "firefox"),
               "--timeout", str(rules["agent_timeout_seconds"])]
    for t in tasks:
        command += ["--task", t]
    for agent in rules["agents"]:
        command += ["--agent", agent]
    if series.get("chromium_options"):
        command += ["--chromium-options=" + series["chromium_options"]]
    if build_only:
        command += ["--reps", "0"]
    else:
        for rep in reps:
            command += ["--rep", str(rep)]
    limits = ",".join(f"{pattern}={n}" for pattern, n in host.get("max_concurrent", {}).items())
    if limits:
        command += ["--max-concurrent", limits]
    return command


def rounds_of(series, selected):
    """The rounds to run: those selected, or the series' own (rounds), as for a series that continues another from its
    third round, or else 1 to reps. A run's repetition is its round."""
    return selected or series.get("rounds") or list(range(1, series["reps"] + 1))


def planned_runs(rules, series, tools, rounds):
    """Every run the selection holds: (round, tool, set name, task, agent, its directory, machine)."""
    for round_number in rounds:
        for tool, set_name, spec in sets_of(rules, series, tools):
            for task in spec["tasks"]:
                for agent in rules["agents"]:
                    harness, model, effort = run.parse_agent(agent)
                    directory = os.path.join(out_of(series, tool, set_name), run.results_name(*task_spec(task)),
                                             run.run_name(harness, model, round_number, effort))
                    yield round_number, tool, set_name, task, agent, directory, machine_of(series, tool)


def log_path(series):
    return os.path.join(BENCH, "results", series["results"], "exclusions.jsonl")


def exclude(series, entry, why):
    """Moves a run aside with why, and logs it: only such a run may run again."""
    round_number, tool, set_name, task, agent, directory, machine = entry
    target = os.path.join(BENCH, "results", series["results"], "excluded", f"round-{round_number}", tool, set_name,
                          run.results_name(*task_spec(task)), os.path.basename(directory))
    attempt = len(glob.glob(target + "*"))
    if os.path.exists(directory):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(directory, f"{target}.{attempt}")
    record = {"time": datetime.datetime.now(datetime.timezone.utc).isoformat(), "round": round_number, "tool": tool,
              "set": set_name, "task": task, "agent": agent, "machine": machine, "attempt": attempt, **why}
    with open(log_path(series), "a") as f:
        f.write(json.dumps(record) + "\n")
    print(f"excluded {tool}/{set_name}/{task} {agent} round {round_number} on {machine}: {why}", flush=True)


def infrastructure(directory):
    """Why a run needs running again: {"kind", "detail"}, or None for a run that stands. A run with no summary.json has no
    result; the harness's stop has report.infrastructure_failure's kinds."""
    if not os.path.isdir(directory):
        return None  # it never started: dispatch.py runs it, with nothing to exclude
    summary = os.path.join(directory, "summary.json")
    if not os.path.exists(summary):
        return {"kind": "no_summary", "detail": "the run left no summary.json"}
    found = load(summary).get("infrastructure")
    if found:
        return found
    return other_load(directory)


def other_load(directory):
    """A run that shared its host with work outside the series, the same rule for every tool: a QEMU process that is
    none of the benchmark's VMs, or the host's guard holding for another benchmark on the host, at any of the run's samples
    (run.py's host_load). None for a run with no samples, as before they were taken."""
    path = os.path.join(directory, "run.json")
    if not os.path.exists(path):
        return None
    sampled = load(path).get("host_load") or {}
    if sampled.get("max_other_qemu"):
        return {"kind": "other_load", "detail": f"{sampled['max_other_qemu']} other QEMU process(es) on the host"}
    if sampled.get("guard"):
        return {"kind": "other_load", "detail": "the host's guard held for another benchmark on the host"}
    return None


def counts_as_pass(failures):
    """Whether a pass with these failures counts towards MAX_PASSES: not when a subscription's limit stopped any of its
    runs, as the others failed in the same pass and run again once it reset, so that a limit never ends a series."""
    return not any(f.get("kind") == "rate_limit" for f in failures)


def wait_for_limits(failures):
    """Waits until the limits that stopped runs have reset, by the time they gave, or LIMIT_WAIT_SECONDS."""
    limits = [f for f in failures if f.get("kind") == "rate_limit"]
    if not limits:
        return
    until = max((f.get("resets_at") or 0 for f in limits), default=0)
    wait = max(until - time.time() + 60, 60) if until else LIMIT_WAIT_SECONDS
    print(f"a subscription's limit stopped {len(limits)} runs: waiting {wait / 60:.0f} minutes", flush=True)
    time.sleep(wait)


def check_versions(rules, machine):
    """A machine's harness versions against the rules, as the failures. The PATH is its dispatch's."""
    host = rules["machines"][machine]
    target, *options = machine_host(rules, machine).split(",")
    bin_dir = next((o.split("=", 1)[1] for o in options if o.startswith("bin=")), None)
    # As dispatch.Host.sh puts it first: an absolute directory as it is, another under the home directory.
    first = (bin_dir if bin_dir.startswith("/") else "$HOME/" + bin_dir) + ":" if bin_dir else ""
    prefix = f'export PATH="{first}$PATH"; '
    script = prefix + "claude --version; codex --version"
    command = ["bash", "-c", script] if target == "local" else ["ssh", "-n", "-o", "BatchMode=yes", target, script]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    found = done.stdout.strip().splitlines()
    want = [rules["harnesses"]["claude"], rules["harnesses"]["codex"]]
    return [] if found == want else [f"{machine} has {found}, the rules {want}"]


def check_sources(rules):
    """The sources' checkouts against the rules: each at its commit and clean, as dispatch.py ships what it finds."""
    failures = []
    for name, source in rules["sources"].items():
        state = run.source_commit(source_path(source))
        if state["commit"] != source["commit"] or state["dirty"]:
            failures.append(f"source {name} at {source['path']} is at {state['commit']}"
                            f"{' with changes' if state['dirty'] else ''}, the rules {source['commit']}")
    bench = run.source_commit(None)
    if bench["dirty"]:
        failures.append("this checkout has changes")
    return failures


def started(series, round_number, tool):
    return os.path.join(BENCH, "results", series["results"], f"round-{round_number}-{tool}.started")


def pace(series, tools, round_number):
    """Waits, for a tool that the series paces by another (pace: {tool: leader}), until the leader has started this
    round, so that a tool on a faster machine never runs ahead of the other's rounds; then says that the selected tools
    started it. Both run at once as processes of their own on their machines."""
    for tool in tools:
        leader = series.get("pace", {}).get(tool)
        if not leader or leader in tools:
            continue
        said = False
        while not os.path.exists(started(series, round_number, leader)):
            if not said:
                print(f"round {round_number}: {tool} waits for {leader} to start it", flush=True)
                said = True
            time.sleep(PACE_POLL_SECONDS)
    for tool in tools:
        path = started(series, round_number, tool)
        if not os.path.exists(path):
            with open(path, "w") as f:
                f.write(datetime.datetime.now(datetime.timezone.utc).isoformat() + "\n")


def gate_overlaps(counts):
    """The other VMs that ran during a calibration, which calibrate.py records and whose numbers do not count."""
    return sorted({pid for c in counts for pid in c.get("other_vms") or []})


def gate_passes(counts, workers, max_slowdown):
    """Whether a calibration allows a machine's workers: the median call with that many VMs at once at most
    max_slowdown slower than with one."""
    by_vms = {c["vms"]: c["all"]["median"] for c in counts}
    return by_vms[workers] <= by_vms[1] * (1 + max_slowdown)


def host_parts(host):
    target, *options = host.split(",")
    return target, dict(o.split("=", 1) for o in options if "=" in o)


def run_gate(rules, series, tool):
    """The calibration gate of a tool's machine, before its first round: for each of the gate's tasks, calibrate.py with
    1 VM and with the machine's workers, at its cores, and the series stops if any is slower by more than the gate
    allows. Its result is kept in the results directory and stands for the series."""
    name = machine_of(series, tool)
    machine = rules["machines"][name]
    gate = machine.get("gate")
    if not gate or gate.get("backend") != rules["tools"][tool]["backend"]:
        return
    path = os.path.join(BENCH, "results", series["results"], f"gate-{name}.json")
    if os.path.exists(path):
        found = load(path)
        if not found["passed"]:
            sys.exit(f"the calibration gate of {name} failed before: {path}")
        return
    for set_name, spec in tool_sets(rules, series, tool).items():
        subprocess.run(dispatch_command(rules, series, tool, set_name, spec, [], build_only=True),
                       cwd=os.path.dirname(BENCH), check=True, stdout=subprocess.DEVNULL)
    target, options = host_parts(machine_host(rules, name))
    bench_commit = run.source_commit(None)["commit"]
    source = rules["sources"][rules["tools"][tool]["source"]]
    if target == "local":
        prefix, tree, source_tree = [], BENCH, source_path(source)
    else:
        base = "$HOME/.cache/hyprmasq-bench/trees"
        prefix = ["ssh", "-n", "-o", "BatchMode=yes", target]
        tree, source_tree = f"{base}/{bench_commit}/bench", f"{base}/{source['commit']}"
    results = []
    for task in gate["tasks"]:
        task_name, _, browser = task.partition("@")
        task_name, variant = task_spec(task_name)
        out = f"/tmp/hyprmasq-bench-gate-{series['results'].replace('/', '-')}-{task.replace(':', '-').replace('@', '-')}"
        script = (f"cd {tree} && rm -rf {out} && python3 calibrate.py --source {source_tree} --desktop {rules['desktop']} "
                  f"--backend {gate['backend']} --task {task_name} {'--variant ' + variant if variant else ''} "
                  f"--browser {browser or 'firefox'} --vms 1,{machine['workers']} --cores {options.get('cores', machine.get('cores', 2))} "
                  f"--rounds {gate.get('rounds', 8)} --lock {options.get('lock', '/tmp/vm.lock')} --out {out} > /dev/null && "
                  f"cat {out}/calibration.json")
        print(f"{name}: calibration gate, {task}", flush=True)
        done = subprocess.run(prefix + ["bash", "-c", script] if not prefix else prefix + [script],
                              capture_output=True, text=True, check=False)
        if done.returncode != 0:
            sys.exit(f"the calibration gate of {name} could not calibrate {task}: {done.stderr[-500:]}")
        counts = json.loads(done.stdout)["counts"]
        # A calibration that shared the machine with other VMs measures them too: it is not kept, and runs again.
        overlaps = gate_overlaps(counts)
        if overlaps:
            sys.exit(f"the calibration gate of {name} ran beside other VMs on {task} (processes {', '.join(map(str, overlaps))}): "
                     f"run it again once the machine is free")
        results.append({"task": task, "medians": {c["vms"]: c["all"]["median"] for c in counts},
                        "passed": gate_passes(counts, machine["workers"], gate["max_slowdown"])})
    record = {"machine": name, "workers": machine["workers"], "max_slowdown": gate["max_slowdown"], "tasks": results,
              "passed": all(r["passed"] for r in results), "time": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with open(path, "w") as f:
        json.dump(record, f, indent=1)
    if not record["passed"]:
        sys.exit(f"the calibration gate of {name} failed: {json.dumps(results)}")


def run_round(rules, series, tools, round_number, log):
    """One round: the selected tools' sets, one set after the other. Returns when all have ended."""
    for tool, set_name, spec in round_order(rules, series, tools, round_number):
        command = dispatch_command(rules, series, tool, set_name, spec, [round_number])
        print(f"round {round_number}: {tool}/{set_name}", flush=True)
        with open(log, "a") as f:
            f.write(shlex.join(command) + "\n")
            f.flush()
            subprocess.run(command, cwd=os.path.dirname(BENCH), stdout=f, stderr=subprocess.STDOUT, check=False)


def main_run(rules, series, tools, rounds):
    machines = machines_of(series, tools)
    failures = check_sources(rules) + [f for machine in machines for f in check_versions(rules, machine)]
    if failures:
        sys.exit("refusing to start:\n" + "\n".join(failures))
    base = os.path.join(BENCH, "results", series["results"])
    os.makedirs(base, exist_ok=True)
    for tool in tools:
        run_gate(rules, series, tool)
    for round_number in rounds:
        pace(series, tools, round_number)
        passes = 0
        while True:
            # Every pass of every round runs on the harness versions of the rules, however long the series takes.
            failures = [f for machine in machines for f in check_versions(rules, machine)]
            if failures:
                sys.exit(f"stopping before round {round_number}, a harness changed:\n" + "\n".join(failures))
            log = os.path.join(base, f"round-{round_number}-{'-'.join(tools)}.log")
            run_round(rules, series, tools, round_number, log)
            found = []
            for entry in planned_runs(rules, series, tools, [round_number]):
                why = infrastructure(entry[5])
                if why:
                    exclude(series, entry, why)
                    found.append(why)
                elif not os.path.isdir(entry[5]):
                    found.append({"kind": "not_run"})
            if not found:
                print(f"round {round_number} is complete", flush=True)
                break
            # A pass that a subscription's limit stopped does not count: the round waits for the limit and goes on.
            passes += counts_as_pass(found)
            if passes >= MAX_PASSES:
                sys.exit(f"round {round_number}: {len(found)} runs still missing after {passes} passes: see {log_path(series)}")
            wait_for_limits(found)


def main_status(rules, series, tools):
    rows = {}
    for round_number, tool, set_name, task, agent, directory, machine in planned_runs(rules, series, tools, rounds_of(series, None)):
        row = rows.setdefault((tool, set_name), {"planned": 0, "done": 0, "passed": 0})
        row["planned"] += 1
        path = os.path.join(directory, "summary.json")
        if os.path.exists(path):
            row["done"] += 1
            row["passed"] += bool(load(path).get("passed"))
    for (tool, set_name), row in rows.items():
        print(f"{tool}/{set_name}: {row['done']}/{row['planned']} done, {row['passed']} passed")
    path = log_path(series)
    if os.path.exists(path):
        with open(path) as f:
            print(f"exclusions: {sum(1 for _ in f)}, in {path}")


def estimate(rules, series, tools):
    """Expected runs and hours of a series on each of its machines: each run's expected seconds, from the rules, over
    the machine's workers. Machines run at once, so the series takes the longest."""
    table = rules["estimate"]
    found = {}
    for _, tool, _, task, _, _, name in planned_runs(rules, series, tools, rounds_of(series, None)):
        machine = rules["machines"][name]
        runs, seconds = found.get(name, (0, 0.0))
        found[name] = (runs + 1, seconds + (table["agent_seconds"][tool][task] + table["overhead_seconds"]) * machine["time_factor"])
    result = {}
    for name, (runs, seconds) in sorted(found.items()):
        workers = rules["machines"][name]["workers"]
        hours = seconds / workers / 3600
        print(f"{runs} runs on {name} with {workers} workers: {hours:.1f} h, {hours / len(rounds_of(series, None)):.1f} h a round")
        result[name] = (runs, hours)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("list", "check", "build", "run", "status", "estimate"))
    parser.add_argument("series", help="main, cross-check or others: a key of the rules' series")
    parser.add_argument("--rules", default=DEFAULT_RULES)
    parser.add_argument("--tool", action="append", help="by default, every tool of the rules, in their order")
    parser.add_argument("--round", action="append", type=int, help="by default, all rounds")
    args = parser.parse_args()
    rules = with_local(load(args.rules))
    if args.series not in rules["series"]:
        parser.error(f"no series {args.series} in the rules: {', '.join(rules['series'])}")
    series = rules["series"][args.series]
    tools = args.tool or series["tools"]
    unknown = [t for t in tools if t not in series["tools"]]
    if unknown:
        parser.error(f"no tool {', '.join(unknown)} in series {args.series}: {', '.join(series['tools'])}")
    rounds = rounds_of(series, args.round)
    if args.command == "list":
        for round_number in rounds:
            print(f"# round {round_number}")
            for tool, set_name, spec in round_order(rules, series, tools, round_number):
                print(shlex.join(dispatch_command(rules, series, tool, set_name, spec, [round_number])))
        print(f"{sum(1 for _ in planned_runs(rules, series, tools, rounds))} runs", file=sys.stderr)
    elif args.command == "check":
        failures = check_sources(rules) + [f for m in machines_of(series, tools) for f in check_versions(rules, m)]
        print("\n".join(failures) if failures else "the sources and the harnesses are as the rules say")
        sys.exit(1 if failures else 0)
    elif args.command == "build":
        for tool, set_name, spec in sets_of(rules, series, tools):
            print(f"{machine_of(series, tool)}: {tool}/{set_name}", flush=True)
            subprocess.run(dispatch_command(rules, series, tool, set_name, spec, [], build_only=True),
                           cwd=os.path.dirname(BENCH), check=True)
    elif args.command == "run":
        main_run(rules, series, tools, rounds)
    elif args.command == "status":
        main_status(rules, series, tools)
    elif args.command == "estimate":
        estimate(rules, series, tools)


if __name__ == "__main__":
    main()
