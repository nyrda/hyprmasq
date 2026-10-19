package server

import (
	"strconv"
	"strings"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestLateShortcutHintConditions(t *testing.T) {
	key := func(keys ...string) action { return action{Kind: "key", Keys: keys} }
	for _, c := range []struct {
		action action
		want   bool
	}{
		{key("ctrl", "l"), true}, {key("alt", "left"), true}, {key("super", "d"), true}, {key("ctrl", "shift", "t"), true},
		{key("Control_L", "w"), true}, {key("f6"), true}, {key("F35"), true}, {key("XF86AudioPlay"), true},
		{key("ctrl"), false}, {key("ctrl", "shift"), false}, {key("shift", "a"), false}, {key("enter"), false}, {key("esc"), false},
		{key("a"), false}, {key("f"), false}, {key("f36"), false}, {action{Kind: "type", Text: "hello\n"}, false},
		{action{Kind: "click", Modifiers: []string{"ctrl"}}, false},
	} {
		if got := shortcutKey(c.action); got != c.want {
			t.Errorf("%s %v: %v", c.action.Kind, c.action.Keys, got)
		}
	}

	shared := hypr.State{Capabilities: hypr.Capabilities{Keyboard: "yes"}}
	own := hypr.State{Capabilities: hypr.Capabilities{Lease: true, Keyboard: "yes"}}
	focused := hypr.State{Capabilities: hypr.Capabilities{DnD: true, Keyboard: "yes"}}
	x11 := hypr.State{XWayland: true, Capabilities: hypr.Capabilities{Keyboard: "x11-focus"}}
	satellite := hypr.State{Capabilities: hypr.Capabilities{Keyboard: "x11-focus"}}
	batch := []action{key("ctrl", "l")}
	unchangedShot := &shotOut{ID: "s3", Changes: &changesOut{Base: "s2"}}
	changedShot := &shotOut{ID: "s3", Changes: &changesOut{Base: "s2", Regions: []regionOut{{Shot: "s4"}}}}
	result := func(change func(out *actOut)) *actOut {
		out := &actOut{Actions: &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, Wait: &waitOut{Ended: "no_redraw"}}
		if change != nil {
			change(out)
		}
		return out
	}
	for name, c := range map[string]struct {
		out   *actOut
		batch []action
		st    hypr.State
		want  bool
	}{
		"no redraw":            {result(nil), batch, shared, true},
		"no change":            {result(func(o *actOut) { o.Wait.Ended, o.Shot = "quiet", unchangedShot }), batch, shared, true},
		"partly delivered":     {result(func(o *actOut) { o.Actions.Delivery = "partial" }), batch, shared, false},
		"after typing":         {result(nil), []action{{Kind: "type", Text: "x"}, key("ctrl", "a")}, shared, true},
		"a change":             {result(func(o *actOut) { o.Wait.Ended, o.Shot = "quiet", changedShot }), batch, shared, false},
		"a redraw":             {result(func(o *actOut) { o.Wait.Ended = "quiet" }), batch, shared, false},
		"a timeout":            {result(func(o *actOut) { o.Wait.Ended = "timeout" }), batch, shared, false},
		"cancelled":            {result(func(o *actOut) { o.Wait.Ended = "cancelled" }), batch, shared, false},
		"nothing sent":         {result(func(o *actOut) { o.Actions.Delivery = "none" }), batch, shared, false},
		"no wait":              {result(func(o *actOut) { o.Wait = nil }), batch, shared, false},
		"window closed":        {result(func(o *actOut) { o.WindowClosed = true }), batch, shared, false},
		"focus returned":       {result(func(o *actOut) { o.Actions.SharedFocusReturned = &focusReturnedOut{Effect: "unknown"} }), batch, shared, false},
		"opened a window":      {result(func(o *actOut) { o.NewWindows = []newWindowOut{{Window: "0xc3", fromTarget: true}} }), batch, shared, false},
		"another's new window": {result(func(o *actOut) { o.NewWindows = []newWindowOut{{Window: "0xc3"}} }), batch, shared, true},
		"typing only":          {result(nil), []action{{Kind: "type", Text: "x"}}, shared, false},
		"own connection":       {result(nil), batch, own, false},
		"the user's window":    {result(nil), batch, focused, false},
		"x11":                  {result(nil), batch, x11, false},
		"xwayland-satellite":   {result(nil), batch, satellite, false},
	} {
		if got := lateShortcutHint(c.out, c.batch, c.st); (got == hintLateShortcut) != c.want || got != "" && got != hintLateShortcut {
			t.Errorf("%s: %q", name, got)
		}
	}
}

// A shortcut to a window that shares its app's keyboard focus with the user's window may come too late for that focus,
// so act says so when the window then showed no effect: no redraw, or a changes shot without a change. A redraw, a
// window of its own connection or the window with the user's focus get no such hint.
func TestLateShortcutBesideTheUsersWindow(t *testing.T) {
	e := newTestEnv(t)
	shared := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Caps.Lease, w.Caps.Hold = false, false })
	e.window("0xb2", "2b", 320, 200)
	noRedraw := func() {
		e.fake.with(func() {
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				expect, _ := strconv.Atoi(args["expect"])
				return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
					"commitsSince": 0, "quietMs": expect, "elapsedMs": expect, "resizing": false})
			}
		})
	}
	hinted := func(res *mcp.CallToolResult) bool {
		hint, _ := get(t, res, "hint").(string)
		return strings.Contains(hint, hintLateShortcut) && strings.Contains(text(res), hintLateShortcut)
	}

	// The hint about keys that the window drew nothing for.
	noRedrawHint := waitHint(&waitOut{Ended: "no_redraw"}, []action{{Kind: "key"}}, "", &actionsOut{Done: 1, Total: 1, Delivery: "complete"}, nil)

	noRedraw()
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none","wait":{"expect_ms":300}}`)
	// It follows the hint about the missing redraw, which still applies.
	if hint := get(t, res, "hint"); res.IsError || !hinted(res) || hint != joinHints(noRedrawHint, hintLateShortcut) {
		t.Fatalf("%s", text(res))
	}
	// The window of its own connection.
	res = e.act(`{"window":"0xb2","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none","wait":{"expect_ms":300}}`)
	if res.IsError || hinted(res) || get(t, res, "hint") != noRedrawHint {
		t.Errorf("%s", text(res))
	}
	// The window with the user's focus keeps it.
	e.fake.with(func() { shared.Caps.DnD = true })
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none","wait":{"expect_ms":300}}`)
	if res.IsError || hinted(res) {
		t.Errorf("%s", text(res))
	}
	e.fake.with(func() { shared.Caps.DnD = false })
	// A shortcut that redrew the window worked, as far as anything tells.
	e.fake.with(func() { e.fake.onWait = nil })
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none"}`)
	if res.IsError || hinted(res) {
		t.Errorf("%s", text(res))
	}
	// A changes shot that found no change, also after a function key.
	res = e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"key","keys":["f6"]}],"capture":"changes"}`)
	if res.IsError || !hinted(res) || !strings.Contains(get(t, res, "summary").(string), "no change") {
		t.Errorf("%s", text(res))
	}
}
