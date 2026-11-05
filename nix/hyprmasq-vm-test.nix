{
  system ? "x86_64-linux",
  # The areas of nix/vm-test/areas.json to run, as a list or a string of names separated by spaces, with the areas
  # they require. null runs them all.
  areas ? null,
  # Ends the test's name when it runs only some areas, which tells builds that run at once apart in their logs.
  label ? "areas",
  # Runs the machine as a systemd-nspawn container instead of a VM, with Hyprland nested in labwc on the host's GPU.
  # The build needs the uid-range and gpu system features, see docs/vm-test.md.
  container ? false,
  # Hyprland's package set at the commit locked in flake.lock.
  pkgs ? import ./pkgs.nix { inherit system; },
  # The nixpkgs whose Go builds hyprmasq-mcp: the flake's nixpkgs input, read from flake.lock.
  goPkgs ?
    let
      lock = builtins.fromJSON (builtins.readFile ../flake.lock);
      locked = lock.nodes.${lock.nodes.root.inputs.nixpkgs}.locked;
    in
    import (builtins.fetchTarball {
      url = "https://github.com/NixOS/nixpkgs/archive/${locked.rev}.tar.gz";
      sha256 = locked.narHash;
    }) { inherit system; },
}:
let
  # Hyprland's flake versions its package "<release>+date=<date>_<commit>".
  targetVersion = pkgs.lib.fileContents ../HYPRLAND_VERSION;
  pinnedVersion = builtins.head (pkgs.lib.splitString "+" pkgs.hyprland.version);

  # Bumping the pin is a port: the pinned Hyprland has to be the release this branch targets.
  hyprland =
    if pinnedVersion == targetVersion then
      pkgs.hyprland
    else
      throw "hyprmasq targets Hyprland ${targetVersion} (HYPRLAND_VERSION), flake.lock pins ${pkgs.hyprland.version}";

  # Same compiler as Hyprland, so the plugin shares its C++ runtime.
  plugin = pkgs.callPackage ./package.nix {
    inherit hyprland;
    inherit (hyprland) stdenv;
    inherit (goPkgs) buildGoModule;
  };

  # The VM installs through the NixOS module, fed with the packages this test pins.
  module = import ./module.nix {
    packages.${system} = {
      hyprmasq = plugin;
      inherit hyprland;
      inherit (pkgs) xdg-desktop-portal-hyprland;
    };
  };
  focusProbe = pkgs.callPackage ./focus-probe.nix { };
  dndProbe = pkgs.callPackage ./dnd-probe.nix { };
  clipProbe = pkgs.callPackage ./clip-probe.nix { };
  comboProbe = pkgs.callPackage ./combo-probe.nix { };
  swapchainProbe = pkgs.callPackage ./swapchain-probe.nix { };
  qtProbe = pkgs.callPackage ./qt-probe.nix { };
  sliderProbe = pkgs.callPackage ./slider-probe.nix { };
  scrollProbe = pkgs.callPackage ./scroll-probe.nix { };
  suspendProbe = pkgs.callPackage ./suspend-probe.nix { };
  # hyprland-dialog, without which Hyprland does not watch for apps that stop answering its pings. This one logs each
  # dialog that it opens, with its arguments on one line, also one that closes again within milliseconds. It comes from
  # hyprland-qtutils of the flake's own nixpkgs: Hyprland's nixpkgs no longer has it, and the hyprland-dialog of its
  # successor, hyprland-guiutils, crashes in the VM before it maps its window.
  dialog = pkgs.writeShellScriptBin "hyprland-dialog" ''
    args="$*"
    printf '%s %s\n' "$(date +%s.%N)" "''${args//$'\n'/ }" >> /tmp/hyprmasq-dialogs.log
    exec ${goPkgs.hyprland-qtutils}/bin/hyprland-dialog "$@"
  '';
  userInput = pkgs.callPackage ./user-input.nix { inherit hyprland; };
  # The keyboard and pointer of labwc's seat, in the container.
  parentSeat = pkgs.callPackage ./parent-seat.nix { inherit hyprland; };
  # labwc, the container's parent compositor, without its own XWayland, which would take the first free X display that
  # the test gives Hyprland's X11 windows.
  labwc = pkgs.labwc.overrideAttrs (old: {
    mesonFlags = map (
      flag: if flag == lib.mesonEnable "xwayland" true then lib.mesonEnable "xwayland" false else flag
    ) old.mesonFlags;
  });
  hyprctlCaller = pkgs.callPackage ./hyprctl-caller.nix {
    inherit plugin;
    inherit (hyprland) stdenv;
  };
  # Plugins that hook a function of hyprmasq's each, which the hooks area loads beside it.
  hookTaker = pkgs.callPackage ./hook-taker.nix {
    inherit plugin;
    inherit (hyprland) stdenv;
  };
  python = pkgs.python3.withPackages (ps: [ ps.pillow ]);
  inherit (pkgs) lib;

  # The test script: common.py, then the code of each area inside a try block, in the order of areas.json.
  allAreas = builtins.fromJSON (builtins.readFile ./vm-test/areas.json);
  areaNames = map (area: area.name) allAreas;
  requires = lib.listToAttrs (map (area: lib.nameValuePair area.name area.requires) allAreas);
  requested =
    if areas == null then
      areaNames
    else if builtins.isList areas then
      areas
    else
      lib.filter (name: name != "") (lib.splitString " " areas);
  unknown = lib.filter (name: !(requires ? ${name})) requested;
  withRequired =
    names:
    let
      more = lib.unique (names ++ lib.concatMap (name: requires.${name}) names);
    in
    if lib.length more == lib.length names then names else withRequired more;
  selected =
    if unknown != [ ] then
      throw "unknown VM test areas ${toString unknown}; the areas are ${toString areaNames}"
    else
      lib.filter (area: lib.elem area.name (withRequired (lib.unique requested))) allAreas;
  indentLine = line: if line == "" then "" else "    " + line;
  areaCode =
    area:
    let
      code = builtins.readFile (./vm-test + "/${area.name}.py");
    in
    lib.concatMapStringsSep "\n" indentLine ([ "# ${area.name}.py" ] ++ lib.splitString "\n" code);
  # Fails the build when an area's code would not survive the indentation, or uses what an area it does not require
  # defines.
  areasChecked = pkgs.runCommand "hyprmasq-vm-test-areas" { nativeBuildInputs = [ pkgs.python3 ]; } ''
    python3 ${./vm-test}/check.py
    touch $out
  '';
  testScript = lib.concatStringsSep "\n" (
    [
      "# Areas: ${toString (map (area: area.name) selected)}, checked by ${areasChecked}"
      "expected_version = ${builtins.toJSON plugin.version}"
      "nested = ${if container then "True" else "False"}"
      (builtins.readFile ./vm-test/common.py)
      "try:"
      "    wait_for_hyprland()"
    ]
    ++ map areaCode selected
    ++ [
      "finally:"
      "    save_logs()"
      ""
    ]
  );

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

  # The machine, the same as a VM and as a container but for what the end of this file adds to each.
  machine =
    { pkgs, ... }:
    {
      # The benchmark's GTK 4 questionnaire, as hyprmasq-bench-form, which the anr and settle areas drive.
      imports = [
        module
        ../bench/tasks/form/vm.nix
      ];
      _module.args.benchVariant = "25";
      programs.hyprmasq.enable = true;
      # The recording area reads its recordings here, and checks that the module writes these settings.
      programs.hyprmasq.mcpSettings.record = {
        dir = "/tmp/hm-recordings";
        gif = true;
      };

      users.users.alice = {
        isNormalUser = true;
        extraGroups = [ "video" ];
      };

      environment.systemPackages = [
        pkgs.foot
        pkgs.grim
        # An idle client, as the user's that turns the monitors off and on again.
        pkgs.hypridle
        pkgs.jq
        pkgs.procps
        # Over the questionnaire module's python3, which has no Pillow.
        (lib.hiPrio python)
        pkgs.wev
        # A virtual pointer, whose clicks are real input to Hyprland.
        pkgs.wlrctl
        focusProbe
        dndProbe
        clipProbe
        comboProbe
        swapchainProbe
        qtProbe
        sliderProbe
        scrollProbe
        suspendProbe
        dialog
        # The user's own clipboard: wl-copy and wl-paste use data-control, as clipboard managers do.
        pkgs.wl-clipboard
        # The user's held button and input method, as real input to Hyprland.
        userInput
        pkgs.xterm
        pkgs.xev
        pkgs.xdotool
        pkgs.xclip
        pkgs.xprop
        pkgs.xwayland-satellite
        # A select on X11, whose popup Chromium closes once its window loses the X focus.
        pkgs.chromium
        # ffprobe and ffmpeg, which read the recordings of hyprmasq-mcp.
        pkgs.ffmpeg-headless
      ];

      environment.etc."hyprmasq-test.lua".source = testConfig;
      # Scripts that the test runs in the VM.
      environment.etc."hyprmasq-test".source = ./vm-test/guest;
      environment.etc."hyprmasq-plugin".text = "${plugin}/lib/hyprmasq.so";
      environment.etc."hyprmasq-hyprctl-caller".text = "${hyprctlCaller}/lib/hyprmasq-hyprctl-caller.so";
      environment.etc."hyprmasq-hook-taker".text = "${hookTaker}/lib";

      environment.variables = {
        AQ_TRACE = "1";
        HYPRLAND_TRACE = "1";
        XDG_CACHE_HOME = "/tmp";
        TERM = "xterm-256color";
      };

      xdg.portal.enable = pkgs.lib.mkForce false;
      services.speechd.enable = false;
      services.getty.autologinUser = "alice";
      # Its Compose table makes the dead acute and c a ç, where that of en_US.UTF-8, the default, makes them a ć.
      i18n.extraLocales = [ "pt_BR.UTF-8/UTF-8" ];

      system.stateVersion = "24.11";
    };

in
pkgs.testers.runNixOSTest (
  {
    passthru = { inherit plugin; };
    name =
      "hyprmasq-window-automation-${if container then "container" else "vm"}"
      + lib.optionalString (areas != null) "-${label}";
    inherit testScript;
  }
  // (
    if container then
      {
        containers.machine = {
          imports = [ machine ];
          # Mesa for labwc and Hyprland, which render on the host's render node. The sandbox has it when the build
          # requires the gpu feature, through nix-required-mounts on the host.
          hardware.graphics.enable = true;
          virtualisation.systemd-nspawn.options = [ "--bind=/dev/dri" ];
          environment.systemPackages = [
            labwc
            parentSeat
          ];
        };
        requiredFeatures.gpu = true;
      }
    else
      {
        nodes.machine = {
          imports = [ machine ];
          virtualisation = {
            cores = 4;
            memorySize = 8192;
            resolution = {
              x = 1024;
              y = 768;
            };
            qemu.options = [ "-vga none -device virtio-gpu-pci" ];
          };
        };
      }
  )
)
