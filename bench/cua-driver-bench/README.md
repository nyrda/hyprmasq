# CuaDriverBench on Omarchy 4.5

This directory holds the `hyprmasq` computer driver, the `cua-driver 0.62.1`
upgrade recipe and the per-trial results reported in
[docs/comparison-cua-driver-bench.md](../../docs/comparison-cua-driver-bench.md)
using Scale AI's
[agentenv-cua-driver-bench](https://github.com/earakely-scale/agentenv-cua-driver-bench)
harness (`f6bf21798c76f5d0f01f22c586d17e1c2386f29b`).

## Layout

- `driver/` is the AgentEnv `computer_driver` package for unmodified
  `hyprmasq 0.5.1` on Omarchy 4.5.0 (Hyprland `0.56.2-1`):
  - `env.toml`, `driver.py`, `install-hyprland.sh`, `arch-day.conf`,
    `mcp.json` and `requirements-omarchy.txt` belong in `envs/hyprmasq/`
    inside a clone of `agentenv-cua-driver-bench`.
  - `desktop-env.toml` belongs in
    `envs/desktop-benchlab-omarchy-hyprmasq/env.toml`. It sets
    `tools = "driver"` so agents see only `hyprmasq-mcp`'s six tools
    (`windows`, `screenshot`, `act`, `launch`, `release`, `close`).
- `cua-driver-0.62.1/` holds the upgrade overlay (`env.toml`,
  `install-hyprland.sh`, `desktop-env.toml`) that installs `cua-driver-rs`
  `0.62.1` and builds `cua-hyprland-plugin 0.62.1` against Omarchy's pinned
  `hyprland 0.56.2-1` headers on top of upstream's `cua-driver` adapter files.
- `data/` holds the per-trial JSON records for all 280 trials (70 per driver
  and model across Claude Sonnet 5.5 and GPT-6.2 Sol for `hyprmasq 0.5.1` and
  `cua-driver 0.62.1`), graded with upstream `grade.py`, `gui_only/verify.py`
  and trycua's A22 sentinel rules.
- `summarize.py` recomputes every suite, per-task and cross-OS table in
  [docs/comparison-cua-driver-bench.md](../../docs/comparison-cua-driver-bench.md)
  from `data/` and Scale AI's pinned Hugging Face parquet revision
  (`ca3d569ef93c1987f75bf00663e3114c0ed601ac`).
- `render.py` generates all eight light and dark SVG figures in
  [docs/comparison-cua-driver-bench/](../../docs/comparison-cua-driver-bench)
  (`--check` verifies they are up to date, and CI runs it on every PR).

## Reproducing the runs

1. Clone `https://github.com/earakely-scale/agentenv-cua-driver-bench` at
   commit `f6bf21798c76f5d0f01f22c586d17e1c2386f29b`, start a local OCI
   registry on `localhost:5000`, and install
   `agentenv-framework==0.9.1649` (with `agentenv-framework-protocol==0.1.664`).
2. Fetch trycua's pinned task pack (`69b459b33ca0`) inside the clone:

   ```sh
   python3 tools/taskpack.py
   ```

3. Install the `hyprmasq 0.5.1` driver and desktop environment into the clone:

   ```sh
   mkdir -p envs/hyprmasq envs/desktop-benchlab-omarchy-hyprmasq
   cp driver/env.toml driver/driver.py driver/install-hyprland.sh \
      driver/arch-day.conf driver/mcp.json driver/requirements-omarchy.txt \
      envs/hyprmasq/
   cp driver/desktop-env.toml envs/desktop-benchlab-omarchy-hyprmasq/env.toml
   ```

   `install-hyprland.sh` compiles `hyprmasq.so` from the verified `v0.5.1`
   source tarball directly against Omarchy's pinned `hyprland 0.56.2-1`
   headers and installs the prebuilt `v0.5.1` tools (`hyprmasq-input`,
   `hyprmasq-launch`, `hyprmasq-mcp`, `hyprmasq-reaper`), matching how
   upstream builds `cua-hyprland-plugin.so` inside the same VM image.
   `envs/hyprmasq/env.toml` waives one Gate 3 probe check (`P6`, CJK Unicode
   via `wtype` under `fcitx5` on the active probe window), which only tests
   the `computer_type` primitive and is disabled for agents under
   `tools = "driver"`.

4. To reproduce the `cua-driver 0.62.1` baseline, copy upstream's shipped
   `cua-driver` adapter files (`driver.py`, `hyprland.py`, `arch-day.conf`,
   `requirements-omarchy.txt`, `mcp.json`, `mcp-hyprland.json`) into
   `envs/cua-driver-latest/`, then apply the upgrade overlay:

   ```sh
   mkdir -p envs/cua-driver-latest envs/desktop-benchlab-omarchy-cua-latest
   cp cua-driver-0.62.1/env.toml cua-driver-0.62.1/install-hyprland.sh \
      envs/cua-driver-latest/
   cp cua-driver-0.62.1/desktop-env.toml \
      envs/desktop-benchlab-omarchy-cua-latest/env.toml
   ```

5. The first `agent-env run` against each desktop environment composes its
   KVM image and runs the Gate 3 conformance probe automatically. Verify the
   probe and background sentinel on each environment before running trials:

   ```sh
   agent-env run . --task probe --set envs/desktop.driver=hyprmasq
   agent-env run . --eval omarchy-sentinel
   ```

   (Point a copy of `tasks/omarchy-sentinel-background.json` at
   `desktop-benchlab-omarchy-hyprmasq` and
   `desktop-benchlab-omarchy-cua-latest` to verify both sentinel guards.)

6. Run the 60 canonical Omarchy trials (`tasks/omarchy-*.json` for Claude
   Sonnet 5.5, or equivalent Codex task JSONs with `builtin_tools:
   {"disable": "*"}` and `medium` reasoning effort for GPT-6.2 Sol) against
   each environment:
   - **Claude Sonnet 5.5** (`Claude Code 2.1.322`, default effort,
     `builtin_tools: {"only": ["Skill", "Read"]}`, AgentEnv default
     `ENABLE_TOOL_SEARCH=false`): point the task JSONs at
     `desktop-benchlab-omarchy-hyprmasq`, and for Cua Driver 0.62.1 point them
     at `desktop-benchlab-omarchy-cua-latest` with
     `--set envs/desktop-benchlab-omarchy-cua-latest.tools=driver`.
   - **GPT-6.2 Sol** (`Codex CLI 0.171.0`, `medium` effort): point the Codex
     task JSONs at `desktop-benchlab-omarchy-hyprmasq` (`tools = "driver"`)
     and `desktop-benchlab-omarchy-cua-latest` (`tools` unset, matching stock
     `desktop-benchlab-omarchy/env.toml`).
   - **Extended `MB-10` and `MB-11` background trials (10 per driver and
     model):** clone the five `mb-10-r1..r5-foreground` and five
     `mb-11-r1..r5-foreground` task JSONs with `"condition": "background"` and
     the same sentinel arm/disarm steps and background brief used by
     `omarchy-mb-09-r*-background.json`.

## Verifying the tables, figures and HF board snapshot

```sh
python3 bench/cua-driver-bench/summarize.py
python3 bench/cua-driver-bench/render.py --check
```

The five external rows in `summarize.py` and `render.py` are filtered from
`https://huggingface.co/datasets/earakely-scale/CuaDriverBench-AgentEnv/resolve/ca3d569ef93c1987f75bf00663e3114c0ed601ac/results/trials.parquet`
with `on_board == True`, `model == "anthropic/claude-sonnet-5-5"`, and the 60
shared BenchLab trials (`mb-09`–`mb-11` and `ir-01`–`ir-04` foreground, plus
`mb-09` and `ir-01`–`ir-04` background), taking medians of `agent_window_s`,
`tool_calls` and `cost_usd` and mean of `cost_usd`.
