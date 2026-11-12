# hyprmasq

This is the `hyprland-0.56` branch, maintained while people move to Hyprland
0.57. `main` targets the latest stable Hyprland. Fixes and MCP changes land on
`main` first and are ported here, where they must pass this branch's VM test.
After a change here, point the Hyprland 0.56.2 entry of `commit_pins` in
`main`'s `hyprpm.toml` at the new head.

A Hyprland plugin (C++, pinned to Hyprland 0.56.2 through `flake.lock`) that
takes per-window screenshots and gives each window its own libei input socket,
so windows on hidden workspaces can be captured and driven without moving the
user's focus. `tools/hyprmasq-input.c` is the libei sender,
`tools/hyprmasq-reaper.c` supervises launched processes, and
`tools/hyprmasq-launch` is the shell launcher. `mcp/` is the Go MCP server;
`bench/` has the benchmark runner and task checkers, and `bench/laptop/` the
runner for real machines. `PKGBUILD` builds the Arch package from a clone. A
change to the files it installs updates its `package()` and the list in
`docs/installation.md`.

Docs: `README.md` is the landing page; `docs/installation.md` covers install,
loading and MCP registration; `docs/automation.md` is the reference for
`hyprctl hyprmasq`, `hyprmasq-input`, `hyprmasq-launch`, the plugin options
and the security model; `docs/mcp.md` covers `hyprmasq-mcp`;
`docs/development.md` covers builds, tests, contributing and releasing, with
`CONTRIBUTING.md` pointing to it and the bug report form in
`.github/ISSUE_TEMPLATE/`, and
`docs/vm-test.md` the VM test, its areas and every subtest;
`docs/benchmarks.md` holds the release benchmark results, whose figures and
tables `bench/render.py` generates from `docs/benchmarks/data`;
`docs/comparison.md` compares hyprmasq with Cua Driver, computer-use-linux and hypruse, with its tables in
`docs/comparison-numbers.md`, and `docs/comparison-macos.md` with OpenAI's and Claude's computer use and Cua Driver on
macOS, their figures and tables generated the same way from `docs/comparison/data`. Keep them in step
with the code: a changed command, option, default or exit code updates the
page that documents it. Agents use the MCP tools; there is no agent skill.

The plugin runs inside the compositor: never block Hyprland's main thread, and
check that hooked functions still do what their hooks expect against the
pinned Hyprland source. The build checks only their types.

## Checks before committing

- Build: `nix develop -c make`
- Format C/C++: `clang-format -i` on changed files (`.clang-format` is in the repo).
- MCP: inside `nix develop`, run `cd mcp && go vet ./... && go test ./...`.
  Use `go test -race ./...` when changing concurrency or process handling.
  Run `gofmt -w` on changed Go files.
- Bench unit tests: `python3 bench/analyze_test.py`,
  `python3 bench/dispatch_test.py`, `python3 bench/harness_test.py`,
  `python3 bench/laptop/run_form_test.py`, `python3 bench/laptop/run_mac_test.py`,
  `python3 bench/render_test.py`, `python3 bench/series_test.py`,
  `python3 bench/vm_test.py`,
  `python3 bench/tasks/board/check_test.py`,
  `python3 bench/tasks/files/check_test.py`,
  `python3 bench/tasks/form/check_test.py`,
  `python3 bench/tasks/inkscape/check_test.py` and
  `python3 bench/tasks/trace/check_test.py`. The Calc checker needs LibreOffice:
  `nix-build bench/tasks/calc/check-test.nix`.
- Test: `./dev.sh gate` runs the test in `nix/vm-test/`, split into areas. It must
  pass. It runs the areas in containers at once, in about a sixth of the time, on a
  machine with a GPU and the Nix settings in `docs/vm-test.md`
  (`.#checks.x86_64-linux.container-test`), and otherwise in a NixOS VM
  (`.#checks.x86_64-linux.vm-test`). Screenshots and logs from the run land in
  `result/`. Add a subtest for new behaviour, and update the
  subtest table in `docs/vm-test.md`, which
  `nix build .#checks.x86_64-linux.vm-test-docs` checks against the code.

Nix only sees files known to Git, so `git add` new files before building.

## Pull requests

CI runs `nix flake check --no-build`, then builds `hyprmasq` and `hyprmasq-mcp`
including the Go tests with `-short`, and the flake checks `mcp-tests` (all Go
tests), `format` (clang-format and gofmt), `vm-test-docs` (the VM test's
subtest tables) and `bench-calc` (the Calc checker). It runs the other bench
unit tests above and checks `docs/benchmarks.md`, `docs/comparison.md` and the comparison's two other pages
against their data with `bench/render.py --check`. It caches the Nix store
between runs, so a check whose inputs did not change is not built again. CI
does not run `go test -race`. The test gate, `./dev.sh gate`, is local; CI does not run it. Before opening a PR, run it on the commit you push and fill
in the test gate section of
`.github/pull_request_template.md` with that commit and which test ran. Before merging, compare
the PR's head commit with the one it passed on: after new commits, an amend, a
rebase or a force-push, run it again on the new head. A PR that changes how the
test itself runs in the VM or the container passes both tests. A PR that changes none
of the plugin, its tools, the MCP server, the NixOS module and the test,
such as one of docs or `bench/` alone, skips the gate and says so in that
section.
