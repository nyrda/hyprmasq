package server

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func (e *testEnv) windowsWith(args string) *mcp.CallToolResult {
	return mirrored(e.t, e.server.windowsTool(context.Background(), &call{args: json.RawMessage(args)}))
}

// The one window that windows lists comes with its shot, which is the one screenshot would give: act takes its
// coordinates, it is the base of the next changes, a starved window gets frame callbacks and time to draw first, and a
// shot of one that did not draw says that it may be stale. Several windows come without shots, and without sockets.
func TestWindowsShotOfOneWindow(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Title, w.Starved = "Mail", true })
	e.window("0xb2", "2b", 320, 200, func(w *fakeWindow) { w.Title = "Notes" })
	if res := e.windows(); hasImage(res) || get(t, res, "windows.0.shot") != nil || get(t, res, "windows.1.shot") != nil || e.fake.find("0xa1").Socket || e.fake.find("0xb2").Socket {
		t.Fatalf("several windows: %s", text(res))
	}

	res := e.windowsWith(`{"match":"mail"}`)
	id, _ := get(t, res, "windows.0.shot.id").(string)
	if res.IsError || id != "s1" || get(t, res, "windows.0.shot.may_be_stale") != nil || get(t, res, "hint") != nil || len(res.Content) != 2 ||
		text(res) != "0xa1 · test.app \"Mail\" · workspace 1 (visible) · 640×400\nshot s1 1280×800 of window 0xa1" {
		t.Fatalf("one window: %s", text(res))
	}
	// The capture fed the starved window frame callbacks and gave it a second to draw, as screenshot does.
	if waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1"); len(waits) != 1 || waitArgsOf(t, waits[0])["expect"] != "1000" {
		t.Errorf("waits %q", waits)
	}
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`); res.IsError || !strings.Contains(text(res), "shot s2 = s1, no change") {
		t.Errorf("act in its shot: %s", text(res))
	}
	if got := e.inputs(); len(got) != 1 || got[0][0] != "move" {
		t.Errorf("inputs %q", got)
	}

	// A window that does not draw.
	e.fake.with(func() {
		w.Starved = true
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			expect, _ := strconv.Atoi(args["expect"])
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
				"commitsSince": 0, "quietMs": expect, "elapsedMs": expect, "resizing": false})
		}
	})
	res = e.windowsWith(`{"match":"mail"}`)
	stale, _ := get(t, res, "windows.0.shot.id").(string)
	if get(t, res, "windows.0.shot.may_be_stale") != true || get(t, res, "summary") != fmt.Sprintf("shot %s 1280×800 (may be stale: not redrawn within 1000 ms) of window 0xa1", stale) {
		t.Errorf("stale shot: %s", text(res))
	}
	if hint, _ := get(t, res, "hint").(string); !strings.HasPrefix(hint, "Shot "+stale+" may show an older frame") {
		t.Errorf("hint %q", hint)
	}
	e.fake.with(func() { e.fake.onWait = nil })

	// A shot that fails leaves the window listed, and says why.
	e.fake.with(func() { e.fake.onScreenshot = func(w *fakeWindow) string { return "no buffer" } })
	res = e.windowsWith(`{"match":"notes"}`)
	if res.IsError || hasImage(res) || get(t, res, "windows.0.window") != "0xb2" || get(t, res, "windows.0.shot") != nil || get(t, res, "hint") != "The shot of window 0xb2 failed: no buffer." {
		t.Errorf("failed shot: %s", text(res))
	}
}

// startWindows calls windows in the background, and returns once it has subscribed to Hyprland's events and listed the
// windows, and some more, so that what happens next happens while it waits.
func (e *testEnv) startWindows(ctx context.Context, args string) <-chan *mcp.CallToolResult {
	e.t.Helper()
	listings := len(e.fake.requestsMatching("j/clients"))
	done := make(chan *mcp.CallToolResult, 1)
	go func() { done <- e.server.windowsTool(ctx, &call{args: json.RawMessage(args)}) }()
	if !e.waitFor(func() bool { return e.fake.subscribed() == 1 && len(e.fake.requestsMatching("j/clients")) > listings }) {
		e.t.Fatal("windows did not subscribe and list the windows")
	}
	time.Sleep(350 * time.Millisecond)
	return done
}

// result waits for the result of a call that startWindows started.
func (e *testEnv) result(done <-chan *mcp.CallToolResult) *mcp.CallToolResult {
	e.t.Helper()
	select {
	case res := <-done:
		return mirrored(e.t, res)
	case <-time.After(20 * time.Second):
		e.t.Fatal("windows did not return")
	}
	return nil
}

// unsubscribed tells whether every subscription to Hyprland's events has ended.
func (e *testEnv) unsubscribed() bool {
	return e.waitFor(func() bool { return e.fake.subscribed() == 0 })
}

// With wait_ms, windows waits for a window that is not there yet: with match for one that matches, without match for one
// that the input of an act opened after the act. It lists the windows again on Hyprland's event that a window mapped,
// or with match that a title changed, and returns as soon as one is there, with the shots it would give at once.
func TestWindowsWaitsForAWindow(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Title = "Forgejo" })
	e.window("0xb2", "2b", 320, 200, func(w *fakeWindow) { w.Title = "Notes" })

	// One that is there returns at once.
	listings := len(e.fake.requestsMatching("j/clients"))
	start := time.Now()
	res := e.windowsWith(`{"match":"forgejo","wait_ms":5000}`)
	if res.IsError || time.Since(start) > 2*time.Second || get(t, res, "wait.ended") != "found" || get(t, res, "wait.elapsed_ms") != 0.0 || get(t, res, "windows.0.shot") == nil ||
		len(e.fake.requestsMatching("j/clients")) != listings+1 {
		t.Errorf("there: %s", text(res))
	}
	if !e.unsubscribed() {
		t.Error("the subscription outlived the call")
	}

	// One that maps while it waits ends the wait on its event, and gets the time to draw what it opened with.
	listings = len(e.fake.requestsMatching("j/clients"))
	done := e.startWindows(context.Background(), `{"match":"open files","wait_ms":5000}`)
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "xdg-desktop-portal-gtk", Title: "Open Files", PID: 7000,
		Workspace: hypr.Workspace{ID: 5, Name: "web"}}, Width: 480, Height: 270})
	res = e.result(done)
	elapsed, _ := get(t, res, "wait.elapsed_ms").(float64)
	if res.IsError || get(t, res, "wait.ended") != "found" || elapsed < 350 || elapsed > 4000 || get(t, res, "windows.0.window") != "0xd1" || get(t, res, "windows.0.shot") == nil {
		t.Fatalf("mapped: %s", text(res))
	}
	if lines := strings.Split(text(res), "\n"); len(lines) != 3 || lines[1] != fmt.Sprintf("found after waiting %d ms", int(elapsed)) || !strings.HasSuffix(lines[2], " of window 0xd1") {
		t.Errorf("mapped: %q", text(res))
	}
	if waits := e.fake.requestsMatching("j/hyprmasq wait 0xd1"); len(waits) != 1 || waitArgsOf(t, waits[0])["quiet"] != "200" {
		t.Errorf("waits %q", waits)
	}
	// It listed the windows when it started, once more right after, and on the event, without polling.
	if n := len(e.fake.requestsMatching("j/clients")) - listings; n != 3 {
		t.Errorf("%d listings", n)
	}
	if !e.unsubscribed() {
		t.Error("the subscription outlived the call")
	}

	// A title that changes to match ends it too.
	e.fake.removeWindow("0xd1")
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd2", StableID: "d2", Class: "xdg-desktop-portal-gtk", Title: "", PID: 7000,
		Workspace: hypr.Workspace{ID: 5, Name: "web"}}, Width: 480, Height: 270})
	done = e.startWindows(context.Background(), `{"match":"open files","wait_ms":5000}`)
	e.fake.setTitle("0xd2", "Open Files")
	if res := e.result(done); get(t, res, "wait.ended") != "found" || get(t, res, "windows.0.window") != "0xd2" || get(t, res, "windows.0.shot") == nil {
		t.Errorf("title: %s", text(res))
	}

	// None by the limit is no error, and says so.
	start = time.Now()
	res = e.windowsWith(`{"match":"nothing","wait_ms":300}`)
	if took := time.Since(start); res.IsError || took < 300*time.Millisecond || took > 3*time.Second || get(t, res, "wait.ended") != "timeout" {
		t.Errorf("limit after %v: %s", took, text(res))
	}
	if want := "no window has \"nothing\" in its class or title, of 3 windows\nwaited 300 ms: no window with \"nothing\" in its class or title mapped"; text(res) != want || get(t, res, "summary") != want {
		t.Errorf("limit: %q", text(res))
	}
	if !e.unsubscribed() {
		t.Error("the subscription outlived the call")
	}

	// A call that the client cancels ends at once, and so does its subscription.
	ctx, cancel := context.WithCancel(context.Background())
	done = e.startWindows(ctx, `{"match":"nothing","wait_ms":10000}`)
	start = time.Now()
	cancel()
	if res := e.result(done); errorCode(t, res) != "cancelled" || time.Since(start) > 2*time.Second {
		t.Errorf("cancelled after %v: %s", time.Since(start), text(res))
	}
	if !e.unsubscribed() {
		t.Error("the subscription outlived the cancelled call")
	}

	for _, args := range []string{`{"wait_ms":10001}`, `{"wait_ms":-1}`, `{"wait_ms":"5000"}`} {
		if code := errorCode(t, e.windowsWith(args)); code != "invalid_arguments" {
			t.Errorf("%s: %q", args, code)
		}
	}
}

// Without match, windows waits for a window that the input of an act opened after the act had returned, which it
// returns as a late window with its shot. Another window that maps meanwhile does not end the wait. Without an act whose
// input can open a window, nothing can come, and it returns at once.
func TestWindowsWaitsForALateWindow(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	res := e.windowsWith(`{"wait_ms":5000}`)
	if res.IsError || get(t, res, "wait.ended") != "nothing" ||
		!strings.HasSuffix(text(res), "\ndid not wait: no act of yours in the last 2 minutes sent input that can open a window; give match to wait for another window") {
		t.Errorf("nothing to wait for: %s", text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	listings := len(e.fake.requestsMatching("j/clients"))
	done := e.startWindows(context.Background(), `{"wait_ms":5000}`)
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd3", StableID: "d3", Class: "other", Title: "Unrelated", PID: 7000}, Width: 100, Height: 100})
	time.Sleep(200 * time.Millisecond)
	e.lateWindow("0xd1", "d1", "Open Files", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true})
	res = e.result(done)
	if res.IsError || get(t, res, "wait.ended") != "found" || get(t, res, "late_windows.0.window") != "0xd1" || get(t, res, "late_windows.0.shot") == nil {
		t.Fatalf("late: %s", text(res))
	}
	if elapsed, _ := get(t, res, "wait.elapsed_ms").(float64); elapsed < 550 || elapsed > 4000 {
		t.Errorf("elapsed %v", elapsed)
	}
	if n := len(e.fake.requestsMatching("j/clients")) - listings; n != 4 {
		t.Errorf("%d listings", n)
	}
	if e.fake.find("0xd3").Socket {
		t.Error("the other window was driven")
	}
	// It was reported.
	if res := e.screenshot(`{"window":"0xa1"}`); get(t, res, "late_windows") != nil {
		t.Errorf("reported again: %s", text(res))
	}

	// None by the limit says that only such a window ends the wait.
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","o"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	res = e.windowsWith(`{"wait_ms":300}`)
	if res.IsError || get(t, res, "wait.ended") != "timeout" || get(t, res, "summary") != "waited 300 ms: no window that your input opened mapped" ||
		!strings.HasPrefix(get(t, res, "hint").(string), "Without match, only a window that hyprmasq found your input to open ends the wait") {
		t.Errorf("limit: %s", text(res))
	}
}

// Hyprland accepts the event socket's connection on its main thread, possibly after it has answered the first listing,
// so a window that maps in between sends its event to no one. The listing right after the first one has it.
func TestWindowsWaitsForAWindowThatMappedBeforeTheSubscription(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	accept := make(chan struct{})
	e.fake.with(func() {
		e.fake.acceptEvents = accept
		e.fake.onClients = func() {
			e.fake.with(func() { e.fake.onClients = nil })
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "portal", Title: "Open Files", PID: 7000}, Width: 100, Height: 100})
			close(accept)
			if !e.waitFor(func() bool { return e.fake.subscribed() == 1 }) {
				t.Error("no subscription")
			}
		}
	})
	start := time.Now()
	res := e.windowsWith(`{"match":"open files","wait_ms":5000}`)
	if res.IsError || get(t, res, "wait.ended") != "found" || get(t, res, "windows.0.window") != "0xd1" || time.Since(start) > 3*time.Second {
		t.Errorf("after %v: %s", time.Since(start), text(res))
	}
}

// Without Hyprland's event socket, windows lists the windows every 100 ms while it waits.
func TestWindowsWaitsWithoutEvents(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.events.Close()
	listings := len(e.fake.requestsMatching("j/clients"))
	done := make(chan *mcp.CallToolResult, 1)
	go func() {
		done <- e.server.windowsTool(context.Background(), &call{args: json.RawMessage(`{"match":"open files","wait_ms":5000}`)})
	}()
	if !e.waitFor(func() bool { return len(e.fake.requestsMatching("j/clients")) >= listings+3 }) {
		t.Fatal("no polling")
	}
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "portal", Title: "Open Files", PID: 7000}, Width: 100, Height: 100})
	if res := e.result(done); get(t, res, "wait.ended") != "found" || get(t, res, "windows.0.window") != "0xd1" {
		t.Errorf("polled: %s", text(res))
	}
}
