# Yukino 实用扩展包 1.0.0

本包为 Yukino Core 0.5.0 新增63个独立插件：开发与数据工具、生活与协作、文字游戏各21个。每个都有独立ID、指令、配置和安装包，不是把命令别名算成新插件。各组目录的README和每个插件README给出准确玩法与边界。

全部使用Python标准库，核心功能不需要额外模型、付费API或第三方账号。不包含别人的代码、图片、文本资料库、聊天记录、运行数据库或凭据。工具查询主要分析用户提供的数据及内置参考；没有声称接入新的实时外部网站。

```powershell
# 在内核源码根目录执行，使用已安装项目依赖的Python环境。
python extensions/expansion_pack/build_pack.py --output ./dist/expansion-pack-1.0.0
python extensions/expansion_pack/verify_pack.py --packages ./dist/expansion-pack-1.0.0 --report ./dist/expansion-verification.json
python -m unittest discover -s extensions/expansion_pack/dev_tools/tests -v
python -m unittest discover -s extensions/expansion_pack/life_tools/tests -v
python -m unittest discover -s extensions/expansion_pack/games/tests -v
```

输出63个单插件安装ZIP、一个开发源码ZIP、SHA-256及pack-manifest。安装时选择单插件ZIP，统一源码ZIP用于开发。通用新实例不会自动安装本包；控制台导入后默认停用，核对参数后启用。依赖0.5.0是为了支持超过50个外部插件；每个worker仍采用文字 `handle(text, context)` 协议。

数据写入每插件固定的 `plugins/state/<ID>`，源码重装不改变其位置。个人数据按会话和用户隔离，协作数据按会话与创建者权限管理。首次体验请先查看相应插件帮助，输入内容使用普通空格或说明中要求的JSON、竖线。

按用户隔离表示不能通过指令读取他人的记录；回复仍发送到原会话，群里的输入与回复对群友可见。游戏对局是短期状态：31天未操作的记录会清理，每个游戏最多保留5000个会话／用户桌；聊天历史与其他插件数据不受此规则影响。

番茄钟、复习、纪念日和工时提供查询/记录，不会自动后台推送；只有内核已有的提醒功能负责定时QQ发送。娱乐游戏没有金钱、赌注或支付。棋盘是文字显示，没有承诺图片UI或实时多人动画。

进程数量由内核统一管理，默认最多8个常驻worker；插件安装数量不等于常驻进程数量。空闲进程被回收时，SQLite数据继续保留。联网、媒体发送、群管和模型调用的公共SDK边界以核心插件编写指南为准。
