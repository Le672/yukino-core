# Yukino QQ Desktop（实验版 0.1.0a3）

将官方 Linux QQ 的可见桌面接到 OneBot v11。它使用系统 AT-SPI 无障碍接口查找控件、填入文字、点击发送和读取可见消息列表，不加载 NapCat，不修改 QQ 文件，也不实现 QQ 私有登录协议。**目前是需要界面校准的原型，尚未完成真实 QQ 登录后的收发验收，不是完整 NapCat 替代品。** QQ 账号安全限制仍需在官方客户端处理。

## 目前范围

- 提供带令牌认证的正向 WebSocket `/`、`/api`、`/event`，支持状态、登录信息、版本、已绑定好友/群列表，以及私聊/群聊纯文本。定期上报 OneBot 心跳，额外的 `get_capabilities` 返回各项能力当前是否就绪。
- 只操作手动核实并绑定的会话；检查本人、聊天目标和每条消息发送者的真实 QQ 数字身份字段，以及唯一联系人控件和目标聊天标题；缺失或歧义时停止操作。昵称或消息正文不能作为身份字段，即使内容恰好是 QQ 数字。
- 轮询当前绑定会话的可见历史。首次见到某会话、进程重启时的现有历史只建立基线，不触发回复；不承诺恢复离线遗漏消息。必须校准稳定的可见消息行标识，不能唯一识别时拒绝推断。
- 发送前检查草稿，不覆盖原有草稿。仅点击一次；没有观察到唯一的新发送行时返回“不确定”，不自动重发。可选 `idempotency_key` 提供7天内本地去重，`echo`只用于关联响应。
- 本地消息ID由SQLite生成，不是QQ服务器原始ID；客户端可见发送记录也不证明收件人已经收到。
- 图片与文件有可选的附件选择窗口通道：单次接受文件、可选的最终确认、核对唯一新附件行；未经手工校准的会话拒绝发送。`can_send_image`仅在本人身份在线且至少一个图片流程完成校准时为true。
- 可读取已由官方QQ下载到指定inbox、并在消息行中暴露真实下载路径的附件；未下载时只报告文件名及不可读取状态。没有扫描QQ缓存或自动下载陌生文件。
- 视频、语音、合并转发、撤回、戳一戳事件和群管理尚不支持。`can_send_record`返回false，其他未实现动作明确返回1404，不伪装成功。

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

### 控制台

访问候选接口同端口的 `http://127.0.0.1:18002/console`，使用本实例private.env中的QQ_DESKTOP_TOKEN连接。令牌只放在当前页面内存中，刷新即清除；接口验证Bearer、浏览器Origin和写入请求头，不使用查询参数传令牌。界面没有发送QQ测试消息的按钮。

- 「读取可见控件」展示私有无障碍树，可以筛选、选择控件查看精确选择器，不切换会话。
- 「保存并暂停」原子保存配置并清除旧核验标记，旧配置保留为config.json.previous。运行中的绑定地址和端口不在这里修改。
- 在远程桌面手动打开目标会话，点「核验当前会话」，校验可见本人、目标与消息发送者数字身份；不点击联系人或发送消息。附件核验勾选框仅代表维护者已手工核对该流程，不代表程序已经通过真实QQ附件收发验收。
- 所有绑定核验后才能启用；启用前再次读取本人身份。读不到所需字段时拒绝启用。配置变更有版本校验，旧页面不能覆盖新设置。
- 附件暂存区仅上传/下载私有文件，不发送QQ。没有凭昵称猜数字身份的功能。

### 手工选择器

1. 正常打开官方QQ并由账号所有者完成登录。不要在账号受限时自动反复尝试。
2. 使用 `applications` 得到实际应用名，填入 `app_name`。用 `inspect` 观察现有控件，使用精确名称/角色或索引路径，不能凭空填写选择器。
3. `identity`必须定位到可见的本人QQ数字字段，`self_id`必须与它逐字一致。当前官方客户端是否在可操作的消息界面持续暴露这个字段，仍须登录后验证。
4. 按 `binding.example.json` 添加私聊/群会话。`id`由本人核实；`target_identity`必须读取当前目标的真实 QQ 号/群号，`sender_id`必须读取消息行内真实发送者 QQ 号。禁止把昵称、群名片或消息正文映射为 QQ 号，这会造成身份冒用。例子是测试结构，不能直接当QQ的真实选择器。
5. 标定稳定的 `row_key`（accessible_id/description/name）、消息体和真实发送者数字字段。重复行键会停止本轮事件读取。**若真实 QQ 界面没有暴露这些身份字段，当前原型无法安全接管该会话，必须保持禁用；不使用昵称猜测或 OCR 兜底。**
6. 完成可见界面核实后设置该绑定 `verified=true`，最后 `enabled=true`。每次QQ界面更新都可能需要重校准。

输入框有内容、QQ切换账号、控件变动或存在歧义时应先修正校准，不要用坐标猜点兜底。默认2秒扫描一个会话，32个会话轮询可能超过一分钟；本方案牺牲吞吐换取可观察的桌面操作。

### 图片和文件

OneBot `send_private_msg/send_group_msg`支持纯文本或**单个**image段，不混发多个附件/文字；图片的file字段支持`base64://...`或本适配器返回的私有媒体句柄。现有Yukino Core表情包发送使用Base64图片段，可以复用此入口。额外的`upload_private_file/upload_group_file`是兼容扩展，不属于OneBot11公共API；其file字段使用媒体句柄或Base64，name为文件名。暂不支持子文件夹参数。

私有媒体目录为`--data/media`：inbox存放由官方QQ下载的文件，outbox保存已校验的内容。单文件8MiB，总配额256MiB；图片允许PNG/JPEG/GIF/WebP，文件额外允许PDF、ZIP/CBZ、TXT/MD。不读取任意绝对路径、不访问网络URL；不支持时明确拒绝，不忽略消息中的未实现段。文件内容不解压、不执行。

每个绑定可选`attachments`配置。下面仅是受控GTK测试窗口的结构，**不是实际QQ选择器**：

```json
{
  "verified": false, "kinds": ["image", "file"],
  "button": {"name": "Attach"},
  "dialog": {"name": "Attachment picker"},
  "path": {"name": "Attachment path"},
  "accept": {"name": "Open attachment"},
  "row_kind": {"path": [0, 3]},
  "row_name": {"path": [0, 4]},
  "row_path": {"path": [0, 5]}
}
```

button、dialog在QQ应用内定位；path、accept相对于文件选择窗口定位；row_kind/name/path相对于每条消息行定位。row_kind必须读取真实附件类型字段text/image/file；row_name是实际文件名；row_path可选，必须是官方QQ真实显示的下载路径。不得将正文、备注或用户自填文本当作这些字段。若选择文件后还有确认窗口，可添加final_send选择器，先核实它的行为。无法校准就保持附件verified=false。

接收图片事件的data.file为本地媒体句柄，附ui_local_media标记；文件段是本桥扩展。读取内容可使用带Bearer认证的`GET /console/api/media/{handle}`，或者`get_image`取得本地路径与鉴权下载地址。**跨容器客户端不能直接读取桥的本地路径；未适配鉴权下载的第三方内核不保证可显示收到的图片。** 没有将接口令牌拼进图片URL。暂未实现平台原始文件ID、下载任务和完整NapCat文件API。

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
# 在拥有DISPLAY和D-Bus的Linux会话，测试真实GTK文字和附件控件，没有QQ收发：
python3 scripts/gtk_acceptance.py
```

47项协议、校准、媒体边界和去重测试，以及GTK窗口的中文输入、单次文字点击、图片/ZIP附件选择、发送行核对和草稿保护可以独立执行。协议/状态测试与GTK无障碍控件验收不等于QQ实机验收。账号限制解除后，仍须验证真实界面是否提供所需数字身份字段、选择器、附件行标识、收发、重启去重及超时不重发。

只打包本适配器源码与许可、不含QQ客户端和实例数据：`python3 scripts/build_source.py --output ./dist/source`。

接口参考[OneBot 11](https://github.com/botuniverse/onebot-11/blob/master/communication/ws.md)，系统界面操作参考[GNOME AT-SPI](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/class.Accessible.html)。本目录遵循仓库MIT许可；操作系统组件和QQ保留各自许可。
