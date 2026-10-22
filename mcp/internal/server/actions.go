package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"math"
	"reflect"
	"slices"
	"strconv"
	"strings"
	"unicode/utf8"
)

// An action of an act batch, checked and with canonical names.
type action struct {
	Kind string
	// Shot pixels, for click, move and scroll.
	X, Y float64
	// Shot pixels, for drag.
	From, To  [2]float64
	Button    string
	Count     int
	Modifiers []string
	DX, DY    float64
	Unit      string
	Text      string
	Keys      []string
	Repeat    int
	Ms        int
}

func (a *action) hasCoordinates() bool {
	switch a.Kind {
	case "click", "move", "drag", "scroll":
		return true
	}
	return false
}

// points returns the shot coordinates of the action, named for messages.
func (a *action) points() []namedPoint {
	switch a.Kind {
	case "click", "move", "scroll":
		return []namedPoint{{"", a.X, a.Y}}
	case "drag":
		return []namedPoint{{"from ", a.From[0], a.From[1]}, {"to ", a.To[0], a.To[1]}}
	}
	return nil
}

type namedPoint struct {
	Name string
	X, Y float64
}

var actionFields = map[string][]string{
	"click":  {"x", "y", "button", "count", "modifiers"},
	"move":   {"x", "y"},
	"drag":   {"from", "to", "button", "modifiers"},
	"scroll": {"x", "y", "dx", "dy", "unit"},
	"type":   {"text"},
	"key":    {"keys", "repeat"},
	"pause":  {"ms"},
}

// The click actions of Anthropic's computer use tool, which Claude models reach for: clicks with their button
// and count. The schema leaves them out, so they cost the model nothing.
var clickAliases = map[string]struct {
	button string
	count  int
}{
	"left_click":   {"left", 1},
	"double_click": {"left", 2},
	"triple_click": {"left", 3},
	"right_click":  {"right", 1},
	"middle_click": {"middle", 1},
}

var actionRequired = map[string][]string{
	"click":  {"x", "y"},
	"move":   {"x", "y"},
	"drag":   {"from", "to"},
	"scroll": {"x", "y"},
	"type":   {"text"},
	"key":    {"keys"},
	"pause":  {"ms"},
}

// Limits within those of hyprmasq-input, which gets every action on its command line.
const (
	maxPauseMs = 5000
	maxRepeat  = 100
	maxNotches = 100
	// A chord of hyprmasq-input has at most nine keys, with names under 64 bytes and 256 bytes in all.
	maxChordKeys  = 9
	maxKeyName    = 63
	maxChordBytes = 255
	maxActions    = 1000
	// The text of a batch, well within Linux's 128 KiB limit for one argument.
	maxTextBytes = 65536
)

// parseActions checks the shape of every action. Errors name the field, for example actions[2].count.
func parseActions(raw []json.RawMessage) ([]action, error) {
	if len(raw) > maxActions {
		return nil, invalidf("actions has %d actions, at most %d", len(raw), maxActions)
	}
	actions := make([]action, 0, len(raw))
	text := 0
	for i, r := range raw {
		a, err := parseAction(r, fmt.Sprintf("actions[%d]", i))
		if err != nil {
			return nil, err
		}
		if text += len(a.Text); text > maxTextBytes {
			return nil, invalidf("actions[%d].text: the batch types more than %d bytes of text", i, maxTextBytes)
		}
		actions = append(actions, a)
	}
	return actions, nil
}

func parseAction(raw json.RawMessage, path string) (action, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return action{}, invalidf("%s must be an object such as {\"action\":\"click\",\"x\":10,\"y\":20}", path)
	}
	var a action
	kindRaw, ok := fields["action"]
	if !ok {
		return a, invalidf("%s.action is missing; it is one of click, move, drag, scroll, type, key, pause", path)
	}
	var named string
	err := json.Unmarshal(kindRaw, &named)
	alias, isAlias := clickAliases[named]
	if err != nil || actionFields[named] == nil && !isAlias {
		return a, invalidf("%s.action must be one of click, move, drag, scroll, type, key, pause, not %s", path, kindRaw)
	}
	a.Kind = named
	allowed := actionFields[a.Kind]
	if isAlias {
		// The alias names the button and count.
		a.Kind, allowed = "click", []string{"x", "y", "modifiers"}
	}
	for name := range fields {
		if name != "action" && !slices.Contains(allowed, name) {
			return a, invalidf("%s.%s is not a field of %s, which takes %s", path, name, named, strings.Join(allowed, ", "))
		}
	}
	required := actionRequired[a.Kind]
	for _, name := range required {
		if _, ok := fields[name]; !ok {
			return a, invalidf("%s.%s is missing", path, name)
		}
	}

	field := func(name string, out any) error {
		r, ok := fields[name]
		if !ok {
			return nil
		}
		if err := checkJSON(r, reflect.TypeOf(out), path+"."+name, slices.Contains(required, name)); err != nil {
			return err
		}
		dec := json.NewDecoder(bytes.NewReader(r))
		if err := dec.Decode(out); err != nil {
			return invalidf("%s.%s must be %s, not %s", path, name, jsonType(reflect.TypeOf(out)), r)
		}
		return nil
	}
	a.Button, a.Count, a.Repeat, a.Unit = "left", 1, 1, "notch"
	var modifiers []string
	var from, to []float64
	errs := []error{
		field("x", &a.X), field("y", &a.Y), field("from", &from), field("to", &to),
		field("button", &a.Button), field("count", &a.Count), field("modifiers", &modifiers),
		field("dx", &a.DX), field("dy", &a.DY), field("unit", &a.Unit), field("text", &a.Text),
		field("repeat", &a.Repeat), field("ms", &a.Ms),
	}
	for _, err := range errs {
		if err != nil {
			return a, err
		}
	}
	if isAlias {
		a.Button, a.Count = alias.button, alias.count
	}

	if a.Kind == "drag" {
		for _, p := range []struct {
			name  string
			value []float64
			out   *[2]float64
		}{{"from", from, &a.From}, {"to", to, &a.To}} {
			if len(p.value) != 2 {
				return a, invalidf("%s.%s must be [x, y] in shot pixels, not %s", path, p.name, fields[p.name])
			}
			*p.out = [2]float64{p.value[0], p.value[1]}
		}
	}
	switch a.Kind {
	case "click", "drag":
		if a.Button != "left" && a.Button != "right" && a.Button != "middle" {
			return a, invalidf("%s.button must be left, right or middle, not %q", path, a.Button)
		}
		if a.Count < 1 || a.Count > 3 {
			return a, invalidf("%s.count must be 1 to 3, not %d", path, a.Count)
		}
		mods, err := canonicalModifiers(modifiers, path+".modifiers")
		if err != nil {
			return a, err
		}
		a.Modifiers = mods
	case "scroll":
		switch a.Unit {
		case "notch":
			if a.DX != math.Trunc(a.DX) || a.DY != math.Trunc(a.DY) {
				return a, invalidf("%s: dx and dy count whole wheel notches with unit notch; use unit px for smooth scrolling", path)
			}
			if math.Abs(a.DX) > maxNotches || math.Abs(a.DY) > maxNotches {
				return a, invalidf("%s: dx and dy are at most %d wheel notches", path, maxNotches)
			}
		case "px":
		default:
			return a, invalidf("%s.unit must be notch or px, not %q", path, a.Unit)
		}
		if a.DX == 0 && a.DY == 0 {
			return a, invalidf("%s: dx and dy are both 0; positive dy scrolls down", path)
		}
	case "type":
		if a.Text == "" {
			return a, invalidf("%s.text is empty", path)
		}
		if !utf8.ValidString(a.Text) {
			return a, invalidf("%s.text is not valid UTF-8", path)
		}
		if strings.ContainsRune(a.Text, 0) {
			return a, invalidf("%s.text contains a NUL character, which cannot be typed", path)
		}
	case "key":
		var keys []string
		if err := field("keys", &keys); err != nil || len(keys) == 0 {
			// An error about an element of the array names that element.
			if raw := bytes.TrimSpace(fields["keys"]); err != nil && len(raw) > 0 && raw[0] == '[' {
				return a, err
			}
			return a, invalidf("%s.keys must be a non-empty array of key names, such as [\"ctrl\",\"l\"] or [\"enter\"]", path)
		}
		chord, err := canonicalChord(keys, path+".keys")
		if err != nil {
			return a, err
		}
		a.Keys = chord
		if a.Repeat < 1 || a.Repeat > maxRepeat {
			return a, invalidf("%s.repeat must be 1 to %d, not %d", path, maxRepeat, a.Repeat)
		}
	case "pause":
		if a.Ms < 0 || a.Ms > maxPauseMs {
			return a, invalidf("%s.ms must be 0 to %d, not %d", path, maxPauseMs, a.Ms)
		}
	}
	return a, nil
}

var keyAliases = map[string]string{
	"ctrl": "ctrl", "control": "ctrl",
	"alt":   "alt",
	"shift": "shift",
	"super": "super", "meta": "super", "win": "super", "cmd": "super",
	"enter": "enter", "return": "enter",
	"esc": "esc", "escape": "esc",
	"backspace": "backspace",
	"delete":    "delete", "del": "delete",
	"tab": "tab", "space": "space",
	"up": "up", "down": "down", "left": "left", "right": "right",
	"home": "home", "end": "end", "pageup": "pageup", "pagedown": "pagedown",
	"plus": "plus",
}

var modifierNames = []string{"ctrl", "alt", "shift", "super"}

// The keysyms of keys that set, latch or lock modifiers or the layout group, in lower case, since key names ignore case.
var modifierKeysyms = map[string]bool{
	"shift_l": true, "shift_r": true, "control_l": true, "control_r": true, "caps_lock": true, "shift_lock": true,
	"meta_l": true, "meta_r": true, "alt_l": true, "alt_r": true, "super_l": true, "super_r": true, "hyper_l": true, "hyper_r": true,
	"num_lock": true, "mode_switch": true, "script_switch": true, "iso_lock": true,
	"iso_level2_latch": true, "iso_level3_shift": true, "iso_level3_latch": true, "iso_level3_lock": true,
	"iso_level5_shift": true, "iso_level5_latch": true, "iso_level5_lock": true,
	"iso_group_shift": true, "iso_group_latch": true, "iso_group_lock": true,
	"iso_next_group": true, "iso_next_group_lock": true, "iso_prev_group": true, "iso_prev_group_lock": true,
	"iso_first_group": true, "iso_first_group_lock": true, "iso_last_group": true, "iso_last_group_lock": true,
}

// heldKey returns the first key of a chord that stays pressed while a later key goes down and is not a modifier, or
// "". Without the hold capability, the plugin may release such a key between input frames, when the user's own input
// takes the keyboard back: it keeps only the modifier state, which it sends again with every frame.
func heldKey(chord []string) string {
	for _, key := range chord[:max(0, len(chord)-1)] {
		if !slices.Contains(modifierNames, key) && !modifierKeysyms[strings.ToLower(key)] {
			return key
		}
	}
	return ""
}

// canonicalKey returns the canonical name of a key. Names that are neither aliases, function keys nor single
// characters are passed on as XKB keysym names, which hyprmasq-input checks before it sends anything.
func canonicalKey(name string) (string, bool) {
	if name == "" || strings.ContainsAny(name, " \t\n\x00") {
		return "", false
	}
	lower := strings.ToLower(name)
	if alias, ok := keyAliases[lower]; ok {
		return alias, true
	}
	if n, err := strconv.Atoi(strings.TrimPrefix(lower, "f")); err == nil && strings.HasPrefix(lower, "f") && n >= 1 && n <= 24 {
		return "f" + strconv.Itoa(n), true
	}
	return name, true
}

func splitKeys(elements []string) []string {
	var keys []string
	for _, element := range elements {
		// The plus key itself is "plus", but a lone "+" can only mean it.
		if element == "+" {
			keys = append(keys, "plus")
			continue
		}
		keys = append(keys, strings.Split(element, "+")...)
	}
	return keys
}

func canonicalChord(elements []string, path string) ([]string, error) {
	var chord []string
	for _, key := range splitKeys(elements) {
		name, ok := canonicalKey(key)
		if !ok {
			return nil, invalidf("%s has an empty or malformed key name in %q; the plus key itself is \"plus\"", path, strings.Join(elements, ","))
		}
		if len(name) > maxKeyName {
			return nil, invalidf("%s: the key name %q is longer than %d bytes", path, name, maxKeyName)
		}
		chord = append(chord, name)
	}
	if len(chord) > maxChordKeys || len(strings.Join(chord, "+")) > maxChordBytes {
		return nil, invalidf("%s: a chord has at most %d keys", path, maxChordKeys)
	}
	return chord, nil
}

func canonicalModifiers(elements []string, path string) ([]string, error) {
	var mods []string
	for _, key := range splitKeys(elements) {
		name, _ := canonicalKey(key)
		if !slices.Contains(modifierNames, name) {
			return nil, invalidf("%s: %q is not a modifier; modifiers are ctrl, alt, shift and super", path, key)
		}
		if !slices.Contains(mods, name) {
			mods = append(mods, name)
		}
	}
	return mods, nil
}

// inputStep is one hyprmasq-input action and the batch action it belongs to.
type inputStep struct {
	Batch int
	Argv  []string
}

func formatNumber(v float64) string {
	return strconv.FormatFloat(math.Round(v*1000)/1000, 'f', -1, 64)
}

func joinModifiers(mods []string) string {
	if len(mods) == 0 {
		return "-"
	}
	return strings.Join(mods, "+")
}

// translate turns one action into hyprmasq-input actions. toLogical maps shot pixels to window-local logical pixels.
func translate(index int, a action, toLogical func(x, y float64) (float64, float64)) []inputStep {
	step := func(argv ...string) inputStep { return inputStep{Batch: index, Argv: argv} }
	point := func(x, y float64) []string {
		lx, ly := toLogical(x, y)
		return []string{formatNumber(lx), formatNumber(ly)}
	}
	switch a.Kind {
	case "click":
		return []inputStep{
			step(append([]string{"move"}, point(a.X, a.Y)...)...),
			step("click", a.Button, strconv.Itoa(a.Count), joinModifiers(a.Modifiers)),
		}
	case "move":
		return []inputStep{step(append([]string{"move"}, point(a.X, a.Y)...)...)}
	case "drag":
		argv := append([]string{"drag"}, point(a.From[0], a.From[1])...)
		argv = append(argv, point(a.To[0], a.To[1])...)
		return []inputStep{step(append(argv, a.Button, joinModifiers(a.Modifiers))...)}
	case "scroll":
		verb := "wheel"
		if a.Unit == "px" {
			verb = "scroll"
		}
		return []inputStep{
			step(append([]string{"move"}, point(a.X, a.Y)...)...),
			step(verb, formatNumber(a.DX), formatNumber(a.DY)),
		}
	case "key":
		return []inputStep{step("keys", strings.Join(a.Keys, "+"), strconv.Itoa(a.Repeat))}
	case "type":
		return []inputStep{step("text", a.Text)}
	case "pause":
		return []inputStep{step("sleep", strconv.Itoa(a.Ms))}
	}
	return nil
}

// describe names an action for messages, such as "action 2 (click)". Indexes in messages start at 0, as in
// failed_index.
func describe(index int, a action) string {
	return fmt.Sprintf("actions[%d] (%s)", index, a.Kind)
}
