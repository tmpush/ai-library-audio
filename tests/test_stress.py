from client.engine.stress import speech_stress
from client.engine.books import Block, prepare_text


def test_explicit_stress_and_languages():
    assert speech_stress('за́мок замо́к') == 'заˊмок замоˊк'
    assert speech_stress('з+амок') == 'заˊмок'
    assert speech_stress('ёлка, 2+2, https://example.com') == 'ёлка, 2+2, https://example.com'
    assert speech_stress('café', 'fr') == 'café'


def test_dictionary_preserves_source_and_accents():
    blocks = [Block('Смотритель открыл окно.')]
    text = prepare_text(blocks, dictionary={'Смотритель': 'Смотри́тель'})
    assert text == 'Смотри́тель открыл окно.'
    assert blocks[0].text == 'Смотритель открыл окно.'


def test_auto_preserves_manual_marks_punctuation_and_source(monkeypatch):
    from client.engine import stress
    seen = []
    def predict(text):
        seen.append(text)
        return text.replace('замок', 'з+амок').replace('смотрителя', 'смотр+ителя')
    monkeypatch.setattr(stress, '_predict', predict)
    source = 'замо́к, з+амок; смотрителя! ёлка. https://пример.рф [[pause:500]]'
    assert stress.automatic_stress(source) == 'замо́к, з+амок; смотри́теля! ёлка. https://пример.рф [[pause:500]]'
    assert all('https' not in t and 'pause' not in t for t in seen)
    assert stress.automatic_stress(source, 'en') == source


def test_auto_rejects_rewritten_or_truncated_predictions(monkeypatch):
    from client.engine import stress
    for prediction in ['совсем др+угой текст', 'смотр+итель', 'смотритель открыл окн+о']:
        monkeypatch.setattr(stress, '_predict', lambda text: prediction)
        assert stress.automatic_stress('смотритель закрыл окно') == 'смотритель закрыл окно'


def test_auto_bounds_long_book_and_manual_dictionary_wins(monkeypatch):
    from client.engine import stress
    def predict(text):
        assert len(text) <= 140
        return text.replace('замок', 'з+амок')
    monkeypatch.setattr(stress, '_predict', predict)
    text = ('замок ' * 3000).strip()
    assert stress.automatic_stress(text) == ('за́мок ' * 3000).strip()
    blocks = [Block('замок')]
    assert prepare_text(blocks, auto_stress=True, dictionary={'замок': 'замо́к'}) == 'замо́к'
    assert blocks[0].text == 'замок'
    monkeypatch.setattr(stress, '_predict', lambda _: (_ for _ in ()).throw(AssertionError('disabled')))
    assert prepare_text(blocks, auto_stress=False) == 'замок'


def test_auto_runs_in_worker_not_enqueue(tmp_path, monkeypatch):
    import hashlib
    import json
    from types import SimpleNamespace
    from client.engine.api import create_app
    from client.engine import stress
    from client.engine.books import import_book
    app = create_app(tmp_path / 'data', 't' * 32)
    studio = app.state.studio
    studio.license_client = SimpleNamespace(authorize=lambda: None)
    studio.settings = {'model_directory': 'fixture', 'model_revision': 'v1', 'device': 'cpu'}
    reference = tmp_path / 'voice.wav'
    reference.write_bytes(b'fixture')
    studio.voices.load = lambda _: (reference, {'sha256': hashlib.sha256(b'fixture').hexdigest(), 'transcript': 'текст'})
    book = import_book(__import__('pathlib').Path('samples/night-keeper.fb2'))
    identifier = studio.library.save(book)
    calls = []
    monkeypatch.setattr(stress, 'automatic_stress', lambda text, language: calls.append(text) or 'смотри́тель')
    job = studio.enqueue(identifier, 'voice', {'auto_stress': True}, denoise=False)
    assert calls == []
    spec = json.loads(studio.queue.state(job)['spec'])
    studio.engine_key = ('fixture', 'v1', 'cpu', 'standard')
    delivered = []
    studio.engine = SimpleNamespace(synthesize=lambda text, *args: delivered.append(text))
    studio.render('смотритель', spec, tmp_path / 'out.wav')
    assert calls == ['смотритель']
    assert delivered == ['смотри́тель']


def test_auto_normalizes_model_whitespace_but_preserves_paragraphs(monkeypatch):
    from client.engine import stress
    def predict(text):
        assert text == 'смотритель замок'
        return 'смотр+итель з+амок'
    monkeypatch.setattr(stress, '_predict', predict)
    assert stress.automatic_stress('смотритель\n\nзамок') == 'смотри́тель\n\nза́мок'
