import pytest
from client.engine.cuda_graphs import require_complete
from client.engine.tts import select_backend


def test_incomplete_generation_is_never_accepted():
    require_complete(210, 250)
    for count, prefill in [(0, 250), (512, 250), (300, 724)]:
        with pytest.raises(ValueError):
            require_complete(count, prefill)


def test_backend_requires_cuda_and_installed_module(monkeypatch):
    monkeypatch.setattr('client.engine.tts.importlib.util.find_spec', lambda name: None)
    assert select_backend('cuda:0', 'auto') == 'standard'
    with pytest.raises(ValueError):
        select_backend('cuda:0', 'cuda_graphs')
    monkeypatch.setattr('client.engine.tts.importlib.util.find_spec', lambda name: object())
    assert select_backend('cuda:0', 'auto') == 'cuda_graphs'
    assert select_backend('cpu', 'auto') == 'standard'
    with pytest.raises(ValueError):
        select_backend('cpu', 'cuda_graphs')


def test_audio_cache_is_separate_for_each_backend(tmp_path):
    from client.engine.queue import RenderQueue
    queue=RenderQueue(tmp_path)
    common=dict(model_revision='same-weights',voice_hash='same-voice',language='ru')
    standard=queue.enqueue(['Один текст'],dict(common,backend='standard'))
    fast=queue.enqueue(['Один текст'],dict(common,backend='cuda_graphs'))
    assert queue.state(standard)['chunks'][0]['cache_key'] != queue.state(fast)['chunks'][0]['cache_key']
