"""Пользовательские команды: граф «триггер → условия/циклы → действия».

Граф хранится в БД как JSON: {"nodes": [{id, type, params, x, y}], "edges": [{from, port, to}]}.
Триггеры: фраза (голосом или в чате, срабатывает раньше ИИ), горячая клавиша, расписание, запуск, вручную.
"""
import asyncio
import datetime as dt
import json
import difflib
import re
import time

from aiohttp import web

from . import bus, db, scheduler, tools
from .config import config

routes = web.RouteTableDef()

# Палитра узлов для редактора: тип → (группа, заголовок, параметры, выходы)
NODE_TYPES = {
    "trigger.phrase": ("trigger", "Голосовая фраза", {"phrases": "фраза; другая фраза; найди {запрос}"}, ["out"]),
    "trigger.hotkey": ("trigger", "Горячая клавиша", {"accelerator": "Alt+Y"}, ["out"]),
    "trigger.schedule": ("trigger", "По расписанию", {"time": "09:00", "days": "пн,вт,ср,чт,пт,сб,вс"}, ["out"]),
    "trigger.startup": ("trigger", "При запуске", {}, ["out"]),
    "trigger.manual": ("trigger", "Вручную", {}, ["out"]),
    "flow.if": ("flow", "Если", {"check": "process_running", "value": "chrome"}, ["true", "false"]),
    "flow.ask": ("flow", "Спросить «да/нет»", {"question": "Продолжить?"}, ["true", "false"]),
    "flow.delay": ("flow", "Пауза", {"seconds": "1"}, ["out"]),
    "flow.loop": ("flow", "Повторить", {"count": "3"}, ["body", "done"]),
    "act.open_url": ("action", "Открыть ссылку", {"url": "https://youtube.com"}, ["out"]),
    "act.open_app": ("action", "Запустить программу", {"name": "Telegram"}, ["out"]),
    "act.open_path": ("action", "Открыть файл/папку", {"path": "загрузки"}, ["out"]),
    "act.youtube": ("action", "Включить на YouTube", {"query": "lofi hip hop"}, ["out"]),
    "act.keys": ("action", "Нажать клавиши", {"combo": "ctrl+c"}, ["out"]),
    "act.type": ("action", "Ввести текст", {"text": "привет"}, ["out"]),
    "act.say": ("action", "Сказать вслух", {"text": "Готово!"}, ["out"]),
    "act.notify": ("action", "Уведомление", {"title": "Напоминание", "text": "Готово"}, ["out"]),
    "act.ai": ("action", "Спросить ИИ → {ai}", {"prompt": "Переведи на русский: {clipboard}"}, ["out"]),
    "act.clipboard": ("action", "Положить в буфер", {"text": "{ai}"}, ["out"]),
    "act.media": ("action", "Плеер", {"action": "play_pause"}, ["out"]),
    "act.volume": ("action", "Громкость", {"set": "30"}, ["out"]),
    "act.window": ("action", "Окно", {"title": "chrome", "action": "focus"}, ["out"]),
    "act.powershell": ("action", "PowerShell", {"command": "Get-Date"}, ["out"]),
    "act.lock": ("action", "Заблокировать ПК", {}, ["out"]),
    "act.tool": ("action", "Любой инструмент ИИ", {"name": "weather", "args": "{}"}, ["out"]),
    "act.chat": ("action", "Передать ИИ как просьбу", {"text": "Расскажи погоду на завтра"}, ["out"]),
    "act.set": ("action", "Переменная", {"name": "x", "value": "1"}, ["out"]),
    # состояние самой программы — для сценариев («рабочий режим», «отбой»)
    "act.theme": ("action", "Тема оформления", {"theme": "night_fox"}, ["out"]),
    "act.quiet": ("action", "Тишина (минут)", {"minutes": "60"}, ["out"]),
    "act.focus": ("action", "Фокус-таймер (минут)", {"minutes": "25"}, ["out"]),
    "act.monitor_off": ("action", "Погасить экран", {}, ["out"]),
}
IF_CHECKS = ["process_running", "window_open", "time_between", "clipboard_contains", "var_equals", "ai_yes"]
DAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# --- хранилище ---

def all_commands():
    rows = db.q("SELECT * FROM commands ORDER BY grp, name")
    for r in rows:
        r["graph"] = json.loads(r["graph"] or '{"nodes":[],"edges":[]}')
    return rows


def get_command(cid):
    r = db.one("SELECT * FROM commands WHERE id=?", (cid,))
    if r:
        r["graph"] = json.loads(r["graph"])
    return r


def save_command(c):
    g = json.dumps(c.get("graph") or {"nodes": [], "edges": []}, ensure_ascii=False)
    if c.get("id"):
        db.ex("UPDATE commands SET name=?, grp=?, enabled=?, graph=?, updated=? WHERE id=?",
              (c["name"], c.get("grp", ""), int(c.get("enabled", 1)), g, time.time(), c["id"]))
        cid = c["id"]
    else:
        cid = db.ex("INSERT INTO commands(name, grp, enabled, graph, updated) VALUES(?,?,?,?,?)",
                    (c["name"], c.get("grp", ""), int(c.get("enabled", 1)), g, time.time()))
    bus.emit("commands.changed")
    return cid


def linear_graph(trigger, steps):
    """Линейная команда из списка шагов — так их создаёт ИИ."""
    nodes = [{"id": "n0", "type": trigger["type"], "params": trigger.get("params", {}), "x": 40, "y": 80}]
    edges = []
    for i, s in enumerate(steps, 1):
        nodes.append({"id": f"n{i}", "type": s["type"], "params": s.get("params", {}), "x": 40 + 240 * i, "y": 80})
        edges.append({"from": f"n{i-1}", "port": "out", "to": f"n{i}"})
    return {"nodes": nodes, "edges": edges}


# --- выполнение ---

class Ctx:
    def __init__(self, vars=None, source="ui", origin=None):
        self.vars = dict(vars or {})
        self.source = source
        self.origin = origin or source   # откуда пришла просьба (для «ai» — чат, Telegram, голос)
        self.steps = 0
        self.said = []                   # что команда «сказала» — уходит ответом, если человек не у ПК

    def sub(self, s):
        if not isinstance(s, str):
            return s

        def rep(m):
            k = m.group(1)
            if k == "clipboard":
                from .tools.system import clipboard_text
                try:
                    return clipboard_text()
                except Exception:
                    return ""
            if k == "time":
                return dt.datetime.now().strftime("%H:%M")
            if k == "date":
                return dt.datetime.now().strftime("%d.%m.%Y")
            if k == "greet":
                h = dt.datetime.now().hour
                return "Доброе утро" if 5 <= h < 11 else "Добрый день" if h < 17 else "Добрый вечер" if h < 23 else "Доброй ночи"
            return str(self.vars.get(k, m.group(0)))
        return re.sub(r"\{([\wа-яА-ЯёЁ]+)\}", rep, s)


async def _check(node, ctx):
    p = {k: ctx.sub(v) for k, v in node.get("params", {}).items()}
    kind, val = p.get("check"), p.get("value", "")
    if kind == "process_running":
        import psutil
        n = val.lower().removesuffix(".exe")
        return any((x.info["name"] or "").lower().removesuffix(".exe") == n for x in psutil.process_iter(["name"]))
    if kind == "window_open":
        from .tools.apps import _find_window
        return bool(await asyncio.to_thread(_find_window, val))
    if kind == "time_between":
        a, b = [x.strip() for x in val.split("-")]
        now = dt.datetime.now().strftime("%H:%M")
        return a <= now <= b if a <= b else (now >= a or now <= b)
    if kind == "clipboard_contains":
        return val.lower() in ctx.sub("{clipboard}").lower()
    if kind == "var_equals":
        k, v = val.split("=", 1)
        return str(ctx.vars.get(k.strip())) == v.strip()
    if kind == "ai_yes":
        from . import llm
        ans = await llm.simple(val + "\nОтветь одним словом: да или нет.")
        return "да" in ans.lower()
    return False


DANGER_STEPS = {"act.powershell", "act.lock"}

_NUMS = {w: i for i, w in enumerate("ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать "
                                     "тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split())}
_NUMS.update(двадцать=20, тридцать=30, сорок=40, пятьдесят=50, шестьдесят=60, семьдесят=70, восемьдесят=80,
             девяносто=90, сто=100, одна=1, две=2)


def _num(v):
    """«30», «-10» или голосом «тридцать пять» → число."""
    v = str(v).strip().lower().replace("%", "").replace("процентов", "")
    try:
        return int(float(v.replace(",", ".")))
    except ValueError:
        words = [w for w in v.split() if w in _NUMS]
        if not words:
            raise ValueError(f"не понимаю число «{v}»")
        return sum(_NUMS[w] for w in words)


async def _guard(t, p, ctx):
    """Команду, запущенную самим ИИ, не пускаем делать опасное без спроса — иначе через
    create_command + run_command можно было бы обойти подтверждение инструментов."""
    if ctx.source != "ai" or not config["confirm_dangerous"]:
        return
    tool_ = tools.REGISTRY.get(p.get("name")) if t == "act.tool" else None
    if t in DANGER_STEPS or (tool_ and tool_.danger):
        from . import agent
        what = f"{NODE_TYPES[t][1]}: {json.dumps(p, ensure_ascii=False)[:300]}"
        if not await agent.ask_confirm(None, t, f"Команда хочет выполнить — {what}", ctx.origin):
            raise RuntimeError("пользователь не разрешил шаг команды")


async def _act(node, ctx):
    t = node["type"]
    p = {k: ctx.sub(v) for k, v in node.get("params", {}).items()}
    await _guard(t, p, ctx)
    call = {"act.open_url": ("open_url", {"url": p.get("url")}),
            "act.open_app": ("open_app", {"name": p.get("name")}),
            "act.open_path": ("open_path", {"path": p.get("path")}),
            "act.youtube": ("youtube", {"query": p.get("query")}),
            "act.keys": ("press_keys", {"combo": p.get("combo")}),
            "act.type": ("type_text", {"text": p.get("text")}),
            "act.media": ("media", {"action": p.get("action", "play_pause")}),
            "act.window": ("window_action", {"title": p.get("title"), "action": p.get("action", "focus")}),
            "act.lock": ("lock_pc", {}),
            "act.powershell": ("run_powershell", {"command": p.get("command")}),
            "act.clipboard": ("clipboard", {"set_text": p.get("text", "")})}
    if t in call:
        name, args = call[t]
        res = await tools.run(name, args)
        ctx.vars["result"] = json.dumps(res, ensure_ascii=False)[:2000]
        if t == "act.powershell" and isinstance(res, dict):
            ctx.vars["result"] = res.get("stdout", "").strip()
        return res
    if t == "act.volume":
        args = {}
        for k in ("set", "change"):
            if str(p.get(k, "")).strip():
                args[k] = _num(p[k])
        if str(p.get("mute", "")).strip():
            args["mute"] = p["mute"].lower() in ("1", "true", "да")
        return await tools.run("volume", args)
    if t == "act.say":
        from . import voice
        ctx.said.append(p.get("text", ""))
        if ctx.origin == "telegram":       # хозяин не у компьютера: фраза уйдёт ответом в Telegram
            return
        bus.emit("chat.notice", text=p.get("text", ""))
        await voice.speak(p.get("text", ""))
    elif t == "act.notify":
        bus.emit("notify", title=p.get("title", config["assistant_name"]), text=p.get("text", ""))
    elif t == "act.ai":
        from . import llm
        ctx.vars["ai"] = (await llm.simple(p.get("prompt", ""))).strip()
    elif t == "act.tool":
        args = json.loads(p.get("args") or "{}")
        res = await tools.run(p["name"], args)
        ctx.vars["result"] = json.dumps(res, ensure_ascii=False)[:2000]
    elif t == "act.chat":
        from . import agent
        chat_id = _command_chat()
        ctx.vars["ai"] = await agent.respond(chat_id, p.get("text", ""), voice=ctx.source == "voice", source="command")
    elif t == "act.set":
        ctx.vars[p.get("name", "x")] = p.get("value", "")
    elif t == "act.theme":
        theme = p.get("theme", "").strip()
        config.update({"ui": {"theme": "" if theme in ("", "default", "тёплый") else theme}})
        bus.emit("config.changed")
    elif t == "act.quiet":
        from . import chatter
        chatter.mute(_num(p.get("minutes", 60)))
    elif t == "act.focus":
        return await tools.run("focus_start", {"minutes": _num(p.get("minutes", 25))})
    elif t == "act.monitor_off":
        import ctypes
        await asyncio.sleep(1.5)                     # дать договорить и отпустить мышь — иначе экран сразу проснётся
        ctypes.windll.user32.PostMessageW(0xFFFF, 0x0112, 0xF170, 2)    # HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, выкл


def _command_chat():
    r = db.one("SELECT id FROM chats WHERE title='Команды'")
    return r["id"] if r else db.new_chat("Команды")


async def _run_from(graph, node_id, port, ctx):
    nodes = {n["id"]: n for n in graph["nodes"]}
    for e in [e for e in graph["edges"] if e["from"] == node_id and e.get("port", "out") == port]:
        await _exec(graph, nodes[e["to"]], ctx)


async def _exec(graph, node, ctx):
    ctx.steps += 1
    if ctx.steps > 500:
        raise RuntimeError("слишком много шагов — похоже на бесконечный цикл")
    t = node["type"]
    bus.emit("command.step", node=node["id"], type=t)
    if t == "flow.if":
        await _run_from(graph, node["id"], "true" if await _check(node, ctx) else "false", ctx)
    elif t == "flow.ask":
        from . import agent
        ok = await agent.ask_confirm(None, "command", ctx.sub(node["params"].get("question", "Продолжить?")), ctx.source)
        await _run_from(graph, node["id"], "true" if ok else "false", ctx)
    elif t == "flow.delay":
        await asyncio.sleep(float(ctx.sub(node["params"].get("seconds", "1")) or 1))
        await _run_from(graph, node["id"], "out", ctx)
    elif t == "flow.loop":
        for i in range(int(ctx.sub(node["params"].get("count", "1")) or 1)):
            ctx.vars["i"] = i + 1
            await _run_from(graph, node["id"], "body", ctx)
        await _run_from(graph, node["id"], "done", ctx)
    else:
        if not t.startswith("trigger."):
            await _act(node, ctx)
        await _run_from(graph, node["id"], "out", ctx)


RUNLOG_FILE = None


def _log_run(c, trigger_type, source, t0, ok, error=""):
    """Журнал запусков (совет ИИ): когда, что сработало, чем кончилось. Последние 200 — в data/commands_log.json."""
    global RUNLOG_FILE
    from .config import DATA
    RUNLOG_FILE = RUNLOG_FILE or DATA / "commands_log.json"
    try:
        log = json.loads(RUNLOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        log = []
    log.insert(0, {"at": time.time(), "id": c["id"], "name": c["name"], "trigger": (trigger_type or "").split(".")[-1],
                   "source": source, "ok": ok, "error": error[:300], "sec": round(time.time() - t0, 2)})
    RUNLOG_FILE.write_text(json.dumps(log[:200], ensure_ascii=False), encoding="utf-8")


async def run_command(cid, trigger_type=None, vars=None, source="ui", origin=None):
    c = get_command(cid)
    if not c:
        return {"error": "нет такой команды"}
    t0 = time.time()
    g = c["graph"]
    starts = [n for n in g["nodes"] if n["type"].startswith("trigger.") and (not trigger_type or n["type"] == trigger_type)]
    if not starts:
        starts = [n for n in g["nodes"] if n["type"].startswith("trigger.")][:1]
    ctx = Ctx(vars, source, origin)
    bus.emit("command.started", id=cid, name=c["name"])
    try:
        for s in starts[:1]:
            await _exec(g, s, ctx)
        bus.emit("command.finished", id=cid, name=c["name"], ok=True)
        _log_run(c, trigger_type, source, t0, True)
        return {"ok": True, "said": ctx.said, "vars": {k: v for k, v in ctx.vars.items() if k != "clipboard"}}
    except Exception as e:
        bus.emit("command.finished", id=cid, name=c["name"], ok=False, error=str(e))
        _log_run(c, trigger_type, source, t0, False, str(e))
        bus.emit("notify", title=f"Команда «{c['name']}»", text=f"Ошибка: {e}")
        return {"error": str(e)}


# --- триггеры ---

def _norm(s):
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[^\w\s{}]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# слова-связки, которые люди добавляют к команде: «ну открой-ка, пожалуйста, майнкрафт»
FILLER = {"пожалуйста", "ну", "а", "давай", "ка", "плиз", "быстро", "мне", "нам", "слушай", "эй"}
_LAT = str.maketrans({"а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
                      "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
                      "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
                      "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya"})


def _sound(w):
    """Грубый «звуковой» ключ слова: кириллица и латиница сводятся к одному виду (Minecraft ≈ майнкрафт)."""
    w = w.translate(_LAT)
    for a, b in (("ck", "k"), ("c", "k"), ("ph", "f"), ("y", "i"), ("j", "i"), ("w", "v"), ("x", "ks"), ("q", "k")):
        w = w.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", w)


def _same_word(a, b):
    """Одно слово в разных формах: общее начало (основа) или похожее звучание."""
    if a == b:
        return True
    pre = 0
    while pre < min(len(a), len(b)) and a[pre] == b[pre]:
        pre += 1
    if pre >= 3 and pre >= min(len(a), len(b)) - 2:                  # открой / откроем / откройте
        return True
    return difflib.SequenceMatcher(None, _sound(a), _sound(b)).ratio() >= 0.8


def _fuzzy(text_words, phrase_words):
    """Фраза без переменных: те же слова по порядку, лишние — только связки."""
    words = [w for w in text_words if w not in FILLER]
    return len(words) == len(phrase_words) and all(_same_word(a, b) for a, b in zip(words, phrase_words))


def _stem_pattern(ph):
    """Шаблон с переменными: неизменные слова — по основе («включи» ловит «включите», «включим»)."""
    out = []
    for part in re.split(r"(\{[\wа-яё]+\})", ph):
        if part.startswith("{"):
            out.append(f"(?P<{part[1:-1]}>.+)")
            continue
        for w in part.split():
            out.append(re.escape(w[:max(3, len(w) - 2)]) + r"\w*")
    return "^(?:(?:" + "|".join(FILLER) + r")\s+)*" + r"\s+".join(out) + "$"


def match_phrase(text):
    """Фраза команды срабатывает без ИИ — мгновенно. Возвращает (id, переменные) или None."""
    t = _norm(text)
    for c in all_commands():
        if not c["enabled"]:
            continue
        for n in c["graph"]["nodes"]:
            if n["type"] != "trigger.phrase":
                continue
            for ph in str(n["params"].get("phrases", "")).split(";"):
                ph = _norm(ph)
                if not ph:
                    continue
                pat = "^" + re.sub(r"\\\{([\wа-яё]+)\\\}", r"(?P<\1>.+)", re.escape(ph)) + "$"
                try:
                    m = re.match(pat, t) or re.match(_stem_pattern(ph), t)
                except re.error:
                    m = None
                if m:
                    return c["id"], m.groupdict()
                if "{" not in ph and _fuzzy(t.split(), ph.split()):
                    return c["id"], {}
    return None


def phrase_hints():
    """Готовые команды хозяина — для подсказки ИИ: если просьба по смыслу та же, пусть зовёт run_command."""
    out = []
    for c in all_commands():
        if not c["enabled"]:
            continue
        phs = [p.strip() for n in c["graph"]["nodes"] if n["type"] == "trigger.phrase"
               for p in str(n["params"].get("phrases", "")).split(";") if p.strip() and "{" not in p]   # с {переменными} ИИ не передаст значение
        if phs:
            out.append(f"- id {c['id']} «{c['name']}»: " + "; ".join(phs))
    return "\n".join(out)


def hotkeys():
    out = []
    for c in all_commands():
        if c["enabled"]:
            for n in c["graph"]["nodes"]:
                if n["type"] == "trigger.hotkey" and n["params"].get("accelerator"):
                    out.append({"id": c["id"], "accelerator": n["params"]["accelerator"], "name": c["name"]})
    return out


async def on_hotkey(acc):
    for h in hotkeys():
        if h["accelerator"].lower() == acc.lower():
            await run_command(h["id"], "trigger.hotkey", source="hotkey")


_last_fired = {}


@scheduler.on_tick
async def _schedule_tick(now):
    d = dt.datetime.fromtimestamp(now)
    hm, day = d.strftime("%H:%M"), DAYS[d.weekday()]
    for c in all_commands():
        if not c["enabled"]:
            continue
        for n in c["graph"]["nodes"]:
            if n["type"] != "trigger.schedule":
                continue
            p = n["params"]
            days = [x.strip() for x in str(p.get("days", ",".join(DAYS))).lower().split(",")]
            key = (c["id"], n["id"], d.strftime("%Y-%m-%d %H:%M"))
            if p.get("time") == hm and day in days and key not in _last_fired:
                _last_fired[key] = True
                asyncio.create_task(run_command(c["id"], "trigger.schedule", source="schedule"))


async def on_startup():
    for c in all_commands():
        if c["enabled"] and any(n["type"] == "trigger.startup" for n in c["graph"]["nodes"]):
            asyncio.create_task(run_command(c["id"], "trigger.startup", source="startup"))


# --- шаблоны: готовые сценарии в один клик (совет ИИ: «простой сценарий — за 15 секунд») ---
def _t(type_, **params):
    return {"type": type_, "params": {k: str(v) if not isinstance(v, str) else v for k, v in params.items()}}


TEMPLATES = [
    # --- сценарии: одна фраза — набор действий. Программы в шагах — примеры, их меняют под себя в редакторе ---
    {"name": "Рабочий режим", "group": "Сценарии", "icon": "💼",
     "desc": "«Рабочий режим» — открывает рабочие программы, тёмная тема, фокус 50 минут, она молчит.",
     "trigger": _t("trigger.phrase", phrases="рабочий режим; за работу; начинаю работать"),
     "steps": [_t("act.open_app", name="Telegram"), _t("act.theme", theme="night_fox"), _t("act.quiet", minutes="50"),
               _t("act.focus", minutes="50"), _t("act.say", text="Рабочий режим. Пятьдесят минут тишины — потом перерыв.")]},
    {"name": "Игровой вечер", "group": "Сценарии", "icon": "🕹️",
     "desc": "«Игровой вечер» — Steam, громкость 60, она молчит 3 часа (игровой режим спрячет её сам).",
     "trigger": _t("trigger.phrase", phrases="игровой вечер; поиграем; запускай стим"),
     "steps": [_t("act.open_app", name="Steam"), _t("act.volume", set="60"), _t("act.quiet", minutes="180"),
               _t("act.say", text="Удачной игры. Позовёте — откликнусь.")]},
    {"name": "Кино", "group": "Сценарии", "icon": "🍿",
     "desc": "«Режим кино» — громкость 70, тишина на 2 часа; она сядет смотреть вместе с вами.",
     "trigger": _t("trigger.phrase", phrases="режим кино; смотрим кино; включи кино"),
     "steps": [_t("act.volume", set="70"), _t("act.quiet", minutes="120"), _t("act.say", text="Попкорн с вас. Я рядом.")]},
    {"name": "Отбой", "group": "Сценарии", "icon": "😴",
     "desc": "«Отбой» — напоминает сохраниться, будильник на 8:00, через 20 секунд гасит экран.",
     "trigger": _t("trigger.phrase", phrases="отбой; спокойной ночи; я спать"),
     "steps": [_t("act.say", text="Сохраните всё, что открыто. Будильник на восемь — спокойной ночи."),
               _t("act.tool", name="add_reminder", args='{"text": "Подъём! Доброе утро", "when": "08:00"}'),
               _t("act.media", action="pause"), _t("flow.delay", seconds="20"), _t("act.monitor_off")]},
    {"name": "Доброе утро", "group": "Каждый день", "icon": "☀️", "desc": "В 9:00 по будням — короткая сводка дня: погода, дела, напоминания.",
     "trigger": _t("trigger.schedule", time="09:00", days="пн,вт,ср,чт,пт"),
     "steps": [_t("act.chat", text="Дай короткую сводку на сегодня: погода, мои дела и напоминания. Коротко, 2-3 фразы.")]},
    {"name": "Режим игры", "group": "Игры", "icon": "🎮", "desc": "«Режим игры» — она молчит 3 часа (кроме срочного в игре).",
     "trigger": _t("trigger.phrase", phrases="режим игры; я играю"),
     "steps": [_t("act.tool", name="chatter_mode", args='{"minutes": 180}'), _t("act.say", text="Хорошо, молчу. Удачной игры.")]},
    {"name": "Перевести выделенное", "group": "Текст", "icon": "🌐", "desc": "Alt+Shift+Y — копирует выделенный текст, переводит и кладёт перевод в буфер.",
     "trigger": _t("trigger.hotkey", accelerator="Alt+Shift+Y"),
     "steps": [_t("act.keys", combo="ctrl+c"), _t("flow.delay", seconds=0.4),
               _t("act.ai", prompt="Переведи на русский (если текст уже русский — на английский). Выведи только перевод: {clipboard}"),
               _t("act.clipboard", text="{ai}"), _t("act.notify", title="Перевод в буфере", text="{ai}")]},
    {"name": "Что на экране", "group": "Текст", "icon": "🔍", "desc": "«Что на экране» — читает текст активного окна и коротко пересказывает.",
     "trigger": _t("trigger.phrase", phrases="что на экране; прочитай экран; что тут написано"),
     "steps": [_t("act.chat", text="Прочитай текст в моём активном окне (read_screen) и коротко перескажи главное.")]},
    {"name": "Фокус 25 минут", "group": "Работа", "icon": "🍅", "desc": "«Фокус» — помодоро: 25 минут она молчит, потом перерыв.",
     "trigger": _t("trigger.phrase", phrases="фокус; запусти фокус"),
     "steps": [_t("act.tool", name="focus_start", args='{"minutes": 25}')]},
    {"name": "Музыка для работы", "group": "Медиа", "icon": "🎧", "desc": "«Музыку для работы» — лоу-фай на YouTube.",
     "trigger": _t("trigger.phrase", phrases="музыку для работы; включи лоуфай"),
     "steps": [_t("act.youtube", query="lofi hip hop radio")]},
    {"name": "Пора спать", "group": "Каждый день", "icon": "🌙", "desc": "В час ночи — напоминает, что пора спать.",
     "trigger": _t("trigger.schedule", time="01:00", days="пн,вт,ср,чт,пт,сб,вс"),
     "steps": [_t("act.say", text="Уже час ночи. Пора спать — завтра скажете спасибо.")]},
    {"name": "Я ухожу", "group": "Каждый день", "icon": "🚪", "desc": "«Я ухожу» — пауза плеера и блокировка ПК (спросит подтверждение).",
     "trigger": _t("trigger.phrase", phrases="я ухожу; я ушёл"),
     "steps": [_t("act.media", action="pause"), _t("act.say", text="Идите. Присмотрю за всем."), _t("act.lock")]},
    {"name": "Загуглить", "group": "Интернет", "icon": "🔎", "desc": "«Загугли котики» — открывает поиск в браузере (поисковик — в настройках).",
     "trigger": _t("trigger.phrase", phrases="загугли {запрос}; погугли {запрос}; найди в браузере {запрос}"),
     "steps": [_t("act.tool", name="search_in_browser", args='{"query": "{запрос}"}')]},
    {"name": "Громкость числом", "group": "Медиа", "icon": "🔊", "desc": "«Громкость 30» — ставит громкость системы мгновенно, без ИИ.",
     "trigger": _t("trigger.phrase", phrases="громкость {n}; сделай громкость {n}"),
     "steps": [_t("act.volume", set="{n}")]},
    {"name": "У меня созвон", "group": "Работа", "icon": "🎧", "desc": "«У меня созвон» — пауза музыки, громкость 40, она молчит час.",
     "trigger": _t("trigger.phrase", phrases="у меня созвон; у меня встреча"),
     "steps": [_t("act.media", action="play_pause"), _t("act.volume", set="40"),
               _t("act.tool", name="chatter_mode", args='{"minutes": 60}'), _t("act.notify", title="Тихий режим", text="Молчу час — удачной встречи")]},
    {"name": "Перерыв на чай", "group": "Работа", "icon": "🍵", "desc": "«Перерыв» — пауза музыки и напоминание вернуться через 10 минут.",
     "trigger": _t("trigger.phrase", phrases="перерыв; перерыв на чай"),
     "steps": [_t("act.media", action="play_pause"),
               _t("act.tool", name="add_reminder", args='{"text": "Перерыв окончен — за работу", "in_minutes": 10}'),
               _t("act.say", text="Десять минут отдыха. Я напомню.")]},
    {"name": "Итог дня", "group": "Каждый день", "icon": "📒", "desc": "В 19:00 по будням — что из задач сделано и что перенести на завтра.",
     "trigger": _t("trigger.schedule", time="19:00", days="пн,вт,ср,чт,пт"),
     "steps": [_t("act.chat", text="Подведи итог дня: какие задачи выполнены, какие остались, что стоит перенести на завтра. Коротко.")]},
    {"name": "Объяснить выделенное", "group": "Текст", "icon": "💡", "desc": "Alt+Shift+E — объясняет выделенный текст простыми словами.",
     "trigger": _t("trigger.hotkey", accelerator="Alt+Shift+E"),
     "steps": [_t("act.keys", combo="ctrl+c"), _t("flow.delay", seconds=0.4),
               _t("act.ai", prompt="Объясни простыми словами в 2-3 предложениях: {clipboard}"),
               _t("act.notify", title="Объяснение", text="{ai}"), _t("act.say", text="{ai}")]},
    {"name": "Экран на телефон", "group": "Телефон", "icon": "📱", "desc": "«Скинь экран» — снимок экрана в ваш Telegram-бот.",
     "trigger": _t("trigger.phrase", phrases="скинь экран; скриншот на телефон"),
     "steps": [_t("act.chat", text="Сделай снимок экрана и отправь мне на телефон.")]},
]


@routes.get("/api/commands/templates")
async def api_templates(_):
    return web.json_response([{k: t[k] for k in ("name", "group", "icon", "desc")} for t in TEMPLATES])


@routes.post("/api/commands/from-template")
async def api_from_template(r):
    i = int((await r.json()).get("i", -1))
    if not 0 <= i < len(TEMPLATES):
        return web.json_response({"error": "нет такого шаблона"}, status=400)
    t = TEMPLATES[i]
    cid = save_command({"name": t["name"], "grp": t["group"], "enabled": 1, "graph": linear_graph(t["trigger"], t["steps"])})
    bus.emit("commands.changed")
    return web.json_response({"id": cid})


@routes.get("/api/commands/log")
async def api_log(_):
    from .config import DATA
    try:
        return web.json_response(json.loads((DATA / "commands_log.json").read_text(encoding="utf-8")))
    except Exception:
        return web.json_response([])


# --- инструменты для ИИ: создавать и запускать команды голосом ---

STEP_HELP = "; ".join(f"{k}({', '.join(v[2])})" for k, v in NODE_TYPES.items() if k.startswith("act."))


@tools.tool("create_command", "Создать пользовательскую команду-автоматизацию: триггер + последовательность шагов. "
            f"Типы шагов и их params: {STEP_HELP}; flow.delay(seconds). В текстах можно {{clipboard}}, {{ai}}, {{time}}.",
            {"name": tools.S("Название команды"),
             "phrase": tools.S("Голосовая фраза-триггер (можно с переменной: 'найди на ютубе {запрос}')"),
             "hotkey": tools.S("Горячая клавиша в формате Electron, например 'Alt+Shift+T' (необязательно)"),
             "steps": {"type": "array", "items": {"type": "object", "properties": {
                 "type": {"type": "string"}, "params": {"type": "object"}}, "required": ["type"]}}},
            ["name", "steps"])
def create_command(name, steps, phrase=None, hotkey=None):
    if hotkey:
        trig = {"type": "trigger.hotkey", "params": {"accelerator": hotkey}}
    elif phrase:
        trig = {"type": "trigger.phrase", "params": {"phrases": phrase}}
    else:
        trig = {"type": "trigger.manual", "params": {}}
    bad = [s["type"] for s in steps if s["type"] not in NODE_TYPES]
    if bad:
        return {"error": f"неизвестные шаги: {bad}"}
    g = linear_graph(trig, steps)
    if phrase and hotkey:
        g["nodes"].append({"id": "nt", "type": "trigger.phrase", "params": {"phrases": phrase}, "x": 40, "y": 240})
        g["edges"].append({"from": "nt", "port": "out", "to": "n1"})
    cid = save_command({"name": name, "grp": "Созданные ИИ", "graph": g})
    return {"ok": True, "id": cid}


@tools.tool("list_commands", "Список пользовательских команд.")
def list_commands():
    out = []
    for c in all_commands():
        trig = [f"{n['type'].split('.')[1]}: {list(n['params'].values())[0] if n['params'] else ''}"
                for n in c["graph"]["nodes"] if n["type"].startswith("trigger.")]
        out.append({"id": c["id"], "name": c["name"], "enabled": bool(c["enabled"]), "triggers": trig})
    return out


@tools.tool("run_command", "Запустить пользовательскую команду по id.", {"id": tools.I("id команды")}, ["id"])
async def run_command_tool(id):
    from .agent import current_source
    return await run_command(id, source="ai", origin=current_source.get())


# --- REST для редактора ---

@routes.get("/api/commands")
async def api_list(_):
    return web.json_response(all_commands())


@routes.get("/api/commands/types")
async def api_types(_):
    return web.json_response({"types": {k: {"group": v[0], "title": v[1], "params": v[2], "outputs": v[3]}
                                        for k, v in NODE_TYPES.items()}, "if_checks": IF_CHECKS})


@routes.get("/api/commands/hotkeys")
async def api_hotkeys(_):
    return web.json_response(hotkeys())


@routes.post("/api/commands")
async def api_save(r):
    return web.json_response({"id": save_command(await r.json())})


@routes.delete("/api/commands/{id}")
async def api_delete(r):
    db.ex("DELETE FROM commands WHERE id=?", (int(r.match_info["id"]),))
    bus.emit("commands.changed")
    return web.json_response({"ok": True})


@routes.post("/api/commands/{id}/run")
async def api_run(r):
    asyncio.create_task(run_command(int(r.match_info["id"]), source="ui"))
    return web.json_response({"ok": True})


def seed():
    # старое приветствие говорило «доброе утро» и в полночь
    db.ex("UPDATE commands SET graph=replace(graph, 'Хмпф. Доброе утро, господин.', 'Хмпф. {greet}, господин.')")
    if db.one("SELECT id FROM commands LIMIT 1"):
        return
    from .i18n import lang
    if lang() != "ru":                  # фразы-триггеры заготовок — русские; остальным — шаблоны во вкладке «Команды»
        return
    examples = [
        ("Поздороваться", "Без группы", {"type": "trigger.phrase", "params": {"phrases": "привет; доброе утро"}},
         [{"type": "act.say", "params": {"text": "{greet}! Рада вас слышать."}}]),
        ("Заблокировать компьютер", "Без группы", {"type": "trigger.phrase", "params": {"phrases": "заблокируй компьютер; я отошёл"}},
         [{"type": "act.say", "params": {"text": "Блокирую. До скорого!"}}, {"type": "act.lock"}]),
        ("YouTube по фразе", "Медиа", {"type": "trigger.phrase", "params": {"phrases": "включи на ютубе {запрос}; найди на ютубе {запрос}"}},
         [{"type": "act.youtube", "params": {"query": "{запрос}"}}]),
        ("Перевод буфера обмена", "ИИ", {"type": "trigger.hotkey", "params": {"accelerator": "Alt+Shift+T"}},
         [{"type": "act.ai", "params": {"prompt": "Переведи на русский (если уже русский — на английский). Выведи только перевод:\n{clipboard}"}},
          {"type": "act.clipboard", "params": {"text": "{ai}"}},
          {"type": "act.notify", "params": {"title": "Перевод в буфере", "text": "{ai}"}}]),
    ]
    for name, grp, trig, steps in examples:
        save_command({"name": name, "grp": grp, "graph": linear_graph(trig, steps)})
