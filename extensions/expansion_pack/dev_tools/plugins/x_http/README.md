# HTTP状态与MIME速查

独立插件 ID：`x_http`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

标准HTTP状态解释和扩展名MIME双向查询。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/HTTP状态`、`/MIME`。

状态码数据来自运行时 Python HTTPStatus；未登记状态显示所属类别。MIME 仅按标准库扩展名表推断，不读取文件、不验证真实内容。

示例：

```text
/HTTP状态 404
```

## 面板配置

- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
