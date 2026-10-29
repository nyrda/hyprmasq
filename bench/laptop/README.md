# Runs on a real Hyprland machine

These scripts ran the [comparisons](../../docs/comparison.md) on a laptop with Omarchy and on a Mac, outside the
NixOS VMs of `bench/run.py`. They drive the user's live Hyprland session over SSH, so the machine must be idle,
unlocked and with its display on, and nobody may use it during a run.

- `run_form.py` runs the [form task](../tasks/form)'s questionnaire with this checkout's app and checker, the GTK 4
  app or, with `--toolkit qt`, its Qt port `form_qt.py`, run by `$QT_PYTHON`, a Python with PySide6. With
  `--no-dropdowns`, the questionnaire asks only checkboxes, sliders and text fields, as the task
  `form-no-dropdowns`.
- `run_inkscape.py` runs the Inkscape task: move the rectangle in [inkscape/drawing.svg](inkscape/drawing.svg)
  from x=120 to x=122 px and save. The grade reads the saved file with `inkscape --query-all`.
- `run_files.py` runs the file manager task: in a Nautilus window on a fresh folder, rename `report-draft.txt` to
  `report-final.txt` and move it into `Archive`. The grade compares every file's contents and place.
- `series.sh OUT "backend rep"...` runs several, `RUNNER=run_inkscape.py` for Inkscape and `EXTRA` for options
  such as `--harness claude --model claude-sonnet-5-5`.
- `analyze.py OUT` writes `OUT/comparison.json`, one entry a run, as [docs/comparison/data](../../docs/comparison/data)
  holds them. Copy it there under a dataset's name, add the name to the blocks of `docs/comparison.md`, and run
  `python3 bench/render.py` to draw its figures and tables.

A run switches to workspace 1, starts a foot terminal there with the focus, for the user, opens the task window and
moves it to a hidden workspace, and runs the agent with `--backend hyprmasq` or `--backend cua` from that
backend's home. It stops before the agent starts if the user is not on workspace 1 or the task window is in view,
as Cua's foreground input can leave it after a run. It logs every event of Hyprland's socket2 with the time, and
the process of each window that opens, which `analyze.py` reads for the focus changes, the windows that opened and
Hyprland's dialogs, whose arguments it records. The form's checker counts the windows of the questionnaire's own
process as its own, such as a list that Qt opens as a window.

## The machine

- hyprmasq built from this checkout with `make`, its plugin loaded with `hyprctl plugin load`, and
  `hyprmasq-mcp`, `hyprmasq-input` and `hyprmasq-launch` on `PATH`, as [Installation](../../docs/installation.md)
  describes. Without Go on the machine, build `hyprmasq-mcp` elsewhere with `nix build .#hyprmasq-mcp` and copy
  it over. It is linked statically.
- Cua Driver from its installer, and its Hyprland plugin built from the source of the same release with
  `-DCUA_HYPRLAND_INPUT=ON`, loaded, and enabled with `hl.config({plugin = {cua = {enabled = true}}})` in
  `hyprland.lua`. Cua's plugin loads only once per desktop session: replacing it needs a new login, or
  `systemctl soft-reboot`, which keeps an encrypted disk unlocked.
- `foot`, `python-gobject` and GTK 4 for the questionnaire, PySide6 for its Qt port, for example in a venv
  (`python3 -m venv ~/bench/qt && ~/bench/qt/bin/pip install PySide6`, then `QT_PYTHON=~/bench/qt/bin/python3`),
  and Inkscape.

## The agent homes

Each backend has its own home in `$BENCH_HOMES`, default `~/bench/homes`, so that no agent sees the other tool, its
skill or the user's own setup:

- `hyprmasq/.codex/config.toml` and `cua/.codex/config.toml` from [homes](homes), each with a copy of the user's
  `~/.codex/auth.json`. Codex starts MCP servers without the session's environment, so both configs pass it on
  with `env_vars`. Their commands are the absolute paths of the laptop that ran the comparison: replace
  `/home/daniel` with your home, and Cua's release directory with the one its installer made.
- Cua's skill for Codex: `HOME=$BENCH_HOMES/cua cua-driver skills install`, with `$BENCH_HOMES/cua/.cua-driver`
  a link to `~/.cua-driver`.
- `claude-hyprmasq` and `claude-cua` for Claude Code, made with `HOME=... claude mcp add --scope user`, the
  command each tool documents, and Cua's skill installed in `claude-cua` the same way. Claude Code runs bill to
  `ANTHROPIC_API_KEY` and `ANTHROPIC_CUSTOM_HEADERS` from the environment `series.sh` starts with.

The runners call Codex and Claude Code by their real paths under `~/.local/share/mise/installs`, since mise's
shims do not work with another `HOME`.

## On a Mac

OpenAI's computer use comes with the ChatGPT app on macOS. `mac_setup.sh` prepares `~/hyprmasq-bench` with the Qt
questionnaire, PySide6 and `Questionnaire.app`, and `run_mac_form.py --tool openai-cu|cua --rep N --out DIR`
runs the questionnaire, with `--no-dropdowns` as on the laptop. It opens the questionnaire behind the user's
window, logs the frontmost app and, with `mac_windows.py`, the questionnaire's windows on screen, and grades the
app's state with `check.py`. macOS keeps no log of the windows that open, so in place of the rule against other
windows, a run fails when an app other than the user's and the questionnaire comes to the front while the agent
works.

- `--via exec` runs the agent itself: the Codex CLI that the ChatGPT app bundles, in code mode as the app runs it,
  with the user's `~/.codex` for OpenAI's computer use, and a TextEdit note in front as the user's window. Codex
  asks before a plugin acts on an app, which `--dangerously-bypass-approvals-and-sandbox` answers, as no one is
  there to: the agent's shell and other tools stay off.
- For Cua, `--tool cua` runs Codex from `~/hyprmasq-bench/homes/cua`, whose `.codex/config.toml` registers
  `cua-driver mcp` with `default_tools_approval_mode = "approve"`, beside a copy of `~/.codex/auth.json`, and
  whose `.agents/skills/cua-driver` links to Cua's skill. Cua Driver needs its own Accessibility and Screen
  Recording grants, which `cua-driver permissions grant` asks for.
- `--via app`, the default, measures a session started by hand in the ChatGPT app, with the app as the user's
  window: start each session as a new one without a project, with the prompt the runner prints, and leave the Mac
  alone until it is done.
- Run it in a terminal of the desktop session, such as tmux started from Terminal: a process started over SSH is
  outside the desktop, where neither the agent nor the computer-use tools can see the apps.

The first session that uses a tool on Questionnaire asks to allow it, which the measured runs must not include.
