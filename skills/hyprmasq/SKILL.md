---
name: hyprmasq
description: Drive GUI applications on Hyprland without taking over the user's screen. Launch apps on a hidden workspace, take screenshots of one window or a region of it, click, scroll and type into it, and wait until it has redrawn. Use for GUI testing of an app under development, for checking what a desktop app shows, and for computer use in an application the user names, when the session runs Hyprland with the hyprmasq plugin loaded.
---

# hyprmasq

hyprmasq is a Hyprland plugin. It renders one window to a PNG, and gives each
window its own input socket, whether or not its workspace is visible. The
user keeps working: their cursor, focus and visible workspace do not change.

## Prefer the MCP tools

If this session has the hyprmasq MCP tools (`windows`, `launch`, `screenshot`,
`act`, `release` and `close`, for example as `mcp__hyprmasq__act`), use them
instead of the shell commands below. One `act` call sends paced input, waits
until the window has redrawn and returns what changed, coordinates are pixels
of the shot you read them from, and every failure says what was sent. Their
descriptions and results explain the rest. What `act` returns:

- What changed since the shot you gave it, or since the last shot of the
  window you got: `shot s7 = s5 + 2 changes`. s7 has the pixels of s5, so keep
  reading coordinates from s5, and pass `s7` or `s5` as `shot`.
- A zoom into each change, at the monitor's scale, listed with the box of
  s5's pixels it shows: `zoom s8 496×80 of s7 at 530,0 248×40`. Read small
  text there, click in it with `shot: "s8"` and its own pixels, and check for
  changes you did not mean, such as keys that reached another widget.
- `shot s7 = s5, no change`, without an image: the window looks as in s5,
  apart from tiny changes, which a line counts, and changes too faint to
  count, such as a hover highlight. After input, the hint says that it may
  have missed; look at s5 before repeating it.
- `N tiny changes left out`: a blinking caret or a few pixels changed.
- `quiet 200 ms except animation at 12,40 300×20`: a small part of the window
  kept redrawing, such as a progress bar, a video or a spinner. The wait did
  not wait for it, and `N changes in animations left out` counts what changed
  there. Look at that box in the shot if it matters, such as a spinner that
  says the page is still loading.
- A failure that comes with a shot is a normal result whose text starts with
  `error CODE:`, so you get its shot; go on from it.
- A full shot, which says why it came instead: without an earlier shot, after
  a resize or a change of popups past the window's edge, for a change of more
  than 35% of the window or too scattered to join into 5 regions, and after 10
  changes shots in a row, each compared with the one before. Read coordinates
  from it from then on.
- When you no longer have the earlier shot, for example after your context was
  compacted, ask for a full shot with `capture: "window"`.

The rest of this skill is the fallback for sessions without them.

## Check that it is available

```sh
hyprctl plugin list | grep -q hyprmasq && command -v hyprmasq-input hyprmasq-launch jq
```

If the plugin is missing, stop and tell the user. Do not load or build it
yourself. `hyprctl` needs `HYPRLAND_INSTANCE_SIGNATURE` from the user's
session.

## The loop

```sh
addr=$(hyprmasq-launch -- foot)                            # 1. start the app hidden
socket=$(hyprctl hyprmasq eis open "$addr")                # 2. its input socket
hyprctl -j hyprmasq wait "$addr"                           # 3. until it has drawn
hyprctl -j hyprmasq screenshot "$addr" /tmp/shot.png       # 4. look
hyprmasq-input --socket "$socket" move 120 40 click        # 5. act
hyprctl -j hyprmasq wait "$addr"                           # 6. until it has redrawn
hyprctl -j hyprmasq screenshot "$addr" /tmp/shot.png       #    look again, and repeat
hyprctl hyprmasq eis close "$addr"                         # 7. clean up
```

- Always use `hyprctl -j` and check `"ok"` in the reply. With `-j`, `hyprctl`
  exits 0 even for errors. Plain `hyprctl hyprmasq ...` exits 7 when the reply
  is an error. Both exit 6 when Hyprland does not answer within 5 seconds.
- Screenshot paths must be absolute and cannot contain spaces. Use a fresh
  path per step, or look at the file before overwriting it.
- Look at a screenshot before every action that depends on the screen. Never
  click at coordinates you have not seen.

## Coordinates

Pointer input uses window-local logical pixels, starting at the window's top
left. Screenshots are taken at the monitor's scale. The screenshot reply
tells you how to convert:

```json
{"ok":true,"path":"/tmp/shot.png","width":1280,"height":960,"scale":2,"region":{"x":0,"y":0,"width":640,"height":480}}
```

A pixel at PX,PY of the PNG is at `region.x + PX / scale`,
`region.y + PY / scale` for `move`. If your image viewer downscaled the PNG,
convert from the viewer's size back to `width` and `height` first.

With `"resizing":true` in the reply, the window has not yet redrawn at a new
size, for example after a window next to it opened or closed, so the image
shows a stale frame. Do not click from it. Wait until the window has caught
up, then take a new screenshot:

```sh
shot=$(hyprctl -j hyprmasq screenshot "$addr" /tmp/shot.png)
jq -e .resizing <<<"$shot" >/dev/null &&
  hyprctl -j hyprmasq wait "$addr" since "$(jq .commits <<<"$shot")" quiet 200 deadline 4500
```

With `"starved":true`, the window is on a hidden workspace and waits for a
frame callback that nothing has sent it, so the image may show an empty or
unfinished window, as of an app that started there. A `wait ... since` with
`expect` sends it frame callbacks; take the screenshot again after it.

To read small text, take a region at full detail:

```sh
hyprctl -j hyprmasq screenshot "$addr" /tmp/zoom.png 0 0 400 60   # X Y WIDTH HEIGHT, logical pixels
```

## Input

`hyprmasq-input --socket "$socket" [--json] ACTION...` checks all of its
actions, then runs them in order:

| Action | Meaning |
| --- | --- |
| `move X Y` | Pointer to X,Y, logical pixels |
| `click [BUTTON [COUNT [MODS]]]` | Click `left`, `right` or `middle` COUNT times (1 to 3) with modifiers held: `click left 2` double-clicks, `click left 1 ctrl` ctrl-clicks |
| `drag X1 Y1 X2 Y2 [BUTTON]` | Press at X1,Y1, move there in steps to X2,Y2, release |
| `down BUTTON`, `up BUTTON` | Press or release a button |
| `wheel DX DY` | Whole wheel notches, positive DY scrolls down |
| `scroll DX DY` | Smooth scroll in logical pixels, positive DY scrolls down |
| `keys CHORD [REPEAT]` | A key or shortcut by name: `keys enter`, `keys ctrl+l`, `keys down 5` |
| `text TEXT` | Text exactly as given, with the user's keyboard layout |
| `type TEXT` | Text with tags such as `<enter>`, see below |
| `sleep MS` | Pause between actions |

Key names are `ctrl`, `shift`, `alt`, `super`, `enter`, `esc`, `tab`,
`space`, `backspace`, `delete`, the arrows, `home`, `end`, `pageup`,
`pagedown`, single characters and XKB keysym names such as `F5`. A letter is
its key without Shift: use `shift+a` for a capital.

Input is paced like a person's: clicks hold the button 50 ms, keys are typed
10 ms apart, and actions are 50 ms apart. Batch only actions whose targets you
have seen. A menu, dialog or page that opens needs a new screenshot before you
click in it: wait, take a screenshot, then go on. Keys you know may still
follow in the same run, such as `keys down keys enter` to pick a menu entry.

Points must lie in the window or right of or below it; points left of or above
the window are refused. Nothing is sent when any action, key name or
character is invalid.

A character without a key of its own, such as `^` on a German layout, is typed
as a dead key sequence, which the app composes with the Compose table of its
own locale. When the app may run in another locale than you, pass its
`LC_ALL`, `LC_CTYPE` or `LANG` from `/proc/<pid>/environ` as
`--locale NAME`, or check such characters in a screenshot.

Tags in `type` text:

| Tag | Result |
| --- | --- |
| `<enter>` `<tab>` `<esc>` `<backspace>` `<delete>` `<space>` | That key |
| `<up>` `<down>` `<left>` `<right>` `<home>` `<end>` `<pageup>` `<pagedown>` | That key |
| `<F5>`, `<Return>`, any XKB keysym without a character | That key |
| `<down:5>` | A key 5 times, up to 100 |
| `<shortcut:ctrl+l>`, `<shortcut:ctrl+shift+t>` | A shortcut: `ctrl`, `shift`, `alt`, `super` |
| `<wait:MS>`, `<settle>` | Pause, `<settle>` is 300 ms |

`\<` types a literal `<`. Unknown tags such as `<div>` are typed as they are,
but escape `<` before a word that is a key name, as in `a \<tab> b`, or use
`text`, which has no tags. Quote the text for the shell with single quotes.

Exit codes of `hyprmasq-input`: 0 done, 2 invalid action, key name or text
(nothing was sent), 1 stopped after it started (see below). With `--json` it
prints how far it got:

```json
{"ok":false,"done":1,"total":2,"failedIndex":1,"delivery":"partial","progress":{"keys":39},"error":{"code":"input_paused","message":"the server paused input, so some events were dropped"}}
```

`delivery` is `none` (nothing sent, safe to fix and retry), `complete`,
`partial` (actions before `failedIndex` arrived, the failed one maybe in part:
`progress.keys` keys of it) or `uncertain`. Never repeat `partial` or
`uncertain` input without taking a screenshot first.

## Waiting

`hyprctl -j hyprmasq wait "$addr" [QUIET-MS [TIMEOUT-MS]]` answers once the
window has drawn nothing new for QUIET-MS (default 200), or after TIMEOUT-MS
(default 3000, at most 4500):

```json
{"ok":true,"settled":true,"waited":230,"frames":0,"framesSinceInput":3}
```

- `"settled":false` means it was still drawing: a loading page, an animation.
  Wait again or take the screenshot anyway.
- `framesSinceInput` counts what the window drew since your last input. `0`
  means your input has not changed anything yet: the click may have missed, or
  the app needs longer to react. Before concluding, wait for a change with
  `wait ... since` and `expect` (below): this plain `wait` sends a hidden window
  no frame callbacks, which it gets only for a second after input, so a
  longer QUIET-MS does not help an app that reacts later. `frames` only counts
  what was drawn during the wait itself, which is often 0 because apps redraw
  quickly.
- Prefer `wait` to `sleep`. Use `sleep` only for delays that are not drawing,
  such as a network request before the app shows anything.

To wait for a change since a screenshot, pass its `commits` to `wait ... since`.
The window needs an open socket. While this wait runs, a hidden window gets
frame callbacks, so it keeps drawing:

```sh
hyprctl -j hyprmasq wait "$addr" since 123 expect 1000 quiet 200 deadline 4500
```

```json
{"ok":true,"ended":"quiet","commits":131,"redrawObserved":true,"commitsSince":8,"quietMs":200,"elapsedMs":412}
```

`ended` is `quiet` (settled), `no_redraw` (nothing drawn within `expect`, so
look before repeating the input), `timeout` (not settled by `deadline`: still
drawing when `redrawObserved` is true, otherwise nothing drawn yet) or
`window_closed`. Keep `deadline` at 4500 or below: `hyprctl` gives up after 5
seconds, and `expect` beyond `deadline` does not wait longer. The screenshot
reply and the wait name the socket in `socketId`; if they differ, the socket
was opened again and the screenshot's `commits` does not apply, so take a new
screenshot.

## GUI testing

Use this for an application you are developing or testing.

1. Launch every window you test with `hyprmasq-launch`. It prints the address
   of the command's first window and leaves the user's screen alone.
   - `--workspace NAME` puts test windows together, `--timeout SECONDS` for
     slow starts (default 30).
   - Apps that pass the request to an instance that is already running open
     their window elsewhere, and the launch times out. Start a separate
     instance: `firefox --new-instance -P test`, `foot` instead of
     `footclient`, `chromium --user-data-dir=/tmp/profile`.
2. Find other windows of the app, such as dialogs, in
   `hyprctl -j clients` by `pid` or `title`. Windows that your input opens
   open on the workspace of the window they came from, without taking the
   user's focus. Menus and popups of Wayland apps are part of their window and show
   in its screenshots.
3. Assert on what you see in screenshots, and keep them as evidence for the
   user.
4. Close your windows when you are done:
   `hyprctl dispatch 'hl.dsp.window.close({ window = "address:'"$addr"'" })'`.

## Computer use on the user's windows

Use this when the user asks you to work in an application they already have
open.

- Drive only windows the user named. Find them with
  `hyprctl -j clients | jq -r '.[] | "\(.address) \(.workspace.name) \(.class) \(.title)"'`
  and confirm with the user when more than one matches.
- Do not close or move their windows, and do not open sockets on other
  windows.
- On a visible workspace, a light blue cursor shows the user where your
  pointer input lands.
- Ask before anything irreversible: sending, deleting, buying, submitting.

## When input does not arrive

| Symptom | Meaning and fix |
| --- | --- |
| `the server paused input, so some events were dropped` | The target is an X11 window and the user is working in another X11 window of the same X server. The input before the pause arrived, `progress.keys` keys of a text: take a screenshot before going on, later. Use `hyprctl hyprmasq eis open "$addr" force` only if the user allows it: it can move input focus they are using. |
| `... is outside the pointer regions ...` | The point lies left of or above the window, or beyond its monitor. Nothing was sent. Click inside the window, or use the keyboard. |
| A menu or dropdown is gone in the next screenshot | The emulated focus ends when `hyprmasq-input` exits, and the menu closed with it. Open it and pick the entry in one run, with `keys`, or take a lease first: `hyprctl -j hyprmasq lease "$addr" 60000 agent` keeps the focus for 60 s (Wayland windows only), and `hyprctl -j hyprmasq lease "$addr" release agent` ends it. |
| Screenshot unchanged after input and `wait` | The app may ignore input until it is focused or may not redraw while hidden. Wait longer, or check that the click hit the element in a region screenshot. |
| `no mapped window 0x...` | The window closed or the address is wrong. List `hyprctl -j clients` again. |
| `the keymap cannot type U+...` | The user's layout has no key for the character. Nothing was sent. Use another spelling, or enter it another way in the app. |
| `the server closed the connection, ...` | The window closed, maybe because of the input, or another sender took over its socket. List `hyprctl -j clients` again. |
| `hyprctl hyprmasq ...` says the command is unknown | The plugin is not loaded. Tell the user. |

X11 menus and tooltips are separate windows. Screenshots of an X11 app do not
include them, and clicks only reach the parts of a menu that lie over the app's
window. Prefer the keyboard in X11 menus.
