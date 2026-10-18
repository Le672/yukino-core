# 构建与发行

当前公开版本：0.6.2。仓库包含核心、控制台、测试、Windows 工具、示例，以及五组共 109 个普通插件的源码。`integrations` 下的私人侧车不在公共仓库与发行包内。

## 通用源码包

```sh
python -m pip install .
python -m unittest discover -s tests -v
python scripts/build_release.py --output ./dist/source
```

输出命名源码 ZIP、独立 SHA-256 和逐文件 `release-manifest.json`。相同内容按固定时间戳和排序打包，输出目录必须是新的。构建会拒绝链接、越界路径、超大文本文件、私钥和已知私人标记；维护者可用私密环境变量 `YUKINO_RELEASE_FORBIDDEN` 追加每行一个的禁止值。不要把这些值写入仓库。

默认资源库为空，控制台使用生成的 Y 字 SVG。输出排除账号、令牌、Cookie、私人角色与对话、旧内核数据库、用户导入图片、漫画、视频、运行日志、依赖缓存和部署脚本。固定公共 DNS 与网络安全测试样本不是部署地址，已单独识别。

## 普通插件包

```sh
python extensions/community_pack/build_pack.py --output ./dist/community-pack
python extensions/maibot_pack/build_pack.py --output ./dist/mai-pack
python extensions/expansion_pack/build_pack.py --output ./dist/expansion-pack
python extensions/market2_pack/build_pack.py --output ./dist/market2-pack
python extensions/interaction_pack/build_pack.py --output ./dist/interaction-pack
```

依次为 11、15、63、8、12 项。各目录的 `pack-manifest.json` 记录插件 ID、ZIP、哈希与推荐参数。发布合集时保留这五组目录、每项 ZIP、校验文件和包说明；合集本身不是可直接上传面板的单插件 ZIP。Windows 工具可以读取这五个目录并捆绑安装。

外部 worker 的测试分别在各组 `tests` 目录，用单独的 `python -m unittest discover -s <目录> -v` 执行，避免不同功能包的同名模块相互遮蔽。

## Windows 便携版

见 [Windows 构建步骤](../windows/README.md)。工具验证 Python 官方运行时与 wheel 的 SHA-256，输出固定依赖清单、CPython SPDX、逐 wheel 许可文件和整个包的 manifest。公开包没有 `data`；首次运行才创建随机管理令牌、空 QQ / 模型与插件状态。

0.6.2 公开便携包为 Windows build 2，包含 16 原生功能与 109 外部插件。私聊 / 群聊、外部模型和通知事件是否可用，仍需在使用者配置自己的服务后实测。

## 发布前检查

- 确认版本与 CHANGELOG、源码、Windows 说明一致；README 的本地链接在白名单源码中都有目标。
- 核对逐文件和整包散列，测试空实例初始化及控制台。保留已有部署的私人数据，不把整个工作目录当发行包。
- 按 [第三方说明](../THIRD_PARTY_NOTICES.md) 与 [许可记录](LICENSE_REVIEW.md) 保留来源、作者和许可文本，不能据此声称所有代码或素材全部原创。
- 将源码、Windows ZIP、插件合集、清单和 `SHA256SUMS.txt` 上传同一个 GitHub Release；tag 对应发布源码提交。
- 从公开 GitHub 页面验证 README、附件大小与下载结果。仓库源码的自动检查见 `.github/workflows/ci.yml`。

源码自动检查、受控网络回环测试与真实 QQ / 付费 API 验收是不同证据。发布说明应明确检查范围，不把未配置账号的新实例描述为已完成真实收发。
