# Yukino Core 插件编写指南

适用内核：**0.4.0—0.7.0**；Python：**3.12+**。普通文字 worker 接口保持兼容；0.7.0 的图片、音频和文件结果见 [媒体结果协议](RICH_PLUGIN_OUTPUTS.md)。原生通知互动见 [通知说明](NOTICE_INTERACTIONS.md)。

本指南依据当前的安装器、插件管理器和进程宿主实现编写。Yukino 使用自己的插件接口，MaiBot／AstrBot 插件需要适配，不能直接复制后运行。完整教学模板在 `examples/plugins/hello_counter/`；该示例不会自动安装到正式机器人。

## 1. 先确定插件能做什么

普通外部插件是一个Python同步函数：

```python
def handle(text, context):
    return "回复文字"
```

| 需求 | 0.4.0 的支持方式 |
| --- | --- |
| 文字命令、计算、查询、小游戏 | 普通worker即可 |
| 面板开关、自定义参数 | `plugin.json` 的 `config_schema` |
| 用户或会话记录 | `context["data_dir"]` 中的SQLite或JSON |
| 自动识别指定网站链接，无需@ | `auto_url_hosts`、`auto_url_paths` |
| 返回图片、音频、文件 | 0.7.0 起普通worker可返回版本化媒体对象，详见媒体结果协议 |
| 视频与合并转发 | 当前仍使用独立服务与发送集成 |
| 禁言、踢人、撤回等QQ动作 | 没有对普通worker开放通用OneBot动作接口；参考原生群管的宿主集成 |
| 使用控制台配置的模型 | `context` 不提供模型客户端、API密钥或通用模型调用方法 |
| 监听所有聊天、群通知、加群事件 | 没有通用外部事件钩子；普通worker只收到已匹配的文字调用 |
| 定时主动发送、后台常驻任务 | worker随时可能回收，不适合承担可靠调度；应接入宿主任务系统或独立服务 |

普通worker提供的是文字调用接口，不是完整机器人SDK。独立进程用于故障隔离，并非系统安全沙箱；它仍具有部署账户的文件与网络权限。

## 2. 最小插件

创建目录并保存两个UTF-8文件：

```text
hello/
├── plugin.json
└── worker.py
```

`plugin.json`：

```json
{
  "id": "hello_demo",
  "name": "问候插件",
  "description": "接收问候命令并返回文字",
  "commands": ["/hello_demo"],
  "worker": "worker.py"
}
```

`worker.py`：

```python
def handle(text, context):
    parts = text.strip().split(maxsplit=1)
    name = parts[1][:80] if len(parts) > 1 else "朋友"
    return f"你好，{name}！"
```

发送 `/hello_demo 小明`，内核匹配后会把完整命令文字传给 `handle`，并把返回的字符串作为普通QQ文字消息发回原会话。群里明确调用已注册指令无需@，仍受接收开关及用户／群访问限制约束。

## 3. 清单字段

ZIP根目录必须有 `plugin.json`。以下为当前安装器保留的字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 正则 `[a-z][a-z0-9_-]{0,39}`，最长40字符；应作为稳定身份使用 |
| `name` | 是 | 非空字符串，最长500字符；建议使用简洁名称 |
| `description` | 是 | 非空字符串，最长500字符 |
| `commands` | 是 | 1～20条，以 `/` 开头、2～40字符、不含空白；命令区分大小写 |
| `worker` | 是 | 包内 `.py` 路径，例如 `worker.py`；始终使用 `/` 路径分隔符 |
| `config_schema` | 否 | 自定义面板参数字段数组；最多使用前30项 |
| `auto_url_hosts` | 否 | 最多16个小写完整主机名，精确匹配，不支持通配符 |
| `auto_url_paths` | 否 | 主机名到路径前缀的对象；键必须出现在hosts列表中 |
| `silent_ack` | 否 | 布尔值，默认false；返回空结果时是否抑制默认确认文字 |

内置ID不可占用：`utilities`、`sessions`、`memory`、`emoji`、`knowledge`、`dice`、`calculator`、`group_admin`、`reminders`、`keyword_reply`。0.5.0外部安装清单最多128条、清单文件最多1MiB，不含内置插件；0.4.0原上限为50条。单个ZIP最多50条目的限制保持不变。

不要注册和其他插件相同的命令。当前按清单顺序取第一个匹配项，重复命令不会自动合并或广播；即使前面的同名插件停用，也可能阻挡后面的同名命令。

`version`、`author`、`dependencies`、`minimum_core_version` 等不是当前运行时保留的清单字段。可以在README、文件名和发布清单记录版本、作者与最低内核版本，但不会因此自动安装依赖、检查兼容性或升级插件。

## 4. 可运行模板：面板参数与持久化

配套模板：`examples/plugins/hello_counter/`。

模板可从开发仓库或配套的 `yukino-plugin-guide-0.4.0.zip` 取得，后者按目录解压到内核源码根目录即可。当前通用内核源码发行包没有附带 `examples` 目录，需要同时取得开发模板包；本指南的入口代码也可直接复制使用。

```text
hello_counter/
├── plugin.json       # 清单与面板字段
├── worker.py         # 同步入口
├── README.md         # 使用方式
├── LICENSE           # 示例许可证
├── build.py          # 开发工具，不装进插件包
└── verify.py         # 隔离验收工具，不装进插件包
```

### 清单

```json
{
  "id": "hello_counter",
  "name": "问候与计数示例",
  "description": "演示文字指令、面板参数与按用户和会话隔离的SQLite持久化",
  "commands": ["/hello", "/hello_count"],
  "worker": "worker.py",
  "config_schema": [
    {"key": "greeting", "type": "string", "label": "问候语", "default": "你好", "max_length": 100},
    {"key": "show_count", "type": "boolean", "label": "问候后显示计数", "default": true},
    {"key": "name_max_chars", "type": "integer", "label": "名字字符上限", "default": 40, "min": 1, "max": 80},
    {"key": "style", "type": "string", "label": "回复风格", "default": "warm", "options": ["warm", "plain"]}
  ]
}
```

### 入口

```python
from pathlib import Path
import sqlite3


def handle(text, context):
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return "用法：/hello 名字，或 /hello_count 查看次数。"
    command = parts[0]
    if command not in ("/hello", "/hello_count"):
        return "用法：/hello 名字，或 /hello_count 查看次数。"

    settings = context.get("settings", {})
    session_id = str(context["session_id"])
    user_id = str(context["user_id"])
    database = Path(context["data_dir"]) / "counter.sqlite3"
    db = sqlite3.connect(database, timeout=1.0)
    try:
        with db:
            db.execute("""CREATE TABLE IF NOT EXISTS counters (
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                total INTEGER NOT NULL,
                PRIMARY KEY (session_id, user_id)
            )""")
            if command == "/hello":
                db.execute("""INSERT INTO counters VALUES (?, ?, 1)
                    ON CONFLICT(session_id, user_id)
                    DO UPDATE SET total=total+1""", (session_id, user_id))
            row = db.execute(
                "SELECT total FROM counters WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            ).fetchone()
            count = row[0] if row else 0
    finally:
        db.close()

    if command == "/hello_count":
        return f"你在这个会话里已使用问候 {count} 次。"
    name = parts[1] if len(parts) > 1 else "朋友"
    name = " ".join(name.split())[:int(settings.get("name_max_chars", 40))]
    greeting = str(settings.get("greeting", "你好"))
    reply = f"{greeting}，{name}。"
    if settings.get("style", "warm") == "warm":
        reply += " 今天也要顺顺利利。"
    if settings.get("show_count", True):
        reply += f"\n这是你在本会话的第 {count} 次问候。"
    return reply
```

`/hello 小明` 增加计数，`/hello_count` 只查询。SQLite用两个独立列构成主键，避免把不同用户、不同群的记录混在一起；SQL值均使用参数绑定。`with db` 提交事务，`finally` 明确关闭连接。

示例依赖Python标准库，不需要任何账号、Cookie或API密钥。

## 5. 本地打包、验证、安装

在**内核源码根目录**打开 PowerShell。下面用通用示例目录，改成你实际克隆或解压的位置：

```powershell
Set-Location 'D:\YukinoCore'
python --version

# 首次开发时创建环境；已有正确的项目环境可跳过。
python -m venv .venv
.\.venv\Scripts\python -m pip install .

.\.venv\Scripts\python -X utf8 examples/plugins/hello_counter/build.py --output ./dist/plugin-guide
.\.venv\Scripts\python -X utf8 examples/plugins/hello_counter/verify.py --package ./dist/plugin-guide/hello_counter-1.0.0.zip
```

Linux/macOS把 `.\.venv\Scripts\python` 换成 `.venv/bin/python`，仍在源码根目录运行。不要把用于Windows的虚拟环境复制到Linux服务器。

构建工具只读取四个公开文件，生成合法ZIP和SHA-256；不会执行worker。输出包已存在时会拒绝覆盖，下一次可换一个输出目录，例如 `./dist/plugin-guide-2`。

验收工具使用临时实例，调用真实 `install_package`、`PluginManager` 和独立Python worker，检查：

1. 安装默认关闭，安装阶段未执行插件。
2. 启用后命令能运行，面板参数改变回复。
3. 不同用户和不同会话的计数隔离。
4. 重新加载后数据保留。
5. 移除、重新安装同ID包后数据保留，新安装仍需启用。

测试不启动QQ连接、不调用模型、不接入正式数据库，成功输出JSON中的 `passed: true`。它验证插件宿主行为，不等于已完成QQ真实消息往返。

安装步骤：

1. 打开控制台「插件管理 → 从本地 ZIP 安装插件」。
2. 选择 `hello_counter-1.0.0.zip`，检查显示的ID和说明。
3. 安装后启用「问候与计数示例」。
4. 在「配置参数」修改问候语等参数并保存。
5. 私聊发送 `/hello 小明`、`/hello_count`，再在允许的群里验证。

无需为安装普通插件重启整个VPS。安装成功只表示文件与清单通过校验，首次调用还可能因导入、缺少依赖或运行错误失败。

## 6. `handle` 与 `context` 契约

必须使用普通 `def handle(text, context)`。当前宿主不会 `await` 返回值，不支持直接写 `async def handle`。

| 字段 | 值与用途 |
| --- | --- |
| `text` | 完整命令文字或匹配链接所在的消息文字；已受字符上限限制 |
| `context["session_id"]` | 字符串；真实QQ会话为 `p:<QQ号>` 或 `g:<群号>` |
| `context["user_id"]` | 当前消息发送者QQ号的字符串 |
| `context["self_id"]` | 配置中的机器人QQ号字符串 |
| `context["owner_ids"]` | 配置中的机器人主人字符串列表 |
| `context["settings"]` | 本次调用的插件配置快照，含通用与自定义参数 |
| `context["data_dir"]` | 宿主为该进程插件创建的持久化目录绝对路径 |

没有默认传入 `group_id`、`message_id`、昵称、QQ群角色、原始消息段、完整聊天历史、数据库句柄、模型对象或发送函数。群号如确有需要可从 `session_id` 的 `g:` 部分读取，但这不会赋予QQ动作权限。

返回值只能是 `str` 或 `None`。返回字典、列表、图片路径、CQ码字符串或OneBot消息段对象不会自动变成对应媒体发送；普通字符串按文字发送。

- 非空字符串：发送文字。
- `None` 或空字符串，且 `silent_ack=false`：宿主发送「插件执行完成。」。
- `None` 或空字符串，且 `silent_ack=true`：标记为插件排队确认，不发送默认确认；它**不会自己创建任务**。
- 返回空值不会转交给下一个插件，也不会自动调用GPT。

配置中的主人身份可用于插件内的主人专用命令。例如，在处理已注册的维护指令前：

```python
owners = {str(value) for value in context.get("owner_ids", [])}
if str(context["user_id"]) not in owners:
    return "这个操作仅允许机器人主人使用。"
```

不要用昵称、聊天文字或命令参数声称的QQ号决定权限。`owner_ids` 表示机器人主人，不等于QQ群管理员；需要真实QQ角色的功能应在宿主中核实。

## 7. 面板参数怎么写

支持四种自定义类型：

| `type` | Python值 | 常用约束 |
| --- | --- | --- |
| `string` | `str` | `max_length`，未提供时保存校验默认最多2000字符 |
| `integer` | `int`，不含bool | `min`、`max` |
| `number` | `int`或`float`，不含bool | `min`、`max` |
| `boolean` | `bool` | true／false |

每个字段可包含 `key`、`type`、`label`、`default`、`min`、`max`、`max_length`、`description`、`options`。`key` 须匹配 `[a-zA-Z][a-zA-Z0-9_]{0,39}`。`options` 使用同类型的原始值数组，如 `["warm", "plain"]`，不要写成 `{"label": ..., "value": ...}` 对象数组。

当前没有自定义 `json`、`list`、`file`、`secret` 或 `password` 类型。结构化参数可用有长度限制的JSON字符串并在worker中解析、校验。未支持的字段类型可能被过滤，安装成功不代表字段已显示；应打开面板确认。默认值同样应满足声明的类型、范围和选项。

所有进程插件自动获得三个参数，不能用自定义字段覆盖同名键：

| 参数 | 默认值 | 可配置范围 |
| --- | --- | --- |
| `timeout` | 2秒 | 0.2～60秒 |
| `input_max_chars` | 4000字符 | 100～8000 |
| `output_max_chars` | 4000字符 | 100～8000 |

输入实际还取内核 `command_max_chars` 的较小值。HTTP请求、导入启动和等候执行都应给总超时留余量。插件作者不能通过自己在 `handle` 中修改settings来修改面板的持久配置。

`config_schema` 只声明默认值和约束，不能改变通用参数默认值。安装包中的 `recommended_settings.json` 没有自动应用逻辑；需要安装后在面板保存，或由受控部署程序通过管理API设置。联网功能可按实际情况将超时调为10～18秒，并为网络请求设置更短的内部超时。

普通插件参数按非秘密配置处理；核心脱敏导出可能包含它们。不要把令牌或Cookie当成普通string字段共享；需要凭据的集成应单独设计存储、遮蔽和导出边界。

## 8. 命令与自动链接触发

指令按消息的**第一个空白分隔词**匹配。`/hello 小明` 可以匹配 `/hello`，`/hello小明` 不匹配。建议参数统一使用普通空格分隔。系统保留 `/help`、`/reset` 等别名，不要占用。

`commands` 始终声明 `/` 前缀。如果用户把全局指令前缀改成 `!`，应发送 `!hello 小明`；宿主会在调用普通worker前把前缀规范成 `/hello 小明`。不要在清单里另外注册 `!hello`。

自动链接示例片段：

```json
{
  "auto_url_hosts": ["www.example.com", "example.com"],
  "auto_url_paths": {"www.example.com": "/video/", "example.com": "/video/"},
  "silent_ack": false
}
```

把此片段合并进正常清单，替换成自己实际支持的站点；仍需保留至少一个普通命令。主机名是精确匹配，子域名不会自动匹配父域。路径前缀以 `/` 开头，只能使用字母、数字、`_`、`-` 和 `/`，总长最多80字符；省略某主机的路径时按 `/` 匹配。带账号、密码或显式端口的URL不参与自动触发。

声明hosts后面板会增加 `auto_parse` 布尔开关，默认true。符合访问限制的群聊链接可以无需@触发；多个插件匹配时仍只选第一个。worker收到原消息文字，自行提取并再次检查URL；context中没有 `url` 或 `@url` 字段。已匹配的普通指令优先于自动链接。

## 9. 进程生命周期、性能与依赖

首次调用时，宿主用**内核运行所用的Python解释器**启动 `python -X utf8 -m yukino_core.plugin_worker <worker路径>`，导入模块一次，再反复调用 `handle`。模块顶层不要启动下载、跑长循环或申请永久资源。

同一插件的调用串行执行，所有会话共享它的进程；全局变量不是按用户隔离。不同插件可以并行。插件进程数量上限默认8，可在对话策略中调整到1～24；空闲worker会按最近使用顺序回收。全部进程忙碌时会返回稍后重试，不保证每次调用立即启动。

停用、重新加载、异常、超时或进程池回收都可能结束worker。不要依赖进程常驻、线程定时器、`atexit` 或最终清理钩子保住数据。新请求等待同插件锁的时间也计入总超时；串行插件内长任务会影响该插件的其他用户。

工作目录继承宿主，**不是插件源码目录**：

```python
from pathlib import Path

SOURCE = Path(__file__).resolve().parent       # 读取包内静态文本
DATABASE = Path(context["data_dir"]) / "data.sqlite3"  # 保存实例数据
```

多文件插件不会自动把worker目录加入模块搜索路径。需要同目录辅助模块时可显式导入：

```python
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from helper import format_reply
```

不要依赖全局 `pip` 恰好装过某库。安装器不会运行 `pip` 或 `requirements.txt`；依赖必须存在于内核的实际解释器或容器镜像中。Docker部署应在自定义镜像中固定依赖并重建，运行中的临时安装不作为可复现部署。

联网插件应使用固定或校验过的服务地址、内部超时、有限响应体和缓存。原站没数据或接口不可达时返回可读说明。大型媒体下载、转码等应交给独立服务，避免长期占用一个worker和同插件队列。

## 10. 持久化、升级与卸载

宿主目录示意：

```text
data/plugins/
├── manifest.json
├── packages/hello_counter-<随机标识>/   # 已安装代码
├── state/hello_counter/               # 稳定实例数据
│   └── counter.sqlite3
└── removed-manifests/                 # 被移除记录的备份
```

只向自己的 `data_dir` 写运行数据，不要写源码包、其他插件目录、`core.db` 或实例配置。需要用户级数据时同时考虑会话和用户；群共享数据可以仅按会话划分，但仍应校验修改者身份。不要把用户输入当成文件路径、SQL片段或shell命令。

SQLite建议使用事务、参数绑定和明确关闭；记录结构变化时自行实现数据版本迁移。JSON文件需要原子替换和适当的并发控制。保持插件ID不变才能继续使用同一数据目录；仅改显示名称不会改变ID。

更新同ID ZIP：先备份，再在面板移除旧包、安装新包并重新启用。安装器拒绝同ID覆盖；移除会注销清单并保留源码、配置和状态，不等于物理删除文件。新包默认停用。需要回退时重新安装旧包；若新代码已迁移数据库，还需要对应的数据备份，旧代码不一定能读取新结构。

「重新加载」用于重新读取管理员已修改的清单并结束旧worker；下一次调用才导入源码。它不是自动下载、更新或测试。安装、移除和重新加载期间会阻止新调用；有worker正在执行时管理操作可能要求稍后重试。

## 11. 调试方法

### 直接调用，查看完整Python异常

在内核源码根目录运行，不使用正式数据：

```powershell
@'
import importlib.util
from pathlib import Path
import tempfile

path = Path("examples/plugins/hello_counter/worker.py")
spec = importlib.util.spec_from_file_location("demo", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as data:
    context = {"session_id": "g:demo", "user_id": "demo-user",
               "self_id": "", "owner_ids": [], "settings": {}, "data_dir": data}
    print(module.handle("/hello 小明", context))
    print(module.handle("/hello_count", context))
'@ | .\.venv\Scripts\python -X utf8 -
```

### 手动观察真实JSON行协议

```powershell
# 先创建独立调试目录；每次测试都可换目录名。
New-Item -ItemType Directory -Path ./.plugin-debug-hello -Force | Out-Null
$pluginDebugData = (Resolve-Path ./.plugin-debug-hello).Path
$request = @{text='/hello 小明'; context=@{session_id='g:demo'; user_id='demo-user'; self_id=''; owner_ids=@(); settings=@{}; data_dir=$pluginDebugData}}
$OutputEncoding = [System.Text.UTF8Encoding]::new()
$request | ConvertTo-Json -Depth 5 -Compress | .\.venv\Scripts\python -X utf8 -m yukino_core.plugin_worker examples/plugins/hello_counter/worker.py
```

应先输出一行 `{"ready": true}`，再输出包含 `text` 的JSON行。上面的 `$OutputEncoding` 用于让Windows的PowerShell管道正确发送中文。

生产插件的 `print` 会被宿主重定向到stderr，生产管理器又丢弃worker的stderr；不能承诺在面板或 `docker logs` 里看到插件的print和完整异常。worker处理异常时只回复 `plugin_failed`。调试具体堆栈优先用上面的直接调用；临时调试日志只能写自己的状态目录，避免记录凭据和完整私聊内容。

## 12. ZIP安装包规则

- 根目录直接包含 `plugin.json`，不能在ZIP外再包一层 `hello_counter/`。
- 压缩文件最多2,000,000字节；所有条目解压总大小最多1,000,000字节；最多50条，目录也计数；路径最多5层。
- 只允许小写扩展名 `.py`、`.json`、`.md`、`.txt`，以及文件名 `LICENSE`、`NOTICE`、`COPYING`；内容须为UTF-8文本。
- 不装入 `.venv`、`.pyc`、二进制、图片、压缩依赖、数据库、密钥、Cookie或聊天记录。运行时自行生成的SQLite不属于源码包。
- 不允许绝对路径、`..`、反斜杠、盘符、Windows保留名、尾随点／空格、符号链接、加密条目或大小写冲突的重复路径。

推荐只列出确实需要的文件，避免把整个工作目录递归压缩。`inspect_package(payload)` 可以检查包结构，但不会执行导入、验证业务逻辑、安装依赖或证明代码可信。

改自其他项目时，保留实际使用代码的许可证和归属说明；功能参考、代码复制、数据来源和素材使用分别说明。不要把旧机器人实例数据库或私人素材一起发布。

## 13. 管理API与高级宿主集成

普通插件不需要调用管理API。给部署工具使用时，管理接口要求已登录会话，写请求还要求该会话的 `X-CSRF-Token`；请求带Origin时也会校验。不要把管理令牌放进普通插件包。

| 方法与路径 | 请求内容 |
| --- | --- |
| `GET /api/plugins` | 获取已安装插件状态 |
| `POST /api/plugins/install` | multipart，文件字段名为 `file` |
| `POST /api/plugins/toggle` | `{"id":"hello_counter","enabled":true}` |
| `GET /api/plugins/hello_counter/config` | 获取schema及当前值 |
| `PUT /api/plugins/hello_counter/config` | `{"values":{"greeting":"早上好"}}` |
| `POST /api/plugins/reload` | 重新读取清单 |
| `DELETE /api/plugins/hello_counter` | 注销并保留源码和数据 |

普通worker若要支持新的消息类型、QQ动作或模型能力，需要先扩展宿主契约，并为权限、会话目标、返回结构、超时及不确定结果定义行为。当前原生群管、提醒和关键词实现可参考 `yukino_core/group_admin.py`、`native_plugins.py` 与 `core.py`；它们是内核代码，不是可通过ZIP注册的native插件。

现有姬漫、视频解析在开发仓库 `integrations/`，使用另外部署的服务和私有集成约定，不是公共媒体发送SDK，也不在通用内核源码包内。不要照搬这些集成的实际地址、凭据或账号。

## 14. 常见问题

| 现象 | 优先检查 |
| --- | --- |
| 上传后看不到插件 | ZIP是否多包一层目录、ID是否合法、清单是否超过限制 |
| 插件出现但不回复 | 是否启用、指令前缀、访问限制、同名命令是否先被其他插件匹配 |
| 首次调用失败 | Python语法、`handle`是否普通函数、依赖是否存在于宿主解释器、辅助模块路径 |
| `None`仍有一句确认 | 默认行为；需要安静空返回时声明 `silent_ack: true` |
| 返回消息段字典后报错 | 入口只接受str或None，不能返回OneBot对象 |
| 参数在面板不显示 | 类型是否受支持、key是否合法、是否与通用字段同名、是否重新加载清单 |
| 修改参数没改变回复 | 是否保存、代码是否从context.settings读取，而不是只用模块全局常量 |
| 记录重启后丢失 | 是否仅放在全局变量、是否误写源码目录、插件ID是否变化 |
| 多个群的用户数据混在一起 | 主键是否同时包括session_id和user_id |
| 本地能运行，VPS失败 | Python版本、Linux大小写、依赖、目录写权限、容器内地址和网络可达性 |
| 长任务让别人也等很久 | 同插件串行；缩短任务或拆成受控后台服务 |
| 拿麦麦／AstrBot包直接安装失败 | SDK不兼容，需要把业务逻辑适配为本指南的入口与清单 |

## 15. 对照现有实现

- 公开契约简表：[PLUGINS.md](PLUGINS.md)。
- 安装、移除、重载：`yukino_core/plugin_packages.py`。
- 匹配、schema、参数、数据目录与进程池：`yukino_core/plugins.py`。
- JSON行宿主和返回类型：`yukino_core/plugin_worker.py`。
- 收到消息后如何调用插件与回复：`yukino_core/core.py`。
- 无联网依赖教学模板：`examples/plugins/hello_counter/`。
- 可参考的独立插件：`extensions/maibot_pack/`、`extensions/community_pack/`。前者F1与Steam、后者公开查询使用了有界的联网辅助模块；不是必须依赖的SDK。

修改内核版本或协议后，应重新核对本指南，尤其是context、返回类型、清单字段和媒体能力，避免把未来计划当作当前已支持功能。
