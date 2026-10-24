// VM fixture: a Wayland client with toplevels on one connection, or in the regions and hold modes a libei sender, that
// the VM test drives in one of the modes of modes[] at the end of this file. Without arguments it lists them.
//
//   hyprmasq-focus-probe MODE [ARGUMENT]...
//
// Its toplevels are 250×200 and blue, 224466, unless their mode says otherwise. It logs its events to stdout, one per
// line. say() starts its lines with a CLOCK_MONOTONIC timestamp in milliseconds in the modes whose table entry is
// stamped; the lines that the drawing and the guard mode's requests print never have one. Each mode's section says
// what it does.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "xdg-foreign-unstable-v2-client-protocol.h"
#include "probe-common.h"
#include <libei.h>
#include <linux/input-event-codes.h>

#define BLUE   0xff224466
#define GREEN  0xff22aa44
#define ORANGE 0xffcc8800

enum mode {
    MODE_GUARD,
    MODE_POPUP,
    MODE_REGIONS,
    MODE_FOCUS,
    MODE_CLOSE,
    MODE_HOLD,
    MODE_RESIZE,
    MODE_FRAMES,
    MODE_OPENER,
    MODE_BROWSER,
    MODE_IMPORTED,
    MODE_ANSWER,
    MODE_MENU,
    MODE_SHORTCUTS,
    MODE_SHIFT,
    MODE_TILES,
    MODE_COUNT,
};
static enum mode mode;

// A mode as main() finds it by name in modes[].
struct mode_info {
    const char* name;
    const char* arguments;
    const char* description;
    int         min_arguments, max_arguments;
    bool        stamped; // say() stamps its lines
    bool        grabs;   // the first press in the first toplevel requests grabs
    // Paints the first toplevel over its color.
    void (*paint)(uint32_t* pixels, int width, int height);
    // Runs the mode, or returns -1 for arguments that it does not take.
    int (*run)(char** arguments, int count);
};
static const struct mode_info modes[MODE_COUNT];

struct window {
    struct wl_surface*   surface;
    struct xdg_surface*  xdg;
    struct xdg_toplevel* toplevel;
    int                  width, height;
    const char*          name;
    uint32_t             color;
    bool                 drawn;
    bool                 hungry; // draws its content only in answer to a frame callback
    bool                 held;   // draws its first frame only when the browser mode lets it
    struct wl_shm*       shm;    // of its connection, if not the first
};
static struct wl_display*             display;
static struct wl_compositor*          compositor;
static struct wl_shm*                 shm;
static struct wl_seat*                seat;
static struct xdg_wm_base*            wm;
static struct wl_data_device_manager* data_manager;
static struct wl_data_device*         data_device;
static struct zxdg_exporter_v2*       exporter;
static struct zxdg_importer_v2*       importer;
static struct window                  windows[3]; // two toplevels and the popup
static struct wl_surface *            pointer_surface, *keyboard_surface;
static bool                           grabs_requested; // or never to be, in all modes but guard and popup

static void connect_display(void);
static void create_toplevel(struct window* w, const char* name, struct xdg_toplevel* parent, uint32_t color);
static void setup_toplevel(struct window* w, struct wl_compositor* on, struct xdg_wm_base* base, struct xdg_toplevel* parent);
static void draw(struct window* w);
static void watch(int fd, void (*ready)(int fd));
static int run_loop(void);
static bool opens_windows(void);

// Popup mode: one toplevel opens a popup on the first SIGUSR1 and destroys it on the second, without any input, so the
// popup's commits are the only ones of the window. With a geometry, the popup lies at X,Y of the window with that size,
// without constraint adjustment, so it can reach outside the window. GX,GY give the toplevel a margin of that size above
// and left of its xdg window geometry, as client-side shadows do. Pointer events are logged with the surface they reach.
static bool popup_placed;
static int  popup_x, popup_y, popup_width = 40, popup_height = 40;
static int  margin_x, margin_y;

static void popup_configure(void* data, struct xdg_popup* popup, int32_t x, int32_t y, int32_t w, int32_t h) {
    printf("popup at %d %d %d %d\n", x, y, w, h);
}
static void popup_done(void* data, struct xdg_popup* popup) {
    puts("popup done");
}
static const struct xdg_popup_listener   popup_listener = {.configure = popup_configure, .popup_done = popup_done};
static const struct xdg_surface_listener surface_listener;

// A red popup of the first toplevel, uncommitted.
static struct xdg_popup* open_popup(void) {
    struct window* w = &windows[2];
    *w               = (struct window){.width = popup_width, .height = popup_height, .name = "popup", .color = 0xffcc2222};
    w->surface       = wl_compositor_create_surface(compositor);
    w->xdg           = xdg_wm_base_get_xdg_surface(wm, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    struct xdg_positioner* positioner = xdg_wm_base_create_positioner(wm);
    xdg_positioner_set_size(positioner, popup_width, popup_height);
    if (popup_placed) {
        xdg_positioner_set_anchor_rect(positioner, 0, 0, 1, 1);
        xdg_positioner_set_anchor(positioner, XDG_POSITIONER_ANCHOR_TOP_LEFT);
        xdg_positioner_set_gravity(positioner, XDG_POSITIONER_GRAVITY_BOTTOM_RIGHT);
        xdg_positioner_set_offset(positioner, popup_x, popup_y);
        xdg_positioner_set_constraint_adjustment(positioner, XDG_POSITIONER_CONSTRAINT_ADJUSTMENT_NONE);
    } else
        xdg_positioner_set_anchor_rect(positioner, 0, 0, 10, 10);
    struct xdg_popup* popup = xdg_surface_get_popup(w->xdg, windows[0].xdg, positioner);
    xdg_positioner_destroy(positioner);
    xdg_popup_add_listener(popup, &popup_listener, NULL);
    return popup;
}
static void popup_signalled(int signals) {
    static struct xdg_popup* popup;
    signal_read(signals);
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
static int run_popup(char** arguments, int count) {
    if (count != 1 && count != 5 && count != 7)
        return -1;
    if (count >= 5) {
        popup_placed = true;
        popup_x      = parse_int(arguments[1], "X", INT32_MIN, INT32_MAX);
        popup_y      = parse_int(arguments[2], "Y", INT32_MIN, INT32_MAX);
        popup_width  = parse_int(arguments[3], "WIDTH", 1, 4096);
        popup_height = parse_int(arguments[4], "HEIGHT", 1, 4096);
    }
    if (count == 7) {
        margin_x = parse_int(arguments[5], "GX", 0, 4096);
        margin_y = parse_int(arguments[6], "GY", 0, 4096);
    }
    connect_display();
    watch(signals_new(SIGUSR1, 0), popup_signalled);
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// Guard mode: two toplevels, hm-guard-hidden and hm-guard-visible. The first press in the first toplevel, as in the
// popup mode, requests a move, a resize, a popup grab and a drag with its serial.
static void source_target(void* data, struct wl_data_source* source, const char* mime) {}
static void source_send(void* data, struct wl_data_source* source, const char* mime, int32_t fd) {
    close(fd);
}
static void source_cancelled(void* data, struct wl_data_source* source) {
    puts("drag cancelled");
    wl_data_source_destroy(source);
}
static const struct wl_data_source_listener source_listener = {.target = source_target, .send = source_send, .cancelled = source_cancelled};

static void request_grabs(uint32_t serial) {
    grabs_requested = true;
    xdg_toplevel_move(windows[0].toplevel, seat, serial);
    xdg_toplevel_resize(windows[0].toplevel, seat, serial, XDG_TOPLEVEL_RESIZE_EDGE_BOTTOM_RIGHT);
    puts("requested move and resize");
    struct xdg_popup* popup = open_popup();
    xdg_popup_grab(popup, seat, serial);
    wl_surface_commit(windows[2].surface);
    puts("requested popup grab");
    struct wl_data_source* source = wl_data_device_manager_create_data_source(data_manager);
    wl_data_source_add_listener(source, &source_listener, NULL);
    wl_data_source_offer(source, "text/plain");
    wl_data_device_start_drag(data_device, source, windows[0].surface, NULL, serial);
    puts("requested drag");
}
static int run_guard(char** arguments, int count) {
    connect_display();
    create_toplevel(&windows[0], "hm-guard-hidden", NULL, BLUE);
    create_toplevel(&windows[1], "hm-guard-visible", NULL, BLUE);
    return run_loop();
}

// Regions mode: connects to a hyprmasq socket as a libei sender for MS milliseconds and prints the regions of every
// pointer device that the plugin adds, and each removal.
static int run_regions(char** arguments, int count) {
    const char* path = arguments[0];
    const int   ms   = parse_int(arguments[1], "MS", 0, INT32_MAX);
    struct ei*  ei   = ei_new_sender(NULL);
    ei_configure_name(ei, "hyprmasq-focus-probe");
    if (ei_setup_backend_socket(ei, path) != 0)
        return 1;
    const uint64_t deadline = now_ms() + (uint64_t)ms;
    struct pollfd  fd       = {.fd = ei_get_fd(ei), .events = POLLIN};
    for (uint64_t now; (now = now_ms()) < deadline;) {
        if (poll(&fd, 1, (int)(deadline - now)) < 0)
            return 1;
        ei_dispatch(ei);
        for (struct ei_event* e; (e = ei_get_event(ei)); ei_event_unref(e)) {
            struct ei_device* device = ei_event_get_device(e);
            switch (ei_event_get_type(e)) {
                case EI_EVENT_SEAT_ADDED: ei_seat_bind_capabilities(ei_event_get_seat(e), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL, NULL); break;
                case EI_EVENT_DEVICE_ADDED:
                    if (!ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE))
                        break;
                    printf("pointer");
                    for (size_t i = 0; ei_device_get_region(device, i); ++i) {
                        struct ei_region* r = ei_device_get_region(device, i);
                        printf("%s %u %u %u %u", i ? "," : "", ei_region_get_x(r), ei_region_get_y(r), ei_region_get_width(r), ei_region_get_height(r));
                    }
                    printf("\n");
                    break;
                case EI_EVENT_DEVICE_REMOVED:
                    if (ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE))
                        puts("pointer removed");
                    break;
                case EI_EVENT_DISCONNECT: puts("disconnected"); return 1;
                default: break;
            }
        }
    }
    ei_unref(ei);
    return 0;
}

// Focus mode: one or two toplevels on one connection, which log their input and focus events.
static int run_focus(char** arguments, int count) {
    connect_display();
    for (int i = 0; i < count; ++i)
        create_toplevel(&windows[i], arguments[i], NULL, BLUE);
    return run_loop();
}

// Close mode: logs like the focus mode for one toplevel, destroys it at once when Escape or the left button goes down
// on it, as a dialog does on the button that closes it, and goes on logging the keys that still arrive.
static void close_window(void) {
    if (!windows[0].toplevel)
        return;
    xdg_toplevel_destroy(windows[0].toplevel);
    xdg_surface_destroy(windows[0].xdg);
    wl_surface_destroy(windows[0].surface);
    windows[0] = (struct window){.name = windows[0].name};
    wl_display_flush(display);
    say("closed");
}
static int run_close(char** arguments, int count) {
    connect_display();
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// Hold mode: moves the pointer to X,Y as a libei sender, and presses Left Shift, A and the left button. Then with stop
// it stops emulating and disconnects a second later, and with disconnect it exits at once, releasing nothing. With
// signal, it prints "ready" once it has its devices, and on SIGUSR1 presses them and exits at once, without waiting for
// the plugin.
static struct ei*        sender;
static struct ei_device *sender_pointer, *sender_keyboard;
static bool              pointer_resumed, keyboard_resumed, ponged;

static void sender_dispatch(void) {
    ei_dispatch(sender);
    for (struct ei_event* e; (e = ei_get_event(sender)); ei_event_unref(e)) {
        struct ei_device* device = ei_event_get_device(e);
        switch (ei_event_get_type(e)) {
            case EI_EVENT_SEAT_ADDED:
                ei_seat_bind_capabilities(ei_event_get_seat(e), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL, EI_DEVICE_CAP_KEYBOARD, NULL);
                break;
            case EI_EVENT_DEVICE_ADDED:
                if (!sender_pointer && ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE))
                    sender_pointer = ei_device_ref(device);
                else if (!sender_keyboard && ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD))
                    sender_keyboard = ei_device_ref(device);
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
static bool sender_wait(bool* flag, int ms) {
    const uint64_t deadline = now_ms() + (uint64_t)ms;
    struct pollfd  fd       = {.fd = ei_get_fd(sender), .events = POLLIN};
    for (uint64_t now; !*flag && (now = now_ms()) < deadline;) {
        if (poll(&fd, 1, (int)(deadline - now)) < 0)
            return false;
        sender_dispatch();
    }
    return *flag;
}
// Returns once the plugin has handled everything sent before.
static bool round_trip(void) {
    struct ei_ping* ping = ei_new_ping(sender);
    ponged               = false;
    ei_ping(ping);
    const bool ok = sender_wait(&ponged, 5000);
    ei_ping_unref(ping);
    return ok;
}
static int run_hold(char** arguments, int count) {
    const char*  path = arguments[0];
    const double x = parse_double(arguments[1], "X"), y = parse_double(arguments[2], "Y");
    const char*  ending = arguments[3];
    if (strcmp(ending, "stop") && strcmp(ending, "disconnect") && strcmp(ending, "signal"))
        return -1;
    sigset_t usr1;
    sigemptyset(&usr1);
    sigaddset(&usr1, SIGUSR1);
    CHECK(sigprocmask(SIG_BLOCK, &usr1, NULL) == 0);
    sender = ei_new_sender(NULL);
    ei_configure_name(sender, "hyprmasq-focus-probe");
    if (ei_setup_backend_socket(sender, path) != 0 || !sender_wait(&pointer_resumed, 5000) || !sender_wait(&keyboard_resumed, 5000))
        return 1;
    const bool signalled = !strcmp(ending, "signal");
    if (signalled) {
        say("ready");
        int received;
        CHECK(sigwait(&usr1, &received) == 0);
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
    if (!round_trip())
        return 1;
    say("held");
    if (!strcmp(ending, "disconnect")) {
        say("disconnecting");
        _exit(0); // the kernel closes the socket
    }
    ei_device_stop_emulating(sender_pointer);
    ei_device_stop_emulating(sender_keyboard);
    if (!round_trip())
        return 1;
    say("stopped");
    bool never = false;
    sender_wait(&never, 1000);
    say("disconnecting");
    ei_device_unref(sender_pointer);
    ei_device_unref(sender_keyboard);
    ei_unref(sender);
    return 0;
}

// Resize mode: one toplevel that acks a configure that keeps its drawn size without a commit, as a client may that has
// nothing new to draw. A SIGUSR1 makes it hold back configures with another size: with hold, it neither acks nor draws
// them, with ack, it acks them at once but does not draw them. The next SIGUSR1 answers the last one held back, acking
// it with hold, and draws it at its size. Its right edge is green, so that a frame of another width shows where it ends.
static bool     ack_at_once, holding, held;
static uint32_t held_serial;
static int      drawn_width, drawn_height;

static void resize_configure(struct window* w, uint32_t serial) {
    if (w->width == drawn_width && w->height == drawn_height) {
        xdg_surface_ack_configure(w->xdg, serial);
        held = false;
        printf("kept %d %d\n", w->width, w->height);
    } else if (holding) {
        if (ack_at_once)
            xdg_surface_ack_configure(w->xdg, serial);
        held        = true;
        held_serial = serial;
        printf("held %d %d\n", w->width, w->height);
    } else {
        xdg_surface_ack_configure(w->xdg, serial);
        draw(w);
    }
}
static void resize_signalled(int signals) {
    signal_read(signals);
    struct window* w = &windows[0];
    holding          = !holding;
    if (holding) {
        puts("holding");
        return;
    }
    if (held) {
        if (!ack_at_once)
            xdg_surface_ack_configure(w->xdg, held_serial);
        held = false;
        draw(w);
    }
    puts("answered");
}
static void resize_paint(uint32_t* pixels, int width, int height) {
    for (int y = 0; y < height; ++y)
        for (int x = width > 8 ? width - 8 : 0; x < width; ++x)
            pixels[(size_t)y * width + x] = 0xff22cc22;
}
static int run_resize(char** arguments, int count) {
    if (strcmp(arguments[1], "hold") && strcmp(arguments[1], "ack"))
        return -1;
    ack_at_once = !strcmp(arguments[1], "ack");
    connect_display();
    watch(signals_new(SIGUSR1, 0), resize_signalled);
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// Frames mode: one toplevel that draws only in answer to frame callbacks, as Firefox and Thunderbird do on a hidden
// workspace. Its first frame is blank, blue, and it draws its content, orange, at the Nth frame callback, asking for each
// one before with a commit that draws nothing. With N 0, it never draws its content, and keeps asking. It waits for one
// frame callback at a time, also when it draws, so each one counts once.
static bool content_drawn, frame_requested;
static int  frames_needed, frames_got;

static void frame_done(void* data, struct wl_callback* callback, uint32_t time);
static const struct wl_callback_listener frame_listener = {.done = frame_done};
static void request_frame(struct window* w) {
    if (frame_requested)
        return;
    frame_requested = true;
    wl_callback_add_listener(wl_surface_frame(w->surface), &frame_listener, w);
}
static void frame_done(void* data, struct wl_callback* callback, uint32_t time) {
    struct window* w = data;
    wl_callback_destroy(callback);
    frame_requested = false;
    printf("frame %d\n", ++frames_got);
    if (content_drawn)
        return;
    if (frames_needed == 0 || frames_got < frames_needed) {
        request_frame(w);
        wl_surface_commit(w->surface);
        return;
    }
    content_drawn = true;
    w->color      = 0xffdd7711;
    draw(w);
}
// It draws a configure only when the size changed, so that it commits nothing else.
static void frames_configure(struct window* w, uint32_t serial) {
    xdg_surface_ack_configure(w->xdg, serial);
    if (w->width != drawn_width || w->height != drawn_height)
        draw(w);
}
static int run_frames(char** arguments, int count) {
    frames_needed = parse_int(arguments[1], "N", 0, INT32_MAX);
    connect_display();
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// Opener mode: logs like the focus mode for one or two toplevels, and opens windows on input to one of them, which it
// redraws in another color:
//   N, a left click    a toplevel without a parent, TITLE-newK, blank until its first frame callback, then green, when it
//                      logs "content"
//   D, a right click   a dialog of that toplevel, TITLE-dialogK
//   L                  a toplevel without a parent 300 ms later, TITLE-lateK
//   K                  one 1500 ms later, TITLE-laterK
//   a middle click     a dialog 1500 ms later, TITLE-late-dialogK
//   G                  a toplevel without a parent 1500 ms later on a second Wayland connection of the process,
//                      TITLE-otherK, as Electron opens GTK's file chooser beside the connection that Chromium draws on
//   F                  starts another process, `imported HANDLE TITLE-foreignK`, which opens a toplevel and names the one
//                      that got the key as its parent through xdg-foreign after its initial commit, as a portal's file
//                      chooser does. It leaves the probe's process tree, as a portal is no child of the app. A SIGUSR1
//                      starts one for the first toplevel without input.
//   A                  a dialog as D does, whose parent answers its close: it asks for a frame callback as the dialog
//                      closes, and 300 ms after the first one comes, which on a hidden workspace is when hyprmasq lets it
//                      draw, it redraws in orange, as a page lays out the file that a file chooser gave it, and logs
//                      "answered NAME"
// In a toplevel that it opened, S closes it and opens a toplevel without a parent 300 ms later, NAME-after, as a mail
// client's progress window after Send; a right click closes it at once, as a dialog's button does; and Enter redraws it
// in another color and closes it 300 ms later, as a file chooser does once it has taken the path. It logs "opened" when
// it creates a toplevel, "mapped" when it draws its first frame, and "destroyed", also for one asked to close.
//
// Browser mode works as the opener mode, but a left click opens a toplevel without a parent as a browser's popup does:
// it creates TITLE-popupK CREATE_MS milliseconds after the press and draws its first frame MAP_MS milliseconds after
// creating it. A SIGUSR2 creates TITLE-aheadK for its last toplevel at once, as an app that creates a window ahead, and
// the next left click there draws it MAP_MS later.
//
// Imported mode is the other process that F starts.
enum opening {
    OPEN_NEW,
    OPEN_DIALOG,
    OPEN_LATE,
    OPEN_LATER,
    OPEN_LATE_DIALOG,
    OPEN_OTHER,
    OPEN_FOREIGN,
    OPEN_ANSWERED,
    OPEN_POPUP
};

static struct window  opened[16];
static int            opened_count, foreign_count, late_timer = -1, close_timer = -1;
static struct window *late_from, *closing; // closing: the toplevel that close_timer closes
static const char*    late_kind;
static bool           late_parented, late_other;
static char*          late_after; // the name of the toplevel to open, when a toplevel closed for an S
static char*          handles[2]; // the xdg-foreign handles of the first toplevels
// The second connection, once G opened it, and its globals.
static struct wl_display*    other_display;
static struct wl_compositor* other_compositor;
static struct wl_shm*        other_shm;
static struct xdg_wm_base*   other_wm;
// The dialog whose close its parent answers, its parent, and the timer of the answer, which starts at the parent's first
// frame callback after the close.
static struct window *answered, *answering;
static int            answer_close_timer = -1;

static bool opens_windows(void) {
    return mode == MODE_OPENER || mode == MODE_BROWSER || mode == MODE_IMPORTED;
}
// The next free entry of opened, or NULL.
static struct window* new_opened(void) {
    return opened_count < (int)(sizeof(opened) / sizeof(opened[0])) ? &opened[opened_count++] : NULL;
}
static void answer_close_frame(void* data, struct wl_callback* callback, uint32_t time) {
    wl_callback_destroy(callback);
    timer_arm_ms(answer_close_timer, 300);
}
static const struct wl_callback_listener answer_close_listener = {.done = answer_close_frame};
static void destroy_opened(struct window* w) {
    xdg_toplevel_destroy(w->toplevel);
    xdg_surface_destroy(w->xdg);
    wl_surface_destroy(w->surface);
    w->toplevel = NULL;
    w->surface  = NULL;
    say("destroyed %s", w->name);
    if (w == answered) {
        answered = NULL;
        wl_callback_add_listener(wl_surface_frame(answering->surface), &answer_close_listener, NULL);
        wl_surface_commit(answering->surface);
    }
}
// A hungry toplevel draws green at its first frame callback.
static void content_frame(void* data, struct wl_callback* callback, uint32_t time) {
    struct window* w = data;
    wl_callback_destroy(callback);
    if (!w->hungry)
        return;
    w->hungry = false;
    w->color  = GREEN;
    draw(w);
    say("content %s", w->name);
}
static const struct wl_callback_listener content_listener = {.done = content_frame};
// Opens a toplevel named after the toplevel that got the input: green with that one as its parent if parented, else
// blue and hungry, as a mail client's compose window on a hidden workspace.
static void open_toplevel(struct window* from, const char* kind, bool parented) {
    struct window* w = new_opened();
    if (!w)
        return;
    char* name;
    CHECK(asprintf(&name, "%s-%s%d", from->name, kind, opened_count) > 0);
    create_toplevel(w, name, parented ? from->toplevel : NULL, parented ? GREEN : BLUE);
    w->hungry = !parented;
    say("opened %s", name);
}
// The second connection's events are read and dispatched as they come, and its requests sent at once.
static void other_readable(int fd) {
    if (wl_display_dispatch(other_display) < 0 || wl_display_flush(other_display) < 0)
        exit(1);
}
// Opens a green toplevel without a parent on the second connection, which it connects first.
static void open_other(struct window* from) {
    if (opened_count == (int)(sizeof(opened) / sizeof(opened[0])))
        return;
    if (!other_display) {
        other_display = wl_display_connect(NULL);
        CHECK(other_display);
        bind_globals(other_display,
                     (const struct probe_global[]){{.proxy = &other_compositor, .interface = &wl_compositor_interface, .version = 1},
                                                   {.proxy = &other_shm, .interface = &wl_shm_interface, .version = 1},
                                                   {.proxy = &other_wm, .interface = &xdg_wm_base_interface, .version = 1, .listener = &pong_listener},
                                                   {0}});
        watch(wl_display_get_fd(other_display), other_readable);
    }
    struct window* w = new_opened();
    char*          name;
    CHECK(asprintf(&name, "%s-other%d", from->name, opened_count) > 0);
    *w = (struct window){.width = 250, .height = 200, .name = name, .color = GREEN, .shm = other_shm};
    setup_toplevel(w, other_compositor, other_wm, NULL);
    say("opened %s", name);
    if (wl_display_flush(other_display) < 0)
        exit(1);
}
static void exported_handle(void* data, struct zxdg_exported_v2* exported, const char* handle) {
    *(char**)data = strdup(handle);
}
static const struct zxdg_exported_v2_listener exported_listener = {.handle = exported_handle};
// Starts another process that opens a toplevel with this one as its parent, through xdg-foreign.
static void open_foreign(struct window* from) {
    const char* handle = handles[from - windows];
    if (!handle) {
        say("no handle for %s", from->name);
        return;
    }
    char* name;
    CHECK(asprintf(&name, "%s-foreign%d", from->name, ++foreign_count) > 0);
    if (fork() == 0) {
        if (fork() == 0) {
            execl("/proc/self/exe", "hyprmasq-focus-probe", "imported", handle, name, (char*)NULL);
            _exit(127);
        }
        _exit(0);
    }
}
// Closes a toplevel that the probe opened, and opens another one 300 ms later, when the first is gone.
static void close_and_follow(struct wl_surface* s) {
    for (int i = 0; s && i < opened_count; ++i) {
        if (opened[i].surface != s)
            continue;
        CHECK(asprintf(&late_after, "%s-after", opened[i].name) > 0);
        destroy_opened(&opened[i]);
        wl_display_flush(display);
        timer_arm_ms(late_timer, 300);
        return;
    }
}
// Closes a toplevel that the probe opened: at once, or after redrawing it in another color, 300 ms later.
static void close_opened(struct wl_surface* s, bool later) {
    for (int i = 0; s && i < opened_count; ++i) {
        if (opened[i].surface != s)
            continue;
        if (!later) {
            destroy_opened(&opened[i]);
            wl_display_flush(display);
            return;
        }
        if (close_timer < 0)
            return;
        opened[i].color = opened[i].color == GREEN ? 0xff7722aa : GREEN;
        draw(&opened[i]);
        closing = &opened[i];
        timer_arm_ms(close_timer, 300);
        return;
    }
}

// The browser mode's popups that clicks open, by the toplevel that got the click until it is created, each with the time
// of its next step, and the timer of the earliest.
struct browser_popup {
    struct window *from, *w;
    uint64_t       due;
    bool           ahead; // created ahead, and waits for a click
};
static int                  browser_create_ms, browser_map_ms, browser_timer = -1, browser_count;
static struct browser_popup browser_popups[16];

static void browser_arm(void) {
    uint64_t due = 0;
    for (int i = 0; i < browser_count; ++i)
        if (!browser_popups[i].ahead && (!due || browser_popups[i].due < due))
            due = browser_popups[i].due;
    struct itimerspec at = {.it_value = {.tv_sec = (time_t)(due / 1000), .tv_nsec = (long)(due % 1000) * 1000000}};
    CHECK(timerfd_settime(browser_timer, TFD_TIMER_ABSTIME, &at, NULL) == 0);
}
// Creates the popups that are due and draws the ones whose time has come.
static void browser_step(void) {
    const uint64_t now = now_ms();
    for (int i = 0; i < browser_count; ++i) {
        if (browser_popups[i].ahead || browser_popups[i].due > now)
            continue;
        if (browser_popups[i].w) {
            browser_popups[i].w->held = false;
            if (browser_popups[i].w->surface)
                draw(browser_popups[i].w);
            browser_popups[i--] = browser_popups[--browser_count];
            continue;
        }
        const int before = opened_count;
        open_toplevel(browser_popups[i].from, "popup", false);
        if (opened_count == before) {
            browser_popups[i--] = browser_popups[--browser_count];
            continue;
        }
        browser_popups[i].w       = &opened[before];
        browser_popups[i].w->held = true;
        browser_popups[i].due     = now + (uint64_t)browser_map_ms;
    }
    if (browser_count)
        browser_arm();
}
static void browser_click(struct window* from) {
    for (int i = 0; i < browser_count; ++i)
        if (browser_popups[i].ahead && browser_popups[i].from == from) {
            browser_popups[i].ahead = false;
            browser_popups[i].due   = now_ms() + (uint64_t)browser_map_ms;
            browser_step();
            return;
        }
    if (browser_count == (int)(sizeof(browser_popups) / sizeof(browser_popups[0])))
        return;
    browser_popups[browser_count++] = (struct browser_popup){.from = from, .due = now_ms() + (uint64_t)browser_create_ms};
    browser_step();
}
static void browser_ahead(void) {
    struct window* from   = windows[1].surface ? &windows[1] : &windows[0];
    const int      before = opened_count;
    if (browser_count == (int)(sizeof(browser_popups) / sizeof(browser_popups[0])))
        return;
    open_toplevel(from, "ahead", false);
    if (opened_count == before)
        return;
    opened[before].held             = true;
    browser_popups[browser_count++] = (struct browser_popup){.from = from, .w = &opened[before], .ahead = true};
}

// Input to one of the first toplevels redraws it in another color, so that it draws something, and opens a window.
static void opener_input(struct wl_surface* s, enum opening opening) {
    struct window* from = s == windows[0].surface ? &windows[0] : s && s == windows[1].surface ? &windows[1] : NULL;
    if (!from)
        return;
    from->color = from->color == BLUE ? 0xff664422 : BLUE;
    draw(from);
    switch (opening) {
        case OPEN_LATE:
        case OPEN_LATER:
        case OPEN_LATE_DIALOG:
        case OPEN_OTHER:
            late_from     = from;
            late_kind     = opening == OPEN_LATE ? "late" : opening == OPEN_LATER ? "later" : "late-dialog";
            late_parented = opening == OPEN_LATE_DIALOG;
            late_other    = opening == OPEN_OTHER;
            timer_arm_ms(late_timer, opening == OPEN_LATE ? 300 : 1500);
            break;
        case OPEN_POPUP: browser_click(from); break;
        case OPEN_FOREIGN: open_foreign(from); break;
        case OPEN_ANSWERED: {
            const int before = opened_count;
            open_toplevel(from, "answer", true);
            if (opened_count > before) {
                answered  = &opened[before];
                answering = from;
            }
            break;
        }
        case OPEN_NEW: open_toplevel(from, "new", false); break;
        case OPEN_DIALOG: open_toplevel(from, "dialog", true); break;
    }
}
static void opener_button(uint32_t button) {
    switch (button) {
        case BTN_LEFT: opener_input(pointer_surface, mode == MODE_BROWSER ? OPEN_POPUP : OPEN_NEW); break;
        case BTN_RIGHT:
            opener_input(pointer_surface, OPEN_DIALOG);
            close_opened(pointer_surface, false);
            break;
        case BTN_MIDDLE: opener_input(pointer_surface, OPEN_LATE_DIALOG); break;
        default: break;
    }
}
static void opener_key(uint32_t key) {
    switch (key) {
        case KEY_N: opener_input(keyboard_surface, OPEN_NEW); break;
        case KEY_D: opener_input(keyboard_surface, OPEN_DIALOG); break;
        case KEY_L: opener_input(keyboard_surface, OPEN_LATE); break;
        case KEY_K: opener_input(keyboard_surface, OPEN_LATER); break;
        case KEY_F: opener_input(keyboard_surface, OPEN_FOREIGN); break;
        case KEY_A: opener_input(keyboard_surface, OPEN_ANSWERED); break;
        case KEY_G: opener_input(keyboard_surface, OPEN_OTHER); break;
        case KEY_S: close_and_follow(keyboard_surface); break;
        case KEY_ENTER: close_opened(keyboard_surface, true); break;
        default: break;
    }
}

static void late_expired(int timer) {
    timer_read(timer);
    if (late_after) {
        struct window* w = new_opened();
        if (w) {
            create_toplevel(w, late_after, NULL, GREEN);
            say("opened %s", late_after);
        }
        late_after = NULL;
    } else if (late_other)
        open_other(late_from);
    else
        open_toplevel(late_from, late_kind, late_parented);
}
static void opener_signalled(int signals) {
    const uint32_t signal = signal_read(signals);
    if (signal == SIGUSR2 && mode == MODE_BROWSER)
        browser_ahead();
    else if (signal == SIGUSR1)
        open_foreign(&windows[0]);
}
static void close_expired(int timer) {
    timer_read(timer);
    if (closing && closing->surface)
        destroy_opened(closing);
    closing = NULL;
}
static void answer_close_expired(int timer) {
    timer_read(timer);
    answering->color = ORANGE;
    draw(answering);
    say("answered %s", answering->name);
}
static void browser_expired(int timer) {
    timer_read(timer);
    browser_step();
}
// Shows one or two toplevels with these titles.
static int run_opener_with(char** titles, int count) {
    signal(SIGCHLD, SIG_IGN);
    connect_display();
    for (int i = 0; i < count; ++i)
        create_toplevel(&windows[i], titles[i], NULL, BLUE);
    for (int i = 0; exporter && i < count; ++i)
        zxdg_exported_v2_add_listener(zxdg_exporter_v2_export_toplevel(exporter, windows[i].surface), &exported_listener, &handles[i]);
    late_timer         = timer_new();
    close_timer        = timer_new();
    answer_close_timer = timer_new();
    browser_timer      = timer_new();
    watch(late_timer, late_expired);
    watch(signals_new(SIGUSR1, SIGUSR2, 0), opener_signalled);
    watch(close_timer, close_expired);
    watch(answer_close_timer, answer_close_expired);
    watch(browser_timer, browser_expired);
    return run_loop();
}
static int run_opener(char** arguments, int count) {
    return run_opener_with(arguments, count);
}
static int run_browser(char** arguments, int count) {
    browser_create_ms = parse_int(arguments[0], "CREATE_MS", 0, INT32_MAX);
    browser_map_ms    = parse_int(arguments[1], "MAP_MS", 0, INT32_MAX);
    return run_opener_with(arguments + 2, count - 2);
}
static int run_imported(char** arguments, int count) {
    const char *handle = arguments[0], *title = arguments[1];
    connect_display();
    CHECK(importer);
    // The parent follows the initial commit, so Hyprland learns it after it created the window, before it maps.
    struct window* w = new_opened();
    create_toplevel(w, title, NULL, GREEN);
    say("opened %s", title);
    zxdg_imported_v2_set_parent_of(zxdg_importer_v2_import_toplevel(importer, handle), w->surface);
    return run_loop();
}

// Answer mode: one toplevel that answers a left click at once by drawing itself dark gray, as a button that is pressed,
// and MS milliseconds later asks for a frame callback and draws its answer, blue, only when that comes, as a browser
// draws the page that loaded, or a mail client a search's results, on a hidden workspace. It logs "pressed" and
// "answered".
static int answer_ms, answer_timer = -1;

static void answer_frame(void* data, struct wl_callback* callback, uint32_t time) {
    struct window* w = data;
    wl_callback_destroy(callback);
    w->color = 0xff2255cc;
    draw(w);
    say("answered");
}
static const struct wl_callback_listener answer_listener = {.done = answer_frame};
static void answer_click(void) {
    windows[0].color = 0xff333333;
    draw(&windows[0]);
    say("pressed");
    timer_arm_ms(answer_timer, (uint64_t)answer_ms);
}
static void answer_expired(int timer) {
    timer_read(timer);
    wl_callback_add_listener(wl_surface_frame(windows[0].surface), &answer_listener, &windows[0]);
    wl_surface_commit(windows[0].surface);
}
static int run_answer(char** arguments, int count) {
    answer_ms = parse_int(arguments[1], "MS", 0, INT32_MAX);
    connect_display();
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    answer_timer = timer_new();
    watch(answer_timer, answer_expired);
    return run_loop();
}

// Menu mode: one toplevel that opens a menu on a left click, as a page's dropdown: it draws itself dark gray at once,
// then a lighter gray for every frame callback, so on a hidden workspace only while hyprmasq sends them, and is open,
// blue, at the first frame callback MS milliseconds after the click. Only then does it take keys: Down highlights the
// next of three entries, from none, and Enter picks the highlighted one and closes the menu. A left click while the menu
// opens or is open closes it. It logs "opening", "open", "highlight N", "picked N", "closed", and "ignored down" or
// "ignored enter" for keys that come while the menu is not open.
static enum {
    MENU_CLOSED,
    MENU_OPENING,
    MENU_OPEN
} menu_state;
static int      menu_ms, menu_highlight; // highlight: from 1, or 0 for none
static uint64_t menu_started;

static void menu_draw(uint32_t color) {
    windows[0].color = color;
    draw(&windows[0]);
}
static void menu_frame(void* data, struct wl_callback* callback, uint32_t time) {
    wl_callback_destroy(callback);
    if (menu_state != MENU_OPENING)
        return;
    const uint64_t elapsed = now_ms() - menu_started;
    if (elapsed >= (uint64_t)menu_ms) {
        menu_state     = MENU_OPEN;
        menu_highlight = 0;
        menu_draw(0xff2255cc);
        say("open");
        return;
    }
    const uint32_t level = 0x33 + (uint32_t)(0x88 * elapsed / (uint64_t)menu_ms);
    menu_draw(0xff000000 | level << 16 | level << 8 | level);
}
static const struct wl_callback_listener menu_listener = {.done = menu_frame};
static void menu_click(void) {
    if (menu_state != MENU_CLOSED) {
        menu_state = MENU_CLOSED;
        menu_draw(BLUE);
        say("closed");
        return;
    }
    menu_state   = MENU_OPENING;
    menu_started = now_ms();
    menu_draw(0xff333333);
    say("opening");
}
static void menu_key(uint32_t key) {
    const char* name = key == KEY_DOWN ? "down" : "enter";
    if (menu_state != MENU_OPEN || (key == KEY_ENTER && !menu_highlight)) {
        say("ignored %s", name);
        return;
    }
    if (key == KEY_DOWN) {
        if (menu_highlight < 3)
            ++menu_highlight;
        menu_draw(0xff2255cc + (uint32_t)menu_highlight * 0x2200);
        say("highlight %d", menu_highlight);
        return;
    }
    say("picked %d", menu_highlight);
    menu_state = MENU_CLOSED;
    menu_draw(BLUE);
}
static int run_menu(char** arguments, int count) {
    menu_ms = parse_int(arguments[1], "MS", 0, INT32_MAX);
    connect_display();
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// Shortcuts mode: logs like the focus mode for two toplevels that, as Chromium's, take the toplevel with the keyboard
// focus for the active one, and run a shortcut, a key pressed while Ctrl or Alt is held, only after the page has let it
// go by: MS milliseconds after the key, in the toplevel that got it, if that is still active. It then logs "shortcut
// TITLE MODS KEY" and redraws that toplevel in another color, and otherwise logs "shortcut TITLE MODS KEY inactive" and
// does nothing.
static int                shortcut_timer = -1, shortcut_ms;
static uint32_t           mods_depressed, shortcut_mods, shortcut_key;
static struct wl_surface* shortcut_surface;

// Ctrl is mask 4 and Alt mask 8 in the default keymap. The modifier keys themselves start no shortcut.
static void shortcut_key_pressed(uint32_t key) {
    const uint32_t mods = mods_depressed & (4 | 8);
    if (!mods || !keyboard_surface || key == KEY_LEFTCTRL || key == KEY_RIGHTCTRL || key == KEY_LEFTALT || key == KEY_RIGHTALT || key == KEY_LEFTSHIFT || key == KEY_RIGHTSHIFT ||
        key == KEY_LEFTMETA || key == KEY_RIGHTMETA)
        return;
    shortcut_surface = keyboard_surface;
    shortcut_mods    = mods;
    shortcut_key     = key;
    timer_arm_ms(shortcut_timer, (uint64_t)shortcut_ms);
}
static void shortcut_expired(int timer) {
    timer_read(timer);
    struct window* w = NULL;
    for (size_t i = 0; i < 2; ++i)
        if (windows[i].surface == shortcut_surface)
            w = &windows[i];
    if (!w)
        return;
    const bool active = keyboard_surface == shortcut_surface;
    say("shortcut %s %u %u%s", w->name, shortcut_mods, shortcut_key, active ? "" : " inactive");
    if (active) {
        w->color = w->color == 0xffaa22aa ? 0xff2244aa : 0xffaa22aa;
        draw(w);
    }
    shortcut_surface = NULL;
}
static int run_shortcuts(char** arguments, int count) {
    shortcut_ms = parse_int(arguments[2], "MS", 0, INT32_MAX);
    connect_display();
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    create_toplevel(&windows[1], arguments[1], NULL, BLUE);
    shortcut_timer = timer_new();
    watch(shortcut_timer, shortcut_expired);
    return run_loop();
}

// Shift mode: one toplevel with a light button at 40,40 of 160×40 with a dark label, which logs its input like the focus
// mode. A SIGUSR1 moves the button 60 pixels down MS milliseconds later, as a form that a finished upload pushes down,
// and logs "shifted"; a SIGUSR2 draws a red block in the bottom right corner, far from the button, and logs "elsewhere".
static bool shift_elsewhere;
static int  shift_ms, shift_offset, shift_timer = -1;

static void fill_rect(uint32_t* pixels, int width, int height, int x, int y, int w, int h, uint32_t color) {
    for (int row = y; row < y + h && row < height; ++row)
        for (int column = x; column < x + w && column < width; ++column)
            if (row >= 0 && column >= 0)
                pixels[(size_t)row * width + column] = color;
}
static void shift_paint(uint32_t* pixels, int width, int height) {
    fill_rect(pixels, width, height, 40, 40 + shift_offset, 160, 40, 0xffeeeeee);
    fill_rect(pixels, width, height, 60, 55 + shift_offset, 80, 10, 0xff222222);
    if (shift_elsewhere)
        fill_rect(pixels, width, height, width - 100, height - 80, 80, 60, 0xffcc2222);
}
static void shift_signalled(int signals) {
    if (signal_read(signals) == SIGUSR1)
        timer_arm_ms(shift_timer, (uint64_t)shift_ms);
    else {
        shift_elsewhere = true;
        draw(&windows[0]);
        say("elsewhere");
    }
}
static void shift_expired(int timer) {
    timer_read(timer);
    shift_offset = 60;
    draw(&windows[0]);
    say("shifted");
}
static int run_shift(char** arguments, int count) {
    shift_ms = parse_int(arguments[1], "MS", 0, INT32_MAX);
    connect_display();
    watch(signals_new(SIGUSR1, SIGUSR2, 0), shift_signalled);
    shift_timer = timer_new();
    watch(shift_timer, shift_expired);
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// Tiles mode: one toplevel that redraws at every frame callback, damaging the tile at 40,40 of 256×128 whole, as
// Thunderbird's renderer damages tiles, though only a spinner at 48,48 of 24×24 changes in it: noise that is new at
// every frame. A left click draws a light block at 180,120 of 96×32 in the same tile, which stays, and logs "content".
static bool     tiles_pending, tiles_content; // pending: a frame callback
static unsigned tiles_frame;
static int      tiles_width, tiles_height; // last drawn

static void tiles_paint(uint32_t* pixels, int width, int height) {
    // Noise of 8×8 cells, each light or dark by a hash of the frame and the cell, so that any two frames differ across
    // the whole spinner.
    for (uint32_t cell = 0; cell < 64; ++cell) {
        uint32_t x = tiles_frame * 0x9e3779b1u ^ (cell + 1) * 0x85ebca77u;
        x ^= x >> 15;
        x *= 0x2c1b3c6du;
        x ^= x >> 12;
        x *= 0x297a2d39u;
        x ^= x >> 15;
        fill_rect(pixels, width, height, 48 + 3 * (int)(cell % 8), 48 + 3 * (int)(cell / 8), 3, 3, x & 1 ? 0xffeeeeee : 0xff111111);
    }
    if (tiles_content)
        fill_rect(pixels, width, height, 180, 120, 96, 32, 0xffeeeeee);
}
static void tiles_frame_done(void* data, struct wl_callback* callback, uint32_t time) {
    wl_callback_destroy(callback);
    tiles_pending = false;
    ++tiles_frame;
    draw(data);
}
static const struct wl_callback_listener tiles_listener = {.done = tiles_frame_done};
static void tiles_click(void) {
    tiles_content = true;
    draw(&windows[0]);
    say("content");
}
static int run_tiles(char** arguments, int count) {
    connect_display();
    create_toplevel(&windows[0], arguments[0], NULL, BLUE);
    return run_loop();
}

// The toplevels: drawing, configures, and the input of the seat, which every mode logs and hands on.
static const char* surface_name(struct wl_surface* s) {
    for (size_t i = 0; i < 3; ++i)
        if (windows[i].surface == s)
            return windows[i].name;
    for (int i = 0; i < opened_count; ++i)
        if (opened[i].surface == s)
            return opened[i].name;
    return "unknown";
}
static void draw(struct window* w) {
    // Only the first toplevel has a margin, which its window geometry leaves out.
    const bool        first = w == &windows[0];
    const int         mx = first ? margin_x : 0, my = first ? margin_y : 0;
    const int         width = w->width + mx, height = w->height + my;
    struct wl_buffer* buffer = shm_buffer(w->shm ? w->shm : shm, width, height, w->color, first ? modes[mode].paint : NULL);
    if (mx || my)
        xdg_surface_set_window_geometry(w->xdg, mx, my, w->width, w->height);
    wl_surface_attach(w->surface, buffer, 0, 0);
    if (mode == MODE_TILES && width == tiles_width && height == tiles_height)
        wl_surface_damage(w->surface, 40, 40, 256, 128);
    else
        wl_surface_damage(w->surface, 0, 0, width, height);
    if (mode == MODE_TILES) {
        tiles_width  = width;
        tiles_height = height;
        if (!tiles_pending) {
            tiles_pending = true;
            wl_callback_add_listener(wl_surface_frame(w->surface), &tiles_listener, w);
        }
    }
    if (mode == MODE_FRAMES)
        request_frame(w);
    if (w->hungry)
        wl_callback_add_listener(wl_surface_frame(w->surface), &content_listener, w);
    if (mode == MODE_MENU && menu_state == MENU_OPENING)
        wl_callback_add_listener(wl_surface_frame(w->surface), &menu_listener, w);
    wl_surface_commit(w->surface);
    if (mode == MODE_RESIZE && first) {
        drawn_width  = w->width;
        drawn_height = w->height;
        printf("drew %d %d\n", w->width, w->height);
    } else if (mode == MODE_FRAMES) {
        drawn_width  = w->width;
        drawn_height = w->height;
        printf("drew %s %d %d\n", content_drawn ? "content" : "blank", w->width, w->height);
    } else
        printf("configured %s\n", w->name);
    if (opens_windows() && !w->drawn)
        say("mapped %s", w->name);
    w->drawn = true;
}
static void configure(void* data, struct xdg_surface* surface, uint32_t serial) {
    struct window* w = data;
    if (mode == MODE_RESIZE && w == &windows[0])
        resize_configure(w, serial);
    else if (mode == MODE_FRAMES)
        frames_configure(w, serial);
    else {
        xdg_surface_ack_configure(surface, serial);
        if (!w->held)
            draw(w);
    }
}
static const struct xdg_surface_listener surface_listener = {.configure = configure};
static void toplevel_configure(void* data, struct xdg_toplevel* top, int32_t width, int32_t height, struct wl_array* states) {
    struct window* w = data;
    if (width > 0)
        w->width = width;
    if (height > 0)
        w->height = height;
}
// A toplevel that the probe opened is destroyed when asked to close, and any other ends the probe.
static void toplevel_close(void* data, struct xdg_toplevel* top) {
    struct window* w = data;
    if (w < opened || w >= opened + opened_count)
        exit(0);
    destroy_opened(w);
}
static const struct xdg_toplevel_listener toplevel_listener = {.configure = toplevel_configure, .close = toplevel_close};
static void setup_toplevel(struct window* w, struct wl_compositor* on, struct xdg_wm_base* base, struct xdg_toplevel* parent) {
    w->surface = wl_compositor_create_surface(on);
    w->xdg     = xdg_wm_base_get_xdg_surface(base, w->surface);
    xdg_surface_add_listener(w->xdg, &surface_listener, w);
    w->toplevel = xdg_surface_get_toplevel(w->xdg);
    xdg_toplevel_add_listener(w->toplevel, &toplevel_listener, w);
    xdg_toplevel_set_title(w->toplevel, w->name);
    if (parent)
        xdg_toplevel_set_parent(w->toplevel, parent);
    wl_surface_commit(w->surface);
}
// A 250×200 toplevel of the first connection, with a parent unless that is NULL, which draws at its first configure.
static void create_toplevel(struct window* w, const char* name, struct xdg_toplevel* parent, uint32_t color) {
    *w = (struct window){.width = 250, .height = 200, .name = name, .color = color};
    setup_toplevel(w, compositor, wm, parent);
}

static void pointer_enter(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s, wl_fixed_t x, wl_fixed_t y) {
    pointer_surface = s;
    say("pointer enter %s", surface_name(s));
}
static void pointer_leave(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s) {
    pointer_surface = NULL;
    say("pointer leave %s", surface_name(s));
}
static void pointer_motion(void* data, struct wl_pointer* p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {
    say("motion %s %g %g", surface_name(pointer_surface), wl_fixed_to_double(x), wl_fixed_to_double(y));
}
static void pointer_button(void* data, struct wl_pointer* p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    say("button %s %u", surface_name(pointer_surface), state);
    if (state != WL_POINTER_BUTTON_STATE_PRESSED)
        return;
    const bool on_first = pointer_surface == windows[0].surface;
    if (on_first && !grabs_requested)
        request_grabs(serial);
    if (on_first && button == BTN_LEFT) {
        switch (mode) {
            case MODE_CLOSE: close_window(); break;
            case MODE_ANSWER: answer_click(); break;
            case MODE_MENU: menu_click(); break;
            case MODE_TILES: tiles_click(); break;
            default: break;
        }
    }
    if (opens_windows())
        opener_button(button);
}
static void pointer_axis(void* data, struct wl_pointer* p, uint32_t time, uint32_t axis, wl_fixed_t value) {
    say("axis %s %u %g", surface_name(pointer_surface), axis, wl_fixed_to_double(value));
}
static const struct wl_pointer_listener pointer_listener = {
    .enter  = pointer_enter,
    .leave  = pointer_leave,
    .motion = pointer_motion,
    .button = pointer_button,
    .axis   = pointer_axis,
};
static void keyboard_keymap(void* data, struct wl_keyboard* kb, uint32_t format, int32_t fd, uint32_t size) {
    close(fd);
}
static void keyboard_enter(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s, struct wl_array* keys) {
    keyboard_surface = s;
    say("keyboard enter %s", surface_name(s));
}
static void keyboard_leave(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s) {
    keyboard_surface = NULL;
    say("keyboard leave %s", surface_name(s));
}
static void keyboard_key(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    say("key %s %u %u", surface_name(keyboard_surface), key, state);
    if (state != WL_KEYBOARD_KEY_STATE_PRESSED)
        return;
    if (mode == MODE_SHORTCUTS)
        shortcut_key_pressed(key);
    if (mode == MODE_MENU && (key == KEY_DOWN || key == KEY_ENTER))
        menu_key(key);
    if (opens_windows())
        opener_key(key);
    if (mode == MODE_CLOSE && key == KEY_ESC)
        close_window();
}
static void keyboard_modifiers(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {
    mods_depressed = dep;
    say("modifiers %s %u", surface_name(keyboard_surface), dep);
}
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap    = keyboard_keymap,
    .enter     = keyboard_enter,
    .leave     = keyboard_leave,
    .key       = keyboard_key,
    .modifiers = keyboard_modifiers,
};
static struct seat_input seat_input = {.pointer_listener = &pointer_listener, .keyboard_listener = &keyboard_listener};

// No drag is expected to enter the probe's windows.
static void offer_mime(void* data, struct wl_data_offer* offer, const char* mime) {}
static const struct wl_data_offer_listener offer_listener = {.offer = offer_mime};
static void data_offer(void* data, struct wl_data_device* device, struct wl_data_offer* offer) {
    wl_data_offer_add_listener(offer, &offer_listener, NULL);
}
static void data_enter(void* data, struct wl_data_device* device, uint32_t serial, struct wl_surface* surface, wl_fixed_t x, wl_fixed_t y, struct wl_data_offer* offer) {
    puts("unexpected drag enter");
}
static void data_leave(void* data, struct wl_data_device* device) {}
static void data_motion(void* data, struct wl_data_device* device, uint32_t time, wl_fixed_t x, wl_fixed_t y) {}
static void data_drop(void* data, struct wl_data_device* device) {}
static void data_selection(void* data, struct wl_data_device* device, struct wl_data_offer* offer) {}
static const struct wl_data_device_listener data_listener = {
    .data_offer = data_offer,
    .enter      = data_enter,
    .leave      = data_leave,
    .motion     = data_motion,
    .drop       = data_drop,
    .selection  = data_selection,
};

static void connect_display(void) {
    display = wl_display_connect(NULL);
    CHECK(display);
    bind_globals(display,
                 (const struct probe_global[]){{.proxy = &compositor, .interface = &wl_compositor_interface, .version = 1},
                                               {.proxy = &shm, .interface = &wl_shm_interface, .version = 1},
                                               {.proxy = &wm, .interface = &xdg_wm_base_interface, .version = 1, .listener = &pong_listener},
                                               {.proxy = &seat, .interface = &wl_seat_interface, .version = 1, .listener = &seat_input_listener, .data = &seat_input},
                                               {.proxy = &data_manager, .interface = &wl_data_device_manager_interface, .version = 1},
                                               {.proxy = &exporter, .interface = &zxdg_exporter_v2_interface, .version = 1, .optional = true},
                                               {.proxy = &importer, .interface = &zxdg_importer_v2_interface, .version = 1, .optional = true},
                                               {0}});
    data_device = wl_data_device_manager_get_data_device(data_manager, seat);
    wl_data_device_add_listener(data_device, &data_listener, NULL);
}

// The event loop: the display's events, and the file descriptors that the mode watches, timers and signals, with what
// to do when one is readable.
#define MAX_WATCHES 8
static struct {
    int fd;
    void (*ready)(int fd);
} watches[MAX_WATCHES];
static int watch_count;

static void watch(int fd, void (*ready)(int fd)) {
    CHECK(fd >= 0 && watch_count < MAX_WATCHES);
    watches[watch_count].fd    = fd;
    watches[watch_count].ready = ready;
    ++watch_count;
}
static int run_loop(void) {
    for (;;) {
        // A handler may add a watch, which the next round polls.
        const int     count                = watch_count;
        struct pollfd fds[1 + MAX_WATCHES] = {{0}};
        for (int i = 0; i < count; ++i)
            fds[1 + i] = (struct pollfd){.fd = watches[i].fd, .events = POLLIN};
        if (!dispatch_display(display, fds, (nfds_t)(1 + count), -1))
            return 1;
        for (int i = 0; i < count; ++i)
            if (fds[1 + i].revents & POLLIN)
                watches[i].ready(watches[i].fd);
    }
}

static const struct mode_info modes[MODE_COUNT] = {
    [MODE_GUARD]     = {"guard", "", "two toplevels whose first press requests a move, a resize, a popup grab and a drag", 0, 0, false, true, NULL, run_guard},
    [MODE_POPUP]     = {"popup", "TITLE [X Y WIDTH HEIGHT [GX GY]]", "a toplevel that opens and closes a popup on SIGUSR1", 1, 7, false, true, NULL, run_popup},
    [MODE_REGIONS]   = {"regions", "SOCKET MS", "a libei sender that prints its pointer regions for MS milliseconds", 2, 2, false, false, NULL, run_regions},
    [MODE_FOCUS]     = {"focus", "TITLE [TITLE]", "one or two toplevels that log their input and focus", 1, 2, true, false, NULL, run_focus},
    [MODE_CLOSE]     = {"close", "TITLE", "a toplevel that closes on Escape or a left press", 1, 1, true, false, NULL, run_close},
    [MODE_HOLD]      = {"hold", "SOCKET X Y stop|disconnect|signal", "a libei sender that presses Left Shift, A and the left button at X,Y", 4, 4, true, false, NULL, run_hold},
    [MODE_RESIZE]    = {"resize", "TITLE hold|ack", "a toplevel that holds back configures of another size from one SIGUSR1 to the next", 2, 2, false, false, resize_paint,
                        run_resize},
    [MODE_FRAMES]    = {"frames", "TITLE N", "a toplevel that draws only at frame callbacks, its content at the Nth", 2, 2, false, false, NULL, run_frames},
    [MODE_OPENER]    = {"opener", "TITLE [TITLE]", "one or two toplevels whose input opens toplevels and dialogs", 1, 2, true, false, NULL, run_opener},
    [MODE_BROWSER]   = {"browser", "CREATE_MS MAP_MS TITLE [TITLE]", "the opener mode, where a left click opens a popup as a browser does", 3, 4, true, false, NULL, run_browser},
    [MODE_IMPORTED]  = {"imported", "HANDLE TITLE", "the toplevel that F in the opener mode opens in another process", 2, 2, true, false, NULL, run_imported},
    [MODE_ANSWER]    = {"answer", "TITLE MS", "a toplevel that draws its answer to a click at a frame callback MS milliseconds later", 2, 2, true, false, NULL, run_answer},
    [MODE_MENU]      = {"menu", "TITLE MS", "a toplevel whose click opens a menu over MS milliseconds of frame callbacks", 2, 2, true, false, NULL, run_menu},
    [MODE_SHORTCUTS] = {"shortcuts", "TITLE TITLE MS", "two toplevels that run a shortcut MS milliseconds after its key if still active", 3, 3, true, false, NULL, run_shortcuts},
    [MODE_SHIFT]     = {"shift", "TITLE MS", "a toplevel whose button moves down MS milliseconds after SIGUSR1", 2, 2, true, false, shift_paint, run_shift},
    [MODE_TILES]     = {"tiles", "TITLE", "a toplevel that redraws a tile with a spinner at every frame callback", 1, 1, true, false, tiles_paint, run_tiles},
};

static void print_mode(const struct mode_info* m) {
    char usage[64];
    snprintf(usage, sizeof(usage), "%s %s", m->name, m->arguments);
    fprintf(stderr, "  %-45s %s\n", usage, m->description);
}
int main(int argc, char** argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    int found = 0;
    while (found < MODE_COUNT && (argc < 2 || strcmp(argv[1], modes[found].name)))
        ++found;
    if (found == MODE_COUNT) {
        if (argc >= 2)
            fprintf(stderr, "hyprmasq-focus-probe: unknown mode %s\n", argv[1]);
        fprintf(stderr, "usage: hyprmasq-focus-probe MODE [ARGUMENT]...\n");
        for (int i = 0; i < MODE_COUNT; ++i)
            print_mode(&modes[i]);
        return 2;
    }
    mode                          = (enum mode)found;
    const struct mode_info* m     = &modes[mode];
    const int               count = argc - 2;
    say_stamped                   = m->stamped;
    grabs_requested               = !m->grabs;
    const int status              = count >= m->min_arguments && count <= m->max_arguments ? m->run(argv + 2, count) : -1;
    if (status < 0) {
        fprintf(stderr, "usage: hyprmasq-focus-probe MODE [ARGUMENT]...\n");
        print_mode(m);
        return 2;
    }
    return status;
}
