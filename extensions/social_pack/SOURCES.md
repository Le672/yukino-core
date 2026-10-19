# 来源与功能参考

2026-10-19 核对麦麦[插件市场](https://plugins.maibot.chat/)与[官方公开索引](https://github.com/Mai-with-u/plugin-repo/blob/main/plugins.json)。本包使用 Yukino 自有文字 worker 接口，各实现与文案均为本项目新增，不包含上游源码、SDK、故事、题库、图片或用户数据，也不代表可安装 MaiBot 原版插件。

- 漂流瓶：索引收录 [AkariRin/drift-bottle-plugin](https://github.com/AkariRin/drift-bottle-plugin)。原仓库 README 本次未能读取，因此只把索引当功能发现依据，未声称核实其完整实现或许可。本包只提供本群内投递与捞取。
- 口袋远征：参考市场中的[文字探索类别](https://github.com/silencieuxzero/backrooms_escape)，使用自有地点、物品与规则，不复制后室故事、角色或图像，不提供 AI NPC。
- 数独规则：[Nikoli](https://www.nikoli.co.jp/en/puzzles/sudoku/)；数织规则：[Conceptis](https://www.conceptispuzzles.com/index.aspx?uri=puzzle/pic-a-pix/rules)。题目程序生成或由本项目编写，求解设上限。
- GitHub 发行查询：[GitHub Releases API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)。软件包版本：[PyPI JSON API](https://docs.pypi.org/api/json/)、[npm Registry API](https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md)。查询只显示必要的公开元数据，不下载或执行附件。
- 时光信、心情与手动睡眠时段记录为独立新增功能，不假装是麦麦消息处理链路的睡眠管理插件。
- 132 条戳一戳 / 搭话文案为项目原创，不摘抄第三方语录库。网络防护代码复用本仓库的 `market2_pack/safe_net.py`，保持其固定公网 DNS/IP、TLS 验证和有界请求策略。

各服务的公开元数据及其可用性仍由相应服务提供。项目代码遵循随包 MIT LICENSE；参考项目的许可只适用于参考项目本身。
