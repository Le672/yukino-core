# F1赛事与积分

Yukino独立Python实现，参考麦麦插件需求和公开数据接口，不直接加载MaiBot SDK插件。

指令：`/f1 下一站`、`/f1 赛历 2026`、`/f1 积分`、`/f1 车队`、`/f1 成绩 2024 5`、`/f1 排位`。默认当前赛季，成绩默认最近一站，时区默认UTC+8。未公布时间或结果明确标记，不推算或伪造。数据源是Jolpica Ergast兼容API，不提供直播视频或实时遥测。数据源缓存5分钟。

参考：https://github.com/Chi8wah/f1-info-plugin
API文档：https://api.jolpi.ca/docs/

执行超时建议18秒，无需API密钥。源站不可达、限流或未发布数据时直接报错，不调用模型补写。
