# 链接结构与跟踪清理

独立插件 ID：`x_url`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

离线URL解析和常见跟踪参数清理，保留其余参数。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/链接信息`、`/清理链接`。

只接收 HTTP/HTTPS，不打开网址、不跟随跳转。拒绝含用户名或密码的 URL。清理 utm_*、spm、fbclid/gclid 等参数；重要链接应检查清理结果后再使用。

示例：

```text
/清理链接 https://example.com/watch?v=42&utm_source=test&spm=123
```

## 面板配置

- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
