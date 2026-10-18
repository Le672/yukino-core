# 插件接口

适用0.6.0。完整教程、带参数与SQLite的示例、打包验收和故障排查见 [插件编写指南](PLUGIN_AUTHORING.md)；本页保留接口简表与安装说明。

Yukino Core 使用自己的插件接口。MaiBot 和 AstrBot 插件依赖各自的注册、消息事件、配置与模型接口，不能仅复制到本内核目录使用。适配时保留上游作者和许可证，并分别核查复制的代码、资源及依赖。

戳一戳等通知由内核原生扩展处理，见[通知互动](NOTICE_INTERACTIONS.md)。普通ZIP的`handle(text, context)`依旧是文字指令接口，不会自动订阅通知、获得QQ动作权限或模型凭据。

## 控制台安装本地 ZIP

在「插件管理 → 从本地 ZIP 安装插件」选择可信源码包。ZIP 根目录必须直接包含 `plugin.json`，不能额外包一层目录：

```text
hello.zip
├── plugin.json
├── hello.py
├── README.md
└── LICENSE
```

最小 `plugin.json`：

```json
{
  "id": "hello",
  "name": "问候示例",
  "description": "一个最小文字命令",
  "commands": ["/hello"],
  "worker": "hello.py",
  "config_schema": [
    {"key": "greeting", "type": "string", "label": "问候语", "default": "你好", "max_length": 100}
  ]
}
```

`hello.py`：

```python
def handle(text, context):
    return context["settings"].get("greeting", "你好") + "，插件已连接。"
```

安装只检查、保存 UTF-8 源码和清单，**不会导入或执行插件**，新插件始终停用。管理员核查代码与许可后，再在列表中启用；第一次调用时才启动 worker。配置项可在插件的「配置」中编辑，下一次调用生效。

包的限制：

- ZIP 上传不超过 2,000,000 字节；解压条目合计不超过 1,000,000 字节，最多 50 个条目（包括目录），路径最多 5 层。
- 支持小写 `.py`、`.json`、`.md`、`.txt`，以及 `LICENSE`、`NOTICE`、`COPYING`。所有文件须为 UTF-8 文本。不安装依赖或执行安装脚本；外部依赖需管理员在隔离部署环境中另行准备。
- ID 为小写字母开头、最多 40 个小写字母、数字、`_` 或 `-`；不能与内置插件或已安装 ID 重复。
- `worker` 必须引用包内 `.py` 文件；`commands` 为 1—20 条以 `/` 开头、长度 2—40 且不含空白的指令。
- 拒绝绝对路径、`..`、反斜杠、驱动器路径、Windows 保留名或末尾点/空格、符号链接、加密条目和重复文件路径（含仅大小写不同的名称）。

源码保存到实例 `plugins/packages/<id>-<随机标识>/`；安装失败会恢复旧清单、开关和配置，只清理此次新建的受控目录。安装、重新加载和移除期间拒绝新的插件调用；有插件正在执行时，管理操作会提示稍后重试。

「重新加载」会重新读取清单并结束旧 worker，下次调用启动新进程。下一次调用才会导入最新源码，重新加载本身不执行测试。更新同 ID 的 ZIP 时先移除旧插件，再安装新包；不会隐式覆盖已有代码。

「移除」从活动清单中注销插件并结束 worker，**保留原源码**，同时把移除的记录存入 `plugins/removed-manifests/<id>-<随机标识>.json`，方便管理员审查或手工恢复。备份并非自动回滚按钮；重新安装后需再次启用。插件配置保留，开关记录删除。内置功能只能停用，不能移除。卸载不会删除源码或其他插件文件。

对应管理接口均要求已登录管理会话；写请求还须携带该会话的 `X-CSRF-Token`：

| 接口 | 请求 |
| --- | --- |
| `POST /api/plugins/install` | `multipart/form-data`，唯一文件字段名 `file` |
| `POST /api/plugins/reload` | 重新读取清单 |
| `DELETE /api/plugins/{id}` | 注销并保留源码 |
| `POST /api/plugins/toggle` | JSON：`{"id":"hello","enabled":true}` |
| `GET /api/plugins/{id}/config` | 获取可编辑字段与当前值 |
| `PUT /api/plugins/{id}/config` | JSON：`{"values":{"greeting":"早安"}}` |

## 手工注册与运行接口

也可由管理员把源码放入 `data/plugins`，通过 `manifest.json` 注册。例如：

```json
[
  {
    "id": "hello",
    "name": "问候示例",
    "description": "一个最小文字命令",
    "commands": ["/hello"],
    "worker": "hello.py"
  }
]
```

同目录的 `hello.py`：

```python
def handle(text, context):
    return "你好，插件已连接。"
```

worker 通过独立 Python 进程运行，内核负责标准输入输出协议、超时和异常收敛。`handle` 返回字符串或 `None`，宿主将它封装为协议的 `text` 字段。后台下载、图片、视频、文件或合并转发需要单独服务与发送接口，不能假定返回任意 OneBot 数据就会自动发送。

`context` 包含以下字段：

| 字段 | 用途 |
| --- | --- |
| `session_id` | 会话标识，私聊为 `p:<QQ号>`，群聊为 `g:<群号>` |
| `user_id` | 本次发送者 QQ 号；群内个人数据应同时按会话与用户划分 |
| `settings` | 本次调用的插件配置快照 |
| `self_id` / `owner_ids` | 机器人账号与管理者账号列表 |
| `data_dir` | 内核提供的插件持久化目录绝对路径，固定为实例 `plugins/state/<插件ID>` |

`data_dir` 只在调用进程插件时创建；读取配置、列出插件或调用原生功能不会建立该目录。内核验证插件 ID，拒绝存储路径中的符号链接和目录联接，并在支持 POSIX 权限的系统中将目录权限设为 `0700`。调用者传入的同名字段不能覆盖内核路径。这是避免意外路径写入的边界，仍不是针对不可信插件的安全沙箱。

需要持久化的插件可在该目录内保存自己的 SQLite 或 JSON 文件，不应直接改动 `core.db`、其他插件数据或实例凭据。目录按 ID 保持稳定，与源码包的随机目录名无关；重新加载、卸载与重新安装同 ID 插件会保留原数据。插件应自行完成数据版本迁移和写入事务，备份时包含整个实例 `plugins/state`。例如：

```python
from pathlib import Path
import sqlite3

def handle(text, context):
    database = Path(context["data_dir"]) / "plugin.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE IF NOT EXISTS visits (scope TEXT PRIMARY KEY, count INTEGER NOT NULL)")
        scope = context["session_id"] + ":" + str(context["user_id"])
        db.execute("INSERT INTO visits VALUES (?, 1) ON CONFLICT(scope) DO UPDATE SET count=count+1", (scope,))
        count = db.execute("SELECT count FROM visits WHERE scope=?", (scope,)).fetchone()[0]
    return f"这个会话中，你已调用 {count} 次。"
```

manifest 可以声明 `auto_url_hosts`、`auto_url_paths` 和 `silent_ack` 来匹配指定站点链接并静默确认任务。主机名精确匹配；消息是否允许处理仍受会话权限与群设置约束。管理员需要更新清单并让服务重新加载插件清单；开关可在控制台管理。

独立进程用于故障隔离，**不构成安全沙箱**。插件可以使用当前进程账户具有的文件和网络权限，所以不应直接运行不可信代码。

现网的姬漫和视频服务位于开发仓库的 `integrations` 中，含特定站点、账号和镜像依赖。默认通用源码包不包含这些部署集成，也不附带漫画、视频、表情文件或站点 Cookie。它们需要先完成独立服务配置与发行审查后再发布为扩展；不能把私人实例的目录直接压缩成公共插件包。
