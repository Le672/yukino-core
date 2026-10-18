# 限时正则测试

独立插件 ID：`x_regex`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

带捕获分组与位置的正则匹配，独立短超时保护。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/正则测试`。

简写 `模式|文本` 使用第一个竖线分隔；含竖线的模式请用 `{"pattern":"cat|dog","text":"cat dog"}`。模式最多 300 字符、文本 4000；使用固定解释器子进程，无 shell/eval，超时终止子进程。

示例：

```text
/正则测试 ([a-z]+)([0-9]+)|abc123 中文
```

## 面板配置

- `timeout_ms`：正则子进程超时毫秒；默认 `500`。
- `match_limit`：最多显示匹配项；默认 `10`。
- `ignore_case`：忽略英文字母大小写；默认 `False`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
