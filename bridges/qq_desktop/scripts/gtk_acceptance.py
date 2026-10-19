"""Use actual AT-SPI text input and button clicks on a real isolated GTK window."""
import json
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qq_desktop.atspi import AtspiDesktop,UiError
from qq_desktop.config import validate

p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('gtk_fixture.py'))],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    cfg={'enabled':True,'app_name':'yukino-desktop-fixture','self_id':'10001','identity':{'name':'10001'},'bindings':[]}
    b={'kind':'private','id':'10002','verified':True,'contact':{'name':'Contact 10002'},'header':{'name':'Conversation 10002'},
       'history':{'name':'Messages'},'editor':{'name':'Composer'},'send':{'name':'Send'},'sender':{'path':[0,0]},'body':{'path':[0,1]},
       'row_key':'description','senders':{'Me':'10001','Peer':'10002'}}
    cfg['bindings']=[b];desktop=AtspiDesktop(validate(cfg))
    for _ in range(80):
        if p.poll() is not None:raise RuntimeError('GTK fixture exited')
        if desktop.ready():break
        time.sleep(.1)
    else:
        root=desktop.api.get_desktop(0)
        names=[root.get_child_at_index(i).get_name() for i in range(root.get_child_count())]
        try:
            print(json.dumps(desktop.inspect()[:80]),file=sys.stderr)
            node=desktop.find(desktop.app(),cfg['identity'])
            print('IDENTITY_TEXT',repr(desktop.text(node)),file=sys.stderr)
        except Exception:
            import traceback
            traceback.print_exc()
        raise RuntimeError('Fixture accessibility did not become ready; apps='+repr(names))
    before=desktop.snapshot(b);assert len(before)==1 and before[0]['text']=='existing history',before
    key=desktop.send(b,'中文 desktop input and click')
    after=desktop.snapshot(b);assert len(after)==2 and after[-1]['key']==key and after[-1]['text']=='中文 desktop input and click'
    editor=desktop.find(desktop.app(),b['editor']);desktop.api.EditableText.set_text_contents(editor.get_editable_text_iface(),'do not overwrite this draft')
    try:desktop.send(b,'must not be sent')
    except UiError:pass
    else:raise AssertionError('Existing draft was overwritten')
    assert desktop.text(editor)=='do not overwrite this draft'
    assert len(desktop.snapshot(b))==2
    print(json.dumps({'real_gtk_accessibility':True,'ui_text_readback':True,'single_button_click':True,'outgoing_row_verified':True,'draft_preserved':True,'qq_messages_sent':0}))
finally:
    p.terminate()
    try:p.wait(timeout=5)
    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
