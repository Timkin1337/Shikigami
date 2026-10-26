"""Фокус (помодоро) с {name}: 25 минут работы — она молчит и не отвлекает, у её ног тикает таймер;
потом хвалит и гонит на перерыв. После четырёх подходов — большой перерыв. Счёт подходов за день хранится.
"""
import asyncio
import datetime as dt
import json
import time

from aiohttp import web

from core import bus, lines
from core.config import DATA
from core.tools import tool, I, S

TITLE = "Фокус"
DESCRIPTION = "Помодоро с {name}: работа 25 минут без отвлечений, перерыв, похвала и счёт за день."

SAVE = DATA / "focus.json"
state = {"phase": None, "until": 0.0, "task": "", "work": 25, "rest": 5, "done_today": 0, "day": "", "total": 0}
_task = {"t": None}

LINES = {
    "start": [("Двадцать пять минут. Я буду тихо. Работайте.", "neutral"),
              ("Фокус. Я рядом, но молчу. Почти.", "neutral")],
    "done": [("Время! Вы молодец. Не то чтобы я следила… Пять минут отдыха.", "happy"),
             ("Готово. Встаньте, потянитесь, попейте воды. Это приказ.", "happy")],
    "big": [("Четыре подхода! Ладно, вы сегодня хорошо поработали. Большой перерыв — пятнадцать минут.", "happy")],
    "rest_over": [("Перерыв всё. Ещё один подход?", "neutral"), ("Отдохнули? Тогда за работу.", "neutral")],
    "stop": [("Остановили? Ну… бывает.", "sad")],
}


lines.register("focus", LINES, {"start": "человек начал 25 минут работы по помодоро — она обещает не мешать",
                                "done": "25 минут работы закончились — пора на короткий перерыв",
                                "big": "четвёртый подход подряд — большой перерыв 15 минут", "rest_over": "перерыв закончился",
                                "stop": "человек остановил фокус-таймер раньше времени"})


def _load():
    try:
        state.update(json.loads(SAVE.read_text(encoding="utf-8")))
    except Exception:
        pass
    state.update(phase=None, until=0)


def _save():
    SAVE.write_text(json.dumps({k: state[k] for k in ("work", "rest", "done_today", "day", "total")}, ensure_ascii=False),
                    encoding="utf-8")


def public():
    left = max(0, int(state["until"] - time.time())) if state["phase"] else 0
    return {**state, "left": left}


async def _say(kind):
    text, emo = lines.pick(f"focus.{kind}")
    bus.emit("avatar.state", state="idle", emotion=emo)
    bus.emit("avatar.gesture", name={"happy": "happy", "sad": "sad"}.get(emo, "nod"), seconds=2.5)
    from core import voice
    asyncio.create_task(voice.speak(text, emotion=emo))


def _emit():
    bus.emit("avatar.focus", **public())
    bus.emit("plugin.focus", state=public())


async def _run(minutes, phase):
    state.update(phase=phase, until=time.time() + minutes * 60)
    _emit()
    await asyncio.sleep(minutes * 60)
    if phase == "work":
        today = dt.date.today().isoformat()
        if state["day"] != today:
            state.update(day=today, done_today=0)
        state["done_today"] += 1
        state["total"] += 1
        _save()
        big = state["done_today"] % 4 == 0
        await _say("big" if big else "done")
        _task["t"] = asyncio.create_task(_run(15 if big else state["rest"], "rest"))
    else:
        state.update(phase=None, until=0)
        _emit()
        await _say("rest_over")


async def start(minutes=None, task=""):
    stop_timer()
    state["work"] = max(1, min(120, int(minutes or state["work"] or 25)))
    state["task"] = task or ""
    await _say("start")
    state.update(phase="work", until=time.time() + state["work"] * 60)     # сразу, чтобы ответ уже показывал таймер
    _task["t"] = asyncio.create_task(_run(state["work"], "work"))
    return public()


def stop_timer():
    t = _task["t"]
    if t and not t.done():
        t.cancel()
    _task["t"] = None
    was = state["phase"]
    state.update(phase=None, until=0)
    _emit()
    return was


def active():
    """Для режиссёра поведения: во время работы — никаких «перерыв?» и болтовни."""
    return state["phase"] == "work"


def setup(api):
    _load()

    @api.route("GET", "/state")
    async def r_state(_):
        return web.json_response(public())

    @api.route("POST", "/start")
    async def r_start(r):
        b = await r.json()
        return web.json_response(await start(b.get("minutes"), b.get("task", "")))

    @api.route("POST", "/stop")
    async def r_stop(_):
        if stop_timer() == "work":
            await _say("stop")
        return web.json_response(public())


@tool("focus_start", "Запустить фокус-таймер (помодоро): работа N минут без отвлечений, затем перерыв. {name} молчит во время работы.",
      {"minutes": I("Минут работы, по умолчанию 25"), "task": S("Над чем работаем (необязательно)")}, [], group="plugin:focus", on_request=True)
async def focus_start(minutes=25, task=""):
    s = await start(minutes, task)
    return {"started": True, "minutes": s["work"], "done_today": s["done_today"]}


@tool("focus_stop", "Остановить фокус-таймер (помодоро).", {}, [], group="plugin:focus")
async def focus_stop():
    was = stop_timer()
    return {"stopped": bool(was), "done_today": state["done_today"]}
