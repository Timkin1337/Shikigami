"""Глаза {name}: что происходит в игре у хозяина.

Клиентский мод «Глаза» (games/minecraft-eyes) раз в полсекунды присылает состояние игрока и мира. Здесь оно
превращается в понятную сводку для ИИ («где, что в руках, кто рядом, что делал последние минуты») и в поводы
отреагировать: срочные предупреждения — заготовками и сразу (крипер шипит, тонет, горит), спокойные события
(новый биом, алмазы, Незер) — репликой от ИИ, но редко, чтобы не болтала под руку.
"""
import random
import re
import time
from collections import deque

DIMS = {"minecraft:overworld": "Верхний мир", "minecraft:the_nether": "Незер", "minecraft:the_end": "Энд"}
FOOD = {"bread", "apple", "golden_apple", "enchanted_golden_apple", "carrot", "golden_carrot", "baked_potato", "potato",
        "beetroot", "melon_slice", "sweet_berries", "glow_berries", "cookie", "pumpkin_pie", "cake", "mushroom_stew",
        "rabbit_stew", "beetroot_soup", "suspicious_stew", "honey_bottle", "dried_kelp", "cooked_beef", "cooked_porkchop",
        "cooked_chicken", "cooked_mutton", "cooked_rabbit", "cooked_cod", "cooked_salmon", "beef", "porkchop",
        "chicken", "mutton", "rabbit", "cod", "salmon", "tropical_fish", "chorus_fruit"}
VALUABLE = {"diamond", "emerald", "ancient_debris", "netherite_scrap", "netherite_ingot", "diamond_ore",
            "deepslate_diamond_ore", "emerald_ore", "deepslate_emerald_ore", "enchanted_golden_apple", "totem_of_undying",
            "elytra", "nether_star", "heart_of_the_sea", "trident", "enchanted_book"}

# срочное — заготовками, без ожидания ответа модели
LINES = {
    "creeper_hiss": ["Крипер шипит! Назад!", "Отбегите, сейчас рванёт!", "Крипер! Бегите!"],
    "creeper_behind": ["Сзади крипер!", "Обернитесь, крипер!"],
    "mob_behind": ["Сзади {name}!", "{name} за спиной!"],
    "hp_low": ["Здоровья мало! Отойдите и поешьте.", "Вы почти без сердечек, осторожнее!"],
    "hp_crit": ["Сейчас умрёте! Бегите!", "Одно сердечко! Уходите!"],
    "hungry_food": ["Вы голодны. Съешьте {food}.", "Поешьте, у вас есть {food}."],
    "hungry_none": ["Есть хочется, а еды нет. Найдите что-нибудь.", "Голод уже {food_lvl}, а еды в инвентаре нет."],
    "air": ["Воздух! Наверх!", "Вы тонете! Всплывайте!"],
    "lava": ["Лава! Выбирайтесь!", "Вы в лаве!"],
    "fire": ["Вы горите! В воду!", "Горите! Потушитесь!"],
    "tool_break": ["{name} сейчас сломается.", "{name} на последнем издыхании."],
    "night_bed": ["Темнеет. Кровать у вас есть — можно спать.", "Вечер. Поставьте кровать, ночь пропустим."],
    "night_nobed": ["Темнеет, а кровати нет. Укрытие и факелы, быстро.", "Ночь скоро. Без кровати — держитесь у света."],
    "dark_cave": ["Темно тут. Факел поставьте.", "Ничего не видно, нужен свет."],
}


def short(item_id):
    return (item_id or "").split(":")[-1]


def dim_of(s):
    m = re.search(r"/\s*([\w.-]+:[\w./-]+)\]", s.get("dim", ""))
    return m.group(1) if m else s.get("dim", "")


def clock(day_time):
    t = (int(day_time) + 6000) % 24000
    return f"{t // 1000:02d}:{int(t % 1000 * 0.06):02d}"


def side(angle):
    a = abs(angle)
    return "впереди" if a <= 45 else "сзади" if a >= 135 else ("справа" if angle > 0 else "слева")


def inv_counts(s):
    out = {}
    for it in s.get("inv") or []:
        out[(it.get("id"), it.get("name"))] = out.get((it.get("id"), it.get("name")), 0) + it.get("count", 0)
    return out


class Eyes:
    def __init__(self):
        self.state, self.at, self.prev = None, 0.0, None
        self.log = deque(maxlen=40)            # (время, что произошло) — «что делал последние минуты»
        self.cool = {}                         # повод → не раньше
        self.seen_biomes = set()
        self.night_day = -1
        self.last_say = 0.0

    def fresh(self, sec=6):
        return bool(self.state and self.state.get("in_world") and time.time() - self.at < sec)

    def _ok(self, key, sec):
        now = time.time()
        if now < self.cool.get(key, 0):
            return False
        self.cool[key] = now + sec
        return True

    def _note(self, text):
        self.log.append((time.time(), text))

    # ---------------- новое состояние → поводы ----------------
    def update(self, s):
        """Возвращает список реакций: (вид, текст, эмоция, срочно, через_ИИ)."""
        prev, self.prev = self.state, self.state
        self.state, self.at = s, time.time()
        if not s.get("in_world"):
            if prev and prev.get("in_world"):
                self._note("вышел из мира")
            return []
        acts = []
        if not prev or not prev.get("in_world"):
            self._note(f"зашёл в мир: {DIMS.get(dim_of(s), dim_of(s))}, {s.get('biome_name')}")
            self.seen_biomes.add(s.get("biome"))
            return []
        self._diff(prev, s, acts)
        if s.get("paused") or s.get("dead"):
            return []
        self._danger(prev, s, acts)
        self._needs(s, acts)
        return acts

    def _diff(self, prev, s, acts):
        # инвентарь: что прибавилось и убавилось
        a, b = inv_counts(prev), inv_counts(s)
        gained, lost = [], []
        for k in set(a) | set(b):
            d = b.get(k, 0) - a.get(k, 0)
            if d > 0:
                gained.append((k, d))
            elif d < 0:
                lost.append((k, -d))
        if gained:
            self._note("+ " + ", ".join(f"{n} ×{d}" for (i, n), d in gained))
            rare = [(n, d) for (i, n), d in gained if short(i) in VALUABLE]
            if rare and self._ok("valuable:" + rare[0][0], 300):
                acts.append(("valuable", f"Хозяин только что получил: {', '.join(f'{n} ×{d}' for n, d in rare)}.", "happy", False, True))
        if lost:
            self._note("− " + ", ".join(f"{n} ×{d}" for (i, n), d in lost))
        # урон
        hp0, hp = prev.get("hp", 0), s.get("hp", 0)
        if hp < hp0 - 0.5:
            dmg = s.get("damage") or {}
            by = dmg.get("by") or {"fall": "падение", "lava": "лава", "inFire": "огонь", "onFire": "огонь",
                                   "drown": "вода", "starve": "голод", "cactus": "кактус"}.get(dmg.get("type"), dmg.get("type") or "?")
            self._note(f"получил урон: {by} (−{round(hp0 - hp, 1)} hp, осталось {hp}/{s.get('hp_max')})")
        if s.get("dead") and not prev.get("dead"):
            self._note("погиб")
        # места
        d0, d1 = dim_of(prev), dim_of(s)
        if d1 != d0:
            name = DIMS.get(d1, d1)
            self._note(f"перешёл в {name}")
            acts.append(("dimension", f"Хозяин только что перешёл в измерение «{name}» (биом {s.get('biome_name')}).",
                         "surprised", False, True))
        elif s.get("biome") != prev.get("biome"):
            self._note(f"биом: {s.get('biome_name')}")
            if s.get("biome") not in self.seen_biomes and self._ok("biome", 240):
                acts.append(("biome", f"Хозяин впервые за сегодня зашёл в биом «{s.get('biome_name')}».", "happy", False, True))
            self.seen_biomes.add(s.get("biome"))
        scr0, scr1 = (prev.get("screen") or {}).get("title"), (s.get("screen") or {}).get("title")
        if scr1 and scr1 != scr0:
            self._note(f"открыл: {scr1}")

    def _danger(self, prev, s, acts):
        for e in s.get("near") or []:
            eid, dist, ang = short(e.get("id")), e.get("dist", 99), e.get("angle", 0)
            if eid == "creeper" and dist <= 5 and e.get("swell", 0) > 0 and self._ok("creeper_hiss", 6):
                acts.append(("creeper_hiss", random.choice(LINES["creeper_hiss"]), "surprised", True, False))
                return
            if eid == "creeper" and dist <= 7 and abs(ang) >= 110 and self._ok("creeper_behind", 20):
                acts.append(("creeper_behind", random.choice(LINES["creeper_behind"]), "surprised", True, False))
                return
            if e.get("hostile") and dist <= 4 and abs(ang) >= 120 and abs(e.get("dy", 0)) <= 3 and self._ok("behind:" + eid, 25):
                acts.append(("mob_behind", random.choice(LINES["mob_behind"]).format(name=e.get("name")), "surprised", True, False))
                return
        hp, hp0 = s.get("hp", 20), prev.get("hp", 20)
        if hp <= 4 and hp < hp0 and self._ok("hp_crit", 20):
            acts.append(("hp_crit", random.choice(LINES["hp_crit"]), "surprised", True, False))
        elif hp <= 7 and hp < hp0 and self._ok("hp_low", 45):
            acts.append(("hp_low", random.choice(LINES["hp_low"]), "sad", True, False))
        if s.get("lava") and self._ok("lava", 6):
            acts.append(("lava", random.choice(LINES["lava"]), "surprised", True, False))
        elif s.get("fire") and not s.get("water") and self._ok("fire", 10):
            acts.append(("fire", random.choice(LINES["fire"]), "surprised", True, False))
        if s.get("water") and s.get("air", 300) <= 90 and self._ok("air", 8):
            acts.append(("air", random.choice(LINES["air"]), "surprised", True, False))

    def _needs(self, s, acts):
        food = s.get("food", 20)
        if food <= 6 and self._ok("hungry", 600):
            foods = [it.get("name") for it in s.get("inv") or [] if short(it.get("id")) in FOOD]
            key = "hungry_food" if foods else "hungry_none"
            acts.append((key, random.choice(LINES[key]).format(food=foods[0] if foods else "", food_lvl=food), "sad", False, False))
        h = s.get("hand") or {}
        if h.get("dur_max", 0) >= 50 and h.get("dur", 999) <= max(3, h["dur_max"] * 0.06) and self._ok("tool:" + h.get("id", ""), 600):
            acts.append(("tool_break", random.choice(LINES["tool_break"]).format(name=h.get("name")), "neutral", False, False))
        t, day = s.get("day_time", 0) % 24000, s.get("day_time", 0) // 24000
        if dim_of(s) == "minecraft:overworld" and 12300 <= t <= 13200 and day != self.night_day and not s.get("sleeping"):
            self.night_day = day
            bed = any(short(it.get("id")).endswith("_bed") for it in s.get("inv") or [])
            acts.append(("night", random.choice(LINES["night_bed" if bed else "night_nobed"]), "neutral", False, False))
        if (s.get("light", 15) <= 2 and not s.get("sky") and s.get("pos", [0, 64, 0])[1] < 50
                and not any(short(it.get("id")) in ("torch", "lantern") for it in [s.get("hand") or {}, s.get("offhand") or {}])
                and self._ok("dark", 900)):
            acts.append(("dark_cave", random.choice(LINES["dark_cave"]), "neutral", False, False))

    # ---------------- сводка для ИИ ----------------
    def describe(self):
        s = self.state or {}
        if not s.get("in_world"):
            return "Хозяин сейчас не в мире Майнкрафта."
        p = s.get("pos") or [0, 0, 0]
        weather = "гроза" if s.get("thunder") else "дождь" if s.get("rain") else "ясно"
        lines = [
            f"Мир: {DIMS.get(dim_of(s), dim_of(s))}, биом «{s.get('biome_name')}», координаты {p[0]} {p[1]} {p[2]}, "
            f"день {s.get('day_time', 0) // 24000 + 1}, время в игре {clock(s.get('day_time', 0))}, {weather}, "
            f"свет {s.get('light')}/15{'' if s.get('sky') else ', под крышей/под землёй'}. Режим: {s.get('mode')}."
            + (" Игра на паузе." if s.get("paused") else ""),
            f"Игрок {s.get('player')}: здоровье {s.get('hp')}/{s.get('hp_max')}, сытость {s.get('food')}/20, броня {s.get('armor')}, "
            f"уровень опыта {s.get('level')}" + (f", воздух {s.get('air')}/{s.get('air_max')}" if s.get("water") else "") + ".",
        ]
        state = [w for k, w in (("dead", "мёртв"), ("lava", "в лаве"), ("fire", "горит"), ("water", "в воде"),
                                ("sleeping", "спит"), ("elytra", "летит на элитрах"), ("mining", "копает"),
                                ("using", "использует предмет"), ("sprint", "бежит"), ("sneak", "крадётся")) if s.get(k)]
        if s.get("vehicle"):
            state.append(f"верхом/в транспорте: {s['vehicle']}")
        if state:
            lines.append("Сейчас: " + ", ".join(state) + ".")
        h, o = s.get("hand") or {}, s.get("offhand") or {}
        def it(x):
            return f"{x['name']}" + (f" ×{x['count']}" if x.get("count", 1) > 1 else "") + (
                f" (прочность {x['dur']}/{x['dur_max']})" if x.get("dur_max") else "")
        lines.append("В руке: " + (it(h) if h.get("name") else "пусто") + (f"; во второй руке: {it(o)}" if o.get("name") else "") + ".")
        lk = s.get("look") or {}
        if lk.get("name"):
            lines.append(f"Смотрит на: {lk['name']} ({lk.get('dist')} м).")
        near = s.get("near") or []
        if near:
            def ent(e):
                tag = " — враждебный" if e.get("hostile") else " — игрок" if e.get("player") else ""
                up = f", {'выше' if e['dy'] > 0 else 'ниже'} на {abs(e['dy'])}" if abs(e.get("dy", 0)) >= 3 else ""
                hiss = ", ШИПИТ" if e.get("swell", 0) > 0 else ""
                return f"{e.get('name')} {e.get('dist')} м {side(e.get('angle', 0))}{up}{tag}{hiss}"
            lines.append("Рядом: " + "; ".join(ent(e) for e in near[:8]) + ".")
        else:
            lines.append("Рядом никого.")
        inv = sorted(s.get("inv") or [], key=lambda x: -x.get("count", 0))
        if inv:
            lines.append("Инвентарь: " + ", ".join(f"{x['name']} ×{x['count']}" for x in inv[:30])
                         + (f" и ещё {len(inv) - 30} видов" if len(inv) > 30 else "") + ".")
        eff = s.get("effects") or []
        if eff:
            lines.append("Эффекты: " + ", ".join(f"{e['name']} {e['lvl']} ({e['sec']} с)" for e in eff) + ".")
        if s.get("screen"):
            lines.append(f"Открыт экран: {s['screen'].get('title') or s['screen'].get('cls')}.")
        recent = self.recent(8)
        if recent:
            lines.append("Последние минуты: " + "; ".join(recent) + ".")
        return "\n".join(lines)

    def recent(self, n=15, minutes=10):
        now = time.time()
        return [f"{int((now - t) // 60)} мин назад: {x}" if now - t >= 60 else x
                for t, x in list(self.log)[-n:] if now - t <= minutes * 60]
