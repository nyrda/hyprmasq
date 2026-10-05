package server

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// fileLock is an flock held on its own open file, so it also excludes other calls of the same process.
type fileLock struct {
	file *os.File
}

const lockPoll = 10 * time.Millisecond

// acquireLock takes an exclusive flock on path, polling so that the wait ends with ctx.
func acquireLock(ctx context.Context, path string) (*fileLock, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	for {
		err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return &fileLock{file: f}, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EINTR) {
			f.Close()
			return nil, err
		}
		select {
		case <-ctx.Done():
			f.Close()
			return nil, ctx.Err()
		case <-time.After(lockPoll):
		}
	}
}

func (l *fileLock) release() {
	if l != nil {
		// Closing the only descriptor of the open file releases the flock.
		l.file.Close()
	}
}
