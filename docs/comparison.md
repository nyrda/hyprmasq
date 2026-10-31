# Comparisons with other computer-use tools

hyprmasq beside three other tools that let an agent use a Linux desktop, on the eleven tasks of the
[benchmarks](benchmarks.md) in their VM, six runs per task and agent, while the user works on their own workspace:

- **[Cua Driver](https://github.com/trycua/cua)** passed 73 % of its runs on the nine tasks it can do, where hyprmasq
  passed 91 %, took 2.4 times as long and took the user's focus in most of them. It cannot act in Firefox or Thunderbird on Hyprland.
- **[computer-use-linux](https://github.com/agent-sh/computer-use-linux)** and
  **[hypruse](https://github.com/IlyasKhallouki/hypruse)** passed 77 % and 74 % with Terra where hyprmasq passed 92 %,
  took 1.7 and 2.0 times as long, and took the user's focus in nearly every passed run.

No hyprmasq run disturbed the user. Each tool ran as its authors document it, run by hyprmasq's author. Every run is
in [comparison/data](comparison/data), and [Method](#method) has the tasks and the setups.

## hyprmasq and Cua Driver

<!-- render vm-hyprmasq,vm-hyprmasq-qt,vm-cua,vm-cua-qt,vm-cua-chromium comparison-pooled -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/comparison-pooled-dark.svg">
  <img alt="Tools compared on the same tasks: a row per tool for each task, with the share of its runs that passed, the median time, and the median uncached input, peak context, cost per run and seconds the user was disturbed." src="comparison/comparison-pooled.svg">
</picture>
<!-- /render -->

On the nine tasks that both can do, with the form in both of its toolkits, hyprmasq passed 180 of 198 runs and Cua
145. For the same task, toolkit and agent, Cua's median time was 2.4 times hyprmasq's, its peak context 3.4 times and
its cost per run 3.9 times. Cua left the user undisturbed only in the Qt form without dropdowns, whose controls it
can set through accessibility. Elsewhere it fell back to input in the foreground, as its plugin sends input in the
background only to two exact Arch packages, none of which the VM has. On Thunderbird and Forgejo it passed no run.

<details><summary>What Cua Driver can drive, app by app</summary>

| App | Tasks | hyprmasq | Cua Driver |
| --- | --- | --- | --- |
| Firefox | Board, trace | In the background | Refused. Its foreground input refuses Firefox, which binds the user's seat more than once (`foreground_primary_binding`), and its browser tools take Chromium only. None of 72 runs passed. |
| Firefox and LibreOffice Calc | Calc | In the background | 16 of 18 runs passed, 12 of them in the foreground. The agents read the reports in Firefox through its accessibility tree, and entered them in Calc. |
| Chromium | Board, trace and calc, for Cua | Not run, the tasks use Firefox | In the foreground. Its browser tools refuse on Hyprland, as they attach through a GNOME Shell helper (`browser_wrong_target_refused`). The plugin refuses input while a drag and drop is active (`foreground_dnd`), which the board's dnd variant needs, and a trace's curve is straight strokes, as Cua has no path of several points on Hyprland. |
| Thunderbird | Thunderbird | In the background | Refused, as Firefox. None of 18 runs passed. |
| Chromium | Forgejo | In the background | None of 18 runs passed. Its browser tools refused, as above, and its foreground input refused while the user typed (`foreground_physical_keys`). |
| The Qt questionnaire | Form | In the background | Through accessibility, in the background: each run of the form without dropdowns left the user undisturbed. A list item of a Qt dropdown takes no accessibility selection, so its lists went to the foreground, and few runs with dropdowns passed. |
| The GTK 4 questionnaire | Form | In the background | Its checkboxes expose no accessibility action, which Cua told the agent, so it clicked them in the foreground, in every run. |
| Nautilus and Inkscape | File manager, Inkscape | In the background | Through accessibility, and in the foreground where accessibility fell short, such as a drag or a field's text. |

Board, trace and calc ran for Cua in Chromium, where it can act. The
[Firefox runs](comparison/data/vm-cua-firefox.json) are kept, and [bench's README](../bench/README.md) has how each
refusal comes about, with the places in Cua's source.

</details>

<!-- render vm-hyprmasq,vm-hyprmasq-qt,vm-cua,vm-cua-qt,vm-cua-chromium comparison-table -->
<details><summary>Numbers</summary>

**Outcome and time**

| Task | Agent | Tool | Where | Passed | Time s | In tools s | Calls | Failed calls | Focus away s | Windows over user s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 94 (83–119) | 34 (32–40) | 17 (16–22) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · Qt | 6/6 | 55 (52–59) | 27 (26–28) | 10 (10–13) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · GTK 4 | 6/6 | 361 (310–411) | 156 (127–205) | 41 (31–48) | 10 (7–14) | 307 (243–369) | – |
| Form with dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · Qt | 4/6 | 209 (180–282) | 80 (44–105) | 28 (10–47) | 10 (1–12) | 162 (88–195) | – |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 79 (68–90) | 34 (30–35) | 20 (13–20) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 6/6 | 51 (46–53) | 29 (27–30) | 8 (8–10) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 6/6 | 171 (157–220) | 64 (50–124) | 34 (29–39) | 6 (6–8) | 141 (122–177) | – |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 0/6 | – | – | 5 (5–11) | 0 (0–2) | – | – |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 5/6 | 76 (74–142) | 36 (34–46) | 15 (14–36) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 5/6 | 52 (48–72) | 28 (26–31) | 13 (10–16) | 0 (0–1) | 0 (0–0) | – |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 2/6 | 148 (143–152) | 30 (20–40) | 26 (16–59) | 8 (5–14) | 125 (124–127) | – |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 1/6 | 92 | 23 | 12 (7–49) | 0 (0–11) | 59 | – |
| Form without dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 68 (63–76) | 32 (32–34) | 8 (7–12) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · Qt | 6/6 | 49 (46–56) | 25 (24–26) | 7 (7–11) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · GTK 4 | 6/6 | 160 (120–194) | 66 (44–94) | 17 (14–21) | 2 (2–4) | 91 (75–112) | – |
| Form without dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · Qt | 6/6 | 46 (44–49) | 12 (11–15) | 9 (7–9) | 1 (0–2) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 56 (53–59) | 32 (32–35) | 7 (7–7) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 6/6 | 39 (35–44) | 24 (24–25) | 6 (6–6) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 6/6 | 92 (63–104) | 34 (20–53) | 15 (11–18) | 2 (0–2) | 46 (32–70) | – |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 6/6 | 28 (21–30) | 9 (8–11) | 4 (4–6) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 60 (56–71) | 29 (27–30) | 10 (9–15) | 0 (0–2) | 0 (0–0) | – |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 5/6 | 42 (42–51) | 24 (24–29) | 10 (9–16) | 0 (0–2) | 0 (0–0) | – |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 6/6 | 101 (50–118) | 23 (11–35) | 30 (18–67) | 2 (1–6) | 61 (29–91) | – |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 6/6 | 45 (37–61) | 14 (11–20) | 10 (8–14) | 1 (0–3) | 0 (0–0) | – |
| Inkscape | gpt-6.1-sol | hyprmasq | Hyprland VM | 6/6 | 22 (20–24) | 3 (3–4) | 4 (4–4) | 0 (0–0) | 0 (0–0) | – |
| Inkscape | gpt-6.1-sol | Cua Driver | Hyprland VM | 6/6 | 72 (45–82) | 20 (15–29) | 8 (7–9) | 2 (1–3) | 29 (15–49) | – |
| Inkscape | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 16 (15–17) | 3 (3–4) | 3 (3–3) | 0 (0–0) | 0 (0–0) | – |
| Inkscape | gpt-6.1-terra | Cua Driver | Hyprland VM | 6/6 | 38 (33–39) | 11 (6–11) | 6 (4–6) | 1 (0–1) | 18 (12–21) | – |
| Inkscape | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 13 (11–18) | 3 (3–4) | 3 (3–4) | 0 (0–1) | 0 (0–0) | – |
| Inkscape | Sonnet 5.5 | Cua Driver | Hyprland VM | 6/6 | 37 (29–73) | 8 (6–28) | 16 (15–20) | 2 (2–3) | 22 (17–52) | – |
| File manager | gpt-6.1-sol | hyprmasq | Hyprland VM | 6/6 | 32 (25–64) | 6 (5–12) | 6 (5–10) | 0 (0–0) | 0 (0–0) | – |
| File manager | gpt-6.1-sol | Cua Driver | Hyprland VM | 5/6 | 96 (54–170) | 19 (18–38) | 14 (10–19) | 4 (3–5) | 50 (26–124) | – |
| File manager | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 23 (20–34) | 6 (5–12) | 6 (5–10) | 0 (0–0) | 0 (0–0) | – |
| File manager | gpt-6.1-terra | Cua Driver | Hyprland VM | 4/6 | 72 (56–79) | 24 (13–26) | 16 (14–22) | 4 (4–6) | 44 (32–58) | – |
| File manager | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 18 (15–19) | 5 (5–6) | 5 (4–6) | 1 (0–1) | 0 (0–0) | – |
| File manager | Sonnet 5.5 | Cua Driver | Hyprland VM | 2/6 | 109 (104–114) | 21 (9–33) | 28 (22–42) | 15 (12–17) | 96 (92–102) | – |
| Board-dnd | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 1/6 | 47 | 12 | 5 (5–7) | 0 (0–0) | 0 | – |
| Board-dnd | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 3/6 | 123 (106–123) | 14 (12–17) | 17 (16–19) | 2 (2–7) | 49 (46–58) | – |
| Board-dnd | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 2/6 | 30 (27–33) | 12 (11–12) | 2 (2–3) | 0 (0–0) | 0 (0–0) | – |
| Board-dnd | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 0/6 | – | – | 8 (7–10) | 3 (2–4) | – | – |
| Board-dnd | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 32 (29–44) | 13 (12–19) | 4 (3–7) | 0 (0–1) | 0 (0–0) | – |
| Board-dnd | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 4/6 | 128 (101–137) | 18 (13–29) | 28 (17–48) | 6 (5–6) | 80 (72–98) | – |
| Board-pointer | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 5/6 | 48 (38–52) | 12 (12–13) | 5 (5–6) | 0 (0–0) | 0 (0–0) | – |
| Board-pointer | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 6/6 | 93 (88–101) | 18 (11–20) | 10 (9–11) | 1 (1–2) | 30 (24–40) | – |
| Board-pointer | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 4/6 | 35 (33–39) | 13 (12–13) | 4 (4–5) | 0 (0–0) | 0 (0–0) | – |
| Board-pointer | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 4/6 | 48 (44–52) | 11 (10–14) | 8 (7–9) | 1 (0–1) | 18 (15–28) | – |
| Board-pointer | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 31 (26–45) | 13 (12–13) | 4 (3–6) | 0 (0–0) | 0 (0–0) | – |
| Board-pointer | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 5/6 | 104 (71–122) | 10 (8–13) | 23 (16–37) | 5 (1–10) | 40 (29–75) | – |
| Calc | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 6/6 | 82 (76–94) | 20 (18–22) | 17 (15–17) | 0 (0–0) | 0 (0–0) | – |
| Calc | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 6/6 | 259 (156–299) | 117 (60–144) | 32 (25–38) | 3 (1–4) | 158 (57–195) | – |
| Calc | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 | 51 (43–62) | 16 (15–17) | 13 (12–14) | 0 (0–0) | 0 (0–0) | – |
| Calc | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 6/6 | 124 (111–236) | 61 (45–134) | 22 (17–31) | 1 (0–3) | 78 (29–120) | – |
| Calc | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 50 (46–88) | 16 (15–28) | 13 (12–16) | 0 (0–1) | 0 (0–0) | – |
| Calc | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 6/6 | 123 (83–204) | 23 (16–59) | 38 (28–52) | 0 (0–2) | 96 (64–182) | – |
| Forgejo | gpt-6.1-sol | hyprmasq | Hyprland VM | 6/6 | 74 (69–79) | 20 (18–22) | 16 (14–18) | 0 (0–0) | 0 (0–0) | – |
| Forgejo | gpt-6.1-sol | Cua Driver | Hyprland VM | 0/6 | – | – | 15 (9–39) | 3 (1–5) | – | – |
| Forgejo | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 54 (51–60) | 20 (18–24) | 15 (14–16) | 0 (0–0) | 0 (0–0) | – |
| Forgejo | gpt-6.1-terra | Cua Driver | Hyprland VM | 0/6 | – | – | 27 (15–37) | 3 (1–8) | – | – |
| Forgejo | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 57 (50–64) | 27 (24–31) | 12 (12–13) | 0 (0–0) | 0 (0–0) | – |
| Forgejo | Sonnet 5.5 | Cua Driver | Hyprland VM | 0/6 | – | – | 20 (14–29) | 4 (2–5) | – | – |
| Thunderbird | gpt-6.1-sol | hyprmasq | Hyprland VM | 6/6 | 129 (93–173) | 35 (27–42) | 26 (19–32) | 0 (0–0) | 0 (0–0) | – |
| Thunderbird | gpt-6.1-sol | Cua Driver | Hyprland VM | 0/6 | – | – | 18 (16–27) | 5 (4–6) | – | – |
| Thunderbird | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 77 (65–86) | 34 (27–40) | 18 (17–20) | 1 (0–1) | 0 (0–0) | – |
| Thunderbird | gpt-6.1-terra | Cua Driver | Hyprland VM | 0/6 | – | – | 8 (7–11) | 4 (2–4) | – | – |
| Thunderbird | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 91 (75–114) | 33 (30–36) | 20 (17–23) | 1 (0–1) | 0 (0–0) | – |
| Thunderbird | Sonnet 5.5 | Cua Driver | Hyprland VM | 0/6 | – | – | 16 (12–22) | 6 (4–10) | – | – |
| Trace-rack | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 6/6 | 38 (29–54) | 3 (2–6) | 6 (5–8) | 0 (0–0) | 0 (0–0) | – |
| Trace-rack | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 5/6 | 74 (55–125) | 9 (5–13) | 10 (8–11) | 2 (1–2) | 30 (11–75) | – |
| Trace-rack | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 | 21 (20–31) | 3 (2–5) | 3 (3–5) | 0 (0–0) | 0 (0–0) | – |
| Trace-rack | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 1/6 | 58 | 15 | 8 (6–11) | 1 (1–2) | 34 | – |
| Trace-rack | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 3/6 | 18 (15–19) | 2 (2–3) | 4 (3–4) | 0 (0–0) | 0 (0–0) | – |
| Trace-rack | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 2/6 | 76 (73–78) | 8 (8–8) | 12 (7–25) | 2 (1–3) | 51 (50–52) | – |
| Trace-river | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 6/6 | 74 (42–96) | 4 (2–6) | 7 (5–9) | 0 (0–0) | 0 (0–0) | – |
| Trace-river | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 6/6 | 84 (57–180) | 13 (6–31) | 11 (8–20) | 2 (1–2) | 35 (9–134) | – |
| Trace-river | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 | 21 (20–31) | 2 (2–6) | 3 (3–5) | 0 (0–0) | 0 (0–0) | – |
| Trace-river | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 5/6 | 49 (40–61) | 12 (7–19) | 8 (6–9) | 1 (1–1) | 14 (7–33) | – |
| Trace-river | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 16 (14–20) | 2 (2–3) | 3 (3–4) | 0 (0–0) | 0 (0–0) | – |
| Trace-river | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 2/6 | 76 (52–100) | 10 (8–12) | 11 (8–20) | 2 (1–2) | 50 (27–72) | – |

**Tokens and cost**

| Task | Agent | Tool | Where | Input k | Uncached k | Peak context k | Cost $ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · GTK 4 | 356 (337–480) | 33 (22–38) | 25 (24–28) | 0.11 (0.10–0.12) |
| Form with dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · Qt | 194 (187–264) | 17 (12–32) | 20 (19–22) | 0.06 (0.05–0.09) |
| Form with dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · GTK 4 | 2526 (1657–4320) | 89 (67–116) | 95 (74–114) | 0.47 (0.35–0.71) |
| Form with dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · Qt | 1930 (580–3003) | 70 (48–89) | 74 (56–93) | 0.37 (0.16–0.52) |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 413 (276–441) | 35 (25–52) | 26 (22–27) | 0.06 (0.05–0.08) |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 180 (179–206) | 18 (14–27) | 23 (19–25) | 0.03 (0.03–0.04) |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 1975 (1804–3095) | 121 (107–146) | 85 (60–102) | 0.24 (0.21–0.29) |
| Form with dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 299 (258–819) | 72 (61–93) | 50 (47–79) | 0.09 (0.07–0.14) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 312 (257–983) | 22 (19–41) | 29 (26–48) | 0.11 (0.10–0.25) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 224 (170–315) | 18 (14–23) | 25 (23–31) | 0.12 (0.09–0.14) |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 1981 (1255–4174) | 44 (37–91) | 100 (93–148) | 0.35 (0.28–0.71) |
| Form with dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 1076 (589–2984) | 59 (46–111) | 111 (103–139) | 0.47 (0.27–0.71) |
| Form without dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · GTK 4 | 174 (156–244) | 21 (17–34) | 24 (23–24) | 0.07 (0.06–0.10) |
| Form without dropdowns | gpt-6.1-sol | hyprmasq | Hyprland VM · Qt | 149 (149–217) | 23 (14–34) | 21 (21–21) | 0.07 (0.05–0.09) |
| Form without dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · GTK 4 | 967 (717–1056) | 51 (44–70) | 55 (51–73) | 0.22 (0.18–0.26) |
| Form without dropdowns | gpt-6.1-sol | Cua Driver | Hyprland VM · Qt | 432 (349–453) | 41 (38–70) | 46 (45–52) | 0.13 (0.12–0.19) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 157 (156–159) | 23 (21–38) | 24 (23–24) | 0.03 (0.03–0.05) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 130 (129–130) | 14 (13–21) | 21 (21–21) | 0.02 (0.02–0.03) |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · GTK 4 | 774 (596–1228) | 72 (67–98) | 54 (51–77) | 0.12 (0.10–0.17) |
| Form without dropdowns | gpt-6.1-terra | Cua Driver | Hyprland VM · Qt | 272 (205–313) | 60 (47–83) | 39 (39–53) | 0.07 (0.06–0.10) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 191 (146–255) | 16 (13–21) | 24 (20–28) | 0.08 (0.06–0.10) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 162 (153–265) | 13 (12–21) | 22 (21–29) | 0.08 (0.08–0.13) |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · GTK 4 | 1833 (1134–2349) | 55 (44–63) | 111 (100–120) | 0.39 (0.26–0.46) |
| Form without dropdowns | Sonnet 5.5 | Cua Driver | Hyprland VM · Qt | 915 (665–1227) | 43 (39–44) | 100 (96–101) | 0.29 (0.27–0.34) |
| Inkscape | gpt-6.1-sol | hyprmasq | Hyprland VM | 80 (80–82) | 14 (8–24) | 15 (15–16) | 0.04 (0.03–0.06) |
| Inkscape | gpt-6.1-sol | Cua Driver | Hyprland VM | 437 (342–631) | 50 (35–60) | 52 (41–65) | 0.15 (0.11–0.19) |
| Inkscape | gpt-6.1-terra | hyprmasq | Hyprland VM | 65 (65–65) | 12 (12–20) | 15 (15–15) | 0.02 (0.02–0.02) |
| Inkscape | gpt-6.1-terra | Cua Driver | Hyprland VM | 324 (236–344) | 61 (53–64) | 45 (39–49) | 0.08 (0.06–0.08) |
| Inkscape | Sonnet 5.5 | hyprmasq | Hyprland VM | 43 (42–55) | 6 (5–6) | 13 (12–14) | 0.02 (0.02–0.03) |
| Inkscape | Sonnet 5.5 | Cua Driver | Hyprland VM | 775 (740–979) | 31 (26–86) | 84 (82–95) | 0.18 (0.16–0.30) |
| File manager | gpt-6.1-sol | hyprmasq | Hyprland VM | 110 (92–184) | 16 (11–24) | 16 (15–18) | 0.05 (0.04–0.06) |
| File manager | gpt-6.1-sol | Cua Driver | Hyprland VM | 653 (448–1259) | 40 (38–90) | 46 (40–76) | 0.16 (0.13–0.28) |
| File manager | gpt-6.1-terra | hyprmasq | Hyprland VM | 105 (92–179) | 13 (12–30) | 15 (15–18) | 0.02 (0.02–0.04) |
| File manager | gpt-6.1-terra | Cua Driver | Hyprland VM | 1092 (728–1805) | 131 (88–150) | 94 (71–105) | 0.19 (0.14–0.24) |
| File manager | Sonnet 5.5 | hyprmasq | Hyprland VM | 66 (47–73) | 5 (4–6) | 13 (11–13) | 0.03 (0.02–0.03) |
| File manager | Sonnet 5.5 | Cua Driver | Hyprland VM | 1983 (1343–3006) | 32 (24–45) | 88 (81–101) | 0.32 (0.23–0.45) |
| Board-dnd | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 98 (98–132) | 14 (9–18) | 16 (16–17) | 0.05 (0.03–0.05) |
| Board-dnd | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 901 (763–1353) | 66 (49–136) | 73 (55–90) | 0.24 (0.19–0.42) |
| Board-dnd | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 57 (49–65) | 13 (12–13) | 15 (14–16) | 0.02 (0.02–0.02) |
| Board-dnd | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 379 (337–475) | 58 (49–86) | 39 (37–41) | 0.08 (0.07–0.11) |
| Board-dnd | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 64 (42–95) | 8 (5–14) | 15 (13–17) | 0.04 (0.03–0.05) |
| Board-dnd | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 1724 (1193–1956) | 35 (25–51) | 92 (82–108) | 0.36 (0.25–0.42) |
| Board-pointer | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 98 (98–115) | 12 (9–19) | 16 (16–17) | 0.04 (0.03–0.05) |
| Board-pointer | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 509 (357–581) | 40 (37–49) | 46 (41–49) | 0.14 (0.14–0.15) |
| Board-pointer | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 81 (79–99) | 15 (13–19) | 16 (16–17) | 0.02 (0.02–0.03) |
| Board-pointer | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 346 (335–415) | 60 (49–74) | 39 (37–41) | 0.08 (0.07–0.10) |
| Board-pointer | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 64 (42–89) | 8 (6–16) | 15 (13–17) | 0.04 (0.03–0.07) |
| Board-pointer | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 1214 (1097–1578) | 37 (31–41) | 94 (88–98) | 0.31 (0.24–0.36) |
| Calc | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 321 (276–330) | 35 (27–47) | 25 (24–26) | 0.12 (0.10–0.14) |
| Calc | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 2196 (1290–2546) | 85 (62–126) | 88 (69–95) | 0.43 (0.28–0.49) |
| Calc | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 261 (237–300) | 35 (25–39) | 23 (23–24) | 0.05 (0.05–0.06) |
| Calc | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 1230 (1046–1931) | 104 (90–119) | 80 (71–100) | 0.18 (0.15–0.23) |
| Calc | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 210 (196–311) | 18 (18–23) | 26 (25–31) | 0.09 (0.09–0.13) |
| Calc | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 2546 (1565–3586) | 61 (47–71) | 118 (104–127) | 0.48 (0.32–0.62) |
| Forgejo | gpt-6.1-sol | hyprmasq | Hyprland VM | 317 (274–363) | 29 (24–32) | 24 (24–25) | 0.10 (0.08–0.11) |
| Forgejo | gpt-6.1-sol | Cua Driver | Hyprland VM | 779 (715–2365) | 63 (43–69) | 50 (49–73) | 0.21 (0.18–0.41) |
| Forgejo | gpt-6.1-terra | hyprmasq | Hyprland VM | 293 (274–315) | 25 (24–38) | 24 (24–24) | 0.04 (0.04–0.06) |
| Forgejo | gpt-6.1-terra | Cua Driver | Hyprland VM | 1932 (831–3139) | 113 (51–200) | 94 (55–131) | 0.22 (0.10–0.32) |
| Forgejo | Sonnet 5.5 | hyprmasq | Hyprland VM | 176 (160–190) | 16 (15–18) | 24 (22–25) | 0.08 (0.07–0.08) |
| Forgejo | Sonnet 5.5 | Cua Driver | Hyprland VM | 1363 (1083–2347) | 45 (33–88) | 92 (88–116) | 0.31 (0.24–0.46) |
| Thunderbird | gpt-6.1-sol | hyprmasq | Hyprland VM | 641 (450–836) | 45 (39–52) | 35 (31–37) | 0.18 (0.14–0.19) |
| Thunderbird | gpt-6.1-sol | Cua Driver | Hyprland VM | 1119 (872–1308) | 71 (65–90) | 73 (65–98) | 0.27 (0.23–0.31) |
| Thunderbird | gpt-6.1-terra | hyprmasq | Hyprland VM | 397 (369–446) | 34 (29–61) | 28 (27–29) | 0.06 (0.05–0.09) |
| Thunderbird | gpt-6.1-terra | Cua Driver | Hyprland VM | 496 (290–754) | 88 (58–97) | 53 (35–61) | 0.11 (0.09–0.12) |
| Thunderbird | Sonnet 5.5 | hyprmasq | Hyprland VM | 500 (332–547) | 32 (25–34) | 39 (33–41) | 0.16 (0.12–0.17) |
| Thunderbird | Sonnet 5.5 | Cua Driver | Hyprland VM | 1110 (797–1569) | 28 (22–30) | 85 (79–86) | 0.21 (0.16–0.27) |
| Trace-rack | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 107 (98–158) | 14 (9–24) | 17 (17–20) | 0.04 (0.03–0.07) |
| Trace-rack | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 495 (311–585) | 41 (32–68) | 42 (39–47) | 0.15 (0.10–0.18) |
| Trace-rack | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 66 (64–100) | 20 (14–24) | 16 (15–18) | 0.03 (0.02–0.03) |
| Trace-rack | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 396 (287–586) | 57 (52–65) | 41 (37–46) | 0.08 (0.07–0.10) |
| Trace-rack | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 47 (41–54) | 5 (5–6) | 13 (12–13) | 0.02 (0.02–0.03) |
| Trace-rack | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 837 (479–1389) | 24 (17–32) | 80 (73–89) | 0.19 (0.11–0.28) |
| Trace-river | gpt-6.1-sol | hyprmasq | Hyprland VM · Firefox | 139 (100–186) | 20 (10–28) | 19 (17–22) | 0.06 (0.04–0.09) |
| Trace-river | gpt-6.1-sol | Cua Driver | Hyprland VM · Chromium | 570 (446–970) | 52 (40–62) | 52 (47–69) | 0.17 (0.15–0.23) |
| Trace-river | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 65 (65–98) | 13 (13–20) | 16 (15–17) | 0.02 (0.02–0.03) |
| Trace-river | gpt-6.1-terra | Cua Driver | Hyprland VM · Chromium | 409 (303–527) | 61 (56–85) | 42 (38–68) | 0.09 (0.07–0.11) |
| Trace-river | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 41 (41–55) | 5 (5–6) | 12 (12–14) | 0.02 (0.02–0.03) |
| Trace-river | Sonnet 5.5 | Cua Driver | Hyprland VM · Chromium | 710 (565–1091) | 25 (22–38) | 81 (79–94) | 0.17 (0.14–0.29) |

</details>
<!-- /render -->

## hyprmasq, computer-use-linux and hypruse

<!-- render vm-hyprmasq,vm-hyprmasq-qt,vm-computer-use-linux,vm-computer-use-linux-qt,vm-hypruse,vm-hypruse-qt,vm-hypruse-sonnet probe -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/probe-dark.svg">
  <img alt="Tools compared on the same tasks: a row per tool for each agent, with the share of its runs that passed, the median time, and the median uncached input, peak context, cost per run and seconds the user was disturbed." src="comparison/probe.svg">
</picture>
<!-- /render -->

Both act through accessibility where they can, and otherwise through the user's focus: computer-use-linux brings a
window to the front even for its screenshot, and hypruse's pointer works only in the foreground.

- **Terra:** hyprmasq passed 72 of 78 runs, computer-use-linux 60 and hypruse 58. Task by task, computer-use-linux
  took 1.7 times as long as hyprmasq and hypruse 2.0 times, with 2.7 and 2.5 times hyprmasq's peak context. Only
  computer-use-linux's runs of the Qt form without dropdowns and one Inkscape run of hypruse left the user
  undisturbed.
- **Sonnet:** computer-use-linux passed 62 of 78 against hyprmasq's 72, took 1.6 times as long, with 4.1 times the
  peak context, and cost $0.37 a run against hyprmasq's $0.07.
- **Forgejo,** where the user types in their own browser window meanwhile: computer-use-linux passed none of its
  runs and hypruse none with Terra, as the user's typing went into the agent's sign-in form and addresses. hyprmasq
  passed each.

hypruse ran with Terra only. Its first seven runs with Sonnet, in the table, reached 18 times hyprmasq's peak context,
as in Claude Code it returns each screenshot twice, as an image and as base64 text.

<details><summary>Each task</summary>

<!-- render vm-hyprmasq,vm-hyprmasq-qt,vm-computer-use-linux,vm-computer-use-linux-qt,vm-hypruse,vm-hypruse-qt,vm-hypruse-sonnet probe-tasks -->
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="comparison/probe-tasks-dark.svg">
  <img alt="Tools compared on the same tasks: a row per tool for each task and agent, with the share of its runs that passed, the median time, and the median uncached input, peak context, cost per run and seconds the user was disturbed." src="comparison/probe-tasks.svg">
</picture>
<!-- /render -->

</details>

<!-- render vm-hyprmasq,vm-hyprmasq-qt,vm-computer-use-linux,vm-computer-use-linux-qt,vm-hypruse,vm-hypruse-qt,vm-hypruse-sonnet probe-table -->
<details><summary>Numbers</summary>

**Outcome and time**

| Task | Agent | Tool | Where | Passed | Time s | In tools s | Calls | Failed calls | Focus away s | Windows over user s |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 79 (68–90) | 34 (30–35) | 20 (13–20) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 6/6 | 51 (46–53) | 29 (27–30) | 8 (8–10) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 6/6 | 148 (113–221) | 47 (37–112) | 102 (37–208) | 4 (4–6) | 122 (99–200) | – |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 6/6 | 184 (151–251) | 115 (74–170) | 258 (188–407) | 0 (0–1) | 121 (99–169) | – |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 6/6 | 103 (95–143) | 52 (43–96) | 78 (73–89) | 0 (0–0) | 71 (65–104) | – |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 6/6 | 79 (64–124) | 29 (18–40) | 72 (54–89) | 0 (0–0) | 47 (37–66) | – |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 5/6 | 76 (74–142) | 36 (34–46) | 15 (14–36) | 0 (0–0) | 0 (0–0) | – |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 5/6 | 52 (48–72) | 28 (26–31) | 13 (10–16) | 0 (0–1) | 0 (0–0) | – |
| Form with dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 0/1 | – | – | 10 | 0 | – | – |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 6/6 | 129 (85–148) | 38 (28–44) | 69 (53–89) | 0 (0–0) | 75 (55–87) | – |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 5/6 | 84 (82–116) | 20 (16–23) | 58 (41–66) | 0 (0–2) | 43 (41–58) | – |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 56 (53–59) | 32 (32–35) | 7 (7–7) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 6/6 | 39 (35–44) | 24 (24–25) | 6 (6–6) | 0 (0–0) | 0 (0–0) | – |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 6/6 | 151 (95–245) | 54 (33–103) | 96 (53–234) | 5 (5–6) | 130 (80–219) | – |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 6/6 | 162 (133–198) | 111 (76–125) | 180 (31–288) | 0 (0–1) | 85 (68–118) | – |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 6/6 | 90 (68–212) | 40 (28–154) | 78 (71–92) | 0 (0–17) | 50 (36–72) | – |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 6/6 | 29 (24–78) | 4 (3–37) | 42 (41–95) | 0 (0–17) | 0 (0–0) | – |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 6/6 | 60 (56–71) | 29 (27–30) | 10 (9–15) | 0 (0–2) | 0 (0–0) | – |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 5/6 | 42 (42–51) | 24 (24–29) | 10 (9–16) | 0 (0–2) | 0 (0–0) | – |
| Form without dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 0/1 | – | – | 4 | 0 | – | – |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 6/6 | 98 (74–128) | 27 (20–33) | 59 (53–63) | 0 (0–0) | 55 (46–79) | – |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 6/6 | 38 (33–44) | 4 (4–4) | 40 (39–41) | 0 (0–0) | 0 (0–0) | – |
| Inkscape | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 16 (15–17) | 3 (3–4) | 3 (3–3) | 0 (0–0) | 0 (0–0) | – |
| Inkscape | gpt-6.1-terra | hypruse | Hyprland VM | 5/6 | 100 (28–166) | 51 (8–81) | 38 (6–69) | 0 (0–2) | 82 (0–144) | – |
| Inkscape | gpt-6.1-terra | computer-use-linux | Hyprland VM | 6/6 | 39 (34–62) | 14 (11–17) | 9 (9–9) | 0 (0–0) | 24 (22–38) | – |
| Inkscape | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 13 (11–18) | 3 (3–4) | 3 (3–4) | 0 (0–1) | 0 (0–0) | – |
| Inkscape | Sonnet 5.5 | computer-use-linux | Hyprland VM | 6/6 | 31 (25–38) | 12 (11–17) | 8 (8–9) | 0 (0–0) | 15 (12–20) | – |
| File manager | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 23 (20–34) | 6 (5–12) | 6 (5–10) | 0 (0–0) | 0 (0–0) | – |
| File manager | gpt-6.1-terra | hypruse | Hyprland VM | 6/6 | 36 (30–46) | 9 (7–10) | 15 (12–16) | 0 (0–0) | 16 (14–19) | – |
| File manager | gpt-6.1-terra | computer-use-linux | Hyprland VM | 6/6 | 42 (33–54) | 12 (10–16) | 11 (10–11) | 0 (0–0) | 30 (25–38) | – |
| File manager | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 18 (15–19) | 5 (5–6) | 5 (4–6) | 1 (0–1) | 0 (0–0) | – |
| File manager | Sonnet 5.5 | computer-use-linux | Hyprland VM | 5/6 | 30 (27–31) | 11 (8–12) | 14 (11–19) | 0 (0–1) | 17 (17–21) | – |
| Board-dnd | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 2/6 | 30 (27–33) | 12 (11–12) | 2 (2–3) | 0 (0–0) | 0 (0–0) | – |
| Board-dnd | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 0/6 | – | – | 10 (8–10) | 0 (0–0) | – | – |
| Board-dnd | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 4/6 | 48 (43–55) | 20 (18–22) | 13 (13–13) | 0 (0–0) | 35 (33–41) | – |
| Board-dnd | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 32 (29–44) | 13 (12–19) | 4 (3–7) | 0 (0–1) | 0 (0–0) | – |
| Board-dnd | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 0/1 | – | – | 17 | 0 | – | – |
| Board-dnd | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 | 42 (37–52) | 16 (15–20) | 13 (13–13) | 0 (0–0) | 33 (29–40) | – |
| Board-pointer | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 4/6 | 35 (33–39) | 13 (12–13) | 4 (4–5) | 0 (0–0) | 0 (0–0) | – |
| Board-pointer | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 3/6 | 39 (33–43) | 9 (8–13) | 14 (13–16) | 0 (0–0) | 22 (18–31) | – |
| Board-pointer | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 3/6 | 50 (43–53) | 21 (17–22) | 13 (12–14) | 0 (0–0) | 37 (33–38) | – |
| Board-pointer | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 31 (26–45) | 13 (12–13) | 4 (3–6) | 0 (0–0) | 0 (0–0) | – |
| Board-pointer | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 1/1 | 57 | 9 | 14 | 0 | 41 | – |
| Board-pointer | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 | 42 (35–55) | 16 (15–28) | 12 (11–15) | 0 (0–1) | 34 (25–41) | – |
| Calc | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 | 51 (43–62) | 16 (15–17) | 13 (12–14) | 0 (0–0) | 0 (0–0) | – |
| Calc | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 5/6 | 102 (76–145) | 33 (21–48) | 33 (24–69) | 0 (0–2) | 83 (59–124) | – |
| Calc | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 6/6 | 106 (74–189) | 41 (21–108) | 46 (24–59) | 1 (1–2) | 78 (54–167) | – |
| Calc | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 50 (46–88) | 16 (15–28) | 13 (12–16) | 0 (0–1) | 0 (0–0) | – |
| Calc | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 1/1 | 204 | 15 | 46 | 0 | 180 | – |
| Calc | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 | 107 (78–160) | 52 (36–88) | 43 (38–58) | 1 (1–2) | 95 (67–138) | – |
| Forgejo | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 54 (51–60) | 20 (18–24) | 15 (14–16) | 0 (0–0) | 0 (0–0) | – |
| Forgejo | gpt-6.1-terra | hypruse | Hyprland VM | 0/6 | – | – | 37 (27–50) | 0 (0–1) | – | – |
| Forgejo | gpt-6.1-terra | computer-use-linux | Hyprland VM | 0/6 | – | – | 25 (13–31) | 2 (0–4) | – | – |
| Forgejo | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 57 (50–64) | 27 (24–31) | 12 (12–13) | 0 (0–0) | 0 (0–0) | – |
| Forgejo | Sonnet 5.5 | hypruse | Hyprland VM | 1/1 | 368 | 120 | 107 | 2 | 130 | – |
| Forgejo | Sonnet 5.5 | computer-use-linux | Hyprland VM | 0/6 | – | – | 25 (12–35) | 2 (0–4) | – | – |
| Thunderbird | gpt-6.1-terra | hyprmasq | Hyprland VM | 6/6 | 77 (65–86) | 34 (27–40) | 18 (17–20) | 1 (0–1) | 0 (0–0) | – |
| Thunderbird | gpt-6.1-terra | hypruse | Hyprland VM | 6/6 | 104 (90–115) | 34 (25–36) | 48 (47–53) | 0 (0–0) | 81 (62–92) | – |
| Thunderbird | gpt-6.1-terra | computer-use-linux | Hyprland VM | 4/6 | 200 (159–325) | 89 (54–108) | 80 (18–109) | 4 (1–9) | 181 (144–304) | – |
| Thunderbird | Sonnet 5.5 | hyprmasq | Hyprland VM | 6/6 | 91 (75–114) | 33 (30–36) | 20 (17–23) | 1 (0–1) | 0 (0–0) | – |
| Thunderbird | Sonnet 5.5 | hypruse | Hyprland VM | 1/1 | 151 | 32 | 60 | 1 | 125 | – |
| Thunderbird | Sonnet 5.5 | computer-use-linux | Hyprland VM | 3/6 | 142 (142–142) | 51 (45–55) | 54 (23–88) | 6 (6–7) | 130 (129–131) | – |
| Trace-rack | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 | 21 (20–31) | 3 (2–5) | 3 (3–5) | 0 (0–0) | 0 (0–0) | – |
| Trace-rack | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 3/6 | 39 (31–47) | 11 (8–16) | 14 (5–18) | 0 (0–0) | 26 (24–33) | – |
| Trace-rack | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 1/6 | 53 | 12 | 10 (10–12) | 0 (0–0) | 38 | – |
| Trace-rack | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 3/6 | 18 (15–19) | 2 (2–3) | 4 (3–4) | 0 (0–0) | 0 (0–0) | – |
| Trace-rack | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 1/6 | 32 | 10 | 10 (7–22) | 0 (0–0) | 27 | – |
| Trace-river | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 6/6 | 21 (20–31) | 2 (2–6) | 3 (3–5) | 0 (0–0) | 0 (0–0) | – |
| Trace-river | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 6/6 | 42 (37–60) | 20 (12–29) | 22 (5–26) | 0 (0–0) | 33 (23–48) | – |
| Trace-river | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 6/6 | 39 (30–41) | 17 (14–20) | 16 (14–20) | 0 (0–0) | 25 (20–29) | – |
| Trace-river | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 6/6 | 16 (14–20) | 2 (2–3) | 3 (3–4) | 0 (0–0) | 0 (0–0) | – |
| Trace-river | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 6/6 | 32 (24–87) | 13 (10–30) | 12 (9–25) | 0 (0–0) | 26 (18–80) | – |

**Tokens and cost**

| Task | Agent | Tool | Where | Input k | Uncached k | Peak context k | Cost $ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 413 (276–441) | 35 (25–52) | 26 (22–27) | 0.06 (0.05–0.08) |
| Form with dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 180 (179–206) | 18 (14–27) | 23 (19–25) | 0.03 (0.03–0.04) |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 1516 (1354–3947) | 96 (78–151) | 59 (55–111) | 0.19 (0.17–0.37) |
| Form with dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 1446 (1063–2475) | 69 (48–102) | 71 (49–85) | 0.16 (0.12–0.24) |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 1255 (1095–1354) | 118 (108–173) | 69 (66–73) | 0.18 (0.17–0.24) |
| Form with dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 1647 (1202–2110) | 83 (79–88) | 82 (73–89) | 0.17 (0.15–0.20) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 312 (257–983) | 22 (19–41) | 29 (26–48) | 0.11 (0.10–0.25) |
| Form with dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 224 (170–315) | 18 (14–23) | 25 (23–31) | 0.12 (0.09–0.14) |
| Form with dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 1512 | 311 | 232 | 0.68 |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 3765 (2278–4971) | 141 (120–184) | 159 (138–202) | 1.04 (0.76–1.27) |
| Form with dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 2522 (991–3448) | 113 (102–124) | 126 (120–142) | 0.74 (0.61–0.91) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · GTK 4 | 157 (156–159) | 23 (21–38) | 24 (23–24) | 0.03 (0.03–0.05) |
| Form without dropdowns | gpt-6.1-terra | hyprmasq | Hyprland VM · Qt | 130 (129–130) | 14 (13–21) | 21 (21–21) | 0.02 (0.02–0.03) |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · GTK 4 | 1690 (1065–2913) | 107 (88–141) | 84 (56–95) | 0.21 (0.15–0.29) |
| Form without dropdowns | gpt-6.1-terra | hypruse | Hyprland VM · Qt | 984 (928–1678) | 68 (40–116) | 63 (47–92) | 0.13 (0.10–0.21) |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · GTK 4 | 872 (757–1178) | 100 (95–112) | 62 (61–65) | 0.15 (0.14–0.16) |
| Form without dropdowns | gpt-6.1-terra | computer-use-linux | Hyprland VM · Qt | 479 (401–511) | 50 (44–74) | 52 (49–55) | 0.08 (0.07–0.09) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · GTK 4 | 191 (146–255) | 16 (13–21) | 24 (20–28) | 0.08 (0.06–0.10) |
| Form without dropdowns | Sonnet 5.5 | hyprmasq | Hyprland VM · Qt | 162 (153–265) | 13 (12–21) | 22 (21–29) | 0.08 (0.08–0.13) |
| Form without dropdowns | Sonnet 5.5 | hypruse | Hyprland VM · GTK 4 | 156 | 95 | 16 | 0.03 |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · GTK 4 | 2957 (2498–3916) | 141 (128–158) | 159 (146–176) | 0.94 (0.82–1.08) |
| Form without dropdowns | Sonnet 5.5 | computer-use-linux | Hyprland VM · Qt | 685 (523–827) | 87 (71–94) | 105 (89–112) | 0.45 (0.37–0.49) |
| Inkscape | gpt-6.1-terra | hyprmasq | Hyprland VM | 65 (65–65) | 12 (12–20) | 15 (15–15) | 0.02 (0.02–0.02) |
| Inkscape | gpt-6.1-terra | hypruse | Hyprland VM | 762 (229–1171) | 61 (28–82) | 45 (34–70) | 0.10 (0.04–0.14) |
| Inkscape | gpt-6.1-terra | computer-use-linux | Hyprland VM | 493 (424–505) | 60 (52–92) | 47 (43–48) | 0.09 (0.07–0.12) |
| Inkscape | Sonnet 5.5 | hyprmasq | Hyprland VM | 43 (42–55) | 6 (5–6) | 13 (12–14) | 0.02 (0.02–0.03) |
| Inkscape | Sonnet 5.5 | computer-use-linux | Hyprland VM | 405 (354–488) | 37 (37–38) | 56 (55–56) | 0.19 (0.19–0.21) |
| File manager | gpt-6.1-terra | hyprmasq | Hyprland VM | 105 (92–179) | 13 (12–30) | 15 (15–18) | 0.02 (0.02–0.04) |
| File manager | gpt-6.1-terra | hypruse | Hyprland VM | 372 (342–551) | 51 (33–60) | 38 (34–62) | 0.07 (0.05–0.09) |
| File manager | gpt-6.1-terra | computer-use-linux | Hyprland VM | 532 (455–587) | 80 (62–92) | 51 (44–53) | 0.11 (0.09–0.12) |
| File manager | Sonnet 5.5 | hyprmasq | Hyprland VM | 66 (47–73) | 5 (4–6) | 13 (11–13) | 0.03 (0.02–0.03) |
| File manager | Sonnet 5.5 | computer-use-linux | Hyprland VM | 473 (418–608) | 40 (37–43) | 58 (55–61) | 0.22 (0.20–0.23) |
| Board-dnd | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 57 (49–65) | 13 (12–13) | 15 (14–16) | 0.02 (0.02–0.02) |
| Board-dnd | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 406 (315–465) | 51 (39–75) | 36 (31–40) | 0.08 (0.06–0.09) |
| Board-dnd | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 302 (254–314) | 54 (48–79) | 43 (41–47) | 0.07 (0.06–0.09) |
| Board-dnd | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 64 (42–95) | 8 (5–14) | 15 (13–17) | 0.04 (0.03–0.05) |
| Board-dnd | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 6594 | 918 | 918 | 2.94 |
| Board-dnd | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 353 (326–411) | 44 (37–62) | 62 (55–63) | 0.24 (0.21–0.31) |
| Board-pointer | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 81 (79–99) | 15 (13–19) | 16 (16–17) | 0.02 (0.02–0.03) |
| Board-pointer | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 260 (212–305) | 46 (34–64) | 29 (26–32) | 0.06 (0.05–0.08) |
| Board-pointer | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 304 (255–354) | 55 (48–75) | 45 (43–46) | 0.07 (0.06–0.09) |
| Board-pointer | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 64 (42–89) | 8 (6–16) | 15 (13–17) | 0.04 (0.03–0.07) |
| Board-pointer | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 1908 | 467 | 467 | 1.35 |
| Board-pointer | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 354 (288–502) | 42 (39–45) | 60 (57–63) | 0.23 (0.21–0.25) |
| Calc | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 261 (237–300) | 35 (25–39) | 23 (23–24) | 0.05 (0.05–0.06) |
| Calc | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 1270 (871–2239) | 96 (68–127) | 64 (52–105) | 0.17 (0.13–0.24) |
| Calc | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 1436 (1283–1867) | 106 (75–119) | 73 (67–80) | 0.19 (0.15–0.21) |
| Calc | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 210 (196–311) | 18 (18–23) | 26 (25–31) | 0.09 (0.09–0.13) |
| Calc | Sonnet 5.5 | hypruse | Hyprland VM · Firefox | 10677 | 1476 | 926 | 5.00 |
| Calc | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 1740 (1299–2376) | 79 (60–99) | 97 (78–117) | 0.55 (0.43–0.70) |
| Forgejo | gpt-6.1-terra | hyprmasq | Hyprland VM | 293 (274–315) | 25 (24–38) | 24 (24–24) | 0.04 (0.04–0.06) |
| Forgejo | gpt-6.1-terra | hypruse | Hyprland VM | 1230 (907–1578) | 115 (103–142) | 75 (67–77) | 0.19 (0.15–0.21) |
| Forgejo | gpt-6.1-terra | computer-use-linux | Hyprland VM | 677 (450–1079) | 73 (63–119) | 52 (43–57) | 0.11 (0.09–0.17) |
| Forgejo | Sonnet 5.5 | hyprmasq | Hyprland VM | 176 (160–190) | 16 (15–18) | 24 (22–25) | 0.08 (0.07–0.08) |
| Forgejo | Sonnet 5.5 | hypruse | Hyprland VM | 14777 | 419 | 428 | 2.66 |
| Forgejo | Sonnet 5.5 | computer-use-linux | Hyprland VM | 1070 (558–1657) | 54 (35–72) | 72 (53–90) | 0.39 (0.21–0.47) |
| Thunderbird | gpt-6.1-terra | hyprmasq | Hyprland VM | 397 (369–446) | 34 (29–61) | 28 (27–29) | 0.06 (0.05–0.09) |
| Thunderbird | gpt-6.1-terra | hypruse | Hyprland VM | 1721 (1336–2196) | 109 (85–142) | 71 (65–99) | 0.19 (0.18–0.24) |
| Thunderbird | gpt-6.1-terra | computer-use-linux | Hyprland VM | 2969 (793–3776) | 124 (92–139) | 95 (84–118) | 0.29 (0.13–0.33) |
| Thunderbird | Sonnet 5.5 | hyprmasq | Hyprland VM | 500 (332–547) | 32 (25–34) | 39 (33–41) | 0.16 (0.12–0.17) |
| Thunderbird | Sonnet 5.5 | hypruse | Hyprland VM | 13984 | 685 | 694 | 3.12 |
| Thunderbird | Sonnet 5.5 | computer-use-linux | Hyprland VM | 2444 (1038–4504) | 90 (65–130) | 108 (83–148) | 0.67 (0.39–1.05) |
| Trace-rack | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 66 (64–100) | 20 (14–24) | 16 (15–18) | 0.03 (0.02–0.03) |
| Trace-rack | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 217 (177–251) | 40 (33–43) | 30 (27–32) | 0.05 (0.05–0.06) |
| Trace-rack | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 270 (180–617) | 52 (44–68) | 42 (38–51) | 0.07 (0.05–0.09) |
| Trace-rack | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 47 (41–54) | 5 (5–6) | 13 (12–13) | 0.02 (0.02–0.03) |
| Trace-rack | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 313 (279–699) | 33 (30–59) | 52 (48–77) | 0.18 (0.16–0.34) |
| Trace-river | gpt-6.1-terra | hyprmasq | Hyprland VM · Firefox | 65 (65–98) | 13 (13–20) | 16 (15–17) | 0.02 (0.02–0.03) |
| Trace-river | gpt-6.1-terra | hypruse | Hyprland VM · Firefox | 177 (140–246) | 45 (28–74) | 27 (24–32) | 0.06 (0.04–0.08) |
| Trace-river | gpt-6.1-terra | computer-use-linux | Hyprland VM · Firefox | 211 (180–285) | 47 (44–82) | 39 (38–47) | 0.06 (0.05–0.09) |
| Trace-river | Sonnet 5.5 | hyprmasq | Hyprland VM · Firefox | 41 (41–55) | 5 (5–6) | 12 (12–14) | 0.02 (0.02–0.03) |
| Trace-river | Sonnet 5.5 | computer-use-linux | Hyprland VM · Firefox | 277 (168–902) | 33 (22–42) | 51 (41–60) | 0.17 (0.12–0.30) |

</details>
<!-- /render -->

## Method

<details><summary>Tasks</summary>

The tasks, their VM, their grading and their measures are those of the [benchmarks](benchmarks.md): the user types in
a terminal on their own workspace, the task's apps are on another, and the agent works through the tool's MCP server
alone. Only the tool differs, and the prompt.

| Task | App | What the agent does |
| --- | --- | --- |
| Form with dropdowns | A questionnaire, in GTK 4 and in its Qt port | Answers 25 questions, checkboxes, sliders, text fields and dropdowns, and presses Submit. |
| Form without dropdowns | The same | The same with 25 questions of the other three kinds. |
| Inkscape | Inkscape | Moves a rectangle from x=120 to x=122 px and saves the drawing. |
| File manager | Nautilus | Renames `report-draft.txt` to `report-final.txt` and moves it into the folder `Archive`. |
| Board-dnd | A web page in Firefox | Drags nine labels onto their parts in a photo of a motherboard, with the page's HTML drag and drop. |
| Board-pointer | The same | The same with pointer events, as a custom widget takes a drag. |
| Calc | Firefox and LibreOffice Calc | Reads three months of revenue of each regional office from reports in Firefox's tabs, and enters them in a sheet. |
| Forgejo | Chromium | Signs in to Forgejo, uploads a file into a repository's folder with a given commit message, and connects a dashboard to the account through its OAuth popup. |
| Thunderbird | Thunderbird | Adds a mail account, then answers a colleague's question with what it finds in the inbox. |
| Trace-rack | A web page in Firefox | Draws a line with a brush along the rack of a mountain railway in a photo. |
| Trace-river | The same | Draws a line along a river's bank where it lies in the shadow of a hill. |

[bench/tasks](../bench/tasks) has each task's prompt, app and grading.

</details>

<details><summary>Scope</summary>

- **Same session:** the agent works in the user's own session, beside them. Cua's separate desktop, a Cua Space,
  is not tested.
- **Cua on Hyprland** ran its opt-in input build, which its documentation calls experimental. The plugin package it
  documents for Arch sends no input of its own: with it, Cua filled text fields and sliders through accessibility,
  but could not tick checkboxes or pick from dropdowns ([run data](comparison/data/omarchy-form-codex-cua-documented.json)). OpenAI's
  computer use offers no desktop tool on Linux.
- **Runs:** six for each task, agent and tool, and seven of hypruse with Sonnet. They describe these runs, not a
  general ranking. The runs were spread over three desktop machines, so their times compare roughly.
- **Models:** Sol and Terra are OpenAI's `gpt-6.1-sol` and `gpt-6.1-terra` in Codex, Sonnet is Claude Sonnet 5.5 in
  Claude Code, all at medium effort. Costs are at the providers' API list prices.

</details>

<details><summary>Reading the figures</summary>

Time runs from the agent's start to its end, the median of the passed runs. Uncached input is the input tokens the
provider did not serve from its cache, summed over a run's requests. Cost is what the requests would cost at the
provider's list prices, cache reads at their lower price, for Codex too, which ran on a ChatGPT subscription. Peak
context is the largest single request. The user was disturbed while the focus was away from their window: the figures
count the passed runs that did, with the median seconds of those runs. Tokens, cost and calls count every run, failed ones too. The tables give the range in brackets, and the
per-run data is in [comparison/data](comparison/data).

</details>

<details><summary>The tools and their setups</summary>

- **hyprmasq:** the runs of the [benchmark set on Hyprland 0.57.0](benchmarks.md), the first six of each task and
  agent at medium effort, with the task's own prompt. The Qt questionnaire ran on a later build, with
  [#102](https://github.com/nyrda/hyprmasq/pull/102) to [#104](https://github.com/nyrda/hyprmasq/pull/104), and its
  Sonnet runs, as each of Cua's, computer-use-linux's and hypruse's in Qt, in Claude Code on a Claude subscription.
- **Cua Driver** 0.38.0, built from its release source as its documentation builds it on Linux, with the plugin's
  input build, on Hyprland 0.56.2: the plugin's input build refuses any other Hyprland, and its driver sees no window
  of Hyprland 0.57.0. Its skill is installed as `cua-driver skills install` installs it and named at the start of the
  prompt. Its server runs with `CUA_DRIVER_RS_ENABLE_WAYLAND=1`, as its Linux guide asks. The user's Chromium starts
  with `--force-renderer-accessibility`, as its browser guide asks, and Cua's server gets `--grant
  existing-profile` for Forgejo. Board, trace and calc ran in Chromium.
- **hypruse** 0.13.0 and **computer-use-linux** 0.7.32 on Hyprland 0.57.0, without hyprmasq's plugin, with their
  defaults. hypruse returns its screenshots as images, its mode for clients that cannot read its files, as the agent
  on the host cannot read the VM's. Terra ran in Codex on a ChatGPT subscription, Sonnet in Claude Code on a Claude
  subscription for computer-use-linux and through the Anthropic API for hypruse. Codex updated itself from 0.165.0
  to 0.166.0 and Claude Code from 2.1.312 to 2.1.314 during these series.
- **Prompt:** for every tool but hyprmasq, the task's comparison prompt, the same for each: the user
  works in another window, has authorized foreground input and focus changes if a tool needs them, and prefers that
  their window is left alone. hyprmasq's runs had the task's own prompt, which says that the tool works in the
  background. [bench's README](../bench/README.md) lists each tool's setup against its documentation.
- **Measured disturbance:** the focus away from the user's terminal, from Hyprland's events, as in the benchmarks.

</details>
