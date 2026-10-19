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

func TestLeaseFollowsInput(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	leases := func() int { return len(e.fake.requestsMatching("j/hyprmasq lease 0xa1 6")) }
	held := func() bool {
		var held bool
		e.fake.with(func() { held = w.Lease != nil })
		return held
	}

	// A lease keeps only the focus that input gives, so a call without input starts none.
	res := e.act(`{"window":"0xa1","shot":"s1"}`)
	if res.IsError || leases() != 0 || held() || get(t, res, "lease.held") != false {
		t.Fatalf("leases %d: %s", leases(), text(res))
	}
	// Input takes it before it is sent and renews it after the capture.
	if res = e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`); res.IsError || leases() != 2 || !strings.Contains(text(res), "lease 60 s") {
		t.Fatalf("leases %d: %s", leases(), text(res))
	}
	// A call without input renews a lease that is held.
	if res = e.act(`{"window":"0xa1","shot":"s1"}`); res.IsError || leases() != 3 || !strings.Contains(text(res), "lease 60 s") {
		t.Fatalf("leases %d: %s", leases(), text(res))
	}

	// A lease taken for input that was never sent is released again.
	e.release(`{"window":"0xa1"}`)
	t.Setenv("FAKE_INPUT_MODE", "fail:cannot_type:0:none:0")
	res = e.act(`{"window":"0xa1","actions":[{"action":"type","text":"é"}]}`)
	if code := errorCode(t, res); code != "cannot_type" || leases() != 4 || held() {
		t.Fatalf("code %q, leases %d, held %v: %s", code, leases(), held(), text(res))
	}
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xa1 release " + e.server.owner); len(got) != 1 {
		t.Errorf("released %q", got)
	}
}

// A call that sends nothing leaves the lease as it was: it takes none while it waits for the locks, and gives back one
// that it renewed for input that was refused.
func TestLeaseOfABatchThatSentNothing(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	other := e.newServer()
	callTool(other, context.Background(), actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	e.fake.with(func() { w.Lease.RemainingMs = 20000 })
	owner := func() (string, int) {
		var o string
		var ms int
		e.fake.with(func() { o, ms = w.Lease.Owner, w.Lease.RemainingMs })
		return o, ms
	}

	// Cancelled and timed out while another window's batch holds the send lock.
	sendLock, err := acquireLock(context.Background(), e.server.sendLockPath())
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		e.waitFor(func() bool { return locked(e.server.lockPath("1a")) })
		time.Sleep(100 * time.Millisecond)
		cancel()
	}()
	if res := callTool(e.server, ctx, actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["b"]}]}`); get(t, res, "wait.ended") != "cancelled" {
		t.Errorf("cancelled: %s", text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["b"]}],"deadline_ms":2500}`); errorCode(t, res) != "deadline" {
		t.Errorf("deadline: %s", text(res))
	}
	sendLock.release()
	if o, ms := owner(); o != other.owner || ms != 20000 {
		t.Errorf("lease of %s with %d ms left", o, ms)
	}

	// Refused by the sender after the renewal.
	t.Setenv("FAKE_INPUT_MODE", "fail:cannot_type:0:none:0")
	if res := e.act(`{"window":"0xa1","actions":[{"action":"type","text":"é"}]}`); errorCode(t, res) != "cannot_type" {
		t.Fatal(text(res))
	}
	if o, ms := owner(); o != other.owner || ms > 20000 || ms < 15000 {
		t.Errorf("lease of %s with %d ms left", o, ms)
	}
	e.server.mu.Lock()
	mine := e.server.leased["0xa1"]
	e.server.mu.Unlock()
	if mine {
		t.Error("the session counts the lease it gave back as its own")
	}
}

func TestLeaseWhoseAnswerIsLost(t *testing.T) {
	e := newTestEnv(t)
	w := e.window("0xa1", "1a", 320, 200)
	// The plugin takes the lease, but its answer is lost.
	e.fake.with(func() {
		e.fake.onPlugin = func(words []string) (string, bool) {
			if words[0] == "lease" && words[2] != "release" {
				e.fake.with(func() { w.Lease = &hypr.Lease{Held: true, RemainingMs: 60000, Owner: words[3]} })
				return "garbled", true
			}
			return "", false
		}
	})
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	e.fake.with(func() { e.fake.onPlugin = nil })
	e.server.mu.Lock()
	kept := e.server.leased["0xa1"]
	e.server.mu.Unlock()
	if !kept {
		t.Error("the session forgot a lease it may hold")
	}
	// A session end that has no time left after waiting for the calls still releases it.
	ended, cancel := context.WithCancel(context.Background())
	cancel()
	e.server.Shutdown(ended)
	e.fake.with(func() {
		if w.Lease != nil {
			t.Error("lease kept after the session ended")
		}
	})
}

func TestSessionEndReleasesOwnLeases(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	b := e.window("0xb2", "2b", 320, 200)
	e.window("0xc3", "3c", 320, 200, func(w *fakeWindow) { w.XWayland, w.Caps = true, hypr.Capabilities{Keyboard: "x11-focus"} })
	other := e.newServer()
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.act(`{"window":"0xc3","actions":[{"action":"key","keys":["a"]}]}`)
	if got := get(t, res, "lease"); !reflect.DeepEqual(got, map[string]any{"held": false, "remaining_s": 0.0}) {
		t.Errorf("X11 lease %v: %s", got, text(res))
	}
	callTool(other, context.Background(), actOn, `{"window":"0xb2","actions":[{"action":"key","keys":["a"]}]}`)
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease == nil || b.Lease == nil {
			t.Error("no leases")
		}
	})
	shots := e.server.shotDir
	if _, err := os.Stat(shots); err != nil {
		t.Fatal(err)
	}
	e.server.Shutdown(context.Background())
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("own lease kept")
		}
		if b.Lease == nil || b.Lease.Owner != other.owner {
			t.Error("another session's lease was released")
		}
	})
	if got := e.fake.requestsMatching("j/hyprmasq lease 0xb2 release"); got != nil {
		t.Errorf("released %q", got)
	}
	if _, err := os.Stat(shots); !os.IsNotExist(err) {
		t.Errorf("shots kept: %v", err)
	}
}

func TestRelease(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.release(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "released") != true {
		t.Fatal(text(res))
	}
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("lease kept")
		}
	})
	res = e.release(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "released") != false {
		t.Fatal(text(res))
	}
}

func TestReleaseEndsAnySessionsLease(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	other := e.newServer()
	callTool(other, context.Background(), actOn, `{"window":"0xa1","actions":[{"action":"key","keys":["a"]}]}`)
	res := e.release(`{"window":"0xa1"}`)
	if res.IsError || get(t, res, "released") != true || !strings.Contains(text(res), "another session") {
		t.Fatal(text(res))
	}
	e.fake.with(func() {
		if e.fake.find("0xa1").Lease != nil {
			t.Error("lease kept")
		}
	})
	// A window without a socket holds no lease.
	res = e.release(`{"window":"0xb2"}`)
	if res.IsError || get(t, res, "released") != false {
		t.Fatal(text(res))
	}
	if code := errorCode(t, e.release(`{"window":"0xff"}`)); code != "window_gone" {
		t.Errorf("code %q", code)
	}
}

// Without the lease capability, a menu that a page draws stayed open between calls, and agents that took the lease for
// what keeps menus open, or its absence for what closes them, clicked the opener of a menu that their shot showed open,
// which closed it. So the lease is described by the focus it keeps, no text ties it or the focus to menus or says that
// they stay open or are gone by the next call, and a summary names the lease only while one is held.
func TestLeaseNotTiedToMenus(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xc3", "3c", 320, 200, func(w *fakeWindow) { w.XWayland, w.Caps = true, hypr.Capabilities{Keyboard: "x11-focus"} })
	session := connect(t, e.server, nil)
	tools, err := session.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	texts := map[string]string{"the instructions": session.InitializeResult().Instructions}
	for _, tool := range tools.Tools {
		schema, _ := json.Marshal(tool.InputSchema)
		texts[tool.Name] = tool.Description + "\n" + string(schema)
	}
	key := `{"action":"key","keys":["a"]}`
	leased := e.act(`{"window":"0xa1","actions":[` + key + `]}`)
	unleased := e.act(`{"window":"0xc3","actions":[` + key + `]}`)
	texts["a leased result"], texts["an unleased result"] = text(leased), text(unleased)
	if !strings.Contains(text(leased), " · lease 60 s") || strings.Contains(text(unleased), "lease") {
		t.Errorf("leased %q, unleased %q", text(leased), text(unleased))
	}
	menu := regexp.MustCompile(`(?i)\b(menu|popup|popover|dropdown)s?\b`)
	claim := regexp.MustCompile(`(?i)\blease\b|\bfocus\b|\bstays? open\b|\b(is|are|be) gone\b|\bmay close\b|\bnext call\b`)
	for name, s := range texts {
		for _, clause := range regexp.MustCompile(`[.;:]\s|\\n|\n`).Split(s, -1) {
			if menu.MatchString(clause) && claim.MatchString(clause) {
				t.Errorf("%s: %q", name, clause)
			}
		}
	}
}
