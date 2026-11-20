package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/nyrda/hyprmasq/mcp/internal/config"
	"github.com/nyrda/hyprmasq/mcp/internal/hypr"
	"github.com/nyrda/hyprmasq/mcp/internal/record"
)

// checkTimeout bounds each request to Hyprland and the run of ffmpeg, so a check never hangs on a stuck compositor.
const checkTimeout = 5 * time.Second

// The results of a check: a failed one keeps the server from working, a warning only from recording, and a skipped one
// could not run because an earlier one failed.
const (
	checkOK   = "ok"
	checkFail = "FAIL"
	checkWarn = "warn"
	checkSkip = "skip"
)

// hyprlandPaths returns Hyprland's request socket and the directory of the plugin's sockets for the Hyprland that
// the environment names.
func hyprlandPaths(runtime, instance string) (socket, stateDir string, err error) {
	if runtime == "" || instance == "" {
		return "", "", errors.New("XDG_RUNTIME_DIR and HYPRLAND_INSTANCE_SIGNATURE must name the Hyprland to drive")
	}
	stateDir = filepath.Join(runtime, "hyprmasq", instanceDir(instance))
	// The plugin splits a request into words at whitespace, and a screenshot request names a file in the directory.
	if strings.ContainsAny(stateDir, " \t\n\v\f\r") {
		return "", "", fmt.Errorf("the directory for screenshots, %q, contains whitespace, which a request to the plugin cannot carry; run Hyprland with an XDG_RUNTIME_DIR without it", stateDir)
	}
	return filepath.Join(runtime, "hypr", instance, ".socket.sock"), stateDir, nil
}

// findInput returns the hyprmasq-input binary: the one that input names, or the one on PATH when input is "".
func findInput(input string) (string, error) {
	if input == "" {
		path, err := exec.LookPath("hyprmasq-input")
		if err != nil {
			return "", errors.New("hyprmasq-input is not on PATH; pass --input")
		}
		return path, nil
	}
	path, err := exec.LookPath(input)
	if err != nil {
		var ee *exec.Error
		if errors.As(err, &ee) {
			err = ee.Err
		}
		return "", fmt.Errorf("%q cannot be run: %v; pass --input", input, err)
	}
	return path, nil
}

// checker writes a line for each check, and counts the failures and warnings.
type checker struct {
	w        io.Writer
	failed   int
	warnings int
}

func (c *checker) report(result, name, detail string) {
	switch result {
	case checkFail:
		c.failed++
	case checkWarn:
		c.warnings++
	}
	fmt.Fprintf(c.w, "%-4s %s: %s\n", result, name, detail)
}

// runCheck checks what the server needs, without starting it: the settings, which have been loaded from configPath
// (or "" for none) into cfg, the Hyprland that the environment names, the plugin in it and its hooks, hyprmasq-input,
// and ffmpeg for recordings. It writes a line for each check to w, and returns the exit status: 0 when the server can
// work, also without recordings, and 1 when it cannot.
func runCheck(w io.Writer, configPath string, cfg config.Config, getenv func(string) string) int {
	c := &checker{w: w}
	if configPath == "" {
		c.report(checkOK, "configuration", "no configuration file, the defaults and options")
	} else {
		c.report(checkOK, "configuration", configPath)
	}

	socket, _, err := hyprlandPaths(getenv("XDG_RUNTIME_DIR"), getenv("HYPRLAND_INSTANCE_SIGNATURE"))
	if err != nil {
		c.report(checkFail, "environment", err.Error())
		c.report(checkSkip, "plugin", "needs the environment")
		c.report(checkSkip, "hooks", "needs the plugin")
	} else {
		c.report(checkOK, "environment", "Hyprland "+getenv("HYPRLAND_INSTANCE_SIGNATURE")+" under "+getenv("XDG_RUNTIME_DIR"))
		checkHyprland(c, &hypr.Client{Socket: socket})
	}

	if path, err := findInput(cfg.Input); err != nil {
		c.report(checkFail, "hyprmasq-input", err.Error())
	} else {
		c.report(checkOK, "hyprmasq-input", path)
	}

	ctx, cancel := context.WithTimeout(context.Background(), checkTimeout)
	path, err := record.CheckFFmpeg(ctx, cfg.Record.FFmpeg)
	cancel()
	if err != nil {
		c.report(checkWarn, "ffmpeg", err.Error()+"; everything but recordings works without it")
	} else {
		c.report(checkOK, "ffmpeg", path+" with libx264")
	}

	switch {
	case c.failed > 0:
		fmt.Fprintf(w, "hyprmasq-mcp is not ready: %s failed\n", plural(c.failed, "check"))
		return 1
	case c.warnings > 0:
		fmt.Fprintf(w, "hyprmasq-mcp is ready, with %s\n", plural(c.warnings, "warning"))
	default:
		fmt.Fprintln(w, "hyprmasq-mcp is ready")
	}
	return 0
}

// checkHyprland checks that the plugin is loaded in the Hyprland at the client's socket, speaks this server's
// interface, and that the running Hyprland takes all of its hooks.
func checkHyprland(c *checker, client *hypr.Client) {
	ctx, cancel := context.WithTimeout(context.Background(), checkTimeout)
	v, err := client.CheckVersion(ctx)
	cancel()
	if err != nil {
		detail := err.Error()
		var ce *hypr.ConnectError
		if errors.As(err, &ce) {
			detail += "; HYPRLAND_INSTANCE_SIGNATURE may name another Hyprland"
		}
		c.report(checkFail, "plugin", detail)
		c.report(checkSkip, "hooks", "needs the plugin")
		return
	}
	c.report(checkOK, "plugin", fmt.Sprintf("hyprmasq %s, interface %d", v.Version, v.Interface))

	ctx, cancel = context.WithTimeout(context.Background(), checkTimeout)
	h, err := client.HooksCheck(ctx)
	cancel()
	if err != nil {
		c.report(checkFail, "hooks", "hyprctl hyprmasq hooks check failed: "+err.Error())
		return
	}
	var refused []string
	for _, g := range h.Groups {
		if g.State == "refused" {
			why := "refused"
			if g.Refused != nil && g.Refused.Message != "" {
				why = g.Refused.Message
			}
			refused = append(refused, g.Name+": "+why)
		}
	}
	if !h.All && len(refused) == 0 {
		c.report(checkFail, "hooks", "the plugin reports that not every group of hooks installs, and names none")
		return
	}
	if len(refused) > 0 {
		c.report(checkFail, "hooks", "Hyprland refuses "+plural(len(refused), "group")+" of hooks; "+strings.Join(refused, "; "))
		return
	}
	c.report(checkOK, "hooks", "all "+plural(len(h.Groups), "group")+" of hooks install")
}

func plural(n int, word string) string {
	if n == 1 {
		return "1 " + word
	}
	return fmt.Sprintf("%d %ss", n, word)
}
