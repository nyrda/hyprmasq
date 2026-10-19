// What the VM test's probes in nix/ share: checks that also run with NDEBUG, strict parsing of arguments, log lines
// stamped with CLOCK_MONOTONIC, binding globals, one step of a Wayland event loop, timers, signals and shm buffers.
//
// Each probe is one file that defines _GNU_SOURCE and includes this header once. The xdg-shell part is there for the
// probes that include xdg-shell-client-protocol.h before it.
#ifndef PROBE_COMMON_H
#define PROBE_COMMON_H

#ifndef _GNU_SOURCE
#error "define _GNU_SOURCE before any include"
#endif

#include <errno.h>
#include <math.h>
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
#include <wayland-client.h>

// Evaluates expr, also with NDEBUG, unlike assert, and aborts naming it and errno when it is false. It clears errno
// first, so that the errno it reports comes from expr.
#define CHECK(expr)                                                                                                                                                                \
    do {                                                                                                                                                                           \
        errno = 0;                                                                                                                                                                 \
        if (!(expr))                                                                                                                                                               \
            check_failed(#expr, __FILE__, __LINE__);                                                                                                                               \
    } while (0)

__attribute__((noreturn)) static inline void check_failed(const char* expr, const char* file, int line) {
    const int error = errno;
    fflush(stdout);
    fprintf(stderr, "%s: %s:%d: check failed: %s", program_invocation_short_name, file, line, expr);
    if (error)
        fprintf(stderr, ": %s", strerror(error));
    fputc('\n', stderr);
    abort();
}

// Parses an integer argument from min to max, or exits with status 2, naming the argument.
static inline int parse_int(const char* text, const char* name, int min, int max) {
    char* end;
    errno            = 0;
    const long value = strtol(text, &end, 10);
    if (end == text || *end || errno || value < min || value > max) {
        fprintf(stderr, "%s: %s must be an integer from %d to %d, not '%s'\n", program_invocation_short_name, name, min, max, text);
        exit(2);
    }
    return (int)value;
}

// Parses a finite number argument, or exits with status 2, naming the argument.
static inline double parse_double(const char* text, const char* name) {
    char* end;
    errno              = 0;
    const double value = strtod(text, &end);
    if (end == text || *end || errno || !isfinite(value)) {
        fprintf(stderr, "%s: %s must be a number, not '%s'\n", program_invocation_short_name, name, text);
        exit(2);
    }
    return value;
}

// CLOCK_MONOTONIC in milliseconds, which the test reads in the VM to compare with the probes' logs.
static inline uint64_t now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
}

// Whether say() starts its lines with now_ms() and a space.
static bool say_stamped = true;

// Prints a log line and flushes it.
__attribute__((format(printf, 1, 2))) static inline void say(const char* format, ...) {
    if (say_stamped)
        printf("%llu ", (unsigned long long)now_ms());
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    putchar('\n');
    fflush(stdout);
}

// A global that a probe binds: a pointer to its proxy variable, its interface and version, the listener to add as it
// binds it, so that it misses none of its events, with that listener's data, and whether the probe can do without it.
struct probe_global {
    void*                      proxy;
    const struct wl_interface* interface;
    uint32_t                   version;
    const void*                listener;
    void*                      data;
    bool                       optional;
};

static inline void probe_global_added(void* data, struct wl_registry* registry, uint32_t name, const char* interface, uint32_t version) {
    for (const struct probe_global* global = data; global->proxy; ++global) {
        if (strcmp(interface, global->interface->name))
            continue;
        struct wl_proxy* proxy = wl_registry_bind(registry, name, global->interface, global->version);
        if (global->listener)
            wl_proxy_add_listener(proxy, (void (**)(void))global->listener, global->data);
        // The variable has the proxy's own pointer type.
        memcpy(global->proxy, &proxy, sizeof(proxy));
    }
}
static inline void probe_global_removed(void* data, struct wl_registry* registry, uint32_t name) {}

// Binds the globals that the compositor has, from a list that ends with an entry without a proxy, and exits with
// status 1 naming a required one that it lacks. Globals added later are not bound.
static inline void bind_globals(struct wl_display* display, const struct probe_global* globals) {
    static const struct wl_registry_listener listener = {.global = probe_global_added, .global_remove = probe_global_removed};
    struct wl_registry*                      registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &listener, (void*)globals);
    CHECK(wl_display_roundtrip(display) >= 0);
    wl_registry_destroy(registry);
    for (const struct probe_global* global = globals; global->proxy; ++global) {
        void* proxy;
        memcpy(&proxy, global->proxy, sizeof(proxy));
        if (!proxy && !global->optional) {
            fprintf(stderr, "%s: the compositor has no %s\n", program_invocation_short_name, global->interface->name);
            exit(1);
        }
    }
}

// The seat's pointer and keyboard, which a probe gets with its listeners once the seat has them. It is the data of
// seat_input_listener.
struct seat_input {
    const struct wl_pointer_listener*  pointer_listener;
    const struct wl_keyboard_listener* keyboard_listener;
    struct wl_pointer*                 pointer;
    struct wl_keyboard*                keyboard;
};
static inline void seat_input_capabilities(void* data, struct wl_seat* seat, uint32_t capabilities) {
    struct seat_input* input = data;
    if ((capabilities & WL_SEAT_CAPABILITY_POINTER) && !input->pointer) {
        input->pointer = wl_seat_get_pointer(seat);
        wl_pointer_add_listener(input->pointer, input->pointer_listener, NULL);
    }
    if ((capabilities & WL_SEAT_CAPABILITY_KEYBOARD) && !input->keyboard) {
        input->keyboard = wl_seat_get_keyboard(seat);
        wl_keyboard_add_listener(input->keyboard, input->keyboard_listener, NULL);
    }
}
static const struct wl_seat_listener seat_input_listener = {.capabilities = seat_input_capabilities};

// Flushes the display, waits until one of fds is readable, or timeout_ms as poll() takes it, and dispatches the
// display's events. It fills in fds[0], the display's. Returns false once the connection failed.
static inline bool dispatch_display(struct wl_display* display, struct pollfd* fds, nfds_t count, int timeout_ms) {
    fds[0] = (struct pollfd){.fd = wl_display_get_fd(display), .events = POLLIN};
    while (wl_display_prepare_read(display) != 0)
        wl_display_dispatch_pending(display);
    if (wl_display_flush(display) < 0 || poll(fds, count, timeout_ms) < 0) {
        wl_display_cancel_read(display);
        return false;
    }
    if (fds[0].revents & POLLIN) {
        if (wl_display_read_events(display) < 0)
            return false;
    } else
        wl_display_cancel_read(display);
    return wl_display_dispatch_pending(display) >= 0;
}

// A CLOCK_MONOTONIC timerfd.
static inline int timer_new(void) {
    const int timer = timerfd_create(CLOCK_MONOTONIC, TFD_CLOEXEC);
    CHECK(timer >= 0);
    return timer;
}

// Arms a timer to expire once ms milliseconds from now, or at once with 0.
static inline void timer_arm_ms(int timer, uint64_t ms) {
    struct itimerspec in = {.it_value = {.tv_sec = (time_t)(ms / 1000), .tv_nsec = (long)(ms % 1000) * 1000000}};
    if (!ms)
        in.it_value.tv_nsec = 1; // 0 would disarm it
    CHECK(timerfd_settime(timer, 0, &in, NULL) == 0);
}

// Reads the expirations of a timer that poll() found readable.
static inline void timer_read(int timer) {
    uint64_t expirations;
    CHECK(read(timer, &expirations, sizeof(expirations)) == sizeof(expirations));
}

// Blocks the signals, a list that ends with 0, and returns a signalfd that reads them.
static inline int signals_new(int signal, ...) {
    sigset_t mask;
    sigemptyset(&mask);
    va_list signals;
    va_start(signals, signal);
    for (; signal; signal = va_arg(signals, int))
        sigaddset(&mask, signal);
    va_end(signals);
    CHECK(sigprocmask(SIG_BLOCK, &mask, NULL) == 0);
    const int fd = signalfd(-1, &mask, SFD_CLOEXEC);
    CHECK(fd >= 0);
    return fd;
}

// Reads the number of a signal that poll() found readable on a signalfd.
static inline uint32_t signal_read(int fd) {
    struct signalfd_siginfo info;
    CHECK(read(fd, &info, sizeof(info)) == sizeof(info));
    return info.ssi_signo;
}

static inline void shm_buffer_release(void* data, struct wl_buffer* buffer) {
    wl_buffer_destroy(buffer);
}
static const struct wl_buffer_listener shm_buffer_listener = {.release = shm_buffer_release};

// A buffer of width×height XRGB8888 pixels in color, which destroys itself once the compositor releases it. paint,
// unless NULL, draws on the pixels first.
static inline struct wl_buffer* shm_buffer(struct wl_shm* shm, int width, int height, uint32_t color, void (*paint)(uint32_t* pixels, int width, int height)) {
    const size_t size = (size_t)width * height * 4;
    const int    fd   = memfd_create(program_invocation_short_name, MFD_CLOEXEC);
    CHECK(fd >= 0);
    CHECK(ftruncate(fd, (off_t)size) == 0);
    uint32_t* pixels = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    CHECK(pixels != MAP_FAILED);
    for (size_t i = 0; i < size / 4; ++i)
        pixels[i] = color;
    if (paint)
        paint(pixels, width, height);
    struct wl_shm_pool* pool   = wl_shm_create_pool(shm, fd, (int32_t)size);
    struct wl_buffer*   buffer = wl_shm_pool_create_buffer(pool, 0, width, height, width * 4, WL_SHM_FORMAT_XRGB8888);
    wl_buffer_add_listener(buffer, &shm_buffer_listener, NULL);
    wl_shm_pool_destroy(pool);
    munmap(pixels, size);
    close(fd);
    return buffer;
}

#ifdef XDG_SHELL_CLIENT_PROTOCOL_H
static inline void pong(void* data, struct xdg_wm_base* wm, uint32_t serial) {
    xdg_wm_base_pong(wm, serial);
}
// Answers the compositor's pings, as every xdg-shell client must.
static const struct xdg_wm_base_listener pong_listener = {.ping = pong};
#endif

#endif
