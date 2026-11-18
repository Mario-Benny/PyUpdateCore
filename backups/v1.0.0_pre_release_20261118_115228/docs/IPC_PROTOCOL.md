# JSON Lines IPC v1 / JSON Lines 接口协议 v1

Start with `Updater.exe serve --stdio`. stdin/stdout use UTF-8 JSON Lines; stdout is reserved for protocol records. 使用 `Updater.exe serve --stdio` 启动；标准输出只能写入协议 JSON。

## Request / 请求

```json
{"protocol_version":1,"type":"request","request_id":"req-1","command":"get_version","params":{}}
```

Every request requires protocol version 1, `type: "request"`, a non-empty request id, command, and optional object params.

## Commands / 命令

| Command | Purpose / 用途 |
| --- | --- |
| `initialize` | Data root readiness / 数据目录初始化状态 |
| `get_version` | Engine and protocol version / 核心版本 |
| `get_status` | Persisted state / 运行状态 |
| `check` | Check update; params.current_version overrides config / 检查更新 |
| `download` | Start background download; threads 1, 4 or 16 / 后台下载 |
| `cancel` | Request task cancellation / 请求取消 |
| `shutdown` | Reply then exit / 正常关闭 |

## Response and events / 响应与事件

```json
{"protocol_version":1,"type":"response","request_id":"req-1","success":true,"result":{"engine_version":"0.0.0"}}
```

Failures return `success:false` with `error.code` and `error.message`. Download progress uses `type:"event"`, and completion emits `task_finished`. Invalid JSON and unknown commands are structured errors; this protocol never executes arbitrary commands, scripts, or paths.
