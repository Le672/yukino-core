"""Maintain declarative plugin manifests/readmes; all gameplay is handwritten."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
SPECS=[
 ('i_turtle','海龟汤主持','/海龟汤','/海龟汤 创建','房间已创建','群内 /海龟汤 创建；主持人私聊 /海龟汤 设题 房间码 汤面 | 汤底；回群 开始。玩家 提问 内容；主持人 下一问、回答 是/否/无关、揭底；汤面、记录、状态、结束。由真人主持，不自动推理或调用模型。'),
 ('i_buzzer','抢答计分','/抢答','/抢答 创建','房间已创建','/抢答 创建；主持人 开题 问题；其他人 抢答，群里作答后主持人 判定 对/错。答对得1分，答错本题不能再抢；结题、积分、状态、结束。先创建，再另发开题。'),
 ('i_story','轮流续写','/续写','/续写 创建 从前有座山','房间已创建','/续写 创建 开头；加入；主持人 开始；轮到的人 写 内容 或 跳过。状态、全文、结束。最多80段，单段300字，全文显示末7500字；完整内容保存在插件状态。'),
 ('i_topics','群话题箱','/话题箱','/话题箱 创建','房间已创建','/话题箱 创建；添加 话题；抽取（取出一条不重复话题）；列表、删除 编号、状态、结束。可删除自己添加的条目，主持人和主人可管理全部；最多100条。'),
 ('i_truth','真心话大冒险','/真心冒险','/真心冒险 创建','房间已创建','/真心冒险 创建；加入；主持人 抽人；被抽到的人 真心话 或 大冒险，然后 完成 或 跳过。每轮可无惩罚跳过；8条原创真心话和8条轻松任务，可重复抽取；状态、结束。'),
 ('i_undercover','谁是卧底','/谁是卧底','/谁是卧底 创建','房间已创建','/谁是卧底 创建；3—12人 加入；主持人 开始。每人私聊 /谁是卧底 取词 房间码；群中描述后 投票 对方QQ；主持人 结算。平票重投，卧底出局平民胜，剩2人卧底胜。状态、结束。8组原创简单词对，投票并非匿名。'),
 ('i_guess','你说我猜','/你说我猜','/你说我猜 创建','房间已创建','/你说我猜 创建；主持人 开始，私聊 /你说我猜 取词 房间码；群内 线索 内容；其他人 猜 答案。猜中得1分可开新轮；主持人 揭晓；积分、状态、结束。12个生活词，真人主持文字玩法。'),
 ('i_teams','队长蛇形选队','/选队','/选队 创建','房间已创建','/选队 创建；4—12人 加入；发起者 选队长 QQ1 QQ2。队长轮流 选 待选QQ，顺序A-B-B-A循环。全部选完结束；状态、结束。报名后无法中途退场，请结束重建。'),
 ('i_dots','点格棋','/点格棋','/点格棋 创建','房间已创建','/点格棋 创建；第二人 加入；发起者 开始。轮到的人 落子 H 行 列（横边，行1—3列1—2）或 V 行 列（竖边，行1—2列1—3）。闭合格子得分并再走，4格全满分高者胜；状态、结束。'),
 ('i_nim','取石子','/取石子','/取石子 创建 3 4 5','房间已创建','/取石子 创建 3 4 5（1—7堆，各1—30颗）；第二人 加入；发起者 开始；轮到的人 取 堆号 数量。每次从一堆取1—3颗（可配置），取最后一颗获胜；状态、结束。'),
 ('i_set','SET集合牌','/集合牌','/集合牌 创建','房间已创建','/集合牌 创建 即开始；其他人 加入。牌有四位数字，各1—3。出 位置1 位置2 位置3：每位属性须全同或全不同，成功得1分并补牌；无组合时 加牌。状态、结束。81张不重复牌，最多21张桌面牌；无牌可补且无SET结束。'),
 ('i_auction','纯虚拟拍卖','/虚拟拍卖','/虚拟拍卖 创建 云朵收藏证书','房间已创建','/虚拟拍卖 创建 虚拟标的名；其他人 加入；发起者 开始；玩家 出价 整数，须高于最高价且不超过预算；主持人 落槌。状态、结束。每房每人默认100虚拟分，无付款、交易或任何奖品，不与签到金币相通。'),
]
def generate():
 catalog=[]
 for pid,name,cmd,smoke,expected,usage in SPECS:
  fields=[dict(key='max_players',type='integer',label='房间人数上限（棋类固定2，卧底至少3，选队至少4）',min=2,max=12,default=12)]
  if pid=='i_nim':fields.append(dict(key='nim_max_take',type='integer',label='创建时每次最多取石数',min=1,max=30,default=3))
  if pid=='i_auction':fields.append(dict(key='auction_budget',type='integer',label='创建时每人虚拟预算',min=10,max=100000,default=100))
  item=dict(id=pid,name=name,description=usage[:490],commands=[cmd],worker='worker.py',config_schema=fields)
  folder=ROOT/'plugins'/pid;folder.mkdir(parents=True,exist_ok=True)
  (folder/'plugin.json').write_text(json.dumps(item,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
  (folder/'worker.py').write_text('from pathlib import Path\nimport sys\nsys.path.insert(0,str(Path(__file__).resolve().parent))\nfrom engine import handle as run\ndef handle(text,context):\n    return run('+repr(pid)+',text,context)\n',encoding='utf-8')
  (folder/'README.md').write_text('# '+name+'\n\n'+usage+'\n\n这是独立实现的Yukino文字插件，不兼容麦麦SDK。无需联网或模型。群内使用完整指令前缀，例如`'+cmd+' 状态`。秘密词只有已加入玩家在私聊取词时可见。房间存储在独立data_dir，30天未操作后过期；新建房间不覆盖进行中的旧房间。结束需发起者或机器人主人，其他参数在控制台配置，部分仅新房间生效。\n',encoding='utf-8')
  catalog.append({**item,'usage':usage,'smoke_command':smoke,'expected_contains':expected,'settings':{'timeout':6,'input_max_chars':4000,'output_max_chars':8000}})
 (ROOT/'catalog.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
if __name__=='__main__':generate()
