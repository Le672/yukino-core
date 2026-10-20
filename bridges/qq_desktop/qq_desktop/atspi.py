"""Only operating-system accessibility APIs; no injection, CDP or QQ private API."""
import hashlib
import re
import time
from .config import validate,numeric,selector


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
            source = {'accessible_id': row.get_accessible_id, 'description': row.get_description, 'name': row.get_name}[binding['row_key']]()
            if not source: raise UiError('Stable visible message key is missing; calibrate row_key')
            notice=binding.get('notices',{})
            if notice.get('verified'):
                body=self.find(row,binding['body']);kind_node=self.find(row,notice['row_type'])
                if kind_node==body:raise UiError('A message body cannot be a system notice kind field')
                kind=self.text(kind_node).strip()
                if kind not in ('message','poke'):raise UiError('Unknown system row kind; recalibrate notice fields')
                if kind=='poke':
                    actor=self.find(row,notice['actor_id']);target=self.find(row,notice['target_id'])
                    if actor in (body,kind_node) or target in (body,kind_node,actor):
                        raise UiError('Notice identities must be separate from user message text')
                    uid=self.text(actor).strip();target_id=self.text(target).strip()
                    if not all(re.fullmatch(r'[1-9][0-9]{3,19}',v) for v in (uid,target_id)):
                        raise UiError('Poke requires genuine numeric actor and target identities')
                    if binding['kind']=='private' and any(v not in (binding['id'],self.config['self_id']) for v in (uid,target_id)):
                        raise UiError('Poke identities do not belong to the verified private conversation')
                    key=hashlib.sha256((source+'\0poke\0'+uid+'\0'+target_id).encode()).hexdigest()
                    result.append({'key':key,'notice':'poke','user_id':uid,'target_id':target_id,'text':''})
                    continue
            uid = self.text(self.find(row, binding['sender_id'])).strip()
            if not re.fullmatch(r'[1-9][0-9]{3,19}',uid):
                raise UiError('A genuine visible numeric sender field is required; nicknames are not identities')
            if binding['kind']=='private' and uid not in (binding['id'],self.config['self_id']):
                raise UiError('Visible sender does not belong to the verified private conversation')
            text = self.text(self.find(row, binding['body']))
            attachment=None
            if 'attachments' in binding:
                m=binding['attachments'];kind=self.text(self.find(row,m['row_kind'])).strip()
                if kind not in ('text','image','file','video'):raise UiError('Unknown attachment kind; recalibrate the genuine row kind field')
                if kind!='text':
                    attachment={'kind':kind,'name':self.text(self.find(row,m['row_name'])).strip()}
                    if not attachment['name']:raise UiError('Attachment has no visible filename')
                    if 'row_path' in m:attachment['path']=self.text(self.find(row,m['row_path'])).strip()
            signature='' if attachment is None else attachment['kind']+'\0'+attachment['name']
            key = hashlib.sha256((source + '\0' + uid + '\0' + text+'\0'+signature).encode()).hexdigest()
            message={'key': key, 'user_id': uid, 'text': text}
            if attachment:message['attachment']=attachment
            result.append(message)
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

    def send_attachment(self,binding,record):
        m=binding.get('attachments',{})
        if not m.get('verified') or record['kind'] not in m.get('kinds',[]):raise UiError('Attachment flow is not calibrated for this conversation')
        root=self.select(binding)
        if self.text(self.find(root,binding['editor'])):raise UiError('An existing draft is present; it was not overwritten')
        before={r['key'] for r in self.snapshot(binding,navigate=False)}
        self.activate(self.find(root,m['button']))
        dialog=None
        for _ in range(40):
            try:dialog=self.find(self.app(),m['dialog']);break
            except UiError:time.sleep(.05)
        if dialog is None:raise UiError('Calibrated file chooser did not open; no file was accepted')
        entry=self.find(dialog,m['path']);editable=entry.get_editable_text_iface()
        if not editable or not self.api.EditableText.set_text_contents(editable,record['path']):raise UiError('File chooser rejected the media path')
        if self.text(entry)!=record['path']:raise UiError('File path readback changed; no file was accepted')
        # Accept may send immediately in some clients. Everything after it is
        # considered uncertain until a unique outgoing attachment is observed.
        if not self.ready():raise UiError('QQ identity changed while choosing a file')
        if self.text(self.find(self.app(),binding['target_identity'])).strip()!=binding['id']:raise UiError('Target changed while choosing a file')
        try:
            self.activate(self.find(dialog,m['accept']))
            if 'final_send' in m:
                control=None
                for _ in range(40):
                    try:control=self.find(self.app(),m['final_send']);break
                    except UiError:time.sleep(.05)
                if control is None:raise UiError('Attachment confirmation is missing')
                if not self.ready() or self.text(self.find(self.app(),binding['target_identity'])).strip()!=binding['id']:raise UiError('Identity changed before attachment confirmation')
                self.activate(control)
            for _ in range(60):
                rows=self.snapshot(binding,navigate=False)
                found=[r for r in rows if r['key'] not in before and r['user_id']==self.config['self_id']
                       and r.get('attachment',{}).get('kind')==record['kind']
                       and r.get('attachment',{}).get('name')==record['name']]
                if len(found)==1:return found[0]['key']
                if len(found)>1:break
                time.sleep(.1)
        except Exception:raise UiUncertain('Attachment may have been submitted; it was not resent') from None
        raise UiUncertain('No unique outgoing attachment appeared; it was not resent')

    def inspect(self):
        root = self.app()
        result=[]
        for n,p in self.walk(root):
            try:value=self.text(n)[:500]
            except Exception:value=''
            result.append({'path':p,'role':n.get_role_name(),'name':(n.get_name() or '')[:500],
                'text':value,'description':(n.get_description() or '')[:500],'accessible_id':n.get_accessible_id() or ''})
        return result

    def diagnose(self):
        """Read-only checks. No login, conversation navigation or send actions."""
        checks=[]
        def add(code,ok,message,next_step=''):
            checks.append({'code':code,'ok':ok,'message':message,'next_step':next_step})
        try:root=self.app();nodes=list(self.walk(root))
        except UiError:
            add('application',False,'未找到唯一的 QQ 可访问窗口','检查官方 QQ 是否运行，以及 app_name 是否与控件树一致。')
            return {'checks':checks,'identity_ready':False,'no_qq_message_sent':True}
        add('application',True,'QQ 窗口可以读取')
        blocked=any(n.get_name() in self.config['blocked_labels'] for n,_ in nodes)
        add('login_screen',not blocked,'检测到登录或安全验证界面' if blocked else '未发现已配置的登录阻断标记',
            '账号可用后，在远程桌面手动处理；适配器不会尝试登录。' if blocked else '')
        identity=False
        try:
            identity=bool(self.config.get('self_id')) and self.text(self.find(root,self.config['identity'])).strip()==self.config['self_id']
        except (UiError,KeyError):pass
        add('self_identity',identity,'本人数字身份匹配' if identity else '尚未核验本人数字身份',
            '' if identity else '登录后校准显示本人 QQ 号的独立控件，昵称不能代替。')
        return {'checks':checks,'identity_ready':identity and not blocked,'visible_nodes':len(nodes),'no_qq_message_sent':True}

    def probe(self,config,binding=None):
        """Read the currently visible conversation; never navigate or send."""
        original=self.config
        try:
            # A new binding is intentionally unverified. Read-only calibration
            # must not require the marks that this very probe is establishing.
            candidate=validate({**config,'enabled':False})
            numeric(candidate.get('self_id'));candidate['identity']=selector(candidate.get('identity'))
            self.config={**candidate,'enabled':True}
            if not self.ready():raise UiError('The visible self QQ identity is missing or mismatched')
            rows=[] if binding is None else self.snapshot(binding,navigate=False)
            return {'identity_verified':True,'visible_rows':len(rows),'numeric_senders_verified':binding is not None,
                    'visible_poke_rows':sum(r.get('notice')=='poke' for r in rows)}
        finally:self.config=original
