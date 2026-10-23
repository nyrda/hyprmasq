// Package hypr talks to Hyprland's request socket and to the hyprmasq plugin behind it.
package hypr

import (
	"bufio"
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"io"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// Interface is the plugin interface version this package is written against.
const Interface = 4

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
	// The context's end, and not a deadline of the connection, stops the request, so that ctx.Err() tells why: a
	// deadline of the connection can pass before the context's own timer has ended it.
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
	Lease bool `json:"lease"`
	Hold  bool `json:"hold"`
	// A drag-and-drop that the window's app starts runs with the user's pointer, as it does in the active window.
	DnD bool `json:"dnd"`
	// A drag-and-drop that emulated input starts in the window runs privately, within its app, and a plugin without
	// private drags reports none.
	PrivateDnD bool   `json:"privateDnd"`
	Keyboard   string `json:"keyboard"`
}

// X11Kind tells what Hyprland's close dispatcher does with a window of its XWayland: it asks one that takes
// WM_DELETE_WINDOW to close, and kills the X client of any other, which ends its app with every window of it.
type X11Kind struct {
	// The window manager does not manage the window: a menu, list, tooltip or the like.
	OverrideRedirect bool `json:"overrideRedirect"`
	// The window's WM_PROTOCOLS lists WM_DELETE_WINDOW, so the dispatcher asks it to close.
	DeleteWindow bool `json:"deleteWindow"`
}

type State struct {
	Address  string `json:"address"`
	StableID string `json:"stableId"`
	XWayland bool   `json:"xwayland"`
	// For a window of Hyprland's XWayland, nil for other windows and from a plugin that does not tell.
	X11  *X11Kind `json:"x11"`
	Size Size     `json:"size"`
	// The window has not caught up with its size: it shows a frame of another size.
	Resizing bool `json:"resizing"`
	// The window is hidden, waits for a frame callback and has got none from hyprmasq, so it may not have drawn what it
	// would draw on screen. A wait sends it frame callbacks.
	Starved bool    `json:"starved"`
	Scale   float64 `json:"scale"`
	Socket  *string `json:"socket"`
	// Names the open socket, whose counter starts again at 0, or nil without one or from a plugin that names none.
	SocketID *string `json:"socketId"`
	Commits  *int64  `json:"commits"`
	// Times the user's own input to the window's app took the keyboard focus back from the window while a shortcut or
	// function key had not settled, which may have kept it from taking effect. Nil without a socket, and from a plugin
	// that does not count them.
	Interrupted *int64 `json:"interrupted"`
	// Why the plugin last paused the socket's devices, which stops the sender with input_paused: x11_focus (the user is in
	// another window of the window's X server), x11_unresponsive (the X server did not answer in time), user_button or
	// user_key (the user held a button or key in a window of the same Wayland connection for longer than the plugin
	// held the input back). Nil without a socket, before the first pause, and from a plugin that does not tell.
	LastPause *string `json:"lastPause"`
	// The drag-and-drop operations that the window's app started while the socket was open. Nil without a socket, and
	// from a plugin that does not count them.
	Drag         *Drag        `json:"drag"`
	Lease        *Lease       `json:"lease"`
	Capabilities Capabilities `json:"capabilities"`
	// The window a new window came from, as the plugin found it when the window mapped, or nil when it found none.
	Source *Source `json:"source"`
}

// Drag counts the drag-and-drop operations of a window's app, and tells how the last one went.
type Drag struct {
	// The id of the last one: they count from 1, and 0 means none yet.
	ID int64 `json:"id"`
	// Of the last one: none, active (before the drop), dropped (waiting for the destination to finish), finished,
	// cancelled or unconfirmed (the destination did not finish in time after the drop).
	Phase string `json:"phase"`
	// The last one ran as a private drag, within the window's app.
	Private bool `json:"private"`
	// Why the last one was cancelled or unconfirmed, or "".
	Reason string `json:"reason"`
	// Of them, the private drags that started and that the destination finished, the cancelled ones, also those that
	// the plugin cancelled before they could start, and the unconfirmed ones.
	Started     int64 `json:"started"`
	Finished    int64 `json:"finished"`
	Cancelled   int64 `json:"cancelled"`
	Unconfirmed int64 `json:"unconfirmed"`
	// How the last ones ended, up to 32, the latest last, or nil from a plugin that does not tell.
	Ended []DragEnd `json:"ended"`
}

// DragEnd tells how the drag with the id ended: finished, cancelled or unconfirmed, and why.
type DragEnd struct {
	ID     int64  `json:"id"`
	Phase  string `json:"phase"`
	Reason string `json:"reason"`
}

// Pending tells whether the last one has not ended.
func (d *Drag) Pending() bool {
	return d.Phase == "active" || d.Phase == "dropped"
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

// An Image is a file that a screenshot wrote.
type Image struct {
	Path   string  `json:"path"`
	Width  int     `json:"width"`
	Height int     `json:"height"`
	Scale  float64 `json:"scale"`
	Region Rect    `json:"region"`
}

type Screenshot struct {
	Image
	Commits *int64 `json:"commits"`
	// The socket whose counter Commits is.
	SocketID *string `json:"socketId"`
	// The window had not caught up with its size when it was rendered, so the image shows a frame of another size.
	Resizing bool `json:"resizing"`
	// The window was starved when it was rendered (see State), so the image may show an older frame.
	Starved bool `json:"starved"`
	// The detail image that ScreenshotArgs.DetailPath asked for, or nil when the image has as many pixels as it would.
	Detail *Detail `json:"detail"`
}

// A Detail is an image of the same render as the screenshot's, with a pixel per logical pixel, or the monitor's pixels
// where they are fewer.
type Detail struct {
	Image
	// The max of a screenshot request that renders the area at the same scale, or 0 for the monitor's scale.
	Max int `json:"max"`
}

// ScreenshotArgs ask for a screenshot of the window with its popups.
type ScreenshotArgs struct {
	// The file for the image: a PNG, or with Raw its pixels (see ReadRaw).
	Path string
	// A region of the box of the window and its popups, or nil for all of it.
	Region *IntRect
	// Fits the image within Max pixels on its long side, if Max > 0.
	Max int
	Raw bool
	// Where to write the pixels of a detail image (see Detail), if set. It is only written when the image has fewer
	// pixels than one per logical pixel or than the monitor's, whichever are fewer.
	DetailPath string
}

// Screenshot renders the window once and writes the images that the arguments ask for.
func (c *Client) Screenshot(ctx context.Context, addr string, a ScreenshotArgs) (Screenshot, error) {
	args := []string{"screenshot", addr, a.Path}
	if a.Region != nil {
		args = append(args, strconv.Itoa(a.Region.X), strconv.Itoa(a.Region.Y), strconv.Itoa(a.Region.Width), strconv.Itoa(a.Region.Height))
	}
	if a.Max > 0 {
		args = append(args, "max", strconv.Itoa(a.Max))
	}
	args = append(args, "popups")
	if a.Raw {
		args = append(args, "raw")
	}
	if a.DetailPath != "" {
		args = append(args, "detail", a.DetailPath)
	}
	var s Screenshot
	err := c.Plugin(ctx, strings.Join(args, " "), &s)
	return s, err
}

// ReadRaw reads the pixels of an image that a screenshot wrote with Raw, or as a detail image: straight-alpha RGBA,
// top row first, without a header.
func ReadRaw(path string, width, height int) (*image.NRGBA, error) {
	if width <= 0 || height <= 0 {
		return nil, fmt.Errorf("%s: an image of %d×%d pixels", path, width, height)
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	img := image.NewNRGBA(image.Rect(0, 0, width, height))
	if _, err := io.ReadFull(f, img.Pix); err != nil {
		return nil, fmt.Errorf("%s: %w", path, err)
	}
	if n, _ := f.Read(make([]byte, 1)); n != 0 {
		return nil, fmt.Errorf("%s has more than the pixels of %d×%d", path, width, height)
	}
	return img, nil
}

type WaitArgs struct {
	Since int64
	// ExpectMs runs the change phase first when it is above 0.
	ExpectMs   int
	QuietMs    int
	DeadlineMs int
	ID         string
	// IgnoreAnimations makes the change phase wait for a commit that is not part of an animation.
	IgnoreAnimations bool
	// Drags is the id of the last drag-and-drop the caller knows of, or nil. Neither phase ends while a later one goes
	// on, and its end ends the change phase.
	Drags *int64
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
	if a.IgnoreAnimations {
		args += " ignore animations"
	}
	if a.Drags != nil {
		args += fmt.Sprintf(" drags %d", *a.Drags)
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

// Event is a line of Hyprland's event socket: its name, such as openwindow, and its data.
type Event struct {
	Name string
	Data string
}

// EventSocket is the path of Hyprland's event socket, which lies next to its request socket.
func (c *Client) EventSocket() string {
	return filepath.Join(filepath.Dir(c.Socket), ".socket2.sock")
}

// Events connects to Hyprland's event socket and sends each event that Hyprland writes to it on the channel, until ctx
// ends or the connection does, and then closes the channel. Hyprland writes an event as it happens, on its main thread,
// so a request sent after an event is answered after what the event announced. It drops a client that falls 64 events
// behind, which ends the connection.
func (c *Client) Events(ctx context.Context) (<-chan Event, error) {
	var dialer net.Dialer
	conn, err := dialer.DialContext(ctx, "unix", c.EventSocket())
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, &ConnectError{Socket: c.EventSocket(), Err: err}
	}
	events := make(chan Event)
	go func() {
		defer close(events)
		defer conn.Close()
		stop := context.AfterFunc(ctx, func() { conn.SetDeadline(time.Unix(1, 0)) })
		defer stop()
		lines := bufio.NewScanner(conn)
		for lines.Scan() {
			name, data, _ := strings.Cut(lines.Text(), ">>")
			select {
			case events <- Event{Name: name, Data: data}:
			case <-ctx.Done():
				return
			}
		}
	}()
	return events, nil
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
