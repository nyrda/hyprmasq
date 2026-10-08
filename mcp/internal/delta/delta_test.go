package delta

import (
	"image"
	"image/color"
	"reflect"
	"testing"
)

var options = Options{Threshold: 24, ColorThreshold: 64, Group: 8, Thin: 4, Speck: 5}

// screen returns a dark 200×100 image with what draw paints on it.
func screen(draw func(img *image.NRGBA)) *image.NRGBA {
	img := image.NewNRGBA(image.Rect(0, 0, 200, 100))
	fill(img, img.Bounds(), color.NRGBA{0x12, 0x12, 0x12, 0xff})
	if draw != nil {
		draw(img)
	}
	return img
}

func fill(img *image.NRGBA, r image.Rectangle, c color.NRGBA) {
	for y := r.Min.Y; y < r.Max.Y; y++ {
		for x := r.Min.X; x < r.Max.X; x++ {
			img.SetNRGBA(x, y, c)
		}
	}
}

var white = color.NRGBA{0xff, 0xff, 0xff, 0xff}

func TestIdenticalImagesHaveNoChanges(t *testing.T) {
	res := Compare(screen(nil), screen(nil), options)
	if res.Changed != 0 || res.Changes != nil || res.Tiny != nil {
		t.Errorf("%+v", res)
	}
}

func TestABlockIsAChangeWithItsBox(t *testing.T) {
	block := image.Rect(40, 20, 60, 36)
	res := Compare(screen(nil), screen(func(img *image.NRGBA) { fill(img, block, white) }), options)
	// The 3×3 average reaches a pixel past each edge.
	if want := []image.Rectangle{block.Inset(-1)}; !reflect.DeepEqual(res.Changes, want) || res.Tiny != nil {
		t.Errorf("changes %v tiny %v, want %v", res.Changes, res.Tiny, want)
	}
}

func TestCaretsAndSpecksAreTiny(t *testing.T) {
	caret := image.Rect(100, 10, 101, 30)
	speck := image.Rect(150, 60, 152, 62)
	res := Compare(screen(nil), screen(func(img *image.NRGBA) {
		fill(img, caret, white)
		fill(img, speck, white)
	}), options)
	if res.Changes != nil || len(res.Tiny) != 2 {
		t.Fatalf("changes %v tiny %v", res.Changes, res.Tiny)
	}
	for _, box := range res.Tiny {
		if !box.Overlaps(caret) && !box.Overlaps(speck) {
			t.Errorf("tiny change %v is neither", box)
		}
	}
}

// Subpixel antialiasing moves color between the channels of neighbouring pixels, as Chromium's switch to grayscale
// antialiasing shows: each channel of a glyph's stem keeps its sum, so the 3×3 average of luminance hardly changes.
func TestAntialiasingSwitchIsNoChange(t *testing.T) {
	stem := func(img *image.NRGBA, sub bool) {
		for y := 20; y < 40; y++ {
			if sub {
				img.SetNRGBA(49, y, color.NRGBA{40, 80, 120, 0xff})
				img.SetNRGBA(50, y, color.NRGBA{0xe0, 0xe0, 0xe0, 0xff})
				img.SetNRGBA(51, y, color.NRGBA{120, 80, 40, 0xff})
			} else {
				img.SetNRGBA(49, y, color.NRGBA{0x50, 0x50, 0x50, 0xff})
				img.SetNRGBA(50, y, color.NRGBA{0xe0, 0xe0, 0xe0, 0xff})
				img.SetNRGBA(51, y, color.NRGBA{0x50, 0x50, 0x50, 0xff})
			}
		}
	}
	res := Compare(screen(func(img *image.NRGBA) { stem(img, true) }), screen(func(img *image.NRGBA) { stem(img, false) }), options)
	if res.Changes != nil || res.Tiny != nil {
		t.Errorf("changes %v tiny %v", res.Changes, res.Tiny)
	}
	// A low-contrast highlight, such as a hover in a dark theme, stays below the threshold.
	res = Compare(screen(nil), screen(func(img *image.NRGBA) { fill(img, image.Rect(0, 50, 200, 70), color.NRGBA{0x2a, 0x2a, 0x2a, 0xff}) }), options)
	if res.Changed != 0 {
		t.Errorf("hover %+v", res)
	}
}

func TestNearbyPixelsGroupIntoOneChange(t *testing.T) {
	// Letters 4 pixels apart are one word; a word 30 pixels away is another change.
	res := Compare(screen(nil), screen(func(img *image.NRGBA) {
		for _, x := range []int{20, 30, 40} {
			fill(img, image.Rect(x, 40, x+6, 52), white)
		}
		fill(img, image.Rect(76, 40, 90, 52), white)
	}), options)
	want := []image.Rectangle{image.Rect(19, 39, 47, 53), image.Rect(75, 39, 91, 53)}
	if !reflect.DeepEqual(res.Changes, want) {
		t.Errorf("changes %v, want %v", res.Changes, want)
	}
}

func TestTransparentPixelsChange(t *testing.T) {
	res := Compare(screen(nil), screen(func(img *image.NRGBA) { fill(img, image.Rect(0, 0, 30, 30), color.NRGBA{}) }), options)
	if len(res.Changes) != 1 || res.Changes[0] != image.Rect(0, 0, 31, 31) {
		t.Errorf("changes %v", res.Changes)
	}
}

func TestCompareStopsAtMaxChanged(t *testing.T) {
	o := options
	o.MaxChanged = 1000
	res := Compare(screen(nil), screen(func(img *image.NRGBA) { fill(img, image.Rect(0, 0, 100, 100), white) }), o)
	if !res.Over(o) || res.Changes != nil {
		t.Errorf("%+v", res)
	}
	if o.MaxChanged = 0; Compare(screen(nil), screen(nil), o).Over(o) {
		t.Error("over without a limit")
	}
}

func TestDilateReachesTheEdges(t *testing.T) {
	mask := make([]bool, 6*5)
	mask[0] = true     // 0,0
	mask[4*6+5] = true // 5,4
	got := dilate(mask, 6, 5, 1)
	for y := 0; y < 5; y++ {
		for x := 0; x < 6; x++ {
			want := (x <= 1 && y <= 1) || (x >= 4 && y >= 3)
			if got[y*6+x] != want {
				t.Errorf("%d,%d is %v", x, y, got[y*6+x])
			}
		}
	}
}

func TestGrayImages(t *testing.T) {
	a, b := image.NewGray(image.Rect(0, 0, 50, 50)), image.NewGray(image.Rect(0, 0, 50, 50))
	for y := 10; y < 20; y++ {
		for x := 10; x < 30; x++ {
			b.SetGray(x, y, color.Gray{200})
		}
	}
	if res := Compare(a, b, options); len(res.Changes) != 1 || res.Changes[0] != image.Rect(9, 9, 31, 21) {
		t.Errorf("changes %v", res.Changes)
	}
}

// A red popup over a blue window of about the same luminance changes the color channels.
func TestColorChangeOfTheSameLuminance(t *testing.T) {
	blue, red := color.NRGBA{0x22, 0x44, 0x66, 0xff}, color.NRGBA{0xcc, 0x22, 0x22, 0xff}
	popup := image.Rect(20, 20, 80, 60)
	a := screen(func(img *image.NRGBA) { fill(img, img.Bounds(), blue) })
	b := screen(func(img *image.NRGBA) {
		fill(img, img.Bounds(), blue)
		fill(img, popup, red)
	})
	// A third of the red channel's change, at the pixels next to the popup, stays below the threshold.
	if res := Compare(a, b, options); !reflect.DeepEqual(res.Changes, []image.Rectangle{popup}) {
		t.Errorf("changes %v", res.Changes)
	}
	o := options
	o.ColorThreshold = 0
	if res := Compare(a, b, o); res.Changed != 0 {
		t.Errorf("luminance alone: %+v", res)
	}
}

func TestCompareStops(t *testing.T) {
	o := options
	o.Stop = func() bool { return true }
	res := Compare(screen(nil), screen(func(img *image.NRGBA) { fill(img, image.Rect(10, 10, 50, 50), white) }), o)
	if !res.Stopped || res.Changes != nil || res.Changed != 0 {
		t.Errorf("%+v", res)
	}
}
