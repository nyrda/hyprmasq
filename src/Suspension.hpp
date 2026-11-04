#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>
#include <hyprland/src/helpers/memory/Memory.hpp>

#include <memory>

class CXDGToplevelResource;

// Hyprland tells the xdg toplevels of windows that it does not show, such as those of hidden workspaces, that they are
// suspended, and some apps then commit no frame at all: Qt takes a suspended window for one that is not exposed, and
// draws neither input nor a new size. A window that the plugin drives is told that it is not suspended for as long as
// a hold on it exists, and what Hyprland told it last once the last hold ends. Hyprland's own state, which its
// CWindow::setSuspended keeps, does not change. X11 windows have no such state, and neither have the toplevels of an
// xdg_wm_base older than version 6. The hook on CXDGToplevelResource::setSuspeneded, through which Hyprland tells a
// toplevel, is installed while a hold exists, see hooks::suspension. While another plugin hooks it, holds do nothing.
// Main thread only.
namespace hyprmasq::suspension {
    // Defines the hook.
    void init(void* handle);

    struct SHeld;

    // Keeps the window from being told that it is suspended while it exists. Holds of one window share its state.
    class CAwake {
      public:
        explicit CAwake(const PHLWINDOW& window);

      private:
        std::shared_ptr<SHeld> m_held;
    };
}
