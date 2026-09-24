# hyprmasq

Per-window screenshots and libei input for **Hyprland 0.56.2**. Drive a Wayland window on your own session, including a hidden workspace, without changing the active window or workspace.

Build against the exact Hyprland package used by your compositor. The flake pins nixpkgs and libei 1.6.0 for development and VM tests.

```sh
nix develop -c make -j8
hyprctl plugin load "$PWD/hyprmasq.so"
hyprctl -j clients

# Use an address from the clients output.
addr=0x12345678
hyprctl hyprmasq screenshot "$addr" /tmp/window.png
socket=$(hyprctl hyprmasq eis open "$addr")
./hyprmasq-input --socket "$socket" move 100 80 click
./hyprmasq-input --socket "$socket" type 'echo ok > /tmp/marker' key 28
./hyprmasq-input --socket "$socket" scroll 0 40
hyprctl hyprmasq eis list
hyprctl hyprmasq eis close "$addr"
```

`eis open` prints the socket path. Reopening an existing session returns the same path. `eis list` prints one address and path per line. Use `hyprctl -j hyprmasq ...` for JSON. Open returns `{"ok":true,"path":"..."}`, list returns an array of objects with `address` and `path`, and errors return `{"ok":false,"error":"..."}`. Check the response body for command errors.

The sender accepts actions in order:

| Action | Meaning |
| --- | --- |
| `move X Y` | Absolute coordinates in window-local logical pixels |
| `click [BUTTON]` | Press and release, default `left` |
| `down BUTTON`, `up BUTTON` | Hold or release a button |
| `scroll DX DY` | Smooth scroll deltas |
| `key CODE` | Press and release a Linux evdev key code, such as `28` for Enter |
| `type TEXT` | Printable ASCII using the US keyboard layout |
| `sleep MS` | Wait between actions |

Buttons accept `left`, `middle`, `right`, or an evdev code. Other libei sender clients can connect to the same socket. The server offers an absolute pointer, buttons, scrolling, and a keyboard with the active seat keyboard's keymap. Input is delivered when the sender finishes each EIS frame. Pointer coordinates are clamped to the window, then resolved to its subsurfaces and popups. Resizing the window replaces the pointer device with a region of the new size.

Sockets exist only after an explicit `eis open` request, at:

```text
$XDG_RUNTIME_DIR/hyprmasq/$HYPRLAND_INSTANCE_SIGNATURE/<addr>.eis
```

Both plugin directories must be owned by the session user and have mode `0700`. Directory symlinks are rejected. The socket has mode `0600`; libeis also creates a lock file. Closing the session, unmapping or destroying the window, or unloading the plugin removes the socket and lock file. Only one sender may be connected to a window's socket at a time. Receiver clients and additional senders are rejected. There is no TCP listener.

Anyone who can connect as the session user can control the selected window, including running commands in a terminal. Treat socket access as authority to use that application. This is a local automation tool for trusted applications, not a sandbox for hostile Wayland clients. Screenshots write to the absolute path you supply, with the compositor user's permissions.

Input goes directly to the target client's Wayland seat resources. The plugin does not move the real cursor or set compositor focus. It clears emulated pointer-button serials so they cannot authorize interactive window moves or resizes. For an open target that is not the real focused window, it suppresses popup grabs and cancels drag-and-drop requests. Loading fails if the required Hyprland hooks cannot be installed.

When the target shares a Wayland connection with the real focused surface, as with `foot --server`, the plugin temporarily switches the client's resource focus, delivers one frame, and restores the real surface before returning to the compositor event loop. Restoration includes real pressed keys and modifiers. Held keys, key repeat, and drags do not survive these switches reliably. The client's ordinary intra-window button interactions still work. A temporary enter is otherwise released after about one second of inactivity when no keys or buttons are held.

Hidden windows receive frame callbacks for about one second after input, including their subsurfaces and popups. This lets applications repaint for subsequent screenshots. Applications that delay updates longer or stop rendering when hidden may still show stale content. A screenshot by itself does not wait for the application to redraw.

X11/XWayland windows are unsupported. An active seat keyboard with a keymap is required. Reconnect senders after changing the seat keyboard layout or keymap. `type` assumes US ASCII regardless of the advertised keymap; use a libei client that interprets the keymap for other layouts. Screenshot paths cannot contain whitespace in the current hyprctl command parser. Plugins depend on Hyprland's internal ABI, so rebuilding for a different compositor version also requires a port and verification.

```sh
./dev.sh build
./dev.sh vm-test
./dev.sh vm-test-interactive
# The same VM is exposed as a flake check.
nix build .#checks.x86_64-linux.vm-test
```

The NixOS VM test reads the nixpkgs pin from `flake.lock`. It checks hidden red/green/blue screenshots and orientation, socket lifecycle and permissions, hidden shell input without focus changes, wev pointer events, two footclient windows sharing one connection, and screenshots before and after typing. A small Wayland fixture also requests popup grabs, interactive moves/resizes, and drag-and-drop from a hidden same-client window. Unload/reload checks cover socket and hook teardown.

New files must be tracked before flake builds can see them. The VM needs a working Nix daemon and virtualization support. Test results must come from a completed VM run; compiling the plugin alone does not verify focus safety.
