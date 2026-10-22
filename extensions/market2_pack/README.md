# Yukino 市场功能扩展（二）1.1.0

适用 Yukino Core 0.7.0+，8 个独立插件。参考麦麦市场及作者公开仓库的功能，采用 Yukino 自有 worker 接口独立实现；不支持直接加载麦麦 SDK 插件。来源核对见 [SOURCES.md](SOURCES.md)。

| 功能 | 插件 ID | 首次使用 |
| --- | --- | --- |
| RSS个人订阅与手动刷新 | `m_rss` | `/订阅源 帮助` |
| Bangumi搜索、详情与放送 | `m_bangumi` | `/番剧 搜索 星际牛仔` |
| 神奇海螺缩写释义 | `m_abbr` | `/缩写 yyds` |
| 公开HTTPS网页正文摘录 | `m_webtext` | `/网页正文 https://www.python.org/` |
| Bing每日壁纸原图 | `m_bing` | `/每日壁纸` |
| ICS个人课表 | `m_ics` | 私聊发送 `/课表 帮助`，按说明粘贴ICS导入 |
| 群聊文字UNO | `m_uno` | 群内 `/UNO 创建`，加入后由创建者开始 |
| 麻将听牌、和牌结构与原创训练 | `m_mahjong` | `/听牌 123m123p123s1112z` |

每个插件的 [plugins 目录](plugins) 有完整 README 和参数说明。安装后在控制台搜索 `m_` 可以找到本包插件，按上表 ID 核对。联网指令在任何网络问题时返回明确失败，不能保证第三方站点一直开放。

## 明确的能力边界

- 响应发送到当前会话，联网查询不调用模型。Bing发送实际原图与版权说明；番剧查询附封面与详情；长内容排版为结果卡。
- RSS按用户和会话保存订阅、阅读去重，用户手动刷新；不会后台推送或把文章自动注入模型上下文。
- UNO的群消息只展示公共状态。每个玩家主动私聊 `/UNO 手牌 房间码` 获取本人手牌；不支持替别人领牌、机器人参与、图形客户端、主动私聊发牌或下注。
- ICS仅私聊纯文本导入，不处理文件/网址、云端OCR或主动提醒。支持 `/课表 本周` 和 `/课表 导出`，后者发送可导入日历的ICS文件。仅支持说明列出的周重复规则；不支持的规则会拒绝整份导入，保留之前课表。显示时区为UTC+8。
- 麻将计算的是无副露、无万能牌的结构和牌与理论余张，不判断役、番种、分数或真实牌山余张，不把何切偏好当作唯一答案。
- 网页抓取只允许公开HTTPS标准443端口，逐跳检查公网DNS并固定连接IP，保留TLS主机名校验，限制大小、跳转和总耗时。不会携带私密Cookie、站点登录、代理认证或读取本地文件。

宿主只注入可信 `context.data_dir` 作为插件状态目录；有些 worker 把它映射为内部 `plugin_data_dir`，消息内容不能覆盖数据路径。停用插件、空闲进程回收和更新源码不会自动清空这些 SQLite 状态。

## 构建和验证

在已安装核心依赖的源码根目录执行：

```powershell
python -m unittest discover -s extensions/market2_pack/tests -v
python extensions/market2_pack/build_pack.py --output ./dist/market2-pack-1.1.0
python extensions/market2_pack/verify_pack.py --packages ./dist/market2-pack-1.1.0 --report ./dist/market2-workers.json
```

构建生成8个独立ZIP、源码ZIP、SHA-256和清单。统一源码ZIP用于开发，控制台安装时选择独立插件ZIP。联网插件推荐公共执行超时25秒；本包安装清单提供该设置，避免使用核心2秒默认值提前结束正常请求。

`verify_pack.py` 使用独立临时实例和真实 worker，不发送QQ消息或调用模型。联网帮助烟测只证明加载与路由，真实站点连通性须另行检查；本包清单中的 `live_cases` 给出公开验收命令，不等于外部接口永远可用。

使用本包MIT源码不自动包含上游网站文章、Bing壁纸或第三方素材的再分发许可。发行时保留SOURCES、LICENSE、各插件说明，把用户配置、SQLite与凭据单独作为私人实例备份。
