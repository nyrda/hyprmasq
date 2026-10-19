package server

import (
	"context"
	"errors"
	"fmt"
	"image"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A baseline is a count of the window's commits that a change is measured from.
type baseline struct {
	commits int64
	// The id of the socket it counts for, or "" from a plugin that names none.
	socket string
}

// currentBaseline is the counter of a state with an open socket.
func currentBaseline(st hypr.State) baseline {
	return baseline{*st.Commits, deref(st.SocketID)}
}

// shotBaseline measures a change since a shot. A shot from an earlier socket, which was closed and opened again, was
// taken before the current socket opened, so every commit the current one counted came after it.
func shotBaseline(sh *shot, st hypr.State) baseline {
	cur := currentBaseline(st)
	if sh.SocketID == "" || cur.socket == "" {
		// Without socket ids, a count above the counter can only be of an earlier socket.
		return baseline{min(sh.Commits, cur.commits), cur.socket}
	}
	if sh.SocketID != cur.socket {
		return baseline{0, cur.socket}
	}
	return baseline{sh.Commits, cur.socket}
}

// socketReplaced tells whether the wait read the counter of a socket that was opened after the baseline was read.
func socketReplaced(base baseline, reply hypr.WaitReply, err error) bool {
	var pe *hypr.Error
	if errors.As(err, &pe) {
		// The counter never falls, so a baseline above it was read from an earlier socket.
		return pe.Code == "invalid_arguments"
	}
	return err == nil && base.socket != "" && reply.SocketID != nil && *reply.SocketID != base.socket
}

// waitForWindow waits for the window to redraw and settle, until sendBy at the latest. A drag-and-drop after the one
// with the id drags keeps it from ending while it goes on, and its end ends the change phase as a redraw does.
func (s *Server) waitForWindow(ctx context.Context, r *actRequest, base baseline, drags *int64, sendBy time.Time) (*waitOut, error) {
	expect := 0
	if r.expectChange {
		expect = r.expectMs
	}
	out := &waitOut{expectMs: expect}
	var reply hypr.WaitReply
	var err error
	for {
		remaining := time.Until(sendBy).Milliseconds()
		if remaining < 1 {
			out.Ended, out.Summary, out.skipped = "timeout", waitSummary("timeout", false, false), true
			return out, nil
		}
		reply, err = s.wait(ctx, r.window, hypr.WaitArgs{Since: base.commits, ExpectMs: expect, QuietMs: r.quietMs, DeadlineMs: int(remaining), ID: s.waitID(), Drags: drags})
		// A counter of 0 counts from the new socket's start already, but a drag id may be the old socket's.
		if base.commits == 0 && (drags == nil || *drags == 0) || !socketReplaced(base, reply, err) || ctx.Err() != nil {
			break
		}
		// The new socket opened after the baseline was read, so every commit it counted came after it, and every drag.
		base = baseline{}
		if drags != nil {
			drags = ptr(int64(0))
		}
	}
	if err != nil {
		var pe *hypr.Error
		if errors.As(err, &pe) && pe.Code == "window_gone" {
			out.Ended = "window_closed"
			return out, nil
		}
		if ctx.Err() != nil {
			out.Ended = "cancelled"
			return out, nil
		}
		return nil, err
	}
	out = waitResult(reply)
	out.expectMs = expect
	// A reply that came after the cancellation, from a plugin that did not take the cancel in time, ended otherwise.
	if ctx.Err() != nil && out.Ended != "window_closed" {
		out.Ended, out.Summary = "cancelled", ""
	}
	return out, nil
}

// watchForRedraw goes on waiting for the window through the grace period after input that can open a window, until
// the time given. An app may draw again a moment after it settled, when work that does not draw ends: a browser the
// page that Enter loads, a mail client the results of a search it starts after a pause in typing. The window gets frame
// callbacks meanwhile, without which an app on a hidden workspace does not draw at all, and a redraw is waited out with
// the call's quiet time, so the capture shows the window after it. After a wait that saw no redraw, a redraw now makes
// it one that did; one that is still going on at the deadline makes it a timeout. It tells whether the window closed
// meanwhile, which keeps the wait as it ended, as a close after the wait does. A reply for another socket, which
// replaced the one the wait counted for, and other errors keep the wait as it ended too.
func (s *Server) watchForRedraw(ctx, capCtx context.Context, r *actRequest, w *waitOut, caps hypr.Capabilities, until, end time.Time) (closed bool) {
	// A deadline that leaves less than the grace period shortens the watch, keeping the quiet time for a redraw at its
	// end.
	budget := func() (deadline, expect int64) {
		deadline = time.Until(end.Add(-captureReserve - newWindowListing)).Milliseconds()
		return deadline, min(time.Until(until).Milliseconds(), deadline-int64(r.quietMs))
	}
	if _, expect := budget(); expect < 1 {
		return false
	}
	// A lease shorter than the wait and the watch would run out meanwhile, which ends the emulated focus, and with it
	// a menu that the input opened, before the capture. Its round trip, which a busy compositor stretches, comes out
	// of the watch.
	s.renewLease(capCtx, r.window, caps)
	deadline, expect := budget()
	if expect < 1 {
		return false
	}
	// A spinner or a blinking caret that was animating before the input does not count as the window drawing again.
	reply, err := s.wait(ctx, r.window, hypr.WaitArgs{Since: w.counter, ExpectMs: int(expect), QuietMs: r.quietMs, DeadlineMs: int(deadline), ID: s.waitID(),
		IgnoreAnimations: true})
	var pe *hypr.Error
	switch {
	case errors.As(err, &pe) && pe.Code == "window_gone", err == nil && reply.Ended == "window_closed":
		return true
	case ctx.Err() != nil:
		w.Ended, w.Summary = "cancelled", ""
		return false
	case err != nil, w.socket != "" && deref(reply.SocketID) != w.socket:
		return false
	}
	// Its commits count, also those of an animation, which end no change phase, and so do its animations, which the
	// capture leaves out of the changes.
	w.Commits += reply.CommitsSince
	w.animated = mergeAnimated(w.animated, reply.Animated)
	switch {
	case reply.Ended == "no_redraw":
		return false
	case w.Ended == "quiet" && w.RedrawObserved && reply.Ended == "quiet":
		w.Again = &waitAgainOut{QuietMs: reply.QuietMs, ElapsedMs: reply.ElapsedMs}
		return false
	}
	resizing := reply.Ended == "timeout" && reply.Resizing
	w.Ended, w.Summary, w.RedrawObserved, w.Resizing = reply.Ended, waitSummary(reply.Ended, true, resizing), true, resizing
	w.QuietMs, w.ElapsedMs = reply.QuietMs, w.ElapsedMs+reply.ElapsedMs
	return false
}

// mergeAnimated adds the animations of a second wait to those of the first, joining the boxes of one animation as the
// plugin does within a wait: boxes that overlap or touch, when the box around both is at most 1.5 times the larger. An
// animation that goes on through both waits is then counted once where its area weighs the changes.
func mergeAnimated(boxes, more []hypr.IntRect) []hypr.IntRect {
	size := func(r image.Rectangle) int { return r.Dx() * r.Dy() }
	for _, m := range more {
		box := rectOf(m)
		for joined := true; joined; {
			joined = false
			for i, b := range boxes {
				r := rectOf(b)
				touch := r.Min.X <= box.Max.X && box.Min.X <= r.Max.X && r.Min.Y <= box.Max.Y && box.Min.Y <= r.Max.Y
				if u := r.Union(box); touch && 2*size(u) <= 3*max(size(r), size(box)) {
					box, boxes, joined = u, append(boxes[:i:i], boxes[i+1:]...), true
					break
				}
			}
		}
		boxes = append(boxes, intRectOf(box))
	}
	return boxes
}

const (
	// How long after its deadline, or the call's, the reply of a wait may come before the server gives up on it.
	waitSlack = 500 * time.Millisecond
	// How long the reply of a cancelled wait may take after the server sent the first cancel, and how long it goes on
	// sending cancels for a wait that has not reached the plugin yet.
	cancelGrace  = 250 * time.Millisecond
	cancelBudget = time.Second
)

// wait runs the plugin's wait. When ctx ends, it cancels the wait by its id and reads the reply for cancelGrace more.
func (s *Server) wait(ctx context.Context, window string, a hypr.WaitArgs) (hypr.WaitReply, error) {
	end := time.Now().Add(time.Duration(a.DeadlineMs) * time.Millisecond)
	if deadline, ok := ctx.Deadline(); ok && deadline.Before(end) {
		end = deadline
	}
	reqCtx, giveUp := context.WithDeadline(context.WithoutCancel(ctx), end.Add(waitSlack))
	defer giveUp()
	// Closed once the plugin has answered, which ends its wait.
	answered := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		time.AfterFunc(cancelGrace, giveUp)
		// The wait may not have reached the plugin yet, so the cancel is repeated until it finds it, also after the reply
		// was given up, since such a wait would run to its deadline.
		budget, done := context.WithTimeout(context.WithoutCancel(ctx), cancelBudget)
		defer done()
		for {
			if found, _ := s.hypr.Cancel(budget, a.ID); found {
				return
			}
			select {
			case <-answered:
				return
			case <-budget.Done():
				return
			case <-time.After(50 * time.Millisecond):
			}
		}
	})
	defer stop()
	reply, err := s.hypr.Wait(reqCtx, window, a)
	if err == nil || reqCtx.Err() == nil {
		close(answered)
	}
	switch {
	case err == nil || reqCtx.Err() == nil:
		return reply, err
	case ctx.Err() != nil:
		return reply, ctx.Err()
	}
	return reply, &toolError{Code: "internal", Message: fmt.Sprintf("the plugin did not answer a wait within %d ms of its deadline", waitSlack.Milliseconds())}
}
