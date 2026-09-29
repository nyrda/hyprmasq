# Development

Run `./dev.sh` from the repository root.

| Command | Purpose |
| --- | --- |
| `./dev.sh build` | Build `hyprmasq.so` and `hyprmasq-input` inside the flake's dev shell. This is the default command. |
| `./dev.sh vm-test` | Run the headless NixOS VM integration test. |
| `./dev.sh vm-test-interactive` | Open the test's interactive driver. |

The VM commands need Nix and KVM, and pass extra arguments to `nix-build`. The
same test is the flake check: `nix build .#checks.x86_64-linux.vm-test`.

New files must be tracked by git (`git add`) before flake builds can see them.

To try a build in a running session, load it by absolute path:

```sh
hyprctl plugin load "$PWD/hyprmasq.so"
hyprctl plugin unload "$PWD/hyprmasq.so"
```

Unloading removes every open socket and every function hook.

## Tests

`nix/hyprmasq-vm-test.nix` boots NixOS with Hyprland on a headless 640×480
output, installs the plugin through the NixOS module, and runs
`nix/vm-test.py`:

| Subtest | Checks |
| --- | --- |
| Screenshots | Red, green and blue foot windows on hidden workspaces have the right colors, and the first text row is at the top. |
| Background encoding | While a screenshot is written to a FIFO that nobody reads yet, Hyprland answers other requests; the reply comes once the file is read. A `--batch` with two screenshots and another command answers all three, in plain text and JSON. |
| Screenshot regions | A region at the top left of a green `foot` window contains its text and regions at the bottom and right do not; the reply reports the scale and the region, cut to the window at its edge; invalid regions fail without writing a file. At scale 2 the same logical region has twice the pixels, and at scale 1.25 it widens to whole device pixels and reports the region captured. |
| Socket lifecycle | Paths, `0700` directories and a `0600` socket; `eis list`; removal on `eis close` and when the window closes. |
| Hidden shell input | Typing into a foot shell on a hidden workspace runs the command, the active window and workspace stay unchanged, and the window repaints. |
| Typing | Under a German keymap, `type` into a hidden shell produces `zy@€äß|{^`, a shortcut on a key that the layout moves works, named keys, keysyms, repeat counts and `<settle>` edit the line, and escaped and unknown tags are typed as text. Invalid text fails before any key is sent. |
| Pointer events | `wev` on a hidden workspace receives enter, motion, button and axis events. |
| Shared connection | Two `footclient` windows of one `foot --server`: the hidden one receives the text, the focused one receives nothing and stays focused. |
| Protections | `nix/focus-probe.c`, a small xdg-shell client, answers an emulated click with a popup grab, a drag and an interactive move and resize. None of them take the real focus or change the window. |
| XWayland screenshots | A red `xterm` on a hidden workspace has the right color and orientation. |
| XWayland shell input | Typing into a hidden `xterm` runs the command while a Wayland window stays active. |
| XWayland pointer events | `xev` on a hidden workspace receives motion, clicks and scrolling at a point where a visible X11 window is stacked above it; the visible one receives no clicks. |
| XWayland focus rule | Input is refused while another X11 window is active and the active window stays untouched; the active window itself can be driven; `force` drives the hidden one. |
| X11 activation | A driven X11 app cannot activate itself; once its socket is closed, it can. |
| X11 active window | While a Wayland window stays active, `_NET_ACTIVE_WINDOW` is written again after typing into a hidden `xterm`, after clicking it, and after it asks to be activated. |
| xwayland-satellite | Screenshots and input for a hidden satellite `xterm`; refused while another satellite window is active, allowed for Hyprland's XWayland windows; `force`; no satellite app can activate itself while a satellite window is driven, but new satellite windows still get focus. |
| Virtual cursor | A moved pointer shows a light blue cursor on the monitor, a click a ripple beside it; both fade out, and input during the fade restores the cursor. A shorter `virtual_cursor_timeout` makes it fade sooner. Nothing is drawn for a window on a hidden workspace. Changing `virtual_cursor_color` recolors it, `virtual_cursor = false` hides it. |
| Unload and reload | Unloading removes all sockets; the plugin loads again. Unloading while a screenshot is being encoded waits for it and answers it. The batch command works without the plugin, and the plugin can be unloaded from within a batch. |
| Compositor log | After Hyprland exits and flushes its log, it shows that the move and resize serials were rejected. |

The test copies the screenshots and logs into the build result, for example
`result/hyprmasq-green.png` after `nix-build nix/hyprmasq-vm-test.nix`.

## Source layout

| Path | Contents |
| --- | --- |
| `src/main.cpp` | Plugin entry points: registers the hyprctl command, installs and removes hooks. |
| `src/Commands.cpp` | Parses `hyprctl hyprmasq ...` and formats plain and JSON replies. |
| `src/Screenshot.cpp` | Offscreen window rendering and PNG output. |
| `src/Deferred.cpp` | Background work for hyprctl replies, including the wrapper around hyprctl's batch command. |
| `src/Eis.cpp` | Per-window EIS sessions, input delivery, frame callbacks, the X11 focus rule, and the popup-grab, drag and X11 activation hooks. |
| `src/Cursor.cpp` | Virtual cursors and click ripples: recolors the cursor theme's arrow, draws and damages them, and their options. |
| `src/X11.cpp` | X input focus and stacking around input to XWayland windows, the window manager hooks, xwayland-satellite detection. |
| `src/Hooks.cpp` | Installing and removing Hyprland function hooks. |
| `src/Windows.cpp` | Window address formatting and lookup. |
| `tools/hyprmasq-input.c` | The libei sender command. |
| `nix/package.nix` | Plugin and tool derivation, for the flake and downstream `callPackage`. |
| `nix/module.nix` | NixOS module: matching Hyprland, `hyprmasq-input`, Lua loader. |
| `nix/hyprmasq-vm-test.nix`, `nix/vm-test.py` | The VM test. |
| `nix/focus-probe.c`, `nix/focus-probe.nix` | Test client for the protections. |

The code follows Hyprland's `.clang-format`.
