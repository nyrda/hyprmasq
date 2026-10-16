# The Thunderbird task's machine: Dovecot with Mara's inbox, a fake SMTP server, and Thunderbird. Everything is
# on example.test, which resolves to localhost. The test CA is in certs/ and trusted by Thunderbird's profile,
# so the account wizard finds the servers without certificate dialogs.
{ config, pkgs, ... }:
let
  certs = ./certs;
  dovecot = config.services.dovecot2.package;
  maildir = pkgs.runCommand "hyprmasq-bench-maildir" { } ''
    ${pkgs.python3}/bin/python3 ${./mails.py} $out
  '';
  smtpPython = pkgs.python3.withPackages (ps: [ ps.aiosmtpd ]);
in
{
  networking.hosts."127.0.0.1" = [
    "example.test"
    "imap.example.test"
    "smtp.example.test"
    "mail.example.test"
  ];

  environment.systemPackages = [
    pkgs.thunderbird
    pkgs.nss.tools
    pkgs.python3
  ];

  # Mara's password, for both forms of her login name: Dovecot strips the domain first.
  environment.etc."dovecot/bench-users".text = ''
    mara:{PLAIN}Lindqvist-2026::::::
  '';

  services.dovecot2 = {
    enable = true;
    # The default follows system.stateVersion, which picks 2.3 for this VM.
    package = pkgs.dovecot;
    settings = {
      dovecot_config_version = dovecot.version;
      dovecot_storage_version = dovecot.version;
      protocols.imap = true;
      ssl = "required";
      ssl_server_cert_file = "${certs}/server.pem";
      ssl_server_key_file = "${certs}/server.key";
      auth_mechanisms = [ "plain" "login" ];
      auth_username_format = "%{user | username | lower}";
      mail_driver = "maildir";
      mail_home = "/var/lib/mail/%{user}";
      mail_path = "~/Maildir";
      mail_uid = "vmail";
      mail_gid = "vmail";
      "passdb passwd-file".passwd_file_path = "/etc/dovecot/bench-users";
      "userdb static".fields = {
        uid = "vmail";
        gid = "vmail";
        home = "/var/lib/mail/%{user}";
      };
      "namespace inbox" = {
        inbox = true;
        separator = "/";
        "mailbox Sent" = {
          special_use = "\\Sent";
          auto = "subscribe";
        };
        "mailbox Drafts" = {
          special_use = "\\Drafts";
          auto = "subscribe";
        };
        "mailbox Trash" = {
          special_use = "\\Trash";
          auto = "subscribe";
        };
        "mailbox Junk" = {
          special_use = "\\Junk";
          auto = "subscribe";
        };
      };
    };
  };

  systemd.services.hyprmasq-bench-maildir = {
    description = "Seed Mara's inbox";
    wantedBy = [ "multi-user.target" ];
    before = [ "dovecot.service" ];
    requiredBy = [ "dovecot.service" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      install -d -o vmail -g vmail -m 0700 /var/lib/mail/mara
      rm -rf /var/lib/mail/mara/Maildir
      cp -r ${maildir} /var/lib/mail/mara/Maildir
      chown -R vmail:vmail /var/lib/mail/mara/Maildir
      chmod -R u+w /var/lib/mail/mara/Maildir
    '';
  };

  systemd.services.hyprmasq-bench-smtp = {
    description = "Fake SMTP submission server";
    wantedBy = [ "multi-user.target" ];
    serviceConfig.ExecStart = "${smtpPython}/bin/python3 ${./smtp.py} ${certs}/server.pem ${certs}/server.key /var/lib/hyprmasq-bench-smtp";
  };

  environment.etc."hyprmasq-bench/thunderbird/user.js".source = ./user.js;
  environment.etc."hyprmasq-bench/thunderbird/ca.pem".source = "${certs}/ca.pem";
  environment.etc."hyprmasq-bench/thunderbird/check.py".source = ./check.py;
}
