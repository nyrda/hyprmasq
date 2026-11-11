# The benchmark VM: Hyprland with hyprmasq, a task's apps and servers, and no network. bench/run.py builds the
# test driver of this file and runs it outside the Nix sandbox, since the agent runs on the host and needs the
# model's API. The VM reaches nothing: the host talks to hyprmasq-mcp over vsock.
#
# `task` names the task under tasks/: its NixOS module (vm.nix) is part of the VM, and its setup.py runs in it.
# `source` is the hyprmasq checkout whose plugin, MCP server and NixOS module the VM gets, by default this one;
# run.py --source points it at another worktree, to benchmark a branch without the benchmark in it.
# `display` is the monitor, WIDTHxHEIGHT@SCALE in device pixels: hyprmasq's shots and zooms depend on it.
# `variant` is the task's variant, for a task that has them (tasks/TASK/variants): its vm.nix gets it as benchVariant.
# `backend` is the desktop tool the agent gets: hyprmasq, from `source`, or in its place cua, Cua Driver with its
# Hyprland plugin (cua/default.nix), hypruse (hypruse/default.nix) or computer-use-linux (computer-use-linux/default.nix),
# each set up as its documentation sets it up. `source` then gives only the Hyprland that the VM runs, and hyprmasq's
# plugin is not loaded.
# `browser` is the user's browser in the tasks that have one, firefox or chromium (tasks/TASK/browsers lists a task's):
# its vm.nix gets it as benchBrowser and its setup.py as BROWSER.
# `desktop` is bare, as the published benchmarks ran, or omarchy: every backend gets Omarchy's input settings and key
# binds (omarchy-desktop.lua), the portals it ships and the packages of its that the tools call, and vm.py parks the
# task's windows on numbered workspaces, which those binds reach. See bench/README.md, the desktop.
#
# Every VM has an accessibility session as a desktop has one, whichever the backend: the AT-SPI bus on the user's
# session bus, which Hyprland and the apps get, with toolkit-accessibility on. Runs before it, such as the
# 0.57.0 baseline, had none.
{
  system ? "x86_64-linux",
  task ? "thunderbird",
  variant ? "",
  source ? ../.,
  display ? "1920x1080@1",
  backend ? "hyprmasq",
  browser ? "firefox",
  desktop ? "bare",
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
  checkedBackend =
    if
      builtins.elem backend [
        "hyprmasq"
        "cua"
        "hypruse"
        "computer-use-linux"
      ]
    then
      backend
    else
      throw "backend must be hyprmasq, cua, hypruse or computer-use-linux, got ${backend}";
  isHyprmasq = checkedBackend == "hyprmasq";
  isCua = checkedBackend == "cua";
  isHypruse = checkedBackend == "hypruse";
  isCul = checkedBackend == "computer-use-linux";
  checkedBrowser =
    if
      builtins.elem browser [
        "firefox"
        "chromium"
      ]
    then
      browser
    else
      throw "browser must be firefox or chromium, got ${browser}";
  checkedDesktop =
    if
      builtins.elem desktop [
        "bare"
        "omarchy"
      ]
    then
      desktop
    else
      throw "desktop must be bare or omarchy, got ${desktop}";
  isOmarchy = checkedDesktop == "omarchy";
  # Whether the user's browser is Chromium: the Forgejo task's always is.
  userHasChromium = checkedBrowser == "chromium" || task == "forgejo";
  # Cua's input build, which refuses a Hyprland whose windows its driver does not see.
  cua = import ./cua { inherit pkgs; };
  hypruse = import ./hypruse { inherit pkgs hyprland; };
  cul = import ./computer-use-linux { inherit pkgs; };
  module = import (source + "/nix/module.nix") {
    packages.${system} = {
      hyprmasq = plugin;
      inherit hyprland;
      inherit (pkgs) xdg-desktop-portal-hyprland;
    };
  };

  # hyprmasq's module loads its plugin. Cua's README loads its plugin by hand (`hyprctl plugin load`) and then enables
  # it in the configuration and reloads: vm.py loads it and writes CUA_CONFIG, which this file reads once it exists.
  # hypruse and computer-use-linux are clients of Hyprland as released, with no plugin.
  hyprlandConfig = pkgs.writeText "hyprmasq-bench.lua" ''
    ${
      if isCua then
        ''pcall(dofile, "${cuaConfig}")''
      else if isHyprmasq then
        ''dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")''
      else
        "-- ${checkedBackend}: no plugin"
    }

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
    ${pkgs.lib.optionalString isOmarchy ''dofile("${./omarchy-desktop.lua}")''}
  '';

  cuaConfig = "/tmp/hyprmasq-bench-cua.lua";

  hyprmasqModule = {
    imports = [ module ];
    programs.hyprmasq.enable = true;
  };
  # Hyprland as hyprmasq's module sets it up, with the same packages, for the other backends.
  hyprlandModule = {
    programs.hyprland = {
      enable = true;
      package = hyprland;
      portalPackage = pkgs.xdg-desktop-portal-hyprland;
    };
  };
  # Cua Driver on PATH as its installer puts it, and a Chromium where a Linux package puts it, in every VM, as each has
  # Chromium: Arch's /usr/lib/chromium/chromium: Cua's isolated browser (`browser_prepare` with `allow_launch`, its browser guide's
  # Use an isolated browser) launches only a root-owned file there, which NixOS does not have. It starts the VM's Chromium.
  cuaModule =
    { lib, ... }:
    {
      imports = [ hyprlandModule ];
      environment.systemPackages = [ cua.driver ];
      systemd.tmpfiles.rules = [
        "d /usr/lib 0755 root root -"
        "d /usr/lib/chromium 0755 root root -"
        "C+ /usr/lib/chromium/chromium 0755 root root - ${pkgs.writeShellScript "chromium" ''
          exec ${pkgs.chromium}/bin/chromium "$@"
        ''}"
      ];
    };
  # hypruse on PATH, as its AUR package and `uv tool install` put it, with grim and wtype, which its README requires.
  hypruseModule = {
    imports = [ hyprlandModule ];
    environment.systemPackages = [
      hypruse
      pkgs.wtype
    ];
  };
  # computer-use-linux as its README sets it up on Hyprland, where xdg-desktop-portal-hyprland implements no
  # RemoteDesktop portal: its three binaries on PATH, as `cargo install` puts them; /dev/uinput for its absolute
  # pointer, open to the input group with the udev rule its install.sh gives, the kernel module loaded and alice in the
  # group (README, Troubleshooting: input.uinput.ok = false); ydotool 1.0.3+ and ydotoold as the user service its
  # install.sh writes, for keys; wtype for text (README: optional Unicode typing on wlroots/Hyprland Wayland); and the
  # desktop portal, whose Screenshot interface, xdg-desktop-portal-hyprland's, takes its screenshots where GNOME
  # Shell's is missing. Its on-screen indicator stays on, as by default.
  culModule =
    { lib, ... }:
    {
      imports = [ hyprlandModule ];
      environment.systemPackages = [
        cul.server
        pkgs.ydotool
        pkgs.wtype
      ];
      boot.kernelModules = [ "uinput" ];
      services.udev.extraRules = ''
        KERNEL=="uinput", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"
      '';
      # The socket's owner in its unit is %U:%U, the user and the group of the user's number, which a user has on the
      # distributions its install.sh supports, where each user has a group of their own. alice's is users: she gets
      # that group too.
      users.groups.alice.gid = 1000;
      users.users.alice.extraGroups = [
        "input"
        "alice"
      ];
      systemd.user.services.ydotoold = {
        description = "ydotool user daemon";
        documentation = [
          "man:ydotool(1)"
          "man:ydotoold(8)"
        ];
        serviceConfig = {
          Type = "simple";
          UMask = "0077";
          ExecStart = "${pkgs.ydotool}/bin/ydotoold --socket-path=%t/.ydotool_socket --socket-own=%U:%U --socket-perm=0600";
          Restart = "on-failure";
        };
        wantedBy = [ "default.target" ];
      };
      xdg.portal = {
        enable = lib.mkForce true;
        config.hyprland."org.freedesktop.impl.portal.Screenshot" = [ "hyprland" ];
      };
    };

  # A desktop session starts graphical-session.target, which xdg-desktop-portal requires: vm.py starts this target once
  # Hyprland runs, as the Forgejo task's setup.py does, whose vm.nix has it already.
  sessionModule =
    { lib, ... }:
    {
      systemd.user.targets = lib.mkIf ((isCul || isOmarchy) && task != "forgejo") {
        hyprmasq-bench-session = {
          description = "The benchmark's graphical session";
          bindsTo = [ "graphical-session.target" ];
          wants = [ "graphical-session-pre.target" ];
          after = [ "graphical-session-pre.target" ];
        };
      };
    };

  # Starts hyprmasq-mcp for one vsock connection, in the session the test script started.
  hyprmasqServer = pkgs.writeShellScript "hyprmasq-bench-mcp" ''
    export XDG_RUNTIME_DIR=/run/user/1000
    export HYPRLAND_INSTANCE_SIGNATURE="$(cat /tmp/hyprmasq-bench-instance)"
    # run.py's --mcp-args, which vm.py leaves here.
    exec ${plugin.mcp}/bin/hyprmasq-mcp $(cat /tmp/hyprmasq-bench-mcp-args 2>/dev/null) 2>>/tmp/hyprmasq-bench-mcp.log
  '';

  # `cua-driver mcp` for one vsock connection, in the session the test script started, with what Cua documents for a
  # Wayland session: CUA_DRIVER_RS_ENABLE_WAYLAND=1 (its skill's LINUX.md) and the session's variables, which the
  # laptop's agent homes passed to it. The VM is offline: telemetry off with Cua's own switch
  # (docs/content/docs/cua-driver/guides/operate.mdx, Telemetry). Where the user's browser is Chromium, in the Forgejo
  # task and with `browser = "chromium"`, Cua's browser tools get `cua-driver mcp --grant existing-profile`, which its
  # browser guide asks for to attach to the user's browser (guides/browsers.mdx, Attach to a logged-in profile). Its
  # browser tools take Chromium only, so no other VM gets a grant.
  cuaServer = pkgs.writeShellScript "hyprmasq-bench-cua-mcp" ''
    export XDG_RUNTIME_DIR=/run/user/1000
    export HYPRLAND_INSTANCE_SIGNATURE="$(cat /tmp/hyprmasq-bench-instance)"
    export WAYLAND_DISPLAY="$(cat /tmp/hyprmasq-bench-display)"
    export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=Hyprland
    export CUA_DRIVER_RS_ENABLE_WAYLAND=1
    export CUA_DRIVER_RS_TELEMETRY_ENABLED=false
    ${pkgs.lib.optionalString userHasChromium ''
      # Cua's isolated browser with a `pid` launches that process's own executable, which on NixOS is Chromium without
      # its wrapper. It then has no sandbox helper beside it, as a distribution's package has, and dies with SIGILL. The
      # variable that the wrapper sets finds it (bench/README.md, Browser tools).
      export CHROME_DEVEL_SANDBOX=/run/wrappers/bin/__chromium-suid-sandbox
    ''}
    # --no-overlay: its agent cursor is a full-screen layer surface that a desktop composites on its GPU for next to
    # nothing. Here the VM's Hyprland composites it in software, at more than two of the VM's four CPUs while the driver
    # reads a window, and its walks take that much longer (bench/README.md, the VM against a desktop). The cursor is for
    # a person to watch, and no tool of the driver depends on it.
    exec ${cua.driver}/bin/cua-driver mcp --no-overlay${
      pkgs.lib.optionalString userHasChromium " --grant existing-profile"
    } 2>>/tmp/hyprmasq-bench-mcp.log
  '';
  # The session's variables, which a server started from the user's desktop has.
  sessionEnv = ''
    export XDG_RUNTIME_DIR=/run/user/1000
    export HYPRLAND_INSTANCE_SIGNATURE="$(cat /tmp/hyprmasq-bench-instance)"
    export WAYLAND_DISPLAY="$(cat /tmp/hyprmasq-bench-display)"
    export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
    export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=Hyprland
  '';

  # hypruse's MCP server, `uvx hypruse` in its README, for one vsock connection, with its defaults (the automatic
  # background mode among them) but one: HYPRUSE_SCREENSHOT_MODE=image, which its README and server.json give for a
  # client that renders MCP images and cannot read files. Its default, file, writes captures in the VM and returns
  # their path, which the agent on the host, without a file reader, could not open.
  hypruseServer = pkgs.writeShellScript "hyprmasq-bench-hypruse-mcp" ''
    ${sessionEnv}
    export HYPRUSE_SCREENSHOT_MODE=image
    exec ${hypruse}/bin/hypruse 2>>/tmp/hyprmasq-bench-mcp.log
  '';

  # `computer-use-linux mcp` for one vsock connection, with the session's variables, which its Codex plugin forwards
  # to it, and COMPUTER_USE_LINUX_HYPRLAND_BACKGROUND=1, its opt-in way on Hyprland to work on a window without focusing
  # it, which its README and its skill name for this and its Codex plugin forwards. Its other settings stay at their
  # defaults: its README says most setups need none.
  culServer = pkgs.writeShellScript "hyprmasq-bench-computer-use-linux-mcp" ''
    ${sessionEnv}
    export COMPUTER_USE_LINUX_HYPRLAND_BACKGROUND=1
    exec ${cul.server}/bin/computer-use-linux mcp 2>>/tmp/hyprmasq-bench-mcp.log
  '';

  mcpServer =
    if isCua then
      cuaServer
    else if isHypruse then
      hypruseServer
    else if isCul then
      culServer
    else
      hyprmasqServer;
  backendModule =
    if isCua then
      cuaModule
    else if isHypruse then
      hypruseModule
    else if isCul then
      culModule
    else
      hyprmasqModule;

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
  name = "hyprmasq-bench-${task}${pkgs.lib.optionalString (variant != "") "-${variant}"}${
    pkgs.lib.optionalString (!isHyprmasq) "-${checkedBackend}"
  }${pkgs.lib.optionalString (browser != "firefox") "-${browser}"}${pkgs.lib.optionalString isOmarchy "-omarchy"}";

  nodes.machine =
    { pkgs, ... }:
    {
      imports = [
        (./tasks + "/${task}/vm.nix")
        backendModule
        sessionModule
      ];
      _module.args.benchVariant = variant;
      _module.args.benchBackend = backend;
      _module.args.benchBrowser = checkedBrowser;

      users.users.alice = {
        isNormalUser = true;
        extraGroups = [ "video" ];
      };

      environment.systemPackages = [
        pkgs.gsettings-desktop-schemas
        pkgs.foot
        pkgs.grim
        pkgs.jq
        pkgs.procps
        pkgs.socat
        pkgs.adwaita-icon-theme
        # Chromium in every VM, as Omarchy ships it, also where the user's browser is Firefox: a tool may open a browser
        # of its own, such as Cua's isolated browser, whatever the user's is. It runs only when something starts it.
        pkgs.chromium
      ]
      # What Omarchy has on PATH of what the tools call, for text on the desktop, the clipboard, opening a file, D-Bus
      # and images.
      ++ pkgs.lib.optionals isOmarchy [
        pkgs.wtype
        pkgs.wl-clipboard
        pkgs.xdg-utils
        pkgs.glib
        pkgs.imagemagick
        pkgs.ffmpeg
        pkgs.binutils
        pkgs.dbus
        pkgs.libnotify
        pkgs.slurp
        pkgs.hyprpicker
      ];
      fonts.enableDefaultPackages = true;

      environment.etc."hyprmasq-bench/hyprland.lua".source = hyprlandConfig;
      environment.etc."hyprmasq-bench/mcp".source = mcpServer;
      environment.etc."hyprmasq-bench/events".source = eventLogger;
      environment.etc."hyprmasq-bench/backend".text = backend;
      environment.etc."hyprmasq-bench/desktop".text = checkedDesktop;
      environment.etc."hyprmasq-bench/cua-plugin".text =
        pkgs.lib.optionalString isCua "${cua.plugin}/lib/cua/hyprland/cua-hyprland-plugin.so";

      # The accessibility session: the AT-SPI bus, activated on the user's session bus, and toolkit-accessibility
      # on, which its bus launcher reads for org.a11y.Status (Cua's LINUX.md: an accessibility bus must run and
      # toolkit-accessibility must be on).
      services.gnome.at-spi2-core.enable = true;
      programs.dconf = {
        enable = true;
        profiles.user.databases = [
          { settings."org/gnome/desktop/interface".toolkit-accessibility = true; }
        ];
      };

      environment.variables = {
        XDG_CACHE_HOME = "/tmp";
        TERM = "xterm-256color";
      };

      # Off unless a task needs it (mkForce there): stronger than the Hyprland module, weaker than mkForce. Omarchy's
      # desktop has its portals: Hyprland's and GTK's, in its order, with the Hyprland module's own package.
      xdg.portal = if isOmarchy then {
        enable = pkgs.lib.mkForce true;
        extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
        config.hyprland.default = pkgs.lib.mkForce [ "hyprland" "gtk" ];
      } else {
        enable = pkgs.lib.mkOverride 60 false;
      };
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
    VARIANT = "${variant}"
    BACKEND = "${backend}"
    BROWSER = "${checkedBrowser}"
    DESKTOP = "${checkedDesktop}"
    exec(open("${./vm.py}").read())
  '';
}
