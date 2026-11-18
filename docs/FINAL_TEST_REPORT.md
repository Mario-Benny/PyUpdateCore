# Final test report

- Python: 3.15.2.
- Direct dependencies: HTTPX 0.28.1; cryptography 46.0.7; PyInstaller 6.22.3.
- Automated suite: 44 passed, 0 failed, 0 skipped.
- Packaged onedir SDK: `Updater.exe --help` succeeded.
- Packaged IPC: `get_version` returned engine name `PythonUpdateCore`, version `1.0.0`, protocol version 1; shutdown succeeded.
- No separate no-Python Windows machine was available. The onedir package includes its runtime and was validated without invoking project source.
