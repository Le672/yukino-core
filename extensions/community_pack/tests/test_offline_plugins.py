import concurrent.futures
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import random
import re
import sqlite3
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]/"plugins"
TZ=timezone(timedelta(hours=8))

def module(name):
    spec=importlib.util.spec_from_file_location("pack_"+name,ROOT/name/"worker.py")
    value=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

class PluginCase(unittest.TestCase):
    plugin=""
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker=module(self.plugin)
        # Synthetic fixture, constructed so release scanners do not mistake it
        # for an embedded deployment owner account.
        self.context={"data_dir":self.temp.name,"session_id":"group:1","user_id":"123456","settings":{},"owner_ids":["9" * 6]}
        self.day=datetime(2026,10,17,12,0,tzinfo=TZ)
        if hasattr(self.worker,"_now"): self.worker._now=lambda:self.day
    def call(self,text,**changes): return self.worker.handle(text,{**self.context,**changes})

class EconomyTests(PluginCase):
    plugin="daily_economy"
    def balance(self,user="123456",scope="group:1"):
        db=sqlite3.connect(Path(self.temp.name)/"economy.sqlite3")
        try:
            row=db.execute("SELECT balance FROM accounts WHERE scope=? AND user=?",(scope,user)).fetchone()
            return row[0] if row else 0
        finally: db.close()
    def test_sign_idempotent_same_day(self):
        self.assertIn("+50",self.call("/签到"))
        self.assertIn("已签到",self.call("/签到"))
        self.assertEqual(50,self.balance())
        self.assertEqual(1,self.call("/流水").count("签到连续1天"))
    def test_next_day_streak_and_gap_reset(self):
        self.call("/签到")
        self.day+=timedelta(days=1)
        self.assertIn("+60",self.call("/签到"))
        self.day+=timedelta(days=2)
        self.assertIn("连续 1 天，+50",self.call("/签到"))
        self.assertEqual(160,self.balance())
    def test_streak_bonus_caps_after_seventh(self):
        rewards=[]
        for _ in range(9):
            rewards.append(int(re.search(r"\+(\d+)",self.call("/签到"))[1]))
            self.day+=timedelta(days=1)
        self.assertEqual([50,60,70,80,90,100,110,110,110],rewards)
    def test_shanghai_midnight_not_utc_midnight(self):
        self.day=datetime(2026,10,17,15,59,tzinfo=timezone.utc)
        self.call("/签到")
        self.day+=timedelta(minutes=2)
        self.assertIn("连续 2 天",self.call("/签到"))
    def test_session_and_user_isolation(self):
        self.call("/签到")
        self.assertIn("余额：0",self.call("/余额",session_id="group:2"))
        self.assertIn("余额：0",self.call("/余额",user_id="654321"))
    def test_restart_persists_sign(self):
        self.call("/签到")
        self.worker=module(self.plugin); self.worker._now=lambda:self.day
        self.assertIn("已签到",self.call("/签到"))
    def test_transfer_double_ledger_and_conservation(self):
        self.call("/签到")
        self.assertIn("转账 20",self.call("/转账 654321 20"))
        self.assertEqual((30,20),(self.balance(),self.balance("654321")))
        self.assertIn("-20 转给654321",self.call("/流水"))
        self.assertIn("+20 来自123456",self.call("/流水",user_id="654321"))
    def test_transfer_invalid_numbers_do_not_mutate(self):
        self.call("/签到")
        for value in ("0","-5","1.5","1e2","+5","01","９","99999999999999"):
            self.call("/转账 654321 "+value)
        self.assertEqual(50,self.balance()); self.assertEqual(0,self.balance("654321"))
    def test_transfer_self_invalid_qq_and_limit(self):
        self.call("/签到")
        self.assertIn("自己",self.call("/转账 123456 1"))
        self.assertIn("用法",self.call("/转账 hello 1"))
        self.assertIn("单笔",self.call("/转账 654321 51",settings={"max_transfer":50}))
        self.assertEqual(50,self.balance())
    def test_insufficient_funds_atomic(self):
        self.call("/签到")
        self.assertIn("不足",self.call("/转账 654321 100"))
        self.assertEqual((50,0),(self.balance(),self.balance("654321")))
        self.assertNotIn("转给",self.call("/流水"))
    def test_recipient_balance_cap_atomic(self):
        settings={"daily_reward":1000,"max_balance":1000}
        self.call("/签到",settings=settings); self.call("/签到",user_id="654321",settings=settings)
        self.assertIn("上限",self.call("/转账 654321 1",settings=settings))
        self.assertEqual((1000,1000),(self.balance(),self.balance("654321")))
    def test_sign_clamps_reward_at_balance_cap(self):
        self.assertIn("+1000",self.call("/签到",settings={"daily_reward":100000,"max_balance":1000}))
        self.day+=timedelta(days=1)
        self.assertIn("+0",self.call("/签到",settings={"daily_reward":100000,"max_balance":1000}))
        self.assertEqual(1000,self.balance())
    def test_shop_purchase_inventory_and_spend(self):
        self.call("/签到")
        self.assertIn("flower｜小花｜30",self.call("/商店"))
        self.assertIn("购买成功",self.call("/购买 flower"))
        self.assertEqual(20,self.balance())
        self.assertIn("flower × 1",self.call("/背包"))
        self.assertIn("不足",self.call("/购买 flower"))
        self.assertEqual(20,self.balance())
    def test_bad_purchase_quantities(self):
        self.call("/签到")
        for suffix in ("0","-1","100","1.0","01","1 2"):
            self.assertIn("用法",self.call("/购买 flower "+suffix))
        self.assertEqual(50,self.balance())
        self.assertIn("空空",self.call("/背包"))
    def test_bad_shop_configuration(self):
        for raw in ('{}','[{"id":"x","name":"x","price":-1}]','[{"id":"../x","name":"x","price":1}]','[{"id":"x","name":"x","price":true}]'):
            self.assertIn("配置无效",self.call("/商店",settings={"shop_json":raw}))
    def test_concurrent_sign_is_exactly_once(self):
        # Initialize schema before racing independent SQLite connections.
        self.call("/余额")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.call("/签到"),range(24)))
        self.assertEqual(1,sum("签到成功" in x for x in results))
        self.assertEqual(50,self.balance())
    def test_concurrent_transfer_never_overdraws(self):
        self.call("/签到",settings={"daily_reward":100})
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(lambda _:self.call("/转账 654321 10"),range(24)))
        self.assertEqual(10,sum("已向" in x for x in results))
        self.assertEqual((0,100),(self.balance(),self.balance("654321")))
    def test_no_source_fallback_without_data_dir(self):
        self.assertIn("目录不可用",self.call("/签到",data_dir=""))

class AffinityTests(PluginCase):
    plugin="affinity"
    def test_commands_only_and_query_does_not_accumulate(self):
        self.assertIn("不会自动计分",self.call("你好"))
        self.assertIn("0/1000",self.call("/好感"))
        self.assertIn("0/1000",self.call("/好感"))
    def test_interaction_cooldown_and_restart(self):
        self.assertIn("+5",self.call("/互动 夸夸"))
        self.assertIn("冷却",self.call("/互动"))
        self.worker=module(self.plugin); self.worker._now=lambda:self.day
        self.assertIn("冷却",self.call("/互动"))
        self.day+=timedelta(seconds=60)
        self.assertIn("当前 10/",self.call("/互动"))
    def test_daily_limit_partial_reward_and_reset(self):
        settings={"daily_limit":9,"cooldown_seconds":0,"points_per_interaction":5}
        self.assertIn("+5",self.call("/互动",settings=settings))
        self.assertIn("+4",self.call("/互动",settings=settings))
        self.assertIn("上限",self.call("/互动",settings=settings))
        self.day+=timedelta(days=1)
        self.assertIn("+5",self.call("/互动",settings=settings))
        self.assertIn("14/",self.call("/好感"))
    def test_points_maximum(self):
        settings={"max_points":10,"points_per_interaction":7,"cooldown_seconds":0}
        self.assertIn("+7",self.call("/互动",settings=settings))
        self.assertIn("+3",self.call("/互动",settings=settings))
        self.assertIn("已满",self.call("/互动",settings=settings))
    def test_owner_privilege_requires_configured_identity(self):
        settings={"owner_max":True}
        self.assertIn("0/1000",self.call("/好感",settings=settings))
        self.assertIn("1000/1000",self.call("/好感",settings=settings,user_id="999999"))
        self.assertIn("0/1000",self.call("/好感",user_id="999999"))
    def test_owner_record_and_scope_isolation(self):
        self.call("/互动",settings={"owner_max":True},user_id="999999")
        self.assertIn("999999：1000",self.call("/好感榜"))
        self.assertIn("暂无",self.call("/好感榜",session_id="private:999999"))
    def test_unsupported_action_rejected(self):
        self.assertIn("可用互动",self.call("/互动 自称主人"))
        self.assertIn("0/1000",self.call("/好感"))
    def test_midnight_still_respects_cooldown(self):
        self.day=self.day.replace(hour=23,minute=59,second=59)
        self.call("/互动")
        self.day+=timedelta(seconds=2)
        self.assertIn("冷却",self.call("/互动"))

class EatTests(PluginCase):
    plugin="eat_what"
    def test_seeded_choice_uses_actual_candidates(self):
        settings={"candidates_json":'["面","饭","饺子"]'}
        self.worker._rng=random.Random(42)
        expected=random.Random(42).choice(["面","饭","饺子"])
        self.assertIn("今天可以吃："+expected,self.call("/吃什么",settings=settings))
    def test_exclusions_and_complete_matching(self):
        settings={"candidates_json":'["饺子","水饺子","米饭"]',"excluded_json":'["米饭"]'}
        self.assertIn("今天可以吃：水饺子",self.call("/吃什么 排除:饺子",settings=settings))
        self.assertIn("饺子、水饺子",self.call("/吃什么列表",settings=settings))
    def test_no_candidate_and_invalid_json(self):
        self.assertIn("没有可选",self.call("/吃什么",settings={"candidates_json":'[]'}))
        for raw in ('{}','[null]','[1]','["a\\nb"]'):
            self.assertIn("JSON",self.call("/吃什么",settings={"candidates_json":raw}))
    def test_invalid_parameter_and_no_state_files(self):
        self.assertIn("可选参数",self.call("/吃什么 火锅"))
        self.call("/吃什么")
        self.assertEqual([],list(Path(self.temp.name).iterdir()))

class DiceTests(PluginCase):
    plugin="trpg_dice"
    def test_seeded_dice_semantics(self):
        expected=random.Random(16)
        rolls=[expected.randint(1,6) for _ in range(3)]
        self.worker._rng=random.Random(16)
        result=self.call("/r 3d6-2")
        self.assertTrue(result.endswith("= "+str(sum(rolls)-2)))
        self.assertIn(" + ".join(map(str,rolls)),result)
    def test_expression_rejects_code_and_complexity(self):
        for expr in ("__import__('os')","2d6+2d6","0d6","-1d6","1000d6","2d0","d9999999","2d6+9999999","2**100","d6;print(1)"):
            result=self.call("/r "+expr)
            self.assertNotIn(" = ",result)
    def test_custom_bounds_and_implicit_one_die(self):
        self.assertIn("上限",self.call("/r 3d6",settings={"max_dice":2}))
        self.assertIn("上限",self.call("/r d20",settings={"max_faces":6}))
        self.assertIn(" = ",self.call("/r d6"))
    def test_coc_check_thresholds_are_explicit(self):
        cases=[(1,0,"大成功"),(96,49,"大失败"),(96,50,"失败"),(100,100,"大失败"),(10,50,"极难成功"),(11,50,"困难成功"),(25,50,"困难成功"),(26,50,"成功"),(51,50,"失败")]
        for roll,skill,label in cases:
            self.assertEqual(label,self.worker._check(roll,skill))
    def test_skill_validation_and_seed(self):
        self.worker._rng=random.Random(2)
        value=random.Random(2).randint(1,100)
        self.assertIn(f"d100={value} / 60",self.call("/ra 60 侦查"))
        for skill in ("-1","101","1.5","1e2"):
            self.assertIn("用法",self.call("/ra "+skill))
    def test_coc_attribute_rules_exact_rolls(self):
        class Six:
            def randint(self,low,high): return high
        self.worker._rng=Six()
        result=self.call("/coc")
        for attr in ("STR","CON","DEX","APP","POW","SIZ","INT","EDU","LUCK"):
            self.assertIn(attr+":90",result)
        class One:
            def randint(self,low,high): return low
        self.worker._rng=One()
        result=self.call("/coc")
        self.assertIn("STR:15",result); self.assertIn("SIZ:40",result); self.assertIn("LUCK:15",result)
    def test_coc_count_bounds_and_disclaimer(self):
        self.assertEqual(5,self.call("/coc 5").count("#"))
        self.assertIn("非完整人物卡",self.call("/coc"))
        for value in ("0","6","999999","1.0"):
            self.assertIn("用法",self.call("/coc "+value))
    def test_initiative_stable_ties_and_validation(self):
        class Ten:
            def randint(self,low,high): return 10
        self.worker._rng=Ten()
        result=self.call("/先攻 A:2 B:2 C:-1")
        self.assertIn("1. A: 10+2=12",result)
        self.assertIn("2. B: 10+2=12",result)
        self.assertIn("3. C: 10-1=9",result)
        for text in ("/先攻 A:101","/先攻 A:1 A:2","/先攻 A:foo"):
            self.assertIn("用法",self.call(text))

class AliasTests(PluginCase):
    plugin="member_aliases"
    def test_self_set_query_clear(self):
        self.assertIn("已设置",self.call("/外号 设置 可爱的人"))
        self.assertIn("可爱的人",self.call("/外号 查"))
        self.assertIn("已清除",self.call("/外号 清除"))
        self.assertIn("没有设置",self.call("/外号 查"))
    def test_restart_scope_and_user_isolation(self):
        self.call("/外号 设置 雪乃")
        self.worker=module(self.plugin)
        self.assertIn("雪乃",self.call("/外号 查"))
        self.assertIn("没有设置",self.call("/外号 查",session_id="group:2"))
        self.assertIn("没有设置",self.call("/外号 查",user_id="654321"))
    def test_unprivileged_cannot_manage_or_clear_target(self):
        self.call("/外号 设置 原名",user_id="654321")
        self.assertIn("只有",self.call("/外号 管理 654321 设置 篡改"))
        self.assertIn("只能清除自己",self.call("/外号 清除 654321"))
        self.assertIn("原名",self.call("/外号 查 654321"))
    def test_owner_can_manage_real_configured_id(self):
        self.assertIn("已设置",self.call("/外号 管理 654321 设置 新名",user_id="999999"))
        self.assertIn("新名",self.call("/外号 查 654321"))
        self.call("/外号 管理 654321 清除",user_id="999999")
        self.assertIn("没有设置",self.call("/外号 查 654321"))
    def test_alias_never_grants_permission(self):
        self.call("/外号 设置 主人")
        self.assertIn("只有",self.call("/外号 管理 654321 设置 改名"))
    def test_controls_injection_long_alias_rejected(self):
        for alias in ("[CQ:at,qq=all]","<owner>","foo\nbar","x"*25):
            self.assertIn("外号须",self.call("/外号 设置 "+alias))
        self.assertIn("没有设置",self.call("/外号 查"))
    def test_apparent_target_remains_own_alias(self):
        self.call("/外号 设置 654321 不会越权")
        self.assertIn("654321 不会越权",self.call("/外号 查"))
        self.assertIn("没有设置",self.call("/外号 查 654321"))
    def test_missing_data_directory_is_not_source_fallback(self):
        self.assertIn("目录不可用",self.call("/外号 设置 名字",data_dir=""))

class ManifestTests(unittest.TestCase):
    def test_every_manifest_has_unique_commands_and_valid_schema(self):
        commands=[]
        for name in ("daily_economy","affinity","eat_what","trpg_dice","member_aliases"):
            record=json.loads((ROOT/name/"plugin.json").read_text(encoding="utf-8"))
            self.assertEqual(name,record["id"])
            self.assertLessEqual(len(record["commands"]),20)
            self.assertTrue((ROOT/name/record["worker"]).is_file())
            for field in record["config_schema"]:
                self.assertIn(field["type"],("string","integer","number","boolean"))
            commands+=record["commands"]
        self.assertEqual(len(commands),len(set(commands)))

if __name__=="__main__": unittest.main()
