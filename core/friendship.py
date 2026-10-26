"""Уровень дружбы: растёт от разговоров, поглаживаний и игр — и никогда не падает.

Нарочно без «ты меня забросил»: очки не сгорают, за пропущенные дни ничего не отнимается (давить виной — не наш
стиль). Уровень меняет тон ответов (строка в характере), сердечки при поглаживании и открывает праздник при
повышении. Всё лежит локально в data/friendship.json.
"""
import asyncio
import datetime as dt
import json

from . import bus
from .config import DATA, config

PATH = DATA / "friendship.json"
LEVELS = [(0, "Знакомые"), (30, "Приятели"), (100, "Друзья"), (250, "Близкие друзья"), (500, "Лучшие друзья")]
# за что и сколько; в скобках — потолок за день, чтобы уровень не «накручивался» спамом
GAIN = {"chat": (1, 25), "pat": (2, 10), "pat_long": (3, 6), "game": (3, 12), "visit": (3, 3)}
# тон — только для стилизатора: исполнитель и инструменты от уровня не зависят
TONE = ["Ты и {user} только знакомитесь: держись чуть сдержаннее, без лишней фамильярности.",
        "Ты и {user} — приятели: можно шутить свободнее.",
        "Ты и {user} — друзья: под колкостями больше теплоты, иногда вспоминай общие моменты из разговора.",
        "Ты и {user} — близкие друзья: можешь прямо сказать, что рада, и чаще проявлять заботу.",
        "Ты и {user} — лучшие друзья: полное доверие, тёплая откровенность — но характер прежний."]
GAME_TOOLS = {"play_game", "chess_game", "fun"}

_state = None


def _load():
    global _state
    if _state is None:
        try:
            _state = json.loads(PATH.read_text(encoding="utf-8"))
        except Exception:
            _state = {"points": 0, "day": "", "today": {}}
    return _state


def _save():
    PATH.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")


def level_of(points):
    i = 0
    for k, (need, _) in enumerate(LEVELS):
        if points >= need:
            i = k
    return i


def status():
    s = _load()
    i = level_of(s["points"])
    nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return {"enabled": enabled(), "points": s["points"], "level": i, "name": LEVELS[i][1],
            "next": nxt, "next_name": LEVELS[i + 1][1] if nxt else None,
            "floor": LEVELS[i][0]}


def enabled():
    return config.get("behavior.friendship", True)


def gain(kind):
    """Начислить очки за событие; повышение уровня — событие friend.levelup для аватара и окна."""
    if not enabled() or kind not in GAIN:
        return
    s = _load()
    today = dt.date.today().isoformat()
    if s.get("day") != today:
        s["day"], s["today"] = today, {}
        if kind != "visit":
            gain("visit")                       # первая встреча за день
    pts, cap = GAIN[kind]
    used = s["today"].get(kind, 0)
    add = max(0, min(pts, cap - used))
    if not add:
        return
    before = level_of(s["points"])
    s["today"][kind] = used + add
    s["points"] += add
    _save()
    after = level_of(s["points"])
    bus.emit("friend.state", **status())
    if after > before:
        print(f"дружба: новый уровень — {LEVELS[after][1]}", flush=True)
        bus.emit("friend.levelup", level=after, name=LEVELS[after][1])


def tone_line():
    if not enabled():
        return ""
    return "\n" + TONE[level_of(_load()["points"])].format(user=config["user_name"] or "пользователь")


async def watch():
    """Слушает шину: реплики пользователя и сыгранные игры."""
    q = bus.subscribe()
    while True:
        m = await q.get()
        try:
            if m["type"] == "chat.user" and m.get("source") != "minecraft":
                gain("chat")
            elif m["type"] == "chat.tool" and m.get("status") == "ok" and m.get("name") in GAME_TOOLS:
                gain("game")
        except Exception as e:
            print("дружба:", repr(e), flush=True)


def start():
    asyncio.get_running_loop().create_task(watch())
