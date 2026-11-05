package server

import (
	"context"
	"strings"
	"testing"
)

// holdLock takes the lock at path for the test, as a call of another server would, and releases it at the end.
func holdLock(t *testing.T, path string) {
	t.Helper()
	l, err := acquireLock(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(l.release)
}

// Input to a window of another process runs while a batch of one app holds its send lock, and input to another window
// of that app waits for it.
func TestInputToOtherAppsDoesNotWait(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits = true, 10 })
	e.window("0xb1", "1b", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits, w.PID = true, 10, 5151 })
	holdLock(t, e.server.sendLockPath(4242, false))

	if res := e.act(`{"window":"0xb1","actions":[["key","b"]],"wait":{"expect_change":false},"deadline_ms":2500}`); get(t, res, "actions.delivery") != "complete" {
		t.Errorf("another app: %s", text(res))
	}
	res := e.act(`{"window":"0xa1","actions":[["key","a"]],"deadline_ms":2500}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "another window of the same app") || get(t, res, "actions.delivery") != "none" {
		t.Errorf("same app: code %q: %s", code, text(res))
	}
}

// The windows of Hyprland's XWayland share one send lock, whatever their processes, since their X server has one focus.
func TestXWaylandWindowsShareTheSendLock(t *testing.T) {
	slow(t)
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits, w.XWayland = true, 10, true })
	e.window("0xb1", "1b", 320, 200, func(w *fakeWindow) { w.Socket, w.Commits, w.XWayland, w.PID = true, 10, true, 5151 })
	holdLock(t, e.server.sendLockPath(4242, true))

	res := e.act(`{"window":"0xb1","actions":[["key","b"]],"deadline_ms":2500}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "another X11 window") || e.inputs() != nil {
		t.Errorf("code %q, sent %q: %s", code, e.inputs(), text(res))
	}
}

// The agent clipboard is one for all windows, so a batch with a paste waits while a paste into a window of another app
// runs, and a batch without one does not.
func TestPastesTakeTurnsAcrossApps(t *testing.T) {
	slow(t)
	e := pasteEnv(t, &fakeClipboard{}, false)
	holdLock(t, e.server.clipboardLockPath())

	res := e.act(`{"window":"0xa1","actions":[["paste","mine"]],"deadline_ms":2500}`)
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "the clipboard lock, held by a paste into another window") || e.inputs() != nil {
		t.Errorf("paste: code %q, sent %q: %s", code, e.inputs(), text(res))
	}
	if res := e.act(`{"window":"0xa1","actions":[["key","a"]],"wait":{"expect_change":false},"deadline_ms":2500}`); get(t, res, "actions.delivery") != "complete" {
		t.Errorf("keys: %s", text(res))
	}
}
