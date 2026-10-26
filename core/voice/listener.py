"""Слушание микрофона: детектор речи → фраза → поиск имени → команда агенту.

Режимы:
  wake  — обычный: реагирует только на фразы, которые начинаются с имени («{name}, …»);
  once  — следующая фраза целиком считается командой (после «{name}?» или Ctrl+Alt+Space);
  ptt   — пока зажата кнопка микрофона, пишется всё подряд.
"""
import difflib
import glob
import os
import queue
import re
import threading
import time

import numpy as np

from .. import bus
from ..config import config

SR = 16000
CHUNK = 512           # 32 мс — столько ест детектор Silero за раз
PRE_ROLL = 10         # 320 мс до начала речи, чтобы не съедать первый слог
END_SILENCE = 22      # ~700 мс тишины — конец фразы
MAX_CHUNKS = int(15 * SR / CHUNK)


class VAD:
    def __init__(self):
        import onnxruntime as ort
        import faster_whisper
        path = glob.glob(os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad*.onnx"))[0]
        o = ort.SessionOptions()
        o.inter_op_num_threads = o.intra_op_num_threads = 1
        o.log_severity_level = 4
        self.s = ort.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=o)
        self.reset()

    def reset(self):
        self.h = np.zeros((1, 1, 128), np.float32)
        self.c = np.zeros((1, 1, 128), np.float32)
        self.ctx = np.zeros(64, np.float32)

    def __call__(self, chunk):
        x = np.concatenate([self.ctx, chunk])[None, :].astype(np.float32)
        p, self.h, self.c = self.s.run(None, {"input": x, "h": self.h, "c": self.c})
        self.ctx = chunk[-64:]
        return float(p.reshape(-1)[0])


def _norm(s):
    return re.sub(r"[^\wё ]", " ", s.lower().replace("ё", "е")).split()


def strip_wake(text, wake):
    """Если фраза начинается с имени (с учётом ошибок распознавания) — вернуть остаток, иначе None."""
    words = _norm(text)
    w = wake.lower().replace("ё", "е")
    for n in (1, 2):  # имя иногда слышится раздельно («ко хаку»)
        head = "".join(words[:n])
        if head and difflib.SequenceMatcher(None, head, w).ratio() >= 0.72:
            rest = re.split(r"[\s,.!?]+", text.strip(), maxsplit=n)
            return rest[n].strip(" ,.!?") if len(rest) > n else ""
    # «Эй, {name}» / «Слушай, {name}»
    if len(words) > 1 and difflib.SequenceMatcher(None, words[1], w).ratio() >= 0.75:
        rest = re.split(r"[\s,.!?]+", text.strip(), maxsplit=2)
        return rest[2].strip(" ,.!?") if len(rest) > 2 else ""
    return None


def name_prob(words, wake):
    """Вероятность, с которой Whisper расслышал имя в первых словах (0 — имени нет)."""
    w = wake.lower().replace("ё", "е")
    best, glued = 0.0, []
    for word, p in words[:3]:
        glued.append((_norm(word), p))
    for i, (ws, p) in enumerate(glued):
        for n in (1, 2):  # «ко хаку» — два слова
            part = glued[i:i + n]
            head = "".join("".join(x[0]) for x in part)
            if head and difflib.SequenceMatcher(None, head, w).ratio() >= 0.72:
                best = max(best, min(x[1] for x in part))
    return best


def default_mic():
    """(id, имя) основного микрофона Windows или (None, None)."""
    try:
        from pycaw.pycaw import AudioUtilities
        m = AudioUtilities.GetMicrophone()
        return m.GetId(), AudioUtilities.CreateDevice(m).FriendlyName
    except Exception:
        return None, None


def input_devices():
    """Микрофоны для выбора в настройках: имена устройств MME (их PortAudio открывает с любой частотой)."""
    import sounddevice as sd
    mme = next((i for i, h in enumerate(sd.query_hostapis()) if h["name"] == "MME"), None)
    names = []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0 and (mme is None or d["hostapi"] == mme):
            n = d["name"].strip()
            if n and n not in names and "Microsoft Sound Mapper" not in n and "Переназначение звуковых" not in n:
                names.append(n)
    return names


def _device_index(name):
    """Номер устройства по имени из настроек; None — основной микрофон Windows."""
    if not name:
        return None
    if isinstance(name, int):
        return name
    import sounddevice as sd
    mme = next((i for i, h in enumerate(sd.query_hostapis()) if h["name"] == "MME"), None)
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and d["name"].strip() == name and (mme is None or d["hostapi"] == mme):
            return i
    print(f"микрофон «{name}» не найден — слушаю основной микрофон Windows", flush=True)
    return None


class Listener:
    def __init__(self, loop, on_text):
        self.loop = loop
        self.on_text = on_text          # async def on_text(text)
        self.q = queue.Queue(maxsize=400)
        self.mode = "wake"
        self.once_deadline = 0
        self.paused = False
        self.ptt_buf = None
        self.stream = None
        self.work = queue.Queue()

    def emit(self, state, **kw):
        self.loop.call_soon_threadsafe(lambda: bus.emit("voice.state", state=state, **kw))

    # --- поток микрофона ---
    def start(self):
        self._open()
        threading.Thread(target=self._loop, daemon=True, name="vad").start()
        threading.Thread(target=self._worker, daemon=True, name="stt").start()
        threading.Thread(target=self._watch_mic, daemon=True, name="mic-watch").start()

    def _open(self):
        import sounddevice as sd
        self.dev_cfg = config.get("voice.input_device") or ""
        dev = _device_index(self.dev_cfg)
        self.mic_id = default_mic()[0]
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=CHUNK,
                                     device=dev, callback=self._cb)
        self.stream.start()
        name = sd.query_devices(self.stream.device)["name"]
        print(f"микрофон слушается: {name}", flush=True)
        self.emit("mic", name=name)

    def _watch_mic(self):
        """Основной микрофон Windows сменился (подключили USB-микрофон, отключили гарнитуру) — переходим на него.
        Поток звука привязывается к устройству при открытии: 18.10 ядро стартовало раньше, чем подключился
        ME6S, и до перезапуска слушало встроенный микрофон ноутбука — распознавание стало «через раз»."""
        import comtypes
        comtypes.CoInitialize()
        from . import state
        while True:
            time.sleep(2)
            want = config.get("voice.input_device") or ""
            if want != self.dev_cfg and not state.get("speaking"):   # в настройках выбрали другой микрофон
                print(f"микрофон из настроек → {want or 'основной Windows'}, переключаюсь", flush=True)
                try:
                    self._reopen()
                except Exception as e:
                    print("не удалось переключить микрофон:", e, flush=True)
                    self.dev_cfg = want
                continue
            if want:                                              # микрофон выбран вручную — за Windows не следим
                continue
            mic_id, name = default_mic()
            if not mic_id or mic_id == self.mic_id or state.get("speaking"):   # пока говорит, звук занят
                continue
            print(f"основной микрофон сменился → {name}, переключаюсь", flush=True)
            try:
                self._reopen()
            except Exception as e:
                print("не удалось переключить микрофон:", e, flush=True)
                self.mic_id = mic_id                                # не долбить каждые 2 с

    def _reopen(self):
        import sounddevice as sd
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        # PortAudio запоминает список устройств при запуске — новый микрофон виден только после перезапуска
        sd._terminate()
        sd._initialize()
        self._open()

    def _cb(self, data, frames, t, status):
        try:
            self.q.put_nowait(data[:, 0].copy())
        except queue.Full:
            pass

    def listen_once(self, timeout=7):
        self.mode = "once"
        self.once_deadline = time.time() + timeout
        self.emit("wake")

    def ptt(self, down):
        if down:
            self.ptt_buf = []
            self.emit("listening")
        elif self.ptt_buf is not None:
            buf, self.ptt_buf = self.ptt_buf, None
            if len(buf) > 8:
                self.work.put(("cmd", np.concatenate(buf)))
            else:
                self.emit("idle")

    def _startle(self, chunk, speaking):
        """Хлопок, удар, грохот — резкий скачок громкости над фоном комнаты. Только уровень, звук никуда не идёт.
        Не во время её собственной речи (колонки), не во время игры и не чаще раза в 90 с — пугливость надоедает."""
        rms = float(np.sqrt(np.mean(chunk * chunk)))
        base = getattr(self, "noise", 0.01)
        loud = rms > 0.12 and rms > base * 9 and float(np.max(np.abs(chunk))) > 0.55
        if not loud:
            self.noise = base * 0.995 + max(rms, 0.002) * 0.005            # фон комнаты — медленное среднее
            return
        now = time.time()
        if speaking or now - getattr(self, "last_startle", 0) < 90 or not config.get("behavior.startle", True):
            return
        from . import state
        from ..behavior import game
        if state.get("speaking") or game.get("on"):
            return
        self.last_startle = now
        self.loop.call_soon_threadsafe(lambda: bus.emit("avatar.startle", level=round(rms, 3)))

    def _loop(self):
        vad = VAD()
        pre, seg, silence, speaking, voiced = [], [], 0, False, 0
        while True:
            chunk = self.q.get()
            try:
                self._startle(chunk, speaking)
            except Exception as e:
                print("вздрагивание:", e, flush=True)
            if self.ptt_buf is not None:
                self.ptt_buf.append(chunk)
                continue
            if self.paused:
                pre, seg, speaking = [], [], False
                vad.reset()
                continue
            if self.mode == "once" and time.time() > self.once_deadline and not speaking:
                self.mode = "wake"
                self.emit("idle")
            p = vad(chunk)
            if not speaking:
                pre = (pre + [chunk])[-PRE_ROLL:]
                voiced = voiced + 1 if p > 0.5 else 0
                if voiced >= 3:
                    speaking, seg, silence = True, list(pre), 0
                    if self.mode == "once":
                        self.emit("listening")
                continue
            seg.append(chunk)
            silence = silence + 1 if p < 0.35 else 0
            if silence >= END_SILENCE or len(seg) >= MAX_CHUNKS:
                audio = np.concatenate(seg)
                speaking, pre, seg = False, [], []
                vad.reset()
                if len(audio) < SR * 0.35:
                    continue
                self.work.put(("cmd" if self.mode == "once" else "wake", audio))
                if self.mode == "once":
                    self.mode = "wake"

    # --- распознавание отдельно, чтобы не терять звук ---
    def _worker(self):
        from . import stt
        wake = config.get("voice.wake_word") or config["assistant_name"]
        # Подсказка именем как началом фразы («{name}, ») заставляет Whisper считать имя уже сказанным
        # и выкидывать его из текста. Описательная подсказка узнаёт имя, но не съедает его.
        prompt = stt.hint()
        try:  # прогрев: первая загрузка моделей долгая
            stt.model("tiny")
            stt.model(config.get("voice.stt_model", "small"))
        except Exception as e:
            self.emit("error", text=f"Whisper: {e}")
        while True:
            kind, audio = self.work.get()
            # имя, город и слово-триггер могли поменять в настройках — берём свежие на каждую фразу
            wake = config.get("voice.wake_word") or config["assistant_name"]
            prompt = stt.hint()
            try:
                if kind == "wake":
                    head = audio[: int(SR * 2.5)]
                    quick, qwords = stt.transcribe(head, "tiny", prompt=prompt, fast=True, words=True)
                    if strip_wake(quick, wake) is None:
                        continue
                    self.emit("recognizing")
                    full = stt.transcribe(audio, prompt=prompt, fast=True) if len(audio) > SR * 1.2 else quick
                    rest = strip_wake(full, wake)
                    if rest is None:  # точная модель не услышала имя — верим быстрой, только если она в имени уверена
                        p = name_prob(qwords, wake)
                        if p < config.get("voice.wake_min_prob", 0.4):
                            # так в команды попадала речь из видео: tiny с подсказкой «додумывала» имя
                            print(f"отбой (имя не подтвердилось, p={p:.2f}): {full}", flush=True)
                            self.emit("idle")
                            continue
                        print(f"имя слышала только tiny (p={p:.2f}) — принимаю", flush=True)
                        rest = full if len(audio) > SR * 1.2 else (strip_wake(quick, wake) or "")
                    if len(re.sub(r"\W", "", rest)) < 2:
                        self._ack()
                        continue
                    self._dispatch(rest)
                else:
                    self.emit("recognizing")
                    text = stt.transcribe(audio, prompt=prompt, fast=True)
                    cleaned = strip_wake(text, wake)
                    text = cleaned if cleaned else text
                    if len(re.sub(r"\W", "", text)) < 2:
                        self.emit("idle")
                        continue
                    self._dispatch(text)
            except Exception as e:
                self.emit("error", text=str(e))

    def _ack(self):
        """Позвали по имени и замолчали. Раньше она отвечала «Да?» голосом — а пока говорит, микрофон выключен,
        и команда, которую уже начали произносить, обрывалась. Теперь отклик молчаливый: уши торчком, кивок,
        «слушаю…» — и следующая фраза целиком считается командой. Голосом — только если включено voice.ack_voice."""
        import asyncio
        if config.get("voice.ack_voice", False):
            from . import speak
            self.listen_once()
            asyncio.run_coroutine_threadsafe(speak("Да?", emotion="surprised", resume="once"), self.loop)
            return
        self.listen_once(timeout=8)
        self.loop.call_soon_threadsafe(lambda: bus.emit("avatar.gesture", name="nod", seconds=1.2))

    def _dispatch(self, text):
        import asyncio
        self.emit("idle")
        print(f"🎙 {text}", flush=True)
        asyncio.run_coroutine_threadsafe(self.on_text(text), self.loop)
