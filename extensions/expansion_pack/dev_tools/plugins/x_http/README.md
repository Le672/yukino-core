# HTTP与网站状态

版本1.2.0，需要Yukino Core0.8.0+。

真实检测公开HTTPS网站状态与响应头，保留HTTP状态码和MIME本地速查。

命令：`/HTTP状态`、`/MIME`、`/网站状态`。

示例：`/网站状态 https://example.com/`、`/HTTP状态 https://example.com/`、`/链接信息 https://example.com/`。清理链接和数字状态码查询保持本地处理。

联网可在面板关闭。所有访问使用有界HTTPS，验证DNS、目标IP、TLS和每次跳转；拒绝内网、凭据URL与自定义端口。归属信息来自ipwho.is或备用ipapi.co，DNS来自Google Public DNS。结果不可用时明确提示，不捏造位置、风险或可用状态。

网络超时、缓存、优先归属源均可在面板设置。成功归属/DNS默认最多缓存600秒；每项网络调用最多8秒，插件总时限30秒。插件包必须包括_engine.py、helper_online.py和helper_net.py。
