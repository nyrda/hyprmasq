package server

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// validateBatch checks coordinates and capabilities before anything is sent.
func validateBatch(batch []action, sh *shot, caps hypr.Capabilities, address string) error {
	motion := 0
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
		// The points of a spline are known once it is sampled, which a batch of long paths would do at length.
		if a.Kind == "path" {
			if motion += len(pathInput(a, sh.inputPoint)); motion > maxPathMotion {
				return invalidf("%s: the paths of the batch move through more than %d points, given or sampled from a spline, which at 10 ms a step take longer than the largest deadline_ms; split them over several calls",
					describe(i, a), maxPathMotion)
			}
		}
	}
	return checkCapabilities(batch, caps, address)
}

// checkCapabilities checks the batch against what the window can do now. Without hold, the plugin gives the user's
// pointer focus back after every input frame, which releases the button of a path, and the keyboard focus once the keys
// are up and a shortcut among them has settled, or at once when the user's own input to the app comes, which gives a
// click the user's modifiers and releases the keys that a chord holds, except for modifiers, whose state the plugin
// sends again with every frame.
func checkCapabilities(batch []action, caps hypr.Capabilities, address string) error {
	if caps.Hold {
		return nil
	}
	for i, a := range batch {
		what, hint := describe(i, a), hintHoldMissing
		switch {
		case a.holdsButton():
		case (a.Kind == "click" || a.Kind == "path") && len(a.Modifiers) > 0:
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

// clickSettleMs is the least time in milliseconds from the end of a click, or of a path that holds a button, to the
// first key or typed text after it in the same batch. Applications move the keyboard focus a moment after a click, and
// keys that come sooner may reach the window's shortcuts instead of the field: Spotify, clicked into its empty search
// field, took a space typed 200 ms after the click as play/pause, and inserted the same space when it came 350 ms after
// the click.
const clickSettleMs = 300

// A click, or a path that holds a button, that more actions of the batch follow gets the window's answer before they
// go: the sender stops at a sync after it, and act waits meanwhile with the plugin's wait for a change since the
// counter before the input, then for quiet (see answerClick). In the benchmark VM at 1920×1080 and 2560×1440, Chromium
// drew its answer to a click, such as a menu of the page or a caret, within 20 ms of the release, before such a wait
// starts, and nothing after it; a click on empty space drew nothing.
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

// translateBatch turns the batch into hyprmasq-input actions, with the points mapped from the shot's pixels. A click,
// or a path that holds a button, that more actions follow gets a sync after it, which belongs to the next action and
// takes the place of the gap between them: the sender waits there while act waits for the window. The sync lasts at
// least until keys or text after the click come clickSettleMs after its end. That time counts the sender's gap before
// every action and the sleeps, but not how long motion or scrolling take, which can only make the wait longer.
func translateBatch(batch []action, sh *shot) []inputStep {
	var steps []inputStep
	sync := -1      // the index of the sync after the last click or held-button path, while no keys followed it
	sinceClick := 0 // ms from the end of that click or path to the next sender action, without the sync
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
		if (a.Kind == "click" || a.holdsButton()) && i < len(batch)-1 {
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
	return overDeadline(minSendTime(steps), deadline, false, typesText(steps))
}

// typesText tells whether the steps type text.
func typesText(steps []inputStep) bool {
	for _, step := range steps {
		if step.Argv[0] == "text" {
			return true
		}
	}
	return false
}

// overDeadline refuses input that takes at least least to send at its pace, or nil when the deadline leaves enough
// time for it. resolved tells that the least time is the sender's, which knows the keys that the keymap needs, and
// typed that the input types text, which the hint for input that no deadline fits counts in characters.
func overDeadline(least, deadline time.Duration, resolved, typed bool) error {
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
	} else if !typed {
		te.Hint = "Split it over several calls: repeated plain keys have 1 ms gaps, and chords with modifiers 40 ms."
	} else if resolved {
		te.Hint = fmt.Sprintf("Split it over several calls: typing takes at least 15 ms %s.", perKey)
	} else {
		te.Hint = fmt.Sprintf("Split it over several calls: typing takes at least 15 ms %s, so one call types up to about %d characters.", perKey, typedWithin(maxDeadline))
	}
	return te
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

// batchHasCoordinates tells whether an action of the batch has coordinates, which are pixels of a shot.
func batchHasCoordinates(batch []action) bool {
	for _, a := range batch {
		if a.hasCoordinates() {
			return true
		}
	}
	return false
}

// opensWindows tells whether a batch has input that can open a window, after which act watches for new windows: a
// click, a path that holds a button, a key, or text with a line break, which presses Enter. Text that only types
// characters, scrolling, motion and pauses do not, as the plugin counts emulated input.
func opensWindows(batch []action) bool {
	for _, a := range batch {
		switch {
		case a.Kind == "click" || a.Kind == "key" || a.holdsButton():
			return true
		case a.Kind == "type":
			if strings.ContainsAny(a.Text, "\r\n") {
				return true
			}
		}
	}
	return false
}
