# Installation

hyprmasq 0.1.0 is built and tested against **Hyprland 0.56.2** on x86_64
Linux. A Hyprland plugin must be compiled against the exact Hyprland build
that loads it: hyprmasq compares the build hash when it loads and refuses to
load into any other build. Rebuild the plugin whenever Hyprland changes.

| Method | Use it when |
| --- | --- |
| [NixOS module](#nixos-module) | You run NixOS. The flake pins Hyprland and the plugin together. This is the main, tested path. |
| [Nix packages](#nix-packages-without-the-module) | You manage the Hyprland package yourself on Nix. |
| [Manual build](#manual-build) | You want a local build, for development or another distribution. |
| [hyprpm](#hyprpm) | Your distribution provides Hyprland 0.56.2 and its headers. |

The methods build these files. `make` builds the first four. The Nix
package also includes `hyprmasq-mcp`, which a manual build compiles with Go
(see [Manual build](#manual-build)). hyprpm installs only the plugin.

| File | Purpose |
| --- | --- |
| `hyprmasq.so` | The plugin. |
| `hyprmasq-input` | The libei sender for scripts. |
| `hyprmasq-launch` | Starts an application on a hidden workspace. Needs `hyprctl` and `jq`. |
| `hyprmasq-reaper` | The helper that launched commands run under. The plugin finds it next to `hyprmasq.so`, in `../libexec` relative to it, or on `PATH`. |
| `hyprmasq-mcp` | The [MCP server](mcp.md), built with Go. Included in the Nix package. |

After installing, [load the plugin](#loading-the-plugin) and
[register the MCP server](#mcp-server) with your agents.

## NixOS module

```nix
# flake.nix
inputs.hyprmasq.url = "github:nyrda/hyprmasq";
```

Do not add `inputs.nixpkgs.follows = "nixpkgs"` to this input: the plugin
would then be built against your system's Hyprland instead of the tested one.

Pass `inputs` through `specialArgs` of `nixosSystem`, then enable the module:

```nix
{ inputs, ... }: {
  imports = [ inputs.hyprmasq.nixosModules.default ];
  programs.hyprmasq.enable = true;
}
```

The module:

- enables `programs.hyprland` and sets its `package` and `portalPackage` to
  the flake's pinned builds, with `mkDefault`
- adds the plugin package to `environment.systemPackages`, which puts
  `hyprmasq-input`, `hyprmasq-launch` and `hyprmasq-mcp` on your `PATH`
- writes `/etc/xdg/hypr/hyprmasq_plugin.lua`, which loads the plugin
- asserts that `programs.hyprland.package` is the exact Hyprland build that
  `programs.hyprmasq.package` was built against, and fails evaluation
  otherwise.

| Option | Default | Description |
| --- | --- | --- |
| `programs.hyprmasq.enable` | `false` | Install the plugin, its tools, the loader, and the matching Hyprland. |
| `programs.hyprmasq.package` | the flake's plugin | The plugin package. It must be built against `programs.hyprland.package`. |

Updating the hyprmasq input updates the compositor and the plugin together.

### Your own Hyprland package

If your configuration sets `programs.hyprland.package` itself, for example
because other plugins need a particular build, build hyprmasq against that
package so that the module's assertion passes:

```nix
{ inputs, pkgs, config, ... }: {
  imports = [ inputs.hyprmasq.nixosModules.default ];
  programs.hyprmasq = {
    enable = true;
    package = pkgs.callPackage "${inputs.hyprmasq}/nix/package.nix" {
      hyprland = config.programs.hyprland.package;
    };
  };
}
```

Your Hyprland must then be 0.56.2: the plugin uses Hyprland internals that
other releases change. Every loaded plugin must be built against the same
Hyprland, so build each one against the package you set.

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
  # The plugin is at "${hm.hyprmasq}/lib/hyprmasq.so".
}
```

The flake's `overlays.default` adds `pkgs.hyprmasq`, built against
`pkgs.hyprland` of your package set. `hm.hyprmasq` contains
`bin/hyprmasq-mcp`, a wrapper that runs the MCP server with the package's own
`hyprmasq-input`. The flake's `hyprmasq-mcp` package is the server alone.

## Manual build

With Nix, from a clone:

```sh
git clone https://github.com/nyrda/hyprmasq.git
cd hyprmasq
./dev.sh build                                      # make inside the flake's dev shell
(cd mcp && nix develop .. -c go build ./cmd/hyprmasq-mcp)
```

Without Nix, install a C++23 compiler, a C compiler, Make, pkg-config, and the
development files of Hyprland 0.56.2 and its dependencies, libei 1.x
(`libei-1.0` and `libeis-1.0`), libpng, xkbcommon and xcb. Then:

```sh
make
sudo make install                                   # PREFIX=/usr/local by default
(cd mcp && go build ./cmd/hyprmasq-mcp)             # Go 1.25 or newer
```

`make` builds `hyprmasq.so`, `hyprmasq-input`, `hyprmasq-reaper` and
`hyprmasq-launch` in the repository root. `make install` installs the plugin
to `$PREFIX/lib`, `hyprmasq-input` and `hyprmasq-launch` to `$PREFIX/bin`,
and `hyprmasq-reaper` to `$PREFIX/libexec`, where the plugin finds it. Set
`PREFIX` and `DESTDIR` for a staged install, such as
`make install PREFIX=/usr DESTDIR=/tmp/stage`. Copy `hyprmasq-mcp` onto your
`PATH` yourself, and install `jq` for `hyprmasq-launch`.

For a one-off test in a running session:

```sh
hyprctl plugin load "$PWD/hyprmasq.so"
```

## hyprpm

hyprpm builds plugins against a system-managed Hyprland. For a Nix-managed
Hyprland, use the module or packages above. With the build dependencies of
the manual build installed:

```sh
hyprpm update
hyprpm add https://github.com/nyrda/hyprmasq
hyprpm enable hyprmasq
```

and load enabled plugins at startup, in `hyprland.lua`:

```lua
hl.on("hyprland.start", function()
    hl.exec_cmd("hyprpm reload -n")
end)
```

or in `hyprland.conf`:

```ini
exec-once = hyprpm reload -n
```

hyprpm installs only the plugin. From a clone, run `make` and copy
`hyprmasq-input`, `hyprmasq-launch` and `hyprmasq-reaper` onto the `PATH`
that Hyprland starts with, install `jq`, and build `hyprmasq-mcp` as in the
manual build. Run `hyprpm update` after every Hyprland update. The repository
has no compatibility pins for older Hyprland releases.

## Loading the plugin

Hyprland 0.56.2 supports a Lua configuration, `hyprland.lua`, and the
hyprlang `hyprland.conf`. The plugin works with either, and the VM test uses a
Lua configuration.

**Lua**, with the NixOS module:

```lua
dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")
```

or by absolute path:

```lua
hl.plugin.load("/usr/local/lib/hyprmasq.so")
```

**hyprlang**, by absolute path:

```ini
plugin = /usr/local/lib/hyprmasq.so
```

The NixOS module ships only the Lua loader. For `hyprland.conf` on NixOS,
write the path from the module's package, for example:

```nix
environment.etc."xdg/hypr/hyprmasq_plugin.conf".text =
  "plugin = ${config.programs.hyprmasq.package}/lib/hyprmasq.so";
```

and add `source = /etc/xdg/hypr/hyprmasq_plugin.conf` to `hyprland.conf`.

The plugin's options are listed in [Configuration](automation.md#configuration).

## MCP server

`hyprmasq-mcp` speaks MCP over stdio, so each agent session starts its own
server. It needs:

- the plugin loaded in the Hyprland it drives, with the same interface
  version, which the plugin and server of one release always have
- `XDG_RUNTIME_DIR` and `HYPRLAND_INSTANCE_SIGNATURE` of that Hyprland, which
  it inherits when the agent runs inside the Hyprland session and the client
  passes them on, as Codex does only when told to
- `hyprmasq-input`, from the Nix package's wrapper, from `PATH`, or given
  with `--input`.

Its options, `--input`, `--max-image-size`, `--lease-ms`,
`--new-window-grace-ms`, `--structured-content` and `--version`, are
described in [Command-line options](mcp.md#command-line-options).

**Claude Code:**

```sh
claude mcp add --scope user hyprmasq -- hyprmasq-mcp
```

**Codex**, in `~/.codex/config.toml`:

```toml
[mcp_servers.hyprmasq]
command = "hyprmasq-mcp"
env_vars = ["XDG_RUNTIME_DIR", "HYPRLAND_INSTANCE_SIGNATURE"]
# args = ["--max-image-size", "0"]
# For codex exec with approval_policy = "never":
# default_tools_approval_mode = "approve"
# For launches with timeout_ms above about 50000:
# tool_timeout_sec = 120
```

Codex starts MCP servers with a few variables of its own environment, such as
`HOME` and `PATH`, and not the session's. `env_vars` passes on the two that
name the Hyprland. Without it the server exits at once with
`XDG_RUNTIME_DIR and HYPRLAND_INSTANCE_SIGNATURE must name the Hyprland to
drive`, and Codex shows the model no hyprmasq tools.

Codex asks for approval before calling a tool that is not read-only, and no
hyprmasq tool is read-only: `screenshot` and `windows` open the window's
input socket, which stays open. `codex exec` with
`approval_policy = "never"` cannot ask, and fails those calls with
`MCP tool call requires approval, but approval policy is never`. Set
`default_tools_approval_mode = "approve"` in the table above, or approve
single tools, for example with `approval_mode = "approve"` in
`[mcp_servers.hyprmasq.tools.act]`.

Codex waits 60 seconds for a tool call by default. An `act` ends by its
`deadline_ms`, at most 30 seconds, but a `launch` can take its `timeout_ms`
plus about 6 seconds.

**pi**, in `~/.pi/agent/mcp.json`:

```json
{
  "mcpServers": {
    "hyprmasq": {
      "command": "hyprmasq-mcp",
      "exposure": "direct"
    }
  }
}
```

`direct` exposure gives the model the tools themselves, with their
descriptions and images. pi then shows the model none of the server's
instructions, but the tool descriptions carry every rule, so nothing is lost.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The plugin fails to load with "built for another Hyprland build" | It was compiled against another Hyprland build. Rebuild it against the running one. `hyprctl version` shows that build. |
| Loading fails with a hook error | The plugin was built against a Hyprland other than 0.56.2, whose functions differ. |
| `hyprctl hyprmasq …` says the command is unknown | The plugin is not loaded. Check `hyprctl plugin list`. |
| `no mapped window 0x…` | Copy the `address` field from `hyprctl -j clients`, including the `0x`. The window must be mapped. |
| `hyprmasq-input` cannot connect | Use the path that `eis open` printed, as the same user that runs Hyprland. |
| `eis open` says the socket path is too long | `XDG_RUNTIME_DIR` is longer than the 57 bytes that socket paths leave for it. |
| `the server paused input, so some events were dropped` | You held a mouse button or key for more than a second in a window of the same app (Wayland connection) as the target. Let go and send again. For an X11 target, another window of its X server has the focus: focus a Wayland window or the target, or open the socket with `force` (see [X11 windows](automation.md#x11-windows)). Otherwise XWayland did not answer in time, so send again. `hyprctl hyprmasq state` shows the reason in `lastPause`. |
| `the server did not offer a pointer and a keyboard` | Hyprland has no active keyboard with a keymap, which happens in some headless setups. |
| Input arrives but the screenshot looks unchanged | The application has not drawn yet. Wait with `wait … since` before the screenshot. See [Waiting](automation.md#waiting). |
| A launch times out and the window opens on your workspace | The application handed the request to an instance that was already running, or `hyprmasq-reaper` was not found. Start a separate instance, and check Hyprland's log for a hyprmasq warning. |
| `hyprmasq-mcp` tools fail with `plugin_missing` | The plugin is not loaded, or the server's `HYPRLAND_INSTANCE_SIGNATURE` names another Hyprland. Start the agent inside the Hyprland session. |
| `hyprmasq-mcp` tools fail with `plugin_version` | The plugin and the server come from different releases. Use both from one release, and reload the plugin after updating it. |
| `hyprmasq-input is not on PATH; pass --input` | Use the Nix package's `hyprmasq-mcp` wrapper, put `hyprmasq-input` on `PATH`, or pass `--input`. |
