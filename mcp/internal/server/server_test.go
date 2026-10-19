package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf16"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

// connect returns a session of an MCP client with the server's registered tools, which calls them as clients do.
func connect(t *testing.T, s *Server, opts *mcp.ClientOptions) *mcp.ClientSession {
	t.Helper()
	return connectAs(t, s, "test", opts)
}

// connectAs is connect for a client that introduces itself by name, such as claude-code.
func connectAs(t *testing.T, s *Server, name string, opts *mcp.ClientOptions) *mcp.ClientSession {
	t.Helper()
	serverTransport, clientTransport := mcp.NewInMemoryTransports()
	ctx := context.Background()
	if _, err := s.NewMCPServer().Connect(ctx, serverTransport, nil); err != nil {
		t.Fatal(err)
	}
	session, err := mcp.NewClient(&mcp.Implementation{Name: name, Version: "0"}, opts).Connect(ctx, clientTransport, nil)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { session.Close() })
	return session
}

// Arguments of the wrong shape are refused before anything is sent, named by their path, down to an element of an
// array, and described in JSON's terms. A null where a number, an array element or a required field belongs is
// refused instead of being read as 0 or "". The SDK leaves checking the arguments against the schema to the server,
// so the calls go through a client.
func TestArgumentErrors(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	session := connect(t, e.server, nil)
	ctx := context.Background()
	call := func(tool, args string) *mcp.CallToolResult {
		t.Helper()
		res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: json.RawMessage(args)})
		if err != nil {
			t.Fatal(err)
		}
		return mirrored(t, res)
	}
	if res := call("screenshot", `{"window":"0xa1"}`); res.IsError {
		t.Fatal(text(res))
	}
	act := func(actions string) string { return `{"window":"0xa1","shot":"s1","actions":[` + actions + `]}` }
	region := `"shot":"s1","x":0,"y":0`
	for _, c := range []struct {
		tool, args, message string
	}{
		{"act", `{"window":"0xa1","actions":"[]"}`, `actions must be an array such as [{"action":"key","keys":["enter"]}], not a string`},
		{"act", `{"window":"0xa1","wait":"{}"}`, `wait must be an object such as {"expect_ms":2000}, not a string`},
		{"act", `{"window":"0xa1","wait":{"expect":500}}`, "wait.expect is not a field of wait, which takes expect_change, expect_ms, quiet_ms"},
		{"act", `{"window":"0xa1","wait":{"quiet_ms":"300"}}`, "wait.quiet_ms must be an integer, not a string"},
		{"act", `{"window":"0xa1","wait":{"expect_change":"no"}}`, "wait.expect_change must be true or false, not a string"},
		{"act", `{"window":"0xa1","deadline_ms":1500.5}`, "deadline_ms must be an integer, not 1500.5"},
		{"act", `{"window":"0xa1","wiat":{}}`, `unknown argument "wiat"; the arguments are window, shot, actions, wait, capture, deadline_ms`},
		// As encoding/json, a name that differs only in case names the field. The first unknown one is named.
		{"act", `{"window":"0xa1","WAIT":{},"zz":1,"aa":2}`, `unknown argument "zz"; the arguments are`},
		{"act", `{"window":"0xa1","wait":{"Quiet_MS":300,"b":1}}`, "wait.b is not a field of wait"},
		{"act", `{"window":"0xa1","capture":{"shot":"s1","x":0,"y":0,"w":1,"height":1}}`, "capture.w is not a field of capture"},
		{"act", `{"window":161}`, "window must be a string, not a number"},
		{"act", act(`{"action":"click","x":"1","y":1}`), "actions[0].x must be a number, not a string"},
		{"act", act(`{"action":"click","x":1,"y":1,"count":1.5}`), "actions[0].count must be an integer, not 1.5"},
		{"act", act(`{"action":"click","x":1,"y":1,"button":1}`), "actions[0].button must be a string, not a number"},
		{"act", act(`{"action":"drag","from":[100],"to":[200,50]}`), "actions[0].from must be [x, y] in shot pixels, not [100]"},
		{"act", act(`{"action":"drag","from":[1,2],"to":[200,50,7]}`), "actions[0].to must be [x, y] in shot pixels, not [200,50,7]"},
		{"act", act(`{"action":"drag","from":["1",2],"to":[200,50]}`), "actions[0].from[0] must be a number, not a string"},
		{"act", act(`{"action":"click","x":1,"y":1,"modifiers":[1]}`), "actions[0].modifiers[0] must be a string, not a number"},
		{"act", act(`{"action":"key","keys":["ctrl",true]}`), "actions[0].keys[1] must be a string, not true or false"},
		{"act", act(`{"action":"key","keys":"ctrl+l"}`), `actions[0].keys must be a non-empty array of key names, such as ["ctrl","l"] or ["enter"]`},
		{"act", act(`{"action":"click","x":null,"y":100}`), "actions[0].x must be a number, not null"},
		{"act", act(`{"action":"click","x":10,"y":null}`), "actions[0].y must be a number, not null"},
		{"act", act(`{"action":"move","x":null,"y":null}`), "actions[0].x must be a number, not null"},
		{"act", act(`{"action":"drag","from":[null,100],"to":[200,100]}`), "actions[0].from[0] must be a number, not null"},
		{"act", act(`{"action":"drag","from":[10,10],"to":[200,null]}`), "actions[0].to[1] must be a number, not null"},
		{"act", act(`{"action":"drag","from":[10,10],"to":null}`), "actions[0].to must be an array, not null"},
		{"act", act(`{"action":"scroll","x":null,"y":10,"dy":1}`), "actions[0].x must be a number, not null"},
		{"act", act(`{"action":"scroll","x":10,"y":10,"dx":1,"dy":null}`), "actions[0].dy must be a number, not null"},
		{"act", act(`{"action":"click","x":10,"y":10,"count":null}`), "actions[0].count must be an integer, not null"},
		{"act", act(`{"action":"click","x":10,"y":10,"modifiers":[null]}`), "actions[0].modifiers[0] must be a string, not null"},
		{"act", act(`{"action":"key","keys":["ctrl",null]}`), "actions[0].keys[1] must be a string, not null"},
		{"act", act(`{"action":"key","keys":["a"],"repeat":null}`), "actions[0].repeat must be an integer, not null"},
		{"act", act(`{"action":"type","text":null}`), "actions[0].text must be a string, not null"},
		{"act", act(`{"action":"type","text":"a"},{"action":"pause","ms":null}`), "actions[1].ms must be an integer, not null"},
		{"act", `{"window":null}`, "window must be a string, not null"},
		{"act", `{"window":"0xa1","deadline_ms":null}`, "deadline_ms must be an integer, not null"},
		{"act", `{"window":"0xa1","wait":{"expect_ms":null}}`, "wait.expect_ms must be an integer, not null"},
		{"act", `{"window":"0xa1","wait":{"quiet_ms":null}}`, "wait.quiet_ms must be an integer, not null"},
		{"act", `{"window":"0xa1","capture":{` + region + `,"width":null,"height":10}}`, "capture.width must be a number, not null"},
		{"act", `{"window":"0xa1","capture":{"shot":"s1","x":0,"width":10,"height":10}}`, "capture.y is missing"},
		{"act", `{"window":"0xa1","capture":{"region":{` + region + `,"width":10,"height":null}}}`, "capture.region.height must be a number, not null"},
		{"screenshot", `{"window":"0xa1","region":"s1"}`, `region must be an object such as {"shot":"s4","x":0,"y":0,"width":100,"height":50}, not a string`},
		{"screenshot", `{"window":"0xa1","region":{` + region + `,"width":1,"height":1,"scale":2}}`, "region.scale is not a field of region, which takes shot, x, y, width, height"},
		{"screenshot", `{"window":"0xa1","region":{"shot":"s1","x":null,"y":0,"width":10,"height":10}}`, "region.x must be a number, not null"},
		{"screenshot", `{"window":"0xa1","region":{"shot":"s1","x":0,"width":10,"height":10}}`, "region.y is missing"},
		{"screenshot", `{"window":"0xa1","region":{` + region + `,"width":"10","height":10}}`, "region.width must be a number, not a string"},
		{"screenshot", `{"window":"0xa1","region":{"shot":null,"x":0,"y":0,"width":10,"height":10}}`, "region.shot must be a string, not null"},
		{"screenshot", `{"window":null}`, "window must be a string, not null"},
		{"launch", `{"command":"foot -T x"}`, `command must be an array such as ["foot","-T","scratch"], not a string`},
		{"launch", `{"command":["foot",1]}`, "command[1] must be a string, not a number"},
		{"launch", `{"command":["foot",null,"-T","x"]}`, "command[1] must be a string, not null"},
		{"launch", `{"command":null}`, `command must be an array such as ["foot","-T","scratch"], not null`},
		{"launch", `{"command":["foot"],"timeout_ms":null}`, "timeout_ms must be an integer, not null"},
		{"launch", `{"command":["foot"],"workspace":7}`, "workspace must be a string, not a number"},
		{"release", `{"window":null}`, "window must be a string, not null"},
		{"close", `{"window":["0xa1"]}`, "window must be a string, not an array"},
	} {
		res := call(c.tool, c.args)
		if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), c.message) {
			t.Errorf("%s %s: code %q: %s", c.tool, c.args, code, text(res))
		}
		if c.tool == "act" && get(t, res, "actions.delivery") != "none" {
			t.Errorf("%s %s: delivery %v", c.tool, c.args, get(t, res, "actions.delivery"))
		}
	}
	if got := e.inputs(); got != nil {
		t.Errorf("sent %q", got)
	}
	for _, prefix := range []string{"j/hyprmasq wait", "j/hyprmasq lease", "/dispatch"} {
		if got := e.fake.requestsMatching(prefix); got != nil {
			t.Errorf("requested %q", got)
		}
	}
	if got := e.fake.requestsMatching("j/hyprmasq screenshot"); len(got) != 1 {
		t.Errorf("captured %q", got)
	}

	// An optional field that is not a number takes its default when it is null, as if it were left out.
	res := call("act", `{"window":"0xa1","shot":"s1","wait":null,"capture":null,"actions":[`+
		`{"action":"click","x":10,"y":10,"button":null,"modifiers":null},{"action":"scroll","x":10,"y":10,"dy":2,"unit":null}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got, want := e.inputs(), [][]string{{"move", "5", "5", "click", "left", "1", "-", "sync", "0", "1000", "move", "5", "5", "wheel", "0", "2"}}; !reflect.DeepEqual(got, want) {
		t.Errorf("inputs %q, want %q", got, want)
	}
	if res := call("screenshot", `{"window":"0xa1","region":null}`); res.IsError {
		t.Fatal(text(res))
	}
}

func TestPluginChecks(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() { e.fake.iface = 1 })
	res := e.act(`{"window":"0xa1"}`)
	if code := errorCode(t, res); code != "plugin_version" || get(t, res, "hint") != hintPluginUser {
		t.Errorf("code %q: %s", code, text(res))
	}
	e.fake.with(func() { e.fake.missing = true })
	if code := errorCode(t, e.screenshot(`{"window":"0xa1"}`)); code != "plugin_missing" {
		t.Errorf("code %q", code)
	}
	e.server.hypr.Socket = "/nonexistent/.socket.sock"
	res = e.server.windowsTool(context.Background(), &call{})
	if code := errorCode(t, res); code != "plugin_missing" || !strings.Contains(text(res), "HYPRLAND_INSTANCE_SIGNATURE") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if code := errorCode(t, e.act(`{"window":"0xa1"}`)); code != "plugin_missing" {
		t.Errorf("code %q", code)
	}
}

// Hyprland and the plugin write addresses in lower case without leading zeros, and compare them as strings, so the tools
// take an address in another form as that one.
func TestAddressInAnotherForm(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	res := e.screenshot(`{"window":"0xA1"}`)
	if res.IsError || !strings.Contains(text(res), "window 0xa1 ") {
		t.Fatal(text(res))
	}
	res = e.act(`{"window":"0x00A1","shot":"` + shotID(t, res) + `","actions":[{"action":"click","x":10,"y":10}]}`)
	if res.IsError || len(e.inputs()) != 1 {
		t.Fatal(text(res))
	}
	if res := e.release(`{"window":"0xA1"}`); res.IsError || get(t, res, "released") != true {
		t.Fatal(text(res))
	}
	e.fake.with(func() {
		e.fake.onDispatch = func(expression string) string {
			if expression == `hl.dsp.window.close({ window = "stableid:1a" })` {
				e.fake.removeWindow("0xa1")
			}
			return "ok"
		}
	})
	if res := e.close(`{"window":"0x0a1"}`); res.IsError || get(t, res, "closed") != true || !strings.HasPrefix(text(res), "closed 0xa1 ") {
		t.Fatal(text(res))
	}
	for _, request := range e.fake.requestsMatching("j/hyprmasq ") {
		if words := strings.Fields(request); len(words) > 2 && strings.HasPrefix(words[2], "0x") && words[2] != "0xa1" {
			t.Errorf("requested %q", request)
		}
	}
}

// Claude Code cuts tool descriptions after 2048 UTF-16 code units, so the rules at the end of a longer one would never
// reach the model.
func TestDescriptionsFitClaudeCode(t *testing.T) {
	session := connect(t, newTestEnv(t).server, nil)
	tools, err := session.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, tool := range tools.Tools {
		if n := len(utf16.Encode([]rune(tool.Description))); n > 2048 {
			t.Errorf("the %s description has %d code units, more than 2048", tool.Name, n)
		}
	}
}

// Claude Code passes the model a result's structured content in place of its text, so by default it gets none and
// passes the text, as other clients do. --structured-content always and never decide it for every client.
func TestStructuredContentPerClient(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	for _, c := range []struct {
		setting, client string
		structured      bool
	}{
		{"", "claude-code", false},
		{StructuredAuto, "claude-code", false},
		{StructuredAuto, "codex-mcp-client", true},
		{StructuredAlways, "claude-code", true},
		{StructuredNever, "codex-mcp-client", false},
	} {
		e.server.cfg.StructuredContent = c.setting
		session := connectAs(t, e.server, c.client, nil)
		for _, params := range []*mcp.CallToolParams{
			{Name: "windows", Arguments: map[string]any{}},
			{Name: "screenshot", Arguments: map[string]any{"window": "0xa1"}},
			{Name: "act", Arguments: map[string]any{"window": "0xa1", "actions": []any{map[string]any{"action": "key", "keys": []string{"a"}}}}},
			{Name: "act", Arguments: map[string]any{"window": "0xff"}},
		} {
			res, err := session.CallTool(context.Background(), params)
			if err != nil {
				t.Fatal(err)
			}
			if (res.StructuredContent != nil) != c.structured || text(res) == "" {
				t.Errorf("%q for %s: %s structured content %v, text %q", c.setting, c.client, params.Name, res.StructuredContent, text(res))
			}
		}
	}
}

// TestProtocol runs the tools through an MCP client: the tool list, progress during a wait and cancellation.
func TestProtocol(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	var mu sync.Mutex
	var progress []string
	session := connect(t, e.server, &mcp.ClientOptions{
		ProgressNotificationHandler: func(_ context.Context, req *mcp.ProgressNotificationClientRequest) {
			mu.Lock()
			progress = append(progress, req.Params.Message)
			mu.Unlock()
		},
	})
	ctx := context.Background()
	if !strings.HasPrefix(session.InitializeResult().Instructions, "hyprmasq: see, click and type") {
		t.Error("no instructions")
	}
	tools, err := session.ListTools(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	readOnly := map[string]bool{}
	notDestructive := map[string]bool{}
	for _, tool := range tools.Tools {
		readOnly[tool.Name] = tool.Annotations != nil && tool.Annotations.ReadOnlyHint
		notDestructive[tool.Name] = tool.Annotations != nil && tool.Annotations.DestructiveHint != nil && !*tool.Annotations.DestructiveHint
		if !strings.Contains(tool.Description, "Example: {") {
			t.Errorf("%s has no example", tool.Name)
		}
	}
	// screenshot opens a socket that stays open, and so does windows when it takes a shot, so they are not read-only.
	if !reflect.DeepEqual(readOnly, map[string]bool{"windows": false, "screenshot": false, "act": false, "launch": false, "release": false, "close": false}) {
		t.Errorf("read-only tools %v", readOnly)
	}
	if !reflect.DeepEqual(notDestructive, map[string]bool{"windows": true, "screenshot": true, "act": false, "launch": true, "release": true, "close": false}) {
		t.Errorf("tools that are not destructive %v", notDestructive)
	}
	// pi shows only the tool descriptions, so they carry the rules for acting. Codex adds the instructions to every
	// description, so the rules are said once, and no sentence of the instructions is said again in a description.
	descriptions := map[string]string{}
	everything := session.InitializeResult().Instructions
	for _, tool := range tools.Tools {
		descriptions[tool.Name] = tool.Description
		everything += "\n" + tool.Description
	}
	for tool, rules := range map[string][]string{
		"act": {"Drive only windows the user named or you launched", "ask the user before anything irreversible: sending, deleting, buying, submitting",
			"Never repeat input whose delivery is partial or uncertain, or from a call that timed out", "Batch only targets visible in the shot",
			"a line break presses Enter and a tab presses Tab", "a call types about 600 characters, or 1400 with deadline_ms 30000",
			"Whether a menu is still open shows in the shot", "as clicking its opener closes it",
			"drag, clicks with modifiers, and chords holding a key other than a modifier while pressing the next need the hold capability",
			`"shot s7 = s5 + 2 changes" has s5's pixels, so keep reading coordinates from s5`, `capture "window" asks for one`},
		"windows": {"needed for drag, for clicks with modifiers, and for key chords that hold a key other than a modifier",
			"without it, the window loses the focus after each call, and what its app closes on focus loss may be gone by the next call"},
		"launch":  {"Close the windows you launch when you are done"},
		"close":   {"Close only windows you launched or the user asked you to close"},
		"release": {"an act without input only renews one that is held"},
	} {
		for _, rule := range rules {
			if !strings.Contains(descriptions[tool], rule) || strings.Count(everything, rule) != 1 {
				t.Errorf("%s does not say %q once", tool, rule)
			}
		}
	}
	for _, sentence := range regexp.MustCompile(`[.;:]\s+`).Split(session.InitializeResult().Instructions, -1) {
		for tool, description := range descriptions {
			if len(sentence) > 20 && strings.Contains(description, strings.TrimSpace(sentence)) {
				t.Errorf("the instructions and %s both say %q", tool, sentence)
			}
		}
	}
	if !strings.Contains(descriptions["windows"], "dnd (") || !strings.Contains(descriptions["act"], "drag-and-drop also dnd") {
		t.Error("windows does not describe dnd, or act does not name it")
	}
	for _, tool := range tools.Tools {
		if schema, _ := json.Marshal(tool.InputSchema); tool.Name == "act" && !strings.Contains(string(schema), "input has to end 2250 ms before the deadline") {
			t.Errorf("act schema %s", schema)
		}
	}

	res, err := session.CallTool(ctx, &mcp.CallToolParams{Name: "screenshot", Arguments: map[string]any{"window": "0xa1"}})
	if err != nil || res.IsError || len(res.Content) != 2 {
		t.Fatalf("%v %v", err, res)
	}
	// Clients that pass the model only the structured content get the summary too.
	if summary := get(t, mirrored(t, res), "summary"); summary != "shot s1 640×400 · window 0xa1 320×200" {
		t.Errorf("summary %v", summary)
	}
	if img, ok := res.Content[1].(*mcp.ImageContent); !ok || img.MIMEType != "image/png" || len(img.Data) == 0 {
		t.Errorf("image %v", res.Content[1])
	}

	release := make(chan struct{})
	e.fake.with(func() { e.fake.onWait = blockingWait(release) })
	// The wait ends once progress about it has arrived, at about a second into the wait.
	go func() {
		defer close(release)
		e.waitFor(func() bool {
			mu.Lock()
			defer mu.Unlock()
			return len(progress) >= 2 && progress[len(progress)-1] == "waiting for the window to settle"
		})
	}()
	res, err = session.CallTool(ctx, &mcp.CallToolParams{Name: "act", Meta: mcp.Meta{"progressToken": "p1"},
		Arguments: map[string]any{"window": "0xa1", "shot": "s1", "actions": []any{map[string]any{"action": "key", "keys": []string{"a"}}}}})
	if err != nil || res.IsError {
		t.Fatalf("%v %v", err, res)
	}
	mu.Lock()
	if len(progress) < 2 || progress[len(progress)-1] != "waiting for the window to settle" {
		t.Errorf("progress %q", progress)
	}
	mu.Unlock()

	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	callCtx, cancel := context.WithCancel(ctx)
	waits := len(e.fake.requestsMatching("j/hyprmasq wait"))
	go func() {
		e.waitFor(func() bool { return len(e.fake.requestsMatching("j/hyprmasq wait")) > waits })
		cancel()
	}()
	_, err = session.CallTool(callCtx, &mcp.CallToolParams{Name: "act", Arguments: map[string]any{"window": "0xa1", "shot": "s1"}})
	if !errors.Is(err, context.Canceled) {
		t.Errorf("err %v", err)
	}
	e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq cancel ") != nil })
	if len(e.fake.requestsMatching("j/hyprmasq cancel ")) == 0 {
		t.Error("the plugin's wait was not cancelled")
	}
}

func TestShutdownCancelsRunningCalls(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	handler := e.server.handle(e.server.actTool)
	done := make(chan *mcp.CallToolResult)
	go func() {
		res, _ := handler(context.Background(), &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{
			Arguments: json.RawMessage(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)}})
		done <- res
	}()
	for len(e.fake.requestsMatching("j/hyprmasq wait")) == 0 {
		time.Sleep(5 * time.Millisecond)
	}
	start := time.Now()
	e.server.Shutdown(context.Background())
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Errorf("shutdown took %v", elapsed)
	}
	res := <-done
	if got := get(t, res, "wait.ended"); got != "cancelled" {
		t.Errorf("ended %v: %s", got, text(res))
	}
	// The lease the call took is released, and the call did not renew it afterwards.
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("lease kept after the session ended")
		}
	})
	res, _ = handler(context.Background(), &mcp.CallToolRequest{Params: &mcp.CallToolParamsRaw{Arguments: json.RawMessage(`{"window":"0xa1"}`)}})
	if code := errorCode(t, res); code != "cancelled" {
		t.Errorf("a call after the end: %q", code)
	}
}

// A client that closes the server's stdin ends the session: the calls still running end without a capture or a renewed
// lease, as on a signal.
func TestStdinEndStopsCalls(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() { e.fake.onWait = blockingWait(make(chan struct{})) })
	clientRead, serverWrite := io.Pipe()
	serverRead, clientWrite := io.Pipe()
	ran := make(chan error, 1)
	go func() {
		ran <- e.server.NewMCPServer().Run(context.Background(), &mcp.IOTransport{Reader: serverRead, Writer: serverWrite})
	}()
	client := mcp.NewClient(&mcp.Implementation{Name: "test", Version: "0"}, nil)
	session, err := client.Connect(context.Background(), &mcp.IOTransport{Reader: clientRead, Writer: clientWrite}, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()
	go session.CallTool(context.Background(), &mcp.CallToolParams{Name: "act", Arguments: map[string]any{
		"window": "0xa1", "actions": []any{map[string]any{"action": "key", "keys": []string{"a"}}}}})
	if !e.waitFor(func() bool { return e.fake.requestsMatching("j/hyprmasq wait") != nil }) {
		t.Fatal("no wait")
	}
	shots, leases := len(e.fake.requestsMatching("j/hyprmasq screenshot")), len(e.fake.requestsMatching("j/hyprmasq lease"))
	clientWrite.Close()
	select {
	case <-ran:
	case <-time.After(5 * time.Second):
		t.Fatal("the session did not end")
	}
	if !e.server.stopping() {
		t.Error("the server goes on taking calls")
	}
	if got := len(e.fake.requestsMatching("j/hyprmasq screenshot")); got != shots {
		t.Errorf("%d captures after the end", got-shots)
	}
	if got := e.fake.requestsMatching("j/hyprmasq lease"); len(got) != leases {
		t.Errorf("lease requests after the end: %q", got[leases:])
	}
	if len(e.fake.requestsMatching("j/hyprmasq cancel ")) == 0 {
		t.Error("the plugin's wait was not cancelled")
	}
}

func TestRemoveStaleShots(t *testing.T) {
	e := newTestEnv(t)
	parent := t.TempDir()
	e.server.shotDir = filepath.Join(parent, strconv.Itoa(os.Getpid()))
	exited := exec.Command("true")
	if err := exited.Run(); err != nil {
		t.Fatal(err)
	}
	dead := filepath.Join(parent, strconv.Itoa(exited.Process.Pid))
	alive := filepath.Join(parent, "1")
	for _, dir := range []string{e.server.shotDir, dead, alive, filepath.Join(parent, "other")} {
		if err := os.MkdirAll(dir, 0o700); err != nil {
			t.Fatal(err)
		}
	}
	e.server.RemoveStaleShots()
	for dir, kept := range map[string]bool{e.server.shotDir: true, dead: false, alive: true, filepath.Join(parent, "other"): true} {
		if _, err := os.Stat(dir); (err == nil) != kept {
			t.Errorf("%s: kept %v", dir, err == nil)
		}
	}
}
