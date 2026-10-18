# 使用与维护

## 常用入口

| 部署 | 面板 | 启停方式 | 日志 |
| --- | --- | --- | --- |
| Windows 便携 | `http://localhost:18801/` | `Start.cmd` / `Stop.cmd` | `data/logs/core.log` |
| Python 源码 | `http://127.0.0.1:18001/` | `scripts/run.py`，Ctrl+C 正常停止 | 进程输出与控制台事件 |
| Docker | `http://127.0.0.1:18001/` | `docker compose -f compose.release.yaml up -d` / `stop` | `docker compose -f compose.release.yaml logs --tail=100 core` |

面板需要运行核心才能访问。`127.0.0.1` 和 `localhost` 都表示当前电脑；VPS 面板需要在本地建立 SSH 转发，地址才指向远端实例。隧道关闭不会停止 VPS 机器人，Windows 本地版则依赖本地电脑保持运行。

管理令牌在该实例的 `data/secrets.json` 中；Windows 可以用 `Show-Token.cmd` 查看。它不是 QQ 密码或 VPS 登录密码。新实例随机生成，升级应保留原有文件。

## 首次检查

先确认「运行概览」里的 QQ 与模型状态，再从另一 QQ 账号私聊机器人：

```text
/ping
/help
你好
```

`/ping` 正常、普通聊天失败时，重点查看模型服务的真实请求测试和事件记录。插件指令通过普通文字 worker 处理；原生通知需要 NapCat 推送真实 OneBot 通知事件。

全局主人在「平台连接」维护，可填写多个账号。权限按发送者 QQ 核验；群管理动作还需要机器人在该群拥有相应 QQ 权限。模型收到的主人标记不替代宿主的权限判断。

## 备份

1. 正常停止核心，等待 worker 和 SQLite 关闭。
2. 复制**整个 `data` 目录**到私人备份位置；Windows 还保留根目录 `windows.json`。
3. 记录当前版本、插件版本及部署配置。

只复制 `core.db` 会遗漏模型与连接设置、资源、插件状态。控制台脱敏配置导出也不是完整备份：不恢复模型凭据、平台连接或全部会话数据。不要把私人备份上传公开仓库或 Issue。

## 升级与回退

Windows：在新目录解压新便携包，停止旧实例，把完整 `data` 和需要的 `windows.json` 迁移到新目录，再启动；保留旧目录和升级前数据备份。不要把旧 `app` / `windows` 代码覆盖到新版。

源码：先停止并备份数据，再更新代码 / 虚拟环境，仍使用同一个 `--data`。不要删除数据目录或重复初始化来升级。

Docker：停止并完成数据备份后，在项目目录更新源码并执行：

```sh
docker compose -f compose.release.yaml up -d --build core
docker compose -f compose.release.yaml logs --tail=100 core
```

回退时使用旧代码 / 镜像和与其匹配的升级前数据备份；不能假设未来版本数据库一定向后兼容。

Windows 打包插件有安装记录：用户停用或移除的旧插件不会因重复启动被强行恢复。手动更新同 ID 插件，先备份，移除旧项，再安装新 ZIP 并按需启用；核心保留被移除源码，详见[插件接口](PLUGINS.md)。

## 常见问题

| 现象 | 检查 |
| --- | --- |
| 面板打不开 | 检查核心是否运行、地址端口、SSH 隧道；Windows 用 `Status.cmd`。端口被占用时更换本实例端口，保留其他进程 |
| 保存配置后仍不回复 | 确认平台接收开关、QQ 登录和 WebSocket、黑白名单、群触发与群范围；查看事件追踪 |
| QQ 在线但模型不可用 | 检查档案是否启用并设为默认、协议与 base URL、密钥、模型权限、真实请求测试 |
| 网页模型需要重新登录 | 去外部网页桥的浏览器环境处理登录 / 验证。核心本身不保存该网页会话 |
| 群聊没反应 | 启用群聊、允许该群，并按已设置的 @、名称或引用触发；检查禁言和机器人 QQ 权限 |
| 戳一戳没反应 | 检查原生戳一戳开关、目标账号、冷却、概率、群范围及平台通知事件；配置入口在该插件的参数面板 |
| 插件不能安装 | 使用单插件 ZIP，根目录必须直接有 `plugin.json`；MaiBot / AstrBot 安装包需要重写适配 |
| 联网插件返回错误 | 查看该插件 README 的地址、密钥及网络要求，配置保存不代表远端接口可用 |
| 回复慢 | 查看排队和模型耗时；适当减少上下文、关闭不需要的检索、缩短合并窗口，并选择响应更快的模型服务 |

修改人设、插件参数等可通过控制台完成；端口、监听地址或数据目录属于启动参数，需要停止后修改并重启。不要把 API Key 放入人设和聊天。

## 校验下载

Windows：

```powershell
Get-FileHash .\YukinoCore-0.6.2-Windows-x64-build2-portable.zip -Algorithm SHA256
```

Linux：

```sh
sha256sum -c SHA256SUMS.txt --ignore-missing
```

将结果与同一 Release 的 `SHA256SUMS.txt` 对照。遇到问题可在仓库 Issue 描述版本、部署方式、复现步骤和脱敏错误；不要附带令牌、Cookie、完整聊天或 data 目录。
