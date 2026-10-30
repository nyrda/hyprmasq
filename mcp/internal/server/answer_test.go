package server

import (
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// afterBatch leaves out the waits for the window's answer to a click within a batch, which wait answerQuietMs for
// quiet within answerMaxMs.
func afterBatch(t *testing.T, waits []string) []string {
	t.Helper()
	var out []string
	for _, w := range waits {
		args := waitArgsOf(t, w)
		if deadline, _ := strconv.Atoi(args["deadline"]); args["quiet"] == strconv.Itoa(answerQuietMs) && deadline <= answerMaxMs {
			continue
		}
		out = append(out, w)
	}
	return out
}

// senderEvent returns when the fake sender logged the event, such as "sync 2".
func senderEvent(t *testing.T, e *testEnv, event string) time.Time {
	t.Helper()
	for _, r := range readInputLog(t, e.inputLog) {
		if r.Event == event {
			return time.Unix(0, r.Time)
		}
	}
	t.Fatalf("the sender logged no %q", event)
	return time.Time{}
}

// quietReply answers a wait as the fake does without onWait: the window drew three frames and went quiet.
func quietReply(w *fakeWindow) string {
	return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits + 3, "socketId": w.socketID(), "redrawObserved": true, "commitsSince": 3,
		"quietMs": 200, "elapsedMs": 412, "resizing": false, "animated": []hypr.IntRect{}})
}

const menuBatch = `[{"action":"click","x":20,"y":20},{"action":"key","keys":["down"]},{"action":"key","keys":["down"]},{"action":"key","keys":["enter"]}]`

// Keys after a click wait at the sync after it while act waits for the window to answer the click: for a change since
// the counter before the input, then for quiet. The sender goes on once that wait has ended, whether the window drew,
// as a menu that opens a moment after the click does, or drew nothing, after the short time of the change phase.
func TestKeysAfterAClickWaitForTheWindowsAnswer(t *testing.T) {
	slow(t)
	for _, c := range []struct {
		name, ended string
		// How long the window takes to answer, and the commits it draws meanwhile.
		answer  time.Duration
		commits int64
	}{
		{"a menu that opens late", "quiet", 400 * time.Millisecond, 5},
		{"a click that draws nothing", "no_redraw", answerExpectMs * time.Millisecond, 0},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
			e.screenshot(`{"window":"0xa1"}`)
			var replied time.Time
			e.fake.with(func() {
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					if args["quiet"] != strconv.Itoa(answerQuietMs) {
						var reply string
						e.fake.with(func() { reply = quietReply(w) })
						return reply
					}
					time.Sleep(c.answer)
					var reply string
					e.fake.with(func() {
						w.Commits += c.commits
						reply = jsonReply(map[string]any{"ok": true, "ended": c.ended, "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": c.commits > 0,
							"commitsSince": c.commits, "quietMs": answerQuietMs, "elapsedMs": c.answer.Milliseconds(), "resizing": false, "animated": []hypr.IntRect{}})
						replied = time.Now()
					})
					return reply
				}
			})
			res := e.act(`{"window":"0xa1","shot":"s1","actions":` + menuBatch + `}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 4.0, "total": 4.0, "delivery": "complete"}) {
				t.Errorf("actions %v", got)
			}
			want := [][]string{{"move", "10", "10", "click", "left", "1", "-", "sync", "300", "1000", "keys", "down", "1", "keys", "down", "1", "keys", "enter", "1"}}
			if got := e.inputs(); !reflect.DeepEqual(got, want) {
				t.Errorf("inputs %q", got)
			}
			// One wait for the answer, from the counter before the input, and the wait after the batch, from the same.
			waits := e.fake.requestsMatching("j/hyprmasq wait")
			if len(waits) != 2 || !strings.HasPrefix(waits[0], "j/hyprmasq wait 0xa1 since 10 expect 150 quiet 150 deadline 1000 id ") ||
				!strings.HasPrefix(waits[1], "j/hyprmasq wait 0xa1 since 10 expect 1000 quiet 200 ") {
				t.Fatalf("waits %q", waits)
			}
			// The click came first, then the wait, and the keys only once it had ended.
			synced, answered, waited := senderEvent(t, e, "sync 2"), senderEvent(t, e, "answered 2"), e.fake.timeOf("j/hyprmasq wait 0xa1 since 10 expect 150")
			if waited.Before(synced) || answered.Before(replied) || answered.Sub(replied) > 200*time.Millisecond {
				t.Errorf("synced %v, waited %v, replied %v, answered %v", synced, waited.Sub(synced), replied.Sub(synced), answered.Sub(synced))
			}
		})
	}
}

// A click alone or at the end of a batch has no sync after it, and only the wait after the batch. One after keys waits
// at a sync before it for the window's answer to the keys, as it would after a click, see closingPopup.
func TestAClickAtTheEndWaitsOnce(t *testing.T) {
	for _, c := range []struct {
		batch string
		syncs int
	}{
		{`[{"action":"click","x":20,"y":20}]`, 0},
		{`[{"action":"click","x":20,"y":20,"count":2}]`, 0},
		{`[{"action":"key","keys":["down"]},{"action":"type","text":"x"},{"action":"click","x":20,"y":20}]`, 1},
	} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
		e.screenshot(`{"window":"0xa1"}`)
		if res := e.act(`{"window":"0xa1","shot":"s1","actions":` + c.batch + `}`); res.IsError {
			t.Fatal(text(res))
		}
		if got := e.inputs(); len(got) != 1 || strings.Count(strings.Join(got[0], " "), "sync") != c.syncs {
			t.Errorf("%s: inputs %q", c.batch, got)
		}
		waits := e.fake.requestsMatching("j/hyprmasq wait")
		if len(waits) != 1+c.syncs || !strings.HasPrefix(waits[len(waits)-1], "j/hyprmasq wait 0xa1 since 10 expect 1000 quiet 200 ") || len(afterBatch(t, waits)) != 1 {
			t.Errorf("%s: waits %q", c.batch, waits)
		}
	}
}

// A window that closes while act waits for its answer to a click, as one does whose close button the click hit, ends
// the connection, and the sender fails at the sync. The click was delivered and the keys after it were not, so the
// result names them, and there is nothing to wait for or capture.
func TestWindowClosedWhileItAnswersAClick(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "fail:unconfirmed:2:partial:0")
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			e.fake.removeWindow("0xa1")
			return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 12, "socketId": w.socketID(), "redrawObserved": true, "commitsSince": 2,
				"quietMs": 0, "elapsedMs": 30, "resizing": false, "animated": []hypr.IntRect{}})
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":` + menuBatch + `}`)
	if code := errorCode(t, res); code != "window_gone" ||
		get(t, res, "error.message") != "window 0xa1 closed after actions[0] (click) was delivered, while actions[1] (key) was sent" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 1.0, "total": 4.0, "delivery": "partial", "failed_index": 1.0}) {
		t.Errorf("actions %v", got)
	}
	if hint, _ := get(t, res, "hint").(string); !strings.Contains(hint, "so do not send it again") {
		t.Errorf("hint %q", hint)
	}
	if get(t, res, "wait.ended") != "window_closed" || get(t, res, "window_closed") != true || hasImage(res) {
		t.Errorf("result %s", text(res))
	}
	if waits := e.fake.requestsMatching("j/hyprmasq wait"); len(waits) != 1 || len(afterBatch(t, waits)) != 0 {
		t.Errorf("waits %q", waits)
	}
}

// The wait for the answer to a click ends early enough that the rest of the batch fits before input has to end, and
// within answerMaxMs, also for a window that keeps drawing.
func TestTheAnswerToAClickKeepsTheDeadline(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
	e.screenshot(`{"window":"0xa1"}`)
	// The window keeps drawing until the wait for its answer is cancelled or its deadline passes.
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if args["quiet"] == strconv.Itoa(answerQuietMs) {
				deadline, _ := strconv.Atoi(args["deadline"])
				select {
				case <-time.After(time.Duration(deadline) * time.Millisecond):
				case <-cancel:
				}
			}
			var reply string
			e.fake.with(func() { reply = quietReply(w) })
			return reply
		}
	})

	// 70 characters take 1040 ms at their pace and 1144 ms with round trips, of the 1750 ms that deadline_ms 4000 leaves
	// for input, so the wait gets at most 606 ms.
	start := time.Now()
	res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":20,"y":20},{"action":"type","text":%q}],"deadline_ms":4000}`, strings.Repeat("x", 70)))
	if res.IsError || get(t, res, "actions.delivery") != "complete" {
		t.Fatalf("after %v: %s", time.Since(start), text(res))
	}
	waits := e.fake.requestsMatching("j/hyprmasq wait")
	if len(waits) != 2 {
		t.Fatalf("waits %q", waits)
	}
	args := waitArgsOf(t, waits[0])
	deadline, _ := strconv.Atoi(args["deadline"])
	expect, _ := strconv.Atoi(args["expect"])
	if args["quiet"] != "150" || deadline < 1 || deadline > 606 || expect != min(answerExpectMs, deadline) {
		t.Errorf("answer wait %q", waits[0])
	}

	// With time enough, the wait for a window that keeps drawing ends at answerMaxMs, and the sender goes on then.
	e = newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			if args["quiet"] != strconv.Itoa(answerQuietMs) {
				var reply string
				e.fake.with(func() { reply = quietReply(w) })
				return reply
			}
			<-cancel
			var reply string
			e.fake.with(func() {
				reply = jsonReply(map[string]any{"ok": true, "ended": "cancelled", "commits": w.Commits + 40, "socketId": w.socketID(), "redrawObserved": true,
					"commitsSince": 40, "quietMs": 0, "elapsedMs": answerMaxMs, "resizing": false, "animated": []hypr.IntRect{}})
			})
			return reply
		}
	})
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":` + menuBatch + `}`); res.IsError || get(t, res, "actions.delivery") != "complete" {
		t.Fatal(text(res))
	}
	synced, answered := senderEvent(t, e, "sync 2"), senderEvent(t, e, "answered 2")
	if took := answered.Sub(synced); took < answerMaxMs*time.Millisecond || took > answerMaxMs*time.Millisecond+500*time.Millisecond {
		t.Errorf("answered after %v", took)
	}
	if args := waitArgsOf(t, e.fake.requestsMatching("j/hyprmasq wait")[0]); args["deadline"] != "1000" {
		t.Errorf("answer wait %v", args)
	}
}
