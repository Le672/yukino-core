# Unix时间戳与ISO互转

独立插件 ID：`x_epoch`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

秒/毫秒时间戳和带时区ISO日期时间双向转换。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/时间戳转换`。

支持 `s:1700000000`、`ms:1700000000000`、ISO 时间。纯数值绝对值 ≥1e11 按毫秒，其他按秒；含糊时请显式加 s:/ms:。无时区 ISO 使用配置偏移，输出同时显示 UTC。

示例：

```text
/时间戳转换 1700000000000
```

## 面板配置

- `utc_offset_hours`：无偏移输入与显示时区UTC小时；默认 `8`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
