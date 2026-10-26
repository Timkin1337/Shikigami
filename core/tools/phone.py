"""Связь с телефоном через Telegram-бота: отправить файл или снимок экрана хозяину."""
from pathlib import Path

from . import tool, S


@tool("send_to_phone", "Отправить файл с ПК пользователю в Telegram (на телефон): снимок экрана, документ, фото, архив до 50 МБ.",
      {"path": S("Полный путь к файлу на ПК"), "caption": S("Подпись, необязательно")}, ["path"])
async def send_to_phone(path, caption=""):
    from .. import telegram
    if not telegram.bot:
        return {"error": "Telegram-бот не подключён: нужен токен и галочка в настройках"}
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"нет такого файла: {p}"}
    await telegram.bot.send_file(p, caption)
    return {"sent": p.name}
