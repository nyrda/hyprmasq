package server

import (
	"context"
	"fmt"
	"time"
	"unicode/utf8"
)

// A paste action puts its text on the agent clipboard, which the plugin holds and which pastes only in windows that
// emulated input reaches, and presses Shift+Insert, which GTK, Qt, Chromium, Electron, Firefox and LibreOffice paste the
// clipboard on. It sets neither the user's clipboard nor the agent's primary selection: GTK drops a text selection whose
// source gets a cancel, so a primary selection of the paste's own would undo the selection that the paste replaces.

const (
	// pasteKeys is the chord that pastes the clipboard.
	pasteKeys = "shift+Insert"
	// pasteChunkBytes is the most text of one clipboard set, which goes to the plugin in hex, within hypr.MaxRequest. A
	// longer paste pastes chunk by chunk.
	pasteChunkBytes = 16000
	// pasteSyncMaxMs is the sender's longest wait at a paste's syncs: a sync that runs out goes on without an answer,
	// so these wait until act answers, or stops the sender when setting the text failed.
	pasteSyncMaxMs = 3600000
	// How long a paste waits for the plugin to set the text, and for the app to read it after Shift+Insert.
	pasteSetTimeout  = 2 * time.Second
	pasteReadTimeout = 3 * time.Second
	pasteReadPoll    = 20 * time.Millisecond
)

// pasteChunks splits a paste's text into chunks of at most pasteChunkBytes, at character boundaries.
func pasteChunks(text string) []string {
	var chunks []string
	for len(text) > pasteChunkBytes {
		cut := pasteChunkBytes
		for cut > 0 && !utf8.RuneStart(text[cut]) {
			cut--
		}
		chunks, text = append(chunks, text[:cut]), text[cut:]
	}
	return append(chunks, text)
}

// checkPaste refuses a paste into an X11 window of Hyprland's XWayland, whose apps paste the X server's selections,
// not the agent clipboard.
func checkPaste(batch []action, xwayland bool) error {
	for i, a := range batch {
		if a.Kind == "paste" && xwayland {
			return &toolError{Code: "unsupported", Message: fmt.Sprintf("%s: paste works in Wayland windows, and this is an X11 window", describe(i, a)),
				Hint: `Type the text with ["type",text] instead.`}
		}
	}
	return nil
}

// setPaste makes the text of a part of the paste action at index the agent clipboard, before its Shift+Insert, and
// returns the text's serial. When that fails after earlier parts of the paste went in, the error says so, as pasting
// the whole text again would insert them twice.
func (s *Server) setPaste(ctx context.Context, r *actRequest, index int, step inputStep) (uint64, error) {
	setCtx, cancel := context.WithTimeout(ctx, pasteSetTimeout)
	defer cancel()
	serial, err := s.hypr.SetClipboard(setCtx, *step.PasteText)
	if err == nil {
		return serial, nil
	}
	if step.PastePart == 0 {
		return 0, &toolError{Code: "internal", Message: fmt.Sprintf("%s: setting the agent clipboard failed, so nothing was pasted: %v", describe(index, r.batch[index]), err),
			Hint: `Type the text with ["type",text] instead, or retry.`}
	}
	return 0, &toolError{Code: "internal",
		Message: fmt.Sprintf("%s: setting the agent clipboard for part %d of %d of the text failed, so only the parts before it were pasted: %v",
			describe(index, r.batch[index]), step.PastePart+1, step.PasteParts, err),
		Hint: "Look at the window before you paste again: pasting the whole text again would insert its first parts twice. Paste or type only what is missing."}
}

// pasteRead waits until the pastes of the text of serial have ended, so that what comes after the Shift+Insert, another
// part or other input, comes after the text. It reports whether one of them got all of the text: not when no paste
// asked for it in time, as when the app does not paste on Shift+Insert or no text field had the focus, nor when the
// app closed its pipe early.
func (s *Server) pasteRead(ctx context.Context, serial uint64) bool {
	until := time.Now().Add(pasteReadTimeout)
	for {
		c, err := s.hypr.Clipboard(ctx)
		if err != nil || c.Serial != serial || !c.Held {
			return false
		}
		if c.Asked > 0 && c.Ended == c.Asked {
			return c.Complete > 0
		}
		if time.Now().After(until) {
			return false
		}
		select {
		case <-ctx.Done():
			return false
		case <-time.After(pasteReadPoll):
		}
	}
}

// unreadPasteHint names the paste actions whose text no app read.
func unreadPasteHint(unread []int) string {
	if len(unread) == 0 {
		return ""
	}
	return fmt.Sprintf("No app read all of the text of the paste at actions %v within %v of its Shift+Insert: the focused widget may not take text, the app may not paste on Shift+Insert, as terminals paste the primary selection there, or a copy in another window replaced the agent clipboard first. Look at the shot; type the text with [\"type\",text] where paste does not work.", unread, pasteReadTimeout)
}
