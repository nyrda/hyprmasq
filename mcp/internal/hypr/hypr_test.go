package hypr

import (
	"context"
	"errors"
	"net"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// serveLikeHyprland answers one connection after reading the request as Hyprland does: in chunks of 1023 bytes, until
// the first short read.
func serveLikeHyprland(t *testing.T, l net.Listener, got chan<- string) {
	conn, err := l.Accept()
	if err != nil {
		return
	}
	defer conn.Close()
	var request []byte
	buf := make([]byte, readChunk)
	for {
		n, err := conn.Read(buf)
		if n < 1 || err != nil {
			break
		}
		request = append(request, buf[:n]...)
		if n < readChunk {
			break
		}
	}
	got <- string(request)
	conn.Write([]byte("ok"))
}

func TestRequestNeverFillsTheLastChunk(t *testing.T) {
	socket := filepath.Join(t.TempDir(), ".socket.sock")
	l, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	c := &Client{Socket: socket}
	for _, size := range []int{10, readChunk - 1, readChunk, 3 * readChunk} {
		got := make(chan string, 1)
		go serveLikeHyprland(t, l, got)
		request := strings.Repeat("x", size)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		reply, err := c.Request(ctx, request)
		cancel()
		if err != nil || string(reply) != "ok" {
			t.Fatalf("%d bytes: %q %v", size, reply, err)
		}
		if r := <-got; r != request && r != request+" " {
			t.Errorf("%d bytes arrived as %d", size, len(r))
		}
	}
	if _, err := c.Request(context.Background(), strings.Repeat("x", MaxRequest+1)); err == nil {
		t.Error("an overlong request was sent")
	}
}

func TestRequestWithAnEndedContext(t *testing.T) {
	socket := filepath.Join(t.TempDir(), ".socket.sock")
	l, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	c := &Client{Socket: socket}
	// A context that ended is not a socket that cannot be reached.
	expired, cancel := context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
	defer cancel()
	if _, err := c.Request(expired, "j/clients"); !errors.Is(err, context.DeadlineExceeded) || errors.As(err, new(*ConnectError)) {
		t.Errorf("expired: %v", err)
	}
	cancelled, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := c.Request(cancelled, "j/clients"); !errors.Is(err, context.Canceled) || errors.As(err, new(*ConnectError)) {
		t.Errorf("cancelled: %v", err)
	}
	c.Socket = filepath.Join(t.TempDir(), "missing.sock")
	if _, err := c.Request(context.Background(), "j/clients"); !errors.As(err, new(*ConnectError)) {
		t.Errorf("missing socket: %v", err)
	}
}

// Events reads the lines of the event socket next to the request socket, and ends the connection and the channel when
// its context ends, also while Hyprland writes nothing.
func TestEvents(t *testing.T) {
	dir := t.TempDir()
	c := &Client{Socket: filepath.Join(dir, ".socket.sock")}
	var connectErr *ConnectError
	if _, err := c.Events(context.Background()); !errors.As(err, &connectErr) {
		t.Errorf("without a socket: %v", err)
	}
	l, err := net.Listen("unix", filepath.Join(dir, ".socket2.sock"))
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	hungUp := make(chan struct{})
	go func() {
		conn, err := l.Accept()
		if err != nil {
			return
		}
		defer conn.Close()
		conn.Write([]byte("openwindow>>55d1c0b11230,web,xdg-desktop-portal-gtk,Open Files\nwindowtitlev2>>55d1c0b11230,Open >> Files\n"))
		// The client sends nothing, so the read ends when it hangs up.
		conn.Read(make([]byte, 1))
		close(hungUp)
	}()
	ctx, cancel := context.WithCancel(context.Background())
	events, err := c.Events(ctx)
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []Event{{"openwindow", "55d1c0b11230,web,xdg-desktop-portal-gtk,Open Files"}, {"windowtitlev2", "55d1c0b11230,Open >> Files"}} {
		if got := <-events; got != want {
			t.Errorf("event %q, want %q", got, want)
		}
	}
	cancel()
	select {
	case _, open := <-events:
		if open {
			t.Error("an event after the end")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the channel stayed open")
	}
	select {
	case <-hungUp:
	case <-time.After(5 * time.Second):
		t.Fatal("the connection stayed open")
	}
}

// A request that its context's deadline ends reports the deadline, and not the connection's i/o timeout, which the
// server would take for a failure of Hyprland.
func TestRequestEndedByItsDeadline(t *testing.T) {
	socket := filepath.Join(t.TempDir(), ".socket.sock")
	l, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	done := make(chan struct{})
	defer close(done)
	go func() {
		for {
			conn, err := l.Accept()
			if err != nil {
				return
			}
			// Hyprland is busy: it reads the request and answers nothing.
			go func() {
				defer conn.Close()
				conn.Read(make([]byte, readChunk))
				<-done
			}()
		}
	}()
	c := &Client{Socket: socket}
	for range 50 {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		_, err := c.Request(ctx, "j/hyprmasq state 0x1")
		cancel()
		if !errors.Is(err, context.DeadlineExceeded) {
			t.Fatalf("error %v", err)
		}
	}
}
