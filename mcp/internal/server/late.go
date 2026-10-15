package server

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// Windows that input opens after its act has returned. An app gives no sign that a window is coming: the portal's file
// chooser mapped 1.4 to 2 s after the click in a benchmark, 0.7 s after the act and its grace for new windows had
// ended, and its toplevel existed 35 ms before it mapped. A longer grace would cost every click, so the next act or
// screenshot of the session reports such a window instead, with a shot.
const (
	// How long after an act a window that maps and comes from its target counts as opened late by its input.
	lateWatch = 2 * time.Minute
	// How many targets of acts are watched at once.
	maxOpeners = 16
)

// An opener is a window that an act of this session sent input to that can open a window, and the windows that were
// open when that act ended. A window that maps later and that the plugin found to come from it opened late.
type opener struct {
	window, stableID string
	pid              int
	known            map[string]bool
	until            time.Time
}

// watchOpener watches for the windows that an act's input to the window opens after the act: windows that are not
// among open, the windows at its end. It replaces the watch of an earlier act on the window, and keeps the windows that
// are late from that one and no result has reported, such as those of an act with capture "none", which claims none.
func (s *Server) watchOpener(window, stableID string, pid int, open []hypr.Window) {
	s.mu.Lock()
	defer s.mu.Unlock()
	i := slices.IndexFunc(s.openers, func(o *opener) bool { return o.window == window && o.stableID == stableID })
	known := make(map[string]bool, len(open))
	for _, w := range open {
		key := windowKey(w)
		if _, settled := s.settled[key]; i >= 0 && !s.openers[i].known[key] && !settled {
			continue
		}
		known[key] = true
	}
	s.openers = slices.DeleteFunc(s.openers, func(o *opener) bool { return o.window == window && o.stableID == stableID })
	s.openers = append(s.openers, &opener{window: window, stableID: stableID, pid: pid, known: known, until: time.Now().Add(lateWatch)})
	if len(s.openers) > maxOpeners {
		s.openers = s.openers[len(s.openers)-maxOpeners:]
	}
}

// pruneLocked forgets the openers whose watch has ended, and the windows settled before the oldest of the others
// began, which all of them know. Under s.mu.
func (s *Server) pruneLocked() {
	now := time.Now()
	s.openers = slices.DeleteFunc(s.openers, func(o *opener) bool { return now.After(o.until) })
	for key, at := range s.settled {
		if now.Sub(at) > lateWatch {
			delete(s.settled, key)
		}
	}
}

// settle marks windows as reported by a result, or as from no opener, so that they are not reported as late.
func (s *Server) settle(keys ...string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now()
	for _, key := range keys {
		s.settled[key] = now
	}
}

// openKeys is the set of the windowKeys of windows.
func openKeys(windows []hypr.Window) map[string]bool {
	keys := make(map[string]bool, len(windows))
	for _, w := range windows {
		keys[windowKey(w)] = true
	}
	return keys
}

// lateOpenerLocked returns the opener that the window came from after its act, by the source that the plugin found
// for it when it mapped, while no result has reported it, or nil. open holds the windowKeys of the windows open now. A
// source without a stable_id is gone, so it is the opener only when the opener's window is gone too. A window that the
// user's own input opened is theirs. Under s.mu.
func (s *Server) lateOpenerLocked(w hypr.Window, src *hypr.Source, open map[string]bool) *opener {
	key := windowKey(w)
	if _, ok := s.settled[key]; ok || src == nil || src.Reason == "user_input" {
		return nil
	}
	for _, o := range s.openers {
		if o.known[key] || src.Window != o.window {
			continue
		}
		if src.StableID == o.stableID || (src.StableID == "" && !open[o.window+"/"+o.stableID]) {
			return o
		}
	}
	return nil
}

// lateFrom returns the window whose input opened w after its act had returned, while no result has reported w, or "".
// open holds the windowKeys of the windows open now.
func (s *Server) lateFrom(w hypr.Window, src *hypr.Source, open map[string]bool) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneLocked()
	if o := s.lateOpenerLocked(w, src, open); o != nil {
		return o.window
	}
	return ""
}

// watching tells whether an opener is watched, so that a result may have late windows to report.
func (s *Server) watching() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.pruneLocked()
	return len(s.openers) > 0
}

// claimLate returns the windows among windows, the windows open now, that the input of an earlier act of this session
// opened after that act had returned, and that no result has reported yet. It claims them for this call, so that no
// other call reports them meanwhile; keepLate keeps the claims of those that the result has a shot of. It settles the
// windows that came from no opener, since the plugin decides where a window came from when it maps. A call that was
// cancelled claims none.
func (s *Server) claimLate(ctx context.Context, windows []hypr.Window) []newWindowOut {
	open := openKeys(windows)
	s.mu.Lock()
	s.pruneLocked()
	var candidates []hypr.Window
	for _, w := range windows {
		if _, ok := s.settled[windowKey(w)]; ok {
			continue
		}
		if slices.ContainsFunc(s.openers, func(o *opener) bool { return !o.known[windowKey(w)] }) {
			candidates = append(candidates, w)
		}
	}
	s.mu.Unlock()
	if len(candidates) == 0 {
		return nil
	}
	type found struct {
		window hypr.Window
		state  hypr.State
		from   *opener
	}
	var checked []found
	for _, w := range candidates {
		// state opens no socket, so it leaves windows of other apps undriven.
		if st, err := s.hypr.State(ctx, w.Address); err == nil && st.StableID == w.StableID {
			checked = append(checked, found{window: w, state: st})
		}
	}
	var claimed []found
	s.mu.Lock()
	now := time.Now()
	for _, f := range checked {
		key := windowKey(f.window)
		if _, ok := s.settled[key]; ok {
			continue
		}
		if f.from = s.lateOpenerLocked(f.window, f.state.Source, open); f.from != nil {
			if ctx.Err() != nil || s.closing {
				continue
			}
			claimed = append(claimed, f)
		}
		s.settled[key] = now
	}
	s.mu.Unlock()
	if len(claimed) == 0 {
		return nil
	}
	visible := s.visibleWorkspaces(ctx)
	focused, _ := s.hypr.ActiveWindow(ctx)
	launched := s.launchedWindows(ctx)
	late := make([]newWindowOut, 0, len(claimed))
	for _, f := range claimed {
		w, src := f.window, f.state.Source
		late = append(late, newWindowOut{Window: w.Address, StableID: w.StableID, Class: w.Class, Title: w.Title, PID: w.PID,
			SamePID: f.from.pid > 0 && w.PID == f.from.pid, Launched: launched[windowKey(w)] != "", Workspace: w.Workspace.Name, workspaceID: w.Workspace.ID,
			Visible: visible[w.Workspace.ID], Focused: w.Address == focused, Source: src.Window, Placed: src.Placed, NotPlaced: src.Reason, fromTarget: true, claimed: now})
	}
	return late
}

// keepLate keeps the late windows that the result has a shot of, which it reports, and releases the claims of the
// others, such as those past the limit of shots or without time for one, so that a later result reports them.
func (s *Server) keepLate(late []newWindowOut) []newWindowOut {
	var kept, released []newWindowOut
	for _, w := range late {
		if w.Shot != nil {
			kept = append(kept, w)
		} else {
			released = append(released, w)
		}
	}
	s.releaseLate(released)
	return kept
}

// reclaimLate claims a late window again whose claim keepLate released, and returns it, or nil when another call has
// claimed or reported it meanwhile.
func (s *Server) reclaimLate(w newWindowOut) *newWindowOut {
	s.mu.Lock()
	defer s.mu.Unlock()
	key := w.Window + "/" + w.StableID
	if _, ok := s.settled[key]; ok || s.closing {
		return nil
	}
	w.claimed = time.Now()
	s.settled[key] = w.claimed
	return &w
}

// releaseLate releases the claims of late windows that no result reports, also those with a shot of a call that was
// cancelled, so that a later result reports them.
func (s *Server) releaseLate(late []newWindowOut) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, w := range late {
		if key := w.Window + "/" + w.StableID; s.settled[key].Equal(w.claimed) {
			delete(s.settled, key)
		}
	}
}

// withoutLate leaves out of the new windows of an act those that it reports as late windows, which came from the
// target of an earlier act.
func withoutLate(newWindows, late []newWindowOut) []newWindowOut {
	return slices.DeleteFunc(newWindows, func(w newWindowOut) bool {
		return slices.ContainsFunc(late, func(l newWindowOut) bool { return l.Window == w.Window && l.StableID == w.StableID })
	})
}

// newWindowKeys are the windowKeys of the new windows, but those of the late ones, whose claims keepLate released.
func newWindowKeys(newWindows, late []newWindowOut, fromTarget bool) []string {
	var keys []string
	for _, w := range newWindows {
		if (!fromTarget || w.fromTarget) && !slices.ContainsFunc(late, func(l newWindowOut) bool { return l.Window == w.Window && l.StableID == w.StableID }) {
			keys = append(keys, w.Window+"/"+w.StableID)
		}
	}
	return keys
}

// lateWindowsFor lists the windows and claims the late windows for a result about the window, which reports that window
// itself.
func (s *Server) lateWindowsFor(ctx context.Context, window, stableID string) []newWindowOut {
	if !s.watching() {
		return nil
	}
	windows, err := s.listWindows(ctx)
	if err != nil {
		return nil
	}
	s.settle(window + "/" + stableID)
	return s.claimLate(ctx, windows)
}

// lateShots takes the shots of the late windows that a result about another window reports, within the deadline, and
// keeps those that it has a shot of. It returns them and their images.
func (s *Server) lateShots(ctx context.Context, end time.Time, late []newWindowOut) ([]newWindowOut, [][]byte) {
	images := s.newWindowShots(ctx, end, late)
	return s.keepLate(late), images
}

// lateWindowsText is the summary's part about late windows.
func lateWindowsText(windows []newWindowOut) string {
	return windowsText(windows, "late window", "late windows")
}

// lateHint says which windows the input of earlier acts opened after those acts had returned, or "".
func lateHint(windows []newWindowOut) string {
	var hints []string
	for _, w := range windows {
		hint := fmt.Sprintf("Your input to %s in an earlier act opened window %s %q after that act had returned", w.Source, w.Window, w.Title)
		if w.Shot != nil {
			hint += "; it is in shot " + w.Shot.ID
		}
		hints = append(hints, hint+".")
	}
	return strings.Join(hints, " ")
}
