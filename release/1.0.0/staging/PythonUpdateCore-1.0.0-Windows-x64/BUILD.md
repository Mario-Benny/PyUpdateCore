# Build

Use Python 3.11 or later to build from source:

```powershell
py -m pip install -e ".[dev]"
py -m pytest -q
py -m PyInstaller --noconfirm --onedir --name Updater updater.py
```

`--onedir` is the recommended SDK format: it avoids onefile extraction on every startup and ships all Python/HTTP dependencies in `_internal`. Use `--onefile` only when a single portable executable is more important than startup latency.

No Python installation is required by consumers of the Windows SDK package.
