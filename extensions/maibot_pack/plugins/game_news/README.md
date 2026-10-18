# Steam游戏更新

Yukino独立Python实现，参考麦麦插件需求和公开数据接口，不直接加载MaiBot SDK插件。

指令：`/游戏更新 730`，参数为Steam AppID。返回公开新闻标题、摘要与原文链接，缓存5分钟，不下载安装补丁。

参考：麦麦插件索引中的游戏更新查询需求；数据源：https://partner.steamgames.com/doc/webapi/ISteamNews

执行超时建议18秒，无需API密钥。源站不可达、限流或未发布数据时直接报错，不调用模型补写。
