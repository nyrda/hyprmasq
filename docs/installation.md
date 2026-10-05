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

After installing, [register the MCP server](#mcp-server) with your coding agents.

Every method produces the plugin `hyprmasq.so` and the `hyprmasq-input`
command-line client. The shell script `tools/hyprmasq-launch`, which starts
applications on a hidden workspace, needs no build. The Nix package also
contains `hyprmasq-mcp`, the [MCP server](#mcp-server) for coding agents;
other methods build it with Go.

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
  `hyprmasq-input`, `hyprmasq-launch` and `hyprmasq-mcp` on your `PATH`;
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
| `programs.hyprmasq.enable` | `false` | Install the plugin, `hyprmasq-input`, `hyprmasq-launch`, `hyprmasq-mcp`, the loader, and the matching Hyprland. |
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

`hm.hyprmasq` contains `bin/hyprmasq-mcp`, a wrapper that runs the MCP server
with the package's own `hyprmasq-input`. The flake's `hyprmasq-mcp` package is
the server alone, which runs the `hyprmasq-input` it finds on `PATH`.

## Manual build

With Nix:

```sh
git clone https://github.com/nyrda/hyprmasq.git
cd hyprmasq
./dev.sh build
```

This runs `make` inside the flake's dev shell and produces `hyprmasq.so` and
`hyprmasq-input` in the repository root. Build the MCP server with the Go of
the same shell:

```sh
cd mcp && nix develop .. -c go build ./cmd/hyprmasq-mcp
```

Without Nix, install a C++23 compiler, a C compiler, Make, pkg-config, and the
development files for Hyprland 0.56.2 and its dependencies, libei 1.x
(`libei-1.0` and `libeis-1.0`), libpng and xkbcommon. Then run `make`. For
`hyprmasq-mcp`, install Go 1.26.7 or newer and run
`go build ./cmd/hyprmasq-mcp` in `mcp/`.

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
sender. Copy `tools/hyprmasq-launch` onto your `PATH` too, and install `jq`
for it. For agents, build `hyprmasq-mcp` from the same clone as in the manual
build and copy it onto your `PATH`. Run `hyprpm update` after every Hyprland update. The repository has no
compatibility pins for older Hyprland releases.

## MCP server

`hyprmasq-mcp` gives coding agents computer use through hyprmasq: tools to
list and launch windows, take screenshots, act on a window and wait for its
redraw in one call, and release and close windows. It speaks MCP over stdio,
so each agent session starts its own server. [mcp.md](mcp.md) describes the
tools; agents learn them from the tool descriptions and need no skill. The
descriptions carry every rule an agent must follow, such as to drive only
windows the user named or the agent launched and to ask before anything
irreversible, so clients that do not show the server's instructions lose none
of them.

The server needs:

- the plugin loaded in the Hyprland it drives, with the same interface
  version, which the plugin and server of one package always have;
- `XDG_RUNTIME_DIR` and `HYPRLAND_INSTANCE_SIGNATURE` of that Hyprland, which
  it inherits when the agent runs inside the Hyprland session;
- `hyprmasq-input`, from the package's wrapper, from `PATH`, or given with
  `--input`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--input PATH` | `hyprmasq-input` from `PATH` | The sender to run for each batch of input. |
| `--max-image-size N` | `1280` | Fit screenshots, also zoomed regions, within N pixels on their long side. `0` keeps the monitor's scale. |
| `--lease-ms MS` | `60000` | How long the focus lease lasts that each `act` starts or renews, 1 to 600000. |
| `--version` | | Print the version and exit. |

Register it once per client.

**Claude Code:**

```sh
claude mcp add --scope user hyprmasq -- hyprmasq-mcp
```

**Codex**, in `~/.codex/config.toml`:

```toml
[mcp_servers.hyprmasq]
command = "hyprmasq-mcp"
# args = ["--max-image-size", "0"]
# For codex exec with approval_policy = "never":
# default_tools_approval_mode = "approve"
```

Codex can ask for approval before it calls a tool that is not read-only. Only
`windows` is read-only: `screenshot` opens the window's socket, which stays
open. `codex exec` with `approval_policy = "never"` cannot ask, and fails
those calls with `MCP tool call requires approval, but approval policy is
never`. For it, set `default_tools_approval_mode = "approve"` in the table
above, or approve single tools, for example `approval_mode = "approve"` in
`[mcp_servers.hyprmasq.tools.screenshot]` and
`[mcp_servers.hyprmasq.tools.act]`.

Codex waits 60 seconds for a tool call by default. An `act` call ends at about
its `deadline_ms`, which is at most 30 seconds, but `launch` can take its
`timeout_s` plus about 6 seconds. Set `tool_timeout_sec` in the same table
when you launch applications with a `timeout_s` above 50.

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
descriptions, images and summary lines, instead of hiding them behind
codemode. With it, pi shows the model none of the server's instructions: pi
lists only servers with `codemode` or `deferred` tools in its system prompt,
and codemode scripts read instructions only through `describeNamespace()`.
The model gets the rules from the tool descriptions, which carry all of
them, so nothing else is needed. pi's per-request `timeout` of 60 seconds
is reset by progress notifications, which `hyprmasq-mcp` sends about once a
second during a call.

Several servers can drive one desktop at once. They take turns on each window
through lock files in the plugin's directory for the Hyprland instance,
`$XDG_RUNTIME_DIR/hyprmasq/<instance>/`, see
[Security model](usage.md#security-model).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| The plugin fails to load | Rebuild against the running Hyprland. `hyprctl plugin list` shows loaded plugins, and `hyprctl version` the running build. |
| Loading fails with a hook error | The plugin hooks eleven Hyprland functions and refuses to load without them. This means the Hyprland build differs from 0.56.2. |
| `hyprctl hyprmasq ...` says the command is unknown | The plugin is not loaded. Check `hyprctl plugin list`. |
| `no mapped window 0x...` | Copy the `address` field from `hyprctl -j clients`, including the `0x`. The window must be mapped. |
| `hyprmasq-input` cannot connect | Use the path that `eis open` printed, as the same user that runs Hyprland. |
| `eis open` says the socket path has too many bytes | `XDG_RUNTIME_DIR` is longer than the 57 characters that socket paths leave for it. Use a shorter one. |
| `the server paused input, so some events were dropped` | The target is an X11 window and another window of the same X server has the focus. Focus a Wayland window or the target itself, or reopen the socket with `force`. See [X11 windows](usage.md#x11-windows). If no other X11 window has the focus, XWayland did not answer in time; send again. |
| `the server did not offer a pointer and a keyboard` | Hyprland has no active keyboard with a keymap, which is normal in some headless setups. |
| Input arrives but the screenshot looks unchanged | Some applications take longer than a second to redraw, or stop rendering while hidden. Use `wait` before the screenshot; see [Waiting for a change](usage.md#waiting-for-a-change). |
| `hyprmasq-mcp` tools fail with `plugin_missing` | The plugin is not loaded, or the server's `HYPRLAND_INSTANCE_SIGNATURE` names another Hyprland. Start the agent from inside the Hyprland session. |
| `hyprmasq-mcp` tools fail with `plugin_version` | The loaded plugin speaks another interface than the server, because one of them is older. Use the plugin and `hyprmasq-mcp` of one package, and reload the plugin after updating it. |
| `hyprmasq-input is not on PATH; pass --input` | `hyprmasq-mcp` cannot find the sender. Use the package's `hyprmasq-mcp` wrapper, or pass `--input`. |
