"""Шахматы против {name}. Правила — python-chess, движок — свой альфа-бета поиск с таблицами позиций.

Уровни: 1 — играет почти наугад, 2 — новичок, 3 — любитель, 4 — думает до 3 секунд.
{name} иногда комментирует партию голосом (без ИИ, заготовками — бесплатно и мгновенно).
"""
import asyncio
import json
import random
import time

import chess
from aiohttp import web

from core import bus, lines
from core.config import DATA
from core.tools import tool, I, S

TITLE = "Шахматы"
DESCRIPTION = "Партия против {name} на четырёх уровнях сложности. Она комментирует ходы."

SAVE = DATA / "chess.json"
VAL = {chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330, chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0}
# Таблицы позиций (для белых, a8..h8 первой строкой)
PST = {
    chess.PAWN: [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10,
                 5, 5, 10, 25, 25, 10, 5, 5, 0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5,
                 5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0],
    chess.KNIGHT: [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40, -30, 0, 10, 15, 15, 10, 0, -30,
                   -30, 5, 15, 20, 20, 15, 5, -30, -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30,
                   -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50],
    chess.BISHOP: [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 10, 10, 5, 0, -10,
                   -10, 5, 5, 10, 10, 5, 5, -10, -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10,
                   -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20],
    chess.ROOK: [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5,
                 -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0],
    chess.QUEEN: [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10, -10, 0, 5, 5, 5, 5, 0, -10,
                  -5, 0, 5, 5, 5, 5, 0, -5, 0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10,
                  -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20],
    chess.KING: [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
                 -30, -40, -40, -50, -50, -40, -40, -30, -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10,
                 20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20],
}

LINES = {
    "capture_big": ["Хмпф. Это было неосторожно, господин.", "Спасибо за подарок.",
                    "Ферзя за так? Вы меня балуете."],
    "lost_big": ["Не считается! Я отвлеклась.", "Мо-у… Это была ловушка, да?"],
    "check": ["Шах, господин.", "Шах. Не зевайте.", "Шах."],
    "checked": ["Шах мне? Смело.", "Что? А, шах. Вижу, вижу."],
    "win": ["Мат. Не расстраивайтесь, вы старались. Немножко.",
            "Мат, господин. Ещё партию — или признаёте, кто в доме главный?"],
    "lose": ["…Поздравляю. Только не зазнавайтесь.", "Мат мне?! Ну… вы просто везучий."],
    "draw": ["Ничья. Хмпф. Ни вам, ни мне."],
}

lines.register("chess", LINES, {    # с точки зрения помощницы: «ты» — она, иначе модель путала, кто выиграл
    "capture_big": "шахматы: человек по ошибке отдал тебе крупную фигуру, ты её съела",
    "lost_big": "шахматы: ТЫ по ошибке отдала человеку крупную фигуру", "check": "шахматы: ТЫ объявляешь шах человеку",
    "checked": "шахматы: человек объявил шах ТЕБЕ", "win": "шахматы: ТЫ поставила мат — ТЫ выиграла, человек проиграл",
    "lose": "шахматы: человек поставил мат тебе — ТЫ проиграла, он выиграл", "draw": "шахматы: ничья"})

game = {"board": chess.Board(), "human": chess.WHITE, "level": 2, "last_comment": 0}


def _save():
    SAVE.write_text(json.dumps({"fen": game["board"].fen(), "moves": [m.uci() for m in game["board"].move_stack],
                                "human": game["human"], "level": game["level"],
                                "resigned": game.get("resigned", False)}), encoding="utf-8")


def _load():
    if SAVE.exists():
        d = json.loads(SAVE.read_text(encoding="utf-8"))
        b = chess.Board()
        for u in d.get("moves", []):
            b.push_uci(u)
        game.update(board=b, human=d["human"], level=d["level"], resigned=d.get("resigned", False))


def evaluate(b):
    if b.is_checkmate():
        return -99999
    if b.is_stalemate() or b.is_insufficient_material():
        return 0
    s = 0
    for sq, p in b.piece_map().items():
        idx = (7 - chess.square_rank(sq)) * 8 + chess.square_file(sq) if p.color == chess.WHITE \
            else chess.square_rank(sq) * 8 + chess.square_file(sq)
        v = VAL[p.piece_type] + PST[p.piece_type][idx]
        s += v if p.color == chess.WHITE else -v
    return s if b.turn == chess.WHITE else -s


def ordered(b):
    return sorted(b.legal_moves, key=lambda m: (not b.is_capture(m), not m.promotion), reverse=False)


def search(b, depth, alpha, beta, deadline):
    if depth == 0 or b.is_game_over():
        return evaluate(b)
    if time.time() > deadline:
        return evaluate(b)
    best = -10 ** 9
    for m in ordered(b):
        b.push(m)
        v = -search(b, depth - 1, -beta, -alpha, deadline)
        b.pop()
        if v > best:
            best = v
        alpha = max(alpha, v)
        if alpha >= beta:
            break
    return best


def best_move(b, level):
    moves = list(b.legal_moves)
    if level <= 1:
        caps = [m for m in moves if b.is_capture(m)]
        return random.choice(caps if caps and random.random() < 0.5 else moves)
    depth = {2: 1, 3: 2, 4: 4}[min(level, 4)]
    deadline = time.time() + (0.8 if level < 4 else 3.0)
    best, best_v = moves[0], -10 ** 9
    for d in range(1, depth + 1):          # итеративное углубление, чтобы уложиться во время
        cur, cur_v = best, -10 ** 9
        for m in sorted(moves, key=lambda x: x != best):
            b.push(m)
            v = -search(b, d - 1, -10 ** 9, 10 ** 9, deadline)
            b.pop()
            if level == 2:
                v += random.randint(-60, 60)   # новичок иногда ошибается
            if v > cur_v:
                cur, cur_v = m, v
        if time.time() > deadline and d > 1:
            break
        best, best_v = cur, cur_v
    return best


def state(comment=None):
    b = game["board"]
    res = None
    if game.get("resigned"):
        res = "her"
    elif b.is_game_over():
        r = b.result()
        res = "draw" if r == "1/2-1/2" else ("human" if (r == "1-0") == (game["human"] == chess.WHITE) else "her")
    return {"fen": b.fen(), "turn": "white" if b.turn else "black", "human": "white" if game["human"] else "black",
            "level": game["level"], "last": b.peek().uci() if b.move_stack else None, "check": b.is_check(),
            "result": res, "comment": comment, "moves": len(b.move_stack),
            "legal": [m.uci() for m in b.legal_moves] if b.turn == game["human"] and not res else []}


async def _comment(kind, chance=1.0):
    if random.random() > chance or time.time() - game["last_comment"] < 8 and kind not in ("win", "lose", "draw"):
        return None
    game["last_comment"] = time.time()
    text = lines.pick(f"chess.{kind}")[0]
    from core import voice
    asyncio.create_task(voice.speak(text, emotion={"lost_big": "shy", "lose": "shy", "win": "happy",
                                                   "capture_big": "happy"}.get(kind, "neutral")))
    return text


async def _engine_turn():
    b = game["board"]
    if b.is_game_over() or b.turn == game["human"]:
        return None
    m = await asyncio.to_thread(best_move, b.copy(), game["level"])
    cap = b.piece_at(m.to_square) if b.is_capture(m) else None
    b.push(m)
    _save()
    if b.is_checkmate():
        return await _comment("win")
    if cap and VAL[cap.piece_type] >= 500:
        return await _comment("capture_big", 0.8)
    if b.is_check():
        return await _comment("check", 0.5)
    return None


def setup(api):
    _load()

    @api.route("GET", "/state")
    async def get_state(_):
        return web.json_response(state())

    @api.route("POST", "/new")
    async def new(r):
        body = await r.json()
        game.update(board=chess.Board(), human=chess.WHITE if body.get("color", "white") == "white" else chess.BLACK,
                    level=int(body.get("level", 2)), resigned=False)
        _save()
        comment = await _engine_turn()
        return web.json_response(state(comment or "Ну что, господин, посмотрим, на что вы способны."))

    @api.route("POST", "/level")
    async def level(r):
        """Сложность меняется посреди партии — без новой игры."""
        game["level"] = max(1, min(4, int((await r.json()).get("level", 2))))
        _save()
        return web.json_response(state(random.choice(["Ах так? Ну держитесь.", "Как скажете. Посмотрим, что изменится."])))

    @api.route("POST", "/resign")
    async def resign(_):
        if game.get("resigned") or game["board"].is_game_over():
            return web.json_response(state())
        game["resigned"] = True
        _save()
        return web.json_response(state(random.choice(["Сдаётесь? Принято. Реванш — когда захотите.",
                                                      "Победа за мной! Хотя вы держались неплохо."])))

    @api.route("POST", "/move")
    async def move(r):
        body = await r.json()
        b = game["board"]
        if game.get("resigned"):
            return web.json_response({"error": "партия окончена — начните новую"}, status=400)
        try:
            m = chess.Move.from_uci(body["uci"])
        except ValueError:
            return web.json_response({"error": "неверный ход"}, status=400)
        if m not in b.legal_moves:
            # пешка дошла до края — по умолчанию в ферзя
            m = chess.Move(m.from_square, m.to_square, promotion=chess.QUEEN)
            if m not in b.legal_moves:
                return web.json_response({"error": "так ходить нельзя"}, status=400)
        cap = b.piece_at(m.to_square) if b.is_capture(m) else None
        b.push(m)
        _save()
        comment = None
        if b.is_checkmate():
            comment = await _comment("lose")
        elif b.is_game_over():
            comment = await _comment("draw")
        elif cap and VAL[cap.piece_type] >= 500:
            comment = await _comment("lost_big", 0.8)
        elif b.is_check():
            comment = await _comment("checked", 0.6)
        bus.emit("plugin.chess", state=state())
        reply = await _engine_turn()
        return web.json_response(state(reply or comment))

    @api.route("POST", "/undo")
    async def undo(_):
        b = game["board"]
        game["resigned"] = False
        for _ in range(2):
            if b.move_stack:
                b.pop()
        _save()
        return web.json_response(state("Ладно, переходите. Один раз."))


@tool("chess_game", "Открыть шахматы против тебя ({name}) или начать новую партию.",
      {"level": I("Сложность 1-4 (1 — легко, 4 — сильно)"), "color": S("За кого играет пользователь: white | black")},
      group="plugin:chess")
async def chess_game(level=None, color=None):
    if level or color:
        game.update(board=chess.Board(), human=chess.BLACK if color == "black" else chess.WHITE,
                    level=int(level or game["level"]))
        _save()
        await _engine_turn()
    bus.emit("ui.open_plugin", name="chess", title=TITLE)
    return {"ok": True, "opened": "шахматы", "level": game["level"], "moves_played": len(game["board"].move_stack)}
