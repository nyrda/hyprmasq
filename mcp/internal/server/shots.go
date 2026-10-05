package server

import (
	"container/list"
	"fmt"
	"math"
	"os"
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
	Path     string
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

const shotsPerWindow = 64

// shotStore keeps the most recently used shots of every window and deletes the files of the ones it drops.
type shotStore struct {
	mu       sync.Mutex
	next     int
	byID     map[string]*list.Element
	byWindow map[string]*list.List
}

func newShotStore() *shotStore {
	return &shotStore{byID: map[string]*list.Element{}, byWindow: map[string]*list.List{}}
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
	st.mu.Lock()
	defer st.mu.Unlock()
	l := st.byWindow[s.Window]
	if l == nil {
		l = list.New()
		st.byWindow[s.Window] = l
	}
	st.byID[s.ID] = l.PushFront(s)
	for l.Len() > shotsPerWindow {
		st.removeLocked(l.Back())
	}
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
	os.Remove(s.Path)
}
