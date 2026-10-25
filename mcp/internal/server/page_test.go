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
	v := alignViews(a, b, func() bool { return false })
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
	v := alignViews(grayOf(bordered(0), 800, 600), grayOf(bordered(42), 800, 600), func() bool { return false })
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
	v = alignViews(grayOf(panes(0), 320, 200), grayOf(panes(42), 320, 200), func() bool { return false })
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

// A known fading scrollbar may keep committing between the reference and input.
// Its evidence must survive both an edit capture and the previous act's shot.
// An animation in the content still makes that same counter drift refuse more input.
func TestPageKeepsScrollbarEvidence(t *testing.T) {
	for _, prefix := range []bool{false, true} {
		for _, strip := range []bool{false, true} {
			t.Run(strconv.FormatBool(prefix)+"/"+strconv.FormatBool(strip), func(t *testing.T) {
				e := newTestEnv(t)
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
