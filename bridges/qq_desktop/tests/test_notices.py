import copy
from pathlib import Path
import tempfile
import unittest
from qq_desktop.bridge import Bridge
from qq_desktop.config import validate
from qq_desktop.atspi import UiError
from test_bridge import config,Driver


def notice_config():
    cfg=config();cfg['bindings'][0]['notices']={'verified':True,'row_type':{'name':'System kind'},'actor_id':{'name':'Actor'},'target_id':{'name':'Target'}}
    return validate(cfg)


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.driver=Driver();self.bridge=Bridge(notice_config(),self.driver,Path(self.tmp.name)/'state')
    async def asyncTearDown(self):await self.bridge.close();self.tmp.cleanup()
    async def test_poke_event_maps_real_numeric_participants_and_is_deduped(self):
        await self.bridge.poll()
        self.driver.rows=[{'key':'poke-1','notice':'poke','user_id':'10002','target_id':'10001','text':''}]
        e=(await self.bridge.poll())[0];self.assertEqual((e['post_type'],e['notice_type'],e['sub_type']),('notice','notify','poke'))
        self.assertEqual((e['user_id'],e['target_id']),(10002,10001));self.assertNotIn('group_id',e)
        self.assertEqual(await self.bridge.poll(),[]);self.assertEqual(self.bridge.metrics['poke_events'],1);self.assertEqual(self.driver.sent,[])
    async def test_poke_history_and_own_pokes_are_not_replayed(self):
        self.driver.rows=[{'key':'old-poke','notice':'poke','user_id':'10002','target_id':'10001','text':''}]
        self.assertEqual(await self.bridge.poll(),[])
        self.driver.rows.append({'key':'own-poke','notice':'poke','user_id':'10001','target_id':'10002','text':''})
        self.assertEqual(await self.bridge.poll(),[])
        await self.bridge.close();self.bridge=Bridge(notice_config(),self.driver,Path(self.tmp.name)/'state')
        self.assertEqual(await self.bridge.poll(),[])
    async def test_unverified_poke_flow_cannot_advertise_or_emit(self):
        self.bridge.config['bindings'][0]['notices']['verified']=False
        self.assertFalse((await self.bridge.capabilities())['poke_receive']['ready']);await self.bridge.poll()
        self.driver.rows=[{'key':'no-calibration','notice':'poke','user_id':'10002','target_id':'10001','text':''}]
        with self.assertRaises(UiError):await self.bridge.poll()
    async def test_group_poke_keeps_group_identity(self):
        b=self.bridge.config['bindings'][0];b['kind']='group';b['id']='20001';await self.bridge.poll()
        self.driver.rows=[{'key':'group-poke','notice':'poke','user_id':'10003','target_id':'10001','text':''}]
        e=(await self.bridge.poll())[0];self.assertEqual(e['group_id'],20001);self.assertEqual(e['user_id'],10003)
    async def test_body_text_does_not_invent_a_notice_and_active_poke_is_unsupported(self):
        await self.bridge.poll();self.driver.rows=[{'key':'ordinary','user_id':'10002','text':'10002 戳了戳 10001'}]
        self.assertEqual((await self.bridge.poll())[0]['post_type'],'message')
        with self.assertRaises(NotImplementedError):await self.bridge.action('send_poke',{'user_id':10002})
    async def test_notice_selectors_cannot_be_the_body_or_alias_each_other(self):
        cfg=notice_config();n=cfg['bindings'][0]['notices'];n['row_type']=copy.deepcopy(cfg['bindings'][0]['body'])
        with self.assertRaises(ValueError):validate(cfg)
        cfg=notice_config();cfg['bindings'][0]['notices']['target_id']=cfg['bindings'][0]['notices']['actor_id']
        with self.assertRaises(ValueError):validate(cfg)
