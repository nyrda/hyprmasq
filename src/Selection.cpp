#include "Selection.hpp"
#include "Eis.hpp"
#include "Hooks.hpp"
#include "PrivateAccess.hpp"
#include "X11.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/config/ConfigValue.hpp>
#include <hyprland/src/config/values/types/IntValue.hpp>
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/managers/SeatManager.hpp>
#include <hyprland/src/protocols/PrimarySelection.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/xwayland/Server.hpp>
#include <hyprland/src/xwayland/XWayland.hpp>

#include <array>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <ranges>
#include <vector>

namespace hyprmasq::selection {
    namespace {
        enum eKind : uint8_t {
            CLIPBOARD,
            PRIMARY,
        };

        // Which selection of a kind an app has: the user's, as Hyprland gives it, the agent's, or none.
        enum class eView : uint8_t {
            USER,
            AGENT,
            NONE,
        };

        // An app that emulated input reached, while it is connected.
        struct SClient {
            wl_listener     destroyed{};
            wl_client*      client = nullptr;
            Time::steady_tp agentAt{}, userAt{}; // the last input from the agent and from the user
            bool            agentInput = false, userInput = false;
            // What it was last given of each kind, and for the agent's, at which generation of offers.
            std::array<eView, 2> views{eView::USER, eView::USER};
            uint64_t             generation = 0;
        };
        std::map<wl_client*, std::unique_ptr<SClient>> clients;

        // The agent's selections.
        struct SSlot {
            WP<IDataSource>     source;
            CHyprSignalListener destroy;
        };
        std::array<SSlot, 2> slots;

        // Rises whenever Hyprland may have marked the offers it sent dead, or sent another selection: after a change of
        // the real focus or of a selection, and of the agent's.
        uint64_t generation = 1;

        // Hyprland has no hook that tells whose input a set_selection follows, so the plugin wraps the set_selection
        // handlers of data devices and primary selection devices.
        CHandlerWraps<CWLDataDeviceResource, CWlDataDevice, SetSelection>                         wrappedData{wireOf, setSelectionOf};
        CHandlerWraps<CPrimarySelectionDevice, CZwpPrimarySelectionDeviceV1, SetPrimarySelection> wrappedPrimary{wireOf, setSelectionOf};

        std::array<CHyprSignalListener, 4>                                                        seatListeners;
        wl_event_source*                                                                          refresher = nullptr;

        // Private members of the window manager, see SPinned at the end of this file.
        using SetClipboard                            = void (CXWM::*)(SXSelection&);
        using SelectionOwner                          = bool (CXWM::*)(xcb_xfixes_selection_notify_event_t*);
        HookFn<SetClipboard>   originalSetClipboard   = nullptr;
        HookFn<SelectionOwner> originalSelectionOwner = nullptr;
        // The X window that owns an X selection that stayed the agent's, per kind, or 0.
        std::array<uint32_t, 2> keptOwners{};

        SClient* find(wl_client* client) {
            const auto it = clients.find(client);
            return it == clients.end() ? nullptr : it->second.get();
        }

        // Whose a selection is that the client sets now: the agent's when the client has a driven window and got the
        // agent's input after the user's last, if any.
        bool agentSelects(wl_client* client) {
            const auto* c = find(client);
            return c && c->agentInput && drivesClient(client) && (!c->userInput || c->agentAt > c->userAt);
        }

        // Which selection of the kind the client should have now, or with keyboard or pointer, once emulated input has
        // those. The clipboard follows the keyboard and the primary selection the pointer, as Hyprland gives them, and the
        // primary selection pastes with keys too: the user's where the user's focus is and emulated input's is not,
        // otherwise the agent's. A client with emulated input's keyboard and the user's pointer, as a lease keeps it, gets
        // no primary selection, which either might paste.
        eView wanted(wl_client* client, eKind kind, bool keyboard, bool pointer) {
            keyboard |= emulatedKeyboardOn(client);
            pointer |= emulatedPointerOn(client);
            const bool realKeyboard = sharesConnection(client, g_pSeatManager->m_state.keyboardFocus), realPointer = sharesConnection(client, g_pSeatManager->m_state.pointerFocus);
            if (kind == CLIPBOARD)
                return keyboard || !realKeyboard ? eView::AGENT : eView::USER;
            if (pointer)
                return eView::AGENT;
            if (keyboard)
                return realPointer ? eView::NONE : eView::AGENT;
            return realPointer || realKeyboard ? eView::USER : eView::AGENT;
        }

        bool userSelection(const SP<IDataSource>& source) {
            return source && (source == g_pSeatManager->m_selection.currentSelection.lock() || source == g_pSeatManager->m_selection.currentPrimarySelection.lock());
        }

        // Sends the client's devices of the kind this selection, or none, through Hyprland's own offers, which pass a
        // paste's file descriptor to the source. Hyprland's XWayland gets X selections from the window manager instead.
        void send(wl_client* client, eKind kind, const SP<IDataSource>& source) {
            if (client == x11::xwaylandClient())
                return;
            if (kind == CLIPBOARD && PROTO::data) {
                for (const auto& device : std::vector{devicesOf(*PROTO::data)}) {
                    if (device && device->client() == client && wireOf(*device) && wireOf(*device)->resource())
                        sendTo(*PROTO::data, device, source);
                }
            }
            if (kind == PRIMARY && PROTO::primarySelection) {
                for (const auto& device : std::vector{devicesOf(*PROTO::primarySelection)}) {
                    if (device && device->client() == client && wireOf(*device) && wireOf(*device)->resource())
                        sendTo(*PROTO::primarySelection, device, source);
                }
            }
        }

        // Gives the client the selections it should have where they changed. With renew, as emulated input reaches it,
        // that is every kind, and the agent's again after Hyprland may have marked the offers of the earlier ones dead.
        // Without, it only gets the user's back where it has the agent's or none: Hyprland gives the user's on every
        // change of the real focus and selection, and an app that emulated input does not reach needs no fresh offers.
        void offer(SClient& c, bool renew, bool keyboard = false, bool pointer = false) {
            const bool fresh = c.generation == generation;
            for (auto kind : {CLIPBOARD, PRIMARY}) {
                const auto view = wanted(c.client, kind, keyboard, pointer);
                if (renew ? view == c.views[kind] && (view == eView::USER || fresh) : view != eView::USER || c.views[kind] == eView::USER)
                    continue;
                c.views[kind]      = view;
                const auto& seat   = g_pSeatManager->m_selection;
                const auto  source = view == eView::AGENT ? slots[kind].source.lock() :
                     view == eView::USER                  ? (kind == CLIPBOARD ? seat.currentSelection : seat.currentPrimarySelection).lock() :
                                                            nullptr;
                send(c.client, kind, source);
            }
            if (renew)
                c.generation = generation;
        }

        // After a change of the real focus or a selection: apps with emulated input's focus get fresh offers of the
        // agent's selections, as Hyprland marked the earlier ones dead, and apps with the agent's and the user's focus
        // the user's.
        void refresh() {
            for (const auto& [client, c] : clients)
                offer(*c, emulatedKeyboardOn(client) || emulatedPointerOn(client));
        }

        void changed() {
            ++generation;
            if (refresher)
                return;
            refresher = wl_event_loop_add_idle(
                g_pCompositor->m_wlEventLoop,
                [](void*) {
                    refresher = nullptr; // libwayland removes it after this call
                    refresh();
                },
                nullptr);
        }

        void clear(eKind kind) {
            slots[kind].source.reset();
            slots[kind].destroy.reset();
            changed();
        }

        // The agent's selection of the kind becomes the source, or none. The one it replaces gets a cancel, as Hyprland's
        // does, unless it has become the user's since.
        void setAgent(eKind kind, const SP<IDataSource>& source) {
            auto&      slot = slots[kind];
            const auto old  = slot.source.lock();
            if (old == source)
                return;
            slot.destroy.reset();
            slot.source = source;
            if (source) {
                source->markUsed();
                slot.destroy = source->m_events.destroy.listen([kind] { clear(kind); });
            }
            if (old && !userSelection(old))
                old->cancelled();
            Log::logger->log(Log::DEBUG, "[hyprmasq] the agent's {} is {:x}", kind == CLIPBOARD ? "clipboard" : "primary selection", rc<uintptr_t>(source.get()));
            changed();
        }

        // A source that the user's input sets is not the agent's any more.
        void forget(const SP<IDataSource>& source) {
            for (auto kind : {CLIPBOARD, PRIMARY}) {
                if (source && slots[kind].source.lock() == source) {
                    slots[kind].destroy.reset();
                    slots[kind].source.reset();
                    changed();
                }
            }
        }

        void watch(wl_client* client) {
            if (PROTO::data) {
                wrappedData.wrap(devicesOf(*PROTO::data), client, [](const SetSelection& original, const SP<CWLDataDeviceResource>&) -> SetSelection {
                    return [original](CWlDataDevice* r, wl_resource* sourceR, uint32_t serial) {
                        const auto source = sourceR ? CWLDataSourceResource::fromResource(sourceR) : nullptr;
                        if (!agentSelects(r->client())) {
                            forget(source);
                            original(r, sourceR, serial);
                            return;
                        }
                        setAgent(CLIPBOARD, source);
                    };
                });
            }
            if (PROTO::primarySelection) {
                wrappedPrimary.wrap(devicesOf(*PROTO::primarySelection), client,
                                    [](const SetPrimarySelection& original, const SP<CPrimarySelectionDevice>&) -> SetPrimarySelection {
                                        return [original](CZwpPrimarySelectionDeviceV1* r, wl_resource* sourceR, uint32_t serial) {
                                            const auto source = sourceR ? CPrimarySelectionSource::fromResource(sourceR) : nullptr;
                                            if (!agentSelects(r->client())) {
                                                forget(source);
                                                original(r, sourceR, serial);
                                                return;
                                            }
                                            // As Hyprland, which takes none while middle-click paste is off.
                                            CConfigValue<Config::INTEGER> middleClickPaste("misc:middle_click_paste");
                                            setAgent(PRIMARY, *middleClickPaste ? source : nullptr);
                                        };
                                    });
            }
        }

        SClient& record(wl_client* client) {
            if (auto* c = find(client))
                return *c;
            auto  c              = std::make_unique<SClient>();
            auto& ref            = *c;
            ref.client           = client;
            ref.destroyed.notify = [](wl_listener* listener, void*) {
                SClient* gone = wl_container_of(listener, gone, destroyed);
                wl_list_remove(&gone->destroyed.link);
                clients.erase(gone->client);
            };
            wl_client_add_destroy_listener(client, &ref.destroyed);
            clients.emplace(client, std::move(c));
            return ref;
        }

        // X apps of one X server share its selections. Hyprland's window manager learns of a new owner of one, and takes
        // the selection to Wayland once it knows its types, if an X window has the real focus. An X selection that the
        // agent's input made stays within the X server: the user's selections do not change, except that one of the
        // user's that came from X goes, since X would now read it from the agent's app. The window manager offers the
        // user's selection to X again once the real keyboard focus moves to an X window. When the agent's X selection
        // loses its owner, as when its app exits, the window manager would clear the user's selection of that kind, which
        // was never the agent's.
        bool selectionOwner(CXWM* self, xcb_xfixes_selection_notify_event_t* e) {
            auto*      found = selectionOf(*self, e->selection);
            const auto kind  = found == &clipboardOf(*self) ? std::optional{CLIPBOARD} : found == &primaryOf(*self) ? std::optional{PRIMARY} : std::nullopt;
            if (!kind)
                return originalSelectionOwner(self, e);
            auto& sel = *found;
            if (e->owner == XCB_WINDOW_NONE) {
                if (keptOwners[*kind] && sel.owner == keptOwners[*kind]) {
                    keptOwners[*kind] = 0;
                    sel.owner         = 0;
                    return true;
                }
                keptOwners[*kind] = 0;
                return originalSelectionOwner(self, e);
            }
            auto*      server = x11::xwaylandClient();
            const bool agent  = e->owner != sel.window && server && agentSelects(server);
            keptOwners[*kind] = agent ? e->owner : 0;
            const bool result = originalSelectionOwner(self, e);
            if (agent) {
                sel.notifyOnFocus   = true;
                const auto& current = *kind == CLIPBOARD ? g_pSeatManager->m_selection.currentSelection : g_pSeatManager->m_selection.currentPrimarySelection;
                if (current && current->type() == DATA_SOURCE_TYPE_X11) {
                    if (*kind == CLIPBOARD)
                        g_pSeatManager->setCurrentSelection(nullptr);
                    else
                        g_pSeatManager->setCurrentPrimarySelection(nullptr);
                }
                Log::logger->log(Log::DEBUG, "[hyprmasq] keeping the X {} of window {:x} from Wayland", *kind == CLIPBOARD ? "clipboard" : "primary selection", e->owner);
            }
            return result;
        }
        void setClipboardToWayland(CXWM* self, SXSelection& sel) {
            const auto kind = &sel == &clipboardOf(*self) ? std::optional{CLIPBOARD} : &sel == &primaryOf(*self) ? std::optional{PRIMARY} : std::nullopt;
            if (kind && keptOwners[*kind] && sel.owner == keptOwners[*kind])
                return;
            originalSetClipboard(self, sel);
        }
    }

    void init(void* handle) {
        originalSetClipboard   = hook<SetClipboard>(handle, "_ZN4CXWM21setClipboardToWaylandER11SXSelection", "CXWM::setClipboardToWayland(SXSelection&)", &setClipboardToWayland);
        originalSelectionOwner = hook<SelectionOwner>(handle, "_ZN4CXWM27handleSelectionXFixesNotifyEP35xcb_xfixes_selection_notify_event_t",
                                                      "CXWM::handleSelectionXFixesNotify(xcb_xfixes_selection_notify_event_t*)", &selectionOwner);
        auto& events           = g_pSeatManager->m_events;
        seatListeners = {events.keyboardFocusChange.listen([] { changed(); }), events.pointerFocusChange.listen([] { changed(); }), events.setSelection.listen([] { changed(); }),
                         events.setPrimarySelection.listen([] { changed(); })};
    }

    void emulatedInput(wl_client* client, bool keyboard, bool pointer) {
        if (!client)
            return;
        watch(client);
        auto& c      = record(client);
        c.agentInput = true;
        c.agentAt    = Time::steadyNow();
        if (keyboard || pointer)
            offer(c, true, keyboard, pointer);
    }

    void userInput(wl_client* client) {
        if (auto* c = client ? find(client) : nullptr) {
            c->userInput = true;
            c->userAt    = Time::steadyNow();
        }
    }

    void restore(wl_client* client) {
        if (auto* c = client ? find(client) : nullptr)
            offer(*c, false);
    }

    void shutdown() {
        for (auto& listener : seatListeners)
            listener.reset();
        if (refresher)
            wl_event_source_remove(refresher);
        refresher = nullptr;
        for (const auto& [client, c] : clients) {
            offer(*c, false);
            wl_list_remove(&c->destroyed.link);
        }
        clients.clear();
        for (auto& slot : slots) {
            slot.destroy.reset();
            if (const auto source = slot.source.lock(); source && !userSelection(source))
                source->cancelled();
            slot.source.reset();
        }
        // An X selection that stayed the agent's goes back to the window manager, with the user's Wayland selection of
        // that kind if there is one: once the hooks are gone, its owner's exit would clear the user's selection.
        if (g_pXWayland && g_pXWayland->m_wm) {
            for (auto kind : {CLIPBOARD, PRIMARY}) {
                auto& sel = kind == CLIPBOARD ? clipboardOf(*g_pXWayland->m_wm) : primaryOf(*g_pXWayland->m_wm);
                if (keptOwners[kind] && sel.owner == keptOwners[kind])
                    sel.onSelection();
            }
        }
        keptOwners = {};
        wrappedData.restore();
        wrappedPrimary.restore();
    }
}

template struct hyprmasq::SPinned<hyprmasq::selection::SetClipboard, &CXWM::setClipboardToWayland>;
template struct hyprmasq::SPinned<hyprmasq::selection::SelectionOwner, &CXWM::handleSelectionXFixesNotify>;
