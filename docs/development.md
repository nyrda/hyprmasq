# Development

Run `./dev.sh` from the repository root.

| Command | Purpose |
| --- | --- |
| `./dev.sh build` | Build `hyprmasq.so`, `hyprmasq-reaper` and `hyprmasq-input` inside the flake's dev shell. This is the default command. |
| `./dev.sh vm-test` | Run the headless NixOS VM integration test. |
| `./dev.sh vm-test-interactive` | Open the test's interactive driver. |

The VM commands need Nix and KVM, and pass extra arguments to `nix build`.
`vm-test` builds `.#checks.x86_64-linux.vm-test -L`, and `vm-test-interactive`
builds its `driverInteractive` attribute and opens the driver.

CI runs `nix flake check --no-build` and builds `hyprmasq` and `hyprmasq-mcp`
with the Go tests that do not wait on real time. It builds the flake's checks
`mcp-tests`, which runs all Go tests, `format`, which checks C/C++ formatting
with clang-format and Go formatting with gofmt, `vm-test-docs`, which checks the
VM test's [subtest tables](vm-test.md#subtests) against its code, and
`bench-calc`, the Calc checker with LibreOffice. It also runs the other
[bench unit tests](#benchmarks). It runs on PRs and pushes to `main`, and keeps
the Nix store between runs in the GitHub Actions cache. A package or check
whose inputs did not change since the cached run is not built again, so a PR of
docs alone builds no plugin and runs no Go tests. A branch runs CI once at a
time: on `main`, a new commit waits for the running run, and on a PR, a new
push cancels the run it replaces. CI does not run the race
detector, which slows the tests of screenshots and pixels a hundredfold.

The NixOS VM test is a local gate, and CI does not run it. It runs on a PR's head
commit before the PR is opened, and again before merging whenever the head commit is no longer the one it passed on. A
PR that changes only Markdown files skips it, since neither the test nor its
Nix build reads them. The PR template asks for both runs.

The flake's dev shell, `nix develop`, has the build inputs of the plugin and
`hyprmasq-input`, Go for `hyprmasq-mcp`, and clang-format from `clang-tools`.

New files must be tracked by git (`git add`) before flake builds can see them.

To try a build in a running session, load it by absolute path:

```sh
hyprctl plugin load "$PWD/hyprmasq.so"
hyprctl plugin unload "$PWD/hyprmasq.so"
```

Unloading removes every open socket and every function hook.

For a staged install, `make install PREFIX=/usr/local DESTDIR=/path/to/stage`
puts the plugin in `lib`, the input tool and launcher in `bin`, and the reaper
in `libexec`, where the plugin finds it relative to its own path.

## Contributing

Contributions are welcome as pull requests against `main`.

1. Build with `./dev.sh build`, or `nix develop -c make`.
2. Run the checks that CI runs: `clang-format -i` on changed C and C++ files,
   `gofmt -w` on changed Go files, `go vet ./...` and `go test ./...` in `mcp/`
   (with `-race` when you change concurrency or process handling), the
   [bench unit tests](#benchmarks), and
   `nix build .#checks.x86_64-linux.vm-test-docs`.
3. Run the VM test, `./dev.sh vm-test`, on the commit you push. It needs Nix
   and KVM, takes a while, and is the local gate that CI does not run. Add a
   subtest for new behaviour and list it in the
   [subtest tables](vm-test.md#subtests).
   A PR that changes only Markdown files skips it.
4. Fill in `.github/pull_request_template.md`, including the commit the VM
   test passed on.

## hyprmasq-mcp

The MCP server is a Go module in `mcp/`, built with the Go of the pinned
nixpkgs and the [official Go SDK](https://github.com/modelcontextprotocol/go-sdk).

```sh
cd mcp
nix develop .. -c go build ./cmd/hyprmasq-mcp
nix develop .. -c go vet ./...
nix develop .. -c go test ./...
nix develop .. -c go test -race ./...
gofmt -w path/to/changed.go
```

Run the race check when changing concurrency or process handling. A plain
`go build` reports version `dev`, and the Nix build injects the top-level `VERSION`.

`cmd/hyprmasq-mcp` is the command, `internal/hypr` the client of Hyprland's
socket and the plugin's commands, and `internal/delta` compares two images for
the changes captures. `internal/server` has the tools, a file per area:

| File | What it has |
| --- | --- |
| `server.go` | The server, the tool descriptions and schemas, argument decoding, progress and the session's end. |
| `windows.go`, `launch.go`, `close.go` | The `windows`, `launch` and `close` tools, and the listing of new windows. |
| `capture.go`, `shots.go`, `pixels.go` | The `screenshot` tool and captures, the shots a session keeps, and their images. |
| `changes.go`, `moved.go` | Changes captures and their zooms, and the check that the targets of a batch did not move since its shot. |
| `act.go` | The `act` tool: its arguments, the order of its steps, and the result it finishes with. |
| `actions.go`, `batch.go` | Parsing actions, and checking a batch and turning it into `hyprmasq-input` steps. |
| `path.go` | The steps of a `path`, and the sampling of its spline. |
| `send.go`, `input.go` | Running `hyprmasq-input` under the send lock, its pacing and locale, and mapping its failures. |
| `wait.go` | The plugin's wait for a redraw, and its baselines. |
| `lease.go` | Focus leases and the `release` tool. |
| `drag.go` | Reading how the app's drag-and-drop went. |
| `newwindows.go`, `late.go` | Shots of the windows that input opened, of the window a closed one came from, and of windows that opened after their act. |
| `result.go`, `summary.go`, `hints.go`, `errors.go` | What a result says: its structured content, its summary line, its hints, and its errors. |
| `locks.go` | The window and send locks, shared with other servers. |

The tests of an area are in the `_test.go` files named after it or a part of
it, such as `send_test.go`, and `answer_test.go` for the wait for a click's
answer while a batch is sent. `fake_test.go` has the fake Hyprland and the fake
`hyprmasq-input` that they run against, and `wording_test.go` pins the hints
and error messages that a model reads. Other tests compare results with the
constants and builders that it names, so a change of wording changes that table
and the summary lines of `summary_test.go`.

The tests need no Hyprland. They run the server against a fake Hyprland
socket that answers the plugin's commands, and the test binary doubles as a
fake `hyprmasq-input` that the server starts and signals like the real one.
The command's own test runs `hyprmasq-mcp` as a subprocess, with its own
`XDG_RUNTIME_DIR` and a made-up `HYPRLAND_INSTANCE_SIGNATURE`, to check its
exit statuses.

`mcp/internal/server/input.go` repeats the pacing constants of
`tools/hyprmasq-input.c`, from which the server computes how long a batch
takes at least, and its `CANCEL_MS`, from which it computes when to stop a
sender that runs late. The sender's `pacedTime` adds up the same delays as
the server's `minSendTime` for its `--budget`, from the keys it resolved.
Change them together. Likewise,
`mcp/cmd/hyprmasq-mcp/main.go` names the directory of a Hyprland instance under
`$XDG_RUNTIME_DIR/hyprmasq` as `src/Eis.cpp` does.
The Nix build runs `go test -short` without the race detector in the check
phase of `nix build .#hyprmasq-mcp`, which `nix build .#hyprmasq` and the VM
test include. `-short` skips the tests that wait on real time for half a second
or more, and CI runs them all through `nix build .#checks.x86_64-linux.mcp-tests`. In `internal/server`, such a test starts with
`slow(t)`.

`nix/mcp-package.nix` vendors the Go dependencies by hash. After changing them
in `go.mod`, update `vendorHash` there: set it to `lib.fakeHash`, build, and
copy the hash from the error.

## Tests

The NixOS VM test in `nix/vm-test/` boots Hyprland with the plugin and covers
the plugin, the tools and the MCP server. Run it with `./dev.sh vm-test`, or
only some of its areas with
`nix-build nix/hyprmasq-vm-test.nix --argstr areas "mcp unload"`.
[VM test](vm-test.md) describes how it is built, its areas and every
subtest. A new subtest goes into its tables there, which
`nix build .#checks.x86_64-linux.vm-test-docs` checks against the code.

The Go tests are described [above](#hyprmasq-mcp), and the bench unit tests
[below](#benchmarks).

## Benchmarks

`bench/` runs agents on tasks in an offline NixOS VM through `hyprmasq-mcp`
alone, grades them from the VM's state, times them from both sides of the MCP
connection and asks them afterwards where hyprmasq slowed them down. It needs
KVM and logged-in `claude` and `codex`, and runs outside the Nix sandbox:

```sh
python3 bench/run.py --agent claude:claude-sonnet-5-5 --reps 1
```

[bench/README.md](../bench/README.md) describes the setup and the measures,
and [benchmarks.md](benchmarks.md) how runs are read and the release results.
Full benchmark runs are not a PR gate. The unit tests are:

```sh
python3 bench/analyze_test.py
python3 bench/dispatch_test.py
python3 bench/harness_test.py
python3 bench/laptop/run_form_test.py
python3 bench/laptop/run_mac_test.py
python3 bench/render_test.py
python3 bench/tasks/board/check_test.py
python3 bench/tasks/files/check_test.py
python3 bench/tasks/form/check_test.py
python3 bench/tasks/inkscape/check_test.py
python3 bench/tasks/trace/check_test.py
nix-build bench/tasks/calc/check-test.nix
```

## Source layout

| Path | Contents |
| --- | --- |
| `src/` | The plugin. `main.cpp` registers the hyprctl command and installs the hooks, `Commands.cpp` parses `hyprctl hyprmasq …`, and `PrivateAccess.cpp` reaches the private members of Hyprland that the plugin uses. |
| `tools/` | `hyprmasq-input`, `hyprmasq-reaper` and `hyprmasq-launch`. |
| `mcp/` | `hyprmasq-mcp`, see [above](#hyprmasq-mcp). |
| `nix/` | The package, the NixOS module, the [VM test](vm-test.md) and its probe clients. |
| `bench/` | The [benchmark](#benchmarks). |
| `PKGBUILD` | The Arch package. Its `package()` and the list in [What this installs](installation.md#what-this-installs) name the same files. |

The C and C++ code follows Hyprland's `.clang-format`. Go code is `gofmt`ed.

## Moving to a new Hyprland release

hyprmasq reaches into Hyprland internals. The compiler checks their types
against a new release, but not what they do:

- Each function hook names the type of the hooked function, its symbol and
  the full signature that the symbol demangles to, and has a
  `template struct hyprmasq::SPinned<…>` beside it in `src/`. The build fails
  when the headers give a hooked function another type, and the plugin
  refuses to load when the running Hyprland has no function of that symbol.
  A hooked function that changed needs its new type, symbol and signature
  together: `nm -D` and `nm -D --demangle` on the new Hyprland list its
  symbol and signature in the same order. Check that each hooked function
  still does what its hook expects. `CXDGToplevelResource::setSuspeneded`,
  whose name has Hyprland's typo, must stay the only way that Hyprland tells
  a toplevel that it is suspended.
- Private and protected members are all reached in one place,
  `src/PrivateAccess.cpp`, through explicit template instantiations
  (`template struct SExpose<…>`), each with a comment naming the Hyprland
  source it mirrors and with the member's type, which the build checks.
  Check that every member listed there still means the same.
- Update the pin in `flake.nix`, run the whole VM test, and add a
  `commit_pins` entry in `hyprpm.toml` mapping the previous Hyprland release
  commit to the last plugin commit that supports it.
