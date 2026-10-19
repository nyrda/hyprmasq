package server

import (
	"context"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"strconv"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

func TestShotCoordinates(t *testing.T) {
	// A popup reaches 20 logical pixels left of and 10 above the window, at scale 2.
	s := &shot{Frame: hypr.Rect{X: -20, Y: -10, Width: 340, Height: 210}, Scale: 2, Width: 680, Height: 420}
	if x, y := s.toLogical(0, 0); x != -20 || y != -10 {
		t.Errorf("0,0 maps to %v,%v", x, y)
	}
	if x, y := s.toLogical(50, 30); x != 5 || y != 5 {
		t.Errorf("50,30 maps to %v,%v", x, y)
	}
	if got := s.logicalRegion(40, 20, 100, 50); got != (hypr.IntRect{X: 0, Y: 0, Width: 50, Height: 25}) {
		t.Errorf("region %+v", got)
	}
	if got := s.logicalRegion(0, 0, 10, 10); got != (hypr.IntRect{X: -20, Y: -10, Width: 5, Height: 5}) {
		t.Errorf("negative region %+v", got)
	}
	// At scale 1.25 the plugin widens regions to whole device pixels, so frames are fractional.
	z := &shot{Frame: hypr.Rect{X: 0.8, Y: 0.8, Width: 10.4, Height: 10.4}, Scale: 1.25, Width: 13, Height: 13}
	if got := z.logicalRegion(1, 1, 5, 5); got != (hypr.IntRect{X: 1, Y: 1, Width: 5, Height: 5}) {
		t.Errorf("fractional region %+v", got)
	}
	if !s.contains(0, 0) || !s.contains(679.5, 419) || s.contains(680, 0) || s.contains(0, 420) || s.contains(-1, 0) {
		t.Error("contains is wrong at the edges")
	}
	// Input gets thousandths of a logical pixel, so a point just left of the window rounds into it.
	third := &shot{Frame: hypr.Rect{X: -1.0 / 3, Width: 100, Height: 100}, Scale: 3, Width: 300, Height: 300}
	if x, y := third.inputPoint(2, 2); x != 0.333 || y != 0.667 {
		t.Errorf("2,2 is sent as %v,%v", x, y)
	}
	if x, _ := third.inputPoint(0.9999, 0); x != 0 || math.Signbit(x) {
		t.Errorf("0.9999 is sent as %v", x)
	}
}

func TestShotStoreEvictsLeastRecentlyUsed(t *testing.T) {
	dir := t.TempDir()
	st := newShotStore()
	add := func(window string) *shot {
		s := &shot{ID: st.newID(), Window: window, Path: dir + "/" + strconv.Itoa(st.next) + ".png"}
		os.WriteFile(s.Path, nil, 0o600)
		st.add(s)
		return s
	}
	first := add("0xa1")
	second := add("0xa1")
	add("0xb2")
	for range shotsPerWindow - 2 {
		add("0xa1")
	}
	st.get(first.ID)
	add("0xa1")
	if st.get(first.ID) == nil {
		t.Error("a recently used shot was dropped")
	}
	if st.get(second.ID) != nil {
		t.Error("the least recently used shot was kept")
	}
	if _, err := os.Stat(second.Path); !os.IsNotExist(err) {
		t.Error("the dropped shot's file was kept")
	}
	if st.get("s3") == nil {
		t.Error("another window's shot was dropped")
	}
}

// Beyond shotBytes, the store drops the least recently used shots of any window, but not the last one of a window that
// a result gave, nor the keptShots most recently used. A call with a dropped shot says why it is gone.
func TestShotStoreBoundsTheFilesOfAllWindows(t *testing.T) {
	dir := t.TempDir()
	st := newShotStore()
	add := func(window string, size int64) *shot {
		s := &shot{ID: st.newID(), Window: window, StableID: window, Path: filepath.Join(dir, strconv.Itoa(st.next)+".png")}
		// Sparse, so that the test writes nothing.
		os.WriteFile(s.Path, nil, 0o600)
		os.Truncate(s.Path, size)
		st.add(s)
		return s
	}
	// Without marking it as used.
	kept := func(s *shot) bool {
		_, ok := st.byID[s.ID]
		return ok
	}
	third := int64(shotBytes / 3)
	last := add("0xa1", third)
	st.returned(last)
	older := add("0xb2", third)
	used := add("0xb2", third)
	newer := add("0xc3", third)
	st.get(used.ID)
	for range keptShots - 4 {
		add("0xc3", 0)
	}
	for _, s := range []*shot{last, older, used, newer} {
		if !kept(s) {
			t.Errorf("%s was dropped while the newest shots took them all", s.ID)
		}
	}
	st.get(used.ID)
	add("0xc3", 0)
	add("0xc3", 0)
	if kept(older) {
		t.Error("the least recently used shot was kept")
	}
	if _, err := os.Stat(older.Path); !os.IsNotExist(err) {
		t.Errorf("file of the dropped shot: %v", err)
	}
	if !kept(last) || !kept(used) || !kept(newer) {
		t.Error("dropped more than needed")
	}
	_, err := (&Server{shots: st}).checkShot(older.ID, "0xb2", hypr.State{StableID: "0xb2"}, false)
	if te := asToolError(err); te.Code != "stale_shot" || te.Message != fmt.Sprintf("there is no shot %s any more; this server drops the least recently used shots once their images take 256 MB", older.ID) {
		t.Errorf("error %v", err)
	}
	// The window's last shot stays, also when it is the least recently used.
	for range keptShots + 1 {
		add("0xd4", third)
	}
	if !kept(last) {
		t.Error("the last shot of a window was dropped")
	}
	// Once a newer shot of the window is returned, the one before can go.
	st.returned(add("0xa1", 0))
	if kept(last) {
		t.Error("the shot that was the last of its window was kept")
	}
}

// A spill file that is written after the shot that let its pixels go counts at once.
func TestShotStoreCountsSpillsWhenWritten(t *testing.T) {
	dir := t.TempDir()
	st := newShotStore()
	old := &shot{ID: st.newID(), Window: "0xa1", Path: filepath.Join(dir, "old.png")}
	os.WriteFile(old.Path, nil, 0o600)
	os.Truncate(old.Path, shotBytes-10)
	st.add(old)
	withDetail := func() {
		id := st.newID()
		d := &detail{Width: 64, Height: 64}
		d.keepPixels(noise(64, 64), filepath.Join(dir, id+".z"))
		st.add(&shot{ID: id, Window: "0xb2", Detail: d})
	}
	withDetail()
	for range keptShots - 1 {
		st.add(&shot{ID: st.newID(), Window: "0xc3"})
	}
	// The first detail image leaves memory, and its file takes more than the 10 bytes left.
	withDetail()
	st.wait()
	st.mu.Lock()
	defer st.mu.Unlock()
	if _, ok := st.byID[old.ID]; ok {
		t.Error("the least recently used shot was kept")
	}
}

// The store remembers the windows of the latest droppedShots shots it dropped, and stops being asked about older ones.
func TestShotStoreForgetsOldDroppedShots(t *testing.T) {
	st := newShotStore()
	for range shotsPerWindow + droppedShots + 1 {
		st.add(&shot{ID: st.newID(), Window: "0xa1", StableID: "1a"})
	}
	if len(st.dropped) != droppedShots || len(st.droppedIDs) != droppedShots {
		t.Errorf("remembers %d dropped shots, %d IDs", len(st.dropped), len(st.droppedIDs))
	}
	if st.stableIDOf("s1", "0xa1") != "" {
		t.Error("remembers the oldest dropped shot")
	}
	if got := st.stableIDOf("s2", "0xa1"); got != "1a" {
		t.Errorf("stable_id of a dropped shot it remembers: %q", got)
	}
	if got := st.stableIDOf(fmt.Sprintf("s%d", st.next), "0xa1"); got != "1a" {
		t.Errorf("stable_id of a kept shot: %q", got)
	}
	st.dropWindow("0xa1")
	if c, ok := st.droppedOf(fmt.Sprintf("s%d", st.next)); !ok || !c.closed {
		t.Errorf("closed shot: %v %v", c, ok)
	}
}

func TestShotsOfWindowsThatClosedElsewhere(t *testing.T) {
	e := newTestEnv(t)
	e.window("0xa1", "1a", 320, 200)
	e.window("0xb2", "2b", 320, 200)
	e.window("0xc3", "3c", 320, 200)
	e.screenshot(`{"window":"0xa1"}`)
	e.screenshot(`{"window":"0xb2"}`)
	e.screenshot(`{"window":"0xc3"}`)
	// One window closes, and another one reuses the address of a second one.
	e.fake.removeWindow("0xa1")
	e.fake.removeWindow("0xb2")
	e.window("0xb2", "4d", 320, 200)
	if res := e.server.windowsTool(context.Background(), &call{}); res.IsError {
		t.Fatal(text(res))
	}
	for id, kept := range map[string]bool{"s1": false, "s2": false, "s3": true} {
		if (e.server.shots.get(id) != nil) != kept {
			t.Errorf("%s kept: %v", id, !kept)
		}
	}
	if files, _ := os.ReadDir(e.server.shotDir); len(files) != 1 {
		t.Errorf("files %v", files)
	}

	// A list requested before a shot got its ID says nothing about its window.
	st := newShotStore()
	st.add(&shot{ID: st.newID(), Window: "0xa1", StableID: "1a"})
	mark := st.mark()
	st.add(&shot{ID: st.newID(), Window: "0xe5", StableID: "5e"})
	st.dropClosed(nil, mark)
	if st.get("s1") != nil || st.get("s2") == nil {
		t.Error("dropped the wrong shots")
	}
}
