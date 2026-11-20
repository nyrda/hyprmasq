# Contributing

Contributions are welcome as pull requests against `main`.

hyprmasq is developed with Nix on x86_64 Linux with KVM. A pull request that
changes the plugin, its tools, the MCP server, the NixOS module or the test
passes the local test gate, `./dev.sh gate`, on its head commit before it is
opened. Changes to docs or `bench/` alone skip the gate, and `mcp/` has a fast
Go test suite (`go test ./...`) for local iteration without booting the test VM.

[Development](docs/development.md#contributing) has the steps, the checks and
the test gate, and the pull request template asks for their results.

Bug reports are welcome from any setup: open an issue with the versions the
form asks for. Report a vulnerability privately, as [Security](SECURITY.md)
says.
