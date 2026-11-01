# Runs on a real Hyprland machine

These scripts ran the earlier comparisons on a laptop with Omarchy and on a Mac, outside the
NixOS VMs of `bench/run.py`. They drive the user's live Hyprland session over SSH, so the machine must be idle,
unlocked and with its display on, and nobody may use it during a run.

- `run_form.py` runs the [form task](../tasks/form)'s questionnaire with this checkout's app and checker, the GTK 4
  app or, with `--toolkit qt`, its Qt port `form_qt.py`, run by `$QT_PYTHON`, a Python with PySide6. With
  `--no-dropdowns`, the questionnaire asks only checkboxes, sliders and text fields, as the task
  `form-no-dropdowns`.
- `run_inkscape.py` runs the [Inkscape task](../tasks/inkscape): move the rectangle in its
  [drawing.svg](../tasks/inkscape/drawing.svg) from x=120 to x=122 px and save. The grade reads the saved file with `inkscape --query-all`.
- `run_files.py` runs the file manager task: in a Nautilus window on a fresh folder, rename `report-draft.txt` to
  `report-final.txt` and move it into `Archive`. The grade compares every file's contents and place, and a window
  of another app fails the run. A dialog of xdg-desktop-portal, such as the folder chooser of Nautilus's Move to…,
  counts as the file manager's own, and the focus it takes as disturbance. Each run
  starts a Nautilus process of its own and ends it, so that a drag an agent left unfinished does not carry into the
  next run, and it stops if Nautilus already runs.
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
  `ANTHROPIC_API_KEY` and `ANTHROPIC_CUSTOM_HEADERS` from the environment `series.sh` starts with, or with
  `--claude-auth login` to the Claude login in the home's `.claude`, such as a link to `~/.claude/.credentials.json`,
  with no API key in their environment.

The runners call Codex and Claude Code by their real paths under `~/.local/share/mise/installs`, since mise's
shims do not work with another `HOME`.

## On a Mac

The Mac runs Cua Driver, OpenAI's computer use, which comes with the ChatGPT app, and Claude's computer use, which
comes with the Claude app, on the user's own Mac. `mac_setup.sh` prepares `~/hyprmasq-bench`: the Qt questionnaire,
PySide6 and `Questionnaire.app`, the runner, and an agent home for each tool. `run_mac.py --task TASK --tool
openai-cu|cua|claude-cu --rep N --out DIR` runs one task:

- `form` and `form-no-dropdowns`, the Qt questionnaire as the app Questionnaire, graded with `check.py`.
- `files`, the file manager task in Finder: a fresh folder Project in a Finder window, graded as on the laptop by
  `files_task.py`, which leaves out the `.DS_Store` files that Finder writes.
- `inkscape`, the drawing in Inkscape 1.4.4 from `brew install --cask inkscape`, the laptop's version, graded by
  `inkscape --query-all` on the saved file. The prompt says Cmd+S where the laptop's says Ctrl+S.

The runner opens the task's window behind the user's, logs the frontmost app and, with `mac_windows.py`, the task
app's windows on screen. macOS keeps no log of the windows that open, so in place of the rule against other
windows, a run fails when an app other than the user's and the task's comes to the front while the agent works.

- **OpenAI's computer use**, `--tool openai-cu --via exec`: the Codex CLI that the ChatGPT app bundles, in code mode
  as the app runs it, from `homes/openai-cu`. That home holds a copy of `~/.codex/auth.json`, a link to the
  Computer Use app, copies of the plugins and their marketplace, and only the computer-use tables of the user's
  `config.toml`, so that the user's `AGENTS.md`, memories and settings stay out of the runs.
  Codex loads the bundled plugins only from its default home, so the runner sets `HOME` to `homes/openai-cu` with
  no `CODEX_HOME`, and the home links the group container where Computer Use's helper finds the app's socket. The user's window is a
  TextEdit note. Codex asks before a plugin acts on an app, which `--dangerously-bypass-approvals-and-sandbox`
  answers, as no one is there to: the agent's shell and other tools stay off. `--via app` measures a session
  started by hand in the ChatGPT app instead, which runs with the user's own `~/.codex`.
- **Cua Driver**, `--tool cua`: Codex from `homes/cua`, whose `.codex/config.toml` registers `cua-driver mcp` with
  `default_tools_approval_mode = "approve"`, beside a copy of `~/.codex/auth.json`, and whose
  `.agents/skills/cua-driver` links to Cua's skill. With `--harness claude`, Claude Code from `homes/claude-cua`,
  with Cua's MCP server and skill, billed to `ANTHROPIC_API_KEY` or, without one, to the user's Claude
  subscription, whose login Claude Code finds through the home's link to `~/Library/Keychains`. Cua Driver needs its own Accessibility and Screen
  Recording grants and macOS's consent to direct capture, which `cua-driver permissions grant` asks for. Both homes
  link `Library/Caches/cua-driver` to the user's, so that `cua-driver` reaches the user's daemon, which holds those
  grants: from a home of its own it would start a daemon there whose screenshots fail. Codex runs Sol and Terra in
  code mode only, so the agent calls Cua's tools from JavaScript and sees a screenshot when its script passes it on.
- **Claude's computer use**, `--tool claude-cu`, runs only in the Claude app, signed in, with Computer use turned on
  in its settings, and Sonnet 5.5 at medium effort chosen for new Code sessions. The app is the user's window. With
  `--via drive`, the default, the runner drives the app itself through macOS's accessibility API (`ax_app.py`,
  with the Accessibility grant of the terminal it runs in), by the app's accessibility tree and without an agent: it opens a new Code session in `~/hyprmasq-bench/claude-cu`
  from the sidebar, or with `claude --desktop` the first time, checks that the session runs the model and effort
  asked for, pastes the prompt and sends it, and presses "Allow for this session" when Claude asks for the task's
  app or for full control of the screen. Every prompt it answers is logged to `claude-app.log`, and the time that
  `request_access` and `request_full_control` waited is left out of the run's time. With `--via app`, a person
  starts the session and answers the prompts by hand. The folder's `.claude/settings.json` denies Claude Code's own
  tools, and a run that calls a tool other than computer use, the ToolSearch that loads it and the app's
  computer-use skill fails. The session runs with the user's own `~/.claude`, so their `CLAUDE.md` and connectors
  are in its context. The app's transcript gives no cost: `analyze.py` prices its requests at the API's list prices.
  Cua Driver's daemons must not run during these runs: each keeps a window over the whole screen, for its agent
  cursor, which Claude's full control of the screen cannot hide, and Claude stops at it. Stop them with
  `pkill -f 'CuaDriver.app/Contents/MacOS/cua-driver serve'` before Claude's runs, and start Cua's again with
  `cua-driver permissions grant` afterwards, which launches it as the app that holds its grants.
- Run it in a terminal of the desktop session, such as tmux started from Terminal: a process started over SSH is
  outside the desktop, where neither the agent nor the computer-use tools can see the apps.

`mac_series.sh OUT REPS SET...` runs a series unattended, each SET a `"TASK TOOL MODEL"` such as
`"files cua gpt-6.1-terra"`, all at medium effort: rep 1 of every set, then rep 2, into `OUT/TASK-TOOL-MODEL`. It
keeps the display awake, resumes where it stopped, and stops after three runner errors in a row. Claude's computer
use runs in a series of its own, with Cua Driver's daemons stopped: the script refuses a series that mixes
`claude-cu` and `cua`, and `run_mac.py` a Claude run while a Cua Driver window is on screen. Before every Codex
run the runner copies the user's current `~/.codex/auth.json` into the agent's home, and afterwards writes a login
that the run renewed back, unless the user's changed meanwhile, as a refresh token works once. It records each
Codex session's service tier from Codex's log and fails a run whose tier is not the standard one.

The first session that uses a tool on an app asks to allow it, which the measured runs must not include: a
warm-up run of each tool on each app, before the series, is discarded.

### Privacy

The runs work on the user's own Mac. Their transcripts and screenshots stay there, but `analyze.py`'s
`comparison.json`, which [docs/comparison/data](../../docs/comparison/data) publishes, keeps text from them: the
agent's final message, the text of failed tool calls, the failures, and the apps that took the focus. Nobody may use
the Mac during a series, as another app in front fails a run and names that app in its data.

`analyze.py` writes a `comparison.json` in each set's directory of a series, and `mac_dataset.py RESULTS-DIR OUT`
joins them into one dataset of [docs/comparison/data](../../docs/comparison/data), keeping the `about` and `sources`
that OUT holds, which describe the series for a reader of the data alone.

Before a dataset is committed, `mac_privacy.py comparison.json [--watch FILE]` lists what in it may be personal:
paths in a home folder, e-mail, web and IP addresses, apps other than the runs' own in the focus changes and
failures, and the words of the watch file, such as names or hosts. It exits with 1 when it lists anything.
