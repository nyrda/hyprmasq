package server

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestErrorMapping(t *testing.T) {
	cases := []struct {
		err  error
		code string
	}{
		{&hypr.Error{Code: "window_gone", Message: "no mapped window"}, "window_gone"},
		{&hypr.Error{Code: "unsupported", Message: "x11"}, "unsupported"},
		{&hypr.Error{Code: "unloaded", Message: "unloaded"}, "plugin_missing"},
		{&hypr.Error{Code: "hook_held", Message: "another plugin hooks Desktop::View::CWindow::activate"}, "hook_held"},
		{&hypr.Error{Code: "internal", Message: "render failed"}, "internal"},
		{&hypr.Error{Message: "unknown subcommand"}, "plugin_version"},
		{context.DeadlineExceeded, "deadline"},
		{errCancelled, "cancelled"},
		{errors.New("boom"), "internal"},
	}
	for _, c := range cases {
		if got := asToolError(c.err); got.Code != c.code {
			t.Errorf("%v: %q, want %q", c.err, got.Code, c.code)
		}
	}
}

func TestHintsNeverInviteRepeatingSentInput(t *testing.T) {
	for _, delivery := range []string{"partial", "uncertain", "complete"} {
		for _, shot := range []*shotOut{nil, {ID: "s2"}} {
			out := &actOut{Actions: &actionsOut{Done: 1, Total: 2, Delivery: delivery}, Shot: shot}
			hint := finalHint(asToolError(context.DeadlineExceeded), out)
			if !strings.Contains(hint, "before repeating anything") || strings.Contains(hint, "Retry") {
				t.Errorf("%s with shot %v: %q", delivery, shot != nil, hint)
			}
		}
	}
	if hint := finalHint(asToolError(context.DeadlineExceeded), &actOut{Actions: &actionsOut{Total: 2, Delivery: "none"}}); hint != joinHints(asToolError(context.DeadlineExceeded).Hint, hintNothingSent) {
		t.Errorf("none: %q", hint)
	}

	// A wait that fails after the input was applied.
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.fake.with(func() {
		e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
			return pluginError("internal", "the wait broke")
		}
	})
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`)
	if code := errorCode(t, res); code != "internal" || get(t, res, "actions.delivery") != "complete" || !hasImage(res) {
		t.Fatalf("code %q: %s", code, text(res))
	}
	if hint := get(t, res, "hint").(string); hint != "Look at the shot before repeating anything." {
		t.Errorf("hint %q", hint)
	}
}

// A sender that the plugin refused because another plugin hooks a function that input needs sent nothing, and the
// result says so, with the function, and that screenshots still work.
func TestHookHeldSender(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	t.Setenv("FAKE_INPUT_MODE", "fail:hook_held:0:none:0")
	res := e.act(`{"window":"0xa1","actions":[{"action":"key","keys":["enter"]}]}`)
	if code := errorCode(t, res); code != "hook_held" || get(t, res, "actions.delivery") != "none" {
		t.Fatalf("code %q: %s", code, text(res))
	}
	hint := get(t, res, "hint").(string)
	if !strings.HasPrefix(hint, hintHookHeld) || !strings.Contains(hint, hintNothingSent) {
		t.Errorf("hint %q", hint)
	}
}

// A sender refused after another of the same call delivered its part leaves the delivery's guidance to the result: the
// hint neither says that nothing happened nor invites the whole call again.
func TestHookHeldAfterDeliveredInput(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = page(300) })
	e.screenshot(`{"window":"0xa1"}`)
	paintOnWaits(e, nil, page(300), page(315), page(390), page(450))
	// The click, the page's first increment, and its second, which the plugin refuses.
	t.Setenv("FAKE_INPUT_MODE", "ok;ok;fail:hook_held:0:none:0")
	res := e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"click","x":20,"y":20},{"action":"scroll","x":200,"y":100,"unit":"page","dy":1}]}`)
	if code := errorCode(t, res); code != "hook_held" || len(e.inputs()) != 3 || get(t, res, "actions.delivery") != "partial" {
		t.Fatalf("code %q, %d senders %v: %s\n%s", code, len(e.inputs()), e.inputs(), text(res), structured(t, res))
	}
	hint := get(t, res, "hint").(string)
	if strings.Contains(hint, hintNothingSent) || strings.Contains(hint, "call again") || !strings.Contains(hint, "before repeating anything") {
		t.Errorf("hint %q: %s", hint, text(res))
	}
}
