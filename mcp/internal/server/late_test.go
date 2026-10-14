package server

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// lateWindow adds a window that maps after the act on its source has returned, as a portal's file chooser did 0.7 s
// after the click's act.
func (e *testEnv) lateWindow(address, stableID, title string, source *hypr.Source) *fakeWindow {
	return e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: address, StableID: stableID, Class: "test.app", Title: title, PID: 4242,
		Workspace: hypr.Workspace{ID: 5, Name: "mail"}}, Width: 100, Height: 100, Source: source})
}

func (e *testEnv) windows() *mcp.CallToolResult {
	return mirrored(e.t, e.server.windowsTool(context.Background(), &call{args: json.RawMessage(`{}`)}))
}

// windowEntry returns the windows entry of the window, and the line that describes it.
func windowEntry(t *testing.T, res *mcp.CallToolResult, address string) (map[string]any, string) {
	t.Helper()
	windows, _ := get(t, res, "windows").([]any)
	for i, w := range windows {
		if entry := w.(map[string]any); entry["window"] == address {
			return entry, strings.Split(text(res), "\n")[i]
		}
	}
	t.Fatalf("no window %s in %s", address, text(res))
	return nil, ""
}

// A window that the input of an act opens after the act has returned is reported by the session's next screenshot,
// with a shot, and windows marks it until then. Windows that the user's input opened, or that came from other
// windows, are not.
func TestLateWindowInTheNextScreenshot(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`); res.IsError || get(t, res, "late_windows") != nil {
		t.Fatal(text(res))
	}
	e.lateWindow("0xd1", "d1", "Open Files", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	e.lateWindow("0xd2", "d2", "Theirs", &hypr.Source{Window: "0xa1", StableID: "1a", Reason: "user_input"})
	e.lateWindow("0xd3", "d3", "Other", &hypr.Source{Window: "0xb2", StableID: "2b", Placed: true})
	e.lateWindow("0xd4", "d4", "Unrelated", nil)

	listing := e.windows()
	entry, line := windowEntry(t, listing, "0xd1")
	if entry["opened_by_input"] != "0xa1" || !strings.Contains(line, " · opened by your input to 0xa1, not yet reported") {
		t.Errorf("windows: %v %q", entry, line)
	}
	for _, address := range []string{"0xa1", "0xd2", "0xd3", "0xd4"} {
		if entry, line := windowEntry(t, listing, address); entry["opened_by_input"] != nil || strings.Contains(line, "opened by") {
			t.Errorf("windows marks %s: %q", address, line)
		}
	}
	// windows opens no socket on it, and reports it to nobody.
	if e.fake.find("0xd1").Socket {
		t.Error("windows opened the late window's socket")
	}

	res := e.screenshot(`{"window":"0xa1"}`)
	lines := strings.Split(text(res), "\n")
	if res.IsError || len(lines) != 3 || !strings.HasSuffix(lines[0], ` · window 0xa1 320×200 · late window 0xd1 (test.app "Open Files", same pid) on workspace mail (hidden)`) ||
		lines[1] != "shot s4 200×200 of late window 0xd1" || lines[2] != `Your input to 0xa1 in an earlier act opened window 0xd1 "Open Files" after that act had returned; it is in shot s4.` {
		t.Fatalf("screenshot %q", text(res))
	}
	late, _ := get(t, res, "late_windows").([]any)
	if len(late) != 1 || get(t, res, "late_windows.0.source") != "0xa1" || get(t, res, "late_windows.0.placed") != true || get(t, res, "late_windows.0.workspace") != "mail" {
		t.Errorf("late windows %v", late)
	}
	if n := len(res.Content) - 1; n != 2 {
		t.Errorf("%d images", n)
	}
	// Its shot takes coordinates at once.
	if res := e.act(`{"window":"0xd1","shot":"s4","actions":[{"action":"click","x":5,"y":5}],"wait":{"expect_change":false}}`); res.IsError {
		t.Errorf("click in the late window's shot: %s", text(res))
	}
	if e.fake.find("0xd2").Socket || e.fake.find("0xd3").Socket || e.fake.find("0xd4").Socket {
		t.Error("a window that the agent's input did not open was driven")
	}

	// It is reported once, and windows no longer marks it.
	if res := e.screenshot(`{"window":"0xa1"}`); get(t, res, "late_windows") != nil || strings.Contains(text(res), "late") {
		t.Errorf("reported again: %s", text(res))
	}
	if entry, _ := windowEntry(t, e.windows(), "0xd1"); entry["opened_by_input"] != nil {
		t.Errorf("still marked: %v", entry)
	}
}

// A late window comes with the next act of the session too, on any window, and with the next screenshot of the late
// window itself only as its shot. Another session does not report it.
func TestLateWindowInTheNextAct(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	other := e.newServer()
	e.screenshot(`{"window":"0xa1"}`)
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	e.lateWindow("0xd1", "d1", "Open Files", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	if res := callTool(other, context.Background(), screenshotOf, `{"window":"0xa1"}`); get(t, res, "late_windows") != nil {
		t.Errorf("another session reported it: %s", text(res))
	}
	res := e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["shift"]}],"wait":{"expect_change":false}}`)
	if res.IsError || get(t, res, "late_windows.0.window") != "0xd1" || get(t, res, "late_windows.0.shot.id") == nil {
		t.Fatalf("act: %s", text(res))
	}
	if windows, _ := get(t, res, "new_windows").([]any); len(windows) != 0 || !strings.Contains(text(res), ` · late window 0xd1 (test.app "Open Files", same pid) on workspace mail (hidden)`) {
		t.Errorf("act: %s", text(res))
	}

	// A late window that maps while the next act runs is late too, not a new window of that act.
	id := shotID(t, e.screenshot(`{"window":"0xb2"}`))
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	added := false
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xb2" && !added {
				added = true
				e.lateWindow("0xd5", "d5", "Print", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "redrawObserved": false, "commitsSince": 0, "quietMs": 200, "elapsedMs": 200})
		}
	})
	res = e.act(`{"window":"0xb2","shot":"` + id + `","actions":[{"action":"click","x":5,"y":5}],"wait":{"expect_change":false}}`)
	if windows, _ := get(t, res, "new_windows").([]any); res.IsError || len(windows) != 0 || get(t, res, "late_windows.0.window") != "0xd5" || get(t, res, "late_windows.0.shot.id") == nil {
		t.Errorf("act during which it mapped: %s", text(res))
	}
	e.fake.with(func() { e.fake.onWait = nil })

	// A screenshot of the late window itself reports it as its shot.
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	e.lateWindow("0xd6", "d6", "Save As", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	if res := e.screenshot(`{"window":"0xd6"}`); get(t, res, "late_windows") != nil || strings.Count(text(res), "0xd6") != 1 {
		t.Errorf("screenshot of the late window: %s", text(res))
	}
	if res := e.screenshot(`{"window":"0xa1"}`); get(t, res, "late_windows") != nil {
		t.Errorf("reported again: %s", text(res))
	}
}

// A window that comes from a target that closed is late too, by the address that the plugin names without a stable_id
// once the target is gone, but not when another window at the address is the one it came from. A window that maps
// after the watch has ended is not late.
func TestLateWindowsOfClosedAndExpiredTargets(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xa1" {
				e.fake.removeWindow("0xa1")
				return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "redrawObserved": false, "commitsSince": 0, "quietMs": 200, "elapsedMs": 200})
		}
	})
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`); res.IsError || get(t, res, "window_closed") != true {
		t.Fatal(text(res))
	}
	// Another window took the address, and one that came from it is not late.
	e.window("0xa1", "9z", 320, 200)
	e.lateWindow("0xd1", "d1", "Sending", &hypr.Source{Window: "0xa1", Placed: true})
	e.lateWindow("0xd2", "d2", "Of the new one", &hypr.Source{Window: "0xa1", StableID: "9z", Placed: true})
	res := e.screenshot(`{"window":"0xb2"}`)
	late, _ := get(t, res, "late_windows").([]any)
	if len(late) != 1 || get(t, res, "late_windows.0.window") != "0xd1" {
		t.Errorf("late windows %s", text(res))
	}

	if res := e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["ctrl","p"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	e.server.mu.Lock()
	for _, o := range e.server.openers {
		o.until = time.Now().Add(-time.Second)
	}
	e.server.mu.Unlock()
	e.lateWindow("0xd3", "d3", "Print", &hypr.Source{Window: "0xb2", StableID: "2b", Placed: true})
	if res := e.screenshot(`{"window":"0xb2"}`); get(t, res, "late_windows") != nil {
		t.Errorf("a window after the watch: %s", text(res))
	}
	if entry, _ := windowEntry(t, e.windows(), "0xd3"); entry["opened_by_input"] != nil {
		t.Errorf("marked after the watch: %v", entry)
	}
}

// A failure before any input comes with a shot, and with the late windows too, which the agent reads before it acts.
func TestLateWindowInAFailedAct(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`); res.IsError {
		t.Fatal(text(res))
	}
	e.lateWindow("0xd1", "d1", "Open Files", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5000,"y":5}]}`)
	if code := errorCode(t, res); code != "out_of_bounds" || get(t, res, "late_windows.0.window") != "0xd1" || get(t, res, "late_windows.0.shot.id") == nil {
		t.Errorf("failure: %s", text(res))
	}
	if hint, _ := get(t, res, "hint").(string); !strings.Contains(hint, `Your input to 0xa1 in an earlier act opened window 0xd1 "Open Files"`) || !strings.HasSuffix(text(res), hint) {
		t.Errorf("hint %q", hint)
	}
}

// A late window comes with a shot: a result without one for it, with capture "none", past the limit of shots, or of a
// call that was cancelled, leaves it to a later result, and windows marks it until then.
func TestLateWindowsWaitForAShot(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	for _, id := range []string{"d1", "d2", "d3", "d4"} {
		e.lateWindow("0x"+id, id, "Dialog "+id, &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	}
	none := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["shift"]}],"wait":{"expect_change":false},"capture":"none"}`)
	if none.IsError || get(t, none, "late_windows") != nil || strings.Contains(text(none), "late") {
		t.Errorf("capture none: %s", text(none))
	}
	marked := func() []string {
		var addresses []string
		for _, w := range get(t, e.windows(), "windows").([]any) {
			if entry := w.(map[string]any); entry["opened_by_input"] != nil {
				addresses = append(addresses, entry["window"].(string))
			}
		}
		return addresses
	}
	if got := marked(); len(got) != 4 {
		t.Errorf("marked %v", got)
	}
	// Nor does one that maps while such an act on another window runs.
	e.window("0xb2", "2b", 320, 200)
	added := false
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if !added {
				added = true
				e.lateWindow("0xd9", "d9", "Dialog d9", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "redrawObserved": false, "commitsSince": 0, "quietMs": 200, "elapsedMs": 200})
		}
	})
	if res := e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["shift"]}],"wait":{"expect_change":false},"capture":"none"}`); get(t, res, "late_windows") != nil {
		t.Errorf("capture none, mapped during the act: %s", text(res))
	}
	e.fake.with(func() { e.fake.onWait = nil })
	if got := marked(); len(got) != 5 {
		t.Errorf("marked %v", got)
	}
	e.fake.removeWindow("0xd9")
	res := e.screenshot(`{"window":"0xa1"}`)
	late, _ := get(t, res, "late_windows").([]any)
	if len(late) != 3 || len(res.Content) != 5 || strings.Count(text(res), " of late window ") != 3 {
		t.Errorf("three shots: %s", text(res))
	}
	if got := marked(); len(got) != 1 || got[0] != "0xd4" {
		t.Errorf("marked %v", got)
	}
	if res := e.screenshot(`{"window":"0xa1"}`); get(t, res, "late_windows.0.window") != "0xd4" || get(t, res, "late_windows.0.shot") == nil {
		t.Errorf("the fourth: %s", text(res))
	}

	// A call that is cancelled reports none.
	e.lateWindow("0xd5", "d5", "Dialog d5", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	ctx, cancel := context.WithCancel(context.Background())
	waits := len(e.fake.requestsMatching("j/hyprmasq wait"))
	go func() {
		e.waitFor(func() bool { return len(e.fake.requestsMatching("j/hyprmasq wait")) > waits })
		cancel()
	}()
	if res := callTool(e.server, ctx, actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`); get(t, res, "wait.ended") != "cancelled" || get(t, res, "late_windows") != nil {
		t.Errorf("cancelled: %s", text(res))
	}
	e.fake.with(func() { e.fake.onWait = nil })
	if res := e.screenshot(`{"window":"0xa1"}`); get(t, res, "late_windows.0.window") != "0xd5" {
		t.Errorf("after the cancelled call: %s", text(res))
	}
}

// A late window that the closed target came from is reported once, as the window to go back to.
func TestLateWindowThatAClosedWindowCameFrom(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	// The late window opened a dialog of its own, which the agent closes.
	e.lateWindow("0xd1", "d1", "Open Files", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	e.lateWindow("0xe1", "e1", "Overwrite?", &hypr.Source{Window: "0xd1", StableID: "d1", Placed: true})
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xe1" {
				e.fake.removeWindow("0xe1")
				return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "redrawObserved": false, "commitsSince": 0, "quietMs": 200, "elapsedMs": 200})
		}
	})
	res := e.act(`{"window":"0xe1","actions":[{"action":"key","keys":["enter"]}]}`)
	if res.IsError || get(t, res, "source.window") != "0xd1" || get(t, res, "source.shot") == nil || get(t, res, "late_windows") != nil || len(res.Content) != 2 {
		t.Errorf("result: %s", text(res))
	}
	if res := e.screenshot(`{"window":"0xa1"}`); get(t, res, "late_windows") != nil {
		t.Errorf("reported again: %s", text(res))
	}
}
