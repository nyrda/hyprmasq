#!/usr/bin/env python3
"""Tests the board task's grader and attribution on constructed states and logs: python3 check_test.py"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import attribute  # noqa: E402
import check  # noqa: E402


def centre(box):
    return [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2]


CORRECT = {label: centre(boxes[0]) for label, boxes in check.REGIONS["labels"].items()}
RAM = centre(check.REGIONS["distractors"]["loose RAM module"][0])
# A correct board of a run from before the M.2 slots label, under the names it had.
OLD_CORRECT = {("8-pin CPU power" if label == "CPU power headers" else label): point
               for label, point in CORRECT.items() if label != "M.2 slots"}


class Grade(unittest.TestCase):
    def test_correct_board(self):
        result = check.grade(CORRECT)
        self.assertTrue(result["passed"])
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["checks"]["correct"], 9)

    def test_run_from_before_the_m2_slots_label_grades_on_its_eight_labels(self):
        result = check.grade(OLD_CORRECT)
        self.assertTrue(result["passed"])
        self.assertEqual(sorted(result["checks"]["labels"]), sorted(OLD_CORRECT))
        self.assertEqual(result["checks"]["correct"], 8)
        result = check.grade({**OLD_CORRECT, "8-pin CPU power": None})
        self.assertEqual(result["failures"], ["8-pin CPU power is still in the tray"])

    def test_label_on_the_ram_module(self):
        result = check.grade({**CORRECT, "Memory slots": RAM})
        self.assertFalse(result["passed"])
        self.assertEqual(result["checks"]["correct"], 8)
        self.assertEqual(result["checks"]["labels"]["Memory slots"]["verdict"], "distractor")
        self.assertEqual(result["failures"], [f"Memory slots is on the loose RAM module, at ({RAM[0]:g}, {RAM[1]:g})"])

    def test_label_in_the_tray(self):
        result = check.grade({**CORRECT, "BMC chip": None})
        self.assertEqual(result["failures"], ["BMC chip is still in the tray"])
        self.assertEqual(result["checks"]["correct"], 8)

    def test_label_missing_from_the_state_is_in_the_tray(self):
        labels = dict(CORRECT)
        del labels["CPU socket"]
        self.assertEqual(check.grade(labels)["failures"], ["CPU socket is still in the tray"])

    def test_label_on_another_labels_part(self):
        usb = CORRECT["USB 3.0 header"]
        result = check.grade({**CORRECT, "SATA ports": usb})
        self.assertEqual(result["checks"]["labels"]["SATA ports"], {"point": usb, "verdict": "other label",
                                                                      "on": "USB 3.0 header"})

    def test_label_on_no_part(self):
        result = check.grade({**CORRECT, "CPU socket": [30, 30]})
        self.assertEqual(result["failures"], ["CPU socket is at (30, 30), on no part"])

    def test_second_box_counts(self):
        result = check.grade({**CORRECT, "SATA ports": centre(check.REGIONS["labels"]["SATA ports"][1])})
        self.assertTrue(result["passed"])

    def test_m2_slots_from_the_drive_bay_to_the_sockets(self):
        # gpt-6.1-sol's drops where the SSD would be installed, and one on the sockets.
        for point in ([1373.8, 490.8], [1375.2, 507.8], [1399.2, 493.7], [1324.4, 662.9]):
            self.assertTrue(check.grade({**CORRECT, "M.2 slots": point})["passed"], point)

    def test_no_point_is_correct_for_two_labels(self):
        boxes = [(label, box) for label, label_boxes in check.REGIONS["labels"].items() for box in label_boxes]
        for i, (a, (ax0, ay0, ax1, ay1)) in enumerate(boxes):
            for b, (bx0, by0, bx1, by1) in boxes[i + 1:]:
                if a != b:
                    self.assertFalse(ax0 <= bx1 and bx0 <= ax1 and ay0 <= by1 and by0 <= ay1, f"{a} and {b} overlap")

    def test_milestones(self):
        places = [{"time": 5, "label": "CPU socket", "point": [1, 1]}]  # before the agent started
        places += [{"time": 10 + i, "label": label, "point": point} for i, (label, point) in enumerate(CORRECT.items())]
        places += [{"time": 30, "label": "BMC chip", "point": None}, {"time": 31, "label": "BMC chip", "point": [2, 2]}]
        self.assertEqual(list(check.events(places, since=8).items()),
                         [("first label placed", 10), ("all labels placed", 18), ("last change", 31)])
        self.assertEqual(check.events([], since=8), {})
        old = [{"time": 10 + i, "label": label, "point": point} for i, (label, point) in enumerate(OLD_CORRECT.items())]
        self.assertEqual(check.events(old)["all labels placed"], 17)


# A page laid out as in the VM at 1920×1080: the content 85.5 logical pixels below the window's top, the photo at
# 16,60 in it at a scale of 0.709, and the tray's chips at x 1464 to 1656.
OFFSET = (0, 85.5)
PHOTO = [16, 60, 1434, 979]
SCALE = (PHOTO[2] - PHOTO[0]) / 2000
CHIPS = {label: [1464, 175 + 42 * i, 1656, 207 + 42 * i] for i, label in enumerate(
    ["First PCIe slot", "Memory slots", "BMC chip", "24-pin ATX power", "SATA ports", "CPU socket", "M.2 slots",
     "USB 3.0 header", "CPU power headers"])}
WINDOW = "0x1234"
VM_TO_HOST = 4000.0


def client_of_photo(point):
    return [PHOTO[0] + point[0] * SCALE, PHOTO[1] + point[1] * SCALE]


def shot_of_client(point):
    """In s1, a 1280×720 shot of the 1920×1080 window."""
    return [(point[0] + OFFSET[0]) / 1.5, (point[1] + OFFSET[1]) / 1.5]


class Run:
    """A run directory under construction: act calls in mcp.jsonl, and the page's events."""

    def __init__(self, variant="dnd"):
        self.tap, self.page, self.id, self.t = [], [], 1, 5000.0
        self.page_event(4999, "layout", variant=variant, screen=list(OFFSET), photo=PHOTO, tray=[1450, 60, 1670, 563],
                        labels={label: [chip] for label, chip in CHIPS.items()}, dpr=1)

    def page_event(self, host, kind, **fields):
        self.page.append({"t": (host - VM_TO_HOST) * 1000, "type": kind, **fields})

    def act(self, actions, shot="s1", done=None, result_shot=None, text=None):
        """An act call. With text, its result has only that text, as hyprmasq-mcp answers Claude Code."""
        self.id += 1
        start, end = self.t, self.t + 1
        self.tap.append({"t": start, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": "act", "arguments": {"window": WINDOW, "shot": shot, "actions": actions}}}})
        sc = {"actions": {"done": len(actions) if done is None else done, "total": len(actions)}}
        if result_shot:
            sc["shot"] = result_shot
        result = {"content": [{"type": "text", "text": text}]} if text else {
            "content": [{"type": "text", "text": "1/1 delivered"}], "structuredContent": sc}
        self.tap.append({"t": end, "dir": "response", "msg": {"id": self.id, "result": result}})
        self.t += 3
        return start

    def call(self, name, arguments, text):
        """A call of another tool whose result has only text."""
        self.id += 1
        self.tap.append({"t": self.t, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": name, "arguments": arguments}}})
        self.tap.append({"t": self.t + 0.2, "dir": "response", "msg": {"id": self.id, "result": {
            "content": [{"type": "text", "text": text}, {"type": "image", "bytes": 1}]}}})
        self.t += 1

    def screenshot(self):
        self.id += 1
        self.tap.append({"t": self.t, "dir": "request", "msg": {"id": self.id, "method": "tools/call", "params": {
            "name": "screenshot", "arguments": {"window": WINDOW}}}})
        self.tap.append({"t": self.t + 0.2, "dir": "response", "msg": {"id": self.id, "result": {
            "content": [], "structuredContent": {"shot": {"id": "s1", "width": 1280, "height": 720,
                                                          "frame": {"x": 0, "y": 0, "width": 1920, "height": 1080}}}}}})
        self.t += 1

    def drag(self, label, to_photo=None, to_client=None, landed=None, seen=True, dropped=True, done=None,
             result_shot=None, shot="s1", via_shot=None, text=None):
        """An act drag from LABEL's chip to a point, and what the page logs for it."""
        chip = CHIPS[label]
        start_client = [(chip[0] + chip[2]) / 2, (chip[1] + chip[3]) / 2]
        to_client = to_client or client_of_photo(to_photo)
        from_shot, to_shot = (via_shot or shot_of_client)(start_client), (via_shot or shot_of_client)(to_client)
        start = self.act([{"action": "drag", "from": from_shot, "to": to_shot}], shot=shot, done=done,
                         result_shot=result_shot, text=text)
        if not seen or done == 0:
            return
        self.page_event(start + 0.1, "pointerdown", x=start_client[0], y=start_client[1], label=label)
        landed = landed or to_client
        if not dropped:
            self.page_event(start + 0.15, "dragstart", x=start_client[0], y=start_client[1], label=label)
            self.page_event(start + 0.16, "pointercancel", x=start_client[0], y=start_client[1], label=label)
            self.page_event(start + 0.17, "dragend", x=start_client[0], y=start_client[1], label=label)
            self.page_event(start + 0.4, "pointerup", x=landed[0], y=landed[1], label=None)
            return
        self.page_event(start + 0.4, "pointerup", x=landed[0], y=landed[1], label=label)
        inside_photo = attribute.inside(PHOTO, landed)
        point = [round((landed[0] - PHOTO[0]) / SCALE, 1), round((landed[1] - PHOTO[1]) / SCALE, 1)] \
            if inside_photo else None
        self.page_event(start + 0.4, "place", label=label, x=landed[0], y=landed[1],
                        to="photo" if inside_photo else "tray", point=point)

    def write(self, d, final):
        with open(os.path.join(d, "ready.json"), "w") as f:
            json.dump({"task_window": WINDOW, "vm_time": 1000.0, "host_time": 1000.0 + VM_TO_HOST}, f)
        for name, lines in (("mcp.jsonl", self.tap), ("vm-board-events.jsonl", self.page)):
            with open(os.path.join(d, name), "w") as f:
                f.writelines(json.dumps(line) + "\n" for line in lines)
        with open(os.path.join(d, "vm-result.json"), "w") as f:
            json.dump({"task": {"check": check.grade(final)}}, f)
        return d


class Attribute(unittest.TestCase):
    def run_of(self, build, final, first=Run.screenshot, variant="dnd"):
        run = Run(variant)
        first(run)
        build(run)
        with tempfile.TemporaryDirectory(prefix="board-attribution-") as d:
            return attribute.attribute(run.write(d, final))

    def test_correct_drags_and_calibration(self):
        out = self.run_of(lambda r: [r.drag(label, point) for label, point in CORRECT.items()], CORRECT)
        self.assertEqual(out["calibration"]["offset"], [0, 85.5])
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct"] * 9)
        self.assertTrue(all(v["blame"] is None for v in out["labels"].values()))

    def test_run_from_before_the_m2_slots_label(self):
        with mock.patch.dict(CHIPS, {"8-pin CPU power": CHIPS["CPU power headers"]}):
            out = self.run_of(lambda r: [r.drag(label, point) for label, point in OLD_CORRECT.items()], OLD_CORRECT)
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct"] * 8)
        self.assertEqual(sorted(out["labels"]), sorted(OLD_CORRECT))

    def test_label_dropped_on_the_ram_module_is_the_models(self):
        def build(r):
            r.drag("CPU socket", CORRECT["CPU socket"])
            r.drag("Memory slots", RAM)
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"], "Memory slots": RAM})
        drag = out["drags"][1]
        self.assertEqual((drag["outcome"], drag["blame"]), ("model", "model"))
        self.assertIn("loose RAM module", drag["why"])
        self.assertEqual(out["labels"]["Memory slots"]["blame"], "model")

    def test_label_left_in_the_tray_is_the_models(self):
        out = self.run_of(lambda r: r.drag("CPU socket", CORRECT["CPU socket"]), {"CPU socket": CORRECT["CPU socket"]})
        self.assertEqual(out["labels"]["BMC chip"], {"verdict": "tray", "on": None, "drags": 0, "blame": "model",
                                                      "failed_drags": 0, "point": None})

    def test_drop_far_from_where_aimed_is_hyprmasqs(self):
        aimed = client_of_photo(CORRECT["BMC chip"])

        def build(r):
            r.drag("CPU socket", CORRECT["CPU socket"])
            r.drag("BMC chip", CORRECT["BMC chip"], landed=[aimed[0] + 40, aimed[1]])
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"]})
        drag = out["drags"][1]
        self.assertEqual((drag["outcome"], drag["blame"]), ("hyprmasq", "hyprmasq"))
        self.assertEqual(drag["off"], 40)
        self.assertEqual(out["labels"]["BMC chip"]["blame"], "hyprmasq")

    def test_drag_the_page_never_saw_is_hyprmasqs(self):
        def build(r):
            r.drag("CPU socket", CORRECT["CPU socket"])
            r.drag("SATA ports", CORRECT["SATA ports"], seen=False)
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"]})
        drag = out["drags"][1]
        self.assertEqual((drag["outcome"], drag["why"]), ("hyprmasq", "the page saw no press"))
        self.assertEqual(drag["aim_label"], "SATA ports")
        self.assertEqual(out["labels"]["SATA ports"]["blame"], "hyprmasq")

    def test_press_lost_in_a_batch(self):
        # One act call with three drags, the second of which the page never saw: the others keep their presses.
        def build(r):
            moves = [("CPU socket", CORRECT["CPU socket"]), ("SATA ports", CORRECT["SATA ports"]),
                     ("BMC chip", CORRECT["BMC chip"])]
            actions, start = [], r.t
            for label, point in moves:
                chip = CHIPS[label]
                actions.append({"action": "drag", "from": shot_of_client([(chip[0] + chip[2]) / 2, (chip[1] + chip[3]) / 2]),
                                "to": shot_of_client(client_of_photo(point))})
            r.act(actions)
            for n, (label, point) in enumerate(moves):
                if label == "SATA ports":
                    continue
                chip, to = CHIPS[label], client_of_photo(point)
                r.page_event(start + 0.1 + n * 0.3, "pointerdown", x=(chip[0] + chip[2]) / 2, y=(chip[1] + chip[3]) / 2,
                             label=label)
                r.page_event(start + 0.3 + n * 0.3, "place", label=label, x=to[0], y=to[1], to="photo", point=point)
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"], "BMC chip": CORRECT["BMC chip"]})
        self.assertEqual(out["calibration"]["offset"], [0, 85.5])  # the layout's mozInnerScreenX/Y
        self.assertEqual([(d["outcome"], d.get("label")) for d in out["drags"]],
                         [("correct", "CPU socket"), ("hyprmasq", None), ("correct", "BMC chip")])
        self.assertEqual(out["drags"][1]["why"], "the page saw no press where the drag started")

    def test_cancelled_drag_and_drop_is_hyprmasqs(self):
        out = self.run_of(lambda r: r.drag("CPU socket", CORRECT["CPU socket"], dropped=False), {})
        drag = out["drags"][0]
        self.assertEqual(drag["blame"], "hyprmasq")
        self.assertEqual(drag["why"], "the label did not move; the page saw dragstart, pointercancel, dragend, pointerup")
        self.assertEqual(out["labels"]["CPU socket"]["failed_drags"], 1)

    def test_drag_too_short_to_start_a_drag_and_drop_is_the_models(self):
        # Haiku's nudge of the SATA ports label by (3, 6) CSS pixels: the page saw the press and the release where it
        # aimed, and the moves between, but Firefox starts a drag-and-drop only past 8 device pixels along an axis.
        def nudge(by, released=True):
            def build(r):
                press = centre(CHIPS["SATA ports"])
                end = [press[0] + by[0], press[1] + by[1]]
                start = r.act([{"action": "drag", "from": shot_of_client(press), "to": shot_of_client(end)}])
                r.page_event(start + 0.1, "pointerdown", x=press[0], y=press[1], label="SATA ports")
                for n in range(1, 4):
                    r.page_event(start + 0.1 + n * 0.02, "pointermove", x=press[0] + by[0] * n / 3,
                                 y=press[1] + by[1] * n / 3, label="SATA ports")
                if released:
                    r.page_event(start + 0.2, "pointerup", x=end[0], y=end[1], label="SATA ports")
            return build

        drag = self.run_of(nudge((3, 6)), {})["drags"][0]
        self.assertEqual((drag["outcome"], drag["why"]), ("model", "too short to start a drag-and-drop"))
        self.assertEqual(drag["page"], ["pointerup"])
        # Past the threshold, Firefox should have started one: the label not moving is hyprmasq's.
        drag = self.run_of(nudge((3, 12)), {})["drags"][0]
        self.assertEqual((drag["outcome"], drag["why"]), ("hyprmasq", "the label did not move; the page saw pointerup"))
        # Shorter than SLOP, but the page never saw the release.
        drag = self.run_of(nudge((2, 1), released=False), {})["drags"][0]
        self.assertEqual((drag["outcome"], drag["why"]),
                         ("hyprmasq", "the label did not move; the page saw only the press"))
        # The pointer variant moves a label once it is dragged SLOP pixels, so there it is hyprmasq's too.
        drag = self.run_of(nudge((3, 6)), {}, variant="pointer")["drags"][0]
        self.assertEqual(drag["outcome"], "hyprmasq")

    def test_back_to_the_tray_and_refused_batches(self):
        def build(r):
            r.drag("CPU socket", CORRECT["CPU socket"])
            r.drag("BMC chip", to_client=[1500, 300])
            r.drag("USB 3.0 header", CORRECT["USB 3.0 header"], done=0)
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"]})
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct", "tray", "not sent"])

    def test_page_zoomed_out(self):
        # At 90%, a CSS pixel is 0.9 logical pixels, and the page logs its devicePixelRatio as 0.9.
        def zoomed(client):
            return [(client[0] * 0.9 + OFFSET[0]) / 1.5, (client[1] * 0.9 + OFFSET[1]) / 1.5]

        def build(r):
            r.drag("CPU socket", CORRECT["CPU socket"])
            r.page_event(r.t - 0.5, "layout", photo=PHOTO, tray=[1450, 60, 1670, 563], labels={}, dpr=0.9)
            r.drag("BMC chip", CORRECT["BMC chip"], via_shot=zoomed)
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"], "BMC chip": CORRECT["BMC chip"]})
        self.assertEqual(out["calibration"]["spread"], 0)
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct", "correct"])

    def test_zoom_into_a_change(self):
        # The first drag's result has a zoom s3 of the box 300,200 to 1120,400 of s2, at 1.5 logical pixels each: the
        # logical pixels 450,300 to 1680,600, which hold the CPU and the tray, captured at scale 1.
        zoom = {"id": "s2", "width": 1280, "height": 720, "frame": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                "changes": {"base": "s1", "regions": [{"shot": "s3", "image_width": 1230, "image_height": 300,
                                                       "x": 300, "y": 200, "width": 820, "height": 200}]}}

        def in_zoom(client):
            return [client[0] + OFFSET[0] - 450, client[1] + OFFSET[1] - 300]

        def build(r):
            r.drag("BMC chip", CORRECT["BMC chip"], result_shot=zoom)
            r.drag("CPU socket", CORRECT["CPU socket"], shot="s3", via_shot=in_zoom)
        out = self.run_of(build, {"BMC chip": CORRECT["BMC chip"], "CPU socket": CORRECT["CPU socket"]})
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct", "correct"])
        self.assertEqual(out["drags"][1]["aim_point"], [round(v, 1) for v in CORRECT["CPU socket"]])


# The texts of results without structured content, which hyprmasq-mcp gives Claude Code, in the forms of
# mcp/internal/server/result.go, windows.go and capture.go.
WINDOWS_TEXT = (f'{WINDOW} · firefox "Figure 3: The motherboard · Build guide — Mozilla Firefox" · workspace web '
                f"(hidden) · 1920×1080 · lease 60 s (this session)\nshot s1 1280×720 of window {WINDOW}")
DELIVERED = "1/1 delivered · drag-and-drop finished · redraw observed · quiet 200 ms · 220 ms"


class AttributeText(unittest.TestCase):
    """The attribution of results that have only text: the drags they delivered, mapped through frames that the text
    leaves to the shots they came from."""

    def run_of(self, build, final):
        return Attribute.run_of(self, build, final, first=lambda r: r.call("windows", {"match": "firefox"},
                                                                           WINDOWS_TEXT))

    def test_drags_in_a_windows_shot_and_in_a_zoom_into_a_change(self):
        # The zoom s3 of Attribute.test_zoom_into_a_change, as its line names it.
        def in_zoom(client):
            return [client[0] + OFFSET[0] - 450, client[1] + OFFSET[1] - 300]

        def build(r):
            r.drag("BMC chip", CORRECT["BMC chip"], text=f"{DELIVERED} · shot s2 = s1 + 1 change · lease 60 s\n"
                                                          "zoom s3 1230×300 of s2 at 300,200 820×200")
            r.drag("CPU socket", CORRECT["CPU socket"], shot="s3", via_shot=in_zoom,
                   text=f"{DELIVERED} · shot s4 = s2, no change outside animations · lease 60 s")
            r.drag("SATA ports", CORRECT["SATA ports"], shot="s4", text=f"{DELIVERED} · shot s5 1280×720")
        out = self.run_of(build, {"BMC chip": CORRECT["BMC chip"], "CPU socket": CORRECT["CPU socket"],
                                  "SATA ports": CORRECT["SATA ports"]})
        self.assertEqual(out["calibration"]["offset"], [0, 85.5])
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct", "correct", "correct"])
        self.assertEqual(out["drags"][1]["aim_point"], [round(v, 1) for v in CORRECT["CPU socket"]])

    def test_refused_and_partly_sent_batches(self):
        def build(r):
            r.drag("CPU socket", CORRECT["CPU socket"], text=f"{DELIVERED} · shot s2 1280×720 · lease 60 s")
            r.drag("BMC chip", CORRECT["BMC chip"], done=0, shot="s9",
                   text="error stale_shot: there is no shot s9; this server keeps the last 64 shots of each window\n"
                        "Take a screenshot and read the coordinates from the new shot.")
            # Of two drags, the first went out before the sender lost its connection.
            moves = [("SATA ports", CORRECT["SATA ports"]), ("USB 3.0 header", CORRECT["USB 3.0 header"])]
            start = r.act([{"action": "drag", "from": shot_of_client(centre(CHIPS[label])),
                            "to": shot_of_client(client_of_photo(point))} for label, point in moves], shot="s2",
                          text="error internal: the sender stopped\n1/2 delivered, delivery uncertain from actions[1] "
                               "(drag) on · shot s5 1280×720 · lease 60 s")
            chip, to = CHIPS["SATA ports"], client_of_photo(CORRECT["SATA ports"])
            r.page_event(start + 0.1, "pointerdown", x=centre(chip)[0], y=centre(chip)[1], label="SATA ports")
            r.page_event(start + 0.3, "place", label="SATA ports", x=to[0], y=to[1], to="photo",
                         point=CORRECT["SATA ports"])
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"], "SATA ports": CORRECT["SATA ports"]})
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct", "not sent", "correct", "not sent"])
        self.assertTrue(out["drags"][1]["why"].startswith("error stale_shot"))

    def test_drag_in_a_region_screenshot(self):
        # A region of s1 from 200,100, 1000×600 of its pixels: the logical pixels 300,150 to 1800,1050, at half a
        # pixel each.
        def in_region(client):
            return [(client[0] + OFFSET[0] - 300) / 2, (client[1] + OFFSET[1] - 150) / 2]

        def build(r):
            r.call("screenshot", {"window": WINDOW, "region": {"shot": "s1", "x": 200, "y": 100, "width": 1000,
                                                               "height": 600}},
                   f"shot s2 750×450 · zoom of s1 at 200,100 1000×600 · window {WINDOW} 1920×1080")
            r.drag("CPU socket", CORRECT["CPU socket"], shot="s2", via_shot=in_region,
                   text=f"{DELIVERED} · shot s3 1280×720 · lease 60 s")
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"]})
        self.assertEqual(out["drags"][0]["outcome"], "correct")
        self.assertEqual(out["drags"][0]["aim_point"], [round(v, 1) for v in CORRECT["CPU socket"]])

    def test_shots_whose_frames_the_text_does_not_tell(self):
        def build(r):
            # act's shot of the whole window has another image size than s1, as after a resize that no text named.
            r.drag("CPU socket", CORRECT["CPU socket"], text=f"{DELIVERED} · shot s2 960×540 · lease 60 s")
            # Then s1's image size again, which a window of another size can have too.
            r.drag("BMC chip", CORRECT["BMC chip"], shot="s2", text=f"{DELIVERED} · shot s3 1280×720")
            r.drag("USB 3.0 header", CORRECT["USB 3.0 header"], shot="s3",
                   text=f"{DELIVERED} · shot s4 = s3, no change")
            # An image taller than the window's aspect ratio: popups reach outside the window.
            r.call("screenshot", {"window": WINDOW}, f"shot s5 1280×800 · window {WINDOW} 1920×1080")
            r.drag("SATA ports", CORRECT["SATA ports"], shot="s5", text=f"{DELIVERED} · shot s6 = s5, no change")
        out = self.run_of(build, {"CPU socket": CORRECT["CPU socket"]})
        self.assertEqual([d["outcome"] for d in out["drags"]], ["correct", "unmapped", "unmapped", "unmapped"])
        self.assertIn("may have been resized", out["drags"][1]["why"])
        self.assertIn("gives no size of its window", out["drags"][2]["why"])
        self.assertIn("popups reach outside the window", out["drags"][3]["why"])


if __name__ == "__main__":
    unittest.main()
