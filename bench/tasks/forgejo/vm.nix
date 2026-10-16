# The Forgejo task's machine: a Forgejo server with Mara's repository, a release dashboard that signs in to it
# with OAuth in a popup window, a notes page where the user types, Chromium, and the desktop portal whose file
# chooser Chromium opens for uploads. Everything is on example.test names that resolve to localhost.
{ config, lib, pkgs, ... }:
let
  python = pkgs.python3;
in
{
  networking.hosts."127.0.0.1" = [
    "git.example.test"
    "dash.example.test"
    "notes.example.test"
  ];

  environment.systemPackages = [
    config.services.forgejo.package
    pkgs.chromium
    pkgs.curl
    pkgs.jq
    python
  ];

  services.forgejo = {
    enable = true;
    settings = {
      server = {
        DOMAIN = "git.example.test";
        HTTP_ADDR = "127.0.0.1";
        HTTP_PORT = 3000;
        ROOT_URL = "http://git.example.test:3000/";
      };
      service.DISABLE_REGISTRATION = true;
      # No mail, no update checks, no telemetry: the VM is offline.
      mailer.ENABLED = false;
      "cron.update_checker".ENABLED = false;
      session.COOKIE_SECURE = false;
    };
  };

  # The dashboard reads the OAuth client that setup.py registers in Forgejo, and the notes page keeps what the
  # user types; both write their state where check.py reads it.
  systemd.services.hyprmasq-bench-dash = {
    description = "Release dashboard, an OAuth client of Forgejo";
    wantedBy = [ "multi-user.target" ];
    serviceConfig.ExecStart = "${python}/bin/python3 ${./dash.py} /var/lib/hyprmasq-bench-dash";
  };
  systemd.services.hyprmasq-bench-notes = {
    description = "The notes page the user types into";
    wantedBy = [ "multi-user.target" ];
    serviceConfig.ExecStart = "${python}/bin/python3 ${./notes.py} /var/lib/hyprmasq-bench-notes";
  };

  # A desktop session starts graphical-session.target, which xdg-desktop-portal requires; setup.py starts this
  # target for it once Hyprland runs.
  systemd.user.targets.hyprmasq-bench-session = {
    description = "The benchmark's graphical session";
    bindsTo = [ "graphical-session.target" ];
    wants = [ "graphical-session-pre.target" ];
    after = [ "graphical-session-pre.target" ];
  };

  # Chromium asks the portal for its file chooser, which xdg-desktop-portal-gtk draws in a process of its own.
  xdg.portal = {
    enable = lib.mkForce true;
    extraPortals = [ pkgs.xdg-desktop-portal-gtk ];
    config.hyprland = {
      default = [ "gtk" ];
      "org.freedesktop.impl.portal.FileChooser" = [ "gtk" ];
    };
  };

  # The user's browser: no first-run screens, sign-in, sync, password prompts or promotions.
  programs.chromium = {
    enable = true;
    extraOpts = {
      BrowserSignin = 0;
      SyncDisabled = true;
      PasswordManagerEnabled = false;
      AutofillAddressEnabled = false;
      AutofillCreditCardEnabled = false;
      DefaultBrowserSettingEnabled = false;
      MetricsReportingEnabled = false;
      PromotionsEnabled = false;
      PrivacySandboxPromptEnabled = false;
      TranslateEnabled = false;
      SpellcheckEnabled = false;
      DownloadRestrictions = 0;
    };
  };

  environment.etc."hyprmasq-bench/forgejo/release-notes-1.4.md".source = ./release-notes-1.4.md;
  environment.etc."hyprmasq-bench/forgejo/check.py".source = ./check.py;
}
