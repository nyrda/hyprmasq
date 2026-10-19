#pragma once

#include "Eis.hpp"

#include <hyprland/src/helpers/math/Math.hpp>
#include <hyprland/src/helpers/memory/Memory.hpp>
#include <hyprland/src/helpers/signal/Signal.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

class CWLDataDeviceResource;
class CWLDataSourceResource;
class CWLSurfaceResource;
class CWlDataOffer;
struct wl_client;
struct wl_event_source;

// A private drag-and-drop: one that a driven window's app starts from emulated input, which the plugin runs for that
// app's own connection, without Hyprland's drag controller, a grab of the seat that takes the user's pointer. It offers
// the drag only on the data device that started it, enters only the surfaces that the emulated pointer is over, and
// passes the file descriptors of a transfer from the destination to the source, without touching the data. See
// "Drag-and-drop" in docs/usage.md.
namespace hyprmasq::drag {
    // A start_drag request as the data device that got it saw it. Hyprland's handler drops the serial before it calls
    // CWLDataDeviceProtocol::initiateDrag.
    struct SRequest {
        WP<CWLDataDeviceResource> device;
        uint32_t                  serial        = 0;
        int                       deviceVersion = 0, sourceVersion = 0;
        bool                      sourceless = false; // a drag within the client, which passes its data itself
    };
    // The start_drag request that Hyprland handles at the moment, of a data device that watchDevices wrapped, or null.
    const SRequest* request();
    // Wraps the start_drag handler of the client's data devices, also of ones created since the last call, so that
    // request() tells the request while Hyprland's handler runs. shutdown() puts Hyprland's handlers back.
    void watchDevices(wl_client* client);
    // True if the client has a data device of version 3 or later: a private drag needs its actions.
    bool hasDevice(wl_client* client);
    // Handles a start_drag without a source, which Hyprland's handler cannot take, during it, with the drag's origin and
    // icon: FocusPolicy.cpp runs it privately or refuses it.
    using SourcelessHandler = void (*)(const SP<CWLSurfaceResource>& origin, const SP<CWLSurfaceResource>& icon);
    void onSourceless(SourcelessHandler handler);
    // How many data offers of private drags the client has not destroyed yet.
    size_t offersOf(wl_client* client);
    // True when the client keeps so many offers of earlier drags that it gets no more.
    bool full(wl_client* client);

    class CPrivateDrag : public std::enable_shared_from_this<CPrivateDrag> {
      public:
        CPrivateDrag(const SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon, std::shared_ptr<SDragRecord> record);
        ~CPrivateDrag();
        CPrivateDrag(const CPrivateDrag&)            = delete;
        CPrivateDrag& operator=(const CPrivateDrag&) = delete;

        // Before the drop.
        bool active() const {
            return m_phase == ePhase::ACTIVE;
        }
        bool ended() const {
            return m_phase == ePhase::ENDED;
        }
        // Ended, and the client destroyed every offer of it.
        bool done() const {
            return ended() && m_offers.empty();
        }
        wl_client* client() const {
            return m_client;
        }
        // The emulated pointer moved over the surface, at a point in its coordinates, or over none of the window.
        void motion(const SP<CWLSurfaceResource>& surface, const Vector2D& local);
        // The emulated button that started the drag was released: a drop where the destination accepted a type and an
        // action, otherwise a cancel.
        void drop();
        // Cancels the drag for the source, or before the drop, gives up on it.
        void cancel(std::string_view reason);

      private:
        enum class ePhase : uint8_t {
            ACTIVE,
            DROPPED,
            ENDED,
        };

        friend std::shared_ptr<CPrivateDrag> start(const SRequest&, const SP<CWLDataSourceResource>&, const SP<CWLSurfaceResource>&, std::shared_ptr<SDragRecord>);
        friend void shutdown();
        friend size_t offersOf(wl_client*);

        void begin();
        bool alive();
        bool enter(const SP<CWLSurfaceResource>& surface, const Vector2D& local);
        void leave();
        void end(std::string_view phase, std::string_view reason);
        int tick();
        void accept(CWlDataOffer* offer, uint32_t serial, const char* mime);
        void setActions(CWlDataOffer* offer, uint32_t actions, uint32_t preferred);
        void receive(CWlDataOffer* offer, const char* mime, int32_t fd);
        void finish(CWlDataOffer* offer);
        void destroyed(CWlDataOffer* offer);

        std::shared_ptr<SDragRecord> m_record;
        uint64_t                     m_id     = 0; // in the record, so that a later drag's outcome is not overwritten
        wl_client*                   m_client = nullptr;
        WP<CWLDataDeviceResource>    m_device;
        WP<CWLDataSourceResource>    m_source;
        bool                         m_sourceGone = false; // the client destroyed it, so it gets no more events
        bool                         m_sourceless = false; // a drag within the client: no offers, and no drop
        WP<CWLSurfaceResource>       m_icon;
        // Every offer of the drag that the client has not destroyed, one for each surface it entered.
        std::vector<SP<CWlDataOffer>> m_offers;
        // The offer of the surface entered, and after the drop, the offer that it went to.
        CWlDataOffer*           m_offer   = nullptr;
        bool                    m_entered = false; // a surface, which may be gone since
        WP<CWLSurfaceResource>  m_target;
        std::string             m_mime;   // that the offer accepted, or none
        std::optional<uint32_t> m_action; // chosen for the offer, once it set its actions
        ePhase                  m_phase     = ePhase::ACTIVE;
        Time::steady_tp         m_lastInput = Time::steadyNow(), m_droppedAt;
        wl_event_source*        m_timer     = nullptr;
        CHyprSignalListener     m_sourceDestroy, m_iconCommit, m_iconDestroy, m_targetDestroy;
    };

    // Starts a private drag on the request's data device, counted in record, or returns null when the device or the
    // source is gone. It enters no surface yet: the caller sends the first motion.
    std::shared_ptr<CPrivateDrag> start(const SRequest& request, const SP<CWLDataSourceResource>& source, const SP<CWLSurfaceResource>& icon, std::shared_ptr<SDragRecord> record);
    // The private drag of the client before its drop, or null.
    std::shared_ptr<CPrivateDrag> activeOn(wl_client* client);
    // Cancels the private drag of the client before its drop, if any: the user's own input reaches the client.
    void cancelClient(wl_client* client, std::string_view reason);
    // Ends every private drag and stops answering the requests of their offers. Call before the plugin unloads.
    void shutdown();
}
