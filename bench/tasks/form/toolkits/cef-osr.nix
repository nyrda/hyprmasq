# The HTML questionnaire (form.html, form_html.py) in a CEF browser that renders off-screen, cefclient, on X11
# through Hyprland's XWayland. See vm.nix for what every toolkit's app does.
args:
let
  html = import ./html args;
in
{
  app = html.app "cef-osr" "x11" html.cefOffScreen;
}
