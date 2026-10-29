import base64
import hashlib
import json
import os
import threading
import time
import re
import wave
import uuid
from pathlib import Path
from .books import chunk_text, prepare_text, spoken_blocks
from .export import export_book, export_mp3
from .hardware import machine_id
from .licensing import EncryptedVault, LicenseClient
from .queue import RenderQueue
from .tts import QwenEngine, LANGUAGES, select_backend
from .voices import VoiceLibrary
from .denoise import reduce_noise, REVISION as AUDIO_REVISION
from .numbers import REVISION as TEXT_REVISION
from .stress import REVISION as STRESS_REVISION, AUTO_REVISION


class Studio:
    def __init__(self, root, library):
        self.root, self.library = root, library
        self.voices = VoiceLibrary(root / 'voices')
        self.queue = RenderQueue(root / 'render')
        self.settings_path = root / 'settings.json'
        self.settings = json.loads(self.settings_path.read_text(encoding='utf-8')) if self.settings_path.exists() else {}
        self.license_client = None
        self.engine = None
        self.engine_key = None
        self.stop = threading.Event()
        self.thread = None
        self.guard = threading.RLock()
        self.active_job = None

    def license(self):
        with self.guard:
            if self.license_client is None:
                server, public = os.getenv('BOOKS_LICENSE_URL'), os.getenv('BOOKS_LICENSE_PUBLIC_KEY')
                if not server or not public:
                    raise ValueError('Сервер лицензий ещё не настроен в этой сборке. Импорт и редактирование доступны')
                machine = machine_id()
                self.license_client = LicenseClient(server, public, machine, EncryptedVault(self.root/'license.enc', machine))
            return self.license_client

    def license_status(self):
        try:
            return self.license().status()
        except (ValueError, OSError) as exc:
            return {'valid':False,'message':str(exc)}

    @staticmethod
    def fingerprint(directory):
        path = Path(directory).expanduser().resolve()
        if not (path/'config.json').is_file():
            raise ValueError('В выбранной папке нет config.json модели')
        # Content digest prevents stale audio cache after replacing a checkpoint in-place.
        digest = hashlib.sha256()
        # Interrupted/resumed downloads can leave staging files beside verified weights.
        # Those files are never loaded by Qwen and must not invalidate cached audio.
        files = sorted(p for p in path.rglob('*') if p.is_file()
                       and '.cache' not in p.parts
                       and not any(part.endswith(('.ranges', '.parts')) for part in p.relative_to(path).parts)
                       and p.suffix not in {'.part', '.assembled'})
        if not any(p.suffix == '.safetensors' for p in files):
            raise ValueError('В папке нет весов safetensors')
        for file in files:
            digest.update(file.relative_to(path).as_posix().encode())
            with file.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):
                    digest.update(chunk)
        return digest.hexdigest()

    def verify_model(self, spec):
        if self.fingerprint(spec['model_directory']) != spec['model_revision']:
            raise ValueError('Файлы модели изменились. Проверьте модель в настройках и создайте новую задачу')

    def configure(self, directory: str, device: str, backend: str = 'auto'):
        if device not in {'cpu','cuda:0','mps'}:
            raise ValueError('Неизвестное устройство')
        path = Path(directory).expanduser().resolve()
        selected = select_backend(device, backend)
        settings = {'model_directory':str(path),'model_revision':self.fingerprint(path),'device':device,
                    'backend':selected,'engine_revision':'cuda-graphs-0.3.2-v1' if selected=='cuda_graphs' else 'qwen-0.1.1-v2'}
        with self.guard:
            if self.active_job or any(j['status']=='running' for j in self.queue.summaries()):
                raise ValueError('Сначала приостановите текущую озвучку')
            self.settings = settings
            temp = self.settings_path.with_suffix('.tmp')
            temp.write_text(json.dumps(settings),encoding='utf-8')
            temp.replace(self.settings_path)
        return settings

    def enqueue(self, identifier, voice_id, options, chapter_index=None, denoise=True, preview=False):
        self.license().authorize()
        if not self.settings.get('model_directory'):
            raise ValueError('Укажите локальную папку модели в настройках')
        reference, voice = self.voices.load(voice_id)
        book = self.library.load(identifier)
        book.preparation=options
        self.library.save(book,identifier)
        chapters = list(enumerate(book.chapters))
        if chapter_index is not None:
            if not 0 <= chapter_index < len(chapters): raise ValueError('Глава не найдена')
            chapters = [chapters[chapter_index]]
        options = {**options, 'dictionary': {**self.pronunciation_rules(), **options.get('dictionary', {})}}
        texts, slices = [], []
        for index, chapter in chapters:
            if chapter.language not in LANGUAGES:
                raise ValueError(f'Выберите поддерживаемый язык для главы «{chapter.title}»')
            text = prepare_text(spoken_blocks(book,chapter.start,chapter.end), language=chapter.language, **{**options, 'auto_stress': False})
            parts = chunk_text(text)
            if preview:
                parts = parts[:1]
            if not parts:
                raise ValueError(f'В главе «{chapter.title}» нет текста для озвучки')
            slices.append({'title':chapter.title,'start':len(texts),'end':len(texts)+len(parts),'language':chapter.language})
            texts.extend(parts)
            if preview:
                break
        spec = {**self.settings,'project_id':identifier,'title':book.title,'author':book.author,
                'cover_id':book.cover_id,'denoise':denoise,'audio_revision':AUDIO_REVISION if denoise else 'raw',
                'text_revision':TEXT_REVISION,'stress_revision':STRESS_REVISION,'auto_stress_revision':AUTO_REVISION if options.get('auto_stress') else None,'auto_stress':bool(options.get('auto_stress')),'preview':preview,'chapter_index':chapter_index,
                'voice_id':voice_id,'voice_hash':voice['sha256'],'transcript':voice['transcript'],
                'language':chapters[0][1].language,'chapters':slices}
        return self.queue.enqueue(texts,spec)

    def pronunciation_rules(self):
        path = self.root/'pronunciation.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}

    def save_pronunciation_rules(self, rules):
        if len(rules)>500 or any(not k.strip() or not v.strip() or len(k)>200 or len(v)>300 for k,v in rules.items()):
            raise ValueError('Не более 500 непустых правил: фраза до 200, замена до 300 символов')
        with self.guard:
            path=self.root/'pronunciation.json'
            temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(rules,ensure_ascii=False,indent=2),encoding='utf-8')
            temp.replace(path)
        return rules

    def repair_state(self, identifier):
        parent=self.queue.state(identifier)
        if json.loads(parent['spec']).get('repair_parent'):
            raise ValueError('Откройте исходную аудиокнигу')
        takes=[]
        for job in reversed(self.queue.summaries()):
            spec=json.loads(job['spec'])
            if spec.get('repair_parent')==identifier:
                chunk=self.queue.state(job['id'])['chunks'][0]
                takes.append({'id':job['id'],'position':spec['repair_position'],
                              'original':bool(spec.get('repair_original')),'text':chunk['text'],
                              'cache_key':chunk['cache_key'],'status':job['status'],'error':job['error']})
        return {'id':identifier,'chunks':parent['chunks'],'takes':takes}

    def create_takes(self, identifier, position, text, count=3):
        self.license().authorize()
        if not text.strip() or len(text)>2000 or not 1<=count<=5:
            raise ValueError('Укажите текст до 2000 символов и от 1 до 5 дублей')
        with self.guard:
            parent=self.queue.state(identifier)
            parent_spec=json.loads(parent['spec'])
            if parent['status']!='done' or parent_spec.get('repair_parent'):
                raise ValueError('Выберите готовую аудиокнигу')
            if not 0<=position<len(parent['chunks']): raise ValueError('Фрагмент не найден')
            chunk=parent['chunks'][position]
            if not self.queue.audio_duration(self.queue.cache/(chunk['cache_key']+'.wav')):
                raise ValueError('Исходный звук отсутствует или повреждён')
            if chunk['text'].startswith('[[pause:'): raise ValueError('Выберите фрагмент речи, а не паузу')
            spec={**self.queue.chunk_spec(parent_spec,position),'preview':True,'auto_stress':False,
                  'chapters':[],'repair_parent':identifier,'repair_position':position}
            # Keep the original as a completed take so it survives restarts and can be restored.
            if not any(t['position']==position and t['original'] for t in self.repair_state(identifier)['takes']):
                original=self.queue.enqueue([chunk['text']],{**spec,'repair_original':True})
                with self.queue.connect() as db:
                    db.execute('UPDATE chunks SET cache_key=?,done=1,seconds=?,audio_seconds=? WHERE job=?',
                               (chunk['cache_key'],chunk['seconds'],chunk['audio_seconds'],original))
                    db.execute("UPDATE jobs SET status='done' WHERE id=?",(original,))
            for _ in range(count):
                self.queue.enqueue([text],{**spec,'take_nonce':str(uuid.uuid4())})
        return self.repair_state(identifier)

    def apply_take(self, identifier, position, take, expected_key):
        with self.guard:
            self.queue.replace_chunk(identifier,position,take,expected_key)
        return self.repair_state(identifier)

    def render(self, text, spec, output):
        pause=re.fullmatch(r'\[\[pause:(\d+)\]\]',text)
        if pause:
            milliseconds=int(pause[1])
            if not 50<=milliseconds<=10000:raise ValueError('Недопустимая пауза')
            with wave.open(str(output),'wb') as wav:
                wav.setparams((1,2,24000,0,'NONE','not compressed'))
                wav.writeframes(b'\0\0'*(24*milliseconds))
            return
        reference, voice = self.voices.load(spec['voice_id'])
        if hashlib.sha256(reference.read_bytes()).hexdigest() != spec['voice_hash']:
            raise ValueError('Образец голоса изменён. Создайте новую задачу')
        backend = spec.get('backend', 'standard')
        key = (spec['model_directory'],spec['model_revision'],spec['device'],backend)
        if key != self.engine_key:
            self.engine = QwenEngine(Path(spec['model_directory']),spec['device'],backend=backend)
            self.engine_key = key
        if spec.get('denoise', False):
            cleaned = reference.parent / ('reference-clean-' + AUDIO_REVISION + '-' + spec['voice_hash'][:16] + '.wav')
            if not self.queue.audio_duration(cleaned):
                reduce_noise(reference, cleaned)
            reference = cleaned
        if spec.get('auto_stress', False):
            from .stress import automatic_stress
            text = automatic_stress(text, spec['language'])
        self.engine.synthesize(text,spec['language'],reference,spec['transcript'],output)
        if spec.get('denoise', False):
            reduce_noise(output, output)

    def start(self):
        self.worker_lock = (self.root/'worker.lock').open('a+b')
        self.worker_lock.seek(0)
        self.worker_lock.write(b'0')
        self.worker_lock.flush()
        self.worker_lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.worker_lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.worker_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.worker_lock.close()
            raise RuntimeError('Эта библиотека уже открыта другим обработчиком')
        # One service is owned by the desktop sidecar. No automatic resume after restart.
        self.queue.recover()
        def work():
            try:
                self.license().authorize(force_check=True)
            except Exception:
                pass
            next_check=time.monotonic()+60
            while not self.stop.is_set():
                if time.monotonic()>=next_check:
                    try:self.license().authorize()
                    except Exception:pass
                    next_check=time.monotonic()+60
                queued = [j for j in reversed(self.queue.summaries()) if j['status']=='queued']
                if not queued:
                    self.stop.wait(.5)
                    continue
                job = queued[0]
                try:
                    with self.guard:
                        if self.queue.status(job['id'])!='queued':continue
                        self.active_job=job['id']
                    self.queue.run(job['id'],self.render,lambda:self.license().authorize(),self.verify_model)
                except Exception:
                    # The queue persists the diagnostic for the UI; keep the worker alive.
                    pass
                finally:
                    with self.guard:self.active_job=None
        self.thread = threading.Thread(target=work,daemon=True,name='book-render')
        self.thread.start()

    def close(self):
        self.stop.set()
        for job in self.queue.summaries():
            self.queue.pause(job['id'])
        if self.thread:
            self.thread.join(timeout=2)
        if hasattr(self,'worker_lock') and (not self.thread or not self.thread.is_alive()):
            self.worker_lock.close()

    def jobs(self):
        result=[]
        for job in self.queue.summaries():
            spec=json.loads(job['spec'])
            rtf=job['compute_seconds']/job['measured_audio_seconds'] if job['measured_audio_seconds'] else None
            chapter_count = len(spec.get('chapters', []))
            try:
                book_chapters = len(self.library.load(spec['project_id']).chapters)
            except (KeyError, ValueError):
                book_chapters = chapter_count
            # Older jobs did not record the explicit chapter choice.
            scope = 'chapter' if spec.get('chapter_index') is not None or chapter_count < book_chapters else 'book'
            result.append({'id':job['id'],'project_id':spec.get('project_id'),'title':spec['title'],'status':job['status'],'error':job['error'],
                           'voice_id':spec.get('voice_id'),'repair_parent':spec.get('repair_parent'),'preview':spec.get('preview',False),'scope':scope,
                           'chapter_titles':[c['title'] for c in spec.get('chapters',[])],
                           'completed':job['completed'],'total':job['total'],'audio_seconds':job['audio_seconds'],'compute_seconds':job['compute_seconds'],'rtf':rtf})
        return result

    def projects(self):
        latest={}
        for job in self.jobs():
            if not job['preview'] and job['scope'] == 'book':
                latest.setdefault(job['project_id'],job)
        result=[]
        for project in self.library.list():
            book=self.library.load(project['id'])
            result.append({**project,'author':book.author,'cover_id':book.cover_id,'render':latest.get(project['id'])})
        return result

    def delete_project(self, identifier):
        with self.guard:
            self.library.load(identifier)
            jobs=[job for job in self.jobs() if job['project_id']==identifier]
            if any(job['id']==self.active_job or job['status']=='running' for job in jobs):
                raise ValueError('Сначала приостановите озвучку и дождитесь окончания текущего фрагмента')
            for job in jobs:self.queue.delete(job['id'])
            self.library.delete(identifier)

    def export(self, identifier, destination):
        job=self.queue.state(identifier)
        if job['status']!='done':raise ValueError('Дождитесь завершения всех фрагментов')
        spec=json.loads(job['spec'])
        chapters=[]
        for chapter in spec['chapters']:
            paths=[self.queue.cache/(c['cache_key']+'.wav') for c in job['chunks'][chapter['start']:chapter['end']]]
            chapters.append((chapter['title'],paths))
        cover=self.root/'covers'/(spec['cover_id']+'.jpg') if spec.get('cover_id') else None
        return [str(p) for p in export_book(chapters,Path(destination).expanduser().resolve(),spec['title'],spec['author'],cover)]

    def export_single_mp3(self, identifier, destination):
        job = self.queue.state(identifier)
        if job['status'] != 'done':
            raise ValueError('Дождитесь завершения всех фрагментов')
        spec = json.loads(job['spec'])
        paths = [self.queue.cache / (c['cache_key'] + '.wav') for c in job['chunks']]
        return str(export_mp3(paths, Path(destination).expanduser().resolve(), spec['title'], spec['author']))
