"""Настройки Shikigami: config.json (правится из интерфейса) + .env (ключи)."""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
# Свои пути разработчика (уже скачанные модели в другом месте) — в data/dev-paths.json: {"cache": …, "ascii_home": …}.
# Файла нет в git и в сборке; у людей всё рядом с программой.
try:
    _dev = json.loads((DATA / "dev-paths.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    _dev = {}
# Модели голоса и распознавания — рядом с программой (data). Но Silero (torch), espeak и CTranslate2 не открывают
# файлы по пути с кириллицей, а программа ставится в профиль пользователя (C:/Users/Иван/…). Тогда модели —
# в C:/ProgramData/Shikigami: латиница, и писать туда может любой пользователь без прав администратора.
_models = ROOT / "data" if str(ROOT).isascii() else Path(os.environ.get("PROGRAMDATA") or "C:/ProgramData") / "Shikigami"
# Кэши моделей (Whisper, PyTorch)
_cache = Path(os.environ.get("SHIKIGAMI_CACHE") or _dev.get("cache") or _models / "cache")
for _k, _sub in (("HF_HOME", "hf"), ("TORCH_HOME", "torch"), ("XDG_CACHE_HOME", "cache")):
    os.environ.setdefault(_k, str(_cache / _sub))
# библиотеки моделей не шлют статистику и не подставляют чужие токены
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
os.environ.setdefault("DO_NOT_TRACK", "1")
# Silero, Piper и окружение голоса с эмоциями — только по латинскому пути
ASCII_HOME = Path(_dev.get("ascii_home") or _models / "voice-models")
DATA.mkdir(exist_ok=True)
CONFIG_PATH = ROOT / "config.json"
ENV_PATH = ROOT / ".env"

DEFAULTS = {
    "setup_done": False,           # мастер первого запуска пройден: имя, характер, модель, «мозг»
    "assistant_name": "",          # имя помощницы — задаёт человек в мастере первого запуска
    "user_birthday": "",           # «ДД.ММ» — поздравит утром с тортом; пусто — не знает
    "user_name": "",               # как зовут пользователя; пусто — «господин» в репликах, «пользователь» в промпте
    "address": "господин",
    "persona": "kitsune",          # kitsune | neutral | custom
    "persona_custom": "",          # свой системный промпт, если persona=custom
    "city": "",                    # для погоды; пусто — спросит
    "country": "",                 # праздники, цены, новости; пусто — не уточняет
    "search_engine": "google",     # для «найди в браузере»: google | yandex | bing | duckduckgo
    "currency_base": "USD",        # ваша валюта: в ней показываются курсы (BYN, UAH, RUB, KZT, EUR…)
    "currency_codes": "EUR,GBP,CNY",
    "server": {"host": "127.0.0.1", "port": 7853},
    "llm": {
        "primary": "nvidia",
        "fallback": "local",
        "history_messages": 24,
        "max_steps": 8,
        "style_model": "nvidia/nemotron-3-ultra-550b-a55b",   # кто пишет ответ в характере: качество важнее скорости
        "auto_model": True,     # сама подбирает самую быструю рабочую модель NVIDIA (раз в 6 ч и после медленных ответов)
        "providers": {
            "nvidia": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "model": "nvidia/nemotron-3-ultra-550b-a55b",
                "api_key_env": "NVIDIA_API_KEY",
                "rpm": 30,  # бесплатный лимит NVIDIA — 40 запросов в минуту
                "extra": {"chat_template_kwargs": {"thinking": False}},
            },
            "local": {
                "base_url": "http://127.0.0.1:11434/v1",   # Ollama; кнопка «Найти локальную модель» впишет нужное
                "model": "",
                "server_exe": "",     # свой llama-server.exe: {name} запустит его сама по первому запросу
                "model_path": "",
                "args": ["-ngl", "26", "-c", "8192", "--jinja"],
                "rpm": 0,
                "extra": {},
            },
        },
    },
    "voice": {
        "enabled": False,          # микрофон; включается сам, когда скачали распознавание речи («Модули»)
        "wake_word": "",             # пусто — её имя
        "speak_typed": False,      # озвучивать и ответы на набранный текст
        "stt_model": "small",       # tiny | base | small | medium
        "stt_hint": "",             # свои слова для распознавания: ники, игры, моды (через запятую)
        "wake_min_prob": 0.4,       # если имя расслышала только tiny — с какой уверенностью ей верить
        "stt_device": "auto",       # auto | cuda | cpu
        "tts_speaker": "xenia", "ptt_key": "", "ptt_mode": "hold", "tts_engine": "silero",
        "espeech_voice": "myagkaya", "espeech_strength": "off", "espeech_steps": 24,     # голос с эмоциями: тембр, сила эмоций, качество
        "edge_voice": "ru-RU-SvetlanaNeural",
        "tts_rate": "medium",
        "tts_pitch": "high",
        "input_device": None,
    },
    "vision": {"enabled": False, "provider": "nvidia", "model": ""},   # «посмотри на экран» — снимок уходит модели со зрением
    "avatar": {"enabled": True, "model": "", "scale": 1.0, "seasons": True, "wander": True,
               "pastimes": True, "shadows": True, "outline": 2, "wardrobe": True, "watch": True,
               # «вебка» поверх игры (behavior.game_style): угол br/bl/tr/tl, сторона квадрата в px, непрозрачность, рамка
               "cam": {"corner": "br", "size": 260, "opacity": 1.0, "frame": True}},
    "behavior": {"enabled": True, "voice": True, "chatty": "minimal", "mute_until": 0, "game_mode": True,
                 "game_style": "auto",      # во время игры: auto — «вебка» в углу, если экран один; cam — всегда; hide — прятаться
                 "startle": True,           # вздрагивает от хлопка или резкого громкого звука (микрофон, только уровень)
                 "friendship": True,        # уровень дружбы: растёт от разговоров, поглаживаний и игр, не падает
                 "respect_dnd": True},      # молчит вслух, пока Windows в «Не беспокоить» или идёт презентация
    # behavior: сама заговаривает — утро, «с возвращением», ночь, перерыв
    "minecraft": {"companion": True, "bot_name": "Shikigami", "react": True, "type_replies": False,
                  "game_dir": ""},          # своя папка игры, если лаунчер не стандартный; пусто — ищет сама
    "telegram": {"enabled": False, "token_env": "TELEGRAM_BOT_TOKEN", "allowed_ids": []},
    "mcp": {"servers": {}},
    "confirm_dangerous": True,
    # PowerShell, выключение ПК, запись/перенос/удаление файлов, завершение процессов.
    # По умолчанию их у ИИ нет совсем: модель их не видит, а команды с такими шагами не выполняются.
    "allow_dangerous": False,
    # недоделанное — только в сборке разработчика: комната «Домой» (бета)
    "features": {"room": False},
    # праздничная шляпа на Хэллоуин, Новый год и день рождения (меню по правому клику или «Настройки → Аватар»)
    "fun": {"hat": True},
    # банк фраз без ИИ (тычки, облачка, игры) под характер человека: сколько вариантов на каждую ситуацию
    "lines": {"count": 20},
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_env():
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class Config:
    def __init__(self):
        load_env()
        user = {}
        if CONFIG_PATH.exists():
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.data = _merge(DEFAULTS, user)
        if user and "setup_done" not in user:            # настройки до мастера: имя уже есть — мастер не нужен
            self.data["setup_done"] = bool(user.get("assistant_name"))
        if not CONFIG_PATH.exists():
            self._first_run_language()
            self.save()

    def _first_run_language(self):
        """Первый запуск на нерусской Windows — язык и обращение сразу по-английски (имя спросит мастер)."""
        try:
            import ctypes
            if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF in (0x19, 0x22, 0x23, 0x3F):
                return
        except Exception:
            return
        self.data.update(address="Master", user_name="")

    def __getitem__(self, k):
        return self.data[k]

    def get(self, path, default=None):
        cur = self.data
        for part in path.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def update(self, patch: dict):
        self.data = _merge(self.data, patch)
        self.save()

    def save(self):
        CONFIG_PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def secret(self, env_name):
        return os.environ.get(env_name or "", "")


config = Config()
