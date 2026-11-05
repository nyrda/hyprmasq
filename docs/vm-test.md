# VM test

`nix/hyprmasq-vm-test.nix` boots NixOS with Hyprland on a headless 640×480
output, installs the plugin through the NixOS module, and runs the test script
that it puts together from `nix/vm-test/`:

- `common.py` starts Hyprland, and has the helpers that more than one area
  uses.
- Each area has a file of its own, and they run in the order of `areas.json`,
  in one `try` block whose `finally` copies the logs into the result, also
  after a failure. The build indents each area's code for that block, so no
  string in an area may span lines, apart from docstrings.
- `guest/` has the scripts that the test runs in the VM, which the VM has in
  `/etc/hyprmasq-test`.
- `check.py` checks the areas as part of the test's build, and the tables
  below against them.

The test's own Wayland clients are in `nix/` and share `nix/probe-common.h`:
`focus-probe.c`, whose modes it lists when run without arguments,
`dnd-probe.c`, `clip-probe.c`, `swapchain-probe.c`, `suspend-probe.c`,
`user-input.c` and, for the [containers](#containers), `parent-seat.c`. They log what they get to stdout, most of it stamped with
`CLOCK_MONOTONIC` in milliseconds, and exit with status 2 on arguments they do
not take.
`qt-probe.py`, a Qt 6 app in PySide6, logs the same way. `combo-probe.py` is a
GTK 3 app on X11 with a combo box, or a scrolled column, which logs alike, and
so does `slider-probe.py`, a slider in Qt 6, GTK 3 or Tk. Chromium opens
`guest/select.html`, a page with a `<select>`, `guest/scroll.html`, a long
page, and `guest/clip.html`, a textarea that logs its pastes, which log to its
console.

`hook-taker.cpp` is a plugin that hooks one function that hyprmasq hooks
too, as another plugin would. `hook-taker.nix` builds it three times, as
`hyprmasq-hook-taker-seat.so` for `CSeatManager::setKeyboardFocus`,
`hyprmasq-hook-taker-xwm.so` for `CXWM::activateSurface` and
`hyprmasq-hook-taker-idle.so` for `CIdleNotifyProtocol::setInhibit`, whose
directory the VM has in `/etc/hyprmasq-hook-taker`. Nothing installs it.

The VM also has `hyprland-dialog`, from `hyprland-qtutils` of the flake's own
nixpkgs, without which Hyprland does not watch for apps that stop answering its
pings. Hyprland's nixpkgs no longer has `hyprland-qtutils`, and the
`hyprland-dialog` of its successor, `hyprland-guiutils`, crashes in the VM
before it maps its window. The VM also has the form task's GTK 4 questionnaire
as `hyprmasq-bench-form`.

## Running it

The gate for PRs is the whole test, which `./dev.sh gate` runs: in
[containers](#containers) on a machine that allows it, and otherwise in the VM,
`nix build .#checks.x86_64-linux.vm-test`, which is what `./dev.sh vm-test` runs.
To iterate on some areas, name them.
They run in order with the areas they require:

```sh
nix-build nix/hyprmasq-vm-test.nix --argstr areas "mcp unload"
```

The test copies the screenshots and logs into the build result, for example
`result/hyprmasq-green.png` after `nix build .#checks.x86_64-linux.vm-test -L`.

`./dev.sh vm-test-interactive` opens the test's interactive driver.

## Containers

`nix/container-test.nix` runs the whole test in systemd-nspawn containers, one
build per group of areas, which Nix runs at once up to `max-jobs`. Each group
also runs `core`, and the evaluation fails when a group leaves out an area or
names one twice. With seven groups, it takes about a sixth of the VM test's
time, and each container uses about 0.5 GiB of its own memory, where a VM
reserves 8 GiB:

```sh
./dev.sh container-test
nix-build nix/hyprmasq-vm-test.nix --arg container true --argstr areas "mcp unload"
```

It needs a GPU with a render node and these Nix settings on the host, which
give builds a range of user ids and the builds that ask for it the render node:

```nix
{
  nix.settings = {
    experimental-features = [ "auto-allocate-uids" "cgroups" ];
    auto-allocate-uids = true;
    extra-system-features = [ "uid-range" "gpu" ];
  };
  programs.nix-required-mounts = {
    enable = true;
    allowedPatterns.gpu = {
      onFeatures = [ "gpu" ];
      paths = [ "/dev/dri/renderD*" "/sys/dev/char/226:*" ];
      unsafeFollowSymlinks = true;
    };
  };
}
```

Aquamarine's headless backend has no allocator, so Hyprland cannot run on it
alone, and a container has no display of its own. So `common.py` starts labwc
on its headless backend, rendering on the host's render node, and Hyprland as
its Wayland client. The test's monitor is the same headless output as in the VM.
Three things stand in for what the VM has:

- labwc is built without XWayland, which would take the X display of Hyprland's.
- `hyprmasq-parent-seat` holds a virtual keyboard and pointer on labwc's seat,
  which Hyprland's Wayland backend then has as its own. It also sends the user's
  keys, as QEMU's `sendkey` does: the areas call `sendkey`, `send_key` and
  `send_chars` of `common.py`, and they reach Hyprland as a real keyboard's.
- Hyprland's output in labwc mirrors the test's monitor. labwc sends keys to its
  window, and as a mirror it stays out of Hyprland's layout and list of monitors.

Timing differs from the VM's, as rendering runs on a GPU and programs start
faster, so the two can find different races. A subtest that fails in one of them
and not in the other is worth a look at the product first. A change to how the
test runs in either of them passes both.

## What it cannot cover

The VM runs Hyprland on its headless backend, which goes on rendering when
DPMS turns a monitor off. The test therefore cannot show that windows of a
visible workspace then get frame callbacks only from the plugin. Check a
change there on a real monitor: with the monitor off, a terminal on the
visible workspace that prints on its own redraws during `wait … since`.

## Areas

The areas share one VM and one Hyprland, and a subtest may use the windows and
other state of earlier subtests of its area. An area that uses the state of
another area requires it in `areas.json`. `check.py` fails when an area reads a
name that only an area it does not require defines. A requirement on state in
the VM, such as a window or a log, is declared by hand.

| Area | Needs | Covers |
| --- | --- | --- |
| `core` | | Screenshots of the colored foot windows that most areas use, the socket lifecycle and input to a hidden shell. Every area needs it, some through another. |
| `typing` | `core` | `type` under a German keymap, and through the Compose table of the target's locale. |
| `input` | `core` | `hyprmasq-input`: pacing, actions, JSON results, its cleanup, and input that closes its window. |
| `waits` | `core` | Both forms of `wait`, `state`, error codes, the commit counter, frame callbacks for hidden windows, and an unload during a wait. |
| `shots` | `core` | Screenshot sizes, detail images, popups, edge pixels, the pointer device, resizes, starved windows and animations. |
| `leases` | `core` | Focus leases, and what a sender's disconnect releases. |
| `launch` | `core` | `hyprmasq-launch`, a shared Wayland connection, and windows that emulated input opens. It starts the log of Hyprland's events that `shared-focus` reads. |
| `shared-focus` | `core`, `launch` | The agent's input beside the user's: modifiers, shortcuts, click to focus, held buttons and keys, an input method, activation. |
| `drag` | `core` | Popup, move, resize and drag protections, and private drag-and-drop. |
| `selections` | `core` | The clipboard and primary selection of hidden apps and of the user, also X11's. |
| `xwayland` | `core` | XWayland and xwayland-satellite windows: shots, input, the X11 focus rule and activation. |
| `x11-menus` | `core` | The menus of X11 apps on a hidden workspace, which open in windows of their own: GTK 3's and the popup of Chromium's `<select>`, and `hyprmasq-mcp`'s `close`, which refuses X11 menus and X11 windows that take no close request. |
| `cursor` | `core` | The virtual cursor and the click ripple. |
| `mcp` | `core` | `hyprmasq-mcp`'s tools, launch, act, coordinates, refusals, waits and leases. Its server, shell and probe serve `mcp-shots`. |
| `new-windows` | `core` | Where windows that input opens go, and how `hyprmasq-mcp` reports them, also late ones and those of the user's input. |
| `mcp-shots` | `core`, `mcp` | `hyprmasq-mcp`'s concurrency and cancellation, captures, changes and full shots, image sizes, resizes, starved and animated windows, input that closes its window, and the server's exit. |
| `anr` | `core` | Hyprland's not-responding dialog, which the VM's `hyprland-dialog` logs: for a stopped app, and never for hidden apps that captures and input drive, one that waits for its dmabuf buffers as Mesa's swapchains do and the form task's GTK 4 questionnaire. |
| `settle` | `core` | How long `act` waits for a hidden GTK 4 app: the form task's questionnaire, whose overlay scrollbar fades in at each act, settles once its widgets have drawn. |
| `recording` | `core` | `hyprmasq-mcp`'s recordings of `act` calls as slides and as video, with the settings that the NixOS module writes, and the recording that a session leaves running. |
| `suspended` | `core` | Hidden windows that Hyprland tells they are suspended: a Qt 6 app, which commits no frame while it is suspended, is shown while driven and draws each act's input and a resize, and every way a hold ends gives it Hyprland's state back, also to a toplevel that unmaps and maps again. |
| `qt-popups` | `core` | A click after the Enter that picks an item of a Qt dropdown: it reaches the window once the list has closed, and `act` holds it back when Enter opened the list again, as it does with the platform theme of GTK. |
| `key-pace` | `core` | Repeated plain keys, with every press counted on Qt 6, GTK 3 and Tk sliders. |
| `scrolling` | `core` | `act`'s scroll at the end of a batch, which comes after the changes of the input before it or is held back, a dropdown that takes the wheel, and scrolls on X11 that move by every notch. |
| `paging` | `core`, `input` | Measured page scrolls, overlap, edit verification and controls that take the wheel. |
| `unload` | `core`, `waits`, `suspended` | Unloading and loading the plugin again, with pending waits on the busy window of `waits` and the Qt window of `suspended`, selections and screenshot writes. |
| `idle` | `core` | An idle client, hypridle, and DPMS: windows that map and unmap on a hidden workspace and the agent's input end neither, real input does. |
| `hooks` | `core` | Sharing Hyprland with another plugin that hooks a function of hyprmasq's: hooks that hyprmasq installs only while input is in use, also by two senders at once, refusals that name the function, and a hook held at load. |
| `compositor-log` | `drag` | Hyprland's log after its exit, with the serials of `drag`'s protections subtest rejected. It ends Hyprland, so it runs last. |

## Subtests

The tables list the subtests of each area in the order they run, by their
names in the code. `nix build .#checks.x86_64-linux.vm-test-docs`, or
`python3 nix/vm-test/check.py --docs docs/vm-test.md`, fails when they
and the code drift apart.

### `core`

| Subtest | Checks |
| --- | --- |
| screenshots on hidden workspaces, colors and orientation | Red, green and blue foot windows on hidden workspaces have the right colors, and the first text row is at the top. |
| screenshots are encoded while Hyprland keeps running | While a screenshot waits for the reader of a FIFO that nobody reads yet, which the test sees in `/proc` as a `hyprmasq-job` thread in `poll`, Hyprland answers other requests. The reply comes once the file is read. A `--batch` with two screenshots and another command answers all three, in plain text and JSON. |
| a screenshot that Hyprland waits for writes regular files, and refuses anything else at once | Through `nix/hyprctl-caller.cpp`, a plugin that passes a request on through `HyprlandAPI::invokeHyprctlCommand`, a screenshot to a FIFO without a reader is refused at once with `unsupported`, and the FIFO stays unopened. A screenshot to a new file and over an existing one is written. Hyprland takes no request through `invokeHyprctlCommand` for a batch, since it puts the format before the command. |
| screenshot regions in logical pixels | A region at the top left of a green `foot` window contains its text and regions at the bottom and right do not. The reply reports the scale and the region, cut to the window at its edge. Invalid regions fail without writing a file. At scale 2 the same logical region has twice the pixels, and at scale 1.25 it widens to whole device pixels and reports the region captured. |
| socket lifecycle | Paths in the instance's directory, named by the signature without its commit hash, so that they leave 57 bytes of `sun_path` for `XDG_RUNTIME_DIR`, with `0700` directories and a `0600` socket. Also `eis list`, and removal on `eis close` and when the window closes. |
| eis close takes an address: prefix, as open does, and eis list answers without sockets | `eis close` takes `address:0x…` as `eis open` does, and gives `no_socket` naming the address once the socket is gone. Without sockets, `eis list` prints `no open sockets` and its JSON is `{"ok":true,"sockets":[]}`. |
| hidden shell input, focus stability and frame callbacks | Typing into a foot shell on a hidden workspace runs the command, the active window and workspace stay unchanged, and the window repaints. |

### `typing`

| Subtest | Checks |
| --- | --- |
| typing tags and characters with the active keymap | Under a German keymap, `type` into a hidden shell produces `zy@€äß|{^`, a shortcut on a key that the layout moves works, named keys, keysyms, repeat counts and `<settle>` edit the line, and escaped and unknown tags are typed as text. Invalid text fails before any key is sent, and so does `text` with 40 times `^`, 80 keys there, under a `--budget` that 40 keys would fit, with `too_long` and `paceMs` 1190. |
| typing through the Compose table of the target's locale | Under a German keymap, `ć` is the dead acute and `c`, which the Compose table of `pt_BR.UTF-8` makes a `ç`. Into a foot in `pt_BR.UTF-8`, a sender in `en_US.UTF-8` types `ć` as `ç` with its own table. With `--locale pt_BR.UTF-8`, `çé` arrives as typed, `compose` names the locale, and `ć` is `cannot_type` naming the table. A sender in `pt_BR.UTF-8` cannot type `ć` into a foot in `en_US.UTF-8` with its own table, but can with `--locale en_US.UTF-8`, and with `--locale xx_XX.UTF-8`, which has no table, through that of `en_US.UTF-8`, which `compose` and stderr report. Text without Compose reports `compose` `null`. |

### `input`

| Subtest | Checks |
| --- | --- |
| hyprmasq-input paces clicks and key chords and reports JSON | In `wev` on a hidden workspace: a click presses 50 ms after the move before it, the gap between actions, and holds the button 50 ms, the clicks of a double click are 80 ms apart, and `--no-pace` sends them at once. A chord presses its modifiers first and the key 10 ms later, holds it 15 ms and releases in reverse. Aliases and keysym names ignore case, and a capital letter is its key without Shift. Repeats of a key without modifiers have a 1 ms gap between distinct press/release pairs, and those of a chord with modifiers 15 ms apart. Actions are 50 ms apart. The mixed-key batch reports a resolved budget of 577 ms, pinning the sender's pacing alongside the Go estimates. Each run reports complete delivery in `--json`. |
| hyprmasq-input scrolls in wheel notches and drags in steps | `wheel` sends one notch per frame, 20 ms apart, on both axes. A `path` of two points, a drag, presses at least 30 ms after its first motion, moves in steps of at most 10 pixels over at least 200 ms, and releases at the end point. |
| hyprmasq-input moves through the points of a path in one press, or without one | In `wev`: `path` through four points presses once, at least 30 ms after its first motion, moves straight along the segments in 50 steps of at most 10 pixels, through every point, and releases at the last one, after at least 80% of its paced time. With `none`, a path through three points moves the pointer in 22 steps along its segments, and no button goes down. |
| hyprmasq-input waits at a sync for its caller's answer, at least MIN and at most MAX | `keys a sync MIN MAX keys b` into `wev` prints `{"sync":1}` before its JSON result, and goes on after MIN when its stdin is closed, after MAX without an answer, and only at the answer with its number, not at one with another number. |
| hyprmasq-input types 2000 characters of paced literal text exactly into a hidden shell | `text` types tags, escapes, quotes, a tab and a line break as they are, and 2000 random characters exactly, into hidden shells. |
| hyprmasq-input sends nothing for an invalid batch | Unknown key names, untypeable characters, a click count of 4, a malformed point, a point outside the pointer regions and a button code below `BTN_MOUSE` exit 2 with delivery `none`, the failed index and its code, and `wev` gets nothing. |
| hyprmasq-input releases what it holds at the end and on SIGTERM | A button pressed by the last action is released when the sender ends. SIGTERM during a sleep releases the held button and reports `cancelled` with partial delivery. SIGTERM during a two-point `path` with Ctrl releases the button before Ctrl. |
| hyprmasq-input reports within a second of SIGTERM while the plugin does not answer | While Hyprland is stopped, SIGTERM to a sender that holds a button makes it release the button and report `cancelled` with uncertain delivery about a second later. The plugin takes the release once Hyprland runs again. |
| hyprmasq-input follows a replaced pointer | A window resized during a sleep replaces the pointer device. The sender follows it and the later click lands. |
| hyprmasq-input stops a long text on SIGTERM, reports its keys and leaves nothing pressed | SIGTERM during a long text reports `cancelled`, partial delivery and `progress.keys` equal to the keys `wev` received, which are exactly the start of the text, and no key stays pressed. |
| hyprmasq-input follows a pointer replaced in the middle of a batch | Resizes while motion and wheel notches are being sent replace the pointer. The sender either completes, with every point delivered and no gap of a second, or stops with `unconfirmed` and partial delivery. |
| hyprmasq-input reports input_paused for an X11 window while another X11 window is active | Typing into a hidden `xterm` while another X11 window becomes active gives `input_paused`, partial delivery, and `progress.keys` equal to the keys the two windows received. |
| hyprmasq-input confirms input that closes its own window | Ctrl+D into `cat` closes foot while `hyprmasq-input` still sends. Three times, the result is partial delivery with the chord or its key confirmed and the following sleep stopped, or, when foot closes while the key is held, uncertain delivery with nothing confirmed and the key counted as sent. |
| hyprmasq-input counts an action cut off by its window's close with only releases left as done | Escape and a left click that close focus-probe's window while the key or the button is held, each followed by a 5-second sleep: the closing action counts as done, and the run fails at the sleep with partial delivery within 4 seconds. A run whose release meets the close on a busy host, which the plugin drops, is partial or uncertain at the closing action and is repeated, up to three times. |
| hyprmasq-input reports a connection that another sender takes over during a burst | A second sender connects while the first sends text without pacing, three times. The first one exits 1 with `unconfirmed` in its JSON, and its delivery is partial with `progress.keys` equal to the keys the shell got, or uncertain with at least those. |
| hyprmasq-input never counts input that its closing window dropped | Escape closes focus-probe's window while `hyprmasq-input` sends text after it without pacing, three times. A partial delivery counts exactly the keys the probe got, an uncertain one at least those. |
| CLI help, versions and argument errors | Both installed tools print help and version on stdout without a compositor connection. Help descriptions align within each section and lines fit within 80 columns. Usage errors exit 2 with a reason and help hint on stderr. Input errors retain their JSON outcome. Missing option values, empty batches, invalid timeouts and workspace names are rejected. |
| input refuses held-button overflow and releases its presses | A batch can repeat a press with 64 held buttons. A 65th distinct press fails with `too_many_held` and partial delivery, releases tracked input, and leaves the socket usable without changing focus. |

### `waits`

| Subtest | Checks |
| --- | --- |
| waiting until a window has settled | `wait` answers after typing into a hidden shell with the frames it drew, settles an idle window after the quiet time without frames, and times out on a visible window that keeps drawing. Invalid arguments fail. |
| input tool version | `hyprmasq-input --version` reports the package version from `VERSION` and exits successfully without a socket. |
| plugin version and window state | `version` reports the package version from `VERSION` and interface 5, and the module installs `hyprmasq-mcp` of that version, with the package's own `hyprmasq-input` on its `PATH`. `state` reports the address, `stableId`, size, that it is not resizing, scale, socket, commit counter, an empty drag record and capabilities of a hidden window and of the focused one, and no `x11`, as for every Wayland window. |
| errors carry stable codes | Malformed commands, unknown windows, missing sockets and invalid `wait`, `screenshot`, `state` and `cancel` arguments give the documented codes, and plain-text errors start with `error:`. |
| the commit counter rises on redraw, never falls, and sees popups open | It rises after typing, never falls, and the screenshot's value lies between readings before and after. It counts a popup that opens and closes without input, including the popup's first buffer. |
| every socket has its own id, which the replies with its counter carry | `state`, `eis list`, `eis open`, screenshots and `wait … since` name the window's socket by its id, and a socket opened again gets another id and a counter that starts again. |
| waiting for a change since a commit count | `wait … since` ends `quiet` after typing, `no_redraw` for an idle window, at once for a change before the wait, also with `ignore animations`, `timeout` for a hidden window that keeps drawing, `cancelled` through `cancel` by its id, and `window_closed` when the window closes. In a batch with `plugin load`, the wait answers in its place, next to `plugin load`'s reply, and Hyprland keeps answering while it runs. |
| a wait for a change without a deadline answers before hyprctl gives up | `wait … since` with `expect 10000` and no `deadline` on an idle window ends `timeout` after 4500 to 4900 ms, before `hyprctl` gives up after 5 seconds. |
| a hidden window keeps drawing during a long wait | Through Hyprland's socket, a wait with a 2-second quiet time keeps a hidden shell drawing for the 3 seconds it prints after the last key, and answers once it is quiet, later than `hyprctl` would wait. |
| a hidden window keeps drawing while a sender pauses between inputs | A hidden shell that keeps printing goes on drawing while a connected sender sleeps for more than a second after its input, and stops once the sender has gone. |
| a wait keeps a window drawing that is hidden while it runs | A shell that keeps printing, visible when a `wait … since` starts and hidden 0.3 s later, goes on drawing until the wait times out. |
| unloading answers pending waits with code unloaded | A pending `wait … since` answers with code `unloaded`, and the reloaded plugin has no sockets. |
| unloading removes the socket directories that the plugin made, while nothing else is in them | The unload removes the instance's socket directory and `$XDG_RUNTIME_DIR/hyprmasq` that the plugin made, each only while nothing else is in it: another instance's directory keeps the shared one, and another program's file keeps the instance's. |

### `shots`

| Subtest | Checks |
| --- | --- |
| screenshots fit within a maximum size, rendered at that size | `max` halves, quarters and fits to a fifth, never enlarges, fits a region from its origin with a widened size, and reports `scale` as image pixels per logical pixel, also at monitor scales 2 and 1.25. Invalid `max` and `popups` arguments fail without a file. |
| one render gives an image and its detail image, also as raw pixels | One render with `detail` writes a shrunk image and, as raw pixels, a detail image at the monitor's pixels, or at one pixel per logical pixel at scale 2 with its `max`. An image with as many pixels gets none. `raw` writes the image's pixels, and bad `detail` and `raw` arguments give `invalid_arguments`. |
| popups outside the window are captured and get pointer input | A popup of `focus-probe` that reaches past the right and bottom edges is in the screenshot with `popups`, transparent around it, and without it only where it lies over the window. Regions with `popups` are cut to the box. The pointer has the window and the areas right of and below it as regions. A click outside the window reaches the popup at its own coordinates and gives the window's own surface the keyboard focus before the button, and a point outside both is clamped to the window, on the last 1/256 of a logical pixel before its edge. |
| a point in the last pixel of the right and bottom edges keeps its fraction | On a 1920x1080 monitor at scales 2 and 1.5, a click at the last column and row of a native shot of a 640x480 and a 641x481 focus-probe window, and at those of the default 1280-pixel overview of the odd window at scale 2, arrives at region origin + shot pixel / scale within wl_fixed's 1/256 of a logical pixel, inside the window's last pixel. A click beyond the window arrives on the last 1/256 before its right and bottom edges, and one on a popup beyond the window reaches the popup at its own coordinates. |
| a region with popups can start above and left of the window | With `popups`, a popup above and left of the window starts the region at negative coordinates, and regions there are cut to the box. Its part over the window takes a click. `hyprmasq-input` refuses a point above and left of the window before sending anything. |
| the pointer device follows resizes and moves | A resize and a move each replace the pointer device with regions for the new geometry, and a click reaches a part of the window that exists only since the resize. |
| a window that has not caught up with a resize says so in its state, shots and waits | A focus-probe window whose tiled neighbour closes while it is hidden holds back the configure for its new size, unacked or acked and not drawn: `state`, the screenshot reply and a `wait … since` that times out say `resizing`, and the shot has the new size but shows the frame of the old one. A wait that runs when the probe answers ends `quiet` only after it has drawn, and then nothing says `resizing`. A window that acked a configure of the size it has drawn, without a commit, is not resizing. |
| a hidden window that waits for frame callbacks is starved until hyprmasq sends it some | focus-probe's frames mode, started on a hidden workspace, draws only in answer to frame callbacks, one at a time: a blank frame, then its content at the third callback. Hyprland may answer the first as it maps the window. `state` and the screenshot reply say `starved`. A screenshot answers one callback and shows the blank frame, and the probe asks for the next. Opening the socket sends none. A `wait … since` lets the probe draw its content, after which nothing says `starved`, although the content asked for another callback. |
| a hidden window that draws a change on its own after a wait is outdated | A launched foot on a hidden workspace with an open socket, settled by a `wait … since`, is neither starved nor outdated. Once its command starts to print a line every 200 ms, it draws the first and waits for a frame callback: `state` and the screenshot reply say `outdated`, not `starved`, and its counter stands still for a second. A `wait … since` lets it draw again. |
| a popup of a window with an xdg geometry offset gets input where shots show it | For a window with a margin outside its xdg window geometry, as client-side shadows have, a click where the shot shows its popup reaches the popup at that point, and a click beside the popup reaches the window. |
| shots taken while Hyprland animates a window's size show the window at the size input uses | With slow window animations, a focus-probe window and an `xev` window pop in on a visible workspace. While the screen still shows them smaller, their shots have their size and show the probe's green edge and xev's inner window where they lie once the animation has ended, and `state` and a `wait … since` that ends `quiet` say that they are not resizing. A point at the edge in the shot reaches the probe there. |
| input reaches the popups of a moving window where shots show them | A click on the popup of a window that moves slowly reaches the popup at the point where the shot shows it, while Hyprland's own hit testing would not yet place the popup there. |

### `leases`

| Subtest | Checks |
| --- | --- |
| a focus lease keeps the emulated focus between inputs | Without a lease, the emulated focus ends a second after input while the sender stays connected, and at once when it exits. A lease keeps it after the sender exits, through real focus and pointer changes in other apps, and reports its owner and remaining time. Only its owner releases it, renewing takes it over, and it ends when it expires. |
| a lease keeps only the keyboard while the real pointer is on its connection | With `follow_mouse = 2`, the real pointer on another window of a leased window's connection takes the pointer from the leased window, which keeps its keyboard focus and its lease. |
| a lease ends when input reaches another window of its connection | Input through the socket of another window of the connection ends the lease and the leased window's emulated focus. |
| a lease ends when the real focus moves to its connection | The lease ends when the real focus moves to another window of the same Wayland connection, and is refused while the focus is there, also for two `footclient` windows of one server. |
| leases are refused for X11 windows and bad arguments | X11 windows and the focused window get `unsupported`. Bad durations and arguments, unknown windows and missing sockets give their codes. A lease of 600000 ms is accepted. |
| a sender's disconnect releases every key, modifier and button it held | A sender that stops emulating or exits while holding Shift, A and the left button has all of them released, with and without a lease. With a lease the focus stays, without one the pointer leaves after the button is released. A sender that exits gets its button released before its keys. A sender that presses Shift, A and the button and exits while Hyprland is stopped has them released once Hyprland runs again, although libeis reports no disconnect for it. |
| the virtual cursor stays while a lease keeps the pointer | The virtual cursor stays on a visible leased window past its timeout, and fades once the lease is released. |

### `launch`

| Subtest | Checks |
| --- | --- |
| launcher handles failed and invalid compositor replies | With a mocked `hyprctl`, a failed launch, failed query, non-JSON or empty reply exits 1 with a clear error and no jq parse error. A successful reply still prints the address. Decimal timeouts with leading zeroes work. |
| launching applications on a hidden workspace | `hyprmasq-launch` opens `foot` silently on its workspace with a title full of quotes and backslashes, finds a window opened by a child process, gives each of two launches at once its own window, and times out for a command without a window. A title with `/instances`, `rollinglog -f` and `/--batch;kill;`, which hyprctl and Hyprland look for in a request, reaches `foot` unchanged, on a workspace that starts with `-`, in a request of exactly 1023 bytes, which Hyprland would wait on without padding. A `foot` and an `xterm` started as children of the launched shell without Hyprland's variables, and the second window of a launch, open on the launch's workspace without the focus changing, found by their parent. `launched` gives the two windows of one launch the same token and others another. |
| hidden wev pointer events | `wev` on a hidden workspace receives enter, motion, button and axis events. |
| two footclient windows on one Wayland connection | Two `footclient` windows of one `foot --server`: the hidden one receives the text, the focused one receives nothing and stays focused. |
| a window that emulated input opens opens silently next to the driven window | As with a browser whose user and agent windows share one process and connection: a `footclient` that the hidden shell of that server starts opens its window on the hidden window's workspace without the focus changing, while the user is in another app, also one on that workspace, and while the user is in the server's other window, and `launched` does not list it. One that opens 6 seconds after the input, and one after a real click in the server's visible window that follows the input, open on the user's workspace with the focus. |

### `shared-focus`

| Subtest | Checks |
| --- | --- |
| a click keeps its modifiers, and a window of the connection with the real focus gives them back after | A Ctrl-click on a window of the connection that has the real keyboard focus, which lacks `hold`, keeps Ctrl, since the window keeps the connection's keyboard focus while the sender holds Ctrl, after which the user's window gets it back with the user's modifiers. With the real focus in another app the window has `hold` and the click keeps Ctrl. |
| a shortcut takes effect in a hidden window of the connection with the real focus, and not in the user's | focus-probe's shortcuts mode, as a browser with the user's window focused and the agent's hidden, runs a shortcut 150 ms after its key in the toplevel that got it, if that still has the keyboard focus, as Chromium does once the page has let the key go by. The user types before Ctrl+L to the hidden window and after it: the shortcut runs there, the user's keys reach only their window and the hidden window's keys only it, and the user's window gets the keyboard back at least 200 ms after the last key. `state` counts no interruption. |
| the user's typing beside a shortcut takes the keyboard back first, and the plugin counts it | The user types into their window while Ctrl+L goes to the hidden window, until one of their keys comes before the shortcut has run: each of their keys reaches their window, with none of the hidden window's Ctrl, the hidden window's keys only it, and the shortcut that found its toplevel no longer active is counted in `state`'s `interrupted`. |
| an emulated click gives a hidden window the keyboard focus before its button, and a lease keeps it | A click into a focus probe on a hidden workspace, while another app has the real focus, gives it `keyboard enter` and `modifiers` right before its press, after the motion, and the keyboard leaves when the sender ends. Under a lease, the keyboard focus from a click stays, and a later sender's key needs no enter. Release ends it. The active window and workspace stay. |
| an emulated click leaves the keyboard focus of the connection with the user's focused window alone | Eight clicks into a hidden toplevel of the probe whose other toplevel has the real focus, while the user types into that one, bring no keyboard event to either window, and each of the user's keys reaches their window. A later click and typed text: the keyboard enters the hidden toplevel after the click, with the text, and the user's key after reaches their window. |
| the user's held button in a window of the agent's connection survives the agent's pointer input | `nix/user-input.c` holds the left button in the user's window of a focus probe through a virtual pointer, as real input, while the agent moves the pointer in the probe's hidden window. For a hold of 0.7 s, the agent's motion arrives only after the user's own release, which is their window's only release, with no pointer leave before it. For a hold of 2.5 s, the sender stops with `input_paused` and delivery `none`, and `state`'s `lastPause` is `user_button`, the user's window keeps the button until its release and the agent's window gets nothing. Once the user has let go, the agent's motion arrives again. |
| the user's held key in a window of the agent's connection survives the agent's keys | The user holds A in their window through QEMU's `sendkey` while the agent sends a key to the hidden window: for 0.7 s, the agent's key arrives after the user's release, with no keyboard leave in between. For 2.5 s, the sender stops with `input_paused`, `lastPause` is `user_key`, and the agent's window gets no key. |
| the agent's pointer input beside the user's window has the agent's modifiers, not the ones the user holds | A focus probe with the user's toplevel focused and the agent's hidden. While the user holds Shift through QEMU's `sendkey`, the agent's plain click arrives with no modifiers, and the user's window has Shift back right after it. While the user holds Ctrl, neither a click nor a wheel or smooth scroll arrives with Ctrl. No keyboard focus moves. The user types capitals during sixteen of the agent's clicks: every key reaches the user's window, each letter with Shift exactly when it is a capital, and no click has Shift. The agent's own Ctrl-click, sent while the user holds Shift, waits for the user to let go and arrives with Ctrl alone, and the user's window gets its modifiers back with the keyboard focus. |
| the user's keys take the keyboard back from a window that keeps it also through an input method | With the keyboard grab of an input method from `nix/user-input.c`, which takes the user's keys instead of their window, the user types while Ctrl+L goes to the hidden window of a shortcuts probe, until one of their keys comes before the shortcut has run: the input method gets every key, the user's window none, the shortcut finds its toplevel no longer active, and `state` counts the interruption. A key held for 0.7 s through the grab holds the agent's key back until its release. Without the grab, the user's keys reach their window again. |
| a driven Wayland window that asks for activation is marked urgent and leaves the user's focus alone | focus-probe's activate mode, driven while the user is in a foot on another workspace, asks for activation a second after the agent's key, with that key's serial, of the driven window and of the other window of its connection: Hyprland's `urgent` event names each, and the user's focus and workspace stay, under `focus_on_activate`. Once its socket is closed, its request focuses it. |

### `drag`

| Subtest | Checks |
| --- | --- |
| same-client popup, move, resize and drag protections | The guard mode of `nix/focus-probe.c`, two toplevels on one connection, answers an emulated click with a popup grab, a drag and an interactive move and resize. None of them take the real focus or change the window. The drag, from a window that shares its connection with the focused one, is cancelled, and `state` counts it with reason `shared_focus`. |
| a hidden window's own drag-and-drop runs privately, and its data goes through file descriptors | `nix/dnd-probe.c`, a raw Wayland client that drags text from its window to itself, on a hidden workspace: an emulated press starts a private drag, the window's pointer leaves without a release, the drag enters the window and then its subsurface with a new offer, both ends learn the move action, and the drop passes the destination's pipe to the source, which writes the text that the destination reads. The icon gets frame callbacks, `state` counts the drag as finished, the shot shows the window that the finish redrew, the user's focus and pointer stay, and the window's pointer takes motion again after the drop. |
| drags in quick succession each run privately, also when the app starts each one at a frame callback | Four drags, each a `path` of two points, in one `hyprmasq-input` run on the drag probe, which starts each drag only at the first frame callback after the pointer moved 8 pixels from its press, as Firefox does, and confirms each drop 300 ms after its data: all four run privately and deliver their data, a drag starts while the drop before it waits for its confirmation, `state` lists four finished drags in `ended`, and the user's focus and pointer stay. Without frame callbacks while the motion streams in, the drags would not start. |
| a private drag that the destination refuses, or that ends outside the window, is cancelled | A destination that accepts no type, one that only asks, and a release outside the window's surfaces cancel the private drag, with reasons `rejected` and `no_target`. |
| only the serial of a held emulated press starts a private drag | A drag request with the serial of the pointer's enter, of a press whose button is up, or a made-up one is cancelled before anything is offered, with reason `serial`. |
| a drag without a source runs within its app without offers, from a held emulated press only | A drag that the probe starts without a source enters its window and then its subsurface without an offer, gets no drop, ends with a leave at the release, and counts as finished, with the user's focus and pointer unchanged. One with a made-up serial is refused with reason `serial` before anything is sent. |
| a private drag ends when its source, data device, window, socket or sender goes away | The source's destruction, the data device's release, the sender's death by SIGKILL, the socket's close and the window's destruction each end a held private drag: the source gets its cancel, and `state` names `source_gone`, `device_gone` and `sender_gone`. |
| a dropped private drag outlives its sender until the destination finishes, or it gives up | A destination that finishes 1.5 s after the data, after the sender has exited, finishes the drag, and `state` says `dropped` until then. One that never finishes ends `unconfirmed` with reason `timeout` 10 s after the drop, and its source gets a cancel. |
| the user's real input to the app of a private drag cancels the drag first | The user's pointer moving onto the probe's second window, beside their terminal, and their focus moving there each cancel a held private drag before the app gets their input, with reason `user_input`, and the input stays where it went. |
| the user's typing during a private drag arrives unchanged, with their focus and pointer | The user types six lines through QEMU into a terminal of theirs while a private drag runs for seconds in the probe on a hidden workspace: they arrive unchanged, the focus and the pointer stay, and the drag finishes after the typing. |

### `selections`

| Subtest | Checks |
| --- | --- |
| a hidden app sees none of the user's selections | A clipboard probe, `nix/clip-probe.c`, on a hidden workspace pastes nothing on emulated V and M while the user's clipboard and primary selection, set through data-control, hold text: it gets no selection, not the user's. |
| the agent's selection and copy in a hidden app leave the user's selections alone, and paste in that app | A double-click on a word and Ctrl+Shift+C in a hidden foot leave the user's clipboard and primary selection, as `wl-paste` reads them, and the user's focus unchanged. Ctrl+Shift+V and Shift+Insert there paste the word. |
| the agent's copy pastes into another hidden app, which the source sends its data | The probe pastes foot's word from the agent's clipboard and primary selection. Its own copy and selection paste into foot, and the probe as the source writes the data into the destination's pipe. The user's selections stay. |
| the user's own copy and paste work, also in an app with a socket | The user's Ctrl+Shift+V and Shift+Insert through QEMU's keyboard paste their own selections into their foot. Their focus on the probe, which their switch to its workspace gives it and which made the agent's clipboard, ends that copy with a cancel and leaves the agent's primary selection. Their key and their click of a virtual pointer in the probe, which has a socket, set their clipboard and primary selection. The agent's copy and paste in the probe while the user's focus is there get the agent's clipboard, and the user's paste right after it cancels the agent's copy before the key and gets theirs. |
| the agent's selections go with the app that made them | Once the probe that holds the agent's selections exits, foot pastes nothing, and the user's selections stay. |
| an app that pastes its own copy pastes the user's clipboard once the user's input reaches it | The agent copies with Ctrl+A and Ctrl+C in a hidden window of a Wayland Chromium, `guest/clip.html`, and pastes it there. Chromium, which pastes its own copy while it takes itself for the owner, pastes the user's clipboard on the user's Ctrl+V in their window of the same Chromium, and the agent's paste then gets nothing, not the user's clipboard. After a second copy, the user focuses the agent's window and pastes their clipboard there too. The user's selections stay. |
| an X11 app's selection that the agent makes stays in its X server, also when the app exits | The agent's double-click in a hidden `xterm`, through a socket with `force` while the user works in another `xterm`, makes the X primary selection, which `xclip` reads, and leaves the user's Wayland selections alone, also once the hidden `xterm` exits. |
| an X11 app's selection that the agent's input makes once the input hooks have gone stays in its X server | The agent double-clicks a word in a hidden `xterm` that is stopped, while the user's `xterm` has the focus. Once the input and X11 input groups are out, with the X selection group still in, the `xterm` continues and takes the X primary selection, and the user's selections stay, also after it exits. |
| the agent's paste pastes its text through the agent clipboard, a long one in parts, and leaves every selection alone | `act`'s `paste` into a clip probe that selected a text as the agent's primary selection: the probe pastes the text on Shift+Insert, then a text of 40000 bytes in three parts in order, each whole. The probe's selection gets no cancel, and the user's selections and focus stay. |
| pastes into windows of different apps take turns, and each app gets its own text | Two servers paste into two clip probes of separate processes. The second paste waits, with the progress message about another window's paste, while the first one's batch pauses after its paste. Each probe pastes its own text and never the other's, and the user's selections and focus stay. |

### `xwayland`

| Subtest | Checks |
| --- | --- |
| XWayland screenshots on a hidden workspace | A red `xterm` on a hidden workspace has the right color and orientation, also after a tiled neighbour closed while it was hidden, once a `wait … since` has ended `quiet` with the window no longer resizing. |
| XWayland hidden shell input while a Wayland window is active | Typing into a hidden `xterm` runs the command while a Wayland window stays active. |
| XWayland pointer events reach a hidden window under a visible one | `xev` on a hidden workspace receives motion, clicks and scrolling at a point where a visible X11 window is stacked above it. The visible one receives no clicks. |
| XWayland input is refused while another X11 window is active | Input is refused while another X11 window is active and the active window stays untouched. The active window itself can be driven. `force` drives the hidden one. |
| a driven X11 app cannot activate itself while its input is in use | While a sender is connected to its socket, which installs the X11 input hooks, a driven X11 app cannot activate itself. Once its socket is closed, it can. |
| X11 apps learn they are not active after input | While a Wayland window stays active, `_NET_ACTIVE_WINDOW` is written again after typing into a hidden `xterm`, after clicking it, and after it asks to be activated while a sender is connected to its socket. |
| an X server that does not answer holds Hyprland up for 25 ms per input frame at most | With X focus held for one hidden `xterm` and XWayland stopped by SIGSTOP, a sender to a second hidden `xterm` stops with `input_paused` within 100 ms, and so does a second one, and `hyprctl` answers within 300 ms. The user's focus stays. Once XWayland runs again, input to the second `xterm` arrives. |
| a stopped XWayland never holds Hyprland up, however many requests it leaves unread | With XWayland stopped by SIGSTOP, 96 pointer frames go in turn to four hidden `xterm`s, each frame with a request for the stacking order that XWayland leaves unread, more than fill a quarter of the socket's buffer, from where a flush would wait for XWayland. `hyprctl` answers within 500 ms after each frame, and the user's focus stays. Once XWayland runs again, input to an `xterm` arrives. |
| xwayland-satellite windows | Screenshots and input for a hidden satellite `xterm`. Input is refused while another satellite window is active, allowed for Hyprland's XWayland windows, and forced with `force`. No satellite app can activate itself while a sender to a satellite window is connected, but new satellite windows still get focus. |

### `x11-menus`

| Subtest | Checks |
| --- | --- |
| a GTK 3 menu on X11 on a hidden workspace opens and picks by pointer | A click on the combo box of `combo-probe.py`, hidden under a visible `xev` window, opens its menu, which stays open past a second. A click in the menu's own window picks an item, and so do a click in the probe's window where the menu lies and a press, a drag onto an item and the release. The `xev` window gets no clicks, and is above the probe again afterwards, where an XTEST click reaches it. The user's focus stays. |
| a Chromium select on X11 on a hidden workspace opens and picks by pointer | A click on a `<select>` in a hidden Chromium window opens its popup, which stays open past a second, also while the user moves between Wayland windows. A click in the popup's own window picks an option, and so does a click in Chromium's window where the popup lies, after which the page loses the X focus. Closing the socket while the popup is open closes it. The user's focus stays. |
| hyprmasq-mcp does not close a GTK 3 menu on X11, which would break its app, and says how to dismiss it | `close` on the menu of a hidden `combo-probe.py`, which an `act` opened, fails with `unsupported` and a hint that names Escape in the menu's window and a click in the probe's window outside it. A second later the probe runs without GTK criticals, its window is mapped and the menu is open. `state` gives the menu `overrideRedirect`, and both windows `deleteWindow`: Hyprland's close dispatcher would send the menu that request, which GTK 3 answers by destroying the menu under the combo box, after which the probe crashes once the combo box opens again. Escape through `act` in the menu's window dismisses it without a choice, so does a click through `act` in the probe's window outside it, and a click on an item in the menu's own shot picks it. `close` on the probe's window closes it, and the probe exits. The user's focus stays. |
| hyprmasq-mcp does not close an X11 window that takes no close request, which would end its app | With `WM_PROTOCOLS` set to `WM_TAKE_FOCUS` alone, `state` gives a hidden `xev` no `deleteWindow`, and `close` fails with `unsupported` and a hint to quit the app from within it, while `xev` goes on running. Once `WM_PROTOCOLS` lists `WM_DELETE_WINDOW` again, `close` closes it, and `xev` exits on the request. |

### `cursor`

| Subtest | Checks |
| --- | --- |
| virtual cursor and click ripple on the active workspace | A moved pointer shows a light blue cursor on the monitor, a click a ripple beside it. Both fade out, and input during the fade restores the cursor. A shorter `virtual_cursor_timeout` makes it fade sooner. Nothing is drawn for a window on a hidden workspace. Changing `virtual_cursor_color` recolors it, `virtual_cursor = false` hides it. |

### `mcp`

| Subtest | Checks |
| --- | --- |
| hyprmasq-mcp introduces itself and its tools | `hyprmasq-mcp`, driven over stdio by a JSON-RPC client in the test: the instructions start with a summary line. `tools/list` has `windows`, `launch`, `screenshot`, `act`, `release` and `close` with input schemas, `act` with named or compact actions, `next_page`, and `record` with `slides`, `video` and `stop`, and `windows` with `match` and `wait_ms` up to 10000. None is read-only, and `windows`, `screenshot`, `launch` and `release` are not destructive. In every subtest, the structured content of each result carries all of its text: the error, the summary, the hint and what `act` did with a recording. |
| hyprmasq-mcp refuses a runtime directory with whitespace | `hyprmasq-mcp` started with an `XDG_RUNTIME_DIR` that has a space exits with status 1 and says that the directory for screenshots contains whitespace, which a request to the plugin cannot carry. |
| hyprmasq-mcp launches a window on a hidden workspace | `launch` starts foot on a hidden workspace and returns its shot and summary line, and `windows` lists it as launched, not resizing, with its capabilities. Its line leaves out the pid and the capabilities, which it has all of. |
| hyprmasq-mcp launches an app whose launcher forks it away and exits | The launched shell starts `foot` in a new session from a shell that exits at once, and exits too, as `kate` without `-b` does. `launch` returns the window on its hidden workspace while the user types into a terminal, both shells have exited, and the focus and every typed line stay with the user. |
| hyprmasq-mcp gives Claude Code the text in place of structured content | A server whose client introduces itself as `claude-code` gives results no structured content, so Claude Code passes the text: `windows` with `match` in another case lists only the launched shell, with its shot and image, a `match` without windows says so, a screenshot has its summary and image, and an error its error line. Another client gets the structured content, with the same `match` and the shell's `shot`, whose line is in its `summary`, and a `match` without windows says so in its `summary`. |
| hyprmasq-mcp types into a hidden shell, waits for its redraw and keeps a lease | One `act` types a command and presses Enter, waits until the shell has redrawn, returns what changed since the shot that `windows` returned for the shell, the session's last of it, a zoom into the lines of the command and its output, and holds a lease. The command ran. |
| hyprmasq-mcp clicks at the shot pixel, also at scale 2 and in a zoom shot | A point-pair click, with its target window identified by the shot, reaches focus-probe at the matching window-local point, also at scale 2 and in a zoom shot of a region, which comes enlarged 4 times and whose line says that act takes its own pixels. The first click draws nothing, so its result has no image, names the shot the window still shows, and says that the click may also have opened a window that maps later. A screenshot of the window's address in upper case and with leading zeros is of that window, and names it in lower case. |
| hyprmasq-mcp takes the click actions of Anthropic's computer use tool | A `triple_click`, which the schema leaves out, reaches focus-probe as three presses and releases at the shot pixel, and the user's focus stays where it was. |
| hyprmasq-mcp draws a spline path as one stroke through its points | A compact `act` action `["path", points, "spline"]` through four points that turn by up to 38 degrees reaches the focus probe as one press and one release around motion that starts and ends at the first and last point, passes through every point, moves at most 10.5 pixels a step, turns less than 10 degrees a step, stays within 5 pixels of the points' box, and holds the button for its paced time and less than 1.5 s. |
| hyprmasq-mcp sends nothing for a batch it refuses | `stale_shot` after a resize, `out_of_bounds` outside the shot and for the part of a popup above the window, `invalid_arguments` naming the field, also for a `null` coordinate and for a `drag`, which says that a drag is a path of two points, and for 40 times `^` under a German keymap, whose 80 keys `deadline_ms` 3200 cannot fit although 40 keys would, `cannot_type`, and `unsupported` for a path with a button, for a click with modifiers and for a chord that holds a letter on a window that shares its connection with the focused one: each sends nothing, and the first ones return a fresh shot in a result that is no tool error, while a failure without a shot is one. A chord of Shift and a letter types the capital in that window. The part of the popup over the window takes a click. |
| hyprmasq-mcp says when the user's typing took the shared keyboard focus back shortly after a shortcut | On the shortcuts probe, an `act` with Ctrl+L to the hidden window takes effect and says nothing more while the user does not type. While the user types into their window, an `act` with a pause and Ctrl+L is sent until one of their keys comes before the shortcut has run: the result then keeps delivery `complete`, says `shared keyboard focus returned for user input`, has `actions.shared_focus_returned` with `effect` `unknown` and `actions.interrupted`, and its hint says that the shortcut may or may not have taken effect and points at the shot. The user's keys reach only their window throughout. |
| hyprmasq-mcp says that a shortcut beside the user's window may have run too late, and only there | On a shortcuts probe that runs its shortcuts 500 ms after the key, an `act` with Ctrl+L to the hidden window ends `no_redraw`, the shortcut finds the user's window active and does nothing, and the hint says that the app shares its keyboard focus with the user's window, that a shortcut it handles later than about 250 ms may not have run, and to use a menu, a button or a separate instance through `launch`. The user's focus stays and their keys after it reach only their window. With the user's focus in another app, the window's lease keeps the focus, the shortcut runs and redraws, and there is no such hint, nor for a focus probe of its own connection whose Ctrl+L draws nothing. The shortcut that runs in 150 ms on the shared connection gets none either. |
| hyprmasq-mcp drags in a hidden app's own drag-and-drop, and says how it ended | `windows` lists the drag probe with `dnd`. An `act` `path` of two points from its window onto its subsurface reports `drag-and-drop finished`, the probe gets the text, and the user's focus and pointer stay. A drop that the probe refuses reports `drag-and-drop cancelled (rejected)` with its hint, and a drag that the probe starts with the serial of the pointer's enter reports `drag-and-drop cancelled to protect the user's input` with reason `serial` and its hint, after a wait that ends quiet within 900 ms. |
| hyprmasq-mcp reports partial delivery when X11 input is paused | Typing into an X11 window while another X11 window becomes active gives `input_paused`, delivery partial with the keys the window got, and a hint that does not suggest repeating. |
| hyprmasq-mcp waits for a redraw, for a change since a shot, and until the deadline | A click that draws nothing gives `no_redraw` and its hint, and with `expect_change` false, input that draws nothing settles after the quiet time. An empty batch sees a popup that opened since its shot, with a summary that names no lease and a `lease` field with `held` false, as the window holds none, and then `no_redraw` since the shot that shows it, and without a shot during the call, as its hint says. A change phase that runs into the deadline times out with `no_change` and `no redraw`. A window that keeps drawing times out before the deadline with `busy`. |
| hyprmasq-mcp does not wait for an animation, and names it | A digit that counts ten times a second at the top left of a foot window is an animation: the wait settles within 5 seconds instead of at the deadline, `wait.animated` has one box at the top left, and the summary says `except animation at …`. Whether the digit's changes are left out is not checked, since two digits can differ by too little to show that the box goes on changing. The next subtest checks it. A plugin `wait … since` with `expect` and `ignore animations` ends `no_redraw`, while one without ends at the digit's next commit. |
| hyprmasq-mcp keeps what a window draws in the box of an animation, and leaves out what goes on changing | focus-probe's tiles mode redraws at every frame callback and damages a tile of 256×128 whole, in which only a 24×24 spinner, noise that is new at every frame, changes, as Thunderbird damages tiles: `wait.animated` covers the tile. A click draws a block in the same tile that stays: the result has one zoom, of the block, which shows it, none of the spinner, and `1 change in an animation left out`. |
| hyprmasq-mcp lets a click settle before the keys after it | After a click on focus-probe, which draws nothing in answer, the first key of the same batch arrives at least 300 ms and less than 700 ms after the button's release, and the key after it follows. The subtest then releases its lease, so the next one starts without emulated focus. |
| hyprmasq-mcp keeps the emulated focus between calls, and releases it on release and on exit | The emulated focus stays between two calls four seconds apart, `windows` shows the lease as this session's, `release` ends it, and a server that exits releases its own leases and no others. |
| hyprmasq-mcp lists the windows that input opens | A window that the launched shell starts is listed in `new_windows` as launched, with a shot, and `close` closes it. |

### `new-windows`

| Subtest | Checks |
| --- | --- |
| windows that input opens open on the workspace of the window that got it, without the focus | focus-probe's opener mode, on a hidden workspace, opens a toplevel without a parent, a dialog with one, and through another process a dialog that names its parent through xdg-foreign after Hyprland created it, as a portal's file chooser does, in response to one `act`: all three open on its workspace, `state` names the probe's window, with its `stableId`, as their source, which placed them, and the active workspace and window stay unchanged. The toplevel floats at the size it asks for, and the probe's window keeps its size. The result lists all three, on that workspace, with a shot of each after the probe's, and a click in the dialog's shot reaches it. The toplevel stays blank until its first frame callback, so its shot shows it green, without `may_be_stale`, only because the capture fed it frame callbacks first. |
| a window that maps after the wait has ended is still reported by that act | A toplevel that maps 300 ms after the key, after a wait with a quiet time of 100 ms, is in that `act`'s result with its shot, on the probe's workspace. |
| a window that an app opens once the dialog that got the input has closed opens next to it | S in a dialog of the probe closes the dialog, and the probe opens a toplevel 300 ms later, when no window of the probe that is still open got input within 5 seconds: it opens floating on the dialog's workspace without the focus, `state` names the closed dialog as its source, without a `stableId` once it is gone, and that `act` returns it with a shot. |
| a window that input opens after its act has returned comes with the next windows, screenshot or act | A dialog that a middle click in the probe's window opens 1.5 s later, after the `act` has returned, is not in that act's result. The next `windows` returns it in `late_windows`, placed on that workspace, with a green shot and a hint that names it, marks its line as opened by the input to the probe's window, and returns no shot of the other windows. The next `screenshot` and `windows` do not report it again. Another such dialog comes with the next `screenshot` of the probe's window, and one more with the next `act`, one without actions. |
| windows waits for a window that input opens after its act, and returns the shot of the one window it lists | Right after such a middle click, `windows` does not list the dialog, or, if it has mapped already, returns it with its shot. `windows` with `wait_ms` then returns it. Called right after the click, `windows` with `wait_ms` 5000 returns once the dialog has mapped, about 1.5 s after the click and within 2.5 s of the map, with `wait.ended` `found`, `found after waiting N ms` and the dialog's shot. With `match` and `wait_ms`, the next dialog is the one window listed, with the shot of its late window. `match` on its title then returns its shot as `screenshot` would: a click at its pixels reaches the dialog there, and the result compares with it. |
| input that closes its window after the wait has ended is delivered, and returns the window it came from | Enter in a dialog of the probe redraws it and closes it 300 ms later, after the wait has ended quiet with a quiet time of 100 ms: the result is `1/1 delivered`, complete, with `wait.ended` `quiet`, `window closed` and `window_closed`, without a shot or a failed capture, and names the probe's window, which the dialog came from, as `back to …`, with a shot of it that takes coordinates. |
| a dialog that a click closes returns the window it came from, unless that is gone or the user's | A right click that closes a dialog of the probe gives `1/1 delivered · window closed · back to …` with the shot of the probe's window. A click whose release met the close is repeated, and its failure has that shot too. A dialog that another process names the child of a probe's window through xdg-foreign, and that outlives that window, gives no window to go back to when a click closes it. |
| a dialog whose parent redraws 300 ms after the close returns the parent's shot with the redraw | In opener mode, A opens a dialog whose parent answers its close: it asks for a frame callback, and redraws in orange 300 ms after the first one, as a hidden page lays out the file that a file chooser gave it once it can draw. A right click that closes the dialog, more than a second after the key that opened it, returns the parent's shot in orange: the parent got its first frame callback from the wait before its shot, which waited for its redraw, not only for 200 ms of quiet. |
| with place_new_windows off, a window that input opens opens as Hyprland places it, with the focus | With `place_new_windows` off, the toplevel opens on the active workspace and takes the focus, `state` gives `disabled`, and the result says that it is visible and focused, and why. |
| a dialog of the user's own window opens as usual, also when input reached another window of its app since | Of two windows of one probe, one on the user's workspace and one hidden: a dialog of the user's window that a real middle click opens 1.5 s later, after input reached the hidden window, opens on the user's workspace with the focus, with no source, since its parent is the user's window. |
| the user's typing into one window of an app does not take a window that input to another opens | A toplevel without a parent that K in the hidden window opens 1.5 s later, while the user types three letters into a window of the same probe on their workspace, opens floating on the hidden window's workspace with the focus unchanged, and `state` names the hidden window as its source, which placed it: typing opens no window, unlike a click, a shortcut, or Enter that follows no typing. |
| a dialog of the user's window that a click closes returns no window | A dialog that the user's real right click opens in their window has no source, and a click of `hyprmasq-mcp` that closes it gives `window closed` without a window to go back to and without driving the user's window. |
| a line break that the user types does not take a popup that the agent's click opens | In focus-probe's browser mode, as Chromium in the benchmark, a click in the hidden window opens a toplevel without a parent that the probe creates 60 ms later and draws 300 ms after that. The user types five letters into a window of the same probe on their workspace, and their Enter, through QEMU's keyboard, comes between the click and the map: the toplevel opens floating on the hidden workspace, placed, with the focus unchanged. |
| neither a line break before the app creates a window nor other input after it takes the window | With the toplevel created 2.5 s after the click and drawn 1.5 s after that, a line that the user types and ends with Enter before the probe creates it, their Ctrl+X after the probe created it and before it draws, and an Enter 1.5 s after the user let go of a Q they held for 1.5 s, more than 2 s after its press, leave it placed on the hidden workspace with the focus unchanged. |
| the user's shortcut or click in their window of the app after the agent's click takes the window that it opens | With those times, the user's Ctrl+X, their Enter after the keypad's 8 with Num Lock off, which moves, their Enter after a Y that a keybind takes, or their real left click, in their window after the click in the hidden window and before the probe creates the toplevel makes it theirs: it opens on the user's workspace and `state` gives `user_input`, with the focus after the keys. The toplevel that the user's click opens is theirs too, and takes the focus. |
| a window that the app created ahead and draws on the agent's click opens next to the agent's window | A toplevel that the probe creates on SIGUSR2, before any input reached it, and draws 300 ms after a click in the hidden window opens placed on the hidden workspace, with the hidden window as its source and the focus unchanged. |
| a dialog that another process opens for a launched window opens next to it | A dialog that a process outside the launch names a launched probe's window the parent of through xdg-foreign, before any input reached the probe, opens floating on the launch's workspace without the focus. A toplevel that N in the launched window opens in the launch's first minute, which the launch places, floats there at its size, with the launched window as its placed source, and the launched window keeps its size. |
| a window that an app opens on another Wayland connection of its process opens next to the agent's window | In opener mode, G opens a toplevel without a parent 1.5 s later on a second Wayland connection of the probe's process, as Electron opens GTK's file chooser. After `hyprmasq-mcp`'s G in the hidden window, while a window of another process opens on the user's workspace with the focus and no source, the toplevel opens floating on the hidden workspace without the focus, with the hidden window as its placed source, and `hyprmasq-mcp` reports it as opened by that input. The user's Ctrl+X in their window of the probe, on the first connection, after the G sent to the hidden window and before the probe creates the toplevel makes it theirs: it opens on the user's workspace with the focus, and `state` gives `user_input`. |

### `mcp-shots`

| Subtest | Checks |
| --- | --- |
| two hyprmasq-mcp servers take turns on one window | Two servers acting on one window take turns: both calls are made before the first one's keys are all typed, and the second sends only after the first has waited and captured. A call that cannot get the window within its deadline fails with `deadline` and sends nothing. |
| hyprmasq-mcp ends a wait when the client cancels the call | `notifications/cancelled` during a long wait ends the call with `cancelled` and a shot, and ends the plugin's wait. Progress notifications carry the client's token. |
| hyprmasq-mcp stops a batch whose window is resized while it is sent | A resize during a batch's pause stops it with `stale_shot`, partial delivery after the first click and a shot at the new size. The click after the pause never arrives. |
| hyprmasq-mcp sends input to windows of different apps at the same time | While a long text goes into a foot window, keys into the focus probe, another process, arrive and return before that text is all typed, without waiting for the foot's send lock. |
| hyprmasq-mcp checks a window again after waiting for input to another window of its app | A focus probe with two toplevels on one connection: a click on one toplevel waits, with the progress message about the same app, while the other gets a long text, and is refused with `stale_shot` when its window is resized meanwhile, and sends nothing. |
| hyprmasq-mcp captures a region after the input | `act` with a region as `capture` returns that zoom shot, enlarged 4 times, whose pixels a later `act` takes. With `capture: "none"` it returns no shot. When the window is resized while an `act` with a region waits, it returns a shot of the whole window at its new size, and the hint says why. |
| hyprmasq-mcp returns what changed since a shot, as a zoom into each changed region | In a foot window that draws what the test writes to a FIFO: an `act` that changes nothing returns no image, and a key that changes nothing says so and names the shot the window still shows. Four white cells are one change, whose zoom shows them, the pixel around them and a margin of 24 pixels, at their place in the shot's pixels. Three blocks far apart are three zooms, top to bottom. Seven blocks whose margins do not touch, six of them in a row about 110 pixels apart, are joined into five zooms, which show every block, the one far apart alone. A line one pixel wide and a dot are two tiny changes, left out and counted, and a highlight 12 levels lighter than the background is no change. |
| hyprmasq-mcp takes coordinates from a changes shot and from its zooms | When focus-probe's red popup, of about the luminance of its blue window, closes and opens again, the result is a zoom into it, cut at the window's edges. The pointer moved through the changes shot at the pixels of the shot it compared with reaches the window there, and a click in the zoom's pixels reaches the popup. |
| hyprmasq-mcp returns a full shot in place of the changes, and says why | A full shot comes in place of the changes and says why: more than 35% of the window changed, 6 large blocks changed in the corners and the middle of two edges, which joined into 5 regions would cover more than 35%, the window was resized, another session has no earlier shot, after which it compares with the full shot it got, and 10 changes shots in a row came before. `"window"` returns a full shot as before, and `"none"` no shot. |
| hyprmasq-mcp fits shots within --max-image-size | With `--max-image-size 150`, the shot of a 300×200 window is 150×100 and a click at a shot pixel reaches the doubled point. Zooms are fitted too, and one that fits keeps the monitor's scale, also a small one that enlarged twice would not fit. |
| hyprmasq-mcp enlarges a small zoom, and takes coordinates between its captured pixels | A zoom of 100×40 pixels of focus-probe's shift mode comes as 400×160, with the button's label in blocks of 4×4 pixels. A click at a zoom pixel reaches the window at a quarter-pixel point, passing the stale-target check, and `act`'s capture region comes enlarged too. With `--min-zoom-size 0` the zoom stays 100×40. |
| hyprmasq-mcp types through the Compose table of the window's locale | A server in `pt_BR.UTF-8` types `ć` under a German keymap into a foot in `en_US.UTF-8`, through that window's table, without a hint about Compose, and into a foot in a locale without a table, through that of `en_US.UTF-8`, with a hint that says so. |
| hyprmasq-mcp gives a window time to catch up with a resize, and takes no coordinates from an older frame | `launch` and `close` of a tiled neighbour name focus-probe's window as resized. While the probe holds back its new size, `windows` lists it as resizing, a screenshot waits 1.5 s, then returns the older frame marked `resizing` with a hint, `act` refuses coordinates from it with `stale_shot` and sends nothing, and an `act` whose wait times out says `still resizing`, with only the hint about the older frame. A screenshot that is waiting when the probe answers returns the frame at the new size, whose coordinates reach the window. |
| hyprmasq-mcp lets a starved window draw before its first shot, and says when it did not |  |
| hyprmasq-mcp waits out a redraw that follows the input after the window settled | focus-probe's answer mode on hidden workspaces draws itself dark gray on a click and its answer, blue, later, only in a frame callback. An answer 400 ms after the click comes after the wait settled: `act` goes on waiting through its grace for new windows, says `drew again, settled N ms later`, and its shot shows the answer. One 1500 ms after it is not waited for: the shot shows the pressed color, and an `act` without actions then shows the answer. |
| hyprmasq-mcp waits for the window to answer a click before the actions after it | focus-probe's menu mode draws itself dark gray on a click, opens its menu over 500 ms in frame callbacks, and takes Down and Enter only once it is open. One `act` with a click, Down, Down and Enter picks the second entry, three times on a hidden workspace and once on a visible one: the keys come after the menu has opened, 500 ms to 1300 ms after the click's release, and none is ignored. |
| hyprmasq-mcp sends no click at a target that moved since its shot, and one after a change elsewhere | focus-probe's shift mode on a hidden workspace moves its button 60 pixels down 200 ms after a SIGUSR1. A click at the button in the shot from before is refused with `stale_shot`, names `actions[0] (click)`, sends nothing, and returns a shot with the button where it is now, where a click reaches it. After a SIGUSR2 draws a block far from the button, a click from the same shot is sent. |
| hyprmasq-mcp takes coordinates from shots taken while a window pops in | With slow window animations, `launch` returns shots of a focus-probe and an `xev` window that pop in on hidden workspaces at their size, and clicks at the probe's edge and in the middle of xev's inner window in those shots reach them there. |
| hyprmasq-mcp reports input that closes its own window | Escape that closes focus-probe's window, with text after it, gives `window_gone` with `wait.ended` `window_closed`, partial or uncertain delivery and no shot, and an error that names Escape as delivered when it was. |
| hyprmasq-mcp counts input that closes its window as delivered when only pauses follow | Escape and a click that close focus-probe's window while the key or the button is held, each followed by a 3-second pause, give `2/2 delivered · window closed`, complete delivery and `wait.ended` `window_closed`, without an error, a shot or new windows, and with only the hint that the window closed, within 2.5 seconds. A call whose release meets the close, which reports nothing delivered, is repeated, up to three times. |
| hyprmasq-mcp ends its calls and releases its leases on SIGTERM | SIGTERM to a server while one of its calls waits ends that call and the plugin's wait. The server exits 0 within 4 seconds, releases its lease and no other, and removes its shots. |
| hyprmasq-mcp closes a launched window, and cleans up when it exits | `close` closes the launched window, a later `act` gives `window_gone`, and a server whose stdin closes exits 0 and removes its shots. |

### `anr`

The area raises the output to 1280×800, lowers `misc:anr_missed_pings` to 1, so
that an app that has not answered a ping by Hyprland's next one, 1.5 s later,
gets the dialog, and shows a foot that draws nothing on the visible workspace,
as on an idle desktop. It sets both back at its end.

| Subtest | Checks |
| --- | --- |
| Hyprland's not-responding dialog opens for a hidden app that stops answering pings | A foot on a hidden workspace that is stopped with `SIGSTOP` gets the dialog, which names it and closes once it runs again. It shows that the other subtests would see one. |
| a capture gives a hidden app its dmabuf buffers back | `swapchain-probe.c` on a hidden workspace draws into three dumb buffers of the VM's DRM card, shared as dmabufs, and waits for a released one before each frame, as Mesa's swapchains do, without dispatching pings. Eight `act` calls of a key and a full shot, 1.5 s apart, get it to draw all 32 frames, it never waits 500 ms for a buffer, and no dialog opens. Hyprland keeps the buffers that a capture draws until a monitor's next frame, and the idle output has none. |
| a hidden GTK 4 questionnaire answers every ping while driven as the benchmark's agents drive it | The form task's questionnaire, floating at 1000×800 on a hidden workspace, gets three rounds of keys from `hyprmasq-mcp`, a second and more apart: a box ticked, a slider moved, text typed into a field and selected, which claims the primary selection, a dropdown's third entry chosen in its popover, and a scroll. The texts and choices arrive and no dialog opens. GTK draws into shm buffers in the VM, with Vulkan on lavapipe, so this does not cover the dmabuf case above. |

### `settle`

The area raises the output to 1280×800, as large as a laptop's, which the
questionnaire's popover needs, and shows a foot on the visible workspace. It
sets the output back at its end.

| Subtest | Checks |
| --- | --- |
| a hidden GTK 4 questionnaire settles once its widgets have drawn, while its overlay scrollbar fades in | `hyprmasq-mcp` launches the form task's questionnaire on a hidden workspace and drives it as the benchmark's agents did, an act every 2.5 s, so that GTK fades the overlay scrollbar out between them and in again at each act's first motion, which redraws a strip 5 pixels wide at the right edge for about 800 ms: two clicks on the margin beside the questions, two boxes ticked, the dropdown opened, Saturn chosen in its popover by keys, and text typed into a field after a click. Every act with a click settles within 600 ms, where the strip alone kept it waiting about a second. After the clicks on the margin, which change nothing else, the summary names the strip as an animation. The questionnaire logs each answer, and the changes shot has a zoom on the box, the popover, the dropdown and the field that show it. |

### `recording`

The VM sets `programs.hyprmasq.mcpSettings` to send recordings to
`/tmp/hm-recordings` with a GIF of each, and the area's server reads them from
the `/etc/xdg/hyprmasq/mcp.toml` that the module writes. It launches two
shells, each on a hidden workspace of its own, and shows a foot on the visible
one, which it closes at its end, back on the workspace it started on.

| Subtest | Checks |
| --- | --- |
| hyprmasq-mcp records slides of acts in two hidden windows, with the settings of the NixOS module | An `act` with `record` `slides` and a `caption` types into the first shell and says where the video goes, in a line after the summary that the structured content carries as `recording`. An act in the second shell is a step too, and an act with `record` `stop` captures without waiting for a change and says that it saved the video and the GIF, with its length and 2 steps. The video is H.264 in `yuv420p`, 640×520, the shells over the bar, with 24 frames over 6 s: each act's input for a second and its result for 1.5, and the last frame for a second more. The GIF has the 4 slides, and the directory holds the two files only. The user's focus stays. |
| hyprmasq-mcp records video of an act at real speed, and the act settles as soon as without | A shell prints a line every 250 ms for 1.5 s. The same act, waiting for 600 ms of quiet, settles with `record` `video` at most 500 ms later than without it. The video has the act at up to 10 frames a second, its result held for a second and the last frame for one more, over 3.8 s or more. The rows with text in its frames grow from the first frame to the last with counts in between, so the frames show the lines as they came, where frames only from before and after the act would have two counts. |
| hyprmasq-mcp saves the recording when the session ends | A recording that runs when the server's stdin closes is saved as a stop would: its video of 3.5 s and its GIF are in the directory without a `.part.mp4` or a temporary directory, and the server logs where. |
### `suspended`

The area raises the output to 1280×800, as large as a laptop's, and shows a
foot that draws nothing on the visible workspace, as on an idle desktop. It
sets the output back at its end. `nix/qt-probe.py` logs each configure that Qt
applies, with whether it was suspended, and `nix/suspend-probe.c` each
configure it gets.

| Subtest | Checks |
| --- | --- |
| a hidden Qt 6 window shows the input of each act in a fresh frame | The Qt probe maps at half width beside the user's foot and moves to a hidden workspace, which gives it the whole output and tells it that it is suspended. Six `act` calls, 2 s apart, as an agent drove the form task's Qt questionnaire: a box ticked, the slider moved with keys, text typed into the line edit, the dropdown's list opened in its popup, an entry chosen with keys and the box cleared. Each act ends `quiet` with a redraw, the probe gets its input, and the act's shot shows it in the widget's box, also the choice after the popup has closed. Each act shows the window, which is suspended again 2 s later. The user's focus and workspace stay. |
| a suspended Qt 6 window that Hyprland resizes draws at its new size once act waits for it | A foot launched onto its workspace halves the suspended probe, which draws nothing at its new size for 2 s. An `act` without actions that waits gets it to draw: the wait ends `quiet` with commits, nothing says `resizing`, and the probe is suspended again afterwards. |
| the Qt window is told that it is suspended again when its socket closes, its sender dies, or its wait is cancelled or times out | A sender that holds its devices in a 20 s sleep keeps the probe shown. `eis close` gets it suspended within 1.5 s, and so does killing a second such sender with `SIGKILL` within 5 s. A `wait … since` that would wait 4 s for quiet shows it, and its `cancel` gets it suspended within 1.5 s. A wait that times out after 1.5 s shows it and then gets it suspended. |
| a hold keeps the Qt window from being told while its workspace is shown and hidden, and gives it Hyprland's state after | A wait that starts while the probe is hidden keeps it from being told while its workspace is shown and hidden again, and one that starts while its workspace is shown keeps it from being told once Hyprland hides it. Cancelling each gets it suspended. The user's focus and workspace come back. |
| the holds of a session and a wait on the Qt window end in either order before it is told that it is suspended | A wait that outlives the second after a sender's input keeps the probe shown 2 s after it, and the probe is suspended once the wait ends. A sender that holds its devices for 4 s keeps it shown after a wait times out within it, and the probe is suspended once the sender ends. |
| a held toplevel that unmaps gets Hyprland's state as it maps again, and an older one is never told that it is suspended | `suspend-probe.c`, launched onto a hidden workspace and suspended, is shown for a sender that holds its devices, and unmaps with a commit without a buffer, keeping its `xdg_toplevel`. Its socket closes with the unmap, and no configure comes while it is unmapped. It maps again with the state that Hyprland told it last, which Hyprland does not update for a window that maps on the visible workspace until the workspaces change, and moved to a hidden workspace, which updates nothing for a window that Hyprland took for suspended, it is told that it is suspended. The probe at `xdg_wm_base` version 5, held by a wait while its workspace is shown, hidden during the hold, and unmapped and mapped again after the wait ended `window_closed`, is never told that it is suspended. |

### `qt-popups`

The Qt probe runs on hidden workspaces of its own, while the user's foot keeps
the focus. One `act` clicks its dropdown, picks the next planet with Down and
Enter, and clicks its check box above the list.

| Subtest | Checks |
| --- | --- |
| a click after the Enter that closed a Qt dropdown's list reaches the window | With Qt's default platform theme, Enter closes the list, the batch is delivered whole, the probe logs the choice and the check, and `state` reports no popup. The user's focus and workspace stay. |
| a click after the Enter that opened a Qt dropdown's list again is held back, and lands once the list is closed | With `QT_QPA_PLATFORMTHEME=gtk3`, Enter closes the list and its press opens it again. `act` delivers 3 of 4 actions, holds back the click with `held_back` and the summary and hint that say why, and its shot shows the list. The probe logs the choice and no check, and `state` reports one popup with `grab` below the dropdown. An `act` with Escape and the click delivers both, the probe logs the check, and the popup is gone. The user's focus and workspace stay. |

### `key-pace`

`nix/slider-probe.py` shows a slider from 0 to 1000 with the keyboard focus, in
Qt 6 or GTK 3 on Wayland, or in Tk on X11, and logs each value it takes.

| Subtest | Checks |
| --- | --- |
| repeated plain keys are sent with 1 ms gaps, and each moves a Qt 6, GTK 3 and Tk slider one step | Each slider probe, alone on a hidden workspace, gets 500 Right, 500 Left and 500 Right presses in three acts, with the default deadline. Each press changes the value once, including every step back to zero; each batch's first and last presses are at least 450 ms apart, with 1 ms gaps. 500 presses of Shift+Right, at 40 ms a press, are refused before anything is sent, with a hint for a deadline of 29 s. The user's focus stays. |
| repeated plain keys remain distinct presses in Wayland and X11 Chromium | Three acts send 500 Right, Left and Right presses using compact arrays on each browser backend. The page independently counts every DOM key-down and key-up, and marks autorepeat events. All 3,000 press/release pairs must arrive, with no autorepeat events or change to the user's focus. |

### `scrolling`

The area raises the output to 1280×800, as large as a laptop's, which the form
task's questionnaire needs, and shows a foot on the visible workspace. It sets
the output back at its end. `combo-probe.py --scroll` shows a column of 200
rows in GTK 3 and logs where it is scrolled to, and Chromium opens
`guest/scroll.html`, a page of 200 rows that logs where it rests.

| Subtest | Checks |
| --- | --- |
| a scroll that ends a batch comes after the changes of the input before it, in a reply of two parts | An `act` on the questionnaire, launched by `hyprmasq-mcp` onto a hidden workspace, ticks Amber and then scrolls three notches over the margin. Its `before_scroll` has a wait that ended `quiet` with a redraw and a changes shot against the launch's shot, with a zoom on the box. After the scroll, the result has a full shot of the window, against that changes shot. The summary has both parts, the images are the zooms and the full shot, and the questionnaire logs Amber. |
| a scroll after input that drew nothing or opened a window is held back | A click on `focus-probe.c`, which draws nothing in answer to it, and a scroll: delivery is partial at the scroll, `held_back` says that the actions before it drew nothing, the summary starts with `1/2 delivered, scroll held back`, the hint says that the scroll was not sent, and the probe logs the click and no scroll. A right click on an opener probe, which opens a dialog, and a scroll: `held_back` names the dialog, and the probe gets no scroll. The user's focus stays. |
| a dropdown under the pointer that takes the wheel shows as a change after the scroll | A click on the Qt probe's check box and a notch over its dropdown: the probe logs the box ticked and Venus chosen. `before_scroll` has a zoom on the box, and the result is a changes shot against that shot, not a full shot, with a zoom on the dropdown, and a hint that a control under the pointer took the wheel. |
| scrolls on X11 move GTK 3 and Chromium by every notch, and px scrolls move them | `combo-probe.py --scroll` on XWayland and then Chromium on X11 with `guest/scroll.html`, one at a time on a hidden workspace, get scrolls through `hyprmasq-input` after motion: one notch, which measures the step, three more and, after a click, two more. Each lands on its notch count times the step, within 3 pixels, where both took the first notch of each scroll for where to count from before. A smooth scroll of 50 px moves both. The user's focus stays. |
| ending an X11 wheel action or smooth scroll keeps its last scroll | Chromium on X11 receives 48 separate sender runs, alternating one and seven wheel notches and reversing before either page end. Every settled position must change by exactly 120 pixels per notch. Then 24 sender runs of a smooth scroll of 50 px must each move the page. This catches sender teardown routing the last queued scroll to the X root window. The user's focus stays. |

### `paging`

The Qt, GTK 3 and GTK 4 list probes and an X11 Chromium page log their true scroll
positions. These tests check page movement at two window heights.

| Subtest | Checks |
| --- | --- |
| page scrolling keeps overlap on Qt, GTK and browser pages at different window heights | At output heights of 800 and 600 pixels, native Qt, GTK 3 and GTK 4 lists and an X11 Chromium page scroll down and back up through `next_page`. Each app logs its true scroll position. The result must measure that position within 3 logical pixels, keep overlap, return a full new view after settling, and leave the user's focus alone. Qt ticks a fixed check box before scrolling and logs its viewport height and row spacing. The inferred moving band may omit up to one row of blank label margins. GTK 4 edits a fixed entry using a shot ID and point pair, tabs out and moves the pointer into the viewport before scrolling. Both calls show the edit before the new view. If unknown redraws stop GTK 4 after one notch, no measured distance is claimed; the next request uses the returned scrollbar-animation evidence and must complete a measured page. |
| a page scroll goes on when its first view releases a frame that the hidden window held back | Chromium at 1280×800 with pauses of 0.5 s and the GTK 4 page at 800×600 with pauses of 3 s scroll down and up twice, each request ending at `page`, not at `reference_changed` after one notch. |
| a page scroll stops when a control takes the wheel without claiming the page ended | A page request over the Qt probe's dropdown sends one notch, selects Venus once, and stops. It reports no page completion or page end, and leaves the user's focus alone. |

### `unload`

| Subtest | Checks |
| --- | --- |
| unload removes all sockets and hooks, reload succeeds | Unloading removes all sockets and answers pending waits, and the hidden Qt window that a `wait … since` kept from being told that it is suspended is told again. It cancels the agent's copy and selection in the clipboard probe, and leaves the user's selections, also once a hidden `xterm` with the agent's X primary selection exits after it. Afterwards, the user's copy and selection in the probe reach Hyprland's own handlers. The plugin loads again. |
| unloading ends screenshot writes that wait for a pipe at once, and Hyprland keeps answering | Unloading while a screenshot waits for a FIFO without a reader, and a raw one for room in the pipe of a reader that never reads, returns within 2 s and answers both with code `unloaded`. `hyprctl version`, run every 100 ms throughout, answers within a second each time. No `hyprmasq-job` thread is left, the library is unmapped, and the batch command works without the plugin. Loaded again, screenshots reach a file, a FIFO whose reader opens it after the screenshot started, and a pipe whose reader reads the raw pixels, more than fit in the pipe, as they come. |
| the plugin can be unloaded from within a batch | The plugin can be unloaded from within a batch. Once the batch has returned, the library is released and the batch command still works, and the plugin loads again. |
| a screenshot whose pixels the GPU still reads back when the plugin unloads is written | In one batch, a screenshot of the green window, and the unload, which runs before the event loop looks whether the GPU has read the pixels back. The screenshot answers `ok`, the plugin is unloaded, and the PNG is green. The plugin loads again. |

### `idle`

| Subtest | Checks |
| --- | --- |
| windows that map and unmap on a hidden workspace and the agent's input end neither idle nor DPMS, and real input does | With `key_press_enables_dpms` and `mouse_move_enables_dpms` on, hypridle with a timeout of a second goes idle and DPMS turns the monitor off. Launching foot on a hidden workspace, typing, pressing Enter, moving and clicking in it through its socket, and closing it neither make hypridle resume nor turn the monitor on. A move of `wlrctl`'s virtual pointer does both. |

### `hooks`

| Subtest | Checks |
| --- | --- |
| an idle hyprmasq holds no input hooks, so another plugin can take one, and input waits for it | With a socket open on a hidden foot, and the leases of earlier areas released, `hooks` shows the input and X11 input groups out and the groups of the plugin's lifetime in, and `hyprmasq-hook-taker-seat.so` loads. `hyprmasq-input` then exits with status 3 and code `hook_held`, naming `CSeatManager::setKeyboardFocus` and another plugin, with delivery `none`: a second later the window has drawn nothing, its shell ran nothing, and the active workspace and window are as before. `hooks` shows the refusal, and a lease is refused with `hook_held` in JSON and with `error:` and status 7 in text, and none is held. Screenshots work on. Once the other plugin unloads, input reaches the shell. |
| hyprmasq loads while another plugin holds an input hook, and only input waits for it | With `hyprmasq-hook-taker-seat.so` loaded first, hyprmasq loads, with the groups of its lifetime in and input out. Screenshots and `eis open` work, and input is refused with `hook_held`. Once the other plugin unloads, input reaches the shell. |
| the input hooks go once the last sender and lease end, so another plugin can take them | While a sender is connected, `hooks` shows the input group in, and the other plugin does not load. Within seconds of the sender's end it shows it out, and the other plugin loads. A lease holds the group without input, also past the second after input, and its release lets it go. |
| senders to windows of two apps share the input hooks, which go only after the last | Senders to two foot windows, of two processes, overlap. `hooks` shows the input group with two claims and one more install than before, installed once for both. Once the first has ended, it shows one claim and still that one install, the group stays in, and the other plugin does not load while the second still sends. After the second has ended, the group goes and the other plugin loads. |
| input to X11 windows needs hooks of its own, which go after it | With `hyprmasq-hook-taker-xwm.so` loaded, input to a hidden `xterm` is refused with `hook_held` naming `CXWM::activateSurface`, while input to a Wayland window goes on without the X11 input group. Once the other plugin unloads, input reaches the `xterm`'s shell, and after the X focus and the window manager's grace for it the group goes and the other plugin loads. |
| an app's own copy that is the agent clipboard holds the touch hook until the user's input ends it | The agent copies in a hidden clipboard probe. Once the input group is out, the clipboard group stays in. The user's focus moving to the probe cancels the copy there, and the clipboard group goes. |
| the input hooks go in and out a hundred times without taking memory each time | A hundred leases that start and end, each waited out until `hooks` shows the input group out, count a hundred installs. Hyprland's anonymous mappings that may be written and executed, where it keeps the code of hooks, grow by less than 8 KiB, where an install of new hooks each time would take 64 KiB. |
| a hook that another plugin holds at load turns off only its own feature | With `hyprmasq-hook-taker-idle.so` loaded first, hyprmasq loads with its idle group out and refused with `hook_held`, naming `CIdleNotifyProtocol::setInhibit`, while input and screenshots work. Loaded again without the other plugin, the group is in. |

### `compositor-log`

| Subtest | Checks |
| --- | --- |
| compositor rejected the emulated move and resize serials | After Hyprland exits and flushes its log, it shows that the move and resize serials were rejected. |
