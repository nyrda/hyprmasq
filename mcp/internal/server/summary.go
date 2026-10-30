package server

import (
	"fmt"
	"strings"
)

// summaryLine is the one line every act result starts with.
func summaryLine(out *actOut, batch []action) string {
	var parts []string
	add := func(s string) {
		if s != "" {
			parts = append(parts, s)
		}
	}
	add(actionsText(out.Actions, batch))
	// A batch that act sent in two parts, before and after its scroll, has a line for each part's wait and shot.
	split := out.BeforeScroll != nil
	if !split {
		add(waitText(out.Wait))
	}
	if out.WindowClosed && (out.Wait == nil || out.Wait.Ended != "window_closed") {
		add("window closed")
	}
	if !split {
		add(resultShotText(out))
	}
	add(sourceText(out.Source))
	// A held lease tells the agent that the window keeps the emulated focus until its next call. A window without one
	// gets no word: windows names the capability it lacks, and the lease field still says so.
	if out.Lease != nil && out.Lease.Held {
		add(fmt.Sprintf("lease %d s", out.Lease.RemainingS))
	}
	add(newWindowsText(out.NewWindows))
	add(lateWindowsText(out.LateWindows))
	lines := []string{strings.Join(parts, " · ")}
	if split {
		before := out.BeforeScroll
		lines = append(lines, "before the scroll: "+joinParts(waitText(before.Wait), shotText(before.Shot)))
		lines = append(lines, changesLines(before.Shot)...)
		lines = append(lines, "after the scroll: "+joinParts(waitText(out.Wait), resultShotText(out)))
	}
	lines = append(lines, changesLines(out.Shot)...)
	if out.Scroll != nil {
		lines = append(lines, out.Scroll.text())
	}
	lines = append(lines, sourceLines(out.Source)...)
	lines = append(lines, newWindowLines(out.NewWindows, "new window")...)
	return strings.Join(append(lines, newWindowLines(out.LateWindows, "late window")...), "\n")
}

// resultShotText is the summary's part about the result's shot, or about the capture that failed.
func resultShotText(out *actOut) string {
	switch {
	case out.Shot != nil:
		return shotText(out.Shot)
	case out.CaptureError != nil:
		return "capture failed: " + out.CaptureError.Message
	}
	return ""
}

// joinParts joins the summary's parts that are not empty.
func joinParts(parts ...string) string {
	var kept []string
	for _, p := range parts {
		if p != "" {
			kept = append(kept, p)
		}
	}
	return strings.Join(kept, " · ")
}

// actionsText counts the actions the application received as delivered: the sender cannot see whether the application
// acted on them as meant, which only the shot can show.
func actionsText(a *actionsOut, batch []action) string {
	if a != nil && a.Total == 0 && a.DragAndDrop != nil {
		return strings.TrimPrefix(dragText(a.DragAndDrop), " · ")
	}
	if a == nil || a.Total == 0 {
		return ""
	}
	text := fmt.Sprintf("%d/%d delivered", a.Done, a.Total)
	failed := ""
	if a.FailedIndex != nil && *a.FailedIndex < len(batch) {
		failed = describe(*a.FailedIndex, batch[*a.FailedIndex])
	}
	switch a.Delivery {
	case "none":
		text += ", nothing sent"
	case "partial":
		if a.HeldBack != "" && a.heldPress && failed != "" {
			text += ", " + failed + " and after held back: popup open"
			break
		}
		if a.HeldBack != "" {
			text += ", scroll held back"
			break
		}
		text += ", delivery partial"
		if failed != "" {
			text += ": " + failed + " may be partly applied"
			if a.Progress != nil {
				text += fmt.Sprintf(" (%d keys sent)", a.Progress.Keys)
			}
		}
	case "uncertain":
		text += ", delivery uncertain"
		if failed != "" {
			text += " from " + failed + " on"
		}
	}
	if a.SharedFocusReturned != nil {
		text += " · shared keyboard focus returned for user input"
	}
	if a.DragAndDrop != nil {
		text += dragText(a.DragAndDrop)
	}
	return text
}

// breakdown is "6 finished, 2 started none" for a drag-and-drop out of several, or "".
func (d *dragOut) breakdown() string {
	if d.Count < 2 && d.None == 0 {
		return ""
	}
	var parts []string
	add := func(n int, what string) {
		if n > 0 {
			parts = append(parts, fmt.Sprintf("%d %s", n, what))
		}
	}
	reasons := ""
	if len(d.Reasons) > 0 {
		reasons = " (" + strings.Join(d.Reasons, ", ") + ")"
	}
	add(d.Finished, "finished")
	add(d.Cancelled, "cancelled"+reasons)
	add(d.Unconfirmed, "unconfirmed")
	add(d.Pending, "pending")
	if d.None == 1 {
		parts = append(parts, "1 drag started none")
	} else {
		add(d.None, "drags started none")
	}
	return strings.Join(parts, ", ")
}

// dragText is the summary's part about a drag-and-drop of the batch.
func dragText(d *dragOut) string {
	if d.Outcome == "cancelled" && !d.Private {
		return " · drag-and-drop cancelled to protect the user's input"
	}
	if b := d.breakdown(); b != "" && d.Outcome != "finished" {
		return " · drag-and-drop: " + b
	}
	switch {
	case d.Outcome == "cancelled", d.Outcome == "unconfirmed":
		return fmt.Sprintf(" · drag-and-drop %s (%s)", d.Outcome, d.Reason)
	case d.Outcome == "pending" && d.Phase == "dropped":
		return " · drag-and-drop pending: dropped, not yet confirmed"
	}
	return " · drag-and-drop " + d.Outcome
}

// waitText is the summary's part about the wait.
func waitText(w *waitOut) string {
	if w == nil {
		return ""
	}
	switch w.Ended {
	case "quiet":
		observed := "redraw observed"
		if !w.RedrawObserved {
			observed = "no redraw"
		}
		return fmt.Sprintf("%s · quiet %d ms%s · %d ms%s", observed, w.QuietMs, animationText(w), w.ElapsedMs, againText(w.Again))
	case "no_redraw":
		return fmt.Sprintf("no redraw within %d ms", w.expectMs)
	case "timeout":
		if w.skipped {
			return "no time left to wait"
		}
		observed := ", no redraw"
		switch {
		case w.Resizing:
			observed = ", still resizing"
		case w.RedrawObserved:
			observed = ", still drawing"
		}
		return fmt.Sprintf("timeout after %d ms%s", w.ElapsedMs, observed)
	case "window_closed":
		return "window closed"
	case "cancelled":
		return "cancelled"
	}
	return w.Ended
}

// animationText names the animations that a quiet phase did not wait for, by their boxes in the result's shot when it
// has one.
func animationText(w *waitOut) string {
	if len(w.animated) == 0 {
		return ""
	}
	noun := "animation"
	if len(w.animated) > 1 {
		noun = "animations"
	}
	if len(w.Animated) == 0 {
		return " except " + noun
	}
	boxes := make([]string, len(w.Animated))
	for i, b := range w.Animated {
		boxes[i] = fmt.Sprintf("%d,%d %d×%d", b.X, b.Y, b.Width, b.Height)
	}
	return fmt.Sprintf(" except %s at %s", noun, strings.Join(boxes, ", "))
}

// againText tells that the window drew again after it had settled, and when it settled again.
func againText(a *waitAgainOut) string {
	if a == nil {
		return ""
	}
	return fmt.Sprintf(" · drew again, settled %d ms later", a.ElapsedMs)
}

// shotText is the summary's part about a shot.
func shotText(s *shotOut) string {
	if c := s.Changes; c != nil {
		switch len(c.Regions) {
		case 0:
			if c.Animated > 0 {
				return fmt.Sprintf("shot %s = %s, no change outside animations", s.ID, c.Base)
			}
			return fmt.Sprintf("shot %s = %s, no change", s.ID, c.Base)
		case 1:
			return fmt.Sprintf("shot %s = %s + 1 change", s.ID, c.Base)
		}
		return fmt.Sprintf("shot %s = %s + %d changes", s.ID, c.Base, len(c.Regions))
	}
	text := fmt.Sprintf("shot %s %d×%d", s.ID, s.Width, s.Height)
	var notes []string
	if s.FullShot != "" {
		notes = append(notes, "full shot: "+s.FullShot)
	}
	if s.Resizing {
		notes = append(notes, "stale: not yet redrawn at its new size")
	}
	switch {
	case s.starved == nil:
	case s.starved.WaitedMs > 0:
		notes = append(notes, fmt.Sprintf("may be stale: not redrawn within %d ms", s.starved.WaitedMs))
	default:
		notes = append(notes, "may be stale: no time to redraw")
	}
	if len(notes) > 0 {
		text += " (" + strings.Join(notes, "; ") + ")"
	}
	return text
}

// changesLines are the lines after the summary line of a changes shot: a line for each zoom, in the order of the
// images, and the tiny changes left out.
func changesLines(s *shotOut) []string {
	if s == nil || s.Changes == nil {
		return nil
	}
	var lines []string
	for _, r := range s.Changes.Regions {
		lines = append(lines, fmt.Sprintf("zoom %s %d×%d of %s at %d,%d %d×%d", r.Shot, r.ImageWidth, r.ImageHeight, s.ID, r.X, r.Y, r.Width, r.Height))
	}
	switch n := s.Changes.LeftOut; {
	case n == 1:
		lines = append(lines, "1 tiny change left out")
	case n > 1:
		lines = append(lines, fmt.Sprintf("%d tiny changes left out", n))
	}
	switch n := s.Changes.Animated; {
	case n == 1:
		lines = append(lines, "1 change in an animation left out")
	case n > 1:
		lines = append(lines, fmt.Sprintf("%d changes in animations left out", n))
	}
	return lines
}

// newWindowsText is the summary's part about new windows.
func newWindowsText(windows []newWindowOut) string {
	return windowsText(windows, "new window", "new windows")
}

// windowsText names windows that appeared, under the noun for one and for several of them, or "" for none.
func windowsText(windows []newWindowOut, one, several string) string {
	if len(windows) == 0 {
		return ""
	}
	var parts []string
	for _, w := range windows {
		relation := ""
		if w.Launched {
			relation = ", launched"
		} else if w.SamePID {
			relation = ", same pid"
		}
		where := "hidden"
		if w.Visible {
			where = "visible"
		}
		if w.Focused {
			where += ", focused"
		}
		parts = append(parts, fmt.Sprintf("%s (%s %q%s) on workspace %s (%s)", w.Window, w.Class, w.Title, relation, w.Workspace, where))
	}
	noun := one
	if len(windows) > 1 {
		noun = several
	}
	return noun + " " + strings.Join(parts, ", ")
}

// newWindowLines are the lines after the summary line for the shots of new windows, or with the noun of other windows
// that appeared, in the order of their images.
func newWindowLines(windows []newWindowOut, noun string) []string {
	var lines []string
	for _, w := range windows {
		if w.Shot != nil {
			lines = append(lines, fmt.Sprintf("%s of %s %s", shotText(w.Shot), noun, w.Window))
		}
	}
	return lines
}

// sourceText is the summary's part about the window that a closed target came from, or "".
func sourceText(src *sourceOut) string {
	if src == nil {
		return ""
	}
	where := "hidden"
	if src.Visible {
		where = "visible"
	}
	return fmt.Sprintf("back to %s (%s %q) on workspace %s (%s)", src.Window, src.Class, src.Title, src.Workspace, where)
}

// sourceLines is the line after the summary line for the shot of the window that a closed target came from.
func sourceLines(src *sourceOut) []string {
	if src == nil || src.Shot == nil {
		return nil
	}
	return []string{fmt.Sprintf("%s of %s, the window it came from", shotText(src.Shot), src.Window)}
}
