// hyprmasq-mcp is an MCP server over stdio that gives agents computer use on Hyprland through hyprmasq.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/nyrda/hyprmasq/mcp/internal/server"
)

func main() {
	log.SetFlags(0)
	log.SetPrefix("hyprmasq-mcp: ")
	input := flag.String("input", "", "the hyprmasq-input binary (default: hyprmasq-input from PATH)")
	maxImage := flag.Int("max-image-size", server.DefaultMaxImageSize, "fit screenshots within `N` pixels on the long side; 0 keeps the monitor's scale")
	leaseMs := flag.Int("lease-ms", 60000, "length of the focus lease each act starts or renews, in milliseconds")
	graceMs := flag.Int("new-window-grace-ms", 500, "how long act goes on watching for new windows after its wait, in milliseconds, when it delivered input")
	structured := flag.String("structured-content", server.StructuredAuto,
		"give results structured content: `auto` (not to clients that show it to the model in place of the text), always or never")
	version := flag.Bool("version", false, "print the version and exit")
	flag.Parse()
	if *version {
		fmt.Println(server.Version)
		return
	}
	validStructured := *structured == server.StructuredAuto || *structured == server.StructuredAlways || *structured == server.StructuredNever
	if flag.NArg() > 0 || *maxImage < 0 || *leaseMs < 1 || *leaseMs > 600000 || *graceMs < 0 || *graceMs > 5000 || !validStructured {
		flag.Usage()
		os.Exit(2)
	}

	runtime := os.Getenv("XDG_RUNTIME_DIR")
	instance := os.Getenv("HYPRLAND_INSTANCE_SIGNATURE")
	if runtime == "" || instance == "" {
		log.Fatal("XDG_RUNTIME_DIR and HYPRLAND_INSTANCE_SIGNATURE must name the Hyprland to drive")
	}
	if *input == "" {
		path, err := exec.LookPath("hyprmasq-input")
		if err != nil {
			log.Fatal("hyprmasq-input is not on PATH; pass --input")
		}
		*input = path
	}

	s := server.New(server.Config{
		HyprlandSocket:    filepath.Join(runtime, "hypr", instance, ".socket.sock"),
		StateDir:          filepath.Join(runtime, "hyprmasq", instanceDir(instance)),
		Input:             *input,
		MaxImageSize:      *maxImage,
		LeaseMs:           *leaseMs,
		NewWindowGrace:    time.Duration(*graceMs) * time.Millisecond,
		StructuredContent: *structured,
	})

	s.RemoveStaleShots()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	// The SDK lets running calls finish before it closes the session, so a signal cancels them first.
	stopCalls := context.AfterFunc(ctx, s.CancelCalls)
	err := s.NewMCPServer().Run(ctx, &mcp.StdioTransport{})
	// stop cancels ctx, so whether a signal ended the session is read before.
	signalled := ctx.Err() != nil
	stopCalls()
	stop()

	// The session ended: the client disconnected or the server was asked to exit.
	shutdown, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	s.Shutdown(shutdown)
	cancel()
	if err != nil && !signalled {
		log.Fatal(err)
	}
}

// instanceDir names the directory of a Hyprland instance under $XDG_RUNTIME_DIR/hyprmasq as the plugin does: its
// signature without the commit hash in front, which keeps the plugin's socket paths within sun_path.
func instanceDir(signature string) string {
	if _, rest, found := strings.Cut(signature, "_"); found && rest != "" {
		return rest
	}
	return signature
}
