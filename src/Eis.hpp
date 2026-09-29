#pragma once
#include "Windows.hpp"
#include <expected>
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
}
