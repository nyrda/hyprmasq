package server

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
)

// stateLayout is $XDG_RUNTIME_DIR/hyprmasq/<instance> in a temporary runtime directory, with a server on it.
type stateLayout struct {
	runtime, state string
	server         *Server
}

func newStateLayout(t *testing.T) stateLayout {
	runtime := t.TempDir()
	state := filepath.Join(runtime, "hyprmasq", "inst")
	return stateLayout{runtime: runtime, state: state, server: New(Config{StateDir: state, RuntimeDir: runtime})}
}

// The directories of the path from $XDG_RUNTIME_DIR to a server's shots, in order.
func (l stateLayout) levels() []string {
	return []string{filepath.Join(l.runtime, "hyprmasq"), l.state, filepath.Join(l.state, "mcp"), l.server.shotDir}
}

func mkdir(t *testing.T, path string, mode os.FileMode) {
	t.Helper()
	if err := os.Mkdir(path, mode); err != nil {
		t.Fatal(err)
	}
	// The umask may have taken bits away.
	if err := os.Chmod(path, mode); err != nil {
		t.Fatal(err)
	}
}

func symlink(t *testing.T, target, path string) {
	t.Helper()
	if err := os.Symlink(target, path); err != nil {
		t.Fatal(err)
	}
}

func modeOf(t *testing.T, path string) os.FileMode {
	t.Helper()
	info, err := os.Lstat(path)
	if err != nil {
		t.Fatal(err)
	}
	return info.Mode()
}

func exists(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

// The server creates the directories down to its shots and its lock files private to the user, and uses them again.
func TestPrivateDirectoriesAreCreatedAndReused(t *testing.T) {
	l := newStateLayout(t)
	for range 2 {
		if err := l.server.makeShotDir(); err != nil {
			t.Fatal(err)
		}
		lock, err := acquireLock(context.Background(), l.server.lockPath("1a"))
		if err != nil {
			t.Fatal(err)
		}
		lock.release()
	}
	for _, dir := range l.levels() {
		if mode := modeOf(t, dir); mode != os.ModeDir|0o700 {
			t.Errorf("%s: mode %v", dir, mode)
		}
	}
	if mode := modeOf(t, filepath.Join(l.state, "window-1a.lock")); mode != 0o600 {
		t.Errorf("lock file: mode %v", mode)
	}
}

// Opening without creating fails on a missing directory and leaves it missing.
func TestPrivateDirectoryOpenWithoutCreating(t *testing.T) {
	l := newStateLayout(t)
	if dir, err := l.server.shotsPath.open(false); err == nil {
		dir.Close()
		t.Fatal("opened a missing directory")
	}
	if exists(filepath.Join(l.runtime, "hyprmasq")) {
		t.Error("created a directory")
	}
}

// A symlink anywhere from $XDG_RUNTIME_DIR down to the shots or a lock file is refused, also when it points at a
// private directory of the user's own, and nothing is created where it points.
func TestPrivateDirectoriesRefuseSymlinks(t *testing.T) {
	for i, name := range []string{"hyprmasq", "instance", "mcp", "pid"} {
		t.Run(name, func(t *testing.T) {
			l := newStateLayout(t)
			levels := l.levels()
			for _, dir := range levels[:i] {
				mkdir(t, dir, 0o700)
			}
			target := t.TempDir()
			symlink(t, target, levels[i])
			if err := l.server.makeShotDir(); err == nil || !strings.Contains(err.Error(), "symlink") {
				t.Errorf("shots: %v", err)
			}
			if i < 2 {
				if _, err := acquireLock(context.Background(), l.server.clipboardLockPath()); err == nil || !strings.Contains(err.Error(), "symlink") {
					t.Errorf("lock: %v", err)
				}
			}
			if entries, _ := os.ReadDir(target); len(entries) != 0 {
				t.Errorf("created %v where the symlink points", entries)
			}
		})
	}
	t.Run("lock", func(t *testing.T) {
		l := newStateLayout(t)
		mkdir(t, filepath.Join(l.runtime, "hyprmasq"), 0o700)
		mkdir(t, l.state, 0o700)
		target := filepath.Join(t.TempDir(), "target")
		symlink(t, target, filepath.Join(l.state, "clipboard.lock"))
		if _, err := acquireLock(context.Background(), l.server.clipboardLockPath()); err == nil || !strings.Contains(err.Error(), "symlink") {
			t.Errorf("lock: %v", err)
		}
		if exists(target) {
			t.Error("created the file the symlink points at")
		}
	})
}

// A directory on the way that its group or others can reach, or that is not a directory, is refused.
func TestPrivateDirectoriesRefuseOpenModes(t *testing.T) {
	for _, mode := range []os.FileMode{0o755, 0o777, 0o750, 0o701, 0o500} {
		t.Run(mode.String(), func(t *testing.T) {
			l := newStateLayout(t)
			mkdir(t, filepath.Join(l.runtime, "hyprmasq"), 0o700)
			mkdir(t, l.state, mode)
			t.Cleanup(func() { os.Chmod(l.state, 0o700) })
			if err := l.server.makeShotDir(); err == nil || !strings.Contains(err.Error(), "mode") {
				t.Errorf("shots: %v", err)
			}
			if _, err := acquireLock(context.Background(), l.server.lockPath("1a")); err == nil || !strings.Contains(err.Error(), "mode") {
				t.Errorf("lock: %v", err)
			}
		})
	}
	t.Run("file", func(t *testing.T) {
		l := newStateLayout(t)
		if err := os.WriteFile(filepath.Join(l.runtime, "hyprmasq"), nil, 0o600); err != nil {
			t.Fatal(err)
		}
		if err := l.server.makeShotDir(); err == nil {
			t.Error("accepted a file")
		}
	})
}

// A directory or file of another user is refused. Tests cannot make one without root, so this checks the test of its
// owner.
func TestOwnPrivateRefusesOtherUsers(t *testing.T) {
	st := syscall.Stat_t{Uid: uint32(os.Getuid()), Mode: syscall.S_IFDIR | 0o700}
	if err := ownPrivate(&st, 0o700); err != nil {
		t.Errorf("own: %v", err)
	}
	st.Uid++
	if err := ownPrivate(&st, 0o700); err == nil || !strings.Contains(err.Error(), "owned by") {
		t.Errorf("other user: %v", err)
	}
}

// A lock file that is not a regular file, or that its group or others can read, is refused.
func TestLockFilesMustBePrivate(t *testing.T) {
	cases := map[string]func(t *testing.T, path string){
		"readable": func(t *testing.T, path string) {
			if err := os.WriteFile(path, nil, 0o644); err != nil {
				t.Fatal(err)
			}
			os.Chmod(path, 0o644)
		},
		"directory": func(t *testing.T, path string) { mkdir(t, path, 0o700) },
		"fifo": func(t *testing.T, path string) {
			if err := syscall.Mkfifo(path, 0o600); err != nil {
				t.Fatal(err)
			}
		},
	}
	for name, make := range cases {
		t.Run(name, func(t *testing.T) {
			l := newStateLayout(t)
			if err := l.server.makeShotDir(); err != nil {
				t.Fatal(err)
			}
			lock := l.server.sendLockPath(4242, false)
			make(t, lock.String())
			if _, err := acquireLock(context.Background(), lock); err == nil {
				t.Error("took the lock")
			}
		})
	}
}

// Stale shot directories go: those of processes that no longer run, when they are private directories. A symlink, a
// directory with an open mode and the directories of running processes stay, and nothing goes through a symlinked mcp
// directory.
func TestRemoveStaleShots(t *testing.T) {
	deadPid := func() string {
		exited := exec.Command("true")
		if err := exited.Run(); err != nil {
			t.Fatal(err)
		}
		return strconv.Itoa(exited.Process.Pid)
	}
	l := newStateLayout(t)
	if err := l.server.makeShotDir(); err != nil {
		t.Fatal(err)
	}
	mcpDir := filepath.Dir(l.server.shotDir)
	dead, open, linked := filepath.Join(mcpDir, deadPid()), filepath.Join(mcpDir, deadPid()), filepath.Join(mcpDir, deadPid())
	alive, other := filepath.Join(mcpDir, "1"), filepath.Join(mcpDir, "other")
	for _, dir := range []string{dead, alive, other} {
		mkdir(t, dir, 0o700)
	}
	if err := os.WriteFile(filepath.Join(dead, "s1.png"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	mkdir(t, open, 0o755)
	target := t.TempDir()
	if err := os.WriteFile(filepath.Join(target, "keep"), nil, 0o600); err != nil {
		t.Fatal(err)
	}
	symlink(t, target, linked)

	l.server.RemoveStaleShots()
	for dir, kept := range map[string]bool{l.server.shotDir: true, dead: false, open: true, linked: true, alive: true, other: true, filepath.Join(target, "keep"): true} {
		if exists(dir) != kept {
			t.Errorf("%s: kept %v", dir, !kept)
		}
	}
}

// RemoveStaleShots reads nothing through a symlink at the mcp directory.
func TestRemoveStaleShotsRefusesSymlinkedDirectory(t *testing.T) {
	l := newStateLayout(t)
	mkdir(t, filepath.Join(l.runtime, "hyprmasq"), 0o700)
	mkdir(t, l.state, 0o700)
	target := t.TempDir()
	symlink(t, target, filepath.Join(l.state, "mcp"))
	exited := exec.Command("true")
	if err := exited.Run(); err != nil {
		t.Fatal(err)
	}
	dead := filepath.Join(target, strconv.Itoa(exited.Process.Pid))
	mkdir(t, dead, 0o700)

	l.server.RemoveStaleShots()
	if !exists(dead) {
		t.Error("removed a directory through the symlink")
	}
}

// A StateDir outside the runtime directory is refused rather than reached by a path that may hold a symlink.
func TestPrivatePathOutsideBase(t *testing.T) {
	base := t.TempDir()
	if _, err := newPrivatePath(filepath.Join(base, "a"), filepath.Join(base, "b")).open(true); err == nil {
		t.Error("opened a directory outside the base")
	}
	if exists(filepath.Join(base, "b")) {
		t.Error("created a directory outside the base")
	}
}
