package server

import (
	"context"
	"slices"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// dragged tells how the drag-and-drop operations went that the window's app started during a batch that presses a
// button, or that were going on before it, from the plugin's drag record before the input, in st, and now, of the
// socket that the wait read, waited. The wait does not end while one goes on, so one that has not ended by now is
// pending at the deadline. It is nil when the app started none, and for a plugin that does not count them.
func (s *Server) dragged(ctx context.Context, r *actRequest, st hypr.State, waited string) *dragOut {
	base, socket, first := dragBaseline(st), deref(st.SocketID), int64(0)
	if st.Drag != nil {
		first = st.Drag.ID
	}
	// A socket that replaced st's during the wait counts its drags from its start, during the call. Input of a batch
	// went to st's socket, so only a call without actions, which waited for those drags, reports them.
	replaced := base != nil && len(r.batch) == 0 && waited != "" && socket != "" && waited != socket
	if replaced {
		base, socket, first = ptr(int64(0)), waited, 0
	}
	if base == nil || !replaced && !st.Drag.Pending() && !slices.ContainsFunc(r.batch, func(a action) bool { return a.Kind == "click" || a.Kind == "drag" }) {
		return nil
	}
	cur, err := s.hypr.State(ctx, r.window)
	if err != nil || cur.StableID != st.StableID || deref(cur.SocketID) != socket || cur.Drag == nil || cur.Drag.ID <= *base {
		return nil
	}
	out := &dragOut{Outcome: cur.Drag.Phase, Private: cur.Drag.Private, Reason: cur.Drag.Reason}
	if cur.Drag.Pending() {
		out.Outcome, out.Phase = "pending", cur.Drag.Phase
	}
	// The ones that the app started during the call, which a drag still going on before it is not, and the drags of the
	// batch that started none, such as one whose app did not start its drag-and-drop in time.
	n := int(cur.Drag.ID - first)
	none := max(0, countKind(r.batch, "drag")-n)
	if (n > 1 || none > 0) && cur.Drag.Ended != nil {
		out.Count, out.None = n, none
		for _, e := range cur.Drag.Ended {
			if e.ID <= first || e.ID > cur.Drag.ID {
				continue
			}
			switch e.Phase {
			case "finished":
				out.Finished++
			case "cancelled":
				out.Cancelled++
			case "unconfirmed":
				out.Unconfirmed++
			}
			if e.Reason != "" && !slices.Contains(out.Reasons, e.Reason) {
				out.Reasons = append(out.Reasons, e.Reason)
			}
		}
		out.Pending = max(0, n-out.Finished-out.Cancelled-out.Unconfirmed)
		// Finished only when every drag of the batch did, and one outcome only when all of them had it: a mix, such as
		// an unconfirmed drop and a cancel, is partial, which keeps the uncertainty of the one that may have moved its
		// item.
		kinds := 0
		for _, k := range []int{out.Finished, out.Cancelled, out.Unconfirmed, out.Pending, none} {
			if k > 0 {
				kinds++
			}
		}
		if kinds > 1 {
			out.Outcome, out.Phase = "partial", ""
		}
	} else if n > 1 {
		out.Count = n
	}
	return out
}

// countKind counts the actions of a kind in the batch.
func countKind(batch []action, kind string) int {
	n := 0
	for _, a := range batch {
		if a.Kind == kind {
			n++
		}
	}
	return n
}

// dragBaseline is the id of the last drag-and-drop of the window's app in st that has ended, which a wait is told so
// that it waits through the later ones, or nil from a plugin that does not count them. A drag that was still going on,
// such as one that an earlier act left pending, counts as later, so that an act without actions waits for it and
// reports how it ended.
func dragBaseline(st hypr.State) *int64 {
	if st.Drag == nil {
		return nil
	}
	id := st.Drag.ID
	if st.Drag.Pending() {
		id--
	}
	return &id
}
