package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const (
	defaultDeadline = 15 * time.Second
	maxDeadline     = 30 * time.Second
	captureReserve  = time.Second
)

// errCancelled means that the client cancelled the call.
var errCancelled = errors.New("cancelled")

type actArgs struct {
	Window     string            `json:"window" args:"required"`
	Shot       string            `json:"shot"`
	Actions    []json.RawMessage `json:"actions"`
	Wait       json.RawMessage   `json:"wait"`
	Capture    json.RawMessage   `json:"capture"`
	DeadlineMs *int              `json:"deadline_ms"`
}

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
}

func intArg(v *int, def, lo, hi int, name string) (int, error) {
	if v == nil {
		return def, nil
	}
	if *v < lo || *v > hi {
		return 0, invalidf("%s must be %d to %d, not %d", name, lo, hi, *v)
	}
	return *v, nil
}

func parseActArgs(c *call) (*actRequest, int, error) {
	var args actArgs
	if err := c.decode(&args); err != nil {
		return nil, 0, err
	}
	total := len(args.Actions)
	window, err := canonicalWindow(args.Window)
	if err != nil {
		return nil, total, err
	}
	r := &actRequest{window: window, shotID: args.Shot, expectChange: true}
	if r.batch, err = parseActions(args.Actions); err != nil {
		return nil, total, err
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
	if r.shotID == "" {
		for i, a := range r.batch {
			if a.hasCoordinates() {
				return nil, total, invalidf("shot is missing: %s has coordinates, which are pixels of the shot they were read from", describe(i, a))
			}
		}
	}
	return r, total, nil
}

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

// validateBatch checks coordinates and capabilities before anything is sent.
func validateBatch(batch []action, sh *shot, caps hypr.Capabilities, address string) error {
	for i, a := range batch {
		for _, pt := range a.points() {
			if !sh.contains(pt.X, pt.Y) {
				return &toolError{Code: "out_of_bounds",
					Message: fmt.Sprintf("%s: %s%s,%s lies outside shot %s (%d×%d)", describe(i, a), pt.Name, formatNumber(pt.X), formatNumber(pt.Y), sh.ID, sh.Width, sh.Height),
					Hint:    fmt.Sprintf("Coordinates are pixels of shot %s, from 0,0 to %d,%d.", sh.ID, sh.Width-1, sh.Height-1)}
			}
			if x, y := sh.inputPoint(pt.X, pt.Y); !reachable(x, y) {
				return &toolError{Code: "out_of_bounds",
					Message: fmt.Sprintf("%s: %s%s,%s of shot %s lies left of or above the window, where pointer input cannot reach", describe(i, a), pt.Name,
						formatNumber(pt.X), formatNumber(pt.Y), sh.ID),
					Hint: hintUnreachable}
			}
		}
	}
	return checkCapabilities(batch, caps, address)
}

// checkCapabilities checks the batch against what the window can do now. Without hold, the plugin gives the user's
// focus back after every input frame, which releases a drag's button, gives a click the user's modifiers, and releases
// the keys that a chord holds, except for modifiers, whose state the plugin sends again with every frame.
func checkCapabilities(batch []action, caps hypr.Capabilities, address string) error {
	if caps.Hold {
		return nil
	}
	for i, a := range batch {
		what, hint := describe(i, a), hintHoldMissing
		switch {
		case a.Kind == "drag":
		case a.Kind == "click" && len(a.Modifiers) > 0:
			what += " with modifiers"
		case a.Kind == "key" && heldKey(a.Keys) != "":
			what += fmt.Sprintf(" holding %s while it presses %s", heldKey(a.Keys), a.Keys[len(a.Keys)-1])
			hint = hintChordHoldMissing
		default:
			continue
		}
		return &toolError{Code: "unsupported", Message: fmt.Sprintf("%s needs the hold capability, which window %s lacks", what, address), Hint: hint}
	}
	return nil
}

// clickSettleMs is the least time in milliseconds from the end of a click or drag to the first key or typed text after
// it in the same batch. Applications move the keyboard focus a moment after a click, and keys that come sooner may
// reach the window's shortcuts instead of the field: Spotify, clicked into its empty search field, took a space typed
// 200 ms after the click as play/pause, and inserted the same space when it came 350 ms after the click.
const clickSettleMs = 300

// translateBatch turns the batch into hyprmasq-input actions, with the points mapped from the shot's pixels. Keys that
// follow a click or drag sooner than clickSettleMs get a sleep in front of them, which belongs to their action. The
// time counts the sender's gap before every action and the sleeps, but not how long motion or scrolling take, which
// can only make the wait longer.
func translateBatch(batch []action, sh *shot) []inputStep {
	var steps []inputStep
	sinceClick := -1 // ms from the end of the last click or drag to the next sender action, while no keys followed it
	for i, a := range batch {
		for _, step := range translate(i, a, func(x, y float64) (float64, float64) { return sh.inputPoint(x, y) }) {
			if sinceClick >= 0 {
				sinceClick += actionGapMs
			}
			verb := step.Argv[0]
			if (verb == "text" || verb == "keys") && sinceClick >= 0 && sinceClick < clickSettleMs {
				// The sleep is an action of its own, so a gap follows it too.
				steps = append(steps, inputStep{Batch: i, Argv: []string{"sleep", strconv.Itoa(max(0, clickSettleMs-sinceClick-actionGapMs))}})
			}
			steps = append(steps, step)
			switch verb {
			case "click", "drag":
				sinceClick = 0
			case "text", "keys":
				sinceClick = -1
			case "sleep":
				if ms, err := strconv.Atoi(step.Argv[1]); err == nil && sinceClick >= 0 {
					sinceClick += ms
				}
			}
		}
	}
	return steps
}

// fitsDeadline refuses a batch whose input takes longer than the deadline leaves for it, which would be cut off
// part-way. It counts a key for every character, so hyprmasq-input checks the keys it resolves the text to again.
func fitsDeadline(steps []inputStep, deadline time.Duration) error {
	if len(steps) == 0 {
		return nil
	}
	return overDeadline(minSendTime(steps), deadline, false)
}

// overDeadline refuses input that takes at least least to send at its pace, or nil when the deadline leaves enough
// time for it. resolved tells that the least time is the sender's, which knows the keys that the keymap needs.
func overDeadline(least, deadline time.Duration, resolved bool) error {
	need, left := withRoundTrips(least), inputTime(deadline)
	if need <= left {
		return nil
	}
	how, perKey := "", "a character"
	if resolved {
		how, perKey = " with the active keymap", "a key, and characters typed through Compose take two keys or more"
	}
	te := &toolError{Code: "invalid_arguments",
		Message: fmt.Sprintf("the batch takes at least %d ms to send%s, about %d ms with round trips; deadline_ms %d leaves %d ms for it, since its last %d ms are kept for stopping input in time and for the capture",
			least.Milliseconds(), how, need.Milliseconds(), deadline.Milliseconds(), max(0, left.Milliseconds()), (deadline - left).Milliseconds())}
	if enough := deadlineFor(least); enough <= maxDeadline {
		te.Hint = fmt.Sprintf("Give a deadline_ms of at least %d, or split the batch over several calls.", enough.Milliseconds())
	} else if resolved {
		te.Hint = fmt.Sprintf("Split it over several calls: typing takes at least 15 ms %s.", perKey)
	} else {
		te.Hint = fmt.Sprintf("Split it over several calls: typing takes at least 15 ms %s, so one call types up to about %d characters.", perKey, typedWithin(maxDeadline))
	}
	return te
}

// tooLong maps the sender's refusal of a batch whose resolved keys take longer at their pace than its budget, left,
// the time to the end of input when it started. A batch that the deadline cannot fit is refused as fitsDeadline does;
// otherwise waiting for other calls took the time.
func tooLong(reply senderReply, deadline, left time.Duration) error {
	if reply.PaceMs == nil {
		return &toolError{Code: "deadline", Message: reply.Error.Message, Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
	}
	least := time.Duration(*reply.PaceMs) * time.Millisecond
	if err := overDeadline(least, deadline, true); err != nil {
		return err
	}
	return &toolError{Code: "deadline",
		Message: fmt.Sprintf("the batch takes about %d ms to send with the active keymap and round trips, and %d ms were left for it after waiting for other calls",
			withRoundTrips(least).Milliseconds(), max(0, left.Milliseconds())),
		Hint: "Retry, or give a larger deadline_ms (at most 30000)."}
}

// batchTypes tells whether the batch types text, whose characters the sender may type through Compose.
func batchTypes(batch []action) bool {
	for _, a := range batch {
		if a.Kind == "type" {
			return true
		}
	}
	return false
}

func batchHasCoordinates(batch []action) bool {
	for _, a := range batch {
		if a.hasCoordinates() {
			return true
		}
	}
	return false
}

// failsWithoutShot tells the errors after which a capture is not possible or not useful.
var failsWithoutShot = map[string]bool{"window_gone": true, "plugin_missing": true, "plugin_version": true, "cancelled": true}

func (s *Server) act(ctx context.Context, r *actRequest, start time.Time, p *progress) *mcp.CallToolResult {
	out := &actOut{Actions: &actionsOut{Total: len(r.batch), Delivery: "none"}, NewWindows: []newWindowOut{}}
	fail := func(err error) *mcp.CallToolResult {
		return errorResult(asToolError(err), out, r.batch, nil)
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
	st, err := s.openState(callCtx, r.window, "")
	if err != nil {
		return fail(err)
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
	if r.captureMode == "changes" {
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
	applied := false
	if len(r.batch) > 0 {
		var locale targetLocale
		if batchTypes(r.batch) {
			locale = s.windowLocale(before, r.window, st.StableID)
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
		st, base = cur, currentBaseline(cur)
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
	wait, err := s.waitForWindow(ctx, r, base, sendBy)
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
	return errorResult(te, out, r.batch, oneImage(png))
}

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

const (
	// How many new windows from the target an act returns a shot of.
	newWindowShots = 3
	// How long a new window gets at most to draw what it opened with before its shot, and how quiet it then has to be.
	newWindowSettle  = time.Second
	newWindowQuietMs = 200
	// The time before the deadline that the grace period leaves for listing the new windows.
	newWindowListing = 250 * time.Millisecond
)

// finish captures, renews the lease and lists new windows, for every outcome after the locks were taken.
func (s *Server) finish(ctx context.Context, r *actRequest, out *actOut, st hypr.State, end time.Time, region *zoom, applied bool, before []hypr.Window) *mcp.CallToolResult {
	waited := time.Now()
	capCtx, cancel := captureContext(ctx, end)
	defer cancel()

	closed := out.Wait != nil && out.Wait.Ended == "window_closed"
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
			te := asToolError(err)
			if !applied {
				return errorResult(te, out, r.batch, nil)
			}
			out.CaptureError = &errorOut{Code: te.Code, Message: te.Message}
		} else {
			s.shots.returned(sh)
			if out.Wait != nil {
				for _, a := range out.Wait.animated {
					if box, ok := sh.pixelBox(a); ok {
						out.Wait.Animated = append(out.Wait.Animated, box)
					}
				}
			}
		}
	}
	switch {
	case closed:
		s.shots.dropWindow(r.window)
		s.setLeased(r.window, false)
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
	// An app may open a window a moment after the window that got the input has settled, so input that was delivered
	// is followed by a grace period, which the capture and the lease use too: input that can open a window.
	if applied && !cancelled && opensWindows(r.batch) {
		s.newWindowGrace(ctx, waited.Add(s.cfg.NewWindowGrace), end.Add(-newWindowListing))
	}
	if after, err := s.listWindows(capCtx); err == nil {
		out.NewWindows = s.newWindows(capCtx, before, after, r.window, windowPID(before, r.window), r.launch)
	}
	if r.captureMode != "none" && !cancelled && ctx.Err() == nil && !s.stopping() {
		images = append(images, s.newWindowShots(ctx, out.NewWindows, end)...)
	}

	out.Summary = summaryLine(out, r.batch)
	wait, unchanged := waitHint(out.Wait, r.batch, r.shotID, out.Actions, out.Shot), noChangeHint(out, r.batch)
	// That the window did not change says nothing about the input when it opened a window.
	if opened := openedHint(out.NewWindows); opened != "" {
		if out.Wait != nil && out.Wait.Ended == "no_redraw" {
			wait = ""
		}
		unchanged = opened
	}
	out.Hint = joinHints(wait, unchanged, out.captureNote, frameHints(out.Shot), newWindowsHint(out.NewWindows))
	if out.Actions != nil {
		out.Hint = joinHints(out.Hint, out.Actions.composeNote)
	}
	if out.CaptureError != nil {
		out.Hint = joinHints(out.Hint, "The input was applied, but the screenshot failed. Take a screenshot before repeating anything.")
	}
	return newResult(out, out.resultText, images, "")
}

// opensWindows tells whether a batch has input that can open a window, after which act watches for new windows: a
// click, a drag, a key, or text with a line break, which presses Enter. Text that only types characters, scrolling,
// motion and pauses do not, as the plugin counts the user's input.
func opensWindows(batch []action) bool {
	for _, a := range batch {
		switch a.Kind {
		case "click", "drag", "key":
			return true
		case "type":
			if strings.ContainsAny(a.Text, "\r\n") {
				return true
			}
		}
	}
	return false
}

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

// newWindowShots takes a shot of each new window that came from the target, up to newWindowShots of them, within the
// deadline, so the agent can act in it without another call. It returns their images.
func (s *Server) newWindowShots(ctx context.Context, windows []newWindowOut, end time.Time) [][]byte {
	var images [][]byte
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

func windowPID(windows []hypr.Window, address string) int {
	for _, w := range windows {
		if w.Address == address {
			return w.PID
		}
	}
	return 0
}

// releaseLease ends this session's lease on the window.
func (s *Server) releaseLease(ctx context.Context, window string) {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_, err := s.hypr.LeaseRelease(ctx, window, s.owner)
	s.leaseAnswered(window, false, err)
}

// leaseAnswered records whether this session holds the window's lease after a lease request. Without an answer of the
// plugin the request may still have taken effect, so the window stays on the list that Shutdown releases.
func (s *Server) leaseAnswered(window string, held bool, err error) {
	var pe *hypr.Error
	switch {
	case err == nil:
		s.setLeased(window, held)
	case errors.As(err, &pe):
		// The plugin refused it, so it changed nothing.
		if pe.Code == "window_gone" || pe.Code == "no_socket" {
			s.setLeased(window, false)
		}
	default:
		s.setLeased(window, true)
	}
}

func (s *Server) renewLease(ctx context.Context, window string, caps hypr.Capabilities) *hypr.Lease {
	if s.stopping() {
		// Shutdown releases the leases it knows of once the running calls are done.
		return nil
	}
	if !caps.Lease {
		return nil
	}
	lease, err := s.hypr.Lease(ctx, window, s.cfg.LeaseMs, s.owner)
	s.leaseAnswered(window, lease.Held, err)
	if err != nil {
		return nil
	}
	return &lease
}

// geometryPoll is how often the window's size is checked while a batch that depends on it is sent.
const geometryPoll = 25 * time.Millisecond

// watchesGeometry tells whether a resize while the batch runs can make coordinates wrong that are still to be sent:
// those of an action after the first, of a drag, which moves over time, or of the only action, which goes out once
// hyprmasq-input has connected. A resize while keys follow a first click or scroll does not stop the batch, since the
// keys themselves may cause it.
func watchesGeometry(batch []action) bool {
	return (len(batch) > 1 && batchHasCoordinates(batch[1:])) || (len(batch) > 0 && (batch[0].Kind == "drag" || (len(batch) == 1 && batch[0].hasCoordinates())))
}

// send takes the send lock, checks the window again, and runs the batch in one hyprmasq-input, which checks every
// action, including whether the keymap can type it, before it sends anything. It returns the window's state read just
// before the input, whose counter is the wait's baseline. clientCtx tells a cancellation from the deadline of sendCtx.
func (s *Server) send(clientCtx, sendCtx context.Context, r *actRequest, steps []inputStep, sh *shot, st hypr.State, start time.Time, p *progress,
	locale targetLocale) (*actionsOut, hypr.State, error) {
	out := &actionsOut{Total: len(r.batch), Delivery: "none"}
	p.set("waiting for input to other windows")
	sendLock, err := acquireLock(sendCtx, s.sendLockPath())
	if err != nil {
		return out, st, lockError(clientCtx, err, start, "the send lock, held by a call on another window")
	}
	defer sendLock.release()

	// Other calls may have changed the window while this one waited for the locks.
	cur, err := s.openState(sendCtx, r.window, st.StableID)
	switch {
	case clientCtx.Err() != nil:
		return out, st, errCancelled
	case sendCtx.Err() != nil:
		return out, st, deadlineWhileSending("none", r.deadline)
	case err != nil:
		return out, st, err
	case sh != nil && batchHasCoordinates(r.batch) && cur.Size != sh.Size:
		return out, cur, &toolError{Code: "stale_shot", Message: fmt.Sprintf("the window was resized from %s to %s since shot %s", formatSize(sh.Size), formatSize(cur.Size), sh.ID),
			Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	if err := checkCapabilities(r.batch, cur.Capabilities, r.window); err != nil {
		return out, cur, err
	}
	// Input that would be cut off part-way is not started. sendCtx ends where the time for input does.
	if inputBy, ok := sendCtx.Deadline(); ok {
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

// restoreLease undoes the renewal before a batch that sent nothing: without input, a new lease would hold no focus,
// and a lease of another session goes back to it for the time it had left.
func (s *Server) restoreLease(ctx context.Context, window string, before *hypr.Lease, read time.Time) {
	if s.stopping() || (before != nil && before.Held && before.Owner == s.owner) {
		return
	}
	remaining := 0
	if before != nil && before.Held {
		remaining = before.RemainingMs - int(time.Since(read).Milliseconds())
	}
	if remaining < 1 {
		s.releaseLease(ctx, window)
		return
	}
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_, err := s.hypr.Lease(ctx, window, remaining, before.Owner)
	s.leaseAnswered(window, false, err)
}

// runBatch runs the batch in one hyprmasq-input, and stops it when the window is resized or replaced meanwhile.
func (s *Server) runBatch(clientCtx, sendCtx context.Context, r *actRequest, steps []inputStep, sh *shot, st hypr.State, locale targetLocale) (*actionsOut, error) {
	out := &actionsOut{Total: len(r.batch), Delivery: "none"}
	runCtx, stop := context.WithCancelCause(sendCtx)
	defer stop(nil)
	watched := make(chan struct{})
	if sh != nil && watchesGeometry(r.batch) {
		go s.watchGeometry(runCtx, stop, r.window, sh, st.StableID, watched)
	} else {
		close(watched)
	}
	inputBy, _ := sendCtx.Deadline()
	left := time.Until(inputBy)
	reply, err := s.runSender(runCtx, *st.Socket, steps, paceBudget(left), locale.name)
	stop(nil)
	<-watched
	if reply.Delivery != "none" {
		out.composeNote = composeNote(reply, locale)
	}
	var changed *toolError
	errors.As(context.Cause(runCtx), &changed)
	if err == nil && reply.Delivery == "none" && reply.Error != nil && reply.Error.Code == "too_long" {
		return out, tooLong(reply, r.deadline, left)
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
		out.Delivery = "uncertain"
		out.FailedIndex = ptr(0)
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

// pausesFrom is the first step after the pauses that the sender failed at, or -1 when it failed at no pause.
func pausesFrom(steps []inputStep, reply senderReply) int {
	if reply.FailedIndex == nil || *reply.FailedIndex < 0 || *reply.FailedIndex >= len(steps) || steps[*reply.FailedIndex].Argv[0] != "sleep" {
		return -1
	}
	next := *reply.FailedIndex
	for next < len(steps) && steps[next].Argv[0] == "sleep" {
		next++
	}
	return next
}

// windowGone tells whether the plugin reports the window as closed.
func (s *Server) windowGone(ctx context.Context, window string) bool {
	ctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	defer cancel()
	_, err := s.hypr.State(ctx, window)
	var pe *hypr.Error
	return errors.As(err, &pe) && pe.Code == "window_gone"
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

func ptr[T any](v T) *T {
	return &v
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
		if step == "move" || step == "drag" {
			return &toolError{Code: "out_of_bounds", Message: fmt.Sprintf("%s lies outside the area that pointer input reaches: %s", where, message),
				Hint: hintOutsideRegions + " " + hintScreenshot, ShotHint: hintOutsideRegions + " " + hintScreenshotShot}
		}
		return &toolError{Code: code, Message: where + ": " + message, Hint: "Fix the action and call again."}
	case "cannot_type":
		return &toolError{Code: code, Message: where + ": " + message, Hint: hintCannotType}
	case "input_paused":
		hint := "The user is working in another window of the same X server. Retry later, or ask the user."
		if delivery != "none" {
			// Retrying would type again what the window already got.
			hint = "The user is working in another window of the same X server, so the plugin paused the input and dropped the rest. Go on once the user has left it, or ask the user."
		}
		return &toolError{Code: code, Message: message, Hint: hint}
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

// A baseline is a count of the window's commits that a change is measured from.
type baseline struct {
	commits int64
	// The id of the socket it counts for, or "" from a plugin that names none.
	socket string
}

func deref(s *string) string {
	if s == nil {
		return ""
	}
	return *s
}

// currentBaseline is the counter of a state with an open socket.
func currentBaseline(st hypr.State) baseline {
	return baseline{*st.Commits, deref(st.SocketID)}
}

// shotBaseline measures a change since a shot. A shot from an earlier socket, which was closed and opened again, was
// taken before the current socket opened, so every commit the current one counted came after it.
func shotBaseline(sh *shot, st hypr.State) baseline {
	cur := currentBaseline(st)
	if sh.SocketID == "" || cur.socket == "" {
		// Without socket ids, a count above the counter can only be of an earlier socket.
		return baseline{min(sh.Commits, cur.commits), cur.socket}
	}
	if sh.SocketID != cur.socket {
		return baseline{0, cur.socket}
	}
	return baseline{sh.Commits, cur.socket}
}

// socketReplaced tells whether the wait read the counter of a socket that was opened after the baseline was read.
func socketReplaced(base baseline, reply hypr.WaitReply, err error) bool {
	var pe *hypr.Error
	if errors.As(err, &pe) {
		// The counter never falls, so a baseline above it was read from an earlier socket.
		return pe.Code == "invalid_arguments"
	}
	return err == nil && base.socket != "" && reply.SocketID != nil && *reply.SocketID != base.socket
}

// waitForWindow waits for the window to redraw and settle, until sendBy at the latest.
func (s *Server) waitForWindow(ctx context.Context, r *actRequest, base baseline, sendBy time.Time) (*waitOut, error) {
	expect := 0
	if r.expectChange {
		expect = r.expectMs
	}
	out := &waitOut{expectMs: expect}
	var reply hypr.WaitReply
	var err error
	for {
		remaining := time.Until(sendBy).Milliseconds()
		if remaining < 1 {
			out.Ended, out.Summary, out.skipped = "timeout", waitSummary("timeout", false, false), true
			return out, nil
		}
		reply, err = s.wait(ctx, r.window, hypr.WaitArgs{Since: base.commits, ExpectMs: expect, QuietMs: r.quietMs, DeadlineMs: int(remaining), ID: s.waitID()})
		if base.commits == 0 || !socketReplaced(base, reply, err) || ctx.Err() != nil {
			break
		}
		// The new socket opened after the baseline was read, so every commit it counted came after it.
		base = baseline{}
	}
	if err != nil {
		var pe *hypr.Error
		if errors.As(err, &pe) && pe.Code == "window_gone" {
			out.Ended = "window_closed"
			return out, nil
		}
		if ctx.Err() != nil {
			out.Ended = "cancelled"
			return out, nil
		}
		return nil, err
	}
	out = waitResult(reply)
	out.expectMs = expect
	return out, nil
}

// wait runs the plugin's wait. When ctx is cancelled, it cancels the wait by its id and still reads the reply.
func (s *Server) wait(ctx context.Context, window string, a hypr.WaitArgs) (hypr.WaitReply, error) {
	reqCtx, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Duration(a.DeadlineMs)*time.Millisecond+2*time.Second)
	defer cancel()
	replied := make(chan struct{})
	cancelled := make(chan struct{})
	stop := context.AfterFunc(ctx, func() {
		defer close(cancelled)
		// The wait may not have reached the plugin yet, so the cancel is repeated until it finds it.
		for range 20 {
			cctx, ccancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
			found, _ := s.hypr.Cancel(cctx, a.ID)
			ccancel()
			if found {
				return
			}
			select {
			case <-replied:
				return
			case <-time.After(50 * time.Millisecond):
			}
		}
	})
	reply, err := s.hypr.Wait(reqCtx, window, a)
	close(replied)
	if !stop() {
		<-cancelled
	}
	return reply, err
}
