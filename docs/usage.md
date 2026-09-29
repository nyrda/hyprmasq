# Usage

Every command takes a window address: the `address` field from
`hyprctl -j clients`, for example `0x55d1c0a3e4b0`.

```sh
hyprctl -j clients | jq -r '.[] | "\(.address)  \(.workspace.id)  \(.class)  \(.title)"'
```

## Commands

| Command | Result |
| --- | --- |
| `hyprctl hyprmasq screenshot <addr> <path.png> [X Y WIDTH HEIGHT]` | Renders the window, or the given region of it, to a PNG at `path.png`, which must be absolute. Prints `ok`. See [Regions](#regions). |
| `hyprctl hyprmasq wait <addr> [QUIET-MS [TIMEOUT-MS]]` | Answers once the window has settled, or when the timeout passes. Prints `settled` or `timeout`. See [Waiting for a redraw](#waiting-for-a-redraw). |
| `hyprctl hyprmasq eis open <addr> [force]` | Creates the window's input socket and prints its path. Opening it again returns the same path. `force` drives an X11 window even while another window of its X server has the focus; see [X11 windows](#x11-windows). Each `open` sets or clears it. |
| `hyprctl hyprmasq eis close <addr>` | Removes the socket and disconnects its client. Prints `ok`. |
| `hyprctl hyprmasq eis list` | Prints one `address path` line per open socket, followed by `force` for sockets opened with `force`. |
| `hyprctl hyprmasq help` | Prints the command summary. |

Errors print `error: <message>`. `hyprctl` itself exits successfully either way,
so check the output in scripts.

With `hyprctl -j`, every command answers in JSON:

| Command | JSON reply |
| --- | --- |
| `screenshot` | `{"ok":true,"path":"/tmp/w.png","width":1280,"height":960,"scale":2,"region":{"x":0,"y":0,"width":640,"height":480}}` |
| `wait` | `{"ok":true,"settled":true,"waited":230,"frames":0,"framesSinceInput":3}` |
| `eis open` | `{"ok":true,"path":"/run/user/1000/hyprmasq/<instance>/<addr>.eis"}` |
| `eis close` | `{"ok":true}` |
| `eis list` | `[{"address":"0x...","path":"...","force":false}]` |
| any error | `{"ok":false,"error":"..."}` |

Screenshots are taken at the monitor's scale, so a 640×480 window on a 2× monitor
gives a 1280×960 PNG. They show the window's own content, without borders,
shadows or the cursor. Wayland popups are included. X11 menus are separate
windows, so a screenshot of an X11 window does not show them.

### Regions

`X Y WIDTH HEIGHT` limits the screenshot to a rectangle of the window, in the
same window-local logical pixels as `hyprmasq-input`. The values are whole
numbers. A region that extends past the window is cut to it, and one that
starts outside it is an error. The region is still read at the monitor's scale,
so a small region shows small text at full detail.

The JSON reply names the monitor `scale` and the `region` that was captured,
also for a whole window. A pixel at PX,PY of the PNG is at
`region.x + PX / scale`, `region.y + PY / scale` for `move`. At fractional
scales the region is widened to whole device pixels, so the captured `region`
can be slightly larger than requested and have fractional values: at scale
1.25, `1 1 10 10` captures 13×13 pixels from 0.8,0.8 with a size of 10.4.

```sh
hyprctl -j hyprmasq screenshot "$addr" /tmp/toolbar.png 0 0 400 60
```

Hyprland only renders the window. The PNG is encoded and written on a
background thread while Hyprland keeps running, and `hyprctl` answers once the
file is complete, also within `hyprctl --batch`. The plugin compresses for speed
rather than size: a 2560×1440 window takes roughly 30 to 130 ms, depending on
the content.

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
drew before it was called. The JSON reply says how long it `waited`, how many
`frames` the window drew in that time, and, for a window with an open socket,
`framesSinceInput`: the frames since the last input through the socket. An
application often draws the result of input before `wait` is called, so
`framesSinceInput` tells whether input changed anything and `frames` does not.
It is `null` without a socket.

`hyprctl` gives up on a reply after 5 seconds, so TIMEOUT-MS is at most 4500.
Call `wait` again for longer waits. `wait` only works through `hyprctl`, not
through `HyprlandAPI::invokeHyprctlCommand`, because the reply has to wait for
Hyprland's main thread.

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

Hyprland starts the command with a window rule, the way `hl.dsp.exec_cmd` does.
The command gets a random `HYPRMASQ_LAUNCH` token in its environment, and
`hyprmasq-launch` prints the first window whose process has that token. Child
processes inherit it, so wrapper scripts and applications that open their
windows from another process work. Applications that hand the request to an
instance that is already running, such as a `footclient` of a `foot --server`
or a browser that is already open, open the window from a process without the
token. The launch then times out, and the window opens wherever that instance
puts it. Start such applications with an option for a separate instance.

`hyprmasq-launch` needs `hyprctl` and `jq`. The Nix package brings `jq`.

## hyprmasq-input

`hyprmasq-input` connects to a socket, runs a list of actions in order, and
exits once the plugin has processed all of them.

```sh
hyprmasq-input --socket PATH ACTION...
```

| Action | Meaning |
| --- | --- |
| `move X Y` | Move the pointer to X,Y in window-local logical pixels. Values are clamped to the window. |
| `click [BUTTON]` | Press and release a button. The default is `left`. |
| `down BUTTON`, `up BUTTON` | Press or release a button, for drags. |
| `scroll DX DY` | Smooth scroll. Positive DY scrolls down. |
| `key CODE` | Press and release a Linux evdev key code, for example `28` for Enter or `1` for Escape. |
| `type TEXT` | Type text with Hyprland's keymap. Tags such as `<enter>` or `<shortcut:ctrl+l>` in the text press keys; see [Typing text](#typing-text). |
| `sleep MS` | Wait between actions. |

Buttons are `left`, `right`, `middle`, or an evdev code such as `0x113`. Key
codes are listed in
[`linux/input-event-codes.h`](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input-event-codes.h).

```sh
socket=$(hyprctl hyprmasq eis open "$addr")

# Click a button at 120,40, then type into the field that got focus.
hyprmasq-input --socket "$socket" move 120 40 click type 'hello world<enter>'

# Right-click, wait for the menu, pick its first entry with the keyboard.
hyprmasq-input --socket "$socket" move 200 200 click right type '<wait:200><down><enter>'

# Drag from one point to another.
hyprmasq-input --socket "$socket" move 50 50 down left move 300 50 up left
```

### Typing text

`type` presses the keys that produce each character with the keymap of
Hyprland's active keyboard, which the plugin sends to every sender. On a German
layout, `z` presses the key labelled Z there, and `@` holds AltGr. Characters
that only a dead key produces, such as `^` on a German layout, are typed as the
dead key followed by Space or another key, as the locale's Compose table says.
Applications that apply Compose, such as GTK and Qt apps and foot, turn that
into the character. A character that the keymap cannot produce fails the whole
action before any key is sent.

Tags in the text press keys or wait:

| Tag | Result |
| --- | --- |
| `<enter>`, `<tab>`, `<space>`, `<esc>`, `<backspace>`, `<delete>` | Press that key. |
| `<up>`, `<down>`, `<left>`, `<right>`, `<home>`, `<end>`, `<pageup>`, `<pagedown>` | Press that key. |
| `<F5>`, `<Return>`, `<XF86AudioMute>`, … | Press the key for an XKB keysym that does not type a character. |
| `<KEY:COUNT>` | Press a named key or keysym COUNT times, 1 to 100, for example `<down:5>`. |
| `<shortcut:MOD+KEY>` | Hold the modifiers and press KEY, for example `<shortcut:ctrl+l>` or `<shortcut:ctrl+shift+F5>`. The modifiers are `ctrl`, `shift`, `alt` and `super`, with `control`, `meta` and `win` as aliases. KEY is a character, a named key or a keysym. |
| `<wait:MS>` | Wait MS milliseconds. |
| `<settle>` | Wait 300 milliseconds. |

Tags are not case-sensitive. `\<` types a literal `<`. Any other `<…>` that is
not a tag, such as `<div>` or `<b>`, is typed as it is, and other backslashes
are typed too. An invalid tag, such as `<wait:soon>` or `<tab:0>`, fails the
whole action before any key is sent.

```sh
hyprmasq-input --socket "$socket" type 'user<tab>secret<enter>'
hyprmasq-input --socket "$socket" type '<shortcut:ctrl+l>https://example.org<enter>'
hyprmasq-input --socket "$socket" type 'if (a \< b) {<enter>'
```

### A screenshot and input loop

```sh
addr=$(hyprmasq-launch -- foot)
socket=$(hyprctl hyprmasq eis open "$addr")

hyprctl hyprmasq screenshot "$addr" /tmp/before.png
hyprmasq-input --socket "$socket" type 'ls -la<enter>'
hyprctl hyprmasq wait "$addr"             # until the application has drawn the result
hyprctl hyprmasq screenshot "$addr" /tmp/after.png

hyprctl hyprmasq eis close "$addr"
```

### Other libei clients

The socket is a standard EIS endpoint. Any libei sender can connect by passing
the path to `ei_setup_backend_socket()`, or through `LIBEI_SOCKET` for tools
that read it. The plugin offers one seat with:

- an absolute pointer with buttons and scrolling, whose single region is the
  window at its logical size, starting at 0,0;
- a keyboard whose keymap is the keymap of Hyprland's active keyboard.

Input is applied at the end of each libei frame. If the window is resized, the
pointer device is replaced by one with the new size, and clients see it as a
removed and re-added device. Only one sender is connected to a socket at a
time: a new sender replaces the previous one. Receiver-mode clients are
rejected.

## How input is delivered

The plugin sends events directly to the target window's Wayland objects. It does
not move the real cursor or change which window Hyprland considers focused. The
application sees pointer and keyboard focus arrive, receives the events, and
loses that focus again after about a second without input.

When the target belongs to the same program as the window you are using, for
example two windows of one `foot --server`, both windows share one Wayland
connection. The plugin then hands that connection's focus to the target for a
single input frame and restores it to your window before any real input is
handled. Held keys, key repeat and drags do not carry across these frames.

Hidden windows normally stop receiving frame callbacks, so many applications
stop redrawing. After input, the plugin sends a hidden target frame callbacks
for about a second, so the next screenshot shows the result.

## Virtual cursor

Where emulated pointer input lands, the plugin draws a copy of your cursor
theme's arrow in light blue, and a ring that grows and fades at each click. The
cursor stays for 3 seconds after the last pointer event, then fades out.
Keyboard input shows nothing.

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
socket's devices. `hyprmasq-input` then exits with
`the server paused input, so some events were dropped`. The devices resume
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

## Security model

- A socket exists only after an explicit `eis open`. It lives at
  `$XDG_RUNTIME_DIR/hyprmasq/$HYPRLAND_INSTANCE_SIGNATURE/<addr>.eis`. Both
  directories must be owned by you with mode `0700`, and the socket itself is
  `0600`.
- The socket is removed when you close it, when the window closes, and when the
  plugin unloads. There is no network listener.
- Anyone who can connect to a socket can control that window, including typing
  commands into a terminal. Treat access to the socket like access to the
  application itself. This matches `hyprctl`, which already needs the same user.
- A driven window cannot take over your real input. The plugin invalidates the
  button serials it generates, so they cannot start interactive moves or
  resizes. For a driven window that is not focused, it also suppresses popup
  grabs and cancels drag-and-drop. The popups themselves still open.
- An X11 app with a driven window cannot take focus while you are in another
  app: the plugin ignores its activation requests and does not focus the
  windows it maps. For xwayland-satellite, and for XWayland apps that do not
  report their process, the plugin cannot tell the apps of one X server apart:
  it ignores activation requests from all of them, but focuses their new
  windows as usual, so apps you start still get the focus.
- X11 has no isolation between the apps of one X server. Any X11 app can
  already read and inject input into the others, so these protections do not
  extend to other X11 apps of the target's X server.
- Screenshots are written to the path you give, with the permissions of the user
  running Hyprland.

## Limitations

- X11 input is refused while another window of the target's X server has the
  focus, unless the socket was opened with `force`.
- X11 menus and tooltips are separate windows. Screenshots of the app window do
  not include them, and pointer input only reaches the parts of a menu that lie
  over the window.
- X11 apps that only accept focus through `WM_TAKE_FOCUS`, such as some Java
  applications, may ignore emulated keys.
- `type` uses the first layout of a keymap with several layouts, such as
  `us,de`.
- Characters that need a dead key rely on the target application to compose
  them. Some X11 applications do not.
- Reconnect senders after changing the keyboard layout or keymap.
- Held keys, key repeat and drags are unreliable when the target shares a
  Wayland connection with the focused window.
- Screenshot paths cannot contain whitespace.
- Applications that ignore frame callbacks while hidden, or redraw slowly, may
  show stale content in screenshots. `wait` cannot tell a redraw that has not
  started yet from one that is done.
- `wait` answers within 4.5 seconds, the limit of `hyprctl`.
- `hyprmasq-launch` cannot find windows that an already running instance
  opens.
- The plugin uses Hyprland internals. Other Hyprland versions need a port and a
  passing VM test.
