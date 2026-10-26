"""Майнкрафт-компаньон: {name} следит за игрой по логу и комментирует голосом.

- Пишете в игровой чат «{name}, как скрафтить поршень?» — отвечает голосом (и может в чат).
- Смерть, достижение, заход в мир — короткая реплика в образе.
- Инструмент minecraft_chat: она сама пишет в игровой чат (нажимает T, печатает, Enter).
Работает с любой сборкой, модов не нужно: только latest.log.
"""
import asyncio
import random
import re
import time
from collections import deque
from pathlib import Path

from aiohttp import web

from core import bus, llm, persona
from core.config import config
from core.tools import tool, S

TITLE = "Майнкрафт"
DESCRIPTION = "{name} следит за игрой по логу: комментирует смерти и достижения, отвечает на «{name}, …» из чата."

import os as _os

_APP, _HOME = Path(_os.environ.get("APPDATA", "")), Path.home()
# где лежат логи у популярных лаунчеров; своя папка — «Настройки → Майнкрафт → Папка игры»
LOG_GLOBS = [(_APP / ".minecraft", ["logs/latest.log", "versions/*/logs/latest.log"]),          # официальный, TLauncher
             (_HOME / "curseforge" / "minecraft" / "Instances", ["*/logs/latest.log"]),
             (_APP / "PrismLauncher" / "instances", ["*/.minecraft/logs/latest.log", "*/minecraft/logs/latest.log"]),
             (_APP / "ModrinthApp" / "profiles", ["*/logs/latest.log"])]
LINE = re.compile(r"^\[(\d\d:\d\d:\d\d)\] \[([^\]]+)\]: (?:\[System\] )?\[CHAT\] (.*)$")
DEATH = re.compile(r"(погиб|был убит|была убита|убит|умер|утонул|сгорел|разбил|взорван|упал|задохнул|замёрз|"
                   r"превратил|расплющ|пронзён|застрелен|was slain|died|drowned|blew up|fell|burned|was shot|was killed)", re.I)
ADVANCEMENT = re.compile(r"(получил достижение|выполнил испытание|достиг цели|has made the advancement|"
                         r"has completed the challenge|has reached the goal)\s*\[(.+?)\]", re.I)

st = {"log": None, "pos": 0, "player": None, "events": deque(maxlen=30), "last_quip": 0, "greeted": 0}


def find_log():
    custom = config.get("minecraft.log")
    if custom and Path(custom).exists():
        return Path(custom)
    roots = list(LOG_GLOBS)
    if config.get("minecraft.game_dir"):
        roots.insert(0, (Path(config.get("minecraft.game_dir")), ["logs/latest.log", "versions/*/logs/latest.log", "*/logs/latest.log"]))
    logs = [p for root, pats in roots if root.is_dir() for pat in pats for p in root.glob(pat)]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


async def tail():
    while True:
        try:
            log = find_log()
            if log != st["log"]:
                st["log"], st["pos"] = log, log.stat().st_size if log else 0   # новое — с конца, старое не пересказываем
            if log:
                size = log.stat().st_size
                if size < st["pos"]:      # игра перезапущена, лог начат заново
                    st["pos"] = 0
                if size > st["pos"]:
                    with open(log, "rb") as f:
                        f.seek(st["pos"])
                        data = f.read()
                    st["pos"] += len(data)
                    for raw in data.decode("utf-8", "replace").splitlines():
                        await on_line(raw)
        except Exception as e:
            print("minecraft:", e, flush=True)
        await asyncio.sleep(0.7)


async def on_line(raw):
    m = re.search(r"Setting user: (\S+)", raw)
    if m:
        st["player"] = m.group(1)
        return
    if re.search(r"\[Server thread/INFO\]: Preparing spawn area|Done \(.*\)! For help", raw) and time.time() - st["greeted"] > 1800:
        st["greeted"] = time.time()
        await quip("Господин только что зашёл в свой мир в Майнкрафте.", "happy")
        return
    lan = LAN.search(raw)
    if lan and ('Started serving' in raw or '[CHAT]' in raw):    # мир открыт для сети — {name} заходит сама
        asyncio.create_task(_maybe_join(int(lan.group(1) or lan.group(2))))
    m = LINE.match(raw)
    if not m:
        return
    text = m.group(3).strip()
    st["events"].append(f"{m.group(1)} {text}")
    me = st["player"] or ""
    chat = re.match(r"^<([^>]+)> (.*)$", text)
    if chat and companion.connected:     # пока {name} в мире, чат слушает она сама
        return
    if chat:
        who, msg = chat.groups()
        rest = _addressed(msg)
        # слушаемся только хозяина: на сервере любой игрок мог бы написать «{name}, нажми win+r…»
        if rest is not None and me and who == me:
            await ask(rest or "Ты звала?")
        return
    if me and text.startswith(me):
        adv = ADVANCEMENT.search(text)
        if adv:
            await quip(f"Господин в Майнкрафте получил достижение «{adv.group(2)}».", "happy", cooldown=40)
        elif DEATH.search(text):
            await quip(f"Господин в Майнкрафте погиб: «{text}».", "angry", cooldown=30)


def _addressed(msg):
    name = config["assistant_name"].lower()
    low = msg.lower().strip()
    for n in (name, config.get("assistant_name") or "", "@" + name):
        if low.startswith(n):
            return msg[len(n):].strip(" ,.!?:")
    return None


async def ask(question):
    from core import agent
    bus.emit("notify", title="Майнкрафт", text=f"«{question}»")
    q = f"(Вопрос из игрового чата Майнкрафта, версия 1.21.11 с модами) {question}"
    _, reply = await agent.handle_user(q, voice=True, source="minecraft")
    if reply and config.get("minecraft.type_replies"):
        short = persona.speech_text(reply)[:240]
        await asyncio.to_thread(_type_in_game, f"[{config['assistant_name']}] {short}")


async def quip(situation, emotion, cooldown=20):
    if not config.get("minecraft.react", True) or time.time() - st["last_quip"] < cooldown:
        return
    st["last_quip"] = time.time()
    try:
        text = await llm.simple(f"{situation} Отреагируй одной короткой репликой (до 15 слов) в своём образе, "
                                f"без ремарок в звёздочках.", system=persona.system_prompt(voice=True),
                                max_tokens=120)
    except Exception:
        return
    from core import voice
    bus.emit("avatar.state", state="idle", emotion=emotion)
    await voice.speak(text.strip(), emotion=emotion)


def _type_in_game(text):
    """Напечатать в чат игры. Раньше при неудачном переключении на окно текст уходил в другое окно, а ответ был
    «ok» — она уверенно говорила «написала в чат». Теперь: не стало активным окно игры — ошибка; написали —
    проверяем по журналу игры, что строка действительно появилась в чате."""
    import win32gui
    from core.tools.apps import _find_window
    from core.tools.system import press_combo, type_unicode
    w = _find_window("Minecraft")
    if not w:
        return {"error": "окно Майнкрафта не найдено — игра не запущена"}
    for _ in range(3):
        try:
            press_combo("alt")                  # Windows отдаёт фокус фоновому процессу только после нажатия клавиши
            win32gui.SetForegroundWindow(w["hwnd"])
        except Exception:
            pass
        time.sleep(0.25)
        if win32gui.GetForegroundWindow() == w["hwnd"]:
            break
    else:
        return {"error": "не получилось переключиться на окно игры — ничего не написала. Пусть хозяин откроет игру и попросит снова"}
    seen = len(st["events"])
    press_combo("t")
    time.sleep(0.25)
    type_unicode(text)
    time.sleep(0.05)
    press_combo("enter")
    if text.startswith("/") or not st.get("log"):
        return {"ok": True, "note": "команда отправлена; проверить её результат по журналу нельзя" if text.startswith("/") else
                "отправлено, но журнал игры не найден — проверить, дошло ли, нельзя"}
    probe = text.strip()[:25]
    for _ in range(12):                         # журнал игры дописывается раз в ~0,5 с
        time.sleep(0.25)
        if any(probe in e for e in list(st["events"])[seen:]):
            return {"ok": True, "in_chat": True}
    return {"error": "напечатала, но в чате игры сообщение не появилось (возможно, было открыто меню) — скажи хозяину честно"}


@tool("minecraft_chat", "Написать сообщение в чат Майнкрафта, где сейчас играет пользователь (или выполнить команду, начав с /).",
      {"text": S("Текст или команда, например '/time set day'")}, ["text"], group="plugin:minecraft")
def minecraft_chat(text):
    return _type_in_game(text)


@tool("minecraft_recent", "Последние события из игры пользователя в Майнкрафте (чат, смерти, достижения).",
      group="plugin:minecraft")
def minecraft_recent():
    bot = (config.get("minecraft.bot_name") or "Shikigami") if companion.connected else None   # companion — ниже, к вызову уже есть
    return {"player": st["player"], "log": str(st["log"]) if st["log"] else None, "bot": bot,
            "events": list(st["events"])[-15:]}


# ===================== {name} — игрок в мире (mineflayer-бот) =====================
# Мир, открытый для сети (LAN), или сервер без модов на блоки: {name} заходит вторым игроком,
# ходит за хозяином, добывает, крафтит, защищает, отдаёт вещи и отвечает в чат. Решает ИИ, руки — бот.
import json as _json
import shutil as _shutil

from core.tools import I as _I

BOT_DIR = Path(__file__).resolve().parents[2] / "games" / "minecraft-bot"
LAN = re.compile(r"Started serving on (\d+)|(?:порт[уе]?|port)\D{0,4}(\d{4,5})", re.I)


class Companion:
    def __init__(self):
        self.proc = None
        self.pending = {}
        self.seq = 0
        self.connected = False
        self.info = {}
        self.chat_id = None
        self.lock = asyncio.Lock()

    async def ensure(self):
        if self.proc and self.proc.returncode is None:
            return
        node = _shutil.which("node")
        if not node:
            raise RuntimeError("не найден Node.js — он нужен, чтобы {name} играла")
        if not (BOT_DIR / "node_modules").exists():
            raise RuntimeError("бот не установлен: в games/minecraft-bot выполните npm install")
        self.proc = await asyncio.create_subprocess_exec(
            node, str(BOT_DIR / "bot.js"), cwd=str(BOT_DIR), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=2**20)
        asyncio.create_task(self._read(self.proc))
        asyncio.create_task(self._errors(self.proc))

    async def call(self, cmd, args=None, timeout=60):
        await self.ensure()
        self.seq += 1
        fut = asyncio.get_running_loop().create_future()
        self.pending[self.seq] = fut
        line = _json.dumps({"id": self.seq, "cmd": cmd, "args": args or {}}, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        await self.proc.stdin.drain()
        msg = await asyncio.wait_for(asyncio.shield(fut), timeout)
        if not msg.get("ok"):
            raise RuntimeError(msg.get("error") or "ошибка бота")
        return msg.get("result")

    async def _read(self, proc):
        async for line in proc.stdout:
            try:
                m = _json.loads(line)
            except ValueError:
                continue
            if "id" in m:
                f = self.pending.pop(m["id"], None)
                if f and not f.done():
                    f.set_result(m)
            elif "event" in m:
                try:
                    await self.on_event(m)
                except Exception as e:
                    print("minecraft bot event:", repr(e), flush=True)
        self.connected = False
        for f in self.pending.values():           # процесс бота умер — никого не держим
            if not f.done():
                f.set_result({"ok": False, "error": "процесс бота завершился"})
        self.pending.clear()

    async def _errors(self, proc):
        async for line in proc.stderr:
            print("[mc-бот]", line.decode("utf-8", "replace").rstrip(), flush=True)

    async def join(self, host="127.0.0.1", port=25565):
        async with self.lock:
            res = await self.call("connect", {"host": host, "port": int(port),
                                              "username": config.get("minecraft.bot_name") or "Shikigami",
                                              "owner": st["player"]}, timeout=40)
            self.connected = True
            self.info = {"host": host, "port": port, **res}
            return res

    async def leave(self):
        if self.connected:
            await self.call("disconnect", timeout=10)
        self.connected = False

    async def say(self, text):
        if self.connected and text:
            await self.call("chat", {"text": persona.speech_text(persona.clean(text))[:480]}, timeout=20)

    # ---------- события из игры ----------
    async def on_event(self, m):
        ev = m["event"]
        if ev == "spawned":
            bus.emit("notify", title="Майнкрафт", text=f"{{name}} зашла в мир ({m.get('version')})")
            await self.say(random.choice(["Я тут, господин. Пишите «{name}, …» — помогу. Или «за мной», «стой», «охраняй».",
                                          "Хмпф. Пришла. Не то чтобы мне было скучно на рабочем столе."]))
        elif ev == "chat":
            asyncio.create_task(self.on_chat(m["username"], m["message"]))
        elif ev == "death":
            await self.say(random.choice(["Ай! Я… вернусь. Наверное.", "Бака! Меня убили! Это всё вы виноваты!"]))
        elif ev == "low_health":
            await self.say("Мне больно! Господин, прикройте!")
        elif ev in ("kicked", "end"):
            was = self.connected
            self.connected = False
            reason = m.get("reason", "")
            if was or ev == "kicked":
                hint = " (мир с модами на блоки не пускает игроков без модов)" if re.search(r"mod|neoforge|forge|registry", reason, re.I) else ""
                bus.emit("notify", title="Майнкрафт", text=f"{{name}} вышла из мира: {reason or 'соединение закрыто'}{hint}")

    async def on_chat(self, who, msg):
        me = st["player"]
        if me and who != me:                       # командует только хозяин
            return
        rest = _addressed(msg)
        if rest is None:
            return
        quick = QUICK.get(re.sub(r"[^\wё ]", "", rest.lower()).strip())
        if quick:                                   # «за мной», «стой» — сразу, без ИИ
            cmd, args, reply = quick
            try:
                await self.call(cmd, {**args, "player": who} if "player" in args else args, timeout=30)
                await self.say(reply)
            except Exception as e:
                await self.say(f"Не выходит: {e}")
            return
        from core import agent, db
        if self.chat_id is None:
            row = db.one("SELECT id FROM chats WHERE title='Майнкрафт'")
            self.chat_id = row["id"] if row else db.new_chat("Майнкрафт")
        q = (f"[Ты сейчас в мире Майнкрафта как игрок рядом с {who}; он пишет тебе в игровой чат. "
             f"Действуй инструментами mc_*.] {rest or 'Ты звала?'}")
        _, reply = await agent.handle_user(q, self.chat_id, voice=True, source="minecraft")
        await self.say(reply)


QUICK = {
    "за мной": ("follow", {"player": None}, "Иду-иду. Не бегите так."),
    "иди за мной": ("follow", {"player": None}, "Иду-иду. Не бегите так."),
    "ко мне": ("come", {"player": None}, "Тут я. Чего звали?"),
    "иди сюда": ("come", {"player": None}, "Тут я. Чего звали?"),
    "стой": ("stop", {}, "Стою. Жду указаний."),
    "стоп": ("stop", {}, "Стою. Жду указаний."),
    "хватит": ("stop", {}, "Ладно-ладно, бросила."),
    "охраняй": ("guard", {"player": None}, "Никто вас не тронет. Пока я рядом."),
    "защищай": ("guard", {"player": None}, "Никто вас не тронет. Пока я рядом."),
}

companion = Companion()


async def _bot(cmd, args=None, timeout=60, done_text=None):
    """Вызов бота из инструмента. Долгое дело не держит ИИ: по таймауту — «ещё делаю», итог — в игровой чат."""
    if not companion.connected:
        return {"error": "{name} сейчас не в мире Майнкрафта: откройте мир для сети (LAN) или позовите mc_join"}
    task = asyncio.ensure_future(companion.call(cmd, args, timeout=600))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        def report(t):
            if t.cancelled():
                return
            text = f"Готово: {done_text or cmd}." if not t.exception() else f"Не вышло: {t.exception()}"
            asyncio.ensure_future(companion.say(text))
        task.add_done_callback(report)
        return {"in_progress": f"ещё делаю ({cmd}); когда закончу — напишу в игровой чат"}
    except RuntimeError as e:
        return {"error": str(e)}


G = "plugin:minecraft"


@tool("mc_join", "Зайти в мир Майнкрафта игроком (мир хозяина, открытый для сети, или сервер).",
      {"port": _I("Порт (мир, открытый для сети, пишет его в чат)"), "host": S("Адрес, по умолчанию этот компьютер")},
      ["port"], group=G)
async def mc_join(port, host="127.0.0.1"):
    try:
        return await companion.join(host, port)
    except Exception as e:
        return {"error": f"не вышло зайти: {e}"}


@tool("mc_leave", "Выйти из мира Майнкрафта.", group=G)
async def mc_leave():
    await companion.leave()
    return {"ok": True}


@tool("mc_status", "{name} в Майнкрафте: здоровье, голод, где стоит, время суток, кто рядом, чем занята.", group=G)
async def mc_status():
    return await _bot("status", timeout=10)


@tool("mc_inventory", "Что у {name} в инвентаре в Майнкрафте.", group=G)
async def mc_inventory():
    return await _bot("inventory", timeout=10)


@tool("mc_follow", "Ходить следом за игроком (по умолчанию за хозяином).", {"player": S("Ник игрока, необязательно")}, group=G)
async def mc_follow(player=None):
    return await _bot("follow", {"player": player}, timeout=15)


@tool("mc_come", "Подойти к игроку (по умолчанию к хозяину).", {"player": S("Ник, необязательно")}, group=G)
async def mc_come(player=None):
    return await _bot("come", {"player": player}, timeout=40, done_text="пришла")


@tool("mc_stop", "Остановиться и бросить текущее дело в Майнкрафте.", group=G)
async def mc_stop():
    return await _bot("stop", timeout=10)


@tool("mc_collect", "Добыть блоки в Майнкрафте: сама найдёт, дойдёт, выкопает нужным инструментом, подберёт. "
      "Названия — английские id: oak_log, log (любые брёвна), stone, cobblestone, coal_ore, iron_ore, sand, dirt…",
      {"block": S("id блока, например oak_log"), "count": _I("Сколько, по умолчанию 1")}, ["block"], group=G)
async def mc_collect(block, count=1):
    return await _bot("collect", {"block": block, "count": int(count)}, timeout=100, done_text=f"добыла {block}")


@tool("mc_craft", "Скрафтить предмет в Майнкрафте (доски, палки и верстак сделает сама). id по-английски: "
      "crafting_table, stick, wooden_pickaxe, stone_sword, torch, chest, furnace, bread…",
      {"item": S("id предмета"), "count": _I("Сколько, по умолчанию 1")}, ["item"], group=G)
async def mc_craft(item, count=1):
    return await _bot("craft", {"item": item, "count": int(count)}, timeout=60, done_text=f"скрафтила {item}")


@tool("mc_give", "Отдать игроку вещи из своего инвентаря (подойдёт и бросит ему под ноги).",
      {"item": S("id предмета; пусто — отдать всё"), "count": _I("Сколько, пусто — все"), "player": S("Ник, необязательно")},
      group=G)
async def mc_give(item=None, count=None, player=None):
    return await _bot("give", {"item": item, "count": count, "player": player}, timeout=40, done_text="отдала")


@tool("mc_attack", "Атаковать моба рядом (по умолчанию ближайшего враждебного).", {"mob": S("id моба, например zombie")}, group=G)
async def mc_attack(mob=None):
    return await _bot("attack", {"mob": mob}, timeout=10)


@tool("mc_guard", "Охранять игрока: держаться рядом и бить враждебных мобов вокруг. on=false — перестать.",
      {"player": S("Ник, необязательно"), "on": {"type": "boolean"}}, group=G)
async def mc_guard(player=None, on=True):
    return await _bot("guard", {"player": player, "on": on}, timeout=10)


@tool("mc_goto", "Пойти в точку с координатами в Майнкрафте.", {"x": _I("x"), "y": _I("y"), "z": _I("z")},
      ["x", "y", "z"], group=G)
async def mc_goto(x, y, z):
    return await _bot("goto", {"x": x, "y": y, "z": z}, timeout=60, done_text="дошла")


@tool("mc_sleep", "Лечь спать в кровать поблизости (ночью).", group=G)
async def mc_sleep():
    return await _bot("sleep", timeout=30)


@tool("mc_say", "Написать в игровой чат Майнкрафта от имени {name}-игрока.", {"text": S("Текст")}, ["text"], group=G)
async def mc_say(text):
    if not companion.connected:
        return {"error": "{name} не в мире"}
    await companion.say(text)
    return {"ok": True}


async def _maybe_join(port):
    if not config.get("minecraft.companion", True) or companion.connected:
        return
    await asyncio.sleep(3)
    try:
        await companion.join("127.0.0.1", port)
    except Exception as e:
        bus.emit("notify", title="Майнкрафт", text=f"{{name}} не смогла зайти в мир: {e}")


# ===================== Глаза: клиентский мод (games/minecraft-eyes) =====================
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location("shikigami_mc_eyes", Path(__file__).with_name("eyes.py"))
_eyes_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_eyes_mod)
EYES = _eyes_mod.Eyes()
_last_line = {"t": 0.0}


async def eyes_react(kind, text, emotion, urgent, via_llm):
    if not config.get("minecraft.react", True) or not config.get("minecraft.eyes_hints", True):
        return
    from core import voice
    if via_llm:                                   # спокойные события — репликой ИИ, редко и только когда она молчит
        from core import chatter
        if not voice.busy() and chatter.allow():
            await quip(f"{text} Что сейчас у него в игре:\n{EYES.describe()}", emotion, cooldown=300)
        return
    if time.time() - _last_line["t"] < (2 if urgent else 30):
        return
    if voice.busy():
        # она занята ответом: срочное — облачком сразу (голосом было бы поздно), спокойное — пропускаем
        if urgent:
            bus.emit("avatar.state", state="idle", emotion=emotion)
            bus.emit("speech", text=text, url=None, emotion=emotion)
        return
    from core import chatter
    if not chatter.allow(urgent):                 # «помолчи» / лимит в час — облачком
        bus.emit("speech", text=text, url=None, emotion=emotion)
        return
    _last_line["t"] = time.time()
    bus.emit("avatar.state", state="idle", emotion=emotion)
    await voice.speak(text, emotion=emotion, fresh_for=1.5 if urgent else 4)


def _eyes_context():
    if not EYES.fresh(10):
        return ""
    return ("Хозяин сейчас играет в Майнкрафт (1.21.11, сборка с модами), ты видишь игру через мод «Глаза». "
            "Отвечая про игру, опирайся на это (инвентарь, кто рядом, где он):\n" + EYES.describe())


@tool("minecraft_view", "Что сейчас происходит в Майнкрафте у хозяина (видно через мод «Глаза {name}»): где он, здоровье, "
      "голод, что в руках и в инвентаре, кто рядом и с какой стороны, на что смотрит, что делал последние минуты.",
      group="plugin:minecraft")
def minecraft_view():
    if not EYES.state:
        return {"error": "мод «Глаза {name}» ничего не присылал — игра не запущена или мод не установлен"}
    return {"сейчас": EYES.describe(), "последние_события": EYES.recent(20), "обновлено_сек_назад": round(time.time() - EYES.at, 1)}


def setup(api):
    asyncio.get_event_loop().create_task(tail())
    persona.CONTEXT.append(_eyes_context)

    @api.route("GET", "/eyes/state")
    async def eyes_state(_):
        """Коротко для виджета на главной: здоровье, сытость, где, враги рядом."""
        s = EYES.state or {}
        if not EYES.fresh(8):
            return web.json_response({"fresh": False})
        hostile = [e for e in s.get("near") or [] if e.get("hostile")]
        return web.json_response({"fresh": True, "hp": s.get("hp"), "hp_max": s.get("hp_max"), "food": s.get("food"),
                                  "biome": s.get("biome_name"), "dim": _eyes_mod.DIMS.get(_eyes_mod.dim_of(s), ""),
                                  "time": _eyes_mod.clock(s.get("day_time", 0)), "hostile": len(hostile),
                                  "nearest": hostile[0]["name"] + f" {hostile[0]['dist']} м" if hostile else "",
                                  "hand": (s.get("hand") or {}).get("name", "")})

    @api.route("POST", "/eyes")
    async def eyes(r):
        s = await r.json()
        if s.get("player") and not st["player"]:
            st["player"] = s["player"]            # ник хозяина известен, даже если {name} запустили после игры
        for a in EYES.update(s):
            asyncio.create_task(eyes_react(*a))
        return web.json_response({"ok": True})

    @api.route("GET", "/status")
    async def status(_):
        return web.json_response(minecraft_recent())
