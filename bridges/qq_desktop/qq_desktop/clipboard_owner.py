"""Short-lived system clipboard owner; text arrives over stdin, never argv."""
import sys


def main():
    raw = sys.stdin.buffer.read(8193)
    if not raw or len(raw) > 8192:
        raise SystemExit(2)
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        raise SystemExit(2) from None
    if len(text) > 2000:
        raise SystemExit(2)
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gtk, Gdk, GLib
    Gtk.init([])
    clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    clip.set_text(text, -1)
    Gdk.Display.get_default().flush()
    def ready():
        print('READY', flush=True)
        return False
    GLib.idle_add(ready)
    GLib.timeout_add_seconds(10, lambda: (Gtk.main_quit(), False)[1])
    try:
        Gtk.main()
    finally:
        clip.clear()


if __name__ == '__main__':
    main()
