"""Tests that render.py fills a page's blocks from a dataset and that --check catches a page or figure out of step
with it: python3 render_test.py"""

import contextlib
import html
import io
import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render  # noqa: E402


def run(model, rep, passed, wall, tools):
    return {"run": f"r/{model}-{rep}", "task": "board", "harness": "codex", "model": model, "effort": "medium",
            "ran_at": "medium", "rep": rep, "date": "2026-10-19", "harness_version": "x", "passed": passed,
            "wall_s": wall, "tools_s": tools, "turn_s": 3.0, "calls": 5, "actions": 9, "images": 7, "errors": 0,
            "input_tokens": 100000, "cache_read_tokens": 90000, "output_tokens": 1000, "cost_usd": 0.05,
            "user_undisturbed": True, "phases": {}, "act_s": {"input": 4, "wait": 2, "watch": 1, "rest": 3}}


PAGE = """# Results

Handwritten text stays.

<!-- render set scoreboard -->
stale content
<!-- /render -->

<!-- render set table board -->
<!-- /render -->
"""


def main(*args):
    with contextlib.redirect_stdout(io.StringIO()):
        render.main(list(args))


class Page(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.makedirs(os.path.join(self.dir.name, "benchmarks", "data"))
        runs = [run("gpt-6.1-sol", 1, True, 50, 12), run("gpt-6.1-sol", 2, False, 60, 13),
                run("claude-opus-5-5", 1, True, 40, 11)]
        with open(os.path.join(self.dir.name, "benchmarks", "data", "set.json"), "w") as f:
            json.dump({"sources": [], "runs": runs}, f)
        self.page = os.path.join(self.dir.name, "benchmarks.md")
        with open(self.page, "w") as f:
            f.write(PAGE)

    def tearDown(self):
        self.dir.cleanup()

    def read(self, *path):
        with open(os.path.join(self.dir.name, *path)) as f:
            return f.read()

    def check_fails(self):
        with self.assertRaises(SystemExit) as cm:
            main("--check", self.page)
        self.assertIn("out of date", str(cm.exception))

    def test_blocks_are_filled_and_check_follows_the_files(self):
        self.check_fails()
        main(self.page)
        page = self.read("benchmarks.md")
        self.assertTrue(page.startswith("# Results\n\nHandwritten text stays.\n\n<!-- render set scoreboard -->\n"))
        self.assertNotIn("stale content", page)
        self.assertIn('srcset="benchmarks/set-scoreboard-dark.svg"', page)
        self.assertIn('src="benchmarks/set-scoreboard.svg"', page)
        self.assertIn("| gpt-6.1-sol | medium | 1/2 | 55 (50–60) |", page)
        self.assertIn("<svg", self.read("benchmarks", "set-scoreboard-dark.svg"))
        main("--check", self.page)

        with open(os.path.join(self.dir.name, "benchmarks", "set-scoreboard.svg"), "a") as f:
            f.write("edited")
        self.check_fails()
        main(self.page)
        main("--check", self.page)

        with open(os.path.join(self.dir.name, "benchmarks", "set-time.svg"), "w") as f:
            f.write("<svg/>")
        self.check_fails()
        main(self.page)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "benchmarks", "set-time.svg")))
        main("--check", self.page)

    def test_a_task_of_several_variants_names_its_variants_alone(self):
        runs = [dict(run("gpt-6.1-sol", 1, True, 50, 12), variant="dnd"),
                dict(run("gpt-6.1-sol", 1, True, 60, 13), variant="pointer"),
                dict(run("gpt-6.1-sol", 1, True, 70, 14), task="calc")]
        with open(os.path.join(self.dir.name, "benchmarks", "data", "set.json"), "w") as f:
            json.dump({"sources": [], "runs": runs}, f)
        with open(self.page, "w") as f:
            f.write("<!-- render set scoreboard board -->\n<!-- /render -->\n")
        main(self.page)
        svg = self.read("benchmarks", "set-scoreboard-board.svg")
        self.assertIn("2 of 2 runs passed", svg)
        self.assertNotIn("Calc", svg)

    def test_compare_puts_each_task_before_and_after(self):
        before = [run("gpt-6.1-sol", 1, False, 80, 20), run("gpt-6.1-sol", 2, True, 100, 30)]
        with open(os.path.join(self.dir.name, "benchmarks", "data", "old.json"), "w") as f:
            json.dump({"sources": [], "runs": before}, f)
        with open(self.page, "w") as f:
            f.write("<!-- render set compare-table old -->\n<!-- /render -->\n\n"
                    "<!-- render set compare old -->\n<!-- /render -->\n")
        main(self.page)
        self.assertIn("| Board | 1/2 → 2/3 | 90 → 50 | 25 → 12 | 3.0 → 3.0 | 5 → 5 | 9 → 9 | 7 → 7 |",
                      self.read("benchmarks.md"))
        # The figure has a bar for each build, with its median and its runs passed.
        svg = self.read("benchmarks", "set-compare-old.svg")
        for text in (">before</text>", ">90 s</text>", ">1/2</text>", ">after</text>", ">50 s</text>", ">2/3</text>"):
            self.assertIn(text, svg)
        main("--check", self.page)


class Load(unittest.TestCase):
    def test_legacy_variant_is_inferred_only_from_unambiguous_task_sources(self):
        with tempfile.TemporaryDirectory() as d:
            for name, variants in (("one", ["dnd"]), ("mixed", ["dnd", "pointer"])):
                with open(os.path.join(d, name + ".json"), "w") as f:
                    json.dump({"sources": [{"task": "board", "variant": v} for v in variants],
                               "runs": [run("gpt-6.1-sol", 1, True, 50, 12)]}, f)
            self.assertEqual(render.load(d, "one")["runs"][0]["variant"], "dnd")
            self.assertNotIn("variant", render.load(d, "mixed")["runs"][0])

    def test_joined_dataset_notes_stand_under_the_overview(self):
        note = "New model: current product. Other models: historical release."
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "benchmarks", "data"))
            for name, model, notes in (("old", "gpt-6.1-sol", []), ("new", "gpt-6.1-terra", [note])):
                with open(os.path.join(d, "benchmarks", "data", name + ".json"), "w") as f:
                    json.dump({"sources": [], "runs": [run(model, 1, True, 50, 12)], "notes": notes}, f)
            data = render.load(os.path.join(d, "benchmarks", "data"), "old,new")
            self.assertEqual(len(data["runs"]), 2)
            self.assertEqual(data["notes"], [note])
            page = os.path.join(d, "benchmarks.md")
            with open(page, "w") as f:
                f.write("<!-- render old,new scoreboard -->\n<!-- /render -->\n\n<!-- render old,new time -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                text = f.read()
            self.assertEqual(text.count(f"<sub>{note}</sub>"), 1)
            self.assertLess(text.index(note), text.index("render old,new time"))

    def test_joined_datasets_keep_every_run(self):
        with tempfile.TemporaryDirectory() as d:
            for name, model in (("a", "gpt-6.1-sol"), ("b", "claude-opus-5-5")):
                with open(os.path.join(d, name + ".json"), "w") as f:
                    json.dump({"sources": [{"commit": name}], "runs": [run(model, 1, True, 50, 12)]}, f)
            with open(os.path.join(d, "c.json"), "w") as f:
                json.dump([run("claude-sonnet-5-5", 1, True, 40, 10)], f)
            data = render.load(d, "a,b,c")
            self.assertEqual([r["model"] for r in data["runs"]], ["gpt-6.1-sol", "claude-opus-5-5", "claude-sonnet-5-5"])
            self.assertEqual(data["sources"], [{"commit": "a"}, {"commit": "b"}])


class Toolkits(unittest.TestCase):
    """The toolkit views split the form task's runs by variant, and the other views refuse several variants."""

    def test_efforts_have_separate_table_rows_matching_the_figure(self):
        runs = [dict(run("gpt-6.1-terra", rep, passed, wall, tools), task="form", variant="qt-25",
                     effort=effort, ran_at=effort, kinds={})
                for effort, rep, passed, wall, tools in (("low", 1, True, 30, 10), ("low", 2, False, 40, 12),
                                                       ("high", 1, True, 300, 50))]
        data = {"runs": runs}
        table = render.toolkits_table(data)
        self.assertIn("| Toolkit | Agent | Effort | Passed |", table)
        self.assertIn("| Qt 6 | gpt-6.1-terra | low | 1/2 | 35 (30–40) | 11 (10–12) |", table)
        self.assertIn("| Qt 6 | gpt-6.1-terra | high | 1/1 | 300 | 50 |", table)
        outcome = table.split("**Tokens and cost**")[0]
        self.assertEqual(len([line for line in outcome.splitlines() if line.startswith("| Qt 6 |")]), 2)
        figure, _ = render.toolkits_figure(data)
        svg = figure.render("light", "Efforts")
        for text in (">low</text>", ">high</text>", ">35 s</text>", ">300 s</text>"):
            self.assertIn(text, svg)

    def test_effort_groups_report_the_effort_actually_used(self):
        runs = [dict(run("gpt-6.1-terra", 1, True, 30, 10), task="form", variant="25",
                     effort="low", ran_at="medium", kinds={}),
                dict(run("gpt-6.1-terra", 1, True, 300, 50), task="form", variant="25",
                     effort="high", ran_at="high", kinds={})]
        table = render.toolkits_table({"runs": runs})
        self.assertIn("| GTK 4 | gpt-6.1-terra | low, ran medium | 1/1 | 30 |", table)
        self.assertIn("| GTK 4 | gpt-6.1-terra | high | 1/1 | 300 |", table)

    def test_toolkit_views(self):
        runs = [dict(run(model, rep, True, wall, 30), task="form", variant=variant, kinds={})
                for variant, wall in (("25", 120), ("qt-25", 80), ("gtk3-x11-25", 140))
                for model in ("gpt-6.1-sol", "claude-sonnet-5-5") for rep in (1, 2)]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "benchmarks", "data"))
            with open(os.path.join(d, "benchmarks", "data", "tk.json"), "w") as f:
                json.dump({"sources": [], "runs": runs}, f)
            page = os.path.join(d, "benchmarks.md")
            with open(page, "w") as f:
                f.write("# T\n\n<!-- render tk toolkits -->\n<!-- /render -->\n\n"
                        "<!-- render tk toolkits-table -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                text = f.read()
            self.assertIn('src="benchmarks/tk-toolkits.svg"', text)
            outcome = text.split("**Tokens and cost**")[0]
            rows = [line.split(" | ")[:3] for line in outcome.splitlines() if line.startswith("| ") and "---" not in line]
            self.assertEqual(rows[1:], [["| GTK 4", "gpt-6.1-sol", "2/2"], ["| GTK 4", "Sonnet 5.5", "2/2"],
                                        ["| GTK 3 (X11)", "gpt-6.1-sol", "2/2"], ["| GTK 3 (X11)", "Sonnet 5.5", "2/2"],
                                        ["| Qt 6", "gpt-6.1-sol", "2/2"], ["| Qt 6", "Sonnet 5.5", "2/2"]])
            # The other views take a variant of the form task by its name.
            with open(page, "w") as f:
                f.write("# T\n\n<!-- render tk table form-qt-25 -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                self.assertIn("| gpt-6.1-sol | medium | 2/2 | 80 (80–80) |", f.read())
            with open(page, "w") as f:
                f.write("# T\n\n<!-- render tk table form -->\n<!-- /render -->\n")
            with self.assertRaises(SystemExit) as cm:
                main(page)
            self.assertIn("It has form-25, form-qt-25, form-gtk3-x11-25.", str(cm.exception))

            with open(os.path.join(d, "benchmarks", "data", "tk.json"), "w") as f:
                json.dump({"sources": [], "runs": runs + [dict(runs[0], variant="qt-10")]}, f)
            with open(page, "w") as f:
                f.write("# T\n\n<!-- render tk toolkits -->\n<!-- /render -->\n")
            with self.assertRaises(SystemExit) as cm:
                main(page)
            self.assertIn("one number of questions, not 10, 25", str(cm.exception))

    def test_the_form_without_dropdowns_is_a_task_of_its_own(self):
        def kinds(names):
            return {k: {"items": 5, "seconds": 20.0, "corrections": 0} for k in names}

        runs = [dict(run(model, rep, True, wall, 30), task="form", variant=variant, kinds=kinds(names),
                     phases={"all answered correctly": wall - 5})
                for variant, wall, names in (("25", 120, render.KINDS), ("25-no-dropdowns", 70, render.KINDS[:3]))
                for model in ("gpt-6.1-sol", "claude-sonnet-5-5") for rep in (1, 2)]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "benchmarks", "data"))
            with open(os.path.join(d, "benchmarks", "data", "f.json"), "w") as f:
                json.dump({"sources": [], "runs": runs}, f)
            page = os.path.join(d, "benchmarks.md")
            with open(page, "w") as f:
                f.write("# T\n\n<!-- render f time -->\n<!-- /render -->\n\n"
                        "<!-- render f table form-no-dropdowns -->\n<!-- /render -->\n\n"
                        "<!-- render f kinds-table form-no-dropdowns -->\n<!-- /render -->\n\n"
                        "<!-- render f kinds-table form -->\n<!-- /render -->\n\n"
                        "<!-- render f toolkits-table -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                text = f.read()
            with open(os.path.join(d, "benchmarks", "f-time.svg")) as f:
                svg = f.read()
            self.assertIn(">Form with dropdowns</text>", svg)
            self.assertIn(">Form without dropdowns</text>", svg)
            self.assertIn("<summary>Numbers: Form without dropdowns</summary>", text)
            self.assertIn("| gpt-6.1-sol | medium | 2/2 | 70 (70–70) |", text)
            self.assertIn("All right at s |", text)
            # Each kinds table asks its own questionnaire's kinds.
            self.assertIn("| Agent | Checkbox s | Slider s | Text s | Corrections |", text)
            self.assertIn("| Agent | Checkbox s | Slider s | Text s | Dropdown s | Corrections |", text)
            # The toolkits compare the questionnaire with dropdowns alone.
            self.assertIn("| GTK 4 | gpt-6.1-sol | 2/2 | 120 (120–120) |", text)

            with open(page, "w") as f:
                f.write("# T\n\n<!-- render f kinds -->\n<!-- /render -->\n")
            with self.assertRaises(SystemExit) as cm:
                main(page)
            self.assertIn("name one of form, form-no-dropdowns", str(cm.exception))

            # Its variants in several toolkits are named by the rest of their names.
            more = runs + [dict(r, variant="qt-25-no-dropdowns") for r in runs if r["variant"] == "25-no-dropdowns"]
            with open(os.path.join(d, "benchmarks", "data", "f.json"), "w") as f:
                json.dump({"sources": [], "runs": more}, f)
            data = render.load(os.path.join(d, "benchmarks", "data"), "f")
            self.assertEqual(render.tasks(data), ["form", "form-no-dropdowns-25", "form-no-dropdowns-qt-25"])
            self.assertEqual(render.task_title("form-no-dropdowns-qt-25"), "Form without dropdowns-qt-25")


# The tasks of a full rerun, as load() names them.
NINE = ["board", "calc", "forgejo", "thunderbird", "form", "form-no-dropdowns", "trace", "inkscape", "files"]
# The tasks Opus runs.
SOME = {"board", "calc", "form"}


def task_set(ts, partial=SOME):
    """Three models' runs of each task, the second of each agent's runs failed, and Opus's of the partial tasks."""
    models = ["claude-sonnet-5-5", "gpt-6.1-sol", "gpt-6.1-terra", "claude-opus-5-5"]
    return {"sources": [], "runs": [dict(run(m, rep, rep != 2, 100 + 900 * (rep == 3), 20), task=t)
                                    for t in ts for m in models for rep in (1, 2, 3)
                                    if m != "claude-opus-5-5" or t in partial]}


def overlaps(svg):
    """The texts of a figure that run into the next one on their baseline, by the widths render.py estimates."""
    pattern = r'<text x="([\d.]+)" y="([\d.]+)" class="\w+" font-size="([\d.]+)"( font-weight="600")?>([^<]*)<'
    texts = sorted((float(y), float(x), html.unescape(s), float(size), bool(bold))
                   for x, y, size, bold, s in re.findall(pattern, svg))
    return [(a[2], b[2]) for a, b in zip(texts, texts[1:])
            if a[0] == b[0] and a[1] + render.text_width(a[2], a[3], a[4]) > b[1]]


class Scoreboard(unittest.TestCase):
    def test_no_text_overlaps_for_any_number_of_tasks(self):
        for n in range(1, len(NINE) + 1):
            with self.subTest(tasks=n):
                svg, _ = render.scoreboard(task_set(NINE[:n]))
                self.assertEqual(overlaps(svg.render("light", "")), [])

    def test_a_task_an_agent_did_not_run_reads_as_not_run(self):
        # Two tasks fit as columns of a row per agent, nine take a row each.
        for ts in (["board", "files"], NINE):
            with self.subTest(tasks=len(ts)):
                data = task_set(ts)
                svg = render.scoreboard(data)[0].render("light", "")
                self.assertEqual(svg.count(">not run</text>"), len(set(ts) - SOME))
                # A failed run's ring, and the legend's.
                self.assertEqual(svg.count('class="fail"'), sum(not r["passed"] for r in data["runs"]) + 1)
                self.assertIn(f">Opus 5.5 ran {len(set(ts) & SOME)} of the {len(ts)} tasks. An agent&#x27;s "
                              "runs passed and cost are of the tasks it ran.</text>", svg)
        self.assertIn("Opus 5.5 ran 3 of the 9 tasks.", render.costs_table(task_set(NINE)))
        self.assertNotIn("tasks it ran", render.costs_table(task_set(NINE, partial=NINE)))
        # The time figure's panels list only the agents that ran their task.
        time = render.time_figure(task_set(NINE))[0].render("light", "")
        self.assertNotIn(">not run</text>", time)
        self.assertEqual(time.count(">Opus 5.5</text>"), len(SOME))

    def test_a_bar_gives_each_cell_the_share_of_its_runs_that_passed(self):
        # Eighteen runs of one agent beside six of another: a share and its count, not a dot per run.
        runs = [run("gpt-6.1-sol", k, k <= 7, 50.0, 10.0) for k in range(1, 19)]
        runs += [run("gpt-6.1-terra", k, True, 30.0, 10.0) for k in range(1, 7)]
        svg = render.scoreboard({"runs": runs})[0].render("light", "")
        self.assertNotIn("<circle", svg)
        for text in (">39%</text>", ">7/18</text>", ">100%</text>", ">6/6</text>", "with the count."):
            self.assertIn(text, svg)
        self.assertEqual(overlaps(svg), [])
        # As many runs in every cell: the subtitle says how many, and the cells only the share.
        svg = render.scoreboard({"runs": runs[:3] + runs[18:21]})[0].render("light", "")
        self.assertIn("share of a task&#x27;s three runs that passed.", svg)
        self.assertNotIn('class="m" font-size="11.5">3/3</text>', svg)  # a count beside a share

    def test_columns_too_narrow_for_the_times_leave_them_to_the_time_figure(self):
        models = ["gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-astra", "claude-opus-5-5", "claude-sonnet-5-5",
                  "claude-haiku-5-5", "deepseek-v4.1-flash"]
        runs = [dict(run(m, k, k != 2, 1733.0, 10.0), task=t) for m in models for t in ("board", "calc", "forgejo",
                "thunderbird") for k in range(1, 4)]
        svg = render.scoreboard({"runs": runs})[0].render("light", "")
        self.assertIn(">Runs passed and cost</text>", svg)
        self.assertIn("The figure below gives the times, which do not fit here.", svg)
        self.assertNotIn(">1733 s</text>", svg)
        self.assertEqual(overlaps(svg), [])

    def test_every_view_names_a_task_by_task_names(self):
        self.assertEqual([render.task_title(t) for t in ("files", "form", "form-no-dropdowns", "board-dnd",
                                                         "form-no-dropdowns-qt-25", "form-25")],
                         ["File manager", "Form with dropdowns", "Form without dropdowns", "Board-dnd",
                          "Form without dropdowns-qt-25", "Form with dropdowns-25"])
        data = task_set(["files", "form"])
        for svg in (render.scoreboard(data)[0], render.time_figure(data)[0], render.compare_figure(data, data)[0]):
            text = svg.render("light", "")
            self.assertIn(">File manager</text>", text)
            self.assertNotIn(">Files</text>", text)
        self.assertIn("| File manager |", render.compare_table(data, data))
        self.assertEqual(render.table_summary("table", "files"), "Numbers: File manager")


class Kinds(unittest.TestCase):
    def test_a_model_keeps_its_colour_in_each_task(self):
        def kinds(names):
            return {k: {"items": 5, "seconds": 20.0, "corrections": 0} for k in names}

        runs = task_set(["form", "form-no-dropdowns"], partial=("form",))["runs"]
        runs = [dict(r, kinds=kinds(render.KINDS if r["task"] == "form" else render.KINDS[:3]),
                     passed=r["passed"] or r["model"] != "claude-sonnet-5-5") for r in runs]
        # Sonnet passes no run of the form with dropdowns.
        runs = [dict(r, passed=False) if r["model"] == "claude-sonnet-5-5" and r["task"] == "form" else r for r in runs]
        data = {"runs": runs}
        with_dropdowns = render.kinds_figure(data, "form")[0].render("light", "")
        without = render.kinds_figure(data, "form-no-dropdowns")[0].render("light", "")
        self.assertIn("Sonnet 5.5 passed no run.", with_dropdowns)
        self.assertNotIn("passed no run", without)
        # Opus, third of the models, keeps the third colour where Sonnet, fourth, is in the figure in its place.
        for svg, model, cls in ((with_dropdowns, "Opus 5.5", "s2"), (without, "Sonnet 5.5", "s3")):
            self.assertRegex(svg, f'class="{cls}" rx="2"/>\n<text [^>]*>{model}</text>')


def series_run(setup, task, model, rep, ok, wall, browser="firefox"):
    """A run of a registered series, as export.py writes it and load marks its dataset."""
    return {"run": f"main--{task}/{model}-{rep}", "dataset": f"v2-{setup}", "task": task, "backend": setup, "browser": browser,
            "harness": "codex", "model": model, "passed": ok, "wall_s": wall, "tools_s": wall / 2, "calls": 10,
            "input_tokens": 100, "cache_read_tokens": 10, "cost_usd": 0.1}


def verdict(setup, task, model, rep, what, reason=None, browser="firefox"):
    return {"setup": setup, "series": "main", "task": task, "run": f"{model}-{rep}", "browser": browser, "verdict": what,
            "unclear_reason": reason}


class Registered(unittest.TestCase):
    """The headline numbers as bench/comparison-series.json fixes them."""
    SOL, TERRA = "gpt-6.1-sol", "gpt-6.1-terra"

    def row(self, table, *cells):
        return next(line for line in table.splitlines() if all(f"| {c} |" in line for c in cells))

    def test_the_mean_of_two_models_is_not_a_pool(self):
        runs = [series_run("cua", "files", self.SOL, i, True, 30) for i in range(6)]
        runs += [series_run("cua", "files", self.TERRA, i, False, 30) for i in range(2)]
        table = render.headline_table({"runs": runs, "analysis": {"min_passes": 3}})
        # Pooled, 6 of 8 would read 75 %.
        self.assertIn("| 50 % |", self.row(table, "Mean of both"))
        self.assertIn("6/6 (61–100 %)", table)

    def test_the_headline_counts_only_the_tools_failures(self):
        runs = [series_run("cua", "files", self.SOL, i, False, 30) for i in (1, 2)]
        verdicts = [verdict("cua", "files", self.SOL, 1, "tool_problem"), verdict("cua", "files", self.SOL, 2, "model_mistake")]
        table = render.headline_table({"runs": runs, "analysis": {"min_passes": 3}, "verdicts": verdicts})
        self.assertIn("| 1 of 2 |", table)

    def test_the_same_run_in_two_browsers_keeps_its_own_verdict(self):
        runs = [series_run("cua", "files", self.SOL, 1, False, 30, browser) for browser in ("firefox", "chromium")]
        verdicts = [verdict("cua", "files", self.SOL, 1, "tool_problem", browser="firefox"),
                    verdict("cua", "files", self.SOL, 1, "model_mistake", browser="chromium")]
        table = render.headline_table({"runs": runs, "analysis": {"min_passes": 3}, "verdicts": verdicts})
        self.assertIn("| 1 of 2 |", table)

    def test_a_failed_run_without_a_verdict_stops_the_page(self):
        runs = [series_run("cua", "files", self.SOL, 1, False, 30), series_run("cua", "files", self.SOL, 2, False, 30)]
        with self.assertRaises(SystemExit):
            render.headline_table({"runs": runs, "verdicts": [verdict("cua", "files", self.SOL, 1, "tool_problem")]})

    def test_the_time_ratio_is_a_geometric_mean_over_units_with_enough_passes(self):
        runs = []
        for rep in range(3):
            runs += [series_run("hyprmasq", task, self.SOL, rep, True, 10) for task in ("inkscape", "files", "thunderbird")]
            runs += [series_run("cua", "inkscape", self.SOL, rep, True, 20), series_run("cua", "files", self.SOL, rep, True, 80),
                     series_run("cua", "thunderbird", self.SOL, rep, rep < 2, 500)]
        row = self.row(render.headline_time_table({"runs": runs, "analysis": {"min_passes": 3}}), "Cua Driver")
        # The geometric mean of 2 and 8 is 4, where the arithmetic one is 5, and Thunderbird's 2 passes leave it out.
        self.assertIn("| 4.00 |", row)
        self.assertIn("| 2 of 3 |", row)
        self.assertIn("Add a mail account, answer a mail (passed: Cua Driver 2, hyprmasq 3)", row)

    def test_the_cost_ratio_is_a_geometric_mean_that_counts_failed_runs(self):
        def priced(setup, task, ok, cost):
            return render.compared(dict(series_run(setup, task, self.SOL, 1, ok, 30), cost_usd=cost))
        base = [priced("hyprmasq", "files", True, 0.1), priced("hyprmasq", "inkscape", True, 0.1)]
        ours = [priced("cua", "files", False, 0.2), priced("cua", "inkscape", True, 0.8)]
        self.assertAlmostEqual(render.cost_ratio(ours, base), 4.0)

    def test_a_typical_time_takes_only_agents_with_enough_passes(self):
        data = {"analysis": {"min_passes": 3}}
        both = [render.compared(series_run("cua", "files", self.SOL, i, True, 20)) for i in range(3)]
        both += [render.compared(series_run("cua", "files", self.TERRA, i, True, 80)) for i in range(3)]
        value, used = render.typical_time(both, data)
        self.assertAlmostEqual(value, 40.0)
        self.assertEqual(used, 2)
        # Two passes with Terra are too few to time, and its failed runs never count.
        one = both[:3] + [render.compared(series_run("cua", "files", self.TERRA, i, i < 2, 900)) for i in range(3)]
        value, used = render.typical_time(one, data)
        self.assertAlmostEqual(value, 20.0)
        self.assertEqual(used, 1)

    def test_the_figures_keep_their_columns_apart(self):
        runs, verdicts = [], []
        for setup in ("hyprmasq", "cua", "hypruse", "computer-use-linux"):
            for model in (self.SOL, self.TERRA):
                runs += [dict(series_run(setup, "files", model, i, i > 0, 30), no_view_change=True) for i in range(3)]
                verdicts.append(verdict(setup, "files", model, 0, "tool_problem"))
        data = {"runs": runs, "verdicts": verdicts, "analysis": {"min_passes": 1}}
        for figure in (render.scorecard_figure, render.unit_grid_figure, render.user_view_figure, render.attribution_figure,
                       render.usage_figure):
            svg, _ = figure(data)
            self.assertEqual(overlaps(svg.render("light", "x")), [], figure.__name__)

    def test_the_user_figure_counts_the_focus_of_passed_runs_and_the_typing_that_was_lost(self):
        def run(rep, ok, away, kept):
            return dict(series_run("cua", "forgejo", self.SOL, rep, ok, 30), focus_away_s=away, cursor_displacement_px=100 * rep,
                        user_work={"kept": kept})
        # The failed run took the focus too, and does not count: a run that did nothing would count as polite.
        runs = [run(1, True, 40.0, True), run(2, True, 0.0, False), run(3, False, 90.0, False)]
        svg = render.user_view_figure({"runs": runs})[0].render("light", "x")
        for text in ("1 of 2", "20 s", "2 of 3", "200 px"):
            self.assertIn(f">{text}</text>", svg)

    def test_the_usage_figure_takes_medians_of_every_run_and_cuts_a_value_far_above_the_rest(self):
        def run(setup, rep, ok, cost, requests):
            return dict(series_run(setup, "files", self.SOL, rep, ok, 30), cost_usd=cost,
                        context={"request_count": requests, "peak_input_tokens": 20000})
        runs = [run("hyprmasq", i, True, 0.02, 6) for i in (1, 2, 3)]
        # The failed run counts: a run that gave up is not cheap.
        runs += [run("cua", 1, True, 0.10, 10), run("cua", 2, False, 0.30, 14), run("cua", 3, True, 0.20, 12)]
        runs += [run("hypruse", i, True, 3.0, 8) for i in (1, 2, 3)]
        svg = render.usage_figure({"runs": runs})[0].render("light", "x")
        for text in ("$0.02", "$0.20", "$3.00", "12", "20k"):
            self.assertIn(f">{text}</text>", svg)
        # Only hypruse's cost runs off the scale, with the two strokes of a break.
        self.assertEqual(svg.count('class="cut"'), 2)

    def test_the_failures_figure_leaves_out_the_passed_runs_and_the_slices(self):
        runs = [series_run("cua", "files", self.SOL, i, i > 2, 30) for i in (1, 2, 3)]
        runs += [series_run("cua-attach", "files", self.SOL, 1, False, 30)]
        verdicts = [verdict("cua", "files", self.SOL, 1, "tool_problem"), verdict("cua", "files", self.SOL, 2, "unclear", "x"),
                    verdict("cua-attach", "files", self.SOL, 1, "model_mistake")]
        svg = render.attribution_figure({"runs": runs, "verdicts": verdicts})[0].render("light", "x")
        self.assertIn(">2 of 3 failed</text>", svg)
        self.assertNotIn("debugging port", svg)

    def test_each_failed_run_counts_once_with_the_reasons_for_unclear(self):
        runs = [series_run("cua", "files", self.SOL, 1, True, 30)]
        runs += [series_run("cua", "files", self.SOL, i, False, 30) for i in (2, 3, 4)]
        verdicts = [verdict("cua", "files", self.SOL, 2, "tool_problem"), verdict("cua", "files", self.SOL, 3, "model_mistake"),
                    verdict("cua", "files", self.SOL, 4, "unclear", "reviewers_disagree")]
        table = render.attribution_table({"runs": runs, "verdicts": verdicts})
        self.assertIn("| 1/4 | 1 | 1 | 1 | reviewers disagree 1 |", table)


def tool_run(task, model, backend, rep, passed, wall, platform="hyprland", focus=0.0, popups=None, dialogs=0):
    """A run as bench/laptop/analyze.py writes it."""
    return {"harness": "codex", "model": model, "task": task, "toolkit": "gtk", "platform": platform,
            "backend": backend, "rep": rep, "passed": passed, "passed_without_hyprland_dialogs": passed,
            "hyprland_dialogs": [{"at": 1.0}] * dialogs, "wall_seconds": wall, "tool_seconds": wall / 4,
            "calls": 10, "errors": 0, "usage": {"input_tokens": 200000, "uncached_input_tokens": 20000,
            "cost_usd": 0.1}, "context": {"peak_input_tokens": 30000},
            "focus": {"seconds_away_from_user": focus}, **({"popups": popups} if popups else {})}


class Comparison(unittest.TestCase):
    def test_disturbance_uses_the_measured_union(self):
        row = {"focus": {"seconds_away_from_user": 3.0, "seconds_disturbed": 5.0},
               "popups": {"seconds_visible": 3.0}}
        self.assertEqual(render.disturbance(row), (5.0, "focus or windows"))

    """A page named comparison.md takes its runs from comparison/data, joined from several datasets."""

    def test_input_beyond_the_first_request(self):
        """A run's input less the first request, which each of its requests carries again: the work's own input."""
        r = tool_run("files", "gpt-6.1-sol", "hyprmasq", 1, True, 40)
        r["context"] = {"first_input_tokens": 9000, "request_count": 8, "peak_input_tokens": 21000, "compactions": 0}
        self.assertEqual(render.beyond_first(r), (200000 - 8 * 9000) / 1000)
        self.assertIsNone(render.beyond_first(tool_run("files", "gpt-6.1-sol", "hyprmasq", 1, True, 40)))
        r["context"]["compactions"] = 1
        self.assertIsNone(render.beyond_first(r))
        # Codex announced more compactions than it recorded: their number is not known.
        r["context"]["compactions"] = None
        self.assertIsNone(render.beyond_first(r))

    def test_disturbance_names_each_way_once(self):
        focus = {"focus": {"seconds_away_from_user": 30, "seconds_disturbed": 30}, "popups": {"seconds_visible": 0},
                 "passed": True}
        both = {"focus": {"seconds_away_from_user": 30, "seconds_disturbed": 40}, "popups": {"seconds_visible": 20},
                "passed": True}
        self.assertEqual(render.disturbance_label(35, [focus, both]), "2 of 2 · 35 s, focus/windows")

    def test_comparison_views(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "comparison", "data"))
            with open(os.path.join(d, "comparison", "data", "a.json"), "w") as f:
                json.dump([tool_run("form", "gpt-6.1-sol", "hyprmasq", r, True, 100 + r, dialogs=2)
                           for r in (1, 2, 3)], f)
            with open(os.path.join(d, "comparison", "data", "b.json"), "w") as f:
                json.dump([tool_run("form", "gpt-6.1-sol", "cua", 1, True, 150, focus=120.0),
                           tool_run("form", "gpt-6.1-sol", "cua", 2, False, 60),
                           tool_run("form", "gpt-6.1-sol", "openai-cu", 1, True, 90, platform="macos",
                                    popups={"shown": 3, "seconds_visible": 20.0}),
                           dict(tool_run("form", "gpt-6.1-sol", "hyprmasq", 1, True, 80), toolkit="qt"),
                           # A session that ended before its first token count recorded no usage.
                           dict(tool_run("form", "claude-sonnet-5-5", "openai-cu", 1, False, 30, platform="macos"),
                                usage={"input_tokens": None, "uncached_input_tokens": None, "cost_usd": None},
                                context={})], f)
            page = os.path.join(d, "comparison.md")
            with open(page, "w") as f:
                f.write("# C\n\n<!-- render a,b comparison -->\n<!-- /render -->\n\n"
                        "<!-- render a,b comparison-table -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                text = f.read()
            self.assertIn('src="comparison/comparison.svg"', text)
            self.assertIn("| gpt-6.1-sol | Cua Driver | Hyprland · GTK | 1/2 | 150 |", text)
            # hyprmasq has a row for each toolkit, GTK's before Qt's, and both before the other tools.
            outcome = text.split("**Tokens and cost**")[0]
            tools = [line.split(" | ")[1:3] for line in outcome.splitlines() if line.startswith("| gpt-6.1-sol")]
            self.assertEqual(tools, [["hyprmasq", "Hyprland · GTK"], ["hyprmasq", "Hyprland · Qt"],
                                     ["Cua Driver", "Hyprland · GTK"], ["OpenAI computer use", "macOS · GTK"]])
            # Usage that a run did not record is unknown, in the table and the figure.
            self.assertIn("| Sonnet 5.5 | OpenAI computer use | macOS · GTK | 0/1 | – | – | 10 | 0 |", text)
            self.assertIn("| Sonnet 5.5 | OpenAI computer use | macOS · GTK | – | – | – | – |", text)
            with open(os.path.join(d, "comparison", "comparison.svg")) as f:
                svg = f.read()
            # The disturbance counts the passed runs that disturbed the user: the failed Cua run took no focus, the
            # passed one 120 s.
            self.assertIn("1 of 1 · 120 s, focus", svg)
            self.assertIn("1 of 1 · 20 s, windows", svg)
            self.assertIn("0 of 3, ANR dialogs", svg)
            # Each row's runs as the scoreboard draws them: a share that passed and its count, not a dot per run.
            self.assertNotIn("<circle", svg)
            self.assertIn(">50%</text>", svg)
            self.assertIn(">1/2</text>", svg)
            main("--check", page)

    def test_a_registered_dataset_follows_the_rules_analysis(self):
        def vm_run(backend, rep, passed, wall, view=True, kept=None):
            return dict(run("claude-sonnet-5-5", rep, passed, wall, wall / 2), task="forgejo", backend=backend,
                        harness="claude", user_no_view_change=view, platform=None,
                        **({"user_work": {"kept": kept, "lost_chars": 0 if kept else 5}} if kept is not None else {}))
        runs = ([vm_run("hyprmasq", r, r != 4, 60 + r, kept=True) for r in (1, 2, 3, 4)]
                + [vm_run("cua", r, r <= 2, 100 + r, view=r != 1, kept=r != 2) for r in (1, 2, 3, 4)])
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "comparison", "data"))
            for name, analysis in (("plain", None), ("registered", {"min_passes": 3, "interval": "wilson-95"})):
                with open(os.path.join(d, "comparison", "data", name + ".json"), "w") as f:
                    json.dump({**({"analysis": analysis} if analysis else {}), "sources": [], "runs": runs}, f)
            page = os.path.join(d, "comparison.md")
            with open(page, "w") as f:
                f.write("# C\n\n<!-- render registered comparison-table -->\n<!-- /render -->\n\n"
                        "<!-- render plain comparison-table -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                registered, plain = f.read().split("<!-- render plain")
        # 3 of 4 passed: the Wilson interval, and the passed runs' time; their one failure's time apart.
        self.assertIn("| hyprmasq | Hyprland VM | 3/4 (30–95 %) | 62 (61–63) | 31 (30–32) | 64 | 4/4 | 4/4 |", registered)
        # 2 of 4 passed: no time for the passed runs, as fewer than 3 passed; the failed runs' times; the runs that left
        # the user's view as it was, and those that kept the user's work.
        self.assertIn("| Cua Driver | Hyprland VM | 2/4 (15–85 %) | – | – | 104 (103–104) | 3/4 | 3/4 |", registered)
        # A dataset that registers no analysis keeps the table it had, with a time from any pass.
        self.assertIn("| Cua Driver | Hyprland VM | 2/4 | 102 (101–102) |", plain)
        self.assertNotIn("%)", plain)
        self.assertEqual(render.wilson(0, 4)[0], 0.0)

    def test_a_page_named_after_another_shares_its_directory(self):
        """comparison-macos.md has no directory of its own, so it reads comparison/data and draws into comparison,
        and neither page alone takes the other's figures for ones that no block draws."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "comparison", "data"))
            with open(os.path.join(d, "comparison", "data", "a.json"), "w") as f:
                json.dump([tool_run("files", "gpt-6.1-sol", "hyprmasq", 1, True, 40)], f)
            page, other = os.path.join(d, "comparison.md"), os.path.join(d, "comparison-macos.md")
            for path, view in ((page, "comparison"), (other, "comparison-time")):
                with open(path, "w") as f:
                    f.write(f"# C\n\n<!-- render a {view} -->\n<!-- /render -->\n")
            main(page, other)
            with open(other) as f:
                self.assertIn('src="comparison/a-comparison-time.svg"', f.read())
            stray = os.path.join(d, "comparison", "stray.svg")
            with open(stray, "w") as f:
                f.write("<svg/>")
            # One page of the two cannot tell a stray figure from the other page's.
            main(page)
            main("--check", page)
            self.assertTrue(os.path.exists(os.path.join(d, "comparison", "a-comparison-time.svg")))
            self.assertTrue(os.path.exists(stray))
            with self.assertRaises(SystemExit):
                main("--check", page, other)
            main(page, other)
            self.assertFalse(os.path.exists(stray))

    def test_a_block_per_task(self):
        """With a task, a comparison block shows that task's rows alone, in figures named after it."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "comparison", "data"))
            with open(os.path.join(d, "comparison", "data", "a.json"), "w") as f:
                json.dump([tool_run("form", "gpt-6.1-sol", "hyprmasq", 1, True, 100),
                           tool_run("inkscape", "gpt-6.1-sol", "cua", 1, True, 40)], f)
            page = os.path.join(d, "comparison.md")
            with open(page, "w") as f:
                f.write("# C\n\n<!-- render a comparison inkscape -->\n<!-- /render -->\n\n"
                        "<!-- render a comparison-table inkscape -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                text = f.read()
            self.assertIn('src="comparison/a-comparison-inkscape.svg"', text)
            self.assertIn("| gpt-6.1-sol | Cua Driver | Hyprland | 1/1 |", text)
            self.assertNotIn("| Form with dropdowns |", text)
            # A figure of one task names only the agent above its rows.
            with open(os.path.join(d, "comparison", "a-comparison-inkscape.svg")) as f:
                svg = f.read()
            self.assertIn(">Hyprland · gpt-6.1-sol</text>", svg)
            self.assertNotIn("Inkscape · gpt-6.1-sol", svg)
            with open(page, "w") as f:
                f.write("# C\n\n<!-- render a costs inkscape -->\n<!-- /render -->\n")
            with self.assertRaises(SystemExit):
                main(page)

    def test_joined_datasets_of_one_view_and_task_keep_their_own_figures(self):
        """Two blocks of the same view and task over several datasets each: the later one's figure is named after
        its last dataset too, and draws over no figure of the earlier."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "comparison", "data"))
            for name, tool in (("a", "hyprmasq"), ("b", "cua"), ("c", "openai-cu")):
                with open(os.path.join(d, "comparison", "data", name + ".json"), "w") as f:
                    json.dump([tool_run("files", "gpt-6.1-sol", tool, 1, True, 40)], f)
            page = os.path.join(d, "comparison.md")
            with open(page, "w") as f:
                f.write("# C\n\n<!-- render a,b comparison files -->\n<!-- /render -->\n\n"
                        "<!-- render a,c comparison files -->\n<!-- /render -->\n")
            main(page)
            with open(page) as f:
                text = f.read()
            self.assertIn('src="comparison/comparison-files.svg"', text)
            self.assertIn('src="comparison/c-comparison-files.svg"', text)
            with open(os.path.join(d, "comparison", "comparison-files.svg")) as f:
                self.assertIn("Cua Driver", f.read())
            with open(os.path.join(d, "comparison", "c-comparison-files.svg")) as f:
                self.assertIn("OpenAI computer use", f.read())
            main("--check", page)

    def test_a_figure_named_as_another_figure_dark_theme_fails(self):
        """A block whose dark figure would be an earlier block's light one draws over no figure."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "comparison", "data"))
            with open(os.path.join(d, "comparison", "data", "a.json"), "w") as f:
                json.dump([tool_run("files", "gpt-6.1-sol", "hyprmasq", 1, True, 40),
                           tool_run("files-dark", "gpt-6.1-sol", "hyprmasq", 1, True, 40)], f)
            page = os.path.join(d, "comparison.md")
            with open(page, "w") as f:
                f.write("# C\n\n<!-- render a comparison files-dark -->\n<!-- /render -->\n\n"
                        "<!-- render a comparison files -->\n<!-- /render -->\n")
            with self.assertRaises(SystemExit) as cm:
                main(page)
            self.assertIn("two blocks draw", str(cm.exception))

if __name__ == "__main__":
    unittest.main()
