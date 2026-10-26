"""Развлечения: омикудзи (лисье гадание дня), пасхалки на секретные фразы, наклейки-ачивки и «20 вопросов».

Всё, кроме «20 вопросов», — заготовками: мгновенно и без запросов к модели. Фразы ловятся до ИИ
(api.on_text) и только целиком: «мяу» — пасхалка, «найди мяу на ютубе» — обычная просьба.
"""
import asyncio
import datetime as dt
import json
import random
import re
import sys
import time

from aiohttp import web

from core import bus
from core.config import DATA, config
from core.tools import tool, S

TITLE = "Развлечения"
DESCRIPTION = "Омикудзи, секретные фразы, наклейки, «20 вопросов», угощения, мячик, прятки, будильник-вредина и Хэллоуин."

SAVE = DATA / "fun.json"
st = {"fortunes": {}, "eggs": {}, "stickers": {}, "said": 0, "days": [], "q20": {"won": 0, "lost": 0}}


def _load():
    try:
        st.update(json.loads(SAVE.read_text(encoding="utf-8")))
    except Exception:
        pass


def _save():
    SAVE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _norm(s):
    return " ".join(re.sub(r"[^\wё -]", " ", s.lower().replace("ё", "е")).replace("-", " ").split())


def say(text, emo="neutral", gesture=None, seconds=2.5):
    from core import voice
    bus.emit("avatar.state", state="idle", emotion=emo)
    if gesture:
        bus.emit("avatar.gesture", name=gesture, seconds=seconds)
    asyncio.create_task(voice.speak(text, emotion=emo))
    return text


# ---------------- наклейки ----------------
# id → (значок, название, за что; None — секрет, пока не получена)
STICKERS = {
    "first_fortune": ("🎴", "Первое гадание", "Вытянуть омикудзи"),
    "daikichi": ("🌟", "Большая удача", "Вытянуть «Дайкити»"),
    "daikyo": ("🍂", "Держитесь, господин", None),
    "fortune_7": ("📿", "Завсегдатай храма", "Гадать семь разных дней"),
    "egg_1": ("🥚", "Секретик", "Найти секретную фразу"),
    "egg_5": ("🔍", "Охотник за секретами", "Найти пять секретных фраз"),
    "egg_all": ("🦊", "Знаток лисьих секретов", "Найти все секретные фразы"),
    "q20_win": ("🔮", "Лиса читает мысли", "{name} угадала загаданное"),
    "q20_fast": ("⚡", "Видит насквозь", "{name} угадала за пять вопросов"),
    "q20_lose": ("🧠", "Перехитрил лису", "Загадать то, что она не угадает"),
    "voice_first": ("🎙", "Голос хозяина", "Первая просьба голосом"),
    "talker": ("💬", "Болтун", "Сто реплик {name}"),
    "night_owl": ("🦉", "Сова", None),
    "early_bird": ("🌅", "Жаворонок", None),
    "week": ("📅", "Неделя вместе", "Общаться семь разных дней"),
    "ttt_win": ("❌", "Крестик победы", "Обыграть в крестики-нолики"),
    "rps_10": ("✂️", "Мастер цу-э-фа", "10 побед в камень-ножницы-бумагу"),
    "quiz_10": ("📚", "Эрудит", "10 верных ответов в викторине"),
    "cities_win": ("🏙", "Географ", "Победить в «Города»"),
}


def award(sid, quiet=False):
    if sid in st["stickers"] or sid not in STICKERS:
        return False
    st["stickers"][sid] = time.time()
    _save()
    icon, title, _ = STICKERS[sid]
    bus.emit("notify", title="Новая наклейка", text=f"{icon} {title}")
    bus.emit("plugin.fun", what="sticker", id=sid)
    if not quiet:
        bus.emit("avatar.gesture", name="happy", seconds=2)
    return True


def _check_games():
    g = sys.modules.get("shikigami_plugin_games")
    sc = getattr(g, "score", None) or {}
    if sc.get("ttt", {}).get("me", 0) >= 1:
        award("ttt_win")
    if sc.get("rps", {}).get("me", 0) >= 10:
        award("rps_10")
    if sc.get("quiz", {}).get("right", 0) >= 10:
        award("quiz_10")
    if sc.get("cities", {}).get("me", 0) >= 1:
        award("cities_win")


def _seen(source):
    """Каждая реплика хозяина: счётчик, дни, время суток."""
    st["said"] += 1
    today = dt.date.today().isoformat()
    if today not in st["days"]:
        st["days"] = (st["days"] + [today])[-60:]
    h = dt.datetime.now().hour
    if source == "voice":
        award("voice_first")
    if 2 <= h < 5:
        award("night_owl")
    elif 5 <= h < 7:
        award("early_bird")
    if st["said"] >= 100:
        award("talker")
    if len(st["days"]) >= 7:
        award("week")
    if st["said"] % 10 == 0:
        _save()


# ---------------- омикудзи ----------------
# (ключ, иероглифы, название, вес) — как в храме: «большой беды» мало, но она бывает
RANKS = [("daikichi", "大吉", "Большая удача", 8), ("kichi", "吉", "Удача", 18), ("chukichi", "中吉", "Средняя удача", 20),
         ("shokichi", "小吉", "Малая удача", 20), ("suekichi", "末吉", "Удача впереди", 16), ("kyo", "凶", "Неудача", 12),
         ("daikyo", "大凶", "Большая неудача", 6)]
GOOD = {"daikichi", "kichi", "chukichi"}
AREAS = {
    "Дела": {True: ["всё, что отложено, сегодня сделается само собой", "смело берите самую трудную задачу первой",
                    "одна мелочь сегодня сэкономит вам целый вечер"],
             False: ["не начинайте большое — доделайте старое", "перепроверьте всё дважды, особенно сохранения",
                     "сегодня лучше делать медленно, чем переделывать"]},
    "Игры": {True: ["лут сегодня будет щедрым", "рандом на вашей стороне — рискуйте", "катка вечером будет легендарной"],
             False: ["крипер ближе, чем кажется", "не тратьте редкие ресурсы", "сохраняйтесь почаще"]},
    "Здоровье": {True: ["сил хватит на всё, но выпейте воды", "прогулка принесёт хорошую мысль"],
                 False: ["ляжьте спать до полуночи. Это не просьба", "встаньте и потянитесь — прямо сейчас"]},
    "Встречи": {True: ["кто-то давно хочет вам написать", "сегодня легко договориться о чём угодно"],
                False: ["в споре промолчите — выиграете больше", "не отвечайте на сообщения сгоряча"]},
}
ITEMS = ["онигири", "тёплые носки", "кружка чая", "синяя ручка", "наушники", "старая монетка", "мандарин", "плед",
         "лисий хвост (мой не трогать)", "печенье", "зарядка от телефона", "кактус"]
COLORS = ["рыжий", "алый", "золотой", "белый", "сиреневый", "мятный", "чёрный", "небесно-голубой"]
FORTUNE_REACT = {
    "daikichi": ("Дайкити! Большая удача! Господин, сегодня ваш день — даже я завидую.", "happy", "happy"),
    "kichi": ("Кити — удача. Хороший день, не испортите его.", "happy", "nod"),
    "chukichi": ("Тюкити, средняя удача. Твёрдая четвёрка, господин.", "neutral", "nod"),
    "shokichi": ("Сёкити — малая удача. Маленькие радости тоже считаются.", "neutral", "nod"),
    "suekichi": ("Суэкити — удача впереди. Потерпите, она уже в пути.", "neutral", "think"),
    "kyo": ("Кё… неудача. Завяжем бумажку на ветке — пусть беда останется в храме.", "sad", "sad"),
    "daikyo": ("Дайкё?! Большая неудача… Ничего! Сегодня я присмотрю за вами особенно внимательно.", "surprised", "surprised"),
}


def draw_fortune():
    today = dt.date.today().isoformat()
    if today in st["fortunes"]:
        return st["fortunes"][today], False
    rng = random.Random(f"{today}-{random.random()}")
    key, kanji, name, _ = rng.choices(RANKS, weights=[r[3] for r in RANKS])[0]
    good = key in GOOD or (key in ("shokichi", "suekichi") and rng.random() < .5)
    lines = {a: rng.choice(v[good if a != "Игры" or key not in ("kyo", "daikyo") else False]) for a, v in AREAS.items()}
    f = {"date": today, "key": key, "kanji": kanji, "name": name, "lines": lines,
         "item": rng.choice(ITEMS), "color": rng.choice(COLORS), "number": rng.randint(1, 99)}
    st["fortunes"][today] = f
    _save()
    award("first_fortune")
    if key in ("daikichi", "daikyo"):
        award(key)
    if len(st["fortunes"]) >= 7:
        award("fortune_7")
    return f, True


def fortune_speech(f, fresh):
    text, emo, gest = FORTUNE_REACT[f["key"]]
    if not fresh:
        return f"Сегодня уже тянули — {f['name'].lower()}. Второй раз боги не отвечают. Счастливый предмет: {f['item']}.", "neutral", "nod"
    return f"{text} Счастливый предмет — {f['item']}, цвет — {f['color']}.", emo, gest


# ---------------- пасхалки ----------------
# (id, шаблон целой фразы после нормализации, [(ответ, эмоция, жест)])
EGGS = [
    ("meow", r"(скажи )?мяу", [("Мяу… В смысле — я лиса! Лисы говорят «кон-кон»!", "shy", "shy")]),
    ("konkon", r"кон кон|кон кон кон", [("Кон-кон! Вы знаете лисий язык? Подозрительно…", "happy", "wave")]),
    ("woof", r"гав|гав гав", [("Гав?! Я вам не собака! Хотя лисы — псовые… Хмпф.", "angry", "angry")]),
    ("nya", r"ня|ня ня", [("Ня? Это кошачье. …Ня. Никому не говорите.", "shy", "shy")]),
    ("love", r"(я тебя люблю|ты меня любишь)", [("Ч-что вы такое говорите посреди дня?! …Я тоже вас очень ценю. Всё, работаем!", "shy", "shy"),
                                                 ("Глупый господин. Конечно. А теперь не смотрите на меня так.", "shy", "shy")]),
    ("cute", r"(ты )?(такая )?(милая|милашка|лапочка)|кто (тут )?самая милая", [("Я в курсе. Но слышать всё равно приятно.", "shy", "shy")]),
    ("barrel", r"сделай бочку", [("Бочку? Я лиса, а не самолёт! …Ладно, смотрите.", "happy", "dance")]),
    ("answer", r"(в чем|какой) смысл жизни|сорок два|42", [("Сорок два. И онигири. В основном онигири.", "neutral", "think")]),
    ("pet", r"(можно )?(тебя )?погладить|(дай|можно) (погладить )?ушки", [("…Только уши. И недолго. И никому ни слова.", "shy", "shy")]),
    ("five", r"дай пять", [("Пять! …Ой. Через экран не получилось. Считаем, что было.", "happy", "wave")]),
    ("magic", r"абракадабра|сим салабим|авада кедавра", [("Лисьи чары в ответ! …Вы всё ещё господин. Ни во что не превратились. Жаль.", "surprised", "surprised")]),
    ("robot", r"ты (робот|бот|нейросеть|программа)", [("Я кицунэ. Цифровая. Это совсем другое, понятно?!", "angry", "angry")]),
    ("sing", r"спой( песенку| что нибудь)?", [("Ла-ла-ла-а~ Всё, концерт окончен. Оплата — онигири.", "happy", "dance")]),
    ("bored", r"мне скучно", [("Скучно? Сыграем в «двадцать вопросов»: загадайте что-нибудь, а я угадаю. Скажите «давай в двадцать вопросов».", "happy", "guide")]),
    ("tail", r"(покажи|какой у тебя) хвост|можно хвост", [("Хвост — это личное! …Ну, он пушистый. Очень. Довольны?", "shy", "shy")]),
    ("sleep_fox", r"лисы спят|ты спишь", [("Лисы не спят, лисы бдят. С закрытыми глазами. Иногда.", "neutral", "nod")]),
    ("pancake", r"хочу есть|я голодный|я голодная", [("Сходите поешьте, господин. Голодный хозяин — грустная лиса.", "sad", "nod")]),
]


def find_egg(text):
    n = _norm(text)
    for eid, pat, lines in EGGS:
        if re.fullmatch(pat, n):
            return eid, random.choice(lines)
    return None, None


def egg_found(eid):
    first = eid not in st["eggs"]
    st["eggs"][eid] = st["eggs"].get(eid, 0) + 1
    _save()
    n = len(st["eggs"])
    award("egg_1")
    if n >= 5:
        award("egg_5")
    if n >= len(EGGS):
        award("egg_all")
    return first


# ---------------- 20 вопросов ----------------
q20 = {"on": False, "hist": [], "guess": None, "reveal": False}
ANSWERS = {"yes": r"да|ага|угу|конечно|верно|точно|да да", "no": r"нет|неа|не|нет нет|неверно|мимо",
           "maybe": r"скорее да|наверное|возможно|иногда|частично|отчасти|бывает",
           "rather_no": r"скорее нет|вряд ли|почти нет", "dunno": r"не знаю|хз|без понятия|не уверен|не уверена"}
ANS_RU = {"yes": "да", "no": "нет", "maybe": "скорее да", "rather_no": "скорее нет", "dunno": "не знаю"}
STOP = r"(я )?сдаюсь|хватит|стоп|закончим|не хочу играть|выход"
START = r"(давай |сыграем |играем )?(в )?(20|двадцать) вопросов|(давай )?(я )?загадаю|угадай (что|кого) я загадал[аи]?"


def _answer_kind(text):
    n = _norm(text)
    for k in ("rather_no", "maybe", "dunno", "yes", "no"):
        if re.fullmatch(ANSWERS[k], n):
            return k
    return None


async def q20_next():
    """Следующий ход {name}: вопрос или догадка."""
    from core import llm
    asked = "\n".join(f"{i + 1}. {q} — {ANS_RU[a]}" for i, (q, a) in enumerate(q20["hist"])) or "пока ничего"
    n = len(q20["hist"])
    wrong = [q[4:].rstrip("?") for q, a in q20["hist"] if q.startswith("Это ") and a == "no"]
    must = "Вопросы кончились — обязательно назови догадку." if n >= 19 else \
        "Если уверена больше чем на 80 % — назови догадку, иначе задай вопрос." if n >= 4 else "Задай вопрос."
    prompt = ("Игра «20 вопросов». Хозяин загадал что угодно: предмет, животное, персонажа, место, еду. Ты угадываешь, "
              "задавая вопросы с ответом да/нет. Вопросы короткие, по-русски, не повторяй уже заданные; каждый вопрос "
              f"должен отсекать примерно половину вариантов.\nУже спрошено:\n{asked}\n"
              + (f"Уже отвергнуто (не называй снова и не уточняй их разновидности): {', '.join(wrong)}.\n" if wrong else "")
              + f"{must}\n"
              '{"type": "question" или "guess", "text": "вопрос, либо название догадки одним-тремя словами"}'
              "\nОтвет — только JSON, без пояснений.")
    txt = await llm.simple(prompt, max_tokens=300)
    m = re.search(r"\{.*\}", txt or "", re.S)
    res = json.loads(m.group(0)) if m else {}
    t = (res.get("text") or "").strip().rstrip("?")
    if not t:
        raise ValueError("пустой ход")
    if res.get("type") == "guess":
        q20["guess"] = t
        line = random.choice(["Я знаю! Это — {}?", "Хм-м… это {}?", "Лисье чутьё подсказывает: {}. Угадала?"]).format(t)
        return say(line, "happy", "think")
    q20["guess"] = None
    q20["last_q"] = t + "?"
    return say(f"Вопрос {n + 1}. {t}?", "neutral", "think" if n % 3 == 0 else None)


async def q20_start():
    q20.update(on=True, hist=[], guess=None, reveal=False, t=time.time(), last_q=None)
    bus.emit("plugin.fun", what="q20")
    say("Загадывайте! Предмет, животное, персонажа — что угодно. Отвечайте «да», «нет» или «не знаю».", "happy", "guide")
    await asyncio.sleep(0.2)
    try:
        return await q20_next()
    except Exception as e:
        q20["on"] = False
        return say(f"Ой, мысли запутались ({e}). Давайте попозже.", "sad")


async def q20_answer(kind):
    if q20["guess"]:
        if kind == "yes":
            n = len(q20["hist"]) + 1
            st["q20"]["won"] += 1
            _save()
            q20["on"] = False
            award("q20_win")
            if n <= 5:
                award("q20_fast")
            bus.emit("plugin.fun", what="q20")
            return say(f"Ура! Угадала за {n} {_q(n)}! Лису не проведёшь.", "happy", "happy")
        q20["hist"].append((f"Это {q20['guess']}?", "no"))
        q20["guess"] = None
    else:
        q20["hist"].append((q20.get("last_q", "?"), kind))
    bus.emit("plugin.fun", what="q20")
    if len(q20["hist"]) >= 20:
        q20.update(on=False, reveal=True)
        return say("Двадцать вопросов… Сдаюсь! Что вы загадали?", "sad", "sad")
    try:
        return await q20_next()
    except Exception as e:
        return say(f"Ой, задумалась и потеряла мысль ({e}). Ответьте ещё раз?", "shy")


def _q(n):
    return "вопрос" if n % 10 == 1 and n % 100 != 11 else "вопроса" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "вопросов"


def q20_reveal(text):
    q20["reveal"] = False
    st["q20"]["lost"] += 1
    _save()
    award("q20_lose")
    bus.emit("plugin.fun", what="q20")
    return say(f"{text.strip().capitalize()}?! Хмпф… Запомню. В следующий раз не уйдёте.", "angry", "angry")


# ---------------- угощения, мячик, прятки, Хэллоуин (визуал — в окне аватара) ----------------
FED = {
    "🍙": [("Онигири! Мои любимые! Спасибо, господин!", "happy"), ("Ням. С тунцом? Вы меня балуете.", "happy")],
    "🍡": [("Данго! Три шарика счастья!", "happy"), ("М-м, данго… Вот теперь я вас прощаю. За всё.", "happy")],
    "🍵": [("Зелёный чай… Тепло. Спасибо.", "neutral"), ("Чай — правильный выбор. Вы растёте, господин.", "happy")],
    "🍪": [("Печенька. Ладно, одну можно.", "neutral"), ("Хрум. Неплохо!", "happy")],
    "🍬": [("Конфета! Сладкое — моя слабость. Никому не говорите.", "shy")],
    "🍎": [("Яблоко? Полезно… и вкусно. Спасибо.", "neutral")],
    "🥦": [("Брокколи?! Лисы такое не едят! Заберите!", "angry"), ("Опять брокколи? Это объявление войны.", "angry")],
}
THROWN = {
    "throw_ball_catch": [("Поймала! Ловите обратно!", "happy"), ("Хоп! Лисья реакция, господин.", "happy")],
    "throw_ball_dodge": [("Мимо! Ха-ха, медленно!", "happy"), ("Не попали. Тренируйтесь.", "happy")],
    "throw_ball_bonk": [("Ай! Прямо в лоб! За что?!", "angry"), ("Ой-ой… Звёздочки… Это было нечестно.", "sad")],
    "throw_pillow_catch": [("Подушечный бой? Получайте!", "happy")],
    "throw_pillow_bonk": [("Пф! Ах так?! Война подушками объявлена!", "angry"), ("Мф… Мягко, но обидно.", "shy")],
}
SEEK = {
    "found_fast": [("Уже нашли?! Вы подглядывали!", "surprised")],
    "found": [("Нашли… Ладно, хорошо искали.", "shy"), ("Попалась. В следующий раз спрячусь лучше.", "happy")],
    "lost": [("Ха! Не нашли! Я всё это время сидела тут!", "happy"), ("Лису так просто не найти. Моя победа!", "happy")],
}
TRICK = {
    "ask": [("Сладость или гадость? У вас сорок секунд, господин. Угощение — в меню по правому клику!", "happy")],
    "treat": [("Сладость! Так и быть — никаких проказ. Пока что.", "happy")],
    "broccoli": [("Брокколи — это не сладость! Это и есть гадость!", "angry")],
    "prank": [("Время вышло! Гадость! Летучие мыши — в атаку! А я обиделась и прячусь.", "angry")],
}
STICKERS.update({
    "feeder": ("🍙", "Кормилец", "Угостить {name} десять раз"),
    "broccoli": ("🥦", "Брокколи?!", None),
    "catcher": ("🏐", "Ловкие лапки", "{name} поймала пять мячиков"),
    "sniper": ("🎯", "Снайпер", None),
    "pillow_war": ("🪶", "Подушечная война", "Кинуть подушку"),
    "seeker": ("🔦", "Сыщик", "Найти {name} в прятках"),
    "seeker_fast": ("👀", "Орлиный глаз", "Найти {name} быстрее чем за 15 секунд"),
    "hider": ("🍃", "Мастер маскировки", None),
    "halloween": ("🎃", "Сладость!", "Угостить на Хэллоуин"),
    "pranked": ("🦇", "Гадость!", None),
    "alarm": ("⏰", "Подъём!", "Встать по будильнику-вредине"),
})


def _cnt(key, n=1):
    st.setdefault("counts", {})
    st["counts"][key] = st["counts"].get(key, 0) + n
    _save()
    return st["counts"][key]


def _line(lines):
    return random.choice(lines)


async def on_avatar_event(kind, msg):
    if kind == "fed":
        food = msg.get("food", "🍙")
        say(*_line(FED.get(food, FED["🍪"])))
        if _cnt("fed") >= 10:
            award("feeder")
        if food == "🥦":
            award("broccoli")
    elif kind.startswith("throw_"):
        say(*_line(THROWN.get(kind, THROWN["throw_ball_catch"])))
        if kind == "throw_ball_catch" and _cnt("caught") >= 5:
            award("catcher")
        if kind == "throw_ball_bonk" and _cnt("bonks") >= 5:
            award("sniper")
        if kind.startswith("throw_pillow"):
            award("pillow_war")
    elif kind == "seek_found":
        sec = int(msg.get("sec", 99))
        text, emo = _line(SEEK["found_fast" if sec < 15 else "found"])
        say(f"{text} {sec} секунд.", emo)
        award("seeker")
        if sec < 15:
            award("seeker_fast")
        best = st.setdefault("counts", {}).get("seek_best")
        if not best or sec < best:
            st["counts"]["seek_best"] = sec
            _save()
    elif kind == "seek_lost":
        say(*_line(SEEK["lost"]))
        award("hider")
    elif kind == "trick_ask":
        trick_ask()
    elif kind == "trick_treat":
        food = msg.get("food")
        say(*_line(TRICK["broccoli" if food == "🥦" else "treat"]))
        if food != "🥦":
            award("halloween")
    elif kind == "trick_prank":
        say(*_line(TRICK["prank"]))
        award("pranked")


def trick_ask():
    bus.emit("avatar.trick", seconds=40)
    return say(*_line(TRICK["ask"]))


def is_halloween():
    v = config.get("fun.halloween")
    if v in (True, False):
        return v
    d = dt.date.today()
    return (d.month == 10 and d.day >= 24) or (d.month == 11 and d.day == 1)


# ---------------- будильник-вредина ----------------
# Будит голосом и не отстаёт, пока не скажешь «встал» и не решишь пример. Один раз можно «ещё пять минут».
alarm = {"ring": None}      # ring: {"t0", "next", "stage": ring|math, "a", "b", "snoozed", "n"}
WAKE_LINES = [("Господин, подъём! Будильник!", "surprised", "call"), ("Вставайте! Солнце уже встало, а вы нет!", "angry", "wave"),
              ("Я не отстану. Подъём, господин!", "angry", "call"), ("Если не встанете, я начну петь. Громко!", "angry", "angry"),
              ("Господи-и-ин! Ну вставайте же!", "sad", "call")]
UP_RE = r"(я )?(встал|встала|проснулся|проснулась|не сплю|уже встал|уже встала)( уже)?"
SNOOZE_RE = r"(еще |ну еще )?(5|пять) минут(очек)?|(дай )?поспать еще|еще чуть чуть"
NUM_WORDS = {w: i for i, w in enumerate("ноль один два три четыре пять шесть семь восемь девять десять одиннадцать двенадцать "
                                        "тринадцать четырнадцать пятнадцать шестнадцать семнадцать восемнадцать девятнадцать".split())}
NUM_WORDS.update({"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60, "семьдесят": 70,
                  "восемьдесят": 80, "девяносто": 90, "сто": 100, "одна": 1, "две": 2})


def _number(text):
    m = re.search(r"\d+", text)
    if m:
        return int(m.group(0))
    total, found = 0, False
    for w in _norm(text).split():
        if w in NUM_WORDS:
            total += NUM_WORDS[w]
            found = True
    return total if found else None


def alarm_list():
    return sorted(st.setdefault("alarms", []), key=lambda a: a["at"])


def alarm_set(hhmm):
    m = re.fullmatch(r"(\d{1,2})[:.](\d{2})", hhmm.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    now = dt.datetime.now()
    at = now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    if at <= now:
        at += dt.timedelta(days=1)
    a = {"id": int(time.time() * 1000) % 10 ** 9, "at": at.timestamp(), "label": at.strftime("%d.%m %H:%M")}
    st.setdefault("alarms", []).append(a)
    _save()
    return a


def alarm_cancel(aid=None):
    st["alarms"] = [a for a in st.get("alarms", []) if aid is not None and a["id"] != aid]
    _save()


async def _phone(text):
    try:
        from core import telegram
        if telegram.bot:
            for uid in config.get("telegram.allowed_ids") or []:
                await telegram.bot.call("sendMessage", chat_id=uid, text=text)
    except Exception:
        pass


async def alarm_tick(now):
    due = [a for a in st.get("alarms", []) if a["at"] <= now]
    if due and not alarm["ring"]:
        st["alarms"] = [a for a in st["alarms"] if a not in due]
        _save()
        # ПК спал или был выключен: будить в обед за утренний будильник — хуже, чем промолчать
        if all(now - a["at"] > 30 * 60 for a in due):
            bus.emit("notify", title="⏰ Будильник", text=f"Пропущен будильник на {due[-1]['label']} — ПК спал.")
            return
        alarm["ring"] = {"t0": now, "next": now, "stage": "ring", "snoozed": False, "n": 0}
        bus.emit("plugin.fun", what="alarm")
        asyncio.create_task(_phone("⏰ Подъём, господин! Скажите мне «встал» — и решите пример."))
    r = alarm["ring"]
    if not r or now < r["next"]:
        return
    if now - r["t0"] > 20 * 60:                     # двадцать минут без толку — сдаётся
        alarm["ring"] = None
        bus.emit("plugin.fun", what="alarm")
        say("Ладно… спите. Но я обиделась.", "sad")
        return
    if r["stage"] == "ring":
        text, emo, gest = WAKE_LINES[min(r["n"], len(WAKE_LINES) - 1)]
        r["n"] += 1
        say(text, emo, gest, 3)
        bus.emit("notify", title="⏰ Будильник", text=text)
        r["next"] = now + 40
    else:                                            # ждёт ответ на пример — напоминает
        say(f"Ну? Сколько будет {r['a']} на {r['b']}?", "neutral")
        r["next"] = now + 45


def alarm_text(n):
    """Ответы, пока звенит будильник. None — не про будильник."""
    r = alarm["ring"]
    if not r:
        return None
    if r["stage"] == "math":
        x = _number(n)
        if x is None:
            return None
        if x == r["a"] * r["b"]:
            alarm["ring"] = None
            bus.emit("plugin.fun", what="alarm")
            award("alarm")
            return say("Правильно! Доброе утро, господин. Умываться — и завтракать!", "happy", "happy")
        return say(f"Неправильно! Ещё спите, что ли? {r['a']} на {r['b']}?", "angry", "angry")
    if re.fullmatch(SNOOZE_RE, n):
        if r["snoozed"]:
            r["next"] = time.time() + 5
            return say("Нет уж! Один раз уже было. Вставайте!", "angry", "angry")
        r.update(snoozed=True, next=time.time() + 300, n=0)
        return say("Ладно… пять минут. Ровно пять! Я засекла.", "neutral")
    if re.fullmatch(UP_RE, n):
        r.update(stage="math", a=random.randint(3, 9), b=random.randint(4, 9), next=time.time() + 45)
        bus.emit("plugin.fun", what="alarm")
        return say(f"Проверим. Сколько будет {r['a']} на {r['b']}?", "neutral", "think")
    return None


# ---------------- перехват фраз ----------------
FORTUNE_RE = r"(погадай|погадай мне|омикудзи|предсказание( на (день|сегодня))?|что меня ждет сегодня|вытяни (мне )?(предсказание|омикудзи)" \
             r"|какая (у меня )?удача сегодня)( пожалуйста)?"
STICKERS_RE = r"(покажи |мои )?(наклейки|ачивки|достижения)|покажи (мои )?(наклейки|ачивки|достижения)"
SEEK_RE = r"(давай |сыграем |поиграем )?(в )?прятки|спрячься( от меня)?|давай поиграем в прятки"
BALL_RE = r"(лови|держи) (мяч|мячик)|(кинуть|кидаю) (тебе )?мячик"
PILLOW_RE = r"(лови|держи) подушку|подушечный бой|бой подушками"
TRICK_RE = r"сладость или гадость|кошелек или жизнь"


async def on_text(text, source):
    _seen(source)
    n = _norm(text)
    said = alarm_text(n)
    if said:
        return said
    if q20["reveal"] and len(n.split()) <= 5:
        return q20_reveal(text)
    if q20["on"]:
        if re.fullmatch(STOP, n):
            q20["on"] = False
            bus.emit("plugin.fun", what="q20")
            return say("Сдались? Значит, я выиграла по очкам. Хмпф. Сыграем ещё потом.", "happy")
        kind = _answer_kind(text)
        if kind:
            return await q20_answer(kind)
        if time.time() - q20.get("t", 0) > 1800:   # забытую игру не держим вечно
            q20["on"] = False
    if re.fullmatch(START, n):
        bus.emit("ui.open_plugin", name="fun", title=TITLE, tab="q20")
        return await q20_start()
    if re.fullmatch(FORTUNE_RE, n):
        f, fresh = draw_fortune()
        bus.emit("ui.open_plugin", name="fun", title=TITLE, tab="omikuji")
        return say(*fortune_speech(f, fresh))
    if re.fullmatch(STICKERS_RE, n):
        bus.emit("ui.open_plugin", name="fun", title=TITLE, tab="stickers")
        k = len(st["stickers"])
        return say(f"Ваш альбом: {k} из {len(STICKERS)}. {'Неплохо!' if k > len(STICKERS) // 2 else 'Есть куда расти.'}", "happy", "guide")
    if re.fullmatch(SEEK_RE, n):
        bus.emit("avatar.seek")
        return "🙈 прячется…"
    if re.fullmatch(BALL_RE, n):
        bus.emit("avatar.throw", what="ball")
        return "🏐"
    if re.fullmatch(PILLOW_RE, n):
        bus.emit("avatar.throw", what="pillow")
        return "🪶"
    if re.fullmatch(TRICK_RE, n):
        return trick_ask()
    eid, line = find_egg(text)
    if eid:
        first = egg_found(eid)
        text, emo, gest = line
        tail = (" Секретик найден, кстати." if text.endswith("…") else " …Секретик найден, кстати.") if first else ""
        return say(text + tail, emo, gest, 3)
    return None


def state():
    today = dt.date.today().isoformat()
    r = alarm["ring"]
    return {"fortune": st["fortunes"].get(today), "fortune_days": len(st["fortunes"]),
            "stickers": [{"id": k, "icon": v[0], "title": v[1], "desc": v[2] or "Секрет", "got": st["stickers"].get(k)}
                         for k, v in STICKERS.items()],
            "eggs": {"found": len(st["eggs"]), "total": len(EGGS)},
            "q20": {"on": q20["on"], "reveal": q20["reveal"], "guess": q20["guess"], "n": len(q20["hist"]), "ask": q20.get("last_q") if q20["on"] else None,
                    "hist": [{"q": q, "a": ANS_RU[a]} for q, a in q20["hist"]], "score": st["q20"]},
            "alarms": [{"id": a["id"], "label": a["label"]} for a in alarm_list()],
            "ring": r and {"stage": r["stage"], "q": f"{r['a']} × {r['b']}" if r["stage"] == "math" else None},
            "halloween": is_halloween()}


def setup(api):
    _load()
    api.on_text(on_text)
    api.on_avatar_event(on_avatar_event)
    last = {"t": 0.0}

    async def tick(now):
        await alarm_tick(now)
        if now - last["t"] > 30:
            last["t"] = now
            _check_games()
            # 31 октября вечером — сама приходит за сладостями (раз в год, если ей можно говорить)
            d = dt.datetime.now()
            key = f"trick-{d.year}"
            if d.month == 10 and d.day == 31 and d.hour >= 18 and is_halloween() and not st.get(key):
                from core import chatter
                if chatter.allow():
                    st[key] = True
                    _save()
                    trick_ask()
    api.on_tick(tick)

    @api.route("GET", "/state")
    async def r_state(_):
        return web.json_response(state())

    @api.route("POST", "/omikuji")
    async def r_omikuji(_):
        f, fresh = draw_fortune()
        say(*fortune_speech(f, fresh))
        return web.json_response({"fortune": f, "fresh": fresh})

    @api.route("POST", "/q20/start")
    async def r_q20_start(_):
        await q20_start()
        return web.json_response(state()["q20"])

    @api.route("POST", "/q20/answer")
    async def r_q20_answer(r):
        kind = (await r.json()).get("a")
        if kind == "stop":
            q20["on"] = False
        elif q20["on"] and kind in ANS_RU:
            await q20_answer(kind)
        return web.json_response(state()["q20"])

    @api.route("POST", "/q20/reveal")
    async def r_q20_reveal(r):
        t = (await r.json()).get("text", "").strip()
        if q20["reveal"] and t:
            q20_reveal(t)
        return web.json_response(state()["q20"])

    @api.route("POST", "/toy")
    async def r_toy(r):
        b = await r.json()
        what = b.get("what")
        if what == "feed":
            bus.emit("avatar.feed", food=b.get("food", "🍙"))
        elif what in ("ball", "pillow"):
            bus.emit("avatar.throw", what=what)
        elif what == "seek":
            bus.emit("avatar.seek")
        elif what == "trick":
            trick_ask()
        return web.json_response({"ok": True})

    @api.route("POST", "/alarm")
    async def r_alarm(r):
        b = await r.json()
        if b.get("cancel") is not None:
            alarm_cancel(b["cancel"])
            return web.json_response(state())
        if b.get("answer") is not None and alarm["ring"]:
            alarm_text(str(b["answer"]))
            return web.json_response(state())
        a = alarm_set(b.get("time", ""))
        return web.json_response({**state(), "error": None if a else "время в формате ЧЧ:ММ"})


@tool("fun", "Развлечения с {name}: омикудзи — лисье гадание на день (omikuji), альбом наклеек (stickers), «20 вопросов» — хозяин "
      "загадывает, {name} угадывает (q20), прятки за окнами (seek), поймать мячик (ball), подушечный бой (pillow), "
      "угостить {name} (feed, food — эмодзи 🍙🍡🍵🍪🍬🍎🥦), «сладость или гадость» (trick).",
      {"what": S("Что", enum=["omikuji", "stickers", "q20", "seek", "ball", "pillow", "feed", "trick"]), "food": S("Эмодзи угощения для feed")},
      ["what"], group="plugin:fun")
async def fun(what, food=None):
    if what in ("omikuji", "stickers", "q20"):
        bus.emit("ui.open_plugin", name="fun", title=TITLE, tab=what)
    if what == "omikuji":
        f, fresh = draw_fortune()
        return {"fortune": f, "fresh": fresh, "note": "предсказание уже озвучено на панели — перескажи коротко"}
    if what == "q20":
        asyncio.create_task(q20_start())
        return {"started": True, "note": "игра началась, {name} уже задаёт вопросы сама — ничего не добавляй"}
    if what == "stickers":
        return {"stickers": f"{len(st['stickers'])} из {len(STICKERS)}"}
    if what == "feed":
        bus.emit("avatar.feed", food=food or "🍙")
    elif what in ("ball", "pillow"):
        bus.emit("avatar.throw", what=what)
    elif what == "seek":
        bus.emit("avatar.seek")
    elif what == "trick":
        trick_ask()
    return {"done": what, "note": "{name} уже отреагировала сама — ответь одной короткой фразой или промолчи"}


@tool("alarm", "Будильник-вредина: будит голосом и не отстаёт, пока хозяин не скажет «встал» и не решит пример. "
      "Обычные напоминания — не сюда.",
      {"time": S("Время ЧЧ:ММ (ближайшее такое)"), "cancel": {"type": "boolean", "description": "true — отменить все будильники"}},
      [], group="plugin:fun")
async def alarm_tool(time=None, cancel=False):
    if cancel:
        alarm_cancel()
        return {"cancelled": True}
    if not time:
        return {"alarms": [a["label"] for a in alarm_list()]}
    a = alarm_set(time)
    return {"set": a["label"]} if a else {"error": "время в формате ЧЧ:ММ"}
