package server

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const (
	launchPoll = 100 * time.Millisecond
	// A new window settles once it has not drawn for this long.
	launchQuietMs   = 300
	launchSettleMax = 5 * time.Second
)

type launchArgs struct {
	Command   []string `json:"command" args:"required"`
	Workspace *string  `json:"workspace"`
	TimeoutMs *int     `json:"timeout_ms"`
}

// shellQuote quotes one argument for /bin/sh, which Hyprland runs the command with.
func shellQuote(arg string) string {
	return "'" + strings.ReplaceAll(arg, "'", `'\''`) + "'"
}

// launchLine is the shell command line that the plugin runs, as hyprmasq-launch does: the command executed by the
// shell, so the launched process is the one the plugin started.
func launchLine(command []string) string {
	line := "exec"
	for _, arg := range command {
		line += " " + shellQuote(arg)
	}
	return line
}

type launchedOut struct {
	Window    string `json:"window"`
	StableID  string `json:"stable_id"`
	Class     string `json:"class"`
	Title     string `json:"title"`
	PID       int    `json:"pid"`
	Workspace string `json:"workspace"`
}

// launchTool runs the launch tool.
func (s *Server) launchTool(ctx context.Context, c *call) *mcp.CallToolResult {
	var args launchArgs
	if err := c.decode(&args); err != nil {
		return simpleError(err)
	}
	if len(args.Command) == 0 || args.Command[0] == "" {
		return simpleError(invalidf("command must name a program, such as [\"foot\", \"-T\", \"scratch\"]"))
	}
	for i, arg := range args.Command {
		if strings.ContainsRune(arg, 0) {
			return simpleError(invalidf("command[%d] contains a NUL character", i))
		}
	}
	workspace := "hyprmasq"
	if args.Workspace != nil {
		workspace = *args.Workspace
	}
	if !workspacePattern.MatchString(workspace) {
		return simpleError(invalidf("workspace may only contain letters, digits, '_', '.' and '-', not %q", workspace))
	}
	timeoutMs, err := intArg(args.TimeoutMs, 30000, 1000, 300000, "timeout_ms")
	if err != nil {
		return simpleError(err)
	}
	token := randomHex(8)
	line := launchLine(args.Command)
	if request := "j/hyprmasq " + hypr.LaunchRequest(workspace, token, line); len(request) > hypr.MaxRequest {
		return simpleError(invalidf("command is too long: %d bytes as a request to the plugin, at most %d", len(request), hypr.MaxRequest))
	}
	timeout := time.Duration(timeoutMs) * time.Millisecond
	p := c.startProgress(ctx, timeout)
	defer p.stop()
	ctx, cancel := context.WithTimeout(ctx, timeout+launchSettleMax+captureReserve)
	defer cancel()
	start := time.Now()

	if err := s.checkPlugin(ctx); err != nil {
		return simpleError(err)
	}
	before, err := s.listWindows(ctx)
	if err != nil {
		return simpleError(err)
	}
	s.mu.Lock()
	s.tokens[token] = 0
	s.mu.Unlock()
	pid, err := s.hypr.Launch(ctx, workspace, token, line)
	s.mu.Lock()
	var refused *hypr.Error
	var unsent *hypr.ConnectError
	switch {
	case err == nil && pid > 0:
		s.tokens[token] = pid
	// The plugin started nothing, or never got the request. A request that failed otherwise may have started the
	// command, whose windows keep the token.
	case errors.As(err, &refused) || errors.Is(err, hypr.ErrPluginMissing) || errors.As(err, &unsent):
		delete(s.tokens, token)
	}
	s.mu.Unlock()
	if err != nil {
		return simpleError(err)
	}

	p.set("waiting for the first window")
	window, after, err := s.findLaunched(ctx, before, token, start.Add(timeout))
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return simpleError(&toolError{Code: "deadline", Message: fmt.Sprintf("no window of %s appeared within %d ms", args.Command[0], timeoutMs),
				Hint: "The command may still be starting, or it handed the request to an instance that is already running, which opens the window elsewhere. Call windows, or launch with an option for a separate instance."})
		}
		return simpleError(err)
	}

	p.set("waiting for the window to settle")
	st, err := s.openState(ctx, window.Address, window.StableID)
	if err != nil {
		return simpleError(err)
	}
	settle := min(launchSettleMax, max(time.Until(start.Add(timeout)), time.Second))
	reply, err := s.wait(ctx, window.Address, hypr.WaitArgs{Since: *st.Commits, QuietMs: launchQuietMs, DeadlineMs: int(settle.Milliseconds()),
		ID: s.waitID()})
	if err != nil {
		return simpleError(err)
	}
	wait := waitResult(reply)
	if reply.Ended == "window_closed" {
		return simpleError(&toolError{Code: "window_gone", Message: fmt.Sprintf("window %s of %s closed right after it appeared", window.Address, args.Command[0]), Hint: hintWindows})
	}
	// A cancelled wait ends early, so the window has not settled.
	if reply.Ended == "cancelled" || ctx.Err() != nil || s.stopping() {
		return simpleError(&toolError{Code: "cancelled", Message: fmt.Sprintf("the call was cancelled while window %s of %s settled", window.Address, args.Command[0])})
	}

	p.set("capturing")
	capCtx, capCancel := context.WithTimeout(context.WithoutCancel(ctx), launchSettleMax)
	defer capCancel()
	sh, png, err := s.capture(capCtx, window.Address, st.StableID, nil)
	if err != nil {
		return simpleError(err)
	}
	if latest, err := s.listWindows(capCtx); err == nil {
		after = latest
	}
	others := []newWindowOut{}
	for _, w := range s.newWindows(capCtx, before, after, window.Address, window.PID, token) {
		if w.Window != window.Address {
			others = append(others, w)
		}
	}

	s.shots.returned(sh)
	out := &launchOut{
		Window: launchedOut{Window: window.Address, StableID: window.StableID, Class: window.Class, Title: window.Title, PID: window.PID, Workspace: window.Workspace.Name},
		Wait:   wait, Shot: shotInfo(sh), NewWindows: others,
	}
	settled := fmt.Sprintf("settled after %d ms", reply.ElapsedMs)
	switch {
	case reply.Ended == "quiet":
	case wait.Resizing:
		settled = fmt.Sprintf("still resizing after %d ms", reply.ElapsedMs)
	case reply.RedrawObserved:
		settled = fmt.Sprintf("still drawing after %d ms", reply.ElapsedMs)
	default:
		// Input from elsewhere kept the quiet phase from ending.
		settled = fmt.Sprintf("not settled after %d ms, no redraw", reply.ElapsedMs)
	}
	parts := []string{
		fmt.Sprintf("launched %s %s %q", window.Address, window.Class, window.Title),
		fmt.Sprintf("pid %d", window.PID),
		"workspace " + window.Workspace.Name,
		settled,
		shotText(out.Shot),
	}
	if text := newWindowsText(others); text != "" {
		parts = append(parts, "also "+text)
	}
	out.Summary = strings.Join(parts, " · ")
	out.Hint = joinHints(frameHints(out.Shot), resizedHint(window, before, after))
	return newResult(out, out.resultText, oneImage(png), "")
}

// resizedHint names the windows on a launched window's workspace whose size changed since before the launch, such as
// tiled windows that make room for it.
func resizedHint(launched hypr.Window, before, after []hypr.Window) string {
	sizes := map[string][]int{}
	for _, w := range before {
		sizes[windowKey(w)] = w.Size
	}
	var resized []string
	for _, w := range after {
		size, ok := sizes[windowKey(w)]
		if ok && w.Workspace.ID == launched.Workspace.ID && !slices.Equal(size, w.Size) {
			resized = append(resized, w.Address)
		}
	}
	if len(resized) == 0 {
		return ""
	}
	return fmt.Sprintf("The new window resized the windows on its workspace (%s), so their earlier shots are stale: take a new screenshot before clicking in one.",
		strings.Join(resized, ", "))
}

type launchOut struct {
	resultText
	Window     launchedOut    `json:"window"`
	Wait       *waitOut       `json:"wait"`
	Shot       *shotOut       `json:"shot"`
	NewWindows []newWindowOut `json:"new_windows"`
}

// findLaunched polls the windows until one that was not there before carries the token.
func (s *Server) findLaunched(ctx context.Context, before []hypr.Window, token string, until time.Time) (hypr.Window, []hypr.Window, error) {
	known := map[string]bool{}
	for _, w := range before {
		known[windowKey(w)] = true
	}
	ctx, cancel := context.WithDeadline(ctx, until)
	defer cancel()
	for {
		launched, err := s.hypr.Launched(ctx)
		if err != nil {
			return hypr.Window{}, nil, err
		}
		// The address and the stable id, since the window may close between the two requests and leave its address to
		// another window.
		ours := map[string]bool{}
		for _, w := range launched {
			if w.Token == token {
				ours[windowKey(hypr.Window{Address: w.Address, StableID: w.StableID})] = true
			}
		}
		if len(ours) > 0 {
			clients, err := s.listWindows(ctx)
			if err != nil {
				return hypr.Window{}, nil, err
			}
			for _, w := range clients {
				if ours[windowKey(w)] && !known[windowKey(w)] {
					return w, clients, nil
				}
			}
		}
		select {
		case <-ctx.Done():
			return hypr.Window{}, nil, ctx.Err()
		case <-time.After(launchPoll):
		}
	}
}
