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
#include "probe-common.h"
#include <fcntl.h>
#include <linux/input-event-codes.h>

#define PAYLOAD "hyprmasq-dnd-payload"

// The serial that a drag starts with, and when the destination finishes a drop, by the options.
static enum {
    SERIAL_PRESS,
    SERIAL_ENTER,
    SERIAL_OLD,
    SERIAL_FORGED
} serial_mode;
static enum {
    FINISH_AT_ONCE,
    FINISH_NEVER,
    FINISH_LATE,
    FINISH_SOON
} finish_mode;

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
static bool                           accept_none, ask, with_icon;
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

static const char* name_of(struct wl_surface* s) {
    if (s && s == agent.surface)
        return agent.name;
    if (s && s == sub)
        return "sub";
    if (s && s == user.surface)
        return user.name;
    return "other";
}

static void fill(struct wl_surface* surface, int width, int height, uint32_t color) {
    wl_surface_attach(surface, shm_buffer(shm, width, height, color, NULL), 0, 0);
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
static const struct xdg_surface_listener surface_listener = {.configure = configure};
static void toplevel_configure(void* data, struct xdg_toplevel* top, int32_t width, int32_t height, struct wl_array* states) {}
static void toplevel_close(void* data, struct xdg_toplevel* top) {}
static const struct xdg_toplevel_listener toplevel_listener = {.configure = toplevel_configure, .close = toplevel_close};
static void create(struct window* w, const char* name, uint32_t color) {
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
static void icon_frame(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener frame_listener = {.done = icon_frame};
static void icon_frame(void* data, struct wl_callback* callback, uint32_t time) {
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
static void data_offer(void* data, struct wl_data_device* d, struct wl_data_offer* offer) {
    wl_data_offer_add_listener(offer, &offer_listener, NULL);
    say("data offer %u", wl_proxy_get_id((struct wl_proxy*)offer));
}
static void data_enter(void* data, struct wl_data_device* d, uint32_t serial, struct wl_surface* surface, wl_fixed_t x, wl_fixed_t y, struct wl_data_offer* offer) {
    entered = offer;
    say("drag enter %s %u %d %d", name_of(surface), offer ? wl_proxy_get_id((struct wl_proxy*)offer) : 0, wl_fixed_to_int(x), wl_fixed_to_int(y));
    if (!offer)
        return;
    wl_data_offer_accept(offer, serial, accept_none ? NULL : "text/plain");
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
    CHECK(pipe2(fds, O_CLOEXEC | O_NONBLOCK) == 0);
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
    switch (finish_mode) {
        case FINISH_AT_ONCE: finish(); break;
        case FINISH_LATE: timer_arm_ms(finish_timer, 1500); break;
        case FINISH_SOON: timer_arm_ms(finish_timer, 300); break;
        case FINISH_NEVER: break;
    }
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
static uint32_t press_serial;
static bool     pressed, waiting_motion;
static void press_frame(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener press_frame_listener = {.done = press_frame};
static void pointer_motion(void* data, struct wl_pointer* p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {
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
    if (serial_mode == SERIAL_OLD && !old_recorded) {
        old_serial   = serial;
        old_recorded = true;
        return;
    }
    switch (serial_mode) {
        case SERIAL_PRESS: break;
        case SERIAL_ENTER: serial = enter_serial; break;
        case SERIAL_OLD: serial = old_serial; break;
        case SERIAL_FORGED: serial = 0x7fffff00; break;
    }
    if (frame_first) {
        press_serial   = serial;
        press_x        = pointer_x;
        press_y        = pointer_y;
        waiting_motion = true;
        return;
    }
    start_drag(serial);
}
static void pointer_axis(void* data, struct wl_pointer* p, uint32_t time, uint32_t axis, wl_fixed_t value) {}
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
static void keyboard_modifiers(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {}
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave, .key = keyboard_key, .modifiers = keyboard_modifiers};
static struct seat_input seat_input = {.pointer_listener = &pointer_listener, .keyboard_listener = &keyboard_listener};

static void usage(void) {
    fprintf(stderr,
            "usage: hyprmasq-dnd-probe TITLE [serial=enter|old|forged] [accept=none] [actions=ask] [finish=never|late|soon] [icon] [source=none] [frame-first] [user NAME]\n");
    exit(2);
}

int main(int argc, char** argv) {
    if (argc < 2)
        usage();
    const char* user_name = NULL;
    for (int i = 2; i < argc; ++i) {
        const char* option = argv[i];
        if (!strcmp(option, "serial=enter"))
            serial_mode = SERIAL_ENTER;
        else if (!strcmp(option, "serial=old"))
            serial_mode = SERIAL_OLD;
        else if (!strcmp(option, "serial=forged"))
            serial_mode = SERIAL_FORGED;
        else if (!strcmp(option, "accept=none"))
            accept_none = true;
        else if (!strcmp(option, "actions=ask"))
            ask = true;
        else if (!strcmp(option, "finish=never"))
            finish_mode = FINISH_NEVER;
        else if (!strcmp(option, "finish=late"))
            finish_mode = FINISH_LATE;
        else if (!strcmp(option, "finish=soon"))
            finish_mode = FINISH_SOON;
        else if (!strcmp(option, "icon"))
            with_icon = true;
        else if (!strcmp(option, "source=none"))
            sourceless = true;
        else if (!strcmp(option, "frame-first"))
            frame_first = true;
        else if (!strcmp(option, "user") && i + 1 < argc)
            user_name = argv[++i];
        else {
            fprintf(stderr, "hyprmasq-dnd-probe: unknown option %s\n", option);
            usage();
        }
    }
    display = wl_display_connect(NULL);
    CHECK(display);
    bind_globals(display,
                 (const struct probe_global[]){{.proxy = &compositor, .interface = &wl_compositor_interface, .version = 1},
                                               {.proxy = &subcompositor, .interface = &wl_subcompositor_interface, .version = 1},
                                               {.proxy = &shm, .interface = &wl_shm_interface, .version = 1},
                                               {.proxy = &wm, .interface = &xdg_wm_base_interface, .version = 1, .listener = &pong_listener},
                                               {.proxy = &seat, .interface = &wl_seat_interface, .version = 1, .listener = &seat_input_listener, .data = &seat_input},
                                               {.proxy = &manager, .interface = &wl_data_device_manager_interface, .version = 3},
                                               {0}});
    device = wl_data_device_manager_get_data_device(manager, seat);
    wl_data_device_add_listener(device, &device_listener, NULL);
    create(&agent, argv[1], 0xff224466);
    sub                              = wl_compositor_create_surface(compositor);
    struct wl_subsurface* subsurface = wl_subcompositor_get_subsurface(subcompositor, sub, agent.surface);
    wl_subsurface_set_position(subsurface, 200, 0);
    wl_subsurface_set_desync(subsurface);
    if (user_name)
        create(&user, user_name, 0xff444444);

    finish_timer      = timer_new();
    const int signals = signals_new(SIGUSR1, SIGUSR2, SIGHUP, 0);
    for (;;) {
        struct pollfd fds[4] = {{0}, {.fd = signals, .events = POLLIN}, {.fd = finish_timer, .events = POLLIN}, {.fd = pipe_read, .events = POLLIN}};
        if (!dispatch_display(display, fds, 4, -1))
            return 1;
        if (fds[1].revents & POLLIN) {
            const uint32_t signal = signal_read(signals);
            if (signal == SIGUSR1 && source) {
                wl_data_source_destroy(source);
                source = NULL;
                say("source destroyed");
            } else if (signal == SIGUSR2 && device) {
                wl_data_device_release(device);
                device = NULL;
                say("device released");
            } else if (signal == SIGHUP && agent.surface) {
                xdg_toplevel_destroy(agent.toplevel);
                xdg_surface_destroy(agent.xdg);
                wl_surface_destroy(sub);
                wl_surface_destroy(agent.surface);
                agent.surface = sub = NULL;
                say("window destroyed");
            }
        }
        if (fds[2].revents & POLLIN) {
            timer_read(finish_timer);
            finish();
        }
        if (pipe_read >= 0 && (fds[3].revents & (POLLIN | POLLHUP)))
            read_payload();
    }
}
