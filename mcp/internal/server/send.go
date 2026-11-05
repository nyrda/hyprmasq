package server

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// geometryPoll is how often the window's size is checked while a batch that depends on it is sent.
const geometryPoll = 25 * time.Millisecond

// watchesGeometry tells whether a resize while the batch runs can make coordinates wrong that are still to be sent:
// those of an action after the first, of a path, which moves over time, or of the only action, which goes out once
// hyprmasq-input has connected. A resize while keys follow a first click or scroll does not stop the batch, since the
// keys themselves may cause it.
func watchesGeometry(batch []action) bool {
	return (len(batch) > 1 && batchHasCoordinates(batch[1:])) || (len(batch) > 0 && (batch[0].Kind == "path" || (len(batch) == 1 && batch[0].hasCoordinates())))
}

// send takes the send lock of the window's app and, for a paste, the clipboard lock, checks the window again, and runs
// the batch in one hyprmasq-input, which checks every action, including whether the keymap can type it, before it
// sends anything. It returns the window's state read just before the input, whose counter is the wait's baseline.
// clientCtx tells a cancellation from the deadline of sendCtx.
func (s *Server) send(clientCtx, sendCtx context.Context, r *actRequest, steps []inputStep, sh *shot, st hypr.State, start time.Time, p *progress,
	locale targetLocale) (*actionsOut, hypr.State, error) {
	out := &actionsOut{Total: len(r.batch), Delivery: "none"}
	// Locks are taken in one order: the window's, its app's send lock, then the clipboard's.
	p.set("waiting for input to other windows of the same app")
	sendLock, err := acquireLock(sendCtx, s.sendLockPath(r.pid, st.XWayland))
	if err != nil {
		what := "the send lock, held by a call on another window of the same app"
		if st.XWayland {
			what = "the send lock, held by a call on another X11 window"
		}
		return out, st, lockError(clientCtx, err, start, what)
	}
	defer sendLock.release()
	if batchPastes(r.batch[r.from:]) {
		p.set("waiting for a paste into another window")
		clipboardLock, err := acquireLock(sendCtx, s.clipboardLockPath())
		if err != nil {
			return out, st, lockError(clientCtx, err, start, "the clipboard lock, held by a paste into another window")
		}
		defer clipboardLock.release()
	}

	// Other calls may have changed the window while this one waited for the locks.
	cur, err := s.sendable(clientCtx, sendCtx, r, sh, st)
	if err != nil {
		return out, cur, err
	}
	inputBy, _ := sendCtx.Deadline()
	for round := 0; sh != nil; round++ {
		// The check leaves the batch the time it takes to send.
		decided, err := s.checkTargets(sendCtx, r, sh, cur, inputBy.Add(-sendTime(steps)), p)
		switch {
		case clientCtx.Err() != nil:
			return out, cur, errCancelled
		case err != nil:
			return out, cur, err
		case decided == nil:
		default:
			// The window may have been resized or replaced meanwhile.
			if cur, err = s.sendable(clientCtx, sendCtx, r, sh, st); err != nil {
				return out, cur, err
			}
		}
		// A window that drew after the capture that the check decided on may have moved its targets since, and is
		// checked once more. One that keeps drawing would never hold still for it.
		if decided == nil || round == movedRechecks || cur.Commits != nil && *cur.Commits == decided.commits && deref(cur.SocketID) == decided.socket {
			break
		}
	}
	// Input that would be cut off part-way is not started. sendCtx ends where the time for input does.
	if !inputBy.IsZero() {
		if need, left := sendTime(steps), time.Until(inputBy); need > left {
			return out, cur, &toolError{Code: "deadline",
				Message: fmt.Sprintf("the batch takes about %d ms to send with round trips, and %d ms were left for it after waiting for other calls", need.Milliseconds(), max(0, left.Milliseconds())),
				Hint:    "Retry, or give a larger deadline_ms (at most 30000)."}
		}
	}

	p.set("sending input")
	// The lease keeps the emulated focus that the input gives, so it is taken first. It keeps the focus from ending
	// while the batch runs and the window redraws.
	before, read := cur.Lease, time.Now()
	s.renewLease(sendCtx, r.window, cur.Capabilities)
	out, err = s.runBatch(clientCtx, sendCtx, r, steps, sh, cur, locale)
	if out.Delivery == "none" && cur.Capabilities.Lease {
		s.restoreLease(clientCtx, r.window, before, read)
	}
	return out, cur, err
}

// sendable reads the window's state right before the input, and fails when the window was replaced, was resized since
// the shot that the batch's coordinates come from, or lacks a capability that the batch needs. st is the state that the
// call read before, which a failure returns when there is no newer one.
func (s *Server) sendable(clientCtx, sendCtx context.Context, r *actRequest, sh *shot, st hypr.State) (hypr.State, error) {
	cur, err := s.openState(sendCtx, r.window, st.StableID)
	switch {
	case clientCtx.Err() != nil:
		return st, errCancelled
	case sendCtx.Err() != nil:
		return st, deadlineWhileSending("none", r.deadline)
	case err != nil:
		return st, err
	case sh != nil && batchHasCoordinates(r.batch[r.from:]) && cur.Size != sh.Size:
		return cur, &toolError{Code: "stale_shot", Message: fmt.Sprintf("the window was resized from %s to %s since shot %s", formatSize(sh.Size), formatSize(cur.Size), sh.ID),
			Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	if err := checkCapabilities(r.batch[r.from:], cur.Capabilities, r.window); err != nil {
		return cur, err
	}
	return cur, checkPaste(r.batch[r.from:], cur.XWayland)
}

// runBatch runs the batch in one hyprmasq-input, and stops it when the window is resized or replaced meanwhile.
func (s *Server) runBatch(clientCtx, sendCtx context.Context, r *actRequest, steps []inputStep, sh *shot, st hypr.State, locale targetLocale) (*actionsOut, error) {
	out := &actionsOut{Total: len(r.batch), Delivery: "none"}
	runCtx, stop := context.WithCancelCause(sendCtx)
	defer stop(nil)
	watched := make(chan struct{})
	if sh != nil && watchesGeometry(r.batch[r.from:]) {
		go s.watchGeometry(runCtx, stop, r.window, sh, st.StableID, watched)
	} else {
		close(watched)
	}
	inputBy, _ := sendCtx.Deadline()
	left := time.Until(inputBy)
	base := currentBaseline(st)
	held := -1        // the sync where the batch stopped for a popup that its press would only close
	var pasted uint64 // the serial of the text of the last paste
	var unread []int
	answer := func(step int) bool {
		if step < 0 || step >= len(steps) || steps[step].Argv[0] != "sync" {
			return true
		}
		if steps[step].PasteText != nil {
			// Without the text on the agent clipboard, Shift+Insert would paste what was there before: the sync gets no
			// answer, and the sender stops there.
			serial, err := s.setPaste(runCtx, r, steps[step].Batch, steps[step])
			if err != nil {
				stop(err)
				return false
			}
			pasted = serial
			return true
		}
		if steps[step].PasteRead {
			if !s.pasteRead(runCtx, pasted) && !slices.Contains(unread, steps[step].Batch) {
				unread = append(unread, steps[step].Batch)
			}
			return true
		}
		x, y, press := pressAfter(steps, step)
		most := answerMaxMs * time.Millisecond
		if press {
			most -= popupCheckMs * time.Millisecond
		}
		base = s.answerClick(runCtx, r.window, base, steps[step+1:], inputBy, most)
		if !press {
			return true
		}
		if cur, err := s.hypr.State(runCtx, r.window); err == nil && cur.StableID == st.StableID && closingPopup(cur.Popups, x, y) {
			held = step
			return false
		}
		return true
	}
	r.rec.inputStarted(time.Now(), steps)
	reply, err := s.runSender(runCtx, *st.Socket, steps, paceBudget(left), locale.name, answer)
	stop(nil)
	<-watched
	out.UnreadPastes = unread
	if reply.Delivery != "none" {
		out.composeNote = composeNote(reply, locale)
	}
	var changed *toolError
	errors.As(context.Cause(runCtx), &changed)
	// The sender stopped at that sync, unless it went on by itself first.
	if held >= 0 && err == nil && changed == nil && clientCtx.Err() == nil && reply.FailedIndex != nil && *reply.FailedIndex == held {
		at := steps[held].Batch
		out.Done, out.Delivery, out.FailedIndex = at, "partial", ptr(at)
		out.HeldBack, out.heldPress = heldPressReason, true
		return out, nil
	}
	if err == nil && reply.Delivery == "none" && reply.Error != nil && reply.Error.Code == "too_long" {
		return out, tooLong(reply, r.deadline, left, batchTypes(r.batch[r.from:]))
	}

	if err != nil {
		var se *startError
		if errors.As(err, &se) {
			switch {
			case clientCtx.Err() != nil:
				return out, errCancelled
			case sendCtx.Err() != nil:
				return out, deadlineWhileSending("none", r.deadline)
			case changed != nil:
				return out, changed
			case se.stopped:
				return out, &toolError{Code: "cancelled", Message: "hyprmasq-input was stopped by a signal from another process before it connected",
					Hint: "Retry; if it happens again, tell the user that something stops hyprmasq-input."}
			}
			return out, &toolError{Code: "internal", Message: err.Error(), Hint: "Tell the user that hyprmasq-input cannot be started; see the --input option of hyprmasq-mcp."}
		}
		// The part of a split batch that a sender runs starts at r.from, after what an earlier part delivered.
		out.Done, out.Delivery, out.FailedIndex = r.from, "uncertain", ptr(r.from)
		return out, &toolError{Code: "internal", Message: err.Error(), Hint: hintUncertain}
	}
	if reply.OK && reply.Delivery == "complete" {
		out.Done, out.Delivery = len(r.batch), "complete"
		return out, nil
	}
	out.Done = doneActions(steps, reply.Done, len(r.batch))
	if reply.Delivery == "none" {
		out.Done = 0
	}
	failed, failedStep := out.Done, ""
	if reply.FailedIndex != nil && *reply.FailedIndex >= 0 && *reply.FailedIndex < len(steps) {
		failed, failedStep = steps[*reply.FailedIndex].Batch, steps[*reply.FailedIndex].Argv[0]
	}
	failed = min(failed, len(r.batch)-1)
	out.FailedIndex = &failed
	out.Delivery = reply.Delivery
	if out.Delivery == "complete" {
		out.Delivery = "partial"
	}
	if reply.Progress != nil && (r.batch[failed].Kind == "type" || r.batch[failed].Kind == "key") {
		out.Progress = &progressOut{Keys: reply.Progress.Keys}
	}
	// The sender stops at a pause when the connection ends, but a pause needs no window. When the window closed, as the
	// input may have made it, the pauses count as delivered: with only pauses left, the batch is complete and the wait
	// finds the window closed, and otherwise the error names the input after them. Another sender that took over the
	// socket leaves the window open, and the batch failed.
	if next := pausesFrom(steps, reply); next >= 0 && out.Delivery == "partial" && changed == nil && sendCtx.Err() == nil &&
		reply.Error != nil && reply.Error.Code == "unconfirmed" && s.windowGone(clientCtx, r.window) {
		if next == len(steps) {
			return &actionsOut{Done: len(r.batch), Total: len(r.batch), Delivery: "complete", composeNote: out.composeNote}, nil
		}
		out.Done, failed, failedStep, out.Progress = steps[next].Batch, steps[next].Batch, steps[next].Argv[0], nil
	}
	if changed != nil && clientCtx.Err() == nil {
		if changed.Code == "window_gone" && out.Delivery != "none" {
			return out, closedWhileSent(r.window, r.batch, out.Done, failed)
		}
		return out, changed
	}
	return out, s.senderError(clientCtx, sendCtx, reply, r, out.Done, failed, failedStep, out.Delivery)
}

// The popup check before a press, see closingPopup: what it leaves of the sync's longest wait for reading the window's
// popups, before the sender goes on by itself.
const popupCheckMs = 100

// heldPressReason is why act held back a press and the actions after it.
const heldPressReason = "the window had a popup open, such as a menu or a dropdown's list, and the press lay outside it, where it would only close the popup"

// pressAfter is the point in window-local logical pixels where the action after the sync at step presses a button: the
// motion of a click, or the first point of a path with a button.
func pressAfter(steps []inputStep, step int) (x, y float64, ok bool) {
	if step+1 >= len(steps) {
		return 0, 0, false
	}
	// move X Y, then click, or path N X1 Y1 … XN YN BUTTON MODS.
	next, first := steps[step+1].Argv, 1
	switch {
	case next[0] == "move" && step+2 < len(steps) && steps[step+2].Argv[0] == "click" && steps[step+2].Batch == steps[step+1].Batch:
	case next[0] == "path" && next[len(next)-2] != "none":
		first = 2
	default:
		return 0, 0, false
	}
	x, errX := strconv.ParseFloat(next[first], 64)
	y, errY := strconv.ParseFloat(next[first+1], 64)
	return x, y, errX == nil && errY == nil
}

// closingPopup tells whether a press at x,y in window-local logical pixels would only close a popup: one that asked for
// a grab is open, and the point lies outside every popup of the window. Apps such as Qt close such a popup on the press
// and give the press to nothing else, as they do for the user's own click. A press in a popup reaches it.
func closingPopup(popups []hypr.Popup, x, y float64) bool {
	grab := false
	for _, p := range popups {
		if p.Contains(x, y) {
			return false
		}
		grab = grab || p.Grab
	}
	return grab
}

// pausesFrom is the first step after the pauses that the sender failed at, or -1 when it failed at no pause. A sync,
// where the sender waits for act, is a pause too.
func pausesFrom(steps []inputStep, reply senderReply) int {
	pause := func(i int) bool { return steps[i].Argv[0] == "sleep" || steps[i].Argv[0] == "sync" }
	if reply.FailedIndex == nil || *reply.FailedIndex < 0 || *reply.FailedIndex >= len(steps) || !pause(*reply.FailedIndex) {
		return -1
	}
	next := *reply.FailedIndex
	for next < len(steps) && pause(next) {
		next++
	}
	return next
}

// answerClick waits for the window to answer a click, or keys before a click, while the sender waits at the sync after
// them: with the plugin's wait for a change since base, at most answerExpectMs, then for answerQuietMs of quiet, within
// most, and early enough that the rest of the batch still fits before inputBy. It returns the baseline for the next
// sync, the counter when the wait ended. A wait that fails leaves the sender to go on after its sync's least time.
func (s *Server) answerClick(ctx context.Context, window string, base baseline, rest []inputStep, inputBy time.Time, most time.Duration) baseline {
	limit := min(most, time.Until(inputBy)-sendTime(rest)).Milliseconds()
	if limit < 1 || ctx.Err() != nil {
		return base
	}
	ctx, cancel := context.WithTimeout(ctx, time.Duration(limit)*time.Millisecond)
	defer cancel()
	reply, err := s.wait(ctx, window, hypr.WaitArgs{Since: base.commits, ExpectMs: int(min(answerExpectMs, limit)), QuietMs: answerQuietMs, DeadlineMs: int(limit), ID: s.waitID()})
	if err != nil || (base.socket != "" && deref(reply.SocketID) != base.socket) {
		return base
	}
	return baseline{reply.Commits, base.socket}
}

// watchGeometry stops the sender when the window is resized or replaced while the batch runs, since the coordinates
// of the later actions no longer match it.
func (s *Server) watchGeometry(ctx context.Context, stop context.CancelCauseFunc, window string, sh *shot, stableID string, done chan<- struct{}) {
	defer close(done)
	ticker := time.NewTicker(geometryPoll)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		cur, err := s.hypr.State(ctx, window)
		if ctx.Err() != nil {
			return
		}
		var pe *hypr.Error
		if (errors.As(err, &pe) && pe.Code == "window_gone") || (err == nil && cur.StableID != stableID) {
			stop(&toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed during the batch", window), Hint: hintWindows})
			return
		}
		if err == nil && cur.Size != sh.Size {
			stop(&toolError{Code: "stale_shot", Message: fmt.Sprintf("the window was resized from %s to %s during the batch, so the batch stopped", formatSize(sh.Size), formatSize(cur.Size)),
				Hint: "The batch stopped because its coordinates no longer match the window."})
			return
		}
	}
}

// doneActions counts the batch actions whose every step the sender confirmed, or counted as done since only releases
// were left of it when the connection ended.
func doneActions(steps []inputStep, stepsDone, total int) int {
	if stepsDone < 0 {
		return 0
	}
	if stepsDone >= len(steps) {
		return total
	}
	return steps[stepsDone].Batch
}

// windowGone tells whether the plugin reports the window as closed.
func (s *Server) windowGone(ctx context.Context, window string) bool {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_, err := s.hypr.State(ctx, window)
	var pe *hypr.Error
	return errors.As(err, &pe) && pe.Code == "window_gone"
}

// pauseReason asks the plugin why it last paused the window's input, or "" when it cannot tell.
func (s *Server) pauseReason(ctx context.Context, window string) string {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	st, err := s.hypr.State(ctx, window)
	if err != nil || st.LastPause == nil {
		return ""
	}
	return *st.LastPause
}

// tooLong maps the sender's refusal of a batch whose resolved keys take longer at their pace than its budget, left,
// the time to the end of input when it started. A batch that the deadline cannot fit is refused as fitsDeadline does;
// otherwise waiting for other calls took the time. typed tells that the batch types text.
func tooLong(reply senderReply, deadline, left time.Duration, typed bool) error {
	if reply.PaceMs == nil {
		return &toolError{Code: "deadline", Message: reply.Error.Message, Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
	}
	least := time.Duration(*reply.PaceMs) * time.Millisecond
	if err := overDeadline(least, deadline, true, typed); err != nil {
		return err
	}
	return &toolError{Code: "deadline",
		Message: fmt.Sprintf("the batch takes about %d ms to send with the active keymap and round trips, and %d ms were left for it after waiting for other calls",
			withRoundTrips(least).Milliseconds(), max(0, left.Milliseconds())),
		Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
}

// deadlineWhileSending reports the deadline that arrived before or while the input was sent, by its delivery.
func deadlineWhileSending(delivery string, deadline time.Duration) error {
	if delivery == "none" {
		return &toolError{Code: "deadline", Message: "the deadline arrived before the input was sent", Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
	}
	hint := "The deadline stopped the input part-way. Once you have seen how far it got, send the rest with a larger deadline_ms, or in parts over several calls."
	if deadline >= maxDeadline {
		hint = "The deadline stopped the input part-way. Once you have seen how far it got, send the rest in parts over several calls."
	}
	return &toolError{Code: "deadline", Message: "the deadline arrived while the input was being sent", Hint: hint}
}

// senderError maps the error of a failed hyprmasq-input run. done counts the batch actions delivered, step is the
// sender's action that failed, such as move, and delivery the batch's, which decides the hint.
func (s *Server) senderError(clientCtx, sendCtx context.Context, reply senderReply, r *actRequest, done, failed int, step, delivery string) error {
	if clientCtx.Err() != nil {
		return errCancelled
	}
	if sendCtx.Err() != nil {
		return deadlineWhileSending(delivery, r.deadline)
	}
	code, message := "internal", "hyprmasq-input failed"
	if reply.Error != nil {
		code, message = reply.Error.Code, reply.Error.Message
	}
	where := describe(failed, r.batch[failed])
	switch code {
	case "invalid_arguments":
		// The server checks every other argument of these, so the point lies outside the pointer's regions, which end
		// at the edges of the window's monitor. The window may have moved since the shot.
		if step == "move" || step == "path" {
			return &toolError{Code: "out_of_bounds", Message: fmt.Sprintf("%s lies outside the area that pointer input reaches: %s", where, message),
				Hint: hintOutsideRegions + " " + hintScreenshot, ShotHint: hintOutsideRegions + " " + hintScreenshotShot}
		}
		return &toolError{Code: code, Message: where + ": " + message, Hint: "Fix the action and call again."}
	case "cannot_type":
		return &toolError{Code: code, Message: where + ": " + message, Hint: hintCannotType}
	case "input_paused":
		return pausedError(message, s.pauseReason(clientCtx, r.window), delivery != "none")
	case "hook_held":
		// hyprmasq-input exits with 3 before it sends anything.
		return &toolError{Code: code, Message: message, Hint: hintHookHeld}
	case "cancelled":
		// Neither the client nor the deadline stopped it.
		hint := "Retry; if it happens again, tell the user that something stops hyprmasq-input."
		if delivery != "none" {
			hint = ""
		}
		return &toolError{Code: code, Message: where + ": hyprmasq-input was stopped by a signal from another process", Hint: hint}
	}
	if s.windowGone(clientCtx, r.window) {
		if delivery != "none" {
			return closedWhileSent(r.window, r.batch, done, failed)
		}
		return &toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed while %s was sent", r.window, where), Hint: hintWindows}
	}
	hint := ""
	switch {
	case delivery == "uncertain":
		hint = hintUncertain
	case delivery == "none" && (code == "connect_failed" || code == "no_devices"):
		hint = "The window's input socket did not take the input. Retry once; if it fails again, tell the user."
	case code == "unconfirmed":
		// The plugin replaces the pointer device on a move or resize, and drops what reached the old one.
		hint = "The input stopped before it was all confirmed: the window moved or was resized while it was sent, or another sender took over its socket."
	}
	return &toolError{Code: code, Message: where + ": " + message, Hint: hint}
}

// pausedError explains an input_paused by the plugin's reason for the pause. With some of the batch delivered, the hint
// does not invite a retry, which would send that part again.
func pausedError(message, reason string, delivered bool) *toolError {
	why, retry, goOn := "", "Retry later, or ask the user.", "Go on once the user has left it, or ask the user."
	switch reason {
	case "user_button", "user_key":
		held := map[string]string{"user_button": "a mouse button", "user_key": "a key"}[reason]
		why = fmt.Sprintf("the user is holding %s in a window of the same app, which the input would have released", held)
		retry = "The plugin held the input back for a second, then paused it. Retry once the user has let go, or ask the user."
		goOn = "The plugin held the input back for a second, then paused it and dropped the rest. Go on once the user has let go, or ask the user."
	case "x11_unresponsive":
		why = "the window's X server did not answer in time"
		retry = "The plugin paused the input for half a second. Retry; if it happens again, tell the user."
		goOn = "The plugin paused the input for half a second and dropped the rest. Look at the window, then go on from what it shows."
	default:
		why = "the user is working in another window of the same X server"
	}
	hint := retry
	if delivered {
		hint = goOn
	}
	return &toolError{Code: "input_paused", Message: message + ": " + why, Hint: hint}
}

// closedWhileSent reports a window that closed while the batch was sent, after some of its input was applied. It names
// the actions before failed, which were delivered, so that they are not sent again.
func closedWhileSent(window string, batch []action, done, failed int) *toolError {
	where := describe(failed, batch[failed])
	if done == 0 {
		return &toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed while %s was sent", window, where), Hint: hintClosedBySent}
	}
	delivered, verb, them := describe(0, batch[0]), "was", "it"
	switch {
	case done == 2:
		delivered, verb, them = delivered+" and "+describe(1, batch[1]), "were", "them"
	case done > 2:
		delivered, verb, them = fmt.Sprintf("actions[0] to actions[%d]", done-1), "were", "them"
	}
	return &toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed after %s %s delivered, while %s was sent", window, delivered, verb, where),
		Hint: fmt.Sprintf("The window closed after %s %s delivered, maybe because of %s, for example through a quit shortcut or a close button, so do not send %s again. Call windows to see what is open now.",
			delivered, verb, them, them)}
}
