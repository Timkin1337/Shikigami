"""Зрение: «посмотри на экран» — снимок экрана или окна уходит модели со зрением, она отвечает, что там.

Снимок уходит в облако (NVIDIA или выбранный провайдер), поэтому зрение — отдельный модуль, выключенный по умолчанию
(config vision.enabled), и снимок делается только по просьбе. Локальная модель со зрением (Ollama/LM Studio) — тот же
путь через провайдера local (vision.provider = "local", vision.model — имя модели).
"""
import asyncio
import base64
import io
import time

import aiohttp

from ..config import config
from . import tool, S

FALLBACK = ["nvidia/nemotron-3.5-super-vl-120b-a12b", "meta/llama-3.2-90b-vision-instruct", "google/gemma-3-12b-it",
            "meta/llama-3.2-11b-vision-instruct"]
PROMPT = ("Это снимок экрана пользователя{where}. Поверх экрана может быть видна аниме-девушка — это сама ассистентка, "
          "её не описывай. Ответь по-русски, по делу, 3–6 предложений: что на экране и что происходит (если игра — что за "
          "игра, сцена, что делает игрок, что видно в интерфейсе: здоровье, задания, карта). Не выдумывай того, чего не видно; "
          "мелкий текст, который не читается, так и назови. Вопрос пользователя: {q}")


def _ours(h):
    import win32process
    import psutil
    try:
        _, pid = win32process.GetWindowThreadProcessId(h)
        return psutil.Process(pid).name().lower() in ("shikigami.exe", "electron.exe")
    except Exception:
        return False


def _target():
    """Окно, о котором спрашивают: активное — если оно не наше. Человек обычно пишет просьбу в чате самой программы,
    и тогда активна она; раньше в этом случае снимался основной монитор целиком, а игра была на другом экране.
    Тогда берём верхнее по z-порядку чужое окно — то, на которое человек смотрел перед этим."""
    import win32gui
    h = win32gui.GetForegroundWindow()
    if h and not _ours(h):
        return h
    found = []

    def cb(w, _):
        if found or not win32gui.IsWindowVisible(w) or win32gui.IsIconic(w) or not win32gui.GetWindowText(w):
            return
        if win32gui.GetClassName(w) in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd") or _ours(w):
            return
        x0, y0, x1, y1 = win32gui.GetWindowRect(w)
        if x1 - x0 > 300 and y1 - y0 > 200:
            found.append(w)
    win32gui.EnumWindows(cb, None)              # сверху вниз по z-порядку
    return found[0] if found else h


def _grab(what):
    from PIL import ImageGrab
    import win32api
    import win32gui
    h = _target()
    title = win32gui.GetWindowText(h)[:80] if h else ""
    if what == "window" and h:
        x0, y0, x1, y1 = win32gui.GetWindowRect(h)
        if x1 - x0 > 200 and y1 - y0 > 150:
            return ImageGrab.grab(bbox=(x0, y0, x1, y1), all_screens=True), title
    if h:                                       # «весь экран» — монитор, где это окно, а не всегда основной
        mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(h, 2))["Monitor"]
        return ImageGrab.grab(bbox=tuple(mon), all_screens=True), title
    return ImageGrab.grab(), ""


def _jpeg(img, side=1280):
    img = img.convert("RGB")
    if max(img.size) > side:
        k = side / max(img.size)
        img = img.resize((round(img.width * k), round(img.height * k)))
    b = io.BytesIO()
    img.save(b, "JPEG", quality=82)
    return base64.b64encode(b.getvalue()).decode()


IMAGE_PROMPT = ("Пользователь прислал эту картинку в чат. Ответь по-русски, по делу, 3–8 предложений: что на ней, текст на ней "
                "(если читается — дословно), детали, важные для вопроса. Не выдумывай того, чего не видно. Вопрос пользователя: {q}")


async def describe(img, question, title="", prompt=None):
    from .. import llm
    name = config.get("vision.provider", "nvidia")
    p = llm.provider(name)
    key = config.secret(p.get("api_key_env", ""))
    models = [config.get("vision.model")] if config.get("vision.model") else []
    models += [m for m in FALLBACK if m not in models] if name == "nvidia" else []
    data = _jpeg(img)
    text = (prompt or PROMPT).format(where=f" (окно «{title}»)" if title else "", q=question or "что на экране?")
    content = [{"type": "text", "text": text},
               {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}]
    errors = []
    for m in models or [p.get("model")]:
        body = {"model": m, "max_tokens": 600, "temperature": 0.2, "messages": [{"role": "user", "content": content}]}
        if "nemotron" in m:          # иначе модель «думает» весь лимит токенов и отвечает пустотой (24.10: 2300 символов мыслей, 0 ответа)
            body["chat_template_kwargs"] = {"thinking": False}
        t0 = time.time()
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40 if m == models[0] else 25)) as s:
                async with s.post(p["base_url"] + "/chat/completions", json=body,
                                  headers={"Authorization": f"Bearer {key}"} if key else {}) as r:
                    if r.status != 200:
                        errors.append(f"{m}: HTTP {r.status}")
                        continue
                    text = ((await r.json())["choices"][0]["message"].get("content") or "").strip()
                    if text:
                        return {"seen": text, "model": m, "seconds": round(time.time() - t0, 1)}
        except Exception as e:
            errors.append(f"{m}: {type(e).__name__}")
    return {"error": "модель со зрением не ответила: " + "; ".join(errors)[:300]}


@tool("look_at_screen", "Посмотреть на экран пользователя и рассказать, что там: игра, окно, ошибка, картинка. "
      "what=window — активное окно (по умолчанию), screen — весь экран. Снимок уходит модели со зрением.",
      {"question": S("Что спросил пользователь про экран (например, «что у меня в игре?», «оцени билд»)"),
       "what": S("window | screen", enum=["window", "screen"])}, [], danger=False, on_request=True)
async def look_at_screen(question="", what="window"):
    if not config.get("vision.enabled", False):
        return {"error": "зрение выключено: его включают во вкладке «Модули» → «Зрение» (снимок экрана уходит в облако "
                         "только по просьбе). Скажи пользователю, как включить."}
    img, title = await asyncio.to_thread(_grab, what)
    return await describe(img, question, title)
