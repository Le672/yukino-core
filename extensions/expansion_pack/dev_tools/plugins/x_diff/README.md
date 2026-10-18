# 多行文本差异

独立插件 ID：`x_diff`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

Unified Diff与字符相似度，支持JSON输入保留竖线。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/文本差异`。

简单输入用 `旧文本|新文本`；包含竖线或多行时使用 `{"a":"旧\n文本","b":"新\n文本"}`。每段最多 200 行、总长度 16000；只比较文本，不读取文件。

示例：

```text
/文本差异 old line|new line
```

## 面板配置

- `context_lines`：差异前后上下文行数；默认 `2`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
