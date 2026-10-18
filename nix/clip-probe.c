// VM fixture: a raw Wayland client that copies, selects and pastes text through the clipboard and the primary selection.
//
//   hyprmasq-clip-probe TITLE
//
// It shows a toplevel TITLE of 200×100, gray. On the press of a key or a button, with that press's serial:
//   C            copies TITLE-copyN to the clipboard, as text/plain;charset=utf-8 and text/plain
//   S, a left    selects TITLE-selectN as the primary selection
//   button
//   V            pastes the clipboard: reads the text/plain;charset=utf-8 of its last selection offer into a pipe
//   M            pastes the primary selection in the same way
// N counts the copies and selections from 1.
//
// It logs every event after a CLOCK_MONOTONIC timestamp in milliseconds, as focus-probe's focus mode does: "copy TEXT",
// "select TEXT", "clipboard offer" or "clipboard none" and "primary offer" or "primary none" for the selections it is
// sent, "source send TEXT" and "source cancelled TEXT" for its sources, "paste clipboard TEXT", "paste primary TEXT", with
// none instead of a TEXT when it has no offer and timeout when no data ended within 3 s, "keyboard enter", "keyboard
// leave" and "ready" once it drew.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "primary-selection-unstable-v1-client-protocol.h"
#include <linux/input-event-codes.h>
#include <assert.h>
#include <fcntl.h>
#include <poll.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#define MIME "text/plain;charset=utf-8"

static struct wl_display*                              display;
static struct wl_compositor*                           compositor;
static struct wl_shm*                                  shm;
static struct wl_seat*                                 seat;
static struct xdg_wm_base*                             wm;
static struct wl_data_device_manager*                  manager;
static struct zwp_primary_selection_device_manager_v1* primary_manager;
static struct wl_data_device*                          device;
static struct zwp_primary_selection_device_v1*         primary_device;
static struct wl_surface*                              surface;
static const char*                                     title;
static struct wl_data_offer*                           clipboard_offer;
static struct zwp_primary_selection_offer_v1*          primary_offer;
static int                                             copies, selections;

// A paste that reads from its pipe until the source closes it.
struct paste {
    const char* kind;
    int         fd;
    uint64_t    started;
    char        text[256];
    size_t      length;
};
static struct paste pastes[2] = {{.kind = "clipboard", .fd = -1}, {.kind = "primary", .fd = -1}};

static uint64_t     now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}
static void say(const char* format, ...) {
    printf("%llu ", (unsigned long long)now_ms());
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n');
    fflush(stdout);
}

static void buffer_release(void* data, struct wl_buffer* buffer) {
    wl_buffer_destroy(buffer);
}
static const struct wl_buffer_listener buffer_listener = {.release = buffer_release};
static void                            draw(void) {
    const int width = 200, height = 100;
    size_t    size = (size_t)width * height * 4;
    int       fd   = memfd_create("hyprmasq-clip-probe", MFD_CLOEXEC);
    assert(fd >= 0 && ftruncate(fd, size) == 0);
    uint32_t* pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(pixels != MAP_FAILED);
    for (size_t i = 0; i < size / 4; ++i)
        pixels[i] = 0xff555555;
    struct wl_shm_pool* pool   = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer*   buffer = wl_shm_pool_create_buffer(pool, 0, width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
    wl_buffer_add_listener(buffer, &buffer_listener, NULL);
    wl_shm_pool_destroy(pool);
    munmap(pixels, size);
    close(fd);
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage(surface, 0, 0, width, height);
    wl_surface_commit(surface);
}
static void configure(void* data, struct xdg_surface* xdg, uint32_t serial) {
    static bool ready;
    xdg_surface_ack_configure(xdg, serial);
    draw();
    if (!ready) {
        ready = true;
        say("ready");
    }
}
static const struct xdg_surface_listener  surface_listener = {.configure = configure};
static void                               toplevel_configure(void* data, struct xdg_toplevel* top, int32_t width, int32_t height, struct wl_array* states) {}
static void                               toplevel_close(void* data, struct xdg_toplevel* top) {}
static const struct xdg_toplevel_listener toplevel_listener = {.configure = toplevel_configure, .close = toplevel_close};
static void                               ping(void* data, struct xdg_wm_base* base, uint32_t serial) {
    xdg_wm_base_pong(base, serial);
}
static const struct xdg_wm_base_listener wm_listener = {.ping = ping};

// Its sources write their text, which is their data, and close the descriptor.
static void send_text(const char* text, int32_t fd) {
    say("source send %s", text);
    ssize_t n = write(fd, text, strlen(text));
    (void)n;
    close(fd);
}
static void source_target(void* data, struct wl_data_source* s, const char* mime) {}
static void source_send(void* data, struct wl_data_source* s, const char* mime, int32_t fd) {
    send_text(data, fd);
}
static void source_cancelled(void* data, struct wl_data_source* s) {
    say("source cancelled %s", (char*)data);
    free(data);
    wl_data_source_destroy(s);
}
static const struct wl_data_source_listener source_listener = {.target = source_target, .send = source_send, .cancelled = source_cancelled};
static void                                 primary_send(void* data, struct zwp_primary_selection_source_v1* s, const char* mime, int32_t fd) {
    send_text(data, fd);
}
static void primary_cancelled(void* data, struct zwp_primary_selection_source_v1* s) {
    say("source cancelled %s", (char*)data);
    free(data);
    zwp_primary_selection_source_v1_destroy(s);
}
static const struct zwp_primary_selection_source_v1_listener primary_source_listener = {.send = primary_send, .cancelled = primary_cancelled};

static void                                                  copy(uint32_t serial) {
    char* text = NULL;
    assert(asprintf(&text, "%s-copy%d", title, ++copies) > 0);
    struct wl_data_source* source = wl_data_device_manager_create_data_source(manager);
    wl_data_source_add_listener(source, &source_listener, text);
    wl_data_source_offer(source, MIME);
    wl_data_source_offer(source, "text/plain");
    wl_data_device_set_selection(device, source, serial);
    say("copy %s", text);
}
static void select_text(uint32_t serial) {
    char* text = NULL;
    assert(asprintf(&text, "%s-select%d", title, ++selections) > 0);
    struct zwp_primary_selection_source_v1* source = zwp_primary_selection_device_manager_v1_create_source(primary_manager);
    zwp_primary_selection_source_v1_add_listener(source, &primary_source_listener, text);
    zwp_primary_selection_source_v1_offer(source, MIME);
    zwp_primary_selection_source_v1_offer(source, "text/plain");
    zwp_primary_selection_device_v1_set_selection(primary_device, source, serial);
    say("select %s", text);
}
static void paste(struct paste* p, void* offer) {
    if (p->fd >= 0)
        close(p->fd);
    p->fd = -1;
    if (!offer) {
        say("paste %s none", p->kind);
        return;
    }
    int fds[2];
    assert(pipe2(fds, O_CLOEXEC) == 0);
    if (p == &pastes[0])
        wl_data_offer_receive(offer, MIME, fds[1]);
    else
        zwp_primary_selection_offer_v1_receive(offer, MIME, fds[1]);
    close(fds[1]);
    fcntl(fds[0], F_SETFL, O_NONBLOCK);
    p->fd      = fds[0];
    p->started = now_ms();
    p->length  = 0;
}
static void read_paste(struct paste* p) {
    for (;;) {
        ssize_t n = read(p->fd, p->text + p->length, sizeof(p->text) - 1 - p->length);
        if (n > 0) {
            p->length += n;
            continue;
        }
        if (n < 0)
            return; // EAGAIN: more to come
        break;
    }
    close(p->fd);
    p->fd              = -1;
    p->text[p->length] = 0;
    say("paste %s %s", p->kind, p->text);
}

// The offers it is sent. Each new selection replaces the one before.
static void                                offer_mime(void* data, struct wl_data_offer* offer, const char* mime) {}
static void                                offer_source_actions(void* data, struct wl_data_offer* offer, uint32_t actions) {}
static void                                offer_action(void* data, struct wl_data_offer* offer, uint32_t action) {}
static const struct wl_data_offer_listener offer_listener = {.offer = offer_mime, .source_actions = offer_source_actions, .action = offer_action};
static void                                data_offer(void* data, struct wl_data_device* d, struct wl_data_offer* offer) {
    wl_data_offer_add_listener(offer, &offer_listener, NULL);
}
static void data_enter(void* data, struct wl_data_device* d, uint32_t serial, struct wl_surface* s, wl_fixed_t x, wl_fixed_t y, struct wl_data_offer* offer) {}
static void data_leave(void* data, struct wl_data_device* d) {}
static void data_motion(void* data, struct wl_data_device* d, uint32_t time, wl_fixed_t x, wl_fixed_t y) {}
static void data_drop(void* data, struct wl_data_device* d) {}
static void data_selection(void* data, struct wl_data_device* d, struct wl_data_offer* offer) {
    if (clipboard_offer && clipboard_offer != offer)
        wl_data_offer_destroy(clipboard_offer);
    clipboard_offer = offer;
    say("clipboard %s", offer ? "offer" : "none");
}
static const struct wl_data_device_listener device_listener = {
    .data_offer = data_offer, .enter = data_enter, .leave = data_leave, .motion = data_motion, .drop = data_drop, .selection = data_selection};
static void                                                 primary_offer_mime(void* data, struct zwp_primary_selection_offer_v1* offer, const char* mime) {}
static const struct zwp_primary_selection_offer_v1_listener primary_offer_listener = {.offer = primary_offer_mime};
static void primary_data_offer(void* data, struct zwp_primary_selection_device_v1* d, struct zwp_primary_selection_offer_v1* offer) {
    zwp_primary_selection_offer_v1_add_listener(offer, &primary_offer_listener, NULL);
}
static void primary_selection(void* data, struct zwp_primary_selection_device_v1* d, struct zwp_primary_selection_offer_v1* offer) {
    if (primary_offer && primary_offer != offer)
        zwp_primary_selection_offer_v1_destroy(primary_offer);
    primary_offer = offer;
    say("primary %s", offer ? "offer" : "none");
}
static const struct zwp_primary_selection_device_v1_listener primary_device_listener = {.data_offer = primary_data_offer, .selection = primary_selection};

static void                                                  pointer_enter(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s, wl_fixed_t x, wl_fixed_t y) {}
static void                                                  pointer_leave(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s) {}
static void                                                  pointer_motion(void* data, struct wl_pointer* p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {}
static void                                                  pointer_button(void* data, struct wl_pointer* p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    if (button == BTN_LEFT && state == WL_POINTER_BUTTON_STATE_PRESSED)
        select_text(serial);
}
static void                             pointer_axis(void* data, struct wl_pointer* p, uint32_t time, uint32_t axis, wl_fixed_t value) {}
static const struct wl_pointer_listener pointer_listener = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion, .button = pointer_button, .axis = pointer_axis};
static void keyboard_keymap(void* data, struct wl_keyboard* kb, uint32_t format, int32_t fd, uint32_t size) {
    close(fd);
}
static void keyboard_enter(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s, struct wl_array* keys) {
    say("keyboard enter");
}
static void keyboard_leave(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s) {
    say("keyboard leave");
}
static void keyboard_key(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    if (state != WL_KEYBOARD_KEY_STATE_PRESSED)
        return;
    switch (key) {
        case KEY_C: copy(serial); break;
        case KEY_S: select_text(serial); break;
        case KEY_V: paste(&pastes[0], clipboard_offer); break;
        case KEY_M: paste(&pastes[1], primary_offer); break;
        default: break;
    }
}
static void                              keyboard_modifiers(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {}
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave, .key = keyboard_key, .modifiers = keyboard_modifiers};
static void seat_caps(void* data, struct wl_seat* s, uint32_t caps) {
    static struct wl_pointer*  pointer;
    static struct wl_keyboard* keyboard;
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
static void                          global(void* data, struct wl_registry* registry, uint32_t name, const char* interface, uint32_t version) {
    if (!strcmp(interface, "wl_compositor"))
        compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 1);
    else if (!strcmp(interface, "wl_shm"))
        shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!strcmp(interface, "xdg_wm_base")) {
        wm = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
        xdg_wm_base_add_listener(wm, &wm_listener, NULL);
    } else if (!strcmp(interface, "wl_seat")) {
        seat = wl_registry_bind(registry, name, &wl_seat_interface, 1);
        wl_seat_add_listener(seat, &seat_listener, NULL);
    } else if (!strcmp(interface, "wl_data_device_manager"))
        manager = wl_registry_bind(registry, name, &wl_data_device_manager_interface, 3);
    else if (!strcmp(interface, "zwp_primary_selection_device_manager_v1"))
        primary_manager = wl_registry_bind(registry, name, &zwp_primary_selection_device_manager_v1_interface, 1);
}
static void                              global_remove(void* data, struct wl_registry* registry, uint32_t name) {}
static const struct wl_registry_listener registry_listener = {.global = global, .global_remove = global_remove};

int                                      main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: hyprmasq-clip-probe TITLE\n");
        return 2;
    }
    title   = argv[1];
    display = wl_display_connect(NULL);
    assert(display);
    struct wl_registry* registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    assert(compositor && shm && wm && seat && manager && primary_manager);
    device = wl_data_device_manager_get_data_device(manager, seat);
    wl_data_device_add_listener(device, &device_listener, NULL);
    primary_device = zwp_primary_selection_device_manager_v1_get_device(primary_manager, seat);
    zwp_primary_selection_device_v1_add_listener(primary_device, &primary_device_listener, NULL);
    surface                 = wl_compositor_create_surface(compositor);
    struct xdg_surface* xdg = xdg_wm_base_get_xdg_surface(wm, surface);
    xdg_surface_add_listener(xdg, &surface_listener, NULL);
    struct xdg_toplevel* toplevel = xdg_surface_get_toplevel(xdg);
    xdg_toplevel_add_listener(toplevel, &toplevel_listener, NULL);
    xdg_toplevel_set_title(toplevel, title);
    wl_surface_commit(surface);

    for (;;) {
        struct pollfd fds[3] = {{.fd = wl_display_get_fd(display), .events = POLLIN}, {.fd = pastes[0].fd, .events = POLLIN}, {.fd = pastes[1].fd, .events = POLLIN}};
        while (wl_display_prepare_read(display) != 0)
            wl_display_dispatch_pending(display);
        if (wl_display_flush(display) < 0 || poll(fds, 3, 100) < 0) {
            wl_display_cancel_read(display);
            return 1;
        }
        if (fds[0].revents & POLLIN) {
            if (wl_display_read_events(display) < 0)
                return 1;
        } else
            wl_display_cancel_read(display);
        if (wl_display_dispatch_pending(display) < 0)
            return 1;
        for (int i = 0; i < 2; ++i) {
            struct paste* p = &pastes[i];
            if (p->fd >= 0 && (fds[i + 1].revents & (POLLIN | POLLHUP)))
                read_paste(p);
            if (p->fd >= 0 && now_ms() - p->started >= 3000) {
                close(p->fd);
                p->fd = -1;
                say("paste %s timeout", p->kind);
            }
        }
    }
}
