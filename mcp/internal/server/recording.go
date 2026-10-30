package server

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
	"github.com/nyrda/hyprmasq/mcp/internal/record"
)

// A recording follows the session's act calls, from the one with record "slides" or "video" to the one with record
// "stop": the frame before each act's input, with the input drawn on it, the frames during the act in video mode, and
// the frame of its result. The frames come from the act's own captures where it has them.

// How long the frames before and after an act may take, and how long the files of a recording may take to finish when
// an act stops it and when the session ends.
const (
	recordFrameTime  = 5 * time.Second
	recordFinishTime = 30 * time.Second
	recordExitTime   = 10 * time.Second
	// The long side of the frames that the recording renders itself, when --max-image-size allows bigger ones.
	recordMaxImage = 1920
	captionMax     = 120
)

// A recordedFrame is the last frame of a window while a recording runs, with the commit counter it was rendered at.
type recordedFrame struct {
	frame   *record.Frame
	commits *int64
	socket  string
}

// recStep is what an act tells the recording while it runs. Its methods do nothing on nil, an act that is not
// recorded.
type recStep struct {
	step *record.Step
	// Stops the sampling of video mode, once.
	stopSampling func()
	once         sync.Once
	// When the act began its capture, after which its frames show its result.
	finishAt time.Time
}

// inputStarted gives each action of the steps the time that the sender reaches it, at its pace from at.
func (rs *recStep) inputStarted(at time.Time, steps []inputStep) {
	if rs == nil {
		return
	}
	begins, ends := map[int]time.Duration{}, map[int]time.Duration{}
	var prev time.Duration
	for j, step := range steps {
		end := minSendTime(steps[:j+1])
		if _, ok := begins[step.Batch]; !ok {
			begins[step.Batch] = prev
		}
		ends[step.Batch] = end
		prev = end
	}
	for index, begin := range begins {
		rs.step.Timed(index, at.Add(begin), ends[index]-begin)
	}
}

// finishing marks the start of the act's capture, and stops sampling, which would delay it.
func (rs *recStep) finishing() {
	if rs == nil {
		return
	}
	rs.once.Do(func() {
		if rs.stopSampling != nil {
			rs.stopSampling()
		}
		rs.finishAt = time.Now()
	})
}

// recordCheck refuses a record or caption that does not fit the session's recording, before anything is sent.
func (s *Server) recordCheck(r *actRequest) error {
	running := s.recording() != nil
	switch starting := r.record == "slides" || r.record == "video"; {
	case starting && running:
		return invalidf("record %q: a recording is running; stop it with record \"stop\" first", r.record)
	case r.record == "stop" && !running:
		return invalidf("record \"stop\": no recording is running")
	case r.caption != nil && !running && !starting:
		return invalidf("caption needs a recording: start one with record \"slides\" or \"video\"")
	}
	return nil
}

func (s *Server) recording() *record.Recording {
	s.recMu.Lock()
	defer s.recMu.Unlock()
	return s.rec
}

// takeRecording ends the session's recording, if it is rec, and returns whether it was.
func (s *Server) takeRecording(rec *record.Recording) bool {
	s.recMu.Lock()
	defer s.recMu.Unlock()
	if s.rec != rec || rec == nil {
		return false
	}
	s.rec, s.recFrames = nil, nil
	return true
}

// recordsAct tells whether an act takes part in a recording.
func (s *Server) recordsAct(r *actRequest) bool {
	return r.record != "" || r.caption != nil || s.recording() != nil
}

// recordedAct runs an act that takes part in a recording: it starts the recording, records the act as a step, and
// stops the recording, as the act asks.
func (s *Server) recordedAct(ctx context.Context, r *actRequest, start time.Time, p *progress) *mcp.CallToolResult {
	window := r.window
	if window == "" {
		if sh := s.shots.get(r.shotID); sh != nil {
			window = sh.Window
		}
	}
	starting := r.record == "slides" || r.record == "video"
	refuse := func(te *toolError) *mcp.CallToolResult {
		return errorResult(te, &actOut{Actions: &actionsOut{Total: len(r.batch), Delivery: "none"}, NewWindows: []newWindowOut{}}, nil, nil)
	}
	if window == "" && starting {
		// act fails on the shot, without a window to record.
		return s.act(ctx, r, start, p)
	}
	// One act records at a time, so that the steps follow each other. The wait for the turn counts towards the
	// deadline, as the wait for the window lock does, and ends where too little time would be left for the input.
	turnBy := start.Add(r.deadline - captureReserve)
	if len(r.batch) > 0 {
		turnBy = start.Add(inputTime(r.deadline))
	}
	turnCtx, cancelTurn := context.WithDeadline(ctx, turnBy)
	select {
	case s.recTurn <- struct{}{}:
		cancelTurn()
		defer func() { <-s.recTurn }()
	case <-turnCtx.Done():
		cancelTurn()
		if ctx.Err() != nil {
			return s.act(ctx, r, start, p)
		}
		return refuse(asToolError(lockError(ctx, context.DeadlineExceeded, start, "the recording's turn, held by another act of this session")))
	}
	// Another act may have started or stopped the recording while this one waited for its turn.
	if err := s.recordCheck(r); err != nil {
		return refuse(asToolError(err))
	}
	work, cancel := context.WithTimeout(context.WithoutCancel(ctx), recordFrameTime)
	defer cancel()

	if starting {
		rec, err := record.Start(record.Mode(r.record), s.cfg.Record, time.Now())
		if err != nil {
			return refuse(&toolError{Code: "recording_failed", Message: err.Error(), Hint: "Tell the user, who can fix it in the configuration file. Nothing was sent."})
		}
		s.recMu.Lock()
		if s.rec != nil {
			s.recMu.Unlock()
			rec.Abort()
			return refuse(asToolError(invalidf("record %q: a recording is running; stop it with record \"stop\" first", r.record)))
		}
		s.rec, s.recFrames = rec, map[string]*recordedFrame{}
		s.recMu.Unlock()
	}
	rec := s.recording()
	if rec == nil || window == "" {
		return s.act(ctx, r, start, p)
	}
	if r.caption != nil {
		rec.SetCaption(*r.caption)
	}

	var line string
	var rs *recStep
	if !(r.record == "stop" && len(r.batch) == 0) {
		var before *record.Frame
		if len(r.batch) > 0 || starting {
			before = s.beforeFrame(work, window)
		}
		step, err := rec.Begin(window, before, recordMarks(r.batch, s.shots.get(r.shotID)))
		switch {
		case err != nil && starting:
			s.takeRecording(rec)
			rec.Abort()
			return refuse(&toolError{Code: "recording_failed", Message: err.Error(), Hint: "Tell the user, who can fix it in the configuration file. Nothing was sent."})
		case err != nil:
			line = s.recordingFailed(rec, err)
		default:
			rs = &recStep{step: step}
			if rec.Mode() == record.Video {
				rs.stopSampling = s.sample(window, step, start.Add(r.deadline-captureReserve), before == nil)
			}
			r.rec = rs
		}
	}

	res := s.act(ctx, r, start, p)

	if rs != nil {
		rs.finishing()
		out, _ := res.StructuredContent.(*actOut)
		// The actions that went out, without those after a failure or a scroll held back.
		delivered := 0
		if out != nil && out.Actions != nil && out.Actions.Delivery != "none" {
			delivered = out.Actions.Done
		}
		var after *record.Frame
		if out == nil || !out.WindowClosed {
			// The act may have taken longer than the frame before it was given.
			capture, cancel := context.WithTimeout(context.WithoutCancel(ctx), recordFrameTime)
			after = s.afterFrame(capture, window, rs.finishAt)
			cancel()
		}
		if err := rs.step.End(after, delivered); err != nil {
			line = s.recordingFailed(rec, err)
		}
	}
	if line == "" {
		switch limit := rec.Limit(); {
		case r.record == "stop" || limit != "":
			line = s.stopRecording(ctx, rec, limit)
		case starting:
			line = fmt.Sprintf("recording %s to %s; an act with record \"stop\" saves it", r.record, rec.Path())
		}
	}
	return withRecording(res, line)
}

// stopRecording finishes the recording, and returns the line of the result that tells what it saved. limit, when set,
// tells which limit stopped it.
func (s *Server) stopRecording(ctx context.Context, rec *record.Recording, limit string) string {
	if !s.takeRecording(rec) {
		return ""
	}
	finish, cancel := context.WithTimeout(context.WithoutCancel(ctx), recordFinishTime)
	defer cancel()
	files, err := rec.Finish(finish)
	if err != nil {
		return "recording failed: " + err.Error()
	}
	line := "recording saved: " + describeFiles(files)
	if limit != "" {
		line = fmt.Sprintf("recording stopped at %s and saved: %s", limit, describeFiles(files))
	}
	return line
}

// recordingFailed ends a recording that failed, and returns the line of the result that says so.
func (s *Server) recordingFailed(rec *record.Recording, err error) string {
	if !s.takeRecording(rec) {
		return ""
	}
	finish, cancel := context.WithTimeout(context.Background(), recordFinishTime)
	defer cancel()
	if _, ferr := rec.Finish(finish); ferr != nil {
		err = ferr
	}
	return "recording failed and stopped: " + err.Error()
}

// finishRecording finishes the recording when the session ends, and logs what it saved.
func (s *Server) finishRecording() {
	rec := s.recording()
	if rec == nil || !s.takeRecording(rec) {
		return
	}
	finish, cancel := context.WithTimeout(context.Background(), recordExitTime)
	defer cancel()
	files, err := rec.Finish(finish)
	if err != nil {
		log.Printf("the session ended, and its recording failed: %v", err)
		return
	}
	log.Printf("the session ended, and its recording was saved: %s", describeFiles(files))
}

// describeFiles names the files of a recording, with its length, steps and sizes.
func describeFiles(f record.Files) string {
	steps := "steps"
	if f.Steps == 1 {
		steps = "step"
	}
	out := fmt.Sprintf("%s (%s, %d %s, %s)", f.Video, record.FormatDuration(f.Duration), f.Steps, steps, formatBytes(f.VideoBytes))
	if f.GIF != "" {
		out += fmt.Sprintf(", GIF %s (%s)", f.GIF, formatBytes(f.GIFBytes))
	}
	return out
}

func formatBytes(n int64) string {
	if n < 1000*1000 {
		return fmt.Sprintf("%d kB", max(1, (n+500)/1000))
	}
	return fmt.Sprintf("%.1f MB", float64(n)/1e6)
}

// withRecording adds the line about the recording to an act's result.
func withRecording(res *mcp.CallToolResult, line string) *mcp.CallToolResult {
	if line == "" {
		return res
	}
	if out, ok := res.StructuredContent.(*actOut); ok {
		out.Recording = line
	}
	for _, c := range res.Content {
		if t, ok := c.(*mcp.TextContent); ok {
			t.Text = strings.TrimPrefix(t.Text+"\n"+line, "\n")
			break
		}
	}
	return res
}

// beforeFrame is the window before an act's input: its last frame while it has not committed since, or a new one, or
// nil when it cannot be captured.
func (s *Server) beforeFrame(ctx context.Context, window string) *record.Frame {
	s.recMu.Lock()
	last := s.recFrames[window]
	s.recMu.Unlock()
	if last != nil && last.commits != nil {
		if st, err := s.hypr.State(ctx, window); err == nil && st.Commits != nil && *st.Commits == *last.commits && deref(st.SocketID) == last.socket {
			f := *last.frame
			f.At = time.Now()
			return &f
		}
	}
	f, err := s.recordShot(ctx, window)
	if err != nil {
		return nil
	}
	return f
}

// afterFrame is the window after an act: the frame of the act's own capture, when it rendered the whole window after
// since, or a new one, or nil when it cannot be captured.
func (s *Server) afterFrame(ctx context.Context, window string, since time.Time) *record.Frame {
	s.recMu.Lock()
	last := s.recFrames[window]
	s.recMu.Unlock()
	if last != nil && !last.frame.At.Before(since) {
		return last.frame
	}
	f, err := s.recordShot(ctx, window)
	if err != nil {
		return nil
	}
	return f
}

// noteFrame keeps the frame as the window's last while a recording runs.
func (s *Server) noteFrame(window string, f *record.Frame, commits *int64, socket *string) {
	s.recMu.Lock()
	defer s.recMu.Unlock()
	if s.rec != nil {
		s.recFrames[window] = &recordedFrame{frame: f, commits: commits, socket: deref(socket)}
	}
}

// noteRendered keeps the overview of a capture as the window's last frame while a recording runs.
func (s *Server) noteRendered(window string, f *frame, at time.Time) {
	if s.recording() == nil || f.overview == nil {
		return
	}
	s.noteFrame(window, &record.Frame{Image: f.overview, Region: recordRect(f.reply.Region), Scale: f.reply.Scale, At: at}, f.reply.Commits, f.reply.SocketID)
}

func recordRect(r hypr.Rect) record.Rect {
	return record.Rect{X: r.X, Y: r.Y, Width: r.Width, Height: r.Height}
}

// recordShot renders the whole window for the recording.
func (s *Server) recordShot(ctx context.Context, window string) (*record.Frame, error) {
	if err := os.MkdirAll(s.shotDir, 0o700); err != nil {
		return nil, err
	}
	path := filepath.Join(s.shotDir, fmt.Sprintf("r%d.raw", s.detailSeq.Add(1)))
	defer os.Remove(path)
	maxImage := s.cfg.MaxImageSize
	if maxImage <= 0 || maxImage > recordMaxImage {
		maxImage = recordMaxImage
	}
	at := time.Now()
	reply, err := s.hypr.Screenshot(ctx, window, hypr.ScreenshotArgs{Path: path, Max: maxImage, Raw: true})
	if err != nil {
		return nil, err
	}
	img, err := hypr.ReadRaw(path, reply.Width, reply.Height)
	if err != nil {
		return nil, err
	}
	f := &record.Frame{Image: img, Region: recordRect(reply.Region), Scale: reply.Scale, At: at}
	s.noteFrame(window, f, reply.Commits, reply.SocketID)
	return f, nil
}

// sample records frames of the window for video mode until the returned function is called or until, at most fps a
// second, and fewer when a frame takes long, so that the recording takes at most half of Hyprland's time. A frame that
// is being rendered when it stops is waited for, since the plugin would write its file after the server deleted it.
func (s *Server) sample(window string, step *record.Step, until time.Time, immediate bool) func() {
	ctx, cancel := context.WithDeadline(context.Background(), until)
	stop, done := make(chan struct{}), make(chan struct{})
	interval := time.Second / time.Duration(max(1, s.cfg.Record.FPS))
	go func() {
		defer close(done)
		defer cancel()
		next := time.Now().Add(interval)
		if immediate {
			next = time.Now()
		}
		for {
			timer := time.NewTimer(time.Until(next))
			select {
			case <-stop:
				timer.Stop()
				return
			case <-ctx.Done():
				timer.Stop()
				return
			case <-timer.C:
			}
			t0 := time.Now()
			f, err := s.recordShot(ctx, window)
			if err != nil {
				if ctx.Err() != nil || asToolError(err).Code == "window_gone" {
					return
				}
			} else {
				step.Sample(f)
			}
			next = t0.Add(max(interval, 2*time.Since(t0)))
		}
	}()
	return func() {
		close(stop)
		<-done
	}
}

// recordMarks are the actions of a batch as the recording draws them, in window-local logical pixels.
func recordMarks(batch []action, sh *shot) []record.Mark {
	marks := make([]record.Mark, len(batch))
	for i, a := range batch {
		if a.hasCoordinates() && sh == nil {
			continue
		}
		point := func(x, y float64) [2]float64 {
			lx, ly := sh.toLogical(x, y)
			return [2]float64{lx, ly}
		}
		m := record.Mark{Kind: a.Kind}
		switch a.Kind {
		case "click":
			m.Points, m.Button, m.Count, m.Label = [][2]float64{point(a.X, a.Y)}, a.Button, a.Count, strings.Join(a.Modifiers, "+")
		case "move":
			m.Points = [][2]float64{point(a.X, a.Y)}
		case "path":
			// The points that the input goes through, sampled from a spline as for the sender.
			m.Points, m.Button, m.Label = pathInput(a, sh.toLogical), a.Button, strings.Join(a.Modifiers, "+")
		case "scroll":
			m.Points, m.Label = [][2]float64{point(a.X, a.Y)}, scrollLabel(a)
		case "type":
			n := utf8.RuneCountInString(a.Text)
			m.Label = fmt.Sprintf("typing %d characters", n)
			if n == 1 {
				m.Label = "typing 1 character"
			}
		case "key":
			m.Kind, m.Label = "keys", strings.Join(a.Keys, "+")
			if a.Repeat > 1 {
				m.Label += fmt.Sprintf(" ×%d", a.Repeat)
			}
		default:
			m.Kind = ""
		}
		marks[i] = m
	}
	return marks
}

// scrollLabel names a scroll, such as "scroll down 3" or "page up".
func scrollLabel(a action) string {
	if a.Unit == "page" {
		if a.DY < 0 {
			return "page up"
		}
		return "page down"
	}
	unit := ""
	if a.Unit == "px" {
		unit = " px"
	}
	var parts []string
	for _, d := range []struct {
		v        float64
		neg, pos string
	}{{a.DY, "up", "down"}, {a.DX, "left", "right"}} {
		switch {
		case d.v < 0:
			parts = append(parts, fmt.Sprintf("%s %s%s", d.neg, formatNumber(-d.v), unit))
		case d.v > 0:
			parts = append(parts, fmt.Sprintf("%s %s%s", d.pos, formatNumber(d.v), unit))
		}
	}
	return "scroll " + strings.Join(parts, " ")
}
