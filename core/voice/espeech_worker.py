"""Рабочий процесс эмоционального голоса (ESpeech — F5-TTS, дообученный на русском, Apache-2.0).

Запускается ядром в ОТДЕЛЬНОМ окружении (voice.espeech_python): там torch/f5-tts/ruaccent, которых нет в ядре.
Протокол — строки JSON: на вход {"text", "emotion", "out"}, на выход {"ok", "sec"} или {"error"}.
Эмоция = образец голоса: data/voices/<голос>/<эмоция>.wav + texts.json (что сказано в образце).
F5 повторяет и тембр, и интонацию образца — поэтому «радостный» образец даёт радостную фразу.
Сеть не нужна: модели берутся из локального кэша HF (HF_HUB_OFFLINE=1).
"""
import json, os, sys, time, types

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"
os.environ["WANDB_MODE"] = "disabled"

VOICE_DIR, DEVICE = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "cpu")
NFE = int(os.environ.get("ESPEECH_NFE", "24"))


def _ascii_crfsuite():
    """pycrfsuite (ударения) открывает модель C-функцией — путь с кириллицей не открывается. Если окружение
    запущено через ASCII-ссылку, подставляем путь через неё."""
    import pycrfsuite
    from ruaccent.koziev.rupostagger import rupostagger as rp
    real = os.path.realpath(sys.prefix)

    class Tagger(pycrfsuite.Tagger):
        def open(self, name, *a, **k):
            name = os.path.normpath(name)
            if name.lower().startswith(real.lower()):
                name = sys.prefix + name[len(real):]
            return super().open(name, *a, **k)

    rp.pycrfsuite = types.SimpleNamespace(Tagger=Tagger)


def main():
    import soundfile as sf
    from huggingface_hub import hf_hub_download
    from ruaccent import RUAccent
    _ascii_crfsuite()
    import f5_tts.infer.utils_infer as U
    from f5_tts.model import DiT

    def sf_load(path, *a, **k):          # torchaudio 2.11 читает через torchcodec, а тому нужны DLL FFmpeg — читаем soundfile
        import torch
        y, sr = sf.read(str(path), dtype="float32", always_2d=True)
        return torch.from_numpy(y.T.copy()), sr
    U.torchaudio.load = sf_load

    U.device = DEVICE
    acc = RUAccent()
    acc.load(omograph_model_size="turbo3.1", use_dictionary=True, tiny_mode=False)
    repo = "ESpeech/ESpeech-TTS-1_RL-V2"
    model = U.load_model(DiT, dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4),
                         hf_hub_download(repo, "espeech_tts_rlv2.pt"), vocab_file=hf_hub_download(repo, "vocab.txt"),
                         device=DEVICE)
    voc = U.load_vocoder(device=DEVICE)
    def load_refs(d):
        texts = json.load(open(os.path.join(d, "texts.json"), encoding="utf-8"))
        return {emo: U.preprocess_ref_audio_text(os.path.join(d, f"{emo}.wav"), acc.process_all(text), show_info=lambda *a: None)
                for emo, text in texts.items()}

    refs = load_refs(VOICE_DIR)                     # яркие эмоции; образцы готовим один раз: обрезка тишины, ударения
    mild_dir = os.path.join(VOICE_DIR, "mild")
    mild = load_refs(mild_dir) if os.path.exists(os.path.join(mild_dir, "texts.json")) else {}
    print(json.dumps({"ready": True, "emotions": sorted(refs), "mild": bool(mild)}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
            t0 = time.time()
            # сила эмоций: off — всегда спокойный образец (эмоция только в словах), mild — мягкие, full — яркие
            emo, strength = req.get("emotion"), req.get("strength", "mild")
            pool = refs if strength == "full" or not mild else mild
            if strength == "off":
                emo = "neutral"
            ref_audio, ref_text = pool.get(emo) or refs["neutral"]
            wav, sr, _ = U.infer_process(ref_audio, ref_text, acc.process_all(req["text"]), model, voc,
                                         nfe_step=int(req.get("steps") or NFE), cross_fade_duration=0.15, speed=req.get("speed", 1.0),
                                         device=DEVICE, show_info=lambda *a: None, progress=None)
            sf.write(req["out"], wav, sr)
            print(json.dumps({"ok": True, "sec": round(time.time() - t0, 2), "sr": sr}), flush=True)
        except Exception as e:
            print(json.dumps({"error": f"{type(e).__name__}: {e}"[:300]}), flush=True)


if __name__ == "__main__":
    main()
