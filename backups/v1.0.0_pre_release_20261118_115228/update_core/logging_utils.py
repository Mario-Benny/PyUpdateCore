from __future__ import annotations

import json
import time
from pathlib import Path


def write_log(log_dir: Path, event: str, **fields: object) -> None:
    """Minimal structured log; callers provide no secrets or temporary signed URLs."""
    log_dir.mkdir(parents=True, exist_ok=True)
    payload = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
    with (log_dir / "updater.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def clean_logs(log_dir: Path, retention_days: int) -> None:
    cutoff = time.time() - retention_days * 86400
    for path in log_dir.glob("*.jsonl"):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
