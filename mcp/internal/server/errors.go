package server

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// errCancelled means that the client cancelled the call.
var errCancelled = errors.New("cancelled")

// toolError is a failure, reported as a tool result with isError unless the result has a shot.
type toolError struct {
	Code    string
	Message string
	Hint    string
	// Replaces Hint when the result carries a shot taken after the failure.
	ShotHint string
	// For a target that moved: the animations that the window drew meanwhile, which the shot taken after the failure
	// records.
	animated []hypr.IntRect
}

func (e *toolError) Error() string {
	return e.Code + ": " + e.Message
}

// invalidf reports an invalid argument, before anything was sent.
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
	hintCaptureFailed  = "The input was applied, but the screenshot failed. Take a screenshot before repeating anything."

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

// errorOut is the error of a result.
type errorOut struct {
	Code    string `json:"code"`
	Message string `json:"message"`
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

// errorLine is the first line of a failure's text.
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
