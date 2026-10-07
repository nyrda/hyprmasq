package server

import (
	"bytes"
	"context"
	"fmt"
	"math"
	"strings"
	"unicode"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const launchVariable = "HYPRMASQ_LAUNCH"

// launchToken returns the HYPRMASQ_LAUNCH token in the process's environment, or "".
func (s *Server) launchToken(pid int) string {
	if pid <= 0 {
		return ""
	}
	environ, err := s.cfg.Environ(pid)
	if err != nil {
		return ""
	}
	return environValue(environ, launchVariable)
}

// environValue returns the value of the variable in the contents of /proc/<pid>/environ, or "".
func environValue(environ []byte, name string) string {
	for _, entry := range bytes.Split(environ, []byte{0}) {
		if value, ok := bytes.CutPrefix(entry, []byte(name+"=")); ok {
			return string(value)
		}
	}
	return ""
}

// launched tells whether the process carries the token of a launch by this server.
func (s *Server) launched(pid int) bool {
	token := s.launchToken(pid)
	if token == "" {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.tokens[token]
}

// listWindows lists the windows, and forgets the shots of windows that have closed.
func (s *Server) listWindows(ctx context.Context) ([]hypr.Window, error) {
	mark := s.shots.mark()
	windows, err := s.hypr.Clients(ctx)
	if err == nil {
		s.shots.dropClosed(windows, mark)
	}
	return windows, err
}

func windowKey(w hypr.Window) string {
	return w.Address + "/" + w.StableID
}

// newWindows lists the windows in after that are not in before. A window at a reused address is new.
func (s *Server) newWindows(before, after []hypr.Window, targetPID int) []newWindowOut {
	known := map[string]bool{}
	for _, w := range before {
		known[windowKey(w)] = true
	}
	windows := []newWindowOut{}
	for _, w := range after {
		if known[windowKey(w)] {
			continue
		}
		windows = append(windows, newWindowOut{Window: w.Address, StableID: w.StableID, Class: w.Class, Title: w.Title, PID: w.PID,
			SamePID: targetPID > 0 && w.PID == targetPID, Launched: s.launched(w.PID)})
	}
	return windows
}

type sizeOut struct {
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

type windowLeaseOut struct {
	Held       bool `json:"held"`
	RemainingS int  `json:"remaining_s"`
	// Whether this server session holds it.
	Mine bool `json:"mine"`
}

// capabilitiesOut is what act can use of the plugin's capabilities. Its dnd is left out: drag-and-drop works only from
// the active window, which shares the user's keyboard focus and so lacks hold, which act needs for a drag.
type capabilitiesOut struct {
	Lease    bool   `json:"lease"`
	Hold     bool   `json:"hold"`
	Keyboard string `json:"keyboard"`
}

type windowOut struct {
	Window    string  `json:"window"`
	StableID  string  `json:"stable_id"`
	Class     string  `json:"class"`
	Title     string  `json:"title"`
	PID       int     `json:"pid"`
	Workspace string  `json:"workspace"`
	Visible   bool    `json:"visible"`
	Size      sizeOut `json:"size"`
	// The window has not caught up with its size, so its shots show an older frame.
	Resizing     bool             `json:"resizing"`
	XWayland     bool             `json:"xwayland"`
	Launched     bool             `json:"launched"`
	Lease        *windowLeaseOut  `json:"lease"`
	Capabilities *capabilitiesOut `json:"capabilities"`
}

func (s *Server) describeWindow(ctx context.Context, w hypr.Window, visible map[int]bool) windowOut {
	out := windowOut{Window: w.Address, StableID: w.StableID, Class: w.Class, Title: w.Title, PID: w.PID,
		Workspace: w.Workspace.Name, Visible: visible[w.Workspace.ID], XWayland: w.XWayland, Launched: s.launched(w.PID)}
	if len(w.Size) == 2 {
		out.Size = sizeOut{float64(w.Size[0]), float64(w.Size[1])}
	}
	if st, err := s.hypr.State(ctx, w.Address); err == nil {
		out.Size = sizeOut(st.Size)
		out.Resizing = st.Resizing
		out.Capabilities = &capabilitiesOut{Lease: st.Capabilities.Lease, Hold: st.Capabilities.Hold, Keyboard: st.Capabilities.Keyboard}
		if st.Lease != nil && st.Lease.Held {
			out.Lease = &windowLeaseOut{Held: true, RemainingS: int(math.Round(float64(st.Lease.RemainingMs) / 1000)), Mine: st.Lease.Owner == s.owner}
		}
	}
	return out
}

type windowsArgs struct {
	Match string `json:"match"`
}

// matches tells whether the window's class or title contains match, ignoring case.
func matches(w hypr.Window, match string) bool {
	match = foldCase(match)
	return strings.Contains(foldCase(w.Class), match) || strings.Contains(foldCase(w.Title), match)
}

// foldCase maps every rune to the smallest rune that Unicode simple case folding takes as equal to it, as
// strings.EqualFold compares them. Lowercasing alone would keep ς and σ apart, which both fold to Σ.
func foldCase(s string) string {
	return strings.Map(func(r rune) rune {
		smallest := r
		for f := unicode.SimpleFold(r); f != r; f = unicode.SimpleFold(f) {
			smallest = min(smallest, f)
		}
		return smallest
	}, s)
}

func (s *Server) windowsTool(ctx context.Context, c *call) *mcp.CallToolResult {
	var args windowsArgs
	if err := c.decode(&args); err != nil {
		return simpleError(err)
	}
	ctx, cancel := context.WithTimeout(ctx, requestTimeout)
	defer cancel()
	if err := s.checkPlugin(ctx); err != nil {
		return simpleError(err)
	}
	clients, err := s.listWindows(ctx)
	if err != nil {
		return simpleError(err)
	}
	visible := map[int]bool{}
	if monitors, err := s.hypr.Monitors(ctx); err == nil {
		for _, m := range monitors {
			visible[m.ActiveWorkspace.ID] = true
			if m.SpecialWorkspace.ID != 0 {
				visible[m.SpecialWorkspace.ID] = true
			}
		}
	}
	windows := []windowOut{}
	var lines []string
	for _, w := range clients {
		if !matches(w, args.Match) {
			continue
		}
		out := s.describeWindow(ctx, w, visible)
		windows = append(windows, out)
		lines = append(lines, windowLine(out))
	}
	switch {
	case len(clients) == 0:
		lines = append(lines, "no windows")
	case len(lines) == 0:
		lines = append(lines, fmt.Sprintf("no window has %q in its class or title, of %d windows", args.Match, len(clients)))
	}
	structured := map[string]any{"windows": windows}
	if len(windows) == 0 {
		// The structured content carries everything the text says, also why the list is empty.
		structured["summary"] = lines[0]
	}
	return &mcp.CallToolResult{
		Content:           []mcp.Content{&mcp.TextContent{Text: strings.Join(lines, "\n")}},
		StructuredContent: structured,
	}
}

// windowLine describes a window in one line. It leaves out what an agent rarely needs: the pid, and the capabilities of a
// window that has them all, as most windows do.
func windowLine(w windowOut) string {
	where := "hidden"
	if w.Visible {
		where = "visible"
	}
	parts := []string{
		w.Window,
		fmt.Sprintf("%s %q", w.Class, w.Title),
		fmt.Sprintf("workspace %s (%s)", w.Workspace, where),
		fmt.Sprintf("%s×%s", formatNumber(w.Size.Width), formatNumber(w.Size.Height)),
	}
	if w.Resizing {
		parts = append(parts, "resizing (not yet redrawn at its new size)")
	}
	if w.XWayland {
		parts = append(parts, "xwayland")
	}
	if w.Launched {
		parts = append(parts, "launched")
	}
	if w.Lease != nil {
		owner := "another session"
		if w.Lease.Mine {
			owner = "this session"
		}
		parts = append(parts, fmt.Sprintf("lease %d s (%s)", w.Lease.RemainingS, owner))
	}
	switch c := w.Capabilities; {
	case c == nil:
		parts = append(parts, "capabilities unknown")
	case !c.Lease || !c.Hold || c.Keyboard != "yes":
		var caps []string
		for _, cap := range []struct {
			name string
			on   bool
		}{{"lease", c.Lease}, {"hold", c.Hold}} {
			if cap.on {
				caps = append(caps, cap.name)
			}
		}
		caps = append(caps, "keyboard "+c.Keyboard)
		parts = append(parts, "capabilities "+strings.Join(caps, ", "))
	}
	return strings.Join(parts, " · ")
}
