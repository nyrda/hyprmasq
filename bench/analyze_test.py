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


class ClaudeDesktopRun(unittest.TestCase):
    """A run of Claude's computer use, read from the Claude app's Code session transcript."""

    def entry(self, kind, at, content, message_id=None, usage=None):
        message = {"role": kind, "content": content}
        if kind == "assistant":
            message.update(id=message_id, model="claude-sonnet-5-5", usage=usage,
                           stop_reason="end_turn" if message_id == "m3" else "tool_use")
        return {"type": kind, "timestamp": f"2026-10-31T10:00:{at:02d}.000Z", "message": message}

    def test_usage_once_a_message_and_the_approval_wait_left_out(self):
        usage = {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 100,
                 "output_tokens": 50}
        transcript = [
            self.entry("user", 0, "You are helping Mara Lindqvist"),
            # A message's content blocks are entries of their own, with the same usage.
            self.entry("assistant", 2, [{"type": "text", "text": "Asking for access."}], "m1", usage),
            self.entry("assistant", 2, [{"type": "tool_use", "id": "t1", "name": "mcp__computer-use__request_access",
                                         "input": {}}], "m1", usage),
            self.entry("user", 12, [{"type": "tool_result", "tool_use_id": "t1", "content": "granted"}]),
            self.entry("assistant", 13, [{"type": "tool_use", "id": "t2", "name": "mcp__computer-use__app_click",
                                          "input": {}}], "m2", dict(usage, cache_read_input_tokens=2000)),
            self.entry("user", 15, [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]),
            self.entry("assistant", 16, [{"type": "text", "text": "Done"}], "m3", usage),
        ]
        start = laptop_analyze.iso_time(transcript[0]["timestamp"])
        summary = {"backend": "claude-cu", "rep": 1, "task": "files", "platform": "macos",
                   "harness": "claude-desktop", "model": "claude-sonnet-5-5", "user_app": "Claude",
                   "agent": {"start": start, "end": start + 16, "timed_out": False}, "wall_seconds": 16.0,
                   "passed": True, "failures": [], "check": {"checks": {}}}
        with tempfile.TemporaryDirectory() as root:
            Path(root, "summary.json").write_text(json.dumps(summary))
            Path(root, "transcript.jsonl").write_text("\n".join(map(json.dumps, transcript)) + "\n")
            Path(root, "front.jsonl").write_text(json.dumps({"time": start, "front": "Claude"}) + "\n")
            Path(root, "windows.jsonl").write_text(json.dumps({"time": start, "windows": []}) + "\n")
            run = laptop_analyze.run_metrics(root)
        self.assertEqual(run["usage"]["input_tokens"], 1110 + 2110 + 1110)
        self.assertEqual(run["usage"]["uncached_input_tokens"], 110 * 3)
        self.assertEqual(run["context"]["peak_input_tokens"], 2110)
        # Three requests at Sonnet 5.5's list prices: 30 input, 300 5-minute cache writes, 4000 cache reads, 150 output.
        self.assertAlmostEqual(run["usage"]["cost_usd"], (30 * 2 + 300 * 2.5 + 4000 * 0.1 + 150 * 10) / 1e6)
        self.assertEqual(run["calls"], 2)
        self.assertEqual(run["approval_seconds"], 10.0)
        self.assertEqual(run["wall_seconds"], 6.0)
        self.assertEqual(run["tool_seconds"], 2.0)
        self.assertEqual(run["final"], '"Done"')
        self.assertEqual(run["by_tool"]["app_click"], {"calls": 1, "errors": 0})
        self.assertEqual(run["context"]["compactions"], 0)

    def test_a_compacted_session_counts_its_compactions(self):
        usage = {"input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 0,
                 "output_tokens": 5}
        transcript = [self.entry("user", 0, "You are helping Mara Lindqvist"),
                      self.entry("assistant", 1, [{"type": "text", "text": "a"}], "m1", usage),
                      {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-31T10:00:02.000Z"},
                      self.entry("assistant", 3, [{"type": "text", "text": "b"}], "m3", usage)]
        self.assertEqual(laptop_analyze.claude_transcript_usage(transcript)[2]["compactions"], 1)


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


class CuaResults(unittest.TestCase):
    """How a run of Cua's counts: by what its results say of themselves, with its server known from the tools it lists."""

    def calls(self, *results):
        return report.tool_calls(server_tap(["run_steps"], *results))

    def test_a_hint_that_names_a_route_is_no_refusal_and_a_refused_call_counts_once(self):
        worked = ("click", {}, {"content": [{"type": "text", "text": "Done. background_unavailable is possible later."}],
                                "structuredContent": {"effect": "unverifiable"}})
        refused = ("click", {}, {"isError": True, "content": [{"type": "text", "text": (
            "foreground_unavailable (primary_target_busy): foreground_grab")}], "structuredContent": {"effect": "refused"}})
        setup = ("browser_prepare", {}, {"content": [{"type": "text", "text": "refused (browser_consent_required): needs a grant"}],
                                         "structuredContent": {"refusal": {"code": "browser_consent_required"}}})
        partial = ("drag", {}, {"isError": True, "content": [{"type": "text", "text": "foreground_unavailable: may have started"}],
                                "structuredContent": {"effect": "partial"}})
        guide = ("get_guide", {}, {"content": [{"type": "text", "text": "Refusals such as background_unavailable mean ..."}]})
        found = self.calls(worked, refused, setup, partial, guide)
        self.assertEqual([c["refused"] for c in found], [False, True, True, True, False])
        self.assertEqual(found[1]["refusals"], ["foreground_grab", "foreground_unavailable", "primary_target_busy"])
        self.assertEqual([c["refusal_kind"] for c in found], [None, "route", "setup", "partial", None])

    def test_the_operations_of_a_script_count_as_actions_and_its_reads_do_not(self):
        script = ("run_script", {"script": "..."}, {"content": [{"type": "text", "text": "run_script: ok"}], "structuredContent": {
            "calls": [{"op": "getApp"}, {"op": "click", "ok": True}, {"op": "query"}, {"op": "drag", "ok": True}]}})
        self.assertEqual(self.calls(script)[0]["actions"], ["click", "drag"])

    def test_walks_are_split_out_by_why_they_stopped_and_a_scripts_count_once(self):
        full = ("get_window_state", {"timeout_ms": 5000}, {"content": [{"type": "text", "text": "window_id=1 elements=28 walk_ms=1000 snapshot_id=s1"}]})
        # Cua's own words for the three cuts: only the time budget's is the machine's.
        cut = ("run_steps", {}, {"content": [{"type": "text", "text": (
            "1/1 steps ok\nwindow_id=1 elements=9 walk_ms=3000 snapshot_id=s2\n\u26a0\ufe0f PARTIAL TREE: the 1000 ms "
            "timeout_ms budget ran out after 9 node(s)\nwindow_id=1 elements=67 walk_ms=500\n\u26a0\ufe0f PARTIAL TREE: the "
            "max_elements node budget ran out after 67 node(s)")}]})
        grid = ("get_window_state", {}, {"content": [{"type": "text", "text": (
            "window_id=2 elements=365 walk_ms=900\n\u26a0\ufe0f PARTIAL TREE: a container with more children than can be "
            "enumerated (e.g. a spreadsheet's cell grid) was not expanded")}]})
        # A script's walk is in its log and again in the state it returned; its own timeout bounds the script.
        script = ("run_script", {"script": "return getState(w)", "timeout_ms": 60000}, {
            "content": [{"type": "text", "text": "run_script: ok\nreturn value: window_id=1 elements=28 walk_ms=700"}],
            "structuredContent": {"calls": [{"op": "get_window_state", "ok": True, "detail": "window_id=1 elements=28 walk_ms=700"}]}})
        self.assertEqual(report.walk_split(self.calls(full, cut, grid, script)),
                         {"seconds": 6.1, "count": 5, "partial": 1, "partial_elements": 1, "partial_container": 1,
                          "median_ms": 900, "budget_named": 1, "first_ms": 1000, "first_partial": False})
        named = ("run_script", {"script": "getState(w, {timeout_ms: 8000})", "timeout_ms": 60000}, script[2])
        self.assertTrue(self.calls(named)[0]["walk_budget_named"])
        self.assertIsNone(report.walk_split(report.tool_calls(server_tap(["act"], full))))

    def test_a_failed_call_is_a_refusal_by_its_status_and_words_and_otherwise_an_error(self):
        uncoded = ("click", {}, {"isError": True, "content": [{"type": "text", "text": "no native Wayland toplevel for pid 12"}]})
        error = ("click", {}, {"isError": True, "content": [{"type": "text", "text": "no element [9] in the last state"}]})
        caught = ("run_script", {"script": "..."}, {"content": [{"type": "text", "text": "run_script: ok"}], "structuredContent": {
            "calls": [{"op": "drag", "ok": False, "detail": "foreground_unavailable (primary_target_busy)"},
                      {"op": "drag", "ok": True}]}})
        found = self.calls(uncoded, error, caught)
        self.assertEqual([(c["refused"], c["refusal_kind"]) for c in found],
                         [(True, "route_uncoded"), (False, "error"), (False, None)])
        self.assertEqual(found[2]["script_refusals"], 1)

    def test_a_batch_says_how_many_of_its_actions_were_asked_for_sent_and_done(self):
        steps = ("run_steps", {"steps": [{"click": {"x": 1, "y": 1}}, {"press_key": {"key": "a"}}, {"click": {"x": 2, "y": 2}}]},
                 {"isError": True, "content": [{"type": "text", "text": "run_steps: step 2 of 3 failed"}],
                  "structuredContent": {"total": 3, "executed": 2, "failed_step": 1}})
        script = ("run_script", {"script": "..."}, {"content": [{"type": "text", "text": "run_script: ok"}], "structuredContent": {
            "calls": [{"op": "getApp"}, {"op": "click", "ok": True}, {"op": "drag", "ok": False}]}})
        self.assertEqual([(c["requested"], c["attempted"], c["done"]) for c in self.calls(steps, script)], [(3, 2, 1), (2, 2, 1)])
        act = report.tool_calls(server_tap(["act"], ("act", {"actions": [["click", 1, 1], ["pause", 200], ["type", "x"]]}, {
            "content": [{"type": "text", "text": "ok"}], "structuredContent": {"actions": {"done": 3, "total": 3}}})))
        self.assertEqual((act[0]["requested"], act[0]["attempted"], act[0]["done"]), (2, 2, 2))


class UserAndFrames(unittest.TestCase):
    """What a run did to the user beside the focus, and what its screenshots say of a window's frame."""

    def test_desktop_input_while_the_user_holds_the_focus_goes_to_the_users_window(self):
        with tempfile.TemporaryDirectory() as run_dir:
            # The focus goes to the task window at 20 s and back to the user's at 30 s, VM time 1000 s behind.
            with open(os.path.join(run_dir, "vm-events.log"), "w") as f:
                f.write(f"{20 - 1000:.3f} activewindowv2>>{TASK[2:]}\n{30 - 1000:.3f} activewindowv2>>{USER[2:]}\n")
            ready = {"user": {"workspace": 1, "window": USER}}
            calls = [{"tool": "drag", "start": 15, "end": 16, "desktop_steps": [("drag", "input")]},
                     {"tool": "hotkey", "start": 17, "end": 18, "desktop_steps": [("hotkey", "shortcut")]},
                     {"tool": "drag", "start": 25, "end": 26, "desktop_steps": [("drag", "input")]},
                     {"tool": "click", "start": 35, "end": 36, "desktop_steps": []}]
            user = report.user_disturbance(run_dir, ready, 10, 40, 1000, calls, ({"x": 960, "y": 540}, {"x": 113, "y": 299}))
        self.assertEqual(user["desktop_input_to_user_window"], [{"at": 5.0, "tool": "drag"}])
        self.assertEqual(user["desktop_shortcuts"], [{"at": 7.0, "tool": "hotkey"}])
        self.assertEqual(user["cursor_displacement_px"], 881)
        self.assertFalse(user["no_view_change"])

    def test_only_desktop_input_that_cua_delivered_counts_and_its_compositor_shortcuts_apart(self):
        desktop = {"target": {"kind": "desktop", "display_id": "primary"}}
        cases = [
            # A batch rejected before it ran anything sent nothing.
            ("run_steps", {"steps": [{"tool": "hotkey", "args": {**desktop, "keys": ["alt", "tab"]}}], "observe": {}},
             {"isError": True, "content": [{"type": "text", "text": "run_steps rejected before running anything: observe"}],
              "structuredContent": {"ok": False, "executed": 0}}, []),
            # A drag whose points could not be mapped sent nothing.
            ("drag", {"scope": "desktop", "from_x": 1, "from_y": 1, "to_x": 2, "to_y": 2},
             {"isError": True, "content": [{"type": "text", "text": "desktop-frame coordinates could not be mapped into window 9"}]}, []),
            # Workspace and window switches are the compositor's binds; a key the binds do not take is input.
            ("hotkey", {"scope": "desktop", "keys": ["super", "2"]},
             {"content": [{"type": "text", "text": "Pressed desktop hotkey super+2."}]}, [("hotkey", "shortcut")]),
            ("hotkey", {**desktop, "keys": ["ctrl", "a"]},
             {"content": [{"type": "text", "text": "Pressed desktop hotkey ctrl+a."}]}, [("hotkey", "input")]),
            # A batch counts the steps before the one that failed, and not that one or those after it.
            ("run_steps", {"steps": [{"tool": "click", "args": {**desktop, "x": 1, "y": 1}},
                                     {"tool": "click", "args": {**desktop, "x": 2, "y": 2, "modifier": ["shift"]}},
                                     {"tool": "drag", "args": {**desktop, "from_x": 1, "from_y": 1, "to_x": 2, "to_y": 2}}]},
             {"isError": True, "content": [{"type": "text", "text": "run_steps: step 2 of 3 failed"}],
              "structuredContent": {"ok": False, "total": 3, "executed": 2, "failed_step": 1}}, [("click", "input")]),
            ("drag", {"scope": "desktop", "from_x": 225, "from_y": 1060, "to_x": 240, "to_y": 670},
             {"content": [{"type": "text", "text": "Dragged on the desktop."}], "structuredContent": {"effect": "unverifiable"}},
             [("drag", "input")]),
        ]
        for tool, args, result, expected in cases:
            with self.subTest(tool=tool, args=args):
                self.assertEqual(report.tool_calls(server_tap(["run_steps"], (tool, args, result)))[0]["desktop_steps"], expected)

    def test_a_small_first_frame_and_a_frame_unchanged_after_an_input_are_flagged(self):
        # A call that names no window, as a desktop state or act's crop of a change, is no window's frame.
        calls = [{"window": None, "image_hashes": ["d"], "image_bytes": 900000, "done": 0},
                 {"window": "7", "image_hashes": ["a"], "image_bytes": 30000, "done": 0},
                 {"window": None, "image_hashes": ["c"], "image_bytes": 1000, "done": 1},
                 {"window": "7", "done": 1},
                 {"window": "7", "image_hashes": ["b"], "image_bytes": 200000, "done": 0},
                 {"window": "7", "done": 2},
                 {"window": "7", "image_hashes": ["b"], "image_bytes": 200000, "done": 0}]
        self.assertEqual(report.frames(calls), {"shots": 5, "first_to_later": 0.15, "first_small": True,
                                                "unchanged_after_input": 1})
        drawn = [{"window": None, "image_hashes": ["d"], "image_bytes": 2700000, "done": 0},
                 {"window": "7", "image_hashes": ["a"], "image_bytes": 1600000, "done": 0},
                 {"window": None, "image_hashes": ["d2"], "image_bytes": 2700000, "done": 0},
                 {"window": "7", "image_hashes": ["b"], "image_bytes": 1600000, "done": 0}]
        self.assertFalse(report.frames(drawn)["first_small"])

    def test_a_scripts_reads_by_their_logged_names_are_no_actions(self):
        structured = {"calls": [{"op": "get_app", "ok": True}, {"op": "get_window_state", "ok": True}, {"op": "find"},
                                {"op": "click", "ok": True}]}
        self.assertEqual(report.call_actions("run_script", {}, "cua", structured), ["click"])

    def test_an_act_result_in_text_alone_counts_as_its_structured_content_would(self):
        args = {"actions": [["click", 1, 2], ["type", "x"]]}
        actions = report.call_actions("act", args, "hyprmasq", {})
        partial = "1/2 delivered, delivery partial: actions[1] (type) may be partly applied · shot s3 1280×720"
        self.assertEqual(report.call_progress("act", args, "hyprmasq", {}, actions, True, partial), (2, 2, 1))
        held = "1/2 delivered, scroll held back · shot s3 1280×720"
        self.assertEqual(report.call_progress("act", args, "hyprmasq", {}, actions, False, held), (2, 1, 1))
        self.assertEqual(report.call_progress("act", args, "hyprmasq", {}, actions, True, "0/2 delivered, nothing sent"),
                         (2, 0, 0))

    def test_an_action_held_back_was_not_attempted(self):
        args = {"actions": [["click", 1, 2], ["scroll", "down", 3]]}
        actions = report.call_actions("act", args, "hyprmasq", {})
        structured = {"actions": {"done": 1, "total": 2, "delivery": "partial", "failed_index": 1, "held_back": "a popup"}}
        self.assertEqual(report.call_progress("act", args, "hyprmasq", structured, actions, False), (2, 1, 1))

    def test_a_frame_is_judged_by_the_input_before_it(self):
        # An act that changed A to B returns B: a later screenshot of B with no input between is not unchanged.
        changed = [{"window": "7", "image_hashes": ["a"], "image_bytes": 100, "done": 0},
                   {"window": "7", "image_hashes": ["b"], "image_bytes": 100, "done": 1},
                   {"window": "7", "image_hashes": ["b"], "image_bytes": 100, "done": 0}]
        self.assertEqual(report.frames(changed)["unchanged_after_input"], 0)
        # An act whose own frame came back the same as before it is.
        same = [{"window": "7", "image_hashes": ["a"], "image_bytes": 100, "done": 0},
                {"window": "7", "image_hashes": ["a"], "image_bytes": 100, "done": 1}]
        self.assertEqual(report.frames(same)["unchanged_after_input"], 1)

    def test_act_crops_of_a_named_window_count_as_input_and_not_as_its_frames(self):
        # hyprmasq's act names its window but returns crops of what changed: a larger crop does not make the first frame
        # look small, and the whole frame after it is compared with the whole frame before.
        calls = [{"window": "7", "image_hashes": ["a"], "image_bytes": 200000, "done": 0, "whole_frame": True},
                 {"window": "7", "image_hashes": ["crop"], "image_bytes": 900000, "done": 1, "whole_frame": False},
                 {"window": "7", "image_hashes": ["a"], "image_bytes": 200000, "done": 0, "whole_frame": True}]
        frames = report.frames(calls)
        self.assertFalse(frames["first_small"])
        self.assertEqual(frames["unchanged_after_input"], 1)


class InfrastructureFailures(unittest.TestCase):
    """What allows a rerun: a limit, a login or the provider, from where the harness reports them, and not the agent's words."""

    def failure(self, stream, stderr="", exit_code=1):
        with tempfile.TemporaryDirectory() as run_dir:
            with open(os.path.join(run_dir, "agent.stderr"), "w") as f:
                f.write(stderr)
            return report.infrastructure_failure(run_dir, [{"line": line} for line in stream], {"agent_exit": exit_code})

    def test_a_rejected_rate_limit_event_says_when_the_limit_ends(self):
        found = self.failure([{"type": "rate_limit_event", "rate_limit_info": {
            "status": "rejected", "rateLimitType": "seven_day", "resetsAt": 123}}])
        self.assertEqual((found["kind"], found["resets_at"]), ("rate_limit", 123))

    def test_the_harnesses_own_errors_are_told_apart(self):
        self.assertEqual(self.failure([{"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}])["kind"],
                         "rate_limit")
        self.assertEqual(self.failure([{"type": "result", "is_error": True, "result": "Please run /login"}])["kind"], "auth")
        self.assertEqual(self.failure([], "stream disconnected before completion")["kind"], "provider")

    def test_a_login_that_could_not_be_refreshed_is_the_infrastructures(self):
        # Claude Code's own result when its login failed, as a run with no tool call ended on a machine whose login broke.
        found = self.failure([{"type": "result", "is_error": True, "subtype": "success", "terminal_reason": "api_error",
                               "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}])
        self.assertEqual(found["kind"], "auth")
        # An agent that only wrote of a failed sign-in in the task's page did not fail for the login.
        self.assertIsNone(self.failure([{"type": "result", "is_error": False,
                                         "result": "The form said: failed to authenticate"}], exit_code=0))

    def test_an_agent_that_only_read_of_a_limit_and_a_warning_are_no_failure(self):
        self.assertIsNone(self.failure([
            {"type": "assistant", "message": {"content": "The page says: you've hit your usage limit."}},
            {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "utilization": 0.9}},
            {"type": "result", "is_error": False, "result": "Done"}], exit_code=0))


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

    def test_the_refusals_of_cuas_admission_by_capability_are_counted(self):
        for text, codes in (
            ("background_unavailable (client_not_bound)", ["background_unavailable", "client_not_bound"]),
            ("background_unavailable (client_drops_background_keys)",
             ["background_unavailable", "client_drops_background_keys"]),
            ("background_unavailable (client_xwayland)", ["background_unavailable", "client_xwayland"]),
            ("background_unavailable (client_unattested)", ["background_unavailable", "client_unattested"]),
        ):
            [call] = report.tool_calls(tap(("click", {}, {"isError": True,
                                                          "content": [{"type": "text", "text": text}]})))
            self.assertEqual(call["refusals"], codes)

    def test_the_foreground_fallback_option_in_a_hint_is_no_refusal(self):
        text = 'To run it, set "foreground_fallback":true on run_steps. foreground_unavailable: foreground_grab'
        [call] = report.tool_calls(tap(("run_steps", {}, {"isError": True,
                                                          "content": [{"type": "text", "text": text}]})))
        self.assertEqual(call["refusals"], ["foreground_grab", "foreground_unavailable"])
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
