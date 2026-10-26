"""Синтез речи: Silero (русские голоса, CPU, бесплатно для личного использования)."""
import re
import threading
import urllib.request
import wave
from pathlib import Path

import numpy as np

from ..config import ASCII_HOME, _cache as _cache_dir

# Качает download-voices.bat (без VPN). Путь латиницей: torch.package открывает файл через fopen,
# а тот не понимает кириллицу в пути (ASCII_HOME — core/config.py).
MODELS = ASCII_HOME / "silero"
URLS = [(v, f"https://models.silero.ai/models/tts/ru/{v}.pt") for v in ("v5_6_ru", "v5_ru", "v4_ru")]
SR = 48000

UNITS = [(r"(\d)\s*%", r"\1 процентов"), (r"(\d)\s*°C?", r"\1 градусов"), (r"(\d)\s*ГБ", r"\1 гигабайт"),
         (r"(\d)\s*МБ", r"\1 мегабайт"), (r"(\d)\s*₽", r"\1 рублей"), (r"(\d)\s*\$", r"\1 долларов"),
         (r"(\d)\s*€", r"\1 евро"), (r"(\d)\s*м/с", r"\1 метров в секунду"), (r"\bUSD\b", "доллар"),
         (r"\bEUR\b", "евро"), (r"\bCNY\b", "юань"), (r"\bRUB\b", "российских рублей"), (r"\bBYN\b", "белорусских рублей"),
         (r"(\d)\s*Br\b", r"\1 рублей"), (r"\bCPU\b", "процессор"),
         (r"\bGPU\b", "видеокарта"), (r"\bRAM\b", "память"), (r"\bID\b", "номер")]


def _num_words(text):
    from num2words import num2words
    text = re.sub(r"(\d)[ \u00a0](\d{3})\b", r"\1\2", text)          # 14 057 → 14057
    text = re.sub(r"(\d+)[.,](\d{3,})", lambda m: f"{m.group(1)},{m.group(2)[:2]}", text)  # 83.6177 → 83,61
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{int(m.group(1))} {m.group(2) if m.group(2) != '00' else 'ноль ноль'}", text)

    def dec(m):
        a, b = m.group(1), m.group(2)
        w = num2words(int(a), lang="ru") + " целых " + num2words(int(b), lang="ru")
        return w + (" десятых" if len(b) == 1 else " сотых")
    text = re.sub(r"(\d+)[.,](\d{1,2})\b", dec, text)
    return re.sub(r"\d+", lambda m: num2words(int(m.group(0)), lang="ru"), text)


SPOKEN = [(r"(?i)\bхмпф\b", "хм"), (r"(?i)\bхмф\b", "хм"), (r"(?i)\bмо-у\b", "моу"), (r"(?i)\bара-ара\b", "ара ара")]


def normalize(text):
    for pat, rep in SPOKEN:          # междометия, которые синтез читал по буквам («Х-М-П-Ф»)
        text = re.sub(pat, rep, text)
    for pat, rep in UNITS:
        text = re.sub(pat, rep, text)
    from ..i18n import lang
    if lang() == "ru":
        text = _num_words(text)
    text = re.sub(r"[«»\"“”()\[\]]", "", text)
    text = text.replace("—", ", ").replace("…", ".")
    text = re.sub(r"[^\w\s.,!?:;\-ёЁ+]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class SileroTTS:
    def __init__(self):
        self.model = None
        self.version = None
        self.lock = threading.Lock()

    def load(self):
        import torch
        torch.set_num_threads(4)
        MODELS.mkdir(parents=True, exist_ok=True)
        last = None
        for ver, url in URLS:
            f = MODELS / f"{ver}.pt"
            if not f.exists():
                continue
            try:
                if not f.exists():
                    print(f"скачиваю голосовую модель {ver}…", flush=True)
                    tmp = f.with_suffix(".part")
                    urllib.request.urlretrieve(url, tmp)
                    tmp.rename(f)
                imp = torch.package.PackageImporter(str(f))
                self.model = imp.load_pickle("tts_models", "model")
                self.model.to(torch.device("cpu"))
                self.version = ver
                print(f"голос {ver}: {self.model.speakers}", flush=True)
                return
            except Exception as e:
                last = e
                print(f"модель {ver} не подошла: {e}", flush=True)
        raise RuntimeError(f"не удалось загрузить Silero: {last}")

    def synth(self, text, speaker="xenia", pitch="medium", rate="medium"):
        """Возвращает (float32-массив, частота)."""
        with self.lock:
            if self.model is None:
                self.load()
            if speaker not in self.model.speakers:
                speaker = self.model.speakers[0]
            out = []
            for chunk in _chunks(normalize(text)):
                ssml = f'<speak><prosody pitch="{pitch}" rate="{rate}">{chunk}</prosody></speak>'
                try:
                    audio = self.model.apply_tts(ssml_text=ssml, speaker=speaker, sample_rate=SR)
                except Exception:
                    audio = self.model.apply_tts(text=chunk, speaker=speaker, sample_rate=SR)
                out.append(audio.numpy())
                out.append(np.zeros(int(SR * 0.12), dtype=np.float32))
            return (np.concatenate(out) if out else np.zeros(1, dtype=np.float32)), SR


PIPER = _cache_dir / "models" / "piper" / "vits-piper-ru_RU-irina-medium"   # data/cache или SHIKIGAMI_CACHE
PIPER_ASCII = ASCII_HOME / "piper" / "vits-piper-ru_RU-irina-medium"
PITCH = {"x-low": 0.88, "low": 0.94, "medium": 1.0, "high": 1.12, "x-high": 1.22}
RATE = {"x-slow": 0.75, "slow": 0.88, "medium": 1.0, "fast": 1.15, "x-fast": 1.3}


class PiperTTS:
    """Запасной голос (Piper «Ирина» через sherpa-onnx). Высота — трюком: синтезируем медленнее
    и ускоряем пересэмплированием, темп остаётся прежним, а голос становится выше и моложе."""

    def __init__(self):
        self.tts = None
        self.version = "piper-irina"
        self.lock = threading.Lock()
        self.speakers = ["irina"]

    def load(self):
        import sherpa_onnx
        # espeak внутри Piper открывает файлы узкими C-путями и не переваривает кириллицу
        # в пути, поэтому модель видна ему через латинский junction в профиле.
        base = PIPER_ASCII if PIPER_ASCII.exists() else PIPER
        onnx = next(base.glob("*.onnx"))
        cfg = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(
                vits=sherpa_onnx.OfflineTtsVitsModelConfig(model=str(onnx), tokens=str(base / "tokens.txt"),
                                                           data_dir=str(base / "espeak-ng-data")),
                num_threads=4, provider="cpu"),
            max_num_sentences=2)
        self.tts = sherpa_onnx.OfflineTts(cfg)
        print("голос: Piper (Ирина)", flush=True)

    def synth(self, text, speaker=None, pitch="medium", rate="medium"):
        with self.lock:
            if self.tts is None:
                self.load()
            f = PITCH.get(pitch, 1.0)
            speed = RATE.get(rate, 1.0) / f
            out = []
            for chunk in _chunks(normalize(text)):
                a = self.tts.generate(chunk, sid=0, speed=speed)
                x = np.asarray(a.samples, dtype=np.float32)
                sr = a.sample_rate
                if f != 1.0 and len(x) > 1:
                    n = int(len(x) / f)
                    x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
                out.append(x)
                out.append(np.zeros(int(sr * 0.12), dtype=np.float32))
            return (np.concatenate(out) if out else np.zeros(1, dtype=np.float32)), (sr if out else 22050)


EDGE_PITCH = {"x-low": "-20Hz", "low": "-10Hz", "medium": "+0Hz", "high": "+18Hz", "x-high": "+32Hz"}
EDGE_RATE = {"x-slow": "-25%", "slow": "-12%", "medium": "+0%", "fast": "+12%", "x-fast": "+25%"}
EDGE_VOICES = ["ru-RU-SvetlanaNeural", "en-US-AvaMultilingualNeural", "en-US-EmmaMultilingualNeural",
               "fr-FR-VivienneMultilingualNeural", "de-DE-SeraphinaMultilingualNeural", "pt-BR-ThalitaMultilingualNeural"]


class EdgeTTS:
    """Нейроголоса Microsoft (как «Прочитать вслух» в Edge): живее Silero, бесплатно, но нужен интернет.
    Многоязычные голоса (Ava, Emma, Vivienne…) тоже говорят по-русски — с лёгким акцентом, звучат моложе."""

    version = "edge"

    def synth(self, text, voice="ru-RU-SvetlanaNeural", pitch="medium", rate="medium"):
        import asyncio
        import io
        import av
        import edge_tts

        async def run():
            buf = bytearray()
            c = edge_tts.Communicate(normalize(text), voice or EDGE_VOICES[0],
                                     pitch=EDGE_PITCH.get(pitch, "+0Hz"), rate=EDGE_RATE.get(rate, "+0%"))
            async for ch in c.stream():
                if ch["type"] == "audio":
                    buf += ch["data"]
            return bytes(buf)
        data = asyncio.run(run())
        if not data:
            raise RuntimeError("пустой ответ сервиса Microsoft")
        with av.open(io.BytesIO(data)) as c:
            r = av.AudioResampler(format="flt", layout="mono", rate=SR)
            parts = [x.to_ndarray().reshape(-1) for fr in c.decode(audio=0) for x in r.resample(fr)]
        return np.concatenate(parts).astype(np.float32), SR


class EspeechTTS:
    """Эмоциональный голос (ESpeech, F5-TTS): отдельный процесс в своём окружении — см. espeech_worker.py.
    В ядре нет ни torch-CUDA, ни f5-tts: рабочий живёт в voice.espeech_python и держит модель в памяти.
    Процесс с пониженным приоритетом, чтобы не отнимать кадры у игры. Не поднялся или завис — Router говорит Silero."""

    RATE = {"x-slow": 0.8, "slow": 0.9, "medium": 1.0, "fast": 1.12, "x-fast": 1.25}
    EMO = {"relaxed": "neutral"}

    IDLE_UNLOAD = 600                  # с: столько тишины — и процесс выгружается, видеопамять свободна

    def __init__(self):
        self.p = None
        self.q = None
        self.lock = threading.Lock()
        self.dead_until = 0.0
        self.last_used = 0.0
        self.ready = False
        threading.Thread(target=self._idle_watch, daemon=True).start()

    def _idle_watch(self):
        import time
        while True:
            time.sleep(30)
            if self.p and time.monotonic() - self.last_used > self.IDLE_UNLOAD and not self.lock.locked():
                print("эмоциональный голос выгружен (тишина) — видеопамять свободна", flush=True)
                self.stop()

    @staticmethod
    def voice_id(want=None):
        """Тембр из настроек; папки нет (удалили или переименовали) — тембр по умолчанию."""
        from ..config import config, ROOT
        want = want or config.get("voice.espeech_voice") or "myagkaya"
        return want if (ROOT / "data" / "voices" / want).is_dir() else "myagkaya"

    @staticmethod
    def paths():
        from ..config import config, ROOT
        py = Path(config.get("voice.espeech_python") or ASCII_HOME / "ttslab" / "Scripts" / "python.exe")
        voice = ROOT / "data" / "voices" / EspeechTTS.voice_id()
        return py, voice

    @classmethod
    def available(cls):
        py, voice = cls.paths()
        return py.exists() and (voice / "texts.json").exists()

    def _read(self, timeout):
        import json, queue
        try:
            line = self.q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"голос не ответил за {timeout:.0f} с")
        if line is None:
            raise RuntimeError("процесс голоса завершился (см. data/espeech.log)")
        return json.loads(line)

    def _start(self):
        import json, os, queue, subprocess, time
        from ..config import config, ROOT
        if time.monotonic() < self.dead_until:
            raise RuntimeError("эмоциональный голос недавно упал — пока Silero")
        py, voice = self.paths()
        self.voice_now = voice.name
        from ..config import _cache
        env = {**os.environ, "HF_HOME": os.environ.get("HF_HOME", ""), "ESPEECH_NFE": str(config.get("voice.espeech_steps", 24)),
               "PYTHONIOENCODING": "utf-8"}
        if (_cache / "ffmpeg").exists():         # pydub внутри f5-tts ищет ffmpeg в PATH
            env["PATH"] = str(_cache / "ffmpeg") + os.pathsep + env.get("PATH", "")
        flags = 0x08000000 | 0x00004000          # без окна, BELOW_NORMAL_PRIORITY
        log = open(ROOT / "data" / "espeech.log", "a", encoding="utf-8")
        self.p = subprocess.Popen([str(py), str(Path(__file__).with_name("espeech_worker.py")), str(voice),
                                   config.get("voice.espeech_device", "cuda")],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                  encoding="utf-8", env=env, creationflags=flags)
        self.q = queue.Queue()

        def pump(out, q):
            for line in out:
                if line.startswith("{"):
                    q.put(line)
            q.put(None)
        threading.Thread(target=pump, args=(self.p.stdout, self.q), daemon=True).start()
        t0 = time.monotonic()
        try:
            msg = self._read(240)                # первая загрузка модели — до пары минут
        except Exception:
            self.stop()
            self.dead_until = time.monotonic() + 600
            raise
        print(f"эмоциональный голос готов за {time.monotonic() - t0:.0f} с: {msg.get('emotions')}", flush=True)

    def stop(self):
        if self.p:
            try:
                self.p.kill()
            except Exception:
                pass
        self.p = None
        self.ready = False

    def warm(self):
        """Поднять модель в фоне (~30 с). Фразы ждут прогрева (synth), чтобы голос в разговоре не менялся."""
        if self.ready or getattr(self, "warming", False) or not self.available():
            return
        self.warming = True

        def run():
            try:
                with self.lock:
                    if not self.p or self.p.poll() is not None:
                        self._start()
                    self.ready = True
            except Exception as e:
                print("эмоциональный голос не поднялся:", e, flush=True)
            finally:
                self.warming = False
        threading.Thread(target=run, daemon=True).start()

    def synth(self, text, emotion="neutral", rate="medium", voice=None, strength=None, steps=None):
        import json, tempfile, time
        from ..config import config
        self.last_used = time.monotonic()
        want = self.voice_id(voice)
        if self.ready and getattr(self, "voice_now", None) not in (None, want):   # выбрали другой тембр — перезапуск
            self.stop()
        if not self.ready or not self.p or self.p.poll() is not None:
            # прогрев (~30 с после запуска, простоя или игры) — ждём его, а не говорим эту фразу Silero:
            # иначе в одном разговоре звучали два разных голоса. Silero — только если голос так и не поднялся
            self.ready = False
            self.warm()
            t0 = time.monotonic()
            while not self.ready and self.warming and time.monotonic() - t0 < 60:
                time.sleep(0.25)
            if not self.ready:
                raise RuntimeError("эмоциональный голос не поднялся — эту фразу скажет Silero")
        with self.lock:
            out = Path(tempfile.gettempdir()) / f"shikigami_es_{threading.get_ident()}.wav"
            parts = []
            for chunk in _chunks(text, 300):     # F5 устаёт на длинном тексте: режем по фразам
                self.p.stdin.write(json.dumps({"text": chunk, "emotion": self.EMO.get(emotion, emotion), "out": str(out),
                                               "speed": self.RATE.get(rate, 1.0),
                                               "strength": strength or config.get("voice.espeech_strength", "off"),
                                               "steps": int(steps or config.get("voice.espeech_steps", 24))},
                                              ensure_ascii=False) + "\n")
                self.p.stdin.flush()
                try:
                    r = self._read(90)
                except Exception:
                    self.stop()
                    self.dead_until = time.monotonic() + 120
                    raise
                if "error" in r:
                    raise RuntimeError(r["error"])
                with wave.open(str(out), "rb") as w:
                    sr = w.getframerate()
                    a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
                parts += [a, np.zeros(int(sr * 0.12), np.float32)]
            out.unlink(missing_ok=True)
            return np.concatenate(parts), sr


class Router:
    """Выбирает движок по настройке voice.tts_engine: edge (онлайн), silero, piper. Нет сети — откат на офлайн."""

    def __init__(self):
        self.offline = SileroTTS() if any((MODELS / f"{v}.pt").exists() for v, _ in URLS) else PiperTTS()
        self.edge = EdgeTTS()
        self.espeech = EspeechTTS()
        self.version = self.offline.version

    def load(self):
        self.offline.load()
        from ..config import config
        if config.get("voice.tts_engine") == "espeech":
            # греем заранее, чтобы первая фраза не ждала полминуты, — но не вместе с Whisper и Silero: 3 ГБ разом
            # при запуске упирали память в подкачку, и подвисала вся система, вплоть до курсора
            threading.Timer(60, self.espeech.warm).start()

    def synth(self, text, speaker=None, pitch="medium", rate="medium", engine=None, edge_voice=None, emotion="neutral", espeech=None):
        from ..config import config
        from ..i18n import lang
        engine = engine or config.get("voice.tts_engine", "silero")
        voice = edge_voice or config.get("voice.edge_voice")
        if lang() != "ru" and engine == "edge" and (not voice or voice.startswith("ru-")):
            voice = "en-US-AvaMultilingualNeural"      # русская Светлана английский читает плохо
        # онлайн-голос Microsoft — только если его выбрали в настройках: иначе текст ответов уходил бы
        # третьей стороне без ведома человека
        if engine == "edge":
            try:
                return self.edge.synth(text, voice, pitch, rate)
            except Exception as e:
                print("голос Microsoft недоступен, говорю офлайн:", e, flush=True)
        if engine == "espeech" and lang() == "ru":
            try:
                return self.espeech.synth(text, emotion, rate, **(espeech or {}))
            except Exception as e:
                print("эмоциональный голос недоступен, говорю Silero:", e, flush=True)
        return self.offline.synth(text, speaker, pitch, rate)


def make_engine():
    """Офлайн — Silero, если модель скачана (иначе Piper); онлайн — нейроголоса Microsoft по настройке."""
    return Router()


def _chunks(text, limit=700):
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if len(cur) + len(sent) > limit and cur:
            parts.append(cur)
            cur = ""
        cur = (cur + " " + sent).strip()
    if cur:
        parts.append(cur)
    return [p for p in parts if re.search(r"\w", p)]


def save_wav(path, audio, sr=SR):
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
