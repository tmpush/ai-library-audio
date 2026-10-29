import json
import os
import sys
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


def ffmpeg_path():
    # PyInstaller puts optional packaged utilities under its extraction directory.
    binary='ffmpeg.exe' if os.name=='nt' else 'ffmpeg'
    bundled=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]))/'tools'/binary
    return str(bundled) if bundled.is_file() else shutil.which('ffmpeg')


def encode(command):
    try:
        subprocess.run(command,check=True,capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.CalledProcessError as exc:
        message=exc.stderr.decode('utf-8',errors='replace')[-2000:]
        raise ValueError('Ошибка экспорта FFmpeg: '+message) from exc


def join_wavs(inputs: list[Path], output: Path):
    if not inputs:
        raise ValueError("Нет готовых фрагментов")
    expected = None
    with wave.open(str(output), "wb") as target:
        for path in inputs:
            with wave.open(str(path), "rb") as source:
                params = source.getparams()[:3]
                if expected is None:
                    expected = params
                    target.setnchannels(params[0]); target.setsampwidth(params[1]); target.setframerate(params[2])
                if params != expected:
                    raise ValueError("У фрагментов разные параметры звука")
                while data := source.readframes(65536):
                    target.writeframesraw(data)


def escape_metadata(value):
    return str(value).replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\r", " ").replace("\n", " ")


def export_mp3(inputs: list[Path], destination: Path, title: str, author: str = ''):
    """One complete MP3, published only after a successful encode; never overwrite."""
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise ValueError('Для экспорта требуется локальный FFmpeg')
    if destination.suffix.lower() != '.mp3':
        raise ValueError('Выберите файл с расширением .mp3')
    if destination.exists():
        raise ValueError('Такой файл уже существует. Укажите другое имя')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='lbo-mp3-', dir=destination.parent) as tempdir:
        temp = Path(tempdir)
        joined = temp / 'book.wav'
        join_wavs(inputs, joined)
        output = temp / 'book.mp3'
        encode([ffmpeg, '-nostdin', '-v', 'error', '-i', str(joined),
                '-c:a', 'libmp3lame', '-b:a', '128k', '-metadata', 'title='+title,
                '-metadata', 'artist='+author, str(output)])
        # Same-volume hard link atomically refuses an existing name (also on NTFS).
        os.link(output, destination)
    return destination


def export_book(chapters: list[tuple[str, list[Path]]], destination: Path, title: str, author: str = "", cover: Path | None = None):
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("FFmpeg не найден. Для экспорта требуется локальный FFmpeg")
    if not chapters or any(not files for _, files in chapters):
        raise ValueError("Все главы должны содержать готовое аудио")
    destination.mkdir(parents=True, exist_ok=True)
    outputs = [destination / f"{i+1:03d}.mp3" for i in range(len(chapters))] + [destination / "audiobook.m4b"]
    if any(p.exists() for p in outputs):
        raise ValueError("В папке уже есть файлы экспорта. Выберите новую папку")
    with tempfile.TemporaryDirectory(prefix="lbo-export-", dir=destination) as tempdir:
        temp = Path(tempdir)
        wavs = []
        metadata = [";FFMETADATA1", "title=" + escape_metadata(title), "artist=" + escape_metadata(author)]
        elapsed_ms = 0
        for i, (name, chunks) in enumerate(chapters):
            wav = temp / f"{i}.wav"
            join_wavs(chunks, wav)
            wavs.append(wav)
            with wave.open(str(wav), "rb") as source:
                duration_ms = round(source.getnframes() * 1000 / source.getframerate())
            metadata.extend(["[CHAPTER]", "TIMEBASE=1/1000", f"START={elapsed_ms}", f"END={elapsed_ms+duration_ms}", "title="+escape_metadata(name)])
            elapsed_ms += duration_ms
            encode([ffmpeg,"-nostdin","-v","error","-i",str(wav),"-c:a","libmp3lame","-b:a","128k","-metadata","title="+name,"-metadata","album="+title,"-metadata","artist="+author,str(temp / outputs[i].name)])
        full = temp / "full.wav"
        join_wavs(wavs, full)
        meta = temp / "metadata.txt"
        meta.write_text("\n".join(metadata), encoding="utf-8")
        command = [ffmpeg,"-nostdin","-v","error","-i",str(full),"-f","ffmetadata","-i",str(meta)]
        if cover:
            command += ["-i", str(cover), "-map", "0:a", "-map", "2:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
        else:
            command += ["-map", "0:a"]
        command += ["-map_metadata","1","-map_chapters","1","-c:a","aac","-b:a","128k","-movflags","+faststart",str(temp / "audiobook.m4b")]
        encode(command)
        for output in outputs:
            (temp / output.name).replace(output)
    return outputs
