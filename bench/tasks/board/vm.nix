# The board task's machine: Mara's build guide, where she labels the figures, and Firefox with her profile. The guide
# is on guide.example.test, which resolves to localhost. benchVariant, from default.nix, is how its page drags: dnd
# or pointer, see guide.py.
{ pkgs, benchVariant, ... }:
let
  python = pkgs.python3;
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-board";
    filter = path: _: baseNameOf path != "__pycache__";
  };
in
{
  # Chromium in place of Firefox, for a run with --browser chromium.
  imports = [ ../browser.nix ];

  assertions = [
    {
      assertion = builtins.elem benchVariant [
        "dnd"
        "pointer"
      ];
      message = "the board task's variant is dnd or pointer, not \"${benchVariant}\"";
    }
  ];

  networking.hosts."127.0.0.1" = [ "guide.example.test" ];

  environment.systemPackages = [ python ];

  systemd.services.hyprmasq-bench-guide = {
    description = "Mara's build guide";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      StateDirectory = "hyprmasq-bench-guide";
      ExecStart = "${python}/bin/python3 ${task}/guide.py /var/lib/hyprmasq-bench-guide ${benchVariant}";
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

  environment.etc."hyprmasq-bench/board".source = task;
}
