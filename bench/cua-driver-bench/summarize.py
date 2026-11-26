#!/usr/bin/env python3
"""Summarize the Omarchy CuaDriverBench runs in bench/cua-driver-bench/data/."""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
TASKS = ["MB-09", "MB-10", "MB-11", "IR-01", "IR-02", "IR-03", "IR-04"]
SUITES = [
    ("foreground", "Foreground (35)"),
    ("standard_background", "Standard background (25)"),
    ("standard_60", "Standard Omarchy suite (60)"),
    ("extended_background", "Extended background MB-10/11 (10)"),
    ("all", "All trials (70)"),
]


def med(values: list[float | int | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return statistics.median(clean) if clean else None


def fmt_num(val: float | None, digits: int = 1) -> str:
    if val is None:
        return "-"
    return f"{val:.{digits}f}"


def fmt_k(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val / 1000:.0f}k"


def select_suite(trials: list[dict], key: str) -> list[dict]:
    if key == "all":
        return trials
    if key == "standard_60":
        return [
            t for t in trials if t["suite"] in ("foreground", "standard_background")
        ]
    return [t for t in trials if t["suite"] == key]


def load_datasets() -> list[dict]:
    files = sorted(DATA_DIR.glob("*.json"))
    if not files:
        sys.exit(f"No data files found in {DATA_DIR}")
    # Order hyprmasq first when present.
    datasets = [json.loads(p.read_text(encoding="utf-8")) for p in files]
    datasets.sort(key=lambda d: (0 if d["driver"] == "hyprmasq" else 1, d["driver"]))
    return datasets


def summarize(datasets: list[dict]) -> None:
    print("## Suite summary\n")
    print(
        "| Driver | Suite | Passed | Undisturbed | Med agent s | Med calls | Med in | Med uncached | Med out |"
    )
    print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for ds in datasets:
        label = f"{ds['driver']} {ds['driver_version']}"
        trials = ds["trials"]
        for key, title in SUITES:
            sub = select_suite(trials, key)
            passed = sum(1 for t in sub if t["passed"])
            bg = [t for t in sub if t["condition"] == "background"]
            undist = (
                f"{sum(1 for t in bg if t['user_undisturbed'])}/{len(bg)}"
                if bg
                else "-"
            )
            print(
                f"| {label} | {title} | {passed}/{len(sub)} ({100 * passed / len(sub):.1f}%) | "
                f"{undist} | {fmt_num(med([t['agent_seconds'] for t in sub]))} | "
                f"{fmt_num(med([t['tool_calls'] for t in sub]), 1)} | "
                f"{fmt_k(med([t['input_tokens'] for t in sub]))} | "
                f"{fmt_k(med([t['uncached_input_tokens'] for t in sub]))} | "
                f"{fmt_num(med([t['output_tokens'] for t in sub]), 0)} |"
            )

    print("\n## Per-task breakdown\n")
    print(
        "| Condition | Task | "
        + " | ".join(f"{ds['driver']} {ds['driver_version']}" for ds in datasets)
        + " |"
    )
    print("| --- | --- | " + " | ".join("---" for _ in datasets) + " |")
    for cond in ("foreground", "background"):
        for task in TASKS:
            cells = []
            for ds in datasets:
                sub = [
                    t
                    for t in ds["trials"]
                    if t["condition"] == cond and t["task"] == task
                ]
                passed = sum(1 for t in sub if t["passed"])
                t_med = fmt_num(med([t["agent_seconds"] for t in sub]))
                c_med = fmt_num(med([t["tool_calls"] for t in sub]), 0)
                in_med = fmt_k(med([t["input_tokens"] for t in sub]))
                cells.append(f"{passed}/{len(sub)} ({t_med}s, {c_med} calls, {in_med} in)")
            print(f"| {cond} | {task} | " + " | ".join(cells) + " |")

    if len(datasets) == 2 and datasets[0]["driver"] == "hyprmasq":
        hm, other = datasets[0], datasets[1]
        hm_by: dict[tuple[str, str], list[dict]] = {}
        ot_by: dict[tuple[str, str], list[dict]] = {}
        for t in hm["trials"]:
            hm_by.setdefault((t["condition"], t["task"]), []).append(t)
        for t in other["trials"]:
            ot_by.setdefault((t["condition"], t["task"]), []).append(t)
        ratios: dict[str, list[float]] = {
            "agent_seconds": [],
            "tool_calls": [],
            "input_tokens": [],
            "uncached_input_tokens": [],
        }
        for key in sorted(hm_by):
            hp = [t for t in hm_by[key] if t["passed"]]
            op = [t for t in ot_by[key] if t["passed"]]
            if len(hp) >= 3 and len(op) >= 3:
                for metric in ratios:
                    ratios[metric].append(
                        med([t[metric] for t in op]) / med([t[metric] for t in hp])  # type: ignore[operator]
                    )
        gm = lambda xs: math.exp(sum(math.log(x) for x in xs) / len(xs))
        n_cells = len(ratios["agent_seconds"])
        print(
            f"\nPaired geometric mean across {n_cells} common passed cells "
            f"({other['driver']} / {hm['driver']}): "
            f"time {gm(ratios['agent_seconds']):.2f}x, "
            f"calls {gm(ratios['tool_calls']):.2f}x, "
            f"input {gm(ratios['input_tokens']):.2f}x, "
            f"uncached {gm(ratios['uncached_input_tokens']):.2f}x"
        )


if __name__ == "__main__":
    summarize(load_datasets())
