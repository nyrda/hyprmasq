#pragma once

#include <hyprland/src/desktop/DesktopTypes.hpp>
#include <hyprland/src/helpers/memory/Memory.hpp>
#include <hyprland/src/helpers/time/Time.hpp>

#include <cstdint>
#include <functional>
#include <vector>

#include <xcb/xcb.h>

class CHyprCtl;
class CIdleNotifyProtocol;
class CPrimarySelectionDevice;
class CPrimarySelectionProtocol;
class CWLDataDeviceProtocol;
class CWLDataDeviceResource;
class CWLKeyboardResource;
class CWLPointerResource;
class CWLSurfaceResource;
class CWlDataDevice;
class CXCursorManager;
class CXDGToplevelResource;
class CXWM;
class CXdgToplevel;
class CZwpPrimarySelectionDeviceV1;
class IDataDevice;
class IDataSource;
class IKeyboard;
struct SHyprCtlCommand;
struct SXSelection;
struct wl_resource;
namespace Desktop::Rule {
    class CWindowRuleApplicator;
}
namespace Pointer::Cursor {
    class CCursorManager;
}
namespace Render {
    class IHyprRenderer;
    enum eRenderPassMode : uint8_t;
}

// The private and protected members of Hyprland's classes that the plugin reaches, for want of an API. Each binds the
// plugin to the pinned Hyprland's layout of its class, or to a function's signature, so check every one against the
// Hyprland source named with it when the pin moves. PrivateAccess.cpp reaches them through explicit instantiations,
// which may name private members, without redefining access specifiers around the includes. Main thread only.
namespace hyprmasq {
    // Seat, src/protocols/core/Seat.hpp.
    // CWLKeyboardResource::m_currentSurface and CWLPointerResource::m_currentSurface: the surface that a keyboard or
    // pointer resource sends its events to.
    WP<CWLSurfaceResource>& surfaceOf(CWLKeyboardResource& keyboard);
    WP<CWLSurfaceResource>& surfaceOf(CWLPointerResource& pointer);
    // CWLPointerResource::m_pressedButtons: the buttons that a pointer resource releases when it leaves its surface,
    // outside Hyprland's own drags.
    std::vector<uint32_t>& pressedOf(CWLPointerResource& pointer);

    // Keyboards, src/devices/IKeyboard.hpp.
    // IKeyboard::m_pressed: the keys that a keyboard device holds. Hyprland updates them for every key, before it sends
    // the key to the seat or to an input method's keyboard grab, and drops them with the device. Its getKeysFromAllKBs()
    // leaves out keys held through a grab, and keeps a key pressed before a grab started and released during it.
    // Hyprland drops the keys of a keyboard that is disabled or lacks input permission, so those do not count.
    std::vector<uint32_t>& keysOf(IKeyboard& keyboard);

    // Data devices, src/protocols/core/DataDevice.hpp, and their wire resources, which hyprwayland-scanner generates into
    // protocols/wayland.hpp.
    // CWLDataDeviceResource::m_resource: a data device's wire resource.
    SP<CWlDataDevice>& wireOf(CWLDataDeviceResource& device);
    // CWLDataDeviceProtocol::m_devices: every data device.
    std::vector<SP<CWLDataDeviceResource>>& devicesOf(CWLDataDeviceProtocol& protocol);
    // CWLDataDeviceProtocol::sendSelectionToDevice: sends a data device a selection, or none, through Hyprland's offers.
    void sendTo(CWLDataDeviceProtocol& protocol, SP<IDataDevice> device, SP<IDataSource> source);
    // CWlDataDevice::requests.startDrag and .setSelection: the handlers of a data device's start_drag and set_selection.
    using StartDrag    = std::function<void(CWlDataDevice*, wl_resource*, wl_resource*, wl_resource*, uint32_t)>;
    using SetSelection = std::function<void(CWlDataDevice*, wl_resource*, uint32_t)>;
    StartDrag& startDragOf(CWlDataDevice& wire);
    SetSelection& setSelectionOf(CWlDataDevice& wire);

    // Primary selection devices, src/protocols/PrimarySelection.hpp, and their wire resources, which
    // hyprwayland-scanner generates into protocols/primary-selection-unstable-v1.hpp.
    // CPrimarySelectionDevice::m_resource: a primary selection device's wire resource.
    SP<CZwpPrimarySelectionDeviceV1>& wireOf(CPrimarySelectionDevice& device);
    // CPrimarySelectionProtocol::m_devices: every primary selection device.
    std::vector<SP<CPrimarySelectionDevice>>& devicesOf(CPrimarySelectionProtocol& protocol);
    // CPrimarySelectionProtocol::sendSelectionToDevice: sends a primary selection device a selection, or none.
    void sendTo(CPrimarySelectionProtocol& protocol, SP<CPrimarySelectionDevice> device, SP<IDataSource> source);
    // CZwpPrimarySelectionDeviceV1::requests.setSelection: the handler of a primary selection device's set_selection.
    using SetPrimarySelection = std::function<void(CZwpPrimarySelectionDeviceV1*, wl_resource*, uint32_t)>;
    SetPrimarySelection& setSelectionOf(CZwpPrimarySelectionDeviceV1& wire);

    // xdg toplevels, src/protocols/XDGShell.hpp, and their wire resources, which hyprwayland-scanner generates into
    // protocols/xdg-shell.hpp.
    // CXDGToplevelResource::m_resource: a toplevel's wire resource, whose version tells which states it takes.
    SP<CXdgToplevel>& wireOf(CXDGToplevelResource& toplevel);

    // The X window manager, src/xwayland/XWM.hpp.
    // CXWM::m_clipboard and CXWM::m_primarySelection: its X selections.
    SXSelection& clipboardOf(CXWM& wm);
    SXSelection& primaryOf(CXWM& wm);
    // CXWM::getSelection: the X selection of an atom, or null.
    SXSelection* selectionOf(CXWM& wm, xcb_atom_t atom);
    // CXWM::getConnection: its connection to the X server, or null. The window manager redirects the restacking of
    // managed windows by other clients to itself, but not its own.
    xcb_connection_t* connectionOf(CXWM& wm);

    // The cursor manager, src/pointer/cursor/CursorManager.hpp.
    // CCursorManager::m_xcursor, m_size and m_cursorScale: the XCursor theme that Hyprland loaded, its size and scale.
    UP<CXCursorManager>& xcursorOf(Pointer::Cursor::CCursorManager& manager);
    int& cursorSizeOf(Pointer::Cursor::CCursorManager& manager);
    float& cursorScaleOf(Pointer::Cursor::CCursorManager& manager);

    // hyprctl, src/debug/HyprCtl.hpp.
    // CHyprCtl::m_commands: its commands, among them the batch command.
    std::vector<SP<SHyprCtlCommand>>& commandsOf(CHyprCtl& ctl);

    // Window rules, src/desktop/rule/windowRule/WindowRuleApplicator.hpp.
    // CWindowRuleApplicator::m_window: the window whose rules an applicator reads.
    PHLWINDOWREF& windowOf(Desktop::Rule::CWindowRuleApplicator& applicator);

    // Idle notifications, src/protocols/IdleNotify.hpp.
    // CIdleNotifyProtocol::isInhibited: whether an idle inhibitor holds off the notifications that obey inhibitors, as
    // CIdleNotifyProtocol::setInhibit last set it.
    bool& inhibitedOf(CIdleNotifyProtocol& protocol);

    // The renderer, src/render/Renderer.hpp.
    // IHyprRenderer::renderWindow, protected: draws a window into the current render.
    void renderWindow(Render::IHyprRenderer& renderer, PHLWINDOW window, PHLMONITOR monitor, const Time::steady_tp& time, bool decorate, Render::eRenderPassMode mode,
                      bool ignorePosition, bool standalone);
}
