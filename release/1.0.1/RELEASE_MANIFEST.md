# PyUpdateCore v1.0.1 Final Release Manifest

## Release location

`release/1.0.1/` in this repository.

## Rebuild and validation status

The previously reported EXE and release archives were not present in the current workspace. The onefile EXE was therefore rebuilt from the current v1.0.1 source, then validated before packaging.

- Source version: `pyproject.toml` = `1.0.1`
- Engine version: `update_core/version.py` = `1.0.1`
- Full regression after rebuild: `70 passed in 24.03s`
- Final onefile IPC smoke test: `get_version` returned `PythonUpdateCore / 1.0.1 / protocol 1`; `shutdown` returned successfully.
- ZIP extraction verification: completed successfully for both archives.

## Release files

| File | Size (bytes) | SHA-256 |
| --- | ---: | --- |
| `PyUpdateCore-v1.0.1.exe` | 38,186,746 | `bcd5eb1b01ce3a9e40ae820a16dbe54830e32a612329acb6287dd15f06cbaad4` |
| `PyUpdateCore-v1.0.1-SDK-Windows-x64.zip` | 37,841,789 | `ba0f3411a9accf907563fd9bbdcb941520a139e11a41363dd39e04a8a2c6423a` |
| `PyUpdateCore-v1.0.1-Source.zip` | 67,188 | `0c52e1bbfa02f8d9f1dd85c211f4e83ce86ef6363a6384dc58829893367384fa` |

The same hashes are recorded in `SHA256SUMS.txt`.

## Package contents verified

### SDK archive

The SDK archive contains a root folder named `PyUpdateCore-v1.0.1-SDK` and includes:

- `Updater.exe`
- `LICENSE` (MIT)
- `docs/BUILD.md`
- `docs/CONFIGURATION.md`
- `docs/IPC_PROTOCOL.md`
- `docs/SECURITY.md`
- `docs/ARCHITECTURE.md`
- `docs/INTEGRATION.md`
- `docs/USER_GUIDE.md`
- Configuration examples under `config/`, including `userdata.example.json`, `coredata.example.json`, `userdata.github.example.json`, and `update.example.json`

### Source archive

The source archive contains a root folder named `PyUpdateCore-v1.0.1-Source` and includes:

- Complete `update_core/` source
- `updater.py`, `pyproject.toml`, and `bootstrap_test.py`
- Full `tests/` suite, including final cross-validation tests
- `examples/` for Python, Java, and C++
- `docs/`
- `build/PyUpdateCore-1.0.1.spec`
- `LICENSE` (MIT)

## Exclusions verified

Neither archive contains:

- `__pycache__` or `.pytest_cache`
- backup folders
- logs or test validation folders
- Git metadata
- user `userdata.json`, `coredata.json`, or `state.json`
- personal configuration or download cache
