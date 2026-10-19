package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"math"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// regionArgs is a rectangle in the pixels of an earlier shot.
type regionArgs struct {
	Shot                string
	X, Y, Width, Height float64
}

// regionJSON is a region as the arguments give it. Every member is required, so the fields tell which are missing.
type regionJSON struct {
	Shot   *string  `json:"shot" args:"required"`
	X      *float64 `json:"x" args:"required"`
	Y      *float64 `json:"y" args:"required"`
	Width  *float64 `json:"width" args:"required"`
	Height *float64 `json:"height" args:"required"`
}

// parseRegion reads a region argument, whose path is field, such as region.
func parseRegion(raw json.RawMessage, field string) (*regionArgs, error) {
	var in regionJSON
	if err := decodeStrict(raw, &in, field); err != nil {
		return nil, err
	}
	for _, member := range []struct {
		name    string
		missing bool
	}{{"shot", in.Shot == nil}, {"x", in.X == nil}, {"y", in.Y == nil}, {"width", in.Width == nil}, {"height", in.Height == nil}} {
		if member.missing {
			return nil, invalidf("%s.%s is missing; a region is {shot, x, y, width, height} in pixels of an earlier shot", field, member.name)
		}
	}
	if *in.Shot == "" {
		return nil, invalidf("%s.shot is empty; a region is given in pixels of an earlier shot", field)
	}
	if *in.Width <= 0 || *in.Height <= 0 {
		return nil, invalidf("%s: width and height must be above 0", field)
	}
	return &regionArgs{Shot: *in.Shot, X: *in.X, Y: *in.Y, Width: *in.Width, Height: *in.Height}, nil
}

// checkShot finds a shot of the window that still matches its current state. The size only matters where the
// shot's coordinates are used.
func (s *Server) checkShot(id, window string, st hypr.State, needSize bool) (*shot, error) {
	sh := s.shots.get(id)
	c, dropped := s.shots.droppedOf(id)
	switch {
	case sh == nil && dropped && c.closed:
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("shot %s is of window %s (stable_id %s), which has closed", id, c.window, c.stableID), Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	case sh == nil && dropped && c.evicted:
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("there is no shot %s any more; this server drops the least recently used shots once their images take %d MB", id, shotBytes>>20),
			Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	case sh == nil:
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("there is no shot %s; this server keeps the last %d shots of each window", id, shotsPerWindow), Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	if sh.Window != window {
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("shot %s is of window %s, not %s", id, sh.Window, window), Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	if sh.StableID != st.StableID {
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("shot %s is of another window that had the address %s (stable_id %s, now %s)", id, window, sh.StableID, st.StableID), Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	if needSize && sh.Size != st.Size {
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("the window was resized from %s to %s since shot %s", formatSize(sh.Size), formatSize(st.Size), id), Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	// The application lays its content out anew once it draws at its size, so the older frame's coordinates would
	// miss, also when it has caught up by now.
	if needSize && sh.Resizing {
		return nil, &toolError{Code: "stale_shot", Message: fmt.Sprintf("shot %s shows an older frame, since the window had not yet redrawn at its new size %s when it was taken", id, formatSize(sh.Size)),
			Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	return sh, nil
}

func formatSize(size hypr.Size) string {
	return fmt.Sprintf("%s×%s", formatNumber(size.Width), formatNumber(size.Height))
}

// A zoom is a region to capture in window-local logical pixels, and the shot it was read from. After a resize, the
// same logical pixels show another part of the window, so it is captured only while the window has that shot's size.
type zoom struct {
	rect hypr.IntRect
	from *shot
}

// zoomResized fails the capture of a zoom whose shot no longer has the window's size.
type zoomResized struct {
	from *shot
	size hypr.Size
}

func (e *zoomResized) Error() string {
	return fmt.Sprintf("the window was resized from %s to %s since shot %s", formatSize(e.from.Size), formatSize(e.size), e.from.ID)
}

// resolveRegion maps a region of an earlier shot to logical pixels, cut to that shot's image.
func (s *Server) resolveRegion(r regionArgs, window string, st hypr.State, field string) (*zoom, error) {
	sh, err := s.checkShot(r.Shot, window, st, true)
	if err != nil {
		return nil, err
	}
	if !sh.contains(r.X, r.Y) {
		return nil, &toolError{Code: "out_of_bounds",
			Message: fmt.Sprintf("%s starts at %s,%s, outside shot %s (%d×%d)", field, formatNumber(r.X), formatNumber(r.Y), sh.ID, sh.Width, sh.Height),
			Hint:    fmt.Sprintf("Coordinates are pixels of shot %s, from 0,0 to %d,%d.", sh.ID, sh.Width-1, sh.Height-1)}
	}
	width := math.Min(r.Width, float64(sh.Width)-r.X)
	height := math.Min(r.Height, float64(sh.Height)-r.Y)
	return &zoom{rect: sh.logicalRegion(r.X, r.Y, width, height), from: sh}, nil
}

// How often capture tries to get an image with the window's size the same before and after.
const captureAttempts = 3

const (
	// How long a capture waits at most for a window to catch up with a resize, or for a starved window to draw, within
	// the time that its context leaves before the capture's reserve.
	catchUpMax = 1500 * time.Millisecond
	// How long the window then has to stay quiet, so that the shot shows the frame it settled on.
	catchUpQuietMs = 100
	// How long a starved window has for its first frame after the frame callbacks start. Thunderbird on a hidden
	// workspace drew within 40 ms of them in the benchmark of 2026-10-14, and so does an application that draws
	// in answer to them; one with nothing new to draw never does, and costs the whole time, but only once: after the
	// wait, the window has got frame callbacks from its socket, so it is no longer starved. A second leaves a busy
	// application 60 frames.
	starvedExpectMs = 1000
)

// catchUp waits for a window that has not caught up with a resize to draw a frame of its size, and for a starved one
// to draw a frame, within starvedExpectMs; then for a short quiet. The plugin's wait does not end quiet before the
// window has caught up, and sends a hidden window the frame callbacks it may need to draw. A window that does not catch
// up or draw in time is captured anyway, and its shot says so. For a starved window, it returns how long the window had
// to draw, 0 when the wait did not run, and whether it drew.
func (s *Server) catchUp(ctx context.Context, window string, commits *int64, starved bool) (expectMs int, drew bool, err error) {
	limit := catchUpMax
	if deadline, ok := ctx.Deadline(); ok {
		limit = min(limit, time.Until(deadline)-captureReserve)
	}
	if limit < catchUpQuietMs*time.Millisecond || commits == nil {
		return 0, false, nil
	}
	args := hypr.WaitArgs{Since: *commits, QuietMs: catchUpQuietMs, DeadlineMs: int(limit.Milliseconds()), ID: s.waitID()}
	if starved {
		args.ExpectMs = min(starvedExpectMs, args.DeadlineMs)
	}
	reply, err := s.wait(ctx, window, args)
	if ctx.Err() != nil {
		return 0, false, ctx.Err()
	}
	var pe *hypr.Error
	if errors.As(err, &pe) && pe.Code == "window_gone" {
		return 0, false, err
	}
	// Any other failure leaves the shot to say whether the window caught up, and a starved window is still starved.
	if err != nil {
		return 0, false, nil
	}
	return args.ExpectMs, reply.RedrawObserved, nil
}

func (s *Server) waitID() string {
	return fmt.Sprintf("%s-w%d", s.owner, s.waitSeq.Add(1))
}

// capture takes a shot of the window with the stable_id, or a zoom into it, and remembers it. A window that has not
// caught up with a resize gets a short time to do so first, and a starved window frame callbacks and a short time to
// draw. A new window at the address is not captured, and a zoom only while the window has the size of the shot it was
// read from. A shot of the whole window comes from one render, which gives its image and its detail image.
func (s *Server) capture(ctx context.Context, window, stableID string, z *zoom) (*shot, []byte, error) {
	if err := os.MkdirAll(s.shotDir, 0o700); err != nil {
		return nil, nil, err
	}
	var region *hypr.IntRect
	if z != nil {
		region = &z.rect
	}
	caughtUp := false
	// For a starved window: how long the wait gave it to draw, 0 without one, and whether it drew.
	starvedMs, starvedDrew := 0, false
	catchUp := func(st hypr.State, starved bool) error {
		caughtUp = true
		ms, drew, err := s.catchUp(ctx, window, st.Commits, starved)
		if starved {
			starvedMs, starvedDrew = ms, drew
		}
		return err
	}
	// A resize between the state and the screenshot would record the wrong size, so the capture is repeated.
	for attempt := 1; ; {
		pre, err := s.openState(ctx, window, stableID)
		if err != nil {
			return nil, nil, err
		}
		if z != nil && pre.Size != z.from.Size {
			return nil, nil, &zoomResized{from: z.from, size: pre.Size}
		}
		if (pre.Resizing || pre.Starved) && !caughtUp {
			if err := catchUp(pre, pre.Starved); err != nil {
				return nil, nil, err
			}
			continue
		}
		id := s.shots.newID()
		path := filepath.Join(s.shotDir, id+".png")
		var f *frame
		var reply hypr.Screenshot
		if z == nil {
			if f, err = s.renderFrame(ctx, window); err == nil {
				reply = f.reply
			}
		} else if reply, err = s.hypr.Screenshot(ctx, window, hypr.ScreenshotArgs{Path: path, Region: region, Max: s.cfg.MaxImageSize}); err != nil {
			os.Remove(path)
			var pe *hypr.Error
			if errors.As(err, &pe) && pe.Code == "invalid_arguments" {
				// The region is whole and not empty, so the plugin refuses it only when it lies outside the window and
				// its popups, as when it showed a popup that has closed since.
				return nil, nil, &toolError{Code: "stale_shot", Message: "the region shows nothing of the window now; a popup it showed may have closed: " + pe.Message,
					Hint: hintScreenshot}
			}
		}
		if err != nil {
			return nil, nil, err
		}
		post, err := s.hypr.State(ctx, window)
		if err != nil {
			os.Remove(path)
			return nil, nil, err
		}
		if post.StableID != pre.StableID {
			os.Remove(path)
			return nil, nil, &toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed during the capture", window), Hint: hintWindows}
		}
		if post.Size != pre.Size {
			os.Remove(path)
			// Which of the sizes the image shows is unknown, so it never becomes a shot.
			if attempt == captureAttempts {
				return nil, nil, &toolError{Code: "internal", Message: fmt.Sprintf("window %s was resized during each of %d captures", window, attempt),
					Hint: "Take a screenshot once the window has stopped resizing."}
			}
			attempt++
			continue
		}
		// A resize, or a wait for a frame callback, that started after the state was read.
		if (reply.Resizing || reply.Starved) && !caughtUp {
			os.Remove(path)
			if err := catchUp(post, reply.Starved); err != nil {
				return nil, nil, err
			}
			continue
		}
		if reply.Scale <= 0 || reply.Width <= 0 || reply.Height <= 0 {
			os.Remove(path)
			return nil, nil, &toolError{Code: "internal", Message: fmt.Sprintf("the plugin returned an empty screenshot (%d×%d at scale %v)", reply.Width, reply.Height, reply.Scale)}
		}
		var sh *shot
		var png []byte
		if z == nil {
			f.pre, f.post = pre, post
			if sh, png, err = s.frameShot(id, window, f); err != nil {
				return nil, nil, err
			}
		} else {
			if png, err = os.ReadFile(path); err != nil {
				os.Remove(path)
				return nil, nil, err
			}
			sh = &shot{ID: id, Window: window, StableID: pre.StableID, Size: post.Size, Frame: reply.Region,
				Width: reply.Width, Height: reply.Height, Scale: reply.Scale, Resizing: reply.Resizing, Path: path}
			sh.setCommits(reply, pre)
			sh.Parent = z.from.ID
			if z.from.Parent != "" {
				sh.Parent = z.from.Parent
			}
			// A zoom is compared by its own image, and keeps the animations of the shot it was read from.
			rect := z.rect
			sh.Ref = detailOf(reply.Image, path, s.cfg.MaxImageSize)
			sh.Ref.Region = &rect
			sh.Animated = z.from.Animated
		}
		// A window still starved got no frame callbacks from the wait, which did not run.
		switch {
		case reply.Starved:
			sh.Starved = &starvation{}
		case starvedMs > 0 && !starvedDrew:
			sh.Starved = &starvation{WaitedMs: starvedMs}
		}
		s.shots.add(sh)
		return sh, png, nil
	}
}

// A frame is what one render of the whole window gave: the overview that a shot shows, and the detail image that is
// compared, in memory.
type frame struct {
	reply    hypr.Screenshot
	overview *image.NRGBA
	// The detail image, which is the overview when that has as many pixels, or nil when the window had not caught up
	// with a resize.
	detail *detail
	// The window's state before and after the render.
	pre, post hypr.State
}

// renderFrame renders the whole window once, for its overview and its detail image, which the plugin writes as raw
// pixels: they are read into memory and their files deleted.
func (s *Server) renderFrame(ctx context.Context, window string) (*frame, error) {
	if err := os.MkdirAll(s.shotDir, 0o700); err != nil {
		return nil, err
	}
	n := s.detailSeq.Add(1)
	path, detailPath := filepath.Join(s.shotDir, fmt.Sprintf("f%d.raw", n)), filepath.Join(s.shotDir, fmt.Sprintf("f%d-detail.raw", n))
	defer os.Remove(path)
	defer os.Remove(detailPath)
	reply, err := s.hypr.Screenshot(ctx, window, hypr.ScreenshotArgs{Path: path, Max: s.cfg.MaxImageSize, Raw: true, DetailPath: detailPath})
	if err != nil {
		return nil, err
	}
	f := &frame{reply: reply}
	if f.overview, err = hypr.ReadRaw(path, reply.Width, reply.Height); err != nil {
		return nil, err
	}
	// Without a detail image, the shot cannot be compared, which a later changes capture answers with a full shot.
	if reply.Resizing || reply.Scale <= 0 {
		return f, nil
	}
	spill := filepath.Join(s.shotDir, fmt.Sprintf("f%d-detail.z", n))
	if reply.Detail == nil {
		f.detail = detailOf(reply.Image, "", s.cfg.MaxImageSize)
		f.detail.keepPixels(f.overview, spill)
		return f, nil
	}
	img, err := hypr.ReadRaw(detailPath, reply.Detail.Width, reply.Detail.Height)
	if err != nil {
		return nil, err
	}
	f.detail = detailOf(reply.Detail.Image, "", reply.Detail.Max)
	f.detail.keepPixels(img, spill)
	return f, nil
}

// frameShot makes the shot with the id of a frame: its overview, encoded as the PNG that the agent gets, and its detail
// image. The caller adds it to the store.
func (s *Server) frameShot(id, window string, f *frame) (*shot, []byte, error) {
	png, err := encodePNG(f.overview)
	if err != nil {
		return nil, nil, err
	}
	path := filepath.Join(s.shotDir, id+".png")
	if err := os.WriteFile(path, png, 0o600); err != nil {
		os.Remove(path)
		return nil, nil, err
	}
	reply := f.reply
	sh := &shot{ID: id, Window: window, StableID: f.pre.StableID, Size: f.post.Size, Frame: reply.Region,
		Width: reply.Width, Height: reply.Height, Scale: reply.Scale, Resizing: reply.Resizing, Path: path, Detail: f.detail}
	// The overview is the detail image, whose pixels its PNG keeps once they leave memory.
	if f.detail != nil && reply.Detail == nil {
		f.detail.Path = path
	}
	sh.setCommits(reply, f.pre)
	return sh, png, nil
}

// setCommits records the window's counter when the shot was rendered, or before, from a plugin that reads none then.
func (sh *shot) setCommits(reply hypr.Screenshot, pre hypr.State) {
	switch {
	case reply.Commits != nil:
		sh.Commits, sh.SocketID = *reply.Commits, deref(reply.SocketID)
	case pre.Commits != nil:
		sh.Commits, sh.SocketID = *pre.Commits, deref(pre.SocketID)
	}
}

func detailOf(img hypr.Image, path string, max int) *detail {
	return &detail{Path: path, Width: img.Width, Height: img.Height, Scale: img.Scale, Frame: img.Region, Max: max}
}

type screenshotArgs struct {
	Window string          `json:"window" args:"required"`
	Region json.RawMessage `json:"region"`
}

// screenshotTool runs the screenshot tool.
func (s *Server) screenshotTool(ctx context.Context, c *call) *mcp.CallToolResult {
	var args screenshotArgs
	if err := c.decode(&args); err != nil {
		return simpleError(err)
	}
	window, err := canonicalWindow(args.Window)
	if err != nil {
		return simpleError(err)
	}
	args.Window = window
	var asked *regionArgs
	if len(args.Region) > 0 && string(args.Region) != "null" {
		if asked, err = parseRegion(args.Region, "region"); err != nil {
			return simpleError(err)
		}
	}
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	if err := s.checkPlugin(ctx); err != nil {
		return simpleError(err)
	}
	var regionShot string
	if asked != nil {
		regionShot = asked.Shot
	}
	shown, stableID := s.shownWindow(args.Window, regionShot)
	st, err := s.openState(ctx, args.Window, stableID)
	if err != nil {
		var windows []hypr.Window
		if shown != "" {
			windows, _ = s.hypr.Clients(ctx)
		}
		return simpleError(goneSinceShot(err, args.Window, shown, stableID, st, windows))
	}
	var z *zoom
	if asked != nil {
		if z, err = s.resolveRegion(*asked, args.Window, st, "region"); err != nil {
			return simpleError(err)
		}
	}
	sh, png, err := s.capture(ctx, args.Window, st.StableID, z)
	if err != nil {
		return simpleError(err)
	}
	s.shots.returned(sh)
	out := &screenshotOut{Shot: shotInfo(sh)}
	images := oneImage(png)
	// The windows that the input of an earlier act opened after it had returned come with the next shot, so that the
	// agent sees them before it acts again.
	if late := s.lateWindowsFor(ctx, args.Window, st.StableID); len(late) > 0 {
		end, _ := ctx.Deadline()
		var lateImages [][]byte
		out.LateWindows, lateImages = s.lateShots(ctx, end, late)
		images = append(images, lateImages...)
	}
	out.Summary = shotText(out.Shot)
	if asked != nil {
		out.Summary += fmt.Sprintf(" · zoom of %s at %s,%s %s×%s", asked.Shot, formatNumber(asked.X), formatNumber(asked.Y), formatNumber(asked.Width), formatNumber(asked.Height))
	}
	out.Summary += fmt.Sprintf(" · window %s %s", args.Window, formatSize(sh.Size))
	if text := lateWindowsText(out.LateWindows); text != "" {
		out.Summary += " · " + text
	}
	out.Summary = strings.Join(append([]string{out.Summary}, newWindowLines(out.LateWindows, "late window")...), "\n")
	out.Hint = joinHints(frameHints(out.Shot), lateHint(out.LateWindows), newWindowsHint(out.LateWindows))
	return newResult(out, out.resultText, images, "")
}

type screenshotOut struct {
	resultText
	Shot *shotOut `json:"shot"`
	// Windows that the input of earlier acts opened after those acts had returned, which no result has reported yet.
	LateWindows []newWindowOut `json:"late_windows,omitempty"`
}

// parseCapture reads act's capture argument: "changes", the default, "window", "none", or a region object.
func parseCapture(raw json.RawMessage) (mode string, region *regionArgs, err error) {
	if len(raw) == 0 || string(raw) == "null" {
		return "changes", nil, nil
	}
	var mode2 string
	if json.Unmarshal(raw, &mode2) == nil {
		if mode2 != "changes" && mode2 != "window" && mode2 != "none" {
			return "", nil, invalidf("capture must be \"changes\", \"window\", \"none\" or a region {shot,x,y,width,height}, not %q", mode2)
		}
		return mode2, nil, nil
	}
	// The region may also be wrapped, as {"region": {...}}.
	var wrapped struct {
		Region json.RawMessage `json:"region"`
	}
	if strictUnmarshal(raw, &wrapped) == nil && len(wrapped.Region) > 0 && string(wrapped.Region) != "null" {
		r, err := parseRegion(wrapped.Region, "capture.region")
		if err != nil {
			return "", nil, err
		}
		return "region", r, nil
	}
	r, err := parseRegion(raw, "capture")
	if err != nil {
		var te *toolError
		errors.As(err, &te)
		return "", nil, invalidf("capture must be \"changes\", \"window\", \"none\" or a region {shot,x,y,width,height}: %s", te.Message)
	}
	return "region", r, nil
}
