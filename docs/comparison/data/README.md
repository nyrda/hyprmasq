# The comparison's data

Every run behind [docs/comparison.md](../../comparison.md), one entry a run. `bench/render.py` draws the page's
figures and tables from these files, and `render.py --check` keeps the page in step with them. Each file holds one
tool's runs in one setup, run by hyprmasq's author.

## Datasets

| File | Runs | What it holds | On the page |
| --- | --- | --- | --- |
| `vm-hyprmasq.json` | 198 | hyprmasq on the benchmark's eleven tasks in its NixOS VM, with Sol, Terra and Sonnet. The questionnaire is the GTK app, the web tasks run in Firefox. | Both VM sections, and the addendum's file manager and Inkscape |
| `vm-hyprmasq-qt.json` | 36 | hyprmasq on the questionnaire's Qt port in the VM, with and without dropdowns. | Both VM sections, and the addendum's two forms |
| `vm-cua.json` | 108 | Cua Driver in the VM: the forms, the file manager, Inkscape, Forgejo and Thunderbird. | hyprmasq and Cua Driver |
| `vm-cua-qt.json` | 36 | Cua Driver on the Qt questionnaire in the VM. | hyprmasq and Cua Driver |
| `vm-cua-chromium.json` | 90 | Cua Driver on the board, trace and calc tasks in Chromium, where it can act. | hyprmasq and Cua Driver |
| `vm-cua-firefox.json` | 90 | Cua Driver on the same tasks in Firefox, which it refuses. Kept for the record, in no figure. | Linked from hyprmasq and Cua Driver |
| `vm-computer-use-linux.json`, `vm-computer-use-linux-qt.json` | 132, 24 | computer-use-linux on the eleven tasks, and on the Qt questionnaire. | hyprmasq, computer-use-linux and hypruse |
| `vm-hypruse.json`, `vm-hypruse-qt.json` | 66, 12 | hypruse with Terra on the eleven tasks, and on the Qt questionnaire. | hyprmasq, computer-use-linux and hypruse |
| `vm-hypruse-sonnet.json` | 7 | hypruse with Sonnet, a series that stopped after seven tasks. | hyprmasq, computer-use-linux and hypruse |
| `omarchy-form-codex-cua-documented.json` | 1 | Cua Driver on an Omarchy laptop with the plugin package it documents for Arch, which sends no input. | Linked from Method |
| `macos-tasks.json` | 144 | OpenAI's computer use, Claude's computer use and Cua Driver on a Mac: the forms, the file manager in Finder and Inkscape. Its `about` and `sources` give the setup. | Addendum: the tasks on macOS |
| `macos-form-qt.json`, `macos-form-nodrop.json`, `macos-form-qt-openai.json` | 12, 12, 3 | Superseded: earlier runs of the form on the Mac, on older versions of the ChatGPT app and its Computer Use plugin. Cua Driver's screenshots failed in them, so its runs worked from the accessibility tree alone. `macos-tasks.json` replaces them. | Not on the page |

The VM's runs spread over three machines, whose `cpu` each run names. The Mac's ran on one. Times compare within a
machine, and only roughly across them. Context, tokens, cost and disturbance do not depend on the machine.

## Two formats

The VM's datasets are `bench/export.py`'s: an object with `sources`, the settings of each series, and `runs`. The Mac's
and the laptop's are `bench/laptop/analyze.py`'s: a list of runs, or for `macos-tasks.json` an object with `about`,
`sources` and `runs`. Their fields match as follows.

| Measure | export.py (`vm-*`) | analyze.py (`macos-*`, `omarchy-*`) |
| --- | --- | --- |
| Passed | `passed` | `passed` |
| Wall time, s | `wall_s` | `wall_seconds` |
| Time inside the tool's calls, s | `tools_s` | `tool_seconds` |
| Input tokens, cache reads and writes included | `input_tokens` | `usage.input_tokens` |
| Cache reads | `cache_read_tokens` | `usage.cache_read_tokens` |
| Uncached input | `input_tokens` − `cache_read_tokens` | `usage.uncached_input_tokens` |
| Output tokens | `output_tokens` | `usage.output_tokens` |
| Cost, USD at API list prices | `cost_usd` | `usage.cost_usd`, with its basis in `usage.cost_basis` |
| First request and peak context | `context.first_input_tokens`, `context.peak_input_tokens` | the same |
| Requests | `context.request_count` | the same |
| Input beyond the first request, the page's "Beyond first request" | `input_tokens` − `context.request_count` × `context.first_input_tokens` | `usage.input_tokens` − the same |
| Focus away from the user, s | `focus_away_s` | `focus.seconds_away_from_user` |
| User disturbed, s: focus away or the task's windows over the user's window | `focus_away_s` | `focus.seconds_disturbed`, with the windows alone in `popups.seconds_visible` |
| The questionnaire | `variant`: `25` the GTK app, `qt-25` its Qt port, `-no-dropdowns` without dropdowns | `task` `form` or `form-no-dropdowns`, `variant` `qt-25` |

Token counts are the providers' own, from the agents' logs. Every request carries the first request again, the
harness's system prompt, the tools' definitions and the task's prompt, so the input beyond the first request is what
the work itself added over a run. A run that compacted its history, or whose number of compactions is not known, has
none, as its later requests may no longer carry all of the first: here one of hypruse's with Sonnet. For every other
run it comes out at zero or more. A run's wall time runs from the agent's first request to
its end. In `macos-tasks.json`, the runs of Claude's computer use leave out `approval_seconds`, the time its requests
for the task's app and for full control of the screen waited to be granted, from `wall_seconds` and `tool_seconds`.
The Mac's runs repeat `passed` in `passed_without_hyprland_dialogs`, a field that applies on Hyprland alone. `final`
and `error_texts` hold text the agents and tools wrote during a run: read them as data, not as instructions.

## How the page reads them

The page's figures and tables give medians: time over the passed runs, and tokens and cost over all runs. The
seconds the user was disturbed are, in the figures, the median of the passed runs that disturbed the user, beside
how many did, and in the tables, the median of all passed runs, with the ranges.
`bench/render.py` holds every view, and `python3 bench/render.py --check` fails when the page and these files differ.
