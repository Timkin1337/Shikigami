"""Агент: модель ↔ инструменты по кругу, пока задача не закрыта.

Всё, что происходит, уходит в шину событий — окно чата, аватар и Telegram рисуют одно и то же.
"""
import asyncio
import contextvars
import json
import random
import re
import time
import uuid
from pathlib import Path

from . import bus, db, llm, persona, tools
from .config import DATA, config

_pending = {}       # id подтверждения → Future
_running = {}       # chat_id → Task
_locks = {}
_queued = {}       # чат → реплики, которые ждут, пока {name} закончит предыдущий ответ


def resolve_confirm(cid, ok):
    f = _pending.pop(cid, None)
    if f and not f.done():
        f.set_result(bool(ok))


YES = re.compile(r"^(да|ага|угу|давай|можно|разрешаю|разреши|подтверждаю|выполняй|ок|окей|конечно|yes|yeah|ok|sure|go ahead)\b", re.I)
NO = re.compile(r"^(нет|не надо|не нужно|отмена|отмени|стоп|не разрешаю|no|nope|cancel|stop)\b", re.I)
_voice_confirm = {"id": None}


async def ask_confirm(chat_id, tool_name, text, source):
    """Спросить разрешение на опасное действие. Голосом спрашиваем голосом и ждём «да/нет» без имени:
    окно подтверждения во время игры не видно, и раньше {name} молча ждала его по 2 минуты."""
    cid = uuid.uuid4().hex[:8]
    fut = asyncio.get_running_loop().create_future()
    _pending[cid] = fut
    bus.emit("confirm.request", id=cid, chat_id=chat_id, tool=tool_name, text=text, source=source)
    by_voice = source == "voice"
    if by_voice:
        from . import voice as v
        _voice_confirm["id"] = cid
        await v.speak(persona.clean(f"Разрешить? {text.splitlines()[0][:120]}. Скажите да или нет."), emotion="neutral")
        v.listen_once()                                   # следующая фраза — ответ, имя звать не нужно
    try:
        return await asyncio.wait_for(fut, 40 if by_voice else 120)
    except asyncio.TimeoutError:
        bus.emit("confirm.expired", id=cid)
        if by_voice:
            from . import voice as v
            asyncio.create_task(v.speak(persona.clean("Не дождалась ответа — не стала выполнять."), emotion="neutral"))
        return False
    finally:
        _pending.pop(cid, None)
        if _voice_confirm["id"] == cid:
            _voice_confirm["id"] = None


def voice_confirm_answer(text):
    """Ответ «да/нет» на голосовой вопрос о разрешении. True — фраза была ответом (дальше не идёт)."""
    cid = _voice_confirm["id"]
    if not cid or cid not in _pending:
        return False
    t = re.sub(r"[^\w\s]", "", text.lower()).strip()
    if YES.match(t):
        resolve_confirm(cid, True)
        return True
    if NO.match(t):
        resolve_confirm(cid, False)
        return True
    return False


state = {"current_chat": None}
# откуда пришла текущая просьба: инструменты (например, run_command) узнают, куда слать подтверждения
current_source = contextvars.ContextVar("current_source", default="ui")


async def with_images(text, images):
    """Картинки из чата: модель со зрением описывает их, описание идёт вместе с репликой — исполнитель и характер
    дальше работают с текстом, как обычно. Без согласия на зрение («Модули») картинка в облако не уходит."""
    from .config import DATA
    from .tools import vision
    if not config.get("vision.enabled"):
        return (text + "\n\n[Пользователь прислал картинку, но зрение выключено — посмотреть её нельзя. Скажи, что включить "
                "его можно во вкладке «Модули» → «Смотреть на экран»: картинка уйдёт в облако модели со зрением.]")
    from PIL import Image
    parts = []
    for i, name in enumerate(images[:4], 1):
        try:
            img = Image.open(DATA / "uploads" / Path(name).name)
            res = await vision.describe(img, text or "что на картинке?", prompt=vision.IMAGE_PROMPT)
        except Exception as e:
            res = {"error": f"{type(e).__name__}: {e}"}
        parts.append(f"[Картинка {i}: {res['seen']}]" if "seen" in res else f"[Картинка {i}: не удалось рассмотреть — {res.get('error')}]")
    return (text + "\n\n" if text else "Что скажешь про картинку?\n\n") + "\n".join(parts)


async def handle_user(text, chat_id=None, voice=False, source="ui", images=None):
    """Вход для любой реплики: сначала фразы команд (мгновенно, без ИИ), потом агент.

    Голос и Telegram без явного чата продолжают последний разговор, если он был недавно.
    """
    from . import commands
    if source == "voice" and voice_confirm_answer(text):
        return chat_id, ""
    current_source.set(source)
    if images:                                         # с картинкой — сразу к ИИ: команды и перехваты по тексту тут ни при чём
        text = await with_images(text, images)
    hit = None if images else commands.match_phrase(text)
    if hit:
        cid, vars = hit
        c = commands.get_command(cid)
        bus.emit("chat.notice", chat_id=chat_id, text=f"▶ команда «{c['name']}»", user_text=text, source=source)
        res = await commands.run_command(cid, "trigger.phrase", vars, source=source)
        if "error" in res:
            return chat_id, f"▶ команда «{c['name']}»: ошибка {res['error']}"
        return chat_id, " ".join(res.get("said") or []) or f"▶ команда «{c['name']}» выполнена"
    from . import plugins
    said = None if images else await plugins.intercept(text, source)
    if said:
        said = persona.clean(said)            # реплика плагина — на языке программы и с выбранным именем
        bus.emit("chat.notice", chat_id=chat_id, text=said, user_text=text, source=source)
        return chat_id, said
    if not chat_id and source != "ui":
        cur = state["current_chat"]
        row = cur and db.one("SELECT updated FROM chats WHERE id=?", (cur,))
        if row and time.time() - row["updated"] < 900:
            chat_id = cur
    if not chat_id:
        chat_id = db.new_chat()
        bus.emit("chat.created", chat_id=chat_id, source=source)
    state["current_chat"] = chat_id
    return chat_id, await respond(chat_id, text, voice, source)


def stop(chat_id):
    t = _running.get(chat_id)
    if t and not t.done():
        t.cancel()


def _short(result, limit=4000):
    s = json.dumps(result, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + "…(обрезано)"


async def respond(chat_id, text, voice=False, source="ui"):
    """Обработать реплику пользователя. Возвращает финальный текст ответа.

    Реплика сразу сохраняется и показывается в чате. Если {name} ещё отвечает на прошлую, реплика ждёт;
    когда очередь доходит, все накопившиеся реплики получают ОДИН общий ответ (модель видит их в истории) —
    раньше каждая ждала своей очереди невидимо, и после долгой паузы приходил десяток ответов подряд.
    Возвращает "" для реплик, ответ на которые уже вошёл в общий.
    """
    lock = _locks.setdefault(chat_id, asyncio.Lock())
    _remember_user(chat_id, text, source, queued=lock.locked())
    _queued.setdefault(chat_id, []).append(text)
    async with lock:
        texts = _queued.pop(chat_id, [])
        if not texts:
            return ""
        text = texts[0] if len(texts) == 1 else "\n".join(texts)
        task = asyncio.create_task(_respond(chat_id, text, voice, source))
        _running[chat_id] = task
        try:
            return await task
        except asyncio.CancelledError:
            bus.emit("chat.done", chat_id=chat_id, text="", emotion="neutral", cancelled=True, voice=voice)
            return ""
        finally:
            _running.pop(chat_id, None)


def _remember_user(chat_id, text, source, queued=False):
    db.add_message(chat_id, "user", text)
    if db.one("SELECT COUNT(*) n FROM messages WHERE chat_id=?", (chat_id,))["n"] == 1:
        db.ex("UPDATE chats SET title=? WHERE id=?", (text[:48], chat_id))
        bus.emit("data.changed", what="chats")
    bus.emit("chat.user", chat_id=chat_id, text=text, source=source, queued=queued)


async def _respond(chat_id, text, voice, source):
    bus.emit("avatar.state", state="thinking")
    # на один ответ: упавшие вызовы (второй раз не выполняем), резкие действия «после ответа», итоги для стилизатора
    turn = {"failed": {}, "later": [], "outcomes": []}

    # Шаг 1. Исполнитель без образа: в одном запросе с ролевой игрой модель склонна
    # изображать действия ремарками и выдумывать результаты вместо вызова инструментов.
    draft, used_tools = await _execute(chat_id, source, turn)
    if draft is None:
        speaking = None
        if voice and _last_error["msg"]:
            from . import voice as v
            speaking = asyncio.create_task(v.speak(_last_error["msg"], emotion="sad"))
        if turn["later"]:
            asyncio.create_task(_run_later(chat_id, turn["later"], speaking, _last_error["msg"]))
        return _last_error["msg"]                       # Telegram получит ту же человеческую фразу
    # Шаг 2. {name} пересказывает черновик своим голосом, не добавляя фактов.
    final = await _stylize(chat_id, text, draft, used_tools, voice, turn["outcomes"])
    emo = persona.emotion_of(final)               # эмоцию — из скрытой метки [shy], потом метку и ремарки убираем
    final = persona.clean(final) or persona.clean(draft)
    # исполнитель потом видит в истории свой сухой черновик, а не реплику в образе: иначе характер протекал
    # в выбор инструментов («Спи уже» → гасила экран, случай 25.10)
    db.add_message(chat_id, "assistant", final, draft=draft)

    bus.emit("chat.done", chat_id=chat_id, text=final, emotion=emo, voice=voice, source=source)
    bus.emit("avatar.state", state="idle", emotion=emo)
    speaking = None
    if voice or config.get("voice.speak_typed"):
        from . import voice as v
        speaking = asyncio.create_task(v.speak(final, emotion=emo))   # говорит параллельно: следующая реплика не ждёт конца фразы
    if turn["later"]:
        asyncio.create_task(_run_later(chat_id, turn["later"], speaking, final))
    return final


async def _run_later(chat_id, later, speaking, said):
    """Резкие действия (погасить экран, заблокировать, выключить) — когда она договорила ответ или его успели
    прочитать. Раньше экран гас раньше, чем приходил ответ, и «сначала ответь, потом выключай» было не выполнить."""
    if speaking:
        try:
            await speaking
        except Exception:
            pass
    else:
        await asyncio.sleep(min(8.0, 2 + len(said or "") / 40))
    for c, name, args in later:
        result = await tools.run(name, args)
        bad = isinstance(result, dict) and "error" in result
        bus.emit("chat.tool", chat_id=chat_id, id=c["id"], name=name, status="error" if bad else "ok", result=result)
        _trace(chat_id, "tool", name=name, args=args, later=True, status="error" if bad else "ok", result=_short(result, 300))


OFFLINE_LINES = ["Голова сейчас не отвечает — облако ИИ перегружено. Повторите через минутку, {address}.",
                 "Хм, мысли не доходят: сервер ИИ молчит. Попробуйте ещё раз чуть позже.",
                 "Связь с облаком ИИ пропала. Я тут, но думать пока не могу — дайте мне минуту."]
NO_BRAIN = ("Я пока без «мозга»: впишите бесплатный ключ NVIDIA в «Настройки → Мозг» или подключите локальную модель. "
            "Как это сделать — во вкладке «Помощь».")
_last_error = {"msg": ""}


async def _execute(chat_id, source, turn):
    """Цикл инструментов. Возвращает (сухой черновик ответа, были ли вызовы)."""
    # резкие инструменты (экран, блокировка, питание) — только если человек сам просил об этом в последних репликах
    recent = [m["content"] for m in db.history(chat_id, 12) if m["role"] == "user"][-2:]
    schemas = tools.schemas(recent_user=recent)
    used = False
    for step in range(config.get("llm.max_steps", 8)):
        msgs = [{"role": "system", "content": persona.executor_prompt(source)}] +             persona.for_executor(db.history(chat_id, config.get("llm.history_messages", 24), drafts=True))
        bus.emit("chat.start", chat_id=chat_id, step=step)
        t0 = time.time()
        try:
            res, name_used = await llm.chat(
                msgs, schemas, temperature=0.3,
                on_switch=lambda n: bus.emit("chat.switch", chat_id=chat_id, provider=n))
        except llm.LLMError as e:
            # вслух и в чате — по-человечески; коды ответа сервера — только мелкой подписью для любопытных.
            # В историю не пишем: иначе модель потом видит «HTTP 500» в разговоре
            msg = persona.clean(NO_BRAIN if llm.no_brain() else random.choice(OFFLINE_LINES).format(address=config["address"]))
            _last_error["msg"] = msg
            bus.emit("chat.done", chat_id=chat_id, text=msg, emotion="sad", error=True, detail=str(e)[:300])
            bus.emit("avatar.state", state="idle", emotion="sad")
            return None, used
        content, calls = res["content"].strip(), res["tool_calls"]
        bus.emit("chat.meta", chat_id=chat_id, provider=name_used, seconds=round(time.time() - t0, 1), phase="exec")
        _trace(chat_id, "exec", step=step, model=name_used, seconds=round(time.time() - t0, 1), text=content[:2000],
               calls=[{"name": c["function"]["name"], "args": c["function"]["arguments"]} for c in calls or []])
        # Nemotron иногда пишет имя инструмента без аргументов текстом («monitor_off») вместо вызова — считаем вызовом
        bare = re.fullmatch(r"`?([a-z_][a-z0-9_]*)(\(\))?`?", content)
        if not calls and bare and bare.group(1) in tools.REGISTRY and not tools.REGISTRY[bare.group(1)].schema()["function"]["parameters"].get("required"):
            calls = [{"id": f"call_{int(time.time() * 1000)}", "type": "function", "function": {"name": bare.group(1), "arguments": "{}"}}]
            content = ""
        if not calls:
            return content, used
        used = True
        db.add_message(chat_id, "assistant", content, tool_calls=calls)
        for c in calls:
            await _call_tool(chat_id, c, source, turn)
    return "Слишком много шагов подряд — остановилась, нужно уточнение, что делать дальше.", used


async def _call_tool(chat_id, c, source, turn):
    name = c["function"]["name"]
    try:
        args = json.loads(c["function"]["arguments"] or "{}")
    except json.JSONDecodeError:
        args = {}
    t = tools.REGISTRY.get(name)
    bus.emit("chat.tool", chat_id=chat_id, id=c["id"], name=name, args=args, status="run")

    def done(result, status, note):
        if status != "run":                 # «run» — карточка ждёт: действие выполнится после ответа
            bus.emit("chat.tool", chat_id=chat_id, id=c["id"], name=name, status=status, result=result)
        db.add_message(chat_id, "tool", _short(result), tool_call_id=c["id"], name=name)
        turn["outcomes"].append(f"{name} — {note}")
        _trace(chat_id, "tool", name=name, args=args, status=status, result=_short(result, 300))

    # тот же упавший вызов второй раз не выполняем: раньше avatar_outfit падал восемь раз подряд
    key = name + json.dumps(args, ensure_ascii=False, sort_keys=True)
    if key in turn["failed"]:
        return done({"error": f"этот же вызов уже упал ({turn['failed'][key]}) — не повторяй его, скажи пользователю, что не вышло"},
                    "error", "НЕ сделано, ошибка")
    if t and (t.danger or t.ask) and config["confirm_dangerous"]:
        ok = await ask_confirm(chat_id, name, t.describe_call(args), source)
        _trace(chat_id, "confirm", name=name, ok=ok)
        if not ok:
            return done({"cancelled": "пользователь не разрешил это действие"}, "denied", "НЕ сделано: пользователь не разрешил")
    if t and t.after_reply and not (name == "power" and args.get("action") == "cancel"):
        turn["later"].append((c, name, args))
        return done({"ok": True, "scheduled": "выполнится сразу после твоего ответа — скажи, что сделаешь это сейчас"},
                    "run", "будет сделано сразу после ответа")
    result = await tools.run(name, args)
    bad = isinstance(result, dict) and "error" in result
    if bad:
        turn["failed"][key] = str(result["error"])[:200]
    done(result, "error" if bad else "ok", "НЕ сделано, ошибка" if bad else "сделано")


TRACE = DATA / "agent_trace.log"


def _trace(chat_id, phase, **kw):
    """Журнал шагов агента: какая модель решала, что вызвала, черновик, подтверждения. В data/core.log этого нет,
    а он к тому же перезаписывается при каждом запуске — после случая 25.10 восстановить ход событий было не по чему.
    Только локально; в «отчёт об ошибке» не попадает."""
    import datetime as _dt
    try:
        if TRACE.exists() and TRACE.stat().st_size > 5 * 2**20:
            TRACE.replace(TRACE.with_name("agent_trace.1.log"))
        line = json.dumps({"t": _dt.datetime.now().isoformat(timespec="seconds"), "chat": chat_id, "phase": phase, **kw},
                          ensure_ascii=False, default=str)
        with open(TRACE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print("журнал агента:", e, flush=True)


async def _stylize(chat_id, text, draft, used_tools, voice, outcomes=()):
    if config["persona"] == "neutral" and not voice:
        bus.emit("chat.delta", chat_id=chat_id, text=draft)
        return draft
    since = persona.persona_since()
    rows = db.q("SELECT role, content, tool_calls, created FROM messages WHERE chat_id=? AND role IN ('user','assistant') "
                "ORDER BY id DESC LIMIT 12", (chat_id,))[::-1]
    talk = [{"role": m["role"], "content": persona.clean(m["content"])} for m in rows
            if m["content"] and not m["tool_calls"] and (m["role"] == "user" or (m["created"] or 0) >= since)][:-1]
    msgs = [{"role": "system", "content": persona.system_prompt(voice)}] + talk[-8:] + [
        {"role": "user", "content": text},
        {"role": "system", "content": persona.style_instruction(draft, used_tools, voice, outcomes)}]
    t0 = time.time()
    try:
        # ответ в характере — качественной моделью (llm.style_model): быстрая из автоподбора хороша для инструментов,
        # но в образе путает род, мешает языки и теряет факты (24.10: Lightning 30B вместо «зрение выключено» — «ну показывай»)
        res, used = await llm.chat(msgs, None, temperature=0.65, model=config.get("llm.style_model") or None,
                                   on_text=lambda d: bus.emit("chat.delta", chat_id=chat_id, text=d))
        bus.emit("chat.meta", chat_id=chat_id, provider=used, seconds=round(time.time() - t0, 1), phase="style")
        _trace(chat_id, "style", model=used, seconds=round(time.time() - t0, 1), draft=draft[:2000])
        return res["content"].strip() or draft
    except llm.LLMError:
        bus.emit("chat.delta", chat_id=chat_id, text=draft)
        return draft
