// hyprmasq-input: a minimal libei sender for the per-window sockets that hyprmasq creates.
//
//   hyprmasq-input --socket PATH ACTION...
//
// Actions run in order. Coordinates are window-local logical pixels.
//   move X Y           move the pointer to X,Y
//   click [BUTTON]     press and release BUTTON (left, right, middle or an evdev code; default left)
//   down BUTTON        press BUTTON
//   up BUTTON          release BUTTON
//   scroll DX DY       smooth scroll by DX,DY
//   key CODE           press and release an evdev key code (see linux/input-event-codes.h)
//   type TEXT          type TEXT with the server's keymap; <enter>, <shortcut:ctrl+l>, <wait:MS> and other tags
//                      press keys or wait, and \< types a literal <
//   sleep MS           wait MS milliseconds

#include <libei.h>
#include <linux/input-event-codes.h>

#include <xkbcommon/xkbcommon-compose.h>
#include <xkbcommon/xkbcommon.h>

#include <ctype.h>
#include <errno.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>

#define TIMEOUT_MS     5000
#define SETTLE_MS      300
#define MAX_REPEAT     100
#define MAX_CHORD_MODS 8
#define EVDEV_OFFSET   8

struct state {
    struct ei*        ei;
    struct ei_device* pointer;
    struct ei_device* keyboard;
    bool              pointerReady;
    bool              keyboardReady;
    bool              pong;
    bool              paused;
    bool              disconnected;
    uint32_t          sequence;
    int               unconfirmed;
    bool              stalled;
    // Loaded from the keyboard device on the first type action.
    struct xkb_context*       xkb;
    struct xkb_keymap*        keymap;
    struct xkb_compose_table* compose;
    // For each modifier index, the evdev code of the key that sets it, or 0.
    uint32_t modifierKeys[32];
};

static void usage(FILE* out) {
    fputs("usage: hyprmasq-input --socket PATH ACTION...\n"
          "actions: move X Y | click [BUTTON] | down BUTTON | up BUTTON | scroll DX DY | key CODE | type TEXT | sleep MS\n",
          out);
}

static void handleEvent(struct state* s, struct ei_event* event) {
    struct ei_device* device = ei_event_get_device(event);

    switch (ei_event_get_type(event)) {
        case EI_EVENT_SEAT_ADDED:
            ei_seat_bind_capabilities(ei_event_get_seat(event), EI_DEVICE_CAP_POINTER_ABSOLUTE, EI_DEVICE_CAP_BUTTON, EI_DEVICE_CAP_SCROLL, EI_DEVICE_CAP_KEYBOARD, NULL);
            break;
        case EI_EVENT_DEVICE_ADDED:
            if (!s->pointer && ei_device_has_capability(device, EI_DEVICE_CAP_POINTER_ABSOLUTE))
                s->pointer = ei_device_ref(device);
            else if (!s->keyboard && ei_device_has_capability(device, EI_DEVICE_CAP_KEYBOARD))
                s->keyboard = ei_device_ref(device);
            break;
        case EI_EVENT_DEVICE_RESUMED:
            if (device == s->pointer)
                s->pointerReady = true;
            else if (device == s->keyboard)
                s->keyboardReady = true;
            else
                break;
            ei_device_start_emulating(device, ++s->sequence);
            break;
        case EI_EVENT_DEVICE_PAUSED:
            if (device == s->pointer)
                s->pointerReady = false;
            else if (device == s->keyboard)
                s->keyboardReady = false;
            else
                break;
            s->paused = true;
            break;
        case EI_EVENT_PONG: s->pong = true; break;
        case EI_EVENT_DISCONNECT: s->disconnected = true; break;
        default: break;
    }
}

// Dispatches until done() is true, the server disconnects, or the timeout expires.
static bool pump(struct state* s, bool (*done)(const struct state*)) {
    struct pollfd pfd  = {.fd = ei_get_fd(s->ei), .events = POLLIN};
    const int     step = 50;

    for (int waited = 0; !done(s) && waited < TIMEOUT_MS; waited += step) {
        if (s->disconnected)
            return false;
        if (poll(&pfd, 1, step) < 0 && errno != EINTR)
            return false;

        ei_dispatch(s->ei);
        for (struct ei_event* event; (event = ei_get_event(s->ei));) {
            handleEvent(s, event);
            ei_event_unref(event);
        }
    }

    return done(s) && !s->disconnected;
}

static bool devicesReady(const struct state* s) {
    return s->pointerReady && s->keyboardReady;
}

static bool gotPong(const struct state* s) {
    return s->pong;
}

// A ping round trip: once it returns, the server has processed every event sent before.
static bool roundTrip(struct state* s) {
    struct ei_ping* ping = ei_new_ping(s->ei);
    s->pong              = false;
    s->unconfirmed       = 0;
    ei_ping(ping);
    const bool ok = pump(s, gotPong);
    ei_ping_unref(ping);
    return ok;
}

static void frame(struct state* s, struct ei_device* device) {
    ei_device_frame(device, ei_now(s->ei));
    // libei 1.6 drops the rest of a message that a full socket only takes in part, and the server then disconnects on
    // the broken stream. Waiting for the server every few frames keeps the socket from filling up during long text.
    if (++s->unconfirmed >= 16 && !s->disconnected && !s->stalled && !roundTrip(s))
        s->stalled = true;
}

static bool parseButton(const char* name, uint32_t* button) {
    if (!strcmp(name, "left"))
        *button = BTN_LEFT;
    else if (!strcmp(name, "right"))
        *button = BTN_RIGHT;
    else if (!strcmp(name, "middle"))
        *button = BTN_MIDDLE;
    else {
        char*               end  = NULL;
        const unsigned long code = strtoul(name, &end, 0);
        if (!end || *end != '\0' || code == 0)
            return false;
        *button = code;
    }
    return true;
}

static bool parseDouble(const char* text, double* out) {
    char* end = NULL;
    *out      = strtod(text, &end);
    return end && *end == '\0';
}

static void sleepMs(long ms) {
    struct timespec ts = {.tv_sec = ms / 1000, .tv_nsec = (ms % 1000) * 1000000L};
    while (nanosleep(&ts, &ts) == -1 && errno == EINTR)
        ;
}

// A key and the modifier keys held around it.
struct chord {
    uint32_t code;
    uint32_t mods[MAX_CHORD_MODS];
    int      modCount;
};

static void pressChord(struct state* s, const struct chord* c) {
    for (int i = 0; i < c->modCount; ++i) {
        ei_device_keyboard_key(s->keyboard, c->mods[i], true);
        frame(s, s->keyboard);
    }
    ei_device_keyboard_key(s->keyboard, c->code, true);
    frame(s, s->keyboard);
    ei_device_keyboard_key(s->keyboard, c->code, false);
    frame(s, s->keyboard);
    for (int i = c->modCount - 1; i >= 0; --i) {
        ei_device_keyboard_key(s->keyboard, c->mods[i], false);
        frame(s, s->keyboard);
    }
}

static bool addMod(struct chord* c, uint32_t code) {
    for (int i = 0; i < c->modCount; ++i)
        if (c->mods[i] == code)
            return true;
    if (c->modCount == MAX_CHORD_MODS)
        return false;
    c->mods[c->modCount++] = code;
    return true;
}

// Keypad, ISO and extended keys. Keymaps put characters on them too, but a main-block key wins when there is one.
static bool offMainBlock(uint32_t code) {
    switch (code) {
        case KEY_KPASTERISK:
        case KEY_KP7 ... KEY_KPDOT:
        case KEY_102ND:
        case KEY_KPJPCOMMA:
        case KEY_KPENTER:
        case KEY_KPSLASH:
        case KEY_KPEQUAL:
        case KEY_KPPLUSMINUS:
        case KEY_KPCOMMA: return true;
        default: return code > KEY_COMPOSE;
    }
}

// xkeyboard-config's fake keys <LVL3>, <MDSW>, <ALT>, <META>, <SUPR> and <HYPR>, which no keyboard has.
static bool virtualKey(uint32_t code) {
    return code == 84 || (code >= 195 && code <= 199);
}

static bool keyBefore(uint32_t a, uint32_t b) {
    return offMainBlock(a) != offMainBlock(b) ? !offMainBlock(a) : a < b;
}

// Finds the key that sets each modifier while held, as the keymap defines it: Right Alt reaches the third level on most
// layouts, Caps Lock does on Neo. Keys whose modifier stays on after release, like Caps Lock on most layouts, are
// skipped. Left Shift wins over Right Shift by the lower code.
static void findModifierKeys(struct state* s) {
    const xkb_keycode_t max = xkb_keymap_max_keycode(s->keymap);
    for (xkb_keycode_t kc = xkb_keymap_min_keycode(s->keymap); kc <= max; ++kc) {
        if (kc < EVDEV_OFFSET || virtualKey(kc - EVDEV_OFFSET))
            continue;
        struct xkb_state* st = xkb_state_new(s->keymap);
        if (!st)
            return;
        xkb_state_update_key(st, kc, XKB_KEY_DOWN);
        const xkb_mod_mask_t held = xkb_state_serialize_mods(st, XKB_STATE_MODS_EFFECTIVE);
        xkb_state_update_key(st, kc, XKB_KEY_UP);
        const xkb_mod_mask_t after = xkb_state_serialize_mods(st, XKB_STATE_MODS_EFFECTIVE);
        xkb_state_unref(st);
        if (!held || after)
            continue;
        const uint32_t code = kc - EVDEV_OFFSET;
        for (int i = 0; i < 32; ++i)
            if ((held & (1u << i)) && (!s->modifierKeys[i] || keyBefore(code, s->modifierKeys[i])))
                s->modifierKeys[i] = code;
    }
}

static bool loadKeymap(struct state* s) {
    if (s->keymap)
        return true;
    struct ei_keymap* km = ei_device_keyboard_get_keymap(s->keyboard);
    if (!km || ei_keymap_get_type(km) != EI_KEYMAP_TYPE_XKB) {
        fprintf(stderr, "hyprmasq-input: the server did not send an XKB keymap\n");
        return false;
    }
    const size_t size = ei_keymap_get_size(km);
    char*        map  = mmap(NULL, size, PROT_READ, MAP_PRIVATE, ei_keymap_get_fd(km), 0);
    if (map == MAP_FAILED) {
        fprintf(stderr, "hyprmasq-input: cannot read the keymap: %s\n", strerror(errno));
        return false;
    }
    char* text = strndup(map, size);
    munmap(map, size);
    s->xkb = xkb_context_new(XKB_CONTEXT_NO_DEFAULT_INCLUDES | XKB_CONTEXT_NO_ENVIRONMENT_NAMES);
    if (text && s->xkb)
        s->keymap = xkb_keymap_new_from_string(s->xkb, text, XKB_KEYMAP_FORMAT_TEXT_V1, XKB_KEYMAP_COMPILE_NO_FLAGS);
    free(text);
    if (!s->keymap) {
        fprintf(stderr, "hyprmasq-input: cannot compile the keymap\n");
        return false;
    }
    findModifierKeys(s);
    return true;
}

// Picks the fewest modifier keys that reach a level through one of its masks.
static bool levelMods(const struct state* s, const xkb_mod_mask_t* masks, size_t count, struct chord* c) {
    bool found = false;
    for (size_t m = 0; m < count; ++m) {
        struct chord candidate = {.code = c->code};
        bool         ok        = true;
        for (int i = 0; ok && i < 32; ++i)
            if (masks[m] & (1u << i))
                ok = s->modifierKeys[i] && addMod(&candidate, s->modifierKeys[i]);
        if (ok && (!found || candidate.modCount < c->modCount)) {
            *c    = candidate;
            found = true;
        }
    }
    return found;
}

// A held key may set more modifiers than the level asks for, so the chord is checked against the keymap.
static bool chordProduces(const struct state* s, const struct chord* c, xkb_keysym_t sym) {
    struct xkb_state* st = xkb_state_new(s->keymap);
    if (!st)
        return false;
    for (int i = 0; i < c->modCount; ++i)
        xkb_state_update_key(st, c->mods[i] + EVDEV_OFFSET, XKB_KEY_DOWN);
    const bool ok = xkb_state_key_get_one_sym(st, c->code + EVDEV_OFFSET) == sym;
    xkb_state_unref(st);
    return ok;
}

// Finds the simplest chord that produces the keysym sym, or with sym 0, a keysym for the character codepoint:
// main-block keys first, then fewer modifiers, the lower level and the lower code. The plugin starts every connection
// in the keymap's first layout, so only that one is searched.
static bool findChord(const struct state* s, xkb_keysym_t sym, uint32_t codepoint, struct chord* best) {
    bool                found     = false;
    int                 bestLevel = 0;
    const xkb_keycode_t max       = xkb_keymap_max_keycode(s->keymap);
    for (xkb_keycode_t kc = xkb_keymap_min_keycode(s->keymap); kc <= max; ++kc) {
        if (kc < EVDEV_OFFSET || virtualKey(kc - EVDEV_OFFSET) || xkb_keymap_num_layouts_for_key(s->keymap, kc) == 0)
            continue;
        const xkb_level_index_t levels = xkb_keymap_num_levels_for_key(s->keymap, kc, 0);
        for (xkb_level_index_t level = 0; level < levels; ++level) {
            const xkb_keysym_t* syms;
            if (xkb_keymap_key_get_syms_by_level(s->keymap, kc, 0, level, &syms) != 1)
                continue;
            if (sym ? syms[0] != sym : xkb_keysym_to_utf32(syms[0]) != codepoint)
                continue;
            xkb_mod_mask_t masks[8];
            size_t         count = xkb_keymap_key_get_mods_for_level(s->keymap, kc, 0, level, masks, 8);
            if (count == 0 && level == 0)
                masks[count++] = 0;
            struct chord c = {.code = kc - EVDEV_OFFSET};
            if (!levelMods(s, masks, count, &c) || !chordProduces(s, &c, syms[0]))
                continue;
            if (found) {
                if (offMainBlock(c.code) != offMainBlock(best->code)) {
                    if (offMainBlock(c.code))
                        continue;
                } else if (c.modCount != best->modCount) {
                    if (c.modCount > best->modCount)
                        continue;
                } else if ((int)level != bestLevel ? (int)level > bestLevel : c.code > best->code)
                    continue;
            }
            *best     = c;
            bestLevel = level;
            found     = true;
        }
    }
    return found;
}

// Decodes one UTF-8 character; returns its length, or 0 if the bytes are not valid UTF-8.
static int decodeUtf8(const char* p, uint32_t* cp) {
    const unsigned char* u = (const unsigned char*)p;
    int                  len;
    uint32_t             min;
    if (u[0] < 0x80) {
        *cp = u[0];
        return 1;
    } else if ((u[0] & 0xE0) == 0xC0) {
        len = 2, min = 0x80, *cp = u[0] & 0x1F;
    } else if ((u[0] & 0xF0) == 0xE0) {
        len = 3, min = 0x800, *cp = u[0] & 0x0F;
    } else if ((u[0] & 0xF8) == 0xF0) {
        len = 4, min = 0x10000, *cp = u[0] & 0x07;
    } else
        return 0;
    for (int i = 1; i < len; ++i) {
        if ((u[i] & 0xC0) != 0x80)
            return 0;
        *cp = (*cp << 6) | (u[i] & 0x3F);
    }
    return *cp >= min && *cp <= 0x10FFFF && (*cp < 0xD800 || *cp > 0xDFFF) ? len : 0;
}

// One step of a type action: a chord, or a wait when code is 0.
struct step {
    struct chord chord;
    long         waitMs;
};

struct steps {
    struct step* items;
    size_t       count, capacity;
};

static bool addStep(struct steps* steps, struct step step) {
    if (steps->count == steps->capacity) {
        const size_t capacity = steps->capacity ? steps->capacity * 2 : 64;
        struct step* items    = realloc(steps->items, capacity * sizeof(*items));
        if (!items)
            return false;
        steps->items    = items;
        steps->capacity = capacity;
    }
    steps->items[steps->count++] = step;
    return true;
}

static bool loadCompose(struct state* s) {
    if (s->compose)
        return true;
    static const char* const variables[] = {"LC_ALL", "LC_CTYPE", "LANG"};
    const char*              locale      = NULL;
    for (size_t i = 0; i < 3 && !locale; ++i)
        if ((locale = getenv(variables[i])) && !*locale)
            locale = NULL;
    if (locale)
        s->compose = xkb_compose_table_new_from_locale(s->xkb, locale, XKB_COMPOSE_COMPILE_NO_FLAGS);
    if (!s->compose)
        s->compose = xkb_compose_table_new_from_locale(s->xkb, "C", XKB_COMPOSE_COMPILE_NO_FLAGS);
    return s->compose;
}

// Characters without a key of their own, such as ^ and ` on German layouts, are typed as the shortest sequence of
// the locale's Compose table that the keymap can press, usually a dead key followed by Space. GTK, Qt and foot apply
// that table to the keys they receive.
static bool addComposed(struct state* s, struct steps* steps, uint32_t codepoint) {
    if (!loadCompose(s))
        return false;
    struct chord*                      best       = NULL;
    size_t                             bestLength = 0;
    struct xkb_compose_table_iterator* it         = xkb_compose_table_iterator_new(s->compose);
    for (struct xkb_compose_table_entry* entry; it && (entry = xkb_compose_table_iterator_next(it));) {
        const char*         utf8 = xkb_compose_table_entry_utf8(entry);
        uint32_t            cp;
        size_t              length;
        const xkb_keysym_t* syms = xkb_compose_table_entry_sequence(entry, &length);
        if (!utf8 || decodeUtf8(utf8, &cp) != (int)strlen(utf8) || cp != codepoint || length == 0 || (best && length >= bestLength))
            continue;
        struct chord* chords = calloc(length, sizeof(*chords));
        size_t        i      = 0;
        while (chords && i < length && findChord(s, syms[i], 0, &chords[i]))
            ++i;
        if (chords && i == length) {
            free(best);
            best       = chords;
            bestLength = length;
        } else
            free(chords);
    }
    xkb_compose_table_iterator_free(it);
    bool ok = best;
    for (size_t i = 0; ok && i < bestLength; ++i)
        ok = addStep(steps, (struct step){.chord = best[i]});
    free(best);
    return ok;
}

static bool addChar(struct state* s, struct steps* steps, uint32_t codepoint) {
    struct step step    = {0};
    const bool  newline = codepoint == '\n' || codepoint == '\r';
    if (!findChord(s, newline ? XKB_KEY_Return : 0, codepoint, &step.chord)) {
        if (!newline && addComposed(s, steps, codepoint))
            return true;
        fprintf(stderr, "hyprmasq-input: the keymap cannot type U+%04X\n", codepoint);
        return false;
    }
    return addStep(steps, step);
}

// Named keys resolve through their keysyms, so they follow keymap options such as caps:swapescape.
static const struct {
    const char*  name;
    xkb_keysym_t sym;
} namedKeys[] = {
    {"space", XKB_KEY_space},   {"enter", XKB_KEY_Return}, {"tab", XKB_KEY_Tab},      {"esc", XKB_KEY_Escape},    {"backspace", XKB_KEY_BackSpace},
    {"delete", XKB_KEY_Delete}, {"up", XKB_KEY_Up},        {"down", XKB_KEY_Down},    {"left", XKB_KEY_Left},     {"right", XKB_KEY_Right},
    {"home", XKB_KEY_Home},     {"end", XKB_KEY_End},      {"pageup", XKB_KEY_Prior}, {"pagedown", XKB_KEY_Next},
};

static xkb_keysym_t namedKeysym(const char* name) {
    for (size_t i = 0; i < sizeof(namedKeys) / sizeof(namedKeys[0]); ++i)
        if (!strcmp(namedKeys[i].name, name))
            return namedKeys[i].sym;
    return XKB_KEY_NoSymbol;
}

// Keysyms without a printable character, such as Return, F5 or XF86AudioMute. Names of printable keysyms, like a or
// section, stay text, so that <a> and <section> type themselves.
static bool isKeyKeysym(xkb_keysym_t sym) {
    const uint32_t cp = xkb_keysym_to_utf32(sym);
    return sym != XKB_KEY_NoSymbol && (cp < 0x20 || (cp >= 0x7F && cp < 0xA0));
}

static bool parseCount(const char* text, long min, long max, long* out) {
    char* end = NULL;
    errno     = 0;
    *out      = strtol(text, &end, 10);
    return end != text && *end == '\0' && errno == 0 && *out >= min && *out <= max;
}

// The key of a shortcut: a named key, one character or a keysym name. Keys that need modifiers on this keymap bring
// them along, so ctrl+@ also holds AltGr on a German layout.
static bool shortcutKey(const struct state* s, const char* name, struct chord* c) {
    uint32_t           cp;
    const xkb_keysym_t named = namedKeysym(name);
    if (named)
        return findChord(s, named, 0, c);
    if (decodeUtf8(name, &cp) == (int)strlen(name) && cp)
        return findChord(s, 0, cp, c);
    const xkb_keysym_t sym = xkb_keysym_from_name(name, XKB_KEYSYM_CASE_INSENSITIVE);
    return sym != XKB_KEY_NoSymbol && findChord(s, sym, 0, c);
}

static bool addShortcut(const struct state* s, struct steps* steps, char* spec) {
    // The keys that set these modifiers in the keymap, so options such as ctrl:swap_lalt_lctl are followed.
    static const struct {
        const char* name;
        const char* modifier;
    } modifiers[] = {
        {"ctrl", XKB_MOD_NAME_CTRL},  {"control", XKB_MOD_NAME_CTRL}, {"shift", XKB_MOD_NAME_SHIFT}, {"alt", XKB_MOD_NAME_ALT},
        {"super", XKB_MOD_NAME_LOGO}, {"meta", XKB_MOD_NAME_LOGO},    {"win", XKB_MOD_NAME_LOGO},
    };
    struct step step = {0};
    char*       key  = strrchr(spec, '+');
    if (!key || key == spec || !key[1]) {
        fprintf(stderr, "hyprmasq-input: a shortcut needs modifiers and a key, as in <shortcut:ctrl+l>\n");
        return false;
    }
    *key++ = '\0';
    for (char* name = strtok(spec, "+"); name; name = strtok(NULL, "+")) {
        const char* modifier = NULL;
        for (size_t i = 0; i < sizeof(modifiers) / sizeof(modifiers[0]); ++i)
            if (!strcmp(modifiers[i].name, name))
                modifier = modifiers[i].modifier;
        if (!modifier) {
            fprintf(stderr, "hyprmasq-input: unknown shortcut modifier '%s'\n", name);
            return false;
        }
        const xkb_mod_index_t index = xkb_keymap_mod_get_index(s->keymap, modifier);
        if (index >= 32 || !s->modifierKeys[index]) {
            fprintf(stderr, "hyprmasq-input: no key for the modifier '%s' in the keymap\n", name);
            return false;
        }
        addMod(&step.chord, s->modifierKeys[index]);
    }
    struct chord keyChord = {0};
    if (!shortcutKey(s, key, &keyChord)) {
        fprintf(stderr, "hyprmasq-input: no key for '%s' in the keymap\n", key);
        return false;
    }
    for (int i = 0; i < keyChord.modCount; ++i)
        if (!addMod(&step.chord, keyChord.mods[i]))
            return false;
    step.chord.code = keyChord.code;
    return addStep(steps, step);
}

// Parses the tag between < and >. Returns 1 for a control, 0 for text that is typed as it is, and -1 on errors.
static int addTag(const struct state* s, struct steps* steps, const char* tag, size_t len) {
    char raw[128], lower[128];
    while (len && isspace((unsigned char)*tag))
        ++tag, --len;
    while (len && isspace((unsigned char)tag[len - 1]))
        --len;
    if (len == 0 || len >= sizeof(raw))
        return 0;
    memcpy(raw, tag, len);
    raw[len] = '\0';
    for (size_t i = 0; i <= len; ++i)
        lower[i] = tolower((unsigned char)raw[i]);

    long ms;
    if (!strcmp(lower, "settle"))
        return addStep(steps, (struct step){.waitMs = SETTLE_MS}) ? 1 : -1;
    if (!strncmp(lower, "wait:", 5)) {
        if (!parseCount(lower + 5, 0, 3600000, &ms)) {
            fprintf(stderr, "hyprmasq-input: <wait:MS> takes a number of milliseconds, got <%s>\n", raw);
            return -1;
        }
        return addStep(steps, (struct step){.waitMs = ms}) ? 1 : -1;
    }
    if (!strncmp(lower, "shortcut:", 9))
        return addShortcut(s, steps, lower + 9) ? 1 : -1;

    // A named key or a keysym, optionally with a repeat count.
    long  count = 1;
    char* colon = strchr(raw, ':');
    if (colon)
        *colon = lower[colon - raw] = '\0';
    struct step        step  = {0};
    const xkb_keysym_t named = namedKeysym(lower);
    const xkb_keysym_t sym   = named ? named : xkb_keysym_from_name(raw, XKB_KEYSYM_CASE_INSENSITIVE);
    if (!named && !isKeyKeysym(sym))
        return 0;
    if (!findChord(s, sym, 0, &step.chord)) {
        fprintf(stderr, "hyprmasq-input: no key for <%s> in the keymap\n", raw);
        return -1;
    }
    if (colon && !parseCount(colon + 1, 1, MAX_REPEAT, &count)) {
        fprintf(stderr, "hyprmasq-input: the repeat count in <%s:%s> must be 1 to %d\n", raw, colon + 1, MAX_REPEAT);
        return -1;
    }
    while (count--)
        if (!addStep(steps, step))
            return -1;
    return 1;
}

// Turns the text of a type action into steps, so nothing is sent when a part of it is invalid.
static bool parseText(struct state* s, const char* text, struct steps* steps) {
    for (const char* p = text; *p;) {
        uint32_t cp;
        if (p[0] == '\\' && p[1] == '<') {
            if (!addChar(s, steps, '<'))
                return false;
            p += 2;
            continue;
        }
        if (p[0] == '<') {
            const char* close = strchr(p + 1, '>');
            const char* open  = strchr(p + 1, '<');
            if (close && (!open || close < open)) {
                const int parsed = addTag(s, steps, p + 1, close - p - 1);
                if (parsed < 0)
                    return false;
                if (parsed > 0) {
                    p = close + 1;
                    continue;
                }
            }
        }
        if (p[0] == '\r' && p[1] == '\n') {
            ++p;
            continue;
        }
        const int len = decodeUtf8(p, &cp);
        if (!len) {
            fprintf(stderr, "hyprmasq-input: the text is not valid UTF-8\n");
            return false;
        }
        if (!addChar(s, steps, cp))
            return false;
        p += len;
    }
    return true;
}

// Runs the action at argv[0]; returns the number of arguments consumed, -1 for an invalid action, -2 for invalid input
// that was already reported, or -3 for a reported failure of the server.
static int runAction(struct state* s, int argc, char** argv) {
    const char* name = argv[0];
    double      x, y;
    uint32_t    button;

    if (!strcmp(name, "move") && argc >= 3 && parseDouble(argv[1], &x) && parseDouble(argv[2], &y)) {
        ei_device_pointer_motion_absolute(s->pointer, x, y);
        frame(s, s->pointer);
        return 3;
    }
    if (!strcmp(name, "click")) {
        int used = 1;
        button   = BTN_LEFT;
        if (argc >= 2 && parseButton(argv[1], &button))
            used = 2;
        ei_device_button_button(s->pointer, button, true);
        frame(s, s->pointer);
        ei_device_button_button(s->pointer, button, false);
        frame(s, s->pointer);
        return used;
    }
    if ((!strcmp(name, "down") || !strcmp(name, "up")) && argc >= 2 && parseButton(argv[1], &button)) {
        ei_device_button_button(s->pointer, button, !strcmp(name, "down"));
        frame(s, s->pointer);
        return 2;
    }
    if (!strcmp(name, "scroll") && argc >= 3 && parseDouble(argv[1], &x) && parseDouble(argv[2], &y)) {
        ei_device_scroll_delta(s->pointer, x, y);
        frame(s, s->pointer);
        ei_device_scroll_stop(s->pointer, x != 0, y != 0);
        frame(s, s->pointer);
        return 3;
    }
    if (!strcmp(name, "key") && argc >= 2) {
        char*         end  = NULL;
        unsigned long code = strtoul(argv[1], &end, 0);
        if (!end || *end != '\0' || code == 0)
            return -1;
        pressChord(s, &(struct chord){.code = code});
        return 2;
    }
    if (!strcmp(name, "type") && argc >= 2) {
        if (!loadKeymap(s))
            return -3;
        struct steps steps = {0};
        const bool   ok    = parseText(s, argv[1], &steps);
        for (size_t i = 0; ok && !s->stalled && !s->disconnected && i < steps.count; ++i) {
            if (steps.items[i].chord.code)
                pressChord(s, &steps.items[i].chord);
            else
                sleepMs(steps.items[i].waitMs);
        }
        free(steps.items);
        return ok ? 2 : -2;
    }
    if (!strcmp(name, "sleep") && argc >= 2) {
        sleepMs(strtol(argv[1], NULL, 10));
        return 2;
    }

    return -1;
}

int main(int argc, char** argv) {
    if (argc < 3 || strcmp(argv[1], "--socket") != 0) {
        usage(stderr);
        return 2;
    }

    struct state s = {0};
    s.ei           = ei_new_sender(&s);
    if (!s.ei)
        return 1;
    ei_configure_name(s.ei, "hyprmasq-input");

    int rc = ei_setup_backend_socket(s.ei, argv[2]);
    if (rc != 0) {
        fprintf(stderr, "hyprmasq-input: cannot connect to %s: %s\n", argv[2], strerror(-rc));
        ei_unref(s.ei);
        return 1;
    }

    int status = 0;
    if (!pump(&s, devicesReady)) {
        fprintf(stderr, "hyprmasq-input: the server did not offer a pointer and a keyboard\n");
        status = 1;
    }

    for (int i = 3; status == 0 && i < argc;) {
        int used = runAction(&s, argc - i, argv + i);
        if (used == -2 || used == -3) {
            status = used == -2 ? 2 : 1;
            break;
        }
        if (used < 0) {
            fprintf(stderr, "hyprmasq-input: invalid action at '%s'\n", argv[i]);
            usage(stderr);
            status = 2;
            break;
        }
        i += used;
    }

    // A ping round trip guarantees the server has processed every event before we disconnect.
    if (status == 0) {
        if (s.stalled || !roundTrip(&s)) {
            fprintf(stderr, "hyprmasq-input: the server did not confirm the events\n");
            status = 1;
        } else if (s.paused) {
            // hyprmasq pauses an X11 window's devices while another window of its X server has the focus.
            fprintf(stderr, "hyprmasq-input: the server paused input, so some events were dropped\n");
            status = 1;
        }
    }

    if (s.pointer) {
        if (s.pointerReady)
            ei_device_stop_emulating(s.pointer);
        ei_device_unref(s.pointer);
    }
    if (s.keyboard) {
        if (s.keyboardReady)
            ei_device_stop_emulating(s.keyboard);
        ei_device_unref(s.keyboard);
    }
    if (s.compose)
        xkb_compose_table_unref(s.compose);
    if (s.keymap)
        xkb_keymap_unref(s.keymap);
    if (s.xkb)
        xkb_context_unref(s.xkb);
    ei_unref(s.ei);
    return status;
}
