# Configuration / 配置

Use `--data-root` to select a runtime directory. The updater creates:

```text
data/userdata.json   application and update task
data/coredata.json   download, security, proxy and logging
data/state.json      runtime state only
download/            cache and resumable parts
logs/                runtime logs
```

`current_version` supports `static` with `value`, or `json_file` with a path relative to `userdata.json` and safe dotted fields such as `application.version`. CLI `--current-version` and IPC `check.params.current_version` override configuration for one task without writing files.

HTTPS and SHA-256 cannot be disabled. Configure every authorized manifest, API, redirect and asset host in `allowed_hosts`.
