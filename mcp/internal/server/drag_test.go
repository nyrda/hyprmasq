package server

import (
	"fmt"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A drag-and-drop that the window's app starts from a driven window that is not active would take the user's pointer,
// so the plugin cancels it and counts it in the window's drag record. act tells the wait the last drag it knew of
// before the input, so that the cancel ends the wait's change phase at once, reads the record again after the wait,
// and reports the cancel with what to do instead, in place of the hints that guess why nothing changed. Batches
// without a press, records of another socket and a plugin without a record get no second look or no report.
func TestDragCancelledToProtectTheUsersInput(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Drag = &hypr.Drag{} })
	// The app starts a drag during the input, which the plugin cancels, and draws nothing: the wait ends without a
	// redraw as soon as it sees the cancel.
	var waits []map[string]string
	cancelOnWait := func(socketID string) {
		e.fake.with(func() {
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				waits = append(waits, args)
				e.fake.with(func() {
					w.Drag = &hypr.Drag{ID: w.Drag.ID + 1, Phase: "cancelled", Reason: "shared_focus", Cancelled: w.Drag.Cancelled + 1}
					if socketID != "" {
						w.SocketID = socketID
					}
				})
				return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": false,
					"commitsSince": 0, "quietMs": 200, "elapsedMs": 210, "resizing": false})
			}
		})
	}
	cancelOnWait("")
	res := e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"drag","from":[10,10],"to":[200,100]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.delivery") != "complete" || get(t, res, "actions.drag_and_drop.outcome") != "cancelled" ||
		get(t, res, "actions.drag_and_drop.reason") != "shared_focus" || get(t, res, "actions.drag_and_drop.private") != false || get(t, res, "actions.drag_and_drop.count") != nil {
		t.Fatalf("%s", text(res))
	}
	if summary := get(t, res, "summary").(string); !strings.HasPrefix(summary, "1/1 delivered · drag-and-drop cancelled to protect the user's input · no redraw · ") {
		t.Errorf("summary %q", summary)
	}
	if hint := get(t, res, "hint"); hint != dragHint(&dragOut{Outcome: "cancelled", Reason: "shared_focus"}, nil) {
		t.Errorf("hint %q", hint)
	}
	if len(waits) != 1 || waits[0]["drags"] != "0" {
		t.Fatalf("waits %v", waits)
	}
	// A click can start one too, and two in one batch are counted. The wait was told the last one before the input.
	cancelOnWait("")
	e.fake.with(func() {
		onWait := e.fake.onWait
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			e.fake.with(func() { w.Drag.ID++ })
			return onWait(w, args, cancel)
		}
	})
	res = e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"click","x":10,"y":10}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.drag_and_drop.outcome") != "cancelled" || get(t, res, "actions.drag_and_drop.count") != 2.0 || waits[1]["drags"] != "1" {
		t.Fatalf("%v %s", waits, text(res))
	}
	// A shot that shows nothing changed adds no guess about a missed input: the cancel says why.
	cancelOnWait("")
	res = e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"drag","from":[10,10],"to":[200,100]}],"capture":"changes"}`)
	if res.IsError || get(t, res, "hint") != dragHint(&dragOut{Outcome: "cancelled", Reason: "shared_focus"}, &shotOut{}) {
		t.Errorf("%s", text(res))
	}
	states := func() int { return len(e.fake.requestsMatching("j/hyprmasq state")) }
	// Keys start no drag, so nothing is read again for them.
	cancelOnWait("")
	before := states()
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","l"]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.drag_and_drop") != nil || states()-before != 2 {
		t.Errorf("%d state reads: %s", states()-before, text(res))
	}
	// No drag during the batch: no report.
	e.fake.with(func() { e.fake.onWait = nil })
	res = e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"click","x":10,"y":10}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.drag_and_drop") != nil || strings.Contains(text(res), "drag-and-drop") {
		t.Errorf("%s", text(res))
	}
	// A record of a socket that replaced the one the input went to says nothing about that input.
	cancelOnWait("another")
	res = e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"drag","from":[10,10],"to":[200,100]}],"capture":"none"}`)
	if res.IsError || get(t, res, "actions.drag_and_drop") != nil {
		t.Errorf("%s", text(res))
	}
	// A plugin that keeps no record gets no drags in its wait.
	e.fake.with(func() {
		w.Drag = nil
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			waits = append(waits, args)
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "socketId": w.socketID(), "redrawObserved": true,
				"commitsSince": 1, "quietMs": 200, "elapsedMs": 210, "resizing": false})
		}
	})
	res = e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"drag","from":[10,10],"to":[200,100]}],"capture":"none"}`)
	if _, ok := waits[len(waits)-1]["drags"]; res.IsError || ok || get(t, res, "actions.drag_and_drop") != nil {
		t.Errorf("%v %s", waits, text(res))
	}
}

// A drag that starts the app's own drag-and-drop in a window with the dnd capability runs within the app. The wait
// goes on while the drag does, and act reports how it ended: finished, with nothing more to say; cancelled or
// unconfirmed, with why and what to do; or pending at the deadline, in place of the hint about a window that kept
// drawing.
func TestPrivateDragAndDropOutcomes(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Drag = &hypr.Drag{} })
	// The wait ends as the plugin's does: quiet once the drag has ended, or at the deadline while it goes on.
	ends := func(next hypr.Drag) {
		e.fake.with(func() {
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				e.fake.with(func() {
					next.ID = w.Drag.ID + 1
					w.Drag = &next
				})
				if next.Pending() {
					deadline, _ := strconv.Atoi(args["deadline"])
					return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": w.Commits + 2, "socketId": w.socketID(), "redrawObserved": true,
						"commitsSince": 2, "quietMs": 0, "elapsedMs": deadline, "resizing": false})
				}
				return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits + 2, "socketId": w.socketID(), "redrawObserved": true,
					"commitsSince": 2, "quietMs": 200, "elapsedMs": 410, "resizing": false})
			}
		})
	}
	drag := func() *mcp.CallToolResult {
		return e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"drag","from":[10,10],"to":[200,100]}],"capture":"none"}`)
	}

	ends(hypr.Drag{Phase: "finished", Private: true, Started: 1, Finished: 1})
	res := drag()
	if res.IsError || get(t, res, "actions.drag_and_drop.outcome") != "finished" || get(t, res, "actions.drag_and_drop.private") != true || get(t, res, "actions.drag_and_drop.reason") != nil ||
		get(t, res, "hint") != nil || !strings.HasPrefix(get(t, res, "summary").(string), "1/1 delivered · drag-and-drop finished · redraw observed · ") {
		t.Fatalf("%s", text(res))
	}

	ends(hypr.Drag{Phase: "cancelled", Private: true, Reason: "rejected", Started: 1, Cancelled: 1})
	res = drag()
	if res.IsError || get(t, res, "actions.drag_and_drop.outcome") != "cancelled" || get(t, res, "actions.drag_and_drop.reason") != "rejected" ||
		!strings.HasPrefix(get(t, res, "summary").(string), "1/1 delivered · drag-and-drop cancelled (rejected) · ") ||
		get(t, res, "hint") != dragHint(&dragOut{Outcome: "cancelled", Private: true, Reason: "rejected"}, nil) {
		t.Fatalf("%s", text(res))
	}

	// The user's input to the app ends the drag first.
	ends(hypr.Drag{Phase: "cancelled", Private: true, Reason: "user_input", Started: 1, Cancelled: 1})
	if res = drag(); get(t, res, "hint") != dragHint(&dragOut{Outcome: "cancelled", Private: true, Reason: "user_input"}, nil) {
		t.Fatalf("%s", text(res))
	}

	ends(hypr.Drag{Phase: "unconfirmed", Private: true, Reason: "timeout", Started: 1, Unconfirmed: 1})
	res = drag()
	if res.IsError || get(t, res, "actions.drag_and_drop.outcome") != "unconfirmed" || !strings.Contains(text(res), " · drag-and-drop unconfirmed (timeout) · ") ||
		get(t, res, "hint") != dragHint(&dragOut{Outcome: "unconfirmed", Private: true, Reason: "timeout"}, nil) {
		t.Fatalf("%s", text(res))
	}
	// One that ended after the drop for another reason says that one.
	ends(hypr.Drag{Phase: "unconfirmed", Private: true, Reason: "source_gone", Started: 1, Unconfirmed: 1})
	if res = drag(); get(t, res, "hint") != dragHint(&dragOut{Outcome: "unconfirmed", Private: true, Reason: "source_gone"}, nil) {
		t.Fatalf("%s", text(res))
	}

	// Dropped and not yet confirmed at the deadline: the wait went on until then.
	ends(hypr.Drag{Phase: "dropped", Private: true, Started: 1})
	res = drag()
	if res.IsError || get(t, res, "actions.drag_and_drop.outcome") != "pending" || get(t, res, "actions.drag_and_drop.phase") != "dropped" ||
		!strings.HasPrefix(get(t, res, "summary").(string), "1/1 delivered · drag-and-drop pending: dropped, not yet confirmed · timeout after ") ||
		get(t, res, "hint") != dragHint(&dragOut{Outcome: "pending", Phase: "dropped", Private: true}, nil) {
		t.Fatalf("%s", text(res))
	}
	// The act without actions that the hint suggests waits for that drag, which is still pending, and reports how it
	// ended.
	var drags string
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			drags = args["drags"]
			e.fake.with(func() { w.Drag = &hypr.Drag{ID: w.Drag.ID, Phase: "finished", Private: true, Started: 1, Finished: 1} })
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits + 1, "socketId": w.socketID(), "redrawObserved": true,
				"commitsSince": 1, "quietMs": 200, "elapsedMs": 300, "resizing": false})
		}
	})
	res = e.act(`{"window":"0xa1","capture":"none"}`)
	if res.IsError || drags != "5" || get(t, res, "actions.drag_and_drop.outcome") != "finished" || !strings.HasPrefix(get(t, res, "summary").(string), "drag-and-drop finished · redraw observed · ") {
		t.Fatalf("drags %q: %s", drags, text(res))
	}
}

// A batch of several drags reports every drag-and-drop that its drags started: finished only when each one did, and
// otherwise partial, with how many finished, were cancelled and why, and how many drags of the batch started none, as
// when the app did not start its drag-and-drop in time.
func TestDragsOfABatchAreCountedOneByOne(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 640, 480, func(w *fakeWindow) { w.Drag = &hypr.Drag{Ended: []hypr.DragEnd{}} })
	// The batch's drags start these drag-and-drops, which end so.
	ends := func(phases ...string) {
		once := sync.Once{}
		e.fake.with(func() {
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				once.Do(func() {
					var d hypr.Drag
					e.fake.with(func() { d = *w.Drag })
					for _, p := range phases {
						d.ID++
						end := hypr.DragEnd{ID: d.ID, Phase: p}
						if p == "cancelled" {
							end.Reason = "rejected"
						}
						d.Ended = append(slices.Clone(d.Ended), end)
						d.Phase, d.Private, d.Reason = p, true, end.Reason
					}
					e.fake.with(func() { w.Drag = &d })
				})
				return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits + 1, "socketId": w.socketID(), "redrawObserved": true,
					"commitsSince": 1, "quietMs": 200, "elapsedMs": 900, "resizing": false})
			}
		})
	}
	drags := func(n int) *mcp.CallToolResult {
		var actions []string
		for i := range n {
			actions = append(actions, fmt.Sprintf(`{"action":"drag","from":[10,%d],"to":[300,%d]}`, 10+20*i, 10+20*i))
		}
		return e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[` + strings.Join(actions, ",") + `],"capture":"none"}`)
	}

	// Two of eight drags started no drag-and-drop.
	ends("finished", "finished", "finished", "finished", "finished", "finished")
	res := drags(8)
	if res.IsError || get(t, res, "actions.drag_and_drop.outcome") != "partial" || get(t, res, "actions.drag_and_drop.count") != 6.0 ||
		get(t, res, "actions.drag_and_drop.finished") != 6.0 || get(t, res, "actions.drag_and_drop.none") != 2.0 ||
		!strings.HasPrefix(get(t, res, "summary").(string), "8/8 delivered · drag-and-drop: 6 finished, 2 drags started none · ") ||
		get(t, res, "hint") != dragHint(&dragOut{Outcome: "partial", Private: true, Count: 6, Finished: 6, None: 2}, nil) {
		t.Fatalf("%s", text(res))
	}
	// One was cancelled.
	ends("finished", "cancelled", "finished")
	res = drags(3)
	if get(t, res, "actions.drag_and_drop.outcome") != "partial" || get(t, res, "actions.drag_and_drop.cancelled") != 1.0 ||
		!slices.Equal(get(t, res, "actions.drag_and_drop.reasons").([]any), []any{"rejected"}) ||
		!strings.HasPrefix(get(t, res, "summary").(string), "3/3 delivered · drag-and-drop: 2 finished, 1 cancelled (rejected) · ") {
		t.Fatalf("%s", text(res))
	}
	// An unconfirmed drop and a cancel: the unconfirmed one may have moved its item.
	ends("unconfirmed", "cancelled")
	res = drags(2)
	if get(t, res, "actions.drag_and_drop.outcome") != "partial" || get(t, res, "actions.drag_and_drop.unconfirmed") != 1.0 ||
		get(t, res, "hint") != dragHint(&dragOut{Outcome: "partial", Private: true, Count: 2, Unconfirmed: 1, Cancelled: 1, Reasons: []string{"rejected"}}, nil) {
		t.Fatalf("%s", text(res))
	}
	// All of them finished.
	ends("finished", "finished", "finished")
	res = drags(3)
	if get(t, res, "actions.drag_and_drop.outcome") != "finished" || get(t, res, "actions.drag_and_drop.finished") != 3.0 || get(t, res, "hint") != nil ||
		!strings.HasPrefix(get(t, res, "summary").(string), "3/3 delivered · drag-and-drop finished · ") {
		t.Fatalf("%s", text(res))
	}
}

// A drag that the batch starts while an earlier one still waits for its app's confirmation is refused as busy. The
// wait goes on until the earlier one has ended too, and the batch reports its own refusal, not the earlier one's end.
func TestBusyDragReportsItsOwnRefusal(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) {
		w.Drag = &hypr.Drag{ID: 5, Phase: "dropped", Private: true, Started: 5, Finished: 4}
	})
	var drags string
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			drags = args["drags"]
			// The refusal is the last one now, and the earlier drag finished during the wait.
			e.fake.with(func() {
				w.Drag = &hypr.Drag{ID: 6, Phase: "cancelled", Reason: "busy", Started: 5, Finished: 5, Cancelled: 1}
			})
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits + 1, "socketId": w.socketID(), "redrawObserved": true,
				"commitsSince": 1, "quietMs": 200, "elapsedMs": 900, "resizing": false})
		}
	})
	res := e.act(`{"window":"0xa1","shot":"` + shotID(t, e.screenshot(`{"window":"0xa1"}`)) + `","actions":[{"action":"drag","from":[10,10],"to":[200,100]}],"capture":"none"}`)
	if res.IsError || drags != "4" || get(t, res, "actions.drag_and_drop.outcome") != "cancelled" || get(t, res, "actions.drag_and_drop.reason") != "busy" ||
		get(t, res, "actions.drag_and_drop.count") != nil || !strings.HasPrefix(get(t, res, "summary").(string), "1/1 delivered · drag-and-drop cancelled to protect the user's input · ") ||
		get(t, res, "hint") != dragHint(&dragOut{Outcome: "cancelled", Reason: "busy"}, nil) {
		t.Fatalf("drags %q: %s", drags, text(res))
	}
}

// A socket that replaced the one whose state act read before the wait counts its drags from 1 again. With a commit
// baseline of 0, which counts from the new socket's start already, the wait is repeated with no drags known, so that a
// drag of the new socket keeps it going, and the result reports that drag.
func TestDragBaselineOfAReplacedSocket(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) {
		w.Socket, w.SocketID, w.Drag = true, "old", &hypr.Drag{ID: 7, Phase: "finished", Private: true, Started: 7, Finished: 7}
	})
	var drags []string
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			drags = append(drags, args["drags"])
			e.fake.with(func() {
				if w.SocketID == "old" {
					w.SocketID, w.Drag = "new", &hypr.Drag{ID: 1, Phase: "finished", Private: true, Started: 1, Finished: 1}
				}
			})
			return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": w.Commits, "socketId": "new", "redrawObserved": false,
				"commitsSince": 0, "quietMs": 200, "elapsedMs": 210, "resizing": false})
		}
	})
	res := e.act(`{"window":"0xa1","capture":"none"}`)
	if res.IsError || !slices.Equal(drags, []string{"7", "0"}) || get(t, res, "actions.drag_and_drop.outcome") != "finished" {
		t.Fatalf("drags %q: %s", drags, text(res))
	}
}
