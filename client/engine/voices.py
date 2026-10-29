import audioop
import hashlib
import json
import shutil
import uuid
import wave
from pathlib import Path


def inspect_voice(path: Path):
    if path.stat().st_size > 30 * 1024 * 1024:
        raise ValueError("Образец превышает 30 МБ")
    try:
        with wave.open(str(path), "rb") as wav:
            rate, channels, width, frames = wav.getframerate(), wav.getnchannels(), wav.getsampwidth(), wav.getnframes()
            if rate < 16000 or channels not in (1, 2) or width != 2:
                raise ValueError("Нужен WAV PCM 16-bit, моно или стерео, частота не ниже 16 кГц")
            duration = frames / rate
            if not 10 <= duration <= 30:
                raise ValueError("Длительность образца должна быть от 10 до 30 секунд")
            data = wav.readframes(frames)
            if len(data) != frames * channels * width:
                raise ValueError("WAV обрезан")
    except (wave.Error,EOFError) as exc:
        raise ValueError("Нужен несжатый WAV PCM") from exc
    # Per-channel RMS avoids falsely rejecting stereo clips with opposing phase.
    window = max(1, rate // 50) * channels * width
    rms = [audioop.rms(data[i:i + window], width) for i in range(0, len(data), window)]
    silence = sum(v < 100 for v in rms) / max(1, len(rms))
    if silence > .8:
        raise ValueError("В образце слишком много тишины. Запишите непрерывную речь")
    return {"duration": duration, "sample_rate": rate, "channels": channels, "silence_fraction": silence,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


class VoiceLibrary:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def add(self, path: Path, name: str, transcript: str = ""):
        if not name.strip():
            raise ValueError("Укажите название голоса")
        metadata = inspect_voice(path)
        identifier = str(uuid.uuid4())
        directory = self.root / identifier
        directory.mkdir()
        shutil.copyfile(path, directory / "reference.wav")
        metadata.update(id=identifier, name=name.strip(), transcript=transcript.strip())
        (directory / "voice.json").write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
        return metadata

    def list(self):
        return [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*/voice.json")]

    def load(self, identifier):
        identifier = str(uuid.UUID(identifier))
        directory = self.root / identifier
        if not (directory/'reference.wav').is_file() or not (directory/'voice.json').is_file():
            raise ValueError('Профиль голоса не найден. Добавьте образец заново')
        return directory / "reference.wav", json.loads((directory / "voice.json").read_text(encoding="utf-8"))
