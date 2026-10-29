# The HTML questionnaire (form.html, form_html.py) in a CEF browser, cefsimple, on X11 through Hyprland's XWayland,
# as the Steam client runs. See vm.nix for what every toolkit's app does.
args:
let
  html = import ./html args;
in
{
  app = html.app "cef-x11" "x11" (html.cef "x11");
}
