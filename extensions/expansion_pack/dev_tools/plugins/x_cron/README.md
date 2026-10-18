# Cron解释与下次时间

独立插件 ID：`x_cron`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

标准五字段Cron解析和有界未来触发时间计算。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/Cron说明`、`/Cron下次`。

支持数字、`*`、`,`、`-`、`/`，不支持秒、L/W/# 或月份英文名。日／星期任一字段以 * 开头（含 */步长）时采用 Vixie AND，否则采用 OR。计算严格晚于指定起点；不创建或执行系统任务。

示例：

```text
/Cron下次 0 9 * * 1-5|2026-10-16T09:00:00+08:00
```

## 面板配置

- `utc_offset_hours`：显示时区UTC偏移小时；默认 `8`。
- `result_count`：显示下次时间数量；默认 `3`。
- `lookahead_days`：最大扫描天数；默认 `1096`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
