# hyprmasq

**Computer use for Hyprland that stays out of your way.**

> This is the `hyprland-0.56` branch: hyprmasq for **Hyprland 0.56.2**. It gets
> the fixes and MCP changes of `main` while people move to Hyprland 0.57, and
> then ends. [`main`](https://github.com/nyrda/hyprmasq) targets the latest
> stable Hyprland.

hyprmasq is a Hyprland plugin for desktop automation and computer use, by
scripts or AI agents. Agents can use it to assist you, check their own UI
work, or automate desktop tasks. It acts on windows even when they are not
visible, while you keep working in yours. Agents connect through an MCP
server, and scripts use its command-line tools.

## Features

**Background capture.** Screenshots of a single window, rendered offscreen,
whether or not its workspace is visible. Wayland popups and menus that reach
past the window are included. Regions use the same coordinates as input. No
screen recording, no switching workspaces.

**Your focus does not get disturbed.** Each window gets its own
[libei](https://gitlab.freedesktop.org/libinput/libei) input socket, and
events go straight to that window. Your cursor, keyboard focus and active
workspace stay as they are, and a driven app cannot grab your pointer or
keyboard while you work elsewhere. Dialogs and popups that the agent's input
opens land on the agent's workspace. On visible workspaces, a light-blue copy
of the cursor shows where emulated input lands.

**Agent computer use.** `hyprmasq-mcp` gives Claude Code, Codex, pi and any
other MCP client six tools. One `act` call clicks and types, waits until the
window has drawn the result, and returns only what changed, which saves input
tokens and tool calls.

**Demo recordings.** Asked to prove a change in a video, as for a pull
request, an agent records its `act` calls as slides or as video, with the
pointer, clicks and keys drawn in and a caption per step, also in windows on a
hidden workspace. See [Recording a demo](docs/mcp.md#recording-a-demo).

**A scriptable automation layer.** `hyprctl hyprmasq …`, `hyprmasq-input` and
`hyprmasq-launch` make the same capabilities usable from shell scripts and
tests.

## Quick start

On NixOS, add the flake input, pass `inputs` through `specialArgs`, and
enable the module. It installs the plugin, the tools and the exact Hyprland
build the plugin was compiled against:

```nix
# flake.nix: inputs.hyprmasq.url = "github:nyrda/hyprmasq/hyprland-0.56";
{ inputs, ... }: {
  imports = [ inputs.hyprmasq.nixosModules.default ];
  programs.hyprmasq.enable = true;
}
```

On Arch Linux and Omarchy, build a pacman package from a clone, against the
installed Hyprland, and build it again with `makepkg -sif` after each
Hyprland update:

```sh
git clone -b hyprland-0.56 https://github.com/nyrda/hyprmasq && cd hyprmasq && makepkg -si
```

Both install a loader for the plugin. Load it from `hyprland.lua`:

```lua
-- hyprland.lua
dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")
```

Register the MCP server with your agent, for example Claude Code:

```sh
claude mcp add --scope user hyprmasq -- hyprmasq-mcp
```

Or drive a window from a script:

```sh
addr=$(hyprmasq-launch -- foot)                        # start foot on a hidden workspace
socket=$(hyprctl hyprmasq eis open "$addr")            # give it an input socket
hyprmasq-input --socket "$socket" type 'echo hello<enter>'
hyprctl hyprmasq wait "$addr"                          # until foot has drawn the result
hyprctl hyprmasq screenshot "$addr" /tmp/foot.png      # render just that window
hyprctl hyprmasq eis close "$addr"
```

[Installation](docs/installation.md) also covers what the Arch package
installs and how it follows Hyprland updates, other Nix setups, a manual
build, hyprpm, hyprlang configuration, and Codex and pi.

## How it works

The plugin runs inside Hyprland. For a screenshot, it renders the window
into an offscreen buffer the way Hyprland would draw it, so the window's
workspace does not need to be visible. For input, it opens a libei socket
for the window and delivers each event to that window's surfaces only,
without moving the real cursor or changing Hyprland's focus. While they are
driven, hidden windows get the frame callbacks they need to keep drawing, and
are not told that they are suspended, which stops some apps from drawing at
all.
`hyprmasq-input` paces its input like a person's, and `hyprmasq-mcp` builds
on the same commands and compares screenshots to find what changed.

## Status and requirements

Three models passed 213 of 246 [benchmark runs](docs/benchmarks.md#results) on
eleven desktop tasks, on one build with Hyprland 0.57.0, and the user's focus
and workspace stayed unchanged in all of them. Every failed run was the
model's: hyprmasq delivered its input where the agent aimed.

- Built and tested against exactly **Hyprland 0.56.2**. A Hyprland plugin must
  be compiled against the exact Hyprland build that loads it.
- Tested on x86_64 Linux, with a NixOS VM test that covers the plugin, the
  tools and the MCP server.
- The Nix flake and NixOS module are the primary install path. On Arch and
  Omarchy, the repository's `PKGBUILD` builds a pacman package. Manual builds
  and hyprpm are supported.
- Wayland windows are the main target. XWayland and xwayland-satellite
  windows work with [caveats](docs/automation.md#x11-windows).

## Trust model

Anyone who can connect to a window's socket controls that window. Sockets
live in a per-user runtime directory with mode `0700`. A socket exists only
after `hyprctl hyprmasq eis open`, which `hyprmasq-mcp` also runs for every
window it captures or acts on, and stays open until it is closed or the
window closes. hyprmasq is not a security boundary between you and the
agent: it keeps the agent out of your way, not out of your session. Give it
only to agents you would trust with your shell. See the
[security model](docs/automation.md#security-model).

## Documentation

| Document | Contents |
| --- | --- |
| [Installation](docs/installation.md) | NixOS module, Nix packages, Arch and Omarchy, manual build, hyprpm, loading the plugin, registering the MCP server, troubleshooting |
| [MCP server](docs/mcp.md) | `hyprmasq-mcp`: tools, shots and coordinates, `act`, the focus lease, failures, options, client notes |
| [Automation](docs/automation.md) | `hyprctl hyprmasq` commands, waiting, `hyprmasq-input`, `hyprmasq-launch`, configuration, security model, limitations |
| [Development](docs/development.md) | Building, tests, contributing, source layout |
| [Benchmarks](docs/benchmarks.md) | How agents are measured with hyprmasq, and benchmark results |
| [Comparisons](docs/comparison.md) | hyprmasq and Cua Driver on an Omarchy laptop, and Cua Driver and OpenAI's computer use on macOS |

## License

[MIT](LICENSE)
