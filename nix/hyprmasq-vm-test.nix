{
  system ? "x86_64-linux",
  nixpkgs ? builtins.fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/6dedf69f94d03cbe7bdde106f2d4c23ae2a853bf.tar.gz";
    sha256 = "sha256-gOTcX/9MZVMUE0Xvb4IEcv+0TQJkZFNEnL757ljU360=";
  },
}:

let
  pkgs = import nixpkgs { inherit system; };

  hyprland =
    if pkgs.lib.versionAtLeast pkgs.hyprland.version "0.55.2" then
      pkgs.hyprland
    else
      throw "hyprmasq VM test requires nixpkgs hyprland >= 0.55.2, got ${pkgs.hyprland.version}";

  pluginBuildInputs = [
    hyprland.dev
    pkgs.aquamarine
    pkgs.cairo
    pkgs.glslang
    pkgs.hyprcursor
    pkgs.hyprgraphics
    pkgs.hyprlang
    pkgs.hyprutils
    pkgs.libGL
    pkgs.libdrm
    pkgs.libinput
    pkgs.libpng
    pkgs.librsvg
    pkgs.libxkbcommon
    pkgs.pixman
    pkgs.systemd
    pkgs.wayland
    pkgs.libxcb
    pkgs."libxcb-errors"
    pkgs."libxcb-wm"
  ];

  plugin = pkgs.stdenv.mkDerivation {
    pname = "hyprmasq";
    version = "0.1.0";

    src = pkgs.lib.cleanSourceWith {
      src = ../.;
      filter =
        path: type:
        let
          base = baseNameOf path;
        in
        !(pkgs.lib.hasPrefix ".git" base)
        && base != "build"
        && base != "nix"
        && base != "hyprmasq.so"
        && !(pkgs.lib.hasSuffix ".o" base)
        && !(pkgs.lib.hasSuffix ".d" base);
    };

    nativeBuildInputs = [ pkgs.pkg-config ];
    buildInputs = pluginBuildInputs;

    preBuild = ''
      export PKG_CONFIG_PATH="${
        pkgs.lib.makeSearchPathOutput "dev" "lib/pkgconfig" pluginBuildInputs
      }:${
        pkgs.lib.makeSearchPathOutput "dev" "share/pkgconfig" pluginBuildInputs
      }:${hyprland.dev}/share/pkgconfig:$PKG_CONFIG_PATH"
    '';

    buildPhase = ''
      runHook preBuild
      make clean all
      runHook postBuild
    '';

    installPhase = ''
      runHook preInstall
      install -Dm755 hyprmasq.so "$out/lib/hyprmasq.so"
      runHook postInstall
    '';
  };

  testConfig = pkgs.writeText "hyprmasq-test.lua" ''
    hl.plugin.load("${plugin}/lib/hyprmasq.so")

    hl.monitor({ output = "HYPRMASQ-TEST", mode = "640x480@60", position = "0x0", scale = "1" })
    hl.monitor({ output = "", disabled = true })

    hl.config({
      general = {
        gaps_in = 0,
        gaps_out = 0,
        border_size = 0,
      },
      decoration = {
        rounding = 0,
        shadow = { enabled = false },
        blur = { enabled = false },
      },
      animations = {
        enabled = false,
      },
      misc = {
        force_default_wallpaper = -1,
        disable_hyprland_logo = true,
      },
      input = {
        kb_layout = "us",
        follow_mouse = 0,
      },
      debug = {
        disable_logs = false,
      },
    })
  '';

in
pkgs.testers.runNixOSTest {
  name = "hyprmasq-window-screenshot-vm";

  nodes.machine =
    { pkgs, ... }:
    {
      users.users.alice = {
        isNormalUser = true;
        extraGroups = [ "video" ];
      };

      environment.systemPackages = [
        hyprland
        pkgs.foot
        pkgs.jq
        pkgs.procps
        pkgs.python3
      ];

      environment.variables = {
        AQ_TRACE = "1";
        HYPRLAND_TRACE = "1";
        XDG_CACHE_HOME = "/tmp";
        TERM = "xterm-256color";
      };

      programs.hyprland = {
        enable = true;
        package = hyprland;
      };

      xdg.portal.enable = pkgs.lib.mkForce false;
      services.speechd.enable = false;
      services.getty.autologinUser = "alice";

      virtualisation = {
        cores = 4;
        memorySize = 8192;
        resolution = {
          x = 1024;
          y = 768;
        };
        qemu.options = [ "-vga none -device virtio-gpu-pci" ];
      };

      system.stateVersion = "24.11";
    };

  testScript = ''
    import shlex
    import time

    machine.wait_for_unit("multi-user.target")

    hyprctl = "${hyprland}/bin/hyprctl"
    hyprland_bin = "${hyprland}/bin/Hyprland"
    runtime = "/tmp"

    machine.succeed("rm -rf /tmp/hypr /tmp/hyprmasq-*")
    machine.succeed(
        "su - alice -c "
        + shlex.quote(
            f"XDG_RUNTIME_DIR={runtime} HYPRLAND_HEADLESS_ONLY=1 "
            f"AQ_TRACE=1 HYPRLAND_TRACE=1 nohup {hyprland_bin} --config ${testConfig} "
            "> /tmp/hyprmasq-hyprland.log 2>&1 &"
        )
    )

    try:
        machine.wait_until_succeeds(f"test -f {runtime}/hypr/*/hyprland.lock", timeout=60)
    except Exception:
        machine.succeed("cat /tmp/hyprmasq-hyprland.log >&2 || true")
        machine.succeed("cat /tmp/hyprland/hyprlandCrashReport*.txt >&2 || true")
        raise

    instance = machine.succeed(f"basename $(dirname $(ls {runtime}/hypr/*/hyprland.lock | head -1))").strip()
    wayland_display = machine.succeed(f"sed -n '2p' {runtime}/hypr/{instance}/hyprland.lock").strip()
    ipc_env = f"XDG_RUNTIME_DIR={runtime} HYPRLAND_INSTANCE_SIGNATURE={instance}"
    client_env = f"XDG_RUNTIME_DIR={runtime} WAYLAND_DISPLAY={wayland_display}"

    def hypr(args):
        return machine.succeed(f"{ipc_env} {hyprctl} {args}")

    def dump_logs():
        machine.succeed(f"{ipc_env} {hyprctl} monitors >&2 || true")
        machine.succeed(f"{ipc_env} {hyprctl} -j monitors >&2 || true")
        machine.succeed(f"{ipc_env} {hyprctl} -j workspaces >&2 || true")
        machine.succeed(f"{ipc_env} {hyprctl} -j clients >&2 || true")
        machine.succeed(f"{ipc_env} {hyprctl} plugin list >&2 || true")
        machine.succeed("cat /tmp/hyprmasq-hyprland.log >&2 || true")
        machine.succeed("cat /tmp/hyprland/hyprlandCrashReport*.txt >&2 || true")
        machine.succeed("for f in /tmp/hyprmasq-*.log; do echo ==== $f ==== >&2; cat $f >&2 || true; done")

    def wait_for_test_monitor():
        deadline = time.time() + 30
        while time.time() < deadline:
            exit_code, _ = machine.execute(
                f"{ipc_env} {hyprctl} -j monitors "
                "| jq -e '.[] | select(.name == \"HYPRMASQ-TEST\" and .activeWorkspace.id != null)'"
            )
            if exit_code == 0:
                return
            time.sleep(1)

        dump_logs()
        raise Exception("Timed out waiting for HYPRMASQ-TEST to become active")

    def wait_for_window(title):
        deadline = time.time() + 60
        while time.time() < deadline:
            exit_code, _ = machine.execute(
                f"{ipc_env} {hyprctl} -j clients "
                f"| jq -e '.[] | select(.title == \"{title}\" and .mapped == true)'"
            )
            if exit_code == 0:
                return
            time.sleep(1)

        dump_logs()
        raise Exception(f"Timed out waiting for window {title}")

    def window_address(title):
        return machine.succeed(
            f"{ipc_env} {hyprctl} -j clients "
            f"| jq -r '.[] | select(.title == \"{title}\") | .address' | head -1"
        ).strip()

    def screenshot(address, path):
        out = hypr(f"hyprmasq screenshot {address} {path}").strip()
        if out != "ok":
            dump_logs()
            raise Exception(f"hyprmasq screenshot failed: {out}")

    def assert_png(path, expected_rgb):
        script = f"""
    import struct
    import zlib
    from pathlib import Path

    path = Path({path!r})
    data = path.read_bytes()
    assert data.startswith(b"\\x89PNG\\r\\n\\x1a\\n"), data[:8]

    pos = 8
    width = height = color_type = bit_depth = None
    compressed = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length

        if kind == b"IHDR":
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(">IIBBBBB", chunk)
            assert bit_depth == 8, bit_depth
            assert color_type == 6, color_type
            assert compression == 0, compression
            assert filter_method == 0, filter_method
            assert interlace == 0, interlace
        elif kind == b"IDAT":
            compressed.extend(chunk)
        elif kind == b"IEND":
            break

    assert width >= 100 and height >= 100, (width, height)
    raw = zlib.decompress(bytes(compressed))
    stride = width * 4
    assert len(raw) == height * (stride + 1), (len(raw), width, height)
    cx, cy = width // 2, height // 2
    row = raw[cy * (stride + 1):(cy + 1) * (stride + 1)]
    assert row[0] == 0, row[0]
    index = 1 + cx * 4
    rgb = tuple(row[index:index + 3])
    expected = {expected_rgb!r}
    if any(abs(int(a) - int(b)) > 35 for a, b in zip(rgb, expected)):
        raise SystemExit(f"unexpected center pixel for {{path}}: got {{rgb}}, expected {{expected}}, size={{width}}x{{height}}")
    print(path, width, height, rgb)
    """
        machine.succeed("python3 - <<'PY'\n" + script + "PY")

    hypr("output create headless HYPRMASQ-TEST")
    wait_for_test_monitor()
    machine.succeed(f"{ipc_env} {hyprctl} plugin list > /tmp/hyprmasq-plugin-list")
    machine.succeed("grep -q hyprmasq /tmp/hyprmasq-plugin-list")

    windows = [
        ("hm-red", "1", "ff0000", (255, 0, 0)),
        ("hm-green", "2", "00ff00", (0, 255, 0)),
        ("hm-blue", "3", "0000ff", (0, 0, 255)),
    ]

    for title, workspace, color, _expected in windows:
        hypr(f"dispatch 'hl.dsp.focus({{ workspace = \"{workspace}\" }})'")
        machine.succeed(
            "su - alice -c "
            + shlex.quote(
                f"{client_env} foot -T {title} -o colors.background={color} "
                "sh -c 'sleep 300' "
                f"> /tmp/hyprmasq-{title}.log 2>&1 &"
            )
        )
        wait_for_window(title)

    hypr("dispatch 'hl.dsp.focus({ workspace = \"4\" })'")
    machine.wait_until_succeeds(
        f"{ipc_env} {hyprctl} -j activeworkspace | jq -e '.id == 4'",
        timeout=30,
    )

    machine.succeed(f"{ipc_env} {hyprctl} -j clients > /tmp/hyprmasq-clients-before.json")
    machine.succeed(
        "jq -e '"
        "[.[] | select(.title | startswith(\"hm-\")) | select(.workspace.id != 4)] | length == 3"
        "' /tmp/hyprmasq-clients-before.json"
    )

    for title, _workspace, _color, expected in windows:
        address = window_address(title)
        if not address.startswith("0x"):
            dump_logs()
            raise Exception(f"invalid address for {title}: {address}")

        path = f"/tmp/hyprmasq-{title}.png"
        screenshot(address, path)
        assert_png(path, expected)
        machine.copy_from_machine(path)

    machine.copy_from_machine("/tmp/hyprmasq-plugin-list")
    machine.copy_from_machine("/tmp/hyprmasq-clients-before.json")
    machine.succeed("cp /tmp/hyprmasq-hyprland.log /tmp/hyprmasq-hyprland.log.final")
    machine.copy_from_machine("/tmp/hyprmasq-hyprland.log.final")

    hypr("dispatch 'hl.dsp.exit()'")
    machine.wait_until_fails(
        f"test -f {runtime}/hypr/{instance}/hyprland.lock "
        f"&& test -e /proc/$(sed -n '1p' {runtime}/hypr/{instance}/hyprland.lock)",
        timeout=60,
    )
  '';
}
