// VM fixture: a raw Wayland client that draws into dmabuf buffers, as an app that renders on a GPU does, and before
// each frame waits for a buffer that the compositor has released, as Mesa's swapchains do, answering no ping meanwhile.
//
//   hyprmasq-swapchain-probe TITLE
//
// It shows a toplevel TITLE of 320×240 from a swapchain of 3 XRGB8888 dumb buffers of the first DRM card, which it
// shares as LINEAR dmabufs. So the probe needs no GPU, and Hyprland holds the buffers as it holds a GPU's, while it
// copies shm buffers and releases them at once. A press of a key or a button draws 4 frames, each in the next color
// and after the frame callback of the one before. A frame takes a buffer that the compositor has released, and while
// none is, the probe dispatches only the queue of the buffers' events, as Mesa's vkAcquireNextImageKHR and
// eglSwapBuffers do: the compositor's pings wait, and Hyprland counts them as missed.
//
// It logs after a CLOCK_MONOTONIC timestamp in milliseconds: "ready" once it committed its first frame, "ping" for each
// ping it answers, "frame N" for each frame it commits, and "waited MS" after it waited MS milliseconds for a buffer.
#define _GNU_SOURCE
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "linux-dmabuf-v1-client-protocol.h"
#include "probe-common.h"
#include <drm/drm.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_mode.h>
#include <fcntl.h>
#include <sys/ioctl.h>

#define WIDTH            320
#define HEIGHT           240
#define BUFFERS          3
#define FRAMES_PER_PRESS 4

struct swap_buffer {
    struct wl_buffer* buffer;
    uint32_t*         pixels;
    bool              busy; // attached, and not released since
};

static struct wl_display*          display;
static struct wl_event_queue*      queue; // the buffers' events, as Mesa keeps them apart from the app's
static struct wl_compositor*       compositor;
static struct wl_seat*             seat;
static struct xdg_wm_base*         wm;
static struct zwp_linux_dmabuf_v1* dmabuf;
static struct wl_surface*          surface;
static int                         card = -1; // the DRM card that has the buffers
static struct swap_buffer          buffers[BUFFERS];
static int                         frames;   // committed
static int                         pending;  // still to draw
static bool                        awaiting; // the frame callback of the last frame

static void ping(void* data, struct xdg_wm_base* base, uint32_t serial) {
    xdg_wm_base_pong(base, serial);
    say("ping");
}
static const struct xdg_wm_base_listener wm_listener = {.ping = ping};

static void buffer_release(void* data, struct wl_buffer* buffer) {
    ((struct swap_buffer*)data)->busy = false;
}
static const struct wl_buffer_listener buffer_listener = {.release = buffer_release};

static void params_created(void* data, struct zwp_linux_buffer_params_v1* params, struct wl_buffer* buffer) {
    *(struct wl_buffer**)data = buffer;
}
static void params_failed(void* data, struct zwp_linux_buffer_params_v1* params) {
    fprintf(stderr, "%s: the compositor cannot import the dmabuf\n", program_invocation_short_name);
    exit(1);
}
static const struct zwp_linux_buffer_params_v1_listener params_listener = {.created = params_created, .failed = params_failed};

static void open_card(void) {
    char path[32];
    for (int i = 0; i < 8 && card < 0; ++i) {
        snprintf(path, sizeof(path), "/dev/dri/card%d", i);
        card = open(path, O_RDWR | O_CLOEXEC);
    }
    if (card < 0) {
        fprintf(stderr, "%s: no DRM card to make buffers on\n", program_invocation_short_name);
        exit(1);
    }
}

static void make_buffer(struct swap_buffer* b) {
    struct drm_mode_create_dumb create = {.height = HEIGHT, .width = WIDTH, .bpp = 32};
    CHECK(ioctl(card, DRM_IOCTL_MODE_CREATE_DUMB, &create) == 0);
    struct drm_mode_map_dumb map = {.handle = create.handle};
    CHECK(ioctl(card, DRM_IOCTL_MODE_MAP_DUMB, &map) == 0);
    b->pixels = mmap(NULL, create.size, PROT_READ | PROT_WRITE, MAP_SHARED, card, (off_t)map.offset);
    CHECK(b->pixels != MAP_FAILED);
    CHECK(create.pitch == WIDTH * 4);
    struct drm_prime_handle prime = {.handle = create.handle, .flags = DRM_CLOEXEC | DRM_RDWR};
    CHECK(ioctl(card, DRM_IOCTL_PRIME_HANDLE_TO_FD, &prime) == 0);
    const int                          fd     = prime.fd;
    struct zwp_linux_buffer_params_v1* params = zwp_linux_dmabuf_v1_create_params(dmabuf);
    zwp_linux_buffer_params_v1_add_listener(params, &params_listener, &b->buffer);
    zwp_linux_buffer_params_v1_add(params, fd, 0, 0, WIDTH * 4, DRM_FORMAT_MOD_LINEAR >> 32, DRM_FORMAT_MOD_LINEAR & 0xffffffff);
    zwp_linux_buffer_params_v1_create(params, WIDTH, HEIGHT, DRM_FORMAT_XRGB8888, 0);
    while (!b->buffer)
        CHECK(wl_display_dispatch(display) >= 0);
    zwp_linux_buffer_params_v1_destroy(params);
    close(fd);
    wl_proxy_set_queue((struct wl_proxy*)b->buffer, queue);
    wl_buffer_add_listener(b->buffer, &buffer_listener, b);
}

// A released buffer, waited for on the buffers' queue alone.
static struct swap_buffer* acquire(void) {
    uint64_t started = 0;
    for (;;) {
        CHECK(wl_display_dispatch_queue_pending(display, queue) >= 0);
        for (int i = 0; i < BUFFERS; ++i) {
            if (buffers[i].busy)
                continue;
            if (started)
                say("waited %llu", (unsigned long long)(now_ms() - started));
            return &buffers[i];
        }
        if (!started)
            started = now_ms();
        CHECK(wl_display_dispatch_queue(display, queue) >= 0);
    }
}

static void frame_done(void* data, struct wl_callback* callback, uint32_t time) {
    wl_callback_destroy(callback);
    awaiting = false;
}
static const struct wl_callback_listener frame_listener = {.done = frame_done};

static void draw(void) {
    static const uint32_t colors[] = {0xff2266cc, 0xffcc6622, 0xff22aa44, 0xffaa2288, 0xff888822};
    struct swap_buffer*   b        = acquire();
    for (size_t i = 0; i < (size_t)WIDTH * HEIGHT; ++i)
        b->pixels[i] = colors[frames % 5];
    wl_surface_attach(surface, b->buffer, 0, 0);
    wl_surface_damage_buffer(surface, 0, 0, WIDTH, HEIGHT);
    wl_callback_add_listener(wl_surface_frame(surface), &frame_listener, NULL);
    wl_surface_commit(surface);
    b->busy  = true;
    awaiting = true;
    say("frame %d", ++frames);
    if (frames == 1)
        say("ready");
}

static void press(void) {
    pending = FRAMES_PER_PRESS;
}

static void configure(void* data, struct xdg_surface* xdg, uint32_t serial) {
    xdg_surface_ack_configure(xdg, serial);
    if (!frames && !pending)
        pending = 1;
}
static const struct xdg_surface_listener surface_listener = {.configure = configure};
static void toplevel_configure(void* data, struct xdg_toplevel* top, int32_t width, int32_t height, struct wl_array* states) {}
static void toplevel_close(void* data, struct xdg_toplevel* top) {
    exit(0);
}
static const struct xdg_toplevel_listener toplevel_listener = {.configure = toplevel_configure, .close = toplevel_close};

static void pointer_enter(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s, wl_fixed_t x, wl_fixed_t y) {}
static void pointer_leave(void* data, struct wl_pointer* p, uint32_t serial, struct wl_surface* s) {}
static void pointer_motion(void* data, struct wl_pointer* p, uint32_t time, wl_fixed_t x, wl_fixed_t y) {}
static void pointer_button(void* data, struct wl_pointer* p, uint32_t serial, uint32_t time, uint32_t button, uint32_t state) {
    if (state == WL_POINTER_BUTTON_STATE_PRESSED)
        press();
}
static void pointer_axis(void* data, struct wl_pointer* p, uint32_t time, uint32_t axis, wl_fixed_t value) {}
static const struct wl_pointer_listener pointer_listener = {
    .enter = pointer_enter, .leave = pointer_leave, .motion = pointer_motion, .button = pointer_button, .axis = pointer_axis};
static void keyboard_keymap(void* data, struct wl_keyboard* kb, uint32_t format, int32_t fd, uint32_t size) {
    close(fd);
}
static void keyboard_enter(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s, struct wl_array* keys) {}
static void keyboard_leave(void* data, struct wl_keyboard* kb, uint32_t serial, struct wl_surface* s) {}
static void keyboard_key(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t time, uint32_t key, uint32_t state) {
    if (state == WL_KEYBOARD_KEY_STATE_PRESSED)
        press();
}
static void keyboard_modifiers(void* data, struct wl_keyboard* kb, uint32_t serial, uint32_t dep, uint32_t lat, uint32_t locked, uint32_t group) {}
static const struct wl_keyboard_listener keyboard_listener = {
    .keymap = keyboard_keymap, .enter = keyboard_enter, .leave = keyboard_leave, .key = keyboard_key, .modifiers = keyboard_modifiers};
static struct seat_input seat_input = {.pointer_listener = &pointer_listener, .keyboard_listener = &keyboard_listener};

int main(int argc, char** argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: hyprmasq-swapchain-probe TITLE\n");
        return 2;
    }
    display = wl_display_connect(NULL);
    CHECK(display);
    queue = wl_display_create_queue(display);
    bind_globals(display,
                 (const struct probe_global[]){{.proxy = &compositor, .interface = &wl_compositor_interface, .version = 4},
                                               {.proxy = &wm, .interface = &xdg_wm_base_interface, .version = 1, .listener = &wm_listener},
                                               {.proxy = &seat, .interface = &wl_seat_interface, .version = 1, .listener = &seat_input_listener, .data = &seat_input},
                                               {.proxy = &dmabuf, .interface = &zwp_linux_dmabuf_v1_interface, .version = 3},
                                               {0}});
    open_card();
    for (int i = 0; i < BUFFERS; ++i)
        make_buffer(&buffers[i]);
    surface                 = wl_compositor_create_surface(compositor);
    struct xdg_surface* xdg = xdg_wm_base_get_xdg_surface(wm, surface);
    xdg_surface_add_listener(xdg, &surface_listener, NULL);
    struct xdg_toplevel* toplevel = xdg_surface_get_toplevel(xdg);
    xdg_toplevel_add_listener(toplevel, &toplevel_listener, NULL);
    xdg_toplevel_set_title(toplevel, argv[1]);
    wl_surface_commit(surface);

    // Frames are drawn here, outside of event handlers, as an app draws in its main loop, between dispatches.
    for (;;) {
        struct pollfd fds[1];
        if (!dispatch_display(display, fds, 1, -1))
            return 1;
        if (pending > 0 && !awaiting) {
            --pending;
            draw();
        }
    }
}
