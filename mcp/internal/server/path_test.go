package server

import (
	"math"
	"testing"
)

// A spline path starts and ends exactly at its first and last points and passes through every point in order, so the
// stroke reaches what the agent aimed at. It stays near the curve that the points describe: on evenly spaced points of
// a circle near the circle, and next to a short segment, which makes a uniform Catmull-Rom spline overshoot or loop,
// close to the box of the points. Its samples lie close enough together that hyprmasq-input takes about as many steps
// for them, after their rounding to thousandths, as for a straight path as long as the curve.
func TestSampleSpline(t *testing.T) {
	arc := func(r float64, degrees ...float64) [][2]float64 {
		var out [][2]float64
		for _, d := range degrees {
			a := d * math.Pi / 180
			out = append(out, [2]float64{500 + r*math.Cos(a), 500 - r*math.Sin(a)})
		}
		return out
	}
	for _, c := range []struct {
		name   string
		points [][2]float64
		// The farthest that a sample may lie outside the box of the points.
		over float64
		// The farthest that a sample between the second and the second to last point may lie from the circle of radius r
		// around 500,500, unless r is 0. The first and last segments leave and reach their ends straight towards the
		// point next to them.
		r, off float64
	}{
		{name: "an arc of evenly spaced points", points: arc(200, 0, 30, 60, 90, 120, 150, 180), over: 1, r: 200, off: 1},
		// A uniform spline overshoots by 11 and 35 pixels.
		{name: "a corner after a short segment", points: [][2]float64{{0, 0}, {200, 0}, {205, 5}, {205, 200}}, over: 5},
		{name: "a hairpin", points: [][2]float64{{0, 0}, {300, 0}, {305, 40}, {0, 45}}, over: 15},
		{name: "a rail across a photo", points: [][2]float64{{210, 690}, {212, 518}, {227, 364}, {256, 230}, {300, 120}}},
		{name: "a short tight curve", points: arc(15, 0, 90, 180, 270), over: 3},
		{name: "a zigzag with a repeated point", points: [][2]float64{{0, 0}, {100, 0}, {100, 0}, {0, 50}, {100, 100}}, over: 10},
		{name: "two points", points: [][2]float64{{10, 10}, {400, 300}}},
		{name: "longer than the most steps a path takes", points: [][2]float64{{0, 0}, {8000, 0}, {0, 4000}, {8000, 8000}}, over: 1000},
	} {
		t.Run(c.name, func(t *testing.T) {
			out := sampleSpline(c.points)
			if out[0] != c.points[0] || out[len(out)-1] != c.points[len(c.points)-1] {
				t.Fatalf("from %v to %v, want %v to %v", out[0], out[len(out)-1], c.points[0], c.points[len(c.points)-1])
			}
			box := [4]float64{math.Inf(1), math.Inf(1), math.Inf(-1), math.Inf(-1)}
			for _, p := range c.points {
				box = [4]float64{min(box[0], p[0]), min(box[1], p[1]), max(box[2], p[0]), max(box[3], p[1])}
			}
			next := 0
			for _, p := range out {
				if math.IsNaN(p[0]) || math.IsNaN(p[1]) {
					t.Fatalf("sample %v", p)
				}
				for next < len(c.points) && p == c.points[next] {
					next++
				}
				if over := max(box[0]-p[0], box[1]-p[1], p[0]-box[2], p[1]-box[3]); over > c.over {
					t.Errorf("%v lies %.2f px outside the box of the points", p, over)
				}
				if c.r > 0 && next >= 2 && next <= len(c.points)-2 {
					if off := math.Abs(math.Hypot(p[0]-500, p[1]-500) - c.r); off > c.off {
						t.Errorf("%v lies %.2f px off the circle", p, off)
					}
				}
			}
			if next != len(c.points) {
				t.Errorf("passes through the points up to %v, not %v", c.points[:next], c.points[next])
			}
			rounded := make([][2]float64, len(out))
			for i, p := range out {
				rounded[i] = [2]float64{roundLogical(p[0]), roundLogical(p[1])}
			}
			// As many steps as a straight path as long as the curve, at least dragMinSteps and at most dragMaxSteps, and up to a tenth
			// more for the samples' margin and one more for each segment, whose end a step need not reach exactly.
			length := polylineLength(out)
			least := min(max(dragMinSteps, int(length/dragStepPx)), dragMaxSteps)
			if moves := pathMoves(rounded); moves < least || moves > least*11/10+len(c.points) {
				t.Errorf("%d steps for %.0f px, want %d to %d", moves, length, least, least*11/10+len(c.points))
			}
		})
	}
}
