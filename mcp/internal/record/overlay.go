package record

import (
	"math"
	"time"
)

// The overlays of a step's frames, in the window's logical pixels. Under r.mu.

// overlayAll draws all of the step's input at once, as slides show it before the result.
func (st *Step) overlayAll() overlay {
	ov := overlay{caption: st.rec.caption, pointer: st.lastPointer()}
	for _, m := range st.marks {
		switch m.Kind {
		case "click":
			ov.ripples = append(ov.ripples, ripple{at: m.Points[0], progress: -1, label: clickLabel(m)})
		case "path":
			if m.Button != "none" {
				ov.paths = append(ov.paths, m.Points)
			}
			if m.Label != "" {
				ov.chips = append(ov.chips, m.Label+" drag")
			}
		case "scroll", "keys", "type":
			ov.chips = append(ov.chips, m.Label)
		}
	}
	return ov
}

// overlayResult draws the pointer where the input left it, over the result.
func (st *Step) overlayResult() overlay {
	return overlay{caption: st.rec.caption, pointer: st.lastPointer()}
}

// lastPointer is where the step's input leaves the pointer, or where it was before, or nil when it is not known.
func (st *Step) lastPointer() *[2]float64 {
	for i := len(st.marks) - 1; i >= 0; i-- {
		if pts := st.marks[i].Points; len(pts) > 0 {
			p := pts[len(pts)-1]
			return &p
		}
	}
	if p, ok := st.rec.pointer[st.window]; ok {
		return &p
	}
	return nil
}

// overlayAt draws the input as it is at t, for video: the actions that began by then, a ripple and a chip for a moment
// after each, and a path as far as the pointer has gone along it.
func (st *Step) overlayAt(t time.Time) overlay {
	ov := overlay{caption: st.rec.caption}
	if p, ok := st.rec.pointer[st.window]; ok {
		ov.pointer = &p
	}
	at := func(p [2]float64) { ov.pointer = &p }
	for _, m := range st.marks {
		if m.Kind == "" || m.At.IsZero() || t.Before(m.At) {
			continue
		}
		elapsed := t.Sub(m.At)
		switch m.Kind {
		case "click":
			at(m.Points[0])
			if elapsed < rippleTime {
				ov.ripples = append(ov.ripples, ripple{at: m.Points[0], progress: float64(elapsed) / float64(rippleTime), label: clickLabel(m)})
			}
		case "move":
			at(m.Points[0])
		case "scroll":
			at(m.Points[0])
			if elapsed < chipTime {
				ov.chips = append(ov.chips, m.Label)
			}
		case "path":
			frac := 1.0
			if m.Duration > 0 {
				frac = math.Min(1, float64(elapsed)/float64(m.Duration))
			}
			done := along(m.Points, frac)
			at(done[len(done)-1])
			if m.Button != "none" && elapsed < m.Duration+rippleTime {
				ov.paths = append(ov.paths, done)
			}
			if m.Label != "" && elapsed < m.Duration+chipTime {
				ov.chips = append(ov.chips, m.Label+" drag")
			}
		case "keys", "type":
			if elapsed < max(chipTime, m.Duration+chipTime/3) {
				ov.chips = append(ov.chips, m.Label)
			}
		}
	}
	return ov
}

// along is the part of the line through the points up to the fraction of its length.
func along(pts [][2]float64, frac float64) [][2]float64 {
	total := 0.0
	for i := 1; i < len(pts); i++ {
		total += math.Hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1])
	}
	if frac >= 1 || total == 0 {
		return pts
	}
	left := total * frac
	out := [][2]float64{pts[0]}
	for i := 1; i < len(pts); i++ {
		a, b := pts[i-1], pts[i]
		d := math.Hypot(b[0]-a[0], b[1]-a[1])
		if d >= left {
			if d > 0 {
				k := left / d
				out = append(out, [2]float64{a[0] + (b[0]-a[0])*k, a[1] + (b[1]-a[1])*k})
			}
			return out
		}
		left -= d
		out = append(out, b)
	}
	return out
}
