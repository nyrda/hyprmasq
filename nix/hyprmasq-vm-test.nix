{
  system ? "x86_64-linux",
  # The areas of nix/vm-test/areas.json to run, as a list or a string of names separated by spaces, with the areas
  # they require. null runs them all.
  areas ? null,
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
  dndProbe = pkgs.callPackage ./dnd-probe.nix { };
  clipProbe = pkgs.callPackage ./clip-probe.nix { };
  comboProbe = pkgs.callPackage ./combo-probe.nix { };
  swapchainProbe = pkgs.callPackage ./swapchain-probe.nix { };
  qtProbe = pkgs.callPackage ./qt-probe.nix { };
  suspendProbe = pkgs.callPackage ./suspend-probe.nix { };
  # hyprland-dialog, without which Hyprland does not watch for apps that stop answering its pings. This one logs each
  # dialog that it opens, with its arguments on one line, also one that closes again within milliseconds.
  dialog = pkgs.writeShellScriptBin "hyprland-dialog" ''
    args="$*"
    printf '%s %s\n' "$(date +%s.%N)" "''${args//$'\n'/ }" >> /tmp/hyprmasq-dialogs.log
    exec ${pkgs.hyprland-qtutils}/bin/hyprland-dialog "$@"
  '';
  userInput = pkgs.callPackage ./user-input.nix { inherit hyprland; };
  hyprctlCaller = pkgs.callPackage ./hyprctl-caller.nix { inherit plugin; };
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

in
pkgs.testers.runNixOSTest {
  passthru = { inherit plugin; };
  name = "hyprmasq-window-automation-vm" + lib.optionalString (areas != null) "-areas";

  nodes.machine =
    { pkgs, ... }:
    {
      # The benchmark's GTK 4 questionnaire, as hyprmasq-bench-form, which the anr and settle areas drive.
      imports = [
        module
        ../bench/tasks/form/vm.nix
      ];
      _module.args.benchVariant = "25";
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
      ];

      environment.etc."hyprmasq-test.lua".source = testConfig;
      # Scripts that the test runs in the VM.
      environment.etc."hyprmasq-test".source = ./vm-test/guest;
      environment.etc."hyprmasq-plugin".text = "${plugin}/lib/hyprmasq.so";
      environment.etc."hyprmasq-hyprctl-caller".text = "${hyprctlCaller}/lib/hyprmasq-hyprctl-caller.so";

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

  inherit testScript;
}
