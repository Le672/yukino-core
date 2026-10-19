"""Controlled local desktop fixture: it never connects to QQ or any account."""
import gi
gi.require_version('Gtk','3.0')
from gi.repository import Gtk,GLib
GLib.set_prgname('yukino-desktop-fixture');GLib.set_application_name('YukinoDesktopFixture')
window=Gtk.Window(title='Yukino Desktop Fixture');window.set_default_size(700,500)
box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8);window.add(box)
box.pack_start(Gtk.Label(label='10001'),False,False,0)
contact=Gtk.Button(label='Contact 10002');box.pack_start(contact,False,False,0)
box.pack_start(Gtk.Label(label='Conversation 10002'),False,False,0)
history=Gtk.ListBox();history.get_accessible().set_name('Messages');box.pack_start(history,True,True,0)
editor=Gtk.Entry();editor.get_accessible().set_name('Composer');box.pack_start(editor,False,False,0)
send=Gtk.Button(label='Send');box.pack_start(send,False,False,0)
counter=0
def append(sender,text):
    global counter
    counter+=1;row=Gtk.ListBoxRow();row.get_accessible().set_description('message:'+str(counter))
    content=Gtk.Box(orientation=Gtk.Orientation.VERTICAL);content.pack_start(Gtk.Label(label=sender),False,False,0);content.pack_start(Gtk.Label(label=text),False,False,0)
    row.add(content);history.add(row);history.show_all()
def on_send(button):
    text=editor.get_text()
    if text:append('Me',text);editor.set_text('')
send.connect('clicked',on_send);window.connect('destroy',Gtk.main_quit)
append('Peer','existing history');window.show_all();Gtk.main()
