package record

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"image"
	"image/png"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// An encoder is the ffmpeg that writes the video while it is recorded, from raw RGBA frames on its stdin. It writes a
// fragmented MP4, which plays up to its last fragment also when ffmpeg never finishes it.
type encoder struct {
	cmd    *exec.Cmd
	stdin  *os.File
	stderr *tail
	// Closed once ffmpeg has exited, with its exit in err.
	exited chan struct{}
	err    error
}

// writeTimeout is how long a frame may wait for ffmpeg to read it. ffmpeg encodes far faster, so one that does not read
// for this long hangs, and is killed, so that no stop or session end waits for it.
const writeTimeout = 5 * time.Second

func startEncoder(ffmpeg, path string, width, height, rate int, mode Mode) (*encoder, error) {
	args := []string{"-hide_banner", "-loglevel", "error", "-nostats",
		"-f", "rawvideo", "-pix_fmt", "rgba", "-s", fmt.Sprintf("%dx%d", width, height), "-framerate", strconv.Itoa(rate), "-i", "pipe:0",
		"-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-pix_fmt", "yuv420p", "-g", strconv.Itoa(2 * rate)}
	if mode == Slides {
		args = append(args, "-tune", "stillimage")
	}
	args = append(args, "-movflags", "+frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "-y", path)
	cmd := exec.Command(ffmpeg, args...)
	// A signal to the server's process group, such as the client's Ctrl+C, leaves ffmpeg to finish the video.
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	// A pipe of its own, whose writes can time out, which those of cmd.StdinPipe cannot.
	frames, stdin, err := os.Pipe()
	if err != nil {
		return nil, err
	}
	cmd.Stdin = frames
	e := &encoder{cmd: cmd, stdin: stdin, stderr: &tail{}, exited: make(chan struct{})}
	cmd.Stderr = e.stderr
	err = cmd.Start()
	frames.Close()
	if err != nil {
		stdin.Close()
		return nil, fmt.Errorf("cannot run ffmpeg: %w", err)
	}
	go func() {
		e.err = cmd.Wait()
		close(e.exited)
	}()
	return e, nil
}

func (e *encoder) write(pix []byte) error {
	e.stdin.SetWriteDeadline(time.Now().Add(writeTimeout))
	if _, err := e.stdin.Write(pix); err != nil {
		if errors.Is(err, os.ErrDeadlineExceeded) {
			e.kill()
			return fmt.Errorf("ffmpeg read no frame for %v, and was stopped", writeTimeout)
		}
		// ffmpeg stopped reading, so it has exited or is about to: its message says why.
		select {
		case <-e.exited:
		case <-time.After(time.Second):
		}
		return e.failure(err)
	}
	return nil
}

func (e *encoder) failure(err error) error {
	if msg := e.stderr.String(); msg != "" {
		return fmt.Errorf("ffmpeg failed: %s", msg)
	}
	return fmt.Errorf("ffmpeg failed: %w", err)
}

// close ends the input, and waits for ffmpeg to finish the video until ctx ends.
func (e *encoder) close(ctx context.Context) error {
	e.stdin.Close()
	select {
	case <-e.exited:
		if e.err != nil {
			return e.failure(e.err)
		}
		return nil
	case <-ctx.Done():
		e.cmd.Process.Kill()
		<-e.exited
		return fmt.Errorf("ffmpeg did not finish the video in time: %w", ctx.Err())
	}
}

func (e *encoder) kill() {
	e.stdin.Close()
	e.cmd.Process.Kill()
}

// tail keeps the last part of what ffmpeg writes on stderr, for a failure's message.
type tail struct {
	mu  sync.Mutex
	buf []byte
}

func (t *tail) Write(p []byte) (int, error) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.buf = append(t.buf, p...)
	if len(t.buf) > 2048 {
		t.buf = t.buf[len(t.buf)-2048:]
	}
	return len(p), nil
}

func (t *tail) String() string {
	t.mu.Lock()
	defer t.mu.Unlock()
	return strings.Join(strings.Fields(string(t.buf)), " ")
}

// run runs ffmpeg with the arguments until ctx ends, and returns its message when it fails.
func run(ctx context.Context, ffmpeg string, args ...string) error {
	cmd := exec.CommandContext(ctx, ffmpeg, append([]string{"-hide_banner", "-loglevel", "error", "-nostats", "-nostdin"}, args...)...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		if ctx.Err() != nil {
			return fmt.Errorf("ffmpeg did not finish in time: %w", ctx.Err())
		}
		if msg := strings.Join(strings.Fields(stderr.String()), " "); msg != "" {
			return fmt.Errorf("ffmpeg failed: %s", msg)
		}
		return fmt.Errorf("ffmpeg failed: %w", err)
	}
	return nil
}

// remux writes the fragmented video again with its index at the start, as players on the web want it, without
// encoding it again.
func remux(ctx context.Context, ffmpeg, part, path string) error {
	if err := run(ctx, ffmpeg, "-i", part, "-c", "copy", "-movflags", "+faststart", "-f", "mp4", "-y", path); err != nil {
		os.Remove(path)
		return err
	}
	return nil
}

// gifFrames are the pictures of a GIF, kept as PNG files until the recording finishes.
type gifFrames struct {
	dir    string
	frames []gifFrame
}

type gifFrame struct {
	file string
	hold time.Duration
}

func (g *gifFrames) add(img *image.RGBA, hold time.Duration) error {
	name := fmt.Sprintf("%05d.png", len(g.frames))
	f, err := os.Create(filepath.Join(g.dir, name))
	if err != nil {
		return err
	}
	enc := png.Encoder{CompressionLevel: png.BestSpeed}
	err = enc.Encode(f, img)
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		return err
	}
	g.frames = append(g.frames, gifFrame{file: name, hold: hold})
	return nil
}

// write makes the GIF from the frames, at most width pixels across, with a palette made for them.
func (g *gifFrames) write(ctx context.Context, ffmpeg, path string, width int) error {
	if len(g.frames) == 0 {
		return errors.New("no frames")
	}
	// Each image is read at 100 frames a second, the GIF's time base, so that its duration is not rounded to the 25 a
	// second of images. The last one's duration only reaches the GIF as final_delay.
	var list strings.Builder
	list.WriteString("ffconcat version 1.0\n")
	for _, f := range g.frames {
		fmt.Fprintf(&list, "file %s\noption framerate 100\nduration %.2f\n", f.file, f.hold.Seconds())
	}
	final := strconv.Itoa(int(g.frames[len(g.frames)-1].hold / (10 * time.Millisecond)))
	listPath := filepath.Join(g.dir, "frames.txt")
	if err := os.WriteFile(listPath, []byte(list.String()), 0o600); err != nil {
		return err
	}
	filter := fmt.Sprintf("scale='min(iw,%d)':-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4", width)
	if err := run(ctx, ffmpeg, "-f", "concat", "-safe", "0", "-i", listPath, "-vf", filter, "-loop", "0", "-final_delay", final, "-f", "gif", "-y", path); err != nil {
		os.Remove(path)
		return err
	}
	return nil
}
