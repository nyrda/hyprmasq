package server

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A batch that ends with scrolling after input that can change what the window shows runs in two parts, so that the
// agent sees what that input did before the page moves on: act sends the actions before the scrolls, waits for the
// window as it does after a batch, and captures what changed since the shot the call started from. Then it sends the
// scrolls in a sender of their own, waits again, and captures what changed since that capture, which comes as a full
// shot once the page has moved, as capture "changes" does after a scroll, and otherwise shows what the scroll did
// change, such as the value of a dropdown that took the wheel. The scrolls are held back, and the result says why,
// unless the first part was delivered whole, settled, opened no window and was captured, and time is left for them.
// Both parts hold the window's lock, and share the call's deadline.
const (
	// What the capture of the first part gets of the time to input, and the least that the wait for it gets beyond the
	// quiet time.
	splitCaptureBudget = time.Second
	splitWaitMin       = 300 * time.Millisecond
)

// beforeScrollOut is what the actions before the scrolls of a split batch did: the wait for them and the shot of what
// they changed.
type beforeScrollOut struct {
	Wait *waitOut `json:"wait"`
	Shot *shotOut `json:"shot"`
}

// scrollSplitAt is the index of the first of the scroll actions that end the batch, when act sends the actions before
// them first, or -1 when the batch runs as one: without capture "changes", without a click, pressing path, key or text
// before the scrolls, with a scroll among them, or without the time to input for both parts and a wait and a capture
// between them.
func scrollSplitAt(r *actRequest, sh *shot, until time.Time) int {
	k := len(r.batch)
	for k > 0 && r.batch[k-1].Kind == "scroll" {
		k--
	}
	if r.captureMode != "changes" && !endsWithPage(r) || k == len(r.batch) || !changesInput(r.batch[:k]) && !endsWithPage(r) || slices.ContainsFunc(r.batch[:k], func(a action) bool { return a.Kind == "scroll" }) {
		return -1
	}
	if endsWithPage(r) {
		return k
	}
	need := sendTime(translateBatch(r.batch[:k], sh)) + time.Duration(r.quietMs)*time.Millisecond + splitWaitMin + splitCaptureBudget +
		sendTime(translateBatch(r.batch[k:], sh))
	if time.Until(until) < need {
		return -1
	}
	return k
}

// changesInput tells whether the actions include input that can change what the window shows: a click, a path that
// holds a button, a key or text. Motion, as for hover along a path without a button, and pauses before a scroll
// run with it.
func changesInput(batch []action) bool {
	for _, a := range batch {
		switch {
		case a.Kind == "click" || a.Kind == "key" || a.Kind == "type" || a.holdsButton():
			return true
		}
	}
	return false
}

// tailSteps are the sender's steps of the scrolls from index k, named by their index in the batch.
func tailSteps(batch []action, k int, sh *shot) []inputStep {
	steps := translateBatch(batch[k:], sh)
	for i := range steps {
		steps[i].Batch += k
	}
	return steps
}

// splitCall is what act knows when it sends a split batch.
type splitCall struct {
	r        *actRequest
	at       int
	out      *actOut
	sh       *shot
	st       hypr.State
	start    time.Time
	end      time.Time
	sendBy   time.Time
	region   *zoom
	before   []hypr.Window
	locale   targetLocale
	p        *progress
	failShot func(error) *mcp.CallToolResult
}

// actSplit sends a batch that ends with scrolling in two parts, see scrollSplitAt, and returns the call's result.
func (s *Server) actSplit(ctx, sendCtx context.Context, c splitCall) *mcp.CallToolResult {
	r, out, k := c.r, c.out, c.at
	head := *r
	head.batch = r.batch[:k]
	actions, cur, err := s.send(ctx, sendCtx, &head, translateBatch(head.batch, c.sh), c.sh, c.st, c.start, c.p, c.locale)
	actions.Total = len(r.batch)
	out.Actions = actions
	applied := actions.Delivery != "none"
	// The scrolls of a call that ends before they go out, after the actions before them did, are held back.
	unsent := func(reason string) {
		if actions.Done == k && actions.Delivery == "complete" {
			out.Actions = heldBack(actions, k, reason)
		}
	}
	if errors.Is(err, errCancelled) {
		out.Wait = &waitOut{Ended: "cancelled"}
		unsent("the call was cancelled before it")
		return s.finish(ctx, r, out, cur, c.end, c.region, applied, c.before)
	}
	if err != nil {
		te := asToolError(err)
		if !applied {
			return c.failShot(te)
		}
		if te.Code == "window_gone" {
			out.Wait = &waitOut{Ended: "window_closed"}
			unsent("the window closed")
		}
		return withError(s.finish(ctx, r, out, cur, c.end, c.region, applied, c.before), te, out, r.batch)
	}
	// A press that was held back holds back the scrolls after it too: the result shows the window after the input before.
	if actions.heldPress {
		c.p.set("waiting for the window to settle")
		wait, err := s.waitForWindow(ctx, r, currentBaseline(cur), dragBaseline(cur), c.sendBy)
		if err != nil {
			return withError(s.finish(ctx, r, out, cur, c.end, c.region, applied, c.before), asToolError(err), out, r.batch)
		}
		out.Wait = wait
		return s.finish(ctx, r, out, cur, c.end, c.region, applied, c.before)
	}
	// The state just before the input, which the result's checks of the whole batch count from.
	st := cur
	inputBy, _ := sendCtx.Deadline()
	tail := tailSteps(r.batch, k, c.sh)

	c.p.set("waiting for the window to settle")
	wait, err := s.waitForWindow(ctx, r, currentBaseline(cur), dragBaseline(cur), inputBy.Add(-sendTime(tail)-splitCaptureBudget))
	if err != nil {
		unsent("the wait for the actions before it failed")
		return withError(s.finish(ctx, r, out, st, c.end, c.region, applied, c.before), asToolError(err), out, r.batch)
	}
	out.Wait = wait
	switch wait.Ended {
	case "cancelled":
		unsent("the call was cancelled before it")
		return s.finish(ctx, r, out, st, c.end, c.region, applied, c.before)
	case "window_closed":
		unsent("the window closed")
		return s.finish(ctx, r, out, st, c.end, c.region, applied, c.before)
	}
	reason := s.holdsScrollBack(ctx, r, wait, st, c.before)
	var shot *shot
	var images [][]byte
	if reason == "" {
		c.p.set("capturing the changes before the scroll")
		headOut := &actOut{Wait: wait}
		captureRequest := *r
		captureRequest.joinChanges = endsWithPage(r)
		capCtx, cancel := context.WithDeadline(ctx, inputBy.Add(-sendTime(tail)))
		shot, images, err = s.captureChanges(capCtx, &captureRequest, headOut, st.StableID)
		cancel()
		switch {
		case err != nil:
			reason = "the window could not be captured after the actions before it"
		case shot.Resizing || shot.Starved != nil:
			reason = "the window had not drawn the actions before it when it was captured"
		default:
			out.BeforeScroll = &beforeScrollOut{Wait: wait, Shot: headOut.Shot}
		}
	}
	if reason != "" {
		out.Actions = heldBack(actions, k, reason)
		return s.finish(ctx, r, out, st, c.end, c.region, applied, c.before)
	}

	// The scrolls, whose changes count from that capture.
	scroll := *r
	scroll.from = k
	c.p.set("sending the scroll")
	var scrolled *actionsOut
	var pageWait *waitOut
	if endsWithPage(r) {
		scrolled, cur, pageWait, out.Scroll, err = s.sendPage(ctx, sendCtx, &scroll, c.sh, st, c.start, c.sendBy, c.p, wait)
		r.captureMode = "window"
	} else {
		scrolled, cur, err = s.send(ctx, sendCtx, &scroll, tail, c.sh, st, c.start, c.p, targetLocale{})
	}
	scrolled.composeNote = actions.composeNote
	if scrolled.Delivery == "none" {
		if errors.Is(err, errCancelled) {
			out.Wait, out.BeforeScroll = &waitOut{Ended: "cancelled"}, nil
			unsent("the call was cancelled before it")
			return s.finish(ctx, r, out, st, c.end, c.region, applied, c.before)
		}
		// Nothing of the scrolls went out: as for a scroll held back, the result shows the window from the call's shot.
		why := "too little time was left for it"
		if err != nil {
			if te := asToolError(err); te.Code != "deadline" {
				why = "it could not be sent: " + te.Message
			}
		}
		out.BeforeScroll = nil
		out.Actions = heldBack(actions, k, why)
		return s.finish(ctx, r, out, st, c.end, c.region, applied, c.before)
	}
	s.shots.returned(shot)
	shot.Animated = mergeAnimated(slices.Clone(shot.Animated), wait.animatedBoxes())
	for _, a := range wait.animated {
		if box, ok := shot.pixelBox(a); ok {
			wait.Animated = append(wait.Animated, box)
		}
	}
	r.base, r.baseReason = shot, ""
	// From here on, the result's wait is the scroll's: the first part's is in before_scroll.
	out.Actions, out.beforeImages, out.Wait = scrolled, images, nil
	if err != nil {
		te := asToolError(err)
		if te.Code == "window_gone" {
			out.Wait = &waitOut{Ended: "window_closed"}
		}
		return withError(s.finish(ctx, r, out, st, c.end, c.region, true, c.before), te, out, r.batch)
	}
	c.p.set("waiting for the window to settle")
	if pageWait != nil {
		wait = pageWait
	} else {
		wait, err = s.waitForWindow(ctx, r, currentBaseline(cur), dragBaseline(st), c.sendBy)
	}
	if err != nil {
		return withError(s.finish(ctx, r, out, st, c.end, c.region, true, c.before), asToolError(err), out, r.batch)
	}
	out.Wait = wait
	c.p.set("capturing")
	return s.finish(ctx, r, out, st, c.end, c.region, true, c.before)
}

// holdsScrollBack tells why the scrolls of a split batch are not sent after the wait for the actions before them, or
// "": the window did not settle, drew nothing, or opened a window, which a scroll would leave behind. st is the state
// before the input.
func (s *Server) holdsScrollBack(ctx context.Context, r *actRequest, wait *waitOut, st hypr.State, before []hypr.Window) string {
	switch wait.Ended {
	case "quiet":
	case "no_redraw":
		return "the actions before it drew nothing, so they may have missed"
	default:
		return "the window had not settled after the actions before it"
	}
	after, err := s.listWindows(ctx)
	if err != nil {
		return "the windows could not be listed to check that the actions before it opened none"
	}
	for _, w := range s.newWindows(ctx, before, after, r.window, windowPID(before, r.window), r.launch) {
		if w.fromTarget {
			return fmt.Sprintf("the actions before it opened window %s", w.Window)
		}
	}
	return ""
}

// heldBack counts the actions before the scrolls at k as delivered, and the scrolls as held back for the reason.
func heldBack(a *actionsOut, k int, reason string) *actionsOut {
	a.Done, a.Delivery, a.FailedIndex, a.HeldBack = k, "partial", ptr(k), reason
	return a
}

// splitHint tells what the two parts of a split batch's result show, and where the next coordinates come from, once the
// scroll has moved the page: then the shot after it is a full one.
func splitHint(out *actOut) string {
	before, after := out.BeforeScroll, out.Shot
	if before == nil || before.Shot == nil || after == nil || after.Changes != nil {
		return ""
	}
	what := fmt.Sprintf("The zooms of %s show what the actions before the scroll changed.", before.Shot.ID)
	switch {
	case before.Shot.Changes == nil:
		what = fmt.Sprintf("Shot %s shows the window after the actions before the scroll.", before.Shot.ID)
	case len(before.Shot.Changes.Regions) == 0:
		what = fmt.Sprintf("The actions before the scroll changed nothing visible since %s.", before.Shot.Changes.Base)
	}
	return fmt.Sprintf("%s Shot %s shows the window after the scroll: read the next coordinates from %s.", what, after.ID, after.ID)
}

// heldBackHint tells that the scrolls at the end of the batch, or a press and the actions after it, were not sent, and
// why.
func heldBackHint(a *actionsOut, batch []action) string {
	if a == nil || a.HeldBack == "" {
		return ""
	}
	if a.heldPress && a.FailedIndex != nil && *a.FailedIndex < len(batch) {
		return fmt.Sprintf("%s and the actions after it were not sent: %s. Look at the shot: act in the popup, or close it, for example with Escape, "+
			"and send what is left again.", describe(*a.FailedIndex, batch[*a.FailedIndex]), a.HeldBack)
	}
	return "The scroll was not sent: " + a.HeldBack + ". The page has not moved: look at the result, and scroll in an act of its own if you still want to."
}
