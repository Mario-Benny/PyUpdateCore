"""Safe adaptive HTTP downloader with resumable Range segments."""
from __future__ import annotations

import hashlib
import queue
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import UpdaterConfig
from .errors import CancelledError, DownloadError, ManifestError, RangeUnsupportedError, VerificationError
from .manifest import validate_url
from .network import SafeHttpClient
from .models import InstallerAsset
from .storage import atomic_write_json, merge_state, read_json
from .verification import ensure_disk_space, verify_installer

MANIFEST_MAX_BYTES = 256 * 1024
_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$", re.I)
_TRANSIENT = {408, 429, 500, 502, 503, 504}
EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class Segment:
    segment_id: int
    start: int
    end: int
    downloaded: int = 0
    completed: bool = False
    retry_count: int = 0
    last_error: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start + 1


class _Events:
    """Callback delivery runs on a daemon so network workers never block on UI/IPC."""
    def __init__(self, callback: EventCallback | None) -> None:
        self.callback, self.q = callback, queue.SimpleQueue()
        if callback:
            threading.Thread(target=self._run, daemon=True, name="updater-events").start()

    def emit(self, status: str, **data: Any) -> None:
        if self.callback:
            self.q.put({"status": status, **data})

    def _run(self) -> None:
        while True:
            event = self.q.get()
            if event is None:
                return
            try:
                self.callback(event)
            except Exception:
                pass

    def close(self) -> None:
        if self.callback:
            self.q.put(None)


class Downloader:
    def __init__(self, config: UpdaterConfig, downloads_dir: Path, state_path: Path, progress: Callable[[int, int], None] | None = None, event_callback: EventCallback | None = None, cancel_event: threading.Event | None = None) -> None:
        self.config, self.downloads_dir, self.state_path = config, downloads_dir, state_path
        self.progress, self.events, self.http = progress, _Events(event_callback), SafeHttpClient(config)
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._current_segments: list[Segment] = []
        self._received = self._active = 0
        self._started = 0.0
        self.cancel_event = cancel_event

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise CancelledError("下载已取消")

    def _wait_retry(self, seconds: float) -> None:
        if self.cancel_event is not None and self.cancel_event.wait(seconds):
            raise CancelledError("下载已取消")
        self._check_cancel()

    def close(self) -> None:
        self.http.close()
        self.events.close()

    def fetch_manifest(self) -> bytes:
        if not self.config.manifest_url:
            raise ManifestError("update_source.manifest_url 尚未填写")
        content = bytearray()
        try:
            with self.http.stream(self.config.manifest_url, {"Accept": "application/json", "Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    raise DownloadError(f"服务器返回 HTTP {response.status_code}")
                for chunk in response.iter_raw():
                    content.extend(chunk)
                    if len(content) > MANIFEST_MAX_BYTES:
                        raise DownloadError("更新清单超过安全大小限制")
        except httpx.HTTPError as exc:
            raise DownloadError("网络请求失败") from exc
        return bytes(content)

    def download(self, asset: InstallerAsset, *, threads: int | None = None) -> Path:
        self._check_cancel()
        requested = self.config.default_threads if threads is None else threads
        if requested not in {1, 4, 16} or requested not in self.config.allowed_threads:
            raise DownloadError("线程数只允许配置允许的 1、4、16")
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        if Path(asset.name).name != asset.name or asset.name in {"", ".", ".."}:
            raise DownloadError("安装包文件名不安全")
        final = self.downloads_dir / asset.name
        if final.resolve().parent != self.downloads_dir.resolve():
            raise DownloadError("安装包路径越界")
        if final.exists():
            try:
                verify_installer(final, asset, require_sha256=self.config.require_sha256)
                return final
            except VerificationError:
                final.unlink(missing_ok=True)
        task_id = hashlib.sha256(f"{asset.url}\0{asset.size}\0{asset.sha256}".encode()).hexdigest()[:16]
        task_dir = self.downloads_dir / f"task_{task_id}"
        task_dir.mkdir(parents=True, exist_ok=True)
        self._started = time.monotonic()
        self.events.emit("download_started", task_id=task_id, downloaded_bytes=0, total_bytes=asset.size, speed_bytes_per_second=None, progress_percent=0.0, estimated_remaining_seconds=None, active_connections=0, download_mode="single" if requested == 1 else f"range_{requested}")
        try:
            if requested == 1 or asset.size < self.config.min_segment_size_bytes * requested:
                if requested != 1:
                    self.events.emit("download_fallback", task_id=task_id, reason="file_too_small", download_mode="single")
                return self._single(asset, final, task_id, task_dir)
            identity = self._probe(asset)
            if identity is None:
                if not self.config.fallback_to_single:
                    raise RangeUnsupportedError("服务器不支持有效 Range 下载")
                self.events.emit("download_fallback", task_id=task_id, reason="range_not_supported", download_mode="single")
                self._clear_parts(task_dir)
                return self._single(asset, final, task_id, task_dir)
            total, etag = identity
            if total != asset.size:
                raise DownloadError("远端资源大小与可信清单不一致")
            try:
                return self._segments(asset, final, task_id, task_dir, requested, etag)
            except RangeUnsupportedError as exc:
                if not self.config.fallback_to_single:
                    raise
                self.events.emit("download_fallback", task_id=task_id, reason=str(exc), download_mode="single")
                self._clear_parts(task_dir)
                return self._single(asset, final, task_id, task_dir)
        except CancelledError:
            self._task_state(task_dir, asset, requested, "", None)
            self._root_state(task_id, {"status": "cancelled", "received": self._received, "task_dir": str(task_dir)})
            self.events.emit("download_cancelled", task_id=task_id, downloaded_bytes=self._received, total_bytes=asset.size)
            raise
        except Exception as exc:
            self._root_state(task_id, {"status": "failed", "last_error": str(exc)})
            self.events.emit("download_failed", task_id=task_id, error=str(exc), downloaded_bytes=self._received, total_bytes=asset.size, speed_bytes_per_second=None, progress_percent=None, estimated_remaining_seconds=None, active_connections=self._active, download_mode="unknown")
            raise exc if isinstance(exc, DownloadError) else DownloadError(f"下载失败：{exc}") from exc
        finally:
            self.close()

    def _probe(self, asset: InstallerAsset) -> tuple[int, str] | None:
        self._check_cancel()
        with self.http.stream(asset.url, {"Range": "bytes=0-0", "Accept-Encoding": "identity"}) as response:
            if response.status_code in {200, 416}:
                return None
            if response.status_code in _TRANSIENT:
                raise DownloadError(f"Range 探测暂时失败（HTTP {response.status_code}）")
            if response.status_code != 206:
                raise DownloadError(f"Range 探测返回 HTTP {response.status_code}")
            body = bytearray()
            for chunk in response.iter_raw():
                self._check_cancel(); body.extend(chunk)
            match = _RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if not match or match.groups()[:2] != ("0", "0") or len(body) != 1 or response.headers.get("Content-Length") not in {None, "1"}:
                raise RangeUnsupportedError("Range 探测响应不符合协议")
            tag = response.headers.get("ETag", "")
            return int(match.group(3)), tag if tag and not tag.startswith("W/") else ""

    def _single(self, asset: InstallerAsset, final: Path, task_id: str, task_dir: Path) -> Path:
        part, state = task_dir / "single.part", read_json(task_dir / "state.json")
        resource = state.get("resource", {})
        if not isinstance(resource, dict): resource = {}
        compatible = resource.get("url") == asset.url and resource.get("size") == asset.size and resource.get("sha256") == asset.sha256
        base = part.stat().st_size if self.config.resume and part.exists() else 0
        if not compatible or (base and self.config.verify_etag and not resource.get("etag")):
            part.unlink(missing_ok=True); base = 0
        if base > asset.size:
            part.unlink(missing_ok=True); base = 0
        ensure_disk_space(self.downloads_dir, asset.size - base)
        for attempt in range(self.config.retry_count + 1):
            self._check_cancel()
            try:
                self._single_state(task_dir, asset, base, task_id, resource.get("etag", ""))
                headers = {"Accept-Encoding": "identity"}
                if base:
                    headers["Range"] = f"bytes={base}-"
                    if state.get("resource", {}).get("etag"):
                        headers["If-Range"] = state["resource"]["etag"]
                with self._connection():
                    with self.http.stream(asset.url, headers) as response:
                        if base:
                            if response.status_code == 200:
                                raise RangeUnsupportedError("服务器忽略续传请求")
                            self._range_headers(response, base, asset.size - 1, asset.size)
                            mode = "ab"
                        else:
                            if response.status_code == 206:
                                raise RangeUnsupportedError("Content-Range 不应出现在完整下载响应中")
                            if response.status_code in _TRANSIENT:
                                error = DownloadError(f"请求暂时失败（HTTP {response.status_code}）")
                                error.retry_after = response.headers.get("Retry-After")
                                raise error
                            if response.status_code != 200:
                                raise DownloadError(f"服务器返回 HTTP {response.status_code}")
                            self._length(response, asset.size); mode = "wb"
                        response_etag = response.headers.get("ETag", "")
                        if base and self.config.verify_etag and resource.get("etag") and response_etag and resource["etag"] != response_etag:
                            raise RangeUnsupportedError("续传资源 ETag 已变化")
                        if response_etag and not response_etag.startswith("W/"):
                            resource = {**resource, "etag": response_etag}
                            self._single_state(task_dir, asset, base, task_id, response_etag)
                        received = base
                        with part.open(mode) as out:
                            for chunk in response.iter_raw():
                                self._check_cancel()
                                received += len(chunk)
                                if received > asset.size: raise DownloadError("安装包超过声明大小")
                                out.write(chunk); self._progress(task_id, received, asset.size, "single")
                        if received != asset.size: raise DownloadError("实际接收字节数与安装包大小不一致")
                self._single_state(task_dir, asset, received, task_id, response_etag if response_etag and not response_etag.startswith("W/") else "")
                verify_installer(part, asset, require_sha256=self.config.require_sha256)
                part.replace(final)
                self._root_state(task_id, {"status": "completed", "path": str(final), "received": asset.size})
                self._completed(task_id, asset, "single")
                return final
            except CancelledError: raise
            except VerificationError:
                part.unlink(missing_ok=True); base = 0
                if attempt >= self.config.retry_count: raise
            except RangeUnsupportedError:
                part.unlink(missing_ok=True); base = 0
                if attempt >= self.config.retry_count: raise
            except (httpx.HTTPError, OSError, DownloadError) as exc:
                base = part.stat().st_size if part.exists() else 0
                if attempt >= self.config.retry_count: raise DownloadError(str(exc)) from exc
                self._retry(task_id, None, attempt + 1, str(exc), base, asset.size, "single")
                self._wait_retry(self._retry_delay(exc, attempt))
        raise DownloadError("单连接下载失败")

    def _segments(self, asset: InstallerAsset, final: Path, task_id: str, task_dir: Path, threads: int, etag: str) -> Path:
        self._check_cancel()
        segments = self._load_plan(task_dir, asset, threads, etag)
        self._current_segments = segments
        self._received = sum(item.downloaded for item in segments)
        ensure_disk_space(self.downloads_dir, sum(item.length-item.downloaded for item in segments) + asset.size)
        self._root_state(task_id, {"status": "downloading", "mode": f"range_{threads}", "received": self._received, "total": asset.size, "task_dir": str(task_dir)})
        with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="updater-download") as pool:
            futures = [pool.submit(self._segment, asset, task_id, task_dir, item, etag, threads) for item in segments if not item.completed and (self.cancel_event is None or not self.cancel_event.is_set())]
            for future in as_completed(futures): future.result()
        self._check_cancel()
        self.events.emit("download_merging", task_id=task_id, downloaded_bytes=asset.size, total_bytes=asset.size, speed_bytes_per_second=self._speed(), progress_percent=100.0, estimated_remaining_seconds=0.0, active_connections=0, download_mode=f"range_{threads}")
        merged = task_dir / "merged.tmp"
        with merged.open("wb") as out:
            for item in sorted(segments, key=lambda value: value.start):
                part = task_dir / f"part_{item.segment_id:03d}.tmp"
                if not item.completed or not part.is_file() or part.stat().st_size != item.length: raise DownloadError(f"分片 {item.segment_id} 不完整")
                with part.open("rb") as source: shutil.copyfileobj(source, out, length=1024*1024)
        if merged.stat().st_size != asset.size: raise DownloadError("合并后文件大小不一致")
        self.events.emit("download_verifying", task_id=task_id, downloaded_bytes=asset.size, total_bytes=asset.size, speed_bytes_per_second=None, progress_percent=100.0, estimated_remaining_seconds=0.0, active_connections=0, download_mode=f"range_{threads}")
        verify_installer(merged, asset, require_sha256=self.config.require_sha256)
        merged.replace(final)
        if self.config.delete_parts_after_merge:
            for item in segments: (task_dir / f"part_{item.segment_id:03d}.tmp").unlink(missing_ok=True)
        self._root_state(task_id, {"status": "completed", "path": str(final), "received": asset.size})
        self._completed(task_id, asset, f"range_{threads}")
        return final

    def _segment(self, asset: InstallerAsset, task_id: str, task_dir: Path, item: Segment, etag: str, threads: int) -> None:
        path = task_dir / f"part_{item.segment_id:03d}.tmp"
        for attempt in range(self.config.retry_count + 1):
            self._check_cancel()
            start = item.start + item.downloaded
            if start > item.end:
                item.completed = True; self._task_state(task_dir, asset, threads, etag, None); return
            try:
                headers = {"Range": f"bytes={start}-{item.end}", "Accept-Encoding": "identity"}
                if etag: headers["If-Range"] = etag
                with self._connection():
                    with self.http.stream(asset.url, headers) as response:
                        if response.status_code == 200: raise RangeUnsupportedError("服务器忽略分片 Range 请求")
                        self._range_headers(response, start, item.end, asset.size)
                        received = item.downloaded
                        with path.open("ab") as out:
                            for chunk in response.iter_raw():
                                self._check_cancel()
                                received += len(chunk)
                                if received > item.length: raise DownloadError("分片接收字节超出边界")
                                out.write(chunk); item.downloaded = received; self._progress(task_id, None, asset.size, f"range_{threads}", len(chunk))
                        if received != item.length: raise DownloadError("分片实际长度与计划区间不一致")
                item.completed, item.last_error = True, ""; self._task_state(task_dir, asset, threads, etag, None); return
            except (RangeUnsupportedError, CancelledError): raise
            except (httpx.HTTPError, OSError, DownloadError) as exc:
                item.retry_count, item.last_error = item.retry_count + 1, str(exc); self._task_state(task_dir, asset, threads, etag, None)
                if attempt >= self.config.retry_count: raise DownloadError(f"分片 {item.segment_id} 下载失败：{exc}") from exc
                self._retry(task_id, item.segment_id, item.retry_count, str(exc), self._received, asset.size, f"range_{threads}")
                self._wait_retry(self._retry_delay(exc, attempt))

    def _load_plan(self, task_dir: Path, asset: InstallerAsset, threads: int, etag: str) -> list[Segment]:
        state = read_json(task_dir / "state.json"); resource = state.get("resource", {})
        reuse = isinstance(resource, dict) and all((resource.get("url") == asset.url, resource.get("size") == asset.size, resource.get("sha256") == asset.sha256, resource.get("threads") == threads))
        if reuse and self.config.verify_etag and ((resource.get("etag") and etag and resource["etag"] != etag) or (not etag and any(item.get("downloaded", 0) for item in state.get("segments", []) if isinstance(item, dict)))): reuse = False
        raw = state.get("segments", []) if reuse else []
        segments: list[Segment] = []
        if isinstance(raw, list):
            try:
                segments = [Segment(**item) for item in raw if isinstance(item, dict)]
            except (TypeError, ValueError):
                segments = []
        if not self._valid_plan(segments, asset.size, threads):
            self._clear_parts(task_dir); segments = self._plan(asset.size, threads)
        for item in segments:
            part = task_dir / f"part_{item.segment_id:03d}.tmp"; size = part.stat().st_size if part.is_file() else 0
            if size > item.length: part.unlink(missing_ok=True); size = 0
            item.downloaded, item.completed = size, size == item.length
        self._task_state(task_dir, asset, threads, etag, segments)
        return segments

    @staticmethod
    def _plan(total: int, threads: int) -> list[Segment]:
        base, extra, start, result = total // threads, total % threads, 0, []
        for index in range(threads):
            length = base + (index < extra); result.append(Segment(index, start, start + length - 1)); start += length
        return result

    @staticmethod
    def _valid_plan(parts: list[Segment], total: int, threads: int) -> bool:
        return len(parts) == threads and bool(parts) and parts[0].start == 0 and parts[-1].end == total-1 and all(first.end+1 == second.start for first, second in zip(parts, parts[1:]))

    @staticmethod
    def _length(response: httpx.Response, expected: int) -> None:
        value = response.headers.get("Content-Length")
        if value is not None and (not value.isdigit() or int(value) != expected): raise DownloadError("Content-Length 与更新清单不一致")

    def _range_headers(self, response: httpx.Response, start: int, end: int, total: int) -> None:
        if response.status_code == 416: raise RangeUnsupportedError("服务器拒绝 Range 请求")
        if response.status_code in _TRANSIENT:
            error = DownloadError(f"Range 请求暂时失败（HTTP {response.status_code}）")
            error.retry_after = response.headers.get("Retry-After")
            raise error
        match = _RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if response.status_code != 206 or not match or tuple(map(int, match.groups())) != (start, end, total): raise RangeUnsupportedError("Content-Range 与请求区间不一致")
        self._length(response, end-start+1)

    def _task_state(self, task_dir: Path, asset: InstallerAsset, threads: int, etag: str, segments: list[Segment] | None) -> None:
        with self._state_lock:
            values = [asdict(item) for item in (segments if segments is not None else self._current_segments)]
            atomic_write_json(task_dir / "state.json", {"schema_version": 1, "resource": {"url": asset.url, "size": asset.size, "sha256": asset.sha256, "etag": etag, "threads": threads}, "segments": values, "updated_at": int(time.time())})

    def _single_state(self, task_dir: Path, asset: InstallerAsset, downloaded: int, task_id: str, etag: str) -> None:
        atomic_write_json(task_dir / "state.json", {"schema_version": 1, "resource": {"url": asset.url, "size": asset.size, "sha256": asset.sha256, "etag": etag, "threads": 1}, "single_downloaded": downloaded})
        self._root_state(task_id, {"status": "downloading", "mode": "single", "received": downloaded, "total": asset.size, "task_dir": str(task_dir)})

    def _root_state(self, task_id: str, payload: dict[str, Any]) -> None:
        merge_state(self.state_path, {"status": payload.get("status", "downloading"), "downloads": {task_id: payload}})

    def _progress(self, task_id: str, absolute: int | None, total: int, mode: str, delta: int = 0) -> None:
        with self._lock:
            self._received = absolute if absolute is not None else self._received + delta
            received, elapsed, active = min(self._received, total), max(time.monotonic()-self._started, .001), self._active
        speed = received / elapsed
        if self.progress: self.progress(received, total)
        self.events.emit("download_progress", task_id=task_id, downloaded_bytes=received, total_bytes=total, speed_bytes_per_second=speed, progress_percent=received*100/total, estimated_remaining_seconds=(total-received)/speed if speed else None, active_connections=active, download_mode=mode)

    def _retry(self, task_id: str, segment_id: int | None, count: int, reason: str, received: int, total: int, mode: str) -> None:
        self.events.emit("download_retrying", task_id=task_id, segment_id=segment_id, retry_count=count, reason=reason, downloaded_bytes=received, total_bytes=total, active_connections=self._active, download_mode=mode)

    def _completed(self, task_id: str, asset: InstallerAsset, mode: str) -> None:
        self.events.emit("download_completed", task_id=task_id, downloaded_bytes=asset.size, total_bytes=asset.size, speed_bytes_per_second=self._speed(), progress_percent=100.0, estimated_remaining_seconds=0.0, active_connections=0, download_mode=mode)

    def _speed(self) -> float | None:
        return self._received / (time.monotonic()-self._started) if self._started and time.monotonic() > self._started else None

    @staticmethod
    def _retry_delay(exc: Exception, attempt: int) -> float:
        value = getattr(exc, "retry_after", None)
        if isinstance(value, str) and value.isdigit():
            return min(float(value), 60.0)
        return min(2 ** attempt, 8)

    @contextmanager
    def _connection(self) -> Iterator[None]:
        with self._lock: self._active += 1
        try: yield
        finally:
            with self._lock: self._active -= 1

    @staticmethod
    def _clear_parts(task_dir: Path) -> None:
        for item in task_dir.glob("part_*.tmp"): item.unlink(missing_ok=True)
        for name in ("single.part", "merged.tmp", "state.json"): (task_dir / name).unlink(missing_ok=True)
