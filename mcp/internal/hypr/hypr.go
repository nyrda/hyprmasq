// Package hypr talks to Hyprland's request socket and to the hyprmasq plugin behind it.
package hypr

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"strconv"
	"strings"
	"time"
)

// Interface is the plugin interface version this package is written against.
const Interface = 3

// Client sends one request per connection, as hyprctl does. Hyprland keeps the connection open until a deferred
// plugin reply is ready, so a request can take as long as its context allows.
type Client struct {
	Socket string
}

// Error is a JSON error reply of the plugin.
type Error struct {
	Code    string
	Message string
}

func (e *Error) Error() string {
	return e.Message
}

// ErrPluginMissing means that Hyprland does not know the hyprmasq command.
var ErrPluginMissing = errors.New("the hyprmasq plugin is not loaded in Hyprland")

// ConnectError means that Hyprland's socket could not be reached.
type ConnectError struct {
	Socket string
	Err    error
}

func (e *ConnectError) Error() string {
	return fmt.Sprintf("cannot connect to Hyprland at %s: %v", e.Socket, e.Err)
}

func (e *ConnectError) Unwrap() error {
	return e.Err
}

// Request sends one request, such as "j/clients", and returns the whole reply.
func (c *Client) Request(ctx context.Context, request string) ([]byte, error) {
	if len(request) > MaxRequest {
		return nil, fmt.Errorf("the request to Hyprland is %d bytes long, more than %d", len(request), MaxRequest)
	}
	var dialer net.Dialer
	conn, err := dialer.DialContext(ctx, "unix", c.Socket)
	if err != nil {
		// A context that ended fails the dial too, which says nothing about the socket.
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, &ConnectError{Socket: c.Socket, Err: err}
	}
	defer conn.Close()
	if deadline, ok := ctx.Deadline(); ok {
		conn.SetDeadline(deadline)
	}
	stop := context.AfterFunc(ctx, func() { conn.SetDeadline(time.Unix(1, 0)) })
	defer stop()

	// Hyprland reads the request in chunks and stops at the first short read, so it has to arrive in one write.
	if _, err := conn.Write(frame(request)); err != nil {
		return nil, contextError(ctx, err)
	}
	reply, err := io.ReadAll(conn)
	if err != nil {
		return nil, contextError(ctx, err)
	}
	return reply, nil
}

// readChunk is how much Hyprland reads of a request at a time.
const readChunk = 1023

// MaxRequest is the longest request this client sends. Longer ones could arrive in several pieces.
const MaxRequest = 32768

// frame returns the bytes to send for request. A request that fills its last chunk exactly would leave Hyprland's main
// thread blocked in a read for a further chunk, so it gets a trailing space, which every command ignores.
func frame(request string) []byte {
	if len(request)%readChunk == 0 {
		request += " "
	}
	return []byte(request)
}

func contextError(ctx context.Context, err error) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return err
}

// Plugin sends "j/hyprmasq ARGS" and decodes a successful reply into out.
func (c *Client) Plugin(ctx context.Context, args string, out any) error {
	reply, err := c.Request(ctx, "j/hyprmasq "+args)
	if err != nil {
		return err
	}
	return decodePluginReply(reply, firstWord(args), out)
}

func firstWord(s string) string {
	word, _, _ := strings.Cut(s, " ")
	return word
}

type Version struct {
	Version   string `json:"version"`
	Interface int    `json:"interface"`
}

func (c *Client) Version(ctx context.Context) (Version, error) {
	var v Version
	err := c.Plugin(ctx, "version", &v)
	return v, err
}

type Size struct {
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

type Rect struct {
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
}

type Lease struct {
	Held        bool   `json:"held"`
	RemainingMs int    `json:"remainingMs"`
	Owner       string `json:"owner"`
}

type Capabilities struct {
	Lease    bool   `json:"lease"`
	Hold     bool   `json:"hold"`
	DnD      bool   `json:"dnd"`
	Keyboard string `json:"keyboard"`
}

type State struct {
	Address  string `json:"address"`
	StableID string `json:"stableId"`
	XWayland bool   `json:"xwayland"`
	Size     Size   `json:"size"`
	// The window has not caught up with its size: it shows a frame of another size.
	Resizing bool `json:"resizing"`
	// The window is hidden, waits for a frame callback and has got none from hyprmasq, so it may not have drawn what it
	// would draw on screen. A wait sends it frame callbacks.
	Starved bool    `json:"starved"`
	Scale   float64 `json:"scale"`
	Socket  *string `json:"socket"`
	// Names the open socket, whose counter starts again at 0, or nil without one or from a plugin that names none.
	SocketID     *string      `json:"socketId"`
	Commits      *int64       `json:"commits"`
	Lease        *Lease       `json:"lease"`
	Capabilities Capabilities `json:"capabilities"`
	// The window a new window came from, as the plugin found it when the window mapped, or nil when it found none.
	Source *Source `json:"source"`
}

// Source is the window whose input a new window came from: its parent, or the window of its app that got emulated
// input last.
type Source struct {
	Window string `json:"window"`
	// The stable_id of that window while it lives, which tells it from a later window at its address, or "" once it is
	// gone, and from a plugin that names none.
	StableID string `json:"stableId"`
	// The plugin opened the new window on that window's workspace, without the focus.
	Placed bool `json:"placed"`
	// Why it did not: disabled, user_input or workspace.
	Reason string `json:"reason"`
}

// LaunchRequest is the plugin request that starts the shell command line on the workspace under the token. The
// workspace and the line are written in hex, since hyprctl and Hyprland look for words such as --batch, /instances and
// rollinglog anywhere in a request.
func LaunchRequest(workspace, token, line string) string {
	return fmt.Sprintf("launch %s %s %s", hex.EncodeToString([]byte(workspace)), token, hex.EncodeToString([]byte(line)))
}

// Launch has the plugin start the shell command line through Hyprland on the workspace, under the token that
// Launched names its windows by, and returns the process's pid.
func (c *Client) Launch(ctx context.Context, workspace, token, line string) (int, error) {
	var r struct {
		PID int `json:"pid"`
	}
	err := c.Plugin(ctx, LaunchRequest(workspace, token, line), &r)
	return r.PID, err
}

func (c *Client) State(ctx context.Context, addr string) (State, error) {
	var s State
	err := c.Plugin(ctx, "state "+addr, &s)
	return s, err
}

// LaunchedWindow is a mapped window of a process that a launch started, or of one of its children.
type LaunchedWindow struct {
	Address string `json:"address"`
	// Tells the window from a later one that Hyprland gives the address to.
	StableID string `json:"stableId"`
	Token    string `json:"token"`
}

// Launched lists the mapped windows of launches, which the plugin found by the launched process among the parents of
// their process.
func (c *Client) Launched(ctx context.Context) ([]LaunchedWindow, error) {
	var r struct {
		Windows []LaunchedWindow `json:"windows"`
	}
	err := c.Plugin(ctx, "launched", &r)
	return r.Windows, err
}

// EisOpen opens the window's input socket, or returns the one that is open.
func (c *Client) EisOpen(ctx context.Context, addr string) (string, error) {
	var r struct {
		Path string `json:"path"`
	}
	err := c.Plugin(ctx, "eis open "+addr, &r)
	return r.Path, err
}

// IntRect is a region in whole window-local logical pixels.
type IntRect struct {
	X      int `json:"x"`
	Y      int `json:"y"`
	Width  int `json:"width"`
	Height int `json:"height"`
}

type Screenshot struct {
	Path    string  `json:"path"`
	Width   int     `json:"width"`
	Height  int     `json:"height"`
	Scale   float64 `json:"scale"`
	Region  Rect    `json:"region"`
	Commits *int64  `json:"commits"`
	// The socket whose counter Commits is.
	SocketID *string `json:"socketId"`
	// The window had not caught up with its size when it was rendered, so the image shows a frame of another size.
	Resizing bool `json:"resizing"`
	// The window was starved when it was rendered (see State), so the image may show an older frame.
	Starved bool `json:"starved"`
}

// Screenshot captures the window with its popups, or a region of that box, fitted within max pixels if max > 0.
func (c *Client) Screenshot(ctx context.Context, addr, path string, region *IntRect, max int) (Screenshot, error) {
	args := []string{"screenshot", addr, path}
	if region != nil {
		args = append(args, strconv.Itoa(region.X), strconv.Itoa(region.Y), strconv.Itoa(region.Width), strconv.Itoa(region.Height))
	}
	if max > 0 {
		args = append(args, "max", strconv.Itoa(max))
	}
	args = append(args, "popups")
	var s Screenshot
	err := c.Plugin(ctx, strings.Join(args, " "), &s)
	return s, err
}

type WaitArgs struct {
	Since int64
	// ExpectMs runs the change phase first when it is above 0.
	ExpectMs   int
	QuietMs    int
	DeadlineMs int
	ID         string
}

type WaitReply struct {
	Ended          string `json:"ended"`
	Commits        int64  `json:"commits"`
	RedrawObserved bool   `json:"redrawObserved"`
	CommitsSince   int64  `json:"commitsSince"`
	QuietMs        int    `json:"quietMs"`
	ElapsedMs      int    `json:"elapsedMs"`
	// The socket whose counter the wait read.
	SocketID *string `json:"socketId"`
	// The window had not caught up with its size when the wait ended.
	Resizing bool `json:"resizing"`
	// The boxes of animations drawn during the wait, which the quiet phase did not wait for, in window-local logical
	// pixels. A plugin that tells no animation apart sends none.
	Animated []IntRect `json:"animated"`
}

func (c *Client) Wait(ctx context.Context, addr string, a WaitArgs) (WaitReply, error) {
	args := fmt.Sprintf("wait %s since %d", addr, a.Since)
	if a.ExpectMs > 0 {
		args += fmt.Sprintf(" expect %d", a.ExpectMs)
	}
	args += fmt.Sprintf(" quiet %d deadline %d", a.QuietMs, a.DeadlineMs)
	if a.ID != "" {
		args += " id " + a.ID
	}
	var r WaitReply
	err := c.Plugin(ctx, args, &r)
	return r, err
}

// Cancel ends the wait with that id, and reports whether there was one.
func (c *Client) Cancel(ctx context.Context, id string) (bool, error) {
	var r struct {
		Cancelled bool `json:"cancelled"`
	}
	err := c.Plugin(ctx, "cancel "+id, &r)
	return r.Cancelled, err
}

// Lease starts or renews the focus lease. The lease keeps the emulated focus that input gives the window, and creates
// none, so it is taken before the input.
func (c *Client) Lease(ctx context.Context, addr string, ms int, owner string) (Lease, error) {
	// The reply has the members of state's lease object next to ok.
	var l Lease
	err := c.Plugin(ctx, fmt.Sprintf("lease %s %d %s", addr, ms, owner), &l)
	return l, err
}

func decodePluginReply(reply []byte, command string, out any) error {
	reply = bytes.TrimSpace(reply)
	if bytes.HasPrefix(reply, []byte("unknown request")) {
		return ErrPluginMissing
	}
	var status struct {
		OK    *bool  `json:"ok"`
		Error string `json:"error"`
		Code  string `json:"code"`
	}
	if err := json.Unmarshal(reply, &status); err != nil || status.OK == nil {
		return fmt.Errorf("unexpected reply to hyprmasq %s: %.200q", command, reply)
	}
	if !*status.OK {
		return &Error{Code: status.Code, Message: status.Error}
	}
	if out == nil {
		return nil
	}
	if err := json.Unmarshal(reply, out); err != nil {
		return fmt.Errorf("unexpected reply to hyprmasq %s: %v", command, err)
	}
	return nil
}

// LeaseRelease ends the lease if owner holds it, or whoever holds it if owner is "", and reports whether one ended.
func (c *Client) LeaseRelease(ctx context.Context, addr, owner string) (bool, error) {
	var r struct {
		Released bool `json:"released"`
	}
	args := fmt.Sprintf("lease %s release", addr)
	if owner != "" {
		args += " " + owner
	}
	err := c.Plugin(ctx, args, &r)
	return r.Released, err
}

type Workspace struct {
	ID   int    `json:"id"`
	Name string `json:"name"`
}

// Window is an entry of j/clients.
type Window struct {
	Address   string    `json:"address"`
	Mapped    bool      `json:"mapped"`
	Floating  bool      `json:"floating"`
	Size      []int     `json:"size"`
	Workspace Workspace `json:"workspace"`
	Class     string    `json:"class"`
	Title     string    `json:"title"`
	PID       int       `json:"pid"`
	XWayland  bool      `json:"xwayland"`
	StableID  string    `json:"stableId"`
}

func (c *Client) Clients(ctx context.Context) ([]Window, error) {
	reply, err := c.Request(ctx, "j/clients")
	if err != nil {
		return nil, err
	}
	var windows []Window
	if err := json.Unmarshal(reply, &windows); err != nil {
		return nil, fmt.Errorf("unexpected reply to clients: %v", err)
	}
	return windows, nil
}

type Monitor struct {
	ActiveWorkspace  Workspace `json:"activeWorkspace"`
	SpecialWorkspace Workspace `json:"specialWorkspace"`
}

// ActiveWindow returns the address of the window with the keyboard focus, or "" when no window has it.
func (c *Client) ActiveWindow(ctx context.Context) (string, error) {
	reply, err := c.Request(ctx, "j/activewindow")
	if err != nil {
		return "", err
	}
	var w struct {
		Address string `json:"address"`
	}
	if err := json.Unmarshal(reply, &w); err != nil {
		return "", fmt.Errorf("unexpected reply to activewindow: %v", err)
	}
	return w.Address, nil
}

func (c *Client) Monitors(ctx context.Context) ([]Monitor, error) {
	reply, err := c.Request(ctx, "j/monitors")
	if err != nil {
		return nil, err
	}
	var monitors []Monitor
	if err := json.Unmarshal(reply, &monitors); err != nil {
		return nil, fmt.Errorf("unexpected reply to monitors: %v", err)
	}
	return monitors, nil
}

// Dispatch runs a Lua dispatcher expression such as hl.dsp.window.close({ window = "stableid:1a" }).
func (c *Client) Dispatch(ctx context.Context, expression string) error {
	reply, err := c.Request(ctx, "/dispatch "+expression)
	if err != nil {
		return err
	}
	if text := strings.TrimSpace(string(reply)); !strings.HasPrefix(text, "ok") {
		return fmt.Errorf("dispatch failed: %s", text)
	}
	return nil
}

// LuaString quotes s as a Lua string literal with every byte written as a decimal escape, so no byte of it can end
// the string or the expression.
func LuaString(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for i := 0; i < len(s); i++ {
		fmt.Fprintf(&b, "\\%03d", s[i])
	}
	b.WriteByte('"')
	return b.String()
}
