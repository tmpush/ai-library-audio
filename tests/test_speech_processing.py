import hashlib
import json
import wave
import pytest
from client.engine.numbers import normalize_numbers
from client.engine.denoise import reduce_noise
from client.engine.books import Block, prepare_text, chunk_text
from client.engine.export import ffmpeg_path


@pytest.mark.parametrize('source,expected', [
    ('Глава 1. Их 12.', 'Глава первая. Их двенадцать.'),
    ('В 2026 году', 'В две тысячи двадцать шестом году'),
    ('29.10.2026', 'двадцать девятое октября две тысячи двадцать шестого года'),
    ('09:05', 'девять часов пять минут'),
    ('21:21', 'двадцать один час двадцать одна минута'),
    ('1 250,50 ₽', 'одна тысяча двести пятьдесят рублей, пятьдесят копеек'),
    ('21%', 'двадцать один процент'),
    ('3,5%', 'три целых пять десятых процента'),
    ('2/3', 'две третьих'),
    ('-5; +2; −12', 'минус пять; плюс два; минус двенадцать'),
    ('3,14; 1.25', 'три целых четырнадцать сотых; одна целая двадцать пять сотых'),
    ('21-й; 2-я; 3-го', 'двадцать первый; вторая; третьего'),
    ('2 км; 5 кг', 'два километра; пять килограммов'),
    ('10–12; 001', 'десять — двенадцать; ноль ноль один'),
    ('31.02.2026; 25:99; v1.2.3; A320; https://test.com/12; [[pause:500]]',
     '31.02.2026; 25:99; v1.2.3; A320; https://test.com/12; [[pause:500]]'),
])
def test_number_contexts(source, expected):
    assert normalize_numbers(source) == expected


def test_normalization_before_chunks_preserves_source_and_pause():
    blocks = [Block('Глава 1. В 2026 году было 12 книг. [[pause:500]]')]
    original = blocks[0].text
    spoken = prepare_text(blocks, numbers=True)
    assert blocks[0].text == original
    assert '[[pause:500]]' in chunk_text(spoken)
    assert prepare_text(blocks, numbers=False) == original


@pytest.mark.skipif(not ffmpeg_path(), reason='FFmpeg required')
def test_noise_reduction_preserves_reference_and_reduces_stationary_noise(tmp_path):
    np = pytest.importorskip('numpy')
    rate = 24000
    t = np.arange(rate * 12) / rate
    rng = np.random.default_rng(24)
    envelope = ((t % 3 > .7) & (t % 3 < 2.5)).astype(float)
    signal = .15 * np.sin(2*np.pi*220*t)*envelope
    noisy = signal + rng.normal(0,.005,len(t)) + .003*np.sin(2*np.pi*50*t)
    source, target = tmp_path/'original.wav', tmp_path/'clean.wav'
    with wave.open(str(source),'wb') as wav:
        wav.setparams((1,2,rate,0,'NONE','not compressed'))
        wav.writeframes((noisy*32767).astype('<i2').tobytes())
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    reduce_noise(source,target)
    with wave.open(str(target),'rb') as wav:
        assert wav.getnframes() == len(t) and wav.getframerate() == rate
        cleaned=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2') / 32768
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
    quiet=(t%3>.2)&(t%3<.5)
    assert np.sqrt(np.mean(cleaned[quiet]**2)) < np.sqrt(np.mean(noisy[quiet]**2))*.65
    voiced=(t%3>1)&(t%3<2)
    assert np.sqrt(np.mean(cleaned[voiced]**2)) > np.sqrt(np.mean(signal[voiced]**2))*.8
    # The voiced envelope must not acquire afftdn's 25 ms buffering delay.
    onset = int(.7*rate)
    assert np.sqrt(np.mean(cleaned[onset+100:onset+400]**2)) > .05


def test_processing_options_are_in_cache_identity(tmp_path):
    from client.engine.queue import RenderQueue
    q=RenderQueue(tmp_path)
    base=dict(model_revision='r',voice_hash='v',language='ru')
    a=q.enqueue(['один'],{**base,'denoise':False})
    b=q.enqueue(['один'],{**base,'denoise':True,'audio_revision':'v1'})
    assert q.state(a)['chunks'][0]['cache_key'] != q.state(b)['chunks'][0]['cache_key']


def test_render_defaults():
    from client.engine.api import Render, Prepare
    assert Render(voice_id='test').denoise and Render(voice_id='test').numbers
    assert Prepare(chapter=0).numbers
