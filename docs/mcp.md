# MCP server

`hyprmasq-mcp` gives coding agents computer use on Hyprland: Claude Code,
Codex, pi and any other client of the [Model Context Protocol](https://modelcontextprotocol.io).
An agent can list and launch windows, take screenshots, and click and type in
any window, also on a hidden workspace, while you keep working in yours.

The server speaks MCP over stdio, so each agent session starts its own.
[Installation](installation.md#mcp-server) covers registering it. The tool
descriptions carry every rule an agent needs, such as to drive only windows
the user named or the agent launched and to ask before anything irreversible,
and the [first view](#the-first-view-of-a-window) of each window adds what an
agent needs once before acting on it, so agents need no further instructions.

## Tools

| Tool | Main arguments | What it does |
| --- | --- | --- |
| `windows` | `match`, `wait_ms` (0 to 10000) | List the windows, or those whose class or title contains `match`: address, class, title, workspace, visible or hidden, size, and the capabilities each lacks. When it lists exactly one window, it also returns a shot of it. With `wait_ms`, it first waits for a window that is not there yet. |
| `launch` | `command` (array), `workspace` (default `hyprmasq`), `timeout_ms` (1000 to 300000, default 30000) | Start an application on a hidden workspace without a shell, wait for its first window to appear and settle, and return its address with a shot. |
| `screenshot` | `window`, `region` | Take a shot of the window now. With `region`, zoom into part of an earlier shot. |
| `act` | `window`, `shot`, `actions`, `next_page`, `wait`, `capture`, `deadline_ms` | Send input, wait until the window has drawn the result, and return what changed. See [act](#act). |
| `release` | `window` | End the window's focus lease. The window stays open. |
| `close` | `window` | Ask the window to close, as Hyprland's close dispatcher does, and report within 2 seconds whether it closed and which windows appeared, such as a confirmation dialog. It refuses X11 menus and X11 windows that take no close request, see [X11 menus](#x11-menus). |

No tool is read-only. `screenshot`, and `windows` when it takes a shot, open
the window's input socket, which stays open, so the plugin treats the window
as driven from then on.

## Shots and coordinates

A **shot** is a screenshot that the server remembers under a short ID, such
as `s12`. Every coordinate an agent sends is in the pixels of one shot, with
0,0 at the image's top left. The server keeps the transform to the window, so
the agent never converts anything.

- Shots of Wayland windows include their open popups, also where they reach
  past the window's edges. An X11 app's menus and tooltips are windows of
  their own and are not in its shots. See
  [X11 windows](automation.md#x11-windows).
- Overview shots are fitted within 1280 pixels on their long side by default
  (`--max-image-size`).
- A **zoom** is a shot of part of an earlier shot, captured again at the
  monitor's full scale: `screenshot` with
  `region: {shot, x, y, width, height}`. It reads small text and hits small
  targets, and `act` takes its own pixels as they are, with the zoom as
  `shot`. Its summary line says so, where the agent reads its next
  coordinates:
  `shot s3 465×921 · zoom of s1 at 10,96 310×614 · window 0x55d1c0a3e4b0 1280×800 · act takes s3's own pixels`.
- Before each capture, the server waits briefly, at most 1.5 s, for a window
  that has not yet redrawn at a new size, or a hidden window that has not
  drawn yet. A shot taken anyway is marked stale or "may be stale" in the
  summary.
- The server keeps the 64 most recently used shots of each window, as files
  under `$XDG_RUNTIME_DIR/hyprmasq/<instance>/mcp/<pid>/`, and deletes them
  when the session ends.

### The first view of a window

The first usable full shot of each window, from `windows`, `screenshot` or
`launch`, comes with a note of what an agent needs once before it acts on
that window: notch scrolling for short moves and `next_page` for long pages,
how an open menu behaves, how much one `act` types, and that a zoom of the
hardest part of precise work shows whether it landed. The note states facts
about the tools rather than orders, since models weigh orders in a tool's
result less than facts. Keeping it out of `act`'s description leaves that
description to what applies to every call, within the 2048 characters that
Claude Code keeps of a tool description.

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

The shot identifies its window, so `window` is optional when `shot` is given.
Without a shot, give `window`. An unavailable shot is refused before input.

One `act` call:

1. **Checks** the whole batch before sending anything: arguments,
   coordinates against the shot, the window's capabilities, key names and
   whether the keymap can type every character.
2. **Refuses stale targets.** If the window was resized since the shot, or
   the window no longer shows what the shot showed around a click point, or
   the ends of a path with a button, the call fails with `stale_shot`, sends
   nothing, and returns a fresh shot to read coordinates from.
3. **Sends the input** through `hyprmasq-input`.
   After a click, or a path with a button, that more actions follow, it waits
   for the window to answer before sending the rest, and keys after a click start at
   least 300 ms after it.
4. **Waits** until the window has drawn the result: first for a redraw
   (`expect_ms`), then until it has been quiet for `quiet_ms`. It does not
   wait for animations, such as a spinner, or the overlay scrollbar that
   GTK fades in when the pointer moves, and the summary names their boxes.
5. **Captures what changed.** By default it compares the window with the shot
   and returns a **changes shot**: a zoom into each changed region, with the
   coordinates of the earlier shot still valid. When a full shot serves
   better, for example after scrolling or when more than 35% of the window
   changed, it returns one and says why.

```text
1/1 delivered · redraw observed · quiet 214 ms · 655 ms · shot s7 = s5 + 2 changes · lease 60 s
zoom s8 496×80 of s7 at 530,0 248×40
zoom s9 456×124 of s7 at 0,670 228×62
```

`shot s7 = s5 + 2 changes` means s7 has s5's pixels, so the agent keeps
reading coordinates from s5, and each zoom is listed with the box of s7 it
shows. Tiny changes, such as a blinking caret, are left out and counted.
`redraw observed` alone does not show that the input changed anything: a
hidden app that the input wakes, such as a Qt app, redraws once anyway. The
changes say what changed.

### Compact actions

Actions can use arrays in place of the named-field objects below. A point
pair `[x, y]` is one left click. All forms send the same input and use the
same checks. Omit trailing defaults; use an
object when you need to skip an optional position.

| Action | Array |
| --- | --- |
| click | `[x, y]`, or `["click", x, y, button?, count?, modifiers?]` |
| move | `["move", x, y]` |
| path | `["path", points, curve?, button?, modifiers?]` |
| scroll | `["scroll", x, y, dy, unit?, dx?]` |
| type | `["type", text]` |
| key | `["key", keys, repeat?]` |
| pause | `["pause", ms]` |

`keys` accepts a single name such as `"right"`, a chord such as `"ctrl+l"`,
or an array such as `["ctrl", "l"]`. The question marks above mark optional
positions; they are not part of a request. Do not use null placeholders.
For example, `[[412, 88], ["type", "hello"], ["key", "enter"]]`.

### Continuing through a scrollable window

Use ordinary notch scrolling for short moves. `next_page` takes additional captures to
measure movement and retain overlap when continuing through a long page. Set it in the
same `act` call as the edits to the visible part:

```json
{"shot":"s4","actions":[[412,88]],"next_page":{"x":900,"y":600,"direction":"down"}}
```

Choose a point on the scrolling background, away from controls. `direction`
defaults to `down`; `up` goes back. Without `actions`, it only scrolls.
The server sends wheel notches and compares screenshots to measure their
movement. It aims to expose the next part while retaining about 15% overlap,
rounded down to whole notches. It uses no application data or PageDown key.

The edits settle and appear before the scroll. When one crop can show all
changed regions within the image budget, they share that crop. The full new
view follows it, and supplies the next coordinates. Check the pre-scroll changes to
verify the edits before continuing from the new view. A page scroll always returns
that full view. The result's `scroll` reports the notches, measured
movement and viewport height in logical pixels, and why it stopped.

If the images cannot be aligned confidently, a control takes the wheel, the
window fails to settle or time runs short, it stops. A failed measurement or
no movement is not a claim that the page ended. Repeating patterns, animations
and separate panes can prevent measurement; inspect the returned view and
use ordinary notch scrolling there.

`deadline_ms` covers the edits and every scrolling step. The checks that hold
back a trailing scroll after failed or unsettled input apply here too.
Use `capture: "changes"` or `"window"`; `"none"` and a region are refused
before input. There can be no earlier scroll in the batch. `next_page` counts
as its last action. The equivalent explicit action is
`{"action":"scroll","x":900,"y":600,"dy":1,"unit":"page"}`,
with `dy` either 1 or -1, last in the batch.

### A scroll at the end of a batch

A batch that ends with one or more `scroll` actions after a click, a path
with a button, a key or text runs in two parts, so that the agent sees what its input did before
the page moves on. `act` sends the actions before the scroll, waits for the
window and captures what changed since the shot. Then it sends the scroll,
waits again and captures what changed since that capture: a full shot once
the page has moved, and otherwise the part of the window that the scroll
changed, such as a dropdown under the pointer that took the wheel.

```text
3/3 delivered · lease 60 s
before the scroll: redraw observed · quiet 200 ms · 412 ms · shot s9 = s8 + 2 changes
zoom s10 496×80 of s9 at 530,0 248×40
zoom s11 456×124 of s9 at 0,670 228×62
after the scroll: redraw observed · quiet 200 ms · 230 ms · shot s12 1280×720 (full shot: 64% changed since s9)
```

The zooms show `s9`, which has the pixels of `s8`, and the next coordinates
come from `s12`. The structured content has the final state at the top level,
with the scroll's wait and shot, and in `before_scroll` the wait and the shot
of the actions before it. `deadline_ms` covers both parts.

The scroll is held back unless the actions before it were delivered whole,
the window drew and settled after them, they opened no window, their changes
were captured, and time is left for the scroll. The result is then that of
the actions before it, with `delivery` `partial`, `failed_index` at the
scroll and `held_back` naming the reason, and its summary starts with
`2/3 delivered, scroll held back`. A batch with a scroll in the middle, with
nothing but motion and pauses before its scroll, or with `capture`
`"window"`, `"none"` or a region runs as one.

Scroll with `unit` `notch`, a wheel click, over the page's background. A notch
moved the pages of every toolkit we measured by 100 to 120 logical pixels.
`px` is smooth scrolling, which apps scale as they like: the same request
moved Qt 6 times as far as GTK 4, and GTK 3 can fling the page to its end. A
dropdown under the pointer takes the wheel in Qt, GTK 3 and Tk, and a slider
or other control can too, which then changes its value instead of moving the
page.

| Argument | Default | Meaning |
| --- | --- | --- |
| `window` | required | Window address from `windows` or `launch`. |
| `shot` | required for coordinates | The shot the coordinates were read from. |
| `next_page` | absent | After the visible edits, scroll a measured page at `{x,y,direction}`. See [Continuing through a scrollable window](#continuing-through-a-scrollable-window). |
| `actions` | `[]` | Up to 1000 actions, run in order. An empty list waits for a change since the shot, then captures. |
| `wait` | | `expect_change` (true), `expect_ms` (1000, at most `deadline_ms` − 1000), `quiet_ms` (200, 1 to 10000). |
| `capture` | `"changes"` | `"changes"`, `"window"` for a full shot, `"none"`, or a region `{shot, x, y, width, height}`. |
| `deadline_ms` | 15000 | 1000 to 30000. Covers the whole call, and the last second is reserved for the capture. |

| Action | Fields |
| --- | --- |
| `click` | `x`, `y`, `button` (`left`, `right`, `middle`), `count` (1 to 3), `modifiers` |
| `move` | `x`, `y`, for hover |
| `path` | `points`, 2 to 500 points [[x, y], …], `curve` (`line` or `spline`, default `line`), `button` (also `none`), `modifiers` |
| `scroll` | `x`, `y`, `dx`, `dy`, `unit` (`page`, a measured page, `notch`, a wheel click, or `px`, smooth scrolling). See [A scroll at the end of a batch](#a-scroll-at-the-end-of-a-batch). |
| `type` | `text`, typed literally with the active keymap. A line break presses Enter. At most 65536 bytes per batch. |
| `key` | `keys`, such as `["ctrl", "l"]` or `["ctrl+l"]`, pressed together, and `repeat` (1 to 500). A single plain key presses as a typed key does; repeated plain keys have 1 ms gaps between distinct press/release frames, and chords with modifiers take 40 ms. |
| `pause` | `ms` (0 to 5000) |

`act` also takes the click actions of Anthropic's computer use tool, which
Claude models reach for: `left_click`, `double_click`, `triple_click`,
`right_click` and `middle_click`, with `x`, `y` and `modifiers`. Each is a
`click` with its button and count. The schema leaves them out, so they add
nothing to the model's prompt.

A batch saves round trips but runs without looking, so agents batch only
targets that are visible in the shot. A menu or dialog that opens needs a new
shot before anything in it is clicked.

A `path` presses the button once, moves through its points in order and
releases once, so an app that draws along the motion draws one stroke, such
as a line along a curve in a paint tool. A drag is a path of two points,
`["path", [from, to]]`, so there is no separate drag action. `act` refuses
`drag`, and `left_click_drag` of Anthropic's computer use tool, with
`invalid_arguments`, sends nothing, and says how to send it as a path.

With `curve` `line` a path moves straight from point to point. With `spline`
it follows a smooth curve that passes through every point, a centripetal
Catmull-Rom spline that the server samples into steps of up to 10 logical
pixels. Points are where the stroke goes, not control handles. Between the
points a spline curves, so it can leave a thin target there. Points close
together keep a precise trace on target, and `line` suits a target that
bends sharply or sparse points. `line` is the default, so a polygon, such as
a lasso around a box, keeps its corners unless the agent asks for a curve.
With `button` `none` the path only moves the pointer, for example along the
diagonal that a submenu needs. A path is paced like a hand's drag: a press,
50 ms, steps 10 ms apart, 50 ms and a release. A spline's samples count as
points, and the paths of one batch move through at most 4000 points.

Paths with a button, clicks with modifiers, and chords that hold a key other
than a modifier need the window's `hold` capability. A path without a button
needs it only with modifiers. A path with a button can start an app's own
drag-and-drop, which runs privately inside the window when the window has the
`dnd` capability. See [Drag-and-drop](automation.md#drag-and-drop).

In Wayland windows, what an agent copies or selects goes to an agent
clipboard of its own, which pastes in the other windows it drives, never in
yours. X11 apps of one X server share its selections, so an X11 app the agent
drives can paste your X11 clipboard, and what it copies is available to your
other apps on that X server. See
[Clipboard and selections](automation.md#clipboard-and-selections).

## Focus lease

Without a lease, a window loses its emulated focus when a batch ends, and
popovers or dropdowns that close on focus loss may be gone by the agent's
next call. An `act` that sends input to a window with the `lease` capability
starts or renews a **focus lease** of 60 seconds (`--lease-ms`): the
window keeps its emulated pointer and keyboard focus between calls, so a
field that a click focused still takes the keys of the next call.

- An `act` without actions renews a held lease but starts none.
- `release` and `close` end the lease, whichever session holds it. It also
  ends when the window closes, when its time is up, when the session ends,
  and as soon as your keyboard focus moves to a window of the same Wayland
  connection.
- No key or button stays pressed between calls.
- The app shows itself as focused while you work elsewhere. Your real input
  is not affected.

X11 windows, and windows that share a Wayland connection with the window you
are using, cannot hold a lease. `windows` names the capabilities a window
lacks.

## Windows that the agent's input opens

A dialog, popup or file chooser that the agent's input opens opens silently
on the workspace of the window it came from, not on yours (see
[New windows](automation.md#new-windows)). `act` lists every window that
appeared during the call as a new window. It returns a shot of each one that
came from the target, up to 3 new and late windows together, unless
`capture` is `"none"` or no time is left. Take a screenshot of the others
before acting in them. After input that
can open a window, such as a click or Enter, it keeps watching for
`--new-window-grace-ms` (500 ms) after its wait.

A window that maps later, such as a file chooser of another process that
appears a second or two after the click, is a **late window**: the session's
next `act`, `screenshot` or `windows` reports it once, with a shot, if it
maps within 2 minutes after that `act`. `windows` with `wait_ms` waits for it.

When the window that got the input closes, such as a dialog after its OK
button, the result says `window closed` and returns a shot of the window it
came from, where the agent goes on.

### X11 menus

An X11 app opens its menus, dropdown lists and tooltips in windows of their
own, override-redirect windows that the window manager does not manage. `act`
lists them as new windows. Escape sent to the menu's window, or a click in
the app's window outside it, dismisses a menu, and a click on an item picks it.

`close` refuses them with `unsupported`, and its hint names these ways.
Hyprland's close dispatcher sends an X11 window the `WM_DELETE_WINDOW` request
when the window takes it. Otherwise it kills the window's X client, which
ends the whole app and loses its unsaved work. A menu that takes the request
fares no better: GTK 3 destroys the menu under its combo box, which crashes
the app once the combo box opens again. For the same reason `close` refuses an
app's own X11 window that takes no `WM_DELETE_WINDOW`, which few apps lack:
quit such an app from within it. See [X11 windows](automation.md#x11-windows).

## Failures

Each result starts with a one-line summary, followed by a hint when one is
needed. A failure reads `error <code>: <message>` and says what was sent:

| `delivery` | Meaning |
| --- | --- |
| `none` | Nothing was sent. Safe to fix and retry. |
| `complete` | Every action reached the application. Only the shot shows whether it took effect. |
| `partial` | The actions before the failed one were delivered, the failed one maybe in part. |
| `uncertain` | The connection ended before the plugin confirmed everything sent. |

Hints never suggest repeating input whose delivery is `partial` or
`uncertain` before looking at a fresh shot. A failure before any input comes
with a fresh shot when the window is known. A failed capture after delivered
input is not an error.

| Code | Meaning |
| --- | --- |
| `invalid_arguments` | An argument is wrong, named by its path, such as `actions[0].count`, or the batch does not fit in `deadline_ms`. |
| `window_gone` | The window closed, or another window has its address now. |
| `stale_shot` | The shot is unknown, of another window, shows an older frame, or the window was resized or its target moved since the shot. |
| `out_of_bounds` | A point lies outside the shot, or outside what pointer input can reach. |
| `cannot_type` | The keymap cannot produce a character. |
| `unsupported` | The window lacks a capability the batch needs, such as `hold`, or `close` would end or break the window's whole X11 app. |
| `input_paused` | The plugin paused input: the user held a button or key in a window of the same Wayland connection, or, for an X11 window, the user is in another window of its X server. |
| `deadline` | The deadline arrived before or while the input was sent, or `launch` found no window within `timeout_ms`. |
| `cancelled` | The server is shutting down, or the call was cancelled. |
| `unconfirmed`, `connect_failed`, `no_devices` | The sender could not deliver or confirm the input. |
| `plugin_missing`, `plugin_version` | The plugin is not loaded in this Hyprland, or has another interface version than the server. |
| `internal` | Anything else. |

`no_redraw` and `timeout` are not errors: they are wait outcomes, returned
with a shot and a hint.

Several servers can drive one desktop. They take turns on each window
through lock files in `$XDG_RUNTIME_DIR/hyprmasq/<instance>/`. Scripts that
run `hyprmasq-input` directly take no locks.

## Command-line options

| Option | Default | Meaning |
| --- | --- | --- |
| `--input PATH` | `hyprmasq-input` on `PATH` | The sender to run for each batch. The Nix package's `hyprmasq-mcp` wrapper puts its own `hyprmasq-input` first on `PATH`. |
| `--max-image-size N` | `1280` | Fit shots, zooms included, within N pixels on their long side. `0` keeps the monitor's scale. |
| `--lease-ms MS` | `60000` | Length of the focus lease that each `act` starts or renews, 1 to 600000. |
| `--new-window-grace-ms MS` | `500` | How long `act` keeps watching for new windows after its wait, when it delivered input, 0 to 5000. |
| `--structured-content auto\|always\|never` | `auto` | Whether results carry MCP structured content next to the text. `auto` leaves it out for clients named `claude-code`, which would show it to the model instead of the shorter text. |
| `--version` | | Print the version and exit. |

The server needs `XDG_RUNTIME_DIR` and `HYPRLAND_INSTANCE_SIGNATURE` of the
Hyprland it drives, which it inherits when the agent runs inside the
Hyprland session and the client passes them on (see
[Client notes](#client-notes) for Codex), and the plugin loaded with the same
interface version. It exits with status 2 on invalid options.

The session ends when the client closes stdin or stdout, or on SIGINT,
SIGTERM or SIGHUP. The server then cancels running calls, releases its
leases and deletes its shots. Sockets and launched windows stay open, and only
`close` closes windows.

## Client notes

**Claude Code** shows a result's structured content to the model in place of
its text, so with the default `--structured-content auto` the server sends it
none.

**Codex** starts MCP servers without the session's environment. Pass on
the Hyprland with `env_vars = ["XDG_RUNTIME_DIR",
"HYPRLAND_INSTANCE_SIGNATURE"]` in the server's table, or the server exits at
start and Codex lists no hyprmasq tools. Codex asks for approval before
calling a tool that is not marked read-only, and no hyprmasq tool is.
`codex exec` with `approval_policy = "never"` cannot ask and fails those
calls. Set
`default_tools_approval_mode = "approve"` for the server, or approve single
tools. Codex waits 60 seconds for a tool call by default. A `launch` can take
its `timeout_ms` plus about 6 seconds, so raise `tool_timeout_sec` for long
launches. See [Installation](installation.md#mcp-server).

**pi** needs `"exposure": "direct"` so that the model sees the tools
themselves. pi then shows the model none of the server's instructions, which
loses nothing: the tool descriptions and the [first view](#the-first-view-of-a-window)
carry every rule.

## Limits

- Pointer input cannot reach the parts of a popup above or left of its
  window. `act` refuses such points with `out_of_bounds`.
- Windows without the `lease` capability lose their emulated focus when the
  batch ends, and what the app closes on focus loss may be gone in the act's
  own shot.
- A shortcut sent to a window that shares its Wayland connection with the
  window you are using may do nothing if the app takes more than a quarter of
  a second to run it.
- A window moved during a batch without changing size is not noticed.
- When another X11 window of the same X server covers the point of a scroll
  in an X11 window, as the X server stacks them, GTK 3 and Chromium count
  only the first notch of each scroll.
- The comparison misses changes below its thresholds, such as a faint hover
  highlight, and a window that keeps animating tends to get full shots.
- A hidden window that was driven and later wants to draw again may show an
  older frame without a stale mark. An `act` without actions lets it draw.
- `close` cannot tell whether a window of xwayland-satellite takes
  `WM_DELETE_WINDOW`, since to Hyprland it is a Wayland window. Satellite
  kills the X client of one that does not, which ends its app.
- An `XDG_RUNTIME_DIR` that contains whitespace is not supported.
