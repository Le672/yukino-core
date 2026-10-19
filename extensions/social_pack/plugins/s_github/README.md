# GitHub 正式发行查询

- `/GitHub发行 Le672/yukino-core`
- 返回 tag、发布时间、原始发行页，以及最多5个附件名称与链接。
- 只调用 `https://api.github.com/repos/<owner>/<repo>/releases/latest`；禁止自定义 URL、代理、Cookie、Token 和重定向。
- GitHub 的 latest 正式 Release 由 GitHub 接口决定；草稿和预发行不会作为结果。不表示“按创建时间排序的所有版本”。
- 只展示公开元数据，不复制整段发行说明，不下载或执行附件，不宣称附件安全。
- 404 表示未找到仓库/正式发行或仓库不公开；403、429 明确提示拒绝或限流，不尝试绕过。

默认成功结果缓存300秒，最多100项，固定于内核分配的 `context.data_dir/network-cache.sqlite3`，数据库限8 MiB。结果显示原查询时间；命中缓存会明确标记，失效后查询失败不会拿旧数据冒充最新。缓存只含公开元数据，不存 QQ、Token、Cookie 或私有仓库信息；设置缓存0秒会停止读写缓存结果。成功结果可在同一插件的数据目录内共享。

网络默认总预算15秒（可设1—15秒），响应解压前后最多1 MiB。复用现有 `safe_net.py`：校验全部 DNS 结果为公网 IP、连接固定 IP、验证 TLS 主机名与证书；只有系统 DNS 全部返回 198.18.0.0/15 Fake-IP 时，才使用固定 8.8.8.8 引导的 Google DoH 公网解析（该服务会收到查询域名），仍共享总预算。系统代理与 Cookie 不参与请求。超大响应、网络不可达或缓存异常会明确失败。

安装包包含本插件 `worker.py`、`plugin.json`、README 和共享 `network.py`、`safe_net.py`、`common.py`。**手动安装 ZIP 后，必须先在「配置参数」将「执行超时（秒）」设为20并保存，再启用。**面板不会自动读取 `recommended_settings.json`；内核默认2秒不足以容纳15秒网络预算，可能在正常查询完成前杀死 worker。Windows 捆绑安装会应用打包参数。可在面板配置缓存秒数、网络预算与回复字数；回复过长会保留完整行并提示截断。
