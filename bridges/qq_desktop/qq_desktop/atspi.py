"""Only operating-system accessibility APIs; no injection, CDP or QQ private API."""
import hashlib
import re
import time


class UiError(RuntimeError):
    pass


class UiUncertain(UiError):
    pass


class AtspiDesktop:
    def __init__(self, config):
        import gi
        gi.require_version('Atspi', '2.0')
        from gi.repository import Atspi
        self.api = Atspi
        Atspi.init()
        self.config = config

    def walk(self, root, limit=2500):
        queue = [(root, [])]; count = 0
        while queue:
            node, path = queue.pop(0); count += 1
            if count > limit:
                raise UiError('Accessibility tree exceeds the traversal budget')
            yield node, path
            children = node.get_child_count()
            if children > 2000 or len(path) > 30:
                raise UiError('Accessibility tree exceeds structural limits')
            for i in range(children):
                child = node.get_child_at_index(i)
                if child is not None: queue.append((child, path + [i]))

    def app(self):
        desktop = self.api.get_desktop(0)
        apps = [desktop.get_child_at_index(i) for i in range(min(desktop.get_child_count(), 200))]
        apps = [a for a in apps if a and a.get_name() == self.config['app_name']]
        if len(apps) != 1:
            raise UiError('Expected one exact QQ application; inspect and calibrate the desktop')
        return apps[0]

    def find(self, root, selector):
        if 'path' in selector:
            node = root
            for index in selector['path']:
                if index >= node.get_child_count(): raise UiError('Calibrated UI path no longer exists')
                node = node.get_child_at_index(index)
            candidates = [node]
        else:
            candidates = [node for node, _ in self.walk(root)]
        matches = [n for n in candidates if ('name' not in selector or n.get_name() == selector['name'])
                   and ('role' not in selector or n.get_role_name() == selector['role'])]
        if len(matches) != 1:
            raise UiError('UI selector is missing or ambiguous; no click was attempted')
        return matches[0]

    def text(self, node):
        interface = node.get_text_iface()
        if interface:
            count = self.api.Text.get_character_count(interface)
            if count > 16000: raise UiError('Visible text exceeds the read limit')
            return self.api.Text.get_text(interface, 0, count)
        return node.get_name() or ''

    def ready(self):
        if not self.config['enabled']: return False
        try:
            root = self.app()
            for node, _ in self.walk(root):
                if node.get_name() in self.config['blocked_labels']: return False
            identity = self.find(root, self.config['identity'])
            # The calibrated account field must actually display the account,
            # rather than trusting a configured nickname or an active window.
            return self.text(identity).strip() == self.config['self_id']
        except Exception:
            return False

    def activate(self, node):
        action = node.get_action_iface()
        if not action: raise UiError('This control has no accessible action')
        choices = [i for i in range(self.api.Action.get_n_actions(action)) if self.api.Action.get_action_name(action, i).lower() in ('click', 'press', 'activate')]
        if len(choices) != 1: raise UiError('The accessible click action is ambiguous')
        if not self.api.Action.do_action(action, choices[0]): raise UiError('The desktop rejected the click')

    def select(self, binding):
        if not self.ready(): raise UiError('QQ is not logged in, blocked or not calibrated')
        root = self.app()
        self.activate(self.find(root, binding['contact']))
        # Waiting for a specific observed control is bounded; no blind Enter or
        # coordinate click can send a message to a different foreground window.
        for _ in range(20):
            try:
                root = self.app(); self.find(root, binding['header'])
                if self.text(self.find(root,binding['target_identity'])).strip()!=binding['id']:
                    raise UiError('Visible numeric conversation identity does not match the binding')
                return root
            except UiError: time.sleep(.05)
        raise UiError('Conversation header did not match the binding')

    def snapshot(self, binding, navigate=True):
        root = self.select(binding) if navigate else self.app()
        self.find(root, binding['header'])
        if self.text(self.find(root,binding['target_identity'])).strip()!=binding['id']:
            raise UiError('Visible numeric conversation identity changed')
        history = self.find(root, binding['history'])
        if history.get_child_count() > 500: raise UiError('Visible history exceeds 500 rows')
        result = []
        for i in range(history.get_child_count()):
            row = history.get_child_at_index(i)
            uid = self.text(self.find(row, binding['sender_id'])).strip()
            if not re.fullmatch(r'[1-9][0-9]{3,19}',uid):
                raise UiError('A genuine visible numeric sender field is required; nicknames are not identities')
            if binding['kind']=='private' and uid not in (binding['id'],self.config['self_id']):
                raise UiError('Visible sender does not belong to the verified private conversation')
            text = self.text(self.find(row, binding['body']))
            source = {'accessible_id': row.get_accessible_id, 'description': row.get_description, 'name': row.get_name}[binding['row_key']]()
            if not source: raise UiError('Stable visible message key is missing; calibrate row_key')
            key = hashlib.sha256((source + '\0' + uid + '\0' + text).encode()).hexdigest()
            result.append({'key': key, 'user_id': uid, 'text': text})
        if len({m['key'] for m in result}) != len(result):
            raise UiError('Visible message keys are ambiguous; no event was inferred')
        return result

    def send(self, binding, text):
        root = self.select(binding)
        before = {m['key'] for m in self.snapshot(binding, navigate=False)}
        editor = self.find(root, binding['editor'])
        if self.text(editor): raise UiError('An existing draft is present; it was not overwritten')
        editable = editor.get_editable_text_iface()
        if not editable or not self.api.EditableText.set_text_contents(editable, text): raise UiError('Cannot enter text through accessibility')
        if self.text(editor) != text: raise UiError('Draft readback does not match; no send was attempted')
        # Recheck target and login immediately before the one permitted click.
        if not self.ready(): raise UiError('QQ state changed before sending')
        root = self.app(); self.find(root, binding['header'])
        if self.text(self.find(root,binding['target_identity'])).strip()!=binding['id']:
            raise UiError('Visible numeric target changed before sending')
        try:
            self.activate(self.find(root, binding['send']))
            for _ in range(40):
                messages = self.snapshot(binding, navigate=False)
                found = [m for m in messages if m['key'] not in before and m['user_id'] == self.config['self_id'] and m['text'] == text]
                if len(found) == 1: return found[0]['key']
                if len(found) > 1: break
                time.sleep(.1)
        except Exception:
            raise UiUncertain('Send may have reached QQ; it will not be retried automatically') from None
        raise UiUncertain('No unique outgoing UI row appeared; delivery is uncertain and was not retried')

    def inspect(self):
        root = self.app()
        return [{'path': p, 'role': n.get_role_name(), 'name': (n.get_name() or '')[:500],
                 'description': (n.get_description() or '')[:500], 'accessible_id': n.get_accessible_id() or ''}
                for n, p in self.walk(root)]
