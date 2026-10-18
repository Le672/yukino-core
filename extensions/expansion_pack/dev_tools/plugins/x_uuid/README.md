# UUID生成与解析

独立插件 ID：`x_uuid`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

随机UUIDv4、可重复的命名UUIDv5及结构解析。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/UUID`、`/UUID解析`。

默认 `/UUID` 生成 v4，`/UUID 4 3` 生成三个；`/UUID 5 dns|名称` 支持 dns/url/oid/x500 或 UUID 命名空间。UUID 不是密码或加密令牌。

示例：

```text
/UUID 5 dns|example.com
```

## 面板配置

- `count`：默认UUIDv4生成数量；默认 `1`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
