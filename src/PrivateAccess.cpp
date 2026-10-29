#include "PrivateAccess.hpp"

#include <hyprland/src/desktop/rule/windowRule/WindowRuleApplicator.hpp>
#include <hyprland/src/desktop/view/window/Window.hpp>
#include <hyprland/src/desktop/view/window/X11Backend.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/pointer/cursor/CursorManager.hpp>
#include <hyprland/src/protocols/PrimarySelection.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/DataDevice.hpp>
#include <hyprland/src/protocols/core/Seat.hpp>
#include <hyprland/src/render/GLRenderer.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/xwayland/XWM.hpp>

#include <utility>

namespace hyprmasq {
    namespace {
        // Gives Member through exposed(Tag). An explicit instantiation may name private and protected members, so the
        // instantiations below reach them without redefining access specifiers around the includes.
        template <typename Tag, auto Member>
        struct SExpose {
            friend constexpr auto exposed(Tag) {
                return Member;
            }
        };

        struct SKeyboardSurface {
            friend constexpr auto exposed(SKeyboardSurface);
        };
        template struct SExpose<SKeyboardSurface, &CWLKeyboardResource::m_currentSurface>;
        struct SPointerSurface {
            friend constexpr auto exposed(SPointerSurface);
        };
        template struct SExpose<SPointerSurface, &CWLPointerResource::m_currentSurface>;
        struct SPointerPressed {
            friend constexpr auto exposed(SPointerPressed);
        };
        template struct SExpose<SPointerPressed, &CWLPointerResource::m_pressedButtons>;

        struct SKeyboardKeys {
            friend constexpr auto exposed(SKeyboardKeys);
        };
        template struct SExpose<SKeyboardKeys, &IKeyboard::m_pressed>;

        struct SDataDeviceWire {
            friend constexpr auto exposed(SDataDeviceWire);
        };
        template struct SExpose<SDataDeviceWire, &CWLDataDeviceResource::m_resource>;
        struct SDataDevices {
            friend constexpr auto exposed(SDataDevices);
        };
        template struct SExpose<SDataDevices, &CWLDataDeviceProtocol::m_devices>;
        struct SDataSend {
            friend constexpr auto exposed(SDataSend);
        };
        template struct SExpose<SDataSend, &CWLDataDeviceProtocol::sendSelectionToDevice>;
        struct SDataRequests {
            friend constexpr auto exposed(SDataRequests);
        };
        template struct SExpose<SDataRequests, &CWlDataDevice::requests>;

        struct SPrimaryWire {
            friend constexpr auto exposed(SPrimaryWire);
        };
        template struct SExpose<SPrimaryWire, &CPrimarySelectionDevice::m_resource>;
        struct SPrimaryDevices {
            friend constexpr auto exposed(SPrimaryDevices);
        };
        template struct SExpose<SPrimaryDevices, &CPrimarySelectionProtocol::m_devices>;
        struct SPrimarySend {
            friend constexpr auto exposed(SPrimarySend);
        };
        template struct SExpose<SPrimarySend, &CPrimarySelectionProtocol::sendSelectionToDevice>;
        struct SPrimaryRequests {
            friend constexpr auto exposed(SPrimaryRequests);
        };
        template struct SExpose<SPrimaryRequests, &CZwpPrimarySelectionDeviceV1::requests>;

        struct SToplevelWire {
            friend constexpr auto exposed(SToplevelWire);
        };
        template struct SExpose<SToplevelWire, &CXDGToplevelResource::m_resource>;

        struct SXwmClipboard {
            friend constexpr auto exposed(SXwmClipboard);
        };
        template struct SExpose<SXwmClipboard, &CXWM::m_clipboard>;
        struct SXwmPrimary {
            friend constexpr auto exposed(SXwmPrimary);
        };
        template struct SExpose<SXwmPrimary, &CXWM::m_primarySelection>;
        struct SXwmGetSelection {
            friend constexpr auto exposed(SXwmGetSelection);
        };
        template struct SExpose<SXwmGetSelection, &CXWM::getSelection>;
        struct SXwmConnection {
            friend constexpr auto exposed(SXwmConnection);
        };
        template struct SExpose<SXwmConnection, &CXWM::getConnection>;

        using CursorManager = Pointer::Cursor::CCursorManager;
        struct SCursorXCursor {
            friend constexpr auto exposed(SCursorXCursor);
        };
        template struct SExpose<SCursorXCursor, &CursorManager::m_xcursor>;
        struct SCursorSize {
            friend constexpr auto exposed(SCursorSize);
        };
        template struct SExpose<SCursorSize, &CursorManager::m_size>;
        struct SCursorScale {
            friend constexpr auto exposed(SCursorScale);
        };
        template struct SExpose<SCursorScale, &CursorManager::m_cursorScale>;

        struct SApplicatorWindow {
            friend constexpr auto exposed(SApplicatorWindow);
        };
        template struct SExpose<SApplicatorWindow, &Desktop::Rule::CWindowRuleApplicator::m_window>;

        struct SWindowSuppression {
            friend constexpr auto exposed(SWindowSuppression);
        };
        template struct SExpose<SWindowSuppression, &Desktop::View::CWindow::m_requestSuppression>;
        struct SX11PendingSize {
            friend constexpr auto exposed(SX11PendingSize);
        };
        template struct SExpose<SX11PendingSize, &Desktop::View::CX11Backend::m_pendingReportedSize>;
        struct SX11Surface {
            friend constexpr auto exposed(SX11Surface);
        };
        template struct SExpose<SX11Surface, &Desktop::View::CX11Backend::m_xwaylandSurface>;

        struct SGLPendingBuffers {
            friend constexpr auto exposed(SGLPendingBuffers);
        };
        template struct SExpose<SGLPendingBuffers, &Render::GL::CHyprGLRenderer::m_pendingBufferUses>;

        struct SRenderWindow {
            friend constexpr auto exposed(SRenderWindow);
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

    PHLWINDOWREF& windowOf(Desktop::Rule::CWindowRuleApplicator& applicator) {
        return applicator.*exposed(SApplicatorWindow{});
    }

    bool& activateFocusOnlyOf(Desktop::View::CWindow& window) {
        return (window.*exposed(SWindowSuppression{})).activateFocusOnly;
    }

    std::optional<Vector2D>& pendingReportedSizeOf(Desktop::View::CX11Backend& backend) {
        return backend.*exposed(SX11PendingSize{});
    }

    WP<CXWaylandSurface>& xwaylandSurfaceOf(Desktop::View::CX11Backend& backend) {
        return backend.*exposed(SX11Surface{});
    }

    void renderWindow(Render::IHyprRenderer& renderer, Render::CRenderContext& ctx, PHLWINDOW window, PHLMONITOR monitor, const Render::SWindowRenderPresentation& presentation,
                      const Time::steady_tp& time, bool decorate, Render::eRenderPassMode mode, bool ignorePosition, bool standalone) {
        (renderer.*exposed(SRenderWindow{}))(ctx, std::move(window), std::move(monitor), presentation, time, decorate, mode, ignorePosition, standalone);
    }

    std::vector<Render::SSurfaceBufferUse>& pendingBufferUsesOf(Render::GL::CHyprGLRenderer& renderer) {
        return renderer.*exposed(SGLPendingBuffers{});
    }
}
