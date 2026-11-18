"""Versioned JSON Lines IPC with non-blocking control operations."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TextIO

from .config import load_split_config
from .errors import CancelledError, UpdaterError
from .layout import initialise_layout
from .storage import DEFAULT_STATE, merge_state, read_state_json
from .version import ENGINE_NAME, ENGINE_VERSION
from .models import InstallerAsset, UpdateManifest
from .workflow import UpdateWorkflow

PROTOCOL_VERSION = 1


def run_install_worker(root_text: str, installer_text: str, manifest_payload: str, process_id: int | None) -> int:
    """Independent handoff process. It may outlive the IPC parent while waiting."""
    root = Path(root_text)
    paths, _ = initialise_layout(root)
    try:
        data = json.loads(manifest_payload)
        installer_data = data["installer"]
        manifest = UpdateManifest(
            str(data["version"]), str(data["channel"]), bool(data["mandatory"]),
            InstallerAsset(str(installer_data["name"]), str(installer_data["url"]), int(installer_data["size"]), str(installer_data["sha256"])),
            str(data.get("published_at", "")), str(data.get("release_notes_url", "")),
        )
        config = load_split_config(paths["userdata"], paths["coredata"])
        UpdateWorkflow(config, paths["root"]).install(Path(installer_text), manifest, process_id)
        merge_state(paths["state"], {"status": "installer_started"})
        return 0
    except Exception as exc:
        merge_state(paths["state"], {"status": "install_failed", "last_error": {"code": type(exc).__name__, "message": str(exc)}})
        return 2


def _handoff_command(root: Path, installer: str, manifest: UpdateManifest, process_id: int | None) -> list[str]:
    payload = json.dumps({
        "version": manifest.version, "channel": manifest.channel, "mandatory": manifest.mandatory,
        "installer": {"name": manifest.installer.name, "url": manifest.installer.url, "size": manifest.installer.size, "sha256": manifest.installer.sha256},
        "published_at": manifest.published_at, "release_notes_url": manifest.release_notes_url,
    }, separators=(",", ":"))
    worker_args = ["--install-worker", "--data-root", str(root), "--installer-path", installer, "--manifest-json", payload]
    if process_id is not None:
        worker_args.extend(["--process-id", str(process_id)])
    if getattr(sys, "frozen", False):
        return [sys.executable, *worker_args]
    return [sys.executable, str(Path(__file__).resolve().parents[1] / "updater.py"), *worker_args]


def serve_stdio(root: Path, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
    paths, _ = initialise_layout(root)
    output_lock = threading.Lock()
    tasks_lock = threading.RLock()
    tasks: dict[str, dict[str, Any]] = {}
    closing = False
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="UpdaterIPC")

    def write(payload: dict[str, Any]) -> None:
        try:
            with output_lock:
                output_stream.write(json.dumps({"protocol_version": PROTOCOL_VERSION, **payload}, ensure_ascii=False, separators=(",", ":")) + "\n")
                output_stream.flush()
        except (BrokenPipeError, OSError):
            pass

    def visible(task: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in task.items() if key not in {"cancel", "future", "manifest", "process"}}

    def snapshot(task_id: str) -> dict[str, Any]:
        with tasks_lock:
            return visible(dict(tasks[task_id]))

    def finish(task_id: str) -> None:
        write({"type": "event", "event": "task_finished", "task_id": task_id, "data": snapshot(task_id)})

    def configured_workflow(task_id: str) -> tuple[UpdateWorkflow, Any]:
        config = load_split_config(paths["userdata"], paths["coredata"])
        workflow = UpdateWorkflow(
            config,
            paths["root"],
            event_callback=lambda event: write({"type": "event", "event": "progress", "task_id": task_id, "data": event}),
        )
        return workflow, config

    def require_confirmation(config: Any, action: str, params: dict[str, Any]) -> None:
        needed = config.confirm_before_download if action == "download" else config.confirm_before_install
        if needed and params.get("confirmed") is not True:
            raise UpdaterError(f"{action} 需要宿主软件明确传入 confirmed=true")

    def run_download(task_id: str, current: str | None, threads: int | None) -> None:
        try:
            flow, _ = configured_workflow(task_id)
            checked = flow.check(current)
            with tasks_lock:
                cancel = tasks[task_id]["cancel"]
            if cancel.is_set():
                raise CancelledError("下载已取消")
            if not checked.available or checked.manifest is None:
                with tasks_lock:
                    tasks[task_id].update(status="completed", result={"available": False})
            else:
                installer = flow.download(checked.manifest, threads=threads, cancel_event=cancel)
                with tasks_lock:
                    tasks[task_id].update(status="completed", manifest=checked.manifest, installer=str(installer), result={"available": True, "version": checked.manifest.version, "verified": True})
            merge_state(paths["state"], {"status": snapshot(task_id)["status"]})
        except CancelledError:
            with tasks_lock:
                tasks[task_id]["status"] = "cancelled"
            merge_state(paths["state"], {"status": "cancelled"})
        except Exception as exc:
            with tasks_lock:
                tasks[task_id].update(status="failed", error={"code": type(exc).__name__, "message": str(exc)})
            merge_state(paths["state"], {"status": "failed"})
        finish(task_id)

    def run_check(task_id: str, current: str | None) -> None:
        try:
            flow, _ = configured_workflow(task_id)
            checked = flow.check(current)
            with tasks_lock:
                if tasks[task_id]["cancel"].is_set():
                    tasks[task_id]["status"] = "cancelled"
                else:
                    tasks[task_id].update(status="completed", result={"current_version": checked.current_version, "available": checked.available, "remote_version": checked.manifest.version if checked.manifest else checked.current_version})
        except CancelledError:
            with tasks_lock:
                tasks[task_id]["status"] = "cancelled"
        except Exception as exc:
            with tasks_lock:
                tasks[task_id].update(status="failed", error={"code": type(exc).__name__, "message": str(exc)})
        finish(task_id)

    def refresh_install_tasks() -> None:
        with tasks_lock:
            for task in tasks.values():
                process = task.get("process")
                if task.get("status") == "installing" and process is not None and process.poll() is not None:
                    task["status"] = "installer_started" if process.returncode == 0 else "install_failed"

    try:
        for line in input_stream:
            request_id: str | None = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("protocol_version") != PROTOCOL_VERSION or request.get("type") != "request":
                    raise ValueError("请求必须是 protocol_version=1 的 request 对象")
                request_id = request.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    raise ValueError("request_id 必须是非空字符串")
                command, params = request.get("command"), request.get("params", {})
                if not isinstance(params, dict):
                    raise ValueError("params 必须是对象")
                if command == "initialize":
                    result = {"data_root": str(paths["root"]), "ready": paths["userdata"].is_file() and paths["coredata"].is_file()}
                elif command == "get_version":
                    result = {"engine_name": ENGINE_NAME, "engine_version": ENGINE_VERSION, "protocol_version": PROTOCOL_VERSION}
                elif command == "get_status":
                    refresh_install_tasks()
                    with tasks_lock:
                        task_snapshot = {key: visible(dict(value)) for key, value in tasks.items()}
                    result = {"state": read_state_json(paths["state"], default=DEFAULT_STATE), "tasks": task_snapshot}
                elif command == "check":
                    task_id = uuid.uuid4().hex
                    with tasks_lock:
                        tasks[task_id] = {"status": "running", "cancel": threading.Event()}
                        tasks[task_id]["future"] = executor.submit(run_check, task_id, params.get("current_version"))
                    result = {"task_id": task_id, "accepted": True}
                elif command == "download":
                    _, config = configured_workflow("pending")
                    require_confirmation(config, "download", params)
                    threads = params.get("threads")
                    if threads not in (None, 1, 4, 16):
                        raise ValueError("threads 只允许 1、4、16")
                    with tasks_lock:
                        if any(task.get("status") in {"pending", "running", "cancelling"} for task in tasks.values()):
                            raise UpdaterError("busy：已有活动下载任务")
                        task_id = uuid.uuid4().hex
                        tasks[task_id] = {"status": "running", "cancel": threading.Event()}
                        tasks[task_id]["future"] = executor.submit(run_download, task_id, params.get("current_version"), threads)
                    result = {"task_id": task_id, "accepted": True}
                elif command == "cancel":
                    task_id = params.get("task_id")
                    with tasks_lock:
                        task = tasks.get(task_id)
                        if not isinstance(task_id, str) or task is None:
                            raise ValueError("未知 task_id")
                        if task["status"] in {"completed", "installer_started", "install_failed", "failed", "cancelled"}:
                            result = {"task_id": task_id, "status": task["status"], "cancel_requested": False}
                        else:
                            task["status"] = "cancelling"
                            task["cancel"].set()
                            result = {"task_id": task_id, "status": "cancelling", "cancel_requested": True}
                elif command == "install":
                    _, config = configured_workflow("pending")
                    require_confirmation(config, "install", params)
                    task_id = params.get("task_id")
                    pid = params.get("process_id")
                    if pid is not None and (not isinstance(pid, int) or pid <= 0):
                        raise ValueError("process_id 必须为正整数")
                    with tasks_lock:
                        task = tasks.get(task_id)
                        if not isinstance(task_id, str) or task is None or task.get("status") != "completed" or "manifest" not in task:
                            raise UpdaterError("安装要求已完成且已验证的下载任务")
                        if task.get("install_requested"):
                            raise UpdaterError("该任务已请求安装")
                        task["install_requested"] = True
                        task["status"] = "installing"
                        installer, manifest = task["installer"], task["manifest"]
                    try:
                        creationflags = 0
                        if sys.platform == "win32":
                            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                        process = subprocess.Popen(
                            _handoff_command(paths["root"], installer, manifest, pid),
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            close_fds=True,
                            creationflags=creationflags,
                        )
                    except Exception:
                        with tasks_lock:
                            tasks[task_id].update(status="install_failed", error={"code": "InstallHandoffError", "message": "无法启动安装交接进程"})
                        raise
                    with tasks_lock:
                        tasks[task_id]["process"] = process
                    merge_state(paths["state"], {"status": "installing"})
                    write({"type": "event", "event": "install_handoff", "task_id": task_id, "data": {"status": "installing"}})
                    result = {"task_id": task_id, "accepted": True, "status": "installing"}
                elif command == "shutdown":
                    closing = True
                    with tasks_lock:
                        for task in tasks.values():
                            if task.get("status") in {"pending", "running", "cancelling"}:
                                task["status"] = "cancelling"
                                task["cancel"].set()
                    # Network workers are cooperatively cancelled and joined. The independent
                    # install handoff is deliberately not waited on, preventing a wait cycle.
                    executor.shutdown(wait=True, cancel_futures=True)
                    write({"type": "response", "request_id": request_id, "success": True, "result": {"shutdown_completed": True}})
                    return 0
                else:
                    raise ValueError("未知命令")
                write({"type": "response", "request_id": request_id, "success": True, "result": result})
            except (ValueError, UpdaterError, json.JSONDecodeError) as exc:
                write({"type": "response", "request_id": request_id, "success": False, "error": {"code": type(exc).__name__, "message": str(exc)}})
    finally:
        if not closing:
            with tasks_lock:
                for task in tasks.values():
                    if task.get("status") in {"pending", "running", "cancelling"}:
                        task["cancel"].set()
            executor.shutdown(wait=True, cancel_futures=True)
    return 0
