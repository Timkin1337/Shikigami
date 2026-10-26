"""Повседневное: напоминания, заметки, задачи, календарь, погода, курсы валют."""
import asyncio
import datetime as dt
import time

import aiohttp

from .. import db
from ..config import config
from ..i18n import lang, tr
from . import tool, S, I


def _parse_when(when=None, in_minutes=None):
    if in_minutes is not None:
        return time.time() + float(in_minutes) * 60
    if not when:
        raise ValueError("нужно when или in_minutes")
    w = when.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%H:%M", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(w, fmt)
            if fmt == "%H:%M":
                now = dt.datetime.now()
                d = now.replace(hour=d.hour, minute=d.minute, second=0, microsecond=0)
                if d < now:
                    d += dt.timedelta(days=1)
            return d.timestamp()
        except ValueError:
            continue
    return dt.datetime.fromisoformat(w).timestamp()


def _fmt(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m %H:%M") if ts else None


# --- напоминания ---

@tool("add_reminder", "Поставить напоминание или таймер. Укажи либо время when, либо in_minutes.",
      {"text": S("О чём напомнить"), "when": S("Дата-время ISO 'YYYY-MM-DDTHH:MM' или 'HH:MM'"),
       "in_minutes": {"type": "number", "description": "Через сколько минут"},
       "repeat": S("Повтор: daily | weekly | (пусто)", enum=["", "daily", "weekly"])}, ["text"])
def add_reminder(text, when=None, in_minutes=None, repeat=""):
    due = _parse_when(when, in_minutes)
    rid = db.ex("INSERT INTO reminders(text, due, repeat, created) VALUES(?,?,?,?)", (text, due, repeat or "", time.time()))
    from .. import bus
    bus.emit("data.changed", what="reminders")
    if not repeat and 0 < due - time.time() <= 2 * 3600:     # короткий таймер — песочные часы рядом с ней
        bus.emit("avatar.timer", id=rid, seconds=round(due - time.time()), text=text)
    return {"ok": True, "id": rid, "due": _fmt(due)}


@tool("list_reminders", "Список активных напоминаний.")
def list_reminders():
    rows = db.q("SELECT id, text, due, repeat FROM reminders WHERE done=0 ORDER BY due")
    return [{"id": r["id"], "text": r["text"], "due": _fmt(r["due"]), "repeat": r["repeat"]} for r in rows]


@tool("cancel_reminder", "Отменить напоминание по id.", {"id": I("id напоминания")}, ["id"])
def cancel_reminder(id):
    db.ex("UPDATE reminders SET done=1 WHERE id=?", (id,))
    from .. import bus
    bus.emit("data.changed", what="reminders")
    bus.emit("avatar.timer.cancel", id=int(id))
    return {"ok": True}


# --- заметки ---

@tool("add_note", "Сохранить заметку.", {"text": S("Текст заметки"), "title": S("Заголовок (необязательно)")}, ["text"])
def add_note(text, title=""):
    now = time.time()
    nid = db.ex("INSERT INTO notes(title, text, created, updated) VALUES(?,?,?,?)", (title or text[:40], text, now, now))
    from .. import bus
    bus.emit("data.changed", what="notes")
    return {"ok": True, "id": nid}


@tool("find_notes", "Найти заметки по словам (пусто — последние).", {"query": S("Что искать")})
def find_notes(query=""):
    if query:
        rows = db.q("SELECT id, title, text, updated FROM notes WHERE deleted=0 AND (text LIKE ? OR title LIKE ?) "
                    "ORDER BY updated DESC LIMIT 20", (f"%{query}%", f"%{query}%"))
    else:
        rows = db.q("SELECT id, title, text, updated FROM notes WHERE deleted=0 ORDER BY updated DESC LIMIT 20")
    return [{"id": r["id"], "title": r["title"], "text": r["text"][:500], "date": _fmt(r["updated"])} for r in rows]


@tool("delete_note", "Удалить заметку по id.", {"id": I("id")}, ["id"])
def delete_note(id):
    db.ex("UPDATE notes SET deleted=1 WHERE id=?", (id,))
    from .. import bus
    bus.emit("data.changed", what="notes")
    return {"ok": True}


# --- задачи ---

@tool("add_task", "Добавить задачу в список дел.", {"text": S("Задача"), "due": S("Срок ISO (необязательно)")}, ["text"])
def add_task(text, due=None):
    tid = db.ex("INSERT INTO tasks(text, due, created) VALUES(?,?,?)", (text, _parse_when(due) if due else None, time.time()))
    from .. import bus
    bus.emit("data.changed", what="tasks")
    return {"ok": True, "id": tid}


@tool("list_tasks", "Список дел.", {"include_done": {"type": "boolean"}})
def list_tasks(include_done=False):
    rows = db.q("SELECT * FROM tasks " + ("" if include_done else "WHERE done=0 ") + "ORDER BY done, COALESCE(due, 9e18)")
    return [{"id": r["id"], "text": r["text"], "done": bool(r["done"]), "due": _fmt(r["due"])} for r in rows]


@tool("complete_task", "Отметить задачу выполненной.", {"id": I("id")}, ["id"])
def complete_task(id):
    db.ex("UPDATE tasks SET done=1 WHERE id=?", (id,))
    from .. import bus
    bus.emit("data.changed", what="tasks")
    return {"ok": True}


# --- календарь ---

@tool("add_event", "Добавить событие в календарь.",
      {"title": S("Название"), "start": S("Начало ISO 'YYYY-MM-DDTHH:MM'"), "end": S("Конец ISO (необязательно)"),
       "place": S("Где (необязательно)"),
       "remind_minutes": I("За сколько минут напомнить (необязательно)")}, ["title", "start"])
def add_event(title, start, end=None, place="", remind_minutes=None):
    s = _parse_when(start)
    e = _parse_when(end) if end else None
    eid = db.ex("INSERT INTO events(title, start, end, place, created) VALUES(?,?,?,?,?)", (title, s, e, place, time.time()))
    if remind_minutes:
        db.ex("INSERT INTO reminders(text, due, repeat, created) VALUES(?,?,?,?)",
              (f"Скоро: {title}", s - remind_minutes * 60, "", time.time()))
    from .. import bus
    bus.emit("data.changed", what="events")
    return {"ok": True, "id": eid, "start": _fmt(s)}


@tool("list_events", "События календаря на ближайшие дни.", {"days": I("Сколько дней вперёд, по умолчанию 7")})
def list_events(days=7):
    now = time.time()
    rows = db.q("SELECT * FROM events WHERE start BETWEEN ? AND ? ORDER BY start", (now - 3600, now + days * 86400))
    return [{"id": r["id"], "title": r["title"], "start": _fmt(r["start"]), "end": _fmt(r["end"]), "place": r["place"]}
            for r in rows]


# --- погода (Open-Meteo, без ключа) ---

WMO = {0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно", 45: "туман", 48: "изморозь",
       51: "морось", 53: "морось", 55: "сильная морось", 61: "небольшой дождь", 63: "дождь", 65: "ливень",
       66: "ледяной дождь", 67: "ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег", 77: "снежная крупа",
       80: "ливневый дождь", 81: "ливни", 82: "сильные ливни", 85: "снегопад", 86: "сильный снегопад",
       95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом"}
_geo_cache = {}


async def _geocode(s, city):
    key = (city, lang())                 # имя города приходит на языке интерфейса
    if key in _geo_cache:
        return _geo_cache[key]
    async with s.get("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": city, "count": 1, "language": lang()}) as r:
        js = await r.json()
    if not js.get("results"):
        return None
    g = js["results"][0]
    _geo_cache[key] = g
    return g


@tool("weather", "Погода сейчас и прогноз на несколько дней.",
      {"city": S("Город (по умолчанию — из настроек)"), "days": I("Дней прогноза 1-7, по умолчанию 3")})
async def weather(city=None, days=3):
    city = city or config["city"]
    if not city:
        return {"error": "город не задан: спроси у пользователя и предложи указать его в настройках"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        g = await _geocode(s, city)
        if not g:
            return {"error": f"не нашла город {city}"}
        params = {
            "latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto",
            "forecast_days": max(2, min(7, int(days or 3))),  # завтра нужно почти всегда
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "wind_speed_unit": "ms"}
        for attempt in range(3):  # Open-Meteo изредка отдаёт пустой ответ — повторяем
            async with s.get("https://api.open-meteo.com/v1/forecast", params=params) as r:
                w = await r.json(content_type=None)
            if "current" in w and "daily" in w:
                break
            await asyncio.sleep(1 + attempt)
        else:
            return {"error": "сервис погоды сейчас перегружен — попробую позже"}
    c = w["current"]
    d = w["daily"]
    return {"city": g["name"], "now": {"temp": round(c["temperature_2m"]), "feels": round(c["apparent_temperature"]),
                                       "sky": tr(WMO.get(c["weather_code"], "?")), "wind_ms": c["wind_speed_10m"],
                                       "humidity": c["relative_humidity_2m"], "code": c["weather_code"]},
            "days": [{"date": d["time"][i], "day": _day_label(d["time"][i]), "min": round(d["temperature_2m_min"][i]), "max": round(d["temperature_2m_max"][i]),
                      "sky": tr(WMO.get(d["weather_code"][i], "?")), "rain_chance": d["precipitation_probability_max"][i]}
                     for i in range(len(d["time"]))]}


def _day_label(iso):
    delta = (dt.date.fromisoformat(iso) - dt.date.today()).days
    names = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    return {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(delta, names[dt.date.fromisoformat(iso).weekday()])


# --- курсы валют: общий бесплатный источник без ключа, любая валюта мира ---
# fawazahmed0/currency-api: 200+ валют, обновляется раз в день, есть архив по датам (для «изменения за день»).

_rates = {"t": 0, "key": None, "data": None}
RATES_URLS = ["https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/{base}.json",
              "https://{day}.currency-api.pages.dev/v1/currencies/{base}.json"]


async def _fetch_rates(s, base, day="latest"):
    for url in RATES_URLS:
        try:
            async with s.get(url.format(day=day, base=base.lower())) as r:
                if r.status == 200:
                    d = await r.json(content_type=None)
                    return d["date"], d[base.lower()]
        except (aiohttp.ClientError, asyncio.TimeoutError, KeyError):
            continue
    raise RuntimeError("сервис курсов не ответил")


async def rates_table():
    base = (config.get("currency_base") or "USD").upper()
    if time.time() - _rates["t"] > 3600 or _rates["key"] != base or not _rates["data"]:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
            day, cur = await _fetch_rates(s, base)
            prev_day = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
            try:
                _, prev = await _fetch_rates(s, base, prev_day)
            except RuntimeError:
                prev = {}
        # в ответе «1 базовая = X валюты»; нам нужна цена валюты в базовой: 1/X
        _rates["data"] = {"base": base, "date": day, "source": "currency-api (рыночные курсы)",
                          "per_unit": {k.upper(): (1 / v, 1 / prev[k] if prev.get(k) else None)
                                       for k, v in cur.items() if isinstance(v, (int, float)) and v > 0}}
        _rates.update(t=time.time(), key=base)
    return _rates["data"]


@tool("currency", "Курсы любых валют мира к валюте пользователя (из настроек) и пересчёт суммы.",
      {"codes": S("Коды валют через запятую (USD,EUR,UAH…), по умолчанию из настроек"),
       "amount": {"type": "number", "description": "Сумма для пересчёта (необязательно)"},
       "from_code": S("Из какой валюты, например USD"), "to_code": S("В какую, например EUR")})
async def currency(codes=None, amount=None, from_code=None, to_code=None):
    t = await rates_table()
    base, table = t["base"], t["per_unit"]

    def in_base(code):
        code = code.upper()
        return 1.0 if code == base else table[code][0]

    out = {"base": base, "date": t["date"], "source": t["source"], "rates": {}}
    for c in (codes or config.get("currency_codes") or "EUR,GBP,CNY").split(","):
        c = c.strip().upper()
        if c in table and c != base:
            now, prev = table[c]
            out["rates"][c] = {"rate": round(now, 6),
                               "change_pct": round((now - prev) / prev * 100, 2) if prev else 0.0}
    if amount is not None and from_code and to_code:
        out["converted"] = round(amount * in_base(from_code) / in_base(to_code), 2)
    return out
