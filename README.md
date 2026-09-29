# hyprmasq

A Hyprland plugin for automating individual windows. It takes screenshots of a
single window and gives each window its own [libei](https://gitlab.freedesktop.org/libinput/libei)
socket for pointer and keyboard input. Both work on windows in hidden
workspaces, and neither changes the window you are working in.

```sh
addr=$(hyprmasq-launch -- foot)                       # start foot on a hidden workspace

hyprctl hyprmasq screenshot "$addr" /tmp/window.png   # render the window to a PNG
socket=$(hyprctl hyprmasq eis open "$addr")           # create its input socket
hyprmasq-input --socket "$socket" type 'echo hello<enter>'
hyprctl hyprmasq wait "$addr"                         # until foot has drawn the result
hyprctl hyprmasq screenshot "$addr" /tmp/after.png
hyprctl hyprmasq eis close "$addr"
```

libei is the standard Linux protocol for emulated input, so any libei sender can
drive the socket. The bundled `hyprmasq-input` covers the common cases: move,
click, scroll, keys, shortcuts and text in the active keyboard layout.

This is a personal plugin. It is built and tested against **Hyprland 0.56.2**
with a Lua configuration. The Nix flake pins the compositor and the plugin
together.

## Features

- **Window screenshots.** Renders one window, or a region of it, offscreen to a
  PNG, whether or not its workspace is visible. Regions use the same
  coordinates as pointer input.
- **A libei socket per window.** Created on request, bound to one window, and
  removed when the window closes. Pointer coordinates are local to the window.
- **Input without focus changes.** Events go straight to the target window. The
  real cursor, the focused window and the active workspace stay as they are.
- **X11 windows too.** XWayland and xwayland-satellite windows can be captured
  and driven while you work in a Wayland window, or with `force`.
- **Your input stays yours.** A window driven through its socket cannot grab
  your real pointer or keyboard with a popup, start a drag, or begin an
  interactive move or resize.
- **Visible activity.** On visible workspaces, a light blue copy of your cursor
  shows where emulated pointer input lands, with a ripple for each click.
- **Hidden windows keep repainting.** After input, hidden windows get frame
  callbacks for a moment, so the next screenshot shows the result. `wait`
  answers once the window has finished drawing.
- **Hidden launches.** `hyprmasq-launch` starts an application on a hidden
  workspace and prints its window's address.
- **An agent skill.** [`skills/hyprmasq`](skills/hyprmasq/SKILL.md) teaches
  coding agents to test GUIs and use applications in the background.

## Install

The plugin must be built against the exact Hyprland build that loads it.
[Installation](docs/installation.md) covers NixOS, a manual build, and hyprpm.
On NixOS, the module installs the matching Hyprland, `hyprmasq-input`,
`hyprmasq-launch`, and a Lua
loader:

```nix
# flake.nix
inputs.hyprmasq.url = "github:nyrda/hyprmasq";
```

```nix
# A NixOS module, with inputs passed through specialArgs.
{ inputs, ... }: {
  imports = [ inputs.hyprmasq.nixosModules.default ];
  programs.hyprmasq.enable = true;
}
```

```lua
-- hyprland.lua
dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")
```

## Documentation

| Document | Contents |
| --- | --- |
| [Installation](docs/installation.md) | NixOS module, Nix packages, manual build, hyprpm, troubleshooting |
| [Usage](docs/usage.md) | hyprctl commands, `hyprmasq-input`, JSON output, security model, limitations |
| [Development](docs/development.md) | Builds, the VM test, source layout |

## Development

```sh
./dev.sh build     # Build hyprmasq.so and hyprmasq-input
./dev.sh vm-test   # Headless NixOS VM integration test
```

## License

[MIT](LICENSE)
