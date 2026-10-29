"""Authenticated loopback API owned by the desktop sidecar or development process."""
import os
import secrets
import tempfile
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .books import import_book, split_chapter, merge_chapters, prepare_text, chunk_text, spoken_blocks
from .storage import Library
from .service import Studio
from .hardware import inspect_hardware
from .models import model_info, ModelDownload
from .covers import extract_cover, store_cover, MAX_COVER


class Split(BaseModel):
    chapter: int = Field(ge=0)
    at: int = Field(ge=0)
    title: str = Field(min_length=1, max_length=200)

class Prepare(BaseModel):
    chapter: int = Field(ge=0)
    remove_notes: bool = False
    numbers: bool = True
    auto_stress: bool = False
    dictionary: dict[str, str] = Field(default_factory=dict)
    remove_page_furniture: bool = False

class ModelSettings(BaseModel):
    directory: str = Field(min_length=1,max_length=4096)
    device: str = 'cpu'
    backend: str = 'auto'

class Activate(BaseModel):
    key: str = Field(min_length=20,max_length=100)

class Render(BaseModel):
    preview: bool = False
    denoise: bool = True
    voice_id: str
    chapter: int | None = Field(default=None,ge=0)
    remove_notes: bool = False
    numbers: bool = True
    auto_stress: bool = False
    dictionary: dict[str,str] = Field(default_factory=dict)
    remove_page_furniture: bool = False

class TextEdit(BaseModel):
    block: int = Field(ge=0)
    text: str = Field(max_length=100000)
    reset: bool = False

class Metadata(BaseModel):
    title: str = Field(min_length=1,max_length=500)
    author: str = Field(default='',max_length=500)

class Export(BaseModel):
    directory: str = Field(min_length=1,max_length=4096)

class ExportMp3(BaseModel):
    path: str = Field(min_length=1,max_length=4096)

class Language(BaseModel):
    chapter: int = Field(ge=0)
    language: str

class Download(BaseModel):
    directory: str = Field(min_length=1,max_length=4096)
    revision: str = Field(pattern=r'^[0-9a-f]{40}$')
    bytes: int = Field(gt=0)
    confirmed: bool = False


def create_app(directory: Path, token: str, ui_directory: Path | None = None):
    if len(token) < 32:
        raise ValueError("Local API token must be at least 32 characters")
    library = Library(directory)
    studio = Studio(directory,library)
    downloads = ModelDownload()
    @asynccontextmanager
    async def lifespan(app):
        studio.start()
        yield
        studio.close()
    app = FastAPI(title="Local Books Engine", docs_url=None, redoc_url=None,lifespan=lifespan)
    app.state.studio = studio
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://tauri.localhost", "tauri://localhost"], allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        from fastapi.responses import JSONResponse
        preview_asset=ui_directory is not None and (request.url.path=='/preview' or request.url.path.startswith('/preview/'))
        if request.method != "OPTIONS" and not preview_asset and not secrets.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
            return JSONResponse(status_code=401, content={"detail": "Нужен токен локальной сессии"})
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404,content={'detail':'Запись не найдена'})

    @app.exception_handler(httpx.HTTPError)
    async def network_error(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=502,content={'detail':'Не удалось связаться с сервером. Проверьте интернет и повторите попытку'})

    @app.get('/hardware')
    def hardware(): return inspect_hardware()

    @app.get('/settings')
    def settings(): return studio.settings

    @app.get('/runtime')
    def runtime():
        import importlib.util
        from .export import ffmpeg_path
        return {'tts_installed':all(importlib.util.find_spec(name) is not None for name in ['torch','qwen_tts','soundfile']),
                'fast_tts_installed':importlib.util.find_spec('faster_qwen3_tts') is not None,
                'export_available':bool(ffmpeg_path())}

    @app.get('/models/catalog')
    def catalog(): return model_info()

    @app.get('/models/download')
    def download_status():return downloads.snapshot()

    @app.post('/models/download')
    def download(body:Download):return downloads.start(Path(body.directory),body.revision,body.bytes,body.confirmed)

    @app.post('/models/download/cancel')
    def cancel_download():return downloads.cancel()

    @app.post('/settings/model')
    def model(body:ModelSettings): return studio.configure(body.directory,body.device,body.backend)

    @app.get('/license')
    def license_status(): return studio.license_status()

    @app.post('/license/activate')
    def activate(body:Activate): return studio.license().activate(body.key)

    @app.get('/voices')
    def voices(): return studio.voices.list()

    @app.post('/voices')
    def add_voice(name:str=Form(...),transcript:str=Form(''),file:UploadFile=File(...)):
        temp=None
        try:
            with tempfile.NamedTemporaryFile(suffix='.wav',delete=False) as out:
                temp=Path(out.name)
                data=file.file.read(30*1024*1024+1)
                if len(data)>30*1024*1024:raise ValueError('Образец превышает 30 МБ')
                out.write(data)
            return studio.voices.add(temp,name,transcript)
        finally:
            if temp:temp.unlink(missing_ok=True)

    @app.get('/jobs')
    def jobs(): return studio.jobs()

    @app.get('/jobs/{identifier}/chunks')
    def chunks(identifier:str):
        return [{'position':c['position'],'text':c['text'][:150],'done':bool(c['done']),
                 'audio_seconds':c['audio_seconds']} for c in studio.queue.state(identifier)['chunks']]

    @app.post('/projects/{identifier}/render')
    def render(identifier:str,body:Render):
        options=body.model_dump(exclude={'voice_id','chapter','denoise','preview'})
        return {'id':studio.enqueue(identifier,body.voice_id,options,body.chapter,denoise=body.denoise,preview=body.preview)}

    @app.post('/jobs/{identifier}/pause')
    def pause(identifier:str):
        studio.queue.state(identifier)
        studio.queue.pause(identifier)
        return {'ok':True}

    @app.post('/jobs/{identifier}/resume')
    def resume(identifier:str):
        studio.license().authorize()
        studio.queue.state(identifier)
        studio.queue.resume(identifier)
        return {'ok':True}

    @app.post('/jobs/{identifier}/export')
    def export(identifier:str,body:Export):return {'files':studio.export(identifier,body.directory)}

    @app.post('/jobs/{identifier}/export-mp3')
    def export_mp3(identifier:str,body:ExportMp3):
        return {'file':studio.export_single_mp3(identifier,body.path)}

    @app.get('/jobs/{identifier}/playback')
    def playback(identifier:str):
        job=studio.queue.state(identifier)
        return {'files':[str(studio.queue.cache/(c['cache_key']+'.wav')) for c in job['chunks'] if c['done']]}

    @app.get('/voices/{identifier}/playback')
    def voice_playback(identifier:str):
        reference, _ = studio.voices.load(identifier)
        return {'files':[str(reference)]}

    @app.get('/jobs/{identifier}/audio/{position}')
    def audio(identifier:str,position:int):
        from fastapi.responses import FileResponse
        job=studio.queue.state(identifier)
        if not 0<=position<len(job['chunks']) or not job['chunks'][position]['done']:
            raise HTTPException(404,'Фрагмент ещё не готов')
        return FileResponse(studio.queue.cache/(job['chunks'][position]['cache_key']+'.wav'),media_type='audio/wav')

    @app.post('/projects/{identifier}/language')
    def language(identifier:str,body:Language):
        from .tts import LANGUAGES
        book=load(identifier)
        if body.chapter>=len(book.chapters) or body.language not in LANGUAGES:
            raise ValueError('Неизвестная глава или неподдерживаемый язык')
        book.chapters[body.chapter].language=body.language
        library.save(book,identifier)
        return book.to_dict()

    @app.post('/projects/{identifier}/text')
    def edit_text(identifier:str,body:TextEdit):
        book=load(identifier)
        if body.block>=len(book.blocks):raise ValueError('Блок не найден')
        if body.reset:book.spoken_overrides.pop(str(body.block),None)
        else:book.spoken_overrides[str(body.block)]=body.text
        library.save(book,identifier)
        return book.to_dict()

    def load(identifier):
        try:
            return library.load(identifier)
        except KeyError:
            raise HTTPException(404, "Проект не найден")

    @app.get("/projects")
    def projects():
        return studio.projects()

    @app.get("/projects/{identifier}")
    def project(identifier: str):
        return load(identifier).to_dict()

    @app.post('/projects/{identifier}/metadata')
    def metadata(identifier:str,body:Metadata):
        book=load(identifier)
        if not body.title.strip():raise ValueError('Укажите название книги')
        book.title,book.author=body.title.strip(),body.author.strip()
        library.save(book,identifier)
        return book.to_dict()

    @app.post('/projects/{identifier}/cover')
    def cover(identifier:str,file:UploadFile=File(...)):
        book=load(identifier)
        book.cover_id=store_cover(file.file.read(MAX_COVER+1),directory/'covers')
        library.save(book,identifier)
        return book.to_dict()

    @app.get('/projects/{identifier}/cover')
    def get_cover(identifier:str):
        from fastapi.responses import FileResponse
        book=load(identifier)
        if not book.cover_id:raise HTTPException(404,'Обложка не задана')
        return FileResponse(directory/'covers'/(book.cover_id+'.jpg'),media_type='image/jpeg')

    @app.delete("/projects/{identifier}")
    def delete(identifier: str):
        load(identifier)
        studio.delete_project(identifier)
        return {"ok": True}

    @app.post("/import")
    def import_file(file: UploadFile = File(...)):
        suffix = Path(file.filename or "").suffix.lower()
        if suffix not in {".epub", ".fb2", ".pdf"}:
            raise ValueError("Выберите EPUB, FB2 или PDF")
        temp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as out:
                temp = Path(out.name)
                total = 0
                while data := file.file.read(1024 * 1024):
                    total += len(data)
                    if total > 100 * 1024 * 1024:
                        raise ValueError("Книга превышает 100 МБ")
                    out.write(data)
            book = import_book(temp)
            try:
                cover=extract_cover(temp)
                if cover:book.cover_id=store_cover(cover,directory/'covers')
            except Exception:
                book.warnings.append('Встроенную обложку прочитать не удалось. Её можно добавить вручную')
            if book.title == temp.stem:
                book.title = Path(file.filename).stem
            return {"id": library.save(book), "book": book.to_dict()}
        except (ValueError, HTTPException):
            raise
        except Exception:
            raise HTTPException(400, "Не удалось разобрать книгу. Проверьте формат и целостность файла")
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    @app.post("/projects/{identifier}/split")
    def split(identifier: str, body: Split):
        book = load(identifier)
        if body.chapter >= len(book.chapters):
            raise ValueError("Глава не найдена")
        split_chapter(book, body.chapter, body.at, body.title)
        library.save(book, identifier)
        return book.to_dict()

    @app.post("/projects/{identifier}/merge/{index}")
    def merge(identifier: str, index: int):
        book = load(identifier)
        merge_chapters(book, index)
        library.save(book, identifier)
        return book.to_dict()

    @app.post("/projects/{identifier}/prepare")
    def prepare(identifier: str, body: Prepare):
        book = load(identifier)
        if body.chapter >= len(book.chapters):
            raise ValueError("Глава не найдена")
        chapter = book.chapters[body.chapter]
        book.preparation=body.model_dump(exclude={'chapter'})
        library.save(book,identifier)
        text = prepare_text(spoken_blocks(book,chapter.start,chapter.end), remove_notes=body.remove_notes, numbers=body.numbers, auto_stress=body.auto_stress, dictionary=body.dictionary, remove_page_furniture=body.remove_page_furniture,language=chapter.language)
        return {"text": text, "chunks": chunk_text(text)}

    if ui_directory is not None:
        from fastapi.staticfiles import StaticFiles
        app.mount('/preview',StaticFiles(directory=ui_directory,html=True),name='preview')
    return app


def from_environment():
    return create_app(Path(os.getenv("BOOKS_DATA", "data/library")), os.environ["BOOKS_LOCAL_TOKEN"])
