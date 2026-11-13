# Benchmarks

`bench/` is a development tool for measuring hyprmasq with real agents. It is not part of what hyprmasq
installs. It requires Linux with KVM, Nix, and the agent CLIs logged in for the user running it. The default
agents use logged-in Claude Code and Codex. API-backed agents need their provider credentials.

Agents complete a task in an offline NixOS VM through `hyprmasq-mcp`. The benchmark grades the VM's state,
measures time on both sides of the MCP connection, and asks the agent for a debrief. It measures how agents
use hyprmasq and where hyprmasq costs them time. The tasks are board, calc, files, forgejo, form, inkscape,
thunderbird and trace.

Run results are in [docs/benchmarks.md](../docs/benchmarks.md). `export.py` turns results directories into the
datasets that page is generated from, and `render.py` draws its figures and tables; see
[Updating the results](#updating-the-results). `render.py` draws
[docs/comparison.md](../docs/comparison.md) as well, with its pages of
[numbers](../docs/comparison-numbers.md) and of [macOS](../docs/comparison-macos.md), from the datasets in
`docs/comparison/data` that `export.py` writes of the other backends' runs.

## Updating the results

[docs/benchmarks/data](../docs/benchmarks/data) holds one JSON file per set of runs, with one run a line.
`bench/export.py` writes such a file from the results directories that `bench/run.py` filled. It leaves out
transcripts, answers, paths, host names and accounts:

```sh
python3 bench/export.py --out docs/benchmarks/data/next-run.json \
  --fail calc/RUN="agent did not finish" \
  bench/results/next-run/board-dnd bench/results/next-run/calc
```

`--fail` counts a run as failed although the VM's check passed it, and records the reason. Each run records its task's
variant, its `backend`, `prompt` and `browser` (see [Cua Driver](#cua-driver)), the tool's `refusals`, and its `context`
as `summary.json` has it (see [Measures](#measures)). A Cua run's `act_s` is `null`: it has no act calls. A dataset
holds one backend, one prompt and one browser of each task. `--several-machines` takes runs that `bench/dispatch.py` spread over several machines into one dataset, each
run with its CPU. `--agent PATTERN` keeps only the runs of the agents that match, as `dispatch.py`'s `agents=`
takes them, such as `'*:*@medium'`, so that one results directory gives several datasets. `python3 bench/render.py` generates each block of `docs/benchmarks.md`, `docs/comparison.md`, `docs/comparison-numbers.md` and
`docs/comparison-macos.md`
between `<!-- render DATASET VIEW [TASK] -->` and `<!-- /render -->` from a dataset. Where a block's data holds
several variants of a task, it names them by their variant too, such as `board-dnd`, and TASK takes that name. It also
writes the figures in `docs/benchmarks` and `docs/comparison`, one for GitHub's light theme and one for its dark
theme. Its docstring lists the views. CI runs `python3 bench/render.py --check`, which fails when a page or a figure
no longer matches the data.

## Tests

From the repository root:

```sh
bench/test.sh
```

This runs all `*_test.py` files, including the Calc checker through `nix-build bench/tasks/calc/check-test.nix`.
The Calc cases use headless LibreOffice in a Nix build. The suite starts no VMs or agents and uses no paid APIs.
It needs Python 3 and Nix. Ordinary `unittest` discovery skips these filenames and does not run the Calc cases.

To build a VM driver without running an agent:

```sh
nix-build bench/default.nix -A driver
# For another task, add --argstr task calc, files, forgejo, board, form, inkscape or trace. Board also needs --argstr variant dnd or
# pointer, form its number of questions, such as --argstr variant 25, and trace its figure, river.
```

See [By hand](#by-hand) to boot that driver and probe MCP without an agent.

## Running

```sh
python3 bench/run.py                                    # 4 agents × 3 runs, the default
python3 bench/run.py --agent claude:claude-sonnet-5-5 --reps 1
python3 bench/run.py --agent pi:tensorx/deepseek/deepseek-v4.1-flash-1017 --agent claude-api:claude-haiku-5-5
python3 bench/run.py --out bench/results/my-run         # resumes: runs with a summary.json are skipped
python3 bench/run.py --source ../hyprmasq-feature       # the plugin and server of another worktree
python3 bench/run.py --task forgejo                     # another task: a directory under bench/tasks
python3 bench/run.py --display 2560x1440@1              # another monitor: WIDTHxHEIGHT@SCALE
python3 bench/run.py --max-calls 20                     # stop and fail a run at its 21st tool call
python3 bench/run.py --task board --variant pointer     # a task's variant: one of tasks/TASK/variants
python3 bench/run.py --task form --variant 10           # a quick probe: 10 questions instead of the default 25
python3 bench/run.py --task form --variant 25-no-dropdowns  # the form without dropdowns
python3 bench/run.py --rep 2 --rep 3                    # only these repetitions, instead of --reps
python3 bench/run.py --prompt comparison                # the tool-neutral prompt of a comparison
python3 bench/run.py --backend cua --prompt comparison --source ../hyprmasq-0.56  # Cua Driver instead of hyprmasq
python3 bench/run.py --backend hypruse --prompt comparison  # hypruse, or computer-use-linux, instead of hyprmasq
python3 bench/run.py --task board --browser chromium    # Chromium as the user's browser instead of Firefox
python3 bench/run.py --desktop omarchy                  # Omarchy's key binds, portals and packages, for every backend
python3 bench/report.py bench/results/my-run            # the report again
```

`--source DIR` builds the plugin, `hyprmasq-mcp` and the NixOS module from another checkout, for example a
feature branch's worktree, so that a branch can be benchmarked without the benchmark in it. The plugin is
built from the files Git tracks there, as in a flake build. `source.json` in the results records its commit,
the task, its variant, the display, the backend, the prompt, the browser and the desktop, this benchmark's commit and
whether it had changes, the hash of the prompt and the debrief the agent gets, the agent's timeout, the budget of calls and
whether a debrief runs. `run.py` and `dispatch.py` refuse to add runs to a directory whose finished runs had another of
them, or that another build ran: each run's `run.json` records the store paths of the VM's driver, with its plugin and
server in it, and of the skill as `identity`, and `ready.json` those of the server and of Cua's plugin. So a change to the
benchmark takes a new results directory, which is what a series wants: its results stay one build's. A `source.json` from
before these fields counts as hyprmasq's with the task's own prompt, Firefox and the bare desktop, and is not compared
for the rest.

`--variant NAME` picks one of a task's variants, which `tasks/TASK/variants` lists, one a line. A task with that
file needs it, unless a line there is `NAME default`, which it then takes; others refuse it. A variant's directory,
`tasks/TASK/NAME`, may hold a `prompt.md` and a `debrief.md` of its own, which the agent then gets. `default.nix`
gives the variant to the task's `vm.nix` as `benchVariant`; the default results directory and the driver's name
carry it.

`--display WIDTHxHEIGHT@SCALE` sets the VM's monitor, in device pixels, by default `1920x1080@1`. Shots fit
within 1280 pixels and zooms come at the monitor's scale, so what agents can read depends on it. The VM draws
in software: at 3840×2160 everything draws slower, also hyprmasq's captures, so times compare only between runs
on the same display. `source.json` records it.

`--mcp-args` gives `hyprmasq-mcp` in the VM options of its own, such as `--max-image-size 1920`, and `source.json`
records them as `mcp_args`. The comparison's image size probe ran with it.

`--lock FILE` holds a shared lock on FILE while each run's VM is up, for machines where other VMs, such as the VM
test under `flock FILE`, would not fit into memory next to the benchmark's. Benchmark VMs share the lock, so the
slots of `dispatch.py` run their VMs together, and `flock FILE`, which takes it exclusive, waits for them, as they
wait for it. flock lets a shared lock in beside another while an exclusive one waits, so slots whose runs overlap
would hold the lock for ever: a run does not take it while a process waits for it exclusive, which `/proc/locks`
shows, and the VM test gets its turn once the runs in progress end. A run waiting so, or for a VM test, has not
started: its agent and times wait with it.

`--cpus LIST`, such as `4-5,12-13`, runs each run's VM, the test driver and QEMU with all its threads, on those
CPUs alone, through the CPU affinity that they inherit from `run.py` and that any user may set. The agent and the
rest of the run go on running on any CPU. `run.json` records the CPUs as `cpus`. `dispatch.py`'s `cores=` gives each
slot such a list.

`--agent HARNESS:MODEL[@EFFORT]` is repeatable. HARNESS is one of:

- `claude`: Claude Code, with the user's login, a claude.ai subscription included;
- `codex`: Codex CLI, with the user's login;
- `claude-api`: Claude Code billed to the user's Anthropic API key instead, such as `claude-api:claude-sonnet-5-5`
  (see [API keys](#api-keys));
- `pi`: pi, with MODEL as `PROVIDER/MODEL` of the user's pi setup, such as
  `pi:tensorx/deepseek/deepseek-v4.1-flash-1017`. A run's directory has `_` for every `/` in the model.

`@EFFORT` sets the reasoning effort, such as `codex:gpt-6.1-sol@high`; see [Effort](#effort). The default is
`codex:gpt-6.1-sol`, `codex:gpt-6.1-terra`, `codex:gpt-6-astra`, `claude:claude-opus-5-5` and
`claude:claude-sonnet-5-5`.
Repetitions go round the agents, so a slow hour of one API does not land on one agent's runs only.

It needs KVM, Nix, and the harnesses of the agents logged in for the user who runs it; it uses those logins as they
are, subscriptions included. It copies no credentials off the machine: a run of the Claude subscription has its own
copy of the access token, without the refresh token, removed with the run's directory. A run takes the VM's boot (about a minute), the
agent, and the debrief; runs are sequential, so that they do not compete for the CPU. `dispatch.py` runs several at
once, each VM on cores of its own.

`--prompt comparison` gives the agent the task's `prompt-comparison.md` instead of its `prompt.md`, the wording of
the [comparisons](../docs/comparison.md) for every tool: the agent sees and controls the desktop only through its
desktop tools, and the user, working in another window, has authorized foreground input and focus changes if the
tools need them, but prefers ways that leave their window alone. The task's own prompt, which names hyprmasq and asks
not to disturb the user, stays the default, so that runs such as the 0.57.0 baseline can be repeated. `run.json` and
`source.json` record the prompt as `prompt`, `task` or `comparison`.

`--browser chromium` gives the user Chromium in place of Firefox in the tasks with a browser of the user's that list it
in `tasks/TASK/browsers`: board, trace and calc. It shows the same page, from the same server, in a window of the same
size on the same workspace, with a profile of its own (`~/.config/chromium-mara`) and the Forgejo task's policies
against first-run screens, sign-in and promotions ([tasks/browser.nix](tasks/browser.nix)), on Wayland, and with
`--force-renderer-accessibility` for Cua, as its browser guide asks. `vm.py`'s `start_browser` starts either browser.
The prompt and the debrief name Chromium where the task's text names Firefox. The checks are the same: the pages save
their state on their server, and calc reads Mara's tabs from Chromium's session file instead of Firefox's. Firefox
stays the default, and hyprmasq, which works with any browser, keeps it. `run.json` and `source.json` record the
browser as `browser`, and `run.py` refuses to mix browsers in one results directory.

`--desktop omarchy` gives the VM what a real Omarchy 4.5 desktop offers a tool, for every backend, in place of the bare
desktop the published benchmarks ran on, which stays the default, so that those can be repeated:

- Omarchy's workspace and window key binds and its input settings ([omarchy-desktop.lua](omarchy-desktop.lua)), but for
  binds that run its own scripts. `SUPER+2` reveals a workspace, as on the machine.
- The task's windows on numbered workspaces, from 2 up, in place of named ones, which those binds reach. vm.py's `move`
  maps the names, the user stays on workspace 1, and the measures of what disturbed the user read as before: a switch of the
  active workspace, a window opened on the user's and the time the focus was away.
- Its portals, Hyprland's and GTK's in its order, running for the whole session, and the session's variables.
- The commands of its package list that the tools call: `wtype`, `wl-copy`, `xdg-open`, `gdbus` and others.
- Chromium with `--force-renderer-accessibility` for every backend, so that all run the same browser.

What stays different from the real machine, and whether it matters, is in [capability-results.md](capability-results.md).

### Cua Driver

`--backend cua` runs the task with [Cua Driver](https://github.com/trycua/cua) in place of hyprmasq, set up as its
documentation sets it up on Linux and Hyprland, for comparing the two on the whole suite. The task, its VM, its
grading and the measures are the same: only the desktop tool differs.

- **Release:** Cua Driver 0.42.1, tag `cua-driver-rs-v0.42.1`, commit `badaa6e0d59fb5add81474bc04efadb89f713eb9`, from
  the tag's source, unchanged ([cua/default.nix](cua/default.nix)), the newest tag that was not a nightly on 7 November
  2026. Tags come several times a day and are marked pre-release, so the suite pins one. The driver is built by Cua's own Nix package with the features of its Linux release binary
  (`portal-input`), the plugin with CMake as its README builds it, with `-DCUA_HYPRLAND_INPUT=ON` as its release build
  kit builds it, against the VM's Hyprland and with Hyprland's compiler, and its CTest suite passes. Since
  [#6495](https://github.com/trycua/cua/pull/6495) and [#6329](https://github.com/trycua/cua/pull/6329) the plugin
  compile-checks Hyprland's internals instead of pinning 0.56.2, and it builds, passes its tests and loads on 0.57.1.
- **Hyprland 0.56.2:** the driver still sees no window of Hyprland 0.57.1, so the plugin's support for it does not help.
  Its `j/monitors` and `j/clients` leave out `id` for a named workspace and for an absent special workspace, which is
  every monitor without one, and the driver's `Workspace` in `wayland/hyprland.rs` requires it. `list_windows` then says
  `Found 0 windows`, with `invalid Hyprland IPC JSON: missing field 'id'` in its log, the same at the pinned tag on 7
  November 2026. Making that field optional in a scratch build, which the suite does not use, gave on 0.57.1 the same
  results as on 0.56.2 in the GTK 4 form, so it is the only obstacle found there. So the Cua backend runs with `--source` at a checkout of
  the `hyprland-0.56` branch, `e4273e0` in the 0.38.0 series and the commit in the rules in the comparison series, and
  `default.nix` refuses it on another Hyprland.
- **Plugin:** loaded and enabled as its README says: `hyprctl plugin load`, then
  `hl.config({plugin = {cua = {enabled = true}}})` in the configuration and `hyprctl reload`. `vm.py` waits for
  `hyprctl -j cua:status` to report its input transport ready, and `ready.json` keeps that status as `cua_status`, with
  the versions of Hyprland and the driver as `versions`, which `export.py` copies into each run. The VM's `kb_layout = "us"`
  compiles to the plain `evdev`/`pc105`/`us` keymap that its foreground keys ask for: they were not refused for the layout.
- **Server:** `cua-driver mcp`, served on the vsock port as `hyprmasq-mcp` is, with `CUA_DRIVER_RS_ENABLE_WAYLAND=1`
  (its skill's `LINUX.md`), the session's variables, and `CUA_DRIVER_RS_TELEMETRY_ENABLED=false`, as the VM is
  offline (its guide's Telemetry). It runs in Cua's default `standard` permission mode. The agent knows the server as
  `cua-driver`, the name in Cua's table of harnesses.
- **Skill:** the skill pack as `cua-driver skills install` installs it on Linux, the release's `Skills/cua-driver`
  without the other platforms' guides, in an agent home of the run's own, and named at the start of the prompt, as
  the [laptop's runs](laptop/README.md) did. Claude Code gets `CLAUDE_CONFIG_DIR` with `skills/cua-driver`, its Skill
  tool beside the server's tools, `--setting-sources user`, which in that fresh home holds the skill alone, and
  `/cua-driver ` before the prompt. Codex gets a `HOME` with `.agents/skills/cua-driver`, its skills listed, and
  `$cua-driver ` before the prompt. The skill's guides, such as `LINUX.md` and `BROWSER.md`, are files that a real agent reads
  with its file tools. Here the agent has none, as no agent in the suite does, and reads them as the server's resources
  (`skill://cua-driver/LINUX.md`), the transport Cua ships for this, with Claude Code's `ListMcpResourcesTool` and
  `ReadMcpResourceTool` and Codex's `read_mcp_resource`. The debrief names Cua Driver where it named hyprmasq. pi does not
  run with Cua, whose documentation has pi call its CLI.
- **Agent cursor:** `cua-driver mcp --no-overlay`. Its cursor is a full-screen layer surface that it repaints while it works, for a
  person to watch, which a desktop composites on its GPU for next to nothing. The VM has no GPU, and its Hyprland composites it in
  software at more than two of the VM's four CPUs while the driver reads a window, which made its walks 2 to 2.7 times as long
  and its screenshots twice as long, as [capability-results.md](capability-results.md) measures. No tool of the driver depends on it.
- **Desktop route:** with `--desktop omarchy`, its desktop tools reach a hidden window: `hotkey` with `super+2` reveals the
  window's workspace, and `click`, `drag`, `type_text` and `press_key` with `target: {kind: desktop}` then act on it, with
  `wtype` for text, as its skill's Desktop loop has it. This is what is left for an app that binds the user's seat more
  than once, as Firefox and Thunderbird do, whose `foreground` the plugin refuses.
- **Background input:** nothing to set up. Since [#6706](https://github.com/trycua/cua/pull/6706) the driver admits any
  native Wayland window whose app bound the plugin's agent seats, in place of the two Arch packages it admitted before.
- **Browser tools:** its documentation sends Chromium's pages to them. They have two ways in, and the suite gives both:
  - The user's own browser, with `cua-driver mcp --grant existing-profile` and `--force-renderer-accessibility` (its guide's
    Attach to a logged-in profile), wherever the user's browser is Chromium: the Forgejo task and `--browser chromium`.
    Calc needs it most, as its tabs are signed in and an isolated browser is not. `browser_prepare` then refuses with
    `browser_wrong_target_refused`, as its navigation to `chrome://inspect` needs a foreground adapter that Hyprland lacks
    (`no trusted Wayland compositor adapter`, `interaction/mod.rs`), where without the grant it refuses with
    `browser_consent_required`. `get_browser_state` before that says `browser_consent_required` either way, its way of
    saying "not attached yet", which `report.py` counts under its kind `setup`. With remote debugging on from the
    browser's start, as this refusal suggests, `browser_prepare` has an endpoint to find and attaches:
    `capability.py`'s case `attach-debugging-board-pointer`.
  - A browser of its own, `browser_prepare` with `allow_launch` and `isolated_new` (its guide's Use an isolated browser,
    which its skill prefers when a task needs no login). It launches only a root-owned file at a package's path, which
    NixOS lacks, so a VM with Chromium gets one at Arch's `/usr/lib/chromium/chromium`, a script that starts the VM's
    Chromium, for Cua alone. This route was closed in the 0.38.0 series, as the file was missing. It binds with
    `binding_quality: exact` on Hyprland, and `browser_pointer` with `delivery_mode: foreground` places the board's labels where aimed, in
    both of its variants. Its window opens on the user's workspace and takes the focus. In the background `browser_pointer`
    refuses a trusted drag (`browser_input_trust_unavailable`), and its `dom_event` route takes only a ref that declares
    `pointer`, which the board's labels do not. Passing the user's browser's `pid`, the guide's other form, launches that
    process's executable, on NixOS Chromium without its wrapper. It died with SIGILL, a failed check of Chromium's as it
    found no sandbox helper beside it, where a distribution's package has one: `--no-sandbox` or the wrapper's
    `CHROME_DEVEL_SANDBOX` alone let it start, and the library path did not. So the server has that variable.

What each app takes on the pinned release, route by route and judged by each app's own state, is in
[capability-results.md](capability-results.md), which `capability.py` writes. See [The capability check](#the-capability-check).

More that holds on Hyprland:

- A hidden Firefox showed its first frame at the size it had before it moved, until input reached it: an agent's first
  screenshot can show half a page, and a drag aimed at it misses.
- `drag` is one straight stroke, and a curve is straight segments. The plugin refuses input while a drag-and-drop is
  active (`foreground_dnd`). A drag-and-drop that a background app starts is cancelled, as the agent seats have none
  (`drags_withheld` in `cua:status`), so the board's dnd variant places no label through `drag` in either browser.
- A background popup, such as a combo box, opens without the grab on the user's keyboard, by its README
  (`popup_grabs_withheld`). This was not checked here.

`report.py` counts these refusals. `foreground_fallback`, an option of `run_steps` that Cua's hints name, is no refusal
and no longer counts as one: the published data still holds it as one, 14 times.

#### Checking a VM without an agent

`bench/cua/probe.py CID DIR` checks a VM of the Cua backend that `smoke.sh` booted, without an agent: it reads each
window but the user's terminal with `get_window_state` and writes how many accessibility elements and what screenshot
each had to `DIR/probe.json`. `--input` also sends each window a click, a key, text, a scroll and a drag in the
background and then the foreground, at the middle of the window or at `--at X,Y` of the task's, and records the
refusals, the route and whether the screenshot changed. That is a hint: Cua's result for a delivered action says
`unverifiable`, and an app can drop it, so a claim needs the app's state, in `vm-result.json` after `agent-done` is written
to DIR. `--browser` asks the browser tools for each window and starts the isolated browser.

```sh
nix-build bench/default.nix -A driver --argstr task form --argstr variant 25 --argstr backend cua \
    --arg source "$SOURCE" -o /tmp/cua-form
bench/smoke.sh /tmp/cua-form /tmp/cua-form-run 5201
python3 bench/cua/probe.py 5201 /tmp/cua-form-run --input --at 545,367
touch /tmp/cua-form-run/agent-done
```

#### A series

[The comparison series](#the-comparison-series) runs Cua Driver on the pinned release with hyprmasq beside it.

#### The series on 0.38.0

[docs/comparison.md](../docs/comparison.md) and its data `vm-cua*.json` hold Cua Driver 0.38.0, tag `cua-driver-rs-v0.38.0`,
commit `85e2b1318bd77ccf3cfa88ccdf875a4bb7eaed19`, run on 30 October 2026, without the isolated browser's file. This was its setup, which that page cites, as it stood:

- **Release:** Cua Driver 0.38.0, the newest non-nightly release of `cua-driver-rs`, from the release tag's source,
  unchanged ([cua/default.nix](cua/default.nix)). The driver is built by Cua's own Nix package with the features of
  its Linux release binary (`portal-input`), the Hyprland plugin with CMake as its README builds it, with
  `-DCUA_HYPRLAND_INPUT=ON` as its release build kit and Omarchy's package build it, against the VM's Hyprland and
  with Hyprland's compiler, and its CTest suite passes.
- **Hyprland 0.56.2:** the plugin's input build refuses any other Hyprland, and its driver sees no window of
  Hyprland 0.57.0, whose `j/monitors` leaves out a workspace's `id`. So the Cua backend runs with `--source` at a
  checkout of the `hyprland-0.56` branch, which gives the VM Hyprland 0.56.2. A comparison can run hyprmasq from the
  same checkout, and the published one sets Cua beside hyprmasq's runs on 0.57.0. `default.nix` refuses the Cua backend on another Hyprland.
- **Plugin:** loaded and enabled as its README says: `hyprctl plugin load`, then
  `hl.config({plugin = {cua = {enabled = true}}})` in the configuration and `hyprctl reload`. `vm.py` waits for
  `hyprctl -j cua:status` to report its input transport ready, and `ready.json` keeps that status as `cua_status`.
- **Server:** `cua-driver mcp`, served on the vsock port as `hyprmasq-mcp` is, with `CUA_DRIVER_RS_ENABLE_WAYLAND=1`
  (its skill's `LINUX.md`), the session's variables, and `CUA_DRIVER_RS_TELEMETRY_ENABLED=false`, as the VM is
  offline (its guide's Telemetry). It runs in Cua's default `standard` permission mode. The agent knows the server as
  `cua-driver`, the name in Cua's table of harnesses.
- **Skill:** the skill pack as `cua-driver skills install` installs it on Linux, the release's `Skills/cua-driver`
  without the other platforms' guides, in an agent home of the run's own, and named at the start of the prompt, as
  the [laptop's runs](laptop/README.md) did. Claude Code gets `CLAUDE_CONFIG_DIR` with `skills/cua-driver`, its Skill
  tool beside the server's tools, `--setting-sources user`, which in that fresh home holds the skill alone, and
  `/cua-driver ` before the prompt. Codex gets a `HOME` with `.agents/skills/cua-driver`, its skills listed, and
  `$cua-driver ` before the prompt, with `CODEX_HOME` the user's for the login and the sessions. The debrief names
  Cua Driver where it named hyprmasq. pi does not run with Cua, whose documentation has pi call its CLI.
- **Forgejo:** Cua's browser tools attach to the user's own browser with `cua-driver mcp --grant existing-profile`,
  and on Linux ask for the browser to be started with `--force-renderer-accessibility` (its guide's Attach to a
  logged-in profile). The Forgejo task's Cua runs get both. Cua's `browser_prepare` turns on Chromium's remote
  debugging itself, through `chrome://inspect`, and nothing in the VM does it for it.
- **Apps:** no app is in the input plugin's qualification, which admits only Arch's exact `libreoffice-fresh` and
  `inkscape` packages, checked in the pacman database. On NixOS every app's background raw input is refused with
  `client_not_qualified`, Cua's behaviour on a system other than Arch: its accessibility actions and its foreground
  input remain. `report.py` counts the refusals.
- **Firefox:** Cua refuses every raw action to Firefox on Hyprland: in the background with `client_not_qualified`, and
  in the foreground with `foreground_primary_binding`, as Firefox binds the user's seat more than once, which the
  plugin refuses (`unique_primary_seat` in its `input_experiment.cpp`). Its browser tools take Chromium only
  (`guides/browsers.mdx`), and their `browser_prepare` refuses on Hyprland with `browser_wrong_target_refused`, as it
  attaches through a GNOME Shell helper there. So Cua cannot do the board, trace and calc tasks in Firefox, and a
  comparison runs them for Cua with `--browser chromium`, where its foreground pointer input works. In Chromium:
  - Board's pointer variant: foreground drags place the labels where aimed.
  - Board's dnd variant: a foreground drag starts the page's drag-and-drop and is then refused with
    `foreground_dnd`, as the plugin refuses input while a drag-and-drop is active, so no label moves.
  - Trace: each foreground `drag` draws a straight stroke. Cua has no path of several points on Hyprland:
    `mouse_button_down` and `mouse_drag` are X11's and do not run in `run_steps`, and Shift and a click is refused
    with `modified clicks are unsupported`, so a curve is straight segments.
  - Background input is refused with `client_not_qualified`, as in every app.

### hypruse and computer-use-linux

`--backend hypruse` and `--backend computer-use-linux` run the task with one of two Linux computer-use MCP servers that
claim Hyprland support, in place of hyprmasq, each set up as its own documentation sets it up on Hyprland, for a
comparison on the whole suite with `--prompt comparison`. Both run as released and without hyprmasq's plugin or any
other, on the Hyprland of `--source`: 0.57.0 in the published runs, and in [the comparison series](#the-comparison-series) the
same 0.56.2 as Cua Driver and hyprmasq, on Omarchy's desktop. They keep their defaults, but for computer-use-linux's
Hyprland background mode below. The task, its VM, its grading, the timeouts (the
agent's, and 600 s a tool call) and the agent's isolation are the same as for the other backends: no shell, and the
tool's MCP server alone, behind `mcp_tap.py` on the vsock port. The tables list each aspect of the setup, what the
tool's documentation says, and what the suite does, with every difference and its reason.

[hypruse](https://github.com/IlyasKhallouki/hypruse) ([hypruse/default.nix](hypruse/default.nix)):

| Aspect | Its documentation | The suite | Difference and why |
| --- | --- | --- | --- |
| Release | PyPI `hypruse`, run with `uvx hypruse` | 0.13.0, the newest, from its PyPI sdist (SHA-256 `321362…e114b`), unchanged, built with nixpkgs' Python 3.14 and `mcp` 1.29.0, its 1040 unit tests passing | Nix in place of uv, as the VM is offline. `mcp` is in its range, `>=1.21.1,<2` |
| Hyprland | 0.54+ for a window's own screenshot, both config managers | The Hyprland of `--source` with the Lua configuration. Its `doctor` passes every check | None |
| Programs | `grim` (1.5+), `wtype`, `busctl`, optional ImageMagick for `marks` and `wl-clipboard` for the opt-in clipboard tool | grim 1.5.0, wtype 0.4, systemd's busctl and ImageMagick with Ghostscript on its PATH | ImageMagick's build with Ghostscript, whose fonts draw the marks' numbers, which the build without cannot. No wl-clipboard, as the clipboard tool is off by default |
| libxkbcommon | Found with `ctypes.util.find_library`, through ldconfig on other distributions | The wrapper gives `LD_LIBRARY_PATH` and `ld`, with which Python finds it on NixOS | NixOS has no ldconfig cache. Without it, keys to a hidden window are refused |
| Server | `claude mcp add -s user hypruse -- uvx hypruse`, a stdio server named `hypruse` | `hypruse` on the vsock port, named `hypruse` for the agent, with the session's variables | None |
| Screenshots | `HYPRUSE_SCREENSHOT_MODE=file` by default, `image` for a client that renders MCP images and cannot read files (its README and `server.json`) | `HYPRUSE_SCREENSHOT_MODE=image` | The default writes the capture in the VM and returns its path, which the agent on the host, without a file reader, could not open |
| Background mode | Automatic by default: actions on a window other than the user's run in the background, the pointer is refused there unless `background=false` | Automatic, unset | None |
| Other settings | All opt-in: read-only, confinement, strict, mark, journal, dry run, clipboard. The authentication guard on | Defaults | None |
| Skill | A skill that teaches its shell verbs, "for agents that run shell commands", installed with `hypruse skill install` | Not given. The server's own instructions reach the agent | The agent has the MCP server and no shell, and the skill's verbs need one |
| `launch` | Runs a command through a shell, behind the client's approval | Kept, as in its default tool set | hyprmasq's `launch` also starts any program. The tasks' apps are running already |
| Browser | Firefox exposes its accessibility tree. Chrome without `--force-renderer-accessibility` has none | Firefox, the tasks' default. Forgejo's Chromium, as for every backend, started with `--force-renderer-accessibility`, as for Cua | The user's Chromium gets the flag that its README names, as a user of hypruse would start it. Without it, `ui` finds nothing in Forgejo's window |

[computer-use-linux](https://github.com/agent-sh/computer-use-linux) ([computer-use-linux/default.nix](computer-use-linux/default.nix)):

| Aspect | Its documentation | The suite | Difference and why |
| --- | --- | --- | --- |
| Release | `cargo install computer-use-linux`, its plugins, npm or prebuilt binaries | 0.7.47, the newest on 7 November 2026, its crate from crates.io (SHA-256 `789c213a…d2ad`), unchanged, built as `cargo install` builds it with the crate's `Cargo.lock`, all three binaries in one directory, and the published runs' 0.7.32 before it | Nix in place of cargo, as the VM is offline. The skill comes from the release's tag, as the crate no longer holds the plugin |
| Hyprland | Window targeting with `hyprctl clients -j` and `hyprctl dispatch`. Since 0.7.35, `COMPUTER_USE_LINUX_HYPRLAND_BACKGROUND=1` to work on a window without focusing it, opt-in: its README's support matrix and settings and its skill name it, its Codex plugin forwards it, and its quick start does not set it | The Hyprland of `--source`. Its `doctor` reports no blocker, and the Hyprland backend lists and focuses windows. **Background mode on** | The docs leave it to the user. A comparison's prompt says the user prefers that their window stays alone, so a user who read the docs for this would set it, and it is the tool's best shot: a hidden window's screenshot and keys work without moving the focus, shown by the form's own events in a VM without an agent. Its pointer still goes through the user's pointer and focuses the window |
| Pointer | The absolute uinput pointer: `/dev/uinput` for the user, through the `input` group, the udev rule its `install.sh` prints, and the module loaded | All three: the rule, alice in `input`, `uinput` loaded at boot | None |
| Keys | A keyboard-capable RemoteDesktop portal, xdotool on X11, or ydotool 1.0.3+ with `ydotoold` as the user service its `install.sh` writes | ydotool 1.0.4 and that service, unchanged. xdg-desktop-portal-hyprland has no RemoteDesktop portal | alice gets a group of her own number, 1000: the service's `--socket-own=%U:%U` names it, as on the distributions `install.sh` supports, where each user has one, and NixOS's alice is in `users` |
| Text | `wtype` on Hyprland | wtype 0.4 | None |
| Screenshots | GNOME Shell, else the XDG Screenshot portal, which may ask for consent on the first capture | xdg-desktop-portal 1.22.1 with xdg-desktop-portal-hyprland 1.4.1 for Screenshot, started with the session's `graphical-session.target`, and the screenshot permission granted in the portal's permission store | The portal asks once, "Allow Apps to Take Screenshots?", and keeps the answer. Here the user has answered yes, as they would have before the agent's first screenshot. Nobody could answer in the VM |
| Accessibility | AT-SPI on, `toolkit-accessibility` true (`setup`) | The suite's accessibility session, the same for every backend | None |
| Server | `claude mcp add --scope user computer-use-linux -- computer-use-linux mcp`, the plugins' `computer-use-linux` | `computer-use-linux mcp` on the vsock port, named `computer-use-linux`, with the session's variables, which its Codex plugin forwards | None |
| Settings | None needed. The indicator on, `run_shell` and the completion cue off | Defaults: the indicator draws on the user's screen | None |
| Skill | Its Claude Code and Codex plugins ship the skill `computer-use-linux` | The plugin's skill, delivered as Cua's: in an agent home of the run's own, and named at the start of the prompt, `/computer-use-linux` or `$computer-use-linux` | As for Cua, the skill is named in the prompt, which the laptop's Cua runs did |
| Browser | Its results ask to relaunch Chromium with `--force-renderer-accessibility` when it exposes no tree | Firefox, the tasks' default. Forgejo's Chromium, as for every backend, started with `--force-renderer-accessibility`, as for Cua | The user's Chromium is started as its results ask, which the agent could not do itself |

`ready.json` keeps each tool's own readiness report as `doctor`: the lines of `hypruse doctor`, or the readiness,
input and windowing of `computer-use-linux doctor`. With computer-use-linux, `vm.py` starts the portal's session and
grants the screenshot permission before the agent starts, and `vm-user-journal.log` keeps alice's journal, with the
portal's and ydotoold's logs. pi does not run with either.

Both act with the user's own pointer, at points on the screen. The board and trace attributions take a pointer call's
window to be the one Hyprland had focused when the call started (`vm-events.log`), and `report.py` counts hypruse's
refusals by kind (`background`, `auth_guard`, `confinement`, `layer`, `session_locked`, `window_moved`) and
computer-use-linux's (`not_sent`, `refused`, `focus_unverified`).

`bench/hypruse/probe.py CID DIR` and `bench/computer-use-linux/probe.py CID DIR` check a VM of each backend that
`smoke.sh` booted, without an agent: the tool lists the task's window on its hidden workspace, reads its screenshot
and its accessibility tree, and presses Escape in it. They write what each call gave to `DIR/probe.json`.

### API keys

`claude-api` and `pi` use API keys of this machine, so `dispatch.py` runs `pi` only here, and `claude-api` here or on
a machine that is named to get the key (see [API keys on other machines](#api-keys-on-other-machines)).

- `claude-api` gives Claude Code `ANTHROPIC_API_KEY`, in that process's environment alone, from
  `$HYPRMASQ_BENCH_ANTHROPIC_API_KEY` if it is set, or else from `pi auth print-api-key --provider anthropic`.
  The request headers of pi's `anthropic` provider in `~/.pi/agent/models.json`, such as `anthropic-workspace-id`
  for a key that is not scoped to a workspace, go along as `ANTHROPIC_CUSTOM_HEADERS`; `$HYPRMASQ_BENCH_ANTHROPIC_HEADERS`,
  `Name: value` lines, replaces them. Before the VM boots, `claude auth status` with that environment must report the
  API key (`authMethod` `api_key`, `apiKeySource` `ANTHROPIC_API_KEY`), or the run fails; and an agent or debrief whose init
  event names other credentials, or none, is killed at once and its run fails. `run.json` records the check (`auth`) and
  what the init events of the run and the debrief said (`api_key_source`, `debrief_api_key_source`). The key goes
  into no file, argument or log, and not into the VM driver's or the other harnesses' environment, which get no Anthropic
  variables at all; after the run,
  a run whose files hold it fails without `summary.json`.
- `pi` reads its keys itself, from `~/.pi/agent/auth.json` (or `$PI_CODING_AGENT_DIR`), which a run links to and does
  not copy.

## On several machines

Runs are independent, so `bench/dispatch.py` spreads them over several machines, one fresh VM per run as before,
and copies each run's directory back into one results directory, laid out as `run.py` lays it out, with its
`source.json` and `report.md`. `report.py`, `analyze.py` and the tasks' scripts read it as they read `run.py`'s.

```sh
python3 bench/dispatch.py --host local,lock=/tmp/vm.lock --host me@box --host me@server,local-lock=/tmp/server.lock \
    --task forgejo --agent codex:gpt-6.1-sol --reps 6 --source ../hyprmasq-main --out bench/results/my-run
python3 bench/dispatch.py --host ... --source ../hyprmasq-main --source ../hyprmasq-feature \
    --out bench/results/main --out bench/results/feature   # two builds, each into its own directory
python3 bench/dispatch.py --host ... --task forgejo --task board:dnd --task form \
    --out bench/results/suite                              # three tasks in one queue, a directory each
python3 bench/dispatch.py --host ... --reps 0              # only ship the commits and build the drivers
```

It takes `run.py`'s options, `--source`, `--out` and `--task` repeatable, and a `--host` for each machine:

- `local`, this machine, or an SSH destination such as `user@machine`, which this machine reaches with its keys and
  without a prompt; `,slots=N` runs N VMs there at once (by default one, as runs on one machine compete for its CPU,
  see below); `,cid=N` is the vsock CID of the first slot's VM, by default 4242, the next slot's one more. Each
  run's VM keeps its disk and sockets in `$XDG_RUNTIME_DIR/hyprmasq-bench-vm-CID`, so that the slots' VMs do not
  share them;
- `,cores=N`: each slot's VM runs on N physical cores of its own, with their SMT siblings, through `run.py --cpus`.
  The slots take the machine's last cores, from the highest down, and the first ones stay to the agents and the
  host, which may not be left without a core: `slots=3,cores=2` takes 6 of 8 cores, CPUs `6-7,14-15`, `4-5,12-13`
  and `2-3,10-11` on a Ryzen 7 5800X3D, and is refused on a 6-core Ryzen 5 5600X. The dispatcher reads the cores
  from the machine's `/sys/devices/system/cpu` and prints each slot's CPUs before the machine takes runs. Without
  it, the VMs run on any CPU, as before. The VM has 4 vCPUs: with 2 cores, each vCPU has a thread of its own;
- `,lock=FILE`: `run.py --lock FILE` on that machine, a lock that its other VM users take there, shared by the slots
  and exclusive for those users, see [Running](#running);
- `,local-lock=FILE`: a lock on this machine that the dispatcher holds shared, as `run.py --lock` holds its own,
  while that machine's VM is up, for a machine whose other VM users take their lock here, such as VM tests that this
  machine runs on a remote Nix store;
- `,dir=DIR`: where the machine keeps the dispatcher's files, by default `~/.cache/hyprmasq-bench`;
- `,agents=PATTERN+PATTERN`: the agents the machine takes, each `HARNESS`, `HARNESS:MODEL` or
  `HARNESS:MODEL@EFFORT` with `*`, such as `agents=codex+claude:*opus*`; by default all. A pattern without `@` takes
  the agent at any effort, and one with `@` only at the efforts it matches, such as `codex:*@high`.
- `,api-keys`: the machine takes `claude-api` runs too, with this machine's Anthropic key, see
  [API keys on other machines](#api-keys-on-other-machines).

A lock is shared among the slots of every host that takes it, so slots run their VMs at once, and a VM test that
takes it exclusive with `flock FILE` waits for the runs in progress.
To run the bench VMs and this repository's VM test on one machine, give the lock that the VM test takes first, such
as `lock=/tmp/vm.lock` for `flock /tmp/vm.lock flock /tmp/hyprmasq-vm.lock nix build …`.

Two more options apply to all hosts:

- `--balance`: every agent's units of every task go to the hosts that take the agent in equal shares, as far as the
  counts allow, instead of to whichever slot is free. A host that is done with its share of an agent takes other agents' units,
  or waits. The wall time, the time in tools and hyprmasq's own time depend on the machine's CPU, so without it a
  faster machine runs more of each agent's runs, and an agent whose runs come first lands mostly on one. Pass rate,
  tokens and cost do not depend on the machine. A host that is dropped, or cannot be prepared, leaves its share to
  the others, and a unit that goes to another host after a failure counts for no share. The shares count the units
  of this dispatch, not the runs that a resume skips.
- `--max-concurrent PATTERN=N[,PATTERN=N]`, repeatable: at most N units at once, over all hosts and tasks, of the
  agents that match PATTERN, a pattern as in `agents=`, such as `--max-concurrent codex=4,claude-api=3`, so that a provider's rate
  limits do not stall runs and inflate their wall time. An agent counts against every pattern it matches. A slot that
  comes for an agent whose limit is full takes another agent's unit, or waits. A pattern that matches none of the
  agents is refused.

`run.json` of a dispatched run records `dispatch`: its `slot`, `balance`, the limits that applied to its agent
(`max_concurrent`) and `waited_seconds`, how long a full limit held its unit back from free slots before it started.
For example, three slots here, three on a 12-thread server with one core each, and the API runs limited:

```sh
python3 bench/dispatch.py --host local,slots=3,cores=2,lock=/tmp/vm.lock --host me@server,slots=3,cores=1 --balance \
    --max-concurrent codex=4,claude-api=3 --task form --reps 6 --source ../hyprmasq-main --out bench/results/my-run
```

Run [the calibration](#calibration) on a machine before giving it more than one slot.

### Several tasks

`--task TASK[:VARIANT]` is repeatable, such as `--task forgejo --task board:dnd --task trace:river`. A task without a
variant takes its default, as in `run.py`, and `--variant` goes only with a single `--task`. The runs of all tasks go
into one queue, so that no slot waits for the other tasks' last runs before the next task starts. Repetitions go
round the tasks and each task's round goes round the agents, so that an agent's runs, and a provider's, spread over
the whole dispatch. `--balance` gives each machine an equal share of every agent's runs of every task, and
`--max-concurrent` counts the runs of all tasks. Every machine builds the drivers of all tasks before it takes runs.

Each task gets a results directory of its own, `TASK` or `TASK-VARIANT` under `--out`, such as
`bench/results/suite/board-dnd/`, laid out as a dispatch of that task alone lays out its `--out`, with its
`source.json`, run directories and `report.md`: `report.py`, `analyze.py` and `export.py` read each as before.
Without `--out`, the directory is `bench/results/DATE-N-tasks`. With several `--source`, each `--out` holds the
tasks' directories of its build. A single `--task` writes into `--out` itself, as before. Running the same command
again resumes every task with its runs that have no `summary.json`.

### API keys on other machines

`pi` runs go only to `local`: it reads its keys from its own files, which a machine would need written. `claude-api`
runs go to `local` and to the machines named with `,api-keys`, and to no other, whatever its `agents=`. Naming
`claude-api` in the `agents=` of an SSH host without `api-keys`, or `pi` in any SSH host's, is an error, as is an
agent that no host takes.

The dispatcher gets the key and the request headers once, as `run.py` gets them here (see [API keys](#api-keys)),
and refuses to start a dispatch with a `claude-api` agent without them. It gives them to a machine with `api-keys` only on the stdin of the SSH session that starts a `claude-api` run there.
The script that starts the run reads them into shell variables and passes them in the environment of the run's
`run.py` alone, as `HYPRMASQ_BENCH_ANTHROPIC_API_KEY` and `HYPRMASQ_BENCH_ANTHROPIC_HEADERS`, before the run detaches
into its own session: so the run holds them in its memory and environment, and survives a dropped connection as
other runs do. They are in no argument, which `ps` would show, and neither the dispatcher nor `run.py` writes them
into a file or log on either machine, `run.json` and `source.json` included, or prints them. `run.py` there passes them on to Claude Code alone, as here,
and drops them from the environment of the VM driver, `nix-build` and the other harnesses. The dispatcher drops Anthropic
keys (`ANTHROPIC_*`, `HYPRMASQ_BENCH_ANTHROPIC_*`) from the environment of ssh, git and rsync.

After a `claude-api` run, on any machine, `run.py` refuses `summary.json` if a file of the run holds the key. Before
the run's directory is copied back, the dispatcher also looks for the key in its files there, with `grep` given the
key on its stdin, removes the files that hold it, and says which: the run then failed, and a resume runs it again.
For example, Codex and the API runs on two machines, and pi here:

```sh
python3 bench/dispatch.py --host local,lock=/tmp/vm.lock --host me@box,api-keys \
    --agent codex:gpt-6.1-sol --agent claude-api:claude-sonnet-5-5 --agent pi:tensorx/deepseek/deepseek-v4.1-flash-1017 \
    --task forgejo --reps 3 --source ../hyprmasq-main --out bench/results/my-run
```

### How it runs

The dispatcher refuses a checkout with changes, its own or a source's, and ships their commits: it pushes them
over SSH into a bare repository in DIR, `repo.git`, which needs no access to GitHub, and checks each out in a
worktree, `trees/COMMIT`. On each machine it then checks the tools, prints its CPU and harness versions, and
builds the drivers (`drivers/`) with `run.py`'s own code, before the machine takes runs. Each run is
`run.py --rep N --agent HARNESS:MODEL[@EFFORT]` from the shipped benchmark with `--source` the shipped source, in
`jobs/` and in a session of its own that survives a dropped connection; the dispatcher prints its progress, copies
its directory back with rsync when it ends, and removes it there. Ctrl-C interrupts the running runs, which grade
and stop their VMs, as Ctrl-C does to `run.py`.

A run that fails before its agent starts, such as a VM that does not boot, goes once to another machine; a machine
where two runs in a row fail so, or that cannot be prepared, takes no more. A run that fails after its agent
started stays as `run.py` leaves it, without `summary.json`, and is not tried again: running the same command again
resumes, as `run.py` does, with the runs that have no `summary.json`. A machine that stops answering is waited for,
with its local lock held, until it answers and its run has ended.

A machine needs Nix with KVM (`/dev/kvm`) and vsock (`/dev/vhost-vsock`), `git`, `rsync`, `python3`, `setsid`,
`pkill`, and the harnesses of the agents it takes logged in for its user, as for `run.py`, and the memory for the
VM, 6 GiB, and the agent. Its user need not be trusted by Nix. A non-interactive SSH session may lack the user's
PATH: the dispatcher adds `~/.local/bin`, mise's shims, and Nix's profiles at the end of it.

Each `run.json` records the machine, and the report adds a table per agent and machine when the runs come from
more than one, and names the machine and the harness's version of every run. The wall time, the time in tools and
hyprmasq's own time in `analyze.py` depend on the machine's CPU, since the VM draws in software. So compare two
builds only over the same machines, with the same runs on each, interleaved in time, or machine by machine. Given
several `--source`, the dispatcher does the first: each agent's repetition of every build is one unit that one
machine runs, the builds one after the other, their order alternating from unit to unit. Units go to whichever
machine is free, so a faster machine runs more of them, unless `--balance` gives each machine an equal share of
each agent's units. Read agents against each other per machine too. A run
that goes to another machine after a failure, or that a resume runs again while its unit's other builds finished,
can land on another machine than they did; the report's table per machine shows where each ran. Check the
harness versions as well: machines that update their CLIs apart can run different versions of one harness, and
`run.json` and the report's runs list show which.

`dispatch_test.py` tests the planning of several tasks, the scheduling with its shares and limits, the hosts' agents,
the slots' cores, the shared lock, and the way of claude-api's key to a machine with `api-keys`, with fake `ssh`,
`rsync`, `git` and `run.py`, without machines: `python3 bench/dispatch_test.py`. `harness_test.py` tests pi's
isolation, claude-api's key and its check, the names of runs, the reading of a recorded pi stream and the pinning of
a run's VM, with fake `pi`, `claude` and test driver: `python3 bench/harness_test.py`.

## Calibration

VMs on one machine compete for its CPU, since they draw in software. `calibrate.py` measures, without a model,
whether more slots slow the times that runs measure. For each count of VMs it boots that many VMs of a task at once,
each on cores of its own as `cores=` gives them, and once all are ready runs the same script in each: a shot of the
task's window, a scroll of 3 notches down, a shot, a scroll back up, a zoom of the middle, and a Tab and a Shift+Tab,
`--rounds` times. The calls go through `mcp_tap.py`, which times them as it times an agent's calls, and the VMs
sample the memory they use, but for forgejo, whose user works in the VM meanwhile. It reports per count of VMs the median and the 90th percentile of the seconds a call,
over all VMs' calls, by kind of call, the most memory in use in a VM and on the host, and QEMU's peak resident
memory:

```sh
python3 bench/calibrate.py --vms 1,2,3 --cores 2 --lock /tmp/vm.lock    # the form task, 20 rounds a VM
python3 bench/calibrate.py --vms 4 --cores 1 --task board --variant dnd --rounds 10
python3 bench/calibrate.py --backend cua --desktop omarchy --vms 1,3 --cores 2 --source ../hyprland-0.56  # Cua's walks
```

The counts run one after the other, holding `--lock` shared from the first to the last, as `run.py --lock` holds it
for a run. The
results go to `bench/results/DATE-calibrate-TASK/` or `--out`: `calibration.md` with the tables, `calibration.json`
with each VM's numbers, CPUs and the CPUs that QEMU's threads may run on, and a directory per VM with its MCP log and
the VM's files. Compare the counts with the first one, a VM alone. The script calls without pause, so all VMs draw
at once, which is more than agents do: they think between calls, mostly while the other VMs wait too. Other load on
the machine, which `load at start` shows, skews the numbers. A guest's peak in use, page cache aside, says how much
of its 6 GiB the task needs. QEMU's resident memory, which the host gives a VM as the guest first touches it, keeps
what the guest freed. With `--backend cua`, the script reads the window with `get_window_state`, takes a shot without a
walk, scrolls and presses a key, and the report adds the driver's own `walk_ms`, what the VM's cores change most. If it saw
another QEMU on the machine while it ran, it says not to trust the numbers.

## The capability check

`capability.py` tries each route of each tool on each app with no model, in a fresh VM per tool, mode and fixture, and asks the
app whether it took effect: the questionnaire's events, the folder, the saved drawing, Calc's cells through UNO, the mail
server's log, the board's and the drawing's saved state, Forgejo's log. Between actions it reads them through the VM's shell
(`HYPRMASQ_BENCH_SHELL`, vm.py). No result rests on a screenshot but a scroll's, which says so. A route that did not work runs
again alone, in a VM of its own, as an earlier route's popup or drag can spoil it. `capability_host.py` runs the same routes
on a real machine, in its session, with its own geometry ([capability-omarchy.json](capability-omarchy.json)).

```sh
python3 bench/capability.py --tool cua --source ../hyprland-0.56 --out bench/results/capability --lock /tmp/vm.lock
python3 bench/capability.py --tool hyprmasq --case board-dnd-chromium --source ../hyprland-0.56 --out bench/results/capability
python3 bench/capability.py --table bench/results/capability > table.md
```

A route is `works`, `refused` with the tool's codes, `dropped` where the tool said it delivered or could not tell and the app's
state did not change, or `error`. A failed route is that route's: a case proves "this route works" or "these routes refuse
on this build", not that an app cannot be automated. Cua's modes are `background`, `foreground`, `desktop` (the key bind and
then global input), `browser` (its browser tools), `walks` (20 walks of the window, hidden and in view) and `environment` (what
the desktop offers, to set beside the real machine's). `fresh`, for both tools, says whether a screenshot shows a background
change in a hidden window: a terminal that prints the time, and in a browser's fixture a page of that browser that shows it,
are moved to a hidden workspace, and the tool takes a screenshot and another 3 s later, which differ only if it shows the
window as it is.

`startcheck.py` checks the start of a run with no model: for each task of a series it boots the task's VM, asks the tool for its
first screenshot of the task's window, then shows that window and captures it with `grim`, and compares the two at `grim`'s size.
A window that a hidden workspace kept from drawing gives a blank frame or one at another size, and a large difference.

```sh
python3 bench/startcheck.py --tool cua --series main --source ../hyprland-0.56 --out bench/results/startcheck --lock /tmp/vm.lock
```

## The comparison series

[comparison-series.json](comparison-series.json) holds the rules of a comparison, committed before any result exists: the
tools with their versions and commits, the models and efforts, the prompt, the sets of tasks, the series with their machine and
repetitions, the workers, the limits per subscription, the order of the rounds, and what allows a rerun. `series.py` carries them out. The rules give each machine's options; its SSH target, and where a series' results lie on the machine
that runs `series.py` if not at their path, come from the untracked `bench/machines.local.json`, after
[machines.example.json](machines.example.json).

```sh
python3 bench/series.py check main        # the source and the harnesses on the series' machine, against the rules
python3 bench/series.py build main        # ships the commits and builds every driver
python3 bench/series.py list main --round 1   # what would run, as commands
python3 bench/series.py run main          # runs, and resumes where it stopped
python3 bench/series.py status main       # what is done, passed and excluded
python3 bench/series.py estimate main     # how many runs, and how long
```

The rules hold five series, each with a results directory of its own: `main`, Cua Driver and hyprmasq on every task and
model, `cua-attach`, Cua Driver with the user's Chromium started with `--remote-debugging-port=0` (`run.py --chromium-options`)
on the tasks in Chromium, a slice never pooled with `main`, `hyprmasq-machine-a`, one round of hyprmasq on Cua Driver's machine, machine A,
`cross-check`, a few repetitions of the same on another machine, published as how much the machine moves the numbers
and never pooled with `main`, and `others`, hypruse and computer-use-linux after `main`, on its machine. A machine moves the numbers
in two ways. It sets how long everything takes, and it sets where a walk ends against Cua's default budget of 2 s for a first read
and 1 s after. An agent whose first read was cut names a longer budget and pays for the whole tree on each read from there on,
which `walk` in `summary.json` shows as `budget_named` and `first_partial`, and that is the agent's reaction and not the machine's speed.

In `main` each tool has its machine, and both run at once, in the same period: Cua Driver on machine A (Ryzen 5 5600X), whose
walks cost least, on `hyprland-0.56` at `89c988d`, and hyprmasq on machine B (a laptop with Omarchy, Ryzen 7 PRO 4750U), the slowest of the three, on `8935ac7`, the same branch with
the fix of a defect that closed apps on a real Omarchy as their popups closed. `hyprmasq-machine-a` and `cross-check` measure how much
the machine moves each tool, per call. `placement` in the rules records why and when, and the two sources differ in that fix
alone. `--tool cua` selects one tool's half, which runs as a process of its own, and `pace` makes hyprmasq's round N wait until
Cua Driver's has started. Before a tool's first round, a machine with a `gate` calibrates it on the gate's tasks with 1 VM and with
its workers, and the series stops if the tool's median call is slower by more than the gate allows (`gate-MACHINE.json`). A
subscription's limit never ends a round: its runs are excluded and run again once it reset, and such a pass does not count
towards the passes a round may take. `amendments` in the rules lists every change with its date, and `analysis` how the results
are read, fixed before the runs. The comparison runs Sonnet and Terra. `main` ran its rounds 1 and 2
at `3a2f692`, `main-rest` runs rounds 3 to 6 at the fix commit (a series' `rounds`), and `forgejo-fix` Forgejo's rounds 1 and 2
again, as v2's start step at `3a2f692` gave the focus to the terminal and not to the user's notes, where Forgejo's user types.
The runs they replace, and Opus's and Sol's, stay on disk and out of the analysis. Opus's and Sol's runs of rounds 1 and 2 stay on disk, unpublished, and every claim is
scoped to this VM setup, with the effects known to favour either tool as a short list (`analysis.scope` in the rules).
`export.py --registered` marks a dataset of the
series, and `render.py` then follows the rules' `analysis`: pass rates with Wilson intervals, a time only where a tool passed at
least 3 runs, failed runs' times apart, and the runs that left the user's view as it was and kept the user's work.

`review_failures.py` attributes the failed runs by `analysis.failure_attribution`. `bundle` writes a directory per failed run from
a tree of the published runs, without images, host names, home directories or pass rates. `run` has one Claude and one Codex
reviewer read each with read-only tools, in processes that load none of the user's settings, and it resumes. `collect` writes the
one verdict per run that `docs/comparison/data/v2-failures.json` holds and `render.py` draws, any disagreement being `unclear`.

A round is one repetition of the selected tools, their sets one after the other, in an order that rotates from round to round.
`series.py` moves a run with no `summary.json`, a run
whose harness stopped for a limit, a login or the provider's failure (`report.infrastructure_failure`), or a run that shared its
host with work outside the series (`series.other_load`: another QEMU process, or the host's guard holding for another benchmark on the host, in
`run.json`'s `host_load`), to `excluded/`, logs it in `exclusions.jsonl` with why, waits if a limit gave a time, and runs it again
on the same machine. Nothing else runs again: a run whose debrief failed keeps its result, with `debrief_error` in `run.json`.

## What a run is

```
host                                              VM (no network)
run.py ─ starts ─▶ nixos-test-driver ─ boots ──▶  Hyprland + hyprmasq, a terminal on the visible workspace,
   │                                               task apps on hidden workspaces, local task servers
   └─ starts ─▶ claude -p / codex exec / pi -p
                  └─ MCP stdio ─▶ mcp_tap.py ── vsock ──▶ socat ─▶ hyprmasq-mcp
```

1. `run.py` builds the test driver of `bench/default.nix` and starts it outside the Nix sandbox. The driver
   boots the VM and runs `bench/vm.py`, which starts Hyprland, a logger of its events, a `top` in a terminal
   on the visible workspace (the user's own work), and the task's `setup.py`. A window that the task moved to a hidden
   workspace right after it mapped has not drawn at its size, so `vm.py` shows each such workspace until the screen stops
   changing and then the user's again, with the focus back on the terminal, for every backend (`start_state` in
   `ready.json`). It then listens for MCP connections on vsock port 5000 and writes `ready.json`.
2. `run.py` starts the agent with the task's prompt and one MCP server, `mcp_tap.py`, which connects to the
   VM over vsock and logs every message with the host time. The agent has no other tools.
3. When the agent exits, `run.py` writes `agent-done`. The VM grades the run with the task's checker,
   collects screenshots and logs, writes `vm-result.json` and stops.
4. `run.py` resumes the agent's session with the debrief (`tasks/*/debrief.md`, or the variant's own), which
   tells it the grade and what was measured and asks where hyprmasq slowed it down, without tools.

The debrief is no evidence for or against a change. An agent knows only the run it had: it cannot compare with a
run without a feature, or tell which result saved it a call, and asked for a loss it names one. Judge a change by
what the agents did, before and after it; see
how the runs are read, under [Method](../docs/benchmarks.md#method).

With `--backend cua`, Cua's plugin takes hyprmasq's place in Hyprland and `cua-driver mcp` that of `hyprmasq-mcp`
(see [Cua Driver](#cua-driver)). With `--backend hypruse` or `computer-use-linux`, Hyprland runs without a plugin and
the tool's MCP server serves the port (see [hypruse and computer-use-linux](#hypruse-and-computer-use-linux)).

The VM has an accessibility session as a desktop does, whichever the backend: the AT-SPI bus on the user's session
bus, which Hyprland and every app get in `DBUS_SESSION_BUS_ADDRESS`, and `toolkit-accessibility` on, so that apps
expose their accessibility tree from their start. hyprmasq reads none of it. `run.json` records it as
`accessibility`. Runs before it, the 0.57.0 baseline among them, had no accessibility bus.

The VM is offline. Its only connection to the host is vsock, and every server the task needs runs in it. Its
clock starts at 2026-03-10 09:30 UTC on every boot. Each task fixes its app profiles and starting data so every
run of that task starts from the same screen.

### Isolation of the agent

The agent runs on the host in an empty directory, with the environment of the user minus the variables of
any agent session around it (`CLAUDE*`, `CODEX*`, `T3CODE*`, …).

- Claude Code: `--tools ""` (no built-in tools), `--setting-sources ""` (none of the user's settings, hooks
  or plugins), `--disable-slash-commands`, `--strict-mcp-config` with only the tap, and
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
- Codex: `--ignore-user-config --ignore-rules`, the shell, computer use, browser, apps, plugins, multi-agent,
  image generation, memories and web search disabled, a read-only sandbox, and only the tap as MCP server.
- Every backend's run has an agent home of its own: for Claude Code a `CLAUDE_CONFIG_DIR` with a copy of the user's
  login without its refresh token, so that a run cannot refresh it and spend the token, and for Codex a `HOME` and a
  `CODEX_HOME` with a link to `auth.json`, which Codex writes a renewed login through. Without the user's skills, and without the harness's own: Codex has
  `skills.bundled.enabled=false`, and Claude Code `CLAUDE_CODE_DISABLE_BUNDLED_SKILLS` and `--settings` turning off the
  skills and plugins that leaves. A transcript's init and first request list the backend's skill alone, or none.
- With Cua or computer-use-linux, Claude Code and Codex get its skill in that home, as [Cua Driver](#cua-driver) describes,
  with the tools that read a server's resources. A backend without a skill stays without one.
- Harnesses do not update themselves during a run: Claude Code has `DISABLE_AUTOUPDATER`, and Codex
  `check_for_update_on_startup=false`. A series also runs them from a directory it pinned, through `dispatch.py`'s `bin=`.
- Before a run of the Claude subscription, `run.py` renews its login if it ends within an hour, more than a run can last,
  one process at a time, as its refresh token works once. It has Claude Code refresh a private copy, puts that copy in
  place of the user's file only if the expiry moved forward, and otherwise fails the run before its VM boots, with the
  user's file as it was.
- pi: an agent directory of the run's own (`PI_CODING_AGENT_DIR`), with links to the user's `auth.json` and
  `models.json` and nothing else of theirs: an `mcp.json` with only the tap, its tools declared to the model
  directly (`exposure: direct`, without codemode), and a `settings.json` that trusts no project. So none of the
  user's MCP servers, settings, packages, extensions, skills, prompts or `AGENTS.md` load. The command adds
  `--tools 'mcp__hyprmasq__*'` (no read, bash, edit or write), `--no-extensions -e builtin:mcp` (the MCP client
  alone), `--no-skills --no-context-files --no-prompt-templates --no-themes --no-approve --offline`, and a session
  directory of the run's own. The debrief resumes the session with `--no-tools` and no MCP client.

### Effort

`@EFFORT` after the model gives the harness a reasoning effort, in the harness's own way; the effort follows the
last `@`, since pi's models have `/` and `:` in them. A level the harness does not take is refused before any run
starts.

| Harness | Option | Levels |
| --- | --- | --- |
| `claude`, `claude-api` | `--effort` | `low`, `medium`, `high`, `xhigh`, `max` |
| `codex` | `-c model_reasoning_effort=…` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, the levels the OpenAI API takes; a model may take fewer |
| `pi` | `--thinking` | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |

The debrief resumes at the same effort. Without `@EFFORT`, the harness runs at
its default for the model: Codex, which ignores the user's config, at its own (`low` for `gpt-6.1-sol`), and Claude
Code at its own (`medium` for Sonnet 5.5 and Haiku 5.5). pi has no default of the model's own: it starts at its
`medium`. pi clamps any level, its default or the one given, to what the model's `thinkingLevelMap` in `models.json`
supports: the DeepSeek models have only `high` and `max`, so a lower level runs at `high`.

`run.json` records `effort`: `requested`, the level given or `null` for the default, `ran_at`, what the harness says
it ran at, and `reported_by`, where it says so:

- Codex, the `reasoning_effort` in the `x-codex-turn-metadata` of each tool call in `mcp.jsonl`; its JSON events
  have none;
- Claude Code, the `output_config.effort` of each request its transcript logs; its stream-json has none, only
  `per_turn_effort_active` in the init event;
- pi, the session's `thinking_level_change`, also recorded as `thinking`.

`ran_at` is `null` where the harness says nothing, such as Codex in a run without tool calls, and has the levels
joined with `/` where they changed during the run. The report shows the effort beside the agent, such as `high` or
`default, ran low`, for runs from before `run.json` recorded it too, from the same files. The same agent at different
efforts is a separate agent: each has its own run directories, `codex-gpt-6.1-sol-high-1` for
`codex:gpt-6.1-sol@high`, and its own row in the report, and a finished run counts only for its effort when a run
resumes. A model whose name ends in a level shares those names, as `codex:gpt-high` and `codex:gpt@high` do: a
results directory that holds a run of one refuses the other.

The sessions' transcripts are moved from `~/.claude/projects`, `~/.codex/sessions` and pi's session directory into
the run's directory after the debrief, and pi's MCP log, if it wrote one, to `pi-mcp.log`.

## Measures

`summary.json` of a run, and the report's table:

| Measure | From |
| --- | --- |
| `effort` | The effort asked for, `requested`, the one the harness ran at, `ran_at`, and where it says so, `reported_by`, from `run.json` (see [Effort](#effort)). |
| `passed`, `failures` | The task's checker, from the VM's state. |
| `wall_seconds` | The agent process, from start to exit, on the host. |
| `tool_seconds` | The union of the tool calls' intervals in the MCP log: time spent in hyprmasq, the vsock and the VM. |
| `outside_tool_seconds` | The rest: the model's reasoning and output, and the harness. |
| `first_call_seconds`, `longest_gap_seconds`, `median_gap_seconds` | Time before the first call, and the longest and the median stretch between calls: the model's and the harness's time per step. |
| `calls`, `by_tool`, `actions` | Tool calls by tool, and the actions of all `act` calls, or Cua's action calls and the steps of its `run_steps`. |
| `actions_requested`, `actions_attempted`, `actions_done` | The actions asked for, pauses aside, those the tool sent, and those it says it did: a batch stops at its first failure, and a script's log says each operation. |
| `images`, `image_kib` | Images in results, and their size. |
| `no_change`, `errors` | Results whose summary says `no change`, and tool errors. |
| `refused_calls`, `refused_kinds`, `script_refusals` | Cua's calls that did not deliver, by their result's status and not by what their text mentions, and what they were: `setup`, the browser tools asking for it, `stale`, `route`, `route_uncoded` (a route that said it did not deliver without a code) or `partial`. A failed call that is no refusal, such as an element that is not there, is an error. `script_refusals` counts the refusals that a `run_script` caught inside it. `refusals` counts each code in the calls it turned up in: one call can name several, so never sum those. `get_guide`'s text refuses nothing |
| `walk` | Cua's walks of the accessibility tree in the run: their `seconds`, `count`, how many its time budget cut as `partial`, apart from those that stopped at its element budget (`partial_elements`) or at a container too large to list (`partial_container`), `median_ms`, how many had a budget the agent named as `budget_named` (a `run_script`'s own `timeout_ms` bounds the script and is none), and `first_ms` and `first_partial` of the first. A script's walks count once, from its log. This is the part of its time in tools that a VM makes slower than a desktop, by a ratio [capability-results.md](capability-results.md) gives. `null` for the other backends |
| `frames` | Screenshots per window: `first_to_later`, the size of a window's first against the median of its later ones, `first_small` when it is under half, and `unchanged_after_input`, screenshots the same as the window's previous one although an input to it worked between them: a frame that may be stale, or an input with nothing to see. Flags, not failures. |
| `focus_takes`, `first_focus_take_seconds` (in `user`) | How often the focus left the user's window, and when first. A tool that does not give it back counts as away until the run ends, and a comparison's prompt authorizes it, so these go beside the seconds away |
| `final`, `error_texts`, `infrastructure`, `assignment` | What the failure analysis needs: the agent's last message, the first line of each call that failed, why the harness itself stopped (`rate_limit`, `auth` or `provider`), and the machine a series gave the run. The checker's `failures` are there already |
| `refusals` | The tool's refusals by code or kind, such as Cua's `client_not_bound` or `foreground_grab`, or hypruse's `background`: how many calls turned each up. |
| `phases` | The task's milestones from the VM's state, in seconds after the agent's start, such as `account verified` and `reply sent` for Thunderbird. |
| `checks` | The checker's details, such as the user's notes for the Forgejo task. |
| `user` | From Hyprland's events in the VM: windows that opened on the user's visible workspace, the seconds the keyboard focus was away from the user's window, and switches of the visible workspace, `no_view_change` when there were none; desktop input that Cua Driver delivered while the user's window held the focus (`desktop_input_to_user_window`: only steps the tool says it ran and did not fail, a batch's at its start, and never a `run_script`'s operations, which name no target; hyprmasq sends no desktop input, so it is Cua Driver's alone), its desktop hotkeys that match a compositor bind of `omarchy-desktop.lua`, as a workspace switch, apart (`desktop_shortcuts`), and how far the user's pointer was from where it started when the run ended (`cursor_displacement_px`: a pointer that moved and came back has 0). None of it sees what changed inside the user's window, and a run that did little has `no_view_change` as easily as a careful one: report it beside the result. |
| `user_work` | Whether the user's own work survived, where the user works during the run (Forgejo): `kept` when all they typed is saved and nothing went elsewhere, with `lost_chars`. Apart from `passed`. |
| `start_state` | What `vm.py` showed before the run so that hidden windows drew: each workspace, how long, and whether the screen settled. |
| `usage` | Tokens, with the cache reads and writes among the input tokens, and turns for Claude Code and pi (whose tokens and cost include its compaction requests), from the agent's event stream, with its final answer. The cost in USD is the one Claude Code and pi compute. For Codex it is what the run's requests in its transcript would cost at OpenAI's API list prices (`OPENAI_PRICES` in `report.py`), each at the prices for its prompt's length. |
| `context` | How large the agent's context got, from the prompt tokens of each of the run's requests, cache reads and writes included: `request_count`, `first_input_tokens`, `peak_input_tokens`, `last_input_tokens`, `growth_input_tokens` (last minus first, which a compaction can make negative) and `compactions`, the times the harness compacted the session's history. Codex's come from the `token_count` records of its transcript, the requests until they add up to the run's totals, and the `compacted` records up to the last of them. Claude Code's come from the usage of each assistant message in its stream and its `compact_boundary` events, pi's from the usage of each reply in its stream and its `compaction_end` events. The debrief's requests do not count. `null` where the logs do not give the size of every request, and `compactions` is `null` where Codex announced more compactions than it recorded. `laptop/analyze.py` computes it the same way. |

The per-call log is `mcp.jsonl`; `report.tool_calls()` pairs its requests and responses. The agent's events
are in `agent.jsonl`, each with the host time it arrived. `report.py` computes the summaries again from these
files, so a change to the measures applies to old runs too.

`analyze.py RESULTS-DIR` counts frictions per run: wait-only `act` calls, pauses and wait overrides the agent
added, new windows and whether the `act` that opened them reported them, follow-up calls, windows on the
user's workspace, errors from input that closed its window and wait timeouts. It also splits the time of the
`act` calls into parts:

| Part | What it counts |
| --- | --- |
| `wait_s` | The wait for the window to settle, as each result's `wait.elapsed_ms` gives it. |
| `watch_s` | The watch for a second redraw and new windows after the wait, after a click, drag, key or line break: the 500 ms that the `act` tool's description in the log names, or the watch's own time when the result says `drew again, settled … later`. When a redraw in the watch makes the result one wait, after a first wait without a redraw, `wait_s` holds the watch, and only the grace for new windows that is left over counts here; after a wait that timed out, nothing does. |
| `input_s` | The sender's pacing of the actions it sent, pauses aside: the gaps, holds and typed keys of `tools/hyprmasq-input.c` (15 ms a typed key), the 300 ms from a click to the keys after it, and at least 150 ms for the window's answer to a click that more actions follow. |
| `pause_s` | The pause actions the agent added. |
| `rest_s` | The rest of the calls' time, not clamped. |

The rest is what hyprmasq does beyond these waits: capturing and comparing images, checking targets that moved, the
shots of new windows and of the window that a closed one came from with their own waits, a click's answer beyond its
least, the sender's start and round trips, the locks and the transport. The results do not time these apart, so the
rest stays one number. The pacing is the least the sender needs: the logs do not record the keymap, so a key that it
reaches with Shift lacks its 10 ms, and a character typed through Compose its further keys.

Claude Code results have no structured content, so the analysis reads their summaries. The text gives no time
for a wait that the window's closing ended, so that time stays in the rest. Unparseable delivery, wait or watch
summaries stop analysis with an error, rather than assigning guessed counts or times.
`analyze_test.py` checks that a result's text gives the same split as its structured content for the keys of a
partly typed action and a cancelled call: `python3 bench/analyze_test.py`.

## Files of a run

| File | Contents |
| --- | --- |
| `summary.json` | The measures above. |
| `run.json` | Harness, model, version, the agent's start, end, exit status and session id, and the machine: host name, CPU, threads and memory. The CPUs the VM ran on (`cpus`, `null` for any), the host's load while the VM was up (`host_load`: every 15 s the load average, the CPUs' clock, the benchmark's own and any other QEMU processes and whether the host's guard held, with their worst) and, for a dispatched run, its slot and the limits it waited for (`dispatch`). The effort asked for and the one the harness ran at (see [Effort](#effort)). For Claude Code, the credentials its init events named; for pi, the thinking level. The `backend`, the `prompt`, the accessibility session (`accessibility`), and `timeouts`: the seconds the agent and one tool call may take, the same for both backends (600 s a tool call, for every harness). |
| `agent.jsonl`, `agent.stderr` | The agent's event stream, timestamped. |
| `mcp.jsonl` | Every MCP message, timestamped, with images replaced by their size and hash. |
| `ready.json`, `vm-result.json` | The VM's state at the start; the grade, the phase events and the state at the end. |
| `start-screen.png`, `end-*.png` | The screen at the start, for every backend, and at the end the screen and, with hyprmasq, every window. |
| `sent-1.eml`, `user-notes.txt`, `sheet.ods`, `vm-board.json`, `vm-form-state.json`, `drawing.svg`, `vm-trace-strokes.json`, `vm-files-watch.jsonl` | Task files: the first mail the agent sent (Thunderbird), what the user typed and what reached their notes (Forgejo), the sheet as saved (Calc), where the labels are (board), the questionnaire's answers, submissions and changes (form), the drawing as saved (inkscape), the drawing (trace), the folder at each change (files). |
| `vm-events.log` | Hyprland's events (`socket2`), each with the VM's time. |
| `vm-*.log`, `vm-*.jsonl` | Hyprland, the MCP server, socat, and the task's servers and apps, such as the browser (`vm-firefox.log` or `vm-chromium.log`), such as the intranet's requests and the watch of the sheet (Calc), or the page's events and saves (board, trace). |
| `attribution.json` | Board and trace: who each drag's or stroke's outcome is due to, written by the task's `attribute.py`. |
| `debrief-prompt.md`, `debrief.md`, `debrief.jsonl` | The debrief. |
| `transcript-*.jsonl` | The harness's own transcript of the session. |
| `driver.log` | The test driver and the VM's console. |

## By hand

`bench/smoke.sh DRIVER DIR` boots the VM for a run directory and returns when it is ready, and
`bench/mcp_probe.py CID TOOL [ARGS] [TOOL [ARGS]]... [--image OUT.png] [--log LOG]` calls tools in it, in one
session, so that `"@shot"` in a call's arguments can stand for the latest shot of the calls before it. Any MCP client can use the VM
with `python3 bench/mcp_tap.py 4242 5000 LOG` as its server, for example an interactive Claude Code session.
`touch DIR/agent-done` grades the run and stops the VM.

## Tasks

### thunderbird

Thunderbird 156 starts with a fresh profile at its account setup, on the hidden workspace `mail`. The prompt
asks the agent to add Mara's account (`mara@example.test`) and to answer Jonas's question by email. The
answer, room 4.12, is in the facilities mail of week 11. The facilities mail of week 10 names another room.

- `vm.nix`: Dovecot 2.4 (IMAP on 993 and 143, passwords in a passwd-file), the fake SMTP server, hosts
  entries for `example.test` and its `imap.`, `smtp.` and `mail.` names.
- `mails.py`: the 27 messages of the inbox, with fixed dates and Message-IDs.
- `smtp.py`: an aiosmtpd submission server on 465 (TLS) and 587 (STARTTLS) with authentication. It delivers
  nothing and spools what it gets.
- `certs/`: a test CA, valid until 2045, and a certificate for the servers, valid 2025 to 2035. The CA's key
  was deleted after signing. Thunderbird's profile trusts the CA, so the account setup shows no certificate
  dialogs.
- `user.js`: prefs that turn off first-run prompts, telemetry, updates and offline detection.
- `check.py`: passes when exactly one message was sent, to Jonas alone, from Mara, with `In-Reply-To` set to
  his question's Message-ID, a `Re:` subject, and 4.12 in the text above the quote. It also reports the copy
  in Sent and other rooms named.
- `setup.py`: the profile, Thunderbird's launch, started like a user would on the visible workspace and moved to
  `mail`, as the other tasks start their apps, and the collection of the grade and the phase events. Runs before
  the [Cua backend](#cua-driver) started it with `hyprmasq-launch`.
- `prompt.md`, `debrief.md`: what the agent is told.

### forgejo

The user's own browser: Chromium 154 with two windows of one process. The user's window, on the visible
workspace, shows a notes page in which the user types a line every 6 seconds through QEMU's keyboard, as real
input, for the whole run. The agent's window is parked on the hidden workspace `web`; it is opened and moved
there like a user would, not with `launch`. The prompt asks the agent to sign in to Forgejo, upload
`~/Documents/release-notes-1.4.md` into `docs/` of Mara's repository `website` on `main` with a given commit
message, and connect a release dashboard to Mara's account.

The task exercises what Thunderbird does not: windows of a process that also has the user's focused window,
a file chooser that the desktop portal draws in a process of its own (`xdg-desktop-portal-gtk`), and an OAuth
sign-in in a popup window.

- `vm.nix`: Forgejo 16 on `git.example.test:3000`, the dashboard on `dash.example.test:8080`, the notes page
  on `notes.example.test:8081`, Chromium with policies against first-run screens, and the portal with its GTK
  backend. A user target binds `graphical-session.target`, which the portal requires.
- `dash.py`: the dashboard, an OAuth client of Forgejo. "Connect Forgejo" opens a popup that goes through
  Forgejo's authorization page and closes itself.
- `notes.py`: the notes page, which saves every change; `collect()` compares it with what the user typed.
- `check.py`: passes when the file is on `main` in `docs/` with the right content, in exactly one new commit
  with the given message, and the dashboard is connected to `mara`. It also reports branches and commits.
- `setup.py`: Mara's account, repository, OAuth client and file; the two browser windows; the user's typing
  (`during()`); and the grade, with the user's notes, in `collect()`.
- The variant `reacting`: the user stops typing once the focus has left their window, after 1.5 s, as a person sees it, and goes
  on once it has been back as long. What they typed before they saw it is counted in `user_notes` as `lost_chars`, how often they
  stopped as `stopped`, and `user-activity.json` has the times. Without a variant the user types on whatever happens, what
  [docs/benchmarks.md](../docs/benchmarks.md) measures, and what a tool that takes the focus types against: the keys land in its
  window, and Cua's foreground refuses while they are held (`foreground_physical_keys`).
- `prompt.md`, `debrief.md`: what the agent is told.

### calc

The user's own Firefox 157 shows five reports of the company intranet as tabs, and LibreOffice Calc 26.8 has
Mara's sheet `~/Documents/Office revenue Q4 2025.ods` open: one row per office, the months as columns, and a
summary. Both apps are started like a user would, on the visible workspace, and moved to hidden ones: side by
side on `work` when the monitor is 2560 logical pixels wide or more, below that Firefox on `web` and Calc on
`calc`. The user stays on workspace 1 with the terminal. With `--browser chromium`, Chromium shows the tabs instead.
The prompt asks the agent to enter each office's
revenue for October to December, to fill in the Q4 totals, the "All offices" row, the average per office and
month and the growth from October to December as formulas, to save the sheet, and to leave Mara's tabs open on
their reports.

The task exercises what the others do not: Gecko outside Thunderbird, Calc, and work that goes back and forth
between two apps, reading pages in one and entering cells in the other. The pages vary their layout the way
real reports do, and each has something to read carefully:

| Tab | Layout | Office | To read carefully |
| --- | --- | --- | --- |
| Region North: revenue Q4 2025 | a plain table | Hamburg, Rotterdam | a "Region North total" row |
| Stockholm: monthly revenue 2025 | a table of the year, six months a page | Stockholm | October to December on page 2, beside the budget |
| Lisbon office: updates | cards, newest first, older ones behind "Show older updates" | Lisbon | 2026's months on top; November's draft, among the older ones, next to October |
| Vienna office: quarterly report Q4 2025 | a long report, key figures at its end | Vienna | the figures are in EUR thousands |
| Dublin closes 2025 with a record December | a news article | Dublin | the figures are in its prose, with December 2024's |

The pages render their content in the browser from JSON that needs Mara's session cookie, which her profile has.
Calc does not get it: its External Data import (Sheet ▸ External Links) of a report gets the sign-in page, and
`WEBSERVICE()` of a page's JSON gives `#VALUE!` (401). So the figures reach the sheet only through the UI.

- `vm.nix`: the intranet on `intranet.example.test:80`, Firefox with policies against first-run pages, and
  LibreOffice.
- `figures.py`: the figures, the traps, the tabs and the sheet's layout, for the pages, the sheet and the grade.
- `intranet.py`: the intranet. It signs in Mara's browser while `setup.py` opens her tabs, as single sign-on
  would, and logs every request.
- `sheet.py`: Mara's sheet, written as ODF XML with fixed dates, the same file on every build.
- `user.js`, `registrymodifications.xcu`: the Firefox and LibreOffice profiles: no first-run pages, tips or
  infobars, and Firefox writing its session every 2 s.
- `watch.py`: reads the open sheet once a second through a UNO pipe that Calc is started with, and notes when
  the file changes on disk, for the milestones `first figure`, `figures entered`, `formulas entered` and `sheet
  saved`. `reports viewed` is when every tab had been shown, from the window titles in Hyprland's events.
- `check.py`: passes when the sheet was saved as ODS under its own name; holds the 18 figures, in euros, as
  numbers; holds formulas for the Q4 totals, the "All offices" row, the average and the growth that read every
  figure their result comes from, directly or through the other totals, with the results that the sheet's own
  figures give, so that a wrong figure is one failure; keeps Mara's labels and note, and has no content outside
  its layout and no other sheet; Mara's five tabs are open on their pages, from the session file of Firefox or, with
  `--browser chromium`, of Chromium (its commands of the tabs' navigations, selected pages and closings); and no
  other file in Mara's home, hidden ones aside, was added, removed or changed. A failure names the
  cell, what it holds and, for a trap, which one: `B10 (Vienna, October) is 112.4, in EUR thousands as the
  report gives it; expected 112400`. It also reports requests without Mara's session, such as Calc's, and when
  Stockholm's page 2 and Lisbon's older updates were loaded.
- `check_test.py`, `check-test.nix`: `nix-build bench/tasks/calc/check-test.nix` fills Mara's sheet through UNO
  in a headless LibreOffice in 33 ways, a right one and ones wrong in each way above, saves it, and checks each
  verdict and its failures.
- `setup.py`: the profiles, the two windows and their workspaces, a window rule that ignores Calc's request to be
  maximized, as Hyprland's example configuration does for every app, the watch, the list of Mara's files to
  compare with, and the grade with the saved sheet and the logs in `collect()`.
- `prompt.md`, `debrief.md`: what the agent is told.

### board

Mara is writing a guide to building a PC. Her Firefox 157 shows the guide's figure of her motherboard, an AM4
micro-ATX server board on a blue mat with a RAM module, a USB stick, an AA battery, thermal paste and tweezers around
it, and a tray of nine labels: CPU socket, Memory slots, 24-pin ATX power, CPU power headers, SATA ports, First PCIe
slot, USB 3.0 header, BMC chip and M.2 slots. Firefox is started like a user would, on the visible workspace, and
moved to the hidden workspace `web`; the user stays on workspace 1 with the terminal. With `--browser chromium`,
Chromium shows the figure instead (see [Running](#running)). The prompt asks the agent to put
each label on its part of the board. It names no tools and gives no coordinates.

The task exercises what the others do not: drags, and a target that only the image shows. Parts of the board look
alike: the loose RAM module is the trap for the memory slots, the M.2 slots and the SATA ports beside them for
each other, the lower PCIe slots for the first, and the heatsinks and connectors around them for the rest. A label
lands where it is dropped, the pointer's position at the drop; dragging it again moves it, and dragging it back to
the tray takes it off. The photo is scaled to fit the window, the same on every load, and the page does not scroll.

The page drags in one of two ways, the task's variants, so that each has its own results directory:

- `dnd`: HTML5 drag-and-drop (`draggable`, `dragstart`, `dragover`, `drop`), which Firefox does with Wayland's
  data-device protocol: `start_drag` and a DnD session in the compositor;
- `pointer`: `pointerdown`, `pointermove` and `pointerup` with pointer capture, as a custom widget or a canvas would
  do, which needs only pointer input.

Both look and behave the same. A press that moves less than 4 CSS pixels is a click, not a drag, in `pointer`; in
`dnd`, Firefox starts a drag-and-drop only once the pointer has moved more than 8 device pixels along an axis, GTK's
default `gtk-dnd-drag-threshold`.

- `vm.nix`: the guide on `guide.example.test:80`, with the variant from `default.nix`, and Firefox with policies
  against first-run pages.
- `guide.py`: the guide's server and its page. Every drop is saved on the server, in `board.json` (each label's
  point in photo pixels, or null in the tray), and logged with the time in `places.jsonl`, so the grade comes from
  the server, not from the screen. The page also logs every pointer and drag event it gets, with its type, client
  and screen coordinates, buttons, the label under it and the time, its layout after every change (the photo's,
  the tray's and every label's box, the scale, `devicePixelRatio` and `mozInnerScreenX/Y`), and each drop it
  applies, with the label, the point in client and photo pixels and where it went, in `events.jsonl`.
- `motherboard.jpg`: the user's photo, 2000×1296, at JPEG quality 95. The original was a PNG cut out along the mat,
  with a transparent edge, which the JPEG has on white.
- `regions.json`: the boxes of the photo, in photo pixels: `labels`, each label's part, some with several boxes,
  and `distractors`, the parts and objects a careless placement lands on.
- `check.py`: per label, where it lies and whether that is inside one of its boxes (correct), on another label's
  part, on a distractor (named), elsewhere, or still in the tray: `Memory slots is on the loose RAM module, at
  (1654.4, 679.8)`. It passes when all nine are correct, and reports the number correct for partial credit, the
  variant and the number of saves. Its milestones are `first label placed`, `all labels placed` and `last change`.
- `attribute.py RUN-DIR | RESULTS-DIR`: who each drag's outcome is due to, from `mcp.jsonl` and the page's events. It
  maps the start and end of each `act` drag, and the first and last point of each pressing `path`, from shot pixels to
  window-local logical pixels through the shot's frame, zooms included, as hyprmasq does, and to the page's
  coordinates with the content's offset in the window, which Wayland does not tell the page: the median difference
  between where the agent's presses (clicks and drag starts) went in the window and where the page saw them, paired in
  order within each `act` call, with the page's zoom (ctrl+minus) from its `devicePixelRatio`. In the VM, it agrees
  within a shot pixel with Firefox's `mozInnerScreenY`. It then finds the press the page logged for the drag and what
  followed, and classifies the outcome: `correct` (the label landed where the agent dropped it, on its part), `model`
  (where the agent dropped it, but on another part or none; or the drag grabbed no label, dropped it off the photo and
  the tray, or was too short to be a drag: aimed shorter than 4 CSS pixels in `pointer`, or in `dnd` aimed and seen
  within Firefox's drag threshold, with the release where aimed and no `dragstart`), `hyprmasq` (the page saw no
  press, the press or the drop came more than 6 CSS pixels from where the agent aimed, or the drag started on a label
  and the label did not move), `tray`, or `not sent` (`act` refused the batch). Per label, it names whose the final
  placement is. It prints a table per run and writes `attribution.json`. Results for Claude Code have no structured
  content, so it reads their text, which gives no frames: a shot of the whole window gets the size from the last
  `windows` or `screenshot` text that named the window, if its image has the size of that text's shot, a zoom the
  region of its shot, and a changes shot the frame of the shot it was compared with. It agrees with the structured
  content within a logical pixel, which a window's fractional size takes. A drag in a shot whose frame the text does
  not tell, such as one with popups outside the window or of a new window, is `unmapped`, with why.
- `check_test.py`: `python3 bench/tasks/board/check_test.py` checks the grade and the attribution on constructed
  states and logs: a correct board, a label on the RAM module, one on another label's part and one in the tray,
  the milestones, a drop far from where the drag aimed, a drag the page never saw, a cancelled drag-and-drop, a
  drag too short to start one, a drag back to the tray, a refused batch, a drag in a zoom's pixels, a page zoomed out, and the same drags from
  results with only their text, as Claude Code gets them.
- `setup.py`: the profile, Firefox's window and its workspace, and the grade with the page's logs in `collect()`.
  With `HYPRMASQ_BENCH_WAYLAND_DEBUG` set in the driver's environment, Firefox logs its Wayland protocol to
  `vm-firefox.log`.
- `user.js`: no first-run pages, tips or telemetry in Firefox.
- `variants`, `prompt.md`, `debrief.md`: the variants, and what the agent is told.

### form

A questionnaire, in one of several UI toolkits, on the hidden workspace `form`. It is started like a user would, on
the visible workspace, and moved there; the user stays on workspace 1 with the terminal. The prompt asks the agent to
answer every question as it says and press Submit at the end, in that window only.

The task measures how fast an agent gets through a chore of input, with nothing to work out: each question says its
answer. It exercises what the others do not: a native GTK 4 app, its dropdowns' popovers, sliders and many small
targets in a column that scrolls. The questions take turns among four kinds, from `items.py`:

| Kind | The question | The widget |
| --- | --- | --- |
| checkbox | Tick the options it names, among five to seven | check buttons, none ticked at the start |
| slider | Set the slider to a number, on ranges such as 0 to 1000 in steps of 5 or -1 to 1 in steps of 0.05 | a slider, a caption with its range and step, and a read-only value beside it; the value snaps to the step, and the pointer or the arrow keys move it |
| text | Type the text it shows, such as `QX-7731-B` | an entry; spaces around the text do not count |
| dropdown | Choose the option it names, among four to twelve | a dropdown that starts at "Choose…" |

The task's variants are the number of questions, from 10 to 40, the first that many of the 40 in `items.py`: 25 by
default, which is the benchmark's, 10 for a quick probe. A variant `N` runs the GTK 4 app, and `TOOLKIT-N` the app of
`toolkits/TOOLKIT.nix`, such as `qt-25`. Each toolkit's app is the same questionnaire, with the same questions,
records and state, and `check.py` grades them alike. Toolkits draw, scroll, open popups and take input in their own
ways, which is what the questionnaire in several of them tests. The questions, their order and their starting values are the
same on every run. The wheel over a slider scrolls the page, as over the other questions, and does not move the
slider.

A variant that ends in `-no-dropdowns`, `25-no-dropdowns` in GTK 4 and `qt-25-no-dropdowns` in Qt, asks no
dropdowns: the first 25 of the 30 checkboxes, sliders and texts, in the same order and numbered from 1. That is the
form without dropdowns of the earlier comparison on a laptop, which `bench/laptop` runs with
`--no-dropdowns`: the same questions and the same grade, so the VM's runs and the comparison's measure the same
questionnaire. The prompt is the task's own, which, like the comparison's, does not name the kinds of question.
`vm.nix` names the kinds, `checkbox,slider,text`, and `setup.py` gives them to the app and to `check.py` in
`$HYPRMASQ_FORM_KINDS`, which `items.py` reads. The variant's debrief, in its directory, does not ask about
dropdowns. `render.py` names its runs Form without dropdowns, as the comparison does, and leaves them out of the
toolkit views.

- `vm.nix`: the app of the variant's toolkit (`hyprmasq-bench-form STATE-DIR COUNT [STATE-FD]`), and the number of
  questions and their kinds from the variant. Its comment says what every toolkit's app must do: record its process
  id, which `setup.py` signals, show the questions in a window titled "Questionnaire", log the changes and
  submissions as `form.py` does, and write its state on SIGUSR1.
- `toolkits/`: a file per toolkit that builds its app: `gtk4.nix` for `form.py`, `qt.nix` for `form_qt.py`,
  `gtk3.nix` for `form_gtk3.py` in GTK 3 on Wayland, `gtk3-x11.nix` for the same app on X11, and `tk.nix` for
  `form_tk.py` in Tk, which runs on X11 only. For the HTML app, `electron.nix` and `cef.nix` run it on Wayland,
  `electron-x11.nix` and `cef-x11.nix` on X11, and `cef-osr.nix` in CEF rendering off-screen, on X11. `toolkits/html/`
  has what these share: the Electron app, and cefsimple and cefclient, CEF's sample browsers, built against nixpkgs'
  `cef-binary`. The X11 apps run on Hyprland's XWayland and
  take hyprmasq's X11 path. Their dropdowns' lists are windows of their own, which Hyprland reports as it does other
  windows: the toolkit file names their classes, and `check.py` does not count them as windows other than the
  questionnaire, nor any window of the questionnaire's own process, since Chromium's lists have no class. As in Qt,
  the wheel over a GTK 3 or Tk dropdown moves through its choices.
- `items.py`: the 40 questions and their answers, for the app and the grade.
- `form.py`: the app. Every change of an answer is logged with the time, and for a slider with how it moved (GTK's
  scroll type: `jump` for the pointer, `step-forward` and the like for keys), and every press of Submit with the
  answers. On SIGUSR1 it writes its state from its memory to its file descriptor 3, a file that only root can open,
  which root opened and passed to it at its start. The grade reads that file, so that only the app's own widgets
  decide it, not a process of the user's that the agent could start.
- `form_qt.py`: the same app in Qt (PySide6), with the same questions, layout, records and state, which `check.py`
  grades alike. It names a slider's moves as `form.py` does. It is for platforms where GTK exposes no
  accessibility, such as macOS, whose GTK builds lack the AccessKit backend, and for comparing tools that read the
  accessibility tree. The VM runs it for the `qt-N` variants, and [bench/laptop](laptop/README.md) with
  `--toolkit qt`.
- `form.html`, `form_html.py`: the same app as an HTML page, for browser engines. See below.
- `check.py`: passes when Submit was pressed and the last submission answers every question, and no window other
  than the questionnaire's opened while the agent worked, from a log of Hyprland's events that root keeps, out of
  reach of the programs the agent could start; without that log it fails. A window of a class that the toolkit
  file names, or of the questionnaire's own process, such as Chromium's dropdown lists on X11, which have no class,
  is the questionnaire's. A failure names the question: `Question 6 (slider) is at -17; expected at -18`. Per
  question and per kind, it reports whether the answer is right, the changes, the corrections (for a checkbox the
  toggles beyond the options named, for the others every setting but the last, where changes less than 2 s apart
  are one setting, as one `act` call makes them) and the seconds from the previous final answer to its own; for a
  slider, also how it moved. It also reports answers changed on screen after the last submission. Its
  milestones are `first answer`, `all answered correctly`, `first submit` and `last submit`.
- `check_test.py`: `python3 bench/tasks/form/check_test.py` checks the grade, the corrections and the milestones on
  constructed states: right and wrong answers of each kind, no submission, a submission from before the agent's
  start, changes after the last one, a drag, keys after a click, a checkbox ticked by mistake, another window, a
  window of the questionnaire's process, no log of the windows, and the grade of a questionnaire without dropdowns,
  run as `setup.py` runs it.
- `setup.py`: the app, started with its state file and the variant's kinds, its window and its workspace, root's
  log of Hyprland's events, and in `collect()` the app's state, asked of the app's own process, its logs and the
  grade.
- `windows.py`: root's log of Hyprland's events, with the process of each window that opens, which it asks
  Hyprland for.
- `variants`, `prompt.md`, `debrief.md`: the numbers of questions, 25 the default, and what the agent is told.
  `25-no-dropdowns/debrief.md` is the debrief without dropdowns, which `qt-25-no-dropdowns/` links to.

The HTML app is one page, `form.html`, with the same questions, layout and records as `form.py`: checkboxes,
`<input type=range>` sliders with their caption and read-only value, text fields, native `<select>` dropdowns,
which open a popup in every engine, and Submit, in one scrolled column. A slider's pointer moves are `jump` and its
keys `step-forward`, `page-backward`, `start` and the like. `form_html.py` is the process that the launcher starts
and records. It serves the page on 127.0.0.1 under a random path, gives the page the questions of `items.py`,
starts the engine on the page's URL as its child, and quits with it. The page sends it every change and every press
of Submit, in order, and the server keeps the state in its memory, logs it as `form.py` does, and writes it on
SIGUSR1. The engine does not inherit the state's file descriptor.

### trace

Mara is writing a blog post and marks up its figure, a photo, with a brush in her Firefox 157: the prompt asks the
agent to draw a line along a line of the photo, and nothing else. It names no tools and gives no coordinates. Firefox
is started like a user would, on the visible workspace, and moved to the hidden workspace `web`. The user stays on
workspace 1 with the terminal. With `--browser chromium`, Chromium shows the figure instead. The task has one figure,
its variant `river`, a directory with its photo, the line to
trace (`track.json`), the page's texts (`figure.json`) and its prompt (`prompt.md`):

- `river`: a river from above, where it bends through a gorge, 731×600 pixels. The agent draws along
  the left bank where it lies in the shadow of the hill, where the water meets the shore. The waterline is a sharp
  edge between the water and the stones, and the page shows the photo at about 1.5 times its size at 1920×1080.

The photo is not under the repository's MIT licence. `river/photo.jpg` is cropped and scaled down from
[Loreleyblick Maria Ruh 2025](https://commons.wikimedia.org/wiki/File:Loreleyblick_Maria_Ruh_2025.jpg) by
Jörg Braukmann on Wikimedia Commons, and is licensed like it under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

The task had a second figure, a rack railway, until its photo's licence turned out not to allow redistributing it.
The figure and its published runs were removed.

The task exercises what board does not: precise input along a curve. On the river, one straight drag along the
shadowed bank covers 30% of it and two chords pass, so the task measures aim more than following a curve. `act`'s `path` action presses once and moves
through a line or a spline of points, so one action can draw the whole line. The agent may also draw it otherwise,
with chained paths, Shift+click lines or dots, and the attribution counts how. The photo is scaled to fit the window,
the same on every load, and the page does not scroll.

The page draws with pointer events on a canvas, as a figure editor would: a drag draws a stroke along every pointer
position Firefox reports, a click a dot, and Shift and a click a straight line from where the last stroke ended. Undo
(or Ctrl+Z) takes back the last stroke and Clear removes them all. The brush is 3 photo pixels wide. The panel beside
the photo says all of this, the way an editor shows its tips. Firefox reports one pointer position a frame: the
positions between, which `getCoalescedEvents()` would give, it gives only to a secure context, and the blog is plain
http. Allowing it as one (`dom.securecontext.allowlist`) makes Firefox block the page's own http requests as mixed
content. A stroke thus has a point about every 16 ms, about every second step of the pointer, which along these curves
stays within a tenth of a photo pixel of the pointer's path.

- `vm.nix`: the blog on `blog.example.test:80` with the variant's figure, and Firefox with policies against first-run
  pages.
- `figure.py STATE-DIR TARGET-DIR`: the blog's server and the figure's page. Every change is saved on the server, in
  `strokes.json` (each stroke's kind and its points in photo pixels), and logged with the time, the change and the
  whole drawing in `saves.jsonl`, so the grade comes from the server, not from the screen. The page also logs every
  pointer and key event it gets, its layout after every change (the photo's and the panel's box, the scale,
  `devicePixelRatio` and `mozInnerScreenX/Y`), and each stroke it draws, with its start and end in client pixels and
  its points in photo pixels, in `events.jsonl`.
- `VARIANT/track.json`: the line every second row of the photo, in photo pixels, with its half-width, and the rows
  that the drawing must cover, `required`. The line is measured, not drawn by eye: by eye, the
  river's waterline is easily placed on a neighbouring, brighter edge.
- `river/trace.py PHOTO`, which needs Pillow and NumPy: the waterline, scanned in each row from the open water to the first pixels that differ
  from the water, smoothed with a median over 31 rows, which the few rows where piles of stones or a jetty pull the
  scan inland cannot move, and a mean over as many: the bank is a smooth curve at this scale. The line is a band
  from 10 pixels inland, over the strip of stones between the water and the path, to 2 pixels into the water: a
  stroke along the embankment anywhere on it is on the line. Below row 480 a band of dark rocks of the water's
  colour lies in front of the stones, which some agents read as the waterline, so the band reaches 30 pixels into
  the water there. The rows required are 230 to 480, where the shadow lies on the bank. The line above them, in the
  sun, may be drawn but need not be.
- `check.py [--target DIR]`: measures the drawing against the variant's line. A point is on the line within its
  half-width there plus the variant's slack, in `figure.json`: 5 pixels of the agent's 1280×720 screenshot at the
  default display, 1920×1080, where the page scales the photo to fit, since an agent aims in the screenshot's
  pixels. That is 4.9 photo pixels on the river. It passes when the strokes cover at least 95%
  of the required rows and at most 10% of the drawing's length, relative to the required part's, lies off the line.
  Drawing along the line outside the required rows is neither required nor off it. A failure names the rows left
  uncovered or how much lies off the line and where: `the drawing covers 30% of the waterline; it leaves the
  waterline uncovered at y 262 to 436`. It also reports the mean and largest distance from the line, the strokes of
  each kind and the changes. Its milestones are `first stroke`, `line drawn` and `last change`.
- `attribute.py RUN-DIR | RESULTS-DIR`: who each stroke's outcome is due to, from `mcp.jsonl` and the page's events,
  against the run's variant. It maps each drawing action of `act` (a drag, a click on the photo, a Shift+click, a
  path) from shot pixels to the page as board's attribution does, with the shared code in `bench/actlog.py`, which
  reads actions in every form `act` takes (objects, arrays that start with the name, point pairs) and calls that
  name only their shot, on that shot's window. It finds the stroke the page drew for its press, and classifies the
  outcome: `on the line` (it started and ended where the agent aimed, on the line), `model` (where the agent aimed,
  but partly off the line, or a press beside the photo), `hyprmasq` (the page saw no press, drew no stroke for it,
  or the stroke started or ended more than 6 CSS pixels from where the agent aimed), `not sent` or `unmapped`. For a
  Shift+click line only its end is the agent's. It also counts how the agent drew: the drawing actions of each `act`
  call, the actions of each kind, undos and clears, the largest distance of a stroke from where it was aimed, and
  which strokes did not last to the end, the work the agent took back or redid. It prints a table per run and writes
  `attribution.json`.
- `check_test.py`: `python3 bench/tasks/trace/check_test.py` checks the grade on constructed drawings of a line of
  its own, `testdata/line`, the narrow curve measured for the rack railway's figure that the task had
  (the centre line, one straight line, five and twelve chords, a line beside it, one that stops short, a
  scribble beside a complete line, lines and dots, nothing) and of the river (the waterline, the part in the sun
  alone, a line on the stones and one on the path), the milestones and the server's check of a saved drawing, and
  the attribution on constructed logs: chained drags in one call, a chord across the bend, a stroke that ends far
  from where it was aimed, a press the page never saw, Shift+click lines with Undo, a stroke taken back and drawn
  again, a spline path after a hover path, actions in array form without a window, a refused batch, and results with
  only their text.
- `setup.py`: the profile, Firefox's window and its workspace, and the grade with the page's logs in `collect()`.
  With `HYPRMASQ_BENCH_WAYLAND_DEBUG` set in the driver's environment, Firefox logs its Wayland protocol to
  `vm-firefox.log`.
- `user.js`: no first-run pages, tips or telemetry in Firefox.
- `variants`, `debrief.md`: the figure, `river`, and the debrief, which also asks how the agent drew
  along the curve.

### files

Mara's folder `Project` in Nautilus 50.2.2, GNOME's file manager, on the hidden workspace `files`. It is the file
manager task of the earlier comparison on a laptop, which [laptop/run_files.py](laptop/run_files.py)
runs on a real machine in Nautilus 50.3.1, Omarchy's, with the same folder, prompt and grade. Nautilus is started like
a user would, on the visible workspace, and moved there. The user stays on workspace 1 with the terminal. The prompt
asks the agent to rename `report-draft.txt` to `report-final.txt` and move it into the folder `Archive`, leaving every
other file and folder as it is, in that window only. Only its first paragraph differs from the laptop's, and is the
one of the other tasks: the laptop's says "your desktop tools" and allows the user's focus to be taken if the tools
need it, for Cua Driver, where the VM's names the hyprmasq tools and asks the agent not to disturb Mara.

The task exercises what the others do not: a file manager's grid of icons, its rename popover, and moving a file
between folders, by drag-and-drop, cut and paste or its menus, whichever the agent picks.

- `vm.nix`: Nautilus from nixpkgs and nothing else. It needs a session bus, which alice's systemd user manager runs,
  and finds dconf on it. Without gvfs it has no Network place, and without localsearch no search, which the task does
  not need.
- `setup.py`: the folder, made fresh in `/home/alice/Project` from `START` in `check.py`, Nautilus's window and its
  workspace, root's log of Hyprland's events (the form task's `windows.py`), the watch of the folder, and the grade
  in `collect()`.
- `check.py`: passes when the report is in `Archive` under its new name with its contents unchanged, every other file
  and folder is as it was, with nothing added, and no window of a class other than Nautilus's opened while the agent
  worked, from root's log of Hyprland's events, without which it fails. Nautilus's own dialogs, such as a rename
  conflict, are of its class. The grade compares every path and every file's SHA-256 with the folder it wants, and a
  failure names the path: `missing: Archive/report-final.txt`, `unexpected: report-final.txt`, `changed: notes.txt`.
  `START` is `run_files.py`'s folder. Its milestones are `first change`, `report renamed` (a `report-final.txt` with
  the report's contents anywhere), `report moved` (the report's contents in `Archive` under any name) and `folder
  right`.
- `watch.py`: root's record of the folder, which it reads four times a second, with a line of the time and every
  path's SHA-256 whenever it changed, for the milestones.
- `check_test.py`: `python3 bench/tasks/files/check_test.py` checks the grade on folders it builds: the right
  result, the folder untouched, renamed but not moved, moved but not renamed, copied instead of moved, the report's
  contents changed, other files changed, moved or added, a folder removed, another app's window, no log of the
  windows, and that `START` is the laptop's. It also checks the windows that count and the milestones.

### inkscape

Inkscape 1.4.4 from nixpkgs shows Mara's drawing, `~/Documents/drawing.svg`: a blue rectangle, 200 by 100 px at x=120,
y=120, on a 640×480 page. Inkscape is started like a user would, on the visible workspace, and moved to the hidden
workspace `drawing`. The user stays on workspace 1 with the terminal. The prompt asks the agent to move the rectangle
exactly 2 px to the right, to x=122, and to save the drawing with Ctrl+S.

It is the task that [bench/laptop/run_inkscape.py](laptop/README.md) runs on a real machine for the earlier
comparison on a laptop, with the same drawing and the same task in the prompt, so that the VM's
runs compare with the laptop's. Its first paragraph is the one of the other VM tasks: the agent has the hyprmasq tools
and must not disturb the user, where the laptop's allowed foreground input and focus changes, which Cua Driver needs. The laptop had Arch's Inkscape 1.4.4 as well. The grade is the laptop's, the rectangle's
geometry as saved and no other app's window, and checks besides that the file was saved and that nothing else of the
drawing changed, which the laptop's did not. The user's window differs: a terminal running `top`, the VM's for every
task, where the laptop's was idle.

The task exercises what the others do not: a GTK 3 drawing app with a canvas, a precise number in a toolbar or a
nudge by keyboard, and a save that only the file shows.

- `vm.nix`: Inkscape and the task's files.
- `drawing.svg`: the drawing, which `run_inkscape.py` copies as well.
- `check.py`: passes when the drawing was saved over itself, its rectangle's bounding box is x=122, y=120, 200 by
  100 px within 0.01 px, its transform included, and nothing else of the drawing changed, and no window of another
  class than Inkscape's opened while the agent worked, from `/tmp/hyprmasq-bench-events.log`. What Inkscape adds when
  it saves, the attributes of its namespaces, the root's `version` and `id`, its view of the page and an empty `defs`,
  is no change. Another element, another page size, another fill, stroke or style of the rectangle, or a style,
  opacity or transform of the whole drawing on its root is one. Inkscape's own `--query-all` reads the
  saved rectangle too, as the laptop's grade does, and the run fails when the two disagree. A failure names what is
  wrong: `drawing.svg was not saved: it is as it was`, `saved rectangle geometry [124.0, 120.0, 200.0, 100.0],
  expected [122.0, 120.0, 200.0, 100.0]` or `the rectangle's fill is '#ff0000'; it was '#3478c6'`. It also reports
  the version of Inkscape that saved the file and whether the window's title showed unsaved changes at the end. Its
  milestone is `drawing saved`, the time of the last save.
- `check_test.py`: `python3 bench/tasks/inkscape/check_test.py` checks the grade on saves as Inkscape writes them: a
  correct one, the move as a transform or with the fill as a style, an unsaved change, wrong positions, a changed
  fill, stroke, opacity or corner radius, another element, another page size, a renamed rectangle, a broken file,
  Inkscape reading the geometry otherwise, and the windows that opened.
- `setup.py`: Mara's drawing, Inkscape's window and its workspace, and the grade with the saved drawing in
  `collect()`.
- `prompt.md`, `debrief.md`: what the agent is told.

## Adding a task

A task is a directory under `tasks/` with a NixOS module (`vm.nix`, which `default.nix` imports for the task
it is given), a `setup.py` for `vm.py`, a `prompt.md`, a `prompt-comparison.md`, its tool-neutral wording for
`--prompt comparison`, and a `debrief.md`, a `browsers` file if it takes Chromium in place of Firefox
(`--browser`), and, if it comes in variants, a `variants`
file, in which a line `NAME default` names the variant a run takes without `--variant`. `setup.py` defines `prepare()`,
which returns the address of the agent's window, and `collect()`, which returns `check` with `passed` and
`failures` and `events` with VM times that `report.metrics()` turns into milestones. It may define
`HYPRLAND_ENV`, variables for Hyprland's environment and so for the apps it starts, and `during(stop)`,
which runs in a thread while the agent works, until the `threading.Event` is set: the user's own activity.
