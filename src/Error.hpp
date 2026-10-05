#pragma once

#include <cstdint>
#include <string>
#include <string_view>

namespace hyprmasq {
    // Stable codes of JSON errors. Clients branch on them, so they are part of the command interface.
    enum class eErrorCode : uint8_t {
        INVALID_ARGUMENTS,
        WINDOW_GONE,
        NO_SOCKET,
        UNSUPPORTED,
        INTERNAL,
        UNLOADED,
    };

    struct SError {
        eErrorCode  code = eErrorCode::INTERNAL;
        std::string message;
    };

    constexpr std::string_view codeName(eErrorCode code) {
        switch (code) {
            case eErrorCode::INVALID_ARGUMENTS: return "invalid_arguments";
            case eErrorCode::WINDOW_GONE: return "window_gone";
            case eErrorCode::NO_SOCKET: return "no_socket";
            case eErrorCode::UNSUPPORTED: return "unsupported";
            case eErrorCode::INTERNAL: return "internal";
            case eErrorCode::UNLOADED: return "unloaded";
        }
        return "internal";
    }
}
