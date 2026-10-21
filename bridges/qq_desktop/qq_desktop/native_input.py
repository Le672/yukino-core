"""Bounded UTF-8 clipboard input to a PID/geometry-verified X11 window.

Only system APIs are used. No QQ internals, browser protocol or injected JS.
The caller must verify the recipient and an empty native editor beforehand.
"""
import ctypes as c
import os
from pathlib import Path
import select
import subprocess
import sys
import time

from .atspi import UiError


class X11Clipboard:
    def __init__(self):
        self.x = c.CDLL('libX11.so.6')
        self.xt = c.CDLL('libXtst.so.6')
        P, U, I, UI = c.c_void_p, c.c_ulong, c.c_int, c.c_uint
        specs = {
            'XOpenDisplay': ([c.c_char_p], P),
            'XDefaultRootWindow': ([P], U),
            'XInternAtom': ([P, c.c_char_p, I], U),
            'XGetWindowProperty': ([P, U, U, c.c_long, c.c_long, I, U,
                                   c.POINTER(U), c.POINTER(I), c.POINTER(U),
                                   c.POINTER(U), c.POINTER(P)], I),
            'XQueryTree': ([P, U, c.POINTER(U), c.POINTER(U), c.POINTER(c.POINTER(U)), c.POINTER(UI)], I),
            'XGetGeometry': ([P, U, c.POINTER(U), c.POINTER(I), c.POINTER(I),
                              c.POINTER(UI), c.POINTER(UI), c.POINTER(UI), c.POINTER(UI)], I),
            'XTranslateCoordinates': ([P,U,U,I,I,c.POINTER(I),c.POINTER(I),c.POINTER(U)], I),
            'XQueryPointer': ([P, U, c.POINTER(U), c.POINTER(U), c.POINTER(I),
                               c.POINTER(I), c.POINTER(I), c.POINTER(I), c.POINTER(UI)], I),
            'XSetInputFocus': ([P, U, I, U], I),
            'XRaiseWindow': ([P,U], I),
            'XKeysymToKeycode': ([P,U], UI),
            'XGetInputFocus': ([P, c.POINTER(U), c.POINTER(I)], I),
            'XSync': ([P, I], I), 'XFree': ([P], I), 'XCloseDisplay': ([P], I),
        }
        for name, (args, result) in specs.items():
            fn = getattr(self.x, name); fn.argtypes = args; fn.restype = result
        self.xt.XTestFakeKeyEvent.argtypes = [P, UI, I, U]
        self.xt.XTestFakeKeyEvent.restype = I
        self.xt.XTestFakeMotionEvent.argtypes = [P, I, I, I, U]
        self.xt.XTestFakeMotionEvent.restype = I
        self.xt.XTestFakeButtonEvent.argtypes = [P, UI, I, U]
        self.xt.XTestFakeButtonEvent.restype = I

    def _pid(self, display, window):
        atom = self.x.XInternAtom(display, b'_NET_WM_PID', True)
        if not atom:
            return None
        kind, count, remaining = c.c_ulong(), c.c_ulong(), c.c_ulong()
        form, data = c.c_int(), c.c_void_p()
        result = self.x.XGetWindowProperty(display, window, atom, 0, 1, False, 0,
                                          c.byref(kind), c.byref(form), c.byref(count),
                                          c.byref(remaining), c.byref(data))
        try:
            if result or form.value != 32 or count.value != 1 or not data:
                return None
            return c.cast(data, c.POINTER(c.c_ulong))[0]
        finally:
            if data:
                self.x.XFree(data)

    def _window(self, display, rect, pid):
        root = self.x.XDefaultRootWindow(display)
        queue, matches, visited = [(root,0)], [], 0
        while queue:
            parent_window, level = queue.pop(0)
            rr, parent, count = c.c_ulong(), c.c_ulong(), c.c_uint()
            children = c.POINTER(c.c_ulong)()
            if not self.x.XQueryTree(display, parent_window, c.byref(rr), c.byref(parent), c.byref(children), c.byref(count)):
                continue
            try:
                visited += count.value
                if visited > 500:
                    raise UiError('Native window traversal budget exceeded')
                windows = [children[i] for i in range(count.value)]
            finally:
                if children:
                    self.x.XFree(children)
            for wid in windows:
                x, y = c.c_int(), c.c_int()
                w, h, border, pixel_depth = (c.c_uint() for _ in range(4))
                ok = self.x.XGetGeometry(display, wid, c.byref(rr), c.byref(x), c.byref(y),
                                         c.byref(w), c.byref(h), c.byref(border), c.byref(pixel_depth))
                child = c.c_ulong()
                ok = ok and self.x.XTranslateCoordinates(display,wid,root,0,0,c.byref(x),c.byref(y),c.byref(child))
                if ok and [x.value, y.value, w.value, h.value] == list(rect) and self._pid(display, wid) == pid:
                    matches.append(wid)
                if level < 4:
                    queue.append((wid,level+1))
        if len(matches) != 1:
            raise UiError('Native QQ window PID/geometry is missing or ambiguous')
        return matches[0]

    def _idle_keyboard(self, display):
        root, child = c.c_ulong(), c.c_ulong()
        rx, ry, wx, wy = (c.c_int() for _ in range(4))
        mask = c.c_uint()
        if not self.x.XQueryPointer(display, self.x.XDefaultRootWindow(display), c.byref(root),
                                   c.byref(child), c.byref(rx), c.byref(ry), c.byref(wx), c.byref(wy), c.byref(mask)):
            raise UiError('Cannot verify the native keyboard state')
        # Caps/Num Lock are harmless here. Never release a user's held modifiers.
        if mask.value & (1 | 4 | 8 | 32 | 64 | 128 | 0x1f00):
            raise UiError('A modifier or mouse button is held; release it in the remote desktop')
        return rx.value, ry.value

    def paste(self, text, rect, pid, readback):
        display = self.x.XOpenDisplay(os.environ.get('DISPLAY', '').encode())
        if not display:
            raise UiError('Cannot open the calibrated X11 display')
        owner = None
        try:
            window = self._window(display, rect, pid)
            self._idle_keyboard(display)
            if readback():
                raise UiError('An existing draft is present; it was not overwritten')
            self.x.XSetInputFocus(display, window, 2, 0)
            self.x.XSync(display, False)
            focus, revert = c.c_ulong(), c.c_int()
            self.x.XGetInputFocus(display, c.byref(focus), c.byref(revert))
            if focus.value != window:
                raise UiError('The native QQ window did not receive keyboard focus')
            ctrl, v = (self.x.XKeysymToKeycode(display, value) for value in (0xffe3, 0x76))
            if not ctrl or not v:
                raise UiError('Native keyboard layout does not expose the paste shortcut')
            # QQ can block its accessibility thread waiting for clipboard data.
            # Serve X11 selection requests in another process, independently of
            # synchronous AT-SPI readback and the bridge's asyncio/UI threads.
            owner = self._clipboard_owner(text)
            try:
                for code, down in ((ctrl, True), (v, True), (v, False)):
                    if not self.xt.XTestFakeKeyEvent(display, code, down, 0):
                        raise UiError('The native paste shortcut was rejected')
            finally:
                self.xt.XTestFakeKeyEvent(display, v, False, 0)
                self.xt.XTestFakeKeyEvent(display, ctrl, False, 0)
                self.x.XSync(display, False)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if readback() == text:
                    return
                time.sleep(.05)
            raise UiError('Draft readback does not match; no send was attempted')
        finally:
            if owner is not None:
                self._stop_owner(owner)
            self.x.XCloseDisplay(display)

    @staticmethod
    def _stop_owner(owner):
        owner.terminate()
        try:
            owner.wait(timeout=2)
        except subprocess.TimeoutExpired:
            owner.kill(); owner.wait(timeout=2)

    def _clipboard_owner(self, text):
        owner = subprocess.Popen([sys.executable, str(Path(__file__).with_name('clipboard_owner.py'))],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            owner.stdin.write(text.encode('utf-8')); owner.stdin.close()
            readable, _, _ = select.select([owner.stdout], [], [], 3)
            if not readable or owner.stdout.readline() != b'READY\n':
                raise UiError('The isolated clipboard helper did not become ready')
            return owner
        except Exception:
            self._stop_owner(owner)
            raise

    def click(self, rect, frame_rect, pid, button=1):
        display = self.x.XOpenDisplay(os.environ.get('DISPLAY', '').encode())
        if not display:
            raise UiError('Cannot open the calibrated X11 display')
        try:
            window=self._window(display, frame_rect, pid)
            self._idle_keyboard(display)
            self.x.XRaiseWindow(display,window)
            self.x.XSetInputFocus(display,window,2,0)
            self.x.XSync(display,False)
            x, y, w, h = rect; fx, fy, fw, fh = frame_rect
            cx, cy = x + w // 2, y + h // 2
            if not (w > 0 and h > 0 and fx <= cx < fx + fw and fy <= cy < fy + fh):
                raise UiError('Native profile control is outside the verified QQ window')
            if not self.xt.XTestFakeMotionEvent(display, -1, cx, cy, 0):
                raise UiError('The native mouse motion was rejected')
            self.x.XSync(display, False)
            if self._idle_keyboard(display) != (cx, cy):
                raise UiError('The native pointer did not reach the verified QQ control')
            if button is None:
                return
            time.sleep(.08)
            try:
                if not self.xt.XTestFakeButtonEvent(display, button, True, 0):
                    raise UiError('The native mouse click was rejected')
                self.x.XSync(display, False)
                time.sleep(.06)
            finally:
                self.xt.XTestFakeButtonEvent(display, button, False, 0)
                self.x.XSync(display, False)
        finally:
            self.x.XCloseDisplay(display)
