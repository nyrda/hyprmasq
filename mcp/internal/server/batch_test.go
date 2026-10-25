package server

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestPointsLeftOfOrAboveTheWindow(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.PopupBox = &hypr.Rect{X: -20, Y: -10, Width: 340, Height: 210} })
	e.screenshot(`{"window":"0xa1"}`)
	for _, c := range []struct{ actions, mention string }{
		{`[{"action":"click","x":10,"y":30}]`, "actions[0] (click): 10,30 of shot s1"},
		{`[{"action":"type","text":"a"},{"action":"scroll","x":50,"y":10,"dy":1}]`, "actions[1] (scroll): 50,10 of shot s1"},
		{`[{"action":"drag","from":[50,30],"to":[30,30]}]`, "actions[0] (drag): to 30,30 of shot s1"},
	} {
		res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s}`, c.actions))
		if code := errorCode(t, res); code != "out_of_bounds" || !strings.Contains(text(res), c.mention) {
			t.Fatalf("code %q: %s", code, text(res))
		}
		if hint := get(t, res, "hint"); hint != joinHints(hintUnreachable, hintNothingSent) {
			t.Errorf("hint %q", hint)
		}
		if get(t, res, "actions.delivery") != "none" || !hasImage(res) {
			t.Errorf("result %s", text(res))
		}
	}
	if got := e.inputs(); got != nil {
		t.Errorf("sent %q", got)
	}
	// The window's own left edge is reachable.
	if res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":40,"y":20}],"capture":"none"}`); res.IsError {
		t.Fatal(text(res))
	}
	if got := e.inputs(); !reflect.DeepEqual(got, [][]string{{"move", "0", "0", "click", "left", "1", "-"}}) {
		t.Errorf("inputs %q", got)
	}
}

func TestValidationSendsNothing(t *testing.T) {
	cases := []struct {
		name, actions, code, mention string
		noHold                       bool
	}{
		{"unknown action", `[{"action":"tap","x":1,"y":1}]`, "invalid_arguments", "actions[0].action", false},
		{"unknown field", `[{"action":"click","x":1,"y":1,"z":3}]`, "invalid_arguments", "actions[0].z", false},
		{"wrong type", `[{"action":"click","x":"1","y":1}]`, "invalid_arguments", "actions[0].x", false},
		{"count", `[{"action":"click","x":1,"y":1,"count":4}]`, "invalid_arguments", "actions[0].count", false},
		{"count of an alias", `[{"action":"triple_click","x":1,"y":1,"count":2}]`, "invalid_arguments", "actions[0].count is not a field of triple_click", false},
		{"alias without a point", `[{"action":"double_click"}]`, "invalid_arguments", "actions[0].x is missing", false},
		{"modifier", `[{"action":"click","x":1,"y":1,"modifiers":["hyper"]}]`, "invalid_arguments", "hyper", false},
		{"repeat", `[{"action":"key","keys":["a"],"repeat":501}]`, "invalid_arguments", "actions[0].repeat", false},
		{"empty key", `[{"action":"key","keys":["ctrl+"]}]`, "invalid_arguments", "plus", false},
		{"pause", `[{"action":"pause","ms":5001}]`, "invalid_arguments", "actions[0].ms", false},
		{"notch", `[{"action":"scroll","x":1,"y":1,"dy":1.5}]`, "invalid_arguments", "notch", false},
		{"later action", `[{"action":"type","text":"a"},{"action":"click","x":700,"y":1}]`, "out_of_bounds", "actions[1] (click)", false},
		{"bottom edge", `[{"action":"click","x":1,"y":400}]`, "out_of_bounds", "1,400", false},
		{"drag target", `[{"action":"drag","from":[1,1],"to":[1,-1]}]`, "out_of_bounds", "to 1,-1", false},
		{"drag without hold", `[{"action":"drag","from":[1,1],"to":[5,5]}]`, "unsupported", "hold", true},
		{"modifiers without hold", `[{"action":"click","x":1,"y":1},{"action":"click","x":1,"y":1,"modifiers":["ctrl"]}]`, "unsupported", "actions[1] (click) with modifiers needs the hold capability", true},
		{"chord without hold", `[{"action":"key","keys":["a","b"]}]`, "unsupported", "actions[0] (key) holding a while it presses b needs the hold capability, which window 0xa1 lacks", true},
		{"held key after a modifier", `[{"action":"key","keys":["enter"]},{"action":"key","keys":["ctrl+Shift_L","space","x"]}]`, "unsupported",
			"actions[1] (key) holding space while it presses x needs the hold capability", true},
		{"nul in text", `[{"action":"type","text":"a\u0000b"}]`, "invalid_arguments", "actions[0].text", false},
		{"nul in key", `[{"action":"key","keys":["a\u0000"]}]`, "invalid_arguments", "actions[0].keys", false},
		{"notches", `[{"action":"scroll","x":1,"y":1,"dy":-101}]`, "invalid_arguments", "at most 100 wheel notches", false},
		{"chord", `[{"action":"key","keys":["ctrl+alt+shift+super+a+b+c+d+e+f"]}]`, "invalid_arguments", "at most 9 keys", false},
		{"key name", `[{"action":"key","keys":["` + strings.Repeat("x", 64) + `"]}]`, "invalid_arguments", "longer than 63 bytes", false},
		{"text", `[{"action":"type","text":"` + strings.Repeat("x", 40000) + `"},{"action":"type","text":"` + strings.Repeat("y", 30000) + `"}]`,
			"invalid_arguments", "actions[1].text", false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			e := newTestEnv(t)
			e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Caps.Hold = !c.noHold })
			e.screenshot(`{"window":"0xa1"}`)
			res := e.act(fmt.Sprintf(`{"window":"0xa1","shot":"s1","actions":%s}`, c.actions))
			if code := errorCode(t, res); code != c.code {
				t.Fatalf("code %q, want %q: %s", code, c.code, text(res))
			}
			if !strings.Contains(text(res), c.mention) {
				t.Errorf("%q does not mention %q", text(res), c.mention)
			}
			if got := get(t, res, "actions.delivery"); got != "none" {
				t.Errorf("delivery %v", got)
			}
			if got := e.inputs(); got != nil {
				t.Errorf("sent %q", got)
			}
			if got := e.fake.requestsMatching("j/hyprmasq wait"); got != nil {
				t.Errorf("waited: %q", got)
			}
			if got := e.fake.requestsMatching("j/hyprmasq lease"); got != nil {
				t.Errorf("leased: %q", got)
			}
		})
	}
}

// Without hold, the user's own input to the app may take the keyboard back between the frames of a chord, after which
// the plugin enters the window again with no keys held, but with the modifier state. So chords that hold only modifiers
// before their last key need no hold.
func TestChordsOfModifiersNeedNoHold(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Caps.Hold = false })
	for _, keys := range []string{`["ctrl","shift","t"]`, `["Shift_L","a"]`, `["iso_level3_shift","q"]`, `["ctrl","shift"]`, `["ctrl+plus"]`, `["a"]`} {
		res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":` + keys + `,"repeat":2}],"capture":"none"}`)
		if res.IsError {
			t.Errorf("%s: %s", keys, text(res))
		}
	}
	if got := len(e.inputs()); got != 6 {
		t.Errorf("%d senders", got)
	}
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["ctrl","a","c"]}]}`)
	if code := errorCode(t, res); code != "unsupported" || get(t, res, "hint") != joinHints(hintChordHoldMissing, hintNothingSent) {
		t.Errorf("code %q: %s", code, text(res))
	}
}

func TestTranslate(t *testing.T) {
	identity := func(x, y float64) (float64, float64) { return x/2 - 1, y/2 + 0.25 }
	cases := []struct {
		action string
		want   [][]string
	}{
		{`{"action":"click","x":10,"y":20}`, [][]string{{"move", "4", "10.25"}, {"click", "left", "1", "-"}}},
		{`{"action":"click","x":10,"y":20,"button":"right","count":2,"modifiers":["control","shift"]}`, [][]string{{"move", "4", "10.25"}, {"click", "right", "2", "ctrl+shift"}}},
		{`{"action":"click","x":1,"y":1,"modifiers":["ctrl+alt"]}`, [][]string{{"move", "-0.5", "0.75"}, {"click", "left", "1", "ctrl+alt"}}},
		{`{"action":"triple_click","x":10,"y":20}`, [][]string{{"move", "4", "10.25"}, {"click", "left", "3", "-"}}},
		{`{"action":"double_click","x":10,"y":20,"modifiers":["shift"]}`, [][]string{{"move", "4", "10.25"}, {"click", "left", "2", "shift"}}},
		{`{"action":"left_click","x":10,"y":20}`, [][]string{{"move", "4", "10.25"}, {"click", "left", "1", "-"}}},
		{`{"action":"right_click","x":10,"y":20}`, [][]string{{"move", "4", "10.25"}, {"click", "right", "1", "-"}}},
		{`{"action":"middle_click","x":10,"y":20}`, [][]string{{"move", "4", "10.25"}, {"click", "middle", "1", "-"}}},
		{`{"action":"move","x":3,"y":5}`, [][]string{{"move", "0.5", "2.75"}}},
		{`{"action":"drag","from":[0,0],"to":[10,10],"button":"middle","modifiers":["super"]}`, [][]string{{"drag", "-1", "0.25", "4", "5.25", "middle", "super"}}},
		{`{"action":"scroll","x":2,"y":2,"dy":3}`, [][]string{{"move", "0", "1.25"}, {"wheel", "0", "3"}}},
		{`{"action":"scroll","x":2,"y":2,"dx":-40.5,"unit":"px"}`, [][]string{{"move", "0", "1.25"}, {"scroll", "-40.5", "0"}}},
		{`{"action":"key","keys":["Ctrl+Shift","T"],"repeat":3}`, [][]string{{"keys", "ctrl+shift+T", "3"}}},
		{`{"action":"key","keys":["Return"]}`, [][]string{{"keys", "enter", "1"}}},
		{`{"action":"key","keys":["ctrl","+"]}`, [][]string{{"keys", "ctrl+plus", "1"}}},
		{`{"action":"key","keys":["F12","XF86AudioMute","del","cmd"]}`, [][]string{{"keys", "f12+XF86AudioMute+delete+super", "1"}}},
		{`{"action":"type","text":"<enter> \\n literal"}`, [][]string{{"text", "<enter> \\n literal"}}},
		{`{"action":"pause","ms":250}`, [][]string{{"sleep", "250"}}},
	}
	for _, c := range cases {
		a, err := parseAction(json.RawMessage(c.action), "actions[0]")
		if err != nil {
			t.Errorf("%s: %v", c.action, err)
			continue
		}
		var got [][]string
		for _, step := range translate(0, a, identity) {
			got = append(got, step.Argv)
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("%s: %q, want %q", c.action, got, c.want)
		}
	}
}

// A click or drag that more actions follow gets a sync after it, which belongs to the next action. It lasts until keys
// and text after the click come 300 ms after its end, counted with the sender's 50 ms between actions, whose first one
// the sync replaces, and only for the first keys after each click.
func TestClickSettle(t *testing.T) {
	const click, typed, key = `{"action":"click","x":1,"y":1}`, `{"action":"type","text":"a b"}`, `{"action":"key","keys":["enter"]}`
	for _, c := range []struct {
		batch string
		want  []string
	}{
		{click + "," + typed, []string{"0 move", "0 click", "1 sync 300 1000", "1 text"}},
		{click + `,{"action":"pause","ms":100},` + typed, []string{"0 move", "0 click", "1 sync 150 1000", "1 sleep 100", "2 text"}},
		{click + `,{"action":"pause","ms":200},` + typed, []string{"0 move", "0 click", "1 sync 50 1000", "1 sleep 200", "2 text"}},
		{click + `,{"action":"pause","ms":300},` + typed, []string{"0 move", "0 click", "1 sync 0 1000", "1 sleep 300", "2 text"}},
		{click + `,{"action":"move","x":2,"y":2},` + key, []string{"0 move", "0 click", "1 sync 250 1000", "1 move", "2 keys"}},
		// A scroll is two sender actions, each with its gap.
		{click + `,{"action":"scroll","x":1,"y":1,"dy":1},{"action":"pause","ms":100},` + key, []string{"0 move", "0 click", "1 sync 50 1000", "1 move", "1 wheel", "2 sleep 100", "3 keys"}},
		{click + "," + typed + "," + key, []string{"0 move", "0 click", "1 sync 300 1000", "1 text", "2 keys"}},
		{click + "," + click + "," + key, []string{"0 move", "0 click", "1 sync 0 1000", "1 move", "1 click", "2 sync 300 1000", "2 keys"}},
		{`{"action":"drag","from":[1,1],"to":[5,5]},` + key, []string{"0 drag", "1 sync 300 1000", "1 keys"}},
		{`{"action":"click","x":1,"y":1,"count":2},{"action":"scroll","x":1,"y":1,"dy":1}`, []string{"0 move", "0 click", "1 sync 0 1000", "1 move", "1 wheel"}},
		{`{"action":"scroll","x":1,"y":1,"dy":1},` + key, []string{"0 move", "0 wheel", "1 keys"}},
		{key + "," + typed, []string{"0 keys", "1 text"}},
		{typed + "," + click, []string{"0 text", "1 move", "1 click"}},
	} {
		var raw []json.RawMessage
		if err := json.Unmarshal([]byte("["+c.batch+"]"), &raw); err != nil {
			t.Fatal(err)
		}
		batch, err := parseActions(raw)
		if err != nil {
			t.Fatal(err)
		}
		var got []string
		for _, step := range translateBatch(batch, &shot{Scale: 1}) {
			got = append(got, strconv.Itoa(step.Batch)+" "+step.Argv[0])
			if step.Argv[0] == "sleep" || step.Argv[0] == "sync" {
				got[len(got)-1] += " " + strings.Join(step.Argv[1:], " ")
			}
		}
		if !reflect.DeepEqual(got, c.want) {
			t.Errorf("[%s]: %q, want %q", c.batch, got, c.want)
		}
	}
}

func TestMinSendTime(t *testing.T) {
	steps := func(argvs ...[]string) []inputStep {
		var out []inputStep
		for _, argv := range argvs {
			out = append(out, inputStep{Argv: argv})
		}
		return out
	}
	for _, c := range []struct {
		steps []inputStep
		ms    int64
	}{
		{steps([]string{"move", "1", "2"}, []string{"click", "left", "1", "-"}), 100},
		{steps([]string{"click", "left", "2", "ctrl"}), 190},
		{steps([]string{"drag", "0", "0", "300", "400", "left", "-"}), 620},
		{steps([]string{"drag", "0", "0", "5", "0", "left", "shift"}), 340},
		// Five notches, 20 ms apart and 20 ms after the last, which XWayland needs before the sender's end leaves.
		{steps([]string{"wheel", "2", "-3"}), 100},
		// A smooth scroll gets a wheel's gap after it.
		{steps([]string{"scroll", "0", "-40.5"}), 20},
		// A chord with modifiers presses its key 10 ms after them, holds it 15 ms and repeats 15 ms apart. A key alone
		// is held 5 ms once, or repeated with 1 ms gaps, also through keymap Shift.
		{steps([]string{"keys", "ctrl+l", "3"}), 105},
		{steps([]string{"keys", "right", "100"}), 99},
		{steps([]string{"keys", "A", "2"}), 1},
		{steps([]string{"text", "aé\r\nc"}), 50},
		{steps([]string{"sleep", "250"}, []string{"keys", "enter", "1"}), 305},
		// A sync takes the place of the gap after the action before it, and lasts at least its MIN.
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "300", "1000"}, []string{"keys", "enter", "1"}), 355},
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "100", "1000"}, []string{"keys", "enter", "1"}), 155},
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "20", "1000"}, []string{"keys", "enter", "1"}), 105},
		{steps([]string{"click", "left", "1", "-"}, []string{"sync", "0", "1000"}, []string{"keys", "enter", "1"}), 105},
	} {
		if got := minSendTime(c.steps).Milliseconds(); got != c.ms {
			t.Errorf("%v: %d ms, want %d", c.steps, got, c.ms)
		}
	}
	// At the largest deadline, the hint for input that the deadline cut off no longer suggests a larger one; see TestWording.
	if hint := asToolError(deadlineWhileSending("partial", maxDeadline)).Hint; hint == asToolError(deadlineWhileSending("partial", maxDeadline-time.Millisecond)).Hint {
		t.Errorf("at the largest deadline: %q", hint)
	}
}

// A batch that cannot be sent within the deadline is refused before anything is sent, instead of being cut off.
func TestBatchesThatTheDeadlineCannotFit(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	for _, c := range []struct{ args, message, hint string }{
		{`"actions":[{"action":"key","keys":["a"]}],"deadline_ms":1000`,
			"at least 5 ms to send, about 5 ms with round trips; deadline_ms 1000 leaves 0 ms for it, since its last 2250 ms are kept for stopping input in time and for the capture",
			"Give a deadline_ms of at least 4000"},
		// The most presses of a chord with modifiers fit the longest deadline, and of keys alone four times as many do not.
		{`"actions":[{"action":"key","keys":["ctrl","right"],"repeat":500}]`, "at least 19985 ms to send, about 21983 ms with round trips; deadline_ms 15000 leaves 12750 ms for it",
			"Give a deadline_ms of at least 29000"},
		{`"actions":[` + strings.Repeat(`{"action":"key","keys":["right"],"repeat":500},`, 54) + `{"action":"key","keys":["left"],"repeat":500}],"deadline_ms":30000`,
			"at least 30145 ms to send", "Split it over several calls: repeated plain keys have 1 ms gaps, and chords with modifiers 40 ms."},
		// Input ends early enough for a sender that the deadline stops to report before the capture.
		{`"actions":[{"action":"key","keys":["a"]}],"deadline_ms":2000`, "deadline_ms 2000 leaves 0 ms for it", "Give a deadline_ms of at least 4000"},
		{`"actions":[{"action":"type","text":"` + strings.Repeat("x", 1000) + `"}]`, "at least 14990 ms to send, about 16489 ms with round trips; deadline_ms 15000 leaves 12750 ms for it",
			"Give a deadline_ms of at least 22000"},
		// Text that fits at 15 ms a character, but not with the round trips, which took about 15.5 ms a character in the VM.
		{`"actions":[{"action":"type","text":"` + strings.Repeat("x", 800) + `"}]`, "at least 11990 ms to send, about 13189 ms with round trips; deadline_ms 15000 leaves 12750 ms for it",
			"Give a deadline_ms of at least 19000"},
		{`"actions":[{"action":"type","text":"` + strings.Repeat("x", 2000) + `"}],"deadline_ms":30000`, "at least 29990 ms", "one call types up to about 1400 characters"},
	} {
		res := e.act(`{"window":"0xa1",` + c.args + `}`)
		if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), c.message) || !strings.Contains(get(t, res, "hint").(string), c.hint) {
			t.Errorf("code %q: %s", code, text(res))
		}
		if get(t, res, "actions.delivery") != "none" || hasImage(res) {
			t.Errorf("%s", text(res))
		}
	}

	// Waiting for another window's input left too little time.
	sendLock, err := acquireLock(context.Background(), e.server.sendLockPath())
	if err != nil {
		t.Fatal(err)
	}
	time.AfterFunc(2*time.Second, sendLock.release)
	res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"` + strings.Repeat("x", 100) + `"}],"deadline_ms":5000}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "takes about 1639 ms to send") || get(t, res, "actions.delivery") != "none" {
		t.Errorf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil || e.fake.requestsMatching("j/hyprmasq lease") != nil {
		t.Errorf("sent %q", e.inputs())
	}
}

// The deadlines that hints suggest, and the character counts of the act description, pass the check.
func TestSuggestedDeadlinesFit(t *testing.T) {
	typing := func(n int) []inputStep { return []inputStep{{Argv: []string{"text", strings.Repeat("x", n)}}} }
	for _, deadline := range []time.Duration{defaultDeadline, maxDeadline} {
		if n := typedWithin(deadline); fitsDeadline(typing(n), deadline) != nil {
			t.Errorf("%d characters do not fit in %v", n, deadline)
		}
	}
	for _, steps := range [][]inputStep{{{Argv: []string{"keys", "a", "1"}}}, typing(1000), typing(1300), {{Argv: []string{"sleep", "5000"}}}} {
		if enough := deadlineFor(minSendTime(steps)); fitsDeadline(steps, enough) != nil {
			t.Errorf("%v does not fit in the deadline_ms %d suggested for it", steps[0].Argv[0], enough.Milliseconds())
		}
	}
}

// Text without a line break opens no window, so the act does not wait for one after its wait.
func TestTypingWaitsForNoNewWindow(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.NewWindowGrace = 3 * time.Second
	e.window("0xa1", "1a", 320, 200)
	start := time.Now()
	if res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"hello"},{"action":"pause","ms":10}]}`); res.IsError {
		t.Fatal(text(res))
	}
	if took := time.Since(start); took > 2*time.Second {
		t.Errorf("typing took %v, as if it waited for new windows", took)
	}
	if !opensWindows([]action{{Kind: "type", Text: "hello\n"}}) || !opensWindows([]action{{Kind: "click"}}) || !opensWindows([]action{{Kind: "key"}}) ||
		opensWindows([]action{{Kind: "type", Text: "a\tb"}, {Kind: "scroll"}, {Kind: "move"}, {Kind: "pause"}}) {
		t.Error("opensWindows")
	}
}
