package server

import (
	"bytes"
	"image"
	"image/png"
	"reflect"
	"regexp"
	"testing"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestMagnification(t *testing.T) {
	for _, c := range []struct{ w, h, min, max, want int }{
		{40, 20, 768, 1280, 4},   // at most 4
		{300, 200, 768, 1280, 3}, // the smallest factor that reaches 768 on the long side
		{200, 300, 768, 1280, 3}, // the long side may be the height
		{640, 100, 768, 1280, 2}, // 1280 still fits
		{700, 400, 768, 1280, 1}, // 2× would pass 1280, and no whole factor lies between
		{700, 400, 768, 0, 2},    // without --max-image-size
		{200, 100, 768, 600, 3},  // within --max-image-size
		{768, 100, 768, 1280, 1}, // large enough
		{200, 100, 0, 1280, 1},   // off
	} {
		if got := magnification(c.w, c.h, c.min, c.max); got != c.want {
			t.Errorf("magnification(%d, %d, %d, %d) = %d, want %d", c.w, c.h, c.min, c.max, got, c.want)
		}
	}
}

// A small zoom, from screenshot or act's capture, comes enlarged, each captured pixel a block, and act maps its pixels,
// and those of a zoom into it, to the window, also between the captured pixels.
func TestSmallZoomIsEnlarged(t *testing.T) {
	e := newTestEnv(t)
	e.server.cfg.MaxImageSize, e.server.cfg.MinZoomSize = 1280, 768
	e.window("0xa1", "1a", 320, 200)
	e.paint("0xa1", hypr.Rect{X: 50, Y: 25, Width: 1, Height: 1})
	if res := e.screenshot(`{"window":"0xa1"}`); res.IsError {
		t.Fatal(text(res))
	}
	// 20×10 logical pixels at the monitor's scale of 2 are 40×20, enlarged 4 times.
	res := e.screenshot(`{"window":"0xa1","region":{"shot":"s1","x":80,"y":40,"width":40,"height":20}}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	if got := text(res); !regexp.MustCompile(`^shot s2 160×80 `).MatchString(got) {
		t.Errorf("text %q", got)
	}
	// The white logical pixel at 50,25 is 2×2 captured pixels at 20,10, each a block of 4×4.
	img := imageOf(t, res)
	for _, p := range []struct {
		x, y  int
		white bool
	}{{79, 44, false}, {80, 40, true}, {87, 47, true}, {88, 44, false}, {84, 48, false}} {
		if v, _, _, _ := img.At(p.x, p.y).RGBA(); (v > 0) != p.white {
			t.Errorf("pixel %d,%d white %v, want %v", p.x, p.y, v > 0, p.white)
		}
	}
	// The stale-target check compares the window with the captured pixels, which have not changed.
	if res := e.act(`{"window":"0xa1","shot":"s2","actions":[{"action":"click","x":80,"y":40},{"action":"click","x":84,"y":44}],"capture":"none"}`); res.IsError {
		t.Fatal(text(res))
	}
	want := [][]string{{"move", "50", "25", "click", "left", "1", "-", "sync", "0", "1000", "move", "50.5", "25.5", "click", "left", "1", "-"}}
	if got := e.inputs(); !reflect.DeepEqual(got, want) {
		t.Errorf("inputs %q, want %q", got, want)
	}
	// A zoom into the enlarged zoom is read in its pixels.
	res = e.screenshot(`{"window":"0xa1","region":{"shot":"s2","x":80,"y":40,"width":8,"height":8}}`)
	if res.IsError || !reflect.DeepEqual(get(t, res, "shot.frame"), map[string]any{"x": 50.0, "y": 25.0, "width": 1.0, "height": 1.0}) || get(t, res, "shot.width") != 8.0 {
		t.Errorf("zoom of the zoom: %s", text(res))
	}
	// act's capture region is a zoom too.
	res = e.act(`{"window":"0xa1","shot":"s1","actions":[{"action":"move","x":0,"y":0}],"capture":{"shot":"s1","x":80,"y":40,"width":40,"height":20}}`)
	if res.IsError || get(t, res, "shot.width") != 160.0 || get(t, res, "shot.height") != 80.0 {
		t.Errorf("capture region: %s", text(res))
	}
}

// imageOf decodes the result's first image.
func imageOf(t *testing.T, res *mcp.CallToolResult) image.Image {
	t.Helper()
	for _, c := range res.Content {
		if data, ok := c.(*mcp.ImageContent); ok {
			img, err := png.Decode(bytes.NewReader(data.Data))
			if err != nil {
				t.Fatal(err)
			}
			return img
		}
	}
	t.Fatal("no image")
	return nil
}
