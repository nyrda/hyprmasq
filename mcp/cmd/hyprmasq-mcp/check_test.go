package main

import (
	"bytes"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	"github.com/nyrda/hyprmasq/mcp/internal/config"
)

const (
	healthyVersion = `{"ok":true,"version":"0.4.0","interface":5}`
	healthyHooks   = `{"ok":true,"all":true,"groups":[{"name":"input","state":"installed","refused":null},{"name":"render","state":"installs","refused":null}]}`
)

// fakeHyprland answers requests on a Hyprland request socket under a new XDG_RUNTIME_DIR with the replies, by request,
// and "unknown request" to any other, as Hyprland does without the plugin. It returns the runtime directory and the
// directory of the socket. The directory is short, since a socket path must fit in sun_path.
func fakeHyprland(t *testing.T, instance string, replies map[string]string) (runtime, socketDir string) {
	t.Helper()
	runtime, err := os.MkdirTemp("", "hm")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(runtime) })
	socketDir = filepath.Join(runtime, "hypr", instance)
	if err := os.MkdirAll(socketDir, 0o700); err != nil {
		t.Fatal(err)
	}
	if replies == nil {
		return runtime, socketDir
	}
	l, err := net.Listen("unix", filepath.Join(socketDir, ".socket.sock"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	go func() {
		for {
			conn, err := l.Accept()
			if err != nil {
				return
			}
			buf := make([]byte, 1023)
			n, _ := conn.Read(buf)
			reply, ok := replies[strings.TrimSpace(string(buf[:n]))]
			if !ok {
				reply = "unknown request"
			}
			conn.Write([]byte(reply))
			conn.Close()
		}
	}()
	return runtime, socketDir
}

// script writes an executable shell script and returns its path.
func script(t *testing.T, name, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte("#!/bin/sh\n"+body), 0o755); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestCheck(t *testing.T) {
	ffmpeg := script(t, "ffmpeg", "echo ' V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC'\n")
	input := script(t, "hyprmasq-input", "")
	refusal := "this hyprmasq package has no plugin for the running Hyprland build 11cbc6c577b8 yet."
	for _, c := range []struct {
		name    string
		replies map[string]string // nil for no socket
		refused string            // the hyprmasq-refused note next to the socket, or ""
		env     map[string]string // overrides of the environment
		input   string
		ffmpeg  string
		status  int
		want    []string // lines of the output
	}{
		{name: "healthy", replies: map[string]string{"j/hyprmasq version": healthyVersion, "j/hyprmasq hooks check": healthyHooks},
			want: []string{"ok   configuration: no configuration file, the defaults and options\n", "ok   plugin: hyprmasq 0.4.0, interface 5\n",
				"ok   hooks: all 2 groups of hooks install\n", "ok   hyprmasq-input: " + input + "\n", "with libx264\n", "hyprmasq-mcp is ready\n"}},
		{name: "no Hyprland", status: 1,
			want: []string{"FAIL plugin: cannot connect to Hyprland at ", "; HYPRLAND_INSTANCE_SIGNATURE may name another Hyprland\n",
				"skip hooks: needs the plugin\n", "hyprmasq-mcp is not ready: 1 check failed\n"}},
		{name: "plugin missing", replies: map[string]string{}, status: 1,
			want: []string{"FAIL plugin: the hyprmasq plugin is not loaded in Hyprland\n", "skip hooks: needs the plugin\n"}},
		{name: "plugin refused", replies: map[string]string{}, refused: refusal + "\n", status: 1,
			want: []string{"FAIL plugin: the hyprmasq plugin is not loaded in Hyprland: it did not load, as " + refusal + "\n"}},
		{name: "interface mismatch", replies: map[string]string{"j/hyprmasq version": `{"ok":true,"version":"0.3.0","interface":4}`}, status: 1,
			want: []string{"FAIL plugin: the hyprmasq plugin 0.3.0 has interface 4, but this server needs interface 5\n", "skip hooks: needs the plugin\n"}},
		{name: "plugin without version", replies: map[string]string{"j/hyprmasq version": `{"ok":false,"error":"unknown subcommand"}`}, status: 1,
			want: []string{"FAIL plugin: the hyprmasq plugin has no version command, so it is older than this server\n"}},
		{name: "hook refused", status: 1, replies: map[string]string{"j/hyprmasq version": healthyVersion,
			"j/hyprmasq hooks check": `{"ok":true,"all":false,"groups":[{"name":"input","state":"installed","refused":null},` +
				`{"name":"drag","state":"refused","refused":{"code":"hook_held","message":"another plugin hooks CWLDataDeviceProtocol::initiateDrag"}}]}`},
			want: []string{"FAIL hooks: Hyprland refuses 1 group of hooks; drag: another plugin hooks CWLDataDeviceProtocol::initiateDrag\n",
				"hyprmasq-mcp is not ready: 1 check failed\n"}},
		{name: "missing environment", env: map[string]string{"HYPRLAND_INSTANCE_SIGNATURE": ""}, status: 1,
			want: []string{"FAIL environment: XDG_RUNTIME_DIR and HYPRLAND_INSTANCE_SIGNATURE must name the Hyprland to drive\n",
				"skip plugin: needs the environment\n", "skip hooks: needs the plugin\n"}},
		{name: "runtime directory with whitespace", env: map[string]string{"XDG_RUNTIME_DIR": "/run/user/1000 x"}, status: 1,
			want: []string{"FAIL environment: the directory for screenshots, \"/run/user/1000 x/hyprmasq/hm-test\", contains whitespace"}},
		{name: "missing hyprmasq-input", replies: map[string]string{"j/hyprmasq version": healthyVersion, "j/hyprmasq hooks check": healthyHooks},
			input: "/nonexistent/hyprmasq-input", status: 1,
			want: []string{"FAIL hyprmasq-input: \"/nonexistent/hyprmasq-input\" cannot be run: ", "; pass --input\n", "hyprmasq-mcp is not ready: 1 check failed\n"}},
		// Recordings are optional, so ffmpeg only warns.
		{name: "missing ffmpeg", replies: map[string]string{"j/hyprmasq version": healthyVersion, "j/hyprmasq hooks check": healthyHooks},
			ffmpeg: "/nonexistent/ffmpeg",
			want: []string{"warn ffmpeg: recording needs ffmpeg, and \"/nonexistent/ffmpeg\" cannot be run: ", "; everything but recordings works without it\n",
				"hyprmasq-mcp is ready, with 1 warning\n"}},
	} {
		t.Run(c.name, func(t *testing.T) {
			runtime, socketDir := fakeHyprland(t, "hm-test", c.replies)
			if c.refused != "" {
				if err := os.WriteFile(filepath.Join(socketDir, "hyprmasq-refused"), []byte(c.refused), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			env := map[string]string{"XDG_RUNTIME_DIR": runtime, "HYPRLAND_INSTANCE_SIGNATURE": "hm-test"}
			for k, v := range c.env {
				env[k] = v
			}
			cfg := config.Defaults()
			cfg.Input = input
			if c.input != "" {
				cfg.Input = c.input
			}
			cfg.Record.FFmpeg = ffmpeg
			if c.ffmpeg != "" {
				cfg.Record.FFmpeg = c.ffmpeg
			}
			var out bytes.Buffer
			status := runCheck(&out, "", cfg, func(k string) string { return env[k] })
			for _, want := range c.want {
				if !strings.Contains(out.String(), want) {
					t.Errorf("the output lacks %q:\n%s", want, out.String())
				}
			}
			if status != c.status {
				t.Errorf("status %d, want %d:\n%s", status, c.status, out.String())
			}
		})
	}
}

// --check runs instead of the server: it reads no stdin, prints the checks on stdout with the file it loaded, and an
// option or file that is not valid ends it with status 2 before any check, as it ends the server.
func TestCheckOption(t *testing.T) {
	runtime, _ := fakeHyprland(t, "hm-test", map[string]string{"j/hyprmasq version": healthyVersion, "j/hyprmasq hooks check": healthyHooks})
	home := t.TempDir()
	configPath := filepath.Join(home, "hyprmasq", "mcp.toml")
	if err := os.MkdirAll(filepath.Dir(configPath), 0o700); err != nil {
		t.Fatal(err)
	}
	ffmpeg := script(t, "ffmpeg", "echo ' V....D libx264 libx264 H.264'\n")
	input := script(t, "hyprmasq-input", "")
	if err := os.WriteFile(configPath, []byte("input = \""+input+"\"\n[record]\nffmpeg = \""+ffmpeg+"\"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	run := func(options string) (int, string, string) {
		cmd := exec.Command(os.Args[0])
		cmd.Env = []string{"HYPRMASQ_MCP_TEST_MAIN=--check " + options, "XDG_RUNTIME_DIR=" + runtime, "HYPRLAND_INSTANCE_SIGNATURE=hm-test",
			"HOME=" + t.TempDir(), "XDG_CONFIG_HOME=" + home, "XDG_CONFIG_DIRS=/nonexistent"}
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		err := cmd.Run()
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			return exit.ExitCode(), stdout.String(), stderr.String()
		}
		if err != nil {
			t.Fatal(err)
		}
		return 0, stdout.String(), stderr.String()
	}
	if status, stdout, stderr := run(""); status != 0 || stderr != "" || !strings.Contains(stdout, "ok   configuration: "+configPath+"\n") ||
		!strings.HasSuffix(stdout, "hyprmasq-mcp is ready\n") {
		t.Errorf("healthy: status %d, stdout %q, stderr %q", status, stdout, stderr)
	}
	if status, stdout, _ := run("--input /nonexistent/hyprmasq-input"); status != 1 || !strings.Contains(stdout, "FAIL hyprmasq-input: \"/nonexistent/hyprmasq-input\"") {
		t.Errorf("--input beats the file: status %d, stdout %q", status, stdout)
	}
	if status, stdout, stderr := run("--lease-ms 0"); status != 2 || stdout != "" || !strings.Contains(stderr, "-lease-ms must be 1 to 600000, not 0") {
		t.Errorf("an invalid option: status %d, stdout %q, stderr %q", status, stdout, stderr)
	}
}
