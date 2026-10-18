# Unicode字符与规范化

独立插件 ID：`x_unicode`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

码点名称类别UTF8字节及NFC/NFD/NFKC/NFKD转换。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/字符信息`、`/文本规范化`。

字符信息按 Unicode 码点统计，组合字形可能包含多个码点。规范化语法 `NFKC|文本`；全角、兼容字符等转换会改变原文本，结果仅作为新消息返回。

示例：

```text
/文本规范化 NFKC|ＡＢＣ１２３
```

## 面板配置

- `character_limit`：字符信息最多码点数；默认 `40`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
