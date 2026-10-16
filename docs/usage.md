# Usage

Every command takes a window address: the `address` field from
`hyprctl -j clients`, for example `0x55d1c0a3e4b0`.

```sh
hyprctl -j clients | jq -r '.[] | "\(.address)  \(.workspace.id)  \(.class)  \(.title)"'
```

Coding agents can use hyprmasq through `hyprmasq-mcp`, an MCP server that
wraps everything on this page in six tools. See [mcp.md](mcp.md) and
[Installation](installation.md#mcp-server). This page describes the commands
underneath, for scripts, tests and people.

## Commands

| Command | Result |
| --- | --- |
| `hyprctl hyprmasq screenshot <addr> <path.png> [X Y WIDTH HEIGHT] [max PIXELS] [popups]` | Renders the window, or the given region of it, to a PNG at `path.png`, which must be absolute. Prints `ok`. See [Screenshots](#screenshots). |
| `hyprctl hyprmasq wait <addr> [QUIET-MS [TIMEOUT-MS]]` | Answers once the window has settled, or when the timeout passes. Prints `settled` or `timeout`. See [Waiting for a redraw](#waiting-for-a-redraw). |
| `hyprctl hyprmasq wait <addr> since COMMITS [expect MS] [quiet MS] [deadline MS] [id WORD]` | Waits for a change since a value of the window's commit counter, then until it is quiet. Prints how the wait ended. See [Waiting for a change](#waiting-for-a-change). |
| `hyprctl hyprmasq cancel <word>` | Ends the pending `wait` with that id. Prints `cancelled` or `no pending wait`. |
| `hyprctl hyprmasq state <addr>` | Prints the window's size, scale, socket, commit counter, lease and capabilities. See [Window state](#window-state). |
| `hyprctl hyprmasq launch <workspace-hex> <token> <command-line-hex>` | Starts the shell command line and opens its windows silently on the workspace, both written in hex. Prints `launched pid N`. See [Launching applications](#launching-applications). |
| `hyprctl hyprmasq launched` | Prints one `address token` line per mapped window of a launch, or `no launched windows`. See [Launching applications](#launching-applications). |
| `hyprctl hyprmasq lease <addr> <ms> <owner>` | Starts or renews the window's focus lease. Prints `held N ms by OWNER`. See [Focus lease](#focus-lease). |
| `hyprctl hyprmasq lease <addr> release [owner]` | Ends the lease, only if `owner` holds it when one is given. Prints `released` or `no lease released`. |
| `hyprctl hyprmasq eis open <addr> [force]` | Creates the window's input socket and prints its path. Opening it again returns the same path. `force` drives an X11 window even while another window of its X server has the focus; see [X11 windows](#x11-windows). Each `open` sets or clears it. |
| `hyprctl hyprmasq eis close <addr>` | Removes the socket and disconnects its client. Prints `ok`. |
| `hyprctl hyprmasq eis list` | Prints one `address path` line per open socket, followed by `force` for sockets opened with `force`. |
| `hyprctl hyprmasq version` | Prints the plugin version and its interface version, such as `hyprmasq 0.3.0 interface 3`. |
| `hyprctl hyprmasq help` | Prints the command summary. |

New keywords such as `since`, `max` and `release` are plain words, because
`hyprctl` takes every argument that starts with a dash as its own flag.
Arguments are separated by whitespace, so paths and owners cannot contain any.

Errors print `error: <message>`, and `hyprctl` then exits with status 7. With
`hyprctl -j`, errors are JSON with `"ok":false` and `hyprctl` exits 0, so check
`ok` in scripts. `hyprctl` exits 6 when Hyprland does not answer within 5
seconds.

With `hyprctl -j`, every command answers in JSON:

| Command | JSON reply |
| --- | --- |
| `screenshot` | `{"ok":true,"path":"/tmp/w.png","width":1280,"height":960,"scale":2,"region":{"x":0,"y":0,"width":640,"height":480},"commits":123,"socketId":"9f3a61c2-3","resizing":false,"starved":false}` |
| `wait` | `{"ok":true,"settled":true,"waited":230,"frames":0,"framesSinceInput":3}` |
| `wait … since` | `{"ok":true,"ended":"quiet","commits":131,"socketId":"9f3a61c2-3","redrawObserved":true,"commitsSince":8,"quietMs":200,"elapsedMs":412,"resizing":false,"animated":[]}` |
| `cancel` | `{"ok":true,"cancelled":true}` |
| `state` | See [Window state](#window-state). |
| `lease` | `{"ok":true,"held":true,"remainingMs":60000,"owner":"mcp-4242-a1b2c3d4"}` |
| `lease … release` | `{"ok":true,"released":true}` |
| `eis open` | `{"ok":true,"path":"/run/user/1000/hyprmasq/<instance>/<addr>.eis","socketId":"9f3a61c2-3"}` |
| `eis close` | `{"ok":true}` |
| `eis list` | `[{"address":"0x...","path":"...","socketId":"9f3a61c2-3","force":false}]` |
| `launch` | `{"ok":true,"pid":4242}` |
| `launched` | `{"ok":true,"windows":[{"address":"0x...","stableId":"1a","token":"9c1f0e4ab27d3365"}]}` |
| `version` | `{"ok":true,"version":"0.3.0","interface":3}` |
| any error | `{"ok":false,"error":"...","code":"window_gone"}` |

Every JSON error has a `code` that scripts can rely on:

| Code | Meaning |
| --- | --- |
| `invalid_arguments` | Malformed command or arguments. |
| `window_gone` | No mapped window at that address, or it closed during a `wait`. |
| `no_socket` | The command needs an open socket for the window (`eis open`). |
| `unsupported` | Not possible for this window, such as a lease on an X11 window, or not possible this way, such as `wait` through `HyprlandAPI::invokeHyprctlCommand`. |
| `internal` | Rendering, allocation, file or other failures inside the plugin. |
| `unloaded` | The plugin was unloaded while the request was pending. |

`version` reports `interface`, which increases with every incompatible change
to the commands. New fields in JSON replies, such as `socketId` and `resizing`,
are not such a change, so read replies in a way that ignores fields you do not
know.
`hyprmasq-mcp` refuses to work with another interface than its own.

## Screenshots

Screenshots are taken at the monitor's scale, so a 640×480 window on a 2× monitor
gives a 1280×960 PNG. They show the window's own content, without borders,
shadows or the cursor. Wayland popups are included where they lie over the
window, also for windows on hidden workspaces. X11 menus are separate windows,
so a screenshot of an X11 window does not show them.

| Option | Effect |
| --- | --- |
| `X Y WIDTH HEIGHT` | Captures a region of the window. See [Regions](#regions). |
| `max PIXELS` | Fits the image within PIXELS pixels on its long side. It is never larger than at the monitor's scale. |
| `popups` | Also captures the parts of the window's Wayland popups that reach outside the window. See [Popups outside the window](#popups-outside-the-window). |

The reply's `scale` is the number of image pixels per logical pixel: the
monitor's scale, or less when `max` shrank the image. `region` is the part of
the window that was captured, in window-local logical pixels. For a window with
an open socket, `commits` is its [commit counter](#the-commit-counter) at the
moment the window was rendered, not when the file was written, and `socketId`
the socket that counter belongs to; without a socket both are `null`.
`resizing` is true when the window had not caught up with a resize when it
was rendered (see [Window state](#window-state)): the image then has the
window's new size but shows a frame drawn at another size, cut off or with
transparent or repeated pixels beside it. Wait with
[`wait … since`](#waiting-for-a-change) and take the screenshot again.
`starved` is true when the window was [starved](#window-state) right before it
was rendered: the image may show an older frame than the window would draw, such
as the empty window of an application that is still starting. Wait with
`wait … since`, with `expect`, which sends it frame callbacks, and take the
screenshot again.

A pixel at PX,PY of the PNG is at `region.x + PX / scale`,
`region.y + PY / scale` in the window-local logical pixels that
`hyprmasq-input` uses.

While Hyprland animates a window's size, as when the window opens or a tiled
neighbour opens or closes, it draws the window on screen at the animated size
and scales or cuts its content to fit. Input and the application use the
window's size meanwhile, so screenshots draw the window as it is once its
animations have ended: at the size [`state`](#window-state) reports, and
without window effects such as motion blur. Animations that move or fade a
window do not change screenshots either.

Hyprland only renders the window. The PNG is encoded and written on a
background thread while Hyprland keeps running, and `hyprctl` answers once the
file is complete, also within `hyprctl --batch`. The plugin compresses for speed
rather than size: a 2560×1440 window takes roughly 30 to 130 ms, depending on
the content.

### Regions

`X Y WIDTH HEIGHT` limits the screenshot to a rectangle of the window, in the
same window-local logical pixels as `hyprmasq-input`. The values are whole
numbers. A region that extends past the window is cut to it, and one that
starts outside it is an error; with `popups`, it may also reach into the
popups around the window. The region is still read at the monitor's scale, so
a small region shows small text at full detail.

At fractional scales the region is widened to whole device pixels, so the
captured `region` can be slightly larger than requested and have fractional
values: at scale 1.25, `1 1 10 10` captures 13×13 pixels from 0.8,0.8 with a
size of 10.4.

```sh
hyprctl -j hyprmasq screenshot "$addr" /tmp/toolbar.png 0 0 400 60
```

### Smaller images

`max PIXELS` renders the window at the monitor's scale and shrinks it on the
GPU, so only the small image is read back and encoded. A 640×480 window with
`max 320` gives a 320×240 PNG with `scale` 0.5. An image that already fits is
not changed. With a region, the image starts exactly at the region's origin in
whole logical pixels, and only the region's size is widened to whole image
pixels.

```sh
hyprctl -j hyprmasq screenshot "$addr" /tmp/overview.png max 1280
```

### Popups outside the window

A Wayland popup, such as a menu or a dropdown list, can reach past the edges of
its window. With `popups`, the captured box grows to include it: the bounding
box of the window and its popups. Popups count only as far as the bounding box
of the window and its monitor reaches. Pixels that belong to neither the
window nor a popup are transparent.

The box can start above or left of the window, so `region.x` and `region.y`
can be negative. With `popups`, a region may extend into that box and start at
negative coordinates. It is cut to the box, and only a region entirely outside
it is an error.

```sh
hyprctl -j hyprmasq screenshot "$addr" /tmp/menu.png popups
# {"ok":true,...,"width":340,"height":230,"scale":1,"region":{"x":-40,"y":-30,"width":340,"height":230},"commits":57}
```

Pointer input cannot reach the parts of a popup above or left of the window;
see [Pointer regions](#pointer-regions).

A box larger than the GPU's texture limit fails with `internal`.

## Waiting for a redraw

After input, an application takes a moment to draw the result, and a
screenshot taken too early shows the old state. `wait` answers once the window
has settled: nothing new was drawn, and no input arrived through its socket,
for QUIET-MS milliseconds (default 200). Everything the window shows counts,
including popups and subsurfaces, and popups that open or close. `wait`
answers `timeout` after TIMEOUT-MS (default 3000) when the window keeps
changing, for example during an animation or while it loads. A timeout is not
an error; take the screenshot anyway, or wait again.

`wait` always takes at least QUIET-MS, because it cannot know what the window
drew before it was called. Unlike `wait … since`, it sends no frame callbacks
itself: a window on a hidden workspace gets them only for a second after input
and while a sender is connected, see
[How input is delivered](#how-input-is-delivered). An application that draws
later than that may not draw during this wait at all; use
[`wait … since`](#waiting-for-a-change) for it. The JSON reply says how long it
`waited`, how many `frames` the window drew in that time, and, for a window
with an open socket, `framesSinceInput`: the frames since the last input
through the socket. An application often draws the result of input before
`wait` is called, so `framesSinceInput` tells whether input changed anything
and `frames` does not. It is `null` without a socket.

`hyprctl` gives up on a reply after 5 seconds, so TIMEOUT-MS is at most 4500.
Call `wait` again for longer waits. `wait` only works through Hyprland's
request socket, which `hyprctl` uses, and not through
`HyprlandAPI::invokeHyprctlCommand` (code `unsupported`), because the reply has
to wait for Hyprland's main thread.

### The commit counter

From `eis open` until the socket closes, the plugin counts the window's
commits: every commit with a new buffer or damage on the window's surface, its
subsurfaces and its popups, every surface that is unmapped, and every popup or
subsurface that appears or goes away. It watches for new popups and
subsurfaces as they are created, so it misses none, also when nothing reads the
counter in between.

The counter never falls while the socket is open. It starts again at 0 when the
socket is closed and opened again. Every socket has its own id, `socketId`,
which differs for every socket the plugin opens, also for one opened again for
the same window and after the plugin is loaded again. `state` reports the
counter, and `screenshot` replies carry its value at the moment the window was
rendered, so a wait from that value counts every change the image lacks. Both
carry the `socketId` the value counts for, and so does the reply of
`wait … since`. Compare counter values only when their `socketId` is the same.
A value of an earlier socket was read before the current socket opened, so
every commit that the current socket counted came after it.

### Waiting for a change

```sh
hyprctl -j hyprmasq wait <addr> since COMMITS [expect MS] [quiet MS] [deadline MS] [id WORD]
```

This form needs an open socket (`no_socket` otherwise). Each option may come
once, in any order. All times are 1 to 60000 milliseconds.

- **Change phase**, only with `expect`. It ends at once when the counter is
  already above COMMITS, otherwise at the first commit after it. If none comes
  within `expect` milliseconds, the wait ends with `no_redraw`.
- **Quiet phase.** It ends once there has been no commit and no input through
  the socket for `quiet` milliseconds (default 200), counted from the latest of
  the last commit, the last input and the start of the wait, and the window is
  not [resizing](#window-state). The wait ends with `quiet`.
- `deadline` (default 5000) ends the wait with `timeout` in either phase.
- `id WORD` names the wait, so that `cancel WORD` can end it with `cancelled`.

The wait also ends with `window_closed` as soon as the window closes, and the
plugin's unload answers it with code `unloaded`. A socket that is closed during
the wait does not end it.

While the wait runs, a window that Hyprland does not render, for example on a
hidden workspace, gets frame callbacks every 16 ms, so it keeps drawing. This
includes a window that is hidden only after the wait started.

| Field | Meaning |
| --- | --- |
| `ended` | `quiet`, `no_redraw`, `timeout`, `window_closed` or `cancelled`. |
| `commits` | The counter when the wait ended. |
| `socketId` | The socket whose counter the wait read. If it is not the one that COMMITS came from, `commitsSince` is not a change since COMMITS. |
| `commitsSince` | Commits after COMMITS. |
| `redrawObserved` | `commitsSince` is above 0. |
| `quietMs` | How long the window had been quiet when the wait ended, not counting the commits of animations. |
| `elapsedMs` | How long the wait took. |
| `resizing` | The window had not caught up with a resize when the wait ended, as after a `timeout` while it was resizing. |
| `animated` | The boxes of the animations drawn during the wait, `{"x","y","width","height"}` in whole window-local logical pixels: small boxes that commits since the last input damaged again and again for a second or more, at least 500 ms after it. The quiet phase does not wait for them. A box is where the application said it redrew, which can be much more than what changed: Thunderbird damages tiles of 1024×512 for a blinking caret, so a box can hold content that the window drew there and that holds still. Compare two captures to tell them apart, as `hyprmasq-mcp` does (see [Animations](mcp.md#animations)). |

COMMITS above the counter is an error (`invalid_arguments`): it can only come
from another socket of the window. So is an id that a pending wait already
uses.

`timeout` does not mean that the window was drawing: with `expect` reaching
past `deadline` and no commit, the wait ends with `timeout` without a redraw,
which `redrawObserved` tells. Only a longer `deadline` makes such a wait
longer.

The wait never blocks Hyprland. In a `hyprctl --batch` with a command that
answers the whole request itself, such as `plugin load`, the reply is that
command's, and the wait runs on unanswered until it ends or is cancelled. If
the plugin cannot start a thread for a wait, it answers with `internal`.

A typical step reads the counter from the last screenshot, sends input, and
waits for the redraw it causes:

```sh
shot=$(hyprctl -j hyprmasq screenshot "$addr" /tmp/before.png)
hyprmasq-input --socket "$socket" move 120 40 click
hyprctl -j hyprmasq wait "$addr" since "$(jq .commits <<<"$shot")" expect 1000 quiet 200 deadline 4500
```

`hyprctl` stops reading after 5 seconds, so keep `deadline` at 4500 or below
with it. A client that writes the request to Hyprland's socket,
`$XDG_RUNTIME_DIR/hypr/$HYPRLAND_INSTANCE_SIGNATURE/.socket.sock`, itself and
reads until Hyprland closes the connection can wait up to 60 seconds. Prefix
the request with `j/` for JSON, as in
`j/hyprmasq wait 0x55d1c0a3e4b0 since 120 expect 20000 deadline 30000`, and
send it in one write. Hyprland reads requests in pieces of 1023 bytes, so add
a space to a request whose length is a multiple of 1023. `hyprmasq-mcp` talks
to the plugin this way.

## Window state

```sh
hyprctl -j hyprmasq state "$addr"
```

```json
{"ok":true,"address":"0x55d1c0a3e4b0","stableId":"1a","xwayland":false,
 "size":{"width":640,"height":400},"resizing":false,"starved":false,"scale":2,
 "socket":"/run/user/1000/hyprmasq/<instance>/0x55d1c0a3e4b0.eis","socketId":"9f3a61c2-3",
 "commits":123,"interrupted":0,"lease":{"held":true,"remainingMs":58210,"owner":"mcp-4242-a1b2c3d4"},
 "capabilities":{"lease":true,"hold":true,"dnd":false,"keyboard":"yes"}}
```

- `stableId` is the `stableId` of `hyprctl -j clients`. It changes when a new
  window reuses an address.
- `size` is the window's logical size, as `hyprctl -j clients` reports it, and
  `scale` the scale of its monitor.
- `resizing` is true while the window has not caught up with a size that
  Hyprland gave it, for example when a tiled neighbour opened or closed: the
  application has not yet acknowledged that size and committed a frame after
  it, and the frame it last committed has another size. Its screenshots then
  show a stale frame, and pointer input may not land where they show things.
  An application on a hidden workspace may catch up only once it gets frame
  callbacks, which a [`wait … since`](#waiting-for-a-change) sends.
- `starved` is true while the window is on a hidden workspace, has asked for a
  frame callback that it has not got, and has got none from hyprmasq: its
  socket has sent it none, or it has no socket. Many applications draw only
  when they get a frame callback, so such a window may show an older frame
  than it would draw on screen: Thunderbird started on a hidden workspace shows
  an empty window. A screenshot answers the callbacks once, after the image,
  which is not enough for applications that need several. Opening the socket
  sends none; input and a `wait … since` do (see
  [How input is delivered](#how-input-is-delivered)), and from then on the
  window is not starved, also when it waits for a frame callback again. An
  application with nothing new to draw is starved too, and a
  `wait … since` with `expect` ends `no_redraw` for it.
- `socket`, `socketId`, `commits` and `interrupted` are `null` without an open
  socket. `socketId` names the socket that `commits` counts for; see
  [The commit counter](#the-commit-counter). `lease` is `null` when none is
  held.
- `interrupted` counts the times your own input to the window's Wayland
  connection took the keyboard focus back from the window before a shortcut or
  function key had settled, which may have kept it from taking effect. See
  [How input is delivered](#how-input-is-delivered).
- `source` is the window that this one came from, as the plugin found it when
  the window mapped, or `null`; see [New windows](#new-windows).
- `capabilities` describe what input can do in the window at the moment:

| Capability | True when |
| --- | --- |
| `lease` | The window is a Wayland window that does not share its Wayland connection with the window that has the real keyboard focus. See [Focus lease](#focus-lease). |
| `hold` | The window shares its Wayland connection with neither the real keyboard focus nor the real pointer focus. Otherwise the plugin hands the connection's pointer focus back to your window after every input frame, which releases held buttons, so drags do not work, and its keyboard focus once the window's keys are up and a shortcut among them has settled, or at once when your own input to the connection comes, which releases held keys and gives a click with modifiers the modifiers of your keyboard. |
| `dnd` | The window is the active window. Drags from other driven windows are cancelled. |
| `keyboard` | `x11-focus` for windows of Hyprland's XWayland or of xwayland-satellite, unless their socket was opened with `force`: their keys depend on which window has the X focus. Otherwise `yes`. |

Plain `hyprctl hyprmasq state` prints the same as `key: value` lines.

## Focus lease

Emulated focus normally ends soon after input, and what the app closes when
its window loses focus, such as some toolkits' dropdowns and popovers, may be
gone by the time the next input comes. Other menus, such as one that a page
draws itself, can stay open, and clicking what opened one closes it, so look
at a screenshot before you open a menu again, and click the entry of a menu
that it shows open. A lease keeps the emulated pointer and keyboard focus on
the window between inputs.

```sh
hyprctl -j hyprmasq lease "$addr" 60000 my-script     # start or renew for 60 s
hyprmasq-input --socket "$socket" move 40 20 click     # open a menu
# ... look at it, decide, later:
hyprmasq-input --socket "$socket" move 60 90 click     # pick an entry
hyprctl -j hyprmasq lease "$addr" release my-script
```

- `lease <addr> MS OWNER` starts or renews the lease for MS milliseconds, 1 to
  600000. It needs an open socket. Renewing it under another owner takes it
  over. The reply has the lease's fields, `held`, `remainingMs` and `owner`,
  next to `ok`.
- `lease <addr> release [OWNER]` ends it, with OWNER only if OWNER holds it.
  It replies `released: false` when there was no lease to end, also for a
  window without a socket.
- A lease keeps the focus that input gives the window. It never gives focus by
  itself, so take it before the input, or the focus can end before the lease
  starts. The pointer stays where the last motion put it.
- It ends when it expires, on release, when the socket closes or the window
  closes, when the plugin unloads, when the real keyboard focus moves to a
  window on the window's Wayland connection, and when input is delivered to
  another window on that connection through its own socket, since a
  connection's keyboard and pointer can only be on one surface. Then the
  emulated focus ends as without a lease, once no input has arrived for a
  second.
- While the real pointer is on a window of the same Wayland connection, the
  lease keeps only the keyboard focus. The pointer comes back with the next
  pointer input.
- It is refused with `unsupported` for X11 windows, where holding X focus
  would take your typing in your X11 apps, and while the window shares its
  Wayland connection with the window that has the real keyboard focus.

No key or button stays pressed under a lease: when the sender stops or
disconnects, the plugin releases what it held and keeps the focus. The
application shows itself as focused, with a caret or active styling, while you
work elsewhere. Your real input is not affected.

## Launching applications

`hyprmasq-launch` starts an application on a hidden workspace and prints the
address of its first window:

```sh
addr=$(hyprmasq-launch -- foot -T scratch)
addr=$(hyprmasq-launch --workspace tests --timeout 60 -- firefox --new-instance -P test)
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--workspace NAME` | `hyprmasq` | Named workspace for the windows. It opens silently: the visible workspace and the focus stay as they are. |
| `--timeout SECONDS` | `30` | How long to wait for the window. |

The plugin starts the command through Hyprland's executor, with
`hyprctl hyprmasq launch WORKSPACE TOKEN COMMAND-LINE`, and remembers the
process under a random token. The workspace and the command line go in hex,
since `hyprctl` and Hyprland look for words such as `--batch`, `/instances`
and `rollinglog` anywhere in a request. When Hyprland creates a window, before it maps
it, the plugin follows the parents of the window's process up to 32 levels. A
window whose process is the launched one or descends from it belongs to the
launch, and `hyprmasq-launch` prints the first one that
`hyprctl hyprmasq launched` lists. This finds the windows of a launcher script
that starts the application as a child instead of with `exec`, such as
Chromium's and so Brave's, and every window of the application.

For a window created within a minute of the launch, the plugin tags the window
and registers a window rule for that tag, so it opens silently on the
workspace: the visible workspace and the focus stay as they are. Registered
after the rules of your configuration, it takes precedence over their
workspaces. Once the window has mapped, the plugin removes the rule and the
tag. Windows that the application opens later, or that a program
started in a launched shell opens a minute after the launch, open where
Hyprland and your rules put them, unless emulated input opened them
(see [New windows](#new-windows)).

Applications that hand the request to an instance that is already running,
such as a `footclient` of a `foot --server` or a browser that is already open,
open the window from a process outside the launch. The launch then times out,
and the window opens wherever that instance puts it. Start such applications
with an option for a separate instance.

`hyprmasq-launch` needs `hyprctl` and `jq`. The Nix package brings `jq`.

## hyprmasq-input

`hyprmasq-input` connects to a socket, checks all of its actions, runs them in
order, and exits once the plugin has processed all of them.

```sh
hyprmasq-input --socket PATH [--no-pace] [--json] [--budget MS] [--locale NAME] ACTION...
```

| Option | Meaning |
| --- | --- |
| `--socket PATH` | The socket that `eis open` printed. Required. |
| `--no-pace` | Sends input as fast as the plugin takes it, without the delays of [Pacing](#pacing). |
| `--json` | Prints the outcome as one JSON object on stdout. See [JSON output](#json-output). |
| `--budget MS` | Refuses the actions, before anything is sent, when they would not end within MS milliseconds (0 to 3600000) of the start at their pace. See [Checks before sending](#checks-before-sending). |
| `--locale NAME` | The locale of the target application, such as `pt_BR.UTF-8`, whose Compose table types the characters without a key of their own. Default: the sender's own. See [Typing text](#typing-text). |

Options come before the first action, in any order.

| Action | Meaning |
| --- | --- |
| `move X Y` | Move the pointer to X,Y in window-local logical pixels. Fractions are allowed. The point must lie in the [pointer regions](#pointer-regions). |
| `click [BUTTON [COUNT [MODS]]]` | Click BUTTON (default `left`) at the pointer, COUNT times (1 to 3, default 1), holding the modifiers MODS, such as `ctrl+shift`, or `-` for none. |
| `down BUTTON`, `up BUTTON` | Press or release a button. |
| `drag X1 Y1 X2 Y2 [BUTTON [MODS]]` | Move to X1,Y1, press BUTTON (default `left`), move to X2,Y2 in steps, and release. |
| `scroll DX DY` | Smooth scroll by DX,DY logical pixels, followed by a scroll stop. Positive DY scrolls down. |
| `wheel DX DY` | Scroll by whole wheel notches, -100 to 100 on each axis. Positive DY scrolls down, positive DX right. |
| `key CODE` | Press and release a Linux evdev key code, for example `28` for Enter or `1` for Escape. |
| `keys CHORD [REPEAT]` | Press the keys of CHORD together, such as `ctrl+shift+t` or `enter`, REPEAT times (1 to 100, default 1). See [Key names](#key-names). |
| `type TEXT` | Type text with Hyprland's keymap. Tags such as `<enter>` or `<shortcut:ctrl+l>` in the text press keys; see [Typing text](#typing-text). |
| `text TEXT` | Type TEXT exactly as it is, without tags or escapes. A line break presses Enter and a tab presses Tab. |
| `sleep MS` | Wait 0 to 3600000 milliseconds. |
| `sync MIN MAX` | Once the plugin has processed every action before it, print `{"sync":N}` on stdout, where N is the number of those actions, and wait for a line with N on stdin, at least MIN and at most MAX milliseconds (MIN up to MAX up to 3600000). See [Waiting for the caller](#waiting-for-the-caller). |

Buttons are `left`, `right`, `middle`, or an evdev code from `0x110`
(`BTN_MOUSE`) on, such as `0x113`. Key and button codes are listed in
[`linux/input-event-codes.h`](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input-event-codes.h).

```sh
socket=$(hyprctl hyprmasq eis open "$addr")

# Click a button at 120,40, then type into the field that got focus.
hyprmasq-input --socket "$socket" move 120 40 click text 'hello world' keys enter

# Double-click a word, then copy it.
hyprmasq-input --socket "$socket" move 200 80 click left 2 keys ctrl+c

# Right-click, wait for the menu, pick its first entry with the keyboard.
hyprmasq-input --socket "$socket" move 200 200 click right sleep 200 keys down keys enter

# Drag from one point to another, and scroll three notches down there.
hyprmasq-input --socket "$socket" drag 50 50 300 50 wheel 0 3
```

### Checks before sending

Every action is checked before anything is sent. The syntax of all actions is
checked before connecting. After connecting, and still before sending
anything, every point is checked against the pointer regions, and every key
name and every character of `keys`, `type` and `text` against the keymap. Any
problem fails the whole run with exit status 2, and nothing is sent.

With `--budget MS`, the sender then adds up the delays of [Pacing](#pacing)
for the keys and buttons that the actions resolved to, with every `sleep`,
`<wait:MS>` and `<settle>`, and the MIN of every `sync`. When that sum is more than what is left of MS since the sender
started, it fails with code `too_long` and exit status 2, and sends nothing.
The sum leaves out the waits for the plugin, so a caller that knows how long
those take passes a budget with room for them. A character typed through
Compose counts a key for each step of its sequence, which only the sender
knows, since it depends on the keymap: on a German layout `^` is two keys.
So a caller with a deadline, such as `hyprmasq-mcp`, gets a run refused that
its deadline would stop part-way.

### Key names

`keys` and the MODS of `click` and `drag` take key names joined by `+`. Names
ignore case:

- the modifiers `ctrl` (`control`), `shift`, `alt` and `super` (`meta`,
  `win`, `cmd`), the only names that MODS takes;
- `space`, `enter`, `tab`, `esc`, `backspace`, `delete` (`del`), `up`, `down`,
  `left`, `right`, `home`, `end`, `pageup` and `pagedown`;
- a single character, such as `a`, `7` or `ä`;
- any XKB keysym name, such as `Return`, `Escape`, `F5`, `plus` or
  `XF86AudioMute`.

A letter names its key: `A` presses the A key without Shift, and
`shift+a` types a capital. A key that needs modifiers on the active keymap
brings them along, so `ctrl+@` also holds AltGr on a German layout. The plus
key is `plus`, and an empty name, as in `ctrl++`, is an error. A chord has at
most 9 keys. A modifier named last, as in `ctrl+shift`, is pressed as the key.

### Pacing

`hyprmasq-input` times input like a person, since applications can miss input
that arrives faster than any hand could send it:

| Input | Timing |
| --- | --- |
| Between actions | 50 ms from the end of one to the start of the next. |
| Press after motion | At least 30 ms after the last motion. |
| `click` | Modifiers down one per frame, then 10 ms. Button held 50 ms. Clicks of one action 80 ms apart. Modifiers up afterwards, in reverse. |
| `keys` | Modifiers down one per frame, the key 10 ms after the last of them, held 15 ms, then everything up in reverse. Repeats 15 ms apart. |
| `key` | Held 15 ms. |
| `type`, `text` | Each key held 5 ms, 10 ms between keys. The modifiers a character needs, such as Shift or AltGr, go down just before its key. |
| `wheel` | One notch per frame, 20 ms apart. |
| `drag` | Motion to the start, modifiers and 10 ms, press, 50 ms, then motion in steps of at most 10 logical pixels, 10 ms apart, at least 21 steps and at most 2000, then 50 ms and release. |
| `scroll` | The scroll and its stop at once. |

A `sync` takes the place of the gap between the actions around it: the action
after it starts once the sync has ended and 50 ms after the end of the action
before it.

`--no-pace` leaves out these delays; a `drag` then sends its steps at once.
`sleep`, `<wait:MS>`, `<settle>` and `sync` still wait.

### Waiting for the caller

`sync MIN MAX` lets the program that runs the sender wait for something between
two actions, as `hyprmasq-mcp` waits for a window to answer a click before the
keys after it, without ending the run, so the window keeps the emulated focus
that the sender's input gave it. Once the plugin has processed every action
before the sync, the sender prints `{"sync":N}` on stdout, N the number of
those actions, and waits for a line with N on stdin. It goes on at that line,
but not before MIN milliseconds, and after MAX milliseconds without one. A line
with another number, such as a late answer to an earlier sync, does not count,
and with stdin closed, a sync waits only its MIN. Meanwhile it handles the
plugin's events, and a signal, a pause of the devices or the end of the
connection stop it as they stop a `sleep`.

```sh
# Click, let the caller wait for the window to settle, then press Down.
coproc input { hyprmasq-input --json --socket "$socket" move 40 20 click sync 0 1000 keys down; }
read -r line <&"${input[0]}"   # {"sync":2}
hyprctl -j hyprmasq wait "$addr" since "$commits" expect 150 quiet 150 deadline 1000
echo 2 >&"${input[1]}"
cat <&"${input[0]}"            # the JSON result
```

In both modes, the sender waits for the plugin after every action and after
every 16 frames, so that it knows what the plugin has processed and never
fills the socket. These round trips confirm that Hyprland processed the
events, not that the application handled them.

### Pointer regions

The pointer device has up to three regions, in window-local logical pixels:
the window itself, the area right of it, and the area below it, each up to
the edges of the bounding box of the window and its monitor. A point over one
of the window's popups reaches the popup at the place where screenshots show
it, also for a window whose xdg geometry has an offset, such as a GTK window
with client-side shadows, and while Hyprland animates the window's position.
Any other point in the regions is clamped to the window.

libei region offsets are unsigned, so no region can lie left of or above the
window, and pointer input cannot reach negative coordinates. `hyprmasq-input`
refuses a point outside the regions before sending anything, with
`X,Y is outside the pointer regions, which span 0,0 to R,B`. The part of a
popup that lies over the window can still be clicked, and the keyboard reaches
the rest.

### Exit status

| Status | Meaning |
| --- | --- |
| 0 | The plugin processed every event, apart from releases left unsent when it closed the connection, which it sends itself: see [Input that closes its window](#input-that-closes-its-window). |
| 1 | The run stopped after it started: no connection, no devices, paused input, a lost or replaced device, a closed connection, or a signal. Some input may have been sent; `--json` tells how much. |
| 2 | An option, action, point, key name or character is invalid, or the actions take longer than `--budget`. Nothing was sent. |

Messages go to stderr as `hyprmasq-input: <message>`.

### JSON output

With `--json`, `hyprmasq-input` prints one object on stdout when it exits, as
its last line, after the lines of its syncs:

```json
{"ok":false,"done":1,"total":2,"failedIndex":1,"delivery":"partial","progress":{"keys":39},"compose":null,"error":{"code":"input_paused","message":"the server paused input, so some events were dropped"}}
```

| Field | Meaning |
| --- | --- |
| `ok` | No error. |
| `done` | Actions that ran to their end and that the plugin confirmed, and one that the closed connection cut off with only releases left: see [Input that closes its window](#input-that-closes-its-window). |
| `total` | The number of actions. After a syntax error, the actions up to and including the invalid one. |
| `failedIndex` | The 0-based index of the action that failed, or `null` on success and when the run failed before its first action, for example because it could not connect. |
| `delivery` | What reached the plugin, see below. |
| `progress` | For a failed `keys`, `type` or `text` action, `{"keys":N}`: the key presses of that action that the plugin processed, or that were sent when the delivery is `uncertain`. Modifiers do not count, and a character typed through Compose counts as several keys. Otherwise `null`. |
| `paceMs` | Only with `--budget`: the milliseconds that the actions take at least at their pace, as `--budget` counts them, or `null` when the run ended before every action was checked. |
| `compose` | When a character of `type` or `text` is typed through Compose, `{"locale":L,"requested":R}`: the table of locale L typed it, and R is the locale asked for, which differs from L when there is no table for R. Otherwise `null`. |
| `error` | `code` and `message`, or `null`. |

| `delivery` | Meaning |
| --- | --- |
| `none` | Nothing was sent. |
| `complete` | The plugin processed every event, apart from releases left unsent when it closed the connection. |
| `partial` | The actions before `failedIndex` were processed. The failed action may be processed in part, for example some keys of a text, and later actions were not sent. The plugin took every frame that was sent, so `progress.keys` counts exactly the keys it processed. |
| `uncertain` | The connection ended, or the plugin stopped answering, before it took everything that was sent, for example frames sent after the plugin closed the socket. Any action from `failedIndex` on may or may not have been processed. |

| `error.code` | Meaning |
| --- | --- |
| `invalid_arguments` | An option, action or point is invalid, or a key name unknown. Exit status 2 when nothing was sent. |
| `cannot_type` | The keymap cannot produce a character, a key or a modifier. Exit status 2. |
| `too_long` | The actions take longer at their pace than what is left of `--budget`, which `paceMs` and the message tell. `failedIndex` is `null`. Exit status 2. |
| `connect_failed` | The socket cannot be reached. |
| `no_devices` | The plugin offered no pointer and keyboard within 5 seconds, removed the pointer without offering a new one within a second, removed the keyboard, or sent no usable keymap. |
| `input_paused` | The plugin paused the devices: an X11 window's input is refused while another window of its X server has the focus. See [X11 windows](#x11-windows). |
| `unconfirmed` | The plugin closed the connection, replaced the pointer while events on it were unconfirmed, or did not answer within 5 seconds. |
| `cancelled` | SIGINT or SIGTERM stopped the run. It reports within about a second, see [Releasing held input](#releasing-held-input). |

"Processed" means that the plugin delivered the events to the window. It does
not mean that the application handled them. Events that the plugin takes only
to drop them, because the window closed, are never confirmed; see
[Input that closes its window](#input-that-closes-its-window).

### Releasing held input

`hyprmasq-input` leaves nothing pressed. The last action takes along whatever
is still held, so `move 50 50 down left` also releases the button. After a
failure, and on SIGINT and SIGTERM, it releases every button and then every
key it pressed before it exits, so modifiers held around a click or drag
still apply to its release. When a sender exits without releasing, the plugin
releases for it: see [How input is delivered](#how-input-is-delivered).

On SIGINT or SIGTERM, `hyprmasq-input` stops whatever it waits for at once:
a `sleep`, pacing, or an answer of the plugin. It releases what it holds,
tries for up to a second to get that confirmed, and exits with status 1 and
code `cancelled`. If the plugin does not answer within that second, for
example because Hyprland is stopped, the delivery is `uncertain`. A signal
while it still resolves a long text, before anything is sent, stops it with
delivery `none`.

### Replaced devices

The plugin replaces the pointer device when its regions change: after the
window is resized or moved, or the monitor layout changes. `hyprmasq-input`
then waits up to a second for the new pointer and goes on, as long as every
event sent to the old one was confirmed and no button was held on it.
Otherwise it stops with `unconfirmed` and delivery `partial`, since events that
reached the removed device are lost. The plugin never replaces the keyboard:
its removal means that the connection is ending.

### Input that closes its window

Input can close its own window, such as Ctrl+D in a shell, a quit shortcut or
a click on a close button. The plugin closes the socket when the window
closes, but it first takes what the sender still has in the socket and
answers the pings among it. Input that it took only to drop, because the
window was gone, is never confirmed. So the outcome depends on what was still
to come:

- When the plugin confirmed every frame that was sent, an action that had
  only the releases of what it holds left counts as done: the plugin sends
  those itself when a sender goes away. A dialog that closes as its button
  goes down closes while the click's button is held, and so does one without
  the `hold` capability, for which the plugin releases the button right after
  the press. The click still counts as done.
- So when the closing action was the last one and the plugin confirmed it,
  or all of it but its releases, the run succeeds with delivery `complete`,
  which is usual for a click on a close button.
- When actions followed, `hyprmasq-input` fails with code `unconfirmed` and
  the message `the server closed the connection, as hyprmasq does when the
  window closes or another sender connects`, also at a `sleep`, which stops
  with the connection. The delivery is `partial` when the plugin took
  everything that was sent, and `progress.keys` then counts the keys the
  window got before it closed. It is `uncertain` when frames were sent after
  the plugin had closed the socket.

So a script cannot tell from the delivery alone whether the window closed;
check the window list.

### Typing text

`type` and `text` press the keys that produce each character with the keymap
of Hyprland's active keyboard, which the plugin sends to every sender. On a
German layout, `z` presses the key labelled Z there, and `@` holds AltGr.
Characters that only a dead key produces, such as `^` on a German layout, are
typed as the shortest sequence of the Compose table that the keymap can press,
usually the dead key followed by Space or another key. The application turns
that sequence into a character with a Compose table of its own. Qt apps and
foot use the table of their locale, the first of `LC_ALL`, `LC_CTYPE` and
`LANG` that is set, or C, and the tables differ: in `pt_BR.UTF-8` the dead
acute and `c` give `ç`, in `en_US.UTF-8` `ć`. GTK uses the table of its locale
in `pt_BR`, `el_GR` and `fi_FI`, and elsewhere a built-in one that follows
`en_US.UTF-8`. So pass the target's locale as `--locale`; without it, the
sender uses the table of its own locale. When
there is no table for the locale, the sender uses that of `en_US.UTF-8`, which
most UTF-8 locales use and GTK's built-in table follows, says so on stderr,
and reports both locales in `compose`. Such characters may then arrive as
others. A character that the keymap cannot produce, also not through the
table, fails the whole run before any key is sent.

```sh
# The locale of the window's process: LC_ALL, LC_CTYPE, LANG or C.
pid=$(hyprctl -j clients | jq -r --arg a "$addr" '.[] | select(.address == $a) | .pid')
locale=$(env -i $(tr '\0' '\n' < "/proc/$pid/environ" | grep -E '^(LC_ALL|LC_CTYPE|LANG)=') \
  sh -c 'echo "${LC_ALL:-${LC_CTYPE:-${LANG:-C}}}"')
hyprmasq-input --socket "$socket" --locale "$locale" text 'Ação'
```

`text` types its argument as it is. `type` reads tags in it, which press keys
or wait:

| Tag | Result |
| --- | --- |
| `<enter>`, `<tab>`, `<space>`, `<esc>`, `<backspace>`, `<delete>` | Press that key. |
| `<up>`, `<down>`, `<left>`, `<right>`, `<home>`, `<end>`, `<pageup>`, `<pagedown>` | Press that key. |
| `<F5>`, `<Return>`, `<XF86AudioMute>`, … | Press the key for an XKB keysym that does not type a character. |
| `<KEY:COUNT>` | Press a named key or keysym COUNT times, 1 to 100, for example `<down:5>`. |
| `<shortcut:MOD+KEY>` | Hold the modifiers and press KEY, for example `<shortcut:ctrl+l>` or `<shortcut:ctrl+shift+F5>`. The modifiers are `ctrl`, `shift`, `alt` and `super`, with `control`, `meta`, `win` and `cmd` as aliases. KEY is a character, a named key or a keysym. |
| `<wait:MS>` | Wait MS milliseconds, up to 3600000. |
| `<settle>` | Wait 300 milliseconds. |

Tags are not case-sensitive. `\<` types a literal `<`. Any other `<…>` that is
not a tag, such as `<div>` or `<b>`, is typed as it is, and other backslashes
are typed too. An invalid tag, such as `<wait:soon>` or `<tab:0>`, fails the
whole run before any key is sent.

```sh
hyprmasq-input --socket "$socket" type 'user<tab>secret<enter>'
hyprmasq-input --socket "$socket" type '<shortcut:ctrl+l>https://example.org<enter>'
hyprmasq-input --socket "$socket" type 'if (a \< b) {<enter>'
hyprmasq-input --socket "$socket" text 'if (a < b) {' keys enter
```

### A screenshot and input loop

```sh
addr=$(hyprmasq-launch -- foot)
socket=$(hyprctl hyprmasq eis open "$addr")

hyprctl hyprmasq screenshot "$addr" /tmp/before.png
hyprmasq-input --socket "$socket" text 'ls -la' keys enter
hyprctl hyprmasq wait "$addr"             # until the application has drawn the result
hyprctl hyprmasq screenshot "$addr" /tmp/after.png

hyprctl hyprmasq eis close "$addr"
```

### Other libei clients

The socket is a standard EIS endpoint. Any libei sender can connect by passing
the path to `ei_setup_backend_socket()`, or through `LIBEI_SOCKET` for tools
that read it. The plugin offers one seat with:

- an absolute pointer with buttons and scrolling, whose regions are described
  in [Pointer regions](#pointer-regions). The first region is always the
  window at its logical size, starting at 0,0;
- a keyboard whose keymap is the keymap of Hyprland's active keyboard.

Input is applied at the end of each libei frame. When the pointer's regions
change, the pointer device is replaced by one with the new regions, and clients
see it as a removed and re-added device. The plugin drops events that reach the
removed one. The keyboard is not replaced; reconnect after changing the keymap.
Only one sender is connected to a socket at a time: a new sender replaces the
previous one. Receiver-mode clients are rejected. The plugin answers pings once
it has processed everything sent before them. When the window closes, it still
answers the pings in the socket, but it removes the devices before it answers
one that follows input it dropped, so treat an answer after a removed device as
no confirmation. The plugin pings a sender that has sent nothing for a second,
once a second, which libei answers by itself.

## How input is delivered

The plugin sends events directly to the target window's Wayland objects. It does
not move the real cursor or change which window Hyprland considers focused. The
application sees pointer and keyboard focus arrive and receives the events.

Without a [lease](#focus-lease), the emulated focus ends as soon as the sender
stops emulating or disconnects, which `hyprmasq-input` does when it exits.
While a sender stays connected, it ends after about a second without input.
With a lease, it stays until the lease ends.

When a sender stops emulating or disconnects, the plugin releases every key,
modifier and button it held. Without a lease, it releases the buttons and then
takes the pointer and keyboard focus away, which releases the keys and
modifiers for the application. With a lease, it sends the releases, buttons
before keys, and keeps the focus. libeis does not always report a sender's
disconnect: when a send to a sender that has exited fails, it closes the
connection silently. So the plugin pings a sender that has sent nothing for a
second, once a second, and handles a failed ping as the disconnect. What such a
sender held is released 1 to 2 seconds after it exits.

When the target belongs to the same program as the window you are using, for
example two windows of one `foot --server` or of one browser, both windows
share one Wayland connection. The plugin then hands that connection's pointer
focus to the target for a single input frame and restores it to your window
before any real input is handled. The keyboard focus goes back to your window
with your modifiers after every frame of plain typing too, but stays with the
target while the sender holds keys, and after a key pressed with Ctrl, Alt or
Super held, a function key, Escape or a media key until it has settled, a
quarter of a second later: apps such as Chromium take the window with the
keyboard focus for the active one, and run a shortcut such as Ctrl+L or
Alt+Left only once the page has let its key go by, a moment after the key.
Your own keys, modifiers and clicks to that connection take the keyboard
focus back first, before Hyprland sends them, so they reach your window, never
the target. A shortcut or function key of the target that had not settled then
may do nothing, which `state` counts in `interrupted`. Drags do not carry
across the frames, and neither do held keys and key repeat once your input has
taken the keyboard back. Only the modifier state of the sender's keys does:
each keyboard frame enters the target again with it, so `keys ctrl+shift+t`
works, while `keys a+b` may arrive as `a` and then `b`, and a click with
modifiers with the modifiers of your keyboard. `state` reports this as a
missing `hold` capability. Each time the keyboard focus moves between the two
windows, the app sees one of them lose focus: a browser fires `blur` and
`focus` in your page.

Hidden windows normally stop receiving frame callbacks, so many applications
stop redrawing. The plugin sends a hidden target frame callbacks every 16 ms
for about a second after input, for as long as a sender is connected and has
its devices, which `hyprmasq-input` keeps until it exits, also through a
`sleep`, and during a `wait … since` for as long as the wait runs. So the next
screenshot shows the result. The plain `wait` sends none of its own. A window
that has got none of these yet, such as an application that started on a
hidden workspace, may not have drawn its content: `state` reports it as
`starved`.

## Virtual cursor

Where emulated pointer input lands, the plugin draws a copy of your cursor
theme's arrow in light blue, and a ring that grows and fades at each click. The
cursor stays for 3 seconds after the last pointer event, or as long as a lease
keeps the emulated pointer on the window, then fades out. Keyboard input
shows nothing.

It is drawn only while the window is on a visible workspace, above windows and
below layer-shell surfaces such as bars. Window screenshots never include it.
It uses the theme and size of your real cursor, from hyprcursor or XCursor, and
keeps the theme's shading: the fill takes the configured color, the outline a
dark or white that contrasts with it.

Set the options after the plugin is loaded:

```lua
hl.config({
  plugin = {
    hyprmasq = {
      virtual_cursor = true, -- false turns it off
      virtual_cursor_color = "rgb(6ec6ff)", -- an alpha, as in rgba(6ec6ffaa), makes it translucent
      virtual_cursor_timeout = 3000, -- milliseconds after the last input before it fades out, up to a day
    },
  },
})
```

## New windows

Hyprland opens a new window on the active workspace and gives it the keyboard
focus. An app cannot tell emulated input from yours, so a window that it opens
in response to emulated input, such as a compose window, a dialog, a browser's
popup or a portal's file chooser, would land on your screen and take your
typing. The plugin opens such a window silently on the workspace of the window
it came from instead: the visible workspace and the focus stay as they are.
When you are on that workspace yourself, you see the window open there, and
keep working in your window. This also covers apps that no launch started, such
as your own browser.

Hyprland creates a Wayland window when the app asks for it, before the app can
name its parent, and an X11 window before it has its `WM_TRANSIENT_FOR` and
`_NET_WM_PID`. So the plugin decides before Hyprland reads the window's rules: for
a Wayland window at its first commit, where apps have usually named the parent,
and again when it maps; for an X11 window when it maps. A new window comes from

- its parent, when it has one: an `xdg_toplevel` parent, `WM_TRANSIENT_FOR`,
  or through xdg-foreign a window of another process, as a portal's file
  chooser has. The parent counts when emulated input reached it or the plugin
  placed it, also as a window of a launch, and your own input to it is older
  than that. A window whose parent is one you work in, such as a dialog of your
  own window of a driven app, opens as usual.
- otherwise the window of its app that emulated input reached last, in the 5
  seconds before the app created the new window, when your own input to that
  app is older. That window may have
  closed since, as a dialog whose Send button made the app open a progress
  window: the new window then opens on the workspace the closed window was on,
  or if that is gone, on that of its parent. An app is a Wayland
  connection, and for Hyprland's XWayland, which serves every X11 app on one
  connection, an X11 process. The apps of an xwayland-satellite share its
  connection and report its process, so they cannot be told apart, and its
  windows without a parent open as usual.

Your input is a key, pressed or released, or a mouse button, to the window and
app with the real keyboard or pointer focus, and a touch or a tablet tip, to the
window that Hyprland focuses for it. When it is newer, and came before the app
created the window, the window is yours, and Hyprland places and focuses it as
usual. An app creates a Wayland window when it asks for it, at its first commit,
before it has drawn anything: in a benchmark, Chromium created a popup 60 ms
after the click that opened it and drew it 240 to 290 ms later. Input after
that, yours or the agent's, cannot have opened the window and does not count.
For an X11 window, which Hyprland learns of when it maps, input counts up to
then, and so it does for a window that nothing before its first commit can have
opened, as one that an app creates ahead and draws on later input.
Which of your input counts:

- for a window with a parent, any of your input to the parent;
- for one without, any of your input to the window of the app that emulated
  input reached last, or input elsewhere in the app that can open a window,
  newer than the agent's last such input to the app. Input that can open a
  window is a click, a touch, a tablet tip, and a key held with Ctrl, Alt or
  Super or one that does not type, edit or move in text, such as Enter or a
  function key. Characters, Space, Tab, Backspace, Delete, Escape, the arrows,
  Home, End and the page keys do not count, nor do modifiers alone. Neither
  does Enter that types a line break: while the app's focused text field takes
  text through text-input, or less than 2 seconds after you let go of a key
  that typed or deleted a character in the same window, with no other key,
  shortcut or click between them. The keypad's digits type only with Num Lock
  on, and a key that a keybind of Hyprland takes types nothing. Apps without
  text-input, such as Chromium unless it runs with
  `--enable-wayland-ime`, tell no text field, so the Enter that ends a line you
  type there counts as a line break by the typing before it. Emulated input
  counts the same way, without the text field and the typing: when the agent's
  input to the app had none that can open a window, any of it counts.

So with your browser window on your workspace and an agent's window of the
same browser on a hidden one, a popup that the agent's click opens stays on the
hidden workspace while you type in your window, also when you end a line as it
opens, and one you open from your window opens on your screen. An app does not
tell which input made it open a window, so when your click or shortcut in one
window of the app races the agent's click in another, before the app creates
the window, the newer one decides.

The plugin places a window as it places the windows of a
[launch](#launching-applications): it tags the window and registers a rule
`workspace <workspace> silent;no_initial_focus 1;float` for that tag, which
Hyprland applies as the window maps, and removes both then. Registered after the
rules of your configuration, the rule takes precedence over their workspaces and
over Hyprland's workspace token. A window that the plugin placed, by a launch or
next to the window it came from, is only marked urgent when it asks for
activation, also with `misc:focus_on_activate`, for as long as it lives; an
activation that you force, for example through a taskbar, still focuses it.

For now, such a window floats at the size the app asks for, centered on its
workspace, while a launch's windows tile as usual. A window that input opens in
a launch's first minute, which the launch places, floats on the launch's
workspace too. Tiled, it would take half of
the space of the window the agent works in, which would then have to redraw at
a new size, and on your visible workspace it would move your own windows. A
Wayland window that the plugin places only when it maps, such as one that names
its parent after its first commit, floats at the size Hyprland suggested to it
for the active workspace.

`state` reports what the plugin found in `source`:

| `source` | Meaning |
| --- | --- |
| `null` | The window came from no window that emulated input reached. |
| `{"window":"0x…","stableId":"1a","placed":true}` | It opened on the workspace of that window, without the focus. |
| `{"window":"0x…","stableId":"1a","placed":false,"reason":"user_input"}` | Your own input to that window or app was newer and came before the app created the window, so Hyprland placed it. |
| `{"window":"0x…","stableId":"1a","placed":false,"reason":"disabled"}` | `place_new_windows` is off. |
| `{"window":"0x…","stableId":"1a","placed":false,"reason":"workspace"}` | A window rule cannot name the workspace of that window: a named or special workspace whose name has characters other than letters, digits, `_`, `.` and `-`. |

`stableId` is the `stableId` of the window it came from while that window
lives, and `null` once it is gone, so it tells that window from a later one at
its address. `hyprmasq-mcp` uses `source` to find the windows that an agent's
input opened after its call had returned, which its next `act`, `screenshot`
or `windows` returns and `windows` can wait for, and the window to go on in
once a window it drove has closed (see [MCP server](mcp.md#late-windows)).

Plain `state` prints it as `source: 0x… placed`, `source: 0x… not placed:
user_input` or `source: none`.

A placed window on a hidden workspace gets frame callbacks as any window
there does: from the plugin, once input reaches it through its socket or
during a `wait … since` on it.

Turn it off to get Hyprland's behaviour back for windows that input opens; the
windows of a launch stay placed:

```lua
hl.config({
  plugin = {
    hyprmasq = {
      place_new_windows = false, -- windows that input opens open where Hyprland puts them, with the focus
    },
  },
})
```

## X11 windows

Screenshots and input work for windows of Hyprland's XWayland and of
[xwayland-satellite](https://github.com/Supreeeme/xwayland-satellite). Both
make every X11 window of one X server share a single Wayland connection, and
X11 does not route input by Wayland surface:

- XWayland sends keys to the window with X input focus. For an XWayland target,
  the plugin moves X input focus to it before the first key, click or scroll, as
  a real click would, and moves it back about a second after the last input, or
  as soon as Hyprland changes focus.
- XWayland sends a click to the topmost X window at that point, and windows on
  hidden workspaces overlap the visible ones. For each pointer frame, the plugin
  raises the target and its menus to the top of the X stack, then puts them
  back.
- xwayland-satellite moves X focus itself when it receives Wayland keyboard
  focus, but only after it has handled the whole batch of Wayland events that
  carried it. So when the plugin gives a satellite window keyboard focus, it
  waits 50 ms before sending the keys, and it takes the focus away no sooner
  than 50 ms after the last key.

Before each of those X changes, the plugin waits until XWayland has read all
pending Wayland input, so X changes and input arrive in order.

X focus makes some apps consider themselves active. Wine, for example, moves
its window to the foreground, which it needs to take input at all, and only
leaves it when `_NET_ACTIVE_WINDOW` names another window. When the plugin gives
X focus back, and when it refuses an activation request from a driven app while
it holds no X focus, it writes `_NET_ACTIVE_WINDOW` again unchanged, so such
apps read it and learn that they are in the background again. This needs a Hyprland that names a window there
while a Wayland window has the focus. Stock Hyprland 0.56.2 sets it to None,
which Wine ignores.

Moving X focus and stacking would misroute input if you were working in
another window of the same X server. So input to an X11 window is only delivered
while the active window is a Wayland window, the target itself, or an X11
window of a different X server. Hyprland's XWayland and each xwayland-satellite
are separate X servers. Otherwise the plugin drops the frame and pauses the
socket's devices. `hyprmasq-input` then stops with code `input_paused` and
`the server paused input, so some events were dropped`, and `progress.keys`
tells how many keys of a text got through. The devices resume
when the focus changes to a window that allows input, at the earliest half a
second after the pause. The plugin also pauses them for half a second when
XWayland does not answer within 100 ms, so a stuck X server cannot freeze
Hyprland.

`eis open <addr> force` skips this check. The plugin then switches X focus
to the target and back within each input frame, as it does for two windows
that share a Wayland connection. Use it only when you are not typing into the
other X11 window yourself: your own keys could reach the target while it holds
X focus, which for xwayland-satellite lasts until 50 ms after the sender's
last queued key, and a menu opened in the target can grab your keyboard.

X11 windows cannot hold a [lease](#focus-lease), so menus that close on focus
loss close once the sender exits. Open them and pick the entry in one run.

## Security model

- A socket exists only after an explicit `eis open`. It lives at
  `$XDG_RUNTIME_DIR/hyprmasq/<instance>/<addr>.eis`, where `<instance>` is
  `$HYPRLAND_INSTANCE_SIGNATURE` without the commit hash in front: the time
  and random number that tell running instances apart, such as
  `1791199230_138021124`. Both directories must be owned by you with mode
  `0700`, and the socket itself is `0600`.
- A socket path has at most 107 bytes. The paths leave 57 of them for
  `XDG_RUNTIME_DIR`, and `eis open` fails with `internal` and a message that
  says so when it is longer.
- The socket is removed when you close it, when the window closes, and when the
  plugin unloads. There is no network listener.
- Anyone who can connect to a socket can control that window, including typing
  commands into a terminal. Treat access to the socket like access to the
  application itself. This matches `hyprctl`, which already needs the same user.
- A driven window cannot take over your real input. The plugin invalidates the
  button serials it generates, so they cannot start interactive moves or
  resizes. For a driven window that is not focused, it also suppresses popup
  grabs and cancels drag-and-drop. The popups themselves still open.
- An app cannot take your focus by opening a window in response to emulated
  input: the window opens silently on the workspace of the window it came from,
  and is only marked urgent when it asks for activation. See
  [New windows](#new-windows).
- An X11 app with a driven window cannot take focus while you are in another
  app: the plugin ignores its activation requests and does not focus the
  windows it maps. For xwayland-satellite, and for XWayland apps that do not
  report their process, the plugin cannot tell the apps of one X server apart:
  it ignores activation requests from all of them, but focuses their new
  windows as usual, so apps you start still get the focus.
- X11 has no isolation between the apps of one X server. Any X11 app can
  already read and inject input into the others, so these protections do not
  extend to other X11 apps of the target's X server.
- A lease ends the moment the real keyboard focus moves to the leased window's
  Wayland connection, so it never holds focus that your typing would need.
- Screenshots are written to the path you give, with the permissions of the user
  running Hyprland.

## Limitations

- X11 input is refused while another window of the target's X server has the
  focus, unless the socket was opened with `force`.
- X11 menus and tooltips are separate windows. Screenshots of the app window do
  not include them, and pointer input only reaches the parts of a menu that lie
  over the window.
- Pointer input cannot reach the parts of a Wayland popup that lie above or
  left of its window, since libei regions cannot start at negative
  coordinates. Screenshots with `popups` show them.
- X11 apps that only accept focus through `WM_TAKE_FOCUS`, such as some Java
  applications, may ignore emulated keys.
- `type` and `text` use the first layout of a keymap with several layouts, such
  as `us,de`.
- Characters that need a dead key rely on the target application to compose
  them. Some X11 applications do not. Applications that read a Compose file of
  their own, such as one named by `XCOMPOSEFILE` in their environment only, or
  GTK's `~/.config/gtk-4.0/Compose`, may compose them differently from the
  table the sender uses. The windows of xwayland-satellite all report the
  satellite's process, so their locale is not known.
- Reconnect senders after changing the keyboard layout or keymap.
- Held keys, key repeat, drags and clicks with modifiers are unreliable when
  the target shares a Wayland connection with the focused window or with the
  window under the real pointer. Shortcuts there may do nothing when you type
  or click in the same app while they settle, or when the app takes longer
  than a quarter of a second to run them.
- Screenshot paths cannot contain whitespace.
- Applications that ignore frame callbacks while hidden, or redraw slowly, may
  show stale content in screenshots. A wait cannot tell a redraw that has not
  started yet from one that is done. `starved` covers only windows that have
  got no frame callbacks from hyprmasq yet.
- `resizing` misses an application that acknowledges a new size and commits a
  frame of the old size before it draws one of the new size.
- libeis can close a sender's connection without reporting it. The plugin
  then notices the sender's exit by a failed ping, and releases what it held
  1 to 2 seconds late.
- Through `hyprctl`, a wait answers within 4.5 seconds, the limit of
  `hyprctl`. Clients of Hyprland's socket can wait up to 60 seconds with
  `wait … since`.
- A leased window keeps its emulated focus while the session is locked. Your
  real keys still go to the lock screen.
- `hyprmasq-launch` cannot find windows that an already running instance
  opens.
- Placing [new windows](#new-windows) does not count scrolling or pointer
  motion as your input, so that hovering over your own window of a driven app
  does not take the agent's windows. A window that an app creates without a
  parent more than 5 seconds after emulated input opens as usual. A window that
  another app makes a driven app open, such as a link you open from a terminal
  or your mail in a driven browser, lands next to the driven window when input
  reached the browser in the last 5 seconds and your own input to it is older:
  the plugin cannot tell it from a popup. Your shortcut, or Enter that follows
  no typing, such as after a pause of 2 seconds or after Tab, counts as input
  that can open a window, so it takes a parentless window that the agent's
  click before it opens in the same app, when it comes before the app creates
  the window. Enter right after typing counts as a line break also where it
  runs what you typed, so a window that it opens in a driven app, such as
  Shift+Enter in Chromium's address bar or a command in a terminal server's
  window, opens next to the agent's window when the agent's click or shortcut
  in that app came after your last click or shortcut there.
- The plugin uses Hyprland internals. Other Hyprland versions need a port and a
  passing VM test.
