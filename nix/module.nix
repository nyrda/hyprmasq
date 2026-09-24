# NixOS module: run the Hyprland this plugin revision is built and tested against, install hyprmasq-input,
# and ship a Lua loader for the plugin.
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
  };

  config = lib.mkIf cfg.enable {
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
  };
}
