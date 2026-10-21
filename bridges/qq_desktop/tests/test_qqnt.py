"""Private-chat identity boundaries and native input/send failure modes."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from qq_desktop.atspi import UiError, UiUncertain
from qq_desktop.config import validate
from qq_desktop.qqnt import QQNTPrivateDesktop


def config():
    return validate({'backend': 'qqnt_private', 'enabled': True, 'self_id': '10001', 'app_name': 'qq',
        'qqnt': {'main': {'path': [0], 'role': 'frame'}, 'self_avatar': {'path': [0], 'role': 'push button'},
                 'profile': {'name': '资料卡', 'role': 'frame'}},
        'bindings': [{'kind': 'private', 'id': '10002', 'verified': True, 'notices': {'verified': True}}]})


class Node:
    def __init__(self, role='section', name='', text=None, css='', attrs=None, children=(), click=None, action='press'):
        self.role, self.name, self.value, self.click, self.action = role, name, text, click, action
        self.children = list(children); self.attrs = {'class': css, **(attrs or {})}
    def get_name(self): return self.name
    def get_role_name(self): return self.role
    def get_attributes(self): return self.attrs
    def get_child_count(self): return len(self.children)
    def get_child_at_index(self, i): return self.children[i]
    def get_text_iface(self): return self if self.value is not None else None
    def get_character_count(self): return len(self.value)
    def get_text(self, a, b): return self.value[a:b]
    def get_action_iface(self): return self if self.click else None
    def get_n_actions(self): return len(getattr(self, 'actions', [self.action]))
    def get_action_name(self, i): return getattr(self, 'actions', [self.action])[i]
    def do_action(self, i): self.click(); return True
    def get_component_iface(self): return self
    def grab_focus(self): return True
    def get_extents(self, coord): return SimpleNamespace(x=100, y=80, width=960, height=640)
    def get_process_id(self): return 123


def text_row(key, text, own=False):
    return Node(css='ml-item', attrs={'id': key}, children=[
        Node(css='message-container' + (' message-container--self' if own else ''), children=[
            Node(css='message-content mix-message__inner', children=[Node(role='static', text=text)])])])


class QQNTTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config(); self.b = self.cfg['bindings'][0]
        self.current_peer = '10002'; self.clicks = 0; self.opens = []; self.changes = []
        self.number = Node(role='static', text='10002')
        self.uid = Node(css='buddy-profile__header-uid', children=[Node(role='static', text='QQ '), self.number])
        def profile(value):
            self.number.value = value; self.changes.append(value)
        def open_chat(): self.opens.append(self.number.value)
        self.card = Node(role='frame', name='资料卡', children=[
            Node(css='buddy-profile', attrs={'id': 'mini-buddy-profile'}, children=[self.uid,
                Node(role='push button', name='发消息', css='q-button--primary', click=open_chat)])])
        self.avatar = Node(role='push button', name='任意昵称', click=lambda: profile('10001'))
        self.header = Node(role='push button', name='任意昵称', css='chat-header__contact-name', click=lambda: profile(self.current_peer))
        self.editor = Node(role='section', name='10002', css='ExEditor-qq-msg-editor', children=[
            Node(role='static', text='\n', css='ProseMirror-trailingBreak')])
        self.history = Node(css='ml-list', children=[text_row('1234567890000000001', '旧消息')])
        def send():
            self.clicks += 1
            self.history.children.append(text_row('12345678900000000'+str(10+self.clicks), self.driver.static_text(self.editor), True))
            self.editor.children = []
        self.button = Node(role='push button', name='发送', css='send-msg', click=send)
        self.frame = Node(role='frame', name='QQ', children=[self.avatar, self.header, self.editor, self.history, self.button])
        self.root = Node(role='application', children=[self.frame, self.card])
        self.driver = QQNTPrivateDesktop.__new__(QQNTPrivateDesktop)
        self.driver.config = self.cfg; self.driver.proof_at = 0; self.driver.poke_proofs = {}; self.driver._input = None
        self.driver.api = SimpleNamespace(Text=Node, Action=Node, Component=Node, CoordType=SimpleNamespace(SCREEN=0))
        self.driver.app = lambda: self.root
        self.driver.open_control = lambda n: n.click() if n.click else None
        self.driver.private_main = self.driver.main
        self.driver.peer_avatar = lambda main: self.header
        self.sleep = patch('qq_desktop.qqnt.time.sleep', lambda _: None); self.sleep.start(); self.addCleanup(self.sleep.stop)

    def input(self, transform=lambda v: v):
        def paste(text, rect, pid, readback):
            self.assertEqual(rect, (100, 80, 960, 640)); self.assertEqual(pid, 123)
            self.assertEqual(readback(), '')
            self.editor.children = [Node(role='static', text=transform(text))]
        self.driver._input = SimpleNamespace(paste=paste)

    def test_real_profile_transitions_and_private_direction_not_nicknames(self):
        self.assertFalse(self.driver.ready())
        result = self.driver.snapshot(self.b)
        self.assertEqual(self.changes, ['10001', '10002'])
        self.assertEqual(result[0]['user_id'], '10002'); self.assertEqual(self.opens, [])
        self.assertTrue(self.driver.ready()); self.assertEqual(self.clicks, 0)

    def test_unverified_disabled_probe_checks_live_identity_without_sending(self):
        candidate = copy.deepcopy(self.cfg); candidate['enabled'] = False; candidate['bindings'][0]['verified'] = False
        self.driver.config = candidate
        result = self.driver.probe(candidate, candidate['bindings'][0])
        self.assertTrue(result['identity_verified']); self.assertFalse(result['no_navigation'])
        self.assertIs(self.driver.config, candidate); self.assertFalse(candidate['enabled']); self.assertEqual(self.clicks, 0)

    def test_same_nickname_wrong_peer_cannot_receive_or_send(self):
        self.current_peer = '10003'; self.input()
        with self.assertRaises(UiError): self.driver.snapshot(self.b)
        with self.assertRaises(UiError): self.driver.send(self.b, '不要发错')
        self.assertEqual(self.clicks, 0); self.assertEqual(self.opens, [])

    def test_stale_profile_cannot_substitute_for_self_transition(self):
        self.avatar.click = lambda: None
        with self.assertRaises(UiError): self.driver.snapshot(self.b)
        self.assertFalse(self.driver.ready()); self.assertEqual(self.clicks, 0)

    def test_number_in_nickname_or_body_does_not_substitute_for_labelled_field(self):
        self.uid.children[0].value = '昵称'; self.header.name = '10002'
        self.history.children.append(text_row('1234567890000000002', 'QQ 10001'))
        with self.assertRaises(UiError): self.driver.snapshot(self.b)
        self.assertEqual(self.clicks, 0)

    def test_duplicate_native_row_ids_stop_without_guessing(self):
        self.history.children.append(text_row('1234567890000000001', '第二条'))
        with self.assertRaises(UiError): self.driver.snapshot(self.b)

    def test_empty_editor_uses_native_text_not_recipient_name(self):
        self.assertEqual(self.driver.static_text(self.editor), '')
        self.input(); key = self.driver.send(self.b, '中文回执')
        self.assertEqual(self.clicks, 1); self.assertEqual(len(key), 64)
        self.assertEqual(self.driver.static_text(self.editor), '')

    def test_preserve_draft_and_changed_readback_never_clicks_send(self):
        self.editor.children = [Node(role='static', text='我的草稿')]
        self.input()
        with self.assertRaises(UiError): self.driver.send(self.b, '新内容')
        self.assertEqual(self.driver.static_text(self.editor), '我的草稿'); self.assertEqual(self.clicks, 0)
        self.editor.children = []; self.input(lambda _: '截断')
        with self.assertRaises(UiError): self.driver.send(self.b, '新内容')
        self.assertEqual(self.clicks, 0)

    def test_target_change_during_paste_is_blocked_before_send(self):
        self.input()
        real = self.driver._input.paste
        def changed(*args): real(*args); self.current_peer = '10003'
        self.driver._input.paste = changed
        with self.assertRaises(UiError): self.driver.send(self.b, '不应发送')
        self.assertEqual(self.clicks, 0)

    def test_send_without_unique_confirmation_is_uncertain_once(self):
        self.input(); self.button.click = lambda: setattr(self, 'clicks', self.clicks + 1)
        with self.assertRaises(UiUncertain): self.driver.send(self.b, '结果未知')
        self.assertEqual(self.clicks, 1)

    def tip(self, actor_id='10002'):
        actor = Node(role='link', name='10001', css='gray-tip-action', action='jump',
                     click=lambda: setattr(self.number, 'value', actor_id))
        return Node(css='ml-item', attrs={'id': '1234567890000000099'}, children=[
            Node(css='gray-tip-message', children=[Node(css='gray-tip-content', children=[actor,
                Node(role='image', css='gray-tip-img', attrs={'src': 'http://tianquan.gtimg.cn/nudgeaction/item/0/expression.jpg'}),
                Node(role='static', text='戳了戳'), Node(role='static', text='你')])])])

    def test_poke_requires_native_system_structure_and_numeric_actor_profile(self):
        self.history.children.append(text_row('1234567890000000002', '任意昵称 戳了戳你'))
        self.assertFalse(any(r.get('notice') for r in self.driver.snapshot(self.b)))
        self.history.children.append(self.tip())
        rows = self.driver.snapshot(self.b); poke = rows[-1]
        self.assertEqual((poke['notice'], poke['user_id'], poke['target_id']), ('poke', '10002', '10001'))
        self.assertEqual(self.clicks, 0)

    def test_poke_actor_nickname_cannot_impersonate_numeric_peer(self):
        self.history.children.append(self.tip('10003'))
        with self.assertRaises(UiError): self.driver.snapshot(self.b)
        self.assertEqual(self.clicks, 0)

    def test_native_profile_jump_coexists_with_context_menu_action(self):
        tip = self.tip(); actor = tip.children[0].children[0].children[0]
        actor.actions = ['showContextMenu', 'jump']
        self.history.children.append(tip)
        self.assertEqual(self.driver.snapshot(self.b)[-1]['notice'], 'poke')
        self.assertEqual(self.clicks, 0)

    def test_temporary_nodes_without_attributes_are_not_mistaken_for_controls(self):
        temporary=Node();temporary.attrs=None;self.frame.children.append(temporary)
        self.assertEqual(self.driver.snapshot(self.b)[0]['user_id'],'10002')
        self.editor.attrs=None;self.driver._control_paths=None
        with self.assertRaises(UiError):self.driver.one_class(self.frame,'ExEditor-qq-msg-editor','section')

    def test_config_rejects_group_multiple_peers_and_unimplemented_media(self):
        for bindings in ([{**self.b, 'kind': 'group'}], [self.b, {**self.b, 'id': '10003'}],
                         [{**self.b, 'attachments': {'verified': True}}], [{**self.b, 'id': '10001'}]):
            with self.subTest(bindings=bindings), self.assertRaises(ValueError): validate({**self.cfg, 'bindings': bindings})

    def test_readonly_status_and_diagnostics_do_not_open_profiles(self):
        self.driver.ready(); d = self.driver.diagnose()
        self.assertTrue(d['no_qq_message_sent']); self.assertEqual(self.changes, []); self.assertEqual(self.opens, [])

    def test_native_group_header_is_rejected_even_when_owner_sent_every_visible_message(self):
        header = Node(css='chat-header', children=[Node(attrs={'id': 'id-func-bar-group_building'}),
            Node(role='push button', name='在线状态 在线')])
        self.frame.children.append(header)
        self.assertIs(QQNTPrivateDesktop.private_main(self.driver), self.frame)
        header.children.append(Node(attrs={'id': 'id-func-bar-group_application'}))
        with self.assertRaises(UiError): QQNTPrivateDesktop.private_main(self.driver)
        header.children.pop(); header.children.pop()
        with self.assertRaises(UiError): QQNTPrivateDesktop.private_main(self.driver)


if __name__ == '__main__': unittest.main()
