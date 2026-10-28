package server

import (
	"bytes"
	"context"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"math/rand/v2"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"sort"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/delta"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// paint makes the window white in the rectangles and black elsewhere, in window-local logical pixels.
func (e *testEnv) paint(address string, rects ...hypr.Rect) {
	e.fake.with(func() {
		e.fake.find(address).Paint = func(x, y float64) uint8 {
			for _, r := range rects {
				if x >= r.X && x < r.X+r.Width && y >= r.Y && y < r.Y+r.Height {
					return 255
				}
			}
			return 0
		}
	})
}

// animate draws the rects over the window's content in a gray that changes with every screenshot, as an animation that
// keeps drawing.
func (e *testEnv) animate(address string, rects ...hypr.Rect) {
	e.fake.with(func() {
		w := e.fake.find(address)
		under := w.Paint
		w.Paint = func(x, y float64) uint8 {
			for _, r := range rects {
				if x >= r.X && x < r.X+r.Width && y >= r.Y && y < r.Y+r.Height {
					return uint8(100 + 50*(w.Frames%2))
				}
			}
			if under == nil {
				return 0
			}
			return under(x, y)
		}
	})
}

func images(res *mcp.CallToolResult) int {
	n := 0
	for _, c := range res.Content {
		if _, ok := c.(*mcp.ImageContent); ok {
			n++
		}
	}
	return n
}

func summaryOf(t *testing.T, res *mcp.CallToolResult) string {
	t.Helper()
	s, _ := get(t, res, "summary").(string)
	return s
}

const pressA = `[{"action":"key","keys":["a"]}]`

// A change comes as a zoom into its region with a margin, listed in the changes shot's pixels, which are the base's:
// act takes coordinates from the base and from the zoom.
func TestChangesShotOfOneRegion(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	// The block and the pixel around it that the 3×3 average reaches, with 24 logical pixels on every side.
	if want := "1/1 delivered · redraw observed · quiet 200 ms · 412 ms · shot s2 = s1 + 1 change · lease 60 s\nzoom s3 140×120 of s2 at 150,50 140×120"; summaryOf(t, res) != want {
		t.Errorf("summary %q, want %q", summaryOf(t, res), want)
	}
	if get(t, res, "hint") != nil || images(res) != 1 {
		t.Errorf("hint %v, %d images", get(t, res, "hint"), images(res))
	}
	want := map[string]any{"id": "s2", "width": 640.0, "height": 400.0, "frame": map[string]any{"x": 0.0, "y": 0.0, "width": 320.0, "height": 200.0},
		"changes": map[string]any{"base": "s1", "regions": []any{map[string]any{"shot": "s3", "image_width": 140.0, "image_height": 120.0, "x": 150.0, "y": 50.0, "width": 140.0, "height": 120.0}}}}
	if got := get(t, res, "shot"); !reflect.DeepEqual(got, want) {
		t.Errorf("shot %v, want %v", got, want)
	}
	// The detail image has the monitor's pixels, so the zoom is cut from the image that was compared, not captured.
	shots := e.fake.requestsMatching("j/hyprmasq screenshot")
	if last := shots[len(shots)-1]; !regexp.MustCompile(`/f2\.raw popups raw detail \S+/f2-detail\.raw$`).MatchString(last) {
		t.Errorf("last screenshot request %q", last)
	}
	if zoom := e.server.shots.get("s3"); zoom == nil || zoom.Frame != (hypr.Rect{X: 75, Y: 25, Width: 70, Height: 60}) || zoom.Scale != 2 || zoom.Parent != "s2" {
		t.Errorf("zoom %+v", zoom)
	}

	// The changes shot has the base's pixels, and the zoom its own.
	if res := e.act(`{"window":"0xa1","shot":"s2","actions":[{"action":"click","x":20,"y":30}],"capture":"none"}`); res.IsError {
		t.Fatal(text(res))
	}
	if res := e.act(`{"window":"0xa1","shot":"s3","actions":[{"action":"click","x":10,"y":10}]}`); res.IsError {
		t.Fatal(text(res))
	} else if !regexp.MustCompile(`· shot s\d+ = s2, no change ·`).MatchString(summaryOf(t, res)) {
		// A zoom's actions compare with the shot it zooms into.
		t.Errorf("summary %q", summaryOf(t, res))
	}
	want2 := [][]string{{"move", "5", "5", "click", "left", "1", "-"}, {"move", "10", "15", "click", "left", "1", "-"}, {"move", "80", "30", "click", "left", "1", "-"}}
	if got := e.inputs(); !reflect.DeepEqual(got, want2) {
		t.Errorf("inputs %q, want %q", got, want2)
	}
	// A zoom of the changes shot is read in its pixels too.
	res = e.screenshot(`{"window":"0xa1","region":{"shot":"s2","x":150,"y":50,"width":140,"height":120}}`)
	if res.IsError || get(t, res, "shot.frame") == nil || !reflect.DeepEqual(get(t, res, "shot.frame"), map[string]any{"x": 75.0, "y": 25.0, "width": 70.0, "height": 60.0}) {
		t.Errorf("zoom of the changes shot: %s %v", text(res), get(t, res, "shot.frame"))
	}
}

// laterClause is what the hint after a click, a drag, a key or Enter that changed nothing adds: the input may have opened
// a window that maps after the call, as a portal's file chooser does. TestWording has its text.
var laterClause = laterWindow([]action{{Kind: "click"}})

// deliveredWithout is the result of a batch that was delivered whole and after which the window settled, with a changes
// shot of the base that found no change.
func deliveredWithout(changes changesOut) *actOut {
	return &actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "quiet"}, Shot: &shotOut{Changes: &changes}}
}

// Without a change there is no image, and delivered input that changed nothing says so.
func TestChangesShotWithoutChange(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":10,"y":10}]}`)
	if res.IsError || images(res) != 0 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	if !strings.Contains(summaryOf(t, res), " · shot s2 = s1, no change · ") || !reflect.DeepEqual(get(t, res, "shot.changes"), map[string]any{"base": "s1", "regions": []any{}}) {
		t.Errorf("summary %q, changes %v", summaryOf(t, res), get(t, res, "shot.changes"))
	}
	// A click may also have opened a window of another process that maps after the call, which the hint says.
	if hint := get(t, res, "hint"); hint != noChangeHint(deliveredWithout(changesOut{Base: "s1"}), []action{{Kind: "click"}}) {
		t.Errorf("hint %v", hint)
	}
	// A window that drew nothing gets the wait's hint, which points at the shot it still shows.
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "redrawObserved": false, "commitsSince": 0, "quietMs": 0, "elapsedMs": 1000})
		}
	})
	res = e.act(`{"window":"0xa1","shot":"s2","actions":[{"action":"click","x":10,"y":10}]}`)
	if hint := get(t, res, "hint"); hint != "The click may have missed or had no visible effect"+laterClause+". Look at s2 before repeating it." {
		t.Errorf("hint %v", hint)
	}
	// Typing without a line break opens no window.
	e.fake.with(func() { e.fake.onWait = nil })
	res = e.act(`{"window":"0xa1","shot":"s3","actions":[{"action":"type","text":"a"}]}`)
	if hint := get(t, res, "hint"); hint != "Nothing changed since s3, so the input may have missed or had no visible effect. Look at s3 before repeating it." {
		t.Errorf("typing: hint %v", hint)
	}
	// Motion after a click keeps what the hint says about the click.
	if res := e.act(`{"window":"0xa1","shot":"s4","actions":[{"action":"click","x":10,"y":10},{"action":"move","x":20,"y":20}]}`); get(t, res, "hint") !=
		"Nothing changed since s4, so the input may have missed or had no visible effect"+laterClause+". Look at s4 before repeating it." {
		t.Errorf("click and move: hint %v", get(t, res, "hint"))
	}
	// Waiting needs no input, and an empty batch that found nothing gets no hint.
	if res := e.act(`{"window":"0xa1","shot":"s5"}`); get(t, res, "hint") != nil || !strings.HasSuffix(summaryOf(t, res), "shot s6 = s5, no change · lease 60 s") {
		t.Errorf("empty batch: %s", text(res))
	}
}

func TestChangesShotOfSeveralRegions(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 400)
	e.screenshot(`{"window":"0xa1"}`)
	// Two blocks 30 pixels apart share a zoom, whose margins would overlap; the others get one each, top to bottom.
	e.paint("0xa1", hypr.Rect{X: 500, Y: 300, Width: 10, Height: 10}, hypr.Rect{X: 100, Y: 50, Width: 10, Height: 10}, hypr.Rect{X: 140, Y: 50, Width: 10, Height: 10},
		hypr.Rect{X: 300, Y: 150, Width: 10, Height: 10})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 3 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	want := "shot s2 = s1 + 3 changes · lease 60 s\nzoom s3 200×120 of s2 at 150,50 200×120\nzoom s4 120×120 of s2 at 550,250 120×120\nzoom s5 120×120 of s2 at 950,550 120×120"
	if got := summaryOf(t, res); !strings.HasSuffix(got, want) {
		t.Errorf("summary %q, want the end %q", got, want)
	}
}

// Paging may put distant edits in one crop, but must show both at their native
// scale. When that crop exceeds the image budget, the separate zooms remain.
func TestPagingChangesKeepAllRegions(t *testing.T) {
	for _, limit := range []int{1280, 640} {
		t.Run(fmt.Sprint(limit), func(t *testing.T) {
			e := newTestEnv(t)
			e.server.cfg.MaxImageSize = limit
			e.window("0xa1", "1a", 640, 400)
			e.screenshot(`{"window":"0xa1"}`)
			e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 10, Height: 10}, hypr.Rect{X: 450, Y: 250, Width: 10, Height: 10})
			out := &actOut{}
			_, data, err := e.server.captureChanges(t.Context(), &actRequest{window: "0xa1", base: e.server.shots.get("s1"), joinChanges: true}, out, "1a")
			want := 1
			if limit == 640 {
				want = 2
			}
			if err != nil || len(data) != want || out.Shot == nil || out.Shot.Changes == nil || len(out.Shot.Changes.Regions) != want {
				t.Fatalf("%d crops, error %v, shot %+v", len(data), err, out.Shot)
			}
			points := []image.Point{{105, 55}, {455, 255}}
			seen := make([]bool, len(points))
			for i, region := range out.Shot.Changes.Regions {
				z := e.server.shots.get(region.Shot)
				img, err := png.Decode(bytes.NewReader(data[i]))
				if err != nil {
					t.Fatal(err)
				}
				for j, p := range points {
					x, y := int((float64(p.X)-z.Frame.X)*z.Scale), int((float64(p.Y)-z.Frame.Y)*z.Scale)
					if image.Pt(x, y).In(img.Bounds()) {
						if got := color.GrayModel.Convert(img.At(x, y)).(color.Gray).Y; got != 255 {
							t.Errorf("edit %v is gray %d", p, got)
						}
						seen[j] = true
					}
				}
			}
			if !seen[0] || !seen[1] {
				t.Fatalf("edits omitted: %v", seen)
			}
		})
	}
}

// Changes within an animation that the wait did not wait for, where the window keeps changing, are left out and
// counted, and the summary names the animation's box in the shot's pixels. The window is captured once more for them.
func TestChangesLeaveOutAnimations(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = []hypr.IntRect{{X: 90, Y: 40, Width: 40, Height: 30}} })
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 300, Y: 200, Width: 10, Height: 10})
	e.animate("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 1 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	want := "redraw observed · quiet 200 ms except animation at 180,80 80×60 · 412 ms · shot s2 = s1 + 1 change · lease 60 s\nzoom s3 "
	if got := summaryOf(t, res); !strings.Contains(got, want) || !strings.HasSuffix(got, "\n1 change in an animation left out") {
		t.Errorf("summary %q, want %q and the animation's line", got, want)
	}
	if got := get(t, res, "wait.animated"); !reflect.DeepEqual(got, []any{map[string]any{"x": 180.0, "y": 80.0, "width": 80.0, "height": 60.0}}) {
		t.Errorf("wait.animated %v", got)
	}
	if got := get(t, res, "shot.changes.animated"); got != 1.0 {
		t.Errorf("shot.changes.animated %v", got)
	}
	// The second capture came after a wait from the counter of the first, for the window's next frame, and is of the
	// animation's box alone.
	if waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1 since 3 expect 200 quiet 100 "); len(waits) != 1 {
		t.Errorf("waits %q", e.fake.requestsMatching("j/hyprmasq wait"))
	}
	if shots := e.fake.requestsMatching("j/hyprmasq screenshot"); len(shots) != 3 || !strings.HasSuffix(shots[2], " 90 40 40 30 popups raw") {
		t.Errorf("screenshots %q", shots)
	}
}

// The window is captured a second time only when that can change the result: not when no change lies within an
// animation and the changes fit into zooms anyway, as a row of a list that reaches into its animated scrollbar, and not
// when they come as a full shot even with every change within the animations left out.
func TestChangesWithoutASecondImageThatChangesNothing(t *testing.T) {
	for _, c := range []struct {
		name         string
		paint, moves []hypr.Rect
		summary      string
	}{
		{name: "a change that reaches into an animation", paint: []hypr.Rect{{X: 20, Y: 100, Width: 300, Height: 20}},
			summary: "shot s2 = s1 + 1 change · lease 60 s\nzoom s3 690×140 of s2 at 0,150 690×140"},
		{name: "a full shot", paint: []hypr.Rect{{X: 0, Y: 200, Width: 640, Height: 200}}, moves: []hypr.Rect{{X: 300, Y: 20, Width: 20, Height: 100}},
			summary: "% changed since s1) · lease 60 s"},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = []hypr.IntRect{{X: 300, Y: 0, Width: 20, Height: 400}} })
			e.screenshot(`{"window":"0xa1"}`)
			e.paint("0xa1", c.paint...)
			e.animate("0xa1", c.moves...)
			res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
			if got := summaryOf(t, res); res.IsError || !strings.HasSuffix(got, c.summary) {
				t.Errorf("summary %q, want the end %q", got, c.summary)
			}
			if waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1 since 3 expect 200 "); len(waits) != 0 {
				t.Errorf("waits %q", e.fake.requestsMatching("j/hyprmasq wait"))
			}
		})
	}
}

// An application can damage much more than it changes, so the box of an animation can hold content that the window
// drew there: in the benchmark VM, Thunderbird damaged a tile of 1024×512 logical pixels around a blinking caret, and
// its completion page with the Finish button came in that tile. A change within the box that holds still in the capture
// after it is such content, and stays; the part that changes again stays out.
func TestChangesKeepContentWithinAnimations(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = []hypr.IntRect{{X: 0, Y: 0, Width: 320, Height: 160}} })
	e.screenshot(`{"window":"0xa1"}`)
	// A button that the window drew in the tile, and a spinner in the same tile that goes on turning.
	e.paint("0xa1", hypr.Rect{X: 200, Y: 100, Width: 60, Height: 20})
	e.animate("0xa1", hypr.Rect{X: 20, Y: 20, Width: 16, Height: 16})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 1 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	want := "quiet 200 ms except animation at 0,0 640×320 · 412 ms · shot s2 = s1 + 1 change · lease 60 s\nzoom s3 220×140 of s2 at 350,150 220×140\n1 change in an animation left out"
	if got := summaryOf(t, res); !strings.HasSuffix(got, want) {
		t.Errorf("summary %q, want the end %q", got, want)
	}

	// A label 4 pixels from the spinner, which the comparison joins with it, stays without the spinner and the pixels
	// within the distance that joins changes.
	e = newTestEnv(t)
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = []hypr.IntRect{{X: 0, Y: 0, Width: 320, Height: 160}} })
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 40, Y: 22, Width: 60, Height: 12})
	e.animate("0xa1", hypr.Rect{X: 20, Y: 20, Width: 16, Height: 16})
	res = e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	want = "shot s2 = s1 + 1 change · lease 60 s\nzoom s3 210×118 of s2 at 40,0 210×118\n1 change in an animation left out"
	if got := summaryOf(t, res); res.IsError || images(res) != 1 || !strings.HasSuffix(got, want) {
		t.Errorf("label: summary %q, want the end %q", got, want)
	}

	// Content that holds still in a box where nothing else changes stays too, without a zoom of the box.
	e = newTestEnv(t)
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = []hypr.IntRect{{X: 0, Y: 0, Width: 320, Height: 160}} })
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 200, Y: 100, Width: 60, Height: 20})
	res = e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if got := summaryOf(t, res); res.IsError || images(res) != 1 || !strings.HasSuffix(got, "shot s2 = s1 + 1 change · lease 60 s\nzoom s3 220×140 of s2 at 350,150 220×140") {
		t.Errorf("%d images: %s", images(res), text(res))
	}
}

// Without a second image to tell an animation from content, every change within the animation stays: when the window
// drew nothing after the first image, when it was still drawing at the wait's deadline, when the wait fails, when the
// second capture fails, and when it is of another socket.
func TestChangesWithinAnimationsWithoutASecondImage(t *testing.T) {
	animation := []hypr.IntRect{{X: 90, Y: 40, Width: 40, Height: 30}}
	for _, c := range []struct {
		name string
		// The reply to the wait after the first image, or "" for one that sees the window draw.
		reply      func(w *fakeWindow) string
		screenshot func(w *fakeWindow, n int) string
	}{
		{name: "no redraw", reply: func(w *fakeWindow) string {
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false, "commitsSince": 0,
				"quietMs": 0, "elapsedMs": 200, "resizing": false, "animated": []hypr.IntRect{}})
		}},
		{name: "still drawing", reply: func(w *fakeWindow) string {
			return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": w.Commits + 30, "socketId": w.socketID(), "redrawObserved": true, "commitsSince": 30,
				"quietMs": 10, "elapsedMs": 400, "resizing": false, "animated": []hypr.IntRect{}})
		}},
		{name: "a failed wait", reply: func(w *fakeWindow) string { return pluginError("internal", "the wait failed") }},
		{name: "a failed capture", screenshot: func(w *fakeWindow, n int) string {
			if n == 2 {
				return "the capture failed"
			}
			return ""
		}},
		{name: "another socket", screenshot: func(w *fakeWindow, n int) string {
			if n == 2 {
				w.SocketID = "another"
			}
			return ""
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = animation })
			e.screenshot(`{"window":"0xa1"}`)
			e.animate("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
			shots := 0
			e.fake.with(func() {
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					if args["quiet"] == "100" && c.reply != nil {
						return c.reply(w)
					}
					var reply string
					e.fake.with(func() {
						reply = jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits + 3, "socketId": w.socketID(), "redrawObserved": true, "commitsSince": 3,
							"quietMs": 200, "elapsedMs": 412, "resizing": false, "animated": animation})
						w.Commits += 3
					})
					return reply
				}
				e.fake.onScreenshot = func(w *fakeWindow) string {
					shots++
					if c.screenshot != nil {
						return c.screenshot(w, shots)
					}
					return ""
				}
			})
			res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
			if got := summaryOf(t, res); res.IsError || images(res) != 1 || !strings.HasSuffix(got, "shot s2 = s1 + 1 change · lease 60 s\nzoom s3 140×120 of s2 at 150,50 140×120") {
				t.Errorf("%d images: %s", images(res), text(res))
			}
			if waits := e.fake.requestsMatching("j/hyprmasq wait 0xa1 since 3 expect 200 quiet 100 "); len(waits) != 1 {
				t.Errorf("waits %q", e.fake.requestsMatching("j/hyprmasq wait"))
			}
		})
	}
}

// Animations together may cover more than 35% of the window without a full shot, and when only they changed, the
// result says so instead of no change, and its hint says that the input's effect may lie inside them. A tiny change
// inside an animation counts as tiny: a tiny difference between the two captures, such as a caret that blinked, does
// not tell an animation apart from content.
func TestChangesOfAnimationsOnly(t *testing.T) {
	e := newTestEnv(t)
	animated := []hypr.IntRect{{X: 0, Y: 0, Width: 300, Height: 200}, {X: 320, Y: 0, Width: 300, Height: 200}, {X: 0, Y: 220, Width: 300, Height: 150}}
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = animated })
	e.screenshot(`{"window":"0xa1"}`)
	e.animate("0xa1", hypr.Rect{X: 10, Y: 10, Width: 280, Height: 180}, hypr.Rect{X: 330, Y: 10, Width: 280, Height: 180}, hypr.Rect{X: 10, Y: 230, Width: 280, Height: 130},
		hypr.Rect{X: 400, Y: 300, Width: 2, Height: 30})
	e.fake.with(func() {
		e.fake.find("0xa1").Animated = append(animated, hypr.IntRect{X: 390, Y: 290, Width: 30, Height: 50})
	})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 0 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	if got := summaryOf(t, res); !strings.HasSuffix(got, "shot s2 = s1, no change outside animations · lease 60 s\n1 tiny change left out\n3 changes in animations left out") {
		t.Errorf("summary %q", got)
	}
	if hint, want := get(t, res, "hint").(string), noChangeHint(deliveredWithout(changesOut{Base: "s1", Animated: 3}), []action{{Kind: "key"}}); !startsWithHint(hint, want) {
		t.Errorf("hint %q", hint)
	}
	if got := get(t, res, "shot.changes.left_out"); got != 1.0 {
		t.Errorf("left_out %v", got)
	}
}

// A region that holds an animation and a change next to it counts only the change towards the 35% that gives a full
// shot.
func TestChangesNextToAnAnimation(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.Animated = []hypr.IntRect{{X: 0, Y: 0, Width: 200, Height: 190}} })
	e.screenshot(`{"window":"0xa1"}`)
	// The animation's change touches another change, so the two make one region of 38% of the window, 26% of it
	// outside the part of the animation that keeps changing.
	e.paint("0xa1", hypr.Rect{X: 190, Y: 10, Width: 200, Height: 200})
	e.animate("0xa1", hypr.Rect{X: 10, Y: 10, Width: 180, Height: 170})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || get(t, res, "shot.full_shot") != nil || images(res) != 1 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
}

// More than 5 regions are joined, the two that add the least area first, until at most 5 are left.
func TestChangesJoinNearbyRegions(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 400)
	e.screenshot(`{"window":"0xa1"}`)
	var blocks []hypr.Rect
	for i := range 6 {
		blocks = append(blocks, hypr.Rect{X: float64(20 + 100*i), Y: float64(20 + 50*(i%2)), Width: 10, Height: 10})
	}
	e.paint("0xa1", blocks...)
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 4 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	// The first and third, in a row and cut by the window's edge, add the least. Their box reaches into the second's
	// region, which joins them.
	if got, want := summaryOf(t, res), "shot s2 = s1 + 4 changes · lease 60 s\nzoom s3 510×210 of s2 at 0,0 510×210\n"; !strings.Contains(got, want) {
		t.Errorf("summary %q, want %q", got, want)
	}
}

// A full shot comes instead of the changes when there is nothing to compare with, when the window's geometry changed,
// when the changes are large or many, after a run of changes shots, and when the zooms would take more pixels than it.
func TestChangesFallBackToAFullShot(t *testing.T) {
	// Six large blocks in the corners and the middle of the edges cover 33% of the window with their margins, and any
	// two joined would cover more than 35%.
	spread := []hypr.Rect{}
	for _, x := range []float64{0, 275, 550} {
		for _, y := range []float64{0, 310} {
			spread = append(spread, hypr.Rect{X: x, Y: y, Width: 90, Height: 90})
		}
	}
	cases := []struct {
		name   string
		setup  func(e *testEnv)
		reason string
	}{
		{"no earlier shot", func(e *testEnv) {}, "no earlier shot to compare with"},
		{"large", func(e *testEnv) {
			e.screenshot(`{"window":"0xa1"}`)
			e.paint("0xa1", hypr.Rect{Width: 640, Height: 200})
		}, "50% changed since s1"},
		{"many", func(e *testEnv) {
			e.screenshot(`{"window":"0xa1"}`)
			e.paint("0xa1", spread...)
		}, "6 changed regions since s1"},
		{"resized", func(e *testEnv) {
			e.screenshot(`{"window":"0xa1"}`)
			e.fake.with(func() { e.fake.find("0xa1").Width = 600 })
		}, "resized since s1"},
		{"popups", func(e *testEnv) {
			e.screenshot(`{"window":"0xa1"}`)
			e.fake.with(func() { e.fake.find("0xa1").PopupBox = &hypr.Rect{X: -20, Y: -10, Width: 660, Height: 410} })
		}, "popups outside the window changed since s1"},
		{"monitor scale", func(e *testEnv) {
			e.screenshot(`{"window":"0xa1"}`)
			e.fake.with(func() { e.fake.scale = 1 })
		}, "the monitor's scale changed since s1"},
		{"run of changes shots", func(e *testEnv) {
			e.screenshot(`{"window":"0xa1"}`)
			for i := range maxDeltas {
				if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); get(t, res, "shot.changes") == nil {
					t.Fatalf("act %d: %s", i, text(res))
				}
			}
		}, "10 changes shots in a row"},
		{"larger zooms", func(e *testEnv) {
			// The overview is shrunk to 100 pixels, so the zooms at the monitor's scale take more.
			e.server.cfg.MaxImageSize = 100
			e.screenshot(`{"window":"0xa1"}`)
			e.paint("0xa1", hypr.Rect{X: 100, Y: 100, Width: 60, Height: 60})
		}, "the zooms into the changes since s1 would be larger"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 640, 400)
			c.setup(e)
			before := len(e.fake.requestsMatching("j/hyprmasq screenshot"))
			res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
			if res.IsError || images(res) != 1 || get(t, res, "shot.changes") != nil {
				t.Fatalf("%d images: %s", images(res), text(res))
			}
			if got := get(t, res, "shot.full_shot"); got != c.reason {
				t.Errorf("reason %v, want %q", got, c.reason)
			}
			// The window is rendered once: a full shot after the comparison shows the frame that was compared.
			if got := len(e.fake.requestsMatching("j/hyprmasq screenshot")) - before; got != 1 {
				t.Errorf("%d screenshots for the act", got)
			}
			id := get(t, res, "shot.id").(string)
			if !regexp.MustCompile(` · shot ` + id + ` \d+×\d+ \(full shot: ` + regexp.QuoteMeta(c.reason) + `\) · `).MatchString(summaryOf(t, res)) {
				t.Errorf("summary %q", summaryOf(t, res))
			}
			// The full shot is the next base.
			if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); get(t, res, "shot.changes.base") != id {
				t.Errorf("next: %s", text(res))
			}
		})
	}
}

// A shot shrunk to fit --max-image-size gets a detail image at logical pixels, which the comparison uses.
func TestChangesOfAShrunkShot(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize, e.server.cfg.MinZoomSize = 320, 768
	e.window("0xa1", "1a", 640, 400)
	e.screenshot(`{"window":"0xa1"}`)
	// One render gives the overview and the detail image.
	shots := e.fake.requestsMatching("j/hyprmasq screenshot")
	if len(shots) != 1 || !regexp.MustCompile(`/f1\.raw max 320 popups raw detail \S+/f1-detail\.raw$`).MatchString(shots[0]) {
		t.Fatalf("requests %q", shots)
	}
	if d := e.server.shots.get("s1").Detail; d == nil || d.Scale != 1 || d.Width != 640 || d.Max != 640 {
		t.Fatalf("detail %+v", d)
	}
	// A block of 4 logical pixels, 2 of the overview, is found in the detail images.
	e.paint("0xa1", hypr.Rect{X: 300, Y: 200, Width: 4, Height: 4})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 1 {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	shots = e.fake.requestsMatching("j/hyprmasq screenshot")
	if detail := shots[1]; !regexp.MustCompile(`/f2\.raw max 320 popups raw detail \S+/f2-detail\.raw$`).MatchString(detail) {
		t.Errorf("detail request %q", detail)
	}
	// The zoom is at the monitor's scale, not enlarged as a zoom the agent asks for, and listed in the overview's pixels.
	if want := "zoom s3 108×108 of s2 at 137,87 28×28"; !strings.HasSuffix(summaryOf(t, res), want) {
		t.Errorf("summary %q, want the end %q", summaryOf(t, res), want)
	}
}

func TestTinyChangesAreLeftOut(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	// A caret, one logical pixel wide.
	e.paint("0xa1", hypr.Rect{X: 50, Y: 50, Width: 1, Height: 14})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if res.IsError || images(res) != 0 || !strings.HasSuffix(summaryOf(t, res), "shot s2 = s1, no change · lease 60 s\n1 tiny change left out") {
		t.Fatalf("%d images: %s", images(res), text(res))
	}
	if get(t, res, "shot.changes.left_out") != 1.0 {
		t.Errorf("changes %v", get(t, res, "shot.changes"))
	}
	if hint := get(t, res, "hint"); hint != "Only tiny changes since s1, such as a caret that moved or blinked, so the input may have missed or had little visible effect"+laterClause+". Look at s1 before repeating it." {
		t.Errorf("hint %v", hint)
	}
	// A tiny change that a zoom shows is not left out: the block is farther from the caret than the changes that group,
	// and closer than the zoom's margin.
	e.paint("0xa1", hypr.Rect{X: 50, Y: 50, Width: 1, Height: 14}, hypr.Rect{X: 70, Y: 50, Width: 20, Height: 14})
	res = e.act(`{"window":"0xa1","shot":"s1","actions":` + pressA + `}`)
	if images(res) != 1 || get(t, res, "shot.changes.left_out") != nil || strings.Contains(summaryOf(t, res), "left out") {
		t.Errorf("%d images: %s", images(res), text(res))
	}
}

// Without a shot, act compares with the last shot of the window that a result gave this session: a screenshot, a
// launch, an act's full or changes shot, or the shot of a failure, but not a zoom.
func TestChangesBase(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	base := func(res *mcp.CallToolResult) any { return get(t, res, "shot.changes.base") }
	e.screenshot(`{"window":"0xa1"}`)
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); base(res) != "s1" {
		t.Errorf("after a screenshot: %s", text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); base(res) != "s2" {
		t.Errorf("after a changes shot: %s", text(res))
	}
	e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":0,"y":0,"width":10,"height":10}}`)
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); base(res) != "s3" {
		t.Errorf("after a zoom: %s", text(res))
	}
	// A zoom given as shot compares with the shot it zooms into.
	if res := e.act(`{"window":"0xa1","shot":"s4","actions":` + pressA + `}`); base(res) != "s1" {
		t.Errorf("with a zoom: %s", text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `,"capture":"window"}`); get(t, res, "shot.id") != "s7" || get(t, res, "shot.full_shot") != nil {
		t.Errorf("window: %s", text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); base(res) != "s7" {
		t.Errorf("after a full shot: %s", text(res))
	}
	// A failure's shot is a full shot, whose coordinates the agent reads anew.
	res := e.act(`{"window":"0xa1","shot":"s8","actions":[{"action":"click","x":5000,"y":5}]}`)
	if errorCode(t, res) != "out_of_bounds" || get(t, res, "shot.changes") != nil || images(res) != 1 {
		t.Fatalf("failure: %s", text(res))
	}
	failed := get(t, res, "shot.id")
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); base(res) != failed {
		t.Errorf("after a failure: %s", text(res))
	}
	// Another session has seen none of these shots.
	other := mirrored(t, callTool(e.newServer(), t.Context(), actOn, `{"window":"0xa1","actions":`+pressA+`}`))
	if get(t, other, "shot.full_shot") != "no earlier shot to compare with" {
		t.Errorf("other session: %s", text(other))
	}
	// A new window at the address has none either.
	e.fake.removeWindow("0xa1")
	e.window("0xa1", "2b", 320, 200)
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); get(t, res, "shot.full_shot") != "no earlier shot to compare with" {
		t.Errorf("new window: %s", text(res))
	}
}

func TestOtherCaptureModesAreUnchanged(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":` + pressA + `,"capture":"window"}`)
	if res.IsError || images(res) != 1 || summaryOf(t, res) != "1/1 delivered · redraw observed · quiet 200 ms · 412 ms · shot s2 640×400 · lease 60 s" {
		t.Errorf("window: %s", text(res))
	}
	if shot := get(t, res, "shot").(map[string]any); shot["changes"] != nil || shot["full_shot"] != nil {
		t.Errorf("window shot %v", shot)
	}
	res = e.act(`{"window":"0xa1","shot":"s1","actions":` + pressA + `,"capture":"none"}`)
	if res.IsError || images(res) != 0 || get(t, res, "shot") != nil || summaryOf(t, res) != "1/1 delivered · redraw observed · quiet 200 ms · 412 ms · lease 60 s" {
		t.Errorf("none: %s", text(res))
	}
	if code := errorCode(t, e.act(`{"window":"0xa1","capture":"delta"}`)); code != "invalid_arguments" {
		t.Errorf("unknown mode: %q", code)
	}
}

// After applied input, a failure's changes shot is what to look at before repeating anything.
func TestChangesAfterAFailure(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "fail:input_paused:1:partial:3")
	batch := `[{"action":"key","keys":["a"]},{"action":"type","text":"hello"}]`
	res := e.act(`{"window":"0xa1","actions":` + batch + `}`)
	if errorCode(t, res) != "input_paused" || !strings.HasSuffix(get(t, res, "hint").(string), "Look at s1 before repeating anything.") {
		t.Errorf("no change: %s", text(res))
	}
	e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	res = e.act(`{"window":"0xa1","actions":` + batch + `}`)
	if errorCode(t, res) != "input_paused" || images(res) != 1 || !strings.HasSuffix(get(t, res, "hint").(string), "Look at the zooms before repeating anything.") {
		t.Errorf("change: %s", text(res))
	}
	if !strings.Contains(text(res), "shot s3 = s2 + 1 change · lease 60 s\nzoom s4 ") {
		t.Errorf("text %q", text(res))
	}
}

// The session forgets the shots of a window that closed, with their files and its last shot.
func TestChangesCleanup(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 320
	e.window("0xa1", "1a", 640, 400)
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); images(res) != 1 {
		t.Fatal(text(res))
	}
	files := func() []string {
		e.server.shots.wait()
		entries, _ := os.ReadDir(e.server.shotDir)
		var names []string
		for _, entry := range entries {
			names = append(names, entry.Name())
		}
		return names
	}
	// The detail image of s1 left memory for that of the changes shot s2, which the zoom s3 is cut from, and went to a
	// file. The raw images of the renders are gone.
	if got := files(); !reflect.DeepEqual(got, []string{"f1-detail.z", "s1.png", "s3.png"}) {
		t.Errorf("files %q", got)
	}
	if e.server.shots.lastOf("0xa1", "1a") == nil {
		t.Fatal("no last shot")
	}
	e.server.shots.dropWindow("0xa1")
	if got := files(); len(got) != 0 {
		t.Errorf("files kept: %q", got)
	}
	if e.server.shots.lastOf("0xa1", "1a") != nil {
		t.Error("the last shot is kept")
	}
}

// A shot that the store drops for room takes its detail image along, and stops being the window's last one.
func TestShotStoreDropsDetails(t *testing.T) {
	dir := t.TempDir()
	st := newShotStore()
	for i := 1; i <= shotsPerWindow+1; i++ {
		path, detailPath := filepath.Join(dir, fmt.Sprintf("s%d.png", i)), filepath.Join(dir, fmt.Sprintf("d%d.png", i))
		os.WriteFile(path, nil, 0o600)
		os.WriteFile(detailPath, nil, 0o600)
		s := &shot{ID: st.newID(), Window: "0xa1", StableID: "1a", Path: path, Detail: &detail{Path: detailPath}}
		st.add(s)
		if i == 1 {
			st.returned(s)
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "d1.png")); !os.IsNotExist(err) {
		t.Errorf("detail of the dropped shot: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "d2.png")); err != nil {
		t.Errorf("detail of a kept shot: %v", err)
	}
	if st.lastOf("0xa1", "1a") != nil {
		t.Error("the dropped shot is still the last")
	}
}

func TestPlanRegions(t *testing.T) {
	// At scale 1.25, a 101 pixels wide window is 126 device pixels, whose frame ends at 100.8. A change in the last
	// column gets a region to 101, whose zoom reads that column.
	g := detailGeometry{frame: hypr.Rect{Width: 100.8, Height: 40}, scale: 1.25}
	regions, _ := planRegions(delta.Result{Changes: []image.Rectangle{image.Rect(110, 10, 126, 20)}}, g)
	if want := []hypr.IntRect{{X: 64, Y: 0, Width: 37, Height: 40}}; !reflect.DeepEqual(regions, want) {
		t.Errorf("regions %+v, want %+v", regions, want)
	}
	// A tiny change counts as shown only when a zoom shows all of it.
	g = detailGeometry{frame: hypr.Rect{Width: 400, Height: 400}, scale: 1}
	res := delta.Result{Changes: []image.Rectangle{image.Rect(100, 100, 120, 110)}, Tiny: []image.Rectangle{
		image.Rect(110, 120, 112, 130), // inside the margin
		image.Rect(90, 125, 92, 300),   // a long line that starts inside it
	}}
	regions, leftOut := planRegions(res, g)
	if want := []hypr.IntRect{{X: 76, Y: 76, Width: 68, Height: 58}}; !reflect.DeepEqual(regions, want) || leftOut != 1 {
		t.Errorf("regions %+v, %d left out", regions, leftOut)
	}
}

func TestPlanRegionsJoinsNearbyRegions(t *testing.T) {
	g := detailGeometry{frame: hypr.Rect{Width: 1000, Height: 1000}, scale: 1}
	box := func(x, y int) image.Rectangle { return image.Rect(x, y, x+10, y+10) }
	// A form that moved, eight changes 60 pixels apart at the bottom, and a tab's icon at the top are joined into five
	// regions that show all of them, the top one alone.
	res := delta.Result{Changes: []image.Rectangle{box(900, 20)}}
	for i := range 8 {
		res.Changes = append(res.Changes, box(100+60*i, 900))
	}
	regions, _ := planRegions(res, g)
	if len(regions) != maxRegions || regions[0] != (hypr.IntRect{X: 876, Y: 0, Width: 58, Height: 54}) {
		t.Fatalf("regions %+v", regions)
	}
	for _, c := range res.Changes {
		shown := false
		for _, r := range regions {
			shown = shown || c.In(rectOf(r))
		}
		if !shown {
			t.Errorf("%v is in no region of %+v", c, regions)
		}
	}
	// Regions that joined would cover more than 35% of the window stay apart, for a full shot.
	res = delta.Result{}
	for _, x := range []int{0, 395, 790} {
		for _, y := range []int{0, 790} {
			res.Changes = append(res.Changes, image.Rect(x, y, x+210, y+210))
		}
	}
	if regions, _ := planRegions(res, g); len(regions) != 6 {
		t.Errorf("scattered: regions %+v", regions)
	}
	// So do more than maxJoined regions.
	res = delta.Result{}
	for i := range maxJoined + 1 {
		res.Changes = append(res.Changes, box(60*(i%16), 60*(i/16)))
	}
	if regions, _ := planRegions(delta.Result{Changes: res.Changes}, detailGeometry{frame: hypr.Rect{Width: 4000, Height: 4000}, scale: 1}); len(regions) != maxJoined+1 {
		t.Errorf("many: %d regions", len(regions))
	}
}

// joinNearest joins the two regions that add the least area, and then those that the joined one touches.
func TestJoinNearest(t *testing.T) {
	rects := []image.Rectangle{image.Rect(0, 0, 10, 10), image.Rect(100, 0, 110, 10), image.Rect(0, 30, 10, 40), image.Rect(8, 18, 18, 22)}
	// Joining the two on the left edge adds 200 pixels, less than either with the small one between them, which the
	// box they make overlaps.
	got := joinNearest(rects, 3)
	want := []image.Rectangle{image.Rect(0, 0, 18, 40), image.Rect(100, 0, 110, 10)}
	sort.Slice(got, func(i, j int) bool { return got[i].Min.X < got[j].Min.X })
	if !reflect.DeepEqual(got, want) {
		t.Errorf("%v, want %v", got, want)
	}
}

// The zooms are counted as the plugin captures them: from the device pixel of a region's corner to that of its end.
func TestZoomPixels(t *testing.T) {
	regions := []hypr.IntRect{{X: 1, Y: 1, Width: 56, Height: 56}, {X: 101, Y: 101, Width: 56, Height: 56}}
	if got := zoomPixels(regions, 1.25, 0); got != 2*71*71 {
		t.Errorf("pixels %d", got)
	}
	// Fitted within 50 pixels.
	if got := zoomPixels(regions[:1], 1.25, 50); got != 50*50 {
		t.Errorf("fitted pixels %d", got)
	}
}

// block paints a white rectangle on black, in window-local logical pixels.
func block(r hypr.Rect) func(x, y float64) uint8 {
	return func(x, y float64) uint8 {
		if x >= r.X && x < r.X+r.Width && y >= r.Y && y < r.Y+r.Height {
			return 255
		}
		return 0
	}
}

// grayAt returns the gray level of a result's image at a pixel.
func grayAt(t *testing.T, res *mcp.CallToolResult, index, x, y int) uint8 {
	t.Helper()
	for _, c := range res.Content {
		if img, ok := c.(*mcp.ImageContent); ok {
			if index--; index < 0 {
				decoded, err := png.Decode(bytes.NewReader(img.Data))
				if err != nil {
					t.Fatal(err)
				}
				return color.GrayModel.Convert(decoded.At(x, y)).(color.Gray).Y
			}
		}
	}
	t.Fatalf("no image %d in %s", index, text(res))
	return 0
}

// An overview and its detail image come from one render, so they show one frame, also of a window that keeps drawing.
func TestOverviewAndDetailAreOneFrame(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 320
	w := e.window("0xa1", "1a", 640, 400)
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			// A block appears, in a frame of its own, with every render.
			w.Commits++
			w.Paint = block(hypr.Rect{X: 100, Y: 100, Width: 40, Height: 20})
			return ""
		}
	})
	res := e.screenshot(`{"window":"0xa1"}`)
	if got := len(e.fake.requestsMatching("j/hyprmasq screenshot")); got != 1 || shotID(t, res) != "s1" {
		t.Fatalf("%d screenshots: %s", got, text(res))
	}
	sh := e.server.shots.get("s1")
	if sh.Detail == nil || grayAt(t, res, 0, 60, 55) != 255 {
		t.Fatalf("detail %+v, the overview shows the block: %v", sh.Detail, grayAt(t, res, 0, 60, 55))
	}
	img, err := sh.Detail.image()
	if err != nil || color.GrayModel.Convert(img.At(110, 105)).(color.Gray).Y != 255 || sh.Detail.Scale != 1 {
		t.Fatalf("the detail image does not show the block: %v", err)
	}
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); get(t, res, "shot.changes") == nil || images(res) != 0 {
		t.Errorf("after the block: %s", text(res))
	}
}

// The full shot that changes come as shows the frame that was compared, not one the window drew after it.
func TestFullShotOfTheComparedFrame(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 320
	w := e.window("0xa1", "1a", 640, 400)
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{Width: 640, Height: 300})
	states := 0
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			// After the render, the window draws again.
			e.fake.onState = func(*fakeWindow) {
				if states++; states == 1 {
					w.Commits++
					w.Paint = nil
				}
			}
			return ""
		}
	})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if get(t, res, "shot.full_shot") != "75% changed since s1" || images(res) != 1 {
		t.Fatalf("result %s", text(res))
	}
	if grayAt(t, res, 0, 10, 10) != 255 || grayAt(t, res, 0, 10, 190) != 0 {
		t.Error("the full shot does not show the compared frame")
	}
}

// Zooms cut from the compared image show what was compared: the act renders the window once, for its detail image,
// and the next result compares with what the zoom showed.
func TestZoomsShowTheComparedFrame(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10})
	before := len(e.fake.requestsMatching("j/hyprmasq screenshot"))
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if images(res) != 1 || grayAt(t, res, 0, 70, 60) != 255 {
		t.Fatalf("the zoom does not show the block: %s", text(res))
	}
	if got := len(e.fake.requestsMatching("j/hyprmasq screenshot")) - before; got != 1 {
		t.Errorf("%d screenshots for the act", got)
	}
	e.paint("0xa1")
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); get(t, res, "shot.changes.base") != "s2" || images(res) != 1 || grayAt(t, res, 0, 70, 60) != 0 {
		t.Errorf("the block that went: %s", text(res))
	}
}

// A zoom that is captured, since the detail image has fewer pixels than the monitor, has to show the compared frame:
// when the window drew meanwhile, act gives the changes up for a full shot, with every zoom it had made.
func TestCapturedZoomsOfAnotherFrame(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 320
	w := e.window("0xa1", "1a", 640, 400)
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}, hypr.Rect{X: 400, Y: 300, Width: 20, Height: 10})
	shots := 0
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			// The second zoom is of the act's fifth screenshot request since the first one, s1's overview.
			if shots++; shots == 3 {
				w.Commits++
			}
			return ""
		}
	})
	res := e.act(`{"window":"0xa1","actions":` + pressA + `}`)
	if get(t, res, "shot.full_shot") != "the window changed while the changes since s1 were captured" || images(res) != 1 {
		t.Fatalf("result %s", text(res))
	}
	for _, id := range []string{"s2", "s3", "s4"} {
		if e.server.shots.get(id) != nil {
			t.Errorf("%s is kept", id)
		}
		if _, err := os.Stat(filepath.Join(e.server.shotDir, id+".png")); !os.IsNotExist(err) {
			t.Errorf("%s.png: %v", id, err)
		}
	}
}

// A popup that closed past the window's edge before its zoom makes the plugin cut the zoom's region, which then no
// longer shows the change, also without a new frame.
func TestCapturedZoomCutByAClosedPopup(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 320
	w := e.window("0xa1", "1a", 640, 400, func(w *fakeWindow) { w.PopupBox = &hypr.Rect{Width: 700, Height: 400} })
	e.screenshot(`{"window":"0xa1"}`)
	e.paint("0xa1", hypr.Rect{X: 650, Y: 100, Width: 20, Height: 10})
	shots := 0
	e.fake.with(func() {
		e.fake.onScreenshot = func(*fakeWindow) string {
			if shots++; shots == 2 {
				w.PopupBox = nil
			}
			return ""
		}
	})
	if res := e.act(`{"window":"0xa1","actions":` + pressA + `}`); get(t, res, "shot.full_shot") != "the window changed while the changes since s1 were captured" {
		t.Errorf("result %s", text(res))
	}
}

// Popups past the window's edges make the box larger than the window, and the detail image keeps logical pixels of
// the whole box.
func TestDetailOfPopupsPastTheEdge(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize = 1280
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PopupBox = &hypr.Rect{Width: 1600, Height: 400} })
	e.screenshot(`{"window":"0xa1"}`)
	shots := e.fake.requestsMatching("j/hyprmasq screenshot")
	if len(shots) != 1 || !strings.HasSuffix(shots[0], "/f1.raw max 1280 popups raw detail "+e.server.shotDir+"/f1-detail.raw") {
		t.Fatalf("requests %q", shots)
	}
	if d := e.server.shots.get("s1").Detail; d == nil || d.Scale != 1 || d.Width != 1600 || d.Max != 1600 {
		t.Errorf("detail %+v", d)
	}
}

// joinTouching joins as the simple loop that starts over after every join does, without its time for many changes.
func TestJoinTouching(t *testing.T) {
	naive := func(rects []image.Rectangle) []image.Rectangle {
		rects = append([]image.Rectangle(nil), rects...)
		for joined := true; joined; {
			joined = false
			for i := 0; i < len(rects) && !joined; i++ {
				for j := i + 1; j < len(rects); j++ {
					if rects[i].Inset(-1).Overlaps(rects[j]) {
						rects[i] = rects[i].Union(rects[j])
						rects = append(rects[:j], rects[j+1:]...)
						joined = true
						break
					}
				}
			}
		}
		return rects
	}
	sorted := func(rects []image.Rectangle) []image.Rectangle {
		sort.Slice(rects, func(i, j int) bool {
			a, b := rects[i], rects[j]
			return a.Min.Y < b.Min.Y || a.Min.Y == b.Min.Y && (a.Min.X < b.Min.X || a.Min.X == b.Min.X && a.Max.X < b.Max.X)
		})
		return rects
	}
	random := rand.New(rand.NewPCG(1, 2))
	for round := range 500 {
		var rects []image.Rectangle
		for range random.IntN(40) {
			x, y := random.IntN(300), random.IntN(300)
			rects = append(rects, image.Rect(x, y, x+1+random.IntN(60), y+1+random.IntN(60)))
		}
		want := sorted(naive(rects))
		if got := sorted(joinTouching(append([]image.Rectangle(nil), rects...))); !reflect.DeepEqual(got, want) {
			t.Fatalf("round %d: %v, want %v", round, got, want)
		}
	}
	// Two changes in each cell of a grid over a 5120×2880 window, as a reviewer timed: the simple loop took 4.7 s.
	var grid []image.Rectangle
	for x := 0; x+140 <= 5120; x += 140 {
		for y := 0; y+80 <= 2880; y += 80 {
			grid = append(grid, image.Rect(x, y, x+54, y+54), image.Rect(x+20, y+10, x+74, y+64))
		}
	}
	start := time.Now()
	if got := joinTouching(grid); len(got) != len(grid)/2 {
		t.Errorf("%d regions of %d changes", len(got), len(grid))
	}
	if took := time.Since(start); took > 2*time.Second {
		t.Errorf("joining %d rectangles took %v", len(grid), took)
	}
}

// A comparison that would not end in time leaves the full shot the time it needs.
func TestComparisonLeavesTimeForTheFullShot(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	ctx, cancel := context.WithTimeout(t.Context(), fallbackReserve-100*time.Millisecond)
	defer cancel()
	out := &actOut{}
	sh, images, err := e.server.captureChanges(ctx, &actRequest{window: "0xa1", captureMode: "changes", base: e.server.shots.get("s1")}, out, "1a")
	if err != nil || sh == nil || len(images) != 1 || out.Shot.FullShot != "too little time left to compare with s1" {
		t.Errorf("shot %+v, %d images, %v: %+v", sh, len(images), err, out.Shot)
	}
}
