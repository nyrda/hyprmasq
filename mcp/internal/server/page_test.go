package server

import (
	"encoding/json"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
	"math"
	"strconv"
	"strings"
	"testing"
)

func scrollShift(a, b grayImage) (int, bool, int, int) {
	v := alignViews(a, b, nil, func() bool { return false })
	return v.shift, v.sure, v.top, v.bottom
}

// page draws a long page, scrolled down by offset logical pixels, under a toolbar 20 pixels high that stays: rows of
// text-like blocks that differ from row to row, so that no other shift matches.
func page(offset float64) func(x, y float64) uint8 {
	return func(x, y float64) uint8 {
		if y < 20 {
			if int(x)/9%2 == 0 {
				return 60
			}
			return 200
		}
		line := uint32(int(y+offset) / 6)
		word := uint32(int(x)/13) + line*7
		if (line*73856093^word*19349663)>>7%7 < 3 {
			return 30
		}
		return 240
	}
}

func grayOf(paint func(x, y float64) uint8, w, h int) grayImage {
	g := grayImage{w: w, h: h, pix: make([]float32, w*h)}
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			g.pix[y*w+x] = float32(paint(float64(x)+0.5, float64(y)+0.5))
		}
	}
	return g
}

// The alignment finds how far the content moved and the rows that scrolled below a toolbar, and is never sure of a wrong
// shift of a page whose rows repeat, as a spreadsheet's empty ones do.
func TestScrollShift(t *testing.T) {
	shift, sure, top, bottom := scrollShift(grayOf(page(0), 320, 200), grayOf(page(42), 320, 200))
	if shift != 42 || !sure || top != 20 || bottom != 200 {
		t.Errorf("shift %d sure %v rows %d to %d", shift, sure, top, bottom)
	}
	// Empty cells, as in a spreadsheet, match at every whole number of rows: without row numbers, a shift by part of a
	// row is not sure, and with row numbers that differ, a sure shift is the true one.
	grid := func(offset float64, numbered bool) func(x, y float64) uint8 {
		return func(x, y float64) uint8 {
			if row := uint32(int(y+offset) / 12); numbered && x < 24 && (row*2654435761)>>(uint32(x)/4+3)&1 == 1 {
				return 0
			}
			if int(y+offset)%12 == 0 || int(x)%40 == 0 {
				return 120
			}
			return 250
		}
	}
	if _, sure, _, _ := scrollShift(grayOf(grid(0, false), 320, 200), grayOf(grid(42, false), 320, 200)); sure {
		t.Error("sure of a grid without row numbers")
	}
	if shift, sure, _, _ := scrollShift(grayOf(grid(0, true), 320, 200), grayOf(grid(42, true), 320, 200)); sure && shift != 42 {
		t.Errorf("a grid with row numbers: sure of shift %d", shift)
	}
	// Short labels down the left edge of a wide window that is blank otherwise, as a list of names: thin strokes, a
	// few pixels a row, that move with the page and match at one shift alone.
	labels := func(offset float64) func(x, y float64) uint8 {
		return func(x, y float64) uint8 {
			// An overlay scrollbar's thumb at the right edge, which moves its own way.
			if x >= 1274 {
				if y >= offset/10 && y < offset/10+60 {
					return 120
				}
				return 246
			}
			line, in := int(y+offset)/25, int(y+offset)%25
			if x < 24 && in >= 6 && in < 14 && (uint32(line)*2654435761^uint32(x)*40503^uint32(in)*9973)>>9%9 == 0 {
				return 40
			}
			return 246
		}
	}
	if shift, sure, _, _ := scrollShift(grayOf(labels(0), 1280, 400), grayOf(labels(131), 1280, 400)); shift != 131 || !sure {
		t.Errorf("labels: shift %d sure %v", shift, sure)
	}
	if shift, sure, _, _ := scrollShift(grayOf(page(0), 320, 200), grayOf(page(0), 320, 200)); shift != 0 || !sure {
		t.Errorf("unchanged: shift %d sure %v", shift, sure)
	}
}

func TestPageBordersAreNotFixedPanes(t *testing.T) {
	bordered := func(offset float64) func(float64, float64) uint8 {
		return func(x, y float64) uint8 {
			if x == 8.5 || x == 778.5 {
				return 0
			}
			if x >= 30 && x < 130 {
				return page(offset)(x, y)
			}
			return 240
		}
	}
	v := alignViews(grayOf(bordered(0), 800, 600), grayOf(bordered(42), 800, 600), nil, func() bool { return false })
	if !v.sure || v.shift != 42 {
		t.Fatalf("bordered page: %+v", v)
	}
	panes := func(offset float64) func(float64, float64) uint8 {
		return func(x, y float64) uint8 {
			if x >= 200 {
				return page(0)(x, y)
			}
			return page(offset)(x, y)
		}
	}
	v = alignViews(grayOf(panes(0), 320, 200), grayOf(panes(42), 320, 200), nil, func() bool { return false })
	if v.sure {
		t.Fatalf("a fixed content pane: %+v", v)
	}
}

// Independent painted offsets represent a wheel moving 15 logical pixels per
// notch in a 180-pixel viewport below a fixed toolbar. A page must expose new
// content without losing the overlap, in either direction, after edits too.
func TestPageScrollKeepsOverlap(t *testing.T) {
	for _, direction := range []int{1, -1} {
		for _, prefix := range []bool{false, true} {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = page(300) })
			e.screenshot(`{"window":"0xa1"}`)
			paints := []func(float64, float64) uint8{page(float64(300 + direction*15)), page(float64(300 + direction*90)), page(float64(300 + direction*150))}
			actions := ""
			if prefix {
				paints = append([]func(float64, float64) uint8{page(300)}, paints...)
				actions = `{"action":"click","x":20,"y":20},`
			}
			paintOnWaits(e, nil, paints...)
			request := `{"window":"0xa1","shot":"s1","actions":[` + actions + `{"action":"scroll","x":200,"y":100,"unit":"page","dy":` + strconv.Itoa(direction) + `}]}`
			if prefix {
				way := "down"
				if direction < 0 {
					way = "up"
				}
				request = `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":20,"y":20}],"next_page":{"x":200,"y":100,"direction":"` + way + `"},"capture":"window"}`
			}
			res := e.act(request)
			if res.IsError || get(t, res, "scroll.ended") != "page" || math.Abs(get(t, res, "scroll.moved_px").(float64)-float64(150*direction)) > 2 || images(res) == 0 {
				t.Fatalf("direction %d prefix %v: %s", direction, prefix, text(res))
			}
			if prefix && get(t, res, "before_scroll.wait.ended") != "quiet" {
				t.Fatal(text(res))
			}
			for _, input := range e.inputs() {
				if strings.Contains(strings.Join(input, " "), "scroll") {
					t.Fatalf("page sent as an app-scaled smooth scroll: %v", input)
				}
			}
		}
	}
}

func TestPageScrollStopsWhenOnlyAControlChanges(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = func(x, y float64) uint8 { return 0 } })
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}))
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"scroll","x":200,"y":100,"unit":"page","dy":1}]}`)
	if res.IsError || len(e.inputs()) != 1 || strings.Contains(text(res), "at its end") {
		t.Fatal(text(res), e.inputs())
	}
}

func TestPageSmallNotchesStayWithinSenderLimit(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 64, 400, func(w *fakeWindow) { w.Paint = page(300) })
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, page(301), page(401), page(501), page(601), page(623))
	res := e.act(`{"window":"0xa1","shot":"s1","next_page":{"x":60,"y":300},"deadline_ms":30000}`)
	if res.IsError || get(t, res, "scroll.ended") != "page" {
		t.Fatal(text(res))
	}
	for _, args := range e.inputs() {
		for i, arg := range args {
			if arg == "wheel" {
				n, err := strconv.Atoi(args[i+2])
				if err != nil || n < 1 || n > maxNotches {
					t.Fatalf("wheel exceeds sender limit: %v", args)
				}
			}
		}
	}
}

func TestInvalidPageRequestsSendNothing(t *testing.T) {
	for _, fields := range []string{
		`"next_page":true`, `"next_page":[]`, `"next_page":{}`, `"next_page":{"x":null,"y":50}`,
		`"next_page":{"x":50,"y":50,"direction":"left"}`, `"next_page":{"x":50,"y":50,"dy":1}`,
		`"next_page":{"x":5000,"y":50}`, `"next_page":{"x":50,"y":50},"capture":"none"`,
		`"next_page":{"x":50,"y":50},"capture":{"shot":"s1","x":0,"y":0,"width":50,"height":50}`,
		`"actions":[["scroll",50,50,1],["scroll",50,50,1,"page"]]`,
		`"actions":[["scroll",50,50,1,"page"],["key","enter"]]`,
	} {
		e := newTestEnv(t)
		e.window("0xa1", "1a", 320, 200)
		e.screenshot(`{"window":"0xa1"}`)
		res := e.act(`{"window":"0xa1","shot":"s1",` + fields + `}`)
		if errorCode(t, res) == "" || get(t, res, "actions.delivery") != "none" || len(e.inputs()) != 0 {
			t.Fatal(fields, text(res), e.inputs())
		}
	}
}

// A completed edit capture does not imply enough budget for another quiet wait.
// The fake wait returns at once, but the requested second wait cannot fit even
// then. Keep the edits delivered and show them without attempting a wheel step.
func TestPageWithoutBudgetKeepsDeliveredEdits(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}))
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[["click",10,10]],"next_page":{"x":200,"y":100},"wait":{"quiet_ms":10000},"deadline_ms":10000}`)
	if res.IsError || get(t, res, "actions.done") != 1.0 || get(t, res, "actions.delivery") != "partial" || get(t, res, "actions.held_back") != "too little time was left for it" || len(e.inputs()) != 1 {
		t.Fatal(text(res), e.inputs())
	}
	if images(res) != 1 || get(t, res, "before_scroll") != nil || grayAt(t, res, 0, 210, 110) != 255 {
		t.Fatal(text(res))
	}
}

// A frame can come right after the first view of the scroll, which sends a hidden window the frame callback that it held
// a frame back for. Nothing has been sent yet, so the view is taken again, and the scroll goes on.
func TestPageScrollIsNotStoppedByAFrameRightAfterItsFirstView(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = page(300) })
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, page(315), page(390), page(450))
	e.fake.with(func() { e.fake.find("0xa1").HeldFrame = true })
	res := e.act(`{"window":"0xa1","shot":"s1","next_page":{"x":200,"y":100}}`)
	if res.IsError || get(t, res, "scroll.ended") != "page" || get(t, res, "scroll.moved_px") != 150.0 {
		t.Fatal(text(res))
	}
}

// Frame callbacks can start an animation in the window, which goes on through the first view and the first notch. A
// plugin from before the drawn command does not tell what the window drew then. The wait before the first view tells
// of the animation, so a scrollbar's fade does not stop the scroll, while an animation in the content does.
func TestPageTakesAnimationsFromTheWaitBeforeItsFirstView(t *testing.T) {
	for _, strip := range []bool{false, true} {
		e := newTestEnv(t)
		e.fake.with(func() { e.fake.noDrawn = true })
		e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = page(300) })
		e.screenshot(`{"window":"0xa1"}`)
		paintOnWaits(e, nil, page(315), page(390), page(450))
		box, ended := hypr.IntRect{X: 20, Y: 40, Width: 16, Height: 20}, "reference_changed"
		if strip {
			box, ended = hypr.IntRect{X: 312, Y: 20, Width: 8, Height: 100}, "page"
		}
		e.fake.with(func() {
			wait := e.fake.onWait
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				var reply map[string]any
				if err := json.Unmarshal([]byte(wait(w, args, cancel)), &reply); err != nil {
					panic(err)
				}
				reply["animated"] = []hypr.IntRect{box}
				return jsonReply(reply)
			}
			e.fake.onState = func(w *fakeWindow) { w.Commits++ }
		})
		res := e.act(`{"window":"0xa1","shot":"s1","next_page":{"x":200,"y":100}}`)
		if res.IsError || get(t, res, "scroll.ended") != ended {
			t.Fatalf("strip %v: %s", strip, text(res))
		}
	}
}

// With a plugin from before the drawn command, a known fading scrollbar may keep committing between the reference and
// input. Its evidence must survive both an edit capture and the previous act's shot. An animation in the content
// still makes that same counter drift refuse more input.
func TestPageKeepsScrollbarEvidence(t *testing.T) {
	for _, prefix := range []bool{false, true} {
		for _, strip := range []bool{false, true} {
			t.Run(strconv.FormatBool(prefix)+"/"+strconv.FormatBool(strip), func(t *testing.T) {
				e := newTestEnv(t)
				e.fake.with(func() { e.fake.noDrawn = true })
				e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = page(300) })
				e.screenshot(`{"window":"0xa1"}`)
				paintOnWaits(e, nil, page(300), page(315), page(390), page(450))
				box := hypr.IntRect{X: 20, Y: 40, Width: 16, Height: 20}
				if strip {
					box = hypr.IntRect{X: 312, Y: 20, Width: 8, Height: 100}
				}
				e.fake.with(func() {
					wait := e.fake.onWait
					e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
						var reply map[string]any
						if err := json.Unmarshal([]byte(wait(w, args, cancel)), &reply); err != nil {
							panic(err)
						}
						reply["animated"] = []hypr.IntRect{box}
						return jsonReply(reply)
					}
					// Capture and send read the counter separately. Represent commits
					// that change only the known animation between those reads.
					e.fake.onState = func(w *fakeWindow) { w.Commits++ }
				})
				shot, actions := "s1", `[[20,20]]`
				if !prefix {
					shot = shotID(t, e.act(`{"window":"0xa1","capture":"window"}`))
					actions = `[]`
				}
				res := e.act(`{"shot":"` + shot + `","actions":` + actions + `,"next_page":{"x":200,"y":100}}`)
				ended, notches := "reference_changed", 1.0
				if strip {
					ended, notches = "page", 10
				}
				if res.IsError || get(t, res, "scroll.ended") != ended || get(t, res, "scroll.notches") != notches {
					t.Fatal(text(res))
				}
				if prefix && get(t, res, "before_scroll.wait.animated.0") == nil {
					t.Fatalf("the first view's animation has no shot coordinates: %s", text(res))
				}
			})
		}
	}
}

// A window with an animation, as a caret that blinks in a text field or a spinner, draws between the view that a step
// measures from and the step's input. The scroll goes on when the plugin tells that nothing but the animation drew, and
// measures the page as without it: the animation's pixels differ from view to view, in the content and in the toolbar
// that stays, and neither makes the view look taller. It stops when the window keeps drawing something else, or when
// the animation is where the wheel goes, and sends no more than the first notch then.
//
// A single frame that the plugin does not take for an animation holds the first notch back for another view. The
// scroll goes on from it when it shows the same as the view before, apart from the animations that the wait before it
// told: the frame showed nothing new, or was the first of an animation. New content in it stops the scroll.
func TestPageScrollGoesOnThroughAnimationAndStopsForAChange(t *testing.T) {
	content, toolbar := hypr.IntRect{X: 20, Y: 40, Width: 2, Height: 12}, hypr.IntRect{X: 100, Y: 4, Width: 10, Height: 10}
	const (
		// The animation draws before the input of each step.
		animation = iota
		// And the window draws something else before the input of each step.
		animationAndChanges
		// The animation draws only after the input of each step, where the wait tells of it.
		animationAfterInput
		// A frame that is no animation comes right after each of the views that the scroll takes first, so that one comes
		// between the last of them and the input, and the box shows the same.
		oneFrame
		// That frame changes the box, and the wait after it tells the box as an animation.
		firstFrameOfAnimation
		// That frame changes the box, and no wait tells of an animation.
		newContent
	)
	for _, c := range []struct {
		name  string
		box   hypr.IntRect
		draws int
		ended string
	}{
		{"a caret in the content", content, animation, "page"},
		{"a spinner in the toolbar", toolbar, animation, "page"},
		{"a spinner in the toolbar that draws after the input", toolbar, animationAfterInput, "page"},
		{"a caret in the content and changes", content, animationAndChanges, "reference_changed"},
		{"a spinner in the toolbar and changes", toolbar, animationAndChanges, "reference_changed"},
		{"an animation under the pointer", hypr.IntRect{X: 90, Y: 40, Width: 20, Height: 20}, animation, "reference_changed"},
		{"a frame that shows nothing new", content, oneFrame, "page"},
		{"the first frame of an animation", content, firstFrameOfAnimation, "page"},
		{"a frame with new content", content, newContent, "reference_changed"},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			w := e.window("0xa1", "1a", 320, 200)
			// The box shows another gray level in every view.
			changing := func(paint func(x, y float64) uint8) func(x, y float64) uint8 {
				if c.draws == oneFrame {
					return paint
				}
				return func(x, y float64) uint8 {
					if x >= float64(c.box.X) && x < float64(c.box.X+c.box.Width) && y >= float64(c.box.Y) && y < float64(c.box.Y+c.box.Height) {
						return uint8(20 + w.Frames*37%200)
					}
					return paint(x, y)
				}
			}
			e.fake.with(func() { w.Paint = changing(page(300)) })
			e.screenshot(`{"window":"0xa1"}`)
			paintOnWaits(e, nil, changing(page(315)), changing(page(390)), changing(page(450)))
			e.fake.with(func() {
				switch c.draws {
				case animation, animationAndChanges:
					e.fake.onState = func(w *fakeWindow) {
						w.animate(c.box)
						if c.draws == animationAndChanges {
							w.Commits++
						}
					}
				case oneFrame, firstFrameOfAnimation, newContent:
					// The scroll takes its first view up to three times while the window draws after it.
					views := w.Frames
					e.fake.onScreenshot = func(w *fakeWindow) string {
						w.HeldFrame = w.Frames < views+1+firstViewRetakes
						return ""
					}
				}
				if c.draws != animationAfterInput && c.draws != firstFrameOfAnimation {
					return
				}
				wait := e.fake.onWait
				e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
					var reply map[string]any
					if err := json.Unmarshal([]byte(wait(w, args, cancel)), &reply); err != nil {
						panic(err)
					}
					reply["animated"] = []hypr.IntRect{c.box}
					return jsonReply(reply)
				}
			})
			// The shot has two pixels a logical pixel, so the wheel goes to 100,50.
			res := e.act(`{"window":"0xa1","shot":"s1","next_page":{"x":200,"y":100}}`)
			if res.IsError || get(t, res, "scroll.ended") != c.ended {
				t.Fatal(text(res))
			}
			if c.ended != "page" {
				if get(t, res, "scroll.notches") != 1.0 || get(t, res, "scroll.moved_px") != nil {
					t.Fatal(text(res))
				}
				return
			}
			if get(t, res, "scroll.moved_px") != 150.0 || get(t, res, "scroll.viewport_px") != 180.0 {
				t.Fatal(text(res))
			}
		})
	}
}
