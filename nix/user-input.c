// VM fixture: the user's own input, which reaches Hyprland as real input, not through hyprmasq.
// With `button X Y MS`, a zwlr_virtual_pointer_v1 moves to X,Y of the 640x480 layout, presses the left button, holds it
// MS milliseconds and releases it, as the user's mouse in a selection or a slider. It prints "pressed T" and
// "released T" after doing so.
// With `ime`, it is an input method that grabs the keyboard, as fcitx5 and ibus do, so Hyprland sends it the user's
// keys instead of the window with the keyboard focus. It prints "grabbed T" once the grab has its keymap, and then
// "key T CODE STATE" for every key it gets, until it is killed. It commits no text.
// T is CLOCK_MONOTONIC in milliseconds, as focus-probe stamps its events.
#include <linux/input-event-codes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include "input-method-unstable-v2-client-protocol.h"
#include "wlr-virtual-pointer-unstable-v1-client-protocol.h"

static struct wl_seat*                         seat;
static struct zwlr_virtual_pointer_manager_v1* pointers;
static struct zwp_input_method_manager_v2*     methods;

static unsigned long long                      now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

static void global(void* data, struct wl_registry* registry, uint32_t name, const char* interface, uint32_t version) {
    if (!strcmp(interface, wl_seat_interface.name))
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
    else if (!strcmp(interface, zwlr_virtual_pointer_manager_v1_interface.name))
        pointers = wl_registry_bind(registry, name, &zwlr_virtual_pointer_manager_v1_interface, 1);
    else if (!strcmp(interface, zwp_input_method_manager_v2_interface.name))
        methods = wl_registry_bind(registry, name, &zwp_input_method_manager_v2_interface, 1);
}
static void                              global_remove(void* data, struct wl_registry* registry, uint32_t name) {}
static const struct wl_registry_listener registry_listener = {global, global_remove};

static void                              keymap(void* data, struct zwp_input_method_keyboard_grab_v2* grab, uint32_t format, int32_t fd, uint32_t size) {
    close(fd);
    printf("grabbed %llu\n", now_ms());
    fflush(stdout);
}
static void key(void* data, struct zwp_input_method_keyboard_grab_v2* grab, uint32_t serial, uint32_t time, uint32_t code, uint32_t state) {
    printf("key %llu %u %u\n", now_ms(), code, state);
    fflush(stdout);
}
static void modifiers(void* data, struct zwp_input_method_keyboard_grab_v2* grab, uint32_t serial, uint32_t depressed, uint32_t latched, uint32_t locked, uint32_t group) {}
static void repeat_info(void* data, struct zwp_input_method_keyboard_grab_v2* grab, int32_t rate, int32_t delay) {}
static const struct zwp_input_method_keyboard_grab_v2_listener grab_listener = {keymap, key, modifiers, repeat_info};

static int                                                     button(struct wl_display* display, int x, int y, int ms) {
    if (!pointers) {
        fprintf(stderr, "no virtual pointer manager\n");
        return 1;
    }
    struct zwlr_virtual_pointer_v1* pointer = zwlr_virtual_pointer_manager_v1_create_virtual_pointer(pointers, seat);
    zwlr_virtual_pointer_v1_motion_absolute(pointer, (uint32_t)now_ms(), x, y, 640, 480);
    zwlr_virtual_pointer_v1_frame(pointer);
    wl_display_roundtrip(display);
    usleep(300 * 1000);
    zwlr_virtual_pointer_v1_button(pointer, (uint32_t)now_ms(), BTN_LEFT, WL_POINTER_BUTTON_STATE_PRESSED);
    zwlr_virtual_pointer_v1_frame(pointer);
    wl_display_roundtrip(display);
    printf("pressed %llu\n", now_ms());
    fflush(stdout);
    usleep((useconds_t)ms * 1000);
    zwlr_virtual_pointer_v1_button(pointer, (uint32_t)now_ms(), BTN_LEFT, WL_POINTER_BUTTON_STATE_RELEASED);
    zwlr_virtual_pointer_v1_frame(pointer);
    wl_display_roundtrip(display);
    printf("released %llu\n", now_ms());
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

int main(int argc, char** argv) {
    const bool isButton = argc == 5 && !strcmp(argv[1], "button"), isIme = argc == 2 && !strcmp(argv[1], "ime");
    if (!isButton && !isIme) {
        fprintf(stderr, "usage: hyprmasq-user-input button X Y MS | ime\n");
        return 2;
    }
    struct wl_display* display = wl_display_connect(NULL);
    if (!display)
        return 1;
    struct wl_registry* registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    if (!seat) {
        fprintf(stderr, "no seat\n");
        return 1;
    }
    return isButton ? button(display, atoi(argv[2]), atoi(argv[3]), atoi(argv[4])) : ime(display);
}
