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
// for one toplevel, destroys it when Escape or the left button goes down on it, and
// goes on logging the keys that still arrive. With `hold SOCKET X Y stop` or
// `hold SOCKET X Y disconnect`, it moves the pointer to X,Y as a libei sender,
// presses Left Shift, A and the left button, and
// then either stops emulating and disconnects a second later, or exits at once,
// releasing nothing. With `hold SOCKET X Y signal`, it prints "ready" once it has
// its devices, and on SIGUSR1 presses them and exits at once, without waiting for
// the plugin. With `resize TITLE hold` or `resize TITLE ack`, it shows one toplevel
// that acks a configure that keeps its drawn size without a commit, as a client may
// that has nothing new to draw. A SIGUSR1 makes it hold back configures with another
// size: with hold, it neither acks nor draws them, with ack, it acks them at once but
// does not draw. The next SIGUSR1 answers the last one held back, acking it with hold,
// and drawing it at its size. Its right edge is green, its rest like the other modes.
// With `frames TITLE N`, it shows one toplevel that draws only in answer to frame
// callbacks, as Firefox and Thunderbird do on a hidden workspace: its first frame is
// blank, in the color of the other modes, and it draws its content, orange, at the
// Nth frame callback, asking for each one before with a commit that draws nothing.
// With N 0, it never draws again, and keeps asking. It waits for one frame callback
// at a time, also when it draws, so each one counts once.
// With `opener TITLE [TITLE]`, it logs like the focus mode and opens windows on
// input to one of its toplevels, which it redraws in another color: N or a left
// click opens a toplevel without a parent, TITLE-newK, which is blank until its first
// frame callback, then green, and logs "content", D or a right click one with
// that toplevel as its parent, TITLE-dialogK, L a toplevel without a parent 300 ms
// later, TITLE-lateK, K one 1500 ms later, TITLE-laterK, and a middle click a dialog
// 1500 ms later, TITLE-late-dialogK.
// F starts another process, `imported HANDLE TITLE-foreignK`, which opens a
// toplevel and names the toplevel that got the key as its parent through
// xdg-foreign, after its initial commit, as a portal's file chooser does; a
// SIGUSR1 starts one for the first toplevel without input. That process leaves
// the probe's process tree, as a portal is no child of the app. S in a toplevel
// that the probe opened closes it and opens a toplevel without a parent 300 ms
// later, NAME-after, as a mail client's progress window after Send. A right
// click in a toplevel that the probe opened closes it at once, as a dialog's
// button does, and Enter redraws it in another color and closes it 300 ms
// later, as a file chooser does once it has taken the path. It logs "opened"
// when it creates a toplevel and "mapped" when it draws its first frame, and
// destroys one that is asked to close.
// With `answer TITLE MS`, it shows one toplevel that answers a left click at once by
// drawing itself dark gray, as a button that is pressed, and MS milliseconds later
// asks for a frame callback and draws its answer, blue, only when that comes, as a
// browser draws the page that loaded, or a mail client a search's results, on a
// hidden workspace. It logs "pressed" and "answered".
// With `shortcuts TITLE TITLE MS`, it logs like the focus mode for two toplevels that, as Chromium's, take the toplevel
// with the keyboard focus for the active one, and run a shortcut, a key pressed while Ctrl or Alt is held, only after
// the page has let it go by: MS milliseconds after the key, in the toplevel that got it, if that is still active. It
// then logs "shortcut TITLE MODS KEY" and redraws that toplevel in another color, and otherwise logs "shortcut TITLE
// MODS KEY inactive" and does nothing.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "xdg-foreign-unstable-v2-client-protocol.h"
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
#include <sys/timerfd.h>
#include <time.h>
#include <unistd.h>

struct window {
    struct wl_surface *surface;
    struct xdg_surface *xdg;
    struct xdg_toplevel *toplevel;
    int width, height;
    const char *name;
    uint32_t color;
    bool drawn;
    bool hungry; // draws its content only in answer to a frame callback
};
static struct wl_display *display;
static struct wl_compositor *compositor;
static struct wl_shm *shm;
static struct wl_seat *seat;
static struct xdg_wm_base *wm;
static struct wl_data_device_manager *data_manager;
static struct wl_data_device *data_device;
static struct zxdg_exporter_v2 *exporter;
static struct zxdg_importer_v2 *importer;
static struct window windows[3];
static struct wl_surface *pointer_surface, *keyboard_surface;
static bool requested;
static bool placed;
static bool stamped;
static bool close_mode;
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

// Opener mode: the toplevels it opened, the one that opens a late toplevel when its timer expires, and the
// xdg-foreign handles of its first toplevels.
static bool opener_mode;
static struct window opened[16];
static int opened_count, foreign_count, late_timer = -1, close_timer = -1;
static struct window *late_from, *closing; // closing: the toplevel that close_timer closes
static const char *late_kind;
static bool late_parented;
static char *late_after; // the name of the toplevel to open, when a toplevel closed for an S
static char *handles[2];

static bool           answer_mode;
static void           answer_click(void);

// Shortcuts mode: the modifiers last sent, and the shortcut that waits for its time.
static bool               shortcut_mode;
static int                shortcut_timer = -1, shortcut_ms;
static uint32_t           mods_depressed, shortcut_mods, shortcut_key;
static struct wl_surface *shortcut_surface;
static void               shortcut_key_pressed(uint32_t key);

static const char *surface_name(struct wl_surface *s) {
    for (size_t i = 0; i < 3; ++i)
        if (windows[i].surface == s) return windows[i].name;
    for (int i = 0; i < opened_count; ++i)
        if (opened[i].surface == s) return opened[i].name;
    return "unknown";
}
static void buffer_release(void *data, struct wl_buffer *buffer) { wl_buffer_destroy(buffer); }
static const struct wl_buffer_listener buffer_listener = {.release = buffer_release};
// Resize mode: a window that holds back configures with a new size, and what it last drew.
static bool resize_mode, ack_at_once, holding, held;
static uint32_t held_serial;
static int drawn_width, drawn_height;
static void draw(struct window *w);
// Frames mode: a window that draws its content only at the Nth frame callback.
static bool frames_mode, content_drawn, frame_requested;
static int frames_needed, frames_got;
static void frame_done(void *data, struct wl_callback *callback, uint32_t time);
static const struct wl_callback_listener frame_listener = {.done = frame_done};
static void request_frame(struct window *w) {
    if (frame_requested) return;
    frame_requested = true;
    struct wl_callback *callback = wl_surface_frame(w->surface);
    wl_callback_add_listener(callback, &frame_listener, w);
}
static void frame_done(void *data, struct wl_callback *callback, uint32_t time) {
    struct window *w = data;
    wl_callback_destroy(callback);
    frame_requested = false;
    printf("frame %d\n", ++frames_got);
    if (content_drawn) return;
    if (frames_needed == 0 || frames_got < frames_needed) {
        request_frame(w);
        wl_surface_commit(w->surface);
        return;
    }
    content_drawn = true;
    w->color = 0xffdd7711;
    draw(w);
}
// It draws a configure only when the size changed, so that it commits nothing else.
static void frames_configure(struct window *w, uint32_t serial) {
    xdg_surface_ack_configure(w->xdg, serial);
    if (w->width != drawn_width || w->height != drawn_height) draw(w);
}
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
    if (frames_mode) {
        frames_configure(w, serial);
        return;
    }
    xdg_surface_ack_configure(surface, serial);
    draw(w);
}
static void content_frame(void *data, struct wl_callback *callback, uint32_t time);
static const struct wl_callback_listener content_listener = {.done = content_frame};
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
    if (frames_mode) request_frame(w);
    if (w->hungry) wl_callback_add_listener(wl_surface_frame(w->surface), &content_listener, w);
    wl_surface_commit(w->surface);
    if (resize_mode && w == &windows[0]) {
        drawn_width = w->width;
        drawn_height = w->height;
        printf("drew %d %d\n", w->width, w->height);
    } else if (frames_mode) {
        drawn_width = w->width;
        drawn_height = w->height;
        printf("drew %s %d %d\n", content_drawn ? "content" : "blank", w->width, w->height);
    } else
        printf("configured %s\n", w->name);
    if (opener_mode && !w->drawn) say("mapped %s", w->name);
    w->drawn = true;
}
static const struct xdg_surface_listener surface_listener = {.configure = configure};
static void toplevel_configure(void *data, struct xdg_toplevel *top, int32_t width, int32_t height, struct wl_array *states) {
    struct window *w = data;
    if (width > 0) w->width = width;
    if (height > 0) w->height = height;
}
static void destroy_opened(struct window *w) {
    xdg_toplevel_destroy(w->toplevel);
    xdg_surface_destroy(w->xdg);
    wl_surface_destroy(w->surface);
    w->toplevel = NULL;
    w->surface = NULL;
    say("destroyed %s", w->name);
}
static void toplevel_close(void *data, struct xdg_toplevel *top) {
    struct window *w = data;
    if (w < opened || w >= opened + opened_count) exit(0);
    destroy_opened(w);
}
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
static void opener_input(struct wl_surface *s, char action);
static void close_and_follow(struct wl_surface *s);
static void close_opened(struct wl_surface *s, bool later);
// Close mode: the window goes away at once, as a dialog does on the button that closes it.
static void close_window(void) {
    if (!close_mode || !windows[0].toplevel)
        return;
    xdg_toplevel_destroy(windows[0].toplevel);
    xdg_surface_destroy(windows[0].xdg);
    wl_surface_destroy(windows[0].surface);
    windows[0] = (struct window){.name = windows[0].name};
    wl_display_flush(display);
    say("closed");
}
static void pointer_button(void *data, struct wl_pointer *p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    say("button %s %u", surface_name(pointer_surface), state);
    if (pointer_surface == windows[0].surface && state == WL_POINTER_BUTTON_STATE_PRESSED && !requested) request_grabs(serial);
    if (pointer_surface == windows[0].surface && button == BTN_LEFT && state == WL_POINTER_BUTTON_STATE_PRESSED)
        close_window();
    if (answer_mode && pointer_surface == windows[0].surface && button == BTN_LEFT && state == WL_POINTER_BUTTON_STATE_PRESSED)
        answer_click();
    if (opener_mode && state == WL_POINTER_BUTTON_STATE_PRESSED && (button == BTN_LEFT || button == BTN_RIGHT || button == BTN_MIDDLE))
        opener_input(pointer_surface, button == BTN_LEFT ? 'n' : button == BTN_RIGHT ? 'd' : 'm');
    if (opener_mode && state == WL_POINTER_BUTTON_STATE_PRESSED && button == BTN_RIGHT) close_opened(pointer_surface, false);
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
    if (shortcut_mode && state == WL_KEYBOARD_KEY_STATE_PRESSED) shortcut_key_pressed(key);
    if (opener_mode && state == WL_KEYBOARD_KEY_STATE_PRESSED && (key == KEY_N || key == KEY_D || key == KEY_L || key == KEY_K || key == KEY_F))
        opener_input(keyboard_surface, key == KEY_N ? 'n' : key == KEY_D ? 'd' : key == KEY_L ? 'l' : key == KEY_K ? 'k' : 'f');
    if (opener_mode && state == WL_KEYBOARD_KEY_STATE_PRESSED && key == KEY_S) close_and_follow(keyboard_surface);
    if (opener_mode && state == WL_KEYBOARD_KEY_STATE_PRESSED && key == KEY_ENTER) close_opened(keyboard_surface, true);
    if (key == KEY_ESC && state == WL_KEYBOARD_KEY_STATE_PRESSED)
        close_window();
}
static void keyboard_modifiers(void *data, struct wl_keyboard *kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {
    mods_depressed = dep;
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
    else if (!strcmp(interface, "zxdg_exporter_v2")) exporter = wl_registry_bind(registry, name, &zxdg_exporter_v2_interface, 1);
    else if (!strcmp(interface, "zxdg_importer_v2")) importer = wl_registry_bind(registry, name, &zxdg_importer_v2_interface, 1);
}
static void global_remove(void *data, struct wl_registry *registry, uint32_t name) {}
static const struct wl_registry_listener registry_listener = {.global = global, .global_remove = global_remove};
static void create_toplevel_with(struct window *w, const char *name, struct xdg_toplevel *parent, uint32_t color) {
    *w = (struct window){.width = 250, .height = 200, .name = name, .color = color};
    w->surface = wl_compositor_create_surface(compositor);
    w->xdg = xdg_wm_base_get_xdg_surface(wm, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    w->toplevel = xdg_surface_get_toplevel(w->xdg);
    xdg_toplevel_add_listener(w->toplevel, &toplevel_listener, w);
    xdg_toplevel_set_title(w->toplevel, w->name);
    if (parent) xdg_toplevel_set_parent(w->toplevel, parent);
    wl_surface_commit(w->surface);
}
static void create_toplevel(struct window *w, const char *name) { create_toplevel_with(w, name, NULL, 0xff224466); }
// A hungry toplevel draws green at its first frame callback.
static void content_frame(void *data, struct wl_callback *callback, uint32_t time) {
    struct window *w = data;
    wl_callback_destroy(callback);
    if (!w->hungry) return;
    w->hungry = false;
    w->color = 0xff22aa44;
    draw(w);
    say("content %s", w->name);
}
// Opens a green toplevel named after the toplevel that got the input, with that one as its parent if parented. One
// without a parent is blank until its first frame callback, as a mail client's compose window on a hidden workspace.
static void open_toplevel(struct window *from, const char *kind, bool parented) {
    if (opened_count == (int)(sizeof(opened) / sizeof(opened[0]))) return;
    char *name;
    assert(asprintf(&name, "%s-%s%d", from->name, kind, opened_count + 1) > 0);
    struct window *w = &opened[opened_count++];
    create_toplevel_with(w, name, parented ? from->toplevel : NULL, parented ? 0xff22aa44 : 0xff224466);
    w->hungry = !parented;
    say("opened %s", name);
}
static void exported_handle(void *data, struct zxdg_exported_v2 *exported, const char *handle) { *(char **)data = strdup(handle); }
static const struct zxdg_exported_v2_listener exported_listener = {.handle = exported_handle};
// Starts another process that opens a toplevel with this one as its parent, through xdg-foreign.
static void open_foreign(struct window *from) {
    const char *handle = handles[from - windows];
    if (!handle) {
        say("no handle for %s", from->name);
        return;
    }
    char *name;
    assert(asprintf(&name, "%s-foreign%d", from->name, ++foreign_count) > 0);
    if (fork() == 0) {
        if (fork() == 0) {
            execl("/proc/self/exe", "hyprmasq-focus-probe", "imported", handle, name, (char *)NULL);
            _exit(127);
        }
        _exit(0);
    }
}
// Closes a toplevel that the probe opened, and opens another one 300 ms later, when the first is gone.
static void close_and_follow(struct wl_surface *s) {
    for (int i = 0; s && i < opened_count; ++i) {
        if (opened[i].surface != s) continue;
        assert(asprintf(&late_after, "%s-after", opened[i].name) > 0);
        destroy_opened(&opened[i]);
        wl_display_flush(display);
        struct itimerspec in = {.it_value = {.tv_nsec = 300 * 1000000}};
        assert(timerfd_settime(late_timer, 0, &in, NULL) == 0);
        return;
    }
}
// Closes a toplevel that the probe opened: at once, or after redrawing it in another color, 300 ms later.
static void close_opened(struct wl_surface *s, bool later) {
    for (int i = 0; s && i < opened_count; ++i) {
        if (opened[i].surface != s) continue;
        if (!later) {
            destroy_opened(&opened[i]);
            wl_display_flush(display);
            return;
        }
        if (close_timer < 0) return;
        opened[i].color = opened[i].color == 0xff22aa44 ? 0xff7722aa : 0xff22aa44;
        draw(&opened[i]);
        closing = &opened[i];
        struct itimerspec in = {.it_value = {.tv_nsec = 300 * 1000000}};
        assert(timerfd_settime(close_timer, 0, &in, NULL) == 0);
        return;
    }
}
// Input to one of the first toplevels redraws it in another color, so it draws something, and opens a toplevel.
static void opener_input(struct wl_surface *s, char action) {
    struct window *from = s == windows[0].surface ? &windows[0] : s && s == windows[1].surface ? &windows[1] : NULL;
    if (!from) return;
    from->color = from->color == 0xff224466 ? 0xff664422 : 0xff224466;
    draw(from);
    if (action == 'l' || action == 'k' || action == 'm') {
        late_from = from;
        late_kind = action == 'l' ? "late" : action == 'k' ? "later" : "late-dialog";
        late_parented = action == 'm';
        struct itimerspec in = {.it_value = {.tv_sec = action == 'l' ? 0 : 1, .tv_nsec = (action == 'l' ? 300 : 500) * 1000000}};
        assert(timerfd_settime(late_timer, 0, &in, NULL) == 0);
    } else if (action == 'f')
        open_foreign(from);
    else
        open_toplevel(from, action == 'n' ? "new" : "dialog", action == 'd');
}
// Answer mode: the timer after a click, and the frame callback that the answer waits for.
static int  answer_ms, answer_timer = -1;

static void answer_frame(void* data, struct wl_callback* callback, uint32_t time) {
    struct window* w = data;
    wl_callback_destroy(callback);
    w->color = 0xff2255cc;
    draw(w);
    say("answered");
}
static const struct wl_callback_listener answer_listener = {.done = answer_frame};

static void                              answer_click(void) {
    windows[0].color = 0xff333333;
    draw(&windows[0]);
    say("pressed");
    struct itimerspec in = {.it_value = {.tv_sec = answer_ms / 1000, .tv_nsec = (long)(answer_ms % 1000) * 1000000}};
    assert(timerfd_settime(answer_timer, 0, &in, NULL) == 0);
}
static int run_answer(void) {
    answer_timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    assert(answer_timer >= 0);
    struct pollfd fds[2] = {{.fd = wl_display_get_fd(display), .events = POLLIN}, {.fd = answer_timer, .events = POLLIN}};
    for (;;) {
        while (wl_display_prepare_read(display) != 0)
            wl_display_dispatch_pending(display);
        if (wl_display_flush(display) < 0 || poll(fds, 2, -1) < 0) {
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
        if (fds[1].revents & POLLIN) {
            uint64_t expirations;
            assert(read(answer_timer, &expirations, sizeof(expirations)) == sizeof(expirations));
            wl_callback_add_listener(wl_surface_frame(windows[0].surface), &answer_listener, &windows[0]);
            wl_surface_commit(windows[0].surface);
        }
    }
}
// Ctrl is mask 4 and Alt mask 8 in the default keymap. The modifier keys themselves start no shortcut.
static void shortcut_key_pressed(uint32_t key) {
    const uint32_t mods = mods_depressed & (4 | 8);
    if (!mods || !keyboard_surface || key == KEY_LEFTCTRL || key == KEY_RIGHTCTRL || key == KEY_LEFTALT || key == KEY_RIGHTALT ||
        key == KEY_LEFTSHIFT || key == KEY_RIGHTSHIFT || key == KEY_LEFTMETA || key == KEY_RIGHTMETA)
        return;
    shortcut_surface = keyboard_surface;
    shortcut_mods = mods;
    shortcut_key = key;
    struct itimerspec at = {.it_value = {.tv_sec = shortcut_ms / 1000, .tv_nsec = (long)(shortcut_ms % 1000) * 1000000 + 1}};
    assert(timerfd_settime(shortcut_timer, 0, &at, NULL) == 0);
}
static void run_shortcut(void) {
    struct window *w = NULL;
    for (size_t i = 0; i < 2; ++i)
        if (windows[i].surface == shortcut_surface) w = &windows[i];
    if (!w) return;
    const bool active = keyboard_surface == shortcut_surface;
    say("shortcut %s %u %u%s", w->name, shortcut_mods, shortcut_key, active ? "" : " inactive");
    if (active) {
        w->color = w->color == 0xffaa22aa ? 0xff2244aa : 0xffaa22aa;
        draw(w);
    }
    shortcut_surface = NULL;
}
static int run_shortcuts(void) {
    shortcut_timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    assert(shortcut_timer >= 0);
    struct pollfd fds[2] = {{.fd = wl_display_get_fd(display), .events = POLLIN}, {.fd = shortcut_timer, .events = POLLIN}};
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
            uint64_t expirations;
            assert(read(shortcut_timer, &expirations, sizeof(expirations)) == sizeof(expirations));
            run_shortcut();
        }
    }
}
static int run_opener(void) {
    late_timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    close_timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    assert(late_timer >= 0 && close_timer >= 0);
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGUSR1);
    assert(sigprocmask(SIG_BLOCK, &mask, NULL) == 0);
    int signals = signalfd(-1, &mask, SFD_CLOEXEC);
    assert(signals >= 0);
    struct pollfd fds[4] = {{.fd = wl_display_get_fd(display), .events = POLLIN},
                            {.fd = late_timer, .events = POLLIN},
                            {.fd = signals, .events = POLLIN},
                            {.fd = close_timer, .events = POLLIN}};
    for (;;) {
        while (wl_display_prepare_read(display) != 0) wl_display_dispatch_pending(display);
        if (wl_display_flush(display) < 0 || poll(fds, 4, -1) < 0) {
            wl_display_cancel_read(display);
            return 1;
        }
        if (fds[0].revents & POLLIN) {
            if (wl_display_read_events(display) < 0) return 1;
        } else
            wl_display_cancel_read(display);
        if (wl_display_dispatch_pending(display) < 0) return 1;
        if (fds[1].revents & POLLIN) {
            uint64_t expirations;
            assert(read(late_timer, &expirations, sizeof(expirations)) == sizeof(expirations));
            if (late_after) {
                create_toplevel_with(&opened[opened_count++], late_after, NULL, 0xff22aa44);
                say("opened %s", late_after);
                late_after = NULL;
            } else
                open_toplevel(late_from, late_kind, late_parented);
        }
        if (fds[2].revents & POLLIN) {
            struct signalfd_siginfo info;
            assert(read(signals, &info, sizeof(info)) == sizeof(info));
            open_foreign(&windows[0]);
        }
        if (fds[3].revents & POLLIN) {
            uint64_t expirations;
            assert(read(close_timer, &expirations, sizeof(expirations)) == sizeof(expirations));
            if (closing && closing->surface) destroy_opened(closing);
            closing = NULL;
        }
    }
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
    if (argc == 4 && !strcmp(argv[1], "frames")) {
        frames_mode = requested = true; // clicks request nothing
        frames_needed = atoi(argv[3]);
        create_toplevel(&windows[0], argv[2]);
        while (wl_display_dispatch(display) >= 0) {}
        return 1;
    }
    if (argc == 4 && !strcmp(argv[1], "answer")) {
        stamped = answer_mode = requested = true; // clicks request nothing
        create_toplevel(&windows[0], argv[2]);
        answer_ms = atoi(argv[3]);
        return run_answer();
    }
    if (argc == 3 && !strcmp(argv[1], "close")) {
        stamped = close_mode = true;
        requested = true;
        create_toplevel(&windows[0], argv[2]);
        while (wl_display_dispatch(display) >= 0) {}
        return 1;
    }
    if ((argc == 3 || argc == 4) && !strcmp(argv[1], "opener")) {
        stamped = opener_mode = true;
        requested = true; // clicks request nothing
        signal(SIGCHLD, SIG_IGN);
        create_toplevel(&windows[0], argv[2]);
        if (argc == 4) create_toplevel(&windows[1], argv[3]);
        for (int i = 0; exporter && i < argc - 2; ++i)
            zxdg_exported_v2_add_listener(zxdg_exporter_v2_export_toplevel(exporter, windows[i].surface), &exported_listener, &handles[i]);
        return run_opener();
    }
    if (argc == 4 && !strcmp(argv[1], "imported")) {
        stamped = opener_mode = true;
        requested = true;
        assert(importer);
        // The parent follows the initial commit, so Hyprland learns it after it created the window, before it maps.
        create_toplevel_with(&opened[opened_count++], argv[3], NULL, 0xff22aa44);
        say("opened %s", argv[3]);
        zxdg_imported_v2_set_parent_of(zxdg_importer_v2_import_toplevel(importer, argv[2]), opened[0].surface);
        while (wl_display_dispatch(display) >= 0) {}
        return 1;
    }
    if (argc == 5 && !strcmp(argv[1], "shortcuts")) {
        stamped = shortcut_mode = true;
        requested = true; // clicks request nothing
        shortcut_ms = atoi(argv[4]);
        create_toplevel(&windows[0], argv[2]);
        create_toplevel(&windows[1], argv[3]);
        return run_shortcuts();
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
