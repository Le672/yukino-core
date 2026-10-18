# Decimal与有理数计算

独立插件 ID：`x_decimal`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

受限AST十进制计算与Fraction精确分数模式。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/精确计算`。

支持数字、括号及 + - * / // % **；`/精确计算 分数 1/3+1/6` 返回 1/2。使用受限 AST 递归求值，无 eval；拒绝函数、变量、属性和代码。幂指数限整数 ±20，表达式最多 600 字符/80 节点。Decimal 采用配置精度，分数模式精确但有容量上限。

示例：

```text
/精确计算 0.1+0.2
```

## 面板配置

- `precision`：Decimal有效数字位数；默认 `28`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
