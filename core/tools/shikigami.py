"""Возможности самой программы для ИИ: занятия аватара, фото, перекраска, тема, видео вместе, чтение выделенного,
экран, модули и модель ИИ. Без них модель на «сделай фото» снимала экран, а на «тему сакура» искала модель «sakura»."""
import asyncio
import re

from .. import bus
from ..config import config
from . import tool, S, I, confirm

ACTIVITIES = {"book": "читать книгу", "tea": "пить чай", "phone": "сидеть в телефоне", "hum": "напевать",
              "juggle": "играть с мячиком", "yawn": "зевнуть", "butterfly": "следить за бабочкой", "fan": "обмахиваться веером",
              "confetti": "конфетти (праздник)"}
THEMES = {"": "Тёплый вечер (по умолчанию)", "night_fox": "Ночной неон", "sakura": "Сакура в сумерках", "matcha": "Матча", "tea": "Чайная"}
COLORS = {"розов": "#ff7eb6", "красн": "#d8364a", "алый": "#e4572e", "рыж": "#e07a2c", "оранж": "#f08a24", "жёлт": "#ffd23f", "желт": "#ffd23f",
          "золот": "#e0b040", "зелён": "#3fae5a", "зелен": "#3fae5a", "мятн": "#7ad7b0", "голуб": "#7ab8ff", "син": "#3a6fd8",
          "фиолет": "#8a5ad8", "сирен": "#b48ae0", "бел": "#f2f0ee", "сер": "#9a9aa2", "черн": "#26222a", "чёрн": "#26222a",
          "коричн": "#7a4a2a", "каштан": "#6a3a24", "бирюз": "#2ab0b0", "лаванд": "#b9a6e8"}


@tool("avatar_activity", "Аватар занимается чем-то на рабочем столе: " + ", ".join(f"{k} — {v}" for k, v in ACTIVITIES.items()) + ".",
      {"what": S("Что делать", enum=list(ACTIVITIES))}, ["what"], danger=False)
def avatar_activity(what):
    bus.emit("avatar.pastime", name=what, force=True)
    return {"ok": True, "doing": ACTIVITIES.get(what, what)}


@tool("friendship_status", "Уровень дружбы с пользователем: название уровня, очки и сколько до следующего. "
      "Для «какой у нас уровень дружбы», «мы друзья?».", {}, [], danger=False)
def friendship_status():
    from .. import friendship
    return friendship.status()


@tool("breathe_with_me", "Подышать вместе с пользователем по схеме 4-7-8 (вдох 4 с, задержка 7 с, выдох 8 с): аватар дышит и "
      "показывает счёт облачком. Для «давай подышим», «успокой меня», «я нервничаю».",
      {"cycles": I("Сколько кругов по 19 секунд, 1–8, обычно 4")}, [], danger=False, on_request=True)
def breathe_with_me(cycles=4):
    cycles = max(1, min(8, int(cycles or 4)))
    bus.emit("avatar.breathe", cycles=cycles)
    return {"ok": True, "cycles": cycles, "seconds": cycles * 19,
            "note": "подсказки идут облачком над аватаром; скажи коротко и спокойно, что дышим вместе"}


@tool("avatar_photo", "Фоторежим: аватар встаёт в позу и сохраняет свой снимок PNG с прозрачным фоном в «Изображения\\Shikigami». "
      "Это фото персонажа, а не снимок экрана.",
      {"pose": S("Поза", enum=["", "wave", "bow", "happy", "shy", "think", "surprised", "angry"]),
       "emotion": S("Лицо", enum=["", "happy", "shy", "angry", "sad", "surprised"])}, [], danger=False, on_request=True)
def avatar_photo(pose="", emotion=""):
    bus.emit("avatar.photo", pose=pose or None, emotion=emotion or None)
    return {"ok": True, "saved_to": "папка «Изображения\\Shikigami» на этом ПК",
            "note": "в окне программы появится уведомление с именем файла (не в Telegram)"}


def _color(c):
    c = (c or "").strip().lower()
    if re.fullmatch(r"#?[0-9a-f]{6}", c):
        return "#" + c.lstrip("#")
    return next((v for k, v in COLORS.items() if k in c), None)


@tool("avatar_recolor", "Перекрасить надетую модель: волосы (hair), глаза (eyes) или одежду (clothes). Цвет — название по-русски "
      "или #RRGGBB; пустой цвет — вернуть исходный. Файл модели не меняется.",
      {"part": S("Что", enum=["hair", "eyes", "clothes"]), "color": S("Цвет: «розовый», «#ff7eb6» или пусто для сброса")},
      ["part"], danger=False, on_request=True)
def avatar_recolor(part, color=""):
    from .avatar import current_model
    name = current_model()
    if not name:
        return {"error": "модель не надета"}
    hexc = _color(color) if color else ""
    if color and not hexc:
        return {"error": f"не знаю цвет «{color}» — назови проще или дай #RRGGBB"}
    config.update({"avatar": {"per_model": {name: {"tint": {part: hexc}}}}})
    bus.emit("config.changed")
    return {"ok": True, "part": part, "color": hexc or "исходный",
            "note": "у моделей не из VRoid часть может не найтись — тогда цвет не изменится"}


@tool("set_theme", "Сменить тему оформления окна программы: " + ", ".join(f"{k or 'default'} — {v}" for k, v in THEMES.items()) + ".",
      {"theme": S("Тема", enum=["default", *[k for k in THEMES if k]])}, ["theme"], danger=False, on_request=True)
def set_theme(theme):
    config.update({"ui": {"theme": "" if theme == "default" else theme}})
    bus.emit("config.changed")
    return {"ok": True, "theme": THEMES.get("" if theme == "default" else theme, theme)}


@tool("watch_video_together", "Аватар садится смотреть видео вместе с пользователем (если сейчас играет видео в браузере или плеере).",
      {}, [], danger=False, on_request=True)
async def watch_video_together():
    from .. import media
    m = await media.now_playing()
    if not m or not m.get("playing"):
        return {"error": "сейчас ничего не играет — включите видео, и она сядет смотреть"}
    bus.emit("avatar.watch")
    return {"ok": True, "title": m.get("title")}


@tool("read_selection_aloud", "Прочитать вслух текст, выделенный сейчас в активном окне (то же, что Ctrl+Alt+T). "
      "Текст уже будет произнесён — ответь потом коротко.", {}, [], danger=False, on_request=True)
async def read_selection_aloud():
    from .. import clip, voice
    text = await asyncio.to_thread(clip.copy_selection)
    if not text:
        return {"error": "ничего не выделено: пусть выделит текст и попросит снова (или нажмёт Ctrl+Alt+T)"}
    asyncio.create_task(voice.speak(text[:2500], emotion="neutral"))
    return {"ok": True, "chars": len(text), "note": "читаю вслух"}


@tool("monitor_off", "Погасить экран (монитор), не выключая компьютер. Проснётся от мыши или клавиатуры.", {}, [], danger=False, ask=True, after_reply=True, on_request=True, confirm="Погасить экран?",
      intent=r"(погас|гас[иь]|выключ|отключ|выруб)\w*\s+(\w+\s+){0,2}(экран|монитор|дисплей)|(экран|монитор|дисплей)\w*\s+(\w+\s+){0,2}(погас|выключ|отключ|выруб)|(turn|switch)\s+off\s+(the\s+)?(screen|monitor|display)")
async def monitor_off():
    import ctypes
    await asyncio.sleep(2)
    ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)
    return {"ok": True}


@tool("modules_list", "Модули программы, которые можно скачать или удалить: распознавание речи, голоса (в т. ч. голос с эмоциями), "
      "плагины — с размером и что установлено.", {}, [], danger=False)
async def modules_list():
    from .. import modules
    return [{k: m[k] for k in ("id", "name", "size", "status", "needs")} for m in await asyncio.to_thread(modules.listing)]


@tool("module_install", "Скачать и установить модуль по id из modules_list (или удалить: action=remove). Большие — спросит разрешения.",
      {"id": S("id модуля"), "action": S("install | remove", enum=["install", "remove"])}, ["id"], danger=False, on_request=True)
async def module_install(id, action="install"):
    from .. import modules
    m = next((x for x in await asyncio.to_thread(modules.listing) if x["id"] == id), None)
    if not m:
        return {"error": f"нет модуля {id}"}
    if action == "install" and m["size"] >= 300 and not await confirm(f"Скачать «{m['name']}» — {m['size']} МБ?", "module_install"):
        return {"error": "пользователь не разрешил скачивание"}
    if not modules.start(id, action):
        return {"error": "модуль уже ставится или действие неизвестно"}
    return {"ok": True, "started": True, "note": "ход видно во вкладке «Модули»"}


@tool("ai_model_status", "Какая модель ИИ сейчас отвечает и как быстро (последний замер подбора моделей).", {}, [], danger=False)
def ai_model_status():
    from .. import llm
    name = config["llm"]["primary"]
    p = llm.provider(name)
    return {"provider": name, "model": p.get("model"), "auto_pick": config.get("llm.auto_model", True),
            "last_bench": [{"model": m, "seconds": s} for m, s in llm._pick.get("results", [])]}


@tool("ai_model_pick", "Замерить доступные модели NVIDIA и переключиться на самую быструю рабочую (минута-другая).", {}, [], danger=False, on_request=True)
async def ai_model_pick():
    from .. import llm
    best = await llm.auto_pick(reason="попросили найти побыстрее", force=True)
    return {"ok": bool(best), "model": best, "results": [{"model": m, "seconds": s} for m, s in llm._pick.get("results", [])]}
