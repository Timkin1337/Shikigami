"""Веб-сервер ядра: интерфейс, WebSocket событий, REST для виджетов и настроек."""
import asyncio
import json
import time
from pathlib import Path

from aiohttp import web, WSMsgType

from . import agent, bus, db, plugins, tools
from .config import ROOT, DATA, config

UI = ROOT / "ui"
MODELS = DATA / "models"
MODELS.mkdir(exist_ok=True)
AUDIO = DATA / "audio"
AUDIO.mkdir(exist_ok=True)

routes = web.RouteTableDef()
_clients = {}  # ws → роль окна (main | avatar | plugin)


def client_roles():
    return set(_clients.values())


def j(data, status=200):
    return web.json_response(data, status=status, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))


@routes.get("/")
async def index(_):
    return web.FileResponse(UI / "index.html", headers={"Cache-Control": "no-store"})


@routes.get("/avatar")
async def avatar(_):
    return web.FileResponse(UI / "avatar.html", headers={"Cache-Control": "no-store"})


# --- WebSocket: события ядра наружу, команды внутрь ---

@routes.get("/ws")
async def ws_handler(request):
    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=8 * 2**20)
    await ws.prepare(request)
    q = bus.subscribe()

    async def pump():
        while True:
            msg = await q.get()
            try:
                await ws.send_str(json.dumps(msg, ensure_ascii=False, default=str))
            except Exception:
                return

    pusher = asyncio.create_task(pump())
    _clients[ws] = request.query.get("role", "main")
    await ws.send_str(json.dumps({"type": "hello", "name": config["assistant_name"]}, ensure_ascii=False))
    from .behavior import game
    if game["on"] and _clients[ws] == "avatar":     # окно аватара перезапустилось посреди игры — сразу в игровой режим
        await ws.send_str(json.dumps({"type": "game.mode", "on": True}))
    try:
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            try:
                await handle(json.loads(m.data))
            except Exception as e:
                bus.emit("error", text=f"{type(e).__name__}: {e}")
    finally:
        pusher.cancel()
        bus.unsubscribe(q)
        _clients.pop(ws, None)
    return ws


async def handle(msg):
    t = msg.get("type")
    if t == "chat":
        asyncio.create_task(agent.handle_user(msg["text"], msg.get("chat_id"), voice=bool(msg.get("voice")),
                                              source=msg.get("source", "ui"), images=msg.get("images") or None))
    elif t == "ui.chat":
        agent.state["current_chat"] = msg.get("chat_id")
    elif t == "confirm":
        agent.resolve_confirm(msg["id"], msg.get("ok"))
    elif t == "stop":
        agent.stop(msg.get("chat_id"))
    elif t == "voice.ptt":
        from . import voice
        await voice.push_to_talk(msg.get("down", True))
    elif t == "speech.ended":
        from . import voice
        voice.speech_ended(msg.get("id"))
    elif t == "avatar.event":
        from . import friendship
        friendship.gain(msg.get("kind", ""))           # поглаживания — в дружбу (остальное там не считается)
        from . import reactions
        asyncio.create_task(reactions.react(msg.get("kind", "")))
        from . import plugins
        asyncio.create_task(plugins.avatar_event(msg.get("kind", ""), msg))
    elif t == "avatar.tint.parts":
        # окно настроек спрашивает, что у модели можно перекрасить (без parts), окно аватара отвечает (с parts)
        bus.emit("avatar.tint.parts", **{k: v for k, v in msg.items() if k != "type"})


# --- REST ---

@routes.get("/api/chats")
async def chats(_):
    return j(db.list_chats())


@routes.get("/api/chats/{id}")
async def chat_get(r):
    cid = int(r.match_info["id"])
    msgs = []
    for m in db.chat_messages(cid):
        if m["role"] == "tool":
            msgs.append({"role": "tool", "name": m["name"], "content": m["content"]})
        elif m["role"] == "assistant" and m["tool_calls"]:
            if m["content"]:
                msgs.append({"role": "assistant", "content": m["content"]})
            for c in json.loads(m["tool_calls"]):
                msgs.append({"role": "call", "name": c["function"]["name"], "args": c["function"]["arguments"]})
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    return j(msgs)


@routes.delete("/api/chats/{id}")
async def chat_del(r):
    cid = int(r.match_info["id"])
    db.ex("DELETE FROM messages WHERE chat_id=?", (cid,))
    db.ex("DELETE FROM chats WHERE id=?", (cid,))
    return j({"ok": True})


@routes.get("/api/media/now")
async def media_now(_):
    """Что играет в Windows (YouTube в браузере, плеер): для «смотрит видео вместе»."""
    from . import media
    try:
        return j(await media.now_playing() or {})
    except Exception as e:
        return j({"error": str(e)})


@routes.get("/api/widgets")
async def widgets(_):
    from .tools import system
    from . import media
    out = {"sys": await asyncio.to_thread(system.sys_stats), "time": time.time()}
    try:
        out["volume"] = await asyncio.to_thread(system.get_volume)
    except Exception:
        pass
    out["media"] = await media.now_playing()
    return j(out)


@routes.post("/api/media/{action}")
async def media_action(r):
    from .tools import system
    return j(await system.media(r.match_info["action"]))


@routes.post("/api/hotkey")
async def hotkey(r):
    from . import commands
    asyncio.create_task(commands.on_hotkey((await r.json())["accelerator"]))
    return j({"ok": True})


VOICES = DATA / "voices"
_design = {"busy": False}


@routes.get("/api/voices")
async def voices_list(_):
    out = []
    for d in sorted(VOICES.glob("*/texts.json")):
        d = d.parent
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        out.append({"id": d.name, "name": meta.get("name") or d.name, "description": meta.get("description", ""),
                    "mild": (d / "mild" / "texts.json").exists()})
    from . import modules
    designer = next((m["status"] for m in modules.listing() if m["id"] == "voice-designer"), "missing")
    return j({"voices": out, "current": config.get("voice.espeech_voice", "myagkaya"), "designer": designer, "busy": _design["busy"]})


@routes.post("/api/voices/design")
async def voices_design(r):
    """Новый тембр по описанию: описание → по-английски (так VoiceDesign слушается лучше) → 12 образцов на видеокарте."""
    import re as _re
    import subprocess
    body = await r.json()
    desc, name = (body.get("description") or "").strip()[:400], (body.get("name") or "").strip()[:40] or "Новый голос"
    if not desc or _design["busy"]:
        return j({"ok": False, "error": "опишите голос" if not desc else "уже создаю другой тембр"}, status=400)
    from . import llm
    from .voice.tts import EspeechTTS
    py, _ = EspeechTTS.paths()
    vid = _re.sub(r"[^a-z0-9]+", "-", (name.lower().encode("ascii", "ignore").decode() or "voice")).strip("-") or "voice"
    vid = f"{vid}-{int(time.time()) % 100000}"
    _design["busy"] = True

    async def run():
        try:
            bus.emit("voice.design", text="перевожу описание…", pct=None)
            try:
                en = (await llm.simple("Translate this voice description into one English sentence for a voice-design TTS model "
                                       "(gender, age, pitch, timbre, manner). Return only the sentence.\n" + desc, max_tokens=120)).strip()
            except Exception:
                en = desc
            out = VOICES / vid
            p = await asyncio.create_subprocess_exec(str(py), str(ROOT / "core" / "voice" / "voice_design.py"), str(out), en, name,
                                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=0x08000000 | 0x00004000,
                                                     env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
            async for line in p.stdout:
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if "step" in m:
                    bus.emit("voice.design", text=f"рисую образцы эмоций: {m['step']} из {m['of']}", pct=int(m["step"] * 100 / m["of"]))
            await p.wait()
            ok = (out / "meta.json").exists()
            bus.emit("voice.design", text="готово — выберите тембр «" + name + "»" if ok else "не вышло (видеокарта занята или нет модуля)",
                     pct=100, done=True, id=vid if ok else None, error=not ok)
        finally:
            _design["busy"] = False

    asyncio.create_task(run())
    return j({"ok": True, "id": vid})


@routes.delete("/api/voices/{id}")
async def voices_delete(r):
    import shutil
    vid = Path(r.match_info["id"]).name
    if vid == "myagkaya" or not (VOICES / vid).exists():
        return j({"ok": False}, status=400)
    shutil.rmtree(VOICES / vid, ignore_errors=True)
    return j({"ok": True})


@routes.post("/api/read-selection")
async def read_selection(_):
    """Ctrl+Alt+T: прочитать вслух выделенный текст своим голосом."""
    from . import clip, voice
    text = await asyncio.to_thread(clip.copy_selection)
    say = text[:2500] if text else "Ничего не выделено. Выделите текст и нажмите ещё раз."
    asyncio.create_task(voice.speak(say, emotion="neutral"))
    return j({"ok": bool(text), "chars": len(text)})


@routes.post("/api/voice/listen")
async def voice_listen(_):
    from . import voice
    voice.listen_once()
    return j({"ok": True})


VOICE_SAMPLE = "Хмпф. Опять вы, господин? Ладно, слушайте. Н-не то чтобы я для вас старалась!"


@routes.get("/api/voice/devices")
async def voice_devices(_):
    from .voice.listener import input_devices
    try:
        return j({"devices": await asyncio.to_thread(input_devices), "current": config.get("voice.input_device") or ""})
    except Exception as e:
        return j({"devices": [], "current": "", "error": str(e)})


UPLOADS = DATA / "uploads"


@routes.post("/api/chat/image")
async def chat_image(r):
    """Картинка для чата (вставили, перетащили, 📎): сохраняем у себя, в облако она уходит только при отправке реплики."""
    import base64
    import io
    from PIL import Image
    data = (await r.json()).get("data", "")
    try:
        raw = base64.b64decode(data.split(",", 1)[-1])
        if len(raw) > 15 * 2**20:
            return j({"error": "картинка больше 15 МБ"}, 400)
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        return j({"error": "это не картинка"}, 400)
    UPLOADS.mkdir(exist_ok=True)
    name = f"{int(time.time() * 1000)}.jpg"
    img.convert("RGB").save(UPLOADS / name, "JPEG", quality=90)
    return j({"id": name, "url": f"/uploads/{name}"})


@routes.get("/api/lines")
async def lines_get(_):
    """Облачка окна аватара в характере человека (банк фраз) — окно берёт их по ключу."""
    from . import lines
    return j({"ready": lines.ready(), "busy": lines.busy(), "lines": lines.for_window()})


@routes.post("/api/lines/regenerate")
async def lines_regenerate(_):
    from . import lines, llm
    if llm.no_brain():
        return j({"ok": False, "error": "нужен «мозг» — ключ NVIDIA или локальная модель"}, 400)
    lines.regenerate(force=True)
    return j({"ok": True})


@routes.get("/api/friendship")
async def friendship_get(_):
    from . import friendship
    return j(friendship.status())


@routes.get("/api/modules")
async def modules_list(request):
    from . import modules
    return web.json_response(await asyncio.to_thread(modules.listing))


@routes.post("/api/modules/{id}/{action}")
async def modules_action(request):
    from . import modules
    ok = modules.start(request.match_info["id"], request.match_info["action"])
    return web.json_response({"ok": ok}, status=200 if ok else 409)


@routes.post("/api/voice/capture-key")
async def voice_capture_key(_):
    """Настройки → «Назначить кнопку»: ждём, что нажмёт хозяин (клавиша или кнопка мыши)."""
    from .voice import hotkey
    name = await asyncio.to_thread(hotkey.capture, 10)
    if name:
        config.update({"voice": {"ptt_key": name}})
        bus.emit("config.changed")
    return j({"key": name})


@routes.post("/api/voice/test")
async def voice_test(r):
    """Проба голоса с настройками из формы, ещё не сохранёнными."""
    from . import voice
    b = await r.json()
    cfg = {k: b[k] for k in ("tts_speaker", "tts_pitch", "tts_rate", "tts_engine", "edge_voice",
                             "espeech_voice", "espeech_strength", "espeech_steps") if b.get(k)}
    asyncio.create_task(voice.speak(b.get("text") or VOICE_SAMPLE, emotion="shy", cfg=cfg))
    return j({"ok": True})


@routes.get("/api/weather")
async def weather(_):
    from .tools import everyday
    try:
        return j(await everyday.weather())
    except Exception as e:
        return j({"error": str(e)})


@routes.get("/api/currency")
async def currency(_):
    from .tools import everyday
    try:
        return j(await everyday.currency())
    except Exception as e:
        return j({"error": str(e)})


@routes.get("/api/lists")
async def lists(_):
    from .tools import everyday
    return j({"reminders": everyday.list_reminders(), "tasks": everyday.list_tasks(),
              "events": everyday.list_events(14), "notes": everyday.find_notes(""),
              "facts": db.q("SELECT id, text FROM facts ORDER BY id DESC")})


@routes.post("/api/lists/{kind}/{action}")
async def lists_action(r):
    from .tools import everyday, memory
    kind, action = r.match_info["kind"], r.match_info["action"]
    body = await r.json()
    fn = {("reminders", "add"): everyday.add_reminder, ("reminders", "delete"): everyday.cancel_reminder,
          ("tasks", "add"): everyday.add_task, ("tasks", "done"): everyday.complete_task,
          ("tasks", "delete"): lambda id: db.ex("DELETE FROM tasks WHERE id=?", (id,)),
          ("notes", "add"): everyday.add_note, ("notes", "delete"): everyday.delete_note,
          ("events", "add"): everyday.add_event, ("events", "delete"): lambda id: db.ex("DELETE FROM events WHERE id=?", (id,)),
          ("facts", "add"): memory.remember, ("facts", "delete"): memory.forget}.get((kind, action))
    if not fn:
        return j({"error": "нет такого действия"}, 404)
    res = await asyncio.to_thread(fn, **body)
    bus.emit("data.changed", what=kind)
    return j(res if isinstance(res, (dict, list)) else {"ok": True})


@routes.get("/api/config")
async def cfg_get(_):
    data = json.loads(json.dumps(config.data))
    data["_secrets"] = {k: bool(config.secret(k)) for k in ("NVIDIA_API_KEY", "TELEGRAM_BOT_TOKEN")}
    return j(data)


@routes.post("/api/config")
async def cfg_set(r):
    patch = await r.json()
    secrets = patch.pop("_secrets", None) or {}
    # сменили имя, а слово-триггер было старым именем — меняем и его, иначе она перестанет откликаться
    new = (patch.get("assistant_name") or "").strip()
    old = config.get("assistant_name") or ""
    sent_wake = ((patch.get("voice") or {}).get("wake_word") or "").strip().lower()
    if new and new != old and (config.get("voice.wake_word") or "").lower() == old.lower() and sent_wake in ("", old.lower()):
        patch.setdefault("voice", {})["wake_word"] = new.lower()
    persona_was = _persona_key()
    config.update(patch)
    if secrets:
        from .config import ENV_PATH
        import os
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
        for k, v in secrets.items():
            if not v:
                continue
            lines = [l for l in lines if not l.startswith(k + "=")] + [f"{k}={v}"]
            os.environ[k] = v
        ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    bus.emit("config.changed")
    from . import lines, llm
    if (_persona_key() != persona_was or secrets) and config.get("setup_done", True) and not llm.no_brain() and not lines.ready():
        lines.regenerate()                    # сменили характер, имя, обращение или язык — сразу новый набор фраз
    return j({"ok": True})


def _persona_key():
    return (config.get("persona"), config.get("persona_custom"), config.get("assistant_name"), config.get("address"), config.get("ui.lang"))


@routes.post("/api/persona/improve")
async def persona_improve(r):
    from . import llm, persona
    draft = ((await r.json()).get("text") or "").strip()
    if len(draft) < 10:
        return j({"error": "напишите хотя бы пару предложений — каким должен быть характер"}, 400)
    try:
        return j({"text": await persona.improve_prompt(draft[:4000])})
    except llm.LLMError as e:
        return j({"error": f"модель недоступна — проверьте ключ NVIDIA или локальную модель ({e})"}, 502)


@routes.post("/api/logs/open")
async def logs_open(_):
    import os
    os.startfile(str(DATA))
    return j({"ok": True})


@routes.get("/api/logs/report")
async def logs_report(_):
    """Отчёт для ручной отправки: версия Windows и хвосты логов. Ключи вычищаются на всякий случай."""
    import platform
    import re
    parts = [f"Windows {platform.version()} · Python {platform.python_version()}"]
    for name in ("crash.log", "core.log", "shell.log"):
        p = DATA / name
        if p.exists():
            tail = p.read_text(encoding="utf-8", errors="replace").splitlines()[-120:]
            parts.append(f"===== {name} =====\n" + "\n".join(tail))
    text = "\n\n".join(parts)
    # реплики человека в логе (голосом, из Telegram, «отбой … : текст») — его личное, в отчёт не идут
    text = re.sub(r"(?m)^(.*?(🎙|отбой|имя слышала|Telegram:.*?«|команда «)).*$", r"\1 [текст скрыт]", text)
    text = re.sub(r"(nvapi-|bot)?[A-Za-z0-9_\-]{30,}", "[скрыто]", text)        # ключи и токены
    text = re.sub(r"\d{6,}:[A-Za-z0-9_\-]{20,}", "[скрыто]", text)                # токен Telegram
    return j({"text": text})


@routes.post("/api/llm/detect")
async def llm_detect(_):
    from . import llm
    return j(await llm.detect_local())


@routes.get("/api/tools")
async def tools_list(_):
    return j([{"name": t.name, "description": t.description, "danger": t.danger, "group": t.group}
              for t in tools.REGISTRY.values()])


@routes.get("/api/plugins")
async def plugins_list(_):
    return j(plugins.info())


@routes.get("/api/models")
async def models_list(_):
    from . import vrm_meta
    out = []
    for p in sorted(MODELS.glob("*.vrm")):
        try:
            out.append(await asyncio.to_thread(vrm_meta.read, p, MODELS / ".thumbs"))
        except Exception as e:
            out.append({"name": p.name, "title": p.stem, "error": str(e)})
    from .tools.avatar import current_model
    per, cur = config.get("avatar.per_model") or {}, current_model()
    for m in out:
        m["settings"] = per.get(m["name"], {})
        m["current"] = m["name"] == cur
    return j(out)


@routes.post("/api/models")
async def models_upload(r):
    reader = await r.multipart()
    names = []
    while (part := await reader.next()) is not None:
        name = Path(part.filename or "").name
        if not name.lower().endswith(".vrm"):
            continue
        with open(MODELS / name, "wb") as f:
            while chunk := await part.read_chunk():
                f.write(chunk)
        names.append(name)
    if not names:
        return j({"error": "нужен файл .vrm"}, 400)
    config.update({"avatar": {"model": names[-1], "enabled": True}})
    bus.emit("config.changed")
    return j({"ok": True, "name": names[-1], "names": names})


@routes.delete("/api/models/{name}")
async def models_delete(r):
    name = Path(r.match_info["name"]).name
    if name == config.get("avatar.model"):
        return j({"error": "это текущая модель — сначала наденьте другую"}, 400)
    (MODELS / name).unlink(missing_ok=True)
    return j({"ok": True})


@routes.post("/api/avatar/model")
async def avatar_model(r):
    name = Path((await r.json())["name"]).name
    if not (MODELS / name).exists():
        return j({"error": "нет такой модели"}, 404)
    config.update({"avatar": {"model": name, "enabled": True}})
    bus.emit("config.changed")
    return j({"ok": True})


@routes.post("/api/debug/recording")
async def debug_recording(r):
    """Видео из окна аватара (avatar-debug.js → __k.record) — для проверки анимаций глазами."""
    out = DATA / "recordings"
    out.mkdir(exist_ok=True)
    name = Path(r.query.get("name") or "avatar.webm").name
    data = await r.read()
    (out / name).write_bytes(data)
    return j({"ok": True, "path": str(out / name), "mb": round(len(data) / 2**20, 2)})


@routes.post("/api/avatar/demo")
async def avatar_demo(_):
    """Показать все движения по очереди — посмотреть, как выглядят."""
    bus.emit("avatar.demo")
    return j({"ok": True})


@routes.post("/api/avatar/photo")
async def avatar_photo(r):
    """Фоторежим: поза и эмоция из «Гардероба» — окно аватара снимет себя и пришлёт PNG."""
    body = await r.json()
    bus.emit("avatar.photo", pose=body.get("pose") or None, emotion=body.get("emotion") or None)
    return j({"ok": True})


@routes.post("/api/avatar/photo/save")
async def avatar_photo_save(r):
    import base64
    import datetime as _dt
    png = base64.b64decode((await r.json())["png"])
    folder = Path.home() / "Pictures" / "Shikigami"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{config['assistant_name']}-{_dt.datetime.now():%Y%m%d-%H%M%S}.png"
    path.write_bytes(png)
    bus.emit("notify", title="Фото сохранено", text=str(path))
    bus.emit("photo.saved", path=str(path))
    return j({"ok": True, "path": str(path)})


@routes.post("/api/avatar/settings")
async def avatar_settings(r):
    body = await r.json()
    name = Path(body.pop("name")).name
    config.update({"avatar": {"per_model": {name: body}}})
    bus.emit("config.changed")
    return j({"ok": True})


ANIMS = DATA / "animations"
ANIMS.mkdir(exist_ok=True)
ANIM_KINDS = ["idle", "greet", "wave", "bow", "dance", "happy", "angry", "shy", "sad", "surprised", "think", "stretch", "sleep",
              "guide", "nod", "call", "walk"]
ANIM_EXT = (".vrma", ".fbx", ".kclip.json")    # .kclip.json — клипы из захвата движения (devtools/bvh2kclip.py)


def anim_kind(name):
    low = name.lower()
    return next((k for k in ANIM_KINDS if low.startswith(k)), "other")


_anim_checks = {}   # файл → (время изменения, отчёт)


def anim_check(p):
    """Отладчик анимаций (devtools/anim_check.py): вывернутые суставы в клипе — заранее, а не на экране."""
    if not p.name.endswith(".kclip.json"):
        return None
    m = p.stat().st_mtime
    if _anim_checks.get(p.name, (None,))[0] != m:
        import sys
        sys.path.insert(0, str(ROOT / "devtools"))
        try:
            from anim_check import check                 # инструмент разработчика: в сборку для людей не входит
        except ImportError:
            return None
        try:
            rep = check(str(p))
        except Exception as e:
            rep = {"ошибка": {"чтение": {"худший": 0, "предел": str(e), "доля": 1, "с": 0}}}
        _anim_checks[p.name] = (m, rep)
    return _anim_checks[p.name][1]


@routes.get("/api/animations")
async def animations(_):
    out = []
    for p in sorted(ANIMS.iterdir()):
        if p.is_file() and p.name.lower().endswith(ANIM_EXT):
            out.append({"name": p.name, "url": f"/animations/{p.name}", "kind": anim_kind(p.name),
                        "check": await asyncio.to_thread(anim_check, p)})
    return j(out)


@routes.post("/api/animations")
async def animations_upload(r):
    reader = await r.multipart()
    names = []
    while (part := await reader.next()) is not None:
        name = Path(part.filename or "").name
        if not name.lower().endswith(ANIM_EXT):
            continue
        with open(ANIMS / name, "wb") as f:
            while chunk := await part.read_chunk():
                f.write(chunk)
        names.append(name)
    bus.emit("animations.changed")
    return j({"ok": bool(names), "names": names})


# --- OpenAI-совместимый вход: моды и программы получают мозг и характер {name} ---

# ---------- комната (режим «Домой») ----------
ROOM_KIT = DATA / "room" / "kenney-furniture"


@routes.post("/api/avatar/home")
async def avatar_home(r):
    bus.emit("avatar.home", on=bool((await r.json()).get("on", True)))
    return j({"ok": True})


@routes.get("/api/room/catalog")
async def room_catalog(_):
    """Мебель Kenney (CC0): имя модели и картинка-превью для каталога в «Гардеробе»."""
    out = []
    for p in sorted((ROOM_KIT / "glb").glob("*.glb")):
        prev = next((f for f in (ROOM_KIT / "preview").glob(p.stem + "*.png")), None) if (ROOM_KIT / "preview").exists() else None
        out.append({"model": p.stem, "preview": f"/room/kenney-furniture/preview/{prev.name}" if prev else None})
    return j(out)


@routes.post("/api/room/add")
async def room_add(r):
    import random
    model = (await r.json()).get("model", "")
    if not (ROOM_KIT / "glb" / f"{model}.glb").exists():
        return j({"error": "нет такой мебели"}, status=400)
    layout = list(config.get("avatar.room_layout") or [])
    n = sum(1 for d in layout if d.get("model") == model)
    layout.append({"id": f"{model}{n + 1}", "model": model, "x": round(random.uniform(-1.2, 1.4), 2),
                   "z": round(random.uniform(-0.6, 1.3), 2), "rot": 0})
    config.update({"avatar": {"room_layout": layout}})
    bus.emit("config.changed")
    return j({"ok": True, "layout": layout})


@routes.post("/api/room/remove")
async def room_remove(r):
    rid = (await r.json()).get("id")
    layout = [d for d in (config.get("avatar.room_layout") or []) if d.get("id") != rid and d.get("onTop") != rid]
    config.update({"avatar": {"room_layout": layout}})
    bus.emit("config.changed")
    return j({"ok": True, "layout": layout})


@routes.get("/v1/models")
async def oa_models(_):
    return j({"object": "list", "data": [{"id": "shikigami", "object": "model", "owned_by": "local"}]})


@routes.post("/v1/chat/completions")
async def oa_chat(r):
    from . import llm, persona
    body = await r.json()
    msgs = [m for m in body.get("messages", []) if m.get("role") in ("system", "user", "assistant")]
    extra = "\n".join(m["content"] for m in msgs if m["role"] == "system" and isinstance(m.get("content"), str))
    sys_prompt = persona.system_prompt(voice=False) + (f"\n\nКонтекст от программы-клиента:\n{extra}" if extra else "")
    convo = [{"role": "system", "content": sys_prompt}] + [m for m in msgs if m["role"] != "system"][-16:]
    try:
        res, used = await llm.chat(convo, None, temperature=body.get("temperature", 0.8),
                                   max_tokens=min(int(body.get("max_tokens") or 600), 2048))
    except llm.LLMError as e:
        return j({"error": {"message": str(e)}}, 502)
    text = res["content"].strip()
    out = {"id": f"shikigami-{int(time.time() * 1000)}", "object": "chat.completion", "created": int(time.time()),
           "model": "shikigami", "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                           "finish_reason": "stop"}],
           "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
    if not body.get("stream"):
        return j(out)
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(r)
    chunk = {**out, "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}
    await resp.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\ndata: [DONE]\n\n".encode())
    return resp


def _allowed_hosts():
    port = config.get("server.port")
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


@web.middleware
async def guard(request, handler):
    """Ядро умеет управлять ПК, поэтому чужим страницам в браузере вход закрыт.

    Любой сайт может постучаться на 127.0.0.1 (WebSocket и «простые» POST браузер шлёт без спроса),
    а через подмену DNS — ещё и прикинуться локальным адресом. Пускаем только свои окна
    (Origin = адрес ядра) и программы без Origin (моды, скрипты); Host — только локальный.
    """
    hosts = _allowed_hosts()
    if request.host not in hosts:
        return web.Response(status=403, text="host not allowed")
    origin = request.headers.get("Origin")
    if origin and origin.split("://", 1)[-1] not in hosts:
        return web.Response(status=403, text="origin not allowed")
    return await handler(request)


@web.middleware
async def no_cache(request, handler):
    """Интерфейс правится часто — окна Electron не должны держать старые стили и скрипты."""
    resp = await handler(request)
    if request.path.startswith(("/ui/", "/plugins/")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def make_app():
    app = web.Application(client_max_size=300 * 2**20, middlewares=[guard, no_cache])
    app.add_routes(routes)
    from . import chatter, clip, commands, desktop
    app.add_routes(chatter.routes)
    app.add_routes(clip.routes)
    clip.start()                                   # история буфера обмена
    app.add_routes(commands.routes)
    app.add_routes(desktop.routes)
    for name, p in plugins.loaded.items():
        for method, path, fn in p["api"].routes:
            app.router.add_route(method, path, fn)
        app.router.add_static(f"/plugins/{name}/", p["api"].path)
    app.router.add_static("/ui/", UI)
    app.router.add_static("/vendor/", ROOT / "shell" / "node_modules")
    (MODELS / ".thumbs").mkdir(exist_ok=True)
    app.router.add_static("/models/", MODELS)
    app.router.add_static("/animations/", ANIMS)
    (DATA / "room").mkdir(exist_ok=True)
    app.router.add_static("/room/", DATA / "room")
    app.router.add_static("/audio/", AUDIO)
    UPLOADS.mkdir(exist_ok=True)
    app.router.add_static("/uploads/", UPLOADS)
    return app
