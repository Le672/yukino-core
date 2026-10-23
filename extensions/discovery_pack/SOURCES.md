# 来源与许可

2026-10-23核查。除明确列出的二维码编码器外，本包为Yukino接口的原创实现；未复制AstrBot/MaiBot插件代码或图片资产，不能直接安装它们的原包。

- 图片功能参考方向：[konley/astrbot_plugin_kimage](https://github.com/konley/astrbot_plugin_kimage)。摸头手势、熊和牌面均用Pillow原创绘制；只读取用户明确发送、同会话引用或指定的网址，不查询QQ头像接口，不上传图片到处理服务。
- 二维码：[Project Nayuki](https://github.com/nayuki/QR-Code-generator)，MIT；仅vendored `python/qrcodegen.py`，原完整版权许可保留。固定提交及SHA-256见 `vendor/provenance.json`；未复制演示图片。
- 限免查询：Epic官方商城公开 `freeGamesPromotions`；实现方式参考[公开抓取项目](https://github.com/s-p-e-c-k/Epic-Games-Store-Free-Games)。筛选有效促销时间、100%折扣与实际零价，排除永久免费商品。封面和商品描述版权归原权利人。
- 项目查询：[GitHub REST仓库搜索](https://docs.github.com/en/rest/search/search#search-repositories)、公开仓库详情；无token，不下载或执行仓库代码。
- 词典：[meetDeveloper/freeDictionaryAPI](https://github.com/meetDeveloper/freeDictionaryAPI)。词条及发音按来源返回，保留Wiktionary/条目许可名称；不声称覆盖全部单词或提供中文翻译。
- 空气质量：[Open-Meteo Air Quality API](https://open-meteo.com/en/docs/air-quality-api)，CAMS模型数据；按文档署名。展示美标AQI及浓度，不混作中国国标AQI或监测站实测。

公开数据源有各自的访问限额与使用条款；GitHub匿名请求会限流。查询有缓存、总耗时和大小上限，失败如实返回，不用过期结果冒充刚查到的数据。源码不包含账户、Cookie、私人图片或服务密钥。
