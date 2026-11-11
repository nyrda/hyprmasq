# The comparison's numbers

Every table behind [the comparison](comparison.md), generated from [comparison/data](comparison/data). Each is folded.

## Runs passed

A tool's pass rate with its 95 % interval, per agent and kind of task. The mean of both agents is never a pool.

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium,v2-failures headline -->
<details><summary>Numbers: pass rates with their intervals, per agent and kind of task</summary>

| Tool | Agent | All 18 units | Apps | Image tasks | Firefox | Chromium | Failed because of the tool |
| --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq | gpt-6.1-terra | 92/108 (77–91 %) | 60/60 (94–100 %) | 32/48 (53–78 %) | 19/30 (46–78 %) | 25/30 (66–93 %) | 0 of 108 |
| hyprmasq | Sonnet 5.5 | 95/108 (80–93 %) | 58/60 (89–99 %) | 37/48 (63–87 %) | 24/30 (63–90 %) | 25/30 (66–93 %) | 0 of 108 |
| hyprmasq | Mean of both | 87 % | 98 % | 72 % | 72 % | 83 % | – |
| Cua Driver | gpt-6.1-terra | 73/108 (58–76 %) | 52/60 (76–93 %) | 21/48 (31–58 %) | 13/30 (27–61 %) | 18/30 (42–75 %) | 6 of 108 |
| Cua Driver | Sonnet 5.5 | 68/108 (54–71 %) | 49/60 (70–89 %) | 19/48 (27–54 %) | 11/30 (22–54 %) | 20/30 (49–81 %) | 7 of 108 |
| Cua Driver | Mean of both | 65 % | 84 % | 42 % | 40 % | 63 % | – |
| hypruse | gpt-6.1-terra | 89/108 (74–88 %) | 51/60 (74–92 %) | 38/48 (66–88 %) | 20/30 (49–81 %) | 26/30 (70–95 %) | 7 of 108 |
| hypruse | Sonnet 5.5 | 90/108 (75–89 %) | 59/60 (91–100 %) | 31/48 (50–77 %) | 20/30 (49–81 %) | 23/30 (59–88 %) | 4 of 108 |
| hypruse | Mean of both | 83 % | 92 % | 72 % | 67 % | 82 % | – |
| computer-use-linux | gpt-6.1-terra | 97/108 (83–94 %) | 60/60 (94–100 %) | 37/48 (63–87 %) | 24/30 (63–90 %) | 25/30 (66–93 %) | 0 of 108 |
| computer-use-linux | Sonnet 5.5 | 96/108 (82–94 %) | 56/60 (84–97 %) | 40/48 (70–91 %) | 27/30 (74–97 %) | 25/30 (66–93 %) | 1 of 108 |
| computer-use-linux | Mean of both | 89 % | 97 % | 80 % | 85 % | 83 % | – |

</details>
<!-- /render -->

## Time

A time ratio is the geometric mean over tasks of a tool's median over hyprmasq's, within each agent, on the tasks
where both passed at least 3 of 6 runs.

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium headline-time -->
<details><summary>Time against hyprmasq, and the task units that entered it</summary>

| Tool | Agent | Time, times hyprmasq's | In tools, times hyprmasq's | Task units | Left out, too few passed runs |
| --- | --- | --- | --- | --- | --- |
| Cua Driver | gpt-6.1-terra | 1.78 | 1.54 | 13 of 18 | Label a photo, drag and drop · Firefox (passed: Cua Driver 0, hyprmasq 4); Label a photo, custom drag · Firefox (passed: Cua Driver 4, hyprmasq 1); Draw along a rail · Firefox (passed: Cua Driver 0, hyprmasq 3); Label a photo, drag and drop · Chromium (passed: Cua Driver 0, hyprmasq 6); Draw along a rail · Chromium (passed: Cua Driver 0, hyprmasq 2) |
| Cua Driver | Sonnet 5.5 | 2.01 | 1.04 | 11 of 18 | Form with dropdowns · Qt (passed: Cua Driver 0, hyprmasq 6); Add a mail account, answer a mail (passed: Cua Driver 2, hyprmasq 5); Label a photo, drag and drop · Firefox (passed: Cua Driver 0, hyprmasq 6); Draw along a rail · Firefox (passed: Cua Driver 0, hyprmasq 2); Draw along a river bank · Firefox (passed: Cua Driver 1, hyprmasq 4); Draw along a rail · Chromium (passed: Cua Driver 4, hyprmasq 2); Draw along a river bank · Chromium (passed: Cua Driver 1, hyprmasq 5) |
| hypruse | gpt-6.1-terra | 2.20 | 2.07 | 14 of 18 | Upload to Forgejo, connect a dashboard (passed: hypruse 1, hyprmasq 6); Label a photo, drag and drop · Firefox (passed: hypruse 0, hyprmasq 4); Label a photo, custom drag · Firefox (passed: hypruse 6, hyprmasq 1); Draw along a rail · Chromium (passed: hypruse 4, hyprmasq 2) |
| hypruse | Sonnet 5.5 | 3.40 | 2.05 | 15 of 18 | Label a photo, drag and drop · Firefox (passed: hypruse 0, hyprmasq 6); Draw along a rail · Firefox (passed: hypruse 3, hyprmasq 2); Draw along a rail · Chromium (passed: hypruse 1, hyprmasq 2) |
| computer-use-linux | gpt-6.1-terra | 1.58 | 1.66 | 15 of 18 | Label a photo, custom drag · Firefox (passed: computer-use-linux 6, hyprmasq 1); Draw along a rail · Firefox (passed: computer-use-linux 1, hyprmasq 3); Draw along a rail · Chromium (passed: computer-use-linux 1, hyprmasq 2) |
| computer-use-linux | Sonnet 5.5 | 1.46 | 1.11 | 15 of 18 | Draw along a rail · Firefox (passed: computer-use-linux 5, hyprmasq 2); Draw along a rail · Chromium (passed: computer-use-linux 6, hyprmasq 2); Draw along a river bank · Chromium (passed: computer-use-linux 1, hyprmasq 5) |

</details>
<!-- /render -->

## The user

**No view change** means that no window opened on the user's workspace, the workspace did not switch and the focus
never left the user's window. A run that did nothing leaves it alone too, so read it beside the result.

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium user -->
<details><summary>Numbers: the user's view, beside the result</summary>

| Tool | Agent | Passed | No view change, all runs | No view change, passed runs | Runs with input to the user's window | Runs with compositor shortcuts | Cursor moved px, median | Forgejo: user's work kept | Forgejo: characters lost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hyprmasq | gpt-6.1-terra | 92/108 | 108/108 | 92/92 | 0 | 0 | 0 | 6/6 | 0 |
| hyprmasq | Sonnet 5.5 | 95/108 | 108/108 | 95/95 | 0 | 0 | 0 | 6/6 | 0 |
| Cua Driver | gpt-6.1-terra | 73/108 | 35/108 | 22/73 | 0 | 13 | 411 | 4/6 | 39 |
| Cua Driver | Sonnet 5.5 | 68/108 | 49/108 | 25/68 | 1 | 0 | 0 | 4/6 | 24 |
| hypruse | gpt-6.1-terra | 89/108 | 0/108 | 0/89 | 0 | 0 | 494 | 4/6 | 30 |
| hypruse | Sonnet 5.5 | 90/108 | 1/108 | 1/90 | 0 | 0 | 526 | 1/6 | 51 |
| computer-use-linux | gpt-6.1-terra | 97/108 | 4/108 | 4/97 | 0 | 0 | 533 | 1/6 | 31 |
| computer-use-linux | Sonnet 5.5 | 96/108 | 10/108 | 7/96 | 0 | 0 | 497 | 5/6 | 11 |

</details>
<!-- /render -->

## Why runs failed

One verdict per failed run. [comparison/data/v2-failures.json](comparison/data/v2-failures.json) has every verdict, both
reviews and their evidence.

<details><summary>What hyprmasq and Cua Driver can do, per app and action</summary>

A scripted check of each route in this VM, with no model. A route counts here if any way of doing it works. hypruse and
computer-use-linux have no such table.

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
| Trace-rack, Firefox | draw a curve | works | works | refused | works |
| Trace-rack, Firefox | draw a stroke | works | works | refused | works |
| Trace-rack, Chromium | draw a curve | works | refused | works | works |
| Trace-rack, Chromium | draw a stroke | works | refused | works | works |
| Trace-river, Firefox | draw a curve | works | works | refused | works |
| Trace-river, Firefox | draw a stroke | works | works | refused | works |
| Trace-river, Chromium | draw a curve | works | refused | works | works |
| Trace-river, Chromium | draw a stroke | works | refused | works | works |
| Board-pointer, Chromium with a debugging port | attach to the user's browser |  | works |  |  |
| Board-pointer, Chromium with a debugging port | drag a label |  | works |  |  |

The cells of the routes that an earlier route could have met were measured again, each alone. Cua Driver's Inkscape
canvas drag in the background is dropped, and its GTK 4 scroll in the background cannot be measured by screenshot. [bench/capability-results.md](../bench/capability-results.md) has each route, the tools' refusal codes and the real
machine's table.

</details>

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium,v2-cua-attach,v2-hyprmasq-machine-a,v2-hyprmasq-machine-a-chromium,v2-failures attribution-table -->
<details><summary>Each task's failed runs, with the reasons for unclear</summary>

| Task | Tool | Agent | Passed | Model mistake | Tool problem | Unclear | Why unclear |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Form without dropdowns · Qt | hyprmasq | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Add a mail account, answer a mail | hyprmasq | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, drag and drop · Firefox | hyprmasq | gpt-6.1-terra | 4/6 | 2 | 0 | 0 | – |
| Label a photo, custom drag · Firefox | hyprmasq | gpt-6.1-terra | 1/6 | 5 | 0 | 0 | – |
| Draw along a rail · Firefox | hyprmasq | gpt-6.1-terra | 3/6 | 3 | 0 | 0 | – |
| Draw along a rail · Firefox | hyprmasq | Sonnet 5.5 | 2/6 | 4 | 0 | 0 | – |
| Draw along a river bank · Firefox | hyprmasq | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Draw along a river bank · Firefox | hyprmasq | Sonnet 5.5 | 4/6 | 2 | 0 | 0 | – |
| Draw along a rail · Chromium | hyprmasq | gpt-6.1-terra | 2/6 | 4 | 0 | 0 | – |
| Draw along a rail · Chromium | hyprmasq | Sonnet 5.5 | 2/6 | 4 | 0 | 0 | – |
| Draw along a river bank · Chromium | hyprmasq | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Draw along a river bank · Chromium | hyprmasq | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Upload to Forgejo, connect a dashboard | Cua Driver | gpt-6.1-terra | 5/6 | 0 | 0 | 1 | reviewers disagree 1 |
| Rename and move a file | Cua Driver | Sonnet 5.5 | 5/6 | 0 | 0 | 1 | reviewers disagree 1 |
| Form with dropdowns · GTK 4 | Cua Driver | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Form with dropdowns · Qt | Cua Driver | gpt-6.1-terra | 3/6 | 0 | 0 | 3 | reviewers disagree 3 |
| Form with dropdowns · Qt | Cua Driver | Sonnet 5.5 | 0/6 | 0 | 1 | 5 | reviewers disagree 5 |
| Add a mail account, answer a mail | Cua Driver | gpt-6.1-terra | 5/6 | 0 | 0 | 1 | reviewers disagree 1 |
| Add a mail account, answer a mail | Cua Driver | Sonnet 5.5 | 2/6 | 2 | 0 | 2 | reviewers disagree 2 |
| Label a photo, drag and drop · Firefox | Cua Driver | gpt-6.1-terra | 0/6 | 0 | 6 | 0 | – |
| Label a photo, drag and drop · Firefox | Cua Driver | Sonnet 5.5 | 0/6 | 0 | 6 | 0 | – |
| Label a photo, custom drag · Firefox | Cua Driver | gpt-6.1-terra | 4/6 | 2 | 0 | 0 | – |
| Label a photo, custom drag · Firefox | Cua Driver | Sonnet 5.5 | 4/6 | 2 | 0 | 0 | – |
| Copy reports into a sheet · Firefox | Cua Driver | gpt-6.1-terra | 4/6 | 2 | 0 | 0 | – |
| Draw along a rail · Firefox | Cua Driver | gpt-6.1-terra | 0/6 | 6 | 0 | 0 | – |
| Draw along a rail · Firefox | Cua Driver | Sonnet 5.5 | 0/6 | 6 | 0 | 0 | – |
| Draw along a river bank · Firefox | Cua Driver | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Draw along a river bank · Firefox | Cua Driver | Sonnet 5.5 | 1/6 | 5 | 0 | 0 | – |
| Label a photo, drag and drop · Chromium | Cua Driver | gpt-6.1-terra | 0/6 | 1 | 0 | 5 | reviewers disagree 5 |
| Label a photo, drag and drop · Chromium | Cua Driver | Sonnet 5.5 | 4/6 | 1 | 0 | 1 | reviewers disagree 1 |
| Label a photo, custom drag · Chromium | Cua Driver | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Draw along a rail · Chromium | Cua Driver | gpt-6.1-terra | 0/6 | 6 | 0 | 0 | – |
| Draw along a rail · Chromium | Cua Driver | Sonnet 5.5 | 4/6 | 2 | 0 | 0 | – |
| Draw along a river bank · Chromium | Cua Driver | Sonnet 5.5 | 1/6 | 5 | 0 | 0 | – |
| Upload to Forgejo, connect a dashboard | hypruse | gpt-6.1-terra | 1/6 | 0 | 3 | 2 | reviewers disagree 2 |
| Upload to Forgejo, connect a dashboard | hypruse | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, drag and drop · Firefox | hypruse | gpt-6.1-terra | 0/6 | 0 | 1 | 5 | evidence insufficient 2, reviewers disagree 3 |
| Label a photo, drag and drop · Firefox | hypruse | Sonnet 5.5 | 0/6 | 0 | 4 | 2 | reviewers disagree 2 |
| Copy reports into a sheet · Firefox | hypruse | gpt-6.1-terra | 3/6 | 0 | 3 | 0 | – |
| Draw along a rail · Firefox | hypruse | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Draw along a rail · Firefox | hypruse | Sonnet 5.5 | 3/6 | 3 | 0 | 0 | – |
| Draw along a river bank · Firefox | hypruse | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, drag and drop · Chromium | hypruse | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Copy reports into a sheet · Chromium | hypruse | gpt-6.1-terra | 5/6 | 0 | 0 | 1 | reviewers disagree 1 |
| Draw along a rail · Chromium | hypruse | gpt-6.1-terra | 4/6 | 2 | 0 | 0 | – |
| Draw along a rail · Chromium | hypruse | Sonnet 5.5 | 1/6 | 5 | 0 | 0 | – |
| Draw along a river bank · Chromium | hypruse | Sonnet 5.5 | 4/6 | 2 | 0 | 0 | – |
| Rename and move a file | computer-use-linux | Sonnet 5.5 | 5/6 | 0 | 1 | 0 | – |
| Form with dropdowns · Qt | computer-use-linux | Sonnet 5.5 | 3/6 | 3 | 0 | 0 | – |
| Label a photo, drag and drop · Firefox | computer-use-linux | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Draw along a rail · Firefox | computer-use-linux | gpt-6.1-terra | 1/6 | 5 | 0 | 0 | – |
| Draw along a rail · Firefox | computer-use-linux | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Draw along a river bank · Firefox | computer-use-linux | Sonnet 5.5 | 4/6 | 2 | 0 | 0 | – |
| Draw along a rail · Chromium | computer-use-linux | gpt-6.1-terra | 1/6 | 5 | 0 | 0 | – |
| Draw along a river bank · Chromium | computer-use-linux | Sonnet 5.5 | 1/6 | 5 | 0 | 0 | – |
| Label a photo, drag and drop · Chromium | Cua Driver, debugging port | Sonnet 5.5 | 5/6 | 1 | 0 | 0 | – |
| Label a photo, custom drag · Chromium | Cua Driver, debugging port | gpt-6.1-terra | 5/6 | 1 | 0 | 0 | – |
| Label a photo, custom drag · Chromium | Cua Driver, debugging port | Sonnet 5.5 | 2/6 | 4 | 0 | 0 | – |
| Copy reports into a sheet · Chromium | Cua Driver, debugging port | gpt-6.1-terra | 4/6 | 0 | 0 | 2 | reviewers disagree 2 |
| Draw along a rail · Chromium | Cua Driver, debugging port | gpt-6.1-terra | 1/6 | 5 | 0 | 0 | – |
| Draw along a rail · Chromium | Cua Driver, debugging port | Sonnet 5.5 | 1/6 | 5 | 0 | 0 | – |
| Draw along a river bank · Chromium | Cua Driver, debugging port | Sonnet 5.5 | 1/6 | 5 | 0 | 0 | – |
| Form without dropdowns · Qt | hyprmasq, machine A | Sonnet 5.5 | 0/1 | 1 | 0 | 0 | – |
| Draw along a rail · Firefox | hyprmasq, machine A | gpt-6.1-terra | 0/1 | 1 | 0 | 0 | – |
| Draw along a rail · Firefox | hyprmasq, machine A | Sonnet 5.5 | 0/1 | 1 | 0 | 0 | – |
| Draw along a rail · Chromium | hyprmasq, machine A | Sonnet 5.5 | 0/1 | 1 | 0 | 0 | – |

</details>
<!-- /render -->

## Every task, tool and agent

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium comparison-table -->
<details><summary>Numbers</summary>

**Outcome and time**

| Task | Agent | Tool | Where | Passed (95 % interval) | Time s, passed | In tools s, passed | Time s, failed | No view change | User's work kept | Calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 107 (79–131) | 48 (39–54) | – | 6/6 | – | 22 (13–27) |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 6/6 (61–100 %) | 61 (57–65) | 30 (29–31) | – | 6/6 | – | 8 (7–9) |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 5/6 (44–97 %) | 196 (190–232) | 64 (62–80) | 232 | 1/6 | – | 34 (31–43) |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 3/6 (19–81 %) | 179 (112–239) | 41 (22–45) | 41 (34–72) | 2/6 | – | 15 (5–58) |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 163 (118–181) | 43 (41–52) | – | 0/6 | – | 86 (69–103) |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 6/6 (61–100 %) | 213 (177–252) | 108 (81–137) | – | 0/6 | – | 344 (188–431) |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 115 (94–125) | 41 (33–44) | – | 0/6 | – | 50 (49–50) |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 6/6 (61–100 %) | 99 (77–116) | 25 (19–32) | – | 0/6 | – | 64 (59–87) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 84 (74–89) | 42 (38–47) | – | 6/6 | – | 18 (15–20) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 6/6 (61–100 %) | 63 (51–74) | 28 (26–30) | – | 6/6 | – | 15 (11–25) |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 168 (136–231) | 56 (19–72) | – | 3/6 | – | 72 (55–109) |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 0/6 (0–39 %) | – | – | 61 (40–87) | 5/6 | – | 14 (8–28) |
| Form with dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 263 (211–366) | 64 (38–97) | – | 0/6 | – | 90 (43–152) |
| Form with dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · Qt | 6/6 (61–100 %) | 273 (227–290) | 81 (52–107) | – | 0/6 | – | 80 (69–152) |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 98 (81–126) | 31 (25–35) | – | 0/6 | – | 59 (51–70) |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 3/6 (19–81 %) | 90 (84–99) | 27 (19–27) | 43 (36–58) | 3/6 | – | 46 (37–58) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 61 (58–66) | 32 (30–32) | – | 6/6 | – | 7 (7–8) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 6/6 (61–100 %) | 52 (50–56) | 25 (25–25) | – | 6/6 | – | 6 (6–6) |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 94 (69–140) | 24 (16–39) | – | 0/6 | – | 16 (13–26) |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 6/6 (61–100 %) | 33 (28–41) | 8 (7–9) | – | 6/6 | – | 5 (4–6) |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 125 (111–251) | 36 (32–80) | – | 0/6 | – | 70 (64–169) |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 6/6 (61–100 %) | 204 (172–217) | 108 (89–146) | – | 0/6 | – | 286 (237–510) |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 89 (79–109) | 30 (26–35) | – | 0/6 | – | 48 (44–50) |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 6/6 (61–100 %) | 36 (32–46) | 4 (4–10) | – | 4/6 | – | 40 (39–41) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 60 (51–73) | 34 (32–39) | – | 6/6 | – | 12 (7–15) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 5/6 (44–97 %) | 51 (40–54) | 24 (20–28) | 47 | 6/6 | – | 10 (8–13) |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 111 (98–135) | 41 (22–52) | – | 5/6 | – | 46 (35–56) |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 6/6 (61–100 %) | 44 (34–66) | 9 (9–12) | – | 5/6 | – | 8 (7–12) |
| Form without dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 188 (118–416) | 53 (38–121) | – | 0/6 | – | 58 (32–117) |
| Form without dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · Qt | 6/6 (61–100 %) | 253 (213–339) | 93 (84–111) | – | 1/6 | – | 63 (51–106) |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 6/6 (61–100 %) | 109 (92–123) | 35 (31–42) | – | 0/6 | – | 60 (46–71) |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 6/6 (61–100 %) | 40 (34–50) | 5 (3–6) | – | 6/6 | – | 39 (38–42) |
| Move a shape in Inkscape | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 17 (15–22) | 3 (3–4) | – | 6/6 | – | 3 (3–3) |
| Move a shape in Inkscape | gpt-6.1-terra | Cua Driver | Hyprland VM | 6/6 (61–100 %) | 33 (30–41) | 9 (7–11) | – | 5/6 | – | 5 (5–7) |
| Move a shape in Inkscape | gpt-6.1-terra | hypruse | Hyprland VM | 6/6 (61–100 %) | 115 (93–145) | 20 (11–34) | – | 0/6 | – | 36 (21–47) |
| Move a shape in Inkscape | gpt-6.1-terra | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 42 (37–48) | 10 (9–10) | – | 0/6 | – | 9 (9–9) |
| Move a shape in Inkscape | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 13 (11–15) | 4 (4–4) | – | 6/6 | – | 4 (3–4) |
| Move a shape in Inkscape | Sonnet 5.5 | Cua Driver | Hyprland VM | 6/6 (61–100 %) | 36 (27–68) | 6 (4–17) | – | 1/6 | – | 18 (13–34) |
| Move a shape in Inkscape | Sonnet 5.5 | hypruse | Hyprland VM | 6/6 (61–100 %) | 139 (26–227) | 25 (2–39) | – | 0/6 | – | 52 (12–70) |
| Move a shape in Inkscape | Sonnet 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 27 (21–29) | 8 (8–9) | – | 0/6 | – | 8 (6–9) |
| Rename and move a file | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 22 (18–31) | 5 (4–7) | – | 6/6 | – | 4 (4–7) |
| Rename and move a file | gpt-6.1-terra | Cua Driver | Hyprland VM | 6/6 (61–100 %) | 38 (33–61) | 5 (5–11) | – | 1/6 | – | 7 (7–15) |
| Rename and move a file | gpt-6.1-terra | hypruse | Hyprland VM | 6/6 (61–100 %) | 48 (34–57) | 7 (6–8) | – | 0/6 | – | 14 (12–15) |
| Rename and move a file | gpt-6.1-terra | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 44 (38–54) | 10 (7–11) | – | 0/6 | – | 10 (9–10) |
| Rename and move a file | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 17 (14–25) | 6 (5–8) | – | 6/6 | – | 6 (4–6) |
| Rename and move a file | Sonnet 5.5 | Cua Driver | Hyprland VM | 5/6 (44–97 %) | 28 (19–72) | 5 (4–7) | 47 | 0/6 | – | 17 (5–36) |
| Rename and move a file | Sonnet 5.5 | hypruse | Hyprland VM | 6/6 (61–100 %) | 49 (39–62) | 8 (7–9) | – | 0/6 | – | 18 (17–24) |
| Rename and move a file | Sonnet 5.5 | computer-use-linux | Hyprland VM | 5/6 (44–97 %) | 29 (24–33) | 8 (6–10) | 30 | 0/6 | – | 12 (10–15) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 70 (65–76) | 22 (22–24) | – | 6/6 | 6/6 | 16 (16–16) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | Cua Driver | Hyprland VM | 5/6 (44–97 %) | 181 (162–225) | 80 (63–84) | 52 | 0/6 | 4/6 | 34 (8–43) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | hypruse | Hyprland VM | 1/6 (3–56 %) | – | – | 144 (131–182) | 0/6 | 4/6 | 51 (49–54) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 170 (148–221) | 76 (55–99) | – | 0/6 | 1/6 | 48 (38–62) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 51 (49–54) | 27 (22–29) | – | 6/6 | 6/6 | 12 (11–13) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | Cua Driver | Hyprland VM | 6/6 (61–100 %) | 117 (92–154) | 24 (15–54) | – | 0/6 | 4/6 | 44 (32–49) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | hypruse | Hyprland VM | 5/6 (44–97 %) | 328 (290–331) | 119 (87–138) | 163 | 0/6 | 1/6 | 108 (55–150) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 84 (70–106) | 24 (21–30) | – | 0/6 | 5/6 | 36 (30–69) |
| Add a mail account, answer a mail | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 (61–100 %) | 90 (77–105) | 35 (28–40) | – | 6/6 | – | 18 (15–19) |
| Add a mail account, answer a mail | gpt-6.1-terra | Cua Driver | Hyprland VM | 5/6 (44–97 %) | 170 (134–204) | 40 (28–43) | 32 | 1/6 | – | 38 (7–43) |
| Add a mail account, answer a mail | gpt-6.1-terra | hypruse | Hyprland VM | 6/6 (61–100 %) | 118 (99–132) | 26 (23–30) | – | 0/6 | – | 49 (32–52) |
| Add a mail account, answer a mail | gpt-6.1-terra | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 144 (130–179) | 42 (36–56) | – | 0/6 | – | 45 (38–64) |
| Add a mail account, answer a mail | Sonnet 5.5 | hyprmasq | Hyprland VM | 5/6 (44–97 %) | 82 (79–92) | 39 (37–42) | 89 | 6/6 | – | 20 (18–24) |
| Add a mail account, answer a mail | Sonnet 5.5 | Cua Driver | Hyprland VM | 2/6 (10–70 %) | – | – | 195 (31–543) | 1/6 | – | 76 (9–152) |
| Add a mail account, answer a mail | Sonnet 5.5 | hypruse | Hyprland VM | 6/6 (61–100 %) | 155 (123–213) | 50 (26–66) | – | 0/6 | – | 60 (51–70) |
| Add a mail account, answer a mail | Sonnet 5.5 | computer-use-linux | Hyprland VM | 6/6 (61–100 %) | 97 (71–109) | 28 (22–38) | – | 0/6 | – | 36 (33–51) |
| Label a photo, drag and drop | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 6/6 (61–100 %) | 34 (29–36) | 12 (12–13) | – | 6/6 | – | 3 (2–4) |
| Label a photo, drag and drop | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 4/6 (30–90 %) | 40 (37–50) | 13 (13–14) | 31 (30–32) | 6/6 | – | 4 (2–6) |
| Label a photo, drag and drop | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0/6 (0–39 %) | – | – | 46 (38–53) | 0/6 | – | 7 (6–9) |
| Label a photo, drag and drop | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0/6 (0–39 %) | – | – | 99 (83–112) | 1/6 | – | 20 (16–24) |
| Label a photo, drag and drop | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 5/6 (44–97 %) | 37 (35–97) | 8 (8–9) | 44 | 0/6 | – | 13 (12–15) |
| Label a photo, drag and drop | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0/6 (0–39 %) | – | – | 57 (42–103) | 0/6 | – | 10 (9–20) |
| Label a photo, drag and drop | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 52 (45–72) | 18 (17–20) | – | 0/6 | – | 12 (12–14) |
| Label a photo, drag and drop | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 5/6 (44–97 %) | 54 (49–65) | 21 (16–22) | 52 | 0/6 | – | 12 (12–14) |
| Label a photo, drag and drop | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 6/6 (61–100 %) | 35 (26–47) | 13 (12–17) | – | 6/6 | – | 6 (4–10) |
| Label a photo, drag and drop | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 (61–100 %) | 33 (31–50) | 14 (14–19) | – | 6/6 | – | 6 (4–11) |
| Label a photo, drag and drop | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 4/6 (30–90 %) | 63 (53–86) | 10 (8–16) | 49 (44–54) | 0/6 | – | 22 (10–25) |
| Label a photo, drag and drop | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0/6 (0–39 %) | – | – | 46 (35–57) | 6/6 | – | 16 (14–21) |
| Label a photo, drag and drop | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 6/6 (61–100 %) | 53 (47–99) | 11 (10–17) | – | 0/6 | – | 8 (7–16) |
| Label a photo, drag and drop | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 0/6 (0–39 %) | – | – | 72 (52–190) | 0/6 | – | 16 (12–29) |
| Label a photo, drag and drop | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 37 (32–54) | 16 (13–18) | – | 0/6 | – | 12 (11–15) |
| Label a photo, drag and drop | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 41 (34–49) | 15 (14–16) | – | 0/6 | – | 12 (11–13) |
| Label a photo, custom drag | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 6/6 (61–100 %) | 33 (26–52) | 12 (12–13) | – | 6/6 | – | 3 (2–8) |
| Label a photo, custom drag | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 1/6 (3–56 %) | – | – | 28 (26–31) | 6/6 | – | 2 (2–3) |
| Label a photo, custom drag | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 6/6 (61–100 %) | 52 (44–59) | 12 (12–12) | – | 0/6 | – | 7 (7–7) |
| Label a photo, custom drag | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 4/6 (30–90 %) | 50 (48–56) | 8 (8–9) | 43 (39–47) | 6/6 | – | 8 (5–8) |
| Label a photo, custom drag | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 6/6 (61–100 %) | 42 (36–52) | 9 (8–10) | – | 0/6 | – | 13 (13–14) |
| Label a photo, custom drag | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 6/6 (61–100 %) | 45 (34–52) | 10 (9–11) | – | 0/6 | – | 14 (6–14) |
| Label a photo, custom drag | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 48 (44–57) | 18 (14–21) | – | 0/6 | – | 12 (12–13) |
| Label a photo, custom drag | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 54 (48–60) | 21 (20–24) | – | 0/6 | – | 12 (12–14) |
| Label a photo, custom drag | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 6/6 (61–100 %) | 30 (25–32) | 13 (12–13) | – | 6/6 | – | 6 (3–7) |
| Label a photo, custom drag | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 (61–100 %) | 31 (29–48) | 13 (13–18) | – | 6/6 | – | 4 (4–9) |
| Label a photo, custom drag | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 5/6 (44–97 %) | 71 (41–90) | 13 (7–15) | 37 | 0/6 | – | 23 (15–31) |
| Label a photo, custom drag | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 4/6 (30–90 %) | 56 (42–87) | 9 (8–22) | 44 (41–46) | 6/6 | – | 18 (6–36) |
| Label a photo, custom drag | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 6/6 (61–100 %) | 58 (49–108) | 11 (9–13) | – | 0/6 | – | 8 (7–14) |
| Label a photo, custom drag | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 6/6 (61–100 %) | 56 (45–76) | 11 (11–16) | – | 0/6 | – | 8 (7–9) |
| Label a photo, custom drag | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 41 (38–60) | 16 (16–21) | – | 0/6 | – | 12 (12–16) |
| Label a photo, custom drag | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 44 (36–48) | 16 (15–20) | – | 0/6 | – | 12 (11–13) |
| Copy reports into a sheet | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 6/6 (61–100 %) | 66 (66–68) | 16 (16–16) | – | 6/6 | – | 15 (14–15) |
| Copy reports into a sheet | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 (61–100 %) | 63 (59–74) | 16 (15–17) | – | 6/6 | – | 14 (14–15) |
| Copy reports into a sheet | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 6/6 (61–100 %) | 118 (106–171) | 37 (32–56) | – | 1/6 | – | 21 (16–35) |
| Copy reports into a sheet | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 4/6 (30–90 %) | 99 (96–161) | 33 (29–44) | 148 (97–199) | 1/6 | – | 16 (14–31) |
| Copy reports into a sheet | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 5/6 (44–97 %) | 100 (95–113) | 20 (16–21) | 88 | 0/6 | – | 23 (21–29) |
| Copy reports into a sheet | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 3/6 (19–81 %) | 144 (116–170) | 25 (24–35) | 94 (82–218) | 0/6 | – | 24 (18–69) |
| Copy reports into a sheet | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 112 (105–118) | 36 (32–39) | – | 0/6 | – | 24 (23–31) |
| Copy reports into a sheet | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 98 (84–114) | 26 (20–38) | – | 0/6 | – | 20 (20–27) |
| Copy reports into a sheet | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 6/6 (61–100 %) | 46 (44–53) | 18 (16–21) | – | 6/6 | – | 12 (12–13) |
| Copy reports into a sheet | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 (61–100 %) | 48 (44–55) | 18 (17–20) | – | 6/6 | – | 14 (12–15) |
| Copy reports into a sheet | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 6/6 (61–100 %) | 132 (92–176) | 35 (30–57) | – | 2/6 | – | 40 (33–65) |
| Copy reports into a sheet | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 6/6 (61–100 %) | 136 (107–159) | 35 (30–44) | – | 4/6 | – | 42 (28–47) |
| Copy reports into a sheet | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 6/6 (61–100 %) | 163 (129–268) | 25 (16–41) | – | 0/6 | – | 52 (37–69) |
| Copy reports into a sheet | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 6/6 (61–100 %) | 228 (100–322) | 26 (18–39) | – | 0/6 | – | 60 (35–81) |
| Copy reports into a sheet | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 84 (63–86) | 25 (19–34) | – | 1/6 | – | 34 (29–39) |
| Copy reports into a sheet | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 86 (67–111) | 24 (13–37) | – | 0/6 | – | 36 (26–44) |
| Draw along a rail | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 2/6 (10–70 %) | – | – | 20 (19–27) | 6/6 | – | 3 (3–3) |
| Draw along a rail | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 3/6 (19–81 %) | 21 (20–22) | 3 (3–3) | 22 (21–24) | 6/6 | – | 3 (3–3) |
| Draw along a rail | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0/6 (0–39 %) | – | – | 57 (40–78) | 0/6 | – | 8 (5–11) |
| Draw along a rail | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0/6 (0–39 %) | – | – | 63 (43–93) | 6/6 | – | 10 (7–17) |
| Draw along a rail | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 4/6 (30–90 %) | 60 (42–76) | 17 (9–25) | 47 (44–50) | 0/6 | – | 16 (7–30) |
| Draw along a rail | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 5/6 (44–97 %) | 79 (39–92) | 17 (12–36) | 41 | 0/6 | – | 24 (4–49) |
| Draw along a rail | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 1/6 (3–56 %) | – | – | 47 (39–70) | 0/6 | – | 14 (12–25) |
| Draw along a rail | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 1/6 (3–56 %) | – | – | 58 (39–61) | 0/6 | – | 14 (10–29) |
| Draw along a rail | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 2/6 (10–70 %) | – | – | 15 (14–16) | 6/6 | – | 3 (3–4) |
| Draw along a rail | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 2/6 (10–70 %) | – | – | 14 (14–17) | 6/6 | – | 3 (3–4) |
| Draw along a rail | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 4/6 (30–90 %) | 53 (46–61) | 6 (6–7) | 57 (41–74) | 0/6 | – | 17 (9–20) |
| Draw along a rail | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0/6 (0–39 %) | – | – | 57 (47–80) | 5/6 | – | 21 (10–31) |
| Draw along a rail | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 1/6 (3–56 %) | – | – | 60 (38–87) | 0/6 | – | 14 (7–16) |
| Draw along a rail | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 3/6 (19–81 %) | 87 (60–94) | 15 (10–23) | 62 (45–115) | 0/6 | – | 13 (10–28) |
| Draw along a rail | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 35 (26–42) | 9 (7–10) | – | 0/6 | – | 9 (7–10) |
| Draw along a rail | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 5/6 (44–97 %) | 43 (40–47) | 12 (12–15) | 47 | 0/6 | – | 10 (9–12) |
| Draw along a river bank | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 5/6 (44–97 %) | 23 (21–42) | 3 (2–8) | 22 | 6/6 | – | 3 (3–5) |
| Draw along a river bank | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 5/6 (44–97 %) | 24 (16–28) | 3 (3–4) | 25 | 6/6 | – | 3 (2–3) |
| Draw along a river bank | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 6/6 (61–100 %) | 47 (46–54) | 10 (8–11) | – | 0/6 | – | 7 (5–8) |
| Draw along a river bank | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 5/6 (44–97 %) | 58 (50–87) | 8 (6–8) | 40 | 4/6 | – | 9 (7–20) |
| Draw along a river bank | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 6/6 (61–100 %) | 48 (47–54) | 20 (16–24) | – | 0/6 | – | 22 (4–26) |
| Draw along a river bank | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 6/6 (61–100 %) | 46 (41–56) | 18 (16–22) | – | 0/6 | – | 13 (4–23) |
| Draw along a river bank | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 6/6 (61–100 %) | 47 (42–54) | 22 (18–26) | – | 0/6 | – | 20 (16–23) |
| Draw along a river bank | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 6/6 (61–100 %) | 43 (42–49) | 19 (18–27) | – | 0/6 | – | 17 (16–23) |
| Draw along a river bank | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 5/6 (44–97 %) | 19 (15–25) | 4 (2–5) | 16 | 6/6 | – | 4 (3–5) |
| Draw along a river bank | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 4/6 (30–90 %) | 15 (14–19) | 3 (3–3) | 23 (23–23) | 6/6 | – | 4 (3–4) |
| Draw along a river bank | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 1/6 (3–56 %) | – | – | 54 (38–67) | 0/6 | – | 18 (6–27) |
| Draw along a river bank | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 1/6 (3–56 %) | – | – | 65 (38–105) | 6/6 | – | 18 (8–37) |
| Draw along a river bank | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 4/6 (30–90 %) | 52 (43–62) | 14 (10–16) | 54 (43–65) | 0/6 | – | 6 (6–17) |
| Draw along a river bank | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 5/6 (44–97 %) | 73 (43–95) | 18 (12–20) | 74 | 0/6 | – | 8 (6–14) |
| Draw along a river bank | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 1/6 (3–56 %) | – | – | 31 (25–47) | 0/6 | – | 9 (9–16) |
| Draw along a river bank | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 4/6 (30–90 %) | 31 (27–33) | 11 (9–13) | 29 (26–33) | 0/6 | – | 9 (8–11) |

**Tokens and cost**

| Task | Agent | Tool | Where | Cost $ |
| --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 0.06 (0.04–0.08) |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 0.04 (0.03–0.05) |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 0.20 (0.16–0.29) |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 0.10 (0.05–0.29) |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 0.15 (0.12–0.21) |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 0.15 (0.10–0.16) |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 0.13 (0.13–0.18) |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 0.12 (0.10–0.16) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 0.18 (0.14–0.21) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 0.15 (0.09–0.21) |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 1.05 (0.93–1.52) |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 0.35 (0.23–0.54) |
| Form with dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 6.28 (5.43–12.77) |
| Form with dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · Qt | 4.97 (3.56–8.00) |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 0.84 (0.68–1.03) |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 0.55 (0.37–0.82) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 0.03 (0.02–0.03) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 0.02 (0.02–0.03) |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 0.11 (0.09–0.14) |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 0.05 (0.04–0.05) |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 0.11 (0.08–0.20) |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 0.11 (0.08–0.12) |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 0.12 (0.11–0.14) |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 0.08 (0.07–0.08) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 0.12 (0.09–0.15) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 0.11 (0.08–0.12) |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 0.62 (0.56–0.79) |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 0.25 (0.19–0.35) |
| Form without dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 4.48 (3.62–8.06) |
| Form without dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · Qt | 3.15 (1.33–5.13) |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 0.95 (0.57–1.02) |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 0.39 (0.33–0.44) |
| Move a shape in Inkscape | gpt-6.1-terra | hyprmasq | Hyprland VM | 0.01 (0.01–0.03) |
| Move a shape in Inkscape | gpt-6.1-terra | Cua Driver | Hyprland VM | 0.06 (0.04–0.08) |
| Move a shape in Inkscape | gpt-6.1-terra | hypruse | Hyprland VM | 0.11 (0.07–0.16) |
| Move a shape in Inkscape | gpt-6.1-terra | computer-use-linux | Hyprland VM | 0.07 (0.06–0.11) |
| Move a shape in Inkscape | Sonnet 5.5 | hyprmasq | Hyprland VM | 0.03 (0.03–0.04) |
| Move a shape in Inkscape | Sonnet 5.5 | Cua Driver | Hyprland VM | 0.23 (0.18–0.44) |
| Move a shape in Inkscape | Sonnet 5.5 | hypruse | Hyprland VM | 4.92 (0.88–5.73) |
| Move a shape in Inkscape | Sonnet 5.5 | computer-use-linux | Hyprland VM | 0.20 (0.18–0.21) |
| Rename and move a file | gpt-6.1-terra | hyprmasq | Hyprland VM | 0.02 (0.01–0.03) |
| Rename and move a file | gpt-6.1-terra | Cua Driver | Hyprland VM | 0.05 (0.05–0.07) |
| Rename and move a file | gpt-6.1-terra | hypruse | Hyprland VM | 0.06 (0.05–0.13) |
| Rename and move a file | gpt-6.1-terra | computer-use-linux | Hyprland VM | 0.08 (0.06–0.10) |
| Rename and move a file | Sonnet 5.5 | hyprmasq | Hyprland VM | 0.04 (0.02–0.04) |
| Rename and move a file | Sonnet 5.5 | Cua Driver | Hyprland VM | 0.25 (0.14–0.44) |
| Rename and move a file | Sonnet 5.5 | hypruse | Hyprland VM | 1.21 (0.86–1.82) |
| Rename and move a file | Sonnet 5.5 | computer-use-linux | Hyprland VM | 0.20 (0.15–0.26) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | hyprmasq | Hyprland VM | 0.04 (0.04–0.04) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | Cua Driver | Hyprland VM | 0.20 (0.06–0.31) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | hypruse | Hyprland VM | 0.21 (0.18–0.28) |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | computer-use-linux | Hyprland VM | 0.24 (0.20–0.26) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | hyprmasq | Hyprland VM | 0.10 (0.10–0.12) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | Cua Driver | Hyprland VM | 0.64 (0.52–0.82) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | hypruse | Hyprland VM | 4.44 (1.87–7.61) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | computer-use-linux | Hyprland VM | 0.48 (0.39–0.68) |
| Add a mail account, answer a mail | gpt-6.1-terra | hyprmasq | Hyprland VM | 0.05 (0.04–0.07) |
| Add a mail account, answer a mail | gpt-6.1-terra | Cua Driver | Hyprland VM | 0.23 (0.05–0.26) |
| Add a mail account, answer a mail | gpt-6.1-terra | hypruse | Hyprland VM | 0.17 (0.10–0.22) |
| Add a mail account, answer a mail | gpt-6.1-terra | computer-use-linux | Hyprland VM | 0.26 (0.23–0.33) |
| Add a mail account, answer a mail | Sonnet 5.5 | hyprmasq | Hyprland VM | 0.18 (0.17–0.23) |
| Add a mail account, answer a mail | Sonnet 5.5 | Cua Driver | Hyprland VM | 1.51 (0.17–2.10) |
| Add a mail account, answer a mail | Sonnet 5.5 | hypruse | Hyprland VM | 5.32 (3.75–5.81) |
| Add a mail account, answer a mail | Sonnet 5.5 | computer-use-linux | Hyprland VM | 0.52 (0.44–0.66) |
| Label a photo, drag and drop | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 0.01 (0.01–0.02) |
| Label a photo, drag and drop | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 0.02 (0.01–0.03) |
| Label a photo, drag and drop | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0.06 (0.05–0.06) |
| Label a photo, drag and drop | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0.11 (0.09–0.14) |
| Label a photo, drag and drop | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 0.03 (0.03–0.04) |
| Label a photo, drag and drop | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0.05 (0.05–0.10) |
| Label a photo, drag and drop | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 0.05 (0.04–0.08) |
| Label a photo, drag and drop | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 0.06 (0.05–0.06) |
| Label a photo, drag and drop | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 0.06 (0.04–0.08) |
| Label a photo, drag and drop | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 0.06 (0.04–0.11) |
| Label a photo, drag and drop | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 0.25 (0.19–0.59) |
| Label a photo, drag and drop | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0.22 (0.19–0.27) |
| Label a photo, drag and drop | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 2.59 (1.65–3.35) |
| Label a photo, drag and drop | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 3.28 (2.92–5.78) |
| Label a photo, drag and drop | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.20 (0.15–0.28) |
| Label a photo, drag and drop | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.22 (0.18–0.25) |
| Label a photo, custom drag | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 0.01 (0.01–0.03) |
| Label a photo, custom drag | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 0.01 (0.01–0.01) |
| Label a photo, custom drag | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0.06 (0.05–0.07) |
| Label a photo, custom drag | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0.05 (0.05–0.07) |
| Label a photo, custom drag | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 0.03 (0.03–0.04) |
| Label a photo, custom drag | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0.04 (0.03–0.05) |
| Label a photo, custom drag | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 0.05 (0.04–0.06) |
| Label a photo, custom drag | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 0.05 (0.04–0.09) |
| Label a photo, custom drag | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 0.05 (0.03–0.06) |
| Label a photo, custom drag | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 0.05 (0.04–0.10) |
| Label a photo, custom drag | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 0.29 (0.19–0.46) |
| Label a photo, custom drag | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0.28 (0.21–0.38) |
| Label a photo, custom drag | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 2.39 (1.36–3.70) |
| Label a photo, custom drag | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 2.01 (1.61–2.81) |
| Label a photo, custom drag | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.21 (0.18–0.29) |
| Label a photo, custom drag | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.23 (0.19–0.26) |
| Copy reports into a sheet | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 0.05 (0.04–0.05) |
| Copy reports into a sheet | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 0.05 (0.04–0.06) |
| Copy reports into a sheet | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0.15 (0.11–0.23) |
| Copy reports into a sheet | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0.15 (0.14–0.20) |
| Copy reports into a sheet | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 0.10 (0.08–0.14) |
| Copy reports into a sheet | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0.11 (0.07–0.19) |
| Copy reports into a sheet | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 0.15 (0.14–0.21) |
| Copy reports into a sheet | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 0.16 (0.13–0.18) |
| Copy reports into a sheet | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 0.12 (0.12–0.14) |
| Copy reports into a sheet | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 0.13 (0.12–0.14) |
| Copy reports into a sheet | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 0.70 (0.47–1.14) |
| Copy reports into a sheet | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0.94 (0.60–1.14) |
| Copy reports into a sheet | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 7.51 (3.71–11.78) |
| Copy reports into a sheet | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 9.33 (4.82–11.67) |
| Copy reports into a sheet | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.61 (0.36–0.70) |
| Copy reports into a sheet | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.79 (0.64–0.96) |
| Draw along a rail | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 0.01 (0.01–0.01) |
| Draw along a rail | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 0.01 (0.01–0.02) |
| Draw along a rail | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0.06 (0.05–0.07) |
| Draw along a rail | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0.06 (0.05–0.09) |
| Draw along a rail | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 0.04 (0.03–0.07) |
| Draw along a rail | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0.05 (0.03–0.06) |
| Draw along a rail | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 0.06 (0.05–0.09) |
| Draw along a rail | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 0.06 (0.04–0.08) |
| Draw along a rail | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 0.03 (0.03–0.04) |
| Draw along a rail | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 0.03 (0.03–0.04) |
| Draw along a rail | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 0.21 (0.18–0.26) |
| Draw along a rail | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0.26 (0.20–0.34) |
| Draw along a rail | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 2.24 (1.52–3.20) |
| Draw along a rail | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 2.41 (2.07–3.05) |
| Draw along a rail | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.18 (0.15–0.26) |
| Draw along a rail | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.27 (0.18–0.30) |
| Draw along a river bank | gpt-6.1-terra | hyprmasq | Hyprland VM · Chromium | 0.01 (0.01–0.02) |
| Draw along a river bank | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 0.01 (0.01–0.02) |
| Draw along a river bank | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0.05 (0.05–0.06) |
| Draw along a river bank | gpt-6.1-terra | Cua Driver | Hyprland VM · Firefox | 0.06 (0.06–0.10) |
| Draw along a river bank | gpt-6.1-terra | hypruse | Hyprland VM · Chromium | 0.03 (0.03–0.04) |
| Draw along a river bank | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0.03 (0.03–0.07) |
| Draw along a river bank | gpt-6.1-terra | computer-use-linux | Hyprland VM · Chromium | 0.05 (0.05–0.05) |
| Draw along a river bank | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 0.05 (0.04–0.06) |
| Draw along a river bank | Sonnet 5.5 | hyprmasq | Hyprland VM · Chromium | 0.04 (0.03–0.05) |
| Draw along a river bank | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 0.04 (0.03–0.05) |
| Draw along a river bank | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 0.23 (0.16–0.26) |
| Draw along a river bank | Sonnet 5.5 | Cua Driver | Hyprland VM · Firefox | 0.29 (0.14–0.44) |
| Draw along a river bank | Sonnet 5.5 | hypruse | Hyprland VM · Chromium | 1.56 (1.33–1.83) |
| Draw along a river bank | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 2.12 (1.61–2.59) |
| Draw along a river bank | Sonnet 5.5 | computer-use-linux | Hyprland VM · Chromium | 0.15 (0.13–0.21) |
| Draw along a river bank | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 0.17 (0.16–0.21) |

</details>
<!-- /render -->

## Side checks

Cua Driver with the user's Chromium started with a debugging port, and one round of hyprmasq on Cua Driver's machine.

<!-- render v2-cua,v2-cua-chromium,v2-cua-attach attach -->
<details><summary>Numbers: Cua Driver with and without the debugging port</summary>

| Task | Agent | As documented: passed | Time s, passed | Debugging port: passed | Time s, passed |
| --- | --- | --- | --- | --- | --- |
| Upload to Forgejo, connect a dashboard | gpt-6.1-terra | 5/6 (44–97 %) | 181 (162–225) | 6/6 (61–100 %) | 158 (121–186) |
| Upload to Forgejo, connect a dashboard | Sonnet 5.5 | 6/6 (61–100 %) | 117 (92–154) | 6/6 (61–100 %) | 130 (92–140) |
| Label a photo, drag and drop · Chromium | gpt-6.1-terra | 0/6 (0–39 %) | – | 6/6 (61–100 %) | 58 (51–63) |
| Label a photo, drag and drop · Chromium | Sonnet 5.5 | 4/6 (30–90 %) | 63 (53–86) | 5/6 (44–97 %) | 72 (55–128) |
| Label a photo, custom drag · Chromium | gpt-6.1-terra | 6/6 (61–100 %) | 52 (44–59) | 5/6 (44–97 %) | 52 (31–68) |
| Label a photo, custom drag · Chromium | Sonnet 5.5 | 5/6 (44–97 %) | 71 (41–90) | 2/6 (10–70 %) | – |
| Copy reports into a sheet · Chromium | gpt-6.1-terra | 6/6 (61–100 %) | 118 (106–171) | 4/6 (30–90 %) | 148 (121–212) |
| Copy reports into a sheet · Chromium | Sonnet 5.5 | 6/6 (61–100 %) | 132 (92–176) | 6/6 (61–100 %) | 96 (82–148) |
| Draw along a rail · Chromium | gpt-6.1-terra | 0/6 (0–39 %) | – | 1/6 (3–56 %) | – |
| Draw along a rail · Chromium | Sonnet 5.5 | 4/6 (30–90 %) | 53 (46–61) | 1/6 (3–56 %) | – |
| Draw along a river bank · Chromium | gpt-6.1-terra | 6/6 (61–100 %) | 47 (46–54) | 6/6 (61–100 %) | 58 (51–67) |
| Draw along a river bank · Chromium | Sonnet 5.5 | 1/6 (3–56 %) | – | 1/6 (3–56 %) | – |

</details>
<!-- /render -->

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium,v2-hyprmasq-machine-a,v2-hyprmasq-machine-a-chromium machine -->
<details><summary>Numbers: hyprmasq's time per call on each machine</summary>

| Agent | Machine B, s a call (runs) | Machine A, s a call (runs) | A over B |
| --- | --- | --- | --- |
| gpt-6.1-terra | 1.57 (108) | 1.75 (18) | 1.12 |
| Sonnet 5.5 | 1.71 (108) | 1.72 (18) | 1.01 |

</details>
<!-- /render -->

## Accessibility walks and frames

Cua Driver's time budget cuts some of its reads of an app's tree, and
[the capability results](../bench/capability-results.md#the-accessibility-walks) compare their cost in the VM and on a real
machine. The frames table holds flags, not failures: a first screenshot under half the encoded size of the later ones, as an
undrawn window is mostly blank, or a page that filled in later, and screenshots
unchanged after input.

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium walks -->
<details><summary>Numbers: Cua Driver's accessibility walks</summary>

| Tool | Agent | Walks | Cut by time | Cut at elements | Cut at containers | Median ms | Runs whose first walk was cut | Runs that named a budget | Runs |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Cua Driver | gpt-6.1-terra | 1134 | 270 | 31 | 91 | 1008 | 7 | 37 | 108 |
| Cua Driver | Sonnet 5.5 | 1038 | 138 | 332 | 52 | 593 | 3 | 22 | 108 |

</details>
<!-- /render -->

<!-- render v2-hyprmasq,v2-hyprmasq-chromium,v2-cua,v2-cua-chromium,v2-hypruse,v2-hypruse-chromium,v2-computer-use-linux,v2-computer-use-linux-chromium frames -->
<details><summary>Numbers: first frames and unchanged frames, per task</summary>

| Task | hyprmasq: small first frame, unchanged frames | Cua Driver: small first frame, unchanged frames | hypruse: small first frame, unchanged frames | computer-use-linux: small first frame, unchanged frames |
| --- | --- | --- | --- | --- |
| Upload to Forgejo, connect a dashboard | 1/12, 0 | 2/12, 2 | 0/12, 0 | 0/12, 0 |
| Rename and move a file | 0/12, 0 | 0/12, 1 | 5/12, 0 | 1/12, 0 |
| Form with dropdowns · GTK 4 | 0/12, 0 | 0/12, 7 | 0/12, 2 | 0/12, 0 |
| Form without dropdowns · GTK 4 | 0/12, 0 | 0/12, 6 | 0/12, 2 | 0/12, 0 |
| Form with dropdowns · Qt | 0/12, 0 | 0/12, 27 | 2/12, 0 | 0/12, 0 |
| Form without dropdowns · Qt | 0/12, 0 | 0/12, 28 | 1/12, 5 | 0/12, 0 |
| Move a shape in Inkscape | 0/12, 0 | 0/12, 2 | 3/12, 13 | 5/12, 0 |
| Add a mail account, answer a mail | 1/12, 0 | 0/12, 17 | 2/12, 0 | 3/12, 0 |
| Label a photo, drag and drop · Firefox | 0/12, 0 | 0/12, 2 | 0/12, 0 | 0/12, 0 |
| Label a photo, custom drag · Firefox | 0/12, 0 | 0/12, 5 | 0/12, 0 | 0/12, 0 |
| Copy reports into a sheet · Firefox | 0/12, 0 | 0/12, 16 | 2/12, 0 | 2/12, 1 |
| Draw along a rail · Firefox | 0/12, 0 | 0/12, 9 | 0/12, 0 | 0/12, 0 |
| Draw along a river bank · Firefox | 0/12, 0 | 0/12, 7 | 0/12, 0 | 0/12, 0 |
| Label a photo, drag and drop · Chromium | 0/12, 0 | 0/12, 0 | 0/12, 0 | 0/12, 0 |
| Label a photo, custom drag · Chromium | 0/12, 0 | 0/12, 0 | 0/12, 0 | 0/12, 0 |
| Copy reports into a sheet · Chromium | 0/12, 0 | 0/12, 13 | 4/12, 1 | 1/12, 0 |
| Draw along a rail · Chromium | 0/12, 0 | 0/12, 0 | 0/12, 0 | 0/12, 0 |
| Draw along a river bank · Chromium | 0/12, 0 | 0/12, 0 | 0/12, 0 | 0/12, 0 |

</details>
<!-- /render -->
