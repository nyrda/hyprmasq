// hyprmasq-input: a minimal libei sender for the per-window sockets that hyprmasq creates.
//
//   hyprmasq-input --socket PATH ACTION...
//
// Actions run in order. Coordinates are window-local logical pixels.
//   move X Y           move the pointer to X,Y
//   click [BUTTON]     press and release BUTTON (left, right, middle or an evdev code; default left)
//   down BUTTON        press BUTTON
//   up BUTTON          release BUTTON
//   scroll DX DY       smooth scroll by DX,DY
//   key CODE           press and release an evdev key code (see linux/input-event-codes.h)
//   type TEXT          type printable ASCII using the US layout
//   sleep MS           wait MS milliseconds

#include <libei.h>
#include <linux/input-event-codes.h>

#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define TIMEOUT_MS 5000

struct state {
    struct ei*        ei;
    struct ei_device* pointer;
    struct ei_device* keyboard;
    bool              pointerReady;
    bool              keyboardReady;
    bool              pong;
    bool              disconnected;
    uint32_t          sequence;
};

static void usage(FILE* out) {
    fputs("usage: hyprmasq-input --socket PATH ACTION...\n"
          "actions: move X Y | click [BUTTON] | down BUTTON | up BUTTON | scroll DX DY | key CODE | type TEXT | sleep MS\n",
          out);
}

static void handleEvent(struct state* s, struct ei_event* event) {
    struct ei_device* device = ei_event_get_device(event);

    switch (ei_event_get_type(event)) {
        case EI_EVENT_SEAT_ADDED:
            ei_seat_bind_capabilities(ei_event_get_seat(event), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL,
                                      EI_DEVICE_CAP_KEYBOARD, NULL);
            break;
        case EI_EVENT_DEVICE_ADDED:
            if (!s->pointer && ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE))
                s->pointer = ei_device_ref(device);
            else if (!s->keyboard && ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD))
                s->keyboard = ei_device_ref(device);
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
            break;
        case EI_EVENT_PONG: s->pong = true; break;
        case EI_EVENT_DISCONNECT: s->disconnected = true; break;
        default: break;
    }
}

// Dispatches until done() is true, the server disconnects, or the timeout expires.
static bool pump(struct state* s, bool (*done)(const struct state*)) {
    struct pollfd pfd = {.fd = ei_get_fd(s->ei), .events = POLLIN};
    const int     step = 50;

    for (int waited = 0; !done(s) && waited < TIMEOUT_MS; waited += step) {
        if (s->disconnected)
            return false;
        if (poll(&pfd, 1, step) < 0 && errno != EINTR)
            return false;

        ei_dispatch(s->ei);
        for (struct ei_event* event; (event = ei_get_event(s->ei));) {
            handleEvent(s, event);
            ei_event_unref(event);
        }
    }

    return done(s) && !s->disconnected;
}

static bool devicesReady(const struct state* s) {
    return s->pointerReady && s->keyboardReady;
}

static bool gotPong(const struct state* s) {
    return s->pong;
}

static void frame(struct state* s, struct ei_device* device) {
    ei_device_frame(device, ei_now(s->ei));
}

static bool parseButton(const char* name, uint32_t* button) {
    if (!strcmp(name, "left"))
        *button = BTN_LEFT;
    else if (!strcmp(name, "right"))
        *button = BTN_RIGHT;
    else if (!strcmp(name, "middle"))
        *button = BTN_MIDDLE;
    else {
        char* end = NULL;
        *button   = strtoul(name, &end, 0);
        return end && *end == '\0' && *button > 0;
    }
    return true;
}

static bool parseDouble(const char* text, double* out) {
    char* end = NULL;
    *out      = strtod(text, &end);
    return end && *end == '\0';
}

struct keystroke {
    uint32_t code;
    bool     shift;
};

// Printable ASCII on a US layout.
static bool asciiKey(char c, struct keystroke* k) {
    static const uint32_t letters[] = {KEY_A, KEY_B, KEY_C, KEY_D, KEY_E, KEY_F, KEY_G, KEY_H, KEY_I, KEY_J, KEY_K, KEY_L, KEY_M,
                                       KEY_N, KEY_O, KEY_P, KEY_Q, KEY_R, KEY_S, KEY_T, KEY_U, KEY_V, KEY_W, KEY_X, KEY_Y, KEY_Z};
    static const char*    digits = "1234567890";
    static const char*    shiftedDigits = "!@#$%^&*()";
    static const uint32_t digitKeys[] = {KEY_1, KEY_2, KEY_3, KEY_4, KEY_5, KEY_6, KEY_7, KEY_8, KEY_9, KEY_0};
    static const struct {
        char     plain, shifted;
        uint32_t code;
    } symbols[] = {
        {'-', '_', KEY_MINUS},      {'=', '+', KEY_EQUAL},  {'[', '{', KEY_LEFTBRACE}, {']', '}', KEY_RIGHTBRACE}, {'\\', '|', KEY_BACKSLASH},
        {';', ':', KEY_SEMICOLON},  {'\'', '"', KEY_APOSTROPHE}, {'`', '~', KEY_GRAVE}, {',', '<', KEY_COMMA}, {'.', '>', KEY_DOT},
        {'/', '?', KEY_SLASH},
    };

    const char* at;
    if (c >= 'a' && c <= 'z') {
        *k = (struct keystroke){letters[c - 'a'], false};
    } else if (c >= 'A' && c <= 'Z') {
        *k = (struct keystroke){letters[c - 'A'], true};
    } else if ((at = strchr(digits, c)) && c) {
        *k = (struct keystroke){digitKeys[at - digits], false};
    } else if ((at = strchr(shiftedDigits, c)) && c) {
        *k = (struct keystroke){digitKeys[at - shiftedDigits], true};
    } else if (c == ' ') {
        *k = (struct keystroke){KEY_SPACE, false};
    } else if (c == '\n') {
        *k = (struct keystroke){KEY_ENTER, false};
    } else if (c == '\t') {
        *k = (struct keystroke){KEY_TAB, false};
    } else {
        for (size_t i = 0; i < sizeof(symbols) / sizeof(symbols[0]); ++i) {
            if (symbols[i].plain == c || symbols[i].shifted == c) {
                *k = (struct keystroke){symbols[i].code, symbols[i].shifted == c};
                return true;
            }
        }
        return false;
    }
    return true;
}

static void sleepMs(long ms) {
    struct timespec ts = {.tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000L};
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR)
        ;
}

static void tapKey(struct state* s, uint32_t code, bool shift) {
    if (shift) {
        ei_device_keyboard_key(s->keyboard, KEY_LEFTSHIFT, true);
        frame(s, s->keyboard);
    }
    ei_device_keyboard_key(s->keyboard, code, true);
    frame(s, s->keyboard);
    ei_device_keyboard_key(s->keyboard, code, false);
    frame(s, s->keyboard);
    if (shift) {
        ei_device_keyboard_key(s->keyboard, KEY_LEFTSHIFT, false);
        frame(s, s->keyboard);
    }
}

// Runs the action at argv[0]; returns the number of arguments consumed or -1 on error.
static int runAction(struct state* s, int argc, char** argv) {
    const char* name = argv[0];
    double      x, y;
    uint32_t    button;

    if (!strcmp(name, "move") && argc >= 3 && parseDouble(argv[1], &x) && parseDouble(argv[2], &y)) {
        ei_device_pointer_motion_absolute(s->pointer, x, y);
        frame(s, s->pointer);
        return 3;
    }
    if (!strcmp(name, "click")) {
        int used = 1;
        button   = BTN_LEFT;
        if (argc >= 2 && parseButton(argv[1], &button))
            used = 2;
        ei_device_button_button(s->pointer, button, true);
        frame(s, s->pointer);
        ei_device_button_button(s->pointer, button, false);
        frame(s, s->pointer);
        return used;
    }
    if ((!strcmp(name, "down") || !strcmp(name, "up")) && argc >= 2 && parseButton(argv[1], &button)) {
        ei_device_button_button(s->pointer, button, !strcmp(name, "down"));
        frame(s, s->pointer);
        return 2;
    }
    if (!strcmp(name, "scroll") && argc >= 3 && parseDouble(argv[1], &x) && parseDouble(argv[2], &y)) {
        ei_device_scroll_delta(s->pointer, x, y);
        frame(s, s->pointer);
        ei_device_scroll_stop(s->pointer, x != 0, y != 0);
        frame(s, s->pointer);
        return 3;
    }
    if (!strcmp(name, "key") && argc >= 2) {
        char*         end  = NULL;
        unsigned long code = strtoul(argv[1], &end, 0);
        if (!end || *end != '\0' || code == 0)
            return -1;
        tapKey(s, code, false);
        return 2;
    }
    if (!strcmp(name, "type") && argc >= 2) {
        for (const char* c = argv[1]; *c; ++c) {
            struct keystroke k;
            if (!asciiKey(*c, &k)) {
                fprintf(stderr, "hyprmasq-input: cannot type character 0x%02x\n", (unsigned char)*c);
                return -1;
            }
            tapKey(s, k.code, k.shift);
        }
        return 2;
    }
    if (!strcmp(name, "sleep") && argc >= 2) {
        sleepMs(strtol(argv[1], NULL, 10));
        return 2;
    }

    return -1;
}

int main(int argc, char** argv) {
    if (argc < 3 || strcmp(argv[1], "--socket") != 0) {
        usage(stderr);
        return 2;
    }

    struct state s = {0};
    s.ei           = ei_new_sender(&s);
    if (!s.ei)
        return 1;
    ei_configure_name(s.ei, "hyprmasq-input");

    int rc = ei_setup_backend_socket(s.ei, argv[2]);
    if (rc != 0) {
        fprintf(stderr, "hyprmasq-input: cannot connect to %s: %s\n", argv[2], strerror(-rc));
        ei_unref(s.ei);
        return 1;
    }

    int status = 0;
    if (!pump(&s, devicesReady)) {
        fprintf(stderr, "hyprmasq-input: the server did not offer a pointer and a keyboard\n");
        status = 1;
    }

    for (int i = 3; status == 0 && i < argc;) {
        int used = runAction(&s, argc - i, argv + i);
        if (used < 0) {
            fprintf(stderr, "hyprmasq-input: invalid action at '%s'\n", argv[i]);
            usage(stderr);
            status = 2;
            break;
        }
        i += used;
    }

    // A ping round trip guarantees the server has processed every event before we disconnect.
    if (status == 0) {
        struct ei_ping* ping = ei_new_ping(s.ei);
        ei_ping(ping);
        if (!pump(&s, gotPong)) {
            fprintf(stderr, "hyprmasq-input: the server did not confirm the events\n");
            status = 1;
        }
        ei_ping_unref(ping);
    }

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
    ei_unref(s.ei);
    return status;
}
