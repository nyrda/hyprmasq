package server

import (
	"bufio"
	"context"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"io"
	"math"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// The test binary doubles as a fake hyprmasq-input, so the server runs a real process it can signal.
func TestMain(m *testing.M) {
	if os.Getenv("HYPRMASQ_FAKE_INPUT") != "" {
		os.Exit(fakeInput())
	}
	os.Exit(m.Run())
}

var inputArity = map[string]int{"move": 2, "click": 3, "drag": 6, "wheel": 2, "scroll": 2, "keys": 2, "text": 1, "sleep": 1, "sync": 2}

// fakeInput logs its arguments and prints the reply that FAKE_INPUT_MODE asks for:
//
//	ok                          every action confirmed
//	fail:CODE:INDEX:DELIVERY:K  the action at INDEX failed with CODE after K keys
//	hang[:DONE[:MS]]            wait for SIGTERM, then report the cancellation after DONE actions (default 1), MS
//	                            milliseconds later
//	sleep:MS                    wait MS milliseconds, then succeed
//	toolong:MS                  refuse the batch, which takes MS milliseconds at its pace, as longer than --budget
//	unhandled                   die of SIGTERM without a reply, as the real one does before it handles signals
//
// FAKE_INPUT_COMPOSE=LOCALE:REQUESTED adds that a character was typed through the Compose table of LOCALE when the
// table of REQUESTED was asked for.
//
// Before it replies, it prints {"sync":N} for each sync action that it gets to, the actions before INDEX or DONE and
// all of them otherwise, and reads the answer, a line on standard input, logging both.
func fakeInput() int {
	mode := os.Getenv("FAKE_INPUT_MODE")
	// Tests signal the sender once it has logged its start, so it handles SIGTERM before that.
	signals := make(chan os.Signal, 1)
	if mode != "unhandled" {
		signal.Notify(signals, syscall.SIGTERM)
	}
	args := os.Args[1:]
	var actions []string
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--socket", "--budget", "--locale":
			i++
		case "--json", "--no-pace":
		default:
			actions = append(actions, args[i])
			i += inputArity[args[i]]
		}
	}
	total := len(actions)
	logLine := func(event string) {
		f, err := os.OpenFile(os.Getenv("FAKE_INPUT_LOG"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
		if err != nil {
			return
		}
		defer f.Close()
		line, _ := json.Marshal(map[string]any{"event": event, "argv": args, "time": time.Now().UnixNano()})
		f.Write(append(line, '\n'))
	}
	logLine("start")
	defer logLine("exit")
	answers := bufio.NewReader(os.Stdin)
	syncsBefore := func(end int) {
		for i := 0; i < end && i < total; i++ {
			if actions[i] != "sync" {
				continue
			}
			fmt.Printf("{\"sync\":%d}\n", i)
			logLine(fmt.Sprintf("sync %d", i))
			answer, err := answers.ReadString('\n')
			if err != nil {
				return
			}
			logLine("answered " + strings.TrimSpace(answer))
		}
	}

	reply := func(v map[string]any) {
		if compose, requested, ok := strings.Cut(os.Getenv("FAKE_INPUT_COMPOSE"), ":"); ok {
			v["compose"] = map[string]any{"locale": compose, "requested": requested}
		}
		out, _ := json.Marshal(v)
		fmt.Println(string(out))
	}
	switch {
	case mode == "" || mode == "ok":
		syncsBefore(total)
	case mode == "hang" || strings.HasPrefix(mode, "hang:"):
		done, delay := 1, 0
		if parts := strings.Split(mode, ":"); len(parts) > 1 {
			done, _ = strconv.Atoi(parts[1])
			if len(parts) > 2 {
				delay, _ = strconv.Atoi(parts[2])
			}
		}
		syncsBefore(done)
		<-signals
		logLine("sigterm")
		time.Sleep(time.Duration(delay) * time.Millisecond)
		reply(map[string]any{"ok": false, "done": done, "total": total, "failedIndex": done, "delivery": "partial", "error": map[string]any{"code": "cancelled", "message": "terminated"}})
		return 1
	case mode == "unhandled":
		time.Sleep(time.Minute)
	case strings.HasPrefix(mode, "sleep:"):
		ms, _ := strconv.Atoi(strings.TrimPrefix(mode, "sleep:"))
		syncsBefore(total)
		time.Sleep(time.Duration(ms) * time.Millisecond)
	case strings.HasPrefix(mode, "toolong:"):
		ms, _ := strconv.Atoi(strings.TrimPrefix(mode, "toolong:"))
		reply(map[string]any{"ok": false, "done": 0, "total": total, "failedIndex": nil, "delivery": "none", "progress": nil, "paceMs": ms,
			"error": map[string]any{"code": "too_long", "message": "fake too_long"}})
		return 2
	case strings.HasPrefix(mode, "fail:"):
		parts := strings.Split(mode, ":")
		index, _ := strconv.Atoi(parts[2])
		keys, _ := strconv.Atoi(parts[4])
		// A sync that the sender fails at was announced, as the real one fails at it once the connection ends.
		syncsBefore(index + 1)
		v := map[string]any{"ok": false, "done": index, "total": total, "failedIndex": index, "delivery": parts[3],
			"error": map[string]any{"code": parts[1], "message": "fake " + parts[1]}}
		if keys > 0 {
			v["progress"] = map[string]any{"keys": keys}
		}
		reply(v)
		if parts[1] == "invalid_arguments" || parts[1] == "cannot_type" {
			return 2
		}
		return 1
	}
	reply(map[string]any{"ok": true, "done": total, "total": total, "failedIndex": nil, "delivery": "complete"})
	return 0
}

type inputRun struct {
	Event string   `json:"event"`
	Argv  []string `json:"argv"`
	Time  int64    `json:"time"`
}

func readInputLog(t *testing.T, path string) []inputRun {
	t.Helper()
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var runs []inputRun
	for _, line := range strings.Split(strings.TrimSpace(string(data)), "\n") {
		if line == "" {
			continue
		}
		var r inputRun
		if err := json.Unmarshal([]byte(line), &r); err != nil {
			t.Fatal(err)
		}
		runs = append(runs, r)
	}
	return runs
}

// actionsOf returns the actions of each sender run, without its options.
func actionsOf(runs []inputRun) [][]string {
	var out [][]string
	for _, r := range runs {
		if r.Event != "start" {
			continue
		}
		var actions []string
		for i := 0; i < len(r.Argv); i++ {
			if r.Argv[i] == "--socket" || r.Argv[i] == "--budget" || r.Argv[i] == "--locale" {
				i++
				continue
			}
			if r.Argv[i] == "--json" {
				continue
			}
			actions = append(actions, r.Argv[i])
		}
		out = append(out, actions)
	}
	return out
}

type fakeWindow struct {
	hypr.Window
	Width, Height float64
	Socket        bool
	// The socket's id, or "" for one named after the address.
	SocketID string
	Commits  int64
	Lease    *hypr.Lease
	Caps     hypr.Capabilities
	// The box of the window and its popups in window-local logical pixels, or nil for the window alone.
	PopupBox *hypr.Rect
	// The window has not caught up with its size. A wait without onWait ends once it has.
	Resizing bool
	// The window waits for a frame callback and has got none from hyprmasq. Waits send it frame callbacks, which end
	// that, also with onWait.
	Starved bool
	// The animations that a wait without onWait reports, in window-local logical pixels. They keep drawing, so a wait
	// without onWait that does not ignore animations sees the window draw also when it is idle.
	Animated []hypr.IntRect
	// The screenshots taken of the window, by which Paint can draw an animation.
	Frames int
	// The window has answered a wait without onWait and draws nothing more until the next state request, which a call
	// makes before it sends input.
	idle bool
	// The gray level at a window-local logical point, which screenshots sample at their scale, or nil for black.
	Paint func(x, y float64) uint8
	// Where the plugin found the window to come from, which state reports.
	Source *hypr.Source
	// The plugin's count of the user's input that took the keyboard back before a shortcut had settled.
	Interrupted int64
	// The plugin's record of the drag-and-drop operations of the window's app, which state reports with a socket, or
	// nil as from a plugin that keeps none.
	Drag *hypr.Drag
}

// fakeHyprland serves Hyprland's request socket with the plugin commands of the contract, and its event socket with
// the events that tell a window mapped or changed its title.
type fakeHyprland struct {
	t      *testing.T
	socket string
	// The event socket, and the connections of its clients.
	events      net.Listener
	subscribers []net.Conn
	// While not nil, a client of the event socket gets no events until it is closed, as one that Hyprland's main thread
	// has not accepted yet.
	acceptEvents chan struct{}
	// Called after a window list has been made, before it is sent, without the lock.
	onClients func()
	mu        sync.Mutex
	// Every request, in order, and when it arrived.
	requests []string
	times    []time.Time
	windows  []*fakeWindow
	// The address of the window with the keyboard focus, or "".
	active  string
	scale   float64
	iface   int
	missing bool
	waits   map[string]chan struct{}
	// Called for every wait with its arguments; returns the reply. Nil answers quiet at once.
	onWait func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string
	// Called for every state request before it is answered, with the lock held.
	onState func(w *fakeWindow)
	// Called for screenshots; a non-empty result is returned as an error reply.
	onScreenshot func(w *fakeWindow) string
	onDispatch   func(expression string) string
	// Called for every plugin request before anything else, without the lock; a reply with true answers it.
	onPlugin func(words []string) (string, bool)
	// The environments, executables and parents of processes, which the plugin reads in /proc.
	environ map[int]string
	exe     map[int]string
	parents map[int]int
	// The tokens of launched processes, by pid. Every launch starts pid 5000.
	launches map[int]string
	// Called for every launch after it was recorded, without the lock.
	onLaunch func(workspace, token, line string)
}

// launchToken finds the token of the launch that the process or a parent of it is, as the plugin does.
func (f *fakeHyprland) launchToken(pid int) string {
	for depth := 0; pid > 1 && depth < 32; depth, pid = depth+1, f.parents[pid] {
		if token, ok := f.launches[pid]; ok {
			return token
		}
	}
	return ""
}

func (w *fakeWindow) socketID() string {
	if w.SocketID == "" {
		return "fake-" + w.Address
	}
	return w.SocketID
}

func newFakeHyprland(t *testing.T) *fakeHyprland {
	// The sockets get a short directory of their own: t.TempDir names it after the test, which can make the path of the
	// event socket longer than a socket address holds.
	dir, err := os.MkdirTemp("", "hm-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	f := &fakeHyprland{t: t, socket: filepath.Join(dir, ".socket.sock"), scale: 2, iface: hypr.Interface, waits: map[string]chan struct{}{},
		environ: map[int]string{}, exe: map[int]string{}, parents: map[int]int{}, launches: map[int]string{}}
	l, err := net.Listen("unix", f.socket)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	go func() {
		for {
			conn, err := l.Accept()
			if err != nil {
				return
			}
			go f.serve(conn)
		}
	}()
	if f.events, err = net.Listen("unix", filepath.Join(filepath.Dir(f.socket), ".socket2.sock")); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.events.Close() })
	go func() {
		for {
			conn, err := f.events.Accept()
			if err != nil {
				return
			}
			var accept chan struct{}
			f.with(func() { accept = f.acceptEvents })
			if accept != nil {
				<-accept
			}
			f.with(func() { f.subscribers = append(f.subscribers, conn) })
			// A client sends nothing, so the read ends when it hangs up.
			go func() {
				io.Copy(io.Discard, conn)
				f.with(func() { f.subscribers = slices.DeleteFunc(f.subscribers, func(c net.Conn) bool { return c == conn }) })
				conn.Close()
			}()
		}
	}()
	return f
}

// postLocked writes an event to the clients of the event socket, as Hyprland does as it happens. Under f.mu.
func (f *fakeHyprland) postLocked(name, data string) {
	for _, conn := range f.subscribers {
		conn.SetWriteDeadline(time.Now().Add(time.Second))
		conn.Write([]byte(name + ">>" + data + "\n"))
	}
}

// subscribed counts the clients of the event socket.
func (f *fakeHyprland) subscribed() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.subscribers)
}

// setTitle changes a window's title, and posts the event.
func (f *fakeHyprland) setTitle(address, title string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	w := f.find(address)
	w.Title = title
	f.postLocked("windowtitlev2", strings.TrimPrefix(address, "0x")+","+title)
}

func (f *fakeHyprland) addWindow(w *fakeWindow) *fakeWindow {
	f.mu.Lock()
	defer f.mu.Unlock()
	if w.Caps.Keyboard == "" {
		w.Caps = hypr.Capabilities{Lease: true, Hold: true, PrivateDnD: true, Keyboard: "yes"}
	}
	if w.Workspace.Name == "" {
		w.Workspace = hypr.Workspace{ID: 1, Name: "1"}
	}
	w.Mapped = true
	f.windows = append(f.windows, w)
	f.postLocked("openwindow", fmt.Sprintf("%s,%s,%s,%s", strings.TrimPrefix(w.Address, "0x"), w.Workspace.Name, w.Class, w.Title))
	return w
}

// with runs fn with the fake's lock held, for changes while requests may be served.
func (f *fakeHyprland) with(fn func()) {
	f.mu.Lock()
	defer f.mu.Unlock()
	fn()
}

func (f *fakeHyprland) removeWindow(address string) {
	f.mu.Lock()
	defer f.mu.Unlock()
	for i, w := range f.windows {
		if w.Address == address {
			f.windows = append(f.windows[:i], f.windows[i+1:]...)
			return
		}
	}
}

func (f *fakeHyprland) find(address string) *fakeWindow {
	for _, w := range f.windows {
		if w.Address == address {
			return w
		}
	}
	return nil
}

func (f *fakeHyprland) requestsMatching(prefix string) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for _, r := range f.requests {
		if strings.HasPrefix(r, prefix) {
			out = append(out, r)
		}
	}
	return out
}

// timeOf returns when the first request with the prefix arrived.
func (f *fakeHyprland) timeOf(prefix string) time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	for i, r := range f.requests {
		if strings.HasPrefix(r, prefix) {
			return f.times[i]
		}
	}
	return time.Time{}
}

// nthTime returns when the request with the prefix after the first n of them arrived.
func (f *fakeHyprland) nthTime(prefix string, n int) time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	for i, r := range f.requests {
		if strings.HasPrefix(r, prefix) {
			if n == 0 {
				return f.times[i]
			}
			n--
		}
	}
	return time.Time{}
}

func (f *fakeHyprland) serve(conn net.Conn) {
	defer conn.Close()
	buf := make([]byte, 65536)
	var request []byte
	for {
		n, err := conn.Read(buf)
		request = append(request, buf[:n]...)
		if err != nil || n < len(buf) {
			break
		}
	}
	conn.Write([]byte(f.reply(string(request))))
}

func jsonReply(v any) string {
	out, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(out)
}

func pluginError(code, message string) string {
	return jsonReply(map[string]any{"ok": false, "error": message, "code": code})
}

func (f *fakeHyprland) reply(request string) string {
	f.mu.Lock()
	f.requests = append(f.requests, request)
	f.times = append(f.times, time.Now())
	f.mu.Unlock()
	switch {
	case request == "j/clients":
		f.mu.Lock()
		windows := []hypr.Window{}
		for _, w := range f.windows {
			win := w.Window
			win.Size = []int{int(w.Width), int(w.Height)}
			windows = append(windows, win)
		}
		onClients := f.onClients
		f.mu.Unlock()
		if onClients != nil {
			onClients()
		}
		return jsonReply(windows)
	case request == "j/activewindow":
		f.mu.Lock()
		defer f.mu.Unlock()
		if f.active == "" {
			return "{}"
		}
		return jsonReply(map[string]any{"address": f.active})
	case request == "j/monitors":
		return `[{"activeWorkspace":{"id":1,"name":"1"},"specialWorkspace":{"id":0,"name":""}}]`
	case strings.HasPrefix(request, "/dispatch "):
		f.mu.Lock()
		onDispatch := f.onDispatch
		f.mu.Unlock()
		if onDispatch != nil {
			return onDispatch(strings.TrimPrefix(request, "/dispatch "))
		}
		return "ok"
	case strings.HasPrefix(request, "j/hyprmasq "):
		if f.missing {
			return "unknown request"
		}
		return f.plugin(strings.Fields(strings.TrimPrefix(request, "j/hyprmasq ")))
	}
	return "unknown request"
}

func (f *fakeHyprland) state(w *fakeWindow) map[string]any {
	st := map[string]any{"ok": true, "address": w.Address, "stableId": w.StableID, "xwayland": w.XWayland,
		"size": map[string]any{"width": w.Width, "height": w.Height}, "resizing": w.Resizing, "starved": w.Starved, "scale": f.scale, "socket": nil, "commits": nil,
		"lease": nil, "capabilities": w.Caps, "source": w.Source}
	if w.Socket {
		st["socket"] = "/fake/" + w.Address + ".eis"
		st["socketId"] = w.socketID()
		st["commits"] = w.Commits
		st["interrupted"] = w.Interrupted
		if w.Drag != nil {
			st["drag"] = *w.Drag
		}
	}
	if w.Lease != nil {
		st["lease"] = w.Lease
	}
	return st
}

func (f *fakeHyprland) plugin(words []string) string {
	if len(words) == 0 {
		return pluginError("invalid_arguments", "usage")
	}
	f.mu.Lock()
	onPlugin := f.onPlugin
	f.mu.Unlock()
	if onPlugin != nil {
		if reply, ok := onPlugin(words); ok {
			return reply
		}
	}
	if words[0] == "version" {
		return jsonReply(map[string]any{"ok": true, "version": "0.3.0", "interface": f.iface})
	}
	if words[0] == "launch" && len(words) == 4 {
		workspace, err1 := hex.DecodeString(words[1])
		line, err2 := hex.DecodeString(words[3])
		if err1 != nil || err2 != nil {
			return pluginError("invalid_arguments", "not hex")
		}
		f.mu.Lock()
		f.launches[5000] = words[2]
		onLaunch := f.onLaunch
		f.mu.Unlock()
		if onLaunch != nil {
			onLaunch(string(workspace), words[2], string(line))
		}
		return jsonReply(map[string]any{"ok": true, "pid": 5000})
	}
	if words[0] == "launched" {
		f.mu.Lock()
		defer f.mu.Unlock()
		windows := []map[string]string{}
		for _, w := range f.windows {
			if token := f.launchToken(w.PID); token != "" {
				windows = append(windows, map[string]string{"address": w.Address, "stableId": w.StableID, "token": token})
			}
		}
		return jsonReply(map[string]any{"ok": true, "windows": windows})
	}
	if words[0] == "cancel" {
		f.mu.Lock()
		defer f.mu.Unlock()
		ch, ok := f.waits[words[1]]
		if ok {
			close(ch)
			delete(f.waits, words[1])
		}
		return jsonReply(map[string]any{"ok": true, "cancelled": ok})
	}
	addrIndex := 1
	if words[0] == "eis" {
		addrIndex = 2
	}
	if len(words) <= addrIndex {
		return pluginError("invalid_arguments", "usage")
	}
	f.mu.Lock()
	w := f.find(words[addrIndex])
	if w == nil {
		f.mu.Unlock()
		return pluginError("window_gone", "no mapped window "+words[addrIndex])
	}
	switch words[0] {
	case "state":
		defer f.mu.Unlock()
		w.idle = false
		if f.onState != nil {
			f.onState(w)
		}
		return jsonReply(f.state(w))
	case "eis":
		defer f.mu.Unlock()
		if !w.Socket {
			w.Socket = true
		}
		return jsonReply(map[string]any{"ok": true, "path": "/fake/" + w.Address + ".eis"})
	case "lease":
		defer f.mu.Unlock()
		if !w.Socket {
			return pluginError("no_socket", "no socket")
		}
		if words[2] == "release" {
			released := w.Lease != nil && (len(words) < 4 || w.Lease.Owner == words[3])
			if released {
				w.Lease = nil
			}
			return jsonReply(map[string]any{"ok": true, "released": released})
		}
		if !w.Caps.Lease {
			return pluginError("unsupported", "X11 windows cannot hold a lease")
		}
		ms, _ := strconv.Atoi(words[2])
		w.Lease = &hypr.Lease{Held: true, RemainingMs: ms, Owner: words[3]}
		return jsonReply(map[string]any{"ok": true, "held": true, "remainingMs": ms, "owner": words[3]})
	case "screenshot":
		defer f.mu.Unlock()
		return f.screenshot(w, words[2:])
	case "wait":
		args := map[string]string{}
		for i := 2; i+1 < len(words); i += 2 {
			args[words[i]] = words[i+1]
		}
		if since, _ := strconv.ParseInt(args["since"], 10, 64); since > w.Commits {
			f.mu.Unlock()
			return pluginError("invalid_arguments", fmt.Sprintf("since %d is above the commit counter, %d", since, w.Commits))
		}
		cancel := make(chan struct{})
		if id := args["id"]; id != "" {
			f.waits[id] = cancel
		}
		onWait := f.onWait
		commits, socketID := w.Commits, w.socketID()
		w.Starved = false
		f.mu.Unlock()
		defer func() {
			f.mu.Lock()
			if f.waits[args["id"]] == cancel {
				delete(f.waits, args["id"])
			}
			f.mu.Unlock()
		}()
		if onWait != nil {
			return onWait(w, args, cancel)
		}
		// The window draws three frames, the first one at its size, and goes quiet. A wait for a change after that, in
		// the same call and from the counter it ended at, sees none.
		since, _ := strconv.ParseInt(args["since"], 10, 64)
		f.mu.Lock()
		w.Resizing = false
		idle := w.idle && args["expect"] != "" && since == commits && (len(w.Animated) == 0 || args["ignore"] == "animations")
		if !idle {
			w.Commits, w.idle = commits+3, true
		}
		f.mu.Unlock()
		if idle {
			expect, _ := strconv.Atoi(args["expect"])
			return jsonReply(map[string]any{"ok": true, "ended": "no_redraw", "commits": commits, "socketId": socketID, "redrawObserved": false, "commitsSince": 0, "quietMs": 0, "elapsedMs": expect, "resizing": false,
				"animated": []hypr.IntRect{}})
		}
		quiet, _ := strconv.Atoi(args["quiet"])
		animated := append([]hypr.IntRect{}, w.Animated...)
		return jsonReply(map[string]any{"ok": true, "ended": "quiet", "commits": commits + 3, "socketId": socketID, "redrawObserved": true, "commitsSince": 3, "quietMs": quiet, "elapsedMs": 412, "resizing": false,
			"animated": animated})
	}
	f.mu.Unlock()
	return pluginError("invalid_arguments", "unknown subcommand "+words[0])
}

func (f *fakeHyprland) screenshot(w *fakeWindow, args []string) string {
	if f.onScreenshot != nil {
		if msg := f.onScreenshot(w); msg != "" {
			return pluginError("internal", msg)
		}
	}
	path := args[0]
	box := hypr.Rect{Width: w.Width, Height: w.Height}
	if w.PopupBox != nil {
		box = *w.PopupBox
	}
	region := box
	rest := args[1:]
	if len(rest) >= 4 {
		if _, err := strconv.Atoi(rest[0]); err == nil {
			v := make([]float64, 4)
			for i := range v {
				n, _ := strconv.Atoi(rest[i])
				v[i] = float64(n)
			}
			region = hypr.Rect{X: v[0], Y: v[1], Width: v[2], Height: v[3]}
			rest = rest[4:]
			if region.X >= box.X+box.Width || region.Y >= box.Y+box.Height || region.X+region.Width <= box.X || region.Y+region.Height <= box.Y {
				return pluginError("invalid_arguments", fmt.Sprintf("the region lies outside the %vx%v box of the window and its popups at %v,%v", box.Width, box.Height, box.X, box.Y))
			}
			// The plugin cuts the region to the box.
			right, bottom := math.Min(region.X+region.Width, box.X+box.Width), math.Min(region.Y+region.Height, box.Y+box.Height)
			region.X, region.Y = math.Max(region.X, box.X), math.Max(region.Y, box.Y)
			region.Width, region.Height = right-region.X, bottom-region.Y
		}
	}
	scale := f.scale
	raw, detailPath := false, ""
	for i := 0; i < len(rest); i++ {
		switch {
		case rest[i] == "max" && i+1 < len(rest):
			limit, _ := strconv.Atoi(rest[i+1])
			if long := math.Max(region.Width, region.Height) * scale; long > float64(limit) {
				scale *= float64(limit) / long
			}
		case rest[i] == "raw":
			raw = true
		case rest[i] == "detail" && i+1 < len(rest):
			detailPath = rest[i+1]
		}
	}
	w.Frames++
	width, height, err := f.writeImage(w, path, region, scale, raw)
	if err != nil {
		return pluginError("internal", err.Error())
	}
	reply := map[string]any{"ok": true, "path": path, "width": width, "height": height, "scale": scale, "region": region, "resizing": w.Resizing, "starved": w.Starved, "detail": nil}
	// The detail image has a pixel per logical pixel, or the monitor's pixels where they are fewer.
	if least := math.Min(f.scale, 1); detailPath != "" && scale < least-1e-6 {
		limit := 0
		if f.scale > 1 {
			limit = int(math.Max(region.Width, region.Height))
		}
		dw, dh, err := f.writeImage(w, detailPath, region, least, true)
		if err != nil {
			return pluginError("internal", err.Error())
		}
		reply["detail"] = map[string]any{"path": detailPath, "width": dw, "height": dh, "scale": least, "region": region, "max": limit}
	}
	if w.Socket {
		reply["commits"] = w.Commits
		reply["socketId"] = w.socketID()
	}
	return jsonReply(reply)
}

// writeImage renders the window's region at the scale, from its Paint, as a gray PNG, or as raw pixels as the plugin
// writes them.
func (f *fakeHyprland) writeImage(w *fakeWindow, path string, region hypr.Rect, scale float64, raw bool) (int, int, error) {
	width, height := int(math.Round(region.Width*scale)), int(math.Round(region.Height*scale))
	img := image.NewGray(image.Rect(0, 0, width, height))
	if w.Paint != nil {
		for py := 0; py < height; py++ {
			for px := 0; px < width; px++ {
				img.Pix[py*img.Stride+px] = w.Paint(region.X+(float64(px)+0.5)/scale, region.Y+(float64(py)+0.5)/scale)
			}
		}
	}
	if raw {
		pix := make([]byte, 0, 4*width*height)
		for _, v := range img.Pix {
			pix = append(pix, v, v, v, 255)
		}
		return width, height, os.WriteFile(path, pix, 0o600)
	}
	file, err := os.Create(path)
	if err != nil {
		return 0, 0, err
	}
	defer file.Close()
	return width, height, png.Encode(file, img)
}

type testEnv struct {
	t        *testing.T
	fake     *fakeHyprland
	server   *Server
	stateDir string
	inputLog string
}

func newTestEnv(t *testing.T) *testEnv {
	e := &testEnv{t: t, fake: newFakeHyprland(t), stateDir: filepath.Join(t.TempDir(), "state")}
	e.inputLog = filepath.Join(t.TempDir(), "input.log")
	t.Setenv("HYPRMASQ_FAKE_INPUT", "1")
	t.Setenv("FAKE_INPUT_LOG", e.inputLog)
	t.Setenv("FAKE_INPUT_MODE", "ok")
	e.server = e.newServer()
	return e
}

// newServer starts another server session on the same Hyprland, as a second agent would.
func (e *testEnv) newServer() *Server {
	s := New(Config{HyprlandSocket: e.fake.socket, StateDir: e.stateDir, Input: os.Args[0], LeaseMs: 60000,
		Environ: func(pid int) ([]byte, error) {
			var env string
			var ok bool
			e.fake.with(func() { env, ok = e.fake.environ[pid] })
			if !ok {
				return nil, os.ErrNotExist
			}
			return []byte(env), nil
		},
		Exe: func(pid int) (string, error) {
			var exe string
			var ok bool
			e.fake.with(func() { exe, ok = e.fake.exe[pid] })
			if !ok {
				return "", os.ErrNotExist
			}
			return exe, nil
		}})
	// Sessions in one test process share the pid, so they need their own shot directories and owners.
	s.shotDir = filepath.Join(e.t.TempDir(), "shots")
	s.owner = "mcp-test-" + randomHex(4)
	e.t.Cleanup(func() { s.Shutdown(context.Background()) })
	return s
}

func (e *testEnv) setEnviron(pid int, env string) {
	e.fake.with(func() { e.fake.environ[pid] = env })
}

// setLaunched makes pid a launched process with the token.
func (e *testEnv) setLaunched(pid int, token string) {
	e.fake.with(func() { e.fake.launches[pid] = token })
}

// setParent makes parent the parent process of pid.
func (e *testEnv) setParent(pid, parent int) {
	e.fake.with(func() { e.fake.parents[pid] = parent })
}

func (e *testEnv) setExe(pid int, exe string) {
	e.fake.with(func() { e.fake.exe[pid] = exe })
}

func (e *testEnv) window(address, stableID string, width, height float64, options ...func(*fakeWindow)) *fakeWindow {
	w := &fakeWindow{Window: hypr.Window{Address: address, StableID: stableID, Class: "test.app", Title: "Test", PID: 4242}, Width: width, Height: height,
		Caps: hypr.Capabilities{Lease: true, Hold: true, PrivateDnD: true, Keyboard: "yes"}}
	for _, option := range options {
		option(w)
	}
	return e.fake.addWindow(w)
}

// waitFor polls until ready, for at most 20 seconds, so that steps of a test follow the server's progress also on a
// loaded machine. It may run outside the test's goroutine.
func (e *testEnv) waitFor(ready func() bool) bool {
	for deadline := time.Now().Add(20 * time.Second); time.Now().Before(deadline); time.Sleep(5 * time.Millisecond) {
		if ready() {
			return true
		}
	}
	return false
}

// senderLogged tells whether a fake sender logged the event, such as start. It does not fail the test, so it may run
// outside the test's goroutine.
func (e *testEnv) senderLogged(event string) bool {
	data, _ := os.ReadFile(e.inputLog)
	return strings.Contains(string(data), `"event":"`+event+`"`)
}

func (e *testEnv) inputs() [][]string {
	return actionsOf(readInputLog(e.t, e.inputLog))
}

func callTool(s *Server, ctx context.Context, tool func(*Server) func(context.Context, *call) *mcp.CallToolResult, args string) *mcp.CallToolResult {
	return tool(s)(ctx, &call{args: json.RawMessage(args)})
}

func actOn(s *Server) func(context.Context, *call) *mcp.CallToolResult { return s.actTool }
func screenshotOf(s *Server) func(context.Context, *call) *mcp.CallToolResult {
	return s.screenshotTool
}

func (e *testEnv) act(args string) *mcp.CallToolResult {
	return mirrored(e.t, callTool(e.server, context.Background(), actOn, args))
}

func (e *testEnv) screenshot(args string) *mcp.CallToolResult {
	return mirrored(e.t, callTool(e.server, context.Background(), screenshotOf, args))
}

func (e *testEnv) launch(args string) *mcp.CallToolResult {
	return mirrored(e.t, e.server.launchTool(context.Background(), &call{args: json.RawMessage(args)}))
}

func (e *testEnv) close(args string) *mcp.CallToolResult {
	return mirrored(e.t, e.server.closeTool(context.Background(), &call{args: json.RawMessage(args)}))
}

func (e *testEnv) release(args string) *mcp.CallToolResult {
	return mirrored(e.t, e.server.releaseTool(context.Background(), &call{args: json.RawMessage(args)}))
}

// mirrored checks that the structured content of a result carries everything its text says, since some clients pass
// the model only the structured content: the error, the summary and the hint. The windows tool lists every field of its
// lines.
func mirrored(t *testing.T, res *mcp.CallToolResult) *mcp.CallToolResult {
	t.Helper()
	data := structured(t, res)
	if _, ok := data["windows"]; ok {
		return res
	}
	var lines []string
	if e, ok := data["error"].(map[string]any); ok {
		lines = append(lines, fmt.Sprintf("error %s: %s", e["code"], e["message"]))
	}
	for _, key := range []string{"summary", "hint"} {
		if line, _ := data[key].(string); line != "" {
			lines = append(lines, line)
		}
	}
	if got, want := text(res), strings.Join(lines, "\n"); got != want {
		t.Errorf("the text %q is not the structured content's %q", got, want)
	}
	return res
}

// text returns the text content of a result.
func text(res *mcp.CallToolResult) string {
	var parts []string
	for _, c := range res.Content {
		if t, ok := c.(*mcp.TextContent); ok {
			parts = append(parts, t.Text)
		}
	}
	return strings.Join(parts, "\n")
}

func hasImage(res *mcp.CallToolResult) bool {
	for _, c := range res.Content {
		if _, ok := c.(*mcp.ImageContent); ok {
			return true
		}
	}
	return false
}

// structured returns the structured content as plain JSON values.
func structured(t *testing.T, res *mcp.CallToolResult) map[string]any {
	t.Helper()
	data, err := json.Marshal(res.StructuredContent)
	if err != nil {
		t.Fatal(err)
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

// get reads a path such as "actions.delivery" from structured content.
func get(t *testing.T, res *mcp.CallToolResult, path string) any {
	t.Helper()
	var v any = structured(t, res)
	for _, key := range strings.Split(path, ".") {
		if m, ok := v.(map[string]any); ok {
			v = m[key]
		} else if a, ok := v.([]any); ok {
			i, _ := strconv.Atoi(key)
			if i >= len(a) {
				return nil
			}
			v = a[i]
		} else {
			return nil
		}
	}
	return v
}

// errorCode returns the code of a failure, which is a tool error unless its result has a shot.
func errorCode(t *testing.T, res *mcp.CallToolResult) string {
	t.Helper()
	code, _ := get(t, res, "error.code").(string)
	// A shot of a window that input opened counts as one, and so does one of the window that a closed window came from.
	hasShot := get(t, res, "shot") != nil || get(t, res, "source.shot") != nil
	for _, list := range []string{"new_windows", "late_windows"} {
		if windows, ok := get(t, res, list).([]any); ok {
			for _, w := range windows {
				hasShot = hasShot || w.(map[string]any)["shot"] != nil
			}
		}
	}
	if code != "" && res.IsError == hasShot {
		t.Errorf("isError %v for a failure with a shot %v: %s", res.IsError, hasShot, text(res))
	}
	return code
}

var shotIDPattern = regexp.MustCompile(`^s\d+$`)

func shotID(t *testing.T, res *mcp.CallToolResult) string {
	t.Helper()
	id, _ := get(t, res, "shot.id").(string)
	if !shotIDPattern.MatchString(id) {
		t.Fatalf("no shot in %s", text(res))
	}
	return id
}
