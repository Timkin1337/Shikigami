"""Новый тембр по описанию (Qwen3-TTS VoiceDesign, Apache-2.0) — запускается ядром в окружении голоса.

voice_design.py <папка тембра> <описание по-английски> <название>
Пишет образцы шести эмоций дважды: яркие (в корне) и мягкие (mild/) — для ползунка «сила эмоций»; texts.json — что
сказано в каждом образце; meta.json. Ход — строками JSON {"step", "of"}. Сеть не нужна, если модель уже скачана.
Мягкие образцы просим «ровно, без дрожи и хрипа»: яркие эмоции VoiceDesign иногда дают дрожащий голос, а ESpeech
копирует образец целиком — с дрожью.
"""
import json, os, sys

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["DO_NOT_TRACK"] = "1"

OUT, DESC, NAME = sys.argv[1], sys.argv[2], sys.argv[3]
EMO = {
    "neutral": ("calm, friendly, even tone", "Сейчас посмотрю. В Минске завтра пасмурно, около десяти градусов, зонт можно не брать."),
    "happy": ("very happy and excited, a little laugh, bright smile", "Ура, получилось! Я же говорила, что справлюсь, правда здорово?"),
    "sad": ("sad and quiet, disappointed, slower pace", "Ну вот... ты опять уходишь. Я, конечно, подожду, но мне будет скучно."),
    "angry": ("annoyed and pouting, grumbling, sharp", "Эй! Не трогай мои уши! Я тебе не игрушка, понял?"),
    "shy": ("shy and embarrassed, soft, hesitant", "Н-не то чтобы я старалась для тебя... просто так получилось."),
    "surprised": ("surprised and amazed, rising intonation", "Что? Серьёзно? Ты правда нашёл меня за этим окном?"),
}
STEADY = " Steady, clear, smooth voice: no trembling, no shaking, no vocal fry, no breathiness, no whisper."


def main():
    import torch, soundfile as sf
    from qwen_tts import Qwen3TTSModel
    model = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign", device_map="cuda:0", dtype=torch.bfloat16)
    os.makedirs(os.path.join(OUT, "mild"), exist_ok=True)
    texts = {e: t for e, (_, t) in EMO.items()}
    jobs = [(e, False) for e in EMO] + [(e, True) for e in EMO]
    for i, (emo, mild) in enumerate(jobs, 1):
        how = EMO[emo][0]
        instr = f"{DESC} Speaking Russian, clean studio recording. Emotion: " + (f"slightly {how}, gentle and controlled." + STEADY if mild else f"{how}.")
        torch.manual_seed(7)                       # одно зерно — тембр меньше гуляет между эмоциями
        wavs, sr = model.generate_voice_design(text=EMO[emo][1], language="Russian", instruct=instr)
        sf.write(os.path.join(OUT, "mild" if mild else "", f"{emo}.wav"), wavs[0], sr)
        print(json.dumps({"step": i, "of": len(jobs)}), flush=True)
    for d in (OUT, os.path.join(OUT, "mild")):
        json.dump(texts, open(os.path.join(d, "texts.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump({"name": NAME, "description": DESC, "source": "Qwen3-TTS VoiceDesign (Apache-2.0)"},
              open(os.path.join(OUT, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({"done": True}), flush=True)


if __name__ == "__main__":
    main()
