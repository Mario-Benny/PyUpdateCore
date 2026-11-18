# Architecture

`update_core` is UI-independent. `config` loads split configuration, `current_version` resolves the local version, providers obtain metadata, `downloader` performs guarded Range transfers, `verification` validates assets, `storage` persists state, and `workflow` coordinates check/download/install. `ipc` exposes the same operations over JSON Lines. Windows-specific process and installer operations are confined to `windows.py`.
