# hyprmasq

A Hyprland plugin (C++, pinned to Hyprland 0.56.2 through `flake.lock`) that
takes per-window screenshots and gives each window its own libei input socket,
so windows on hidden workspaces can be captured and driven without moving the
user's focus. `tools/hyprmasq-input.c` is the libei sender,
`tools/hyprmasq-reaper.c` supervises launched processes, and
`tools/hyprmasq-launch` is the shell launcher. `mcp/` is the Go MCP server;
`bench/` has the benchmark runner and task checkers. User docs are in `docs/`.

The plugin runs inside the compositor: never block Hyprland's main thread, and
check hooked function signatures against the pinned Hyprland source, since
hooks are matched by name only.

## Checks before committing

- Build: `nix develop -c make`
- Format C/C++: `clang-format -i` on changed files (`.clang-format` is in the repo).
- MCP: inside `nix develop`, run `cd mcp && go vet ./... && go test ./...`.
  Use `go test -race ./...` when changing concurrency or process handling.
  Run `gofmt -w` on changed Go files.
- Bench unit tests: `python3 bench/analyze_test.py`,
  `python3 bench/dispatch_test.py`, `python3 bench/harness_test.py`, and
  `python3 bench/tasks/board/check_test.py`. The Calc checker needs LibreOffice:
  `nix-build bench/tasks/calc/check-test.nix`.
- Test: `nix build .#checks.x86_64-linux.vm-test -L`, the NixOS VM test in
  `nix/vm-test/`, split into areas. It must pass. Screenshots and logs from the
  run land in `result/`. Add a subtest for new behaviour, and update the
  subtest table in `docs/development.md`, which
  `nix build .#checks.x86_64-linux.vm-test-docs` checks against the code.

Nix only sees files known to Git, so `git add` new files before building.

## Pull requests

CI runs `nix flake check --no-build`, builds `hyprmasq` and `hyprmasq-mcp`
including the Go tests with `-short`, runs all Go tests with and without
`-race`, checks C/C++ with clang-format and Go with gofmt, checks the VM
test's subtest tables (`vm-test-docs`), and runs the bench unit tests above,
including Calc. The VM test is a local gate; CI
does not run it. Before opening a PR, run it on the commit you push and fill
in the VM test section of
`.github/pull_request_template.md` with that commit. Before merging, compare
the PR's head commit with the one it passed on: after new commits, an amend, a
rebase or a force-push, run it again on the new head. A PR that changes only
Markdown files skips the VM test and says so in that section.
