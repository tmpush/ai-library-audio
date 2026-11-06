import hashlib
import json
import wave
from types import SimpleNamespace

import pytest

from client.engine import espeech, stress
from client.engine.books import import_book
from client.engine.service import Studio
from client.engine.storage import Library


def test_espeech_marks_keep_manual_priority(monkeypatch):
    monkeypatch.setattr(stress, '_predict', lambda text: text.replace('замок', 'з+амок'))
    assert espeech.marked_text('замо́к замок з+амок ёлка') == 'зам+ок з+амок з+амок ёлка'


def test_reference_rejects_long_or_changed_pair(tmp_path):
    pytest.importorskip('soundfile')
    ref = tmp_path / 'reference.wav'
    def write(path, seconds):
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            stream.writeframes(b'\0\0' * (16000 * seconds))
    write(ref, 20)
    with pytest.raises(ValueError, match='12 секунд'):
        espeech.reference_for(ref, 'Текст')
    short = tmp_path / 'espeech-reference.wav'
    write(short, 5)
    data = {'source_sha256': hashlib.sha256(ref.read_bytes()).hexdigest(), 'transcript': 'Короткий текст'}
    (tmp_path / 'espeech-reference.json').write_text(json.dumps(data), encoding='utf-8')
    assert espeech.reference_for(ref, 'Старый текст') == (short, 'Короткий текст')
    write(ref, 21)
    with pytest.raises(ValueError, match='устарел'):
        espeech.reference_for(ref, 'Текст')


def test_language_dispatch_and_frozen_reference(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    root.mkdir()
    studio = Studio(root, Library(root))
    studio.license_client = SimpleNamespace(authorize=lambda: None)
    studio.settings = dict(model_directory='qwen', model_revision='q1', device='cpu', espeech_directory='espeech', espeech_revision='e1')
    reference = tmp_path / 'reference.wav'
    reference.write_bytes(b'voice')
    voice = dict(sha256=hashlib.sha256(b'voice').hexdigest(), transcript='Точный текст')
    studio.voices.load = lambda _: (reference, voice)
    monkeypatch.setattr(espeech, 'reference_for', lambda path, text: (path, text))
    book = import_book(__import__('pathlib').Path('samples/night-keeper.fb2'))
    identifier = studio.library.save(book)
    job = studio.enqueue(identifier, 'voice', {}, preview=True, denoise=False)
    spec = json.loads(studio.queue.state(job)['spec'])
    calls = []
    class Fake:
        def __init__(self, path, device, **kwargs):
            calls.append(str(path))
        def synthesize(self, text, language, reference, transcript, output):
            calls.append((language, transcript))
    monkeypatch.setattr(espeech, 'ESpeechEngine', Fake)
    monkeypatch.setattr('client.engine.service.QwenEngine', Fake)
    studio.render('Проба', spec, tmp_path / 'out.wav')
    assert calls == ['espeech', ('ru', 'Точный текст')]
    voice['transcript'] = 'Изменённый текст'
    with pytest.raises(ValueError, match='изменён'):
        studio.render('Проба', spec, tmp_path / 'out.wav')
    # English and existing jobs without ESpeech remain on Qwen.
    studio.render('Hello', {**spec, 'language': 'en'}, tmp_path / 'out.wav')
    assert calls[-2:] == ['qwen', ('en', 'Точный текст')]
    legacy = {k: v for k, v in spec.items() if not k.startswith('espeech_')}
    studio.render('Проба', legacy, tmp_path / 'out.wav')
    assert calls[-1] == ('ru', 'Точный текст')
