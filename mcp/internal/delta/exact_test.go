package delta

import (
	"image"
	"image/color"
	"image/draw"
	"math"
	"math/rand"
	"reflect"
	"testing"
)

// wrapped hides an image's type, so that Compare reads it through At.
type wrapped struct{ image.Image }

// part returns a w×h part of a new image that has pixels of its own around it, at origin.
func part(origin image.Point, w, h int) *image.NRGBA {
	whole := image.NewNRGBA(image.Rectangle{Min: origin, Max: origin.Add(image.Pt(w+7, h+5))})
	return whole.SubImage(image.Rectangle{Min: origin.Add(image.Pt(3, 2)), Max: origin.Add(image.Pt(w+3, h+2))}).(*image.NRGBA)
}

// randomize sets every byte of a part's pixels, and of those right of and below it, from rng: opaque pixels if opaque is
// set.
func randomize(img *image.NRGBA, rng *rand.Rand, opaque bool) {
	pix := img.Pix[:cap(img.Pix)]
	for i := range pix {
		pix[i] = uint8(rng.Intn(256))
		if opaque && i%4 == 3 {
			pix[i] = 0xff
		}
	}
}

func convert(img *image.NRGBA, kind int) image.Image {
	switch kind {
	case 1:
		out := image.NewRGBA(img.Rect)
		draw.Draw(out, out.Rect, img, img.Rect.Min, draw.Src)
		return out
	case 2:
		out := image.NewGray(img.Rect)
		draw.Draw(out, out.Rect, img, img.Rect.Min, draw.Src)
		return out
	case 3:
		return wrapped{img}
	}
	return img
}

func expectReference(t *testing.T, a, b image.Image, o Options) Result {
	t.Helper()
	got, want := Compare(a, b, o), reference(a, b, o)
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("%T %v and %T %v with %+v:\ngot  %+v\nwant %+v", a, a.Bounds(), b, b.Bounds(), o, got, want)
	}
	return got
}

// Compare returns what it returned before it compared only around the differing pixels, on every kind of input: images
// of every type that it reads, parts of images, with pixels around them that differ, at other origins in a and b,
// sizes down to a pixel, any alpha, changes at the edges, small and large, below and above the thresholds, any
// thresholds, Ignore boxes that overlap each other and the edges, limits and grouping.
func TestCompareReturnsWhatTheReferenceDoes(t *testing.T) {
	rng := rand.New(rand.NewSource(20261017))
	// 9 times these is negative, so that every pixel counts as changed.
	overflow := math.MaxInt/9 + 1
	thresholds := []int{-1, 0, 1, 24, 255, overflow}
	colorThresholds := []int{0, -1, 1, 64, 255, overflow}
	for i := 0; i < 3000; i++ {
		w, h := 1+rng.Intn(60), 1+rng.Intn(60)
		if i%7 == 0 {
			w = 1 + rng.Intn(3)
		}
		if i%11 == 0 {
			h = 1 + rng.Intn(3)
		}
		pa := part(image.Pt(rng.Intn(20)-10, rng.Intn(20)-10), w, h)
		pb := part(image.Pt(rng.Intn(20)-10, rng.Intn(20)-10), w, h)
		randomize(pa, rng, i%2 == 0)
		randomize(pb, rng, i%2 == 0)
		if i%3 != 0 {
			// A flat background, which small changes stay below the thresholds on.
			c := color.NRGBA{uint8(rng.Intn(256)), uint8(rng.Intn(256)), uint8(rng.Intn(256)), 0xff}
			fill(pa, pa.Rect, c)
		}
		draw.Draw(pb, pb.Rect, pa, pa.Rect.Min, draw.Src)
		n := rng.Intn(w*h + 1)
		switch i % 4 {
		case 0:
			n = 0
		case 1:
			n = min(n, 5)
		}
		for j := 0; j < n; j++ {
			x, y := pb.Rect.Min.X+rng.Intn(w), pb.Rect.Min.Y+rng.Intn(h)
			c := pb.NRGBAAt(x, y)
			if j%2 == 0 {
				// Often at the ends, where compositing over grey rounds differently.
				level := func() uint8 { return []uint8{0, 0xff, uint8(rng.Intn(256))}[rng.Intn(3)] }
				c = color.NRGBA{level(), level(), level(), []uint8{0xfe, uint8(rng.Intn(256))}[rng.Intn(2)]}
			} else {
				c.R += uint8(rng.Intn(41))
				c.A -= uint8(rng.Intn(3))
			}
			pb.SetNRGBA(x, y, c)
		}
		o := Options{Threshold: thresholds[i%len(thresholds)], ColorThreshold: colorThresholds[(i/len(thresholds))%len(colorThresholds)],
			Group: rng.Intn(24) - 2, Thin: rng.Intn(6), Speck: rng.Intn(8), MaxChanged: []int{0, 1, w * h / 3, -1}[i%4]}
		if i%5 < 2 {
			lo, hi := pa.Rect.Min, pa.Rect.Max
			o.Ignore = []image.Rectangle{
				image.Rect(lo.X-5, lo.Y-3, lo.X+w/2, hi.Y+3),
				image.Rect(lo.X+rng.Intn(w), lo.Y+rng.Intn(h), hi.X+2, lo.Y+h/2+1),
				image.Rect(hi.X+1, lo.Y, hi.X+9, hi.Y),
			}
		}
		var a, b image.Image = convert(pa, i%5), convert(pb, i%5)
		if i%13 == 0 {
			// Images of different types.
			a = convert(pa, 3)
		}
		expectReference(t, a, b, o)
		expectReference(t, b, a, o)
		if i%17 == 0 {
			o.Stop = func() bool { return true }
			expectReference(t, a, b, o)
		}
	}
}

// Pixels at the edges of the differing ones see in their 3×3 average the unchanged pixels around them that they see in
// the whole image, and group joins changes, or keeps them apart, as there, at every distance and grouping.
func TestComparisonAroundDifferingPixelsIsThatOfTheWhole(t *testing.T) {
	for _, group := range []int{-1, 0, 1, 2, 3, 7, 8, 9, 16} {
		o := options
		o.Group = group
		joined, apart := false, false
		// Blocks of 6×6 pixels, d apart.
		for d := 6; d <= max(0, group)+12; d++ {
			for _, step := range []image.Point{{d, 0}, {0, d}, {d, d}, {d, -d / 2}} {
				b := screen(nil)
				for _, at := range []image.Point{image.Pt(80, 40), image.Pt(80, 40).Add(step)} {
					fill(b, image.Rectangle{Min: at, Max: at.Add(image.Pt(6, 6))}, white)
				}
				res := expectReference(t, screen(nil), b, o)
				joined = joined || len(res.Changes) == 1
				apart = apart || len(res.Changes) == 2
			}
		}
		if !joined || !apart {
			t.Errorf("group %d: changes that joined %v, apart %v", group, joined, apart)
		}
	}
}

// Parts of images at other origins, whose rows are further apart than their width, compare by their own pixels: the
// pixels around them differ, and changes at their edges keep the coordinates of a.
func TestPartsOfImagesCompareTheirOwnPixels(t *testing.T) {
	rng := rand.New(rand.NewSource(1))
	a, b := part(image.Pt(-40, 25), 50, 30), part(image.Pt(13, -7), 50, 30)
	randomize(a, rng, true)
	randomize(b, rng, true)
	fill(a, a.Rect, color.NRGBA{0x12, 0x12, 0x12, 0xff})
	fill(b, b.Rect, color.NRGBA{0x12, 0x12, 0x12, 0xff})
	if res := expectReference(t, a, b, options); res.Changed != 0 || res.Changes != nil || res.Tiny != nil {
		t.Errorf("the same parts: %+v", res)
	}
	// Blocks at the top left and the bottom right corner.
	fill(b, image.Rectangle{Min: b.Rect.Min, Max: b.Rect.Min.Add(image.Pt(8, 8))}, white)
	fill(b, image.Rectangle{Min: b.Rect.Max.Sub(image.Pt(8, 8)), Max: b.Rect.Max}, white)
	res := expectReference(t, a, b, options)
	want := []image.Rectangle{
		{Min: a.Rect.Min, Max: a.Rect.Min.Add(image.Pt(9, 9))},
		{Min: a.Rect.Max.Sub(image.Pt(9, 9)), Max: a.Rect.Max},
	}
	if !reflect.DeepEqual(res.Changes, want) {
		t.Errorf("changes %v, want %v", res.Changes, want)
	}
}

// Ignore boxes, in a's coordinates, leave out changed pixels as in the whole image, also where they reach past it.
func TestIgnoreInAPartOfAnImage(t *testing.T) {
	a, b := part(image.Pt(100, 200), 80, 60), part(image.Pt(-5, 0), 80, 60)
	fill(a, a.Rect, color.NRGBA{0x12, 0x12, 0x12, 0xff})
	fill(b, b.Rect, color.NRGBA{0x12, 0x12, 0x12, 0xff})
	fill(b, image.Rectangle{Min: b.Rect.Min.Add(image.Pt(10, 10)), Max: b.Rect.Min.Add(image.Pt(30, 20))}, white)
	fill(b, image.Rectangle{Min: b.Rect.Min.Add(image.Pt(60, 40)), Max: b.Rect.Max}, white)
	o := options
	at := a.Rect.Min
	o.Ignore = []image.Rectangle{
		// The whole first block and more, and half of it again.
		image.Rect(at.X-50, at.Y-50, at.X+40, at.Y+25),
		image.Rect(at.X+10, at.Y+10, at.X+20, at.Y+20),
		// Past the bottom right corner, over part of the second block.
		image.Rect(at.X+70, at.Y+50, at.X+500, at.Y+500),
	}
	res := expectReference(t, a, b, o)
	if want := []image.Rectangle{image.Rect(at.X+59, at.Y+39, at.X+80, at.Y+60)}; !reflect.DeepEqual(res.Changes, want) || res.Ignored != 22*12+10*10 {
		t.Errorf("changes %v, %d ignored, want %v", res.Changes, res.Ignored, want)
	}
}

// Transparent pixels whose color changed stay unchanged, mid grey, and translucent ones compare their color over it.
func TestTransparentPixelsCompareOverGrey(t *testing.T) {
	a, b := screen(nil), screen(nil)
	fill(a, image.Rect(20, 20, 60, 60), color.NRGBA{0xff, 0, 0, 0})
	fill(b, image.Rect(20, 20, 60, 60), color.NRGBA{0, 0xff, 0xff, 0})
	if res := expectReference(t, a, b, options); res.Changed != 0 {
		t.Errorf("transparent: %+v", res)
	}
	for _, alpha := range []uint8{1, 0x40, 0x80, 0xfe} {
		fill(b, image.Rect(30, 30, 50, 50), color.NRGBA{0xff, 0xff, 0xff, alpha})
		expectReference(t, a, b, options)
	}
	// Black at 254 of 255 is a level lighter than opaque black, which a threshold of 0 sees.
	o := options
	o.Threshold = 0
	b = screen(nil)
	fill(a, a.Rect, color.NRGBA{0, 0, 0, 0xff})
	fill(b, b.Rect, color.NRGBA{0, 0, 0, 0xff})
	fill(b, image.Rect(20, 20, 60, 60), color.NRGBA{0, 0, 0, 0xfe})
	for _, pair := range [][2]image.Image{{a, b}, {b, a}} {
		if res := expectReference(t, pair[0], pair[1], o); len(res.Changes) != 1 {
			t.Errorf("black at 254: %+v", res)
		}
	}
}

// Compare asks Stop now and then until it is done, and gives up when it says so: a Stop that says so at its last
// question still stops a comparison, of images that are the same or not.
func TestCompareAsksStopUntilItIsDone(t *testing.T) {
	big := func(draw func(img *image.NRGBA)) *image.NRGBA {
		img := image.NewNRGBA(image.Rect(0, 0, 300, 1000))
		fill(img, img.Rect, color.NRGBA{0x12, 0x12, 0x12, 0xff})
		if draw != nil {
			draw(img)
		}
		return img
	}
	for name, b := range map[string]*image.NRGBA{
		"the same":      big(nil),
		"a small block": big(func(img *image.NRGBA) { fill(img, image.Rect(100, 400, 120, 420), white) }),
		"every row":     big(func(img *image.NRGBA) { fill(img, image.Rect(100, 0, 120, 1000), white) }),
	} {
		asked := 0
		o := options
		o.Stop = func() bool { asked++; return false }
		if res, want := Compare(big(nil), b, o), reference(big(nil), b, options); !reflect.DeepEqual(res, want) {
			t.Errorf("%s: %+v, want %+v", name, res, want)
		}
		if asked < 1000/64 {
			t.Errorf("%s: asked Stop %d times", name, asked)
		}
		last := asked
		asked = 0
		o.Stop = func() bool { asked++; return asked == last }
		if res := Compare(big(nil), b, o); !reflect.DeepEqual(res, Result{Stopped: true}) {
			t.Errorf("%s: stopped at the last of %d questions: %+v", name, last, res)
		}
	}
}

// Images that are the same take no memory to compare.
func TestTheSameImagesAllocateNothing(t *testing.T) {
	a, b := screen(nil), screen(nil)
	o := options
	o.Stop = func() bool { return false }
	if n := testing.AllocsPerRun(10, func() { Compare(a, b, o) }); n != 0 {
		t.Errorf("%v allocations", n)
	}
}
