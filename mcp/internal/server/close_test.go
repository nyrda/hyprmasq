package server

import (
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestClose(t *testing.T) {
	slow(t)
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
