# Benchmarks

What the benchmark in [bench/](../bench/README.md) found. It runs agents on a task in an offline NixOS VM
through `hyprmasq-mcp` alone, grades the result from the VM's state, times the run from both sides of the
MCP connection, logs Hyprland's events, and asks the agent afterwards where hyprmasq slowed it down.

The point is less the ranking of models than what in hyprmasq costs agents time, and what breaks its promise
not to disturb the user.

## How the runs are read

A change is judged by what the agents did before and after it, from both sides of the MCP connection
(`mcp.jsonl`, and the harness's transcript, which has the images) and from Hyprland's events in the VM:

- passed, wall time, calls, the time in tools and hyprmasq's own time;
- new looks: a `screenshot`, or an `act` without actions, right after a result, which did not show the agent
  what it needed or showed work still going on;
- misses: a click or key whose result says `no change`, a wrong target, a way back from a wrong page or entry;
- the model's time from a result to its next call.

What agents do after a result is counted per kind of result (a full shot, zooms by their number, no change),
and the new looks by the step they follow. A share of calls alone misleads: an agent that asks for
`capture: "window"` for its next call has acted on the result before it, and it can only see what a zoom left out
because the zoom showed it the rest. On #23 to #25, agents acted directly on 90% of the 467 results that were
only zooms: 98% of those with one zoom and all with five. 46 of the 49 new looks after them came from two
Thunderbird steps: waiting out the account lookup, and zooms that left out the Finish button
([#26](#2026-10-16-animations-that-hid-real-changes-26)). In the browser task, the model took 2.9 s (median) to
its next call after five zooms, and 3.1 s after a full shot.

The debrief asks the agent where hyprmasq slowed it down. An agent knows only the run it had: it cannot compare
with a run without a feature, or tell which result saved it a call, and asked for a loss it names one. So a debrief
points to where to look in the logs and is no evidence for or against a change. Debriefs called five zooms harder
to read than a full shot in almost every browser run, and the logs show no cost; a debrief named the step where
the Finish button was missing, and the logs showed why. Sections before 2026-10-16 counted debriefs in places;
those counts are left out below.

## 2026-10-14: Thunderbird

The task: Thunderbird 156 runs with a fresh profile on the hidden workspace `mail`, at its account setup,
while the user works in a terminal on the visible workspace. The agent adds Mara's account and answers
Jonas's question by email; the answer is a room number in one of 27 mails, next to a stale one in an older
mail. See [the task](../bench/README.md#thunderbird).

Setup: hyprmasq at b9112b1, Hyprland 0.56.2, a 1920×1080 monitor at scale 1, `hyprmasq-mcp` with its
defaults (images at most 1280 pixels wide). Claude Code 2.1.296 at its default effort (`medium`), Codex CLI
0.162.1 at the model's default reasoning effort. Three runs per agent, one after another.

This run measured b9112b1, which was two merged PRs behind `main`: #12 (placing the windows of a launch's
child processes) and #13 (placing windows that emulated input opens) were missing. Finding 1 is already fixed
on `main`; see [the fixes](#2026-10-14-the-first-fixes) for the runs on `main` and on the PRs that followed.

### Results

| Agent | Passed | Wall s | In tools s | Outside tools s | Turn s | Calls | Actions | Images | Account at s | Reply at s | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Code, claude-opus-5-5 | 3/3 | 107 (103–148) | 47 (41–58) | 66 (56–89) | 2.6 (2.6–3.6) | 20 (19–24) | 25 (25–28) | 22 (20–22) | 31 (28–32) | 95 (92–136) | 0/3 |
| Claude Code, claude-sonnet-5-5 | 3/3 | 88 (81–94) | 40 (28–40) | 52 (49–54) | 2.2 (2.1–2.4) | 23 (22–23) | 40 (31–40) | 18 (17–23) | 24 (21–26) | 80 (70–86) | 0/3 |
| Codex, gpt-6-astra | 3/3 | 132 (130–148) | 16 (15–17) | 116 (116–131) | 4.9 (4.2–5.2) | 23 (22–24) | 21 (20–21) | 20 (19–29) | 43 (36–43) | 120 (119–128) | 0/3 |
| Codex, gpt-6.1-sol | 3/3 | 122 (117–168) | 18 (16–20) | 102 (99–152) | 3.5 (3.3–7.2) | 25 (22–26) | 25 (22–31) | 22 (19–22) | 37 (32–39) | 103 (90–147) | 0/3 |

Medians, with the range of the three runs in brackets. *In tools* is the time inside hyprmasq's tool calls,
*outside tools* the model's and the harness's. *Turn* is the median time between two calls. *Account at*
and *Reply at* are Mara's first IMAP login and the first submitted mail, in seconds after the agent started.
*User undisturbed* counts runs in which no window opened on the user's workspace and the keyboard focus never
left the user's window.

- **Every run passed.** The reply named room 4.12, went to Jonas alone as a reply, and landed in Sent. So
  the task measures speed, as intended.
- **Every run disturbed the user.** See [finding 1](#1-new-windows-land-on-the-users-workspace-and-take-the-focus).
- **Most of the time is the model's.** Codex spends 85–90% of a run outside tools, Claude Code 55–65%.
  Codex's turns are about twice as long (3.5–4.9 s against 2.2–2.6 s), and its first call comes 9–13 s
  after the start, Claude Code's after 2.5 s.
- **Tool time is mostly waiting the agents asked for.** The Claude models add 19–30 s of `pause` actions
  (Sonnet) or raise `wait.quiet_ms` to 1–2 s in most calls (Opus); Codex keeps the defaults and spends
  15–20 s in tools. What hyprmasq itself adds, the time of `act` calls beyond the wait, the pauses and typing,
  is 4–6 s a run, about 0.3 s a call.
- **Wall time follows the API's load.** gpt-6.1-sol's second run made the same calls as its first, but its
  turns took 7–8.5 s instead of 3–4 s from the 46th second on: 168 s against 117 s. Calls and tool time are
  the measures to compare hyprmasq versions by; wall time needs more runs.
- About 15 interactions are needed at least. The agents made 19–26 calls; the findings below account for
  most of the difference.

Frictions per run, from `bench/analyze.py`:

| Run | Wait-only acts | Pauses s | Wait overrides | New windows | Missed by act | Follow-up calls | On user's workspace | Closing errors | Timeouts | hyprmasq s | Acts |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-opus-5-5 #1 | 1 | 0.8 | 13 | 2 | 0 | 1 | 2 | 0 | 0 | 6.0 | 20 |
| claude-opus-5-5 #2 | 0 | 7.5 | 9 | 2 | 0 | 1 | 2 | 0 | 1 | 3.9 | 15 |
| claude-opus-5-5 #3 | 1 | 0.0 | 13 | 2 | 0 | 1 | 2 | 0 | 2 | 4.7 | 16 |
| claude-sonnet-5-5 #1 | 0 | 30.5 | 0 | 2 | 0 | 3 | 2 | 1 | 0 | 4.5 | 16 |
| claude-sonnet-5-5 #2 | 0 | 29.0 | 2 | 3 | 1 | 3 | 3 | 1 | 0 | 6.0 | 16 |
| claude-sonnet-5-5 #3 | 0 | 19.0 | 0 | 3 | 0 | 6 | 3 | 1 | 0 | 4.3 | 13 |
| gpt-6-astra #1 | 3 | 0.0 | 0 | 2 | 1 | 2 | 2 | 0 | 0 | 5.1 | 17 |
| gpt-6-astra #2 | 3 | 0.0 | 0 | 2 | 1 | 2 | 2 | 0 | 0 | 4.8 | 17 |
| gpt-6-astra #3 | 3 | 0.0 | 0 | 2 | 1 | 3 | 2 | 0 | 0 | 4.2 | 17 |
| gpt-6.1-sol #1 | 3 | 0.0 | 0 | 2 | 1 | 4 | 2 | 0 | 0 | 5.0 | 19 |
| gpt-6.1-sol #2 | 3 | 0.0 | 0 | 2 | 1 | 2 | 2 | 0 | 0 | 4.3 | 16 |
| gpt-6.1-sol #3 | 4 | 0.7 | 0 | 2 | 1 | 2 | 2 | 0 | 0 | 5.6 | 20 |

### Findings

Ordered by what they cost. Each is backed by the logs of the runs, not only by what the agents said.

#### 1. New windows land on the user's workspace and take the focus

Fixed on `main` by #13, which this run did not have.

In all 12 runs, Thunderbird's compose window and its "Sending Message" progress window, and in Sonnet's runs
also the "Send Message" confirmation, opened on the user's visible workspace 1. The compose window tiled next
to the user's terminal, halving it, and took the keyboard focus for 12.5–35 s a run (median 18 s). Anything
the user typed meanwhile would have gone into the reply.

Hyprland opens a new window on the active workspace. hyprmasq's `launch` places only the first window of the
command on the hidden workspace, and an app the user started themselves gets no rule at all, so every later
window of a hidden app, such as a compose window, dialog or file chooser, opens on the user's screen. Nothing
in the results says so: `act` names the new window, and only `windows` shows `workspace 1 (visible)`. Agents
had no way to move it, and all three Opus runs told the user in their final message that the terminal had not
been touched.

The disturbance also adds noise: when the compose window takes the focus, the main window redraws its
toolbar in its inactive style, and the `act` that pressed Reply returns four or five zooms of those slivers
(Opus #2, #3).

Options: the plugin could keep new toplevels of a driven client on the parent's workspace, silently and
without focus, at map time; or at least `act` and `windows` could say that a new window opened on a visible
workspace and holds the focus.

#### 2. act does not return new windows, and misses those that map late

When an `act` opens a window, the result names it but shows the old window. Every run then needed a
`screenshot` of the new window, and often a `windows` call first: 1–6 follow-up calls a run, each a model
turn of 2–5 s.

In all six Codex runs, the compose window mapped 70–170 ms after the `act` that pressed Ctrl+R had returned:
the wait had settled on the quiet main window. That `act` could not report it, and the agents had to find it
with `windows`, once also missing it there. The Claude runs saw it only because of their longer waits.

Options: return the new window's address and shot from the `act` that opened it, watch for windows of
the target's client for a short grace period after the input, and capture a new window in the same result.

#### 3. The first shot of a hidden Gecko window is blank

In all 12 runs, the agent's first `screenshot` of Thunderbird showed an empty window. Thunderbird had started
on a hidden workspace, where it gets no frame callbacks, and does not draw until the plugin, driving the
window, sends them. The `screenshot` returned the stale buffer without a word. Four agents clicked into the
blank shot (the next result: `full shot: 76% changed since s1`), two paused, and the six Codex runs made a
wait-only `act`.

Option: a screenshot of a window that the plugin did not drive before could start its frame callbacks and
wait for a frame, as it waits for a window that has not caught up with a resize.

#### 4. The default quiet time ends waits during work that does not draw

Thunderbird looks up the server configuration, creates the account and searches without drawing for more
than 200 ms at a time, so `act` settles before the work is done. Every Codex run made the same three
wait-only `act` calls: after the blank first shot, after Continue in the account setup, and after the
password. The Claude models instead distrust the wait from the start: Sonnet adds 19–30 s of pauses a run,
Opus raises `quiet_ms` to 1–2 s in most calls. With Opus's longer quiet times, three waits during a search
never settled and ran to their deadline, 13.3–13.5 s each (Opus #2, #3). The agents guessed that the status
bar ("Connecting to imap…") kept drawing, which other calls had excluded as an animation; the timeout said
`still drawing` without saying where.

Options: name the region that kept drawing in timeouts, as the summary does for animations; a wait condition
on a region or the title; and in the tool descriptions, that a pause action is no substitute for the wait.

#### 5. A click that closes its dialog is an error

Clicking a button that closes its dialog, followed by a pause in the same batch, returns `window_gone`,
`delivery partial`, "may be partly applied" (Sonnet, every run). The click had done exactly what it should;
the agents checked with `windows` and screenshots, and in a pilot run clicked Send again. Without the
following pause the same click returns `1/1 delivered · window closed`.

Option: when the window closes after a delivered click and only pauses remain, report the batch as delivered
and the window as closed.

#### Smaller

- **Leases.** `lacks lease, hold` and `no lease` appeared when the compose window, on the visible workspace,
  had the real focus, and follow from finding 1.
- **Region screenshots.** `screenshot` with a region in a shot's pixels captures the window as it is now;
  `zoom of s11` reads as a crop of s11, and one agent got a blank white region where s11 had text (Opus #3).
- **Small text.** At 1280×720, the room number in the mail was too small to read for three Codex runs,
  which zoomed in with a region to read it.
- **`expect_change: false`** gave `redraw observed`, which surprised the agent (Opus #2).
- **The wait time in the summary** leaves out typing: `231 ms` for a call of 3.3 s (Opus #1).
- **A sender that cannot start** (in a pilot, `hyprmasq-input` lacked `libei`) gives "delivery uncertain …
  from actions[0] (click) on" instead of "nothing sent"; the agent read "on" as a missing target.
- **Codex's sandbox.** In Codex's `read-only` and `workspace-write` sandboxes, every MCP tool without
  `readOnlyHint` needs approval, and `codex exec` cannot ask: the first `screenshot` fails with "MCP tool
  call requires approval, but approval policy is never". The bench sets
  `mcp_servers.hyprmasq.default_tools_approval_mode = "approve"`. [Installation](installation.md) could say
  so for Codex users.

#### Found while building the benchmark

`nix/package.nix` copied a `hyprmasq-input` that `make` had left in the work tree into the source of
non-flake builds, such as `nix-build nix/hyprmasq-vm-test.nix`. `make` then did not build it, and the fixup
stripped its RPATH, so the installed sender could not load `libei`. The source filter now leaves it out.

### Limits

- One task, three runs per agent. Wall times vary by up to 50% with the API's load.
- The user is a terminal that redraws every second; nobody types during the run. The benchmark measures
  that the focus left the user's window, not what that did to their typing.
- Effort is each harness's default, so a harness update can change the results.
- Until 2026-10-16, agents ran in a directory inside this checkout, and Codex loads `AGENTS.md` from the
  repository a directory belongs to: every Codex run up to #24 had hyprmasq's development instructions in its
  context, which Claude Code does not load. They were the same for every build, so comparisons between builds
  hold. Agents now run in a fresh directory under `/tmp`.
- The findings above rest on the logs, not on the debriefs; see [how the runs are read](#how-the-runs-are-read).
  Where an agent's claim did not hold up, it is left out or corrected (the blank region was a capture of the
  current window, not a bug).
- The raw data, about 90 MB with screenshots and transcripts, is in `bench/results/`, which Git ignores.

### Next

See [what is open](#open) after the fixes.

## 2026-10-14: the first fixes

The findings above went to three PRs, each benchmarked from its head commit with `run.py --source` against a
new baseline on `main` (02c0edf, with #12 and #13). Only Claude Code with claude-sonnet-5-5 and Codex with
gpt-6.1-sol ran, three times each; the setup is otherwise the one above.

| Build | Agent | Passed | Wall s | In tools s | Turn s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main` 02c0edf | Sonnet | 3/3 | 96 (92–104) | 38 (33–40) | 2.2 | 26 (24–26) | 3/3 |
| | gpt-6.1-sol | 3/3 | 158 (132–166) | 16 (16–19) | 4.7 | 25 (24–31) | 3/3 |
| #17 fresh first shot, 37f7e41 | Sonnet | 3/3 | 81 (77–107) | 32 (29–34) | 2.0 | 23 (21–25) | 2/3 |
| | gpt-6.1-sol | 3/3 | 114 (108–140) | 15 (14–15) | 4.5 | 22 (20–23) | 3/3 |
| #16 closing click, df7189f | Sonnet | 3/3 | 80 (74–113) | 38 (24–40) | 1.9 | 23 (20–25) | 3/3 |
| | gpt-6.1-sol | 3/3 | 140 (131–164) | 18 (17–18) | 4.0 | 26 (25–32) | 3/3 |
| #15 new windows, bc7dfa6 | Sonnet | 3/3 | 96 (86–104) | 45 (44–47) | 2.2 | 23 (19–23) | 3/3 |
| | gpt-6.1-sol | 2/3 | 188 (187–214) | 22 (22–24) | 7.3 | 22 (20–25) | 3/3 |

- **`main` (#13) fixes finding 1.** In 6 of 6 runs no window opened on the user's workspace and the focus never
  left the user, against 0 of 6 on b9112b1. A side effect: the placed compose window tiled next to
  Thunderbird's main window on the hidden workspace and halved it, in 6 of 6 runs; two runs then got
  `stale_shot`.
- **#17 fixes finding 3.** The first shot waited about 0.27 s for Thunderbird to draw and showed the account
  setup in 6 of 6 runs; no agent clicked into a blank shot, and Codex made 2 wait-only `act` calls a run
  instead of 4–5. The Sonnet run counted as disturbed had a progress window on the user's workspace for
  0.2 s, from a gap in #13's attribution that #15 fixes: the dialog that got the click had closed before the
  new window appeared.
- **#16 fixes finding 5.** A click that closed its dialog returned "delivered · window closed" in 6 of 6 runs,
  also with a pause after it; on `main` every Sonnet run got `window_gone` there.
- **#15 fixes finding 2 and the side effect of #13.** `act` returned every new window with its shot; no
  window mapped too late for its result (on `main`, one per Codex run did), and follow-up calls fell from 2–4
  to 0–2 a run. Placed windows float at their own size: no resize of the main window and no `stale_shot` in
  6 of 6 runs. The cost: `act` watches for new windows for 500 ms after its wait, so hyprmasq's own time
  rose from 4–7 s to 9–16 s a run. The failed run misread room 4.12 as 2.12 in a 1280-pixel shot without
  zooming in. gpt-6.1-sol's longer wall times on #15 come mostly from slower turns (6.1–7.6 s against 4.5–4.8 s).

## 2026-10-14: Forgejo in the user's browser

A second task for what Thunderbird does not show: the user's own Chromium, with one window on the user's
workspace where the user types notes through QEMU's keyboard during the whole run, and one window of the same
process parked on a hidden workspace for the agent. The agent signs in to Forgejo, uploads a file through the
file chooser that the desktop portal draws in a process of its own, and connects a dashboard to Forgejo
through an OAuth popup. See [the task](../bench/README.md#forgejo).

| Build | Agent | Passed | Wall s | Calls | User undisturbed | Focus away s |
| --- | --- | --- | --- | --- | --- | --- |
| `main` 02c0edf | Sonnet | 1/3 | 50 (42–66) | 15 (14–24) | 0/3 | 9–26 |
| | gpt-6.1-sol | 0/3 | 216 (149–264) | 32 (24–45) | 0/3 | 79–85 |
| #15 bc7dfa6 | Sonnet | 3/3 | 52 (51–58) | 23 (20–24) | 2/3 | 0–5 |
| | gpt-6.1-sol | 3/3 | 219 (212–252) | 24 (23–38) | 2/3 | 0–19 |

- **On `main`, the file chooser blocks the task.** In 6 of 6 runs it opened on the user's workspace with the
  focus, since it comes from the portal's process, which #13 does not relate to the browser. The user's typing
  went into its search field and mixed into the agent's path; agents quoted the user's sentences from it.
  Five of six agents closed it and stopped, as the prompt told them not to disturb the user.
- **#15 places it by its parent.** The portal names the browser window as the dialog's parent through
  xdg-foreign, and #15 decides when Hyprland applies window rules, when the parent is known. The chooser
  opened on `web` in 6 of 6 runs, and all 6 passed.
- **The OAuth popup** has no parent. It landed on `web` in the runs on `main` that got that far, and in 4 of 6
  on #15. In the other 2 it opened on the user's workspace with the focus for 5 and 19 s: the user had typed
  into their own window of the same browser after the agent's click, and #15 lets the user win when the app's
  input is newer. #15 says so in its result. A sharper attribution is open.
- **Shortcuts fail silently in the agent's window.** It shares its Wayland connection with the user's focused
  window, so it lacks `hold`, and Chromium gets the user's modifiers: Ctrl+L did nothing in 3 runs on `main`,
  while `act` reported it delivered. Clicking the address bar works.
- **The user's notes** lost 21–57% of what the user typed on `main`, in the runs where the chooser had the
  focus. These counts are rough: the notes page could save out of order, which 0ff7d7f fixed after these runs,
  so small differences, such as in one #15 run without any window on the user's workspace, come from that.

## 2026-10-14: #15 after its follow-ups

#15 then changed how it tells the agent's popups from the user's, replaced the hint that said nothing had
changed when the input had opened a window, and stopped waiting for new windows after input that cannot open
one (87e86d8). After a rebase onto #16 and #17 and two fixes from review (0dd216c), it was merged as c07bdd1,
so the second row of each task is `main` with all four PRs. Sonnet and gpt-6.1-sol, three runs each.

| Build | Task | Agent | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 87e86d8 | Thunderbird | Sonnet | 3/3 | 103 (94–111) | 47 (44–50) | 19 (18–23) | 3/3 |
| | | gpt-6.1-sol | 3/3 | 181 (133–217) | 25 (19–26) | 24 (19–25) | 3/3 |
| 0dd216c | Thunderbird | Sonnet | 3/3 | 84 (74–88) | 32 (26–39) | 21 (18–21) | 3/3 |
| | | gpt-6.1-sol | 3/3 | 142 (106–192) | 20 (19–28) | 20 (19–25) | 3/3 |
| 87e86d8 | Forgejo | Sonnet | 3/3 | 62 (62–65) | 25 (22–28) | 15 (14–18) | 3/3 |
| | | gpt-6.1-sol | 3/3 | 203 (184–245) | 24 (20–26) | 24 (21–27) | 3/3 |
| 0dd216c | Forgejo | Sonnet | 3/3 | 50 (49–62) | 21 (17–28) | 14 (13–19) | 3/3 |
| | | gpt-6.1-sol | 3/3 | 134 (104–170) | 21 (20–21) | 20 (20–21) | 3/3 |

- **The OAuth popup now stays with the agent.** A window without a parent goes to the user only when the user's
  own input to the app after the agent's could have opened it: a click, Enter outside a text field or a
  shortcut, not typing. The popup opened on `web` with its shot in the result in 12 of 12 runs, against 4 of 6
  on bc7dfa6, and the focus never left the user in any of the 24 runs.
- **Thunderbird's windows** float on `mail`; its main window kept its size in all 12 runs, without `resizing`,
  `stale_shot` or `may_be_stale`.
- **#16 and #17 under #15** removed the `window_gone` errors of closing clicks (9 on 87e86d8, none on 0dd216c)
  and most of the time in tools that came with them.
- **The 500 ms after input** stayed: hyprmasq's own time per Thunderbird run is 8–13 s, against 4–7 s before #15,
  since almost every call there clicks or presses a key.
- **The file chooser maps too late for its `act`.** It appears 1.4–2.0 s after the click, 0.6–1.0 s after
  the result, so `act` missed it in 4 of 6 Forgejo runs on both builds, and the agent needed `windows` and
  `screenshot` to find it. Enter in the chooser closed it after the wait but before the capture in 2 of 3 Codex
  runs, which read `capture failed: no mapped window`.

## 2026-10-14: late windows, closes, and waits during work that does not draw

Two more PRs, each benchmarked from its head against 0dd216c (the last row of #15 above), then together on `main`:
#19 reports a window that maps after its `act` in the next call, a window that closes after the wait as closed,
and the window a closed one came from with its shot; #18 (finding 4) goes on watching the window through the
500 ms that #15 waits for new windows, and captures after a redraw in that time. Only gpt-6.1-sol ran on `main`,
five times per task.

### How finding 4 was measured

A replay ran the `act` calls of six recorded runs (Sonnet and gpt-6.1-sol, both tasks) one by one in a fresh VM,
with a plugin that logged every commit of the driven windows, and a 2.5 s quiet wait after each call. The commit
timelines then gave the moment each shot would have been taken under other wait rules, and whether a redraw that
was no animation or caret blink came after it. Of 80 `act` calls with input:

| Rule | Shots taken before a later redraw | Time added over the 80 calls |
| --- | --- | --- |
| 200 ms quiet, as before | 44 | – |
| 300 ms quiet | 40 | 10.1 s |
| 500 ms quiet | 32 | 33.1 s |
| Watch through the 500 ms after clicks and keys (#18) | 16 | 5.6 s |
| Watch again after each redraw | 13 | 28.7 s |

The redraws that a 200 ms quiet misses come 250 to 700 ms after the input: Chromium draws nothing for about half
a second after Enter in the address bar and then the page; Thunderbird draws its quick filter's results 250 to
700 ms after the typing, and a second time 250 to 300 ms after many clicks. A longer quiet time pays on every
call and still ends between two bursts, while the 500 ms after clicks and keys were spent anyway, without frame
callbacks, so an app on a hidden workspace could not even draw in them. What stays missed takes longer without
drawing, such as Thunderbird's account lookup, about 2 s.

### Results

| Build | Task | Agent | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0dd216c | Thunderbird | gpt-6.1-sol | 3/3 | 142 (106–192) | 20 (19–28) | 20 (19–25) | 3/3 |
| #18 4cba68a | | | 3/3 | 123 (107–133) | 23 (23–24) | 18 (18–20) | 3/3 |
| #19 c74ffde | | | 3/3 | 109 (98–124) | 22 (19–23) | 21 (17–21) | 3/3 |
| `main` 3d28984 | | | 4/5 | 106 (93–140) | 26 (24–36) | 18 (17–24) | 5/5 |
| 0dd216c | Forgejo | gpt-6.1-sol | 3/3 | 134 (104–170) | 21 (20–21) | 20 (20–21) | 3/3 |
| #18 4cba68a | | | 2/3 | 164 (156–192) | 30 (28–34) | 25 (25–29) | 3/3 |
| #19 c74ffde | | | 3/3 | 141 (96–158) | 29 (22–34) | 24 (18–25) | 3/3 |
| `main` 3d28984 | | | 5/5 | 138 (98–149) | 29 (22–35) | 23 (17–24) | 5/5 |
| 0dd216c | Thunderbird | Sonnet | 3/3 | 84 (74–88) | 32 (26–39) | 21 (18–21) | 3/3 |
| #18 4cba68a | | | 3/3 | 80 (71–99) | 40 (36–46) | 19 (16–23) | 3/3 |
| 0dd216c | Forgejo | Sonnet | 3/3 | 50 (49–62) | 21 (17–28) | 14 (13–19) | 3/3 |
| #18 4cba68a | | | 3/3 | 66 (55–66) | 21 (21–24) | 20 (18–22) | 3/3 |
| #19 c74ffde | | | 3/3 | 83 (60–87) | 30 (23–35) | 19 (16–25) | 3/3 |

- **#18 catches the second wave.** `act` said `drew again` 3–9 times a Thunderbird run and 1–5 times a Forgejo run,
  after navigations, the login, searches and the account setup. gpt-6.1-sol's wait-only calls in Thunderbird
  fell from 2–5 to 1–3 a run.
- **#19 closes the gaps of #15.** Every dialog that closed, the file chooser after Enter and the OAuth popup after
  Authorize, came back as `window closed · back to` the browser window with its shot, and Thunderbird's compose
  window after Send as `back to` the main window; `capture failed` is gone (2 of 3 gpt-6.1-sol runs on 0dd216c).
  gpt-6.1-sol took 2.8 screenshots a Forgejo run on `main`, against 5. The file chooser that maps after its click
  came with the next `act` when the agent acted next; half the agents called `windows` first, which marks it as
  opened by their input but has no shot.
- **The cost.** hyprmasq's own time per run rose from 8–13 s to 12–20 s in Thunderbird and from 9–10 s to 11–20 s
  in Forgejo: `act` now waits out the second wave and takes the shots of the window to go back to, time the agent
  spent before in a call of its own.
- **Wall times** move with the model's speed more than with hyprmasq: gpt-6.1-sol's median turn was 3.8–3.9 s on
  `main` against 5.0–5.8 s on 0dd216c, a few hours earlier. The Forgejo failure on #18 was the model's: it stopped to
  ask for approval at the OAuth prompt. Sonnet's longer Forgejo runs on #18 and #19 came from different paths
  through Forgejo's menus and from Ctrl+L, below; the Thunderbird failure on `main` misread room 4.12 again.

## 2026-10-15: shortcuts beside the user's window

#20 keeps the keyboard focus of the user's Wayland connection on the agent's window while that window's keys are
held, and for 250 ms after a shortcut key, and gives it back before the user's own input reaches Hyprland. Before,
the focus went back to the user's window after every input frame. Chromium runs a shortcut that a page may use
itself, such as Ctrl+L, Alt+Left or F6, only after the page has let its key go by, and by then the agent's window
was no longer the active one, so the shortcut did nothing (see #20). Benchmarked from its head 787e2df against
`main` at 3d28984, then merged as 0df36ba; gpt-6.1-sol only, five runs per task.

| Build | Task | Agent | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main` 3d28984 | Thunderbird | gpt-6.1-sol | 4/5 | 106 (93–140) | 26 (24–36) | 18 (17–24) | 5/5 |
| #20 787e2df | | | 5/5 | 86 (73–102) | 24 (22–27) | 18 (15–21) | 5/5 |
| `main` 3d28984 | Forgejo | gpt-6.1-sol | 5/5 | 138 (98–149) | 29 (22–35) | 23 (17–24) | 5/5 |
| #20 787e2df | | | 5/5 | 83 (71–109) | 19 (18–20) | 15 (15–15) | 5/5 |

- **Shortcuts take effect.** Ctrl+L, an address and Enter navigated the agent's window in 5 of 5 runs, against 0
  of 5 on `main`, where Alt+Left had also failed in 3 runs; counted from the window's title between the call and
  the next one.
- **Keyboard menus seemed to work too.** The agents open Forgejo's "Add file" menu with a click and pick "Upload
  file" with Down, Down and Enter. On `main` that opened "New file" in 5 of 5 runs, and the way back cost several
  calls; on #20 it opened "Upload file" in 5 of 5. Later runs on the same code went back from a wrong item again,
  at larger displays and at 1080p ([below](#2026-10-15-larger-displays-and-windows-that-waits-21)), so these 5 of 5
  were not #20's doing alone.
- **The user** kept their notes intact and their focus in all runs, as on `main`, and no result reported a shortcut
  interrupted by the user's typing.
- **Forgejo took 55 s less**, from fewer calls: every run took the same 15 calls, and the time outside tools fell
  from 110 to 64 s while gpt-6.1-sol's median turn barely changed (3.9 against 3.6 s). hyprmasq's own time fell
  from 29 to 19 s. In Thunderbird, where no window shares the user's connection, the calls stayed the same; its
  shorter runs follow the model's turn, 3.0 s against 3.8 s.
- **What is left in Forgejo** is the file chooser that maps after its click. All five agents then called `windows`
  and `screenshot`; in two runs the click's result said `no change`, with the hint that the input may have
  missed.

## 2026-10-15: larger displays, and `windows` that waits (#21)

The VM's monitor had been 1920×1080 at scale 1, where the 1280-pixel overview shrinks a full-screen window 1.5
times. `bench/run.py --display` now sets another, and `main` at 0df36ba ran at 2560×1440 at scale 1 (shrunk 2
times), 3840×2160 at scale 2 (the logical size of 1080p at twice the pixels) and 3840×2160 at scale 1 (3 times).
The 1080p rows are #20's head 787e2df, the same tree. gpt-6.1-sol, five runs per task. The VM draws in software,
so its apps and hyprmasq's captures slow down with the pixels, more than on real hardware.

| Display | Task | Passed | Wall s | In tools s | Calls | Images | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1920×1080 at 1 | Thunderbird | 5/5 | 86 (73–102) | 24 (22–27) | 18 (15–21) | 23 (18–24) | 5/5 |
| 2560×1440 at 1 | | 5/5 | 83 (77–218) | 21 (19–23) | 16 (15–18) | 20 (19–21) | 5/5 |
| 3840×2160 at 2 | | 5/5 | 92 (89–103) | 28 (25–30) | 18 (16–18) | 17 (17–19) | 5/5 |
| 3840×2160 at 1 | | 5/5 | 87 (84–98) | 26 (24–28) | 16 (16–18) | 17 (17–19) | 5/5 |
| 1920×1080 at 1 | Forgejo | 5/5 | 83 (71–109) | 19 (18–20) | 15 (15–15) | 18 (17–18) | 5/5 |
| 2560×1440 at 1 | | 5/5 | 89 (76–108) | 24 (22–29) | 18 (16–22) | 25 (23–32) | 5/5 |
| 3840×2160 at 2 | | 5/5 | 86 (76–102) | 23 (22–28) | 16 (15–21) | 16 (12–16) | 4/5 |
| 3840×2160 at 1 | | 5/5 | 96 (83–112) | 27 (24–35) | 18 (16–21) | 26 (19–38) | 5/5 |

- **Every run passed** at every size. Shots stayed 1280 pixels wide, so at 3840×2160 at scale 1 the browser's
  address bar showed text 4 to 5 pixels tall. Thunderbird's agents zoomed into a shot to read 3 times a run there,
  once at 1440p and never at 1080p.
- **At scale 2, the changes became full shots.** Zooms come at the monitor's scale, so at scale 2 they would have
  had more pixels than the full shot, and `act` sent the full shot instead (13 times in the browser task, 22 in
  Thunderbird). The agents then asked for `capture: "window"` in 3 to 4% of their `act` calls, against 19 to 61%
  at the other sizes. That alone shows no cost of the zooms ([how the runs are read](#how-the-runs-are-read)):
  most of Thunderbird's requests at 1440p followed zooms that had left out the Finish button, a bug
  ([#26](#2026-10-16-animations-that-hid-real-changes-26)), and in the browser agents asked for a full shot for the
  call after a page change.
- **Shortcuts beside the user's window kept working**: Ctrl+L and Alt+Left took effect 13 of 13 times at 1440p, 8
  of 8 at 3840×2160 at scale 2 and 7 of 9 at scale 1. But at each larger size `act` flagged 3 to 6 of the calls
  with a Ctrl or Alt chord `interrupted by the user's input`, against none at 1080p, mostly calls whose shortcut
  had worked.
- **The "Add file" menu** went wrong again: runs that went back from a wrong item with Alt+Left were 2 of 5 at
  1440p, 2 of 5 at 3840×2160 at scale 2 and 1 of 5 at scale 1, against none on #20 at 1080p. Not because of the
  display: the agents had clicked the opener of a menu that was still open, see
  [#22](#2026-10-15-the-add-file-menu-22).
- **A window on the user's workspace**: in one run at 3840×2160 at scale 2 (a new browser window), and in one run
  of #21 below (the OAuth popup), a window opened on the user's workspace with the keyboard focus for 8 to 9 s. The
  user had typed into their own window of the browser between the agent's click and the window's map, and #15 lets
  the user's input win then; the slower the app, the longer that gap. None at 1080p since 87e86d8.

### #21

#21 makes `windows` return a window that the agent's input opened after its `act` with its shot, return the shot
of the one window it lists, and wait for a window with `wait_ms`; after a click or key that changed nothing, the
hint says that a window may still open. `act` is unchanged. Benchmarked from its head bd556d8 at 1080p and 1440p,
then merged as 55b9245.

| Build | Display | Task | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main` 0df36ba | 1920×1080 at 1 | Forgejo | 5/5 | 83 (71–109) | 19 (18–20) | 15 (15–15) | 5/5 |
| #21 bd556d8 | | | 5/5 | 77 (68–96) | 19 (19–28) | 14 (14–20) | 5/5 |
| `main` 0df36ba | 2560×1440 at 1 | | 5/5 | 89 (76–108) | 24 (22–29) | 18 (16–22) | 5/5 |
| #21 bd556d8 | | | 5/5 | 103 (80–134) | 29 (20–35) | 20 (16–24) | 4/5 |
| `main` 0df36ba | 1920×1080 at 1 | Thunderbird | 5/5 | 86 (73–102) | 24 (22–27) | 18 (15–21) | 5/5 |
| #21 bd556d8 | | | 5/5 | 88 (84–98) | 25 (24–26) | 17 (15–19) | 5/5 |
| `main` 0df36ba | 2560×1440 at 1 | | 5/5 | 83 (77–218) | 21 (19–23) | 16 (15–18) | 5/5 |
| #21 bd556d8 | | | 5/5 | 79 (76–90) | 22 (21–25) | 15 (14–19) | 5/5 |

- **The file chooser costs one call.** After the click that opens it, all ten agents called `windows` with
  `wait_ms` 2000 to 5000 and got the chooser with its shot: one call before they acted in it, against `windows` and
  `screenshot` in all 20 runs on `main` at the four sizes. That stretch took 6.5 s (median) at 1080p against 10.2,
  and 5.6 s at 1440p against 8.3.
- **The browser task at 1440p took more calls** (20 against 18), from the "Add file" menu: 4 of 5 runs went back
  from a wrong item, against 2 of 5 on `main`, and 2 of 5 at 1080p, against none on #20. #21 does not change `act`.
- **Thunderbird** kept its calls, 17 and 15 against 18 and 16.

## 2026-10-15: the "Add file" menu (#22)

The menu went wrong for another reason than the one above. #22 measured it in the VM with Chromium's DevTools at
1080p and 1440p: the page opens the menu 5 to 15 ms after the click, and the keys of the same `act` came about
330 ms after it, so click, Down, Down and Enter picked "Upload file" 13 of 13 times from a closed menu, also while
the user typed. Every batch that went wrong followed a call that had clicked "Add file" alone and whose shot showed
the menu open: it stays open between calls without `lease`. The `act` description said that without `lease` a
menu is gone by the next call, so the agents clicked the opener again, which closed it, and Down then reopened it
with nothing highlighted, so the second Down picked "New file". #22 corrects the description (a menu may still be
open: click its entry if the shot shows it), and lets `act` wait, at most 1 s, for the window's answer to a click
that more actions of the batch follow, for apps that draw a menu later. Benchmarked from its head f6ce0c4 against
#21's head bd556d8, then merged as 4de3157.

| Build | Display | Task | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| #21 bd556d8 | 1920×1080 at 1 | Forgejo | 5/5 | 77 (68–96) | 19 (19–28) | 14 (14–20) | 5/5 |
| #22 f6ce0c4 | | | 5/5 | 82 (78–100) | 20 (19–23) | 15 (14–18) | 5/5 |
| #21 bd556d8 | 2560×1440 at 1 | | 5/5 | 103 (80–134) | 29 (20–35) | 20 (16–24) | 4/5 |
| #22 f6ce0c4 | | | 5/5 | 90 (84–101) | 23 (21–26) | 16 (15–18) | 5/5 |
| #21 bd556d8 | 1920×1080 at 1 | Thunderbird | 5/5 | 88 (84–98) | 25 (24–26) | 17 (15–19) | 5/5 |
| #22 f6ce0c4 | | | 5/5 | 101 (85–105) | 26 (24–28) | 17 (15–18) | 5/5 |
| #21 bd556d8 | 2560×1440 at 1 | | 5/5 | 79 (76–90) | 22 (21–25) | 15 (14–19) | 5/5 |
| #22 f6ce0c4 | | | 5/5 | 82 (80–98) | 21 (21–23) | 15 (14–17) | 5/5 |

- **Half the menu detours are gone**: runs that went back from a wrong entry with Alt+Left fell from 2 to 1 of 5 at
  1080p and from 4 to 2 of 5 at 1440p. In the three left, the agent still clicked the opener of a menu that its
  shot showed open; every result still ends in `· no lease`.
- **The wait cost nothing measurable**: hyprmasq's own time per run stayed within the ranges of #21. Thunderbird's
  longer runs at 1080p came from outside the tools, with the same calls.

## 2026-10-15: the lease without menus (#23)

The three runs on #22 that still reopened an open menu had read it in two other texts: every `act` summary of a
window without a lease ended in `· no lease`, and `windows` described the capability as "focus can be held between
act calls, so menus stay open". #23 describes the lease by the focus it keeps, leaves whether a menu is open to the
shot, and names the lease in a summary only while one is held. Benchmarked from its head 5199835 against `main` at
4de3157: #22's runs above, which had the same tree, and five more runs of the browser task per size, for 10.
Merged as 5bb5766.

| Build | Display | Task | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main` 4de3157 | 1920×1080 at 1 | Forgejo | 10/10 | 82 (78–120) | 20 (19–30) | 15.5 (14–23) | 10/10 |
| #23 5199835 | | | 9/10 | 83 (76–118) | 20 (19–31) | 16 (15–22) | 10/10 |
| `main` 4de3157 | 2560×1440 at 1 | | 10/10 | 88 (77–123) | 23 (20–30) | 16.5 (15–21) | 9/10 |
| #23 5199835 | | | 10/10 | 84 (80–100) | 22 (20–26) | 16 (14–19) | 10/10 |
| `main` 4de3157 | 1920×1080 at 1 | Thunderbird | 5/5 | 101 (85–105) | 26 (24–28) | 17 (15–18) | 5/5 |
| #23 5199835 | | | 5/5 | 93 (78–110) | 25 (23–29) | 17 (15–21) | 5/5 |
| `main` 4de3157 | 2560×1440 at 1 | | 5/5 | 82 (80–98) | 21 (21–23) | 15 (14–17) | 5/5 |
| #23 5199835 | | | 5/5 | 86 (80–93) | 23 (22–24) | 16 (14–16) | 5/5 |

- **No agent reopened an open menu**: 0 of 20 browser runs, against 5 of 20 on `main`, and none went back from a
  wrong entry with Alt+Left. The median calls stayed at 16, since most runs had not gone wrong before either.
- **The failure** made a second commit: the agent clicked the commit-message field and typed, and the text went
  into the folder field above it. Its shot, the one `act` returned when the file chooser closed, already showed the
  form below the uploaded file, and the click's coordinates were on the commit-message field there, so the form had
  not moved; the folder field kept or took the keyboard focus. Why is open.

## 2026-10-16: targets that moved since the shot (#24)

A run on #18 lost a click to a page that moved: the agent took a `screenshot` while Forgejo still uploaded the file,
the upload finished and moved the form 13 pixels down, and the click on the commit-message field landed above it.
#24 makes `act` refuse a click or drag whose surroundings changed since the shot it was read from: when the window
has drawn since that shot, it captures the window and compares a 49×49 logical-pixel box around each point,
ignoring carets, animations the wait saw and content that keeps changing, and returns a fresh shot without sending
anything. When the window did not draw, nothing is captured. The shot of the window that a closed dialog came from
now waits for that window's answer, at most 1.5 s. Benchmarked from 229a04a, with 10 browser and 5 Thunderbird
runs per size, and from its final head ce100b9, with 5 browser runs per size; merged as 7959faf.

| Build | Display | Task | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `main` 5bb5766 | 1920×1080 at 1 | Forgejo | 9/10 | 83 (76–118) | 20 (19–31) | 16 (15–22) | 10/10 |
| #24 229a04a | | | 10/10 | 68 (65–77) | 20 (19–21) | 15 (15–15) | 10/10 |
| #24 ce100b9 | | | 5/5 | 68 (65–120) | 20 (20–30) | 15 (15–25) | 4/5 |
| `main` 5bb5766 | 2560×1440 at 1 | | 10/10 | 84 (80–100) | 22 (20–26) | 16 (14–19) | 10/10 |
| #24 229a04a | | | 10/10 | 72 (63–94) | 22 (20–26) | 16 (14–20) | 9/10 |
| #24 ce100b9 | | | 5/5 | 74 (68–89) | 22 (21–24) | 16 (15–16) | 5/5 |
| `main` 5bb5766 | 1920×1080 at 1 | Thunderbird | 5/5 | 93 (78–110) | 25 (23–29) | 17 (15–21) | 5/5 |
| #24 229a04a | | | 4/5 | 90 (77–94) | 27 (23–28) | 17 (15–20) | 5/5 |
| `main` 5bb5766 | 2560×1440 at 1 | | 5/5 | 86 (80–93) | 23 (22–24) | 16 (14–16) | 5/5 |
| #24 229a04a | | | 5/5 | 82 (78–93) | 23 (22–26) | 16 (15–19) | 5/5 |

- **One refusal in 40 runs, and a right one**: the OAuth popup's Authorize button moved about 100 pixels up between
  the agent's `screenshot` and its click. The next click, at the button's place in the returned shot, worked. No
  click was refused where nothing had moved.
- **No cost**: hyprmasq's own time per run stayed the same. The shorter browser runs came from outside the tools.
- **The rest**, as on `main`: one Thunderbird run misread room 4.12, and in 2 browser runs the OAuth popup opened on
  the user's workspace after the user had typed into their own window. 1 of the 50 browser runs since #23 reopened
  an open menu, against 5 of 20 before.

## 2026-10-16: the user's line breaks (#25)

In 5 of 100 browser runs since #20, a window that the agent's click opened, mostly the OAuth popup, went to the
user's workspace with the keyboard focus for 8 to 11 s, and the user's next line of notes went into it. #15 gives a
window without a parent to the user when the user's input to the app after the agent's could have opened it, and
counted Enter unless the app reports a text field through text-input, which Chromium does only with
`--enable-wayland-ime`. The simulated user ends each line of notes with Enter, every 8 s or so, and the popup maps
about 0.35 s after the agent's click, so an Enter fell in between about once in 20 popups. Any user typing lines in
their browser while the agent works hits the same. #25 counts an Enter within 2 s of typing into the same window as
a line break, and decides a Wayland window by the input up to its first commit, when the app creates it: Chromium
creates the popup about 60 ms after the click and maps it about 300 ms after. Benchmarked from 4902aec, with 10
browser and 5 Thunderbird runs per size, and from its final head c552fb6, with 5 browser runs per size; merged as
7c0514a.

| Build | Display | Task | Passed | Wall s | In tools s | Calls | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| #25 4902aec | 1920×1080 at 1 | Forgejo | 10/10 | 80 (72–95) | 20 (19–24) | 15 (15–17) | 10/10 |
| #25 c552fb6 | | | 5/5 | 72 (68–77) | 20 (19–22) | 15 (15–17) | 5/5 |
| #25 4902aec | 2560×1440 at 1 | | 10/10 | 77 (71–256) | 23 (20–24) | 15 (14–16) | 10/10 |
| #25 c552fb6 | | | 4/5 | 73 (66–145) | 21 (21–24) | 15 (15–15) | 5/5 |
| #25 4902aec | 1920×1080 at 1 | Thunderbird | 5/5 | 94 (76–104) | 25 (23–31) | 17 (15–19) | 5/5 |
| #25 4902aec | 2560×1440 at 1 | | 5/5 | 80 (73–83) | 23 (21–24) | 16 (15–17) | 5/5 |

- **The user was undisturbed in all 40 runs**, and no window was given to the user's input, against 5 of 100
  browser runs before. At this rate, 40 runs would have shown about two.
- **The one failure** was the model's: it stopped at the OAuth prompt to wait for approval, as a run on #18 did.

## 2026-10-16: animations that hid real changes (#26)

`act` left out every change within the box of an animation that the wait reported, and the plugin finds animations
by the boxes that commits damage. Thunderbird's renderer damages whole tiles of 1024×512 logical pixels. After the
password, the completion page of the Account Hub drew into one of them for about a second, so the tile counted as an
animation, and its content, the Finish button, two links, a line and an icon, was left out of the changes although
the compared image showed it. Since the animation limit is 15% of the window and the tile has a fixed size, this hit
2560×1440 (14%) and 3840×2160 (6%), not 1920×1080 (25%). #26 leaves out a change within an animation's box only where
a second image of that box, taken a moment later, still differs, and takes that image only when it can change the
result. Benchmarked on Thunderbird only, the one task where changes were left out as animations: from 1bf9c05 at
three sizes, and from its final head c6b85d6 at the two larger ones; merged as 2f2ea9c.

| Build | Display | Passed | Wall s | In tools s | Calls | hyprmasq s | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| #25 4902aec | 1920×1080 at 1 | 5/5 | 94 (76–104) | 25 (23–31) | 17 (15–19) | 14.6 | 5/5 |
| #26 1bf9c05 | | 5/5 | 102 (85–120) | 25 (24–35) | 18 (15–20) | 13.8 | 5/5 |
| #25 4902aec | 2560×1440 at 1 | 5/5 | 80 (73–83) | 23 (21–24) | 16 (15–17) | 12.8 | 5/5 |
| #26 1bf9c05 | | 5/5 | 87 (74–89) | 24 (22–26) | 15 (12–16) | 14.9 | 5/5 |
| #26 c6b85d6 | | 5/5 | 82 (76–84) | 23 (23–24) | 15 (14–15) | 14.1 | 5/5 |
| `main` 0df36ba | 3840×2160 at 1 | 5/5 | 87 (84–98) | 26 (24–28) | 16 (16–18) | 15.7 | 5/5 |
| #26 1bf9c05 | | 5/5 | 94 (93–102) | 29 (29–32) | 16 (16–17) | 20.1 | 5/5 |
| #26 c6b85d6 | | 5/5 | 96 (85–103) | 27 (26–32) | 16 (15–17) | 17.4 | 5/5 |

*hyprmasq s* is the median of `analyze.py`'s time that `act` adds beyond waits, pauses and typing. The 3840×2160
baseline is older than #22, which made the click on Finish and the Inbox wait for the first click's answer.

- **No change was left out as an animation** in the 25 runs, against the password step in 28 of 30 runs at 2560×1440
  and 2 of 5 at 3840×2160 before. That step now comes as a full shot with the completion page.
- **The new looks after that step** fell from 4 and 5 of 5 runs on #25 and #24 at 2560×1440 to 3 of 10. The three,
  and one at each other size, have another cause, which `main` has too: the completion page draws either about 0.5
  or about 1 s after the wait ends, and the watch for a second wave lasts 0.5 s, so the result can show the empty
  page in between. In alternating trials in the VM, `main` missed it in 3 of 4 and #26 in 0 of 4.
- **Full shots asked for** at 2560×1440: 13 of 59 `act` calls on c6b85d6, against 24 of 62 on #25. At 3840×2160 they
  rose to 27 of 59 against 11 of 56, mostly on message clicks in two runs, with the same calls per run.
- **The cost**: on 1bf9c05 the second image was taken whenever a change touched an animation's box, five times a
  session at 3840×2160, without changing a result; c6b85d6 takes it only when it can, and of the boxes alone (51 ms
  for the tile at 2560×1440, against 85 ms for the window). hyprmasq's own time per run is 1.3 s above #25 at
  2560×1440, where the password step's full shot costs more than the four zooms did.

## 2026-10-16: the Calc task

The third task ([bench/README.md](../bench/README.md#calc)): Mara's Firefox shows five reports of the company
intranet as tabs, and the agent enters 18 monthly figures from them into her sheet in LibreOffice Calc, adds the
totals, an average and a growth as formulas, and saves. Each page has something to read carefully: a region's total
row, a budget column, a superseded draft, figures in EUR thousands, last year's figure in the prose. Firefox and Calc
share one hidden workspace at 2560×1440, and have one each at 1920×1080. hyprmasq as on `main` at 649f320, the task
at f156ac5; the sheets were graded again with the final grader of #27, with the same verdicts. gpt-6.1-sol, five
runs per size.

| Display | Passed | Wall s | In tools s | Calls | hyprmasq s | Sheet saved at s | User undisturbed |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1920×1080 at 1 | 5/5 | 112 (98–114) | 30 (26–33) | 21 (18–22) | 14.4 | 102 (68–106) | 5/5 |
| 2560×1440 at 1 | 5/5 | 95 (80–100) | 28 (22–28) | 18 (16–19) | 11.8 | 86 (71–92) | 5/5 |

- **Every run read every trap right**, and every sheet used `SUM`, `AVERAGE` and a ratio of the two months.
- **The Name Box**: to start entering at B5, all 10 agents clicked Calc's Name Box and typed `B5`. The click put
  the caret after the `A1` there instead of selecting it, so in 8 runs the box got `A1B5`, and the figures that
  followed went from A1 over Mara's labels. Those 8 agents saw it in the result and undid it, with 27 or 28 times
  Ctrl+Z, at the cost of two calls; the other 2 had pressed Ctrl+A in the box first. Whether a real click selects
  the box's text in this LibreOffice, so that the emulated click differs from it, is not checked yet.
- **Full shots asked for**: in 51 of 77 `act` calls at 1920×1080 and 27 of 64 at 2560×1440, against 10 to 39% in
  the other tasks. After the Name Box went wrong, agents asked for one on 49 of the 53 calls left; before it, on
  0 of 8 to 10 calls in five runs, and in the others from a tab switch on, after a switch had come as zooms.
- **One click refused**, rightly: the shot of Calc that `act` returned when the Manage Names dialog closed showed
  its toolbar greyed out, and by the next call the buttons had come back, so the clicks on two of them were
  refused with `stale_shot` and a fresh shot, and the next call worked.
- **Four results after a tab switch** came as a full shot because the shot before `cannot be compared`, beside
  Firefox's overlay scrollbar, which the wait named as an animation.
- **Tidying up**: an earlier wording of the prompt ("change nothing else") had the agents spend three to four calls
  putting Mara's tabs back on their first page; with "leave Mara's tabs open on their reports", one run of ten went
  back to the first tab, the others stopped after saving.

## Open

- **Typing that lands in another field**: in 1 of about 140 browser runs, the text after a click on Forgejo's
  commit-message field went into the folder field, with the shot and the click right.
- **`interrupted` flags shortcuts that worked**, at larger displays in 3 to 6 calls a task, and reads as a failure
  next to "delivered".
- **Shortcuts that an app runs more than 250 ms after the key** still fail without a word beside the user's window.
- **Work that draws nothing for longer than the watch**, such as Thunderbird's account lookup: every Thunderbird
  run on #23 to #25 got `Looking up configuration` from the `act` that pressed Continue and made an `act` without
  actions to wait it out, 30 of the 49 new looks after zooms. After the password, the completion page draws about
  0.5 or 1 s after the wait, and the 0.5 s watch for a second wave misses the later one.
- **hyprmasq's own time per call**, 19 to 29 s a run.
- **Calc's Name Box** keeps its text when clicked, so typing into it appends: 8 of 10 Calc runs, two calls each.
  Not known yet whether a real click selects it.
- **The shot of the window a dialog came from** can be taken before that window has drawn itself active again,
  such as Calc's toolbar greyed out after Manage Names, which gets the next click refused.
- More apps, to settle floating and placement: a Qt app, an Electron app.

Settled, and not for hyprmasq:

- **Small text** is a limit of the images the harness gives the model, not of hyprmasq. A region of `screenshot`
  captures an area again at the monitor's resolution, and agents use it: in Thunderbird since #20, 1.1 region shots
  a run at 2560×1440 and 3.0 at 3840×2160 at scale 1, with no wrong room in 55 runs. The three runs that sent a
  wrong room (1.12 or 2.12 for 4.12) were at 1920×1080 and read the overview without zooming.
