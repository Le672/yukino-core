# Third-party notices

Yukino Core 项目自有代码沿用上层项目的 MIT License，版权声明见 [LICENSE](LICENSE)。此声明不把第三方依赖、人物图像、用户导入素材或外部项目的代码重新许可为 MIT，也不是“全部原创”的声明。

下列清单按当前源码和依赖声明核对，核对日期为 2026-10-16。默认源码 ZIP 不附带第三方 Python wheel、系统包或二进制；安装或构建容器时由各自软件源提供。发行含依赖的镜像、离线包时，需要再保留其完整许可证及传递依赖声明。

| 项目 | 用途与范围 | 上游许可 | 官方来源 |
| --- | --- | --- | --- |
| aiohttp | 核心 HTTP 与 WebSocket | Apache-2.0；具体发行内容以其许可证为准 | [LICENSE](https://github.com/aio-libs/aiohttp/blob/master/LICENSE.txt) |
| Pillow / PIL | 核心图片验证与可选图片处理 | MIT-CMU / PIL 许可文本 | [LICENSE](https://github.com/python-pillow/Pillow/blob/main/LICENSE) |
| CPython | 外部 Python 运行时；Docker 基础镜像包含运行时 | PSF 与随附第三方声明 | [Python license](https://docs.python.org/3/license.html) |
| pyzipper | 开发仓库的可选姬漫导出组件 | MIT，Copyright (c) 2017 Daniel Hillier | [LICENSE](https://github.com/danifus/pyzipper/blob/master/LICENSE) |
| pypdf | 开发仓库的可选 PDF 导出组件 | BSD-3-Clause | [LICENSE](https://github.com/py-pdf/pypdf/blob/main/LICENSE) |
| yt-dlp | 开发仓库的可选视频组件 | Unlicense 为主；项目列出的捆绑代码另有许可 | [LICENSE](https://github.com/yt-dlp/yt-dlp/blob/master/LICENSE) |
| FFmpeg | 可选视频下载后封装、分段 | 默认 LGPL-2.1-or-later；启用特定部分会受 GPL 等条款约束 | [官方许可说明](https://ffmpeg.org/legal.html) |
| zyipeng/video-downloader | 视频移动分享页方法的参考项目，未打包整个上游仓库 | MIT，Copyright (c) 2026 zhaoyipeng | [LICENSE](https://github.com/zyipeng/video-downloader/blob/main/LICENSE) |
| Shaun520/ai-video-downloader | 视频移动分享页方法的参考项目，未打包整个上游仓库 | MIT，Copyright (c) 2026 AI 视频下载器（ai-video-downloader） | [LICENSE](https://github.com/Shaun520/ai-video-downloader/blob/main/LICENSE) |

已保存核查时上游公开的 aiohttp、Pillow、pyzipper、pypdf 和两项视频参考项目的许可文本，位于 `docs/licenses`；下载来源与 SHA-256 在 `docs/licenses/sources.json`。这些文本是核查时的上游快照，不代表所有可安装版本和传递依赖都已审查。

## 独立组件与素材

- NapCat、QQ 客户端、ChatGPT 网页桥、模型服务与账户会话不包含在核心源码包中，分别适用各自条款和许可。核心通过标准接口连接它们，不代表获得品牌或服务转售授权。
- MaiBot 的旧数据库读取兼容和名称迁移不自动构成其代码的授权。未完成对全部历史来源的逐段比对，不能据此保证不存在任何衍生代码；如果将来复制或适配上游实现，必须按具体上游许可处理，不能覆盖原声明。[MaiBot LICENSE](https://github.com/Mai-with-u/MaiBot/blob/main/LICENSE)
- 当前可选姬漫服务通过站点 HTTP API 工作；其 `requirements.txt` 未直接安装 `jmcomic` 或 `pikepdf`。若将来捆绑后端，则需要增加它们及依赖的许可审查。[JMComic 源码](https://github.com/hect0x7/JMComic-Crawler-Python)、[pikepdf LICENSE](https://github.com/pikepdf/pikepdf/blob/main/LICENSE.txt)。
- 原私人部署的人物主站 Logo、表情资源大包和验收下载的漫画/视频不随源码发行。生成的文字 Y 标识仅作为默认标记；导入其他标识、图片或内容应先取得相应使用和分发权。

具体未确认项与发布边界见 [许可检查报告](docs/LICENSE_REVIEW.md)。
