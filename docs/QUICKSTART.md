# Yukino Core 0.6.2

Yukino Core 是一个通过 OneBot 连接 QQ 的 Python 内核，附带浏览器控制台。先启动控制台，再配置 QQ 连接、模型服务、人设、消息策略、记忆、插件和资源。首次实例不绑定任何 QQ 账号，也不预装私人聊天和表情素材。

## Python 起步

Windows 用户也可直接下载 [便携发行包](https://github.com/Le672/yukino-core/releases/latest)，无需安装 Python。解压后依次使用 `Start.cmd`、`Show-Token.cmd`，在 `http://localhost:18801/` 登录；详见 [Windows 说明](../windows/README.md)。以下为源码与 Docker 路径。

需要 Python 3.12 或更新版本。解压源码包后，在项目目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
.venv\Scripts\python scripts/init_instance.py --data ./data
.venv\Scripts\python scripts/run.py --data ./data
```

Linux/macOS 将 `.venv\Scripts\python` 换成 `.venv/bin/python`。如果本机有多个 Python，先确认建环境时选用的是 3.12 或更新版本。

打开 [控制台](http://127.0.0.1:18001/)。用本机文本编辑器打开 `data/secrets.json`，复制 `admin_token` 值登录；初始化脚本不会把令牌打印到终端。该文件是私密文件，不要提交 Git、发到群里或放入发行包。`init_instance.py` 不覆盖已有实例。

## Docker 起步

先安装 Docker Engine 和 Compose 插件，并准备一个空实例。初始化仍需要一次 Python 3.12+：

```sh
python scripts/init_instance.py --data ./data --docker
# Linux：容器默认以 UID 10001 运行，确保它能写实例目录。
sudo chown -R 10001:10001 ./data
docker compose -f compose.release.yaml up -d --build
docker compose -f compose.release.yaml logs --tail=50 core
```

Windows Docker Desktop 不需要执行 `chown`。控制台仍在 [127.0.0.1:18001](http://127.0.0.1:18001/)。发行 compose 只绑定本机回环地址；部署在远程机器时，用 SSH 端口转发打开控制台。

```sh
ssh -N -L 18001:127.0.0.1:18001 your-user@your-server
```

## 连接 QQ

NapCat 或其他兼容 OneBot 实现是独立组件，需要自行安装并登录 QQ。Yukino Core 不打包 QQ 客户端或登录会话。参考 [NapCat 官方项目](https://github.com/NapNeko/NapCatQQ)。

在 NapCat 配置 **正向 WebSocket** 服务及访问令牌，然后在 Yukino Core 的 QQ 连接页面填写：

1. WebSocket 地址，例如 Python 同机运行时的 `ws://127.0.0.1:3001`。
2. 与 NapCat 一致的 OneBot 访问令牌。
3. 机器人 QQ、主人 QQ，以及是否接收消息。

Docker 中的 `127.0.0.1` 指向容器本身。如果 NapCat 在宿主机，可使用 `ws://host.docker.internal:3001`，并确认服务监听与防火墙允许容器访问；如果双方都在 Docker，加入同一网络并使用实际服务名称。初始接收开关关闭，完成配置后再打开。

## 配置模型

在模型页面新建服务档案，选择对应协议、服务地址、模型名称和密钥，并设为默认。可建立多个档案后切换。不要把密钥写到人设或聊天消息里。

- OpenAI 兼容接口适用于实现相同请求协议的服务；兼容性取决于服务实际支持的端点和参数。
- Anthropic、Gemini、Ollama 使用各自的服务类型。Ollama 自托管需要额外的模型进程及相应算力，内核容器不提供本地推理。
- ChatGPT 网页桥是可选外部组件，源码发行包不包含浏览器、账号会话或网页桥。没有该组件也可以使用其他模型服务。网页登录状态、验证码或权限失效需要在桥服务所在环境处理。

价格、额度和模型可用性由所选服务决定。新建实例不附送 API 密钥或模型订阅。

## 数据、升级与插件

`data` 保存实例配置、凭据、数据库、记忆、资源和插件。升级代码前先备份整个实例目录；停止服务后复制目录，或对运行中的 SQLite 使用其备份接口。更新代码或重建镜像时保留 `data`，不要再次运行初始化脚本覆盖实例。

内置功能包括会话、记忆、基础工具、骰子、计算器和本地资源检索。表情和资料需要自行导入有权使用的内容。外部插件遵守 Yukino 的 manifest/worker 接口，**不能直接安装 MaiBot 或 AstrBot 插件**；需要针对其事件、配置和发送接口编写适配。见 [插件接口](PLUGINS.md)。

更多信息：[配置与数据](CONFIGURATION.md)、[发行指南](RELEASE.md)、[许可检查](LICENSE_REVIEW.md)、[第三方说明](../THIRD_PARTY_NOTICES.md)。源码包根目录的 README 提供功能与协议总览。
