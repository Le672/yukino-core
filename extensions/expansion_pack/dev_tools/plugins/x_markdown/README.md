# Markdown目录与表格

独立插件 ID：`x_markdown`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

跳过代码围栏提取标题目录和CSV转Markdown表格。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/Markdown目录`、`/Markdown表格`。

目录支持 ATX 的 # 到 ###### 标题，忽略 ``` 和 ~~~ 围栏中的内容；不同网站的锚点规则可能不同。表格接受 CSV，多行单元格转为 <br>，竖线转义，最多 20 列。

示例：

```text
/Markdown目录 # 开始
## 安装
## 使用
```

## 面板配置

- `delimiter`：CSV分隔符：逗号、分号或制表符；默认 `,`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
