"""Окна рабочего стола для аватара: по каким краям можно ходить и сидеть, за какое окно спрятаться.

Координаты — физические пиксели (поток переключается в режим per-monitor DPI), оболочка Electron переводит их
в свои единицы (screenToDipRect): например, экран ноутбука на 125 %, мониторы на 100 %.
Рамки — видимые (DWMWA_EXTENDED_FRAME_BOUNDS): обычный GetWindowRect на Windows 10/11 захватывает невидимую
рамку для растягивания, и {name} садилась бы на 7 пикселей выше заголовка.
"""
import asyncio
import ctypes
import ctypes.wintypes as wt

from aiohttp import web

routes = web.RouteTableDef()
user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
AVATAR_TITLE = "Shikigami — аватар"
GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x80, 0x8


class _Dpi:
    """Внутри блока — координаты в физических пикселях независимо от настроек процесса."""
    def __enter__(self):
        self.prev = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))

    def __exit__(self, *a):
        user32.SetThreadDpiAwarenessContext(self.prev)


def _cloaked(h):
    v = ctypes.c_int(0)
    dwm.DwmGetWindowAttribute(wt.HWND(h), 14, ctypes.byref(v), ctypes.sizeof(v))     # DWMWA_CLOAKED
    return v.value != 0


def _rect(h):
    r = wt.RECT()
    if dwm.DwmGetWindowAttribute(wt.HWND(h), 9, ctypes.byref(r), ctypes.sizeof(r)) != 0:   # видимая рамка
        user32.GetWindowRect(wt.HWND(h), ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def _title(h):
    # InternalGetWindowText не шлёт окну WM_GETTEXT: GetWindowTextW ждал ответа от каждого чужого окна,
    # и одно занятое (его как раз тащат) задерживало весь перебор до секунды
    b = ctypes.create_unicode_buffer(512)
    user32.InternalGetWindowText(wt.HWND(h), b, 512)
    return b.value


def _info(h):
    import win32gui
    if not win32gui.IsWindow(h):
        return None
    x, y, w, hh = _rect(h)
    placement = win32gui.GetWindowPlacement(h)[1]
    return {"hwnd": h, "title": _title(h), "x": x, "y": y, "w": w, "h": hh,
            "visible": bool(win32gui.IsWindowVisible(h)) and not _cloaked(h),
            "minimized": bool(win32gui.IsIconic(h)), "maximized": placement == 3,
            "topmost": bool(win32gui.GetWindowLong(h, GWL_EXSTYLE) & WS_EX_TOPMOST)}


def windows():
    """Видимые окна сверху вниз (по z-порядку): на чьём краю можно стоять и кто кого закрывает."""
    import win32gui
    out = []

    def cb(h, _):
        if not win32gui.IsWindowVisible(h) or win32gui.IsIconic(h) or _cloaked(h):
            return True
        if win32gui.GetWindowLong(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        t = _title(h)
        if not t or t in ("Program Manager", AVATAR_TITLE):
            return True
        x, y, w, hh = _rect(h)
        if w < 160 or hh < 100:
            return True
        out.append({"hwnd": h, "title": t[:80], "x": x, "y": y, "w": w, "h": hh,
                    "maximized": win32gui.GetWindowPlacement(h)[1] == 3})
        return True
    with _Dpi():
        win32gui.EnumWindows(cb, None)
    return out


def avatar_hwnd():
    import win32gui
    return win32gui.FindWindow(None, AVATAR_TITLE)


@routes.get("/api/desktop/windows")
async def api_windows(_):
    return web.json_response(await asyncio.to_thread(windows))     # не держим цикл событий: окно аватара опрашивает часто


@routes.get("/api/desktop/window/{hwnd}")
async def api_window(r):
    with _Dpi():
        return web.json_response(_info(int(r.match_info["hwnd"])) or {"gone": True})


@routes.post("/api/desktop/behind")
async def api_behind(r):
    """Поставить окно аватара сразу под указанным окном (оболочка перед этим снимает «поверх всех»)."""
    import win32con
    import win32gui
    target = int((await r.json()).get("hwnd", 0))
    me = avatar_hwnd()
    if not me or not target or not win32gui.IsWindow(target):
        return web.json_response({"ok": False})
    flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE
    win32gui.SetWindowPos(me, target, 0, 0, 0, 0, flags)
    return web.json_response({"ok": True})
