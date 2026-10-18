# 公开网页正文摘录

插件ID `m_webtext`，版本1.0.0，适配Yukino Core 0.5普通worker；原创独立实现，不加载或执行市场插件源码。

安全获取公开HTTPS网页标题及可见正文摘录。

## 使用

```text
/网页正文 帮助
/网页正文 https://www.python.org/
```

仅抓取HTML/纯文本，不执行JavaScript、不使用浏览器登录状态，不支持文件、图片、PDF、付费墙或登录页绕过。忽略script/style/noscript/template/svg/canvas及导航页脚等标签，提取结果是规则摘录而非AI总结。动态网站可能没有可提取正文；不保证所有网站正文清洗完整。

## 面板配置

- `text_max_chars`：正文摘录最多字符；默认 `2400`。
- `network_timeout_seconds`：单命令联网总预算秒；默认 `12`。

宿主公共执行超时 `timeout` **建议25秒**（默认2秒不够联网），`input_max_chars=4000`、`output_max_chars=6000`。联网预算2～15秒，默认12秒，重定向共用预算；RSS多源刷新也共用一次预算，未完成请指定编号重试。参数保存后下次调用生效。

## 访问与状态边界

仅接收无凭据、标准443端口的公开HTTPS。每一跳验证全部DNS结果，混合内外网地址整个拒绝；使用已验证数字IP直连并保留原主机名做TLS证书/SNI验证，不经环境代理、不会二次DNS连接。拒绝localhost、私网、回环、链路本地、IPv4-mapped IPv6和转换地址；重定向最多3次，响应正文最多1MiB；仅支持identity及有界gzip/deflate流式解压，传输与解压后正文分别最多1MiB，拒绝压缩膨胀、未知编码、截断或多成员压缩。连接和完整读取受总预算限制，失败返回原因，不伪造在线结果。

只返回普通文字，不发QQ附件、私信或后台推送；不执行远程脚本，不读取任意本地文件。RSS持久化只用宿主分配的 `context['data_dir']`，worker映射成内部 `plugin_data_dir`，固定文件名 `market2-rss.sqlite3`，按会话与QQ隔离；其他四项无持久化。消息正文不能提供或覆盖数据目录。


仅当系统DNS返回的全部地址都属于198.18.0.0/15 Fake-IP保留范围时，启用固定Google JSON DoH回退：直连8.8.8.8:443并验证dns.google的TLS主机名，分别查询A/AAAA，共用原请求总预算，响应最多64KiB，要求DNS成功且未截断、查询匹配、所有IP答案均为公网。混合私网/公网、真实私网、解析失败都不会触发回退；198.18地址本身永不作为连接目标。Google仅收到目标域名与记录类型，edns_client_subnet=0.0.0.0/0，不提交网页路径、查询参数或QQ内容；不更改系统DNS或代理。此固定策略无可配置引导IP。官方说明：https://developers.google.com/speed/public-dns/docs/doh/json 。

## 打包

ZIP根目录包含本目录plugin.json、worker.py、README.md，加组根network_engine.py复制为`_network.py`、safe_net.py复制为`_safe_net.py`；开发源码可回退加载组根文件。许可证和功能来源由总构建器统一附带。
