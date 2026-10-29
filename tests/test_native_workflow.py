import json
import shutil
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from client.engine.api import create_app
from client.engine.export import export_mp3


def test_short_preview_keeps_book_job_separate(tmp_path):
    app = create_app(tmp_path, 't' * 32)
    api = TestClient(app, headers={'Authorization': 'Bearer ' + 't' * 32})
    sample = Path(__file__).resolve().parents[1] / 'samples/night-keeper.fb2'
    project = api.post('/import', files={'file': (sample.name, sample.read_bytes())}).json()['id']
    studio = app.state.studio
    studio.license_client = SimpleNamespace(authorize=lambda: None)
    studio.settings = {'model_directory': 'fixture', 'model_revision': 'fixture-revision'}
    studio.voices.load = lambda _: (tmp_path/'voice.wav', {'sha256': 'hash', 'transcript': 'текст'})
    preview = api.post(f'/projects/{project}/render', json={'voice_id': 'voice', 'preview': True}).json()['id']
    state = studio.queue.state(preview)
    assert len(state['chunks']) == 1
    assert json.loads(state['spec'])['preview'] is True
    assert api.get('/projects').json()[0]['render'] is None
    full = api.post(f'/projects/{project}/render', json={'voice_id': 'voice'}).json()['id']
    assert len(studio.queue.state(full)['chunks']) > 1
    assert api.get('/projects').json()[0]['render']['id'] == full
    chapter_job = api.post(f'/projects/{project}/render', json={'voice_id': 'voice', 'chapter': 0}).json()['id']
    jobs = {j['id']: j for j in api.get('/jobs').json()}
    assert jobs[chapter_job]['scope'] == 'chapter'
    assert jobs[full]['scope'] == 'book'
    assert api.get('/projects').json()[0]['render']['id'] == full
    assert api.post(f'/jobs/{preview}/export-mp3', json={'path': str(tmp_path/'out.mp3')}).status_code == 400
    assert api.get(f'/jobs/{full}/playback').json() == {'files': []}


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg unavailable')
def test_single_mp3_preserves_full_duration_and_existing_file(tmp_path):
    source = tmp_path/'chunk.wav'
    with wave.open(str(source), 'wb') as wav:
        wav.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\0\0' * 24000)
    output = export_mp3([source, source, source], tmp_path/'Книга.mp3', 'Книга', 'Автор')
    info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(output)]))
    assert 3 <= float(info['format']['duration']) < 3.2
    assert info['format']['tags']['title'] == 'Книга'
    original = output.read_bytes()
    with pytest.raises(ValueError):
        export_mp3([source], output, 'Другая')
    assert output.read_bytes() == original
    assert not list(tmp_path.glob('lbo-mp3-*'))
