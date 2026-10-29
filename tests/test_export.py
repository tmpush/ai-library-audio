import json
import shutil
import subprocess
import wave
import pytest
from client.engine.export import export_book
from PIL import Image


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg/ffprobe not installed')
def test_m4b_chapters_and_metadata(tmp_path):
    wav=tmp_path/'sample.wav'
    with wave.open(str(wav),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'))
        out.writeframes(b'\0\0'*16000)
    cover=tmp_path/'cover.jpg'
    Image.new('RGB',(300,400),'navy').save(cover)
    files=export_book([('Глава 1',[wav]),('Глава 2',[wav,wav])],tmp_path/'export','Тестовая книга','Автор',cover)
    assert all(f.stat().st_size>0 for f in files)
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_chapters','-show_format','-show_streams','-of','json',str(files[-1])]))
    assert len(info['chapters'])==2
    assert info['chapters'][1]['tags']['title']=='Глава 2'
    assert float(info['chapters'][1]['start_time'])==1
    assert float(info['chapters'][1]['end_time'])==3
    assert info['format']['tags']['title']=='Тестовая книга'
    assert any(stream.get('disposition',{}).get('attached_pic')==1 for stream in info['streams'])
    with pytest.raises(ValueError):
        export_book([('Глава 1',[wav])],tmp_path/'export','Книга')
