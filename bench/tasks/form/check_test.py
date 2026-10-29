"""Tests the form task's grader on constructed states of the questionnaire: python3 check_test.py"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check  # noqa: E402
import items  # noqa: E402

START = 1000.0


def answers(count):
    return {str(i["number"]): i["answer"] for i in items.items(count)}


def state(values, events=(), submitted_at=(2000.0,), on_screen=None):
    return {
        "values": on_screen or values,
        "events": list(events),
        "submissions": [{"time": t, "values": values} for t in submitted_at],
    }


class Grade(unittest.TestCase):
    def test_every_answer_right_passes_at_each_length(self):
        for count in (10, 25, 40):
            result = check.grade(state(answers(count)), count, START)
            self.assertTrue(result["passed"], result["failures"])
            self.assertEqual(result["checks"]["correct"], count)

    def test_counts_outside_the_questionnaire_are_refused(self):
        for count in (9, 41):
            with self.assertRaises(ValueError):
                items.items(count)

    def test_the_kinds_take_turns(self):
        self.assertEqual([i["kind"] for i in items.items(10)],
                         ["checkbox", "slider", "text", "dropdown"] * 2 + ["checkbox", "slider"])

    def test_a_questionnaire_without_dropdowns_numbers_and_grades_the_rest(self):
        os.environ["HYPRMASQ_FORM_KINDS"] = "checkbox,slider,text"
        try:
            asked = items.items(25)
            self.assertEqual([i["kind"] for i in asked[:4]], ["checkbox", "slider", "text", "checkbox"])
            self.assertEqual([i["number"] for i in asked], list(range(1, 26)))
            self.assertEqual(asked[3]["question"], "Tick 7.")
            result = check.grade(state(answers(25)), 25, START)
            self.assertTrue(result["passed"], result["failures"])
            self.assertEqual(sorted(result["checks"]["kinds"]), ["checkbox", "slider", "text"])
            with self.assertRaises(ValueError):
                items.items(31)
        finally:
            del os.environ["HYPRMASQ_FORM_KINDS"]

    def test_a_wrong_answer_of_each_kind_is_named(self):
        values = {**answers(10), "1": ["Amber", "Olive"], "2": "38", "3": "Northwind 24", "4": items.PLACEHOLDER}
        result = check.grade(state(values), 10, START)
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"]["correct"], 6)
        self.assertEqual(result["failures"], [
            "Question 1 (checkbox) is ticked Amber and Olive; expected ticked Amber, Cobalt and Olive",
            "Question 2 (slider) is at 38; expected at 37",
            "Question 3 (text) is 'Northwind 24'; expected 'Northwind 42'",
            "Question 4 (dropdown) is Choose…; expected Saturn",
        ])

    def test_ticks_in_another_order_and_spaces_around_text_count_as_right(self):
        values = {**answers(10), "1": ["Olive", "Amber", "Cobalt"], "3": "  Northwind 42 "}
        self.assertTrue(check.grade(state(values), 10, START)["passed"])

    def test_text_is_case_sensitive(self):
        values = {**answers(10), "3": "northwind 42"}
        self.assertFalse(check.grade(state(values), 10, START)["passed"])

    def test_without_submit_it_fails_even_with_right_answers_on_screen(self):
        result = check.grade(state(answers(10), submitted_at=()), 10, START)
        self.assertEqual(result["failures"], ["Submit was never pressed"])

    def test_the_last_submission_counts_and_later_changes_are_reported(self):
        wrong = {**answers(10), "2": "36"}
        s = {"values": answers(10), "events": [],
             "submissions": [{"time": 1500.0, "values": answers(10)}, {"time": 1600.0, "values": wrong}]}
        result = check.grade(s, 10, START)
        self.assertEqual(result["failures"], ["Question 2 (slider) is at 36; expected at 37"])
        self.assertEqual(result["checks"]["items"]["2"]["on_screen"], "37")
        self.assertEqual(result["checks"]["submissions"], 2)

    def test_a_submission_from_before_the_agent_started_does_not_count(self):
        result = check.grade(state(answers(10), submitted_at=(START - 1,)), 10, START)
        self.assertEqual(result["failures"], ["Submit was never pressed"])

    def test_no_state_fails(self):
        self.assertEqual(check.grade(None, 10, START)["failures"], ["the questionnaire wrote no state"])

    def test_without_the_log_of_windows_it_fails(self):
        result = check.grade(state(answers(10)), 10, START, None)
        self.assertEqual(result["failures"], ["no record of the windows that opened"])

    def test_another_window_fails_the_run(self):
        opened = [{"class": "foot", "title": "foot", "workspace": "hyprmasq"}]
        result = check.grade(state(answers(10)), 10, START, opened)
        self.assertEqual(result["failures"], ["a window other than the questionnaire opened: foot 'foot'"])


class Corrections(unittest.TestCase):
    def event(self, t, number, value, how=None):
        item = items.ITEMS[number - 1]
        return {"time": START + t, "number": number, "kind": item["kind"], "value": value,
                **({"how": how} if how else {})}

    def test_a_drag_and_typed_text_are_one_setting_each(self):
        events = [self.event(1 + i * 0.01, 2, str(v), "jump") for i, v in enumerate(range(1, 38))]
        events += [self.event(3 + i * 0.015, 3, "Northwind 42"[: i + 1]) for i in range(12)]
        result = check.grade(state(answers(10), events), 10, START)
        self.assertEqual(result["checks"]["items"]["2"]["corrections"], 0)
        self.assertEqual(result["checks"]["items"]["3"]["corrections"], 0)
        self.assertEqual(result["checks"]["items"]["2"]["moved_by"], ["jump"])

    def test_a_slider_set_again_with_keys_is_one_correction(self):
        events = [self.event(1, 2, "40", "jump"), self.event(4, 2, "39", "step-backward"),
                  self.event(4.2, 2, "38", "step-backward"), self.event(4.4, 2, "37", "step-backward")]
        item = check.grade(state(answers(10), events), 10, START)["checks"]["items"]["2"]
        self.assertEqual(item["corrections"], 1)
        self.assertEqual(item["moved_by"], ["jump", "step-backward"])

    def test_a_checkbox_ticked_by_mistake_and_cleared_is_two_corrections(self):
        events = [self.event(1, 1, ["Amber"]), self.event(1.2, 1, ["Amber", "Beige"]),
                  self.event(2, 1, ["Amber"]), self.event(2.2, 1, ["Amber", "Cobalt"]),
                  self.event(2.4, 1, ["Amber", "Cobalt", "Olive"])]
        result = check.grade(state(answers(10), events), 10, START)
        self.assertEqual(result["checks"]["items"]["1"]["corrections"], 2)
        self.assertEqual(result["checks"]["kinds"]["checkbox"]["corrections"], 2)

    def test_a_dropdown_chosen_twice_is_one_correction(self):
        events = [self.event(1, 4, "Uranus"), self.event(5, 4, "Saturn")]
        item = check.grade(state(answers(10), events), 10, START)["checks"]["items"]["4"]
        self.assertEqual(item["corrections"], 1)


class Milestones(unittest.TestCase):
    def test_all_answered_correctly_is_when_the_last_answer_became_right(self):
        count = 10
        events = []
        for n, item in enumerate(items.items(count), 1):
            events.append({"time": START + n, "number": item["number"], "kind": item["kind"],
                           "value": item["answer"]})
        result = check.grade(state(answers(count), events, submitted_at=(START + 20, START + 30)), count, START)
        self.assertEqual(result["events"], {"first answer": START + 1, "all answered correctly": START + count,
                                            "first submit": START + 20, "last submit": START + 30})
        # Each answer took the second since the one before.
        self.assertEqual(result["checks"]["kinds"]["slider"]["seconds"], 3.0)

    def test_a_wrong_final_answer_keeps_its_own_time(self):
        events = [{"time": START + 10, "number": 2, "kind": "slider", "value": "38"},
                  {"time": START + 20, "number": 3, "kind": "text", "value": "Northwind 42"}]
        result = check.grade(state(answers(10), events), 10, START)
        self.assertEqual(result["checks"]["items"]["2"]["seconds"], 10.0)
        self.assertEqual(result["checks"]["items"]["3"]["seconds"], 10.0)

    def test_answers_never_all_right_have_no_such_milestone(self):
        events = [{"time": START + 1, "number": 2, "kind": "slider", "value": "37"}]
        result = check.grade(state(answers(10), events), 10, START)
        self.assertNotIn("all answered correctly", result["events"])


class OtherWindows(unittest.TestCase):
    def test_windows_after_the_start_other_than_the_questionnaire(self):
        log = "\n".join([
            "900.0 openwindow>>aaa,1,foot,user-terminal",
            "1001.0 openwindow>>bbb,form,org.example.Questionnaire,Questionnaire",
            "1002.0 activewindowv2>>ccc",
            "1003.0 openwindow>>ccc,hyprmasq,foot,foot",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write(log + "\n")
            f.flush()
            opened = check.other_windows(f.name, "0xbbb", START)
        self.assertEqual(opened, [{"class": "foot", "title": "foot", "workspace": "hyprmasq"}])
        self.assertIsNone(check.other_windows(f.name + ".missing", "0xbbb", START))

    def test_an_x11_apps_popups_of_the_classes_named_do_not_count(self):
        log = "\n".join([
            "1001.0 openwindow>>ddd,form,ComboboxPopdown,popdown",
            "1002.0 openwindow>>eee,form,foot,foot",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write(log + "\n")
            f.flush()
            named = check.other_windows(f.name, "0xbbb", START, ["ComboboxPopdown"])
            unnamed = check.other_windows(f.name, "0xbbb", START)
        self.assertEqual(named, [{"class": "foot", "title": "foot", "workspace": "form"}])
        self.assertEqual([w["class"] for w in unnamed], ["ComboboxPopdown", "foot"])

    def test_windows_of_the_questionnaires_process_are_its_own(self):
        log = "\n".join([
            "1001.0 openwindow>>bbb,form,questionnaire,Questionnaire",
            "1001.0 windowpid>>0xbbb,42",
            "1002.0 openwindow>>ccc,form,,",
            "1002.0 windowpid>>0xccc,42",
            "1003.0 openwindow>>ddd,form,,",
            "1003.0 windowpid>>0xddd,43",
            "1004.0 openwindow>>eee,form,,",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write(log + "\n")
            f.flush()
            opened = check.other_windows(f.name, "0xbbb", START)
        self.assertEqual(opened, [{"class": "", "title": "", "workspace": "form"}] * 2)


class NoDropdowns(unittest.TestCase):
    """A variant without dropdowns, such as 25-no-dropdowns: setup.py runs the grade with the kinds that vm.nix names
    in $HYPRMASQ_FORM_KINDS, as it starts the app."""

    KINDS = {"HYPRMASQ_FORM_KINDS": "checkbox,slider,text"}

    def grade(self, values, env):
        """check.py's grade, as setup.py runs it, or None when it exits with an error."""
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "state.json"), "w") as f:
                json.dump(state(values), f)
            open(os.path.join(d, "windows.log"), "w").close()
            out = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "check.py"),
                 "--state", os.path.join(d, "state.json"), "--count", "25", "--window", "0xbbb",
                 "--events", os.path.join(d, "windows.log"), "--since", str(START)],
                env=dict(os.environ, **env), capture_output=True, text=True)
        return json.loads(out.stdout) if out.returncode == 0 else None

    def test_the_grade_asks_the_kinds_of_its_environment(self):
        with mock.patch.dict(os.environ, self.KINDS):
            right = answers(25)
        result = self.grade(right, self.KINDS)
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["checks"]["correct"], 25)
        self.assertEqual(sorted(result["checks"]["kinds"]), ["checkbox", "slider", "text"])
        # Question 4 is the second checkbox, where the questionnaire with dropdowns asks its first dropdown.
        result = self.grade({**right, "4": []}, self.KINDS)
        self.assertEqual(result["failures"], ["Question 4 (checkbox) is nothing ticked; expected ticked 7"])
        # Without the kinds, the grade asks the questionnaire with dropdowns, which these answers do not fit.
        self.assertFalse((self.grade(right, {}) or {}).get("passed"))


if __name__ == "__main__":
    unittest.main()
