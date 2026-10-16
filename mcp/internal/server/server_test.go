package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf16"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestShotCoordinates(t *testing.T) {
	// A popup reaches 20 logical pixels left of and 10 above the window, at scale 2.
	s := &shot{Frame: hypr.Rect{X: -20, Y: -10, Width: 340, Height: 210}, Scale: 2, Width: 680, Height: 420}
	if x, y := s.toLogical(0, 0); x != -20 || y != -10 {
		t.Errorf("0,0 maps to %v,%v", x, y)
	}
	if x, y := s.toLogical(50, 30); x != 5 || y != 5 {
		t.Errorf("50,30 maps to %v,%v", x, y)
	}
	if got := s.logicalRegion(40, 20, 100, 50); got != (hypr.IntRect{X: 0, Y: 0, Width: 50, Height: 25}) {
		t.Errorf("region %+v", got)
	}
	if got := s.logicalRegion(0, 0, 10, 10); got != (hypr.IntRect{X: -20, Y: -10, Width: 5, Height: 5}) {
		t.Errorf("negative region %+v", got)
	}
	// At scale 1.25 the plugin widens regions to whole device pixels, so frames are fractional.
	z := &shot{Frame: hypr.Rect{X: 0.8, Y: 0.8, Width: 10.4, Height: 10.4}, Scale: 1.25, Width: 13, Height: 13}
	if got := z.logicalRegion(1, 1, 5, 5); got != (hypr.IntRect{X: 1, Y: 1, Width: 5, Height: 5}) {
		t.Errorf("fractional region %+v", got)
	}
	if !s.contains(0, 0) || !s.contains(679.5, 419) || s.contains(680, 0) || s.contains(0, 420) || s.contains(-1, 0) {
		t.Error("contains is wrong at the edges")
	}
	// Input gets thousandths of a logical pixel, so a point just left of the window rounds into it.
	third := &shot{Frame: hypr.Rect{X: -1.0 / 3, Width: 100, Height: 100}, Scale: 3, Width: 300, Height: 300}
	if x, y := third.inputPoint(2, 2); x != 0.333 || y != 0.667 {
		t.Errorf("2,2 is sent as %v,%v", x, y)
	}
	if x, _ := third.inputPoint(0.9999, 0); x != 0 || math.Signbit(x) {
		t.Errorf("0.9999 is sent as %v", x)
	}
}

func TestPointsLeftOfOrAboveTheWindow(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PopupBox = &hypr.Rect{X: -20, Y: -10, Width: 340, Height: 210} })
	e.screenshot(`{"window":"0xa1"}`)
	for _, c := range []struct{ actions, mention string }{
		{`[{"action":"click","x":10,"y":30}]`, "actions[0] (click): 10,30 of shot s1"},
		{`[{"action":"type","text":"a"},{"action":"scroll","x":50,"y":10,"dy":1}]`, "actions[1] (scroll): 50,10 of shot s1"},
		{`[{"action":"drag","from":[50,30],"to":[30,30]}]`, "actions[0] (drag): to 30,30 of shot s1"},
	} {
		res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s}`, c.actions))
		if code := errorCode(t, res); code != "out_of_bounds" || !strings.Contains(text(res), c.mention) {
			t.Fatalf("code %q: %s", code, text(res))
		}
		if hint := get(t, res, "hint").(string); !strings.Contains(hint, "the part of the popup that lies over the window") || !strings.HasSuffix(hint, "Nothing was sent.") {
			t.Errorf("hint %q", hint)
		}
		if get(t, res, "actions.delivery") != "none" || !hasImage(res) {
			t.Errorf("result %s", text(res))
		}
	}
	if got := e.inputs(); got != nil {
		t.Errorf("sent %q", got)
	}
	// The window's own left edge is reachable.
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":40,"y":20}],"capture":"none"}`); res.IsError {
		t.Fatal(text(res))
	}
	if got := e.inputs(); !reflect.DeepEqual(got, [][]string{{"move", "0", "0", "click", "left", "1", "-"}}) {
		t.Errorf("inputs %q", got)
	}
}

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
		name   string
		change func(e *testEnv)
		shot   string
	}{
		{"resize", func(e *testEnv) { e.fake.with(func() { e.fake.find("0xa1").Width = 400 }) }, "s1"},
		{"stable id", func(e *testEnv) {
			e.fake.removeWindow("0xa1")
			e.window("0xa1", "2b", 320, 200)
		}, "s1"},
		{"unknown", func(e *testEnv) {}, "s99"},
		{"other window", func(e *testEnv) { e.window("0xb2", "3c", 320, 200) }, "s1"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			if res := e.screenshot(`{"window":"0xa1"}`); res.IsError {
				t.Fatal(text(res))
			}
			c.change(e)
			target := "0xa1"
			if c.name == "other window" {
				target = "0xb2"
			}
			res := e.act(fmt.Sprintf(`{"window":%q,"shot":%q,"actions":[{"action":"click","x":10,"y":10}]}`, target, c.shot))
			if code := errorCode(t, res); code != "stale_shot" {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if got := get(t, res, "actions.delivery"); got != "none" {
				t.Errorf("delivery %v", got)
			}
			if got := e.inputs(); got != nil {
				t.Errorf("sent %q", got)
			}
			// The failure comes with a shot of the window as it is now.
			if !hasImage(res) || get(t, res, "shot.id") == nil || !strings.Contains(text(res), "Read the coordinates from the new shot") {
				t.Errorf("no new shot in %q", text(res))
			}
			if !strings.Contains(text(res), "Nothing was sent.") {
				t.Errorf("text %q", text(res))
			}
			res = e.act(fmt.Sprintf(`{"window":%q,"shot":%q,"actions":[{"action":"click","x":10,"y":10}],"capture":"none"}`, target, c.shot))
			if errorCode(t, res) != "stale_shot" || hasImage(res) || !strings.Contains(text(res), "Take a screenshot") {
				t.Errorf("without capture: %q", text(res))
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
	if res.IsError || get(t, res, "shot.resizing") != nil || get(t, res, "hint") != nil {
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
	if hint, _ := get(t, res, "hint").(string); !strings.HasPrefix(hint, "Shot "+stale+" shows an older frame") || !strings.Contains(hint, "act with no actions and wait.expect_change false") {
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
	if hint, _ := get(t, res, "hint").(string); !strings.HasPrefix(hint, "Nothing was sent. Shot ") || strings.Contains(hint, "Read the coordinates from the new shot") {
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
	if code := errorCode(t, res); code != "stale_shot" || get(t, res, "hint") != "Read the coordinates from the new shot in this result. Nothing was sent." {
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
	if res.IsError || get(t, res, "shot.may_be_stale") != nil || get(t, res, "hint") != nil {
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
	if hint, _ := get(t, res, "hint").(string); !strings.HasPrefix(hint, "Shot "+stale+" may show an older frame") ||
		!strings.Contains(hint, "did not redraw within 1000 ms") || !strings.Contains(hint, "act with no actions") {
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
	if hint, _ := get(t, res, "hint").(string); !strings.Contains(hint, "Shot "+id+" may show an older frame") || !strings.Contains(hint, "no time left") {
		t.Errorf("hint %q", hint)
	}
	if got := e.fake.requestsMatching("j/hyprmasq wait"); len(got) != len(waits) {
		t.Errorf("waits %q", got[len(waits):])
	}
}

// The wait for a window to catch up comes out of the call's time: after its wait ran into the deadline, act captures
// at once.
func TestCatchingUpWithinTheDeadline(t *testing.T) {
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
	if hint, _ := get(t, res, "hint").(string); !strings.HasPrefix(hint, "Shot "+id+" shows an older frame") || strings.Contains(hint, "Nothing was drawn") || strings.Contains(hint, "kept drawing") {
		t.Errorf("hint %q", hint)
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

func TestValidationSendsNothing(t *testing.T) {
	cases := []struct {
		name, actions, code, mention string
		noHold                       bool
	}{
		{"unknown action", `[{"action":"tap","x":1,"y":1}]`, "invalid_arguments", "actions[0].action", false},
		{"unknown field", `[{"action":"click","x":1,"y":1,"z":3}]`, "invalid_arguments", "actions[0].z", false},
		{"wrong type", `[{"action":"click","x":"1","y":1}]`, "invalid_arguments", "actions[0].x", false},
		{"count", `[{"action":"click","x":1,"y":1,"count":4}]`, "invalid_arguments", "actions[0].count", false},
		{"modifier", `[{"action":"click","x":1,"y":1,"modifiers":["hyper"]}]`, "invalid_arguments", "hyper", false},
		{"repeat", `[{"action":"key","keys":["a"],"repeat":101}]`, "invalid_arguments", "actions[0].repeat", false},
		{"empty key", `[{"action":"key","keys":["ctrl+"]}]`, "invalid_arguments", "plus", false},
		{"pause", `[{"action":"pause","ms":5001}]`, "invalid_arguments", "actions[0].ms", false},
		{"notch", `[{"action":"scroll","x":1,"y":1,"dy":1.5}]`, "invalid_arguments", "notch", false},
		{"later action", `[{"action":"type","text":"a"},{"action":"click","x":700,"y":1}]`, "out_of_bounds", "actions[1] (click)", false},
		{"bottom edge", `[{"action":"click","x":1,"y":400}]`, "out_of_bounds", "1,400", false},
		{"drag target", `[{"action":"drag","from":[1,1],"to":[1,-1]}]`, "out_of_bounds", "to 1,-1", false},
		{"drag without hold", `[{"action":"drag","from":[1,1],"to":[5,5]}]`, "unsupported", "hold", true},
		{"modifiers without hold", `[{"action":"click","x":1,"y":1},{"action":"click","x":1,"y":1,"modifiers":["ctrl"]}]`, "unsupported", "actions[1] (click) with modifiers needs the hold capability", true},
		{"chord without hold", `[{"action":"key","keys":["a","b"]}]`, "unsupported", "actions[0] (key) holding a while it presses b needs the hold capability, which window 0xa1 lacks", true},
		{"held key after a modifier", `[{"action":"key","keys":["enter"]},{"action":"key","keys":["ctrl+Shift_L","space","x"]}]`, "unsupported",
			"actions[1] (key) holding space while it presses x needs the hold capability", true},
		{"nul in text", `[{"action":"type","text":"a\u0000b"}]`, "invalid_arguments", "actions[0].text", false},
		{"nul in key", `[{"action":"key","keys":["a\u0000"]}]`, "invalid_arguments", "actions[0].keys", false},
		{"notches", `[{"action":"scroll","x":1,"y":1,"dy":-101}]`, "invalid_arguments", "at most 100 wheel notches", false},
		{"chord", `[{"action":"key","keys":["ctrl+alt+shift+super+a+b+c+d+e+f"]}]`, "invalid_arguments", "at most 9 keys", false},
		{"key name", `[{"action":"key","keys":["` + strings.Repeat("x", 64) + `"]}]`, "invalid_arguments", "longer than 63 bytes", false},
		{"text", `[{"action":"type","text":"` + strings.Repeat("x", 40000) + `"},{"action":"type","text":"` + strings.Repeat("y", 30000) + `"}]`,
			"invalid_arguments", "actions[1].text", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Caps.Hold = !c.noHold })
			e.screenshot(`{"window":"0xa1"}`)
			res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s}`, c.actions))
			if code := errorCode(t, res); code != c.code {
				t.Fatalf("code %q, want %q: %s", code, c.code, text(res))
			}
			if !strings.Contains(text(res), c.mention) {
				t.Errorf("%q does not mention %q", text(res), c.mention)
			}
			if got := get(t, res, "actions.delivery"); got != "none" {
				t.Errorf("delivery %v", got)
			}
			if got := e.inputs(); got != nil {
				t.Errorf("sent %q", got)
			}
			if got := e.fake.requestsMatching("j/hyprmasq wait"); got != nil {
				t.Errorf("waited: %q", got)
			}
			if got := e.fake.requestsMatching("j/hyprmasq lease"); got != nil {
				t.Errorf("leased: %q", got)
			}
		})
	}
}

// connect returns a session of an MCP client with the server's registered tools, which calls them as clients do.
func connect(t *testing.T, s *Server, opts *mcp.ClientOptions) *mcp.ClientSession {
	t.Helper()
	return connectAs(t, s, "test", opts)
}

// connectAs is connect for a client that introduces itself by name, such as claude-code.
func connectAs(t *testing.T, s *Server, name string, opts *mcp.ClientOptions) *mcp.ClientSession {
	t.Helper()
	serverTransport, clientTransport := mcp.NewInMemoryTransports()
	ctx := context.Background()
	if _, err := s.NewMCPServer().Connect(ctx, serverTransport, nil); err != nil {
		t.Fatal(err)
	}
	session, err := mcp.NewClient(&mcp.Implementation{Name: name, Version: "0"}, opts).Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { session.Close() })
	return session
}

// Arguments of the wrong shape are refused before anything is sent, named by their path, down to an element of an
// array, and described in JSON's terms. A null where a number, an array element or a required field belongs is
// refused instead of being read as 0 or "". The SDK leaves checking the arguments against the schema to the server,
// so the calls go through a client.
func TestArgumentErrors(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	session := connect(t, e.server, nil)
	ctx := context.Background()
	call := func(tool, args string) *mcp.CallToolResult {
		t.Helper()
		res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		return mirrored(t, res)
	}
	if res := call("screenshot", `{"window":"0xa1"}`); res.IsError {
		t.Fatal(text(res))
	}
	act := func(actions string) string { return `{"window":"0xa1","shot":"s1","actions":[` + actions + `]}` }
	region := `"shot":"s1","x":0,"y":0`
	for _, c := range []struct {
		tool, args, message string
	}{
		{"act", `{"window":"0xa1","actions":"[]"}`, `actions must be an array such as [{"action":"key","keys":["enter"]}], not a string`},
		{"act", `{"window":"0xa1","wait":"{}"}`, `wait must be an object such as {"expect_ms":2000}, not a string`},
		{"act", `{"window":"0xa1","wait":{"expect":500}}`, "wait.expect is not a field of wait, which takes expect_change, expect_ms, quiet_ms"},
		{"act", `{"window":"0xa1","wait":{"quiet_ms":"300"}}`, "wait.quiet_ms must be an integer, not a string"},
		{"act", `{"window":"0xa1","wait":{"expect_change":"no"}}`, "wait.expect_change must be true or false, not a string"},
		{"act", `{"window":"0xa1","deadline_ms":1500.5}`, "deadline_ms must be an integer, not 1500.5"},
		{"act", `{"window":"0xa1","wiat":{}}`, `unknown argument "wiat"; the arguments are window, shot, actions, wait, capture, deadline_ms`},
		{"act", `{"window":"0xa1","capture":{"shot":"s1","x":0,"y":0,"w":1,"height":1}}`, "capture.w is not a field of capture"},
		{"act", `{"window":161}`, "window must be a string, not a number"},
		{"act", act(`{"action":"click","x":"1","y":1}`), "actions[0].x must be a number, not a string"},
		{"act", act(`{"action":"click","x":1,"y":1,"count":1.5}`), "actions[0].count must be an integer, not 1.5"},
		{"act", act(`{"action":"click","x":1,"y":1,"button":1}`), "actions[0].button must be a string, not a number"},
		{"act", act(`{"action":"drag","from":[100],"to":[200,50]}`), "actions[0].from must be [x, y] in shot pixels, not [100]"},
		{"act", act(`{"action":"drag","from":[1,2],"to":[200,50,7]}`), "actions[0].to must be [x, y] in shot pixels, not [200,50,7]"},
		{"act", act(`{"action":"drag","from":["1",2],"to":[200,50]}`), "actions[0].from[0] must be a number, not a string"},
		{"act", act(`{"action":"click","x":1,"y":1,"modifiers":[1]}`), "actions[0].modifiers[0] must be a string, not a number"},
		{"act", act(`{"action":"key","keys":["ctrl",true]}`), "actions[0].keys[1] must be a string, not true or false"},
		{"act", act(`{"action":"key","keys":"ctrl+l"}`), `actions[0].keys must be a non-empty array of key names, such as ["ctrl","l"] or ["enter"]`},
		{"act", act(`{"action":"click","x":null,"y":100}`), "actions[0].x must be a number, not null"},
		{"act", act(`{"action":"click","x":10,"y":null}`), "actions[0].y must be a number, not null"},
		{"act", act(`{"action":"move","x":null,"y":null}`), "actions[0].x must be a number, not null"},
		{"act", act(`{"action":"drag","from":[null,100],"to":[200,100]}`), "actions[0].from[0] must be a number, not null"},
		{"act", act(`{"action":"drag","from":[10,10],"to":[200,null]}`), "actions[0].to[1] must be a number, not null"},
		{"act", act(`{"action":"drag","from":[10,10],"to":null}`), "actions[0].to must be an array, not null"},
		{"act", act(`{"action":"scroll","x":null,"y":10,"dy":1}`), "actions[0].x must be a number, not null"},
		{"act", act(`{"action":"scroll","x":10,"y":10,"dx":1,"dy":null}`), "actions[0].dy must be a number, not null"},
		{"act", act(`{"action":"click","x":10,"y":10,"count":null}`), "actions[0].count must be an integer, not null"},
		{"act", act(`{"action":"click","x":10,"y":10,"modifiers":[null]}`), "actions[0].modifiers[0] must be a string, not null"},
		{"act", act(`{"action":"key","keys":["ctrl",null]}`), "actions[0].keys[1] must be a string, not null"},
		{"act", act(`{"action":"key","keys":["a"],"repeat":null}`), "actions[0].repeat must be an integer, not null"},
		{"act", act(`{"action":"type","text":null}`), "actions[0].text must be a string, not null"},
		{"act", act(`{"action":"type","text":"a"},{"action":"pause","ms":null}`), "actions[1].ms must be an integer, not null"},
		{"act", `{"window":null}`, "window must be a string, not null"},
		{"act", `{"window":"0xa1","deadline_ms":null}`, "deadline_ms must be an integer, not null"},
		{"act", `{"window":"0xa1","wait":{"expect_ms":null}}`, "wait.expect_ms must be an integer, not null"},
		{"act", `{"window":"0xa1","wait":{"quiet_ms":null}}`, "wait.quiet_ms must be an integer, not null"},
		{"act", `{"window":"0xa1","capture":{` + region + `,"width":null,"height":10}}`, "capture.width must be a number, not null"},
		{"act", `{"window":"0xa1","capture":{"shot":"s1","x":0,"width":10,"height":10}}`, "capture.y is missing"},
		{"act", `{"window":"0xa1","capture":{"region":{` + region + `,"width":10,"height":null}}}`, "capture.region.height must be a number, not null"},
		{"screenshot", `{"window":"0xa1","region":"s1"}`, `region must be an object such as {"shot":"s4","x":0,"y":0,"width":100,"height":50}, not a string`},
		{"screenshot", `{"window":"0xa1","region":{` + region + `,"width":1,"height":1,"scale":2}}`, "region.scale is not a field of region, which takes shot, x, y, width, height"},
		{"screenshot", `{"window":"0xa1","region":{"shot":"s1","x":null,"y":0,"width":10,"height":10}}`, "region.x must be a number, not null"},
		{"screenshot", `{"window":"0xa1","region":{"shot":"s1","x":0,"width":10,"height":10}}`, "region.y is missing"},
		{"screenshot", `{"window":"0xa1","region":{` + region + `,"width":"10","height":10}}`, "region.width must be a number, not a string"},
		{"screenshot", `{"window":"0xa1","region":{"shot":null,"x":0,"y":0,"width":10,"height":10}}`, "region.shot must be a string, not null"},
		{"screenshot", `{"window":null}`, "window must be a string, not null"},
		{"launch", `{"command":"foot -T x"}`, `command must be an array such as ["foot","-T","scratch"], not a string`},
		{"launch", `{"command":["foot",1]}`, "command[1] must be a string, not a number"},
		{"launch", `{"command":["foot",null,"-T","x"]}`, "command[1] must be a string, not null"},
		{"launch", `{"command":null}`, `command must be an array such as ["foot","-T","scratch"], not null`},
		{"launch", `{"command":["foot"],"timeout_s":null}`, "timeout_s must be an integer, not null"},
		{"launch", `{"command":["foot"],"workspace":7}`, "workspace must be a string, not a number"},
		{"release", `{"window":null}`, "window must be a string, not null"},
		{"close", `{"window":["0xa1"]}`, "window must be a string, not an array"},
	} {
		res := call(c.tool, c.args)
		if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), c.message) {
			t.Errorf("%s %s: code %q: %s", c.tool, c.args, code, text(res))
		}
		if c.tool == "act" && get(t, res, "actions.delivery") != "none" {
			t.Errorf("%s %s: delivery %v", c.tool, c.args, get(t, res, "actions.delivery"))
		}
	}
	if got := e.inputs(); got != nil {
		t.Errorf("sent %q", got)
	}
	for _, prefix := range []string{"j/hyprmasq wait", "j/hyprmasq lease", "/dispatch"} {
		if got := e.fake.requestsMatching(prefix); got != nil {
			t.Errorf("requested %q", got)
		}
	}
	if got := e.fake.requestsMatching("j/hyprmasq screenshot"); len(got) != 1 {
		t.Errorf("captured %q", got)
	}

	// An optional field that is not a number takes its default when it is null, as if it were left out.
	res := call("act", `{"window":"0xa1","shot":"s1","wait":null,"capture":null,"actions":[`+
		`{"action":"click","x":10,"y":10,"button":null,"modifiers":null},{"action":"scroll","x":10,"y":10,"dy":2,"unit":null}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got, want := e.inputs(), [][]string{{"move", "5", "5", "click", "left", "1", "-", "sync", "0", "1000", "move", "5", "5", "wheel", "0", "2"}}; !reflect.DeepEqual(got, want) {
		t.Errorf("inputs %q, want %q", got, want)
	}
	if res := call("screenshot", `{"window":"0xa1","region":null}`); res.IsError {
		t.Fatal(text(res))
	}
}

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

func TestTranslate(t *testing.T) {
	identity := func(x, y float64) (float64, float64) { return x/2 - 1, y/2 + 0.25 }
	cases := []struct {
		action string
		want   [][]string
	}{
		{`{"action":"click","x":10,"y":20}`, [][]string{{"move", "4", "10.25"}, {"click", "left", "1", "-"}}},
		{`{"action":"click","x":10,"y":20,"button":"right","count":2,"modifiers":["control","shift"]}`, [][]string{{"move", "4", "10.25"}, {"click", "right", "2", "ctrl+shift"}}},
		{`{"action":"click","x":1,"y":1,"modifiers":["ctrl+alt"]}`, [][]string{{"move", "-0.5", "0.75"}, {"click", "left", "1", "ctrl+alt"}}},
		{`{"action":"move","x":3,"y":5}`, [][]string{{"move", "0.5", "2.75"}}},
		{`{"action":"drag","from":[0,0],"to":[10,10],"button":"middle","modifiers":["super"]}`, [][]string{{"drag", "-1", "0.25", "4", "5.25", "middle", "super"}}},
		{`{"action":"scroll","x":2,"y":2,"dy":3}`, [][]string{{"move", "0", "1.25"}, {"wheel", "0", "3"}}},
		{`{"action":"scroll","x":2,"y":2,"dx":-40.5,"unit":"px"}`, [][]string{{"move", "0", "1.25"}, {"scroll", "-40.5", "0"}}},
		{`{"action":"key","keys":["Ctrl+Shift","T"],"repeat":3}`, [][]string{{"keys", "ctrl+shift+T", "3"}}},
		{`{"action":"key","keys":["Return"]}`, [][]string{{"keys", "enter", "1"}}},
		{`{"action":"key","keys":["ctrl","+"]}`, [][]string{{"keys", "ctrl+plus", "1"}}},
		{`{"action":"key","keys":["F12","XF86AudioMute","del","cmd"]}`, [][]string{{"keys", "f12+XF86AudioMute+delete+super", "1"}}},
		{`{"action":"type","text":"<enter> \\n literal"}`, [][]string{{"text", "<enter> \\n literal"}}},
		{`{"action":"pause","ms":250}`, [][]string{{"sleep", "250"}}},
	}
	for _, c := range cases {
		a, err := parseAction(json.RawMessage(c.action), "actions[0]")
		if err != nil {
			t.Errorf("%s: %v", c.action, err)
			continue
		}
		var got [][]string
		for _, step := range translate(0, a, identity) {
			got = append(got, step.Argv)
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: %q, want %q", c.action, got, c.want)
		}
	}
}

// A click or drag that more actions follow gets a sync after it, which belongs to the next action. It lasts until keys
// and text after the click come 300 ms after its end, counted with the sender's 50 ms between actions, whose first one
// the sync replaces, and only for the first keys after each click.
func TestClickSettle(t *testing.T) {
	const click, typed, key = `{"action":"click","x":1,"y":1}`, `{"action":"type","text":"a b"}`, `{"action":"key","keys":["enter"]}`
	for _, c := range []struct {
		batch string
		want  []string
	}{
		{click + "," + typed, []string{"0 move", "0 click", "1 sync 300 1000", "1 text"}},
		{click + `,{"action":"pause","ms":100},` + typed, []string{"0 move", "0 click", "1 sync 150 1000", "1 sleep 100", "2 text"}},
		{click + `,{"action":"pause","ms":200},` + typed, []string{"0 move", "0 click", "1 sync 50 1000", "1 sleep 200", "2 text"}},
		{click + `,{"action":"pause","ms":300},` + typed, []string{"0 move", "0 click", "1 sync 0 1000", "1 sleep 300", "2 text"}},
		{click + `,{"action":"move","x":2,"y":2},` + key, []string{"0 move", "0 click", "1 sync 250 1000", "1 move", "2 keys"}},
		// A scroll is two sender actions, each with its gap.
		{click + `,{"action":"scroll","x":1,"y":1,"dy":1},{"action":"pause","ms":100},` + key, []string{"0 move", "0 click", "1 sync 50 1000", "1 move", "1 wheel", "2 sleep 100", "3 keys"}},
		{click + "," + typed + "," + key, []string{"0 move", "0 click", "1 sync 300 1000", "1 text", "2 keys"}},
		{click + "," + click + "," + key, []string{"0 move", "0 click", "1 sync 0 1000", "1 move", "1 click", "2 sync 300 1000", "2 keys"}},
		{`{"action":"drag","from":[1,1],"to":[5,5]},` + key, []string{"0 drag", "1 sync 300 1000", "1 keys"}},
		{`{"action":"click","x":1,"y":1,"count":2},{"action":"scroll","x":1,"y":1,"dy":1}`, []string{"0 move", "0 click", "1 sync 0 1000", "1 move", "1 wheel"}},
		{`{"action":"scroll","x":1,"y":1,"dy":1},` + key, []string{"0 move", "0 wheel", "1 keys"}},
		{key + "," + typed, []string{"0 keys", "1 text"}},
		{typed + "," + click, []string{"0 text", "1 move", "1 click"}},
	} {
		var raw []json.RawMessage
		if err := json.Unmarshal([]byte("["+c.batch+"]"), &raw); err != nil {
			t.Fatal(err)
		}
		batch, err := parseActions(raw)
		if err != nil {
			t.Fatal(err)
		}
		var got []string
		for _, step := range translateBatch(batch, &shot{Scale: 1}) {
			got = append(got, strconv.Itoa(step.Batch)+" "+step.Argv[0])
			if step.Argv[0] == "sleep" || step.Argv[0] == "sync" {
				got[len(got)-1] += " " + strings.Join(step.Argv[1:], " ")
			}
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("[%s]: %q, want %q", c.batch, got, c.want)
		}
	}
}

func TestMinSendTime(t *testing.T) {
	steps := func(argvs ...[]string) []inputStep {
		var out []inputStep
		for _, argv := range argvs {
			out = append(out, inputStep{Argv: argv})
		}
		return out
	}
	for _, c := range []struct {
		steps []inputStep
		ms    int64
	}{
		{steps([]string{"move", "1", "2"}, []string{"click", "left", "1", "-"}), 100},
		{steps([]string{"click", "left", "2", "ctrl"}), 190},
		{steps([]string{"drag", "0", "0", "300", "400", "left", "-"}), 620},
		{steps([]string{"drag", "0", "0", "5", "0", "left", "shift"}), 340},
		{steps([]string{"wheel", "2", "-3"}), 80},
		{steps([]string{"scroll", "0", "-40.5"}), 0},
		{steps([]string{"keys", "ctrl+l", "3"}), 105},
		{steps([]string{"text", "aé\r\nc"}), 50},
		{steps([]string{"sleep", "250"}, []string{"keys", "enter", "1"}), 315},
		// A sync takes the place of the gap after the action before it, and lasts at least its MIN.
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "300", "1000"}, []string{"keys", "enter", "1"}), 365},
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "100", "1000"}, []string{"keys", "enter", "1"}), 165},
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "20", "1000"}, []string{"keys", "enter", "1"}), 115},
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "0", "1000"}, []string{"keys", "enter", "1"}), 115},
	} {
		if got := minSendTime(c.steps).Milliseconds(); got != c.ms {
			t.Errorf("%v: %d ms, want %d", c.steps, got, c.ms)
		}
	}
	if hint := asToolError(deadlineWhileSending("partial", maxDeadline)).Hint; strings.Contains(hint, "larger deadline_ms") || !strings.Contains(hint, "in parts") {
		t.Errorf("at the largest deadline: %q", hint)
	}
}

// A batch that cannot be sent within the deadline is refused before anything is sent, instead of being cut off.
func TestBatchesThatTheDeadlineCannotFit(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	for _, c := range []struct{ args, message, hint string }{
		{`"actions":[{"action":"key","keys":["a"]}],"deadline_ms":1000`,
			"at least 15 ms to send, about 16 ms with round trips; deadline_ms 1000 leaves 0 ms for it, since its last 2250 ms are kept for stopping input in time and for the capture",
			"Give a deadline_ms of at least 4000"},
		// Input ends early enough for a sender that the deadline stops to report before the capture.
		{`"actions":[{"action":"key","keys":["a"]}],"deadline_ms":2000`, "deadline_ms 2000 leaves 0 ms for it", "Give a deadline_ms of at least 4000"},
		{`"actions":[{"action":"type","text":"` + strings.Repeat("x", 1000) + `"}]`, "at least 14990 ms to send, about 16489 ms with round trips; deadline_ms 15000 leaves 12750 ms for it",
			"Give a deadline_ms of at least 22000"},
		// Text that fits at 15 ms a character, but not with the round trips, which took about 15.5 ms a character in the VM.
		{`"actions":[{"action":"type","text":"` + strings.Repeat("x", 800) + `"}]`, "at least 11990 ms to send, about 13189 ms with round trips; deadline_ms 15000 leaves 12750 ms for it",
			"Give a deadline_ms of at least 19000"},
		{`"actions":[{"action":"type","text":"` + strings.Repeat("x", 2000) + `"}],"deadline_ms":30000`, "at least 29990 ms", "one call types up to about 1400 characters"},
	} {
		res := e.act(`{"window":"0xa1",` + c.args + `}`)
		if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), c.message) || !strings.Contains(get(t, res, "hint").(string), c.hint) {
			t.Errorf("code %q: %s", code, text(res))
		}
		if get(t, res, "actions.delivery") != "none" || hasImage(res) {
			t.Errorf("%s", text(res))
		}
	}

	// Waiting for another window's input left too little time.
	sendLock, err := acquireLock(context.Background(), e.server.sendLockPath())
	if err != nil {
		t.Fatal(err)
	}
	time.AfterFunc(2*time.Second, sendLock.release)
	res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + strings.Repeat("x", 100) + `"}],"deadline_ms":5000}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "takes about 1639 ms to send") || get(t, res, "actions.delivery") != "none" {
		t.Errorf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil || e.fake.requestsMatching("j/hyprmasq lease") != nil {
		t.Errorf("sent %q", e.inputs())
	}
}

// Only hyprmasq-input knows the keys that text resolves to with the keymap, such as a dead key and Space for ^ on a
// German layout. It gets the time left for input, less the round trips' share, as its budget, and refuses a batch that
// would not end in time before it sends anything. The server reports that as it does for a batch that its own estimate
// does not fit.
func TestBatchesThatTheResolvedKeysCannotFit(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	// 40 times ^ is 80 keys, 1190 ms at their pace, while the server counts 40 keys, 590 ms.
	carets := strings.Repeat("^", 40)
	t.Setenv("FAKE_INPUT_MODE", "toolong:1190")
	res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + carets + `"}],"deadline_ms":3200}`)
	want := "the batch takes at least 1190 ms to send with the active keymap, about 1309 ms with round trips; deadline_ms 3200 leaves 950 ms for it, " +
		"since its last 2250 ms are kept for stopping input in time and for the capture"
	if code := errorCode(t, res); code != "invalid_arguments" || get(t, res, "error.message") != want || get(t, res, "actions.delivery") != "none" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if hint := get(t, res, "hint"); hint != "Give a deadline_ms of at least 5000, or split the batch over several calls. Nothing was sent." {
		t.Errorf("hint %q", hint)
	}
	if get(t, res, "actions.failed_index") != nil {
		t.Errorf("failed index in %s", text(res))
	}
	// The sender got the time left for input as its budget, less the round trips' share.
	runs := readInputLog(t, e.inputLog)
	budget := -1
	for i, arg := range runs[0].Argv {
		if arg == "--budget" {
			budget, _ = strconv.Atoi(runs[0].Argv[i+1])
		}
	}
	if budget < 700 || budget > 950*10/11 {
		t.Errorf("budget %d in %q", budget, runs[0].Argv)
	}
	if got := e.fake.requestsMatching("j/hyprmasq wait"); got != nil {
		t.Errorf("waited: %q", got)
	}
	if got := e.fake.requestsMatching("j/hyprmasq state 0xa1"); len(got) < 2 {
		t.Errorf("states %q", got)
	}

	// Too long for no deadline_ms.
	t.Setenv("FAKE_INPUT_MODE", "toolong:40000")
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + carets + `"}]}`)
	if code := errorCode(t, res); code != "invalid_arguments" ||
		get(t, res, "hint") != "Split it over several calls: typing takes at least 15 ms a key, and characters typed through Compose take two keys or more. Nothing was sent." {
		t.Errorf("code %q: %s", code, text(res))
	}
	// The deadline fits the batch, so waiting for the locks took the time.
	t.Setenv("FAKE_INPUT_MODE", "toolong:1190")
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + carets + `"}]}`)
	if code := errorCode(t, res); code != "deadline" ||
		!regexp.MustCompile(`^the batch takes about 1309 ms to send with the active keymap and round trips, and \d+ ms were left for it after waiting for other calls$`).MatchString(get(t, res, "error.message").(string)) ||
		!strings.HasSuffix(get(t, res, "hint").(string), "Nothing was sent.") {
		t.Errorf("code %q: %s", code, text(res))
	}
	// Nothing was sent, so the lease that the call took before the sender is given back.
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xa1 release"); len(got) != 3 {
		t.Errorf("releases %q", got)
	}
}

// The deadlines that hints suggest, and the character counts of the act description, pass the check.
func TestSuggestedDeadlinesFit(t *testing.T) {
	typing := func(n int) []inputStep { return []inputStep{{Argv: []string{"text", strings.Repeat("x", n)}}} }
	for _, deadline := range []time.Duration{defaultDeadline, maxDeadline} {
		if n := typedWithin(deadline); fitsDeadline(typing(n), deadline) != nil {
			t.Errorf("%d characters do not fit in %v", n, deadline)
		}
	}
	for _, steps := range [][]inputStep{{{Argv: []string{"keys", "a", "1"}}}, typing(1000), typing(1300), {{Argv: []string{"sleep", "5000"}}}} {
		if enough := deadlineFor(minSendTime(steps)); fitsDeadline(steps, enough) != nil {
			t.Errorf("%v does not fit in the deadline_ms %d suggested for it", steps[0].Argv[0], enough.Milliseconds())
		}
	}
}

func TestBatchRunsInOneSender(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[
		{"action":"click","x":20,"y":20},{"action":"type","text":"hi there"},{"action":"key","keys":["enter"]},
		{"action":"scroll","x":40,"y":40,"dy":2},{"action":"pause","ms":10},
		{"action":"drag","from":[0,0],"to":[100,100]}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	// One sender checks the whole batch, including whether the keymap can type it, before it sends anything. The text
	// waits for the window's answer to the click, and at least until the click has settled.
	want := [][]string{{
		"move", "10", "10", "click", "left", "1", "-", "sync", "300", "1000", "text", "hi there", "keys", "enter", "1",
		"move", "20", "20", "wheel", "0", "2", "sleep", "10", "drag", "0", "0", "50", "50", "left", "-",
	}}
	if got := e.inputs(); !reflect.DeepEqual(got, want) {
		t.Errorf("inputs %q\nwant %q", got, want)
	}
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 6.0, "total": 6.0, "delivery": "complete"}) {
		t.Errorf("actions %v", got)
	}
}

// The sender types characters without a key of their own through the Compose table of the target application's
// locale, which the server reads from the window's process. When it cannot, the hint says that such characters may
// have arrived as others.
func TestComposeLocaleOfTheWindow(t *testing.T) {
	const unknownNote = "Characters without a key of their own were typed through the Compose table of en_US.UTF-8, since the window's locale is not known: "
	cases := []struct {
		name    string
		pid     int
		environ string
		exe     string
		// FAKE_INPUT_COMPOSE: what the sender typed through Compose, if anything.
		compose string
		locale  string
		hint    string
	}{
		{name: "LC_ALL first", pid: 4242, environ: "LANG=de_DE.UTF-8\x00LC_ALL=pt_BR.UTF-8\x00LC_CTYPE=fi_FI.UTF-8\x00",
			compose: "pt_BR.UTF-8:pt_BR.UTF-8", locale: "pt_BR.UTF-8"},
		{name: "empty variables are unset", pid: 4242, environ: "LC_ALL=\x00LANG=de_DE.UTF-8\x00LC_CTYPE=fi_FI.UTF-8\x00",
			compose: "fi_FI.UTF-8:fi_FI.UTF-8", locale: "fi_FI.UTF-8"},
		{name: "LANG", pid: 4242, environ: "PATH=/bin\x00LANG=de_DE.UTF-8\x00", compose: "de_DE.UTF-8:de_DE.UTF-8", locale: "de_DE.UTF-8"},
		{name: "none set", pid: 4242, environ: "PATH=/bin\x00", locale: "C"},
		{name: "no table for the locale", pid: 4242, environ: "LANG=xx_XX.UTF-8\x00", compose: "en_US.UTF-8:xx_XX.UTF-8", locale: "xx_XX.UTF-8",
			hint: "Characters without a key of their own were typed through the Compose table of en_US.UTF-8, since there is none for the window's locale xx_XX.UTF-8"},
		{name: "unreadable environment", pid: 4242, compose: "en_US.UTF-8:en_US.UTF-8",
			hint: unknownNote + "the environment of its process 4242 cannot be read"},
		{name: "no process", pid: 0, environ: "LANG=de_DE.UTF-8\x00", compose: "en_US.UTF-8:en_US.UTF-8",
			hint: unknownNote + "Hyprland names no process for the window"},
		{name: "xwayland-satellite", pid: 4242, environ: "LANG=de_DE.UTF-8\x00", exe: "/nix/store/x-xwayland-satellite-0.7/bin/.xwayland-satellite-wrapped",
			compose: "en_US.UTF-8:en_US.UTF-8", hint: unknownNote + "the window belongs to xwayland-satellite"},
		// Nothing went through Compose, so nothing could arrive as another character.
		{name: "unknown without Compose", pid: 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PID = c.pid })
			if c.environ != "" {
				e.setEnviron(c.pid, c.environ)
			}
			if c.exe != "" {
				e.setExe(c.pid, c.exe)
			}
			t.Setenv("FAKE_INPUT_COMPOSE", c.compose)
			res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"ç"}]}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			runs := readInputLog(t, e.inputLog)
			locale := ""
			for i, arg := range runs[0].Argv {
				if arg == "--locale" {
					locale = runs[0].Argv[i+1]
				}
			}
			if locale != c.locale {
				t.Errorf("--locale %q in %q, want %q", locale, runs[0].Argv, c.locale)
			}
			hint, _ := get(t, res, "hint").(string)
			if c.hint == "" && strings.Contains(hint, "Compose") || !strings.Contains(hint, c.hint) {
				t.Errorf("hint %q, want %q", hint, c.hint)
			}
			if c.hint != "" && !strings.Contains(hint, ". The application may compose them differently, so look at the shot to check that they arrived as typed.") {
				t.Errorf("hint %q", hint)
			}
		})
	}

	// Batches without text leave the locale alone.
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.setEnviron(4242, "LANG=de_DE.UTF-8\x00")
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	if runs := readInputLog(t, e.inputLog); slices.Contains(runs[0].Argv, "--locale") {
		t.Errorf("argv %q", runs[0].Argv)
	}
}

func TestResizeDuringBatchStopsIt(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	// The window grows while the sender runs, after it has sent the first click (two of its actions).
	t.Setenv("FAKE_INPUT_MODE", "hang:2")
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if data, _ := os.ReadFile(e.inputLog); strings.Contains(string(data), `"start"`) {
				w.Width = 400
			}
		}
	})
	start := time.Now()
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":20,"y":20},{"action":"click","x":40,"y":40}]}`)
	if code := errorCode(t, res); code != "stale_shot" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// Without the check, the batch would run until the deadline, 14 seconds later.
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Errorf("stopped after %v", elapsed)
	}
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0}) {
		t.Errorf("actions %v", got)
	}
	var terminated bool
	for _, r := range readInputLog(t, e.inputLog) {
		terminated = terminated || r.Event == "sigterm"
	}
	if !terminated {
		t.Error("the sender was not stopped")
	}
	if get(t, res, "shot.id") == nil || !hasImage(res) {
		t.Error("no capture after the partial batch")
	}
	if !strings.Contains(text(res), "1/2 delivered, delivery partial: actions[1] (click) may be partly applied") || !strings.Contains(text(res), "Look at the shot before repeating anything.") {
		t.Errorf("text %q", text(res))
	}

	// A batch whose later actions have no coordinates does not depend on the size.
	e.fake.with(func() { e.fake.onState = nil; e.fake.find("0xa1").Width = 320 })
	t.Setenv("FAKE_INPUT_MODE", "sleep:200")
	e.screenshot(`{"window":"0xa1"}`)
	before := len(e.fake.requestsMatching("j/hyprmasq state"))
	res = e.act(`{"window":"0xa1","shot":"s3","actions":[{"action":"click","x":20,"y":20},{"action":"type","text":"abc"}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if polled := len(e.fake.requestsMatching("j/hyprmasq state")) - before; polled > 4 {
		t.Errorf("%d state requests for a batch that does not depend on the size", polled)
	}
}

func TestSummaryLines(t *testing.T) {
	click := []action{{Kind: "click"}}
	three := []action{{Kind: "click"}, {Kind: "type"}, {Kind: "key"}}
	shot5 := &shotOut{ID: "s5", Width: 1280, Height: 800}
	cases := []struct {
		name  string
		out   actOut
		batch []action
		want  string
	}{
		{"quiet", actOut{Actions: &actionsOut{Done: 3, Total: 3, Delivery: "complete"},
			Wait: &waitOut{Ended: "quiet", RedrawObserved: true, QuietMs: 200, ElapsedMs: 412}, Shot: shot5, Lease: &leaseOut{Held: true, RemainingS: 60}}, three,
			"3/3 delivered · redraw observed · quiet 200 ms · 412 ms · shot s5 1280×800 · lease 60 s"},
		{"no redraw", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"},
			Wait: &waitOut{Ended: "no_redraw", expectMs: 1000}, Shot: &shotOut{ID: "s6", Width: 1280, Height: 800}, Lease: &leaseOut{Held: true, RemainingS: 60}}, click,
			"1/1 delivered · no redraw within 1000 ms · shot s6 1280×800 · lease 60 s\nThe click may have missed or had no visible effect" + laterClause + ". Look at the shot before repeating it."},
		{"no redraw after a click and a move", actOut{Actions: &actionsOut{Done: 2, Total: 2, Delivery: "complete"},
			Wait: &waitOut{Ended: "no_redraw", expectMs: 1000}, Shot: &shotOut{ID: "s6", Width: 1280, Height: 800}, Lease: &leaseOut{Held: true, RemainingS: 60}},
			[]action{{Kind: "click"}, {Kind: "move"}},
			"2/2 delivered · no redraw within 1000 ms · shot s6 1280×800 · lease 60 s\nThe input may have had no visible effect" + laterClause + ". Look at the shot before repeating it."},
		{"no redraw after scrolling", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"},
			Wait: &waitOut{Ended: "no_redraw", expectMs: 1000}, Shot: &shotOut{ID: "s6", Width: 1280, Height: 800}, Lease: &leaseOut{Held: true, RemainingS: 60}}, []action{{Kind: "scroll"}},
			"1/1 delivered · no redraw within 1000 ms · shot s6 1280×800 · lease 60 s\nThe input may have had no visible effect. Look at the shot before repeating it."},
		{"quiet without change", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"},
			Wait: &waitOut{Ended: "quiet", QuietMs: 200, ElapsedMs: 200}, Shot: shot5, Lease: &leaseOut{}}, []action{{Kind: "move"}},
			"1/1 delivered · no redraw · quiet 200 ms · 200 ms · shot s5 1280×800"},
		{"timeout", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"},
			Wait: &waitOut{Ended: "timeout", RedrawObserved: true, ElapsedMs: 13990}, Shot: shot5, Lease: &leaseOut{Held: true, RemainingS: 59}}, click,
			"1/1 delivered · timeout after 13990 ms, still drawing · shot s5 1280×800 · lease 59 s\nThe window kept drawing until the deadline, so it may be animating or still loading. Look at the shot; to wait longer, act with no actions and a larger deadline_ms (at most 30000)."},
		{"timeout without a redraw", actOut{Actions: &actionsOut{}, Wait: &waitOut{Ended: "timeout", ElapsedMs: 13990}, Shot: shot5, Lease: &leaseOut{}}, nil,
			"timeout after 13990 ms, no redraw · shot s5 1280×800\nNothing was drawn before the deadline. Look at the shot; to wait longer for a first redraw, act with no actions and a larger deadline_ms (at most 30000)."},
		{"timeout without a redraw after a click", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "timeout", ElapsedMs: 13990}, Shot: shot5,
			Lease: &leaseOut{}}, click,
			"1/1 delivered · timeout after 13990 ms, no redraw · shot s5 1280×800\nNothing was drawn before the deadline" + laterClause +
				". Look at the shot; to wait longer for a first redraw, act with no actions and a larger deadline_ms (at most 30000)."},
		{"timeout while resizing, caught up by the capture", actOut{Actions: &actionsOut{}, Wait: &waitOut{Ended: "timeout", Resizing: true, ElapsedMs: 13990}, Shot: shot5, Lease: &leaseOut{}}, nil,
			"timeout after 13990 ms, still resizing · shot s5 1280×800\nThe window had not yet redrawn at its new size when the wait's deadline arrived, but had by the capture. Look at the shot before repeating anything."},
		{"timeout while resizing without a shot", actOut{Actions: &actionsOut{}, Wait: &waitOut{Ended: "timeout", Resizing: true, ElapsedMs: 13990}, Lease: &leaseOut{}}, nil,
			"timeout after 13990 ms, still resizing\nThe window had not yet redrawn at its new size when the deadline arrived. Take a screenshot and look at it before repeating anything."},
		{"no time to wait", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "timeout", skipped: true}, Shot: shot5, Lease: &leaseOut{}}, click,
			"1/1 delivered · no time left to wait · shot s5 1280×800\nThe deadline left no time to wait for a redraw. Look at the shot before repeating anything; to wait, act with no actions."},
		{"no redraw without a shot", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "no_redraw", expectMs: 1000}, Lease: &leaseOut{}}, click,
			"1/1 delivered · no redraw within 1000 ms\nThe click may have missed or had no visible effect" + laterClause + ". Take a screenshot and look at it before repeating it."},
		{"window closed", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "window_closed"}, WindowClosed: true},
			[]action{{Kind: "key"}}, "1/1 delivered · window closed\nThe window closed. Any new windows are listed; call windows for the rest."},
		{"window closed after the wait, back to the window it came from", actOut{Actions: &actionsOut{Done: 3, Total: 3, Delivery: "complete"},
			Wait: &waitOut{Ended: "quiet", RedrawObserved: true, QuietMs: 201, ElapsedMs: 492}, WindowClosed: true,
			Source: &sourceOut{Window: "0xa1", Class: "chromium-browser", Title: "Upload", Workspace: "web", Shot: &shotOut{ID: "s9", Width: 1280, Height: 720}}}, three,
			"3/3 delivered · redraw observed · quiet 201 ms · 492 ms · window closed · back to 0xa1 (chromium-browser \"Upload\") on workspace web (hidden)\n" +
				"shot s9 1280×720 of 0xa1, the window it came from\n" +
				"The window closed. It came from window 0xa1, which shot s9 shows now: go on there with that shot's pixels. Any new windows are listed; call windows for the rest."},
		{"window closed, back to a window without a shot", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "window_closed"}, WindowClosed: true,
			Source: &sourceOut{Window: "0xa1", Class: "thunderbird", Title: "Inbox", Workspace: "1", Visible: true}}, click,
			"1/1 delivered · window closed · back to 0xa1 (thunderbird \"Inbox\") on workspace 1 (visible)\n" +
				"The window closed. It came from window 0xa1: take a screenshot of it to go on there. Any new windows are listed; call windows for the rest."},
		{"cancelled", actOut{Actions: &actionsOut{Done: 1, Total: 2, Delivery: "partial", FailedIndex: ptr(1)}, Wait: &waitOut{Ended: "cancelled"}, Shot: shot5, Lease: &leaseOut{Held: true, RemainingS: 60}},
			[]action{{Kind: "click"}, {Kind: "type"}}, "1/2 delivered, delivery partial: actions[1] (type) may be partly applied · cancelled · shot s5 1280×800 · lease 60 s\nThe call was cancelled while the input was sent. Look at the shot before repeating anything."},
		{"empty batch", actOut{Actions: &actionsOut{}, Wait: &waitOut{Ended: "no_redraw", expectMs: 20000}, Shot: shot5, Lease: &leaseOut{Held: true, RemainingS: 60}},
			nil, "no redraw within 20000 ms · shot s5 1280×800 · lease 60 s\nNothing was drawn since shot s4. If the window is still loading, wait again: act with no actions, a larger wait.expect_ms, and a deadline_ms at least 1000 above it."},
		{"new window", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "quiet", RedrawObserved: true, QuietMs: 200, ElapsedMs: 300},
			Shot: shot5, Lease: &leaseOut{Held: true, RemainingS: 60}, NewWindows: []newWindowOut{{Window: "0xb2", Class: "org.gnome.Nautilus", Title: "Save As", SamePID: true, Launched: true,
				Workspace: "mail", Shot: &shotOut{ID: "s6", Width: 400, Height: 300}}}},
			click, "1/1 delivered · redraw observed · quiet 200 ms · 300 ms · shot s5 1280×800 · lease 60 s · new window 0xb2 (org.gnome.Nautilus \"Save As\", launched) on workspace mail (hidden)\n" +
				"shot s6 400×300 of new window 0xb2"},
		{"late window", actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "quiet", RedrawObserved: true, QuietMs: 200, ElapsedMs: 300},
			Shot: shot5, Lease: &leaseOut{}, LateWindows: []newWindowOut{{Window: "0xc3", Class: "xdg-desktop-portal-gtk", Title: "Open Files", Workspace: "web", Source: "0xa1",
				Shot: &shotOut{ID: "s6", Width: 960, Height: 540}}}},
			click, "1/1 delivered · redraw observed · quiet 200 ms · 300 ms · shot s5 1280×800 · late window 0xc3 (xdg-desktop-portal-gtk \"Open Files\") on workspace web (hidden)\n" +
				"shot s6 960×540 of late window 0xc3\n" +
				"Your input to 0xa1 in an earlier act opened window 0xc3 \"Open Files\" after that act had returned; it is in shot s6."},
	}
	for _, c := range cases {
		line := summaryLine(&c.out, c.batch)
		hint := joinHints(waitHint(c.out.Wait, c.batch, "s4", c.out.Actions, c.out.Shot), lateHint(c.out.LateWindows))
		if c.out.WindowClosed {
			hint = joinHints(hint, closedHint(c.out.Source))
		}
		if hint != "" {
			line += "\n" + hint
		}
		if line != c.want {
			t.Errorf("%s:\n got %q\nwant %q", c.name, line, c.want)
		}
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

func TestDeadlineCountsLockWaiting(t *testing.T) {
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

func TestCancelWhileSending(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "hang")
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.senderLogged("start") })
		cancel()
	}()
	res := callTool(e.server, ctx, actOn, `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5},{"action":"type","text":"abc"}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := get(t, res, "wait.ended"); got != "cancelled" {
		t.Errorf("ended %v", got)
	}
	if got := get(t, res, "actions.delivery"); got != "partial" {
		t.Errorf("delivery %v", got)
	}
	var terminated bool
	for _, r := range readInputLog(t, e.inputLog) {
		terminated = terminated || r.Event == "sigterm"
	}
	if !terminated {
		t.Error("the sender got no SIGTERM")
	}
	if got := e.fake.requestsMatching("j/hyprmasq wait"); got != nil {
		t.Errorf("waited after the cancellation: %q", got)
	}
}

// A sender that SIGTERM kills before it handles signals has not connected, so nothing was sent.
func TestSenderStoppedBeforeItConnects(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	t.Setenv("FAKE_INPUT_MODE", "unhandled")
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.senderLogged("start") })
		cancel()
	}()
	res := callTool(e.server, ctx, actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	if res.IsError || get(t, res, "wait.ended") != "cancelled" || get(t, res, "actions.delivery") != "none" {
		t.Errorf("cancelled: %s", text(res))
	}

	start := time.Now()
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}],"deadline_ms":3500}`)
	if code := errorCode(t, res); code != "deadline" || get(t, res, "actions.delivery") != "none" || !strings.HasSuffix(get(t, res, "hint").(string), "Nothing was sent.") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if elapsed := time.Since(start); elapsed < inputTime(3500*time.Millisecond) || elapsed > 3500*time.Millisecond {
		t.Errorf("took %v", elapsed)
	}
}

// The deadline stops a sender early enough that it has its CANCEL_MS to report before the capture's reserve starts, so
// the call ends within its deadline.
func TestDeadlineStopsTheSenderInTime(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	t.Setenv("FAKE_INPUT_MODE", fmt.Sprintf("hang:1:%d", senderCancel.Milliseconds()))
	start := time.Now()
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]},{"action":"type","text":"abc"}],"deadline_ms":4000}`)
	elapsed := time.Since(start)
	if code := errorCode(t, res); code != "deadline" || get(t, res, "actions.delivery") != "partial" || get(t, res, "actions.failed_index") != 1.0 || !hasImage(res) {
		t.Fatalf("code %q: %s", code, text(res))
	}
	var stopped time.Time
	for _, r := range readInputLog(t, e.inputLog) {
		if r.Event == "sigterm" {
			stopped = time.Unix(0, r.Time)
		}
	}
	sendBy := start.Add(4000*time.Millisecond - captureReserve)
	if stopped.IsZero() || stopped.After(sendBy.Add(-senderCancel)) {
		t.Errorf("SIGTERM %v after the start, less than the sender's %v before the capture's reserve", stopped.Sub(start), senderCancel)
	}
	if captured := e.fake.timeOf("j/hyprmasq screenshot"); captured.After(sendBy) {
		t.Errorf("captured %v after the start, in the capture's reserve", captured.Sub(start))
	}
	if elapsed > 4000*time.Millisecond {
		t.Errorf("took %v, more than deadline_ms", elapsed)
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
		if a, b := e.fake.find("0xa1").Lease.Owner, e.fake.find("0xb2").Lease.Owner; a != e.server.Owner() || b != other.Owner() || a == b {
			t.Errorf("lease owners %q and %q", a, b)
		}
	})
}

func TestSendLockSerializesSenders(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	other := e.newServer()
	t.Setenv("FAKE_INPUT_MODE", "sleep:300")
	var wg sync.WaitGroup
	for _, c := range []struct {
		s      *Server
		window string
	}{{e.server, "0xa1"}, {other, "0xb2"}} {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if res := callTool(c.s, context.Background(), actOn, fmt.Sprintf(`{"window":%q,"actions":[{"action":"key","keys":["a"]}]}`, c.window)); res.IsError {
				t.Error(text(res))
			}
		}()
	}
	wg.Wait()
	runs := readInputLog(t, e.inputLog)
	if len(runs) != 4 {
		t.Fatalf("runs %v", runs)
	}
	for i, want := range []string{"start", "exit", "start", "exit"} {
		if runs[i].Event != want {
			t.Errorf("senders overlap: %v", runs)
			break
		}
	}
}

func TestNewWindows(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.server.tokens["t0k3n"] = true
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
	e.server.tokens["t0k3n"] = true
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
	for _, want := range []string{`The input opened new windows 0xd2 "Helper" in shot s3, 0xd5 "Odd" in shot s4.`,
		"New window 0xd2 opened on the user's visible workspace 1 and has their keyboard focus, so what they type goes there, since the plugin could not tell that hyprmasq's input opened it. Tell the user.",
		"New window 0xd5 opened on the user's visible workspace 1, since no window rule can name the workspace of the window it came from. Tell the user."} {
		if !strings.Contains(hint, want) {
			t.Errorf("hint %q lacks %q", hint, want)
		}
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

// Text without a line break opens no window, so the act does not wait for one after its wait.
func TestTypingWaitsForNoNewWindow(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.NewWindowGrace = 3 * time.Second
	e.window("0xa1", "1a", 320, 200)
	start := time.Now()
	if res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"hello"},{"action":"pause","ms":10}]}`); res.IsError {
		t.Fatal(text(res))
	}
	if took := time.Since(start); took > 2*time.Second {
		t.Errorf("typing took %v, as if it waited for new windows", took)
	}
	if !opensWindows([]action{{Kind: "type", Text: "hello\n"}}) || !opensWindows([]action{{Kind: "click"}}) || !opensWindows([]action{{Kind: "key"}}) ||
		opensWindows([]action{{Kind: "type", Text: "a\tb"}, {Kind: "scroll"}, {Kind: "move"}, {Kind: "pause"}}) {
		t.Error("opensWindows")
	}
}

// A window that maps a moment after the window that got the input has settled is still reported, with its shot.
func TestNewWindowAfterTheWait(t *testing.T) {
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

// After input that can open a window, act goes on waiting through the grace period, and a redraw in that time, such as
// a page that Enter loads, is waited out before the capture.
func TestRedrawAfterSettling(t *testing.T) {
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
	if hint, _ := get(t, res, "hint").(string); !strings.Contains(hint, "Shot "+id+" may show an older frame") {
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

func TestLaunch(t *testing.T) {
	e := newTestEnv(t)
	// A window of another launch whose process carries a foreign token.
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xc0", StableID: "c0", Class: "decoy", PID: 5001}, Width: 50, Height: 50})
	e.setLaunched(5001, "0123456789abcdef")
	var line, workspace, token string
	e.fake.with(func() {
		e.fake.onLaunch = func(w, k, l string) {
			workspace, token, line = w, k, l
			time.AfterFunc(250*time.Millisecond, func() {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", Title: "it's", PID: 5000,
					Workspace: hypr.Workspace{ID: -98, Name: "name:tests"}}, Width: 320, Height: 200})
			})
		}
	})
	res := e.launch(`{"command":["foot","-T","it's"],"workspace":"tests"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if line != `exec 'foot' '-T' 'it'\''s'` || workspace != "tests" || !regexp.MustCompile(`^[0-9a-f]{16}$`).MatchString(token) {
		t.Errorf("command line %q on %q as %q", line, workspace, token)
	}
	if got := get(t, res, "window.window"); got != "0xe1" {
		t.Errorf("window %v", got)
	}
	if !hasImage(res) || get(t, res, "shot.id") == nil || get(t, res, "wait.ended") != "quiet" {
		t.Errorf("result %s", text(res))
	}
	if args := waitArgsOf(t, e.fake.requestsMatching("j/hyprmasq wait 0xe1")[0]); args["expect"] != "" || args["quiet"] != "300" {
		t.Errorf("settle wait %v", args)
	}
	if !strings.HasPrefix(text(res), `launched 0xe1 foot "it's" · pid 5000`) || get(t, res, "hint") != nil {
		t.Errorf("text %q", text(res))
	}

	res = e.server.windowsTool(context.Background(), &call{})
	launched := map[string]bool{}
	for _, w := range get(t, res, "windows").([]any) {
		w := w.(map[string]any)
		launched[w["window"].(string)] = w["launched"].(bool)
	}
	if !reflect.DeepEqual(launched, map[string]bool{"0xc0": false, "0xe1": true}) {
		t.Errorf("launched %v", launched)
	}
}

// The window of a child process of the launched one counts, as a browser's that its launcher script starts without exec.
func TestLaunchFindsTheWindowOfAChild(t *testing.T) {
	e := newTestEnv(t)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			time.AfterFunc(100*time.Millisecond, func() {
				e.setParent(5001, 5000)
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe2", StableID: "e2", Class: "brave", PID: 5001,
					Workspace: hypr.Workspace{ID: -98, Name: "name:tests"}}, Width: 320, Height: 200})
			})
		}
	})
	res := e.launch(`{"command":["brave"],"workspace":"tests"}`)
	if res.IsError || get(t, res, "window.window") != "0xe2" || get(t, res, "window.pid") != 5001.0 {
		t.Fatal(text(res))
	}
	res = e.server.windowsTool(context.Background(), &call{})
	if w := get(t, res, "windows").([]any)[0].(map[string]any); w["window"] != "0xe2" || w["launched"] != true {
		t.Errorf("windows %v", w)
	}
}

// A launched window that closed between the plugin's list and the window list may have left its address to another
// window, which is not the launch's.
func TestLaunchIgnoresAWindowAtAReusedAddress(t *testing.T) {
	e := newTestEnv(t)
	var token string
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, k, line string) {
			token = k
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "bb", Class: "other", PID: 7000}, Width: 320, Height: 200})
		}
		e.fake.onPlugin = func(words []string) (string, bool) {
			if words[0] != "launched" || token == "" {
				return "", false
			}
			return jsonReply(map[string]any{"ok": true, "windows": []map[string]string{{"address": "0xe1", "stableId": "aa", "token": token}}}), true
		}
	})
	if code := errorCode(t, e.launch(`{"command":["foot"],"timeout_s":1}`)); code != "deadline" {
		t.Errorf("code %q", code)
	}
	res := e.server.windowsTool(context.Background(), &call{})
	if w := get(t, res, "windows").([]any)[0].(map[string]any); w["window"] != "0xe1" || w["launched"] != false {
		t.Errorf("windows %v", w)
	}
}

// A launched tiled window takes space from the windows on its workspace, whose earlier shots are then stale.
func TestLaunchNamesResizedWindows(t *testing.T) {
	e := newTestEnv(t)
	tests := hypr.Workspace{ID: -98, Name: "name:tests"}
	left := e.window("0xa1", "1a", 640, 480, func(w *fakeWindow) { w.Workspace = tests })
	e.window("0xb2", "2b", 640, 480, func(w *fakeWindow) { w.Workspace = tests })
	other := e.window("0xc3", "3c", 640, 480)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000, Workspace: tests}, Width: 320, Height: 480})
			// Only the size counts: 0xb2 keeps its own, and 0xc3 on another workspace is resized by something else.
			e.fake.with(func() { left.Width, other.Width = 320, 320 })
		}
	})
	res := e.launch(`{"command":["foot"],"workspace":"tests"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if hint := get(t, res, "hint"); hint != "The new window resized the windows on its workspace (0xa1), so their earlier shots are stale: take a new screenshot before clicking in one." {
		t.Errorf("hint %v", hint)
	}
}

func TestLaunchTimeout(t *testing.T) {
	e := newTestEnv(t)
	start := time.Now()
	res := e.launch(`{"command":["footclient"],"timeout_s":1}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "already running") {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// The window's settling and capture add up to 6 seconds to a launch that found its window.
	if elapsed := time.Since(start); elapsed > 4*time.Second {
		t.Errorf("took %v", elapsed)
	}
	for _, args := range []string{`{"command":[]}`, `{"command":["a"],"workspace":"x y"}`, `{"command":["a"],"timeout_s":0}`, `{"cmd":["a"]}`} {
		if code := errorCode(t, e.launch(args)); code != "invalid_arguments" {
			t.Errorf("%s: code %q", args, code)
		}
	}
}

func TestClose(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	e.window("0xc3", "3c", 100, 100, func(w *fakeWindow) { w.Floating = true })
	e.window("0xe4", "4e", 320, 200, func(w *fakeWindow) { w.Workspace = hypr.Workspace{ID: 2, Name: "2"} })
	e.screenshot(`{"window":"0xa1"}`)
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	onDispatch := func(expression string) string {
		switch expression {
		case `hl.dsp.window.close({ window = "stableid:1a" })`:
			time.AfterFunc(100*time.Millisecond, func() { e.fake.removeWindow("0xa1") })
		case `hl.dsp.window.close({ window = "stableid:3c" })`:
			e.fake.removeWindow("0xc3")
		case `hl.dsp.window.close({ window = "stableid:2b" })`:
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xd1", StableID: "d1", Class: "test.app", Title: "Save changes?", PID: 4242}, Width: 100, Height: 50})
		default:
			return "error: unexpected " + expression
		}
		return "ok"
	}
	e.fake.with(func() { e.fake.onDispatch = onDispatch })
	res := e.close(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "closed") != true {
		t.Fatal(text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xa1 release"); len(got) != 1 || got[0] != "j/hyprmasq lease 0xa1 release" {
		t.Errorf("lease release %q", got)
	}
	if e.server.shots.get("s1") != nil {
		t.Error("the closed window's shots were kept")
	}
	// The tiled window beside it takes up its space; the floating one and the one on another workspace keep their size.
	if hint := get(t, res, "hint"); hint != "Closing a tiled window resizes the tiled windows on its workspace (0xb2), so their earlier shots are stale: take a new screenshot before clicking in one." {
		t.Errorf("hint %v", hint)
	}
	if res := e.close(`{"window":"0xc3"}`); get(t, res, "closed") != true || get(t, res, "hint") != nil {
		t.Errorf("closing a floating window: %s", text(res))
	}

	start := time.Now()
	res = e.close(`{"window":"0xb2"}`)
	if res.IsError || get(t, res, "closed") != false {
		t.Fatal(text(res))
	}
	if elapsed := time.Since(start); elapsed < 2*time.Second || elapsed > 5*time.Second {
		t.Errorf("waited %v", elapsed)
	}
	if got := get(t, res, "new_windows.0.title"); got != "Save changes?" {
		t.Errorf("new windows %v", get(t, res, "new_windows"))
	}
	if !strings.Contains(text(res), "confirmation in the new window") {
		t.Errorf("text %q", text(res))
	}

	if code := errorCode(t, e.close(`{"window":"0xff"}`)); code != "window_gone" {
		t.Errorf("code %q", code)
	}
}

// A window that closes and leaves its address to another one before close dispatches is closed by its stable_id, so
// the other window stays open.
func TestCloseLeavesAWindowThatTookOverTheAddress(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() {
		e.fake.onDispatch = func(expression string) string {
			// Hyprland runs the dispatcher after the window closed and another took its address.
			e.fake.removeWindow("0xa1")
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xa1", StableID: "5f", Class: "other.app", Title: "Other", PID: 5000}, Width: 320, Height: 200})
			selector := strings.TrimSuffix(strings.TrimPrefix(expression, `hl.dsp.window.close({ window = "`), `" })`)
			e.fake.with(func() {
				e.fake.windows = slices.DeleteFunc(e.fake.windows, func(w *fakeWindow) bool {
					return selector == "stableid:"+w.StableID || selector == "address:"+w.Address
				})
			})
			return "ok"
		}
	})
	res := e.close(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "closed") != true {
		t.Fatal(text(res))
	}
	if w := e.fake.find("0xa1"); w == nil || w.StableID != "5f" {
		t.Errorf("the window that took over the address was closed: %q", e.fake.requestsMatching("/dispatch"))
	}
}

func TestErrorMapping(t *testing.T) {
	cases := []struct {
		err  error
		code string
	}{
		{&hypr.Error{Code: "window_gone", Message: "no mapped window"}, "window_gone"},
		{&hypr.Error{Code: "unsupported", Message: "x11"}, "unsupported"},
		{&hypr.Error{Code: "unloaded", Message: "unloaded"}, "plugin_missing"},
		{&hypr.Error{Code: "internal", Message: "render failed"}, "internal"},
		{&hypr.Error{Message: "unknown subcommand"}, "plugin_version"},
		{context.DeadlineExceeded, "deadline"},
		{errCancelled, "cancelled"},
		{errors.New("boom"), "internal"},
	}
	for _, c := range cases {
		if got := asToolError(c.err); got.Code != c.code {
			t.Errorf("%v: %q, want %q", c.err, got.Code, c.code)
		}
	}
}

func TestPluginChecks(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() { e.fake.iface = 1 })
	res := e.act(`{"window":"0xa1"}`)
	if code := errorCode(t, res); code != "plugin_version" || !strings.Contains(text(res), "Tell the user") {
		t.Errorf("code %q: %s", code, text(res))
	}
	e.fake.with(func() { e.fake.missing = true })
	if code := errorCode(t, e.screenshot(`{"window":"0xa1"}`)); code != "plugin_missing" {
		t.Errorf("code %q", code)
	}
	e.server.hypr.Socket = "/nonexistent/.socket.sock"
	res = e.server.windowsTool(context.Background(), &call{})
	if code := errorCode(t, res); code != "plugin_missing" || !strings.Contains(text(res), "HYPRLAND_INSTANCE_SIGNATURE") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if code := errorCode(t, e.act(`{"window":"0xa1"}`)); code != "plugin_missing" {
		t.Errorf("code %q", code)
	}
}

func TestSenderFailures(t *testing.T) {
	cases := []struct {
		name, mode, actions, code, delivery string
		failed                              any
		keys                                any
		captured                            bool
		hint                                string
	}{
		{"cannot type", "fail:cannot_type:0:none:0", `[{"action":"type","text":"é"}]`, "cannot_type", "none", 0.0, nil, true, "Leave the character out"},
		{"unknown key", "fail:invalid_arguments:0:none:0", `[{"action":"key","keys":["nosuchkey"]}]`, "invalid_arguments", "none", 0.0, nil, true, "Nothing was sent"},
		{"input paused", "fail:input_paused:2:partial:14", `[{"action":"click","x":5,"y":5},{"action":"type","text":"hello world, this is long"}]`, "input_paused", "partial", 1.0, 14.0, true, "ask the user"},
		{"unconfirmed", "fail:unconfirmed:0:uncertain:0", `[{"action":"key","keys":["a"]}]`, "unconfirmed", "uncertain", 0.0, nil, true, "may or may not have been applied"},
		{"connect failed", "fail:connect_failed:0:none:0", `[{"action":"key","keys":["a"]}]`, "connect_failed", "none", 0.0, nil, true, "Retry once"},
		// The sender refuses points outside the pointer's regions, which end at the monitor's edges.
		{"outside the regions", "fail:invalid_arguments:1:none:0", `[{"action":"key","keys":["a"]},{"action":"click","x":5,"y":5}]`, "out_of_bounds", "none", 1.0, nil, true,
			"up to the edges of its monitor"},
		{"wheel", "fail:invalid_arguments:1:none:0", `[{"action":"scroll","x":5,"y":5,"dy":1}]`, "invalid_arguments", "none", 0.0, nil, true, "Fix the action"},
		// Something other than this server sent the sender SIGTERM.
		{"killed", "fail:cancelled:1:partial:3", `[{"action":"key","keys":["a"]},{"action":"type","text":"hello"}]`, "cancelled", "partial", 1.0, 3.0, true, "Look at the shot"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			t.Setenv("FAKE_INPUT_MODE", c.mode)
			res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s,"capture":"window"}`, c.actions))
			if code := errorCode(t, res); code != c.code {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if got := get(t, res, "actions.delivery"); got != c.delivery {
				t.Errorf("delivery %v", got)
			}
			if got := get(t, res, "actions.failed_index"); got != c.failed {
				t.Errorf("failed index %v", got)
			}
			if got := get(t, res, "actions.progress.keys"); got != c.keys {
				t.Errorf("keys %v", got)
			}
			if hasImage(res) != c.captured || (get(t, res, "shot") != nil) != c.captured {
				t.Errorf("captured %v: %s", hasImage(res), text(res))
			}
			hint := get(t, res, "hint").(string)
			if !strings.Contains(hint, c.hint) {
				t.Errorf("hint %q", hint)
			}
			if c.delivery != "none" && strings.Contains(strings.ToLower(hint), "retry") {
				t.Errorf("hint invites repeating sent input: %q", hint)
			}
			if !strings.HasPrefix(text(res), "error "+c.code+": ") {
				t.Errorf("text %q", text(res))
			}
			if got := afterBatch(t, e.fake.requestsMatching("j/hyprmasq wait")); got != nil {
				t.Errorf("waited after a failure: %q", got)
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
	if !strings.Contains(text(res), "capture failed: the renderer failed") || !strings.Contains(text(res), "Take a screenshot before repeating anything") {
		t.Errorf("text %q", text(res))
	}
	// Without input it is an error.
	if code := errorCode(t, e.act(`{"window":"0xa1"}`)); code != "internal" {
		t.Errorf("code %q", code)
	}
}

func TestSessionEndReleasesOwnLeases(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	b := e.window("0xb2", "2b", 320, 200)
	e.window("0xc3", "3c", 320, 200, func(w *fakeWindow) { w.XWayland, w.Caps = true, hypr.Capabilities{Keyboard: "x11-focus"} })
	other := e.newServer()
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.act(`{"window":"0xc3","actions":[{"action":"key","keys":["a"]}]}`)
	if got := get(t, res, "lease"); !reflect.DeepEqual(got, map[string]any{"held": false, "remaining_s": 0.0}) {
		t.Errorf("X11 lease %v: %s", got, text(res))
	}
	callTool(other, context.Background(), actOn, `{"window":"0xb2","actions":[{"action":"key","keys":["a"]}]}`)
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease == nil || b.Lease == nil {
			t.Error("no leases")
		}
	})
	shots := e.server.shotDir
	if _, err := os.Stat(shots); err != nil {
		t.Fatal(err)
	}
	e.server.Shutdown(context.Background())
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("own lease kept")
		}
		if b.Lease == nil || b.Lease.Owner != other.Owner() {
			t.Error("another session's lease was released")
		}
	})
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xb2 release"); got != nil {
		t.Errorf("released %q", got)
	}
	if _, err := os.Stat(shots); !os.IsNotExist(err) {
		t.Errorf("shots kept: %v", err)
	}
}

func TestShotStoreEvictsLeastRecentlyUsed(t *testing.T) {
	dir := t.TempDir()
	st := newShotStore()
	add := func(window string) *shot {
		s := &shot{ID: st.newID(), Window: window, Path: dir + "/" + strconv.Itoa(st.next) + ".png"}
		os.WriteFile(s.Path, nil, 0o600)
		st.add(s)
		return s
	}
	first := add("0xa1")
	second := add("0xa1")
	add("0xb2")
	for range shotsPerWindow - 2 {
		add("0xa1")
	}
	st.get(first.ID)
	add("0xa1")
	if st.get(first.ID) == nil {
		t.Error("a recently used shot was dropped")
	}
	if st.get(second.ID) != nil {
		t.Error("the least recently used shot was kept")
	}
	if _, err := os.Stat(second.Path); !os.IsNotExist(err) {
		t.Error("the dropped shot's file was kept")
	}
	if st.get("s3") == nil {
		t.Error("another window's shot was dropped")
	}
}

func TestShotsOfWindowsThatClosedElsewhere(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	e.window("0xc3", "3c", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.screenshot(`{"window":"0xb2"}`)
	e.screenshot(`{"window":"0xc3"}`)
	// One window closes, and another one reuses the address of a second one.
	e.fake.removeWindow("0xa1")
	e.fake.removeWindow("0xb2")
	e.window("0xb2", "4d", 320, 200)
	if res := e.server.windowsTool(context.Background(), &call{}); res.IsError {
		t.Fatal(text(res))
	}
	for id, kept := range map[string]bool{"s1": false, "s2": false, "s3": true} {
		if (e.server.shots.get(id) != nil) != kept {
			t.Errorf("%s kept: %v", id, !kept)
		}
	}
	if files, _ := os.ReadDir(e.server.shotDir); len(files) != 1 {
		t.Errorf("files %v", files)
	}

	// A list requested before a shot got its ID says nothing about its window.
	st := newShotStore()
	st.add(&shot{ID: st.newID(), Window: "0xa1", StableID: "1a"})
	mark := st.mark()
	st.add(&shot{ID: st.newID(), Window: "0xe5", StableID: "5e"})
	st.dropClosed(nil, mark)
	if st.get("s1") != nil || st.get("s2") == nil {
		t.Error("dropped the wrong shots")
	}
}

func TestWindowsTool(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Resizing = true })
	e.window("0xb2", "2b", 640, 480, func(w *fakeWindow) { w.Workspace = hypr.Workspace{ID: -98, Name: "name:hyprmasq"} })
	e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.server.windowsTool(context.Background(), &call{args: json.RawMessage(`{}`)})
	if res.IsError {
		t.Fatal(text(res))
	}
	want := map[string]any{"window": "0xb2", "stable_id": "2b", "class": "test.app", "title": "Test", "pid": 4242.0, "workspace": "name:hyprmasq",
		"visible": false, "size": map[string]any{"width": 640.0, "height": 480.0}, "resizing": false, "xwayland": false, "launched": false,
		"lease":        map[string]any{"held": true, "remaining_s": 60.0, "mine": true},
		"capabilities": map[string]any{"lease": true, "hold": true, "keyboard": "yes"}}
	if got := get(t, res, "windows.1"); !reflect.DeepEqual(got, want) {
		t.Errorf("window %v", got)
	}
	if got := get(t, res, "windows.0.visible"); got != true {
		t.Errorf("visible %v", got)
	}
	if got := get(t, res, "windows.0.resizing"); got != true {
		t.Errorf("resizing %v", got)
	}
	// The lines leave out the pid and the capabilities of a window that has them all.
	if got := strings.Split(text(res), "\n"); !reflect.DeepEqual(got, []string{
		`0xa1 · test.app "Test" · workspace 1 (visible) · 320×200 · resizing (not yet redrawn at its new size)`,
		`0xb2 · test.app "Test" · workspace name:hyprmasq (hidden) · 640×480 · lease 60 s (this session)`,
	}) {
		t.Errorf("lines %q", got)
	}
	if code := errorCode(t, e.server.windowsTool(context.Background(), &call{args: json.RawMessage(`{"all":true}`)})); code != "invalid_arguments" {
		t.Errorf("code %q", code)
	}
}

// match keeps the windows whose class or title contains it, ignoring case as Unicode case folding does. A window that
// lacks a capability names what it lacks. A list without windows says why, also in the structured content.
func TestWindowsMatch(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 1264, 687, func(w *fakeWindow) {
		w.Class, w.Title, w.XWayland = "Spotify", "Monolith - Crossfaith", true
		w.Caps = hypr.Capabilities{Hold: true, Keyboard: "x11-focus"}
	})
	e.window("0xb2", "2b", 640, 480, func(w *fakeWindow) { w.Class, w.Title = "org.gnome.Evolution", "Inbox (3 unread)" })
	e.window("0xc3", "3c", 640, 480, func(w *fakeWindow) { w.Class, w.Title = "foot", "spotify-tui" })
	e.window("0xd4", "4d", 640, 480, func(w *fakeWindow) { w.Class, w.Title = "org.kde.okular", "Οδυσσεύς" })
	windows := func(args string) (*mcp.CallToolResult, []string) {
		t.Helper()
		res := e.server.windowsTool(context.Background(), &call{args: json.RawMessage(args)})
		if res.IsError {
			t.Fatal(text(res))
		}
		var addresses []string
		for _, w := range get(t, res, "windows").([]any) {
			addresses = append(addresses, w.(map[string]any)["window"].(string))
		}
		return res, addresses
	}
	// Several windows come without shots.
	if res, got := windows(`{"match":"SPOT"}`); !reflect.DeepEqual(got, []string{"0xa1", "0xc3"}) || hasImage(res) || get(t, res, "windows.0.shot") != nil {
		t.Errorf("class or title with SPOT: %v %s", got, text(res))
	}
	// One window comes with its shot.
	res, got := windows(`{"match":"inbox"}`)
	if !reflect.DeepEqual(got, []string{"0xb2"}) || text(res) != "0xb2 · org.gnome.Evolution \"Inbox (3 unread)\" · workspace 1 (visible) · 640×480\nshot s1 1280×960 of window 0xb2" ||
		get(t, res, "windows.0.shot.id") != "s1" || get(t, res, "summary") != "shot s1 1280×960 of window 0xb2" || len(res.Content) != 2 {
		t.Errorf("title with inbox: %v %q", got, text(res))
	}
	if res, _ := windows(`{"match":"monolith"}`); text(res) != "0xa1 · Spotify \"Monolith - Crossfaith\" · workspace 1 (visible) · 1264×687 · xwayland · lacks lease · keyboard x11-focus\nshot s2 2528×1374 of window 0xa1" {
		t.Errorf("an X11 window: %q", text(res))
	}
	if res, _ := windows(`{"match":"SPOT"}`); get(t, res, "summary") != nil {
		t.Errorf("a summary beside the windows: %v", get(t, res, "summary"))
	}
	// Lowercasing alone would turn Σ into σ and keep the final ς.
	for _, match := range []string{"ΟΔΥΣΣΕΎΣ", "Σ", "ς"} {
		if _, got := windows(`{"match":"` + match + `"}`); !reflect.DeepEqual(got, []string{"0xd4"}) {
			t.Errorf("%s: %v", match, got)
		}
	}
	res, got = windows(`{"match":"steam"}`)
	if got != nil || text(res) != `no window has "steam" in its class or title, of 4 windows` || get(t, res, "summary") != text(res) {
		t.Errorf("no match: %v %q %v", got, text(res), get(t, res, "summary"))
	}
	if _, got := windows(`{"match":""}`); len(got) != 4 {
		t.Errorf("empty match: %v", got)
	}
	empty := newTestEnv(t).server.windowsTool(context.Background(), &call{})
	if text(empty) != "no windows" || get(t, empty, "summary") != "no windows" {
		t.Errorf("no windows: %q %v", text(empty), get(t, empty, "summary"))
	}
}

func TestRelease(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.release(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "released") != true {
		t.Fatal(text(res))
	}
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("lease kept")
		}
	})
	res = e.release(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "released") != false {
		t.Fatal(text(res))
	}
}

// Hyprland and the plugin write addresses in lower case without leading zeros, and compare them as strings, so the tools
// take an address in another form as that one.
func TestAddressInAnotherForm(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	res := e.screenshot(`{"window":"0xA1"}`)
	if res.IsError || !strings.Contains(text(res), "window 0xa1 ") {
		t.Fatal(text(res))
	}
	res = e.act(`{"window":"0x00A1","shot":"` + shotID(t, res) + `","actions":[{"action":"click","x":10,"y":10}]}`)
	if res.IsError || len(e.inputs()) != 1 {
		t.Fatal(text(res))
	}
	if res := e.release(`{"window":"0xA1"}`); res.IsError || get(t, res, "released") != true {
		t.Fatal(text(res))
	}
	e.fake.with(func() {
		e.fake.onDispatch = func(expression string) string {
			if expression == `hl.dsp.window.close({ window = "stableid:1a" })` {
				e.fake.removeWindow("0xa1")
			}
			return "ok"
		}
	})
	if res := e.close(`{"window":"0x0a1"}`); res.IsError || get(t, res, "closed") != true || !strings.HasPrefix(text(res), "closed 0xa1 ") {
		t.Fatal(text(res))
	}
	for _, request := range e.fake.requestsMatching("j/hyprmasq ") {
		if words := strings.Fields(request); len(words) > 2 && strings.HasPrefix(words[2], "0x") && words[2] != "0xa1" {
			t.Errorf("requested %q", request)
		}
	}
}

// Claude Code cuts tool descriptions after 2048 UTF-16 code units, so the rules at the end of a longer one would never
// reach the model.
func TestDescriptionsFitClaudeCode(t *testing.T) {
	session := connect(t, newTestEnv(t).server, nil)
	tools, err := session.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, tool := range tools.Tools {
		if n := len(utf16.Encode([]rune(tool.Description))); n > 2048 {
			t.Errorf("the %s description has %d code units, more than 2048", tool.Name, n)
		}
	}
}

// Without the lease capability, a menu that a page draws stayed open between calls, and agents that took the lease for
// what keeps menus open, or its absence for what closes them, clicked the opener of a menu that their shot showed open,
// which closed it. So the lease is described by the focus it keeps, no text ties it or the focus to menus or says that
// they stay open or are gone by the next call, and a summary names the lease only while one is held.
func TestLeaseNotTiedToMenus(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xc3", "3c", 320, 200, func(w *fakeWindow) { w.XWayland, w.Caps = true, hypr.Capabilities{Keyboard: "x11-focus"} })
	session := connect(t, e.server, nil)
	tools, err := session.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	texts := map[string]string{"the instructions": session.InitializeResult().Instructions}
	for _, tool := range tools.Tools {
		schema, _ := json.Marshal(tool.InputSchema)
		texts[tool.Name] = tool.Description + "\n" + string(schema)
	}
	key := `{"action":"key","keys":["a"]}`
	leased := e.act(`{"window":"0xa1","actions":[` + key + `]}`)
	unleased := e.act(`{"window":"0xc3","actions":[` + key + `]}`)
	texts["a leased result"], texts["an unleased result"] = text(leased), text(unleased)
	if !strings.Contains(text(leased), " · lease 60 s") || strings.Contains(text(unleased), "lease") {
		t.Errorf("leased %q, unleased %q", text(leased), text(unleased))
	}
	menu := regexp.MustCompile(`(?i)\b(menu|popup|popover|dropdown)s?\b`)
	claim := regexp.MustCompile(`(?i)\blease\b|\bfocus\b|\bstays? open\b|\b(is|are|be) gone\b|\bmay close\b|\bnext call\b`)
	for name, s := range texts {
		for _, clause := range regexp.MustCompile(`[.;:]\s|\\n|\n`).Split(s, -1) {
			if menu.MatchString(clause) && claim.MatchString(clause) {
				t.Errorf("%s: %q", name, clause)
			}
		}
	}
}

// Claude Code passes the model a result's structured content in place of its text, so by default it gets none and
// passes the text, as other clients do. --structured-content always and never decide it for every client.
func TestStructuredContentPerClient(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	for _, c := range []struct {
		setting, client string
		structured      bool
	}{
		{"", "claude-code", false},
		{StructuredAuto, "claude-code", false},
		{StructuredAuto, "codex-mcp-client", true},
		{StructuredAlways, "claude-code", true},
		{StructuredNever, "codex-mcp-client", false},
	} {
		e.server.cfg.StructuredContent = c.setting
		session := connectAs(t, e.server, c.client, nil)
		for _, params := range []*mcp.CallToolParams{
			{Name: "windows", Arguments: map[string]any{}},
			{Name: "screenshot", Arguments: map[string]any{"window": "0xa1"}},
			{Name: "act", Arguments: map[string]any{"window": "0xa1", "actions": []any{map[string]any{"action": "key", "keys": []string{"a"}}}}},
			{Name: "act", Arguments: map[string]any{"window": "0xff"}},
		} {
			res, err := session.CallTool(context.Background(), params)
			if err != nil {
				t.Fatal(err)
			}
			if (res.StructuredContent != nil) != c.structured || text(res) == "" {
				t.Errorf("%q for %s: %s structured content %v, text %q", c.setting, c.client, params.Name, res.StructuredContent, text(res))
			}
		}
	}
}

// TestProtocol runs the tools through an MCP client: the tool list, progress during a wait and cancellation.
func TestProtocol(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	var mu sync.Mutex
	var progress []string
	session := connect(t, e.server, &mcp.ClientOptions{
		ProgressNotificationHandler: func(_ context.Context, req *mcp.ProgressNotificationClientRequest) {
			mu.Lock()
			progress = append(progress, req.Params.Message)
			mu.Unlock()
		},
	})
	ctx := context.Background()
	if !strings.HasPrefix(session.InitializeResult().Instructions, "hyprmasq: see, click and type") {
		t.Error("no instructions")
	}
	tools, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	readOnly := map[string]bool{}
	notDestructive := map[string]bool{}
	for _, tool := range tools.Tools {
		readOnly[tool.Name] = tool.Annotations != nil && tool.Annotations.ReadOnlyHint
		notDestructive[tool.Name] = tool.Annotations != nil && tool.Annotations.DestructiveHint != nil && !*tool.Annotations.DestructiveHint
		if !strings.Contains(tool.Description, "Example: {") {
			t.Errorf("%s has no example", tool.Name)
		}
	}
	// screenshot opens a socket that stays open, and so does windows when it takes a shot, so they are not read-only.
	if !reflect.DeepEqual(readOnly, map[string]bool{"windows": false, "screenshot": false, "act": false, "launch": false, "release": false, "close": false}) {
		t.Errorf("read-only tools %v", readOnly)
	}
	if !reflect.DeepEqual(notDestructive, map[string]bool{"windows": true, "screenshot": true, "act": false, "launch": true, "release": true, "close": false}) {
		t.Errorf("tools that are not destructive %v", notDestructive)
	}
	// pi shows only the tool descriptions, so they carry the rules for acting. Codex adds the instructions to every
	// description, so the rules are said once, and no sentence of the instructions is said again in a description.
	descriptions := map[string]string{}
	everything := session.InitializeResult().Instructions
	for _, tool := range tools.Tools {
		descriptions[tool.Name] = tool.Description
		everything += "\n" + tool.Description
	}
	for tool, rules := range map[string][]string{
		"act": {"Drive only windows the user named or you launched", "ask the user before anything irreversible: sending, deleting, buying, submitting",
			"Never repeat input whose delivery is partial or uncertain, or from a call that timed out", "Batch only targets visible in the shot",
			"a line break presses Enter and a tab presses Tab", "a call types about 600 characters, or 1400 with deadline_ms 30000",
			"Whether a menu is still open shows in the shot", "as clicking its opener closes it",
			"drag, clicks with modifiers, and chords holding a key other than a modifier while pressing the next need the hold capability",
			`"shot s7 = s5 + 2 changes" has s5's pixels, so keep reading coordinates from s5`, `capture "window" asks for one`},
		"windows": {"needed for drag, for clicks with modifiers, and for key chords that hold a key other than a modifier",
			"without it, the window loses the focus after each call, and what its app closes on focus loss may be gone by the next call"},
		"launch":  {"Close the windows you launch when you are done"},
		"close":   {"Close only windows you launched or the user asked you to close"},
		"release": {"an act without input only renews one that is held"},
	} {
		for _, rule := range rules {
			if !strings.Contains(descriptions[tool], rule) || strings.Count(everything, rule) != 1 {
				t.Errorf("%s does not say %q once", tool, rule)
			}
		}
	}
	for _, sentence := range regexp.MustCompile(`[.;:]\s+`).Split(session.InitializeResult().Instructions, -1) {
		for tool, description := range descriptions {
			if len(sentence) > 20 && strings.Contains(description, strings.TrimSpace(sentence)) {
				t.Errorf("the instructions and %s both say %q", tool, sentence)
			}
		}
	}
	if strings.Contains(descriptions["windows"], "dnd") {
		t.Error("windows describes dnd, which act cannot use")
	}
	for _, tool := range tools.Tools {
		if schema, _ := json.Marshal(tool.InputSchema); tool.Name == "act" && !strings.Contains(string(schema), "input has to end 2250 ms before the deadline") {
			t.Errorf("act schema %s", schema)
		}
	}

	res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "screenshot", Arguments: map[string]any{"window": "0xa1"}})
	if err != nil || res.IsError || len(res.Content) != 2 {
		t.Fatalf("%v %v", err, res)
	}
	// Clients that pass the model only the structured content get the summary too.
	if summary := get(t, mirrored(t, res), "summary"); summary != "shot s1 640×400 · window 0xa1 320×200" {
		t.Errorf("summary %v", summary)
	}
	if img, ok := res.Content[1].(*mcp.ImageContent); !ok || img.MIMEType != "image/png" || len(img.Data) == 0 {
		t.Errorf("image %v", res.Content[1])
	}

	release := make(chan struct{})
	e.fake.with(func() { e.fake.onWait = blockingWait(release) })
	// The wait ends once progress about it has arrived, at about a second into the wait.
	go func() {
		defer close(release)
		e.waitFor(func() bool {
			mu.Lock()
			defer mu.Unlock()
			return len(progress) >= 2 && progress[len(progress)-1] == "waiting for the window to settle"
		})
	}()
	res, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "act", Meta: mcp.Meta{"progressToken": "p1"},
		Arguments: map[string]any{"window": "0xa1", "shot": "s1", "actions": []any{map[string]any{"action": "key", "keys": []string{"a"}}}}})
	if err != nil || res.IsError {
		t.Fatalf("%v %v", err, res)
	}
	mu.Lock()
	if len(progress) < 2 || progress[len(progress)-1] != "waiting for the window to settle" {
		t.Errorf("progress %q", progress)
	}
	mu.Unlock()

	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	callCtx, cancel := context.WithCancel(ctx)
	waits := len(e.fake.requestsMatching("j/hyprmasq wait"))
	go func() {
		e.waitFor(func() bool { return len(e.fake.requestsMatching("j/hyprmasq wait")) > waits })
		cancel()
	}()
	_, err = session.CallTool(callCtx, &mcp.CallToolParams{Name: "act", Arguments: map[string]any{"window": "0xa1", "shot": "s1"}})
	if !errors.Is(err, context.Canceled) {
		t.Errorf("err %v", err)
	}
	e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq cancel ") != nil })
	if len(e.fake.requestsMatching("j/hyprmasq cancel ")) == 0 {
		t.Error("the plugin's wait was not cancelled")
	}
}

func TestHoldIsCheckedAgainAfterTheLock(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	// The user focuses a window of the same connection while the call waits for the lock.
	states := 0
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if states++; states > 1 {
				w.Caps.Hold = false
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"drag","from":[1,1],"to":[50,50]}]}`)
	if code := errorCode(t, res); code != "unsupported" || get(t, res, "actions.delivery") != "none" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil {
		t.Errorf("sent %q", e.inputs())
	}
}

// Without hold, the user's own input to the app may take the keyboard back between the frames of a chord, after which
// the plugin enters the window again with no keys held, but with the modifier state. So chords that hold only modifiers
// before their last key need no hold.
func TestChordsOfModifiersNeedNoHold(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Caps.Hold = false })
	for _, keys := range []string{`["ctrl","shift","t"]`, `["Shift_L","a"]`, `["iso_level3_shift","q"]`, `["ctrl","shift"]`, `["ctrl+plus"]`, `["a"]`} {
		res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":` + keys + `,"repeat":2}],"capture":"none"}`)
		if res.IsError {
			t.Errorf("%s: %s", keys, text(res))
		}
	}
	if got := len(e.inputs()); got != 6 {
		t.Errorf("%d senders", got)
	}
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","a","c"]}]}`)
	if code := errorCode(t, res); code != "unsupported" || !strings.HasPrefix(get(t, res, "hint").(string), "This window has the user's keyboard or pointer focus") ||
		!strings.Contains(get(t, res, "hint").(string), "Hold only modifiers, or press the keys in separate key actions. Nothing was sent.") {
		t.Errorf("code %q: %s", code, text(res))
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
	if !strings.HasPrefix(hint.(string), "The user's input took this app's shared keyboard focus back from this window shortly after a shortcut or function key of the batch, so it may or may not have taken effect. Take a screenshot and look at it: if it shows the result, go on; if it is unclear, verify") {
		t.Errorf("hint %q", hint)
	}
	if shared.Interrupted != 1 {
		t.Fatalf("counter %d", shared.Interrupted)
	}
	// With a shot, the hint points at it.
	interrupt("")
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"window"}`)
	if res.IsError || !flagged(res) || get(t, res, "hint") != focusReturnedHint(&shotOut{}) || !strings.Contains(get(t, res, "hint").(string), " Look at the shot: if it shows the result") {
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

// A call that sends nothing leaves the lease as it was: it takes none while it waits for the locks, and gives back one
// that it renewed for input that was refused.
func TestLeaseOfABatchThatSentNothing(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	other := e.newServer()
	callTool(other, context.Background(), actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	e.fake.with(func() { w.Lease.RemainingMs = 20000 })
	owner := func() (string, int) {
		var o string
		var ms int
		e.fake.with(func() { o, ms = w.Lease.Owner, w.Lease.RemainingMs })
		return o, ms
	}

	// Cancelled and timed out while another window's batch holds the send lock.
	sendLock, err := acquireLock(context.Background(), e.server.sendLockPath())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return locked(e.server.lockPath("1a")) })
		time.Sleep(100 * time.Millisecond)
		cancel()
	}()
	if res := callTool(e.server, ctx, actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["b"]}]}`); get(t, res, "wait.ended") != "cancelled" {
		t.Errorf("cancelled: %s", text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["b"]}],"deadline_ms":2500}`); errorCode(t, res) != "deadline" {
		t.Errorf("deadline: %s", text(res))
	}
	sendLock.release()
	if o, ms := owner(); o != other.Owner() || ms != 20000 {
		t.Errorf("lease of %s with %d ms left", o, ms)
	}

	// Refused by the sender after the renewal.
	t.Setenv("FAKE_INPUT_MODE", "fail:cannot_type:0:none:0")
	if res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"é"}]}`); errorCode(t, res) != "cannot_type" {
		t.Fatal(text(res))
	}
	if o, ms := owner(); o != other.Owner() || ms > 20000 || ms < 15000 {
		t.Errorf("lease of %s with %d ms left", o, ms)
	}
	e.server.mu.Lock()
	mine := e.server.leased["0xa1"]
	e.server.mu.Unlock()
	if mine {
		t.Error("the session counts the lease it gave back as its own")
	}
}

func TestResizeDuringASingleDragStopsIt(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "hang:0")
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if e.senderLogged("start") {
				w.Width = 400
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"drag","from":[5,5],"to":[300,300]}]}`)
	if code := errorCode(t, res); code != "stale_shot" || !e.senderLogged("sigterm") {
		t.Fatalf("code %q: %s", code, text(res))
	}
}

// A resize that stops the sender before it connected sent nothing.
func TestResizeBeforeTheSenderConnects(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "unhandled")
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if e.senderLogged("start") {
				w.Width = 400
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`)
	if code := errorCode(t, res); code != "stale_shot" || get(t, res, "actions.delivery") != "none" || !strings.HasSuffix(get(t, res, "hint").(string), "Nothing was sent.") {
		t.Errorf("code %q: %s", code, text(res))
	}
}

func TestHintsNeverInviteRepeatingSentInput(t *testing.T) {
	for _, delivery := range []string{"partial", "uncertain", "complete"} {
		for _, shot := range []*shotOut{nil, {ID: "s2"}} {
			out := &actOut{Actions: &actionsOut{Done: 1, Total: 2, Delivery: delivery}, Shot: shot}
			hint := finalHint(asToolError(context.DeadlineExceeded), out)
			if !strings.Contains(hint, "before repeating anything") || strings.Contains(hint, "Retry") {
				t.Errorf("%s with shot %v: %q", delivery, shot != nil, hint)
			}
		}
	}
	if hint := finalHint(asToolError(context.DeadlineExceeded), &actOut{Actions: &actionsOut{Total: 2, Delivery: "none"}}); !strings.Contains(hint, "Nothing was sent.") {
		t.Errorf("none: %q", hint)
	}

	// A wait that fails after the input was applied.
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			return pluginError("internal", "the wait broke")
		}
	})
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`)
	if code := errorCode(t, res); code != "internal" || get(t, res, "actions.delivery") != "complete" || !hasImage(res) {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if hint := get(t, res, "hint").(string); hint != "Look at the shot before repeating anything." {
		t.Errorf("hint %q", hint)
	}
}

func TestReleaseEndsAnySessionsLease(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	other := e.newServer()
	callTool(other, context.Background(), actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.release(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "released") != true || !strings.Contains(text(res), "another session") {
		t.Fatal(text(res))
	}
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("lease kept")
		}
	})
	// A window without a socket holds no lease.
	res = e.release(`{"window":"0xb2"}`)
	if res.IsError || get(t, res, "released") != false {
		t.Fatal(text(res))
	}
	if code := errorCode(t, e.release(`{"window":"0xff"}`)); code != "window_gone" {
		t.Errorf("code %q", code)
	}
}

func TestShutdownCancelsRunningCalls(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	handler := e.server.handle(e.server.actTool)
	done := make(chan *mcp.CallToolResult)
	go func() {
		res, _ := handler(context.Background(), &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{
			Arguments: json.RawMessage(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)}})
		done <- res
	}()
	for len(e.fake.requestsMatching("j/hyprmasq wait")) == 0 {
		time.Sleep(5 * time.Millisecond)
	}
	start := time.Now()
	e.server.Shutdown(context.Background())
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Errorf("shutdown took %v", elapsed)
	}
	res := <-done
	if got := get(t, res, "wait.ended"); got != "cancelled" {
		t.Errorf("ended %v: %s", got, text(res))
	}
	// The lease the call took is released, and the call did not renew it afterwards.
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("lease kept after the session ended")
		}
	})
	res, _ = handler(context.Background(), &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{Arguments: json.RawMessage(`{"window":"0xa1"}`)}})
	if code := errorCode(t, res); code != "cancelled" {
		t.Errorf("a call after the end: %q", code)
	}
}

// A client that closes the server's stdin ends the session: the calls still running end without a capture or a renewed
// lease, as on a signal.
func TestStdinEndStopsCalls(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	clientRead, serverWrite := io.Pipe()
	serverRead, clientWrite := io.Pipe()
	ran := make(chan error, 1)
	go func() {
		ran <- e.server.NewMCPServer().Run(context.Background(), &mcp.IOTransport{Reader: serverRead, Writer: serverWrite})
	}()
	client := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "0"}, nil)
	session, err := client.Connect(context.Background(), &mcp.IOTransport{Reader: clientRead, Writer: clientWrite}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	go session.CallTool(context.Background(), &mcp.CallToolParams{Name: "act", Arguments: map[string]any{
		"window": "0xa1", "actions": []any{map[string]any{"action": "key", "keys": []string{"a"}}}}})
	if !e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq wait") != nil }) {
		t.Fatal("no wait")
	}
	shots, leases := len(e.fake.requestsMatching("j/hyprmasq screenshot")), len(e.fake.requestsMatching("j/hyprmasq lease"))
	clientWrite.Close()
	select {
	case <-ran:
	case <-time.After(5 * time.Second):
		t.Fatal("the session did not end")
	}
	if !e.server.stopping() {
		t.Error("the server goes on taking calls")
	}
	if got := len(e.fake.requestsMatching("j/hyprmasq screenshot")); got != shots {
		t.Errorf("%d captures after the end", got-shots)
	}
	if got := e.fake.requestsMatching("j/hyprmasq lease"); len(got) != leases {
		t.Errorf("lease requests after the end: %q", got[leases:])
	}
	if len(e.fake.requestsMatching("j/hyprmasq cancel ")) == 0 {
		t.Error("the plugin's wait was not cancelled")
	}
}

// A launched window that does not settle in time is still drawing, or, when input from elsewhere kept it from counting
// as quiet, drew nothing.
func TestLaunchedWindowThatDoesNotSettle(t *testing.T) {
	for _, c := range []struct {
		redrawn, resizing bool
		summary, text     string
	}{
		{true, false, "busy", "still drawing after 5000 ms"},
		{false, false, "no_change", "not settled after 5000 ms, no redraw"},
		// It never drew at the size it was given.
		{false, true, "resizing", "still resizing after 5000 ms"},
	} {
		e := newTestEnv(t)
		e.fake.with(func() {
			e.fake.onLaunch = func(workspace, token, line string) {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000}, Width: 320, Height: 200, Resizing: c.resizing})
			}
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": 0, "redrawObserved": c.redrawn, "commitsSince": 0, "quietMs": 0, "elapsedMs": 5000, "resizing": c.resizing})
			}
		})
		res := e.launch(`{"command":["foot"]}`)
		if res.IsError || get(t, res, "wait.summary") != c.summary || (get(t, res, "wait.resizing") == true) != c.resizing || !strings.Contains(text(res), " · "+c.text+" · shot ") {
			t.Errorf("redrawn %v: %s, %v", c.redrawn, text(res), get(t, res, "wait"))
		}
		if hint, _ := get(t, res, "hint").(string); (get(t, res, "shot.resizing") == true) != c.resizing || strings.Contains(hint, "shows an older frame") != c.resizing {
			t.Errorf("resizing %v: %s", c.resizing, text(res))
		}
	}
}

func TestLaunchCancelledWhileSettling(t *testing.T) {
	e := newTestEnv(t)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000}, Width: 320, Height: 200})
		}
		e.fake.onWait = blockingWait(make(chan struct{}))
	})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq wait") != nil })
		cancel()
	}()
	res := e.server.launchTool(ctx, &call{args: json.RawMessage(`{"command":["foot"]}`)})
	if code := errorCode(t, res); code != "cancelled" || !strings.Contains(text(res), "0xe1") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq screenshot"); got != nil {
		t.Errorf("captured after the cancellation: %q", got)
	}
}

func TestLeaseWhoseAnswerIsLost(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	// The plugin takes the lease, but its answer is lost.
	e.fake.with(func() {
		e.fake.onPlugin = func(words []string) (string, bool) {
			if words[0] == "lease" && words[2] != "release" {
				e.fake.with(func() { w.Lease = &hypr.Lease{Held: true, RemainingMs: 60000, Owner: words[3]} })
				return "garbled", true
			}
			return "", false
		}
	})
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	e.fake.with(func() { e.fake.onPlugin = nil })
	e.server.mu.Lock()
	kept := e.server.leased["0xa1"]
	e.server.mu.Unlock()
	if !kept {
		t.Error("the session forgot a lease it may hold")
	}
	// A session end that has no time left after waiting for the calls still releases it.
	ended, cancel := context.WithCancel(context.Background())
	cancel()
	e.server.Shutdown(ended)
	e.fake.with(func() {
		if w.Lease != nil {
			t.Error("lease kept after the session ended")
		}
	})
}

func TestRemoveStaleShots(t *testing.T) {
	e := newTestEnv(t)
	parent := t.TempDir()
	e.server.shotDir = filepath.Join(parent, strconv.Itoa(os.Getpid()))
	exited := exec.Command("true")
	if err := exited.Run(); err != nil {
		t.Fatal(err)
	}
	dead := filepath.Join(parent, strconv.Itoa(exited.Process.Pid))
	alive := filepath.Join(parent, "1")
	for _, dir := range []string{e.server.shotDir, dead, alive, filepath.Join(parent, "other")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	e.server.RemoveStaleShots()
	for dir, kept := range map[string]bool{e.server.shotDir: true, dead: false, alive: true, filepath.Join(parent, "other"): true} {
		if _, err := os.Stat(dir); (err == nil) != kept {
			t.Errorf("%s: kept %v", dir, err == nil)
		}
	}
}

func TestLaunchRejectsOverlongCommands(t *testing.T) {
	e := newTestEnv(t)
	args, _ := json.Marshal(map[string]any{"command": []string{"sh", "-c", strings.Repeat("x", hypr.MaxRequest)}})
	res := e.server.launchTool(context.Background(), &call{args: args})
	if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), "too long") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq launch"); got != nil {
		t.Errorf("sent %d launch requests", len(got))
	}
}

func TestLeaseFollowsInput(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	leases := func() int { return len(e.fake.requestsMatching("j/hyprmasq lease 0xa1 6")) }
	held := func() bool {
		var held bool
		e.fake.with(func() { held = w.Lease != nil })
		return held
	}

	// A lease keeps only the focus that input gives, so a call without input starts none.
	res := e.act(`{"window":"0xa1","shot":"s1"}`)
	if res.IsError || leases() != 0 || held() || get(t, res, "lease.held") != false {
		t.Fatalf("leases %d: %s", leases(), text(res))
	}
	// Input takes it before it is sent and renews it after the capture.
	if res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`); res.IsError || leases() != 2 || !strings.Contains(text(res), "lease 60 s") {
		t.Fatalf("leases %d: %s", leases(), text(res))
	}
	// A call without input renews a lease that is held.
	if res = e.act(`{"window":"0xa1","shot":"s1"}`); res.IsError || leases() != 3 || !strings.Contains(text(res), "lease 60 s") {
		t.Fatalf("leases %d: %s", leases(), text(res))
	}

	// A lease taken for input that was never sent is released again.
	e.release(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "fail:cannot_type:0:none:0")
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"é"}]}`)
	if code := errorCode(t, res); code != "cannot_type" || leases() != 4 || held() {
		t.Fatalf("code %q, leases %d, held %v: %s", code, leases(), held(), text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xa1 release " + e.server.Owner()); len(got) != 1 {
		t.Errorf("released %q", got)
	}
}

// Input can close its window, as a click on a dialog's button or ctrl+q does. The sender stops when the connection
// ends and reports how far it got, and the window is gone by the time the server looks again.
func TestWindowClosedBySentInput(t *testing.T) {
	const clickPause = `[{"action":"click","x":5,"y":5},{"action":"pause","ms":500}]`
	for _, c := range []struct {
		name, mode, actions string
		// Whether the window closes once the sender has exited.
		closes bool
		// The error and its message, or "" for a result without an error.
		code, message string
		want          map[string]any
	}{
		// The sender stops at the pause, which needs no window.
		{"pause after the input", "fail:unconfirmed:2:partial:0", clickPause, true, "", "",
			map[string]any{"done": 2.0, "total": 2.0, "delivery": "complete"}},
		{"input after a pause", "fail:unconfirmed:2:partial:0", `[{"action":"click","x":5,"y":5},{"action":"pause","ms":500},{"action":"type","text":"more"}]`, true,
			"window_gone", "window 0xa1 closed after actions[0] (click) and actions[1] (pause) were delivered, while actions[2] (type) was sent",
			map[string]any{"done": 2.0, "total": 3.0, "delivery": "partial", "failed_index": 2.0}},
		{"input after the close", "fail:unconfirmed:1:partial:0", `[{"action":"key","keys":["ctrl+q"]},{"action":"type","text":"more"}]`, true,
			"window_gone", "window 0xa1 closed after actions[0] (key) was delivered, while actions[1] (type) was sent",
			map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0}},
		{"closed by the first action", "fail:unconfirmed:0:uncertain:0", `[{"action":"key","keys":["ctrl+q"]},{"action":"type","text":"more"}]`, true,
			"window_gone", "window 0xa1 closed while actions[0] (key) was sent",
			map[string]any{"done": 0.0, "total": 2.0, "delivery": "uncertain", "failed_index": 0.0}},
		// Another sender that takes over the socket ends the connection too, but the window stays, so the pause was cut short.
		{"taken over during a pause", "fail:unconfirmed:2:partial:0", clickPause, false, "unconfirmed", "actions[1] (pause): fake unconfirmed",
			map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0}},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			t.Setenv("FAKE_INPUT_MODE", c.mode)
			e.fake.with(func() {
				e.fake.onPlugin = func(words []string) (string, bool) {
					if c.closes && e.senderLogged("exit") {
						e.fake.removeWindow("0xa1")
					}
					return "", false
				}
			})
			res := e.act(`{"window":"0xa1","shot":"s1","actions":` + c.actions + `}`)
			if got := get(t, res, "actions"); !reflect.DeepEqual(got, c.want) {
				t.Errorf("actions %v", got)
			}
			if c.code == "" {
				if res.IsError || get(t, res, "error") != nil || !strings.HasPrefix(text(res), "2/2 delivered · window closed") {
					t.Errorf("result: %s", text(res))
				}
			} else if code := errorCode(t, res); code != c.code || get(t, res, "error.message") != c.message {
				t.Errorf("code %q: %s", code, text(res))
			}
			if !c.closes {
				return
			}
			if got := get(t, res, "wait.ended"); got != "window_closed" {
				t.Errorf("ended %v", got)
			}
			if hasImage(res) || get(t, res, "shot") != nil || get(t, res, "capture_error") != nil {
				t.Errorf("tried to capture: %s", text(res))
			}
			if c.code == "" {
				// The input opened no window, so the result is only that the window closed.
				newWindows, _ := get(t, res, "new_windows").([]any)
				if text(res) != "2/2 delivered · window closed\nThe window closed. Any new windows are listed; call windows for the rest." || len(newWindows) != 0 {
					t.Errorf("result %q", text(res))
				}
				return
			}
			hint := get(t, res, "hint").(string)
			if !strings.Contains(hint, "maybe because of") || strings.Contains(hint, "Retry") || strings.Contains(hint, "so do not send") != (c.want["done"] != 0.0) {
				t.Errorf("hint %q", hint)
			}
			if got := afterBatch(t, e.fake.requestsMatching("j/hyprmasq wait")); got != nil {
				t.Errorf("waited: %q", got)
			}
		})
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

// Input that closes its window after the wait has ended quiet, as a file chooser does a moment after Enter, before or
// after the capture, is delivered and closed the window: no failed capture, and no shot of a window that is gone.
func TestWindowClosedAfterTheWait(t *testing.T) {
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
			if want := "1/1 delivered · redraw observed · quiet 201 ms · 492 ms · window closed\nThe window closed. Any new windows are listed; call windows for the rest."; text(res) != want {
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
	if want := "1/1 delivered · no redraw within 1000 ms · window closed\nThe window closed. Any new windows are listed; call windows for the rest."; res.IsError || text(res) != want {
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
				if get(t, res, "source") != nil || hasImage(res) || strings.Contains(text(res), "0xa1") || text(res) != "1/1 delivered · window closed\nThe window closed. Any new windows are listed; call windows for the rest." {
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
					hint != "The window closed. It came from window 0xa1: take a screenshot of it to go on there. Any new windows are listed; call windows for the rest." {
					t.Errorf("without a capture: %s", text(res))
				}
				return
			}
			shot, _ := get(t, res, "source.shot.id").(string)
			if shot == "" || len(res.Content) != 2 || lines[1] != "shot "+shot+" 640×400 of 0xa1, the window it came from" ||
				hint != "The window closed. It came from window 0xa1, which shot "+shot+" shows now: go on there with that shot's pixels. Any new windows are listed; call windows for the rest." {
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
	if res.IsError || get(t, res, "source") != nil || hasImage(res) || text(res) != "1/1 delivered · window closed\nThe window closed. Any new windows are listed; call windows for the rest." {
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
	if hint, _ := get(t, res, "hint").(string); !strings.Contains(hint, "It came from window 0xa1, which shot") {
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
	zoomed := func(e *testEnv, from int) []string {
		var out []string
		for _, r := range e.fake.requestsMatching("j/hyprmasq screenshot")[from:] {
			if !strings.HasSuffix(r, ".png popups") {
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
		want := "Read the coordinates from the new shot in this result. The window was resized from 320×200 to 400×200 since shot s1, " +
			"which the capture region was read from, so shot " + id + " shows the whole window instead. Nothing was sent."
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
