package server

import (
	"context"
	"errors"
	"fmt"
	"math"
	"slices"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A page scroll measures wheel movement from images. It stops before sending another
// step when the images cannot be aligned, input fails, or the window stops settling.
// Neither the application nor the contents of the page supply a scroll distance.
type pageScrollOut struct {
	Notches  int    `json:"notches"`
	Moved    *int   `json:"moved_px,omitempty"`
	Viewport int    `json:"viewport_px,omitempty"`
	Ended    string `json:"ended"`
}

func (p *pageScrollOut) text() string {
	if p.Ended == "page" {
		return fmt.Sprintf("Page scroll moved the view %d logical px, leaving overlap with the previous view.", *p.Moved)
	}
	return fmt.Sprintf("Page scroll stopped after %d wheel notches (%s). Read the returned view before continuing.", p.Notches, p.Ended)
}

func endsWithPage(r *actRequest) bool {
	return len(r.batch) > 0 && r.batch[len(r.batch)-1].Unit == "page"
}

func (s *Server) actPage(ctx, sendCtx context.Context, r *actRequest, out *actOut, sh *shot, st hypr.State, start, end, sendBy time.Time, p *progress, before []hypr.Window) *mcp.CallToolResult {
	var observed *waitOut
	if sh != nil {
		observed = &waitOut{animated: slices.Clone(sh.Animated)}
	}
	a, cur, wait, page, err := s.sendPage(ctx, sendCtx, r, sh, st, start, sendBy, p, observed, true)
	out.Actions, out.Wait, out.Scroll = a, wait, page
	r.captureMode = "window"
	result := s.finish(ctx, r, out, cur, end, nil, a.Delivery != "none", before)
	if err != nil {
		return withError(result, asToolError(err), out, r.batch)
	}
	return result
}

// firstViewRetakes is how many times a page scroll takes its first view again after the window drew.
const firstViewRetakes = 2

// settledFirstView takes the first view of a page scroll, of a window that no input in this call has made draw, and
// returns it with the animations that it saw. Nothing has been sent yet, so a frame that comes after the view costs only
// another view. It comes often: a hidden window gets no frame callbacks some time after the last input, and an
// application or XWayland then holds a new frame back for one, which its state does not tell. Rendering the window for
// the view sends it that callback, and the frame arrives right after the view, as a change that the scroll cannot tell
// from the window's own. Frame callbacks can also start an animation that was waiting for them, as GTK 4's overlay
// scrollbar fades out then. A wait sends frame callbacks, shows whether the window drew, and ends while an animation
// goes on, whose boxes it tells. A window that draws anything else all along gets its last view anyway, and stops the
// scroll.
func (s *Server) settledFirstView(ctx, sendCtx context.Context, window string, st hypr.State, sendBy time.Time) (*frame, []hypr.IntRect, error) {
	waitCtx, done := context.WithDeadline(ctx, sendBy)
	defer done()
	var animated []hypr.IntRect
	settle := func(commits *int64, starved bool) (drew bool, err error) {
		_, reply, err := s.catchUp(waitCtx, window, commits, starved)
		switch {
		case ctx.Err() != nil:
			return false, errCancelled
		case errors.Is(err, context.DeadlineExceeded):
			// The scroll reports the deadline.
			return false, nil
		case err != nil:
			return false, err
		}
		animated = mergeAnimated(animated, reply.Animated)
		return reply.RedrawObserved, nil
	}
	f, err := s.captureFrame(sendCtx, window, st.StableID)
	if errors.Is(err, errStarved) {
		if _, err := settle(st.Commits, true); err != nil {
			return nil, nil, err
		}
		f, err = s.captureFrame(sendCtx, window, st.StableID)
		if err != nil {
			return nil, nil, err
		}
	} else if err != nil {
		return nil, nil, err
	}
	for retake := 0; retake < firstViewRetakes; retake++ {
		drew, err := settle(f.reply.Commits, false)
		if err != nil {
			return nil, nil, err
		}
		if !drew {
			break
		}
		next, err := s.captureFrame(sendCtx, window, st.StableID)
		// A window that cannot be taken again now keeps the view it has, which the scroll then finds changed.
		if errors.Is(err, errStarved) || errors.Is(err, errNotSettled) {
			break
		}
		if err != nil {
			return nil, nil, err
		}
		f = next
	}
	return f, animated, nil
}

func (s *Server) sendPage(ctx, sendCtx context.Context, r *actRequest, sh *shot, st hypr.State, start, sendBy time.Time, p *progress, observed *waitOut, settle bool) (*actionsOut, hypr.State, *waitOut, *pageScrollOut, error) {
	index := len(r.batch) - 1
	page := &pageScrollOut{Ended: "step_limit"}
	actions := &actionsOut{Done: r.from, Total: len(r.batch), Delivery: "none"}
	var wait *waitOut
	scale := 1.0
	finish := func(err error) (*actionsOut, hypr.State, *waitOut, *pageScrollOut, error) {
		if page.Moved != nil {
			page.Moved = ptr(int(math.Round(float64(*page.Moved) / scale)))
		}
		page.Viewport = int(math.Round(float64(page.Viewport) / scale))
		return actions, st, wait, page, err
	}
	var f *frame
	var err error
	if settle {
		var animated []hypr.IntRect
		f, animated, err = s.settledFirstView(ctx, sendCtx, r.window, st, sendBy)
		if len(animated) > 0 {
			observed = &waitOut{animated: mergeAnimated(slices.Clone(observed.animatedBoxes()), animated)}
		}
	} else {
		// The input of the actions before the scroll got the frame callbacks going, and the wait for it ended quiet.
		f, err = s.captureFrame(sendCtx, r.window, st.StableID)
	}
	if err != nil {
		return finish(err)
	}
	img, err := f.detail.image()
	if err != nil {
		return finish(err)
	}
	scale = f.detail.Scale
	previous := toGray(img)
	previousWait := observed
	direction := int(r.batch[index].DY)
	total, rate, target := 0, 0.0, 0
	for round := 0; round < 6; round++ {
		if ctx.Err() != nil {
			return finish(errCancelled)
		}
		n := 1
		if rate > 0 {
			n = int(math.Floor(float64(target-total) / rate))
			// Each measurement still shares most of the last view. This also
			// gives changing step sizes a chance to stop the loop early.
			n = min(n, maxNotches, max(1, int(float64(page.Viewport)*0.45/rate)))
			if n < 1 {
				page.Ended = "page"
				break
			}
		}
		part := *r
		part.batch = append([]action(nil), r.batch...)
		part.batch[index].Unit, part.batch[index].DY = "notch", float64(direction*n)
		part.from = index
		steps := tailSteps(part.batch, index, sh)
		if time.Until(sendBy) < sendTime(steps)+time.Duration(r.quietMs)*time.Millisecond+splitCaptureBudget {
			page.Ended = "deadline"
			break
		}
		page.Moved = nil
		p.set("scrolling a page")
		a, cur, err := s.send(ctx, sendCtx, &part, steps, sh, st, start, p, targetLocale{})
		st = cur
		if a.Delivery != "none" {
			actions = a
		}
		if a.Delivery == "complete" {
			page.Notches += n
		}
		if err != nil {
			if page.Notches > 0 {
				actions.Done, actions.Delivery, actions.FailedIndex = index, "partial", ptr(index)
			}
			return finish(err)
		}
		wait, err = s.waitForWindow(ctx, r, currentBaseline(cur), dragBaseline(cur), sendBy)
		if err != nil {
			return finish(err)
		}
		if wait.Ended != "quiet" && wait.Ended != "no_redraw" {
			page.Ended = wait.Ended
			break
		}
		next, err := s.captureFrame(sendCtx, r.window, st.StableID)
		if err != nil {
			return finish(err)
		}
		img, err = next.detail.image()
		if err != nil {
			return finish(err)
		}
		current := toGray(img)
		if f.reply.Commits == nil || cur.Commits == nil {
			page.Ended = "missing_frame_counter"
			break
		}
		if *f.reply.Commits != *cur.Commits && !onlyScrollbarAnimation(previousWait, f.detail) {
			page.Ended = "reference_changed"
			break
		}
		if deref(f.reply.SocketID) != deref(cur.SocketID) || !f.detail.sameGeometry(next.detail) {
			page.Ended = "geometry_changed"
			break
		}

		alignment := alignViews(previous, current, func() bool { return sendCtx.Err() != nil || time.Until(sendBy) < splitCaptureBudget })
		moved := alignment.shift * direction
		if !alignment.sure {
			page.Ended = "ambiguous"
			break
		}
		if moved < 0 {
			page.Ended = "opposite_movement"
			break
		}
		if moved == 0 {
			page.Moved = ptr(total * direction)
			page.Ended = "no_movement"
			break
		}
		viewport := alignment.bottom - alignment.top
		if viewport < current.h*6/10 || moved > viewport/2 {
			page.Ended = "partial_view_or_large_step"
			break
		}
		page.Viewport = viewport
		target = int(float64(viewport) * 0.85)
		total += moved
		page.Moved = ptr(total * direction)
		observed := float64(moved) / float64(n)
		if rate > 0 && observed < rate*0.9 {
			page.Ended = "movement_shortened"
			break
		}
		rate = max(rate, observed)
		if total >= target || target-total < int(math.Ceil(rate)) {
			page.Ended = "page"
			break
		}
		previous, f, previousWait = current, next, wait
	}
	return finish(nil)
}

// Overlay scrollbars can keep committing after a settled view. The image
// alignment already excludes their strip; their known animation need not
// invalidate the last observation of the content.
func onlyScrollbarAnimation(wait *waitOut, d *detail) bool {
	if wait == nil || len(wait.animated) == 0 {
		return false
	}
	for _, a := range wait.animated {
		if float64(a.X) < d.Frame.X+d.Frame.Width-float64(scrollStripPx)/d.Scale || float64(a.X+a.Width) > d.Frame.X+d.Frame.Width {
			return false
		}
	}
	return true
}
