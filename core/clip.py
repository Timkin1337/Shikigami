"""История буфера обмена и текст с экрана (OCR).

- История: последние 60 текстов, закреплённые не вытесняются. Пароли не сохраняем: менеджеры паролей помечают их
  форматом ExcludeClipboardContentFromMonitorProcessing, а строки, похожие на ключи и токены, отбрасываем сами.
- OCR: выделили область (Ctrl+Alt+O, оболочка рисует рамку) → встроенный в Windows распознаватель (без интернета,
  русский и английский) → текст в буфер.
"""
import asyncio
import json
import re
import threading
import time

from aiohttp import web

from . import bus
from .config import DATA

routes = web.RouteTableDef()
SAVE = DATA / "clipboard.json"
MAX = 60
hist = {"items": []}            # {text, at, pinned}
_lock = threading.Lock()
SECRET = re.compile(r"^(sk-|ghp_|gho_|github_pat_|xox[bp]-|eyJ[\w-]{10,}\.|AKIA|AIza|-----BEGIN)|^[A-Za-z0-9+/=_\-]{24,}$")


def _load():
    try:
        hist["items"] = json.loads(SAVE.read_text(encoding="utf-8"))
    except Exception:
        hist["items"] = []


def _save():
    SAVE.write_text(json.dumps(hist["items"], ensure_ascii=False), encoding="utf-8")


def _read_clip():
    import win32clipboard as cb
    import win32con
    excl = cb.RegisterClipboardFormat("ExcludeClipboardContentFromMonitorProcessing")
    for _ in range(5):
        try:
            cb.OpenClipboard()
            break
        except Exception:
            time.sleep(0.05)
    else:
        return None
    try:
        if cb.IsClipboardFormatAvailable(excl):
            return None
        if cb.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return cb.GetClipboardData(win32con.CF_UNICODETEXT)
    finally:
        cb.CloseClipboard()
    return None


def set_clip(text):
    import win32clipboard as cb
    import win32con
    cb.OpenClipboard()
    try:
        cb.EmptyClipboard()
        cb.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        cb.CloseClipboard()


def _add(text):
    t = text.strip("\x00")
    if not t.strip() or len(t) > 20000 or SECRET.search(t.strip()):
        return
    with _lock:
        items = [i for i in hist["items"] if i["text"] != t]
        pinned = next((i["pinned"] for i in hist["items"] if i["text"] == t), False)
        items.insert(0, {"text": t, "at": time.time(), "pinned": pinned})
        keep, free = [], 0
        for i in items:
            if i["pinned"] or free < MAX:
                keep.append(i)
                free += 0 if i["pinned"] else 1
        hist["items"] = keep
        _save()
    bus.emit("clip.changed")


_skip_until = 0.0


def copy_selection():
    """Выделенный текст активного окна — для «прочитай вслух» (Ctrl+Alt+T). Копируем Ctrl+C и сразу возвращаем
    прежний буфер: человек не должен заметить, что в буфере что-то побывало; в историю буфера это не пишем."""
    global _skip_until
    import ctypes
    u = ctypes.windll.user32
    for _ in range(40):                  # ждём, пока отпустят Ctrl/Alt горячей клавиши — иначе Alt откроет меню окна
        if not any(u.GetAsyncKeyState(vk) & 0x8000 for vk in (0x11, 0x12, 0x10)):
            break
        time.sleep(0.025)
    _skip_until = time.time() + 2.5
    old = _read_clip()
    seq0 = u.GetClipboardSequenceNumber()
    u.keybd_event(0x11, 0, 0, 0); u.keybd_event(0x43, 0, 0, 0)
    u.keybd_event(0x43, 0, 2, 0); u.keybd_event(0x11, 0, 2, 0)
    for _ in range(25):
        time.sleep(0.03)
        if u.GetClipboardSequenceNumber() != seq0:
            break
    else:
        return ""                        # буфер не изменился — ничего не выделено
    time.sleep(0.05)
    text = _read_clip() or ""
    if old is not None:
        try:
            set_clip(old)
        except Exception:
            pass
    return text.strip()


def _watch():
    import ctypes
    seq = ctypes.windll.user32.GetClipboardSequenceNumber
    last = seq()
    while True:
        time.sleep(0.7)
        n = seq()
        if n == last:
            continue
        last = n
        if time.time() < _skip_until:     # это «прочитай выделенное» — не история человека
            continue
        try:
            t = _read_clip()
            if t:
                _add(t)
        except Exception:
            pass


def start():
    _load()
    threading.Thread(target=_watch, daemon=True, name="clipboard").start()


# ---------------- OCR ----------------
async def ocr_image(img):
    """PIL-картинка → текст встроенным распознавателем Windows (языки профиля: русский, английский)."""
    import io
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    if img.width < 40 or img.height < 20:                    # мелкий текст распознаётся лучше увеличенным
        img = img.resize((img.width * 3, img.height * 3))
    elif img.height < 200:
        img = img.resize((img.width * 2, img.height * 2))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "PNG")
    stream = InMemoryRandomAccessStream()
    w = DataWriter(stream)
    w.write_bytes(buf.getvalue())
    await w.store_async()
    stream.seek(0)
    bmp = await (await BitmapDecoder.create_async(stream)).get_software_bitmap_async()
    eng = OcrEngine.try_create_from_user_profile_languages()
    res = await eng.recognize_async(bmp)
    return "\n".join(l.text for l in res.lines)


@routes.post("/api/ocr")
async def api_ocr(r):
    """Область экрана в физических пикселях → текст (и сразу в буфер)."""
    b = await r.json()
    x, y, w, h = (int(b[k]) for k in ("x", "y", "w", "h"))
    if w < 4 or h < 4:
        return web.json_response({"error": "слишком маленькая область"})
    from PIL import ImageGrab
    img = await asyncio.to_thread(ImageGrab.grab, bbox=(x, y, x + w, y + h), all_screens=True)
    try:
        text = (await ocr_image(img)).strip()
    except Exception as e:
        return web.json_response({"error": f"распознавание: {e}"})
    if text:
        await asyncio.to_thread(set_clip, text)
        short = text if len(text) < 140 else text[:140] + "…"
        bus.emit("speech", text=f"Скопировала текст: {short}", url=None, emotion="happy")
    else:
        bus.emit("speech", text="Не вижу там текста.", url=None, emotion="sad")
    return web.json_response({"text": text})


# ---------------- история ----------------
@routes.get("/api/clip")
async def api_list(_):
    return web.json_response(hist["items"])


@routes.post("/api/clip/{act}")
async def api_act(r):
    act = r.match_info["act"]
    b = await r.json() if r.can_read_body else {}
    with _lock:
        items = hist["items"]
        i = b.get("i")
        if act == "copy" and i is not None and 0 <= i < len(items):
            await asyncio.to_thread(set_clip, items[i]["text"])
        elif act == "pin" and i is not None and 0 <= i < len(items):
            items[i]["pinned"] = not items[i]["pinned"]
        elif act == "delete" and i is not None and 0 <= i < len(items):
            items.pop(i)
        elif act == "clear":
            hist["items"] = [x for x in items if x["pinned"]]
        _save()
    return web.json_response(hist["items"])


# ---------------- инструмент для ИИ ----------------
from .tools import tool, S  # noqa: E402


@tool("read_screen", "Прочитать текст с экрана распознаванием (OCR): активное окно (window) или весь экран (screen). "
      "Для «что тут написано», «переведи, что на экране», «прочитай ошибку».",
      {"what": S("window | screen", enum=["window", "screen"])}, [])
async def read_screen(what="window"):
    from PIL import ImageGrab
    bbox = None
    if what == "window":
        from .desktop import _Dpi, _rect
        import win32gui
        with _Dpi():
            x, y, w, h = _rect(win32gui.GetForegroundWindow())
        bbox = (x, y, x + w, y + h)
    img = await asyncio.to_thread(ImageGrab.grab, bbox=bbox, all_screens=True)
    text = (await ocr_image(img)).strip()
    return {"text": text[:6000] or "(текста не видно)", "truncated": len(text) > 6000}
