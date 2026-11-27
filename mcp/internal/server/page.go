package server

import (
	"context"
	"errors"
	"fmt"
	"image"
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

// firstViewRetakes is how many times a page scroll takes its first view again after the window drew, and how many
// times a step takes the view that it measures from again, see sameView.
const firstViewRetakes = 2

// errViewDrew keeps the input of a step from being sent, for another look at the window.
var errViewDrew = errors.New("the window drew after the view")

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
		// The window may draw between the view that this step measures from and its input. Asked right before the
		// input, the plugin tells what it drew: the counter alone does not tell a caret that blinks from new content.
		// While the step may take its view again, anything but animation holds the input back for that.
		var drift referenceDrift
		retakes := 0
		part.beforeInput = func(ctx context.Context, cur hypr.State) error {
			drift = s.driftSince(ctx, r.window, f, cur)
			if drift.drewMore() && retakes < firstViewRetakes {
				return errViewDrew
			}
			return nil
		}
		steps := tailSteps(part.batch, index, sh)
		enough := func() bool {
			return time.Until(sendBy) >= sendTime(steps)+time.Duration(r.quietMs)*time.Millisecond+splitCaptureBudget
		}
		if !enough() {
			page.Ended = "deadline"
			break
		}
		page.Moved = nil
		p.set("scrolling a page")
		wheelX, wheelY := wheelPoint(r.batch[index], sh)
		a, cur, err := s.send(ctx, sendCtx, &part, steps, sh, st, start, p, targetLocale{})
		late := false
		for errors.Is(err, errViewDrew) {
			// Nothing of the step has been sent. A view that cannot be taken again, or shows something else, ends the
			// retakes: the step goes out and the scroll stops after it, as the reference changed.
			if ctx.Err() != nil {
				return finish(errCancelled)
			}
			retakes++
			p.set("looking at the page again")
			if again, gray, known, same := s.sameView(ctx, sendCtx, r.window, cur, f, previous, previousWait.animatedBoxes(), wheelX, wheelY, sendBy); same {
				f, previous, previousWait = again, gray, &waitOut{animated: known}
			} else {
				retakes = firstViewRetakes
			}
			if late = !enough(); late {
				break
			}
			p.set("scrolling a page")
			a, cur, err = s.send(ctx, sendCtx, &part, steps, sh, cur, start, p, targetLocale{})
		}
		st = cur
		if late {
			page.Ended = "deadline"
			break
		}
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
		drifted, ok := drift.tolerated(f, cur, previousWait, wheelX, wheelY)
		if !ok {
			page.Ended = "reference_changed"
			break
		}
		if deref(f.reply.SocketID) != deref(cur.SocketID) || !f.detail.sameGeometry(next.detail) {
			page.Ended = "geometry_changed"
			break
		}

		// Animations draw on their own, also between the two views, so their pixels say nothing about the movement.
		animated := animationPixels(f.detail, wheelX, wheelY, drifted, previousWait.animatedBoxes(), wait.animatedBoxes())
		alignment := alignViews(previous, current, animated, func() bool { return sendCtx.Err() != nil || time.Until(sendBy) < splitCaptureBudget })
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

// referenceDrift is what the window drew between the view that a step of a page scroll measures from and the step's
// input, as the plugin told right before the input.
type referenceDrift struct {
	// The counter had moved, on the same socket, so the plugin was asked.
	asked bool
	drawn hypr.Drawn
	err   error
}

func (s *Server) driftSince(ctx context.Context, window string, f *frame, cur hypr.State) referenceDrift {
	if f.reply.Commits == nil || cur.Commits == nil || *cur.Commits == *f.reply.Commits || deref(cur.SocketID) != deref(f.reply.SocketID) {
		return referenceDrift{}
	}
	drawn, err := s.hypr.Drawn(ctx, window, *f.reply.Commits)
	return referenceDrift{asked: true, drawn: drawn, err: err}
}

// drewMore tells whether the plugin answered that the window drew anything but animation.
func (d referenceDrift) drewMore() bool {
	return d.asked && d.err == nil && !d.drawn.OnlyAnimation
}

// animationPixels are the boxes of animations, from lists in window-local logical pixels, in the pixels of a view.
// Those that hold the point where the wheel goes stay out: what the wheel acts on is never left out of a comparison.
func animationPixels(d *detail, x, y float64, lists ...[]hypr.IntRect) []image.Rectangle {
	var pixels []image.Rectangle
	for _, boxes := range lists {
		for _, box := range boxes {
			if px := d.pixelsOf(box); !px.Empty() && !holds(box, x, y) {
				pixels = append(pixels, px)
			}
		}
	}
	return pixels
}

// sameView takes the view that a step measures from again, after the window drew something in it that the plugin does
// not take for an animation, and nothing of the step has been sent. That is often the start of an animation: the first
// two frames of a caret's fade or of a scrollbar's, which the plugin takes for one from the third on. The wait lets
// the window draw until nothing but animation has come for a while, and tells the animations that it saw, which are
// known before the input and not from what the scroll sets off. The new view replaces the old one only when it shows
// the same outside the boxes of the animations known by then and the scrollbar's strip: new content elsewhere fails
// the comparison. It returns the view, its gray image and the animations known.
func (s *Server) sameView(ctx, sendCtx context.Context, window string, cur hypr.State, f *frame, previous grayImage, known []hypr.IntRect, x, y float64,
	sendBy time.Time) (*frame, grayImage, []hypr.IntRect, bool) {
	waitCtx, done := context.WithDeadline(ctx, sendBy)
	defer done()
	_, reply, err := s.catchUp(waitCtx, window, f.reply.Commits, false)
	if err != nil || ctx.Err() != nil {
		return nil, grayImage{}, nil, false
	}
	known = mergeAnimated(slices.Clone(known), reply.Animated)
	next, err := s.captureFrame(sendCtx, window, cur.StableID)
	if err != nil || deref(next.reply.SocketID) != deref(f.reply.SocketID) || !f.detail.sameGeometry(next.detail) {
		return nil, grayImage{}, nil, false
	}
	img, err := next.detail.image()
	if err != nil {
		return nil, grayImage{}, nil, false
	}
	gray := toGray(img)
	stop := func() bool { return sendCtx.Err() != nil || time.Until(sendBy) < splitCaptureBudget }
	if !alignViews(previous, gray, animationPixels(f.detail, x, y, known), stop).unchanged {
		return nil, grayImage{}, nil, false
	}
	return next, gray, known, true
}

// tolerated tells whether the step may measure from its view f although the window drew after it, before the input
// that cur was read for. It may when every commit since was part of an animation: the plugin tells that by what each
// commit damaged, as it came, from the commits before it alone. So the animations that the scroll itself starts, as an
// overlay scrollbar that shows, excuse nothing that came before it. The boxes that those commits damaged come back:
// outside them the window still showed f when the input went out, and the alignment leaves them out. A change within
// such a box is not told from the animation, which is why none of them may hold the point that the wheel goes to, where a
// change could be of what takes the wheel.
//
// A plugin without the command leaves the rule from before it: a counter that moved stops the scroll unless every
// animation known before the step lies in the scrollbar's strip.
func (d referenceDrift) tolerated(f *frame, cur hypr.State, known *waitOut, x, y float64) ([]hypr.IntRect, bool) {
	if *f.reply.Commits == *cur.Commits {
		return nil, true
	}
	var pe *hypr.Error
	switch {
	case !d.asked, errors.As(d.err, &pe) && pe.Code == "invalid_arguments":
		return nil, onlyScrollbarAnimation(known, f.detail)
	case d.err != nil, !d.drawn.OnlyAnimation, deref(d.drawn.SocketID) != deref(f.reply.SocketID):
		return nil, false
	}
	for _, box := range d.drawn.Animated {
		if holds(box, x, y) {
			return nil, false
		}
	}
	return d.drawn.Animated, true
}

// wheelPoint is where a scroll action sends the wheel, in window-local logical pixels.
func wheelPoint(a action, sh *shot) (x, y float64) {
	if sh == nil {
		return a.X, a.Y
	}
	return sh.toLogical(a.X, a.Y)
}

// holds tells whether the box, or the logical pixel around it that its edge can reach into, holds the point.
func holds(box hypr.IntRect, x, y float64) bool {
	return x >= float64(box.X-1) && x < float64(box.X+box.Width+1) && y >= float64(box.Y-1) && y < float64(box.Y+box.Height+1)
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
