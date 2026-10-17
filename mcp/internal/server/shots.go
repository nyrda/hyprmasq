package server

import (
	"container/list"
	"fmt"
	"image"
	"math"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"

	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
)

// A shot is a screenshot the server remembers, so coordinates in its pixels can be mapped to the window.
type shot struct {
	ID       string
	Window   string
	StableID string
	// The window's logical size at capture.
	Size hypr.Size
	// The captured box in window-local logical pixels. It starts at negative coordinates when a popup reaches above
	// or left of the window.
	Frame  hypr.Rect
	Width  int
	Height int
	// Image pixels per logical pixel.
	Scale float64
	// The window's commit counter when it was rendered, and the id of the socket it counts for, or "" from a plugin
	// that names none.
	Commits  int64
	SocketID string
	// The window had not caught up with its size when it was rendered, so the image shows an older frame of another
	// size, which act takes no coordinates from.
	Resizing bool
	// Set when the window was starved and did not draw before the capture, so the image may show an older frame.
	Starved *starvation
	// The image, or "" for a changes shot, whose zooms show what it has.
	Path string
	// For a zoom, the shot of the whole window that it was read from, or from whose zoom.
	Parent string
	// For a changes shot, the shot it was compared with, whose pixels it has.
	Base string
	// How many changes shots in a row lead to this one: 0 for a full shot.
	Deltas int
	// What the next changes shot is compared with, for a shot of the whole window, or nil.
	Detail *detail
	// The image that act compares the window with before it clicks or drags at the shot's coordinates, when it has
	// none in Detail: a zoom's own image, the detail image that a zoom of a changes shot was cut from, or the overview of
	// a shot without a detail image. See reference.
	Ref *detail
	// The animations that the window drew during the wait before the shot, in window-local logical pixels, whose changes
	// do not count as the window moving its content.
	Animated []hypr.IntRect
}

// A starvation tells why the shot of a starved window may show an older frame: the window did not draw within WaitedMs
// of the frame callbacks that the capture's wait sent it, or, when it is 0, the wait did not run.
type starvation struct {
	WaitedMs int
}

// A detail is an image of the window as a shot of it saw it, in enough detail to find what changed since: a few
// pixels of text, which shrinking for an overview would blur away. It is the shot's own image when that was not shrunk.
// Its pixels are in memory while it is among the latest (see residentDetails), and otherwise in a file.
type detail struct {
	// The PNG of the image, when it has one: the shot's own image, or "".
	Path   string
	Width  int
	Height int
	Scale  float64
	Frame  hypr.Rect
	// The max of the screenshot request, which the next one repeats to get an image at the same scale.
	Max int
	// The region of the screenshot request, in window-local logical pixels, or nil for the whole window.
	Region *hypr.IntRect

	mu sync.Mutex
	// The pixels, while they are in memory.
	img *image.NRGBA
	// The file that the pixels are written to when they leave memory without a PNG, and the file once they are.
	spillPath, spilled string
	// Set once the pixels leave memory, and once they are gone for good.
	leaving, gone bool
}

func (d *detail) geometry() detailGeometry {
	return detailGeometry{frame: d.Frame, scale: d.Scale}
}

// sameGeometry tells whether two detail images show the same box at the same scale, pixel for pixel.
func (d *detail) sameGeometry(o *detail) bool {
	near := func(a, b float64) bool { return math.Abs(a-b) < 1e-6 }
	return d.Width == o.Width && d.Height == o.Height && near(d.Scale, o.Scale) && near(d.Frame.X, o.Frame.X) && near(d.Frame.Y, o.Frame.Y) &&
		near(d.Frame.Width, o.Frame.Width) && near(d.Frame.Height, o.Frame.Height)
}

// reference is the image of the window as the shot shows it, which act compares a new capture with before it clicks or
// drags at the shot's coordinates, or nil.
func (s *shot) reference() *detail {
	if s.Detail != nil {
		return s.Detail
	}
	return s.Ref
}

// toLogical maps a point in shot pixels to window-local logical pixels.
func (s *shot) toLogical(x, y float64) (float64, float64) {
	return s.Frame.X + x/s.Scale, s.Frame.Y + y/s.Scale
}

// inputPoint maps a point in shot pixels to the window-local logical pixels that hyprmasq-input gets.
func (s *shot) inputPoint(x, y float64) (float64, float64) {
	lx, ly := s.toLogical(x, y)
	return roundLogical(lx), roundLogical(ly)
}

// roundLogical rounds to the thousandths that hyprmasq-input gets, without a negative zero.
func roundLogical(v float64) float64 {
	if r := math.Round(v*1000) / 1000; r != 0 {
		return r
	}
	return 0
}

// reachable tells whether pointer input can reach a window-local point. libei cannot express pointer regions at
// negative coordinates, so the plugin's start at the window's top left corner, and the part of a popup above or left of
// the window shows in shots but cannot be clicked.
func reachable(x, y float64) bool {
	return x >= 0 && y >= 0
}

func (s *shot) contains(x, y float64) bool {
	return x >= 0 && y >= 0 && x < float64(s.Width) && y < float64(s.Height)
}

// logicalRegion maps a rectangle in shot pixels to the whole logical pixels that cover it.
func (s *shot) logicalRegion(x, y, width, height float64) hypr.IntRect {
	x0, y0 := s.toLogical(x, y)
	x1, y1 := s.toLogical(x+width, y+height)
	left, top := math.Floor(x0+1e-9), math.Floor(y0+1e-9)
	right, bottom := math.Ceil(x1-1e-9), math.Ceil(y1-1e-9)
	return hypr.IntRect{X: int(left), Y: int(top), Width: max(1, int(right-left)), Height: max(1, int(bottom-top))}
}

// pixelBox maps a box in window-local logical pixels to the whole shot pixels that cover it, cut to the shot. False
// when no part of it is in the shot.
func (s *shot) pixelBox(r hypr.IntRect) (boxOut, bool) {
	x0 := max(0, math.Floor((float64(r.X)-s.Frame.X)*s.Scale+1e-6))
	y0 := max(0, math.Floor((float64(r.Y)-s.Frame.Y)*s.Scale+1e-6))
	x1 := min(float64(s.Width), math.Ceil((float64(r.X+r.Width)-s.Frame.X)*s.Scale-1e-6))
	y1 := min(float64(s.Height), math.Ceil((float64(r.Y+r.Height)-s.Frame.Y)*s.Scale-1e-6))
	if x1 <= x0 || y1 <= y0 {
		return boxOut{}, false
	}
	return boxOut{X: int(x0), Y: int(y0), Width: int(x1 - x0), Height: int(y1 - y0)}, true
}

const shotsPerWindow = 64

// shotStore keeps the most recently used shots of every window and deletes the files of the ones it drops.
type shotStore struct {
	mu       sync.Mutex
	next     int
	byID     map[string]*list.Element
	byWindow map[string]*list.List
	// The last shot of the whole window that a result gave the session, by window.
	last map[string]string
	// The detail images whose pixels are in memory, oldest first, and the writes of the others to their files.
	resident []residentDetail
	spills   *spiller
}

type residentDetail struct {
	window string
	d      *detail
}

func newShotStore() *shotStore {
	return &shotStore{byID: map[string]*list.Element{}, byWindow: map[string]*list.List{}, last: map[string]string{}, spills: newSpiller()}
}

// returned records a shot that a result gives the session. A zoom shows part of a shot the session has, which stays
// the last one of the window.
func (st *shotStore) returned(s *shot) {
	if s == nil || s.Parent != "" {
		return
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	if _, ok := st.byID[s.ID]; ok {
		st.last[s.Window] = s.ID
	}
}

// lastOf returns the last shot of the whole window that a result gave the session, if the store still has it and it is
// of the window with the stable_id, and marks it as used.
func (st *shotStore) lastOf(window, stableID string) *shot {
	st.mu.Lock()
	id := st.last[window]
	st.mu.Unlock()
	if s := st.get(id); s != nil && s.StableID == stableID {
		return s
	}
	return nil
}

func (st *shotStore) newID() string {
	st.mu.Lock()
	defer st.mu.Unlock()
	st.next++
	return fmt.Sprintf("s%d", st.next)
}

// mark returns the number of the latest shot ID.
func (st *shotStore) mark() int {
	st.mu.Lock()
	defer st.mu.Unlock()
	return st.next
}

// number returns the number of a shot ID such as s12.
func number(id string) int {
	n, _ := strconv.Atoi(strings.TrimPrefix(id, "s"))
	return n
}

// dropClosed forgets the shots of windows that are not in a window list requested after the shot numbered mark got its
// ID. Their captures had found the window, so it has closed since. Later shots may be of windows newer than the list.
func (st *shotStore) dropClosed(windows []hypr.Window, mark int) {
	open := map[string]string{}
	for _, w := range windows {
		open[w.Address] = w.StableID
	}
	st.mu.Lock()
	defer st.mu.Unlock()
	for address, l := range st.byWindow {
		for e := l.Front(); e != nil; {
			next := e.Next()
			if s := e.Value.(*shot); number(s.ID) <= mark {
				if stableID, ok := open[address]; !ok || stableID != s.StableID {
					st.removeLocked(e)
				}
			}
			e = next
		}
	}
}

func (st *shotStore) add(s *shot) {
	// The writes of the pixels that leave memory start once the store is unlocked, since they may wait for others.
	var leaving []*detail
	defer func() {
		for _, d := range leaving {
			d.release(st.spills)
		}
	}()
	st.mu.Lock()
	defer st.mu.Unlock()
	l := st.byWindow[s.Window]
	if l == nil {
		l = list.New()
		st.byWindow[s.Window] = l
	}
	st.byID[s.ID] = l.PushFront(s)
	if s.Detail != nil && s.Detail.inMemory() {
		// The window's older detail images leave memory, and then those of the windows whose latest is oldest.
		st.resident = slices.DeleteFunc(st.resident, func(r residentDetail) bool {
			if r.window == s.Window {
				leaving = append(leaving, r.d)
			}
			return r.window == s.Window
		})
		st.resident = append(st.resident, residentDetail{s.Window, s.Detail})
		for len(st.resident) > residentDetails {
			leaving = append(leaving, st.resident[0].d)
			st.resident = st.resident[1:]
		}
	}
	for l.Len() > shotsPerWindow {
		st.removeLocked(l.Back())
	}
}

// wait waits for the detail images that are being written to their files.
func (st *shotStore) wait() {
	st.spills.wait()
}

// get returns the shot and marks it as used.
func (st *shotStore) get(id string) *shot {
	st.mu.Lock()
	defer st.mu.Unlock()
	e := st.byID[id]
	if e == nil {
		return nil
	}
	s := e.Value.(*shot)
	st.byWindow[s.Window].MoveToFront(e)
	return s
}

// remove forgets a shot and deletes its files.
func (st *shotStore) remove(id string) {
	st.mu.Lock()
	defer st.mu.Unlock()
	if e := st.byID[id]; e != nil {
		st.removeLocked(e)
	}
}

// dropWindow forgets every shot of a window that closed.
func (st *shotStore) dropWindow(window string) {
	st.mu.Lock()
	defer st.mu.Unlock()
	if l := st.byWindow[window]; l != nil {
		for l.Len() > 0 {
			st.removeLocked(l.Back())
		}
	}
}

func (st *shotStore) removeLocked(e *list.Element) {
	s := e.Value.(*shot)
	l := st.byWindow[s.Window]
	l.Remove(e)
	if l.Len() == 0 {
		delete(st.byWindow, s.Window)
	}
	delete(st.byID, s.ID)
	if st.last[s.Window] == s.ID {
		delete(st.last, s.Window)
	}
	if s.Detail != nil {
		st.resident = slices.DeleteFunc(st.resident, func(r residentDetail) bool { return r.d == s.Detail })
		s.Detail.drop()
	}
	s.removeFiles()
}

// removeFiles deletes the shot's image and the PNG of its detail image.
func (s *shot) removeFiles() {
	if s.Path != "" {
		os.Remove(s.Path)
	}
	if s.Detail != nil && s.Detail.Path != "" && s.Detail.Path != s.Path {
		os.Remove(s.Detail.Path)
	}
}
