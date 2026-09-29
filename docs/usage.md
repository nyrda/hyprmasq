# Usage

Every command takes a window address: the `address` field from
`hyprctl -j clients`, for example `0x55d1c0a3e4b0`.

```sh
hyprctl -j clients | jq -r '.[] | "\(.address)  \(.workspace.id)  \(.class)  \(.title)"'
```

## Commands

| Command | Result |
| --- | --- |
| `hyprctl hyprmasq screenshot <addr> <path.png>` | Renders the window to a PNG at `path.png`, which must be absolute. Prints `ok`. |
| `hyprctl hyprmasq eis open <addr> [force]` | Creates the window's input socket and prints its path. Opening it again returns the same path. `force` drives an X11 window even while another window of its X server has the focus; see [X11 windows](#x11-windows). Each `open` sets or clears it. |
| `hyprctl hyprmasq eis close <addr>` | Removes the socket and disconnects its client. Prints `ok`. |
| `hyprctl hyprmasq eis list` | Prints one `address path` line per open socket, followed by `force` for sockets opened with `force`. |
| `hyprctl hyprmasq help` | Prints the command summary. |

Errors print `error: <message>`. `hyprctl` itself exits successfully either way,
so check the output in scripts.

With `hyprctl -j`, every command answers in JSON:

| Command | JSON reply |
| --- | --- |
| `screenshot` | `{"ok":true,"path":"/tmp/w.png","width":640,"height":480}` |
| `eis open` | `{"ok":true,"path":"/run/user/1000/hyprmasq/<instance>/<addr>.eis"}` |
| `eis close` | `{"ok":true}` |
| `eis list` | `[{"address":"0x...","path":"...","force":false}]` |
| any error | `{"ok":false,"error":"..."}` |

Screenshots are taken at the monitor's scale, so a 640×480 window on a 2× monitor
gives a 1280×960 PNG. They show the window's own content, without borders,
shadows or the cursor. Wayland popups are included. X11 menus are separate
windows, so a screenshot of an X11 window does not show them.

Hyprland only renders the window. The PNG is encoded and written on a
background thread while Hyprland keeps running, and `hyprctl` answers once the
file is complete, also within `hyprctl --batch`. The plugin compresses for speed
rather than size: a 2560×1440 window takes roughly 30 to 130 ms, depending on
the content.

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
| `type TEXT` | Type printable ASCII, tab and newline, assuming a US layout. |
| `sleep MS` | Wait between actions. |

Buttons are `left`, `right`, `middle`, or an evdev code such as `0x113`. Key
codes are listed in
[`linux/input-event-codes.h`](https://github.com/torvalds/linux/blob/master/include/uapi/linux/input-event-codes.h).

```sh
socket=$(hyprctl hyprmasq eis open "$addr")

# Click a button at 120,40, then type into the field that got focus.
hyprmasq-input --socket "$socket" move 120 40 click type 'hello world' key 28

# Right-click, wait for the menu, pick its first entry with the keyboard.
hyprmasq-input --socket "$socket" move 200 200 click right sleep 200 key 108 key 28

# Drag from one point to another.
hyprmasq-input --socket "$socket" move 50 50 down left move 300 50 up left
```

### A screenshot and input loop

```sh
addr=0x55d1c0a3e4b0
socket=$(hyprctl hyprmasq eis open "$addr")

hyprctl hyprmasq screenshot "$addr" /tmp/before.png
hyprmasq-input --socket "$socket" type 'ls -la' key 28
sleep 0.3                                 # give the application time to redraw
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
- `type` assumes a US layout, whatever keymap Hyprland uses. For other layouts,
  use `key` or a libei client that reads the advertised keymap.
- Reconnect senders after changing the keyboard layout or keymap.
- Held keys, key repeat and drags are unreliable when the target shares a
  Wayland connection with the focused window.
- Screenshot paths cannot contain whitespace.
- Applications that ignore frame callbacks while hidden, or redraw slowly, may
  show stale content in screenshots.
- The plugin uses Hyprland internals. Other Hyprland versions need a port and a
  passing VM test.
