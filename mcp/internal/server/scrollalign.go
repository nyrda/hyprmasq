package server

import (
	"image"
	"math"
)

const (
	scrollStripPx       = 16
	scrollAmbiguousRows = 8
	scrollAmbiguity     = 3
)

type grayImage struct {
	w, h int
	pix  []float32
}

func toGray(img image.Image) grayImage {
	b := img.Bounds()
	g := grayImage{w: b.Dx(), h: b.Dy(), pix: make([]float32, b.Dx()*b.Dy())}
	if n, ok := img.(*image.NRGBA); ok {
		for y := 0; y < g.h; y++ {
			row := n.Pix[y*n.Stride:]
			for x := 0; x < g.w; x++ {
				p := row[4*x:]
				g.pix[y*g.w+x] = float32(54*int(p[0])+183*int(p[1])+19*int(p[2])) / 256
			}
		}
		return g
	}
	for y := 0; y < g.h; y++ {
		for x := 0; x < g.w; x++ {
			r, gr, bl, _ := img.At(b.Min.X+x, b.Min.Y+y).RGBA()
			g.pix[y*g.w+x] = float32(54*(r>>8)+183*(gr>>8)+19*(bl>>8)) / 256
		}
	}
	return g
}

// viewAlignment is what alignViews found.
type viewAlignment struct {
	shift int
	sure  bool
	// The rows that scrolled.
	top, bottom int
	// Nothing changed but a strip of scrollStripPx at the right edge, as an overlay scrollbar that fades.
	unchanged bool
}

// alignViews is scrollShift, and tells too whether nothing changed at all. It gives up, unsure,
// once stop says so.
func alignViews(a, b grayImage, stop func() bool) viewAlignment {
	if a.w != b.w || a.h != b.h || a.h < 16 {
		return viewAlignment{}
	}
	h, w := b.h, b.w
	same := make([]bool, h)
	changed := make([]bool, w)
	kept := 0
	for y := 0; y < h; y++ {
		ra, rb := a.pix[y*w:(y+1)*w], b.pix[y*w:(y+1)*w]
		same[y] = true
		for x := range rb {
			if d := rb[x] - ra[x]; d > 16 || d < -16 {
				same[y], changed[x] = false, true
			}
		}
		if same[y] {
			kept++
		}
	}
	// The columns that changed somewhere, which alone tell the shift: a narrow column of text, as a list of names on a
	// wide page that is blank otherwise, would hide between the columns of a fixed step. About 480 of them are compared
	// in a coarse pass, and every other one in a fine one.
	var cols []int
	for x, c := range changed {
		// An overlay scrollbar at the right edge moves its own way, not with the content.
		if c && x < w-scrollStripPx {
			cols = append(cols, x)
		}
	}
	coarseStep := max(1, len(cols)/480)
	// Nothing changed, or only a strip at the right edge, where an overlay scrollbar fades: a control that changed, as a
	// digit of a spin box, is narrow too, and is a change.
	if len(cols) == 0 {
		return viewAlignment{sure: true, bottom: h, unchanged: true}
	}
	if kept*100 > 95*h {
		return viewAlignment{sure: true, bottom: h}
	}
	top, bottom := 0, h
	for top < h && same[top] {
		top++
	}
	for bottom > top && same[bottom-1] {
		bottom--
	}
	// Content beside the columns that changed, which stayed where it was in the rows that scrolled, is a pane of its
	// own, as a list beside a fixed panel: the part that scrolled is then no view of the whole window. A page that is
	// blank beside a narrow column of text has nothing there.
	// A fixed pane has content that varies down its columns. A thin vertical
	// frame or scrollbar edge is nearly constant along its height. Ignore
	// columns whose variation occupies at most 5% of the band, including the
	// few corner pixels where a frame meets a horizontal edge.
	textured := make([]bool, w)
	for x := 1; x < w-scrollStripPx; x++ {
		if changed[x] {
			continue
		}
		var levels [16]int
		n := 0
		for y := top; y < bottom; y += 2 {
			levels[min(15, max(0, int(b.pix[y*w+x])/16))]++
			n++
		}
		mode := 0
		for i := range levels {
			if levels[i] > levels[mode] {
				mode = i
			}
		}
		different := 0
		for y := top; y < bottom; y += 2 {
			if d := b.pix[y*w+x] - float32(mode*16+8); d > 24 || d < -24 {
				different++
			}
		}
		textured[x] = different*20 > n
	}
	stayed, beside := 0, 0
	for y := top; y < bottom; y += 2 {
		rb := b.pix[y*w : (y+1)*w]
		for x := 1; x < w-scrollStripPx; x++ {
			if changed[x] || changed[x-1] {
				continue
			}
			beside++
			if !textured[x] && !textured[x-1] {
				continue
			}
			if d := rb[x] - rb[x-1]; d > 16 || d < -16 {
				stayed++
			}
		}
	}
	if beside > 0 && stayed*200 > beside {
		return viewAlignment{top: top, bottom: bottom}
	}
	minRows := max(h/7, 16)
	// The search stops, unsure, once stop says so, which it asks every polls residuals.
	const polls = 32
	calls := 0
	stopped := false
	residual := func(o, rowStep, colStep int) float64 {
		if calls++; calls%polls == 0 && stop() {
			stopped = true
		}
		if stopped {
			return math.Inf(1)
		}
		lo, hi := max(top, top-o), min(bottom, bottom-o)
		if hi-lo < minRows {
			return math.Inf(1)
		}
		var sum float64
		n := 0
		for y := lo; y < hi; y += rowStep {
			ra, rb := a.pix[(y+o)*w:], b.pix[y*w:]
			for i := 0; i < len(cols); i += colStep {
				d := float64(rb[cols[i]] - ra[cols[i]])
				sum += d * d
				n++
			}
		}
		return sum / float64(n)
	}
	// Coarse, on every shift, as sharp text matches at one shift alone, with a few rows and columns, then in full around
	// the best six.
	type cand struct {
		o int
		r float64
	}
	var coarse []cand
	for o := -(bottom - top - minRows); o <= bottom-top-minRows && !stopped; o++ {
		if r := residual(o, 8, coarseStep); !math.IsInf(r, 1) {
			coarse = append(coarse, cand{o, r})
		}
	}
	if len(coarse) == 0 || stopped {
		return viewAlignment{top: top, bottom: bottom}
	}
	for i := 0; i < 6 && i < len(coarse); i++ {
		for j := i + 1; j < len(coarse); j++ {
			if coarse[j].r < coarse[i].r {
				coarse[i], coarse[j] = coarse[j], coarse[i]
			}
		}
	}
	best, bestR := 0, math.Inf(1)
	for i := 0; i < 6 && i < len(coarse); i++ {
		for o := coarse[i].o - 3; o <= coarse[i].o+3; o++ {
			if r := residual(o, 2, max(1, coarseStep/2)); r < bestR {
				best, bestR = o, r
			}
		}
	}
	// The best of the shifts farther away, found among all coarse ones and refined as finely as the best one: content
	// that repeats, as a grid's lines, matches as well a whole number of its periods off.
	var far []cand
	for _, c := range coarse {
		if d := c.o - best; d > scrollAmbiguousRows || d < -scrollAmbiguousRows {
			far = append(far, c)
		}
	}
	for i := 0; i < 6 && i < len(far); i++ {
		for j := i + 1; j < len(far); j++ {
			if far[j].r < far[i].r {
				far[i], far[j] = far[j], far[i]
			}
		}
	}
	otherR := math.Inf(1)
	for i := 0; i < 6 && i < len(far); i++ {
		for o := far[i].o - 3; o <= far[i].o+3; o++ {
			if d := o - best; d > scrollAmbiguousRows || d < -scrollAmbiguousRows {
				otherR = min(otherR, residual(o, 2, max(1, coarseStep/2)))
			}
		}
	}
	if stopped {
		return viewAlignment{top: top, bottom: bottom}
	}
	return viewAlignment{shift: best, sure: otherR >= scrollAmbiguity*math.Max(bestR, 0.5), top: top, bottom: bottom}
}
