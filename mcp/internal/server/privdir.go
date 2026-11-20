package server

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"syscall"
)

// A privatePath is a directory that the server keeps to its user, as the plugin keeps the directories of its sockets
// (privateDir in src/Eis.cpp): base, which it opens without following a symlink and trusts otherwise, and the
// directories under it down to the one it names. It steps through them with openat, so that no symlink is followed,
// and accepts each only as a directory of the user's own with mode 0700, which it creates when asked to.
type privatePath struct {
	base  string
	names []string
	err   error
}

// newPrivatePath names dir, which is base or a directory under it.
func newPrivatePath(base, dir string) privatePath {
	rel, err := filepath.Rel(base, dir)
	if err != nil || rel == ".." || strings.HasPrefix(rel, "../") || filepath.IsAbs(rel) {
		return privatePath{err: fmt.Errorf("%s is not in %s", dir, base)}
	}
	p := privatePath{base: base}
	if rel != "." {
		p.names = strings.Split(rel, "/")
	}
	return p
}

// join names a directory under p.
func (p privatePath) join(names ...string) privatePath {
	return privatePath{base: p.base, names: append(append([]string(nil), p.names...), names...), err: p.err}
}

func (p privatePath) String() string {
	return filepath.Join(append([]string{p.base}, p.names...)...)
}

// open opens the directory, creating the missing ones on the way when create is set. It fails on a symlink, on what is
// not a directory, and on a directory of another user or with a mode other than 0700.
func (p privatePath) open(create bool) (*os.File, error) {
	if p.err != nil {
		return nil, p.err
	}
	fd, err := syscall.Open(p.base, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, &os.PathError{Op: "open", Path: p.base, Err: err}
	}
	path := p.base
	for _, name := range p.names {
		path = filepath.Join(path, name)
		next, err := privateDir(fd, name, create)
		syscall.Close(fd)
		if err != nil {
			return nil, &os.PathError{Op: "open private directory", Path: path, Err: err}
		}
		fd = next
	}
	return os.NewFile(uintptr(fd), p.String()), nil
}

// privateDir opens the directory name in parent, creating it with mode 0700 first when create is set.
func privateDir(parent int, name string, create bool) (int, error) {
	if name == "" || name == "." || name == ".." || strings.Contains(name, "/") {
		return -1, fmt.Errorf("%q is not a directory name", name)
	}
	if create {
		if err := syscall.Mkdirat(parent, name, 0o700); err != nil && !errors.Is(err, syscall.EEXIST) {
			return -1, err
		}
	}
	fd, err := syscall.Openat(parent, name, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		if errors.Is(err, syscall.ELOOP) || errors.Is(err, syscall.ENOTDIR) {
			return -1, errors.New("a symlink or not a directory")
		}
		return -1, err
	}
	var st syscall.Stat_t
	if err := syscall.Fstat(fd, &st); err != nil {
		syscall.Close(fd)
		return -1, err
	}
	if err := ownPrivate(&st, 0o700); err != nil {
		syscall.Close(fd)
		return -1, err
	}
	return fd, nil
}

// ownPrivate checks that a file belongs to the user and has the mode, without bits for its group and others.
func ownPrivate(st *syscall.Stat_t, mode uint32) error {
	if st.Uid != uint32(os.Getuid()) {
		return fmt.Errorf("owned by uid %d, not %d", st.Uid, os.Getuid())
	}
	if m := st.Mode & 0o777; m != mode {
		return fmt.Errorf("mode %#o, not %#o", m, mode)
	}
	return nil
}
