"""Мини-игры с {name}: крестики-нолики и камень-ножницы-бумага.

Она реагирует голосом и всем телом (жесты из захвата движения), помнит счёт между сессиями и подначивает
на реванш. Реплики — заготовками: мгновенно и без запросов к модели.
"""
import asyncio
import json
import random
import re
import time

from aiohttp import web

from core import bus, lines
from core.config import DATA
from core.tools import tool, I, S

TITLE = "Мини-игры"
DESCRIPTION = "Крестики-нолики и камень-ножницы-бумага против {name} — с реакциями и счётом."

SAVE = DATA / "games.json"
score = {"ttt": {"me": 0, "her": 0, "draw": 0}, "rps": {"me": 0, "her": 0, "draw": 0},
         "pokes": {"best": 0}, "hide": {"best": 0}, "emo": {"me": 0, "miss": 0}}

LINES = {
    "ttt_start": [("Крестики-нолики? Хмпф. Ходите первым, так и быть.", "neutral"),
                  ("Опять проиграть мне хотите? Ну давайте.", "happy")],
    "ttt_her_win": [("Три в ряд! Я же говорила.", "happy"),
                    ("Победа. Не расстраивайтесь, господин — со мной трудно.", "happy")],
    "ttt_me_win": [("Что?! Я отвлеклась! Реванш, немедленно!", "angry"),
                   ("…Ладно. Поздравляю. Только не зазнавайтесь.", "shy")],
    "ttt_draw": [("Ничья. Хмпф, ни вам, ни мне.", "neutral"), ("Ничья… Вы стали играть лучше. Немножко.", "shy")],
    "ttt_block": [("Ага, не выйдет.", "happy"), ("Я всё вижу.", "neutral")],
    "rps_win": [("Ха! Я так и знала, что вы это выкинете.", "happy"), ("Моя взяла. Лисы видят вас насквозь.", "happy")],
    "rps_lose": [("Мо-у! Нечестно, вы подглядывали!", "angry"), ("Случайность. Ещё раз.", "angry")],
    "rps_draw": [("Одинаково? Читаете мои мысли, да?", "surprised"), ("Ничья. Ещё!", "neutral")],
    "rps_match_win": [("Матч мой! Спасибо за игру, господин.", "happy")],
    "rps_match_lose": [("…Матч ваш. В следующий раз я вас уничтожу. В смысле — сыграем ещё?", "shy")],
}
LINES.update({
    "pokes_record": [("Сколько?! Вы что, тренировались? Новый рекорд, господин.", "surprised")],
    "pokes_low": [("И это всё? Я даже не почувствовала. Хмф.", "angry"), ("Слабовато. Ещё раз — если не боитесь.", "happy")],
    "pokes_mid": [("Ай-ай-ай! Хватит уже, пальцы сотрёте!", "shy")],
    "hide_good": [("Поймали… почти всё. Ладно, вы глазастый.", "shy")],
    "hide_bad": [("Ха! Лису так просто не поймать!", "happy")],
    "emo_ok": [("Угадали. Хмф, не так уж сложно было.", "shy"), ("Правильно! Вы меня хорошо знаете… слишком хорошо.", "happy")],
    "emo_miss": [("Мимо! Вы на меня вообще смотрите?", "angry"), ("Нет же! Внимательнее, господин.", "sad")],
})
EMO_NAMES = {"happy": "радость", "shy": "смущение", "angry": "злость", "sad": "грусть", "surprised": "удивление"}
emo_round = {"emo": None}
GESTURE = {"happy": "happy", "angry": "angry", "shy": "shy", "surprised": "nod", "neutral": "nod"}


def _load():
    try:
        score.update(json.loads(SAVE.read_text(encoding="utf-8")))
    except Exception:
        pass


def _save():
    SAVE.write_text(json.dumps(score, ensure_ascii=False), encoding="utf-8")


_last = {"t": 0.0}


async def react(kind, force=False, gesture=True):
    """Реплика голосом + жест всем телом; не чаще раза в 2,5 с, чтобы не тараторила."""
    if not force and time.time() - _last["t"] < 2.5:
        return
    _last["t"] = time.time()
    text, emo = lines.pick(f"games.{kind}")      # банк фраз: в характере человека
    from core import voice
    bus.emit("avatar.state", state="idle", emotion=emo)
    if gesture:
        bus.emit("avatar.gesture", name=GESTURE.get(emo, "nod"), seconds=2.5)
    asyncio.create_task(voice.speak(text, emotion=emo))
    return text


# ---------------- крестики-нолики ----------------
WIN = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]
ttt = {"board": [""] * 9, "level": 2, "over": None, "line": None, "turn": "X", "msg": ""}


def winner(b):
    for a, c, d in WIN:
        if b[a] and b[a] == b[c] == b[d]:
            return b[a], (a, c, d)
    return ("draw", None) if all(b) else (None, None)


def minimax(b, me, depth=0):
    w, _ = winner(b)
    if w == "O":
        return 10 - depth, None
    if w == "X":
        return depth - 10, None
    if w == "draw":
        return 0, None
    best = (-99, None) if me else (99, None)
    for i in range(9):
        if b[i]:
            continue
        b[i] = "O" if me else "X"
        sc, _ = minimax(b, not me, depth + 1)
        b[i] = ""
        if (me and sc > best[0]) or (not me and sc < best[0]):
            best = (sc, i)
    return best


def her_move(b, level):
    free = [i for i in range(9) if not b[i]]
    # уровень 1 — часто ошибается, 2 — иногда, 3 — не проигрывает никогда
    mistake = {1: 0.55, 2: 0.2, 3: 0.0}[level]
    if random.random() < mistake:
        return random.choice(free)
    return minimax(b, True)[1]


def ttt_state():
    return {**ttt, "score": score["ttt"]}


async def ttt_new(level=None, she_starts=False):
    ttt.update(board=[""] * 9, over=None, line=None, turn="X", msg="")
    if level:
        ttt["level"] = max(1, min(3, int(level)))
    await react("ttt_start", force=True)
    if she_starts:
        ttt["board"][random.choice([0, 2, 4, 6, 8])] = "O"
    return ttt_state()


async def ttt_move(cell):
    b = ttt["board"]
    if ttt["over"] or not 0 <= cell < 9 or b[cell]:
        return ttt_state()
    b[cell] = "X"
    w, line = winner(b)
    if not w:
        # «блокировала» ли она угрозу — для реплики
        threat = any(sum(1 for k in t if b[k] == "X") == 2 and any(not b[k] for k in t) for t in WIN)
        await asyncio.sleep(0.5 + random.random() * 0.5)          # «думает»
        b[her_move(b, ttt["level"])] = "O"
        w, line = winner(b)
        if not w and threat and random.random() < 0.5:
            await react("ttt_block")
    if w:
        ttt["over"], ttt["line"] = w, line
        key = {"O": "her", "X": "me", "draw": "draw"}[w]
        score["ttt"][key] += 1
        _save()
        await react({"O": "ttt_her_win", "X": "ttt_me_win", "draw": "ttt_draw"}[w], force=True)
    bus.emit("plugin.games", game="ttt", state=ttt_state())
    return ttt_state()


# ---------------- камень-ножницы-бумага ----------------
BEATS = {"rock": "scissors", "scissors": "paper", "paper": "rock"}
rps = {"round": 0, "me": 0, "her": 0, "last": None, "history": [], "over": None}


def her_choice():
    """Не чистый случай: лиса «читает» любимый жест хозяина, но не всегда — иначе было бы нечестно."""
    hist = rps["history"]
    if len(hist) >= 3 and random.random() < 0.35:
        fav = max(set(hist), key=hist.count)
        return next(k for k, v in BEATS.items() if v == fav)
    return random.choice(list(BEATS))


async def rps_play(choice):
    if choice not in BEATS:
        return {"error": "rock | paper | scissors"}
    if rps["over"]:
        rps.update(round=0, me=0, her=0, over=None)
    her = her_choice()
    rps["history"] = (rps["history"] + [choice])[-12:]
    rps["round"] += 1
    if BEATS[choice] == her:
        res = "me"
    elif BEATS[her] == choice:
        res = "her"
    else:
        res = "draw"
    if res != "draw":
        rps[res] += 1
    rps["last"] = {"me": choice, "her": her, "result": res}
    if rps["me"] == 3 or rps["her"] == 3:          # до трёх побед
        rps["over"] = "me" if rps["me"] == 3 else "her"
        score["rps"][rps["over"]] += 1
        _save()
        await react("rps_match_win" if rps["over"] == "her" else "rps_match_lose", force=True)
    else:
        await react({"me": "rps_lose", "her": "rps_win", "draw": "rps_draw"}[res])
    st = {**rps, "score": score["rps"]}
    bus.emit("plugin.games", game="rps", state=st)
    return st


async def desk_result(game, value):
    """Итог игры на рабочем столе (присылает окно аватара): рекорд и реакция."""
    value = int(value)
    if game == "pokes":
        rec = value > score["pokes"]["best"]
        if rec:
            score["pokes"]["best"] = value
        _save()
        await react("pokes_record" if rec and value > 15 else "pokes_low" if value < 20 else "pokes_mid", force=True)
    elif game == "reflex":                       # реакция: меньше — лучше; -1 — нажал раньше времени
        if value < 0:
            await react("reflex_early", force=True)
        else:
            if not score["reflex"]["best"] or value < score["reflex"]["best"]:
                score["reflex"]["best"] = value
            _save()
            await react("reflex_good" if value < 300 else "reflex_ok" if value < 500 else "reflex_slow", force=True)
    elif game == "hide":
        if value > score["hide"]["best"]:
            score["hide"]["best"] = value
        _save()
        await react("hide_good" if value >= 4 else "hide_bad", force=True)
    return score


# ================= новые игры: угадай число, реакция, города, викторина =================
LINES.update({
    "guess_start": [("Я загадала число от 1 до 100. Угадывайте. Хмф, не то чтобы мне было интересно.", "neutral")],
    "guess_hot": [("Горячо! Почти!", "surprised"), ("Ой… совсем рядом.", "shy")],
    "guess_warm": [("Теплее.", "neutral"), ("Уже лучше. Немножко.", "neutral")],
    "guess_cold": [("Холодно. Очень холодно.", "happy"), ("Мимо. Далеко мимо.", "happy")],
    "guess_win": [("Угадали! За {n} попыток. Ладно, вы молодец.", "shy"), ("Да, это {n}… То есть угадали за {n}. Не важно!", "shy")],
    "reflex_good": [("Сколько?! Быстрый какой…", "surprised")],
    "reflex_ok": [("Неплохо. Для человека.", "happy")],
    "reflex_slow": [("Медленно! Я бы уже три раза убежала.", "happy")],
    "reflex_early": [("Рано! Я ещё не сказала «сейчас»!", "angry")],
    "cities_bad": [("Нет такого города. Или буква не та. Хмф.", "angry")],
    "cities_used": [("Этот уже был! Не жульничайте.", "angry")],
    "cities_her_lose": [("…Не могу вспомнить. Ладно, вы победили. В этот раз.", "shy")],
    "quiz_right": [("Правильно! Откуда вы это знаете?", "surprised"), ("Верно. Хмф, умник.", "shy")],
    "quiz_wrong": [("Не-а. Подумайте лучше.", "happy"), ("Мимо! Я так и знала.", "happy")],
})
_ME, _HUMAN = "ТЫ выиграла, человек проиграл", "человек выиграл, ТЫ проиграла"
lines.register("games", LINES, {        # с точки зрения помощницы: «ты» — она, иначе модель путала, кто выиграл
    "ttt_start": "начинаете крестики-нолики, человек ходит первым", "ttt_her_win": f"крестики-нолики: {_ME}",
    "ttt_me_win": f"крестики-нолики: {_HUMAN}", "ttt_draw": "крестики-нолики: ничья",
    "ttt_block": "крестики-нолики: ты помешала человеку собрать три в ряд",
    "rps_win": f"камень-ножницы-бумага, раунд: {_ME}", "rps_lose": f"камень-ножницы-бумага, раунд: {_HUMAN}",
    "rps_draw": "камень-ножницы-бумага: показали одно и то же", "rps_match_win": f"камень-ножницы-бумага, весь матч: {_ME}",
    "rps_match_lose": f"камень-ножницы-бумага, весь матч: {_HUMAN}",
    "pokes_record": "игра «тычки»: человек натыкал тебя рекордное число раз за 10 секунд",
    "pokes_low": "игра «тычки»: человек натыкал мало", "pokes_mid": "игра «тычки»: человек натыкал много",
    "hide_good": "игра «поймай»: человек ловил тебя почти каждый раз", "hide_bad": "игра «поймай»: человек почти ни разу тебя не поймал",
    "emo_ok": "игра «угадай эмоцию»: человек угадал твою эмоцию", "emo_miss": "игра «угадай эмоцию»: человек не угадал",
    "guess_start": "ты загадала число от 1 до 100, человек угадывает", "guess_hot": "«угадай число»: человек почти угадал",
    "guess_warm": "«угадай число»: уже ближе", "guess_cold": "«угадай число»: далеко",
    "guess_win": "«угадай число»: человек угадал твоё число за {n} попыток", "reflex_good": "игра «реакция»: человек очень быстрый",
    "reflex_ok": "игра «реакция»: неплохая скорость", "reflex_slow": "игра «реакция»: человек медленный",
    "reflex_early": "игра «реакция»: человек кликнул раньше сигнала", "cities_bad": "игра «города»: такого города нет или не та буква",
    "cities_used": "игра «города»: этот город уже называли", "cities_her_lose": f"игра «города»: ты не вспомнила город — {_HUMAN}",
    "quiz_right": "викторина: человек ответил правильно", "quiz_wrong": "викторина: человек ошибся"})
score.setdefault("guess", {"best": 0, "games": 0})
score.setdefault("reflex", {"best": 0})
score.setdefault("cities", {"me": 0, "her": 0})
score.setdefault("quiz", {"right": 0, "wrong": 0})
guess = {"n": None, "tries": 0, "last": None}
cities = {"used": [], "letter": None}
quiz = {"q": None}


def _say(kind, **fmt):
    """Как react(), но с подстановкой; в играх — без паузы: хозяин ждёт ответа."""
    text, emo = lines.pick(f"games.{kind}")      # банк фраз: в характере человека
    text = text.format(**fmt)
    from core import voice
    bus.emit("avatar.state", state="idle", emotion=emo)
    bus.emit("avatar.gesture", name=GESTURE.get(emo, "nod"), seconds=2)
    asyncio.create_task(voice.speak(text, emotion=emo))
    return text


def _last_letter(word):
    w = word.lower().replace("ё", "е").strip(" .!?")
    for ch in reversed(w):
        if ch.isalpha() and ch not in "ьъый":
            return ch
    return w[-1:] or ""


async def _llm_json(prompt):
    from core import llm
    txt = await llm.simple(prompt + "\nОтвет — только JSON, без пояснений.", max_tokens=400)
    m = re.search(r"\{.*\}", txt or "", re.S)
    return json.loads(m.group(0)) if m else {}


def setup_more(api):
    @api.route("POST", "/guess/new")
    async def r_guess_new(_):
        guess.update(n=random.randint(1, 100), tries=0, last=None)
        return web.json_response({"text": _say("guess_start")})

    @api.route("POST", "/guess/try")
    async def r_guess_try(r):
        if guess["n"] is None:
            guess.update(n=random.randint(1, 100), tries=0, last=None)
        x = int((await r.json()).get("n", 0))
        guess["tries"] += 1
        d = abs(x - guess["n"])
        if d == 0:
            n = guess["tries"]
            score["guess"]["games"] += 1
            if not score["guess"]["best"] or n < score["guess"]["best"]:
                score["guess"]["best"] = n
            _save()
            guess["n"] = None
            return web.json_response({"win": True, "tries": n, "text": _say("guess_win", n=n), "score": score["guess"]})
        hint = "больше" if x < guess["n"] else "меньше"
        kind = "guess_hot" if d <= 5 else "guess_warm" if d <= 15 else "guess_cold"
        return web.json_response({"win": False, "hint": hint, "tries": guess["tries"], "text": f"{_say(kind)} Загаданное {hint}."})

    @api.route("POST", "/cities/move")
    async def r_cities(r):
        city = (await r.json()).get("city", "").strip()
        if not city:
            return web.json_response({"error": "назовите город"})
        if cities["letter"] and city.lower().replace("ё", "е")[0] != cities["letter"]:
            return web.json_response({"ok": False, "text": f"Нужно на «{cities['letter'].upper()}»! " + _say("cities_bad")})
        if city.lower() in cities["used"]:
            return web.json_response({"ok": False, "text": _say("cities_used")})
        need = _last_letter(city)
        used = ", ".join(cities["used"][-60:])
        try:
            res = await _llm_json(f"Игра в города (реальные города мира, по-русски). Игрок назвал «{city}». "
                                  f"1) Существует ли такой город? 2) Назови другой реальный город на букву «{need.upper()}», "
                                  f"не из списка: {used or 'пусто'}, и не «{city}». "
                                  '{"valid": true/false, "city": "Название", "fact": "одна короткая забавная деталь о твоём городе"}')
        except Exception:
            res = {}
        if res.get("valid") is False:
            return web.json_response({"ok": False, "text": _say("cities_bad")})
        cities["used"].append(city.lower())
        mine = (res.get("city") or "").strip()
        if not mine or mine.lower().replace("ё", "е")[0] != need or mine.lower() in cities["used"]:
            score["cities"]["me"] += 1
            _save()
            cities.update(used=[], letter=None)
            return web.json_response({"ok": True, "end": True, "text": _say("cities_her_lose"), "score": score["cities"]})
        cities["used"].append(mine.lower())
        cities["letter"] = _last_letter(mine)
        text = f"{mine}. {res.get('fact', '')} Вам на «{cities['letter'].upper()}»."
        from core import voice
        asyncio.create_task(voice.speak(text, emotion="happy"))
        return web.json_response({"ok": True, "city": mine, "letter": cities["letter"], "text": text, "used": len(cities["used"])})

    @api.route("POST", "/cities/new")
    async def r_cities_new(_):
        cities.update(used=[], letter=None)
        return web.json_response({"text": "Города! Начинайте, называйте любой."})

    @api.route("POST", "/quiz/new")
    async def r_quiz_new(r):
        topic = (await r.json()).get("topic") or "аниме, игры, наука, история, Майнкрафт — на выбор"
        try:
            q = await _llm_json(f"Придумай один интересный вопрос для викторины, тема: {topic}. Средняя сложность, факт должен быть "
                                "точным и общеизвестным. Четыре варианта, один верный. "
                                '{"q": "вопрос", "options": ["a", "b", "c", "d"], "answer": 0, "fact": "короткое пояснение"}')
        except Exception as e:
            return web.json_response({"error": f"не придумала вопрос: {e}"})
        if not q.get("options") or len(q["options"]) != 4:
            return web.json_response({"error": "не придумала вопрос, ещё раз?"})
        quiz["q"] = q
        from core import voice
        asyncio.create_task(voice.speak(q["q"], emotion="neutral"))
        return web.json_response({"q": q["q"], "options": q["options"]})

    @api.route("POST", "/quiz/answer")
    async def r_quiz_answer(r):
        q = quiz["q"]
        if not q:
            return web.json_response({"error": "сначала вопрос"})
        i = int((await r.json()).get("i", -1))
        ok = i == int(q.get("answer", -1))
        score["quiz"]["right" if ok else "wrong"] += 1
        _save()
        quiz["q"] = None
        _say("quiz_right" if ok else "quiz_wrong")
        return web.json_response({"ok": ok, "answer": q["answer"], "fact": q.get("fact", ""), "score": score["quiz"]})


def setup(api):
    _load()
    for k, v in (("guess", {"best": 0, "games": 0}), ("reflex", {"best": 0}), ("cities", {"me": 0, "her": 0}), ("quiz", {"right": 0, "wrong": 0})):
        score.setdefault(k, v)
    setup_more(api)

    @api.route("POST", "/desk/start")
    async def r_desk_start(r):
        kind = (await r.json()).get("kind")
        if kind == "emotion":
            emo_round["emo"] = random.choice(list(EMO_NAMES))
            bus.emit("avatar.game", kind="emotion", emo=emo_round["emo"])
        elif kind in ("pokes", "hide", "reflex"):
            bus.emit("avatar.game", kind=kind)
        return web.json_response({"ok": True, "score": score})

    @api.route("POST", "/desk/result")
    async def r_desk_result(r):
        b = await r.json()
        return web.json_response(await desk_result(b.get("game"), b.get("value", 0)))

    @api.route("POST", "/emo/guess")
    async def r_emo_guess(r):
        guess = (await r.json()).get("emo")
        right = emo_round["emo"]
        if not right:
            return web.json_response({"error": "сначала начните раунд"})
        ok = guess == right
        score["emo"]["me" if ok else "miss"] += 1
        emo_round["emo"] = None
        _save()
        await react("emo_ok" if ok else "emo_miss", force=True)
        return web.json_response({"ok": ok, "right": right, "right_name": EMO_NAMES[right], "score": score["emo"]})

    @api.route("GET", "/state")
    async def get_state(_):
        return web.json_response({"ttt": ttt_state(), "rps": {**rps, "score": score["rps"]},
                                  "desk": {k: score[k] for k in ("pokes", "hide", "emo", "reflex")},
                                  "more": {k: score[k] for k in ("guess", "cities", "quiz")}})

    @api.route("POST", "/ttt/new")
    async def r_ttt_new(r):
        b = await r.json()
        return web.json_response(await ttt_new(b.get("level"), b.get("she_starts")))

    @api.route("POST", "/ttt/move")
    async def r_ttt_move(r):
        return web.json_response(await ttt_move(int((await r.json())["cell"])))

    @api.route("POST", "/rps/play")
    async def r_rps(r):
        return web.json_response(await rps_play((await r.json())["choice"]))


@tool("play_game", "Открыть мини-игру с {name}: крестики-нолики (ttt), камень-ножницы-бумага (rps), угадай число (guess), "
      "города (cities), викторина (quiz), игры на рабочем столе — тычки, прятки, угадай эмоцию, реакция (desk). Счёт хранится.",
      {"game": S("ttt | rps | guess | cities | quiz | desk", enum=["ttt", "rps", "guess", "cities", "quiz", "desk"]),
       "level": I("Сложность крестиков 1-3")}, ["game"], group="plugin:games")
async def play_game(game, level=None):
    if game == "ttt":
        await ttt_new(level)
    bus.emit("ui.open_plugin", name="games", title=TITLE, tab=game)
    return {"opened": game, "score": score.get(game) or score.get({"desk": "pokes"}.get(game, ""), {})}
