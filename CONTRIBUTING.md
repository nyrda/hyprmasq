# Contributing

Contributions are welcome as pull requests against `main`.

hyprmasq is developed with Nix on x86_64 Linux with KVM. The build, the
checks and the test gate all run through the flake, and there is no other
supported setup. A pull request passes the test gate, `./dev.sh gate`, on its
head commit before it is opened, unless it changes none of the plugin, its
tools, the MCP server, the NixOS module and the test.

[Development](docs/development.md#contributing) has the steps, the checks and
the test gate, and the pull request template asks for their results.

A bug goes to the issues, with the versions that the form asks for. A
vulnerability is reported privately, as [Security](SECURITY.md) says.
