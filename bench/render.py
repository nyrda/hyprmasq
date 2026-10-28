#!/usr/bin/env python3
"""Renders the benchmark results in docs/benchmarks.md from the datasets that export.py writes.

    render.py [--check] [MARKDOWN...]

Each block of a Markdown file, by default docs/benchmarks.md, between

    <!-- render DATASET VIEW [TASK] -->
    <!-- /render -->

is replaced with the view of docs/benchmarks/data/DATASET.json (the data directory beside the Markdown file).
Figures are SVG files in the benchmarks directory beside it, one for GitHub's light theme and one for its dark
theme, which a <picture> element picks between. Views:

- scoreboard: a row per agent, the runs of each task passed or failed with their median wall time, and the cost
  per passed run.
- time: each run's wall time and the medians of the time in hyprmasq's tools and outside them, a panel per task,
  or only TASK's.
- kinds: the form task's seconds per question of each kind, a bar per model.
- act: what the act calls' time went to, as analyze.py splits it.
- costs: a table of the tokens and the cost of each agent's runs.
- table: a table of the medians of TASK's runs, a row per agent and effort.
- kinds-table: a table of the form task's seconds per question of each kind.
- compare: a table of each task's runs passed and medians, beside those of the dataset TASK names, an earlier
  build's: `<!-- render AFTER compare BEFORE -->`.

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
    "gpt-6-astra": "gpt-6-astra",
    "gpt-6.1-terra": "gpt-6.1-terra",
    "claude-opus-5-5": "Opus 5.5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-haiku-5-5": "Haiku 5.5",
    "tensorx/deepseek/deepseek-v4.1-flash-1017": "DeepSeek v4.1 Flash",
}
EFFORTS = ["default", "none", "off", "minimal", "low", "medium", "high", "xhigh", "max"]
KINDS = ["checkbox", "slider", "text", "dropdown"]
# The milestone each task's table reports, with its column.
PHASES = {"form": ("all answered correctly", "All right at s")}
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

    def text(self, x, y, s, cls="t", size=12.5, anchor="start", weight=None):
        attrs = f' text-anchor="{anchor}"' if anchor != "start" else ""
        attrs += f' font-weight="{weight}"' if weight else ""
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
    with open(os.path.join(data_dir, name + ".json"), encoding="utf-8") as f:
        return json.load(f)


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

def scoreboard(data):
    runs = data["runs"]
    ts = tasks(data)
    agents = agent_order(runs)
    names = agent_names(runs)
    svg = Svg()
    total = len(runs)
    passed = sum(r["passed"] for r in runs)
    top = header(
        svg, "Runs passed, wall time and cost",
        [f"{passed} of {total} runs passed. Each dot is one run. The time is the task's median wall time."]
        + [line for line in [effort_note(runs)] if line],
        [("dot", "pass", "passed"), ("dot", "fail", "failed")],
    )
    label_w = max(text_width(n, 13, True) for n in names.values()) + 24
    passed_w, cost_w = 64, 200
    task_w = (WIDTH - 2 * PAD - label_w - passed_w - cost_w) / len(ts)
    x_passed = PAD + label_w
    x_task = x_passed + passed_w
    x_cost = x_task + task_w * len(ts)
    head_y = top + 14
    svg.text(x_passed, head_y, "Passed", cls="t2", size=12, weight="600")
    for i, t in enumerate(ts):
        svg.text(x_task + i * task_w, head_y, t.capitalize(), cls="t2", size=12, weight="600")
    svg.text(x_cost, head_y, "Cost per passed run", cls="t2", size=12, weight="600")
    row_h = 34
    # The dots of a task's runs move closer when there are more than its column holds beside the time.
    reps = max(sum(1 for r in runs if (r["harness"], r["model"]) == a and r["task"] == t) for a in agents for t in ts)
    step = min(14, (task_w - 60) / reps)
    y0 = head_y + 12
    svg.line(PAD, y0, WIDTH - PAD, y0, "axis")
    per_pass = {}
    for a in agents:
        rs = [r for r in runs if (r["harness"], r["model"]) == a]
        ok = sum(r["passed"] for r in rs)
        cost = sum(r["cost_usd"] or 0 for r in rs)
        per_pass[a] = cost / ok if ok and all(r["cost_usd"] is not None for r in rs) else None
    top_cost = max((v for v in per_pass.values() if v is not None), default=0)
    bar_max = cost_w - 64
    for i, a in enumerate(agents):
        cy = y0 + row_h * i + row_h / 2
        base = cy + 4.5
        rs = [r for r in runs if (r["harness"], r["model"]) == a]
        svg.text(PAD, base, names[a], size=13, weight="600")
        svg.text(x_passed, base, f"{sum(r['passed'] for r in rs)}/{len(rs)}", size=13)
        for j, t in enumerate(ts):
            tr = sorted((r for r in rs if r["task"] == t), key=lambda r: r["rep"])
            x = x_task + j * task_w
            for k, r in enumerate(tr):
                svg.circle(x + 5 + k * step, cy, 5 if r["passed"] else 4.25, "pass" if r["passed"] else "fail")
            wall = median(r["wall_s"] for r in tr)
            if wall is not None:
                svg.text(x + 5 + len(tr) * step + 4, base, f"{wall:.0f} s", cls="t2", size=12.5)
        cost = per_pass[a]
        if cost is None:
            svg.text(x_cost, base, "–", cls="t2", size=12.5)
        else:
            w = max(2, bar_max * cost / top_cost) if top_cost else 2
            svg.bar(x_cost, cy - 5, w, 10, "tools")
            svg.text(x_cost + w + 8, base, f"${cost:.2f}", cls="t2", size=12.5)
        if i < len(agents) - 1:
            svg.line(PAD, y0 + row_h * (i + 1), WIDTH - PAD, y0 + row_h * (i + 1), "grid")
    svg.height = y0 + row_h * len(agents) + 20
    alt = (f"Runs passed, wall time and cost. {passed} of {total} runs passed. A row per agent shows its runs of "
           "each task, their median wall time and the cost per passed run.")
    return svg, alt


def time_panel(svg, rows, x0, width, y0, row_h, title, note):
    """A panel of horizontal bars, a row per group: the medians in tools and outside, and a dot per run."""
    if title:
        svg.text(x0, y0, title, size=13, weight="600")
    svg.text(x0 + width, y0, note, cls="m", size=12, anchor="end")
    # The medians stand in a column at the panel's right.
    plot_w = width - 74
    tops = sorted((max(r["wall_s"] for r in rs) for _, rs in rows if rs), reverse=True)
    # An agent far slower than the rest would squeeze them: its bar is cut at the edge instead.
    limit = tops[0] if len(tops) < 2 or tops[0] <= 2.5 * tops[1] else tops[1] * 1.15
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
        if not rs:
            continue
        cy = top + row_h * i + row_h / 2
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
    return bottom + 15


def time_figure(data, only=None):
    runs = data["runs"]
    ts = [only] if only else tasks(data)
    labels = row_labels(runs)
    svg = Svg()
    top = header(
        svg,
        "Wall time per run" + (f": {ts[0].capitalize()}" if len(ts) == 1 else ""),
        ["Each bar is a median wall time, written at the right. Its blue part is the median time in hyprmasq's",
         "MCP tools, and the grey part fills the bar up to the median wall time. Each dot is one run."]
        + [line for line in [effort_note([r for r in runs if r["task"] in ts])] if line],
        [("swatch", "tools", "median in hyprmasq's tools"), ("swatch", "out", "rest of the median wall time"),
         ("dot", "dot", "one run")]
        + ([("dot", "miss", "failed run")] if any(not r["passed"] for r in runs if r["task"] in ts) else []),
    )
    keys = [key for key, _ in groups([r for r in runs if r["task"] in ts])]
    efforts = several_efforts(runs)
    label_w = max(text_width(labels[k][0], 12.5, True) for k in keys) + (
        max(text_width(labels[k][1], 12) for k in keys) + 16 if efforts else 0) + 20
    columns = 1 if len(ts) == 1 else 2
    gap = 36
    panel_w = (WIDTH - 2 * PAD - label_w - gap * (columns - 1)) / columns
    row_h = 24 if efforts else 26
    y = top + 18
    for start in range(0, len(ts), columns):
        bottom = y
        for c, t in enumerate(ts[start:start + columns]):
            # Every panel has a row for each key, empty where the task has no runs of it, so that rows line up.
            by_key = dict(groups([r for r in runs if r["task"] == t]))
            rows = [(key, by_key.get(key, [])) for key in keys]
            ok = sum(r["passed"] for _, rs in rows for r in rs)
            n = sum(len(rs) for _, rs in rows)
            x0 = PAD + label_w + c * (panel_w + gap)
            title = t.capitalize() if len(ts) > 1 else ""
            bottom = max(bottom, time_panel(svg, rows, x0, panel_w, y, row_h, title, f"{ok}/{n} passed"))
            if c == 0:
                for i, key in enumerate(keys):
                    name, effort = labels[key]
                    base = y + 12 + row_h * i + row_h / 2 + 4.5
                    first = i == 0 or keys[i - 1][0] != key[0]
                    if first:
                        svg.text(PAD, base, name, size=12.5, weight="600")
                    if effort:
                        svg.text(PAD + label_w - 16, base, effort, cls="t2", size=12, anchor="end")
        y = bottom + 34
    svg.height = y - 10
    alt = ("Wall time per run of each agent" + (f" in the {ts[0]} task" if len(ts) == 1 else ", a panel per task")
           + ". Each bar is the median wall time with the median time in hyprmasq's tools inside it, and each dot "
           "one run.")
    return svg, alt


def kinds_runs(data):
    """The runs whose time per kind counts: passed ones, as a run that stopped early has unanswered questions whose
    time is none."""
    runs = [r for r in data["runs"] if r.get("kinds") and r["passed"]]
    if not runs:
        raise SystemExit("render.py: the kinds views need passed runs of the form task")
    return runs


def kinds_figure(data):
    runs = kinds_runs(data)
    agents = agent_order(runs)
    if len(agents) > len(THEMES["light"]["series"]):
        raise SystemExit(f"render.py: the kinds figure has colours for {len(THEMES['light']['series'])} models")
    names = agent_names(runs)
    kinds = [k for k in KINDS if k in runs[0]["kinds"]] + [k for k in runs[0]["kinds"] if k not in KINDS]
    per = lambda rs, k: median(r["kinds"][k]["seconds"] / r["kinds"][k]["items"] for r in rs)  # noqa: E731
    values = {(a, k): per([r for r in runs if (r["harness"], r["model"]) == a], k) for a in agents for k in kinds}
    svg = Svg()
    efforts = sorted({r["effort"] for r in runs}, key=effort_key)
    top = header(
        svg, "Seconds per question by kind",
        ["The time from the previous answer to this one. Medians of each model's passed runs"
         + (f" at {', '.join(efforts[:-1])} and {efforts[-1]} effort" if len(efforts) > 1 else "") + "."],
        [("swatch", f"s{i}", names[a]) for i, a in enumerate(agents)],
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
            svg.bar(x0, by, v * scale, bar_h, f"s{i}")
            svg.text(x0 + v * scale + 6, by + bar_h - 0.5, f"{v:.1f}", cls="t2", size=11)
        y += group_h + group_gap
    svg.height = bottom + 40
    alt = "Seconds per question by kind, a bar per model. Over all runs: " + ", ".join(
        f"{k} {per(runs, k):.1f} s" for k in kinds)
    return svg, alt


def act_figure(data):
    runs = data["runs"]
    totals = {key: sum(r["act_s"].get(key, 0) for r in runs) for key, _, _ in ACT_PARTS}
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


FIGURES = {"scoreboard": scoreboard, "time": time_figure, "kinds": kinds_figure, "act": act_figure}


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
    return markdown_table(columns, rows)


def total(runs, key):
    """The sum of a measure over the runs, unknown when any run's is."""
    return None if any(r[key] is None for r in runs) else sum(r[key] for r in runs)


def costs_table(data):
    runs = data["runs"]
    names = agent_names(runs)
    rows = []
    for a in agent_order(runs):
        rs = [r for r in runs if (r["harness"], r["model"]) == a]
        ok = sum(r["passed"] for r in rs)
        inp, cache, out, cost = (total(rs, k) for k in ("input_tokens", "cache_read_tokens", "output_tokens",
                                                        "cost_usd"))
        rows.append([
            names[a], f"{ok}/{len(rs)}", f"{inp / 1e6:.2f}" if inp is not None else "–",
            f"{100 * cache / inp:.0f}" if inp and cache is not None else "–",
            f"{out / 1000:.0f}" if out is not None else "–", f"{cost:.2f}" if cost is not None else "–",
            f"{cost / ok:.2f}" if ok and cost is not None else "–",
        ])
    return markdown_table(["Agent", "Passed", "Input M", "Cached %", "Output k", "Cost $", "Cost per passed run $"],
                          rows)


def kinds_table(data):
    runs = kinds_runs(data)
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

        rows.append([task, f"{sum(r['passed'] for r in b)}/{len(b)} → {sum(r['passed'] for r in a)}/{len(a)}",
                     both("wall_s"), both("tools_s"), both("turn_s", 1), both("calls"), both("actions"),
                     both("images")])
    return markdown_table(["Task", "Passed", "Wall s", "In tools s", "Turn s", "Calls", "Actions", "Images"], rows)


TABLES = {"costs": costs_table, "table": task_table, "kinds-table": kinds_table}


# The Markdown blocks.

# Markers stand at the start of a line, so that text can quote them.
BLOCK = re.compile(r"^(<!-- render (\S+) (\S+)(?: (\S+))? -->\n).*?^(<!-- /render -->)", re.S | re.M)


def render_block(markdown_path, dataset, view, arg, files):
    """The Markdown of one block, adding its figures' files to files."""
    doc_dir = os.path.dirname(os.path.abspath(markdown_path))
    data = load(os.path.join(doc_dir, "benchmarks", "data"), dataset)
    if view == "compare":
        if not arg:
            raise SystemExit(f"render.py: {markdown_path}: compare names the dataset to compare {dataset} with")
        return compare_table(data, load(os.path.join(doc_dir, "benchmarks", "data"), arg)) + "\n"
    if (view == "table" or arg) and arg not in tasks(data):
        raise SystemExit(f"render.py: {markdown_path}: {dataset} has no task {arg}. It has {', '.join(tasks(data))}.")
    if view in TABLES:
        table = TABLES[view](data, arg) if view == "table" else TABLES[view](data)
        return f"<details><summary>Numbers</summary>\n\n{table}\n\n</details>\n"
    if view not in FIGURES:
        raise SystemExit(f"render.py: {markdown_path}: unknown view {view}")
    svg, alt = FIGURES[view](data, arg) if arg else FIGURES[view](data)
    stem = "-".join([dataset, view] + ([arg] if arg else []))
    for theme, suffix in (("light", ""), ("dark", "-dark")):
        files[os.path.join(doc_dir, "benchmarks", stem + suffix + ".svg")] = svg.render(theme, alt)
    return (
        "<picture>\n"
        f'  <source media="(prefers-color-scheme: dark)" srcset="benchmarks/{stem}-dark.svg">\n'
        f'  <img alt="{html.escape(alt)}" src="benchmarks/{stem}.svg">\n'
        "</picture>\n"
    )


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
    parser.add_argument("markdown", nargs="*", default=[os.path.join(root, "docs", "benchmarks.md")])
    args = parser.parse_args(argv)
    files = {}
    for path in args.markdown:
        with open(path, encoding="utf-8", newline="") as f:
            files[path] = render(path, f.read(), files)
    # Figures that no block draws any more.
    figure_dirs = {os.path.join(os.path.dirname(os.path.abspath(p)), "benchmarks") for p in args.markdown}
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
