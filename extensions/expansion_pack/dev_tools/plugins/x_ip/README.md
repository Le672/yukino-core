# IP归属与DNS查询

版本1.2.0，需要Yukino Core0.8.0+。

真实查询公网归属、运营商、ASN与DNS，保留IPv4/IPv6/CIDR本地分析。

命令：`/IP信息`、`/IP查询`、`/IP`、`/DNS`、`/DNS查询`、`/域名查询`。

示例：`/IP信息 8.8.8.8`、`/IP信息 2001:db8::1/64`、`/DNS example.com MX`；不带IP时查询机器人服务器出口。私网/保留地址仅本地分析，不发给外部归属服务。

联网可在面板关闭。所有访问使用有界HTTPS，验证DNS、目标IP、TLS和每次跳转；拒绝内网、凭据URL与自定义端口。归属信息来自ipwho.is或备用ipapi.co，DNS来自Google Public DNS。结果不可用时明确提示，不捏造位置、风险或可用状态。

网络超时、缓存、优先归属源均可在面板设置。成功归属/DNS默认最多缓存600秒；每项网络调用最多8秒，插件总时限30秒。插件包必须包括_engine.py、helper_online.py和helper_net.py。
