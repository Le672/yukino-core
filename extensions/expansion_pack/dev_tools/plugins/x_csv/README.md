# CSV结构与数值统计

独立插件 ID：`x_csv`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

解析含引号CSV，统计缺失、唯一值及数值列平均值。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/CSV统计`。

支持 CSV 引号转义和单元格内换行，默认首行表头。每次最多 1000 行、50 列。

示例：

```text
/CSV统计 姓名,分数
小明,90
小红,80
```

## 面板配置

- `delimiter`：分隔符：逗号、分号或制表符；默认 `,`。
- `has_header`：首行为表头；默认 `True`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
