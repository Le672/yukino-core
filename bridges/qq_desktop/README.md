# Yukino QQ Desktop（实验版 0.1.0a1）

将官方 Linux QQ 的可见桌面接到 OneBot v11。它使用系统 AT-SPI 无障碍接口查找控件、填入文字、点击发送和读取可见消息列表，不加载 NapCat，不修改 QQ 文件，也不实现 QQ 私有登录协议。**目前是需要界面校准的原型，尚未完成真实 QQ 登录后的收发验收，不是完整 NapCat 替代品。** QQ 账号安全限制仍需在官方客户端处理。

## 目前范围

- 提供带令牌认证的正向 WebSocket `/`、`/api`、`/event`，支持状态、登录信息、版本、已绑定好友/群列表，以及私聊/群聊纯文本。
- 只操作手动核实并绑定的会话；不按昵称猜 QQ 号。检查账号数字字段、唯一联系人控件和目标聊天标题；缺失或歧义时停止操作。
- 轮询当前绑定会话的可见历史。首次见到某会话、进程重启时的现有历史只建立基线，不触发回复；不承诺恢复离线遗漏消息。必须校准稳定的可见消息行标识，不能唯一识别时拒绝推断。
- 发送前检查草稿，不覆盖原有草稿。仅点击一次；没有观察到唯一的新发送行时返回“不确定”，不自动重发。可选 `idempotency_key` 提供7天内本地去重，`echo`只用于关联响应。
- 本地消息ID由SQLite生成，不是QQ服务器原始ID；客户端可见发送记录也不证明收件人已经收到。
- 图片、视频、文件、语音、合并转发、撤回、戳一戳事件和群管理尚不支持。`can_send_image/record`返回false，其他未实现动作明确返回1404，不伪装成功。

因此不能把现有机器人全部媒体/管理插件直接视为已兼容。GUI自动化也不能保证不触发平台限制；没有破解验证或绕过受限账号的功能。

## Linux 运行

Ubuntu 24.04 安装系统无障碍依赖、官方 QQ 和 Python aiohttp。Python 的 `gi` 来自系统包，普通 pip 环境不能代替它。

```sh
sudo apt install python3-aiohttp python3-gi gir1.2-atspi-2.0 at-spi2-core dbus-x11
cp config.example.json config.json
export QQ_DESKTOP_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
python3 -m qq_desktop check-config --config config.json
# 必须与QQ运行在同一个DISPLAY和D-Bus会话。
python3 -m qq_desktop applications --config config.json
python3 -m qq_desktop serve --config config.json --data ./data
```

默认 `enabled=false`，不会点击联系人、读取聊天或发送消息；状态明确为离线/尚未校准。`applications`仅列应用名称；`inspect --output /private/path/ui.json`读取选定QQ应用的无障碍树，输出可能包含可见聊天，应保存在私有位置，不上传Git。

## 校准

1. 正常打开官方QQ并由账号所有者完成登录。不要在账号受限时自动反复尝试。
2. 使用 `applications` 得到实际应用名，填入 `app_name`。用 `inspect` 观察现有控件，使用精确名称/角色或索引路径，不能凭空填写选择器。
3. `identity`必须定位到可见的本人QQ数字字段，`self_id`必须与它逐字一致。当前官方客户端是否在可操作的消息界面持续暴露这个字段，仍须登录后验证。
4. 按 `binding.example.json` 添加私聊/群会话。`id`由本人核实；`senders`将界面中明确的发送者标签映射到已核实QQ号。重名和未知成员不猜测。例子是测试结构，不能直接当QQ的真实选择器。
5. 标定稳定的 `row_key`（accessible_id/description/name），消息体与发送者子控件。重复行键会停止本轮事件读取。
6. 完成可见界面核实后设置该绑定 `verified=true`，最后 `enabled=true`。每次QQ界面更新都可能需要重校准。

输入框有内容、QQ切换账号、控件变动或存在歧义时应先修正校准，不要用坐标猜点兜底。默认2秒扫描一个会话，32个会话轮询可能超过一分钟；本方案牺牲吞吐换取可观察的桌面操作。

## Docker 隔离部署

见 `compose.example.yaml`。从[腾讯官网](https://im.qq.com/linuxqq/index.shtml)取得官方下载地址，记录下载文件SHA-256；设置固定Ubuntu镜像digest、URL与文件hash再构建。官方QQ文件仅在使用者本机构建中下载，不随本仓库发布。`private.env`只存随机 `QQ_DESKTOP_TOKEN`，禁止提交；`data/config.json`需设置容器内 `host=0.0.0.0`、`allow_network_bind=true`，宿主机映射仍只监听127.0.0.1。`data`的运行UID为10001。

`client-lock.example.json`记录本次实际下载并核对的腾讯原始deb与Ubuntu镜像digest，不含客户端本体。Linux源码目录可执行：

```sh
sudo python3 scripts/init_instance.py
sudo docker compose --env-file client.env -f compose.example.yaml build
sudo docker compose --env-file client.env -f compose.example.yaml up -d
```

初始化会生成随机私密令牌和禁用状态配置，遇到已有data/private.env会拒绝覆盖。样例锁定链接可能随厂商更新失效，换版本时必须重新核对文件来源和hash；没有从第三方打包客户端兜底。

候选接口 `ws://127.0.0.1:18002/`；桌面 `http://127.0.0.1:16081/vnc.html`，远程使用SSH隧道。VNC只应在隧道内访问。QQ配置、日志、令牌和界面检查文件全部保留在私有data。Docker使用无沙盒QQ启动标志以适应容器，故额外采用普通用户、资源限额和隔离数据目录；不与原NapCat共用登录资料。

不要在未通过真实账号收发验收前替换正式OneBot地址。核心在Docker内时，不能直接把自身127.0.0.1当宿主机；后续切换应使用同一私有容器网络与鉴权配置。

容器的 `/data/session-env.json` 只保存DISPLAY、D-Bus地址与HOME，供同一容器内的校准进程恢复桌面会话环境；不包含QQ或OneBot令牌。不要在机器人操作同一窗口时同时手工编辑聊天。

## 验证

```sh
python3 -m unittest discover -s tests -v
# 在拥有DISPLAY和D-Bus的Linux会话，测试真实GTK控件，没有QQ收发：
python3 scripts/gtk_acceptance.py
```

协议/状态测试与GTK无障碍控件验收不等于QQ实机验收。账号限制解除后，仍须验证真实界面选择器、发送者映射、消息行标识、收发、重启去重及超时不重发。

只打包本适配器源码与许可、不含QQ客户端和实例数据：`python3 scripts/build_source.py --output ./dist/source`。

接口参考[OneBot 11](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md)，系统界面操作参考[GNOME AT-SPI](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.Accessible.html)。本目录遵循仓库MIT许可；操作系统组件和QQ保留各自许可。
