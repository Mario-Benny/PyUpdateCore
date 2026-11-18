from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path

from .errors import UpdaterError


def secure_cache_directory(path: Path) -> None:
    """Create a non-reparse-point cache and restrict its Windows ACL to its owner/system/admins."""
    path.mkdir(parents=True, exist_ok=True)
    is_junction = getattr(os.path, "isjunction", lambda _: False)
    if path.is_symlink() or is_junction(path):
        raise UpdaterError("更新缓存目录不能是符号链接或联接点")
    if os.name != "nt":
        return
    username = os.environ.get("USERNAME", "").strip()
    if not username:
        raise UpdaterError("无法确定当前 Windows 用户，拒绝使用更新缓存目录")
    command = [
        "icacls", str(path), "/inheritance:r", "/grant:r",
        f"{username}:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise UpdaterError("无法设置更新缓存目录权限") from exc
    if result.returncode != 0:
        raise UpdaterError("无法设置更新缓存目录权限")


def wait_for_process(pid: int, timeout_seconds: int = 600) -> None:
    if pid <= 0:
        return
    if os.name != "nt":
        raise UpdaterError("进程等待仅支持 Windows")
    ctypes.set_last_error(0)
    handle = ctypes.windll.kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists
            return
        if error == 5:
            raise UpdaterError("无法等待主程序：访问被拒，请使用与主程序相同的权限运行更新器")
        raise UpdaterError(f"无法打开主程序进程（Windows 错误 {error}）")
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_seconds * 1000)
        if result == 0x00000102:
            raise UpdaterError("等待主程序退出超时")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def launch_installer(path: Path, arguments: tuple[str, ...], request_admin: bool) -> None:
    if not path.is_file():
        raise UpdaterError("安装器不存在")
    if path.suffix.lower() != ".exe":
        raise UpdaterError("当前版本仅允许启动 EXE 安装器")
    if request_admin:
        params = subprocess.list2cmdline(list(arguments))
        result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(path), params, str(path.parent), 1)
        if result <= 32:
            raise UpdaterError("管理员权限安装器启动失败或被取消")
    else:
        subprocess.Popen([str(path), *arguments], cwd=path.parent)


def restart_program(path: Path, arguments: tuple[str, ...]) -> None:
    if not path.is_file():
        raise UpdaterError("重启目标程序不存在")
    subprocess.Popen([str(path), *arguments], cwd=path.parent)
