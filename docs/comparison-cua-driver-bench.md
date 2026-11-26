# CuaDriverBench on Omarchy

hyprmasq 0.5.1 beside [Cua Driver](https://github.com/trycua/cua) 0.62.1 inside
Scale AI's [CuaDriverBench-AgentEnv](https://huggingface.co/spaces/earakely-scale/CuaDriverBench-AgentEnv)
harness on Omarchy 4.5 (Hyprland `0.56.2-1`, 1920x1080, KVM). Both drivers ran
the same seven Linux BenchLab tasks (`MB-09` through `MB-11` and `IR-01`
through `IR-04`, five seeds each) across two frontier models from different
providers: **Claude Sonnet 5.5** (Claude Code `2.1.322`, default effort,
matching Scale AI's headline Omarchy setup with `tools = "driver"` on both
drivers) and **GPT-6.2 Sol** (Codex CLI `0.171.0`, medium effort). Every trial
was graded by trycua's own evaluators, GUI-only checks and A22
user-undisturbed sentinel criteria.

Alongside the standard 60-trial Omarchy suite (35 foreground trials plus 25
background trials), both drivers also ran `MB-10` (hover overlay) and `MB-11`
(tooltip query) in the background, for 70 trials per driver and model (**280
trials total**). Upstream omits `MB-10` and `MB-11` from background mode
because a real hover and native tooltip normally require the shared pointer,
whereas hyprmasq gives each window its own virtual pointer seat and composites
popups offscreen.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison-cua-driver-bench/scorecard-dark.svg">
  <img alt="CuaDriverBench on Omarchy 4.5 across 280 trials (Claude Sonnet 5.5 and GPT-6.2 Sol): pass rate with 95 % Wilson interval on all 70 trials, background pass bar, and wall time, tool calls and list-price cost as multiples of hyprmasq 0.5.1." src="comparison-cua-driver-bench/scorecard.svg">
</picture>

- **Runs passed (280 trials):** hyprmasq passed **140/140 (100.0%)** across
  both models (**70/70** Claude Sonnet 5.5 and **70/70** GPT-6.2 Sol), against
  **97/140 (69.3%)** for Cua Driver 0.62.1 (**47/70** Sonnet 5.5 and **50/70**
  GPT-6.2 Sol). On the standard 60-trial Omarchy suite, hyprmasq passed
  **60/60** on both models against **45/60 (75.0%)** (Sonnet 5.5) and
  **46/60 (76.7%)** (GPT-6.2 Sol) for Cua Driver 0.62.1.
- **Background isolation (A22):** hyprmasq passed **70/70 (100.0%)** of all
  background trials with **70/70** undisturbed across all eight A22 criteria
  and zero GUI-only violations, including **20/20** on `MB-10` and `MB-11`
  background. Cua Driver 0.62.1 passed **39/70 (55.7%)** in the background
  (**18/35** Sonnet 5.5, **21/35** Sol), failed `MB-09` (`0/10`) and `MB-11`
  (`0/10`) on every background run of both models, and disturbed the user in
  **4/35** Sonnet 5.5 background trials (`MB-11` r2, r3, r4 and `IR-03` r3).
- **Speed, calls and cost (Claude Sonnet 5.5, `tools = "driver"` on both):**
  across all 70 trials, hyprmasq finished in **16.4 s** median wall time
  (**18.4 s** on the standard 60 suite, prompt start to finish including all
  tool calls), **5.5** tool calls and **$0.047** median list-price cost
  (**$3.53** total for 70 trials). Cua Driver 0.62.1 took **1.87×** the wall
  time (`30.7 s`), **3.27×** the tool calls (`18.0`), **10.55×** the input
  tokens (`968k` vs `92k`) and **5.52×** the median cost (`$0.257`, **$21.93**
  total = **6.21×**). Paired across the 9 common passed cells (passed runs
  only), Cua Driver's geometric mean was **1.52×** wall time, **2.30×** calls,
  **7.88×** input tokens and **4.56×** cost.
- **Speed, calls and cost (GPT-6.2 Sol):** across all 70 trials, hyprmasq
  finished in **36.5 s** median wall time (**38.5 s** standard 60), **8.0**
  tool calls and **$0.067** median cost (**$4.83** total). Cua Driver 0.62.1
  took **1.75×** the wall time (`63.9 s`), **2.19×** the calls (`17.5`),
  **3.13×** the input tokens (`577k` vs `184k`) and **2.38×** the median cost
  (`$0.158`, **$12.31** total = **2.55×**). Paired across the 10 common passed
  cells, Cua Driver's geometric mean was **1.82×** wall time, **2.08×** calls,
  **3.12×** input tokens and **2.52×** cost.

The hyprmasq driver package, per-trial JSON records for all 280 trials, summary
script and figure renderer live in
[bench/cua-driver-bench](../bench/cua-driver-bench).

## Wall time, tool calls, tokens and cost

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison-cua-driver-bench/usage-dark.svg">
  <img alt="Median wall time, tool calls, input tokens and list-price cost per trial across all 70 trials for hyprmasq 0.5.1 and Cua Driver 0.62.1 with Claude Sonnet 5.5 and GPT-6.2 Sol." src="comparison-cua-driver-bench/usage.svg">
</picture>

### Claude Sonnet 5.5 (`tools = "driver"` on both drivers)

| Driver | Suite | Passed | User undisturbed (A22) | Median wall s | Median calls | Median input | Median uncached | Median output | Median cost | Mean cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq 0.5.1 | Foreground (35) | 35/35 (100.0%) | - | 14.4 | 5.0 | 91k | 9k | 728 | $0.045 | $0.052 |
| hyprmasq 0.5.1 | Standard background (25) | 25/25 (100.0%) | 25/25 | 20.5 | 6.0 | 110k | 10k | 879 | $0.058 | $0.057 |
| hyprmasq 0.5.1 | Standard Omarchy suite (60) | 60/60 (100.0%) | 25/25 | 18.4 | 6.0 | 108k | 9k | 809 | $0.050 | $0.054 |
| hyprmasq 0.5.1 | Extended background MB-10/11 (10) | 10/10 (100.0%) | 10/10 | 12.3 | 3.5 | 63k | 6k | 440 | $0.028 | $0.029 |
| hyprmasq 0.5.1 | All trials (70) | 70/70 (100.0%) | 35/35 | 16.4 | 5.5 | 92k | 9k | 730 | $0.047 | $0.050 |
| Cua Driver 0.62.1 | Foreground (35) | 29/35 (82.9%) | - | 30.5 | 16.0 | 921k | 17k | 2,148 | $0.255 | $0.308 |
| Cua Driver 0.62.1 | Standard background (25) | 16/25 (64.0%) | 24/25 | 32.7 | 18.0 | 1,095k | 17k | 3,032 | $0.283 | $0.329 |
| Cua Driver 0.62.1 | Standard Omarchy suite (60) | 45/60 (75.0%) | 24/25 | 30.7 | 18.0 | 997k | 17k | 2,389 | $0.258 | $0.317 |
| Cua Driver 0.62.1 | Extended background MB-10/11 (10) | 2/10 (20.0%) | 7/10 | 26.7 | 12.5 | 686k | 13k | 2,268 | $0.187 | $0.291 |
| Cua Driver 0.62.1 | All trials (70) | 47/70 (67.1%) | 31/35 | 30.7 | 18.0 | 968k | 17k | 2,389 | $0.257 | $0.313 |

### GPT-6.2 Sol

| Driver | Suite | Passed | User undisturbed (A22) | Median wall s | Median calls | Median input | Median uncached | Median output | Median cost | Mean cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq 0.5.1 | Foreground (35) | 35/35 (100.0%) | - | 36.5 | 8.0 | 178k | 20k | 501 | $0.066 | $0.065 |
| hyprmasq 0.5.1 | Standard background (25) | 25/25 (100.0%) | 25/25 | 40.5 | 10.0 | 219k | 23k | 561 | $0.071 | $0.078 |
| hyprmasq 0.5.1 | Standard Omarchy suite (60) | 60/60 (100.0%) | 25/25 | 38.5 | 9.0 | 206k | 22k | 527 | $0.069 | $0.071 |
| hyprmasq 0.5.1 | Extended background MB-10/11 (10) | 10/10 (100.0%) | 10/10 | 25.4 | 5.0 | 128k | 22k | 407 | $0.057 | $0.059 |
| hyprmasq 0.5.1 | All trials (70) | 70/70 (100.0%) | 35/35 | 36.5 | 8.0 | 184k | 22k | 504 | $0.067 | $0.069 |
| Cua Driver 0.62.1 | Foreground (35) | 29/35 (82.9%) | - | 63.0 | 16.0 | 557k | 47k | 1,310 | $0.158 | $0.171 |
| Cua Driver 0.62.1 | Standard background (25) | 17/25 (68.0%) | 25/25 | 65.2 | 19.0 | 634k | 51k | 1,497 | $0.177 | $0.194 |
| Cua Driver 0.62.1 | Standard Omarchy suite (60) | 46/60 (76.7%) | 25/25 | 63.9 | 17.5 | 590k | 48k | 1,386 | $0.159 | $0.181 |
| Cua Driver 0.62.1 | Extended background MB-10/11 (10) | 4/10 (40.0%) | 10/10 | 62.2 | 27.0 | 378k | 40k | 1,396 | $0.131 | $0.146 |
| Cua Driver 0.62.1 | All trials (70) | 50/70 (71.4%) | 35/35 | 63.9 | 17.5 | 577k | 44k | 1,386 | $0.158 | $0.176 |

## Task by task

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison-cua-driver-bench/tasks-dark.svg">
  <img alt="Every trial of CuaDriverBench on Omarchy 4.5 across Claude Sonnet 5.5 and GPT-6.2 Sol, task by task: ten squares per driver (five Sonnet, five Sol) coloured by outcome, beside Claude Sonnet 5.5 median wall time on a log scale." src="comparison-cua-driver-bench/tasks.svg">
</picture>

Each cell shows passed trials out of 5, followed by median wall time (prompt
start to finish, including all tool calls), median tool calls and median
list-price cost across all 5 seeds (`r1` through `r5`).

### Claude Sonnet 5.5

| Condition | Task | hyprmasq 0.5.1 | Cua Driver 0.62.1 |
| --- | --- | --- | --- |
| foreground | `MB-09` List drag and drop | 5/5 (14.4 s, 5 calls, $0.038) | 2/5 (52.9 s, 35 calls, $0.434) |
| foreground | `MB-10` Hover overlay | 5/5 (10.6 s, 4 calls, $0.033) | 5/5 (12.6 s, 6 calls, $0.096) |
| foreground | `MB-11` Native tooltip | 5/5 (12.3 s, 4 calls, $0.028) | 4/5 (30.5 s, 15 calls, $0.253) |
| foreground | `IR-01` Permission dialog | 5/5 (22.4 s, 8 calls, $0.063) | 3/5 (36.5 s, 24 calls, $0.317) |
| foreground | `IR-02` Update banner | 5/5 (14.3 s, 5 calls, $0.045) | 5/5 (20.6 s, 9 calls, $0.160) |
| foreground | `IR-03` Unsaved-changes modal | 5/5 (26.5 s, 10 calls, $0.082) | 5/5 (84.3 s, 35 calls, $0.626) |
| foreground | `IR-04` Cookie consent sheet | 5/5 (20.4 s, 9 calls, $0.064) | 5/5 (26.5 s, 16 calls, $0.227) |
| background | `MB-09` List drag and drop | 5/5 (12.6 s, 4 calls, $0.037) | 0/5 (43.3 s, 22 calls, $0.298) |
| background | `IR-01` Permission dialog | 5/5 (22.4 s, 8 calls, $0.058) | 2/5 (38.7 s, 26 calls, $0.443) |
| background | `IR-02` Update banner | 5/5 (16.4 s, 6 calls, $0.048) | 5/5 (16.6 s, 10 calls, $0.147) |
| background | `IR-03` Unsaved-changes modal | 5/5 (26.5 s, 9 calls, $0.076) | 4/5 (48.7 s, 32 calls, $0.509) |
| background | `IR-04` Cookie consent sheet | 5/5 (24.4 s, 9 calls, $0.064) | 5/5 (24.5 s, 16 calls, $0.229) |
| background | `MB-10` Hover overlay (ext) | 5/5 (12.3 s, 4 calls, $0.033) | 2/5 (14.4 s, 7 calls, $0.118) |
| background | `MB-11` Native tooltip (ext) | 5/5 (12.3 s, 3 calls, $0.028) | 0/5 (61.0 s, 29 calls, $0.504) |

### GPT-6.2 Sol

| Condition | Task | hyprmasq 0.5.1 | Cua Driver 0.62.1 |
| --- | --- | --- | --- |
| foreground | `MB-09` List drag and drop | 5/5 (24.4 s, 4 calls, $0.045) | 5/5 (66.8 s, 15 calls, $0.134) |
| foreground | `MB-10` Hover overlay | 5/5 (18.7 s, 3 calls, $0.048) | 5/5 (34.5 s, 5 calls, $0.091) |
| foreground | `MB-11` Native tooltip | 5/5 (22.4 s, 4 calls, $0.055) | 5/5 (44.6 s, 9 calls, $0.160) |
| foreground | `IR-01` Permission dialog | 5/5 (50.6 s, 12 calls, $0.070) | 5/5 (80.8 s, 20 calls, $0.172) |
| foreground | `IR-02` Update banner | 5/5 (36.5 s, 8 calls, $0.069) | 3/5 (127.7 s, 34 calls, $0.294) |
| foreground | `IR-03` Unsaved-changes modal | 5/5 (46.5 s, 11 calls, $0.071) | 5/5 (62.7 s, 19 calls, $0.134) |
| foreground | `IR-04` Cookie consent sheet | 5/5 (42.6 s, 10 calls, $0.072) | 1/5 (63.0 s, 16 calls, $0.138) |
| background | `MB-09` List drag and drop | 5/5 (30.4 s, 5 calls, $0.054) | 0/5 (137.5 s, 24 calls, $0.309) |
| background | `IR-01` Permission dialog | 5/5 (44.6 s, 10 calls, $0.070) | 5/5 (70.9 s, 19 calls, $0.225) |
| background | `IR-02` Update banner | 5/5 (34.5 s, 9 calls, $0.077) | 5/5 (60.6 s, 13 calls, $0.181) |
| background | `IR-03` Unsaved-changes modal | 5/5 (50.6 s, 12 calls, $0.071) | 5/5 (65.2 s, 19 calls, $0.152) |
| background | `IR-04` Cookie consent sheet | 5/5 (40.9 s, 10 calls, $0.075) | 2/5 (52.6 s, 13 calls, $0.127) |
| background | `MB-10` Hover overlay (ext) | 5/5 (22.4 s, 3 calls, $0.060) | 4/5 (40.5 s, 7 calls, $0.106) |
| background | `MB-11` Native tooltip (ext) | 5/5 (28.4 s, 5 calls, $0.052) | 0/5 (93.4 s, 45 calls, $0.176) |

## Why Cua Driver 0.62.1 failed 43 trials (23 Sonnet 5.5, 20 GPT-6.2 Sol)

Every failure is recorded with its final BenchLab state, failed grader checks,
A22 criteria and final assistant message in
[bench/cua-driver-bench/data](../bench/cua-driver-bench/data).

- **`MB-09` background (0/10 across both models, driver limitation):** Every
  background trial on both Sonnet 5.5 (`0/5`) and GPT-6.2 Sol (`0/5`) started
  GTK3 Wayland drags (`drag_begins: 1` to `9`) and dropped **zero** rows
  (`move_count: 0`). Both models kept the user undisturbed and reported
  `BLOCKED` on all 10 trials. Scale AI's Omarchy notes for Cua Driver 0.47.4
  reported the same defect, and it remains unresolved in `0.62.1`. hyprmasq
  runs Wayland `wl_data_device` drag-and-drop privately inside unfocused
  windows and passed `10/10` (`12.6 s` Sonnet, `30.4 s` Sol).
- **`MB-11` background (0/10 across both models, driver limitation):** GTK3
  draws tooltips on separate popup surfaces that Cua Driver's Hyprland
  background capture does not composite, and BenchLab's dynamic `query-tooltip`
  text is absent from the static AT-SPI tree. With GPT-6.2 Sol, all five trials
  hit the 45-turn cap with zero submits. With Claude Sonnet 5.5, `r1` and `r5`
  reported `BLOCKED`, while on `r2`, `r3` and `r4` Sonnet fell back to
  foreground pointer input to trigger the tooltip, failing A22 on `key_focus`,
  `typing`, `pointer` and `activation`. hyprmasq composites popup surfaces into
  its offscreen window screenshot and holds a per-window pointer lease, passing
  `10/10` (`12.3 s` Sonnet, `28.4 s` Sol) with zero disturbances.
- **`MB-09` foreground on Sonnet 5.5 with `tools = "driver"` (2/5, driver
  bug):** On `r2`, `r3` and `r5`, Cua Driver 0.62.1's own drag posted without
  moving any row (`drag_begins: 2`, `move_count: 0`) and left BenchLab stuck in
  a drag state, causing every subsequent click, key press and drag to fail with
  `primary_target_busy` or `focused_window_mismatch`. In the GPT-6.2 Sol run
  where `tools` was left unset, Sol bypassed this by calling AgentEnv's
  `computer_drag` on all five `MB-09` foreground trials (`5/5`).
- **`IR-01` on Sonnet 5.5 (3/5 fg, 2/5 bg — 5 failures):** On `r2` and `r4`
  foreground and `r2`, `r3` and `r5` background, BenchLab recorded
  `prompt_answers: ["deny", "deny"]` while the grader checks for a single deny.
  With `hyprmasq`, Sonnet passed `10/10`.
- **`MB-10` background (2/5 Sonnet, 4/5 Sol — 4 failures):** On Sonnet `r3`,
  `r4` and `r5`, Cua Driver reported the first accessibility click on the
  target button as unverifiable with no visible change, so Sonnet clicked a
  second time and failed `single_click` (`2 overlay click(s)`). On Sol `r5`,
  the agent clicked the hidden `Share` button directly via AT-SPI without
  hovering (`hover_enter_count: 0`, `last_click_overlay_visible: false`).
- **`IR-04` on GPT-6.2 Sol (1/5 fg, 2/5 bg — 7 failures):** All seven failures
  hit the exact same sequence (`submits_blocked: 1`, cookie consent rejected,
  `submit_count: 0`, agent replied `DONE`): Sol clicked the `Sign up` AT-SPI
  token from its pre-consent snapshot after the asynchronous consent sheet had
  already opened, then rejected cookies and stopped rather than pressing
  `Sign up` again. With Sonnet 5.5, Cua Driver passed `10/10` on `IR-04`.
- **Remaining 4 failures:** `IR-02` foreground on Sol (`r4` and `r5` hit 45
  turns with `apply_count: 5` and `8`), `IR-03` background on Sonnet (`r3` hit
  45 turns and failed A22 on `key_focus`, `typing`, `pointer` and
  `activation`), and `MB-11` foreground on Sonnet (`r1` batched typing and
  Submit into two submits).

## Relation to Scale AI's published board across OSes

On Scale AI's published Hugging Face dataset
(`earakely-scale/CuaDriverBench-AgentEnv`, filtering `results/trials.parquet`
to the exact same 60 BenchLab trials), Claude Sonnet 5.5 is the strongest
published `cua-driver` model on both Omarchy and macOS, and `macOS · Aside
Computer Use 1.26.1111` is the highest-scoring setup overall. Note that macOS,
Windows and Ubuntu Linux also run three multi-app tasks (`G02` through `G04`)
that have not been ported to Omarchy yet, and Scale AI's published runs ran on
EC2 hosts with earlier driver versions.

| Setup (Claude Sonnet 5.5, same 60 BenchLab trials) | FG (of 35) | BG (of 25) | Undisturbed | Median wall s | Median calls | Mean cost | Median cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **Omarchy · hyprmasq 0.5.1** *(this run)* | **35/35** | **25/25** | **25/25** | 18.4 s | **6.0** | **$0.054** | **$0.050** |
| macOS · Aside Computer Use `1.26.1111` *(HF board #1)* | **35/35** | **25/25** | **25/25** | **15.6 s** | **6.0** | $0.062 | $0.058 |
| macOS · Cua Driver `0.47.10` *(HF board)* | **35/35** | 17/25 | 22/25 | 20.0 s | 8.0 | $0.154 | $0.134 |
| Omarchy · Cua Driver `0.47.4` *(HF board)* | 34/35 | 18/25 | 22/25 | 30.2 s | 18.0 | $0.276 | $0.245 |
| **Omarchy · Cua Driver 0.62.1** *(this run)* | 29/35 | 16/25 | 24/25 | 30.7 s | 18.0 | $0.317 | $0.258 |
| Windows · Cua Driver `0.38.0` *(HF board)* | 33/35 | 11/25 | 11/25 | - | - | - | - |
| Ubuntu Linux · Cua Driver `0.38.0` *(HF board)* | **35/35** | 5/25 | 9/25 | 24.3 s | 14.0 | $0.259 | $0.243 |

On the shared 60 BenchLab trials with Claude Sonnet 5.5, hyprmasq 0.5.1 ties
`macOS · Aside` on pass rate (`60/60`), background isolation (`25/25`) and tool
calls (`6.0`), comes in cheaper per trial (`$0.054` mean / `$0.050` median vs
`$0.062` / `$0.058`), and also passes `10/10` on `MB-10` and `MB-11` in the
background (where Aside documents that it hovers only after taking the
foreground).

## Method

- **VM and compositor:** Scale AI's `agentenv-cua-driver-bench` bundle
  (trycua taskpack commit `69b459b33ca0`, `agentenv-framework==0.9.1649`),
  Omarchy 4.5.0 base image under KVM (`1920x1080`, Hyprland `0.56.2-1`), two
  concurrent VMs per sweep on the same Linux host.
- **Agents and models:**
  - **Claude Sonnet 5.5:** Claude Code `2.1.322`, `builtin_tools: {"only": ["Skill", "Read"]}`, default effort, `ENABLE_TOOL_SEARCH=false`, verbatim
    `tasks/omarchy-*.json` system prompt and briefs, 360 s timeout and 45-turn
    cap (matching Scale AI's `2026-11-11` Omarchy runs).
  - **GPT-6.2 Sol:** Codex CLI `0.171.0`, `builtin_tools: {"disable": "*"}`,
    `medium` reasoning effort, verbatim Linux system prompt and briefs, 360 s
    timeout and 45-turn cap.
- **hyprmasq 0.5.1 setup:** `hyprmasq.so` compiled from the verified `v0.5.1`
  source tarball against Omarchy's pinned `hyprland 0.56.2-1` headers (matching
  how the bundle compiles `cua-hyprland-plugin.so`), with prebuilt `v0.5.1`
  tools (`hyprmasq-input`, `hyprmasq-launch`, `hyprmasq-mcp`,
  `hyprmasq-reaper`), `virtual_cursor = false` and `place_new_windows = true`.
  The desktop environment sets `tools = "driver"`, so the agent sees only
  `hyprmasq-mcp`'s six tools (`windows`, `screenshot`, `act`, `launch`,
  `release`, `close`). Gate 3 conformance passed with one probe-only waiver
  (`P6`, CJK Unicode through `wtype` under `fcitx5` on the active probe
  window, testing `computer_type` which is disabled for agents under
  `tools = "driver"`), and `omarchy-hyprmasq-sentinel-background` passed.
- **Cua Driver 0.62.1 setup and `tools = "driver"` audit:** latest release
  `cua-driver-rs-v0.62.1` and its matching `cua-hyprland-plugin 0.62.1` built
  for Omarchy's `hyprland 0.56.2-1`, with its live 65-tool / 11-resource MCP
  manifest. Gate 3 conformance passed with zero waivers and
  `omarchy-cua0621-sentinel-background` passed.
  - On **Claude Sonnet 5.5**, Cua Driver 0.62.1 ran with
    `--set envs/desktop-benchlab-omarchy-cua-latest.tools=driver` (matching
    Scale AI's `2026-11-11` Omarchy runs and hyprmasq's exact `tools = "driver"`
    setting). Auditing all 70 Sonnet trajectories confirmed **0 `computer_*`
    calls**.
  - On **GPT-6.2 Sol**, Cua Driver 0.62.1 followed the bundle's stock
    `envs/desktop-benchlab-omarchy/env.toml` with `tools` unset. Auditing all
    70 Sol trajectories on disk showed **0 `computer_*` calls across all 35
    background trials** and **57 `computer_*` calls out of 1,337 total calls
    (4.3%) across 18 of 35 foreground trials**: `computer_screenshot` (32),
    `computer_drag` (15, used on all five `MB-09` foreground passes) and
    `computer_click` (10). Leaving `tools` unset did not harm Cua Driver's
    background results and actually helped its `MB-09` foreground score (5/5
    with `computer_drag` vs 2/5 under `tools = "driver"` on Sonnet 5.5).
- **Timing and cost:** wall time (`wall_seconds` in the raw JSON) measures the
  full `prompt_agent` step from prompt start to finish, including all model
  turns and every tool call (settle waits, captures, AT-SPI walks and input),
  excluding only AgentEnv's fixed VM boot, snapshot and grading overhead. Costs
  use provider API list prices matching `bench/report.py` and Scale AI's
  `export.py` (`$2.00 / $0.20 / $2.50 / $10.00` per million uncached input,
  cache read, cache write and output tokens for Claude Sonnet 5.5, and
  `$2.00 / $0.10 / $2.50 / $10.00` for GPT-6.2 Sol). Claude Code's own reported
  cost (which prices cache reads at `$0.10/M` and 1-hour cache writes at
  `$4.00/M`) is also kept in `cost_usd_reported` (mean `$0.053` vs `$0.226`
  across all 70 Sonnet trials, `4.3×`).
- **Grading and retries:** every trial was graded with upstream
  `artifacts/grader/grade.py` and `artifacts/gui_only/verify.py`. Two Cua
  Driver 0.62.1 Sonnet 5.5 trials (`MB-10` r2 and r3 foreground) failed before
  turn 1 with `OAuth session expired` (`0` tool calls, `$0.00`) and were
  re-run once after the host OAuth token refreshed (both passed on re-run). No
  other trial was retried, excluded or overridden.
- **Reproducibility:** [bench/cua-driver-bench/README.md](../bench/cua-driver-bench/README.md)
  documents how to install the `hyprmasq` driver into
  `agentenv-cua-driver-bench`, `python3 bench/cua-driver-bench/summarize.py`
  recomputes every table on this page from
  [bench/cua-driver-bench/data](../bench/cua-driver-bench/data), and
  `python3 bench/cua-driver-bench/render.py --check` verifies the light and
  dark figures.
