# 插件结果与媒体消息

0.7.0 增加版本化结果协议，旧文字 worker 继续兼容。公开包包含17个内置功能和119个外部插件；现有部署另有漫画、视频两个独立服务。完整逐项列表见 [138项清单](PLUGIN_OUTPUT_MATRIX.md)。

## 使用变化

- `/搜歌 起风了`、`/点歌 歌名` 或“我想听 起风了”：默认发送可用歌曲的音频文件与封面。长歌附带最多90秒的语音片段，原文件不裁剪。可选 `/选歌 2` 切换本人15分钟内的候选结果，无须先选歌才能播放。
- 发送网易云单曲链接也会自动处理，无需@。搜索/播放先用网易云公开接口，无法取得音频时可使用GD Studio公开API；实际音源显示在消息中。没有可用音频时明确说明失败，不用页面链接冒充播放。
- `/每日壁纸` 发送原图，Steam、B站和番剧查询补充公开封面与实际详情。B站搜索与视频链接解析是不同功能：搜索给结果；发送视频链接由现有解析服务发送视频。
- F1 数据附CSV；开发数据工具附结果文件。课表新增 `/课表 本周` 和 `/课表 导出`，导出ICS保留重复规则、时区和排除日期。
- 棋盘、牌面、解谜等依据真实状态绘图。大段查询和清单自动排版；短确认、计算值、管理回执保持直接文字。热榜继续只显示编号和标题。
- 控制台历史能预览插件图片、播放音频、下载文件。访问这些附件仍要求管理登录。

插件参数中的 `output_mode` 控制自动排版、纯文字或卡片；`export_result` 控制自动附带的长文本/数据文件。参数不替代明确请求的媒体内容。点歌还有直接播放、公开音源回退、语音、原文件及片段长度选项。

## 编写媒体 worker

```python
def handle(text, context):
    return {
        "version": 1,
        "text": "已经生成结果",
        "media": [
            {"type": "image", "url": "https://example.com/public-image.png"},
            {"type": "audio", "url": "https://example.com/song.mp3", "name": "song.mp3", "duration": 120},
            {"type": "file", "name": "result.json", "text": '{"answer": 42}'}
        ]
    }
```

可以返回 `str`、`None` 或上述对象。对象只接受 `version`、`text`、`media`、`layout`；`layout` 可为 `auto/text/card/board`。每次最多6个媒体来源，图片实际发送最多4张。

媒体 `type` 为 `image/audio/file`，且 `url/text/data` 中恰有一个。`url` 使用公开HTTPS；`text` 仅用于不超过40000字符的UTF-8文件；`data` 仅用于不超过50000字符的图片Base64。不能传本地文件路径、QQ动作或目标QQ；内核只向触发消息所在会话发送。

内核分配 `context['data_dir']` 和 `context['rich_result_version']=1`。需要兼容旧内核时可检查后者并返回文字。插件配置 `minimum_core_version` 声明最低版本，新安装器会核对。

## 限额与可靠性

媒体单项最多24MiB，图片最多5MB/2000万像素，缓存最多512MiB。网络连接验证完整DNS结果、实际连接IP和TLS主机名；拒绝本地/内网、凭据URL、非HTTPS和不安全重定向。媒体不转交给QQ端任意读取URL，而由宿主下载、校验后发送。

长歌语音片段使用可用的FFmpeg；Docker版含所需字体和转码器。Windows没有FFmpeg时仍发送原音频文件，控制台可以播放；需要片段时安装FFmpeg并加入PATH。中文卡片优先使用系统微软雅黑/思源字体，可用 `YUKINO_CARD_FONT` 指定字体文件。

媒体各部分独立记录发送状态。结果不确定时不会自动重发；已经成功的音频和图片不因后续附件失败而重复发送。确认送达后，数据库压缩媒体正文为本地资源引用，避免反复存储大段Base64。缓存达到上限后旧预览可能不可用；已经发送到QQ的附件不受缓存清理影响。

## 参考实现与服务

- [pan-ice/maibot-music](https://github.com/pan-ice/maibot-music)：MIT，参考歌曲选择、音频文件和语音交付方式。
- [yt-dlp 网易云实现](https://github.com/yt-dlp/yt-dlp/blob/master/yt_dlp/extractor/neteasemusic.py)：核查正常播放接口与不可用音源的处理；本项目未复制其实现。
- [GD Studio作者公开接口说明](https://github.com/UnblockNeteaseMusic/server/issues/1395)：本项目使用公开解析API，可在插件参数关闭此回退。服务可用性与使用条件独立于内核MIT许可；查询关键词会发送给所选音源，不包含QQ令牌、聊天历史或模型凭据。
- [nonebot-plugin-htmlrender](https://github.com/kexue-z/nonebot-plugin-htmlrender)：参考类型化渲染产物和有界资源缓存；这里采用独立Pillow绘制，没有引入浏览器渲染依赖。

没有新增账号登录或付费音源凭据。实际音质和是否为试听由音源决定，不承诺每首歌都能取得完整版。
