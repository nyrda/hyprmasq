package server

import (
	"context"
	"errors"
	"fmt"
	"image"
	"image/color"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/delta"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A page can move its content after a shot without the window being resized, as Forgejo's upload form moves down when
// the file that a file chooser gave it lands in the list above it, and a click at the shot's coordinates then reaches
// another field. So before act sends a batch that clicks or drags at the coordinates of a shot, it checks that the
// window still shows there what the shot showed: at once from the commit counter, which has not moved when the window
// drew nothing since the shot, and otherwise by comparing a new capture with the shot around each point, as capture
// "changes" compares, with its thresholds, without tiny changes such as a caret, and without the parts of the
// animations that the wait before the shot saw that keep changing, and for an edge that moved across the box.
const (
	// The check compares the box that reaches this many logical pixels around each point, the margin that the zooms of
	// capture "changes" give a change. Its 49 pixels cover a text field or button of up to 48 pixels in height, such as
	// Forgejo's of 38, when the click is in its middle: any edge that a move carries across the point lies within half of
	// the move from it, so a move of up to 48 pixels changes the box also between plain borders, and a longer one brings
	// other content there. It stays clear of most of what changes next to a target, such as a field above a button.
	targetMargin = 24
	// After a change around a target, the window gets up to movedSettleMax with frame callbacks to finish drawing, until
	// it has been quiet for movedQuietMs, and is captured again. A change that holds still is a move; one that keeps
	// changing, such as a video, which the plugin does not take for an animation when it is large, would never match a
	// shot, so its click is sent. The times are those that act waits for the window to answer a click: an animation of
	// the page draws every 16 ms, and a page that a link loaded is quiet within a second.
	movedSettleMax = answerMaxMs * time.Millisecond
	movedQuietMs   = 200
	// The least time that the wait leaves for the capture after it, for a capture that a busy compositor slows down.
	movedCaptureMin = 250 * time.Millisecond
	// How often the check runs again when the window drew after the capture that it decided on.
	movedRechecks = 1
)

// A target is a point where a batch clicks or drags, and the box around it, in window-local logical pixels, that the
// check compares.
type target struct {
	index int
	point namedPoint
	box   hypr.IntRect
}

// targetsOf returns the points of the batch's clicks and drags. Motion and scrolling go out at the shot's coordinates
// too, but hover only shows something, and a scroll at a moved point scrolls what lies there, which its result shows.
func targetsOf(batch []action, sh *shot) []target {
	var out []target
	for i := range batch {
		a := &batch[i]
		if a.Kind != "click" && a.Kind != "drag" {
			continue
		}
		for _, pt := range a.points() {
			x, y := sh.toLogical(pt.X, pt.Y)
			cx, cy := int(math.Floor(x)), int(math.Floor(y))
			out = append(out, target{index: i, point: pt, box: hypr.IntRect{X: cx - targetMargin, Y: cy - targetMargin, Width: 2*targetMargin + 1, Height: 2*targetMargin + 1}})
		}
	}
	return out
}

// drewSince tells whether the window may have drawn since the shot: its counter moved, or counts for another socket.
func drewSince(sh *shot, st hypr.State) bool {
	return st.Commits == nil || *st.Commits != sh.Commits || deref(st.SocketID) != sh.SocketID
}

// A view is an image of the window that the check compares, with how its pixels lie in the window, and the window's
// commit counter when it was rendered.
type view struct {
	img image.Image
	geo *detail
	// The window's counter when the view was rendered, and the socket it counts for.
	at baseline
}

// readView decodes the reference image of a shot.
func readView(ref *detail) (view, error) {
	img, err := readPNG(ref.Path)
	return view{img: img, geo: ref}, err
}

// captureView renders the window with the request that rendered ref, so that a window that did not change gives the
// same pixels.
func (s *Server) captureView(ctx context.Context, window string, ref *detail) (view, error) {
	if err := os.MkdirAll(s.shotDir, 0o700); err != nil {
		return view{}, err
	}
	path := filepath.Join(s.shotDir, fmt.Sprintf("t%d.png", s.detailSeq.Add(1)))
	defer os.Remove(path)
	reply, err := s.hypr.Screenshot(ctx, window, path, ref.Region, ref.Max)
	if err != nil {
		return view{}, err
	}
	img, err := readPNG(path)
	if err != nil {
		return view{}, err
	}
	v := view{img: img, geo: detailOf(reply, path, ref.Max), at: baseline{socket: deref(reply.SocketID)}}
	if reply.Commits != nil {
		v.at.commits = *reply.Commits
	}
	return v, nil
}

// alignment returns the shift by which a pixel of a lies at the same place of the window as the pixel that far further
// in b, and false when the views do not lie pixel on pixel.
func alignment(a, b view) (image.Point, bool) {
	scale := a.geo.Scale
	ox, oy := (a.geo.Frame.X-b.geo.Frame.X)*scale, (a.geo.Frame.Y-b.geo.Frame.Y)*scale
	if math.Abs(scale-b.geo.Scale) > 1e-6 || math.Abs(ox-math.Round(ox)) > 1e-3 || math.Abs(oy-math.Round(oy)) > 1e-3 {
		return image.Point{}, false
	}
	return image.Pt(int(math.Round(ox)), int(math.Round(oy))), true
}

// changingParts returns where two views of the window differ within the boxes of the animations, as capture "changes"
// finds changes, without tiny ones such as a caret that blinked, in whole window-local logical pixels: the parts of
// the animations that the window changed between the views. Views that do not lie pixel on pixel give none.
func changingParts(a, b view, animated []hypr.IntRect) []hypr.IntRect {
	shift, ok := alignment(a, b)
	if !ok {
		return nil
	}
	var parts []hypr.IntRect
	for _, box := range animated {
		inA := a.geo.pixelsOf(box).Intersect(a.img.Bounds().Sub(a.img.Bounds().Min))
		if inA.Empty() || !inA.Add(shift).In(b.img.Bounds().Sub(b.img.Bounds().Min)) {
			continue
		}
		ca, okA := crop(a.img, inA)
		cb, okB := crop(b.img, inA.Add(shift))
		if !okA || !okB {
			continue
		}
		for _, c := range delta.Compare(ca, cb, deltaOptions(a.geo.Scale, 0)).Changes {
			parts = append(parts, a.geo.geometry().logicalBox(c.Sub(a.img.Bounds().Min)))
		}
	}
	return parts
}

// changedAt tells whether the box, in window-local logical pixels, differs between two views beyond what capture
// "changes" leaves out: changes below its thresholds, tiny changes such as a caret, and changes within the animations
// where the window kept changing (see withoutAnimations). An edge that moved across the box counts too, outside those
// parts (see edgeMoved). Views that do not lie pixel on pixel, such as after a popup past the window's edge opened at a
// scale that the box sets, cannot be compared, and count as changed, and so do views that show different parts of the
// box, as when a popup past the window's edge that the box reaches into opened or closed.
func changedAt(a, b view, box hypr.IntRect, animated, changing []hypr.IntRect) bool {
	shift, ok := alignment(a, b)
	if !ok {
		return true
	}
	inA := a.geo.pixelsOf(box).Intersect(a.img.Bounds().Sub(a.img.Bounds().Min))
	if inB := b.geo.pixelsOf(box).Intersect(b.img.Bounds().Sub(b.img.Bounds().Min)); inB != inA.Add(shift) {
		return !inA.Empty() || !inB.Empty()
	}
	if inA.Empty() {
		return false
	}
	ca, okA := crop(a.img, inA)
	cb, okB := crop(b.img, inA.Add(shift))
	if !okA || !okB {
		return true
	}
	o := deltaOptions(a.geo.Scale, 0)
	res, _ := withoutAnimations(ca, cb, delta.Compare(ca, cb, o), o, a.geo.geometry(), animated, changing)
	return len(res.Changes) > 0 || edgeMoved(a, b, inA, shift, changing)
}

// The share of a row or column of a target's box whose pixels have to change for an edge to have moved across it.
const edgeShare = 0.9

// edgeMoved tells whether a row or column of the box, inA in pixels of a, changed in nearly all of its pixels between
// the views, each by more than the luminance threshold of capture "changes", outside the parts of animations that kept
// changing (see changingParts). That is an edge that moved across the box, and no caret, whose line is short, or text,
// whose pixels fill no row. Around a click in an empty field, the field's faint border of a pixel is all that changes
// when the field moves away, and the 3×3 average of the comparison dilutes it below the threshold: in the benchmark VM,
// Forgejo's commit field, whose border is 33 levels darker than the white inside, moved away from a click in its middle
// unnoticed, and the click opened the file chooser of the upload box that had moved there.
func edgeMoved(a, b view, inA image.Rectangle, shift image.Point, changing []hypr.IntRect) bool {
	var skip []image.Rectangle
	for _, box := range changing {
		box.X, box.Y, box.Width, box.Height = box.X-1, box.Y-1, box.Width+2, box.Height+2
		skip = append(skip, a.geo.pixelsOf(box))
	}
	// The pixels of every row, then of every column, that are compared, and those of them that changed.
	w, h := inA.Dx(), inA.Dy()
	counted, changed := make([]int, h+w), make([]int, h+w)
	minA, minB := a.img.Bounds().Min, b.img.Bounds().Min
	for y := 0; y < h; y++ {
		for x := 0; x < w; x++ {
			p := inA.Min.Add(image.Pt(x, y))
			if slices.ContainsFunc(skip, p.In) {
				continue
			}
			counted[y]++
			counted[h+x]++
			if d := luminance(a.img, p.Add(minA)) - luminance(b.img, p.Add(shift).Add(minB)); d > changeThreshold || -d > changeThreshold {
				changed[y]++
				changed[h+x]++
			}
		}
	}
	for i, n := range changed {
		length := w
		if i >= h {
			length = h
		}
		// A line that animations cover for the most part says nothing.
		if 2*counted[i] >= length && float64(n) >= edgeShare*float64(counted[i]) {
			return true
		}
	}
	return false
}

// luminance is the luminance of a pixel with the weights of the delta package.
func luminance(img image.Image, p image.Point) int {
	c := color.NRGBAModel.Convert(img.At(p.X, p.Y)).(color.NRGBA)
	return int((54*uint32(c.R) + 183*uint32(c.G) + 19*uint32(c.B)) >> 8)
}

// crop returns the part of an image within r, in pixels from its top left corner, keeping the image's coordinates.
func crop(img image.Image, r image.Rectangle) (image.Image, bool) {
	sub, ok := img.(interface {
		SubImage(image.Rectangle) image.Image
	})
	if !ok {
		return nil, false
	}
	return sub.SubImage(r.Add(img.Bounds().Min)), true
}

// seenShot is the shot that the check compares the window with when the batch's coordinates come from sh: the last
// shot of the window that a result gave the session, when that is a changes shot that descends from sh, or from the
// shot that the zoom sh was read from, through the changes shots that the agent got since, and sh otherwise. Its
// coordinates are sh's, but the agent has seen the changes since in their zooms.
func (s *Server) seenShot(sh *shot) *shot {
	last := s.shots.lastOf(sh.Window, sh.StableID)
	if last == nil || number(last.ID) <= number(sh.ID) {
		return sh
	}
	for c, n := last, 0; c != nil && c.Base != "" && n <= maxDeltas; c, n = s.shots.get(c.Base), n+1 {
		if c.Base == sh.ID || sh.Parent != "" && c.Base == sh.Parent {
			return last
		}
	}
	return sh
}

// checkTargets checks, before a batch is sent, whether the window changed around a point where it clicks or drags
// since the shot that its coordinates come from, sh, or the changes shot after it that the agent has seen since (see
// seenShot), and returns a stale_shot failure if it did. A window whose counter, in cur, has not moved since that shot
// is not captured. Otherwise the window is rendered as the shot's reference image was and compared with it around each
// point; a change gets the window up to movedSettleMax to settle, and counts if it then holds still, also within an
// animation. The check ends by the time given, when the batch has to start to fit before input has to end. When it
// captured the window, it returns the counter of the capture that it decided on: a window that has drawn since may
// have moved its targets after it, and is checked again.
func (s *Server) checkTargets(ctx context.Context, r *actRequest, coords *shot, cur hypr.State, by time.Time, p *progress) (decided *baseline, err error) {
	targets := targetsOf(r.batch, coords)
	if len(targets) == 0 {
		return nil, nil
	}
	sh := s.seenShot(coords)
	ref := sh.reference()
	if ref == nil || !drewSince(sh, cur) {
		return nil, nil
	}
	// A reference that is gone, as the detail image of a changes shot that the server has dropped while one of its zooms
	// stays, leaves nothing to compare with, so the batch is refused with a new shot to read coordinates from.
	before, err := readView(ref)
	if err != nil {
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("shot %s can no longer be compared with the window, which drew since: the image it was read from is gone", sh.ID),
			Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	p.set("checking the targets against the shot")
	ctx, cancel := context.WithDeadline(ctx, by)
	defer cancel()
	start := time.Now()
	now, err := s.captureView(ctx, r.window, ref)
	if err != nil {
		return nil, checkError(ctx, err, sh, r.batch, targets)
	}
	// One capture cannot tell an animation from content that the window drew in its box, so every change counts here,
	// and the capture after the wait below tells them apart.
	var changed []target
	for _, t := range targets {
		if changedAt(before, now, t.box, nil, nil) {
			changed = append(changed, t)
		}
	}
	if len(changed) == 0 {
		return &now.at, nil
	}

	// The window may still be drawing the change, or keep changing there. It gets frame callbacks meanwhile, without
	// which a hidden window does not draw, and leaves the capture after it twice the time of the first, at least
	// movedCaptureMin.
	animated := slices.Clone(sh.Animated)
	if settle := min(movedSettleMax, time.Until(by)-max(2*time.Since(start), movedCaptureMin)); settle >= movedQuietMs*time.Millisecond {
		reply, err := s.wait(ctx, r.window, hypr.WaitArgs{Since: now.at.commits, QuietMs: movedQuietMs, DeadlineMs: int(settle.Milliseconds()), ID: s.waitID(), IgnoreAnimations: true})
		var pe *hypr.Error
		switch {
		case errors.As(err, &pe) && pe.Code == "window_gone":
			return nil, err
		case err == nil && deref(reply.SocketID) == deref(cur.SocketID):
			animated = mergeAnimated(animated, reply.Animated)
		}
	}
	moved, at := changed, now.at
	if later, err := s.captureView(ctx, r.window, ref); err == nil {
		// Within the animations, what changed between the two captures keeps changing, and the rest holds still.
		var boxes []hypr.IntRect
		for _, t := range changed {
			for _, a := range animated {
				if box := rectOf(a).Intersect(rectOf(t.box)); !box.Empty() {
					boxes = append(boxes, intRectOf(box))
				}
			}
		}
		changing := changingParts(now, later, boxes)
		moved, at = slices.DeleteFunc(changed, func(t target) bool {
			return !changedAt(before, later, t.box, animated, changing) || changedAt(now, later, t.box, animated, changing)
		}), later.at
	} else if asToolError(err).Code == "window_gone" || expired(ctx) {
		// A check that ran out of time could not tell a move from content that keeps changing.
		return nil, checkError(ctx, err, sh, r.batch, changed)
	}
	if len(moved) == 0 {
		return &at, nil
	}
	return nil, movedError(sh, r.batch, moved, animated)
}

// checkError maps a failed capture of the check. A capture region whose popup has closed since the shot shows nothing
// of the window: what the shot showed there is gone.
func checkError(ctx context.Context, err error, sh *shot, batch []action, targets []target) error {
	var pe *hypr.Error
	switch {
	case errors.As(err, &pe) && pe.Code == "invalid_arguments" && sh.reference().Region != nil:
		return movedError(sh, batch, targets, sh.Animated)
	case expired(ctx):
		return &toolError{Code: "deadline", Message: fmt.Sprintf("too little time was left before the input had to start to check whether the window changed where the batch clicks or drags since shot %s", sh.ID),
			Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
	}
	return err
}

// expired tells whether the check's time is up. A request can fail at its deadline a moment before the context says so.
func expired(ctx context.Context) bool {
	deadline, ok := ctx.Deadline()
	return errors.Is(ctx.Err(), context.DeadlineExceeded) || ok && !time.Now().Before(deadline)
}

// movedError refuses a batch whose targets changed since the shot, naming the actions they belong to. It carries the
// animations that the check saw, which the shot taken after it records.
func movedError(sh *shot, batch []action, moved []target, animated []hypr.IntRect) *toolError {
	var at, actions []string
	for _, t := range moved {
		name := "at "
		if t.point.Name != "" {
			name = t.point.Name
		}
		at = append(at, fmt.Sprintf("%s %s%s,%s", describe(t.index, batch[t.index]), name, formatNumber(t.point.X), formatNumber(t.point.Y)))
		if d := describe(t.index, batch[t.index]); !slices.Contains(actions, d) {
			actions = append(actions, d)
		}
	}
	targets := "The target of " + strings.Join(actions, " and of ")
	return &toolError{Code: "stale_shot",
		Message:  fmt.Sprintf("the window changed since shot %s around %s, so the input could reach something other than what the shot shows there", sh.ID, strings.Join(at, " and ")),
		Hint:     fmt.Sprintf("%s moved or changed since %s. Take a screenshot and read the coordinates from the new shot.", targets, sh.ID),
		ShotHint: fmt.Sprintf("%s moved or changed since %s. Read the coordinates from the new shot in this result.", targets, sh.ID),
		animated: animated}
}
