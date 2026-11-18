# Security

PythonUpdateCore enforces HTTPS and SHA-256. Configuration supplies an explicit host allow-list; redirects are checked hop by hop. Installers are constrained to the updater cache, verified before and immediately before launch, and are never launched without the caller's explicit install action.

Manifest signatures can use Ed25519 trusted public keys. GitHub Release mode requires a unique installer asset and SHA-256 sidecar. Tokens, passwords, browser cookies and arbitrary shell commands are not accepted from configuration.

System proxy discovery supports environment variables and Windows Internet Settings. PAC is reported rather than executed, and unavailable proxies do not silently fall back to direct connections.
