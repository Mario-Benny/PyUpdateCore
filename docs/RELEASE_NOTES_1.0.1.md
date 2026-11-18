# PythonUpdateCore 1.0.1

Patch release for the standalone Windows update SDK. No new product surface, no GUI, no
protocol-version bump: IPC remains `protocol_version 1` and existing callers keep working.

## Security & correctness

- Ed25519 manifest signatures verified against `coredata` trusted keys before any download.
- `HTTPS` and `SHA-256` are hard floors and can no longer be disabled; `allowed_hosts` must be
  a non-empty list.
- Installer names are filtered against Windows reserved device names and illegal characters.
- Per-hop redirect validation now lives in the network layer and strips credentials on redirect.
- Private / loopback / link-local / reserved IP literals are blocked for manifest and asset URLs.

## Reliability

- Adaptive Range downloader no longer crashes on resume (operator-precedence fix); cancellation
  and resume are exercised end to end for 1 / 4 / 16 thread modes.
- Corrupted resume state now self-heals: segments that fail to deserialize are discarded and the
  transfer is replanned instead of re-crashing on every resume.
- GitHub provider wraps non-JSON API responses as a clean `DownloadError` (no bare traceback).
- Stale lock files are reclaimed only after checking the owner PID is dead.
- `merge_state` read-modify-write is guarded by a cross-thread lock so parallel IPC workers
  cannot drop each other's updates.

## IPC / CLI

- Control commands run fully in the background; a second concurrent download is rejected as
  `busy` rather than queued and failing.
- `check` is now report-only and no longer triggers a download, restoring `confirm_before_download`.
- `shutdown` cooperatively cancels active tasks and waits for the worker pool.
- Install re-verifies the asset hash after waiting for the main process, immediately before launch.
- `--json-output` emits a structured JSON error line on `OSError`, not plain text.

## Configuration changes

- Removed dead keys `download.mode` and `download.merge_after_download` (older files that still
  carry them are loaded and ignored).
- `installer.wait_timeout_seconds` is now configurable (default 600) instead of hard-coded.

## Proxy / network

- Environment proxies without a scheme are normalized; an invalid proxy now raises `DownloadError`
  instead of failing at client construction. PAC is reported, never executed.

## Testing

- 69 automated checks pass, 1 skipped (the onefile integration case runs only when a packaged
  EXE is present).

## Known limits (unchanged)

- No GUI; the caller owns UX and the explicit install decision.
- `trusted_manifest_keys` lives in local `coredata.json`; local write access is trusted (SDK
  ships per-application keys by design).
- Browser channels and self-update remain experimental; DNS-rebinding against an allowed host and
  slow-trickle stalls remain documented residual risks.
