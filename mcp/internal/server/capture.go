package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
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
	if sh == nil {
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
	// How long a capture waits at most for a window to catch up with a resize, within the time that its context leaves
	// before the capture's reserve.
	catchUpMax = 1500 * time.Millisecond
	// How long the window then has to stay quiet, so that the shot shows the frame it settled on.
	catchUpQuietMs = 100
)

// catchUp waits for a window that has not caught up with a resize to draw a frame of its size, and for a short quiet.
// The plugin's wait does not end quiet before, and sends a hidden window the frame callbacks it may need to draw. A
// window that does not catch up in time is captured anyway, and its shot says so.
func (s *Server) catchUp(ctx context.Context, window string, st hypr.State) error {
	limit := catchUpMax
	if deadline, ok := ctx.Deadline(); ok {
		limit = min(limit, time.Until(deadline)-captureReserve)
	}
	if limit < catchUpQuietMs*time.Millisecond || st.Commits == nil {
		return nil
	}
	_, err := s.wait(ctx, window, hypr.WaitArgs{Since: *st.Commits, QuietMs: catchUpQuietMs, DeadlineMs: int(limit.Milliseconds()), ID: s.waitID()})
	if ctx.Err() != nil {
		return ctx.Err()
	}
	var pe *hypr.Error
	if errors.As(err, &pe) && pe.Code == "window_gone" {
		return err
	}
	// Any other failure leaves the shot to say whether the window caught up.
	return nil
}

func (s *Server) waitID() string {
	return fmt.Sprintf("%s-w%d", s.owner, s.waitSeq.Add(1))
}

// capture takes a shot of the window with the stable_id, or a zoom into it, and remembers it. A window that has not
// caught up with a resize gets a short time to do so first. A new window at the address is not captured, and a zoom
// only while the window has the size of the shot it was read from.
func (s *Server) capture(ctx context.Context, window, stableID string, z *zoom) (*shot, []byte, error) {
	if err := os.MkdirAll(s.shotDir, 0o700); err != nil {
		return nil, nil, err
	}
	var region *hypr.IntRect
	if z != nil {
		region = &z.rect
	}
	caughtUp := false
	// A resize between the state and the screenshot would record the wrong size, so the capture is repeated.
	for attempt := 1; ; {
		pre, err := s.openState(ctx, window, stableID)
		if err != nil {
			return nil, nil, err
		}
		if z != nil && pre.Size != z.from.Size {
			return nil, nil, &zoomResized{from: z.from, size: pre.Size}
		}
		if pre.Resizing && !caughtUp {
			caughtUp = true
			if err := s.catchUp(ctx, window, pre); err != nil {
				return nil, nil, err
			}
			continue
		}
		id := s.shots.newID()
		path := filepath.Join(s.shotDir, id+".png")
		reply, err := s.hypr.Screenshot(ctx, window, path, region, s.cfg.MaxImageSize)
		if err != nil {
			os.Remove(path)
			var pe *hypr.Error
			if region != nil && errors.As(err, &pe) && pe.Code == "invalid_arguments" {
				// The region is whole and not empty, so the plugin refuses it only when it lies outside the window and
				// its popups, as when it showed a popup that has closed since.
				return nil, nil, &toolError{Code: "stale_shot", Message: "the region shows nothing of the window now; a popup it showed may have closed: " + pe.Message,
					Hint: hintScreenshot}
			}
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
		// A resize that started after the state was read.
		if reply.Resizing && !caughtUp {
			os.Remove(path)
			caughtUp = true
			if err := s.catchUp(ctx, window, post); err != nil {
				return nil, nil, err
			}
			continue
		}
		png, err := os.ReadFile(path)
		if err != nil {
			return nil, nil, err
		}
		sh := &shot{ID: id, Window: window, StableID: pre.StableID, Size: post.Size, Frame: reply.Region,
			Width: reply.Width, Height: reply.Height, Scale: reply.Scale, Resizing: reply.Resizing, Path: path}
		switch {
		case reply.Commits != nil:
			sh.Commits, sh.SocketID = *reply.Commits, deref(reply.SocketID)
		case pre.Commits != nil:
			sh.Commits, sh.SocketID = *pre.Commits, deref(pre.SocketID)
		}
		if sh.Scale <= 0 || sh.Width <= 0 || sh.Height <= 0 {
			os.Remove(path)
			return nil, nil, &toolError{Code: "internal", Message: fmt.Sprintf("the plugin returned an empty screenshot (%d×%d at scale %v)", sh.Width, sh.Height, sh.Scale)}
		}
		s.shots.add(sh)
		return sh, png, nil
	}
}

type screenshotArgs struct {
	Window string          `json:"window" args:"required"`
	Region json.RawMessage `json:"region"`
}

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
	st, err := s.openState(ctx, args.Window, "")
	if err != nil {
		return simpleError(err)
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
	out := &screenshotOut{Shot: shotInfo(sh)}
	out.Summary = shotText(out.Shot)
	if asked != nil {
		out.Summary += fmt.Sprintf(" · zoom of %s at %s,%s %s×%s", asked.Shot, formatNumber(asked.X), formatNumber(asked.Y), formatNumber(asked.Width), formatNumber(asked.Height))
	}
	out.Summary += fmt.Sprintf(" · window %s %s", args.Window, formatSize(sh.Size))
	out.Hint = resizingHint(out.Shot)
	return newResult(out, out.resultText, png, "")
}

type screenshotOut struct {
	resultText
	Shot *shotOut `json:"shot"`
}

// parseCapture reads act's capture argument: "window", "none", or a region object.
func parseCapture(raw json.RawMessage) (mode string, region *regionArgs, err error) {
	if len(raw) == 0 || string(raw) == "null" {
		return "window", nil, nil
	}
	var mode2 string
	if json.Unmarshal(raw, &mode2) == nil {
		if mode2 != "window" && mode2 != "none" {
			return "", nil, invalidf("capture must be \"window\", \"none\" or a region {shot,x,y,width,height}, not %q", mode2)
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
		return "", nil, invalidf("capture must be \"window\", \"none\" or a region {shot,x,y,width,height}: %s", te.Message)
	}
	return "region", r, nil
}
