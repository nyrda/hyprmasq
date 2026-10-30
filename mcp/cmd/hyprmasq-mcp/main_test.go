package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// The test binary doubles as the server, so the tests run main as a process of its own.
func TestMain(m *testing.M) {
	if os.Getenv("HYPRMASQ_MCP_TEST_MAIN") != "" {
		os.Args = append(os.Args[:1], strings.Fields(os.Getenv("HYPRMASQ_MCP_TEST_MAIN"))...)
		main()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

// serve runs the server with stdin as its input, on a Hyprland that does not exist, and returns its exit status and
// what it printed on stderr.
func serve(t *testing.T, stdin string) (int, string) {
	t.Helper()
	cmd := exec.Command(os.Args[0])
	// Only the variables it needs, so it never reaches the Hyprland that runs the tests.
	cmd.Env = []string{"HYPRMASQ_MCP_TEST_MAIN=--input /bin/true", "XDG_RUNTIME_DIR=" + t.TempDir(), "HYPRLAND_INSTANCE_SIGNATURE=hyprmasq-test-none"}
	cmd.Stdin = strings.NewReader(stdin)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	err := cmd.Run()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return exit.ExitCode(), stderr.String()
	}
	if err != nil {
		t.Fatal(err)
	}
	return 0, stderr.String()
}

func TestExitStatus(t *testing.T) {
	if status, stderr := serve(t, ""); status != 0 || stderr != "" {
		t.Errorf("a client that closes stdin: status %d, %q", status, stderr)
	}
	// A transport error ends the session too, and is reported.
	if status, stderr := serve(t, "this is not JSON\n"); status != 1 || !strings.HasPrefix(stderr, "hyprmasq-mcp: ") {
		t.Errorf("an unreadable message: status %d, %q", status, stderr)
	}
}

// The plugin names the directory the same way, in src/Eis.cpp.
func TestInstanceDir(t *testing.T) {
	for signature, want := range map[string]string{
		"efb50993780079460b0cbed1363e2166a2de1d9f_1791199230_138021124": "1791199230_138021124",
		// Hyprland built without a commit hash.
		"_1791199230_138021124": "1791199230_138021124",
		"hyprmasq-test-none":    "hyprmasq-test-none",
		"trailing_":             "trailing_",
	} {
		if got := instanceDir(signature); got != want {
			t.Errorf("instanceDir(%q) = %q, want %q", signature, got, want)
		}
	}
}

// A screenshot request names its file in words split at whitespace, so a runtime directory with a space is refused.
func TestRuntimeDirWithWhitespace(t *testing.T) {
	cmd := exec.Command(os.Args[0])
	cmd.Env = []string{"HYPRMASQ_MCP_TEST_MAIN=--input /bin/true", "XDG_RUNTIME_DIR=" + t.TempDir() + "/run time", "HYPRLAND_INSTANCE_SIGNATURE=hyprmasq-test-none"}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	var exit *exec.ExitError
	if err := cmd.Run(); !errors.As(err, &exit) || exit.ExitCode() != 1 || !strings.Contains(stderr.String(), "/run time/hyprmasq/hyprmasq-test-none\", contains whitespace") {
		t.Errorf("%v, %q", err, stderr.String())
	}
}

func TestMaxImageSizeDefault(t *testing.T) {
	cmd := exec.Command(os.Args[0])
	cmd.Env = []string{"HYPRMASQ_MCP_TEST_MAIN=-h"}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	cmd.Run()
	if !strings.Contains(stderr.String(), "-max-image-size N\n    \tfit screenshots within N pixels on the long side; 0 keeps the monitor's scale (default 1280)") {
		t.Errorf("usage %q", stderr.String())
	}
}

func TestStructuredContentOption(t *testing.T) {
	cmd := exec.Command(os.Args[0])
	cmd.Env = []string{"HYPRMASQ_MCP_TEST_MAIN=--structured-content sometimes"}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	var exit *exec.ExitError
	if err := cmd.Run(); !errors.As(err, &exit) || exit.ExitCode() != 2 || !strings.Contains(stderr.String(), "-structured-content auto") {
		t.Errorf("an unknown value: %v, %q", err, stderr.String())
	}
}

// The configuration file is the first that exists of $XDG_CONFIG_HOME/hyprmasq/mcp.toml and hyprmasq/mcp.toml in each
// directory of $XDG_CONFIG_DIRS, or the one --config names. Options beat it, and a key or value that is not valid ends
// the server with status 2 and a message that names the file and the key.
func TestConfigurationFile(t *testing.T) {
	write := func(dir, content string) string {
		path := filepath.Join(dir, "hyprmasq", "mcp.toml")
		if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
		return path
	}
	for _, c := range []struct {
		name          string
		home, system  string // the files in XDG_CONFIG_HOME and in the second directory of XDG_CONFIG_DIRS, or ""
		options       string
		status        int
		stderr        string // what stderr contains, with HOME and SYSTEM for the paths of the files
		stderrIsEmpty bool
	}{
		{name: "valid", home: "max-image-size = 0\n[record]\ndir = \"~/clips\"\ngif = true\nfps = 30\n", stderrIsEmpty: true},
		{name: "unknown key", home: "[record]\ngfi = true\n", status: 2, stderr: "HOME: unknown key record.gfi"},
		// The TOML decoder would take it for lease-ms.
		{name: "key in other case", home: "LEASE-MS = 1000\n", status: 2, stderr: "HOME: unknown key LEASE-MS"},
		{name: "wrong type", home: "lease-ms = \"long\"\n", status: 2, stderr: "HOME: toml: line 1 (last key \"lease-ms\")"},
		{name: "out of range", home: "[record]\nfps = 60\n", status: 2, stderr: "HOME: record.fps must be 1 to 30, not 60"},
		{name: "relative directory", home: "[record]\ndir = \"clips\"\n", status: 2, stderr: `HOME: record.dir must be an absolute path, or start with ~ or a variable, not "clips"`},
		{name: "system directory", system: "structured-content = \"sometimes\"\n", status: 2, stderr: `SYSTEM: structured-content must be auto, always or never, not "sometimes"`},
		// Only the first file counts, so the second one's value is never read.
		{name: "first file", home: "lease-ms = 5000\n", system: "lease-ms = 0\n", stderrIsEmpty: true},
		{name: "option beats file", home: "structured-content = \"sometimes\"\n", options: "--structured-content never", stderrIsEmpty: true},
		{name: "option checked", home: "lease-ms = 5000\n", options: "--lease-ms 0", status: 2, stderr: "-lease-ms must be 1 to 600000, not 0\n"},
		{name: "missing --config", options: "--config /nonexistent/mcp.toml", status: 2, stderr: "the configuration file: stat /nonexistent/mcp.toml: no such file or directory"},
	} {
		t.Run(c.name, func(t *testing.T) {
			home, first, second := t.TempDir(), t.TempDir(), t.TempDir()
			homePath, systemPath := "", ""
			if c.home != "" {
				homePath = write(home, c.home)
			}
			if c.system != "" {
				systemPath = write(second, c.system)
			}
			cmd := exec.Command(os.Args[0])
			cmd.Env = []string{"HYPRMASQ_MCP_TEST_MAIN=--input /bin/true " + c.options, "XDG_RUNTIME_DIR=" + t.TempDir(), "HYPRLAND_INSTANCE_SIGNATURE=hyprmasq-test-none",
				"HOME=" + t.TempDir(), "XDG_CONFIG_HOME=" + home, "XDG_CONFIG_DIRS=" + first + ":" + second}
			cmd.Stdin = strings.NewReader("")
			var stderr bytes.Buffer
			cmd.Stderr = &stderr
			status := 0
			if err := cmd.Run(); err != nil {
				var exit *exec.ExitError
				if !errors.As(err, &exit) {
					t.Fatal(err)
				}
				status = exit.ExitCode()
			}
			want := strings.NewReplacer("HOME", homePath, "SYSTEM", systemPath).Replace(c.stderr)
			if status != c.status || (c.stderrIsEmpty && stderr.Len() > 0) || !strings.Contains(stderr.String(), want) {
				t.Errorf("status %d, %q; want status %d, %q", status, stderr.String(), c.status, want)
			}
		})
	}
}
