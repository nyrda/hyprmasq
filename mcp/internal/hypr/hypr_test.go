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
