"""Regenerate original independent plugin manifests, wrappers and their catalog."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent


def integer(key, label, default, low, high):
    return dict(key=key, type='integer', label=label, default=default, min=low, max=high)


SPECS = [
    ('x_json', 'JSON格式与路径取值', ['/JSON格式', '/JSON取值'], '严格JSON格式化、数组/属性路径和JSON Pointer取值', '/JSON取值 user.name|{"user":{"name":"雪乃"}}', '雪乃', '/JSON格式 {broken}', [integer('indent', '格式化缩进空格', 2, 1, 4)], '取值语法：`a.b[0]|JSON`，含特殊属性名时使用 `/a/b/0|JSON`。JSON 最大 40 层、10000 个节点；拒绝 NaN/Infinity。'),
    ('x_csv', 'CSV结构与数值统计', ['/CSV统计'], '解析含引号CSV，统计缺失、唯一值及数值列平均值', '/CSV统计 姓名,分数\n小明,90\n小红,80', '平均 85', '/CSV统计 "unterminated', [dict(key='delimiter', type='string', label='分隔符：逗号、分号或制表符', default=','), dict(key='has_header', type='boolean', label='首行为表头', default=True)], '支持 CSV 引号转义和单元格内换行，默认首行表头。每次最多 1000 行、50 列。'),
    ('x_regex', '限时正则测试', ['/正则测试'], '带捕获分组与位置的正则匹配，独立短超时保护', '/正则测试 ([a-z]+)([0-9]+)|abc123 中文', 'abc123', '/正则测试 [|abc', [integer('timeout_ms', '正则子进程超时毫秒', 500, 200, 1500), integer('match_limit', '最多显示匹配项', 10, 1, 30), dict(key='ignore_case', type='boolean', label='忽略英文字母大小写', default=False)], '简写 `模式|文本` 使用第一个竖线分隔；含竖线的模式请用 `{"pattern":"cat|dog","text":"cat dog"}`。模式最多 300 字符、文本 4000；使用固定解释器子进程，无 shell/eval，超时终止子进程。'),
    ('x_cron', 'Cron解释与下次时间', ['/Cron说明', '/Cron下次'], '标准五字段Cron解析和有界未来触发时间计算', '/Cron下次 0 9 * * 1-5|2026-10-16T09:00:00+08:00', '2026-10-19T09:00+08:00', '/Cron说明 60 * * * *', [integer('utc_offset_hours', '显示时区UTC偏移小时', 8, -12, 14), integer('result_count', '显示下次时间数量', 3, 1, 5), integer('lookahead_days', '最大扫描天数', 1096, 1, 1096)], '支持数字、`*`、`,`、`-`、`/`，不支持秒、L/W/# 或月份英文名。日／星期任一字段以 * 开头（含 */步长）时采用 Vixie AND，否则采用 OR。计算严格晚于指定起点；不创建或执行系统任务。'),
    ('x_uuid', 'UUID生成与解析', ['/UUID', '/UUID解析'], '随机UUIDv4、可重复的命名UUIDv5及结构解析', '/UUID 5 dns|example.com', 'UUID v5', '/UUID解析 bad-uuid', [integer('count', '默认UUIDv4生成数量', 1, 1, 10)], '默认 `/UUID` 生成 v4，`/UUID 4 3` 生成三个；`/UUID 5 dns|名称` 支持 dns/url/oid/x500 或 UUID 命名空间。UUID 不是密码或加密令牌。'),
    ('x_ip', 'IP与CIDR离线分析', ['/IP信息'], 'IPv4/IPv6地址、网络掩码、地址数与类别分析', '/IP信息 192.168.1.10/24', '192.168.1.0/24', '/IP信息 999.999.999.999', [], '支持 IPv4/IPv6/CIDR，仅使用 Python ipaddress 离线分析；不联网查询归属、运营商、黑名单或延迟。IPv4 /31、/32 按标准库地址数量显示。'),
    ('x_url', '链接结构与跟踪清理', ['/链接信息', '/清理链接'], '离线URL解析和常见跟踪参数清理，保留其余参数', '/清理链接 https://example.com/watch?v=42&utm_source=test&spm=123', 'https://example.com/watch?v=42', '/链接信息 file:///etc/passwd', [], '只接收 HTTP/HTTPS，不打开网址、不跟随跳转。拒绝含用户名或密码的 URL。清理 utm_*、spm、fbclid/gclid 等参数；重要链接应检查清理结果后再使用。'),
    ('x_color', '颜色转换与可读性对比', ['/颜色', '/对比度'], 'HEX/RGB/HSL转换、亮度和WCAG文字对比度计算', '/对比度 #000000|#ffffff', '21.00:1', '/颜色 rgb(256,0,0)', [], '颜色格式支持 #RGB、#RRGGBB、rgb(r,g,b)。对比度使用 sRGB 相对亮度计算，显示 AA/AAA 普通文字及 AA 大号文字门槛。'),
    ('x_units', '多量纲单位换算', ['/单位换算'], '温度长度质量速度面积体积数据与时间间隔换算', '/单位换算 32 F C', '= 0 C', '/单位换算 1 kg m', [integer('precision', '展示有效位数', 14, 4, 28)], '语法：`数值 原单位 目标单位`。支持 m/km/cm/mm/in/ft/yd/mi，kg/g/mg/t/lb/oz，m/s/km/h/mph/knot，m2/km2/cm2/ha，m3/l/ml/gal，b/kb/mb/gb/kib/mib/gib，s/min/h/day，以及 C/F/K。b 表示字节；gal 是美制液体加仑。温度拒绝低于绝对零度。'),
    ('x_radix', '任意进制整数转换', ['/进制转换'], '2到36进制有符号整数转换与容量限制', '/进制转换 FF 16 2', '11111111', '/进制转换 2 2 10', [], '语法：`数字 原进制 目标进制`，进制 2～36；支持正负号，不支持小数、下划线或 0x 等前缀。最大 2048 个二进制位。'),
    ('x_unicode', 'Unicode字符与规范化', ['/字符信息', '/文本规范化'], '码点名称类别UTF8字节及NFC/NFD/NFKC/NFKD转换', '/文本规范化 NFKC|ＡＢＣ１２３', 'ABC123', '/文本规范化 BAD|abc', [integer('character_limit', '字符信息最多码点数', 40, 1, 80)], '字符信息按 Unicode 码点统计，组合字形可能包含多个码点。规范化语法 `NFKC|文本`；全角、兼容字符等转换会改变原文本，结果仅作为新消息返回。'),
    ('x_markdown', 'Markdown目录与表格', ['/Markdown目录', '/Markdown表格'], '跳过代码围栏提取标题目录和CSV转Markdown表格', '/Markdown目录 # 开始\n## 安装\n## 使用', '[安装](#安装)', '/Markdown表格 "unterminated', [dict(key='delimiter', type='string', label='CSV分隔符：逗号、分号或制表符', default=',')], '目录支持 ATX 的 # 到 ###### 标题，忽略 ``` 和 ~~~ 围栏中的内容；不同网站的锚点规则可能不同。表格接受 CSV，多行单元格转为 <br>，竖线转义，最多 20 列。'),
    ('x_diff', '多行文本差异', ['/文本差异'], 'Unified Diff与字符相似度，支持JSON输入保留竖线', '/文本差异 old line|new line', '-old line', '/文本差异 missingseparator', [integer('context_lines', '差异前后上下文行数', 2, 0, 5)], '简单输入用 `旧文本|新文本`；包含竖线或多行时使用 `{"a":"旧\\n文本","b":"新\\n文本"}`。每段最多 200 行、总长度 16000；只比较文本，不读取文件。'),
    ('x_semver', '语义版本比较', ['/版本比较'], '严格SemVer排序，包括预发行数字标识与构建元数据', '/版本比较 1.2.3-alpha.2 1.2.3-alpha.10', ' < ', '/版本比较 01.2.3 1.2.3', [], '严格要求 MAJOR.MINOR.PATCH，拒绝前导零。预发行数字项按数值、非数字按 ASCII 比较；正式版优先级高于预发行，+build 不参与优先级。'),
    ('x_http', 'HTTP状态与MIME速查', ['/HTTP状态', '/MIME'], '标准HTTP状态解释和扩展名MIME双向查询', '/HTTP状态 404', 'Not Found', '/HTTP状态 700', [], '状态码数据来自运行时 Python HTTPStatus；未登记状态显示所属类别。MIME 仅按标准库扩展名表推断，不读取文件、不验证真实内容。'),
    ('x_epoch', 'Unix时间戳与ISO互转', ['/时间戳转换'], '秒/毫秒时间戳和带时区ISO日期时间双向转换', '/时间戳转换 1700000000000', '2023-11-14T22:13:20+00:00', '/时间戳转换 not-a-date', [integer('utc_offset_hours', '无偏移输入与显示时区UTC小时', 8, -12, 14)], '支持 `s:1700000000`、`ms:1700000000000`、ISO 时间。纯数值绝对值 ≥1e11 按毫秒，其他按秒；含糊时请显式加 s:/ms:。无时区 ISO 使用配置偏移，输出同时显示 UTC。'),
    ('x_decimal', 'Decimal与有理数计算', ['/精确计算'], '受限AST十进制计算与Fraction精确分数模式', '/精确计算 0.1+0.2', '= 0.3', '/精确计算 __import__("os").system("echo unsafe")', [integer('precision', 'Decimal有效数字位数', 28, 10, 80)], '支持数字、括号及 + - * / // % **；`/精确计算 分数 1/3+1/6` 返回 1/2。使用受限 AST 递归求值，无 eval；拒绝函数、变量、属性和代码。幂指数限整数 ±20，表达式最多 600 字符/80 节点。Decimal 采用配置精度，分数模式精确但有容量上限。'),
    ('x_matrix', '小矩阵代数', ['/矩阵'], '精确有理数转置、矩阵乘法与消元法行列式', '/矩阵 行列式 [[1,2],[3,4]]', '行列式：-2', '/矩阵 行列式 [[1,2,3],[4,5,6]]', [], '语法：`转置 JSON`、`乘法 A|B`、`行列式 JSON`。最多 6×6，数组必须规则，元素为有限数字。内部采用 Fraction，分数直接显示；非方阵拒绝行列式、维度不匹配拒绝乘法。'),
    ('x_stats', '描述性数据统计', ['/数据统计'], '均值中位数四分位总体/样本方差标准差众数', '/数据统计 1,2,3,4', '平均：2.5', '/数据统计 1,NaN,3', [], '最多 1000 个数字，分隔符为空格/逗号/分号。采用中心化偏移和动态工作精度保留大偏移下的小幅变化。四分位采用 (n−1)p 位置线性插值；总体方差除以 n，样本方差除以 n−1。单个数据不能计算样本方差。'),
    ('x_probability', '组合排列与二项概率', ['/概率计算'], '精确组合排列及独立同概率试验二项分布', '/概率计算 二项 10 3 0.5', '0.1171875', '/概率计算 二项 10 3 1.5', [], '语法：`组合 n k`、`排列 n k`、`二项 n k p`。要求 0≤k≤n；组合/排列 n≤2000，二项 n≤500，p∈[0,1]。返回 P(X=k)、P(X≤k)、期望和方差；输出有容量上限。'),
    ('x_polynomial', '多项式求值导数与方程', ['/多项式'], 'Horner求值、导数系数和含复根的二次方程求解', '/多项式 求值 1,-3,2|3', 'P(x)=2', '/多项式 二次 1 2', [], '系数按最高次到常数排列，最多 21 项（20 次）。`求值 系数|x`、`导数 系数`、`二次 a b c`。二次退化为一次/恒等式时分别处理；负判别式显示共轭复根；实根使用稳定 q 求根法，避免大系数导致小根消减为零。'),
]

# Network-capable variants retain the original offline calculations.
NETWORK_METADATA={'x_ip': ('IP归属与DNS查询', ['/IP信息', '/IP查询', '/IP', '/DNS', '/DNS查询', '/域名查询'], '真实查询公网归属、运营商、ASN与DNS，保留IPv4/IPv6/CIDR本地分析'), 'x_url': ('链接检查与跟踪清理', ['/链接信息', '/清理链接'], '查询公开网站实际HTTP状态、类型和跳转结果，保留链接结构分析与离线清理'), 'x_http': ('HTTP与网站状态', ['/HTTP状态', '/MIME', '/网站状态'], '真实检测公开HTTPS网站状态与响应头，保留HTTP状态码和MIME本地速查')}
NETWORK_FIELDS=[{'key': 'online_enabled', 'type': 'boolean', 'label': '允许联网查询', 'default': True}, {'key': 'network_timeout_seconds', 'type': 'integer', 'label': '每个数据源网络超时秒数', 'default': 8, 'min': 2, 'max': 15}, {'key': 'cache_seconds', 'type': 'integer', 'label': '归属与DNS成功结果缓存秒数', 'default': 600, 'min': 0, 'max': 3600}]
NETWORK_DETAILS={
    'x_ip':'公网IP查询真实归属、ISP/组织、ASN与时区，失败使用备用源；私网和保留地址仅本地分析。/DNS example.com MX 查询实际记录与TTL。不带IP时查询机器人服务器出口。',
    'x_url':'/链接信息 查询实际HTTPS状态与最终地址。/清理链接 保持离线，只移除已知跟踪参数。公开目标的DNS/IP、每次跳转和TLS均验证，拒绝内网、凭据和自定义端口。',
    'x_http':'/网站状态 https://example.com 或 /HTTP状态 URL 查询实际HEAD状态和响应头；数字状态码、扩展名与MIME速查保持本地。'}
NETWORK_METADATA['x_ip'][1].extend(['/ip','/dns'])
SPECS=[(pid,*NETWORK_METADATA[pid][:2],NETWORK_METADATA[pid][2],smoke,expected,invalid,
        schema+NETWORK_FIELDS+([dict(key='ip_provider',type='string',label='优先IP归属数据源',default='ipwhois',options=['ipwhois','ipapi'])] if pid=='x_ip' else []),NETWORK_DETAILS[pid])
       if pid in NETWORK_METADATA else (pid,name,commands,description,smoke,expected,invalid,schema,details)
       for pid,name,commands,description,smoke,expected,invalid,schema,details in SPECS]


def main():
    catalog = []
    for plugin_id, name, commands, description, smoke, expected, invalid, schema, details in SPECS:
        directory = ROOT / 'plugins' / plugin_id
        directory.mkdir(parents=True, exist_ok=True)
        manifest = dict(id=plugin_id, name=name, version='1.2.0', minimum_core_version='0.8.0', result_protocol=1, description=description, worker='worker.py', commands=commands, config_schema=schema + [integer('max_output_chars', '单次回复最多字符', 6000, 1000, 12000)])
        (directory / 'plugin.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        wrapper = f'''import importlib.util
from pathlib import Path
KIND = {plugin_id!r}
COMMANDS = {tuple(commands)!r}
helper = Path(__file__).with_name('_engine.py')
if not helper.exists():
    helper = Path(__file__).parents[2] / 'dev_engine.py'
spec = importlib.util.spec_from_file_location('yukino_expansion_' + KIND, helper)
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

def handle(text, context):
    if not isinstance(text, str) or not text.strip() or text.strip().split()[0] not in COMMANDS:
        return None
    return engine.handle(KIND, text, context)
'''
        (directory / 'worker.py').write_text(wrapper, encoding='utf-8')
        parameters = '\n'.join(f"- `{field['key']}`：{field['label']}；默认 `{field['default']}`。" for field in manifest['config_schema'])
        (directory / 'README.md').write_text(f'''# {name}

独立插件 ID：`{plugin_id}`，版本 1.2.0，适配 Yukino Core 0.8.0 普通 worker。

{description}。{'仅显式查询访问公网HTTPS。' if plugin_id in NETWORK_METADATA else '完全离线。'}命令不调用模型，不读取聊天历史或服务器秘密。

## 使用

命令：{'、'.join(f'`{command}`' for command in commands)}。

{details}

示例：

```text
{smoke}
```

## 面板配置

{parameters}

所有输入都有长度/数值边界，错误返回普通说明。无持久化、无任意路径读写。{'联网仅限公开HTTPS，面板可关闭。' if plugin_id in NETWORK_METADATA else '无网络。'}输出超限会明确注明截断。

## 打包

由扩展包构建器把本目录的 plugin.json、worker.py、README.md 与 dev_tools/dev_engine.py（改名为 _engine.py）放在同一插件 ZIP 根目录。源码开发时 worker 可回退加载共享 dev_engine.py。不要省略 _engine.py；此共享 helper 不是额外插件，不计入插件数量。
''', encoding='utf-8')
        catalog.append(dict(id=plugin_id, name=name, commands=commands, smoke_command=smoke, expected_contains=expected, invalid_command=invalid))
    (ROOT / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Generated {len(catalog)} independent plugins.')


if __name__ == '__main__':
    main()
