# NixOS module: run the Hyprland this plugin revision is built and tested against, install hyprmasq-input,
# ship a Lua loader for the plugin, and write hyprmasq-mcp's settings.
self:
{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.programs.hyprmasq;
  packages = self.packages.${pkgs.stdenv.hostPlatform.system};
in
{
  options.programs.hyprmasq = {
    enable = lib.mkEnableOption "the hyprmasq Hyprland plugin together with its matching Hyprland";

    package = lib.mkOption {
      type = lib.types.package;
      default = packages.hyprmasq;
      defaultText = lib.literalExpression "hyprmasq.packages.\${system}.hyprmasq";
      description = "The plugin package. It has to be built against the Hyprland in programs.hyprland.package.";
    };

    mcpSettings = lib.mkOption {
      type = (pkgs.formats.toml { }).type;
      default = { };
      example = lib.literalExpression ''
        {
          max-image-size = 1280;
          record = {
            dir = "~/Videos/hyprmasq";
            gif = true;
          };
        }
      '';
      description = ''
        Settings of hyprmasq-mcp, written to /etc/xdg/hyprmasq/mcp.toml. A user's own
        ~/.config/hyprmasq/mcp.toml is read instead. See docs/mcp.md for the keys.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        # outPath identifies the exact compositor build and is available without building it.
        assertion = cfg.package ? hyprland && config.programs.hyprland.package.outPath == cfg.package.hyprland.outPath;
        message = ''
          hyprmasq was built against Hyprland ${cfg.package.hyprland.version or "unknown"} (${cfg.package.hyprland.outPath or "unknown"}), but programs.hyprland.package is ${config.programs.hyprland.package.version} (${config.programs.hyprland.package.outPath}).
          Set programs.hyprmasq.package = pkgs.callPackage "''${hyprmasq}/nix/package.nix" { hyprland = yourPackage; }; to build the plugin against your programs.hyprland.package, or use the module's default Hyprland package.
        '';
      }
    ];
    programs.hyprland = {
      enable = lib.mkDefault true;
      # Both come from the nixpkgs revision locked by the hyprmasq flake, not from the system's nixpkgs.
      package = lib.mkDefault packages.hyprland;
      portalPackage = lib.mkDefault packages.xdg-desktop-portal-hyprland;
    };

    # Provides hyprmasq-input.
    environment.systemPackages = [ cfg.package ];

    # Load it from hyprland.lua: dofile("/etc/xdg/hypr/hyprmasq_plugin.lua")
    environment.etc."xdg/hypr/hyprmasq_plugin.lua".text = ''
      hl.plugin.load("${cfg.package}/lib/hyprmasq.so")
    '';

    environment.etc."xdg/hyprmasq/mcp.toml" = lib.mkIf (cfg.mcpSettings != { }) {
      source = (pkgs.formats.toml { }).generate "hyprmasq-mcp.toml" cfg.mcpSettings;
    };
  };
}
