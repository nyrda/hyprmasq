package server

import (
	"context"
	"errors"
	"fmt"
	"math"
	"slices"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// toolError is a failure, reported as a tool result with isError unless the result has a shot.
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

// boxOut is a box in pixels of a shot.
type boxOut struct {
	X      int `json:"x"`
	Y      int `json:"y"`
	Width  int `json:"width"`
	Height int `json:"height"`
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
	// Present only when true: the window was starved and did not draw before the capture, so the shot may show an
	// older frame.
	MayBeStale bool `json:"may_be_stale,omitempty"`
	starved    *starvation
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
	// Changes within the animations of the wait, which no zoom shows.
	Animated int `json:"animated,omitempty"`
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
	return &shotOut{ID: s.ID, Width: s.Width, Height: s.Height, Frame: rectOut(s.Frame), Resizing: s.Resizing, MayBeStale: s.Starved != nil, starved: s.Starved}
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

// frameHints explains a shot that shows, or may show, an older frame, or "".
func frameHints(s *shotOut) string {
	return joinHints(resizingHint(s), starvedHint(s))
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
	// The animations the window drew during the wait, which the quiet phase did not wait for, in pixels of the result's
	// shot.
	Animated []boxOut `json:"animated,omitempty"`
	// The animations in window-local logical pixels.
	animated []hypr.IntRect
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
		Commits: r.CommitsSince, QuietMs: r.QuietMs, ElapsedMs: r.ElapsedMs, Resizing: resizing, animated: r.Animated}
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
	Window    string `json:"window"`
	StableID  string `json:"stable_id"`
	Class     string `json:"class"`
	Title     string `json:"title"`
	PID       int    `json:"pid"`
	SamePID   bool   `json:"same_pid"`
	Launched  bool   `json:"launched"`
	Workspace string `json:"workspace"`
	Visible   bool   `json:"visible"`
	// It has the user's keyboard focus.
	Focused bool `json:"focused"`
	// The window it came from, as the plugin found it when it mapped, if it found one.
	Source string `json:"source,omitempty"`
	// The plugin opened it on the workspace of its source, without the focus.
	Placed bool `json:"placed"`
	// Why the plugin did not: disabled, user_input or workspace.
	NotPlaced string `json:"not_placed,omitempty"`
	// A shot of it, for a window that came from the target of an act.
	Shot *shotOut `json:"shot,omitempty"`

	workspaceID int
	// It came from the target, as the plugin found it, or belongs to a launch by this server.
	fromTarget bool
	// For a late window: when this call claimed it.
	claimed time.Time
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
	Actions      *actionsOut `json:"actions,omitempty"`
	Wait         *waitOut    `json:"wait,omitempty"`
	Shot         *shotOut    `json:"shot,omitempty"`
	CaptureError *errorOut   `json:"capture_error,omitempty"`
	// The window closed during the call: its wait ended so, or it closed after the wait.
	WindowClosed bool `json:"window_closed,omitempty"`
	// For a window that closed, the window it came from, if that is still open.
	Source     *sourceOut     `json:"source,omitempty"`
	Lease      *leaseOut      `json:"lease,omitempty"`
	NewWindows []newWindowOut `json:"new_windows"`
	// Windows that the input of earlier acts opened after those acts had returned, which no result has reported yet.
	LateWindows []newWindowOut `json:"late_windows,omitempty"`
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
		return fmt.Sprintf("%s · quiet %d ms%s · %d ms", observed, w.QuietMs, animationText(w), w.ElapsedMs)
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

// sourceOut is the window that a target that closed came from, where the agent goes on.
type sourceOut struct {
	Window    string   `json:"window"`
	StableID  string   `json:"stable_id"`
	Class     string   `json:"class"`
	Title     string   `json:"title"`
	Workspace string   `json:"workspace"`
	Visible   bool     `json:"visible"`
	Shot      *shotOut `json:"shot,omitempty"`
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

// sourceShot is the shot of the window that a closed target came from, or nil.
func sourceShot(src *sourceOut) *shotOut {
	if src == nil {
		return nil
	}
	return src.Shot
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

// notPlacedReasons say why the plugin did not open a window on the workspace of the window it came from.
var notPlacedReasons = map[string]string{
	"disabled":  "the plugin's place_new_windows option is off",
	"workspace": "no window rule can name the workspace of the window it came from",
	"":          "the plugin could not tell that hyprmasq's input opened it",
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
	if out.WindowClosed && (out.Wait == nil || out.Wait.Ended != "window_closed") {
		add("window closed")
	}
	if out.Shot != nil {
		add(shotText(out.Shot))
	} else if out.CaptureError != nil {
		add("capture failed: " + out.CaptureError.Message)
	}
	add(sourceText(out.Source))
	if out.Lease != nil {
		if out.Lease.Held {
			add(fmt.Sprintf("lease %d s", out.Lease.RemainingS))
		} else {
			add("no lease")
		}
	}
	add(newWindowsText(out.NewWindows))
	add(lateWindowsText(out.LateWindows))
	lines := append([]string{strings.Join(parts, " · ")}, changesLines(out.Shot)...)
	lines = append(lines, sourceLines(out.Source)...)
	lines = append(lines, newWindowLines(out.NewWindows, "new window")...)
	return strings.Join(append(lines, newWindowLines(out.LateWindows, "late window")...), "\n")
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
	// A call that fell short but captured a shot, of the window, of the window that a window that closed came from, or
	// of a window that input opened, is no tool error: clients such as Claude Code show only the text of an error, and
	// the agent goes on from the shot.
	hasShot := func(w newWindowOut) bool { return w.Shot != nil }
	res.IsError = out.Shot == nil && sourceShot(out.Source) == nil && !slices.ContainsFunc(out.NewWindows, hasShot) && !slices.ContainsFunc(out.LateWindows, hasShot)
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
	return joinHints(hint, frameHints(out.Shot), sourceHint(out.Source), frameHints(sourceShot(out.Source)), openedHint(out.NewWindows), newWindowsHint(out.NewWindows),
		lateHint(out.LateWindows), newWindowsHint(out.LateWindows))
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
	if out.Shot.Changes.Animated > 0 {
		return fmt.Sprintf("Nothing changed since %s outside the animations, so the input may have missed, or its effect lies inside them. Look at %s, or take a screenshot to see the animations as they are now, before repeating it.",
			base, base)
	}
	if out.Shot.Changes.LeftOut > 0 {
		return fmt.Sprintf("Only tiny changes since %s, such as a caret that moved or blinked, so the input may have missed or had little visible effect. Look at %s before repeating it.",
			base, base)
	}
	return fmt.Sprintf("Nothing changed since %s, so the input may have missed or had no visible effect. Look at %s before repeating it.", base, base)
}
