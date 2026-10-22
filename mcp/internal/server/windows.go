package server

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"math"
	"slices"
	"strings"
	"time"
	"unicode"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// environValue returns the value of the variable in the contents of /proc/<pid>/environ, or "".
func environValue(environ []byte, name string) string {
	for _, entry := range bytes.Split(environ, []byte{0}) {
		if value, ok := bytes.CutPrefix(entry, []byte(name+"=")); ok {
			return string(value)
		}
	}
	return ""
}

// launchedWindows returns the tokens of this server's launches by the windowKey of their windows, which the plugin
// found by their process or a parent of it. The address alone could be one that Hyprland gave to another window since.
// It forgets the tokens of the launches that have ended: their process has exited, and none of their windows is left.
// The process stays while windows can come, as hyprmasq-reaper stays while any process of the command runs.
func (s *Server) launchedWindows(ctx context.Context) map[string]string {
	windows, err := s.hypr.Launched(ctx)
	if err != nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	launched := map[string]string{}
	for _, w := range windows {
		if _, ok := s.tokens[w.Token]; ok {
			launched[windowKey(hypr.Window{Address: w.Address, StableID: w.StableID})] = w.Token
		}
	}
	for token, pid := range s.tokens {
		if pid > 0 && !slices.ContainsFunc(windows, func(w hypr.LaunchedWindow) bool { return w.Token == token }) && !s.cfg.Alive(pid) {
			delete(s.tokens, token)
		}
	}
	return launched
}

// launchOf returns the token of the launch by this server that the window at the address in windows belongs to, or "".
// It asks the plugin only when this server has launched something.
func (s *Server) launchOf(ctx context.Context, windows []hypr.Window, address string) string {
	s.mu.Lock()
	launches := len(s.tokens)
	s.mu.Unlock()
	if launches == 0 {
		return ""
	}
	for _, w := range windows {
		if w.Address == address {
			return s.launchedWindows(ctx)[windowKey(w)]
		}
	}
	return ""
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

// newWindows lists the windows in after that are not in before, with where they opened and where the plugin found
// them to come from. A window at a reused address is new. target is the window that the call is about, targetPID its
// process and targetLaunch the token of its launch by this server, or "".
func (s *Server) newWindows(ctx context.Context, before, after []hypr.Window, target string, targetPID int, targetLaunch string) []newWindowOut {
	known := map[string]bool{}
	for _, w := range before {
		known[windowKey(w)] = true
	}
	windows := []newWindowOut{}
	var launched map[string]string
	for _, w := range after {
		if known[windowKey(w)] {
			continue
		}
		if launched == nil {
			launched = s.launchedWindows(ctx)
		}
		token := launched[windowKey(w)]
		windows = append(windows, newWindowOut{Window: w.Address, StableID: w.StableID, Class: w.Class, Title: w.Title, PID: w.PID,
			SamePID: targetPID > 0 && w.PID == targetPID, Launched: token != "", Workspace: w.Workspace.Name, workspaceID: w.Workspace.ID,
			fromTarget: token != "" && token == targetLaunch})
	}
	if len(windows) == 0 {
		return windows
	}
	visible := s.visibleWorkspaces(ctx)
	focused, _ := s.hypr.ActiveWindow(ctx)
	for i := range windows {
		w := &windows[i]
		w.Visible, w.Focused = visible[w.workspaceID], w.Window == focused
		// state opens no socket, so it leaves windows of other apps undriven.
		if st, err := s.hypr.State(ctx, w.Window); err == nil && st.StableID == w.StableID && st.Source != nil {
			w.Source, w.Placed, w.NotPlaced = st.Source.Window, st.Source.Placed, st.Source.Reason
			// A window that the plugin found the user's own input to have opened is theirs, also when it came from the
			// target.
			w.fromTarget = w.fromTarget || (st.Source.Window == target && st.Source.Reason != "user_input")
		}
		// Besides the source, only a launch of the target's tells: the process says too little, since xwayland-satellite
		// hosts every X11 app of its server in one, and another launch of this server is another app.
	}
	return windows
}

// visibleWorkspaces returns the ids of the workspaces that the monitors show, active and special ones.
func (s *Server) visibleWorkspaces(ctx context.Context) map[int]bool {
	visible := map[int]bool{}
	if monitors, err := s.hypr.Monitors(ctx); err == nil {
		for _, m := range monitors {
			visible[m.ActiveWorkspace.ID] = true
			if m.SpecialWorkspace.ID != 0 {
				visible[m.SpecialWorkspace.ID] = true
			}
		}
	}
	return visible
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

// capabilitiesOut is what act can use of the plugin's capabilities. Its dnd is the plugin's privateDnd: the active
// window, whose drags run with the user's pointer, shares the user's keyboard focus and so lacks hold, which act needs
// for a drag.
type capabilitiesOut struct {
	Lease    bool   `json:"lease"`
	Hold     bool   `json:"hold"`
	DnD      bool   `json:"dnd"`
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
	// The window whose input in an earlier act opened this one after that act had returned, while no earlier result has
	// reported it: this one does, with a shot, unless it is past the limit of shots.
	OpenedByInput string `json:"opened_by_input,omitempty"`
	// Its shot in this result: of the one window listed, or of a late window.
	Shot *shotOut `json:"shot,omitempty"`
	// This result reports it as a late window, with its shot.
	reported bool
}

// describeWindow describes a window of the list, whose windowKeys open holds.
func (s *Server) describeWindow(ctx context.Context, w hypr.Window, visible map[int]bool, launched map[string]string, open map[string]bool) windowOut {
	out := windowOut{Window: w.Address, StableID: w.StableID, Class: w.Class, Title: w.Title, PID: w.PID,
		Workspace: w.Workspace.Name, Visible: visible[w.Workspace.ID], XWayland: w.XWayland, Launched: launched[windowKey(w)] != ""}
	if len(w.Size) == 2 {
		out.Size = sizeOut{float64(w.Size[0]), float64(w.Size[1])}
	}
	if st, err := s.hypr.State(ctx, w.Address); err == nil {
		out.Size = sizeOut(st.Size)
		out.Resizing = st.Resizing
		out.Capabilities = &capabilitiesOut{Lease: st.Capabilities.Lease, Hold: st.Capabilities.Hold, DnD: st.Capabilities.PrivateDnD, Keyboard: st.Capabilities.Keyboard}
		if st.Lease != nil && st.Lease.Held {
			out.Lease = &windowLeaseOut{Held: true, RemainingS: int(math.Round(float64(st.Lease.RemainingMs) / 1000)), Mine: st.Lease.Owner == s.owner}
		}
		if st.StableID == w.StableID {
			out.OpenedByInput = s.lateFrom(w, st.Source, open)
		}
	}
	return out
}

type windowsArgs struct {
	Match  string `json:"match"`
	WaitMs *int   `json:"wait_ms"`
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

// windowsOut is the structured content of windows. Its summary has the lines of the text after those of the windows.
type windowsOut struct {
	resultText
	Windows []windowOut `json:"windows"`
	// Windows that the input of earlier acts opened after those acts had returned, which no result had reported.
	LateWindows []newWindowOut  `json:"late_windows,omitempty"`
	Wait        *windowsWaitOut `json:"wait,omitempty"`
}

// windowsWaitOut is how the wait of wait_ms ended: found when a window that it waits for was there, at once or once it
// had mapped, timeout when none came within wait_ms, and nothing when no window can come: without match, when no act
// of the session within lateWatch sent input that can open a window.
type windowsWaitOut struct {
	Ended     string `json:"ended"`
	ElapsedMs int    `json:"elapsed_ms"`
}

const (
	// The longest wait_ms of windows. The portal's file chooser mapped 1.5 to 1.8 s after its click in a benchmark, and a
	// dialog whose app or portal has to start first takes a few seconds more. A wait in vain costs the agent no more than
	// this, which stays far within the tool timeouts of clients, such as Codex's 60 s.
	maxWindowsWait = 10 * time.Second
	// How often windows lists the windows while it waits, when Hyprland's event socket cannot be read.
	windowsPoll = 100 * time.Millisecond
)

// A mapWatch tells when a window may have mapped, and with titles when one may have changed its title: on Hyprland's
// events, or every windowsPoll when its event socket cannot be read or has dropped the connection.
type mapWatch struct {
	// The subscription's events, and while it lasts the same channel in live, which is nil once it has ended.
	events, live <-chan hypr.Event
	stop         context.CancelFunc
	titles       bool
}

// watchMaps subscribes to Hyprland's events. A caller subscribes before it lists the windows, so that no window maps
// unseen between the two.
func (s *Server) watchMaps(ctx context.Context, titles bool) *mapWatch {
	ctx, stop := context.WithCancel(ctx)
	events, err := s.hypr.Events(ctx)
	if err != nil {
		events = nil
	}
	return &mapWatch{events: events, live: events, stop: stop, titles: titles}
}

// next waits for an event that may have changed the windows, or for the next poll, and tells whether one came before
// until and before ctx ended.
func (m *mapWatch) next(ctx context.Context, until time.Time) bool {
	limit := time.NewTimer(time.Until(until))
	defer limit.Stop()
	for {
		var poll <-chan time.Time
		if m.live == nil {
			poll = time.After(windowsPoll)
		}
		select {
		case <-ctx.Done():
			return false
		case <-limit.C:
			return false
		case <-poll:
			return true
		case e, ok := <-m.live:
			switch {
			case !ok:
				// Events may have been lost, so the windows are listed again, and from now on every windowsPoll.
				m.live = nil
				return true
			case e.Name == "openwindow", m.titles && e.Name == "windowtitlev2":
				return true
			}
		}
	}
}

// close ends the subscription, and returns once its reader has stopped.
func (m *mapWatch) close() {
	m.stop()
	if m.events != nil {
		for range m.events {
		}
	}
}

// windowsTool runs the windows tool.
func (s *Server) windowsTool(ctx context.Context, c *call) *mcp.CallToolResult {
	var args windowsArgs
	if err := c.decode(&args); err != nil {
		return simpleError(err)
	}
	waitMs, err := intArg(args.WaitMs, 0, 0, int(maxWindowsWait/time.Millisecond), "wait_ms")
	if err != nil {
		return simpleError(err)
	}
	start := time.Now()
	wait := time.Duration(waitMs) * time.Millisecond
	ctx, cancel := context.WithTimeout(ctx, requestTimeout+wait)
	defer cancel()
	if err := s.checkPlugin(ctx); err != nil {
		return simpleError(err)
	}
	var maps *mapWatch
	if wait > 0 {
		maps = s.watchMaps(ctx, args.Match != "")
		defer maps.close()
	}
	clients, err := s.listWindows(ctx)
	if err != nil {
		return simpleError(err)
	}
	listed := func() []hypr.Window {
		return slices.DeleteFunc(slices.Clone(clients), func(w hypr.Window) bool { return !matches(w, args.Match) })
	}
	before := openKeys(clients)
	// The windows that the input of earlier acts opened after they had returned come with this result, as with the next
	// act or screenshot, so that the agent sees them before it acts again.
	late := s.claimLate(ctx, clients)
	var waited *windowsWaitOut
	if wait > 0 {
		found := func() bool {
			if args.Match != "" {
				return len(listed()) > 0
			}
			return len(late) > 0
		}
		waited = &windowsWaitOut{Ended: "found"}
		switch {
		case found():
		case args.Match == "" && !s.watching():
			waited.Ended = "nothing"
		default:
			// Hyprland accepts the event socket's connection on its main thread, possibly in the same pass in which it
			// answered the first listing, after it, so a window that mapped between the two sent its event to no one. The
			// next listing is answered in a later pass, after the accept, so the windows are listed once more at once:
			// every window that maps after that listing sends its event.
			for until, again := start.Add(wait), true; !found(); again = false {
				if !again && !maps.next(ctx, until) {
					if ctx.Err() != nil {
						s.keepLate(late)
						return simpleError(ctx.Err())
					}
					waited.Ended = "timeout"
					break
				}
				if clients, err = s.listWindows(ctx); err != nil {
					s.keepLate(late)
					return simpleError(err)
				}
				late = append(late, s.claimLate(ctx, clients)...)
			}
			waited.ElapsedMs = int(time.Since(start).Milliseconds())
		}
	}
	end, _ := ctx.Deadline()
	var images [][]byte
	out := &windowsOut{Windows: []windowOut{}, Wait: waited}
	out.LateWindows, images = s.lateShots(ctx, end, late)
	shown := listed()
	visible := s.visibleWorkspaces(ctx)
	launched := s.launchedWindows(ctx)
	open := openKeys(clients)
	for _, w := range shown {
		o := s.describeWindow(ctx, w, visible, launched, open)
		if i := slices.IndexFunc(out.LateWindows, func(l newWindowOut) bool { return l.Window == w.Address && l.StableID == w.StableID }); i >= 0 {
			o.OpenedByInput, o.Shot, o.reported = out.LateWindows[i].Source, out.LateWindows[i].Shot, true
		}
		out.Windows = append(out.Windows, o)
	}
	var lines, hints []string
	// The one window listed comes with its shot, as from screenshot, since that is what an agent takes next.
	if len(out.Windows) == 1 && out.Windows[0].Shot == nil {
		o, w := &out.Windows[0], shown[0]
		// A late window past the limit of shots, whose claim keepLate released, is claimed again for this shot, which
		// reports it as a late window, unless another call has claimed or reported it meanwhile.
		var reclaimed *newWindowOut
		if i := slices.IndexFunc(late, func(l newWindowOut) bool { return l.Window == w.Address && l.StableID == w.StableID }); i >= 0 {
			reclaimed = s.reclaimLate(late[i])
		}
		var sh *shot
		var png []byte
		if before[windowKey(w)] {
			sh, png, err = s.capture(ctx, w.Address, w.StableID, nil)
		} else {
			// It mapped while the call waited, so it gets the time to draw what it opened with, as a new window does.
			sh, png, err = s.newWindowShot(ctx, w.Address, w.StableID, end)
		}
		if err == nil {
			s.shots.returned(sh)
			o.Shot, o.reported = shotInfo(sh), o.OpenedByInput != ""
			// The capture gives a window that has not caught up with a resize time to draw first, so its shot is newer.
			o.Resizing = o.Shot.Resizing
			images = append([][]byte{png}, images...)
			if reclaimed != nil {
				// The late window's line and hints name it, with this shot.
				reclaimed.Shot = o.Shot
				out.LateWindows = append([]newWindowOut{*reclaimed}, out.LateWindows...)
			} else {
				lines = append(lines, fmt.Sprintf("%s of window %s", shotText(o.Shot), w.Address))
				hints = append(hints, frameHints(o.Shot))
			}
		} else {
			if reclaimed != nil {
				s.releaseLate([]newWindowOut{*reclaimed})
			}
			te := asToolError(err)
			hints = append(hints, joinHints(fmt.Sprintf("The shot of window %s failed: %s.", w.Address, te.Message), te.Hint))
		}
	}
	// A client that cancelled the call gets no result, so the late windows are left to a later one.
	if errors.Is(ctx.Err(), context.Canceled) {
		s.releaseLate(out.LateWindows)
		return simpleError(ctx.Err())
	}
	if len(out.Windows) == 1 && out.Windows[0].Shot != nil {
		// Its shot reports it, also when it is a late window whose claim keepLate released.
		s.settle(windowKey(shown[0]))
	}
	var leadLines []string
	for _, o := range out.Windows {
		leadLines = append(leadLines, windowLine(o))
	}
	var notes []string
	switch {
	case len(clients) == 0:
		notes = append(notes, "no windows")
	case len(out.Windows) == 0:
		notes = append(notes, fmt.Sprintf("no window has %q in its class or title, of %d windows", args.Match, len(clients)))
	}
	if note := windowsWaitText(waited, args.Match, waitMs); note != "" {
		notes = append(notes, note)
	}
	// A late window that the match leaves out of the list is named as in the other results.
	unlisted := slices.DeleteFunc(slices.Clone(out.LateWindows), func(l newWindowOut) bool {
		return slices.ContainsFunc(shown, func(w hypr.Window) bool { return w.Address == l.Window && w.StableID == l.StableID })
	})
	if text := lateWindowsText(unlisted); text != "" {
		notes = append(notes, text)
	}
	lines = append(notes, append(lines, newWindowLines(out.LateWindows, "late window")...)...)
	out.Summary = strings.Join(lines, "\n")
	if waited != nil && waited.Ended == "timeout" && args.Match == "" {
		hints = append(hints, "Without match, only a window that hyprmasq found your input to open ends the wait; give match to wait for a window by its class or title.")
	}
	out.Hint = joinHints(append(hints, lateHint(out.LateWindows), newWindowsHint(out.LateWindows))...)
	return newResult(out, out.resultText, images, strings.Join(leadLines, "\n"))
}

// windowsWaitText is the summary's line about the wait of wait_ms, or "" when it found a window at once or did not wait.
func windowsWaitText(w *windowsWaitOut, match string, waitMs int) string {
	switch {
	case w == nil:
		return ""
	case w.Ended == "nothing":
		return "did not wait: no act of yours in the last 2 minutes sent input that can open a window; give match to wait for another window"
	case w.Ended == "timeout" && match != "":
		return fmt.Sprintf("waited %d ms: no window with %q in its class or title mapped", waitMs, match)
	case w.Ended == "timeout":
		return fmt.Sprintf("waited %d ms: no window that your input opened mapped", waitMs)
	case w.ElapsedMs > 0:
		return fmt.Sprintf("found after waiting %d ms", w.ElapsedMs)
	}
	return ""
}

// windowLine describes a window in one line. It leaves out what an agent rarely needs: the pid, and the capabilities of a
// window that has them all, as most windows do. Of the others it names what they lack, so that nothing has to be
// inferred from what is missing from the line.
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
	switch {
	case w.OpenedByInput != "" && w.reported:
		parts = append(parts, "opened by your input to "+w.OpenedByInput)
	case w.OpenedByInput != "":
		parts = append(parts, fmt.Sprintf("opened by your input to %s, not yet reported", w.OpenedByInput))
	}
	if w.Lease != nil {
		owner := "another session"
		if w.Lease.Mine {
			owner = "this session"
		}
		parts = append(parts, fmt.Sprintf("lease %d s (%s)", w.Lease.RemainingS, owner))
	}
	if c := w.Capabilities; c == nil {
		parts = append(parts, "capabilities unknown")
	} else {
		var lacks []string
		for _, cap := range []struct {
			name string
			on   bool
		}{{"lease", c.Lease}, {"hold", c.Hold}, {"dnd", c.DnD}} {
			if !cap.on {
				lacks = append(lacks, cap.name)
			}
		}
		if len(lacks) > 0 {
			parts = append(parts, "lacks "+strings.Join(lacks, ", "))
		}
		if c.Keyboard != "yes" {
			parts = append(parts, "keyboard "+c.Keyboard)
		}
	}
	return strings.Join(parts, " · ")
}

// findWindow returns the window at the address in the list, or nil.
func findWindow(windows []hypr.Window, address string) *hypr.Window {
	for i := range windows {
		if windows[i].Address == address {
			return &windows[i]
		}
	}
	return nil
}

// windowPID returns the pid of the window at the address in the list, or 0.
func windowPID(windows []hypr.Window, address string) int {
	for _, w := range windows {
		if w.Address == address {
			return w.PID
		}
	}
	return 0
}
