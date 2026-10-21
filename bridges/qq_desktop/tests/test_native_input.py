from types import SimpleNamespace
import io
import unittest
from unittest.mock import patch

from qq_desktop.atspi import UiError
from qq_desktop.native_input import X11Clipboard


class NativeInputTests(unittest.TestCase):
    def test_held_alt_ctrl_shift_super_and_mouse_are_rejected_without_releasing_them(self):
        d = X11Clipboard.__new__(X11Clipboard)
        for mask in (1, 4, 8, 32, 64, 128, 256, 4096, 0, 2, 16):
            def query(*args): args[-1]._obj.value = mask; return 1
            d.x = SimpleNamespace(XQueryPointer=query, XDefaultRootWindow=lambda _: 1)
            if mask & (1 | 4 | 8 | 32 | 64 | 128 | 0x1f00):
                with self.subTest(mask=mask), self.assertRaises(UiError): d._idle_keyboard(1)
            else: d._idle_keyboard(1)

    def fake_clipboard(self, readback):
        d = X11Clipboard.__new__(X11Clipboard); d.events = []; d.cleared = 0
        d._window = lambda *a: 42; d._idle_keyboard = lambda *a: None
        def focus(display, result, revert): result._obj.value = 42
        d.x = SimpleNamespace(XOpenDisplay=lambda _: 1, XSetInputFocus=lambda *a: None,
                              XSync=lambda *a: None, XGetInputFocus=focus, XCloseDisplay=lambda _: None,
                              XKeysymToKeycode=lambda display, value: 37 if value==0xffe3 else 55)
        def key(*args): d.events.append(args[1:3]); return 1
        d.xt = SimpleNamespace(XTestFakeKeyEvent=key)
        owner=SimpleNamespace(terminate=lambda: setattr(d,'cleared',d.cleared+1),wait=lambda **kw:None)
        def start(text):d.sent_text=text;return owner
        d._clipboard_owner=start
        return d

    def test_unicode_readback_is_independent_and_releases_only_our_keys(self):
        reads = iter(['', '中文\n回执']); d = self.fake_clipboard(None)
        d.paste('中文\n回执', (1, 2, 3, 4), 123, lambda: next(reads))
        self.assertEqual(d.sent_text,'中文\n回执'); self.assertEqual(d.cleared, 1)
        self.assertEqual(d.events, [(37, True), (55, True), (55, False), (55, False), (37, False)])

    def test_clipboard_readback_deadline_stops_the_separate_owner(self):
        d = self.fake_clipboard(None)
        with patch('qq_desktop.native_input.time.monotonic', side_effect=[0, 1, 2, 5]), patch('qq_desktop.native_input.time.sleep'):
            with self.assertRaises(UiError): d.paste('中文', (1, 2, 3, 4), 123, lambda: '')
        self.assertEqual(d.cleared, 1)

    def test_helper_text_uses_stdin_not_arguments_and_startup_timeout_stops_it(self):
        d=X11Clipboard.__new__(X11Clipboard);written=[];stopped=[]
        owner=SimpleNamespace(stdin=SimpleNamespace(write=lambda b:written.append(b),close=lambda:None),
            stdout=io.BytesIO(b'READY\n'),terminate=lambda:stopped.append(True),wait=lambda **kw:None)
        with patch('qq_desktop.native_input.subprocess.Popen',return_value=owner) as launch, patch('qq_desktop.native_input.select.select',return_value=([owner.stdout],[],[])):
            self.assertIs(d._clipboard_owner('私有中文'),owner)
            self.assertNotIn('私有中文',' '.join(launch.call_args.args[0]))
            self.assertEqual(written,[b'\xe7\xa7\x81\xe6\x9c\x89\xe4\xb8\xad\xe6\x96\x87'])
        with patch('qq_desktop.native_input.subprocess.Popen',return_value=owner),patch('qq_desktop.native_input.select.select',return_value=([],[],[])):
            with self.assertRaises(UiError):d._clipboard_owner('测试')
        self.assertEqual(stopped,[True])


if __name__ == '__main__': unittest.main()
