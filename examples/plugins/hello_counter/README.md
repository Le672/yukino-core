# 问候与计数示例

适用 Yukino Core 0.4.0、Python 3.12+，仅使用 Python 标准库。

- `/hello 小明`：问候并给本人在当前会话的计数加一。
- `/hello_count`：只读查看本人在当前会话的计数，不新增次数。
- 插件面板参数：问候语、计数开关、名字长度和回复风格。
- SQLite 保存在宿主提供的 `data_dir` 内，按 `(session_id, user_id)` 隔离。没有QQ、模型或网络调用。

在内核源码根目录执行：

```powershell
python -X utf8 examples/plugins/hello_counter/build.py --output ./dist/plugin-guide
python -X utf8 examples/plugins/hello_counter/verify.py --package ./dist/plugin-guide/hello_counter-1.0.0.zip
```

`build.py` 只将 `plugin.json`、`worker.py`、本说明和 `LICENSE` 四个文件放进安装包，并输出SHA-256。默认不覆盖已有包。`verify.py` 在临时实例中用真实安装器与进程宿主验证；无QQ、模型或外部网络连接，不修改正式实例。

控制台「插件管理 → 从本地 ZIP 安装插件」导入打包结果，新插件默认关闭。启用后发送上面的命令。更新同ID包前先备份，再移除旧插件并导入新包；数据按ID保留。请先阅读项目 `docs/PLUGIN_AUTHORING.md`。

此示例是教学模板，不在正式机器人上自动安装。
