{
  system ? "x86_64-linux",
  nixpkgs ?
    let
      locked = (builtins.fromJSON (builtins.readFile ../flake.lock)).nodes.nixpkgs.locked;
    in
    builtins.fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    },
}:
let
  pkgs = import nixpkgs { inherit system; };
  hyprland =
    if pkgs.hyprland.version == "0.56.2" then pkgs.hyprland
    else throw "hyprmasq requires Hyprland 0.56.2, got ${pkgs.hyprland.version}";
  plugin = pkgs.callPackage ./package.nix { inherit hyprland; };

  # The VM installs through the NixOS module, fed with the packages this test pins.
  module = import ./module.nix {
    packages.${system} = {
      hyprmasq = plugin;
      inherit hyprland;
      inherit (pkgs) xdg-desktop-portal-hyprland;
    };
  };
  focusProbe = pkgs.callPackage ./focus-probe.nix { };
  python = pkgs.python3.withPackages (ps: [ ps.pillow ]);

  testConfig = pkgs.writeText "hyprmasq-test.lua" ''
    dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")

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
        -- Activation requests move focus, so the test can check that driven X11 apps cannot.
        focus_on_activate = true,
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
  passthru = { inherit plugin; };
  name = "hyprmasq-window-automation-vm";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [ module ];
      programs.hyprmasq.enable = true;

      users.users.alice = {
        isNormalUser = true;
        extraGroups = [ "video" ];
      };

      environment.systemPackages = [
        pkgs.foot
        pkgs.jq
        pkgs.procps
        python
        pkgs.wev
        focusProbe
        pkgs.xterm
        pkgs.xev
        pkgs.xdotool
        pkgs.xwayland-satellite
      ];

      environment.etc."hyprmasq-test.lua".source = testConfig;
      environment.etc."hyprmasq-plugin".text = "${plugin}/lib/hyprmasq.so";

      environment.variables = {
        AQ_TRACE = "1";
        HYPRLAND_TRACE = "1";
        XDG_CACHE_HOME = "/tmp";
        TERM = "xterm-256color";
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

  testScript = builtins.readFile ./vm-test.py;
}
