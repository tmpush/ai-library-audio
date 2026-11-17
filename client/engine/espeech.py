"""Local Russian ESpeech adapter. Downloads are configured separately."""
import contextlib
import hashlib
import json
import re
import sys
from pathlib import Path

REVISION = "espeech-rlv2-sequential-v3"
FILES = ("espeech_tts_rlv2.pt", "vocab.txt", "vocos/config.yaml", "vocos/pytorch_model.bin")


def fingerprint(directory):
    root = Path(directory)
    digest = hashlib.sha256()
    for name in FILES:
        path = root / name
        if not path.is_file():
            raise ValueError(f"В папке ESpeech отсутствует {name}")
        digest.update(name.encode())
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def reference_for(path, transcript):
    """Use an explicitly paired short reference when supplied for an older voice."""
    import soundfile as sf
    manifest = path.parent / "espeech-reference.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data["source_sha256"] != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError("Короткий образец ESpeech устарел. Добавьте голос заново")
        path = path.parent / "espeech-reference.wav"
        transcript = data["transcript"]
    info = sf.info(path)
    if not 3 <= info.duration <= 60:
        raise ValueError("Для ESpeech нужен образец от 3 до 60 секунд с точной расшифровкой")
    if not transcript.strip():
        raise ValueError("Для ESpeech нужна точная расшифровка образца")
    return path, transcript


def marked_text(text):
    from .stress import automatic_stress, VOWELS
    text = automatic_stress(text, "ru")
    return re.sub(r"([" + VOWELS + r"])[\u0301\u02ca]", lambda m: "+" + m[1], text)


class ESpeechEngine:
    def __init__(self, directory, device):
        if device not in {"cpu", "cuda:0"}:
            raise ValueError("ESpeech поддерживает CPU и NVIDIA CUDA")
        import torch
        from f5_tts.model import DiT
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        self.device = "cuda" if device == "cuda:0" else "cpu"
        with contextlib.redirect_stdout(sys.stderr):
            self.model = load_model(DiT, dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4),
                                    str(directory / FILES[0]), vocab_file=str(directory / FILES[1]), device=self.device)
            self.vocoder = load_vocoder(is_local=True, local_path=str(directory / "vocos"), device=self.device)

    def synthesize(self, text, language, reference, transcript, output):
        import soundfile as sf
        import torch
        from f5_tts.infer.utils_infer import infer_batch_process
        if language != "ru":
            raise ValueError("ESpeech используется для русского языка")
        audio, rate = sf.read(reference, dtype="float32", always_2d=True)
        tensor = torch.from_numpy(audio.mean(axis=1).copy()).unsqueeze(0)
        # Array input avoids TorchCodec and preserves the exact reference/transcript pair.
        import numpy as np
        from .books import chunk_text

        marked_reference = marked_text(transcript)
        reference_seconds = tensor.shape[-1] / rate
        # F5 estimates output duration from the ratio of UTF-8 text lengths.
        bytes_per_second = len(marked_reference.encode("utf-8")) / reference_seconds
        limit = max(20, min(len(text), int(8 * bytes_per_second / 3)))
        pieces = []
        with contextlib.redirect_stdout(sys.stderr):
            for part in chunk_text(text, limit):
                marked_part = marked_text(part)
                fix_duration = None
                if reference_seconds > 12:
                    # Keep reference plus output inside the model's positional window.
                    part_bytes = len(marked_part.encode("utf-8"))
                    speed = 0.3 if part_bytes < 10 else 1.0
                    estimated_seconds = part_bytes / (bytes_per_second * speed)
                    fix_duration = reference_seconds + min(16, estimated_seconds)
                wav, output_rate, _ = next(infer_batch_process(
                    (tensor, rate), marked_reference, [marked_part],
                    self.model, self.vocoder, device=self.device, nfe_step=48, fix_duration=fix_duration))
                if wav is None or len(wav) == 0:
                    raise ValueError("ESpeech не создала аудио")
                pieces.append(wav)
        sf.write(output, np.concatenate(pieces), output_rate, subtype="PCM_16")
