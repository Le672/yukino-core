# IP与CIDR离线分析

独立插件 ID：`x_ip`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

IPv4/IPv6地址、网络掩码、地址数与类别分析。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/IP信息`。

支持 IPv4/IPv6/CIDR，仅使用 Python ipaddress 离线分析；不联网查询归属、运营商、黑名单或延迟。IPv4 /31、/32 按标准库地址数量显示。

示例：

```text
/IP信息 192.168.1.10/24
```

## 面板配置

- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
