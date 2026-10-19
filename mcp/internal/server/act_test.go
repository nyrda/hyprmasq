package server

import (
	"context"
	"fmt"
	"os"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestShotRequiredForCoordinates(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]},{"action":"move","x":1,"y":1}]}`)
	if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), "actions[1] (move)") {
		t.Fatalf("%s", text(res))
	}
	res = e.act(`{"window":"0xa1 eis close 0xa1","actions":[]}`)
	if code := errorCode(t, res); code != "invalid_arguments" {
		t.Fatalf("an address with spaces was accepted: %s", text(res))
	}
	if e.inputs() != nil {
		t.Error("sent input")
	}
}

// The wait ends where the capture's reserve starts, so a change phase that reaches past it is refused.
func TestWaitWithinTheDeadline(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	res := e.act(`{"window":"0xa1","wait":{"expect_ms":20000}}`)
	if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), "give a deadline_ms of at least 21000") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if res := e.act(`{"window":"0xa1","wait":{"expect_ms":20000},"deadline_ms":21000}`); res.IsError {
		t.Error(text(res))
	}
	if res := e.act(`{"window":"0xa1","wait":{"expect_ms":20000,"expect_change":false}}`); res.IsError {
		t.Error(text(res))
	}
	// Without time for a wait, the call only captures.
	waits := len(e.fake.requestsMatching("j/hyprmasq wait"))
	res = e.act(`{"window":"0xa1","deadline_ms":1000}`)
	if res.IsError || !strings.Contains(text(res), "no time left to wait") || len(e.fake.requestsMatching("j/hyprmasq wait")) != waits || get(t, res, "shot") == nil {
		t.Errorf("%s", text(res))
	}
	if got := get(t, res, "wait"); got.(map[string]any)["ended"] != "timeout" || got.(map[string]any)["summary"] != "no_change" {
		t.Errorf("wait %v", got)
	}
}

func TestKeyboardBatchIgnoresResize(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() { e.fake.find("0xa1").Width = 400 })
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["enter"]}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
}

func TestDeadlineCountsLockWaiting(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	lock, err := acquireLock(context.Background(), e.server.lockPath("1a"))
	if err != nil {
		t.Fatal(err)
	}
	time.AfterFunc(1500*time.Millisecond, lock.release)
	start := time.Now()
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}],"deadline_ms":5000}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	waited := e.fake.timeOf("j/hyprmasq wait").Sub(start)
	if waited < 1500*time.Millisecond {
		t.Fatalf("waited %v while the lock was held", waited)
	}
	args := waitArgsOf(t, e.fake.requestsMatching("j/hyprmasq wait")[0])
	want := (5000*time.Millisecond - captureReserve - waited).Milliseconds()
	if d, _ := strconv.Atoi(args["deadline"]); int64(d) > want+250 || int64(d) < want-250 {
		t.Errorf("wait deadline %d, want %d: the deadline minus the capture reserve and the %v spent on the lock and input", d, want, waited)
	}

	lock, err = acquireLock(context.Background(), e.server.lockPath("1a"))
	if err != nil {
		t.Fatal(err)
	}
	defer lock.release()
	shots := len(e.fake.requestsMatching("j/hyprmasq screenshot"))
	start = time.Now()
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["b"]}],"deadline_ms":4000}`)
	if code := errorCode(t, res); code != "deadline" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// The call gives up on the lock when the time for input has run out, and captures right away.
	want = inputTime(4000 * time.Millisecond).Milliseconds()
	if gaveUp := e.fake.nthTime("j/hyprmasq screenshot", shots).Sub(start).Milliseconds(); gaveUp < want-100 || gaveUp > want+900 {
		t.Errorf("gave up after %d ms, want the deadline minus the capture reserve and the time to stop a sender, %d ms", gaveUp, want)
	}
	if got := get(t, res, "actions.delivery"); got != "none" {
		t.Errorf("delivery %v", got)
	}
	if len(e.inputs()) != 1 {
		t.Errorf("inputs %q", e.inputs())
	}
}

func TestTwoServersContend(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	other := e.newServer()
	release := make(chan struct{})
	block := blockingWait(release)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if w.Address == "0xa1" {
				return block(w, args, cancel)
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 200, "elapsedMs": 200})
		}
	})
	done := make(chan *mcp.CallToolResult)
	go func() { done <- e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`) }()
	for len(e.fake.requestsMatching("j/hyprmasq wait 0xa1")) == 0 {
		time.Sleep(5 * time.Millisecond)
	}

	// The window is locked until the first call has captured.
	res := callTool(other, context.Background(), actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["b"]}],"deadline_ms":2500}`)
	if code := errorCode(t, res); code != "deadline" {
		t.Fatalf("code %q: %s", code, text(res))
	}

	// Another window is not.
	res = callTool(other, context.Background(), actOn, `{"window":"0xb2","actions":[{"action":"key","keys":["c"]}],"deadline_ms":10000}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	close(release)
	if res := <-done; res.IsError {
		t.Fatal(text(res))
	}
	if got := e.inputs(); !reflect.DeepEqual(got, [][]string{{"keys", "a", "1"}, {"keys", "c", "1"}}) {
		t.Errorf("inputs %q", got)
	}
	// Both sessions renewed leases under their own owners.
	e.fake.with(func() {
		if a, b := e.fake.find("0xa1").Lease.Owner, e.fake.find("0xb2").Lease.Owner; a != e.server.owner || b != other.owner || a == b {
			t.Errorf("lease owners %q and %q", a, b)
		}
	})
}

// locked tells whether someone else holds the lock.
func locked(path string) bool {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	l, err := acquireLock(ctx, path)
	l.release()
	return err != nil
}

// The window is read again once the call holds both locks, since other calls may have changed it while it waited.
func TestWindowReadAgainAfterTheLocks(t *testing.T) {
	slow(t)
	cases := []struct {
		name, actions string
		change        func(w *fakeWindow)
		code          string
	}{
		{"baseline", `[{"action":"key","keys":["a"]}]`, func(w *fakeWindow) { w.Commits = 77 }, ""},
		{"size", `[{"action":"click","x":5,"y":5}]`, func(w *fakeWindow) { w.Width = 400 }, "stale_shot"},
		{"capabilities", `[{"action":"drag","from":[5,5],"to":[50,50]}]`, func(w *fakeWindow) { w.Caps.Hold = false }, "unsupported"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			w := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
			e.screenshot(`{"window":"0xa1"}`)
			// A batch on another window holds the send lock.
			sendLock, err := acquireLock(context.Background(), e.server.sendLockPath())
			if err != nil {
				t.Fatal(err)
			}
			done := make(chan *mcp.CallToolResult)
			go func() { done <- e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s}`, c.actions)) }()
			e.waitFor(func() bool { return locked(e.server.lockPath("1a")) })
			time.Sleep(200 * time.Millisecond)
			e.fake.with(func() { c.change(w) })
			sendLock.release()
			res := <-done
			if code := errorCode(t, res); code != c.code {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if c.code != "" {
				if e.inputs() != nil || get(t, res, "actions.delivery") != "none" || e.fake.requestsMatching("j/hyprmasq lease") != nil {
					t.Errorf("sent %q, leased %q: %s", e.inputs(), e.fake.requestsMatching("j/hyprmasq lease"), text(res))
				}
				return
			}
			if args := waitArgsOf(t, e.fake.requestsMatching("j/hyprmasq wait")[0]); args["since"] != "77" {
				t.Errorf("wait %v", args)
			}
		})
	}
}

// An act without actions reads the window again once it holds the window's lock. A window that closed while the call
// waited, and whose address another window has now, is gone: the other window is neither waited on nor captured, and
// gets no socket.
func TestEmptyActForAWindowReplacedWhileItWaited(t *testing.T) {
	for _, args := range []string{`{"window":"0xa1"}`, `{"window":"0xa1","shot":"s1"}`} {
		t.Run(args, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			// Another call on the window holds its lock.
			windowLock, err := acquireLock(context.Background(), e.server.lockPath("1a"))
			if err != nil {
				t.Fatal(err)
			}
			states := len(e.fake.requestsMatching("j/hyprmasq state"))
			done := make(chan *mcp.CallToolResult)
			go func() { done <- e.act(args) }()
			e.waitFor(func() bool { return len(e.fake.requestsMatching("j/hyprmasq state")) > states })
			time.Sleep(200 * time.Millisecond)
			e.fake.removeWindow("0xa1")
			e.window("0xa1", "2b", 640, 400)
			counts := map[string]int{}
			prefixes := []string{"j/hyprmasq wait", "j/hyprmasq screenshot", "j/hyprmasq eis", "j/hyprmasq lease"}
			for _, prefix := range prefixes {
				counts[prefix] = len(e.fake.requestsMatching(prefix))
			}
			windowLock.release()
			res := <-done
			if code := errorCode(t, res); code != "window_gone" || hasImage(res) || get(t, res, "shot") != nil {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if msg := get(t, res, "error.message"); msg != "window 0xa1 closed, and another window has its address now" {
				t.Errorf("message %q", msg)
			}
			for _, prefix := range prefixes {
				if got := e.fake.requestsMatching(prefix); len(got) != counts[prefix] {
					t.Errorf("requested %q", got[counts[prefix]:])
				}
			}
		})
	}
}

func TestCaptureErrorAfterInput(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() { e.fake.onScreenshot = func(w *fakeWindow) string { return "the renderer failed" } })
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`)
	if res.IsError {
		t.Fatalf("a failed capture after input is an error: %s", text(res))
	}
	if got := get(t, res, "capture_error.message"); got != "the renderer failed" {
		t.Errorf("capture error %v", got)
	}
	if !strings.Contains(text(res), "capture failed: the renderer failed") || get(t, res, "hint") != hintCaptureFailed {
		t.Errorf("text %q", text(res))
	}
	// Without input it is an error.
	if code := errorCode(t, e.act(`{"window":"0xa1"}`)); code != "internal" {
		t.Errorf("code %q", code)
	}
}

// A window without hold shares its app's connection with the user's focused window, and keeps the keyboard focus for a
// quarter of a second after a shortcut. The plugin counts the user's input to the app that took it back within that
// time, and act reports the event, from the counter read once more after the wait, without claiming that the shortcut
// failed or worked. Batches without a key action, windows with hold and counters of another socket get no second look
// or no flag.
func TestSharedFocusReturnedForUserInput(t *testing.T) {
	e := newTestEnv(t)
	shared := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Caps.Hold = false })
	e.window("0xb2", "2b", 320, 200)
	// The user's typing interrupts the shortcut while the sender runs: the counter rises once input has started.
	interrupt := func(socketID string) {
		e.fake.with(func() {
			bumped := false
			e.fake.onState = func(w *fakeWindow) {
				if !bumped && e.senderLogged("start") {
					bumped = true
					w.Interrupted++
					if socketID != "" {
						w.SocketID = socketID
					}
				}
			}
		})
		os.Remove(e.inputLog)
	}
	states := func() int { return len(e.fake.requestsMatching("j/hyprmasq state")) }

	// The event, with delivery as it was, and the old field for clients that read it.
	flagged := func(res *mcp.CallToolResult) bool {
		return get(t, res, "actions.delivery") == "complete" && get(t, res, "actions.shared_focus_returned.effect") == "unknown" &&
			get(t, res, "actions.interrupted") == true
	}
	interrupt("")
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]},{"action":"type","text":"example.org\n"}],"capture":"none"}`)
	summary, hint := get(t, res, "summary").(string), get(t, res, "hint")
	if res.IsError || !flagged(res) || !strings.HasPrefix(summary, "2/2 delivered · shared keyboard focus returned for user input · ") || hint != focusReturnedHint(nil) {
		t.Fatalf("%s", text(res))
	}
	if shared.Interrupted != 1 {
		t.Fatalf("counter %d", shared.Interrupted)
	}
	// With a shot, the hint points at it.
	interrupt("")
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"window"}`)
	if res.IsError || !flagged(res) || get(t, res, "hint") != focusReturnedHint(&shotOut{}) {
		t.Fatalf("%s", text(res))
	}
	// A shortcut that did nothing draws nothing, which the hint about the focus already says to check, without the
	// guesses of the hint about a missing redraw.
	interrupt("")
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			expect, _ := strconv.Atoi(args["expect"])
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
				"commitsSince": 0, "quietMs": expect, "elapsedMs": expect, "resizing": false})
		}
	})
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none","wait":{"expect_ms":300}}`)
	if res.IsError || !flagged(res) || get(t, res, "hint") != focusReturnedHint(nil) || !strings.Contains(text(res), "no redraw within 300 ms") {
		t.Fatalf("%s", text(res))
	}
	e.fake.with(func() { e.fake.onWait = nil })
	// The counter stays: the focus did not return within the next shortcut.
	e.fake.with(func() { e.fake.onState = nil })
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["alt","left"]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.interrupted") != nil || get(t, res, "actions.shared_focus_returned") != nil || strings.Contains(text(res), "keyboard focus") {
		t.Errorf("%s", text(res))
	}
	// Typing alone takes effect without a round trip, so nothing is read again for it.
	interrupt("")
	before := states()
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"hello"}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.interrupted") != nil || states()-before != 2 {
		t.Errorf("%d state reads: %s", states()-before, text(res))
	}
	// A counter of a socket that replaced the one the input went to says nothing about that input.
	interrupt("another")
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["f6"]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.interrupted") != nil {
		t.Errorf("%s", text(res))
	}
	// A window with hold has the keyboard to itself between its frames. Its first act opens its socket.
	e.fake.with(func() { e.fake.onState = nil })
	e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none"}`)
	interrupt("")
	before = states()
	res = e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.interrupted") != nil || states()-before != 2 {
		t.Errorf("%d state reads: %s", states()-before, text(res))
	}
}

// Input that closes its window after the wait has ended quiet, as a file chooser does a moment after Enter, before or
// after the capture, is delivered and closed the window: no failed capture, and no shot of a window that is gone.
func TestWindowClosedAfterTheWait(t *testing.T) {
	slow(t)
	quiet := jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 201, "elapsedMs": 492})
	for _, c := range []struct {
		name, capture string
		// Whether the window closes right after the wait, before the capture, or a moment later, during the grace.
		beforeCapture bool
	}{
		{"before the changes", "changes", true},
		{"before the shot", "window", true},
		{"after the shot", "changes", false},
		{"without a capture", "none", false},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.server.cfg.NewWindowGrace = 500 * time.Millisecond
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			e.fake.with(func() {
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					if c.beforeCapture {
						e.fake.removeWindow("0xa1")
					} else {
						time.AfterFunc(200*time.Millisecond, func() { e.fake.removeWindow("0xa1") })
					}
					return quiet
				}
			})
			res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}],"capture":"` + c.capture + `"}`)
			if res.IsError || get(t, res, "error") != nil || get(t, res, "capture_error") != nil || get(t, res, "shot") != nil || hasImage(res) {
				t.Fatalf("result: %s", text(res))
			}
			if get(t, res, "window_closed") != true || get(t, res, "wait.ended") != "quiet" || get(t, res, "actions.delivery") != "complete" || get(t, res, "lease") != nil {
				t.Errorf("result: %v", structured(t, res))
			}
			if want := "1/1 delivered · redraw observed · quiet 201 ms · 492 ms · window closed\n" + closedHint(nil); text(res) != want {
				t.Errorf("text %q", text(res))
			}
			if e.server.shots.lastOf("0xa1", "1a") != nil {
				t.Error("kept a shot of the closed window")
			}
		})
	}

	// A wait that saw no redraw says nothing about a window that closed after it, so the hint does not say that the click
	// may have missed.
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			e.fake.removeWindow("0xa1")
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "redrawObserved": false, "commitsSince": 0, "quietMs": 1000, "elapsedMs": 1000})
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`)
	if want := "1/1 delivered · no redraw within 1000 ms · window closed\n" + closedHint(nil); res.IsError || text(res) != want {
		t.Errorf("no redraw, then closed: %q", text(res))
	}

	// An act without actions whose window closes after the wait reports that too.
	e = newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			e.fake.removeWindow("0xa1")
			return quiet
		}
	})
	if res := e.act(`{"window":"0xa1"}`); res.IsError || get(t, res, "window_closed") != true || !strings.HasPrefix(text(res), "redraw observed · quiet 201 ms · 492 ms · window closed\n") {
		t.Errorf("empty act: %s", text(res))
	}
}
