# JSON格式与路径取值

独立插件 ID：`x_json`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

严格JSON格式化、数组/属性路径和JSON Pointer取值。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/JSON格式`、`/JSON取值`。

取值语法：`a.b[0]|JSON`，含特殊属性名时使用 `/a/b/0|JSON`。JSON 最大 40 层、10000 个节点；拒绝 NaN/Infinity。

示例：

```text
/JSON取值 user.name|{"user":{"name":"雪乃"}}
```

## 面板配置

- `indent`：格式化缩进空格；默认 `2`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
