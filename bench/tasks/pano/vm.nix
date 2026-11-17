# The pano task's machine: Mara's blog, where she sets the opening view of a post's panorama, and Firefox with her profile. The
# blog is on blog.example.test, which resolves to localhost. benchVariant, from default.nix, is the panorama: a
# directory of the task with its photo and view.json, the page's texts, where the view starts and the target.
{ pkgs, lib, benchVariant, ... }:
let
  python = pkgs.python3;
  # The task's files without Python's caches, which a run of the checker on the host may leave.
  task = builtins.path {
    path = ./.;
    name = "hyprmasq-bench-pano";
    filter = path: _: baseNameOf path != "__pycache__";
  };
  variants = map (line: builtins.head (lib.splitString " " line)) (
    builtins.filter (line: line != "") (lib.splitString "\n" (builtins.readFile ./variants))
  );
in
{
  # Chromium in place of Firefox, for a run with --browser chromium.
  imports = [ ../browser.nix ];

  assertions = [
    {
      assertion = builtins.elem benchVariant variants;
      message = "the pano task's variant is one of ${toString variants}, not \"${benchVariant}\"";
    }
  ];

  networking.hosts."127.0.0.1" = [ "blog.example.test" ];

  environment.systemPackages = [ python ];

  systemd.services.hyprmasq-bench-blog = {
    description = "Mara's blog";
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      StateDirectory = "hyprmasq-bench-blog";
      ExecStart = "${python}/bin/python3 ${task}/viewer.py /var/lib/hyprmasq-bench-blog ${task}/${benchVariant}";
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

  environment.etc."hyprmasq-bench/pano".source = task;
}
