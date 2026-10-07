package main

import (
	"bytes"
	"errors"
	"os"
	"os/exec"
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
