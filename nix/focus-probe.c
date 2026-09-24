// VM fixture: two toplevels share a Wayland connection. The hidden one requests
// popup grabs, interactive moves/resizes and DnD using an emulated button serial.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

struct window {
    struct wl_surface *surface;
    struct xdg_surface *xdg;
    struct xdg_toplevel *toplevel;
    int width, height;
    const char *name;
};
static struct wl_display *display;
static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct xdg_wm_base *wm;
static struct wl_data_device_manager *data_manager;
static struct wl_data_device *data_device;
static struct window windows[3];
static struct wl_surface *pointer_surface, *keyboard_surface;
static bool requested;

static const char *surface_name(struct wl_surface *s) {
    for (size_t i = 0; i < 3; ++i)
        if (windows[i].surface == s) return windows[i].name;
    return "unknown";
}
static void buffer_release(void *data, struct wl_buffer *buffer) { wl_buffer_destroy(buffer); }
static const struct wl_buffer_listener buffer_listener = {.release = buffer_release};
static void configure(void *data, struct xdg_surface *surface, uint32_t serial) {
    struct window *w = data;
    xdg_surface_ack_configure(surface, serial);
    size_t size = (size_t)w->width * w->height * 4;
    int fd = memfd_create("hyprmasq-focus-probe", MFD_CLOEXEC);
    assert(fd >= 0 && ftruncate(fd, size) == 0);
    uint32_t *pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(pixels != MAP_FAILED);
    for (size_t i = 0; i < size / 4; ++i) pixels[i] = 0xff224466;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer *buffer = wl_shm_pool_create_buffer(pool, 0, w->width, w->height, w->width * 4, WL_SHM_FORMAT_XRGB8888);
    wl_buffer_add_listener(buffer, &buffer_listener, NULL);
    wl_shm_pool_destroy(pool);
    munmap(pixels, size);
    close(fd);
    wl_surface_attach(w->surface, buffer, 0, 0);
    wl_surface_damage(w->surface, 0, 0, w->width, w->height);
    wl_surface_commit(w->surface);
    printf("configured %s\n", w->name);
}
static const struct xdg_surface_listener surface_listener = {.configure = configure};
static void toplevel_configure(void *data, struct xdg_toplevel *top, int32_t width, int32_t height, struct wl_array *states) {
    struct window *w = data;
    if (width > 0) w->width = width;
    if (height > 0) w->height = height;
}
static void toplevel_close(void *data, struct xdg_toplevel *top) { exit(0); }
static const struct xdg_toplevel_listener toplevel_listener = {.configure = toplevel_configure, .close = toplevel_close};
static void ping(void *data, struct xdg_wm_base *base, uint32_t serial) { xdg_wm_base_pong(base, serial); }
static const struct xdg_wm_base_listener wm_listener = {.ping = ping};
static void offer_mime(void *data, struct wl_data_offer *offer, const char *mime) {}
static const struct wl_data_offer_listener offer_listener = {.offer = offer_mime};
static void data_offer(void *data, struct wl_data_device *device, struct wl_data_offer *offer) {
    wl_data_offer_add_listener(offer, &offer_listener, NULL);
}
static void data_enter(void *data, struct wl_data_device *device, uint32_t serial, struct wl_surface *surface,
                       wl_fixed_t x, wl_fixed_t y, struct wl_data_offer *offer) { puts("unexpected drag enter"); }
static void data_leave(void *data, struct wl_data_device *device) {}
static void data_motion(void *data, struct wl_data_device *device, uint32_t time, wl_fixed_t x, wl_fixed_t y) {}
static void data_drop(void *data, struct wl_data_device *device) {}
static void data_selection(void *data, struct wl_data_device *device, struct wl_data_offer *offer) {}
static const struct wl_data_device_listener data_listener = {
    .data_offer = data_offer, .enter = data_enter, .leave = data_leave, .motion = data_motion, .drop = data_drop, .selection = data_selection,
};
static void source_target(void *data, struct wl_data_source *source, const char *mime) {}
static void source_send(void *data, struct wl_data_source *source, const char *mime, int32_t fd) { close(fd); }
static void source_cancelled(void *data, struct wl_data_source *source) {
    puts("drag cancelled");
    wl_data_source_destroy(source);
}
static const struct wl_data_source_listener source_listener = {.target = source_target, .send = source_send, .cancelled = source_cancelled};
static void popup_configure(void *data, struct xdg_popup *popup, int32_t x, int32_t y, int32_t w, int32_t h) {}
static void popup_done(void *data, struct xdg_popup *popup) { puts("popup done"); }
static const struct xdg_popup_listener popup_listener = {.configure = popup_configure, .popup_done = popup_done};

static void request_grabs(uint32_t serial) {
    requested = true;
    xdg_toplevel_move(windows[0].toplevel, seat, serial);
    xdg_toplevel_resize(windows[0].toplevel, seat, serial, XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM_RIGHT);
    puts("requested move and resize");
    struct window *w = &windows[2];
    *w = (struct window){.width = 40, .height = 40, .name = "popup"};
    w->surface = wl_compositor_create_surface(compositor);
    w->xdg = xdg_wm_base_get_xdg_surface(wm, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    struct xdg_positioner *positioner = xdg_wm_base_create_positioner(wm);
    xdg_positioner_set_size(positioner, 40, 40);
    xdg_positioner_set_anchor_rect(positioner, 0, 0, 10, 10);
    struct xdg_popup *popup = xdg_surface_get_popup(w->xdg, windows[0].xdg, positioner);
    xdg_positioner_destroy(positioner);
    xdg_popup_add_listener(popup, &popup_listener, NULL);
    xdg_popup_grab(popup, seat, serial);
    wl_surface_commit(w->surface);
    puts("requested popup grab");
    struct wl_data_source *source = wl_data_device_manager_create_data_source(data_manager);
    wl_data_source_add_listener(source, &source_listener, NULL);
    wl_data_source_offer(source, "text/plain");
    wl_data_device_start_drag(data_device, source, windows[0].surface, NULL, serial);
    puts("requested drag");
}
static void pointer_enter(void *data, struct wl_pointer *p, uint32_t serial, struct wl_surface *s, wl_fixed_t x, wl_fixed_t y) {
    pointer_surface = s;
    printf("pointer enter %s\n", surface_name(s));
}
static void pointer_leave(void *data, struct wl_pointer *p, uint32_t serial, struct wl_surface *s) {
    pointer_surface = NULL;
    printf("pointer leave %s\n", surface_name(s));
}
static void pointer_motion(void *data, struct wl_pointer *p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {}
static void pointer_button(void *data, struct wl_pointer *p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    printf("button %s %u\n", surface_name(pointer_surface), state);
    if (pointer_surface == windows[0].surface && state == WL_POINTER_BUTTON_STATE_PRESSED && !requested) request_grabs(serial);
}
static void pointer_axis(void *data, struct wl_pointer *p, uint32_t time, uint32_t axis, wl_fixed_t value) {}
static const struct wl_pointer_listener pointer_listener = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion, .button = pointer_button, .axis = pointer_axis,
};
static void keyboard_keymap(void *data, struct wl_keyboard *kb, uint32_t format, int32_t fd, uint32_t size) { close(fd); }
static void keyboard_enter(void *data, struct wl_keyboard *kb, uint32_t serial, struct wl_surface *s, struct wl_array *keys) {
    keyboard_surface = s;
    printf("keyboard enter %s\n", surface_name(s));
}
static void keyboard_leave(void *data, struct wl_keyboard *kb, uint32_t serial, struct wl_surface *s) { keyboard_surface = NULL; }
static void keyboard_key(void *data, struct wl_keyboard *kb, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    printf("key %s %u %u\n", surface_name(keyboard_surface), key, state);
}
static void keyboard_modifiers(void *data, struct wl_keyboard *kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {}
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave, .key = keyboard_key, .modifiers = keyboard_modifiers,
};
static void seat_caps(void *data, struct wl_seat *s, uint32_t caps) {
    static struct wl_pointer *pointer;
    static struct wl_keyboard *keyboard;
    if ((caps & WL_SEAT_CAPABILITY_POINTER) && !pointer) {
        pointer = wl_seat_get_pointer(s);
        wl_pointer_add_listener(pointer, &pointer_listener, NULL);
    }
    if ((caps & WL_SEAT_CAPABILITY_KEYBOARD) && !keyboard) {
        keyboard = wl_seat_get_keyboard(s);
        wl_keyboard_add_listener(keyboard, &keyboard_listener, NULL);
    }
}
static const struct wl_seat_listener seat_listener = {.capabilities = seat_caps};
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    if (!strcmp(interface, "wl_compositor")) compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 1);
    else if (!strcmp(interface, "wl_shm")) shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!strcmp(interface, "xdg_wm_base")) {
        wm = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
        xdg_wm_base_add_listener(wm, &wm_listener, NULL);
    } else if (!strcmp(interface, "wl_seat")) {
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
        wl_seat_add_listener(seat, &seat_listener, NULL);
    } else if (!strcmp(interface, "wl_data_device_manager")) data_manager = wl_registry_bind(registry, name, &wl_data_device_manager_interface, 1);
}
static void global_remove(void *data, struct wl_registry *registry, uint32_t name) {}
static const struct wl_registry_listener registry_listener = {.global = global, .global_remove = global_remove};
int main(void) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    display = wl_display_connect(NULL);
    assert(display);
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    assert(wl_display_roundtrip(display) >= 0);
    assert(compositor && shm && wm && seat && data_manager);
    data_device = wl_data_device_manager_get_data_device(data_manager, seat);
    wl_data_device_add_listener(data_device, &data_listener, NULL);
    for (size_t i = 0; i < 2; ++i) {
        struct window *w = &windows[i];
        *w = (struct window){.width = 250, .height = 200, .name = i ? "hm-guard-visible" : "hm-guard-hidden"};
        w->surface = wl_compositor_create_surface(compositor);
        w->xdg = xdg_wm_base_get_xdg_surface(wm, w->surface);
        xdg_surface_add_listener(w->xdg, &surface_listener, w);
        w->toplevel = xdg_surface_get_toplevel(w->xdg);
        xdg_toplevel_add_listener(w->toplevel, &toplevel_listener, w);
        xdg_toplevel_set_title(w->toplevel, w->name);
        wl_surface_commit(w->surface);
    }
    while (wl_display_dispatch(display) >= 0) {}
    return 1;
}
