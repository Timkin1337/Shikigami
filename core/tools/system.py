"""Система: консоль, звук, медиа, клавиатура, буфер обмена, блокировка, выключение, сведения о ПК."""
import ctypes
import subprocess
import time
from ctypes import wintypes

import psutil

from . import tool, S, I

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]

# --- ввод с клавиатуры через SendInput ---

INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x2, 0x4
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key(vk, up=False):
    return INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)))


VK = {"ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "enter": 0x0D, "esc": 0x1B,
      "escape": 0x1B, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "home": 0x24,
      "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
      "printscreen": 0x2C, "insert": 0x2D, "capslock": 0x14,
      "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD, "playpause": 0xB3, "next": 0xB0, "prev": 0xB1,
      "stop": 0xB2}
VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})


def press_combo(combo):
    keys = []
    for part in combo.lower().replace(" ", "").split("+"):
        if part in VK:
            keys.append(VK[part])
        elif len(part) == 1:
            vk = user32.VkKeyScanW(ord(part)) & 0xFF
            keys.append(vk)
        else:
            raise ValueError(f"неизвестная клавиша {part}")
    _send(*[_key(k) for k in keys])
    time.sleep(0.03)
    _send(*[_key(k, True) for k in reversed(keys)])


def type_unicode(text):
    for ch in text:
        if ch == "\n":
            press_combo("enter")
            continue
        code = ord(ch)
        down = INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)))
        up = INPUT(INPUT_KEYBOARD, _U(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        _send(down, up)
        time.sleep(0.004)


@tool("press_keys", "Нажать сочетание клавиш в активном окне, например 'ctrl+c', 'alt+tab', 'win+d', 'f5'.",
      {"combo": S("Клавиши через +")}, ["combo"])
def press_keys(combo):
    press_combo(combo)
    return {"ok": True}


@tool("type_text", "Напечатать текст в активное окно (как будто с клавиатуры).", {"text": S("Текст")}, ["text"])
def type_text(text):
    time.sleep(0.2)
    type_unicode(text)
    return {"ok": True, "chars": len(text)}


# --- звук ---

def _endpoint():
    import comtypes
    comtypes.CoInitialize()
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


def get_volume():
    v = _endpoint()
    return {"volume": round(v.GetMasterVolumeLevelScalar() * 100), "muted": bool(v.GetMute())}


@tool("volume", "Громкость системы: узнать, установить (0-100), изменить на величину или выключить/включить звук.",
      {"set": I("Установить уровень 0-100"), "change": I("Изменить на (+10 / -10)"),
       "mute": {"type": "boolean", "description": "true — выключить звук, false — включить"}})
def volume(set=None, change=None, mute=None):
    v = _endpoint()
    if mute is not None:
        v.SetMute(1 if mute else 0, None)
    if set is not None or change is not None:
        cur = v.GetMasterVolumeLevelScalar() * 100
        new = max(0, min(100, set if set is not None else cur + change))
        v.SetMasterVolumeLevelScalar(new / 100, None)
        if new > 0:
            v.SetMute(0, None)
    return get_volume()


@tool("media", "Управление музыкой/видео в любом плеере: play_pause, next, prev, stop. Или status — что сейчас играет.",
      {"action": S("play_pause | next | prev | stop | status",
                   enum=["play_pause", "next", "prev", "stop", "status"])}, ["action"])
async def media(action):
    from .. import media as m
    if action == "status":
        return await m.now_playing() or {"playing": False}
    ok = await m.control(action)
    if not ok:  # плеер не отдаёт сессию — жмём мультимедийную клавишу
        press_combo({"play_pause": "playpause", "next": "next", "prev": "prev", "stop": "stop"}[action])
    return {"ok": True}


# --- консоль ---

@tool("run_powershell", "Выполнить команду PowerShell на ПК и вернуть вывод. Для того, чего нет в других инструментах.",
      {"command": S("Команда PowerShell"), "timeout": I("Секунд на выполнение, по умолчанию 60")}, ["command"],
      danger=True, confirm="Выполнить в PowerShell:\n{command}")
def run_powershell(command, timeout=60):
    wrapped = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
    p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", wrapped],
                       capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    return {"exit_code": p.returncode, "stdout": out[-6000:], "stderr": err[-2000:]}


# --- буфер обмена ---

def clipboard_text():
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        if win32clipboard.IsClipboardFormatAvailable(13):
            return win32clipboard.GetClipboardData(13)
        return ""
    finally:
        win32clipboard.CloseClipboard()


@tool("clipboard", "Прочитать буфер обмена или положить в него текст.",
      {"set_text": S("Если указан — положить этот текст в буфер")})
def clipboard(set_text=None):
    import win32clipboard
    if set_text is not None:
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(13, set_text)
        finally:
            win32clipboard.CloseClipboard()
        return {"ok": True}
    return {"text": clipboard_text()[:6000]}


# --- питание ---

@tool("lock_pc", "Заблокировать компьютер (экран входа).", ask=True, after_reply=True, on_request=True,
      confirm="Заблокировать компьютер?", intent=r"заблокир|блокир\w*\s+(\w+\s+)?(комп|пк|экран|систем|винд)|lock\s+(the\s+)?(pc|computer|screen)")
def lock_pc():
    user32.LockWorkStation()
    return {"ok": True}


@tool("power", "Выключение/перезагрузка/сон ПК, с задержкой в минутах, или отмена таймера выключения.",
      {"action": S("shutdown | restart | sleep | cancel", enum=["shutdown", "restart", "sleep", "cancel"]),
       "minutes": I("Через сколько минут, по умолчанию 0")}, ["action"],
      danger=True, confirm="Питание ПК: {action}?", after_reply=True, on_request=True,
      intent=r"(выключ|выруб|перезагр|перезапуст|ребут)\w*\s*(\w+\s+){0,2}(комп|пк|систем|ноут|винд)|^\s*(лан[ь]?,?\s+)?(выключай|вырубай|выруби|перезагружай)|спящ\w+\s+режим|\bв\s+сон\b|гибернац|отмени\w*\s+выключ|shut\s*down|restart|reboot|go\s+to\s+sleep\s+mode")
def power(action, minutes=0):
    sec = str(int(minutes * 60))
    if action == "shutdown":
        subprocess.run(["shutdown", "/s", "/t", sec])
    elif action == "restart":
        subprocess.run(["shutdown", "/r", "/t", sec])
    elif action == "cancel":
        subprocess.run(["shutdown", "/a"])
    elif action == "sleep":
        subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"])
    return {"ok": True}


# --- сведения о ПК ---

_nv = {"h": None}


def gpu_stats():
    try:
        import pynvml
        if _nv["h"] is None:
            pynvml.nvmlInit()
            _nv["h"] = pynvml.nvmlDeviceGetHandleByIndex(0)
        h = _nv["h"]
        u = pynvml.nvmlDeviceGetUtilizationRates(h)
        m = pynvml.nvmlDeviceGetMemoryInfo(h)
        t = pynvml.nvmlDeviceGetTemperature(h, 0)
        return {"gpu": u.gpu, "vram_used_gb": round(m.used / 2**30, 1), "vram_total_gb": round(m.total / 2**30, 1),
                "gpu_temp": t}
    except Exception:
        return {}


def sys_stats():
    vm = psutil.virtual_memory()
    out = {"cpu": psutil.cpu_percent(None), "ram": vm.percent, "ram_used_gb": round(vm.used / 2**30, 1),
           "ram_total_gb": round(vm.total / 2**30, 1)}
    out.update(gpu_stats())
    b = psutil.sensors_battery()
    if b:
        out["battery"] = round(b.percent)
        out["plugged"] = b.power_plugged
    return out


@tool("system_info", "Загрузка процессора, памяти, видеокарты, батарея, место на дисках, самые прожорливые процессы.")
def system_info():
    psutil.cpu_percent(None)
    time.sleep(0.5)
    s = sys_stats()
    s["disks"] = {}
    for part in psutil.disk_partitions():
        try:
            u = psutil.disk_usage(part.mountpoint)
            s["disks"][part.device] = f"свободно {u.free / 2**30:.0f} из {u.total / 2**30:.0f} ГБ"
        except Exception:
            pass
    procs = sorted(psutil.process_iter(["name", "memory_info"]),
                   key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0, reverse=True)[:6]
    s["top_ram"] = [f"{p.info['name']} {p.info['memory_info'].rss / 2**20:.0f} МБ" for p in procs]
    return s


@tool("screenshot", "Сделать снимок экрана (всех мониторов или одного). Возвращает путь к PNG. "
      "Чтобы показать его пользователю в Telegram — потом вызови send_to_phone с этим путём.",
      {"monitor": I("Номер монитора с 1; не указывать — все мониторы вместе")})
def screenshot(monitor=None):
    from PIL import ImageGrab
    from ..config import DATA
    img = ImageGrab.grab(all_screens=True)
    if monitor:
        mons = _monitors()
        if not 1 <= monitor <= len(mons):
            return {"error": f"мониторов {len(mons)}, нет номера {monitor}"}
        # ImageGrab с all_screens начинает с левого верхнего угла виртуального экрана
        ox, oy = min(m[0] for m in mons), min(m[1] for m in mons)
        x0, y0, x1, y1 = mons[monitor - 1]
        img = img.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy))
    out = DATA / "shots"
    out.mkdir(exist_ok=True)
    for old in sorted(out.glob("*.png"))[:-20]:   # храним последние 20
        old.unlink(missing_ok=True)
    path = out / time.strftime("screen_%Y%m%d_%H%M%S.png")
    img.save(path, optimize=True)
    return {"path": str(path), "size": f"{img.width}x{img.height}"}


def _monitors():
    """Прямоугольники мониторов (left, top, right, bottom), основной первым."""
    rects = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(_h, _dc, r, _l):
        rects.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom))
        return True
    # в физических пикселях, как и снимок Pillow: у мониторов бывает разный масштаб
    prev = user32.SetThreadDpiAwarenessContext(-4)
    try:
        user32.EnumDisplayMonitors(None, None, proc(cb), 0)
    finally:
        user32.SetThreadDpiAwarenessContext(prev)
    rects.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0]))
    return rects
