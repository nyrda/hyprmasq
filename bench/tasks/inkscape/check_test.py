"""Tests the Inkscape task's grader on constructed saves of Mara's drawing: python3 check_test.py"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check  # noqa: E402

ORIGINAL = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "drawing.svg"), "rb").read()

# The drawing as Inkscape 1.4.4 saved it with Ctrl+S after a move in the VM: its namespaces' attributes, its view
# of the page and an empty defs added.
SAVED = """<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<svg
   width="640"
   height="480"
   viewBox="0 0 640 480"
   version="1.1"
   id="svg1"
   sodipodi:docname="drawing.svg"
   inkscape:version="1.4.4 (dcaf3e7d9e, 2026-05-05)"
   xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
   xmlns:sodipodi="http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
   xmlns="http://www.w3.org/2000/svg"
   xmlns:svg="http://www.w3.org/2000/svg">
  <defs
     id="defs1" />
  <sodipodi:namedview
     id="namedview1"
     inkscape:document-units="px"
     pagecolor="#ffffff"
     bordercolor="#000000"
     borderopacity="0.25"
     inkscape:showpageshadow="2"
     inkscape:pageopacity="0.0"
     inkscape:pagecheckerboard="0"
     inkscape:deskcolor="#d1d1d1"
     inkscape:zoom="0.71875"
     inkscape:cx="320"
     inkscape:cy="239.30435"
     inkscape:window-width="1920"
     inkscape:window-height="1080"
     inkscape:window-x="0"
     inkscape:window-y="0"
     inkscape:window-maximized="1"
     inkscape:current-layer="svg1" />
  <rect
     id="bench-rectangle"
     x="{x}"
     y="120"
     width="200"
     height="100"
     {rest} />
</svg>
"""


def saved(x="122", rest='fill="#3478c6"'):
    return SAVED.replace("{x}", x).replace("{rest}", rest).encode()


class Grade(unittest.TestCase):
    def test_a_correct_save_passes(self):
        result = check.grade(ORIGINAL, saved(), mtime=2000.0, opened=[], title="drawing.svg - Inkscape")
        self.assertTrue(result["passed"], result["failures"])
        self.assertEqual(result["checks"]["geometry"], [122.0, 120.0, 200.0, 100.0])
        self.assertEqual(result["checks"]["saved_by"], "1.4.4 (dcaf3e7d9e, 2026-05-05)")
        self.assertFalse(result["checks"]["unsaved_changes_at_end"])
        self.assertEqual(result["events"], {"drawing saved": 2000.0})

    def test_the_move_as_a_transform_or_with_the_fill_as_style_passes(self):
        for x, rest in [("120", 'transform="translate(2)" fill="#3478c6"'),
                        ("120", 'transform="matrix(1,0,0,1,2,0)" fill="#3478C6"'),
                        ("122", 'style="fill:#3478c6"')]:
            with self.subTest(rest=rest):
                result = check.grade(ORIGINAL, saved(x, rest), opened=[])
                self.assertTrue(result["passed"], result["failures"])

    def test_an_unsaved_change_fails(self):
        result = check.grade(ORIGINAL, ORIGINAL, opened=[], title="*drawing.svg - Inkscape")
        self.assertEqual(result["failures"], ["drawing.svg was not saved: it is as it was"])
        self.assertTrue(result["checks"]["unsaved_changes_at_end"])
        self.assertEqual(result["events"], {})

    def test_a_wrong_position_fails(self):
        for x, rest in [("122.5", 'fill="#3478c6"'), ("122", 'transform="translate(0,2)" fill="#3478c6"')]:
            with self.subTest(x=x, rest=rest):
                result = check.grade(ORIGINAL, saved(x, rest), opened=[])
                self.assertFalse(result["passed"])
                self.assertEqual(len(result["failures"]), 1)
                self.assertTrue(result["failures"][0].startswith("saved rectangle geometry"), result["failures"])
        result = check.grade(ORIGINAL, saved("124"), opened=[])
        self.assertEqual(result["failures"],
                         ["saved rectangle geometry [124.0, 120.0, 200.0, 100.0], expected [122.0, 120.0, 200.0, 100.0]"])

    def test_another_attribute_changed_fails(self):
        cases = [
            ('fill="#ff0000"', "the rectangle's fill is '#ff0000'; it was '#3478c6'"),
            ('fill="#3478c6" stroke="#000000"', "the rectangle's stroke is '#000000'; it had none"),
            ('style="fill:#3478c6;opacity:0.5"', "the rectangle's opacity is '0.5'; it had none"),
            ('rx="4" fill="#3478c6"', "the rectangle's rx is '4'; it had none"),
            ("", "the rectangle lost its fill '#3478c6'"),
        ]
        for rest, failure in cases:
            with self.subTest(rest=rest):
                result = check.grade(ORIGINAL, saved(rest=rest), opened=[])
                self.assertEqual(result["failures"], [failure])

    def test_another_element_or_page_size_fails(self):
        doc = saved().replace(b"</svg>", b'<circle id="c1" cx="5" cy="5" r="3"/></svg>')
        self.assertEqual(check.grade(ORIGINAL, doc, opened=[])["failures"],
                         ["the drawing has another element: circle 'c1'"])
        doc = saved().replace(b'width="640"', b'width="800"')
        self.assertEqual(check.grade(ORIGINAL, doc, opened=[])["failures"],
                         ["the page's width is '800'; it was '640'"])

    def test_the_whole_drawing_s_presentation_changed_fails(self):
        cases = [
            (b'opacity="0"', "the drawing's opacity is '0'; it had none"),
            (b'style="display:none"', "the drawing's display is 'none'; it had none"),
            (b'transform="scale(2)"', "the drawing's transform is 'scale(2)'; it had none"),
        ]
        for attribute, failure in cases:
            with self.subTest(attribute=attribute):
                doc = saved().replace(b'id="svg1"', b'id="svg1" ' + attribute)
                self.assertEqual(check.grade(ORIGINAL, doc, opened=[])["failures"], [failure])

    def test_a_removed_rectangle_or_a_broken_file_fails(self):
        doc = saved().replace(b'id="bench-rectangle"', b'id="rect2"')
        self.assertEqual(check.grade(ORIGINAL, doc, opened=[])["failures"], [
            "the drawing has no rectangle bench-rectangle", "the drawing has another element: rect 'rect2'"])
        self.assertFalse(check.grade(ORIGINAL, b"<svg", opened=[])["passed"])
        self.assertEqual(check.grade(ORIGINAL, None, opened=[])["failures"], ["drawing.svg is gone"])

    def test_inkscape_must_read_the_geometry_alike(self):
        result = check.grade(ORIGINAL, saved(), opened=[], queried=[122.0, 120.0, 200.0, 100.0], inkscape=True)
        self.assertTrue(result["passed"], result["failures"])
        result = check.grade(ORIGINAL, saved(), opened=[], queried=None, inkscape=True)
        self.assertEqual(result["failures"],
                         ["Inkscape reads the rectangle as None, the checker as [122.0, 120.0, 200.0, 100.0]"])


class Windows(unittest.TestCase):
    def log(self, text):
        f = tempfile.NamedTemporaryFile("w", suffix=".log", delete=False)
        f.write(text)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_another_app_s_window_fails_and_inkscape_s_dialogs_do_not(self):
        path = self.log("900.0 openwindow>>a1,1,foot,before the agent\n"
                        "1001.0 openwindow>>b2,drawing,org.inkscape.Inkscape,Document Properties\n"
                        "1002.0 openwindow>>c3,1,foot,a shell\n")
        opened = check.other_windows(path, "org.inkscape.Inkscape", 1000.0)
        self.assertEqual(opened, [{"class": "foot", "title": "a shell", "workspace": "1"}])
        result = check.grade(ORIGINAL, saved(), opened=opened)
        self.assertEqual(result["failures"], ["a window other than Inkscape's opened: foot 'a shell'"])

    def test_without_the_log_it_fails(self):
        self.assertIsNone(check.other_windows("/nonexistent", "org.inkscape.Inkscape", 0))
        result = check.grade(ORIGINAL, saved(), opened=None)
        self.assertEqual(result["failures"], ["no record of the windows that opened"])


if __name__ == "__main__":
    unittest.main()
