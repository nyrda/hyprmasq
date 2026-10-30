package server

import (
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A click after keys that leave a popup open that asked for a grab, as Enter in a Qt dropdown with a GTK theme closes
// its list and opens it again, would only close the popup when it lies outside it: act holds the click back with the
// actions after it, and the result says so. A click into the popup goes out, and so does one beside a popup without a
// grab, such as a tooltip, or without a popup.
func TestAPressThatWouldOnlyCloseAPopupIsHeldBack(t *testing.T) {
	for _, c := range []struct {
		name   string
		popups []hypr.Popup
		held   bool
	}{
		// The shot is at scale 2, so the click comes at 75,60 in logical pixels, which popups are in.
		{"outside a popup with a grab", []hypr.Popup{{X: 100, Y: 20, Width: 90, Height: 150, Grab: true}}, true},
		{"outside a nested popup with a grab", []hypr.Popup{{X: 100, Y: 20, Width: 90, Height: 150, Grab: true}, {X: 190, Y: 20, Width: 30, Height: 30}}, true},
		{"in the popup", []hypr.Popup{{X: 50, Y: 20, Width: 60, Height: 100, Grab: true}}, false},
		{"in a nested popup", []hypr.Popup{{X: 100, Y: 20, Width: 90, Height: 150, Grab: true}, {X: 60, Y: 50, Width: 40, Height: 20}}, false},
		{"outside a tooltip", []hypr.Popup{{X: 100, Y: 20, Width: 90, Height: 20}}, false},
		{"without a popup", nil, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits, w.Popups = true, 10, c.popups })
			shot := shotID(t, e.screenshot(`{"window":"0xa1"}`))
			res := e.act(`{"shot":"` + shot + `","actions":[{"action":"key","keys":["enter"]},{"action":"click","x":150,"y":120},{"action":"key","keys":["a"]}]}`)
			if res.IsError {
				t.Fatalf("error %s", text(res))
			}
			terminated := slices.ContainsFunc(readInputLog(t, e.inputLog), func(r inputRun) bool { return r.Event == "sigterm" })
			if !c.held {
				if get(t, res, "actions.delivery") != "complete" || get(t, res, "actions.done") != 3.0 || get(t, res, "actions.held_back") != nil || terminated {
					t.Fatalf("actions %v, sigterm %v", get(t, res, "actions"), terminated)
				}
				return
			}
			want := map[string]any{"done": 1.0, "total": 3.0, "delivery": "partial", "failed_index": 1.0, "held_back": heldPressReason}
			if got := get(t, res, "actions"); !reflect.DeepEqual(got, want) || !terminated {
				t.Fatalf("actions %v, want %v, sigterm %v", got, want, terminated)
			}
			if summary := summaryOf(t, res); !strings.HasPrefix(summary, "1/3 delivered, actions[1] (click) and after held back: popup open · ") {
				t.Errorf("summary %q", summary)
			}
			if hint, _ := get(t, res, "hint").(string); !startsWithHint(hint, heldBackHint(&actionsOut{HeldBack: heldPressReason, heldPress: true, FailedIndex: ptr(1)}, []action{{Kind: "key"}, {Kind: "click"}})) {
				t.Errorf("hint %q", hint)
			}
		})
	}
}

// The point of a press after a sync is that of the click's motion or of the path's first point. Motion alone, a scroll
// and a path without a button press nothing.
func TestPressAfter(t *testing.T) {
	steps := []inputStep{
		{Batch: 0, Argv: []string{"keys", "enter", "1"}},
		{Batch: 1, Argv: []string{"sync", "0", "1000"}},
		{Batch: 1, Argv: []string{"move", "10.5", "20"}},
		{Batch: 1, Argv: []string{"click", "left", "1", "-"}},
		{Batch: 2, Argv: []string{"sync", "0", "1000"}},
		{Batch: 2, Argv: []string{"path", "2", "3", "4", "5", "6", "left", "-"}},
		{Batch: 3, Argv: []string{"sync", "0", "1000"}},
		{Batch: 3, Argv: []string{"path", "2", "3", "4", "5", "6", "none", "-"}},
		{Batch: 4, Argv: []string{"sync", "0", "1000"}},
		{Batch: 4, Argv: []string{"move", "1", "1"}},
		{Batch: 4, Argv: []string{"wheel", "0", "1"}},
		{Batch: 5, Argv: []string{"sync", "0", "1000"}},
	}
	for _, c := range []struct {
		step  int
		x, y  float64
		press bool
	}{{1, 10.5, 20, true}, {4, 3, 4, true}, {6, 0, 0, false}, {8, 0, 0, false}, {11, 0, 0, false}} {
		if x, y, press := pressAfter(steps, c.step); x != c.x || y != c.y || press != c.press {
			t.Errorf("step %d: %v,%v %v", c.step, x, y, press)
		}
	}
}
