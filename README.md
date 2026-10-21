# Yukino Core

带 Web 控制台、多模型支持和独立插件系统的轻量 QQ 机器人内核。支持 Windows、Linux 与 Docker，通过 **OneBot v11 正向 WebSocket** 连接 NapCat 等平台实现。

[下载 0.6.4](https://github.com/Le672/yukino-core/releases/tag/v0.6.4) · [安装与使用](docs/QUICKSTART.md) · [插件开发](docs/PLUGIN_AUTHORING.md) · [更新记录](CHANGELOG.md) · [MIT License](LICENSE)

## 功能

- **控制台**：平台连接、多个模型档案、会话模型路由、人设、消息触发、长期记忆、插件安装与参数、资源编辑、消息历史、事件与诊断。
- **模型**：OpenAI 兼容 Chat Completions、OpenAI Responses、Anthropic Messages、Gemini、Ollama、可选 ChatGPT 网页桥。
- **对话**：私聊与群聊、@ / 引用 / 名称触发、黑白名单、上下文、消息合并、冷却、队列、图片表情和知识检索。
- **管理与互动**：全局主人、群管理、提醒、关键词回复、戳一戳、欢迎、退群、管理员 / 头衔 / 名片通知、[偷表情包](docs/EMOJI_COLLECTOR.md)，共 **17 项原生功能**。
- **扩展**：**119 个外部插件**，覆盖 F1、签到、查询、开发工具、生活记录和文字游戏；每项都有源码、独立 ZIP 和用法。

普通指令无需模型；模型对话需要自己的可用服务。核心不捆绑 QQ 客户端、模型账号、浏览器会话或表情素材。

## 下载哪个包

在 [Releases](https://github.com/Le672/yukino-core/releases/latest) 下载：

| 文件 | 用途 |
| --- | --- |
| `YukinoCore-0.6.4-Windows-x64-portable.zip` | Windows 10/11 x64，内置 Python、依赖和 119 个扩展，解压双击启动 |
| `yukino-core-0.6.4-source.zip` | 通用源码，包含核心、文档、测试、全部普通插件源码与132条互动文案和 Windows 构建工具 |
| `yukino-plugins-119-1.0.1.zip` | 六组共 119 个单插件安装 ZIP、清单和使用说明，供源码 / Docker 实例按需安装 |
| `SHA256SUMS.txt` | 发布附件的 SHA-256 校验值 |

GitHub 自动生成的 “Source code” 也能用于开发；想复核发布清单时下载带 `release-manifest.json` 的命名源码包。

## Windows：解压运行

1. 完整解压便携包到可写目录，例如 `D:\YukinoCore`。
2. 双击 **`Start.cmd`**，在 Chrome 打开 [localhost:18801](http://localhost:18801/)。
3. 双击 **`Show-Token.cmd`**，使用本机生成的管理令牌登录。
4. 按下方步骤配置 QQ 和模型，再启用接收。停止时双击 **`Stop.cmd`**。

无需安装 Python 或 Docker。关闭浏览器后核心继续运行，电脑关机或休眠后本地机器人停止处理。新实例有 136 项功能，其中戳一戳、偷表情包和 119 个扩展默认启用，其他五种群通知默认关闭；实际触发还受 QQ 接收、群范围和权限控制。

完整操作、升级和离线构建见 [Windows 使用说明](windows/README.md)。

## Linux / macOS：Python 源码运行

需要 Python **3.12+**，在仓库或命名源码包根目录执行：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python scripts/init_instance.py --data ./data
.venv/bin/python scripts/run.py --data ./data
```

在本机打开 [127.0.0.1:18001](http://127.0.0.1:18001/)，用编辑器查看 `data/secrets.json` 中的 `admin_token` 登录。Ubuntu 若缺少 venv 组件，先安装所选 Python 对应的 venv 包。Windows 源码运行将解释器路径换为 `.venv\Scripts\python`。

初始化生成随机令牌、空模型与账号，并关闭接收，不覆盖已有实例。源码实例默认只有原生功能，外部插件按需安装。

## Docker / VPS

在已安装 Docker Engine、Compose 和 Python 3.12+ 的机器中：

```sh
python3 scripts/init_instance.py --data ./data --docker
# Linux：让容器用户能够写入该实例。Windows Docker Desktop 跳过此行。
sudo chown -R 10001:10001 ./data
docker compose -f compose.release.yaml up -d --build
docker compose -f compose.release.yaml logs --tail=50 core
```

面板默认只监听服务器回环地址。在自己的电脑保持以下隧道运行，然后用 Chrome 打开 `http://127.0.0.1:18001/`：

```sh
ssh -N -L 18001:127.0.0.1:18001 your-user@your-server
```

隧道用于访问面板；断开隧道或关闭本地电脑，VPS 上的核心仍继续运行。Docker 中的 `127.0.0.1` 指向容器自身，连接宿主机 OneBot 时使用可达的宿主机地址，例如发行配置中的 `host.docker.internal`。

## 连接 QQ 与模型

1. 自行安装并登录 [NapCat](https://github.com/NapNeko/NapCatQQ)，配置 **WebSocket 服务器（正向）** 和访问令牌。
2. 在 Yukino 控制台「平台连接」填写 WebSocket 地址、同一令牌、机器人 QQ、主人 QQ。主人列表按真实发送账号识别，私聊与群聊统一生效。
3. 在「模型服务」创建服务档案，填写对应协议、地址、模型和密钥，测试请求并设为默认。可以建立多个档案并为会话单独选择模型。
4. 在「人格与设置」配置角色、触发与聊天范围，最后启用平台接收；群聊还需要启用群聊并配置允许范围。
5. 用自己的 QQ 私聊发送 `/ping`、`/help`，再发送普通文字。前两项检查内核，普通文字检查模型链路。

当前只支持 OneBot v11 正向 WebSocket。ChatGPT 网页桥是外部可选服务，本发行包不包含桥、浏览器或登录状态；可直接选择其他模型协议。各协议有本地回环测试，真实服务可用性以该实例的请求测试为准。

## 插件

| 功能包 | 数量 | 使用说明 |
| --- | ---: | --- |
| 社区功能 | 11 | [签到、好感、天气、Steam、音乐等](docs/COMMUNITY_PLUGINS.md) |
| 麦麦市场功能参考 | 15 | [F1、查询、记录与游戏](docs/MAI_EXTENSIONS.md) |
| 实用扩展 | 63 | [开发工具、生活协作、文字游戏](docs/EXPANSION_PLUGINS.md) |
| 第二批市场扩展 | 8 | [RSS、Bangumi、课表、UNO、麻将等](extensions/market2_pack/README.md) |
| 互动扩展 | 12 | [海龟汤、抢答、续写、谁是卧底、棋类等](docs/INTERACTION_PLUGINS.md) |
| 社交解谜与查询 | 10 | [漂流瓶、时光信、数独、远征、搭话、版本查询等](extensions/social_pack/README.md) |

Windows 便携版首次启动自动安装上述扩展。源码 / Docker 用户解压插件合集，在「插件管理 → 从本地 ZIP 安装」选择里面的**单插件 ZIP**，再启用。合集外层 ZIP 不是单插件安装包。联网插件可能需要自己的 API Key、服务地址或网络条件，详见各插件说明。

本项目采用 Yukino 自有的 manifest / worker 接口，**不能直接安装 MaiBot 或 AstrBot 插件**。普通外部 worker 提供文字命令接口；戳一戳和 QQ 群管理属于宿主原生功能。插件进程用于故障隔离，不是运行不可信代码的安全沙箱。

从 [插件编写指南](docs/PLUGIN_AUTHORING.md) 和 [可运行示例](examples/plugins/hello_counter/README.md) 开始开发。姬漫与视频侧车集成尚未通用化，不在这次公共发行中。

## 维护与文档

- [完整安装与首次配置](docs/QUICKSTART.md)
- [配置字段、数据与全局主人](docs/CONFIGURATION.md)
- [备份、更新、恢复与故障排查](docs/OPERATIONS.md)
- [原生通知互动](docs/NOTICE_INTERACTIONS.md) · [132条戳一戳与搭话文案](docs/BANTER_COPY.md)
- [插件接口](docs/PLUGINS.md) · [插件开发教程](docs/PLUGIN_AUTHORING.md)
- [架构](ARCHITECTURE.md) · [构建与发行](docs/RELEASE.md)

`data` 是私人实例目录，保存令牌、账号、模型配置、聊天、记忆、资源和插件状态。升级时保留整个目录；源码包和 Release 均不含现有部署的数据。

## 开发与检查

```sh
python -m pip install .
python -m unittest discover -s tests -v
python -m unittest discover -s windows/tests -v
python scripts/build_release.py --output ./dist/source
```

各扩展包的 README 给出独立测试位置；实用扩展的测试分为 `dev_tools/tests`、`life_tools/tests` 和 `games/tests`。`build_pack.py --output <新目录>` 生成单插件 ZIP。Windows 构建步骤见 [Windows 说明](windows/README.md)。GitHub Actions 检查核心、启动器、六组插件与白名单打包。

## 许可与来源

核心代码按 [MIT License](LICENSE) 分发，并保留上层项目 [make-your-qq-ai-group-member](https://github.com/mtshang/make-your-qq-ai-group-member) 的版权声明。扩展包各自保留 LICENSE / SOURCES / NOTICE；第三方依赖和用户素材适用各自许可。

公共源码使用生成的 Y 字 SVG 和空资源库。Windows 附件包含固定依赖清单、运行时许可证和各 wheel 的许可文件。来源与已有检查边界见 [第三方说明](THIRD_PARTY_NOTICES.md) 和 [许可记录](docs/LICENSE_REVIEW.md)。
