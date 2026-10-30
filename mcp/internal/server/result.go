package server

import (
	"math"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

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

// shotInfo describes a shot for a result, or is nil without one.
func shotInfo(sh *shot) *shotOut {
	if sh == nil {
		return nil
	}
	return &shotOut{ID: sh.ID, Width: sh.Width, Height: sh.Height, Frame: rectOut(sh.Frame), Resizing: sh.Resizing, MayBeStale: sh.Starved != nil, starved: sh.Starved}
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
	// Present only when the user's own input to the window's app took the keyboard focus that the window shares with
	// the user's window back within a quarter of a second of a shortcut or function key of the batch. It records that
	// event, not whether the shortcut took effect, which nothing the compositor sees tells.
	SharedFocusReturned *focusReturnedOut `json:"shared_focus_returned,omitempty"`
	// The same event under its older name, kept for clients that read it: true with shared_focus_returned.
	Interrupted bool `json:"interrupted,omitempty"`
	// Present only when the window's app started a drag-and-drop during the batch: how the last one went.
	DragAndDrop *dragOut `json:"drag_and_drop,omitempty"`
	// Present only when act did not send all of the batch, and why: the scrolls that end it, after the actions before
	// them (see split.go), or a press and the actions after it, when the window had a popup open that the press would
	// only have closed (see closingPopup). failed_index is the first action held back.
	HeldBack string `json:"held_back,omitempty"`
	// The press at failed_index was held back, not the scrolls at the end.
	heldPress bool
	// Why characters typed through Compose may have arrived as others, for the hint.
	composeNote string
}

// dragOut tells how the last drag-and-drop went that the window's app started during the batch.
type dragOut struct {
	// finished: the app took the drop and confirmed it. cancelled: nothing was dropped. pending: it had not ended by
	// the deadline, see phase. unconfirmed: the drop went out, but the app did not confirm it within 10 s, so whether
	// it took the item is not known.
	Outcome string `json:"outcome"`
	// For a pending one: active, before the drop, or dropped, waiting for the app to confirm it.
	Phase string `json:"phase,omitempty"`
	// It ran within the window's app, which only a drag of a window with the dnd capability does. Without it, hyprmasq
	// cancelled it before it started, to protect the user's input.
	Private bool `json:"private"`
	// Why it was cancelled or unconfirmed, see dragReasons.
	Reason string `json:"reason,omitempty"`
	// Present only when the app started more than one, or when a drag of the batch started none: how many it started,
	// and of them how many finished, were cancelled, are unconfirmed and still go on, with the reasons of those that
	// were cancelled or are unconfirmed, and how many drags of the batch started none. The outcome is then finished
	// only when every drag of the batch finished, and otherwise partial, unless none finished.
	Count       int      `json:"count,omitempty"`
	Finished    int      `json:"finished,omitempty"`
	Cancelled   int      `json:"cancelled,omitempty"`
	Unconfirmed int      `json:"unconfirmed,omitempty"`
	Pending     int      `json:"pending,omitempty"`
	Reasons     []string `json:"reasons,omitempty"`
	None        int      `json:"none,omitempty"`
}

type focusReturnedOut struct {
	// Always "unknown": the shortcut may have taken effect before the focus returned, or not.
	Effect string `json:"effect"`
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
	// Present only when the window drew again during the grace period after input that can open a window, which act
	// waited out before the capture.
	Again *waitAgainOut `json:"again,omitempty"`
	// The change phase's limit, for the summary line.
	expectMs int
	// The deadline left no time for the wait, so it did not run.
	skipped bool
	// The window's commit counter when the wait ended, and the socket it counts for, or "" when the plugin named none.
	counter int64
	socket  string
}

// animatedBoxes is the animations of the wait in window-local logical pixels, or nil without a wait.
func (w *waitOut) animatedBoxes() []hypr.IntRect {
	if w == nil {
		return nil
	}
	return w.animated
}

// waitAgainOut is the wait for a window that drew again after it had settled, until it settled again.
type waitAgainOut struct {
	QuietMs   int `json:"quiet_ms"`
	ElapsedMs int `json:"elapsed_ms"`
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
		Commits: r.CommitsSince, QuietMs: r.QuietMs, ElapsedMs: r.ElapsedMs, Resizing: resizing, animated: r.Animated, counter: r.Commits, socket: deref(r.SocketID)}
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

// sourceShot is the shot of the window that a closed target came from, or nil.
func sourceShot(src *sourceOut) *shotOut {
	if src == nil {
		return nil
	}
	return src.Shot
}

// resultText is the summary line and the hint of a result, which are its text content and part of its structured
// content too: some clients pass the model only the text, others only the structured content.
type resultText struct {
	Summary string `json:"summary,omitempty"`
	Hint    string `json:"hint,omitempty"`
	// What act did with the session's recording: started it, or saved it, or why it stopped.
	Recording string `json:"recording,omitempty"`
}

// newResult returns a result whose text is the lead line, if any, the summary and the hint, followed by the images.
func newResult(structured any, rt resultText, images [][]byte, lead string) *mcp.CallToolResult {
	var lines []string
	for _, line := range []string{lead, rt.Summary, rt.Hint, rt.Recording} {
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
	// For a batch that ended with scrolling after other input, which act sent in two parts, what the input before the
	// scroll did. Wait and Shot are then the scroll's. See split.go.
	BeforeScroll *beforeScrollOut `json:"before_scroll,omitempty"`
	Scroll       *pageScrollOut   `json:"scroll,omitempty"`
	// The images of BeforeScroll's shot, which come before the others.
	beforeImages [][]byte
	// Why the shot shows the whole window instead of the capture region, for the hint.
	captureNote string
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

func imageContent(png []byte) mcp.Content {
	return &mcp.ImageContent{Data: png, MIMEType: "image/png"}
}
