package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const (
	defaultDeadline = 15 * time.Second
	maxDeadline     = 30 * time.Second
	captureReserve  = time.Second
)

// actArgs are the arguments of act as the client sends them; parseActArgs checks them.
type actArgs struct {
	Window     string            `json:"window" args:"required"`
	Shot       string            `json:"shot"`
	Actions    []json.RawMessage `json:"actions"`
	NextPage   json.RawMessage   `json:"next_page"`
	Wait       json.RawMessage   `json:"wait"`
	Capture    json.RawMessage   `json:"capture"`
	DeadlineMs *int              `json:"deadline_ms"`
}

// waitArgs are the fields of act's wait argument.
type waitArgs struct {
	ExpectChange *bool `json:"expect_change"`
	ExpectMs     *int  `json:"expect_ms"`
	QuietMs      *int  `json:"quiet_ms"`
}

// actRequest is a checked act call.
type actRequest struct {
	window        string
	shotID        string
	batch         []action
	expectChange  bool
	expectMs      int
	quietMs       int
	deadline      time.Duration
	captureMode   string
	captureRegion *regionArgs
	// For capture "changes": the shot to compare with, or why there is none.
	base       *shot
	baseReason string
	// The token of the launch by this server that the window belongs to, read before the input, which may close it.
	launch string
	// The first action of the part of the batch that a send sends and checks: act sends a batch that ends with
	// scrolling in two parts, see split.go.
	from        int
	joinChanges bool
}

// regionShot is the shot of a capture region, or "".
func (r *actRequest) regionShot() string {
	if r.captureRegion == nil {
		return ""
	}
	return r.captureRegion.Shot
}

// intArg is an optional integer argument, def when it is missing, which must lie from lo to hi.
func intArg(v *int, def, lo, hi int, name string) (int, error) {
	if v == nil {
		return def, nil
	}
	if *v < lo || *v > hi {
		return 0, invalidf("%s must be %d to %d, not %d", name, lo, hi, *v)
	}
	return *v, nil
}

// parseActArgs checks the arguments of an act call. It also returns the number of actions, which a failure reports.
func parseActArgs(c *call) (*actRequest, int, error) {
	var args actArgs
	if err := c.decode(&args); err != nil {
		return nil, 0, err
	}
	total := len(args.Actions)
	window := args.Window
	var err error
	if window != "" || args.Shot == "" {
		if window, err = canonicalWindow(window); err != nil {
			return nil, total, err
		}
	}
	r := &actRequest{window: window, shotID: args.Shot, expectChange: true}
	if r.batch, err = parseActions(args.Actions); err != nil {
		return nil, total, err
	}
	if len(args.NextPage) > 0 && string(args.NextPage) != "null" {
		if len(r.batch) >= maxActions {
			return nil, total, invalidf("actions and next_page together allow at most %d actions", maxActions)
		}
		var next struct {
			X         *float64 `json:"x"`
			Y         *float64 `json:"y"`
			Direction string   `json:"direction"`
		}
		if err := decodeStrict(args.NextPage, &next, "next_page"); err != nil {
			return nil, total, err
		}
		if next.X == nil || next.Y == nil {
			return nil, total, invalidf("next_page needs x and y on the scrolling background, in shot pixels")
		}
		dy := 1.0
		if next.Direction == "up" {
			dy = -1
		} else if next.Direction != "" && next.Direction != "down" {
			return nil, total, invalidf("next_page.direction must be down or up")
		}
		r.batch = append(r.batch, action{Kind: "scroll", X: *next.X, Y: *next.Y, DY: dy, Unit: "page"})
		total++
	}
	w := &waitArgs{}
	if len(args.Wait) > 0 && string(args.Wait) != "null" {
		if err := decodeStrict(args.Wait, w, "wait"); err != nil {
			return nil, total, err
		}
	}
	if w.ExpectChange != nil {
		r.expectChange = *w.ExpectChange
	}
	if r.expectMs, err = intArg(w.ExpectMs, 1000, 1, int((maxDeadline-captureReserve)/time.Millisecond), "wait.expect_ms"); err != nil {
		return nil, total, err
	}
	if r.quietMs, err = intArg(w.QuietMs, 200, 1, 10000, "wait.quiet_ms"); err != nil {
		return nil, total, err
	}
	deadlineMs, err := intArg(args.DeadlineMs, int(defaultDeadline/time.Millisecond), int(captureReserve/time.Millisecond), int(maxDeadline/time.Millisecond), "deadline_ms")
	if err != nil {
		return nil, total, err
	}
	r.deadline = time.Duration(deadlineMs) * time.Millisecond
	// The wait ends where the capture's reserve starts, also in its change phase.
	if limit := r.deadline - captureReserve; r.expectChange && w.ExpectMs != nil && time.Duration(r.expectMs)*time.Millisecond > limit {
		return nil, total, invalidf("wait.expect_ms %d does not fit in deadline_ms %d, whose last 1000 ms are kept for the capture; give a deadline_ms of at least %d",
			r.expectMs, deadlineMs, r.expectMs+int(captureReserve/time.Millisecond))
	}
	if r.captureMode, r.captureRegion, err = parseCapture(args.Capture); err != nil {
		return nil, total, err
	}
	for i, a := range r.batch {
		if a.Unit == "page" && (i != len(r.batch)-1 || slices.ContainsFunc(r.batch[:i], func(a action) bool { return a.Kind == "scroll" }) || r.captureMode == "none" || r.captureMode == "region") {
			return nil, total, invalidf("a page scroll must be the last action, with no earlier scroll and capture changes or window")
		}
	}
	if r.shotID == "" {
		for i, a := range r.batch {
			if a.hasCoordinates() {
				return nil, total, invalidf("shot is missing: %s has coordinates, which are pixels of the shot they were read from", describe(i, a))
			}
		}
	}
	return r, total, nil
}

// actTool runs the act tool.
func (s *Server) actTool(ctx context.Context, c *call) *mcp.CallToolResult {
	start := time.Now()
	r, total, err := parseActArgs(c)
	if err != nil {
		return errorResult(asToolError(err), &actOut{Actions: &actionsOut{Total: total, Delivery: "none"}, NewWindows: []newWindowOut{}}, nil, nil)
	}
	p := c.startProgress(ctx, r.deadline)
	defer p.stop()
	return s.act(ctx, r, start, p)
}

// failsWithoutShot tells the errors after which a capture is not possible or not useful.
var failsWithoutShot = map[string]bool{"window_gone": true, "plugin_missing": true, "plugin_version": true, "cancelled": true}

// act runs a checked act call: it takes the window's locks, sends the batch, waits for the window and captures it,
// within the call's deadline.
func (s *Server) act(ctx context.Context, r *actRequest, start time.Time, p *progress) *mcp.CallToolResult {
	out := &actOut{Actions: &actionsOut{Total: len(r.batch), Delivery: "none"}, NewWindows: []newWindowOut{}}
	fail := func(err error) *mcp.CallToolResult {
		return errorResult(asToolError(err), out, r.batch, nil)
	}
	// A shot already binds its coordinates to one window and stable identity.
	// Never infer a target from the last or active window.
	if r.window == "" {
		shown := s.shots.get(r.shotID)
		if shown == nil {
			return fail(&toolError{Code: "stale_shot", Message: fmt.Sprintf("there is no shot %s to identify the window", r.shotID), Hint: hintWindows})
		}
		r.window = shown.Window
	}
	end := start.Add(r.deadline)
	sendBy := end.Add(-captureReserve)
	callCtx, cancel := context.WithDeadline(ctx, end)
	defer cancel()

	if err := s.checkPlugin(callCtx); err != nil {
		return fail(err)
	}
	before, err := s.listWindows(callCtx)
	if err != nil {
		return fail(err)
	}
	r.launch = s.launchOf(callCtx, before, r.window)
	// A shot names the window by its stable_id too, so a window that closed since, also one whose address another
	// window has now, fails before anything opens a socket or captures.
	shown, stableID := s.shownWindow(r.window, r.shotID, r.regionShot())
	st, err := s.openState(callCtx, r.window, stableID)
	if err != nil {
		return fail(goneSinceShot(err, r.window, shown, stableID, st, before))
	}
	var region *zoom
	if r.captureMode == "region" {
		if region, err = s.resolveRegion(*r.captureRegion, r.window, st, "capture"); err != nil {
			return fail(err)
		}
	}
	// From here on the window is known, so a failure comes with a shot of it as it is now.
	failShot := func(err error) *mcp.CallToolResult {
		return s.failCaptured(ctx, r, out, asToolError(err), end, st.StableID, region)
	}
	coordinates := batchHasCoordinates(r.batch)
	var sh *shot
	if r.shotID != "" {
		if sh, err = s.checkShot(r.shotID, r.window, st, coordinates); err != nil {
			return failShot(err)
		}
	}
	// What the agent has seen of the window when the call starts.
	if r.captureMode == "changes" || endsWithPage(r) {
		r.base, r.baseReason = s.changesBase(r.window, sh, st)
	}
	if err := validateBatch(r.batch, sh, st.Capabilities, r.window); err != nil {
		return failShot(err)
	}
	steps := translateBatch(r.batch, sh)
	if err := fitsDeadline(steps, r.deadline); err != nil {
		return fail(err)
	}

	// Locks and input share the time before the capture's reserve. Input ends senderStop earlier, so that a sender the
	// deadline stops has reported by sendBy, and waiting for the locks past that point would be in vain.
	inputBy := sendBy
	if len(r.batch) > 0 {
		inputBy = start.Add(inputTime(r.deadline))
	}
	sendCtx, sendCancel := context.WithDeadline(ctx, inputBy)
	defer sendCancel()
	p.set("waiting for other calls on the window")
	lockStart := time.Now()
	windowLock, err := acquireLock(sendCtx, s.lockPath(st.StableID))
	if err != nil {
		err = lockError(ctx, err, start, "the window lock, held by another call on this window")
		if errors.Is(err, errCancelled) {
			out.Wait = &waitOut{Ended: "cancelled"}
			return s.finish(ctx, r, out, st, end, region, false, before)
		}
		return failShot(err)
	}
	defer windowLock.release()
	// Another call held the lock, and the windows that its input opened meanwhile are not new to this one.
	if time.Since(lockStart) >= lockPoll {
		if latest, err := s.listWindows(sendCtx); err == nil {
			before = latest
		}
	}

	var base baseline
	drags := dragBaseline(st)
	applied := false
	if len(r.batch) > 0 {
		var locale targetLocale
		if batchTypes(r.batch) {
			locale = s.windowLocale(before, r.window, st.StableID)
		}
		if k := scrollSplitAt(r, sh, inputBy); k > 0 {
			return s.actSplit(ctx, sendCtx, splitCall{r: r, at: k, out: out, sh: sh, st: st, start: start, end: end, sendBy: sendBy, region: region,
				before: before, locale: locale, p: p, failShot: failShot})
		}
		if endsWithPage(r) {
			return s.actPage(ctx, sendCtx, r, out, sh, st, start, end, sendBy, p, before)
		}
		actions, cur, err := s.send(ctx, sendCtx, r, steps, sh, st, start, p, locale)
		out.Actions = actions
		applied = actions.Delivery != "none"
		if errors.Is(err, errCancelled) {
			out.Wait = &waitOut{Ended: "cancelled"}
			return s.finish(ctx, r, out, cur, end, region, applied, before)
		}
		if err != nil {
			te := asToolError(err)
			if !applied {
				return failShot(te)
			}
			if te.Code == "window_gone" {
				// The input may have closed the window, so there is nothing to wait for or capture.
				out.Wait = &waitOut{Ended: "window_closed"}
			}
			res := s.finish(ctx, r, out, cur, end, region, applied, before)
			return withError(res, te, out, r.batch)
		}
		st, base, drags = cur, currentBaseline(cur), dragBaseline(cur)
	} else {
		// Without a shot, the baseline is read when the call starts.
		base = currentBaseline(st)
		// Without actions there is no send to read the window again, so it is read here: it may have closed while the
		// call waited for its lock, and another window may have its address now.
		cur, err := s.openState(sendCtx, r.window, st.StableID)
		switch {
		case ctx.Err() != nil:
			out.Wait = &waitOut{Ended: "cancelled"}
			return s.finish(ctx, r, out, st, end, region, false, before)
		case err == nil:
			st = cur
		case sendCtx.Err() == nil:
			return failShot(err)
		}
		// After the deadline there is no time to wait, and the capture checks the window itself.
		if sh != nil {
			base = shotBaseline(sh, st)
		}
	}

	p.set("waiting for the window to settle")
	wait, err := s.waitForWindow(ctx, r, base, drags, sendBy)
	if err != nil {
		te := asToolError(err)
		if !applied {
			return failShot(te)
		}
		res := s.finish(ctx, r, out, st, end, region, applied, before)
		return withError(res, te, out, r.batch)
	}
	out.Wait = wait
	p.set("capturing")
	return s.finish(ctx, r, out, st, end, region, applied, before)
}

// captureContext gives a capture the rest of the deadline, and at least its reserve, also after a cancellation.
func captureContext(ctx context.Context, end time.Time) (context.Context, context.CancelFunc) {
	capEnd := end
	if reserve := time.Now().Add(captureReserve); capEnd.Before(reserve) || ctx.Err() != nil {
		capEnd = reserve
	}
	return context.WithDeadline(context.WithoutCancel(ctx), capEnd)
}

// failCaptured reports a failure before any input was applied, with a shot of the window as it is now, so the agent
// can go on from there without another call. With capture "changes" it is a full shot, from which the agent reads
// coordinates anew.
func (s *Server) failCaptured(ctx context.Context, r *actRequest, out *actOut, te *toolError, end time.Time, stableID string, region *zoom) *mcp.CallToolResult {
	if r.captureMode == "none" || failsWithoutShot[te.Code] || s.stopping() {
		return errorResult(te, out, r.batch, nil)
	}
	capCtx, cancel := captureContext(ctx, end)
	defer cancel()
	sh, png, err := s.captureResult(capCtx, r, out, stableID, region)
	if err != nil {
		return errorResult(te, out, r.batch, nil)
	}
	out.Shot = shotInfo(sh)
	s.shots.returned(sh)
	sh.Animated = mergeAnimated(slices.Clone(sh.Animated), te.animated)
	images := oneImage(png)
	// The windows that earlier input opened after its act come with this result too, which the agent reads before it
	// acts again.
	if ctx.Err() == nil {
		capEnd, _ := capCtx.Deadline()
		var lateImages [][]byte
		out.LateWindows, lateImages = s.lateShots(ctx, capEnd, s.lateWindowsFor(capCtx, r.window, stableID))
		images = append(images, lateImages...)
	}
	return errorResult(te, out, r.batch, images)
}

// lockError maps a failure to take a lock, which what names, to a cancellation or the deadline.
func lockError(ctx context.Context, err error, start time.Time, what string) error {
	if ctx.Err() != nil {
		return errCancelled
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return &toolError{Code: "deadline", Message: fmt.Sprintf("waited %d ms for %s, until too little time was left before the deadline", time.Since(start).Milliseconds(), what),
			Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
	}
	return err
}

// finish captures, renews the lease and lists new windows, for every outcome after the locks were taken.
func (s *Server) finish(ctx context.Context, r *actRequest, out *actOut, st hypr.State, end time.Time, region *zoom, applied bool, before []hypr.Window) *mcp.CallToolResult {
	waited := time.Now()
	capCtx, cancel := captureContext(ctx, end)
	defer cancel()

	watched := applied && opensWindows(r.batch) && out.Wait != nil && (out.Wait.Ended == "quiet" || out.Wait.Ended == "no_redraw")
	closed := watched && s.watchForRedraw(ctx, capCtx, r, out.Wait, st.Capabilities, waited.Add(s.cfg.NewWindowGrace), end)
	closed = closed || out.Wait != nil && out.Wait.Ended == "window_closed"
	cancelled := out.Wait != nil && out.Wait.Ended == "cancelled"
	var images [][]byte
	if !closed && r.captureMode != "none" && !s.stopping() {
		var sh *shot
		var err error
		if r.captureMode == "changes" {
			// captureChanges describes the shot in out itself.
			sh, images, err = s.captureChanges(capCtx, r, out, st.StableID)
		} else {
			var png []byte
			if sh, png, err = s.captureResult(capCtx, r, out, st.StableID, region); err == nil {
				out.Shot, images = shotInfo(sh), oneImage(png)
			}
		}
		if err != nil {
			switch te := asToolError(err); {
			case te.Code == "window_gone":
				// The window closed after the wait, as a file chooser does a moment after it has drawn the path that Enter
				// gave it.
				closed = true
			case !applied:
				return errorResult(te, out, r.batch, nil)
			default:
				out.CaptureError = &errorOut{Code: te.Code, Message: te.Message}
			}
		} else {
			s.shots.returned(sh)
			// The animations count for the check of targets in the shot, as they do for its changes.
			sh.Animated = mergeAnimated(slices.Clone(sh.Animated), out.Wait.animatedBoxes())
			if out.Wait != nil {
				for _, a := range out.Wait.animated {
					if box, ok := sh.pixelBox(a); ok {
						out.Wait.Animated = append(out.Wait.Animated, box)
					}
				}
			}
		}
	}
	if !closed {
		switch {
		case applied:
			out.Lease = leaseInfo(s.renewLease(capCtx, r.window, st.Capabilities))
		default:
			// A lease keeps the emulated focus that input gave, and gives none itself. A call that sent nothing renews a
			// lease that is held, unless it was cancelled, but starts none, and none of another window at the address.
			if cur, err := s.hypr.State(capCtx, r.window); err == nil && cur.StableID == st.StableID {
				if cur.Lease != nil && cur.Lease.Held && !cancelled {
					out.Lease = leaseInfo(s.renewLease(capCtx, r.window, cur.Capabilities))
				} else {
					out.Lease = leaseInfo(cur.Lease)
				}
			}
		}
	}
	// An app may open a window a moment after the window that got the input has settled, so input that was delivered
	// is followed by a grace period, which the capture and the lease use too: input that can open a window.
	if applied && !cancelled && opensWindows(r.batch) {
		s.newWindowGrace(ctx, waited.Add(s.cfg.NewWindowGrace), end.Add(-newWindowListing))
	}
	after, err := s.listWindows(capCtx)
	if err == nil && !closed {
		if w := findWindow(after, r.window); w == nil || w.StableID != st.StableID {
			// The window closed after its capture, during the grace: the shot is of a window that is gone, which the
			// listing has forgotten, and so is the note about a capture region that it replaced.
			closed = true
			out.Shot, out.CaptureError, out.Lease, images, out.captureNote = nil, nil, nil, nil, ""
			if out.Wait != nil {
				out.Wait.Animated = nil
			}
		}
	}
	if closed {
		out.WindowClosed = true
		s.shots.dropWindow(r.window)
		s.setLeased(r.window, false)
	}
	reports := !cancelled && ctx.Err() == nil && !s.stopping()
	var late []newWindowOut
	if err == nil {
		out.NewWindows = s.newWindows(capCtx, before, after, r.window, windowPID(before, r.window), r.launch)
		if reports {
			// The result reports the window and the windows that its input opened, which are not late, and claims the
			// late windows of earlier acts when it can give them a shot.
			s.settle(append(newWindowKeys(out.NewWindows, nil, true), r.window+"/"+st.StableID)...)
			// The window that a closed one came from is reported as such, and not again as a late window.
			if closed {
				if out.Source = s.closedSource(capCtx, st.Source, r.window, after); out.Source != nil {
					s.settle(out.Source.Window + "/" + out.Source.StableID)
				}
			}
			if r.captureMode != "none" {
				late = s.claimLate(capCtx, after)
			}
		}
		if applied && !cancelled && opensWindows(r.batch) {
			s.watchOpener(r.window, st.StableID, windowPID(before, r.window), after)
		}
	}
	if r.captureMode != "none" && reports {
		if src := out.Source; src != nil {
			switch sh, png, err := s.captureSource(ctx, src.Window, src.StableID, r.quietMs, end); {
			case err == nil:
				s.shots.returned(sh)
				src.Shot = shotInfo(sh)
				images = append(images, png)
			case asToolError(err).Code == "window_gone":
				// It closed too, so there is no window to go back to.
				out.Source = nil
			}
		}
		images = append(images, s.newWindowShots(ctx, end, out.NewWindows, late)...)
	}
	// A late window that maps while this act runs is reported as late, with its shot, and not as one of the act's new
	// windows. One without a shot stays for a later result.
	out.LateWindows = s.keepLate(late)
	out.NewWindows = withoutLate(out.NewWindows, out.LateWindows)
	// Without a claim, as with capture "none", a new window may be a late one of an earlier act, which a later result
	// reports with its shot.
	if reports && r.captureMode != "none" {
		s.settle(newWindowKeys(out.NewWindows, late, false)...)
	}
	if applied && !closed && !cancelled && s.focusReturned(capCtx, r, st) {
		out.Actions.SharedFocusReturned, out.Actions.Interrupted = &focusReturnedOut{Effect: "unknown"}, true
	}
	// A drag that was going on before the call counts also without input.
	if out.Actions != nil && !closed && !cancelled && (applied || len(r.batch) == 0) {
		waited := ""
		if out.Wait != nil {
			waited = out.Wait.socket
		}
		out.Actions.DragAndDrop = s.dragged(capCtx, r, st, waited)
	}

	out.Summary = summaryLine(out, r.batch)
	// The hints speak of the input that went out, without scrolls held back.
	sent := r.batch
	if out.Actions != nil && out.Actions.HeldBack != "" {
		sent = r.batch[:out.Actions.Done]
	}
	wait, unchanged := waitHint(out.Wait, sent, r.shotID, out.Actions, out.Shot), noChangeHint(out, sent)
	// The returned focus already tells to check the shot before repeating anything, which the hints about a missing
	// redraw or change would repeat with a guess at the cause.
	focus := ""
	if out.Actions != nil && out.Actions.SharedFocusReturned != nil {
		focus, unchanged = focusReturnedHint(out.Shot), ""
		if out.Wait != nil && out.Wait.Ended == "no_redraw" {
			wait = ""
		}
	}
	// A drag-and-drop that did not go through tells why the window may not have changed, which the hints about a
	// missing redraw or change would only guess at.
	drag := ""
	if out.Actions != nil && out.Actions.DragAndDrop != nil {
		drag = dragHint(out.Actions.DragAndDrop, out.Shot)
		if drag != "" {
			unchanged = ""
			// A drag that goes on keeps the wait from ending before the deadline.
			if out.Wait != nil && (out.Wait.Ended == "no_redraw" || out.Wait.Ended == "timeout" && out.Actions.DragAndDrop.Outcome == "pending") {
				wait = ""
			}
		}
	}
	// That the window did not change says nothing about the input when it opened a window.
	if opened := openedHint(out.NewWindows); opened != "" {
		if out.Wait != nil && out.Wait.Ended == "no_redraw" {
			wait = ""
		}
		unchanged = opened
	}
	if closed {
		// The window is gone, so what the wait saw of it, such as no redraw after a click, says nothing that the input
		// should be repeated for.
		if out.Wait == nil || out.Wait.Ended != "cancelled" {
			wait = ""
		}
		wait = joinHints(wait, closedHint(out.Source))
	}
	// A shortcut beside the user's window that changed nothing may have come too late for the shared keyboard focus.
	out.Hint = joinHints(heldBackHint(out.Actions, r.batch), focus, drag, wait, unchanged, partScrolledHint(out, sent), splitHint(out), lateShortcutHint(out, sent, st),
		out.captureNote, frameHints(out.Shot), frameHints(sourceShot(out.Source)), newWindowsHint(out.NewWindows), lateHint(out.LateWindows), newWindowsHint(out.LateWindows))
	if out.Actions != nil {
		out.Hint = joinHints(out.Hint, out.Actions.composeNote)
	}
	if out.CaptureError != nil {
		out.Hint = joinHints(out.Hint, hintCaptureFailed)
	}
	// The zooms of the actions before a scroll come first, as the summary lists them.
	return newResult(out, out.resultText, append(slices.Clone(out.beforeImages), images...), "")
}

// focusReturned tells whether the user's own input to the window's app took the keyboard back from the window within a
// quarter of a second of a shortcut of the batch, from the plugin's interrupted counter before the input, in st, and
// now. A window without hold shares its app's connection with the user's focused window, and keeps the keyboard focus
// after a shortcut for that quarter of a second, which the wait and the capture have taken by now. Whether the shortcut
// ran before the focus returned is not known.
func (s *Server) focusReturned(ctx context.Context, r *actRequest, st hypr.State) bool {
	if st.Capabilities.Hold || st.Interrupted == nil || !slices.ContainsFunc(r.batch, func(a action) bool { return a.Kind == "key" }) {
		return false
	}
	cur, err := s.hypr.State(ctx, r.window)
	return err == nil && cur.StableID == st.StableID && deref(cur.SocketID) == deref(st.SocketID) && cur.Interrupted != nil && *cur.Interrupted > *st.Interrupted
}

// captureResult captures the window for the result of an act. A capture region whose shot no longer has the window's
// size, after a resize during the call, would show another part of the window than it did in that shot, so the whole
// window is captured instead, and the hint says why.
func (s *Server) captureResult(ctx context.Context, r *actRequest, out *actOut, stableID string, z *zoom) (*shot, []byte, error) {
	sh, png, err := s.capture(ctx, r.window, stableID, z)
	var zr *zoomResized
	if !errors.As(err, &zr) {
		return sh, png, err
	}
	if sh, png, err = s.capture(ctx, r.window, stableID, nil); err == nil {
		out.captureNote = fmt.Sprintf("The window was resized from %s to %s since shot %s, which the capture region was read from, so shot %s shows the whole window instead.",
			formatSize(zr.from.Size), formatSize(zr.size), zr.from.ID, sh.ID)
	}
	return sh, png, err
}

// withError turns a finished result into a failure, keeping its images, unless it reports a failure already.
func withError(res *mcp.CallToolResult, te *toolError, out *actOut, batch []action) *mcp.CallToolResult {
	if out.Error != nil {
		return res
	}
	var images [][]byte
	for _, c := range res.Content {
		if img, ok := c.(*mcp.ImageContent); ok {
			images = append(images, img.Data)
		}
	}
	return errorResult(te, out, batch, images)
}
