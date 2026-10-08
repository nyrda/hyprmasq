# MCP server

Status: implemented in hyprmasq 0.3.0, plugin interface 2. This document is
the design as built. [Installation](installation.md#mcp-server) covers
installing and registering the server.

`hyprmasq-mcp` gives coding agents (Claude Code, Codex, pi and other MCP
clients) computer use on Hyprland through hyprmasq. One tool call acts on a
window, waits until it has drawn the result, and returns what changed since
the agent's last shot of it. The shell API in [usage.md](usage.md) stays as it
is for scripts, CI and people.

## Goals

- **A fast loop.** One call per step: act, wait, capture.
- **Failures that are easy to handle.** Every failure says what was sent, what
  was not, and what to do next.
- **Reliable, paced input.** Input is timed like a person's, not sent as fast
  as the socket takes it.
- **Waiting with documented guarantees.** Each guarantee is backed by a VM
  subtest or a unit test, and so is each case it does not cover.
- **Self-documenting tools.** An agent needs no skill or external docs to use
  them.

Not a goal: a security boundary. An agent with a shell can call `hyprctl`,
open any socket or load its own plugin, so a gate inside the MCP server would
protect nothing. Whoever runs hyprmasq trusts the agents they give it to. A
security layer, if ever, belongs in the plugin or in Hyprland. See
[Later](#later).

## Components

| Component | Role |
| --- | --- |
| Plugin | Screenshots, the commit counter, waiting, the focus lease, frame callbacks for hidden windows. Exact semantics, JSON error codes. |
| `hyprmasq-input` | The only input sender. Paced input, literal text, wheel scrolling, interpolated drags, multi-clicks, key chords by name, per-frame confirmation and JSON results. |
| `hyprmasq-mcp` | In Go with the [official SDK](https://github.com/modelcontextprotocol/go-sdk) v1.8.0. Runs over stdio. Talks to Hyprland's socket directly, runs one `hyprmasq-input` for each batch, keeps shots, validates batches, takes the locks, and turns plugin and sender replies into tool results. |

The server speaks MCP 2025-11-25, which Claude Code, Codex and pi use for stdio
servers today. It does not narrow the versions the SDK supports, so it also
speaks 2026-07-28 with a client that asks for it. It keeps no state that
2026-07-28 would forbid: shots are handles passed as arguments.

The server sends requests to `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock`
itself instead of running `hyprctl`. Hyprland keeps a connection open until the
plugin's deferred reply is ready, so waits are not limited by `hyprctl`'s
5-second read timeout.

## Tools

| Tool | Arguments | Result |
| --- | --- | --- |
| `windows` | optional `match` | All windows, or with `match` those whose class or title contains it, ignoring case as Unicode simple case folding does (`Σ` finds `ς`): `window` (address), `stable_id`, `class`, `title`, `pid`, `workspace` (name), `visible` (on a monitor's active or special workspace), `size` (logical), `resizing` (not yet redrawn at its size, see [Shots](#shots-and-coordinates)), `xwayland`, `launched` (by this server), `lease` (`held`, `remaining_s`, `mine`, or `null`), `capabilities`. |
| `launch` | `command` (array, required), `workspace` (default `hyprmasq`), `timeout_s` (1 to 300, default 30) | Starts the command on a hidden workspace like `hyprmasq-launch`, waits for its first window and for that window to settle, and returns `window`, `wait`, `shot` and `new_windows`, the other windows that appeared, with the image. The hint names the windows on its workspace whose size changed meanwhile, such as tiled windows that made room for it, so that their earlier shots are stale. |
| `screenshot` | `window`, optional `region` | A shot, with the image. No lock, and no waiting except for a window that has not caught up with a resize. See [Shots](#shots-and-coordinates). |
| `act` | `window`, `shot`, `actions`, `wait`, `capture`, `deadline_ms` | Runs the actions, waits, and captures what changed. See [act](#act) and [Changes](#changes). |
| `release` | `window` | Ends the window's focus lease, whichever session holds it. The window stays open. Returns `released`. |
| `close` | `window` | Asks the window to close, as Hyprland's close dispatcher does, ends its lease, and reports whether it closed within 2 s (`closed`, `elapsed_ms`) and which windows appeared (`new_windows`), such as a confirmation dialog. A window that stays open is not an error. When a tiled window closed, the hint names the tiled windows on its workspace, which take up its space, so that their earlier shots are stale. |

`windows` is annotated `readOnlyHint`, `screenshot`, `launch` and `release` as
not destructive, and `release` as idempotent. `act` and `close` keep the
defaults. `screenshot` is not read-only: it opens the window's socket for the
commit counter that the shot records, and the socket stays open, so from then
on the plugin treats the window as driven (see the
[security model](usage.md#security-model)).

`window` takes an address as `windows` lists it: `0x` and up to 16 hex digits.
Hyprland and the plugin compare addresses as strings, so the server first
writes the address as they do, in lower case and without leading zeros:
`0x00A1` names the window `0xa1`, and results name it so.

`capabilities` describe what works on a window at the moment: `lease` (focus
can be held between calls), `hold` (buttons and keys can stay pressed across
input frames, needed for `drag`, for a `click` with modifiers and for a `key`
chord that holds a key other than a modifier) and
`keyboard` (`yes`, or `x11-focus` when X11 input depends on which window has
the focus). They are observations, not reservations. `act` checks `hold`
before sending and again once it holds both [locks](#concurrency). The
plugin's `dnd` capability is left out, see [Actions](#actions).

Every call first checks that the plugin is loaded and has interface 2
(`plugin_missing`, `plugin_version`).

## Shots and coordinates

A shot is a screenshot the server remembers under a short ID such as `s12`.
Every coordinate an agent sends is in the pixels of one shot. The server keeps
the transform, so the agent never converts anything.

A shot records:

- the window's address and `stable_id`, so a new window at a reused address
  does not match;
- the window's logical size at capture;
- its frame: the box it shows, in window-local logical pixels. Every shot is
  taken with the window's Wayland popups, also where they reach outside the
  window, so the frame can start at negative coordinates when a popup reaches
  above or left of the window. Shot pixels always start at 0,0;
- the image size and the scale from logical to shot pixels;
- the window's commit counter at the moment of capture (not when the PNG was
  done), used as a [wait baseline](#waiting), and the id of the socket that
  counter belongs to;
- whether the window had caught up with its size, see below;
- for a shot of the whole window, the detail image that a later comparison
  uses, and how many [changes shots](#changes) in a row lead to it; for a
  changes shot, the shot it was compared with; for a zoom, the shot of the
  whole window it was read from.

A shot pixel X,Y maps to the window-local point `frame.x + X / scale`,
`frame.y + Y / scale`, rounded to thousandths of a logical pixel for
`hyprmasq-input`. This holds also for a shot taken while Hyprland animates the
window, as when it opens or a tiled neighbour opens or closes: on screen,
Hyprland draws the window at the animated size and scales or cuts its content
to fit, but the plugin draws a shot's window at its size, where input lands
(see [`screenshot`](#plugin-commands)).

**Overview shots** are fitted within 1280 pixels on the long side by default,
never enlarged beyond the monitor's scale. The plugin shrinks the image on the
GPU rather than encoding a full-size image. The server option
`--max-image-size N` sets another limit, and `0` keeps the monitor's scale.
The result always states the exact image size, and coordinates refer to that
size; a client that shrinks the image leaves the model to scale its
coordinates back.

The default comes from 16 practice runs in which Claude Code 2.1.289 and Codex
clicked targets on a Chromium page in a 2560×1440 window, with and without the
limit. Both clients shrank the full-size overviews before the model saw them,
Claude Code to 2000×1125 and Codex to 2048×1152. With shots of 1280 pixels the
agents clicked just as accurately, and Claude's runs cost 35 to 42% less. Zoom
shots still show small areas in full detail. Dense interfaces with small text
have not been tried at 1280 pixels.

**Zoom shots** come from `screenshot` with `region: {shot, x, y, width,
height}`, given in the pixels of an earlier shot of the same window. The
region must start inside that shot's image and is cut to it. The server turns
it into whole logical pixels, rounding the start down and the end up, and the
plugin captures the current content of that area at the monitor's full scale,
fitted within `--max-image-size`, which a small area does not reach. The
result is a new shot. Actions accept coordinates in a zoom shot's pixels. Use
zoom to read small text or to hit small targets. A zoom into the part of a
popup that has closed since its shot fails with `stale_shot`. The area is in
window-local pixels, so after a resize it would show another part of the
window than the earlier shot did: the zoom is taken only while the window has
that shot's size, which the server checks right before the capture, also
after it waited for the window to catch up with a resize. Otherwise
`screenshot` fails with `stale_shot`.

**A window that has not caught up with a resize** shows an older frame: after
a tiled neighbour opened or closed, for example, Hyprland gives the window a
new size at once, but until the application has drawn at that size, Hyprland
shows its last frame in the new box, cut off or with repeated or transparent
pixels beside it. The plugin reports such a window as `resizing` (see
[`state`](#plugin-commands)). Before every capture, for `screenshot`,
`launch`, `act` and the shot that comes with a failure, the server checks for
it, and if the window is resizing, runs a `wait … since` without a change
phase and with a quiet time of 100 ms. That wait ends once the window has drawn
at its size and gone quiet, and sends a hidden window the frame callbacks it
may need. It runs for at most 1.5 s, and only within the time the call has
left before the capture's reserve, so after an `act` whose wait ran into the
deadline it does not run. A window that has not caught up by then is captured
anyway: its shot has `"resizing": true`, the summary says
`shot sN W×H (stale: not yet redrawn at its new size)`, and the hint says to
wait with an `act` without actions and with `expect_change` false, whose
quiet phase does not end before the window has caught up. When an `act`'s own
wait timed out because the window had not caught up, the summary says
`timeout after N ms, still resizing`, `wait.summary` is `resizing`, and the
hint is only the one about the older frame, not one about drawing. `windows`
reports `resizing` for each window too.

`act` takes no coordinates from such a shot, and neither does a region. It
fails with `stale_shot`, sends nothing and returns a new shot, also when the
window has caught up since: the application lays its content out anew at the
new size, so the older frame shows things where they no longer are. In a
practice run, an agent took a screenshot right after closing a window, and its
click on the older frame of the remaining window missed. A warning in the
result would leave that to the agent; the refusal costs one call, which
returns the new shot. Keyboard input needs no coordinates, and is sent.

**Checks** before any input is sent:

- the shot exists in this server and belongs to the given window (same address
  and `stable_id`);
- the window still has the shot's logical size, and the shot does not show an
  older frame, if the batch has coordinates;
- every coordinate lies inside the shot's image;
- no point lies left of or above the window, which pointer input cannot reach
  (see [Pointer regions](usage.md#pointer-regions));
- the points lie in the pointer regions of the window as it is now, which
  `hyprmasq-input` checks when it connects.

Otherwise the call fails with `stale_shot` or `out_of_bounds` and nothing is
sent. Once the call holds both [locks](#concurrency), the server reads the
window's state again, right before it starts the sender. A window replaced
since (`window_gone`), resized since the shot (`stale_shot`), or now lacking a
capability the batch needs (`unsupported`) fails the call with nothing sent.
An `act` without actions reads the window again once it holds the window
lock, and fails with `window_gone` when the window closed while the call
waited, also when another window has its address now. Nothing of the call
reaches such another window: every capture checks the `stable_id` before it
opens a socket or takes a shot, and fails with `window_gone` otherwise.

While the batch is sent, the server reads the window's state every 25 ms
whenever a resize could make coordinates wrong that are still to be sent: when
an action after the first has coordinates, when the first action is a `drag`,
and when the batch is a single action with coordinates, which goes out only
once `hyprmasq-input` has connected. A resize stops the sender with SIGTERM
and fails the call with `stale_shot` and the delivery so far: usually
`partial`, or `none` when the sender had not connected yet. A window that
closes or is replaced gives `window_gone`. A resize while keys follow a first
click or scroll does not stop the batch, since the keys may cause it
themselves. A move that keeps the size is not noticed; see
[Known limits](#known-limits).

These checks guard geometry, not content. Content can move without the window
resizing, and a popup can close after its shot was taken. The server does not
invalidate shots on every redraw, which would make animated windows unusable.

Shot IDs count up per server process. The server keeps the 64 most recently
used shots per window, and deletes their files, detail images included, when
it drops them. It forgets the shots of a window that it saw close, and,
whenever a tool lists the windows, those of windows that the list no longer has
or whose address now has another `stable_id`; with them it forgets which shot
of the window a result gave last. Image files live under
`$XDG_RUNTIME_DIR/hyprmasq/<instance>/mcp/<pid>/`, in the plugin's directory
for the Hyprland instance, whose name is `$HYPRLAND_INSTANCE_SIGNATURE`
without the commit hash in front (see
[Security model](usage.md#security-model)).

## Changes

By default (`capture: "changes"`), `act` returns only what changed since a
shot the agent already has: a changes shot, and a zoom into each region that
changed. The agent reads less than a new overview, sees small text at the
monitor's scale, and sees at once a change outside its target, such as keys
that reached another widget. The zooms are meant to feed a quick check of
whether an action landed as well.

**Base.** The changes are measured from the shot given in `shot`, or for a
zoom, from the shot of the whole window that it was read from. Without
`shot`, they are measured from the last shot of the whole window that a
result gave this session: a `screenshot` or `launch` shot, a full or changes
shot of `act`, or the shot that comes with a failure. A zoom does not count,
since it shows part of a shot the agent has. Shot IDs belong to one server
process, so another session has no base until it gets a shot of its own, and
neither has a new window at the address. The base is picked when the call
starts, from what the agent had then.

**Detail images.** Overviews shrink large windows: a 2544×1432 window is 1280
pixels wide, and a window that fills a 5120-pixel monitor a quarter of its
size. At a quarter, a typed `i` or `.` did not show in the comparison at all,
while in logical pixels each changed a box of 6×16 or 5×16 pixels. So every
shot of the whole window keeps a detail image: its own image when it has the
window's logical pixels or the monitor's, whichever are fewer, and otherwise
one more screenshot right after the overview, with `max` set to the long side
of the box of the window and its popups in logical pixels on a monitor above
scale 1, and without `max` otherwise. Both images have to show one frame: when
the commit counters of the two replies differ, since the window drew in
between, or the box differs, since a popup opened or closed past the window's
edge, the pair is captured once more, and when that happens again the shot
keeps no detail image, which a later changes capture answers with a full shot.
A detail image never holds what the agent was not shown. A changes shot keeps
the detail image it was compared at. For a
2544×1432 window, a detail image takes about 50 ms to capture and 25 ms to
decode, and the comparison 120 ms.

**Comparison.** After the wait, the server captures a detail image with the
same request as the base's, and compares the two if the window has the same
size, the box of the window and its popups is the same, and the scale is the
same. A pixel has changed when the average of the 3×3 pixels around it changed
by more than 24 of 255 levels in luminance, or by more than 64 in a color
channel; transparent pixels count as mid grey. Averages ignore Chromium's
switches between subpixel and grayscale antialiasing, which it makes when it
draws a layer anew and which move color between neighbouring pixels. The
color channels catch a change of color that keeps the luminance, such as a red
popup over a blue window, at a threshold at which the antialiasing does not
show. Changed pixels at most 8 logical pixels apart are one change, such as a
typed word and the caret after it. A change at most 4 logical pixels wide or
tall, such as a blinking caret, or at most 5 in both directions, such as a
speck, is tiny: it is left out, and counted unless a zoom shows all of it.
Every other change gets a margin of 24 logical pixels for context; changes
whose margins overlap or touch share a region, and regions are cut to the
whole logical pixels that cover the box of the shot.

**Result.** A changes shot keeps the base's coordinates: it has the base's
width, height, frame and scale, so the agent goes on with coordinates read
from the base, and `act` takes them with the changes shot's ID as well. Its
commit counter is that of its own capture, the baseline of a later wait. Each
region becomes a zoom shot at the monitor's scale, with pixels of its own that
`act` takes. When the compared detail image has the monitor's pixels, as on a
monitor at or below scale 1, the zoom is cut from it, so that it shows exactly
what was compared. Otherwise, or when it would not fit within
`--max-image-size`, it is captured as `screenshot` with `region` captures it,
and has to show the compared frame: the same commit counter, and all of its
region, which the plugin cuts when a popup past the window's edge closed. The summary names the base and the number of changes,
and a line follows for each zoom, in the order of the images, top to bottom
and then left to right, with its image size and the box of the changes shot's
pixels that it shows:

```
1/1 delivered · redraw observed · quiet 214 ms · 655 ms · shot s7 = s5 + 2 changes · lease 60 s
zoom s8 496×80 of s7 at 530,0 248×40
zoom s9 456×124 of s7 at 0,670 228×62
1 tiny change left out
```

Without a change there is no image: `shot s7 = s5, no change`. The window
then looks as s5 does, apart from the tiny changes that a line counts and
changes below the thresholds, so the hints that say to look before repeating
input name s5: after a click that drew nothing, `The click may have missed or
had no visible effect. Look at s5 before repeating it.`, after input that the
window redrew for but that changed nothing, `Nothing changed since s5, so the
input may have missed or had no visible effect. Look at s5 before repeating
it.`, and when tiny changes were left out, `Only tiny changes since s5, such as
a caret that moved or blinked, so the input may have missed or had little
visible effect. Look at s5 before repeating it.` After a hover, which may
change nothing, there is no such hint, nor after an empty batch.

**Full shots.** A full shot, as `capture: "window"` returns it, comes in
place of the changes when there is nothing to compare with or a full shot
serves better. It says why after the shot in the summary, as in
`shot s7 1280×721 (full shot: 68% changed since s5)`, and in `shot.full_shot`,
and it is the next base:

| Reason | When |
| --- | --- |
| `no earlier shot to compare with` | There is no `shot` and no shot of the window in this session. |
| `the shot that sN zooms into is gone` | `shot` is a zoom, and the server no longer keeps the shot it zooms into. |
| `sN cannot be compared` | The base shows an older frame of a window that had not caught up with a resize, or has no detail image, as when the window drew between its overview and detail image twice in a row. |
| `10 changes shots in a row` | The base is the tenth of a chain of changes shots since a full shot, each compared with the one before, as when `act` gets no `shot`. The eleventh gets a full shot, so that the agent's picture, an overview with the zooms of ten results over it, does not drift from the window. Calls that keep comparing with the same shot show every change since it in their zooms, and need no such refresh. |
| `the window is resizing` | The window had not caught up with a resize, or was resized, while the detail image was captured. The full shot gives it time to catch up, as every capture does. |
| `resized since sN` | The window's size changed. |
| `the monitor's scale changed since sN` | The detail images have different scales, as after a move between monitors at or below scale 1. Above scale 1 detail images have logical pixels, which stay comparable, and coordinates are in logical pixels anyway. |
| `popups outside the window changed since sN` | The box of the window and its popups changed, as when a menu that reaches past the window's edge opens or closes. The base's coordinates would not cover it. |
| `N% changed since sN` | More than 35% of the pixels changed, or the regions cover more than 35% of the window, as after scrolling or navigating. |
| `N changed regions since sN` | More than 5 regions. |
| `the zooms into the changes since sN would be larger` | The zooms, at the monitor's scale, have more pixels than the full shot, as for a large window whose overview is shrunk to half. |
| `too little time left to compare with sN` | The call's deadline would not leave the full shot the 500 ms it may need after the comparison. The comparison takes about 60 ns a pixel of the detail images, and stops once that time is reached. |
| `the window changed while the changes since sN were captured` | A captured zoom failed, or shows another frame than the compared one, as when the window drew, was resized or closed a popup past its edge after the comparison. The zooms made before go with the changes shot. |

`capture: "window"` asks for a full shot, for example when the agent no
longer has the earlier shot after its context was compacted. A failure before
any input comes with a full shot, from which the agent reads coordinates anew.

**Limits.** The comparison does not see a change below its thresholds, such as
a row's hover highlight in a dark theme, 24 levels lighter, or a change that a
tiny one hides. A window that keeps drawing, such as one with an animation,
often draws between two captures, and then gets full shots.

The limits come from measurements with windows launched for them, before and
after an input and the wait that `act` runs, compared at logical pixels: a
Spotify-like page in Brave, about the size of the Spotify window that
prompted this, at 2544×1432 on Wayland with a lease and at 2545×1420 on
XWayland without one, and gnome-calculator at 360×616.

| Interaction | Result |
| --- | --- |
| Typing a word, a single character or a backspace into a search field | 1 region |
| A click into the search field | 3 regions: the field, and the page's clock and progress bar |
| A click on a list row | 1 or 2 regions: the row, and the field it took the focus from |
| A double click on a song, which changes the row, the queue, the now-playing area and the progress bar | 3 or 4 regions, 12 to 14% of the window |
| Opening or closing a menu of the page | 2 or 3 regions |
| Moving the pointer over a list row | no change: its highlight is 24 levels lighter |
| A caret's blink, idle | 1 tiny change |
| Idle for 1.5 s, while the page's clock ticks | 2 regions |
| Scrolling 3 notches | full shot, 73 to 75% |
| Navigating to another view | full shot, 78 to 87% |
| A click on a calculator button, typing digits | 1 or 2 regions |
| The calculator's mode menu, a popover inside the window, opening and closing | full shot at 38%, then 1 region at 34% |
| The calculator's main menu, a popover past the window's edge | full shot, popups changed |

After the two clicks into the page, the luminance at a threshold of 20 left
32 and 34 boxes of switched antialiasing besides the 3 and 2 changes, and at
24 none; the color channels alone left 2 more at 32, and none at 64. Chromium
switched the antialiasing on Wayland, not on XWayland.

## act

```json
{
  "window": "0x55d1c0a3e4b0",
  "shot": "s4",
  "actions": [
    {"action": "click", "x": 412, "y": 88},
    {"action": "type", "text": "hello world"},
    {"action": "key", "keys": ["enter"]}
  ]
}
```

| Argument | Default | Meaning |
| --- | --- | --- |
| `window` | required | Window address. |
| `shot` | required if any action has coordinates | The shot the coordinates were read from. Keyboard-only batches need none. |
| `actions` | `[]` | Run in order, at most 1000. An empty list only waits and captures. |
| `wait` | see [Waiting](#waiting) | `expect_change` (true), `expect_ms` (1000, 1 to 29000), `quiet_ms` (200, 1 to 10000). An `expect_ms` given above `deadline_ms` − 1000, with `expect_change` true, is refused, naming the `deadline_ms` it needs. |
| `capture` | `"changes"` | `"changes"` for what changed since the base, as a changes shot and zooms, or a full shot that says why (see [Changes](#changes)); `"window"` for an overview shot; `"none"`; or a region `{shot, x, y, width, height}` as for `screenshot`, also accepted as `{"region": {...}}`. When the window was resized during the call, for example by its own input, the region would show another part of the window, so the call captures the whole window instead and the hint says why. |
| `deadline_ms` | 15000, 1000 to 30000 | Covers everything: waiting for locks, validation, sending, waiting and capture. The last 1000 ms are reserved for the capture, and input ends 1250 ms before them. A batch whose input takes longer than that leaves is refused, see [Pacing](#pacing). |

Unknown arguments and fields are refused. Errors name the field by its path,
down to an element of an array, and say in JSON's terms what it takes, for
example `wait.quiet_ms must be an integer, not a string`,
`command[1] must be a string, not a number`, `wait.expect is not a field of
wait, which takes expect_change, expect_ms, quiet_ms`, or `unknown argument
"x"; the arguments are …`. Every tool checks its arguments this way before it
does anything, since the SDK leaves checking them against the schema to the
server. `null` is refused for a number, for an element of an array and for a
required field, for example `actions[0].x must be a number, not null` or
`actions[0].from[1] must be a number, not null`. An optional field of another
type given as `null` takes its default, as if it were left out.

### Actions

| Action | Fields | Sends |
| --- | --- | --- |
| `click` | `x`, `y`, `button` (`left`, `right`, `middle`), `count` (1 to 3), `modifiers` | Motion, then presses and releases. With `modifiers`, needs `hold`. |
| `move` | `x`, `y` | Motion only, for hover. |
| `drag` | `from` [x, y], `to` [x, y], each exactly two numbers, `button`, `modifiers` | Press, motion in steps, release. Needs `hold`. |
| `scroll` | `x`, `y`, `dx`, `dy`, `unit` (`notch` or `px`, default `notch`) | Motion to x,y, then scrolling there. `notch` is a wheel click, whole numbers up to 100 per axis; `px` is smooth scrolling in window-local logical pixels. Positive `dy` scrolls down, positive `dx` right. Neither promises how far the content moves. |
| `type` | `text` | The text exactly as given, with the active keymap. No tags, no escapes. A line break presses Enter and a tab presses Tab, so a new line in a chat or form field needs a `key` action such as `shift+enter`. At most 65536 bytes of text per batch. |
| `key` | `keys`, `repeat` (1 to 100) | The keys of `keys` pressed together, in order, and released in reverse. `repeat` presses the chord that many times. At most 9 keys. A chord that holds a key other than a modifier while a later key goes down, such as `["a", "b"]`, needs `hold`; modifiers are `ctrl`, `alt`, `shift`, `super` and the keysyms of modifier keys, such as `Shift_L` or `ISO_Level3_Shift`. |
| `pause` | `ms` (0 to 5000) | Waits. It does not check that anything is ready. Keys after a click need none, see [Pacing](#pacing). |

Key names are XKB keysym names, case-insensitive, plus aliases: `ctrl`
(`control`), `alt`, `shift`, `super` (`meta`, `win`, `cmd`), `enter`
(`return`), `esc` (`escape`), `backspace`, `delete` (`del`), `tab`,
`space`, `up`, `down`, `left`, `right`, `home`, `end`, `pageup`,
`pagedown`, `f1` to `f24`, `plus`, and single characters. An element such as
`"ctrl+l"` is split at `+`, and a lone `"+"` means `plus`. A letter names its
key, so `["shift", "a"]` types a capital. `modifiers` take only `ctrl`, `alt`,
`shift` and `super`.

Every batch is checked as a whole before anything is sent: arguments,
coordinates and capabilities by the server, then key names, typeability of
every character with the current keymap, and the pointer regions by
`hyprmasq-input` once it has connected. These last checks run after the locks
are taken, but they too fail the call with nothing sent.

A `drag` presses, moves and releases, which works for sliders and selections.
A Wayland drag-and-drop that the application starts is cancelled: the plugin
lets only the active window start one (its `dnd` capability), and the active
window lacks `hold`, which `act` needs for a drag. So `windows` does not report
`dnd`.

### Batching

A batch saves round trips but runs without looking. The `act` description
teaches one rule: **batch only actions whose targets are visible in the
current shot.** Clicking a visible field, typing and pressing Enter is one
batch. A menu, dialog or page that opens needs a new shot before anything in
it is clicked. Keys whose effect is known may still follow in the same batch,
for example `alt+f`, then `down` and `enter` in an app whose menu you have
seen. In a window without the `lease` capability, a menu opened in one call is
gone by the next, so the description says to open it and pick the entry with
the keyboard in one batch.

### Pacing

One `hyprmasq-input` runs the whole batch, paced as described in
[usage.md](usage.md#pacing): 50 ms between actions, a press at least 30 ms
after its motion, buttons held 50 ms, the clicks of a double click 80 ms
apart, chord keys 10 ms after their modifiers and held 15 ms, typed keys held
5 ms and 10 ms apart, wheel notches 20 ms apart, and drags in steps of at most
10 logical pixels over at least 200 ms.

The server sends a `click` as two sender actions, `move` and `click`, so its
motion comes 50 ms before the press. A `scroll` is likewise `move`, then
`wheel` or `scroll`. `type` becomes `text`, `key` becomes `keys`, and `pause`
becomes `sleep`.

The first `type` or `key` after a `click` or `drag` in a batch starts at least
300 ms after the click or drag has ended. Applications move the keyboard focus
a moment after a click, and keys that come sooner can reach the window's
shortcuts instead of the field: Spotify, clicked into its empty search field
on a playlist page, took a space typed 200 ms after the click as play/pause,
and typed it when it came 350 ms after the click. When the sender actions in
between, counting the 50 ms before each of them and any `pause` but not how
long motion or scrolling take, add up to less, the server puts a `sleep` in
front of the keys, which belongs to their action, so a deadline
that stops it reports that action. It counts toward the batch's pace like any
other `sleep`. The `ms` description of `pause` tells agents that they need no
pause of their own for it.

The sender waits for the plugin after every action and every 16 frames. These
round trips confirm that Hyprland processed the events, not that the
application is ready for more.

From these delays the server computes the least time a batch takes to send,
and adds 10% for the round trips: typing measured about 15.5 ms a character in
the VM. Input has to end 1250 ms before the capture's reserve: a sender that
runs late gets SIGTERM then, and takes up to a second, its `CANCEL_MS`, to
release what it holds and report (see [hyprmasq-input](#hyprmasq-input)), so
the call still ends by its deadline. A batch that needs more than
`deadline_ms` − 2250 is refused with `invalid_arguments` before any lock is
taken, so a batch with actions needs a `deadline_ms` above 2250. The hint names
a `deadline_ms` that fits, or says to split the batch when none up to 30000
does. A typed character takes at least 15 ms, so one call types about 600
characters with the default deadline and about 1400 with `deadline_ms` 30000,
as the `act` description says. These estimates and the hint's `deadline_ms`
allow 25% for the round trips and time for a short wait, so they always pass
the check. If waiting for the locks left too little time, the call fails with
`deadline` and sends nothing. `deadline_ms` 1000 leaves no time to wait: an
empty batch only captures.

The server counts a key for every character, but a character without a key of
its own takes more: `hyprmasq-input` types it through Compose, as a dead key
and Space or a longer sequence, which only it knows once it has the keymap (see
[Typing text](usage.md#typing-text)). So the server passes the sender the time
left for input, less the 10% for the round trips, as `--budget`, and the sender
adds up the delays of the keys it resolved the batch to before it sends
anything. A batch that does not fit is refused with nothing sent, in the same
way as by the server's own check: with `invalid_arguments` and a `deadline_ms`
that fits, or the advice to split the batch, when the deadline cannot fit it,
and with `deadline` when waiting for the locks took the time. On a German
layout, 40 times `^` are 80 keys, which take at least 1190 ms.

The application turns such a sequence into a character with the Compose table
of its own locale, and the tables of locales differ: in `pt_BR.UTF-8` the dead
acute and `c` give `ç`, in `en_US.UTF-8` `ć`. So for a batch with `type`, the
server reads the locale of the window's process, the first of `LC_ALL`,
`LC_CTYPE` and `LANG` set in `/proc/<pid>/environ`, or C, and passes it to the
sender as `--locale`. Hyprland names the process of a Wayland window by its
connection, and of a window of its own XWayland through XRes. When it names
none, the environment cannot be read, or the window belongs to
xwayland-satellite, whose windows all name the satellite's process, the server
passes no locale and the sender uses its own. When characters were typed
through Compose and the window's locale was not known, or the sender had no
table for it and used that of `en_US.UTF-8`, the hint says which table typed
them and to check them in the shot.

## Waiting

### Definitions

- **Commit:** a commit on any surface of the window (its toplevel, subsurfaces
  and popups) that carries a new buffer or damage or unmaps the surface, or a
  popup or subsurface appearing or going away.
- **Commit counter:** a per-window counter of commits that the plugin keeps
  while the window has an open socket. The server opens a window's socket
  before its first shot and never closes it. The counter starts again at 0 when
  someone else closes the socket and it is opened again. Every socket has its
  own id, which the plugin reports with every counter value, so values of
  different sockets are never compared.
- **Baseline:** the counter value that a change is measured against. For a
  batch with actions, the server reads it once it holds the window's lock and
  the send lock, right before it starts the sender, so what the window draws
  while the call waits for input to other windows does not count. For an empty
  batch, it is the counter of the given shot, so a change means "changed since
  the screenshot you looked at"; without a shot, it is read when the call
  starts. A shot of an earlier socket was taken before the current socket
  opened, so every commit that the current socket counted came after it: the
  change is then measured from 0. A wait that reads the counter of a socket
  opened after its baseline was read runs again from 0, within the time left.
- **Applied:** the plugin has processed every event of the batch (a libei
  round trip after every frame), or the sender has reported which it did not.
  Applied does not mean the application accepted or handled the input.

### Phases

After the last action has been applied, the server starts the plugin's
`wait … since` with the baseline:

1. **Change phase**, only with `expect_change: true`. Ends at once if the
   counter is already above the baseline, otherwise at the first commit after
   it. If none arrives within `expect_ms`, the wait ends with `no_redraw`. A
   quiet window does not count as settled while it is still waiting for a
   change.
2. **Quiet phase.** Ends once there has been no commit and no input for
   `quiet_ms`, counted from the latest of the last commit, the last input and
   the start of the wait, and the window has caught up with any resize (the
   plugin's `resizing`). The wait ends with `quiet`. An animation of
   Hyprland's, such as a window popping in, does not keep it from ending,
   since shots draw the window as it is once the animation has ended.

With `expect_change: false` only the quiet phase runs. Use it for input that
may legitimately change nothing on screen: hover, harmless keys, scrolling at
the end of a list.

The wait's own deadline is the call's deadline less the capture's reserve, so
`deadline_ms` is what limits a wait, and `expect_ms` has to fit in it. A
window that is already drawing ends the change phase at once, after which
`expect_ms` no longer matters. An empty batch with a large `expect_ms` and a
`deadline_ms` at least 1000 above it is the explicit wait, for example for a
page to load: `{"window": "…", "shot": "s7", "wait": {"expect_ms": 20000},
"deadline_ms": 30000}`. When sending or waiting for the locks left no time,
the wait does not run, and the summary says `no time left to wait`.

`ended` is one of:

| `ended` | When | `summary` |
| --- | --- | --- |
| `quiet` | Quiet phase completed | `settled` |
| `no_redraw` | No commit within `expect_ms` | `no_change` |
| `timeout` | The deadline arrived in either phase, or no time was left to wait | `resizing` when the window had not caught up with a resize, otherwise `busy` after a redraw, otherwise `no_change` |
| `window_closed` | The window closed | |
| `cancelled` | The client cancelled the call | |

`window_closed` wins over `cancelled`. When a phase's ending and the deadline
fall together, the phase's ending wins. A timeout without a redraw, when the
change phase ran into the deadline or no time was left to wait, is
`no_change` like `no_redraw`, since the call saw no redraw.

A capture is taken in every case except `window_closed`.

### Frame callbacks

A window on a hidden workspace gets no frame callbacks from Hyprland, and many
applications stop drawing without them. The plugin sends a hidden window frame
callbacks every 16 ms:

- for a second after each input;
- while a sender is connected to the window's socket and has its devices.
  `hyprmasq-input` keeps them until it exits, also through a `pause`;
- while a `wait … since` runs, also for a window that is hidden only after the
  wait started.

Together they cover the batch and its wait. The wait's callbacks stop when the
wait ends, also when it is cancelled.

That is the whole guarantee: the window can draw. It does not make the
application behave as if it were visible. Some throttle in other ways.

### What the wait does not tell

- Work an application does without drawing, such as a network request, is
  invisible. A `settled` window can still be loading. The description of
  `wait.quiet_ms` says so, and to act again with no actions while the shot
  still shows loading.
- An application that draws in bursts further apart than `quiet_ms` can settle
  between two bursts.
- A commit is not proof that the pixels changed, and not proof that the input
  did what the agent wanted. Only the screenshot shows that.
- X11 menus and tooltips are separate windows and do not count as commits of
  the app's window. They show up as new windows in the result, if they appear
  before the call ends.

## Focus lease

Without a lease, a driven window loses its emulated pointer and keyboard focus
as soon as `hyprmasq-input` exits. An agent's next call usually comes several
seconds later, by which time dropdowns, hover menus and popovers that close on
focus loss are gone.

With a lease, the plugin keeps the emulated pointer (at its last position)
and keyboard focus on the window between calls:

- An `act` that sends input to a window with the `lease` capability starts or
  renews a lease of `--lease-ms` (60 seconds), owned by this server session.
  The lease keeps the focus that input gives and gives none by itself, so the
  server takes it before sending: once it holds both locks and has checked the
  window again, right before it starts the sender. It renews it once more
  after the capture, and the result reports that renewal.
- A batch that ends up sending nothing undoes its renewal: it releases a lease
  that was not held before, and gives a lease of another session back to that
  session for the time it had left. A lease of this session keeps the renewal.
- An `act` without actions renews a lease that is held, but starts none. A
  call that is cancelled, or runs out of time, before it starts the sender
  touches no lease.
- Renewing a lease that another session holds takes it over.
- It ends with `release` and `close` (whichever session holds it), when the
  window closes, when the socket closes, when its time is up, when the server
  session that owns it ends, and when it becomes unsafe.
- Unsafe: the real keyboard focus moves to a window of the same Wayland
  connection, or input is delivered to another window of that connection. The
  plugin ends the lease the moment either happens. X11 windows never get one,
  since holding X focus would take the user's typing in their X11 apps.
- While the real pointer is on a window of the same connection, the lease
  keeps only the keyboard focus.
- No key or button stays pressed between calls. The sender releases
  everything it pressed, and the plugin releases whatever a sender left held.
- The application shows itself as focused (caret, active styling) while the
  user works elsewhere, and the virtual cursor stays on a visible leased
  window. The user's real input is not affected.

Windows without the `lease` capability lose the focus when the batch ends. The
`act` description tells agents to use the keyboard for menus in such windows,
since a menu opened in one call is gone by the next. It never suggests
clicking where a menu item probably is.

## Results

A result has three parts:

1. Text: a one-line summary, for a changes shot a line for each zoom and one
   for the tiny changes left out, then a hint when one is needed:

   ```
   3/3 delivered · redraw observed · quiet 214 ms · 655 ms · shot s5 1280×800 · lease 60 s
   ```

   ```
   1/1 delivered · no redraw within 1000 ms · shot s6 = s5, no change · lease 60 s
   The click may have missed or had no visible effect. Look at s5 before repeating it.
   ```

   ```
   2/2 delivered · redraw observed · quiet 200 ms · 512 ms · shot s7 = s6 + 1 change · lease 60 s
   zoom s8 214×98 of s7 at 40,120 107×49
   ```

   The parts, joined by ` · `:

   | Part | Forms |
   | --- | --- |
   | Actions, if any | `N/M delivered`, with `, nothing sent`, `, delivery partial: actions[i] (type) may be partly applied (K keys sent)` or `, delivery uncertain from actions[i] (click) on` |
   | Wait | `redraw observed · quiet N ms · N ms`, `no redraw · quiet N ms · N ms`, `no redraw within N ms`, `timeout after N ms, still resizing`, `timeout after N ms, still drawing`, `timeout after N ms, no redraw`, `no time left to wait`, `window closed`, `cancelled` |
   | Capture | `shot sN W×H`, `shot sN W×H (full shot: REASON)`, `shot sN W×H (stale: not yet redrawn at its new size)`, both as `(full shot: REASON; stale: …)`, `shot sN = sM + K changes` followed by the lines `zoom sZ W×H of sN at X,Y W×H` and `K tiny changes left out`, `shot sN = sM, no change`, or `capture failed: MESSAGE` |
   | Lease | `lease N s` or `no lease` |
   | New windows | `new window ADDR (CLASS "TITLE"[, launched][, same pid])`, or `new windows` with a list |

2. The image, when one was captured: the shot, or the zooms of a changes
   shot in the order of their lines.
3. Structured content, except for clients that would pass it to the model in
   place of the text (see below): the summary and the hint again, in
   `summary` and `hint`, and the facts in fields, for scripts and for pi's
   codemode:

```json
{
  "summary": "3/3 delivered · redraw observed · quiet 214 ms · 655 ms · shot s5 1280×800 · lease 60 s",
  "actions": {"done": 3, "total": 3, "delivery": "complete"},
  "wait": {"ended": "quiet", "summary": "settled", "redraw_observed": true, "commits": 7, "quiet_ms": 214, "elapsed_ms": 655},
  "shot": {"id": "s5", "width": 1280, "height": 800, "frame": {"x": 0, "y": 0, "width": 640, "height": 400}},
  "lease": {"held": true, "remaining_s": 60},
  "new_windows": [{"window": "0x55d1c0b11230", "stable_id": "1a", "class": "org.gnome.Nautilus", "title": "Save As", "pid": 4242, "same_pid": true, "launched": true}]
}
```

A changes shot's `shot` has the base's `width`, `height` and `frame`, and
`changes`: `{"base": "s6", "regions": [{"shot": "s8", "image_width": 214,
"image_height": 98, "x": 40, "y": 120, "width": 107, "height": 49}],
"left_out": 1}`, with `left_out` only when tiny changes were left out. A full
shot that capture `"changes"` took in their place has `full_shot` with the
reason.

`hint` is left out when there is none, and `shot.resizing` and
`wait.resizing` unless they are true. `wait.resizing` is true when the wait
timed out while the window had not caught up with a resize. `actions` also
has `failed_index` and `progress` (`{"keys": N}` for a `type` or `key`
action) when an action failed.
`wait.commits` counts the commits since the baseline, and `wait.quiet_ms` how
long the window had been quiet when the wait ended. An error adds `error`
(`code`, `message`), and a failed capture after applied input adds
`capture_error`.

`new_windows` lists every window in the window list at the end of the call
that was not there at its start, including ones whose relation to the target
is unknown. `same_pid` (the target's process) and `launched` (the process
carries the launch token of a `launch` by this server) are the only relations
claimed. A window that maps after the call's last listing is not reported by
any later call, since each call compares its own start and end.

The summary counts actions as delivered: the plugin passed every event to the
window's application. Whether the application acted on them as meant, for
example a game or a web app that drops keys while it is busy or takes them as
shortcuts, only what changed on the window can show, and the `act`
description says so.

Clients differ in what they pass the model: Codex and pi pass the text and
the image, Claude Code the structured content as JSON and the image, without
the text. The JSON repeats the summary in fields and, for `windows`, lists
facts the lines leave out, so it costs Claude Code more tokens than the text,
for a list of windows about twice as many. With `--structured-content auto`, the default, the server therefore
gives results no structured content when the client introduced itself as
`claude-code` at initialization, so Claude Code passes the text and the image
like the others. The tools declare no output schema, so the structured
content is optional. `always` gives it to every client, `never` to none.
Where it is given, the structured content of every result carries all of its
text: `error` the error line, `summary` the summary and `hint` the hint, and
the fields hold every fact of the summary.

The other tools answer in the same way:

| Tool | Text | Structured content |
| --- | --- | --- |
| `windows` | One line per window: `ADDR · CLASS "TITLE" · workspace NAME (hidden\|visible) · W×H [· resizing (not yet redrawn at its new size)] [· xwayland] [· launched] [· lease N s (this session\|another session)] [· lacks lease, hold] [· keyboard x11-focus]`, naming only what the window lacks of `lease`, `hold` and `keyboard yes`, so a line without them has them all, and `capabilities unknown` when the plugin did not answer for it. With `match` and no window matching: `no window has "TEXT" in its class or title, of N windows`. | `{"windows": [...]}`, with `stable_id`, `pid`, `capabilities` `{"lease", "hold", "keyboard"}` and every other fact of the lines, and when it lists none, `summary` with the line that says why |
| `screenshot` | `shot sN W×H · window ADDR W×H`, with ` · zoom of sM at X,Y W×H` after the shot for a zoom, and ` (stale: not yet redrawn at its new size)` after the shot when it shows an older frame. | `{"summary": "...", "shot": {...}}` |
| `launch` | `launched ADDR CLASS "TITLE" · pid N · workspace NAME · settled after N ms · shot sN W×H`, or `still resizing after N ms` when the window had not caught up with a resize, `still drawing after N ms`, or `not settled after N ms, no redraw` when input from elsewhere kept the window from counting as quiet, with ` (stale: not yet redrawn at its new size)` after the shot when it shows an older frame, then other new windows. The hint names the windows on its workspace whose size changed. | `{"summary": "...", "window": {...}, "wait": {...}, "shot": {...}, "new_windows": [...]}` |
| `release` | `lease released; ADDR stays open`, `lease of another session released; ADDR stays open`, or `no lease was held on ADDR`. | `{"summary": "...", "released": true}` |
| `close` | `closed ADDR after N ms`, or `ADDR is still open after N ms`, then new windows. The hint says to take a screenshot of a window that stays open, or names the tiled windows that take up the space of a tiled window that closed. | `{"summary": "...", "hint": "...", "closed": true, "elapsed_ms": 140, "new_windows": []}` |

During a call that asked for progress with a `progressToken`, the server sends
a progress notification about once a second: `progress` is the milliseconds
elapsed, `total` the deadline (for `launch`, `timeout_s`), and `message` the
phase: `waiting for other calls on the window`, `waiting for input to other
windows`, `sending input`, `waiting for the window to settle` or `capturing`,
and for `launch` `waiting for the first window`. pi resets its tool timeout on
progress. Other clients may not, which is one reason for the deadline cap.

## Failures

### Delivery

| `delivery` | Meaning |
| --- | --- |
| `none` | Nothing was sent. Safe to fix and retry. |
| `complete` | Every action was delivered: the plugin passed every event to the window's application. Whether the application acted on them as meant, only the shot can show. |
| `partial` | Actions before `failed_index` were applied; the action at `failed_index` may be partly applied, for example some characters of a `type`. The plugin took everything that was sent. Where known, `progress` says how much: the keys the plugin processed, not the characters the application inserted. |
| `uncertain` | The connection ended, or the plugin stopped answering, before the plugin had taken everything that was sent. Any action from `failed_index` on may or may not have been applied. |

`done` counts the batch actions whose every event was delivered. After input
was sent, a failed batch reports `done` equal to `failed_index`; with nothing
sent, `done` is 0, and the message names the action that was refused.

Hints never suggest repeating input whose delivery is `partial` or `uncertain`.
Every error hint of an `act` with actions ends with `Nothing was sent.` when
the delivery is `none`, and otherwise with `Look at the shot before repeating
anything.`, `Look at the zooms before repeating anything.` for a changes shot,
`Look at sM before repeating anything.` for one that found no change since sM,
or `Take a screenshot and look at it before repeating anything.` when no shot
is attached. An `act` without actions sends nothing, and its hints
do not say so. The `act` description adds that input of a call that timed out
is not repeated without a look at a fresh screenshot, since its outcome is
unknown.

When the window is known, a failure before any input was applied comes with a
fresh shot of the window, unless `capture` is `"none"`, so the agent can go
on without another call. With `capture` `"changes"` it is a full shot, since
the agent reads coordinates anew. `stale_shot` then says to read the coordinates from
the new shot. Failures in the arguments themselves, `window_gone`,
`plugin_missing`, `plugin_version` and `cancelled` come without one.

A failed capture after applied input is not an error: the result reports the
input outcome and a `capture_error`, so an agent does not repeat a submit
because its screenshot failed.

### Errors

Errors are tool results with `isError: true`. The text is
`error <code>: <message>`, then for `act` the summary line when something was
sent or a shot is attached, then the hint. The structured content has `error`
(`code`, `message`), `summary` when the text has one, and `hint`, and for
`act` also `actions`, `new_windows` and, when there is one, `shot`.

| Code | Delivery | Meaning and hint |
| --- | --- | --- |
| `invalid_arguments` | none | Names the field by its path, for example `actions[0].count must be 1 to 3, not 5`, `actions[0].from must be [x, y] in shot pixels, not [100]` or `actions[0].x must be a number, not null`, or the key name the sender did not know. Also a batch that its `deadline_ms` cannot fit, also once the sender has resolved its keys with the keymap, or a `wait.expect_ms` that does not fit in it, naming the `deadline_ms` needed. |
| `window_gone` | none, partial or uncertain | The window closed or its `stable_id` changed. Call `windows`. When it closed while the input was sent, maybe because of it, `wait.ended` is `window_closed` and no shot is taken. When the batch's last action closed it and the plugin had confirmed that action, the result is no error: it reports `complete` and `wait.ended` `window_closed`. |
| `stale_shot` | none, partial or uncertain | Unknown shot, another window's shot, a shot of an older frame, a resize since the shot, while the call waited for the locks, or during the batch, or a zoom into a popup that has closed or into a window that was resized since the zoom's shot. Read the coordinates from the attached shot, or take a screenshot. |
| `out_of_bounds` | none or partial | A coordinate lies outside the shot, left of or above the window, or outside the pointer regions, which end at the monitor's edges. Names the action. With `partial`, the window moved during the batch and a later point no longer lies in the new regions. |
| `cannot_type` | none | The keymap cannot produce a character. Names it, for example U+00E9. |
| `unsupported` | none | The window lacks a capability the batch needs: `hold` for a `drag`, a `click` with modifiers, or a `key` chord that holds a key other than a modifier. Says why and suggests the keyboard or a click without modifiers, or for a chord, holding only modifiers or separate `key` actions. |
| `input_paused` | none or partial | X11: the user is in another window of the same X server. With nothing sent: retry later, or ask the user. Otherwise: go on once the user has left it, or ask the user. |
| `deadline` | none, partial or uncertain | The deadline arrived before the input was sent, for example while waiting for a lock, or too little time was left after the locks to send the batch, or it arrived while the input was being sent. After a partial send, the hint says to send the rest with a larger `deadline_ms` or in parts. For `launch`, no window appeared within `timeout_s`. |
| `cancelled` | none, partial or uncertain | The server is shutting down, a `launch` or `close` was cancelled, or another process stopped the sender with a signal. A client's cancellation of an `act` is no error; see [Cancellation](#cancellation). |
| `unconfirmed` | partial or uncertain | From the sender: input stopped before the plugin confirmed it, because the window moved or was resized while it was sent, another sender took over the socket, or the plugin stopped answering. |
| `connect_failed`, `no_devices` | none | From the sender: the window's socket did not take input. Retry once, then tell the user. |
| `plugin_missing`, `plugin_version` | none | The plugin is not loaded, was unloaded, Hyprland's socket cannot be reached, or the plugin has another interface than this server. Tell the user. |
| `internal` | any | Anything else, such as a sender that cannot be started or gave no result, or a window that was resized during each of 3 capture attempts. |

A request to Hyprland that fails because the call's own deadline or
cancellation ended it reports `deadline` or `cancelled`, not
`plugin_missing`. A sender that the server stopped before it connected
reports delivery `none`.

`no_redraw` and `timeout` are not errors. They are wait outcomes with a
screenshot and a hint: for `no_redraw`, what may have happened to the last
action, or for an empty batch, to wait again with a larger `expect_ms` and a
`deadline_ms` at least 1000 above it; for `timeout`, whether the window kept
drawing or drew nothing, to look at the shot, and to wait longer with an empty
batch and a larger `deadline_ms`, or, for a window that had not caught up with
a resize, only the hint about the shot's older frame.

### Cancellation

When the client cancels a call (`notifications/cancelled`), the server stops
what it is doing:

- waiting for a lock: it stops waiting, nothing is sent, and no lease is
  touched;
- sending: it stops the sender with SIGTERM, which releases every button, key
  and modifier it pressed and reports how far it got, within about a second;
- waiting: it sends the plugin `cancel` with the wait's id, which ends the
  wait and its frame callbacks.

It then still captures, within at least the capture's reserve, and answers
with `wait.ended: cancelled`, the delivery so far and the shot. The SDK sends
that answer, which a client that cancelled may ignore. The focus lease stays,
since it holds no pressed input.

## Concurrency

Agents call tools in parallel, and several agents can share one desktop.
Two kinds of file lock under `$XDG_RUNTIME_DIR/hyprmasq/<instance>/`
coordinate every `hyprmasq-mcp` process:

| Lock | Held | Prevents |
| --- | --- | --- |
| `window-<stable_id>.lock` | By every `act`, from before the baseline is read to the end of the capture and the lease renewal | Another call changing the window during this call's wait. |
| `send.lock` | By an `act` with actions, from before it checks the window again and reads the baseline until `hyprmasq-input` has exited | Two senders at once, which would fight over focus in shared Wayland connections and X servers. |

A call takes the window lock first, then the send lock. Waits and captures of
different windows run at the same time. Waiting for a lock counts against the
deadline, up to the capture's reserve, or for a batch with actions up to the
end of the time for input; a call that gets no lock in time, or too little time
to send its batch after them, fails with `deadline` and sends nothing.
`screenshot`, `windows`, `launch`, `release` and `close` take no lock.

Scripts that run `hyprmasq-input` directly take neither lock, and a new sender
on a socket still replaces the previous one. Their input can disturb an
agent's observations.

## Session lifecycle

The session ends when the client closes the server's stdin or its stdout, and
on SIGINT, SIGTERM and SIGHUP. The server then cancels the calls still running,
which end as cancelled without a capture or a lease renewal, and refuses new
ones with `cancelled`. A `launch` cancelled while its window settles returns
`cancelled`, naming the window. The server waits up to 2 seconds for the calls,
then releases the leases it may hold, with 2 seconds of their own: those it
took, and those whose lease or release request got no answer, since it may have
taken effect. It deletes its shot directory and exits with status 0, or with
status 1 and a `hyprmasq-mcp: …` message on stderr when the session ended on a
transport error: a line on stdin that is not JSON-RPC, or a stdout that cannot
be written. Sockets stay open: another session may be using them, and the
plugin removes them when their window closes. Windows started with `launch`
stay open. Only `close` closes windows.

At start, the server deletes the shot directories of server processes that no
longer run.

## Self-documentation

What each client shows the model:

| Client | Server `instructions` | Tool descriptions | Results |
| --- | --- | --- | --- |
| Claude Code | In the system prompt, in full | Once tool search loads the tool, cut after 2048 characters | The structured content as JSON and the image, not the text, so the server gives it none and it passes the text |
| Codex | In front of every tool's description | Yes | The text and the image |
| pi | None with `direct` exposure, which [Installation](installation.md#mcp-server) registers. Only servers with `codemode` or `deferred` tools get the first line in the system prompt, and the rest through `describeNamespace()` in codemode scripts. | Yes, with `direct` exposure | The text and the image |

So:

- **Tool descriptions stand on their own.** Each states its units, the
  coordinate rule and, for `act`, the batching rule, with one example call.
  They carry every rule an agent must follow, so a client that shows only the
  descriptions, such as pi, loses none: `act` the rules to drive only windows
  the user named or the agent launched and to ask before anything
  irreversible, the batching rule and the rule against repeating input,
  `close` the rule to close only windows the agent launched or the user asked
  it to close, and `launch` the rule to close launched windows.
- **Each description fits in 2048 characters**, after which Claude Code cuts
  it off. Reference that belongs to one parameter is in that parameter's
  description instead: the key names, the defaults of `wait`, the forms of
  `capture` and what `deadline_ms` covers. A Go test checks the length.
- **The instructions teach the loop**, and to read each result's summary and
  hint. Their first line is a complete summary. They say nothing that a
  description says, and are short, since Codex puts them in front of every
  tool's description.
- **Results teach the rest.** Every result says what happened in one line, and
  every error and every `no_redraw` or `timeout` carries a hint with the next
  step, in the text and in the structured content alike. There is no help
  tool.

The instructions:

```
hyprmasq: see, click and type in Hyprland windows without disturbing the user's screen; one act call acts, waits for the redraw and returns screenshots of what changed.

Loop: windows or launch → screenshot → act with coordinates in that shot's pixels → read the changes or the shot that act returns → act again.
Read each result's summary, and its hint when it has one, before the next call.
```

The `act` description, with the character counts for the default deadline and
for 30000:

```
Act on a window, wait until it has redrawn, and return what changed. Drive only windows the user named or you launched, and ask the user before anything irreversible: sending, deleting, buying, submitting.
Coordinates are pixels of the shot given in "shot" (required for click, move, drag, scroll).
Actions: click {x,y,button,count,modifiers} · move {x,y} · drag {from,to,button,modifiers} · scroll {x,y,dx,dy,unit:notch|px} · type {text} · key {keys:["ctrl","l"], repeat} · pause {ms}.
The result shows what changed since "shot" or your last shot: "shot s7 = s5 + 2 changes" has s5's pixels, so keep reading coordinates from s5; each change is a zoom shot, listed with its box in them. A full shot comes when it serves better, saying why; capture "window" asks for one.
Batch only targets visible in the shot: a menu, dialog or page that opens needs a new shot before you click in it; keys you know, such as arrows and enter in a menu, may follow. Without the lease capability (see windows), a menu opened in one call is gone by the next: open it and pick the entry by keyboard in one batch.
drag, clicks with modifiers, and chords holding a key other than a modifier while pressing the next need the hold capability.
type uses the active keymap; a line break presses Enter and a tab presses Tab, so for a new line in a chat or form field use a key action such as shift+enter. At one key per 15 ms, and two or more through Compose for a character without a key, a call types about 600 characters, or 1400 with deadline_ms 30000; split longer text.
Delivered input reached the application but may not have taken effect: a busy one, such as a game or a web app, can drop keys or take them as shortcuts, so check typed text in the result.
Never repeat input whose delivery is partial or uncertain, or from a call that timed out, before looking at a fresh screenshot.
Example: {"window":"0x55d1c0a3e4b0","shot":"s4","actions":[{"action":"click","x":412,"y":88},{"action":"type","text":"hello"},{"action":"key","keys":["enter"]}]}
```

The other descriptions are in `mcp/internal/server/server.go`. The
[skill](../skills/hyprmasq/SKILL.md) keeps documenting the shell API, and
tells agents to use the hyprmasq MCP tools when the session has them.

## Interfaces

The exact interfaces between the three components. The MCP server is written
against these, so changes to them change this section first.
[usage.md](usage.md) describes the same commands for people.

### Plugin commands

New commands and options use plain keywords, because `hyprctl` takes every
argument that starts with a dash as its own flag. Existing syntax keeps
working. Every JSON error is `{"ok":false,"error":"<message>","code":"<code>"}`,
and plain-text errors still start with `error:`.

| Code | Meaning |
| --- | --- |
| `invalid_arguments` | Malformed command or arguments. |
| `window_gone` | No mapped window at that address. |
| `no_socket` | The command needs an open socket for the window (`eis open`). |
| `unsupported` | Not possible for this window, for example a lease on an X11 window, or `wait` outside Hyprland's request socket. |
| `internal` | Rendering, allocation, file or other failures inside the plugin. |
| `unloaded` | The plugin was unloaded while the request was pending. |

**`version`**

```json
{"ok":true,"version":"0.3.0","interface":2}
```

`interface` increases with every incompatible change to these commands. New
fields in replies are compatible: `socketId` and `resizing` came without a new
interface. The server reads replies without `socketId` as from a plugin that
names no sockets, and without `resizing` as of a window that has caught up.
The server refuses to run against another interface (`plugin_version`).

**`state <addr>`**

```json
{"ok":true,"address":"0x55d1c0a3e4b0","stableId":"1a","xwayland":false,
 "size":{"width":640,"height":400},"resizing":false,"scale":2,
 "socket":"/run/user/1000/hyprmasq/<instance>/0x55d1c0a3e4b0.eis","socketId":"9f3a61c2-3",
 "commits":123,"lease":{"held":true,"remainingMs":58210,"owner":"mcp-4242-a1b2c3d4"},
 "capabilities":{"lease":true,"hold":true,"dnd":false,"keyboard":"yes"}}
```

- `stableId` is Hyprland's `m_stableID`, formatted as `hyprctl -j clients`
  does.
- `size` is the window's logical size as `hyprctl -j clients` reports it,
  `scale` the monitor's scale, or `null` without a monitor.
- `resizing`: the window has not caught up with the size Hyprland gave it. For
  a Wayland window, Hyprland's size for it, floored, differs both from the
  last size it acked before a commit, which Hyprland records with every
  configure, and from the size of its committed content: the xdg window
  geometry when it sets one, otherwise its surface. The second check keeps a
  window that acked a configure of its current size and had nothing to commit
  from counting as resizing. For an X11 window, Hyprland's last configure
  differs both from the size it took as reported at the window's last commit
  and from the surface's size. A window that acks a size and then commits a
  frame of the old size is not caught.
- `socket`, `socketId` and `commits` are `null` without an open socket.
  `lease` is `null` when none is held.
- `socketId` names the socket: it differs for every socket the plugin opens,
  also for one opened again for the same window, and in every load of the
  plugin. Plain text prints it as a `socketId:` line.
- `capabilities`, as observed now:
  - `lease`: a Wayland window that does not share its Wayland connection with
    the window that has the real keyboard focus. Windows of Hyprland's
    XWayland and of xwayland-satellite count as X11.
  - `hold`: sharing its Wayland connection with neither the real keyboard
    focus nor the real pointer focus. Same-connection input restores the
    user's focus after every input frame, which releases held buttons and
    gives a click the user's modifiers instead of its own. A keyboard frame
    enters the window again with no keys held, only with the modifier state
    of the keys the sender holds, so of the keys of a chord only modifiers
    stay pressed.
  - `dnd`: the window is the active window. Drags from other driven windows
    are cancelled. `hyprmasq-mcp` does not report it.
  - `keyboard`: `x11-focus` for X11 windows of Hyprland's XWayland or
    xwayland-satellite whose socket was opened without `force`, or that have
    no socket, otherwise `yes`.

**Commit counter.** From `eis open` until the socket closes, the plugin counts
the window's commits as defined in [Waiting](#definitions). It never falls
while the socket is open, and starts again at 0 for a new socket, which has a
new `socketId`. It never misses a popup that opened between scans: the plugin
watches for new popups and subsurfaces continuously, not only around input. The
existing `frames` and `framesSinceInput` fields of the old `wait` count the
same commits.

**`screenshot <addr> <path> [X Y WIDTH HEIGHT] [max PIXELS] [popups]`**

- `max PIXELS`: fit the image within PIXELS on its long side, never above the
  monitor's scale. The plugin renders at the monitor's scale and shrinks on
  the GPU: halving passes, then one bilinear pass. With `max`, image pixels
  start at the region's origin, so the region's `x` and `y` are whole logical
  pixels and only its size is widened, to whole image pixels.
- The window is drawn as it is once its animations have ended: at its size
  from `state`, which input uses, not at the size that Hyprland animates it
  through and fits its content to on screen, and without window transformers
  such as motion blur. Animations that move or fade a window do not change
  shots either.
- `popups`: capture the window together with its Wayland popups, also where
  they reach outside the window, but only within the bounding box of the
  window and its monitor. Without a region the captured box is the bounding
  box of the window and its popups. With a region, the region may extend into
  that box, so X and Y can be negative; it is cut to the box, and a region
  entirely outside the box is `invalid_arguments`.
- Popups of windows on hidden workspaces are captured too, with or without
  `popups`.
- The reply's `scale` is image pixels per logical pixel, so it is below the
  monitor's scale when `max` shrank the image. `region` is the captured box
  in window-local logical pixels, and can start at negative coordinates with
  `popups`. `commits` is the counter read when the window was rendered, not
  when the PNG was written, and `socketId` the socket it counts for; both are
  `null` without a socket. `resizing` is the window's `resizing` from `state`
  when it was rendered: the image has the window's size but shows a frame of
  another size.
- A box larger than the GPU's texture limit is `internal`.

**`wait <addr> since COMMITS [expect MS] [quiet MS] [deadline MS] [id WORD]`**

The new form. `wait <addr> [QUIET-MS [TIMEOUT-MS]]` keeps working as before.
Needs an open socket (`no_socket` otherwise) and a request through Hyprland's
socket (`unsupported` otherwise). Each option may come once, in any order.

- With `expect`, a change phase runs first: it ends at once when the counter
  is already above COMMITS, otherwise at the first commit, and the wait ends
  with `no_redraw` when `expect` milliseconds pass without one.
- The quiet phase ends once there has been no commit and no input through the
  socket for `quiet` milliseconds (default 200), counted from the later of the
  last commit and the last input, but not before the wait started, and the
  window is not `resizing`. A client waits for a window to catch up with a
  resize with a wait without `expect`.
- `deadline` (default 5000) ends the wait with `timeout` in either phase. A
  phase's ending wins a tie with it.
- `expect`, `quiet` and `deadline` are 1 to 60000.
- While the wait runs, a window that is not rendered gets frame callbacks
  every 16 ms, also one that stops being rendered during the wait. They stop
  when the wait ends.
- `id WORD` names the wait for `cancel`. An id of a pending wait is
  `invalid_arguments`.
- COMMITS above the counter is `invalid_arguments`.
- A socket that closes during the wait does not end it.
- The wait never blocks Hyprland's main thread. In a `hyprctl --batch` whose
  reply another command sends at once, such as `plugin load`, the wait runs
  on unanswered until it ends or is cancelled. If the plugin cannot start a
  thread for it, the reply is `internal`.

```json
{"ok":true,"ended":"quiet","commits":131,"socketId":"9f3a61c2-3","redrawObserved":true,"commitsSince":8,"quietMs":200,"elapsedMs":412,"resizing":false}
```

`ended` is `quiet`, `no_redraw`, `timeout`, `window_closed` or `cancelled`.
`commits` is the counter when the wait ended, `socketId` the socket whose
counter the wait read, `commitsSince` the commits after COMMITS,
`redrawObserved` whether there were any, `quietMs` how long the window had
been quiet when the wait ended, and `resizing` whether it was resizing then. When several endings hold at once,
`window_closed` wins, then `cancelled`. Plain text prints `ended`.

**`cancel WORD`** ends the wait with that id with `ended: cancelled` and
replies `{"ok":true,"cancelled":true}`, or `false` when there was none or the
window closed at the same moment, in which case the wait ends with
`window_closed`.

**`lease <addr> MS OWNER`** starts or renews the focus lease for MS
milliseconds (1 to 600000), owned by OWNER. Renewing it under another owner
takes it over. Needs an open socket. Replies with the members of the lease
object of `state` next to `ok`:

```json
{"ok":true,"held":true,"remainingMs":60000,"owner":"mcp-4242-a1b2c3d4"}
```

or with `unsupported` and the reason when the window cannot hold one: an X11
window, or one that shares its Wayland connection with the real keyboard
focus. Malformed arguments are checked first, then the window, the socket and
the capability.

**`lease <addr> release [OWNER]`** ends it; with OWNER only if it matches.
Replies `{"ok":true,"released":true|false}`, also `false` for a window without
a socket.

A lease keeps the emulated pointer and keyboard focus that input gave the
window; it never gives focus by itself. While it is held, the plugin does not
end that focus when the sender stops or a second after input. The pointer
stays where the last motion put it. The lease ends when it expires, on
release, when the socket or window closes, as soon as the real keyboard focus
moves to the window's Wayland connection, and when input is delivered to
another window of that connection. Ending it ends the emulated focus as
without a lease, once no input has arrived for a second. While the real
pointer is on the window's connection, the lease keeps only the keyboard. The
plugin keeps the focus by hooking `CSeatManager::setKeyboardFocus` and
`CSeatManager::setPointerFocus`, which would otherwise send the leased window
a leave on every change of the real focus.

Without a lease, the emulated focus ends as soon as the sender stops
emulating or disconnects, and after a second without input while it stays
connected.

**Pointer regions.** libei region offsets are unsigned, so a region cannot
start at negative coordinates. The pointer device has up to three regions in
window-local logical pixels, in this order: the window (0,0,W,H), the area
right of it (W,0,R−W,H) and the area below it (0,H,R,B−H), where R and B are
the right and bottom edges of the bounding box of the window and its monitor.
Points left of or above the window cannot be sent. A point over one of the
window's popups goes to that popup, where shots show it. For a toplevel whose
xdg geometry has an offset, such as a GTK window with client-side shadows,
Hyprland's own popup hit testing differs from where its renderer draws the
popups by that offset; the plugin follows the renderer. Hyprland's hit testing
places popups at the window's animated position, so while the window moves the
plugin tests a point there too, and the point still reaches the popup that
shots show at it. Any other point is clamped to the window. The device is
replaced when its regions change: after a resize, a move or a change of the
monitor layout. The keyboard is never replaced.

**Disconnect.** When a sender disconnects or stops emulating, the plugin
releases every button, then every key and modifier it held, so the
application sees them released. libeis can lose a sender's disconnect when a
send to it fails, so the plugin pings a sender that has sent nothing for a
second, once a second, and treats a failed ping as the disconnect: what such
a sender held is released 1 to 2 seconds after it exits.

**Closing window.** Before the plugin closes a socket because its window
closed, it takes what the sender still has in the socket, so the pings among
it are answered. Input it drops this way is never confirmed: it removes the
devices before it answers a ping that follows such input, and senders take no
answer after that as a confirmation.

### hyprmasq-input

```sh
hyprmasq-input --socket PATH [--no-pace] [--json] [--budget MS] [--locale NAME] ACTION...
```

[usage.md](usage.md#hyprmasq-input) documents the actions, key names, pacing,
exit statuses and JSON output. What the server relies on:

- Options come before the first action. Every action is checked before
  anything is sent, including whether the keymap can type every character of
  `type` and `text`, whether every key name exists, and whether every point
  lies in the pointer regions. Any problem exits with status 2 and sends
  nothing. Status 2 always means that nothing was sent.
- `--budget MS`: after those checks, the sender adds up the paced delays of
  the keys and buttons the actions resolved to, counting a character typed
  through Compose as a key for each step of its sequence, and the sleeps. When
  the sum is more than what is left of MS since it started, it exits with
  status 2, code `too_long`, `failedIndex` `null` and delivery `none`. With
  `--budget`, the JSON has `paceMs`, that sum, or `null` when the actions were
  not all checked. The server passes it on every run.
- `--locale NAME`: characters without a key of their own are typed through the
  Compose table of that locale instead of the sender's own (`LC_ALL`,
  `LC_CTYPE`, `LANG`, or C). Without a table for it, the sender uses that of
  `en_US.UTF-8`. The JSON has `compose`: `{"locale":L,"requested":R}` when a
  character was typed through the table of L, where R is the locale asked for
  and differs from L after that fallback, otherwise `null`. `cannot_type` for a
  character that the table has no sequence for names the table.
- Bounds: `wheel` up to 100 notches per axis, `keys` chords of up to 9 keys
  with REPEAT 1 to 100, `sleep` 0 to 3600000 ms, drags of at most 2000 steps,
  numeric buttons from `0x110` (`BTN_MOUSE`). The server validates within
  them.
- It pings after every frame and waits for the plugin after every action and
  every 16 frames, also with `--no-pace`, so `done` and `failedIndex` are
  exact.
- **Leaving nothing pressed.** Before exiting, also after a failure or on
  SIGINT and SIGTERM, the sender releases every button and then every key it
  pressed and did not release.
- **Signals.** On SIGINT or SIGTERM, the sender stops waiting, releases what
  it holds, tries for up to 1 second (`CANCEL_MS`) to get that confirmed, and
  prints its JSON. If the plugin does not answer in that second, the result is
  `cancelled` with delivery `uncertain`. A signal while it resolves a long text
  stops it with `cancelled` and delivery `none`. `hyprmasq-mcp` sends SIGTERM
  to a sender that its deadline cuts off 1250 ms, `CANCEL_MS` and a margin,
  before the capture's reserve, so that it has reported by then. It kills a
  sender 2 seconds after SIGTERM.
- **Device replacement.** When the plugin replaces the pointer device, the
  sender waits up to 1 s for the new one and continues if nothing was in
  flight on the old one. Otherwise it stops with `unconfirmed` and delivery
  `partial`. A removed keyboard means the connection is ending, which the
  sender confirms with a round trip before it reports anything.
- **`--json`** prints one object on stdout when the sender exits:

```json
{"ok":false,"done":2,"total":3,"failedIndex":2,"delivery":"partial",
 "progress":{"keys":14},"paceMs":1540,"compose":null,"error":{"code":"input_paused","message":"the server paused input, so some events were dropped"}}
```

- `done`: actions completed and confirmed. `failedIndex` is 0-based, or `null`.
- `delivery`: `none` (nothing sent), `complete` (the plugin confirmed every
  event), `partial` (actions before `failedIndex` confirmed, the failed one
  maybe in part; the plugin took every frame sent), `uncertain` (the
  connection ended, or the plugin stopped answering, before it took every
  frame sent).
- `progress`: for a failed `type`, `text` or `keys` action, the key presses
  the plugin processed, or the ones sent when the delivery is `uncertain`,
  without modifiers.
- `error.code`: `invalid_arguments`, `cannot_type` (status 2 when nothing was
  sent), `too_long` (status 2), `connect_failed`, `no_devices`,
  `input_paused`, `unconfirmed`, `cancelled` (status 1).
- Input that closes its own window: when the closing action was the last one
  and the plugin confirmed it before the window closed, the run succeeds with
  `complete`. Otherwise it gives `unconfirmed` with `partial`, or with
  `uncertain` when frames sent after the close never reached the plugin. The
  server handles both: after a complete run its wait ends with
  `window_closed`, and after a failed one the call fails with `window_gone`.

### hyprmasq-mcp

- Go module `github.com/nyrda/hyprmasq/mcp` in `mcp/`, command
  `mcp/cmd/hyprmasq-mcp`, built with the Go of the pinned nixpkgs and the
  official Go SDK v1.8.0.
- Options: `--input PATH` (default: `hyprmasq-input` from `PATH`),
  `--max-image-size N` (default 1280, 0 for the monitor's scale), `--lease-ms`
  (default 60000, 1 to 600000), `--structured-content auto|always|never`
  (default `auto`, see [Results](#results)), `--version`. It needs
  `XDG_RUNTIME_DIR` and `HYPRLAND_INSTANCE_SIGNATURE`.
- Talks to `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock`
  with `j/hyprmasq …`, `j/clients`, `j/monitors` and `/dispatch` requests, one
  connection per request, each written at once. Hyprland reads requests in
  pieces of 1023 bytes and blocks its main thread on a request that fills its
  last piece exactly, so such requests get a trailing space. Requests over 32
  KiB are refused, so an overlong `launch` command is `invalid_arguments`.
- `launch` dispatches `hl.dsp.exec_cmd` with a `name:<workspace> silent`
  workspace rule as `hyprmasq-launch` does, with the command quoted for the
  shell and the whole line written as a Lua string of decimal escapes. It finds
  the window by the `HYPRMASQ_LAUNCH` token in `/proc/<pid>/environ`, polling
  every 100 ms. It then settles it with a wait without a change phase: quiet
  300 ms, deadline up to 5 s. A call cancelled during that wait returns
  `cancelled`, naming the window, without a capture. It compares the sizes in
  the window lists from before the dispatch and after the capture to name the
  windows on the new window's workspace that were resized.
- `close` dispatches `hl.dsp.window.close` with the selector
  `stableid:<stable_id>` of the window it listed at the address, and polls the
  window list every 50 ms for 2 s. Hyprland resolves the selector when it runs
  the dispatcher and never gives a stable id to another window, so when the
  window closed and another one took over its address before the dispatch,
  the other one stays open.
- Lease owner: `mcp-<pid>-<random>`. Wait ids: `<owner>-w<N>`.

## Testing

**Plugin and sender subtests** in `nix/vm-test.py` check each guarantee; the
[subtest table](development.md#tests) lists them. Among them: the counter,
including popups that open without input, and the socket ids; every `ended`;
a hidden window that keeps drawing during a long wait, while a sender pauses,
and when it is hidden during a wait; screenshot sizes and popups outside the
window; popups of a window with an xdg geometry offset; the pointer regions
and their replacement; a window that has not caught up with a resize, in
`state`, screenshots and waits; Wayland and X11 windows popping in, whose
shots have their size while Hyprland draws them smaller, and popups of a
window that moves; the lease, its end when the real focus moves to its
connection or input reaches another window of it, and release of held input
on disconnect, also when libeis reports none; pacing measured with `wev`
timestamps; 2000 characters typed exactly; a budget that refuses text the
keymap types through Compose as more keys; Compose sequences of the target's
locale, also without a table for it; input paused on X11 with the keys
that got through; input that closes its own window, and input that it drops;
a connection taken over during a burst; SIGTERM while the plugin does not
answer.

**MCP subtests** drive `hyprmasq-mcp` over stdio from the test script:
launch, results without structured content for a client that introduces
itself as `claude-code`, `windows` with `match`, typing with a redraw and a
lease, the wait between a click and the keys after it, coordinates at scale 1 and 2 and in
zoom shots, `stale_shot` after a resize, before and during a batch and while
a call waits for the send lock, `out_of_bounds` (also for a popup above the
window), `invalid_arguments`, also for text that the keymap types through
Compose as more keys than the deadline fits, `cannot_type`, typing through the
Compose table of the window's locale and the hint without a table for it,
`unsupported` for
a drag, a click with modifiers and a chord that holds a letter on a shared
connection, where a chord of Shift and a letter still types a capital, partial
delivery when X11 input is paused, the wait outcomes, the lease across calls and on exit, new
windows, two servers acting at once on one window, the deadline,
cancellation, `act` with a capture region, also when the window is resized
during the call, changes shots of a terminal that the test draws in, from no
change and one region, whose zoom shows the drawn cells at their place, to
several, tiny changes left out and a highlight below the threshold, pointer
input through a changes shot and through its zoom, a popup of about the luminance
of its window, each reason for a full shot that a window can give, and
`"window"` and `"none"` as they were, `--max-image-size`, a window that
catches up with a resize during a screenshot and the refusal of coordinates
from a shot of its older frame, `launch` and `close` naming the window they
resize, `windows` and an `act` that times out reporting it as resizing,
coordinates from shots of Wayland and X11 windows that pop in, input that
closes its window, SIGTERM to the server, `close` and exit.

**Go tests** in `mcp/` run the server against a fake Hyprland and a fake
sender, including resizes during a batch, windows that do not catch up with a
resize, cancellation while sending and waiting, lock contention, the checks
after the locks, also of an `act` without actions whose window another one
replaced while it waited, capture regions of a window resized during the call,
socket ids, lost lease answers, session ends and exit
statuses, argument errors of every tool through an SDK client, `null` among
them, the hints, the length of every tool description, structured content by
client and `--structured-content`, `windows` with `match`, the sleep in
front of keys that follow a click or drag too soon, and changes shots: one
region and several, merged regions, no change and the hint after input that
changed nothing, tiny changes left out and counted, each reason for a full
shot, detail images of shrunk shots, the base by shot, zoom, session and
window, coordinates of changes shots and their zooms, error paths, the files
and last shots forgotten with a window, an overview and detail image of one
frame, zooms of the compared frame, captured zooms of another frame or cut by a
closed popup and the zooms given up with them, the detail image of popups past
the window's edge, joining thousands of regions, and the time left for the
full shot. The comparison has tests of its
own: a block, a caret and a speck, a switch of antialiasing, a hover
highlight, a change of color that keeps the luminance, grouping, transparency,
the limit of changed pixels and stopping. They run in the Nix build.

Not yet covered by a VM subtest: a toolkit's dropdown kept open across calls.

**Acceptance tasks**, still to be run end to end with a real agent, measuring
success, steps, tokens and recovery cost: fill a GTK form and submit it,
answer a modal dialog, pick from a delayed hover menu, pick a dropdown entry
in a later call, save a file through a file chooser, enter text with Compose
and into an autoindenting editor (asserting the documented behaviour, not
universal success), recover from a timeout.

Practice runs with Claude Code and Codex have driven Chromium through the
server on an isolated Hyprland. pi is still to be checked by hand with
`direct` exposure.

## Packaging and registration

`nix/mcp-package.nix` builds `hyprmasq-mcp` with `buildGoModule` and runs its
tests. The plugin package installs it as `bin/hyprmasq-mcp`, a wrapper that
puts the package's own `bin`, with its `hyprmasq-input`, first on `PATH`, so
the server, the sender and the plugin always come from one revision. The NixOS
module installs that package. The flake's `hyprmasq-mcp` package is the server
alone.

| Client | Registration |
| --- | --- |
| Claude Code | `claude mcp add --scope user hyprmasq -- hyprmasq-mcp` |
| Codex | `[mcp_servers.hyprmasq]` with `command = "hyprmasq-mcp"` in `~/.codex/config.toml`, and `default_tools_approval_mode = "approve"` for `codex exec` with `approval_policy = "never"`, which otherwise fails every call that needs approval: all but `windows`. |
| pi | `~/.pi/agent/mcp.json` with `"exposure": "direct"`, so the tools are not hidden in codemode. pi then shows none of the server instructions; the tool descriptions carry their rules. |

See [Installation](installation.md#mcp-server) for the full entries and
timeouts.

## Known limits

- Pointer input cannot reach the parts of a popup above or left of its window.
  Shots show them; `act` refuses such points with `out_of_bounds` and a hint
  to click the part over the window or use the keyboard.
- A window that acks a new size and then commits a frame of the old size is
  not reported as `resizing`, so its shots are not marked as older frames.
- A window that is moved during a batch, but keeps its size, is not noticed
  by the server. The plugin replaces the pointer device, and a later point
  that lies outside the new pointer regions fails with `out_of_bounds` and
  delivery `partial`; points inside them land at the same window-local
  position.
- Windows without a lease, X11 windows and windows sharing a connection with
  the user's focus, lose the emulated focus when the batch ends, and a menu
  that closes on focus loss may already be closed in the act's own shot.
- Leased windows keep their emulated focus while the session is locked. The
  user's real keys never reach them.
- Plugin arguments are split at whitespace, so an `XDG_RUNTIME_DIR` with
  spaces breaks the shot paths.
- If the plugin ever replaced the keyboard, for example on a keymap change,
  the sender would stop with `no_devices`. Reconnecting picks up the new
  keymap.
- The hooks on `Desktop::View::CWindow::updateWindowDecos`,
  `CSeatManager::setKeyboardFocus` and `CSeatManager::setPointerFocus`, and
  the access to the private `m_currentSurface` of `CWLKeyboardResource` and
  `CWLPointerResource`, have to be checked again whenever the Hyprland pin
  moves.

## Later

- A security layer, in the plugin or Hyprland, if hyprmasq is ever used by
  agents that cannot reach `hyprctl`.
- AT-SPI: bounded subtree queries with roles, names, states and bounds, never
  the whole tree after every action.
- `client_responsive` from `xdg_wm_base` ping and pong, as a diagnostic, once
  pongs can be attributed (Hyprland always pings with serial 1337).
- Request IDs with cached results, if lost responses turn out to cause
  duplicate input.
- An explicit clipboard paste for long text, never as a silent fallback.
- MCP 2026-07-28, once clients use it for stdio servers.
- An isolated mode: the same tools against a separate headless Hyprland.

## Open questions

1. Is a 60-second lease right, and is a focused-looking app on a visible
   workspace acceptable while the user works elsewhere?

Settled: `hyprmasq-input` paces by default, for scripts too, and `--no-pace`
turns it off. `--max-image-size` defaults to 1280, after practice runs with
Claude Code and Codex (see [Shots](#shots-and-coordinates)).
