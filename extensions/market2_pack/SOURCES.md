# 市场功能参考与来源

本包从 [麦麦插件商店](https://plugins.maibot.chat/) 及 [公开索引](https://github.com/Mai-with-u/plugin-repo) 发现功能，由 Yukino Core 独立实现。不是麦麦 SDK 兼容层；没有复制下列仓库的实现、题库或图片资产。自有代码使用附带的 MIT LICENSE。

2026-10-17 在市场当前界面核对到的对应功能为 [RSS](https://plugins.maibot.chat/plugin/com.0-hz.rss-reader)、[Bangumi](https://plugins.maibot.chat/plugin/github.fffold.bangumi-browse-plugin)、[缩写翻译](https://plugins.maibot.chat/plugin/XXXxx7258.nbnhhsh_translate_plugin)、[网页抓取](https://plugins.maibot.chat/plugin/com.0-hz.fetch-url)、[UNO](https://plugins.maibot.chat/plugin/juzi865.uno-game-plugin) 和 [麻将训练](https://plugins.maibot.chat/plugin/github.QlzqQlzq.mahjong-quiz-plugin)。部分作者已更新插件或更换命名；下表也保留早先功能参考仓库。Bing 和 ICS 是同类公开作者仓库的补充功能，当前市场搜索未找到对应条目，不宣称它们此时仍在商店上架。

| 功能 | 功能参考仓库 | 上游标注许可／边界 |
| --- | --- | --- |
| RSS阅读 | https://github.com/yufei-pan/MaiBot-rss-reader | MIT；本实现仅按需刷新，不自动推送 |
| Bangumi查询 | https://github.com/FFFold/bangumi_browse_plugin | MIT；本实现使用公开API |
| 缩写释义 | https://github.com/YukiSakiko/nbnhhsh_translate_plugin | MIT；解释取自神奇海螺接口，不编造 |
| 网页正文 | https://github.com/yufei-pan/maibot-fetch-url-plugin | MIT；本实现不带Jina、登录Cookie或PDF处理 |
| Bing每日图信息 | https://github.com/ikun-11451/bing_daily_image_plugin | GPL-3.0；仅独立实现公开元数据查询，没有复制代码或分发图片 |
| ICS课表 | https://github.com/BUNNY-19C/maibot-class_schedule-plugin | MIT；作者仓库已核实，市场索引的收录状态未完全确认 |
| UNO文字房间 | https://github.com/juzi865/uno-game-plugin | MIT；原创规则引擎，不复制图形界面和牌面 |
| 麻将听牌训练 | https://github.com/QlzqQlzq/MaiBot-Mahjong-Quiz-Plugin | MIT；原创建题和结构判定，不复制题库 |

上游许可是来源说明，不代表本包包含上游代码或获得官方背书。公开API、网站内容和壁纸版权仍属于相应服务与权利人；解析和查询可能受网络或接口变化影响。接口失败必须明确返回错误，不能以缓存旧内容冒充实时结果。

本包只通过宿主向当前会话返回文字，不主动跨会话发送，不声明支持图片、文件附件、后台订阅推送、网页Cookie或自动登录。
