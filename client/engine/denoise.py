"""Local, conservative stationary-noise reduction; no model downloads."""
import audioop
import math
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from .export import ffmpeg_path

REVISION = 'fft-gentle-v2'


def reduce_noise(source: Path, destination: Path):
    binary = ffmpeg_path()
    if not binary:
        raise ValueError('Для очистки шума нужен FFmpeg. Установите полную сборку или отключите очистку')
    with wave.open(str(source), 'rb') as wav:
        channels, width, rate, frames = wav.getparams()[:4]
        if width != 2:
            raise ValueError('Для очистки нужен WAV PCM 16-bit')
        data = wav.readframes(frames)
    window = max(1, rate // 50) * channels * width
    levels = sorted(audioop.rms(data[i:i+window], width) / 32768 for i in range(0, len(data), window))
    # Low percentile avoids teaching the filter that sustained vowels are noise.
    # Cap at -40 dBFS to prevent aggressive subtraction on clips without pauses.
    floor = min(-40, max(-70, 20 * math.log10(max(levels[int(len(levels)*.10)] if levels else 0, 1e-6))))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='denoise-', dir=destination.parent) as temp:
        output = Path(temp)/'clean.wav'
        # afftdn buffers two sample advances (rate/80 each). Flush the tail,
        # then remove that latency so it cannot cut a final syllable or add gaps.
        delay = 2 * (rate // 80)
        filters = (f'apad=pad_len={delay},highpass=f=65,afftdn=nr=10:nf={floor:.2f}:tn=0:gs=6,'
                   f'atrim=start_sample={delay}:end_sample={frames+delay},asetpts=PTS-STARTPTS')
        try:
            subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source),
                            '-af', filters, '-ar', str(rate), '-ac', str(channels), '-c:a', 'pcm_s16le', str(output)],
                           check=True, capture_output=True, timeout=90,
                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError('Не удалось очистить шум. Исходная запись сохранена') from exc
        # Exact length keeps chunk timings and reference transcript aligned.
        with wave.open(str(output), 'rb') as wav:
            cleaned = wav.readframes(frames)
        cleaned = (cleaned + b'\0' * max(0, len(data)-len(cleaned)))[:len(data)]
        with wave.open(str(output), 'wb') as wav:
            wav.setparams((channels,width,rate,0,'NONE','not compressed'))
            wav.writeframes(cleaned)
        output.replace(destination)
    return {'revision': REVISION, 'noise_floor_dbfs': round(floor,2), 'reduction_db': 10}
