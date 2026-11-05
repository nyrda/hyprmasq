// Container test fixture: the keyboard and pointer of the parent compositor's seat, which Hyprland nested in it gets
// as its own, and the user's keys on that keyboard, as QEMU's sendkey gives them to the VM.
//
//   hyprmasq-parent-seat FIFO
//
// labwc's headless seat has no keyboard and no pointer, and Hyprland's Wayland backend then gives Hyprland neither,
// where QEMU gives the VM both. It creates a zwp_virtual_keyboard_v1 with the us keymap of the test config and a
// zwlr_virtual_pointer_v1 on the compositor that WAYLAND_DISPLAY names, prints "ready" once the compositor has both,
// and holds them until it is killed.
//
// It reads lines "sendkey HOLD_MS CODE..." from FIFO, with Linux key codes, and sends them as QEMU's sendkey does
// (ui/input.c, qmp_send_key): it presses the keys in order and releases them in reverse order, and pauses HOLD_MS after
// each of these events, or 10 ms with 0. All lines share one queue, so a line waits for the pauses of the lines before
// it, and the writer does not wait for its keys. As a virtual keyboard must, it sends the modifiers that its own xkb
// state of the keymap gives after each key. It logs "key CODE 1" and "key CODE 0" with each press and release.
#define _GNU_SOURCE
#include <wayland-client.h>
#include <xkbcommon/xkbcommon.h>
#include "virtual-keyboard-unstable-v1-client-protocol.h"
#include "wlr-virtual-pointer-unstable-v1-client-protocol.h"
#include "probe-common.h"
#include <fcntl.h>
#include <linux/input-event-codes.h>

#define DEFAULT_PAUSE_MS 10
#define QUEUE_SIZE       4096
#define MAX_KEYS         8

// A press or release of code, or with code 0 a pause of ms.
struct step {
    uint32_t code;
    bool     pressed;
    uint32_t ms;
};

static struct step                     queue[QUEUE_SIZE];
static size_t                          head, tail; // queue[head..tail) waits, modulo QUEUE_SIZE
static bool                            pausing;    // the timer runs for the pause at the head
static int                             timer;
static struct zwp_virtual_keyboard_v1* keyboard;
static struct xkb_state*               state;

static void push(struct step step) {
    if ((tail + 1) % QUEUE_SIZE == head) {
        fprintf(stderr, "%s: more than %d steps wait\n", program_invocation_short_name, QUEUE_SIZE - 1);
        exit(1);
    }
    queue[tail] = step;
    tail        = (tail + 1) % QUEUE_SIZE;
}

static void send_key(uint32_t code, bool pressed) {
    zwp_virtual_keyboard_v1_key(keyboard, (uint32_t)now_ms(), code, pressed ? WL_KEYBOARD_KEY_STATE_PRESSED : WL_KEYBOARD_KEY_STATE_RELEASED);
    // xkb codes are evdev codes + 8.
    xkb_state_update_key(state, code + 8, pressed ? XKB_KEY_DOWN : XKB_KEY_UP);
    zwp_virtual_keyboard_v1_modifiers(keyboard, xkb_state_serialize_mods(state, XKB_STATE_MODS_DEPRESSED), xkb_state_serialize_mods(state, XKB_STATE_MODS_LATCHED),
                                      xkb_state_serialize_mods(state, XKB_STATE_MODS_LOCKED), xkb_state_serialize_layout(state, XKB_STATE_LAYOUT_EFFECTIVE));
    say("key %u %d", code, pressed);
}

// Sends the steps at the head of the queue up to the next pause, and starts the timer for that pause.
static void run_queue(void) {
    while (!pausing && head != tail) {
        const struct step step = queue[head];
        if (step.code) {
            send_key(step.code, step.pressed);
            head = (head + 1) % QUEUE_SIZE;
        } else {
            timer_arm_ms(timer, step.ms);
            pausing = true;
        }
    }
}

static void pause_ended(void) {
    timer_read(timer);
    pausing = false;
    head    = (head + 1) % QUEUE_SIZE;
    run_queue();
}

// Queues a line "sendkey HOLD_MS CODE...".
static void queue_line(char* line) {
    char* words[MAX_KEYS + 2];
    int   count = 0;
    for (char* word = strtok(line, " \t"); word; word = strtok(NULL, " \t")) {
        if (count == MAX_KEYS + 2) {
            fprintf(stderr, "%s: more than %d keys in a line\n", program_invocation_short_name, MAX_KEYS);
            exit(2);
        }
        words[count++] = word;
    }
    if (count < 3 || strcmp(words[0], "sendkey")) {
        fprintf(stderr, "%s: expected \"sendkey HOLD_MS CODE...\"\n", program_invocation_short_name);
        exit(2);
    }
    const int ms    = parse_int(words[1], "HOLD_MS", 0, 60000);
    const int pause = ms ? ms : DEFAULT_PAUSE_MS;
    int       codes[MAX_KEYS];
    for (int i = 2; i < count; ++i)
        codes[i - 2] = parse_int(words[i], "CODE", 1, KEY_MAX);
    for (int i = 0; i < count - 2; ++i) {
        push((struct step){.code = (uint32_t)codes[i], .pressed = true});
        push((struct step){.ms = (uint32_t)pause});
    }
    for (int i = count - 3; i >= 0; --i) {
        push((struct step){.code = (uint32_t)codes[i], .pressed = false});
        push((struct step){.ms = (uint32_t)pause});
    }
}

// Queues the complete lines that the FIFO has, and keeps a partial line for the next read.
static void read_fifo(int fifo) {
    static char   buffer[4096];
    static size_t used;
    const ssize_t got = read(fifo, buffer + used, sizeof(buffer) - 1 - used);
    if (got < 0 && errno == EAGAIN)
        return;
    CHECK(got > 0);
    used += (size_t)got;
    buffer[used] = '\0';
    char* start  = buffer;
    for (char* end; (end = strchr(start, '\n')); start = end + 1) {
        *end = '\0';
        if (*start)
            queue_line(start);
    }
    used -= (size_t)(start - buffer);
    memmove(buffer, start, used);
    if (used == sizeof(buffer) - 1) {
        fprintf(stderr, "%s: a line longer than %zu bytes\n", program_invocation_short_name, sizeof(buffer) - 2);
        exit(2);
    }
    run_queue();
}

int main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s FIFO\n", argv[0]);
        return 2;
    }
    // Opened for writing too, so that the FIFO never reads end of file between writers.
    const int fifo = open(argv[1], O_RDWR | O_NONBLOCK | O_CLOEXEC);
    CHECK(fifo >= 0);
    struct wl_display* display = wl_display_connect(NULL);
    CHECK(display);
    struct wl_seat*                         seat      = NULL;
    struct zwp_virtual_keyboard_manager_v1* keyboards = NULL;
    struct zwlr_virtual_pointer_manager_v1* pointers  = NULL;
    bind_globals(display,
                 (const struct probe_global[]){{.proxy = &seat, .interface = &wl_seat_interface, .version = 1},
                                               {.proxy = &keyboards, .interface = &zwp_virtual_keyboard_manager_v1_interface, .version = 1},
                                               {.proxy = &pointers, .interface = &zwlr_virtual_pointer_manager_v1_interface, .version = 1},
                                               {0}});

    // The keymap that the test config gives Hyprland, so that a key code means the same key on both seats.
    struct xkb_context* context = xkb_context_new(XKB_CONTEXT_NO_FLAGS);
    CHECK(context);
    struct xkb_keymap* keymap = xkb_keymap_new_from_names(context, &(struct xkb_rule_names){.layout = "us"}, XKB_KEYMAP_COMPILE_NO_FLAGS);
    CHECK(keymap);
    CHECK(state = xkb_state_new(keymap));
    char* text = xkb_keymap_get_as_string(keymap, XKB_KEYMAP_FORMAT_TEXT_V1);
    CHECK(text);
    const size_t size = strlen(text) + 1;
    const int    fd   = memfd_create("parent-seat-keymap", MFD_CLOEXEC);
    CHECK(fd >= 0);
    CHECK(write(fd, text, size) == (ssize_t)size);

    keyboard = zwp_virtual_keyboard_manager_v1_create_virtual_keyboard(keyboards, seat);
    zwp_virtual_keyboard_v1_keymap(keyboard, WL_KEYBOARD_KEYMAP_FORMAT_XKB_V1, fd, size);
    struct zwlr_virtual_pointer_v1* pointer = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(pointers, seat);
    // A frame, so that the compositor has seen the pointer as a device before the test goes on.
    zwlr_virtual_pointer_v1_frame(pointer);
    CHECK(wl_display_roundtrip(display) >= 0);
    timer = timer_new();
    say("ready");

    struct pollfd fds[3] = {[1] = {.fd = fifo, .events = POLLIN}, [2] = {.fd = timer, .events = POLLIN}};
    while (dispatch_display(display, fds, 3, -1)) {
        if (fds[1].revents & POLLIN)
            read_fifo(fifo);
        if (fds[2].revents & POLLIN)
            pause_ended();
    }
    fprintf(stderr, "%s: the connection to the compositor ended\n", program_invocation_short_name);
    return 1;
}
