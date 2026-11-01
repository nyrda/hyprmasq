#include "PrivateAccess.hpp"

#include <hyprland/src/debug/HyprCtl.hpp>
#include <hyprland/src/desktop/rule/windowRule/WindowRuleApplicator.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/pointer/cursor/CursorManager.hpp>
#include <hyprland/src/protocols/IdleNotify.hpp>
#include <hyprland/src/protocols/PrimarySelection.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/core/Seat.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/xwayland/XWM.hpp>

#include <utility>

namespace hyprmasq {
    namespace {
        // Gives Member through exposed(Tag). An explicit instantiation may name private and protected members, so the
        // instantiations below reach them without redefining access specifiers around the includes. Tag::type is the
        // member's type at the pin, and an instantiation stops compiling once Hyprland gives the member another type.
        template <typename Tag, typename Tag::type Member>
        struct SExpose {
            friend constexpr typename Tag::type exposed(Tag) {
                return Member;
            }
        };
        // The same for a member of an unnamed struct type, which no tag can name. The accessor below that reads its
        // fields binds each to a reference of the field's type, which checks those.
        template <typename Tag, auto Member>
        struct SExposeUnnamed {
            friend constexpr auto exposed(Tag) {
                return Member;
            }
        };

        struct SKeyboardSurface {
            using type = WP<CWLSurfaceResource> CWLKeyboardResource::*;
            friend constexpr type               exposed(SKeyboardSurface);
        };
        template struct SExpose<SKeyboardSurface, &CWLKeyboardResource::m_currentSurface>;
        struct SPointerSurface {
            using type = WP<CWLSurfaceResource> CWLPointerResource::*;
            friend constexpr type               exposed(SPointerSurface);
        };
        template struct SExpose<SPointerSurface, &CWLPointerResource::m_currentSurface>;
        struct SPointerPressed {
            using type = std::vector<uint32_t> CWLPointerResource::*;
            friend constexpr type              exposed(SPointerPressed);
        };
        template struct SExpose<SPointerPressed, &CWLPointerResource::m_pressedButtons>;

        struct SKeyboardKeys {
            using type = std::vector<uint32_t> IKeyboard::*;
            friend constexpr type              exposed(SKeyboardKeys);
        };
        template struct SExpose<SKeyboardKeys, &IKeyboard::m_pressed>;

        struct SDataDeviceWire {
            using type = SP<CWlDataDevice> CWLDataDeviceResource::*;
            friend constexpr type          exposed(SDataDeviceWire);
        };
        template struct SExpose<SDataDeviceWire, &CWLDataDeviceResource::m_resource>;
        struct SDataDevices {
            using type = std::vector<SP<CWLDataDeviceResource>> CWLDataDeviceProtocol::*;
            friend constexpr type                               exposed(SDataDevices);
        };
        template struct SExpose<SDataDevices, &CWLDataDeviceProtocol::m_devices>;
        struct SDataSend {
            using type = void (CWLDataDeviceProtocol::*)(SP<IDataDevice>, SP<IDataSource>);
            friend constexpr type exposed(SDataSend);
        };
        template struct SExpose<SDataSend, &CWLDataDeviceProtocol::sendSelectionToDevice>;
        struct SDataRequests {
            friend constexpr auto exposed(SDataRequests);
        };
        template struct SExposeUnnamed<SDataRequests, &CWlDataDevice::requests>;

        struct SPrimaryWire {
            using type = SP<CZwpPrimarySelectionDeviceV1> CPrimarySelectionDevice::*;
            friend constexpr type                         exposed(SPrimaryWire);
        };
        template struct SExpose<SPrimaryWire, &CPrimarySelectionDevice::m_resource>;
        struct SPrimaryDevices {
            using type = std::vector<SP<CPrimarySelectionDevice>> CPrimarySelectionProtocol::*;
            friend constexpr type                                 exposed(SPrimaryDevices);
        };
        template struct SExpose<SPrimaryDevices, &CPrimarySelectionProtocol::m_devices>;
        struct SPrimarySend {
            using type = void (CPrimarySelectionProtocol::*)(SP<CPrimarySelectionDevice>, SP<IDataSource>);
            friend constexpr type exposed(SPrimarySend);
        };
        template struct SExpose<SPrimarySend, &CPrimarySelectionProtocol::sendSelectionToDevice>;
        struct SPrimaryRequests {
            friend constexpr auto exposed(SPrimaryRequests);
        };
        template struct SExposeUnnamed<SPrimaryRequests, &CZwpPrimarySelectionDeviceV1::requests>;

        struct SToplevelWire {
            using type = SP<CXdgToplevel> CXDGToplevelResource::*;
            friend constexpr type         exposed(SToplevelWire);
        };
        template struct SExpose<SToplevelWire, &CXDGToplevelResource::m_resource>;

        struct SXwmClipboard {
            using type = SXSelection CXWM::*;
            friend constexpr type    exposed(SXwmClipboard);
        };
        template struct SExpose<SXwmClipboard, &CXWM::m_clipboard>;
        struct SXwmPrimary {
            using type = SXSelection CXWM::*;
            friend constexpr type    exposed(SXwmPrimary);
        };
        template struct SExpose<SXwmPrimary, &CXWM::m_primarySelection>;
        struct SXwmGetSelection {
            using type = SXSelection* (CXWM::*)(xcb_atom_t);
            friend constexpr type exposed(SXwmGetSelection);
        };
        template struct SExpose<SXwmGetSelection, &CXWM::getSelection>;
        struct SXwmConnection {
            using type = xcb_connection_t* (CXWM::*)();
            friend constexpr type exposed(SXwmConnection);
        };
        template struct SExpose<SXwmConnection, &CXWM::getConnection>;

        using CursorManager = Pointer::Cursor::CCursorManager;
        struct SCursorXCursor {
            using type = UP<CXCursorManager> CursorManager::*;
            friend constexpr type            exposed(SCursorXCursor);
        };
        template struct SExpose<SCursorXCursor, &CursorManager::m_xcursor>;
        struct SCursorSize {
            using type = int      CursorManager::*;
            friend constexpr type exposed(SCursorSize);
        };
        template struct SExpose<SCursorSize, &CursorManager::m_size>;
        struct SCursorScale {
            using type = float    CursorManager::*;
            friend constexpr type exposed(SCursorScale);
        };
        template struct SExpose<SCursorScale, &CursorManager::m_cursorScale>;

        struct SHyprCtlCommands {
            using type = std::vector<SP<SHyprCtlCommand>> CHyprCtl::*;
            friend constexpr type                         exposed(SHyprCtlCommands);
        };
        template struct SExpose<SHyprCtlCommands, &CHyprCtl::m_commands>;

        struct SApplicatorWindow {
            using type = PHLWINDOWREF Desktop::Rule::CWindowRuleApplicator::*;
            friend constexpr type     exposed(SApplicatorWindow);
        };
        template struct SExpose<SApplicatorWindow, &Desktop::Rule::CWindowRuleApplicator::m_window>;

        struct SIdleInhibited {
            using type = bool     CIdleNotifyProtocol::*;
            friend constexpr type exposed(SIdleInhibited);
        };
        template struct SExpose<SIdleInhibited, &CIdleNotifyProtocol::isInhibited>;

        struct SRenderWindow {
            using type = void (Render::IHyprRenderer::*)(PHLWINDOW, PHLMONITOR, const Time::steady_tp&, bool, Render::eRenderPassMode, bool, bool);
            friend constexpr type exposed(SRenderWindow);
        };
        template struct SExpose<SRenderWindow, &Render::IHyprRenderer::renderWindow>;
    }

    WP<CWLSurfaceResource>& surfaceOf(CWLKeyboardResource& keyboard) {
        return keyboard.*exposed(SKeyboardSurface{});
    }

    WP<CWLSurfaceResource>& surfaceOf(CWLPointerResource& pointer) {
        return pointer.*exposed(SPointerSurface{});
    }

    std::vector<uint32_t>& pressedOf(CWLPointerResource& pointer) {
        return pointer.*exposed(SPointerPressed{});
    }

    std::vector<uint32_t>& keysOf(IKeyboard& keyboard) {
        return keyboard.*exposed(SKeyboardKeys{});
    }

    SP<CWlDataDevice>& wireOf(CWLDataDeviceResource& device) {
        return device.*exposed(SDataDeviceWire{});
    }

    std::vector<SP<CWLDataDeviceResource>>& devicesOf(CWLDataDeviceProtocol& protocol) {
        return protocol.*exposed(SDataDevices{});
    }

    void sendTo(CWLDataDeviceProtocol& protocol, SP<IDataDevice> device, SP<IDataSource> source) {
        (protocol.*exposed(SDataSend{}))(std::move(device), std::move(source));
    }

    StartDrag& startDragOf(CWlDataDevice& wire) {
        return (wire.*exposed(SDataRequests{})).startDrag;
    }

    SetSelection& setSelectionOf(CWlDataDevice& wire) {
        return (wire.*exposed(SDataRequests{})).setSelection;
    }

    SP<CZwpPrimarySelectionDeviceV1>& wireOf(CPrimarySelectionDevice& device) {
        return device.*exposed(SPrimaryWire{});
    }

    std::vector<SP<CPrimarySelectionDevice>>& devicesOf(CPrimarySelectionProtocol& protocol) {
        return protocol.*exposed(SPrimaryDevices{});
    }

    void sendTo(CPrimarySelectionProtocol& protocol, SP<CPrimarySelectionDevice> device, SP<IDataSource> source) {
        (protocol.*exposed(SPrimarySend{}))(std::move(device), std::move(source));
    }

    SetPrimarySelection& setSelectionOf(CZwpPrimarySelectionDeviceV1& wire) {
        return (wire.*exposed(SPrimaryRequests{})).setSelection;
    }

    SP<CXdgToplevel>& wireOf(CXDGToplevelResource& toplevel) {
        return toplevel.*exposed(SToplevelWire{});
    }

    SXSelection& clipboardOf(CXWM& wm) {
        return wm.*exposed(SXwmClipboard{});
    }

    SXSelection& primaryOf(CXWM& wm) {
        return wm.*exposed(SXwmPrimary{});
    }

    SXSelection* selectionOf(CXWM& wm, xcb_atom_t atom) {
        return (wm.*exposed(SXwmGetSelection{}))(atom);
    }

    xcb_connection_t* connectionOf(CXWM& wm) {
        return (wm.*exposed(SXwmConnection{}))();
    }

    UP<CXCursorManager>& xcursorOf(CursorManager& manager) {
        return manager.*exposed(SCursorXCursor{});
    }

    int& cursorSizeOf(CursorManager& manager) {
        return manager.*exposed(SCursorSize{});
    }

    float& cursorScaleOf(CursorManager& manager) {
        return manager.*exposed(SCursorScale{});
    }

    std::vector<SP<SHyprCtlCommand>>& commandsOf(CHyprCtl& ctl) {
        return ctl.*exposed(SHyprCtlCommands{});
    }

    PHLWINDOWREF& windowOf(Desktop::Rule::CWindowRuleApplicator& applicator) {
        return applicator.*exposed(SApplicatorWindow{});
    }

    bool& inhibitedOf(CIdleNotifyProtocol& protocol) {
        return protocol.*exposed(SIdleInhibited{});
    }

    void renderWindow(Render::IHyprRenderer& renderer, PHLWINDOW window, PHLMONITOR monitor, const Time::steady_tp& time, bool decorate, Render::eRenderPassMode mode,
                      bool ignorePosition, bool standalone) {
        (renderer.*exposed(SRenderWindow{}))(std::move(window), std::move(monitor), time, decorate, mode, ignorePosition, standalone);
    }
}
