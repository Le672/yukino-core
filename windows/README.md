# Yukino Core Windows 便携版

适用于 Windows 10/11 x64。内置官方 CPython 3.14.8、固定版本依赖和普通插件，无需安装 Python、Docker 或修改系统 PATH。控制台与服务器版相同；运行在当前电脑，关机或睡眠后本地机器人不能继续处理消息。

## 第一次运行

1. 完整解压 ZIP 到可写的本地文件夹，如 `D:\YukinoCore`。不要在压缩包预览中运行，也不要放在 `Program Files`、网络共享或仅有读取权限的目录。
2. 双击 `Start.cmd`。程序后台运行，并尝试用 Chrome 打开 `http://localhost:18801/`。找不到 Chrome 时按提示手动打开地址。使用 localhost 可以与现有 `127.0.0.1:18001` VPS 面板分别保存登录会话。
3. 双击 `Show-Token.cmd`，把显示的管理令牌填入控制台。令牌只保存在本机 `data/secrets.json`，每个新实例随机生成；不要把这个文件或整个 data 文件夹公开分享。
4. 在「模型服务」添加你自己的模型档案，在「平台连接」填写 NapCat 的 OneBot WebSocket 地址、访问令牌、机器人 QQ 和主人 QQ。确认后启用接收。新实例默认不接收 QQ，不带任何现有 VPS 账号或会话。

控制台支持已有的六类模型连接。使用 ChatGPT 网页桥时，还需要单独运行网页桥并保持该账号已登录；便携核心没有捆绑浏览器会话或桥服务。NapCat/QQ 程序也需单独安装或连接你已有的服务。不要让本地版与 VPS 同时使用同一个机器人账号处理相同事件。

## 启动、停止和配置

- `Start.cmd`：后台启动，重复点击会打开现有实例，不重复创建机器人。
- `Stop.cmd`：通过本地管理认证正常停止服务，等待 worker 和数据库关闭；不按未经核实的 PID 强行杀进程。
- `Status.cmd`：显示本地实例是否在线。
- `Open-Panel.cmd`：用 Chrome 打开正在运行的控制台。
- `Show-Token.cmd`：在本地窗口显示管理令牌，不写入普通日志。

关闭 Chrome 不会停止核心；需要停机时使用 `Stop.cmd`。本包不会自动添加开机任务、防火墙例外或公共监听端口。默认只监听本机 127.0.0.1，避免与服务器 SSH 隧道使用的 18001 冲突。停止后修改根目录 `windows.json` 的 port 可以换端口，范围 1024—65535。

程序保存在 `app`，运行时在 `runtime`，启动器在 `windows`，新实例和聊天/插件状态在 `data`。日志在 `data/logs/core.log`。已有 data 不会被默认配置覆盖；部分配置损坏会报错，需从备份恢复。升级新增打包插件时，仅首次加入该插件；你停用或移除过的旧打包插件不会被下次启动强行启用/重装。

## 插件

0.6.2 Windows build 2 内置 16 个原生功能，并附带 109 个普通进程插件（社区 11 个、麦麦功能参考 15 个、实用扩展 63 个、第二批市场功能参考 8 个、互动扩展 12 个），新实例共 125 项。109 个外部插件首次启动安装并启用，原生群通知中除戳一戳以外的五项默认关闭。它们使用同一份 Yukino 源码，不等于支持直接安装麦麦 SDK 插件。每个 ZIP、SHA-256 和推荐参数保存在 `plugin-packages`；插件运行期间仍受内核 worker 数量限制，默认 8 个。

各插件的完整说明可直接在根目录 `plugin-help` 中阅读，无需逐个解压安装ZIP。

姬漫和视频解析是额外服务集成，当前便携包不附带这两个侧车及其浏览器、FFmpeg 等环境。详细指令见 `app/docs/EXPANSION_PLUGINS.md`、`app/docs/MAI_EXTENSIONS.md`、`app/docs/PLUGINS.md`、`app/docs/COMMUNITY_PLUGINS.md` 和 `app/docs/INTERACTION_PLUGINS.md`，每项扩展的说明也在 `plugin-help`。

## 备份和排查

先使用 `Stop.cmd`，再复制整个 `data` 作为私人备份。不要只复制 core.db 而遗漏插件 SQLite。迁移电脑时复制完整便携目录可以保留实例；对外发送干净发行包时只发送原始 ZIP，不能把已生成的 data 打包进去。

端口占用时启动器会报错并保留占用进程。修改 windows.json 后再启动，不会结束其他程序。启动失败先查看 `data/logs/core.log`；不要公开上传其中的私人对话。没有配置模型时，控制台管理和普通指令仍可使用，模型对话需补充连接配置。

## 构建来源

运行时来自 [Python 官方 3.14.8 发布页](https://www.python.org/downloads/release/python-3148/)，校验官方 SHA-256，并附带官方 SPDX 清单和 Python 许可证。第三方 wheel 从 PyPI 下载，逐个比对其官方 JSON 中的 SHA-256；版本、文件名、校验值和许可证记录在 `dependency-lock.json` 及 `licenses`。源码、启动器和包内容的校验清单在 `windows-release-manifest.json`。

构建脚本不会复制 VPS 凭据、网页 Cookie、聊天数据库、未知授权素材或私人 logo。本地实际验证是独立实例，不代表未配置账号的新实例已经完成真实 QQ 和模型收发。

此便携包另外包含 tzdata 2026.5；世界时间和课表可以使用 IANA 时区名称，不依赖 Windows 提供 Unix 时区文件。

## 开发者构建

在已安装构建工具的 Python 环境中运行以下命令。下载缓存可以重复使用；`fetch_runtime.py` 校验官方散列后生成依赖锁。构建目录必须是新的空输出位置，避免覆盖已经发行的ZIP。

```powershell
python -m pip download --only-binary=:all: --platform win_amd64 --python-version 314 --implementation cp --abi cp314 -r windows/requirements.txt --dest ./windows-cache
python windows/fetch_runtime.py --cache ./windows-cache
python scripts/build_release.py --output ./dist/source
python extensions/community_pack/build_pack.py --output ./dist/community-pack
python extensions/maibot_pack/build_pack.py --output ./dist/mai-pack
python extensions/expansion_pack/build_pack.py --output ./dist/expansion-pack
python extensions/market2_pack/build_pack.py --output ./dist/market2-pack
python extensions/interaction_pack/build_pack.py --output ./dist/interaction-pack
python windows/build_windows.py --core-source ./dist/source/yukino-core-0.6.2-source.zip --cache ./windows-cache --plugins ./dist/community-pack --plugins ./dist/mai-pack --plugins ./dist/expansion-pack --plugins ./dist/market2-pack --plugins ./dist/interaction-pack --build-number 2 --output ./dist/windows-062
```

0.6.0包含戳一戳等六项原生通知互动，具体开关在「插件管理 → 配置参数」，见应用目录`app/docs/NOTICE_INTERACTIONS.md`。通用新实例默认仅开启戳一戳，其他五类群通知可分别开启；只有平台连接和QQ接收已配置并启用后，真实事件才会进入内核。

发行目录名、版本与内置插件数从冻结源码读取。每个外部插件的用法保存在`plugin-help/<插件ID>.md`；互动包的完整规则也随单插件说明提供。升级应在新目录解压，停止旧实例后迁移整个`data`，保留旧目录以便回退；不要把旧`app`或`windows`代码覆盖进新版。

五个插件目录均须含构建后生成的 `pack-manifest.json` 和对应ZIP。核心源码ZIP使用项目白名单发行工具生成。使用已保存的 dependency-lock.json 及其校验值恢复相同缓存，不要用浮动的新依赖冒充同一构建。


