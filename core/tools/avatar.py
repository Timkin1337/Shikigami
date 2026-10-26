"""Аватар: переодеться (сменить модель), сделать жест."""
import difflib

from .. import bus
from ..config import config, DATA
from . import tool, S, I

GESTURES = ["wave", "bow", "greet", "guide", "nod", "call", "think", "shy", "angry", "happy", "sad", "surprised", "stretch", "dance"]


def _models():
    from .. import vrm_meta
    out = []
    for p in sorted((DATA / "models").glob("*.vrm")):
        try:
            out.append(vrm_meta.read(p, DATA / "models" / ".thumbs"))
        except Exception:
            out.append({"name": p.name, "title": p.stem})
    return out


def current_model():
    """Какая модель надета на самом деле. В настройках пусто или файл удалён — аватар берёт первую по алфавиту
    (ui/avatar.js, doLoadModel); здесь то же правило."""
    names = sorted(p.name for p in (DATA / "models").glob("*.vrm"))
    cur = config.get("avatar.model")
    if cur in names:
        return cur
    return names[0] if names else ""


@tool("list_avatars", "Список моделей (нарядов) персонажа, какая надета сейчас.")
def list_avatars():
    cur = current_model()
    return [{"file": m["name"], "title": m.get("title"), "author": m.get("author"), "current": m["name"] == cur} for m in _models()]


@tool("switch_avatar", "Переодеться: сменить 3D-модель персонажа на рабочем столе по названию или имени файла.",
      {"name": S("Название модели или часть имени файла, например 'goth', 'Seed-san'")}, ["name"], on_request=True)
def switch_avatar(name):
    models = _models()
    key = name.lower()
    hit = next((m for m in models if key in m["name"].lower() or key in (m.get("title") or "").lower()), None)
    if not hit:
        titles = {(m.get("title") or m["name"]).lower(): m for m in models}
        close = difflib.get_close_matches(key, list(titles), n=1, cutoff=0.4)
        hit = titles[close[0]] if close else None
    if not hit:
        return {"error": f"нет модели «{name}»", "available": [m.get("title") or m["name"] for m in models]}
    config.update({"avatar": {"model": hit["name"], "enabled": True}})
    bus.emit("config.changed")
    return {"ok": True, "now_wearing": hit.get("title") or hit["name"]}


# avatar_outfit («сними туфли») убран 25.10: аватар не умеет скрывать части наряда (per_model.hidden никто не
# читал), а инструмент всегда падал на импорте несуществующего avatar_parts. Вернуть — вместе со скрытием в ui/avatar.js.


@tool("avatar_home", "{name} уходит домой — в свою комнату на рабочем столе (on=true) или возвращается на рабочий стол (on=false).",
      {"on": {"type": "boolean", "description": "true — домой, false — на рабочий стол"}}, ["on"], on_request=True)
def avatar_home(on):
    from ..config import config
    if on and not config.get("features.room"):
        return {"error": "комната пока в разработке и выключена"}
    bus.emit("avatar.home", on=bool(on))
    return {"ok": True, "home": bool(on)}


@tool("chatter_mode", "Сколько {name} говорит сама: помолчать N минут (minutes) или сменить режим (mode: silent | minimal | "
      "normal | chatty). Вызывай, когда хозяин просит помолчать, говорить меньше или больше.",
      {"minutes": I("Помолчать столько минут, 0 — снять"), "mode": S("Режим", enum=["silent", "minimal", "normal", "chatty"])}, [], on_request=True)
def chatter_mode(minutes=None, mode=None):
    from .. import chatter
    if mode:
        config.update({"behavior": {"chatty": mode}})
        bus.emit("config.changed")
    if minutes is not None:
        chatter.mute(int(minutes))
    return {"ok": True, "mode": chatter.mode(), "muted_min": int(minutes or 0)}


@tool("avatar_hide", "{name} прячется за ближайшее окно и выглядывает из-за него («спрячься»). Игра в прятки со счётом "
      "и наклейками — fun(what=seek).", {}, [])
def avatar_hide():
    bus.emit("avatar.hide")
    return {"ok": True}


@tool("avatar_screen", "Перейти персонажем на другой монитор (номер слева направо, с 1).",
      {"index": I("Номер экрана слева направо")}, ["index"], on_request=True)
def avatar_screen(index):
    bus.emit("avatar.screen", index=int(index))
    return {"ok": True, "screen": index}


@tool("avatar_gesture", f"Сделать жест аватаром на рабочем столе: {', '.join(GESTURES)}.",
      {"name": S("Жест", enum=GESTURES), "seconds": I("Сколько секунд, по умолчанию 3")}, ["name"])
def avatar_gesture(name, seconds=3):
    if name == "dance" and (seconds or 0) < 10:
        seconds = 20          # танец по просьбе — секунд двадцать, потом сама остановится
    bus.emit("avatar.gesture", name=name, seconds=seconds)
    return {"ok": True}
