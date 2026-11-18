# Integration

Start `Updater.exe serve --stdio` and exchange UTF-8 JSON Lines as documented in `IPC_PROTOCOL.md`. Python, Java and C++ examples are under `examples/`. CLI callers may use `--current-version`; IPC callers may set `check.params.current_version`.

For non-interactive hosts, consume structured response and event messages. Keep updater stdout dedicated to JSON; send host logs elsewhere. The host remains responsible for UI decisions and explicit installation authorization.
