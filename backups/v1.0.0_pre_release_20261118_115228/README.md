# PyUpdataCore

[中文说明](README.zh-CN.md) · [IPC protocol](docs/IPC_PROTOCOL.md)

PyUpdataCore is a configurable Windows update execution core packaged as a standalone `Updater.exe`. Python, Java, C++, C#, Go and Rust hosts can use CLI or JSON Lines IPC.

It supports Manifest and GitHub Release providers, `static` and safe `json_file` version sources, HTTPS/host allow-list/SHA-256/optional Ed25519 validation, 1/4/16 connection Range downloads with resume, proxy support, and cross-language IPC.

```powershell
py -m pip install -e ".[dev]"
py updater.py --data-root .\runtime check --current-version 1.2.2
py updater.py serve --stdio --data-root .\runtime
```

The host decides when to check, download and install. This project is an update SDK, not a GUI or software distribution platform. See [LICENSE](LICENSE) for MIT licensing.
