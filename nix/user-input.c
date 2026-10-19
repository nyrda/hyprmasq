// VM fixture: the user's own input, which reaches Hyprland as real input, not through hyprmasq.
// With `button X Y MS`, a zwlr_virtual_pointer_v1 moves to X,Y of the 640x480 layout, presses the left button, holds it
// MS milliseconds and releases it, as the user's mouse in a selection or a slider. It prints "pressed T" and
// "released T" after doing so.
// With `ime`, it is an input method that grabs the keyboard, as fcitx5 and ibus do, so Hyprland sends it the user's
// keys instead of the window with the keyboard focus. It prints "grabbed T" once the grab has its keymap, and then
// "key T CODE STATE" for every key it gets, until it is killed. It commits no text.
// T is CLOCK_MONOTONIC in milliseconds, as focus-probe stamps its events.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "input-method-unstable-v2-client-protocol.h"
#include "wlr-virtual-pointer-unstable-v1-client-protocol.h"
#include "probe-common.h"
#include <linux/input-event-codes.h>

static struct wl_seat*                         seat;
static struct zwlr_virtual_pointer_manager_v1* pointers;
static struct zwp_input_method_manager_v2*     methods;

static void sleep_ms(int ms) {
    struct timespec duration = {.tv_sec = ms / 1000, .tv_nsec = (long)(ms % 1000) * 1000000};
    while (nanosleep(&duration, &duration) != 0 && errno == EINTR) {}
}

static void keymap(void* data, struct zwp_input_method_keyboard_grab_v2* grab, uint32_t format, int32_t fd, uint32_t size) {
    close(fd);
    printf("grabbed %llu\n", (unsigned long long)now_ms());
    fflush(stdout);
}
static void key(void* data, struct zwp_input_method_keyboard_grab_v2* grab, uint32_t serial, uint32_t time, uint32_t code, uint32_t state) {
    printf("key %llu %u %u\n", (unsigned long long)now_ms(), code, state);
    fflush(stdout);
}
static void modifiers(void* data, struct zwp_input_method_keyboard_grab_v2* grab, uint32_t serial, uint32_t depressed, uint32_t latched, uint32_t locked, uint32_t group) {}
static void repeat_info(void* data, struct zwp_input_method_keyboard_grab_v2* grab, int32_t rate, int32_t delay) {}
static const struct zwp_input_method_keyboard_grab_v2_listener grab_listener = {.keymap = keymap, .key = key, .modifiers = modifiers, .repeat_info = repeat_info};

static int button(struct wl_display* display, int x, int y, int ms) {
    if (!pointers) {
        fprintf(stderr, "no virtual pointer manager\n");
        return 1;
    }
    struct zwlr_virtual_pointer_v1* pointer = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(pointers, seat);
    zwlr_virtual_pointer_v1_motion_absolute(pointer, (uint32_t)now_ms(), x, y, 640, 480);
    zwlr_virtual_pointer_v1_frame(pointer);
    wl_display_roundtrip(display);
    sleep_ms(300);
    zwlr_virtual_pointer_v1_button(pointer, (uint32_t)now_ms(), BTN_LEFT, WL_POINTER_BUTTON_STATE_PRESSED);
    zwlr_virtual_pointer_v1_frame(pointer);
    wl_display_roundtrip(display);
    printf("pressed %llu\n", (unsigned long long)now_ms());
    fflush(stdout);
    sleep_ms(ms);
    zwlr_virtual_pointer_v1_button(pointer, (uint32_t)now_ms(), BTN_LEFT, WL_POINTER_BUTTON_STATE_RELEASED);
    zwlr_virtual_pointer_v1_frame(pointer);
    wl_display_roundtrip(display);
    printf("released %llu\n", (unsigned long long)now_ms());
    fflush(stdout);
    zwlr_virtual_pointer_v1_destroy(pointer);
    wl_display_roundtrip(display);
    return 0;
}

static int ime(struct wl_display* display) {
    if (!methods) {
        fprintf(stderr, "no input method manager\n");
        return 1;
    }
    struct zwp_input_method_v2*               method = zwp_input_method_manager_v2_get_input_method(methods, seat);
    struct zwp_input_method_keyboard_grab_v2* grab   = zwp_input_method_v2_grab_keyboard(method);
    zwp_input_method_keyboard_grab_v2_add_listener(grab, &grab_listener, NULL);
    while (wl_display_dispatch(display) >= 0)
        ;
    return 0;
}

static void usage(void) {
    fprintf(stderr, "usage: hyprmasq-user-input button X Y MS | ime\n");
    exit(2);
}

int main(int argc, char** argv) {
    const bool is_button = argc == 5 && !strcmp(argv[1], "button"), is_ime = argc == 2 && !strcmp(argv[1], "ime");
    if (!is_button && !is_ime)
        usage();
    int x = 0, y = 0, ms = 0;
    if (is_button) {
        x  = parse_int(argv[2], "X", 0, 639);
        y  = parse_int(argv[3], "Y", 0, 479);
        ms = parse_int(argv[4], "MS", 0, INT32_MAX);
    }
    struct wl_display* display = wl_display_connect(NULL);
    if (!display) {
        fprintf(stderr, "hyprmasq-user-input: cannot connect to the compositor\n");
        return 1;
    }
    bind_globals(display,
                 (const struct probe_global[]){{.proxy = &seat, .interface = &wl_seat_interface, .version = 1},
                                               {.proxy = &pointers, .interface = &zwlr_virtual_pointer_manager_v1_interface, .version = 1, .optional = true},
                                               {.proxy = &methods, .interface = &zwp_input_method_manager_v2_interface, .version = 1, .optional = true},
                                               {0}});
    return is_button ? button(display, x, y, ms) : ime(display);
}
