// Package delta finds what changed between two screenshots of a window: boxes of changed pixels, grouped into changes,
// with tiny ones, such as a blinking caret, told apart.
package delta

import (
	"image"
	"image/color"
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
	bounds := a.Bounds()
	w, h := bounds.Dx(), bounds.Dy()
	if b.Bounds().Dx() != w || b.Bounds().Dy() != h || w == 0 || h == 0 {
		panic("delta: the images differ in size")
	}
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

// rowSums keeps, for the rows around the one being compared, the sum of every 3 pixels across in every plane, with
// the edge pixels repeated beyond the edges.
type rowSums struct {
	img    image.Image
	w      int
	values [planes][]uint8
	// By row modulo 3.
	ring [3][planes][]uint16
}

func newRowSums(img image.Image, w int) *rowSums {
	s := &rowSums{img: img, w: w}
	for p := range planes {
		s.values[p] = make([]uint8, w)
		for r := range s.ring {
			s.ring[r][p] = make([]uint16, w)
		}
	}
	return s
}

// over composites a channel of straight alpha over mid grey.
func over(c, a uint32) uint8 {
	return uint8((c*a + 128*(255-a) + 127) / 255)
}

func (s *rowSums) setPixel(x int, r, g, b, a uint32) {
	// Rec. 709 weights in 1/256.
	s.values[0][x] = over((54*r+183*g+19*b)>>8, a)
	s.values[1][x], s.values[2][x], s.values[3][x] = over(r, a), over(g, a), over(b, a)
}

// load computes the sums of row y.
func (s *rowSums) load(y int) {
	w, bounds := s.w, s.img.Bounds()
	switch img := s.img.(type) {
	case *image.NRGBA:
		row := img.Pix[y*img.Stride : y*img.Stride+4*w]
		for x := 0; x < w; x++ {
			s.setPixel(x, uint32(row[4*x]), uint32(row[4*x+1]), uint32(row[4*x+2]), uint32(row[4*x+3]))
		}
	case *image.Gray:
		row := img.Pix[y*img.Stride : y*img.Stride+w]
		for p := range planes {
			copy(s.values[p], row)
		}
	default:
		for x := 0; x < w; x++ {
			c := color.NRGBAModel.Convert(img.At(bounds.Min.X+x, bounds.Min.Y+y)).(color.NRGBA)
			s.setPixel(x, uint32(c.R), uint32(c.G), uint32(c.B), uint32(c.A))
		}
	}
	for p := range planes {
		v, out := s.values[p], s.ring[y%3][p]
		for x := 0; x < w; x++ {
			out[x] = uint16(v[max(0, x-1)]) + uint16(v[x]) + uint16(v[min(w-1, x+1)])
		}
	}
}

// changedPixels marks the pixels whose 3×3 average changed by more than the thresholds, and counts them, unless Stop
// ends it.
func changedPixels(a, b image.Image, w, h int, o Options) ([]bool, int, bool) {
	limits := [planes]int{9 * o.Threshold, 9 * o.ColorThreshold, 9 * o.ColorThreshold, 9 * o.ColorThreshold}
	checked := planes
	if o.ColorThreshold <= 0 {
		checked = 1
	}
	sa, sb := newRowSums(a, w), newRowSums(b, w)
	sa.load(0)
	sb.load(0)
	mask := make([]bool, w*h)
	changed := 0
	for y := 0; y < h; y++ {
		if o.Stop != nil && y%64 == 0 && o.Stop() {
			return nil, 0, true
		}
		if y+1 < h {
			sa.load(y + 1)
			sb.load(y + 1)
		}
		up, mid, down := max(0, y-1)%3, y%3, min(h-1, y+1)%3
		for p := 0; p < checked; p++ {
			a0, a1, a2 := sa.ring[up][p], sa.ring[mid][p], sa.ring[down][p]
			b0, b1, b2 := sb.ring[up][p], sb.ring[mid][p], sb.ring[down][p]
			limit, row := limits[p], mask[y*w:(y+1)*w]
			for x := 0; x < w; x++ {
				if row[x] {
					continue
				}
				d := int(a0[x]) + int(a1[x]) + int(a2[x]) - int(b0[x]) - int(b1[x]) - int(b2[x])
				if d > limit || -d > limit {
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
