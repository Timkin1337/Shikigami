"""Режиссёр поведения: {name} сама замечает, что происходит, и иногда заговаривает первой.

Следит за бездействием (GetLastInputInfo), временем суток и полноэкранными окнами. Правила вежливости:
не чаще одной реплики по своей инициативе в 15 минут (утро и «с возвращением» — вне очереди),
молчит, пока на весь экран открыта игра или фильм, пока она сама говорит или думает.
"""
import asyncio
import ctypes
import datetime as dt
import json
import time

from . import bus
from .config import config, DATA

STATE_FILE = DATA / "behavior.json"
AWAY_AFTER = 5 * 60          # столько без мыши и клавиатуры — «отошёл»
COOLDOWN = 15 * 60           # пауза между инициативными репликами
BREAK_AFTER = 2 * 3600       # столько подряд за компьютером — пора размяться


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def idle_seconds():
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return max(0, (ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000


def windows_quiet():
    """Windows сама просит не беспокоить: «Не беспокоить»/фокусировка, презентация, полноэкранное приложение
    (SHQueryUserNotificationState: BUSY, D3D, PRESENTATION, QUIET_TIME) или уведомления выключены целиком."""
    st = ctypes.c_int(0)
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(st)) == 0 and st.value in (2, 3, 4, 6):
            return True
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings") as k:
            return winreg.QueryValueEx(k, "NOC_GLOBAL_SETTING_TOASTS_ENABLED")[0] == 0
    except OSError:
        return False


def fullscreen_app():
    """Активное окно на весь монитор — игра или фильм. Тогда не мешаем."""
    import win32api
    import win32gui
    h = win32gui.GetForegroundWindow()
    if not h or win32gui.GetClassName(h) in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False
    try:
        rect = win32gui.GetWindowRect(h)
        mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(h))["Monitor"]
    except Exception:
        return False
    return tuple(rect) == tuple(mon)


BIRTHDAY = ["С днём рождения, {user}! Не то чтобы я весь день ждала, когда вы проснётесь… Задуйте свечку!",
            "{user}, с днём рождения! Торт настоящий только у меня в руках, но желание загадывайте по-настоящему.",
            "Сегодня ваш день, {user}. С днём рождения! Я буду вести себя почти хорошо. Почти."]
NEW_YEAR = ["С Новым годом! Пусть в этом году всё получится — а я присмотрю.",
            "Полночь! С Новым годом, господин! Загадали желание? Я — да, но не скажу.",
            "Ура, Новый год! Спасибо, что провели этот год со мной."]


def is_birthday():
    import re
    m = re.match(r"^(\d{1,2})\.(\d{1,2})", str(config.get("user_birthday") or "").strip())
    today = dt.date.today()
    return bool(m) and int(m.group(1)) == today.day and int(m.group(2)) == today.month


LINES = {
    "back_short": [("О, вернулись. Я и не заметила, что вас не было.", "shy"),
                   ("С возвращением, господин. Чай остыл, между прочим.", "neutral"),
                   ("Ну наконец-то. В смысле — работайте, я тут.", "shy")],
    "back_long": [("Где вы пропадали столько времени? Не то чтобы я ждала…", "shy"),
                  ("Явились. Я уже успела всё перемыть дважды. Хмпф.", "angry"),
                  ("С возвращением. Просто проверяю, что вы живы.", "shy")],
    "night": [("{user}-сан, уже {time}. Нормальные люди спят. И лисы тоже.", "sad"),
              ("Господин, если вы не ляжете, завтра будете как варёный тофу. Я предупредила.", "angry"),
              ("Уже поздно… Может, хватит на сегодня? Я посижу рядом, пока закрываете окна.", "sad")],
    "break": [("Вы два часа не вставали. Встаньте, потянитесь. Это не просьба.", "angry"),
              ("Перерыв. Пять минут. Глаза скажут спасибо.", "neutral"),
              ("Хмпф. Если вы прирастёте к стулу, кто будет пить мой чай? Разомнитесь.", "shy")],
    "lunch": [("Обед, господин. Голодный хозяин — позор для горничной.", "angry"),
              ("Уже второй час дня. Вы ели? Только не врите.", "neutral")],
}


class Director:
    def __init__(self):
        self.away = False
        self.away_since = 0.0
        self.session_start = time.time()
        self.last_talk = 0.0
        self.mem = self._load()

    def _load(self):
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self):
        STATE_FILE.write_text(json.dumps(self.mem, ensure_ascii=False), encoding="utf-8")

    def busy(self):
        from . import voice, agent, plugins
        focus = plugins.loaded.get("focus", {}).get("module")         # идёт помодоро — не отвлекаем
        return voice.state.get("speaking") or agent._running or fullscreen_app() or (focus and focus.active())

    async def say(self, text, emotion="neutral", force=False):
        now = time.time()
        if not force and now - self.last_talk < COOLDOWN:
            return False
        if self.busy():
            return False
        self.last_talk = now
        bus.emit("avatar.state", state="idle", emotion=emotion)
        from . import chatter
        if config.get("behavior.voice", True) and chatter.allow():
            from . import voice
            asyncio.create_task(voice.speak(text, emotion=emotion))
        else:
            bus.emit("speech", text=text, url=None, emotion=emotion)
        return True

    def line(self, kind):
        from . import lines
        text, emo = lines.pick(f"behavior.{kind}")        # вариант в характере человека (банк фраз) или исходный
        return text.format(user=config["user_name"] or config.get("address") or "господин", time=dt.datetime.now().strftime("%H:%M")), emo

    async def tick(self):
        idle = await asyncio.to_thread(idle_seconds)
        now, today = dt.datetime.now(), dt.date.today().isoformat()
        if idle >= AWAY_AFTER and not self.away:
            self.away, self.away_since = True, time.time() - idle
        elif self.away and idle < 5:
            self.away = False
            gone = time.time() - self.away_since
            self.session_start = time.time()
            if now.hour >= 5 and self.mem.get("morning") != today:
                return await self.morning(today)
            await self.say(*self.line("back_long" if gone > 2 * 3600 else "back_short"), force=gone > 20 * 60)
            return
        if now.month == 1 and now.day == 1 and now.hour == 0 and idle < 600:
            return await self.new_year(now)
        if idle > 60:
            return
        # первая активность за день (после 5 утра) — приветствие с погодой и делами
        if now.hour >= 5 and self.mem.get("morning") != today:
            return await self.morning(today)
        if 1 <= now.hour < 5 and time.time() - self.mem.get("night", 0) > 3600:
            if await self.say(*self.line("night")):
                self.mem["night"] = time.time()
                self._save()
            return
        if time.time() - self.session_start > BREAK_AFTER and time.time() - self.mem.get("break", 0) > BREAK_AFTER:
            if await self.say(*self.line("break")):
                self.mem["break"] = time.time()
                self._save()
            return
        if 13 <= now.hour < 14 and self.mem.get("lunch") != today:
            if await self.say(*self.line("lunch")):
                self.mem["lunch"] = today
                self._save()

    async def morning(self, today):
        if self.busy():
            return
        self.mem["morning"] = today
        self._save()
        if is_birthday():                                    # день рождения — сначала поздравление с тортом
            bus.emit("avatar.pastime", name="cake", force=True)
            from . import lines
            await self.say(lines.pick("behavior.birthday")[0].format(user=config["user_name"] or config.get("address") or "господин"),
                           "happy", force=True)
            return
        text = await morning_text()
        await self.say(text, "happy", force=True)

    async def new_year(self, now):
        """Ровно в полночь 1 января — конфетти и поздравление (раз в год)."""
        if self.mem.get("new_year") == now.year:
            return
        self.mem["new_year"] = now.year
        self._save()
        bus.emit("avatar.pastime", name="confetti", force=True)
        from . import lines
        await self.say(lines.pick("behavior.new_year")[0], "happy", force=True)


async def morning_text():
    """Утреннее приветствие: погода и дела на сегодня, своими словами — а если модель недоступна, шаблоном."""
    from .tools import everyday
    from . import llm, persona
    facts = []
    try:
        w = await everyday.weather(days=1)
        if "now" in w:
            d0 = w["days"][0]
            facts.append(f"погода в городе {w['city']}: сейчас {w['now']['temp']}°, {w['now']['sky']}, "
                         f"днём до {d0['max']}°, вероятность дождя {d0['rain_chance']}%")
    except Exception:
        pass
    end = dt.datetime.combine(dt.date.today(), dt.time(23, 59)).timestamp()
    rem = [r["text"] for r in everyday.db.q("SELECT text FROM reminders WHERE done=0 AND due<=?", (end,))][:4]
    tasks = [t["text"] for t in everyday.list_tasks()][:4]
    events = [e["title"] + (f" в {e['start'][-5:]}" if e["start"] else "") for e in everyday.list_events(1)][:4]
    if rem:
        facts.append("напоминания на сегодня: " + "; ".join(rem))
    if events:
        facts.append("события сегодня: " + "; ".join(events))
    if tasks:
        facts.append("открытые задачи: " + "; ".join(tasks))
    weekend = dt.date.today().weekday() >= 5
    h = dt.datetime.now().hour
    part = "Утро" if h < 12 else "День" if h < 17 else "Вечер" if h < 23 else "Ночь"
    situation = (f"{part}, хозяин впервые за сегодня сел за компьютер" + (" (сегодня выходной)" if weekend else "") + ". "
                 + ("Факты: " + ". ".join(facts) + "." if facts else "Дел и напоминаний на сегодня нет."))
    try:
        text = await llm.simple(
            situation + " Поздоровайся в образе и коротко (2-3 предложения) перескажи главное из фактов. "
            "Ничего не выдумывай сверх фактов.", system=persona.system_prompt(voice=True), max_tokens=220)
        if text.strip():
            return text.strip()
    except Exception:
        pass
    hello = {"Утро": "Доброе утро", "День": "Добрый день", "Вечер": "Добрый вечер"}.get(part, "Доброй ночи") + ", господин."
    hello += " Выходной, между прочим." if weekend else ""
    return " ".join([hello] + [f.capitalize() + "." for f in facts[:2]])


director = Director()


game = {"on": False, "seen": 0}


def _own_window():
    """Наше окно (Shikigami развернули на весь экран) — не игра."""
    import win32gui
    import win32process
    import os
    h = win32gui.GetForegroundWindow()
    try:
        _, pid = win32process.GetWindowThreadProcessId(h)
        import psutil
        return psutil.Process(pid).name().lower() in ("shikigami.exe", "electron.exe") or pid == os.getpid()
    except Exception:
        return False


async def game_watch():
    """Игровой режим: что-то на весь экран (игра, фильм) дольше 6 с — аватар прячется и не рисует 3D,
    пока окно не уйдёт с полного экрана. Видеокарта и кадры — игре. Отключается behavior.game_mode."""
    await asyncio.sleep(15)
    while True:
        try:
            full = config.get("behavior.game_mode", True) and await asyncio.to_thread(lambda: fullscreen_app() and not _own_window())
            game["seen"] = game["seen"] + 1 if full else 0
            on = game["seen"] >= 2
            if on != game["on"]:
                game["on"] = on
                print("игровой режим:", "вкл — аватар спрятан" if on else "выкл", flush=True)
                bus.emit("game.mode", on=on)
                if on:                          # голос с эмоциями держит ~3 ГБ видеопамяти — отдаём их игре
                    from . import voice
                    eng = voice.state.get("tts")
                    if eng is not None and getattr(eng, "espeech", None) and eng.espeech.p:
                        eng.espeech.stop()
                        print("эмоциональный голос выгружен на время игры", flush=True)
        except Exception as e:
            print("game_watch:", repr(e), flush=True)
        await asyncio.sleep(3)


def _register_lines():
    from . import lines
    lines.register("behavior", LINES, {
        "back_short": "человек вернулся к компьютеру после короткой отлучки",
        "back_long": "человек вернулся к компьютеру после долгого отсутствия (часы)",
        "night": "глубокая ночь ({time}), человек всё ещё за компьютером — пора спать",
        "break": "человек два часа не вставал из-за компьютера — пора размяться",
        "lunch": "время обеда, напоминает поесть"})
    lines.register("behavior", {"birthday": BIRTHDAY, "new_year": NEW_YEAR}, {
        "birthday": "у человека сегодня день рождения ({user} — его имя), утреннее поздравление",
        "new_year": "полночь 1 января, поздравление с Новым годом"}, emo="happy")


_register_lines()


async def lines_watch():
    """Банк фраз под характер: нет или устарел (сменили характер, язык) — написать, когда есть «мозг»."""
    from . import lines, llm
    await asyncio.sleep(40)
    while True:
        try:
            if config.get("setup_done", True) and not lines.ready() and not lines.busy() and not llm.no_brain():
                await lines.regenerate()
        except Exception as e:
            print("lines_watch:", repr(e), flush=True)
        await asyncio.sleep(300)


async def run():
    asyncio.get_running_loop().create_task(game_watch())
    from . import friendship
    friendship.start()
    asyncio.get_running_loop().create_task(lines_watch())
    await asyncio.sleep(20)          # пусть ядро и окна спокойно поднимутся
    while True:
        try:
            if config.get("behavior.enabled", True) and config.get("setup_done", True):   # до мастера — молчит
                await director.tick()
        except Exception as e:
            print("behavior:", repr(e), flush=True)
        await asyncio.sleep(5)
