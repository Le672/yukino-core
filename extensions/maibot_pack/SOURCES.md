# 功能发现与数据来源

检索日期：2026-10-17。麦麦官方 `plugins.json` 当时有410条登记。这里只记录功能参考，并不表示对应作者参与或认可本项目。Yukino 新实现采用所附 MIT 许可；没有复制上游实现、牌面、图片或文本库。

| 功能需求 | 参考项目 | 本版实现 |
| --- | --- | --- |
| F1 | [Chi8wah/f1-info-plugin](https://github.com/Chi8wah/f1-info-plugin)（官方索引登记；仓库内容本轮未能完整读取） | 依据Jolpica公开接口文档独立写请求、时区与格式化，不使用上游代码或FastF1遥测 |
| 群管与分级权限 | [DrSmoothl/maibot-group-admin-plugin](https://github.com/DrSmoothl/maibot-group-admin-plugin) | 独立原生处理，实时核对QQ角色，只操作当前群；未复制其GPL实现 |
| 提醒 | [xhstoyea/maibot-reminder](https://github.com/xhstoyea/maibot-reminder) | 使用明确时间格式的持久化任务，不让模型自动决定发送对象 |
| 关键词回复 | [FlandreSatori/maibot_plugin_keywords_reply](https://github.com/FlandreSatori/maibot_plugin_keywords_reply)（索引登记） | 精确/包含匹配、主人配置和冷却，不执行正则或脚本 |
| 答案之书、早晚安 | [NetaJuutilainen/Maibot_fun_pack_plugin](https://github.com/NetaJuutilainen/Maibot_fun_pack_plugin) | 自有短句与消息时间记录，不复制900条原词库或图片 |
| 猜词 | [jh-mmm/maibot-wordle-plugin](https://github.com/jh-mmm/maibot-wordle-plugin) | 独立规则、短单词表、重复字母计数，不复制原数据 |
| 塔罗 | [A0000Xz/MaiBot-Tarots-Plugin](https://github.com/A0000Xz/MaiBot-Tarots-Plugin)（索引登记） | 22个常见牌名、自有短句，不带牌面图片，不声称完整占卜体系 |
| 游戏更新查询 | [AirLibrax/game-update-watcher](https://github.com/AirLibrax/game-update-watcher)（索引登记） | 仅按AppID查询Steam公开新闻，不后台追踪或推送 |
| 投票、便签、待办、语录、猜数字、猜拳、选择、时间与编码 | 通用机器人能力补充 | 均为Yukino独立实现，不归属某个上游插件移植 |

## 公开数据接口

- [Jolpica F1 API文档](https://api.jolpi.ca/docs/) 与 [Ergast兼容接口](https://api.jolpi.ca/ergast/f1/)。调用缓存5分钟；查询结果注明来源。数据使用应遵守其[使用条款](https://github.com/jolpica/jolpica-f1/blob/main/TERMS.md)，本包的代码许可不覆盖第三方赛事数据。
- [Steam ISteamNews](https://partner.steamgames.com/doc/webapi/ISteamNews)，仅公开新闻元数据与短摘要，不下载游戏、补丁或付费内容。

Yukino 核心既有许可审查中的历史来源和依赖待确认事项继续保留，不因本包独立实现而视为已经全部完成。
