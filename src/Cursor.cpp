#include "Cursor.hpp"

#include <hyprland/src/Compositor.hpp>
#include <hyprland/src/config/ConfigValue.hpp>
#include <hyprland/src/config/values/types/BoolValue.hpp>
#include <hyprland/src/config/values/types/ColorValue.hpp>
#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/event/EventBus.hpp>
#include <hyprland/src/managers/XCursorManager.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/plugins/PluginAPI.hpp>
#include <hyprland/src/pointer/cursor/CursorManager.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/pass/BorderPassElement.hpp>
#include <hyprland/src/render/pass/TexPassElement.hpp>
#include <hyprutils/math/Region.hpp>

#include <drm_fourcc.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <stdexcept>
#include <vector>

using namespace Hyprutils::Math;

namespace hyprmasq::cursor {
    namespace {
        using Manager = Pointer::Cursor::CCursorManager;

        // The theme Hyprland loaded is private to its cursor manager. An explicit instantiation may name private
        // members, so these read them without redefining access specifiers around the include.
        UP<CXCursorManager>& xcursorOf(Manager& manager);
        int&                 sizeOf(Manager& manager);
        float&               scaleOf(Manager& manager);
        template <UP<CXCursorManager> Manager::* XCursor, int Manager::* Size, float Manager::* Scale>
        struct SManagerAccess {
            friend UP<CXCursorManager>& xcursorOf(Manager& manager) {
                return manager.*XCursor;
            }
            friend int& sizeOf(Manager& manager) {
                return manager.*Size;
            }
            friend float& scaleOf(Manager& manager) {
                return manager.*Scale;
            }
        };
        template struct SManagerAccess<&Manager::m_xcursor, &Manager::m_size, &Manager::m_cursorScale>;

        constexpr auto   HOLD = std::chrono::milliseconds(1500), FADE = std::chrono::milliseconds(400), RIPPLE = std::chrono::milliseconds(450);
        constexpr double RIPPLE_FROM = 4, RIPPLE_TO = 18;
        constexpr int    RIPPLE_WIDTH = 2;

        struct SImage {
            std::vector<uint32_t> source, pixels; // premultiplied ARGB, as cursor themes store them
            Config::INTEGER       color = 0;
            Vector2D              pixelSize;
            Vector2D              size, hotspot; // logical pixels
            SP<Render::ITexture>  texture;
        };

        struct SRipple {
            Vector2D        position;
            Time::steady_tp start;
        };

        struct SCursor {
            PHLWINDOWREF         window;
            Vector2D             position;
            Time::steady_tp      last;
            std::vector<SRipple> ripples;
            CBox                 damaged;         // what the last frame drew, in global logical pixels
            bool                 changed = false; // looks different from what the last frame drew
        };

        SP<Config::Values::CBoolValue>  enabled;
        SP<Config::Values::CColorValue> color;
        SImage                          image;
        std::vector<SCursor>            cursors;
        wl_event_source*                timer = nullptr;
        CHyprSignalListener             renderListener;

        double                          luminance(double r, double g, double b) {
            return 0.2126 * r + 0.7152 * g + 0.0722 * b;
        }

        // Keeps the theme's shape and shading. The fill takes the configured color, the outline a color that contrasts
        // with it. Themes come with dark fills and light outlines or the reverse; most opaque pixels are fill. A cursor
        // in a single tone is all fill.
        std::vector<uint32_t> recolor(const std::vector<uint32_t>& source, const CHyprColor& accent) {
            struct SPixel {
                double a, l;
            };
            auto unpack = [](uint32_t p) {
                const double a = (p >> 24) / 255.0;
                if (a == 0)
                    return SPixel{0, 0};
                auto channel = [&](int shift) { return std::min(1.0, ((p >> shift) & 0xFF) / 255.0 / a); };
                return SPixel{a, luminance(channel(16), channel(8), channel(0))};
            };
            double total = 0, darkest = 1, brightest = 0;
            size_t opaque = 0;
            for (auto p : source) {
                const auto px = unpack(p);
                if (px.a >= 0.5) {
                    total += px.l;
                    darkest   = std::min(darkest, px.l);
                    brightest = std::max(brightest, px.l);
                    ++opaque;
                }
            }
            const double          range    = brightest - darkest;
            const bool            darkFill = opaque == 0 || total / opaque < (darkest + brightest) / 2;
            const double          outline  = luminance(accent.r, accent.g, accent.b) > 0.5 ? 0.1 : 1.0;

            std::vector<uint32_t> out(source.size());
            for (size_t i = 0; i < source.size(); ++i) {
                const auto px = unpack(source[i]);
                if (px.a == 0)
                    continue;
                const double tone    = range < 0.2 ? 1 : std::clamp((px.l - darkest) / range, 0.0, 1.0);
                const double fill    = range < 0.2 ? 1 : darkFill ? 1 - tone : tone;
                const double a       = px.a * accent.a;
                auto         channel = [&](double c) { return sc<uint32_t>(std::lround((outline + (c - outline) * fill) * a * 255)); };
                out[i]               = sc<uint32_t>(std::lround(a * 255)) << 24 | channel(accent.r) << 16 | channel(accent.g) << 8 | channel(accent.b);
            }
            return out;
        }

        // Takes the default arrow the way CCursorManager::setCursorFromName does: from hyprcursor when it is enabled and
        // has a theme, otherwise from XCursor.
        void refreshImage() {
            auto& manager = Pointer::Cursor::mgr();
            if (!manager)
                return;
            const float                   scale = scaleOf(*manager);
            CConfigValue<Config::INTEGER> useHyprcursor("cursor:enable_hyprcursor");
            SCursorImageData              data{};
            for (const char* name : {"left_ptr", "default", "left-ptr"}) {
                if (!*useHyprcursor || (data = manager->dataFor(name)).surface)
                    break;
            }

            std::vector<uint32_t> source;
            Vector2D              pixelSize, hotspot;
            float                 bufferScale = 1;
            if (data.surface && cairo_image_surface_get_format(data.surface) == CAIRO_FORMAT_ARGB32) {
                cairo_surface_flush(data.surface);
                const int   width = cairo_image_surface_get_width(data.surface), height = cairo_image_surface_get_height(data.surface);
                const int   stride = cairo_image_surface_get_stride(data.surface);
                const auto* bytes  = cairo_image_surface_get_data(data.surface);
                source.resize(sc<size_t>(width) * height);
                for (int y = 0; y < height; ++y)
                    std::memcpy(source.data() + sc<size_t>(y) * width, bytes + sc<size_t>(y) * stride, sc<size_t>(width) * 4);
                pixelSize   = {width, height};
                hotspot     = {data.hotspotX, data.hotspotY};
                bufferScale = scale;
            } else if (auto& xcursor = xcursorOf(*manager)) {
                auto shape = xcursor->getShape("left_ptr", sizeOf(*manager), scale);
                if (shape && !shape->images.empty()) {
                    const auto& icon = shape->images.front();
                    source           = icon.pixels;
                    pixelSize        = icon.size;
                    hotspot          = icon.hotspot;
                    bufferScale      = std::ceil(scale);
                }
            }

            const auto hex  = color->value();
            const auto size = pixelSize / bufferScale;
            hotspot /= bufferScale;
            if (source == image.source && pixelSize == image.pixelSize && size == image.size && hotspot == image.hotspot && hex == image.color)
                return;
            image           = {};
            image.pixels    = recolor(source, CHyprColor(sc<uint64_t>(hex)));
            image.source    = std::move(source);
            image.color     = hex;
            image.pixelSize = pixelSize;
            image.size      = size;
            image.hotspot   = hotspot;
            for (auto& cursor : cursors)
                cursor.changed = true;
        }

        // Where Hyprland draws the window this frame, including workspace switch animations.
        Vector2D origin(const PHLWINDOW& window) {
            auto position = window->position(Desktop::View::IGeometric::GEOMETRIC_CURRENT);
            if (window->m_workspace && !window->m_pinned)
                position += window->m_workspace->m_renderOffset->value();
            return position;
        }

        float opacity(const SCursor& cursor, Time::steady_tp now) {
            const auto age = now - cursor.last;
            if (age < HOLD)
                return 1;
            return std::max(0.F, 1.F - std::chrono::duration<float>(age - HOLD) / std::chrono::duration<float>(FADE));
        }

        double progress(const SRipple& ripple, Time::steady_tp now) {
            return std::clamp(std::chrono::duration<double>(now - ripple.start) / std::chrono::duration<double>(RIPPLE), 0.0, 1.0);
        }

        double radius(const SRipple& ripple, Time::steady_tp now) {
            const double p = progress(ripple, now);
            return RIPPLE_FROM + (RIPPLE_TO - RIPPLE_FROM) * (1 - (1 - p) * (1 - p));
        }

        // Empty while the window is not on screen.
        CBox bounds(const SCursor& cursor, const PHLWINDOW& window, Time::steady_tp now) {
            if (!g_pHyprRenderer->shouldRenderWindow(window))
                return {};
            const auto o = origin(window);
            CRegion    region;
            if (opacity(cursor, now) > 0 && !image.pixels.empty())
                region.add(CBox{o + cursor.position - image.hotspot, image.size});
            const double outer = RIPPLE_TO + RIPPLE_WIDTH;
            for (const auto& ripple : cursor.ripples)
                region.add(CBox{o + ripple.position - Vector2D{outer, outer}, Vector2D{outer, outer} * 2});
            return region.empty() ? CBox{} : region.getExtents().expand(2);
        }

        void damage(const CBox& box) {
            if (!box.empty())
                g_pHyprRenderer->damageBox(box);
        }

        // Damages what changed since the last frame, and drops cursors that are gone.
        int tick(void* = nullptr) {
            const auto now = Time::steadyNow();
            const bool on  = enabled->value();
            std::erase_if(cursors, [&](SCursor& cursor) {
                std::erase_if(cursor.ripples, [&](const SRipple& ripple) { return now - ripple.start >= RIPPLE; });
                auto       window    = cursor.window.lock();
                const bool done      = !on || !window || !window->m_isMapped || (now - cursor.last >= HOLD + FADE && cursor.ripples.empty());
                const auto box       = done ? CBox{} : bounds(cursor, window, now);
                const bool animating = !cursor.ripples.empty() || now - cursor.last >= HOLD;
                if (done || animating || cursor.changed || box != cursor.damaged) {
                    damage(cursor.damaged);
                    damage(box);
                    cursor.damaged = box;
                    cursor.changed = false;
                }
                return done;
            });
            if (!cursors.empty())
                wl_event_source_timer_update(timer, 16);
            return 0;
        }

        void render(eRenderStage stage) {
            if (stage != RENDER_POST_WINDOWS || cursors.empty() || !enabled->value())
                return;
            auto monitor = g_pHyprRenderer->m_renderData.pMonitor.lock();
            if (!monitor)
                return;
            if (!image.texture && !image.pixels.empty())
                image.texture = g_pHyprRenderer->createTexture(DRM_FORMAT_ARGB8888, reinterpret_cast<uint8_t*>(image.pixels.data()), image.pixelSize.x * 4, image.pixelSize);

            const auto       now = Time::steadyNow();
            const CHyprColor accent(sc<uint64_t>(color->value()));
            for (const auto& cursor : cursors) {
                auto window = cursor.window.lock();
                if (!window || !window->m_isMapped || !g_pHyprRenderer->shouldRenderWindow(window, monitor))
                    continue;
                const auto o = origin(window) - monitor->m_position;
                for (const auto& ripple : cursor.ripples) {
                    const double                    r = radius(ripple, now);
                    CBorderPassElement::SBorderData data;
                    data.box        = CBox{o + ripple.position - Vector2D{r, r}, Vector2D{r, r} * 2}.scale(monitor->m_scale).round();
                    data.round      = std::round(r * monitor->m_scale);
                    data.borderSize = RIPPLE_WIDTH;
                    data.grad1      = Config::CGradientValueData(CHyprColor(accent.r, accent.g, accent.b, 1.F));
                    data.a          = (1 - progress(ripple, now)) * accent.a;
                    g_pHyprRenderer->m_renderPass.add(makeUnique<CBorderPassElement>(data));
                }
                const float a = opacity(cursor, now);
                if (!image.texture || a <= 0)
                    continue;
                CTexPassElement::SRenderData data;
                data.tex = image.texture;
                data.box = CBox{o + cursor.position - image.hotspot, image.size}.scale(monitor->m_scale).round();
                data.a   = a;
                g_pHyprRenderer->m_renderPass.add(makeUnique<CTexPassElement>(std::move(data)));
            }
        }
    }

    void init(void* handle) {
        enabled = makeShared<Config::Values::CBoolValue>("plugin:hyprmasq:virtual_cursor", "show where emulated pointer input lands", true);
        color   = makeShared<Config::Values::CColorValue>("plugin:hyprmasq:virtual_cursor_color", "color of the virtual cursor and click ripples", 0xFF6EC6FF);
        if (!HyprlandAPI::addConfigValueV2(handle, enabled) || !HyprlandAPI::addConfigValueV2(handle, color))
            throw std::runtime_error("hyprmasq: cannot register config values");
        timer = wl_event_loop_add_timer(g_pCompositor->m_wlEventLoop, tick, nullptr);
        if (!timer)
            throw std::runtime_error("hyprmasq: cannot create the virtual cursor timer");
        renderListener = Event::bus()->m_events.render.stage.listen([](eRenderStage stage) { render(stage); });
    }

    void shutdown() {
        renderListener.reset();
        if (timer)
            wl_event_source_remove(timer);
        timer = nullptr;
        for (const auto& cursor : cursors)
            damage(cursor.damaged);
        cursors.clear();
        image = {};
        enabled.reset();
        color.reset();
    }

    void show(const PHLWINDOW& window, const Vector2D& position, bool click) {
        if (!enabled || !enabled->value())
            return;
        refreshImage();
        const auto now = Time::steadyNow();
        auto       it  = std::ranges::find_if(cursors, [&](const SCursor& cursor) { return cursor.window.lock() == window; });
        if (it == cursors.end())
            it = cursors.insert(cursors.end(), SCursor{});
        it->window   = window;
        it->position = position;
        it->last     = now;
        it->changed  = true;
        if (click)
            it->ripples.push_back({.position = position, .start = now});
        tick();
    }
}
