#!/usr/bin/env python3
"""Render light and dark SVG figures for docs/comparison-cua-driver-bench.md
from bench/cua-driver-bench/data/*.json.

    python3 bench/cua-driver-bench/render.py [--check]
"""
from __future__ import annotations

import argparse
import html
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(__file__).resolve().parent / "data"
OUT_DIR = ROOT / "docs" / "comparison-cua-driver-bench"

WIDTH = 880
FONT = '-apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans", Helvetica, Arial, sans-serif'

# Matches bench/render.py's GitHub light and dark themes verbatim.
THEMES = {
    "light": {
        "surface": "#ffffff",
        "border": "#d1d9e0",
        "ink": "#1f2328",
        "ink2": "#59636e",
        "muted": "#818b98",
        "grid": "#eff2f5",
        "axis": "#d1d9e0",
        "out": "#c8d1da",
        "dot": "#424a53",
        "good": "#1a7f37",
        "bad": "#d1242f",
        "s0": "#2a78d6",
        "s1": "#eb6834",
        "s6": "#6250d6",
        "s7": "#e34948",
    },
    "dark": {
        "surface": "#0d1117",
        "border": "#3d444d",
        "ink": "#f0f6fc",
        "ink2": "#9198a1",
        "muted": "#7d8590",
        "grid": "#1c2128",
        "axis": "#3d444d",
        "out": "#454c56",
        "dot": "#d1d7e0",
        "good": "#3fb950",
        "bad": "#f85149",
        "s0": "#3987e5",
        "s1": "#d95926",
        "s6": "#9085e9",
        "s7": "#e66767",
    },
}

TASK_META = [
    ("MB-09", "List drag and drop"),
    ("MB-10", "Hover overlay"),
    ("MB-11", "Native tooltip"),
    ("IR-01", "Permission dialog"),
    ("IR-02", "Update banner"),
    ("IR-03", "Unsaved-changes modal"),
    ("IR-04", "Cookie consent sheet"),
]

BG_ORDER = [
    ("MB-09", "List drag and drop", False),
    ("IR-01", "Permission dialog", False),
    ("IR-02", "Update banner", False),
    ("IR-03", "Unsaved-changes modal", False),
    ("IR-04", "Cookie consent sheet", False),
    ("MB-10", "Hover overlay", True),
    ("MB-11", "Native tooltip", True),
]


def nfmt(v: float) -> str:
    r = round(v, 1)
    return str(int(r)) if r == int(r) else str(r)


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def wilson(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt((p * (1.0 - p) + z2 / (4.0 * n)) / n)
    return (max(0.0, center - margin), min(1.0, center + margin))


def med(values: list[float | int | None]) -> float:
    clean = [float(v) for v in values if v is not None]
    return statistics.median(clean)


def rbar(x: float, y: float, w: float, h: float, r: float = 3.0) -> str:
    """Horizontal bar with rounded right corners, matching docs/comparison/usage.svg."""
    w = max(w, 2.0)
    rr = min(r, w / 2.0, h / 2.0)
    x2 = x + w
    y2 = y + h
    return (
        f"M{nfmt(x)} {nfmt(y)}H{nfmt(x2 - rr)}"
        f"Q{nfmt(x2)} {nfmt(y)} {nfmt(x2)} {nfmt(y + rr)}"
        f"V{nfmt(y2 - rr)}"
        f"Q{nfmt(x2)} {nfmt(y2)} {nfmt(x2 - rr)} {nfmt(y2)}"
        f"H{nfmt(x)}Z"
    )


def svg_wrap(height: int, theme_name: str, title: str, body: list[str]) -> str:
    c = THEMES[theme_name]
    style = (
        f"text{{font-family:{FONT};font-variant-numeric:tabular-nums}}"
        f".t{{fill:{c['ink']}}}.t2{{fill:{c['ink2']}}}.m{{fill:{c['muted']}}}"
        f".bg{{fill:{c['surface']};stroke:{c['border']}}}"
        f".grid{{stroke:{c['grid']};stroke-width:1}}.axis{{stroke:{c['axis']};stroke-width:1}}"
        f".out{{fill:{c['out']}}}.pass{{fill:{c['good']}}}"
        f".dot0{{fill:{c['s0']};stroke:{c['surface']};stroke-width:1.5}}"
        f".dot1{{fill:{c['s1']};stroke:{c['surface']};stroke-width:1.5}}"
        f".ring1{{fill:{c['surface']};stroke:{c['s1']};stroke-width:1.75}}"
        f".dot{{fill:{c['dot']};stroke:{c['surface']};stroke-width:1.5}}"
        f".whisker{{stroke:{c['ink']};stroke-width:1.5}}"
        f".range{{stroke:{c['axis']};stroke-width:1.5}}"
        f".s0{{fill:{c['s0']}}}.s1{{fill:{c['s1']}}}.s6{{fill:{c['s6']}}}.s7{{fill:{c['s7']}}}"
    )
    t_esc = esc(title)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" aria-label="{t_esc}">',
        f"<title>{t_esc}</title>",
        f"<style>{style}</style>",
        f'<rect x="0.5" y="0.5" width="{WIDTH - 1}" height="{height - 1}" rx="6" class="bg"/>',
        *body,
        "</svg>\n",
    ]
    return "\n".join(lines)


def select_suite(trials: list[dict], key: str) -> list[dict]:
    if key == "all":
        return trials
    if key == "standard_60":
        return [t for t in trials if t["suite"] in ("foreground", "standard_background")]
    return [t for t in trials if t["suite"] == key]


def render_scorecard(hm: dict, cua: dict, theme_name: str) -> str:
    alt = (
        "CuaDriverBench on Omarchy 4.5 with GPT-6.2 Sol: pass rates with 95 % Wilson intervals, "
        "median wall time, tool calls and input tokens for hyprmasq 0.5.1 and Cua Driver 0.62.1 "
        "across standard, foreground, background and extended background suites."
    )
    suites = [
        ("standard_60", "Standard Omarchy suite", "35 foreground + 25 background"),
        ("foreground", "Foreground", "7 BenchLab tasks · 5 seeds each"),
        ("standard_background", "Standard background", "MB-09 and IR-01 to IR-04"),
        ("extended_background", "Extended background", "MB-10 hover and MB-11 tooltip"),
    ]

    # X layout (880 px total, 24 px margins)
    x_rate0, x_rate1 = 316.0, 504.0  # 0% to 100%
    x_pct = 516.0
    x_cnt = 558.0
    x_time = 648.0
    x_calls = 724.0
    x_in = 812.0

    def rx(p: float) -> float:
        return x_rate0 + p * (x_rate1 - x_rate0)

    y_hdr = 104
    y_grid_top = 132
    block_h = 62
    y_grid_bot = y_grid_top + len(suites) * block_h
    height = y_grid_bot + 42

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">CuaDriverBench on Omarchy 4.5 · GPT-6.2 Sol</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">140 trials (70 per driver), Codex CLI 0.171.0 at medium effort. The dot is the pass rate and the line its</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">95 % Wilson interval. Wall time runs from prompt start to finish, including all tool calls.</text>')

    # Legend on header line
    b.append(f'<rect x="24" y="{y_hdr - 9}" width="10" height="10" class="s0" rx="2"/>')
    b.append(f'<text x="39" y="{y_hdr}" class="t" font-size="12" font-weight="600">hyprmasq 0.5.1</text>')
    b.append(f'<rect x="150" y="{y_hdr - 9}" width="10" height="10" class="s1" rx="2"/>')
    b.append(f'<text x="165" y="{y_hdr}" class="t" font-size="12" font-weight="600">Cua Driver 0.62.1</text>')

    # Column headers
    b.append(f'<text x="{nfmt(x_rate0)}" y="{y_hdr}" class="t2" font-size="12">Trials passed</text>')
    for tick in (0, 50, 100):
        tx = rx(tick / 100.0)
        b.append(f'<text x="{nfmt(tx)}" y="{y_hdr + 17}" class="m" font-size="10.5" text-anchor="middle">{tick} %</text>')
        b.append(f'<line x1="{nfmt(tx)}" y1="{y_grid_top}" x2="{nfmt(tx)}" y2="{y_grid_bot}" class="grid"/>')
    # Subtle 25% and 75% gridlines
    for tick in (25, 75):
        tx = rx(tick / 100.0)
        b.append(f'<line x1="{nfmt(tx)}" y1="{y_grid_top}" x2="{nfmt(tx)}" y2="{y_grid_bot}" class="grid"/>')

    b.append(f'<text x="{nfmt(x_time)}" y="{y_hdr}" class="t2" font-size="12" text-anchor="middle">Median</text>')
    b.append(f'<text x="{nfmt(x_time)}" y="{y_hdr + 15}" class="t2" font-size="12" text-anchor="middle">wall s</text>')
    b.append(f'<text x="{nfmt(x_calls)}" y="{y_hdr}" class="t2" font-size="12" text-anchor="middle">Median</text>')
    b.append(f'<text x="{nfmt(x_calls)}" y="{y_hdr + 15}" class="t2" font-size="12" text-anchor="middle">calls</text>')
    b.append(f'<text x="{nfmt(x_in)}" y="{y_hdr}" class="t2" font-size="12" text-anchor="middle">Median</text>')
    b.append(f'<text x="{nfmt(x_in)}" y="{y_hdr + 15}" class="t2" font-size="12" text-anchor="middle">input tok</text>')

    b.append(f'<line x1="24" y1="{y_grid_top}" x2="856" y2="{y_grid_top}" class="grid"/>')

    for idx, (key, label, sub) in enumerate(suites):
        top = y_grid_top + idx * block_h
        if idx > 0:
            b.append(f'<line x1="24" y1="{top}" x2="856" y2="{top}" class="grid"/>')
        b.append(f'<text x="24" y="{top + 25}" class="t" font-size="13" font-weight="600">{esc(label)}</text>')
        b.append(f'<text x="24" y="{top + 42}" class="m" font-size="10.5">{esc(sub)}</text>')

        hm_sub = select_suite(hm["trials"], key)
        cua_sub = select_suite(cua["trials"], key)
        rows = [
            ("hyprmasq", "s0", "dot0", hm_sub, top + 19),
            ("Cua Driver", "s1", "dot1", cua_sub, top + 44),
        ]
        hm_t = med([t["agent_seconds"] for t in hm_sub])
        hm_c = med([t["tool_calls"] for t in hm_sub])
        hm_i = med([t["input_tokens"] for t in hm_sub])

        for dname, bar_cls, dot_cls, sub_trials, cy in rows:
            n = len(sub_trials)
            k = sum(1 for t in sub_trials if t["passed"])
            p = k / n
            lo, hi = wilson(k, n)
            # Driver chip
            b.append(f'<rect x="202" y="{cy - 6}" width="4" height="12" class="{bar_cls}" rx="2"/>')
            b.append(f'<text x="212" y="{cy + 4}" class="t2" font-size="11.5">{dname}</text>')
            # Wilson whisker + dot
            b.append(f'<line x1="{nfmt(rx(lo))}" y1="{cy}" x2="{nfmt(rx(hi))}" y2="{cy}" class="whisker"/>')
            b.append(f'<circle cx="{nfmt(rx(p))}" cy="{cy}" r="4.5" class="{dot_cls}"/>')
            # Pass rate + count
            b.append(f'<text x="{nfmt(x_pct)}" y="{cy + 4}" class="t" font-size="12.5" font-weight="600">{round(100 * p)} %</text>')
            b.append(f'<text x="{nfmt(x_cnt)}" y="{cy + 4}" class="m" font-size="11">{k}/{n}</text>')
            # Time, calls, input tokens
            t_med = med([t["agent_seconds"] for t in sub_trials])
            c_med = med([t["tool_calls"] for t in sub_trials])
            i_med = med([t["input_tokens"] for t in sub_trials])
            t_str = f"{t_med:.1f} s"
            c_str = f"{c_med:.1f}" if c_med != int(c_med) else f"{int(c_med)}"
            i_str = f"{i_med / 1000:.0f}k"
            if dname == "Cua Driver":
                t_str = f"{t_med:.1f} s ({t_med / hm_t:.1f}×)"
                c_str = f"{c_str} ({c_med / hm_c:.1f}×)"
                i_str = f"{i_str} ({i_med / hm_i:.1f}×)"
            cls_num = "t" if dname == "hyprmasq" else "t2"
            b.append(f'<text x="{nfmt(x_time)}" y="{cy + 4}" class="{cls_num}" font-size="12" text-anchor="middle">{t_str}</text>')
            b.append(f'<text x="{nfmt(x_calls)}" y="{cy + 4}" class="{cls_num}" font-size="12" text-anchor="middle">{c_str}</text>')
            b.append(f'<text x="{nfmt(x_in)}" y="{cy + 4}" class="{cls_num}" font-size="12" text-anchor="middle">{i_str}</text>')

    b.append(f'<line x1="24" y1="{y_grid_bot}" x2="856" y2="{y_grid_bot}" class="grid"/>')
    b.append(
        f'<text x="24" y="{y_grid_bot + 24}" class="m" font-size="11">'
        f'All 35 background trials of each driver passed all 8 A22 user-undisturbed criteria with 0 GUI-only violations.'
        f"</text>"
    )
    return svg_wrap(height, theme_name, alt, b)


def classify_trial(trial: dict) -> str:
    if trial["passed"]:
        return "pass"
    if trial["condition"] == "background" and trial["task"] in ("MB-09", "MB-11"):
        return "driver"
    return "atspi"


def render_tasks(hm: dict, cua: dict, theme_name: str) -> str:
    alt = (
        "Every trial of CuaDriverBench on Omarchy 4.5, task by task: five squares per driver showing "
        "each seed's outcome (passed, driver limitation or AT-SPI/turn-limit failure) alongside median "
        "wall time on a log scale."
    )
    hm_by: dict[tuple[str, str], list[dict]] = {}
    cua_by: dict[tuple[str, str], list[dict]] = {}
    for t in hm["trials"]:
        hm_by.setdefault((t["condition"], t["task"]), []).append(t)
    for t in cua["trials"]:
        cua_by.setdefault((t["condition"], t["task"]), []).append(t)

    # X layout
    x_hm = 278.0
    x_cua = 418.0
    x_t0, x_t1 = 588.0, 848.0
    t_min, t_max = 10.0, 200.0

    def tx(sec: float) -> float:
        clamped = max(t_min, min(t_max, sec))
        return x_t0 + (math.log10(clamped) - math.log10(t_min)) / (math.log10(t_max) - math.log10(t_min)) * (x_t1 - x_t0)

    y_leg = 104
    y_col = 142
    y_grid_top = 164
    row_h = 26
    hdr_h = 30
    # Foreground: header + 7 rows; gap 14; Background: header + 7 rows
    y_fg_hdr = y_grid_top + 18
    y_fg_rows = y_grid_top + hdr_h
    y_bg_hdr = y_fg_rows + 7 * row_h + 24
    y_bg_rows = y_bg_hdr + 14
    y_grid_bot = y_bg_rows + 7 * row_h + 6
    height = y_grid_bot + 26

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">Every trial, task by task</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">Left: one square per seed (r1 to r5), coloured by outcome. Right: median wall time (including tool</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">calls) on a log scale, further left is faster. Hollow dot: Cua Driver passed fewer than 3 of 5.</text>')

    # Legend
    b.append(f'<rect x="24" y="{y_leg - 9}" width="11" height="10" class="pass" rx="2"/>')
    b.append(f'<text x="41" y="{y_leg}" class="t2" font-size="11.5">Passed</text>')
    b.append(f'<rect x="104" y="{y_leg - 9}" width="11" height="10" class="s7" rx="2"/>')
    b.append(f'<text x="121" y="{y_leg}" class="t2" font-size="11.5">Background driver gap (MB-09 drag, MB-11 tooltip)</text>')
    b.append(f'<rect x="436" y="{y_leg - 9}" width="11" height="10" class="s6" rx="2"/>')
    b.append(f'<text x="453" y="{y_leg}" class="t2" font-size="11.5">AT-SPI / turn-limit failure (IR-04, IR-02, MB-10)</text>')

    # Column headers
    b.append(f'<text x="{nfmt(x_hm)}" y="{y_col}" class="t" font-size="11.5" font-weight="600">hyprmasq 0.5.1</text>')
    b.append(f'<rect x="{nfmt(x_hm)}" y="{y_col + 5}" width="104" height="3" class="s0" rx="1.5"/>')
    b.append(f'<text x="{nfmt(x_cua)}" y="{y_col}" class="t" font-size="11.5" font-weight="600">Cua Driver 0.62.1</text>')
    b.append(f'<rect x="{nfmt(x_cua)}" y="{y_col + 5}" width="104" height="3" class="s1" rx="1.5"/>')
    b.append(f'<text x="{nfmt(x_t0)}" y="{y_col}" class="t2" font-size="11.5" font-weight="600">Median wall time (log scale)</text>')

    for tick in (10, 30, 100, 200):
        px = tx(float(tick))
        b.append(f'<text x="{nfmt(px)}" y="{y_col + 17}" class="m" font-size="10.5" text-anchor="middle">{tick} s</text>')
        b.append(f'<line x1="{nfmt(px)}" y1="{y_grid_top}" x2="{nfmt(px)}" y2="{y_grid_bot}" class="grid"/>')

    def draw_group(cond: str, title: str, y_hdr: int, y_start: int, items: list[tuple[str, str, bool]]) -> None:
        hm_g = [t for t in hm["trials"] if t["condition"] == cond]
        cua_g = [t for t in cua["trials"] if t["condition"] == cond]
        hm_p = sum(t["passed"] for t in hm_g)
        cua_p = sum(t["passed"] for t in cua_g)
        b.append(f'<text x="24" y="{y_hdr}" class="t" font-size="12" font-weight="600">{esc(title)}</text>')
        b.append(f'<text x="{nfmt(x_hm)}" y="{y_hdr}" class="t2" font-size="11.5" font-weight="600">{hm_p}/{len(hm_g)}</text>')
        b.append(f'<text x="{nfmt(x_cua)}" y="{y_hdr}" class="t2" font-size="11.5" font-weight="600">{cua_p}/{len(cua_g)}</text>')
        b.append(f'<line x1="24" y1="{y_hdr + 8}" x2="856" y2="{y_hdr + 8}" class="grid"/>')

        cls_map = {"pass": "pass", "driver": "s7", "atspi": "s6"}
        for i, (code, desc, is_ext) in enumerate(items):
            cy = y_start + i * row_h + 13
            if i > 0:
                b.append(f'<line x1="24" y1="{y_start + i * row_h}" x2="564" y2="{y_start + i * row_h}" class="grid"/>')
            suffix = " · ext" if is_ext else ""
            b.append(
                f'<text x="24" y="{cy + 4}" class="t" font-size="12">'
                f'<tspan font-weight="600">{code}</tspan>'
                f'<tspan class="t2">  {esc(desc)}</tspan>'
                f'<tspan class="m" font-size="10.5">{suffix}</tspan>'
                f"</text>"
            )

            ht = sorted(hm_by[(cond, code)], key=lambda r: r["rep"])
            ct = sorted(cua_by[(cond, code)], key=lambda r: r["rep"])

            # 5 squares + fraction for hyprmasq
            for j, tr in enumerate(ht):
                sx = x_hm + j * 13.5
                b.append(f'<rect x="{nfmt(sx)}" y="{nfmt(cy - 5)}" width="10" height="10" class="{cls_map[classify_trial(tr)]}" rx="2"/>')
            hp = sum(t["passed"] for t in ht)
            b.append(f'<text x="{nfmt(x_hm + 74)}" y="{cy + 4}" class="m" font-size="11">{hp}/5</text>')

            # 5 squares + fraction for Cua Driver
            for j, tr in enumerate(ct):
                sx = x_cua + j * 13.5
                b.append(f'<rect x="{nfmt(sx)}" y="{nfmt(cy - 5)}" width="10" height="10" class="{cls_map[classify_trial(tr)]}" rx="2"/>')
            cp = sum(t["passed"] for t in ct)
            b.append(f'<text x="{nfmt(x_cua + 74)}" y="{cy + 4}" class="m" font-size="11">{cp}/5</text>')

            # Time dots on log axis
            hm_sec = med([t["agent_seconds"] for t in ht])
            cua_sec = med([t["agent_seconds"] for t in ct])
            x0, x1 = tx(hm_sec), tx(cua_sec)
            b.append(f'<line x1="{nfmt(min(x0, x1))}" y1="{cy}" x2="{nfmt(max(x0, x1))}" y2="{cy}" class="range"/>')
            b.append(f'<circle cx="{nfmt(x0)}" cy="{cy}" r="4.5" class="dot0"/>')
            cua_dot_cls = "dot1" if cp >= 3 else "ring1"
            b.append(f'<circle cx="{nfmt(x1)}" cy="{cy}" r="4.5" class="{cua_dot_cls}"/>')

    fg_items = [(code, desc, False) for code, desc in TASK_META]
    draw_group("foreground", "Foreground", y_fg_hdr, y_fg_rows, fg_items)
    draw_group("background", "Background · user typing in sentinel", y_bg_hdr, y_bg_rows, BG_ORDER)

    return svg_wrap(height, theme_name, alt, b)


def render_usage(hm: dict, cua: dict, theme_name: str) -> str:
    alt = (
        "Median wall time, tool calls and input tokens per trial for hyprmasq 0.5.1 and "
        "Cua Driver 0.62.1 across foreground, standard background, extended background and all 70 trials."
    )
    suites = [
        ("foreground", "Foreground (35)"),
        ("standard_background", "Standard background (25)"),
        ("extended_background", "Extended background (10)"),
        ("all", "All trials (70)"),
    ]

    # Three columns matching docs/comparison/usage.svg
    cols = [
        ("Wall time", 254.0, 140.0, 80.0, lambda sub: med([t["agent_seconds"] for t in sub]), lambda v: f"{v:.1f} s"),
        ("Tool calls", 464.0, 130.0, 32.0, lambda sub: med([t["tool_calls"] for t in sub]), lambda v: f"{v:.1f}" if v != int(v) else f"{int(v)}"),
        ("Input tokens", 664.0, 136.0, 720.0, lambda sub: med([t["input_tokens"] for t in sub]) / 1000.0, lambda v: f"{v:.0f}k"),
    ]

    y_hdr = 98
    y_top = 112
    block_h = 56
    y_bot = y_top + len(suites) * block_h
    height = y_bot + 38

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">Speed, tool calls and tokens</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">Medians across every trial, passed or not. Wall time runs from prompt start to finish, including all</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">tool calls. Over 70 trials: 12.89M input (1.67M uncached) vs 42.92M (3.55M uncached).</text>')

    b.append(f'<rect x="24" y="{y_hdr - 9}" width="10" height="10" class="s0" rx="2"/>')
    b.append(f'<text x="39" y="{y_hdr}" class="t" font-size="12" font-weight="600">hyprmasq 0.5.1</text>')
    b.append(f'<rect x="138" y="{y_hdr - 9}" width="10" height="10" class="s1" rx="2"/>')
    b.append(f'<text x="153" y="{y_hdr}" class="t" font-size="12" font-weight="600">Cua Driver</text>')

    for title, cx, _, _, _, _ in cols:
        b.append(f'<text x="{nfmt(cx)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">{title}</text>')

    b.append(f'<line x1="24" y1="{y_top}" x2="856" y2="{y_top}" class="grid"/>')

    for idx, (key, label) in enumerate(suites):
        top = y_top + idx * block_h
        if idx > 0:
            b.append(f'<line x1="24" y1="{top}" x2="856" y2="{top}" class="grid"/>')
        b.append(f'<text x="24" y="{top + 32}" class="t" font-size="12.5" font-weight="600">{esc(label)}</text>')

        hm_sub = select_suite(hm["trials"], key)
        cua_sub = select_suite(cua["trials"], key)
        for _, cx, max_w, scale_max, fn, fmt_fn in cols:
            hv = fn(hm_sub)
            cv = fn(cua_sub)
            hw = min(max_w, (hv / scale_max) * max_w)
            cw = min(max_w, (cv / scale_max) * max_w)
            b.append(f'<path d="{rbar(cx, top + 12, hw, 11)}" class="s0"/>')
            b.append(f'<text x="{nfmt(cx + hw + 7)}" y="{top + 21.5}" class="t" font-size="12">{fmt_fn(hv)}</text>')
            b.append(f'<path d="{rbar(cx, top + 31, cw, 11)}" class="s1"/>')
            b.append(f'<text x="{nfmt(cx + cw + 7)}" y="{top + 40.5}" class="t2" font-size="12">{fmt_fn(cv)}</text>')

    b.append(f'<line x1="24" y1="{y_bot}" x2="856" y2="{y_bot}" class="grid"/>')
    b.append(
        f'<text x="24" y="{y_bot + 23}" class="m" font-size="11">'
        f'Paired geometric mean on the 10 common passed cells (Cua Driver / hyprmasq): 1.82× time, 2.08× calls, 3.12× input, 2.19× uncached.'
        f"</text>"
    )
    return svg_wrap(height, theme_name, alt, b)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if any figure on disk is out of date")
    args = parser.parse_args(argv)

    hm = json.loads((DATA_DIR / "hyprmasq-0.5.1.json").read_text(encoding="utf-8"))
    cua = json.loads((DATA_DIR / "cua-driver-0.62.1.json").read_text(encoding="utf-8"))

    figures = {
        "scorecard": render_scorecard,
        "tasks": render_tasks,
        "usage": render_usage,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for name, fn in figures.items():
        for theme in ("light", "dark"):
            suffix = "" if theme == "light" else "-dark"
            path = OUT_DIR / f"{name}{suffix}.svg"
            content = fn(hm, cua, theme)
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current == content:
                continue
            if args.check:
                stale.append(str(path.relative_to(ROOT)))
            else:
                path.write_text(content, encoding="utf-8")
                print(f"wrote {path.relative_to(ROOT)}")

    if stale:
        sys.exit(
            "bench/cua-driver-bench/render.py: out of date, run python3 bench/cua-driver-bench/render.py: "
            + ", ".join(stale)
        )


if __name__ == "__main__":
    main(sys.argv[1:])
