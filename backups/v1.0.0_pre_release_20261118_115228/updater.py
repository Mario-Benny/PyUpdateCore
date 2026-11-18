from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE_VERSION = "0.0.0"


def _base_dir() -> Path:
    return Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent


def _ask(question: str) -> bool:
    return input(f"{question} [y/N]: ").strip().lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"PyUpdataCore {ENGINE_VERSION}")
    parser.add_argument("action", nargs="?", choices=("check", "download", "serve"))
    parser.add_argument("--stdio", action="store_true", help="run the JSON Lines IPC server")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--current-version")
    parser.add_argument("--process-id", type=int)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--threads", type=int, choices=(1, 4, 16))
    parser.add_argument("--json-output", action="store_true")
    parser.add_argument("--yes", action="store_true", help="confirm configured operations non-interactively")
    args = parser.parse_args(argv)
    root = (args.data_root if args.data_root else _base_dir()).resolve()
    if args.action == "serve" or args.stdio:
        from update_core.ipc import serve_stdio
        return serve_stdio(root)
    # Keep --help and argument validation free of downloader/HTTPX imports.
    from update_core.config import load_split_config
    from update_core.errors import UpdaterError
    from update_core.layout import initialise_layout
    from update_core.logging_utils import clean_logs, write_log
    from update_core.workflow import UpdateWorkflow
    paths, migrated = initialise_layout(root)
    if migrated or not paths["userdata"].exists() or not paths["coredata"].exists():
        print(f"已创建配置模板：{paths['data']}")
        print("请填写 userdata.json 与 coredata.json 后重新运行；本次不会联网、下载或安装。")
        return 0
    try:
        config = load_split_config(paths["userdata"], paths["coredata"])
        clean_logs(paths["logs"], config.logging_retention_days)
        write_log(paths["logs"], "startup", application_id=config.application_id)
        def emit(event):
            if args.json_output:
                print(json.dumps(event, ensure_ascii=False), flush=True)
        show_progress = config.show_progress and not args.json_output
        workflow = UpdateWorkflow(config, root, progress=(lambda done, total: print(f"下载进度：{done}/{total}", end="\r")) if show_progress else None, event_callback=emit if args.json_output else None)
        result = workflow.check(args.current_version)
        if not args.json_output:
            print(result.message + f"（当前 {result.current_version}）")
        wants_download = args.action == "download" or args.download or args.install
        if args.json_output:
            print(json.dumps({"status": "update_available" if result.available else "no_update", "current_version": result.current_version, "remote_version": result.manifest.version if result.manifest else result.current_version}, ensure_ascii=False), flush=True)
        if not result.available or not wants_download:
            return 0
        if result.manifest is None:
            raise UpdaterError("更新状态不完整：缺少远程清单")
        if config.confirm_before_download and not args.yes and not _ask(f"下载 {result.manifest.version} 吗？"):
            return 0
        installer = workflow.download(result.manifest, threads=args.threads)
        if not args.json_output:
            print(f"\n已校验安装包：{installer}")
        if not args.install:
            return 0
        if config.confirm_before_install and not args.yes and not _ask("立即启动安装器吗？"):
            return 0
        workflow.install(installer, result.manifest, args.process_id)
        return 0
    except UpdaterError as exc:
        if args.json_output:
            print(json.dumps({"status": "error", "code": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"更新失败：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"更新失败：系统操作失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
