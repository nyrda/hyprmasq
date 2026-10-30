// Package record turns the frames and the input of act calls into a video, such as a demo for a pull request: slides,
// a fixed time for the input and for the result of each act, or video, the frames of each act at real speed without the
// time between acts.
package record

import (
	"context"
	"errors"
	"fmt"
	"image"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

type Mode string

const (
	Slides Mode = "slides"
	Video  Mode = "video"
)

// Options are the user's settings of recordings.
type Options struct {
	// The directory of the videos. A leading ~ and environment variables are expanded when a recording starts.
	Dir string
	// Also write a GIF of the steps next to the video, at most GIFWidth pixels across.
	GIF      bool
	GIFWidth int
	// The most frames per second of video mode.
	FPS      int
	MaxSteps int
	// The most recorded time, of the video itself.
	MaxTime time.Duration
	// The ffmpeg binary, a name on PATH or a path.
	FFmpeg string
}

// Rect is a box in window-local logical pixels.
type Rect struct{ X, Y, Width, Height float64 }

// A Frame is an image of a window: its pixels cover Region, in window-local logical pixels, at Scale image pixels per
// logical pixel.
type Frame struct {
	Image  *image.NRGBA
	Region Rect
	Scale  float64
	At     time.Time
}

// A Mark is an input action as the recording draws it, in window-local logical pixels.
type Mark struct {
	// click, move, path, scroll, keys or type. Other actions have none, which draws nothing.
	Kind string
	// One point for click, move and scroll, all of them for path.
	Points [][2]float64
	// click and path. A path with "none" only moves.
	Button string
	Count  int
	// The text of the chip for keys, type and scroll, and the modifiers of a click or path.
	Label string
	// When the action's input began and how long it lasted, for video, or zero while not known.
	At       time.Time
	Duration time.Duration
}

// How long slides show the input of a step, its result, and a recording's first frame, and how long video holds the
// result of a step. The last frame stays for finalHold more.
const (
	slideInput  = time.Second
	slideResult = 1500 * time.Millisecond
	// Reading time for a caption that a step brings, per character past captionFree, up to captionMax in all.
	captionFree    = 30
	captionPerChar = 40 * time.Millisecond
	captionMax     = 4 * time.Second
	videoResult    = time.Second
	finalHold      = time.Second
	// The frame rate of slides, which only needs to place their times.
	slidesRate = 4
	// The long side of the canvas at most, which bigger frames are shrunk to.
	maxCanvas = 1920
)

// Recording is a recording in progress. Its methods are safe to call from several goroutines.
type Recording struct {
	mode Mode
	opts Options
	// The video once finished, the file that ffmpeg writes until then, and the GIF.
	path, part, gifPath string
	ffmpeg              string

	mu      sync.Mutex
	canvas  *canvas
	enc     *encoder
	rate    int
	frames  int
	steps   int
	caption string
	// The caption changed with the step that is being recorded.
	newCaption bool
	// Where the pointer was last, for each window, in its logical pixels.
	pointer map[string][2]float64
	// The last frame written, which finishing holds a little longer.
	last *image.RGBA
	gif  *gifFrames
	err  error
	done bool
}

// Start begins a recording: it checks that ffmpeg runs and creates the directory, and names the files. ffmpeg starts
// with the first frame, which sets the size of the video.
func Start(mode Mode, opts Options, now time.Time) (*Recording, error) {
	ffmpeg, err := exec.LookPath(opts.FFmpeg)
	if err != nil {
		return nil, fmt.Errorf("recording needs ffmpeg, and %q cannot be run: %w", opts.FFmpeg, unwrapExec(err))
	}
	dir, err := ExpandDir(opts.Dir, os.Getenv)
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("cannot create the directory for recordings: %w", err)
	}
	// The part file is created here, which reserves the name also against the servers of other sessions that record
	// into the directory.
	stamp := now.Format("20060102-150405")
	name := stamp
	for n := 2; ; n++ {
		if !exists(filepath.Join(dir, name+".mp4")) && !exists(filepath.Join(dir, name+".gif")) {
			f, err := os.OpenFile(filepath.Join(dir, name+".part.mp4"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
			if err == nil {
				f.Close()
				break
			}
			if !errors.Is(err, os.ErrExist) {
				return nil, fmt.Errorf("cannot create the recording: %w", err)
			}
		}
		name = fmt.Sprintf("%s-%d", stamp, n)
	}
	r := &Recording{mode: mode, opts: opts, ffmpeg: ffmpeg, pointer: map[string][2]float64{},
		path: filepath.Join(dir, name+".mp4"), part: filepath.Join(dir, name+".part.mp4")}
	r.rate = slidesRate
	if mode == Video {
		r.rate = opts.FPS
	}
	if opts.GIF {
		r.gifPath = filepath.Join(dir, name+".gif")
	}
	return r, nil
}

func unwrapExec(err error) error {
	var ee *exec.Error
	if errors.As(err, &ee) {
		return ee.Err
	}
	return err
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// ExpandDir expands a leading ~ and environment variables in a directory of recordings, which must then be absolute.
func ExpandDir(dir string, getenv func(string) string) (string, error) {
	if dir == "~" || strings.HasPrefix(dir, "~/") {
		home := getenv("HOME")
		if !filepath.IsAbs(home) {
			return "", fmt.Errorf("the directory for recordings, %q, starts with ~, and HOME is not an absolute path", dir)
		}
		dir = home + dir[1:]
	}
	expanded := os.Expand(dir, getenv)
	if !filepath.IsAbs(expanded) {
		return "", fmt.Errorf("the directory for recordings must be an absolute path, not %q", expanded)
	}
	return filepath.Clean(expanded), nil
}

// Path is the video's file once the recording is finished.
func (r *Recording) Path() string { return r.path }

func (r *Recording) Mode() Mode { return r.mode }

// SetCaption shows text from the next step on, until another caption. "" removes it.
func (r *Recording) SetCaption(text string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if text != r.caption {
		r.caption, r.newCaption = text, true
	}
}

// Err is the failure that stopped the recording, or nil.
func (r *Recording) Err() error {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.err
}

// Limit tells why the recording reached its limit, or "" while it has not.
func (r *Recording) Limit() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	switch {
	case r.steps >= r.opts.MaxSteps && r.opts.MaxSteps == 1:
		return "its limit of 1 step"
	case r.steps >= r.opts.MaxSteps:
		return fmt.Sprintf("its limit of %d steps", r.opts.MaxSteps)
	case r.opts.MaxTime > 0 && r.recorded() >= r.opts.MaxTime:
		return fmt.Sprintf("its limit of %s of recorded time", FormatDuration(r.opts.MaxTime))
	}
	return ""
}

// recorded is the length of the video so far. Under r.mu.
func (r *Recording) recorded() time.Duration {
	return time.Duration(r.frames) * time.Second / time.Duration(r.rate)
}

// A Step is the recording of one act: the frame before its input, with the input drawn on it, the frames during the act
// in video mode, and the frame of its result.
type Step struct {
	rec    *Recording
	window string
	marks  []Mark
	before *Frame
	// Video: the frame that is shown until the next one, when the step's first frame came, and how many frames the step
	// has written since.
	current *Frame
	t0      time.Time
	written int
}

// Begin starts a step of the window. before, which may be nil, is the window before the act's input, and marks its
// input, by the index of the action. The first step of a recording starts ffmpeg, whose failure is returned.
func (r *Recording) Begin(window string, before *Frame, marks []Mark) (*Step, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err != nil {
		return nil, r.err
	}
	if r.done {
		return nil, errors.New("the recording has finished")
	}
	st := &Step{rec: r, window: window, marks: marks, before: before}
	if before != nil {
		if err := r.ensureCanvas(before); err != nil {
			return nil, r.fail(err)
		}
		if r.mode == Video {
			st.push(before)
		}
	}
	return st, nil
}

// fail stops the recording with err, and returns it. Under r.mu.
func (r *Recording) fail(err error) error {
	if r.err == nil {
		r.err = err
		if r.enc != nil {
			r.enc.kill()
		}
	}
	return r.err
}

// ensureCanvas sizes the video after its first frame and starts ffmpeg. Under r.mu.
func (r *Recording) ensureCanvas(f *Frame) error {
	if r.canvas != nil {
		return nil
	}
	w, h := f.Image.Rect.Dx(), f.Image.Rect.Dy()
	if long := max(w, h); long > maxCanvas {
		w, h = w*maxCanvas/long, h*maxCanvas/long
	}
	c, err := newCanvas(w, h)
	if err != nil {
		return err
	}
	enc, err := startEncoder(r.ffmpeg, r.part, c.width, c.height, r.rate, r.mode)
	if err != nil {
		return err
	}
	r.canvas, r.enc = c, enc
	if r.gifPath != "" {
		dir, err := os.MkdirTemp(filepath.Dir(r.path), "."+strings.TrimSuffix(filepath.Base(r.path), ".mp4")+"-gif-")
		if err != nil {
			return err
		}
		r.gif = &gifFrames{dir: dir}
	}
	return nil
}

// Timed sets when the input of the action at index began and how long it took.
func (st *Step) Timed(index int, at time.Time, d time.Duration) {
	st.rec.mu.Lock()
	defer st.rec.mu.Unlock()
	if index >= 0 && index < len(st.marks) {
		st.marks[index].At, st.marks[index].Duration = at, d
	}
}

// Sample adds a frame of the window during the act, in video mode.
func (st *Step) Sample(f *Frame) {
	r := st.rec
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err != nil || r.done || r.mode != Video {
		return
	}
	if err := r.ensureCanvas(f); err != nil {
		r.fail(err)
		return
	}
	st.push(f)
}

// push writes the frame that is shown until f came, and makes f the current one. Under r.mu.
func (st *Step) push(f *Frame) {
	if st.current == nil {
		st.current, st.t0 = f, f.At
		return
	}
	st.writeUntil(f.At)
	st.current = f
}

// writeUntil writes the current frame for each frame time of the step before t. Under r.mu.
func (st *Step) writeUntil(t time.Time) {
	r := st.rec
	target := int(t.Sub(st.t0) * time.Duration(r.rate) / time.Second)
	for ; st.written < target && r.err == nil; st.written++ {
		at := st.t0.Add(time.Duration(st.written) * time.Second / time.Duration(r.rate))
		r.write(r.canvas.compose(st.current, st.overlayAt(at)), 1)
	}
}

// End finishes the step with after, the window's frame after the act, or nil when there is none, such as for a window
// that closed. delivered is how many of the act's actions went out, from the first: the others are not drawn. Without
// any, nothing changed, and the step only adds its first frame when it is the recording's first.
func (st *Step) End(after *Frame, delivered int) error {
	r := st.rec
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.err != nil {
		return r.err
	}
	if r.done {
		return nil
	}
	first := r.steps == 0
	refused := delivered <= 0 && hasInput(st.marks)
	if refused {
		st.marks, after = nil, nil
	}
	for i := max(0, delivered); i < len(st.marks); i++ {
		st.marks[i] = Mark{}
	}
	if after != nil {
		if err := r.ensureCanvas(after); err != nil {
			return r.fail(err)
		}
	}
	// Video needs the slides only for the GIF.
	var slides []slide
	if (!refused || first) && (r.mode == Slides || r.gif != nil) {
		slides = st.slides(after, first)
	}
	wrote := false
	if r.mode == Video && (!refused || first) {
		switch {
		case after != nil && st.current == nil:
			st.current, st.t0 = after, after.At
		case after != nil:
			st.writeUntil(after.At)
			st.current = after
		case st.current != nil:
			st.writeUntil(time.Now())
		}
		if st.current != nil {
			r.write(r.canvas.compose(st.current, st.overlayResult()), frames(videoResult, r.rate))
			wrote = true
		}
	} else if r.mode == Slides {
		for _, s := range slides {
			r.write(s.img, frames(s.hold, r.rate))
		}
		wrote = len(slides) > 0
	}
	if r.gif != nil && r.err == nil {
		for _, s := range slides {
			if err := r.gif.add(s.img, s.hold); err != nil {
				return r.fail(err)
			}
		}
	}
	if wrote {
		r.steps++
		r.newCaption = false
		if last := st.lastPointer(); last != nil {
			r.pointer[st.window] = *last
		}
	}
	return r.err
}

// frames is the number of frames that last d at the rate.
func frames(d time.Duration, rate int) int {
	return int(d * time.Duration(rate) / time.Second)
}

type slide struct {
	img  *image.RGBA
	hold time.Duration
}

// slides are the step as slides show it: the window before the input with the input drawn on it, and the result. A
// step without input shows its result, and the first step of a recording shows its first frame also without input.
// Under r.mu.
func (st *Step) slides(after *Frame, first bool) []slide {
	r := st.rec
	if r.canvas == nil {
		return nil
	}
	var out []slide
	input := hasInput(st.marks)
	if st.before != nil && (input || first) {
		hold := slideInput
		if !input {
			hold = slideResult
		}
		out = append(out, slide{r.canvas.compose(st.before, st.overlayAll()), hold})
	}
	if after != nil && (input || !first || st.before == nil) {
		hold := slideResult
		if r.newCaption {
			if n := utf8.RuneCountInString(r.caption) - captionFree; n > 0 {
				hold = min(captionMax, hold+time.Duration(n)*captionPerChar)
			}
		}
		out = append(out, slide{r.canvas.compose(after, st.overlayResult()), hold})
	}
	return out
}

func hasInput(marks []Mark) bool {
	for _, m := range marks {
		if m.Kind != "" {
			return true
		}
	}
	return false
}

// write writes img count times. Under r.mu.
func (r *Recording) write(img *image.RGBA, count int) {
	if r.err != nil || count <= 0 {
		return
	}
	for range count {
		if err := r.enc.write(img.Pix); err != nil {
			r.fail(err)
			return
		}
	}
	r.frames += count
	r.last = img
}

// Files are what a finished recording wrote.
type Files struct {
	Video      string
	VideoBytes int64
	// "" without a GIF.
	GIF      string
	GIFBytes int64
	Duration time.Duration
	Steps    int
}

// Finish ends the recording within ctx: ffmpeg finishes the video, which is then written with its index at the start,
// as players on the web want it, and the GIF is made. A recording that failed keeps what ffmpeg wrote, and the error
// names the file.
func (r *Recording) Finish(ctx context.Context) (Files, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.done {
		return Files{}, errors.New("the recording has already finished")
	}
	r.done = true
	if r.gif != nil {
		defer os.RemoveAll(r.gif.dir)
	}
	files := Files{Video: r.path, Duration: r.recorded(), Steps: r.steps}
	if r.enc == nil {
		os.Remove(r.part)
		if r.err != nil {
			return files, r.err
		}
		return files, errors.New("the recording has no frames: no act was recorded")
	}
	if r.err == nil && r.last != nil {
		r.write(r.last, frames(finalHold, r.rate))
		files.Duration = r.recorded()
	}
	if err := r.enc.close(ctx); err != nil && r.err == nil {
		r.err = err
	}
	if r.err != nil {
		if exists(r.part) {
			return files, fmt.Errorf("%w; what was recorded is in %s", r.err, r.part)
		}
		return files, r.err
	}
	if err := remux(ctx, r.ffmpeg, r.part, r.path); err != nil {
		return files, fmt.Errorf("%w; the video is in %s", err, r.part)
	}
	os.Remove(r.part)
	if info, err := os.Stat(r.path); err == nil {
		files.VideoBytes = info.Size()
	}
	if r.gif != nil && len(r.gif.frames) > 0 {
		if err := r.gif.write(ctx, r.ffmpeg, r.gifPath, r.opts.GIFWidth); err != nil {
			return files, fmt.Errorf("the video is saved, the GIF failed: %w", err)
		}
		files.GIF = r.gifPath
		if info, err := os.Stat(r.gifPath); err == nil {
			files.GIFBytes = info.Size()
		}
	}
	return files, nil
}

// Abort ends a recording without finishing its files, and deletes them.
func (r *Recording) Abort() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.done = true
	if r.enc != nil {
		r.enc.kill()
	}
	if r.gif != nil {
		os.RemoveAll(r.gif.dir)
	}
	os.Remove(r.part)
}

// FormatDuration writes a duration as m:ss.
func FormatDuration(d time.Duration) string {
	s := int((d + time.Second/2) / time.Second)
	return fmt.Sprintf("%d:%02d", s/60, s%60)
}
