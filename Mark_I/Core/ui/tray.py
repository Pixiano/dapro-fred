"""System tray icon for Mark I: Show HUD (left click), Restart, Quit. Ported from the old FRED's pill_app tray. The pill is click-through, so this is the way to open the HUD and to quit.

Sockets
  in : Tray(on_show, on_restart, on_quit).start() / stop()      each callback takes no arguments and runs on the tray thread
  out: a notification-area icon "Mark I". A missing pystray or tray backend prints one line and Mark I carries on without it.
Self-check: python ui/tray.py (builds the icon and menu, never shows it)
"""
import threading


def _image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([6, 6, 58, 58], fill=(18, 22, 30, 255))
    d.ellipse([20, 26, 44, 38], fill=(90, 200, 255, 255))
    return img


class Tray:
    def __init__(self, on_show, on_restart, on_quit):
        self.on_show, self.on_restart, self.on_quit, self.icon = on_show, on_restart, on_quit, None

    def build(self):
        import pystray
        item = pystray.MenuItem
        return pystray.Icon("Mark I", _image(), "Mark I - hold Left Ctrl+Alt",
                            menu=pystray.Menu(item("Show HUD", lambda: self.on_show(), default=True),      # default = a plain left click
                                              item("Restart Mark I", lambda: self.on_restart()),
                                              item("Quit Mark I", lambda: self.on_quit())))

    def start(self):
        try:
            self.icon = self.build()
            threading.Thread(target=self.icon.run, daemon=True, name="tray").start()
        except Exception as ex:
            print(f"[tray] unavailable ({type(ex).__name__}): Ctrl+C quits")

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


def _selfcheck():
    calls = []
    t = Tray(lambda: calls.append("show"), lambda: calls.append("restart"), lambda: calls.append("quit"))
    icon = t.build()
    items = list(icon.menu.items)
    assert [i.text for i in items] == ["Show HUD", "Restart Mark I", "Quit Mark I"] and items[0].default and icon.title.startswith("Mark I")
    for i in items:
        i(icon)
    assert calls == ["show", "restart", "quit"]
    print("tray ok")


if __name__ == "__main__":
    _selfcheck()
