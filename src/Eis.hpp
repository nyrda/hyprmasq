#pragma once
#include "Windows.hpp"
#include <hyprland/src/helpers/time/Time.hpp>
#include <expected>
#include <optional>
#include <string>
#include <vector>

namespace hyprmasq {
    struct SEisInfo {
        std::string address, path;
        bool        force = false;
    };

    void initEis(void* handle);
    void shutdownEis();
    // force: drive X11 windows even while another window of their X server has the focus.
    std::expected<std::string, std::string> openEis(const PHLWINDOW& window, bool force);
    bool                                    closeEis(const std::string& address);
    std::vector<SEisInfo>                   listEis();
    struct SInputState {
        Time::steady_tp lastInput;  // when emulated input last reached the window
        int             frames = 0; // frames the window drew since then
    };
    // For a window with a socket. Main thread only.
    std::optional<SInputState> inputState(const PHLWINDOW& window);
}
