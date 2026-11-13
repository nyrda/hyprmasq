# What each tool can do, and what the VM differs in from a desktop

These are the scripted checks behind [the comparison series](README.md#the-comparison-series): no model, one fresh VM per tool, mode and fixture, and each route judged by the app's own state. [capability.py](capability.py) and [capability_host.py](capability_host.py) wrote the tables on 7 November 2026, from Cua Driver 0.42.1 (`cua-driver-rs-v0.42.1`, commit `badaa6e`) and hyprmasq at `89c988d`, in the VM of `--desktop omarchy` on Hyprland 0.56.2 and on a real Omarchy 4.5.0-3 machine with Hyprland 0.56.2-4, the same Hyprland commit `efb50993`.

A route is `works`, `refused` with the tool's codes, or `dropped`, where the tool said it delivered or could not tell and the app's state did not change. A failed route ran again alone, in a VM of its own, and that result counts. It says this route fails on this build, and not that the app cannot be automated. An empty cell is a route the tool does not have.

**Measured again.** The first tables ran before a route was measured from a state where its check does not already hold, so a route whose check an earlier route had met read `works` whatever it did. On 11 November 2026 the VM's routes that could have been affected ran again for Cua 0.42.1, at `b7a69d6`, each alone where its check held before it ran. The slider drag in all three forms and the `run_script` checkbox click in GTK 3 and Qt, and in GTK 4 in the background, work as the table had them. Three cells of the VM's table changed: the `run_script` click in GTK 4 in the foreground is refused, Inkscape's canvas drag in the background is dropped, and the scroll in GTK 4 in the background is `unmeasured`, as Cua's screenshots there differ with no input, so a screenshot cannot show a scroll. Cua's desktop curve by `mouse_button_down`, `mouse_drag` and `mouse_button_up` had gone to the window and not to the desktop: on the VM it ran again on the desktop, at `f55da4f`, and is refused there too, so its cells stand. The real machine's table did not run again: its cells for the routes above may read `works` for the same reason, and its desktop cells of that curve were measured against the window.

What this says:

- **Thunderbird and Firefox are open to Cua.** Its background pointer and keys added a whole account in Thunderbird, on the VM's mail server, and placed labels and drew strokes in Firefox's board `pointer` and trace pages. Its foreground refuses both apps (`foreground_primary_binding`).
- **Chromium takes Cua's foreground and desktop routes**, and refuses its background (`client_not_bound`). Its browser tools attach only when remote debugging was on from the browser's start, and its isolated browser starts in both documented forms.
- **Firefox's board `dnd` variant** drops Cua's background and desktop drags in the VM, and its foreground refuses them. In Chromium its desktop route places them, and its foreground refuses them.
- **Keys in the file manager:** cut and paste drop in Cua's background and on its desktop, and work in its foreground since 0.42.1. A drag into a folder works only on its desktop. Its rename works by keys and `set_value` in the background.
- **`mouse_button_down` and `mouse_drag`** refuse everywhere, so a curve is joined straight drags in Cua, where it is one path in hyprmasq.
- **Dropdowns:** see the table. Which route works depends on the toolkit and the mode, and in GTK and Qt a foreground popup holds the seat (`foreground_grab`).
- **Forgejo with a user who reacts:** Cua signs in on its foreground and desktop, with the user's notes intact, and hyprmasq in the background. With a user who types without a pause, Cua's routes drop or refuse, which is why the series runs the reacting variant.

## The VM

On machine A (Ryzen 5 5600X, 6 cores, 31 GiB), the series' machine, with Cua's cursor off and each VM on 2 physical cores. The forms and the file manager ran three times with the same result in every row. Machine C (Ryzen 7 5800X3D, 8 cores, 31 GiB) gave the same table in all 277 rows that both ran but one: a desktop drag into a folder dropped there, with four VMs running at once, and worked in all three passes on machine A. hyprmasq has no route with an element token or `set_value`.

| App | Action | Route | hyprmasq | Cua background | Cua foreground | Cua desktop |
| --- | --- | --- | --- | --- | --- | --- |
| attach-debugging-board-pointer | attach to the user's browser | existing_profile, debugging on |  | works |  |  |
| attach-debugging-board-pointer | drag a label | browser_pointer, background |  | refused `browser_input_trust_unavailable` |  |  |
| attach-debugging-board-pointer | drag a label | browser_pointer, foreground |  | works |  |  |
| board-dnd-chromium | drag a label | drag | works | refused `client_not_bound` | refused `foreground_unavailable` | works |
| board-dnd-chromium | drag a label | drag with steps |  | refused `client_not_bound` | refused `foreground_unavailable` | works |
| board-dnd-chromium | drag a label | joined path | works | refused `client_not_bound` | refused `foreground_unavailable` | works |
| board-dnd-firefox | drag a label | drag | works | dropped | refused `foreground_primary_binding` | dropped |
| board-dnd-firefox | drag a label | drag with steps |  | dropped | refused `foreground_primary_binding` | dropped |
| board-dnd-firefox | drag a label | joined path | works | dropped | refused `foreground_primary_binding` | dropped |
| board-pointer-chromium | drag a label | drag | works | refused `client_not_bound` | works | works |
| board-pointer-chromium | drag a label | drag with steps |  | refused `client_not_bound` | works | works |
| board-pointer-chromium | drag a label | joined path | works | refused `client_not_bound` | works | works |
| board-pointer-firefox | drag a label | drag | works | works | refused `foreground_primary_binding` | works |
| board-pointer-firefox | drag a label | drag with steps |  | works | refused `foreground_primary_binding` | works |
| board-pointer-firefox | drag a label | joined path | works | works | refused `foreground_primary_binding` | works |
| calc-chromium | click a tab in the browser | pixels | works | refused `client_not_bound` | works | works |
| calc-chromium | key in the browser | Ctrl+Tab | works | refused `client_not_bound` | works | works |
| calc-chromium | read the report tab | accessibility tree |  | works | works |  |
| calc-chromium | read the report tab | browser tools |  | refused `browser_wrong_target_refused` | refused `browser_wrong_target_refused` |  |
| calc-chromium | save | Ctrl+S | works | works | works | works |
| calc-chromium | type a figure in a cell | click, type, Return | works | works | works | works |
| calc-chromium | type a formula | click, type, Return | works | works | works | works |
| calc-firefox | click a tab in the browser | pixels | works | works | refused `foreground_primary_binding` | works |
| calc-firefox | key in the browser | Ctrl+Tab | works | works | refused `foreground_primary_binding` | works |
| calc-firefox | read the report tab | accessibility tree |  | works | works |  |
| calc-firefox | read the report tab | browser tools |  | refused `browser_consent_required` | refused `browser_consent_required` |  |
| calc-firefox | save | Ctrl+S | works | works | works | works |
| calc-firefox | type a figure in a cell | click, type, Return | works | works | works | works |
| calc-firefox | type a formula | click, type, Return | works | works | works | works |
| files | drag into folder | pixels | works | dropped | refused `foreground_unavailable` | works |
| files | move by keys | click, cut, open, paste | works | dropped | works | dropped |
| files | move by keys | element tokens, cut, paste |  | dropped | dropped |  |
| files | rename | click, F2, type, Return | works | works | refused `foreground_grab` | works |
| files | rename | element token, F2, set_value |  | works | refused |  |
| forgejo-reacting | sign in | pixels, type | works | refused `background_unavailable` | works | works |
| forgejo-typing | sign in | pixels, type | works | refused `background_unavailable` | dropped | dropped |
| form-gtk3 | click checkbox | pixels | works | works | works | works |
| form-gtk3 | click checkbox | run_script |  | works | works |  |
| form-gtk3 | drag slider | pixels | works | works | works | works |
| form-gtk3 | dropdown | click, Down keys, Return | works | works | refused `foreground_grab` | dropped |
| form-gtk3 | dropdown | click, click item by pixels | works | dropped | refused `foreground_grab` | works |
| form-gtk3 | dropdown | element tokens |  | works | works |  |
| form-gtk3 | key toggles checkbox | Tab, space | works | works | works | dropped |
| form-gtk3 | scroll | pixels | works | works | works | works |
| form-gtk3 | set field | set_value |  | works | works |  |
| form-gtk3 | set slider | set_value |  | works | works |  |
| form-gtk3 | type in field | click, type | works | dropped | works | works |
| form-gtk4 | click checkbox | pixels | works | works | works | works |
| form-gtk4 | click checkbox | run_script |  | works | refused `background_unavailable` |  |
| form-gtk4 | drag slider | pixels | works | works | works | works |
| form-gtk4 | dropdown | click, Down keys, Return | works | works | refused `foreground_grab` | works |
| form-gtk4 | dropdown | click, click item by pixels | works | dropped | refused `foreground_grab` | works |
| form-gtk4 | dropdown | element tokens |  | dropped | refused |  |
| form-gtk4 | key toggles checkbox | Tab, space | works | works | works | works |
| form-gtk4 | scroll | pixels | works | unmeasured | works | works |
| form-gtk4 | set field | set_value |  | works | works |  |
| form-gtk4 | set slider | set_value |  | works | works |  |
| form-gtk4 | type in field | click, type | works | works | works | works |
| form-qt | click checkbox | pixels | works | works | works | works |
| form-qt | click checkbox | run_script |  | works | works |  |
| form-qt | drag slider | pixels | works | works | works | works |
| form-qt | dropdown | click, Down keys, Return | works | refused `client_drops_background_keys` | refused `foreground_grab` | works |
| form-qt | dropdown | click, click item by pixels | works | dropped | refused `foreground_grab` | works |
| form-qt | dropdown | element tokens |  | dropped | dropped |  |
| form-qt | key toggles checkbox | Tab, space | works | refused `client_drops_background_keys` | works | works |
| form-qt | scroll | pixels | works | dropped | works | works |
| form-qt | set field | set_value |  | works | works |  |
| form-qt | set slider | set_value |  | works | works |  |
| form-qt | type in field | click, type | works | refused `client_drops_background_keys` | works | works |
| inkscape | drag on canvas, save | pixels, Ctrl+S | works | dropped | works | works |
| inkscape | move by X field, save | click, type, Ctrl+S | works | dropped | works | dropped |
| inkscape | move by X field, save | element token, set_value, Ctrl+S |  | works | works |  |
| isolated-board-dnd | attach to the user's browser | existing_profile |  | refused `browser_wrong_target_refused` |  |  |
| isolated-board-dnd | drag a label | browser_pointer, background |  | refused `browser_input_trust_unavailable` |  |  |
| isolated-board-dnd | drag a label | browser_pointer, foreground |  | works |  |  |
| isolated-board-dnd | isolated browser | allow_launch, no pid |  | works |  |  |
| isolated-board-dnd | isolated browser | allow_launch, with pid |  | works |  |  |
| isolated-board-pointer | attach to the user's browser | existing_profile |  | refused `browser_wrong_target_refused` |  |  |
| isolated-board-pointer | drag a label | browser_pointer, background |  | refused `browser_input_trust_unavailable` |  |  |
| isolated-board-pointer | drag a label | browser_pointer, foreground |  | works |  |  |
| isolated-board-pointer | isolated browser | allow_launch, no pid |  | works |  |  |
| isolated-board-pointer | isolated browser | allow_launch, with pid |  | works |  |  |
| thunderbird | add the account | pixels, type | works | works | refused `foreground_primary_binding` | works |
| thunderbird | set the name field | set_value |  | dropped | dropped |  |
| trace-river-chromium | draw a curve | joined drags |  | refused `client_not_bound` | works | works |
| trace-river-chromium | draw a curve | mouse_button_down, drag, up |  | refused | refused | refused |
| trace-river-chromium | draw a curve | path of many points | works |  |  |  |
| trace-river-chromium | draw a stroke | drag | works | refused `client_not_bound` | works | works |
| trace-river-firefox | draw a curve | joined drags |  | works | refused `foreground_primary_binding` | works |
| trace-river-firefox | draw a curve | mouse_button_down, drag, up |  | refused | refused | refused |
| trace-river-firefox | draw a curve | path of many points | works |  |  |  |
| trace-river-firefox | draw a stroke | drag | works | works | refused `foreground_primary_binding` | works |

### What moved from Cua 0.41.2 to 0.42.1

The whole table ran on both releases, and these rows differ. The commit that moved each was not traced. The release's own notes name non-ASCII text in GTK 3 and Qt, a window state by `pid` alone on Wayland, the browser's setup and fixes to its capture worker.

| App | Action | Route | Mode | 0.41.2 | 0.42.1 |
| --- | --- | --- | --- | --- | --- |
| files | move by keys | click, cut, open, paste | foreground | dropped | works |
| form-gtk4 | type in field | click, type | background | dropped | works |
| form-gtk3 | dropdown | click, click item by pixels | background | works | dropped |
| form-gtk3 | dropdown | click, Down keys, Return | foreground | works, after `foreground_grab` | refused `foreground_grab` |
| form-gtk3 | dropdown | click, click item by pixels | foreground | works, after `foreground_grab` | refused `foreground_grab` |

The GTK 3 dropdown still has a working route in each of those modes: Down keys in the background and element tokens in both.

## The real machine

The same routes on the real Omarchy machine, machine B (a laptop with Omarchy, Ryzen 7 PRO 4750U, 8 cores, 31 GiB), at its own scale and in its own layout, but for Forgejo, which has no server there, Calc, which has no fixture there, and Thunderbird's account, for which there is no mail server.

Cua's columns are of 0.42.1, from a Hyprland of their own on another virtual terminal of the same machine, beside the user's session, with Omarchy's configuration and Cua's plugin loaded once, as its README asks. A plugin that loads again into a running Hyprland stays in `discovery_only` and refuses the background and the foreground, so the user's session could not serve. This Hyprland had `"state":"input_v3_candidate"` with its transport ready, and stayed on its terminal to the end. It left out Omarchy's autostart, so its shell's bar of 26 px was not there, and a line meant to keep its place failed, so Hyprland's bar of configuration errors took 42 px at the top: windows were 16 px shorter than in the session. Against the session's runs of 0.41.2 it moved where the VM moved between the releases, and in four more places, each now as in the VM: Chromium's foreground, foreground Tab and space in the GTK 4 and Qt forms, and a click on the GTK 4 dropdown's item in the background. It agrees with the VM in every route but one, which the session's runs share, so no difference found points to the shorter windows.

| App | Action | Route | hyprmasq `89c988d` | Cua background | Cua foreground | Cua desktop |
| --- | --- | --- | --- | --- | --- | --- |
| board-dnd-chromium | drag a label | drag | works | refused `client_not_bound` | refused `foreground_unavailable` | works |
| board-dnd-chromium | drag a label | drag with steps |  | refused `client_not_bound` | refused `foreground_unavailable` | works |
| board-dnd-chromium | drag a label | joined path | works | refused `client_not_bound` | refused `foreground_unavailable` | works |
| board-dnd-firefox | drag a label | drag | works | dropped | refused `foreground_primary_binding` | dropped |
| board-dnd-firefox | drag a label | drag with steps |  | dropped | refused `foreground_primary_binding` | dropped |
| board-dnd-firefox | drag a label | joined path | works | dropped | refused `foreground_primary_binding` | dropped |
| board-pointer-chromium | drag a label | drag | works | refused `client_not_bound` | works | works |
| board-pointer-chromium | drag a label | drag with steps |  | refused `client_not_bound` | works | works |
| board-pointer-chromium | drag a label | joined path | works | refused `client_not_bound` | works | works |
| board-pointer-firefox | drag a label | drag | works | works | refused `foreground_primary_binding` | works |
| board-pointer-firefox | drag a label | drag with steps |  | works | refused `foreground_primary_binding` | works |
| board-pointer-firefox | drag a label | joined path | works | works | refused `foreground_primary_binding` | works |
| files | drag into folder | pixels | works | dropped | refused `foreground_unavailable` | works |
| files | move by keys | click, cut, open, paste | works | dropped | works | dropped |
| files | move by keys | element tokens, cut, paste |  | dropped | dropped |  |
| files | rename | click, F2, type, Return | refused | works | refused `foreground_grab` | works |
| files | rename | element token, F2, set_value |  | works | refused |  |
| form-gtk3 | click checkbox | pixels | works | works | works | works |
| form-gtk3 | click checkbox | run_script |  | works | works |  |
| form-gtk3 | drag slider | pixels | works | works | works | works |
| form-gtk3 | dropdown | click, Down keys, Return | works | works | refused `foreground_grab` | dropped |
| form-gtk3 | dropdown | click, click item by pixels | dropped | dropped | refused `foreground_grab` | dropped |
| form-gtk3 | dropdown | element tokens |  | works | works |  |
| form-gtk3 | key toggles checkbox | Tab, space | works | works | works | dropped |
| form-gtk3 | scroll | pixels | works | works | works | works |
| form-gtk3 | set field | set_value |  | works | works |  |
| form-gtk3 | set slider | set_value |  | works | works |  |
| form-gtk3 | type in field | click, type | works | dropped | works | works |
| form-gtk4 | click checkbox | pixels | works | works | works | works |
| form-gtk4 | click checkbox | run_script |  | works | works |  |
| form-gtk4 | drag slider | pixels | works | works | works | works |
| form-gtk4 | dropdown | click, Down keys, Return | works | works | refused `foreground_grab` | works |
| form-gtk4 | dropdown | click, click item by pixels | dropped | dropped | refused `foreground_grab` | works |
| form-gtk4 | dropdown | element tokens |  | dropped | refused |  |
| form-gtk4 | key toggles checkbox | Tab, space | works | works | works | works |
| form-gtk4 | scroll | pixels | works | works | works | works |
| form-gtk4 | set field | set_value |  | works | works |  |
| form-gtk4 | set slider | set_value |  | works | works |  |
| form-gtk4 | type in field | click, type | works | works | works | works |
| form-qt | click checkbox | pixels | works | works | works | works |
| form-qt | click checkbox | run_script |  | works | works |  |
| form-qt | drag slider | pixels | works | works | works | works |
| form-qt | dropdown | click, Down keys, Return | works | refused `client_drops_background_keys` | refused `foreground_grab` | works |
| form-qt | dropdown | click, click item by pixels | works | dropped | refused `foreground_grab` | works |
| form-qt | dropdown | element tokens |  | dropped | dropped |  |
| form-qt | key toggles checkbox | Tab, space | works | refused `client_drops_background_keys` | works | works |
| form-qt | scroll | pixels | works | dropped | works | works |
| form-qt | set field | set_value |  | works | works |  |
| form-qt | set slider | set_value |  | works | works |  |
| form-qt | type in field | click, type | works | refused `client_drops_background_keys` | works | works |
| inkscape | drag on canvas, save | pixels, Ctrl+S | works | works | works | works |
| inkscape | move by X field, save | click, type, Ctrl+S | works | dropped | works | dropped |
| inkscape | move by X field, save | element token, set_value, Ctrl+S |  | works | works |  |
| thunderbird | set the name field | set_value |  | dropped | dropped |  |
| thunderbird | type in the name field | click, type | works | refused `background_unavailable` | refused `foreground_primary_binding` | works |
| trace-river-chromium | draw a curve | joined drags |  | refused `client_not_bound` | works | works |
| trace-river-chromium | draw a curve | mouse_button_down, drag, up |  | refused | refused | refused |
| trace-river-chromium | draw a curve | path of many points | works |  |  |  |
| trace-river-chromium | draw a stroke | drag | works | refused `client_not_bound` | works | works |
| trace-river-firefox | draw a curve | joined drags |  | works | refused `foreground_primary_binding` | works |
| trace-river-firefox | draw a curve | mouse_button_down, drag, up |  | refused | refused | refused |
| trace-river-firefox | draw a curve | path of many points | works |  |  |  |
| trace-river-firefox | draw a stroke | drag | works | works | refused `foreground_primary_binding` | works |

## The start of a run

Until v2, `vm.py` took hyprmasq's screenshot of every window before `ready.json`, for hyprmasq alone. Such a capture lets a window on a hidden workspace draw, so hyprmasq's runs started with every window drawn and Cua Driver's did not: a window that a task moved away right after it mapped stayed blank or at the size it had before, until some input let it draw. From v2 on, `vm.py` shows each task's workspace until the screen stops changing, then the user's again with the focus back on the terminal, for every tool, and takes no window screenshot before the run.

`startcheck.py` asks each tool for its first screenshot of each task's window, before anything else touches it, and compares it with `grim`'s capture of the window drawn: the mean absolute difference of their pixels, 0 to 255, at `grim`'s size, `matches` under 8. On machine A for Cua Driver and on machine B for hyprmasq, every task of the series:

| Task | Browser | Cua Driver, start before v2 | Cua Driver, v2 | hyprmasq, v2 |
| --- | --- | --- | --- | --- |
| board:dnd | firefox | differs (130.66) | matches (4.08) | matches (4.99) |
| board:pointer | firefox | differs (130.66) | matches (4.08) | matches (4.99) |
| calc | firefox | differs (122.74) | matches (0.99) | matches (1.57) |
| files | firefox | matches (1.71) | matches (0.22) | matches (1.22) |
| forgejo:reacting | firefox | differs (13.48) | matches (1.81) | matches (1.86) |
| form:25 | firefox | matches (0.67) | matches (0.67) | matches (0.73) |
| form:25-no-dropdowns | firefox | matches (0.68) | matches (0.68) | matches (0.74) |
| form:qt-25 | firefox | matches (1.41) | matches (1.41) | matches (1.36) |
| form:qt-25-no-dropdowns | firefox | matches (1.49) | matches (1.49) | matches (1.45) |
| inkscape | firefox | matches (4.74) | matches (2.04) | matches (2.71) |
| thunderbird | firefox | differs (125.77) | matches (0.68) | matches (0.86) |
| trace:river | firefox | differs (152.78) | matches (1.19) | matches (2.02) |
| board:dnd | chromium | differs (77.57) | matches (4.19) | matches (4.42) |
| board:pointer | chromium | differs (77.57) | matches (4.19) | matches (4.42) |
| calc | chromium | matches (7.74) | matches (1.32) | matches (1.32) |
| trace:river | chromium | differs (61.64) | matches (1.6) | matches (1.77) |

Before v2, Cua Driver's first screenshot showed the board, the trace and Calc's report at half their width or blank, Thunderbird undrawn and Forgejo's window partly drawn, in Firefox and in Chromium but for two pages that drew in time. The forms, the file manager and Inkscape drew before they moved. In v2 every first screenshot matches the window as drawn, for both tools.

v2's runs at `3a2f692` gave the focus back to the terminal after the start step, which in Forgejo is not the user's window: its user types in their browser's notes, and typed nothing there. The fix gives the focus back to the window that had it before the step. At the fix commit, `startcheck.py --hold 30` on Forgejo shows the notes window focused at the start and the user typing 124 characters into it in 30 s, for both tools, where every run at `3a2f692` had none. For the other tasks the start is the same as at `3a2f692`: the same workspaces shown, the user's window the terminal, every first screenshot matching, and Cua Driver's first screenshot the same bytes as in every run of v2's rounds 1 and 2 of that task, but for Thunderbird and Inkscape, whose first screenshots differ from run to run at `3a2f692` too.

## Screenshots of hidden windows

`capability.py --mode fresh`, and `capability_host.py` on the real machine, ask whether a tool's screenshot shows a change that a hidden window's app made by itself: a terminal that prints the time, and a page of the fixture's browser that shows it, each moved to a hidden workspace, and two screenshots 3 s apart. `works` when they differ, `dropped` when the tool gave the same image twice, a stale frame.

| Window | VM, hyprmasq | VM, Cua Driver | Real Omarchy, hyprmasq | Real Omarchy, Cua Driver |
| --- | --- | --- | --- | --- |
| Terminal | shows the change | shows the change | shows the change | shows the change |
| Firefox | shows the change | shows the change | shows the change | shows the change |
| Chromium | shows the change | shows the change | shows the change | **the same image** |

In the VM both tools showed every change. On the real machine Cua Driver's screenshot of a hidden Chromium window stayed the same image, in the case and again alone, where its terminal and Firefox did not. A run's `frames` in `summary.json` flags screenshots that came back unchanged after an input that worked, for both tools.

## The VM against the real machine

Every difference found, with what was done about it. On Cua 0.42.1 the two tables differ in one route, and in Thunderbird, where they ran different routes.

| Difference | VM | Real Omarchy | Effect | Done |
| --- | --- | --- | --- | --- |
| Agent cursor | Software compositing, at more than 2 of the VM's 4 CPUs during a walk | GPU, unnoticeable | Walks 2 to 2.7 times as long, shots 2 times, for Cua alone | **Fixed:** `--no-overlay` in the VM |
| Accessibility walks | See below | See below | 1.4 to 2.4 times as long on machine A, for Cua alone, and past its budget of 1 s for a later read in the GTK 4 form, Inkscape and Chromium | **Cannot be fixed, written down:** `summary.json`'s `walk` splits them out, and the machine with the least cost runs the series |
| Cua Driver's screenshot of a hidden Chromium window | Shows a change the page made by itself | The same image twice | Holds Cua Driver back on the real machine, and the VM hides it: it favours Cua Driver in the VM | **Not fixed:** written down, and each run's `frames` flags it |
| Clicking a dropdown's item in the GTK 3 form, Cua's desktop | Works | Dropped, on both releases and in both Hyprlands | Favours Cua in the VM on this route, where Down keys and tokens work on both | Written down |
| Firefox's board `dnd` on Cua's desktop | Dropped in all 4 tries, on both releases | Worked in 1 of 3 tries, on 0.42.1 in the user's session | A drag that rarely lands on the real machine, and not in the VM: at most a small help Cua lacks in the VM on `board:dnd` in Firefox, where its background drops and its foreground refuses too | **Not fixed:** written down |
| Typing in Thunderbird in Cua's background | Added the whole account | Refused (`primary_target_busy`) on both releases | Cua's plugin refuses a background action at a client that holds the real seat's pointer or keyboard focus (`primary_conflict`, by client and not by window). The real check parks Thunderbird's first window, and a first start in a new profile may open a second, so this is most likely the check's and not the desktop's. In the series the user works in another app | **Not fixed:** written down |
| Key binds | 54, of workspaces and windows | 248, with apps and its own scripts | None on the routes | Reproduced in [omarchy-desktop.lua](omarchy-desktop.lua) |
| Portals | Hyprland's and GTK's, running | The same | None | Same |
| Commands on PATH | `wtype`, `wl-copy`, `gdbus` and the rest | The same, by `--environment` | Cua's desktop text needs `wtype` | **Fixed** |
| Task windows | Numbered workspaces | The user's workspaces | Binds reach them | **Fixed**, and the measures read as before |
| Isolated browser with `pid` | Chromium without its Nix wrapper died with SIGILL | Has its sandbox helper | The guide's example failed | **Fixed:** `CHROME_DEVEL_SANDBOX` in the server |
| Chromium | 154, with `--force-renderer-accessibility` | 155, without by default | In the VM's favour for Cua, whose guide asks for it | Kept, for every backend |
| `toolkit-accessibility` | On | Off by default on this machine | Trees were read on both | Kept: Cua's setup asks for it |
| Keymap | Plain `us` | `us` with `omarchy:shift_caps_compose` | Foreground keys worked on both | Kept: no route differs |
| Monitor | 1920×1080 at scale 1, one window fills it | 1920×1080 at 1.25, tiled at 1528×830 with gaps and animations | Coordinates map on both | Kept: the published tasks' display |
| Toolkits | GTK 4.22.4, Qt from the pinned set, Firefox 157, at-spi 2.60.6 | GTK 4.24.1, Qt 6.12, Firefox 158, at-spi 2.62.1 | The VM's GTK 4 walks 4 to 14 % slower on the real machine: small | Kept: one package set for all tools |
| The VM's GPU | None: apps and Hyprland draw in software | Integrated | Every tool's shots and redraws | Same for all, compared per machine |
| hyprmasq's popups | Work | **Closing a popup kills the GTK 4.24 or Qt 6.12 app**, after its value is set, or the rename's, with `error in client communication` in Hyprland, also with the packaged build at `f8d35bc` | Holds hyprmasq back on the real machine, and the VM hides it | **Not fixed here: a defect of hyprmasq to look into.** It does not touch Cua |

### The accessibility walks

The median of 20 walks in ms, at Cua 0.42.1, with its overlay off in the VM and the VM on 2 physical cores of machine A. Real Omarchy ran with its overlay on, which adds about 15 % there. Cua's budget is 2 s for a first read and 1 s for a later one, unless the agent names another.

| App | VM, hidden | Real, hidden | VM against real | Elements, VM and real |
| --- | --- | --- | --- | --- |
| GTK 4 form | 1578 | 807 | 2.0 | 28, 28 |
| GTK 3 form | 513 | 247 | 2.1 | 62, 62 |
| Qt form | 663 | 474 | 1.4 | 115, 115 |
| File manager | 550 | 403 | 1.4 | 28, 36 |
| Inkscape | 1837 | 1087 | 1.7 | 250, 250 |
| Firefox, board `pointer` | 424 | 577 | 0.7 | 122, 250 |
| Chromium, board `pointer` | 1158 | 477 | 2.4 | 39, 42 |
| Thunderbird | 239 | 145 | 1.6 | 68, 34 |
| Calc in Firefox | 794 | not run | | 250 |

Firefox and Thunderbird had different trees on the two, so their ratios say little. Where the trees agree, a walk on machine A costs 1.4 to 2.4 times the real machine's. In three of these apps it passes 1 s only in the VM or, for Inkscape, by more there: an agent in the VM gets a partial tree on a later read of the GTK 4 form or of Chromium, where on the real machine the whole tree fits the same budget.

The VM's cost is the wake-up of a process by another, which a walk does for every property of every element: a round trip over a Unix socket took these µs, on one machine without and with a VM.

| Machine | Without a VM | In the VM on 2 cores |
| --- | --- | --- |
| Machine B, laptop with Omarchy (Ryzen 7 PRO 4750U) | 11 to 15 | 54 to 64 |
| Machine C (Ryzen 7 5800X3D) | 9 to 10 | 30 |
| Machine A (Ryzen 5 5600X) | 7 | 14 |

A guest that polls in place of halting (`idle=poll`), `mitigations=off`, transparent huge pages, GTK's Cairo renderer, a quiet console and more or fewer vCPUs each changed the walks by less than the noise or made them worse, but for fewer physical cores: 1 core takes twice as long as 2, and 3 cores or any CPU gain 5 to 25 % over 2 with one VM running. So the series runs on the machine with the least cost, and each run's `walk` shows how much of its time went there, so that a page can give the ratio. The audit's 3,992 ms against 1,063 ms on the GTK 4 form was this and the overlay together.

### Workers on machine A

The driver's own time of a walk in ms, its median and 90th percentile, with Cua 0.42.1, and hyprmasq's call for comparison. The series keeps 2 VMs on 2 cores each. `dispatch.py` refuses a third VM on 2 cores, as that leaves this 6-core machine no core for the harness, and without pinning it brings the GTK 4 walk past the 2 s budget of a first read, which changes what agents do, as the next section shows.

| VMs | GTK 4 form, 2 cores each | GTK 4 form, any CPU | Inkscape, 2 cores each | Inkscape, any CPU | hyprmasq on the board, 2 cores each, any CPU |
| --- | --- | --- | --- | --- | --- |
| 1 | 1571 / 1623 | 1250 / 1281 | 1866 / 2329 | 1452 / 1672 | 0.777 s, 0.771 s |
| 2 | 1638 / 1722 | 1509 / 1538 | 2008 / 2341 | 1800 / 2148 | 0.779 s, 0.771 s |
| 3 | refused | 2036 / 2196 | refused | 2263 / 2605 | refused, 0.774 s |

On 2 cores each a second VM costs a walk 4 to 8 %, where on any CPU it costs 21 to 24 %, so the pin keeps a run's walks from depending on what else runs. Other load on the host sometimes takes most of a core, which the series leaves alone and each run's `host_load` records.

### Why walks are cut at about 1 s in some runs and whole in others

This is partly what agents do, and it should not be read as the machine's speed. Three runs each of Cua on the GTK 4 form, with GPT-6.1 Terra at medium effort, before the series. Three runs are few, and they show the pattern and not its size:

- On machine A the whole walk, 1570 to 1790 ms, fits the budget of a first read, so the agent keeps the default. Its later reads are cut at about 1010 ms, in 26 of 30 and 17 of 19 walks, which is cheap, and it names a longer budget on 1 to 3 reads. Its tool calls took 10 to 53 s of a run.
- On machines C and B the first read is cut at about 2020 ms, with 13 to 18 elements. With the form's dropdowns, agents then name 5 or 10 s on nearly all reads, 21 to 29 of 23 to 31, and pay 3 to 4 s each time. Tool calls took 91 to 133 s of a run. One run on `local` named it on 10 of 27 and took 53 s.
- Without the dropdowns, all machines name a longer budget on 1 to 4 reads only.

So a machine moves Cua's time mostly through whether its walk fits a first read. Past that line an agent's own reaction costs it more than the walk's extra time, and runs differ widely. On the real machine the GTK 4 walk nearly fits even a later read's 1 s, so machine A, the closest VM, still cuts reads where a real machine is borderline. `summary.json`'s `walk` has `budget_named`, `first_ms` and `first_partial`, so that the series can publish this.
