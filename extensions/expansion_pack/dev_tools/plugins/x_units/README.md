# 多量纲单位换算

独立插件 ID：`x_units`，版本 1.0.0，适配 Yukino Core 0.4.0 普通 worker。

温度长度质量速度面积体积数据与时间间隔换算。完全离线，命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：`/单位换算`。

语法：`数值 原单位 目标单位`。支持 m/km/cm/mm/in/ft/yd/mi，kg/g/mg/t/lb/oz，m/s/km/h/mph/knot，m2/km2/cm2/ha，m3/l/ml/gal，b/kb/mb/gb/kib/mib/gib，s/min/h/day，以及 C/F/K。b 表示字节；gal 是美制液体加仑。温度拒绝低于绝对零度。

示例：

```text
/单位换算 32 F C
```

## 面板配置

- `precision`：展示有效位数；默认 `14`。
- `max_output_chars`：单次回复最多字符；默认 `6000`。

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无网络、无任意路径读写。输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
