// Package config reads the configuration file of hyprmasq-mcp, whose keys are named as its command-line options.
package config

import (
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"

	"github.com/BurntSushi/toml"
)

// The values of structured-content.
const (
	StructuredAuto   = "auto"
	StructuredAlways = "always"
	StructuredNever  = "never"
)

// DefaultMaxImageSize is the default of max-image-size. In practice runs, agents clicked as accurately in shots of 1280
// pixels as in larger ones, which Claude Code and Codex shrank to about 2000 pixels anyway, and Claude cost about 40%
// less. Zoom shots still show small areas in full detail.
const DefaultMaxImageSize = 1280

// DefaultMinZoomSize is the default of min-zoom-size. A vision model places points coarsely in a small image, whose
// encoder works in patches many pixels wide: in a benchmark of tracing a thin line in a photo, Opus 5.5 passed 9 of 12
// runs with zooms enlarged to it, and 1 of 6 without.
const DefaultMinZoomSize = 768

// Config holds the settings of hyprmasq-mcp. The top-level keys are also command-line options of the same names.
type Config struct {
	// The hyprmasq-input binary, or "" for the one on PATH.
	Input             string `toml:"input"`
	MaxImageSize      int    `toml:"max-image-size"`
	MinZoomSize       int    `toml:"min-zoom-size"`
	LeaseMs           int    `toml:"lease-ms"`
	NewWindowGraceMs  int    `toml:"new-window-grace-ms"`
	StructuredContent string `toml:"structured-content"`
	Record            Record `toml:"record"`
}

// Record holds the settings of recordings, which have no command-line options.
type Record struct {
	// The directory of the videos. A leading ~ and environment variables in it are expanded when a recording starts.
	Dir string `toml:"dir"`
	// Also write a GIF of the steps next to each video.
	GIF bool `toml:"gif"`
	// The most pixels the GIF has across.
	GIFWidth int `toml:"gif-width"`
	// The most frames per second that video mode records.
	FPS        int `toml:"fps"`
	MaxSteps   int `toml:"max-steps"`
	MaxMinutes int `toml:"max-minutes"`
	// The ffmpeg binary, a name on PATH or a path.
	FFmpeg string `toml:"ffmpeg"`
}

// Defaults returns the settings without a configuration file or options.
func Defaults() Config {
	return Config{
		MaxImageSize:      DefaultMaxImageSize,
		MinZoomSize:       DefaultMinZoomSize,
		LeaseMs:           60000,
		NewWindowGraceMs:  500,
		StructuredContent: StructuredAuto,
		Record: Record{
			Dir:        "~/Videos/hyprmasq",
			GIFWidth:   800,
			FPS:        10,
			MaxSteps:   500,
			MaxMinutes: 10,
			FFmpeg:     "ffmpeg",
		},
	}
}

// Name is the file's name in each configuration directory.
const Name = "hyprmasq/mcp.toml"

// Find returns the configuration file: explicit when it is set, which must exist, or else the first file that exists of
// $XDG_CONFIG_HOME/hyprmasq/mcp.toml and hyprmasq/mcp.toml in each directory of $XDG_CONFIG_DIRS, or "" for none.
func Find(explicit string, getenv func(string) string) (string, error) {
	if explicit != "" {
		info, err := os.Stat(explicit)
		if err != nil {
			return "", err
		}
		if info.IsDir() {
			return "", fmt.Errorf("%s is a directory", explicit)
		}
		return explicit, nil
	}
	for _, dir := range searchDirs(getenv) {
		path := filepath.Join(dir, Name)
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			return path, nil
		}
	}
	return "", nil
}

// searchDirs returns the configuration directories in the order of the XDG Base Directory Specification, which ignores
// relative paths.
func searchDirs(getenv func(string) string) []string {
	var dirs []string
	home := getenv("XDG_CONFIG_HOME")
	if !filepath.IsAbs(home) {
		home = ""
		if h := getenv("HOME"); filepath.IsAbs(h) {
			home = filepath.Join(h, ".config")
		}
	}
	if home != "" {
		dirs = append(dirs, home)
	}
	system := getenv("XDG_CONFIG_DIRS")
	if system == "" {
		system = "/etc/xdg"
	}
	for _, dir := range strings.Split(system, ":") {
		if filepath.IsAbs(dir) {
			dirs = append(dirs, dir)
		}
	}
	return dirs
}

// Load reads the file into c. Keys that the file leaves out keep their values in c, and a key that c does not have is
// an error, so that a misspelled key does not go unnoticed. The decoder also takes a key in other case for a field,
// so keys are compared with the exact names.
func Load(path string, c *Config) error {
	md, err := toml.DecodeFile(path, c)
	if err != nil {
		return err
	}
	known := keyNames(reflect.TypeOf(*c), "")
	var unknown []string
	for _, key := range md.Keys() {
		if !known[key.String()] {
			unknown = append(unknown, key.String())
		}
	}
	if len(unknown) > 0 {
		return fmt.Errorf("unknown key %s", strings.Join(unknown, ", "))
	}
	return nil
}

// keyNames returns the keys of a struct's toml tags, and of the structs within it as dotted paths after prefix.
func keyNames(t reflect.Type, prefix string) map[string]bool {
	names := map[string]bool{}
	for i := range t.NumField() {
		f := t.Field(i)
		name := prefix + f.Tag.Get("toml")
		names[name] = true
		if f.Type.Kind() == reflect.Struct {
			for sub := range keyNames(f.Type, name+".") {
				names[sub] = true
			}
		}
	}
	return names
}

// A KeyError names the key whose value is not valid.
type KeyError struct {
	Key     string
	Message string
}

func (e *KeyError) Error() string { return e.Key + " " + e.Message }

// Validate checks the values, and returns the first that is not valid as a KeyError.
func (c *Config) Validate() error {
	between := func(key string, v, lo, hi int) error {
		if v < lo || v > hi {
			return &KeyError{key, fmt.Sprintf("must be %d to %d, not %d", lo, hi, v)}
		}
		return nil
	}
	checks := []error{
		between("max-image-size", c.MaxImageSize, 0, 1<<20),
		between("min-zoom-size", c.MinZoomSize, 0, 1<<20),
		between("lease-ms", c.LeaseMs, 1, 600000),
		between("new-window-grace-ms", c.NewWindowGraceMs, 0, 5000),
	}
	switch c.StructuredContent {
	case StructuredAuto, StructuredAlways, StructuredNever:
	default:
		checks = append(checks, &KeyError{"structured-content", fmt.Sprintf("must be auto, always or never, not %q", c.StructuredContent)})
	}
	// ~ and variables are expanded when a recording starts, so that a missing HOME only matters to recordings.
	if d := c.Record.Dir; !filepath.IsAbs(d) && !strings.HasPrefix(d, "~") && !strings.HasPrefix(d, "$") {
		checks = append(checks, &KeyError{"record.dir", fmt.Sprintf("must be an absolute path, or start with ~ or a variable, not %q", d)})
	}
	if c.Record.FFmpeg == "" {
		checks = append(checks, &KeyError{"record.ffmpeg", "must name the ffmpeg binary"})
	}
	checks = append(checks,
		between("record.gif-width", c.Record.GIFWidth, 128, 3840),
		between("record.fps", c.Record.FPS, 1, 30),
		between("record.max-steps", c.Record.MaxSteps, 1, 10000),
		between("record.max-minutes", c.Record.MaxMinutes, 1, 120),
	)
	for _, err := range checks {
		if err != nil {
			return err
		}
	}
	return nil
}
