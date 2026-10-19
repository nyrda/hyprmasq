package server

import (
	"testing"
)

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
