# Benchmarks

`bench/` is a development tool for measuring hyprmasq with real agents. It is not part of what hyprmasq
installs. It requires Linux with KVM, Nix, and the agent CLIs logged in for the user running it. The default
agents use logged-in Claude Code and Codex. API-backed agents need their provider credentials.

Agents complete a task in an offline NixOS VM through `hyprmasq-mcp`. The benchmark grades the VM's state,
measures time on both sides of the MCP connection, and asks the agent for a debrief. It measures how agents
use hyprmasq and where hyprmasq costs them time. The tasks are board, calc, files, forgejo, form, thunderbird and trace.

Run results are in [docs/benchmarks.md](../docs/benchmarks.md). `export.py` turns results directories into the
datasets that page is generated from, and `render.py` draws its figures and tables; see
[Updating the results](#updating-the-results). `render.py` draws
[docs/comparison.md](../docs/comparison.md) as well, from the runs in `docs/comparison/data` that
[laptop/analyze.py](laptop/README.md) writes.

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
variant. `--several-machines` takes runs that `bench/dispatch.py` spread over several machines into one dataset, each
run with its CPU. `python3 bench/render.py` generates each block of `docs/benchmarks.md` and `docs/comparison.md`
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
# For another task, add --argstr task calc, files, forgejo, board, form or trace. Board also needs --argstr variant dnd or
# pointer, form its number of questions, such as --argstr variant 25, and trace its figure, river or rack.
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
python3 bench/run.py --task board --variant pointer     # a task's variant: one of tasks/TASK/variants
python3 bench/run.py --task form --variant 10           # a quick probe: 10 questions instead of the default 25
python3 bench/run.py --rep 2 --rep 3                    # only these repetitions, instead of --reps
python3 bench/report.py bench/results/my-run            # the report again
```

`--source DIR` builds the plugin, `hyprmasq-mcp` and the NixOS module from another checkout, for example a
feature branch's worktree, so that a branch can be benchmarked without the benchmark in it. The plugin is
built from the files Git tracks there, as in a flake build. `source.json` in the results records its commit,
the task, its variant and the display; `run.py` refuses to add runs to a directory whose finished runs had another
of them.

`--variant NAME` picks one of a task's variants, which `tasks/TASK/variants` lists, one a line. A task with that
file needs it, unless a line there is `NAME default`, which it then takes; others refuse it. A variant's directory,
`tasks/TASK/NAME`, may hold a `prompt.md` of its own, which the agent then gets. `default.nix` gives it to the
task's `vm.nix` as `benchVariant`; the default results directory and the driver's name carry it.

`--display WIDTHxHEIGHT@SCALE` sets the VM's monitor, in device pixels, by default `1920x1080@1`. Shots fit
within 1280 pixels and zooms come at the monitor's scale, so what agents can read depends on it. The VM draws
in software: at 3840×2160 everything draws slower, also hyprmasq's captures, so times compare only between runs
on the same display. `source.json` records it.

`--lock FILE` holds an exclusive lock on FILE during each run, for machines where other VMs, such as the VM
test under `flock FILE`, would not fit into memory next to the benchmark's.

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
are, subscriptions included, and copies no credentials. A run takes the VM's boot (about a minute), the
agent, and the debrief; runs are sequential, so that they do not compete for the CPU.

### API keys

`claude-api` and `pi` use API keys of this machine, so `dispatch.py` runs them only here (see below).

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
python3 bench/dispatch.py --host local,lock=/tmp/vm.lock --host me@box --host me@nas,local-lock=/tmp/nas.lock \
    --task forgejo --agent codex:gpt-6.1-sol --reps 6 --source ../hyprmasq-main --out bench/results/my-run
python3 bench/dispatch.py --host ... --source ../hyprmasq-main --source ../hyprmasq-feature \
    --out bench/results/main --out bench/results/feature   # two builds, each into its own directory
python3 bench/dispatch.py --host ... --reps 0              # only ship the commits and build the drivers
```

It takes `run.py`'s options, `--source` and `--out` repeatable, and a `--host` for each machine:

- `local`, this machine, or an SSH destination such as `user@machine`, which this machine reaches with its keys and
  without a prompt; `,slots=N` runs N VMs there at once (by default one, as runs on one machine compete for its CPU,
  see below); `,cid=N` is the vsock CID of the first slot's VM, by default 4242, the next slot's one more. Each
  run's VM keeps its disk and sockets in `$XDG_RUNTIME_DIR/hyprmasq-bench-vm-CID`, so that the slots' VMs do not
  share them;
- `,lock=FILE`: `run.py --lock FILE` on that machine, a lock that its other VM users take there;
- `,local-lock=FILE`: a lock on this machine that the dispatcher holds while that machine's VM is up, for a machine
  whose other VM users take their lock here, such as VM tests that this machine runs on a remote Nix store;
- `,dir=DIR`: where the machine keeps the dispatcher's files, by default `~/.cache/hyprmasq-bench`;
- `,agents=PATTERN+PATTERN`: the agents the machine takes, each `HARNESS`, `HARNESS:MODEL` or
  `HARNESS:MODEL@EFFORT` with `*`, such as `agents=codex+claude:*opus*`; by default all. A pattern without `@` takes
  the agent at any effort, and one with `@` only at the efforts it matches, such as `codex:*@high`.

A lock is exclusive, so with a lock a machine's slots take turns at the VM.

`claude-api` and `pi` runs go only to `local`, since they use this machine's API keys: another machine never takes
them, whatever its `agents=`, and naming them in an SSH host's `agents=` is an error, as is an agent that no host
takes. The dispatcher drops Anthropic keys (`ANTHROPIC_*`, `HYPRMASQ_BENCH_ANTHROPIC_*`) from the environment of ssh, git
and rsync. For
example, Codex runs on two machines and the API runs here:

```sh
python3 bench/dispatch.py --host local,lock=/tmp/vm.lock --host me@box,agents=codex \
    --agent codex:gpt-6.1-sol --agent claude-api:claude-sonnet-5-5 --agent pi:tensorx/deepseek/deepseek-v4.1-flash-1017 \
    --task forgejo --reps 3 --source ../hyprmasq-main --out bench/results/my-run
```

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
machine is free, so a faster machine runs more of them; read agents against each other per machine too. A run
that goes to another machine after a failure, or that a resume runs again while its unit's other builds finished,
can land on another machine than they did; the report's table per machine shows where each ran. Check the
harness versions as well: machines that update their CLIs apart can run different versions of one harness, and
`run.json` and the report's runs list show which.

`dispatch_test.py` tests the planning, the scheduling and the hosts' agents without machines:
`python3 bench/dispatch_test.py`. `harness_test.py` tests pi's isolation, claude-api's key and its check, the names
of runs and the reading of a recorded pi stream, with fake `pi` and `claude`: `python3 bench/harness_test.py`.

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
   on the visible workspace (the user's own work), and the task's `setup.py`, then listens for MCP
   connections on vsock port 5000 and writes `ready.json`.
2. `run.py` starts the agent with the task's prompt and one MCP server, `mcp_tap.py`, which connects to the
   VM over vsock and logs every message with the host time. The agent has no other tools.
3. When the agent exits, `run.py` writes `agent-done`. The VM grades the run with the task's checker,
   collects screenshots and logs, writes `vm-result.json` and stops.
4. `run.py` resumes the agent's session with the debrief (`tasks/*/debrief.md`), which tells it the grade and
   what was measured and asks where hyprmasq slowed it down, without tools.

The debrief is no evidence for or against a change. An agent knows only the run it had: it cannot compare with a
run without a feature, or tell which result saved it a call, and asked for a loss it names one. Judge a change by
what the agents did, before and after it; see
[how the runs are read](../docs/benchmarks.md#how-the-runs-are-read).

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
| `calls`, `by_tool`, `actions` | Tool calls by tool, and the actions of all `act` calls. |
| `images`, `image_kib` | Images in results, and their size. |
| `no_change`, `errors` | Results whose summary says `no change`, and tool errors. |
| `phases` | The task's milestones from the VM's state, in seconds after the agent's start, such as `account verified` and `reply sent` for Thunderbird. |
| `checks` | The checker's details, such as the user's notes for the Forgejo task. |
| `user` | From Hyprland's events in the VM: windows that opened on the user's visible workspace, the seconds the keyboard focus was away from the user's window, and switches of the visible workspace. `undisturbed` when there were none. |
| `usage` | Tokens, with the cache reads and writes among the input tokens, and turns for Claude Code and pi (whose tokens and cost include its compaction requests), from the agent's event stream, with its final answer. The cost in USD is the one Claude Code and pi compute. For Codex it is what the run's requests in its transcript would cost at OpenAI's API list prices (`OPENAI_PRICES` in `report.py`), each at the prices for its prompt's length. |

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
| `run.json` | Harness, model, version, the agent's start, end, exit status and session id, and the machine: host name, CPU, threads and memory. The effort asked for and the one the harness ran at (see [Effort](#effort)). For Claude Code, the credentials its init events named; for pi, the thinking level. |
| `agent.jsonl`, `agent.stderr` | The agent's event stream, timestamped. |
| `mcp.jsonl` | Every MCP message, timestamped, with images replaced by their size. |
| `ready.json`, `vm-result.json` | The VM's state at the start; the grade, the phase events and the state at the end. |
| `start-*.png`, `end-*.png` | Screenshots of every window and of the screen. |
| `sent-1.eml`, `user-notes.txt`, `sheet.ods`, `vm-board.json`, `vm-form-state.json`, `vm-trace-strokes.json`, `vm-files-watch.jsonl` | Task files: the first mail the agent sent (Thunderbird), what the user typed and what reached their notes (Forgejo), the sheet as saved (Calc), where the labels are (board), the questionnaire's answers, submissions and changes (form), the drawing (trace), the folder at each change (files). |
| `vm-events.log` | Hyprland's events (`socket2`), each with the VM's time. |
| `vm-*.log`, `vm-*.jsonl` | Hyprland, hyprmasq-mcp, socat, and the task's servers and apps, such as the intranet's requests and the watch of the sheet (Calc), or the page's events and saves (board, trace). |
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
- `setup.py`: the profile, Thunderbird's launch with `hyprmasq-launch`, and the collection of the grade and
  the phase events.
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
- `prompt.md`, `debrief.md`: what the agent is told.

### calc

The user's own Firefox 157 shows five reports of the company intranet as tabs, and LibreOffice Calc 26.8 has
Mara's sheet `~/Documents/Office revenue Q4 2025.ods` open: one row per office, the months as columns, and a
summary. Both apps are started like a user would, on the visible workspace, and moved to hidden ones: side by
side on `work` when the monitor is 2560 logical pixels wide or more, below that Firefox on `web` and Calc on
`calc`. The user stays on workspace 1 with the terminal. The prompt asks the agent to enter each office's
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
  its layout and no other sheet; Mara's five tabs are open on their pages, from Firefox's session
  file; and no other file in Mara's home, hidden ones aside, was added, removed or changed. A failure names the
  cell, what it holds and, for a trap, which one: `B10 (Vienna, October) is 112.4, in EUR thousands as the
  report gives it; expected 112400`. It also reports requests without Mara's session, such as Calc's, and when
  Stockholm's page 2 and Lisbon's older updates were loaded.
- `check_test.py`, `check-test.nix`: `nix-build bench/tasks/calc/check-test.nix` fills Mara's sheet through UNO
  in a headless LibreOffice in 28 ways, a right one and ones wrong in each way above, saves it, and checks each
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
moved to the hidden workspace `web`; the user stays on workspace 1 with the terminal. The prompt asks the agent to put
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

- `vm.nix`: the app of the variant's toolkit (`hyprmasq-bench-form STATE-DIR COUNT [STATE-FD]`), and the number of
  questions from the variant. Its comment says what every toolkit's app must do: record its process id, which
  `setup.py` signals, show the questions in a window titled "Questionnaire", log the changes and submissions as
  `form.py` does, and write its state on SIGUSR1.
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
  window of the questionnaire's process, and no log of the windows.
- `setup.py`: the app, started with its state file, its window and its workspace, root's log of Hyprland's events,
  and in `collect()` the app's state, asked of the app's own process, its logs and the grade.
- `windows.py`: root's log of Hyprland's events, with the process of each window that opens, which it asks
  Hyprland for.
- `variants`, `prompt.md`, `debrief.md`: the numbers of questions, 25 the default, and what the agent is told.

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
workspace 1 with the terminal. The task's variants are the figures, each a directory with its photo, the line to
trace (`track.json`), the page's texts (`figure.json`) and its prompt (`prompt.md`):

- `river`, the default: a river from above, where it bends through a gorge, 731×600 pixels. The agent draws along
  the left bank where it lies in the shadow of the hill, where the water meets the shore. The waterline is a sharp
  edge between the water and the stones, and the page shows the photo at about 1.5 times its size at 1920×1080.
- `rack`: a rack railway, 239×476 pixels, which the page scales up about twice. The agent draws over the rack, the
  toothed rail between the running rails, from the bottom edge to where it leaves the photo at the top. Near the top
  the rack is a ridge 2 pixels wide a few pixels from a brighter rail, so the variant tests recognition as much as
  aim: agents slide onto the rail there.

The task exercises what board does not: precise input along a curve. On the river, one straight drag along the
shadowed bank covers 30% of it and two chords pass, so the variant measures aim more than following a curve. On the
rack, one straight drag covers 14%, five chords 90%, and eight pass. `act`'s `path` action presses once and moves
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
  that the drawing must cover, `required`. The line is measured, not drawn by eye: by eye, the rack's top and the
  river's waterline are easily placed on a neighbouring, brighter edge.
- `river/trace.py PHOTO`: the waterline, scanned in each row from the open water to the first pixels that differ
  from the water, smoothed with a median over 31 rows, which the few rows where piles of stones or a jetty pull the
  scan inland cannot move, and a mean over as many: the bank is a smooth curve at this scale. The line is a band
  from 10 pixels inland, over the strip of stones between the water and the path, to 2 pixels into the water: a
  stroke along the embankment anywhere on it is on the line. Below row 480 a band of dark rocks of the water's
  colour lies in front of the stones, which some agents read as the waterline, so the band reaches 30 pixels into
  the water there. The rows required are 230 to 480, where the shadow lies on the bank. The line above them, in the
  sun, may be drawn but need not be.
- `rack/trace.py PHOTO [--start X0,X1] [--end Y] [--overlay OUT.png]`: the rack, following its two edges row by row
  from the bottom up, from where `--start` says they are in the bottom row, to row `--end`, and smoothed. Its overlay
  shows the trace on the photo enlarged, to check by eye before committing. Both scripts need Pillow and NumPy.
- `check.py [--target DIR]`: measures the drawing against the variant's line. A point is on the line within its
  half-width there plus the variant's slack, in `figure.json`: 5 pixels of the agent's 1280×720 screenshot at the
  default display, 1920×1080, where the page scales the photo to fit, since an agent aims in the screenshot's
  pixels. That is 4.9 photo pixels on the river and 3.9 on the rack. It passes when the strokes cover at least 95%
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
- `check_test.py`: `python3 bench/tasks/trace/check_test.py` checks the grade on constructed drawings of the rack
  (the centre line, one straight line, five and twelve chords, a line beside the rack, one that stops short, a
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
- `variants`, `debrief.md`: the figures, `river` the default, and the debrief, which also asks how the agent drew
  along the curve.

### files

Mara's folder `Project` in Nautilus 50.2.2, GNOME's file manager, on the hidden workspace `files`. It is the file
manager task of [docs/comparison.md](../docs/comparison.md#file-manager), which [laptop/run_files.py](laptop/run_files.py)
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
- `prompt.md`, `debrief.md`: what the agent is told.

## Adding a task

A task is a directory under `tasks/` with a NixOS module (`vm.nix`, which `default.nix` imports for the task
it is given), a `setup.py` for `vm.py`, a `prompt.md` and a `debrief.md`, and, if it comes in variants, a `variants`
file, in which a line `NAME default` names the variant a run takes without `--variant`. `setup.py` defines `prepare()`,
which returns the address of the agent's window, and `collect()`, which returns `check` with `passed` and
`failures` and `events` with VM times that `report.metrics()` turns into milestones. It may define
`HYPRLAND_ENV`, variables for Hyprland's environment and so for the apps it starts, and `during(stop)`,
which runs in a thread while the agent works, until the `threading.Event` is set: the user's own activity.
