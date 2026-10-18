# 配置与数据

## 主人身份

在「平台连接」设置`owner_ids`，按QQ账号全局识别，私聊和群聊使用同一名单。0.6.2起，模型对话也会接收由内核根据实际发送者生成的`bot_owner`标记；不把完整主人名单或管理令牌发送给模型。昵称、消息自称或引用他人的话不会授予主人身份，实际管理指令仍独立核验QQ账号。主人设置属于私人实例数据，不写入通用发行包。

配置入口以控制台为主。不同服务的令牌与账号保存在实例目录中，不作为公开配置导出。修改前备份实例；手工编辑 JSON 时使用 UTF-8，并在停机时编辑。

| 文件 | 内容 | 处理方式 |
| --- | --- | --- |
| `config.json` | 名称、人设、触发、上下文、队列、消息限制、插件开关和设置 | 可从中性 `config.example.json` 起步；导出前检查内容 |
| `connections.json` | QQ 地址、账号、主人、OneBot 令牌、接收和重连参数 | 私密，不提交版本库 |
| `providers.json` | 模型档案、模型名称、地址、API Key 和默认模型 | 私密，不提交版本库 |
| `secrets.json` | 管理令牌及兼容旧部署所需令牌 | 私密；实例初始化随机生成 |
| `core.db` | 会话、历史、记忆和事件 | 用户数据，不随源码发行 |
| `resources/` | 用户导入的图片、用语、知识与编辑数据 | 只有获得分发授权的素材才能另行发布 |
| `plugins/` | 外部插件清单、代码及组件配置 | 只安装可信代码，分别核查许可证 |

## 对话与队列

`name` 与 `persona` 控制回复角色；`trigger_names` 控制群触发名称。`private_enabled`、`group_enabled`、`group_allowlist` 控制聊天范围。`history_limit` 控制带入模型的历史条数；`merge_window_ms` 控制短时间消息合并；`max_pending` 控制待处理队列上限。`response_max_chars` 限制发送长度；`provider_timeout` 是模型调用等待时间。`web_prompt_marker` 是网页桥兼容标记，新发行实例为空。

| 类别 | 其余可调字段 | 作用 |
| --- | --- | --- |
| 访问范围 | `private_allowlist`、`user_blocklist`、`group_blocklist`、`ignore_bots` | 限制允许与禁止处理的私聊、群和用户 |
| 群触发 | `mention_enabled`、`reply_trigger_enabled`、`trigger_prefix_enabled`、`group_reply_all` | 配置 @、引用回复、名称开头和全部消息触发 |
| 上下文 | `history_char_limit`、`current_message_max_chars` | 限制累计历史和本轮输入长度 |
| 记忆与检索 | `memory_enabled`、`memory_limit`、`resource_lookup_enabled`、`resource_limit` | 控制每轮注入记忆与本地资料的数量 |
| 图片 | `emoji_enabled`、`emoji_max_per_reply` | 控制模型回复的表情图片 |
| 节流 | `session_cooldown_ms`、`session_max_pending` | 会话冷却与会话内待处理数量 |
| 运行 | `command_prefix`、`command_max_chars`、`health_interval` | 指令前缀、插件输入长度和连接检查间隔 |

模型设置文件包含 `profiles` 与 `default_id`。每个档案使用 `id`、`name`、`type`、`base_url`、`model`、`enabled`、`api_key`、`temperature`、`top_p`、`max_tokens`、`timeout` 与 `extra_body`。参数是否生效取决于所选协议和服务；服务不支持时应查看连接测试结果。初始 `profiles` 为空，面板应显示未配置状态。

QQ 设置包含 `onebot_url`、`self_id`、`owner_ids`、`receive_enabled`、`onebot_token`、`action_timeout`、`reconnect_min` 与 `reconnect_max`。连接后的实际在线状态与配置保存成功是两回事，应以状态页和真实收发测试为准。

## 环境与运行参数

`--host`、`--port`、`--data`、`--resources` 是启动参数；它们涉及监听和存储位置，应通过启动命令或部署文件调整。`--legacy` 用于显式只读兼容旧图片注册记录，不会自动导入旧聊天或人物设置；发行实例不设置此项，也不提供旧数据库。

旧环境变量保留迁移兼容，包括 `YUKINO_SECRETS_FILE`、`YUKINO_ADMIN_TOKEN`、`YUKINO_ONEBOT_URL`、`YUKINO_ONEBOT_TOKEN`、`YUKINO_SELF_ID`、`YUKINO_OWNER_ID`、`YUKINO_PROVIDER_URL`、`YUKINO_PROVIDER_TOKEN` 与 `YUKINO_RECEIVE_ENABLED`。新实例通过控制台管理连接与模型；不要继续把账号写死在 compose 中。

修改监听端口或数据挂载后需要重启进程或容器；对话设置、连接和模型的保存应遵循控制台给出的生效状态。若配置损坏，保留原文件并恢复上一份备份，不要删除数据库来“重置”。
