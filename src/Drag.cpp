#include "Drag.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/debug/log/Logger.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/types/SurfaceRole.hpp>
#include <hyprutils/os/FileDescriptor.hpp>

#include <algorithm>
#include <bit>
#include <chrono>
#include <functional>
#include <ranges>
#include <utility>

#include <unistd.h>

namespace hyprmasq::drag {
    namespace {
        using std::chrono::milliseconds;
        using StartDrag = std::function<void(CWlDataDevice*, wl_resource*, wl_resource*, wl_resource*, uint32_t)>;

        // Hyprland keeps a data device's wire resource, its list of data devices and a wire resource's request handlers
        // private, and has no hook that sees a start_drag's serial or that lets a drag run without the seat. An explicit
        // instantiation may name private members, so these reach them without redefining access specifiers around the
        // includes. They bind the plugin to the pinned Hyprland's layout of these classes, as surfaceOf in Eis.cpp does.
        SP<CWlDataDevice>&                      wireOf(CWLDataDeviceResource& device);
        std::vector<SP<CWLDataDeviceResource>>& devicesOf(CWLDataDeviceProtocol& protocol);
        StartDrag&                              startDragOf(CWlDataDevice& wire);
        template <SP<CWlDataDevice> CWLDataDeviceResource::* Wire, std::vector<SP<CWLDataDeviceResource>> CWLDataDeviceProtocol::* Devices, auto Requests>
        struct SDataAccess {
            friend SP<CWlDataDevice>& wireOf(CWLDataDeviceResource& device) {
                return device.*Wire;
            }
            friend std::vector<SP<CWLDataDeviceResource>>& devicesOf(CWLDataDeviceProtocol& protocol) {
                return protocol.*Devices;
            }
            friend StartDrag& startDragOf(CWlDataDevice& wire) {
                return (wire.*Requests).startDrag;
            }
        };
        template struct SDataAccess<&CWLDataDeviceResource::m_resource, &CWLDataDeviceProtocol::m_devices, &CWlDataDevice::requests>;

        // How long a drag waits for emulated input before the drop, and for the destination's finish after it. A drag
        // whose sender stalls while it holds the button, and a destination that never finishes, end then.
        constexpr auto kIdleLimit   = std::chrono::seconds(10);
        constexpr auto kFinishLimit = std::chrono::seconds(10);
        // Limits on what a client can make the plugin keep: the offers of one drag, one for each surface it enters, and
        // the offers of all its drags that it has not destroyed.
        constexpr size_t   kMaxOffers       = 32;
        constexpr size_t   kMaxClientOffers = 64;
        constexpr uint32_t kActions         = WL_DATA_DEVICE_MANAGER_DND_ACTION_COPY | WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE | WL_DATA_DEVICE_MANAGER_DND_ACTION_ASK;

        struct SWrapped {
            WP<CWLDataDeviceResource> device;
            StartDrag                 original; // Hyprland's handler
        };
        std::vector<SWrapped>                      wrapped;
        const SRequest*                            current    = nullptr;
        SourcelessHandler                          sourceless = nullptr;

        std::vector<std::shared_ptr<CPrivateDrag>> drags;
        // Offers that the client destroyed, which go once the request that destroyed them has returned: an offer's
        // handler must not destroy the offer, and with it the handler, while it runs.
        std::vector<SP<CWlDataOffer>> buried;
        wl_event_source*              reaper = nullptr;

        void                          reap() {
            buried.clear();
            std::erase_if(drags, [](const auto& d) { return d->done() && d.use_count() == 1; });
        }

        void reapLater() {
            if (reaper)
                return;
            reaper = wl_event_loop_add_idle(
                g_pCompositor->m_wlEventLoop,
                [](void*) {
                    reaper = nullptr; // libwayland removes it after this call
                    reap();
                },
                nullptr);
        }

        bool offered(const SP<CWLDataSourceResource>& source, const char* mime) {
            return mime && std::ranges::contains(source->mimes(), std::string_view{mime});
        }
    }

    const SRequest* request() {
        return current;
    }

    void onSourceless(SourcelessHandler handler) {
        sourceless = handler;
    }

    void watchDevices(wl_client* client) {
        if (!PROTO::data)
            return;
        std::erase_if(wrapped, [](const auto& w) { return !w.device; });
        for (const auto& device : devicesOf(*PROTO::data)) {
            if (!device || device->client() != client || std::ranges::any_of(wrapped, [&](const auto& w) { return w.device == device; }))
                continue;
            auto& wire = wireOf(*device);
            if (!wire || !wire->resource())
                continue;
            auto& handler = startDragOf(*wire);
            wrapped.push_back({.device = device, .original = handler});
            handler = [original = handler, device = WP<CWLDataDeviceResource>(device)](CWlDataDevice* r, wl_resource* source, wl_resource* origin, wl_resource* icon,
                                                                                       uint32_t serial) {
                // Hyprland's handler would look up a null source, which the protocol allows for a drag within the client, so
                // that one never reaches it.
                if (!source) {
                    const SRequest request{.device = device, .serial = serial, .deviceVersion = r->version(), .sourceless = true};
                    const auto*    previous = std::exchange(current, &request);
                    if (sourceless && origin)
                        sourceless(CWLSurfaceResource::fromResource(origin), icon ? CWLSurfaceResource::fromResource(icon) : nullptr);
                    else
                        Log::logger->log(Log::WARN, "[hyprmasq] ignoring a drag without a source");
                    current = previous;
                    return;
                }
                const SRequest request{.device = device, .serial = serial, .deviceVersion = r->version(), .sourceVersion = wl_resource_get_version(source)};
                const auto*    previous = std::exchange(current, &request);
                original(r, source, origin, icon, serial);
                current = previous;
            };
        }
    }

    bool hasDevice(wl_client* client) {
        if (!PROTO::data)
            return false;
        return std::ranges::any_of(devicesOf(*PROTO::data), [&](const auto& device) {
            const auto& wire = device ? wireOf(*device) : nullptr;
            return wire && device->client() == client && wire->resource() && wire->version() >= 3;
        });
    }

    size_t offersOf(wl_client* client) {
        size_t n = 0;
        for (const auto& d : drags) {
            if (d->m_client == client)
                n += d->m_offers.size();
        }
        return n;
    }

    bool full(wl_client* client) {
        return offersOf(client) >= kMaxClientOffers;
    }

    std::shared_ptr<CPrivateDrag> start(const SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon, std::shared_ptr<SDragRecord> record) {
        if (!request.device || (!source && !request.sourceless) || !wireOf(*request.device))
            return nullptr;
        auto drag = std::make_shared<CPrivateDrag>(request, source, icon, std::move(record));
        drags.push_back(drag);
        drag->begin();
        return drag;
    }

    std::shared_ptr<CPrivateDrag> activeOn(wl_client* client) {
        const auto it = std::ranges::find_if(drags, [&](const auto& d) { return d->active() && d->client() == client; });
        return it == drags.end() ? nullptr : *it;
    }

    void cancelClient(wl_client* client, std::string_view reason) {
        if (auto drag = activeOn(client))
            drag->cancel(reason);
    }

    void shutdown() {
        for (const auto& d : std::vector{drags})
            d->cancel("unloaded");
        // The client may still send requests to the offers, which then go nowhere: their handlers are the plugin's
        // code. Destroying them would make such a request a protocol error that ends the client, so they are left to
        // the client, without handlers, until it destroys them or disconnects.
        for (const auto& d : drags) {
            for (auto& offer : d->m_offers) {
                offer->setAccept(nullptr);
                offer->setReceive(nullptr);
                offer->setDestroy(nullptr);
                offer->setFinish(nullptr);
                offer->setSetActions(nullptr);
                offer->setOnDestroy(nullptr);
                new SP<CWlDataOffer>(std::move(offer)); // NOLINT: kept as long as the client keeps it
            }
            d->m_offers.clear();
        }
        drags.clear();
        if (reaper)
            wl_event_source_remove(reaper);
        reaper = nullptr;
        buried.clear();
        for (auto& w : wrapped) {
            if (auto device = w.device.lock(); device && wireOf(*device))
                startDragOf(*wireOf(*device)) = std::move(w.original);
        }
        wrapped.clear();
        sourceless = nullptr;
    }

    CPrivateDrag::CPrivateDrag(const SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon, std::shared_ptr<SDragRecord> record) :
        m_record(std::move(record)), m_client(request.device->client()), m_device(request.device), m_source(source), m_sourceless(!source), m_icon(icon) {}

    CPrivateDrag::~CPrivateDrag() {
        if (m_timer)
            wl_event_source_remove(m_timer);
    }

    void CPrivateDrag::begin() {
        const auto source = m_source.lock();
        m_id              = ++m_record->id;
        ++m_record->started;
        ++m_record->unfinished;
        m_record->phase     = "active";
        m_record->privately = true;
        m_record->reason.clear();
        m_record->changed.emit();
        // Hyprland's own drags mark the source in its start_drag handler, which ran before.
        const auto weak = weak_from_this();
        if (source)
            m_sourceDestroy = source->m_events.destroy.listen([weak] {
                if (auto d = weak.lock()) {
                    d->m_sourceGone = true;
                    d->cancel("source_gone");
                }
            });
        if (const auto icon = m_icon.lock()) {
            // The pinned Hyprland has no role for a drag icon. The surface must have none, and taking one ends the drag.
            m_iconCommit  = icon->m_events.commit.listen([weak] {
                auto d    = weak.lock();
                auto icon = d ? d->m_icon.lock() : nullptr;
                if (icon && icon->m_role->role() != SURFACE_ROLE_UNASSIGNED)
                    d->cancel("icon");
            });
            m_iconDestroy = icon->m_events.destroy.listen([weak] {
                if (auto d = weak.lock()) {
                    d->m_icon.reset();
                    d->m_iconCommit.reset();
                    d->m_iconDestroy.reset();
                }
            });
        }
        m_timer = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, [](void* data) { return static_cast<CPrivateDrag*>(data)->tick(); }, this);
        if (m_timer)
            wl_event_source_timer_update(m_timer, 16);
    }

    // Cancels a drag that lost its source or its data device.
    bool CPrivateDrag::alive() {
        if (!m_sourceless && (m_sourceGone || !m_source)) {
            m_sourceGone = true;
            cancel("source_gone");
            return false;
        }
        if (m_phase == ePhase::ACTIVE && (!m_device || !wireOf(*m_device.lock()))) {
            cancel("device_gone");
            return false;
        }
        return m_phase != ePhase::ENDED;
    }

    void CPrivateDrag::motion(const SP<CWLSurfaceResource>& surface, const Vector2D& local) {
        if (m_phase != ePhase::ACTIVE || !alive())
            return;
        m_lastInput = Time::steadyNow();
        if (m_entered && surface && surface == m_target.lock()) {
            wireOf(*m_device.lock())->sendMotion(Time::millis(m_lastInput), wl_fixed_from_double(local.x), wl_fixed_from_double(local.y));
            return;
        }
        leave();
        if (surface)
            enter(surface, local);
    }

    // Enters the surface with a new offer: an offer belongs to the surface it entered.
    bool CPrivateDrag::enter(const SP<CWLSurfaceResource>& surface, const Vector2D& local) {
        const auto source = m_source.lock();
        const auto wire   = wireOf(*m_device.lock());
        const auto weak   = weak_from_this();
        // A drag within the client enters without an offer: the client passes its data itself.
        if (m_sourceless) {
            wire->sendEnterRaw(wl_display_next_serial(g_pCompositor->m_wlDisplay), surface->getResource()->resource(), wl_fixed_from_double(local.x), wl_fixed_from_double(local.y),
                               nullptr);
            m_entered       = true;
            m_target        = surface;
            m_targetDestroy = surface->m_events.destroy.listen([weak] {
                if (auto d = weak.lock(); d && d->m_phase == ePhase::ACTIVE)
                    d->leave();
            });
            return true;
        }
        if (m_offers.size() >= kMaxOffers) {
            cancel("limit");
            return false;
        }
        auto offer = makeShared<CWlDataOffer>(wire->client(), wire->version(), 0);
        if (!offer->resource()) {
            wire->noMemory();
            cancel("limit");
            return false;
        }
        // Each handler finds the drag again, and does nothing once it is gone or done with the offer.
        auto* raw = offer.get();
        offer->setAccept([weak, raw](CWlDataOffer*, uint32_t serial, const char* mime) {
            if (auto d = weak.lock())
                d->accept(raw, serial, mime);
        });
        offer->setSetActions([weak, raw](CWlDataOffer*, uint32_t actions, uint32_t preferred) {
            if (auto d = weak.lock())
                d->setActions(raw, actions, preferred);
        });
        offer->setReceive([weak, raw](CWlDataOffer*, const char* mime, int32_t fd) {
            if (auto d = weak.lock())
                d->receive(raw, mime, fd);
            else
                close(fd);
        });
        offer->setFinish([weak, raw](CWlDataOffer*) {
            if (auto d = weak.lock())
                d->finish(raw);
        });
        offer->setDestroy([weak, raw](CWlDataOffer*) {
            if (auto d = weak.lock())
                d->destroyed(raw);
        });
        offer->setOnDestroy([weak, raw](CWlDataOffer*) {
            if (auto d = weak.lock())
                d->destroyed(raw);
        });
        m_offers.push_back(offer);
        wire->sendDataOffer(raw);
        for (const auto& mime : source->mimes())
            offer->sendOffer(mime.c_str());
        offer->sendSourceActions(source->actions());
        wire->sendEnterRaw(wl_display_next_serial(g_pCompositor->m_wlDisplay), surface->getResource()->resource(), wl_fixed_from_double(local.x), wl_fixed_from_double(local.y),
                           offer->resource());
        m_offer   = raw;
        m_entered = true;
        m_target  = surface;
        m_mime.clear();
        m_action.reset();
        m_targetDestroy = surface->m_events.destroy.listen([weak] {
            if (auto d = weak.lock(); d && d->m_phase == ePhase::ACTIVE)
                d->leave();
        });
        return true;
    }

    // Leaves the surface entered, before the drop: the destination's offer is done with, and the source learns that
    // nothing accepts it.
    void CPrivateDrag::leave() {
        if (!m_entered)
            return;
        m_entered = false;
        m_target.reset();
        m_targetDestroy.reset();
        if (const auto device = m_device.lock(); device && wireOf(*device))
            wireOf(*device)->sendLeave();
        const auto source = m_sourceGone ? nullptr : m_source.lock();
        if (source && !m_mime.empty())
            source->accepted("");
        if (source && m_action.value_or(WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE) != WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE)
            source->sendDndAction(WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE);
        m_offer = nullptr;
        m_mime.clear();
        m_action.reset();
    }

    void CPrivateDrag::drop() {
        if (m_phase != ePhase::ACTIVE || !alive())
            return;
        if (!m_entered) {
            cancel("no_target");
            return;
        }
        // A drag within the client gets only enters, motion and leaves: the release ends it where the client takes it.
        if (m_sourceless) {
            leave();
            end("finished", "");
            return;
        }
        if (!m_offer || m_mime.empty() || m_action.value_or(WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE) == WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE) {
            cancel("rejected");
            return;
        }
        const auto wire = wireOf(*m_device.lock());
        wire->sendDrop();
        m_source.lock()->sendDndDropPerformed();
        // The destination's offer stays for the transfer and the finish, after the leave that ends the motion.
        m_entered = false;
        m_target.reset();
        m_targetDestroy.reset();
        wire->sendLeave();
        m_phase     = ePhase::DROPPED;
        m_droppedAt = Time::steadyNow();
        if (m_record->id == m_id) {
            m_record->phase = "dropped";
            m_record->changed.emit();
        }
        if (m_timer)
            wl_event_source_timer_update(m_timer, sc<int>(std::chrono::duration_cast<milliseconds>(kFinishLimit).count()));
    }

    void CPrivateDrag::cancel(std::string_view reason) {
        if (m_phase == ePhase::ENDED)
            return;
        const bool dropped = m_phase == ePhase::DROPPED;
        leave();
        if (const auto source = m_sourceGone ? nullptr : m_source.lock())
            source->cancelled();
        // After the drop, the destination may have taken the data: only its refusal tells that it did not.
        end(dropped && reason != "destination" ? "unconfirmed" : "cancelled", reason);
    }

    void CPrivateDrag::end(std::string_view phase, std::string_view reason) {
        m_phase = ePhase::ENDED;
        m_sourceDestroy.reset();
        m_targetDestroy.reset();
        m_iconCommit.reset();
        m_iconDestroy.reset();
        // An icon that waits for a frame callback gets its last one, so that its client does not wait on.
        if (const auto icon = m_icon.lock())
            icon->frame(Time::steadyNow());
        m_icon.reset();
        if (m_timer)
            wl_event_source_timer_update(m_timer, 0);
        --m_record->unfinished;
        if (phase == "finished")
            ++m_record->finished;
        else if (phase == "cancelled")
            ++m_record->cancelled;
        else
            ++m_record->unconfirmed;
        if (m_record->id == m_id) {
            m_record->phase  = phase;
            m_record->reason = reason;
        }
        m_record->endedAt = Time::steadyNow();
        m_record->end(m_id, phase, reason);
        m_record->changed.emit();
        Log::logger->log(Log::DEBUG, "[hyprmasq] private drag {} {}", phase, reason);
        reapLater();
    }

    // Sends the icon frame callbacks, which a hidden drag gets from nothing else, and ends a drag that lost its device
    // or waited too long.
    int CPrivateDrag::tick() {
        const auto now = Time::steadyNow();
        if (m_phase == ePhase::ACTIVE) {
            if (!alive())
                return 0;
            if (now - m_lastInput >= kIdleLimit) {
                cancel("timeout");
                return 0;
            }
            if (const auto icon = m_icon.lock())
                icon->frame(now);
            wl_event_source_timer_update(m_timer, 16);
        } else if (m_phase == ePhase::DROPPED) {
            if (now - m_droppedAt >= kFinishLimit)
                cancel("timeout");
            else
                wl_event_source_timer_update(m_timer, std::max(1, sc<int>(std::chrono::ceil<milliseconds>(m_droppedAt + kFinishLimit - now).count())));
        }
        return 0;
    }

    void CPrivateDrag::accept(CWlDataOffer* offer, uint32_t serial, const char* mime) {
        // An accept of an earlier enter's offer, or after the drop, changes nothing. Its serial need not be the enter's.
        if (m_phase != ePhase::ACTIVE || offer != m_offer || !alive())
            return;
        const auto source = m_source.lock();
        m_mime            = offered(source, mime) ? mime : "";
        source->accepted(m_mime);
    }

    // Chooses an action that both sides allow, COPY or MOVE, the destination's preferred one when it can. ASK, which
    // needs a choice after the drop, is not offered.
    void CPrivateDrag::setActions(CWlDataOffer* offer, uint32_t actions, uint32_t preferred) {
        if (m_phase != ePhase::ACTIVE || offer != m_offer || !alive())
            return;
        const auto source = m_source.lock();
        uint32_t   chosen = WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE;
        // An invalid mask or preferred action chooses none, so the drop is refused.
        if ((actions & ~kActions) == 0 && (preferred & ~actions) == 0 && std::popcount(preferred) <= 1) {
            const uint32_t common = actions & source->actions() & (WL_DATA_DEVICE_MANAGER_DND_ACTION_COPY | WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE);
            if (preferred & common)
                chosen = preferred;
            else if (common & WL_DATA_DEVICE_MANAGER_DND_ACTION_COPY)
                chosen = WL_DATA_DEVICE_MANAGER_DND_ACTION_COPY;
            else if (common & WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE)
                chosen = WL_DATA_DEVICE_MANAGER_DND_ACTION_MOVE;
        }
        // Both ends hear of a change only: each answers an action with requests of its own.
        if (m_action == chosen)
            return;
        m_action = chosen;
        offer->sendAction(chosen);
        source->sendDndAction(sc<wl_data_device_manager_dnd_action>(chosen));
    }

    // Hands the destination's file descriptor to the source, which writes the data into it. The compositor never reads
    // or writes it, and its copy closes once it is queued for the source. A destination may read before the drop, as a
    // preview, and more than once.
    void CPrivateDrag::receive(CWlDataOffer* offer, const char* mime, int32_t fd) {
        Hyprutils::OS::CFileDescriptor owned{fd};
        if (m_phase == ePhase::ENDED || offer != m_offer || m_sourceGone)
            return;
        const auto source = m_source.lock();
        if (source && offered(source, mime))
            source->send(mime, std::move(owned));
    }

    void CPrivateDrag::finish(CWlDataOffer* offer) {
        if (offer != m_offer)
            return;
        // A finish before the drop is the destination's error, which ends the drag.
        if (m_phase != ePhase::DROPPED) {
            if (m_phase == ePhase::ACTIVE)
                cancel("destination");
            return;
        }
        if (const auto source = m_sourceGone ? nullptr : m_source.lock())
            source->sendDndFinished();
        end("finished", "");
    }

    void CPrivateDrag::destroyed(CWlDataOffer* offer) {
        const auto it = std::ranges::find_if(m_offers, [&](const auto& o) { return o.get() == offer; });
        if (it == m_offers.end())
            return;
        buried.push_back(std::move(*it));
        m_offers.erase(it);
        reapLater();
        if (offer != m_offer)
            return;
        m_offer = nullptr;
        // A destination that destroys the offer after the drop without finishing refuses it. One that destroys it
        // before has accepted nothing.
        if (m_phase == ePhase::DROPPED)
            cancel("destination");
        else if (m_phase == ePhase::ACTIVE) {
            // The source learns that nothing accepts it any more.
            const auto source = m_sourceGone ? nullptr : m_source.lock();
            if (source && !m_mime.empty())
                source->accepted("");
            if (source && m_action.value_or(WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE) != WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE)
                source->sendDndAction(WL_DATA_DEVICE_MANAGER_DND_ACTION_NONE);
            m_mime.clear();
            m_action.reset();
        }
    }
}
