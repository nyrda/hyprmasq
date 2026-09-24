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
| `hyprctl hyprmasq eis open <addr>` | Creates the window's input socket and prints its path. Opening it again returns the same path. |
| `hyprctl hyprmasq eis close <addr>` | Removes the socket and disconnects its client. Prints `ok`. |
| `hyprctl hyprmasq eis list` | Prints one `address path` line per open socket. |
| `hyprctl hyprmasq help` | Prints the command summary. |

Errors print `error: <message>`. `hyprctl` itself exits successfully either way,
so check the output in scripts.

With `hyprctl -j`, every command answers in JSON:

| Command | JSON reply |
| --- | --- |
| `screenshot` | `{"ok":true,"path":"/tmp/w.png","width":640,"height":480}` |
| `eis open` | `{"ok":true,"path":"/run/user/1000/hyprmasq/<instance>/<addr>.eis"}` |
| `eis close` | `{"ok":true}` |
| `eis list` | `[{"address":"0x...","path":"..."}]` |
| any error | `{"ok":false,"error":"..."}` |

Screenshots are taken at the monitor's scale, so a 640×480 window on a 2× monitor
gives a 1280×960 PNG. They show the window's own content, without borders,
shadows or the cursor.

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
removed and re-added device. Only one sender can be connected to a socket at a
time, and receiver-mode clients are rejected.

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
- Screenshots are written to the path you give, with the permissions of the user
  running Hyprland.

## Limitations

- X11 and XWayland windows are not supported.
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
