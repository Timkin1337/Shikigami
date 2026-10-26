"""Telegram: свой бот пользователя → команды {name} с телефона, текстом и голосовыми.

Слушается только тех, чьи ID перечислены в настройках. Чужим отвечает их ID и больше ничего —
бот выполняет команды на ПК, поэтому это по сути пульт удалённого доступа.
"""
import asyncio
import contextlib
import io
import json
import time
from pathlib import Path

import aiohttp
import numpy as np

from . import agent, bus, db
from .config import config

INBOX = Path.home() / "Downloads" / "Shikigami"   # файлы, присланные с телефона
bot = None   # работающий бот, если он есть: через него инструменты шлют файлы на телефон

API = "https://api.telegram.org/bot{token}/{method}"
FILE = "https://api.telegram.org/file/bot{token}/{path}"


class Bot:
    def __init__(self, token):
        self.token = token
        self.s = None
        self.chats = {}      # tg chat → чат {name}
        self.offset = 0

    async def call(self, method, **params):
        files = params.pop("_files", None)
        url = API.format(token=self.token, method=method)
        if files:
            form = aiohttp.FormData()
            for k, v in params.items():
                form.add_field(k, v if isinstance(v, str) else json.dumps(v))
            for k, (name, data, ctype) in files.items():
                form.add_field(k, data, filename=name, content_type=ctype)
            async with self.s.post(url, data=form) as r:
                return await r.json()
        async with self.s.post(url, json=params) as r:
            return await r.json()

    def allowed(self, uid):
        return uid in (config.get("telegram.allowed_ids") or [])

    async def run(self):
        timeout = aiohttp.ClientTimeout(total=70)
        async with aiohttp.ClientSession(timeout=timeout) as self.s:
            me = await self.call("getMe")
            if not me.get("ok"):
                print("Telegram: неверный токен", me, flush=True)
                return
            print(f"Telegram: бот @{me['result']['username']} на связи", flush=True)
            fwd = asyncio.create_task(self.forward_events())
            try:
                await self.poll()
            finally:
                fwd.cancel()

    async def poll(self):
        while True:
            try:
                res = await self.call("getUpdates", offset=self.offset, timeout=50,
                                      allowed_updates=["message", "callback_query"])
                if not res.get("ok"):   # 409 — этот же бот опрашивает другая программа
                    print("Telegram:", res.get("description"), flush=True)
                    await asyncio.sleep(10)
                for u in res.get("result", []):
                    self.offset = u["update_id"] + 1
                    asyncio.create_task(self.safe_update(u))
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await asyncio.sleep(3)

    async def safe_update(self, u):
        try:
            await self.on_update(u)
        except Exception as e:
            import traceback
            traceback.print_exc()
            chat = (u.get("message") or {}).get("chat", {}).get("id")
            if chat:
                with contextlib.suppress(Exception):
                    await self.call("sendMessage", chat_id=chat, text=f"Ой, ошибка: {type(e).__name__}: {e}")

    async def on_update(self, u):
        if "callback_query" in u:
            cq = u["callback_query"]
            if not self.allowed(cq["from"]["id"]):
                return
            parts = (cq.get("data") or "").split(":")
            if len(parts) != 3 or parts[0] != "c":   # кнопки, оставшиеся от прежней программы бота
                await self.call("answerCallbackQuery", callback_query_id=cq["id"], text="Эта кнопка больше не работает")
                return
            _, cid, ok = parts
            agent.resolve_confirm(cid, ok == "1")
            await self.call("answerCallbackQuery", callback_query_id=cq["id"], text="Да" if ok == "1" else "Нет")
            await self.call("editMessageReplyMarkup", chat_id=cq["message"]["chat"]["id"],
                            message_id=cq["message"]["message_id"], reply_markup={"inline_keyboard": []})
            return
        m = u.get("message") or {}
        uid, chat = m.get("from", {}).get("id"), m.get("chat", {}).get("id")
        if not uid:
            return
        if not self.allowed(uid):
            await self.call("sendMessage", chat_id=chat,
                            text=f"Хмпф. Я вас не знаю. Ваш ID: {uid}\n"
                                 f"Если вы хозяин — впишите его в настройках {config['assistant_name']} (Telegram → разрешённые ID).")
            return
        if m.get("text", "").startswith("/start"):
            # remove_keyboard убирает кнопки, которые оставил прежний бот на этом же токене
            await self.call("sendMessage", chat_id=chat, reply_markup={"remove_keyboard": True},
                            text=f"Я здесь, {config['address']}. Пишите или присылайте голосовые — я на связи с вашим ПК.\n"
                                 "Можно прислать файл или фото — положу в Загрузки\\Shikigami. "
                                 "Попросите «скинь скриншот экрана» — пришлю.")
            return
        voice_in = False
        if "voice" in m or "audio" in m:
            voice_in = True
            text = await self.transcribe(m.get("voice") or m.get("audio"))
            if not text:
                await self.call("sendMessage", chat_id=chat, text="Не расслышала, повторите?")
                return
            await self.call("sendMessage", chat_id=chat, text=f"🎙 «{text}»")
        else:
            text = m.get("text") or m.get("caption") or ""
            saved = await self.save_incoming(m)
            if saved:
                if not text.strip():
                    await self.call("sendMessage", chat_id=chat, text=f"Сохранила: {saved}")
                    return
                text = f"[Пользователь прислал с телефона файл, он сохранён на ПК: {saved}]\n{text}"
        if not text.strip():
            return
        kchat = self.chats.get(chat)
        if kchat is None:
            row = db.one("SELECT id FROM chats WHERE title=?", (f"Telegram {chat}",))
            kchat = row["id"] if row else db.new_chat(f"Telegram {chat}")
            self.chats[chat] = kchat
        self.active_chat = chat
        typing = asyncio.create_task(self.keep_typing(chat))   # «печатает…» гаснет через 5 с, держим его
        try:
            _, reply = await agent.handle_user(text, kchat, voice=False, source="telegram")
        finally:
            typing.cancel()
        if not reply:
            return
        for i in range(0, len(reply), 4000):
            await self.call("sendMessage", chat_id=chat, text=reply[i:i + 4000])
        if voice_in:
            await self.send_voice(chat, reply)

    async def keep_typing(self, chat):
        while True:
            with contextlib.suppress(Exception):
                await self.call("sendChatAction", chat_id=chat, action="typing")
            await asyncio.sleep(4.5)

    async def download(self, file_id):
        info = await self.call("getFile", file_id=file_id)
        path = info.get("result", {}).get("file_path")
        if not path:
            raise RuntimeError(info.get("description") or "Telegram не отдал файл (больше 20 МБ?)")
        async with self.s.get(FILE.format(token=self.token, path=path)) as r:
            return path, await r.read()

    async def save_incoming(self, m):
        """Фото, видео и документы с телефона → Загрузки\\Shikigami. Возвращает путь или None."""
        if "document" in m:
            f = m["document"]
            name = f.get("file_name") or f"file_{f['file_unique_id']}"
        elif "photo" in m:
            f = m["photo"][-1]   # самый большой размер
            name = time.strftime("photo_%Y%m%d_%H%M%S.jpg")
        elif "video" in m:
            f = m["video"]
            name = f.get("file_name") or time.strftime("video_%Y%m%d_%H%M%S.mp4")
        else:
            return None
        _, data = await self.download(f["file_id"])
        INBOX.mkdir(parents=True, exist_ok=True)
        dst = INBOX / Path(name).name
        n = 1
        while dst.exists():
            dst = INBOX / f"{Path(name).stem} ({n}){Path(name).suffix}"
            n += 1
        dst.write_bytes(data)
        return str(dst)

    async def send_file(self, path, caption=""):
        """Отправить файл с ПК всем хозяевам бота. Картинки — фото, остальное — документом."""
        p = Path(path)
        data = p.read_bytes()
        if len(data) > 49 * 1024 * 1024:
            raise RuntimeError("файл больше 50 МБ — Telegram такой не примет")
        photo = p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") and len(data) < 9 * 1024 * 1024
        method, field = ("sendPhoto", "photo") if photo else ("sendDocument", "document")
        ok = []
        for uid in config.get("telegram.allowed_ids") or []:
            res = await self.call(method, chat_id=str(uid), caption=caption[:1000],
                                  _files={field: (p.name, data, "application/octet-stream")})
            ok.append(bool(res.get("ok")))
            if not res.get("ok"):
                raise RuntimeError(res.get("description") or "Telegram не принял файл")
        return bool(ok)

    async def transcribe(self, f):
        _, data = await self.download(f["file_id"])
        from .voice import stt
        audio = await asyncio.to_thread(_decode_to_16k, data)
        return await asyncio.to_thread(stt.transcribe, audio, None, f"Ассистентку зовут {config['assistant_name']}.", True)

    async def send_voice(self, chat, text):
        from .voice import _tts
        from .persona import speech_text
        spoken = speech_text(text)
        if not spoken:
            return
        cfg = config["voice"]
        audio, sr = await asyncio.to_thread(_tts().synth, spoken, cfg["tts_speaker"], cfg["tts_pitch"], cfg["tts_rate"],
                                            cfg.get("tts_engine"), cfg.get("edge_voice"))
        ogg = await asyncio.to_thread(_encode_ogg, audio, sr)
        await self.call("sendVoice", chat_id=str(chat), _files={"voice": ("voice.ogg", ogg, "audio/ogg")})

    async def forward_events(self):
        """Подтверждения опасных действий и напоминания — тоже в Telegram."""
        q = bus.subscribe()
        try:
            while True:
                m = await q.get()
                ids = config.get("telegram.allowed_ids") or []
                try:
                    if m["type"] == "confirm.request" and m.get("source") == "telegram":
                        for uid in ids:
                            await self.call("sendMessage", chat_id=uid, text=f"Разрешить?\n{m['text']}", reply_markup={
                                "inline_keyboard": [[{"text": "Да", "callback_data": f"c:{m['id']}:1"},
                                                     {"text": "Нет", "callback_data": f"c:{m['id']}:0"}]]})
                    elif m["type"] == "notify" and m.get("kind") == "reminder":
                        for uid in ids:
                            await self.call("sendMessage", chat_id=uid, text=f"🔔 {m['text']}")
                except aiohttp.ClientError:
                    pass
        finally:
            bus.unsubscribe(q)


def _decode_to_16k(data):
    import av
    with av.open(io.BytesIO(data)) as c:
        res = av.AudioResampler(format="flt", layout="mono", rate=16000)
        chunks = []
        for frame in c.decode(audio=0):
            for f in res.resample(frame):
                chunks.append(f.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(1, np.float32)


def _encode_ogg(audio, sr):
    import av
    buf = io.BytesIO()
    with av.open(buf, "w", format="ogg") as c:
        st = c.add_stream("libopus", rate=48000)
        st.layout = "mono"
        res = av.AudioResampler(format="s16", layout="mono", rate=48000)
        pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)[None, :]
        frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = sr
        for f in res.resample(frame):
            for p in st.encode(f):
                c.mux(p)
        for p in st.encode(None):
            c.mux(p)
    return buf.getvalue()


async def run():
    """Следит за настройками: бот включается, выключается и меняет токен без перезапуска ядра."""
    global bot
    task, key, failed_at = None, None, 0.0
    while True:
        token = config.secret(config.get("telegram.token_env")) if config.get("telegram.enabled") else ""
        if task and task.done():
            if not task.cancelled() and task.exception():
                print("Telegram: бот упал:", repr(task.exception()), flush=True)
            failed_at, task, bot = time.time(), None, None
        if token != key or (task is None and token and time.time() - failed_at > 30):
            if task:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                print("Telegram: бот остановлен", flush=True)
            bot = Bot(token) if token else None
            task, key = (asyncio.create_task(bot.run()) if bot else None), token
        await asyncio.sleep(3)
