# Automation

This page is the reference for driving windows from shell scripts and tests:
the `hyprctl hyprmasq` commands of the plugin, the `hyprmasq-input` sender and
the `hyprmasq-launch` launcher. Coding agents use the same features through
the MCP server, described in [MCP server](mcp.md).

## Concepts

- **Window address.** Every command names a window by the `address` field of
  `hyprctl -j clients`, such as `0x55d1c0a3e4b0`. The window must be mapped.
- **Window-local coordinates.** Points and regions are in logical pixels
  relative to the window's top-left corner, the same for screenshots and for
  pointer input. A screenshot pixel at PX,PY is at
  `region.x + PX / scale`, `region.y + PY / scale`, using the `region` and
  `scale` of the screenshot's JSON reply.
- **Input socket.** `eis open` creates a [libei](https://gitlab.freedesktop.org/libinput/libei)
  socket bound to one window. Input sent to it goes to that window only.
  The socket is removed when you close it, when the window closes, or when the
  plugin unloads.
- **Commit counter.** While a window has an open socket, the plugin counts the
  frames the window draws, including its popups and subsurfaces. Screenshots
  and `state` report the counter, so a later `wait … since` can wait for a
  change since that moment.

```sh
hyprctl -j clients | jq -r '.[] | "\(.address)  \(.workspace.name)  \(.class)  \(.title)"'
```

A complete loop:

```sh
addr=$(hyprmasq-launch -- foot)                         # start foot on a hidden workspace
socket=$(hyprctl hyprmasq eis open "$addr")             # create its input socket
shot=$(hyprctl -j hyprmasq screenshot "$addr" /tmp/before.png)
hyprmasq-input --socket "$socket" type 'ls -la<enter>'
hyprctl hyprmasq wait "$addr" since "$(jq .commits <<<"$shot")" expect 1000
hyprctl hyprmasq screenshot "$addr" /tmp/after.png
hyprctl hyprmasq eis close "$addr"
```

## Commands

| Command | Reply | Description |
| --- | --- | --- |
| `screenshot <addr> <path> [X Y W H] [max PIXELS] [popups] [raw] [detail <path>]` | `ok` | Render the window, or a region of it, to a PNG at an absolute path. See [Screenshots](#screenshots). |
| `wait <addr> [QUIET-MS [TIMEOUT-MS]]` | `settled` or `timeout` | Wait until the window has drawn nothing for QUIET-MS. See [Waiting](#waiting). |
| `wait <addr> since COMMITS [expect MS] [quiet MS] [deadline MS] [id WORD] [ignore animations] [drags ID]` | how it ended | Wait for a change since a commit counter value, then for quiet. Needs a socket. |
| `cancel <word>` | `cancelled` or `no pending wait` | End the pending `wait … since` with that `id`. |
| `state <addr>` | `key: value` lines | Size, scale, socket, counter, lease, capabilities and more. See [Window state](#window-state). |
| `lease <addr> <ms> <owner>` | `held N ms by OWNER` | Start or renew the window's focus lease, 1 to 600000 ms. Needs a socket. See [Focus lease](#focus-lease). |
| `lease <addr> release [owner]` | `released` or `no lease released` | End the lease. With `owner`, only if that owner holds it. |
| `eis open <addr> [force]` | socket path | Create the window's input socket, or return the path of the open one. `force` is for [X11 windows](#x11-windows), and each `open` sets or clears it. |
| `eis close <addr>` | `ok` | Remove the socket and disconnect its sender. |
| `eis list` | `address path [force]` lines | List the open sockets, or `no open sockets`. |
| `launch <workspace-hex> <token> <command-line-hex>` | `launched pid N` | Start a shell command line whose windows open silently on a workspace: a name, or the number of a numbered workspace. Both are written in hex. Use [`hyprmasq-launch`](#hyprmasq-launch) instead. |
| `launched` | `address token` lines | The mapped windows of launches, or `no launched windows`. |
| `clipboard set <text-hex>` | `text N` | Make the text, written in hex, the agent's clipboard, which the plugin holds and pastes. `N` is its serial. See [Clipboard and selections](#clipboard-and-selections). |
| `clipboard` | `text N: A pastes asked for it, E have ended, C with all of it` | How far the pastes of the last text that `clipboard set` made have read it, while it is still the agent's clipboard. |
| `hooks` | `group mode, state` lines | The plugin's groups of function hooks, whether each is installed, and why its last install was refused. See [Other plugins](#other-plugins). |
| `version` | `hyprmasq 0.1.0 interface 5` | Plugin version and command interface version. |
| `help` | usage | The command summary. |

All commands are `hyprctl hyprmasq …`. Keywords such as `since`, `max` and
`release` are plain words, because `hyprctl` treats every argument that
starts with a dash, apart from a negative number, as its own flag. Arguments are split at whitespace, so
paths and owners cannot contain any.

A failed command prints `error: <message>` and `hyprctl` exits with status 7.
`hyprctl` exits with status 6 when Hyprland does not answer within 5 seconds.

### JSON output

With `hyprctl -j`, every command but `help` replies with one JSON object, and
errors are JSON too, with `hyprctl` exiting 0, so check `ok`:

| Command | JSON reply |
| --- | --- |
| `screenshot` | `{"ok":true,"path":"/tmp/w.png","width":1280,"height":960,"scale":2,"region":{"x":0,"y":0,"width":640,"height":480},"commits":123,"socketId":"9f3a61c2-3","resizing":false,"starved":false,"outdated":false,"detail":null}` |
| `wait` | `{"ok":true,"settled":true,"waited":230,"frames":0,"framesSinceInput":3}` |
| `wait … since` | `{"ok":true,"ended":"quiet","commits":131,"socketId":"9f3a61c2-3","redrawObserved":true,"commitsSince":8,"quietMs":200,"elapsedMs":412,"resizing":false,"animated":[]}` |
| `cancel` | `{"ok":true,"cancelled":true}` |
| `lease` | `{"ok":true,"held":true,"remainingMs":60000,"owner":"my-script"}` |
| `lease … release` | `{"ok":true,"released":true}` |
| `eis open` | `{"ok":true,"path":"/run/user/1000/hyprmasq/<instance>/<addr>.eis","socketId":"9f3a61c2-3"}` |
| `eis close` | `{"ok":true}` |
| `eis list` | `{"ok":true,"sockets":[{"address":"0x…","path":"…","socketId":"9f3a61c2-3","force":false}]}` |
| `launch` | `{"ok":true,"pid":4242}` |
| `launched` | `{"ok":true,"windows":[{"address":"0x…","stableId":"1a","token":"9c1f0e4ab27d3365"}]}` |
| `clipboard set` | `{"ok":true,"serial":3}` |
| `clipboard` | `{"ok":true,"serial":3,"held":true,"asked":1,"ended":1,"complete":1}` |
| `version` | `{"ok":true,"version":"0.1.0","interface":5}` |
| `hooks` | `{"ok":true,"groups":[{"name":"input","onDemand":true,"installed":false,"claims":0,"installs":3,"functions":["CSeatManager::setKeyboardFocus(…)"],"refused":null}]}` |
| an error | `{"ok":false,"error":"no mapped window 0x1234","code":"window_gone"}` |

| Error code | Meaning |
| --- | --- |
| `invalid_arguments` | Malformed command or arguments. |
| `window_gone` | No mapped window at that address, or it closed during a wait. |
| `no_socket` | The command needs an open socket (`eis open`). |
| `unsupported` | Not possible for this window, such as a lease on an X11 window, or a `wait` that did not come through Hyprland's request socket. |
| `internal` | Rendering, file or other failures inside the plugin. |
| `unloaded` | The plugin was unloaded while the request was pending. |
| `hook_held` | Another plugin hooks a function that the command needs, which the message names, as for a `lease` or a `launch`. Nothing changed. See [Other plugins](#other-plugins). |

`interface` changes with every incompatible change to the commands. New
fields in replies are not such a change, so ignore fields you do not know.
`hyprmasq-mcp` refuses to run against another interface than its own.

## Screenshots

A screenshot is rendered offscreen at the monitor's scale, so a 640×480
window on a 2× monitor gives a 1280×960 PNG. It shows the window's own
content, without borders, shadows or cursors, also when its workspace is
hidden. Wayland popups that lie over the window are included. While Hyprland
animates a window, the screenshot shows the window as it will be once the
animation has ended.

| Option | Effect |
| --- | --- |
| `X Y W H` | Capture only this region, in whole window-local logical pixels. A region that reaches past the window is cut to it, and one that starts outside it is an error. With `popups`, the region is cut to the box of the window and its popups instead, so it can start at negative coordinates. It is an error only when it lies entirely outside that box. |
| `max PIXELS` | Shrink the image on the GPU to fit within PIXELS on its long side. It is never enlarged. |
| `popups` | Grow the captured box to include popups that reach past the window's edges. `region.x` and `region.y` can then be negative. Pixels outside the window and its popups are transparent. |
| `raw` | Write straight-alpha RGBA pixels, 4 bytes each, top row first, without a header, instead of a PNG. |
| `detail <path>` | With `max`, also write a raw image of the same frame at one pixel per logical pixel, or at the monitor's pixels where they are fewer. The reply's `detail` describes it, or is `null` when the main image is not smaller. |

Reply fields: `scale` is image pixels per logical pixel. `region` is the
captured part of the window in logical pixels, which at fractional scales can
be slightly larger than requested. `commits` and `socketId` are the
[commit counter](#waiting) at the moment of rendering, or `null` without a
socket. `resizing` is true when the window had not yet redrawn at a new size,
`starved` when a hidden window was waiting for a frame callback it never got,
and `outdated` when one had drawn a change on its own since the plugin last
sent it frame callbacks and was waiting for another. In each case the image
may show an older frame: wait with `wait … since` and take it again.

Hyprland's main thread renders the window and has the GPU copy its pixels
out, without waiting for the GPU. Once the GPU is done, the PNG is encoded
and written on a background thread, and the reply comes once the file is
complete. A request that another plugin passes on through
`HyprlandAPI::invokeHyprctlCommand` needs its reply at once, so Hyprland
waits for the GPU and the file, and the screenshot writes only regular
files.

## Waiting

After input, an application takes a moment to draw the result. There are two
waits.

**`wait <addr> [QUIET-MS [TIMEOUT-MS]]`** answers `settled` once the window
has drawn nothing and received no input through its socket for QUIET-MS
(default 200), or `timeout` after TIMEOUT-MS (default 3000, at most 4500).
It always takes at least QUIET-MS. It sends no frame callbacks of its own, so
a hidden window that draws later than a second after input may not draw
during it. The JSON reply has `waited`, the `frames` drawn during the wait,
and `framesSinceInput`, the frames since the last input through the socket,
or `null` without one.

**`wait <addr> since COMMITS …`** waits for a change after a known counter
value, such as the `commits` of a screenshot. It needs an open socket. Each
option may come once, in any order, and all times are 1 to 60000 ms.

| Option | Default | Effect |
| --- | --- | --- |
| `expect MS` | none | First wait up to MS for a frame after COMMITS. It ends at once if the counter is already past COMMITS. Without a frame in time, the wait ends with `no_redraw`. Without `expect`, there is no such phase. |
| `quiet MS` | 200 | Then wait until there has been no frame and no input for MS, and the window is not resizing. The wait ends with `quiet`. Animations do not keep this phase from ending. A small box that has redrawn again and again for a second since the input, such as a spinner, is one, and so is a box at most 8 logical pixels wide or high from its third frame since the input, such as an overlay scrollbar that fades in or a caret that blinks. A frame is part of an animation only when each separate part that it redraws is. Their boxes are reported in `animated`. |
| `deadline MS` | 4500 | End the wait with `timeout` in either phase. |
| `id WORD` | none | Name the wait so that `cancel WORD` ends it with `cancelled`. |
| `ignore animations` | off | Frames that belong to an animation do not end the `expect` phase. |
| `drags ID` | none | Neither phase ends while a drag-and-drop newer than drag ID is going on, and its end counts as a change. See [Drag-and-drop](#drag-and-drop). |

The wait also ends with `window_closed` when the window closes. While it runs,
a window that Hyprland does not render, such as one on a hidden workspace or on
a monitor that DPMS turned off, gets a frame callback every 16 ms and is not told that it is suspended, so it
keeps drawing. Compare counter values only when their `socketId` is the same:
the counter starts again at 0 when a socket is reopened, and a COMMITS above
the counter is an error.

The default deadline of 4500 ms keeps the reply within `hyprctl`'s 5-second
limit. A program that writes the request to Hyprland's socket,
`$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock`, itself can
wait up to 60 seconds: send `j/hyprmasq wait 0x… since 120 deadline 30000` in
one write and read until Hyprland closes the connection. Waits never block
Hyprland, and both work only through Hyprland's request socket, which
`hyprctl` uses.

## Focus lease

Without a lease, the window's emulated pointer and keyboard focus ends when
the sender exits, or about a second after the last input while a sender stays
connected. Popovers and dropdowns that close on focus loss can then be gone
before your next input. A **focus lease** keeps the emulated focus on the
window between inputs:

```sh
hyprctl hyprmasq lease "$addr" 60000 my-script        # start or renew for 60 s
hyprmasq-input --socket "$socket" move 40 20 click    # open a menu
# look at a screenshot, decide, then:
hyprmasq-input --socket "$socket" move 60 90 click    # pick an entry
hyprctl hyprmasq lease "$addr" release my-script
```

- A lease keeps the focus that input gives. It never gives focus by itself, so
  take it before the input. Renewing under another owner takes it over.
- No key or button stays pressed: when a sender stops, the plugin releases
  what it held and keeps the focus.
- It ends when it expires, on release, when the socket or window closes, when
  the plugin unloads, when your keyboard focus moves to a window of the same
  Wayland connection, or when input goes to another window of that
  connection through its own socket.
- While your pointer is on a window of the same connection, the lease keeps
  only the keyboard focus.
- It is refused with `unsupported` for X11 windows, and while the window
  shares its Wayland connection with the window that has your keyboard focus.
  It is refused with `hook_held` while another plugin hooks a function that
  input to windows needs, see [Other plugins](#other-plugins).

## Window state

`hyprctl -j hyprmasq state <addr>` reports:

| Field | Meaning |
| --- | --- |
| `address`, `stableId`, `xwayland` | The window. `stableId` is that of `hyprctl -j clients` and tells a new window at a reused address apart. |
| `x11` | For a window of Hyprland's XWayland, `overrideRedirect` and `deleteWindow`, see below. `null` for other windows, also those of xwayland-satellite. |
| `size`, `scale` | Logical size, and the monitor's scale. |
| `resizing` | The window has not yet drawn a frame at the size Hyprland gave it. Screenshots show an older frame, and pointer input may not land where they show things. |
| `starved` | The window is hidden, waits for a frame callback, and has got none from hyprmasq. It may show an older frame. Input and `wait … since` send frame callbacks. |
| `outdated` | The window is hidden, has an open socket, has drawn a change on its own since hyprmasq last sent it frame callbacks, such as output that a terminal printed, and waits for another. It may have more to draw. Frames of an animation do not count. |
| `socket`, `socketId`, `commits` | The open socket, its id and its commit counter, or `null`. |
| `interrupted` | How often your own input to the same Wayland connection took the keyboard focus back within a quarter of a second of an emulated shortcut. See [Input delivery](#input-delivery). |
| `lastPause` | Why the plugin last paused the socket's input: `user_button`, `user_key`, `x11_focus` or `x11_unresponsive`, or `null`. |
| `drag` | The drag-and-drop operations of the window. See [Drag-and-drop](#drag-and-drop). |
| `lease` | `held`, `remainingMs` and `owner`, or `null`. |
| `capabilities` | What input can do in the window right now, see below. |
| `source` | The window this one was opened from, see [New windows](#new-windows), or `null`. |
| `popups` | The window's mapped popups, parents before their children: `x`, `y`, `width` and `height` in window-local logical pixels, where pointer input reaches them, and `grab`, true for a popup that asked to take the pointer and keys while it is open, as menus and dropdown lists do and tooltips do not. Empty for X11 windows, whose menus are windows of their own. |

| Capability | True when |
| --- | --- |
| `lease` | The window can hold a focus lease: a Wayland window that does not share its connection with the window that has your keyboard focus. |
| `hold` | Buttons and keys can stay pressed across input frames, which paths with a button, clicks with modifiers and chords like `a+b` need. False while the window shares its Wayland connection with your keyboard or pointer focus. |
| `dnd` | The window is the active window, whose drag-and-drop Hyprland runs with your pointer. |
| `privateDnd` | A drag-and-drop that emulated input starts runs privately inside the window. See [Drag-and-drop](#drag-and-drop). |
| `keyboard` | `x11-focus` for X11 windows not opened with `force`, whose keys depend on the X focus. Otherwise `yes`. |

`x11` tells what Hyprland's close dispatcher does with the window.
`overrideRedirect` is true for a window that the window manager does not
manage, such as a menu, list or tooltip. `deleteWindow` is true when the
window's `WM_PROTOCOLS` lists `WM_DELETE_WINDOW`, as Hyprland's window manager
last read it. The dispatcher asks such a window to close, and kills the X
client of any other window, which ends its app with all of its windows.

## hyprmasq-input

`hyprmasq-input` connects to a window's socket, checks every action, sends
them in order with person-like pacing, and exits once the plugin has
processed them.

```text
hyprmasq-input --socket PATH [OPTIONS] ACTION...
hyprmasq-input --help | --version
```

| Option | Meaning |
| --- | --- |
| `--socket PATH` | The socket that `eis open` printed. Required. |
| `--no-pace` | Leave out the delays that pace input like a person's. `sleep`, `<wait:MS>`, `<settle>` and `sync` still wait. |
| `--json` | Print the outcome as one JSON object on stdout. |
| `--budget MS` | Refuse the actions, before sending anything, if at their pace they cannot end within MS (0 to 3600000) of the start. |
| `--locale NAME` | The target application's locale, such as `pt_BR.UTF-8`, whose Compose table types characters that have no key of their own. Default: the sender's own locale. |
| `-h`, `--help` | Show help and exit. |
| `--version` | Show the version and exit. |

Options come before the first action.

| Action | Meaning |
| --- | --- |
| `move X Y` | Move the pointer to X,Y. Fractions are allowed. |
| `click [BUTTON [COUNT [MODS]]]` | Click BUTTON (default `left`) COUNT times (1 to 3, default 1), holding MODS such as `ctrl+shift`, or `-` for none. |
| `down BUTTON`, `up BUTTON` | Press or release a button. |
| `path N X1 Y1 … XN YN [BUTTON [MODS]]` | Press at the first of N points (2 to 4000), move through the others in steps, release at the last. A drag is `path 2 X1 Y1 X2 Y2`. BUTTON `none` moves without pressing. |
| `scroll DX DY` | Smooth scroll by DX and DY, as a touchpad does. Apps scale it as they like, and some X11 apps ignore it, so prefer `wheel`. Positive DY scrolls down. |
| `wheel DX DY` | Scroll whole wheel notches, -100 to 100 per axis. |
| `key CODE` | Press and release a Linux evdev key code, such as `28` for Enter. |
| `keys CHORD [REPEAT]` | Press the keys of CHORD together, such as `ctrl+shift+t` or `enter`, 1 to 500 times. |
| `type TEXT` | Type text, with [key tags](#key-tags) for keys and waits. |
| `text TEXT` | Type TEXT literally, without tags or escapes. |
| `sleep MS` | Wait 0 to 3600000 ms. |
| `sync MIN MAX` | Once the plugin has processed the N actions before it, print `{"sync":N}` and wait for a line with N on stdin, at least MIN and at most MAX ms (0 ≤ MIN ≤ MAX ≤ 3600000). Lets the caller wait for the window between two actions without ending the run. |

Buttons are `left`, `right`, `middle` or an evdev code such as `0x113`. Key
names in CHORD and MODS are joined by `+` and ignore case: the modifiers
`ctrl` (`control`), `shift`, `alt` and `super` (`meta`, `win`, `cmd`); `space`,
`enter`, `tab`, `esc`, `backspace`, `delete` (`del`), the arrows, `home`,
`end`, `pageup` and `pagedown`; a single character; or any XKB keysym name
such as `F5` or `XF86AudioMute`. The plus key is `plus`. A chord has at most
9 keys.

```sh
hyprmasq-input --socket "$socket" move 120 40 click text 'hello world' keys enter
hyprmasq-input --socket "$socket" move 200 80 click left 2 keys ctrl+c
hyprmasq-input --socket "$socket" path 2 50 50 300 50 wheel 0 3
hyprmasq-input --socket "$socket" path 4 20 200 120 60 220 60 320 200 left
```

**Checks.** Every action is checked before anything is sent: the syntax
before connecting, then every point against the window's pointer regions and
every key and character against the keymap. Any problem fails the whole run
with exit status 2 and nothing sent. Of a path, only the first and last
points are checked. Its steps and points between them that leave the regions
are skipped, as libei would drop them.

**Text.** `type` and `text` use the keymap of Hyprland's active keyboard,
which the plugin sends to the sender. Characters without a key of their own,
such as `^` on a German layout, are typed as a Compose sequence, which the
application turns into the character with the Compose table of its locale.
Pass that locale with `--locale` when it differs from yours.

**Pacing.** Applications can miss input that comes faster than a hand could
send it, so `hyprmasq-input` paces its input unless `--no-pace` is given:

| Input | Pacing |
| --- | --- |
| Between actions | 50 ms |
| Press after motion | at least 30 ms |
| Button held | 50 ms |
| Chords with modifiers | the key 10 ms after its modifiers, held 15 ms, 15 ms apart |
| Typed keys, and a single press with `keys` | held 5 ms, 10 ms apart |
| Repeated plain keys | distinct press/release frames with 1 ms gaps |
| Wheel notches | 20 ms apart, then 20 ms after the last notch |
| Smooth scrolls | 20 ms after each |
| Paths | steps of at most 10 logical pixels, 10 ms apart. A path takes 21 to 2000 steps by its length, so they are shorter under 210 pixels and longer over 20000, and at least one per segment. |

**Releases.** `hyprmasq-input` leaves nothing pressed. After a failure, and on
SIGINT or SIGTERM, it releases every button and key it holds and exits within
about a second. If a sender exits without releasing, the plugin releases for
it.

### Key tags

`type` reads tags in its text. Tags ignore case.

| Tag | Result |
| --- | --- |
| `<enter>`, `<tab>`, `<space>`, `<esc>`, `<backspace>`, `<delete>` | Press that key. |
| `<up>`, `<down>`, `<left>`, `<right>`, `<home>`, `<end>`, `<pageup>`, `<pagedown>` | Press that key. |
| `<F5>`, `<XF86AudioMute>`, … | Press the key of an XKB keysym that does not type a character. |
| `<KEY:COUNT>` | Press a named key or keysym 1 to 500 times, such as `<down:5>`. |
| `<shortcut:MODS+KEY>` | Hold the modifiers around KEY, such as `<shortcut:ctrl+l>`. |
| `<wait:MS>` | Wait 0 to 3600000 ms. |
| `<settle>` | Wait 300 ms. |

`\<` types a literal `<`. Anything else in angle brackets that is not a tag,
such as `<div>`, is typed as it is. An invalid tag, such as `<tab:0>`, fails
the run before anything is sent.

```sh
hyprmasq-input --socket "$socket" type '<shortcut:ctrl+l>https://example.org<enter>'
```

### Exit status and JSON

| Status | Meaning |
| --- | --- |
| 0 | The plugin confirmed all input, or help or version was printed. |
| 1 | Runtime failure after the start, such as no connection, no devices, paused input, a closed connection, memory exhaustion or a signal. Some input may have been sent. |
| 2 | Invalid arguments, untypable input or an exceeded `--budget`. Nothing was sent. |
| 3 | The plugin refused input to the window, as while another plugin hooks a function that the input needs. Nothing was sent. |

Messages go to stderr as `hyprmasq-input: <message>`. With `--json`, the last
line on stdout is one object:

```json
{"ok":false,"done":1,"total":2,"failedIndex":1,"delivery":"partial","progress":{"keys":39},"compose":null,"error":{"code":"input_paused","message":"the server paused input, so some events were dropped"}}
```

| Field | Meaning |
| --- | --- |
| `done`, `total` | Actions the plugin confirmed, and the number of actions. |
| `failedIndex` | 0-based index of the failed action, or `null`. |
| `delivery` | `none`: nothing arrived. `complete`: everything was processed. `partial`: actions before `failedIndex` were processed, the failed one maybe in part, later ones not sent. `uncertain`: the connection ended before the plugin took everything sent. |
| `progress` | For a failed `keys`, `type` or `text`, `{"keys":N}`: the key presses the plugin processed, or with delivery `uncertain`, the key presses sent, some of which may not have arrived. |
| `paceMs` | With `--budget`: how long the actions take at least at their pace. |
| `compose` | `{"locale":L,"requested":R}` when a character was typed through Compose, otherwise `null`. |
| `error` | `{"code","message"}` or `null`. |

| `error.code` | Meaning |
| --- | --- |
| `invalid_arguments` | An option, action, point or key name is invalid. |
| `cannot_type` | The keymap cannot produce a character, key or modifier. |
| `too_long` | The actions take longer than `--budget`. |
| `connect_failed` | The socket cannot be reached. |
| `no_devices` | The plugin offered no pointer and keyboard, removed one, or sent no usable keymap. |
| `input_paused` | The plugin paused input: you held a button or key in a window of the same Wayland connection for over a second, or for an X11 window another window of its X server has the focus. `state`'s `lastPause` says which. |
| `unconfirmed` | The plugin closed the connection, replaced the pointer while events were unconfirmed, or stopped answering. |
| `hook_held` | The plugin refused input to the window: another plugin hooks a function that the input needs, which the message names. Nothing was sent. See [Other plugins](#other-plugins). |
| `cancelled` | SIGINT or SIGTERM stopped the run. |
| `too_many_held`, `out_of_memory` | Too many keys or buttons held at once, or memory exhaustion. |

"Processed" means the plugin delivered the events to the window, not that the
application handled them. Input can close its own window, such as Ctrl+D in a
shell. When the action that closed it was the last one and the plugin
confirmed it, the run still succeeds.

### Other libei senders

The socket is a standard EIS endpoint: any libei sender can connect with
`ei_setup_backend_socket()`. The plugin offers one seat with an absolute
pointer, whose first region is the window at its logical size from 0,0, and a
keyboard with the keymap of Hyprland's active keyboard. One sender is
connected at a time, and a new one replaces the previous one. The pointer device
is replaced when the window moves or resizes. Reconnect after a keymap
change. When the plugin refuses input to the window, it offers a seat without
capabilities instead, named `hyprmasq refused <code>: <message>`, such as
`hyprmasq refused hook_held: another plugin hooks …`, and closes the
connection.

## hyprmasq-launch

`hyprmasq-launch` starts an application on a hidden workspace and prints the
address of its first window. The visible workspace and your focus stay as
they are.

```text
hyprmasq-launch [OPTIONS] [--] COMMAND [ARG...]
hyprmasq-launch --help | --version
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--workspace NAME` | `hyprmasq` | Workspace for the windows: a name of letters, digits, `_`, `.` and `-`, or the number of a numbered workspace. |
| `--timeout SECONDS` | `30` | How long to wait for a window, a positive whole number up to 2147483647. |
| `--` | | End of options. The rest is the command. |
| `-h`, `--help` | | Show help and exit. |
| `--version` | | Show the version and exit. |

| Status | Meaning |
| --- | --- |
| 0 | A window address, help or version was printed on stdout. |
| 1 | The launch failed, the compositor's reply was invalid, or no window appeared in time. |
| 2 | Invalid arguments. |

```sh
addr=$(hyprmasq-launch -- foot -T scratch)
addr=$(hyprmasq-launch --workspace tests --timeout 60 -- firefox --new-instance -P test)
```

The plugin runs the command through Hyprland under `hyprmasq-reaper`, and a
window belongs to the launch when its process is the launched process or one
of its descendants. Windows created within a minute of the launch open
silently on the workspace. Applications that hand the request to an instance
that is already running, such as a `footclient` or an open browser, or that
D-Bus starts, open the window from a process outside the launch, and the
launch times out. Start them as a separate instance. `hyprmasq-launch` needs
`hyprctl` and `jq` on `PATH`, and the Nix package brings `jq`.

The plugin finds `hyprmasq-reaper` next to `hyprmasq.so`, in `../libexec`
relative to it, or on `PATH`. Without it, the windows of an application whose
launcher forks and exits open on your workspace, and Hyprland's log has a
warning.

## Input delivery

The plugin sends events straight to the target window's Wayland objects. The
real cursor, the focused window and the active workspace do not change. A
click brings the window keyboard focus with it, as Hyprland's click-to-focus
does.

**Hidden windows.** Hyprland sends no frame callbacks to windows it does not
render, and many applications stop drawing without them. Hyprland renders
nothing on a monitor that DPMS turned off, or while the session is on another
VT, so the plugin treats the windows of visible workspaces as hidden then. It
sends a hidden target frame callbacks every 16 ms with its input, for about a
second after it while a sender is connected, and during a `wait … since`. A
target that draws a change on its own after that, such as output that a
terminal prints, then waits for the next, and `state` and screenshots say
`outdated`. Hyprland also tells the windows of hidden workspaces that they are
suspended, and some applications, such as Qt ones, then draw nothing at all,
neither input nor a new size. For as long as the target gets frame callbacks, the plugin keeps
Hyprland from telling it, and then tells it Hyprland's state again. A sender
that stays connected with its devices keeps the target awake for as long as
it stays. A woken application may redraw once, also when the input changed
nothing. X11 windows have no such state, and neither have the windows of
clients that bind `xdg_wm_base` below version 6.

**Popups.** Pointer input over an open popup of the target reaches the popup.
A popup that asks to take the pointer and keys, such as a menu or a dropdown's
list, closes when a click comes outside it, and many apps, such as Qt ones,
give that click to nothing else, as they do with your own click. Input that
closes a popup can also open another one: a Qt dropdown with the platform
theme of GTK closes its list on Enter and opens it again on the press of the
same key. `popups` in the [window state](#window-state) tells what is open.
`hyprmasq-input` sends what it is given, and `hyprmasq-mcp` holds back such a
click, see [act](mcp.md#a-press-outside-an-open-popup).

**Idle.** Hyprland restarts the idle timers of idle clients such as hypridle
whenever a window maps, unmaps or takes the focus, which those clients take for
activity: hypridle runs its `on-resume` commands, such as turning the monitors
back on. The plugin keeps Hyprland from doing so unless an idle inhibitor
comes or goes, so launching and closing windows on hidden workspaces leaves
monitors that DPMS turned off dark. The plugin's input never counts as
activity, and never turns monitors on through `misc:key_press_enables_dpms`
or `misc:mouse_move_enables_dpms`: it reaches the window without passing
through Hyprland's input devices.

**Shared connections.** Two windows of one program, such as two windows of one
browser, can share a Wayland connection, and a connection has one keyboard and
pointer focus. When the target shares a connection with your window, the
plugin lends the target the connection's focus for each input frame and gives
it back before your next input. Your own keys and clicks always reach your
window. A connection also has one set of modifiers, which apps apply to all of
its pointer input. The target's clicks, scrolls and drags come with the
sender's modifiers, and the connection gets yours back right after each of
their frames, before your next key, while your window keeps the keyboard
focus. So a Shift or Ctrl that you hold while you type does not turn a click
of the sender's into a Shift- or Ctrl-click, or its scroll into a zoom. Drags,
held keys and clicks with modifiers are then unreliable (`hold` is false), and
a shortcut the app runs more than a quarter of a second late may run in your
window or not at all. A separate instance of the app avoids this. While you
hold a button or key in a window of the target's connection, emulated input
waits, and after a second it pauses with `input_paused`.

### New windows

A window that an app opens in response to emulated input, such as a dialog, a
popup or a file chooser, opens silently on the workspace of the window it
came from, without your focus. The plugin decides this from the new window's
parent, or else from the window of the same app that emulated input reached
in the last 5 seconds. The apps behind one xwayland-satellite share its
connection and process, so a window of theirs without a parent opens as
usual, possibly on your workspace. When your own input to that window or app
is newer, the window is yours and opens as usual. Placed windows float, centered, at
the size the app asks for. `state`'s `source` says what the plugin found:
`placed` true, or false with a `reason` of `user_input`, `disabled` or
`workspace`.

A driven Wayland window that asks for activation is only marked urgent, so it
cannot take your focus or switch your workspace. Hyprland itself refuses an
activation request that names no input the app got, such as foot's for its
bell, and spends the input serial of every request, also one the plugin
ignores, so an xwayland-satellite app can activate itself again only after
you type in or enter one of its windows. Set `place_new_windows` to
false to let Hyprland place windows that input opens. Launched windows stay
placed.

### Clipboard and selections

What a driven Wayland app copies or selects through emulated input goes to an
agent clipboard and agent primary selection. They never become your
clipboard or primary selection, and paste only in apps that emulated input
reaches, so an agent can copy in one hidden app and paste in another. Until
the agent has copied something, a paste through emulated input pastes
nothing, not your clipboard. The data passes from app to app, and the compositor
never reads it.

An app that made the agent's copy takes itself for the clipboard's owner, and
Chromium, Electron, GTK and other toolkits then paste their own copy rather
than the clipboard the compositor offers. So that such an app never pastes
the agent's copy for you, your input to it, a key, a button or the keyboard
focus, ends the agent's clipboard before the input arrives: the app's copy gets
a cancel, and the agent's next paste pastes nothing until it copies again. The
agent's primary selection stays, as GTK drops a text selection whose source
gets a cancel.

`hyprctl hyprmasq clipboard set <text-hex>` makes a text the agent's clipboard.
The plugin holds that text and writes it into the pipe of each paste, without
blocking the compositor. It sets neither your clipboard nor the agent's
primary selection. Anyone who can run `hyprctl` can set the agent's clipboard.
`hyprctl hyprmasq clipboard` reports how many pastes asked for that text, how
many of them have ended, and how many of these got all of it rather than being
cut off by an app that closed its pipe, an error or a 30 s timeout. A script
can wait for a paste to arrive before its next input. `hyprmasq-mcp`'s `paste` action uses both.

X11 apps of one X server share that server's selections: a driven X11 app of
Hyprland's XWayland can paste your clipboard, and what it copies stays in
that X server, where your X11 apps of the same server can paste it.

### Drag-and-drop

A `path` with a button, from `hyprmasq-input` or `act`, starts an app's
drag-and-drop as a hand's drag does. A drag-and-drop that emulated input
starts in a window that is not the active window never runs through
Hyprland's drag, which would follow your pointer.
When `privateDnd` is true, it runs as a private drag inside the window's own
app: it follows the emulated pointer, drops only on that window's own
surfaces, and passes the data from the app to itself. Your pointer, touch or
focus reaching the app cancels it. Otherwise the plugin cancels the drag, and
the app sees a drop that nothing accepted. `state`'s `drag` records the last
drag's `phase` (`active`, `dropped`, `finished`, `cancelled` or
`unconfirmed`), whether it ran `private`, the `reason` of a cancel, and
counters. Screenshots do not show the drag icon.

### X11 windows

Screenshots and input work for windows of Hyprland's XWayland and of
xwayland-satellite. All X11 windows of one X server share one Wayland
connection, and X routes keys by X focus and pointer input by the stacking
order. So the plugin moves X focus to the target for the input and back
afterwards, and for each pointer frame puts the X windows above the target
below it and back, all but the menus of the target's app.

**Menus and lists.** X11 toolkits such as GTK 3, Tk and Chromium open menus,
dropdown lists and the popup of a `<select>` in windows of their own, which
`hyprmasq-mcp` reports as new windows. With Hyprland's XWayland, a click opens
them, and a click in the menu's own window, or in the app's window where the
menu lies over it, picks an item. So does a press, a drag onto the item and
the release. While the app has a menu open, an override-redirect window of
the menu, popup menu, dropdown menu or combo type, X focus stays with its
window after input pauses, since Chromium closes its menus once its window
loses X focus. It goes back once the menu closes, when the socket closes, or
when you put the keyboard focus on an X11 window of the same X server. Input
to the menu's own window leaves X focus with the app's window. Escape sent to
the menu's window, or a click in the app's window outside the menu, dismisses
it. Do not close a menu with Hyprland's close dispatcher. It kills the X
client of a menu that takes no `WM_DELETE_WINDOW`, which ends the whole app,
and GTK 3 answers the request by destroying the menu under its combo box,
which crashes the app once the combo box opens again. `hyprmasq-mcp`'s
`close` refuses menus, see
`x11` in [Window state](#window-state).

**Scrolling.** XInput 2 clients, such as GTK 3 and Chromium, turn a scroll into
the difference of a scroll valuator from the value they saw last, and forget
that value when the pointer enters a window and when the device that sends
the pointer's events changes, as it does between motion and a scroll in
Hyprland's XWayland. So before the first scroll after other pointer input, the
plugin sends the X server a scroll of 0 in a frame before it, which those
clients take for the value to count from. `hyprmasq-input` also waits 20 ms
after the last wheel notch, and after a smooth scroll, before it ends. XWayland
queues scroll motion but handles the pointer's leave at the sender's end at
once, and without that wait the last scroll can reach the X root window
instead of the app. The wait is a
matter of timing, not an acknowledgement from XWayland, and `--no-pace` skips
it. Another case remains: when another X11 window of the same X server lies
over the target where the pointer is, the restacking for each frame tells the
app that the pointer entered again, and only the first notch of a scroll
counts.

To keep this from misrouting your typing, input to an X11 window is delivered
only while the active window is a Wayland window, the target itself, or an
X11 window of another X server. Otherwise the plugin drops the input and
pauses the socket, and `hyprmasq-input` stops with `input_paused`. It also
pauses for half a second when XWayland does not answer within 25 ms, or has
not read what the plugin sent it before.
`eis open <addr> force` skips the check and switches X focus within each
input frame. Use it only when you are not typing into another X11 window of
the same server yourself. X11 windows cannot hold a lease.

## Configuration

The plugin registers these options after it is loaded:

| Option | Type | Default | Effect |
| --- | --- | --- | --- |
| `plugin:hyprmasq:virtual_cursor` | bool | `true` | Draw a light-blue copy of your cursor, with a ripple for each click, where emulated pointer input lands on a visible workspace. Screenshots never include it. |
| `plugin:hyprmasq:virtual_cursor_color` | color | `rgb(6ec6ff)` | Color of the virtual cursor and ripples. An alpha, as in `rgba(6ec6ffaa)`, makes it translucent. |
| `plugin:hyprmasq:virtual_cursor_timeout` | int | `3000` | Milliseconds after the last pointer input before the cursor fades, 0 to 86400000. A lease keeps it visible. |
| `plugin:hyprmasq:place_new_windows` | bool | `true` | Open windows that emulated input opens on the workspace of the window they came from, without focus. See [New windows](#new-windows). |

In `hyprland.lua`:

```lua
hl.config({
  plugin = {
    hyprmasq = {
      virtual_cursor = true,
      virtual_cursor_color = "rgb(6ec6ff)",
      virtual_cursor_timeout = 3000,
      place_new_windows = true,
    },
  },
})
```

In `hyprland.conf`:

```ini
plugin {
    hyprmasq {
        virtual_cursor = true
        virtual_cursor_color = rgb(6ec6ff)
        virtual_cursor_timeout = 3000
        place_new_windows = true
    }
}
```

## Other plugins

Hyprland lets one plugin at a time hook a function, and refuses a second hook
of it. hyprmasq hooks the functions of most of its features only while the
feature is in use, so that other plugins can hook them in between. A feature
whose function another plugin hooks is refused, with code `hook_held` and a
message that names the function, and the rest of hyprmasq works on. hyprmasq
also loads while another plugin holds one of its functions: a load fails on
the build check and on a hooked function that the running Hyprland does not
have, not on another plugin's hook. The hooks are in groups, which go in all or nothing. Uses
of a group by several windows, senders and agents share one install, and the
group goes once the last of them has ended.

| Group | Installed | Functions | While another plugin holds one |
| --- | --- | --- | --- |
| `input` | From a sender's connection to any socket, or the start of a lease, until no socket has a sender or a lease, every window has given back its emulated focus, and a second has passed since the last input | `CXDGShellProtocol::addOrStartGrab`, `CWLDataDeviceProtocol::initiateDrag`, `Desktop::View::CWindow::activate`, `Desktop::View::CWindowPresentation::updateDecorations`, and of `CSeatManager` `setKeyboardFocus`, `setPointerFocus`, `sendKeyboardKey`, `sendKeyboardMods`, `sendPointerButton` and `sendTouchDown` | A sender is refused before anything is sent: `hyprmasq-input` exits with status 3 and `hook_held`. A lease is refused with `hook_held`. |
| `x11-input` | The same, for the sockets of X11 windows, of Hyprland's XWayland or of xwayland-satellite, and until half a second after the X focus has gone back | `CXWM::activateSurface`, `CXWM::handleFocusIn`, `CXWM::setClipboardToWayland`, `CXWM::handleSelectionXFixesNotify`, `Desktop::View::CWindow::onActivationRequest`, `Desktop::CFocusState::rawWindowFocus` | Input to X11 windows is refused the same way. Input to Wayland windows goes on. |
| `x11-selection` | From the agent's input to an X11 window of Hyprland's XWayland until your own input to one, or until no socket is open on one, since an app may take a selection long after the input, and while an X selection that the agent's input made stays the agent's, whichever agent made it | `CXWM::setClipboardToWayland`, `CXWM::handleSelectionXFixesNotify` | Installed with `x11-input`, which input to X11 windows needs. |
| `clipboard` | While the agent clipboard is a copy that an app made | `CSeatManager::sendTouchDown` | Your touch ends the agent's copy in its app only once Hyprland has sent the touch, so an app that pastes on a touch may paste the agent's copy. |
| `launch` | From a `launch` until no window that the plugin placed lives and a minute has passed since the last launch | `Desktop::View::CWindow::activate` | `launch` is refused with `hook_held` before anything starts. A window that emulated input opens is placed, but may take your focus by asking for activation. |
| `suspension` | While a hidden window is driven or a `wait … since` waits on it | `CXDGToplevelResource::setSuspeneded` | Hidden windows are told that they are suspended, as without hyprmasq. |
| `placement` | From the load | `Desktop::Rule::CWindowRuleApplicator::readStaticRules` | Windows that emulated input opens open where Hyprland puts them. |
| `idle` | From the load | `CIdleNotifyProtocol::setInhibit` | Windows that map and unmap restart idle timers, as without hyprmasq. |
| `plugin-calls` | From the load | `invokeHyprctlCommand` | A `screenshot` or `wait` that another plugin calls through `HyprlandAPI::invokeHyprctlCommand` gets Hyprland's `deferred response unavailable for in-process invocation`. |

The last three stay for the plugin's lifetime, because what they act on comes
at any time: a window that emulated input opened can map long after the
input, any window's map or unmap restarts idle timers, and other plugins call
when they like. When another plugin holds one at the load, a notification
says which feature is off until hyprmasq loads again.

The second after input covers apps that answer it with a popup, an activation
request or a new window after the sender has gone, such as a menu that a
click opens. The agent clipboard follows your keys, buttons and focus through
Hyprland's input events, which need no hook, so it works the same while the
groups are out. Hyprland tells which app a touch reaches only after its event,
so the `clipboard` group ends an app's own copy before your touch reaches it.

Hyprland takes memory for each hook it installs and never frees it, also
once the hook is removed. hyprmasq hooks each function that way once per
load, and installs it again later with that same memory, so groups that go in
and out over weeks take no more memory than hooks that stay. Should a
Hyprland reuse that memory for another hook, hyprmasq finds it changed before
it installs the hook again, and refuses the feature with code `internal`,
which `hooks` shows.

`hyprctl hyprmasq hooks` lists the groups, one line each, such as
`input on demand, not installed`. In JSON, `onDemand`, `installed`, `claims`,
the uses that keep the group installed, `installs`, how often it went in since
the load, `functions`, and `refused`, the `code` and `message` of the last
refused install if none succeeded since, or `null`.

## Security model

hyprmasq keeps automation out of your way. It is not a security boundary
between you and the agent or script that drives your windows.

- A socket exists only after `eis open`, which `hyprmasq-mcp` also runs for
  every window it captures or acts on. It lives at
  `$XDG_RUNTIME_DIR/hyprmasq/<instance>/<addr>.eis`, where `<instance>` is
  `$HYPRLAND_INSTANCE_SIGNATURE` without its leading commit hash. Both
  directories must be owned by you with mode `0700`, and the socket is `0600`.
  There is no network listener.
- Anyone who can connect to a window's socket controls that window, for
  example by typing commands into a terminal. This is the same user that can
  already run `hyprctl`, which can open any socket.
- Emulated input cannot start an interactive move or resize. While its input
  is in use, see [Other plugins](#other-plugins), a driven window that does
  not have your focus cannot grab your pointer or keyboard with a popup, and
  its drag-and-drop never reaches your pointer or another app.
- Emulated input never releases a button or key you hold: it waits, then
  pauses.
- Windows that emulated input opens do not take your focus, and neither do
  activation requests of driven apps while their input is in use.
- Emulated input cannot change your clipboard or primary selection, or paste
  them into a Wayland app.
- X11 has no isolation between the apps of one X server, so these protections
  do not extend to other X11 apps of the target's X server.
- Screenshots are written with the permissions of the user running Hyprland.
- `hyprmasq-mcp` writes the [recordings](mcp.md#recording-a-demo) that an
  agent asks for into `record.dir` of its configuration,
  `~/Videos/hyprmasq` by default, under names it chooses. The agent passes no
  path. A recording holds what the windows that the agent drives showed, as
  their screenshots do.

## Limitations

- Pointer input cannot reach the parts of a popup above or left of its
  window, because libei regions cannot start at negative coordinates.
  Screenshots with `popups` show them, and the keyboard can reach them.
- X11 menus and tooltips are separate windows: screenshots of the app window
  do not include them.
- An open X11 menu grabs the pointer of its X server, and a GTK menu its
  keyboard too, as on an X11 desktop. While a driven app has one open, your
  first click into an X11 window of the same X server closes that menu instead
  of reaching the window, and what you type into such a window goes to a GTK
  menu until it closes.
- A menu that an X11 app draws inside its own window, such as the popup of a
  `<select>` in CEF's off-screen rendering, keeps no X focus: X focus goes
  back a second after input, and an app that closes its menus then closes it.
  Use the keyboard on such a control, such as Home and Down on a closed
  `<select>`.
- X11 input is refused while another window of the target's X server has the
  focus, unless the socket was opened with `force`. Some X11 apps that only
  accept focus through `WM_TAKE_FOCUS`, such as some Java applications, may
  ignore emulated keys.
- `type` and `text` use the first layout of a keymap with several layouts.
  Characters typed through Compose depend on the application's own Compose
  table. Reconnect senders after a keymap change.
- Applications that ignore frame callbacks while hidden, or redraw slowly, can
  show stale content. A wait cannot tell a redraw that has not started yet
  from one that is done.
- A hidden window that the plugin does not drive stays suspended. A Qt
  application there draws nothing, also not at a new size, until input or a
  `wait … since` reaches it, so screenshots in between show its last frame.
  Qt 6.11 and 6.12 acknowledge a new size while suspended with an empty
  commit, which `resizing` misses.
- `resizing` misses an application that acknowledges a new size and then
  commits a frame of the old size.
- Through `hyprctl`, a wait answers within 4.5 seconds.
- A leased window keeps its emulated focus while the session is locked, but your
  real keys still go to the lock screen.
- `hyprmasq-launch` cannot find windows that an already running instance, or
  a process started by D-Bus, opens.
- A window that another program makes a driven app open, such as a link you
  open from a terminal in a driven browser, can land next to the driven window
  when emulated input reached that app in the last 5 seconds.
- The plugin uses Hyprland internals and loads only into the exact Hyprland
  build it was compiled against.
- The protections of driven windows hold while their input is in use, see
  [Other plugins](#other-plugins). An app that grabs with a popup or asks for
  activation more than a second after its last input, with no sender and no
  lease, is handled as Hyprland handles any app. A focus lease, which
  `hyprmasq-mcp` takes for every Wayland window it acts on, keeps them for its
  length.
- A popup's `grab` in `state` is known only for popups that asked for their
  grab while the `input` group was installed.
