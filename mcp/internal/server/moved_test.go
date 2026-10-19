package server

import (
	"fmt"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A form is a window's content: dark blocks on white, which a test moves and changes, blocks in a faint gray, 33
// levels darker than the white, as the border of Forgejo's text fields, and a block whose gray level changes with every
// screenshot, as a video or an animation does.
type form struct {
	mu     sync.Mutex
	blocks []hypr.Rect
	faint  []hypr.Rect
	live   *hypr.Rect
	frames int
}

// animate makes the block keep changing.
func (f *form) animate(r hypr.Rect) {
	f.mu.Lock()
	f.live = &r
	f.mu.Unlock()
}

func (f *form) paint(x, y float64) uint8 {
	f.mu.Lock()
	defer f.mu.Unlock()
	in := func(r hypr.Rect) bool { return x >= r.X && x < r.X+r.Width && y >= r.Y && y < r.Y+r.Height }
	if f.live != nil && in(*f.live) {
		return uint8(f.frames * 60 % 256)
	}
	for _, r := range f.blocks {
		if in(r) {
			return 30
		}
	}
	for _, r := range f.faint {
		if in(r) {
			return 222
		}
	}
	return 255
}

// formWindow opens a 320×200 window with an open socket that shows the form, at the fake's scale of 2.
func formWindow(e *testEnv, f *form) *fakeWindow {
	w := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits, w.Paint = true, 10, f.paint })
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			f.mu.Lock()
			f.frames++
			f.mu.Unlock()
			return ""
		}
	})
	return w
}

// redraw changes the form, and the window commits the frame that shows it.
func (e *testEnv) redraw(w *fakeWindow, f *form, blocks ...hypr.Rect) {
	f.mu.Lock()
	f.blocks = blocks
	f.mu.Unlock()
	e.fake.with(func() { w.Commits += 2 })
}

// The screenshots that the check of the targets took, which go to files of their own.
var checkCapture = regexp.MustCompile(`^j/hyprmasq screenshot 0xa1 \S+/t\d+\.raw`)

func (e *testEnv) checkCaptures() []string {
	var out []string
	for _, r := range e.fake.requestsMatching("j/hyprmasq screenshot") {
		if checkCapture.MatchString(r) {
			out = append(out, r)
		}
	}
	return out
}

var (
	// A button at 100,80 of 40×30, whose middle, 120,95, is 240,190 in shots of the whole window at scale 2.
	button = hypr.Rect{X: 100, Y: 80, Width: 40, Height: 30}
	// Elsewhere in the window, far from the button.
	elsewhere = hypr.Rect{X: 250, Y: 150, Width: 40, Height: 30}
)

func moved(r hypr.Rect, dy float64) hypr.Rect {
	r.Y += dy
	return r
}

// Before a batch clicks or drags at a shot's coordinates, act compares the window around each such point with the shot,
// once the window has drawn since it, and sends nothing when the content there changed and holds still: it refuses
// with stale_shot, a new shot, and names the action. Changes elsewhere, a caret, the animations that the wait before
// the shot saw, and content that keeps changing do not refuse. The points map to the window through the shot they come
// from, also a zoom, a changes shot or an overview shrunk below the logical pixels.
func TestTargetsThatMovedSinceTheShot(t *testing.T) {
	slow(t)
	for _, c := range []struct {
		name string
		// The content when the shot is taken, and after.
		before, after []hypr.Rect
		// Takes the shot that the act gives, returning its id; nil takes a screenshot.
		shot    func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string
		actions string
		// The action that the refusal names with its point, or "" when the batch is sent.
		refused string
		// How many screenshots the check takes.
		captures int
		// The request of the check's screenshot, after the path.
		request string
		// The target is gone, so the coordinates do not lie in the new shot.
		gone bool
	}{
		{name: "a button that moved", before: []hypr.Rect{button}, after: []hypr.Rect{moved(button, 60)},
			actions: `[{"action":"click","x":240,"y":190}]`, refused: "actions[0] (click) at 240,190", captures: 2, request: " popups"},
		{name: "a later click of the batch", before: []hypr.Rect{button, elsewhere}, after: []hypr.Rect{moved(button, 60), elsewhere},
			actions: `[{"action":"click","x":540,"y":330},{"action":"type","text":"x"},{"action":"click","x":240,"y":190}]`, refused: "actions[2] (click) at 240,190", captures: 2},
		{name: "the end of a drag", before: []hypr.Rect{button, elsewhere}, after: []hypr.Rect{button, moved(elsewhere, -40)},
			actions: `[{"action":"drag","from":[240,190],"to":[540,330]}]`, refused: "actions[0] (drag) to 540,330", captures: 2},
		{name: "an edge that crosses the box", before: []hypr.Rect{{X: 0, Y: 90, Width: 320, Height: 1}}, after: []hypr.Rect{{X: 0, Y: 108, Width: 320, Height: 1}},
			actions: `[{"action":"click","x":440,"y":200}]`, refused: "actions[0] (click) at 440,200", captures: 2},
		{name: "the faint border of an empty field", shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
			// A field from 85 to 106 that moves 40 pixels down, away from a click in its middle, at 220,95.
			e.redraw(w, f)
			f.mu.Lock()
			f.faint = []hypr.Rect{{X: 0, Y: 85, Width: 320, Height: 1}, {X: 0, Y: 105, Width: 320, Height: 1}}
			f.mu.Unlock()
			id := shotID(t, e.screenshot(`{"window":"0xa1"}`))
			f.mu.Lock()
			f.faint = []hypr.Rect{{X: 0, Y: 125, Width: 320, Height: 1}, {X: 0, Y: 145, Width: 320, Height: 1}}
			f.mu.Unlock()
			return id
		},
			actions: `[{"action":"click","x":440,"y":190}]`, refused: "actions[0] (click) at 440,190", captures: 2},
		{name: "a zoom", before: []hypr.Rect{button}, after: []hypr.Rect{moved(button, 60)},
			// The zoom shows 80,60 to 200,140 at scale 2, so the button's middle is at 80,70 in it.
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.screenshot(`{"window":"0xa1"}`)
				return shotID(t, e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":160,"y":120,"width":240,"height":160}}`))
			},
			actions: `[{"action":"click","x":80,"y":70}]`, refused: "actions[0] (click) at 80,70", captures: 2, request: " 80 60 120 80 popups"},
		{name: "a changes shot", before: []hypr.Rect{button}, after: []hypr.Rect{moved(button, 60), elsewhere},
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.screenshot(`{"window":"0xa1"}`)
				e.redraw(w, f, button, elsewhere)
				res := e.act(`{"window":"0xa1","shot":"s1"}`)
				if get(t, res, "shot.changes.base") != "s1" {
					t.Fatalf("no changes shot: %s", text(res))
				}
				return shotID(t, res)
			},
			actions: `[{"action":"click","x":240,"y":190}]`, refused: "actions[0] (click) at 240,190", captures: 2, request: " popups"},
		{name: "a zoom of a changes shot", before: []hypr.Rect{button}, after: []hypr.Rect{moved(elsewhere, 10)},
			// The zoom is cut from the detail image that the changes were found in, and shows 225,125 to 315,200.
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.screenshot(`{"window":"0xa1"}`)
				e.redraw(w, f, button, elsewhere)
				res := e.act(`{"window":"0xa1","shot":"s1"}`)
				if get(t, res, "shot.changes.regions.0.x") != 450.0 {
					t.Fatalf("zoom: %s", text(res))
				}
				return get(t, res, "shot.changes.regions.0.shot").(string)
			},
			actions: `[{"action":"click","x":88,"y":60}]`, refused: "actions[0] (click) at 88,60", captures: 2, request: " popups"},
		{name: "a popup past the window's edge that closed", before: []hypr.Rect{button}, after: []hypr.Rect{button},
			// The shot shows a popup that reaches 80 pixels past the window's right edge, and the click is in it.
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.fake.with(func() { w.PopupBox = &hypr.Rect{Width: 400, Height: 200} })
				id := shotID(t, e.screenshot(`{"window":"0xa1"}`))
				e.fake.with(func() { w.PopupBox = nil })
				return id
			},
			actions: `[{"action":"click","x":700,"y":100}]`, refused: "actions[0] (click) at 700,100", captures: 2, request: " popups", gone: true},
		{name: "an overview smaller than the window", before: []hypr.Rect{button}, after: []hypr.Rect{moved(button, 60)},
			// The overview has half of the logical pixels, and its detail image all of them, which the check compares.
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.server.cfg.MaxImageSize = 160
				return shotID(t, e.screenshot(`{"window":"0xa1"}`))
			},
			actions: `[{"action":"click","x":60,"y":47.5}]`, refused: "actions[0] (click) at 60,47.5", captures: 2, request: " max 320 popups"},

		{name: "the base of a changes shot that showed a change there", before: []hypr.Rect{button},
			after: []hypr.Rect{button, {X: 100, Y: 112, Width: 40, Height: 6}, elsewhere},
			// The agent passes s1, whose coordinates s2 keeps, after s2 showed the bar below the button in a zoom.
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.screenshot(`{"window":"0xa1"}`)
				e.redraw(w, f, button, hypr.Rect{X: 100, Y: 112, Width: 40, Height: 6})
				if res := e.act(`{"window":"0xa1","shot":"s1"}`); get(t, res, "shot.changes.base") != "s1" {
					t.Fatalf("no changes shot: %s", text(res))
				}
				return "s1"
			},
			actions: `[{"action":"click","x":240,"y":190}]`, captures: 1},
		{name: "nothing changed there", before: []hypr.Rect{button}, after: []hypr.Rect{button},
			actions: `[{"action":"click","x":240,"y":190}]`, captures: 1},
		{name: "a change elsewhere", before: []hypr.Rect{button}, after: []hypr.Rect{button, elsewhere},
			actions: `[{"action":"click","x":240,"y":190}]`, captures: 1},
		{name: "a caret next to the button", before: []hypr.Rect{button}, after: []hypr.Rect{button, {X: 143, Y: 86, Width: 2, Height: 18}},
			actions: `[{"action":"click","x":240,"y":190}]`, captures: 1},
		// The bar below the button keeps changing, between the captures of the check too.
		{name: "an animation that the wait before the shot saw", before: []hypr.Rect{button}, after: []hypr.Rect{button},
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.fake.with(func() { w.Animated = []hypr.IntRect{{X: 100, Y: 112, Width: 40, Height: 6}} })
				f.animate(hypr.Rect{X: 100, Y: 112, Width: 30, Height: 6})
				return shotID(t, e.act(`{"window":"0xa1","capture":"window"}`))
			},
			actions: `[{"action":"click","x":240,"y":190}]`, captures: 2},
		{name: "a zoom read from a shot with an animation", before: []hypr.Rect{button}, after: []hypr.Rect{button},
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.fake.with(func() { w.Animated = []hypr.IntRect{{X: 100, Y: 112, Width: 40, Height: 6}} })
				f.animate(hypr.Rect{X: 100, Y: 112, Width: 30, Height: 6})
				id := shotID(t, e.act(`{"window":"0xa1","capture":"window"}`))
				return shotID(t, e.screenshot(`{"window":"0xa1","region":{"shot":"`+id+`","x":160,"y":120,"width":240,"height":160}}`))
			},
			actions: `[{"action":"click","x":80,"y":70}]`, captures: 2},
		// An application can damage much more than it changes, as Thunderbird damages tiles of 1024×512 logical pixels
		// around a blinking caret, so a target can move within the box of an animation, also next to a part of it that
		// keeps changing.
		{name: "a button that moved within an animation", before: []hypr.Rect{button}, after: []hypr.Rect{moved(button, 60)},
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.fake.with(func() { w.Animated = []hypr.IntRect{{X: 0, Y: 0, Width: 320, Height: 200}} })
				return shotID(t, e.act(`{"window":"0xa1","capture":"window"}`))
			},
			actions: `[{"action":"click","x":240,"y":190}]`, refused: "actions[0] (click) at 240,190", captures: 2},
		{name: "a button that moved next to an animation", before: []hypr.Rect{button}, after: []hypr.Rect{moved(button, 60)},
			shot: func(t *testing.T, e *testEnv, w *fakeWindow, f *form) string {
				e.fake.with(func() { w.Animated = []hypr.IntRect{{X: 0, Y: 0, Width: 320, Height: 200}} })
				f.animate(hypr.Rect{X: 100, Y: 112, Width: 30, Height: 6})
				return shotID(t, e.act(`{"window":"0xa1","capture":"window"}`))
			},
			actions: `[{"action":"click","x":240,"y":190}]`, refused: "actions[0] (click) at 240,190", captures: 2},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			f := &form{}
			w := formWindow(e, f)
			e.redraw(w, f, c.before...)
			shot := "s1"
			if c.shot != nil {
				shot = c.shot(t, e, w, f)
			} else {
				e.screenshot(`{"window":"0xa1"}`)
			}
			e.redraw(w, f, c.after...)
			res := e.act(`{"window":"0xa1","shot":"` + shot + `","actions":` + c.actions + `}`)
			if captures := e.checkCaptures(); len(captures) != c.captures || c.request != "" && !strings.HasSuffix(captures[0], c.request+" raw") {
				t.Errorf("the check's screenshots %q", captures)
			}
			if c.refused == "" {
				if res.IsError || get(t, res, "actions.delivery") != "complete" || len(e.inputs()) != 1 {
					t.Fatalf("not sent: %s", text(res))
				}
				return
			}
			if code := errorCode(t, res); code != "stale_shot" || len(e.inputs()) != 0 || get(t, res, "actions.delivery") != "none" {
				t.Fatalf("code %q, inputs %q: %s", code, e.inputs(), text(res))
			}
			action := c.refused[:strings.Index(c.refused, ")")+1]
			message := fmt.Sprintf("the window changed since shot %s around %s, so the input could reach something other than what the shot shows there", shot, c.refused)
			hint := fmt.Sprintf("The target of %s moved or changed since %s. Read the coordinates from the new shot in this result. Nothing was sent.", action, shot)
			if get(t, res, "error.message") != message || get(t, res, "hint") != hint {
				t.Errorf("result: %s", text(res))
			}
			// The new shot is of the whole window, as it is now, and takes the coordinates that the agent reads anew.
			next := shotID(t, res)
			if get(t, res, "shot.changes") != nil || images(res) != 1 {
				t.Fatalf("shot: %s", text(res))
			}
			if res := e.act(`{"window":"0xa1","shot":"` + next + `","actions":` + c.actions + `}`); !c.gone && (res.IsError || get(t, res, "actions.delivery") != "complete") {
				t.Errorf("with the new shot: %s", text(res))
			}
		})
	}
}

// A window that has not drawn since the shot has its counter where the shot left it, so act sends at once, without
// capturing it.
func TestTargetsOfAWindowThatDidNotDrawAreNotCaptured(t *testing.T) {
	e := newTestEnv(t)
	f := &form{blocks: []hypr.Rect{button}}
	formWindow(e, f)
	e.screenshot(`{"window":"0xa1"}`)
	before := len(e.fake.requestsMatching("j/hyprmasq screenshot"))
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":240,"y":190},{"action":"drag","from":[240,190],"to":[10,10]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.delivery") != "complete" {
		t.Fatal(text(res))
	}
	if after := len(e.fake.requestsMatching("j/hyprmasq screenshot")); after != before {
		t.Errorf("%d screenshots", after-before)
	}
}

// Content that keeps changing around a target, such as a video, which the plugin does not take for an animation, would
// never match a shot: after the window has had time to settle, it is still changing there, so the click is sent.
func TestTargetsInContentThatKeepsChanging(t *testing.T) {
	e := newTestEnv(t)
	f := &form{blocks: []hypr.Rect{button}, live: &hypr.Rect{X: 102, Y: 82, Width: 20, Height: 20}}
	w := formWindow(e, f)
	e.screenshot(`{"window":"0xa1"}`)
	e.fake.with(func() { w.Commits += 2 })
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":240,"y":190}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.delivery") != "complete" {
		t.Fatal(text(res))
	}
	// It got a wait to settle, between two captures.
	if len(e.checkCaptures()) != 2 || len(e.fake.requestsMatching("j/hyprmasq wait 0xa1 since 12 quiet 200 ")) != 1 {
		t.Errorf("captures %q, waits %q", e.checkCaptures(), e.fake.requestsMatching("j/hyprmasq wait"))
	}
}

// The check ends early enough for the batch to fit before input has to end, and a check that runs out of time sends
// nothing. The wait for a change to settle leaves the capture after it time too.
func TestTheCheckOfTheTargetsKeepsTheDeadline(t *testing.T) {
	slow(t)
	// 70 characters take 1144 ms with round trips, of the 1750 ms that deadline_ms 4000 leaves for input.
	batch := fmt.Sprintf(`[{"action":"click","x":240,"y":190},{"action":"type","text":%q}]`, strings.Repeat("x", 70))
	e := newTestEnv(t)
	f := &form{blocks: []hypr.Rect{button}}
	w := formWindow(e, f)
	e.screenshot(`{"window":"0xa1"}`)
	e.redraw(w, f, moved(button, 60))
	// The window takes a second for a screenshot.
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			time.Sleep(time.Second)
			return ""
		}
	})
	start := time.Now()
	res := e.act(`{"window":"0xa1","shot":"s1","actions":` + batch + `,"capture":"none","deadline_ms":4000}`)
	if took := time.Since(start); took > 4*time.Second {
		t.Errorf("took %v", took)
	}
	if code := errorCode(t, res); code != "deadline" || len(e.inputs()) != 0 ||
		get(t, res, "error.message") != "too little time was left before the input had to start to check whether the window changed where the batch clicks or drags since shot s1" {
		t.Fatalf("code %q: %s", code, text(res))
	}

	// A window that keeps drawing waits out the wait's deadline, which leaves the second capture its time, and sends
	// nothing: stale_shot once that capture shows the target held still, or deadline when it took longer than its
	// time on a busy host. 30 characters take about 900 ms with round trips and the time that keys after a click wait.
	batch = fmt.Sprintf(`[{"action":"click","x":240,"y":190},{"action":"type","text":%q}]`, strings.Repeat("x", 30))
	e = newTestEnv(t)
	f = &form{blocks: []hypr.Rect{button}}
	w = formWindow(e, f)
	e.screenshot(`{"window":"0xa1"}`)
	e.redraw(w, f, moved(button, 60))
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			deadline, _ := time.ParseDuration(args["deadline"] + "ms")
			select {
			case <-time.After(deadline):
			case <-cancel:
			}
			var reply string
			e.fake.with(func() { reply = quietReply(w) })
			return reply
		}
	})
	start = time.Now()
	res = e.act(`{"window":"0xa1","shot":"s1","actions":` + batch + `,"capture":"none","deadline_ms":4000}`)
	if took := time.Since(start); took > 4*time.Second {
		t.Errorf("took %v", took)
	}
	if code := errorCode(t, res); code != "stale_shot" && code != "deadline" || len(e.inputs()) != 0 {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// Of the about 850 ms that the batch leaves, the capture before the wait took some, and at least movedCaptureMin is
	// left for the capture after it. A host too busy to leave the wait its quiet time skips it.
	waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1")
	if len(waits) > 1 {
		t.Fatalf("waits %q", waits)
	}
	for _, wait := range waits {
		if deadline, _ := strconv.Atoi(waitArgsOf(t, wait)["deadline"]); deadline < movedQuietMs || deadline > 850-int(movedCaptureMin.Milliseconds()) {
			t.Errorf("wait %q", wait)
		}
	}

	// A capture after the wait that runs out of time cannot tell a move from content that keeps changing.
	e = newTestEnv(t)
	f = &form{blocks: []hypr.Rect{button}}
	w = formWindow(e, f)
	e.screenshot(`{"window":"0xa1"}`)
	e.redraw(w, f, moved(button, 60))
	captures := 0
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			if captures++; captures == 2 {
				time.Sleep(1500 * time.Millisecond)
			}
			return ""
		}
	})
	start = time.Now()
	res = e.act(`{"window":"0xa1","shot":"s1","actions":` + batch + `,"capture":"none","deadline_ms":4000}`)
	if took := time.Since(start); took > 4*time.Second {
		t.Errorf("took %v", took)
	}
	if code := errorCode(t, res); code != "deadline" || len(e.inputs()) != 0 {
		t.Fatalf("code %q: %s", code, text(res))
	}
}

// A window that draws after the capture that the check decided on, before the input, may have moved its target in
// between: it is checked again.
func TestTargetsThatMoveRightAfterTheCheck(t *testing.T) {
	e := newTestEnv(t)
	f := &form{}
	w := formWindow(e, f)
	e.redraw(w, f, button)
	e.screenshot(`{"window":"0xa1"}`)
	e.redraw(w, f, button, elsewhere)
	captures, shifted := 0, false
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			captures++
			return ""
		}
		// The state that the call reads after the check's capture finds the button moved.
		e.fake.onState = func(w *fakeWindow) {
			if captures == 1 && !shifted {
				shifted = true
				f.mu.Lock()
				f.blocks = []hypr.Rect{moved(button, 60), elsewhere}
				f.mu.Unlock()
				w.Commits += 2
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":240,"y":190}],"capture":"none"}`)
	if code := errorCode(t, res); code != "stale_shot" || len(e.inputs()) != 0 || !shifted {
		t.Fatalf("code %q, shifted %v: %s", code, shifted, text(res))
	}
}

// A zoom cut from a changes shot that the server has dropped since has lost the image to compare with: once the window
// has drawn, its batch is refused with a new shot.
func TestTargetsOfAZoomWhoseImageIsGone(t *testing.T) {
	e := newTestEnv(t)
	f := &form{}
	w := formWindow(e, f)
	e.redraw(w, f, button)
	e.screenshot(`{"window":"0xa1"}`)
	e.redraw(w, f, button, elsewhere)
	res := e.act(`{"window":"0xa1","shot":"s1"}`)
	zoom, _ := get(t, res, "shot.changes.regions.0.shot").(string)
	if zoom == "" {
		t.Fatalf("no zoom: %s", text(res))
	}
	e.server.shots.remove(shotID(t, res))
	e.redraw(w, f, button, elsewhere)
	res = e.act(`{"window":"0xa1","shot":"` + zoom + `","actions":[{"action":"click","x":88,"y":60}]}`)
	if code := errorCode(t, res); code != "stale_shot" || len(e.inputs()) != 0 || get(t, res, "shot.id") == nil {
		t.Fatalf("code %q: %s", code, text(res))
	}
}
