"""Модули: что из тяжёлого поставлено, сколько весит, что требует — и установка/удаление по кнопке.

В установщике только ядро: библиотеки голоса и распознавания (faster-whisper, torch, sherpa-onnx…) модуль
ставит сам в Python программы через uv (runtime/uv.exe в сборке), а потом качает модель.

Идея: в сборке только ядро и заглушка-модель. Голоса, распознавание речи покрупнее, эмоциональный голос
и плагины человек выбирает сам под свой ПК (вкладка «Модули» в настройках). Каждый модуль:
  id, group, name, size (МБ), needs (что нагружает), desc, license, source;
  status() -> "installed" | "missing" | "off"; install(log) / remove() — синхронные, их зовут в потоке.
Ход установки уходит событием module.progress {id, text, pct} — его показывает вкладка.
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import urllib.request
from pathlib import Path

from . import bus
from .config import ASCII_HOME, ROOT, _cache, config

HF_HUB = Path(os.environ.get("HF_HOME", _cache / "hf")) / "hub"
_busy = {}
_loop = None


def _emit(**data):
    """Ход установки — из рабочего потока в цикл ядра (bus не потокобезопасен)."""
    if _loop:
        bus.emit_threadsafe(_loop, "module.progress", **data)


def _download(url, dest, mid, label):
    """Скачать файл с ходом в процентах (через .part — оборванная загрузка не выдаёт себя за готовую)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "Shikigami"})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = last = 0
        while chunk := r.read(1 << 20):
            f.write(chunk)
            done += len(chunk)
            pct = int(done * 100 / total) if total else None
            if pct is not None and pct != last:
                last = pct
                _emit(id=mid, text=f"{label}: {done >> 20} из {total >> 20} МБ", pct=pct)
    tmp.replace(dest)


def _uv():
    """uv — ставит библиотеки и окружения. В сборке лежит рядом (runtime/uv.exe), у разработчика — в PATH."""
    for p in (ROOT / "runtime" / "uv.exe", shutil.which("uv"), Path.home() / ".local" / "bin" / "uv.exe"):
        if p and Path(p).exists():
            return str(p)
    raise RuntimeError("не найден uv (менеджер Python) — переустановите программу")


# библиотеки модулей: (пакеты для pip, модули для проверки импорта, свой индекс пакетов)
LIBS = {
    "stt": (["faster-whisper", "sounddevice"], ["faster_whisper", "sounddevice", "onnxruntime"], None),
    "silero": (["torch"], ["torch"], "https://download.pytorch.org/whl/cpu"),
    "piper": (["sherpa-onnx"], ["sherpa_onnx"], None),
}


def libs_ok(kind):
    return all(importlib.util.find_spec(m) is not None for m in LIBS[kind][1])


def _ensure_libs(kind, mid):
    if libs_ok(kind):
        return
    pkgs, _, index = LIBS[kind]
    _emit(id=mid, text=f"ставлю библиотеки: {', '.join(pkgs)}…", pct=None)
    args = [_uv(), "pip", "install", "--python", sys.executable, *pkgs] + (["--index-url", index] if index else [])
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, "UV_CACHE_DIR": str(_cache / "uv-cache")}, creationflags=0x08000000)
    if r.returncode:
        raise RuntimeError(f"библиотеки не встали: {(r.stderr or r.stdout)[-300:]}")
    importlib.invalidate_caches()


# ---------- распознавание речи (Whisper) ----------
def _whisper(size, mb, desc, needs):
    repo = HF_HUB / f"models--Systran--faster-whisper-{size}"

    def status():
        return "installed" if any(repo.glob("snapshots/*/model.bin")) and libs_ok("stt") else "missing"

    def install():
        _ensure_libs("stt", f"whisper-{size}")
        from faster_whisper.utils import download_model
        _emit(id=f"whisper-{size}", text=f"скачиваю Whisper {size} (~{mb} МБ)…", pct=None)
        download_model(size, cache_dir=str(HF_HUB))
        if not config.get("voice.enabled"):              # распознавание есть — микрофон включаем (с перезапуска)
            config.update({"voice": {"enabled": True}})

    def remove():
        shutil.rmtree(repo, ignore_errors=True)

    return dict(id=f"whisper-{size}", group="Распознавание речи", name=f"Whisper {size}", size=mb, needs=needs,
                desc=desc, license="MIT", source="huggingface.co/Systran", status=status, install=install, remove=remove)


# ---------- голоса ----------
def _silero():
    from .voice.tts import MODELS
    f = MODELS / "v5_6_ru.pt"
    return dict(id="voice-silero", group="Голос", name="Silero (русский)", size=148, needs="процессор, чуть-чуть",
                desc="Основной офлайн-голос: быстрый, внятный, без эмоций. Голоса xenia, baya, kseniya.",
                license="CC BY-NC-SA 4.0 — только некоммерческое", source="models.silero.ai",
                status=lambda: "installed" if f.exists() and libs_ok("silero") else "missing",
                install=lambda: (_ensure_libs("silero", "voice-silero"),
                                 _download("https://models.silero.ai/models/tts/ru/v5_6_ru.pt", f, "voice-silero", "Silero")),
                remove=lambda: f.unlink(missing_ok=True))


def _piper():
    from .voice.tts import PIPER
    url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-ru_RU-irina-medium.tar.bz2"

    def install():
        _ensure_libs("piper", "voice-piper")
        arc = PIPER.parent / "irina.tar.bz2"
        _download(url, arc, "voice-piper", "Piper «Ирина»")
        _emit(id="voice-piper", text="распаковываю…", pct=None)
        with tarfile.open(arc) as t:
            t.extractall(PIPER.parent, filter="data")
        arc.unlink(missing_ok=True)

    return dict(id="voice-piper", group="Голос", name="Piper «Ирина» (запасной)", size=64, needs="процессор, чуть-чуть",
                desc="Запасной офлайн-голос, если Silero не скачался. Звучит взрослее и суше.",
                license="MIT (sherpa-onnx), голос — piper-voices", source="github.com/k2-fsa/sherpa-onnx",
                status=lambda: "installed" if (PIPER / "tokens.txt").exists() and libs_ok("piper") else "missing",
                install=install, remove=lambda: shutil.rmtree(PIPER, ignore_errors=True))


def _espeech():
    """Эмоциональный голос: своё окружение Python (torch + f5-tts + ruaccent) и модель ESpeech 2,7 ГБ."""
    env = _cache / "tts-lab"
    link = ASCII_HOME / "ttslab"           # латинский путь: ударения не открывают файлы по кириллице
    repo = HF_HUB / "models--ESpeech--ESpeech-TTS-1_RL-V2"

    def status():
        from .voice.tts import EspeechTTS
        ok = (link / "Scripts" / "python.exe").exists() and any(repo.glob("snapshots/*/espeech_tts_rlv2.pt"))
        return "installed" if ok and EspeechTTS.available() else "missing"

    def run(args, label):
        _emit(id="voice-espeech", text=label, pct=None)
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env={**os.environ, "UV_CACHE_DIR": str(_cache / "uv-cache")}, creationflags=0x08000000)
        if r.returncode:
            raise RuntimeError(f"{label}: {(r.stderr or r.stdout)[-400:]}")

    def install():
        uv = _uv()
        py = env / "Scripts" / "python.exe"
        if not py.exists():
            run([uv, "venv", "--python", "3.12", str(env)], "создаю окружение Python 3.12…")
        gpu = shutil.which("nvidia-smi") and config.get("voice.espeech_device", "cuda") == "cuda"
        index = "https://download.pytorch.org/whl/cu128" if gpu else "https://download.pytorch.org/whl/cpu"
        run([uv, "pip", "install", "--python", str(py), "torch", "torchaudio", "--index-url", index],
            f"ставлю torch ({'видеокарта' if gpu else 'процессор'}) — это самая большая часть…")
        run([uv, "pip", "install", "--python", str(py), "f5-tts", "ruaccent", "soundfile"], "ставлю f5-tts и ударения…")
        if not link.exists():
            link.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(env)], capture_output=True, creationflags=0x08000000)
        run([str(link / "Scripts" / "python.exe"), "-c",
             "from huggingface_hub import hf_hub_download as d; r='ESpeech/ESpeech-TTS-1_RL-V2';"
             "d(r,'espeech_tts_rlv2.pt'); d(r,'vocab.txt'); d('charactr/vocos-mel-24khz','config.yaml');"
             "d('charactr/vocos-mel-24khz','pytorch_model.bin')"], "скачиваю модель ESpeech (2,7 ГБ)…")

    def remove():
        if link.exists():
            os.rmdir(link)                               # junction: удаляется сама ссылка, не содержимое
        shutil.rmtree(env, ignore_errors=True)
        shutil.rmtree(repo, ignore_errors=True)

    return dict(id="voice-espeech", group="Голос", name="Голос с эмоциями (ESpeech)", size=7500,
                needs="видеокарта NVIDIA, ~3 ГБ видеопамяти: фраза за ~2 с (на процессоре — до минуты, не годится). "
                      "Выгружается сама после 10 мин тишины и на время игр",
                desc="Живой голос {name}: радуется, грустит, ворчит, смущается — по настроению ответа. Только русский. "
                     "Ставит своё окружение Python и модель ~2,7 ГБ.",
                license="Apache-2.0 (ESpeech, F5-TTS)", source="huggingface.co/ESpeech",
                status=status, install=install, remove=remove)


def _designer():
    """Создание тембров по описанию: пакет qwen-tts в окружении голоса + модель VoiceDesign 4,3 ГБ."""
    env = _cache / "tts-lab"
    repo = HF_HUB / "models--Qwen--Qwen3-TTS-12Hz-1.7B-VoiceDesign"

    def status():
        ok = (env / "Lib" / "site-packages" / "qwen_tts").exists() and any(repo.glob("snapshots/*/*.safetensors"))
        return "installed" if ok else "missing"

    def install():
        if not (env / "Scripts" / "python.exe").exists():
            raise RuntimeError("сначала поставьте «Голос с эмоциями» — тембры создаются в его окружении")
        uv = _uv()
        _emit(id="voice-designer", text="ставлю qwen-tts…", pct=None)
        r = subprocess.run([uv, "pip", "install", "--python", str(env / "Scripts" / "python.exe"), "qwen-tts"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", env={**os.environ, "UV_CACHE_DIR": str(_cache / "uv-cache")},
                           creationflags=0x08000000)
        if r.returncode:
            raise RuntimeError(r.stderr[-400:])
        _emit(id="voice-designer", text="скачиваю модель VoiceDesign (4,3 ГБ)…", pct=None)
        r = subprocess.run([str(env / "Scripts" / "python.exe"), "-c",
                            "from huggingface_hub import snapshot_download as s; s('Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign')"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=0x08000000)
        if r.returncode:
            raise RuntimeError(r.stderr[-400:])

    return dict(id="voice-designer", group="Голос", name="Создание тембров по описанию", size=4400,
                needs="видеокарта NVIDIA, ~5 ГБ видеопамяти; тембр рисуется 1–3 минуты, потом не нужна",
                desc="Пишете словами, каким должен быть голос («низкий, спокойный, чуть хрипловатый») — получается новый тембр "
                     "со всеми эмоциями для голоса с эмоциями. Нужен модуль «Голос с эмоциями».",
                license="Apache-2.0 (Qwen3-TTS)", source="huggingface.co/Qwen",
                status=status, install=install, remove=lambda: shutil.rmtree(repo, ignore_errors=True))


def _vision():
    """Зрение — не скачивание, а согласие: снимок экрана уходит в облако модели со зрением, только по просьбе."""
    def toggle(on):
        config.update({"vision": {"enabled": on}})
        from . import bus as _b
        _b.emit("config.changed")
    return dict(id="vision", group="Зрение", name="Смотреть на экран", size=0, toggle=True,
                needs="облако NVIDIA (или своя локальная модель со зрением) — компьютер не грузит",
                desc="«Посмотри, что у меня на экране», «что в игре происходит?» — {name} делает снимок окна и рассказывает. "
                     "Снимок уходит в NVIDIA только в момент просьбы и нигде не сохраняется.",
                license="модели NVIDIA (Nemotron VL, Llama Vision)", source="integrate.api.nvidia.com",
                status=lambda: "installed" if config.get("vision.enabled") else "off",
                install=lambda: toggle(True), remove=lambda: toggle(False))


# ---------- плагины ----------
PLUGIN_INFO = {
    "chess": ("Шахматы", "Партия с {name} на доске, четыре уровня и комментарии к ходам."),
    "games": ("Мини-игры", "Угадай число, реакция, города, викторина — в чате и на рабочем столе."),
    "fun": ("Развлечения", "Омикудзи, пасхалки, наклейки, угощения, прятки за окнами, праздники."),
    "focus": ("Фокус", "Помидорный таймер: {name} не отвлекает, пока вы работаете."),
    "minecraft": ("Майнкрафт", "Видит вашу игру по журналу и (с модом) глазами персонажа; может зайти вторым игроком."),
}


def _plugin(name):
    d = ROOT / "plugins" / name
    title, desc = PLUGIN_INFO.get(name, (name, ""))

    def status():
        return "off" if (d / "disabled").exists() else "installed"

    return dict(id=f"plugin-{name}", group="Плагины", name=title, size=0, needs="почти ничего", desc=desc,
                license="MIT", source="в составе программы", status=status, toggle=True,
                install=lambda: (d / "disabled").unlink(missing_ok=True),
                remove=lambda: (d / "disabled").write_text("выключен во вкладке «Модули»\n", encoding="utf-8"))


def all_modules():
    mods = [
        _whisper("tiny", 75, "Слышит имя «{name}» — без него голосовое управление не работает.", "процессор, чуть-чуть"),
        _whisper("small", 480, "Распознаёт команды. Хороший выбор для большинства ПК.", "процессор ~2 с на фразу"),
        _whisper("medium", 1500, "Точнее small на шумном микрофоне и редких словах, но втрое медленнее.",
                 "процессор ~6 с на фразу или видеокарта"),
        _silero(), _piper(), _espeech(), _designer(), _vision(),
    ]
    for d in sorted((ROOT / "plugins").iterdir()):
        if d.is_dir() and not d.name.startswith((".", "_")) and (d / "plugin.py").exists():
            mods.append(_plugin(d.name))
    return mods


def listing():
    out = []
    for m in all_modules():
        try:
            st = m["status"]()
        except Exception as e:
            st = "missing"
            print("модуль", m["id"], "— не понять состояние:", e, flush=True)
        out.append({k: v for k, v in m.items() if not callable(v)} | {"status": "busy" if m["id"] in _busy else st})
    return out


def start(mid, action):
    """Установить/удалить в фоне. Итог — событие module.progress с done=True (и error, если не вышло)."""
    m = next((x for x in all_modules() if x["id"] == mid), None)
    if not m or mid in _busy or action not in ("install", "remove"):
        return False

    def work():
        try:
            m[action]()
            _emit(id=mid, text="готово" if action == "install" else "удалено", pct=100, done=True,
                     restart=mid.startswith("plugin-"))
        except Exception as e:
            print(f"модуль {mid}: {action} не удался:", e, flush=True)
            _emit(id=mid, text=f"не вышло: {e}"[:300], pct=None, done=True, error=True)
        finally:
            _busy.pop(mid, None)

    global _loop
    import asyncio
    _loop = asyncio.get_running_loop()
    _busy[mid] = action
    threading.Thread(target=work, daemon=True).start()
    return True
