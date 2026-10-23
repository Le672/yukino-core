"""Exercise every declared AI game through real packaged JSON-line workers."""
import asyncio,contextlib,copy,hashlib,importlib.util,io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from types import SimpleNamespace
from yukino_core.config import DEFAULTS
from yukino_core.game_catalog import GAMES,MULTIPLAYER
from yukino_core.game_state import Frame
from yukino_core.plugin_packages import inspect_package
from yukino_core.plugins import PluginManager

ROOT=Path(__file__).resolve().parents[1]
_PACKAGES=None


def packages():
    global _PACKAGES
    if _PACKAGES is not None:return _PACKAGES
    result={}
    with tempfile.TemporaryDirectory() as folder:
        for pack in ('maibot_pack','expansion_pack','market2_pack','interaction_pack','social_pack'):
            spec=importlib.util.spec_from_file_location('ai_test_build_'+pack,ROOT/'extensions'/pack/'build_pack.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);output=Path(folder)/pack
            with contextlib.redirect_stdout(io.StringIO()):module.build(output)
            manifest=json.loads((output/'pack-manifest.json').read_text(encoding='utf-8'))
            for record in manifest['plugins']:
                if record['id'] in GAMES:result[record['id']]=(output/record['archive']).read_bytes()
    assert set(result)==set(GAMES) and len(result)==41
    _PACKAGES=result
    return result


class WorkerFixture(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.config=SimpleNamespace(data=copy.deepcopy(DEFAULTS),plugin_flags={},plugin_settings={},self_id='900',owner_id='11',owner_ids=['11'],save=lambda:None,receive_enabled=True)
        self.manager=PluginManager(self.config,self.root/'plugins');self.item=None;self.pid=None
    async def asyncTearDown(self):
        await self.manager.close();self.tmp.cleanup()
    def install(self,pid):
        record,files=inspect_package(packages()[pid]);target=self.manager.root/'packages'/pid;target.mkdir(parents=True)
        for name,data in files.items():path=target/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        self.item={**record,'worker':(target/record['worker']).relative_to(self.manager.root).as_posix()};self.pid=pid
        self.manager.items.append(self.item);self.config.plugin_settings[pid]={'timeout':15}
    def ctx(self,user='11',sid='g:77'):
        return dict(session_id=sid,user_id=user,self_id='900',owner_ids=['11'],settings=self.manager.settings(self.pid),data_dir=str(self.manager._state_directory(self.pid)))
    async def call(self,arg='',user='11',sid='g:77',**control):
        context=dict(session_id=sid,user_id=user)
        if control:context['_game_ai']=control
        return await self.manager.execute(self.item,'/'+GAMES[self.pid]+(' '+arg if arg else ''),context)
    def frame(self,user='11',sid='g:77'):return Frame(self.pid,self.ctx(user,sid))
    async def apply(self,packet,response):
        pending=packet['game_ai']['task'];self.assertIsNotNone(pending,packet)
        return await self.call(op='apply',scope_owner='11',nonce=pending['nonce'],response=response)
    async def prepare(self,pid):
        self.install(pid)
        if pid in ('x_connect4','x_tictactoe','x_gomoku','x_reversi'):
            await self.call('开始 人机');return await self.call(dict(x_connect4='4',x_tictactoe='1 1',x_gomoku='5 5',x_reversi='2 3')[pid])
        if pid not in MULTIPLAYER:return await self.call('开始 人机')
        tail=' 从前有一座城' if pid=='i_story' else ' 一张虚拟贴纸' if pid=='i_auction' else ''
        packet=await self.call('创建 人机'+tail)
        if pid=='i_turtle':
            code=self.frame().read()['code'];await self.call('设题 '+code+' 一个男人在车站哭了。 | HIDDEN_TURTLE_ANSWER',sid='p:11')
        if pid=='i_undercover':await self.call('加入',user='22')
        if pid=='i_teams':
            await self.call('加入',user='22');await self.call('加入',user='33');return await self.call('选队长 AI 11')
        if pid=='i_set':return packet
        if pid=='i_topics':await self.call('添加 最喜欢哪一种天气');return await self.call('抽取')
        if pid=='i_truth':
            state=self.frame().read();state.update(selected='900',task='如果多一天假期你会做什么？');self.frame().write(state);return await self.call('AI状态')
        if pid=='i_buzzer':return await self.call('开题 1+1等于多少？')
        packet=await self.call('开始')
        if pid=='i_story':return await self.call('写 天黑以后，城门外来了一个旅人。')
        if pid=='i_undercover':return await self.call('描述 这是一种常见的东西。')
        if pid=='i_guess':return await self.call('线索 这是一种常见的日用品。')
        if pid=='i_dots':return await self.call('落子 H 1 1')
        if pid=='i_nim':return await self.call('取 1 1')
        if pid=='m_uno':await self.call('摸牌');return await self.call('跳过')
        return packet


class AllGameActions(WorkerFixture):
    pass


def action_test(pid):
    async def verify(self):
        packet=await self.prepare(pid)
        self.assertIsInstance(packet,dict,packet);self.assertEqual(packet.get('version'),2,packet)
        self.assertNotIn('error',packet['game_ai'],packet)
        task=packet['game_ai']['task'];self.assertIsNotNone(task,packet)
        if task['mode']=='choice':response={'action':task['choices'][0]['id']}
        else:
            sample={'wordle':'crane','guess_number':'0123','x_hangman':'e','x_anagram':'apple','x_arithmetic':'0','m_mahjong':'1m',
                    'i_undercover':'使用时往往要考虑场合。','i_story':'旅人敲了三下门，守门人却已经睡着了。','i_turtle':'这个人原本健康吗？',
                    'i_buzzer':'2','i_guess':'桌子','i_truth':'假设放一天假，我会找个安静地方散步。','i_topics':'下雨的时候待在屋里挺舒服的。'}
            if pid=='x_twentyfour':sample[pid]='+'.join(map(str,self.frame().read()['numbers']))
            response={'text':sample[pid]}
        applied=await self.apply(packet,response)
        self.assertIsInstance(applied,dict,applied);self.assertFalse(applied['game_ai'].get('error'),applied)
        self.assertTrue(applied['text'],applied)
    verify.__name__='test_'+pid+'_model_action_uses_real_rules'
    return verify


for _pid in GAMES:setattr(AllGameActions,'test_'+_pid+'_model_action_uses_real_rules',action_test(_pid))


class Boundaries(WorkerFixture):
    async def test_undercover_own_word_only_order_votes_and_automatic_settlement(self):
        packet=await self.prepare('i_undercover');s=self.frame().read();own=s['hands']['900'];other=s['hands'][next(u for u in s['players'] if s['hands'][u]!=own)]
        obs=packet['game_ai']['task']['observation'];encoded=json.dumps(obs,ensure_ascii=False)
        self.assertEqual(obs['your_word'],own);self.assertNotIn(other,encoded);self.assertNotIn('spy',encoded);self.assertNotIn('hands',encoded)
        rejected=await self.apply(packet,{'text':'我的词是'+own});self.assertTrue(rejected['game_ai']['error']);self.assertNotIn(own,rejected['text'])
        applied=await self.apply(packet,{'text':'使用时往往要考虑场合。'});self.assertFalse(applied['game_ai'].get('error'))
        duplicate=await self.apply(packet,{'text':'另一个描述。'});self.assertTrue(duplicate['game_ai']['stale'])
        packet=await self.call('描述 我在日常生活中见过。',user='22');self.assertEqual(packet['game_ai']['task']['mode'],'choice')
        await self.apply(packet,{'action':'1'});await self.call('投票 22');last=await self.call('投票 11',user='22')
        self.assertEqual(self.frame().read()['phase'],'done');self.assertIn('获胜',last['text'])

    async def test_hidden_board_answers_never_reach_observation(self):
        for pid,keys in [('x_twentyfour',['solution']),('x_minesweeper',['mines']),('x_battleship',['ships']),('s_sudoku',['solution']),('wordle',['answer'])]:
            with self.subTest(pid=pid):
                packet=await self.prepare(pid);obs=json.dumps(packet['game_ai']['task']['observation'],ensure_ascii=False)
                for key in keys:self.assertNotIn('"'+key+'"',obs)
                if pid=='wordle':self.assertNotIn(self.frame().read()['answer'],obs)
                self.item=None;self.pid=None

    async def test_uno_only_own_hand_and_stale_action_rejected(self):
        packet=await self.prepare('m_uno');obs=packet['game_ai']['task']['observation']
        self.assertIn('your_hand',obs);self.assertNotIn('deck',obs);self.assertNotIn('hands',obs);self.assertNotIn('900',json.dumps(obs))
        s=self.frame().read();s['moves']+=1;self.frame().write(s)
        result=await self.apply(packet,{'action':'1'});self.assertTrue(result['game_ai']['stale'])

    async def test_rps_commits_before_human_throw_and_reveals_verifiable_hash(self):
        packet=await self.prepare('rps');commit=await self.apply(packet,{'action':'1'});self.assertIn('SHA-256',commit['text'])
        outcome=await self.call('石头');self.assertIn('平局',outcome['text'])
        original=outcome['text'].split('承诺原文：')[1].split('\n')[0]
        self.assertIn(hashlib.sha256(original.encode()).hexdigest(),commit['text'])
        self.assertIsNotNone(outcome['game_ai']['task'])

    async def test_solo_does_one_step_and_other_user_cannot_apply_its_token(self):
        packet=await self.prepare('x_lights');result=await self.apply(packet,{'action':'1'});self.assertIsNone(result['game_ai']['task'])
        bad=await self.call(user='22',op='apply',scope_owner='22',nonce=packet['game_ai']['task']['nonce'],response={'action':'1'})
        self.assertTrue(bad['game_ai'].get('stale') or bad['game_ai'].get('error'))
        status=await self.call('状态');self.assertIsNone(status['game_ai']['task'])

    async def test_invite_remove_and_pure_human_modes_obey_host_permissions(self):
        self.install('i_undercover');await self.call('创建')
        bad=await self.call('加入AI',user='22');self.assertTrue(bad['game_ai']['error'])
        self.config.plugin_settings[self.pid]['ai_auto_join']=True
        await self.call('结束');packet=await self.call('创建 纯玩家');self.assertFalse(packet['game_ai']['tracking']['active'])
        packet=await self.call('加入AI');self.assertIn('900',self.frame().read()['players'])
        packet=await self.call('AI退出');self.assertNotIn('900',self.frame().read()['players']);self.assertFalse(packet['game_ai']['tracking']['active'])

    async def test_normal_undercover_description_is_observed_without_mention(self):
        self.install('i_undercover');await self.call('创建 人机');await self.call('加入',user='22');await self.call('开始')
        result=await self.manager.execute(self.item,'这是一种常见的东西。',dict(session_id='g:77',user_id='11',_game_ai=dict(op='observe',scope_owner='11')))
        self.assertTrue(result['game_ai']['consumed']);self.assertIsNotNone(result['game_ai']['task'])
