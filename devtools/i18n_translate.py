r"""Перевести пустые строки словаря ui/lang/en.json через модель из настроек (NVIDIA или локальную).

Пачками по 60; перевод принимается, только если совпало число меток {} и нет кириллицы.
Запуск:  .venv\Scripts\python.exe devtools\i18n_translate.py
"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "ui" / "lang" / "en.json"
SYSTEM = ("You translate the UI and spoken lines of a desktop voice companion app from Russian to natural English. "
          "The character is an anime kitsune maid with a tsundere attitude; she calls the user «господин» → \"Master\". "
          "Keep emoji, punctuation style, «{}» placeholders (same count, same order), keyboard shortcuts and names "
          "(Shikigami, Telegram, Minecraft). Short, lively, natural spoken English. "
          "Answer ONLY with a JSON object mapping each given id to its translation.")


async def batch(llm, items):
    prompt = json.dumps({str(i): s for i, s in items}, ensure_ascii=False)
    res, _ = await llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                            temperature=0.2, max_tokens=6000)
    text = res["content"]
    text = text[text.find("{"): text.rfind("}") + 1]
    return json.loads(text)


async def main():
    from core import config as _cfg  # noqa: F401 — загружает .env и config.json
    from core import llm
    d = json.loads(OUT.read_text(encoding="utf-8"))
    todo = [(i, k) for i, (k, v) in enumerate(d.items()) if not v and "${" not in k and not k.startswith(";")]
    keys = list(d)
    print("переводить:", len(todo))
    for n in range(0, len(todo), 60):
        part = todo[n:n + 60]
        for attempt in range(3):
            try:
                got = await batch(llm, part)
                break
            except Exception as e:
                print("пачка", n, "ошибка:", str(e)[:120])
                got = {}
        ok = 0
        for i, src in part:
            t = (got.get(str(i)) or "").strip()
            if t and src.count("{}") == t.count("{}") and not re.search(r"[А-Яа-яЁё]", t):
                d[keys[i]] = t
                ok += 1
        print(f"пачка {n // 60 + 1}: {ok}/{len(part)}", flush=True)
        OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    print("без перевода:", sum(1 for v in d.values() if not v))


asyncio.run(main())
