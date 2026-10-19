package server

import (
	"context"
	"fmt"
	"regexp"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const (
	closeWait = 2 * time.Second
	closePoll = 50 * time.Millisecond
)

var stableIDPattern = regexp.MustCompile(`^[0-9a-f]{1,16}$`)

// closeTool runs the close tool.
func (s *Server) closeTool(ctx context.Context, c *call) *mcp.CallToolResult {
	window, err := c.windowArg()
	if err != nil {
		return simpleError(err)
	}
	ctx, cancel := context.WithTimeout(ctx, closeWait+requestTimeout)
	defer cancel()
	if err := s.checkPlugin(ctx); err != nil {
		return simpleError(err)
	}
	before, err := s.listWindows(ctx)
	if err != nil {
		return simpleError(err)
	}
	target := findWindow(before, window)
	if target == nil {
		return simpleError(&toolError{Code: "window_gone", Message: fmt.Sprintf("there is no window %s", window), Hint: hintWindows})
	}
	// Hyprland selects the window when it runs the dispatcher, by its stable_id, which it never gives to another
	// window. So a window that took over the address after the target closed is not closed.
	if !stableIDPattern.MatchString(target.StableID) {
		return simpleError(&toolError{Code: "internal", Message: fmt.Sprintf("Hyprland lists window %s with the stable_id %q, which is not hexadecimal", window, target.StableID)})
	}
	start := time.Now()
	if err := s.hypr.Dispatch(ctx, fmt.Sprintf(`hl.dsp.window.close({ window = "stableid:%s" })`, target.StableID)); err != nil {
		return simpleError(err)
	}
	// The lease ends with close, whichever session holds it, also when the application asks for confirmation instead
	// of closing.
	_, err = s.hypr.LeaseRelease(ctx, window, "")
	s.leaseAnswered(window, false, err)

	closed := false
	after := before
	for {
		clients, err := s.listWindows(ctx)
		if err != nil {
			return simpleError(err)
		}
		after = clients
		if w := findWindow(clients, window); w == nil || w.StableID != target.StableID {
			closed = true
			break
		}
		if time.Since(start) >= closeWait {
			break
		}
		select {
		case <-ctx.Done():
			return simpleError(ctx.Err())
		case <-time.After(closePoll):
		}
	}
	elapsed := time.Since(start).Milliseconds()
	out := &closeOut{Closed: closed, ElapsedMs: elapsed, NewWindows: s.newWindows(ctx, before, after, window, target.PID, "")}
	out.Summary = fmt.Sprintf("closed %s after %d ms", window, elapsed)
	if closed {
		s.shots.dropWindow(window)
	} else {
		out.Summary = fmt.Sprintf("%s is still open after %d ms", window, elapsed)
	}
	if t := newWindowsText(out.NewWindows); t != "" {
		out.Summary += " · " + t
	}
	switch {
	case !closed && len(out.NewWindows) > 0:
		out.Hint = "The application may be asking for confirmation in the new window. Take a screenshot of it."
	case !closed:
		out.Hint = "The application may be asking for confirmation inside the window, or closing slowly. Take a screenshot of it."
	case !target.Floating:
		out.Hint = neighboursHint(target, after)
	}
	return newResult(out, out.resultText, nil, "")
}

// neighboursHint names the tiled windows that share a closed tiled window's workspace, which take up its space.
func neighboursHint(closed *hypr.Window, windows []hypr.Window) string {
	var neighbours []string
	for _, w := range windows {
		if w.Workspace.ID == closed.Workspace.ID && !w.Floating && windowKey(w) != windowKey(*closed) {
			neighbours = append(neighbours, w.Address)
		}
	}
	if len(neighbours) == 0 {
		return ""
	}
	return fmt.Sprintf("Closing a tiled window resizes the tiled windows on its workspace (%s), so their earlier shots are stale: take a new screenshot before clicking in one.",
		strings.Join(neighbours, ", "))
}

type closeOut struct {
	resultText
	Closed     bool           `json:"closed"`
	ElapsedMs  int64          `json:"elapsed_ms"`
	NewWindows []newWindowOut `json:"new_windows"`
}
