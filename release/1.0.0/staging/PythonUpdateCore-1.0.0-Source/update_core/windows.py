from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path

from .errors import UpdaterError


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
