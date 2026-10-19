package server

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func waitArgsOf(t *testing.T, request string) map[string]string {
	t.Helper()
	words := strings.Fields(request)
	args := map[string]string{}
	for i := 3; i+1 < len(words); i += 2 {
		args[words[i]] = words[i+1]
	}
	return args
}

func TestWaitBaselines(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() { w.Commits = 50 })

	// Without actions, a change is measured from the shot.
	res := e.act(`{"window":"0xa1","shot":"s1","wait":{"expect_ms":20000},"deadline_ms":30000}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	waits := e.fake.requestsMatching("j/hyprmasq wait")
	args := waitArgsOf(t, waits[0])
	if args["since"] != "10" || args["expect"] != "20000" || args["quiet"] != "200" || args["id"] == "" {
		t.Errorf("wait %q", waits[0])
	}
	if d, _ := strconv.Atoi(args["deadline"]); d < 24000 || d > 29000 {
		t.Errorf("deadline %d, want about 30000 minus the capture reserve", d)
	}
	if got := get(t, res, "wait"); !reflect.DeepEqual(got, map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}) {
		t.Errorf("wait %v", got)
	}

	// With actions, from the counter read just before sending.
	e.fake.with(func() { w.Commits = 50 })
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}],"wait":{"expect_change":false,"quiet_ms":300},"deadline_ms":5000}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	waits = e.fake.requestsMatching("j/hyprmasq wait")
	args = waitArgsOf(t, waits[1])
	if args["since"] != "50" || args["expect"] != "" || args["quiet"] != "300" {
		t.Errorf("wait %q", waits[1])
	}

	// Without a shot, from the counter when the call starts.
	e.fake.with(func() { w.Commits = 70 })
	e.act(`{"window":"0xa1"}`)
	waits = e.fake.requestsMatching("j/hyprmasq wait")
	if args = waitArgsOf(t, waits[len(waits)-1]); args["since"] != "70" {
		t.Errorf("wait %q", waits[len(waits)-1])
	}

	// A socket opened again after the shot counts only commits after it, from 0, whether its counter is above the
	// shot's count or below.
	for i, commits := range []int64{30, 4} {
		e.fake.with(func() { w.SocketID, w.Commits = fmt.Sprintf("reopened-%d", i), commits })
		if res := e.act(`{"window":"0xa1","shot":"s1"}`); res.IsError {
			t.Fatal(text(res))
		}
		waits = e.fake.requestsMatching("j/hyprmasq wait")
		if args = waitArgsOf(t, waits[len(waits)-1]); args["since"] != "0" {
			t.Errorf("after %d commits of a new socket: wait %q", commits, waits[len(waits)-1])
		}
	}
}

func TestShotBaseline(t *testing.T) {
	state := func(socket string, commits int64) hypr.State {
		st := hypr.State{Commits: &commits}
		if socket != "" {
			st.SocketID = &socket
		}
		return st
	}
	for _, c := range []struct {
		shotSocket   string
		shotCommits  int64
		state        hypr.State
		want         int64
		wantSocketID string
	}{
		{"k1", 10, state("k1", 50), 10, "k1"},
		{"k1", 10, state("k2", 50), 0, "k2"},
		{"k1", 10, state("k2", 4), 0, "k2"},
		// A plugin that names no socket.
		{"", 10, state("", 50), 10, ""},
		{"", 10, state("", 4), 4, ""},
	} {
		if got := shotBaseline(&shot{SocketID: c.shotSocket, Commits: c.shotCommits}, c.state); got != (baseline{c.want, c.wantSocketID}) {
			t.Errorf("shot %d of %q, now %d of %q: %+v", c.shotCommits, c.shotSocket, *c.state.Commits, deref(c.state.SocketID), got)
		}
	}
}

// A socket opened again between the baseline and the wait counts from 0, so the wait counts all of its commits.
func TestWaitAfterTheSocketWasOpenedAgain(t *testing.T) {
	for _, commits := range []int64{2, 50} {
		t.Run(fmt.Sprint(commits), func(t *testing.T) {
			e := newTestEnv(t)
			w := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.SocketID, w.Commits = true, "k1", 30 })
			replaced := false
			e.fake.with(func() {
				e.fake.onPlugin = func(words []string) (string, bool) {
					if words[0] == "wait" && !replaced {
						replaced = true
						e.fake.with(func() { w.SocketID, w.Commits = "k2", commits })
					}
					return "", false
				}
			})
			if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`); res.IsError || get(t, res, "wait.ended") != "quiet" {
				t.Fatal(text(res))
			}
			waits := e.fake.requestsMatching("j/hyprmasq wait")
			if len(waits) != 2 || waitArgsOf(t, waits[0])["since"] != "30" || waitArgsOf(t, waits[1])["since"] != "0" {
				t.Errorf("waits %q", waits)
			}
		})
	}
}

func TestWaitEndings(t *testing.T) {
	for _, c := range []struct {
		ended   string
		redrawn bool
		summary any
		text    string
	}{
		{"no_redraw", false, "no_change", "1/1 delivered · no redraw within 1000 ms"},
		// A timeout tells a window that kept drawing from one that drew nothing, such as one whose change phase ran
		// into the deadline.
		{"timeout", true, "busy", "1/1 delivered · timeout after 1000 ms, still drawing"},
		{"timeout", false, "no_change", "1/1 delivered · timeout after 1000 ms, no redraw"},
		{"window_closed", false, nil, "1/1 delivered · window closed"},
	} {
		t.Run(fmt.Sprintf("%s redrawn %v", c.ended, c.redrawn), func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			e.fake.with(func() {
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					return jsonReply(map[string]any{"ok": true, "ended": c.ended, "commits": 0, "redrawObserved": c.redrawn, "commitsSince": 0, "quietMs": 0, "elapsedMs": 1000})
				}
			})
			res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}],"capture":"window"}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			if got := get(t, res, "wait.ended"); got != c.ended {
				t.Errorf("ended %v", got)
			}
			if got := get(t, res, "wait.summary"); got != c.summary {
				t.Errorf("summary %v", got)
			}
			if !strings.HasPrefix(text(res), c.text) {
				t.Errorf("text %q", text(res))
			}
			if captured := hasImage(res); captured == (c.ended == "window_closed") {
				t.Errorf("captured %v", captured)
			}
		})
	}
}

// Without a shot, an act without actions measures a change from the call's start, so its no_redraw hint speaks of the
// call: the window may have drawn after the agent's last shot, as the result's own shot then shows.
func TestNoRedrawWithoutAShot(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() {
		w.Commits += 5
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
				"commitsSince": 0, "quietMs": 1000, "elapsedMs": 1000, "resizing": false})
		}
	})
	res := e.act(`{"window":"0xa1","actions":[],"capture":"window"}`)
	if hint := get(t, res, "hint"); hint != waitHint(&waitOut{Ended: "no_redraw"}, nil, "", &actionsOut{}, nil) {
		t.Errorf("%s", text(res))
	}
}

// blockingWait makes waits run until they are cancelled or release is closed.
func blockingWait(release <-chan struct{}) func(*fakeWindow, map[string]string, <-chan struct{}) string {
	return func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
		ended := "quiet"
		select {
		case <-cancel:
			ended = "cancelled"
		case <-release:
		}
		return jsonReply(map[string]any{"ok": true, "ended": ended, "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 10, "elapsedMs": 10})
	}
}

func TestCancelWhileWaiting(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq wait") != nil })
		cancel()
	}()
	res := callTool(e.server, ctx, actOn, `{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["a"]}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := get(t, res, "wait.ended"); got != "cancelled" {
		t.Errorf("ended %v", got)
	}
	if got := get(t, res, "actions.delivery"); got != "complete" {
		t.Errorf("delivery %v", got)
	}
	cancels := e.fake.requestsMatching("j/hyprmasq cancel ")
	wait := e.fake.requestsMatching("j/hyprmasq wait")[0]
	if len(cancels) == 0 || !strings.HasSuffix(wait, " id "+strings.TrimPrefix(cancels[0], "j/hyprmasq cancel ")) {
		t.Errorf("cancel %q for wait %q", cancels, wait)
	}
}

// stalledPlugin makes the fake answer no plugin request once a wait has started, as when Hyprland's main thread stalls,
// until release is closed.
func stalledPlugin(e *testEnv, release <-chan struct{}) {
	stalled := make(chan struct{})
	var once sync.Once
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			once.Do(func() { close(stalled) })
			<-release
			return quietReply(w)
		}
		e.fake.onPlugin = func(words []string) (string, bool) {
			select {
			case <-stalled:
				<-release
			default:
			}
			return "", false
		}
	})
}

// A cancelled call ends within cancelGrace of the cancel and the capture's reserve, and releases the window's lock, also
// when the plugin takes the cancel but its wait goes on, or answers nothing at all. A reply that comes after the cancel
// reports the cancellation, not how the wait ended.
func TestCancelWithAPluginThatDoesNotEndTheWait(t *testing.T) {
	slow(t)
	for _, plugin := range []string{"wait goes on", "late reply", "no answers"} {
		t.Run(plugin, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			release := make(chan struct{})
			t.Cleanup(func() { close(release) })
			timeout := func(w *fakeWindow) string {
				return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
					"commitsSince": 0, "quietMs": 0, "elapsedMs": 14000, "resizing": false})
			}
			switch plugin {
			case "wait goes on":
				e.fake.with(func() {
					e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
						<-release
						return timeout(w)
					}
				})
			case "late reply":
				e.fake.with(func() {
					e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
						<-cancel
						time.Sleep(cancelGrace / 2)
						return timeout(w)
					}
				})
			case "no answers":
				stalledPlugin(e, release)
			}
			ctx, cancel := context.WithCancel(context.Background())
			cancelled := make(chan time.Time, 1)
			go func() {
				e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq wait") != nil })
				cancelled <- time.Now()
				cancel()
			}()
			res := callTool(e.server, ctx, actOn, `{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["a"]}],"deadline_ms":15000}`)
			if after := time.Since(<-cancelled); after > cancelGrace+captureReserve+300*time.Millisecond {
				t.Errorf("the call returned %v after the cancel", after)
			}
			if res.IsError || get(t, res, "wait.ended") != "cancelled" {
				t.Errorf("%s", text(res))
			}
			lockCtx, lockCancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
			defer lockCancel()
			lock, err := acquireLock(lockCtx, e.server.lockPath("1a"))
			if err != nil {
				t.Fatalf("the window lock is still held: %v", err)
			}
			lock.release()
		})
	}
}

// deadline_ms bounds a call within waitSlack also when the plugin answers its wait late or not at all: the server gives
// the reply up waitSlack after the wait's deadline, and reports that the plugin did not answer.
func TestDeadlineWithAPluginThatAnswersAWaitLate(t *testing.T) {
	slow(t)
	for _, c := range []struct{ plugin, args string }{
		{"late reply", `{"window":"0xa1","shot":"s1","actions":[],"capture":"window","deadline_ms":3000}`},
		{"no answers", `{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["a"]}],"deadline_ms":3000}`},
	} {
		t.Run(c.plugin, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			release := make(chan struct{})
			t.Cleanup(func() { close(release) })
			if c.plugin == "no answers" {
				stalledPlugin(e, release)
			} else {
				e.fake.with(func() {
					e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
						deadline, _ := strconv.Atoi(args["deadline"])
						select {
						case <-time.After(time.Duration(deadline)*time.Millisecond + 2*waitSlack):
						case <-release:
						}
						return quietReply(w)
					}
				})
			}
			start := time.Now()
			res := e.act(c.args)
			if elapsed := time.Since(start); elapsed > 3000*time.Millisecond+waitSlack+200*time.Millisecond {
				t.Errorf("the call took %v", elapsed)
			}
			if code := get(t, res, "error.code"); code != "internal" || get(t, res, "error.message") != "the plugin did not answer a wait within 500 ms of its deadline" {
				t.Errorf("code %v: %s", code, text(res))
			}
		})
	}
}

// After input that can open a window, act goes on waiting through the grace period, and a redraw in that time, such as
// a page that Enter loads, is waited out before the capture.
func TestRedrawAfterSettling(t *testing.T) {
	slow(t)
	quiet := func(commits, since, elapsed int) string {
		return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": commits, "redrawObserved": since > 0, "commitsSince": since, "quietMs": 200, "elapsedMs": elapsed})
	}
	noRedraw := jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": 10, "redrawObserved": false, "commitsSince": 0, "quietMs": 0, "elapsedMs": 1000})
	onSocket := func(reply, socket string) string {
		var m map[string]any
		_ = json.Unmarshal([]byte(reply), &m)
		m["socketId"] = socket
		return jsonReply(m)
	}
	for _, c := range []struct {
		name    string
		actions string // and further arguments
		first   string
		watch   string // the reply to the second wait, or "" when there must be none
		summary string // in the result's text
		wait    map[string]any
	}{
		{"drew again", `[{"action":"click","x":5,"y":5}]`, quiet(13, 3, 412), quiet(17, 4, 380),
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · drew again, settled 380 ms later · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 7.0, "quiet_ms": 200.0, "elapsed_ms": 412.0,
				"again": map[string]any{"quiet_ms": 200.0, "elapsed_ms": 380.0}}},
		{"drew only after the change phase", `[{"action":"key","keys":["enter"]}]`, noRedraw, quiet(12, 2, 300),
			"1/1 delivered · redraw observed · quiet 200 ms · 1300 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 2.0, "quiet_ms": 200.0, "elapsed_ms": 1300.0}},
		{"still drawing", `[{"action":"type","text":"query\n"}]`, quiet(13, 3, 412),
			jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": 53, "redrawObserved": true, "commitsSince": 40, "quietMs": 16, "elapsedMs": 9000}),
			"1/1 delivered · timeout after 9412 ms, still drawing · shot",
			map[string]any{"ended": "timeout", "summary": "busy", "redraw_observed": true, "commits": 43.0, "quiet_ms": 16.0, "elapsed_ms": 9412.0}},
		// The plugin ends a wait whose window closes with window_closed, and refuses one whose window is gone already.
		{"closed", `[{"action":"click","x":5,"y":5}]`, quiet(13, 3, 412),
			jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 14, "redrawObserved": true, "commitsSince": 1, "quietMs": 1, "elapsedMs": 180}),
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · window closed",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}},
		{"gone", `[{"action":"click","x":5,"y":5}]`, quiet(13, 3, 412), pluginError("window_gone", "no mapped window 0xa1"),
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · window closed",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}},
		{"idle", `[{"action":"click","x":5,"y":5}]`, quiet(13, 3, 412), jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": 13, "redrawObserved": false, "commitsSince": 0, "quietMs": 0, "elapsedMs": 480}),
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}},
		{"drew only after a quiet wait", `[{"action":"click","x":5,"y":5}],"wait":{"expect_change":false}`, quiet(10, 0, 300), quiet(12, 2, 300),
			"1/1 delivered · redraw observed · quiet 200 ms · 600 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 2.0, "quiet_ms": 200.0, "elapsed_ms": 600.0}},
		{"another socket", `[{"action":"click","x":5,"y":5}]`, onSocket(quiet(13, 3, 412), "k1"), onSocket(quiet(20, 20, 300), "k2"),
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}},
		// The first wait ends 550 ms before the capture's reserve, which leaves less than the grace.
		{"short deadline", `[{"action":"click","x":5,"y":5}],"deadline_ms":3000`, quiet(13, 3, 412), quiet(17, 4, 300),
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · drew again, settled 300 ms later · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 7.0, "quiet_ms": 200.0, "elapsed_ms": 412.0,
				"again": map[string]any{"quiet_ms": 200.0, "elapsed_ms": 300.0}}},
		// A spinner that was going on before the input ends no change phase, but its commits count and the capture
		// leaves it out.
		{"animation only", `[{"action":"click","x":5,"y":5}]`, quiet(13, 3, 412),
			jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": 19, "redrawObserved": true, "commitsSince": 6, "quietMs": 480, "elapsedMs": 480,
				"animated": []map[string]any{{"x": 10, "y": 20, "width": 30, "height": 40}}}),
			"1/1 delivered · redraw observed · quiet 200 ms except animation at 20,40 60×80 · 412 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 9.0, "quiet_ms": 200.0, "elapsed_ms": 412.0,
				"animated": []any{map[string]any{"x": 20.0, "y": 40.0, "width": 60.0, "height": 80.0}}}},
		// An animation that goes on through both waits is one.
		{"one animation in both waits", `[{"action":"click","x":5,"y":5}]`,
			jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": 13, "redrawObserved": true, "commitsSince": 3, "quietMs": 200, "elapsedMs": 412,
				"animated": []map[string]any{{"x": 10, "y": 20, "width": 30, "height": 40}}}),
			jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": 19, "redrawObserved": true, "commitsSince": 6, "quietMs": 480, "elapsedMs": 480,
				"animated": []map[string]any{{"x": 11, "y": 20, "width": 30, "height": 40}}}),
			"1/1 delivered · redraw observed · quiet 200 ms except animation at 20,40 62×80 · 412 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 9.0, "quiet_ms": 200.0, "elapsed_ms": 412.0,
				"animated": []any{map[string]any{"x": 20.0, "y": 40.0, "width": 62.0, "height": 80.0}}}},
		// The lease's round trip takes the rest of the grace, so no watch follows it.
		{"slow lease", `[{"action":"click","x":5,"y":5}]`, quiet(13, 3, 412), "",
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}},
		{"typing that opens no window", `[{"action":"type","text":"query"}]`, quiet(13, 3, 412), "",
			"1/1 delivered · redraw observed · quiet 200 ms · 412 ms · shot",
			map[string]any{"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 3.0, "quiet_ms": 200.0, "elapsed_ms": 412.0}},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.server.cfg.NewWindowGrace = 500 * time.Millisecond
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.SocketID, w.Commits = true, "k1", 10 })
			e.screenshot(`{"window":"0xa1"}`)
			var waits []map[string]string
			e.fake.with(func() {
				if c.name == "slow lease" {
					e.fake.onPlugin = func(words []string) (string, bool) {
						if words[0] == "lease" && len(waits) == 1 {
							time.Sleep(600 * time.Millisecond)
						}
						return "", false
					}
				}
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					waits = append(waits, args)
					if len(waits) > 1 {
						return c.watch
					}
					if c.name == "short deadline" {
						// Until 550 ms before the wait's deadline, the capture's reserve, which leaves 300 ms for the
						// watch before the listing's.
						deadline, _ := strconv.Atoi(args["deadline"])
						time.Sleep(time.Duration(deadline-550) * time.Millisecond)
					}
					var reply struct{ Commits int64 }
					_ = json.Unmarshal([]byte(c.first), &reply)
					e.fake.with(func() { w.Commits = reply.Commits })
					return c.first
				}
			})
			res := e.act(`{"window":"0xa1","shot":"s1","actions":` + c.actions + `}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			if !strings.HasPrefix(text(res), c.summary) {
				t.Errorf("summary %q, want it to start with %q", text(res), c.summary)
			}
			if got := get(t, res, "wait"); !reflect.DeepEqual(got, c.wait) {
				t.Errorf("wait %v, want %v", got, c.wait)
			}
			if c.watch == "" {
				if len(waits) != 1 {
					t.Errorf("waits %v, want one", waits)
				}
				return
			}
			if len(waits) != 2 {
				t.Fatalf("waits %v, want two", waits)
			}
			// The second wait counts from where the first one ended, for the rest of the grace period, or less when the
			// deadline leaves less, with the quiet time for a redraw at its end.
			expect, _ := strconv.Atoi(waits[1]["expect"])
			deadline, _ := strconv.Atoi(waits[1]["deadline"])
			if waits[1]["since"] != "13" && waits[1]["since"] != "10" || expect < 1 || expect > 500 || expect > deadline-200 || waits[1]["quiet"] != "200" ||
				c.name == "short deadline" && expect > 400 {
				t.Errorf("second wait %v", waits[1])
			}
			// Animation that was going on before the input does not count as drawing again.
			if waits[1]["ignore"] != "animations" {
				t.Errorf("second wait %v", waits[1])
			}
			// The lease is renewed before it, and the capture follows it.
			requests := e.fake.requestsMatching("j/hyprmasq")
			var waitAt []int
			lastWait, capture, lease := -1, -1, -1
			for i, r := range requests {
				switch {
				case strings.HasPrefix(r, "j/hyprmasq wait"):
					lastWait = i
					waitAt = append(waitAt, i)
				case strings.HasPrefix(r, "j/hyprmasq lease 0xa1") && len(waitAt) == 1:
					lease = i
				case strings.HasPrefix(r, "j/hyprmasq screenshot 0xa1") && lastWait >= 0:
					capture = i
				}
			}
			if lease < 0 {
				t.Errorf("no lease renewed between the waits: %q", requests)
			}
			if c.name == "closed" || c.name == "gone" {
				if get(t, res, "window_closed") != true || capture >= 0 {
					t.Errorf("a closed window was captured: %q", requests)
				}
			} else if capture < lastWait {
				t.Errorf("captured before the second wait: %q", requests)
			}
		})
	}
}
