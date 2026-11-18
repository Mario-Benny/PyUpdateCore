# PyUpdateCore

[English](README.md) · [接口协议](docs/IPC_PROTOCOL.md)

PyUpdateCore 是可打包为独立 `Updater.exe` 的 Windows 更新执行核心。Python、Java、C++、C#、Go、Rust 等调用方可通过 CLI 或 JSON Lines IPC 接入。

支持 Manifest/GitHub Release、`static` 与安全 `json_file` 版本来源、HTTPS/白名单/SHA-256/可选 Ed25519 校验、1/4/16 线程 Range 下载与断点续传、代理及跨语言 IPC。

```powershell
py -m pip install -e ".[dev]"
py updater.py --data-root .\runtime check --current-version 1.2.2
py updater.py serve --stdio --data-root .\runtime
```

调用方决定何时检查、下载和安装；本项目是更新 SDK，不提供 GUI 或软件商店。许可证见 [LICENSE](LICENSE)。
