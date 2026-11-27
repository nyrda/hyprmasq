#!/usr/bin/env python3
"""Reads replayed runs: how many recordings replay to a pass, how much their times vary, and what two builds differ by.

    replay_report.py DIR [--against DIR] [--json OUT]

DIR is a results directory of replays (run.py's harness replay), or the directory of the tasks' results directories
that dispatch.py makes. It prints three tables:

- Coverage: of the recordings, by task and by the client that recorded them, how many replayed to a pass in every
  repetition, and why the others did not: the replay diverged from the recording, or the task's checker failed it.
- Repeatability: for the recordings with at least two repetitions that passed without diverging, the spread between
  repetitions of the time in tools and of each part of the act calls' time that analyze.py splits it into. And each
  repetition against the first, as if they were two builds: the difference that the method finds where there is none.
- With --against, the difference DIR minus that directory, over the recordings that are a sample in both, each by
  the mean of its repetitions, with a 95 % bootstrap interval over the recordings.

Only a replay that passed and did not diverge is a sample of the tool's time. See "Replaying a recorded run" in
bench/README.md.
"""

import json
import math
import os
import random
import statistics
import sys

import analyze
import report

# The times of a run, in seconds: the time in tools, and the parts of its act calls' time (analyze.split).
PARTS = ("wait", "watch", "input", "pauses", "rest")
METRICS = ("tool",) + PARTS
BOOTSTRAP = 10000


def run_dirs(root):
    """The run directories under a results directory, or under a directory of results directories."""
    found = []
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if os.path.exists(os.path.join(path, "summary.json")):
            found.append(path)
        elif os.path.isdir(path) and not os.path.exists(os.path.join(path, "run.json")):
            found += [os.path.join(path, n) for n in sorted(os.listdir(path))
                      if os.path.exists(os.path.join(path, n, "summary.json"))]
    return found


def times(run_dir):
    """The seconds of a run from its MCP log, unrounded: in tools, and by part of its act calls, with how many of them
    act watched after. None for the parts of a log that analyze cannot split."""
    tap = report.load_jsonl(os.path.join(run_dir, "mcp.jsonl"))
    calls = [c for c in report.tool_calls(tap) if c.get("end")]
    out = {"tool": report.union_seconds([(c["start"], c["end"]) for c in calls]), "watched_acts": 0}
    args, structured, started = {}, {}, {}
    for entry in tap:
        msg = entry.get("msg") or {}
        if entry["dir"] == "request" and msg.get("method") == "tools/call":
            args[entry["t"]] = msg["params"].get("arguments") or {}
            started[msg.get("id")] = entry["t"]
        elif entry["dir"] == "response" and msg.get("id") in started:
            structured[started.pop(msg["id"])] = (msg.get("result") or {}).get("structuredContent") or {}
    try:
        watch_ms = analyze.act_tool(tap)
        parts = dict.fromkeys(PARTS, 0.0)
        for c in calls:
            if c["tool"] != "act":
                continue
            split = analyze.split(c, args.get(c["start"], {}), structured.get(c["start"], {}), watch_ms)
            for part in PARTS:
                parts[part] += split[part]
            out["watched_acts"] += split["watch"] > 0
        out.update(parts)
    except ValueError:
        out.update(dict.fromkeys(PARTS))
    return out


def sample(run_dir):
    """A replayed run: its recording, its outcome and its times. None for a run that is no replay."""
    with open(os.path.join(run_dir, "summary.json")) as f:
        summary = json.load(f)
    outcome = summary.get("replay")
    if outcome is None:
        return None
    diverged = outcome.get("diverged")
    return {
        "task": os.path.basename(os.path.dirname(run_dir)), "recording": summary["model"], "rep": summary["rep"],
        "client": (outcome.get("client") or "unknown").split(" ")[0],
        "passed": bool(summary.get("passed")), "diverged": diverged.get("kind") if diverged else None,
        "detail": (diverged or {}).get("detail"), "failures": summary.get("failures") or [],
        "calls": summary.get("calls"), "arguments": outcome.get("arguments") or {},
        # A replay that left the recording's way, or that the checker failed, is no sample of the tool's time.
        "valid": bool(summary.get("passed")) and not diverged,
        **times(run_dir),
    }


def load(root):
    return [s for s in map(sample, run_dirs(root)) if s]


def by_recording(samples):
    groups = {}
    for s in samples:
        groups.setdefault((s["task"], s["recording"]), []).append(s)
    return groups


def coverage(samples):
    """A row per task and client: its recordings, how many passed in every repetition, in some, and in none, and the
    repetitions by outcome: passed, diverged by kind, and failed by the checker without diverging."""
    rows = {}
    for (task, _), runs in by_recording(samples).items():
        row = rows.setdefault((task, runs[0]["client"]), {"recordings": 0, "all": 0, "some": 0, "none": 0, "runs": 0,
                                                          "passed": 0, "diverged": {}, "failed": 0})
        valid = sum(s["valid"] for s in runs)
        row["recordings"] += 1
        row["all" if valid == len(runs) else "some" if valid else "none"] += 1
        for s in runs:
            row["runs"] += 1
            if s["valid"]:
                row["passed"] += 1
            elif s["diverged"]:
                row["diverged"][s["diverged"]] = row["diverged"].get(s["diverged"], 0) + 1
            else:
                row["failed"] += 1
    return rows


def repeatability(samples, metrics=METRICS):
    """The spread between the repetitions of each recording that has at least two samples, for each metric: the
    recordings, the mean of their means, the pooled standard deviation within a recording, the median and the largest
    of their ranges, and the standard deviation of the suite's sum, each repetition's total over the recordings that
    are a sample in every repetition."""
    groups = {k: [s for s in v if s["valid"]] for k, v in by_recording(samples).items()}
    groups = {k: v for k, v in groups.items() if len(v) >= 2}
    reps = sorted({s["rep"] for v in groups.values() for s in v})
    full = [v for v in groups.values() if sorted(s["rep"] for s in v) == reps]
    out = {}
    for metric in metrics:
        values = [[s[metric] for s in v] for v in groups.values() if all(s[metric] is not None for s in v)]
        if not values:
            continue
        freedom = sum(len(v) - 1 for v in values)
        pooled = math.sqrt(sum(statistics.variance(v) * (len(v) - 1) for v in values) / freedom)
        ranges = [max(v) - min(v) for v in values]
        sums = [sum(s[metric] for v in full for s in v if s["rep"] == rep) for rep in reps] if all(
            s[metric] is not None for v in full for s in v) else []
        out[metric] = {
            "recordings": len(values), "mean": statistics.mean(statistics.mean(v) for v in values), "pooled_sd": pooled,
            "median_range": statistics.median(ranges), "max_range": max(ranges),
            "suite_recordings": len(full), "suite_sums": sums,
            "suite_sd": statistics.stdev(sums) if len(sums) > 1 else None,
        }
    return out


def bootstrap(values, seed=1):
    """The mean of the values and its 95 % bootstrap interval over them."""
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(BOOTSTRAP))
    return statistics.mean(values), means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def difference(samples, base, metrics=METRICS + ("watched_acts",)):
    """The difference of samples against base for each metric, over the recordings that have a sample in both, each
    by the mean of its samples: the pairs, base's mean, and the mean difference with its interval."""
    a = {k: [s for s in v if s["valid"]] for k, v in by_recording(samples).items()}
    b = {k: [s for s in v if s["valid"]] for k, v in by_recording(base).items()}
    pairs = sorted(k for k in a if a[k] and b.get(k))
    out = {}
    for metric in metrics:
        keys = [k for k in pairs if all(s[metric] is not None for s in a[k] + b[k])]
        if not keys:
            continue
        ours = [statistics.mean(s[metric] for s in a[k]) for k in keys]
        theirs = [statistics.mean(s[metric] for s in b[k]) for k in keys]
        mean, low, high = bootstrap([x - y for x, y in zip(ours, theirs)])
        out[metric] = {"pairs": len(keys), "base_mean": statistics.mean(theirs), "mean": mean, "low": low, "high": high}
    return out


def between_repetitions(samples, metrics=("tool",)):
    """Each repetition against the first, as difference() compares two builds: what the method measures between two
    sets of replays of the same build, which a difference between builds has to exceed."""
    reps = sorted({s["rep"] for s in samples})
    first = [s for s in samples if s["rep"] == reps[0]] if reps else []
    return {rep: difference([s for s in samples if s["rep"] == rep], first, metrics) for rep in reps[1:]}


def table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    return "\n".join(lines + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows])


def coverage_table(samples):
    rows, total = [], {"recordings": 0, "all": 0, "some": 0, "none": 0, "runs": 0, "passed": 0, "failed": 0, "diverged": {}}
    for (task, client), row in sorted(coverage(samples).items()):
        rows.append(coverage_row(task, client, row))
        for key in ("recordings", "all", "some", "none", "runs", "passed", "failed"):
            total[key] += row[key]
        for kind, n in row["diverged"].items():
            total["diverged"][kind] = total["diverged"].get(kind, 0) + n
    rows.append(coverage_row("all", "", total))
    return table(["Task", "Client", "Recordings", "Pass every time", "Pass sometimes", "Never pass", "Replays",
                  "Passed", "Diverged", "Failed by the checker"], rows)


def coverage_row(task, client, row):
    diverged = ", ".join(f"{n} {kind}" for kind, n in sorted(row["diverged"].items())) or "0"
    return [task, client, row["recordings"], row["all"], row["some"], row["none"], row["runs"], row["passed"], diverged,
            row["failed"]]


def repeatability_table(samples):
    rows = []
    for metric, r in repeatability(samples).items():
        suite = "–"
        if r["suite_sd"] is not None:
            mean = statistics.mean(r["suite_sums"])
            suite = (f"{mean:.1f} s ± {r['suite_sd']:.2f} s"
                     + (f" ({100 * r['suite_sd'] / mean:.2f} %)" if mean else "") + f" over {r['suite_recordings']}")
        rows.append([metric, r["recordings"], f"{r['mean']:.2f}", f"{r['pooled_sd']:.3f}",
                     f"{100 * r['pooled_sd'] / r['mean']:.2f} %" if r["mean"] else "–",
                     f"{r['median_range']:.3f}", f"{r['max_range']:.3f}", suite])
    return table(["Seconds a run", "Recordings", "Mean", "SD between repetitions", "SD of the mean", "Median range",
                  "Largest range", "Suite's sum, mean ± SD of the repetitions"], rows)


def difference_rows(differences, label=None):
    rows = []
    for metric, d in differences.items():
        unit = "" if metric == "watched_acts" else " s"
        share = f"{100 * d['mean'] / d['base_mean']:+.2f} %" if d["base_mean"] else "–"
        rows.append([label or metric, d["pairs"], f"{d['base_mean']:.3f}{unit}", f"{d['mean']:+.3f}{unit}",
                     f"[{d['low']:+.3f}, {d['high']:+.3f}]", share])
    return rows


def difference_table(samples, base):
    return table(["A run's", "Recordings", "Base", "Difference", "95 % interval", "Of the base"],
                 difference_rows(difference(samples, base)))


def repetitions_table(samples):
    rows = [row for rep, d in between_repetitions(samples).items() for row in difference_rows(d, f"{rep} against 1")]
    return table(["Repetition", "Recordings", "Seconds in tools", "Difference", "95 % interval", "Of the base"], rows)


def main(argv):
    options = {"--against": None, "--json": None}
    for option in options:
        if option in argv:
            i = argv.index(option)
            options[option] = argv[i + 1]
            del argv[i:i + 2]
    if len(argv) != 1:
        raise SystemExit(__doc__)
    samples = load(argv[0])
    if not samples:
        raise SystemExit(f"no replayed run under {argv[0]}")
    print("Coverage\n\n" + coverage_table(samples))
    for s in samples:
        if not s["valid"]:
            why = f"diverged ({s['diverged']}): {s['detail']}" if s["diverged"] else "failed: " + "; ".join(s["failures"])
            print(f"- {s['task']} {s['recording']} #{s['rep']}: {why}"[:400])
    print("\nRepeatability\n\n" + repeatability_table(samples))
    if len({s["rep"] for s in samples}) > 1:
        print("\nRepetitions against each other\n\n" + repetitions_table(samples))
    base = load(options["--against"]) if options["--against"] else None
    if base:
        print(f"\nDifference against {options['--against']}\n\n" + difference_table(samples, base))
    if options["--json"]:
        with open(options["--json"], "w") as f:
            json.dump({"samples": samples, "repeatability": repeatability(samples),
                       "difference": difference(samples, base) if base else None}, f, indent=1)


if __name__ == "__main__":
    if sys.argv[1:] in ([], ["--help"]):
        print(__doc__)
    else:
        main(sys.argv[1:])
