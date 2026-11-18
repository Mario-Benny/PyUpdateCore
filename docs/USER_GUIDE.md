# PythonUpdateCore 使用说明

Configure application fields and `current_version`/`update_source` in `data/userdata.json`; configure download, security, network and logging in `data/coredata.json`. `current_version` supports `static` and safe `json_file` nested keys. An explicit CLI or IPC current version wins for one task only.

The standard interface is CLI or JSON Lines IPC. HTTPS and SHA-256 remain mandatory. Use native/system proxy by default; browser channels remain experimental. Self-update is a test-only Beta component and is never automatically loaded.
