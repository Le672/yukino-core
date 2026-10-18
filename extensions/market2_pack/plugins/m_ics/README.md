# ICS 个人课程表

原创 stdlib 实现，参考课程表插件的功能目标，不使用上游代码。不接收文件或网址，也不会后台刷新或主动发送提醒。

在**私聊**发送 `/课表 导入`，换行粘贴完整 `BEGIN:VCALENDAR` 到 `END:VCALENDAR`。发送 `/课表 示例` 获得可用文本。整条命令最多4000字；超过上限请精简课程与说明，不能分片导入。

```text
/课表 导入
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:my-course
SUMMARY:数据结构
DTSTART;TZID=Asia/Shanghai:20261019T090000
DTEND;TZID=Asia/Shanghai:20261019T103000
RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=4;BYDAY=MO
EXDATE;TZID=Asia/Shanghai:20261026T090000
LOCATION:教学楼101
END:VEVENT
END:VCALENDAR
```

- `/课表` 或 `/课表 今天`：今天课程。
- `/课表 明天`、`/课表 2026-10-19`：指定日期。
- `/课表 历史`：本用户在本私聊的版本编号。
- `/课表 恢复 1`：恢复自己的版本，旧版本继续保留。
- `/课表 清空`：归档当前课表，可以从历史恢复。
- `/课表 帮助`：操作说明。

支持时间值 `YYYYMMDDTHHMMSS`，UTC 在末尾加 `Z`；命名时区支持 `Asia/Shanghai`、UTC，以及文档中明确的固定 UTC+8 别名。无 TZID 的时间按固定 UTC+8 解释，查询统一 UTC+8。只接受1992～2099年，单节课程最长48小时；可显示跨午夜课程。

RRULE 只支持 `FREQ=WEEKLY`、`INTERVAL=1..52`、`COUNT=1..10000`、`UNTIL`、不带序号的 `BYDAY=MO,TU,...,SU`。COUNT 与 UNTIL 不可同时使用。DTSTART 的星期必须在 BYDAY 内；周从周一开始。带 TZID 或 Z 的 DTSTART 要求 UNTIL 为 UTC Z 格式。COUNT 在 EXDATE 排除**之前**计数，排除后不补足次数。

支持 EXDATE 时间列表、空格或 TAB 折行、文本的 `\\`、`\,`、`\;`、`\n` 转义。每条课程必须有唯一 UID、DTSTART、DTEND。未提供 SUMMARY 时如实显示“未提供标题”。不支持全天事件、VTIMEZONE、VALARM、RDATE、EXRULE、RECURRENCE-ID、DURATION、其它 RRULE 字段或取消邀请；遇到这些内容会拒绝整份导入，不保存半份课表。

**面板参数均实际生效：** `max_events` 默认50（5～100）；`max_versions` 默认10（1～30）；`result_limit` 默认20（1～50）。版本达到上限会拒绝新的不同课表，旧数据保留；相同内容不重复占用版本。降低版本上限不会删除已有历史。

数据仅保存在宿主分配的 `data_dir/courses.sqlite3`，按 `session_id + user_id` 隔离。编号不能用于读取别人的课表；群聊请求不导入、不查询，也不会创建数据库。导入经过完整校验后在事务中归档旧版本并保存新版本。
