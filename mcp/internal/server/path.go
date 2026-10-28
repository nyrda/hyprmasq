package server

import (
	"math"
)

// A path presses a button once, moves through its points and releases once, so that an app that draws along the
// motion draws one stroke. Its curve is a line, straight from point to point, or a spline, a centripetal Catmull-Rom
// spline through every point, which the server samples into points a little closer together than the sender's steps.
const (
	// The points that a path action gives, which the sender gets as they are for a line.
	maxPathPoints = 500
	// The points that the paths of a batch send, given or sampled, as the sender's MAX_PATH_POINTS. With up to 34 bytes
	// of arguments a point, they take up to about 136 KB, well within Linux's 2 MiB limit for a command line with the
	// default stack. At 10 ms a step they take longer than the largest deadline_ms anyway.
	maxPathMotion = 4000
	// The centripetal parameterization, which does not loop or overshoot much with unevenly spaced points.
	splineAlpha = 0.5
	// Each segment of a spline is measured at this many parameter steps before it is sampled by length.
	splineTable = 32
	// The share of the sender's step that the samples of a spline are apart along it at most, so that the sender moves
	// from one to the next in one step. It measures the path from sample to sample, a little shorter than the curve,
	// and for a path under 210 or over 20000 pixels makes its steps a share of that length, so a little shorter too.
	splineStepShare = 0.95
)

// pathStep is the length of hyprmasq-input's steps along a path of total logical pixels, as its pathStep: dragStepPx,
// shorter for a path that dragMinSteps of them would not cover, and longer for one that would take more than
// dragMaxSteps.
func pathStep(total float64) float64 {
	return max(min(dragStepPx, total/dragMinSteps), total/dragMaxSteps)
}

// segmentMoves is how many steps of step hyprmasq-input takes for a segment of length, as its segmentSteps: at least
// one, so that it reaches every point. A path of one segment takes dragMinSteps to dragMaxSteps.
func segmentMoves(length, step float64) int {
	if length <= 0 {
		return 1
	}
	return int(math.Ceil(length/step - 1e-9))
}

// pathMoves is how many steps hyprmasq-input takes to move through points after the first.
func pathMoves(points [][2]float64) int {
	step, n := pathStep(polylineLength(points)), 0
	for i := 1; i < len(points); i++ {
		n += segmentMoves(distance(points[i-1], points[i]), step)
	}
	return n
}

func distance(a, b [2]float64) float64 {
	return math.Hypot(b[0]-a[0], b[1]-a[1])
}

func polylineLength(points [][2]float64) float64 {
	total := 0.0
	for i := 1; i < len(points); i++ {
		total += distance(points[i-1], points[i])
	}
	return total
}

// pathInput returns the points that hyprmasq-input gets for a path, mapped by toInput from shot pixels: the given
// points of a line, or the samples of a spline.
func pathInput(a action, toInput func(x, y float64) (float64, float64)) [][2]float64 {
	points := make([][2]float64, len(a.Points))
	for i, p := range a.Points {
		points[i][0], points[i][1] = toInput(p[0], p[1])
	}
	if a.Curve != "spline" {
		return points
	}
	return sampleSpline(points)
}

// sampleSpline samples the centripetal Catmull-Rom spline through points into points that start and end at its ends and
// pass through every given point, a little closer along the curve than the steps that hyprmasq-input takes for a path
// as long as the curve, so that it moves from sample to sample in one step each, also after their rounding. A point
// that repeats the one before it is left out, since a segment needs a length, and the ends get phantom neighbours that
// mirror the point next to them, so the curve leaves the first point towards the second and arrives at the last from
// the one before it.
func sampleSpline(points [][2]float64) [][2]float64 {
	var p [][2]float64
	for _, q := range points {
		if len(p) == 0 || q != p[len(p)-1] {
			p = append(p, q)
		}
	}
	switch len(p) {
	case 1:
		// A spline through one point stays there, which the sender takes as a path of two.
		return [][2]float64{p[0], p[0]}
	case 2:
		// Through two points the spline is the line between them.
		return p
	}
	mirror := func(a, b [2]float64) [2]float64 { return [2]float64{2*a[0] - b[0], 2*a[1] - b[1]} }
	ext := append(append([][2]float64{mirror(p[0], p[1])}, p...), mirror(p[len(p)-1], p[len(p)-2]))

	// Each segment as a table of points at even parameter steps and the length along it to each.
	type table struct {
		seg     [4][2]float64
		at      [splineTable + 1][2]float64
		lengths [splineTable + 1]float64
	}
	tables := make([]table, len(p)-1)
	total := 0.0
	for i := range tables {
		t := &tables[i]
		copy(t.seg[:], ext[i:i+4])
		for k := range t.at {
			t.at[k] = catmullRom(t.seg, float64(k)/splineTable)
			if k > 0 {
				t.lengths[k] = t.lengths[k-1] + distance(t.at[k-1], t.at[k])
			}
		}
		total += t.lengths[splineTable]
	}

	// Each segment is cut into pieces of even length, and ends at its point.
	step := pathStep(total) * splineStepShare
	out := [][2]float64{p[0]}
	for i := range tables {
		t := &tables[i]
		length := t.lengths[splineTable]
		n := segmentMoves(length, step)
		k := 0
		for j := 1; j < n; j++ {
			s := length * float64(j) / float64(n)
			for k < splineTable-1 && t.lengths[k+1] < s {
				k++
			}
			u := float64(k)
			if span := t.lengths[k+1] - t.lengths[k]; span > 0 {
				u += (s - t.lengths[k]) / span
			}
			out = append(out, catmullRom(t.seg, u/splineTable))
		}
		out = append(out, p[i+1])
	}
	return out
}

// catmullRom evaluates the segment from seg[1] to seg[2] of a centripetal Catmull-Rom spline at u from 0 to 1, with
// the Barry and Goldman pyramid. Its points differ from their neighbours, so every knot interval is positive.
func catmullRom(seg [4][2]float64, u float64) [2]float64 {
	var knots [4]float64
	for i := 1; i < 4; i++ {
		knots[i] = knots[i-1] + math.Pow(distance(seg[i-1], seg[i]), splineAlpha)
	}
	t := knots[1] + u*(knots[2]-knots[1])
	lerp := func(a, b [2]float64, ta, tb float64) [2]float64 {
		w := (t - ta) / (tb - ta)
		return [2]float64{a[0] + w*(b[0]-a[0]), a[1] + w*(b[1]-a[1])}
	}
	a1 := lerp(seg[0], seg[1], knots[0], knots[1])
	a2 := lerp(seg[1], seg[2], knots[1], knots[2])
	a3 := lerp(seg[2], seg[3], knots[2], knots[3])
	b1 := lerp(a1, a2, knots[0], knots[2])
	b2 := lerp(a2, a3, knots[1], knots[3])
	return lerp(b1, b2, knots[1], knots[2])
}
