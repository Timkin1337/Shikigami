"""Банк фраз: реплики без ИИ (тычки, «с возвращением», облачка аватара, игры) — в характере самого человека.

Исходные фразы написаны под встроенную кицунэ-цундере; у человека характер свой. Поэтому каждая ситуация —
ключ, описание и исходные фразы-примеры, а модель заранее пишет по N вариантов в текущем характере и на языке
программы (generate). Банк — data/lines.json; в момент реакции модель не ждём. Пока банка нет (нет «мозга»,
ещё пишется) — звучат исходные фразы.

Ситуации регистрируют ядро и плагины (register); облачка окна аватара — здесь же (AVATAR), окно берёт их
по ключу через /api/lines. Служебные надписи (счёт в играх, «Экрана 3 нет») сюда не входят — это не характер.
"""
import asyncio
import json
import random
from collections import Counter

from . import bus
from .config import DATA, config

BANK = DATA / "lines.json"
SITUATIONS = {}           # ключ → {"what": описание, "base": [(текст, эмоция)]}
_bank = {"hash": None, "lines": {}}
_job = {"task": None}


def register(prefix, table, what=None, emo="neutral"):
    """table: {вид: [текст | (текст, эмоция) | (текст, эмоция, ...)]}; what: {вид: описание ситуации}."""
    for kind, items in table.items():
        base = [(x, emo) if isinstance(x, str) else (x[0], x[1]) for x in items]
        SITUATIONS[f"{prefix}.{kind}"] = {"what": (what or {}).get(kind, ""), "base": base}


# облачка окна аватара (ui/avatar.js берёт их по ключу); {level} — название уровня дружбы
AVATAR = {
    "outfit": ("переоделась — человек сменил ей модель (наряд)", ["Ну как? Не то чтобы я для вас наряжалась!",
               "Переоделась. Не пяльтесь.", "Та-дам. …Что? Лисы так умеют."], "happy"),
    "startle": ("вздрогнула от внезапного грохота или хлопка рядом", ["Ай! Что это было?!", "Кья! Напугали!",
                "Ч-что грохнуло?!", "Эй! Предупреждать надо!"], "surprised"),
    "startle_asleep": ("дремала и проснулась от грохота", ["А?! Я не спала! Совсем не спала!", "Ч-что?! Кто тут?!"], "surprised"),
    "dizzy": ("её крутят мышкой по кругу, кружится голова", ["Голова кружится…", "Перестаньте крутить! Всё плывёт…",
              "Ещё круг — и я упаду прямо на вас."], "surprised"),
    "dozing": ("дремлет, но делает вид, что не спит", ["…Я не сплю. Проверяю обстановку.", "Я всё вижу, господин. Даже во сне.",
               "…Только попробуйте тронуть хвост."], "relaxed"),
    "late": ("поздняя ночь, человек всё ещё за компьютером", ["Господин, уже поздно…"], "sad"),
    "cam_hello": ("человек запустил игру, она теперь в маленьком окошке в углу экрана и смотрит", ["Я тут, в уголке. Удачи!",
                  "Смотрю. Не опозорьтесь.", "Болею за вас. Тихонько."], "happy"),
    "cam_busy": ("просят сделать что-то на рабочем столе, а она в окошке в углу, пока идёт игра", [
                 "Не сейчас — я в уголке, пока вы играете.", "Потом! Я тут смотрю, как вы играете."], "neutral"),
    "levelup": ("ваша дружба выросла до нового уровня {level}", ["Мы теперь… «{level}». Не то чтобы мне это было важно!",
                "«{level}»! Ну… я рада. Немножко.", "Уровень «{level}». Только не зазнавайтесь."], "happy"),
    "no_hiding_place": ("хотела спрятаться за окно, но все окна развёрнуты или их нет", ["Тут негде спрятаться…"], "sad"),
    "caught": ("в игре «поймай» её поймали кликом", ["Ай! Поймали!", "Нечестно!", "Эй!"], "surprised"),
    "mute_on": ("её попросили помолчать час", ["Хмф. Ладно, молчу."], "angry"),
    "mute_off": ("ей снова разрешили говорить", ["Ну наконец-то."], "happy"),
    "hang": ("её уронили мышкой, и она зацепилась руками за край окна", ["Ой! Держусь!", "Ай-ай, высоко!",
             "Не отпускайте… То есть я сама держусь!"], "surprised"),
    "climb": ("висела на краю окна и сама забралась наверх", ["Хмпф! Забралась.", "Вот так. Даже не запыхалась."], "happy"),
    "slip": ("висела на краю окна и сорвалась вниз", ["Ай! Руки соскользнули!", "Всё, не могу больше!"], "surprised"),
    "window_gone": ("сидела на окне, а его закрыли или свернули — падает", ["Эй! Куда окно дели?!", "Ай! Я же тут сидела!"], "surprised"),
    "window_covered": ("сидела на окне, а его заслонило другое окно — падает", ["Эй, меня заслонили!", "Ой, окно уехало назад!"], "surprised"),
    "ride_shake": ("окно, на котором она сидит, трясут мышкой туда-сюда", ["Не тряси меня!", "Эй! Я не коктейль!",
                   "Хватит трясти, укачает же!"], "angry"),
    "ride_fast": ("окно, на котором она сидит, резко дёрнули", ["Эй, не так резко!", "Ай! Держусь!", "Полегче на поворотах!"], "surprised"),
    "ride_stop": ("долго катилась, сидя на перетаскиваемом окне, и окно остановилось", ["Приехали?", "Ух. Хорошо прокатились.",
                  "Это что, такси?"], "happy"),
    "peek_found": ("пряталась за окном, и её нашли", ["Ой! Нашли…", "Эх, раскрыли.", "Я не пряталась! Просто стояла."], "surprised"),
    "screen_moved": ("переехала на другой монитор", ["Та-дам. Я тут.", "Переехала. Здесь светлее.", "Пуф! Соскучились?"], "happy"),
}
for _k, (_w, _l, _e) in AVATAR.items():
    SITUATIONS[f"avatar.{_k}"] = {"what": _w, "base": [(t, _e) for t in _l]}


def bank_hash():
    from .i18n import lang
    from .persona import persona_hash
    return f"{persona_hash()}:{lang()}"


def _load():
    if _bank["hash"] is None:
        try:
            _bank.update(json.loads(BANK.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _bank.update(hash="", lines={})
    return _bank


def ready():
    return _load().get("hash") == bank_hash()


def variants(key):
    b = _load()
    got = b["lines"].get(key) if b.get("hash") == bank_hash() else None
    return got or [t for t, _ in SITUATIONS.get(key, {}).get("base", [])]


def pick(key):
    """(текст, эмоция) для ситуации: вариант из банка в характере человека или исходная фраза."""
    s = SITUATIONS.get(key)
    if not s:
        return "", "neutral"
    emo = Counter(e for _, e in s["base"]).most_common(1)[0][0]
    return random.choice(variants(key)), emo


def for_window(prefix="avatar."):
    return {k[len(prefix):]: variants(k) for k in SITUATIONS if k.startswith(prefix)}


def _junk(t):
    """Мусор генерации: эмодзи, чужие алфавиты, а в русском режиме — латиница («Schlafzimmer», «при문», «…Borderline»)."""
    import re
    from .i18n import lang
    bare = re.sub(r"\{\w+\}", "", t)
    if re.search(r"[☀-➿🀀-🫿぀-ヿ㐀-鿿가-힯]", bare):
        return True
    return lang() == "ru" and re.search(r"[A-Za-z]", bare) is not None


def _placeholders(t):
    import re
    return set(re.findall(r"\{\w+\}", t))


async def generate(force=False):
    """Написать банк под текущий характер. Идёт в фоне; ход — событием lines.progress {done, total, text}."""
    from . import llm, persona
    h = bank_hash()
    if not force and ready():
        return 0
    n = max(5, min(40, int(config.get("lines.count", 20) or 20)))
    keys = list(SITUATIONS)
    out, step = {}, 4
    from .i18n import lang
    rules = (f"придумай {n} разных коротких реплик в своём характере: одна-две фразы, разные по словам и по настроению внутри "
             "своего характера. Реплики говоришь ТЫ сама: «она» в описании ситуации — это ты (она победила — значит, победила ты). "
             "О себе — только в женском роде («поставила», «не ожидала»). "
             "Примеры показывают смысл ситуации, а не тон — тон только твой. Без ремарок в звёздочках, без эмодзи, без своего "
             "имени" + (", только русские слова — никаких английских вставок" if lang() == "ru" else "") + ". Заполнители в фигурных "
             "скобках — только те, что указаны у ситуации, и ровно в таком виде; других не придумывай.")

    def sit(k):
        base = [t for t, _ in SITUATIONS[k]["base"]]
        ph = sorted(set().union(*(_placeholders(t) for t in base)))
        return {"ситуация": SITUATIONS[k]["what"] or k, "примеры": base, "заполнители": ph or "нет"}

    def keep(k, items):
        if not isinstance(items, list):                # объект вместо списка давал «реплики» «ситуация», «примеры»
            return
        base = [_placeholders(t) for t, _ in SITUATIONS[k]["base"]]
        allowed, required = set().union(*base), set.intersection(*base)   # {user} где-то можно, {level} — всегда нужен
        good = list(out.get(k, []))                    # переспрос добавляет к уже набранному, а не заменяет
        seen = {t.strip().lower() for t, _ in SITUATIONS[k]["base"]} | {g.lower() for g in good}   # копии примеров — не новые
        for x in items:
            if not (isinstance(x, str) and x.strip() and required <= _placeholders(x) <= allowed) or _junk(x):
                continue
            x = persona.clean(x)
            if x and x.lower() not in seen:
                seen.add(x.lower())
                good.append(x)
        if good:
            out[k] = good[:n]

    async def ask(text):
        # пишет та же качественная модель, что и ответы в характере (быстрая из автоподбора путала слова и род);
        # характер — без голосовых правил («без списков»): они спорили с просьбой вернуть JSON
        msgs = [{"role": "system", "content": persona.system_prompt(voice=False)}, {"role": "user", "content": text}]
        res, _ = await llm.chat(msgs, temperature=0.65, max_tokens=6000, model=config.get("llm.style_model") or None)
        raw = res["content"]
        starts = [i for i in (raw.find("{"), raw.find("[")) if i >= 0]
        if not starts:
            raise ValueError("в ответе нет JSON")
        return json.JSONDecoder().raw_decode(raw[min(starts):])[0]   # до конца первого значения: хвост текста не мешает

    for i in range(0, len(keys), step):
        part = keys[i:i + step]
        bus.emit("lines.progress", done=i, total=len(keys), text=f"пишу фразы под характер: {i} из {len(keys)} ситуаций…")
        try:
            got = await ask("Напиши реплики для себя на разные ситуации — потом они будут звучать сами, без тебя. Для каждой "
                            f"ситуации {rules} " + 'Верни ТОЛЬКО JSON-объект вида {"ключ": ["реплика", …]} с теми же ключами.\n'
                            + json.dumps({k: sit(k) for k in part}, ensure_ascii=False))
            for k in part:
                keep(k, got.get(k) if isinstance(got, dict) else None)
        except Exception as e:
            print("банк фраз: пачка не разобралась, по одной —", repr(e)[:120], flush=True)
        for k in part:                                    # не вышло пачкой или вышло мало — по одной ситуации, массивом
            if len(out.get(k, [])) >= max(3, n // 3):
                continue
            try:
                got = await ask(f"Ситуация: {json.dumps(sit(k), ensure_ascii=False)}\nДля неё {rules} "
                                'Верни ТОЛЬКО JSON-массив строк: ["реплика", …].')
                keep(k, got if isinstance(got, list) else None)
            except Exception as e:
                print("банк фраз:", k, "не вышла —", repr(e)[:120], flush=True)
        if h != bank_hash():                      # характер сменили посреди генерации — эта уже не нужна
            return 0
    if not out:
        bus.emit("lines.progress", done=0, total=len(keys), text="фразы под характер не написались — нужен «мозг» (ключ NVIDIA)",
                 error=True, finished=True)
        return 0
    _bank.update(hash=h, lines=out)
    BANK.write_text(json.dumps(_bank, ensure_ascii=False, indent=1), encoding="utf-8")
    bus.emit("lines.progress", done=len(keys), total=len(keys), text=f"фразы под характер готовы: {len(out)} ситуаций", finished=True)
    bus.emit("lines.changed")
    print(f"банк фраз: {len(out)} ситуаций по ~{n} вариантов", flush=True)
    return len(out)


def regenerate(force=False):
    """Запустить генерацию в фоне (одна за раз; новая заменяет идущую — например, характер поменяли снова)."""
    t = _job["task"]
    if t and not t.done():
        t.cancel()
    _job["task"] = asyncio.get_running_loop().create_task(generate(force))
    return _job["task"]


def busy():
    t = _job["task"]
    return bool(t and not t.done())
