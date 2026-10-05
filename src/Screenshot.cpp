#include "Screenshot.hpp"

#include <hyprland/src/desktop/Workspace.hpp>
#include <hyprland/src/desktop/view/Popup.hpp>
#include <hyprland/src/desktop/view/Window.hpp>
#include <hyprland/src/helpers/time/Time.hpp>
#include <hyprland/src/output/Monitor.hpp>
#include <hyprland/src/protocols/XDGShell.hpp>
#include <hyprland/src/protocols/core/Compositor.hpp>
#include <hyprland/src/render/OpenGL.hpp>
#include <hyprland/src/render/Renderer.hpp>
#include <hyprland/src/render/gl/GLFramebuffer.hpp>
#include <hyprland/src/render/pass/TexPassElement.hpp>
#include <hyprutils/math/Region.hpp>

#include <GLES3/gl32.h>
#include <png.h>

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <format>
#include <memory>
#include <utility>
#include <vector>

using namespace Hyprutils::Math;

namespace hyprmasq {
    namespace {
        // IHyprRenderer::renderWindow is protected. A pointer to it formed in a derived class is
        // ordinary C++, and avoids redefining access specifiers around the include.
        struct SRendererAccess : Render::IHyprRenderer {
            static constexpr auto RENDER_WINDOW = &SRendererAccess::renderWindow;
        };

        struct SFileCloser {
            void operator()(std::FILE* file) const {
                std::fclose(file);
            }
        };

        std::expected<void, std::string> encode(const std::filesystem::path& path, int width, int height, std::vector<uint8_t>& rgba) {
            std::unique_ptr<std::FILE, SFileCloser> file{std::fopen(path.c_str(), "wb")};
            if (!file)
                return std::unexpected(std::format("cannot open {}", path.string()));

            png_structp png  = png_create_write_struct(PNG_LIBPNG_VER_STRING, nullptr, nullptr, nullptr);
            png_infop   info = png ? png_create_info_struct(png) : nullptr;
            if (!png || !info) {
                png_destroy_write_struct(&png, &info);
                return std::unexpected("cannot create png writer");
            }

            if (setjmp(png_jmpbuf(png))) {
                png_destroy_write_struct(&png, &info);
                return std::unexpected(std::format("cannot write {}", path.string()));
            }

            png_init_io(png, file.get());
            // hyprctl answers once the file is written. libpng's defaults, zlib level 6 and every row filter tried, take
            // up to a second for a large window; level 1 with the Sub filter is 4 to 7 times faster, for files about a
            // fifth larger.
            png_set_compression_level(png, 1);
            png_set_filter(png, PNG_FILTER_TYPE_BASE, PNG_FILTER_SUB);
            png_set_IHDR(png, info, width, height, 8, PNG_COLOR_TYPE_RGBA, PNG_INTERLACE_NONE, PNG_COMPRESSION_TYPE_DEFAULT, PNG_FILTER_TYPE_DEFAULT);
            png_write_info(png, info);

            // RPT_EXPORT already renders top-down, so rows are stored in image order.
            std::vector<png_bytep> rows(height);
            for (int y = 0; y < height; ++y)
                rows[y] = rgba.data() + sc<size_t>(y) * width * 4;

            png_write_image(png, rows.data());
            png_write_end(png, nullptr);
            png_destroy_write_struct(&png, &info);
            // The last buffered bytes reach the file only here, so a full disk can show up only here.
            if (std::fclose(file.release()) != 0)
                return std::unexpected(std::format("cannot write {}", path.string()));
            return {};
        }

        // Every framebuffer holds the colors of the monitor's work buffer, as a screenshot always has, so drawing one
        // into another converts nothing.
        SP<Render::IFramebuffer> framebuffer(const PHLMONITOR& monitor, int width, int height) {
            auto fb = g_pHyprRenderer->createFB("hyprmasq screenshot");
            if (!fb || !fb->alloc(width, height, DRM_FORMAT_ABGR8888))
                return nullptr;
            fb->setImageDescription(monitor->workBufferImageDescription());
            return fb;
        }

        // Starts an offscreen render into fb, in its own pixels. Mirrors Screenshare::CScreenshareFrame::renderWindow.
        bool beginRender(const PHLMONITOR& monitor, const SP<Render::IFramebuffer>& fb, int width, int height) {
            CRegion damage = {0.0, 0.0, sc<double>(width), sc<double>(height)};
            if (!g_pHyprRenderer->beginFullFakeRender(monitor, damage, fb))
                return false;
            g_pHyprRenderer->m_renderData.fbSize          = Vector2D{width, height};
            g_pHyprRenderer->m_renderData.transformDamage = false;
            g_pHyprRenderer->setProjectionType(Render::RPT_EXPORT);
            g_pHyprRenderer->setViewport(0, 0, width, height);
            glClearColor(0.F, 0.F, 0.F, 0.F);
            glClear(GL_COLOR_BUFFER_BIT);
            return true;
        }

        void finishRender() {
            g_pHyprRenderer->m_renderData.blockScreenShader = true;
            g_pHyprRenderer->endRender();
        }

        // Draws the source's texture into a new framebuffer of the given size, at box in its pixels.
        SP<Render::IFramebuffer> resample(const PHLMONITOR& monitor, const SP<Render::IFramebuffer>& source, int width, int height, const CBox& box) {
            auto fb = framebuffer(monitor, width, height);
            if (!fb || !beginRender(monitor, fb, width, height))
                return nullptr;
            CTexPassElement::SRenderData data;
            data.tex = source->getTexture();
            data.box = box;
            g_pHyprRenderer->draw(data, CRegion{0.0, 0.0, sc<double>(width), sc<double>(height)});
            finishRender();
            return fb;
        }

        // The window-local logical box of the popups that renderWindow draws: mapped popups, their surfaces with a
        // buffer, at their parent's origin less the window's geometry offset, as the renderer places them.
        std::optional<CBox> popupExtents(const PHLWINDOW& window) {
            if (window->m_isX11 || !window->m_popupHead || !window->m_xdgSurface)
                return std::nullopt;
            const auto          geometry = window->m_xdgSurface->m_current.geometry.pos();
            std::optional<CBox> extents;
            window->m_popupHead->breadthfirst(
                [&](SP<Desktop::View::CPopup> popup, void*) {
                    const auto surface = popup->resource();
                    if (!popup->m_mapped || !surface || !surface->m_mapped)
                        return;
                    const auto origin = popup->coordsRelativeToParent() - geometry;
                    surface->breadthfirst(
                        [&](SP<CWLSurfaceResource> s, const Vector2D& offset, void*) {
                            if (!s->m_current.texture || s->m_current.size.x < 1 || s->m_current.size.y < 1)
                                return;
                            const CBox box{origin + offset, s->m_current.size};
                            if (!extents)
                                extents = box;
                            else {
                                const double x = std::min(extents->x, box.x), y = std::min(extents->y, box.y);
                                extents = CBox{x, y, std::max(extents->x + extents->w, box.x + box.w) - x, std::max(extents->y + extents->h, box.y + box.h) - y};
                            }
                        },
                        nullptr);
                },
                nullptr);
            return extents;
        }

        // While Hyprland animates a window's size, it draws the window at the animated size and fits the content to it:
        // an X11 window and a window that animates in are scaled, the subsurfaces of any window too, other windows cut
        // off or with repeated edges. Window transformers, such as motion blur, change the image further. Input reaches
        // the window at its goal size, where the application lays out its content, so a capture draws the window as it
        // is once its animations have ended: at its goal size, no longer animating in, and without transformers. The
        // render pass reads the size again when it draws, so this lasts until the pass is done. Main thread only, and
        // nothing that animates runs in between.
        class CGoalGeometry {
          public:
            explicit CGoalGeometry(const PHLWINDOW& window) : m_window(window) {
                auto& size     = window->sizeAnimation();
                m_size         = std::exchange(size->value(), size->goal());
                m_animatingIn  = std::exchange(window->m_animatingIn, false);
                m_transformers = std::exchange(window->m_transformers, {});
            }
            ~CGoalGeometry() {
                m_window->sizeAnimation()->value() = m_size;
                m_window->m_animatingIn            = m_animatingIn;
                m_window->m_transformers           = std::move(m_transformers);
            }
            CGoalGeometry(const CGoalGeometry&)            = delete;
            CGoalGeometry& operator=(const CGoalGeometry&) = delete;

          private:
            PHLWINDOW                                   m_window;
            Vector2D                                    m_size;
            bool                                        m_animatingIn = false;
            std::vector<UP<Render::IWindowTransformer>> m_transformers;
        };

        // The renderer produces premultiplied alpha, PNG stores straight alpha.
        void unpremultiply(std::vector<uint8_t>& rgba) {
            for (size_t i = 0; i < rgba.size(); i += 4) {
                const uint8_t a = rgba[i + 3];
                if (a == 0 || a == 255)
                    continue;
                for (size_t c = 0; c < 3; ++c)
                    rgba[i + c] = sc<uint8_t>(std::min(255, (rgba[i + c] * 255 + a / 2) / a));
            }
        }
    }

    std::expected<SCapture, SError> captureWindow(const PHLWINDOW& window, const SCaptureOptions& options) {
        if (!window || !window->m_isMapped)
            return std::unexpected(SError{eErrorCode::WINDOW_GONE, "window is not mapped"});

        const auto monitor = window->m_monitor.lock();
        if (!monitor)
            return std::unexpected(SError{eErrorCode::INTERNAL, "window has no monitor"});

        // The size input uses, which state reports. See CGoalGeometry.
        const auto logical      = window->size(Desktop::View::IGeometric::GEOMETRIC_GOAL);
        const int  logicalWidth = std::max(1, sc<int>(std::round(logical.x))), logicalHeight = std::max(1, sc<int>(std::round(logical.y)));

        // The box that can be captured, in window-local logical pixels: the window, or the window and its popups. Its
        // origin is whole, so the framebuffer below holds the window on the same device pixels as without popups.
        // Popups count as far as the window's monitor reaches, as pointer input does, which also bounds the framebuffer.
        int    boxX = 0, boxY = 0, boxRight = logicalWidth, boxBottom = logicalHeight;
        double right = logical.x, bottom = logical.y;
        auto   extents = options.popups ? popupExtents(window) : std::nullopt;
        if (extents) {
            const auto   screen = monitor->m_position - window->position(Desktop::View::IGeometric::GEOMETRIC_GOAL);
            const double x = std::min(0.0, screen.x), y = std::min(0.0, screen.y);
            extents = extents->intersection(CBox{x, y, std::max(logical.x, screen.x + monitor->m_size.x) - x, std::max(logical.y, screen.y + monitor->m_size.y) - y});
            if (extents->empty())
                extents.reset();
        }
        if (extents) {
            boxX      = std::min(0, sc<int>(std::floor(extents->x)));
            boxY      = std::min(0, sc<int>(std::floor(extents->y)));
            right     = std::max(right, extents->x + extents->w);
            bottom    = std::max(bottom, extents->y + extents->h);
            boxRight  = std::max(boxRight, sc<int>(std::ceil(right)));
            boxBottom = std::max(boxBottom, sc<int>(std::ceil(bottom)));
        }

        SRegion area = options.region.value_or(SRegion{boxX, boxY, boxRight - boxX, boxBottom - boxY});
        if (!options.popups) {
            if (area.x < 0 || area.y < 0 || area.width < 1 || area.height < 1)
                return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, "the region needs a non-negative position and a positive size"});
            if (area.x >= logicalWidth || area.y >= logicalHeight)
                return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, std::format("the region starts outside the {}x{} window", logicalWidth, logicalHeight)});
        } else if (area.width < 1 || area.height < 1)
            return std::unexpected(SError{eErrorCode::INVALID_ARGUMENTS, "the region needs a positive size"});
        const int areaRight  = sc<int>(std::min<int64_t>(sc<int64_t>(area.x) + area.width, boxRight));
        const int areaBottom = sc<int>(std::min<int64_t>(sc<int64_t>(area.y) + area.height, boxBottom));
        area.x               = std::max(area.x, boxX);
        area.y               = std::max(area.y, boxY);
        if (area.x >= areaRight || area.y >= areaBottom)
            return std::unexpected(
                SError{eErrorCode::INVALID_ARGUMENTS,
                       std::format("the region lies outside the {}x{} box of the window and its popups at {},{}", boxRight - boxX, boxBottom - boxY, boxX, boxY)});
        area.width  = areaRight - area.x;
        area.height = areaBottom - area.y;

        // The box is rendered at the monitor's scale, and the region read back widened to whole device pixels so that it
        // covers the requested area at fractional scales. The epsilon keeps 10 * 1.2 from rounding up to 13.
        const double scale = monitor->m_scale, epsilon = 1e-6;
        const int    width  = std::max(1, sc<int>(std::round((right - boxX) * scale)));
        const int    height = std::max(1, sc<int>(std::round((bottom - boxY) * scale)));
        int          readX  = std::clamp(sc<int>(std::floor((area.x - boxX) * scale + epsilon)), 0, width - 1);
        int          readY  = std::clamp(sc<int>(std::floor((area.y - boxY) * scale + epsilon)), 0, height - 1);
        int          readW  = std::clamp(sc<int>(std::ceil((area.x + area.width - boxX) * scale - epsilon)) - readX, 1, width - readX);
        int          readH  = std::clamp(sc<int>(std::ceil((area.y + area.height - boxY) * scale - epsilon)) - readY, 1, height - readY);

        SCapture     capture;
        capture.scale  = scale;
        capture.region = {boxX + readX / scale, boxY + readY / scale, readW / scale, readH / scale};

        // A framebuffer beyond the GPU's limit would abort Hyprland.
        GLint maxSize = 0;
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        glGetIntegerv(GL_MAX_TEXTURE_SIZE, &maxSize);
        if (width > maxSize || height > maxSize)
            return std::unexpected(SError{eErrorCode::INTERNAL, std::format("{}x{} pixels exceed the GPU's limit of {}", width, height, maxSize)});

        auto fb = framebuffer(monitor, width, height);
        if (!fb)
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot allocate framebuffer"});
        if (!dynamic_cast<Render::GL::CGLFramebuffer*>(fb.get()))
            return std::unexpected(SError{eErrorCode::INTERNAL, "only the GL renderer is supported"});
        if (!beginRender(monitor, fb, width, height))
            return std::unexpected(SError{eErrorCode::INTERNAL, "cannot begin offscreen render"});

        // Visible windows already get frame callbacks from the normal render loop.
        g_pHyprRenderer->m_bBlockSurfaceFeedback = g_pHyprRenderer->shouldRenderWindow(window);
        {
            const CGoalGeometry goal{window};
            {
                // renderWindow puts the window at the image's origin plus its floating offset, which moves popups above
                // or left of the window into the image, and draws popups only while their window is rendered on screen.
                const auto workspace = window->m_workspace;
                const auto offset    = std::exchange(window->m_floatingOffset, Vector2D{-boxX, -boxY});
                const bool forced    = workspace && std::exchange(workspace->m_forceRendering, true);
                (g_pHyprRenderer.get()->*SRendererAccess::RENDER_WINDOW)(window, monitor, Time::steadyNow(), false, Render::RENDER_PASS_ALL, true, true);
                window->m_floatingOffset = offset;
                if (workspace)
                    workspace->m_forceRendering = forced;
            }
            g_pHyprRenderer->m_bBlockSurfaceFeedback = false;
            // The box can reach past the monitor, where simplifying the render pass would cut it off.
            const bool simplify = std::exchange(g_pHyprRenderer->m_renderData.noSimplify, true);
            finishRender();
            g_pHyprRenderer->m_renderData.noSimplify = simplify;
        }

        // A smaller image is drawn from the full one on the GPU. Its pixels start at the region's origin, so the region
        // is exact in whole logical pixels and only its size is widened, to whole image pixels.
        if (options.maxPixels && std::max(readW, readH) > *options.maxPixels) {
            const double target  = std::min(scale, *options.maxPixels / sc<double>(std::max(area.width, area.height)));
            const int    outW    = std::max(1, sc<int>(std::ceil(area.width * target - epsilon)));
            const int    outH    = std::max(1, sc<int>(std::ceil(area.height * target - epsilon)));
            double       fbScale = scale;
            int          fbW = width, fbH = height;
            // Halving first takes every pixel into the average: one bilinear pass to below half the size skips some.
            while (target / fbScale < 0.5) {
                const int halfW = (fbW + 1) / 2, halfH = (fbH + 1) / 2;
                fb = resample(monitor, fb, halfW, halfH, CBox{0, 0, fbW / 2.0, fbH / 2.0});
                if (!fb)
                    return std::unexpected(SError{eErrorCode::INTERNAL, "cannot scale the screenshot"});
                fbW = halfW;
                fbH = halfH;
                fbScale /= 2;
            }
            const double factor = target / fbScale;
            fb                  = resample(monitor, fb, outW, outH, CBox{(boxX - area.x) * target, (boxY - area.y) * target, fbW * factor, fbH * factor});
            if (!fb)
                return std::unexpected(SError{eErrorCode::INTERNAL, "cannot scale the screenshot"});
            readX = readY  = 0;
            readW          = outW;
            readH          = outH;
            capture.scale  = target;
            capture.region = {sc<double>(area.x), sc<double>(area.y), outW / target, outH / target};
        }

        capture.width  = readW;
        capture.height = readH;
        capture.rgba.resize(sc<size_t>(readW) * readH * 4);
        Render::GL::g_pHyprOpenGL->makeEGLCurrent();
        glBindFramebuffer(GL_READ_FRAMEBUFFER, dynamic_cast<Render::GL::CGLFramebuffer*>(fb.get())->getFBID());
        glPixelStorei(GL_PACK_ALIGNMENT, 1);
        // RPT_EXPORT renders top-down, so framebuffer rows count from the top of the window too.
        glReadPixels(readX, readY, readW, readH, GL_RGBA, GL_UNSIGNED_BYTE, capture.rgba.data());
        glPixelStorei(GL_PACK_ALIGNMENT, 4);
        glBindFramebuffer(GL_READ_FRAMEBUFFER, 0);

        g_pHyprRenderer->m_renderData.pMonitor.reset();
        return capture;
    }

    std::expected<void, std::string> writePNG(SCapture& capture, const std::filesystem::path& output) {
        unpremultiply(capture.rgba);
        return encode(output, capture.width, capture.height, capture.rgba);
    }
}
