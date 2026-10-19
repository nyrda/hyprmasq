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
