"""Tests that analyze.py splits an act call's time from its result's text, as hyprmasq-mcp answers Claude Code, as it
does from the structured content, and how the attributions and the report read Cua Driver's calls from an MCP log: its
pointer calls and run_steps as act-like calls whose points map through the window's latest get_window_state
screenshot, and its refusals by code: python3 analyze_test.py"""

import os
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import actlog  # noqa: E402
import analyze  # noqa: E402
import report  # noqa: E402
from laptop import analyze as laptop_analyze  # noqa: E402


class MacWindowVisibility(unittest.TestCase):
    def measure(self, observations):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "front.jsonl").write_text(json.dumps({"time": 0, "front": "TextEdit"}) + "\n")
            Path(root, "windows.jsonl").write_text("\n".join(json.dumps(row) for row in observations) + "\n")
            return laptop_analyze.mac_disturbance(root, "TextEdit", 1, 9)

    def test_normal_layer_overlay_counts_without_a_focus_change(self):
        focus, visible = self.measure([
            {"time": 0, "windows": []},
            {"time": 2, "windows": [{"layer": 0, "over_user": True}]},
            {"time": 5, "windows": [{"layer": 0, "over_user": False}]},
        ])
        self.assertEqual(focus["seconds_away_from_user"], 0)
        self.assertEqual(visible, {"shown": 1, "seconds_visible": 3.0})

    def test_visible_interval_is_clipped_to_the_agent_run(self):
        _, visible = self.measure([
            {"time": 0, "windows": [{"layer": 0, "over_user": True}]},
            {"time": 12, "windows": []},
        ])
        self.assertEqual(visible, {"shown": 0, "seconds_visible": 8.0})

    def test_old_observations_keep_the_positive_layer_fallback(self):
        _, visible = self.measure([
            {"time": 0, "windows": [{"layer": 0}]},
            {"time": 3, "windows": [{"layer": 1, "over_user": False}]},
            {"time": 6, "windows": []},
        ])
        self.assertEqual(visible, {"shown": 1, "seconds_visible": 3.0})

    def test_disturbance_unions_focus_and_visible_windows(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "front.jsonl").write_text("\n".join(json.dumps(row) for row in [
                {"time": 0, "front": "TextEdit"}, {"time": 2, "front": "Questionnaire"},
                {"time": 5, "front": "TextEdit"}]) + "\n")
            Path(root, "windows.jsonl").write_text("\n".join(json.dumps(row) for row in [
                {"time": 0, "windows": []}, {"time": 4, "windows": [{"layer": 0, "over_user": True}]},
                {"time": 7, "windows": []}]) + "\n")
            focus, windows = laptop_analyze.mac_disturbance(root, "TextEdit", 1, 9)
        self.assertEqual(focus["seconds_away_from_user"], 3.0)
        self.assertEqual(windows["seconds_visible"], 3.0)
        self.assertEqual(focus["seconds_disturbed"], 5.0)


def both(actions, text, structured):
    """The split of one act call from its structured content, and from its text alone."""
    lines = text.split("\n")
    call = {"summary": lines[0], "hint": "\n".join(lines[1:]), "seconds": 2.0}
    args = {"actions": actions}
    return (analyze.split(call, args, structured, 500), analyze.split(call, args, {}, 500))


class SplitFromText(unittest.TestCase):
    def test_compact_actions_keep_the_report_and_pacing(self):
        compact = [["click", 10, 20], ["key", "right", 100]]
        named = [{"action": "click", "x": 10, "y": 20},
                 {"action": "key", "keys": ["right"], "repeat": 100}]
        summary = "2/2 delivered · redraw observed · quiet 200 ms · 200 ms · shot s2 640×400"
        structured = {"actions": {"done": 2, "total": 2, "delivery": "complete"},
                      "wait": {"ended": "quiet", "elapsed_ms": 200}}
        self.assertEqual(both(compact, summary, structured), both(named, summary, structured))
        points = [[10, 20], ["key", "right", 100]]
        self.assertEqual(both(points, summary, structured), both(named, summary, structured))
        calls = analyze.report.tool_calls([
            {"dir": "request", "t": 1, "msg": {"id": 1, "method": "tools/call", "params": {
                "name": "act", "arguments": {"shot": "s1", "actions": points, "next_page": {"x": 100, "y": 200}}}}},
            {"dir": "response", "t": 3, "msg": {"id": 1, "result": {"content": []}}},
        ])
        self.assertEqual(calls[0]["actions"], ["click", "key", "scroll"])

    def test_keys_of_a_partly_typed_action(self):
        actions = [{"action": "type", "text": "hello world"}]
        from_structured, from_text = both(
            actions,
            "error window_gone: window 0x1 closed while actions[0] (type) was sent\n0/1 delivered, delivery partial: "
            "actions[0] (type \"hello world\") may be partly applied (4 keys sent) · window closed",
            {"actions": {"done": 0, "total": 1, "delivery": "partial", "progress": {"keys": 4}},
             "wait": {"ended": "window_closed", "elapsed_ms": 0}})
        self.assertEqual(from_text["input"], from_structured["input"])
        self.assertGreater(from_text["input"], 0)

    def test_wait_only_call_has_no_delivery_prefix(self):
        from_structured, from_text = both(
            [], "redraw observed · quiet 200 ms · 123 ms · no change",
            {"actions": {"done": 0, "total": 0}, "wait": {"ended": "quiet", "elapsed_ms": 123}})
        self.assertEqual(from_text, from_structured)
        self.assertEqual(from_text["wait"], 0.123)
        self.assertEqual(from_text["input"], 0)

    def test_no_watch_after_a_cancelled_call(self):
        actions = [{"action": "click", "x": 1, "y": 1}]
        from_structured, from_text = both(
            actions,
            "error cancelled: the client cancelled the call\n0/1 delivered, delivery partial · cancelled",
            {"actions": {"done": 0, "total": 1, "delivery": "partial"}, "wait": {"ended": "cancelled"}})
        self.assertEqual(from_text["watch"], 0)
        self.assertEqual(from_text, from_structured)

    def test_a_batch_that_ends_with_a_scroll_waits_twice(self):
        actions = [{"action": "click", "x": 1, "y": 1}, {"action": "scroll", "x": 1, "y": 1, "dy": 3}]
        from_structured, from_text = both(
            actions,
            "2/2 delivered · lease 60 s\nbefore the scroll: redraw observed · quiet 200 ms · 412 ms · shot s2 = s1 + 1 change\n"
            "zoom s3 140×120 of s2 at 150,50 140×120\nafter the scroll: redraw observed · quiet 200 ms · 230 ms · shot s4 640×400 "
            "(full shot: 58% changed since s2)",
            {"actions": {"done": 2, "total": 2, "delivery": "complete"}, "wait": {"ended": "quiet", "elapsed_ms": 230},
             "before_scroll": {"wait": {"ended": "quiet", "elapsed_ms": 412}}})
        self.assertEqual(from_text, from_structured)
        self.assertEqual(from_text["wait"], 0.642)

    def test_the_watch_after_a_split_batch_counts_from_its_last_wait(self):
        # Two waits of 800 ms, neither of which took the watch's 500 ms in.
        actions = [{"action": "click", "x": 1, "y": 1}, {"action": "scroll", "x": 1, "y": 1, "dy": 3}]
        from_structured, from_text = both(
            actions,
            "2/2 delivered · lease 60 s\nbefore the scroll: redraw observed · quiet 200 ms · 800 ms · shot s2 = s1 + 1 change\n"
            "zoom s3 140×120 of s2 at 150,50 140×120\nafter the scroll: redraw observed · quiet 200 ms · 800 ms · shot s4 640×400 "
            "(full shot: 58% changed since s2)",
            {"actions": {"done": 2, "total": 2, "delivery": "complete"}, "wait": {"ended": "quiet", "elapsed_ms": 800},
             "before_scroll": {"wait": {"ended": "quiet", "elapsed_ms": 800}}})
        self.assertEqual(from_text, from_structured)
        self.assertEqual(from_text["watch"], 0.5)


class CurrentProtocol(unittest.TestCase):
    def test_sender_and_server_pacing_constants_have_not_drifted(self):
        root = Path(__file__).resolve().parents[1]
        sender = (root / "tools/hyprmasq-input.c").read_text()
        names = ("ACTION_GAP_MS", "MOTION_PRESS_MS", "CLICK_HOLD_MS", "CLICK_GAP_MS", "CHORD_MOD_MS",
                 "CHORD_HOLD_MS", "CHORD_GAP_MS", "TYPE_HOLD_MS", "TYPE_GAP_MS", "WHEEL_GAP_MS",
                 "DRAG_HOLD_MS", "DRAG_STEP_MS", "DRAG_STEP_PX", "DRAG_MIN_STEPS")
        constants = dict(re.findall(r"^#define\s+(\w+)\s+(\d+)\b", sender, re.MULTILINE))
        for name in names:
            with self.subTest(name=name):
                self.assertEqual(getattr(analyze, name), int(constants[name]))
        self.assertEqual(analyze.DRAG_MAX_STEPS, int(constants["MAX_DRAG_STEPS"]))
        server = (root / "mcp/internal/server/batch.go").read_text()
        for name, copied in (("clickSettleMs", analyze.CLICK_SETTLE_MS),
                             ("answerExpectMs", analyze.ANSWER_MS), ("answerQuietMs", analyze.ANSWER_MS)):
            with self.subTest(name=name):
                match = re.search(r"\b" + name + r"\s*=\s*(\d+)\b", server)
                self.assertIsNotNone(match, f"Missing server constant {name}")
                self.assertEqual(copied, int(match.group(1)))

    def test_current_watch_description_is_required(self):
        tool = {"name": "act", "description": "a redraw that starts within 500 ms after it settled"}
        tap = [{"msg": {"result": {"tools": [tool]}}}]
        self.assertEqual(analyze.act_tool(tap), 500)
        tool["description"] = "changed wording"
        with self.assertRaisesRegex(ValueError, "Unparseable act watch description"):
            analyze.act_tool(tap)
        with self.assertRaisesRegex(ValueError, "Missing act tool description"):
            analyze.act_tool([])

    def test_unparseable_delivery_and_wait_are_reported(self):
        for summary in ("new delivery wording", "1/1 delivered · new wait wording"):
            with self.subTest(summary=summary), self.assertRaisesRegex(ValueError, "Unparseable act"):
                analyze.split({"summary": summary, "hint": "", "seconds": 2},
                              {"actions": [{"action": "click"}]}, {}, 500)

    def test_no_time_left_to_wait_is_a_known_summary(self):
        result = analyze.split({"summary": "1/1 delivered · no time left to wait", "hint": "", "seconds": 2},
                               {"actions": [{"action": "type", "text": "a"}]}, {}, 500)
        self.assertEqual(result["wait"], 0)

    def test_plain_repeats_have_their_own_pace(self):
        self.assertEqual(analyze.pacing([{"action": "key", "keys": ["right"], "repeat": 100}]), (99, 0))
        self.assertEqual(analyze.pacing([{"action": "key", "keys": ["ctrl", "right"], "repeat": 2}]), (65, 0))

    def test_a_path_with_a_button_is_paced_as_a_drag(self):
        # 100 px: a drag's 21 steps, the press after the motion and the holds; without a button, a gap every step.
        drag = analyze.pacing([{"action": "drag", "from": [0, 0], "to": [100, 0]}])
        self.assertEqual(drag, (330, 0))
        self.assertEqual(analyze.pacing([{"action": "path", "points": [[0, 0], [100, 0]]}]), drag)
        self.assertEqual(analyze.pacing([{"action": "path", "points": [[0, 0], [100, 0]], "button": "none"}]), (210, 0))
        # Two segments of 500 px: 100 steps of DRAG_STEP_PX, the press after the motion and the holds.
        self.assertEqual(analyze.pacing([{"action": "path", "points": [[0, 0], [500, 0], [500, 500]]}]), (1120, 0))

    def test_clicks_always_wait_for_the_answer(self):
        # move, click, sync, move, click: two normal gaps and the sync's 150 ms minimum.
        self.assertEqual(analyze.pacing([{"action": "click"}, {"action": "click"}]), (350, 0))


WINDOW = 0x5F4D28D544D0  # Cua's window_id is Hyprland's address


def tap(*calls):
    """An MCP log of Cua's server: its tools, then each (tool, arguments, result) as a request and its response."""
    entries = [{"t": 0, "dir": "response", "msg": {"id": 0, "result": {"tools": [{"name": "get_window_state"},
                                                                                  {"name": "run_steps"}]}}}]
    for n, (tool, args, result) in enumerate(calls, 1):
        entries.append({"t": n, "dir": "request",
                        "msg": {"id": n, "method": "tools/call", "params": {"name": tool, "arguments": args}}})
        entries.append({"t": n + 0.5, "dir": "response", "msg": {"id": n, "result": result}})
    return entries


def state(window=WINDOW, width=1912, height=1046, shot=(1449, 792)):
    bounds = {"x": 4, "y": 30, "width": width, "height": height}
    return {"structuredContent": {"window_id": window, "window_bounds": bounds, "screenshot_width": shot[0],
                                  "screenshot_height": shot[1]}}


class CuaCalls(unittest.TestCase):
    def test_points_map_through_the_windows_latest_screenshot_and_a_failed_step_ends_the_batch(self):
        steps = {"window_id": WINDOW, "pid": 7, "steps": [
            {"tool": "drag", "args": {"from_x": 100, "from_y": 50, "to_x": 400, "to_y": 300}},
            {"click": {"x": 10, "y": 20, "count": 2}},
            {"tool": "click", "args": {"element_token": "s1:3"}},
        ]}
        log = tap(("get_window_state", {"pid": 7, "window_id": WINDOW}, state()),
                  ("run_steps", steps, {"isError": True, "structuredContent": {"failed_step": 1, "steps": [{}]},
                                        "content": [{"type": "text", "text": "step 2 of 3 failed"}]}),
                  ("get_window_state", {"pid": 7, "window_id": WINDOW}, state(shot=(956, 523))),
                  ("right_click", {"pid": 7, "window_id": WINDOW, "x": 478, "y": 261}, {}))
        calls = actlog.calls(log)
        self.assertEqual(len(calls), 2)
        batch, click = calls
        self.assertEqual(batch["window"], f"0x{WINDOW:x}")
        self.assertEqual([a["action"] for a in batch["actions"]], ["drag", "click", "click by element"])
        self.assertEqual(batch["result"]["actions"]["done"], 1)
        shot = batch["shots"][batch["args"]["shot"]]
        self.assertEqual(actlog.to_window(shot, batch["actions"][0]["from"]), (100 * 1912 / 1449, 50 * 1046 / 792))
        # The presses the page should see: the drag's start alone, as the double click did not run.
        self.assertEqual([i for i, _ in actlog.presses(batch, batch["window"])], [0])
        # A later read with another screenshot size is the frame of the calls after it.
        later = click["shots"][click["args"]["shot"]]
        self.assertEqual(actlog.to_window(later, (478, 261)), (478 * 1912 / 956, 261 * 1046 / 523))
        self.assertEqual((click["actions"][0]["button"], click["result"]["actions"]["done"]), ("right", 1))

    def test_an_action_whose_delivery_cua_does_not_know_counts_as_sent(self):
        # As a foreground drag that started the page's drag-and-drop before the plugin refused the rest of it.
        unknown = ("run_steps: step 1 of 1 failed; 0 step(s) ran before it, 0 not run\n1. drag ERROR (action): "
                   "foreground_unavailable: foreground activation or input may have started; final input delivery is "
                   "unknown")
        refused = "run_steps: step 1 of 1 failed\n1. drag ERROR (action): foreground_unavailable (primary_target_busy)"
        drag = {"tool": "drag", "args": {"from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}}
        batch = {"pid": 7, "window_id": WINDOW, "delivery_mode": "foreground", "steps": [drag]}
        calls = actlog.calls(tap(*[("run_steps", batch, {"isError": True, "structuredContent": {"failed_step": 0},
                                                         "content": [{"type": "text", "text": text}]})
                                   for text in (unknown, refused)]))
        self.assertEqual([c["result"]["actions"]["done"] for c in calls], [1, 0])

    def test_points_in_a_zoom_or_before_any_screenshot_are_not_mapped(self):
        drag = {"pid": 7, "window_id": WINDOW, "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}
        first, zoomed = actlog.calls(tap(("drag", drag, {}),
                                         ("get_window_state", {"pid": 7, "window_id": WINDOW}, state()),
                                         ("drag", {**drag, "from_zoom": True}, {})))
        for call, why in ((first, "no get_window_state screenshot of the window before it"),
                          (zoomed, "its points are in a zoom")):
            self.assertEqual(call["shots"], {})
            self.assertEqual(call["unknown"], {call["args"]["shot"]: why})

    def test_a_run_with_hyprmasqs_act_tool_is_read_as_act_calls(self):
        log = [{"t": 0, "dir": "response", "msg": {"id": 0, "result": {"tools": [{"name": "act"}]}}}]
        with mock.patch.object(actlog, "act_calls", return_value=["act"]) as act_calls:
            self.assertEqual(actlog.calls(log), ["act"])
        act_calls.assert_called_once_with(log)


class Refusals(unittest.TestCase):
    def test_a_calls_refusal_codes_and_actions_are_counted(self):
        text = ("run_steps: step 2 of 2 failed. press_key ERROR (action): "
                "foreground_unavailable (primary_target_busy): foreground_grab\n"
                "background_unavailable (client_not_qualified): client_not_qualified")
        steps = {"steps": [{"tool": "click", "args": {}}, {"press_key": {"key": "a"}}]}
        [call] = report.tool_calls(tap(("run_steps", steps, {"isError": True,
                                                             "content": [{"type": "text", "text": text}]})))
        self.assertEqual(call["refusals"], ["background_unavailable", "client_not_qualified", "foreground_grab",
                                            "foreground_unavailable", "primary_target_busy"])
        self.assertEqual((call["actions"], call["error"]), (["click", "press_key"], True))
        [read] = report.tool_calls(tap(("get_window_state", {}, {"content": [{"type": "text", "text": "ok"}]})))
        self.assertEqual((read["actions"], read["refusals"]), ([], []))



def server_tap(tools, *calls):
    """An MCP log of a server that listed tools, then each (tool, arguments, result) at host times 10, 20, …"""
    entries = [{"t": 0, "dir": "response", "msg": {"id": 0, "result": {"tools": [{"name": t} for t in tools]}}}]
    for n, (tool, args, result) in enumerate(calls, 1):
        entries.append({"t": 10 * n, "dir": "request",
                        "msg": {"id": n, "method": "tools/call", "params": {"name": tool, "arguments": args}}})
        entries.append({"t": 10 * n + 1, "dir": "response", "msg": {"id": n, "result": result}})
    return entries


def text(value, error=False):
    return {"isError": error, "content": [{"type": "text", "text": value if isinstance(value, str)
                                           else json.dumps(value)}]}


TASK, USER = "0x5862141559d0", "0x5862131b6c10"


class ScreenTools(unittest.TestCase):
    """hypruse's and computer-use-linux's runs, whose pointers act at points on the screen."""

    def run_dir(self, root, focus_at=15):
        """A run whose task window is at 100,50 on its workspace, and which the agent focused at host time focus_at:
        the VM's clock is 1000 s behind the host's."""
        run = os.path.join(root, "run")
        os.makedirs(run)
        with open(os.path.join(root, "source.json"), "w") as f:
            json.dump({"display": "1920x1080@1"}, f)
        with open(os.path.join(run, "ready.json"), "w") as f:
            json.dump({"host_time": 2000, "vm_time": 1000, "task_window": TASK,
                       "user": {"workspace": 1, "window": USER},
                       "clients": [{"address": USER, "at": [0, 0], "size": [1920, 1080]},
                                   {"address": TASK, "at": [100, 50], "size": [1800, 1000]}]}, f)
        with open(os.path.join(run, "vm-events.log"), "w") as f:
            f.write(f"{focus_at - 1000:.3f} activewindowv2>>{TASK[2:]}\n")
        return run

    def test_hypruse_points_land_on_the_focused_window_where_it_is(self):
        log = server_tap(("click_ui", "sequence", "pointer"),
                         ("pointer", {"action": "click", "x": 300, "y": 200}, text("click ok; cursor now at (300, 200)")),
                         ("pointer", {"action": "drag", "x": 300, "y": 200, "to_x": 700, "to_y": 600},
                          text("drag ok; cursor now at (700, 600)")),
                         ("sequence", {"steps": [{"op": "click_ui", "name": "Amber", "window": TASK},
                                                 {"op": "pointer", "action": "click", "x": 10, "y": 20},
                                                 {"op": "pointer", "action": "click", "x": 30, "y": 40}]},
                          text("sequence: stopped after 2/3 steps, step 1 raised: no\n[0] click_ui Amber: pressed\n"
                               "[1] pointer: ERROR no")))
        with tempfile.TemporaryDirectory() as root:
            first, drag, batch = actlog.calls(log, self.run_dir(root))
        # Before the focus moved, the click went to the user's window; after, to the task's, from where it is.
        self.assertEqual((first["window"], drag["window"]), (USER, TASK))
        shot = drag["shots"][drag["args"]["shot"]]
        self.assertEqual(actlog.to_window(shot, drag["actions"][0]["from"]), (200, 150))
        self.assertEqual([a["action"] for a in batch["actions"]], ["click_ui by element", "click", "click"])
        self.assertEqual(batch["result"]["actions"]["done"], 1)
        self.assertEqual([i for i, _ in actlog.presses(batch, TASK)], [])

    def test_a_pointer_acts_on_the_window_that_took_the_focus_during_its_call(self):
        # A sequence focuses the task window, drags there and gives the focus back to the user, all within its call.
        log = server_tap(("click_ui", "sequence", "pointer"),
                         ("sequence", {"steps": [{"op": "hypr", "command": "focus_window"},
                                                 {"op": "pointer", "action": "drag", "x": 300, "y": 200, "to_x": 700,
                                                  "to_y": 600}]},
                          text("sequence: all 2/2 steps ran\n[0] hypr focus_window: ok\n[1] pointer: drag ok")))
        with tempfile.TemporaryDirectory() as root:
            run = self.run_dir(root, focus_at=10.3)
            with open(os.path.join(run, "vm-events.log"), "a") as f:
                f.write(f"{10.8 - 1000:.3f} activewindowv2>>{USER[2:]}\n")
            (batch,) = actlog.calls(log, run)
        self.assertEqual(batch["window"], TASK)
        self.assertEqual(actlog.to_window(batch["shots"][batch["args"]["shot"]], batch["actions"][1]["from"]),
                         (200, 150))

    def test_computer_use_linux_points_are_capture_pixels_or_the_windows_crop(self):
        place = {"windows": [{"window_id": int(TASK, 16), "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080}}]}
        log = server_tap(("get_app_state", "click", "drag"),
                         ("list_windows", {}, text(place)),
                         ("click", {"x": 30, "y": 40, "relative": True, "window_id": int(TASK, 16)},
                          text({"ok": True, "message": "Clicked."})),
                         ("drag", {"start_x": 5, "start_y": 6, "end_x": 7, "end_y": 8},
                          text({"ok": False, "message": "Coordinate input was not sent: no pointer"})),
                         ("click", {"element_index": 4}, text({"ok": True})))
        with tempfile.TemporaryDirectory() as root:
            click, drag, element = actlog.calls(log, self.run_dir(root, focus_at=25))
        self.assertEqual((click["window"], drag["window"]), (TASK, TASK))
        self.assertEqual(actlog.to_window(click["shots"][click["args"]["shot"]], (30, 40)), (30, 40))
        self.assertEqual([c["result"]["actions"]["done"] for c in (click, drag, element)], [1, 0, 1])
        self.assertEqual(element["actions"], [{"action": "click by element"}])

    def test_their_actions_and_refusals_are_counted(self):
        refused = ("Error executing tool pointer: (300, 200) is on foot (0x1), not the window the human is in, and the "
                   "pointer would move their cursor and take their focus, so it cannot be done in the background: "
                   "pass background=false")
        hyp = report.tool_calls(server_tap(("click_ui",), ("pointer", {"action": "drag"}, text(refused, True)),
                                           ("sequence", {"steps": [{"op": "keyboard", "action": "type"},
                                                                   {"op": "wait_for"}, {"op": "click_ui"}]},
                                            text("sequence: all 3/3 steps ran")),
                                           ("desktop", {}, text("{}"))))
        self.assertEqual([(c["actions"], c["refusals"]) for c in hyp],
                         [(["pointer drag"], ["background"]), (["keyboard type", "click_ui"], []), ([], [])])
        cul = report.tool_calls(server_tap(("get_app_state",),
                                           ("drag", {}, text({"ok": False, "message": "Coordinate input was not sent: "
                                                                                       "ydotool cannot position"})),
                                           ("screenshot", {}, text({"ok": True}))))
        self.assertEqual([(c["actions"], c["refusals"]) for c in cul], [(["drag"], ["not_sent"]), ([], [])])


if __name__ == "__main__":
    unittest.main()
