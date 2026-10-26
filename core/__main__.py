"""Запуск ядра: python -m core"""
import asyncio
import sys

from aiohttp import web

from . import commands, plugins, scheduler, tools
from .config import config


def _preload():
    """Нативные библиотеки грузим заранее, по очереди и в строгом порядке.

    winrt (виджет «что играет») подтягивает системную onnxruntime.dll из Windows. Если она загрузилась
    первой, наша onnxruntime (детектор речи, Whisper) получает чужую DLL и процесс падает с нарушением
    доступа (0xC0000005) без трассировки. Поэтому сначала свои библиотеки — и всегда, даже без
    микрофона: голосовые из Telegram тоже идут через Whisper. winrt — последним.
    """
    import importlib
    mods = ["onnxruntime", "ctranslate2", "faster_whisper", "sounddevice"]
    from .voice.tts import MODELS, URLS
    if any((MODELS / f"{v}.pt").exists() for v, _ in URLS):
        mods.append("torch")
    mods += ["win32gui", "winrt.windows.media.control"]
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception as e:
            print(f"не загрузилась библиотека {m}: {e}", flush=True)


def _background_priority():
    """Ядро — фоновый помощник: при нагрузке уступает играм, окнам и курсору. Загрузка моделей при запуске
    (Whisper, torch) с обычным приоритетом подвешивала систему; ответы от пониженного приоритета не медленнее."""
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception as e:
        print("приоритет ядра не понизился:", e, flush=True)


async def main():
    _background_priority()
    _preload()
    tools.load_all()
    plugins.load_all()
    commands.seed()
    app = __import__("core.server", fromlist=["make_app"]).make_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    host, port = config.get("server.host"), config.get("server.port")
    await web.TCPSite(runner, host, port).start()
    print(f"Shikigami слушает на http://{host}:{port}", flush=True)
    from . import llm
    asyncio.get_running_loop().create_task(llm.auto_pick_loop())   # самая быстрая модель NVIDIA — сама

    from . import behavior
    tasks = [asyncio.create_task(scheduler.run()), asyncio.create_task(behavior.run())]
    if "--no-voice" not in sys.argv and config.get("voice.enabled"):
        from . import voice
        tasks.append(asyncio.create_task(voice.start()))
    from . import telegram   # сам следит за настройками: включить бота можно без перезапуска
    tasks.append(asyncio.create_task(telegram.run()))
    from . import mcp_client
    tasks.append(asyncio.create_task(mcp_client.start_all()))
    await commands.on_startup()
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        from .llm import local_server
        local_server.stop()
