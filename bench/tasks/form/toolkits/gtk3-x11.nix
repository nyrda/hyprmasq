# The GTK 3 questionnaire of gtk3.nix on X11, through Hyprland's XWayland: hyprmasq's X11 path with a common
# toolkit. See vm.nix for what every toolkit's app does.
args: import ./gtk3.nix (args // { x11 = true; })
