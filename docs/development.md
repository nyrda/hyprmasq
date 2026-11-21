# Development

Run `./dev.sh` from the repository root.

| Command | Purpose |
| --- | --- |
| `./dev.sh build` | Build `hyprmasq.so`, `hyprmasq-input`, `hyprmasq-reaper` and `hyprmasq-launch` inside the flake's dev shell. This is the default command. |
| `./dev.sh gate` | Run the test gate for PRs: the container test where the machine allows it, and otherwise the VM test. |
| `./dev.sh vm-test` | Run the headless NixOS VM integration test. |
| `./dev.sh container-test` | Run the same test in containers at once, in about a sixth of the time. It needs a GPU and [these Nix settings](vm-test.md#containers). |
| `./dev.sh vm-test-interactive` | Open the test's interactive driver. |

`--hyprland 0.56` after the command builds or tests against Hyprland 0.56.2,
the older of the [two Hyprland releases](#two-hyprland-releases), as in
`./dev.sh gate --hyprland 0.56`, and `--hyprland main` against
[Hyprland main](#hyprland-main). The VM commands need Nix and KVM, and pass
extra arguments to `nix build`.
`vm-test` builds `.#checks.x86_64-linux.vm-test -L`, and `vm-test-interactive`
builds its `driverInteractive` attribute and opens the driver.

CI runs `nix flake check --no-build` and builds `hyprmasq`, the plugin for
Hyprland 0.56.2 `hyprmasq-hyprland056`, the plugin for Hyprland main
`hyprmasq-hyprland-main`, and `hyprmasq-mcp` with the Go tests
that do not wait on real time. It builds the flake's checks
`mcp-tests`, which runs all Go tests, `format`, which checks C/C++ formatting
with clang-format and Go formatting with gofmt, `vm-test-docs`, which checks the
VM test's [subtest tables](vm-test.md#subtests) against its code, and
`bench-calc`, the Calc checker with LibreOffice. It also runs the other
[bench unit tests](#benchmarks), and checks `docs/benchmarks.md` and
`docs/comparison.md` against their data with `bench/render.py --check`. It runs on PRs and pushes to `main`, and keeps
the Nix store between runs in the GitHub Actions cache. A package or check
whose inputs did not change since the cached run is not built again, so a PR of
docs alone builds no plugin and runs no Go tests. A branch runs CI once at a
time: on `main`, a new commit waits for the running run, and on a PR, a new
push cancels the run it replaces. CI does not run the race
detector, which slows the tests of screenshots and pixels a hundredfold.

A second workflow, `.github/workflows/aur.yml`, builds and publishes the
[AUR packages](#aur-packages). On a PR that changes the plugin, the tools, the
MCP server or the packaging, it builds the prebuilt plugins and tools and the
three packages, and verifies the Hyprland builds of Arch and Omarchy, without
publishing.

The test gate, `./dev.sh gate`, is local, and CI does not run it. It runs the
[container test](vm-test.md#containers) on a machine with a render node and the
`uid-range` and `gpu` system features, and otherwise the VM test. It runs on a PR's head
commit before the PR is opened, and again before merging whenever the head commit is no longer the one it passed on. A
PR that changes how the test itself runs in the VM or the container passes both
tests. A PR that changes none of the plugin, its tools, the MCP server, the NixOS
module and the test, such as one of docs or `bench/` alone, skips it. A PR that
touches what differs between the two Hyprland releases also passes the gate on
Hyprland 0.56.2, see [when 0.56 is tested](#when-056-is-tested), and a PR that
moves `hyprland-main` or changes code that only Hyprland main builds passes it
on [Hyprland main](#hyprland-main). The PR template asks for each run.

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

Contributions are welcome as pull requests against `main`. Development needs
Nix on x86_64 Linux with KVM: the build, the checks and the test gate all run
through the flake, and there is no other supported setup.

1. Build with `./dev.sh build`, or `nix develop -c make`.
2. Run the checks: `clang-format -i` on changed C and C++ files,
   `gofmt -w` on changed Go files, `go vet ./...` and `go test ./...` in `mcp/`
   (with `-race` when you change concurrency or process handling), the
   [bench unit tests](#benchmarks), `python3 bench/render.py --check`, and
   `nix build .#checks.x86_64-linux.vm-test-docs`. CI runs them too, apart
   from `go vet` and the race detector.
3. Run the test gate, `./dev.sh gate`, on the commit you push. It is the local
   gate that CI does not run: the container test with a GPU and
   [these Nix settings](vm-test.md#containers), and otherwise the VM test, which
   needs Nix and KVM and takes a while. Add a
   subtest for new behaviour and list it in the
   [subtest tables](vm-test.md#subtests).
   A PR that changes none of the plugin, its tools, the MCP server, the NixOS
   module and the test skips it. Run `./dev.sh gate --hyprland 0.56` as well
   when [the PR needs it](#when-056-is-tested), and
   `./dev.sh gate --hyprland main` when [that one does](#hyprland-main).
4. Fill in `.github/pull_request_template.md`, including the commit the test
   passed on and which test ran.

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

`cmd/hyprmasq-mcp` is the command, `internal/config` reads its configuration
file, `internal/hypr` is the client of Hyprland's socket and the plugin's
commands, `internal/delta` compares two images for the changes captures, and
`internal/record` turns the frames and the input of act calls into slides or a
video. `internal/server` has the tools, a file per area:

| File | What it has |
| --- | --- |
| `server.go` | The server, the tool descriptions and schemas, argument decoding, progress and the session's end. |
| `windows.go`, `launch.go`, `close.go` | The `windows`, `launch` and `close` tools, and the listing of new windows. |
| `capture.go`, `shots.go`, `pixels.go` | The `screenshot` tool and captures, the shots a session keeps, and their images. |
| `changes.go`, `magnify.go`, `moved.go` | Changes captures and their zooms, how far a small zoom is enlarged, and the check that the targets of a batch did not move since its shot. |
| `act.go` | The `act` tool: its arguments, the order of its steps, and the result it finishes with. |
| `actions.go`, `batch.go` | Parsing actions, and checking a batch and turning it into `hyprmasq-input` steps. |
| `path.go` | The steps of a `path`, and the sampling of its spline. |
| `paste.go` | The `paste` action: its text on the agent clipboard, and the key that pastes it. |
| `split.go` | A batch that ends with scrolling, run in two parts, so that the agent sees what its input did before the page moves. |
| `page.go`, `scrollalign.go` | A page scroll, which measures how far the window scrolled by aligning its views before and after. |
| `send.go`, `input.go` | Running `hyprmasq-input` under the send lock of the window's app and the clipboard lock of a paste, its pacing and locale, and mapping its failures. |
| `wait.go` | The plugin's wait for a redraw, and its baselines. |
| `lease.go` | Focus leases and the `release` tool. |
| `recording.go` | Recordings of act calls: where one starts and stops, its frames, and finishing its files. |
| `drag.go` | Reading how the app's drag-and-drop went. |
| `newwindows.go`, `late.go` | Shots of the windows that input opened, of the window a closed one came from, and of windows that opened after their act. |
| `result.go`, `encode.go`, `summary.go`, `hints.go`, `errors.go` | What a result says: its structured content and how that is encoded, its summary line, its hints, and its errors. |
| `locks.go` | The window, send and clipboard locks, shared with other servers. A send lock is shared by the windows of one process, or by all windows of Hyprland's XWayland. |

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
`./dev.sh container-test` runs all of it in [containers](vm-test.md#containers)
at once, and `./dev.sh gate` picks that where the machine allows it.
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
python3 bench/series_test.py
python3 bench/vm_test.py
python3 bench/tasks/board/check_test.py
python3 bench/tasks/files/check_test.py
python3 bench/tasks/form/check_test.py
python3 bench/tasks/inkscape/check_test.py
python3 bench/tasks/pano/check_test.py
python3 bench/tasks/trace/check_test.py
nix-build bench/tasks/calc/check-test.nix
```

## Source layout

| Path | Contents |
| --- | --- |
| `src/` | The plugin. `main.cpp` registers the hyprctl command and sets up the features, `Hooks.cpp` resolves the hooked functions at load and installs them in groups while their features are in use, `Commands.cpp` parses `hyprctl hyprmasq …`, `PrivateAccess.cpp` reaches the private members of Hyprland that the plugin uses, and `Hyprland.hpp` holds what differs between the [two Hyprland releases](#two-hyprland-releases). |
| `tools/` | `hyprmasq-input`, `hyprmasq-reaper` and `hyprmasq-launch`. |
| `mcp/` | `hyprmasq-mcp`, see [above](#hyprmasq-mcp). |
| `nix/` | The package, the NixOS module, the [VM test](vm-test.md) and its probe clients. |
| `bench/` | The [benchmark](#benchmarks). |
| `PKGBUILD` | The Arch package built from a clone. Its `package()` and the clone's part of [Arch and Omarchy](installation.md#building-from-a-clone) name the same files. |
| `packaging/aur/` | The [AUR packages](#aur-packages) and the scripts that build, verify and publish them. |

The C and C++ code follows Hyprland's `.clang-format`. Go code is `gofmt`ed.

## Two Hyprland releases

The plugin builds against two Hyprland releases from the same source: the one
`flake.lock` pins, 0.57.2, and the release before it, 0.56.2, which Omarchy's
stable channel ships for about a month after Arch moves on. `HYPRLAND_VERSION`
names both, the pinned one first, and the Makefile refuses other versions.

- `src/HyprlandVersion.hpp` sets `HYPRMASQ_HYPRLAND` to 57 or 56 from the
  headers it finds, so every build system picks the right code without a flag.
- `src/Hyprland.hpp` includes the Hyprland headers that moved, and has a
  helper in `hyprmasq::hl` for each accessor that differs, such as
  `hl::mapped(window)`, `hl::isX11(window)` and `hl::log(...)`. Use those
  instead of the Hyprland call.
- What has no helper sits behind `#if HYPRMASQ_HYPRLAND >= 57` in the file
  that needs it: two hooked functions with other names in
  `src/FocusPolicy.cpp`, the render context in `src/Screenshot.cpp` and
  `src/Cursor.cpp`, and the private members of `src/PrivateAccess.cpp`.
  0.56 has no deferred hyprctl replies, so `src/Deferred056.cpp` implements
  `src/Deferred.hpp` for it with placeholders that a wrapped batch command
  fills in.
- The flake builds the plugin for 0.56.2 as `hyprmasq-hyprland056`, against the
  Hyprland of its `nixpkgs` input, and runs the test on it as
  `vm-test-hyprland056` and `container-test-hyprland056`. Areas read
  `hyprland_release` where Hyprland itself answers differently, as hyprctl's
  batches do.

### When 0.56 is tested

Every PR builds the plugin for both releases in CI, which catches an API that
one of them lacks. A PR also passes the gate on 0.56, with
`./dev.sh gate --hyprland 0.56`, when it:

1. adds, removes or changes a hook, or relies on more of what a hooked function
   does.
2. changes code that differs between the releases: `src/Hyprland.hpp`,
   `src/HyprlandVersion.hpp`, `src/Deferred056.cpp`, or a
   `#if HYPRMASQ_HYPRLAND` block.
3. starts using a Hyprland function, member, event or header that the plugin
   did not use before, also through `src/PrivateAccess.cpp`.
4. changes the test itself.

Other changes, such as to the MCP server, the tools, the docs or plugin logic
that uses Hyprland as before, pass the gate on 0.57.2 alone. A release commit
passes both.

## Hyprland main

hyprmasq's `main` follows the Hyprland releases. The branch
`port-hyprland-main`, a draft PR, also builds the plugin against Hyprland's
`main` branch, so that a change on the way to the next Hyprland release finds
hyprmasq ready, and so that the plugin runs on a Hyprland built from `main`,
such as the AUR's `hyprland-git`. On that branch the flake
locks a commit of it as its `hyprland-main` input, which hyprland.cachix.org
has prebuilt, and builds the plugin against it as `hyprmasq-hyprland-main`,
the Hyprland as `hyprland-hyprland-main`, and the test as
`vm-test-hyprland-main` and `container-test-hyprland-main`. The dev shell
`nix develop .#hyprland-main` and `./dev.sh ... --hyprland main` build against it.

Hyprland main reports the version of the last release until the next one, and
`HYPRLAND_MAIN_VERSION` names it. The Makefile takes that version as well as
those of `HYPRLAND_VERSION`, so a build from a clone works on `hyprland-git`,
and also on the release of that version, which it cannot tell from `main`.

Support for Hyprland main never costs the releases theirs. Hyprland main builds
as the pinned release, `HYPRMASQ_HYPRLAND` 57, until a commit of `main` changes
something that the plugin uses. Then `src/HyprlandVersion.hpp` tells it apart
by the first header that changed and sets `HYPRMASQ_HYPRLAND` to the next minor
version, 58, and the code for it goes behind `#if HYPRMASQ_HYPRLAND >= 58` or
into a helper of `src/Hyprland.hpp`, as for 0.56. The releases keep their code
and their test.

Where Hyprland main draws differently but through the same API, the plugin
follows the drawing for every release without a version check.
`drawnOffset()` in `src/Windows.cpp` is the place of a window's surface as the
renderer puts it: centered in the window when the surface is smaller, on
Hyprland main, and at 0 on the releases, whose `correctSmallVec()` returns
nothing there. Emulated input and the waits place the window's surface by it,
and the test's drag probe, whose 400×200 surface is smaller than its window,
is addressed through `dnd_point()` of `nix/vm-test/common.py`.

The branch moves `hyprland-main` to newer commits of Hyprland main and is
rebased onto hyprmasq's `main` as both move. Each move checks the commits since
the last one as [a new release](#moving-to-a-new-hyprland-release) would be
checked: the hooked functions and their page offsets, and the private members,
against the new source and build. It passes the gate on Hyprland main, and on
both releases too when it adds code for Hyprland main. A change on Hyprland
main that breaks hyprmasq is handled on the branch at Hyprland main's head,
never by leaving `hyprland-main` on a commit before it, and the fix keeps both
releases working.

When Hyprland tags the next release, the branch becomes the port to it and the
PR becomes ready. What it merges builds against the new release and the one
before it, and leaves out the Hyprland main target, which the next branch
brings back.

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
  still does what its hook expects, and that `src/plugins/HookSystem.cpp`
  still refuses a second hook of a function through `m_activeHooks`, which
  `src/Hooks.cpp` reads to name the function another plugin holds.
  `src/Hooks.cpp` also installs a hook again without `CFunctionHook::hook()`,
  which takes trampolines that Hyprland never frees: it writes back the jump
  that the first `hook()` wrote, and the state that `unhook()` reset. Check
  that `hook()` still writes only that jump over `m_hookLen` bytes, that
  `unhook()` still leaves the trampolines in place, and that
  `getAddressForTrampo()` only hands out new space, so that Hyprland never
  reuses or unmaps a trampoline. `src/PrivateAccess.cpp` asserts the sizes of
  `CHookSystem` and `CFunctionHook` at the pin, which a list of freed
  trampolines would change, and stops the build until this is checked. At
  run time, the plugin compares the trampolines with what the first `hook()`
  wrote before it installs a hook again, and refuses the feature with code
  `internal` if they changed.
- A build of Hyprland may rely on what a function it knows does to its
  arguments, as Arch's does with LTO. Arch's Hyprland releases its `SP` that
  it passed `CXDGShellProtocol::addOrStartGrab` by value through the pointer
  it held before the call. So the code that Hyprland calls for a hook,
  `SCounted` in `src/Hooks.hpp`, hands the hook copies and leaves the
  arguments as they came. The `qt-popups` area checks it with
  `nix/call-probe.cpp`.
- `unhook()` makes only the first page of a hooked function writable before
  it writes the function's first bytes back. For each hooked function, its
  offset within its 4 KiB page plus the bytes that `hook()` replaces, whole
  instructions over at least 5 bytes and so at most 19, must stay at most
  4096. List the offsets with
  `nm -D --defined-only .Hyprland-wrapped`, on the binary in the Hyprland
  package's `bin`, as the address of each symbol that `src/` hooks modulo
  4096. An offset of at most 4077 is safe. At v0.57.2 every one is 16-byte
  aligned and the largest is 4000. `CXDGToplevelResource::setSuspeneded`,
  whose name has Hyprland's typo, must stay the only way that Hyprland tells
  a toplevel that it is suspended.
- Private and protected members are all reached in one place,
  `src/PrivateAccess.cpp`, through explicit template instantiations
  (`template struct SExpose<…>`), each with a comment naming the Hyprland
  source it mirrors and with the member's type, which the build checks.
  Check that every member listed there still means the same.
- Update the pin in `flake.nix` and `HYPRLAND_VERSION`. The release that was
  pinned becomes the older of the [two releases](#two-hyprland-releases): its
  differences go behind `#if HYPRMASQ_HYPRLAND` and the helpers of
  `src/Hyprland.hpp`, and the plugin builds it from the flake's `nixpkgs`
  input at a revision whose Hyprland is that release, or else from an input
  that pins its release commit. The port is the branch of [Hyprland main](#hyprland-main):
  the code it already has for the new release becomes the pinned release's,
  and the Hyprland main target leaves the port. The release before it drops out with its
  code, once neither Arch nor Omarchy's stable channel ships it. Run the
  whole test on both, and add a `commit_pins` entry in `hyprpm.toml` for a
  patch release that `main` moved past, mapping its Hyprland commit to the
  last commit of `main` that targets it.

## Releasing

A release is a commit of `main` whose `VERSION` names it, with the tag
`vX.Y.Z` and a GitHub release. The plugin, `hyprmasq-input`,
`hyprmasq-launch` and the Nix and Arch builds of `hyprmasq-mcp` report
`VERSION`, so a commit after a release reports that release until the next
one.

1. In a PR of its own, set `VERSION` and the lines of the docs that quote it:
   the first paragraph and the `pacman -Q` example of `docs/installation.md`,
   and the two `version` rows of `docs/automation.md`. It changes what the
   plugin and the tools report, so it passes the [test gate](#contributing)
   on both Hyprland releases.
2. Tag the PR's commit on `main` and push the tag:
   `git tag -a vX.Y.Z -m "hyprmasq X.Y.Z" <commit>`, then
   `git push origin vX.Y.Z`. The tags are not signed yet, as
   [Checking what you install](installation.md#checking-what-you-install) says.
3. The tag's push runs `.github/workflows/aur.yml`: it builds the prebuilt
   plugins and tools, creates the GitHub release with them attached, each
   with a build provenance attestation, and publishes the three
   [AUR packages](#aur-packages) of the release. Check that the run passed,
   and that `gh attestation verify <file> --repo nyrda/hyprmasq` passes for a
   downloaded plugin and the tools archive.
4. Write the GitHub release's notes. They are the project's changelog: what
   changed since the last release, what a user has to change, and the Hyprland
   releases it builds against. Their links go to the docs at the tag,
   `blob/vX.Y.Z/docs/…`.

## AUR packages

`packaging/aur` holds the AUR packages that `yay -S hyprmasq` and
`yay -S hyprmasq-bin` install:

- `hyprmasq` builds `hyprmasq-mcp`, `hyprmasq-input`, `hyprmasq-launch` and
  `hyprmasq-reaper` from the release's archive, and depends on
  `hyprmasq-plugin` of its version or newer. yay installs a new
  `hyprmasq-plugin-bin` before it builds the new `hyprmasq`, so an exact
  version would stop the update.
- `hyprmasq-bin` installs the same programs prebuilt, in place of `hyprmasq`.
  `build-tools.sh` builds them with the PKGBUILD of `hyprmasq`, tests
  included, on the oldest snapshot that a plugin builds on, as below, and
  `check-userland.sh` loads their libraries on each userland. The release has
  them as `hyprmasq-<version>-x86_64.tar.gz`.
- `hyprmasq-plugin-bin` provides it: one prebuilt plugin for each Hyprland
  release that `HYPRLAND_VERSION` names and Arch or Omarchy's stable channel
  serve, `hyprland-builds` with the Hyprland builds that each plugin runs, and
  `hyprmasq_plugin.lua`, which loads `/usr/lib/hyprmasq/<hl.version()>/hyprmasq.so`.

The plugin loads into the build it was compiled against, as everywhere, and
into a build that the `hyprland-builds` beside it names. Without that file, as
in every other install, only the first.

`packaging/aur/builds.py` decides what goes in the list. A release's plugin for
a Hyprland release is compiled against a reference build: the one that the
oldest userland serves, Omarchy's stable channel before Arch.
`build-plugin.sh` builds it on the Arch archive's snapshot of the day that
build entered Arch, starting from the `archlinux/archlinux` image from before
it, so that it needs no newer glibc or libstdc++ than any userland that runs
it, and `check-userland.sh` loads its libraries on each of those userlands.
Another served build goes in the list when:

1. the plugin compiled against its headers and those of the libraries that its
   `version.h` names is byte for byte the plugin compiled against the
   reference's, with the same toolchain, the reference's `version.h` and no
   build id,
2. its Hyprland and libraries export every symbol that the shipped plugin
   imports or hooks, and
3. its Hyprland commit is the reference's, or the source files of every hooked
   function, and of every class whose private members `src/PrivateAccess.cpp`
   reads, did not change since. A function that it cannot find, or that moved
   to another file, counts as a change.

`.github/workflows/aur.yml` runs every hour. When Arch's `extra`,
`extra-testing` or `extra-staging`, or Omarchy's stable mirror, serve a
Hyprland build that the AUR's `hyprland-builds` lacks, it verifies it against
the released plugins, and publishes the list with a new `pkgrel` through
`packaging/aur/publish.sh`, which accepts only lines of hashes and versions. It
also checks every hour that the plugins and tools still load their libraries. A build
that fails a check is held: it opens one issue for it, with the reasons, and
the plugin refuses that build. When only the Hyprland sources changed, read the
change, and run the workflow by hand with `accept` set to the build's version,
which skips that one check. Otherwise the build needs a new plugin, which comes
with the next release.

The verification and the issues run without secrets. Only the step that
publishes has the AUR key, in the repository secret `AUR_SSH_PRIVATE_KEY`.
Only the job that attaches a release's plugins and tools has `id-token: write`
and `attestations: write`: before it attaches a file that the release lacks, it
attests the file's build provenance with `actions/attest-build-provenance`. A
file that is attached already keeps the attestation of the run that attached
it, and forks and PRs attest nothing.
GitHub turns off a scheduled workflow after 60 days without a commit to the
repository, so look at its runs after a quiet spell.
