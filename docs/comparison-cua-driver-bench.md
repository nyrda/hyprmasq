# CuaDriverBench on Omarchy

hyprmasq 0.5.1 beside [Cua Driver](https://github.com/trycua/cua) 0.62.1 inside
Scale AI's [CuaDriverBench-AgentEnv](https://huggingface.co/spaces/earakely-scale/CuaDriverBench-AgentEnv)
harness on Omarchy 4.5 (Hyprland `0.56.2-1`, 1920x1080, KVM). Both drivers ran
the same seven Linux BenchLab tasks (`MB-09` through `MB-11` and `IR-01`
through `IR-04`, five seeds each) with GPT-6.2 Sol in Codex CLI `0.171.0` at
medium effort, graded by trycua's own evaluators, GUI-only checks and A22
user-undisturbed sentinel criteria.

Alongside the standard 60-trial Omarchy suite (35 foreground trials plus 25
background trials), both drivers also ran `MB-10` (hover overlay) and `MB-11`
(tooltip query) in the background, for 70 trials per driver (140 trials total).
Upstream omits `MB-10` and `MB-11` from background mode because a real hover
and native tooltip normally require the shared pointer, whereas hyprmasq gives
each window its own virtual pointer seat and composites popups offscreen.

- **Standard Omarchy suite (60 trials):** hyprmasq passed **60/60 (100.0%)**
  against **46/60 (76.7%)** for Cua Driver 0.62.1 (`35/35` vs `29/35`
  foreground, `25/25` vs `17/25` standard background).
- **Extended background (`MB-10` and `MB-11`, 10 trials):** hyprmasq passed
  **10/10 (100.0%)** against **4/10 (40.0%)** for Cua Driver 0.62.1 (`4/5` on
  `MB-10`, `0/5` on `MB-11`).
- **All 70 trials:** hyprmasq passed **70/70 (100.0%)** against **50/70
  (71.4%)** for Cua Driver 0.62.1. Every background trial of both drivers
  (`35/35` each) passed all eight A22 user-undisturbed criteria with zero
  GUI-only violations.
- **Speed and tokens:** across all 70 trials, Cua Driver 0.62.1 took **1.75x**
  the median agent time (`63.9 s` vs `36.5 s`), **2.19x** the tool calls
  (`17.5` vs `8.0`), **3.13x** the input tokens (`577k` vs `184k`), **1.99x**
  the uncached input tokens (`44k` vs `22k`) and **2.75x** the output tokens
  (`1,386` vs `504`). Paired across the 10 task-condition cells where both
  passed at least 3 of 5 runs (passed runs only), Cua Driver's per-cell
  geometric mean was **1.82x** the agent time, **2.08x** the tool calls,
  **3.12x** the input tokens and **2.19x** the uncached input tokens.

The hyprmasq driver package, per-trial JSON records and summary script live in
[bench/cua-driver-bench](../bench/cua-driver-bench).

## Summary

| Driver | Suite | Passed | User undisturbed (A22) | Median agent s | Median calls | Median input | Median uncached | Median output |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq 0.5.1 | Foreground (35) | 35/35 (100.0%) | - | 36.5 | 8.0 | 178k | 20k | 501 |
| hyprmasq 0.5.1 | Standard background (25) | 25/25 (100.0%) | 25/25 | 40.5 | 10.0 | 219k | 23k | 561 |
| hyprmasq 0.5.1 | Standard Omarchy suite (60) | 60/60 (100.0%) | 25/25 | 38.5 | 9.0 | 206k | 22k | 527 |
| hyprmasq 0.5.1 | Extended background MB-10/11 (10) | 10/10 (100.0%) | 10/10 | 25.4 | 5.0 | 128k | 22k | 407 |
| hyprmasq 0.5.1 | All trials (70) | 70/70 (100.0%) | 35/35 | 36.5 | 8.0 | 184k | 22k | 504 |
| Cua Driver 0.62.1 | Foreground (35) | 29/35 (82.9%) | - | 63.0 | 16.0 | 557k | 47k | 1,310 |
| Cua Driver 0.62.1 | Standard background (25) | 17/25 (68.0%) | 25/25 | 65.2 | 19.0 | 634k | 51k | 1,497 |
| Cua Driver 0.62.1 | Standard Omarchy suite (60) | 46/60 (76.7%) | 25/25 | 63.9 | 17.5 | 590k | 48k | 1,386 |
| Cua Driver 0.62.1 | Extended background MB-10/11 (10) | 4/10 (40.0%) | 10/10 | 62.2 | 27.0 | 378k | 40k | 1,396 |
| Cua Driver 0.62.1 | All trials (70) | 50/70 (71.4%) | 35/35 | 63.9 | 17.5 | 577k | 44k | 1,386 |

Across all 70 trials, hyprmasq consumed **12.89M** total input tokens
(**1.67M** uncached) and **35.9k** output tokens, while Cua Driver 0.62.1
consumed **42.92M** total input tokens (**3.55M** uncached) and **128.4k**
output tokens (on passed trials alone, Cua Driver's median remained `60.7 s`,
`17.0` calls, `577k` input, `43k` uncached).

## Task by task

Each cell shows passed trials out of 5, followed by median agent time, median
tool calls and median input tokens across all 5 seeds (`r1` through `r5`).

### Foreground

| Task | What it tests | hyprmasq 0.5.1 | Cua Driver 0.62.1 |
| --- | --- | --- | --- |
| `MB-09` | Reorder 5-row GTK3 list by drag and drop | 5/5 (24.4 s, 4 calls, 107k in) | 5/5 (66.8 s, 15 calls, 509k in) |
| `MB-10` | Hover toolbar zone to reveal and click an overlay button | 5/5 (18.7 s, 3 calls, 100k in) | 5/5 (34.5 s, 5 calls, 250k in) |
| `MB-11` | Hover an icon, read its native tooltip code and submit | 5/5 (22.4 s, 4 calls, 104k in) | 5/5 (44.6 s, 9 calls, 390k in) |
| `IR-01` | Fill form while answering an unexpected permission dialog | 5/5 (50.6 s, 12 calls, 270k in) | 5/5 (80.8 s, 20 calls, 695k in) |
| `IR-02` | Set quantity and priority while dismissing an update banner | 5/5 (36.5 s, 8 calls, 179k in) | 3/5 (127.7 s, 34 calls, 1,155k in) |
| `IR-03` | Navigate away, keep editing on unsaved-changes modal, submit | 5/5 (46.5 s, 11 calls, 255k in) | 5/5 (62.7 s, 19 calls, 651k in) |
| `IR-04` | Sign up while rejecting an asynchronous cookie consent sheet | 5/5 (42.6 s, 10 calls, 224k in) | 1/5 (63.0 s, 16 calls, 483k in) |

### Background (user typing in foreground sentinel)

`MB-09` and `IR-01` through `IR-04` are the standard 25-trial Omarchy
background suite. `MB-10` and `MB-11` are the 10 extended background trials.

| Task | Suite | hyprmasq 0.5.1 | Cua Driver 0.62.1 |
| --- | --- | --- | --- |
| `MB-09` | Standard | 5/5 (30.4 s, 5 calls, 145k in) | 0/5 (137.5 s, 24 calls, 1,005k in) |
| `IR-01` | Standard | 5/5 (44.6 s, 10 calls, 219k in) | 5/5 (70.9 s, 19 calls, 660k in) |
| `IR-02` | Standard | 5/5 (34.5 s, 9 calls, 217k in) | 5/5 (60.6 s, 13 calls, 487k in) |
| `IR-03` | Standard | 5/5 (50.6 s, 12 calls, 258k in) | 5/5 (65.2 s, 19 calls, 634k in) |
| `IR-04` | Standard | 5/5 (40.9 s, 10 calls, 197k in) | 2/5 (52.6 s, 13 calls, 451k in) |
| `MB-10` | Extended | 5/5 (22.4 s, 3 calls, 106k in) | 4/5 (40.5 s, 7 calls, 293k in) |
| `MB-11` | Extended | 5/5 (28.4 s, 5 calls, 139k in) | 0/5 (93.4 s, 45 calls, 490k in) |

## Why Cua Driver 0.62.1 failed 20 trials

Every failure is recorded with its final BenchLab state, failed grader checks
and final assistant message in
[bench/cua-driver-bench/data/cua-driver-0.62.1.json](../bench/cua-driver-bench/data/cua-driver-0.62.1.json).

- **`MB-09` background (0/5, driver limitation):** Cua Driver passed `5/5` on
  `MB-09` in the foreground, confirming GPT-6.2 Sol handles the drag-and-drop
  list cleanly when drags deliver. In the background, BenchLab logged 5 to 9
  `drag_begins` per trial and `0` drops (`move_count: 0`) on all five seeds.
  The agent kept the user window undisturbed and reported `BLOCKED` on every
  run. Scale AI's own Omarchy notes for Cua Driver 0.47.4 reported the same
  defect, and it remains unresolved in `0.62.1`. hyprmasq runs Wayland
  `wl_data_device` drag-and-drop privately inside unfocused windows and passed
  `5/5` in `30.4 s` median.
- **`MB-11` background (0/5, driver limitation):** Cua Driver passed `5/5` on
  `MB-11` in the foreground, but all five background trials hit the 45-turn
  cap with zero submits. BenchLab recorded pointer hover events (`seq: 10` to
  `18`), and on `r1` the agent explicitly noted that the background hover
  reached BenchLab without the tooltip appearing in the capture. GTK3 draws
  tooltips on separate popup surfaces that Cua Driver's Hyprland background
  capture does not composite, and BenchLab's dynamic `query-tooltip` text is
  absent from the static AT-SPI tree. hyprmasq composites popup surfaces into
  its offscreen window screenshot and holds a per-window pointer lease, passing
  `5/5` in `28.4 s` median.
- **`IR-04` foreground (1/5) and background (2/5), 7 failures:** All seven
  failures (`r2` through `r5` foreground and `r2`, `r4`, `r5` background) hit
  the exact same sequence in BenchLab's event log (`submits_blocked: 1`,
  `consent_answers: [{"answer": "reject", ...}]`, `submit_count: 0`, agent
  replied `DONE`). BenchLab raises the cookie consent sheet 250 ms after the
  first field edit. Through Cua Driver's AT-SPI workflow, the agent filled the
  form and clicked the `Sign up` token from its earlier snapshot while the
  consent sheet was already up. AT-SPI reported the click as delivered while
  BenchLab silently blocked it. The agent then saw the consent sheet on its
  next snapshot, rejected cookies and stopped rather than pressing `Sign up` a
  second time (the prompt asks to press `Sign up` once). hyprmasq returns
  changed screen crops after every `act` call, so the agent always saw the
  consent sheet before clicking `Sign up` and passed `10/10`.
- **`IR-02` foreground (3/5), 2 failures:** On `r4` and `r5`, the agent hit
  the 45-turn cap while repeatedly clicking `Apply` (`apply_count: 5` and `8`)
  and dismissing the update banner (`later` / `dismiss` 3 to 4 times) because
  it remained unsure whether `Apply` had registered.
- **`MB-10` background (4/5), 1 failure:** On `r1` through `r4`, Cua Driver
  hovered the hot zone (`hover_enter_count: 1`) and clicked the target button
  while the overlay was visible. On `r5`, the agent clicked the hidden `Share`
  button directly by its AT-SPI token in 4 tool calls (`hover_enter_count: 0`,
  `last_click_overlay_visible: false`), failing the grader's `hover_genuine`
  and `overlay_visible_at_click` checks.

## Relation to Scale AI's published Omarchy board

Scale AI's published Omarchy runs (`cua-driver-*-2026-11-11` on Hugging Face)
tested Cua Driver `0.47.4` with eight models (Claude 5.2/5.5 in Claude Code
`2.1.280` and GPT-6/6.1 in Codex CLI `0.147.0`) on the 60-trial standard suite.
Their best Omarchy model was Claude Sonnet 5.5 (`34/35` foreground, `18/25`
background, `0/5` on `MB-09` background), while GPT-6.1 Sol scored `17/35`
foreground (`0/15` precision because Codex CLI `0.147.0` lacked screenshot
images) and `16/25` background.

Those published numbers are not directly comparable to ours because both the
model (`gpt-6.2-sol`), Codex CLI (`0.171.0`, which handles MCP image responses
cleanly) and Cua Driver (`0.62.1`, released 2026-11-24) changed. Running both
drivers side by side on the exact same machine, VM image, model and CLI
isolates the driver difference.

## Method

- **VM and compositor:** Scale AI's `agentenv-cua-driver-bench` bundle
  (trycua taskpack commit `69b459b33ca0`, `agentenv-framework==0.9.1649`),
  Omarchy 4.5.0 base image under KVM (`1920x1080`, Hyprland `0.56.2-1`), two
  concurrent VMs per sweep on the same Linux host.
- **Agent and model:** Codex CLI `0.171.0` (`gpt-6.2-sol`, `medium` reasoning
  effort) with built-in tools disabled, trycua's verbatim Linux system prompt
  and per-seed task briefs, 360 s timeout and 45-turn cap.
- **hyprmasq 0.5.1 setup:** `hyprmasq.so` compiled from the verified `v0.5.1`
  source tarball against Omarchy's pinned `hyprland 0.56.2-1` headers (matching
  how the bundle compiles `cua-hyprland-plugin.so`), with prebuilt `v0.5.1`
  tools (`hyprmasq-input`, `hyprmasq-launch`, `hyprmasq-mcp`,
  `hyprmasq-reaper`), `virtual_cursor = false` and `place_new_windows = true`.
  The desktop environment sets `tools = "driver"`, so the agent receives only
  `hyprmasq-mcp`'s six tools (`windows`, `screenshot`, `act`, `launch`,
  `release`, `close`). Gate 3 conformance passed with one probe-only waiver
  (`P6`, CJK Unicode through `wtype` under `fcitx5` on the active probe
  window, testing `computer_type` which is disabled for agents under
  `tools = "driver"`), and `omarchy-hyprmasq-sentinel-background` passed.
- **Cua Driver 0.62.1 setup:** latest release `cua-driver-rs-v0.62.1` and its
  matching `cua-hyprland-plugin 0.62.1` built for Omarchy's `hyprland 0.56.2-1`,
  with its live 65-tool / 11-resource MCP manifest. Gate 3 conformance passed
  with zero waivers and `omarchy-cua0621-sentinel-background` passed. Following
  the bundle's stock `envs/desktop-benchlab-omarchy/env.toml`, `tools` was left
  unset (exposing AgentEnv's `computer_*` primitives alongside Cua Driver's
  tools), whereas Scale AI's `2026-11-11` runs passed
  `--set envs/<computer>.tools=driver`. Exposing the extra primitives did not
  cause any A22 disturbance (`35/35` undisturbed).
- **Grading:** every trial was graded with upstream `artifacts/grader/grade.py`
  and `artifacts/gui_only/verify.py` (trycua's per-probe evaluator, GUI-only
  guard and all eight A22 criteria on `sentinel.jsonl`). No trials were
  retried, excluded or overridden.
- **Reproducibility:** [bench/cua-driver-bench/README.md](../bench/cua-driver-bench/README.md)
  documents how to install the `hyprmasq` driver into
  `agentenv-cua-driver-bench`, and `python3 bench/cua-driver-bench/summarize.py`
  recomputes every table on this page from
  [bench/cua-driver-bench/data](../bench/cua-driver-bench/data).
