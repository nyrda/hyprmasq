package server

import (
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// paintOnWaits answers the waits for input, as the fake does without onWait, after it has painted the window with the
// next of the paints, as the window draws what each part of a split batch did. The watch after the wait, which ignores
// animations, sees nothing more. With an answer, the first wait gets it instead, after the first paint.
func paintOnWaits(e *testEnv, answer func(w *fakeWindow, args map[string]string) string, paints ...func(x, y float64) uint8) {
	var mu sync.Mutex
	n := 0
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, _ <-chan struct{}) string {
			since, _ := strconv.ParseInt(args["since"], 10, 64)
			if args["ignore"] == "animations" {
				return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": since, "socketId": w.socketID(), "redrawObserved": false,
					"commitsSince": 0, "quietMs": 0, "elapsedMs": 100, "resizing": false})
			}
			if args["quiet"] == strconv.Itoa(catchUpQuietMs) {
				// A wait with no input before it, for what the window still has to draw, which uses up no paint.
				return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "socketId": w.socketID(),
					"redrawObserved": w.Commits > since, "commitsSince": w.Commits - since, "quietMs": catchUpQuietMs, "elapsedMs": catchUpQuietMs, "resizing": false})
			}
			mu.Lock()
			i := n
			n++
			mu.Unlock()
			e.fake.with(func() {
				w.Paint = paints[min(i, len(paints)-1)]
				w.Commits += 3
			})
			if i == 0 && answer != nil {
				return answer(w, args)
			}
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": since + 3, "socketId": w.socketID(), "redrawObserved": true,
				"commitsSince": 3, "quietMs": 200, "elapsedMs": 412, "resizing": false, "animated": []hypr.IntRect{}})
		}
	})
}

// stripes fills the window with horizontal stripes, as a page that scrolled to new content.
func stripes(x, y float64) uint8 {
	if int(y)/7%2 == 0 {
		return 255
	}
	return 0
}

// A scroll that ends a batch after a click goes out once the click has been waited for and captured: the result has the
// click's changes against the call's shot, and then the window after the scroll, against that capture, which is a full
// shot once the page moved, and otherwise the part that the scroll changed, as a dropdown that took the wheel.
func TestScrollAfterInputComesWithItsChanges(t *testing.T) {
	tick := block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	for _, c := range []struct {
		name   string
		scroll func(x, y float64) uint8
		after  string
		hint   string
		images int
	}{
		{"the page moved", stripes,
			"after the scroll: redraw observed · quiet 200 ms · 412 ms · shot s4 640×400 (full shot: 58% changed since s2)",
			"The zooms of s2 show what the actions before the scroll changed. Shot s4 shows the window after the scroll: read the next coordinates from s4.", 2},
		{"a dropdown took the wheel", func(x, y float64) uint8 {
			if x >= 200 && x < 240 && y >= 150 && y < 160 {
				return 255
			}
			return tick(x, y)
		},
			"after the scroll: redraw observed · quiet 200 ms · 412 ms · shot s4 = s2 + 1 change\nzoom s5 180×120 of s4 at 350,250 180×120",
			partScrolledHint(&actOut{Shot: &shotOut{Changes: &changesOut{Regions: []regionOut{{}}}}, Actions: &actionsOut{Delivery: "complete"}}, []action{{Kind: "scroll"}}), 2},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			paintOnWaits(e, nil, tick, c.scroll)
			res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}]}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			want := "2/2 delivered · lease 60 s\nbefore the scroll: redraw observed · quiet 200 ms · 412 ms · shot s2 = s1 + 1 change\nzoom s3 140×120 of s2 at 150,50 140×120\n" + c.after
			if got := summaryOf(t, res); got != want {
				t.Errorf("summary %q, want %q", got, want)
			}
			if got := get(t, res, "hint"); got != c.hint {
				t.Errorf("hint %q, want %q", got, c.hint)
			}
			// The scroll goes out in a sender of its own, after the click.
			if got, want := e.inputs(), [][]string{{"move", "5", "5", "click", "left", "1", "-"}, {"move", "5", "5", "wheel", "0", "3"}}; !reflect.DeepEqual(got, want) {
				t.Errorf("inputs %q, want %q", got, want)
			}
			if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 2.0, "total": 2.0, "delivery": "complete"}) {
				t.Errorf("actions %v", got)
			}
			if get(t, res, "before_scroll.shot.changes.base") != "s1" || get(t, res, "before_scroll.wait.ended") != "quiet" || get(t, res, "shot.id") != "s4" {
				t.Errorf("before_scroll %v, shot %v", get(t, res, "before_scroll"), get(t, res, "shot"))
			}
			if images(res) != c.images {
				t.Errorf("%d images", images(res))
			}
		})
	}
}

// The scroll stays unsent, and the result says why, when the input before it did not end where the scroll could take
// over: the window drew nothing, did not settle or opened a window. The result is then that of the input alone.
func TestScrollIsHeldBack(t *testing.T) {
	tick := block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	for _, c := range []struct {
		name, reason, wait string
		answer             func(e *testEnv) func(w *fakeWindow, args map[string]string) string
	}{
		{"nothing drawn", "the actions before it drew nothing, so they may have missed", "no redraw within 1000 ms",
			func(*testEnv) func(*fakeWindow, map[string]string) string {
				return func(w *fakeWindow, args map[string]string) string {
					since, _ := strconv.ParseInt(args["since"], 10, 64)
					return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": since, "socketId": w.socketID(), "redrawObserved": false,
						"commitsSince": 0, "quietMs": 0, "elapsedMs": 1000, "resizing": false})
				}
			}},
		{"still drawing", "the window had not settled after the actions before it", "timeout after 2000 ms, still drawing",
			func(*testEnv) func(*fakeWindow, map[string]string) string {
				return func(w *fakeWindow, args map[string]string) string {
					since, _ := strconv.ParseInt(args["since"], 10, 64)
					return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": since + 30, "socketId": w.socketID(), "redrawObserved": true,
						"commitsSince": 30, "quietMs": 10, "elapsedMs": 2000, "resizing": false})
				}
			}},
		{"a window opened", "the actions before it opened window 0xb2", "redraw observed · quiet 200 ms · 412 ms",
			func(e *testEnv) func(*fakeWindow, map[string]string) string {
				return func(w *fakeWindow, args map[string]string) string {
					e.window("0xb2", "2b", 200, 100, func(d *fakeWindow) { d.Source = &hypr.Source{Window: "0xa1", StableID: "1a"} })
					since, _ := strconv.ParseInt(args["since"], 10, 64)
					return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": since + 3, "socketId": w.socketID(), "redrawObserved": true,
						"commitsSince": 3, "quietMs": 200, "elapsedMs": 412, "resizing": false})
				}
			}},
	} {
		for _, unit := range []string{"notch", "page"} {
			t.Run(c.name+"/"+unit, func(t *testing.T) {
				e := newTestEnv(t)
				e.window("0xa1", "1a", 320, 200)
				e.screenshot(`{"window":"0xa1"}`)
				paintOnWaits(e, c.answer(e), tick)
				scroll := `{"action":"scroll","x":10,"y":10,"dy":3}`
				if unit == "page" {
					scroll = `{"action":"scroll","x":10,"y":10,"dy":1,"unit":"page"}`
				}
				res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10},` + scroll + `]}`)
				if res.IsError {
					t.Fatal(text(res))
				}
				if got := e.inputs(); !reflect.DeepEqual(got, [][]string{{"move", "5", "5", "click", "left", "1", "-"}}) {
					t.Errorf("inputs %q", got)
				}
				if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0, "held_back": c.reason}) {
					t.Errorf("actions %v", got)
				}
				if summary := summaryOf(t, res); !strings.HasPrefix(summary, "1/2 delivered, scroll held back · "+c.wait) || strings.Contains(summary, "before the scroll") {
					t.Errorf("summary %q", summary)
				}
				if hint, _ := get(t, res, "hint").(string); !strings.HasPrefix(hint, heldBackHint(&actionsOut{HeldBack: c.reason}, nil)) {
					t.Errorf("hint %q", hint)
				}
				// The shot is of the click's changes against the call's shot, as for the click alone.
				if get(t, res, "before_scroll") != nil || get(t, res, "shot.changes.base") != "s1" && get(t, res, "shot.full_shot") == nil {
					t.Errorf("shot %v, before_scroll %v", get(t, res, "shot"), get(t, res, "before_scroll"))
				}
			})
		}
	}
}

// A batch runs as one, as it did before act split a batch at its scroll, when the scroll does not end it, nothing but
// motion comes before it, or the capture is not of the changes.
func TestBatchesWithAScrollThatRunAsOne(t *testing.T) {
	for _, c := range []struct{ name, args string }{
		{"a scroll alone", `"actions":[{"action":"scroll","x":10,"y":10,"dy":3}]`},
		{"a scroll in the middle", `"actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3},{"action":"key","keys":["a"]}]`},
		{"a scroll in the middle and at the end", `"actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3},{"action":"key","keys":["a"]},{"action":"scroll","x":10,"y":10,"dy":1}]`},
		{"hover before the scroll", `"actions":[{"action":"move","x":20,"y":20},{"action":"scroll","x":10,"y":10,"dy":3}]`},
		{"a hover path before the scroll", `"actions":[["path",[[20,20],[60,40]],"line","none"],{"action":"scroll","x":10,"y":10,"dy":3}]`},
		{"capture window", `"actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}],"capture":"window"`},
		{"capture none", `"actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}],"capture":"none"`},
		{"capture region", `"actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}],"capture":{"shot":"s1","x":0,"y":0,"width":100,"height":100}`},
		{"too little time for two parts", `"actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}],"deadline_ms":3000`},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			res := e.act(`{"window":"0xa1","shot":"s1",` + c.args + `}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			if runs := e.inputs(); len(runs) != 1 || !strings.Contains(strings.Join(runs[0], " "), "wheel 0 3") {
				t.Errorf("inputs %q", runs)
			}
			if get(t, res, "before_scroll") != nil || strings.Contains(text(res), "the scroll") {
				t.Errorf("%s", text(res))
			}
		})
	}
}

// A call that ends after the actions before the scroll, as their window closes, holds the scroll back and says so.
func TestScrollIsHeldBackWhenTheWindowCloses(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, func(w *fakeWindow, args map[string]string) string {
		return jsonReply(map[string]any{"ok": true, "ended": "window_closed", "commits": 1, "redrawObserved": true, "commitsSince": 1, "quietMs": 0, "elapsedMs": 100})
	}, block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}))
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}]}`)
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0, "held_back": "the window closed"}) {
		t.Errorf("actions %v", got)
	}
	if got := e.inputs(); len(got) != 1 {
		t.Errorf("inputs %q", got)
	}
}

// A scroll whose sender fails after part of it went out has no wait of its own: the result does not give it the wait
// of the actions before it, which before_scroll has.
func TestAScrollThatFailsHasNoWaitOfItsOwn(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}), stripes)
	t.Setenv("FAKE_INPUT_MODE", "ok;fail:unconfirmed:1:partial:0")
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}]}`)
	if errorCode(t, res) != "unconfirmed" || get(t, res, "actions.delivery") != "partial" || get(t, res, "before_scroll.wait.ended") != "quiet" {
		t.Fatalf("%s", text(res))
	}
	if got := get(t, res, "wait"); got != nil {
		t.Errorf("wait %v", got)
	}
}

// A scroll sender that exits without a reply leaves the actions before the scroll delivered, and the scroll uncertain.
func TestAScrollSenderWithoutAReplyKeepsWhatWentBefore(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}), stripes)
	t.Setenv("FAKE_INPUT_MODE", "ok;crash")
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10},{"action":"scroll","x":10,"y":10,"dy":3}]}`)
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 1.0, "total": 2.0, "delivery": "uncertain", "failed_index": 1.0}) {
		t.Errorf("actions %v", got)
	}
}
