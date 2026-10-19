package server

import (
	"context"
	"errors"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const (
	// How many new windows from the target an act returns a shot of.
	newWindowShots = 3
	// How long a new window gets at most to draw what it opened with before its shot, and how quiet it then has to be.
	newWindowSettle  = time.Second
	newWindowQuietMs = 200
	// The time before the deadline that the grace period leaves for listing the new windows.
	newWindowListing = 250 * time.Millisecond
)

// newWindowGrace waits until the grace period for new windows ends, at latest at the time given, or until the call is
// cancelled.
func (s *Server) newWindowGrace(ctx context.Context, until, latest time.Time) {
	if latest.Before(until) {
		until = latest
	}
	wait := time.Until(until)
	if wait <= 0 {
		return
	}
	timer := time.NewTimer(wait)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}

// newWindowShots takes a shot of each new window that came from the target, up to newWindowShots of them in all the
// lists, within the deadline, so the agent can act in it without another call. It returns their images.
func (s *Server) newWindowShots(ctx context.Context, end time.Time, lists ...[]newWindowOut) [][]byte {
	var images [][]byte
	for _, windows := range lists {
		for i := range windows {
			w := &windows[i]
			if !w.fromTarget || len(images) == newWindowShots {
				continue
			}
			sh, png, err := s.newWindowShot(ctx, w.Window, w.StableID, end)
			if err != nil {
				continue
			}
			s.shots.returned(sh)
			w.Shot = shotInfo(sh)
			images = append(images, png)
		}
	}
	return images
}

// newWindowShot captures a new window once it has drawn what it opened with. A window on a hidden workspace draws
// only with the frame callbacks that the plugin sends during a wait. One that is starved, waiting for them, gets them
// from the capture's own wait, which tells whether it drew, so that its shot says when it may show an older frame.
func (s *Server) newWindowShot(ctx context.Context, window, stableID string, end time.Time) (*shot, []byte, error) {
	ctx, cancel := context.WithDeadline(ctx, end)
	defer cancel()
	st, err := s.openState(ctx, window, stableID)
	if err != nil {
		return nil, nil, err
	}
	// Half of the capture's reserve is left for the capture.
	if settle := min(newWindowSettle, time.Until(end)-captureReserve/2); !st.Starved && settle > newWindowQuietMs*time.Millisecond {
		_, err := s.wait(ctx, window, hypr.WaitArgs{Since: *st.Commits, QuietMs: newWindowQuietMs, DeadlineMs: int(settle.Milliseconds()), ID: s.waitID()})
		var pe *hypr.Error
		switch {
		case ctx.Err() != nil:
			return nil, nil, ctx.Err()
		case errors.As(err, &pe) && pe.Code == "window_gone":
			return nil, nil, err
		}
	}
	return s.capture(ctx, window, stableID, nil)
}

// closedSource describes the window that the target came from, as the plugin found it when the target mapped, once the
// target has closed: the agent works in it, since the plugin found the target to come from it, and goes on there. A
// target that the user's own input opened has none, and nor has one whose source is gone or no longer open, which the
// plugin's stable_id tells from a later window at its address.
func (s *Server) closedSource(ctx context.Context, src *hypr.Source, target string, windows []hypr.Window) *sourceOut {
	if src == nil || src.Reason == "user_input" || src.Window == target || src.StableID == "" {
		return nil
	}
	w := findWindow(windows, src.Window)
	if w == nil || w.StableID != src.StableID {
		return nil
	}
	return &sourceOut{Window: w.Address, StableID: w.StableID, Class: w.Class, Title: w.Title, Workspace: w.Workspace.Name, Visible: s.visibleWorkspaces(ctx)[w.Workspace.ID]}
}

// The window that a closed target came from gets time to answer the close before its shot, as a page draws the file
// that a file chooser gave it. In the benchmark VM at 2560×1440, Chromium drew the file from the portal's chooser only
// once it got frame callbacks, which a hidden window gets from hyprmasq's waits: 4 to 16 ms after the first one, or once
// by itself 44 ms after the chooser closed. It then drew every frame while the page animated the upload: for 330 ms
// when the callbacks started 0.4 or 1.5 s after the close, and for 950 ms when they started with it.
const (
	// How long the window gets to start drawing once the wait sends it frame callbacks: ten times the 44 ms in which
	// Chromium had the chooser's file, for an app that first reads the file it got, and the time that act watches a
	// window that settled for drawing again (see watchForRedraw). A window that draws nothing spends it instead of the
	// 200 ms of quiet of before.
	sourceExpectMs = 500
	// The most that the wait takes: the longest redraw, 950 ms, and a quiet time of 200 ms after it, with room to spare.
	// In the benchmark run at 1920×1080 that lost a click after an upload, Chromium still drew after the wait of up to a
	// second of before: between the two images of the shot, twice, so that the shot kept no detail image.
	sourceSettleMax = 1500 * time.Millisecond
)

// captureSource captures the window that a closed target came from once it has answered the close: after a wait that
// sends it frame callbacks, for its first redraw up to sourceExpectMs, and if it drew, for quietMs of quiet, within
// sourceSettleMax and the deadline, which leaves half of the capture's reserve to the capture. Animations, such as a
// caret that blinks, neither end the change phase nor keep the quiet phase from ending, and the shot records them. A
// starved window gets frame callbacks from the capture's own wait.
func (s *Server) captureSource(ctx context.Context, window, stableID string, quietMs int, end time.Time) (*shot, []byte, error) {
	ctx, cancel := context.WithDeadline(ctx, end)
	defer cancel()
	st, err := s.openState(ctx, window, stableID)
	if err != nil {
		return nil, nil, err
	}
	var animated []hypr.IntRect
	if settle := min(sourceSettleMax, time.Until(end)-captureReserve/2).Milliseconds(); !st.Starved && settle >= 1 {
		reply, err := s.wait(ctx, window, hypr.WaitArgs{Since: *st.Commits, ExpectMs: int(min(sourceExpectMs, settle)), QuietMs: quietMs, DeadlineMs: int(settle),
			ID: s.waitID(), IgnoreAnimations: true})
		var pe *hypr.Error
		switch {
		case ctx.Err() != nil:
			return nil, nil, ctx.Err()
		case errors.As(err, &pe) && pe.Code == "window_gone":
			return nil, nil, err
		case err == nil && deref(reply.SocketID) == deref(st.SocketID):
			animated = reply.Animated
		}
	}
	sh, png, err := s.capture(ctx, window, stableID, nil)
	if err == nil {
		sh.Animated = animated
	}
	return sh, png, err
}
