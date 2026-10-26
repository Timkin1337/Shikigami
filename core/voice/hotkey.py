"""Своя кнопка голоса: клавиша или кнопка мыши (боковые «назад/вперёд», колесо), работает и в играх.

Electron видит только клавиатурные сочетания, поэтому здесь низкоуровневые перехваты Windows (WH_KEYBOARD_LL,
WH_MOUSE_LL) в отдельном потоке. Нажатия не глотаются — игра их тоже получает.

config voice.ptt_key: "mouse4" | "mouse5" | "mouse3" | "key:<VK-код>" | "" (выключено)
config voice.ptt_mode: "hold" — пока держите, слушает (отпустили — распознаёт);
                       "press" — нажали, и следующая фраза — команда (как «{name}?» без имени).
"""
import ctypes
import ctypes.wintypes as wt
import threading

from ..config import config

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP = 0x207, 0x208, 0x20B, 0x20C
LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]


class KBD(ctypes.Structure):
    _fields_ = [("vk", wt.DWORD), ("scan", wt.DWORD), ("flags", wt.DWORD), ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


class MSE(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long), ("data", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("extra", ctypes.c_size_t)]


_down = {"on": False}
_capture = {"cb": None}          # режим «нажмите кнопку…» в настройках: следующее нажатие — новая кнопка


def _fire(name, down):
    if _capture["cb"] and down:
        cb, _capture["cb"] = _capture["cb"], None
        cb(name)
        return
    if not name or name != config.get("voice.ptt_key"):
        return
    if down == _down["on"]:          # автоповтор клавиши
        return
    _down["on"] = down
    from . import state
    lst = state.get("listener")
    if not lst:
        return
    if config.get("voice.ptt_mode", "hold") == "hold":
        lst.ptt(down)
    elif down:
        lst.listen_once()


def _kb(n, w, l):
    if n == 0:
        k = ctypes.cast(l, ctypes.POINTER(KBD)).contents
        if w in (WM_KEYDOWN, WM_SYSKEYDOWN, WM_KEYUP, WM_SYSKEYUP):
            _fire(f"key:{k.vk}", w in (WM_KEYDOWN, WM_SYSKEYDOWN))
    return user32.CallNextHookEx(None, n, w, l)


def _ms(n, w, l):
    if n == 0 and w in (WM_MBUTTONDOWN, WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP):
        m = ctypes.cast(l, ctypes.POINTER(MSE)).contents
        if w in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            name = "mouse3"
        else:
            name = "mouse4" if (m.data >> 16) & 0xFFFF == 1 else "mouse5"
        _fire(name, w in (WM_MBUTTONDOWN, WM_XBUTTONDOWN))
    return user32.CallNextHookEx(None, n, w, l)


_procs = [HOOKPROC(_kb), HOOKPROC(_ms)]          # держим ссылки — иначе сборщик мусора убьёт колбэки


def _run():
    h1 = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _procs[0], None, 0)
    h2 = user32.SetWindowsHookExW(WH_MOUSE_LL, _procs[1], None, 0)
    if not h1 or not h2:
        print("кнопка голоса: перехват не установился", flush=True)
    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def start():
    threading.Thread(target=_run, daemon=True, name="voice-hotkey").start()


def capture(timeout=10):
    """Ждать следующего нажатия клавиши или кнопки мыши (Esc — отмена). Возвращает имя или None."""
    ev, out = threading.Event(), {}

    def cb(name):
        out["name"] = None if name == "key:27" else name
        ev.set()
    _capture["cb"] = cb
    ev.wait(timeout)
    _capture["cb"] = None
    return out.get("name")
