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

# Scale AI published Hugging Face dataset (earakely-scale/CuaDriverBench-AgentEnv,
# commit ca3d569ef93c1987f75bf00663e3114c0ed601ac, results/trials.parquet filtered to
# on_board=True, model=anthropic/claude-sonnet-5-5, and the 60 shared BenchLab trials).
# Wall times use agent_window_s uniformly across all five HF board rows.
# Costs use Scale AI export.py's cost_usd column ($0.20/M cache read, $2.50/M cache write).
HF_DATASET_SHA = "ca3d569ef93c1987f75bf00663e3114c0ed601ac"
HF_BOARD_60 = [
    ("macOS · Aside 1.26.1111", "HF board #1 · EC2", "s6", False, 60, 25, 25, 15.622, 0.057950),
    ("macOS · Cua Driver 0.47.10", "HF board · EC2", "s1", False, 52, 17, 22, 20.032, 0.134250),
    ("Omarchy · Cua Driver 0.47.4", "HF board · EC2", "s1", False, 52, 18, 22, 30.187, 0.245150),
    ("Windows · Cua Driver 0.38.0", "HF board · EC2", "s1", False, 44, 11, 11, 21.798, 0.284550),
    ("Ubuntu · Cua Driver 0.38.0", "HF board · EC2", "s1", False, 40, 5, 9, 27.260, 0.243250),
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
    if key == "all_bg":
        return [t for t in trials if t["condition"] == "background"]
    return [t for t in trials if t["suite"] == key]


def render_scorecard(data: dict[str, dict], theme_name: str) -> str:
    alt = (
        "CuaDriverBench on Omarchy 4.5 across 280 trials (Claude Sonnet 5.5 and GPT-6.2 Sol): "
        "standard 60-trial Omarchy suite pass rate with descriptive 95 % Wilson interval, "
        "extended MB-10/11 background pass bar, and standard-suite median wall time, tool calls "
        "and API list-price cost as multiples of hyprmasq 0.5.1."
    )
    x_rate0, x_rate1 = 286.0, 428.0  # 40% to 100%
    x_pct = 442.0
    x_cnt = 482.0
    x_ext_bar = 542.0
    ext_bar_w = 82.0
    x_ext_txt = 632.0
    x_time = 716.0
    x_calls = 774.0
    x_cost = 834.0

    def rx(p: float) -> float:
        clamped = max(0.4, min(1.0, p))
        return x_rate0 + (clamped - 0.4) / 0.6 * (x_rate1 - x_rate0)

    y_hdr = 108
    y_grid_top = 136
    block_h = 66
    y_grid_bot = y_grid_top + 2 * block_h
    height = y_grid_bot + 42

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">CuaDriverBench on Omarchy 4.5 · 240 standard + 40 extended trials</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">Standard 60-trial Omarchy suite (35 foreground, 25 background) plus 10 author-added MB-10/11 background trials.</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">Dot is pass rate with descriptive 95 % Wilson interval. Multiples compare standard-60 medians at provider list prices.</text>')

    b.append(f'<text x="{nfmt(x_rate0)}" y="{y_hdr}" class="t2" font-size="12">Standard 60 passed</text>')
    for tick in (40, 70, 100):
        tx = rx(tick / 100.0)
        b.append(f'<text x="{nfmt(tx)}" y="{y_hdr + 16}" class="m" font-size="10.5" text-anchor="middle">{tick} %</text>')
        b.append(f'<line x1="{nfmt(tx)}" y1="{y_grid_top}" x2="{nfmt(tx)}" y2="{y_grid_bot}" class="grid"/>')

    b.append(f'<text x="{nfmt(x_ext_bar)}" y="{y_hdr}" class="t2" font-size="12">Extended bg</text>')
    b.append(f'<text x="{nfmt(x_ext_bar)}" y="{y_hdr + 16}" class="m" font-size="10.5">MB-10/11 (of 10)</text>')

    b.append(f'<text x="{nfmt((x_time + x_cost) / 2)}" y="{y_hdr}" class="t2" font-size="12" text-anchor="middle">vs hyprmasq (std 60)</text>')
    b.append(f'<text x="{nfmt(x_time)}" y="{y_hdr + 16}" class="t2" font-size="11.5" text-anchor="middle">Wall s</text>')
    b.append(f'<text x="{nfmt(x_calls)}" y="{y_hdr + 16}" class="t2" font-size="11.5" text-anchor="middle">Calls</text>')
    b.append(f'<text x="{nfmt(x_cost)}" y="{y_hdr + 16}" class="t2" font-size="11.5" text-anchor="middle">Cost</text>')

    b.append(f'<line x1="24" y1="{y_grid_top}" x2="856" y2="{y_grid_top}" class="grid"/>')

    drivers = [
        ("hyprmasq 0.5.1", "tools = driver (6 tools)", "s0", "dot0", "hm_sonnet", "hm_sol"),
        ("Cua Driver 0.62.1", "driver (Sonnet) · stock (Sol)", "s1", "dot1", "cua_sonnet", "cua_sol"),
    ]

    for idx, (dlabel, dsub, bar_cls, dot_cls, k_son, k_sol) in enumerate(drivers):
        top = y_grid_top + idx * block_h
        if idx > 0:
            b.append(f'<line x1="24" y1="{top}" x2="856" y2="{top}" class="grid"/>')
        b.append(f'<rect x="24" y="{top + 10}" width="4" height="46" class="{bar_cls}" rx="2"/>')
        b.append(f'<text x="36" y="{top + 28}" class="t" font-size="13.5" font-weight="600">{dlabel}</text>')
        b.append(f'<text x="36" y="{top + 44}" class="m" font-size="10.2">{esc(dsub)}</text>')

        model_rows = [
            ("Sonnet 5.5", data[k_son], data["hm_sonnet"], top + 20),
            ("GPT-6.2 Sol", data[k_sol], data["hm_sol"], top + 46),
        ]
        for mlabel, ds, hm_ref, cy in model_rows:
            std = select_suite(ds["trials"], "standard_60")
            ext = select_suite(ds["trials"], "extended_background")
            n = len(std)
            k = sum(t["passed"] for t in std)
            p = k / n
            lo, hi = wilson(k, n)
            ext_p = sum(t["passed"] for t in ext)

            b.append(f'<text x="198" y="{cy + 4}" class="t2" font-size="12">{mlabel}</text>')
            b.append(f'<line x1="{nfmt(rx(lo))}" y1="{cy}" x2="{nfmt(rx(hi))}" y2="{cy}" class="whisker"/>')
            b.append(f'<circle cx="{nfmt(rx(p))}" cy="{cy}" r="4.5" class="{dot_cls}"/>')
            b.append(f'<text x="{nfmt(x_pct)}" y="{cy + 4}" class="t" font-size="12.5" font-weight="600">{round(100 * p)} %</text>')
            b.append(f'<text x="{nfmt(x_cnt)}" y="{cy + 4}" class="m" font-size="11">{k}/{n}</text>')

            b.append(f'<rect x="{nfmt(x_ext_bar)}" y="{cy - 4}" width="{nfmt(ext_bar_w)}" height="8" class="out" rx="2"/>')
            b.append(f'<rect x="{nfmt(x_ext_bar)}" y="{cy - 4}" width="{nfmt(ext_bar_w * ext_p / len(ext))}" height="8" class="pass" rx="2"/>')
            b.append(f'<text x="{nfmt(x_ext_txt)}" y="{cy + 4}" class="t" font-size="12">{ext_p}/{len(ext)}</text>')

            if ds["driver"] == "hyprmasq":
                b.append(f'<text x="{nfmt(x_time)}" y="{cy + 4}" class="m" font-size="12.5" text-anchor="middle">1×</text>')
                b.append(f'<text x="{nfmt(x_calls)}" y="{cy + 4}" class="m" font-size="12.5" text-anchor="middle">1×</text>')
                b.append(f'<text x="{nfmt(x_cost)}" y="{cy + 4}" class="m" font-size="12.5" text-anchor="middle">1×</text>')
            else:
                hm_std = select_suite(hm_ref["trials"], "standard_60")
                wt = med([t["wall_seconds"] for t in std]) / med([t["wall_seconds"] for t in hm_std])
                ct = med([t["tool_calls"] for t in std]) / med([t["tool_calls"] for t in hm_std])
                dt = med([t["cost_usd"] for t in std]) / med([t["cost_usd"] for t in hm_std])
                b.append(f'<text x="{nfmt(x_time)}" y="{cy + 4}" class="t" font-size="12.5" text-anchor="middle">{wt:.1f}×</text>')
                b.append(f'<text x="{nfmt(x_calls)}" y="{cy + 4}" class="t" font-size="12.5" text-anchor="middle">{ct:.1f}×</text>')
                b.append(f'<text x="{nfmt(x_cost)}" y="{cy + 4}" class="t" font-size="12.5" text-anchor="middle">{dt:.1f}×</text>')

    b.append(f'<line x1="24" y1="{y_grid_bot}" x2="856" y2="{y_grid_bot}" class="grid"/>')
    hm_s_all = sum(t["passed"] for t in data["hm_sonnet"]["trials"])
    hm_g_all = sum(t["passed"] for t in data["hm_sol"]["trials"])
    cua_s_all = sum(t["passed"] for t in data["cua_sonnet"]["trials"])
    cua_g_all = sum(t["passed"] for t in data["cua_sol"]["trials"])
    hm_a22 = sum(
        1
        for k in ("hm_sonnet", "hm_sol")
        for t in data[k]["trials"]
        if t["condition"] == "background" and t["user_undisturbed"]
    )
    cua_a22 = sum(
        1
        for k in ("cua_sonnet", "cua_sol")
        for t in data[k]["trials"]
        if t["condition"] == "background" and t["user_undisturbed"]
    )
    b.append(
        f'<text x="24" y="{y_grid_bot + 24}" class="m" font-size="10.8">'
        f'All 70 trials: hyprmasq {hm_s_all}/70 and {hm_g_all}/70 vs Cua Driver {cua_s_all}/70 and {cua_g_all}/70. '
        f'A22 undisturbed (bg): hyprmasq {hm_a22}/70, Cua Driver {cua_a22}/70.'
        f"</text>"
    )
    return svg_wrap(height, theme_name, alt, b)


def classify_trial(trial: dict) -> str:
    if trial["passed"]:
        return "pass"
    if trial["condition"] == "background" and (
        trial["task"] in ("MB-09", "MB-11") or trial["user_undisturbed"] is False
    ):
        return "driver"
    return "other"


def render_tasks(data: dict[str, dict], theme_name: str) -> str:
    alt = (
        "Every trial of CuaDriverBench on Omarchy 4.5 across Claude Sonnet 5.5 and GPT-6.2 Sol, task by task: "
        "ten squares per driver (five Sonnet, five Sol) coloured by outcome, beside Claude Sonnet 5.5 median "
        "wall time on a log scale."
    )
    def index_by(ds: dict) -> dict[tuple[str, str], list[dict]]:
        out: dict[tuple[str, str], list[dict]] = {}
        for t in ds["trials"]:
            out.setdefault((t["condition"], t["task"]), []).append(t)
        return out

    hm_son = index_by(data["hm_sonnet"])
    hm_sol = index_by(data["hm_sol"])
    cua_son = index_by(data["cua_sonnet"])
    cua_sol = index_by(data["cua_sol"])

    x_hm = 258.0
    x_cua = 414.0
    x_t0, x_t1 = 592.0, 848.0
    t_min, t_max = 8.0, 120.0

    def tx(sec: float) -> float:
        clamped = max(t_min, min(t_max, sec))
        return x_t0 + (math.log10(clamped) - math.log10(t_min)) / (math.log10(t_max) - math.log10(t_min)) * (x_t1 - x_t0)

    y_leg = 104
    y_col = 140
    y_grid_top = 174
    row_h = 26
    y_fg_hdr = y_grid_top + 16
    y_fg_rows = y_grid_top + 28
    y_bg_hdr = y_fg_rows + 7 * row_h + 24
    y_bg_rows = y_bg_hdr + 14
    y_grid_bot = y_bg_rows + 7 * row_h + 6
    height = y_grid_bot + 26

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">Every trial, task by task (280 trials)</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">Left: ten squares per driver (5 seeds Sonnet 5.5, 5 seeds GPT-6.2 Sol). Right: Claude Sonnet 5.5 median</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">wall time (including tool calls, log scale, further left is faster). Hollow dot: fewer than 3 of 5 passed.</text>')

    b.append(f'<rect x="24" y="{y_leg - 9}" width="11" height="10" class="pass" rx="2"/>')
    b.append(f'<text x="41" y="{y_leg}" class="t2" font-size="11.5">Passed</text>')
    b.append(f'<rect x="104" y="{y_leg - 9}" width="11" height="10" class="s7" rx="2"/>')
    b.append(f'<text x="121" y="{y_leg}" class="t2" font-size="11.5">Background driver gap / A22 disturbance</text>')
    b.append(f'<rect x="378" y="{y_leg - 9}" width="11" height="10" class="s6" rx="2"/>')
    b.append(f'<text x="395" y="{y_leg}" class="t2" font-size="11.5">Other failure (drag lock, AT-SPI, double action)</text>')

    sq = 8.0
    step = 9.5
    gap = 7.0
    bar_w = 5 * step - 1.5 + gap + 5 * step - 1.5
    for x0, title, cls in [(x_hm, "hyprmasq 0.5.1", "s0"), (x_cua, "Cua Driver 0.62.1", "s1")]:
        b.append(f'<text x="{nfmt(x0)}" y="{y_col}" class="t" font-size="11.5" font-weight="600">{title}</text>')
        b.append(f'<rect x="{nfmt(x0)}" y="{y_col + 5}" width="{nfmt(bar_w)}" height="3" class="{cls}" rx="1.5"/>')
        b.append(f'<text x="{nfmt(x0)}" y="{y_col + 21}" class="m" font-size="9.5">Sonnet</text>')
        b.append(f'<text x="{nfmt(x0 + 5 * step + gap)}" y="{y_col + 21}" class="m" font-size="9.5">Sol</text>')

    b.append(f'<text x="{nfmt(x_t0)}" y="{y_col}" class="t2" font-size="11.5" font-weight="600">Median wall time · Sonnet 5.5</text>')
    for tick in (10, 20, 40, 80, 120):
        px = tx(float(tick))
        b.append(f'<text x="{nfmt(px)}" y="{y_col + 21}" class="m" font-size="10.5" text-anchor="middle">{tick} s</text>')
        b.append(f'<line x1="{nfmt(px)}" y1="{y_grid_top}" x2="{nfmt(px)}" y2="{y_grid_bot}" class="grid"/>')

    cls_map = {"pass": "pass", "driver": "s7", "other": "s6"}

    def draw_group(cond: str, title: str, y_hdr: int, y_start: int, items: list[tuple[str, str, bool]]) -> None:
        hm_p = sum(t["passed"] for t in data["hm_sonnet"]["trials"] + data["hm_sol"]["trials"] if t["condition"] == cond)
        cua_p = sum(t["passed"] for t in data["cua_sonnet"]["trials"] + data["cua_sol"]["trials"] if t["condition"] == cond)
        b.append(f'<text x="24" y="{y_hdr}" class="t" font-size="12" font-weight="600">{esc(title)}</text>')
        b.append(f'<text x="{nfmt(x_hm)}" y="{y_hdr}" class="t2" font-size="11.5" font-weight="600">{hm_p}/70</text>')
        b.append(f'<text x="{nfmt(x_cua)}" y="{y_hdr}" class="t2" font-size="11.5" font-weight="600">{cua_p}/70</text>')
        b.append(f'<line x1="24" y1="{y_hdr + 8}" x2="856" y2="{y_hdr + 8}" class="grid"/>')

        for i, (code, desc, is_ext) in enumerate(items):
            cy = y_start + i * row_h + 13
            if i > 0:
                b.append(f'<line x1="24" y1="{y_start + i * row_h}" x2="570" y2="{y_start + i * row_h}" class="grid"/>')
            ext_span = '<tspan dx="5" class="m" font-size="10.5">· ext</tspan>' if is_ext else ""
            b.append(
                f'<text x="24" y="{cy + 4}" class="t" font-size="12">'
                f'<tspan font-weight="600">{code}</tspan>'
                f'<tspan dx="6" class="t2">{esc(desc)}</tspan>'
                f"{ext_span}"
                f"</text>"
            )

            for x_base, d_son, d_sol in [(x_hm, hm_son, hm_sol), (x_cua, cua_son, cua_sol)]:
                s1 = sorted(d_son[(cond, code)], key=lambda r: r["rep"])
                s2 = sorted(d_sol[(cond, code)], key=lambda r: r["rep"])
                for j, tr in enumerate(s1):
                    sx = x_base + j * step
                    b.append(f'<rect x="{nfmt(sx)}" y="{nfmt(cy - sq / 2)}" width="{nfmt(sq)}" height="{nfmt(sq)}" class="{cls_map[classify_trial(tr)]}" rx="1.5"/>')
                for j, tr in enumerate(s2):
                    sx = x_base + 5 * step + gap + j * step
                    b.append(f'<rect x="{nfmt(sx)}" y="{nfmt(cy - sq / 2)}" width="{nfmt(sq)}" height="{nfmt(sq)}" class="{cls_map[classify_trial(tr)]}" rx="1.5"/>')
                tot_p = sum(t["passed"] for t in s1 + s2)
                b.append(f'<text x="{nfmt(x_base + bar_w + 7)}" y="{cy + 4}" class="m" font-size="10.5">{tot_p}/10</text>')

            hs = hm_son[(cond, code)]
            cs = cua_son[(cond, code)]
            hm_sec = med([t["wall_seconds"] for t in hs])
            cua_sec = med([t["wall_seconds"] for t in cs])
            x0, x1 = tx(hm_sec), tx(cua_sec)
            cp = sum(t["passed"] for t in cs)
            cua_cls = "dot1" if cp >= 3 else "ring1"
            if abs(x1 - x0) < 10.5:
                b.append(f'<circle cx="{nfmt(x0)}" cy="{nfmt(cy - 4.8)}" r="4.3" class="dot0"/>')
                b.append(f'<circle cx="{nfmt(x1)}" cy="{nfmt(cy + 4.8)}" r="4.3" class="{cua_cls}"/>')
            else:
                b.append(f'<line x1="{nfmt(min(x0, x1))}" y1="{cy}" x2="{nfmt(max(x0, x1))}" y2="{cy}" class="range"/>')
                b.append(f'<circle cx="{nfmt(x0)}" cy="{cy}" r="4.5" class="dot0"/>')
                b.append(f'<circle cx="{nfmt(x1)}" cy="{cy}" r="4.5" class="{cua_cls}"/>')

    draw_group("foreground", "Foreground", y_fg_hdr, y_fg_rows, [(c, d, False) for c, d in TASK_META])
    draw_group("background", "Background · user typing in sentinel", y_bg_hdr, y_bg_rows, BG_ORDER)
    return svg_wrap(height, theme_name, alt, b)


def render_usage(data: dict[str, dict], theme_name: str) -> str:
    alt = (
        "Median wall time, tool calls, input tokens and API list-price cost per trial across all 70 trials "
        "for hyprmasq 0.5.1 and Cua Driver 0.62.1 with Claude Sonnet 5.5 and GPT-6.2 Sol."
    )
    cols = [
        ("Wall time", 268.0, 96.0, 75.0, lambda tr: med([t["wall_seconds"] for t in tr]), lambda v: f"{v:.1f} s"),
        ("Tool calls", 424.0, 92.0, 22.0, lambda tr: med([t["tool_calls"] for t in tr]), lambda v: f"{v:.1f}" if v != int(v) else f"{int(v)}"),
        ("Input tokens", 570.0, 96.0, 1100.0, lambda tr: med([t["input_tokens"] for t in tr]) / 1000.0, lambda v: f"{v:.0f}k"),
        ("Cost per trial", 726.0, 86.0, 0.22, lambda tr: med([t["cost_usd"] for t in tr]), lambda v: f"${v:.3f}"),
    ]

    y_hdr = 98
    y_top = 112
    block_h = 64
    y_bot = y_top + 2 * block_h
    height = y_bot + 40

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">Wall time, tool calls, tokens and cost</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">Medians across all 70 trials (passed or not). Wall time runs from prompt start to finish, including all</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">tool calls. Costs use provider API list prices ($0.10/M cache reads for both Sonnet 5.5 and GPT-6.2 Sol).</text>')

    for title, cx, _, _, _, _ in cols:
        b.append(f'<text x="{nfmt(cx)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">{title}</text>')

    b.append(f'<line x1="24" y1="{y_top}" x2="856" y2="{y_top}" class="grid"/>')

    drivers = [
        ("hyprmasq 0.5.1", "s0", "hm_sonnet", "hm_sol"),
        ("Cua Driver 0.62.1", "s1", "cua_sonnet", "cua_sol"),
    ]
    for idx, (dlabel, cls, k_son, k_sol) in enumerate(drivers):
        top = y_top + idx * block_h
        if idx > 0:
            b.append(f'<line x1="24" y1="{top}" x2="856" y2="{top}" class="grid"/>')
        b.append(f'<rect x="24" y="{top + 10}" width="4" height="44" class="{cls}" rx="2"/>')
        b.append(f'<text x="36" y="{top + 36}" class="t" font-size="13.5" font-weight="600">{dlabel}</text>')

        for mlabel, key, ry in [("Sonnet 5.5", k_son, top + 14), ("GPT-6.2 Sol", k_sol, top + 39)]:
            tr = data[key]["trials"]
            b.append(f'<text x="178" y="{ry + 9.5}" class="t2" font-size="12">{mlabel}</text>')
            for _, cx, max_w, scale_max, fn, fmt_fn in cols:
                val = fn(tr)
                w = min(max_w, (val / scale_max) * max_w)
                b.append(f'<path d="{rbar(cx, ry, w, 10)}" class="{cls}"/>')
                b.append(f'<text x="{nfmt(cx + w + 6)}" y="{ry + 9.5}" class="t" font-size="11.5">{fmt_fn(val)}</text>')

    b.append(f'<line x1="24" y1="{y_bot}" x2="856" y2="{y_bot}" class="grid"/>')
    hm_s_tot = sum(t["cost_usd"] for t in data["hm_sonnet"]["trials"])
    cua_s_tot = sum(t["cost_usd"] for t in data["cua_sonnet"]["trials"])
    hm_g_tot = sum(t["cost_usd"] for t in data["hm_sol"]["trials"])
    cua_g_tot = sum(t["cost_usd"] for t in data["cua_sol"]["trials"])
    b.append(
        f'<text x="24" y="{y_bot + 24}" class="m" font-size="11">'
        f'Total 70-trial cost: Sonnet 5.5 ${hm_s_tot:.2f} vs ${cua_s_tot:.2f} ({cua_s_tot / hm_s_tot:.1f}×) · '
        f'GPT-6.2 Sol ${hm_g_tot:.2f} vs ${cua_g_tot:.2f} ({cua_g_tot / hm_g_tot:.1f}×).'
        f"</text>"
    )
    return svg_wrap(height, theme_name, alt, b)


def render_cross_os(data: dict[str, dict], theme_name: str) -> str:
    alt = (
        "Claude Sonnet 5.5 on the same 60 BenchLab trials across operating systems: "
        "local Omarchy runs for hyprmasq 0.5.1 and Cua Driver 0.62.1 beside Scale AI's "
        "published Hugging Face board for macOS, Omarchy, Windows and Ubuntu, comparing "
        "total passed, background passed, A22 user-undisturbed, median wall time and Scale export.py median cost."
    )

    def local_row(label: str, cls: str, key: str) -> tuple:
        std = select_suite(data[key]["trials"], "standard_60")
        bg = [t for t in std if t["condition"] == "background"]
        passed = sum(t["passed"] for t in std)
        bg_p = sum(t["passed"] for t in bg)
        undist = sum(1 for t in bg if t["user_undisturbed"])
        wall = med([t["wall_seconds"] for t in std])
        cost_key = "cost_usd_scale_export" if "cost_usd_scale_export" in std[0] else "cost_usd"
        cost = med([t[cost_key] for t in std])
        return (label, "this run · KVM", cls, True, passed, bg_p, undist, wall, cost)

    rows = [
        *HF_BOARD_60,
        local_row("Omarchy · hyprmasq 0.5.1", "s0", "hm_sonnet"),
        local_row("Omarchy · Cua Driver 0.62.1", "s1", "cua_sonnet"),
    ]
    rows.sort(key=lambda r: (-r[4], -r[6], r[7]))

    x_pass, w_pass = 248.0, 68.0
    x_bg, w_bg = 378.0, 58.0
    x_a22, w_a22 = 496.0, 54.0
    x_wall, w_wall, max_wall = 612.0, 62.0, 34.0
    x_cost, w_cost, max_cost = 738.0, 64.0, 0.31

    y_hdr = 98
    y_sub = 113
    y_top = 124
    row_h = 42
    y_bot = y_top + len(rows) * row_h
    height = y_bot + 40

    b: list[str] = []
    b.append('<text x="24" y="34" class="t" font-size="15" font-weight="600">Across operating systems · Claude Sonnet 5.5 (same 60 BenchLab trials)</text>')
    b.append('<text x="24" y="53" class="t2" font-size="12.5">Local Omarchy runs beside Scale AI&#x27;s published Hugging Face board (35 foreground + 25 standard background).</text>')
    b.append('<text x="24" y="72" class="t2" font-size="12.5">Longer bars are better on the left three columns, while shorter bars are faster and cheaper on the right two.</text>')

    b.append(f'<text x="{nfmt(x_pass)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">Passed</text>')
    b.append(f'<text x="{nfmt(x_pass)}" y="{y_sub}" class="m" font-size="10.2">of 60</text>')
    b.append(f'<text x="{nfmt(x_bg)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">Background</text>')
    b.append(f'<text x="{nfmt(x_bg)}" y="{y_sub}" class="m" font-size="10.2">of 25</text>')
    b.append(f'<text x="{nfmt(x_a22)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">Undisturbed</text>')
    b.append(f'<text x="{nfmt(x_a22)}" y="{y_sub}" class="m" font-size="10.2">A22 of 25</text>')
    b.append(f'<text x="{nfmt(x_wall)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">Median wall</text>')
    b.append(f'<text x="{nfmt(x_wall)}" y="{y_sub}" class="m" font-size="10.2">s (approx)</text>')
    b.append(f'<text x="{nfmt(x_cost)}" y="{y_hdr}" class="t2" font-size="12" font-weight="600">Median cost</text>')
    b.append(f'<text x="{nfmt(x_cost)}" y="{y_sub}" class="m" font-size="10.2">export.py</text>')

    b.append(f'<line x1="24" y1="{y_top}" x2="856" y2="{y_top}" class="grid"/>')

    for i, (label, sub, cls, is_local, passed, bg_p, undist, wall, cost) in enumerate(rows):
        top = y_top + i * row_h
        cy = top + row_h / 2.0
        if i > 0:
            b.append(f'<line x1="24" y1="{top}" x2="856" y2="{top}" class="grid"/>')
        b.append(f'<rect x="24" y="{top + 7}" width="4" height="28" class="{cls}" rx="2"/>')
        weight = ' font-weight="600"' if is_local else ""
        b.append(f'<text x="36" y="{top + 19}" class="t" font-size="12"{weight}>{esc(label)}</text>')
        b.append(f'<text x="36" y="{top + 33}" class="m" font-size="10">{esc(sub)}</text>')

        b.append(f'<rect x="{nfmt(x_pass)}" y="{nfmt(cy - 4.5)}" width="{nfmt(w_pass)}" height="9" class="out" rx="2"/>')
        b.append(f'<path d="{rbar(x_pass, cy - 4.5, w_pass * passed / 60.0, 9)}" class="{cls}"/>')
        b.append(f'<text x="{nfmt(x_pass + w_pass + 6)}" y="{nfmt(cy + 4)}" class="t" font-size="11.5"{weight}>{passed}/60</text>')

        b.append(f'<rect x="{nfmt(x_bg)}" y="{nfmt(cy - 4.5)}" width="{nfmt(w_bg)}" height="9" class="out" rx="2"/>')
        b.append(f'<path d="{rbar(x_bg, cy - 4.5, w_bg * bg_p / 25.0, 9)}" class="pass"/>')
        b.append(f'<text x="{nfmt(x_bg + w_bg + 6)}" y="{nfmt(cy + 4)}" class="t" font-size="11.5"{weight}>{bg_p}/25</text>')

        b.append(f'<rect x="{nfmt(x_a22)}" y="{nfmt(cy - 4.5)}" width="{nfmt(w_a22)}" height="9" class="out" rx="2"/>')
        b.append(f'<path d="{rbar(x_a22, cy - 4.5, w_a22 * undist / 25.0, 9)}" class="pass"/>')
        b.append(f'<text x="{nfmt(x_a22 + w_a22 + 6)}" y="{nfmt(cy + 4)}" class="t" font-size="11.5"{weight}>{undist}/25</text>')

        ww = min(w_wall, (wall / max_wall) * w_wall)
        b.append(f'<path d="{rbar(x_wall, cy - 4.5, ww, 9)}" class="{cls}"/>')
        b.append(f'<text x="{nfmt(x_wall + ww + 6)}" y="{nfmt(cy + 4)}" class="t" font-size="11.5">{wall:.1f} s</text>')

        cw = min(w_cost, (cost / max_cost) * w_cost)
        b.append(f'<path d="{rbar(x_cost, cy - 4.5, cw, 9)}" class="{cls}"/>')
        b.append(f'<text x="{nfmt(x_cost + cw + 6)}" y="{nfmt(cy + 4)}" class="t" font-size="11.5">${cost:.3f}</text>')

    b.append(f'<line x1="24" y1="{y_bot}" x2="856" y2="{y_bot}" class="grid"/>')
    b.append(
        f'<text x="24" y="{y_bot + 24}" class="m" font-size="10.4">'
        f'HF rows (parquet {HF_DATASET_SHA[:8]}) use agent_window_s on EC2. Local rows use prompt_agent wall time on KVM. '
        f'Costs use Scale export.py ($0.20/M read).'
        f"</text>"
    )
    return svg_wrap(height, theme_name, alt, b)


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if any figure on disk is out of date")
    args = parser.parse_args(argv)

    data = {
        "hm_sonnet": json.loads((DATA_DIR / "hyprmasq-0.5.1-claude-sonnet-5-5.json").read_text(encoding="utf-8")),
        "cua_sonnet": json.loads((DATA_DIR / "cua-driver-0.62.1-claude-sonnet-5-5.json").read_text(encoding="utf-8")),
        "hm_sol": json.loads((DATA_DIR / "hyprmasq-0.5.1-gpt-6.2-sol.json").read_text(encoding="utf-8")),
        "cua_sol": json.loads((DATA_DIR / "cua-driver-0.62.1-gpt-6.2-sol.json").read_text(encoding="utf-8")),
    }

    figures = {
        "scorecard": render_scorecard,
        "tasks": render_tasks,
        "usage": render_usage,
        "cross-os": render_cross_os,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for name, fn in figures.items():
        for theme in ("light", "dark"):
            suffix = "" if theme == "light" else "-dark"
            path = OUT_DIR / f"{name}{suffix}.svg"
            content = fn(data, theme)
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
