"""Official QQNT private-chat adapter, calibrated against Linux QQ 3.2.34.

QQ numbers come from the native profile's labelled numeric field. They never
come from names, message text or a nickname-to-ID table. Group/media are absent.
Every read/send opens peer -> self -> peer profiles and requires real field
transitions. Use a dedicated desktop; concurrent manual chat changes pause it.
"""
import hashlib
import re
import time

from .atspi import AtspiDesktop, UiError, UiUncertain
from .config import numeric, validate


def classes(node):
    return set((node.get_attributes() or {}).get('class', '').split())


class QQNTPrivateDesktop(AtspiDesktop):
    def __init__(self, config):
        super().__init__(config)
        self.proof_at = 0
        self.poke_proofs = {}
        self._input = None
        self._profile_cache = None

    def one_class(self, root, value, role=None):
        # Cache paths only for QQ's main controls, never numeric identities or
        # row bodies. Re-read native class/role at the path on every use.
        main_classes = {'chat-header','chat-msg-area','ml-list','ExEditor-qq-msg-editor','send-msg','chat-header__contact-name'}
        cache = getattr(self,'_control_paths',None)
        if value in main_classes and cache and cache[0] == root and value in cache[1]:
            try:
                node=self.find(root,{'path':cache[1][value]})
                if value in classes(node) and (role is None or node.get_role_name()==role):
                    return node
            except Exception:
                pass
            self._control_paths=None
        walked=list(self.walk(root))
        labelled=[(n,p,classes(n)) for n,p in walked]
        if value in main_classes:
            paths={}
            for token in main_classes:
                found=[p for n,p,c in labelled if token in c]
                if len(found)==1:paths[token]=found[0]
            self._control_paths=(root,paths)
        nodes = [n for n, _, c in labelled if value in c and
                 (role is None or n.get_role_name() == role)]
        if len(nodes) != 1:
            raise UiError('QQNT native control is missing or ambiguous: ' + value)
        return nodes[0]

    def main(self):
        root = self.app()
        main = self.find(root, self.config['qqnt']['main'])
        if main.get_process_id() <= 0:
            raise UiError('Native QQ process identity is missing')
        return main

    def available(self):
        if not self.config['enabled']:
            return False
        try:
            self.private_main()
            return True
        except Exception:
            return False

    def private_main(self):
        main = self.main()
        header = self.one_class(main, 'chat-header')
        nodes = list(self.walk(header))
        # These are native QQ header controls, outside names/message bodies.
        # This pinned mode requires the private contact-status + create-group
        # toolbar combination. Unknown/group header layouts fail closed.
        if (any((n.get_attributes() or {}).get('id') == 'id-func-bar-group_application' for n, _ in nodes) or
            not any((n.get_attributes() or {}).get('id') == 'id-func-bar-group_building' for n, _ in nodes) or
            sum(n.get_role_name() == 'push button' and n.get_name().startswith('在线状态 ') for n, _ in nodes) != 1):
            raise UiError('QQNT native private conversation markers are missing')
        return main

    def profile(self):
        root = self.app(); sel = self.config['qqnt']['profile']
        if 'path' in sel:
            card = self.find(root, sel)
        else:
            # QQ reuses a top-level profile window. Do not scan all cached chat
            # windows (or user message bodies) to locate this window.
            cards = [root.get_child_at_index(i) for i in range(min(root.get_child_count(), 100))]
            cards = [n for n in cards if ('name' not in sel or n.get_name() == sel['name']) and
                     ('role' not in sel or n.get_role_name() == sel['role'])]
            if len(cards) != 1:
                raise UiError('Native QQ profile window is missing or ambiguous')
            card = cards[0]
        cached = getattr(self, '_profile_cache', None)
        if cached and cached[0] == card:
            profile, field = cached[1:]
        else:
            profile = self.one_class(card, 'buddy-profile')
            field = self.one_class(profile, 'buddy-profile__header-uid')
            self._profile_cache = (card, profile, field)
        if (profile.get_attributes() or {}).get('id') != 'mini-buddy-profile':
            raise UiError('The native QQ profile structure changed')
        if field.get_child_count() != 2:
            raise UiError('The native QQ number field structure changed')
        label, number = (field.get_child_at_index(i) for i in (0, 1))
        if any(n.get_role_name() != 'static' for n in (label, number)) or self.text(label) != 'QQ ':
            raise UiError('QQ identity must use the genuine labelled numeric profile field')
        return numeric(self.text(number).strip()), profile

    def wait_profile(self, expected):
        for _ in range(30):
            try:
                value, profile = self.profile()
                if value == expected:
                    return profile
            except (UiError, ValueError):
                pass
            self._profile_cache = None
            time.sleep(.05)
        self.proof_at = 0
        raise UiError('Native QQ numeric profile does not match the calibrated account/peer')

    def open_control(self, node):
        # QQ advertises a press action for these controls, but on the tested
        # client it does not open the profile. Use the observed native control
        # rectangle and the system mouse API; every resulting profile is checked.
        component = node.get_component_iface()
        if not component or node.get_role_name() not in ('push button', 'link', 'section'):
            raise UiError('Native profile control cannot be clicked')
        rect = self.api.Component.get_extents(component, self.api.CoordType.SCREEN)
        if not (0 <= rect.x < 16384 and 0 <= rect.y < 16384 and 0 < rect.width < 4096 and 0 < rect.height < 4096):
            raise UiError('Native profile control is not on screen')
        if self._input is None:
            from .native_input import X11Clipboard
            self._input = X11Clipboard()
        main = self.main()
        frame = self.api.Component.get_extents(main.get_component_iface(), self.api.CoordType.SCREEN)
        self._input.click((rect.x, rect.y, rect.width, rect.height),
                          (frame.x, frame.y, frame.width, frame.height), main.get_process_id())

    def dismiss_profile(self):
        # A profile can cover the chat header/avatar. Clicking the empty/owned
        # editor only focuses it; it neither changes a draft nor sends anything.
        editor = self.one_class(self.main(), 'ExEditor-qq-msg-editor', 'section')
        self.open_control(editor)

    def close_verified_profile(self, expected):
        value, _ = self.profile()
        if value != expected:
            raise UiError('Native QQ peer identity changed before returning to the chat')
        self.dismiss_profile()
        self.private_main()

    def peer_avatar(self, main):
        area = self.one_class(main, 'chat-msg-area')
        bounds = self.api.Component.get_extents(area.get_component_iface(), self.api.CoordType.SCREEN)
        for _, row in reversed(self.native_rows(main)):
            nodes = list(self.walk(row))
            incoming = [n for n, _ in nodes if 'message-container' in classes(n) and 'message-container--self' not in classes(n)]
            if len(incoming) != 1:
                continue
            avatars = [n for n, _ in self.walk(incoming[0]) if 'message-container__avatar' in classes(n)]
            if len(avatars) != 1:
                continue
            r = self.api.Component.get_extents(avatars[0].get_component_iface(), self.api.CoordType.SCREEN)
            x, y = r.x + r.width / 2, r.y + r.height / 2
            if bounds.x <= x < bounds.x + bounds.width and bounds.y <= y < bounds.y + bounds.height:
                return avatars[0]
        raise UiError('No visible incoming private message avatar is available for numeric peer verification')

    def open_peer_profile(self, main):
        # On this QQ build, the native avatar wrapper does not reliably open a
        # profile. A genuine system poke actor link has an explicit profile jump.
        for _, row in reversed(self.native_rows(main)):
            for tip, _ in self.walk(row):
                if 'gray-tip-message' not in classes(tip):
                    continue
                actor = self.standard_poke_actor(tip)
                if actor is not None:
                    self.jump_profile(actor)
                    return
        self.open_control(self.peer_avatar(main))

    def jump_profile(self, actor):
        action = actor.get_action_iface()
        choices = [] if not action else [i for i in range(self.api.Action.get_n_actions(action))
                                       if self.api.Action.get_action_name(action, i).lower() == 'jump']
        if len(choices) != 1:
            raise UiError('Native poke actor does not have a unique profile action')
        if not self.api.Action.do_action(action, choices[0]):
            raise UiError('Native poke actor profile could not be opened')

    def select(self, binding):
        if binding['kind'] != 'private' or binding['id'] == self.config['self_id']:
            raise UiError('QQNT backend requires one distinct private peer')
        if not self.config['enabled']:
            raise UiError('QQ is offline or the native private chat is unavailable')
        self.proof_at = 0
        self._profile_cache = None
        main = self.private_main()
        self.dismiss_profile()
        try:before=self.profile()[0]
        except (UiError,ValueError):before=None
        if before is None or before==self.config['self_id']:
            self.open_peer_profile(main)
            self.wait_profile(binding['id'])
        # Require two actual changes: non-self -> self -> peer. If the field
        # already contains self, first establish a different peer baseline.
        self.dismiss_profile()
        self.open_control(self.find(main, self.config['qqnt']['self_avatar']))
        self.wait_profile(self.config['self_id'])
        self.dismiss_profile()
        self.open_peer_profile(main)
        self.wait_profile(binding['id'])
        self.close_verified_profile(binding['id'])
        self.proof_at = time.monotonic()
        return self.main()

    def ready(self):
        # Status/diagnostics are read-only. A real operation establishes proof.
        try:
            return self.available() and time.monotonic() - self.proof_at < 30 and self.profile()[0] == self.config['bindings'][0]['id']
        except Exception:
            return False

    def static_text(self, root, *, editor=False):
        parts = []
        budget = [0]
        def visit(node, depth=0):
            budget[0] += 1
            if budget[0] > 1000 or depth > 30 or node.get_child_count() > 500:
                raise UiError('Native text structure exceeds the traversal budget')
            if 'ProseMirror-trailingBreak' in classes(node):
                return
            role = node.get_role_name()
            if role == 'static':
                parts.append(self.text(node))
                return
            if role == 'image':
                raise UiError('QQNT private backend has no image/emoji text conversion')
            for i in range(node.get_child_count()):
                child = node.get_child_at_index(i)
                if child is not None:
                    visit(child, depth + 1)
        visit(root)
        value = ''.join(parts)
        if len(value) > 16000:
            raise UiError('Native text exceeds the read limit')
        return value

    def native_rows(self, main):
        history = self.one_class(main, 'ml-list')
        if history.get_child_count() > 500:
            raise UiError('Visible history exceeds 500 rows')
        rows, keys = [], set()
        for i in range(history.get_child_count()):
            row = history.get_child_at_index(i)
            if 'ml-item' not in classes(row):
                continue
            key = (row.get_attributes() or {}).get('id', '')
            if not re.fullmatch(r'[1-9][0-9]{9,24}', key) or key in keys:
                raise UiError('Native QQ stable row ID is missing or duplicated')
            keys.add(key); rows.append((key, row))
        return rows

    def standard_poke_actor(self, row):
        containers = [n for n, _ in self.walk(row) if 'gray-tip-content' in classes(n)]
        if len(containers) != 1:
            return None
        tip = containers[0]
        if tip.get_child_count() != 4:
            return None
        actor, image, verb, target = (tip.get_child_at_index(i) for i in range(4))
        if (actor.get_role_name() != 'link' or 'gray-tip-action' not in classes(actor) or
            image.get_role_name() != 'image' or 'gray-tip-img' not in classes(image) or
            (image.get_attributes() or {}).get('src') != 'http://tianquan.gtimg.cn/nudgeaction/item/0/expression.jpg' or
            verb.get_role_name() != 'static' or self.text(verb) != '戳了戳' or
            target.get_role_name() != 'static' or self.text(target) != '你'):
            return None
        return actor

    def poke(self, row, key, binding):
        actor = self.standard_poke_actor(row)
        if actor is None:
            return None
        if key not in self.poke_proofs:
            # The actor link must resolve to the same numeric peer; its visible
            # nickname is never used as an identity. Transition from self first.
            main = self.main()
            self.open_control(self.find(main, self.config['qqnt']['self_avatar']))
            self.wait_profile(self.config['self_id'])
            self.dismiss_profile()
            self.jump_profile(actor)
            self.wait_profile(binding['id'])
            self.close_verified_profile(binding['id'])
            self.poke_proofs[key] = binding['id']
            if len(self.poke_proofs) > 500:
                self.poke_proofs.pop(next(iter(self.poke_proofs)))
        if self.poke_proofs[key] != binding['id']:
            raise UiError('Native poke proof belongs to another conversation')
        return {'key': hashlib.sha256(('qqnt-poke:' + key).encode()).hexdigest(),
                'notice': 'poke', 'user_id': binding['id'], 'target_id': self.config['self_id'], 'text': ''}

    def read_rows(self, main, binding, *, notices=True):
        return self.parse_rows(self.native_rows(main), binding, notices=notices)

    def parse_rows(self, rows, binding, *, notices=True):
        result = []
        for key, row in rows:
            nodes = list(self.walk(row))
            containers = [n for n, _ in nodes if 'message-container' in classes(n)]
            tips = [n for n, _ in nodes if 'gray-tip-message' in classes(n)]
            if tips:
                if len(tips) != 1 or containers:
                    raise UiError('Ambiguous QQ system row structure')
                if notices and binding.get('notices', {}).get('verified'):
                    notice = self.poke(tips[0], key, binding)
                    if notice:
                        result.append(notice)
                continue
            if not containers:
                continue
            if len(containers) != 1:
                raise UiError('Ambiguous native QQ message container')
            body = [n for n, _ in nodes if {'message-content', 'mix-message__inner'} <= classes(n)]
            if not body:
                continue  # Media, cards and other unimplemented native row kinds.
            if len(body) != 1:
                raise UiError('Ambiguous native QQ text body')
            try:
                text = self.static_text(body[0])
            except UiError as error:
                if str(error) != 'QQNT private backend has no image/emoji text conversion':
                    raise
                continue
            if not text:
                continue
            uid = self.config['self_id'] if 'message-container--self' in classes(containers[0]) else binding['id']
            result.append({'key': hashlib.sha256(('qqnt-message:' + key).encode()).hexdigest(),
                           'user_id': uid, 'text': text})
        return result

    def snapshot(self, binding, navigate=True):
        main = self.select(binding) if navigate else self.main()
        return self.read_rows(main, binding)

    def send(self, binding, text):
        self.phase='identity'
        main = self.select(binding)
        self.phase='draft_guard'
        before = {key for key, _ in self.native_rows(main)}
        editor = self.one_class(main, 'ExEditor-qq-msg-editor', 'section')
        if self.static_text(editor, editor=True):
            raise UiError('An existing draft is present; it was not overwritten')
        if not self.api.Component.grab_focus(editor.get_component_iface()):
            raise UiError('The native QQ editor rejected focus')
        if self._input is None:
            from .native_input import X11Clipboard
            self._input = X11Clipboard()
        rect = self.api.Component.get_extents(main.get_component_iface(), self.api.CoordType.SCREEN)
        self.phase='paste'
        self._input.paste(text, (rect.x, rect.y, rect.width, rect.height), main.get_process_id(),
                          lambda: self.static_text(editor, editor=True))
        # Re-open the actual numeric profiles immediately before the only Send
        # click. Do not trust a cached card, nickname, editor name or foreground.
        self.phase='identity_recheck'
        main = self.select(binding)
        editor = self.one_class(main, 'ExEditor-qq-msg-editor', 'section')
        if self.static_text(editor, editor=True) != text:
            raise UiError('Draft readback does not match; no send was attempted')
        try:
            self.phase='click'
            self.activate(self.one_class(main, 'send-msg', 'push button'))
            self.phase='confirmation'
            for _ in range(40):
                found = []
                for key, row in self.native_rows(self.main()):
                    if key in before:
                        continue
                    messages = self.parse_rows([(key, row)], binding, notices=False)
                    if any(m['user_id'] == self.config['self_id'] and m['text'] == text for m in messages):
                        found.append(key)
                if len(found) == 1:
                    self.phase='confirmed'
                    return hashlib.sha256(('qqnt-message:' + found[0]).encode()).hexdigest()
                if len(found) > 1:
                    break
                time.sleep(.1)
        except Exception:
            raise UiUncertain('Send may have reached QQ; it will not be retried automatically') from None
        raise UiUncertain('No unique outgoing native QQ row appeared; it was not resent')

    def send_attachment(self, binding, record):
        raise UiError('QQNT private backend does not implement attachments')

    def probe(self, config, binding=None):
        original = self.config
        try:
            self.config = validate({**config, 'enabled': False})
            numeric(self.config.get('self_id'))
            if not self.config['bindings']:
                raise UiError('QQNT backend requires one private binding before calibration')
            binding = binding or self.config['bindings'][0]
            self.config = {**self.config, 'enabled': True}
            rows = self.snapshot(binding)
            return {'identity_verified': True, 'numeric_senders_verified': False, 'private_participants_verified': True,
                    'identity_source': 'native_profile_numeric_field_and_private_direction',
                    'visible_rows': len(rows), 'visible_poke_rows': sum(r.get('notice') == 'poke' for r in rows),
                    'no_navigation': False, 'no_qq_message_sent': True}
        finally:
            self.config = original

    def diagnose(self):
        available = self.available() if self.config['enabled'] else self._inspect_available()
        ready = self.ready()
        return {'identity_ready': ready, 'no_qq_message_sent': True, 'checks': [
            {'code': 'native_private_ui', 'ok': available, 'message': '官方 QQ 私聊控件可读取' if available else '官方 QQ 私聊控件尚未就绪',
             'next_step': '' if available else '登录并打开已绑定的私聊；本模式不接管群聊。'},
            {'code': 'native_identity', 'ok': ready, 'message': '最近一次本人及对方数字 QQ 号核验有效' if ready else '需要重新核验数字 QQ 号',
             'next_step': '' if ready else '核验操作会打开本人及对方资料卡，但不会发送消息。'},
        ]}

    def _inspect_available(self):
        try:
            self.private_main()
            return True
        except Exception:
            return False
