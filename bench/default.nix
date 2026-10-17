# The benchmark VM: Hyprland with hyprmasq, a task's apps and servers, and no network. bench/run.py builds the
# test driver of this file and runs it outside the Nix sandbox, since the agent runs on the host and needs the
# model's API. The VM reaches nothing: the host talks to hyprmasq-mcp over vsock.
#
# `task` names the task under tasks/: its NixOS module (vm.nix) is part of the VM, and its setup.py runs in it.
# `source` is the hyprmasq checkout whose plugin, MCP server and NixOS module the VM gets, by default this one;
# run.py --source points it at another worktree, to benchmark a branch without the benchmark in it.
# `display` is the monitor, WIDTHxHEIGHT@SCALE in device pixels: hyprmasq's shots and zooms depend on it.
# `variant` is the task's variant, for a task that has them (tasks/TASK/variants): its vm.nix gets it as benchVariant.
{
  system ? "x86_64-linux",
  task ? "thunderbird",
  variant ? "",
  source ? ../.,
  display ? "1920x1080@1",
  nixpkgs ?
    let
      locked = (builtins.fromJSON (builtins.readFile (source + "/flake.lock"))).nodes.nixpkgs.locked;
    in
    builtins.fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    },
}:
let
  pkgs = import nixpkgs { inherit system; };
  monitor =
    let
      m = builtins.match "([0-9]+x[0-9]+)@([0-9.]+)" display;
    in
    if m == null then
      throw "display must be WIDTHxHEIGHT@SCALE, such as 2560x1440@1, got ${display}"
    else
      {
        mode = builtins.elemAt m 0;
        scale = builtins.elemAt m 1;
      };
  hyprland =
    if pkgs.hyprland.version == "0.56.2" then
      pkgs.hyprland
    else
      throw "hyprmasq requires Hyprland 0.56.2, got ${pkgs.hyprland.version}";
  # Only files Git tracks, as in a flake build: a hyprmasq-input that make left in the checkout must not stand in
  # for the one the package builds.
  fs = pkgs.lib.fileset;
  pluginSource = fs.toSource {
    root = source;
    fileset = fs.difference (fs.gitTracked source) (
      fs.unions (map (d: fs.maybeMissing (source + "/${d}")) [ "nix" "docs" "mcp" "bench" ])
    );
  };
  plugin = pkgs.callPackage (source + "/nix/package.nix") {
    inherit hyprland;
    hyprmasqSrc = pluginSource;
  };
  module = import (source + "/nix/module.nix") {
    packages.${system} = {
      hyprmasq = plugin;
      inherit hyprland;
      inherit (pkgs) xdg-desktop-portal-hyprland;
    };
  };

  hyprlandConfig = pkgs.writeText "hyprmasq-bench.lua" ''
    dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")

    hl.monitor({ output = "HYPRMASQ-BENCH", mode = "${monitor.mode}@60", position = "0x0", scale = "${monitor.scale}" })
    hl.monitor({ output = "", disabled = true })

    hl.config({
      general = { gaps_in = 0, gaps_out = 0, border_size = 0 },
      decoration = {
        rounding = 0,
        shadow = { enabled = false },
        blur = { enabled = false },
      },
      animations = { enabled = false },
      misc = {
        force_default_wallpaper = -1,
        disable_hyprland_logo = true,
      },
      input = { kb_layout = "us", follow_mouse = 0 },
    })
  '';

  # Starts hyprmasq-mcp for one vsock connection, in the session the test script started.
  mcpServer = pkgs.writeShellScript "hyprmasq-bench-mcp" ''
    export XDG_RUNTIME_DIR=/run/user/1000
    export HYPRLAND_INSTANCE_SIGNATURE="$(cat /tmp/hyprmasq-bench-instance)"
    exec ${plugin.mcp}/bin/hyprmasq-mcp 2>>/tmp/hyprmasq-bench-mcp.log
  '';

  # Logs Hyprland's events (windows opening and closing, focus, workspaces) with the time of each, so that
  # report.py can tell whether the run disturbed the user's workspace at any point.
  eventLogger = pkgs.writeScript "hyprmasq-bench-events" ''
    #!${pkgs.python3}/bin/python3
    import socket, sys, time
    sock = socket.socket(socket.AF_UNIX)
    sock.connect(sys.argv[1])
    out = open(sys.argv[2], "a", buffering=1)
    for line in sock.makefile("r", errors="replace"):
        out.write(f"{time.time():.3f} {line}")
  '';
in
pkgs.testers.runNixOSTest {
  name = "hyprmasq-bench-${task}${pkgs.lib.optionalString (variant != "") "-${variant}"}";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [
        module
        (./tasks + "/${task}/vm.nix")
      ];
      _module.args.benchVariant = variant;
      programs.hyprmasq.enable = true;

      users.users.alice = {
        isNormalUser = true;
        extraGroups = [ "video" ];
      };

      environment.systemPackages = [
        pkgs.foot
        pkgs.grim
        pkgs.jq
        pkgs.procps
        pkgs.socat
        pkgs.adwaita-icon-theme
      ];
      fonts.enableDefaultPackages = true;

      environment.etc."hyprmasq-bench/hyprland.lua".source = hyprlandConfig;
      environment.etc."hyprmasq-bench/mcp".source = mcpServer;
      environment.etc."hyprmasq-bench/events".source = eventLogger;

      environment.variables = {
        XDG_CACHE_HOME = "/tmp";
        TERM = "xterm-256color";
      };

      # Off unless a task needs it (mkForce there): stronger than the Hyprland module, weaker than mkForce.
      xdg.portal.enable = pkgs.lib.mkOverride 60 false;
      services.speechd.enable = false;
      services.timesyncd.enable = false;
      services.getty.autologinUser = "alice";

      # No network: the task's servers listen on localhost, and the host reaches hyprmasq-mcp over vsock.
      virtualisation = {
        vlans = [ ];
        cores = 4;
        memorySize = 6144;
        qemu.options = [
          "-vga none -device virtio-gpu-pci"
          "-device vhost-vsock-pci,guest-cid=\${HYPRMASQ_BENCH_CID:-4242}"
          # Tasks see the same date on every run.
          "-rtc base=2026-03-10T09:30:00"
        ];
      };
      boot.kernelModules = [ "vmw_vsock_virtio_transport" ];

      system.stateVersion = "24.11";
    };

  testScript = ''
    TASK_DIR = "${./tasks + "/${task}"}"
    exec(open("${./vm.py}").read())
  '';
}
