#!/usr/bin/env python3
"""Compares the results of builds that one dispatch ran: each build against the baseline, run by run.

    compare.py BASELINE [OTHER...] [--control DIR]

Each is a build's results directory, the --out that dispatch.py took for it: with several tasks, the directory that
holds the tasks' directories. A run of a build pairs with the baseline's run of the same task, agent and repetition,
which dispatch.py ran in one unit, one after the other on one machine. For each agent it prints the runs passed, the
passes lost and gained with what the checker said, and for the wall time, the time in tools, the tool calls, the
model's requests, the cost, the input tokens and the growth of the context the mean of the pairs' differences, a 95 %
bootstrap interval of it over the pairs, and its share of the baseline's mean.

--control is a second results directory of the baseline's own build, as a dispatch makes with the baseline's checkout
given twice as --source. It is compared first: what it differs from the baseline by is what two results of one build
differ by, the noise floor. Each difference of the other builds is then marked as inside or outside the control's
range, which is the end of the control's interval that lies farther from zero, taken to both sides.

It then prints the means by the runs' place in their unit, where run.json records it (dispatch.position).

Runs that the infrastructure stopped (summary.json's infrastructure) stay out, with the pairs they belong to, and are
counted. It reads summary.json as it is: report.py writes it again from a run's files.

It is for runs with a model. Replays of recorded runs (run.py's harness replay) are read with replay_report.py, which
compares builds by recording and leaves out the replays that diverged: here each recording would be an agent of its
own, which it says when it meets one.

What it cannot do. The interval says how well these pairs know the mean difference: half its width, as a share of the
baseline's mean, is the smallest effect they can tell from none, and a smaller one stays unseen however real. A
difference inside the control's range is not evidence of an effect. One outside it is one draw against one draw of the
noise, over many measures at once, and no test. See bench/README.md.
"""

import argparse
import collections
import json
import os
import random
import statistics
import sys

import run

RESAMPLES = 10000


def thousands(tokens):
    return tokens / 1000 if tokens is not None else None


# The measures compared: the column's name, the value of a run's summary.json or None, and the digits printed.
MEASURES = [
    ("Wall s", lambda s: s.get("wall_seconds"), 1),
    ("In tools s", lambda s: s.get("tool_seconds"), 1),
    ("Calls", lambda s: s.get("calls"), 2),
    ("Requests", lambda s: (s.get("context") or {}).get("request_count"), 2),
    ("Cost $", lambda s: (s.get("usage") or {}).get("cost_usd"), 4),
    ("Input k", lambda s: thousands((s.get("usage") or {}).get("input_tokens")), 1),
    ("Context growth k", lambda s: thousands((s.get("context") or {}).get("growth_input_tokens")), 2),
]


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_dirs(path):
    """The directories of the finished runs of a results directory."""
    if not os.path.isdir(path):
        return []
    return [os.path.join(path, name) for name in sorted(os.listdir(path))
            if os.path.exists(os.path.join(path, name, "summary.json"))]


def task_dirs(path):
    """The results directories of a build's --out by task: the directory itself if it holds runs, named by its
    source.json, or else its directories that hold runs, which dispatch.py names TASK[-VARIANT]."""
    if run_dirs(path):
        source = load_json(os.path.join(path, "source.json")) or {}
        return {run.results_name(source["task"], source.get("variant")) if source.get("task") else "": path}
    return {name: os.path.join(path, name) for name in sorted(os.listdir(path)) if run_dirs(os.path.join(path, name))}


class Results:
    """A build's finished runs by (task, agent, repetition), without those that the infrastructure stopped, which
    stopped lists."""

    def __init__(self, path, name):
        self.path, self.name = path, name
        self.runs, self.stopped, self.commits = {}, [], set()
        self.replays = 0  # runs that replayed a recording, which replay_report.py reads
        for task, directory in task_dirs(path).items():
            source = load_json(os.path.join(directory, "source.json")) or {}
            self.commits.add(source.get("commit"))
            for run_dir in run_dirs(directory):
                summary = load_json(os.path.join(run_dir, "summary.json"))
                if summary is None:
                    print(f"{run_dir}: summary.json cannot be read, left out", file=sys.stderr)
                    continue
                if summary.get("infrastructure"):
                    self.stopped.append((os.path.relpath(run_dir, path), summary["infrastructure"].get("kind")))
                    continue
                self.replays += summary.get("replay") is not None
                agent = run.agent_spec(summary["harness"], summary["model"], (summary.get("effort") or {}).get("requested"))
                dispatched = (load_json(os.path.join(run_dir, "run.json")) or {}).get("dispatch") or {}
                position, ran_after = dispatched.get("position"), dispatched.get("ran_after")
                self.runs[task, agent, summary["rep"]] = {
                    "summary": summary, "position": position, "builds": dispatched.get("builds"),
                    # Whether the runs before it in its unit's order ran right before it: not after a resume or a retry.
                    "in_place": position is not None and ran_after in (None, position - 1),
                }

    def commit(self):
        return ", ".join(c[:10] if c else "unrecorded" for c in sorted(self.commits, key=str)) or "unrecorded"


def interval(differences):
    """The 95 % bootstrap interval of the mean of the pairs' differences, resampling the pairs, or None for fewer than
    three. The same differences give the same interval."""
    if len(differences) < 3:
        return None
    rnd = random.Random(1)
    means = sorted(statistics.fmean(rnd.choices(differences, k=len(differences))) for _ in range(RESAMPLES))
    return means[int(0.025 * RESAMPLES)], means[int(0.975 * RESAMPLES) - 1]


def against(baseline, other, agent):
    """An agent's runs of a build against the baseline's: the pairs, the passes and their flips, and each measure's
    paired difference, None where no pair has the measure."""
    pairs = sorted(key for key in other.runs if key in baseline.runs and key[1] == agent)
    base = [baseline.runs[key]["summary"] for key in pairs]
    them = [other.runs[key]["summary"] for key in pairs]
    measures = {}
    for label, value, _ in MEASURES:
        both = [(value(b), value(o)) for b, o in zip(base, them)]
        both = [(b, o) for b, o in both if b is not None and o is not None]
        if not both:
            measures[label] = None
            continue
        differences = [o - b for b, o in both]
        base_mean, difference, ends = statistics.fmean(b for b, o in both), statistics.fmean(differences), interval(differences)
        measures[label] = {
            "pairs": len(both), "baseline": base_mean, "other": statistics.fmean(o for b, o in both),
            "difference": difference, "interval": ends,
            "percent": 100 * difference / base_mean if base_mean else None,
            # Half the interval's width as a share of the baseline's mean: the effect these pairs can tell from none.
            "resolves": 100 * (ends[1] - ends[0]) / 2 / base_mean if ends and base_mean else None,
            # How far from zero the interval reaches: as a control's, what two results of one build differ by.
            "reach": max(abs(ends[0]), abs(ends[1])) if ends else None,
        }
    return {
        "pairs": pairs,
        "unpaired": (sum(1 for key in baseline.runs if key[1] == agent and key not in other.runs),
                     sum(1 for key in other.runs if key[1] == agent and key not in baseline.runs)),
        "passed": (sum(1 for s in base if s["passed"]), sum(1 for s in them if s["passed"])),
        "lost": [(key, o) for key, b, o in zip(pairs, base, them) if b["passed"] and not o["passed"]],
        "gained": [(key, b) for key, b, o in zip(pairs, base, them) if o["passed"] and not b["passed"]],
        "measures": measures,
    }


def signed(value, digits):
    return f"{value:+.{digits}f}"


def percent(value):
    return f"{value:+.1f} %" if value is not None else "–"


def failures(summary):
    text = "; ".join(str(f) for f in summary.get("failures") or []) or "no failure recorded"
    return text if len(text) <= 240 else text[:239] + "…"


def run_label(key):
    return f"{key[0]} #{key[2]}" if key[0] else f"#{key[2]}"


def section(title, name, result, floor=None):
    """An agent's comparison of the build of that name with the baseline, against()'s result, as Markdown. floor is
    the control's result for the agent, which the differences are held against."""
    n = len(result["pairs"])
    out = [f"### {title}", ""]
    if not n:
        return out + [f"No run of {name} pairs with a run of the baseline for this agent.", ""]
    lost, gained = len(result["lost"]), len(result["gained"])
    line = (f"{n} pairs. Passed: baseline {result['passed'][0]}, {name} {result['passed'][1]} of {n}. "
            f"Passes lost {lost}, gained {gained}")
    if floor is not None:
        reach = max(len(floor["lost"]), len(floor["gained"]))
        line += (f": {'outside' if max(lost, gained) > reach else 'inside'} what the control shows, which lost "
                 f"{len(floor['lost'])} and gained {len(floor['gained'])}")
    out.append(line + ".")
    if any(result["unpaired"]):
        out.append(f"Without a partner, and left out: {result['unpaired'][0]} runs of the baseline and "
                   f"{result['unpaired'][1]} of {name}.")
    out += [f"- lost {run_label(key)}: {failures(summary)}" for key, summary in result["lost"]]
    out += [f"- gained {run_label(key)}, where the baseline failed: {failures(summary)}" for key, summary in result["gained"]]
    columns = ["Measure", "Pairs", "Baseline", name, "Difference", "95 % interval", "Of baseline", "Resolves"]
    columns += ["Control's range"] if floor is not None else []
    out += ["", "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for label, _, digits in MEASURES:
        m = result["measures"][label]
        if m is None:
            out.append(f"| {label} | 0 |" + " – |" * (len(columns) - 2))
            continue
        ends = m["interval"]
        cells = [label, str(m["pairs"]), f"{m['baseline']:.{digits}f}", f"{m['other']:.{digits}f}",
                 signed(m["difference"], digits),
                 f"{signed(ends[0], digits)} to {signed(ends[1], digits)}" if ends else "–",
                 percent(m["percent"]), f"±{m['resolves']:.1f} %" if m["resolves"] is not None else "–"]
        if floor is not None:
            reach = (floor["measures"][label] or {}).get("reach")
            cells.append("–" if reach is None else
                         f"{'outside' if abs(m['difference']) > reach else 'inside'} ±{reach:.{digits}f}")
        out.append("| " + " | ".join(cells) + " |")
    return out + [""]


def positions(results, agent):
    """The means of an agent's runs by their place in their unit, over all the builds given, and how often each build
    ran at each place, or None if no run records its place."""
    runs = [(r.name, record) for r in results for key, record in r.runs.items() if key[1] == agent]
    placed = [(name, record) for name, record in runs if record["in_place"]]
    if not placed:
        return None
    by_place = collections.defaultdict(list)
    for name, record in placed:
        by_place[record["builds"], record["position"]].append((name, record["summary"]))
    columns = ["Position", "Runs", "Passed"] + [label for label, _, _ in MEASURES] + [r.name for r in results]
    out = ["### By position in the unit", "",
           "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for (builds, position), at in sorted(by_place.items(), key=lambda item: (item[0][0] or 0, item[0][1])):
        cells = [f"{position} of {builds}" if builds else str(position), str(len(at)),
                 str(sum(1 for _, s in at if s["passed"]))]
        for _, value, digits in MEASURES:
            values = [value(s) for _, s in at if value(s) is not None]
            cells.append(f"{statistics.fmean(values):.{digits}f}" if values else "–")
        cells += [str(sum(1 for name, _ in at if name == r.name)) for r in results]
        out.append("| " + " | ".join(cells) + " |")
    out += ["", "The means of all the builds' runs at a position, and in the last columns how many runs each build "
                "has there: the builds' own differences cancel only where those counts are equal."]
    unrecorded = sum(1 for _, record in runs if record["position"] is None)
    displaced = len(runs) - len(placed) - unrecorded
    if unrecorded or displaced:
        out.append(f"Left out: {unrecorded} runs that record no position, and {displaced} that a resume or a retry "
                   "ran without the runs before them in their unit.")
    return out + [""]


def names(paths):
    """A name for each results directory: its last part, or the path as given where two end alike."""
    last = [os.path.basename(os.path.normpath(path)) for path in paths]
    return [name if last.count(name) == 1 else path for name, path in zip(last, paths)]


def compare(baseline, others=(), control=None):
    """The comparison as Markdown: the paths of the baseline's, the other builds' and the control's results."""
    paths = [baseline, *([control] if control else []), *others]
    results = [Results(path, name) for path, name in zip(paths, names(paths))]
    base, control_results = results[0], results[1] if control else None
    out = ["# Comparison of builds", ""]
    for r in results:
        role = "Baseline" if r is base else "Control" if r is control_results else "Build"
        line = f"- {role} {r.name}: {r.path}, commit {r.commit()}, {len(r.runs)} runs"
        if r.stopped:
            kinds = collections.Counter(kind or "unknown" for _, kind in r.stopped)
            line += (f", and {len(r.stopped)} left out that the infrastructure stopped ("
                     + ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items())) + "): "
                     + ", ".join(name for name, _ in r.stopped))
        out.append(line)
    if any(r.replays for r in results):
        out += ["", "These results hold replays of recorded runs. Each recording counts as an agent of its own here, and "
                    "a replay that diverged from its recording counts as a run that failed: replay_report.py reads "
                    "replays by recording, and leaves the diverged ones out."]
    if control_results and control_results.commits != base.commits:
        out += ["", f"The control is not the baseline's build: commit {control_results.commit()} against "
                    f"{base.commit()}. What it differs by is then not the noise alone."]
    out += ["", "A difference is a build's run minus the baseline's run of the same task, agent and repetition, and "
                "the mean is over those pairs. The interval is a 95 % bootstrap interval of that mean over the pairs. "
                "Resolves is half its width as a share of the baseline's mean: a real difference smaller than that "
                "cannot be told from none with these pairs."]
    if control_results:
        out += ["The control is a second result of the baseline's own build, so what it differs by is noise. Its range "
                "for a measure is the end of its interval farther from zero, taken to both sides. A difference inside "
                "that range is not evidence of an effect."]
    else:
        out += ["No control was given, so nothing here says how much two results of one build differ: an interval "
                "that excludes zero is not yet an effect."]
    out.append("")
    agents = list(dict.fromkeys(key[1] for key in sorted(base.runs, key=lambda key: (key[1], key[0], key[2]))))
    placed = False
    for agent in agents:
        out += [f"## {agent}", ""]
        floor = None
        if control_results:
            floor = against(base, control_results, agent)
            out += section(f"Control: {control_results.name}, the noise floor", control_results.name, floor)
            if not floor["pairs"]:
                floor = None
        for r in results[2 if control else 1:]:
            out += section(r.name, r.name, against(base, r, agent), floor)
        by_position = positions(results, agent)
        placed = placed or by_position is not None
        out += by_position or []
    if not placed:
        out += ["The runs record no position in their unit (`dispatch.position` in `run.json`), so there are no means "
                "by position: an order of the builds that follows the agent would not show here.", ""]
    out += ["## Limits", "",
            "- The pairs tell a difference from none only if it is larger than Resolves. More pairs narrow it with "
            "the square root of their count: a quarter of it takes sixteen times the runs.",
            "- A difference inside the control's range is what two results of one build show too. One outside it is "
            "one draw against one draw of the noise: a second control can differ more.",
            "- About one interval in twenty of two equal builds excludes zero, and each agent has seven measures for "
            "every build here.",
            "- The pairs share a machine and an hour, not a state: a run's place in its unit, and what the provider "
            "cached from the run before it, are in the differences unless the order was balanced."]
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("baseline", help="the baseline's results directory")
    parser.add_argument("other", nargs="*", help="another build's results directory, of the same dispatch")
    parser.add_argument("--control", metavar="DIR", help="a second results directory of the baseline's own build")
    args = parser.parse_args()
    if not args.other and not args.control:
        parser.error("give a build to compare with the baseline, or --control")
    for path in (args.baseline, *args.other, *([args.control] if args.control else [])):
        if not os.path.isdir(path) or not task_dirs(path):
            parser.error(f"{path} holds no finished runs: give a build's --out")
    print(compare(args.baseline, args.other, args.control))


if __name__ == "__main__":
    main()
