# MCP server

Status: implemented in hyprmasq 0.3.0, plugin interface 3. This document is
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
| `windows` | optional `match`, `wait_ms` (0 to 10000, default 0) | All windows, or with `match` those whose class or title contains it, ignoring case as Unicode simple case folding does (`Σ` finds `ς`): `window` (address), `stable_id`, `class`, `title`, `pid`, `workspace` (name), `visible` (on a monitor's active or special workspace), `size` (logical), `resizing` (not yet redrawn at its size, see [Shots](#shots-and-coordinates)), `xwayland`, `launched` (by this server), `lease` (`held`, `remaining_s`, `mine`, or `null`), `capabilities`, and for a [late window](#late-windows) `opened_by_input`, the window whose input opened it. The one window it lists comes with a `shot`, as from `screenshot`, with the image, and the [late windows](#late-windows) of the session come with a shot each. With `wait_ms`, it first waits for a window that is not there yet; see [Waiting for a window](#waiting-for-a-window). |
| `launch` | `command` (array, required), `workspace` (default `hyprmasq`), `timeout_s` (1 to 300, default 30) | Starts the command on a hidden workspace like `hyprmasq-launch`, waits for its first window and for that window to settle, and returns `window`, `wait`, `shot` and `new_windows`, the other windows that appeared, with the image. The hint names the windows on its workspace whose size changed meanwhile, such as tiled windows that made room for it, so that their earlier shots are stale. |
| `screenshot` | `window`, optional `region` | A shot, with the image. No lock, and no waiting except for a window that has not caught up with a resize, or a hidden one that has not drawn since it got no frame callbacks. See [Shots](#shots-and-coordinates). Also the [late windows](#late-windows) of the session, with a shot each. |
| `act` | `window`, `shot`, `actions`, `wait`, `capture`, `deadline_ms` | Runs the actions, waits, and captures what changed, and the windows that the input opened, with a shot of each, and the [late windows](#late-windows) of earlier acts. When the window closed, the window it came from, with a shot. See [act](#act), [Changes](#changes), [New windows](#new-windows) and [A window that closes](#a-window-that-closes). |
| `release` | `window` | Ends the window's focus lease, whichever session holds it. The window stays open. Returns `released`. |
| `close` | `window` | Asks the window to close, as Hyprland's close dispatcher does, ends its lease, and reports whether it closed within 2 s (`closed`, `elapsed_ms`) and which windows appeared (`new_windows`), such as a confirmation dialog. A window that stays open is not an error. When a tiled window closed, the hint names the tiled windows on its workspace, which take up its space, so that their earlier shots are stale. |

`windows`, `screenshot`, `launch` and `release` are annotated as not
destructive, and `release` as idempotent. `act` and `close` keep the
defaults. No tool is read-only: `screenshot` opens the window's socket for the
commit counter that the shot records, and the socket stays open, so from then
on the plugin treats the window as driven (see the
[security model](usage.md#security-model)), and so does `windows` for the
window it takes a shot of.

`window` takes an address as `windows` lists it: `0x` and up to 16 hex digits.
Hyprland and the plugin compare addresses as strings, so the server first
writes the address as they do, in lower case and without leading zeros:
`0x00A1` names the window `0xa1`, and results name it so.

`capabilities` describe what works on a window at the moment: `lease` (the
emulated focus can stay on the window between calls, see
[Focus lease](#focus-lease)), `hold` (buttons and keys can stay pressed across
input frames, needed for `drag`, for a `click` with modifiers and for a `key`
chord that holds a key other than a modifier) and
`keyboard` (`yes`, or `x11-focus` when X11 input depends on which window has
the focus). They are observations, not reservations. `act` checks `hold`
before sending and again once it holds both [locks](#concurrency). The
plugin's `dnd` capability is left out, see [Actions](#actions).

Every call first checks that the plugin is loaded and has interface 3
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

**A starved window** may show an older frame too. Hyprland sends a window on a
hidden workspace no frame callbacks, and many applications draw only when they
get one: Thunderbird started on a hidden workspace shows an empty window until
something sends it frame callbacks. A screenshot alone does not do it: the
plugin answers the callbacks a hidden window waits for whenever it renders the
window, but only after the image, and Thunderbird needs a run of them. The
plugin reports such a window as `starved` (see [`state`](#plugin-commands)):
it is hidden, waits for a frame callback, and has got none from hyprmasq.
Before every capture, the server checks for it as for a resize, and if the
window is starved, runs a `wait … since` with `expect` 1000 ms and a quiet
time of 100 ms, within the same 1.5 s and the same time left, one wait for
both when the window is also resizing. The wait sends the window frame
callbacks every 16 ms; once it has drawn and gone quiet, the shot shows the
frame it settled on. In a benchmark, Thunderbird drew within 40 ms of its
first frame callbacks. A window that does not draw within the second is
captured anyway: its shot has `"may_be_stale": true`, the summary says
`shot sN W×H (may be stale: not redrawn within 1000 ms)`, or `(may be stale:
no time to redraw)` when no time was left to wait, and the hint says to go on
if the shot looks complete, and otherwise to wait with an `act` without
actions. An application with nothing new to draw does not draw either, so its
shot is marked although it is current, which is why `act` takes coordinates
from such a shot. That costs the second once: the wait sends the window frame
callbacks through its socket, and from then on it is not starved. `act` and
`launch` send the window frame callbacks before they capture it anyway,
through input and their waits, so their shots are not starved unless no time
was left to wait. A window driven before that later waits for frame callbacks
again is not starved; see [Known limits](#known-limits).

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
whole logical pixels that cover the box of the shot. More than 5 regions are
joined until at most 5 are left, each time the two whose joined box adds the
least area, along with the regions that box touches, as a form that moved and
the text typed into it become one zoom instead of a full shot. A joined zoom
shows the unchanged pixels between its changes too.

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
input name s5: after a scroll that drew nothing, `The input may have had no
visible effect. Look at s5 before repeating it.`, after typing that the window
redrew for but that changed nothing, `Nothing changed since s5, so the input
may have missed or had no visible effect. Look at s5 before repeating it.`, and
when tiny changes were left out, `Only tiny changes since s5, such as a caret
that moved or blinked, so the input may have missed or had little visible
effect. Look at s5 before repeating it.` After motion alone, as for a hover,
which may change nothing, there is no such hint, nor after an empty batch.

After input that can open a window, a click, a drag, a key or text with a line
break, the window that got it may not change because the input opened a
window of another process, which maps after the call has returned, as the
portal's file chooser did up to 1.8 s after its click in a benchmark. The hint
says so in place of suggesting only a miss, as after a click that drew
nothing: `The click may have missed or had no visible effect; it may also have
opened a window that maps later, as a file chooser of another process can a
second or two after the input: windows with wait_ms waits for it, and your
next act or screenshot returns it. Look at s5 before repeating it.` The hints
for a window that redrew without a change, for tiny changes and for a wait
that timed out without a redraw add the same clause. See
[Late windows](#late-windows).

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
| `the window waits to draw` | The window was [starved](#shots-and-coordinates) while the detail image was captured, as after an `act` that sent nothing and had no time left to wait. The full shot lets it draw if there is time, and otherwise says that it may be stale. |
| `resized since sN` | The window's size changed. |
| `the monitor's scale changed since sN` | The detail images have different scales, as after a move between monitors at or below scale 1. Above scale 1 detail images have logical pixels, which stay comparable, and coordinates are in logical pixels anyway. |
| `popups outside the window changed since sN` | The box of the window and its popups changed, as when a menu that reaches past the window's edge opens or closes. The base's coordinates would not cover it. |
| `N% changed since sN` | More than 35% of the pixels changed, or the regions cover more than 35% of the window, as after scrolling or navigating. |
| `N changed regions since sN` | More than 5 regions, which joined into 5 would cover more than 35% of the window, or more than 128, too many to join. |
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
| `pause` | `ms` (0 to 5000) | Waits. It does not check that anything is ready. Keys after a click need none, see [Pacing](#pacing). When the input before it closed the window, it ends there. |

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
seen.

Whether a menu is still open at the next call depends on the app. The
[lease](#focus-lease) keeps the window's emulated focus, which matters only for
what the app closes on focus loss. In the benchmark's browser task, whose
Chromium window lacks the `lease` capability, Forgejo's "Add file" menu, which
the page draws, was still open 12 s after the click that opened it, while the
user typed in their own window of the same browser, and so was Chromium's own
popup of a `<select>`. Clicking what opened such a menu closes it. Agents had
followed the description's earlier claim that without the lease a menu is gone
by the next call: after a call that opened the menu, they clicked its opener
again and pressed Down, Down and Enter. The click closed the menu, the first
Down opened it again, and the second highlighted the first entry, which Enter
picked, in 11 of 25 runs. After Alt+Left, the browser restored the page with
that entry still highlighted, so the same batch picked the third. Once the
description said that a menu may be gone or still open, 3 of 10 runs still did
so, and their debriefs blamed the guidance for windows without a lease: every
result ended in `· no lease`, and the capability was described as what keeps
menus open. So the descriptions tie the lease only to the focus, the summary
names it only while one is held (see [Results](#results)), and the `act`
description makes the shot the test: if it shows the menu still open, click its
entry, as clicking its opener closes it; if the menu closed, open it and pick
the entry by keyboard in one batch.

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

A `click` or `drag` that more actions of the batch follow gets the window's
answer before they go. The server puts a `sync` after it (see
[hyprmasq-input](#hyprmasq-input)), which belongs to the next action and takes
the place of the 50 ms between the two: the sender stops there once the plugin
has processed the click, and goes on when the server answers. Meanwhile the
server runs the plugin's `wait … since` the counter read before the input,
with `expect 150 quiet 150`: it ends 150 ms after the window's last commit,
or after 150 ms without one when the click draws nothing. Its deadline is
1000 ms, and less when the rest of the batch would not fit before input has to
end otherwise; with no time left, the server answers at once. The sync gives up
after 1000 ms itself. A wait that fails, such as for a window that closed,
answers too, and the sender then finds the connection gone. A click alone or
at the end of the batch has only the wait after the batch.

The times come from Chromium and Forgejo in the benchmark VM, measured from the
release of the button, with waits started after it as the server starts them,
at 1920×1080 and 2560×1440 alike:

| Click | Drawing | Quiet for 150 ms |
| --- | --- | --- |
| "Add file", which opens a menu of the page | One commit, within 20 ms | 184–193 ms |
| Into a text field, which shows its caret | One commit, within 20 ms | 185–191 ms |
| Empty space | None | `no_redraw` at 185–204 ms |
| One that starts a 400 ms CSS transition | A commit every 16 ms | 527–571 ms |
| A link to another page | Commits with pauses of up to about 100 ms | 579–688 ms |

A quiet time of 100 ms ended within a page load in 1 of 6 loads, and one of
50 ms in all of them, so 150 ms waits for a page that a link opens, which the
batching rule lets known keys follow. The bound of a second covers that and an
opening animation of half a second. Before the change phase ends without a
commit, a click has had about 200 ms to draw, ten times what Chromium took.
Waiting the quiet time after the click's answer, not a fixed time, makes no
assumption about how fast the display draws.

The first `type` or `key` after a `click` or `drag` in a batch starts at least
300 ms after the click or drag has ended, also when the window answered sooner.
Applications move the keyboard focus a moment after a click, and keys that come
sooner can reach the window's shortcuts instead of the field: Spotify, clicked
into its empty search field on a playlist page, took a space typed 200 ms after
the click as play/pause, and typed it when it came 350 ms after the click. When
the sender actions in between, counting the 50 ms before each of them and any
`pause` but not how long motion or scrolling take, add up to less, the sync
after the click lasts at least the rest, which counts toward the batch's pace
like a `sleep`. In Chromium, whose menu opened within 20 ms, the wait for the
answer ends before those 300 ms, so a click, Down, Down and Enter takes as long
as before. The `ms` description of `pause` tells agents that they need no
pause of their own for it.

Keys and text after other keys and text get no such wait: an application takes
the keys of one window in order, each once it has handled the ones before, and
a wait for quiet after every key would add its quiet time to each shortcut and
line of a typed form.

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
   since shots draw the window as it is once the animation has ended. Neither
   does an animation of the window's own (see below).
3. **Watch**, only after input that can open a window (see
   [New windows](#new-windows)), when the wait ended `quiet` or `no_redraw`.
   An app may draw again a moment after it settled, once work that does not
   draw ends: a browser draws the page that Enter loads about half a second
   later, a mail client the results of a search it starts after a pause in
   typing. So the server renews the lease and goes on with a `wait … since`
   from the counter where the wait ended, with `ignore animations`, the
   call's `quiet_ms`, and `expect` the rest of the grace period for new
   windows, 500 ms after the wait by default, or less when the deadline
   leaves less, keeping `quiet_ms` for a redraw at its end. The
   `wait.quiet_ms` description names the grace the server runs with. The
   window keeps getting frame callbacks meanwhile, without which an app on a
   hidden workspace does not draw at all. A redraw that is not part of an
   animation, such as a spinner or a blinking caret that was going on before
   the input, ends its change phase, and its quiet phase waits it out; the
   capture follows. Its commits and animations count also when it ends
   `no_redraw`, so the capture leaves those animations out of the changes. After a `quiet` wait that saw a redraw, the summary adds
   `drew again, settled N ms later`, and `wait.again` has its `quiet_ms` and
   `elapsed_ms`; after `no_redraw`, or a `quiet` wait without one, the wait
   ends `quiet` with a redraw, both waits' time in `elapsed_ms`. A redraw
   that goes on until the deadline makes it a `timeout`. A window that closes
   meanwhile keeps the wait as it ended and closed after it: the summary adds
   `window closed` and the result sets `window_closed`, as for a close after
   the wait. A reply for another socket, which replaced the one the wait
   counted for, is not taken, and neither is an error, such as from a plugin
   without `ignore animations`. Without a redraw, nothing changes but the
   time of the capture, which the grace period spends anyway.

### Animations

The plugin tells animation apart from other drawing by the box that each
commit damages, as the application reports it to Hyprland: a commit is part of an animation when it
damages a box of at most 15% of the window that at least 3 commits since the
last input, this one included, have damaged within the last 3 seconds, the
first of them a second or more ago, and it comes at least 500 ms after the last
input. A box that animated before the input has to keep animating for a second
after it, so that the window's answer in that box, such as content that
replaces a spinner, counts as a change. Two damaged boxes
are of the same thing when they overlap or touch and the box around both is at
most 1.5 times the larger one. A progress bar, a video, a spinner or a blinking
caret qualify; typing, which ends within a second of its first key, the redraw
that answers input, and an application that damages its whole window do not.

The quiet phase does not wait for commits of an animation, and `quiet_ms`
counts from the last other one. The result names the animations drawn during
the wait in the summary, `quiet 200 ms except animation at 12,40 300×20`, and in
`wait.animated`, as boxes in the pixels of the result's shot. With capture
`"changes"`, the changes within them are left out of the zooms and counted in
`shot.changes.animated`, with the line `1 change in an animation left out`.
They do not count towards the 35% of the window that gives a full shot. When
nothing changed outside them, the summary says `shot s7 = s5, no change
outside animations`, and after input the hint says that its effect may lie
inside them.
Before an animation has redrawn for a second, it counts as drawing like any
other, so a wait over a window that keeps animating ends about 1.2 seconds
after the input instead of at the deadline.

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

A capture is taken in every case except `window_closed`. A window that
closes after the wait reports as closed too; see
[A window that closes](#a-window-that-closes).

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

A screenshot of a hidden window answers the frame callbacks it waits for once,
after the image. Before the server captures a window that waits for one and
has got none from hyprmasq, it lets the window draw: see
[a starved window](#shots-and-coordinates).

### What the wait does not tell

- Work an application does without drawing, such as a network request, is
  invisible. A `settled` window can still be loading, when the work takes
  longer than the watch after input, or follows input that has none. The
  description of `wait.quiet_ms` says so, and to act again with no actions
  while the shot still shows loading.
- An application that draws in bursts further apart than `quiet_ms` can settle
  between two bursts.
- A commit is not proof that the pixels changed, and not proof that the input
  did what the agent wanted. Only the screenshot shows that.
- X11 menus and tooltips are separate windows and do not count as commits of
  the app's window. They show up as [new windows](#new-windows) in the result,
  if they map before the call lists the windows.

## Focus lease

Without a lease, a driven window loses its emulated pointer and keyboard focus
as soon as `hyprmasq-input` exits. An agent's next call usually comes several
seconds later, by which time what the app closes on focus loss, such as some
toolkits' popovers and dropdowns, may be gone. Other popups, such as a menu
that a web page draws, can stay open.

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

Windows without the `lease` capability lose the focus when the batch ends.
The `windows` description says so in the capability's terms: without it, the
window loses the focus after each call, and what its app closes on focus loss
may be gone by the next call. It does not say which menus those are, since
that depends on the app (see [Batching](#batching)): the `act` description
tells agents to click the entry of a menu that the shot shows open, and if the
menu closed, to open it and pick the entry with the keyboard in one batch. It
never suggests clicking where a menu item probably is.

## Shortcuts beside the user's window

A browser with the user's window on their workspace and the agent's on a
hidden one is one process with one Wayland connection, and the user's window
has the real keyboard focus. Keys reach the agent's window only while the
plugin gives it the connection's keyboard focus, and Chromium takes the window
with the keyboard focus for the active one. It runs a shortcut that a page may
use itself, such as Ctrl+L, Alt+Left or F6, only once the page has let the key
go by, 10 to 90 ms after the key on a hidden workspace, and does nothing with
it in a window that is no longer active by then. So the plugin keeps the
keyboard focus on the agent's window while the sender holds keys, and after a
key pressed with Ctrl, Alt or Super held, a function key, Escape or a media
key for a quarter of a second, and then gives it back to the user's window
with the user's modifiers. After plain typing, it gives it back with every
frame, as before. Typing after the shortcut, such as the address after Ctrl+L,
reaches what the shortcut focused.

The user's own key, modifier or button to the connection takes the keyboard
focus back first, before Hyprland sends it: it reaches the user's window,
never the agent's. A shortcut that has not run by then may do nothing, and the
URL typed after it go into the page. The plugin counts such an interruption
within a quarter of a second of a key pressed with Ctrl, Alt or Super held, a
function key, Escape or a media key, in `state`'s `interrupted`. An `act` with
a `key` action on a window without `hold` reads the counter before the input
and once more after the wait and the capture; when it rose, the result has
`actions.interrupted`, the summary says `, interrupted by the user's input`,
and the hint tells to check the shot and send the key again if it did not take
effect. Typing alone needs no round trip in the app and is not counted.

Each move of the keyboard focus between the two windows makes the browser
deactivate one of them: the user's page gets `blur` and `focus` around each
key that the agent types, and around a shortcut while it settles.

## Results

A result has three parts:

1. Text: a one-line summary, for a changes shot a line for each zoom and one
   for the tiny changes left out, then a hint when one is needed:

   ```
   3/3 delivered · redraw observed · quiet 214 ms · 655 ms · shot s5 1280×800 · lease 60 s
   ```

   ```
   1/1 delivered · no redraw within 1000 ms · shot s6 = s5, no change · lease 60 s
   The click may have missed or had no visible effect; it may also have opened a window that maps later, as a file chooser of another process can a second or two after the input: windows with wait_ms waits for it, and your next act or screenshot returns it. Look at s5 before repeating it.
   ```

   ```
   2/2 delivered · redraw observed · quiet 200 ms · 512 ms · shot s7 = s6 + 1 change · lease 60 s
   zoom s8 214×98 of s7 at 40,120 107×49
   ```

   A click that closes its dialog, with a pause after it:

   ```
   2/2 delivered · window closed · back to 0x55d1c0a3e4b0 (thunderbird "Inbox - Mozilla Thunderbird") on workspace mail (hidden) · new window 0x55d1c0b2f4a0 (thunderbird "Sending Message - Re: Design review room?", same pid) on workspace mail (hidden)
   shot s9 1280×720 of 0x55d1c0a3e4b0, the window it came from
   shot s10 640×180 of new window 0x55d1c0b2f4a0
   The window closed. It came from window 0x55d1c0a3e4b0, which shot s9 shows now: go on there with that shot's pixels. Any new windows are listed; call windows for the rest. The input opened new window 0x55d1c0b2f4a0 "Sending Message - Re: Design review room?" in shot s10.
   ```

   The parts, joined by ` · `:

   | Part | Forms |
   | --- | --- |
   | Actions, if any | `N/M delivered`, with `, nothing sent`, `, delivery partial: actions[i] (type) may be partly applied (K keys sent)` or `, delivery uncertain from actions[i] (click) on`, and with `, interrupted by the user's input` when the user's input took the keyboard back before a shortcut had settled (see [Shortcuts beside the user's window](#shortcuts-beside-the-users-window)) |
   | Wait | `redraw observed · quiet N ms · N ms`, `no redraw · quiet N ms · N ms`, either with `quiet N ms except animation at X,Y W×H` or `except animations at …, …`, and after the first with ` · drew again, settled N ms later` when the window drew again during the [watch](#phases), `no redraw within N ms`, `timeout after N ms, still resizing`, `timeout after N ms, still drawing`, `timeout after N ms, no redraw`, `no time left to wait`, `window closed`, `cancelled` |
   | Window closed | `window closed` after the wait's part, when the window closed after its wait had ended |
   | Capture | `shot sN W×H`, `shot sN W×H (full shot: REASON)`, `shot sN W×H (stale: not yet redrawn at its new size)`, `shot sN W×H (may be stale: not redrawn within N ms)` or `(may be stale: no time to redraw)`, several of them as `(full shot: REASON; stale: …)`, `shot sN = sM + K changes` followed by the lines `zoom sZ W×H of sN at X,Y W×H`, `K tiny changes left out` and `K changes in animations left out`, `shot sN = sM, no change`, `shot sN = sM, no change outside animations`, or `capture failed: MESSAGE` |
   | Back to | When the window closed: `back to ADDR (CLASS "TITLE") on workspace NAME (hidden\|visible)`, the window it came from, followed by a line `shot sN W×H of ADDR, the window it came from` |
   | Lease | `lease N s` while the window holds a lease, otherwise nothing: `windows` names the capability a window lacks, and `lease` in the structured content has `held` |
   | New windows | `new window ADDR (CLASS "TITLE"[, launched][, same pid]) on workspace NAME (hidden\|visible[, focused])`, or `new windows` with a list, followed by a line `shot sN W×H of new window ADDR` for each shot of one |
   | Late windows | `late window ADDR (…) on workspace NAME (…)`, or `late windows` with a list, as for new windows, followed by a line `shot sN W×H of late window ADDR` for each shot of one |

2. The images, when they were captured: the shot, or the zooms of a changes
   shot in the order of their lines, then the shot of the window that a closed
   window came from, then the shots of new windows and of late windows in the
   order of their lines.
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
  "new_windows": [{"window": "0x55d1c0b11230", "stable_id": "1a", "class": "org.gnome.Nautilus", "title": "Save As", "pid": 4242, "same_pid": true, "launched": true,
                   "workspace": "hyprmasq", "visible": false, "focused": false, "source": "0x55d1c0a3e4b0", "placed": true,
                   "shot": {"id": "s6", "width": 900, "height": 640, "frame": {"x": 0, "y": 0, "width": 450, "height": 320}}}]
}
```

A changes shot's `shot` has the base's `width`, `height` and `frame`, and
`changes`: `{"base": "s6", "regions": [{"shot": "s8", "image_width": 214,
"image_height": 98, "x": 40, "y": 120, "width": 107, "height": 49}],
"left_out": 1}`, with `left_out` only when tiny changes were left out. A full
shot that capture `"changes"` took in their place has `full_shot` with the
reason.

`hint` is left out when there is none, and `shot.resizing`,
`shot.may_be_stale` and `wait.resizing` unless they are true. `wait.resizing` is true when the wait
timed out while the window had not caught up with a resize. `actions` also
has `failed_index` and `progress` (`{"keys": N}` for a `type` or `key`
action) when an action failed, and `interrupted` when it is true.
`wait.commits` counts the commits since the baseline, and `wait.quiet_ms` how
long the window had been quiet when the wait ended. `wait.again` is there only
when the window drew again during the [watch](#phases) after it settled. An error adds `error`
(`code`, `message`), and a failed capture after applied input adds
`capture_error`.

`lease` is `{"held": false, "remaining_s": 0}` when the window holds no lease
after the call, which the summary then leaves out.

`new_windows` lists the [new windows](#new-windows) of the call, and
`late_windows`, left out when there are none, the [late windows](#late-windows)
that it reports. `window_closed` is true, and left out otherwise, when the
window closed during the call, and `source` is then the window it came from
(see [A window that closes](#a-window-that-closes)).

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
| `windows` | One line per window: `ADDR · CLASS "TITLE" · workspace NAME (hidden\|visible) · W×H [· resizing (not yet redrawn at its new size)] [· xwayland] [· launched] [· opened by your input to ADDR[, not yet reported]] [· lease N s (this session\|another session)] [· lacks lease, hold] [· keyboard x11-focus]`, naming only what the window lacks of `lease`, `hold` and `keyboard yes`, so a line without them has them all, and `capabilities unknown` when the plugin did not answer for it. With `match` and no window matching: `no window has "TEXT" in its class or title, of N windows`. Then a line on how a wait ended, if it did not find a window at once, `late window ADDR (…) on workspace NAME (…)` for a late window that `match` leaves out, `shot sN W×H of window ADDR` for the shot of the one window listed and `shot sN W×H of late window ADDR` for that of each late window, in the order of the images, and the hint: the one about a shot's older frame, as for `screenshot`, and those about late windows, as for `act`. | `{"windows": [...]}`, with `stable_id`, `pid`, `capabilities` `{"lease", "hold", "keyboard"}`, `shot` for a window with one and every other fact of the lines; `late_windows` when there are any, as for `act`; `wait` `{"ended", "elapsed_ms"}` with `wait_ms`; `summary` with the lines after those of the windows, when there are any, such as the one that says why it lists none; and `hint` |
| `screenshot` | `shot sN W×H · window ADDR W×H`, with ` · zoom of sM at X,Y W×H` after the shot for a zoom, and ` (stale: not yet redrawn at its new size)` after the shot when it shows an older frame, or ` (may be stale: not redrawn within N ms)` or ` (may be stale: no time to redraw)` when the window was starved and did not draw. Then the [late windows](#late-windows), as for `act`. | `{"summary": "...", "shot": {...}}`, and `late_windows` when there are any |
| `launch` | `launched ADDR CLASS "TITLE" · pid N · workspace NAME · settled after N ms · shot sN W×H`, or `still resizing after N ms` when the window had not caught up with a resize, `still drawing after N ms`, or `not settled after N ms, no redraw` when input from elsewhere kept the window from counting as quiet, with ` (stale: not yet redrawn at its new size)` or ` (may be stale: …)` after the shot as for `screenshot`, then other new windows. The hint names the windows on its workspace whose size changed. | `{"summary": "...", "window": {...}, "wait": {...}, "shot": {...}, "new_windows": [...]}` |
| `release` | `lease released; ADDR stays open`, `lease of another session released; ADDR stays open`, or `no lease was held on ADDR`. | `{"summary": "...", "released": true}` |
| `close` | `closed ADDR after N ms`, or `ADDR is still open after N ms`, then new windows. The hint says to take a screenshot of a window that stays open, or names the tiled windows that take up the space of a tiled window that closed. | `{"summary": "...", "hint": "...", "closed": true, "elapsed_ms": 140, "new_windows": []}` |

During a call that asked for progress with a `progressToken`, the server sends
a progress notification about once a second: `progress` is the milliseconds
elapsed, `total` the deadline (for `launch`, `timeout_s`), and `message` the
phase: `waiting for other calls on the window`, `waiting for input to other
windows`, `sending input`, `waiting for the window to settle` or `capturing`,
and for `launch` `waiting for the first window`. pi resets its tool timeout on
progress. Other clients may not, which is one reason for the deadline cap.

### New windows

`new_windows` lists every window in the window list at the end of the call
that was not there at its start, including ones whose relation to the target
is unknown. An app may open a window a moment after the window that got the
input has settled, as Thunderbird's compose window did 70 to 170 ms after the
`act` that pressed Reply had returned, in a benchmark. So a call that
delivered input that can open a window, a click, a drag, a key, or text with a
line break, lists the windows `--new-window-grace-ms` after its wait, 500 ms by
default, while it watches the window for another redraw (see
[Phases](#phases)), captures it and renews the lease; the grace ends at the latest
250 ms before the deadline, which leaves time for the listing. Text without a
line break, scrolling, motion and pauses list them at once. A window that maps
after that is a [late window](#late-windows), which the session's next `act`,
`screenshot` or `windows` reports.

Each entry has the window's `workspace` (name), whether it is `visible` on a
monitor and whether it has the user's keyboard focus (`focused`). `source` is
the window it came from as the plugin found it when it mapped, and `placed`
whether the plugin opened it on that window's workspace without the focus, as
[usage.md](usage.md#new-windows) describes; `not_placed` says why it did not:
`disabled`, `user_input` or `workspace`. `source` is left out when the plugin found none.
`same_pid` (the target's process) and `launched` (the window belongs to a
`launch` by this server, as the plugin's `launched` lists it) are relations of
the server's own.

A new window that came from the target, by its `source`, unless the plugin
found the user's own input to have opened it, or that belongs to the target's
launch by this server gets a `shot` in the same result, up to 3 of them and none with
capture `"none"`, so the agent can act in it at once with the shot's pixels.
The server opens its socket and waits up to 1 s for it to draw what it opened
with, until it has been quiet for 200 ms, since a window on a hidden
workspace gets frame callbacks only from such a wait, and then captures the
whole window, all within the deadline. A new window that is
[starved](#shots-and-coordinates), as one that an app opens on a hidden
workspace and draws only in answer to frame callbacks, skips that wait: the
capture's own wait for a starved window feeds it frame callbacks, and its shot
has `"may_be_stale": true` and the hint about it when the window did not draw.
Windows of other apps, and the user's, get no shot and stay undriven. A call
that waited for another call on the window compares with the windows as they
are once it holds the window's lock, so the windows that the other call's
input opened are not its own. The hint names the new windows from the target, in place of the
hint that the target did not change, which says nothing about input that opened
a window. It also names a new window from the target that has no shot, and one
that the plugin did not place that is visible, and whether it has the user's
focus and why, so the agent can tell the user:

```
1/1 delivered · redraw observed · quiet 200 ms · 655 ms · shot s7 = s5 + 1 change · lease 60 s · new window 0x55d1c0b11230 (thunderbird "Write: (no subject)", same pid) on workspace 1 (visible, focused)
zoom s8 214×98 of s7 at 40,120 107×49
shot s9 960×1080 of new window 0x55d1c0b11230
The input opened new window 0x55d1c0b11230 "Write: (no subject)" in shot s9. New window 0x55d1c0b11230 opened on the user's visible workspace 1 and has their keyboard focus, so what they type goes there, since the plugin's place_new_windows option is off. Tell the user.
```

### Late windows

An app gives no sign that a window is coming. In a benchmark, the portal's
file chooser mapped 1.4 to 2 s after the click, 0.7 s after the `act` and its
grace had ended, and its toplevel existed 35 ms before it mapped, so nothing
could have told the act to wait for it, and a longer grace would cost every
click. The session reports such a window in its next result instead. A late
window is one that the plugin found to come from the target of an `act` of
the session that delivered input that can open a window, by its `source`, and
that maps after that act ended, within 2 minutes, unless the user's own input
opened it. The next `act`, `screenshot` or `windows` of the session, on any
window and with any `match`, lists it in `late_windows`, with the fields of a
new window and a shot, and names it in the summary and the hint. It comes
only with a shot: a result that has none for it, with capture `"none"`, past
the 3 shots that a result takes of new and late windows together, or without
the time for one, leaves it to a later result, and so does a call that is
cancelled.

```
shot s16 1280×720 · window 0x5d963cd598b0 1920×1080 · late window 0x5d963c7794f0 (xdg-desktop-portal-gtk "Open Files") on workspace web (hidden)
shot s17 960×540 of late window 0x5d963c7794f0
Your input to 0x5d963cd598b0 in an earlier act opened window 0x5d963c7794f0 "Open Files" after that act had returned; it is in shot s17.
```

A failure of an `act` before any input, which comes with a shot, lists them
too. One result reports each late window, once; a `screenshot` or `act` of the late
window itself reports it with its own shot, and so does `windows` when it is
the one window listed. `windows` marks a late window in its line with `opened
by your input to ADDR`, and one that it leaves to a later result, past the
limit of shots, with `opened by your input to ADDR, not yet reported`; both
have `opened_by_input`. A late window past the limit that is the one window
listed is reported with the shot of that window, in `late_windows` too. In a benchmark, every agent whose click opened the
portal's file chooser after its `act` called `windows` next, and then
`screenshot` on the chooser, so `windows` returns its shot.
Another session does not report it, since the input was not its own. A late
window that maps while another `act` runs is that act's late window, not one
of its new windows. A window that comes from a target that has closed since,
such as a progress window after a dialog's Send, is late too. The plugin names
the source's `stable_id` while it lives, so a window from a later window at a
reused address is not taken for one.

### Waiting for a window

An agent that clicked Upload or Browse expects a window that may come from
another process a second or two later, after the `act` and its grace. With
`wait_ms`, `windows` waits for it instead of the agent calling again:

- With `match`, it waits until a window whose class or title contains it is
  there: one that maps, or one whose title changes to match.
- Without `match`, it waits until a [late window](#late-windows) of the
  session is there, a window that the plugin found the input of an `act` to
  have opened after that act had returned. Other windows that map meanwhile,
  such as one of another app, do not end the wait. When no `act` of the
  session in the last 2 minutes sent input that can open a window, none can
  come, so it does not wait, and says `did not wait: …`.

It returns at once when such a window is there when it is called, and as soon
as one comes, with the shots it would give at once: of the late windows, and
of the one window it lists. A window that mapped during the wait gets up to
1 s to draw what it opened with first, as a new window of `act` does. When
`wait_ms` passes without one, it lists the windows as without a wait, which
is no error, and says `waited N ms: no window with "TEXT" in its class or
title mapped` or `waited N ms: no window that your input opened mapped`. One
found after a wait says `found after waiting N ms`. `wait` in the structured
content has `ended`, `found`, `timeout` or `nothing`, and `elapsed_ms`.

`wait_ms` is at most 10000. The file chooser mapped 1.5 to 1.8 s after its
click in a benchmark, and a dialog whose app or portal has to start first
takes a few seconds more; a wait in vain costs no more than that, which stays
far within the tool timeouts of clients, such as Codex's 60 s. The whole call
has 10 s more for listing and shots.

The wait ends on Hyprland's events, not by polling: before it lists the
windows, `windows` connects to Hyprland's event socket, `.socket2.sock` next
to the request socket. Hyprland accepts that connection on its main thread,
possibly in the pass in which it answers the listing, after it, so a window
that maps in between sends its event to no one. `windows` therefore lists the
windows once more before it waits: Hyprland answers that in a later pass,
after the accept, so every window that maps after it sends its event. Then it
lists the windows again on each `openwindow`, and with `match` on each
`windowtitlev2`. Hyprland writes an event as it happens, on its main thread,
so the list that follows has the window, and the plugin has found its source
by then. Without the event socket, or once Hyprland dropped the connection,
which it does to a client 64 events behind, it lists the windows every
100 ms. A call that is cancelled, or the session's end, ends the wait at once,
and the connection with it; the late windows it found are left to a later
result.

### A window that closes

When the window closes during the call, the summary says `window closed` and
`window_closed` is true: when the wait ended so, and when the window closed
after the wait, before the capture or during the grace for new windows, as the
portal's file chooser did a moment after it had drawn the path that Enter gave
it. The capture that finds the window gone is no failure, the delivery stays as
it was, and a shot taken before the window closed is left out, since its
window is gone. The summary keeps how the wait ended, as in `3/3 delivered ·
redraw observed · quiet 201 ms · 492 ms · window closed`.

The result names the window that the closed window came from, where the agent
goes on, and returns a shot of it, taken as a new window's shot is: the
`source` that the plugin found for the closed window when it mapped, its
parent or the window of its app that input reached last (see
[Windows that input opens](#windows-that-input-opens)), if that window is
still open with the same `stable_id`. The agent works in it, since the plugin
found the closed window to come from it, and it is not listed again among the
late windows when it is one. A window that the user's own input opened, or
whose source has closed, has none.

```
3/3 delivered · redraw observed · quiet 201 ms · 492 ms · window closed · back to 0x5d963cd598b0 (chromium-browser "mara/website - Forgejo") on workspace web (hidden)
shot s15 1280×720 of 0x5d963cd598b0, the window it came from
The window closed. It came from window 0x5d963cd598b0, which shot s15 shows now: go on there with that shot's pixels. Any new windows are listed; call windows for the rest.
```

`source` has `window`, `stable_id`, `class`, `title`, `workspace`, `visible`
and `shot`, which becomes the window's last shot, the base of its next
changes. With capture `"none"` it has no shot, and the hint says to take a
screenshot of it. A failure whose window closed while its input was sent comes
with the shot too, and is then no tool error. The windows that the closing
input opened are new windows, as before.

## Failures

### Delivery

| `delivery` | Meaning |
| --- | --- |
| `none` | Nothing was sent. Safe to fix and retry. |
| `complete` | Every action was delivered: the plugin passed every event to the window's application. Whether the application acted on them as meant, only the shot can show. When the input closed the window and the plugin had confirmed every event sent, what needs no window counts as delivered too: the releases still to come of the action that closed it, which the plugin sends itself when the sender goes away, and the pauses after it, which end there. |
| `partial` | Actions before `failed_index` were applied; the action at `failed_index` may be partly applied, for example some characters of a `type`. The plugin took everything that was sent. Where known, `progress` says how much: the keys the plugin processed, not the characters the application inserted. |
| `uncertain` | The connection ended, or the plugin stopped answering, before the plugin had taken everything that was sent. Any action from `failed_index` on may or may not have been applied. |

`done` counts the batch actions whose every event was delivered. After input
was sent, a failed batch reports `done` equal to `failed_index`; with nothing
sent, `done` is 0, and the message names the action that was refused. When
the window closed during the batch, the message and the hint name the actions
delivered before it closed, so that they are not sent again, and
`failed_index` is the first action with input that it no longer got, after any
pauses.

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
because its screenshot failed. A capture that finds the window closed is no
failure at all: the result reports the window closed, see
[A window that closes](#a-window-that-closes).

### Errors

A failure that comes with a shot is a result without `isError`: the call fell
short, but captured the window, and the agent goes on from that shot. Clients
such as Claude Code show only the text of a tool error, so the shot would not
reach the agent otherwise. Failures without a shot are tool results with
`isError: true`. Either way the text is `error <code>: <message>`, then for
`act` the summary line when something was sent or a shot is attached, then the
hint. The structured content has `error`
(`code`, `message`), `summary` when the text has one, and `hint`, and for
`act` also `actions`, `new_windows` and, when there is one, `shot`.

| Code | Delivery | Meaning and hint |
| --- | --- | --- |
| `invalid_arguments` | none | Names the field by its path, for example `actions[0].count must be 1 to 3, not 5`, `actions[0].from must be [x, y] in shot pixels, not [100]` or `actions[0].x must be a number, not null`, or the key name the sender did not know. Also a batch that its `deadline_ms` cannot fit, also once the sender has resolved its keys with the keymap, or a `wait.expect_ms` that does not fit in it, naming the `deadline_ms` needed. |
| `window_gone` | none, partial or uncertain | The window closed or its `stable_id` changed. Call `windows`. When it closed while the input was sent, maybe because of it, `wait.ended` is `window_closed`, no shot is taken, and the message names the actions delivered before it closed: `window 0x… closed after actions[0] (key) was delivered, while actions[1] (type) was sent`. When only pauses were left once the plugin had confirmed the input, as after a click on the button that closes a dialog, the result is no error: it reports `complete` and `wait.ended` `window_closed`. Events in flight when it closed, such as a release sent as it closed, are not counted as delivered, and the delivery stays `partial` or `uncertain`. Either way the result names the window it came from, if it has one, with a shot, as [A window that closes](#a-window-that-closes) describes. |
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
Batch only targets visible in the shot: a menu, dialog or page that opens needs a new shot before you click in it; keys you know, such as arrows and enter in a menu, may follow. Whether a menu is still open shows in the shot: if it is, click its entry, as clicking its opener closes it; if it closed, open it and pick by keyboard in one batch.
drag, clicks with modifiers, and chords holding a key other than a modifier while pressing the next need the hold capability.
type uses the active keymap; a line break presses Enter and a tab presses Tab, so for a new line in a chat or form field use a key such as shift+enter. At 15 ms a key, two or more for a character typed through Compose, a call types about 600 characters, or 1400 with deadline_ms 30000; split longer text.
Delivered input reached the app but may not have taken effect: a busy one, such as a game or a web app, can drop keys or take them as shortcuts, so check typed text in the result.
Never repeat input whose delivery is partial or uncertain, or from a call that timed out, before looking at a fresh shot.
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
{"ok":true,"version":"0.3.0","interface":3}
```

`interface` increases with every incompatible change to these commands. New
fields in replies are compatible: `socketId`, `resizing`, `starved`,
`interrupted` and the `stableId` of `source` came without a new interface. The
server reads replies without `socketId` as from a plugin that names no sockets,
without `resizing` as of a window that has caught up, without `starved` as of a
window that is not starved, without `interrupted` as from a plugin that counts
no interruptions, and a `source` without
`stableId` as one that is gone: it cannot tell such a source from a later window
at its address, so with that plugin it reports no
[late windows](#late-windows) and no window to go back to.
The server refuses to run against another interface (`plugin_version`).

**`state <addr>`**

```json
{"ok":true,"address":"0x55d1c0a3e4b0","stableId":"1a","xwayland":false,
 "size":{"width":640,"height":400},"resizing":false,"starved":false,"scale":2,
 "socket":"/run/user/1000/hyprmasq/<instance>/0x55d1c0a3e4b0.eis","socketId":"9f3a61c2-3",
 "commits":123,"interrupted":0,"lease":{"held":true,"remainingMs":58210,"owner":"mcp-4242-a1b2c3d4"},
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
- `starved`: the window is not rendered, has asked for a frame callback that
  it has not got (on its surface, a subsurface or a popup), and has got none
  from hyprmasq: its socket has sent it none since it opened, or it has none.
  Hyprland sends hidden windows no frame callbacks, so such a window may not
  have drawn what it would draw on screen, such as an application that
  started on a hidden workspace. Opening the socket sends none; input, a
  connected sender and a `wait … since` do (see
  [Frame callbacks](#frame-callbacks)), and from then on the window is not
  starved, also once it waits for a frame callback again. A screenshot answers
  the callbacks once, after the image, which leaves a window starved that asks
  for the next one.
- `socket`, `socketId`, `commits` and `interrupted` are `null` without an open
  socket. `lease` is `null` when none is held.
- `interrupted` counts the times the user's own key, modifier or button to the
  window's Wayland connection took the keyboard focus back from the window
  within a quarter of a second of a key it pressed with Ctrl, Alt or Super
  held, a function key, Escape or a media key, see
  [Shortcuts beside the user's window](#shortcuts-beside-the-users-window).
- `socketId` names the socket: it differs for every socket the plugin opens,
  also for one opened again for the same window, and in every load of the
  plugin. Plain text prints it as a `socketId:` line.
- `capabilities`, as observed now:
  - `lease`: a Wayland window that does not share its Wayland connection with
    the window that has the real keyboard focus. Windows of Hyprland's
    XWayland and of xwayland-satellite count as X11.
  - `hold`: sharing its Wayland connection with neither the real keyboard
    focus nor the real pointer focus. Same-connection input restores the
    user's pointer focus after every input frame, which releases held
    buttons, and the user's keyboard focus once the window's keys are up and
    a shortcut among them has settled, or at once when the user's own input
    to the connection comes,
    which gives a click the user's modifiers instead of its own. A keyboard
    frame after that enters the window again with no keys held, only with the
    modifier state of the keys the sender holds, so of the keys of a chord
    only modifiers stay pressed.
  - `dnd`: the window is the active window. Drags from other driven windows
    are cancelled. `hyprmasq-mcp` does not report it.
  - `keyboard`: `x11-focus` for X11 windows of Hyprland's XWayland or
    xwayland-satellite whose socket was opened without `force`, or that have
    no socket, otherwise `yes`.
- `source`: the window this one came from, as the plugin found it when it
  mapped, see [Windows that input opens](#windows-that-input-opens):
  `{"window", "stableId", "placed"}`, with `reason` (`disabled`, `user_input`
  or `workspace`) when `placed` is false, or `null`. `stableId` is that
  window's while it lives, and `null` once it is gone, so it tells the window
  from a later one at its address. Plain text prints a `source:` line.

**`launch <workspace> <token> <command line>`** starts the command line with
`/bin/sh -c` through Hyprland's executor, and answers `{"ok":true,"pid":N}`.
The workspace and the command line are written in hex, two digits a byte,
since `hyprctl` and Hyprland look for words such as `--batch`, `/instances`
and `rollinglog` anywhere in a request. The workspace is a name of letters, digits,
`_`, `.` and `-`, the token 1 to 64 hex digits. The plugin remembers the
process with its start time, which tells it from a later process with the same
pid.

**`launched`** lists the mapped windows of launches:
`{"ok":true,"windows":[{"address":"0x55d1c0a3e4b0","stableId":"1a","token":"9c1f0e4ab27d3365"}]}`,
with the `stableId` that tells a window from a later one at its address.
When Hyprland creates a window, before it maps it, the plugin follows the
parents of the window's process in `/proc/<pid>/stat`, which the kernel answers
without waiting for the process, up to 32 of them and up to Hyprland itself. A
window whose process is a launched one or descends from it belongs to that
launch, also one of a child that a launcher script starts without `exec`, as
Chromium's does. Within a minute of the launch, the plugin tags the window
`hyprmasq-<n>` and registers a window rule, `workspace name:<workspace>
silent;no_initial_focus 1`, that matches that tag. Hyprland ignores a workspace
rule, `silent` included, that names the workspace the window would open on
anyway, so `no_initial_focus` keeps the focus also when that is the active
workspace. Hyprland matches tags on the window itself,
while matching an exec rule would read the environment of the window's
process, which can wait for that process. Hyprland applies the rule as the
window maps, and the plugin then removes the rule and the tag, as it does for
a window that is destroyed before it maps. Registered after the rules of the
configuration, the rule takes precedence over their workspaces. A window that a
program started in a launched shell opens later than a minute after the
launch opens where Hyprland and the user's rules put it, unless input opened
it.

**Windows that input opens.** An application cannot tell emulated input from
the user's, so a window that input opens, such as a browser's popup, a dialog
or a portal's file chooser, would open on the user's workspace and take the
focus. Hyprland creates a Wayland window, and emits `window.create`, when the
client asks for its `xdg_toplevel`, before the client can name a parent, and an
X11 window at its `CreateNotify`, before `WM_TRANSIENT_FOR` and `_NET_WM_PID` are
set. So the plugin decides where a new window came from in a hook on
`Desktop::Rule::CWindowRuleApplicator::readStaticRules`, before Hyprland reads
the window's rules: for a Wayland window at its first commit, where clients
have usually named the parent and before Hyprland suggests a size for the
window, and again when Hyprland maps it, where the parent and the X11 process
are known; for an X11 window when it maps. The decision at the map keeps,
moves or takes back what the first commit placed. A new window comes from

- its parent, when it has one: an `xdg_toplevel` parent, `WM_TRANSIENT_FOR`, or
  through xdg-foreign a window of another process, as a portal's file chooser
  has. The parent counts when input reached it through its socket or the plugin
  placed it, also by a launch, and the user's own input to it is older than that;
- otherwise the window of the same app, a Wayland connection or for Hyprland's
  XWayland an X11 process, that input reached last, within the last 5 seconds,
  when the user's own input to that app is older. The plugin keeps the
  workspace and parent of each window that input reached for those 5 seconds,
  so a window that the app opens after that one closed, such as a progress
  window after Send in a dialog, still opens on its workspace, or on that of its
  parent when the workspace is gone. The windows of an
  xwayland-satellite, which hosts every app of its X server on one connection
  and process, are left out.

The user's own input is a key or a mouse button, counted for the window and app
with the keyboard or pointer focus that gets it, and a touch or a tablet tip,
counted for the window that Hyprland focuses for it. Any of it counts for a
window that the agent works in, but for the rest of the app only input that
can open a window: a button, a touch, a tablet tip, or a key press with Ctrl,
Alt or Super, or of a key that does not type, edit or move in text, such as
Enter or a function key. Enter does not count while
`m_relay.getFocusedTextInput()` is enabled, since the focused text field takes
it as a line break. Without a parent, the window is the user's when their input
to the window that the agent's input reached last is newer than that input, or
their input to the app that can open a window is newer than the agent's last
such input to the app, or than its last input when it had none, counting the
modifiers that the sender holds for emulated keys. An app does not tell
which input made it open a window: Hyprland's `xdg_activation_v1` ignores the
serial and surface of a token, and Chromium names the last press on the seat
and the surface with the keyboard focus, which are the user's while they type.
The plugin then places the
window silently on that window's workspace with the same tag and rule as a
launch, naming a numbered workspace by its number and others by their name,
and adds `float` to the rule: for now, the window floats at the size it asks
for, so the window it came from keeps its size, and shots of it stay valid.
A window that a launch's rule places already, in the launch's first minute,
keeps the launch's workspace, and its rule gains `float`. A window from a
workspace whose name a rule cannot give, a named or special one with
characters other than letters, digits, `_`, `.` and `-`, is not placed, and
its `source` says `workspace`. With `plugin:hyprmasq:place_new_windows` false, it places none. A window that
the plugin placed, by a launch or next to its source, is marked urgent but not
focused when it asks for activation, also with `misc:focus_on_activate`, for as
long as it lives; a forced activation, such as one through a taskbar, still
focuses it. This also covers apps that no launch started, such as the user's own
browser, and the windows of a launch after its first minute. `launched` lists
such a window only when it belongs to a launch.

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
  another size. `starved` is the window's `starved` from `state` right before
  it was rendered: the image may show an older frame than the window would
  draw.
- A box larger than the GPU's texture limit is `internal`.

**`wait <addr> since COMMITS [expect MS] [quiet MS] [deadline MS] [id WORD] [ignore animations]`**

The new form. `wait <addr> [QUIET-MS [TIMEOUT-MS]]` keeps working as before.
Needs an open socket (`no_socket` otherwise) and a request through Hyprland's
socket (`unsupported` otherwise). Each option may come once, in any order.

- With `expect`, a change phase runs first: it ends at once when the counter
  is already above COMMITS, otherwise at the first commit, and the wait ends
  with `no_redraw` when `expect` milliseconds pass without one.
- The quiet phase ends once there has been no commit and no input through the
  socket for `quiet` milliseconds (default 200), counted from the later of the
  last commit and the last input, but not before the wait started, and the
  window is not `resizing`. Commits of an animation do not count (see
  [Animations](#animations)). A client waits for a window to catch up with a
  resize with a wait without `expect`.
- `deadline` (default 5000) ends the wait with `timeout` in either phase. A
  phase's ending wins a tie with it.
- `expect`, `quiet` and `deadline` are 1 to 60000.
- While the wait runs, a window that is not rendered gets frame callbacks
  every 16 ms, also one that stops being rendered during the wait. They stop
  when the wait ends.
- `id WORD` names the wait for `cancel`. An id of a pending wait is
  `invalid_arguments`.
- With `ignore animations`, the change phase waits for a commit after
  COMMITS that is not part of an animation: it ends at once when one came
  before the wait, and animation does not end it. Another value is
  `invalid_arguments`.
- COMMITS above the counter is `invalid_arguments`.
- A socket that closes during the wait does not end it.
- The wait never blocks Hyprland's main thread. In a `hyprctl --batch` whose
  reply another command sends at once, such as `plugin load`, the wait runs
  on unanswered until it ends or is cancelled. If the plugin cannot start a
  thread for it, the reply is `internal`.

```json
{"ok":true,"ended":"quiet","commits":131,"socketId":"9f3a61c2-3","redrawObserved":true,"commitsSince":8,"quietMs":200,"elapsedMs":412,"resizing":false,"animated":[{"x":12,"y":40,"width":300,"height":20}]}
```

`ended` is `quiet`, `no_redraw`, `timeout`, `window_closed` or `cancelled`.
`commits` is the counter when the wait ended, `socketId` the socket whose
counter the wait read, `commitsSince` the commits after COMMITS,
`redrawObserved` whether there were any, `quietMs` how long the window had
been quiet when the wait ended, apart from animations, `resizing` whether it was resizing then, and
`animated` the boxes of the animations drawn during the wait, in whole window-local logical pixels. When several endings hold at once,
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
  with REPEAT 1 to 100, `sleep` 0 to 3600000 ms, `sync` MIN up to MAX up to
  3600000 ms, drags of at most 2000 steps,
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
- **`sync MIN MAX`** stops the run once the round trip after the action before
  it has confirmed everything sent: the sender prints `{"sync":N}` on stdout, N
  the number of actions before the sync, and goes on when a line with N
  arrives on stdin, at least MIN and at most MAX milliseconds later, at once
  after MIN when stdin is closed. A line with another number, such as a late
  answer to an earlier sync, does not count. It handles the plugin's events
  meanwhile and keeps its devices, so the window keeps its emulated focus as
  during a `sleep`, and a signal, a pause or the end of the connection stop it
  as they stop a `sleep`. It takes the place of the 50 ms between the actions
  around it: the next one starts once the sync has ended and 50 ms after the
  end of the one before the sync. `--budget` counts its MIN, as the server's
  estimate does, and not how long the caller takes to answer, which the server
  keeps short enough for the rest of the batch.
- **`--json`** prints one object on stdout when the sender exits, as its last
  line, after the lines of its syncs:

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
- Input that closes its own window: when the plugin closes the connection
  after confirming every frame sent, an action that had only the releases of
  what it holds left counts as done, since the plugin releases what a sender
  holds when its connection ends, as when the window closes while a click's
  button or a key is held. So when that action was the last one, the run
  succeeds with `complete`. Otherwise it gives `unconfirmed` with `partial`,
  also at a sleep, which stops with the connection, or with `uncertain` when
  frames sent after the close never reached the plugin. The server handles
  both: after a complete run its wait ends with `window_closed`. After a
  failed one whose window is gone, the pauses from `failedIndex` on count as
  delivered, since they need no window: with only pauses left the batch is
  complete, and otherwise the call fails with `window_gone` at the input after
  them. Another sender that takes over the socket leaves the window open, and
  a pause it cuts short fails the batch.

### hyprmasq-mcp

- Go module `github.com/nyrda/hyprmasq/mcp` in `mcp/`, command
  `mcp/cmd/hyprmasq-mcp`, built with the Go of the pinned nixpkgs and the
  official Go SDK v1.8.0.
- Options: `--input PATH` (default: `hyprmasq-input` from `PATH`),
  `--max-image-size N` (default 1280, 0 for the monitor's scale), `--lease-ms`
  (default 60000, 1 to 600000), `--new-window-grace-ms` (default 500, 0 to
  5000, see [New windows](#new-windows)), `--structured-content
  auto|always|never` (default `auto`, see [Results](#results)), `--version`.
  It needs
  `XDG_RUNTIME_DIR` and `HYPRLAND_INSTANCE_SIGNATURE`.
- Talks to `$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock`
  with `j/hyprmasq …`, `j/clients`, `j/monitors`, `j/activewindow` and
  `/dispatch` requests, one
  connection per request, each written at once. Hyprland reads requests in
  pieces of 1023 bytes and blocks its main thread on a request that fills its
  last piece exactly, so such requests get a trailing space. Requests over 32
  KiB are refused, so an overlong `launch` command is `invalid_arguments`.
- Reads Hyprland's event socket, `.socket2.sock` in the same directory, only
  while `windows` waits with `wait_ms`, on a connection of its own for that
  call (see [Waiting for a window](#waiting-for-a-window)).
- `launch` has the plugin start `exec` and the command, each argument quoted
  for the shell, as `hyprmasq-launch` does, with the plugin's `launch` under a
  random token. It finds the window among those the plugin's `launched` lists
  with that token, polling every 100 ms, by address and `stable_id`, since the
  window may close between the two lists and leave its address to another. The plugin found them by the launched
  process among the parents of their process, and placed them on the
  workspace (see [Plugin commands](#plugin-commands)). The `launched` mark of
  `windows` comes from the same list. It then settles it with a wait without a change phase: quiet
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
`state`, screenshots and waits; a starved window, which draws only in answer to
frame callbacks, in `state` and screenshots, until a wait feeds it; Wayland and X11 windows popping in, whose
shots have their size while Hyprland draws them smaller, and popups of a
window that moves; the lease, its end when the real focus moves to its
connection or input reaches another window of it, and release of held input
on disconnect, also when libeis reports none; new windows that input opens,
placed on the workspace of the window they came from without the focus, by
their app or their parent, also one of another process that names its parent
through xdg-foreign, and a dialog of the user's own window of the same app; pacing measured with `wev`
timestamps; 2000 characters typed exactly; a budget that refuses text the
keymap types through Compose as more keys; Compose sequences of the target's
locale, also without a table for it; input paused on X11 with the keys
that got through; input that closes its own window, also with only releases
and sleeps left, and input that it drops;
a connection taken over during a burst; SIGTERM while the plugin does not
answer; a shortcut that runs, as Chromium's, a moment after its key in a
hidden window of the connection with the real focus, and the user's typing
beside it, which reaches the user's window alone and is counted when it
interrupts the shortcut.

**MCP subtests** drive `hyprmasq-mcp` over stdio from the test script:
launch, results without structured content for a client that introduces
itself as `claude-code`, `windows` with `match` and the shot of the one window
it lists, typing with a redraw and a
lease, the wait between a click and the keys after it, coordinates at scale 1 and 2 and in
zoom shots, `stale_shot` after a resize, before and during a batch and while
a call waits for the send lock, `out_of_bounds` (also for a popup above the
window), `invalid_arguments`, also for text that the keymap types through
Compose as more keys than the deadline fits, `cannot_type`, typing through the
Compose table of the window's locale and the hint without a table for it,
`unsupported` for
a drag, a click with modifiers and a chord that holds a letter on a shared
connection, where a chord of Shift and a letter still types a capital, a
shortcut there that the user's typing interrupted, partial
delivery when X11 input is paused, the wait outcomes, the lease across calls and on exit, new
windows that the input opens, placed beside the target with a shot each, also
one that maps after the wait, and with placement off one that has the user's
focus, a dialog that maps after its act has returned, which the next
`windows`, `screenshot` and `act` return with a shot, which `windows` right
after the click lists only once it has mapped, which `windows` with `wait_ms`
returns as soon as it maps, and which a `match` with one result returns with
the shot that `act` takes and compares with, Enter that closes its
dialog after the wait has ended quiet, delivered without a failed capture,
and the shot of the window that a dialog came from when Enter or a click
closes it, and none for a dialog whose parent is gone or the user's, two
servers acting at once on one window, the deadline,
cancellation, `act` with a capture region, also when the window is resized
during the call, changes shots of a terminal that the test draws in, from no
change and one region, whose zoom shows the drawn cells at their place, to
several, tiny changes left out and a highlight below the threshold, pointer
input through a changes shot and through its zoom, a popup of about the luminance
of its window, each reason for a full shot that a window can give, and
`"window"` and `"none"` as they were, `--max-image-size`, a window that
catches up with a resize during a screenshot and the refusal of coordinates
from a shot of its older frame, `launch` and `close` naming the window they
resize, `windows` and an `act` that times out reporting it as resizing, the
first shot of a starved window, which shows its content, of one that never
draws, which is marked as maybe stale, and of the same windows once fed,
coordinates from shots of Wayland and X11 windows that pop in, input that
closes its window, with text or a pause after it, SIGTERM to the server,
`close` and exit.

**Go tests** in `mcp/` run the server against a fake Hyprland and a fake
sender, including resizes during a batch, windows that do not catch up with a
resize, starved windows that draw and do not draw before the capture, and
one that `act` had no time to let draw, cancellation while sending and
waiting, lock contention, the checks after the locks, also of an `act`
without actions whose window another one replaced while it waited, capture regions of a window resized during the call,
socket ids, lost lease answers, session ends and exit
statuses, new windows with their workspace, focus, source and shots, also
one that maps after the wait, late windows in the next screenshot, act,
`windows` and failure with a shot, once, also when `match` leaves them out,
and marked by `windows` past its limit of shots unless it is the one window
listed, which no other call reports while `windows` takes its shot, left to a later
result with capture `"none"`, past the limit of shots and when the call is
cancelled, the shot of the one window that `windows` lists, as from
`screenshot`, also of a starved window, and none of several, `wait_ms` that
returns at once, waits until a window maps or its title matches, also one
that maps before Hyprland has accepted the event connection, waits
without `match` for a late window and not for another window, ends at its
limit without error, ends with its subscription when cancelled, does not wait
when nothing can come, and polls without the event socket, also of a target that closed, and none from another window at a reused address, of the
user's input, of another session or after the watch, windows that close after
the wait, before or after the capture, the shot of the window that a closed
window came from, once also when it is a late window, and none for one that is
gone, replaced or the user's, no note about a capture region's shot of a window
that closed during the grace,
argument errors of every tool through an SDK client, `null` among
them, the hints, also the one after input that can open a window, the length of every tool description, structured content by
client and `--structured-content`, `windows` with `match`, the sleep in
front of keys that follow a click or drag too soon, and changes shots: one
region and several, merged regions, regions joined until 5 are left and
regions too scattered or too many to join, no change and the hint after input that
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
- Only a window that has got no frame callbacks from hyprmasq counts as
  starved. A hidden window that was driven, and wants to draw again after its
  frame callbacks stopped, a second after the last input or when the wait
  ended, shows its older frame in a screenshot without a mark; an `act`
  without actions lets it draw. Counting every hidden window that waits for a
  frame callback would delay screenshots of idle windows by a second: many
  applications ask for one with every frame they draw, also the last, and draw
  nothing when they get it.
- A window that is moved during a batch, but keeps its size, is not noticed
  by the server. The plugin replaces the pointer device, and a later point
  that lies outside the new pointer regions fails with `out_of_bounds` and
  delivery `partial`; points inside them land at the same window-local
  position.
- Windows without a lease, X11 windows and windows sharing a connection with
  the user's focus, lose the emulated focus when the batch ends, and what the
  app closes on focus loss may already be gone in the act's own shot.
- A shortcut in a window that shares its connection with the user's focused
  window does nothing when the app takes more than a quarter of a second to
  run it, as a page busy with a script may, and the result does not say so.
- Leased windows keep their emulated focus while the session is locked. The
  user's real keys never reach them.
- Plugin arguments are split at whitespace, so an `XDG_RUNTIME_DIR` with
  spaces breaks the shot paths.
- If the plugin ever replaced the keyboard, for example on a keymap change,
  the sender would stop with `no_devices`. Reconnecting picks up the new
  keymap.
- A window that maps more than `--new-window-grace-ms` after an `act`'s wait
  comes with the session's next `act`, `screenshot` or `windows`, not with
  that act, and one that maps more than 2 minutes after it as no late window.
  `windows` with `wait_ms` waits for it. One that the plugin cannot relate to
  the input, such as a window of another app without a parent, ends such a
  wait only with `match`.
- The hooks on `Desktop::View::CWindow::updateWindowDecos`,
  `Desktop::Rule::CWindowRuleApplicator::readStaticRules`,
  `CSeatManager::setKeyboardFocus`, `CSeatManager::setPointerFocus`,
  `CSeatManager::sendKeyboardKey`, `CSeatManager::sendKeyboardMods` and
  `CSeatManager::sendPointerButton`, and
  the access to the private `m_currentSurface` of `CWLKeyboardResource` and
  `CWLPointerResource` and to the private `m_window` of
  `CWindowRuleApplicator`, have to be checked again whenever the Hyprland pin
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
