package server

import (
	"context"
	"image"
	"image/color"
	"image/draw"
	"image/gif"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/record"
)

// recordingEnv is a test session whose recordings go to a directory of the test, made with ffmpeg from PATH.
func recordingEnv(t *testing.T, options ...func(*record.Options)) (*testEnv, string) {
	t.Helper()
	for _, tool := range []string{"ffmpeg", "ffprobe"} {
		if _, err := exec.LookPath(tool); err != nil {
			t.Fatalf("the recording tests need %s on PATH, as nix develop has it", tool)
		}
	}
	e := newTestEnv(t)
	dir := filepath.Join(t.TempDir(), "videos")
	e.server.cfg.Record = record.Options{Dir: dir, GIFWidth: 800, FPS: 10, MaxSteps: 500, MaxTime: 10 * time.Minute, FFmpeg: "ffmpeg"}
	for _, option := range options {
		option(&e.server.cfg.Record)
	}
	return e, dir
}

// probe returns a stream property of a video that ffprobe reads, such as width or duration.
func probe(t *testing.T, path, entry string) string {
	t.Helper()
	out, err := exec.Command("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream="+entry, "-of", "default=noprint_wrappers=1:nokey=1", path).Output()
	if err != nil {
		t.Fatalf("ffprobe %s: %v", path, err)
	}
	return strings.TrimSpace(string(out))
}

// videoGray returns the gray level of the video's frame at the time, at a point of it.
func videoGray(t *testing.T, path string, at time.Duration, x, y, width int) uint8 {
	t.Helper()
	out, err := exec.Command("ffmpeg", "-v", "error", "-ss", strconv.FormatFloat(at.Seconds(), 'f', 3, 64), "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-").Output()
	if err != nil {
		t.Fatalf("a frame of %s at %v: %v", path, at, err)
	}
	return out[y*width+x]
}

// gifFrames decodes a GIF into whole frames, each drawn over the one before as a viewer shows them.
func gifFrames(t *testing.T, path string) ([]*image.RGBA, []int) {
	t.Helper()
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	g, err := gif.DecodeAll(f)
	if err != nil {
		t.Fatal(err)
	}
	canvas := image.NewRGBA(image.Rect(0, 0, g.Config.Width, g.Config.Height))
	var frames []*image.RGBA
	for _, img := range g.Image {
		draw.Draw(canvas, img.Bounds(), img, img.Bounds().Min, draw.Over)
		frames = append(frames, image.NewRGBA(canvas.Rect))
		copy(frames[len(frames)-1].Pix, canvas.Pix)
	}
	return frames, g.Delay
}

func isCursorBlue(c color.Color) bool {
	r, g, b, _ := c.RGBA()
	return b>>8 > 170 && g>>8 > 120 && r>>8 < 170
}

// A slides recording follows the acts across windows: the input drawn where it went, the result, and the caption, in a
// video and, when asked, a GIF of the same steps.
func TestRecordingSlides(t *testing.T) {
	e, dir := recordingEnv(t, func(o *record.Options) { o.GIF = true })
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = func(x, y float64) uint8 { return 20 } })
	e.window("0xb2", "2b", 200, 120, func(w *fakeWindow) { w.Paint = func(x, y float64) uint8 { return 200 } })
	quick := `"wait":{"expect_change":false,"quiet_ms":1}`
	e.screenshot(`{"window":"0xa1"}`)

	// The fake renders at scale 2, so shot pixel 200,100 is logical 100,50, which is pixel 200,100 of the recording.
	res := e.act(`{"shot":"s1","actions":[[200,100]],"record":"slides","caption":"Click in A",` + quick + `}`)
	started := regexp.MustCompile(`recording slides to (` + regexp.QuoteMeta(dir) + `/\d{8}-\d{6}\.mp4); an act with record "stop" saves it`)
	m := started.FindStringSubmatch(text(res))
	if res.IsError || m == nil || get(t, res, "recording") == nil {
		t.Fatalf("%s", text(res))
	}
	video := m[1]
	if res := e.act(`{"window":"0xb2","actions":[["key","ctrl+s"]],` + quick + `}`); res.IsError || get(t, res, "recording") != nil {
		t.Fatalf("%s", text(res))
	}
	res = e.act(`{"window":"0xb2","record":"stop"}`)
	gifPath := strings.TrimSuffix(video, ".mp4") + ".gif"
	saved := "recording saved: " + video + ` \(0:06, 2 steps, \d+ kB\), GIF ` + regexp.QuoteMeta(gifPath) + ` \(\d+ kB\)`
	if res.IsError || !regexp.MustCompile(saved).MatchString(text(res)) {
		t.Fatalf("%s", text(res))
	}
	entries, _ := os.ReadDir(dir)
	var names []string
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	if want := []string{filepath.Base(gifPath), filepath.Base(video)}; !slices.Equal(names, want) {
		t.Errorf("the directory has %v, not %v", names, want)
	}

	// The window of 640×400 pixels with the bar of 40 below it, for 1 s of input and 1.5 s of result of each act, and
	// a second more of the last frame.
	if size := probe(t, video, "width") + "×" + probe(t, video, "height"); size != "640×440" {
		t.Errorf("the video is %s", size)
	}
	if d, _ := strconv.ParseFloat(probe(t, video, "duration"), 64); d < 5.9 || d > 6.1 {
		t.Errorf("the video lasts %v s", d)
	}
	if a, b := videoGray(t, video, 1500*time.Millisecond, 320, 200, 640), videoGray(t, video, 4000*time.Millisecond, 320, 200, 640); a > 40 || b < 180 {
		t.Errorf("the video's middle is %d in the first act's result and %d in the second's", a, b)
	}

	frames, delays := gifFrames(t, gifPath)
	if len(frames) != 4 || !slices.Equal(delays, []int{100, 150, 100, 150}) {
		t.Fatalf("the GIF has %d frames, delays %v", len(frames), delays)
	}
	// A ring around the click, left of the pointer, in the frame before the result.
	if c := frames[0].At(200-11, 100); !isCursorBlue(c) {
		t.Errorf("the ring around the click is %v", c)
	}
	if c := frames[1].At(200-11, 100); isCursorBlue(c) {
		t.Errorf("the result has the ring: %v", c)
	}
	// The second window, smaller, is centred.
	if g := color.GrayModel.Convert(frames[3].At(320, 200)).(color.Gray).Y; g < 180 {
		t.Errorf("the second window's middle is %d", g)
	}
	if g := color.GrayModel.Convert(frames[3].At(40, 30)).(color.Gray).Y; g > 40 {
		t.Errorf("the backdrop beside it is %d", g)
	}
	// The caption, in light text in the bar, stays until another one.
	for i, f := range frames {
		light := false
		for x := 10; x < 200 && !light; x++ {
			for y := 405; y < 435 && !light; y++ {
				light = color.GrayModel.Convert(f.At(x, y)).(color.Gray).Y > 200
			}
		}
		if !light {
			t.Errorf("frame %d has no caption", i)
		}
	}
}

// What does not fit the session's recording is refused before anything is sent.
func TestRecordingRefusals(t *testing.T) {
	e, dir := recordingEnv(t)
	e.window("0xa1", "1a", 320, 200)
	key := `"actions":[["key","a"]]`
	for _, c := range []struct{ args, code, message string }{
		{`{"window":"0xa1",` + key + `,"caption":"Open the file"}`, "invalid_arguments", `caption needs a recording: start one with record "slides" or "video"`},
		{`{"window":"0xa1",` + key + `,"record":"stop"}`, "invalid_arguments", `record "stop": no recording is running`},
		{`{"window":"0xa1",` + key + `,"record":"film"}`, "invalid_arguments", `record must be slides, video or stop, not "film"`},
		{`{"window":"0xa1",` + key + `,"caption":"` + strings.Repeat("x", 121) + `"}`, "invalid_arguments", "caption has 121 characters, at most 120"},
	} {
		res := e.act(c.args)
		if code := errorCode(t, res); code != c.code || !strings.Contains(text(res), c.message) {
			t.Errorf("%s: code %q: %s", c.args, code, text(res))
		}
	}
	e.server.cfg.Record.FFmpeg = "/nonexistent/ffmpeg"
	res := e.act(`{"window":"0xa1",` + key + `,"record":"slides"}`)
	if code := errorCode(t, res); code != "recording_failed" || !strings.Contains(text(res), `recording needs ffmpeg, and "/nonexistent/ffmpeg" cannot be run`) {
		t.Errorf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil {
		t.Fatalf("sent input: %v", e.inputs())
	}
	// A failed start leaves no recording behind.
	if code := errorCode(t, e.act(`{"window":"0xa1","record":"stop"}`)); code != "invalid_arguments" {
		t.Errorf("a stop after the failed start: %q", code)
	}

	e.server.cfg.Record.FFmpeg = "ffmpeg"
	if res := e.act(`{"window":"0xa1","record":"video"}`); res.IsError {
		t.Fatal(text(res))
	}
	res = e.act(`{"window":"0xa1",` + key + `,"record":"slides"}`)
	if code := errorCode(t, res); code != "invalid_arguments" || !strings.Contains(text(res), `a recording is running; stop it with record "stop" first`) {
		t.Errorf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil {
		t.Fatalf("sent input: %v", e.inputs())
	}
	// The running recording goes on.
	if res := e.act(`{"window":"0xa1","record":"stop"}`); !strings.Contains(text(res), "recording saved: "+dir) {
		t.Errorf("%s", text(res))
	}
}

// An act that only starts or stops a recording captures at once, where an act without actions waits for a change.
func TestRecordOnlyCapturesAtOnce(t *testing.T) {
	e, _ := recordingEnv(t)
	e.window("0xa1", "1a", 320, 200)
	waitOf := func(args string) string {
		t.Helper()
		before := len(e.fake.requestsMatching("j/hyprmasq wait"))
		if res := e.act(args); res.IsError {
			t.Fatalf("%s: %s", args, text(res))
		}
		waits := e.fake.requestsMatching("j/hyprmasq wait")
		if len(waits) != before+1 {
			t.Fatalf("%s: waits %v", args, waits[before:])
		}
		return waits[before]
	}
	for _, args := range []string{`{"window":"0xa1","record":"slides"}`, `{"window":"0xa1","record":"stop"}`} {
		if wait := waitOf(args); strings.Contains(wait, " expect ") {
			t.Errorf("%s: %s", args, wait)
		}
	}
	if wait := waitOf(`{"window":"0xa1"}`); !strings.Contains(wait, " expect ") {
		t.Errorf("an act without actions: %s", wait)
	}
}

// A recording that reaches its limit stops and saves itself, and says so.
func TestRecordingLimit(t *testing.T) {
	e, dir := recordingEnv(t, func(o *record.Options) { o.MaxSteps = 1 })
	e.window("0xa1", "1a", 320, 200)
	res := e.act(`{"window":"0xa1","actions":[["key","a"]],"record":"slides","wait":{"expect_change":false,"quiet_ms":1}}`)
	if !strings.Contains(text(res), "recording stopped at its limit of 1 step and saved: "+dir) {
		t.Fatalf("%s", text(res))
	}
	if code := errorCode(t, e.act(`{"window":"0xa1","record":"stop"}`)); code != "invalid_arguments" {
		t.Errorf("a stop after the limit: %q", code)
	}
}

// When the session ends, the recording is saved as a stop would.
func TestSessionEndSavesTheRecording(t *testing.T) {
	e, dir := recordingEnv(t)
	e.window("0xa1", "1a", 320, 200)
	if res := e.act(`{"window":"0xa1","actions":[["key","a"]],"record":"slides","wait":{"expect_change":false,"quiet_ms":1}}`); res.IsError {
		t.Fatal(text(res))
	}
	e.server.Shutdown(context.Background())
	entries, _ := os.ReadDir(dir)
	if len(entries) != 1 || !strings.HasSuffix(entries[0].Name(), ".mp4") || strings.HasSuffix(entries[0].Name(), ".part.mp4") {
		t.Fatalf("the directory has %v", entries)
	}
	if d, _ := strconv.ParseFloat(probe(t, filepath.Join(dir, entries[0].Name()), "duration"), 64); d < 3.4 || d > 3.6 {
		t.Errorf("the video lasts %v s", d)
	}
}

// Video mode records the window while the act runs, at real speed, also what it shows only during the wait.
func TestVideoRecordsTheActAtRealSpeed(t *testing.T) {
	slow(t)
	e, _ := recordingEnv(t)
	dark := func(x, y float64) uint8 { return 20 }
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = dark })
	waited := false
	e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
		if !waited {
			waited = true
			// Light for a moment during the act's wait, dark again before its end, so only frames during the wait
			// show it.
			e.fake.with(func() { w.Paint = func(x, y float64) uint8 { return 230 } })
			time.Sleep(1500 * time.Millisecond)
			e.fake.with(func() { w.Paint = dark })
			time.Sleep(500 * time.Millisecond)
		}
		var reply string
		e.fake.with(func() { reply = quietReply(w) })
		return reply
	}
	res := e.act(`{"window":"0xa1","actions":[["key","a"]],"record":"video"}`)
	if res.IsError {
		t.Fatal(text(res))
	}
	res = e.act(`{"window":"0xa1","record":"stop"}`)
	m := regexp.MustCompile(`recording saved: (\S+\.mp4) \(0:0(\d)`).FindStringSubmatch(text(res))
	if m == nil {
		t.Fatalf("%s", text(res))
	}
	// The act's 2 s at least, its result held for a second, and the last frame for one more.
	if seconds, _ := strconv.Atoi(m[2]); seconds < 4 {
		t.Errorf("the video lasts %s s", m[2])
	}
	// The middle of each frame: dark before the input and at the end, light in frames during the wait.
	out, err := exec.Command("ffmpeg", "-v", "error", "-i", m[1], "-vf", "crop=2:2:320:200", "-f", "rawvideo", "-pix_fmt", "gray", "-").Output()
	if err != nil || len(out) < 12 {
		t.Fatalf("the frames of %s: %v", m[1], err)
	}
	var middle []uint8
	for i := 0; i < len(out); i += 4 {
		middle = append(middle, out[i])
	}
	if first, last := middle[0], middle[len(middle)-1]; first > 40 || last > 40 || slices.Max(middle) < 200 {
		t.Errorf("the middle of the frames is %v", middle)
	}
}

// An act that waited for its turn while another stopped the recording is refused as it would have been without the
// wait, before it sends anything.
func TestQueuedActCheckedAgain(t *testing.T) {
	e, _ := recordingEnv(t)
	e.window("0xa1", "1a", 320, 200)
	if res := e.act(`{"window":"0xa1","record":"slides"}`); res.IsError {
		t.Fatal(text(res))
	}
	// Another act records a step meanwhile.
	e.server.recTurn <- struct{}{}
	queued := make(chan string, 1)
	go func() {
		res := callTool(e.server, context.Background(), actOn, `{"window":"0xa1","actions":[["key","a"]],"caption":"Type a"}`)
		queued <- errorCode(t, res) + ": " + text(res)
	}()
	// It has passed the check before it waits for its turn.
	time.Sleep(200 * time.Millisecond)
	e.server.stopRecording(context.Background(), e.server.recording(), "")
	<-e.server.recTurn
	if got := <-queued; !strings.HasPrefix(got, "invalid_arguments: ") || !strings.Contains(got, "caption needs a recording") {
		t.Errorf("the queued act: %s", got)
	}
	if e.inputs() != nil {
		t.Errorf("sent input: %v", e.inputs())
	}
}

// The result of an act that takes longer than the frame before it was given is still recorded, also when the act
// captures nothing itself.
func TestLongActKeepsItsResult(t *testing.T) {
	slow(t)
	e, _ := recordingEnv(t, func(o *record.Options) { o.GIF = true })
	e.window("0xa1", "1a", 320, 200)
	waited := false
	e.fake.onWait = func(w *fakeWindow, args map[string]string, cancel <-chan struct{}) string {
		if !waited {
			waited = true
			time.Sleep(recordFrameTime + 500*time.Millisecond)
		}
		var reply string
		e.fake.with(func() { reply = quietReply(w) })
		return reply
	}
	if res := e.act(`{"window":"0xa1","actions":[["key","a"]],"record":"slides","capture":"none"}`); res.IsError {
		t.Fatal(text(res))
	}
	res := e.act(`{"window":"0xa1","record":"stop"}`)
	m := regexp.MustCompile(`GIF (\S+\.gif)`).FindStringSubmatch(text(res))
	if m == nil {
		t.Fatalf("%s", text(res))
	}
	// The window before the input, and the result.
	if frames, _ := gifFrames(t, m[1]); len(frames) != 2 {
		t.Errorf("the GIF has %d frames", len(frames))
	}
}

// An act that waits for the recording's turn gives up where its deadline leaves too little time for its input, as it
// does for the window lock, and sends nothing.
func TestRecordingTurnWithinTheDeadline(t *testing.T) {
	slow(t)
	e, _ := recordingEnv(t)
	e.window("0xa1", "1a", 320, 200)
	if res := e.act(`{"window":"0xa1","record":"slides"}`); res.IsError {
		t.Fatal(text(res))
	}
	// Another act records a step for longer than the deadline.
	e.server.recTurn <- struct{}{}
	time.AfterFunc(5*time.Second, func() { <-e.server.recTurn })
	start := time.Now()
	res := e.act(`{"window":"0xa1","actions":[["key","a"]],"deadline_ms":3000}`)
	if took := time.Since(start); took > 3*time.Second {
		t.Errorf("the act took %v", took)
	}
	if code := errorCode(t, res); code != "deadline" || !strings.Contains(text(res), "for the recording's turn, held by another act of this session, until too little time was left before the deadline") {
		t.Errorf("code %q: %s", code, text(res))
	}
	if e.inputs() != nil {
		t.Errorf("sent input: %v", e.inputs())
	}
}

// Only the actions that went out are drawn: here the click, and not the key after it, which failed.
func TestRecordingDrawsDeliveredActions(t *testing.T) {
	e, _ := recordingEnv(t, func(o *record.Options) { o.GIF = true })
	e.window("0xa1", "1a", 320, 200, func(w *fakeWindow) { w.Paint = func(x, y float64) uint8 { return 20 } })
	e.screenshot(`{"window":"0xa1"}`)
	// The sender's actions are move, click, sync and keys, and the keys fail.
	t.Setenv("FAKE_INPUT_MODE", "fail:unconfirmed:3:partial:0")
	res := e.act(`{"shot":"s1","actions":[[200,100],["key","ctrl+s"]],"record":"slides"}`)
	if got := get(t, res, "actions.done"); got != 1.0 {
		t.Fatalf("done %v: %s", got, text(res))
	}
	res = e.act(`{"window":"0xa1","record":"stop"}`)
	m := regexp.MustCompile(`GIF (\S+\.gif)`).FindStringSubmatch(text(res))
	if m == nil {
		t.Fatalf("%s", text(res))
	}
	frames, _ := gifFrames(t, m[1])
	if c := frames[0].At(200-11, 100); !isCursorBlue(c) {
		t.Errorf("the ring around the click is %v", c)
	}
	// The bar of 40 pixels below the window of 640×400 has no chip for the key.
	for x := 320; x < 640; x++ {
		for y := 400; y < 440; y++ {
			if c := frames[0].At(x, y); isCursorBlue(c) {
				t.Fatalf("the bar has a chip at %d,%d: %v", x, y, c)
			}
		}
	}
}
