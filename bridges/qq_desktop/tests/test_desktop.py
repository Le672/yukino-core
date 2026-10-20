import copy
import unittest
from qq_desktop.atspi import AtspiDesktop,UiError
from test_bridge import config


class Node:
    def __init__(self,name='',text=None,children=None,key='',click=None):
        self.name,self.value,self.children,self.key,self.click=name,text,list(children or []),key,click
    def get_name(self):return self.name
    def get_role_name(self):return 'push button' if self.click else 'label'
    def get_child_count(self):return len(self.children)
    def get_child_at_index(self,index):return self.children[index]
    def get_accessible_id(self):return self.key
    def get_description(self):return self.key
    def get_text_iface(self):return self if self.value is not None else None
    def get_character_count(self):return len(self.value)
    def get_text(self,a,b):return self.value[a:b]
    def get_action_iface(self):return self if self.click else None
    def get_n_actions(self):return 1
    def get_action_name(self,index):return 'click'
    def do_action(self,index):self.click();return True
    def get_editable_text_iface(self):return self
    def set_text_contents(self,text):self.value=getattr(self,'transform',lambda value:value)(text);return True


def row(key,sender,text,uid=None):
    uid=uid or ('10001' if sender=='Me' else '10002' if sender=='Peer' else '10003')
    return Node(key=key,children=[Node('sender',sender),Node('body',text),Node('sender_id',uid)])


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.cfg=config();self.cfg['app_name']='QQ';self.binding=self.cfg['bindings'][0]
        self.history=Node('history',children=[row('r1','Peer','old')])
        self.editor=Node('editor','');self.clicks=0
        def sent():
            self.clicks+=1;self.history.children.append(row('r'+str(self.clicks+1),'Me',self.editor.value));self.editor.value=''
        self.root=Node('QQ',children=[Node('10001'),Node('contact',click=lambda:None),Node('header'),self.history,self.editor,Node('send',click=sent),Node('target_identity','10002')])
        self.desktop=AtspiDesktop.__new__(AtspiDesktop);self.desktop.config=self.cfg
        class API:
            Text=Node
            Action=Node
            EditableText=Node
            def __init__(self,root):self.root=root
            def get_desktop(self,index):return Node(children=[self.root])
        self.desktop.api=API(self.root)
    def test_unique_target_single_click_and_visible_outgoing_confirmation(self):
        self.assertTrue(self.desktop.ready());key=self.desktop.send(self.binding,'hello')
        self.assertEqual(self.clicks,1);self.assertEqual(len(self.desktop.snapshot(self.binding)),2);self.assertTrue(key)
    def test_wrong_account_or_blocked_ui_cannot_send(self):
        self.root.children[0].name='99999'
        self.assertFalse(self.desktop.ready())
        with self.assertRaises(UiError):self.desktop.send(self.binding,'no')
        self.root.children[0].name='10001';self.root.children.append(Node('安全验证'))
        self.assertFalse(self.desktop.ready());self.assertEqual(self.clicks,0)
    def test_ambiguous_sender_or_missing_row_identity_rejects_poll(self):
        self.history.children.append(row('r2','unbound sender','x'))
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
        self.history.children.pop();self.history.children[0].key=''
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
    def test_duplicate_message_keys_are_not_inferred_as_distinct_events(self):
        self.history.children.append(row('r1','Peer','old'))
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
    def test_existing_draft_is_preserved_and_send_not_clicked(self):
        self.editor.value='my existing draft'
        with self.assertRaises(UiError):self.desktop.send(self.binding,'no')
        self.assertEqual(self.editor.value,'my existing draft');self.assertEqual(self.clicks,0)
    def test_wrong_or_ambiguous_header_is_not_sent_to(self):
        self.root.children.append(Node('header'))
        with self.assertRaises(UiError):self.desktop.send(self.binding,'no')
        self.assertEqual(self.clicks,0);self.assertEqual(self.editor.value,'')
    def test_duplicate_applications_are_not_guessed(self):
        root=self.root
        self.desktop.api.get_desktop=lambda index:Node(children=[root,Node('QQ')])
        self.assertFalse(self.desktop.ready())
        with self.assertRaises(UiError):self.desktop.send(self.binding,'no')
    def test_changed_draft_readback_cannot_trigger_send(self):
        self.editor.transform=lambda value:'different'
        with self.assertRaises(UiError):self.desktop.send(self.binding,'no')
        self.assertEqual(self.clicks,0)
    def test_nickname_cannot_impersonate_numeric_sender_or_target(self):
        self.history.children=[row('rename','Me','nickname is not identity',uid='10002')]
        self.assertEqual(self.desktop.snapshot(self.binding)[0]['user_id'],'10002')
        self.history.children[0].children[-1].value='Me'
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
        self.root.children[-1].value='10003'
        with self.assertRaises(UiError):self.desktop.send(self.binding,'must not send to another target')
        self.assertEqual(self.clicks,0)
    def test_readonly_probe_never_clicks_and_restores_driver_configuration(self):
        original=self.desktop.config
        self.assertTrue(self.desktop.probe({**self.cfg,'enabled':False},self.binding)['numeric_senders_verified'])
        self.assertIs(self.desktop.config,original);self.assertEqual(self.clicks,0)
        with self.assertRaises(UiError):self.desktop.probe({**self.cfg,'self_id':'99999'},self.binding)
        self.assertIs(self.desktop.config,original);self.assertEqual(self.clicks,0)
    def test_attachment_target_change_before_accept_cannot_submit_file(self):
        m={'verified':True,'kinds':['image']}
        for k in ('button','dialog','path','accept','row_kind','row_name'):m[k]={'name':k}
        self.binding['attachments']=m
        self.history.children[0].children.extend([Node('row_kind','text'),Node('row_name','')])
        self.accepted=0
        def accept():self.accepted+=1
        def open_dialog():
            self.root.children[-1].value='10003'
            self.root.children.append(Node('dialog',children=[Node('path',''),Node('accept',click=accept)]))
        self.root.children.insert(-1,Node('button',click=open_dialog))
        with self.assertRaises(UiError):self.desktop.send_attachment(self.binding,{'kind':'image','name':'x.png','path':'/media/x.png'})
        self.assertEqual(self.accepted,0);self.assertEqual(self.editor.value,'')
    def notice_fields(self):
        n={'verified':True,'row_type':{'name':'system_kind'},'actor_id':{'name':'actor'},'target_id':{'name':'target'}}
        self.binding['notices']=n
        self.history.children[0].children.extend([Node('system_kind','message'),Node('actor',''),Node('target','')])
        return n
    def test_system_poke_is_read_only_and_never_guessed_from_chat_text(self):
        self.notice_fields();r=self.history.children[0];r.children[1].value='10002 戳了戳 10001'
        self.assertNotIn('notice',self.desktop.snapshot(self.binding)[0])
        r.children[3].value='poke';r.children[4].value='10002';r.children[5].value='10001'
        result=self.desktop.snapshot(self.binding)[0]
        self.assertEqual(result['notice'],'poke');self.assertEqual(result['target_id'],'10001');self.assertEqual(self.clicks,0)
    def test_poke_numeric_identity_scope_unknown_kind_and_selector_alias_are_rejected(self):
        n=self.notice_fields();r=self.history.children[0]
        r.children[3].value='poke';r.children[4].value='nickname';r.children[5].value='10001'
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
        r.children[4].value='10003'
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
        r.children[4].value='10002';r.children[3].value='other-event'
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
        # Different selector syntax that still aliases the body must fail.
        n['row_type']={'path':[1]};r.children[1].value='poke'
        with self.assertRaises(UiError):self.desktop.snapshot(self.binding)
        self.assertEqual(self.clicks,0)
    def test_first_calibration_accepts_unverified_binding_without_enabling_driver(self):
        self.binding['verified']=False;self.cfg['enabled']=False;original=self.desktop.config
        result=self.desktop.probe(self.cfg,self.binding)
        self.assertTrue(result['identity_verified']);self.assertIs(self.desktop.config,original)
        self.assertFalse(self.desktop.config['enabled']);self.assertFalse(self.binding['verified']);self.assertEqual(self.clicks,0)
    def test_diagnostics_is_readonly_and_distinguishes_missing_identity(self):
        self.assertTrue(self.desktop.diagnose()['identity_ready']);self.assertEqual(self.clicks,0)
        self.root.children[0].name='99999';d=self.desktop.diagnose()
        self.assertFalse(d['identity_ready']);self.assertTrue(d['no_qq_message_sent']);self.assertEqual(self.clicks,0)
        self.root.children.append(Node('安全验证'));d=self.desktop.diagnose()
        self.assertFalse(next(c['ok'] for c in d['checks'] if c['code']=='login_screen'))


if __name__=='__main__':unittest.main()
