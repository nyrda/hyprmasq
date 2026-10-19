package server

import (
	"fmt"
	"slices"
	"strconv"
	"strings"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// joinHints joins the hints that apply to a result into one.
func joinHints(hints ...string) string {
	var parts []string
	for _, h := range hints {
		if h != "" {
			parts = append(parts, h)
		}
	}
	return strings.Join(parts, " ")
}

// waitHint explains a wait outcome that needs a decision. A commit only shows that the window drew, so the hints say
// no more than that about the screen.
func waitHint(w *waitOut, batch []action, shotID string, actions *actionsOut, shot *shotOut) string {
	if w == nil {
		return ""
	}
	hasShot := shot != nil
	look := lookAt(shot)
	switch w.Ended {
	case "cancelled":
		if actions != nil && actions.Delivery != "none" && actions.Delivery != "complete" {
			return "The call was cancelled while the input was sent. " + look + " before repeating anything."
		}
	case "no_redraw":
		kind := ""
		for _, a := range batch {
			if a.Kind != "pause" {
				kind = a.Kind
			}
		}
		switch kind {
		case "":
			// Without a shot, the baseline is read when the call starts, and the window may have drawn before.
			since := "during this call"
			if shotID != "" {
				since = "since shot " + shotID
			}
			return fmt.Sprintf("Nothing was drawn %s. If the window is still loading, wait again: act with no actions, a larger wait.expect_ms, and a deadline_ms at least 1000 above it.", since)
		case "click":
			return "The click may have missed or had no visible effect" + laterWindow(batch) + ". " + look + " before repeating it."
		case "key", "type":
			return "The keys may have gone to another widget or had no visible effect" + laterWindow(batch) + ". " + look + " before repeating them."
		case "move":
			if !opensWindows(batch) {
				return "The pointer motion had no visible effect. Use wait.expect_change false for hover that may change nothing."
			}
			// The batch has other input before the motion, such as a click.
			fallthrough
		default:
			return "The input may have had no visible effect" + laterWindow(batch) + ". " + look + " before repeating it."
		}
	case "timeout":
		switch {
		case w.skipped:
			return "The deadline left no time to wait for a redraw. " + look + " before repeating anything; to wait, act with no actions."
		case w.Resizing && hasShot && shot.Resizing:
			// The hint about the shot's older frame says how to wait for the redraw.
			return ""
		case w.Resizing && hasShot:
			return "The window had not yet redrawn at its new size when the wait's deadline arrived, but had by the capture. Look at the shot before repeating anything."
		case w.Resizing:
			return "The window had not yet redrawn at its new size when the deadline arrived. Take a screenshot and look at it before repeating anything."
		case w.RedrawObserved:
			return "The window kept drawing until the deadline, so it may be animating or still loading. " + look + "; to wait longer, act with no actions and a larger deadline_ms (at most 30000)."
		}
		return "Nothing was drawn before the deadline" + laterWindow(batch) + ". " + look + "; to wait longer for a first redraw, act with no actions and a larger deadline_ms (at most 30000)."
	}
	return ""
}

// noChangeHint says that delivered input may have had no visible effect, when the window settled and a changes shot
// found no change beyond tiny ones, or "" otherwise. The wait's hint covers a window that drew nothing, and the motion
// of a move, as for hover, may change nothing.
func noChangeHint(out *actOut, batch []action) string {
	if !unchanged(out.Shot) || out.Actions == nil || out.Actions.Delivery == "none" || out.Wait == nil || out.Wait.Ended != "quiet" {
		return ""
	}
	kind := ""
	for _, a := range batch {
		if a.Kind != "pause" {
			kind = a.Kind
		}
	}
	// Motion, as for hover, may change nothing, but not other input before it that can open a window.
	if kind == "" || kind == "move" && !opensWindows(batch) {
		return ""
	}
	base := out.Shot.Changes.Base
	later := laterWindow(batch)
	if out.Shot.Changes.Animated > 0 {
		return fmt.Sprintf("Nothing changed since %s outside the animations, so the input may have missed, or its effect lies inside them%s. Look at %s, or take a screenshot to see the animations as they are now, before repeating it.",
			base, later, base)
	}
	if out.Shot.Changes.LeftOut > 0 {
		return fmt.Sprintf("Only tiny changes since %s, such as a caret that moved or blinked, so the input may have missed or had little visible effect%s. Look at %s before repeating it.",
			base, later, base)
	}
	return fmt.Sprintf("Nothing changed since %s, so the input may have missed or had no visible effect%s. Look at %s before repeating it.", base, later, base)
}

// laterWindow adds to a hint about input that can open a window and changed nothing that it may have opened a window
// that maps after the call, which the wait for the input's own window cannot see, or "" for other input. An app gives
// no sign that a window is coming: the portal's file chooser mapped up to 1.8 s after its click in a benchmark.
func laterWindow(batch []action) string {
	if !opensWindows(batch) {
		return ""
	}
	return "; it may also have opened a window that maps later, as a file chooser of another process can a second or two after the input: windows with wait_ms waits for it, and your next act or screenshot returns it"
}

// unchanged tells whether a shot is a changes shot that found no change, which comes without an image.
func unchanged(s *shotOut) bool {
	return s != nil && s.Changes != nil && len(s.Changes.Regions) == 0
}

// lookAt says what to look at before repeating input: the shot, the zooms of a changes shot, or for one that found no
// change the shot it was compared with.
func lookAt(s *shotOut) string {
	switch {
	case s == nil:
		return "Take a screenshot and look at it"
	case unchanged(s):
		return "Look at " + s.Changes.Base
	case s.Changes != nil:
		return "Look at the zooms"
	}
	return "Look at the shot"
}

// frameHints explains a shot that shows, or may show, an older frame, or "".
func frameHints(s *shotOut) string {
	return joinHints(resizingHint(s), starvedHint(s))
}

// resizingHint explains a shot of a window that had not caught up with a resize, or "".
func resizingHint(s *shotOut) string {
	if s == nil || !s.Resizing {
		return ""
	}
	return fmt.Sprintf("Shot %s shows an older frame, since the window had not yet redrawn at its new size, so act takes no coordinates from it. To wait for the redraw, act with no actions and wait.expect_change false, and read the coordinates from the shot it returns; if the window does not redraw, use the keyboard or tell the user.", s.ID)
}

// starvedHint explains a shot of a starved window that did not draw before the capture, or "".
func starvedHint(s *shotOut) string {
	if s == nil || s.starved == nil {
		return ""
	}
	why := "this call had no time left to send it some"
	if s.starved.WaitedMs > 0 {
		why = fmt.Sprintf("it did not redraw within %d ms of those this call sent it", s.starved.WaitedMs)
	}
	return fmt.Sprintf("Shot %s may show an older frame: the window is on a hidden workspace, where it gets no frame callbacks, which many applications wait for before they draw, and %s. If the shot looks complete, go on; if it looks empty or unfinished, as an application that is still starting does, wait: act with no actions, a larger wait.expect_ms and a deadline_ms at least 1000 above it, and use the shot it returns.", s.ID, why)
}

// focusReturnedHint explains a shared keyboard focus that the user's input took back shortly after a shortcut, which
// says nothing about whether the shortcut took effect, so the hint leaves that to the shot.
func focusReturnedHint(s *shotOut) string {
	return "The user's input took this app's shared keyboard focus back from this window shortly after a shortcut or function key of the batch, so it may or may not have taken effect. " +
		lookAt(s) + ": if it shows the result, go on; if it is unclear, verify the window's state before repeating the action."
}

// hintLateShortcut explains a shortcut that changed nothing in a window that shares its app's keyboard focus with the
// user's window, see lateShortcutHint.
const hintLateShortcut = "This app shares its keyboard focus between this window and the user's, which gets it back about 250 ms after a shortcut or function key, so one that the app handles later may not have run. " +
	"Use a menu or a button instead, or launch a separate instance of the app with launch."

// lateShortcutHint says that a shortcut may have come too late for the keyboard focus, or "". A window that shares its
// Wayland connection with the user's focused window keeps the connection's keyboard focus for a quarter of a second
// after a shortcut, and an app that runs it later finds the focus back on the user's window. So the hint comes after a
// batch delivered whole with a shortcut key to such a window, when the window did not redraw, or a changes shot found no
// change, and nothing else explains that: the focus that the user's input took back, or a window that the input opened.
func lateShortcutHint(out *actOut, batch []action, st hypr.State) string {
	if out.Actions == nil || out.Actions.Delivery != "complete" || out.Actions.SharedFocusReturned != nil || out.WindowClosed || openedHint(out.NewWindows) != "" ||
		!sharesUsersKeyboard(st) || !slices.ContainsFunc(batch, shortcutKey) || out.Wait == nil {
		return ""
	}
	if out.Wait.Ended == "no_redraw" || out.Wait.Ended == "quiet" && unchanged(out.Shot) {
		return hintLateShortcut
	}
	return ""
}

// sharesUsersKeyboard tells whether the window shares its Wayland connection with the window that has the user's
// keyboard focus, without being that window, from its capabilities: the plugin refuses a lease to a Wayland window only
// for that connection, and gives dnd to the window with the focus alone. An X11 window, which never has a lease, gets
// no input at all while the user is in another window of its X server.
func sharesUsersKeyboard(st hypr.State) bool {
	return !st.XWayland && st.Capabilities.Keyboard != "x11-focus" && !st.Capabilities.Lease && !st.Capabilities.DnD
}

// The keysyms of keys that hold Ctrl, Alt or Super in the default keymaps, in lower case.
var commandModifierKeysyms = map[string]bool{
	"control_l": true, "control_r": true, "alt_l": true, "alt_r": true, "meta_l": true, "meta_r": true,
	"super_l": true, "super_r": true, "hyper_l": true, "hyper_r": true,
}

// shortcutKey tells whether the action presses a key that the plugin keeps the shared keyboard focus for until it has
// settled, and that it counts as input that can open a window: a key other than a modifier pressed with Ctrl, Alt or
// Super held, a function key or a media key. The plugin keeps the focus after Escape too, and gives it back at once
// after Enter, which are left out.
func shortcutKey(a action) bool {
	if a.Kind != "key" || len(a.Keys) == 0 {
		return false
	}
	last := strings.ToLower(a.Keys[len(a.Keys)-1])
	if slices.Contains(modifierNames, last) || modifierKeysyms[last] {
		return false
	}
	for _, key := range a.Keys[:len(a.Keys)-1] {
		if key == "ctrl" || key == "alt" || key == "super" || commandModifierKeysyms[strings.ToLower(key)] {
			return true
		}
	}
	if n, err := strconv.Atoi(strings.TrimPrefix(last, "f")); err == nil && strings.HasPrefix(last, "f") && n >= 1 && n <= 35 {
		return true
	}
	return strings.HasPrefix(last, "xf86")
}

// dragReasons explain why the plugin cancelled a drag-and-drop. Those of a drag that did not run within the app say
// why it could not; see `drag` in docs/mcp.md.
var dragReasons = map[string]string{
	"shared_focus": "this window shares its app with the window that has the user's keyboard or pointer focus, so the user's input could reach the drag",
	"x11":          "the window is an X11 window, whose drags Hyprland would run with the user's pointer",
	"user_drag":    "the user was dragging something at the time",
	"serial":       "the app started it from a press that hyprmasq did not send, or whose button was already up",
	"version":      "the app uses a version of the drag-and-drop protocol older than 3",
	"icon":         "the app's drag icon is a surface with another role",
	"busy":         "an earlier drag of the app had not ended, or its drop was still waiting for the app",
	"limit":        "the app keeps too many offers of earlier drags",
	"source_gone":  "the app ended the drag itself",
	"device_gone":  "the app ended the drag itself",
	"destination":  "the app refused the drop after it",
	"rejected":     "it ended over a place that took none of its types or actions",
	"no_target":    "it ended outside the window, where it can drop nothing: it drops only within this window",
	"user_input":   "the user's own input reached the app, which ends the drag first",
	"sender_gone":  "the input ended before the button's release",
	"window_moved": "the window moved or was resized during it",
	"other_window": "input went to another window of the same app during it",
	"timeout":      "it got no input for 10 seconds",
}

// dragHint explains a drag-and-drop of the batch that did not go through, or whose end is not known, and what to do,
// or "".
func dragHint(d *dragOut, s *shotOut) string {
	why := dragReasons[d.Reason]
	if why == "" {
		why = "hyprmasq ended it"
	}
	look := lookAt(s)
	if d.Outcome == "partial" {
		hint := "Not every drag of the batch went through: " + d.breakdown() + "."
		if d.None > 0 {
			hint += " A drag that started no drag-and-drop moved nothing in an app that drags that way, as when it was not ready to start one."
		}
		if d.Cancelled > 0 {
			hint += " Nothing was dropped for a cancelled one."
		}
		hint += " " + look + ", and drag again only the items that did not move"
		if d.Unconfirmed > 0 || d.Pending > 0 {
			hint += "; an unconfirmed or pending drop may have moved its item already"
		}
		return hint + "."
	}
	switch {
	case d.Outcome == "cancelled" && !d.Private:
		return "The app started a drag-and-drop, which hyprmasq cancelled to protect the user's input: " + why + ". Nothing was dropped, though the pointer motion was delivered. " +
			"Do it without dragging, through what the app offers instead, such as cut and paste, a menu, buttons or keyboard commands that move the item, or a mode that drags with plain pointer motion; otherwise ask the user to do the drag."
	case d.Outcome == "cancelled" && d.Reason == "user_input":
		return "The app's drag-and-drop was cancelled, since " + why + ": nothing was dropped. " + look + ", and drag again once the user has left the app, or ask the user."
	case d.Outcome == "cancelled":
		return "The app's drag-and-drop was cancelled, since " + why + ": nothing was dropped. " + look + " before you drag again, at a place that takes the item."
	case d.Outcome == "unconfirmed" && d.Reason == "timeout":
		return "The drag-and-drop dropped, but the app did not confirm it within 10 seconds, so whether it took the item is not known. " + look + " before repeating anything: a repeated move may move the item twice."
	case d.Outcome == "unconfirmed":
		return "The drag-and-drop dropped, but it ended before the app confirmed it, since " + why + ", so whether the app took the item is not known. " + look + " before repeating anything: a repeated move may move the item twice."
	case d.Outcome == "pending" && d.Phase == "dropped":
		return "The drag-and-drop dropped, and the app had not confirmed it by the deadline. " + look + " before repeating anything; to wait for it, act with no actions."
	case d.Outcome == "pending":
		return "The drag-and-drop had not dropped by the deadline. " + look + "; to wait for it, act with no actions, and drag again only if the item did not move."
	}
	return ""
}

// openedHint says which new windows the input opened, the ones that came from the target, or "" when it opened none.
func openedHint(windows []newWindowOut) string {
	var parts []string
	for _, w := range windows {
		if !w.fromTarget {
			continue
		}
		part := fmt.Sprintf("%s %q", w.Window, w.Title)
		if w.Shot != nil {
			part += " in shot " + w.Shot.ID
		}
		parts = append(parts, part)
	}
	switch len(parts) {
	case 0:
		return ""
	case 1:
		return "The input opened new window " + parts[0] + "."
	}
	return "The input opened new windows " + strings.Join(parts, ", ") + "."
}

// notPlacedReasons say why the plugin did not open a window on the workspace of the window it came from.
var notPlacedReasons = map[string]string{
	"disabled":  "the plugin's place_new_windows option is off",
	"workspace": "no window rule can name the workspace of the window it came from",
	"":          "the plugin could not tell that hyprmasq's input opened it",
}

// newWindowsHint names the new windows from the target that opened where the user sees them, since the plugin did not
// place them, those that have no shot, and those whose shot shows, or may show, an older frame.
func newWindowsHint(windows []newWindowOut) string {
	var hints []string
	for _, w := range windows {
		if !w.fromTarget {
			continue
		}
		hints = append(hints, frameHints(w.Shot))
		if w.Visible && !w.Placed {
			hint := fmt.Sprintf("New window %s opened on the user's visible workspace %s", w.Window, w.Workspace)
			if w.Focused {
				hint += " and has their keyboard focus, so what they type goes there"
			}
			reason, ok := notPlacedReasons[w.NotPlaced]
			if !ok {
				reason = notPlacedReasons[""]
			}
			hints = append(hints, hint+", since "+reason+". Tell the user.")
		}
		if w.Shot == nil {
			hints = append(hints, fmt.Sprintf("Take a screenshot of new window %s before acting in it.", w.Window))
		}
	}
	return joinHints(hints...)
}

// sourceHint names the window that a closed target came from, where the agent goes on, or "".
func sourceHint(src *sourceOut) string {
	switch {
	case src == nil:
		return ""
	case src.Shot != nil:
		return fmt.Sprintf("It came from window %s, which shot %s shows now: go on there with that shot's pixels.", src.Window, src.Shot.ID)
	}
	return fmt.Sprintf("It came from window %s: take a screenshot of it to go on there.", src.Window)
}

// closedHint says that the target closed, and where the agent goes on.
func closedHint(src *sourceOut) string {
	return joinHints("The window closed.", sourceHint(src), "Any new windows are listed; call windows for the rest.")
}
