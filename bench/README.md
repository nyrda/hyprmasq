# Benchmarks

Agents do a task in a NixOS VM through `hyprmasq-mcp` alone. Each run is graded from the VM's state, timed
from both sides of the MCP connection, and ends with a debrief in which the agent says where hyprmasq slowed
it down, which points to where to look in the logs. The goal is not a leaderboard of models: it is to see how fast agents finish ordinary computer-use
tasks with hyprmasq, and what in hyprmasq costs them time.

Results of the benchmark runs so far are in [docs/benchmarks.md](../docs/benchmarks.md).

## Running

```sh
python3 bench/run.py                                    # 4 agents × 3 runs, the default
python3 bench/run.py --agent claude:claude-sonnet-5-5 --reps 1
python3 bench/run.py --out bench/results/my-run         # resumes: runs with a summary.json are skipped
python3 bench/run.py --source ../hyprmasq-feature       # the plugin and server of another worktree
python3 bench/run.py --task forgejo                     # another task: a directory under bench/tasks
python3 bench/run.py --display 2560x1440@1              # another monitor: WIDTHxHEIGHT@SCALE
python3 bench/report.py bench/results/my-run            # the report again
```

`--source DIR` builds the plugin, `hyprmasq-mcp` and the NixOS module from another checkout, for example a
feature branch's worktree, so that a branch can be benchmarked without the benchmark in it. The plugin is
built from the files Git tracks there, as in a flake build. `source.json` in the results records its commit,
the task and the display; `run.py` refuses to add runs to a directory whose finished runs had another of them.

`--display WIDTHxHEIGHT@SCALE` sets the VM's monitor, in device pixels, by default `1920x1080@1`. Shots fit
within 1280 pixels and zooms come at the monitor's scale, so what agents can read depends on it. The VM draws
in software: at 3840×2160 everything draws slower, also hyprmasq's captures, so times compare only between runs
on the same display. `source.json` records it.

`--lock FILE` holds an exclusive lock on FILE during each run, for machines where other VMs, such as the VM
test under `flock FILE`, would not fit into memory next to the benchmark's.

`--agent HARNESS:MODEL` is repeatable; HARNESS is `claude` (Claude Code) or `codex` (Codex CLI). The default
is `codex:gpt-6.1-sol`, `codex:gpt-6-astra`, `claude:claude-opus-5-5` and `claude:claude-sonnet-5-5`.
Repetitions go round the agents, so a slow hour of one API does not land on one agent's runs only.

It needs KVM, Nix, and `claude` and `codex` logged in for the user who runs it; it uses those logins as they
are, subscriptions included, and copies no credentials. A run takes the VM's boot (about a minute), the
agent, and the debrief; runs are sequential, so that they do not compete for the CPU.

## What a run is

```
host                                              VM (no network)
run.py ─ starts ─▶ nixos-test-driver ─ boots ──▶  Hyprland + hyprmasq, a terminal on the visible workspace,
   │                                               Thunderbird on a hidden one, Dovecot, fake SMTP
   └─ starts ─▶ claude -p / codex exec
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

The VM is offline: its only device to the outside is vsock, and every server the task needs runs in it. Its
clock starts at 2026-03-10 09:30 UTC on every boot, and the mailbox, certificates and Thunderbird profile are
fixed, so every run starts from the same screen. Only the harness and the model vary.

### Isolation of the agent

The agent runs on the host in an empty directory, with the environment of the user minus the variables of
any agent session around it (`CLAUDE*`, `CODEX*`, `T3CODE*`, …).

- Claude Code: `--tools ""` (no built-in tools), `--setting-sources ""` (none of the user's settings, hooks
  or plugins), `--disable-slash-commands`, `--strict-mcp-config` with only the tap, and
  `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
- Codex: `--ignore-user-config --ignore-rules`, the shell, computer use, browser, apps, plugins, multi-agent,
  image generation, memories and web search disabled, a read-only sandbox, and only the tap as MCP server.

Effort and reasoning are each harness's default for the model.

The sessions' transcripts are moved from `~/.claude/projects` and `~/.codex/sessions` into the run's
directory after the debrief.

## Measures

`summary.json` of a run, and the report's table:

| Measure | From |
| --- | --- |
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
| `usage` | Tokens, and turns for Claude Code, from the agent's event stream. |

The per-call log is `mcp.jsonl`; `report.tool_calls()` pairs its requests and responses. The agent's events
are in `agent.jsonl`, each with the host time it arrived. `report.py` computes the summaries again from these
files, so a change to the measures applies to old runs too.

`analyze.py RESULTS-DIR` counts frictions per run: wait-only `act` calls, pauses and wait overrides the agent
added, new windows and whether the `act` that opened them reported them, follow-up calls, windows on the
user's workspace, errors from input that closed its window, wait timeouts, and the time hyprmasq added to
`act` calls beyond waiting, pauses and typing.

## Files of a run

| File | Contents |
| --- | --- |
| `summary.json` | The measures above. |
| `run.json` | Harness, model, version, the agent's start, end, exit status and session id. |
| `agent.jsonl`, `agent.stderr` | The agent's event stream, timestamped. |
| `mcp.jsonl` | Every MCP message, timestamped, with images replaced by their size. |
| `ready.json`, `vm-result.json` | The VM's state at the start; the grade, the phase events and the state at the end. |
| `start-*.png`, `end-*.png` | Screenshots of every window and of the screen. |
| `sent-1.eml`, `user-notes.txt` | Task files: the first mail the agent sent (Thunderbird), what the user typed and what reached their notes (Forgejo). |
| `vm-events.log` | Hyprland's events (`socket2`), each with the VM's time. |
| `vm-*.log` | Hyprland, hyprmasq-mcp, socat, and the task's servers. |
| `debrief-prompt.md`, `debrief.md`, `debrief.jsonl` | The debrief. |
| `transcript-*.jsonl` | The harness's own transcript of the session. |
| `driver.log` | The test driver and the VM's console. |

## By hand

`bench/smoke.sh DRIVER DIR` boots the VM for a run directory and returns when it is ready, and
`bench/mcp_probe.py CID TOOL [ARGS] [--image OUT.png]` calls one tool in it. Any MCP client can use the VM
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

## Adding a task

A task is a directory under `tasks/` with a NixOS module (`vm.nix`, which `default.nix` imports for the task
it is given), a `setup.py` for `vm.py`, a `prompt.md` and a `debrief.md`. `setup.py` defines `prepare()`,
which returns the address of the agent's window, and `collect()`, which returns `check` with `passed` and
`failures` and `events` with VM times that `report.metrics()` turns into milestones. It may define
`HYPRLAND_ENV`, variables for Hyprland's environment and so for the apps it starts, and `during(stop)`,
which runs in a thread while the agent works, until the `threading.Event` is set: the user's own activity.
