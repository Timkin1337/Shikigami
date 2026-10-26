"""Голос: синтез речи и слушание микрофона. Движки грузятся лениво — без них ядро работает молча.

Кто воспроизводит речь: окно аватара (там же липсинк), если его нет — главное окно,
если не открыто ничего — само ядро через колонки.
"""
import asyncio
import uuid

from .. import bus
from ..config import config, DATA

AUDIO = DATA / "audio"
AUDIO.mkdir(exist_ok=True)
state = {"listener": None, "tts": None, "speaking": False}
_ended = {}
_speak_lock = asyncio.Lock()


def _tts():
    if state["tts"] is None:
        from .tts import make_engine
        state["tts"] = make_engine()
    return state["tts"]


def speech_ended(sid):
    f = _ended.pop(sid, None)
    if f and not f.done():
        f.set_result(True)


def busy():
    """Говорит или уже ждёт очереди — для реплик, которым важно прозвучать сразу или никогда."""
    return state["speaking"] or _speak_lock.locked()


async def speak(text, emotion="neutral", resume=None, cfg=None, fresh_for=None):
    """cfg — разово другие настройки голоса (кнопка «Прослушать» в настройках).
    fresh_for — сколько секунд реплика актуальна: «Сзади крипер!», дождавшееся конца длинного ответа, — уже враньё."""
    import time as _t
    t0 = _t.monotonic()
    from ..persona import clean
    text = clean(text)              # без ремарок в звёздочках и заиканий «Н-не» — и в облачке, и в голосе
    import sys as _sys
    import os as _os
    if "--mute" in _sys.argv or _os.environ.get("SHIKIGAMI_MUTE"):  # тесты: без звука, чтобы не мешать хозяину играть
        return
    if not text or not text.strip():
        return
    async with _speak_lock:
        if fresh_for is not None and _t.monotonic() - t0 > fresh_for:
            return
        from ..server import client_roles
        cfg = {**config["voice"], **(cfg or {})}
        from ..persona import speech_text
        spoken = speech_text(text)
        if not spoken:
            return
        # длинный ответ голосом с эмоциями — конвейером: первое предложение звучит, пока готовится следующее
        # (фраза синтезируется ~2 с — без этого длинный ответ молчал бы, пока не готов целиком)
        es = {k: cfg[f"espeech_{k}"] for k in ("voice", "strength", "steps") if cfg.get(f"espeech_{k}")}
        parts = _sentences(spoken) if cfg.get("tts_engine") == "espeech" and len(spoken) > 140 else [spoken]

        def synth(chunk):
            return asyncio.to_thread(_tts().synth, chunk, cfg["tts_speaker"], cfg["tts_pitch"], cfg["tts_rate"],
                                     cfg.get("tts_engine"), cfg.get("edge_voice"), emotion, es)
        nxt = asyncio.create_task(synth(parts[0]))
        roles = client_roles()
        player = "avatar" if "avatar" in roles else "main" if "main" in roles else "core"
        lst = state["listener"]
        if lst:
            lst.paused = True
        state["speaking"] = True
        bus.emit("voice.state", state="speaking")
        try:
            for i in range(len(parts)):
                try:
                    audio, SR = await nxt
                except Exception as e:
                    bus.emit("voice.state", state="error", text=f"синтез речи: {e}")
                    bus.emit("speech", text=text, url=None, emotion=emotion)
                    return
                if i + 1 < len(parts):
                    nxt = asyncio.create_task(synth(parts[i + 1]))
                await _play(audio, SR, text, emotion, player)
        finally:
            state["speaking"] = False
            await asyncio.sleep(0.25)  # хвост эха из колонок
            if lst:
                lst.paused = False
                if resume == "once":
                    lst.listen_once()
            bus.emit("voice.state", state="wake" if resume == "once" else "idle")


def _sentences(text, limit=220):
    """Куски по предложениям, не длиннее limit: первый — покороче, чтобы зазвучало быстрее."""
    import re
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?…])\s+", text):
        cap = 90 if not out else limit
        if cur and len(cur) + len(sent) > cap:
            out.append(cur)
            cur = ""
        cur = (cur + " " + sent).strip()
    if cur:
        out.append(cur)
    return [x for x in out if x.strip()] or [text]


async def _play(audio, SR, text, emotion, player):
    from .tts import save_wav
    sid = uuid.uuid4().hex[:10]
    path = AUDIO / f"{sid}.wav"
    await asyncio.to_thread(save_wav, path, audio, SR)
    _cleanup()
    dur = len(audio) / SR
    if player == "core":
        await asyncio.to_thread(_play_local, audio, SR)
        return
    fut = asyncio.get_running_loop().create_future()
    _ended[sid] = fut
    bus.emit("speech", id=sid, url=f"/audio/{sid}.wav", text=text, emotion=emotion, player=player, duration=dur)
    try:
        await asyncio.wait_for(fut, dur + 5)
    except asyncio.TimeoutError:
        pass
    finally:
        _ended.pop(sid, None)


def _play_local(audio, sr):
    import sounddevice as sd
    sd.play(audio, sr)
    sd.wait()


def _cleanup(keep=30):
    files = sorted(AUDIO.glob("*.wav"), key=lambda p: p.stat().st_mtime)
    for f in files[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass


async def start():
    """Запустить микрофон. Ошибки не роняют ядро: без микрофона {name} просто не слышит."""
    loop = asyncio.get_running_loop()
    from .listener import Listener
    from ..agent import handle_user

    async def on_text(text):
        bus.emit("voice.heard", text=text)
        await handle_user(text, voice=True, source="voice")

    try:
        lst = Listener(loop, on_text)
        await asyncio.to_thread(lst.start)
        state["listener"] = lst
        bus.emit("voice.state", state="idle")
        from . import hotkey
        hotkey.start()                                # своя кнопка голоса (клавиша или кнопка мыши)
    except ImportError as e:
        print("распознавание речи не установлено:", e, flush=True)
        bus.emit("voice.state", state="error", text="распознавание речи не установлено — вкладка «Модули»")
    except Exception as e:
        print("микрофон не запустился:", e, flush=True)
        bus.emit("voice.state", state="error", text=f"микрофон: {e}")
    # синтез прогреваем заранее, чтобы первая фраза не ждала загрузки модели
    try:
        await asyncio.to_thread(_tts().load)
    except Exception as e:
        print("синтез речи не загрузился:", e, flush=True)


async def push_to_talk(down=True):
    if state["listener"]:
        state["listener"].ptt(down)


def listen_once():
    if state["listener"]:
        state["listener"].listen_once()
