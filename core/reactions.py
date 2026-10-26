"""Мелкие реакции аватара без ИИ — мгновенно и не тратят лимит запросов."""
import random
import time

from . import bus

LINES = {
    "poke_head": [("Эй! Не тыкайте в меня, господин.", "shy"), ("Что? Я работаю, между прочим.", "surprised"),
                  ("Не мешайте. Я… думаю.", "shy")],
    "poke_ears": [("Уши не трогать! …Они чувствительные.", "shy"), ("Мо-у! Только не за уши!", "angry"),
                  ("Господин!", "shy")],
    "poke_tail": [("Хвост — святое! Руки прочь.", "angry"), ("Хмпф. За хвост дёргают только невоспитанные.", "angry"),
                  ("Ещё раз — и останетесь без чая.", "angry")],
    "poke_body": [("Руки, господин. Руки.", "angry"), ("Хмпф. Если вам скучно — у меня тут пыль не вытерта.", "angry"),
                  ("Ара-ара, кто-то соскучился?", "happy")],
    "pat": [("…Ну ладно. Только недолго.", "shy"), ("Что вы делаете? …Продолжайте. В смысле — нет! В смысле…", "shy"),
            ("Это не я, это хвост.", "happy")],
    "pat_long": [("Если вы не остановитесь, я усну прямо тут…", "relaxed"),
                 ("Ладно. Признаю. Это приятно. Никому не говорите.", "shy")],
    "pickup": [("Эй-эй-эй! Поставьте меня!", "surprised"), ("Я вам не котёнок, чтобы меня таскать!", "angry"),
               ("Господин!", "surprised")],
    "drop": [("Хмпф. Аккуратнее надо.", "angry"), ("Ну и куда вы меня притащили?", "neutral")],
    "wake": [("М… я не спала! Просто глаза отдыхали.", "shy"), ("А? Я тут. Я всё слышала.", "surprised")],
    "sneeze": [("Это не я мило чихнула! Это у вас пыль.", "shy"), ("Не смотрите так. Кицунэ тоже чихают.", "shy"),
               ("Хмпф. Кто-то обо мне вспоминает… Наверняка вы.", "shy")],
}
_WHAT = {"poke_head": "её ткнули мышкой в голову", "poke_ears": "её ткнули в уши", "poke_tail": "её дёрнули за хвост",
         "poke_body": "её ткнули мышкой", "pat": "её гладят по голове", "pat_long": "её долго гладят по голове",
         "pickup": "её подняли мышкой и тащат", "drop": "её отпустили после перетаскивания",
         "wake": "она дремала, и её разбудили", "sneeze": "она чихнула", "offended": "её тыкали много раз подряд — обиделась"}
from . import lines as _lines
_lines.register("reactions", {**LINES, "offended": [("Всё! Я обиделась. На целую минуту.", "angry")]}, _WHAT)
_last = {"t": 0, "n": 0, "kind": ""}
AMBIENT = {"sneeze", "wake", "drop", "pickup", "pat_long"}


async def react(kind):
    if kind == "poke":
        kind = "poke_body"
    if kind not in LINES:
        return
    now = time.time()
    if now - _last["t"] < (1.0 if kind in ("pat_long", "drop", "sneeze") else 2.5):
        return
    pokes = kind.startswith("poke")
    _last["n"] = _last["n"] + 1 if pokes and now - _last["t"] < 20 else (1 if pokes else 0)
    _last["t"] = now
    if _last["n"] >= 4:
        kind = "offended"
        _last["n"] = 0
    text, emo = _lines.pick(f"reactions.{kind}")      # в характере человека (банк фраз) или исходная
    from . import voice
    bus.emit("avatar.state", state="idle", emotion=emo)
    if kind in AMBIENT:
        # не на прямое действие хозяина — говорит изредка и только когда молчит, иначе облачко
        from . import chatter
        if voice.busy() or now - _last.get("amb", 0) < 180 or random.random() < 0.5 or not chatter.allow():
            bus.emit("speech", text=text, url=None, emotion=emo)
            return
        _last["amb"] = now
    await voice.speak(text, emotion=emo, fresh_for=3)
