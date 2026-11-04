#!/usr/bin/env python3
"""Renders the benchmark results in docs/benchmarks.md from the datasets that export.py writes, and the comparison
in docs/comparison.md from the runs that bench/laptop/analyze.py writes and from export.py's datasets of other
backends than hyprmasq, which its views take as runs on the VM's Hyprland.

    render.py [--check] [MARKDOWN...]

Each block of a Markdown file, by default docs/benchmarks.md and docs/comparison.md, between

    <!-- render DATASET VIEW [TASK] -->
    <!-- /render -->

is replaced with the view of DATASET.json in the data directory of the directory named after the page beside it,
docs/benchmarks/data for docs/benchmarks.md. DATASET can join several with commas. Figures are SVG files in that
directory, one for GitHub's light theme and one for its dark theme, which a <picture> element picks between, named
after the dataset, view and task, the dataset left out when there are several, unless an earlier block of the page
has that name: then the last of the datasets names it too. Views:

- scoreboard: a row per agent, for each task a bar of the share of its runs that passed, the share, the count where
  the tasks' counts differ, and the median wall time, and the cost per passed run. Where the tasks do not fit as
  columns, a row per task and a column per agent instead, with each agent's runs passed and cost below, and without
  the times where those columns are too narrow for them. A task an agent did not run reads "not run".
- glance: a row per agent, the best first by the mean of its tasks' shares of runs passed, with that mean, each
  task's share, the sum of its tasks' median wall times and its cost per passed run.
- overview: time with each row's runs passed beside its median, to set agents side by side on every task, each
  panel's scale fitting the agents whose median is at most 2.5 times the panel's typical one.
- time: each run's wall time and the medians of the time in hyprmasq's tools and outside them, a panel per task,
  or only TASK's.
- kinds: the form task's seconds per question of each kind, a bar per model. TASK names the task where the dataset
  holds several, such as form and form-no-dropdowns.
- act: what the act calls' time went to, as analyze.py splits it.
- costs: a table of the tokens and the cost of each agent's runs.
- table: a table of the medians of TASK's runs, a row per agent and effort.
- kinds-table: a table of the form task's seconds per question of each kind, of TASK's runs as kinds takes them.
- compare: each task's wall time per run beside that of the dataset TASK names, an earlier build's:
  `<!-- render AFTER compare BEFORE -->`.
- compare-table: a table of each task's runs passed and medians, before → after, as compare takes its datasets.
- comparison: for docs/comparison.md, a row per tool and where it ran for each task and agent, with its runs and median time and
  columns of uncached input, peak context, cost per run and the seconds the user was disturbed. With TASK, only
  that task's rows, as in the two views below.
- comparison-pooled: comparison with a row per tool for each task that holds the runs of every agent.
- comparison-time: each tool's median time inside its calls and outside them, and a dot per run.
- comparison-table: a table of the comparison's medians and ranges.
- probe: comparison of the tasks and agents that ran with hyprmasq and with another tool, a row per tool for each
  agent that holds the runs of every such task, for the tools that ran each of them.
- probe-tasks: comparison of those tasks and agents, a row per tool for each task and agent.
- probe-table: comparison-table of those tasks and agents.
- toolkits: the form task's wall time per run and its medians in hyprmasq's tools and outside them, a panel per
  toolkit, which the run's variant names.
- toolkits-table: a table of the form task's medians, a row per toolkit and agent, with the act calls' settle wait.

Where the datasets of a block hold several variants of a task, its runs are named by their variant too, such as
board-dnd, and TASK names them so. TASK may also name such a task, board, for any view but table of its variants
alone. The form task's variants without dropdowns, such as 25-no-dropdowns, are the task form-no-dropdowns. The
toolkit views take the form task's runs of every other variant. Every view names a task by TASK_NAMES where it has
the task, such as Form without dropdowns or File manager, as the comparison does, and by its own name otherwise.

--check renders in memory and fails when a Markdown block or figure differs from what is on disk, or when a figure
is drawn by no block; without it, render.py writes them and removes such figures. The figures use
only the standard library, and their text widths are estimated from Helvetica's metrics.
"""

import argparse
import html
import json
import math
import os
import re
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from report import fmt  # noqa: E402

# Display names of the models, in the order agents are listed in; others follow by their ids.
NAMES = {
    "gpt-6.1-sol": "gpt-6.1-sol",
    "gpt-6.1-terra": "gpt-6.1-terra",
    "gpt-6-astra": "gpt-6-astra",
    "claude-opus-5-5": "Opus 5.5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-haiku-5-5": "Haiku 5.5",
    "gpt-6-luna": "gpt-6-luna",
    "tensorx/deepseek/deepseek-v4.1-flash-1017": "DeepSeek v4.1 Flash",
    "opencode-go/kimi-k3.1-pro": "Kimi K3.1 Pro",
    "opencode-go/gemini-4-argon": "Gemini 4 Argon",
    "opencode-go/deepseek-v4.1-flash": "DeepSeek v4.1 Flash",
    "opencode-go/qwen3.8-flash": "Qwen 3.8 Flash",
}
EFFORTS = ["default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max"]
KINDS = ["checkbox", "slider", "text", "dropdown"]
# The milestone each task's table reports, with its column.
PHASES = {"form": ("all answered correctly", "All right at s"),
          "form-no-dropdowns": ("all answered correctly", "All right at s")}
# The form task's variants without dropdowns end in this, such as 25-no-dropdowns.
NO_DROPDOWNS = "-no-dropdowns"
# The names of the tasks in the figures and tables where a task's own name would not do, in the comparison's order.
TASK_NAMES = {"form": "Form with dropdowns", "form-no-dropdowns": "Form without dropdowns", "inkscape": "Inkscape",
              "files": "File manager"}
ACT_PARTS = [
    ("input", "Input pacing", "the sender's keys and clicks"),
    ("wait", "Settle wait", "until the window is quiet"),
    ("watch", "Redraw watch", "for a second redraw"),
    ("pause", "Pauses", "the agent asked for"),
    ("rest", "Rest", "captures, checks and transport"),
]

# GitHub's own greys for the chrome, so that the figures sit in its pages; a validated categorical palette for
# the data, stepped for each theme.
THEMES = {
    "light": {
        "surface": "#ffffff", "border": "#d1d9e0", "ink": "#1f2328", "ink2": "#59636e", "muted": "#818b98",
        "grid": "#eff2f5", "axis": "#d1d9e0", "tools": "#2a78d6", "outside": "#c8d1da", "dot": "#424a53",
        "good": "#1a7f37", "bad": "#d1242f",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
    },
    "dark": {
        "surface": "#0d1117", "border": "#3d444d", "ink": "#f0f6fc", "ink2": "#9198a1", "muted": "#7d8590",
        "grid": "#1c2128", "axis": "#3d444d", "tools": "#3987e5", "outside": "#454c56", "dot": "#d1d7e0",
        "good": "#3fb950", "bad": "#f85149",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
    },
}
FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'
WIDTH = 880
PAD = 24

# Helvetica's advance widths of the printable ASCII characters, in thousandths of an em.
_HELVETICA = (
    "278 278 355 556 556 889 667 191 333 333 389 584 278 333 278 278 556 556 556 556 556 556 556 556 556 556 278 "
    "278 584 584 584 556 1015 667 667 722 722 667 611 778 722 278 500 667 556 833 722 778 667 778 722 667 611 722 "
    "667 944 667 667 611 278 278 278 469 556 333 556 556 500 556 556 278 556 556 222 222 500 222 833 556 556 556 "
    "556 333 500 278 556 500 722 500 500 500 334 260 334 584"
)
WIDTHS = dict(zip((chr(c) for c in range(32, 127)), map(int, _HELVETICA.split())))


def text_width(s, size, bold=False):
    return sum(WIDTHS.get(c, 556) for c in s) / 1000 * size * (1.06 if bold else 1)


def num(v):
    """A number as SVG writes it: short, and the same on every run."""
    v = round(v, 1)
    return str(int(v)) if v == int(v) else str(v)


class Svg:
    """An SVG figure drawn once with classes, and written in each theme's colours."""

    def __init__(self, height=0):
        self.items, self.height = [], height

    def add(self, item):
        self.items.append(item)

    def text(self, x, y, s, cls="t", size=12.5, anchor="start", weight=None, rotate=None):
        attrs = f' text-anchor="{anchor}"' if anchor != "start" else ""
        attrs += f' font-weight="{weight}"' if weight else ""
        attrs += f' transform="rotate({num(rotate)} {num(x)} {num(y)})"' if rotate else ""
        self.add(f'<text x="{num(x)}" y="{num(y)}" class="{cls}" font-size="{size}"{attrs}>{html.escape(s)}</text>')

    def rect(self, x, y, w, h, cls, rx=0):
        r = f' rx="{num(rx)}"' if rx else ""
        self.add(f'<rect x="{num(x)}" y="{num(y)}" width="{num(max(w, 0))}" height="{num(h)}" class="{cls}"{r}/>')

    def bar(self, x, y, w, h, cls, round_end=True):
        """A bar from x to the right, square at its start, with a rounded end."""
        if w <= 0:
            return
        r = min(3, h / 2, w) if round_end else 0
        if not r:
            return self.rect(x, y, w, h, cls)
        self.add(
            f'<path d="M{num(x)} {num(y)}H{num(x + w - r)}Q{num(x + w)} {num(y)} {num(x + w)} {num(y + r)}'
            f'V{num(y + h - r)}Q{num(x + w)} {num(y + h)} {num(x + w - r)} {num(y + h)}H{num(x)}Z" class="{cls}"/>'
        )

    def line(self, x1, y1, x2, y2, cls):
        self.add(f'<line x1="{num(x1)}" y1="{num(y1)}" x2="{num(x2)}" y2="{num(y2)}" class="{cls}"/>')

    def circle(self, x, y, r, cls):
        self.add(f'<circle cx="{num(x)}" cy="{num(y)}" r="{num(r)}" class="{cls}"/>')

    def render(self, theme, title):
        c = THEMES[theme]
        style = (
            f"text{{font-family:{FONT};font-variant-numeric:tabular-nums}}"
            f".t{{fill:{c['ink']}}}.t2{{fill:{c['ink2']}}}.m{{fill:{c['muted']}}}"
            f".bg{{fill:{c['surface']};stroke:{c['border']}}}"
            f".grid{{stroke:{c['grid']};stroke-width:1}}.axis{{stroke:{c['axis']};stroke-width:1}}"
            f".tools{{fill:{c['tools']}}}.out{{fill:{c['outside']}}}"
            f".dot{{fill:{c['dot']};stroke:{c['surface']};stroke-width:1.5}}"
            f".miss{{fill:{c['surface']};stroke:{c['bad']};stroke-width:1.75}}"
            f".pass{{fill:{c['good']}}}.fail{{fill:{c['surface']};stroke:{c['bad']};stroke-width:1.75}}"
            f".cut{{stroke:{c['surface']};stroke-width:2}}"
            + "".join(f".s{i}{{fill:{v}}}" for i, v in enumerate(c["series"]))
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{num(self.height)}" '
            f'viewBox="0 0 {WIDTH} {num(self.height)}" role="img" aria-label="{html.escape(title)}">\n'
            f"<title>{html.escape(title)}</title>\n<style>{style}</style>\n"
            f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{num(self.height - 1)}" rx="6" class="bg"/>\n'
            + "\n".join(self.items) + "\n</svg>\n"
        )


def header(svg, title, subtitle, legend=()):
    """The figure's title, its subtitle and a legend of (kind, class, label), and the y below them."""
    svg.text(PAD, 34, title, size=15, weight="600")
    y = 34
    for line in subtitle:
        y += 19
        svg.text(PAD, y, line, cls="t2", size=12.5)
    if legend:
        y += 26
        x = PAD
        for kind, cls, label in legend:
            if kind == "swatch":
                svg.rect(x, y - 9, 12, 10, cls, rx=2)
                x += 18
            else:
                svg.circle(x + 5, y - 4, 4.5 if kind == "dot" else 4, cls)
                x += 15
            svg.text(x, y, label, cls="t2", size=12)
            x += text_width(label, 12) + 22
    return y + 16


def nice_ticks(top, count=4):
    """Round tick values from 0 up to top, and the scale's end: top, or the last tick when it is close above."""
    if top <= 0:
        return [0, 1], 1
    raw = top / count
    step = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if step * m >= raw:
            step *= m
            break
    ticks = [round(i * step, 6) for i in range(math.floor(top / step + 1e-9) + 1)]
    end = ticks[-1] + step
    if end <= top * 1.08:
        ticks.append(end)
        return ticks, end
    return ticks, top * 1.04


def tick_label(v, unit=""):
    text = f"{v:g}"
    return text + unit


# The data.

def load(data_dir, name):
    """A dataset, or several joined by commas. A dataset is export.py's, or a list of runs, as bench/laptop's
    analyze.py writes them for docs/comparison.md."""
    runs, sources, notes = [], [], []
    for part in name.split(","):
        with open(os.path.join(data_dir, part + ".json"), encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, list):
            runs += loaded
        else:
            # Older exports recorded the variant only in source metadata. Recover
            # it when unambiguous so an old dnd run and a newer dnd run agree.
            for run in loaded["runs"]:
                variants = {s.get("variant") for s in loaded.get("sources", []) if s.get("task") == run["task"]}
                if "variant" not in run and len(variants) == 1:
                    run = dict(run, variant=variants.pop())
                runs.append(run)
            sources += loaded.get("sources", [])
            notes += loaded.get("notes", [])
    # The form without dropdowns is a task of its own, as on the comparison page, and its variant the rest of the
    # name, such as qt-25.
    runs = [dict(r, task="form" + NO_DROPDOWNS, variant=r["variant"].removesuffix(NO_DROPDOWNS))
            if r["task"] == "form" and (r.get("variant") or "").endswith(NO_DROPDOWNS) else r for r in runs]
    # A task that the datasets hold in several variants is named by its variant too, such as board-dnd, so that each
    # view's medians take one variant. task_of gives the task's own name.
    variants = {}
    for r in runs:
        variants.setdefault(r["task"], set()).add(r.get("variant"))
    runs = [dict(r, task=f"{r['task']}-{r['variant']}", task_of=r["task"]) if len(variants[r["task"]]) > 1 else r
            for r in runs]
    return {"sources": sources, "runs": runs, **({"notes": list(dict.fromkeys(notes))} if notes else {})}


def model_name(model):
    return NAMES.get(model, model.rsplit("/", 1)[-1])


def agent_order(runs):
    """The agents of the runs, (harness, model), in NAMES' order and then by name."""
    agents = list(dict.fromkeys((r["harness"], r["model"]) for r in runs))
    order = list(NAMES)
    return sorted(agents, key=lambda a: (order.index(a[1]) if a[1] in order else len(order), a[1], a[0]))


def agent_names(runs):
    """Display names of the agents: the model's full id where models share a short name, and the harness where
    agents share a model."""
    agents = agent_order(runs)
    models = {}
    for _, model in agents:
        models.setdefault(model_name(model), set()).add(model)
    names = {a: model_name(a[1]) if len(models[model_name(a[1])]) == 1 else a[1] for a in agents}
    counts = list(names.values())
    return {a: n + (f" ({a[0]})" if counts.count(n) > 1 else "") for a, n in names.items()}


def effort_key(effort):
    return (EFFORTS.index(effort) if effort in EFFORTS else len(EFFORTS), effort)


def groups(runs):
    """The runs a row per agent and effort asked for, in order: [((agent, effort), runs)]."""
    out = []
    efforts = sorted({r["effort"] for r in runs}, key=effort_key)
    for agent in agent_order(runs):
        for effort in efforts:
            rs = [r for r in runs if (r["harness"], r["model"]) == agent and r["effort"] == effort]
            if rs:
                out.append(((agent, effort), rs))
    return out


def tasks(data):
    return list(dict.fromkeys(r["task"] for r in data["runs"]))


def task_title(task):
    """A task's name in every figure and table: its name in TASK_NAMES, such as File manager, or else its own
    capitalized, followed by its variant where the datasets hold several, such as Board-dnd."""
    base = max((k for k in TASK_NAMES if task == k or task.startswith(k + "-")), key=len, default=None)
    return TASK_NAMES[base] + task[len(base):] if base else task.capitalize()


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def several_efforts(runs):
    return len({r["effort"] for r in runs}) > 1


def ran_at(runs):
    """The efforts the runs ran at, as the harnesses report them."""
    return "/".join(dict.fromkeys(r["ran_at"] or "unknown" for r in runs))


def effort_text(runs):
    """The effort the runs asked for, and what they ran at where that differs, as report.py writes it."""
    asked = "/".join(dict.fromkeys(r["effort"] for r in runs))
    ran = ran_at(runs)
    return asked + (f", ran {ran}" if ran != asked else "")


def effort_note(runs):
    """A line on the effort of the runs, naming the agents that ran at another than they asked for. Runs that asked
    for several have rows that show them, and a line only where an agent ran at another."""
    asked = {r["effort"] for r in runs}
    names = agent_names(runs)
    if len(asked) > 1:
        other = [(names[agent], effort, ran_at(rs)) for (agent, effort), rs in groups(runs)]
        other = [f"{name} asked for {effort} effort and ran at {ran}." for name, effort, ran in other if ran != effort]
        return " ".join(other) or None
    asked = asked.pop()
    other = [(names[a], ran_at([r for r in runs if (r["harness"], r["model"]) == a])) for a in agent_order(runs)]
    other = [(name, ran) for name, ran in other if ran != asked]
    level = "the harness's default effort" if asked == "default" else f"{asked} effort"
    if not other:
        return f"All runs ran at {level}."
    return f"All runs asked for {level}. " + " ".join(f"{name} ran at {ran}." for name, ran in other)


def row_labels(runs):
    """A row's label, and its effort's when the runs have more than one."""
    names = agent_names(runs)
    return {key: (names[key[0]], key[1] if several_efforts(runs) else None) for key, _ in groups(runs)}


# The figures.

def wrap(text, size, width, bold=False):
    """Text as lines no wider than width, broken between words. A longer word stands on a line of its own."""
    lines = []
    for word in text.split():
        if lines and text_width(lines[-1] + " " + word, size, bold) <= width:
            lines[-1] += " " + word
        else:
            lines.append(word)
    return lines


def column_heads(svg, heads, y):
    """Column heads, [(x, lines)], whose last lines share a baseline at y or below it, and that baseline."""
    y += 15 * (max(len(lines) for _, lines in heads) - 1)
    for x, lines in heads:
        for i, line in enumerate(lines):
            svg.text(x, y - 15 * (len(lines) - 1 - i), line, cls="t2", size=12, weight="600")
    return y


def partial_note(runs):
    """A line naming the agents that ran only some of the tasks, whose runs passed and cost are of those alone."""
    ts = tasks({"runs": runs})
    names = agent_names(runs)
    ran = [(names[a], len({r["task"] for r in runs if (r["harness"], r["model"]) == a})) for a in agent_order(runs)]
    partial = [f"{name} ran {n} of the {len(ts)} tasks." for name, n in ran if n < len(ts)]
    return " ".join(partial + ["An agent's runs passed and cost are of the tasks it ran."]) if partial else None


SHARE_W, SHARE_W_NARROW = 56, 30  # the widths of the bar of a task's runs passed, and where columns are narrow
NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 8: "eight", 9: "nine", 10: "ten", 12: "twelve"}


def share_text(ok, n):
    """The share of n runs that ok passed, and how many of how many."""
    return f"{round(100 * ok / n)}%", f"{ok}/{n}"


def share_layout(shares, time_w, room):
    """How share_cell draws the shares, (passed, runs) pairs, in columns room wide: the bar's width, the width of the
    share and that of the count, which is 0 where every cell has as many runs and the subtitle says how many, whether
    the time stands beside them, and the cell's width. The wider bar where it fits, and the time unless nothing else
    does."""
    texts = [share_text(ok, n) for ok, n in shares]
    pct_w = max(text_width(p, 13) for p, _ in texts)
    uniform = len({n.split("/")[1] for _, n in texts}) == 1
    count_w = 0 if uniform else max(text_width(n, 11.5) for _, n in texts)
    for bar_w, with_time in ((SHARE_W, True), (SHARE_W_NARROW, True), (SHARE_W_NARROW, False)):
        width = bar_w + 8 + pct_w + (5 + count_w if count_w else 0) + (12 + time_w if with_time else 0)
        if width <= room:
            break
    return bar_w, pct_w, count_w, with_time, width


def share_cell(svg, x, cy, ok, n, time, layout):
    """Runs of a task: a bar of the share that passed, filled, and that failed, outlined, the share and the count,
    and a time beside them. Which runs failed does not matter, and a bar reads the same for six runs as for
    eighteen."""
    base = cy + 4.5
    bar_w, pct_w, count_w, with_time, _ = layout
    w = bar_w * ok / n
    if ok < n:
        svg.rect(x + w + 0.9, cy - 4.1, bar_w - w - 1.8, 8.2, "fail", rx=1.5)
    if ok:
        svg.rect(x, cy - 5, w, 10, "pass", rx=2)
    pct, count = share_text(ok, n)
    svg.text(x + bar_w + 8, base, pct, size=13)
    x_time = x + bar_w + 8 + pct_w + 12
    if count_w:
        svg.text(x + bar_w + 8 + pct_w + 5, base, count, cls="m", size=11.5)
        x_time += 5 + count_w
    if with_time:
        svg.text(x_time, base, time, cls="t2", size=12.5)


def run_cell(svg, x, cy, rs, layout):
    """An agent's runs of a task as share_cell draws them, with their median wall time, or that the agent did not
    run it."""
    if not rs:
        svg.text(x, cy + 4.5, "not run", cls="m", size=12)
        return
    wall = median(r["wall_s"] for r in rs)
    share_cell(svg, x, cy, sum(r["passed"] for r in rs), len(rs), f"{wall:.0f} s" if wall is not None else "",
               layout)


def cost_cell(svg, x, cy, cost, top_cost, bar_max):
    """An agent's cost per passed run, a bar on the scale of the highest."""
    base = cy + 4.5
    if cost is None:
        svg.text(x, base, "–", cls="t2", size=12.5)
        return
    w = max(2, bar_max * cost / top_cost) if top_cost else 2
    svg.bar(x, cy - 5, w, 10, "tools")
    svg.text(x + w + 8, base, f"${cost:.2f}", cls="t2", size=12.5)


def scoreboard(data):
    runs = data["runs"]
    ts = tasks(data)
    agents = agent_order(runs)
    names = agent_names(runs)
    svg = Svg()
    total = len(runs)
    passed = sum(r["passed"] for r in runs)
    cells = {(a, t): [r for r in runs if (r["harness"], r["model"]) == a and r["task"] == t]
             for a in agents for t in ts}
    time_w = max(text_width(f"{median(r['wall_s'] for r in rs):.0f} s", 12.5) for rs in cells.values() if rs)
    per_pass, passed_of = {}, {}
    for a in agents:
        rs = [r for r in runs if (r["harness"], r["model"]) == a]
        ok = sum(r["passed"] for r in rs)
        cost = sum(r["cost_usd"] or 0 for r in rs)
        per_pass[a] = cost / ok if ok and all(r["cost_usd"] is not None for r in rs) else None
        passed_of[a] = f"{ok}/{len(rs)}"
    top_cost = max((v for v in per_pass.values() if v is not None), default=0)
    row_h = 34
    label_w = max(text_width(n, 13, True) for n in names.values()) + 24
    passed_w, cost_w = 64, 200
    task_w = (WIDTH - 2 * PAD - label_w - passed_w - cost_w) / len(ts)
    heads = [wrap(task_title(t), 12, task_w - 10, True) for t in ts]
    shares = [(sum(r["passed"] for r in rs), len(rs)) for rs in cells.values() if rs]
    layout = share_layout(shares, time_w, task_w - 8)
    # A row per agent while the tasks fit as its columns: their names on two lines at most, and their runs with their
    # time. A row per task beyond that, and a column per agent.
    by_agent = (task_w >= layout[4] + 8 and layout[3] and max(len(h) for h in heads) <= 2
                and max(text_width(line, 12, True) for h in heads for line in h) <= task_w - 10)
    if not by_agent:
        label_w = max(text_width(s, 13, True) for s in [task_title(t) for t in ts] + ["Cost per passed run"]) + 24
        col_w = (WIDTH - 2 * PAD - label_w) / len(agents)
        layout = share_layout(shares, time_w, col_w - 10)
    counts = {len(rs) for rs in cells.values() if rs}
    each = (f"Each bar is the share of a task's {NUMBER_WORDS.get(min(counts), min(counts))} runs that passed."
            if len(counts) == 1 else "Each bar is the share of a task's runs that passed, with the count.")
    when = ("The time is the task's median wall time." if layout[3]
            else "The figure below gives the times, which do not fit here.")
    top = header(
        svg, "Runs passed, wall time and cost" if layout[3] else "Runs passed and cost",
        [f"{passed} of {total} runs passed. {each} {when}"]
        + [line for line in [effort_note(runs), partial_note(runs)] if line],
        [("swatch", "pass", "passed"), ("swatch", "fail", "failed")],
    )
    if by_agent:
        x_passed = PAD + label_w
        x_task = x_passed + passed_w
        x_cost = x_task + task_w * len(ts)
        head_y = column_heads(svg, [(x_passed, ["Passed"])] + [(x_task + i * task_w, h) for i, h in enumerate(heads)]
                              + [(x_cost, ["Cost per passed run"])], top + 14)
        y0 = head_y + 12
        svg.line(PAD, y0, WIDTH - PAD, y0, "axis")
        for i, a in enumerate(agents):
            cy = y0 + row_h * i + row_h / 2
            svg.text(PAD, cy + 4.5, names[a], size=13, weight="600")
            svg.text(x_passed, cy + 4.5, passed_of[a], size=13)
            for j, t in enumerate(ts):
                run_cell(svg, x_task + j * task_w, cy, cells[(a, t)], layout)
            cost_cell(svg, x_cost, cy, per_pass[a], top_cost, cost_w - 64)
            if i < len(agents) - 1:
                svg.line(PAD, y0 + row_h * (i + 1), WIDTH - PAD, y0 + row_h * (i + 1), "grid")
        svg.height = y0 + row_h * len(agents) + 20
        alt = (f"Runs passed, wall time and cost. {passed} of {total} runs passed. A row per agent shows the share of "
               "its runs of each task that passed, their median wall time and the cost per passed run.")
        return svg, alt
    x_col = PAD + label_w
    head_y = column_heads(svg, [(x_col + i * col_w, wrap(names[a], 12, col_w - 10, True))
                                for i, a in enumerate(agents)], top + 14)
    y0 = head_y + 12
    svg.line(PAD, y0, WIDTH - PAD, y0, "axis")
    for j, t in enumerate(ts):
        cy = y0 + row_h * j + row_h / 2
        svg.text(PAD, cy + 4.5, task_title(t), size=13, weight="600")
        for i, a in enumerate(agents):
            run_cell(svg, x_col + i * col_w, cy, cells[(a, t)], layout)
        if j < len(ts) - 1:
            svg.line(PAD, y0 + row_h * (j + 1), WIDTH - PAD, y0 + row_h * (j + 1), "grid")
    # Each agent's runs passed and cost per passed run below its column.
    y1 = y0 + row_h * len(ts)
    svg.line(PAD, y1, WIDTH - PAD, y1, "axis")
    for k, label in enumerate(["Passed", "Cost per passed run"]):
        cy = y1 + row_h * k + row_h / 2
        svg.text(PAD, cy + 4.5, label, cls="t2", size=12, weight="600")
        for i, a in enumerate(agents):
            if k == 0:
                svg.text(x_col + i * col_w, cy + 4.5, passed_of[a], size=13)
            else:
                cost_cell(svg, x_col + i * col_w, cy, per_pass[a], top_cost, col_w - 64)
    svg.height = y1 + row_h * 2 + 20
    alt = (f"Runs passed{', wall time' if layout[3] else ''} and cost. {passed} of {total} runs passed. A row per task "
           f"shows the share of each agent's runs of it that passed{' and their median wall time' if layout[3] else ''}, "
           "and the rows below each agent's runs passed and cost per passed run.")
    return svg, alt


def glance(data):
    """A row per agent, the best share of runs passed first: a bar of all its runs passed, a cell per task with the
    share of that task's runs, the sum of its tasks' median wall times with the time in hyprmasq's tools, and its cost
    per passed run."""
    runs = data["runs"]
    ts = tasks(data)
    names = agent_names(runs)
    order = agent_order(runs)
    of = dict(groups(runs))
    def task_share(rs):
        """The mean of the tasks' shares of runs passed, so that a task with more runs does not weigh more."""
        ts_of = list(dict.fromkeys(r["task"] for r in rs))
        return sum(sum(r["passed"] for r in rs if r["task"] == t) / len([r for r in rs if r["task"] == t])
                   for t in ts_of) / len(ts_of)

    share_of = {k: task_share(of[k]) for k in of}
    keys = sorted(of, key=lambda k: (-share_of[k], order.index(k[0])))
    efforts = several_efforts(runs)
    total, passed = len(runs), sum(r["passed"] for r in runs)

    def per_task(rs, key):
        return sum(median(r[key] for r in rs if r["task"] == t) for t in {r["task"] for r in rs})

    wall = {k: per_task(of[k], "wall_s") for k in keys}
    tools = {k: per_task(of[k], "tools_s") for k in keys}
    cost = {}
    for k in keys:
        ok = sum(r["passed"] for r in of[k])
        cost[k] = (sum(r["cost_usd"] for r in of[k]) / ok
                   if ok and all(r["cost_usd"] is not None for r in of[k]) else None)
    svg = Svg()
    top = header(
        svg, "Runs passed, time and cost per model",
        [f"{passed} of {total} runs passed. A row per model, the best first by the mean of its tasks' shares of runs",
         "passed, which the first bar shows, so that a task with more runs does not weigh more. Its time is the sum of",
         "its tasks' median wall times, the part in hyprmasq's tools in blue, and its cost is per passed run."]
        + [line for line in [effort_note(runs)] if line],
        [("swatch", "pass", "passed"), ("swatch", "fail", "failed"), ("swatch", "tools", "in hyprmasq's tools"),
         ("swatch", "out", "rest of the time")],
    )
    name_w = max(text_width(names[k[0]], 13, True) for k in keys)
    effort_w = max(text_width(k[1], 12) for k in keys) + 10 if efforts else 0
    label_w = name_w + effort_w + 16
    pct_w = text_width("100%", 13, True)
    count_w = max(text_width(f"{sum(r['passed'] for r in of[k])}/{len(of[k])}", 11.5) for k in keys)
    bar_w, cell_bar = 60, 18
    passed_w = bar_w + 6 + pct_w + 5 + count_w + 14
    time_texts = {k: f"{wall[k] / 60:.0f} min" for k in keys}
    cost_texts = {k: f"${cost[k]:.2f}" if cost[k] is not None else "–" for k in keys}
    time_bar, cost_bar = 64, 44
    time_w = time_bar + 6 + max(text_width(t, 12.5) for t in time_texts.values()) + 14
    cost_w = cost_bar + 6 + max(text_width(t, 12.5) for t in cost_texts.values())
    room = WIDTH - 2 * PAD - label_w - passed_w - time_w - cost_w
    # The tasks' names stand at an angle above their cells, which are too narrow for them. The last one rises clear of
    # the time's two-line head beside it.
    reach = cell_bar / 2 + 2 + 44
    task_w = (room - reach) / (len(ts) - 1) if len(ts) > 1 else room
    x_passed = PAD + label_w
    x_task = x_passed + passed_w
    x_time = WIDTH - PAD - cost_w - time_w
    x_cost = x_time + time_w
    head_h = max(text_width(task_title(t), 11.5, True) for t in ts) * 0.71 + 6
    base = top + head_h
    column_heads(svg, [(x_passed, ["Tasks passed"]), (x_time, ["Time, all", "tasks"]),
                       (x_cost, ["Cost per", "passed run"])], base - 15)
    for j, t in enumerate(ts):
        x = x_task + j * task_w + cell_bar / 2 + 2
        svg.text(x, base, task_title(t), cls="t2", size=11.5, weight="600", rotate=-45)
    top_wall = max(wall.values())
    top_cost = max((c for c in cost.values() if c is not None), default=0)
    row_h = 28
    y0 = base + 10
    svg.line(PAD, y0, WIDTH - PAD, y0, "axis")

    def share(x, cy, ok, n, width, h):
        w = width * ok / n
        if ok < n:
            svg.rect(x + w + 0.9, cy - h / 2 + 0.9, width - w - 1.8, h - 1.8, "fail", rx=1.5)
        if ok:
            svg.rect(x, cy - h / 2, w, h, "pass", rx=2)

    for i, key in enumerate(keys):
        rs = of[key]
        cy = y0 + row_h * i + row_h / 2
        svg.text(PAD, cy + 4.5, names[key[0]], size=13, weight="600")
        if efforts:
            svg.text(PAD + label_w - 16, cy + 4.5, key[1], cls="m", size=12, anchor="end")
        ok = sum(r["passed"] for r in rs)
        share(x_passed, cy, share_of[key], 1, bar_w, 12)
        svg.text(x_passed + bar_w + 6, cy + 4.5, f"{100 * share_of[key]:.0f}%", size=13, weight="600")
        svg.text(x_passed + bar_w + 6 + pct_w + 5, cy + 4.5, f"{ok}/{len(rs)}", cls="m", size=11.5)
        for j, t in enumerate(ts):
            cell = [r for r in rs if r["task"] == t]
            x = x_task + j * task_w
            if cell:
                share(x, cy, sum(r["passed"] for r in cell), len(cell), cell_bar, 12)
            else:
                svg.text(x, cy + 4.5, "–", cls="m", size=12)
        ww = time_bar * wall[key] / top_wall
        tw = time_bar * tools[key] / top_wall
        svg.bar(x_time, cy - 6, tw, 12, "tools", round_end=False)
        svg.bar(x_time + tw + 1.5, cy - 6, ww - tw - 1.5, 12, "out")
        svg.text(x_time + time_bar + 6, cy + 4.5, time_texts[key], cls="t2", size=12.5)
        if cost[key] is not None:
            svg.bar(x_cost, cy - 6, max(2, cost_bar * cost[key] / top_cost) if top_cost else 2, 12, "m")
        svg.text(x_cost + cost_bar + 6, cy + 4.5, cost_texts[key], cls="t2", size=12.5)
        if i < len(keys) - 1:
            svg.line(PAD, y0 + row_h * (i + 1), WIDTH - PAD, y0 + row_h * (i + 1), "grid")
    svg.height = y0 + row_h * len(keys) + 20
    alt = (f"Runs passed, time and cost per model. {passed} of {total} runs passed. A row per model, the best first, "
           "with the mean of its tasks' shares of runs passed, each task's share, the sum of its tasks' median wall "
           "times and its cost per passed run.")
    return svg, alt


def time_panel(svg, rows, x0, width, y0, row_h, title, note, limit=None, counts=False):
    """A panel of horizontal bars, a row per group: the medians in tools and outside, and a dot per run. limit sets
    the scale, which otherwise fits the panel's runs. counts writes each row's runs passed beside its median."""
    if title:
        svg.text(x0, y0, title, size=13, weight="600")
    svg.text(x0 + width, y0, note, cls="m", size=12, anchor="end")
    # The medians stand in a column at the panel's right.
    plot_w = width - (118 if counts else 74)
    if limit is None:
        limit = scale_limit([rs for _, rs in rows])
    ticks, end = nice_ticks(limit)
    scale = plot_w / end
    top = y0 + 12
    bottom = top + row_h * len(rows)
    for t in ticks:
        x = x0 + t * scale
        svg.line(x, top, x, bottom, "axis" if t == 0 else "grid")
        svg.text(x, bottom + 15, tick_label(t, " s" if t == ticks[-1] else ""), cls="m", size=11,
                 anchor="middle" if t else "start")
    for i, (_, rs) in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        if not rs:
            svg.text(x0 + 6, cy + 4.5, "not run", cls="m", size=12)
            continue
        tools = median(r["tools_s"] for r in rs)
        wall = median(r["wall_s"] for r in rs)
        cut = wall > end
        tw = min(tools, end) * scale
        ww = min(wall, end) * scale
        svg.bar(x0, cy - 6, tw, 12, "tools", round_end=False)
        if ww - tw > 2:
            svg.bar(x0 + tw + 2, cy - 6, ww - tw - 2, 12, "out", round_end=not cut)
        if cut:
            for dx in (-12, -7):
                svg.line(x0 + ww + dx, cy + 8, x0 + ww + dx + 4, cy - 8, "cut")
        # Runs off the scale stand in a row past its edge.
        beyond = 0
        for r in sorted(rs, key=lambda r: r["wall_s"]):
            if r["wall_s"] <= end:
                x = x0 + r["wall_s"] * scale
            else:
                x = x0 + plot_w + 6 + 7 * beyond
                beyond += 1
            svg.circle(x, cy, 3.5 if r["passed"] else 3, "dot" if r["passed"] else "miss")
        svg.text(x0 + width, cy + 4.5, f"{wall:.0f} s", cls="t2", size=12, anchor="end")
        if counts:
            svg.text(x0 + width - 44, cy + 4.5, f"{sum(r['passed'] for r in rs)}/{len(rs)}",
                     cls="t" if all(r["passed"] for r in rs) else "t2", size=12, anchor="end",
                     weight=None if all(r["passed"] for r in rs) else "600")
    return bottom + 15


def scale_limit(groups_of_runs):
    """The end of a time scale for groups of runs. A group far slower than the rest would squeeze them: its bar is
    cut at the edge instead."""
    tops = sorted((max(r["wall_s"] for r in rs) for rs in groups_of_runs if rs), reverse=True)
    return tops[0] if len(tops) < 2 or tops[0] <= 2.5 * tops[1] else tops[1] * 1.15


def focus_limit(groups_of_runs):
    """The end of a time scale that fits the groups whose median is at most 2.5 times the median of the groups'
    medians. A slower group's bar is cut at the edge, its time written beside it, so that it does not squeeze the
    others."""
    medians = [median(r["wall_s"] for r in rs) for rs in groups_of_runs if rs]
    if not medians:
        return None  # time_panel then fits its own scale
    typical = median(medians)
    return max(r["wall_s"] for rs in groups_of_runs if rs and median(r["wall_s"] for r in rs) <= 2.5 * typical
               for r in rs)


def shared_limit(groups_of_runs):
    """The end of a time scale that panels share: the slowest run that took at most twice its group's median. A run
    slower than that, such as one that waited long before its first call, stands past the scale's edge."""
    return max(r["wall_s"] for rs in groups_of_runs if rs for r in rs
               if r["wall_s"] <= 2 * median(x["wall_s"] for x in rs))


def time_figure(data, only=None, title=task_title, noun="task", shared=False, counts=False):
    """title gives a panel's title for its task. shared puts all panels on one scale, for panels to compare. counts
    writes each row's runs passed beside its median, for the overview."""
    runs = data["runs"]
    ts = [only] if only else tasks(data)
    labels = row_labels(runs)
    limit = shared_limit([rs for t in ts for _, rs in groups([r for r in runs if r["task"] == t])]) if shared else None
    off_scale = limit is not None and any(r["wall_s"] > nice_ticks(limit)[1] for r in runs if r["task"] in ts)
    svg = Svg()
    top = header(
        svg,
        ("Runs passed and wall time" if counts else "Wall time per run") + (f": {title(ts[0])}" if len(ts) == 1 else ""),
        (["Each row gives the runs passed and the median wall time. In its bar, the blue part is the median time",
          "in hyprmasq's MCP tools, and the grey part fills it up to the median wall time. Each dot is one run.",
          "Each panel's scale fits most models: a slower model's bar is cut at its edge, and its runs stand past it."]
         if counts else
         ["Each bar is a median wall time, written at the right. Its blue part is the median time in hyprmasq's",
          "MCP tools, and the grey part fills the bar up to the median wall time. Each dot is one run."])
        + (["A dot past the end of its axis is a run off the scale, more than twice its row's median."]
           if off_scale else [])
        + [line for line in [effort_note([r for r in runs if r["task"] in ts])] if line],
        [("swatch", "tools", "median in hyprmasq's tools"), ("swatch", "out", "rest of the median wall time"),
         ("dot", "dot", "one run")]
        + ([("dot", "miss", "failed run")] if any(not r["passed"] for r in runs if r["task"] in ts) else []),
    )
    keys = [key for key, _ in groups([r for r in runs if r["task"] in ts])]
    efforts = several_efforts(runs)
    label_w = max(text_width(labels[k][0], 12.5, True) for k in keys) + (
        max(text_width(labels[k][1], 12) for k in keys) + 16 if efforts else 0) + 20
    # Where an agent did not run some tasks, such as the efforts tried only where a model failed, each panel lists only
    # the rows it has, one panel a row, each with its own labels.
    sparse = any(not [r for r in runs if r["task"] == t and (r["harness"], r["model"]) == key[0] and r["effort"] == key[1]]
                 for t in ts for key in keys)
    columns = 1 if len(ts) == 1 or sparse else 2
    gap = 36
    panel_w = (WIDTH - 2 * PAD - label_w - gap * (columns - 1)) / columns
    row_h = 24 if efforts else 26
    y = top + 18
    for start in range(0, len(ts), columns):
        bottom = y
        for c, t in enumerate(ts[start:start + columns]):
            # Every panel has a row for each key, empty where the task has no runs of it, so that rows line up.
            by_key = dict(groups([r for r in runs if r["task"] == t]))
            rows = [(key, by_key.get(key, [])) for key in keys if key in by_key or not sparse]
            ok = sum(r["passed"] for _, rs in rows for r in rs)
            n = sum(len(rs) for _, rs in rows)
            x0 = PAD + label_w + c * (panel_w + gap)
            panel_limit = focus_limit([rs for _, rs in rows]) if counts and limit is None else limit
            bottom = max(bottom, time_panel(svg, rows, x0, panel_w, y, row_h, title(t) if len(ts) > 1 else "",
                                            f"{ok}/{n} passed", panel_limit, counts))
            if c == 0:
                shown = [key for key, _ in rows]
                for i, key in enumerate(shown):
                    name, effort = labels[key]
                    base = y + 12 + row_h * i + row_h / 2 + 4.5
                    first = i == 0 or shown[i - 1][0] != key[0]
                    if first:
                        svg.text(PAD, base, name, size=12.5, weight="600")
                    if effort:
                        svg.text(PAD + label_w - 16, base, effort, cls="t2", size=12, anchor="end")
        y = bottom + 34
    svg.height = y - 10
    alt = (("Runs passed and wall time per run of each agent" if counts else "Wall time per run of each agent")
           + (f" in the {title(ts[0])} {noun}" if len(ts) == 1 else f", a panel per {noun}")
           + ". Each bar is the median wall time with the median time in hyprmasq's tools inside it, and each dot "
           "one run." + (" Each row also gives its runs passed." if counts else ""))
    return svg, alt


# The form task's toolkits, by the variant's name without its number of questions, as bench/tasks/form/variants
# names them: GTK 4's variants are the number alone.
TOOLKITS = {
    "": "GTK 4", "gtk3": "GTK 3", "gtk3-x11": "GTK 3 (X11)", "qt": "Qt 6", "tk": "Tk (X11)",
    "electron": "Electron", "electron-x11": "Electron (X11)", "cef": "CEF", "cef-x11": "CEF (X11)",
    "cef-osr": "CEF off-screen (X11)",
}


def toolkit(run):
    """The toolkit of a form run, from its variant."""
    name = (run.get("variant") or "").rpartition("-")[0]
    if name not in TOOLKITS:
        raise SystemExit(f"render.py: the form variant {run.get('variant')} has no toolkit in TOOLKITS")
    return TOOLKITS[name]


def toolkit_runs(data):
    """The form runs with their toolkit as their task, in TOOLKITS' order, for the views that compare toolkits. Their
    variants must have the same number of questions, which the toolkit's name leaves out."""
    runs = [dict(r, task=toolkit(r)) for r in data["runs"] if r.get("task_of", r["task"]) == "form"]
    if not runs:
        raise SystemExit("render.py: the toolkit views need runs of the form task")
    counts = sorted({(r.get("variant") or "").rpartition("-")[2] for r in runs})
    if len(counts) > 1:
        raise SystemExit(f"render.py: the toolkit views compare runs of one number of questions, not {', '.join(counts)}")
    order = list(TOOLKITS.values())
    return sorted(runs, key=lambda r: order.index(r["task"]))


def toolkits_figure(data):
    return time_figure({"runs": toolkit_runs(data)}, title=lambda t: t, noun="toolkit", shared=True)


def kinds_runs(data, task=None):
    """The runs whose time per kind counts: passed ones, as a run that stopped early has unanswered questions whose
    time is none. They are TASK's, or the dataset's one task's: the form with and without dropdowns ask other kinds."""
    runs = [r for r in data["runs"] if r.get("kinds") and r["passed"] and task in (None, r["task"])]
    if not runs:
        raise SystemExit("render.py: the kinds views need passed runs of the form task")
    several = list(dict.fromkeys(r["task"] for r in runs))
    if len(several) > 1:
        raise SystemExit(f"render.py: the kinds views take one task's runs: name one of {', '.join(several)}")
    return runs


def kinds_figure(data, task=None):
    runs = kinds_runs(data, task)
    agents = agent_order(runs)
    # A model keeps its colour in each task's figure: its place among all the dataset's models. The models that ran
    # the task and passed no run have no bars, which a line names.
    colour = {a: f"s{i}" for i, a in enumerate(agent_order(data["runs"]))}
    if any(int(colour[a][1:]) >= len(THEMES["light"]["series"]) for a in agents):
        raise SystemExit(f"render.py: the kinds figure has colours for {len(THEMES['light']['series'])} models")
    ran = [r for r in data["runs"] if r.get("kinds") and r["task"] == runs[0]["task"]]
    names = agent_names(ran)
    none = [names[a] for a in agent_order(ran) if a not in agents]
    kinds = [k for k in KINDS if k in runs[0]["kinds"]] + [k for k in runs[0]["kinds"] if k not in KINDS]
    per = lambda rs, k: median(r["kinds"][k]["seconds"] / r["kinds"][k]["items"] for r in rs)  # noqa: E731
    values = {(a, k): per([r for r in runs if (r["harness"], r["model"]) == a], k) for a in agents for k in kinds}
    svg = Svg()
    efforts = sorted({r["effort"] for r in runs}, key=effort_key)
    top = header(
        svg, "Seconds per question by kind",
        ["The time from the previous answer to this one. Medians of each model's passed runs"
         + (f" at {', '.join(efforts[:-1])} and {efforts[-1]} effort" if len(efforts) > 1 else "") + "."]
        + ([f"{', '.join(none[:-1]) + ' and ' if len(none) > 1 else ''}{none[-1]} passed no run."] if none else []),
        [("swatch", colour[a], names[a]) for a in agents],
    )
    label_w = 150
    plot_w = WIDTH - 2 * PAD - label_w - 40
    ticks, end = nice_ticks(max(values.values()))
    scale = plot_w / end
    bar_h, bar_gap, group_gap = 8, 3, 18
    group_h = len(agents) * (bar_h + bar_gap) - bar_gap
    x0 = PAD + label_w
    y = top + 14
    bottom = y + len(kinds) * (group_h + group_gap) - group_gap
    for t in ticks:
        x = x0 + t * scale
        svg.line(x, y - 6, x, bottom + 6, "axis" if t == 0 else "grid")
        svg.text(x, bottom + 22, tick_label(t, " s" if t == ticks[-1] else ""), cls="m", size=11,
                 anchor="middle" if t else "start")
    for k in kinds:
        overall = per(runs, k)
        svg.text(PAD, y + group_h / 2 - 1, k.capitalize(), size=13, weight="600")
        svg.text(PAD, y + group_h / 2 + 15, f"{overall:.1f} s over all runs", cls="t2", size=12)
        for i, a in enumerate(agents):
            v = values[(a, k)]
            by = y + i * (bar_h + bar_gap)
            svg.bar(x0, by, v * scale, bar_h, colour[a])
            svg.text(x0 + v * scale + 6, by + bar_h - 0.5, f"{v:.1f}", cls="t2", size=11)
        y += group_h + group_gap
    svg.height = bottom + 40
    alt = "Seconds per question by kind, a bar per model. Over all runs: " + ", ".join(
        f"{k} {per(runs, k):.1f} s" for k in kinds)
    return svg, alt


def act_figure(data):
    runs = data["runs"]
    # A Cua run has no act calls, and no act_s.
    totals = {key: sum((r["act_s"] or {}).get(key, 0) for r in runs) for key, _, _ in ACT_PARTS}
    # A part without time has no segment. The rest, which analyze.py does not clamp, can even be negative.
    parts = [(i, key, label, detail) for i, (key, label, detail) in enumerate(ACT_PARTS) if totals[key] > 0]
    whole = sum(totals[key] for _, key, _, _ in parts)
    if not whole:
        raise SystemExit("render.py: the act view needs runs with time in act")
    svg = Svg()
    top = header(
        svg, "Time in act calls",
        [f"{whole:.0f} s in the finished act calls of all {len(runs)} runs, split by analyze.py."],
    )
    x0, w = PAD, WIDTH - 2 * PAD
    y = top + 8
    x = x0
    for n, (i, key, label, detail) in enumerate(parts):
        sw = w * totals[key] / whole - (2 if n < len(parts) - 1 else 0)
        svg.rect(x, y, sw, 20, f"s{i}")
        x += sw + 2
    # The parts' labels flow below the bar, as a part can be too narrow for its own.
    x, ly = x0, y + 44
    for i, key, label, detail in parts:
        value = f"{totals[key]:.0f} s · {100 * totals[key] / whole:.0f}%"
        item_w = 18 + max(text_width(label, 13, True), text_width(value, 12.5), text_width(detail, 11.5))
        if x > x0 and x + item_w > x0 + w:
            x, ly = x0, ly + 64
        svg.rect(x, ly - 10, 12, 10, f"s{i}", rx=2)
        svg.text(x + 18, ly, label, size=13, weight="600")
        svg.text(x + 18, ly + 18, value, cls="t2", size=12.5)
        svg.text(x + 18, ly + 35, detail, cls="m", size=11.5)
        x += item_w + 20
    svg.height = ly + 54
    alt = "Time in finished act calls: " + ", ".join(
        f"{label} {totals[key]:.0f} s" for _, key, label, _ in parts)
    return svg, alt


# The comparison with other computer-use tools, from bench/laptop's runs.

# Each tool's name and series colour, the same in every figure.
TOOLS = {"hyprmasq": ("hyprmasq", "s0"), "cua": ("Cua Driver", "s1"), "openai-cu": ("OpenAI computer use", "s2"),
         "hypruse": ("hypruse", "s3"), "computer-use-linux": ("computer-use-linux", "s4"),
         "claude-cu": ("Claude computer use", "s6")}
PLATFORMS = {"hyprland": "Hyprland", "hyprland-vm": "Hyprland VM", "macos": "macOS"}
# The tasks whose page is in the user's browser, Firefox or Chromium, which the VM's runs name. Forgejo's is always
# Chromium.
BROWSER_TASKS = {"board", "trace", "calc"}


def compared(r):
    """A run in the form of bench/laptop's runs, which the comparison's views read. A run of the benchmark's VM, as
    export.py writes it, runs on the VM's Hyprland, and its user was disturbed while the focus was away from them. Its
    form task is one task in each toolkit, as on the laptop, with the toolkit of its variant: qt-25 is the Qt port,
    25 the GTK 4 app."""
    if "wall_seconds" in r:
        return r
    uncached = r["input_tokens"] - (r["cache_read_tokens"] or 0) if r.get("input_tokens") is not None else None
    if r.get("task_of", r["task"]).startswith("form"):
        variant = r.get("variant") or ""
        r = dict(r, task=r.get("task_of", r["task"]), toolkit=variant.split("-", 1)[0] if not variant[:1].isdigit()
                 else "gtk4")
    return dict(r, platform="hyprland-vm", wall_seconds=r["wall_s"], tool_seconds=r["tools_s"],
                usage={"input_tokens": r.get("input_tokens"), "uncached_input_tokens": uncached,
                       "cost_usd": r.get("cost_usd")},
                focus={"seconds_away_from_user": r.get("focus_away_s")})


def tool_rows(runs, pooled=None):
    """The runs grouped by task, platform and agent, a row per tool and where it ran, such as hyprmasq with the
    questionnaire in GTK and in Qt: [((task, platform, model), [(tool, runs)])]. The platforms' times do not compare,
    so their rows stand apart. Pooled "agents", a row holds the runs of every agent, and the model is None. Pooled
    "tasks", it holds the runs of every task, which is "all"."""
    order = list(NAMES)
    if pooled == "agents":
        runs = [dict(r, model=None) for r in runs]
    elif pooled == "tasks":
        runs = [dict(r, task="all", task_of="all") for r in runs]
    keys = list(dict.fromkeys((r["task"], r.get("platform"), r["model"]) for r in runs))
    # Tasks without a name in TASK_NAMES come after those, in the order the runs hold them, each with its agents.
    seen = list(dict.fromkeys(r["task"] for r in runs))
    keys.sort(key=lambda k: (list(TASK_NAMES).index(k[0]) if k[0] in TASK_NAMES else 9, seen.index(k[0]),
                             list(PLATFORMS).index(k[1]) if k[1] in PLATFORMS else len(PLATFORMS),
                             order.index(k[2]) if k[2] in order else len(order)))
    out = []
    for key in keys:
        rs = [r for r in runs if (r["task"], r.get("platform"), r["model"]) == key]
        by_row = {}
        for r in rs:
            by_row.setdefault((r["backend"], setting([r])), []).append(r)
        rows = sorted(by_row.items(), key=lambda item: (list(TOOLS).index(item[0][0]), item[0][1]))
        out.append((key, [(tool, group) for (tool, _), group in rows]))
    return out


def setting(rs):
    """Where a tool's runs ran: platform, toolkit of the task's app, Finder for the Mac's file manager, and on a Mac
    the app or CLI that ran the agent."""
    r = rs[0]
    parts = [PLATFORMS.get(r.get("platform"), r.get("platform") or "")]
    if r["task"].startswith("form"):
        parts.append({"gtk": "GTK", "gtk4": "GTK 4", "qt": "Qt"}.get(r.get("toolkit"), r.get("toolkit") or ""))
    if r.get("task_of", r["task"]) in BROWSER_TASKS and r.get("browser"):
        parts.append(r["browser"].capitalize())
    if r["task"] == "files" and r.get("platform") == "macos":
        parts.append("Finder")
    parts.append({"codex-desktop": "ChatGPT app", "codex-exec": "Codex CLI", "claude-desktop": "Claude app",
                  "claude-exec": "Claude Code"}.get(r["harness"], ""))
    return " · ".join(p for p in parts if p)


def passed(r):
    """Passed, leaving out Hyprland's own ANR dialogs, which the form's checker counts as windows that opened."""
    return r.get("passed_without_hyprland_dialogs", r["passed"])


def group_title(key, rows):
    """A group's platform and agent, after its task where the figure has several."""
    task, platform, model = key
    title = f"{PLATFORMS.get(platform, platform or '')} · {model_name(model) if model else 'all agents'}"
    if len({k[0] for k, _ in rows}) == 1:
        return title
    return f"{task_title(task)} · {title}"


def disturbance(r):
    """Seconds the user's screen was disturbed, and how: the focus moved away, or the app's windows were drawn
    over the user's window."""
    focus = (r.get("focus") or {}).get("seconds_away_from_user") or 0
    popups = (r.get("popups") or {}).get("seconds_visible") or 0
    combined = (r.get("focus") or {}).get("seconds_disturbed")
    if combined is not None:
        kind = "focus or windows" if focus and popups else "focus taken" if focus else "windows over the user"
        return combined, kind if combined else ""
    if focus >= 1:
        return focus, "focus taken"
    if popups:
        return popups, "windows over the user"
    return 0.0, ""


def disturbed(r):
    """Whether a run disturbed the user: a second or more, as the figures round it."""
    return disturbance(r)[0] >= 0.5


def disturbance_label(seconds, rs):
    """The disturbance column's text: how many of the passed runs disturbed the user, and the median seconds and
    the way of those that did, with Hyprland's ANR dialogs where there were any."""
    ok = [r for r in rs if passed(r)]
    hit = [r for r in ok if disturbed(r)]
    if not ok:
        return "–"
    text = f"{len(hit)} of {len(ok)}"
    if hit:
        # Each way once, where some runs took the focus and others also drew windows: focus/windows.
        words = {"focus taken": ["focus"], "windows over the user": ["windows"],
                 "focus or windows": ["focus", "windows"]}
        kinds = {w for r in hit for w in words[disturbance(r)[1]]}
        text += f" · {seconds:.0f} s, " + "/".join(sorted(kinds))
    if any(r.get("hyprland_dialogs") for r in rs):
        text += ", ANR dialogs"
    return text


def per_k(value):
    """Thousands, or None for a value that a run did not record, such as the tokens of a session that ended before
    its first count."""
    return value / 1000 if value is not None else None


def comparison_measures():
    """(title, value of a run, the runs it is the median of, text of the median and the row's runs)."""
    # The disturbance is that of the passed runs that disturbed the user, beside how many did.
    every, hit = (lambda rs: rs), (lambda rs: [r for r in rs if passed(r) and disturbed(r)])
    known = lambda text: lambda v, rs: text(v) if v is not None else "–"
    return [
        ("Uncached input", lambda r: per_k(r["usage"]["uncached_input_tokens"]), every, known(lambda v: f"{v:.0f}k")),
        ("Peak context", lambda r: per_k((r.get("context") or {}).get("peak_input_tokens")), every,
         known(lambda v: f"{v:.0f}k")),
        ("Cost per run", lambda r: r["usage"]["cost_usd"], every, known(lambda v: f"${v:.2f}")),
        ("User disturbed", lambda r: disturbance(r)[0], hit, disturbance_label),
    ]


def of_task(data, task):
    """The runs of one task, or all of them without one: a page can show each task's comparison in its own block."""
    return [compared(r) for r in data["runs"] if task is None or r["task"] == task]


def comparison_figure(data, task=None, pooled=None, runs=None):
    runs = runs if runs is not None else of_task(data, task)
    rows = tool_rows(runs, pooled)
    tools = list(dict.fromkeys(r["backend"] for r in runs))
    svg = Svg()
    top = header(
        svg, "Tools compared on the same tasks",
        ["The first bar is the share of runs that passed, with the count and the passed runs' median time. The other",
         "bars have a scale each: tokens and cost are medians over all runs. User disturbed counts the passed runs",
         "that took the focus or drew over the user's window, and its bar is their median seconds."]
        + ({"agents": ["Each row pools the runs of every agent."],
            "tasks": ["Each row pools the runs of every task that the agent ran with hyprmasq and with the other tools."]}
           .get(pooled, [])),
        [("swatch", TOOLS[t][1], TOOLS[t][0]) for t in TOOLS if t in tools]
        + [("swatch", "pass", "passed"), ("swatch", "fail", "failed")],
    )
    measures = comparison_measures()
    label_w = 190
    # The share of runs passed, then the median time of the passed ones.
    cells = [rs for _, group in rows for _, rs in group]
    times = [[r["wall_seconds"] for r in rs if passed(r)] for rs in cells]
    time_w = max(text_width(f"{median(t):.0f} s" if t else "–", 12.5) for t in times)
    shares = [(sum(passed(r) for r in rs), len(rs)) for rs in cells]
    # The narrow bar, and the count even where every row has as many runs, as the figure's subtitle does not say how
    # many.
    layout = share_layout(shares, time_w, 0)
    layout = layout[:2] + (layout[2] or max(text_width(f"{n}/{n}", 11.5) for _, n in shares), True) + layout[4:]
    runs_w = layout[0] + 8 + layout[1] + 5 + layout[2] + 12 + time_w + 10
    # The disturbance's text says how many runs and how, so its column takes what the others leave.
    widths = [104] * (len(measures) - 1)
    widths.append(WIDTH - 2 * PAD - label_w - runs_w - sum(widths))
    lefts = [sum(widths[:i]) for i in range(len(widths))]
    x_runs = PAD + label_w
    head_y = top + 14
    svg.text(x_runs, head_y, "Runs passed, time", cls="t2", size=12, weight="600")
    for i, (title, _, _, _) in enumerate(measures):
        svg.text(x_runs + runs_w + lefts[i], head_y, title, cls="t2", size=12, weight="600")
    tops = [max((median(f(r) for r in pick(rs)) or 0) for _, group in rows for _, rs in group) or 1
            for _, f, pick, _ in measures]
    row_h, group_h = 34, 30
    y = head_y + 12
    svg.line(PAD, y, WIDTH - PAD, y, "axis")
    for g, (key, group) in enumerate(rows):
        svg.text(PAD, y + 20, group_title(key, rows), size=13, weight="600")
        y += group_h
        for tool, rs in group:
            cy = y + row_h / 2
            base = cy + 4.5
            name, cls = TOOLS[tool]
            svg.rect(PAD, cy - 9, 4, 18, cls, rx=2)
            svg.text(PAD + 12, cy - 1.5, name, size=12.5, weight="600")
            svg.text(PAD + 12, cy + 12.5, setting(rs), cls="m", size=11)
            times = [r["wall_seconds"] for r in rs if passed(r)]
            share_cell(svg, x_runs, cy, sum(passed(r) for r in rs), len(rs),
                       f"{median(times):.0f} s" if times else "–", layout)
            for i, (_, f, pick, label) in enumerate(measures):
                v = median(f(r) for r in pick(rs))
                x = x_runs + runs_w + lefts[i]
                # A median of no recorded values is unknown, not zero.
                text = label(v, rs)
                bar_max = widths[i] - max(58, text_width(text, 12) + 10)
                w = bar_max * v / tops[i] if v else 0
                if w >= 1:
                    svg.bar(x, cy - 5, max(w, 2), 10, cls)
                svg.text(x + (max(w, 2) + 6 if w >= 1 else 0), base, text, cls="t2", size=12)
            y += row_h
        if g < len(rows) - 1:
            svg.line(PAD, y + 4, WIDTH - PAD, y + 4, "grid")
            y += 8
    svg.height = y + 22
    alt = (f"Tools compared on the same tasks: a row per tool for each "
           f"{ {'agents': 'task', 'tasks': 'agent'}.get(pooled, 'task and agent') }, with "
           "the share of its runs "
           "that passed, the median time, and the median uncached input, peak context, cost per run and seconds the "
           "user was disturbed.")
    return svg, alt


def comparison_time_figure(data, task=None):
    runs = of_task(data, task)
    rows = tool_rows(runs)
    svg = Svg()
    top = header(
        svg, "Time per run, in the tools and outside them",
        ["Each bar is the median time of a tool's passed runs, written at the right. Its coloured part is the",
         "median time inside the tool's calls, the grey part the rest, mostly the model's turns. Each dot is one run."],
        [("swatch", TOOLS[t][1], f"in {TOOLS[t][0]}'s calls") for t in TOOLS
         if t in {r["backend"] for r in runs}]
        + [("swatch", "out", "the rest"), ("dot", "dot", "one run"), ("dot", "miss", "failed run")],
    )
    label_w = 210
    x0 = PAD + label_w
    plot_w = WIDTH - PAD - x0 - 60
    longest = max(r["wall_seconds"] for r in runs)
    ticks, end = nice_ticks(longest)
    scale = plot_w / end
    row_h, group_h = 30, 28
    y = top + 10
    plot_top = y
    rows_drawn = []
    for g, (key, group) in enumerate(rows):
        svg.text(PAD, y + 19, group_title(key, rows), size=13, weight="600")
        y += group_h
        for tool, rs in group:
            rows_drawn.append((y, tool, rs))
            y += row_h
        y += 6
    bottom = y
    for t in ticks:
        x = x0 + t * scale
        svg.line(x, plot_top, x, bottom, "axis" if t == 0 else "grid")
        svg.text(x, bottom + 15, tick_label(t, " s" if t == ticks[-1] else ""), cls="m", size=11,
                 anchor="middle" if t else "start")
    for ry, tool, rs in rows_drawn:
        cy = ry + row_h / 2
        name, cls = TOOLS[tool]
        svg.rect(PAD, cy - 9, 4, 18, cls, rx=2)
        svg.text(PAD + 12, cy - 1.5, name, size=12.5, weight="600")
        svg.text(PAD + 12, cy + 12.5, setting(rs), cls="m", size=11)
        ok = [r for r in rs if passed(r)]
        if ok:
            wall = median(r["wall_seconds"] for r in ok)
            tools_s = min(median(r["tool_seconds"] for r in ok), wall)
            svg.bar(x0, cy - 6, tools_s * scale, 12, cls, round_end=False)
            if (wall - tools_s) * scale > 2:
                svg.bar(x0 + tools_s * scale + 2, cy - 6, (wall - tools_s) * scale - 2, 12, "out")
            svg.text(WIDTH - PAD, cy + 4.5, f"{wall:.0f} s", cls="t2", size=12, anchor="end")
        for r in sorted(rs, key=lambda r: r["wall_seconds"]):
            svg.circle(x0 + r["wall_seconds"] * scale, cy, 3.5 if passed(r) else 3, "dot" if passed(r) else "miss")
    svg.height = bottom + 34
    alt = ("Time per run of each tool for each task and agent: the median time of the passed runs, split into the "
           "time inside the tool's calls and the rest, and a dot per run.")
    return svg, alt


def beyond_first(r):
    """The input a run's work added, in k tokens: its input less the first request, which every request carries again,
    so the harness's system prompt and the tools' definitions are left out. None where its context is not known, or
    unless its context records that it compacted its history no time: after a compaction a request no longer carries
    all of the first, and a count that is not known, as where Codex announced more than it recorded, may hide one."""
    context = r.get("context") or {}
    first, requests, total = context.get("first_input_tokens"), context.get("request_count"), r["usage"]["input_tokens"]
    if None in (first, requests, total) or context.get("compactions") != 0:
        return None
    return (total - requests * first) / 1000


def comparison_table(data, task=None):
    runs = of_task(data, task)
    # Hyprland's ANR dialogs get a column only where a run had any.
    any_dialogs = any(r.get("hyprland_dialogs") for r in runs)
    rows = []
    for key, group in tool_rows(runs):
        for tool, rs in group:
            dialogs = [len(r.get("hyprland_dialogs") or []) for r in rs]
            rows.append([
                task_title(key[0]), model_name(key[2]), TOOLS[tool][0], setting(rs),
                f"{sum(passed(r) for r in rs)}/{len(rs)}",
                fmt([r["wall_seconds"] for r in rs if passed(r)]), fmt([r["tool_seconds"] for r in rs if passed(r)]),
                fmt([r["calls"] for r in rs]), fmt([r["errors"] for r in rs]),
                fmt([per_k(r["usage"]["input_tokens"]) for r in rs]),
                fmt([per_k(r["usage"]["uncached_input_tokens"]) for r in rs]),
                fmt([per_k((r.get("context") or {}).get("peak_input_tokens")) for r in rs]),
                fmt([(r.get("context") or {}).get("request_count") for r in rs]), fmt([beyond_first(r) for r in rs]),
                fmt([r["usage"]["cost_usd"] for r in rs], 2),
                fmt([(r.get("focus") or {}).get("seconds_away_from_user") for r in rs if passed(r)]),
                fmt([(r.get("popups") or {}).get("seconds_visible") for r in rs if passed(r)]),
            ] + ([fmt(dialogs) if any(dialogs) else "0"] if any_dialogs else []))
    columns = ["Task", "Agent", "Tool", "Where", "Passed", "Time s", "In tools s", "Calls", "Failed calls",
               "Input k", "Uncached k", "Peak context k", "Requests", "Beyond first request k", "Cost $", "Focus away s",
               "Windows over user s"] + (
        ["ANR dialogs"] if any_dialogs else [])
    # A table of one task leaves out its name, which the page's heading gives.
    if len({r["task"] for r in runs}) == 1:
        columns, rows = columns[1:], [row[1:] for row in rows]
        return split_table(columns, rows, 3)
    return split_table(columns, rows, 4)


def matched(runs):
    """The runs of the tasks and agents that ran with hyprmasq and with another tool: a probe of a tool ran fewer
    agents than hyprmasq's set."""
    tools = {}
    for r in runs:
        tools.setdefault((r["task"], r["model"]), set()).add(r["backend"])
    return [r for r in runs if len(tools[(r["task"], r["model"])]) > 1 and "hyprmasq" in tools[(r["task"], r["model"])]]


def probe_figure(data):
    runs = matched(of_task(data, None))
    # A row of every task holds only a tool that ran each of the agent's tasks: one that ran some of them would have
    # medians of other tasks than hyprmasq's row.
    tasks_of = {}
    for r in runs:
        tasks_of.setdefault((r["model"], r["backend"]), set()).add(r["task"])
    runs = [r for r in runs if tasks_of[(r["model"], r["backend"])] == tasks_of[(r["model"], "hyprmasq")]]
    return comparison_figure(data, pooled="tasks", runs=runs)


def probe_tasks_figure(data):
    return comparison_figure(data, runs=matched(of_task(data, None)))


def probe_table(data):
    return comparison_table(dict(data, runs=matched(data["runs"])))


def compare_figure(data, before):
    """Each task's wall time per run before a change and after it, the medians over all agents."""
    builds = [("before", before["runs"]), ("after", data["runs"])]
    ts = tasks(data)
    for t in ts:
        if not any(r["task"] == t for r in before["runs"]):
            raise SystemExit(f"render.py: compare: the earlier dataset has no task {t}")
    failed = any(not r["passed"] for _, runs in builds for r in runs)
    svg = Svg()
    top = header(
        svg, "Wall time per run, before the change and after it",
        ["Each bar is the median wall time of a task's runs over all agents, written at the right with the runs",
         "passed. Its blue part is the median time in hyprmasq's MCP tools. Each dot is one run."],
        [("swatch", "tools", "median in hyprmasq's tools"), ("swatch", "out", "rest of the median wall time"),
         ("dot", "dot", "one run")] + ([("dot", "miss", "failed run")] if failed else []),
    )
    label_w = max(text_width(task_title(t), 12.5, True) for t in ts) + text_width("before", 12) + 36
    x0 = PAD + label_w
    right_w = 110
    plot_w = WIDTH - PAD - right_w - x0
    longest = max(r["wall_s"] for _, runs in builds for r in runs if r["task"] in ts)
    ticks, end = nice_ticks(longest)
    scale = plot_w / end
    row_h, gap = 22, 12
    y = top + 14
    svg.text(WIDTH - PAD - 56, y, "Median", cls="t2", size=12, weight="600", anchor="end")
    svg.text(WIDTH - PAD, y, "Passed", cls="t2", size=12, weight="600", anchor="end")
    plot_top = y + 8
    y = plot_top
    rows = []
    for t in ts:
        for b, (build, runs) in enumerate(builds):
            rows.append((y, t if b == 0 else "", build, [r for r in runs if r["task"] == t]))
            y += row_h
        y += gap
    bottom = y - gap + 4
    for v in ticks:
        x = x0 + v * scale
        svg.line(x, plot_top, x, bottom, "axis" if v == 0 else "grid")
        svg.text(x, bottom + 15, tick_label(v, " s" if v == ticks[-1] else ""), cls="m", size=11,
                 anchor="middle" if v else "start")
    for ry, task, build, rs in rows:
        cy = ry + row_h / 2
        base = cy + 4.5
        if task:
            svg.text(PAD, base, task_title(task), size=12.5, weight="600")
        svg.text(x0 - 12, base, build, cls="t2", size=12, anchor="end")
        wall = median(r["wall_s"] for r in rs)
        tools = min(median(r["tools_s"] for r in rs), wall)
        svg.bar(x0, cy - 6, tools * scale, 12, "tools", round_end=False)
        if (wall - tools) * scale > 2:
            svg.bar(x0 + tools * scale + 2, cy - 6, (wall - tools) * scale - 2, 12, "out")
        for r in sorted(rs, key=lambda r: r["wall_s"]):
            svg.circle(x0 + r["wall_s"] * scale, cy, 3.5 if r["passed"] else 3, "dot" if r["passed"] else "miss")
        svg.text(WIDTH - PAD - 56, base, f"{wall:.0f} s", cls="t2", size=12, anchor="end")
        svg.text(WIDTH - PAD, base, f"{sum(r['passed'] for r in rs)}/{len(rs)}", cls="t2", size=12, anchor="end")
    svg.height = bottom + 34
    alt = ("Wall time per run of each task before the change and after it: the median over all agents, with the "
           "median time in hyprmasq's tools inside it, a dot per run, and the runs passed.")
    return svg, alt


FIGURES = {"scoreboard": scoreboard, "time": time_figure, "kinds": kinds_figure, "act": act_figure,
           "compare": compare_figure, "glance": glance,
           "overview": lambda data: time_figure(data, counts=True), "comparison": comparison_figure, "comparison-time": comparison_time_figure,
           "comparison-pooled": lambda data: comparison_figure(data, pooled="agents"),
           "probe": probe_figure, "probe-tasks": probe_tasks_figure,

           "toolkits": toolkits_figure}


# The tables.

def thousands(r, key, by=1000):
    return r[key] / by if r.get(key) is not None else None


def cached(r):
    if not r.get("input_tokens") or r.get("cache_read_tokens") is None:
        return None
    return 100 * r["cache_read_tokens"] / r["input_tokens"]


def markdown_table(columns, rows):
    return "\n".join(["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
                     + ["| " + " | ".join(row) + " |" for row in rows])


# The columns of tokens and cost, which a wide table puts in a table of their own.
USAGE_COLUMNS = ["Input k", "Uncached k", "Cached %", "Peak context k", "Requests", "Beyond first request k", "Output k",
                 "Cost $"]


def split_table(columns, rows, keys):
    """A table too wide for GitHub's page as two, its outcome and time, and its tokens and cost, each starting with
    the first keys columns that name a row."""
    usage = [i for i, c in enumerate(columns) if c in USAGE_COLUMNS]
    rest = [i for i in range(keys, len(columns)) if i not in usage]
    out = []
    for title, picked in (("Outcome and time", rest), ("Tokens and cost", usage)):
        idx = list(range(keys)) + picked
        out.append(f"**{title}**\n\n" + markdown_table([columns[i] for i in idx], [[row[i] for i in idx] for row in rows]))
    return "\n\n".join(out)


def task_table(data, task):
    runs = [r for r in data["runs"] if r["task"] == task]
    labels = row_labels(runs)
    phase = PHASES.get(task)
    columns = ["Agent", "Effort"] + [
        "Passed", "Wall s", "In tools s", "Turn s", "Calls", "Actions", "Images", "Input k", "Cached %",
        "Output k", "Cost $"] + ([phase[1]] if phase else [])
    rows = []
    for key, rs in groups(runs):
        rows.append([labels[key][0], effort_text(rs),
            f"{sum(r['passed'] for r in rs)}/{len(rs)}",
            fmt([r["wall_s"] for r in rs]), fmt([r["tools_s"] for r in rs]), fmt([r["turn_s"] for r in rs], 1),
            fmt([r["calls"] for r in rs]), fmt([r["actions"] for r in rs]), fmt([r["images"] for r in rs]),
            fmt([thousands(r, "input_tokens") for r in rs]), fmt([cached(r) for r in rs]),
            fmt([thousands(r, "output_tokens") for r in rs]), fmt([r["cost_usd"] for r in rs], 3),
        ] + ([fmt([r["phases"].get(phase[0]) for r in rs])] if phase else []))
    return split_table(columns, rows, 2)


def total(runs, key):
    """The sum of a measure over the runs, unknown when any run's is."""
    return None if any(r[key] is None for r in runs) else sum(r[key] for r in runs)


def costs_table(data):
    runs = data["runs"]
    names = agent_names(runs)
    rows = []
    # A row per effort where an agent ran at several, so that their costs do not mix.
    split = any(len({r["effort"] for r in runs if (r["harness"], r["model"]) == a}) > 1 for a in agent_order(runs))
    for (a, effort), rs in (groups(runs) if split else
                            [((a, None), [r for r in runs if (r["harness"], r["model"]) == a])
                             for a in agent_order(runs)]):
        ok = sum(r["passed"] for r in rs)
        inp, cache, out, cost = (total(rs, k) for k in ("input_tokens", "cache_read_tokens", "output_tokens",
                                                        "cost_usd"))
        rows.append([names[a]] + ([effort_text(rs)] if split else []) + [
            f"{ok}/{len(rs)}", f"{inp / 1e6:.2f}" if inp is not None else "–",
            f"{100 * cache / inp:.0f}" if inp and cache is not None else "–",
            f"{out / 1000:.0f}" if out is not None else "–", f"{cost:.2f}" if cost is not None else "–",
            f"{cost / ok:.2f}" if ok and cost is not None else "–",
        ])
    note = partial_note(runs)
    return markdown_table(["Agent"] + (["Effort"] if split else []) + ["Passed", "Input M", "Cached %", "Output k",
                                                                       "Cost $", "Cost per passed run $"],
                          rows) + (f"\n\n{note}" if note else "")


def kinds_table(data, task=None):
    runs = kinds_runs(data, task)
    labels = row_labels(runs)
    efforts = several_efforts(runs)
    kinds = [k for k in KINDS if k in runs[0]["kinds"]]
    rows = []
    for key, rs in groups(runs):
        name, effort = labels[key]
        rows.append([name] + ([effort] if efforts else [])
                    + [fmt([r["kinds"][k]["seconds"] / r["kinds"][k]["items"] for r in rs], 1) for k in kinds]
                    + [str(sum(r["kinds"][k]["corrections"] for r in rs for k in kinds))])
    return markdown_table(["Agent"] + (["Effort"] if efforts else []) + [f"{k.capitalize()} s" for k in kinds]
                          + ["Corrections"], rows)


def compare_table(data, before):
    """Each task's runs passed and medians over all of them, before and after, as "before → after"."""
    old = before["runs"]
    rows = []
    for task in tasks(data):
        a = [r for r in data["runs"] if r["task"] == task]
        b = [r for r in old if r["task"] == task]
        if not b:
            raise SystemExit(f"render.py: compare: the earlier dataset has no task {task}")

        def both(key, digits=0):
            return " → ".join(f"{v:.{digits}f}" if v is not None else "–"
                              for v in (median(r[key] for r in b), median(r[key] for r in a)))

        rows.append([task_title(task), f"{sum(r['passed'] for r in b)}/{len(b)} → {sum(r['passed'] for r in a)}/{len(a)}",
                     both("wall_s"), both("tools_s"), both("turn_s", 1), both("calls"), both("actions"),
                     both("images")])
    return markdown_table(["Task", "Passed", "Wall s", "In tools s", "Turn s", "Calls", "Actions", "Images"], rows)


TABLES = {"costs": costs_table, "table": task_table, "kinds-table": kinds_table}


def toolkits_table(data):
    runs = toolkit_runs(data)
    names = agent_names(runs)
    efforts = several_efforts(runs)
    rows = []
    for t in dict.fromkeys(r["task"] for r in runs):
        for (a, _), rs in groups([r for r in runs if r["task"] == t]):
            rows.append([t, names[a]] + ([effort_text(rs)] if efforts else []) + [f"{sum(r['passed'] for r in rs)}/{len(rs)}",
                fmt([r["wall_s"] for r in rs]), fmt([r["tools_s"] for r in rs]),
                fmt([(r["act_s"] or {}).get("wait") for r in rs]), fmt([r["turn_s"] for r in rs], 1),
                fmt([r["calls"] for r in rs]), fmt([r["actions"] for r in rs]),
                fmt([thousands(r, "input_tokens") for r in rs]), fmt([cached(r) for r in rs]),
                fmt([r["cost_usd"] for r in rs], 3),
                str(sum(k["corrections"] for r in rs for k in (r.get("kinds") or {}).values()))])
    columns = ["Toolkit", "Agent"] + (["Effort"] if efforts else []) + [
        "Passed", "Wall s", "In tools s", "Settle wait s", "Turn s", "Calls", "Actions", "Input k", "Cached %",
        "Cost $", "Corrections"]
    return split_table(columns, rows, 3 if efforts else 2)


TABLES = {"costs": costs_table, "table": task_table, "kinds-table": kinds_table, "compare-table": compare_table,
          "comparison-table": comparison_table, "toolkits-table": toolkits_table, "probe-table": probe_table}
# The views that take a TASK, and those that take an earlier build's dataset instead.
BY_TASK = {"time", "table", "kinds", "kinds-table", "comparison", "comparison-time", "comparison-table"}
COMPARE = {"compare", "compare-table"}


# The Markdown blocks.

# Markers stand at the start of a line, so that text can quote them.
BLOCK = re.compile(r"^(<!-- render (\S+) (\S+)(?: (\S+))? -->\n).*?^(<!-- /render -->)", re.S | re.M)


def render_block(markdown_path, dataset, view, arg, files):
    """The Markdown of one block, adding its figures' files to files."""
    doc_dir = os.path.dirname(os.path.abspath(markdown_path))
    base = page_dir(markdown_path)
    data = load(os.path.join(doc_dir, base, "data"), dataset)
    # The compare views take the dataset of the build before in place of a task.
    if view in COMPARE:
        if not arg:
            raise SystemExit(f"render.py: {markdown_path}: {view} names the dataset to compare {dataset} with")
        args = [load(os.path.join(doc_dir, base, "data"), arg)]
    elif arg and view != "table" and arg in {r.get("task_of") for r in data["runs"]}:
        # A task held in several variants: the view of its variants alone, each as a task of its own. A table is
        # of one variant.
        data = dict(data, runs=[r for r in data["runs"] if r.get("task_of") == arg])
        args = []
    elif arg and view not in BY_TASK:
        raise SystemExit(f"render.py: {markdown_path}: {view} takes no task")
    elif (view == "table" or arg) and arg not in tasks(data):
        raise SystemExit(f"render.py: {markdown_path}: {dataset} has no task {arg}. It has {', '.join(tasks(data))}.")
    else:
        args = [arg] if arg else []
    if view in TABLES:
        return (f"<details><summary>{table_summary(view, arg)}</summary>\n\n{TABLES[view](data, *args)}\n\n"
                "</details>\n")
    if view not in FIGURES:
        raise SystemExit(f"render.py: {markdown_path}: unknown view {view}")
    svg, alt = FIGURES[view](data, *args)
    # A figure of several datasets is named after its view alone, unless an earlier block of the page took that name:
    # then after its last dataset too, so that no block draws over another's figure.
    stem = "-".join(([dataset] if "," not in dataset else []) + [view] + ([arg] if arg else []))
    paths = lambda stem: [os.path.join(doc_dir, base, stem + suffix + ".svg") for suffix in ("", "-dark")]
    if "," in dataset and any(p in files for p in paths(stem)):
        stem = f"{dataset.rsplit(',', 1)[1]}-{stem}"
    if any(p in files for p in paths(stem)):
        raise SystemExit(f"render.py: {markdown_path}: two blocks draw the figure {stem}")
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        files[os.path.join(doc_dir, base, stem + suffix + ".svg")] = svg.render(theme, alt)
    # The datasets' notes, such as which build a model ran on, stand under the overview, not inside a figure.
    notes = data.get("notes", []) if view in ("scoreboard", "glance") else []
    return (
        "<picture>\n"
        f'  <source media="(prefers-color-scheme: dark)" srcset="{base}/{stem}-dark.svg">\n'
        f'  <img alt="{html.escape(alt)}" src="{base}/{stem}.svg">\n'
        "</picture>\n"
        + (f"\n<sub>{html.escape(' '.join(notes))}</sub>\n" if notes else "")
    )


def table_summary(view, arg):
    """The line a table's block shows while it is folded, which says what the table holds."""
    if view in TABLE_SUMMARIES:
        return TABLE_SUMMARIES[view] + (f": {task_title(arg)}" if view == "kinds-table" and arg else "")
    if not arg:
        return "Numbers"
    return f"Numbers: {task_title(arg)}"


TABLE_SUMMARIES = {"costs": "Tokens and cost per agent", "kinds-table": "Numbers: seconds per question of each kind",
                   "compare-table": "Numbers before → after", "toolkits-table": "Numbers per toolkit and agent"}


def page_dir(markdown_path):
    """The directory beside a page that holds its data and figures: docs/benchmarks for docs/benchmarks.md."""
    return os.path.splitext(os.path.basename(markdown_path))[0]


def render(markdown_path, text, files):
    # A marker that lost its pair would leave a block, or a stretch of handwritten text, out of the generated ones.
    blocks = len(BLOCK.findall(text))
    if not blocks == len(re.findall(r"^<!-- render ", text, re.M)) == len(re.findall(r"^<!-- /render -->", text, re.M)):
        raise SystemExit(f"render.py: {markdown_path}: a <!-- render ... --> or <!-- /render --> marker has no pair")
    return BLOCK.sub(
        lambda m: m.group(1) + render_block(markdown_path, m.group(2), m.group(3), m.group(4), files) + m.group(5),
        text,
    )


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("markdown", nargs="*", default=[os.path.join(root, "docs", "benchmarks.md"),
                                                        os.path.join(root, "docs", "comparison.md")])
    args = parser.parse_args(argv)
    files = {}
    for path in args.markdown:
        with open(path, encoding="utf-8", newline="") as f:
            files[path] = render(path, f.read(), files)
    # Figures that no block draws any more.
    figure_dirs = {os.path.join(os.path.dirname(os.path.abspath(p)), page_dir(p)) for p in args.markdown}
    orphans = sorted(os.path.join(d, n) for d in figure_dirs if os.path.isdir(d)
                     for n in os.listdir(d) if n.endswith(".svg") and os.path.join(d, n) not in files)
    stale = []
    for path, content in files.items():
        current = None
        if os.path.exists(path):
            with open(path, encoding="utf-8", newline="") as f:
                current = f.read()
        if current == content:
            continue
        if args.check:
            stale.append(os.path.relpath(path))
        else:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            print(f"wrote {os.path.relpath(path)}")
    for path in orphans:
        if args.check:
            stale.append(os.path.relpath(path) + " (drawn by no block)")
        else:
            os.remove(path)
            print(f"removed {os.path.relpath(path)}")
    if stale:
        sys.exit("render.py: out of date, run python3 bench/render.py: " + ", ".join(stale))


if __name__ == "__main__":
    main(sys.argv[1:])
