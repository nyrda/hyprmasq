# The comparison's numbers

Every table behind [the comparison](comparison.md), generated from [comparison/data](comparison/data). Each is folded.

## Runs passed

A tool's pass rate with its 95 % interval, per agent and kind of task. The mean of both agents is never a pool.

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium,v3-failures headline -->
<details><summary>Numbers: pass rates with their intervals, per agent and kind of task</summary>

| Tool | Agent | All 16 units | Apps | Image tasks | Firefox | Chromium | Failed because of the tool |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq | gpt-6.2-sol | 96/96 (96–100 %) | 60/60 (94–100 %) | 36/36 (90–100 %) | 24/24 (86–100 %) | 24/24 (86–100 %) | 0 of 96 |
| hyprmasq | Opus 5.5 | 94/96 (93–99 %) | 60/60 (94–100 %) | 34/36 (82–98 %) | 24/24 (86–100 %) | 22/24 (74–98 %) | 0 of 96 |
| hyprmasq | Mean of both | 99 % | 100 % | 97 % | 100 % | 96 % | – |
| Cua Driver | gpt-6.2-sol | 83/96 (78–92 %) | 54/60 (80–95 %) | 29/36 (65–90 %) | 18/24 (55–88 %) | 23/24 (80–99 %) | 10 of 96 |
| Cua Driver | Opus 5.5 | 88/96 (84–96 %) | 60/60 (94–100 %) | 28/36 (62–88 %) | 17/24 (51–85 %) | 23/24 (80–99 %) | 6 of 96 |
| Cua Driver | Mean of both | 89 % | 95 % | 79 % | 73 % | 96 % | – |
| hypruse | gpt-6.2-sol | 94/96 (93–99 %) | 60/60 (94–100 %) | 34/36 (82–98 %) | 22/24 (74–98 %) | 24/24 (86–100 %) | 2 of 96 |
| hypruse | Opus 5.5 | 87/96 (83–95 %) | 60/60 (94–100 %) | 27/36 (59–86 %) | 15/24 (43–79 %) | 24/24 (86–100 %) | 6 of 96 |
| hypruse | Mean of both | 94 % | 100 % | 85 % | 77 % | 100 % | – |
| computer-use-linux | gpt-6.2-sol | 96/96 (96–100 %) | 60/60 (94–100 %) | 36/36 (90–100 %) | 24/24 (86–100 %) | 24/24 (86–100 %) | 0 of 96 |
| computer-use-linux | Opus 5.5 | 87/96 (83–95 %) | 59/60 (91–100 %) | 28/36 (62–88 %) | 19/24 (60–91 %) | 21/24 (69–96 %) | 0 of 96 |
| computer-use-linux | Mean of both | 95 % | 99 % | 89 % | 90 % | 94 % | – |

</details>
<!-- /render -->

## Time

A time ratio is the geometric mean over tasks of a tool's median over hyprmasq's, within each agent, on the tasks
where both passed at least 3 of 6 runs. All four tools ran on the same host (Ryzen 7 5800X3D): `hyprmasq`, Cua Driver
and `hypruse` in rootless GPU containers, and `computer-use-linux` in KVM VMs (required for `/dev/uinput` on Hyprland).

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium headline-time -->
<details><summary>Time against hyprmasq, and the task units that entered it</summary>

| Tool | Agent | Time, times hyprmasq's | In tools, times hyprmasq's | Task units | Left out, too few passed runs |
| --- | --- | --- | --- | --- | --- |
| Cua Driver | gpt-6.2-sol | 1.74 | 0.83 | 14 of 16 | Form with dropdowns · Qt (passed: Cua Driver 1, hyprmasq 6); Label a photo, drag and drop · Firefox (passed: Cua Driver 0, hyprmasq 6) |
| Cua Driver | Opus 5.5 | 2.04 | 1.06 | 15 of 16 | Label a photo, drag and drop · Firefox (passed: Cua Driver 0, hyprmasq 6) |
| hypruse | gpt-6.2-sol | 1.72 | 1.22 | 16 of 16 | – |
| hypruse | Opus 5.5 | 1.86 | 1.13 | 15 of 16 | Label a photo, drag and drop · Firefox (passed: hypruse 0, hyprmasq 6) |
| computer-use-linux (KVM VM) | gpt-6.2-sol | 2.13 | 2.93 | 16 of 16 | – |
| computer-use-linux (KVM VM) | Opus 5.5 | 1.78 | 1.59 | 15 of 16 | Draw along a river bank · Firefox (passed: computer-use-linux 1, hyprmasq 6) |

</details>
<!-- /render -->

## The user

**No view change** means that no window opened on the user's workspace, the workspace did not switch and the focus
never left the user's window. A run that did nothing leaves it alone too, so read it beside the result. Input to the
user's window and compositor shortcuts are instrumented from Cua Driver's logs only (`–` for hypruse and
computer-use-linux). Net end cursor displacement compares start and end pointer position (Hyprland's `focuswindow`
warps the pointer back onto the user's window when focus returns, so a run that moved the pointer can still end at
0 px).

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium user -->
<details><summary>Numbers: the user's view, beside the result</summary>

| Tool | Agent | Passed | No view change, all runs | No view change, passed runs | Runs with input to the user's window | Runs with compositor shortcuts | Net end cursor disp. ≠0 (med px) | Forgejo: user's work kept | Forgejo: characters lost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq | gpt-6.2-sol | 96/96 | 96/96 | 96/96 | 0 | 0 | 0/96 (med 0) | 6/6 | 0 |
| hyprmasq | Opus 5.5 | 94/96 | 96/96 | 94/94 | 0 | 0 | 0/96 (med 0) | 6/6 | 0 |
| Cua Driver | gpt-6.2-sol | 83/96 | 45/96 | 40/83 | 2 | 14 | 48/96 (med 127) | 5/6 | 22 |
| Cua Driver | Opus 5.5 | 88/96 | 52/96 | 45/88 | 1 | 0 | 36/96 (med 0) | 3/6 | 33 |
| hypruse | gpt-6.2-sol | 94/96 | 5/96 | 5/94 | – | – | 23/96 (med 0) | 2/6 | 51 |
| hypruse | Opus 5.5 | 87/96 | 3/96 | 3/87 | – | – | 7/96 (med 0) | 4/6 | 33 |
| computer-use-linux | gpt-6.2-sol | 96/96 | 23/96 | 23/96 | – | – | 15/96 (med 0) | 3/6 | 78 |
| computer-use-linux | Opus 5.5 | 87/96 | 28/96 | 27/87 | – | – | 13/96 (med 0) | 2/6 | 51 |

</details>
<!-- /render -->

## Why runs failed

One verdict per failed run. [The failure verdicts](comparison/data/v3-failures.json) have every verdict, both
reviews and their evidence.

<details><summary>Earlier VM reference: what hyprmasq and Cua Driver 0.42.1 could do on Hyprland 0.56.2</summary>

A scripted check of each route in a Hyprland 0.56.2 VM against Cua Driver 0.42.1, with no model (kept as historical
reference, not re-measured for the current tool versions and not given to the failure reviewers). A route counts here
if any way of doing it works. hypruse and computer-use-linux have no such table.

| App | Action | hyprmasq | Cua background | Cua foreground | Cua desktop |
| --- | --- | --- | --- | --- | --- |
| Form, GTK 4 | click checkbox | works | works | works | works |
| Form, GTK 4 | drag slider | works | works | works | works |
| Form, GTK 4 | dropdown | works | works | refused | works |
| Form, GTK 4 | key toggles checkbox | works | works | works | works |
| Form, GTK 4 | scroll | works | unmeasured | works | works |
| Form, GTK 4 | set field |  | works | works |  |
| Form, GTK 4 | set slider |  | works | works |  |
| Form, GTK 4 | type in field | works | works | works | works |
| Form, Qt | click checkbox | works | works | works | works |
| Form, Qt | drag slider | works | works | works | works |
| Form, Qt | dropdown | works | dropped | dropped | works |
| Form, Qt | key toggles checkbox | works | refused | works | works |
| Form, Qt | scroll | works | dropped | works | works |
| Form, Qt | set field |  | works | works |  |
| Form, Qt | set slider |  | works | works |  |
| Form, Qt | type in field | works | refused | works | works |
| File manager | drag into folder | works | dropped | refused | works |
| File manager | move by keys | works | dropped | works | dropped |
| File manager | rename | works | works | refused | works |
| Inkscape | drag on canvas, save | works | dropped | works | works |
| Inkscape | move by X field, save | works | works | works | dropped |
| Thunderbird | add the account | works | works | refused | works |
| Thunderbird | set the name field |  | dropped | dropped |  |
| Forgejo | sign in | works | refused | works | works |
| Calc, Firefox | click a tab in the browser | works | works | refused | works |
| Calc, Firefox | key in the browser | works | works | refused | works |
| Calc, Firefox | read the report tab |  | works | works |  |
| Calc, Firefox | save | works | works | works | works |
| Calc, Firefox | type a figure in a cell | works | works | works | works |
| Calc, Firefox | type a formula | works | works | works | works |
| Calc, Chromium | click a tab in the browser | works | refused | works | works |
| Calc, Chromium | key in the browser | works | refused | works | works |
| Calc, Chromium | read the report tab |  | works | works |  |
| Calc, Chromium | save | works | works | works | works |
| Calc, Chromium | type a figure in a cell | works | works | works | works |
| Calc, Chromium | type a formula | works | works | works | works |
| Board-dnd, Firefox | drag a label | works | dropped | refused | dropped |
| Board-dnd, Chromium | drag a label | works | refused | refused | works |
| Board-pointer, Firefox | drag a label | works | works | refused | works |
| Board-pointer, Chromium | drag a label | works | refused | works | works |
| Trace, Firefox | draw a curve | works | works | refused | works |
| Trace, Firefox | draw a stroke | works | works | refused | works |
| Trace, Chromium | draw a curve | works | refused | works | works |
| Trace, Chromium | draw a stroke | works | refused | works | works |
| Board-pointer, Chromium with a debugging port | attach to the user's browser |  | works |  |  |
| Board-pointer, Chromium with a debugging port | drag a label |  | works |  |  |

The cells of the routes that an earlier route could have met were measured again, each alone. Cua Driver's Inkscape
canvas drag in the background is dropped, and its GTK 4 scroll in the background cannot be measured by screenshot.
[bench/capability-results.md](../bench/capability-results.md) has each route, the tools' refusal codes and the real
machine's table.

</details>

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium,v3-cua-attach,v3-failures attribution-table -->
<details><summary>Each task's failed runs, with the reasons for unclear</summary>

| Task | Tool | Agent | Passed | Model mistake | Tool problem | Unclear | Why unclear |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Label a photo, drag and drop · Chromium | hyprmasq | Opus 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, custom drag · Chromium | hyprmasq | Opus 5.5 | 5/6 | 1 | 0 | 0 | – |
| Upload to Forgejo, connect a dashboard | Cua Driver | gpt-6.2-sol | 5/6 | 1 | 0 | 0 | – |
| Form with dropdowns · Qt | Cua Driver | gpt-6.2-sol | 1/6 | 0 | 4 | 1 | reviewers disagree 1 |
| Label a photo, drag and drop · Firefox | Cua Driver | gpt-6.2-sol | 0/6 | 0 | 6 | 0 | – |
| Label a photo, drag and drop · Firefox | Cua Driver | Opus 5.5 | 0/6 | 0 | 6 | 0 | – |
| Draw along a river bank · Firefox | Cua Driver | Opus 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, custom drag · Chromium | Cua Driver | gpt-6.2-sol | 5/6 | 1 | 0 | 0 | – |
| Draw along a river bank · Chromium | Cua Driver | Opus 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, drag and drop · Firefox | hypruse | gpt-6.2-sol | 4/6 | 0 | 2 | 0 | – |
| Label a photo, drag and drop · Firefox | hypruse | Opus 5.5 | 0/6 | 0 | 6 | 0 | – |
| Draw along a river bank · Firefox | hypruse | Opus 5.5 | 3/6 | 3 | 0 | 0 | – |
| Form with dropdowns · Qt | computer-use-linux | Opus 5.5 | 5/6 | 1 | 0 | 0 | – |
| Draw along a river bank · Firefox | computer-use-linux | Opus 5.5 | 1/6 | 5 | 0 | 0 | – |
| Draw along a river bank · Chromium | computer-use-linux | Opus 5.5 | 3/6 | 3 | 0 | 0 | – |

</details>
<!-- /render -->

## Every task, tool and agent

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium comparison-table -->
<details><summary>Numbers</summary>

**Outcome and time**

| Task | Agent | Tool | Where | Passed (95 % interval) | Time s, passed | In tools s, passed | Time s, failed | No view change | User's work kept | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · GTK 4 | 6/6 (61–100 %) | 85 (72–110) | 34 (34–39) | – | 6/6 | – | 14 (13–18) |
| Form with dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · Qt | 6/6 (61–100 %) | 66 (60–124) | 26 (26–27) | – | 6/6 | – | 12 (11–13) |
| Form with dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · GTK 4 | 6/6 (61–100 %) | 148 (112–190) | 26 (20–30) | – | 3/6 | – | 36 (23–40) |
| Form with dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · Qt | 1/6 (3–56 %) | – | – | 62 (56–67) | 5/6 | – | 9 (7–29) |
| Form with dropdowns | gpt-6.2-sol | hypruse | Hyprland container · GTK 4 | 6/6 (61–100 %) | 117 (104–141) | 32 (30–54) | – | 0/6 | – | 26 (21–51) |
| Form with dropdowns | gpt-6.2-sol | hypruse | Hyprland container · Qt | 6/6 (61–100 %) | 136 (109–143) | 46 (20–57) | – | 0/6 | – | 27 (23–68) |
| Form with dropdowns | Opus 5.5 | hyprmasq | Hyprland container · GTK 4 | 6/6 (61–100 %) | 77 (70–83) | 40 (38–41) | – | 6/6 | – | 19 (18–21) |
| Form with dropdowns | Opus 5.5 | hyprmasq | Hyprland container · Qt | 6/6 (61–100 %) | 69 (65–87) | 30 (28–32) | – | 6/6 | – | 22 (18–28) |
| Form with dropdowns | Opus 5.5 | Cua Driver | Hyprland container · GTK 4 | 6/6 (61–100 %) | 166 (134–330) | 31 (17–91) | – | 4/6 | – | 55 (45–81) |
| Form with dropdowns | Opus 5.5 | Cua Driver | Hyprland container · Qt | 6/6 (61–100 %) | 146 (112–168) | 19 (17–28) | – | 0/6 | – | 46 (26–58) |
| Form with dropdowns | Opus 5.5 | hypruse | Hyprland container · GTK 4 | 6/6 (61–100 %) | 155 (133–193) | 38 (29–53) | – | 0/6 | – | 30 (24–36) |
| Form with dropdowns | Opus 5.5 | hypruse | Hyprland container · Qt | 6/6 (61–100 %) | 185 (152–260) | 57 (45–94) | – | 0/6 | – | 41 (35–67) |
| Form with dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 149 (129–161) | 56 (46–66) | – | 0/6 | – | 58 (51–78) |
| Form with dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · Qt | 6/6 (61–100 %) | 141 (118–151) | 38 (31–55) | – | 0/6 | – | 96 (61–101) |
| Form with dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 246 (102–276) | 75 (42–108) | – | 3/6 | – | 148 (47–166) |
| Form with dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · Qt | 5/6 (44–97 %) | 143 (111–175) | 25 (20–32) | 80 | 4/6 | – | 71 (39–98) |
| Form without dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · GTK 4 | 6/6 (61–100 %) | 72 (69–102) | 30 (28–37) | – | 6/6 | – | 12 (12–14) |
| Form without dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · Qt | 6/6 (61–100 %) | 59 (54–71) | 24 (23–25) | – | 6/6 | – | 9 (7–10) |
| Form without dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · GTK 4 | 6/6 (61–100 %) | 86 (66–96) | 11 (7–16) | – | 2/6 | – | 17 (14–24) |
| Form without dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · Qt | 6/6 (61–100 %) | 43 (40–84) | 6 (6–7) | – | 5/6 | – | 6 (6–8) |
| Form without dropdowns | gpt-6.2-sol | hypruse | Hyprland container · GTK 4 | 6/6 (61–100 %) | 93 (85–102) | 29 (25–31) | – | 0/6 | – | 19 (16–28) |
| Form without dropdowns | gpt-6.2-sol | hypruse | Hyprland container · Qt | 6/6 (61–100 %) | 145 (107–205) | 46 (26–78) | – | 0/6 | – | 118 (28–156) |
| Form without dropdowns | Opus 5.5 | hyprmasq | Hyprland container · GTK 4 | 6/6 (61–100 %) | 66 (56–77) | 35 (34–38) | – | 6/6 | – | 17 (16–18) |
| Form without dropdowns | Opus 5.5 | hyprmasq | Hyprland container · Qt | 6/6 (61–100 %) | 52 (51–62) | 28 (27–28) | – | 6/6 | – | 15 (13–17) |
| Form without dropdowns | Opus 5.5 | Cua Driver | Hyprland container · GTK 4 | 6/6 (61–100 %) | 127 (109–194) | 22 (14–80) | – | 6/6 | – | 44 (39–52) |
| Form without dropdowns | Opus 5.5 | Cua Driver | Hyprland container · Qt | 6/6 (61–100 %) | 36 (29–80) | 6 (6–11) | – | 6/6 | – | 6 (4–20) |
| Form without dropdowns | Opus 5.5 | hypruse | Hyprland container · GTK 4 | 6/6 (61–100 %) | 190 (125–232) | 54 (35–65) | – | 0/6 | – | 31 (24–46) |
| Form without dropdowns | Opus 5.5 | hypruse | Hyprland container · Qt | 6/6 (61–100 %) | 200 (163–244) | 75 (59–92) | – | 0/6 | – | 38 (34–42) |
| Form without dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 122 (90–215) | 45 (34–78) | – | 1/6 | – | 72 (50–150) |
| Form without dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · Qt | 6/6 (61–100 %) | 60 (48–452) | 11 (7–58) | – | 6/6 | – | 40 (39–43) |
| Form without dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 143 (110–192) | 50 (38–70) | – | 3/6 | – | 88 (46–131) |
| Form without dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · Qt | 6/6 (61–100 %) | 58 (49–61) | 7 (6–8) | – | 6/6 | – | 40 (39–41) |
| Move a shape in Inkscape | gpt-6.2-sol | hyprmasq | Hyprland container | 6/6 (61–100 %) | 25 (24–28) | 4 (4–4) | – | 6/6 | – | 5 (4–6) |
| Move a shape in Inkscape | gpt-6.2-sol | Cua Driver | Hyprland container | 6/6 (61–100 %) | 42 (34–46) | 7 (6–8) | – | 4/6 | – | 6 (6–8) |
| Move a shape in Inkscape | gpt-6.2-sol | hypruse | Hyprland container | 6/6 (61–100 %) | 35 (26–41) | 2 (2–3) | – | 5/6 | – | 8 (6–10) |
| Move a shape in Inkscape | Opus 5.5 | hyprmasq | Hyprland container | 6/6 (61–100 %) | 23 (19–27) | 6 (5–7) | – | 6/6 | – | 7 (4–8) |
| Move a shape in Inkscape | Opus 5.5 | Cua Driver | Hyprland container | 6/6 (61–100 %) | 30 (22–39) | 6 (5–12) | – | 6/6 | – | 7 (6–8) |
| Move a shape in Inkscape | Opus 5.5 | hypruse | Hyprland container | 6/6 (61–100 %) | 41 (23–138) | 4 (2–17) | – | 3/6 | – | 12 (6–41) |
| Move a shape in Inkscape | gpt-6.2-sol | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 46 (37–73) | 13 (10–20) | – | 6/6 | – | 9 (8–9) |
| Move a shape in Inkscape | Opus 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 49 (36–81) | 21 (11–31) | – | 4/6 | – | 10 (8–19) |
| Rename and move a file | gpt-6.2-sol | hyprmasq | Hyprland container | 6/6 (61–100 %) | 29 (27–32) | 6 (5–6) | – | 6/6 | – | 7 (6–7) |
| Rename and move a file | gpt-6.2-sol | Cua Driver | Hyprland container | 6/6 (61–100 %) | 40 (35–41) | 4 (2–8) | – | 3/6 | – | 8 (6–8) |
| Rename and move a file | gpt-6.2-sol | hypruse | Hyprland container | 6/6 (61–100 %) | 63 (56–76) | 7 (6–9) | – | 0/6 | – | 17 (15–25) |
| Rename and move a file | Opus 5.5 | hyprmasq | Hyprland container | 6/6 (61–100 %) | 23 (22–24) | 7 (6–7) | – | 6/6 | – | 7 (7–8) |
| Rename and move a file | Opus 5.5 | Cua Driver | Hyprland container | 6/6 (61–100 %) | 38 (30–47) | 6 (4–6) | – | 5/6 | – | 11 (8–14) |
| Rename and move a file | Opus 5.5 | hypruse | Hyprland container | 6/6 (61–100 %) | 57 (42–65) | 6 (5–7) | – | 0/6 | – | 16 (11–20) |
| Rename and move a file | gpt-6.2-sol | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 97 (72–112) | 35 (20–43) | – | 2/6 | – | 18 (12–23) |
| Rename and move a file | Opus 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 34 (34–57) | 9 (9–14) | – | 1/6 | – | 12 (10–19) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | hyprmasq | Hyprland container | 6/6 (61–100 %) | 82 (65–111) | 17 (17–19) | – | 6/6 | 6/6 | 17 (15–18) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | Cua Driver | Hyprland container | 5/6 (44–97 %) | 168 (137–240) | 21 (17–33) | 451 | 0/6 | 5/6 | 32 (27–42) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | hypruse | Hyprland container | 6/6 (61–100 %) | 181 (171–205) | 18 (16–20) | – | 0/6 | 2/6 | 63 (49–73) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | hyprmasq | Hyprland container | 6/6 (61–100 %) | 52 (48–56) | 22 (16–24) | – | 6/6 | 6/6 | 14 (13–15) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | Cua Driver | Hyprland container | 6/6 (61–100 %) | 130 (84–155) | 22 (16–24) | – | 0/6 | 3/6 | 38 (28–44) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | hypruse | Hyprland container | 6/6 (61–100 %) | 135 (113–167) | 27 (21–43) | – | 0/6 | 4/6 | 44 (40–59) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 303 (248–436) | 124 (100–195) | – | 0/6 | 3/6 | 76 (51–114) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 166 (128–210) | 57 (48–90) | – | 0/6 | 2/6 | 58 (56–72) |
| Add a mail account, answer a mail | gpt-6.2-sol | hyprmasq | Hyprland container | 6/6 (61–100 %) | 92 (78–122) | 25 (21–26) | – | 6/6 | – | 20 (18–25) |
| Add a mail account, answer a mail | gpt-6.2-sol | Cua Driver | Hyprland container | 6/6 (61–100 %) | 184 (119–469) | 20 (14–40) | – | 0/6 | – | 47 (32–59) |
| Add a mail account, answer a mail | gpt-6.2-sol | hypruse | Hyprland container | 6/6 (61–100 %) | 140 (110–172) | 24 (18–30) | – | 0/6 | – | 54 (29–72) |
| Add a mail account, answer a mail | Opus 5.5 | hyprmasq | Hyprland container | 6/6 (61–100 %) | 82 (72–101) | 35 (30–41) | – | 6/6 | – | 20 (19–26) |
| Add a mail account, answer a mail | Opus 5.5 | Cua Driver | Hyprland container | 6/6 (61–100 %) | 414 (276–501) | 142 (42–202) | – | 0/6 | – | 74 (62–101) |
| Add a mail account, answer a mail | Opus 5.5 | hypruse | Hyprland container | 6/6 (61–100 %) | 121 (107–128) | 25 (17–28) | – | 0/6 | – | 30 (24–38) |
| Add a mail account, answer a mail | gpt-6.2-sol | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 228 (206–307) | 70 (63–109) | – | 0/6 | – | 72 (55–86) |
| Add a mail account, answer a mail | Opus 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 138 (129–169) | 40 (35–43) | – | 0/6 | – | 48 (44–52) |
| Label a photo, drag and drop | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 6/6 (61–100 %) | 46 (42–57) | 12 (12–12) | – | 6/6 | – | 5 (5–6) |
| Label a photo, drag and drop | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 44 (41–57) | 12 (12–12) | – | 6/6 | – | 5 (3–6) |
| Label a photo, drag and drop | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 6/6 (61–100 %) | 80 (73–94) | 6 (5–7) | – | 0/6 | – | 14 (10–19) |
| Label a photo, drag and drop | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 0/6 (0–39 %) | – | – | 329 (293–501) | 0/6 | – | 46 (43–60) |
| Label a photo, drag and drop | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 49 (45–52) | 7 (7–7) | – | 0/6 | – | 9 (8–10) |
| Label a photo, drag and drop | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 4/6 (30–90 %) | 137 (125–197) | 18 (15–34) | 167 (158–175) | 0/6 | – | 23 (16–31) |
| Label a photo, drag and drop | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 5/6 (44–97 %) | 48 (40–65) | 13 (12–18) | 41 | 6/6 | – | 7 (5–10) |
| Label a photo, drag and drop | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 45 (38–57) | 13 (13–15) | – | 6/6 | – | 6 (5–8) |
| Label a photo, drag and drop | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 6/6 (61–100 %) | 78 (74–94) | 13 (6–14) | – | 0/6 | – | 18 (16–22) |
| Label a photo, drag and drop | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 0/6 (0–39 %) | – | – | 158 (117–185) | 6/6 | – | 32 (28–39) |
| Label a photo, drag and drop | Opus 5.5 | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 48 (42–57) | 7 (6–8) | – | 0/6 | – | 8 (6–11) |
| Label a photo, drag and drop | Opus 5.5 | hypruse | Hyprland container · Firefox | 0/6 (0–39 %) | – | – | 97 (75–125) | 0/6 | – | 18 (13–26) |
| Label a photo, drag and drop | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 83 (64–100) | 37 (22–40) | – | 0/6 | – | 16 (14–24) |
| Label a photo, drag and drop | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 71 (62–83) | 22 (22–29) | – | 0/6 | – | 20 (14–24) |
| Label a photo, drag and drop | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 53 (49–61) | 17 (16–19) | – | 0/6 | – | 14 (13–14) |
| Label a photo, drag and drop | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 59 (50–70) | 18 (17–20) | – | 0/6 | – | 14 (13–15) |
| Label a photo, custom drag | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 6/6 (61–100 %) | 45 (38–51) | 12 (11–13) | – | 6/6 | – | 5 (3–6) |
| Label a photo, custom drag | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 44 (38–59) | 12 (12–13) | – | 6/6 | – | 5 (4–5) |
| Label a photo, custom drag | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 5/6 (44–97 %) | 59 (40–76) | 6 (5–7) | 76 | 0/6 | – | 9 (6–13) |
| Label a photo, custom drag | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 6/6 (61–100 %) | 79 (64–86) | 7 (4–12) | – | 5/6 | – | 12 (9–22) |
| Label a photo, custom drag | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 47 (45–53) | 7 (6–7) | – | 0/6 | – | 9 (9–9) |
| Label a photo, custom drag | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 6/6 (61–100 %) | 54 (51–62) | 7 (6–8) | – | 0/6 | – | 10 (8–13) |
| Label a photo, custom drag | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 5/6 (44–97 %) | 42 (35–56) | 13 (13–15) | 42 | 6/6 | – | 6 (5–9) |
| Label a photo, custom drag | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 44 (37–66) | 14 (13–17) | – | 6/6 | – | 6 (5–10) |
| Label a photo, custom drag | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 6/6 (61–100 %) | 67 (56–74) | 11 (8–14) | – | 0/6 | – | 14 (11–14) |
| Label a photo, custom drag | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 6/6 (61–100 %) | 103 (74–136) | 21 (13–29) | – | 6/6 | – | 18 (15–33) |
| Label a photo, custom drag | Opus 5.5 | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 53 (40–62) | 7 (7–8) | – | 0/6 | – | 8 (7–12) |
| Label a photo, custom drag | Opus 5.5 | hypruse | Hyprland container · Firefox | 6/6 (61–100 %) | 54 (47–68) | 7 (7–8) | – | 0/6 | – | 10 (9–13) |
| Label a photo, custom drag | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 74 (70–107) | 23 (20–45) | – | 0/6 | – | 16 (15–18) |
| Label a photo, custom drag | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 68 (60–93) | 23 (21–27) | – | 0/6 | – | 16 (14–17) |
| Label a photo, custom drag | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 53 (44–59) | 18 (16–20) | – | 0/6 | – | 14 (13–15) |
| Label a photo, custom drag | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 58 (51–73) | 18 (17–18) | – | 0/6 | – | 14 (13–16) |
| Copy reports into a sheet | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 6/6 (61–100 %) | 68 (65–78) | 16 (15–18) | – | 6/6 | – | 16 (15–18) |
| Copy reports into a sheet | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 72 (66–76) | 16 (15–17) | – | 6/6 | – | 17 (15–18) |
| Copy reports into a sheet | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 6/6 (61–100 %) | 112 (93–141) | 17 (15–18) | – | 6/6 | – | 22 (18–26) |
| Copy reports into a sheet | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 6/6 (61–100 %) | 139 (124–192) | 20 (18–25) | – | 6/6 | – | 27 (23–41) |
| Copy reports into a sheet | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 99 (91–169) | 12 (11–13) | – | 0/6 | – | 34 (27–78) |
| Copy reports into a sheet | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 6/6 (61–100 %) | 105 (88–141) | 14 (10–17) | – | 0/6 | – | 29 (22–43) |
| Copy reports into a sheet | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 6/6 (61–100 %) | 68 (56–71) | 18 (16–20) | – | 6/6 | – | 20 (18–23) |
| Copy reports into a sheet | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 66 (65–70) | 19 (17–20) | – | 6/6 | – | 20 (19–21) |
| Copy reports into a sheet | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 6/6 (61–100 %) | 111 (83–140) | 17 (14–37) | – | 4/6 | – | 34 (23–42) |
| Copy reports into a sheet | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 6/6 (61–100 %) | 118 (67–150) | 20 (12–27) | – | 3/6 | – | 31 (18–35) |
| Copy reports into a sheet | Opus 5.5 | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 87 (80–116) | 11 (9–12) | – | 0/6 | – | 24 (21–37) |
| Copy reports into a sheet | Opus 5.5 | hypruse | Hyprland container · Firefox | 6/6 (61–100 %) | 88 (77–100) | 12 (10–15) | – | 0/6 | – | 24 (19–24) |
| Copy reports into a sheet | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 206 (156–270) | 81 (40–113) | – | 4/6 | – | 52 (48–69) |
| Copy reports into a sheet | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 165 (133–210) | 43 (29–88) | – | 4/6 | – | 52 (40–103) |
| Copy reports into a sheet | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 125 (109–135) | 42 (32–48) | – | 3/6 | – | 38 (36–55) |
| Copy reports into a sheet | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 92 (72–100) | 26 (17–28) | – | 4/6 | – | 28 (22–32) |
| Draw along a river bank | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 6/6 (61–100 %) | 48 (31–49) | 3 (2–3) | – | 6/6 | – | 6 (5–8) |
| Draw along a river bank | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 58 (40–76) | 4 (3–8) | – | 6/6 | – | 7 (5–9) |
| Draw along a river bank | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 6/6 (61–100 %) | 168 (146–220) | 6 (5–13) | – | 0/6 | – | 20 (16–27) |
| Draw along a river bank | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 6/6 (61–100 %) | 209 (129–253) | 9 (7–11) | – | 6/6 | – | 22 (16–29) |
| Draw along a river bank | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 127 (82–162) | 21 (19–24) | – | 0/6 | – | 12 (10–23) |
| Draw along a river bank | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 6/6 (61–100 %) | 145 (82–179) | 22 (14–29) | – | 0/6 | – | 16 (10–33) |
| Draw along a river bank | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 6/6 (61–100 %) | 27 (19–45) | 2 (2–6) | – | 6/6 | – | 5 (3–7) |
| Draw along a river bank | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 6/6 (61–100 %) | 32 (23–39) | 3 (3–5) | – | 6/6 | – | 5 (4–6) |
| Draw along a river bank | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 5/6 (44–97 %) | 105 (62–111) | 7 (5–9) | 110 | 0/6 | – | 18 (13–20) |
| Draw along a river bank | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 5/6 (44–97 %) | 124 (76–172) | 7 (5–36) | 124 | 6/6 | – | 23 (17–39) |
| Draw along a river bank | Opus 5.5 | hypruse | Hyprland container · Chromium | 6/6 (61–100 %) | 65 (43–74) | 12 (10–14) | – | 0/6 | – | 10 (7–11) |
| Draw along a river bank | Opus 5.5 | hypruse | Hyprland container · Firefox | 3/6 (19–81 %) | 54 (44–59) | 12 (10–13) | 54 (53–86) | 0/6 | – | 9 (7–10) |
| Draw along a river bank | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 155 (120–216) | 40 (21–58) | – | 0/6 | – | 30 (18–63) |
| Draw along a river bank | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 164 (125–1755) | 42 (30–48) | – | 0/6 | – | 34 (28–39) |
| Draw along a river bank | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 3/6 (19–81 %) | 92 (81–100) | 28 (22–28) | 78 (66–118) | 0/6 | – | 22 (15–29) |
| Draw along a river bank | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 1/6 (3–56 %) | – | – | 93 (69–118) | 0/6 | – | 20 (14–29) |

**Tokens and cost**

| Task | Agent | Tool | Where | Cost $ |
| --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · GTK 4 | 0.10 (0.08–0.21) |
| Form with dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · Qt | 0.08 (0.06–0.11) |
| Form with dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · GTK 4 | 0.33 (0.22–0.39) |
| Form with dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · Qt | 0.16 (0.10–0.28) |
| Form with dropdowns | gpt-6.2-sol | hypruse | Hyprland container · GTK 4 | 0.22 (0.18–0.26) |
| Form with dropdowns | gpt-6.2-sol | hypruse | Hyprland container · Qt | 0.25 (0.22–0.27) |
| Form with dropdowns | Opus 5.5 | hyprmasq | Hyprland container · GTK 4 | 0.42 (0.39–0.45) |
| Form with dropdowns | Opus 5.5 | hyprmasq | Hyprland container · Qt | 0.42 (0.34–0.52) |
| Form with dropdowns | Opus 5.5 | Cua Driver | Hyprland container · GTK 4 | 2.04 (1.66–3.69) |
| Form with dropdowns | Opus 5.5 | Cua Driver | Hyprland container · Qt | 1.71 (1.31–2.05) |
| Form with dropdowns | Opus 5.5 | hypruse | Hyprland container · GTK 4 | 1.25 (1.03–1.60) |
| Form with dropdowns | Opus 5.5 | hypruse | Hyprland container · Qt | 1.43 (1.09–2.05) |
| Form with dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · GTK 4 | 0.30 (0.28–0.35) |
| Form with dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · Qt | 0.21 (0.19–0.25) |
| Form with dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 3.64 (1.67–5.28) |
| Form with dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · Qt | 2.16 (1.25–3.32) |
| Form without dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · GTK 4 | 0.12 (0.08–0.15) |
| Form without dropdowns | gpt-6.2-sol | hyprmasq | Hyprland container · Qt | 0.08 (0.05–0.14) |
| Form without dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · GTK 4 | 0.17 (0.15–0.21) |
| Form without dropdowns | gpt-6.2-sol | Cua Driver | Hyprland container · Qt | 0.11 (0.09–0.15) |
| Form without dropdowns | gpt-6.2-sol | hypruse | Hyprland container · GTK 4 | 0.18 (0.14–0.22) |
| Form without dropdowns | gpt-6.2-sol | hypruse | Hyprland container · Qt | 0.21 (0.15–0.31) |
| Form without dropdowns | Opus 5.5 | hyprmasq | Hyprland container · GTK 4 | 0.36 (0.32–0.44) |
| Form without dropdowns | Opus 5.5 | hyprmasq | Hyprland container · Qt | 0.30 (0.28–0.31) |
| Form without dropdowns | Opus 5.5 | Cua Driver | Hyprland container · GTK 4 | 1.53 (1.44–1.96) |
| Form without dropdowns | Opus 5.5 | Cua Driver | Hyprland container · Qt | 0.45 (0.37–0.79) |
| Form without dropdowns | Opus 5.5 | hypruse | Hyprland container · GTK 4 | 1.26 (1.04–2.02) |
| Form without dropdowns | Opus 5.5 | hypruse | Hyprland container · Qt | 1.13 (1.03–1.41) |
| Form without dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · GTK 4 | 0.20 (0.18–0.29) |
| Form without dropdowns | gpt-6.2-sol | computer-use-linux | Hyprland VM · Qt | 0.15 (0.12–0.24) |
| Form without dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 1.96 (1.47–2.80) |
| Form without dropdowns | Opus 5.5 | computer-use-linux | Hyprland VM · Qt | 1.03 (0.89–1.11) |
| Move a shape in Inkscape | gpt-6.2-sol | hyprmasq | Hyprland container | 0.04 (0.03–0.11) |
| Move a shape in Inkscape | gpt-6.2-sol | Cua Driver | Hyprland container | 0.09 (0.07–0.15) |
| Move a shape in Inkscape | gpt-6.2-sol | hypruse | Hyprland container | 0.09 (0.06–0.12) |
| Move a shape in Inkscape | Opus 5.5 | hyprmasq | Hyprland container | 0.13 (0.09–0.13) |
| Move a shape in Inkscape | Opus 5.5 | Cua Driver | Hyprland container | 0.56 (0.39–0.64) |
| Move a shape in Inkscape | Opus 5.5 | hypruse | Hyprland container | 0.29 (0.16–1.17) |
| Move a shape in Inkscape | gpt-6.2-sol | computer-use-linux | Hyprland VM | 0.10 (0.08–0.16) |
| Move a shape in Inkscape | Opus 5.5 | computer-use-linux | Hyprland VM | 0.59 (0.43–1.15) |
| Rename and move a file | gpt-6.2-sol | hyprmasq | Hyprland container | 0.05 (0.04–0.07) |
| Rename and move a file | gpt-6.2-sol | Cua Driver | Hyprland container | 0.11 (0.08–0.16) |
| Rename and move a file | gpt-6.2-sol | hypruse | Hyprland container | 0.14 (0.10–0.20) |
| Rename and move a file | Opus 5.5 | hyprmasq | Hyprland container | 0.12 (0.10–0.12) |
| Rename and move a file | Opus 5.5 | Cua Driver | Hyprland container | 0.51 (0.39–0.60) |
| Rename and move a file | Opus 5.5 | hypruse | Hyprland container | 0.42 (0.29–0.52) |
| Rename and move a file | gpt-6.2-sol | computer-use-linux | Hyprland VM | 0.18 (0.14–0.29) |
| Rename and move a file | Opus 5.5 | computer-use-linux | Hyprland VM | 0.56 (0.45–0.79) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | hyprmasq | Hyprland container | 0.14 (0.12–0.20) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | Cua Driver | Hyprland container | 0.36 (0.27–0.45) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | hypruse | Hyprland container | 0.37 (0.25–0.49) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | hyprmasq | Hyprland container | 0.23 (0.22–0.24) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | Cua Driver | Hyprland container | 1.60 (1.22–2.33) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | hypruse | Hyprland container | 1.01 (0.78–1.59) |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | computer-use-linux | Hyprland VM | 0.47 (0.34–0.59) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | computer-use-linux | Hyprland VM | 2.45 (1.83–4.03) |
| Add a mail account, answer a mail | gpt-6.2-sol | hyprmasq | Hyprland container | 0.15 (0.10–0.21) |
| Add a mail account, answer a mail | gpt-6.2-sol | Cua Driver | Hyprland container | 0.50 (0.31–0.54) |
| Add a mail account, answer a mail | gpt-6.2-sol | hypruse | Hyprland container | 0.30 (0.24–0.38) |
| Add a mail account, answer a mail | Opus 5.5 | hyprmasq | Hyprland container | 0.39 (0.36–0.52) |
| Add a mail account, answer a mail | Opus 5.5 | Cua Driver | Hyprland container | 3.99 (3.25–5.22) |
| Add a mail account, answer a mail | Opus 5.5 | hypruse | Hyprland container | 0.93 (0.86–1.08) |
| Add a mail account, answer a mail | gpt-6.2-sol | computer-use-linux | Hyprland VM | 0.44 (0.36–0.55) |
| Add a mail account, answer a mail | Opus 5.5 | computer-use-linux | Hyprland VM | 2.56 (2.39–3.29) |
| Label a photo, drag and drop | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 0.06 (0.04–0.10) |
| Label a photo, drag and drop | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 0.06 (0.03–0.12) |
| Label a photo, drag and drop | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 0.16 (0.12–0.16) |
| Label a photo, drag and drop | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 0.48 (0.38–0.68) |
| Label a photo, drag and drop | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 0.08 (0.07–0.11) |
| Label a photo, drag and drop | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 0.25 (0.17–0.34) |
| Label a photo, drag and drop | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 0.16 (0.14–0.23) |
| Label a photo, drag and drop | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 0.15 (0.12–0.20) |
| Label a photo, drag and drop | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 0.62 (0.56–0.75) |
| Label a photo, drag and drop | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 1.31 (1.04–1.48) |
| Label a photo, drag and drop | Opus 5.5 | hypruse | Hyprland container · Chromium | 0.24 (0.18–0.27) |
| Label a photo, drag and drop | Opus 5.5 | hypruse | Hyprland container · Firefox | 0.60 (0.44–0.86) |
| Label a photo, drag and drop | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 0.13 (0.11–0.24) |
| Label a photo, drag and drop | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 0.16 (0.12–0.19) |
| Label a photo, drag and drop | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.50 (0.48–0.55) |
| Label a photo, drag and drop | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.58 (0.51–0.64) |
| Label a photo, custom drag | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 0.05 (0.05–0.07) |
| Label a photo, custom drag | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 0.06 (0.04–0.09) |
| Label a photo, custom drag | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 0.10 (0.07–0.14) |
| Label a photo, custom drag | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 0.16 (0.10–0.20) |
| Label a photo, custom drag | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 0.12 (0.07–0.15) |
| Label a photo, custom drag | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 0.11 (0.09–0.13) |
| Label a photo, custom drag | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 0.14 (0.12–0.21) |
| Label a photo, custom drag | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 0.15 (0.13–0.24) |
| Label a photo, custom drag | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 0.53 (0.46–0.60) |
| Label a photo, custom drag | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 0.76 (0.56–1.47) |
| Label a photo, custom drag | Opus 5.5 | hypruse | Hyprland container · Chromium | 0.26 (0.18–0.30) |
| Label a photo, custom drag | Opus 5.5 | hypruse | Hyprland container · Firefox | 0.27 (0.24–0.31) |
| Label a photo, custom drag | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 0.15 (0.13–0.20) |
| Label a photo, custom drag | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 0.13 (0.12–0.14) |
| Label a photo, custom drag | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.49 (0.41–0.54) |
| Label a photo, custom drag | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.56 (0.50–0.62) |
| Copy reports into a sheet | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 0.11 (0.09–0.17) |
| Copy reports into a sheet | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 0.14 (0.10–0.16) |
| Copy reports into a sheet | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 0.23 (0.20–0.29) |
| Copy reports into a sheet | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 0.34 (0.26–0.52) |
| Copy reports into a sheet | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 0.19 (0.16–0.21) |
| Copy reports into a sheet | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 0.22 (0.17–0.28) |
| Copy reports into a sheet | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 0.38 (0.32–0.39) |
| Copy reports into a sheet | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 0.38 (0.37–0.40) |
| Copy reports into a sheet | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 1.51 (1.05–1.88) |
| Copy reports into a sheet | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 1.63 (0.90–1.95) |
| Copy reports into a sheet | Opus 5.5 | hypruse | Hyprland container · Chromium | 0.77 (0.74–1.00) |
| Copy reports into a sheet | Opus 5.5 | hypruse | Hyprland container · Firefox | 0.75 (0.74–0.79) |
| Copy reports into a sheet | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 0.25 (0.21–0.31) |
| Copy reports into a sheet | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 0.28 (0.18–0.33) |
| Copy reports into a sheet | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 2.34 (1.91–2.67) |
| Copy reports into a sheet | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 2.19 (1.55–2.35) |
| Draw along a river bank | gpt-6.2-sol | hyprmasq | Hyprland container · Chromium | 0.07 (0.05–0.12) |
| Draw along a river bank | gpt-6.2-sol | hyprmasq | Hyprland container · Firefox | 0.07 (0.06–0.11) |
| Draw along a river bank | gpt-6.2-sol | Cua Driver | Hyprland container · Chromium | 0.27 (0.21–0.34) |
| Draw along a river bank | gpt-6.2-sol | Cua Driver | Hyprland container · Firefox | 0.36 (0.27–0.40) |
| Draw along a river bank | gpt-6.2-sol | hypruse | Hyprland container · Chromium | 0.14 (0.11–0.26) |
| Draw along a river bank | gpt-6.2-sol | hypruse | Hyprland container · Firefox | 0.14 (0.10–0.20) |
| Draw along a river bank | Opus 5.5 | hyprmasq | Hyprland container · Chromium | 0.12 (0.08–0.19) |
| Draw along a river bank | Opus 5.5 | hyprmasq | Hyprland container · Firefox | 0.13 (0.11–0.16) |
| Draw along a river bank | Opus 5.5 | Cua Driver | Hyprland container · Chromium | 0.72 (0.50–0.73) |
| Draw along a river bank | Opus 5.5 | Cua Driver | Hyprland container · Firefox | 0.90 (0.64–1.36) |
| Draw along a river bank | Opus 5.5 | hypruse | Hyprland container · Chromium | 0.26 (0.19–0.29) |
| Draw along a river bank | Opus 5.5 | hypruse | Hyprland container · Firefox | 0.23 (0.19–0.33) |
| Draw along a river bank | gpt-6.2-sol | computer-use-linux | Hyprland VM · Chromium | 0.19 (0.18–0.33) |
| Draw along a river bank | gpt-6.2-sol | computer-use-linux | Hyprland VM · Firefox | 0.24 (0.18–0.29) |
| Draw along a river bank | Opus 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.75 (0.55–0.94) |
| Draw along a river bank | Opus 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.85 (0.71–1.05) |

</details>
<!-- /render -->

## Side checks

Cua Driver with the user's Chromium started with `--remote-debugging-port=0`, on the 5 Chromium units where attaching
applies (never pooled with the primary runs).

<!-- render v3-cua,v3-cua-chromium,v3-cua-attach attach -->
<details><summary>Numbers: Cua Driver with and without the debugging port</summary>

| Task | Agent | As documented: passed | Time s, passed | Debugging port: passed | Time s, passed |
| --- | --- | --- | --- | --- | --- |
| Upload to Forgejo, connect a dashboard | gpt-6.2-sol | 5/6 (44–97 %) | 168 (137–240) | 6/6 (61–100 %) | 214 (144–250) |
| Upload to Forgejo, connect a dashboard | Opus 5.5 | 6/6 (61–100 %) | 130 (84–155) | 6/6 (61–100 %) | 100 (91–117) |
| Label a photo, drag and drop · Chromium | gpt-6.2-sol | 6/6 (61–100 %) | 80 (73–94) | 6/6 (61–100 %) | 81 (59–110) |
| Label a photo, drag and drop · Chromium | Opus 5.5 | 6/6 (61–100 %) | 78 (74–94) | 6/6 (61–100 %) | 70 (56–80) |
| Label a photo, custom drag · Chromium | gpt-6.2-sol | 5/6 (44–97 %) | 59 (40–76) | 6/6 (61–100 %) | 57 (50–76) |
| Label a photo, custom drag · Chromium | Opus 5.5 | 6/6 (61–100 %) | 67 (56–74) | 6/6 (61–100 %) | 67 (58–74) |
| Copy reports into a sheet · Chromium | gpt-6.2-sol | 6/6 (61–100 %) | 112 (93–141) | 6/6 (61–100 %) | 109 (100–114) |
| Copy reports into a sheet · Chromium | Opus 5.5 | 6/6 (61–100 %) | 111 (83–140) | 6/6 (61–100 %) | 107 (80–134) |
| Draw along a river bank · Chromium | gpt-6.2-sol | 6/6 (61–100 %) | 168 (146–220) | 6/6 (61–100 %) | 133 (103–241) |
| Draw along a river bank · Chromium | Opus 5.5 | 5/6 (44–97 %) | 105 (62–111) | 6/6 (61–100 %) | 117 (92–261) |

</details>
<!-- /render -->

One full round of hyprmasq (32 runs across all 16 units and both agents, `n = 1` per unit/agent) in the exact same
3 × 2-core KVM VM setup as computer-use-linux, measuring VM vs GPU-container overhead directly (never pooled with the
primary runs). Because `n = 1` per cell, single-unit ratios span 0.68–2.60× (Forgejo Sol had a 143 s API stall), so the
pooled VM medians sit slightly below the container medians while the per-unit geometric means are 1.03× / 1.07×.

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-hyprmasq-vm,v3-hyprmasq-vm-chromium vm-overhead -->
<details><summary>Numbers: hyprmasq in GPU containers vs 3×2-core KVM VMs on the same host</summary>

| Agent | VM passed | Container wall med | KVM VM wall med | VM / container wall | Container tool med | KVM VM tool med | VM / container tool |
| --- | --- | --- | --- | --- | --- | --- | --- |
| gpt-6.2-sol | 16/16 | 59.0 s | 57.0 s | 1.03× | 13.7 s | 14.3 s | 1.06× |
| Opus 5.5 | 16/16 | 52.0 s | 50.5 s | 1.07× | 17.2 s | 15.6 s | 1.11× |

</details>
<!-- /render -->

All 384 Claude Opus 5.5 runs repeated with Claude Code's built-in `ToolSearch` enabled (`hyprmasq`, Cua Driver and
`hypruse` in 4 GPU containers on the Ryzen 7 5800X3D host, and `computer-use-linux` in 2 × 2-core KVM VMs on a Ryzen 5
5600X host), shown beside the eager-schema Opus 5.5 runs.

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium,v3-ts-hyprmasq,v3-ts-hyprmasq-chromium,v3-ts-cua,v3-ts-cua-chromium,v3-ts-hypruse,v3-ts-hypruse-chromium,v3-ts-computer-use-linux,v3-ts-computer-use-linux-chromium toolsearch-table -->
<details><summary>Numbers: Claude Opus 5.5 with eager schemas vs ToolSearch</summary>

| Tool | Mode | Passed (95 % interval) | No view change, passed | First req k | Peak ctx k | Requests | Med cost | Total cost | Wall s, passed |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq | Eager schemas | 94/96 (93–99 %) | 94/94 | 8.5 | 24.7 | 10.5 | $0.23 | $24.1 | 52.0 s |
| hyprmasq | ToolSearch | 94/96 (93–99 %) | 94/94 | 3.6 | 26.4 | 13.0 | $0.28 | $28.1 | 59.5 s |
| Cua Driver | Eager schemas | 88/96 (84–96 %) | 45/88 | 61.7 | 110.8 | 24.0 | $1.06 | $125.5 | 104.7 s |
| Cua Driver | ToolSearch | 78/96 (72–88 %) | 38/78 | 9.7 | 66.5 | 22.0 | $0.83 | $102.5 | 96.8 s |
| hypruse | Eager schemas | 87/96 (83–95 %) | 3/87 | 12.5 | 57.9 | 33.0 | $0.75 | $70.3 | 89.0 s |
| hypruse | ToolSearch | 85/96 (81–93 %) | 14/85 | 5.8 | 52.1 | 32.0 | $0.80 | $89.5 | 103.4 s |
| computer-use-linux | Eager schemas | 87/96 (83–95 %) | 27/87 | 44.6 | 118.6 | 13.5 | $0.99 | $141.7 | 81.2 s |
| computer-use-linux | ToolSearch | 85/96 (81–93 %) | 27/85 | 15.0 | 112.3 | 16.0 | $1.12 | $149.7 | 80.1 s |

</details>
<!-- /render -->

## Accessibility walks and frames

In container mode on the host GPU, Cua Driver's accessibility walks run well below its 1,000 ms budget (median
176–182 ms, 0 first walks cut, 36 of 2,502 walks cut by a time budget). The frames table holds flags, not failures: a
first screenshot under half the encoded size of the later ones, as an undrawn window is mostly blank or a page filled
in later, and screenshots unchanged after input. For hypruse, the `/6` denominator covers Codex's 6 runs per unit only,
because Claude Code runs in `HYPRUSE_SCREENSHOT_MODE=file` (reading screenshots via `Read` rather than inline MCP
images).

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium walks -->
<details><summary>Numbers: Cua Driver's accessibility walks</summary>

| Tool | Agent | Walks | Cut by time | Cut at elements | Cut at containers | Median ms | Runs whose first walk was cut | Runs that named a budget | Runs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cua Driver | gpt-6.2-sol | 1131 | 22 | 45 | 81 | 176 | 0 | 5 | 96 |
| Cua Driver | Opus 5.5 | 1371 | 14 | 24 | 64 | 182 | 0 | 16 | 96 |

</details>
<!-- /render -->

<!-- render v3-hyprmasq,v3-hyprmasq-chromium,v3-cua,v3-cua-chromium,v3-hypruse,v3-hypruse-chromium,v3-computer-use-linux,v3-computer-use-linux-chromium frames -->
<details><summary>Numbers: first frames and unchanged frames, per task</summary>

| Task | hyprmasq: small first frame, unchanged frames | Cua Driver: small first frame, unchanged frames | hypruse: small first frame, unchanged frames | computer-use-linux: small first frame, unchanged frames |
| --- | --- | --- | --- | --- |
| Upload to Forgejo, connect a dashboard | 0/12, 0 | 9/12, 0 | 0/6, 4 | 9/12, 17 |
| Rename and move a file | 0/12, 0 | 0/12, 0 | 0/6, 0 | 2/12, 1 |
| Form with dropdowns · GTK 4 | 0/12, 0 | 0/12, 6 | 0/6, 0 | 0/12, 0 |
| Form without dropdowns · GTK 4 | 0/12, 0 | 0/12, 0 | 0/6, 0 | 0/12, 0 |
| Form with dropdowns · Qt | 0/12, 0 | 0/12, 30 | 0/6, 3 | 0/12, 2 |
| Form without dropdowns · Qt | 0/12, 0 | 0/12, 15 | 0/6, 5 | 0/12, 0 |
| Move a shape in Inkscape | 0/12, 0 | 0/12, 0 | 0/6, 0 | 5/12, 1 |
| Add a mail account, answer a mail | 0/12, 0 | 5/12, 14 | 3/6, 0 | 3/12, 2 |
| Label a photo, drag and drop · Firefox | 0/12, 0 | 0/12, 25 | 0/6, 0 | 0/12, 0 |
| Label a photo, custom drag · Firefox | 0/12, 0 | 0/12, 14 | 0/6, 0 | 0/12, 0 |
| Copy reports into a sheet · Firefox | 0/12, 0 | 0/12, 15 | 0/6, 0 | 2/12, 3 |
| Draw along a river bank · Firefox | 0/12, 0 | 0/12, 16 | 0/6, 0 | 2/12, 0 |
| Label a photo, drag and drop · Chromium | 0/12, 0 | 0/12, 0 | 0/6, 0 | 0/12, 0 |
| Label a photo, custom drag · Chromium | 0/12, 0 | 0/12, 0 | 0/6, 0 | 0/12, 0 |
| Copy reports into a sheet · Chromium | 0/12, 0 | 0/12, 13 | 0/6, 1 | 2/12, 3 |
| Draw along a river bank · Chromium | 0/12, 0 | 0/12, 1 | 0/6, 0 | 5/12, 0 |

</details>
<!-- /render -->
