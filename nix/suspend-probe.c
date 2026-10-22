// VM fixture: a raw Wayland toplevel that logs whether the compositor tells it that it is suspended, and unmaps and
// maps again on signals while it keeps its xdg_toplevel.
//
//   hyprmasq-suspend-probe TITLE VERSION
//
// It binds xdg_wm_base at VERSION, 1 to 6, whose configures can say that a toplevel is suspended from version 6 on, and
// shows a toplevel TITLE in a gray shm buffer of the size of each configure, 240×160 when the compositor leaves it the
// choice. A SIGUSR1 unmaps it with a commit without a buffer, which keeps its xdg_toplevel, and a SIGUSR2 maps it again
// with a buffer of the size of the last configure. It binds the seat, without which the plugin delivers it no input,
// and takes no input from it.
//
// It logs after a CLOCK_MONOTONIC timestamp in milliseconds "configure W H suspended" or "configure W H shown" for each
// configure, with " unmapped" after it for one that comes while it is unmapped, which it acks only as it maps again,
// and "mapped" and "unmapped" as it maps and unmaps.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "probe-common.h"

#define GRAY 0xff808080

static struct wl_display*    display;
static struct wl_compositor* compositor;
static struct wl_shm*        shm;
static struct wl_seat*       seat;
static struct xdg_wm_base*   wm;
static struct wl_surface*    surface;
static struct xdg_surface*   xdg;
static struct xdg_toplevel*  toplevel;
static int                   width = 240, height = 160; // of the last configure that gave a size
static int                   configured_width, configured_height;
static bool                  configured_suspended;
static bool                  mapped, mapping = true; // mapping: the first configure maps it
static uint32_t              unacked;                // the serial of a configure that came while unmapped, or 0

static void draw(void) {
    wl_surface_attach(surface, shm_buffer(shm, width, height, GRAY, NULL), 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, width, height);
    wl_surface_commit(surface);
}

static void toplevel_configure(void* data, struct xdg_toplevel* top, int32_t w, int32_t h, struct wl_array* states) {
    configured_width     = w;
    configured_height    = h;
    configured_suspended = false;
    const uint32_t* state;
    wl_array_for_each(state, states) {
        if (*state == XDG_TOPLEVEL_STATE_SUSPENDED)
            configured_suspended = true;
    }
}
static void toplevel_close(void* data, struct xdg_toplevel* top) {}
static void toplevel_bounds(void* data, struct xdg_toplevel* top, int32_t w, int32_t h) {}
static void toplevel_capabilities(void* data, struct xdg_toplevel* top, struct wl_array* capabilities) {}
static const struct xdg_toplevel_listener toplevel_listener = {
    .configure = toplevel_configure, .close = toplevel_close, .configure_bounds = toplevel_bounds, .wm_capabilities = toplevel_capabilities};

static void surface_configure(void* data, struct xdg_surface* surf, uint32_t serial) {
    if (configured_width > 0 && configured_height > 0) {
        width  = configured_width;
        height = configured_height;
    }
    const char* state = configured_suspended ? "suspended" : "shown";
    if (!mapped && !mapping) {
        say("configure %d %d %s unmapped", width, height, state);
        unacked = serial;
        return;
    }
    say("configure %d %d %s", width, height, state);
    xdg_surface_ack_configure(surf, serial);
    draw();
    if (mapping) {
        mapping = false;
        mapped  = true;
        say("mapped");
    }
}
static const struct xdg_surface_listener surface_listener = {.configure = surface_configure};

int main(int argc, char** argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: hyprmasq-suspend-probe TITLE VERSION\n");
        return 2;
    }
    const int version = parse_int(argv[2], "VERSION", 1, 6);
    CHECK((display = wl_display_connect(NULL)));
    const struct probe_global globals[] = {
        {.proxy = &compositor, .interface = &wl_compositor_interface, .version = 4},
        {.proxy = &shm, .interface = &wl_shm_interface, .version = 1},
        {.proxy = &seat, .interface = &wl_seat_interface, .version = 1},
        {.proxy = &wm, .interface = &xdg_wm_base_interface, .version = (uint32_t)version, .listener = &pong_listener},
        {0},
    };
    bind_globals(display, globals);

    surface = wl_compositor_create_surface(compositor);
    xdg     = xdg_wm_base_get_xdg_surface(wm, surface);
    xdg_surface_add_listener(xdg, &surface_listener, NULL);
    toplevel = xdg_surface_get_toplevel(xdg);
    xdg_toplevel_add_listener(toplevel, &toplevel_listener, NULL);
    xdg_toplevel_set_title(toplevel, argv[1]);
    xdg_toplevel_set_app_id(toplevel, "hyprmasq-suspend-probe");
    wl_surface_commit(surface);

    const int     signals = signals_new(SIGUSR1, SIGUSR2, 0);
    struct pollfd fds[2]  = {[1] = {.fd = signals, .events = POLLIN}};
    while (dispatch_display(display, fds, 2, -1)) {
        if (!(fds[1].revents & POLLIN))
            continue;
        const uint32_t signal = signal_read(signals);
        if (signal == SIGUSR1 && mapped) {
            wl_surface_attach(surface, NULL, 0, 0);
            wl_surface_commit(surface);
            mapped = false;
            say("unmapped");
        } else if (signal == SIGUSR2 && !mapped && !mapping) {
            if (unacked)
                xdg_surface_ack_configure(xdg, unacked);
            unacked = 0;
            draw();
            mapped = true;
            say("mapped");
        }
    }
    return 0;
}
