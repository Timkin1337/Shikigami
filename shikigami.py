"""Точка входа ядра из любой папки: python shikigami.py [--no-voice]"""
import os
import runpy
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)
# Кэши моделей (HF_HOME, TORCH_HOME, XDG_CACHE_HOME) выбирает core/config.py: data/cache рядом с программой.
# Падение внутри нативных библиотек (torch, Whisper, звук) не оставляет трассировки — пишем её в файл
import faulthandler
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
_crash = open(os.path.join(ROOT, "data", "crash.log"), "a", encoding="utf-8")
faulthandler.enable(_crash)
runpy.run_module("core", run_name="__main__")
