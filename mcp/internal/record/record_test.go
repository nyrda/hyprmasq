package record

import (
	"context"
	"image"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// fakeFFmpeg writes a script that stands in for ffmpeg, and returns options that run it.
func fakeFFmpeg(t *testing.T, script string) Options {
	t.Helper()
	path := filepath.Join(t.TempDir(), "ffmpeg")
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+script+"\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	return Options{Dir: t.TempDir(), GIFWidth: 800, FPS: 10, MaxSteps: 500, MaxTime: time.Minute, FFmpeg: path}
}

// A frame larger than a pipe's buffer, so that writing it waits for ffmpeg to read.
func bigFrame() *Frame {
	return &Frame{Image: image.NewNRGBA(image.Rect(0, 0, 640, 400)), Region: Rect{Width: 640, Height: 400}, Scale: 1, At: time.Now()}
}

// finishWithin fails the test when Finish does not return in time.
func finishWithin(t *testing.T, r *Recording, d time.Duration) error {
	t.Helper()
	done := make(chan error, 1)
	go func() {
		ctx, cancel := context.WithTimeout(context.Background(), d)
		defer cancel()
		_, err := r.Finish(ctx)
		done <- err
	}()
	select {
	case err := <-done:
		return err
	case <-time.After(d + 5*time.Second):
		t.Fatal("Finish did not return")
		return nil
	}
}

// An ffmpeg that exits while frames are written, as on a full disk, stops the recording with its message, and
// finishing it returns.
func TestEncoderThatExits(t *testing.T) {
	opts := fakeFFmpeg(t, "head -c 100 > /dev/null\necho 'No space left on device' >&2\nexit 1")
	r, err := Start(Slides, opts, time.Now())
	if err != nil {
		t.Fatal(err)
	}
	st, err := r.Begin("0xa1", bigFrame(), nil)
	if err != nil {
		t.Fatal(err)
	}
	if err := st.End(bigFrame(), 0); err == nil || !strings.Contains(err.Error(), "No space left on device") {
		t.Errorf("End: %v", err)
	}
	if err := finishWithin(t, r, 2*time.Second); err == nil || !strings.Contains(err.Error(), "No space left on device") {
		t.Errorf("Finish: %v", err)
	}
}

// An ffmpeg that stops reading frames is stopped after writeTimeout, so that a stop or the session's end does not wait
// for it.
func TestEncoderThatHangs(t *testing.T) {
	if testing.Short() {
		t.Skip("waits for the write timeout; runs without -short")
	}
	r, err := Start(Slides, fakeFFmpeg(t, "exec sleep 60"), time.Now())
	if err != nil {
		t.Fatal(err)
	}
	st, err := r.Begin("0xa1", bigFrame(), nil)
	if err != nil {
		t.Fatal(err)
	}
	start := time.Now()
	if err := st.End(bigFrame(), 0); err == nil || !strings.Contains(err.Error(), "ffmpeg read no frame for 5s") {
		t.Errorf("End: %v", err)
	}
	if took := time.Since(start); took > writeTimeout+2*time.Second {
		t.Errorf("End took %v", took)
	}
	finishWithin(t, r, 2*time.Second)
}

// Recordings that start in the same second get names of their own, also in two sessions.
func TestStartReservesNames(t *testing.T) {
	opts := fakeFFmpeg(t, "exit 0")
	now := time.Now()
	a, err := Start(Slides, opts, now)
	if err != nil {
		t.Fatal(err)
	}
	defer a.Abort()
	b, err := Start(Slides, opts, now)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Abort()
	stamp := now.Format("20060102-150405")
	if a.Path() != filepath.Join(opts.Dir, stamp+".mp4") || b.Path() != filepath.Join(opts.Dir, stamp+"-2.mp4") {
		t.Errorf("the recordings are %s and %s", a.Path(), b.Path())
	}
}
