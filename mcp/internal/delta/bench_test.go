package delta

import (
	"encoding/json"
	"fmt"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"math/rand"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// serverOptions are those of the server at scale 1, for images of w×h pixels.
func serverOptions(w, h int) Options {
	return Options{Threshold: 24, ColorThreshold: 64, Group: 8, Thin: 4, Speck: 5, MaxChanged: int(0.35 * float64(w*h))}
}

// page returns a w×h window with a light page of text on it, the lines starting at line and shifted by shift pixels.
func page(w, h, line, shift int) *image.NRGBA {
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	fill(img, img.Rect, color.NRGBA{0xf4, 0xf4, 0xf4, 0xff})
	fill(img, image.Rect(0, 0, w, 40), color.NRGBA{0x30, 0x34, 0x3a, 0xff})
	fill(img, image.Rect(0, 40, w/6, h), color.NRGBA{0xe0, 0xe3, 0xe8, 0xff})
	text(img, image.Rect(w/6+20, 60, w-20, h-20), line, shift, color.NRGBA{0x20, 0x20, 0x20, 0xff})
	return img
}

// text draws lines of glyph-like strokes into r, starting at line, the same for the same line and shift.
func text(img *image.NRGBA, r image.Rectangle, line, shift int, c color.NRGBA) {
	for y := r.Min.Y; y+16 <= r.Max.Y; y += 24 {
		rng := rand.New(rand.NewSource(int64(line)))
		line++
		for x := r.Min.X + shift; x+8 <= r.Max.X; x += 9 {
			if rng.Intn(6) == 0 {
				continue
			}
			for i := 0; i < 3; i++ {
				x0, y0 := x+rng.Intn(7), y+rng.Intn(14)
				fill(img, image.Rect(x0, y0, x0+1+rng.Intn(2), y0+2+rng.Intn(10)), c)
			}
		}
	}
}

// A pair of images to compare.
type pair struct {
	name string
	a, b *image.NRGBA
}

// pairs are windows before and after what an input changes, at each size: nothing; a typed character; a menu; a new
// page.
func pairs() []pair {
	var out []pair
	for _, size := range []image.Point{{1920, 1080}, {2560, 1440}, {3840, 2160}} {
		w, h := size.X, size.Y
		a := page(w, h, 0, 0)
		small, medium := page(w, h, 0, 0), page(w, h, 0, 0)
		fill(small, image.Rect(w/2, h/2, w/2+2, h/2+14), color.NRGBA{0x20, 0x20, 0x20, 0xff})
		menu := image.Rect(w/3, 40, w/3+300, 440)
		fill(medium, menu, color.NRGBA{0xff, 0xff, 0xff, 0xff})
		text(medium, menu.Inset(12), 1000, 0, color.NRGBA{0x20, 0x20, 0x20, 0xff})
		for _, p := range []struct {
			kind string
			b    *image.NRGBA
		}{{"none", page(w, h, 0, 0)}, {"small", small}, {"medium", medium}, {"large", page(w, h, 500, 3)}} {
			out = append(out, pair{fmt.Sprintf("%dx%d/%s", w, h, p.kind), a, p.b})
		}
	}
	return out
}

var sink Result

func BenchmarkCompare(b *testing.B) {
	for _, p := range pairs() {
		o := serverOptions(p.a.Rect.Dx(), p.a.Rect.Dy())
		b.Run(p.name, func(b *testing.B) {
			b.ReportAllocs()
			for b.Loop() {
				sink = Compare(p.a, p.b, o)
			}
		})
	}
}

// BenchmarkReference times the comparison as it was, for the same pairs.
func BenchmarkReference(b *testing.B) {
	for _, p := range pairs() {
		o := serverOptions(p.a.Rect.Dx(), p.a.Rect.Dy())
		b.Run(p.name, func(b *testing.B) {
			b.ReportAllocs()
			for b.Loop() {
				sink = reference(p.a, p.b, o)
			}
		})
	}
}

// corpus returns the pairs of captures in the directory that HYPRMASQ_DELTA_CORPUS names, as listed in its
// manifest.json, [{"name", "a", "b"}], with the PNG files in its pairs directory.
func corpus(tb testing.TB) []pair {
	dir := os.Getenv("HYPRMASQ_DELTA_CORPUS")
	if dir == "" {
		tb.Skip("HYPRMASQ_DELTA_CORPUS is not set")
	}
	data, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		tb.Fatal(err)
	}
	var manifest []struct{ Name, A, B string }
	if err := json.Unmarshal(data, &manifest); err != nil {
		tb.Fatal(err)
	}
	load := func(name string) *image.NRGBA {
		f, err := os.Open(filepath.Join(dir, "pairs", name))
		if err != nil {
			tb.Fatal(err)
		}
		defer f.Close()
		img, err := png.Decode(f)
		if err != nil {
			tb.Fatal(err)
		}
		out := image.NewNRGBA(img.Bounds())
		draw.Draw(out, out.Rect, img, img.Bounds().Min, draw.Src)
		return out
	}
	var out []pair
	for _, m := range manifest {
		out = append(out, pair{m.Name, load(m.A), load(m.B)})
	}
	return out
}

// Real captures compare as before, with the server's limit of changed pixels and without one, as for animations.
func TestCorpusComparesAsTheReferenceDoes(t *testing.T) {
	pairs := corpus(t)
	t.Logf("%d pairs", len(pairs))
	for _, p := range pairs {
		o := serverOptions(p.a.Rect.Dx(), p.a.Rect.Dy())
		for _, limit := range []int{o.MaxChanged, 0} {
			o.MaxChanged = limit
			if got, want := Compare(p.a, p.b, o), reference(p.a, p.b, o); !reflect.DeepEqual(got, want) {
				t.Errorf("%s, limit %d: got %+v, want %+v", p.name, limit, got, want)
			}
		}
	}
}

func BenchmarkCorpus(b *testing.B) {
	for _, p := range corpus(b) {
		o := serverOptions(p.a.Rect.Dx(), p.a.Rect.Dy())
		for _, c := range []struct {
			name    string
			compare func(a, b image.Image, o Options) Result
		}{{"compare", Compare}, {"reference", reference}} {
			b.Run(p.name+"/"+c.name, func(b *testing.B) {
				b.ReportAllocs()
				for b.Loop() {
					sink = c.compare(p.a, p.b, o)
				}
			})
		}
	}
}
