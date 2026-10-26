"""Распознавание речи: faster-whisper. tiny — искать имя в начале фразы, small — точная команда."""
import re
import threading

import numpy as np

from ..config import config

_models = {}
_lock = threading.Lock()

# Whisper «озвучивает» тишину и шум фразами из обучающих субтитров, а с подсказкой — повторяет саму подсказку
# (в логе было «🎙 Ассистентка зовут {name}.» как команда). Такие куски выкидываем.
_JUNK = re.compile(r"субтитр|продолжение следует|спасибо за (просмотр|внимание)|подписывайтесь|ставьте лайк"
                   r"|dimatorzok|ассистентк\w* зовут|thanks? for watching|subscribe to|assistant's name is", re.I)


def _bare(s):
    return re.sub(r"[^\wё]", "", s.lower())


def hint():
    """Подсказка Whisper: имя ассистентки + словарь хозяина (город, ники, игры) — так реже «в низке» вместо «в Минске»."""
    from ..config import config as c
    from ..i18n import lang
    en = lang() != "ru"
    parts = [f"The assistant's name is {c['assistant_name']}." if en else f"Ассистентку зовут {c['assistant_name']}."]
    if c.get("city"):
        parts.append(f"City: {c['city']}." if en else f"Город — {c['city']}.")
    if c.get("voice.stt_hint"):
        parts.append(str(c.get("voice.stt_hint")))
    return " ".join(parts)


def model(size):
    with _lock:
        if size not in _models:
            from faster_whisper import WhisperModel
            dev = config.get("voice.stt_device", "auto")
            device = "cuda" if dev == "cuda" else "cpu"
            ctype = "int8_float16" if device == "cuda" else "int8"
            print(f"загружаю Whisper {size} ({device})…", flush=True)
            try:        # уже скачана — ни одного запроса в сеть (иначе каждый запуск проверял обновления на huggingface.co)
                _models[size] = WhisperModel(size, device=device, compute_type=ctype, cpu_threads=4, local_files_only=True)
            except Exception:
                print(f"модель Whisper {size} не найдена на диске — скачиваю один раз (~{ {'tiny': 75, 'base': 145, 'small': 480, 'medium': 1500}.get(size, 500)} МБ)…", flush=True)
                _models[size] = WhisperModel(size, device=device, compute_type=ctype, cpu_threads=4)
        return _models[size]


def transcribe(audio: np.ndarray, size=None, prompt=None, fast=False, with_conf=False, words=False):
    """Текст фразы. with_conf=True — ещё и уверенность: средний avg_logprob оставшихся кусков (0 — уверен, -1 — гадает).
    words=True — ещё и список (слово, вероятность): по нему видно, правда ли прозвучало имя."""
    size = size or config.get("voice.stt_model", "small")
    m = model(size)
    from ..i18n import lang
    segs, _ = m.transcribe(audio, language=lang(), beam_size=1 if fast else 4, vad_filter=False,
                           initial_prompt=prompt, condition_on_previous_text=False,
                           without_timestamps=not words, word_timestamps=words, temperature=0.0)
    texts, probs, ws = [], [], []
    for s in segs:
        t = s.text.strip()
        # эхо подсказки; короткие куски не трогаем — одно «{name}?» тоже входит в подсказку, а это зов
        if not t or _JUNK.search(t) or (prompt and len(_bare(t)) >= 10 and _bare(t) in _bare(prompt)):
            continue
        if s.no_speech_prob > 0.6 and s.avg_logprob < -0.7:  # тишина/шум, которую модель «услышала»
            continue
        texts.append(t)
        probs.append(s.avg_logprob)
        ws += [(w.word.strip(), w.probability) for w in (s.words or [])]
    text = " ".join(texts).strip()
    out = [text]
    if with_conf:
        out.append(sum(probs) / len(probs) if probs else -9.0)
    if words:
        out.append(ws)
    return tuple(out) if len(out) > 1 else text
