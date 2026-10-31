# The Calc task's machine: Kestrel's intranet, where the offices post their figures, Firefox with Mara's profile,
# and LibreOffice Calc with her sheet. The intranet is on intranet.example.test, which resolves to localhost.
{ pkgs, ... }:
let
  python = pkgs.python3;
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-calc";
    filter = path: _: baseNameOf path != "__pycache__";
  };
  sheet = pkgs.runCommand "hyprmasq-bench-calc-sheet" { } ''
    mkdir $out
    ${python}/bin/python3 ${task}/sheet.py "$out/Office revenue Q4 2025.ods"
  '';
  # watch.py reads the open sheet through Calc's UNO pipe, with the Python that LibreOffice's pyuno is built for.
  lo = pkgs.libreoffice.unwrapped;
  watch = pkgs.writeShellScript "hyprmasq-bench-calc-watch" ''
    export URE_BOOTSTRAP=vnd.sun.star.pathname:${lo}/lib/libreoffice/program/fundamentalrc
    export PYTHONPATH=${lo}/lib/libreoffice/program
    exec ${python}/bin/python3 ${task}/watch.py "$@"
  '';
in
{
  # Chromium in place of Firefox, for a run with --browser chromium.
  imports = [ ../browser.nix ];

  networking.hosts."127.0.0.1" = [ "intranet.example.test" ];

  environment.systemPackages = [
    pkgs.libreoffice
    python
  ];

  systemd.services.hyprmasq-bench-intranet = {
    description = "Kestrel's intranet";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      StateDirectory = "hyprmasq-bench-intranet";
      # Signs in Mara's browser while setup.py opens her tabs; setup.py removes the file.
      ExecStartPre = "${pkgs.coreutils}/bin/touch /var/lib/hyprmasq-bench-intranet/sign-in-open";
      ExecStart = "${python}/bin/python3 ${task}/intranet.py /var/lib/hyprmasq-bench-intranet";
    };
  };

  # The user's browser: no first-run pages, onboarding, telemetry, updates or captive portal checks. user.js in
  # the profile has the rest.
  programs.firefox = {
    enable = true;
    policies = {
      DisableTelemetry = true;
      DisableFirefoxStudies = true;
      DisablePocket = true;
      DisableAppUpdate = true;
      DisableProfileImport = true;
      DontCheckDefaultBrowser = true;
      NoDefaultBookmarks = true;
      OverrideFirstRunPage = "";
      OverridePostUpdatePage = "";
      CaptivePortal = false;
      OfferToSaveLogins = false;
      UserMessaging = {
        ExtensionRecommendations = false;
        FeatureRecommendations = false;
        UrlbarInterventions = false;
        SkipOnboarding = true;
        MoreFromMozilla = false;
        FirefoxLabs = false;
        Locked = true;
      };
    };
  };

  environment.etc."hyprmasq-bench/calc".source = task;
  environment.etc."hyprmasq-bench/calc-sheet".source = sheet;
  environment.etc."hyprmasq-bench/calc-watch".source = watch;
}
