#pragma once
#include "Drag.hpp"
#include "Eis.hpp"
#include <hyprland/src/helpers/math/Math.hpp>
#include <hyprland/src/helpers/memory/Memory.hpp>
#include <hyprland/src/helpers/signal/Signal.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <libeis.h>
#include <xkbcommon/xkbcommon.h>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <set>
#include <string>
#include <string_view>
#include <vector>

class CWLDataSourceResource;
class CWLPointerResource;
class CWLSeatResource;
class CWLSurfaceResource;
struct wl_client;
struct wl_event_source;

// The input sockets behind Eis.hpp, one for each driven window, which Eis.cpp opens and closes. Main thread only.
namespace hyprmasq::input {
    // A focus lease, see holdLease.
    struct SLease {
        Time::steady_tp until;
        std::string     owner;
    };

    // A region of the emulated pointer device, in window-local logical pixels.
    struct SPointerRegion {
        uint32_t x = 0, y = 0, width = 0, height = 0;
        bool operator==(const SPointerRegion&) const = default;
    };

    // One window's input socket: its libeis context and sender, the emulated pointer and keyboard and the input they
    // deliver to the window's Wayland connection, the focus lease, and the private drags that its input starts.
    struct SSession {
        PHLWINDOWREF            window;
        std::string             path;
        std::string             id; // see SEisInfo
        eis*                    context  = nullptr;
        eis_client*             client   = nullptr;
        eis_seat*               seat     = nullptr;
        eis_device*             pointer  = nullptr;
        eis_device*             keyboard = nullptr;
        xkb_state*              state    = nullptr;
        wl_event_source*        fdSource = nullptr;
        wl_event_source*        timer    = nullptr;
        CHyprSignalListener     unmap, destroy, resize, keyboardFocus, pointerFocus;
        WP<CWLSurfaceResource>  enteredKeyboard, enteredPointer;
        std::vector<eis_event*> pending;
        std::set<uint32_t>      keys, buttons;
        Vector2D                position  = {1, 1};
        Time::steady_tp         lastInput = Time::steadyNow();
        // The window's commits from the socket's opening, for wait.
        std::shared_ptr<wait::CContentWatch> content;
        uint64_t                             commitsAtInput = 0;
        bool                                 force = false, paused = false, deferredKeys = false, restoreDue = false;
        bool                                 keyboardEmulating = false; // between the keyboard's start and stop of emulation
        Time::steady_tp                      pausedAt, holdUntil = Time::steadyNow(), lastKeys = Time::steadyNow(), lastPing;
        eis_event*                           stashed = nullptr; // taken from libeis, waiting for a hold to end
        // The device of a frame that waits for the user to let go, see userHolds, and since when it waits.
        eis_device*                    deferred = nullptr;
        std::optional<Time::steady_tp> deferredSince;
        const char*                    lastPause   = "";      // why the devices were last paused, see SSocketState; a literal
        wl_client*                     server      = nullptr; // the window's X server, looked up once
        bool                           serverKnown = false;
        std::vector<SPointerRegion>    regions; // of the pointer device, as announced
        wl_event_source*               regionCheck = nullptr;
        std::optional<SLease>          lease;
        // Set while Hyprland changes the real focus and a lease keeps this window's emulated focus, see CKeptFocus.
        bool keepingKeyboard = false, keepingPointer = false;
        // While the window keeps the emulated keyboard focus on the connection of the real focus, see keepKeyboard:
        // that connection's seat, and when it gives it back without further keys.
        bool                         keyboardKept = false;
        WP<CWLSeatResource>          keptSeat;
        Time::steady_tp              keysSettled;
        Time::steady_tp              lastCommand;     // the last press of a commandKey
        uint64_t                     interrupted = 0; // times the user's own input took the keyboard back before that one settled
        std::shared_ptr<SDragRecord> drags       = std::make_shared<SDragRecord>();
        // The serials of the presses that the window got from the sender, while their buttons are held: only they
        // start a private drag.
        struct SPress {
            uint32_t serial = 0, button = 0;
        };
        std::vector<SPress>               pressSerials;
        std::weak_ptr<drag::CPrivateDrag> drag;
        uint32_t                          dragButton = 0;               // the held button that started the drag, its drop
        Vector2D                          pointerAt  = position;        // the last motion, also outside the window
        const char*                       closing    = "socket_closed"; // why the session ends, for its drag; a literal

        SSession(const PHLWINDOW& w, std::string p, std::string i, bool f);
        ~SSession();
        void clearDevice(eis_device*& d);
        void clearClient();
        // tick() stops while keys or buttons are held. Once they are let go without further input, it has to run
        // again to give X focus back.
        void scheduleIdle();
        wl_client* xServer();
        SP<CWLSeatResource> wlSeat();
        // X focus outlives the sender, which disconnects right after its last event: apps such as games can drop input
        // that arrives just before they lose the foreground.
        void releaseX();
        void leaveKeyboard();
        void leavePointer();
        void leave();
        // Counts a drag-and-drop of the window's app that the plugin cancelled before it could start, and why.
        void dragCancelled(std::string_view reason);
        // The private drag of the window's app, before its drop.
        std::shared_ptr<drag::CPrivateDrag> activeDrag() const;
        void cancelDrag(std::string_view reason);
        // Why a drag that the window's app starts cannot run as a private drag, or none when it can: see "Drag-and-drop" in
        // docs/automation.md.
        std::optional<std::string_view> dragRefusal(const drag::SRequest* request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon);
        void beginDrag(const drag::SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon);
        // Moves the drag to the surface under the emulated pointer. Only the window's own surfaces take part: a point
        // outside the window and its popups is over none, and the drag leaves the surface it was over.
        void dragMotion(const std::shared_ptr<drag::CPrivateDrag>& d, const PHLWINDOW& w);
        // Lets go of the keys the sender holds. Without a lease the keyboard leaves the window, which releases them
        // for the application. With one, or while the window keeps the focus for a shortcut to settle, the focus
        // stays, so each key goes up.
        void releaseKeyboard();
        // Lets go of the buttons the sender holds. Without a lease the pointer leaves the window; with one it stays
        // where it is.
        void releasePointer();
        bool leased() const;
        std::optional<SLeaseState> leaseState() const;
        // The emulated focus then ends as without a lease, once input has paused for a second.
        void endLease();
        bool sharesKeyboardFocus();
        void makePointer();
        // Replaces the pointer device once its regions no longer match the window and its monitor, after a resize or
        // a move. Checked once the current batch of events is handled, since moves are seen inside Hyprland's own
        // updates.
        void checkRegionsLater();
        void makeKeyboard();
        // X focus and stacking are shared by every window of one X server. While another of them has the real
        // focus, moving them would misroute input, so only the focused window itself or force may proceed.
        bool blocked();
        // Senders see paused devices, so they can tell that input was refused.
        void setPaused(bool p);
        void pause(const char* reason);
        // libeis disconnects a sender whose events reach a resumed device before it saw the pause, so a sender still
        // busy sending when it was paused gets time to notice first.
        void maybeResume();
        void drop(eis_device* device);
        // Stops taking events from the socket until `until`, SATELLITE_BATCH from now by default. libeis answers a
        // sender's ping only once it is taken, so senders still learn when everything has been delivered.
        void hold(std::optional<Time::steady_tp> until = std::nullopt);
        // xwayland-satellite drops keys that share a batch of Wayland events with the keyboard leave that follows
        // them. True if this event would send that leave too early.
        bool leavesSatelliteTooSoon(eis_event* e);
        // Also while a frame waits for the user, so that the frames after it wait too.
        bool holding() const;
        void resumeHeld();
        void enterKeyboard(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& target, bool restore);
        // Apps such as Chromium take the window with the keyboard focus for the active one, and run a shortcut only
        // once the page has let its key go by, after the focus would have gone back to the user's window. A window of
        // the connection with the real focus keeps the emulated focus while its sender holds keys and until a command
        // key has settled; after typing, it goes back with every frame. The user's own input to the connection takes
        // it back first, see returnKeyboard, so that it reaches the user's window.
        void keepKeyboard(const SP<CWLSeatResource>& s);
        // Gives the connection's keyboard focus back to the surface of the real focus, also when the window's surface
        // is gone. byUser: the user's own input to the connection is about to be sent, which interrupts a command key
        // that has not settled.
        void returnKeyboard(bool byUser);
        void restoreKeyboard(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& real);
        void mods(const SP<CWLSeatResource>& s, bool toSender = true);
        void rememberPress(uint32_t serial, uint32_t button);
        // Input to a window of the connection with the real focus first takes that focus from the user's window, and
        // gives it back after the frame. A pointer's leave releases every button that it holds, as Hyprland's
        // sendLeave does, and a keyboard's leave makes apps let go of its keys, so a key the user holds stops
        // repeating. The reason for the pause, see SSocketState, while the user holds what the frame would release,
        // otherwise null.
        const char* userHolds(eis_device* device, const SP<CWLSeatResource>& s);
        // Sends the window the events of a frame of the device that the sender ended, or drops them.
        void deliver(eis_device* device);
        // What deliver() knows of the frame it sends, for the helpers that send its events.
        struct SFrame {
            PHLWINDOW           window;
            SP<CWLSeatResource> seat;                    // of the window's connection
            uint64_t            time            = 0;     // of the events, in milliseconds
            bool                restore         = false; // the real focus is on the window's connection, and goes back after the frame
            bool                trigger         = false; // the frame has input that can open a window, see placement::triggers
            bool                focusedKeyboard = false; // a press gave the window the keyboard focus
        };
        // The frame waits while the user holds what it would release, and is dropped with a pause if they hold
        // it longer, so that the sender learns that its input did not arrive. True if it waits or was dropped.
        bool waitsForUser(eis_device* device, const SP<CWLSeatResource>& s);
        // Another socket may address a second window on this same Wayland client. The client's keyboard and
        // pointer are on one surface at a time, so that window loses its emulated focus and its lease.
        void takeConnection(const SP<CWLSeatResource>& s);
        // Sends a key event of the frame, or a pointer, button or scroll event.
        void deliverKey(SFrame& frame, eis_event* e);
        void deliverPointer(SFrame& frame, eis_event* e);
        // Sends a pointer, button or scroll event of the frame to one of the connection's pointer resources, at local on
        // target.
        void sendPointerEvent(const SFrame& frame, const WP<CWLPointerResource>& p, eis_event* e, const SP<CWLSurfaceResource>& target, const Vector2D& local);
        // Gives the connection's pointer focus back to the surface of the real focus, where the user's pointer is.
        void restorePointer(const SP<CWLSeatResource>& s, const SP<CWLSurfaceResource>& real);
        int tick();
        int poll();
        // The window is gone, so what the sender still has in the socket can only be dropped. Taking it answers the
        // pings among it, so a sender whose input closed the window, such as exit in a shell, learns that the plugin
        // processed everything before them instead of losing the answers with the connection. A ping after dropped
        // input must not confirm it, so the devices are removed first: senders take no answer after that as a
        // confirmation.
        void answerPings();
        void process();
    };

    // Every open socket, by the address of its window.
    extern std::map<std::string, std::unique_ptr<SSession>> sessions;
}
