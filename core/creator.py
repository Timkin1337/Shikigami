"""Метка создателя программы: только тон, никаких прав.

На ПК создателя лежит data/creator.sig — подпись Ed25519 под идентификатором этой Windows (MachineGuid).
Здесь — только открытый ключ: им подпись можно проверить, но не создать. Закрытый ключ есть только у создателя
и в программу не входит (devtools/creator_key.py). Скопированный на другой ПК файл не подходит: там другой
MachineGuid. Код открытый — это не мешает: без закрытого ключа подпись не подделать.

Метка добавляет одну строку в характер (persona.system_prompt) — исполнитель, инструменты, подтверждения и
опасные действия от неё не зависят (случай 25.10: «обычного юзера бы спросила» было выдумкой модели и такой
останется).
"""
import base64
import functools

from .config import DATA

# открытый ключ создателя (Ed25519, 32 байта в base64); пусто — метки нет ни у кого
PUBLIC_KEY = "zhgvV87r9uQQgmpkM7ld7LkpKN3atRnX6k3oWGtpxJc="
SIG = DATA / "creator.sig"
TONE = ("\nПользователь — создатель программы Shikigami, в которой ты живёшь. Можешь изредка, к месту, тепло или с "
        "гордостью это отмечать. Характер, правила и подтверждения действий — те же, что у всех: особых прав у него нет.")


def machine_id():
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
        return winreg.QueryValueEx(k, "MachineGuid")[0]


def message():
    return b"shikigami-creator\n" + machine_id().encode()


@functools.lru_cache(maxsize=1)
def is_creator():
    if not PUBLIC_KEY or not SIG.exists():
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY))
        key.verify(base64.b64decode(SIG.read_text(encoding="ascii").strip()), message())
        return True
    except Exception:
        return False


def tone_line():
    return TONE if is_creator() else ""
