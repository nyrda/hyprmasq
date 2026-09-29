---
name: hyprmasq
description: Drive GUI applications on Hyprland without taking over the user's screen. Launch apps on a hidden workspace, take screenshots of one window or a region of it, click, scroll and type into it, and wait until it has redrawn. Use for GUI testing of an app under development, for checking what a desktop app shows, and for computer use in an application the user names, when the session runs Hyprland with the hyprmasq plugin loaded.
---

# hyprmasq

hyprmasq is a Hyprland plugin. It renders one window to a PNG, and gives each
window its own input socket, whether or not its workspace is visible. The
user keeps working: their cursor, focus and visible workspace do not change.

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

- Always use `hyprctl -j` and check `"ok"` in the reply. `hyprctl` exits 0
  even for errors.
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

To read small text, take a region at full detail:

```sh
hyprctl -j hyprmasq screenshot "$addr" /tmp/zoom.png 0 0 400 60   # X Y WIDTH HEIGHT, logical pixels
```

## Input

`hyprmasq-input --socket "$socket" ACTION...` runs its actions in order:

| Action | Meaning |
| --- | --- |
| `move X Y` | Pointer to X,Y, logical pixels |
| `click [left\|right\|middle]` | Press and release a button |
| `down BUTTON`, `up BUTTON` | For drags: `move 10 10 down left move 200 10 up left` |
| `scroll DX DY` | Smooth scroll in logical pixels, positive DY scrolls down |
| `type TEXT` | Text with the user's keyboard layout, with tags |
| `sleep MS` | Pause between actions |

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
but escape `<` before a word that is a key name, as in `a \<tab> b`. Quote the
text for the shell with single quotes.

Exit codes of `hyprmasq-input`: 0 done, 2 invalid action or text (nothing was
sent), 1 the plugin refused or dropped input (see below).

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
  the app needs longer to react. Wait with a longer QUIET-MS, such as
  `wait "$addr" 1000 4500`, before concluding. `frames` only counts what was
  drawn during the wait itself, which is often 0 because apps redraw quickly.
- Prefer `wait` to `sleep`. Use `sleep` only for delays that are not drawing,
  such as a network request before the app shows anything.

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
   `hyprctl -j clients` by `pid` or `title`. Menus and popups of Wayland apps
   are part of their window and show in its screenshots.
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
| `the server paused input, so some events were dropped` | The target is an X11 window and the user is working in another X11 window of the same X server. Wait and retry later. Use `hyprctl hyprmasq eis open "$addr" force` only if the user allows it: it can move input focus they are using. |
| Screenshot unchanged after input and `wait` | The app may ignore input until it is focused or may not redraw while hidden. Wait longer, or check that the click hit the element in a region screenshot. |
| `no mapped window 0x...` | The window closed or the address is wrong. List `hyprctl -j clients` again. |
| `the keymap cannot type U+...` | The user's layout has no key for the character. Nothing was sent. Use another spelling, or enter it another way in the app. |
| `hyprctl hyprmasq ...` says the command is unknown | The plugin is not loaded. Tell the user. |

X11 menus and tooltips are separate windows. Screenshots of an X11 app do not
include them, and clicks only reach the parts of a menu that lie over the app's
window. Prefer the keyboard in X11 menus.
