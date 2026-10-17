// VM fixture: a raw Wayland drag-and-drop within one client, which is the drag's source and its destination.
//
//   hyprmasq-dnd-probe TITLE [OPTION]...
//
// It shows a toplevel TITLE of 400×200, blue, with a subsurface over its right half, purple, and with `user NAME` a
// second toplevel NAME on the same connection. A left press on TITLE, not on NAME, starts a drag of the text
// "hyprmasq-dnd-payload" as text/plain, with the actions copy and move, from TITLE's surface. As the destination, it
// accepts text/plain on every enter, sets the actions copy and move with move preferred, and on the drop receives the
// data into a pipe, reads it to the end, checks it, finishes and destroys the offer, and draws TITLE green. Options
// change that:
//   serial=enter|old|forged  start the drag with the serial of the pointer's enter, of the previous left press (the
//                            first press only records it), or a made-up one, instead of the press's
//   accept=none              accept no type
//   actions=ask              offer only the ask action, which it also prefers
//   finish=never|late|soon   never finish, or finish 1500 ms or 300 ms after the data arrived
//   frame-first              start a drag once the pointer has moved 8 pixels from its press, at the first frame
//                            callback after that, if the button is still down, as Firefox does, which renders the
//                            drag's image first
//   icon                     start the drag with an icon surface, 16×16 orange, that asks for frame callbacks
//   source=none              start one drag without a source, which the protocol allows within a client: it then gets
//                            enters without an offer, motion and leaves
//   user NAME                show the second toplevel NAME
// SIGUSR1 destroys the drag's source, SIGUSR2 releases the data device, and SIGHUP destroys TITLE.
//
// It logs every event after a CLOCK_MONOTONIC timestamp in milliseconds, as focus-probe's focus mode does: pointer and
// keyboard enters and leaves, buttons, "drag start SERIAL", the data device's offers, enters, motion, leaves and drops,
// each offer's types and actions, the source's target, action, send, drop performed, finished and cancelled, "payload
// TEXT" once the data arrived, "finish", "icon frame" and "ready" once both windows drew.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include <linux/input-event-codes.h>
#include <assert.h>
#include <fcntl.h>
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

#define PAYLOAD "hyprmasq-dnd-payload"

struct window {
    struct wl_surface*   surface;
    struct xdg_surface*  xdg;
    struct xdg_toplevel* toplevel;
    const char*          name;
    uint32_t             color;
    bool                 drawn;
};
static struct wl_display*             display;
static struct wl_compositor*          compositor;
static struct wl_subcompositor*       subcompositor;
static struct wl_shm*                 shm;
static struct wl_seat*                seat;
static struct xdg_wm_base*            wm;
static struct wl_data_device_manager* manager;
static struct wl_data_device*         device;
static struct window                  agent, user;
static struct wl_surface *            sub, *icon;
static struct wl_data_source*         source;
static struct wl_data_offer *         entered, *dropped;
static const char *                   serial_mode = "press", *accept_mode = "text/plain", *finish_mode = "yes";
static bool                           ask, with_icon;
static uint32_t                       enter_serial, old_serial;
static struct wl_surface*             pointed; // that the pointer is on
static bool                           ready;
static bool                           sourceless, sourceless_started; // with source=none: the drag has no source
static bool                           frame_first;
static double                         pointer_x, pointer_y, press_x, press_y;
static bool                           old_recorded;
static int                            pipe_read = -1, finish_timer = -1;
static char                           received[256];
static size_t                         received_len;

static uint64_t                       now_ms(void) {
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
static const char* name_of(struct wl_surface* s) {
    if (s && s == agent.surface)
        return agent.name;
    if (s && s == sub)
        return "sub";
    if (s && s == user.surface)
        return user.name;
    return "other";
}

static void buffer_release(void* data, struct wl_buffer* buffer) {
    wl_buffer_destroy(buffer);
}
static const struct wl_buffer_listener buffer_listener = {.release = buffer_release};
static void                            fill(struct wl_surface* surface, int width, int height, uint32_t color) {
    size_t size = (size_t)width * height * 4;
    int    fd   = memfd_create("hyprmasq-dnd-probe", MFD_CLOEXEC);
    assert(fd >= 0 && ftruncate(fd, size) == 0);
    uint32_t* pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(pixels != MAP_FAILED);
    for (size_t i = 0; i < size / 4; ++i)
        pixels[i] = color;
    struct wl_shm_pool* pool   = wl_shm_create_pool(shm, fd, size);
    struct wl_buffer*   buffer = wl_shm_pool_create_buffer(pool, 0, width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
    wl_buffer_add_listener(buffer, &buffer_listener, NULL);
    wl_shm_pool_destroy(pool);
    munmap(pixels, size);
    close(fd);
    wl_surface_attach(surface, buffer, 0, 0);
    wl_surface_damage(surface, 0, 0, width, height);
}
static void draw(struct window* w) {
    if (w == &agent && sub) {
        fill(sub, 200, 200, 0xff663399);
        wl_surface_commit(sub);
    }
    fill(w->surface, 400, 200, w->color);
    wl_surface_commit(w->surface);
    w->drawn = true;
    if (agent.drawn && (!user.name || user.drawn) && !ready) {
        ready = true;
        say("ready");
    }
}
static void configure(void* data, struct xdg_surface* surface, uint32_t serial) {
    xdg_surface_ack_configure(surface, serial);
    // A commit after every configure tells Hyprland that the window took its size, which it fills with its fixed
    // buffer from the top left, so that it does not count as resizing.
    draw(data);
}
static const struct xdg_surface_listener  surface_listener = {.configure = configure};
static void                               toplevel_configure(void* data, struct xdg_toplevel* top, int32_t width, int32_t height, struct wl_array* states) {}
static void                               toplevel_close(void* data, struct xdg_toplevel* top) {}
static const struct xdg_toplevel_listener toplevel_listener = {.configure = toplevel_configure, .close = toplevel_close};
static void                               ping(void* data, struct xdg_wm_base* base, uint32_t serial) {
    xdg_wm_base_pong(base, serial);
}
static const struct xdg_wm_base_listener wm_listener = {.ping = ping};
static void                              create(struct window* w, const char* name, uint32_t color) {
    *w         = (struct window){.name = name, .color = color};
    w->surface = wl_compositor_create_surface(compositor);
    w->xdg     = xdg_wm_base_get_xdg_surface(wm, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    w->toplevel = xdg_surface_get_toplevel(w->xdg);
    xdg_toplevel_add_listener(w->toplevel, &toplevel_listener, w);
    xdg_toplevel_set_title(w->toplevel, name);
    wl_surface_commit(w->surface);
}

// The source.
static void source_target(void* data, struct wl_data_source* s, const char* mime) {
    say("source target %s", mime ? mime : "null");
}
static void source_send(void* data, struct wl_data_source* s, const char* mime, int32_t fd) {
    say("source send %s", mime);
    if (write(fd, PAYLOAD, strlen(PAYLOAD)) != (ssize_t)strlen(PAYLOAD))
        say("source write failed");
    close(fd);
}
static void source_cancelled(void* data, struct wl_data_source* s) {
    say("source cancelled");
    wl_data_source_destroy(s);
    if (s == source)
        source = NULL;
}
static void source_drop_performed(void* data, struct wl_data_source* s) {
    say("source drop performed");
}
static void source_finished(void* data, struct wl_data_source* s) {
    say("source finished");
    wl_data_source_destroy(s);
    if (s == source)
        source = NULL;
}
static void source_action(void* data, struct wl_data_source* s, uint32_t action) {
    say("source action %u", action);
}
static const struct wl_data_source_listener source_listener = {.target             = source_target,
                                                               .send               = source_send,
                                                               .cancelled          = source_cancelled,
                                                               .dnd_drop_performed = source_drop_performed,
                                                               .dnd_finished       = source_finished,
                                                               .action             = source_action};

// The icon asks for one frame callback after another, as a toolkit's animated icon does.
static void                              icon_frame(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener frame_listener = {.done = icon_frame};
static void                              icon_frame(void* data, struct wl_callback* callback, uint32_t time) {
    wl_callback_destroy(callback);
    say("icon frame");
    if (!icon)
        return;
    wl_callback_add_listener(wl_surface_frame(icon), &frame_listener, NULL);
    wl_surface_commit(icon);
}

static void start_drag(uint32_t serial) {
    if (sourceless)
        sourceless_started = true;
    else {
        source = wl_data_device_manager_create_data_source(manager);
        wl_data_source_add_listener(source, &source_listener, NULL);
        wl_data_source_offer(source, "text/plain");
        wl_data_source_set_actions(source, WL_DATA_DEVICE_MANAGER_DND_ACTION_COPY | WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE);
    }
    if (with_icon) {
        icon = wl_compositor_create_surface(compositor);
        fill(icon, 16, 16, 0xffcc8800);
    }
    wl_data_device_start_drag(device, source, agent.surface, icon, serial);
    if (icon) {
        // Its content comes with the commit after the request, as a toolkit draws it.
        wl_callback_add_listener(wl_surface_frame(icon), &frame_listener, NULL);
        wl_surface_commit(icon);
    }
    say("drag start %u", serial);
}

// The destination.
static void offer_mime(void* data, struct wl_data_offer* offer, const char* mime) {
    say("offer %u type %s", wl_proxy_get_id((struct wl_proxy*)offer), mime);
}
static void offer_source_actions(void* data, struct wl_data_offer* offer, uint32_t actions) {
    say("offer %u source actions %u", wl_proxy_get_id((struct wl_proxy*)offer), actions);
}
static void offer_action(void* data, struct wl_data_offer* offer, uint32_t action) {
    say("offer %u action %u", wl_proxy_get_id((struct wl_proxy*)offer), action);
}
static const struct wl_data_offer_listener offer_listener = {.offer = offer_mime, .source_actions = offer_source_actions, .action = offer_action};
static void                                data_offer(void* data, struct wl_data_device* d, struct wl_data_offer* offer) {
    wl_data_offer_add_listener(offer, &offer_listener, NULL);
    say("data offer %u", wl_proxy_get_id((struct wl_proxy*)offer));
}
static void data_enter(void* data, struct wl_data_device* d, uint32_t serial, struct wl_surface* surface, wl_fixed_t x, wl_fixed_t y, struct wl_data_offer* offer) {
    entered = offer;
    say("drag enter %s %u %d %d", name_of(surface), offer ? wl_proxy_get_id((struct wl_proxy*)offer) : 0, wl_fixed_to_int(x), wl_fixed_to_int(y));
    if (!offer)
        return;
    wl_data_offer_accept(offer, serial, strcmp(accept_mode, "none") ? accept_mode : NULL);
    if (ask)
        wl_data_offer_set_actions(offer, WL_DATA_DEVICE_MANAGER_DND_ACTION_ASK, WL_DATA_DEVICE_MANAGER_DND_ACTION_ASK);
    else
        wl_data_offer_set_actions(offer, WL_DATA_DEVICE_MANAGER_DND_ACTION_COPY | WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE, WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE);
}
static void data_leave(void* data, struct wl_data_device* d) {
    say("drag leave");
    // An offer that was not dropped is done with.
    if (entered && entered != dropped)
        wl_data_offer_destroy(entered);
    entered = NULL;
}
static void data_motion(void* data, struct wl_data_device* d, uint32_t time, wl_fixed_t x, wl_fixed_t y) {
    say("drag motion %d %d", wl_fixed_to_int(x), wl_fixed_to_int(y));
}
static void data_drop(void* data, struct wl_data_device* d) {
    say("drag drop");
    dropped = entered;
    if (!dropped)
        return;
    int fds[2];
    assert(pipe2(fds, O_CLOEXEC | O_NONBLOCK) == 0);
    wl_data_offer_receive(dropped, "text/plain", fds[1]);
    close(fds[1]);
    pipe_read    = fds[0];
    received_len = 0;
    say("receive %u", wl_proxy_get_id((struct wl_proxy*)dropped));
}
static void data_selection(void* data, struct wl_data_device* d, struct wl_data_offer* offer) {
    if (offer)
        wl_data_offer_destroy(offer);
}
static const struct wl_data_device_listener device_listener = {
    .data_offer = data_offer, .enter = data_enter, .leave = data_leave, .motion = data_motion, .drop = data_drop, .selection = data_selection};
static void finish(void) {
    if (!dropped)
        return;
    wl_data_offer_finish(dropped);
    wl_data_offer_destroy(dropped);
    say("finish");
    dropped     = NULL;
    agent.color = 0xff22aa44;
    draw(&agent);
}
// Reads the data that the source writes into the pipe, without blocking, until it ends.
static void read_payload(void) {
    for (;;) {
        ssize_t n = read(pipe_read, received + received_len, sizeof(received) - 1 - received_len);
        if (n > 0) {
            received_len += n;
            continue;
        }
        if (n < 0)
            return; // EAGAIN: more to come
        break;
    }
    close(pipe_read);
    pipe_read              = -1;
    received[received_len] = 0;
    say("payload %s", received);
    if (!strcmp(finish_mode, "yes"))
        finish();
    else if (!strcmp(finish_mode, "late"))
        timerfd_settime(finish_timer, 0, &(struct itimerspec){.it_value = {.tv_sec = 1, .tv_nsec = 500000000}}, NULL);
    else if (!strcmp(finish_mode, "soon"))
        timerfd_settime(finish_timer, 0, &(struct itimerspec){.it_value = {.tv_nsec = 300000000}}, NULL);
}

static void pointer_enter(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s, wl_fixed_t x, wl_fixed_t y) {
    enter_serial = serial;
    pointed      = s;
    pointer_x    = wl_fixed_to_double(x);
    pointer_y    = wl_fixed_to_double(y);
    say("pointer enter %s", name_of(s));
}
static void pointer_leave(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s) {
    pointed = NULL;
    say("pointer leave %s", name_of(s));
}
// With frame-first, a press starts its drag once the pointer has moved 8 pixels from it, at the next frame callback
// after that, if the button is still down, as Firefox does, which renders the drag's image first.
static uint32_t                          press_serial;
static bool                              pressed, waiting_motion;
static double                            pointer_x, pointer_y, press_x, press_y;
static void                              press_frame(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener press_frame_listener = {.done = press_frame};
static void                              pointer_motion(void* data, struct wl_pointer* p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {
    pointer_x = wl_fixed_to_double(x);
    pointer_y = wl_fixed_to_double(y);
    if (waiting_motion && pressed && (pointer_x - press_x) * (pointer_x - press_x) + (pointer_y - press_y) * (pointer_y - press_y) >= 64) {
        waiting_motion = false;
        wl_callback_add_listener(wl_surface_frame(agent.surface), &press_frame_listener, NULL);
        wl_surface_commit(agent.surface);
        say("drag threshold");
    }
}
static void press_frame(void* data, struct wl_callback* callback, uint32_t time) {
    wl_callback_destroy(callback);
    say("press frame");
    if (pressed)
        start_drag(press_serial);
}
static void pointer_button(void* data, struct wl_pointer* p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    say("button %u %u", button, state);
    if (button == BTN_LEFT)
        pressed = state == WL_POINTER_BUTTON_STATE_PRESSED;
    // A drag may start while an earlier one waits for its finish.
    if (button != BTN_LEFT || state != WL_POINTER_BUTTON_STATE_PRESSED || sourceless_started || (pointed != agent.surface && pointed != sub))
        return;
    if (!strcmp(serial_mode, "old") && !old_recorded) {
        old_serial   = serial;
        old_recorded = true;
        return;
    }
    if (!strcmp(serial_mode, "enter"))
        serial = enter_serial;
    else if (!strcmp(serial_mode, "old"))
        serial = old_serial;
    else if (!strcmp(serial_mode, "forged"))
        serial = 0x7fffff00;
    if (frame_first) {
        press_serial   = serial;
        press_x        = pointer_x;
        press_y        = pointer_y;
        waiting_motion = true;
        return;
    }
    start_drag(serial);
}
static void                             pointer_axis(void* data, struct wl_pointer* p, uint32_t time, uint32_t axis, wl_fixed_t value) {}
static const struct wl_pointer_listener pointer_listener = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion, .button = pointer_button, .axis = pointer_axis};
static void keyboard_keymap(void* data, struct wl_keyboard* kb, uint32_t format, int32_t fd, uint32_t size) {
    close(fd);
}
static void keyboard_enter(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s, struct wl_array* keys) {
    say("keyboard enter %s", name_of(s));
}
static void keyboard_leave(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s) {
    say("keyboard leave %s", name_of(s));
}
static void keyboard_key(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    say("key %u %u", key, state);
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
    else if (!strcmp(interface, "wl_subcompositor"))
        subcompositor = wl_registry_bind(registry, name, &wl_subcompositor_interface, 1);
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
}
static void                              global_remove(void* data, struct wl_registry* registry, uint32_t name) {}
static const struct wl_registry_listener registry_listener = {.global = global, .global_remove = global_remove};

int                                      main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr,
                                                     "usage: hyprmasq-dnd-probe TITLE [serial=enter|old|forged] [accept=none] [actions=ask] [finish=never|late|soon] [icon] [source=none] [frame-first] [user NAME]\n");
        return 2;
    }
    const char* user_name = NULL;
    for (int i = 2; i < argc; ++i) {
        if (!strncmp(argv[i], "serial=", 7))
            serial_mode = argv[i] + 7;
        else if (!strncmp(argv[i], "accept=", 7))
            accept_mode = argv[i] + 7;
        else if (!strcmp(argv[i], "actions=ask"))
            ask = true;
        else if (!strncmp(argv[i], "finish=", 7))
            finish_mode = argv[i] + 7;
        else if (!strcmp(argv[i], "icon"))
            with_icon = true;
        else if (!strcmp(argv[i], "source=none"))
            sourceless = true;
        else if (!strcmp(argv[i], "frame-first"))
            frame_first = true;
        else if (!strcmp(argv[i], "user") && i + 1 < argc)
            user_name = argv[++i];
        else {
            fprintf(stderr, "unknown option %s\n", argv[i]);
            return 2;
        }
    }
    display = wl_display_connect(NULL);
    assert(display);
    struct wl_registry* registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    wl_display_roundtrip(display);
    assert(compositor && subcompositor && shm && wm && seat && manager);
    device = wl_data_device_manager_get_data_device(manager, seat);
    wl_data_device_add_listener(device, &device_listener, NULL);
    create(&agent, argv[1], 0xff224466);
    sub                              = wl_compositor_create_surface(compositor);
    struct wl_subsurface* subsurface = wl_subcompositor_get_subsurface(subcompositor, sub, agent.surface);
    wl_subsurface_set_position(subsurface, 200, 0);
    wl_subsurface_set_desync(subsurface);
    if (user_name)
        create(&user, user_name, 0xff444444);

    finish_timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    sigset_t mask;
    sigemptyset(&mask);
    sigaddset(&mask, SIGUSR1);
    sigaddset(&mask, SIGUSR2);
    sigaddset(&mask, SIGHUP);
    assert(sigprocmask(SIG_BLOCK, &mask, NULL) == 0);
    int signals = signalfd(-1, &mask, SFD_CLOEXEC);
    assert(signals >= 0 && finish_timer >= 0);
    for (;;) {
        struct pollfd fds[4] = {
            {.fd = wl_display_get_fd(display), .events = POLLIN}, {.fd = signals, .events = POLLIN}, {.fd = finish_timer, .events = POLLIN}, {.fd = pipe_read, .events = POLLIN}};
        while (wl_display_prepare_read(display) != 0)
            wl_display_dispatch_pending(display);
        if (wl_display_flush(display) < 0 || poll(fds, 4, -1) < 0) {
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
            struct signalfd_siginfo info;
            assert(read(signals, &info, sizeof(info)) == sizeof(info));
            if (info.ssi_signo == SIGUSR1 && source) {
                wl_data_source_destroy(source);
                source = NULL;
                say("source destroyed");
            } else if (info.ssi_signo == SIGUSR2 && device) {
                wl_data_device_release(device);
                device = NULL;
                say("device released");
            } else if (info.ssi_signo == SIGHUP && agent.surface) {
                xdg_toplevel_destroy(agent.toplevel);
                xdg_surface_destroy(agent.xdg);
                wl_surface_destroy(sub);
                wl_surface_destroy(agent.surface);
                agent.surface = sub = NULL;
                say("window destroyed");
            }
        }
        if (fds[2].revents & POLLIN) {
            uint64_t expirations;
            assert(read(finish_timer, &expirations, sizeof(expirations)) == sizeof(expirations));
            finish();
        }
        if (pipe_read >= 0 && (fds[3].revents & (POLLIN | POLLHUP)))
            read_payload();
    }
}
