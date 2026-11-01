package server

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"slices"
	"strings"
	"sync"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// fakeClipboard is the plugin's agent clipboard for paste tests: clipboard set makes a text with the next serial, or
// fails from the refuseFrom-th text on, and clipboard reports a paste of the text as ended once readAfter queries have
// asked for it, or never with readAfter < 0, and as cut off before its end with truncated.
type fakeClipboard struct {
	mu         sync.Mutex
	readAfter  int
	truncated  bool
	refuseFrom int
	serial     uint64
	queries    int
	texts      []string
	events     []string
}

func (c *fakeClipboard) plugin(words []string) (string, bool) {
	if words[0] != "clipboard" {
		return "", false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(words) == 3 && words[1] == "set" {
		if c.refuseFrom > 0 && int(c.serial)+1 >= c.refuseFrom {
			return pluginError("internal", "refused"), true
		}
		text, _ := hex.DecodeString(words[2])
		c.serial, c.queries = c.serial+1, 0
		c.texts = append(c.texts, string(text))
		c.events = append(c.events, fmt.Sprintf("set %d", c.serial))
		return jsonReply(map[string]any{"ok": true, "serial": c.serial}), true
	}
	c.queries++
	read := c.readAfter >= 0 && c.queries > c.readAfter
	if read {
		c.events = append(c.events, fmt.Sprintf("read %d", c.serial))
	}
	n, complete := 0, 0
	if read {
		n = 1
		if !c.truncated {
			complete = 1
		}
	}
	return jsonReply(map[string]any{"ok": true, "serial": c.serial, "held": c.serial > 0, "asked": n, "ended": n, "complete": complete}), true
}

func pasteEnv(t *testing.T, c *fakeClipboard, xwayland bool) *testEnv {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits, w.XWayland = true, 10, xwayland })
	e.fake.onPlugin = c.plugin
	return e
}

// A paste whose text the plugin did not take sends no Shift+Insert, which would paste what the agent clipboard held
// before: the sync before it gets no answer, the sender stops there, and the result says that nothing was pasted.
func TestAPasteWhoseTextThePluginRefusesSendsNothing(t *testing.T) {
	e := pasteEnv(t, &fakeClipboard{refuseFrom: 1}, false)
	res := e.act(`{"window":"0xa1","actions":[["paste","secret"]]}`)
	if code := errorCode(t, res); code != "internal" || !strings.Contains(text(res), "nothing was pasted") {
		t.Fatalf("code %q: %s", code, text(res))
	}
	runs := readInputLog(t, e.inputLog)
	answered := slices.ContainsFunc(runs, func(r inputRun) bool { return strings.HasPrefix(r.Event, "answered") })
	stopped := slices.ContainsFunc(runs, func(r inputRun) bool { return r.Event == "sigterm" })
	if answered || !stopped {
		t.Errorf("answered %v, sigterm %v: %v", answered, stopped, runs)
	}
}

// Apps of Hyprland's XWayland paste the X server's selections, so a paste into an X11 window is refused before any
// input.
func TestPasteIsRefusedInX11Windows(t *testing.T) {
	c := &fakeClipboard{}
	e := pasteEnv(t, c, true)
	res := e.act(`{"window":"0xa1","actions":[["key","a"],["paste","secret"]]}`)
	if code := errorCode(t, res); code != "unsupported" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if inputs := e.inputs(); len(inputs) != 0 || len(c.texts) != 0 {
		t.Errorf("sent %v, set %v", inputs, c.texts)
	}
}

// A paste longer than one clipboard set pastes chunk by chunk, each made the agent clipboard only once the app has read
// the one before, or the next would take its place before the app asked for it. The chunks end at character
// boundaries and add up to the text.
func TestALongPasteSetsEachChunkAfterTheAppReadTheOneBefore(t *testing.T) {
	c := &fakeClipboard{readAfter: 3}
	e := pasteEnv(t, c, false)
	long := strings.Repeat("é", 20000) // 40000 bytes, three chunks
	quoted, _ := json.Marshal(long)
	res := e.act(`{"window":"0xa1","actions":[["paste",` + string(quoted) + `]]}`)
	if res.IsError {
		t.Fatalf("error %s", text(res))
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if strings.Join(c.texts, "") != long || len(c.texts) != 3 {
		t.Fatalf("%d chunks, joined equal %v", len(c.texts), strings.Join(c.texts, "") == long)
	}
	want := []string{"set 1", "read 1", "set 2", "read 2", "set 3", "read 3"}
	if got := slices.Compact(slices.Clone(c.events)); !slices.Equal(got, want) {
		t.Errorf("events %v, want %v", got, want)
	}
	if got := get(t, res, "actions.unread_pastes"); got != nil {
		t.Errorf("unread %v", got)
	}
}

// When no app reads a paste, as in a terminal, which pastes the primary selection on Shift+Insert, or the app closes
// its pipe before the end of the text, the batch goes on, and the result names the paste so that the agent can type the
// text instead.
func TestAPasteThatNoAppReadsWhollyIsReported(t *testing.T) {
	for _, c := range []struct {
		name string
		clip func() *fakeClipboard
	}{
		{"no app asked", func() *fakeClipboard { return &fakeClipboard{readAfter: -1} }},
		{"the app closed its pipe early", func() *fakeClipboard { return &fakeClipboard{truncated: true} }},
	} {
		t.Run(c.name, func(t *testing.T) {
			e := pasteEnv(t, c.clip(), false)
			res := e.act(`{"window":"0xa1","actions":[["paste","hello"],["key","enter"]]}`)
			if res.IsError {
				t.Fatalf("error %s", text(res))
			}
			if got := get(t, res, "actions.delivery"); got != "complete" {
				t.Errorf("delivery %v", got)
			}
			if got := get(t, res, "actions.unread_pastes"); fmt.Sprint(got) != "[0]" {
				t.Errorf("unread %v", got)
			}
			if !strings.Contains(text(res), "No app read all of the text of the paste") {
				t.Errorf("no hint: %s", text(res))
			}
		})
	}
}

// A paste that a scroll at the end of the batch follows runs in a part of its own, and the result of the batch, which
// is the scroll's, still names the paste that no app read.
func TestAnUnreadPasteBeforeAScrollIsReported(t *testing.T) {
	e := pasteEnv(t, &fakeClipboard{readAfter: -1}, false)
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, block(hypr.Rect{X: 100, Y: 50, Width: 20, Height: 10}), stripes)
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[["paste","hello"],["scroll",10,10,3]]}`)
	if res.IsError {
		t.Fatalf("error %s", text(res))
	}
	if get(t, res, "before_scroll") == nil {
		t.Fatalf("not split: %s", text(res))
	}
	if got := get(t, res, "actions.unread_pastes"); fmt.Sprint(got) != "[0]" {
		t.Errorf("unread %v: %s", got, text(res))
	}
}

// When a later part of a long paste cannot be set, the parts before it are in the app already: the error says so,
// rather than that nothing was pasted, as pasting the whole text again would insert them twice.
func TestALaterPartThatCannotBeSetSaysTheEarlierPartsWentIn(t *testing.T) {
	c := &fakeClipboard{refuseFrom: 2}
	e := pasteEnv(t, c, false)
	quoted, _ := json.Marshal(strings.Repeat("x", 40000))
	res := e.act(`{"window":"0xa1","actions":[["paste",` + string(quoted) + `]]}`)
	if code := errorCode(t, res); code != "internal" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	msg := text(res)
	if strings.Contains(msg, "nothing was pasted") || !strings.Contains(msg, "part 2 of 3") || !strings.Contains(msg, "insert its first parts twice") {
		t.Errorf("message: %s", msg)
	}
	if c.mu.Lock(); len(c.texts) != 1 {
		t.Errorf("set %d parts", len(c.texts))
	}
	c.mu.Unlock()
}
