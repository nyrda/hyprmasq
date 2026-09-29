#pragma once
#include <string_view>

namespace hyprmasq {
    // Hooks the Hyprland function with this qualified name, for example "CXWM::activateSurface", and returns a pointer
    // to its original. Throws if the name matches no function or several.
    void* hook(void* handle, std::string_view qualifiedName, void* replacement);
    // Removes every hook, in reverse order of installation.
    void unhookAll();
}
