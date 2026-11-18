# PythonUpdateCore 1.0.0

First formal release of the standalone Windows update SDK.

- Providers: GitHub Release and HTTPS manifest.
- Downloading: 1/4/16 connection Range mode, resume, retries, ETag, size and SHA-256 validation.
- Security: HTTPS and SHA-256 enforcement, host allow-list, optional Ed25519 manifest verification.
- Network: direct, system and custom proxy policy.
- Integration: CLI and JSON Lines IPC v1; Python, Java and C++ examples.
- Version sources: static, json_file and explicit caller override.

Known limits: no GUI; caller owns UX and installation decision. Browser channels and self-update remain experimental, and PAC files are detected rather than executed.
