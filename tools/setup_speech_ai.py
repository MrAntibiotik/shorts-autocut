"""ЕКСПЕРИМЕНТАЛЬНО: встановлення «розумного» розпізнавання мови (Silero VAD + faster-whisper).

Запуск: extra\\setup_speech_ai.bat. Що робить:
  1. Ставить у venv бібліотеки silero-vad і faster-whisper (PyTorch уже є після SETUP.bat).
  2. Завантажує модель faster-whisper (за замовчуванням large-v3, ~3 ГБ) у models\\faster-whisper\\.
     Обірвалось — запустіть ще раз, вже завантажене не качається вдруге.
  3. Пробує розпізнавання на відеокарті (float16 → int8_float16), якщо не вийшло — на процесорі
     (int8, повільніше). Записує вдалий варіант у settings.json → speech_ai.device / compute_type.

Нічого не вмикає: розпізнавання вмикається галочкою у вікні запуску. Решту інструмента не чіпає.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from shorts.config import SETTINGS_FILE, load_settings  # noqa: E402

MODELS = {
    "1": ("large-v3", "найкраща якість для української (~3 ГБ) — рекомендовано"),
    "2": ("large-v3-turbo", "швидша, трохи гірша якість (~1,6 ГБ)"),
    "3": ("medium", "середня (~1,5 ГБ), для слабшої відеокарти або процесора"),
}


def update_settings(changes: dict) -> None:
    data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8-sig"))
    data.setdefault("speech_ai", {}).update(changes)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  settings.json оновлено: {changes}")


def pip_install() -> bool:
    print("\n[1/3] Встановлюю бібліотеки silero-vad і faster-whisper…")
    r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "silero-vad", "faster-whisper"])
    if r.returncode != 0:
        print("  [ПОМИЛКА] Не вдалося встановити бібліотеки (перевірте інтернет і запустіть ще раз).")
        return False
    return True


def download(name: str, models_dir: Path) -> Path:
    print(f"\n[2/3] Завантажую модель {name} у {models_dir}… (може зайняти 5–20 хв)")
    from faster_whisper import download_model
    dst = models_dir / name
    dst.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, 4):
        try:
            download_model(name, output_dir=str(dst))
            break
        except Exception as e:
            print(f"  Спроба {attempt}: {e}")
            if attempt == 3:
                raise
            time.sleep(5 * attempt)
    if not (dst / "model.bin").exists():
        raise RuntimeError(f"Модель не завантажилась повністю: немає {dst / 'model.bin'}")
    print("  Модель на місці.")
    return dst


def test_variants(cfg: dict) -> dict | None:
    print("\n[3/3] Пробне розпізнавання…")
    from shorts import speech_ai
    t = np.arange(16000 * 3) / 16000
    y = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    for device, ctype in (("cuda", "float16"), ("cuda", "int8_float16"), ("cpu", "int8")):
        c = dict(cfg, device=device, compute_type=ctype)
        speech_ai._WHISPER = None
        try:
            t0 = time.time()
            speech_ai.transcribe_audio(y, c)
            print(f"  {device}/{ctype}: працює ({time.time() - t0:.1f} с)")
            return {"device": device, "compute_type": ctype}
        except Exception as e:
            print(f"  {device}/{ctype}: не вийшло — {type(e).__name__}: {str(e)[:200]}")
    return None


def test_vad() -> bool:
    try:
        from shorts.speech_ai import silero_mask
        silero_mask(np.zeros(16000 * 2, np.float32), {})
        print("  Silero VAD: працює")
        return True
    except Exception as e:
        print(f"  Silero VAD: не вийшло — {type(e).__name__}: {e}")
        return False


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("=" * 70)
    print(" ЕКСПЕРИМЕНТ: розумне розпізнавання мови (Silero VAD + faster-whisper)")
    print("=" * 70)
    print("Нічого в звичайній роботі не змінюється, доки ви не поставите галочку у вікні запуску.\n")
    for k, (name, desc) in MODELS.items():
        print(f"  {k} — {name}: {desc}")
    choice = input("Яку модель завантажити? [Enter = 1]: ").strip() or "1"
    name = MODELS.get(choice, MODELS["1"])[0]

    if not pip_install():
        return 1
    settings = load_settings()
    cfg = dict(settings.get("speech_ai", {}))
    from shorts.speech_ai import model_dir
    download(name, model_dir(cfg))
    cfg["model"] = name
    test_vad()
    ok = test_variants(cfg)
    if ok is None:
        print("\n[ПОМИЛКА] Розпізнавання не запрацювало ні на відеокарті, ні на процесорі. "
              "Надішліть цей текст. Звичайна робота інструмента від цього не постраждала.")
        update_settings({"model": name})
        return 1
    update_settings({"model": name, **ok})
    if ok["device"] == "cpu":
        print("\n  Увага: працює лише на процесорі — повільніше (хвилини на бій). Для швидкості спробуйте "
              "модель 3 (medium) або оновіть драйвер NVIDIA і запустіть ще раз.")
    print("\nГотово. Далі:\n"
          "  1) extra\\test_speech.bat — перевірити на відрізку запису (звіт із фразами і субтитрами);\n"
          "  2) якщо подобається — галочка «Розумне розпізнавання мови» у вікні запуску.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nПерервано. Запустіть ще раз — завантаження продовжиться.")
