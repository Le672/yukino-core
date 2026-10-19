# PyPI / npm 包版本

- `/包版本 PyPI requests`
- `/包版本 npm vue`
- `/包版本 npm @types/node`

PyPI 使用固定 `https://pypi.org/pypi/<name>/json` 的 `info.version`；npm 使用固定 `https://registry.npmjs.org/<name>/latest` 的 latest 标签。npm 的 latest 由发布者设置，不保证是数值最高版本。

返回版本、简介、作者/发布者名称字段、许可证字段、Python/Node 要求和官方包页。缺失字段标记“未提供”；字段为注册表提供的数据，不是安全、正版、维护状态或许可证合规认证。不会安装包、执行代码或下载包文件。

支持标准 ASCII PyPI 包名和小写 npm 包名，包含 npm `@scope/name`；不接受自定义 URL、代理、Cookie、Token 或重定向。公开404/403/429状态会明确显示。

默认成功结果缓存300秒，最多100项，固定于内核分配的 `context.data_dir/network-cache.sqlite3`，数据库限8 MiB。结果显示原查询时间；命中缓存会明确标记，失效后查询失败不会拿旧数据冒充最新。缓存只含公开元数据，不存 QQ、Token、Cookie 或私有仓库信息；设置缓存0秒会停止读写缓存结果。成功结果可在同一插件的数据目录内共享。

网络默认总预算15秒（可设1—15秒）。**仅固定 PyPI `/pypi/<name>/json` 端点的响应上限为8 MiB**，以容纳 numpy、Pillow 等常见包的完整元数据；npm 仍为1 MiB。传输正文和解压后的正文均分别受该上限约束，超过即拒绝，不会截断 JSON 冒充成功。复用现有 `safe_net.py` 的 DNS/IP/TLS 防护，仅为禁止重定向、锁定 `pypi.org` 的 GET 元数据端点增加此限额：校验全部 DNS 结果为公网 IP、连接固定 IP、验证 TLS 主机名与证书；只有系统 DNS 全部返回 198.18.0.0/15 Fake-IP 时，才使用固定 8.8.8.8 引导的 Google DoH 公网解析（该服务会收到查询域名），仍共享总预算。系统代理与 Cookie 不参与请求。超大响应、网络不可达或缓存异常会明确失败。

安装包包含本插件 `worker.py`、`plugin.json`、README 和共享 `network.py`、`safe_net.py`、`common.py`。**手动安装 ZIP 后，必须先在「配置参数」将「执行超时（秒）」设为20并保存，再启用。**面板不会自动读取 `recommended_settings.json`；内核默认2秒不足以容纳15秒网络预算，可能在正常查询完成前杀死 worker。Windows 捆绑安装会应用打包参数。可在面板配置缓存秒数、网络预算与回复字数；回复过长会保留完整行并提示截断。
