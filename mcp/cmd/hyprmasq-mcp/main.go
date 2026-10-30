// hyprmasq-mcp is an MCP server over stdio that gives agents computer use on Hyprland through hyprmasq.
package main

import (
	"context"
	"errors"
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
	"github.com/nyrda/hyprmasq/mcp/internal/config"
	"github.com/nyrda/hyprmasq/mcp/internal/record"
	"github.com/nyrda/hyprmasq/mcp/internal/server"
)

func main() {
	log.SetFlags(0)
	log.SetPrefix("hyprmasq-mcp: ")
	defaults := config.Defaults()
	configFile := flag.String("config", "", "read the settings from `FILE` (default: the first of $XDG_CONFIG_HOME/hyprmasq/mcp.toml and hyprmasq/mcp.toml in $XDG_CONFIG_DIRS)")
	input := flag.String("input", "", "the hyprmasq-input binary (default: hyprmasq-input from PATH)")
	maxImage := flag.Int("max-image-size", defaults.MaxImageSize, "fit screenshots within `N` pixels on the long side; 0 keeps the monitor's scale")
	minZoom := flag.Int("min-zoom-size", defaults.MinZoomSize, "enlarge zooms smaller than `N` pixels on the long side by a whole factor, at most 4; 0 leaves them as captured")
	leaseMs := flag.Int("lease-ms", defaults.LeaseMs, "length of the focus lease each act starts or renews, in milliseconds")
	graceMs := flag.Int("new-window-grace-ms", defaults.NewWindowGraceMs, "how long act goes on watching for new windows after its wait, in milliseconds, when it delivered input")
	structured := flag.String("structured-content", defaults.StructuredContent,
		"give results structured content: `auto` (not to clients that show it to the model in place of the text), always or never")
	version := flag.Bool("version", false, "print the version and exit")
	flag.Parse()
	if *version {
		fmt.Println(server.Version)
		return
	}
	if flag.NArg() > 0 {
		flag.Usage()
		os.Exit(2)
	}

	// The configuration file sets what the options do not.
	cfg := defaults
	path, err := config.Find(*configFile, os.Getenv)
	if err != nil {
		log.Printf("the configuration file: %v", err)
		os.Exit(2)
	}
	if path != "" {
		if err := config.Load(path, &cfg); err != nil {
			log.Printf("%s: %v", path, err)
			os.Exit(2)
		}
	}
	set := map[string]bool{}
	flag.Visit(func(f *flag.Flag) { set[f.Name] = true })
	for name, apply := range map[string]func(){
		"input":               func() { cfg.Input = *input },
		"max-image-size":      func() { cfg.MaxImageSize = *maxImage },
		"min-zoom-size":       func() { cfg.MinZoomSize = *minZoom },
		"lease-ms":            func() { cfg.LeaseMs = *leaseMs },
		"new-window-grace-ms": func() { cfg.NewWindowGraceMs = *graceMs },
		"structured-content":  func() { cfg.StructuredContent = *structured },
	} {
		if set[name] {
			apply()
		}
	}
	if err := cfg.Validate(); err != nil {
		var ke *config.KeyError
		if errors.As(err, &ke) && set[ke.Key] {
			log.Printf("-%v", err)
			flag.Usage()
		} else {
			log.Printf("%s: %v", path, err)
		}
		os.Exit(2)
	}

	runtime := os.Getenv("XDG_RUNTIME_DIR")
	instance := os.Getenv("HYPRLAND_INSTANCE_SIGNATURE")
	if runtime == "" || instance == "" {
		log.Fatal("XDG_RUNTIME_DIR and HYPRLAND_INSTANCE_SIGNATURE must name the Hyprland to drive")
	}
	stateDir := filepath.Join(runtime, "hyprmasq", instanceDir(instance))
	// The plugin splits a request into words at whitespace, and a screenshot request names a file in the directory.
	if strings.ContainsAny(stateDir, " \t\n\v\f\r") {
		log.Fatalf("the directory for screenshots, %q, contains whitespace, which a request to the plugin cannot carry; run Hyprland with an XDG_RUNTIME_DIR without it", stateDir)
	}
	if cfg.Input == "" {
		path, err := exec.LookPath("hyprmasq-input")
		if err != nil {
			log.Fatal("hyprmasq-input is not on PATH; pass --input")
		}
		cfg.Input = path
	}

	s := server.New(server.Config{
		HyprlandSocket:    filepath.Join(runtime, "hypr", instance, ".socket.sock"),
		StateDir:          stateDir,
		Input:             cfg.Input,
		MaxImageSize:      cfg.MaxImageSize,
		MinZoomSize:       cfg.MinZoomSize,
		LeaseMs:           cfg.LeaseMs,
		NewWindowGrace:    time.Duration(cfg.NewWindowGraceMs) * time.Millisecond,
		StructuredContent: cfg.StructuredContent,
		Record: record.Options{
			Dir:      cfg.Record.Dir,
			GIF:      cfg.Record.GIF,
			GIFWidth: cfg.Record.GIFWidth,
			FPS:      cfg.Record.FPS,
			MaxSteps: cfg.Record.MaxSteps,
			MaxTime:  time.Duration(cfg.Record.MaxMinutes) * time.Minute,
			FFmpeg:   cfg.Record.FFmpeg,
		},
	})

	s.RemoveStaleShots()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM, syscall.SIGHUP)
	// The SDK lets running calls finish before it closes the session, so a signal cancels them first.
	stopCalls := context.AfterFunc(ctx, s.CancelCalls)
	err = s.NewMCPServer().Run(ctx, &mcp.StdioTransport{})
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
