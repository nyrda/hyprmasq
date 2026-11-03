# Security

hyprmasq is not a security boundary between you and the agent that drives
your windows: whoever can reach a window's input socket controls that window,
as the [security model](docs/automation.md#security-model) says. A driven
window that takes your focus or touches your clipboard is a bug, so please
open an issue.

A vulnerability is a way for another user, or anyone without access to your
session, to reach your sockets, screenshots or recordings, or to make
`hyprmasq-mcp` write outside its directories. Report it privately: on the
repository's Security tab, choose **Report a vulnerability**. Include
`hyprctl hyprmasq version`, `hyprctl version` and the steps that reproduce it.

Fixes go into the latest release.
