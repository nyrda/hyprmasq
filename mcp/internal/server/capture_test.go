package server

import (
	"context"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestActMapsShotPixelsWithNegativeFrameAndZoom(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PopupBox = &hypr.Rect{X: -20, Y: -10, Width: 340, Height: 210} })

	res := e.screenshot(`{"window":"0xa1"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := text(res); !strings.HasPrefix(got, "shot s1 680×420") {
		t.Errorf("text %q", got)
	}
	if got := get(t, res, "shot.frame.x"); got != -20.0 {
		t.Errorf("frame x %v", got)
	}
	if !hasImage(res) {
		t.Error("no image")
	}

	res = e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":50,"y":30}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}

	res = e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":40,"y":20,"width":100,"height":50}}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	zoom := shotID(t, res)
	// Its line, where the coordinates for the next act are read, says that act takes them as they are.
	if line := strings.Split(text(res), "\n")[0]; !strings.HasSuffix(line, " · window 0xa1 320×200 · act takes "+zoom+"'s own pixels") {
		t.Errorf("zoom line %q", line)
	}
	shots := e.fake.requestsMatching("j/hyprmasq screenshot")
	if last := shots[len(shots)-1]; !strings.HasSuffix(last, " 0 0 50 25 popups") {
		t.Errorf("zoom request %q", last)
	}
	res = e.act(fmt.Sprintf(`{"window":"0xa1","shot":%q,"actions":[{"action":"click","x":10,"y":20}],"capture":"none"}`, zoom))
	if res.IsError {
		t.Fatal(text(res))
	}
	want := [][]string{{"move", "5", "5", "click", "left", "1", "-"}, {"move", "5", "10", "click", "left", "1", "-"}}
	if got := e.inputs(); !reflect.DeepEqual(got, want) {
		t.Errorf("inputs %q, want %q", got, want)
	}
}

func TestMaxImageSize(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 320
	e.window("0xa1", "1a", 320, 200)
	res := e.screenshot(`{"window":"0xa1"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if !strings.Contains(e.fake.requestsMatching("j/hyprmasq screenshot")[0], " max 320 popups") {
		t.Error("no max in the screenshot request")
	}
	res = e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"move","x":160,"y":100}],"capture":"none"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := e.inputs(); !reflect.DeepEqual(got, [][]string{{"move", "160", "100"}}) {
		t.Errorf("inputs %q", got)
	}
}

func TestStaleShots(t *testing.T) {
	cases := []struct {
		name, target string
		change       func(e *testEnv)
		shot         string
		message      string
	}{
		{"resize", "0xa1", func(e *testEnv) { e.fake.with(func() { e.fake.find("0xa1").Width = 400 }) }, "s1", "the window was resized from 320×200 to 400×200 since shot s1"},
		{"unknown", "0xa1", func(e *testEnv) {}, "s99", "there is no shot s99; this server keeps the last 64 shots of each window"},
		{"other window", "0xb2", func(e *testEnv) { e.window("0xb2", "3c", 320, 200) }, "s1", "shot s1 is of window 0xa1, not 0xb2"},
		{"closed window", "0xb2", func(e *testEnv) {
			e.fake.removeWindow("0xa1")
			e.window("0xb2", "3c", 320, 200)
		}, "s1", "shot s1 is of window 0xa1 (stable_id 1a), which has closed"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			if res := e.screenshot(`{"window":"0xa1"}`); res.IsError {
				t.Fatal(text(res))
			}
			c.change(e)
			target := c.target
			res := e.act(fmt.Sprintf(`{"window":%q,"shot":%q,"actions":[{"action":"click","x":10,"y":10}]}`, target, c.shot))
			if code := errorCode(t, res); code != "stale_shot" {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if msg := get(t, res, "error.message"); msg != c.message {
				t.Errorf("message %q", msg)
			}
			if got := get(t, res, "actions.delivery"); got != "none" {
				t.Errorf("delivery %v", got)
			}
			if got := e.inputs(); got != nil {
				t.Errorf("sent %q", got)
			}
			// The failure comes with a shot of the window as it is now.
			if !hasImage(res) || get(t, res, "shot.id") == nil {
				t.Errorf("no new shot in %q", text(res))
			}
			if hint := get(t, res, "hint"); hint != joinHints(hintScreenshotShot, hintNothingSent) {
				t.Errorf("hint %q", hint)
			}
			res = e.act(fmt.Sprintf(`{"window":%q,"shot":%q,"actions":[{"action":"click","x":10,"y":10}],"capture":"none"}`, target, c.shot))
			if errorCode(t, res) != "stale_shot" || hasImage(res) || get(t, res, "hint") != joinHints(hintScreenshot, hintNothingSent) {
				t.Errorf("without capture: %q", text(res))
			}
		})
	}
}

// A shot names its window by its stable_id too. When the window has closed and another window has its address, a call
// with the shot, or with a region of it, fails with window_gone and nothing of it reaches the other window: no socket,
// no input, no wait and no capture. Also when an earlier listing has forgotten the shot.
func TestShotOfAWindowWhoseAddressAnotherWindowHasNow(t *testing.T) {
	const region = `{"shot":"s1","x":0,"y":0,"width":10,"height":10}`
	replaced := `window 0xa1 that s1 shows closed; another window, user.passwords "Unlock vault", has its address now`
	cases := []struct {
		name, args string
		screenshot bool
		listed     bool
		replace    bool
		// Later shots of the window push the shot out of the store before it closes.
		evicted bool
		message string
	}{
		{"act", `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`, false, false, true, false, replaced},
		{"act after windows", `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`, false, true, true, false, replaced},
		{"act with an evicted shot", `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`, false, false, true, true, replaced},
		{"act with a capture region", `{"window":"0xa1","capture":` + region + `}`, false, false, true, false, replaced},
		{"screenshot of a region", `{"window":"0xa1","region":` + region + `}`, true, false, true, false, replaced},
		{"closed", `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`, false, false, false, false, "window 0xa1 that s1 shows closed"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			if c.evicted {
				for range shotsPerWindow {
					e.screenshot(`{"window":"0xa1"}`)
				}
				if e.server.shots.get("s1") != nil {
					t.Fatal("s1 is still in the store")
				}
			}
			e.fake.removeWindow("0xa1")
			var other *fakeWindow
			if c.replace {
				other = e.window("0xa1", "2b", 500, 300, func(w *fakeWindow) { w.Class, w.Title = "user.passwords", "Unlock vault" })
			}
			if c.listed {
				// With a second window, the listing comes without a shot of the one at the address.
				e.window("0xb2", "3c", 320, 200)
				if res := e.windows(); res.IsError || hasImage(res) {
					t.Fatalf("windows: %s", text(res))
				}
			}
			prefixes := []string{"j/hyprmasq eis", "j/hyprmasq wait", "j/hyprmasq screenshot", "j/hyprmasq lease"}
			counts := map[string]int{}
			for _, prefix := range prefixes {
				counts[prefix] = len(e.fake.requestsMatching(prefix))
			}
			var res *mcp.CallToolResult
			if c.screenshot {
				res = e.screenshot(c.args)
			} else {
				res = e.act(c.args)
			}
			if code := errorCode(t, res); code != "window_gone" || hasImage(res) || get(t, res, "shot") != nil {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if msg := get(t, res, "error.message"); msg != c.message {
				t.Errorf("message %q", msg)
			}
			// Without actions, the hint has nothing to say about sending.
			if hint := get(t, res, "hint"); hint != hintWindows && hint != joinHints(hintWindows, hintNothingSent) {
				t.Errorf("hint %q", hint)
			}
			for _, prefix := range prefixes {
				if got := e.fake.requestsMatching(prefix); len(got) != counts[prefix] {
					t.Errorf("requested %q", got[counts[prefix]:])
				}
			}
			if got := e.inputs(); got != nil {
				t.Errorf("sent %q", got)
			}
			if other != nil {
				e.fake.with(func() {
					if other.Socket {
						t.Error("the socket of the window that has the address now was opened")
					}
				})
			}
		})
	}
}

func TestCaptureOfAWindowThatKeepsResizing(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	states := 0
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			states++
			w.Width = float64(300 + states)
		}
	})
	res := e.screenshot(`{"window":"0xa1"}`)
	if code := errorCode(t, res); code != "internal" || !strings.Contains(text(res), "resized during each of 3 captures") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if e.server.shots.get("s3") != nil {
		t.Error("an image of unknown size became a shot")
	}
	if files, _ := os.ReadDir(e.server.shotDir); len(files) != 0 {
		t.Errorf("files kept: %v", files)
	}
	e.fake.with(func() { e.fake.onState = nil })
	if res := e.screenshot(`{"window":"0xa1"}`); res.IsError {
		t.Errorf("%s", text(res))
	}
}

// stuckResizing answers waits as the plugin does for a window that does not catch up with its size, at once.
func stuckResizing(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
	deadline, _ := strconv.Atoi(args["deadline"])
	return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
		"commitsSince": 0, "quietMs": deadline, "elapsedMs": deadline, "resizing": true})
}

// A window that has not caught up with a resize, as after its tiled neighbour closed, shows an older frame of another
// size. A capture gives it time to catch up, and a shot that still shows the older frame says so and gives no
// coordinates.
func TestShotsOfAWindowThatHasNotCaughtUpWithAResize(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Resizing = true })
	res := e.screenshot(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "shot.resizing") != nil || get(t, res, "hint") != firstViewHint {
		t.Fatalf("after catching up: %s", text(res))
	}
	waits := e.fake.requestsMatching("j/hyprmasq wait")
	if len(waits) != 1 {
		t.Fatalf("waits %q", waits)
	}
	if args := waitArgsOf(t, waits[0]); args["expect"] != "" || args["quiet"] != "100" || args["deadline"] != "1500" {
		t.Errorf("wait %q", waits[0])
	}

	e.fake.with(func() {
		w.Resizing = true
		e.fake.onWait = stuckResizing
	})
	res = e.screenshot(`{"window":"0xa1"}`)
	stale := shotID(t, res)
	if get(t, res, "shot.resizing") != true || get(t, res, "summary") != fmt.Sprintf("shot %s 1280×800 (stale: not yet redrawn at its new size) · window 0xa1 640×400", stale) {
		t.Errorf("stale shot: %s", text(res))
	}
	if hint := get(t, res, "hint"); hint != resizingHint(&shotOut{ID: stale, Resizing: true}) {
		t.Errorf("hint %q", hint)
	}
	if waits := e.fake.requestsMatching("j/hyprmasq wait"); len(waits) != 2 {
		t.Errorf("waits %q", waits)
	}

	// act takes no coordinates from it, and its fresh shot shows an older frame too.
	res = e.act(fmt.Sprintf(`{"window":"0xa1","shot":%q,"actions":[{"action":"click","x":10,"y":10}]}`, stale))
	if code := errorCode(t, res); code != "stale_shot" || get(t, res, "actions.delivery") != "none" || get(t, res, "shot.resizing") != true {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// The new shot's hint says where to read coordinates, in place of the stale shot's.
	if hint := get(t, res, "hint"); hint != joinHints(hintNothingSent, resizingHint(&shotOut{ID: shotID(t, res), Resizing: true})) {
		t.Errorf("hint %q", hint)
	}
	if res := e.screenshot(fmt.Sprintf(`{"window":"0xa1","region":{"shot":%q,"x":0,"y":0,"width":10,"height":10}}`, stale)); errorCode(t, res) != "stale_shot" {
		t.Errorf("zoom: %s", text(res))
	}
	if got := e.inputs(); got != nil {
		t.Fatalf("sent %q", got)
	}
	// Keys need no coordinates.
	if res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":%q,"actions":[{"action":"key","keys":["enter"]}]}`, stale)); res.IsError {
		t.Errorf("keys: %s", text(res))
	}

	// Once the window has caught up, the older frame's coordinates would still miss.
	e.fake.with(func() {
		w.Resizing = false
		e.fake.onWait = nil
	})
	res = e.act(fmt.Sprintf(`{"window":"0xa1","shot":%q,"actions":[{"action":"click","x":10,"y":10}]}`, stale))
	if code := errorCode(t, res); code != "stale_shot" || get(t, res, "hint") != joinHints(hintScreenshotShot, hintNothingSent) {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":%q,"actions":[{"action":"click","x":10,"y":10}]}`, shotID(t, res))); res.IsError {
		t.Errorf("fresh shot: %s", text(res))
	}
	if got := e.inputs(); len(got) != 2 || got[1][0] != "move" {
		t.Errorf("inputs %q", got)
	}
}

// A window on a hidden workspace that waits for a frame callback, as an application that started there does, may not
// have drawn what it shows. A capture sends it frame callbacks and gives it a second to draw, and a shot of one that
// did not draw says that it may show an older frame. A window that got frame callbacks is captured at once.
func TestShotsOfAStarvedWindow(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Starved = true })
	res := e.screenshot(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "shot.may_be_stale") != nil || get(t, res, "hint") != firstViewHint {
		t.Fatalf("after drawing: %s", text(res))
	}
	waits := e.fake.requestsMatching("j/hyprmasq wait")
	if len(waits) != 1 {
		t.Fatalf("waits %q", waits)
	}
	if args := waitArgsOf(t, waits[0]); args["expect"] != "1000" || args["quiet"] != "100" || args["deadline"] != "1500" {
		t.Errorf("wait %q", waits[0])
	}
	if res := e.screenshot(`{"window":"0xa1"}`); res.IsError || get(t, res, "hint") != nil {
		t.Errorf("fed: %s", text(res))
	}
	if waits := e.fake.requestsMatching("j/hyprmasq wait"); len(waits) != 1 {
		t.Errorf("waits after it was fed %q", waits)
	}

	// The plugin's wait for a window that does not draw.
	e.fake.with(func() {
		w.Starved = true
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			expect, _ := strconv.Atoi(args["expect"])
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
				"commitsSince": 0, "quietMs": expect, "elapsedMs": expect, "resizing": false})
		}
	})
	res = e.screenshot(`{"window":"0xa1"}`)
	stale := shotID(t, res)
	if get(t, res, "shot.may_be_stale") != true || get(t, res, "summary") != fmt.Sprintf("shot %s 1280×800 (may be stale: not redrawn within 1000 ms) · window 0xa1 640×400", stale) {
		t.Errorf("shot: %s", text(res))
	}
	if hint := get(t, res, "hint"); hint != starvedHint(&shotOut{ID: stale, starved: &starvation{WaitedMs: 1000}}) {
		t.Errorf("hint %q", hint)
	}
	// An application with nothing new to draw shows what it has, so act takes coordinates from the shot.
	if res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":%q,"actions":[{"action":"click","x":10,"y":10}]}`, stale)); res.IsError {
		t.Errorf("act: %s", text(res))
	}
	if got := e.inputs(); len(got) != 1 {
		t.Errorf("inputs %q", got)
	}

	// An act that sends nothing and has no time left to wait does not compare a starved window with its last shot: it
	// takes a full shot, which says that it may be stale.
	e.fake.with(func() {
		w.Starved = true
		e.fake.onWait = nil
	})
	waits = e.fake.requestsMatching("j/hyprmasq wait")
	res = e.act(`{"window":"0xa1","deadline_ms":1000}`)
	id := shotID(t, res)
	if summary, _ := get(t, res, "summary").(string); res.IsError || get(t, res, "shot.may_be_stale") != true ||
		!strings.Contains(summary, fmt.Sprintf("shot %s 1280×800 (full shot: the window waits to draw; may be stale: no time to redraw)", id)) {
		t.Errorf("act without time: %s", text(res))
	}
	if hint, _ := get(t, res, "hint").(string); !containsHint(hint, starvedHint(&shotOut{ID: id, starved: &starvation{}})) {
		t.Errorf("hint %q", hint)
	}
	if got := e.fake.requestsMatching("j/hyprmasq wait"); len(got) != len(waits) {
		t.Errorf("waits %q", got[len(waits):])
	}
}

// The wait for a window to catch up comes out of the call's time: after its wait ran into the deadline, act captures
// at once.
func TestCatchingUpWithinTheDeadline(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Resizing = true })
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			deadline, _ := strconv.Atoi(args["deadline"])
			select {
			case <-time.After(time.Duration(deadline) * time.Millisecond):
			case <-cancel:
			}
			return stuckResizing(w, args, cancel)
		}
	})
	start := time.Now()
	res := e.act(`{"window":"0xa1","wait":{"expect_change":false},"deadline_ms":2000}`)
	if elapsed := time.Since(start); res.IsError || elapsed > 2*time.Second || get(t, res, "shot.resizing") != true {
		t.Fatalf("after %v: %s", elapsed, text(res))
	}
	if waits := e.fake.requestsMatching("j/hyprmasq wait"); len(waits) != 1 {
		t.Errorf("waits %q", waits)
	}
	// The summary and the hint point at the resize, not at drawing.
	id := shotID(t, res)
	if get(t, res, "wait.summary") != "resizing" || get(t, res, "wait.resizing") != true ||
		!regexp.MustCompile(`^timeout after \d+ ms, still resizing · shot `+id+` 1280×800 \(full shot: no earlier shot to compare with; stale: not yet redrawn at its new size\)$`).MatchString(get(t, res, "summary").(string)) {
		t.Errorf("summary %q, wait %v", get(t, res, "summary"), get(t, res, "wait"))
	}
	if hint := get(t, res, "hint"); hint != resizingHint(&shotOut{ID: id, Resizing: true}) {
		t.Errorf("hint %q", hint)
	}
}

func TestZoomIntoAClosedPopup(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PopupBox = &hypr.Rect{X: -20, Y: -10, Width: 340, Height: 210} })
	e.screenshot(`{"window":"0xa1"}`)
	// The part of the popup above and left of the window can be zoomed into while it is open.
	res := e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":0,"y":0,"width":30,"height":16}}`)
	if res.IsError || get(t, res, "shot.frame.x") != -20.0 {
		t.Fatal(text(res))
	}
	e.fake.with(func() { w.PopupBox = nil })
	res = e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":0,"y":0,"width":30,"height":16}}`)
	if code := errorCode(t, res); code != "stale_shot" || !strings.Contains(text(res), "popup it showed may have closed") {
		t.Errorf("code %q: %s", code, text(res))
	}
}

// A capture region is window-local, so after a resize it shows another part of the window than in the shot it was read
// from. A zoom is captured only while the window still has that shot's size: screenshot refuses it, and act captures
// the whole window instead and says why.
func TestCaptureRegionAfterAResize(t *testing.T) {
	slow(t)
	zoomed := func(e *testEnv, from int) []string {
		var out []string
		for _, r := range e.fake.requestsMatching("j/hyprmasq screenshot")[from:] {
			if !strings.Contains(r, " detail ") {
				out = append(out, r)
			}
		}
		return out
	}
	resize := func(w *fakeWindow) func(*fakeWindow, map[string]string, <-chan struct{}) string {
		return func(_ *fakeWindow, args map[string]string, _ <-chan struct{}) string {
			w.Width, w.Resizing = 400, false
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": true,
				"commitsSince": 1, "quietMs": 200, "elapsedMs": 300, "resizing": false})
		}
	}
	region := `{"shot":"s1","x":40,"y":20,"width":100,"height":50}`

	t.Run("own input", func(t *testing.T) {
		e := newTestEnv(t)
		w := e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		// The click opens a sidebar, say, which makes the window wider while act waits for the redraw.
		e.fake.with(func() { e.fake.onWait = resize(w) })
		res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}],"capture":` + region + `}`)
		if res.IsError || get(t, res, "actions.delivery") != "complete" {
			t.Fatal(text(res))
		}
		id := shotID(t, res)
		if frame := get(t, res, "shot.frame"); !reflect.DeepEqual(frame, map[string]any{"x": 0.0, "y": 0.0, "width": 400.0, "height": 200.0}) {
			t.Errorf("frame %v", frame)
		}
		want := "The window was resized from 320×200 to 400×200 since shot s1, which the capture region was read from, so shot " + id + " shows the whole window instead."
		if hint, _ := get(t, res, "hint").(string); !strings.Contains(hint, want) {
			t.Errorf("hint %q", hint)
		}
		if got := zoomed(e, 0); got != nil {
			t.Errorf("zoomed: %q", got)
		}
	})

	t.Run("closed", func(t *testing.T) {
		// The window closes after the whole window was captured in place of the region, during the grace, so the note
		// about that shot goes with it.
		e := newTestEnv(t)
		e.server.cfg.NewWindowGrace = 500 * time.Millisecond
		w := e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		e.fake.with(func() {
			e.fake.onWait = func(w2 *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				time.AfterFunc(200*time.Millisecond, func() { e.fake.removeWindow("0xa1") })
				return resize(w)(w2, args, cancel)
			}
		})
		res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}],"capture":` + region + `}`)
		if hint, _ := get(t, res, "hint").(string); res.IsError || get(t, res, "window_closed") != true || get(t, res, "shot") != nil || strings.Contains(hint, "resized") {
			t.Errorf("result: %s", text(res))
		}
	})

	t.Run("lock", func(t *testing.T) {
		e := newTestEnv(t)
		w := e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		windowLock, err := acquireLock(context.Background(), e.server.lockPath("1a"))
		if err != nil {
			t.Fatal(err)
		}
		states := len(e.fake.requestsMatching("j/hyprmasq state"))
		done := make(chan *mcp.CallToolResult)
		go func() {
			done <- e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}],"capture":` + region + `}`)
		}()
		e.waitFor(func() bool { return len(e.fake.requestsMatching("j/hyprmasq state")) > states })
		time.Sleep(200 * time.Millisecond)
		e.fake.with(func() { w.Width = 400 })
		windowLock.release()
		res := <-done
		if code := errorCode(t, res); code != "stale_shot" || get(t, res, "actions.delivery") != "none" || get(t, res, "shot.frame.width") != 400.0 {
			t.Fatalf("code %q: %s", code, text(res))
		}
		id := shotID(t, res)
		want := joinHints(hintScreenshotShot, "The window was resized from 320×200 to 400×200 since shot s1, which the capture region was read from, so shot "+id+
			" shows the whole window instead.", hintNothingSent)
		if hint := get(t, res, "hint"); hint != want {
			t.Errorf("hint %q", hint)
		}
		if got := zoomed(e, 0); got != nil || e.inputs() != nil {
			t.Errorf("zoomed %q, sent %q", got, e.inputs())
		}
	})

	t.Run("screenshot", func(t *testing.T) {
		e := newTestEnv(t)
		w := e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		// The window catches up at another size while the screenshot waits for it.
		e.fake.with(func() {
			w.Resizing = true
			e.fake.onWait = resize(w)
		})
		res := e.screenshot(`{"window":"0xa1","region":` + region + `}`)
		if code := errorCode(t, res); code != "stale_shot" || get(t, res, "error.message") != "the window was resized from 320×200 to 400×200 since shot s1" {
			t.Fatalf("code %q: %s", code, text(res))
		}
		if got := zoomed(e, 0); got != nil {
			t.Errorf("zoomed: %q", got)
		}
	})
}
