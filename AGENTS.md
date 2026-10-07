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

## Pull requests

CI does not run the VM test, so it is a gate on your machine. Before opening a
PR, run it on the commit you push and fill in the VM test section of
`.github/pull_request_template.md` with that commit. Before merging, compare
the PR's head commit with the one it passed on: after new commits, an amend, a
rebase or a force-push, run it again on the new head. A PR that changes only
Markdown files skips the VM test and says so in that section.
