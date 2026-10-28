package server

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"regexp"
	"strings"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestLaunch(t *testing.T) {
	e := newTestEnv(t)
	// A window of another launch whose process carries a foreign token.
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xc0", StableID: "c0", Class: "decoy", PID: 5001}, Width: 50, Height: 50})
	e.setLaunched(5001, "0123456789abcdef")
	var line, workspace, token string
	e.fake.with(func() {
		e.fake.onLaunch = func(w, k, l string) {
			workspace, token, line = w, k, l
			time.AfterFunc(250*time.Millisecond, func() {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", Title: "it's", PID: 5000,
					Workspace: hypr.Workspace{ID: -98, Name: "name:tests"}}, Width: 320, Height: 200})
			})
		}
	})
	res := e.launch(`{"command":["foot","-T","it's"],"workspace":"tests"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if line != `exec 'foot' '-T' 'it'\''s'` || workspace != "tests" || !regexp.MustCompile(`^[0-9a-f]{16}$`).MatchString(token) {
		t.Errorf("command line %q on %q as %q", line, workspace, token)
	}
	if got := get(t, res, "window.window"); got != "0xe1" {
		t.Errorf("window %v", got)
	}
	if !hasImage(res) || get(t, res, "shot.id") == nil || get(t, res, "wait.ended") != "quiet" {
		t.Errorf("result %s", text(res))
	}
	if args := waitArgsOf(t, e.fake.requestsMatching("j/hyprmasq wait 0xe1")[0]); args["expect"] != "" || args["quiet"] != "300" {
		t.Errorf("settle wait %v", args)
	}
	if !strings.HasPrefix(text(res), `launched 0xe1 foot "it's" · pid 5000`) || get(t, res, "hint") != firstViewHint {
		t.Errorf("text %q", text(res))
	}

	res = e.server.windowsTool(context.Background(), &call{})
	launched := map[string]bool{}
	for _, w := range get(t, res, "windows").([]any) {
		w := w.(map[string]any)
		launched[w["window"].(string)] = w["launched"].(bool)
	}
	if !reflect.DeepEqual(launched, map[string]bool{"0xc0": false, "0xe1": true}) {
		t.Errorf("launched %v", launched)
	}
}

// The window of a child process of the launched one counts, as a browser's that its launcher script starts without exec.
func TestLaunchFindsTheWindowOfAChild(t *testing.T) {
	e := newTestEnv(t)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			time.AfterFunc(100*time.Millisecond, func() {
				e.setParent(5001, 5000)
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe2", StableID: "e2", Class: "brave", PID: 5001,
					Workspace: hypr.Workspace{ID: -98, Name: "name:tests"}}, Width: 320, Height: 200})
			})
		}
	})
	res := e.launch(`{"command":["brave"],"workspace":"tests"}`)
	if res.IsError || get(t, res, "window.window") != "0xe2" || get(t, res, "window.pid") != 5001.0 {
		t.Fatal(text(res))
	}
	res = e.server.windowsTool(context.Background(), &call{})
	if w := get(t, res, "windows").([]any)[0].(map[string]any); w["window"] != "0xe2" || w["launched"] != true {
		t.Errorf("windows %v", w)
	}
}

// A launched window that closed between the plugin's list and the window list may have left its address to another
// window, which is not the launch's.
func TestLaunchIgnoresAWindowAtAReusedAddress(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	var token string
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, k, line string) {
			token = k
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "bb", Class: "other", PID: 7000}, Width: 320, Height: 200})
		}
		e.fake.onPlugin = func(words []string) (string, bool) {
			if words[0] != "launched" || token == "" {
				return "", false
			}
			return jsonReply(map[string]any{"ok": true, "windows": []map[string]string{{"address": "0xe1", "stableId": "aa", "token": token}}}), true
		}
	})
	if code := errorCode(t, e.launch(`{"command":["foot"],"timeout_ms":1000}`)); code != "deadline" {
		t.Errorf("code %q", code)
	}
	res := e.server.windowsTool(context.Background(), &call{})
	if w := get(t, res, "windows").([]any)[0].(map[string]any); w["window"] != "0xe1" || w["launched"] != false {
		t.Errorf("windows %v", w)
	}
}

// A launched tiled window takes space from the windows on its workspace, whose earlier shots are then stale.
func TestLaunchNamesResizedWindows(t *testing.T) {
	e := newTestEnv(t)
	tests := hypr.Workspace{ID: -98, Name: "name:tests"}
	left := e.window("0xa1", "1a", 640, 480, func(w *fakeWindow) { w.Workspace = tests })
	e.window("0xb2", "2b", 640, 480, func(w *fakeWindow) { w.Workspace = tests })
	other := e.window("0xc3", "3c", 640, 480)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000, Workspace: tests}, Width: 320, Height: 480})
			// Only the size counts: 0xb2 keeps its own, and 0xc3 on another workspace is resized by something else.
			e.fake.with(func() { left.Width, other.Width = 320, 320 })
		}
	})
	res := e.launch(`{"command":["foot"],"workspace":"tests"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if hint := get(t, res, "hint"); hint != joinHints("The new window resized the windows on its workspace (0xa1), so their earlier shots are stale: take a new screenshot before clicking in one.", firstViewHint) {
		t.Errorf("hint %v", hint)
	}
}

func TestLaunchTimeout(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	start := time.Now()
	res := e.launch(`{"command":["footclient"],"timeout_ms":1000}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "already running") {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// The window's settling and capture add up to 6 seconds to a launch that found its window.
	if elapsed := time.Since(start); elapsed > 4*time.Second {
		t.Errorf("took %v", elapsed)
	}
	for _, args := range []string{`{"command":[]}`, `{"command":["a"],"workspace":"x y"}`, `{"command":["a"],"timeout_ms":999}`, `{"cmd":["a"]}`} {
		if code := errorCode(t, e.launch(args)); code != "invalid_arguments" {
			t.Errorf("%s: code %q", args, code)
		}
	}
}

// The server forgets the token of a launch once its process has exited and none of its windows is left, and the token
// of a launch that the plugin refused at once.
func TestLaunchTokensEndWithTheLaunch(t *testing.T) {
	e := newTestEnv(t)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000}, Width: 320, Height: 200})
		}
	})
	if res := e.launch(`{"command":["foot"]}`); res.IsError {
		t.Fatal(text(res))
	}
	tokens := func() int {
		e.server.launchedWindows(context.Background())
		e.server.mu.Lock()
		defer e.server.mu.Unlock()
		return len(e.server.tokens)
	}
	// The process may open more windows while it runs, and its window keeps the token after it exited.
	e.fake.removeWindow("0xe1")
	if n := tokens(); n != 1 {
		t.Errorf("tokens while the process runs: %d", n)
	}
	e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe2", StableID: "e2", Class: "foot", PID: 5000}, Width: 320, Height: 200})
	e.fake.with(func() { e.fake.exited[5000] = true })
	if n := tokens(); n != 1 {
		t.Errorf("tokens while a window is left: %d", n)
	}
	e.fake.removeWindow("0xe2")
	if n := tokens(); n != 0 {
		t.Errorf("tokens after the launch ended: %d", n)
	}

	e.fake.with(func() {
		e.fake.onPlugin = func(words []string) (string, bool) {
			if words[0] == "launch" {
				return pluginError("invalid_arguments", "no"), true
			}
			return "", false
		}
	})
	if code := errorCode(t, e.launch(`{"command":["foot"]}`)); code != "invalid_arguments" {
		t.Errorf("code %q", code)
	}
	// Nor does a request that never reached Hyprland: the socket is gone after the window list that comes before it.
	gone := e.fake.socket + ".gone"
	e.fake.with(func() {
		e.fake.onPlugin = nil
		e.fake.onClients = func() { os.Rename(e.fake.socket, gone) }
	})
	if res := e.launch(`{"command":["foot"]}`); !res.IsError {
		t.Errorf("launched without Hyprland: %s", text(res))
	}
	os.Rename(gone, e.fake.socket)
	e.server.mu.Lock()
	defer e.server.mu.Unlock()
	if len(e.server.tokens) != 0 {
		t.Errorf("tokens after launches that started nothing: %v", e.server.tokens)
	}
}

// A launched window that does not settle in time is still drawing, or, when input from elsewhere kept it from counting
// as quiet, drew nothing.
func TestLaunchedWindowThatDoesNotSettle(t *testing.T) {
	for _, c := range []struct {
		redrawn, resizing bool
		summary, text     string
	}{
		{true, false, "busy", "still drawing after 5000 ms"},
		{false, false, "no_change", "not settled after 5000 ms, no redraw"},
		// It never drew at the size it was given.
		{false, true, "resizing", "still resizing after 5000 ms"},
	} {
		e := newTestEnv(t)
		e.fake.with(func() {
			e.fake.onLaunch = func(workspace, token, line string) {
				e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000}, Width: 320, Height: 200, Resizing: c.resizing})
			}
			e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
				return jsonReply(map[string]any{"ok": true, "ended": "timeout", "commits": 0, "redrawObserved": c.redrawn, "commitsSince": 0, "quietMs": 0, "elapsedMs": 5000, "resizing": c.resizing})
			}
		})
		res := e.launch(`{"command":["foot"]}`)
		if res.IsError || get(t, res, "wait.summary") != c.summary || (get(t, res, "wait.resizing") == true) != c.resizing || !strings.Contains(text(res), " · "+c.text+" · shot ") {
			t.Errorf("redrawn %v: %s, %v", c.redrawn, text(res), get(t, res, "wait"))
		}
		// Only a shot that shows an older frame comes with a hint, which says so.
		id, _ := get(t, res, "shot.id").(string)
		want := ""
		if c.resizing {
			want = resizingHint(&shotOut{ID: id, Resizing: true})
		}
		if hint, _ := get(t, res, "hint").(string); (get(t, res, "shot.resizing") == true) != c.resizing || hint != want {
			t.Errorf("resizing %v: %s", c.resizing, text(res))
		}
	}
}

func TestLaunchCancelledWhileSettling(t *testing.T) {
	e := newTestEnv(t)
	e.fake.with(func() {
		e.fake.onLaunch = func(workspace, token, line string) {
			e.fake.addWindow(&fakeWindow{Window: hypr.Window{Address: "0xe1", StableID: "e1", Class: "foot", PID: 5000}, Width: 320, Height: 200})
		}
		e.fake.onWait = blockingWait(make(chan struct{}))
	})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq wait") != nil })
		cancel()
	}()
	res := e.server.launchTool(ctx, &call{args: json.RawMessage(`{"command":["foot"]}`)})
	if code := errorCode(t, res); code != "cancelled" || !strings.Contains(text(res), "0xe1") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq screenshot"); got != nil {
		t.Errorf("captured after the cancellation: %q", got)
	}
}

func TestLaunchRejectsOverlongCommands(t *testing.T) {
	e := newTestEnv(t)
	args, _ := json.Marshal(map[string]any{"command": []string{"sh", "-c", strings.Repeat("x", hypr.MaxRequest)}})
	res := e.server.launchTool(context.Background(), &call{args: args})
	if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), "too long") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq launch"); got != nil {
		t.Errorf("sent %d launch requests", len(got))
	}
}
