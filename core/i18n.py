"""Язык программы: ui.lang = auto | ru | en (auto — по языку Windows).

Заготовленные реплики, уведомления и подписи написаны по-русски; для другого языка они переводятся
по общему с окнами словарю ui/lang/<язык>.json (точное совпадение строки; «{}» в ключе — любая подстановка).
Ответы модели не переводятся — ей просто велено отвечать на нужном языке (persona.lang_rule).
"""
import json
import re
from pathlib import Path

from .config import ROOT, config

LANG_DIR = ROOT / "ui" / "lang"
_RU_FAMILY = {0x19, 0x22, 0x23, 0x3F}          # русский, украинский, белорусский, казахский — им удобнее по-русски
_cache = {"lang": None, "mtime": 0, "dict": {}, "patterns": []}


def system_lang():
    try:
        import ctypes
        return "ru" if ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF in _RU_FAMILY else "en"
    except Exception:
        return "ru"


def lang():
    want = (config.get("ui.lang") or "auto").lower()
    return system_lang() if want == "auto" else want


def _load(lg):
    path = LANG_DIR / f"{lg}.json"
    if not path.exists():
        return {}, []
    mtime = path.stat().st_mtime
    if _cache["lang"] == lg and _cache["mtime"] == mtime:
        return _cache["dict"], _cache["patterns"]
    exact, patterns = {}, []
    for k, v in json.loads(path.read_text(encoding="utf-8")).items():
        if not v:
            continue
        if "{}" in k:
            if len(re.sub(r"[^\w]|\d|_", "", k.replace("{}", ""))) < 4:   # «{} из {}» поймал бы что угодно
                continue
            rx = "^" + "(.+?)".join(re.escape(p) for p in k.split("{}")) + "$"
            patterns.append((re.compile(rx, re.S), v))
        else:
            exact[k] = v
    _cache.update(lang=lg, mtime=mtime, dict=exact, patterns=patterns)
    return exact, patterns


def tr(text):
    """Заготовленная строка → на язык программы (русский — как есть)."""
    lg = lang()
    if lg == "ru" or not text:
        return text
    exact, patterns = _load(lg)
    key = re.sub(r"\s+", " ", text).strip()
    out = exact.get(key)
    if out is None:
        for rx, tpl in patterns:
            m = rx.match(key)
            if m:
                vals = iter(m.groups())
                out = re.sub(r"\{\}", lambda _: next(vals), tpl)
                break
    return out if out else text
