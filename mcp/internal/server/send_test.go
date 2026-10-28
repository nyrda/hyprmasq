package server

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestBatchRunsInOneSender(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[
		{"action":"click","x":20,"y":20},{"action":"type","text":"hi there"},{"action":"key","keys":["enter"]},
		{"action":"scroll","x":40,"y":40,"dy":2},{"action":"pause","ms":10},
		{"action":"path","points":[[0,0],[100,100]]}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	// One sender checks the whole batch, including whether the keymap can type it, before it sends anything. The text
	// waits for the window's answer to the click, and at least until the click has settled.
	want := [][]string{{
		"move", "10", "10", "click", "left", "1", "-", "sync", "300", "1000", "text", "hi there", "keys", "enter", "1",
		"move", "20", "20", "wheel", "0", "2", "sleep", "10", "path", "2", "0", "0", "50", "50", "left", "-",
	}}
	if got := e.inputs(); !reflect.DeepEqual(got, want) {
		t.Errorf("inputs %q\nwant %q", got, want)
	}
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 6.0, "total": 6.0, "delivery": "complete"}) {
		t.Errorf("actions %v", got)
	}
}

// The sender types characters without a key of their own through the Compose table of the target application's
// locale, which the server reads from the window's process. When it cannot, the hint says that such characters may
// have arrived as others.
func TestComposeLocaleOfTheWindow(t *testing.T) {
	const unknownNote = "Characters without a key of their own were typed through the Compose table of en_US.UTF-8, since the window's locale is not known: "
	cases := []struct {
		name    string
		pid     int
		environ string
		exe     string
		// FAKE_INPUT_COMPOSE: what the sender typed through Compose, if anything.
		compose string
		locale  string
		hint    string
	}{
		{name: "LC_ALL first", pid: 4242, environ: "LANG=de_DE.UTF-8\x00LC_ALL=pt_BR.UTF-8\x00LC_CTYPE=fi_FI.UTF-8\x00",
			compose: "pt_BR.UTF-8:pt_BR.UTF-8", locale: "pt_BR.UTF-8"},
		{name: "empty variables are unset", pid: 4242, environ: "LC_ALL=\x00LANG=de_DE.UTF-8\x00LC_CTYPE=fi_FI.UTF-8\x00",
			compose: "fi_FI.UTF-8:fi_FI.UTF-8", locale: "fi_FI.UTF-8"},
		{name: "LANG", pid: 4242, environ: "PATH=/bin\x00LANG=de_DE.UTF-8\x00", compose: "de_DE.UTF-8:de_DE.UTF-8", locale: "de_DE.UTF-8"},
		{name: "none set", pid: 4242, environ: "PATH=/bin\x00", locale: "C"},
		{name: "no table for the locale", pid: 4242, environ: "LANG=xx_XX.UTF-8\x00", compose: "en_US.UTF-8:xx_XX.UTF-8", locale: "xx_XX.UTF-8",
			hint: "Characters without a key of their own were typed through the Compose table of en_US.UTF-8, since there is none for the window's locale xx_XX.UTF-8"},
		{name: "unreadable environment", pid: 4242, compose: "en_US.UTF-8:en_US.UTF-8",
			hint: unknownNote + "the environment of its process 4242 cannot be read"},
		{name: "no process", pid: 0, environ: "LANG=de_DE.UTF-8\x00", compose: "en_US.UTF-8:en_US.UTF-8",
			hint: unknownNote + "Hyprland names no process for the window"},
		{name: "xwayland-satellite", pid: 4242, environ: "LANG=de_DE.UTF-8\x00", exe: "/nix/store/x-xwayland-satellite-0.7/bin/.xwayland-satellite-wrapped",
			compose: "en_US.UTF-8:en_US.UTF-8", hint: unknownNote + "the window belongs to xwayland-satellite"},
		// Nothing went through Compose, so nothing could arrive as another character.
		{name: "unknown without Compose", pid: 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PID = c.pid })
			if c.environ != "" {
				e.setEnviron(c.pid, c.environ)
			}
			if c.exe != "" {
				e.setExe(c.pid, c.exe)
			}
			t.Setenv("FAKE_INPUT_COMPOSE", c.compose)
			res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"ç"}]}`)
			if res.IsError {
				t.Fatal(text(res))
			}
			runs := readInputLog(t, e.inputLog)
			locale := ""
			for i, arg := range runs[0].Argv {
				if arg == "--locale" {
					locale = runs[0].Argv[i+1]
				}
			}
			if locale != c.locale {
				t.Errorf("--locale %q in %q, want %q", locale, runs[0].Argv, c.locale)
			}
			hint, _ := get(t, res, "hint").(string)
			if c.hint == "" && strings.Contains(hint, "Compose") || !strings.Contains(hint, c.hint) {
				t.Errorf("hint %q, want %q", hint, c.hint)
			}
			if c.hint != "" && !strings.Contains(hint, ". The application may compose them differently, so look at the shot to check that they arrived as typed.") {
				t.Errorf("hint %q", hint)
			}
		})
	}

	// Batches without text leave the locale alone.
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.setEnviron(4242, "LANG=de_DE.UTF-8\x00")
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`); res.IsError {
		t.Fatal(text(res))
	}
	if runs := readInputLog(t, e.inputLog); slices.Contains(runs[0].Argv, "--locale") {
		t.Errorf("argv %q", runs[0].Argv)
	}
}

// Only hyprmasq-input knows the keys that text resolves to with the keymap, such as a dead key and Space for ^ on a
// German layout. It gets the time left for input, less the round trips' share, as its budget, and refuses a batch that
// would not end in time before it sends anything. The server reports that as it does for a batch that its own estimate
// does not fit.
func TestBatchesThatTheResolvedKeysCannotFit(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	// 40 times ^ is 80 keys, 1190 ms at their pace, while the server counts 40 keys, 590 ms.
	carets := strings.Repeat("^", 40)
	t.Setenv("FAKE_INPUT_MODE", "toolong:1190")
	res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + carets + `"}],"deadline_ms":3200}`)
	want := "the batch takes at least 1190 ms to send with the active keymap, about 1309 ms with round trips; deadline_ms 3200 leaves 950 ms for it, " +
		"since its last 2250 ms are kept for stopping input in time and for the capture"
	if code := errorCode(t, res); code != "invalid_arguments" || get(t, res, "error.message") != want || get(t, res, "actions.delivery") != "none" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if hint := get(t, res, "hint"); hint != joinHints("Give a deadline_ms of at least 5000, or split the batch over several calls.", hintNothingSent) {
		t.Errorf("hint %q", hint)
	}
	if get(t, res, "actions.failed_index") != nil {
		t.Errorf("failed index in %s", text(res))
	}
	// The sender got the time left for input as its budget, less the round trips' share.
	runs := readInputLog(t, e.inputLog)
	budget := -1
	for i, arg := range runs[0].Argv {
		if arg == "--budget" {
			budget, _ = strconv.Atoi(runs[0].Argv[i+1])
		}
	}
	if budget < 700 || budget > 950*10/11 {
		t.Errorf("budget %d in %q", budget, runs[0].Argv)
	}
	if got := e.fake.requestsMatching("j/hyprmasq wait"); got != nil {
		t.Errorf("waited: %q", got)
	}
	if got := e.fake.requestsMatching("j/hyprmasq state 0xa1"); len(got) < 2 {
		t.Errorf("states %q", got)
	}

	// Too long for no deadline_ms.
	t.Setenv("FAKE_INPUT_MODE", "toolong:40000")
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + carets + `"}]}`)
	if code := errorCode(t, res); code != "invalid_arguments" ||
		get(t, res, "hint") != joinHints("Split it over several calls: typing takes at least 15 ms a key, and characters typed through Compose take two keys or more.", hintNothingSent) {
		t.Errorf("code %q: %s", code, text(res))
	}
	// The deadline fits the batch, so waiting for the locks took the time.
	t.Setenv("FAKE_INPUT_MODE", "toolong:1190")
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + carets + `"}]}`)
	if code := errorCode(t, res); code != "deadline" ||
		!regexp.MustCompile(`^the batch takes about 1309 ms to send with the active keymap and round trips, and \d+ ms were left for it after waiting for other calls$`).MatchString(get(t, res, "error.message").(string)) ||
		!strings.HasSuffix(get(t, res, "hint").(string), hintNothingSent) {
		t.Errorf("code %q: %s", code, text(res))
	}
	// Nothing was sent, so the lease that the call took before the sender is given back.
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xa1 release"); len(got) != 3 {
		t.Errorf("releases %q", got)
	}
}

func TestHoldIsCheckedAgainAfterTheLock(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	// The user focuses a window of the same connection while the call waits for the lock.
	states := 0
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if states++; states > 1 {
				w.Caps.Hold = false
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"path","points":[[1,1],[50,50]]}]}`)
	if code := errorCode(t, res); code != "unsupported" || get(t, res, "actions.delivery") != "none" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil {
		t.Errorf("sent %q", e.inputs())
	}
}

func TestResizeDuringBatchStopsIt(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	// The window grows while the sender runs, after it has sent the first click (two of its actions).
	t.Setenv("FAKE_INPUT_MODE", "hang:2")
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if data, _ := os.ReadFile(e.inputLog); strings.Contains(string(data), `"start"`) {
				w.Width = 400
			}
		}
	})
	start := time.Now()
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":20,"y":20},{"action":"click","x":40,"y":40}]}`)
	if code := errorCode(t, res); code != "stale_shot" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	// Without the check, the batch would run until the deadline, 14 seconds later.
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Errorf("stopped after %v", elapsed)
	}
	if got := get(t, res, "actions"); !reflect.DeepEqual(got, map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0}) {
		t.Errorf("actions %v", got)
	}
	var terminated bool
	for _, r := range readInputLog(t, e.inputLog) {
		terminated = terminated || r.Event == "sigterm"
	}
	if !terminated {
		t.Error("the sender was not stopped")
	}
	if get(t, res, "shot.id") == nil || !hasImage(res) {
		t.Error("no capture after the partial batch")
	}
	if !strings.Contains(text(res), "1/2 delivered, delivery partial: actions[1] (click) may be partly applied") || !strings.Contains(text(res), "Look at the shot before repeating anything.") {
		t.Errorf("text %q", text(res))
	}

	// A batch whose later actions have no coordinates does not depend on the size.
	e.fake.with(func() { e.fake.onState = nil; e.fake.find("0xa1").Width = 320 })
	t.Setenv("FAKE_INPUT_MODE", "sleep:200")
	e.screenshot(`{"window":"0xa1"}`)
	before := len(e.fake.requestsMatching("j/hyprmasq state"))
	res = e.act(`{"window":"0xa1","shot":"s3","actions":[{"action":"click","x":20,"y":20},{"action":"type","text":"abc"}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if polled := len(e.fake.requestsMatching("j/hyprmasq state")) - before; polled > 4 {
		t.Errorf("%d state requests for a batch that does not depend on the size", polled)
	}
}

func TestResizeDuringASingleDragStopsIt(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "hang:0")
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if e.senderLogged("start") {
				w.Width = 400
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"path","points":[[5,5],[300,300]]}]}`)
	if code := errorCode(t, res); code != "stale_shot" || !e.senderLogged("sigterm") {
		t.Fatalf("code %q: %s", code, text(res))
	}
}

// A resize that stops the sender before it connected sent nothing.
func TestResizeBeforeTheSenderConnects(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "unhandled")
	e.fake.with(func() {
		e.fake.onState = func(w *fakeWindow) {
			if e.senderLogged("start") {
				w.Width = 400
			}
		}
	})
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5}]}`)
	if code := errorCode(t, res); code != "stale_shot" || get(t, res, "actions.delivery") != "none" || !strings.HasSuffix(get(t, res, "hint").(string), hintNothingSent) {
		t.Errorf("code %q: %s", code, text(res))
	}
}

func TestCancelWhileSending(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "hang")
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.senderLogged("start") })
		cancel()
	}()
	res := callTool(e.server, ctx, actOn, `{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":5,"y":5},{"action":"type","text":"abc"}]}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := get(t, res, "wait.ended"); got != "cancelled" {
		t.Errorf("ended %v", got)
	}
	if got := get(t, res, "actions.delivery"); got != "partial" {
		t.Errorf("delivery %v", got)
	}
	var terminated bool
	for _, r := range readInputLog(t, e.inputLog) {
		terminated = terminated || r.Event == "sigterm"
	}
	if !terminated {
		t.Error("the sender got no SIGTERM")
	}
	if got := e.fake.requestsMatching("j/hyprmasq wait"); got != nil {
		t.Errorf("waited after the cancellation: %q", got)
	}
}

// A sender that SIGTERM kills before it handles signals has not connected, so nothing was sent.
func TestSenderStoppedBeforeItConnects(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	t.Setenv("FAKE_INPUT_MODE", "unhandled")
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return e.senderLogged("start") })
		cancel()
	}()
	res := callTool(e.server, ctx, actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	if res.IsError || get(t, res, "wait.ended") != "cancelled" || get(t, res, "actions.delivery") != "none" {
		t.Errorf("cancelled: %s", text(res))
	}

	start := time.Now()
	res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}],"deadline_ms":3500}`)
	if code := errorCode(t, res); code != "deadline" || get(t, res, "actions.delivery") != "none" || !strings.HasSuffix(get(t, res, "hint").(string), hintNothingSent) {
		t.Errorf("code %q: %s", code, text(res))
	}
	if elapsed := time.Since(start); elapsed < inputTime(3500*time.Millisecond) || elapsed > 3500*time.Millisecond {
		t.Errorf("took %v", elapsed)
	}
}

// The deadline stops a sender early enough that it has its CANCEL_MS to report before the capture's reserve starts, so
// the call ends within its deadline.
func TestDeadlineStopsTheSenderInTime(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	t.Setenv("FAKE_INPUT_MODE", fmt.Sprintf("hang:1:%d", senderCancel.Milliseconds()))
	start := time.Now()
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]},{"action":"type","text":"abc"}],"deadline_ms":4000}`)
	elapsed := time.Since(start)
	if code := errorCode(t, res); code != "deadline" || get(t, res, "actions.delivery") != "partial" || get(t, res, "actions.failed_index") != 1.0 || !hasImage(res) {
		t.Fatalf("code %q: %s", code, text(res))
	}
	var stopped time.Time
	for _, r := range readInputLog(t, e.inputLog) {
		if r.Event == "sigterm" {
			stopped = time.Unix(0, r.Time)
		}
	}
	sendBy := start.Add(4000*time.Millisecond - captureReserve)
	if stopped.IsZero() || stopped.After(sendBy.Add(-senderCancel)) {
		t.Errorf("SIGTERM %v after the start, less than the sender's %v before the capture's reserve", stopped.Sub(start), senderCancel)
	}
	if captured := e.fake.timeOf("j/hyprmasq screenshot"); captured.After(sendBy) {
		t.Errorf("captured %v after the start, in the capture's reserve", captured.Sub(start))
	}
	if elapsed > 4000*time.Millisecond {
		t.Errorf("took %v, more than deadline_ms", elapsed)
	}
}

func TestSendLockSerializesSenders(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	other := e.newServer()
	t.Setenv("FAKE_INPUT_MODE", "sleep:300")
	var wg sync.WaitGroup
	for _, c := range []struct {
		s      *Server
		window string
	}{{e.server, "0xa1"}, {other, "0xb2"}} {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if res := callTool(c.s, context.Background(), actOn, fmt.Sprintf(`{"window":%q,"actions":[{"action":"key","keys":["a"]}]}`, c.window)); res.IsError {
				t.Error(text(res))
			}
		}()
	}
	wg.Wait()
	runs := readInputLog(t, e.inputLog)
	if len(runs) != 4 {
		t.Fatalf("runs %v", runs)
	}
	for i, want := range []string{"start", "exit", "start", "exit"} {
		if runs[i].Event != want {
			t.Errorf("senders overlap: %v", runs)
			break
		}
	}
}

func TestSenderFailures(t *testing.T) {
	cases := []struct {
		name, mode, actions, code, delivery string
		failed                              any
		keys                                any
		captured                            bool
		hint                                string
	}{
		{"cannot type", "fail:cannot_type:0:none:0", `[{"action":"type","text":"é"}]`, "cannot_type", "none", 0.0, nil, true, hintCannotType},
		{"unknown key", "fail:invalid_arguments:0:none:0", `[{"action":"key","keys":["nosuchkey"]}]`, "invalid_arguments", "none", 0.0, nil, true, hintNothingSent},
		{"input paused", "fail:input_paused:2:partial:14", `[{"action":"click","x":5,"y":5},{"action":"type","text":"hello world, this is long"}]`, "input_paused", "partial", 1.0, 14.0, true,
			pausedError("fake input_paused", "", true).Hint},
		{"unconfirmed", "fail:unconfirmed:0:uncertain:0", `[{"action":"key","keys":["a"]}]`, "unconfirmed", "uncertain", 0.0, nil, true, hintUncertain},
		{"connect failed", "fail:connect_failed:0:none:0", `[{"action":"key","keys":["a"]}]`, "connect_failed", "none", 0.0, nil, true, "Retry once"},
		// The sender refuses points outside the pointer's regions, which end at the monitor's edges.
		{"outside the regions", "fail:invalid_arguments:1:none:0", `[{"action":"key","keys":["a"]},{"action":"click","x":5,"y":5}]`, "out_of_bounds", "none", 1.0, nil, true,
			hintOutsideRegions},
		{"wheel", "fail:invalid_arguments:1:none:0", `[{"action":"scroll","x":5,"y":5,"dy":1}]`, "invalid_arguments", "none", 0.0, nil, true, "Fix the action"},
		// Something other than this server sent the sender SIGTERM.
		{"killed", "fail:cancelled:1:partial:3", `[{"action":"key","keys":["a"]},{"action":"type","text":"hello"}]`, "cancelled", "partial", 1.0, 3.0, true, "Look at the shot"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			t.Setenv("FAKE_INPUT_MODE", c.mode)
			res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s,"capture":"window"}`, c.actions))
			if code := errorCode(t, res); code != c.code {
				t.Fatalf("code %q: %s", code, text(res))
			}
			if got := get(t, res, "actions.delivery"); got != c.delivery {
				t.Errorf("delivery %v", got)
			}
			if got := get(t, res, "actions.failed_index"); got != c.failed {
				t.Errorf("failed index %v", got)
			}
			if got := get(t, res, "actions.progress.keys"); got != c.keys {
				t.Errorf("keys %v", got)
			}
			if hasImage(res) != c.captured || (get(t, res, "shot") != nil) != c.captured {
				t.Errorf("captured %v: %s", hasImage(res), text(res))
			}
			hint := get(t, res, "hint").(string)
			if !containsHint(hint, c.hint) {
				t.Errorf("hint %q", hint)
			}
			if c.delivery != "none" && strings.Contains(strings.ToLower(hint), "retry") {
				t.Errorf("hint invites repeating sent input: %q", hint)
			}
			if !strings.HasPrefix(text(res), "error "+c.code+": ") {
				t.Errorf("text %q", text(res))
			}
			if got := afterBatch(t, e.fake.requestsMatching("j/hyprmasq wait")); got != nil {
				t.Errorf("waited after a failure: %q", got)
			}
		})
	}
}

// An input_paused says why the plugin paused the input, from its state, and its hint invites a retry only when nothing
// was delivered. TestWording has what pausedError says for each reason.
func TestPauseReasons(t *testing.T) {
	// "" is a plugin that does not tell.
	for _, reason := range []string{"user_button", "user_key", "x11_unresponsive", "x11_focus", ""} {
		for _, mode := range []string{"fail:input_paused:0:none:0", "fail:input_paused:1:partial:3"} {
			t.Run(reason+" "+mode, func(t *testing.T) {
				e := newTestEnv(t)
				e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.LastPause = reason })
				e.screenshot(`{"window":"0xa1"}`)
				t.Setenv("FAKE_INPUT_MODE", mode)
				res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"key","keys":["a"]},{"action":"type","text":"hello"}],"capture":"window"}`)
				if code := errorCode(t, res); code != "input_paused" {
					t.Fatalf("code %q: %s", code, text(res))
				}
				delivered := strings.Contains(mode, "partial")
				want := pausedError("fake input_paused", reason, delivered)
				if got := get(t, res, "error.message"); got != want.Message {
					t.Errorf("message %q", got)
				}
				// The hint goes on with what the delivery needs: that nothing was sent, or to look at the shot first.
				delivery := map[bool]string{false: "none", true: "partial"}[delivered]
				full := finalHint(want, &actOut{Actions: &actionsOut{Total: 2, Delivery: delivery}, Shot: &shotOut{}})
				if hint := get(t, res, "hint").(string); !startsWithHint(hint, want.Hint) || hint != full {
					t.Errorf("hint %q", hint)
				}
			})
		}
	}
}

// Input can close its window, as a click on a dialog's button or ctrl+q does. The sender stops when the connection
// ends and reports how far it got, and the window is gone by the time the server looks again.
func TestWindowClosedBySentInput(t *testing.T) {
	const clickPause = `[{"action":"click","x":5,"y":5},{"action":"pause","ms":500}]`
	for _, c := range []struct {
		name, mode, actions string
		// Whether the window closes once the sender has exited.
		closes bool
		// The error and its message, or "" for a result without an error.
		code, message string
		want          map[string]any
	}{
		// The sender stops at the pause, which needs no window.
		{"pause after the input", "fail:unconfirmed:2:partial:0", clickPause, true, "", "",
			map[string]any{"done": 2.0, "total": 2.0, "delivery": "complete"}},
		{"input after a pause", "fail:unconfirmed:2:partial:0", `[{"action":"click","x":5,"y":5},{"action":"pause","ms":500},{"action":"type","text":"more"}]`, true,
			"window_gone", "window 0xa1 closed after actions[0] (click) and actions[1] (pause) were delivered, while actions[2] (type) was sent",
			map[string]any{"done": 2.0, "total": 3.0, "delivery": "partial", "failed_index": 2.0}},
		{"input after the close", "fail:unconfirmed:1:partial:0", `[{"action":"key","keys":["ctrl+q"]},{"action":"type","text":"more"}]`, true,
			"window_gone", "window 0xa1 closed after actions[0] (key) was delivered, while actions[1] (type) was sent",
			map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0}},
		{"closed by the first action", "fail:unconfirmed:0:uncertain:0", `[{"action":"key","keys":["ctrl+q"]},{"action":"type","text":"more"}]`, true,
			"window_gone", "window 0xa1 closed while actions[0] (key) was sent",
			map[string]any{"done": 0.0, "total": 2.0, "delivery": "uncertain", "failed_index": 0.0}},
		// Another sender that takes over the socket ends the connection too, but the window stays, so the pause was cut short.
		{"taken over during a pause", "fail:unconfirmed:2:partial:0", clickPause, false, "unconfirmed", "actions[1] (pause): fake unconfirmed",
			map[string]any{"done": 1.0, "total": 2.0, "delivery": "partial", "failed_index": 1.0}},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200)
			e.screenshot(`{"window":"0xa1"}`)
			t.Setenv("FAKE_INPUT_MODE", c.mode)
			e.fake.with(func() {
				e.fake.onPlugin = func(words []string) (string, bool) {
					if c.closes && e.senderLogged("exit") {
						e.fake.removeWindow("0xa1")
					}
					return "", false
				}
			})
			res := e.act(`{"window":"0xa1","shot":"s1","actions":` + c.actions + `}`)
			if got := get(t, res, "actions"); !reflect.DeepEqual(got, c.want) {
				t.Errorf("actions %v", got)
			}
			if c.code == "" {
				if res.IsError || get(t, res, "error") != nil || !strings.HasPrefix(text(res), "2/2 delivered · window closed") {
					t.Errorf("result: %s", text(res))
				}
			} else if code := errorCode(t, res); code != c.code || get(t, res, "error.message") != c.message {
				t.Errorf("code %q: %s", code, text(res))
			}
			if !c.closes {
				return
			}
			if got := get(t, res, "wait.ended"); got != "window_closed" {
				t.Errorf("ended %v", got)
			}
			if hasImage(res) || get(t, res, "shot") != nil || get(t, res, "capture_error") != nil {
				t.Errorf("tried to capture: %s", text(res))
			}
			if c.code == "" {
				// The input opened no window, so the result is only that the window closed.
				newWindows, _ := get(t, res, "new_windows").([]any)
				if text(res) != "2/2 delivered · window closed\n"+closedHint(nil) || len(newWindows) != 0 {
					t.Errorf("result %q", text(res))
				}
				return
			}
			// The hint names the actions that were delivered, so that they are not sent again, and nothing else: the
			// window is gone, so there is no shot to look at.
			var raw []json.RawMessage
			if err := json.Unmarshal([]byte(c.actions), &raw); err != nil {
				t.Fatal(err)
			}
			batch, err := parseActions(raw)
			if err != nil {
				t.Fatal(err)
			}
			te := closedWhileSent("0xa1", batch, int(c.want["done"].(float64)), int(c.want["failed_index"].(float64)))
			want := finalHint(te, &actOut{Actions: &actionsOut{Total: len(batch), Delivery: c.want["delivery"].(string)}})
			if hint := get(t, res, "hint").(string); !startsWithHint(hint, te.Hint) || hint != want || strings.Contains(hint, "Retry") {
				t.Errorf("hint %q", hint)
			}
			if got := afterBatch(t, e.fake.requestsMatching("j/hyprmasq wait")); got != nil {
				t.Errorf("waited: %q", got)
			}
		})
	}
}
