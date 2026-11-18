"""Versioned JSON Lines IPC; stdout is reserved exclusively for protocol data."""
from __future__ import annotations

import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TextIO

from .config import load_split_config
from .errors import UpdaterError
from .layout import initialise_layout
from .storage import merge_state, read_json
from .workflow import UpdateWorkflow
from .version import ENGINE_NAME, ENGINE_VERSION

PROTOCOL_VERSION = 1


def serve_stdio(root: Path, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
    paths, _ = initialise_layout(root)
    write_lock = threading.Lock()
    tasks: dict[str, dict[str, Any]] = {}

    def write(payload: dict[str, Any]) -> None:
        try:
            with write_lock:
                output_stream.write(json.dumps({"protocol_version": PROTOCOL_VERSION, **payload}, ensure_ascii=False, separators=(",", ":")) + "\n")
                output_stream.flush()
        except (BrokenPipeError, OSError):
            pass

    def workflow(task_id: str | None = None) -> UpdateWorkflow:
        config = load_split_config(paths["userdata"], paths["coredata"])
        return UpdateWorkflow(config, paths["root"], event_callback=lambda payload: write({"type": "event", "event": "progress", "task_id": task_id, "data": payload}))

    def run_download(task_id: str, current_version: str | None, threads: int | None) -> None:
        task = tasks[task_id]
        try:
            checked = workflow(task_id).check(current_version)
            if not checked.available or checked.manifest is None:
                task.update(status="completed", result={"available": False})
            elif task["cancel"].is_set():
                task.update(status="cancelled")
            else:
                task["installer"] = str(workflow(task_id).download(checked.manifest, threads=threads))
                task.update(status="completed", result={"available": True, "version": checked.manifest.version})
            merge_state(paths["state"], {"status": task["status"]})
            write({"type": "event", "event": "task_finished", "task_id": task_id, "data": {k: v for k, v in task.items() if k != "cancel"}})
        except Exception as exc:
            task.update(status="failed", error={"code": type(exc).__name__, "message": str(exc)})
            merge_state(paths["state"], {"status": "failed"})
            write({"type": "event", "event": "task_finished", "task_id": task_id, "data": {"status": "failed", "error": task["error"]}})

    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="UpdaterIPC")
    try:
        for line in input_stream:
            request_id: str | None = None
            try:
                request = json.loads(line)
                if not isinstance(request, dict) or request.get("protocol_version") != PROTOCOL_VERSION or request.get("type") != "request": raise ValueError("请求必须是 protocol_version=1 的 request 对象")
                request_id = request.get("request_id")
                if not isinstance(request_id, str) or not request_id: raise ValueError("request_id 必须是非空字符串")
                command, params = request.get("command"), request.get("params", {})
                if not isinstance(params, dict): raise ValueError("params 必须是对象")
                if command == "initialize": result = {"data_root": str(paths["root"]), "ready": paths["userdata"].is_file() and paths["coredata"].is_file()}
                elif command == "get_version": result = {"engine_name": ENGINE_NAME, "engine_version": ENGINE_VERSION, "protocol_version": PROTOCOL_VERSION}
                elif command == "get_status": result = read_json(paths["state"])
                elif command == "check":
                    checked = workflow().check(params.get("current_version"))
                    result = {"current_version": checked.current_version, "available": checked.available, "remote_version": checked.manifest.version if checked.manifest else checked.current_version}
                elif command == "download":
                    threads = params.get("threads")
                    if threads not in (None, 1, 4, 16): raise ValueError("threads 只允许 1、4、16")
                    task_id = uuid.uuid4().hex
                    tasks[task_id] = {"status": "running", "cancel": threading.Event()}
                    merge_state(paths["state"], {"status": "downloading"})
                    executor.submit(run_download, task_id, params.get("current_version"), threads)
                    result = {"task_id": task_id, "accepted": True}
                elif command == "cancel":
                    task_id = params.get("task_id")
                    if not isinstance(task_id, str) or task_id not in tasks: raise ValueError("未知 task_id")
                    tasks[task_id]["cancel"].set(); result = {"task_id": task_id, "cancel_requested": True}
                elif command == "shutdown":
                    write({"type": "response", "request_id": request_id, "success": True, "result": {"shutting_down": True}}); return 0
                else: raise ValueError("未知命令")
                write({"type": "response", "request_id": request_id, "success": True, "result": result})
            except (ValueError, UpdaterError, json.JSONDecodeError) as exc:
                write({"type": "response", "request_id": request_id, "success": False, "error": {"code": type(exc).__name__, "message": str(exc)}})
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return 0
