// Package server implements the hyprmasq MCP tools.
package server

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

const Version = "0.3.0"

// DefaultMaxImageSize is the default of --max-image-size. In practice runs, agents clicked as accurately in shots of 1280
// pixels as in larger ones, which Claude Code and Codex shrank to about 2000 pixels anyway, and Claude cost about 40%
// less. Zoom shots still show small areas in full detail.
const DefaultMaxImageSize = 1280

// The values of Config.StructuredContent and --structured-content.
const (
	StructuredAuto   = "auto"
	StructuredAlways = "always"
	StructuredNever  = "never"
)

// textClients pass the model a result's structured content in place of its text, which says the same in fewer tokens.
// With StructuredAuto they get no structured content, so they pass the text and the image, as other clients do.
var textClients = map[string]bool{"claude-code": true}

type Config struct {
	// Hyprland's request socket, $XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock.
	HyprlandSocket string
	// $XDG_RUNTIME_DIR/hyprmasq/<instance>, the directory of the plugin's sockets, shared with every server.
	StateDir string
	// The hyprmasq-input binary.
	Input string
	// Fit overview and zoom shots within this many pixels on the long side, or 0 for the monitor's scale.
	MaxImageSize int
	LeaseMs      int
	// Whether results carry structured content: StructuredAlways, StructuredNever, or StructuredAuto or "", which
	// leaves it out for the clients in textClients.
	StructuredContent string
	// Reads /proc/<pid>/environ. Nil reads the real file.
	Environ func(pid int) ([]byte, error)
	// Reads the link /proc/<pid>/exe. Nil reads the real link.
	Exe func(pid int) (string, error)
}

type Server struct {
	cfg     Config
	hypr    *hypr.Client
	shots   *shotStore
	shotDir string
	owner   string
	waitSeq atomic.Int64

	mu sync.Mutex
	// Windows whose lease this server started or renewed.
	leased map[string]bool
	// HYPRMASQ_LAUNCH tokens of this server's launches.
	tokens map[string]bool
	// Set once the session ends: calls still running are cancelled, and no new lease is taken.
	closing bool
	calls   sync.WaitGroup
	// Cancels the calls that are running when the session ends.
	stopCalls context.Context
	stop      context.CancelFunc
}

func New(cfg Config) *Server {
	if cfg.Environ == nil {
		cfg.Environ = func(pid int) ([]byte, error) { return os.ReadFile(fmt.Sprintf("/proc/%d/environ", pid)) }
	}
	if cfg.Exe == nil {
		cfg.Exe = func(pid int) (string, error) { return os.Readlink(fmt.Sprintf("/proc/%d/exe", pid)) }
	}
	if cfg.LeaseMs <= 0 {
		cfg.LeaseMs = 60000
	}
	stopCalls, stop := context.WithCancel(context.Background())
	return &Server{
		cfg:       cfg,
		hypr:      &hypr.Client{Socket: cfg.HyprlandSocket},
		shots:     newShotStore(),
		shotDir:   filepath.Join(cfg.StateDir, "mcp", fmt.Sprint(os.Getpid())),
		owner:     fmt.Sprintf("mcp-%d-%s", os.Getpid(), randomHex(4)),
		leased:    map[string]bool{},
		tokens:    map[string]bool{},
		stopCalls: stopCalls,
		stop:      stop,
	}
}

func randomHex(n int) string {
	b := make([]byte, n)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// Owner is the lease owner of this server session.
func (s *Server) Owner() string {
	return s.owner
}

// How long Shutdown tries to release the leases, after waiting for the calls.
const shutdownRelease = 2 * time.Second

// Shutdown ends the session: it cancels the calls that still run and waits for them until ctx ends, then releases
// the leases this server may hold and deletes its shots. Sockets and launched windows stay.
func (s *Server) Shutdown(ctx context.Context) {
	s.CancelCalls()
	finished := make(chan struct{})
	go func() {
		s.calls.Wait()
		close(finished)
	}()
	select {
	case <-finished:
	case <-ctx.Done():
	}

	s.mu.Lock()
	windows := make([]string, 0, len(s.leased))
	for w := range s.leased {
		windows = append(windows, w)
	}
	s.leased = map[string]bool{}
	s.mu.Unlock()
	// The releases get their own time, also when the calls took all of ctx.
	release, cancel := context.WithTimeout(context.Background(), shutdownRelease)
	defer cancel()
	for _, w := range windows {
		s.hypr.LeaseRelease(release, w, s.owner)
	}
	os.RemoveAll(s.shotDir)
}

// CancelCalls cancels the calls that are running and refuses new ones, for a server that is asked to exit.
func (s *Server) CancelCalls() {
	s.mu.Lock()
	s.closing = true
	s.mu.Unlock()
	s.stop()
}

func (s *Server) stopping() bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.closing
}

// RemoveStaleShots deletes the shot directories of servers that are no longer running, such as ones that were killed.
func (s *Server) RemoveStaleShots() {
	dir := filepath.Dir(s.shotDir)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 0 || pid == os.Getpid() {
			continue
		}
		// Signal 0 checks that the process exists. EPERM means it exists under another user.
		if err := syscall.Kill(pid, 0); errors.Is(err, syscall.ESRCH) {
			os.RemoveAll(filepath.Join(dir, entry.Name()))
		}
	}
}

func (s *Server) setLeased(window string, held bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if held {
		s.leased[window] = true
	} else {
		delete(s.leased, window)
	}
}

// The instructions teach the loop and how to read results. Codex adds them to every tool's description, and pi with
// direct exposure shows none of them, so the rules for acting are in the tool descriptions, once.
const instructions = `hyprmasq: see, click and type in Hyprland windows without disturbing the user's screen; one act call acts, waits for the redraw and returns a screenshot.

Loop: windows or launch → screenshot → act with coordinates in that shot's pixels → read the shot that act returns → act again.
Read each result's summary, and its hint when it has one, before the next call.`

var actDescription = fmt.Sprintf(`Act on a window, wait until it has redrawn, and return a screenshot. Drive only windows the user named or you launched, and ask the user before anything irreversible: sending, deleting, buying, submitting.
Coordinates are pixels of the shot given in "shot" (required for click, move, drag, scroll).
Actions: click {x,y,button,count,modifiers} · move {x,y} · drag {from,to,button,modifiers} · scroll {x,y,dx,dy,unit:notch|px} · type {text} · key {keys:["ctrl","l"], repeat} · pause {ms}.
Batch only targets visible in the shot: a menu, dialog or page that opens needs a new shot before you click in it, but keys you know, such as arrows and enter in a menu, may follow in the same batch. In a window without the lease capability (see windows), a menu opened in one call is gone by the next, so open it and pick the entry with the keyboard in one batch.
drag, clicks with modifiers, and key chords that hold a key other than a modifier while they press the next need the hold capability.
type presses the keys for the text as given, with the active keymap: a line break presses Enter and a tab presses Tab, so for a new line in a chat or form field use a key action such as shift+enter. It types at most one key every 15 ms, and a character without a key of its own takes two or more through Compose: about %d characters in a call, or %d with deadline_ms 30000; split longer text over several calls.
Delivered input reached the window's application, which does not mean it took effect: a slow or busy application, such as a game or a web app, can drop keys or take them as shortcuts, so check typed text in the shot.
Never repeat input whose delivery is partial or uncertain, or from a call that timed out, before looking at a fresh screenshot.
Example: {"window":"0x55d1c0a3e4b0","shot":"s4","actions":[{"action":"click","x":412,"y":88},{"action":"type","text":"hello"},{"action":"key","keys":["enter"]}]}`,
	typedWithin(defaultDeadline), typedWithin(maxDeadline))

const windowsDescription = `List the windows, or with match only those whose class or title contains it, ignoring case. Each has its address (pass it as window to the other tools), class, title, workspace, whether it is visible (on a shown workspace) or hidden, and its size in logical pixels. Where they apply, it is also resizing (not yet redrawn at its new size, so a screenshot shows an older frame), xwayland, launched (by this server) or leased, and lists its capabilities when it lacks one of lease, hold and keyboard yes.
capabilities: lease (focus can be held between act calls, so menus stay open), hold (buttons and keys can stay pressed across input frames, needed for drag, for clicks with modifiers, and for key chords that hold a key other than a modifier), keyboard ("yes", or "x11-focus" when X11 keyboard input depends on which window has the focus).
The size is not a coordinate system: take a screenshot, and act with the pixels of that shot.
Example: {"match":"firefox"}`

// screenshotDescription describes the screenshot tool of a server whose images fit within maxImage pixels, or 0.
func screenshotDescription(maxImage int) string {
	scale := "at the monitor's full scale"
	if maxImage > 0 {
		scale = fmt.Sprintf("at the monitor's full scale, shrunk only to fit within %d pixels on the long side", maxImage)
	}
	return fmt.Sprintf(`Take a screenshot of a window now and return it as a shot such as "s5"; it waits only for a window that has not yet redrawn at a new size, for up to %g s. Coordinates in later act calls are pixels of the returned image, with 0,0 at its top left; the window's open popups are included.
region zooms in: {shot, x, y, width, height} in pixels of an earlier shot of the same window. The area is captured again %s, and becomes a new shot whose own pixels act accepts. Use it to read small text or to hit small targets.
Example: {"window":"0x55d1c0a3e4b0","region":{"shot":"s4","x":380,"y":60,"width":200,"height":80}}`, catchUpMax.Seconds(), scale)
}

const launchDescription = `Start an application on a hidden workspace, without changing the user's visible workspace or focus. Waits up to timeout_s seconds (default 30) for its first window and for that window to settle, and returns the window's address with a shot; act takes coordinates in that shot's pixels. Close the windows you launch when you are done.
command is the program and its arguments, run without a shell. workspace is a named workspace (default "hyprmasq"). Applications that hand the request to an instance that is already running, such as a browser that is open, cannot be launched this way: pass an option for a separate instance.
Example: {"command":["foot","-T","scratch"]}`

const releaseDescription = `End the focus lease on a window, whichever session holds it. The window stays open and loses the emulated pointer and keyboard focus, so open menus may close. An act that sends input starts or renews a lease on windows that support it; an act without input only renews one that is held.
Example: {"window":"0x55d1c0a3e4b0"}`

const closeDescription = `Ask a window to close, as Hyprland's close dispatcher does. Close only windows you launched or the user asked you to close. Reports within 2 seconds whether it closed, and lists windows that appeared meanwhile, such as a confirmation dialog, which you can then screenshot and act on.
Example: {"window":"0x55d1c0a3e4b0"}`

var (
	windowProperty = json.RawMessage(`{"type":"string","description":"Window address from windows or launch, such as 0x55d1c0a3e4b0."}`)
	regionSchema   = json.RawMessage(`{"type":"object","description":"A rectangle in pixels of an earlier shot of the same window.","properties":{"shot":{"type":"string","description":"The shot the rectangle is in, such as s4."},"x":{"type":"number"},"y":{"type":"number"},"width":{"type":"number","exclusiveMinimum":0},"height":{"type":"number","exclusiveMinimum":0}},"required":["shot","x","y","width","height"],"additionalProperties":false}`)
)

func objectSchema(properties map[string]json.RawMessage, required ...string) json.RawMessage {
	schema := map[string]any{"type": "object", "properties": properties, "additionalProperties": false}
	if len(required) > 0 {
		schema["required"] = required
	}
	out, err := json.Marshal(schema)
	if err != nil {
		panic(err)
	}
	return out
}

var actionSchema = json.RawMessage(`{
	"type": "object",
	"properties": {
		"action": {"type": "string", "enum": ["click", "move", "drag", "scroll", "type", "key", "pause"]},
		"x": {"type": "number", "description": "Shot pixels. click, move, scroll."},
		"y": {"type": "number", "description": "Shot pixels. click, move, scroll."},
		"button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left", "description": "click, drag."},
		"count": {"type": "integer", "minimum": 1, "maximum": 3, "default": 1, "description": "click: 2 for a double click."},
		"modifiers": {"type": "array", "items": {"type": "string"}, "description": "Held around a click or drag: ctrl, alt, shift, super."},
		"from": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "drag: [x, y] in shot pixels."},
		"to": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2, "description": "drag: [x, y] in shot pixels."},
		"dx": {"type": "number", "description": "scroll: positive scrolls right."},
		"dy": {"type": "number", "description": "scroll: positive scrolls down."},
		"unit": {"type": "string", "enum": ["notch", "px"], "default": "notch", "description": "scroll: notch is a wheel click, px smooth scrolling in logical pixels."},
		"text": {"type": "string", "description": "type: the text, typed as given, with no tags or escapes. A line break presses Enter and a tab presses Tab."},
		"keys": {"type": "array", "items": {"type": "string"}, "description": "key: pressed together in order and released in reverse, such as [\"ctrl\", \"shift\", \"t\"] or [\"ctrl+l\"], which is split at +. Names: XKB keysyms, single characters and ctrl, alt, shift, super, enter, esc, backspace, delete, tab, space, up, down, left, right, home, end, pageup, pagedown, f1 to f24, plus."},
		"repeat": {"type": "integer", "minimum": 1, "maximum": 100, "default": 1, "description": "key: presses of the chord."},
		"ms": {"type": "integer", "minimum": 0, "maximum": 5000, "description": "pause: milliseconds."}
	},
	"required": ["action"]
}`)

var actSchema = objectSchema(map[string]json.RawMessage{
	"window":  windowProperty,
	"shot":    json.RawMessage(`{"type":"string","description":"The shot the coordinates were read from, such as s4. Required if any action has coordinates."}`),
	"actions": json.RawMessage(`{"type":"array","items":` + string(actionSchema) + `,"default":[],"description":"Run in order. An empty list waits for a change since the shot, then captures."}`),
	"wait": json.RawMessage(`{"type":"object","properties":{
		"expect_change":{"type":"boolean","default":true,"description":"Wait for a redraw first. False for input that may change nothing on screen."},
		"expect_ms":{"type":"integer","minimum":1,"maximum":29000,"default":1000,"description":"How long to wait for the first redraw, at most deadline_ms minus 1000."},
		"quiet_ms":{"type":"integer","minimum":1,"maximum":10000,"default":200,"description":"How long the window must stay quiet to count as settled. Quiet means nothing was redrawn for this long, not that loading finished: if the shot still shows loading, act again with no actions."}},"additionalProperties":false}`),
	"capture":     json.RawMessage(`{"description":"\"window\" for an overview shot, \"none\", or a region {shot,x,y,width,height} in pixels of an earlier shot.","anyOf":[{"type":"string","enum":["window","none"]},` + string(regionSchema) + `],"default":"window"}`),
	"deadline_ms": deadlineSchema,
}, "window")

var deadlineSchema = json.RawMessage(fmt.Sprintf(`{"type":"integer","minimum":1000,"maximum":30000,"default":15000,"description":"Covers the whole call, including waiting for other calls on the window; the last 1000 ms are reserved for the capture, and input has to end %d ms before the deadline."}`,
	(captureReserve + senderStop).Milliseconds()))

// Register adds the tools to srv.
func (s *Server) Register(srv *mcp.Server) {
	readOnly := &mcp.ToolAnnotations{ReadOnlyHint: true}
	notDestructive := false
	srv.AddTool(&mcp.Tool{Name: "windows", Description: windowsDescription, Annotations: readOnly,
		InputSchema: objectSchema(map[string]json.RawMessage{
			"match": json.RawMessage(`{"type":"string","description":"Only windows whose class or title contains this, ignoring case, such as \"firefox\"."}`),
		})}, s.handle(s.windowsTool))
	// screenshot opens the window's socket, which stays open, so the plugin treats the window as driven from then on.
	srv.AddTool(&mcp.Tool{Name: "screenshot", Description: screenshotDescription(s.cfg.MaxImageSize), Annotations: &mcp.ToolAnnotations{DestructiveHint: &notDestructive},
		InputSchema: objectSchema(map[string]json.RawMessage{"window": windowProperty, "region": regionSchema}, "window")}, s.handle(s.screenshotTool))
	srv.AddTool(&mcp.Tool{Name: "act", Description: actDescription, InputSchema: actSchema}, s.handle(s.actTool))
	srv.AddTool(&mcp.Tool{Name: "launch", Description: launchDescription, Annotations: &mcp.ToolAnnotations{DestructiveHint: &notDestructive},
		InputSchema: objectSchema(map[string]json.RawMessage{
			"command":   json.RawMessage(`{"type":"array","items":{"type":"string"},"minItems":1,"description":"Program and arguments, such as [\"foot\", \"-T\", \"scratch\"]."}`),
			"workspace": json.RawMessage(`{"type":"string","default":"hyprmasq","description":"Named workspace for the windows: letters, digits, _, . and -."}`),
			"timeout_s": json.RawMessage(`{"type":"integer","minimum":1,"maximum":300,"default":30,"description":"Seconds to wait for the first window."}`),
		}, "command")}, s.handle(s.launchTool))
	srv.AddTool(&mcp.Tool{Name: "release", Description: releaseDescription, Annotations: &mcp.ToolAnnotations{DestructiveHint: &notDestructive, IdempotentHint: true},
		InputSchema: objectSchema(map[string]json.RawMessage{"window": windowProperty}, "window")}, s.handle(s.releaseTool))
	srv.AddTool(&mcp.Tool{Name: "close", Description: closeDescription,
		InputSchema: objectSchema(map[string]json.RawMessage{"window": windowProperty}, "window")}, s.handle(s.closeTool))
}

// NewMCPServer returns an MCP server with the tools and instructions.
func (s *Server) NewMCPServer() *mcp.Server {
	srv := mcp.NewServer(&mcp.Implementation{Name: "hyprmasq-mcp", Version: Version}, &mcp.ServerOptions{
		Instructions: instructions,
		Capabilities: &mcp.ServerCapabilities{Tools: &mcp.ToolCapabilities{}},
	})
	s.Register(srv)
	return srv
}

// requestTimeout bounds the tools that only make a few requests, in case Hyprland does not answer.
const requestTimeout = 10 * time.Second

// call is one tool call: its arguments and a way to report progress.
type call struct {
	req  *mcp.CallToolRequest
	args json.RawMessage
}

func (s *Server) handle(fn func(context.Context, *call) *mcp.CallToolResult) mcp.ToolHandler {
	return func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		res := s.run(ctx, req, fn)
		if !s.sendsStructured(req) {
			res.StructuredContent = nil
		}
		return res, nil
	}
}

// run runs one call of a tool, unless the session is ending.
func (s *Server) run(ctx context.Context, req *mcp.CallToolRequest, fn func(context.Context, *call) *mcp.CallToolResult) *mcp.CallToolResult {
	s.mu.Lock()
	if s.closing {
		s.mu.Unlock()
		return simpleError(&toolError{Code: "cancelled", Message: "the server is shutting down"})
	}
	s.calls.Add(1)
	s.mu.Unlock()
	defer s.calls.Done()
	// The SDK cancels a call that the client cancelled with context.Canceled, and every call with the read error
	// when stdin ends, or with the write error when the client stops reading. Those end the session.
	defer context.AfterFunc(ctx, func() {
		if !errors.Is(context.Cause(ctx), context.Canceled) {
			s.CancelCalls()
		}
	})()
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	defer context.AfterFunc(s.stopCalls, cancel)()
	return fn(ctx, &call{req: req, args: req.Params.Arguments})
}

// sendsStructured tells whether the results for the client of req carry structured content. The tools declare no
// output schema, so the structured content is optional.
func (s *Server) sendsStructured(req *mcp.CallToolRequest) bool {
	switch s.cfg.StructuredContent {
	case StructuredAlways:
		return true
	case StructuredNever:
		return false
	}
	info := req.ClientInfo()
	return info == nil || !textClients[info.Name]
}

// decode reads the arguments strictly, so a misspelled field is named instead of ignored.
func (c *call) decode(out any) error {
	args := c.args
	if len(bytes.TrimSpace(args)) == 0 || string(bytes.TrimSpace(args)) == "null" {
		args = json.RawMessage("{}")
	}
	return decodeStrict(args, out, "")
}

// decodeStrict reads an object of the arguments strictly. Its errors name the field by its path from the arguments,
// which path starts, such as wait, and say in JSON's terms what it takes. See checkJSON for null.
func decodeStrict(data []byte, out any, path string) error {
	if err := checkJSON(data, reflect.TypeOf(out), path, false); err != nil {
		return err
	}
	err := strictUnmarshal(data, out)
	if err == nil {
		return nil
	}
	var typeErr *json.UnmarshalTypeError
	if errors.As(err, &typeErr) {
		field := strings.Trim(path+"."+typeErr.Field, ".")
		if field == "" {
			field = "the arguments"
		}
		return invalidf("%s must be %s, not %s", field, expectedJSON(typeErr.Type, field), givenJSON(typeErr.Value))
	}
	if name, ok := strings.CutPrefix(err.Error(), "json: unknown field "); ok {
		if path == "" {
			return invalidf("unknown argument %s; the arguments are %s", name, strings.Join(jsonFields(out), ", "))
		}
		return invalidf("%s.%s is not a field of %s, which takes %s", path, strings.Trim(name, `"`), path, strings.Join(jsonFields(out), ", "))
	}
	if path == "" {
		return invalidf("arguments: %v", err)
	}
	return invalidf("%s: %v", path, err)
}

// Examples of the arguments whose shape an agent most likely gets wrong, for example as a string with JSON in it.
var argumentShapes = map[string]string{
	"actions": `[{"action":"key","keys":["enter"]}]`,
	"wait":    `{"expect_ms":2000}`,
	"region":  `{"shot":"s4","x":0,"y":0,"width":100,"height":50}`,
	"command": `["foot","-T","scratch"]`,
}

// expectedJSON says which JSON value the field at path takes, with an example for the shapes that agents get wrong.
func expectedJSON(t reflect.Type, path string) string {
	if shape := argumentShapes[path]; shape != "" {
		return jsonType(t) + " such as " + shape
	}
	return jsonType(t)
}

var rawMessageType = reflect.TypeFor[json.RawMessage]()

// checkJSON checks that raw, the value at path, is the kind of JSON value that t takes, down to each element of an
// array, before encoding/json decodes it. The decoder leaves a value as it was for null, so a null coordinate would be
// read as 0, and a null element of an array as 0 or "". So null is refused for a number, an array element and a field
// tagged args:"required", while an optional field of another type given as null takes its default, as if it were left
// out. The decoder also names the array for an element of the wrong type, not the element. Values of type
// json.RawMessage are left to whoever decodes them, and whether a number is whole to the decoder.
func checkJSON(raw json.RawMessage, t reflect.Type, path string, required bool) error {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	raw = bytes.TrimSpace(raw)
	if t == rawMessageType || len(raw) == 0 {
		return nil
	}
	given := givenKind(raw)
	if given == "null" && !required && !isNumber(t) {
		return nil
	}
	if given == "null" || !takes(t, raw[0]) {
		name := path
		if name == "" {
			name = "the arguments"
		}
		return invalidf("%s must be %s, not %s", name, expectedJSON(t, path), given)
	}
	switch t.Kind() {
	case reflect.Slice:
		var elements []json.RawMessage
		if json.Unmarshal(raw, &elements) != nil {
			return nil
		}
		for i, element := range elements {
			if err := checkJSON(element, t.Elem(), fmt.Sprintf("%s[%d]", path, i), true); err != nil {
				return err
			}
		}
	case reflect.Struct:
		var members map[string]json.RawMessage
		if json.Unmarshal(raw, &members) != nil {
			return nil
		}
		// In the order of the fields, so that the first of several is named. encoding/json also takes a member whose
		// name differs from the field's only in case.
		for i := range t.NumField() {
			f := t.Field(i)
			name, _, _ := strings.Cut(f.Tag.Get("json"), ",")
			if name == "" || name == "-" {
				continue
			}
			for member, value := range members {
				if !strings.EqualFold(member, name) {
					continue
				}
				if err := checkJSON(value, f.Type, strings.TrimPrefix(path+"."+name, "."), f.Tag.Get("args") == "required"); err != nil {
					return err
				}
			}
		}
	}
	return nil
}

// takes says whether a JSON value that starts with first can be decoded into t.
func takes(t reflect.Type, first byte) bool {
	switch {
	case t.Kind() == reflect.Struct || t.Kind() == reflect.Map:
		return first == '{'
	case t.Kind() == reflect.Slice || t.Kind() == reflect.Array:
		return first == '['
	case t.Kind() == reflect.String:
		return first == '"'
	case t.Kind() == reflect.Bool:
		return first == 't' || first == 'f'
	case isNumber(t):
		return first == '-' || first >= '0' && first <= '9'
	}
	return true
}

// givenKind words the kind of a JSON value, as jsonType does for a Go type.
func givenKind(raw json.RawMessage) string {
	switch raw[0] {
	case '{':
		return "an object"
	case '[':
		return "an array"
	case '"':
		return "a string"
	case 't', 'f':
		return "true or false"
	case 'n':
		return "null"
	}
	return "a number"
}

func isNumber(t reflect.Type) bool {
	switch t.Kind() {
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64,
		reflect.Float32, reflect.Float64:
		return true
	}
	return false
}

// jsonType says which JSON value a Go type takes.
func jsonType(t reflect.Type) string {
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	switch t.Kind() {
	case reflect.Struct, reflect.Map:
		return "an object"
	case reflect.Slice, reflect.Array:
		return "an array"
	case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64, reflect.Uint, reflect.Uint8, reflect.Uint16, reflect.Uint32, reflect.Uint64:
		return "an integer"
	case reflect.Float32, reflect.Float64:
		return "a number"
	case reflect.String:
		return "a string"
	case reflect.Bool:
		return "true or false"
	}
	return "another value"
}

// givenJSON words the Value of a json.UnmarshalTypeError, such as "string" or "number 1.5".
func givenJSON(value string) string {
	switch value {
	case "string", "number":
		return "a " + value
	case "array", "object":
		return "an " + value
	case "bool":
		return "true or false"
	}
	return strings.TrimPrefix(value, "number ")
}

// jsonFields lists the JSON names of a struct's fields.
func jsonFields(out any) []string {
	t := reflect.TypeOf(out)
	for t.Kind() == reflect.Pointer {
		t = t.Elem()
	}
	var names []string
	for i := range t.NumField() {
		if name, _, _ := strings.Cut(t.Field(i).Tag.Get("json"), ","); name != "" && name != "-" {
			names = append(names, name)
		}
	}
	return names
}

func strictUnmarshal(data []byte, out any) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	return dec.Decode(out)
}

// progress sends a progress notification about once a second while a call runs, if the client asked for them.
type progress struct {
	mu    sync.Mutex
	phase string
	stop  func()
}

func (c *call) startProgress(ctx context.Context, total time.Duration) *progress {
	p := &progress{stop: func() {}}
	if c.req == nil || c.req.Session == nil || c.req.Params.GetProgressToken() == nil {
		return p
	}
	token := c.req.Params.GetProgressToken()
	ctx, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	p.stop = func() {
		cancel()
		<-done
	}
	start := time.Now()
	go func() {
		defer close(done)
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				p.mu.Lock()
				phase := p.phase
				p.mu.Unlock()
				c.req.Session.NotifyProgress(ctx, &mcp.ProgressNotificationParams{
					ProgressToken: token,
					Progress:      float64(time.Since(start).Milliseconds()),
					Total:         float64(total.Milliseconds()),
					Message:       phase,
				})
			}
		}
	}()
	return p
}

func (p *progress) set(phase string) {
	p.mu.Lock()
	p.phase = phase
	p.mu.Unlock()
}

var (
	addressPattern   = regexp.MustCompile(`^0x[0-9a-fA-F]{1,16}$`)
	workspacePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)
)

// canonicalWindow checks a window argument and returns it as Hyprland writes addresses, in lower case without leading
// zeros, since Hyprland and the plugin compare addresses as strings.
func canonicalWindow(window string) (string, error) {
	if window == "" {
		return "", invalidf("window is missing; pass an address from windows, such as 0x55d1c0a3e4b0")
	}
	if !addressPattern.MatchString(window) {
		return "", invalidf("window must be an address such as 0x55d1c0a3e4b0, not %q", window)
	}
	address, err := strconv.ParseUint(window[2:], 16, 64)
	if err != nil {
		return "", invalidf("window must be an address such as 0x55d1c0a3e4b0, not %q", window)
	}
	return fmt.Sprintf("0x%x", address), nil
}

// checkPlugin makes sure that the plugin is loaded and speaks this server's interface.
func (s *Server) checkPlugin(ctx context.Context) error {
	v, err := s.hypr.Version(ctx)
	if err != nil {
		var pe *hypr.Error
		if errors.As(err, &pe) && (pe.Code == "" || pe.Code == "invalid_arguments") {
			return &toolError{Code: "plugin_version", Message: "the hyprmasq plugin has no version command, so it is older than this server", Hint: hintPluginUser}
		}
		return err
	}
	if v.Interface != hypr.Interface {
		return &toolError{Code: "plugin_version",
			Message: fmt.Sprintf("the hyprmasq plugin %s has interface %d, but this server needs interface %d", v.Version, v.Interface, hypr.Interface),
			Hint:    hintPluginUser}
	}
	return nil
}

// openState returns the window's state with its socket open, so its commits are counted. Given the stable_id of the
// window a call is about, it fails with window_gone once that window has closed and another one has its address,
// without opening a socket on the other one.
func (s *Server) openState(ctx context.Context, window, stableID string) (hypr.State, error) {
	st, err := s.hypr.State(ctx, window)
	if err != nil {
		return st, err
	}
	if stableID != "" && st.StableID != stableID {
		return st, replacedError(window)
	}
	if st.Socket == nil || st.Commits == nil {
		if _, err := s.hypr.EisOpen(ctx, window); err != nil {
			return st, err
		}
		if st, err = s.hypr.State(ctx, window); err != nil {
			return st, err
		}
		if stableID != "" && st.StableID != stableID {
			return st, replacedError(window)
		}
		if st.Commits == nil {
			return st, &toolError{Code: "internal", Message: "the plugin reports no commit counter after opening the socket"}
		}
	}
	return st, nil
}

// replacedError reports a window whose address now belongs to another window.
func replacedError(window string) *toolError {
	return &toolError{Code: "window_gone", Message: fmt.Sprintf("window %s closed, and another window has its address now", window), Hint: hintWindows}
}

var unsafePathChars = regexp.MustCompile(`[^A-Za-z0-9_-]`)

func (s *Server) lockPath(stableID string) string {
	return filepath.Join(s.cfg.StateDir, "window-"+unsafePathChars.ReplaceAllString(stableID, "_")+".lock")
}

func (s *Server) sendLockPath() string {
	return filepath.Join(s.cfg.StateDir, "send.lock")
}
