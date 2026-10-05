#pragma once
#include "Error.hpp"
#include "Windows.hpp"
#include <hyprland/src/helpers/time/Time.hpp>
#include <cstdint>
#include <expected>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace hyprmasq {
    namespace wait {
        class CContentWatch;
    }

    struct SEisInfo {
        std::string address, path;
        std::string id; // differs for every socket, also one opened again for the same window
        bool        force = false;
    };

    void initEis(void* handle);
    void shutdownEis();
    // force: drive X11 windows even while another window of their X server has the focus.
    std::expected<SEisInfo, SError> openEis(const PHLWINDOW& window, bool force);
    bool                            closeEis(const std::string& address);
    std::vector<SEisInfo>           listEis();

    struct SLeaseState {
        int         remainingMs = 0;
        std::string owner;
    };

    struct SSocketState {
        std::string                          path;
        std::string                          id; // of this socket, see SEisInfo
        bool                                 force = false;
        Time::steady_tp                      lastInput;            // when emulated input last reached the window
        int                                  framesSinceInput = 0; // commits since then
        std::shared_ptr<wait::CContentWatch> content;              // the window's commit counter, kept while the socket is open
        std::optional<SLeaseState>           lease;                // while one is held
    };
    // For a window with a socket. Main thread only.
    std::optional<SSocketState> socketState(const PHLWINDOW& window);

    // Starts or renews the focus lease of a window with a socket for ms milliseconds, see `lease` in docs/mcp.md.
    std::expected<SLeaseState, SError> holdLease(const PHLWINDOW& window, int ms, const std::string& owner);
    // Ends the window's lease, only if owner holds it when one is given. False if no lease ended.
    bool releaseLease(const PHLWINDOW& window, const std::optional<std::string>& owner);
    // True while a lease keeps the emulated pointer on the window.
    bool leaseKeepsPointer(const PHLWINDOW& window);

    // What input can do in a window at the moment, see `state` in docs/mcp.md.
    struct SCapabilities {
        bool lease = false, hold = false, dnd = false;
        bool x11Focus = false; // keys follow X input focus, which the plugin moves
    };
    SCapabilities capabilities(const PHLWINDOW& window);
}
