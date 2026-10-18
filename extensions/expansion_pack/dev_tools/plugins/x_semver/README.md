# 语义版本比较

独立插件 ID：`x_semver`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

严格SemVer排序，包括预发行数字标识与构建元数据。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/版本比较`。

严格要求 MAJOR.MINOR.PATCH，拒绝前导零。预发行数字项按数值、非数字按 ASCII 比较；正式版优先级高于预发行，+build 不参与优先级。

示例：

```text
/版本比较 1.2.3-alpha.2 1.2.3-alpha.10
```

## 面板配置

- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
