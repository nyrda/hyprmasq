package server

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"slices"
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
// pointer focus back after every input frame, which releases a drag's button, and the keyboard focus once the keys are
// up and a shortcut among them has settled, or at once when the user's own input to the app comes, which gives a click
// the user's modifiers and releases the keys that a chord holds, except for modifiers, whose state the plugin sends
// again with every frame.
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

// A click or drag that more actions of the batch follow gets the window's answer before they go: the sender stops at a
// sync after it, and act waits meanwhile with the plugin's wait for a change since the counter before the input, then
// for quiet (see answerClick). In the benchmark VM at 1920×1080 and 2560×1440, Chromium drew its answer to a click,
// such as a menu of the page or a caret, within 20 ms of the release, before such a wait starts, and nothing after it;
// a click on empty space drew nothing.
const (
	// How long the window gets to start drawing, from the start of the wait, before the batch goes on without a redraw.
	answerExpectMs = 150
	// How long the window has to be quiet after drawing: a CSS transition drew every 16 ms, while a page that a link
	// loaded paused for up to about 100 ms between its first frames and the new page, which a quiet time of 150 ms
	// waited out every time.
	answerQuietMs = 150
	// The most that the wait takes: a page that a link loaded was quiet for 150 ms 580 to 690 ms after the click, and
	// a 400 ms transition 530 to 570 ms after it.
	answerMaxMs = 1000
)

// translateBatch turns the batch into hyprmasq-input actions, with the points mapped from the shot's pixels. A click or
// drag that more actions follow gets a sync after it, which belongs to the next action and takes the place of the gap
// between them: the sender waits there while act waits for the window. The sync lasts at least until keys or text
// after the click come clickSettleMs after its end. That time counts the sender's gap before every action and the
// sleeps, but not how long motion or scrolling take, which can only make the wait longer.
func translateBatch(batch []action, sh *shot) []inputStep {
	var steps []inputStep
	sync := -1      // the index of the sync after the last click or drag, while no keys followed it
	sinceClick := 0 // ms from the end of that click or drag to the next sender action, without the sync
	for i, a := range batch {
		for _, step := range translate(i, a, func(x, y float64) (float64, float64) { return sh.inputPoint(x, y) }) {
			verb := step.Argv[0]
			if sync >= 0 {
				sinceClick += actionGapMs
				switch verb {
				case "text", "keys":
					// The sync replaces the gap after the click, so it lasts as long as the gap and what follows it fall
					// short of clickSettleMs.
					steps[sync].Argv[1] = strconv.Itoa(max(0, clickSettleMs-sinceClick+actionGapMs))
					sync = -1
				case "sleep":
					if ms, err := strconv.Atoi(step.Argv[1]); err == nil {
						sinceClick += ms
					}
				}
			}
			steps = append(steps, step)
		}
		if (a.Kind == "click" || a.Kind == "drag") && i < len(batch)-1 {
			steps = append(steps, inputStep{Batch: i + 1, Argv: []string{"sync", "0", strconv.Itoa(answerMaxMs)}})
			sync, sinceClick = len(steps)-1, 0
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
			switch sh, png, err := s.sourceShot(ctx, src.Window, src.StableID, r.quietMs, end); {
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
	if applied && !closed && !cancelled {
		out.Actions.Interrupted = s.interrupted(capCtx, r, st)
	}

	out.Summary = summaryLine(out, r.batch)
	wait, unchanged := waitHint(out.Wait, r.batch, r.shotID, out.Actions, out.Shot), noChangeHint(out, r.batch)
	// The user's input tells why a shortcut may have done nothing, which the hints about a missing redraw or change
	// would only guess at.
	interrupted := ""
	if out.Actions != nil && out.Actions.Interrupted {
		interrupted, unchanged = hintInterrupted, ""
		if out.Wait != nil && out.Wait.Ended == "no_redraw" {
			wait = ""
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
	out.Hint = joinHints(interrupted, wait, unchanged, out.captureNote, frameHints(out.Shot), frameHints(sourceShot(out.Source)), newWindowsHint(out.NewWindows),
		lateHint(out.LateWindows), newWindowsHint(out.LateWindows))
	if out.Actions != nil {
		out.Hint = joinHints(out.Hint, out.Actions.composeNote)
	}
	if out.CaptureError != nil {
		out.Hint = joinHints(out.Hint, "The input was applied, but the screenshot failed. Take a screenshot before repeating anything.")
	}
	return newResult(out, out.resultText, images, "")
}

// interrupted tells whether the user's own input to the window's app took the keyboard back from the window before a
// shortcut of the batch had settled, from the plugin's counter before the input, in st, and now. A window without hold
// shares its app's connection with the user's focused window, and keeps the keyboard focus after a shortcut only until
// it has settled, a quarter of a second, which the wait and the capture have taken by now.
func (s *Server) interrupted(ctx context.Context, r *actRequest, st hypr.State) bool {
	if st.Capabilities.Hold || st.Interrupted == nil || !slices.ContainsFunc(r.batch, func(a action) bool { return a.Kind == "key" }) {
		return false
	}
	cur, err := s.hypr.State(ctx, r.window)
	return err == nil && cur.StableID == st.StableID && deref(cur.SocketID) == deref(st.SocketID) && cur.Interrupted != nil && *cur.Interrupted > *st.Interrupted
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

// opensWindows tells whether a batch has input that can open a window, after which act watches for new windows: a
// click, a drag, a key, or text with a line break, which presses Enter. Text that only types characters, scrolling,
// motion and pauses do not, as the plugin counts emulated input.
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

// watchForRedraw goes on waiting for the window through the grace period after input that can open a window, until
// the time given. An app may draw again a moment after it settled, when work that does not draw ends: a browser the
// page that Enter loads, a mail client the results of a search it starts after a pause in typing. The window gets frame
// callbacks meanwhile, without which an app on a hidden workspace does not draw at all, and a redraw is waited out with
// the call's quiet time, so the capture shows the window after it. After a wait that saw no redraw, a redraw now makes
// it one that did; one that is still going on at the deadline makes it a timeout. It tells whether the window closed
// meanwhile, which keeps the wait as it ended, as a close after the wait does. A reply for another socket, which
// replaced the one the wait counted for, and other errors keep the wait as it ended too.
func (s *Server) watchForRedraw(ctx, capCtx context.Context, r *actRequest, w *waitOut, caps hypr.Capabilities, until, end time.Time) (closed bool) {
	// A deadline that leaves less than the grace period shortens the watch, keeping the quiet time for a redraw at its
	// end.
	budget := func() (deadline, expect int64) {
		deadline = time.Until(end.Add(-captureReserve - newWindowListing)).Milliseconds()
		return deadline, min(time.Until(until).Milliseconds(), deadline-int64(r.quietMs))
	}
	if _, expect := budget(); expect < 1 {
		return false
	}
	// A lease shorter than the wait and the watch would run out meanwhile, which ends the emulated focus, and with it
	// a menu that the input opened, before the capture. Its round trip, which a busy compositor stretches, comes out
	// of the watch.
	s.renewLease(capCtx, r.window, caps)
	deadline, expect := budget()
	if expect < 1 {
		return false
	}
	// A spinner or a blinking caret that was animating before the input does not count as the window drawing again.
	reply, err := s.wait(ctx, r.window, hypr.WaitArgs{Since: w.counter, ExpectMs: int(expect), QuietMs: r.quietMs, DeadlineMs: int(deadline), ID: s.waitID(),
		IgnoreAnimations: true})
	var pe *hypr.Error
	switch {
	case errors.As(err, &pe) && pe.Code == "window_gone", err == nil && reply.Ended == "window_closed":
		return true
	case ctx.Err() != nil:
		w.Ended, w.Summary = "cancelled", ""
		return false
	case err != nil, w.socket != "" && deref(reply.SocketID) != w.socket:
		return false
	}
	// Its commits count, also those of an animation, which end no change phase, and so do its animations, which the
	// capture leaves out of the changes.
	w.Commits += reply.CommitsSince
	w.animated = mergeAnimated(w.animated, reply.Animated)
	switch {
	case reply.Ended == "no_redraw":
		return false
	case w.Ended == "quiet" && w.RedrawObserved && reply.Ended == "quiet":
		w.Again = &waitAgainOut{QuietMs: reply.QuietMs, ElapsedMs: reply.ElapsedMs}
		return false
	}
	resizing := reply.Ended == "timeout" && reply.Resizing
	w.Ended, w.Summary, w.RedrawObserved, w.Resizing = reply.Ended, waitSummary(reply.Ended, true, resizing), true, resizing
	w.QuietMs, w.ElapsedMs = reply.QuietMs, w.ElapsedMs+reply.ElapsedMs
	return false
}

// mergeAnimated adds the animations of a second wait to those of the first, joining the boxes of one animation as the
// plugin does within a wait: boxes that overlap or touch, when the box around both is at most 1.5 times the larger. An
// animation that goes on through both waits is then counted once where its area weighs the changes.
func mergeAnimated(boxes, more []hypr.IntRect) []hypr.IntRect {
	size := func(r image.Rectangle) int { return r.Dx() * r.Dy() }
	for _, m := range more {
		box := rectOf(m)
		for joined := true; joined; {
			joined = false
			for i, b := range boxes {
				r := rectOf(b)
				touch := r.Min.X <= box.Max.X && box.Min.X <= r.Max.X && r.Min.Y <= box.Max.Y && box.Min.Y <= r.Max.Y
				if u := r.Union(box); touch && 2*size(u) <= 3*max(size(r), size(box)) {
					box, boxes, joined = u, append(boxes[:i:i], boxes[i+1:]...), true
					break
				}
			}
		}
		boxes = append(boxes, intRectOf(box))
	}
	return boxes
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

// sourceShot captures the window that a closed target came from once it has answered the close: after a wait that
// sends it frame callbacks, for its first redraw up to sourceExpectMs, and if it drew, for quietMs of quiet, within
// sourceSettleMax and the deadline, which leaves half of the capture's reserve to the capture. Animations, such as a
// caret that blinks, neither end the change phase nor keep the quiet phase from ending, and the shot records them. A
// starved window gets frame callbacks from the capture's own wait.
func (s *Server) sourceShot(ctx context.Context, window, stableID string, quietMs int, end time.Time) (*shot, []byte, error) {
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
	case sh != nil && batchHasCoordinates(r.batch) && cur.Size != sh.Size:
		return cur, &toolError{Code: "stale_shot", Message: fmt.Sprintf("the window was resized from %s to %s since shot %s", formatSize(sh.Size), formatSize(cur.Size), sh.ID),
			Hint: hintScreenshot, ShotHint: hintScreenshotShot}
	}
	return cur, checkCapabilities(r.batch, cur.Capabilities, r.window)
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
	base := currentBaseline(st)
	answer := func(step int) {
		if step >= 0 && step < len(steps) && steps[step].Argv[0] == "sync" {
			base = s.answerClick(runCtx, r.window, base, steps[step+1:], inputBy)
		}
	}
	reply, err := s.runSender(runCtx, *st.Socket, steps, paceBudget(left), locale.name, answer)
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

// answerClick waits for the window to answer a click while the sender waits at the sync after it: with the plugin's
// wait for a change since base, at most answerExpectMs, then for answerQuietMs of quiet, within answerMaxMs, and early
// enough that the rest of the batch still fits before inputBy. It returns the baseline for the next sync, the counter
// when the wait ended. A wait that fails leaves the sender to go on after its sync's least time.
func (s *Server) answerClick(ctx context.Context, window string, base baseline, rest []inputStep, inputBy time.Time) baseline {
	limit := min(answerMaxMs*time.Millisecond, time.Until(inputBy)-sendTime(rest)).Milliseconds()
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
