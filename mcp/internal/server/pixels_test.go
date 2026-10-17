package server

import (
	"bytes"
	"fmt"
	"image"
	"image/color"
	"image/png"
	"os"
	"path/filepath"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// noise is an image whose pixels all differ from their neighbours, with transparent ones among them.
func noise(w, h int) *image.NRGBA {
	img := image.NewNRGBA(image.Rect(0, 0, w, h))
	for i := range img.Pix {
		img.Pix[i] = uint8(i*7919 + i/3)
	}
	return img
}

func samePixels(t *testing.T, got, want image.Image) {
	t.Helper()
	if got.Bounds().Size() != want.Bounds().Size() {
		t.Fatalf("size %v, want %v", got.Bounds().Size(), want.Bounds().Size())
	}
	g, w := got.Bounds().Min, want.Bounds().Min
	for y := range want.Bounds().Dy() {
		for x := range want.Bounds().Dx() {
			a := color.NRGBAModel.Convert(got.At(g.X+x, g.Y+y))
			b := color.NRGBAModel.Convert(want.At(w.X+x, w.Y+y))
			if a != b {
				t.Fatalf("pixel %d,%d is %v, want %v", x, y, a, b)
			}
		}
	}
}

// The PNG that the agent gets has the pixels as they are, also of a zoom cut from a larger image.
func TestEncodePNG(t *testing.T) {
	img := noise(37, 21)
	for name, src := range map[string]image.Image{"whole": img, "cut": img.SubImage(image.Rect(5, 3, 30, 20)), "gray": image.NewGray(image.Rect(0, 0, 9, 4))} {
		data, err := encodePNG(src)
		if err != nil {
			t.Fatal(err)
		}
		decoded, err := png.Decode(bytes.NewReader(data))
		if err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		samePixels(t, decoded, src)
	}
}

// A detail image's pixels come back the same from memory, from their spill file, and from the shot's PNG.
func TestDetailPixels(t *testing.T) {
	dir := t.TempDir()
	spills := newSpiller()
	img := noise(64, 40)
	d := &detail{Width: 64, Height: 40}
	d.keepPixels(img, filepath.Join(dir, "d.z"))
	if got, err := d.image(); err != nil || got != image.Image(img) {
		t.Fatalf("in memory: %v", err)
	}
	d.release(spills)
	spills.wait()
	if d.inMemory() {
		t.Error("still in memory")
	}
	got, err := d.image()
	if err != nil {
		t.Fatal(err)
	}
	samePixels(t, got, img)
	d.drop()
	if _, err := os.Stat(filepath.Join(dir, "d.z")); !os.IsNotExist(err) {
		t.Errorf("spill file kept: %v", err)
	}
	if _, err := d.image(); err != errDetailGone {
		t.Errorf("after drop: %v", err)
	}

	// A detail that is the shot's image goes back to its PNG, without a spill file.
	data, _ := encodePNG(img)
	path := filepath.Join(dir, "s1.png")
	os.WriteFile(path, data, 0o600)
	d = &detail{Path: path, Width: 64, Height: 40}
	d.keepPixels(img, filepath.Join(dir, "s1.z"))
	d.release(spills)
	spills.wait()
	if got, err = d.image(); err != nil {
		t.Fatal(err)
	}
	samePixels(t, got, img)
	if _, err := os.Stat(filepath.Join(dir, "s1.z")); !os.IsNotExist(err) {
		t.Errorf("spill file of a PNG: %v", err)
	}

	// Pixels that cannot be written are gone, and so is a detail whose shot went while they were written.
	d = &detail{Width: 64, Height: 40}
	d.keepPixels(img, filepath.Join(dir, "missing", "d.z"))
	d.release(spills)
	spills.wait()
	if _, err := d.image(); err != errDetailGone {
		t.Errorf("unwritable: %v", err)
	}
	d = &detail{Width: 64, Height: 40}
	d.keepPixels(img, filepath.Join(dir, "late.z"))
	d.release(spills)
	d.drop()
	spills.wait()
	if _, err := os.Stat(filepath.Join(dir, "late.z")); !os.IsNotExist(err) {
		t.Errorf("spill file of a dropped detail: %v", err)
	}
}

// The store keeps the pixels of the latest detail image of each window in memory, of at most residentDetails windows,
// and the others in files, which go with their shots.
func TestShotStoreKeepsFewDetailsInMemory(t *testing.T) {
	dir := t.TempDir()
	st := newShotStore()
	add := func(window string) *shot {
		id := st.newID()
		d := &detail{Width: 8, Height: 8}
		d.keepPixels(noise(8, 8), filepath.Join(dir, id+".z"))
		s := &shot{ID: id, Window: window, StableID: window, Detail: d}
		st.add(s)
		return s
	}
	first := add("0xa1")
	second := add("0xa1")
	st.wait()
	if first.Detail.inMemory() || !second.Detail.inMemory() {
		t.Errorf("in memory: older %v, latest %v", first.Detail.inMemory(), second.Detail.inMemory())
	}
	if _, err := first.Detail.image(); err != nil {
		t.Errorf("older: %v", err)
	}
	var latest []*shot
	for i := range residentDetails {
		latest = append(latest, add(fmt.Sprintf("0xb%d", i)))
	}
	st.wait()
	if second.Detail.inMemory() {
		t.Error("the window whose latest is oldest keeps it in memory")
	}
	for _, s := range latest {
		if !s.Detail.inMemory() {
			t.Errorf("%s left memory", s.ID)
		}
	}
	st.dropWindow("0xa1")
	if files, _ := filepath.Glob(filepath.Join(dir, "s[12].z")); len(files) != 0 {
		t.Errorf("files of dropped shots: %q", files)
	}
}

// ReadRaw reads the pixels that the plugin writes, and refuses a file of another size.
func TestReadRaw(t *testing.T) {
	img := noise(5, 3)
	path := filepath.Join(t.TempDir(), "f.raw")
	os.WriteFile(path, img.Pix, 0o600)
	got, err := hypr.ReadRaw(path, 5, 3)
	if err != nil {
		t.Fatal(err)
	}
	samePixels(t, got, img)
	for _, size := range [][2]int{{5, 4}, {4, 3}, {0, 3}} {
		if _, err := hypr.ReadRaw(path, size[0], size[1]); err == nil {
			t.Errorf("%v: no error", size)
		}
	}
}
