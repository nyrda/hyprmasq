#include "PrivateAccess.hpp"

#include <hyprland/src/desktop/rule/windowRule/WindowRuleApplicator.hpp>
#include <hyprland/src/desktop/view/window/Window.hpp>
#include <hyprland/src/desktop/view/window/X11Backend.hpp>
#include <hyprland/src/devices/IKeyboard.hpp>
#include <hyprland/src/plugins/HookSystem.hpp>
#include <hyprland/src/pointer/cursor/CursorManager.hpp>
#include <hyprland/src/protocols/IdleNotify.hpp>
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

        struct SApplicatorWindow {
            using type = PHLWINDOWREF Desktop::Rule::CWindowRuleApplicator::*;
            friend constexpr type     exposed(SApplicatorWindow);
        };
        template struct SExpose<SApplicatorWindow, &Desktop::Rule::CWindowRuleApplicator::m_window>;

        struct SWindowSuppression {
            friend constexpr auto exposed(SWindowSuppression);
        };
        template struct SExposeUnnamed<SWindowSuppression, &Desktop::View::CWindow::m_requestSuppression>;
        struct SX11PendingSize {
            using type = std::optional<Vector2D> Desktop::View::CX11Backend::*;
            friend constexpr type                exposed(SX11PendingSize);
        };
        template struct SExpose<SX11PendingSize, &Desktop::View::CX11Backend::m_pendingReportedSize>;
        struct SX11Surface {
            using type = WP<CXWaylandSurface> Desktop::View::CX11Backend::*;
            friend constexpr type             exposed(SX11Surface);
        };
        template struct SExpose<SX11Surface, &Desktop::View::CX11Backend::m_xwaylandSurface>;

        struct SIdleInhibited {
            using type = bool     CIdleNotifyProtocol::*;
            friend constexpr type exposed(SIdleInhibited);
        };
        template struct SExpose<SIdleInhibited, &CIdleNotifyProtocol::isInhibited>;

        struct SActiveHooks {
            using type = std::unordered_set<uint64_t> CHookSystem::*;
            friend constexpr type                     exposed(SActiveHooks);
        };
        template struct SExpose<SActiveHooks, &CHookSystem::m_activeHooks>;
        // src/Hooks.cpp installs a hook again with the trampolines of its first CFunctionHook::hook(), which is safe only
        // while Hyprland never frees, reuses or unmaps a trampoline. No type says so, so these sizes stop the build once
        // the hook system changes, as a list of freed trampolines to reuse would change CHookSystem.
        static_assert(sizeof(CHookSystem) == 104,
                      "CHookSystem changed: re-read src/plugins/HookSystem.cpp, check that unhook() still neither frees nor reuses trampolines and that "
                      "getAddressForTrampo() only hands out new space, which rehook() in src/Hooks.cpp relies on, then update this size");
        static_assert(sizeof(CFunctionHook) == 88,
                      "CFunctionHook changed: re-read src/plugins/HookSystem.cpp, check that unhook() still neither frees nor reuses trampolines and that "
                      "getAddressForTrampo() only hands out new space, which rehook() in src/Hooks.cpp relies on, then update this size");
        struct SLaunchTrampoline {
            using type = void*    CFunctionHook::*;
            friend constexpr type exposed(SLaunchTrampoline);
        };
        template struct SExpose<SLaunchTrampoline, &CFunctionHook::m_launchTrampolineAddr>;
        struct SLandTrampoline {
            using type = void*    CFunctionHook::*;
            friend constexpr type exposed(SLandTrampoline);
        };
        template struct SExpose<SLandTrampoline, &CFunctionHook::m_landTrampolineAddr>;
        struct SHookLen {
            using type = size_t   CFunctionHook::*;
            friend constexpr type exposed(SHookLen);
        };
        template struct SExpose<SHookLen, &CFunctionHook::m_hookLen>;
        struct SOriginalBytes {
            using type = std::vector<unsigned char> CFunctionHook::*;
            friend constexpr type                   exposed(SOriginalBytes);
        };
        template struct SExpose<SOriginalBytes, &CFunctionHook::m_originalBytes>;
        struct SHookActive {
            using type = bool     CFunctionHook::*;
            friend constexpr type exposed(SHookActive);
        };
        template struct SExpose<SHookActive, &CFunctionHook::m_active>;

        struct SGLPendingBuffers {
            using type = std::vector<Render::SSurfaceBufferUse> Render::GL::CHyprGLRenderer::*;
            friend constexpr type                               exposed(SGLPendingBuffers);
        };
        template struct SExpose<SGLPendingBuffers, &Render::GL::CHyprGLRenderer::m_pendingBufferUses>;

        struct SRenderWindow {
            using type = void (Render::IHyprRenderer::*)(Render::CRenderContext&, PHLWINDOW, PHLMONITOR, const Render::SWindowRenderPresentation&, const Time::steady_tp&, bool,
                                                         Render::eRenderPassMode, bool, bool);
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

    bool& inhibitedOf(CIdleNotifyProtocol& protocol) {
        return protocol.*exposed(SIdleInhibited{});
    }

    std::unordered_set<uint64_t>& activeHooksOf(CHookSystem& system) {
        return system.*exposed(SActiveHooks{});
    }

    void*& launchTrampolineOf(CFunctionHook& hook) {
        return hook.*exposed(SLaunchTrampoline{});
    }

    void*& landTrampolineOf(CFunctionHook& hook) {
        return hook.*exposed(SLandTrampoline{});
    }

    size_t& hookLenOf(CFunctionHook& hook) {
        return hook.*exposed(SHookLen{});
    }

    std::vector<unsigned char>& originalBytesOf(CFunctionHook& hook) {
        return hook.*exposed(SOriginalBytes{});
    }

    bool& activeOf(CFunctionHook& hook) {
        return hook.*exposed(SHookActive{});
    }

    void renderWindow(Render::IHyprRenderer& renderer, Render::CRenderContext& ctx, PHLWINDOW window, PHLMONITOR monitor, const Render::SWindowRenderPresentation& presentation,
                      const Time::steady_tp& time, bool decorate, Render::eRenderPassMode mode, bool ignorePosition, bool standalone) {
        (renderer.*exposed(SRenderWindow{}))(ctx, std::move(window), std::move(monitor), presentation, time, decorate, mode, ignorePosition, standalone);
    }

    std::vector<Render::SSurfaceBufferUse>& pendingBufferUsesOf(Render::GL::CHyprGLRenderer& renderer) {
        return renderer.*exposed(SGLPendingBuffers{});
    }
}
