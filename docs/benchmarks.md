# Benchmarks

The benchmark measures how well coding agents work through `hyprmasq-mcp`.
Each run gives an agent a task in an offline NixOS VM, such as answering a
mail in Thunderbird or uploading a file to a Forgejo repository in a browser.
The agent's window sits on a hidden workspace while the user's own work stays
on the visible one. In the browser task, the user keeps typing into another
window of the same browser. The agent can use only hyprmasq's MCP tools. The
benchmark grades the result from the VM's state, times every call from both
sides of the MCP connection, and asks the agent afterwards where hyprmasq
slowed it down. [bench/README.md](../bench/README.md) describes the tasks, the
setup and how to run it.

## How the runs are read

A change is judged by what the agents did before and after it, from the MCP
log, the agent's transcript and Hyprland's events in the VM:

- whether the task passed, wall time, number of calls, time spent in tools
  and hyprmasq's own share of it
- extra looks: a `screenshot`, or an `act` without actions, right after a
  result that did not show the agent what it needed
- misses: input whose result shows no change, a wrong target, or a way back
  from a wrong page or entry
- the model's time from a result to its next call.

The agent's debrief points to where to look in the logs. It is no evidence
on its own: an agent knows only the run it had, and cannot compare it with a
run without a feature.

## Results

hyprmasq 0.1.0, commit
[9ae9280](https://github.com/nyrda/hyprmasq/commit/9ae92806c9f8de43e428b0bdba9b17fbaeafe48f),
run on 2026-10-19. Six agents ran all four tasks three times each: 72 runs in sequential
offline VMs, at `1920x1080@1`. Board used the `dnd` variant. The host had an AMD Ryzen 7
5800X3D (8 cores, 16 threads) and 31 GiB of memory. The VM draws in software, so wall
and in-tool times depend on the CPU.

- Codex with a ChatGPT login: `gpt-6.1-sol` and `gpt-6-astra`.
- Claude Code with the Anthropic API key through `claude-api`: `claude-opus-5-5`,
  `claude-sonnet-5-5` and `claude-haiku-5-5`.
- pi with the TensorX API key: `tensorx/deepseek/deepseek-v4.1-flash-1017`.

All requested `medium` effort. Codex and Claude Code reported `medium`, while pi clamped
DeepSeek to `high`, the lowest level its model configuration supports. Harness versions
were Codex CLI 0.163.0, Claude Code 2.1.302 and pi 1.3.0.

Tables show medians and ranges over three runs, using `bench/report.py` formatting. Wall
time excludes VM boot and debrief. In tools is time inside MCP calls. Turn is the median
of a run's gaps outside tool calls, including model and harness time: from the agent's
start to its first call, between calls, and from the last result to the agent's exit.
Provider image-limit endings count as failed runs here, including one Calc run whose
saved state passed the VM checker.

Input k is the prompt tokens of all the run's requests in thousands, cache reads and
writes included. Cached % is the share of them read from the provider's cache, and
Output k includes thinking. Cost $ is at list prices without the debrief. For Claude Code
it is its own figure for the Anthropic API, and for pi its figure for TensorX. The Codex
runs used a ChatGPT subscription, so their cost is what the same requests would have cost
on OpenAI's API at its list prices on 2026-10-19.

Across all 72 runs, the user's focus and visible workspace stayed unchanged, and no
window opened on their workspace. All 18 Forgejo checks preserved the user's typed
notes. The benchmark did not measure the clipboard.

Elapsed time was 4h 25m 46s, including builds, lock waits and debriefs. Agent wall time
totalled 2h 46m 6s. No runs were retried. Pi automatically retried one TensorX 504
within the first board run and resumed successfully.

### Tokens and cost

All 12 runs of each agent:

| Agent | Passed | Input M | Cached % | Output k | Cost $ | Cost per passed run $ |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | 11/12 | 3.67 | 88 | 20 | 1.42 | 0.13 |
| gpt-6-astra | 12/12 | 3.40 | 92 | 18 | 6.75 | 0.56 |
| Opus 5.5 | 12/12 | 3.27 | 90 | 43 | 3.12 | 0.26 |
| Sonnet 5.5 | 12/12 | 2.90 | 89 | 33 | 1.40 | 0.12 |
| Haiku 5.5 | 10/12 | 3.98 | 91 | 64 | 0.11 | 0.01 |
| DeepSeek v4.1 Flash | 6/12 | 9.51 | 92 | 209 | 1.83 | 0.30 |

The 72 runs came to $14.62. Most of every prompt was a cache read, so the cost follows
the cache price more than the input price. Codex reported no cache writes, so its
estimate prices all uncached input at the input rate. Billed as cache writes instead,
the Codex runs would cost up to 16% more for gpt-6.1-sol and 10% more for gpt-6-astra.
DeepSeek's prompt total is largest because its board runs went on for up to 65 calls
before the provider's image limit ended them.

### Board

Place nine labels on a motherboard photo in Firefox, using HTML5 drag-and-drop.

| Agent | Passed | Wall s | In tools s | Turn s | Calls | Images | Input k | Cached % | Output k | Cost $ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | 2/3 | 53 (48–58) | 13 (12–13) | 6.0 (5.1–7.1) | 6 (5–6) | 8 (7–8) | 119 (101–119) | 82 (77–86) | 1 (1–1) | 0.055 (0.054–0.073) |
| gpt-6-astra | 3/3 | 50 (48–53) | 12 (12–12) | 6.2 (5.7–6.3) | 5 (5–5) | 7 (7–8) | 101 (100–101) | 82 (82–89) | 1 (1–1) | 0.304 (0.231–0.305) |
| Opus 5.5 | 3/3 | 47 (44–57) | 13 (12–13) | 6.4 (6.0–6.9) | 5 (4–6) | 6 (5–7) | 55 (53–84) | 74 (73–81) | 2 (2–3) | 0.124 (0.118–0.153) |
| Sonnet 5.5 | 3/3 | 32 (28–32) | 12 (12–13) | 3.1 (2.4–4.7) | 4 (4–5) | 9 (8–11) | 52 (52–53) | 74 (74–74) | 1 (1–1) | 0.051 (0.048–0.053) |
| Haiku 5.5 | 2/3 | 53 (52–92) | 16 (15–27) | 2.9 (2.5–4.6) | 9 (7–21) | 12 (10–30) | 164 (115–439) | 85 (82–93) | 5 (5–9) | 0.007 (0.006–0.012) |
| DeepSeek v4.1 Flash | 0/3 | 1733 (624–1741) | 27 (19–43) | 17.6 (11.1–28.3) | 61 (57–65) | 65 (65–65) | 1906 (1210–1956) | 95 (93–95) | 40 (33–59) | 0.345 (0.237–0.382) |

What we saw:

- Attribution mapped all 150 drags: 140 correct drops and 10 model target errors, with
  none attributed to hyprmasq. Haiku repetition 1 left SATA ports off its part, and
  gpt-6.1-sol repetition 2 misplaced CPU power headers and M.2 slots. The other target
  errors were corrected during their runs.
- All three DeepSeek runs ended at the provider's 64-image limit, each with 65 images,
  after 65, 61 and 57 calls respectively. They placed 0, 1 and 5 labels correctly. The
  model's image use exhausted the provider budget. These runs count as failures and
  were not retried.

### Calc

Read five intranet reports in Firefox and fill, calculate and save a LibreOffice sheet.

| Agent | Passed | Wall s | In tools s | Turn s | Calls | Images | Input k | Cached % | Output k | Cost $ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | 3/3 | 87 (87–93) | 22 (20–23) | 3.4 (3.2–4.2) | 17 (16–17) | 23 (21–27) | 327 (301–328) | 88 (84–92) | 2 (2–2) | 0.127 (0.102–0.140) |
| gpt-6-astra | 3/3 | 92 (87–95) | 18 (17–19) | 3.7 (3.7–4.4) | 16 (15–16) | 19 (17–20) | 279 (275–303) | 93 (93–94) | 2 (2–2) | 0.546 (0.539–0.570) |
| Opus 5.5 | 3/3 | 68 (67–76) | 17 (16–18) | 3.2 (3.0–3.4) | 16 (13–17) | 15 (12–16) | 234 (225–261) | 88 (88–89) | 4 (3–4) | 0.254 (0.247–0.264) |
| Sonnet 5.5 | 3/3 | 58 (51–66) | 19 (19–20) | 2.4 (2.1–2.5) | 14 (13–16) | 14 (14–15) | 221 (196–278) | 88 (87–89) | 3 (3–4) | 0.118 (0.111–0.139) |
| Haiku 5.5 | 2/3 | 68 (53–71) | 18 (18–22) | 1.9 (1.5–2.3) | 17 (17–19) | 19 (14–31) | 358 (299–375) | 91 (88–91) | 7 (6–7) | 0.011 (0.010–0.011) |
| DeepSeek v4.1 Flash | 0/3 | 126 (123–154) | 27 (27–32) | 3.1 (2.5–3.4) | 30 (28–31) | 65 (65–69) | 523 (450–565) | 88 (88–89) | 10 (9–11) | 0.106 (0.093–0.112) |

What we saw:

- Haiku repetition 3 selected E14 explicitly and saved the summary formulas in E14/E15
  instead of B14/B15. Input reached the requested cells, but the model chose the wrong
  column.
- DeepSeek hit the 64-image limit in all three runs: 65 images/28 calls, 65/30 and
  69/31. Repetitions 1 and 3 left the sheet unsaved. Repetition 2 saved a correct sheet
  before the cap ended the run, so the raw VM report gives DeepSeek 1/3, but the release
  tally counts the cap ending as failed and gives 0/3.
- One Sonnet run requested an unsupported `triple_click` action. The server refused it
  without input, and the model recovered and passed.

### Forgejo

Upload a file in Chromium and connect a release dashboard through an OAuth popup.

| Agent | Passed | Wall s | In tools s | Turn s | Calls | Images | Input k | Cached % | Output k | Cost $ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | 3/3 | 72 (62–87) | 18 (18–21) | 3.0 (2.4–3.7) | 15 (15–17) | 26 (26–28) | 305 (305–349) | 88 (88–89) | 2 (2–2) | 0.115 (0.114–0.124) |
| gpt-6-astra | 3/3 | 91 (82–91) | 19 (19–19) | 3.5 (3.5–3.9) | 16 (16–16) | 24 (20–28) | 331 (328–332) | 93 (91–93) | 2 (2–2) | 0.609 (0.606–0.676) |
| Opus 5.5 | 3/3 | 80 (72–109) | 19 (19–24) | 3.3 (2.8–3.8) | 17 (16–22) | 14 (14–26) | 304 (276–449) | 90 (89–92) | 3 (3–4) | 0.264 (0.254–0.338) |
| Sonnet 5.5 | 3/3 | 51 (38–53) | 15 (15–16) | 2.3 (1.8–3.1) | 14 (11–16) | 12 (9–14) | 194 (158–251) | 86 (86–88) | 2 (2–3) | 0.107 (0.087–0.122) |
| Haiku 5.5 | 3/3 | 60 (56–65) | 25 (21–34) | 1.8 (1.4–2.1) | 21 (16–21) | 16 (14–23) | 345 (295–409) | 91 (90–91) | 4 (3–4) | 0.009 (0.008–0.010) |
| DeepSeek v4.1 Flash | 3/3 | 66 (64–76) | 20 (19–22) | 2.4 (2.2–2.5) | 18 (18–20) | 20 (19–22) | 211 (203–246) | 86 (86–86) | 4 (3–5) | 0.043 (0.042–0.053) |

What we saw:

- All 18 runs uploaded the correct file in exactly one commit with the required message
  and connected the dashboard to the account.
- In 17/18 runs, the GTK portal file chooser mapped 0.36–0.72 s after the triggering
  `act` returned. Its result therefore did not report the chooser, and agents needed a
  separate window lookup. This was the recurring hyprmasq discovery cost in this task.
- Haiku repetition 1 recovered from a `stale_shot` refusal. Repetition 2 added 15 s of
  explicit pause actions, which are included in its tool time.

### Thunderbird

Configure the mail account, find the answer in the inbox and reply to the right thread.

| Agent | Passed | Wall s | In tools s | Turn s | Calls | Images | Input k | Cached % | Output k | Cost $ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | 3/3 | 107 (83–108) | 27 (23–28) | 3.0 (2.9–3.3) | 19 (18–21) | 31 (24–33) | 459 (427–534) | 88 (88–89) | 2 (2–2) | 0.162 (0.154–0.198) |
| gpt-6-astra | 3/3 | 106 (87–129) | 24 (21–29) | 3.7 (3.5–4.0) | 17 (15–21) | 22 (19–28) | 389 (342–514) | 94 (89–94) | 2 (2–2) | 0.768 (0.703–0.891) |
| Opus 5.5 | 3/3 | 139 (129–160) | 56 (46–70) | 3.9 (3.8–3.9) | 19 (19–22) | 20 (20–22) | 446 (363–522) | 92 (90–93) | 5 (5–6) | 0.359 (0.345–0.397) |
| Sonnet 5.5 | 3/3 | 90 (82–90) | 33 (33–35) | 2.3 (2.1–2.8) | 21 (20–24) | 22 (21–25) | 434 (427–588) | 91 (91–93) | 4 (4–5) | 0.177 (0.174–0.208) |
| Haiku 5.5 | 3/3 | 73 (73–87) | 34 (32–42) | 2.2 (1.8–2.2) | 17 (17–22) | 21 (20–32) | 347 (343–488) | 91 (91–92) | 5 (4–6) | 0.009 (0.009–0.012) |
| DeepSeek v4.1 Flash | 3/3 | 218 (175–387) | 71 (58–76) | 3.8 (3.0–5.7) | 35 (33–42) | 41 (36–58) | 699 (557–980) | 91 (90–92) | 13 (8–14) | 0.133 (0.105–0.177) |

What we saw:

- All 18 runs sent exactly one reply to the required recipient, in the right thread,
  with room 4.12 in the answer. No run ended at the image limit.
- Longer waits contributed to tool time. Opus repetition 2 set quiet waits of 0.5–3 s on
  18 calls and accumulated 47.4 s of settling waits. One call returned "still drawing"
  after 13.4 s, with its input delivered, and the run passed. DeepSeek repetition 1 added
  36.7 s of pause actions.

## Form

The form task measures speed alone: a questionnaire in a native GTK 4 app whose 25 questions each say their
answer, in four kinds that take turns: tick checkboxes, set a slider to a number on its read-only value, type a
given text, and choose in a dropdown. Then Submit. [bench/README.md](../bench/README.md#form) describes it; `--variant`
sets 10 to 40 questions, and 25, the default, is the one reported here.

hyprmasq's plugin and server as of commit
[ea3d206](https://github.com/nyrda/hyprmasq/commit/ea3d206), which the form task does not change, with the task as
of its branch's commit `144e845`, run on 2026-10-21. Four models
ran at `low`, `medium` and `high` effort, three times each: 36 runs in sequential offline VMs, at `1920x1080@1`, on
the same AMD Ryzen 7 5800X3D host as the release results. A game was running on the host for at least part of the
runs, so wall and in-tool times may be somewhat longer than on an idle host.

- Codex with a ChatGPT login: `gpt-6.1-sol` and `gpt-6-astra`.
- Claude Code with a claude.ai login: `claude-opus-5-5` and `claude-sonnet-5-5`.

Every run ran at the effort it requested, by the harnesses' own records. Harness versions were Codex CLI 0.163.0
and Claude Code 2.1.304. Cost $ is Claude Code's own figure for the Anthropic API and, for Codex, what the same
requests would cost on OpenAI's API at its list prices; both logins are subscriptions. All right at s is when every
answer on screen was first right at once, in seconds after the agent's start.

All 36 runs passed, with all 25 answers right in their last submission and one submission each. No window other
than the questionnaire opened, and the user's focus and visible workspace stayed unchanged in all 36. From the first
agent's start to the last one's end took 1h 39m; agent wall time totalled 1h 12m, of which 34m were in tools. The 36
runs came to $12.60.

| Agent | Effort | Passed | Wall s | In tools s | Turn s | Calls | Actions | Images | Input k | Cached % | Output k | Cost $ | All right at s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | low | 3/3 | 116 (93–141) | 53 (52–58) | 3.6 (2.3–4.2) | 15 (14–17) | 62 (59–81) | 29 (17–33) | 327 (293–364) | 91 (91–93) | 2 (2–2) | 0.106 (0.098–0.106) | 106 (86–122) |
| gpt-6.1-sol | medium | 3/3 | 121 (114–125) | 54 (50–54) | 4.0 (3.9–4.2) | 15 (14–16) | 77 (76–81) | 32 (24–38) | 320 (289–337) | 87 (85–90) | 2 (2–2) | 0.131 (0.116–0.140) | 109 (104–113) |
| gpt-6.1-sol | high | 3/3 | 122 (118–130) | 57 (53–65) | 3.7 (3.1–4.0) | 15 (14–19) | 71 (68–76) | 25 (14–41) | 301 (292–425) | 91 (86–93) | 2 (2–3) | 0.102 (0.090–0.182) | 113 (109–122) |
| gpt-6-astra | low | 3/3 | 131 (116–145) | 54 (53–54) | 4.6 (3.8–4.7) | 14 (14–17) | 60 (58–69) | 33 (33–36) | 304 (297–371) | 93 (87–94) | 2 (2–2) | 0.674 (0.585–0.752) | 117 (108–137) |
| gpt-6-astra | medium | 3/3 | 143 (131–167) | 51 (50–56) | 5.4 (4.8–6.6) | 14 (14–15) | 64 (62–73) | 18 (14–27) | 286 (282–313) | 89 (87–93) | 2 (2–2) | 0.710 (0.551–0.725) | 136 (127–155) |
| gpt-6-astra | high | 3/3 | 152 (144–165) | 55 (55–56) | 5.5 (5.2–5.5) | 18 (14–19) | 63 (61–76) | 17 (17–36) | 332 (293–387) | 91 (86–92) | 2 (2–2) | 0.768 (0.642–0.879) | 135 (129–142) |
| Opus 5.5 | low | 3/3 | 110 (106–112) | 54 (54–61) | 2.5 (2.5–3.4) | 17 (15–18) | 59 (57–69) | 28 (19–30) | 396 (311–425) | 93 (90–93) | 4 (4–4) | 0.372 (0.367–0.398) | 95 (91–97) |
| Opus 5.5 | medium | 3/3 | 110 (106–116) | 58 (57–62) | 2.6 (2.4–2.7) | 17 (16–19) | 66 (61–68) | 18 (16–23) | 397 (363–474) | 93 (93–94) | 4 (4–4) | 0.371 (0.355–0.419) | 97 (89–99) |
| Opus 5.5 | high | 3/3 | 122 (115–127) | 60 (60–64) | 2.4 (2.2–3.0) | 20 (19–22) | 59 (59–76) | 28 (17–41) | 494 (453–574) | 94 (94–94) | 4 (4–5) | 0.420 (0.407–0.467) | 101 (96–104) |
| Sonnet 5.5 | low | 3/3 | 101 (96–112) | 60 (57–61) | 2.4 (2.1–2.9) | 17 (15–17) | 61 (60–62) | 23 (22–27) | 361 (325–365) | 93 (93–93) | 4 (4–4) | 0.172 (0.164–0.179) | 87 (86–98) |
| Sonnet 5.5 | medium | 3/3 | 93 (86–96) | 55 (54–56) | 2.0 (1.9–2.0) | 17 (15–19) | 59 (58–61) | 21 (20–22) | 397 (295–414) | 93 (92–93) | 4 (3–4) | 0.186 (0.157–0.197) | 81 (73–86) |
| Sonnet 5.5 | high | 3/3 | 112 (106–121) | 65 (63–65) | 2.2 (1.9–3.0) | 19 (18–20) | 66 (65–77) | 32 (24–51) | 417 (410–481) | 93 (93–93) | 5 (4–5) | 0.203 (0.188–0.217) | 96 (90–99) |

Seconds per question of each kind, from the previous final answer to its own, and the corrections of all three runs
(see `check.py` in [bench/README.md](../bench/README.md#form)). Question 1 is a checkbox, so the checkbox column
includes the time from the agent's start to its first answer.

| Agent | Effort | Checkbox s | Slider s | Text s | Dropdown s | Corrections |
| --- | --- | --- | --- | --- | --- | --- |
| gpt-6.1-sol | low | 5.0 (4.2–5.3) | 3.7 (2.5–4.2) | 1.2 (1.2–2.1) | 6.7 (4.0–10.2) | 0 |
| gpt-6.1-sol | medium | 3.6 (2.8–4.1) | 3.6 (2.6–4.8) | 3.5 (1.2–6.0) | 7.1 (5.6–8.2) | 1 |
| gpt-6.1-sol | high | 3.8 (3.4–6.7) | 3.2 (2.7–3.7) | 4.9 (2.5–7.3) | 5.5 (5.1–6.7) | 4 |
| gpt-6-astra | low | 6.4 (3.1–7.1) | 2.6 (2.6–2.7) | 4.8 (2.2–5.7) | 7.4 (5.7–7.6) | 1 |
| gpt-6-astra | medium | 5.5 (4.4–5.5) | 2.8 (2.1–4.0) | 5.7 (3.8–7.6) | 8.3 (8.0–9.8) | 3 |
| gpt-6-astra | high | 4.6 (3.4–8.4) | 2.6 (2.5–3.3) | 6.8 (2.4–7.7) | 7.5 (7.0–9.2) | 3 |
| Opus 5.5 | low | 5.1 (2.9–5.4) | 3.4 (2.6–4.6) | 2.2 (2.0–3.1) | 4.2 (4.2–5.5) | 0 |
| Opus 5.5 | medium | 3.1 (2.4–4.0) | 4.6 (3.4–5.3) | 2.1 (2.0–4.1) | 5.1 (4.5–5.9) | 2 |
| Opus 5.5 | high | 3.5 (3.3–3.7) | 3.4 (3.2–4.7) | 2.8 (2.1–4.0) | 6.0 (5.4–6.7) | 0 |
| Sonnet 5.5 | low | 3.2 (2.9–3.7) | 3.1 (3.1–3.2) | 2.5 (1.9–2.7) | 5.8 (5.6–6.3) | 3 |
| Sonnet 5.5 | medium | 3.9 (2.7–5.3) | 2.6 (2.5–3.0) | 1.9 (1.2–2.7) | 4.2 (3.8–4.7) | 0 |
| Sonnet 5.5 | high | 3.7 (2.4–3.8) | 4.6 (4.1–4.8) | 2.3 (1.9–3.1) | 4.8 (4.7–5.9) | 3 |

What we saw:

- The whole questionnaire took 93 to 152 s at the medians, and 51 to 65 s of it were in hyprmasq's tools for every
  agent. The differences between agents are the models' time outside the tools: Turn s ranges from 2.0 s (Sonnet,
  medium) to 5.5 s (gpt-6-astra, high). Higher effort was slower or no faster for every model, except Sonnet,
  whose `medium` runs were faster than its `low` ones; every model was slowest at `high`.
- Dropdowns were the slowest kind for every agent, 5.8 s a question at the median over all runs, against 3.8 s for
  checkboxes, 3.2 s for sliders and 2.6 s for text. Agents opened a dropdown in one call and chose in the next, after
  reading the popover in the result.
- Of the 216 slider answers, 203 were set with the keyboard alone: Home or End, then arrow keys, with the step from
  the caption. 13 started with a click on the slider. Key presses made the largest part of the time in `act`: of
  its 2008 s over all runs, `analyze.py` attributes 852 s to the sender's pacing of input, 387 s to waits for the
  window to settle, 279 s to the watch for a second redraw and 490 s to the rest.
- 12 of the 36 runs had one refused `act`: a key `repeat` above its limit of 100, such as 147 presses for a slider,
  which the server refused before sending anything. The agents split the presses and went on.
- In the debriefs, 31 of 36 agents named a way to set a slider or choose in a dropdown by value as the change that
  would have made them fastest.
