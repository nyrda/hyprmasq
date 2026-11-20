package server

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"time"
)

// fileLock is an flock held on its own open file, so it also excludes other calls of the same process.
type fileLock struct {
	file *os.File
}

// A lockFile is a lock file in a private directory, which acquireLock opens without following a symlink.
type lockFile struct {
	dir  privatePath
	name string
}

func (l lockFile) String() string {
	return filepath.Join(l.dir.String(), l.name)
}

const lockPoll = 10 * time.Millisecond

// acquireLock takes an exclusive flock on the lock file, polling so that the wait ends with ctx.
func acquireLock(ctx context.Context, lock lockFile) (*fileLock, error) {
	f, err := openLock(lock)
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

// openLock opens the lock file in its private directory, which it creates, and creates the file with mode 0600. It
// refuses a symlink, and a file that is not a regular file of the user's own without bits for its group and others.
func openLock(lock lockFile) (*os.File, error) {
	dir, err := lock.dir.open(true)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	fd, err := syscall.Openat(int(dir.Fd()), lock.name, syscall.O_RDWR|syscall.O_CREAT|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0o600)
	if errors.Is(err, syscall.ELOOP) {
		err = errors.New("a symlink")
	}
	if err != nil {
		return nil, &os.PathError{Op: "open lock", Path: lock.String(), Err: err}
	}
	var st syscall.Stat_t
	if err = syscall.Fstat(fd, &st); err == nil {
		switch {
		case st.Mode&syscall.S_IFMT != syscall.S_IFREG:
			err = errors.New("not a regular file")
		case st.Uid != uint32(os.Getuid()):
			err = fmt.Errorf("owned by uid %d, not %d", st.Uid, os.Getuid())
		case st.Mode&0o077 != 0:
			err = fmt.Errorf("mode %#o, which gives its group or others access", st.Mode&0o777)
		}
	}
	if err != nil {
		syscall.Close(fd)
		return nil, &os.PathError{Op: "open lock", Path: lock.String(), Err: err}
	}
	return os.NewFile(uintptr(fd), lock.String()), nil
}

func (l *fileLock) release() {
	if l != nil {
		// Closing the only descriptor of the open file releases the flock.
		l.file.Close()
	}
}
