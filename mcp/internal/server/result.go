package server

import (
	"context"
	"errors"
	"fmt"
	"math"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// toolError is a failure reported as a tool result with isError.
type toolError struct {
	Code    string
	Message string
	Hint    string
	// Replaces Hint when the result carries a shot taken after the failure.
	ShotHint string
}

func (e *toolError) Error() string {
	return e.Code + ": " + e.Message
}

func invalidf(format string, args ...any) *toolError {
	return &toolError{Code: "invalid_arguments", Message: fmt.Sprintf(format, args...), Hint: "Fix the named field and call again. Nothing was sent."}
}

const (
	hintWindows        = "Call windows to find the window again."
	hintScreenshot     = "Take a screenshot and read the coordinates from the new shot."
	hintScreenshotShot = "Read the coordinates from the new shot in this result."
	hintPluginUser     = "Tell the user. hyprmasq cannot work until this is fixed."
	hintUncertain      = "The sender lost its connection before the plugin confirmed the input, so any action may or may not have been applied."
	hintCannotType     = "Leave the character out, or ask the user how to enter it."
	hintHoldMissing    = "This window has the user's keyboard or pointer focus, or shares its Wayland connection with the window that has it, so buttons and modifiers cannot stay pressed between input frames. Use the keyboard instead, for example shift and the arrow keys to select, or a click without modifiers."
	hintUnreachable    = "Pointer input reaches the window and the area right of and below it. Click the part of the popup that lies over the window, or use the keyboard, for example the arrow keys and enter in a menu."
	hintOutsideRegions = "Pointer input reaches the window and the area right of and below it up to the edges of its monitor; the window may have moved since the shot."
	hintClosedBySent   = "The window closed while the input was sent, maybe because of it, for example through a quit shortcut or a close button. Call windows to see what is open now."
	hintNothingSent    = "Nothing was sent."

	// The plugin sends the modifier state again with every frame, but no other held key.
	hintChordHoldMissing = "This window has the user's keyboard or pointer focus, or shares its Wayland connection with the window that has it, so of the keys of a chord only modifiers such as ctrl, alt, shift and super stay pressed between input frames, and the others would arrive one after another. Hold only modifiers, or press the keys in separate key actions."
)

// asToolError maps errors of Hyprland, the plugin and the sender to tool errors.
func asToolError(err error) *toolError {
	var te *toolError
	if errors.As(err, &te) {
		return te
	}
	var zr *zoomResized
	if errors.As(err, &zr) {
		return &toolError{Code: "stale_shot", Message: zr.Error(), Hint: hintScreenshot}
	}
	// An ended context fails whatever request was running, so it is checked before what the request reported.
	if errors.Is(err, errCancelled) || errors.Is(err, context.Canceled) {
		return &toolError{Code: "cancelled", Message: "the client cancelled the call"}
	}
	if errors.Is(err, context.DeadlineExceeded) {
		return &toolError{Code: "deadline", Message: "the deadline arrived", Hint: "Give a larger deadline_ms (at most 30000)."}
	}
	var ce *hypr.ConnectError
	if errors.As(err, &ce) {
		return &toolError{Code: "plugin_missing", Message: ce.Error(), Hint: "Tell the user that Hyprland's socket cannot be reached; HYPRLAND_INSTANCE_SIGNATURE may name another Hyprland."}
	}
	if errors.Is(err, hypr.ErrPluginMissing) {
		return &toolError{Code: "plugin_missing", Message: err.Error(), Hint: hintPluginUser}
	}
	var pe *hypr.Error
	if errors.As(err, &pe) {
		switch pe.Code {
		case "window_gone":
			return &toolError{Code: "window_gone", Message: pe.Message, Hint: hintWindows}
		case "unloaded":
			return &toolError{Code: "plugin_missing", Message: "the hyprmasq plugin was unloaded", Hint: hintPluginUser}
		case "unsupported", "invalid_arguments":
			return &toolError{Code: pe.Code, Message: pe.Message}
		case "":
			return &toolError{Code: "plugin_version", Message: "the hyprmasq plugin answers without error codes, so it is older than this server: " + pe.Message, Hint: hintPluginUser}
		}
		return &toolError{Code: "internal", Message: pe.Message}
	}
	return &toolError{Code: "internal", Message: err.Error()}
}

type rectOut struct {
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

type shotOut struct {
	ID     string  `json:"id"`
	Width  int     `json:"width"`
	Height int     `json:"height"`
	Frame  rectOut `json:"frame"`
	// Present only when true: the shot shows an older frame.
	Resizing bool `json:"resizing,omitempty"`
	// For a changes shot: what changed since the shot it was compared with, whose pixels it has. It has no image of its
	// own.
	Changes *changesOut `json:"changes,omitempty"`
	// Why capture "changes" took a full shot instead.
	FullShot string `json:"full_shot,omitempty"`
}

type changesOut struct {
	Base string `json:"base"`
	// A zoom into each region that changed, in the order of the images.
	Regions []regionOut `json:"regions"`
	// Tiny changes, such as a blinking caret, that no zoom shows.
	LeftOut int `json:"left_out,omitempty"`
}

// regionOut is a zoom shot of a changes shot, and the box of the changes shot's pixels that it shows.
type regionOut struct {
	Shot        string `json:"shot"`
	ImageWidth  int    `json:"image_width"`
	ImageHeight int    `json:"image_height"`
	X           int    `json:"x"`
	Y           int    `json:"y"`
	Width       int    `json:"width"`
	Height      int    `json:"height"`
}

func shotInfo(s *shot) *shotOut {
	if s == nil {
		return nil
	}
	return &shotOut{ID: s.ID, Width: s.Width, Height: s.Height, Frame: rectOut(s.Frame), Resizing: s.Resizing}
}

// shotText is the summary's part about a shot.
func shotText(s *shotOut) string {
	if c := s.Changes; c != nil {
		switch len(c.Regions) {
		case 0:
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
	return lines
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

// resizingHint explains a shot of a window that had not caught up with a resize, or "".
func resizingHint(s *shotOut) string {
	if s == nil || !s.Resizing {
		return ""
	}
	return fmt.Sprintf("Shot %s shows an older frame, since the window had not yet redrawn at its new size, so act takes no coordinates from it. To wait for the redraw, act with no actions and wait.expect_change false, and read the coordinates from the shot it returns; if the window does not redraw, use the keyboard or tell the user.", s.ID)
}

type progressOut struct {
	Keys int `json:"keys"`
}

type actionsOut struct {
	Done        int          `json:"done"`
	Total       int          `json:"total"`
	Delivery    string       `json:"delivery"`
	FailedIndex *int         `json:"failed_index,omitempty"`
	Progress    *progressOut `json:"progress,omitempty"`
	// Why characters typed through Compose may have arrived as others, for the hint.
	composeNote string
}

type waitOut struct {
	Ended          string `json:"ended"`
	Summary        string `json:"summary,omitempty"`
	RedrawObserved bool   `json:"redraw_observed"`
	Commits        int64  `json:"commits"`
	QuietMs        int    `json:"quiet_ms"`
	ElapsedMs      int    `json:"elapsed_ms"`
	// Present only when true: the wait timed out while the window had not caught up with a resize.
	Resizing bool `json:"resizing,omitempty"`
	// The change phase's limit, for the summary line.
	expectMs int
	// The deadline left no time for the wait, so it did not run.
	skipped bool
}

// waitSummary sums up how a wait ended: settled, resizing when it timed out while the window had not caught up with a
// resize, no_change when the window drew nothing, also when the deadline ended the change phase or left no time to
// wait, and busy when it was still drawing at the deadline.
func waitSummary(ended string, redrawObserved, resizing bool) string {
	switch {
	case ended == "quiet":
		return "settled"
	case ended == "timeout" && resizing:
		return "resizing"
	case ended == "no_redraw", ended == "timeout" && !redrawObserved:
		return "no_change"
	case ended == "timeout":
		return "busy"
	}
	return ""
}

// waitResult is the outcome of a plugin wait that ran. Of a resize it reports only one that outlasted the wait's
// deadline, since the quiet phase does not end before the window has caught up.
func waitResult(r hypr.WaitReply) *waitOut {
	resizing := r.Ended == "timeout" && r.Resizing
	return &waitOut{Ended: r.Ended, Summary: waitSummary(r.Ended, r.RedrawObserved, resizing), RedrawObserved: r.RedrawObserved,
		Commits: r.CommitsSince, QuietMs: r.QuietMs, ElapsedMs: r.ElapsedMs, Resizing: resizing}
}

type leaseOut struct {
	Held       bool `json:"held"`
	RemainingS int  `json:"remaining_s"`
}

func leaseInfo(l *hypr.Lease) *leaseOut {
	if l == nil || !l.Held {
		return &leaseOut{}
	}
	return &leaseOut{Held: true, RemainingS: int(math.Round(float64(l.RemainingMs) / 1000))}
}

type newWindowOut struct {
	Window   string `json:"window"`
	StableID string `json:"stable_id"`
	Class    string `json:"class"`
	Title    string `json:"title"`
	PID      int    `json:"pid"`
	SamePID  bool   `json:"same_pid"`
	Launched bool   `json:"launched"`
}

type errorOut struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// resultText is the summary line and the hint of a result, which are its text content and part of its structured
// content too: some clients pass the model only the text, others only the structured content.
type resultText struct {
	Summary string `json:"summary,omitempty"`
	Hint    string `json:"hint,omitempty"`
}

// newResult returns a result whose text is the lead line, if any, the summary and the hint, followed by the images.
func newResult(structured any, rt resultText, images [][]byte, lead string) *mcp.CallToolResult {
	var lines []string
	for _, line := range []string{lead, rt.Summary, rt.Hint} {
		if line != "" {
			lines = append(lines, line)
		}
	}
	content := []mcp.Content{&mcp.TextContent{Text: strings.Join(lines, "\n")}}
	for _, png := range images {
		content = append(content, imageContent(png))
	}
	return &mcp.CallToolResult{Content: content, StructuredContent: structured}
}

// oneImage is the images of a result with one image, or none for nil.
func oneImage(png []byte) [][]byte {
	if png == nil {
		return nil
	}
	return [][]byte{png}
}

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

// actOut is the structured content of act.
type actOut struct {
	Error *errorOut `json:"error,omitempty"`
	resultText
	Actions      *actionsOut    `json:"actions,omitempty"`
	Wait         *waitOut       `json:"wait,omitempty"`
	Shot         *shotOut       `json:"shot,omitempty"`
	CaptureError *errorOut      `json:"capture_error,omitempty"`
	Lease        *leaseOut      `json:"lease,omitempty"`
	NewWindows   []newWindowOut `json:"new_windows"`
	// Why the shot shows the whole window instead of the capture region, for the hint.
	captureNote string
}

// actionsText counts the actions the application received as delivered: the sender cannot see whether the application
// acted on them as meant, which only the shot can show.
func actionsText(a *actionsOut, batch []action) string {
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
	return text
}

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
		return fmt.Sprintf("%s · quiet %d ms · %d ms", observed, w.QuietMs, w.ElapsedMs)
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

func newWindowsText(windows []newWindowOut) string {
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
		parts = append(parts, fmt.Sprintf("%s (%s %q%s)", w.Window, w.Class, w.Title, relation))
	}
	noun := "new window"
	if len(windows) > 1 {
		noun = "new windows"
	}
	return noun + " " + strings.Join(parts, ", ")
}

// summaryLine is the one line every act result starts with.
func summaryLine(out *actOut, batch []action) string {
	var parts []string
	add := func(s string) {
		if s != "" {
			parts = append(parts, s)
		}
	}
	add(actionsText(out.Actions, batch))
	add(waitText(out.Wait))
	if out.Shot != nil {
		add(shotText(out.Shot))
	} else if out.CaptureError != nil {
		add("capture failed: " + out.CaptureError.Message)
	}
	if out.Lease != nil {
		if out.Lease.Held {
			add(fmt.Sprintf("lease %d s", out.Lease.RemainingS))
		} else {
			add("no lease")
		}
	}
	add(newWindowsText(out.NewWindows))
	return strings.Join(append([]string{strings.Join(parts, " · ")}, changesLines(out.Shot)...), "\n")
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
			since := "since the shot"
			if shotID != "" {
				since = "since shot " + shotID
			}
			return fmt.Sprintf("Nothing was drawn %s. If the window is still loading, wait again: act with no actions, a larger wait.expect_ms, and a deadline_ms at least 1000 above it.", since)
		case "click":
			return "The click may have missed or had no visible effect. " + look + " before repeating it."
		case "key", "type":
			return "The keys may have gone to another widget or had no visible effect. " + look + " before repeating them."
		case "move":
			return "The pointer motion had no visible effect. Use wait.expect_change false for hover that may change nothing."
		default:
			return "The input may have had no visible effect. " + look + " before repeating it."
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
		return "Nothing was drawn before the deadline. " + look + "; to wait longer for a first redraw, act with no actions and a larger deadline_ms (at most 30000)."
	case "window_closed":
		return "The window closed. Any new windows are listed; call windows for the rest."
	}
	return ""
}

func imageContent(png []byte) mcp.Content {
	return &mcp.ImageContent{Data: png, MIMEType: "image/png"}
}

// errorResult reports err with what was sent so far, and the images of the shot taken after the failure, if any.
func errorResult(te *toolError, out *actOut, batch []action, images [][]byte) *mcp.CallToolResult {
	if out == nil {
		out = &actOut{NewWindows: []newWindowOut{}}
	}
	out.Error = &errorOut{Code: te.Code, Message: te.Message}
	out.Summary, out.Hint = "", finalHint(te, out)
	// A summary of a call that sent nothing and has no shot would only repeat the error.
	if out.Actions == nil || out.Actions.Delivery != "none" || out.Shot != nil {
		out.Summary = summaryLine(out, batch)
	}
	res := newResult(out, out.resultText, images, errorLine(te))
	res.IsError = true
	return res
}

func errorLine(te *toolError) string {
	return fmt.Sprintf("error %s: %s", te.Code, te.Message)
}

// finalHint completes the hint of an act failure with what the input needs: that nothing was sent, or that input
// which was sent is never repeated without looking first.
func finalHint(te *toolError, out *actOut) string {
	hint := te.Hint
	switch {
	case out.Shot == nil || te.ShotHint == "":
	case !out.Shot.Resizing:
		hint = te.ShotHint
	case te.Code == "stale_shot":
		// The new shot shows an older frame too, and the hint about it says where to get coordinates.
		hint = ""
	}
	hint = joinHints(hint, out.captureNote)
	if out.Actions != nil {
		hint = joinHints(hint, out.Actions.composeNote)
	}
	if out.Actions != nil && out.Actions.Total > 0 {
		add := hintNothingSent
		if out.Actions.Delivery != "none" {
			add = lookAt(out.Shot) + " before repeating anything."
		}
		if !strings.Contains(hint, add) {
			hint = joinHints(hint, add)
		}
	}
	return joinHints(hint, resizingHint(out.Shot))
}

// errorOnlyOut is the structured content of a failure of a tool other than act.
type errorOnlyOut struct {
	Error *errorOut `json:"error"`
	resultText
}

// simpleError reports a failure of a tool other than act.
func simpleError(err error) *mcp.CallToolResult {
	te := asToolError(err)
	out := &errorOnlyOut{Error: &errorOut{Code: te.Code, Message: te.Message}, resultText: resultText{Hint: te.Hint}}
	res := newResult(out, out.resultText, nil, errorLine(te))
	res.IsError = true
	return res
}

// noChangeHint says that delivered input may have had no visible effect, when the window settled and a changes shot
// found no change beyond tiny ones, or "" otherwise. The wait's hint covers a window that drew nothing, and the motion of a move, as for
// hover, may change nothing.
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
	if kind == "" || kind == "move" {
		return ""
	}
	base := out.Shot.Changes.Base
	if out.Shot.Changes.LeftOut > 0 {
		return fmt.Sprintf("Only tiny changes since %s, such as a caret that moved or blinked, so the input may have missed or had little visible effect. Look at %s before repeating it.",
			base, base)
	}
	return fmt.Sprintf("Nothing changed since %s, so the input may have missed or had no visible effect. Look at %s before repeating it.", base, base)
}
