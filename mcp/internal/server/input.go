package server

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// senderReply is what hyprmasq-input --json prints when it exits.
type senderReply struct {
	OK          bool   `json:"ok"`
	Done        int    `json:"done"`
	Total       int    `json:"total"`
	FailedIndex *int   `json:"failedIndex"`
	Delivery    string `json:"delivery"`
	Progress    *struct {
		Keys int `json:"keys"`
	} `json:"progress"`
	// With --budget, the least time the batch takes at its pace, with the keys the sender resolved it to.
	PaceMs *int `json:"paceMs"`
	// When a character was typed through Compose: the locale whose table typed it, and the one asked for, which differs
	// when there was no table for it.
	Compose *struct {
		Locale    string `json:"locale"`
		Requested string `json:"requested"`
	} `json:"compose"`
	Error *struct {
		Code    string `json:"code"`
		Message string `json:"message"`
	} `json:"error"`
}

// Stopping a sender. After SIGTERM, hyprmasq-input takes up to its CANCEL_MS (tools/hyprmasq-input.c) to release what it
// holds, get that confirmed and print its result.
const (
	// The sender's CANCEL_MS.
	senderCancel = 1000 * time.Millisecond
	// How long before the wait's and the capture's time the deadline stops a sender: its CANCEL_MS and a margin to exit
	// and to have its result read, so a sender that the deadline cuts off has reported by then.
	senderStop = senderCancel + 250*time.Millisecond
	// How long a sender gets after SIGTERM before it is killed: twice its CANCEL_MS, so only one that hangs is killed.
	senderKillDelay = 2 * senderCancel
)

// The pacing of hyprmasq-input in milliseconds, as in tools/hyprmasq-input.c.
const (
	actionGapMs   = 50
	motionPressMs = 30
	clickHoldMs   = 50
	clickGapMs    = 80
	chordModMs    = 10
	chordHoldMs   = 15
	chordGapMs    = 15
	typeHoldMs    = 5
	typeGapMs     = 10
	repeatHoldMs  = 0
	repeatGapMs   = 1
	wheelGapMs    = 20
	dragHoldMs    = 50
	dragStepMs    = 10
	dragStepPx    = 10
	dragMinSteps  = 21
	dragMaxSteps  = 2000
)

// minSendTime is the least time hyprmasq-input takes to send the steps at its pace, without its round trips.
func minSendTime(steps []inputStep) time.Duration {
	ms := 0
	value := func(s string) float64 {
		v, _ := strconv.ParseFloat(s, 64)
		return v
	}
	for i, step := range steps {
		argv := step.Argv
		// A sync takes the place of the gap after the action before it, and lasts at least its MIN.
		if argv[0] == "sync" {
			ms += int(value(argv[1]))
			continue
		}
		if i > 0 {
			gap := actionGapMs
			if prev := steps[i-1].Argv; prev[0] == "sync" {
				gap = max(0, gap-int(value(prev[1])))
			}
			ms += gap
		}
		switch argv[0] {
		case "click":
			count := int(value(argv[2]))
			ms += count*clickHoldMs + (count-1)*clickGapMs
			if argv[3] != "-" {
				ms += chordModMs
			}
		case "path":
			// path N X1 Y1 … XN YN BUTTON MODS. Without a button, it moves a step after its first point.
			n := int(value(argv[1]))
			points := make([][2]float64, n)
			for i := range points {
				points[i] = [2]float64{value(argv[2+2*i]), value(argv[3+2*i])}
			}
			moves := pathMoves(points)
			if argv[2+2*n] == "none" {
				ms += moves * dragStepMs
			} else {
				ms += motionPressMs + 2*dragHoldMs + (moves-1)*dragStepMs
			}
			if argv[3+2*n] != "-" {
				ms += chordModMs
			}
		case "wheel":
			if notches := int(math.Abs(value(argv[1])) + math.Abs(value(argv[2]))); notches > 0 {
				// The last notch gets its gap too, before the sender can end: see wheel in hyprmasq-input.c.
				ms += notches * wheelGapMs
			}
		case "scroll":
			// So does a smooth scroll.
			ms += wheelGapMs
		case "keys":
			// Plain repeats use distinct press/release pairs; a single key keeps
			// typing pace, and a chord goes down after its modifiers.
			repeat := int(value(argv[2]))
			if strings.Contains(argv[1], "+") {
				ms += repeat*(chordModMs+chordHoldMs) + (repeat-1)*chordGapMs
			} else {
				if repeat > 1 {
					ms += repeat*repeatHoldMs + (repeat-1)*repeatGapMs
				} else {
					ms += typeHoldMs
				}
			}
		case "text":
			// Every character is at least one key press, and a CR LF is one Enter.
			if keys := utf8.RuneCountInString(argv[1]) - strings.Count(argv[1], "\r\n"); keys > 0 {
				ms += keys*typeHoldMs + (keys-1)*typeGapMs
			}
		case "sleep":
			ms += int(value(argv[1]))
		}
	}
	return time.Duration(ms) * time.Millisecond
}

// sendTime is the time that the checks allow a batch: its paced minimum and 10% for the round trips, which added about
// 3% to long text in the VM, 15.5 ms a character. typedWithin and deadlineFor plan with 25% and a short wait, so what
// they suggest passes the checks.
func sendTime(steps []inputStep) time.Duration {
	return withRoundTrips(minSendTime(steps))
}

func withRoundTrips(paced time.Duration) time.Duration {
	return paced * 11 / 10
}

// paceBudget is the paced time that withRoundTrips fits within left. The server estimates a batch from its arguments,
// but only hyprmasq-input knows the keys it resolves to, such as the two or more keys of a character that it types
// through Compose. So the sender gets this as its --budget and refuses a batch that would not end in time.
func paceBudget(left time.Duration) time.Duration {
	return left * 10 / 11
}

// typedWithin is about how many characters one call types within the deadline, with time for the round trips and a
// short wait.
func typedWithin(deadline time.Duration) int {
	chars := int((inputTime(deadline) - time.Second) / ((typeHoldMs + typeGapMs) * time.Millisecond * 5 / 4))
	return chars / 100 * 100
}

// inputTime is the part of a deadline that input may take. It ends senderStop before the capture's reserve, so that a
// sender the deadline stops has reported before the capture.
func inputTime(deadline time.Duration) time.Duration {
	return deadline - captureReserve - senderStop
}

// deadlineFor is the deadline_ms to suggest for a batch that takes at least minimum to send: with time for the round
// trips and a short wait, as typedWithin counts, in whole seconds.
func deadlineFor(minimum time.Duration) time.Duration {
	return (minimum*5/4 + time.Second + captureReserve + senderStop + time.Second - 1).Truncate(time.Second)
}

// targetLocale is what the sender needs to know of the target's locale: its name, or why it is not known.
type targetLocale struct {
	name    string
	unknown string
}

// windowLocale finds the locale whose Compose table the window's application applies to the dead keys it receives: the
// first of LC_ALL, LC_CTYPE and LANG that is set in its process's environment, or C, as Qt and foot pick it. Hyprland
// names the process of a window by its Wayland connection, and of an X11 window of its own XWayland by XRes. The
// windows of xwayland-satellite come from the satellite's own connection, so their applications are not known.
func (s *Server) windowLocale(windows []hypr.Window, address, stableID string) targetLocale {
	w := findWindow(windows, address)
	switch {
	case w == nil || w.StableID != stableID:
		return targetLocale{unknown: "the window was not in the window list"}
	case w.PID <= 0:
		return targetLocale{unknown: "Hyprland names no process for the window"}
	}
	// The plugin tells xwayland-satellite by its executable in the same way. Nix wraps it as .xwayland-satellite-wrapped.
	if exe, err := s.cfg.Exe(w.PID); err == nil && strings.Contains(filepath.Base(exe), "xwayland-satellite") {
		return targetLocale{unknown: "the window belongs to xwayland-satellite, which does not tell the process of its X11 applications"}
	}
	environ, err := s.cfg.Environ(w.PID)
	if err != nil {
		return targetLocale{unknown: fmt.Sprintf("the environment of its process %d cannot be read", w.PID)}
	}
	for _, name := range []string{"LC_ALL", "LC_CTYPE", "LANG"} {
		if value := environValue(environ, name); value != "" {
			return targetLocale{name: value}
		}
	}
	return targetLocale{name: "C"}
}

// composeNote says when characters typed through Compose may arrive as others, since the sender could not use the
// target's Compose table: the window's locale is not known, or there is no table for it.
func composeNote(reply senderReply, locale targetLocale) string {
	const check = " The application may compose them differently, so look at the shot to check that they arrived as typed."
	switch c := reply.Compose; {
	case c == nil:
		return ""
	case locale.unknown != "":
		return fmt.Sprintf("Characters without a key of their own were typed through the Compose table of %s, since the window's locale is not known: %s.", c.Locale, locale.unknown) + check
	case c.Locale != c.Requested:
		return fmt.Sprintf("Characters without a key of their own were typed through the Compose table of %s, since there is none for the window's locale %s.", c.Locale, c.Requested) + check
	}
	return ""
}

// runSender runs hyprmasq-input with the steps and returns its JSON reply. The sender types characters without a key of
// their own through the Compose table of locale, or of its own locale when that is "". It sends nothing when the steps
// take longer than budget at their pace. At a sync step, once the plugin has processed the steps before it, answer gets
// the step's index, and the sender goes on when answer returns. When ctx ends, the sender gets SIGTERM, releases
// everything it pressed and reports how far it got. An error means that there is no reply to read.
func (s *Server) runSender(ctx context.Context, socket string, steps []inputStep, budget time.Duration, locale string, answer func(step int)) (senderReply, error) {
	args := []string{"--socket", socket, "--json", "--budget", strconv.FormatInt(max(0, budget.Milliseconds()), 10)}
	if locale != "" {
		args = append(args, "--locale", locale)
	}
	for _, step := range steps {
		args = append(args, step.Argv...)
	}
	if err := ctx.Err(); err != nil {
		return senderReply{}, &startError{err: err}
	}
	cmd := exec.CommandContext(ctx, s.cfg.Input, args...)
	cmd.Cancel = func() error { return cmd.Process.Signal(syscall.SIGTERM) }
	cmd.WaitDelay = senderKillDelay
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return senderReply{}, &startError{err: err}
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return senderReply{}, &startError{err: err}
	}
	if err := cmd.Start(); err != nil {
		return senderReply{}, &startError{err: err}
	}
	// The reply is the last line. A sync line before it waits for its answer, which a sender that has exited meanwhile
	// does not read.
	var last []byte
	printed := false
	lines := bufio.NewScanner(stdout)
	lines.Buffer(nil, 1<<20)
	for lines.Scan() {
		printed = true
		var sync struct {
			Sync *int `json:"sync"`
		}
		if json.Unmarshal(lines.Bytes(), &sync) == nil && sync.Sync != nil {
			if answer != nil {
				answer(*sync.Sync)
			}
			fmt.Fprintf(stdin, "%d\n", *sync.Sync)
			continue
		}
		last = append(last[:0], lines.Bytes()...)
	}
	io.Copy(io.Discard, stdout)
	stdin.Close()
	runErr := cmd.Wait()

	var reply senderReply
	if err := json.Unmarshal(bytes.TrimSpace(last), &reply); err != nil || reply.Delivery == "" {
		// hyprmasq-input handles SIGTERM from before it connects, so one that killed it came before it could send.
		if status, ok := cmd.ProcessState.Sys().(syscall.WaitStatus); ok && status.Signaled() && status.Signal() == syscall.SIGTERM && !printed {
			return senderReply{}, &startError{err: errors.New("hyprmasq-input was stopped before it connected"), stopped: true}
		}
		message := strings.TrimSpace(stderr.String())
		if message == "" && runErr != nil {
			message = runErr.Error()
		}
		return senderReply{}, fmt.Errorf("hyprmasq-input gave no result: %s", message)
	}
	if reply.Error == nil && !reply.OK {
		reply.Error = &struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		}{Code: "internal", Message: strings.TrimSpace(stderr.String())}
	}
	return reply, nil
}

// startError means that the sender did not get to connect, so nothing was sent.
type startError struct {
	err error
	// It started, and a signal stopped it before it connected.
	stopped bool
}

func (e *startError) Error() string {
	if e.stopped {
		return e.err.Error()
	}
	return fmt.Sprintf("cannot start hyprmasq-input: %v", e.err)
}
