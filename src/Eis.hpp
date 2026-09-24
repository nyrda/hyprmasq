#pragma once
#include "Windows.hpp"
#include <expected>
#include <string>
#include <vector>

namespace hyprmasq {
    void                                             initEis(void* handle);
    void                                             shutdownEis();
    std::expected<std::string, std::string>          openEis(const PHLWINDOW& window);
    bool                                             closeEis(const std::string& address);
    std::vector<std::pair<std::string, std::string>> listEis();
}
