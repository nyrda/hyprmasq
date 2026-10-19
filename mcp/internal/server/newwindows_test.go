package server

import (
	"context"
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestNewWindows(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.server.tokens["t0k3n"] = 777
	e.setLaunched(777, "t0k3n")
	e.setLaunched(888, "foreign")
	mail := hypr.Workspace{ID: 5, Name: "mail"}
	onWait := func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
		if w.Address == "0xa1" {
			// The plugin placed the dialog on the target's hidden workspace. A window of another launch of this server
			// opened on the user's workspace and took the focus, and another app's window too, neither of them the
			// target's.
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "test.app", Title: "Save As", PID: 4242, Workspace: mail}, Width: 100, Height: 100,
				Source: &hypr.Source{Window: "0xa1", Placed: true}})
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd2", StableID: "d2", Class: "helper", Title: "Helper", PID: 777}, Width: 100, Height: 50})
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd3", StableID: "d3", Class: "other", Title: "Other", PID: 888}, Width: 100, Height: 100})
			// Of the target's process but from no window the plugin knows of, as another app of an xwayland-satellite,
			// which hosts every app of its X server in one process.
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd4", StableID: "d4", Class: "xterm", Title: "Other X11 app", PID: 4242}, Width: 100, Height: 100})
			e.fake.active = "0xd2"
		}
		return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 200, "elapsedMs": 300})
	}
	e.fake.with(func() { e.fake.onWait = onWait })
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	shot := func(id string, width, height float64) map[string]any {
		return map[string]any{"id": id, "width": width, "height": height, "frame": map[string]any{"x": 0.0, "y": 0.0, "width": width / 2, "height": height / 2}}
	}
	want := []any{
		map[string]any{"window": "0xd1", "stable_id": "d1", "class": "test.app", "title": "Save As", "pid": 4242.0, "same_pid": true, "launched": false,
			"workspace": "mail", "visible": false, "focused": false, "source": "0xa1", "placed": true, "shot": shot("s3", 200, 200)},
		map[string]any{"window": "0xd2", "stable_id": "d2", "class": "helper", "title": "Helper", "pid": 777.0, "same_pid": false, "launched": true,
			"workspace": "1", "visible": true, "focused": true, "placed": false},
		map[string]any{"window": "0xd3", "stable_id": "d3", "class": "other", "title": "Other", "pid": 888.0, "same_pid": false, "launched": false,
			"workspace": "1", "visible": true, "focused": false, "placed": false},
		map[string]any{"window": "0xd4", "stable_id": "d4", "class": "xterm", "title": "Other X11 app", "pid": 4242.0, "same_pid": true, "launched": false,
			"workspace": "1", "visible": true, "focused": false, "placed": false},
	}
	if got := get(t, res, "new_windows"); !reflect.DeepEqual(got, want) {
		t.Errorf("new windows %v", got)
	}
	// The click changed nothing, so the image is the shot of the window from the target.
	lines := strings.Split(text(res), "\n")
	if !strings.HasSuffix(lines[0], ` · new windows 0xd1 (test.app "Save As", same pid) on workspace mail (hidden), 0xd2 (helper "Helper", launched) on workspace 1 (visible, focused), `+
		`0xd3 (other "Other") on workspace 1 (visible), 0xd4 (xterm "Other X11 app", same pid) on workspace 1 (visible)`) || lines[1] != "shot s3 200×200 of new window 0xd1" || len(lines) != 3 {
		t.Errorf("text %q", text(res))
	}
	// The click changed nothing in its window, but it opened windows, which the hint says in place of the click missing.
	if hint := get(t, res, "hint").(string); hint != `The input opened new window 0xd1 "Save As" in shot s3.` {
		t.Errorf("hint %q", hint)
	}
	if n := len(res.Content) - 1; n != 1 {
		t.Errorf("%d images", n)
	}
	// Its shot takes coordinates, and its socket is open. The other apps' windows stay undriven.
	if !e.fake.find("0xd1").Socket || e.fake.find("0xd2").Socket || e.fake.find("0xd3").Socket || e.fake.find("0xd4").Socket {
		t.Errorf("sockets %v %v %v %v", e.fake.find("0xd1").Socket, e.fake.find("0xd2").Socket, e.fake.find("0xd3").Socket, e.fake.find("0xd4").Socket)
	}
	if res := e.act(`{"window":"0xd1","shot":"s3","actions":[{"action":"click","x":5,"y":5}]}`); res.IsError {
		t.Errorf("click in the new window's shot: %s", text(res))
	}
	// A new window at a reused address is new.
	before := []hypr.Window{{Address: "0xd1", StableID: "d1"}}
	after := []hypr.Window{{Address: "0xd1", StableID: "e9", PID: 1}}
	if got := e.server.newWindows(t.Context(), before, after, "", 0, ""); len(got) != 1 || got[0].StableID != "e9" {
		t.Errorf("reused address: %v", got)
	}
}

// A window of the target's own launch comes from the target without a source, and one whose source is the target but
// that the plugin could not place says why, both with a shot and the hint that they took the user's screen.
func TestNewWindowsOfTheTargetsLaunch(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PID = 777 })
	e.screenshot(`{"window":"0xa1"}`)
	e.server.tokens["t0k3n"] = 777
	e.setLaunched(777, "t0k3n")
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xa1" {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd2", StableID: "d2", Class: "helper", Title: "Helper", PID: 777}, Width: 100, Height: 50})
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd5", StableID: "d5", Class: "test.app", Title: "Odd", PID: 4242}, Width: 100, Height: 50,
					Source: &hypr.Source{Window: "0xa1", Reason: "workspace"}})
				// The user's own input to the app came later, so the plugin left this one to them.
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd7", StableID: "d7", Class: "test.app", Title: "Theirs", PID: 4242}, Width: 100, Height: 50,
					Source: &hypr.Source{Window: "0xa1", Reason: "user_input"}})
				e.fake.active = "0xd2"
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 200, "elapsedMs": 300})
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`)
	if res.IsError || get(t, res, "new_windows.0.launched") != true || get(t, res, "new_windows.0.shot.id") != "s3" || get(t, res, "new_windows.1.shot.id") != "s4" {
		t.Fatal(text(res))
	}
	if get(t, res, "new_windows.2.window") != "0xd7" || get(t, res, "new_windows.2.not_placed") != "user_input" || get(t, res, "new_windows.2.shot") != nil || e.fake.find("0xd7").Socket {
		t.Errorf("the user's window: %s", text(res))
	}
	hint, _ := get(t, res, "hint").(string)
	if strings.Contains(hint, "0xd7") {
		t.Errorf("hint %q names the user's window", hint)
	}
	// The hint names the windows that the input opened, and the two on the user's workspace with why they are there.
	opened := []newWindowOut{
		{Window: "0xd2", Title: "Helper", Workspace: "1", Visible: true, Focused: true, fromTarget: true, Shot: &shotOut{ID: "s3"}},
		{Window: "0xd5", Title: "Odd", Workspace: "1", Visible: true, NotPlaced: "workspace", fromTarget: true, Shot: &shotOut{ID: "s4"}},
	}
	if want := joinHints(openedHint(opened), newWindowsHint(opened)); hint != want {
		t.Errorf("hint %q, want %q", hint, want)
	}

	// Input that closes the launched window, after which the launch opens another one, still counts it as the target's,
	// although the closed window is no longer the launch's.
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xa1" {
				e.fake.removeWindow("0xa1")
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd6", StableID: "d6", Class: "helper", Title: "Next", PID: 777}, Width: 100, Height: 50})
			}
			return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
		}
	})
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl+w"]}]}`)
	if got := get(t, res, "new_windows.0.window"); got != "0xd6" || get(t, res, "new_windows.0.shot.id") == nil {
		t.Errorf("after the close: %s", text(res))
	}
}

// A window that another call's input opened while this call waited for the window's lock is not new to this call.
func TestNewWindowsCountFromTheLock(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	windowLock, err := acquireLock(context.Background(), e.server.lockPath("1a"))
	if err != nil {
		t.Fatal(err)
	}
	states := len(e.fake.requestsMatching("j/hyprmasq state"))
	done := make(chan *mcp.CallToolResult)
	go func() { done <- e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`) }()
	e.waitFor(func() bool { return len(e.fake.requestsMatching("j/hyprmasq state")) > states })
	time.Sleep(100 * time.Millisecond)
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "test.app", Title: "Other call's", PID: 4242}, Width: 100, Height: 100,
		Source: &hypr.Source{Window: "0xa1", Placed: true}})
	windowLock.release()
	res := <-done
	if windows, _ := get(t, res, "new_windows").([]any); res.IsError || len(windows) != 0 || e.fake.find("0xd1").Socket {
		t.Errorf("result %s", text(res))
	}
}

// A window that maps a moment after the window that got the input has settled is still reported, with its shot.
func TestNewWindowAfterTheWait(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.server.cfg.NewWindowGrace = 500 * time.Millisecond
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	onWait := func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
		if w.Address == "0xa1" {
			time.AfterFunc(150*time.Millisecond, func() {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "test.app", Title: "Compose", PID: 4242,
					Workspace: hypr.Workspace{ID: 5, Name: "mail"}}, Width: 100, Height: 100, Source: &hypr.Source{Window: "0xa1", Placed: true}})
			})
		}
		return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 200, "elapsedMs": 300})
	}
	e.fake.with(func() { e.fake.onWait = onWait })
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["ctrl","r"]}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := get(t, res, "new_windows.0.window"); got != "0xd1" || get(t, res, "new_windows.0.shot.id") == nil {
		t.Errorf("new windows %v", get(t, res, "new_windows"))
	}
}

// A new window on a hidden workspace that waits for frame callbacks gets them from the wait of its capture, with no wait
// of its own before, and its shot says that it may show an older frame when it did not draw.
func TestShotOfAStarvedNewWindow(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xa1" {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "test.app", Title: "Compose", PID: 4242,
					Workspace: hypr.Workspace{ID: 5, Name: "mail"}}, Width: 100, Height: 100, Starved: true, Source: &hypr.Source{Window: "0xa1", Placed: true}})
				return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 200, "elapsedMs": 300})
			}
			expect, _ := strconv.Atoi(args["expect"])
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
				"commitsSince": 0, "quietMs": expect, "elapsedMs": expect, "resizing": false})
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	id, _ := get(t, res, "new_windows.0.shot.id").(string)
	if id == "" || get(t, res, "new_windows.0.shot.may_be_stale") != true ||
		!strings.Contains(text(res), "(may be stale: not redrawn within 1000 ms) of new window 0xd1") {
		t.Errorf("new window shot: %s", text(res))
	}
	if hint, _ := get(t, res, "hint").(string); !containsHint(hint, starvedHint(&shotOut{ID: id, starved: &starvation{WaitedMs: 1000}})) {
		t.Errorf("hint %q", hint)
	}
	waits := e.fake.requestsMatching("j/hyprmasq wait 0xd1")
	if len(waits) != 1 {
		t.Fatalf("waits %q", waits)
	}
	if args := waitArgsOf(t, waits[0]); args["expect"] != "1000" || args["quiet"] != "100" {
		t.Errorf("wait %q", waits[0])
	}
}

// An act whose input closed its window and failed, but opened a window that it returns a shot of, is no tool error:
// clients drop the images of an error, and the agent goes on from that shot.
func TestFailedActWithANewWindowShot(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "fail:unconfirmed:1:partial:0")
	closed := false
	e.fake.with(func() {
		e.fake.onPlugin = func(words []string) (string, bool) {
			if !closed && e.senderLogged("exit") {
				closed = true
				e.fake.removeWindow("0xa1")
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "test.app", Title: "Sending", PID: 4242,
					Workspace: hypr.Workspace{ID: 5, Name: "mail"}}, Width: 100, Height: 100, Source: &hypr.Source{Window: "0xa1", Placed: true}})
			}
			return "", false
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["ctrl+q"]},{"action":"type","text":"more"}]}`)
	if code := errorCode(t, res); code != "window_gone" || res.IsError || !hasImage(res) || get(t, res, "new_windows.0.shot.id") == nil {
		t.Errorf("result: error %v %s", res.IsError, text(res))
	}
}

// When input closes its window, the result carries a shot of the window that it came from, as the plugin found it, if
// that is still open and not the user's: the agent goes on there.
func TestShotOfTheWindowAClosedWindowCameFrom(t *testing.T) {
	closing := jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
	for _, c := range []struct {
		name   string
		source *hypr.Source
		// Replaces the parent with another window at its address.
		replaced bool
		capture  string
		// Whether the result names the parent, and has its shot.
		named, shot bool
	}{
		{"from its parent", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true}, false, "changes", true, true},
		{"without a capture", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true}, false, "none", true, false},
		{"opened by the user", &hypr.Source{Window: "0xa1", StableID: "1a", Reason: "user_input"}, false, "changes", false, false},
		{"from one gone", &hypr.Source{Window: "0xa1", Placed: true}, false, "changes", false, false},
		{"address reused", &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true}, true, "changes", false, false},
		{"from no window", nil, false, "changes", false, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			stableID := "1a"
			if c.replaced {
				stableID = "9z"
			}
			e.window("0xa1", stableID, 320, 200, func(w *fakeWindow) { w.Title = "Upload"; w.Workspace = hypr.Workspace{ID: 5, Name: "web"} })
			e.window("0xd1", "d1", 100, 80, func(w *fakeWindow) { w.Title = "Open Files"; w.Source = c.source })
			id := shotID(t, e.screenshot(`{"window":"0xd1"}`))
			e.fake.with(func() {
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					if w.Address == "0xd1" {
						e.fake.removeWindow("0xd1")
						return closing
					}
					return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "redrawObserved": true, "commitsSince": 0, "quietMs": 200, "elapsedMs": 250})
				}
			})
			res := e.act(`{"window":"0xd1","shot":"` + id + `","actions":[{"action":"click","x":5,"y":5}],"capture":"` + c.capture + `"}`)
			if res.IsError || get(t, res, "window_closed") != true {
				t.Fatalf("result: %s", text(res))
			}
			lines := strings.Split(text(res), "\n")
			hint, _ := get(t, res, "hint").(string)
			if !c.named {
				if get(t, res, "source") != nil || hasImage(res) || strings.Contains(text(res), "0xa1") || text(res) != "1/1 delivered · window closed\n"+closedHint(nil) {
					t.Errorf("result: %s", text(res))
				}
				if e.fake.find("0xa1").Socket {
					t.Error("drove the window")
				}
				return
			}
			if lines[0] != `1/1 delivered · window closed · back to 0xa1 (test.app "Upload") on workspace web (hidden)` || get(t, res, "source.window") != "0xa1" ||
				get(t, res, "source.stable_id") != "1a" || get(t, res, "source.workspace") != "web" || get(t, res, "source.visible") != false {
				t.Errorf("result: %s", text(res))
			}
			if !c.shot {
				if hasImage(res) || get(t, res, "source.shot") != nil ||
					hint != closedHint(&sourceOut{Window: "0xa1"}) {
					t.Errorf("without a capture: %s", text(res))
				}
				return
			}
			shot, _ := get(t, res, "source.shot.id").(string)
			if shot == "" || len(res.Content) != 2 || lines[1] != "shot "+shot+" 640×400 of 0xa1, the window it came from" ||
				hint != closedHint(&sourceOut{Window: "0xa1", Shot: &shotOut{ID: shot}}) {
				t.Fatalf("result: %s", text(res))
			}
			// The window answered the close before its shot, which takes coordinates and is the window's last shot,
			// which the next changes compare with.
			if waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1"); len(waits) != 1 {
				t.Errorf("waits %q", waits)
			}
			if last := e.server.shots.lastOf("0xa1", "1a"); last == nil || last.ID != shot {
				t.Errorf("last shot %v", last)
			}
			if res := e.act(`{"window":"0xa1","shot":"` + shot + `","actions":[{"action":"click","x":5,"y":5}]}`); res.IsError {
				t.Errorf("click in its shot: %s", text(res))
			}
		})
	}
}

// The window that a closed window came from gets time to answer the close before its shot, as a page draws the file that
// a file chooser gave it once it gets frame callbacks: the wait sends them, waits for its first redraw, and if it comes,
// for quiet. A window that draws nothing is captured after the change phase. The wait ends early enough for the capture
// within the deadline.
func TestTheWindowItCameFromAnswersTheClose(t *testing.T) {
	slow(t)
	for _, c := range []struct {
		name     string
		redraw   bool
		deadline int
		// How long the dialog takes to close after the input.
		closing time.Duration
	}{
		{"it draws", true, 15000, 0},
		{"it draws nothing", false, 15000, 0},
		// Less than 1500 ms is left after the dialog closed and the capture's half reserve.
		{"a short deadline", true, 3000, 1200 * time.Millisecond},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			f := &form{}
			parent := formWindow(e, f)
			e.window("0xd1", "d1", 100, 80, func(w *fakeWindow) { w.Source = &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true} })
			var start time.Time
			e.fake.with(func() {
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					if w.Address == "0xd1" {
						time.Sleep(c.closing)
						e.fake.removeWindow("0xd1")
						start = time.Now()
						return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
					}
					expect, _ := strconv.Atoi(args["expect"])
					if !c.redraw {
						time.Sleep(time.Duration(expect) * time.Millisecond)
						var reply string
						e.fake.with(func() {
							reply = jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false, "commitsSince": 0,
								"quietMs": 0, "elapsedMs": expect, "resizing": false, "animated": []hypr.IntRect{}})
						})
						return reply
					}
					// The file lands in the list a moment after the frame callbacks start.
					time.Sleep(50 * time.Millisecond)
					e.redraw(parent, f, elsewhere)
					var reply string
					e.fake.with(func() { reply = quietReply(w) })
					return reply
				}
			})
			called := time.Now()
			res := e.act(fmt.Sprintf(`{"window":"0xd1","actions":[{"action":"key","keys":["enter"]}],"deadline_ms":%d}`, c.deadline))
			took := time.Since(start)
			if time.Since(called) > time.Duration(c.deadline)*time.Millisecond {
				t.Errorf("returned after %v", time.Since(called))
			}
			shot, _ := get(t, res, "source.shot.id").(string)
			if res.IsError || shot == "" {
				t.Fatalf("result: %s", text(res))
			}
			waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1 ")
			if len(waits) != 1 {
				t.Fatalf("waits %q", waits)
			}
			args := waitArgsOf(t, waits[0])
			deadline, _ := strconv.Atoi(args["deadline"])
			// The capture has half of its reserve left after the wait.
			if args["expect"] != strconv.Itoa(min(sourceExpectMs, deadline)) || args["quiet"] != "200" || args["ignore"] != "animations" ||
				deadline > int(sourceSettleMax.Milliseconds()) {
				t.Errorf("wait %q", waits[0])
			}
			if left := called.Add(time.Duration(c.deadline)*time.Millisecond).Sub(e.fake.timeOf("j/hyprmasq wait 0xa1 ")) - captureReserve/2; c.closing > 0 &&
				(deadline >= int(sourceSettleMax.Milliseconds()) || time.Duration(deadline)*time.Millisecond > left+20*time.Millisecond) {
				t.Errorf("wait %q", waits[0])
			}
			// The shot shows the window after its redraw.
			var drew bool
			e.fake.with(func() { drew = e.server.shots.get(shot).Commits == parent.Commits })
			if !drew {
				t.Errorf("shot %s was taken before the redraw", shot)
			}
			if !c.redraw && (took < sourceExpectMs*time.Millisecond || took > sourceExpectMs*time.Millisecond+time.Second) {
				t.Errorf("took %v", took)
			}
		})
	}
}

// A window that the closed window came from, and that closes before its shot, is no window to go back to.
func TestClosedSourceThatClosesBeforeItsShot(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xd1", "d1", 100, 80, func(w *fakeWindow) { w.Source = &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true} })
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			e.fake.removeWindow(w.Address)
			if w.Address == "0xd1" {
				return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
			}
			return pluginError("window_gone", "no mapped window "+w.Address)
		}
	})
	res := e.act(`{"window":"0xd1","actions":[{"action":"key","keys":["enter"]}]}`)
	if res.IsError || get(t, res, "source") != nil || hasImage(res) || text(res) != "1/1 delivered · window closed\n"+closedHint(nil) {
		t.Errorf("result: %s", text(res))
	}
}

// Input that closes its window with more input after it fails, but its result has the shot of the window that the
// closed window came from, so it is no tool error.
func TestFailedActWithTheShotOfTheWindowItCameFrom(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xd1", "d1", 100, 80, func(w *fakeWindow) { w.Source = &hypr.Source{Window: "0xa1", StableID: "1a", Placed: true} })
	t.Setenv("FAKE_INPUT_MODE", "fail:unconfirmed:1:partial:0")
	closed := false
	e.fake.with(func() {
		e.fake.onPlugin = func(words []string) (string, bool) {
			if !closed && e.senderLogged("exit") {
				closed = true
				e.fake.removeWindow("0xd1")
			}
			return "", false
		}
	})
	res := e.act(`{"window":"0xd1","actions":[{"action":"key","keys":["enter"]},{"action":"type","text":"more"}]}`)
	if code := errorCode(t, res); code != "window_gone" || res.IsError || !hasImage(res) || get(t, res, "source.shot.id") == nil {
		t.Fatalf("result: error %v %s", res.IsError, text(res))
	}
	shot, _ := get(t, res, "source.shot.id").(string)
	if hint, _ := get(t, res, "hint").(string); !containsHint(hint, sourceHint(&sourceOut{Window: "0xa1", Shot: &shotOut{ID: shot}})) {
		t.Errorf("hint %q", hint)
	}
}
