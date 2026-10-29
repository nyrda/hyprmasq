"""Tests the file manager task's grader on folders and logs it builds: python3 check_test.py"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check  # noqa: E402

START = 1000.0
NAUTILUS = "org.gnome.Nautilus"


def make(root, files):
    for path, contents in files.items():
        full = os.path.join(root, path)
        if contents is None:
            os.makedirs(full, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(contents)


class Grade(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        make(self.root, check.START)

    def tearDown(self):
        shutil.rmtree(self.root)

    def at(self, path):
        return os.path.join(self.root, path)

    def grade(self, opened=()):
        return check.grade(check.tree(self.root), list(opened))

    def test_the_report_renamed_into_archive_passes(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        result = self.grade()
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["checks"]["tree"], check.expected_tree())

    def test_the_folder_as_it_started_fails(self):
        self.assertEqual(self.grade()["failures"],
                         ["missing: Archive/report-final.txt", "unexpected: report-draft.txt"])

    def test_renamed_but_not_moved_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("report-final.txt"))
        self.assertEqual(self.grade()["failures"],
                         ["missing: Archive/report-final.txt", "unexpected: report-final.txt"])

    def test_moved_but_not_renamed_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-draft.txt"))
        self.assertEqual(self.grade()["failures"],
                         ["missing: Archive/report-final.txt", "unexpected: Archive/report-draft.txt"])

    def test_copied_instead_of_moved_fails(self):
        shutil.copy(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        self.assertEqual(self.grade()["failures"], ["unexpected: report-draft.txt"])

    def test_the_reports_contents_changed_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        with open(self.at("Archive/report-final.txt"), "a") as f:
            f.write("final\n")
        self.assertEqual(self.grade()["failures"], ["changed: Archive/report-final.txt"])

    def test_another_file_touched_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        with open(self.at("notes.txt"), "w") as f:
            f.write("")
        os.rename(self.at("Drafts/ideas.txt"), self.at("ideas.txt"))
        os.mkdir(self.at("New Folder"))
        self.assertEqual(self.grade()["failures"], [
            "missing: Drafts/ideas.txt", "unexpected: New Folder", "unexpected: ideas.txt", "changed: notes.txt",
        ])

    def test_a_folder_emptied_or_removed_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        shutil.rmtree(self.at("Drafts"))
        self.assertEqual(self.grade()["failures"], ["missing: Drafts", "missing: Drafts/ideas.txt"])

    def test_another_apps_window_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        result = self.grade([{"class": "foot", "title": "foot", "workspace": "files"}])
        self.assertEqual(result["failures"], ["a window other than the file manager's opened: foot 'foot'"])

    def test_without_the_log_of_windows_it_fails(self):
        os.rename(self.at("report-draft.txt"), self.at("Archive/report-final.txt"))
        result = check.grade(check.tree(self.root), None)
        self.assertEqual(result["failures"], ["no record of the windows that opened"])

    def test_the_laptops_folder_is_the_same(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "laptop"))
        import run_files
        self.assertEqual(check.START, run_files.START)
        self.assertEqual(check.EXPECTED, run_files.EXPECTED)


class OtherWindows(unittest.TestCase):
    def test_windows_of_another_class_after_the_start(self):
        log = "\n".join([
            "900.0 openwindow>>aaa,1,foot,user-terminal",
            "901.0 openwindow>>bbb,1,org.gnome.Nautilus,Project",
            "1001.0 openwindow>>ccc,files,org.gnome.Nautilus,File Already Exists",
            "1001.0 windowpid>>0xccc,42",
            "1002.0 activewindowv2>>ccc",
            "1003.0 openwindow>>ddd,files,org.gnome.TextEditor,report-draft.txt",
        ])
        with tempfile.NamedTemporaryFile("w", suffix=".log") as f:
            f.write(log + "\n")
            f.flush()
            opened = check.other_windows(f.name, NAUTILUS, START)
        self.assertEqual(opened, [{"class": "org.gnome.TextEditor", "title": "report-draft.txt",
                                   "workspace": "files"}])
        self.assertIsNone(check.other_windows(f.name + ".missing", NAUTILUS, START))


class Milestones(unittest.TestCase):
    def tree(self, moves):
        files = {p: check.digest(c) for p, c in check.START.items()}
        for old, new in moves:
            files[new] = files.pop(old)
        return files

    def test_renamed_then_moved(self):
        watch = [
            {"time": START - 5, "tree": self.tree([])},
            {"time": START + 10, "tree": self.tree([("report-draft.txt", "report-final.txt")])},
            {"time": START + 20, "tree": self.tree([("report-draft.txt", "Archive/report-final.txt")])},
        ]
        self.assertEqual(check.milestones(watch, START), {
            "first change": START + 10, "report renamed": START + 10, "report moved": START + 20,
            "folder right": START + 20,
        })

    def test_moved_then_renamed(self):
        watch = [
            {"time": START + 10, "tree": self.tree([("report-draft.txt", "Archive/report-draft.txt")])},
            {"time": START + 20, "tree": self.tree([("report-draft.txt", "Archive/report-final.txt")])},
        ]
        self.assertEqual(check.milestones(watch, START), {
            "first change": START + 10, "report moved": START + 10, "report renamed": START + 20,
            "folder right": START + 20,
        })

    def test_a_folder_as_it_started_has_none(self):
        with tempfile.TemporaryDirectory() as root:
            make(root, check.START)
            self.assertEqual(check.milestones([{"time": START + 1, "tree": check.tree(root)}], START), {})


if __name__ == "__main__":
    unittest.main()
