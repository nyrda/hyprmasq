package server

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"image"
	"image/png"
	"math"
	"os"
	"path/filepath"
	"sort"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/delta"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// The limits of capture "changes", in logical pixels unless they say otherwise.
const (
	// Levels, of 255, by which the 3×3 average around a pixel has to change in luminance, or in a color channel. Below
	// 24, Chromium's switches between subpixel and grayscale antialiasing still left dozens of specks; a row's hover
	// highlight in a dark theme changes by 24 and is left out. The channels catch a change of color that keeps the
	// luminance, and at 64 none of the antialiasing.
	changeThreshold      = 24
	changeColorThreshold = 64
	// Changed pixels at most this far apart are one change, such as a typed word and the caret after it.
	changeGroup = 8
	// A change at most changeThin wide or tall, such as a blinking caret of 2 pixels and the pixel around it that the
	// average reaches, or at most changeSpeck in both directions, is tiny and left out.
	changeThin  = 4
	changeSpeck = 5
	// The margin around each change in its zoom, for context. Changes whose zooms would overlap share one.
	regionMargin = 24
	// More regions than this, or regions that cover more of the window than maxChangedArea, come as a full shot.
	maxRegions     = 5
	maxChangedArea = 0.35
	// After this many changes shots in a row, the next shot is a full one.
	maxDeltas = 10
)

const (
	// The time that a changes capture leaves before its deadline for the full shot it may fall back to.
	fallbackReserve = 500 * time.Millisecond
	// About how long decoding two detail images and comparing them takes per pixel: 170 ms for 2544×1432.
	comparePerPixel = 60 * time.Nanosecond
)

// detailGeometry places the pixels of a detail image in the window: pixel X,Y is at frame.X + X / scale,
// frame.Y + Y / scale.
type detailGeometry struct {
	frame hypr.Rect
	scale float64
}

// deltaOptions are the limits of capture "changes" in pixels of detail images at scale.
func deltaOptions(scale float64, maxChanged int) delta.Options {
	px := func(logical float64) int { return int(math.Round(logical * scale)) }
	return delta.Options{Threshold: changeThreshold, ColorThreshold: changeColorThreshold, Group: max(1, px(changeGroup)), Thin: px(changeThin), Speck: px(changeSpeck),
		MaxChanged: maxChanged}
}

// logicalBox maps a box of detail pixels to the whole logical pixels that cover it.
func (g detailGeometry) logicalBox(box image.Rectangle) hypr.IntRect {
	x0 := math.Floor(g.frame.X + float64(box.Min.X)/g.scale + 1e-9)
	y0 := math.Floor(g.frame.Y + float64(box.Min.Y)/g.scale + 1e-9)
	x1 := math.Ceil(g.frame.X + float64(box.Max.X)/g.scale - 1e-9)
	y1 := math.Ceil(g.frame.Y + float64(box.Max.Y)/g.scale - 1e-9)
	return hypr.IntRect{X: int(x0), Y: int(y0), Width: int(x1 - x0), Height: int(y1 - y0)}
}

// bounds is the box of whole logical pixels that covers the frame. At a fractional scale the frame can end within a
// logical pixel, whose device pixels a zoom then still reads; the plugin cuts zooms to the window and its popups.
func (g detailGeometry) bounds() hypr.IntRect {
	x0, y0 := math.Floor(g.frame.X+1e-9), math.Floor(g.frame.Y+1e-9)
	x1, y1 := math.Ceil(g.frame.X+g.frame.Width-1e-9), math.Ceil(g.frame.Y+g.frame.Height-1e-9)
	return hypr.IntRect{X: int(x0), Y: int(y0), Width: int(x1 - x0), Height: int(y1 - y0)}
}

func rectOf(r hypr.IntRect) image.Rectangle {
	return image.Rect(r.X, r.Y, r.X+r.Width, r.Y+r.Height)
}

func intRectOf(r image.Rectangle) hypr.IntRect {
	return hypr.IntRect{X: r.Min.X, Y: r.Min.Y, Width: r.Dx(), Height: r.Dy()}
}

// planRegions turns the changes into the regions to zoom into, in whole logical pixels: each change with a margin,
// cut to the frame, and joined with the regions it overlaps or touches. It also counts the tiny changes that no region
// shows whole.
func planRegions(res delta.Result, g detailGeometry) (regions []hypr.IntRect, leftOut int) {
	bounds := rectOf(g.bounds())
	var rects []image.Rectangle
	for _, box := range res.Changes {
		r := rectOf(g.logicalBox(box)).Inset(-regionMargin).Intersect(bounds)
		if !r.Empty() {
			rects = append(rects, r)
		}
	}
	rects = joinTouching(rects)
	// Top to bottom, then left to right, as one reads them.
	for i := 1; i < len(rects); i++ {
		for j := i; j > 0 && (rects[j].Min.Y < rects[j-1].Min.Y || rects[j].Min.Y == rects[j-1].Min.Y && rects[j].Min.X < rects[j-1].Min.X); j-- {
			rects[j], rects[j-1] = rects[j-1], rects[j]
		}
	}
	for _, box := range res.Tiny {
		t := rectOf(g.logicalBox(box)).Intersect(bounds)
		shown := false
		for _, r := range rects {
			if t.In(r) {
				shown = true
				break
			}
		}
		if !shown {
			leftOut++
		}
	}
	for _, r := range rects {
		regions = append(regions, intRectOf(r))
	}
	return regions, leftOut
}

// joinTouching joins rectangles that overlap or touch until none do. Sorted by their left edges, a rectangle can only
// touch those that start at or before its right edge, so a pass compares neighbours along x; a rectangle that grew may
// touch one before it, which the next pass joins.
func joinTouching(rects []image.Rectangle) []image.Rectangle {
	for joined := true; joined; {
		joined = false
		sort.Slice(rects, func(i, j int) bool { return rects[i].Min.X < rects[j].Min.X })
		gone := make([]bool, len(rects))
		for i := range rects {
			if gone[i] {
				continue
			}
			for j := i + 1; j < len(rects) && rects[j].Min.X <= rects[i].Max.X; j++ {
				// Rectangles that touch share an edge, which Overlaps does not count.
				if !gone[j] && rects[i].Inset(-1).Overlaps(rects[j]) {
					rects[i] = rects[i].Union(rects[j])
					gone[j], joined = true, true
				}
			}
		}
		kept := rects[:0]
		for i, r := range rects {
			if !gone[i] {
				kept = append(kept, r)
			}
		}
		rects = kept
	}
	return rects
}

// regionsArea is the share of the frame that the regions cover.
func regionsArea(regions []hypr.IntRect, g detailGeometry) float64 {
	area := 0
	for _, r := range regions {
		area += r.Width * r.Height
	}
	return float64(area) / (g.frame.Width * g.frame.Height)
}

// changesBase is the shot that capture "changes" compares with, or why there is none: the shot that the coordinates
// come from, or for a zoom the shot of the whole window it was read from, and without a shot the last shot of the
// window that a result gave the session.
func (s *Server) changesBase(window string, sh *shot, st hypr.State) (*shot, string) {
	if sh == nil {
		if last := s.shots.lastOf(window, st.StableID); last != nil {
			return last, ""
		}
		return nil, "no earlier shot to compare with"
	}
	if sh.Parent == "" {
		return sh, ""
	}
	if parent := s.shots.get(sh.Parent); parent != nil && parent.StableID == sh.StableID {
		return parent, ""
	}
	return nil, fmt.Sprintf("the shot that %s zooms into is gone", sh.ID)
}

// errNotSettled means that the window had not caught up with a resize, or was resized, while a detail image was
// rendered, which a full shot then waits for.
var errNotSettled = errors.New("the window is resizing")

// captureDetail renders a detail image of the window with the max of an earlier one, and returns it with the window's
// state after it.
func (s *Server) captureDetail(ctx context.Context, window, stableID string, limit int) (*detail, hypr.State, hypr.Screenshot, error) {
	pre, err := s.openState(ctx, window, stableID)
	if err != nil {
		return nil, pre, hypr.Screenshot{}, err
	}
	if pre.Resizing {
		return nil, pre, hypr.Screenshot{}, errNotSettled
	}
	if err := os.MkdirAll(s.shotDir, 0o700); err != nil {
		return nil, pre, hypr.Screenshot{}, err
	}
	path := filepath.Join(s.shotDir, fmt.Sprintf("d%d.png", s.detailSeq.Add(1)))
	reply, err := s.hypr.Screenshot(ctx, window, path, nil, limit)
	if err != nil {
		os.Remove(path)
		return nil, pre, reply, err
	}
	post, err := s.hypr.State(ctx, window)
	switch {
	case err != nil:
	case post.StableID != pre.StableID:
		err = &toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed during the capture", window), Hint: hintWindows}
	case post.Size != pre.Size || reply.Resizing || reply.Scale <= 0 || reply.Width <= 0 || reply.Height <= 0:
		err = errNotSettled
	}
	if err != nil {
		os.Remove(path)
		return nil, post, reply, err
	}
	return detailOf(reply, path, limit), post, reply, nil
}

func readPNG(path string) (image.Image, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return png.Decode(f)
}

// captureChanges captures what changed since the base shot: a changes shot with the base's pixels, and a zoom into each
// region that changed, at the monitor's scale. Without a base to compare with, or when a full shot serves better, it
// captures a full shot and says why.
func (s *Server) captureChanges(ctx context.Context, r *actRequest, out *actOut, stableID string) (*shot, [][]byte, error) {
	full := func(reason string) (*shot, [][]byte, error) {
		sh, image, err := s.capture(ctx, r.window, stableID, nil)
		if err != nil {
			return nil, nil, err
		}
		out.Shot = shotInfo(sh)
		out.Shot.FullShot = reason
		return sh, [][]byte{image}, nil
	}
	base := r.base
	switch {
	case base == nil:
		return full(r.baseReason)
	case base.Detail == nil || base.Resizing:
		return full(fmt.Sprintf("%s cannot be compared", base.ID))
	case base.Deltas >= maxDeltas:
		return full(fmt.Sprintf("%d changes shots in a row", base.Deltas))
	}
	after, st, reply, err := s.captureDetail(ctx, r.window, stableID, base.Detail.Max)
	if errors.Is(err, errNotSettled) {
		return full("the window is resizing")
	}
	if err != nil {
		return nil, nil, err
	}
	keep := false
	defer func() {
		if !keep {
			os.Remove(after.Path)
		}
	}()
	switch {
	case st.Size != base.Size:
		return full("resized since " + base.ID)
	case math.Abs(after.Scale-base.Detail.Scale) > 1e-6:
		return full("the monitor's scale changed since " + base.ID)
	case !after.sameGeometry(base.Detail):
		return full("popups outside the window changed since " + base.ID)
	}
	// A comparison that runs out of time leaves the full shot the time it needs.
	pixels := after.Width * after.Height
	deadline, hasDeadline := ctx.Deadline()
	stopBy := deadline.Add(-fallbackReserve)
	if hasDeadline && time.Until(stopBy) < time.Duration(pixels)*comparePerPixel {
		return full("too little time left to compare with " + base.ID)
	}
	before, err := readPNG(base.Detail.Path)
	if err != nil {
		return full(fmt.Sprintf("%s cannot be compared", base.ID))
	}
	now, err := readPNG(after.Path)
	if err != nil {
		return nil, nil, err
	}
	if before.Bounds().Size() != now.Bounds().Size() {
		return full(fmt.Sprintf("%s cannot be compared", base.ID))
	}
	o := deltaOptions(after.Scale, int(maxChangedArea*float64(pixels)))
	if hasDeadline {
		o.Stop = func() bool { return time.Now().After(stopBy) }
	}
	res := delta.Compare(before, now, o)
	if res.Stopped {
		return full("too little time left to compare with " + base.ID)
	}
	if res.Over(o) {
		return full(fmt.Sprintf("%d%% changed since %s", int(math.Round(100*float64(res.Changed)/float64(pixels))), base.ID))
	}
	g := after.geometry()
	regions, leftOut := planRegions(res, g)
	if area := regionsArea(regions, g); area > maxChangedArea {
		return full(fmt.Sprintf("%d%% changed since %s", int(math.Round(100*area)), base.ID))
	}
	if len(regions) > maxRegions {
		return full(fmt.Sprintf("%d changed regions since %s", len(regions), base.ID))
	}
	if zoomPixels(regions, st.Scale, s.cfg.MaxImageSize) > base.Width*base.Height {
		return full(fmt.Sprintf("the zooms into the changes since %s would be larger", base.ID))
	}

	sh := &shot{ID: s.shots.newID(), Window: r.window, StableID: base.StableID, Size: st.Size, Frame: base.Frame, Width: base.Width, Height: base.Height,
		Scale: base.Scale, Base: base.ID, Deltas: base.Deltas + 1, Detail: after}
	if reply.Commits != nil {
		sh.Commits, sh.SocketID = *reply.Commits, deref(reply.SocketID)
	} else if st.Commits != nil {
		sh.Commits, sh.SocketID = *st.Commits, deref(st.SocketID)
	}
	keep = true
	s.shots.add(sh)
	info := shotInfo(sh)
	info.Changes = &changesOut{Base: base.ID, Regions: []regionOut{}, LeftOut: leftOut}
	// The zooms are cut from the compared image when it has the monitor's pixels, so that they show what was compared.
	// Otherwise they are captured, and a zoom of another frame, as when the window drew or a popup closed in between,
	// gives up the changes.
	cut := math.Abs(after.Scale-st.Scale) < 1e-6
	var zooms []string
	var images [][]byte
	for _, region := range regions {
		var zsh *shot
		var image []byte
		if area := after.pixelsOf(region); cut && (s.cfg.MaxImageSize <= 0 || max(area.Dx(), area.Dy()) <= s.cfg.MaxImageSize) {
			zsh, image, err = s.cutZoom(now, after, area, sh)
		} else if zsh, image, err = s.capture(ctx, r.window, stableID, &zoom{rect: region, from: sh}); err == nil && !zsh.sameFrame(sh, region, after.Frame) {
			zooms = append(zooms, zsh.ID)
			err = errors.New("the window drew while the zooms were captured")
		}
		if err != nil {
			for _, id := range append(zooms, sh.ID) {
				s.shots.remove(id)
			}
			if asToolError(err).Code == "window_gone" || ctx.Err() != nil {
				return nil, nil, err
			}
			// The window changed while the zooms were taken, so the whole window is captured as it is now.
			return full(fmt.Sprintf("the window changed while the changes since %s were captured", base.ID))
		}
		zooms = append(zooms, zsh.ID)
		info.Changes.Regions = append(info.Changes.Regions, regionInfo(zsh, sh))
		images = append(images, image)
	}
	out.Shot = info
	return sh, images, nil
}

// pixelsOf is the box of the detail image's pixels that cover a region, cut to the image.
func (d *detail) pixelsOf(region hypr.IntRect) image.Rectangle {
	x0 := math.Floor((float64(region.X)-d.Frame.X)*d.Scale + 1e-6)
	y0 := math.Floor((float64(region.Y)-d.Frame.Y)*d.Scale + 1e-6)
	x1 := math.Ceil((float64(region.X+region.Width)-d.Frame.X)*d.Scale - 1e-6)
	y1 := math.Ceil((float64(region.Y+region.Height)-d.Frame.Y)*d.Scale - 1e-6)
	return image.Rect(int(x0), int(y0), int(x1), int(y1)).Intersect(image.Rect(0, 0, d.Width, d.Height))
}

// cutZoom makes a zoom shot of a changes shot from the box of its detail image, which has the monitor's pixels.
func (s *Server) cutZoom(img image.Image, d *detail, box image.Rectangle, of *shot) (*shot, []byte, error) {
	sub, ok := img.(interface {
		SubImage(image.Rectangle) image.Image
	})
	if !ok || box.Empty() {
		return nil, nil, fmt.Errorf("cannot cut %v from the detail image", box)
	}
	var buf bytes.Buffer
	if err := (&png.Encoder{CompressionLevel: png.BestSpeed}).Encode(&buf, sub.SubImage(box.Add(img.Bounds().Min))); err != nil {
		return nil, nil, err
	}
	id := s.shots.newID()
	path := filepath.Join(s.shotDir, id+".png")
	if err := os.WriteFile(path, buf.Bytes(), 0o600); err != nil {
		os.Remove(path)
		return nil, nil, err
	}
	zsh := &shot{ID: id, Window: of.Window, StableID: of.StableID, Size: of.Size,
		Frame: hypr.Rect{X: d.Frame.X + float64(box.Min.X)/d.Scale, Y: d.Frame.Y + float64(box.Min.Y)/d.Scale, Width: float64(box.Dx()) / d.Scale, Height: float64(box.Dy()) / d.Scale},
		Width: box.Dx(), Height: box.Dy(), Scale: d.Scale, Commits: of.Commits, SocketID: of.SocketID, Path: path, Parent: of.ID}
	s.shots.add(zsh)
	return zsh, buf.Bytes(), nil
}

// sameFrame tells whether a captured zoom of a changes shot shows the frame that the changes shot was compared at,
// and all of its region within the box of that frame: the commit counter has not moved since, and no popup that closed
// meanwhile made the plugin cut the region.
func (z *shot) sameFrame(of *shot, region hypr.IntRect, box hypr.Rect) bool {
	if z.Commits != of.Commits || z.SocketID != of.SocketID {
		return false
	}
	x0, y0 := max(float64(region.X), box.X), max(float64(region.Y), box.Y)
	x1, y1 := min(float64(region.X+region.Width), box.X+box.Width), min(float64(region.Y+region.Height), box.Y+box.Height)
	const slack = 1e-6
	return z.Frame.X <= x0+slack && z.Frame.Y <= y0+slack && z.Frame.X+z.Frame.Width >= x1-slack && z.Frame.Y+z.Frame.Height >= y1-slack
}

// zoomPixels is how many pixels the zooms into the regions take, as the plugin captures them: at the monitor's scale,
// from the device pixel that holds a region's corner to the one that holds its end, or fitted within maxImage.
func zoomPixels(regions []hypr.IntRect, scale float64, maxImage int) int {
	const epsilon = 1e-6
	total := 0
	for _, r := range regions {
		x, y, right, bottom := float64(r.X), float64(r.Y), float64(r.X+r.Width), float64(r.Y+r.Height)
		w := math.Ceil(right*scale-epsilon) - math.Floor(x*scale+epsilon)
		h := math.Ceil(bottom*scale-epsilon) - math.Floor(y*scale+epsilon)
		if maxImage > 0 && max(w, h) > float64(maxImage) {
			target := min(scale, float64(maxImage)/float64(max(r.Width, r.Height)))
			w, h = math.Ceil(float64(r.Width)*target-epsilon), math.Ceil(float64(r.Height)*target-epsilon)
		}
		total += int(w * h)
	}
	return total
}

// regionInfo describes a zoom of a changes shot by the box of the shot's pixels that it shows.
func regionInfo(zoom, of *shot) regionOut {
	x0 := math.Floor((zoom.Frame.X-of.Frame.X)*of.Scale + 1e-6)
	y0 := math.Floor((zoom.Frame.Y-of.Frame.Y)*of.Scale + 1e-6)
	x1 := math.Ceil((zoom.Frame.X+zoom.Frame.Width-of.Frame.X)*of.Scale - 1e-6)
	y1 := math.Ceil((zoom.Frame.Y+zoom.Frame.Height-of.Frame.Y)*of.Scale - 1e-6)
	x0, y0 = max(0, x0), max(0, y0)
	x1, y1 = min(float64(of.Width), x1), min(float64(of.Height), y1)
	return regionOut{Shot: zoom.ID, ImageWidth: zoom.Width, ImageHeight: zoom.Height, X: int(x0), Y: int(y0), Width: int(x1 - x0), Height: int(y1 - y0)}
}
