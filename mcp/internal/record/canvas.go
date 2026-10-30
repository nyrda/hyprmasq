package record

import (
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"math"
	"strings"
	"sync"
	"time"

	xdraw "golang.org/x/image/draw"
	"golang.org/x/image/font"
	"golang.org/x/image/font/gofont/goregular"
	"golang.org/x/image/font/opentype"
	"golang.org/x/image/math/fixed"
	"golang.org/x/image/vector"
)

// The colors of the recording. The pointer and its ripples have the color of the plugin's virtual cursor,
// plugin:hyprmasq:virtual_cursor_color's default.
var (
	backdrop    = color.RGBA{0x0e, 0x0e, 0x0e, 0xff}
	barColor    = color.RGBA{0x1b, 0x1b, 0x1b, 0xff}
	captionText = color.RGBA{0xf2, 0xf2, 0xf2, 0xff}
	cursorColor = color.RGBA{0x6e, 0xc6, 0xff, 0xff}
	outline     = color.RGBA{0x0b, 0x1a, 0x24, 0xff}
	chipText    = color.RGBA{0x0b, 0x1a, 0x24, 0xff}
)

// How long video shows a click's ripple and a key's chip.
const (
	rippleTime = 600 * time.Millisecond
	chipTime   = 1500 * time.Millisecond
)

// A canvas is the picture of the video: the window's frame above a bar with the caption and the chips of keys.
type canvas struct {
	// The whole picture, and the part above the bar where frames go.
	width, height  int
	frameW, frameH int
	barH           int
	// The size of the pointer and the marks, relative to a frame 1280 pixels across.
	unit             float64
	caption, chip    font.Face
	captionBaseline  int
	chipH, chipInset int
	// The last frame drawn, without its overlay, which video draws again and again.
	baseOf *Frame
	base   *image.RGBA
}

var (
	fontOnce sync.Once
	goFont   *opentype.Font
	fontErr  error
)

func newCanvas(frameW, frameH int) (*canvas, error) {
	fontOnce.Do(func() { goFont, fontErr = opentype.Parse(goregular.TTF) })
	if fontErr != nil {
		return nil, fontErr
	}
	// H.264 in yuv420p needs an even width and height.
	even := func(v int) int { return max(2, v+v%2) }
	frameW, frameH = even(frameW), even(frameH)
	barH := even(min(80, max(40, frameW/22)))
	c := &canvas{width: frameW, height: frameH + barH, frameW: frameW, frameH: frameH, barH: barH, unit: max(0.8, float64(frameW)/1280*1.15)}
	face := func(size float64) (font.Face, error) {
		return opentype.NewFace(goFont, &opentype.FaceOptions{Size: size, DPI: 72, Hinting: font.HintingFull})
	}
	var err error
	if c.caption, err = face(float64(barH) * 0.4); err != nil {
		return nil, err
	}
	if c.chip, err = face(float64(barH) * 0.34); err != nil {
		return nil, err
	}
	m := c.caption.Metrics()
	c.captionBaseline = frameH + (barH+(m.Ascent-m.Descent).Ceil())/2
	c.chipH = int(float64(barH) * 0.62)
	c.chipInset = int(float64(barH) * 0.24)
	return c, nil
}

// placement maps a frame onto the canvas: shrunk to fit if it is bigger, and centred.
type placement struct {
	f      *Frame
	scale  float64
	offset [2]float64
}

func (c *canvas) place(f *Frame) placement {
	w, h := float64(f.Image.Rect.Dx()), float64(f.Image.Rect.Dy())
	s := math.Min(1, math.Min(float64(c.frameW)/w, float64(c.frameH)/h))
	return placement{f: f, scale: s, offset: [2]float64{(float64(c.frameW) - w*s) / 2, (float64(c.frameH) - h*s) / 2}}
}

// point maps a window-local logical point to the canvas.
func (p placement) point(pt [2]float64) [2]float64 {
	x := (pt[0] - p.f.Region.X) * p.f.Scale
	y := (pt[1] - p.f.Region.Y) * p.f.Scale
	return [2]float64{p.offset[0] + x*p.scale, p.offset[1] + y*p.scale}
}

// An overlay is what is drawn over a frame: the input in the window's logical pixels, and the bar's text.
type overlay struct {
	pointer *[2]float64
	ripples []ripple
	paths   [][][2]float64
	chips   []string
	caption string
}

type ripple struct {
	at [2]float64
	// 0 to 1 while video plays it, or -1 for the still ring of slides.
	progress float64
	label    string
}

// compose draws the frame and the overlay into a new picture.
func (c *canvas) compose(f *Frame, ov overlay) *image.RGBA {
	if c.baseOf != f {
		c.base = c.drawBase(f)
		c.baseOf = f
	}
	img := image.NewRGBA(c.base.Rect)
	copy(img.Pix, c.base.Pix)
	p := c.place(f)
	for _, path := range ov.paths {
		pts := make([][2]float64, len(path))
		for i, pt := range path {
			pts[i] = p.point(pt)
		}
		fill(img, outline, polyline(pts, 6*c.unit)...)
		fill(img, cursorColor, polyline(pts, 3.5*c.unit)...)
	}
	for _, r := range ov.ripples {
		at := p.point(r.at)
		radius, alpha := 13*c.unit, 1.0
		if r.progress >= 0 {
			radius, alpha = (6+16*r.progress)*c.unit, 1-r.progress*0.8
		}
		fill(img, fade(outline, alpha), ring(at, radius+2.5*c.unit, 5.5*c.unit)...)
		fill(img, fade(cursorColor, alpha), ring(at, radius+1.5*c.unit, 3*c.unit)...)
		if r.label != "" {
			c.shadowText(img, r.label, int(at[0]+radius+5*c.unit), int(at[1]-radius))
		}
	}
	if ov.pointer != nil {
		c.drawPointer(img, p.point(*ov.pointer))
	}
	c.drawBar(img, ov)
	return img
}

// drawBase draws the frame on the backdrop, and the empty bar.
func (c *canvas) drawBase(f *Frame) *image.RGBA {
	img := image.NewRGBA(image.Rect(0, 0, c.width, c.height))
	draw.Draw(img, image.Rect(0, 0, c.frameW, c.frameH), image.NewUniform(backdrop), image.Point{}, draw.Src)
	draw.Draw(img, image.Rect(0, c.frameH, c.width, c.height), image.NewUniform(barColor), image.Point{}, draw.Src)
	p := c.place(f)
	w, h := f.Image.Rect.Dx(), f.Image.Rect.Dy()
	at := image.Pt(int(math.Round(p.offset[0])), int(math.Round(p.offset[1])))
	if p.scale == 1 {
		draw.Draw(img, image.Rectangle{at, at.Add(image.Pt(w, h))}, f.Image, f.Image.Rect.Min, draw.Over)
	} else {
		size := image.Pt(int(math.Round(float64(w)*p.scale)), int(math.Round(float64(h)*p.scale)))
		xdraw.ApproxBiLinear.Scale(img, image.Rectangle{at, at.Add(size)}, f.Image, f.Image.Rect, xdraw.Over, nil)
	}
	return img
}

// drawBar writes the caption on the left of the bar and the chips on its right, which leave out what does not fit.
func (c *canvas) drawBar(img *image.RGBA, ov overlay) {
	margin := c.barH / 3
	right := c.width - margin
	gap := c.barH / 6
	top := c.frameH + (c.barH-c.chipH)/2
	cm := c.chip.Metrics()
	baseline := top + (c.chipH+(cm.Ascent-cm.Descent).Ceil())/2
	// The latest chips are rightmost.
	for i := len(ov.chips) - 1; i >= 0; i-- {
		label := ov.chips[i]
		w := font.MeasureString(c.chip, label).Ceil() + 2*c.chipInset
		left := right - w
		if left < c.width/2 {
			break
		}
		r := float64(c.chipH) / 2
		fill(img, cursorColor, roundedRect(float64(left), float64(top), float64(right), float64(top+c.chipH), r))
		d := font.Drawer{Dst: img, Src: image.NewUniform(chipText), Face: c.chip, Dot: fixed.P(left+c.chipInset, baseline)}
		d.DrawString(label)
		right = left - gap
	}
	if ov.caption == "" {
		return
	}
	text := fitText(c.caption, ov.caption, right-gap-margin)
	d := font.Drawer{Dst: img, Src: image.NewUniform(captionText), Face: c.caption, Dot: fixed.P(margin, c.captionBaseline)}
	d.DrawString(text)
}

// fitText shortens text with an ellipsis until it fits within width pixels.
func fitText(face font.Face, text string, width int) string {
	if font.MeasureString(face, text).Ceil() <= width {
		return text
	}
	runes := []rune(text)
	for n := len(runes) - 1; n > 0; n-- {
		short := strings.TrimRight(string(runes[:n]), " ") + "…"
		if font.MeasureString(face, short).Ceil() <= width {
			return short
		}
	}
	return ""
}

// shadowText writes a small label in the pointer's color with a dark edge, readable on any window.
func (c *canvas) shadowText(img *image.RGBA, text string, x, y int) {
	for _, off := range [][2]int{{-1, 0}, {1, 0}, {0, -1}, {0, 1}, {1, 1}} {
		d := font.Drawer{Dst: img, Src: image.NewUniform(outline), Face: c.chip, Dot: fixed.P(x+off[0], y+off[1])}
		d.DrawString(text)
	}
	d := font.Drawer{Dst: img, Src: image.NewUniform(cursorColor), Face: c.chip, Dot: fixed.P(x, y)}
	d.DrawString(text)
}

// drawPointer draws an arrow with its tip at the point.
func (c *canvas) drawPointer(img *image.RGBA, tip [2]float64) {
	shape := [][2]float64{{0, 0}, {0, 18}, {4.5, 14}, {7.5, 21}, {10.5, 19.8}, {7.5, 13}, {13, 13}}
	pts := make([][2]float64, len(shape))
	for i, p := range shape {
		pts[i] = [2]float64{tip[0] + p[0]*c.unit*1.15, tip[1] + p[1]*c.unit*1.15}
	}
	closed := append(append([][2]float64(nil), pts...), pts[0])
	fill(img, outline, polyline(closed, 2.6*c.unit)...)
	fill(img, cursorColor, pts)
}

func fade(c color.RGBA, alpha float64) color.NRGBA {
	return color.NRGBA{c.R, c.G, c.B, uint8(math.Round(float64(c.A) * math.Max(0, math.Min(1, alpha))))}
}

// fill draws the polygons in one color, as their union, except where a polygon winds the other way, which cuts it out.
func fill(dst *image.RGBA, col color.Color, polys ...[][2]float64) {
	minX, minY, maxX, maxY := math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)
	for _, p := range polys {
		for _, pt := range p {
			minX, minY = math.Min(minX, pt[0]), math.Min(minY, pt[1])
			maxX, maxY = math.Max(maxX, pt[0]), math.Max(maxY, pt[1])
		}
	}
	box := image.Rect(int(math.Floor(minX)), int(math.Floor(minY)), int(math.Ceil(maxX))+1, int(math.Ceil(maxY))+1)
	if box.Empty() || !box.Overlaps(dst.Rect) {
		return
	}
	z := vector.NewRasterizer(box.Dx(), box.Dy())
	for _, p := range polys {
		if len(p) < 3 {
			continue
		}
		z.MoveTo(float32(p[0][0]-float64(box.Min.X)), float32(p[0][1]-float64(box.Min.Y)))
		for _, pt := range p[1:] {
			z.LineTo(float32(pt[0]-float64(box.Min.X)), float32(pt[1]-float64(box.Min.Y)))
		}
		z.ClosePath()
	}
	mask := image.NewAlpha(image.Rect(0, 0, box.Dx(), box.Dy()))
	z.Draw(mask, mask.Rect, image.Opaque, image.Point{})
	draw.DrawMask(dst, box, image.NewUniform(col), image.Point{}, mask, image.Point{}, draw.Over)
}

// circle is a polygon around a circle, counterclockwise on the screen, or clockwise.
func circle(center [2]float64, radius float64, clockwise bool) [][2]float64 {
	n := max(16, int(radius))
	pts := make([][2]float64, n)
	for i := range pts {
		a := 2 * math.Pi * float64(i) / float64(n)
		if clockwise {
			a = -a
		}
		pts[i] = [2]float64{center[0] + radius*math.Cos(a), center[1] + radius*math.Sin(a)}
	}
	return pts
}

// ring is the outline of a circle of the radius, width pixels wide inwards.
func ring(center [2]float64, radius, width float64) [][][2]float64 {
	return [][][2]float64{circle(center, radius, false), circle(center, math.Max(0, radius-width), true)}
}

// polyline is a line through the points, width pixels wide with round joins and ends, as polygons that wind alike.
func polyline(pts [][2]float64, width float64) [][][2]float64 {
	var out [][][2]float64
	for i, p := range pts {
		out = append(out, circle(p, width/2, false))
		if i == 0 {
			continue
		}
		a := pts[i-1]
		dx, dy := p[0]-a[0], p[1]-a[1]
		length := math.Hypot(dx, dy)
		if length == 0 {
			continue
		}
		nx, ny := -dy/length*width/2, dx/length*width/2
		quad := [][2]float64{{a[0] + nx, a[1] + ny}, {p[0] + nx, p[1] + ny}, {p[0] - nx, p[1] - ny}, {a[0] - nx, a[1] - ny}}
		out = append(out, wound(quad))
	}
	return out
}

// wound turns a polygon to wind as circle does without clockwise.
func wound(p [][2]float64) [][2]float64 {
	area := 0.0
	for i := range p {
		j := (i + 1) % len(p)
		area += p[i][0]*p[j][1] - p[j][0]*p[i][1]
	}
	if area < 0 {
		for i, j := 0, len(p)-1; i < j; i, j = i+1, j-1 {
			p[i], p[j] = p[j], p[i]
		}
	}
	return p
}

// roundedRect is a rectangle with corners of the radius.
func roundedRect(x0, y0, x1, y1, r float64) [][2]float64 {
	var pts [][2]float64
	corner := func(cx, cy, from float64) {
		for i := 0; i <= 8; i++ {
			a := from + math.Pi/2*float64(i)/8
			pts = append(pts, [2]float64{cx + r*math.Cos(a), cy + r*math.Sin(a)})
		}
	}
	corner(x1-r, y1-r, 0)
	corner(x0+r, y1-r, math.Pi/2)
	corner(x0+r, y0+r, math.Pi)
	corner(x1-r, y0+r, 3*math.Pi/2)
	return pts
}

// clickLabel names a click that is not a single left click, such as "2×" or "right".
func clickLabel(m Mark) string {
	var parts []string
	if m.Label != "" {
		parts = append(parts, m.Label)
	}
	if m.Button != "" && m.Button != "left" {
		parts = append(parts, m.Button)
	}
	if m.Count > 1 {
		parts = append(parts, fmt.Sprintf("%d×", m.Count))
	}
	return strings.Join(parts, " ")
}
