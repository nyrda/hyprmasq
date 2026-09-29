# Installation

hyprmasq targets **Hyprland 0.56.2** with a Lua configuration. A Hyprland
plugin only loads into the exact compositor build it was compiled against, so
rebuild the plugin whenever Hyprland changes.

| Method | Use it when |
| --- | --- |
| [NixOS module](#nixos-module) | You run NixOS. The flake pins Hyprland and the plugin together. |
| [Nix packages](#nix-packages-without-the-module) | You manage the Hyprland package yourself on Nix. |
| [Manual build](#manual-build) | You want a local `.so`, for development or another distribution. |
| [hyprpm](#hyprpm) | Your distribution provides Hyprland 0.56.2 and its headers. |

Every method produces two things: the plugin `hyprmasq.so`, and the
`hyprmasq-input` command-line client.

## NixOS module

The flake provides the plugin, the matching Hyprland and portal packages, and a
NixOS module for `x86_64-linux`.

```nix
# flake.nix
inputs.hyprmasq.url = "github:nyrda/hyprmasq";
```

Do not add `inputs.nixpkgs.follows = "nixpkgs"` to this input. It would build
the plugin against your system's Hyprland instead of the tested one.

Pass `inputs` through `specialArgs` of `nixosSystem`, then enable the module:

```nix
{ inputs, ... }: {
  imports = [ inputs.hyprmasq.nixosModules.default ];
  programs.hyprmasq.enable = true;
}
```

The module:

- enables `programs.hyprland` and sets its `package` and `portalPackage` to the
  pinned builds, all with `mkDefault`;
- adds the plugin package to `environment.systemPackages`, which puts
  `hyprmasq-input` on your `PATH`;
- writes `/etc/xdg/hypr/hyprmasq_plugin.lua`, which loads the plugin.

Load it from `hyprland.lua`:

```lua
dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")
```

If your configuration sets `programs.hyprland.package` itself, use the flake's
`hyprland` and `xdg-desktop-portal-hyprland` packages there. Updating the
hyprmasq input updates the compositor and the plugin together.

| Module option | Default | Description |
| --- | --- | --- |
| `programs.hyprmasq.enable` | `false` | Install the plugin, `hyprmasq-input`, the loader, and the matching Hyprland. |
| `programs.hyprmasq.package` | the flake's plugin | Plugin package. It must be built against `programs.hyprland.package`. |

### Using it together with other plugins

Every loaded plugin must be built against the same Hyprland. When you combine
hyprmasq with other plugins, set `programs.hyprland.package` yourself and build
each plugin against that package with `callPackage`, as described next. That way
no module's default decides which compositor you run.

## Nix packages without the module

```nix
let
  hm = inputs.hyprmasq.packages.${pkgs.stdenv.hostPlatform.system};
in {
  programs.hyprland = {
    enable = true;
    package = hm.hyprland;
    portalPackage = hm.xdg-desktop-portal-hyprland;
  };
  environment.systemPackages = [ hm.hyprmasq ];
  # In your Lua config: hl.plugin.load("${hm.hyprmasq}/lib/hyprmasq.so")
}
```

To build the plugin against your own package set instead, use
`pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" { }` or the flake's
`overlays.default`, which adds `pkgs.hyprmasq`. Your Hyprland must then provide
the same plugin API as 0.56.2.

## Manual build

With Nix:

```sh
git clone https://github.com/nyrda/hyprmasq.git
cd hyprmasq
./dev.sh build
```

This runs `make` inside the flake's dev shell and produces `hyprmasq.so` and
`hyprmasq-input` in the repository root.

Without Nix, install a C++23 compiler, a C compiler, Make, pkg-config, and the
development files for Hyprland 0.56.2 and its dependencies, libei 1.x
(`libei-1.0` and `libeis-1.0`), libpng and xkbcommon. Then run `make`.

Load the plugin by absolute path:

```lua
hl.plugin.load("/absolute/path/to/hyprmasq.so")
```

or, for a one-off test in a running session:

```sh
hyprctl plugin load "$PWD/hyprmasq.so"
```

## hyprpm

hyprpm builds plugins against a system-managed Hyprland. For a Nix-managed
Hyprland, use the module or packages above. With the build dependencies from
the manual build installed:

```sh
hyprpm update
hyprpm add https://github.com/nyrda/hyprmasq
hyprpm enable hyprmasq
```

Load enabled plugins at startup:

```lua
hl.on("hyprland.start", function()
    hl.exec_cmd("hyprpm reload -n")
end)
```

hyprpm installs only the plugin. Build `hyprmasq-input` from a clone with
`make hyprmasq-input` and copy it onto your `PATH`, or use any other libei
sender. Run `hyprpm update` after every Hyprland update. The repository has no
compatibility pins for older Hyprland releases.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The plugin fails to load | Rebuild against the running Hyprland. `hyprctl plugin list` shows loaded plugins, and `hyprctl version` the running build. |
| Loading fails with a hook error | The plugin hooks eight Hyprland functions and refuses to load without them. This means the Hyprland build differs from 0.56.2. |
| `hyprctl hyprmasq ...` says the command is unknown | The plugin is not loaded. Check `hyprctl plugin list`. |
| `no mapped window 0x...` | Copy the `address` field from `hyprctl -j clients`, including the `0x`. The window must be mapped. |
| `hyprmasq-input` cannot connect | Use the path that `eis open` printed, as the same user that runs Hyprland. |
| `the server paused input, so some events were dropped` | The target is an X11 window and another window of the same X server has the focus. Focus a Wayland window or the target itself, or reopen the socket with `force`. See [X11 windows](usage.md#x11-windows). If no other X11 window has the focus, XWayland did not answer in time; send again. |
| `the server did not offer a pointer and a keyboard` | Hyprland has no active keyboard with a keymap, which is normal in some headless setups. |
| Input arrives but the screenshot looks unchanged | Some applications take longer than a second to redraw, or stop rendering while hidden. Take the screenshot again after a short wait. |
