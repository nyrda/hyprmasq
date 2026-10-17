// Package delta finds what changed between two screenshots of a window: boxes of changed pixels, grouped into changes,
// with tiny ones, such as a blinking caret, told apart.
package delta

import (
	"bytes"
	"image"
	"image/color"
	"slices"
)

// Options are in pixels of the compared images.
type Options struct {
	// How many of 255 levels the average of the 3×3 pixels around a pixel has to change by in luminance, or in one of
	// the color channels, for the pixel to count as changed. Averages ignore a switch of text antialiasing between
	// subpixel and grayscale, which Chromium makes when it draws a layer anew, and which moves color between
	// neighbouring pixels. The channels catch a change of color that keeps the luminance, such as a red popup opening
	// over a blue window, at a higher threshold, since antialiasing moves more of a channel than of the luminance.
	Threshold, ColorThreshold int
	// Changed pixels at most this far apart belong to one change, such as the letters of a word.
	Group int
	// A change at most Thin pixels wide or tall, such as a blinking caret, or at most Speck pixels in both directions,
	// is tiny.
	Thin, Speck int
	// When more pixels than this have changed, Compare only counts them and finds no changes. 0 has no limit.
	MaxChanged int
	// Pixels within these boxes, in the images' coordinates, count as unchanged.
	Ignore []image.Rectangle
	// Compare asks Stop now and then, if it is set, and gives up when it says so.
	Stop func() bool
}

// Result is what changed between two images, in their pixels.
type Result struct {
	// The changes that are not tiny, top to bottom.
	Changes []image.Rectangle
	// The tiny changes.
	Tiny []image.Rectangle
	// How many pixels changed, and how many changed pixels Ignore left out of that.
	Changed, Ignored int
	// Stop ended the comparison, so the result says nothing.
	Stopped bool
}

// Over tells whether more than MaxChanged pixels changed, so that Compare found no changes.
func (r Result) Over(o Options) bool {
	return o.MaxChanged > 0 && r.Changed > o.MaxChanged
}

// Compare compares two images of the same size. Transparent pixels count as mid grey, so that a pixel that becomes
// transparent changes.
func Compare(a, b image.Image, o Options) Result {
	w, h := a.Bounds().Dx(), a.Bounds().Dy()
	if b.Bounds().Dx() != w || b.Bounds().Dy() != h || w == 0 || h == 0 {
		panic("delta: the images differ in size")
	}
	limits, checked := planeLimits(o)
	na, okA := a.(*image.NRGBA)
	nb, okB := b.(*image.NRGBA)
	// Only pixels near those that differ can change, unless a negative limit counts the others as changed too.
	if !okA || !okB || slices.Min(limits[:checked]) < 0 {
		return compare(a, b, o)
	}
	area, stopped := differing(na, nb, o.Stop)
	switch {
	case stopped:
		return Result{Stopped: true}
	case area.Empty():
		return Result{}
	}
	// The pixels that the 3×3 average spreads a difference to, with a pixel to spare, which they see as they would in
	// the whole image, and those that group widens them by, which join them as in the whole image.
	halo := max(2, 1+o.Group/2+o.Group%2)
	area = area.Inset(-halo).Intersect(image.Rect(0, 0, w, h))
	return compare(na.SubImage(area.Add(na.Rect.Min)), nb.SubImage(area.Add(nb.Rect.Min)), o)
}

// differing returns the box of the pixels that differ between two images of the same size, from their top left, unless
// stop ends it.
func differing(a, b *image.NRGBA, stop func() bool) (image.Rectangle, bool) {
	w, h := a.Rect.Dx(), a.Rect.Dy()
	x0, y0, x1, y1 := w, h, 0, 0
	for y := 0; y < h; y++ {
		if stop != nil && y%64 == 0 && stop() {
			return image.Rectangle{}, true
		}
		ra, rb := a.Pix[y*a.Stride:y*a.Stride+4*w], b.Pix[y*b.Stride:y*b.Stride+4*w]
		if bytes.Equal(ra, rb) {
			continue
		}
		y0, y1 = min(y0, y), y+1
		// Only bytes left or right of the box so far can widen it.
		if !bytes.Equal(ra[:4*x0], rb[:4*x0]) {
			i := 0
			for ra[i] == rb[i] {
				i++
			}
			x0 = i / 4
		}
		if !bytes.Equal(ra[4*x1:], rb[4*x1:]) {
			i := len(ra) - 1
			for ra[i] == rb[i] {
				i--
			}
			x1 = i/4 + 1
		}
	}
	if y1 == 0 {
		return image.Rectangle{}, false
	}
	return image.Rect(x0, y0, x1, y1), false
}

// compare compares two images of the same size pixel by pixel.
func compare(a, b image.Image, o Options) Result {
	bounds := a.Bounds()
	w, h := bounds.Dx(), bounds.Dy()
	mask, changed, stopped := changedPixels(a, b, w, h, o)
	if stopped {
		return Result{Stopped: true}
	}
	ignored := 0
	for _, r := range o.Ignore {
		r = r.Sub(bounds.Min).Intersect(image.Rect(0, 0, w, h))
		for y := r.Min.Y; y < r.Max.Y; y++ {
			for x := r.Min.X; x < r.Max.X; x++ {
				if mask[y*w+x] {
					mask[y*w+x] = false
					ignored++
				}
			}
		}
	}
	res := Result{Changed: changed - ignored, Ignored: ignored}
	if res.Changed == 0 || res.Over(o) {
		return res
	}
	for _, box := range group(mask, w, h, max(0, o.Group)) {
		box = box.Add(bounds.Min)
		if min(box.Dx(), box.Dy()) <= o.Thin || max(box.Dx(), box.Dy()) <= o.Speck {
			res.Tiny = append(res.Tiny, box)
		} else {
			res.Changes = append(res.Changes, box)
		}
	}
	return res
}

// The planes that are compared: the luminance, and the red, green and blue channels.
const planes = 4

// rowDiffs keeps, for the rows around the one being compared, the sum of every 3 pixels across of how much a exceeds b
// in every plane, with the edge pixels repeated beyond the edges. Differences add up to the difference of the sums,
// which is what the 3×3 averages compare, and fit an int16: a sum of 9 pixels differs by at most 9×255.
type rowDiffs struct {
	a, b image.Image
	// Both images, when they are NRGBA, as the server's are.
	na, nb *image.NRGBA
	w      int
	// The values of a row of each image, for other images, and how much they differ.
	va, vb [planes][]uint8
	diff   [planes][]int16
	// By row modulo 3.
	ring [3][planes][]int16
}

func newRowDiffs(a, b image.Image, w int) *rowDiffs {
	s := &rowDiffs{a: a, b: b, w: w}
	na, okA := a.(*image.NRGBA)
	nb, okB := b.(*image.NRGBA)
	if okA && okB {
		s.na, s.nb = na, nb
	}
	for p := range planes {
		if s.na == nil {
			s.va[p], s.vb[p] = make([]uint8, w), make([]uint8, w)
		}
		s.diff[p] = make([]int16, w)
		for r := range s.ring {
			s.ring[r][p] = make([]int16, w)
		}
	}
	return s
}

// over composites a channel of straight alpha over mid grey.
func over(c, a uint32) uint8 {
	return uint8((c*a + 128*(255-a) + 127) / 255)
}

// luminance has Rec. 709 weights in 1/256.
func luminance(r, g, b uint32) uint32 {
	return (54*r + 183*g + 19*b) >> 8
}

// load computes the sums of row y.
func (s *rowDiffs) load(y int) {
	w := s.w
	lum, red, green, blue := s.diff[0][:w], s.diff[1][:w], s.diff[2][:w], s.diff[3][:w]
	if s.na != nil {
		// What values does, for both images, without a call for every pixel, which the compiler does not inline. Opaque
		// pixels are their own composite.
		rowA, rowB := s.na.Pix[y*s.na.Stride:y*s.na.Stride+4*w], s.nb.Pix[y*s.nb.Stride:y*s.nb.Stride+4*w]
		for x := range lum {
			pa, pb := rowA[4*x:4*x+4:4*x+4], rowB[4*x:4*x+4:4*x+4]
			ra, ga, ba, aa := uint32(pa[0]), uint32(pa[1]), uint32(pa[2]), uint32(pa[3])
			la := luminance(ra, ga, ba)
			if aa != 0xff {
				la, ra, ga, ba = uint32(over(la, aa)), uint32(over(ra, aa)), uint32(over(ga, aa)), uint32(over(ba, aa))
			}
			rb, gb, bb, ab := uint32(pb[0]), uint32(pb[1]), uint32(pb[2]), uint32(pb[3])
			lb := luminance(rb, gb, bb)
			if ab != 0xff {
				lb, rb, gb, bb = uint32(over(lb, ab)), uint32(over(rb, ab)), uint32(over(gb, ab)), uint32(over(bb, ab))
			}
			lum[x], red[x], green[x], blue[x] = int16(la)-int16(lb), int16(ra)-int16(rb), int16(ga)-int16(gb), int16(ba)-int16(bb)
		}
	} else {
		values(s.a, y, w, s.va)
		values(s.b, y, w, s.vb)
		for p := range planes {
			va, vb, d := s.va[p][:w], s.vb[p][:w], s.diff[p][:w]
			for x := range d {
				d[x] = int16(va[x]) - int16(vb[x])
			}
		}
	}
	for p := range planes {
		d, out := s.diff[p][:w], s.ring[y%3][p][:w]
		if w == 1 {
			out[0] = 3 * d[0]
			continue
		}
		out[0], out[w-1] = 2*d[0]+d[1], d[w-2]+2*d[w-1]
		left, right, inner := d[:w-2], d[2:w], out[1:w-1]
		for x, c := range d[1 : w-1] {
			inner[x] = left[x] + c + right[x]
		}
	}
}

// values sets the values of row y of an image in every plane: its pixels composited over mid grey, in whole levels,
// which the images are subtracted in.
func values(img image.Image, y, w int, out [planes][]uint8) {
	set := func(x int, r, g, b, a uint32) {
		out[0][x] = over(luminance(r, g, b), a)
		out[1][x], out[2][x], out[3][x] = over(r, a), over(g, a), over(b, a)
	}
	switch img := img.(type) {
	case *image.NRGBA:
		row := img.Pix[y*img.Stride : y*img.Stride+4*w]
		for x := 0; x < w; x++ {
			set(x, uint32(row[4*x]), uint32(row[4*x+1]), uint32(row[4*x+2]), uint32(row[4*x+3]))
		}
	case *image.Gray:
		row := img.Pix[y*img.Stride : y*img.Stride+w]
		for p := range planes {
			copy(out[p], row)
		}
	default:
		bounds := img.Bounds()
		for x := 0; x < w; x++ {
			c := color.NRGBAModel.Convert(img.At(bounds.Min.X+x, bounds.Min.Y+y)).(color.NRGBA)
			set(x, uint32(c.R), uint32(c.G), uint32(c.B), uint32(c.A))
		}
	}
}

// planeLimits returns how much the sum of 3×3 pixels may change in each plane, and how many of the planes are compared.
func planeLimits(o Options) ([planes]int, int) {
	limits := [planes]int{9 * o.Threshold, 9 * o.ColorThreshold, 9 * o.ColorThreshold, 9 * o.ColorThreshold}
	if o.ColorThreshold <= 0 {
		return limits, 1
	}
	return limits, planes
}

// changedPixels marks the pixels whose 3×3 average changed by more than the thresholds, and counts them, unless Stop
// ends it.
func changedPixels(a, b image.Image, w, h int, o Options) ([]bool, int, bool) {
	limits, checked := planeLimits(o)
	s := newRowDiffs(a, b, w)
	s.load(0)
	mask := make([]bool, w*h)
	changed := 0
	for y := 0; y < h; y++ {
		if o.Stop != nil && y%64 == 0 && o.Stop() {
			return nil, 0, true
		}
		if y+1 < h {
			s.load(y + 1)
		}
		up, mid, down := max(0, y-1)%3, y%3, min(h-1, y+1)%3
		row := mask[y*w : (y+1)*w]
		for p := 0; p < checked; p++ {
			d0, d1, d2, limit := s.ring[up][p][:w], s.ring[mid][p][:w], s.ring[down][p][:w], limits[p]
			for x := range row {
				if row[x] {
					continue
				}
				if d := int(d0[x]) + int(d1[x]) + int(d2[x]); d > limit || -d > limit {
					row[x] = true
					changed++
				}
			}
		}
	}
	return mask, changed, false
}

// group joins changed pixels at most gap pixels apart, and returns the box of each group's changed pixels, top to
// bottom. The pixels are joined through runs of the mask widened by gap/2 on every side, rounded up.
func group(mask []bool, w, h, gap int) []image.Rectangle {
	r := (gap + 1) / 2
	wide := dilate(mask, w, h, r)
	// Runs of the widened mask, row by row, joined by a union-find with the runs they touch in the row above.
	type run struct{ y, x0, x1 int }
	var runs []run
	parent := []int{}
	find := func(i int) int {
		for parent[i] != i {
			parent[i] = parent[parent[i]]
			i = parent[i]
		}
		return i
	}
	prevStart := 0
	for y := 0; y < h; y++ {
		start := len(runs)
		// The runs of the row above, from p on, end right of the runs of this row found so far.
		p := prevStart
		for x := 0; x < w; {
			if !wide[y*w+x] {
				x++
				continue
			}
			x0 := x
			for x < w && wide[y*w+x] {
				x++
			}
			id := len(runs)
			runs = append(runs, run{y, x0, x})
			parent = append(parent, id)
			for p < start && runs[p].x1 <= x0 {
				p++
			}
			for q := p; q < start && runs[q].x0 < x; q++ {
				if a, b := find(q), find(id); a != b {
					parent[b] = a
				}
			}
		}
		prevStart = start
	}
	boxes := map[int]*image.Rectangle{}
	var order []int
	for i, rn := range runs {
		for x := rn.x0; x < rn.x1; x++ {
			if !mask[rn.y*w+x] {
				continue
			}
			root := find(i)
			box := boxes[root]
			if box == nil {
				box = &image.Rectangle{Min: image.Pt(x, rn.y), Max: image.Pt(x+1, rn.y+1)}
				boxes[root] = box
				order = append(order, root)
			}
			*box = box.Union(image.Rect(x, rn.y, x+1, rn.y+1))
		}
	}
	out := make([]image.Rectangle, 0, len(order))
	for _, root := range order {
		out = append(out, *boxes[root])
	}
	return out
}

// dilate widens the mask by r pixels in every direction.
func dilate(mask []bool, w, h, r int) []bool {
	if r == 0 {
		return mask
	}
	// widen sets out[i] when in has a pixel within r of it along a line of n pixels, step apart, from first.
	widen := func(in, out []bool, first, n, step int, count []int) {
		for k := 0; k < n; k++ {
			count[k+1] = count[k]
			if in[first+k*step] {
				count[k+1]++
			}
		}
		for k := 0; k < n; k++ {
			out[first+k*step] = count[min(n, k+r+1)] > count[max(0, k-r)]
		}
	}
	across, out := make([]bool, w*h), make([]bool, w*h)
	count := make([]int, max(w, h)+1)
	for y := 0; y < h; y++ {
		widen(mask, across, y*w, w, 1, count)
	}
	for x := 0; x < w; x++ {
		widen(across, out, x, h, w, count)
	}
	return out
}
