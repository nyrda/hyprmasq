# Installation

hyprmasq 0.3.1 is built and tested against **Hyprland v0.57.2** at the
release commit `flake.lock` pins, and against **v0.56.2**, the release before
it, on x86_64 Linux. Both build from the same source, see
[Older Hyprland releases](#older-hyprland-releases).
A Hyprland plugin must be compiled against the exact Hyprland build
that loads it: hyprmasq compares the build hash when it loads and refuses to
load into any other build. Rebuild the plugin whenever Hyprland changes.

| Method | Use it when |
| --- | --- |
| [Arch and Omarchy](#arch-and-omarchy) | You run Arch Linux or Omarchy. `yay -S hyprmasq` installs the plugin prebuilt for Arch's and Omarchy's Hyprland, and the MCP server. `yay -S hyprmasq-bin` installs the MCP server prebuilt too. |
| [NixOS module](#nixos-module) | You run NixOS. The flake pins Hyprland and the plugin together. This is the main, tested path. |
| [Nix packages](#nix-packages-without-the-module) | You manage the Hyprland package yourself on Nix. |
| [Manual build](#manual-build) | You want a local build, for development or another distribution. |
| [hyprpm](#hyprpm) | Your distribution provides that Hyprland and its headers. |

The methods build these files. `make` builds the first four. The Nix and
Arch packages also include `hyprmasq-mcp`, which a manual build compiles with
Go (see [Manual build](#manual-build)). hyprpm installs only the plugin.

| File | Purpose |
| --- | --- |
| `hyprmasq.so` | The plugin. |
| `hyprmasq-input` | The libei sender for scripts. |
| `hyprmasq-launch` | Starts an application on a hidden workspace. Needs `hyprctl` and `jq`. |
| `hyprmasq-reaper` | The helper that launched commands run under. The plugin finds it next to `hyprmasq.so`, in `../libexec` relative to it, or on `PATH`. |
| `hyprmasq-mcp` | The [MCP server](mcp.md), built with Go. Included in the Nix and Arch packages. Its [recordings](mcp.md#recording-a-demo) need `ffmpeg` with libx264. |

After installing, [load the plugin](#loading-the-plugin) and
[register the MCP server](#mcp-server) with your agents.

## Arch and Omarchy

Install hyprmasq from the AUR with a helper such as yay:

```sh
yay -S hyprmasq                 # or: paru -S hyprmasq
yay -S hyprmasq-bin             # the same, with nothing built on your machine
```

Either installs two packages of the same release:

| Package | Contents | Built |
| --- | --- | --- |
| `hyprmasq` | `hyprmasq-mcp`, `hyprmasq-input`, `hyprmasq-launch` and `hyprmasq-reaper`. | On your machine, from the release's source, with Go. None of them depends on Hyprland's internals. |
| `hyprmasq-bin` | The same programs, in place of `hyprmasq`. | By the project's CI, with the PKGBUILD of `hyprmasq`, on the oldest userland that the plugins run. |
| `hyprmasq-plugin-bin` | The plugin, once for each Hyprland release that hyprmasq targets, and the loader. Both packages above install it. | By the project's CI, for the Hyprland builds that Arch and Omarchy's stable channel ship. |

Nothing compiles the plugin on your machine, so a Hyprland update needs no
rebuild. `hyprmasq` builds the programs once for each hyprmasq release,
which takes about a minute and installs `go` for the build.
`hyprmasq-bin` skips that. Install one of the two. Each replaces the other,
and both replace a [build from a clone](#building-from-a-clone): pacman
offers to remove it.

Then add the loader to `~/.config/hypr/hyprland.lua`, which Omarchy uses:

```lua
pcall(dofile, "/etc/xdg/hypr/hyprmasq_plugin.lua")
```

`pcall` keeps the config working after you remove the package. The loader
loads the plugin for the running Hyprland's version, and shows a
notification instead when the package has none. Hyprland 0.57 reads only
`hyprland.lua`. On Hyprland 0.56.2 with `hyprland.conf`, add
`plugin = /usr/lib/hyprmasq/0.56.2/hyprmasq.so` instead.

Restart Hyprland, or load the plugin into the running session with
`hyprctl plugin load /usr/lib/hyprmasq/<version>/hyprmasq.so`, with the
version that `hyprctl version` shows. Then
[register the MCP server](#mcp-server) with your agents.

`hyprmasq-mcp` runs `hyprmasq-input` from `PATH`. Remove copies from an
earlier manual install, for example in `/usr/local/bin` or `~/.local/bin`,
which come before `/usr/bin` on `PATH`, or pass
`--input /usr/bin/hyprmasq-input`.

### Updates

`yay -Syu` updates the packages with the rest of the system. A hyprmasq
release updates both of yours. An update takes effect when Hyprland loads the plugin
again, at its next start.

A plugin must match the Hyprland build that loads it: hyprmasq compares the
commit that Hyprland was built from, and the major and minor versions of
aquamarine, hyprutils, hyprgraphics, hyprcursor and hyprlang. Each plugin of
`hyprmasq-plugin-bin` has a list of the Hyprland builds that it runs,
`hyprland-builds`, beside it. When Arch or Omarchy's stable channel ships a
new Hyprland build, the project's CI checks within the hour that the plugin
compiles to the same binary against its headers, that the build exports what
the plugin uses, and that the Hyprland sources of the hooked functions did not
change. It then adds the build to the list and publishes the package again.
Arch's mirrors take hours to sync, so `yay -Syu` usually brings the new
Hyprland and the new list together.

When it does not, the plugin refuses to load, before it hooks anything, and
Hyprland runs normally without it. Hyprland shows "[hyprmasq] Not loaded:
this hyprmasq package has no plugin for the running Hyprland build … yet",
and the MCP tools fail with `plugin_missing` and the same reason. Run
`yay -Syu` again later and restart Hyprland. A Hyprland build that needs a new
plugin waits for a hyprmasq release.

The packages cover the `hyprland` packages of Arch and of Omarchy's stable
channel, which also covers Omarchy's edge channel. `hyprland-git` and other
builds of Hyprland need the plugin built against them: build from a clone, as
below.

### What this installs

| File | Package | Purpose |
| --- | --- | --- |
| `/usr/lib/hyprmasq/<release>/hyprmasq.so` | `hyprmasq-plugin-bin` | The plugin for a Hyprland release, such as `0.57.2`. |
| `/usr/lib/hyprmasq/<release>/hyprland-builds` | `hyprmasq-plugin-bin` | The Hyprland builds that this plugin runs. |
| `/usr/lib/hyprmasq/<version>` | `hyprmasq-plugin-bin` | A link to the plugin for each other Hyprland version that one runs. |
| `/etc/xdg/hypr/hyprmasq_plugin.lua` | `hyprmasq-plugin-bin` | The loader. |
| `/usr/lib/hyprmasq/hyprmasq-reaper` | `hyprmasq` or `hyprmasq-bin` | The helper that launched commands run under. The plugin finds it in its parent directory. |
| `/usr/bin/hyprmasq-mcp` | `hyprmasq` or `hyprmasq-bin` | The [MCP server](mcp.md). |
| `/usr/bin/hyprmasq-input` | `hyprmasq` or `hyprmasq-bin` | The libei sender for scripts. |
| `/usr/bin/hyprmasq-launch` | `hyprmasq` or `hyprmasq-bin` | Starts an application on a hidden workspace. |
| `/usr/share/licenses/<package>/LICENSE` | each | The MIT license. |

`pacman -Ql hyprmasq hyprmasq-plugin-bin` lists them, with `hyprmasq-bin` in
place of `hyprmasq` if you installed that. The packages depend on
`hyprland` without pinning its version. A pin would keep `pacman -Syu` from
updating Hyprland until you removed hyprmasq.

### Checking what you install

- Read the PKGBUILDs before you install them. Your AUR helper shows them, and
  `packaging/aur` in the repository holds the same files.
- The plugins and the programs of `hyprmasq-bin` are built by the
  repository's GitHub Actions from the release tag, with public logs, and
  attached to the GitHub release, where they are never replaced.
  `hyprmasq-plugin-bin` and `hyprmasq-bin` pin each by its hash, and
  `hyprmasq` the release's archive. The project does not sign its releases
  yet.
- `go` refuses a module whose download does not match its hash in
  `mcp/go.sum`.

### Building from a clone

The repository's `PKGBUILD` builds one package, `hyprmasq`, with the plugin
compiled against the Hyprland that pacman installed. Use it for a commit that
is not released, or for a Hyprland that `hyprmasq-plugin-bin` does not cover:

```sh
git clone https://github.com/nyrda/hyprmasq && cd hyprmasq && makepkg -si
```

`-s` installs the missing dependencies, such as `go` for the build, and `-i`
installs the package, both through pacman. It replaces the AUR packages, and
pacman offers to remove `hyprmasq-plugin-bin`, and `hyprmasq-bin` if it is
installed. The package has no install
script and no pacman hook. The installed Hyprland must be version 0.57.2 or
0.56.2. Other releases have other internals, and the build stops on their
version.

makepkg builds the commit that is checked out, `HEAD`. It leaves out
uncommitted changes and warns when there are any, so commit a change to build
it. The only downloads are the Go modules of `hyprmasq-mcp`. Go fetches them
through `proxy.golang.org`, or from their repositories with
`GOPROXY=direct makepkg -si`. makepkg builds in `build/makepkg` of the clone,
or under `BUILDDIR` when you set one, and writes the package files to the
clone's root. `pacman -Q hyprmasq` shows a version such as
`0.3.1.r12.g5e0bb2a`: the `VERSION` file, the number of commits and the
commit. The programs report the `VERSION`, here `0.3.1`.

It installs the plugin as `/usr/lib/hyprmasq/hyprmasq.so`, and as the loader
`/etc/xdg/hypr/hyprmasq_plugin.lua` the line
`hl.plugin.load("/usr/lib/hyprmasq/hyprmasq.so")`, which the same `dofile`
line loads. With `hyprland.conf`, use `plugin = /usr/lib/hyprmasq/hyprmasq.so`.

This plugin is compiled against one Hyprland build, so after every update of
the `hyprland` package, rebuild and restart Hyprland:

```sh
git pull && makepkg -sif
```

`-f` builds again when the commit has not changed. Until then, the plugin
refuses to load and says "it was built for another Hyprland build".

### Removing

Run `sudo pacman -R hyprmasq hyprmasq-plugin-bin`, with `hyprmasq-bin` in
place of `hyprmasq` if you installed that, or `sudo pacman -R hyprmasq` for a
build from a clone, with `hyprmasq-debug` if it is installed. The
`pcall` around the loader's `dofile` keeps `hyprland.lua` working without it.

## NixOS module

```nix
# flake.nix
inputs.hyprmasq.url = "github:nyrda/hyprmasq";
```

Do not make this input's `hyprland` input follow another one: the plugin
would then be built against another Hyprland than the tested one.

The pinned Hyprland comes from Hyprland's own flake, prebuilt on
hyprland.cachix.org. Without that cache, Nix compiles Hyprland and its
libraries. Add it to your system:

```nix
nix.settings = {
  extra-substituters = [ "https://hyprland.cachix.org" ];
  extra-trusted-public-keys = [ "hyprland.cachix.org-1:a7pgxzMz7+chwVL3/pzj6jIBMioiJM7ypFP8PwtkuGc=" ];
};
```

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
- writes `/etc/xdg/hyprmasq/mcp.toml` from `programs.hyprmasq.mcpSettings`,
  when it is set
- asserts that `programs.hyprland.package` is the exact Hyprland build that
  `programs.hyprmasq.package` was built against, and fails evaluation
  otherwise.

| Option | Default | Description |
| --- | --- | --- |
| `programs.hyprmasq.enable` | `false` | Install the plugin, its tools, the loader, and the matching Hyprland. |
| `programs.hyprmasq.package` | the flake's plugin | The plugin package. It must be built against `programs.hyprland.package`. |
| `programs.hyprmasq.mcpSettings` | `{ }` | The [settings of `hyprmasq-mcp`](mcp.md#configuration), such as `{ record.gif = true; }`. A user's own `~/.config/hyprmasq/mcp.toml` is read instead. |

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

Your Hyprland must then be the pinned commit: the plugin uses Hyprland
internals that other commits change. Every loaded plugin must be built against the same
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
`hyprmasq-input` and an ffmpeg for its recordings. The flake's `hyprmasq-mcp`
package is the server alone.

## Manual build

With Nix, from a clone:

```sh
git clone https://github.com/nyrda/hyprmasq.git
cd hyprmasq
./dev.sh build                                      # make inside the flake's dev shell
(cd mcp && nix develop .. -c go build ./cmd/hyprmasq-mcp)
```

Without Nix, install a C++23 compiler, a C compiler, Make, pkg-config, and the
development files of Hyprland 0.57.2 or 0.56.2 and their dependencies, libei 1.x
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
`PATH` yourself, and install `jq` for `hyprmasq-launch` and `ffmpeg` with
libx264 for the recordings of `hyprmasq-mcp`.

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
manual build. Run `hyprpm update` after every Hyprland update.

hyprpm builds `main` on Hyprland 0.57.2 and 0.56.2. `commit_pins` in
`hyprpm.toml` give a patch release that `main` moved past the last commit of
`main` for it, so hyprpm on Hyprland 0.57.0 or 0.57.1 builds that commit.

## Older Hyprland releases

`main` builds against the latest stable Hyprland and the release before it,
from the same source, so both get every change. The build picks the code for
the Hyprland whose headers it finds.

| Hyprland | Status |
| --- | --- |
| v0.57.2 | The release `flake.lock` pins. |
| v0.56.2 | The release before it, which Omarchy's stable channel still ships. |

On Hyprland 0.56.2, install as on 0.57.2:

- Arch and Omarchy: nothing to change. `hyprmasq-plugin-bin` has a plugin
  for 0.56.2, and a build from a clone uses the installed Hyprland's headers.
- Manual build and hyprpm: nothing to change. The build uses the headers of
  the installed Hyprland.
- NixOS module: set
  `programs.hyprmasq.package = inputs.hyprmasq.packages.x86_64-linux.hyprmasq-hyprland056;`
  and `programs.hyprland.package = inputs.hyprmasq.packages.x86_64-linux.hyprland-hyprland056;`,
  the plugin and the Hyprland 0.56.2 it is tested with.
- Nix packages: `hyprmasq-hyprland056`, or build `nix/package.nix` against
  your own Hyprland 0.56.2.

`main` drops the older release once neither Arch nor Omarchy's stable channel
ships it. The patch releases that `main` moved past, v0.57.0 and v0.57.1, get
no further changes. Update Hyprland to v0.57.2, or build the
commit that `commit_pins` in `hyprpm.toml` names for your release, as hyprpm
does on its own.

## Loading the plugin

Hyprland supports a Lua configuration, `hyprland.lua`, and the
hyprlang `hyprland.conf`. The plugin works with either, and the VM test uses a
Lua configuration.

**Lua**, with the NixOS module or the [Arch package](#arch-and-omarchy):

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

The Arch packages install the plugin as `/usr/lib/hyprmasq/<version>/hyprmasq.so`
from the AUR, and as `/usr/lib/hyprmasq/hyprmasq.so` from a clone, see
[Arch and Omarchy](#arch-and-omarchy).

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
  with `--input`
- for [recordings](mcp.md#recording-a-demo), `ffmpeg` with libx264, from the
  Nix package's wrapper or from `PATH`.

Its settings, in `~/.config/hyprmasq/mcp.toml` or as options such as
`--max-image-size`, are described in [Configuration](mcp.md#configuration).

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
| The plugin fails to load with "built for another Hyprland build" | It was compiled against another Hyprland build. Rebuild it against the running one. `hyprctl version` shows that build. On Arch with a build from a clone, after a Hyprland update, run `makepkg -sif` in your clone and restart Hyprland. |
| The plugin fails to load with "this hyprmasq package has no plugin for the running Hyprland build" | `hyprmasq-plugin-bin` has not verified that Hyprland build yet. Run `yay -Syu` later and restart Hyprland, see [Updates](#updates). The MCP tools' `plugin_missing` error gives the same reason. |
| Loading fails with a hook error | The plugin was built against a Hyprland other than the pinned commit, whose functions differ. |
| `the plugin refused input to the window: another plugin hooks …` | Another Hyprland plugin hooks a function that hyprmasq needs for input to windows, which the message names. Screenshots keep working. Input works again once that plugin no longer holds it. `hyprctl hyprmasq hooks` lists what hyprmasq hooks and what was refused. See [Other plugins](automation.md#other-plugins). |
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
| `hyprmasq-mcp` fails to start with a message about `mcp.toml` | A key or value in its [configuration file](mcp.md#configuration) is not valid. The message names the file and the key. |
| A recording fails with `recording needs ffmpeg` | Install `ffmpeg` with libx264, or set `record.ffmpeg` in the configuration file. |
