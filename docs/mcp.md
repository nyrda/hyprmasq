# MCP server

`hyprmasq-mcp` gives coding agents computer use on Hyprland: Claude Code,
Codex, pi and any other client of the [Model Context Protocol](https://modelcontextprotocol.io).
An agent can list and launch windows, take screenshots, and click and type in
any window, also on a hidden workspace, while you keep working in yours.

The server speaks MCP over stdio, so each agent session starts its own.
[Installation](installation.md#mcp-server) covers registering it. The tool
descriptions carry every rule an agent needs, such as to drive only windows
the user named or the agent launched and to ask before anything irreversible,
so agents need no further instructions.

## Tools

| Tool | Main arguments | What it does |
| --- | --- | --- |
| `windows` | `match`, `wait_ms` (0 to 10000) | List the windows, or those whose class or title contains `match`: address, class, title, workspace, visible or hidden, size, and the capabilities each lacks. When it lists exactly one window, it also returns a shot of it. With `wait_ms`, it first waits for a window that is not there yet. |
| `launch` | `command` (array), `workspace` (default `hyprmasq`), `timeout_ms` (1000 to 300000, default 30000) | Start an application on a hidden workspace without a shell, wait for its first window to appear and settle, and return its address with a shot. |
| `screenshot` | `window`, `region` | Take a shot of the window now. With `region`, zoom into part of an earlier shot. |
| `act` | `window`, `shot`, `actions`, `wait`, `capture`, `deadline_ms` | Send input, wait until the window has drawn the result, and return what changed. See [act](#act). |
| `release` | `window` | End the window's focus lease. The window stays open. |
| `close` | `window` | Ask the window to close, as Hyprland's close dispatcher does, and report within 2 seconds whether it closed and which windows appeared, such as a confirmation dialog. |

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
  targets, and `act` accepts its pixels too.
- Before each capture, the server waits briefly, at most 1.5 s, for a window
  that has not yet redrawn at a new size, or a hidden window that has not
  drawn yet. A shot taken anyway is marked stale or "may be stale" in the
  summary.
- The server keeps the 64 most recently used shots of each window, as files
  under `$XDG_RUNTIME_DIR/hyprmasq/<instance>/mcp/<pid>/`, and deletes them
  when the session ends.

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

One `act` call:

1. **Checks** the whole batch before sending anything: arguments,
   coordinates against the shot, the window's capabilities, key names and
   whether the keymap can type every character.
2. **Refuses stale targets.** If the window was resized since the shot, or
   the window no longer shows what the shot showed around a click or drag
   point, the call fails with `stale_shot`, sends nothing, and returns a
   fresh shot to read coordinates from.
3. **Sends the input** through one `hyprmasq-input`, paced like a person's.
   After a click or drag that more actions follow, it waits for the window to
   answer before sending the rest, and keys after a click start at least
   300 ms after it.
4. **Waits** until the window has drawn the result: first for a redraw
   (`expect_ms`), then until it has been quiet for `quiet_ms`.
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

| Argument | Default | Meaning |
| --- | --- | --- |
| `window` | required | Window address from `windows` or `launch`. |
| `shot` | required for coordinates | The shot the coordinates were read from. |
| `actions` | `[]` | Up to 1000 actions, run in order. An empty list waits for a change since the shot, then captures. |
| `wait` | | `expect_change` (true), `expect_ms` (1000, at most `deadline_ms` − 1000), `quiet_ms` (200, 1 to 10000). |
| `capture` | `"changes"` | `"changes"`, `"window"` for a full shot, `"none"`, or a region `{shot, x, y, width, height}`. |
| `deadline_ms` | 15000 | 1000 to 30000. Covers the whole call, and the last second is reserved for the capture. |

| Action | Fields |
| --- | --- |
| `click` | `x`, `y`, `button` (`left`, `right`, `middle`), `count` (1 to 3), `modifiers` |
| `move` | `x`, `y`, for hover |
| `drag` | `from` [x, y], `to` [x, y], `button`, `modifiers` |
| `scroll` | `x`, `y`, `dx`, `dy`, `unit` (`notch` or `px`) |
| `type` | `text`, typed literally with the active keymap. A line break presses Enter. At most 65536 bytes per batch. |
| `key` | `keys`, such as `["ctrl", "l"]` or `["ctrl+l"]`, pressed together, and `repeat` (1 to 100) |
| `pause` | `ms` (0 to 5000) |

`act` also takes the click actions of Anthropic's computer use tool, which
Claude models reach for: `left_click`, `double_click`, `triple_click`,
`right_click` and `middle_click`, with `x`, `y` and `modifiers`. Each is a
`click` with its button and count. The schema leaves them out, so they add
nothing to the model's prompt.

A batch saves round trips but runs without looking, so agents batch only
targets that are visible in the shot. A menu or dialog that opens needs a new
shot before anything in it is clicked.

Drags, clicks with modifiers, and chords that hold a key other than a
modifier need the window's `hold` capability. A drag can start an app's own
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
| `unsupported` | The window lacks a capability the batch needs, such as `hold`. |
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
loses nothing: the tool descriptions carry every rule.

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
- The comparison misses changes below its thresholds, such as a faint hover
  highlight, and a window that keeps animating tends to get full shots.
- A hidden window that was driven and later wants to draw again may show an
  older frame without a stale mark. An `act` without actions lets it draw.
- An `XDG_RUNTIME_DIR` that contains whitespace is not supported.
