# PyUpdateCore

A lightweight, standalone Windows update engine you can package as its own `Updater.exe`.
It supports GitHub Releases and signed manifests, multi-threaded resumable downloads, and
cross-language process communication.

[简体中文](README.zh-CN.md) · [Architecture](docs/ARCHITECTURE.md) · [Configuration](docs/CONFIGURATION.md) · [IPC protocol](docs/IPC_PROTOCOL.md) · [Security](docs/SECURITY.md) · [Release notes 1.0.1](docs/RELEASE_NOTES_1.0.1.md)

PyUpdateCore ships as an update **SDK**, not an end-user app. Python, Java, C++, C#, Go and Rust
callers integrate through the CLI or a JSON Lines IPC channel:

```powershell
py -m pip install -e ".[dev]"
py updater.py --data-root .\runtime check --current-version 1.2.2
py updater.py serve --stdio --data-root .\runtime
```

Highlights:

- **Providers** — GitHub Release (HTTPS + SHA-256 sidecar) and HTTPS manifest (SHA-256 + optional Ed25519).
- **Downloading** — adaptive 1 / 4 / 16 connection Range transfers with resume, retry, ETag and stall detection.
- **Security floor** — HTTPS, SHA-256 and a host allow-list are mandatory; redirects are checked hop by hop.
- **Version sources** — `static`, safe nested `json_file`, plus a per-call CLI/IPC override.

The caller decides when to check, download and install; this project never shows a GUI or acts as a
software store. License: [MIT](LICENSE).
