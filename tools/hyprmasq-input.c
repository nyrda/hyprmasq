// hyprmasq-input: a libei sender for the per-window sockets that hyprmasq creates.
// Run hyprmasq-input --help for options, actions, text tags and exit codes.

#define _GNU_SOURCE

#include <libei.h>
#include <linux/input-event-codes.h>

#include <xkbcommon/xkbcommon-compose.h>
#include <xkbcommon/xkbcommon.h>

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define TIMEOUT_MS     5000
#define REPLACE_MS     1000
#define SETTLE_MS      300
#define MAX_NOTCHES    100
#define MAX_SLEEP_MS   3600000
#define MAX_CHORD_MODS 8
#define MAX_HELD       64
#define MAX_DRAG_STEPS 2000
#define EVDEV_OFFSET   8
// The presses of a key or tag. 500 presses of a key without modifiers take 7.5 s at their pace, and of a chord with
// modifiers 20 s, which the longest deadline of hyprmasq-mcp still fits: enough for a slider of 360 steps, end to end.
// hyprmasq-mcp's maxRepeat repeats it. Change them together.
#define MAX_REPEAT 500
// After SIGINT or SIGTERM, what is left to do gets this long: releasing what is held, confirming it and reporting.
// hyprmasq-mcp stops a sender this long and a margin before its wait and capture start, and kills it 2 s after SIGTERM:
// senderCancel and the constants after it in mcp/internal/server/input.go. Change them together.
#define CANCEL_MS 1000
// Pings in flight; a round trip every ROUND_TRIP_FRAMES frames keeps them fewer.
#define MAX_PINGS         64
#define ROUND_TRIP_FRAMES 16

// Pacing, in milliseconds.
#define ACTION_GAP_MS   50
#define MOTION_PRESS_MS 30
#define CLICK_HOLD_MS   50
#define CLICK_GAP_MS    80
#define CHORD_MOD_MS    10
#define CHORD_HOLD_MS   15
#define CHORD_GAP_MS    15
#define TYPE_HOLD_MS    5
#define TYPE_GAP_MS     10
#define WHEEL_GAP_MS    20
#define DRAG_HOLD_MS    50
#define DRAG_STEP_MS    10
#define DRAG_STEP_PX    10
// 20 intervals of DRAG_STEP_MS spread a drag over at least 200 ms.
#define DRAG_MIN_STEPS 21

#define MS(ms) ((uint64_t)(ms) * 1000000u)

// A key and the modifier keys held around it.
struct chord {
    uint32_t code;
    uint32_t mods[MAX_CHORD_MODS];
    int      modCount;
};

// What was sent: frames, the frames of the pointer, completed actions, and the keys of chords and text without their
// modifiers.
struct counts {
    uint64_t frames, pointerFrames;
    int      actions;
    long     keys;
};

struct state {
    struct ei*        ei;
    struct ei_device* pointer;
    struct ei_device* keyboard;
    bool              pointerReady;
    bool              keyboardReady;
    bool              paused;
    bool              disconnected;
    // The seat is gone, which happens only when the connection ends. libei then answers the pings in flight itself, so
    // pongs after it confirm nothing.
    bool seatRemoved;
    bool stalled;
    // The server removed a device that had unconfirmed events or held keys or buttons.
    bool lost;
    // All that the running action has left to send are releases of what it holds, which hyprmasq sends itself when the
    // connection ends.
    bool     releasing;
    bool     pace;
    uint32_t sequence;
    // A pong tells that the plugin has taken everything sent before its ping, and unless it paused or removed a device
    // meanwhile, processed it: the counts its ping was sent with are taken, or confirmed.
    struct counts sent, taken, confirmed;
    struct counts marks[MAX_PINGS];
    uint64_t      pings, answered, awaited;
    int           sinceRoundTrip;
    uint64_t      lastMotion;
    // Keys and buttons pressed and not released yet, in the order they were pressed.
    uint32_t heldKeys[MAX_HELD], heldButtons[MAX_HELD];
    int      heldKeyCount, heldButtonCount;
    // Loaded from the keyboard device for the actions that need it.
    struct xkb_context*       xkb;
    struct xkb_keymap*        keymap;
    struct xkb_compose_table* compose;
    // The table was looked for, so a missing one is not looked for again.
    bool composeLoaded;
    // For each modifier index, the evdev code of the key that sets it, or 0.
    uint32_t modifierKeys[32];
};

enum actionType {
    ACTION_MOVE,
    ACTION_CLICK,
    ACTION_DOWN,
    ACTION_UP,
    ACTION_DRAG,
    ACTION_SCROLL,
    ACTION_WHEEL,
    ACTION_KEY,
    ACTION_KEYS,
    ACTION_TYPE,
    ACTION_TEXT,
    ACTION_SLEEP,
    ACTION_SYNC,
};

static const struct {
    const char* name;
    const char* syntax;
} actionSyntax[] = {
    [ACTION_MOVE]   = {"move", "move X Y"},
    [ACTION_CLICK]  = {"click", "click [BUTTON [COUNT [MODS]]], COUNT 1 to 3"},
    [ACTION_DOWN]   = {"down", "down BUTTON"},
    [ACTION_UP]     = {"up", "up BUTTON"},
    [ACTION_DRAG]   = {"drag", "drag X1 Y1 X2 Y2 [BUTTON [MODS]]"},
    [ACTION_SCROLL] = {"scroll", "scroll DX DY"},
    [ACTION_WHEEL]  = {"wheel", "wheel DX DY, whole notches up to 100"},
    [ACTION_KEY]    = {"key", "key CODE"},
    [ACTION_KEYS]   = {"keys", "keys CHORD [REPEAT], REPEAT 1 to 500"},
    [ACTION_TYPE]   = {"type", "type TEXT"},
    [ACTION_TEXT]   = {"text", "text TEXT"},
    [ACTION_SLEEP]  = {"sleep", "sleep MS, up to 3600000"},
    [ACTION_SYNC]   = {"sync", "sync MIN MAX, MIN up to MAX up to 3600000"},
};

#define ACTION_TYPES (sizeof(actionSyntax) / sizeof(actionSyntax[0]))

struct step;

struct steps {
    struct step* items;
    size_t       count, capacity;
};

struct action {
    enum actionType type;
    double          x, y, x2, y2;
    uint32_t        code;   // the button or the key code
    long            count;  // clicks, repeats or milliseconds, the least of sync
    long            limit;  // the most milliseconds of sync
    long            dx, dy; // wheel notches
    const char*     text;   // the text of type and text, the chord of keys
    const char*     mods;   // the modifiers of click and drag
    struct chord    chord;  // the chord of keys, the modifiers of click and drag
    struct steps    steps;  // the keys of type and text
    long            keysAt; // the keys sent before the action started, or -1
};

// The first failure, which decides the exit status and the JSON error.
static struct {
    const char* code;
    char        message[512];
} failure;

static volatile sig_atomic_t cancelled;
// SIGINT and SIGTERM are blocked except while waiting for the server, so no wait can miss one.
static sigset_t waitMask;
// When the first of them arrived. It is only written in a wait, so it is never read half written.
static uint64_t cancelledAt;
// The milliseconds of --budget, or -1, and the least time the actions take at their pace once resolved, or -1.
static long budgetMs = -1, pacedMs = -1;
// The locale whose Compose table was asked for: --locale, or the sender's own. The one whose table was loaded, which
// differs when there was none for the first. Whether a character was typed through it.
static const char *composeRequested, *composeLocale;
static bool        composed;

static void usage(FILE* out) {
    fputs("usage: hyprmasq-input --socket PATH [OPTIONS] ACTION...\n"
          "Try 'hyprmasq-input --help' for options and actions.\n",
          out);
}

static void help(void) {
    fputs("Send input to a window's hyprmasq libei socket.\n\n"
          "Usage: hyprmasq-input --socket PATH [OPTIONS] ACTION...\n"
          "       hyprmasq-input --help | --version\n\n"
          "Options:\n"
          "  --socket PATH  Connect to this window's input socket. Required.\n"
          "  --no-pace      Omit delays that pace input like a person's.\n"
          "  --json         Print the outcome as JSON on stdout.\n"
          "  --budget MS    Refuse actions that cannot finish within MS, 0 to 3600000.\n"
          "  --locale NAME  Use the target app's Compose locale. Default: sender's locale.\n"
          "  -h, --help     Show this help and exit.\n"
          "  --version      Show the version and exit.\n\n"
          "Actions, in order:\n"
          "  move X Y                          Move the pointer.\n"
          "  click [BUTTON [COUNT [MODS]]]     Click 1 to 3 times. Default: left, 1.\n"
          "  down BUTTON / up BUTTON           Press / release a button.\n"
          "  drag X1 Y1 X2 Y2 [BUTTON [MODS]]  Press, move in steps, release.\n"
          "  scroll DX DY                      Smooth scroll in logical pixels.\n"
          "  wheel DX DY                       Scroll whole notches, -100 to 100.\n"
          "  key CODE                          Press and release an evdev key code.\n"
          "  keys CHORD [REPEAT]               Press a chord 1 to 500 times. Default: 1.\n"
          "  type TEXT                         Type text with key tags and escapes.\n"
          "  text TEXT                         Type literal text without tags or escapes.\n"
          "  sleep MS                          Wait 0 to 3600000 milliseconds.\n"
          "  sync MIN MAX                      Print {\"sync\":N} after the server processes\n"
          "                                    the N preceding actions, then wait for a\n"
          "                                    line with N on stdin, at least MIN and at\n"
          "                                    most MAX milliseconds, 0 <= MIN <= MAX <=\n"
          "                                    3600000.\n\n"
          "Coordinates are window-local logical pixels within the pointer regions.\n"
          "BUTTON is left, right, middle or an evdev code, e.g. 0x113.\n"
          "MODS joins modifiers with +, e.g. ctrl+shift; - means none.\n"
          "CHORD joins key names with +, e.g. ctrl+shift+t or enter; use plus for +.\n"
          "Text uses the server's keymap and the target locale's Compose table.\n\n"
          "Key tags in type TEXT, case-insensitive:\n"
          "  <enter>, <tab>, <esc>, <backspace>, <delete>, <up>, <down>, <left>,\n"
          "  <right>, <home>, <end>, <pageup>, <pagedown>, <space>, <F5>\n"
          "  <KEY:COUNT>         Repeat a named key or non-printable keysym, 1 to 500.\n"
          "  <shortcut:ctrl+l>   Hold modifiers around a key.\n"
          "  <wait:MS>           Wait 0 to 3600000 milliseconds.\n"
          "  <settle>            Wait 300 milliseconds.\n"
          "  \\<                  Type a literal <. Unknown tags remain literal text.\n\n"
          "Every action is checked before any input is sent. Held input is released\n"
          "when the batch ends or fails. A closed window's remaining releases count\n"
          "as done if the server confirmed all events sent before it closed.\n\n"
          "Exit codes:\n"
          "  0  The server confirmed all input, or help/version was printed.\n"
          "  1  Runtime failure, including connection failure, memory exhaustion or signal.\n"
          "  2  Invalid arguments, untypable input or exceeded budget; no input sent.\n",
          stdout);
}

static void fail(const char* code, const char* format, ...) {
    char    message[sizeof(failure.message)];
    va_list args;
    va_start(args, format);
    vsnprintf(message, sizeof(message), format, args);
    va_end(args);
    fprintf(stderr, "hyprmasq-input: %s\n", message);
    if (!failure.code) {
        failure.code = code;
        memcpy(failure.message, message, sizeof(message));
    }
}

static uint64_t nowNs(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000u + ts.tv_nsec;
}

static void onSignal(int signal) {
    (void)signal;
    if (!cancelled)
        cancelledAt = nowNs();
    cancelled = 1;
}

// The plugin replaces the pointer when the window is resized or moved. It drops the events that reach the old one and
// releases what the old one held, so input is only lost if the old one had unconfirmed frames or held buttons.
static void forgetDevice(struct state* s, struct ei_device* device) {
    const bool     pointer   = device == s->pointer;
    const uint64_t sent      = pointer ? s->sent.pointerFrames : s->sent.frames - s->sent.pointerFrames;
    const uint64_t confirmed = pointer ? s->confirmed.pointerFrames : s->confirmed.frames - s->confirmed.pointerFrames;
    if (sent != confirmed || (pointer ? s->heldButtonCount : s->heldKeyCount))
        s->lost = true;
    if (pointer) {
        s->heldButtonCount = 0;
        s->pointer         = NULL;
        s->pointerReady    = false;
    } else {
        s->heldKeyCount  = 0;
        s->keyboard      = NULL;
        s->keyboardReady = false;
    }
    ei_device_unref(device);
}

static void handleEvent(struct state* s, struct ei_event* event) {
    struct ei_device* device = ei_event_get_device(event);

    switch (ei_event_get_type(event)) {
        case EI_EVENT_SEAT_ADDED:
            ei_seat_bind_capabilities(ei_event_get_seat(event), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL, EI_DEVICE_CAP_KEYBOARD, NULL);
            break;
        case EI_EVENT_DEVICE_ADDED:
            // A new pointer replaces the old one, also if the server adds it before removing the old one.
            if (ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE)) {
                if (s->pointer)
                    forgetDevice(s, s->pointer);
                s->pointer = ei_device_ref(device);
            } else if (!s->keyboard && ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD))
                s->keyboard = ei_device_ref(device);
            break;
        case EI_EVENT_DEVICE_REMOVED:
            if (device == s->pointer || device == s->keyboard)
                forgetDevice(s, device);
            break;
        case EI_EVENT_DEVICE_RESUMED:
            if (device == s->pointer)
                s->pointerReady = true;
            else if (device == s->keyboard)
                s->keyboardReady = true;
            else
                break;
            ei_device_start_emulating(device, ++s->sequence);
            break;
        case EI_EVENT_DEVICE_PAUSED:
            if (device == s->pointer)
                s->pointerReady = false;
            else if (device == s->keyboard)
                s->keyboardReady = false;
            else
                break;
            s->paused = true;
            break;
        case EI_EVENT_PONG: {
            // Pongs come in the order of the pings, and in order with pauses and removed devices. After a pause or a lost
            // device, the plugin dropped some of the events before the ping.
            const uint64_t id = (uintptr_t)ei_ping_get_user_data(ei_event_pong_get_ping(event));
            if (id > s->answered)
                s->answered = id;
            if (!id || s->seatRemoved || s->disconnected)
                break;
            s->taken = s->marks[id % MAX_PINGS];
            if (!s->paused && !s->lost)
                s->confirmed = s->taken;
            break;
        }
        case EI_EVENT_SEAT_REMOVED: s->seatRemoved = true; break;
        case EI_EVENT_DISCONNECT: s->disconnected = true; break;
        default: break;
    }
}

// libei disconnects at once when a send fails, as it does once the plugin has closed the socket, and queues the events
// of the disconnect: the removed devices and seat, and a pong that confirms nothing for every ping in flight and for the
// ping it failed to send. Nothing else queues events outside ei_dispatch, after which pump() takes them all. Returns
// false once the connection is gone.
static bool connected(struct state* s) {
    struct ei_event* event = ei_get_event(s->ei);
    if (event) {
        s->disconnected = true;
        do {
            handleEvent(s, event);
            ei_event_unref(event);
        } while ((event = ei_get_event(s->ei)));
    }
    return !s->disconnected;
}

// Handles server events until done() is true, the server disconnects, or the deadline passes, which a signal brings
// forward to CANCEL_MS after it.
static bool pump(struct state* s, uint64_t deadline, bool (*done)(const struct state*)) {
    struct pollfd pfd = {.fd = ei_get_fd(s->ei), .events = POLLIN};

    for (;;) {
        ei_dispatch(s->ei);
        for (struct ei_event* event; (event = ei_get_event(s->ei));) {
            handleEvent(s, event);
            ei_event_unref(event);
        }
        if (done(s))
            return true;
        const uint64_t now = nowNs();
        if (cancelled && cancelledAt + MS(CANCEL_MS) < deadline)
            deadline = cancelledAt + MS(CANCEL_MS);
        if (s->disconnected || now >= deadline)
            return false;
        const uint64_t  left    = deadline - now;
        struct timespec timeout = {.tv_sec = left / 1000000000u, .tv_nsec = left % 1000000000u};
        if (ppoll(&pfd, 1, &timeout, &waitMask) < 0 && errno != EINTR)
            return false;
    }
}

static bool devicesReady(const struct state* s) {
    return (s->pointerReady && s->keyboardReady) || cancelled;
}

static bool answered(const struct state* s) {
    return s->answered >= s->awaited;
}

// Whether a signal arrived, also one that arrived outside a wait.
static bool signalled(void) {
    static const struct timespec now = {0};
    if (!cancelled)
        ppoll(NULL, 0, &now, &waitMask);
    return cancelled;
}

// Whether the actions have to stop.
static bool interrupted(const struct state* s) {
    return signalled() || s->disconnected || s->stalled || s->paused || s->lost;
}

static bool pointerBack(const struct state* s) {
    return s->pointerReady || interrupted(s);
}

// Sends a ping that confirms what was sent so far once its pong arrives, unless the server paused or removed a device
// meanwhile. Returns its id, or 0.
static uint64_t ping(struct state* s) {
    if (s->disconnected || s->stalled)
        return 0;
    struct ei_ping* ping = ei_new_ping(s->ei);
    if (!ping)
        return 0;
    const uint64_t id        = ++s->pings;
    s->marks[id % MAX_PINGS] = s->sent;
    ei_ping_set_user_data(ping, (void*)(uintptr_t)id);
    ei_ping(ping);
    ei_ping_unref(ping);
    return connected(s) ? id : 0;
}

// Waits for a ping's pong: once it returns, the server has processed every event sent before.
static bool roundTrip(struct state* s) {
    s->sinceRoundTrip = 0;
    if (!(s->awaited = ping(s)))
        return false;
    if (!pump(s, nowNs() + MS(TIMEOUT_MS), answered) || s->disconnected)
        s->stalled = true;
    return !s->stalled;
}

static void frame(struct state* s, struct ei_device* device) {
    ei_device_frame(device, ei_now(s->ei));
    ++s->sent.frames;
    if (device == s->pointer)
        ++s->sent.pointerFrames;
    // libei 1.6 crashes on a ping after a failed send has closed its socket.
    if (!connected(s))
        return;
    // A ping after every frame tells which frames the plugin had processed when it paused input or removed a device.
    ping(s);
    // libei 1.6 drops the rest of a message that a full socket only takes in part, and the server then disconnects on
    // the broken stream. Waiting for the server every few frames keeps the socket from filling up during long text.
    if (++s->sinceRoundTrip >= ROUND_TRIP_FRAMES)
        roundTrip(s);
}

// Waits until the deadline while handling server events. False if the actions have to stop.
static bool waitUntil(struct state* s, uint64_t deadline) {
    pump(s, deadline, interrupted);
    return !interrupted(s);
}

// A wait that paces input, which --no-pace leaves out.
static bool paceWait(struct state* s, long ms) {
    return s->pace && ms ? waitUntil(s, nowNs() + MS(ms)) : !interrupted(s);
}

static bool track(uint32_t* held, int* count, uint32_t code, bool press) {
    for (int i = 0; i < *count; ++i)
        if (held[i] == code) {
            if (!press) {
                memmove(held + i, held + i + 1, (*count - i - 1) * sizeof(*held));
                --*count;
            }
            return true;
        }
    if (press) {
        if (*count == MAX_HELD) {
            fail("too_many_held", "cannot hold more than %d keys or buttons per device", MAX_HELD);
            return false;
        }
        held[(*count)++] = code;
    }
    return true;
}

static bool useKeyboard(struct state* s) {
    if (interrupted(s))
        return false;
    if (!s->keyboardReady) {
        // hyprmasq removes the keyboard only when it closes the connection, whose end may still be on its way.
        roundTrip(s);
        if (!interrupted(s))
            fail("no_devices", "the server removed the keyboard");
        return false;
    }
    return true;
}

// A pointer that the plugin replaced gets a second to come back.
static bool usePointer(struct state* s) {
    if (!s->pointerReady && !interrupted(s))
        pump(s, nowNs() + MS(REPLACE_MS), pointerBack);
    if (interrupted(s))
        return false;
    if (!s->pointerReady) {
        fail("no_devices", "the server removed the pointer and offered no new one");
        return false;
    }
    return true;
}

// Presses of keys that count are the progress of keys, type and text; modifiers do not count.
static bool sendKey(struct state* s, uint32_t code, bool press, bool counts) {
    if (!useKeyboard(s))
        return false;
    if (!track(s->heldKeys, &s->heldKeyCount, code, press))
        return false;
    ei_device_keyboard_key(s->keyboard, code, press);
    s->sent.keys += press && counts;
    frame(s, s->keyboard);
    return !interrupted(s);
}

static bool inRegion(struct ei_device* device, double x, double y) {
    return !ei_device_get_region(device, 0) || ei_device_get_region_at(device, x, y);
}

// libei drops motion outside the pointer's regions without a word, so the points of the actions are checked first.
// hyprmasq's regions are the window and the area right of and below it, up to its monitor's edges: libei region
// offsets are unsigned, so nothing left of or above the window can be reached.
static bool checkPoint(struct state* s, double x, double y) {
    if (inRegion(s->pointer, x, y))
        return true;
    uint32_t          left = UINT32_MAX, top = UINT32_MAX, right = 0, bottom = 0;
    struct ei_region* r;
    for (size_t i = 0; (r = ei_device_get_region(s->pointer, i)); ++i) {
        left   = ei_region_get_x(r) < left ? ei_region_get_x(r) : left;
        top    = ei_region_get_y(r) < top ? ei_region_get_y(r) : top;
        right  = ei_region_get_x(r) + ei_region_get_width(r) > right ? ei_region_get_x(r) + ei_region_get_width(r) : right;
        bottom = ei_region_get_y(r) + ei_region_get_height(r) > bottom ? ei_region_get_y(r) + ei_region_get_height(r) : bottom;
    }
    fail("invalid_arguments", "%g,%g is outside the pointer regions, which span %u,%u to %u,%u", x, y, left, top, right, bottom);
    return false;
}

// Interpolated points of a drag that leave the region are skipped, as libei would.
static bool sendMotion(struct state* s, double x, double y, bool exact) {
    if (!usePointer(s))
        return false;
    if (!inRegion(s->pointer, x, y))
        return !exact || checkPoint(s, x, y);
    ei_device_pointer_motion_absolute(s->pointer, x, y);
    frame(s, s->pointer);
    s->lastMotion = nowNs();
    return !interrupted(s);
}

static bool sendButton(struct state* s, uint32_t button, bool press) {
    // A press right after motion can reach the application before it has handled the motion.
    if (press && s->pace && s->lastMotion && !waitUntil(s, s->lastMotion + MS(MOTION_PRESS_MS)))
        return false;
    if (!usePointer(s))
        return false;
    if (!track(s->heldButtons, &s->heldButtonCount, button, press))
        return false;
    ei_device_button_button(s->pointer, button, press);
    frame(s, s->pointer);
    return !interrupted(s);
}

// Releases every key and button still pressed, also after a failure or a signal. Buttons go first, as at the end of a
// click or drag, so that the modifiers held around them still apply to the release.
static void releaseAll(struct state* s) {
    while (s->heldButtonCount) {
        const uint32_t button = s->heldButtons[--s->heldButtonCount];
        if (s->pointerReady && !s->disconnected) {
            ei_device_button_button(s->pointer, button, false);
            frame(s, s->pointer);
        }
    }
    while (s->heldKeyCount) {
        const uint32_t code = s->heldKeys[--s->heldKeyCount];
        if (s->keyboardReady && !s->disconnected) {
            ei_device_keyboard_key(s->keyboard, code, false);
            frame(s, s->keyboard);
        }
    }
}

// Modifiers go down one per frame, before the key or button they modify.
static bool pressMods(struct state* s, const struct chord* c, long ms) {
    for (int i = 0; i < c->modCount; ++i)
        if (!sendKey(s, c->mods[i], true, false))
            return false;
    return !c->modCount || paceWait(s, ms);
}

static bool releaseMods(struct state* s, const struct chord* c) {
    for (int i = c->modCount - 1; i >= 0; --i)
        if (!sendKey(s, c->mods[i], false, false))
            return false;
    return true;
}

// last tells that the chord is the action's last, after whose key only releases are left.
static bool pressChord(struct state* s, const struct chord* c, long modMs, long holdMs, bool last) {
    if (!pressMods(s, c, modMs) || !sendKey(s, c->code, true, true))
        return false;
    s->releasing = last;
    return paceWait(s, holdMs) && sendKey(s, c->code, false, false) && releaseMods(s, c);
}

static bool parseButton(const char* name, uint32_t* button) {
    if (!strcmp(name, "left"))
        *button = BTN_LEFT;
    else if (!strcmp(name, "right"))
        *button = BTN_RIGHT;
    else if (!strcmp(name, "middle"))
        *button = BTN_MIDDLE;
    else {
        char*               end  = NULL;
        const unsigned long code = strtoul(name, &end, 0);
        // libei drops codes below BTN_MOUSE without a word, such as 1 for the left button.
        if (!end || end == name || *end != '\0' || code < BTN_MOUSE || code > KEY_MAX)
            return false;
        *button = code;
    }
    return true;
}

static bool parseDouble(const char* text, double* out) {
    char* end = NULL;
    *out      = strtod(text, &end);
    return end && end != text && *end == '\0' && isfinite(*out);
}

static bool parseCount(const char* text, long min, long max, long* out) {
    char* end = NULL;
    errno     = 0;
    *out      = strtol(text, &end, 10);
    return end != text && *end == '\0' && errno == 0 && *out >= min && *out <= max;
}

static bool addMod(struct chord* c, uint32_t code) {
    for (int i = 0; i < c->modCount; ++i)
        if (c->mods[i] == code)
            return true;
    if (c->modCount == MAX_CHORD_MODS)
        return false;
    c->mods[c->modCount++] = code;
    return true;
}

// Keypad, ISO and extended keys. Keymaps put characters on them too, but a main-block key wins when there is one.
static bool offMainBlock(uint32_t code) {
    switch (code) {
        case KEY_KPASTERISK:
        case KEY_KP7 ... KEY_KPDOT:
        case KEY_102ND:
        case KEY_KPJPCOMMA:
        case KEY_KPENTER:
        case KEY_KPSLASH:
        case KEY_KPEQUAL:
        case KEY_KPPLUSMINUS:
        case KEY_KPCOMMA: return true;
        default: return code > KEY_COMPOSE;
    }
}

// xkeyboard-config's fake keys <LVL3>, <MDSW>, <ALT>, <META>, <SUPR> and <HYPR>, which no keyboard has.
static bool virtualKey(uint32_t code) {
    return code == 84 || (code >= 195 && code <= 199);
}

static bool keyBefore(uint32_t a, uint32_t b) {
    return offMainBlock(a) != offMainBlock(b) ? !offMainBlock(a) : a < b;
}

// Finds the key that sets each modifier while held, as the keymap defines it: Right Alt reaches the third level on most
// layouts, Caps Lock does on Neo. Keys whose modifier stays on after release, like Caps Lock on most layouts, are
// skipped. Left Shift wins over Right Shift by the lower code.
static void findModifierKeys(struct state* s) {
    const xkb_keycode_t max = xkb_keymap_max_keycode(s->keymap);
    for (xkb_keycode_t kc = xkb_keymap_min_keycode(s->keymap); kc <= max; ++kc) {
        if (kc < EVDEV_OFFSET || virtualKey(kc - EVDEV_OFFSET))
            continue;
        struct xkb_state* st = xkb_state_new(s->keymap);
        if (!st)
            return;
        xkb_state_update_key(st, kc, XKB_KEY_DOWN);
        const xkb_mod_mask_t held = xkb_state_serialize_mods(st, XKB_STATE_MODS_EFFECTIVE);
        xkb_state_update_key(st, kc, XKB_KEY_UP);
        const xkb_mod_mask_t after = xkb_state_serialize_mods(st, XKB_STATE_MODS_EFFECTIVE);
        xkb_state_unref(st);
        if (!held || after)
            continue;
        const uint32_t code = kc - EVDEV_OFFSET;
        for (int i = 0; i < 32; ++i)
            if ((held & (1u << i)) && (!s->modifierKeys[i] || keyBefore(code, s->modifierKeys[i])))
                s->modifierKeys[i] = code;
    }
}

static bool loadKeymap(struct state* s) {
    if (s->keymap)
        return true;
    struct ei_keymap* km = ei_device_keyboard_get_keymap(s->keyboard);
    if (!km || ei_keymap_get_type(km) != EI_KEYMAP_TYPE_XKB) {
        fail("no_devices", "the server did not send an XKB keymap");
        return false;
    }
    const size_t size = ei_keymap_get_size(km);
    char*        map  = mmap(NULL, size, PROT_READ, MAP_PRIVATE, ei_keymap_get_fd(km), 0);
    if (map == MAP_FAILED) {
        fail("no_devices", "cannot read the keymap: %s", strerror(errno));
        return false;
    }
    char* text = strndup(map, size);
    munmap(map, size);
    s->xkb = xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES | XKB_CONTEXT_NO_ENVIRONMENT_NAMES);
    if (text && s->xkb)
        s->keymap = xkb_keymap_new_from_string(s->xkb, text, XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
    free(text);
    if (!s->keymap) {
        fail("no_devices", "cannot compile the keymap");
        return false;
    }
    findModifierKeys(s);
    return true;
}

// Picks the fewest modifier keys that reach a level through one of its masks.
static bool levelMods(const struct state* s, const xkb_mod_mask_t* masks, size_t count, struct chord* c) {
    bool found = false;
    for (size_t m = 0; m < count; ++m) {
        struct chord candidate = {.code = c->code};
        bool         ok        = true;
        for (int i = 0; ok && i < 32; ++i)
            if (masks[m] & (1u << i))
                ok = s->modifierKeys[i] && addMod(&candidate, s->modifierKeys[i]);
        if (ok && (!found || candidate.modCount < c->modCount)) {
            *c    = candidate;
            found = true;
        }
    }
    return found;
}

// A held key may set more modifiers than the level asks for, so the chord is checked against the keymap.
static bool chordProduces(const struct state* s, const struct chord* c, xkb_keysym_t sym) {
    struct xkb_state* st = xkb_state_new(s->keymap);
    if (!st)
        return false;
    for (int i = 0; i < c->modCount; ++i)
        xkb_state_update_key(st, c->mods[i] + EVDEV_OFFSET, XKB_KEY_DOWN);
    const bool ok = xkb_state_key_get_one_sym(st, c->code + EVDEV_OFFSET) == sym;
    xkb_state_unref(st);
    return ok;
}

// Finds the simplest chord that produces the keysym sym, or with sym 0, a keysym for the character codepoint:
// main-block keys first, then fewer modifiers, the lower level and the lower code. The plugin starts every connection
// in the keymap's first layout, so only that one is searched.
static bool findChord(const struct state* s, xkb_keysym_t sym, uint32_t codepoint, struct chord* best) {
    bool                found     = false;
    int                 bestLevel = 0;
    const xkb_keycode_t max       = xkb_keymap_max_keycode(s->keymap);
    for (xkb_keycode_t kc = xkb_keymap_min_keycode(s->keymap); kc <= max; ++kc) {
        if (kc < EVDEV_OFFSET || virtualKey(kc - EVDEV_OFFSET) || xkb_keymap_num_layouts_for_key(s->keymap, kc) == 0)
            continue;
        const xkb_level_index_t levels = xkb_keymap_num_levels_for_key(s->keymap, kc, 0);
        for (xkb_level_index_t level = 0; level < levels; ++level) {
            const xkb_keysym_t* syms;
            if (xkb_keymap_key_get_syms_by_level(s->keymap, kc, 0, level, &syms) != 1)
                continue;
            if (sym ? syms[0] != sym : xkb_keysym_to_utf32(syms[0]) != codepoint)
                continue;
            xkb_mod_mask_t masks[8];
            size_t         count = xkb_keymap_key_get_mods_for_level(s->keymap, kc, 0, level, masks, 8);
            if (count == 0 && level == 0)
                masks[count++] = 0;
            struct chord c = {.code = kc - EVDEV_OFFSET};
            if (!levelMods(s, masks, count, &c) || !chordProduces(s, &c, syms[0]))
                continue;
            if (found) {
                if (offMainBlock(c.code) != offMainBlock(best->code)) {
                    if (offMainBlock(c.code))
                        continue;
                } else if (c.modCount != best->modCount) {
                    if (c.modCount > best->modCount)
                        continue;
                } else if ((int)level != bestLevel ? (int)level > bestLevel : c.code > best->code)
                    continue;
            }
            *best     = c;
            bestLevel = level;
            found     = true;
        }
    }
    return found;
}

// Decodes one UTF-8 character; returns its length, or 0 if the bytes are not valid UTF-8.
static int decodeUtf8(const char* p, uint32_t* cp) {
    const unsigned char* u = (const unsigned char*)p;
    int                  len;
    uint32_t             min;
    if (u[0] < 0x80) {
        *cp = u[0];
        return 1;
    } else if ((u[0] & 0xE0) == 0xC0) {
        len = 2, min = 0x80, *cp = u[0] & 0x1F;
    } else if ((u[0] & 0xF0) == 0xE0) {
        len = 3, min = 0x800, *cp = u[0] & 0x0F;
    } else if ((u[0] & 0xF8) == 0xF0) {
        len = 4, min = 0x10000, *cp = u[0] & 0x07;
    } else
        return 0;
    for (int i = 1; i < len; ++i) {
        if ((u[i] & 0xC0) != 0x80)
            return 0;
        *cp = (*cp << 6) | (u[i] & 0x3F);
    }
    return *cp >= min && *cp <= 0x10FFFF && (*cp < 0xD800 || *cp > 0xDFFF) ? len : 0;
}

static bool validUtf8(const char* text) {
    uint32_t cp;
    for (int len; *text; text += len)
        if (!(len = decodeUtf8(text, &cp)))
            return false;
    return true;
}

// One step of a type or text action: a chord, or a wait when code is 0.
struct step {
    struct chord chord;
    long         waitMs;
};

static bool addStep(struct steps* steps, struct step step) {
    if (steps->count == steps->capacity) {
        const size_t capacity = steps->capacity ? steps->capacity * 2 : 64;
        struct step* items    = realloc(steps->items, capacity * sizeof(*items));
        if (!items) {
            fail("out_of_memory", "out of memory");
            return false;
        }
        steps->items    = items;
        steps->capacity = capacity;
    }
    steps->items[steps->count++] = step;
    return true;
}

// The table that most UTF-8 locales use, and that GTK's built-in table follows.
#define FALLBACK_COMPOSE_LOCALE "en_US.UTF-8"

// Loads the Compose table of the target application's locale: --locale, or else the sender's own, the first of LC_ALL,
// LC_CTYPE and LANG that is set, or C, as Qt and libxkbcommon pick it. Without a table for that locale, the dead keys
// of the application may compose differently or not at all, so the most common table stands in for it, and the
// outcome says so.
static bool loadCompose(struct state* s) {
    if (s->composeLoaded)
        return s->compose;
    s->composeLoaded                     = true;
    static const char* const variables[] = {"LC_ALL", "LC_CTYPE", "LANG"};
    for (size_t i = 0; i < 3 && !composeRequested; ++i)
        if ((composeRequested = getenv(variables[i])) && !*composeRequested)
            composeRequested = NULL;
    if (!composeRequested)
        composeRequested = "C";
    composeLocale = composeRequested;
    s->compose    = xkb_compose_table_new_from_locale(s->xkb, composeLocale, XKB_COMPOSE_COMPILE_NO_FLAGS);
    if (!s->compose && strcmp(composeLocale, FALLBACK_COMPOSE_LOCALE)) {
        composeLocale = FALLBACK_COMPOSE_LOCALE;
        s->compose    = xkb_compose_table_new_from_locale(s->xkb, composeLocale, XKB_COMPOSE_COMPILE_NO_FLAGS);
        if (s->compose)
            fprintf(stderr, "hyprmasq-input: no Compose table for the locale '%s', so the table of %s is used\n", composeRequested, composeLocale);
    }
    return s->compose;
}

// Characters without a key of their own, such as ^ and ` on German layouts, are typed as the shortest sequence of
// the target's Compose table that the keymap can press, usually a dead key followed by Space. GTK, Qt and foot apply
// the table of their own locale to the keys they receive, so the sender has to use the same one.
static bool addComposed(struct state* s, struct steps* steps, uint32_t codepoint) {
    if (!loadCompose(s))
        return false;
    struct chord*                      best       = NULL;
    size_t                             bestLength = 0;
    struct xkb_compose_table_iterator* it         = xkb_compose_table_iterator_new(s->compose);
    for (struct xkb_compose_table_entry* entry; it && (entry = xkb_compose_table_iterator_next(it));) {
        const char*         utf8 = xkb_compose_table_entry_utf8(entry);
        uint32_t            cp;
        size_t              length;
        const xkb_keysym_t* syms = xkb_compose_table_entry_sequence(entry, &length);
        if (!utf8 || decodeUtf8(utf8, &cp) != (int)strlen(utf8) || cp != codepoint || length == 0 || (best && length >= bestLength))
            continue;
        struct chord* chords = calloc(length, sizeof(*chords));
        if (!chords) {
            fail("out_of_memory", "out of memory");
            free(best);
            xkb_compose_table_iterator_free(it);
            return false;
        }
        size_t i = 0;
        while (chords && i < length && findChord(s, syms[i], 0, &chords[i]))
            ++i;
        if (chords && i == length) {
            free(best);
            best       = chords;
            bestLength = length;
        } else
            free(chords);
    }
    xkb_compose_table_iterator_free(it);
    bool ok = best;
    for (size_t i = 0; ok && i < bestLength; ++i)
        ok = addStep(steps, (struct step){.chord = best[i]});
    free(best);
    composed |= ok;
    return ok;
}

static bool addChar(struct state* s, struct steps* steps, uint32_t codepoint) {
    struct step step    = {0};
    const bool  newline = codepoint == '\n' || codepoint == '\r';
    if (!findChord(s, newline ? XKB_KEY_Return : 0, codepoint, &step.chord)) {
        if (!newline && addComposed(s, steps, codepoint))
            return true;
        if (failure.code)
            return false;
        if (newline || !composeLocale)
            fail("cannot_type", "the keymap cannot type U+%04X", codepoint);
        else if (!s->compose)
            fail("cannot_type", "the keymap cannot type U+%04X, and there is no Compose table for the locale %s", codepoint, composeRequested);
        else
            fail("cannot_type", "the keymap cannot type U+%04X, also not through the Compose table of %s", codepoint, composeLocale);
        return false;
    }
    return addStep(steps, step);
}

// Named keys resolve through their keysyms, so they follow keymap options such as caps:swapescape.
static const struct {
    const char*  name;
    xkb_keysym_t sym;
} namedKeys[] = {
    {"space", XKB_KEY_space},   {"enter", XKB_KEY_Return}, {"tab", XKB_KEY_Tab},      {"esc", XKB_KEY_Escape},    {"backspace", XKB_KEY_BackSpace},
    {"delete", XKB_KEY_Delete}, {"up", XKB_KEY_Up},        {"down", XKB_KEY_Down},    {"left", XKB_KEY_Left},     {"right", XKB_KEY_Right},
    {"home", XKB_KEY_Home},     {"end", XKB_KEY_End},      {"pageup", XKB_KEY_Prior}, {"pagedown", XKB_KEY_Next},
};

static xkb_keysym_t namedKeysym(const char* name) {
    for (size_t i = 0; i < sizeof(namedKeys) / sizeof(namedKeys[0]); ++i)
        if (!strcmp(namedKeys[i].name, name))
            return namedKeys[i].sym;
    return XKB_KEY_NoSymbol;
}

// The modifier names of shortcuts, keys and MODS. The keys that set these modifiers come from the keymap, so options
// such as ctrl:swap_lalt_lctl are followed.
static const struct {
    const char* name;
    const char* modifier;
} modifierNames[] = {
    {"ctrl", XKB_MOD_NAME_CTRL},  {"control", XKB_MOD_NAME_CTRL}, {"shift", XKB_MOD_NAME_SHIFT}, {"alt", XKB_MOD_NAME_ALT},
    {"super", XKB_MOD_NAME_LOGO}, {"meta", XKB_MOD_NAME_LOGO},    {"win", XKB_MOD_NAME_LOGO},    {"cmd", XKB_MOD_NAME_LOGO},
};

static const char* modifierName(const char* lower) {
    for (size_t i = 0; i < sizeof(modifierNames) / sizeof(modifierNames[0]); ++i)
        if (!strcmp(modifierNames[i].name, lower))
            return modifierNames[i].modifier;
    return NULL;
}

static bool modifierKey(const struct state* s, const char* modifier, const char* name, uint32_t* code) {
    const xkb_mod_index_t index = xkb_keymap_mod_get_index(s->keymap, modifier);
    if (index >= 32 || !s->modifierKeys[index]) {
        fail("cannot_type", "no key for the modifier '%s' in the keymap", name);
        return false;
    }
    *code = s->modifierKeys[index];
    return true;
}

// Keysyms without a printable character, such as Return, F5 or XF86AudioMute. Names of printable keysyms, like a or
// section, stay text, so that <a> and <section> type themselves.
static bool isKeyKeysym(xkb_keysym_t sym) {
    const uint32_t cp = xkb_keysym_to_utf32(sym);
    return sym != XKB_KEY_NoSymbol && (cp < 0x20 || (cp >= 0x7F && cp < 0xA0));
}

// The key of a shortcut: a named key, one character or a keysym name. Keys that need modifiers on this keymap bring
// them along, so ctrl+@ also holds AltGr on a German layout.
static bool shortcutKey(const struct state* s, const char* name, struct chord* c) {
    uint32_t           cp;
    const xkb_keysym_t named = namedKeysym(name);
    if (named)
        return findChord(s, named, 0, c);
    if (decodeUtf8(name, &cp) == (int)strlen(name) && cp)
        return findChord(s, 0, cp, c);
    const xkb_keysym_t sym = xkb_keysym_from_name(name, XKB_KEYSYM_CASE_INSENSITIVE);
    return sym != XKB_KEY_NoSymbol && findChord(s, sym, 0, c);
}

static bool addShortcut(const struct state* s, struct steps* steps, char* spec) {
    struct step step = {0};
    char*       key  = strrchr(spec, '+');
    if (!key || key == spec || !key[1]) {
        fail("invalid_arguments", "a shortcut needs modifiers and a key, as in <shortcut:ctrl+l>");
        return false;
    }
    *key++ = '\0';
    for (char* name = strtok(spec, "+"); name; name = strtok(NULL, "+")) {
        const char* modifier = modifierName(name);
        uint32_t    code;
        if (!modifier) {
            fail("invalid_arguments", "unknown shortcut modifier '%s'", name);
            return false;
        }
        if (!modifierKey(s, modifier, name, &code))
            return false;
        addMod(&step.chord, code);
    }
    struct chord keyChord = {0};
    if (!shortcutKey(s, key, &keyChord)) {
        fail("cannot_type", "no key for '%s' in the keymap", key);
        return false;
    }
    for (int i = 0; i < keyChord.modCount; ++i)
        if (!addMod(&step.chord, keyChord.mods[i])) {
            fail("invalid_arguments", "too many modifiers in <shortcut:%s>", spec);
            return false;
        }
    step.chord.code = keyChord.code;
    return addStep(steps, step);
}

// Parses the tag between < and >. Returns 1 for a control, 0 for text that is typed as it is, and -1 on errors.
static int addTag(const struct state* s, struct steps* steps, const char* tag, size_t len) {
    char raw[128], lower[128];
    while (len && isspace((unsigned char)*tag))
        ++tag, --len;
    while (len && isspace((unsigned char)tag[len - 1]))
        --len;
    if (len == 0 || len >= sizeof(raw))
        return 0;
    memcpy(raw, tag, len);
    raw[len] = '\0';
    for (size_t i = 0; i <= len; ++i)
        lower[i] = tolower((unsigned char)raw[i]);

    long ms;
    if (!strcmp(lower, "settle"))
        return addStep(steps, (struct step){.waitMs = SETTLE_MS}) ? 1 : -1;
    if (!strncmp(lower, "wait:", 5)) {
        if (!parseCount(lower + 5, 0, MAX_SLEEP_MS, &ms)) {
            fail("invalid_arguments", "<wait:MS> takes a number of milliseconds, got <%s>", raw);
            return -1;
        }
        return addStep(steps, (struct step){.waitMs = ms}) ? 1 : -1;
    }
    if (!strncmp(lower, "shortcut:", 9))
        return addShortcut(s, steps, lower + 9) ? 1 : -1;

    // A named key or a keysym, optionally with a repeat count.
    long  count = 1;
    char* colon = strchr(raw, ':');
    if (colon)
        *colon = lower[colon - raw] = '\0';
    struct step        step  = {0};
    const xkb_keysym_t named = namedKeysym(lower);
    const xkb_keysym_t sym   = named ? named : xkb_keysym_from_name(raw, XKB_KEYSYM_CASE_INSENSITIVE);
    if (!named && !isKeyKeysym(sym))
        return 0;
    if (!findChord(s, sym, 0, &step.chord)) {
        fail("cannot_type", "no key for <%s> in the keymap", raw);
        return -1;
    }
    if (colon && !parseCount(colon + 1, 1, MAX_REPEAT, &count)) {
        fail("invalid_arguments", "the repeat count in <%s:%s> must be 1 to %d", raw, colon + 1, MAX_REPEAT);
        return -1;
    }
    while (count--)
        if (!addStep(steps, step))
            return -1;
    return 1;
}

// Resolving a long text takes a while. A signal stops it, as it would stop sending.
static bool stopParsing(size_t characters) {
    if (characters % 64 || !signalled())
        return false;
    fail("cancelled", "stopped by a signal");
    return true;
}

// Turns the text of a type action into steps, so nothing is sent when a part of it is invalid.
static bool parseText(struct state* s, const char* text, struct steps* steps) {
    size_t characters = 0;
    for (const char* p = text; *p;) {
        uint32_t cp;
        if (stopParsing(characters++))
            return false;
        if (p[0] == '\\' && p[1] == '<') {
            if (!addChar(s, steps, '<'))
                return false;
            p += 2;
            continue;
        }
        if (p[0] == '<') {
            const char* close = strchr(p + 1, '>');
            const char* open  = strchr(p + 1, '<');
            if (close && (!open || close < open)) {
                const int parsed = addTag(s, steps, p + 1, close - p - 1);
                if (parsed < 0)
                    return false;
                if (parsed > 0) {
                    p = close + 1;
                    continue;
                }
            }
        }
        if (p[0] == '\r' && p[1] == '\n') {
            ++p;
            continue;
        }
        const int len = decodeUtf8(p, &cp);
        if (!len) {
            fail("invalid_arguments", "the text is not valid UTF-8");
            return false;
        }
        if (!addChar(s, steps, cp))
            return false;
        p += len;
    }
    return true;
}

// The text of a text action, every character as it is. Line breaks press Enter.
static bool parseLiteral(struct state* s, const char* text, struct steps* steps) {
    size_t characters = 0;
    for (const char* p = text; *p;) {
        uint32_t cp;
        if (stopParsing(characters++))
            return false;
        if (p[0] == '\r' && p[1] == '\n') {
            ++p;
            continue;
        }
        const int len = decodeUtf8(p, &cp);
        if (!len) {
            fail("invalid_arguments", "the text is not valid UTF-8");
            return false;
        }
        if (!addChar(s, steps, cp))
            return false;
        p += len;
    }
    return true;
}

// A key name of keys and MODS: a modifier, an alias, one character or an XKB keysym name, ignoring case. A letter
// stands for its key, so A is the A key without Shift.
struct keyName {
    const char*  modifier;
    xkb_keysym_t sym;
    uint32_t     codepoint;
};

static bool parseKeyName(const char* name, struct keyName* k) {
    char         lower[64];
    const size_t len = strlen(name);
    uint32_t     cp;
    *k = (struct keyName){0};
    if (len == 0 || len >= sizeof(lower))
        return false;
    for (size_t i = 0; i <= len; ++i)
        lower[i] = tolower((unsigned char)name[i]);
    if ((k->modifier = modifierName(lower)))
        return true;
    if ((k->sym = namedKeysym(lower)) || (!strcmp(lower, "del") && (k->sym = XKB_KEY_Delete)))
        return true;
    if (decodeUtf8(name, &cp) == (int)len && cp) {
        const uint32_t folded = xkb_keysym_to_utf32(xkb_keysym_to_lower(xkb_utf32_to_keysym(cp)));
        k->codepoint          = folded ? folded : cp;
        return true;
    }
    k->sym = xkb_keysym_from_name(name, XKB_KEYSYM_CASE_INSENSITIVE);
    return k->sym != XKB_KEY_NoSymbol;
}

// Splits a chord such as ctrl+shift+t into its names. Empty names, as in ctrl++, are errors: the plus key is plus.
static int splitChord(char* spec, char** names, int max) {
    for (int count = 0;;) {
        char* plus = strchr(spec, '+');
        if (plus)
            *plus = '\0';
        if (!*spec || count == max)
            return -1;
        names[count++] = spec;
        if (!plus)
            return count;
        spec = plus + 1;
    }
}

// Checks the names of a chord, before the keymap is known.
static bool checkChord(const char* spec, bool modifiersOnly) {
    char  buffer[256];
    char* names[MAX_CHORD_MODS + 1];
    int   count = strlen(spec) < sizeof(buffer) ? splitChord(strcpy(buffer, spec), names, MAX_CHORD_MODS + 1) : -1;
    if (count < 0) {
        fail("invalid_arguments", "invalid chord '%s': join up to %d key names with +, and name the plus key plus", spec, MAX_CHORD_MODS + 1);
        return false;
    }
    for (int i = 0; i < count; ++i) {
        struct keyName k;
        if (!parseKeyName(names[i], &k)) {
            fail("invalid_arguments", "unknown key name '%s'", names[i]);
            return false;
        }
        if (modifiersOnly && !k.modifier) {
            fail("invalid_arguments", "'%s' is not a modifier: use ctrl, shift, alt or super", names[i]);
            return false;
        }
    }
    return true;
}

// The keys of a chord in the order they go down. A key that needs modifiers on this keymap brings them along, so
// ctrl+@ also holds AltGr on a German layout. With modifiersOnly, all of them are modifiers and the chord has no key.
static bool resolveChord(struct state* s, const char* spec, bool modifiersOnly, struct chord* c) {
    char      buffer[256];
    char*     names[MAX_CHORD_MODS + 1];
    const int count = splitChord(strcpy(buffer, spec), names, MAX_CHORD_MODS + 1);
    *c              = (struct chord){0};
    for (int i = 0; i < count; ++i) {
        struct keyName k;
        struct chord   key = {0};
        parseKeyName(names[i], &k);
        if (k.modifier && !modifierKey(s, k.modifier, names[i], &key.code))
            return false;
        if (!k.modifier && !findChord(s, k.sym, k.codepoint, &key)) {
            fail("cannot_type", "no key for '%s' in the keymap", names[i]);
            return false;
        }
        bool ok = true;
        for (int j = 0; j < key.modCount; ++j)
            ok = ok && addMod(c, key.mods[j]);
        if (modifiersOnly || i < count - 1)
            ok = ok && addMod(c, key.code);
        else
            c->code = key.code;
        if (!ok) {
            fail("invalid_arguments", "the chord '%s' needs more than %d keys", spec, MAX_CHORD_MODS + 1);
            return false;
        }
    }
    // A modifier named last, as in ctrl+shift, is the key itself.
    for (int i = 0; i < c->modCount; ++i)
        if (c->mods[i] == c->code) {
            memmove(c->mods + i, c->mods + i + 1, (c->modCount - i - 1) * sizeof(*c->mods));
            --c->modCount;
            break;
        }
    return true;
}

static int findActionType(const char* word) {
    for (size_t i = 0; i < ACTION_TYPES; ++i)
        if (!strcmp(actionSyntax[i].name, word))
            return i;
    return -1;
}

// Optional arguments end at the next action.
static bool hasOptional(int argc, char** argv, int i) {
    return i < argc && findActionType(argv[i]) < 0;
}

static bool parseMods(struct action* a, const char* mods) {
    a->mods = strcmp(mods, "-") ? mods : NULL;
    return !a->mods || checkChord(a->mods, true);
}

// Parses the action at argv[0]; returns the number of arguments it takes, or 0 for an invalid action, which it reports.
static int parseAction(int argc, char** argv, struct action* a) {
    const int type = findActionType(argv[0]);
    if (type < 0) {
        fail("invalid_arguments", "invalid action at '%s'", argv[0]);
        return 0;
    }
    *a = (struct action){.type = type, .code = BTN_LEFT, .count = 1, .keysAt = -1};
    // The arguments each action requires, which are all checked below; optional ones are checked as they come.
    static const int required[ACTION_TYPES] = {[ACTION_MOVE] = 2, [ACTION_DOWN] = 1, [ACTION_UP] = 1,   [ACTION_DRAG] = 4, [ACTION_SCROLL] = 2, [ACTION_WHEEL] = 2,
                                               [ACTION_KEY] = 1,  [ACTION_KEYS] = 1, [ACTION_TYPE] = 1, [ACTION_TEXT] = 1, [ACTION_SLEEP] = 1,  [ACTION_SYNC] = 2};
    unsigned long    code;
    char*            end = NULL;
    int              i   = 1 + required[type];
    bool             ok  = argc >= i;
    switch (ok ? (int)a->type : -1) {
        case ACTION_MOVE:
        case ACTION_SCROLL: ok = parseDouble(argv[1], &a->x) && parseDouble(argv[2], &a->y); break;
        case ACTION_DRAG:
            ok = parseDouble(argv[1], &a->x) && parseDouble(argv[2], &a->y) && parseDouble(argv[3], &a->x2) && parseDouble(argv[4], &a->y2);
            if (ok && hasOptional(argc, argv, i))
                ok = parseButton(argv[i++], &a->code) && (!hasOptional(argc, argv, i) || parseMods(a, argv[i++]));
            break;
        case ACTION_CLICK:
            if (hasOptional(argc, argv, i))
                ok = parseButton(argv[i++], &a->code);
            if (ok && hasOptional(argc, argv, i))
                ok = parseCount(argv[i++], 1, 3, &a->count) && (!hasOptional(argc, argv, i) || parseMods(a, argv[i++]));
            break;
        case ACTION_DOWN:
        case ACTION_UP: ok = parseButton(argv[1], &a->code); break;
        case ACTION_WHEEL: ok = parseCount(argv[1], -MAX_NOTCHES, MAX_NOTCHES, &a->dx) && parseCount(argv[2], -MAX_NOTCHES, MAX_NOTCHES, &a->dy); break;
        case ACTION_KEY:
            code    = strtoul(argv[1], &end, 0);
            ok      = end != argv[1] && *end == '\0' && code != 0 && code <= KEY_MAX;
            a->code = code;
            break;
        case ACTION_KEYS:
            ok = checkChord(a->text = argv[1], false);
            if (ok && hasOptional(argc, argv, i))
                ok = parseCount(argv[i++], 1, MAX_REPEAT, &a->count);
            break;
        case ACTION_TYPE: a->text = argv[1]; break;
        case ACTION_TEXT:
            if (!(ok = validUtf8(a->text = argv[1])))
                fail("invalid_arguments", "the text is not valid UTF-8");
            break;
        case ACTION_SLEEP: ok = parseCount(argv[1], 0, MAX_SLEEP_MS, &a->count); break;
        case ACTION_SYNC: ok = parseCount(argv[1], 0, MAX_SLEEP_MS, &a->count) && parseCount(argv[2], a->count, MAX_SLEEP_MS, &a->limit); break;
        default: break;
    }
    if (ok)
        return i;
    if (!failure.code)
        fail("invalid_arguments", "invalid arguments for %s, expected %s", argv[0], actionSyntax[type].syntax);
    return 0;
}

static bool needsKeymap(const struct action* a) {
    return a->type == ACTION_KEYS || a->type == ACTION_TYPE || a->type == ACTION_TEXT || a->mods;
}

// Checks an action against the keymap and the pointer region, which the server sends after connecting.
static bool resolveAction(struct state* s, struct action* a) {
    if (needsKeymap(a) && !loadKeymap(s))
        return false;
    switch (a->type) {
        case ACTION_MOVE: return checkPoint(s, a->x, a->y);
        case ACTION_DRAG:
            if (!checkPoint(s, a->x, a->y) || !checkPoint(s, a->x2, a->y2))
                return false;
            break;
        case ACTION_KEYS: return resolveChord(s, a->text, false, &a->chord);
        case ACTION_TYPE: return parseText(s, a->text, &a->steps);
        case ACTION_TEXT: return parseLiteral(s, a->text, &a->steps);
        default: break;
    }
    return !a->mods || resolveChord(s, a->mods, true, &a->chord);
}

static bool click(struct state* s, const struct action* a) {
    if (!pressMods(s, &a->chord, CHORD_MOD_MS))
        return false;
    for (long i = 0; i < a->count; ++i) {
        if ((i && !paceWait(s, CLICK_GAP_MS)) || !sendButton(s, a->code, true))
            return false;
        s->releasing = i + 1 == a->count;
        if (!paceWait(s, CLICK_HOLD_MS) || !sendButton(s, a->code, false))
            return false;
    }
    return releaseMods(s, &a->chord);
}

// Steps of at most DRAG_STEP_PX, at least DRAG_MIN_STEPS and at most MAX_DRAG_STEPS of them.
static long dragSteps(const struct action* a) {
    const double dx = a->x2 - a->x, dy = a->y2 - a->y;
    const double steps = ceil(hypot(dx, dy) / DRAG_STEP_PX);
    return steps < DRAG_MIN_STEPS ? DRAG_MIN_STEPS : steps > MAX_DRAG_STEPS ? MAX_DRAG_STEPS : (long)steps;
}

static bool drag(struct state* s, const struct action* a) {
    if (!sendMotion(s, a->x, a->y, true) || !pressMods(s, &a->chord, CHORD_MOD_MS) || !sendButton(s, a->code, true) || !paceWait(s, DRAG_HOLD_MS))
        return false;
    const double dx = a->x2 - a->x, dy = a->y2 - a->y;
    const long   steps = dragSteps(a);
    for (long i = 1; i <= steps; ++i) {
        const bool last = i == steps;
        if ((i > 1 && !paceWait(s, DRAG_STEP_MS)) || !sendMotion(s, last ? a->x2 : a->x + dx * i / steps, last ? a->y2 : a->y + dy * i / steps, last))
            return false;
    }
    s->releasing = true;
    return paceWait(s, DRAG_HOLD_MS) && sendButton(s, a->code, false) && releaseMods(s, &a->chord);
}

static bool scroll(struct state* s, const struct action* a) {
    if (!usePointer(s))
        return false;
    ei_device_scroll_delta(s->pointer, a->x, a->y);
    frame(s, s->pointer);
    if (!usePointer(s))
        return false;
    ei_device_scroll_stop(s->pointer, a->x != 0, a->y != 0);
    frame(s, s->pointer);
    return !interrupted(s);
}

// One notch per frame, as a wheel sends them.
static bool wheel(struct state* s, const struct action* a) {
    const long notches[2] = {labs(a->dx), labs(a->dy)};
    bool       sent       = false;
    for (long i = 0; i < notches[0] || i < notches[1]; ++i)
        for (int axis = 0; axis < 2; ++axis) {
            if (i >= notches[axis])
                continue;
            if ((sent && !paceWait(s, WHEEL_GAP_MS)) || !usePointer(s))
                return false;
            const int32_t value = (axis ? a->dy : a->dx) < 0 ? -120 : 120;
            ei_device_scroll_discrete(s->pointer, axis ? 0 : value, axis ? value : 0);
            frame(s, s->pointer);
            sent = true;
            if (interrupted(s))
                return false;
        }
    // XWayland queues scroll motion but handles wl_pointer.leave immediately. When the sender exits, hyprmasq
    // leaves the pointer, which can route the last queued notch to the X root window instead of the target. Give
    // that notch the same processing interval as the others before the sender can stop emulating.
    return !sent || paceWait(s, WHEEL_GAP_MS);
}

// The answers to syncs on standard input: lines with the number that a sync printed.
static struct {
    long   last;   // the highest number answered, or -1
    bool   closed; // standard input ended, so a sync waits for nothing but its MIN
    char   line[32];
    size_t length;
} answers = {.last = -1};

// Reads what standard input has, which poll found ready.
static void readAnswers(void) {
    char          buffer[256];
    const ssize_t n = read(STDIN_FILENO, buffer, sizeof(buffer));
    if (n < 0 && (errno == EINTR || errno == EAGAIN))
        return;
    if (n <= 0) {
        answers.closed = true;
        return;
    }
    for (ssize_t i = 0; i < n; ++i) {
        if (buffer[i] != '\n') {
            if (answers.length + 1 < sizeof(answers.line))
                answers.line[answers.length++] = buffer[i];
            continue;
        }
        answers.line[answers.length] = '\0';
        char*      end;
        const long number = strtol(answers.line, &end, 10);
        if (end != answers.line && number > answers.last)
            answers.last = number;
        answers.length = 0;
    }
}

// Tells the caller that the server has processed the actions before, as the round trip after each of them confirmed,
// and waits for its answer, at least MIN and at most MAX milliseconds, while handling server events. hyprmasq-mcp waits
// meanwhile for the window to answer a click. An answer to an earlier sync, which came after that one had ended, does
// not count.
static bool awaitAnswer(struct state* s, const struct action* a) {
    const uint64_t start = nowNs(), earliest = start + MS(a->count), latest = start + MS(a->limit);
    const long     number = s->sent.actions;
    printf("{\"sync\":%ld}\n", number);
    fflush(stdout);
    struct pollfd fds[2] = {{.fd = ei_get_fd(s->ei), .events = POLLIN}, {.fd = STDIN_FILENO, .events = POLLIN}};
    for (;;) {
        ei_dispatch(s->ei);
        for (struct ei_event* event; (event = ei_get_event(s->ei));) {
            handleEvent(s, event);
            ei_event_unref(event);
        }
        if (interrupted(s))
            return false;
        const bool     answered = answers.closed || answers.last >= number;
        const uint64_t now = nowNs(), until = answered ? earliest : latest;
        if (now >= until)
            return true;
        fds[1].fd               = answered ? -1 : STDIN_FILENO;
        const uint64_t  left    = until - now;
        struct timespec timeout = {.tv_sec = left / 1000000000u, .tv_nsec = left % 1000000000u};
        if (ppoll(fds, 2, &timeout, &waitMask) < 0 && errno != EINTR)
            return false;
        if (fds[1].fd >= 0 && fds[1].revents)
            readAnswers();
    }
}

static bool typeSteps(struct state* s, const struct steps* steps) {
    // After the press of the last key, only its releases are left, until a wait after it.
    size_t lastKey = 0;
    for (size_t i = 0; i < steps->count; ++i)
        if (steps->items[i].chord.code)
            lastKey = i;
    bool typed = false;
    for (size_t i = 0; i < steps->count; ++i) {
        const struct step* step = &steps->items[i];
        if (!step->chord.code) {
            s->releasing = false;
            if (!waitUntil(s, nowNs() + MS(step->waitMs)))
                return false;
            continue;
        }
        if ((typed && !paceWait(s, TYPE_GAP_MS)) || !pressChord(s, &step->chord, 0, TYPE_HOLD_MS, i == lastKey))
            return false;
        typed = true;
    }
    return true;
}

// Whether a keys action names one key, without modifiers, which it presses as typing would: held TYPE_HOLD_MS and
// TYPE_GAP_MS apart, with any modifier that the keymap needs for it, such as Shift for @, at once before it. Apps took
// a hundred arrow presses at this pace one step each, in Qt, GTK, Tk and Chromium. Chords with modifiers keep their
// pace, which gives a shortcut's modifiers time to take effect.
static bool typedKeys(const struct action* a) {
    return a->type == ACTION_KEYS && !strchr(a->text, '+');
}

// The least time that the resolved actions take at their pace, in milliseconds, without the round trips: the delays of
// the pacing, of which only sleeps and waits remain with --no-pace. type and text count every step they resolved to, so
// a character typed through Compose counts a key for each keysym of its sequence.
static long pacedTime(const struct state* s, const struct action* actions, int total) {
    const long pace = s->pace ? 1 : 0;
    long       ms   = 0;
    for (int i = 0; i < total; ++i) {
        const struct action* a    = &actions[i];
        const long           mods = a->chord.modCount ? CHORD_MOD_MS : 0;
        long                 keys = 0, notches;
        // A sync takes the place of the gap after the action before it, and lasts at least MIN.
        if (a->type == ACTION_SYNC) {
            ms += a->count;
            continue;
        }
        long gap = i ? pace * ACTION_GAP_MS : 0;
        if (i && actions[i - 1].type == ACTION_SYNC)
            gap = gap > actions[i - 1].count ? gap - actions[i - 1].count : 0;
        ms += gap;
        switch (a->type) {
            case ACTION_CLICK: ms += pace * (mods + a->count * CLICK_HOLD_MS + (a->count - 1) * CLICK_GAP_MS); break;
            // The modifiers go down while the press waits for the motion.
            case ACTION_DRAG: ms += pace * (MOTION_PRESS_MS + 2 * DRAG_HOLD_MS + (dragSteps(a) - 1) * DRAG_STEP_MS); break;
            case ACTION_WHEEL:
                notches = labs(a->dx) + labs(a->dy);
                ms += notches ? pace * notches * WHEEL_GAP_MS : 0;
                break;
            case ACTION_KEY: ms += pace * CHORD_HOLD_MS; break;
            case ACTION_KEYS:
                if (typedKeys(a))
                    ms += pace * (a->count * TYPE_HOLD_MS + (a->count - 1) * TYPE_GAP_MS);
                else
                    ms += pace * (a->count * (mods + CHORD_HOLD_MS) + (a->count - 1) * CHORD_GAP_MS);
                break;
            case ACTION_TYPE:
            case ACTION_TEXT:
                for (size_t j = 0; j < a->steps.count; ++j) {
                    if (a->steps.items[j].chord.code)
                        ++keys;
                    else
                        ms += a->steps.items[j].waitMs;
                }
                ms += keys ? pace * (keys * TYPE_HOLD_MS + (keys - 1) * TYPE_GAP_MS) : 0;
                break;
            case ACTION_SLEEP: ms += a->count; break;
            default: break;
        }
    }
    return ms;
}

static bool runAction(struct state* s, const struct action* a) {
    switch (a->type) {
        case ACTION_MOVE: return sendMotion(s, a->x, a->y, true);
        case ACTION_CLICK: return click(s, a);
        case ACTION_DOWN:
        case ACTION_UP: return sendButton(s, a->code, a->type == ACTION_DOWN);
        case ACTION_DRAG: return drag(s, a);
        case ACTION_SCROLL: return scroll(s, a);
        case ACTION_WHEEL: return wheel(s, a);
        case ACTION_KEY: return pressChord(s, &(struct chord){.code = a->code}, 0, CHORD_HOLD_MS, true);
        case ACTION_KEYS:
            for (long i = 0; i < a->count; ++i) {
                const bool typed = typedKeys(a);
                if ((i && !paceWait(s, typed ? TYPE_GAP_MS : CHORD_GAP_MS)) ||
                    !pressChord(s, &a->chord, typed ? 0 : CHORD_MOD_MS, typed ? TYPE_HOLD_MS : CHORD_HOLD_MS, i + 1 == a->count))
                    return false;
            }
            return true;
        case ACTION_TYPE:
        case ACTION_TEXT: return typeSteps(s, &a->steps);
        case ACTION_SLEEP: return waitUntil(s, nowNs() + MS(a->count));
        case ACTION_SYNC: return awaitAnswer(s, a);
    }
    return false;
}

// Messages contain arguments, which need not be valid UTF-8.
static void printJsonString(const char* text) {
    putchar('"');
    for (const char* p = text; *p;) {
        uint32_t  cp;
        const int len = decodeUtf8(p, &cp);
        if (!len) {
            fputs("\\ufffd", stdout);
            ++p;
            continue;
        }
        if (cp == '"' || cp == '\\')
            printf("\\%c", (char)cp);
        else if (cp < 0x20)
            printf("\\u%04x", cp);
        else
            fwrite(p, 1, len, stdout);
        p += len;
    }
    putchar('"');
}

static void printOutcome(int done, int total, int failedIndex, const char* delivery, long keys) {
    printf("{\"ok\":%s,\"done\":%d,\"total\":%d,\"failedIndex\":", failure.code ? "false" : "true", done, total);
    failedIndex < 0 ? printf("null") : printf("%d", failedIndex);
    printf(",\"delivery\":\"%s\",\"progress\":", delivery);
    keys < 0 ? printf("null") : printf("{\"keys\":%ld}", keys);
    if (budgetMs >= 0) {
        printf(",\"paceMs\":");
        pacedMs < 0 ? printf("null") : printf("%ld", pacedMs);
    }
    printf(",\"compose\":");
    if (composed) {
        printf("{\"locale\":");
        printJsonString(composeLocale);
        printf(",\"requested\":");
        printJsonString(composeRequested);
        putchar('}');
    } else
        printf("null");
    if (failure.code) {
        printf(",\"error\":{\"code\":\"%s\",\"message\":", failure.code);
        printJsonString(failure.message);
        printf("}}\n");
    } else
        printf(",\"error\":null}\n");
    fflush(stdout);
}

int main(int argc, char** argv) {
    if (argc == 2 && !strcmp(argv[1], "--version")) {
        puts("hyprmasq-input " HYPRMASQ_VERSION);
        return 0;
    }
    // The budget counts from here.
    const uint64_t started = nowNs();
    const char*    socket  = NULL;
    bool           json = false, pace = true;
    const char*    badOption    = NULL;
    const char*    badBudget    = NULL;
    const char*    missingValue = NULL;
    int            first        = 1;
    for (; first < argc && argv[first][0] == '-'; ++first) {
        if (!strcmp(argv[first], "-h") || !strcmp(argv[first], "--help")) {
            help();
            return 0;
        }
        if ((!strcmp(argv[first], "--socket") || !strcmp(argv[first], "--budget") || !strcmp(argv[first], "--locale")) && first + 1 == argc) {
            missingValue = argv[first];
            break;
        }
        if (!strcmp(argv[first], "--socket") && first + 1 < argc)
            socket = argv[++first];
        else if (!strcmp(argv[first], "--no-pace"))
            pace = false;
        else if (!strcmp(argv[first], "--json"))
            json = true;
        else if (!strcmp(argv[first], "--budget") && first + 1 < argc) {
            if (!parseCount(argv[++first], 0, MAX_SLEEP_MS, &budgetMs) && !badBudget)
                badBudget = argv[first];
        } else if (!strcmp(argv[first], "--locale") && first + 1 < argc) {
            if (!*(composeRequested = argv[++first]) && !badOption)
                badOption = "--locale ''";
        } else if (!badOption)
            badOption = argv[first];
    }
    if (badBudget) {
        budgetMs = -1;
        fail("invalid_arguments", "--budget takes 0 to %d milliseconds, not '%s'", MAX_SLEEP_MS, badBudget);
        usage(stderr);
        if (json)
            printOutcome(0, 0, -1, "none", -1);
        return 2;
    }
    if (badOption || missingValue || !socket || first == argc) {
        if (badOption)
            fail("invalid_arguments", "invalid option '%s'", badOption);
        else if (missingValue)
            fail("invalid_arguments", "%s needs a value", missingValue);
        else
            fail("invalid_arguments", !socket ? "--socket PATH is required" : "at least one action is required");
        usage(stderr);
        if (json)
            printOutcome(0, 0, -1, "none", -1);
        return 2;
    }

    // Every action is parsed before connecting, so invalid ones fail without a server.
    struct action* actions = calloc(argc - first + 1, sizeof(*actions));
    int            total   = 0;
    for (int i = first; actions && i < argc; ++total) {
        const int used = parseAction(argc - i, argv + i, &actions[total]);
        if (!used) {
            usage(stderr);
            if (json)
                printOutcome(0, total + 1, total, "none", -1);
            free(actions);
            return 2;
        }
        i += used;
    }
    if (!actions) {
        fail("out_of_memory", "out of memory");
        if (json)
            printOutcome(0, 0, -1, "none", -1);
        return 1;
    }

    sigset_t blocked;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGINT);
    sigaddset(&blocked, SIGTERM);
    sigprocmask(SIG_BLOCK, &blocked, &waitMask);
    sigdelset(&waitMask, SIGINT);
    sigdelset(&waitMask, SIGTERM);
    struct sigaction action = {.sa_handler = onSignal};
    sigemptyset(&action.sa_mask);
    sigaction(SIGINT, &action, NULL);
    sigaction(SIGTERM, &action, NULL);

    struct state s = {.pace = pace};
    // The index of the action that failed before anything was sent, or -1.
    int  invalidIndex = -1;
    bool running      = false;
    s.ei              = ei_new_sender(&s);
    if (!s.ei) {
        fail("connect_failed", "cannot create a libei context");
        if (json)
            printOutcome(0, total, -1, "none", -1);
        free(actions);
        return 1;
    }
    ei_configure_name(s.ei, "hyprmasq-input");

    const int rc = ei_setup_backend_socket(s.ei, socket);
    if (rc != 0)
        fail("connect_failed", "cannot connect to %s: %s", socket, strerror(-rc));
    else if (!pump(&s, nowNs() + MS(TIMEOUT_MS), devicesReady)) {
        if (s.pointer && s.keyboard && !s.disconnected)
            fail("input_paused", "the server paused input before anything was sent");
        else
            fail("no_devices", "the server did not offer a pointer and a keyboard");
    } else if (cancelled)
        fail("cancelled", "stopped by a signal");
    else {
        // Nothing is sent before every action has been checked.
        for (int i = 0; i < total && invalidIndex < 0; ++i)
            if (!resolveAction(&s, &actions[i]))
                invalidIndex = i;
        running = invalidIndex < 0;
        // Actions that the budget cannot fit would be stopped part-way, so none of them is sent.
        if (running && budgetMs >= 0) {
            pacedMs         = pacedTime(&s, actions, total);
            const long left = budgetMs - (long)((nowNs() - started) / 1000000u);
            if (pacedMs > left) {
                fail("too_long", "the actions take at least %ld ms at their pace, more than the %ld ms left of the budget", pacedMs, left > 0 ? left : 0);
                running = false;
            }
        }
    }

    uint64_t lastEnd = 0;
    for (int i = 0; running && i < total; ++i) {
        // The gap counts from the end of the action before, also across a sync, which takes its place.
        if (i && s.pace && actions[i].type != ACTION_SYNC && !waitUntil(&s, lastEnd + MS(ACTION_GAP_MS)))
            break;
        actions[i].keysAt = s.sent.keys;
        if (!runAction(&s, &actions[i]))
            break;
        // The last action takes along whatever is still pressed, so the sender leaves nothing held.
        if (i == total - 1)
            releaseAll(&s);
        s.sent.actions = i + 1;
        s.releasing    = false;
        if (actions[i].type != ACTION_SYNC)
            lastEnd = nowNs();
        if (!roundTrip(&s) || interrupted(&s))
            break;
    }
    // Once every frame sent is confirmed, so are the actions that ran to their end, also when the connection ended
    // before the round trip after the last of them.
    if (s.confirmed.frames == s.sent.frames)
        s.confirmed.actions = s.sent.actions;

    int         done = 0, failedIndex = invalidIndex;
    const char* delivery = "none";
    long        keys     = -1;
    if (running && s.confirmed.actions < total) {
        releaseAll(&s);
        // Devices removed with input in flight may also mean that the connection is ending, which a round trip shows.
        if (s.sent.frames != s.confirmed.frames || (s.lost && !s.disconnected))
            roundTrip(&s);
        // When the server closes the connection after confirming every frame sent, as hyprmasq does when the input closed
        // the window, an action with only the releases of what it holds left to send counts as done: hyprmasq releases
        // what a sender holds when its connection ends.
        if (!failure.code && !cancelled && !s.paused && s.disconnected && s.releasing && s.confirmed.frames == s.sent.frames)
            s.confirmed.actions = s.sent.actions + 1;
    }
    if (running && s.confirmed.actions < total) {
        if (failure.code)
            ; // reported where it happened
        else if (cancelled)
            fail("cancelled", "stopped by a signal");
        else if (s.paused)
            // hyprmasq pauses an X11 window's devices while another window of its X server has the focus, and a window's
            // devices while the user holds a button or key in a window of the same Wayland connection for over a second.
            fail("input_paused", "the server paused input, so some events were dropped");
        else if (s.lost && !s.disconnected && !s.stalled)
            fail("unconfirmed", "the plugin replaced a device, so some events were lost");
        else if (s.disconnected)
            // hyprmasq closes the socket when its window closes, which the input itself may have caused.
            fail("unconfirmed", "the server closed the connection, as hyprmasq does when the window closes or another sender connects");
        else
            fail("unconfirmed", "the server did not confirm the events");
    }
    if (running) {
        done        = s.confirmed.actions;
        failedIndex = failure.code ? done : -1;
        if (!failure.code)
            delivery = "complete";
        // After a pause, the plugin dropped every frame it had not confirmed, so with none confirmed nothing arrived.
        else if (s.sent.frames == 0 || (s.paused && !s.lost && !s.disconnected && s.confirmed.frames == 0))
            delivery = "none";
        // Without an answer to the last ping, the plugin may still have processed what the sender had sent before it.
        else if ((s.disconnected || s.stalled) && s.sent.frames != s.taken.frames)
            delivery = "uncertain";
        else
            delivery = "partial";
        // The keys of the failed action that the plugin processed, or for an uncertain delivery, the keys sent.
        const struct action* a = failure.code && failedIndex < total ? &actions[failedIndex] : NULL;
        if (a && (a->type == ACTION_TYPE || a->type == ACTION_TEXT || a->type == ACTION_KEYS)) {
            const long start = a->keysAt < 0 ? s.sent.keys : a->keysAt;
            const long end   = failedIndex + 1 < total && a[1].keysAt >= 0 ? a[1].keysAt : s.sent.keys;
            const long known = strcmp(delivery, "uncertain") ? s.confirmed.keys : end;
            keys             = known < start ? 0 : (known > end ? end : known) - start;
        }
    }
    if (failure.code && !strcmp(failure.code, "invalid_arguments") && s.sent.frames == 0)
        usage(stderr);
    if (json)
        printOutcome(done, total, failedIndex, delivery, keys);

    int status = 0;
    if (failure.code)
        status = s.sent.frames == 0 && (!strcmp(failure.code, "invalid_arguments") || !strcmp(failure.code, "cannot_type") || !strcmp(failure.code, "too_long")) ? 2 : 1;

    if (s.pointer) {
        if (s.pointerReady)
            ei_device_stop_emulating(s.pointer);
        ei_device_unref(s.pointer);
    }
    if (s.keyboard) {
        if (s.keyboardReady)
            ei_device_stop_emulating(s.keyboard);
        ei_device_unref(s.keyboard);
    }
    for (int i = 0; i < total; ++i)
        free(actions[i].steps.items);
    free(actions);
    if (s.compose)
        xkb_compose_table_unref(s.compose);
    if (s.keymap)
        xkb_keymap_unref(s.keymap);
    if (s.xkb)
        xkb_context_unref(s.xkb);
    ei_unref(s.ei);
    return status;
}
