# CuaDriverBench on Omarchy 4.5

This directory holds the `hyprmasq` computer driver for Scale AI's
[agentenv-cua-driver-bench](https://huggingface.co/spaces/earakely-scale/CuaDriverBench-AgentEnv)
harness and the per-trial results reported in
[docs/comparison-cua-driver-bench.md](../../docs/comparison-cua-driver-bench.md).

## Layout

- `driver/` is the AgentEnv `computer_driver` package for `hyprmasq 0.5.1` on
  Omarchy 4.5.0 (Hyprland `0.56.2-1`):
  - `env.toml`, `driver.py`, `install-hyprland.sh`, `arch-day.conf`,
    `mcp.json` and `requirements-omarchy.txt` belong in `envs/hyprmasq/`
    inside a clone of `agentenv-cua-driver-bench`.
  - `desktop-env.toml` belongs in
    `envs/desktop-benchlab-omarchy-hyprmasq/env.toml`. It sets
    `tools = "driver"` so agents see only `hyprmasq-mcp`'s six tools
    (`windows`, `screenshot`, `act`, `launch`, `release`, `close`).
- `data/` holds the per-trial JSON records for all 140 trials (70 for
  `hyprmasq 0.5.1` and 70 for `cua-driver 0.62.1`), graded with upstream
  `grade.py` and trycua's A22 sentinel rules.
- `summarize.py` recomputes the suite and per-task tables in
  [docs/comparison-cua-driver-bench.md](../../docs/comparison-cua-driver-bench.md)
  directly from `data/`.

## Reproducing the hyprmasq run

1. Clone `https://huggingface.co/datasets/earakely-scale/agentenv-cua-driver-bench`
   and install `agentenv-framework==0.9.1649` (with `agentenv-framework-protocol==0.1.664`).
2. Fetch trycua's taskpack inside the clone (`python3 scripts/taskpack.py fetch`).
3. Copy the driver and desktop environment definitions into the clone:

   ```sh
   mkdir -p envs/hyprmasq envs/desktop-benchlab-omarchy-hyprmasq
   cp driver/env.toml driver/driver.py driver/install-hyprland.sh \
      driver/arch-day.conf driver/mcp.json driver/requirements-omarchy.txt \
      envs/hyprmasq/
   cp driver/desktop-env.toml envs/desktop-benchlab-omarchy-hyprmasq/env.toml
   ```

4. Compose the Omarchy VM image with the `hyprmasq` driver and run the Gate 3
   conformance check:

   ```sh
   agent-env compose . --env desktop-benchlab-omarchy-hyprmasq
   agent-env verify . --env desktop-benchlab-omarchy-hyprmasq
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

5. Point the Omarchy `codex` task JSONs (`MB-09`, `MB-10`, `MB-11`, `IR-01`
   through `IR-04`, seeds `r1` through `r5`, both `foreground` and
   `background`) at `desktop-benchlab-omarchy-hyprmasq` and run them with
   `agent-env run . --task <task-name> --model gpt-6.2-sol`.

## Verifying the tables

```sh
python3 bench/cua-driver-bench/summarize.py
```
