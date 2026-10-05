// VM fixture: two toplevels share a Wayland connection. The hidden one requests
// popup grabs, interactive moves/resizes and DnD using an emulated button serial.
// With `popup TITLE [X Y WIDTH HEIGHT [GX GY]]`, one toplevel opens a popup on the
// first SIGUSR1 and destroys it on the second, without any input. With a geometry,
// the popup lies at X,Y of the window with that size, without constraint
// adjustment, so it can reach outside the window. GX,GY give the toplevel a margin
// of that size above and left of its xdg window geometry, as client-side shadows
// do. Pointer events are logged with the surface they reach. With `regions SOCKET MS`, it connects to a hyprmasq socket as a libei
// sender for MS milliseconds and prints the regions of every pointer device the
// plugin adds, and each removal. With `focus TITLE [TITLE]`, it shows one or two
// toplevels on one connection and logs their input and focus events, each after
// a CLOCK_MONOTONIC timestamp in milliseconds. With `close TITLE`, it logs like that
// for one toplevel, destroys it on Escape and goes on logging the keys that still
// arrive. With `hold SOCKET X Y stop` or `hold SOCKET X Y disconnect`, it moves the
// pointer to X,Y as a libei sender, presses Left Shift, A and the left button, and
// then either stops emulating and disconnects a second later, or exits at once,
// releasing nothing. With `hold SOCKET X Y signal`, it prints "ready" once it has
// its devices, and on SIGUSR1 presses them and exits at once, without waiting for
// the plugin. With `resize TITLE hold` or `resize TITLE ack`, it shows one toplevel
// that acks a configure that keeps its drawn size without a commit, as a client may
// that has nothing new to draw. A SIGUSR1 makes it hold back configures with another
// size: with hold, it neither acks nor draws them, with ack, it acks them at once but
// does not draw. The next SIGUSR1 answers the last one held back, acking it with hold,
// and drawing it at its size. Its right edge is green, its rest like the other modes.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include <libei.h>
#include <linux/input-event-codes.h>
#include <assert.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/signalfd.h>
#include <time.h>
#include <unistd.h>

struct window {
    struct wl_surface *surface;
    struct xdg_surface *xdg;
    struct xdg_toplevel *toplevel;
    int width, height;
    const char *name;
    uint32_t color;
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
static bool placed;
static bool stamped;
static bool close_on_escape;
static int popup_x, popup_y, popup_width = 40, popup_height = 40;
static int margin_x, margin_y;

static uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}
// Prints a log line, after a timestamp in the focus and hold modes.
static void say(const char *format, ...) {
    if (stamped) printf("%llu ", (unsigned long long)now_ms());
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n');
}

static const char *surface_name(struct wl_surface *s) {
    for (size_t i = 0; i < 3; ++i)
        if (windows[i].surface == s) return windows[i].name;
    return "unknown";
}
static void buffer_release(void *data, struct wl_buffer *buffer) { wl_buffer_destroy(buffer); }
static const struct wl_buffer_listener buffer_listener = {.release = buffer_release};
// Resize mode: a window that holds back configures with a new size, and what it last drew.
static bool resize_mode, ack_at_once, holding, held;
static uint32_t held_serial;
static int drawn_width, drawn_height;
static void draw(struct window *w);
static void resize_configure(struct window *w, uint32_t serial) {
    if (w->width == drawn_width && w->height == drawn_height) {
        xdg_surface_ack_configure(w->xdg, serial);
        held = false;
        printf("kept %d %d\n", w->width, w->height);
    } else if (holding) {
        if (ack_at_once) xdg_surface_ack_configure(w->xdg, serial);
        held = true;
        held_serial = serial;
        printf("held %d %d\n", w->width, w->height);
    } else {
        xdg_surface_ack_configure(w->xdg, serial);
        draw(w);
    }
}
// Each SIGUSR1 starts holding back configures, or answers the one held back.
static void resize_on_signal(void) {
    struct window *w = &windows[0];
    holding = !holding;
    if (holding) {
        puts("holding");
        return;
    }
    if (held) {
        if (!ack_at_once) xdg_surface_ack_configure(w->xdg, held_serial);
        held = false;
        draw(w);
    }
    puts("answered");
}
static void configure(void *data, struct xdg_surface *surface, uint32_t serial) {
    struct window *w = data;
    if (resize_mode && w == &windows[0]) {
        resize_configure(w, serial);
        return;
    }
    xdg_surface_ack_configure(surface, serial);
    draw(w);
}
static void draw(struct window *w) {
    // Only the first toplevel has a margin, which its window geometry leaves out.
    const int mx = w == &windows[0] ? margin_x : 0, my = w == &windows[0] ? margin_y : 0;
    const int width = w->width + mx, height = w->height + my;
    size_t size = (size_t)width * height * 4;
    int fd = memfd_create("hyprmasq-focus-probe", MFD_CLOEXEC);
    assert(fd >= 0 && ftruncate(fd, size) == 0);
    uint32_t *pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(pixels != MAP_FAILED);
    for (size_t i = 0; i < size / 4; ++i) pixels[i] = w->color;
    // In resize mode, its right edge is green, so a frame of another width shows where it ends.
    if (resize_mode && w == &windows[0])
        for (int y = 0; y < height; ++y)
            for (int x = width > 8 ? width - 8 : 0; x < width; ++x) pixels[(size_t)y * width + x] = 0xff22cc22;
    struct wl_shm_pool *pool = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer *buffer = wl_shm_pool_create_buffer(pool, 0, width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
    wl_buffer_add_listener(buffer, &buffer_listener, NULL);
    wl_shm_pool_destroy(pool);
    munmap(pixels, size);
    close(fd);
    if (mx || my) xdg_surface_set_window_geometry(w->xdg, mx, my, w->width, w->height);
    wl_surface_attach(w->surface, buffer, 0, 0);
    wl_surface_damage(w->surface, 0, 0, width, height);
    wl_surface_commit(w->surface);
    if (resize_mode && w == &windows[0]) {
        drawn_width = w->width;
        drawn_height = w->height;
        printf("drew %d %d\n", w->width, w->height);
    } else
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
static void popup_configure(void *data, struct xdg_popup *popup, int32_t x, int32_t y, int32_t w, int32_t h) {
    printf("popup at %d %d %d %d\n", x, y, w, h);
}
static void popup_done(void *data, struct xdg_popup *popup) { puts("popup done"); }
static const struct xdg_popup_listener popup_listener = {.configure = popup_configure, .popup_done = popup_done};

static struct xdg_popup *open_popup(void) {
    struct window *w = &windows[2];
    *w = (struct window){.width = popup_width, .height = popup_height, .name = "popup", .color = 0xffcc2222};
    w->surface = wl_compositor_create_surface(compositor);
    w->xdg = xdg_wm_base_get_xdg_surface(wm, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    struct xdg_positioner *positioner = xdg_wm_base_create_positioner(wm);
    xdg_positioner_set_size(positioner, popup_width, popup_height);
    if (placed) {
        xdg_positioner_set_anchor_rect(positioner, 0, 0, 1, 1);
        xdg_positioner_set_anchor(positioner, XDG_POSITIONER_ANCHOR_TOP_LEFT);
        xdg_positioner_set_gravity(positioner, XDG_POSITIONER_GRAVITY_BOTTOM_RIGHT);
        xdg_positioner_set_offset(positioner, popup_x, popup_y);
        xdg_positioner_set_constraint_adjustment(positioner, XDG_POSITIONER_CONSTRAINT_ADJUSTMENT_NONE);
    } else
        xdg_positioner_set_anchor_rect(positioner, 0, 0, 10, 10);
    struct xdg_popup *popup = xdg_surface_get_popup(w->xdg, windows[0].xdg, positioner);
    xdg_positioner_destroy(positioner);
    xdg_popup_add_listener(popup, &popup_listener, NULL);
    return popup;
}
static void request_grabs(uint32_t serial) {
    requested = true;
    xdg_toplevel_move(windows[0].toplevel, seat, serial);
    xdg_toplevel_resize(windows[0].toplevel, seat, serial, XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM_RIGHT);
    puts("requested move and resize");
    struct xdg_popup *popup = open_popup();
    xdg_popup_grab(popup, seat, serial);
    wl_surface_commit(windows[2].surface);
    puts("requested popup grab");
    struct wl_data_source *source = wl_data_device_manager_create_data_source(data_manager);
    wl_data_source_add_listener(source, &source_listener, NULL);
    wl_data_source_offer(source, "text/plain");
    wl_data_device_start_drag(data_device, source, windows[0].surface, NULL, serial);
    puts("requested drag");
}
static void pointer_enter(void *data, struct wl_pointer *p, uint32_t serial, struct wl_surface *s, wl_fixed_t x, wl_fixed_t y) {
    pointer_surface = s;
    say("pointer enter %s", surface_name(s));
}
static void pointer_leave(void *data, struct wl_pointer *p, uint32_t serial, struct wl_surface *s) {
    pointer_surface = NULL;
    say("pointer leave %s", surface_name(s));
}
static void pointer_motion(void *data, struct wl_pointer *p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {
    say("motion %s %g %g", surface_name(pointer_surface), wl_fixed_to_double(x), wl_fixed_to_double(y));
}
static void pointer_button(void *data, struct wl_pointer *p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    say("button %s %u", surface_name(pointer_surface), state);
    if (pointer_surface == windows[0].surface && state == WL_POINTER_BUTTON_STATE_PRESSED && !requested) request_grabs(serial);
}
static void pointer_axis(void *data, struct wl_pointer *p, uint32_t time, uint32_t axis, wl_fixed_t value) {}
static const struct wl_pointer_listener pointer_listener = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion, .button = pointer_button, .axis = pointer_axis,
};
static void keyboard_keymap(void *data, struct wl_keyboard *kb, uint32_t format, int32_t fd, uint32_t size) { close(fd); }
static void keyboard_enter(void *data, struct wl_keyboard *kb, uint32_t serial, struct wl_surface *s, struct wl_array *keys) {
    keyboard_surface = s;
    say("keyboard enter %s", surface_name(s));
}
static void keyboard_leave(void *data, struct wl_keyboard *kb, uint32_t serial, struct wl_surface *s) {
    keyboard_surface = NULL;
    say("keyboard leave %s", surface_name(s));
}
static void keyboard_key(void *data, struct wl_keyboard *kb, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    say("key %s %u %u", surface_name(keyboard_surface), key, state);
    if (close_on_escape && key == KEY_ESC && state == WL_KEYBOARD_KEY_STATE_PRESSED && windows[0].toplevel) {
        xdg_toplevel_destroy(windows[0].toplevel);
        xdg_surface_destroy(windows[0].xdg);
        wl_surface_destroy(windows[0].surface);
        windows[0] = (struct window){.name = windows[0].name};
        wl_display_flush(display);
        say("closed");
    }
}
static void keyboard_modifiers(void *data, struct wl_keyboard *kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {
    say("modifiers %s %u", surface_name(keyboard_surface), dep);
}
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
static void create_toplevel(struct window *w, const char *name) {
    *w = (struct window){.width = 250, .height = 200, .name = name, .color = 0xff224466};
    w->surface = wl_compositor_create_surface(compositor);
    w->xdg = xdg_wm_base_get_xdg_surface(wm, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    w->toplevel = xdg_surface_get_toplevel(w->xdg);
    xdg_toplevel_add_listener(w->toplevel, &toplevel_listener, w);
    xdg_toplevel_set_title(w->toplevel, w->name);
    wl_surface_commit(w->surface);
}
// Each SIGUSR1 opens or destroys the popup. Nothing else happens in between, so the
// popup's commits are the only ones of the window.
static void popup_on_signal(void) {
    static struct xdg_popup *popup;
    if (!popup) {
        popup = open_popup();
        wl_surface_commit(windows[2].surface);
        puts("popup opened");
    } else {
        xdg_popup_destroy(popup);
        xdg_surface_destroy(windows[2].xdg);
        wl_surface_destroy(windows[2].surface);
        popup = NULL;
        puts("popup closed");
    }
}
// Shows one toplevel and calls on_signal for each SIGUSR1.
static int run_with_signal(const char *title, void (*on_signal)(void)) {
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGUSR1);
    assert(sigprocmask(SIG_BLOCK, &mask, NULL) == 0);
    int signals = signalfd(-1, &mask, SFD_CLOEXEC);
    assert(signals >= 0);
    create_toplevel(&windows[0], title);
    struct pollfd fds[2] = {{.fd = wl_display_get_fd(display), .events = POLLIN}, {.fd = signals, .events = POLLIN}};
    for (;;) {
        while (wl_display_prepare_read(display) != 0) wl_display_dispatch_pending(display);
        if (wl_display_flush(display) < 0 || poll(fds, 2, -1) < 0) {
            wl_display_cancel_read(display);
            return 1;
        }
        if (fds[0].revents & POLLIN) {
            if (wl_display_read_events(display) < 0) return 1;
        } else
            wl_display_cancel_read(display);
        if (wl_display_dispatch_pending(display) < 0) return 1;
        if (fds[1].revents & POLLIN) {
            struct signalfd_siginfo info;
            assert(read(signals, &info, sizeof(info)) == sizeof(info));
            on_signal();
        }
    }
}
static int print_regions(const char *path, int ms) {
    struct ei *ei = ei_new_sender(NULL);
    ei_configure_name(ei, "hyprmasq-focus-probe");
    if (ei_setup_backend_socket(ei, path) != 0) return 1;
    const uint64_t deadline = now_ms() + (uint64_t)ms;
    struct pollfd fd = {.fd = ei_get_fd(ei), .events = POLLIN};
    for (uint64_t now; (now = now_ms()) < deadline;) {
        if (poll(&fd, 1, (int)(deadline - now)) < 0) return 1;
        ei_dispatch(ei);
        for (struct ei_event *e; (e = ei_get_event(ei)); ei_event_unref(e)) {
            struct ei_device *device = ei_event_get_device(e);
            switch (ei_event_get_type(e)) {
                case EI_EVENT_SEAT_ADDED:
                    ei_seat_bind_capabilities(ei_event_get_seat(e), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL, NULL);
                    break;
                case EI_EVENT_DEVICE_ADDED:
                    if (!ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE)) break;
                    printf("pointer");
                    for (size_t i = 0; ei_device_get_region(device, i); ++i) {
                        struct ei_region *r = ei_device_get_region(device, i);
                        printf("%s %u %u %u %u", i ? "," : "", ei_region_get_x(r), ei_region_get_y(r), ei_region_get_width(r), ei_region_get_height(r));
                    }
                    printf("\n");
                    break;
                case EI_EVENT_DEVICE_REMOVED:
                    if (ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE)) puts("pointer removed");
                    break;
                case EI_EVENT_DISCONNECT: puts("disconnected"); return 1;
                default: break;
            }
        }
    }
    ei_unref(ei);
    return 0;
}

static struct ei *sender;
static struct ei_device *sender_pointer, *sender_keyboard;
static bool pointer_resumed, keyboard_resumed, ponged;
static void sender_dispatch(void) {
    ei_dispatch(sender);
    for (struct ei_event *e; (e = ei_get_event(sender)); ei_event_unref(e)) {
        struct ei_device *device = ei_event_get_device(e);
        switch (ei_event_get_type(e)) {
            case EI_EVENT_SEAT_ADDED:
                ei_seat_bind_capabilities(ei_event_get_seat(e), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL,
                                          EI_DEVICE_CAP_KEYBOARD, NULL);
                break;
            case EI_EVENT_DEVICE_ADDED:
                if (!sender_pointer && ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE)) sender_pointer = ei_device_ref(device);
                else if (!sender_keyboard && ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD)) sender_keyboard = ei_device_ref(device);
                break;
            case EI_EVENT_DEVICE_RESUMED:
                pointer_resumed |= device == sender_pointer;
                keyboard_resumed |= device == sender_keyboard;
                break;
            case EI_EVENT_PONG: ponged = true; break;
            case EI_EVENT_DISCONNECT: say("disconnected"); exit(1);
            default: break;
        }
    }
}
// Dispatches until *flag is set, for at most ms milliseconds.
static bool sender_wait(bool *flag, int ms) {
    const uint64_t deadline = now_ms() + (uint64_t)ms;
    struct pollfd fd = {.fd = ei_get_fd(sender), .events = POLLIN};
    for (uint64_t now; !*flag && (now = now_ms()) < deadline;) {
        if (poll(&fd, 1, (int)(deadline - now)) < 0) return false;
        sender_dispatch();
    }
    return *flag;
}
// Returns once the plugin has handled everything sent before.
static bool round_trip(void) {
    struct ei_ping *ping = ei_new_ping(sender);
    ponged = false;
    ei_ping(ping);
    const bool ok = sender_wait(&ponged, 5000);
    ei_ping_unref(ping);
    return ok;
}
static int hold_input(const char *path, double x, double y, const char *mode) {
    sigset_t usr1;
    sigemptyset(&usr1);
    sigaddset(&usr1, SIGUSR1);
    assert(sigprocmask(SIG_BLOCK, &usr1, NULL) == 0);
    sender = ei_new_sender(NULL);
    ei_configure_name(sender, "hyprmasq-focus-probe");
    if (ei_setup_backend_socket(sender, path) != 0 || !sender_wait(&pointer_resumed, 5000) || !sender_wait(&keyboard_resumed, 5000)) return 1;
    const bool signalled = !strcmp(mode, "signal");
    if (signalled) {
        say("ready");
        int received;
        sigwait(&usr1, &received);
    }
    ei_device_start_emulating(sender_pointer, 1);
    ei_device_start_emulating(sender_keyboard, 2);
    ei_device_pointer_motion_absolute(sender_pointer, x, y);
    ei_device_frame(sender_pointer, ei_now(sender));
    ei_device_button_button(sender_pointer, BTN_LEFT, true);
    ei_device_frame(sender_pointer, ei_now(sender));
    ei_device_keyboard_key(sender_keyboard, KEY_LEFTSHIFT, true);
    ei_device_frame(sender_keyboard, ei_now(sender));
    ei_device_keyboard_key(sender_keyboard, KEY_A, true);
    ei_device_frame(sender_keyboard, ei_now(sender));
    if (signalled) {
        say("disconnecting");
        _exit(0);
    }
    if (!round_trip()) return 1;
    say("held");
    if (!strcmp(mode, "disconnect")) {
        say("disconnecting");
        _exit(0); // the kernel closes the socket
    }
    ei_device_stop_emulating(sender_pointer);
    ei_device_stop_emulating(sender_keyboard);
    if (!round_trip()) return 1;
    say("stopped");
    bool never = false;
    sender_wait(&never, 1000);
    say("disconnecting");
    ei_device_unref(sender_pointer);
    ei_device_unref(sender_keyboard);
    ei_unref(sender);
    return 0;
}

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc == 4 && !strcmp(argv[1], "regions")) return print_regions(argv[2], atoi(argv[3]));
    if (argc == 6 && !strcmp(argv[1], "hold")) {
        stamped = true;
        return hold_input(argv[2], atof(argv[3]), atof(argv[4]), argv[5]);
    }
    display = wl_display_connect(NULL);
    assert(display);
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    assert(wl_display_roundtrip(display) >= 0);
    assert(compositor && shm && wm && seat && data_manager);
    data_device = wl_data_device_manager_get_data_device(data_manager, seat);
    wl_data_device_add_listener(data_device, &data_listener, NULL);
    if ((argc == 3 || argc == 7 || argc == 9) && !strcmp(argv[1], "popup")) {
        if (argc >= 7) {
            placed = true;
            popup_x = atoi(argv[3]);
            popup_y = atoi(argv[4]);
            popup_width = atoi(argv[5]);
            popup_height = atoi(argv[6]);
        }
        if (argc == 9) {
            margin_x = atoi(argv[7]);
            margin_y = atoi(argv[8]);
        }
        return run_with_signal(argv[2], popup_on_signal);
    }
    if (argc == 4 && !strcmp(argv[1], "resize") && (!strcmp(argv[3], "hold") || !strcmp(argv[3], "ack"))) {
        resize_mode = true;
        requested = true; // clicks request nothing
        ack_at_once = !strcmp(argv[3], "ack");
        return run_with_signal(argv[2], resize_on_signal);
    }
    if (argc == 3 && !strcmp(argv[1], "close")) {
        stamped = close_on_escape = true;
        requested = true;
        create_toplevel(&windows[0], argv[2]);
        while (wl_display_dispatch(display) >= 0) {}
        return 1;
    }
    if ((argc == 3 || argc == 4) && !strcmp(argv[1], "focus")) {
        stamped = true;
        requested = true; // clicks request nothing
        create_toplevel(&windows[0], argv[2]);
        if (argc == 4) create_toplevel(&windows[1], argv[3]);
        while (wl_display_dispatch(display) >= 0) {}
        return 1;
    }
    create_toplevel(&windows[0], "hm-guard-hidden");
    create_toplevel(&windows[1], "hm-guard-visible");
    while (wl_display_dispatch(display) >= 0) {}
    return 1;
}
