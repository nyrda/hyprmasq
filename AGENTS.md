# hyprmasq

A Hyprland plugin (C++, pinned to Hyprland 0.56.2 through `flake.lock`) that
takes per-window screenshots and gives each window its own libei input socket,
so windows on hidden workspaces can be captured and driven without moving the
user's focus. `tools/hyprmasq-input.c` is the libei sender. User docs are in
`docs/`.

The plugin runs inside the compositor: never block Hyprland's main thread, and
check hooked function signatures against the pinned Hyprland source, since
hooks are matched by name only.

## Checks before committing

- Build: `nix develop -c make`
- Format C/C++: `clang-format -i` on changed files (`.clang-format` is in the repo).
- Test: `nix build .#checks.x86_64-linux.vm-test -L`, the NixOS VM test in
  `nix/vm-test.py`. It must pass. Screenshots and logs from the run land in
  `result/`. Add a subtest for new behaviour, and update the subtest table in
  `docs/development.md`.

Nix only sees files known to Git, so `git add` new files before building.
