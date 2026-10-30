"""Local book parsing. Input files are never modified or uploaded."""
from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from defusedxml import ElementTree as ET
from .numbers import normalize_numbers
from .pronunciation import normalize_symbols
from pypdf import PdfReader
from langdetect import detect_langs, DetectorFactory, LangDetectException
DetectorFactory.seed = 0

MAX_INPUT = 100 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024


@dataclass
class Block:
    text: str
    kind: str = "paragraph"
    source: str = ""


@dataclass
class Chapter:
    title: str
    start: int
    end: int
    language: str = "ru"


@dataclass
class Book:
    title: str
    author: str
    source_hash: str
    blocks: list[Block]
    chapters: list[Chapter]
    warnings: list[str] = field(default_factory=list)
    spoken_overrides: dict[str, str] = field(default_factory=dict)
    preparation: dict = field(default_factory=dict)
    cover_id: str = ''

    def to_dict(self):
        return asdict(self)


def detect_chapters(blocks: list[Block]) -> list[Chapter]:
    starts = [i for i, block in enumerate(blocks) if block.kind == "heading"]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    chapters = [Chapter(blocks[start].text if blocks[start].kind == "heading" else "Начало",
                    start, starts[i + 1] if i + 1 < len(starts) else len(blocks))
            for i, start in enumerate(starts)] if blocks else []
    previous_language = 'ru'
    for chapter in chapters:
        text=' '.join(b.text for b in blocks[chapter.start:chapter.end])[:5000]
        if len(re.findall(r'[^\W\d_]', text)) < 40:
            chapter.language = previous_language
            continue
        try:
            detected=detect_langs(text)
            language=detected[0].lang
            if detected[0].prob>=.8:
                chapter.language={'zh-cn':'zh','zh-tw':'zh'}.get(language,language)
        except LangDetectException:
            pass
        previous_language = chapter.language
    return chapters


def spoken_blocks(book: Book, start: int, end: int) -> list[Block]:
    return [Block(book.spoken_overrides.get(str(i),block.text),block.kind,block.source)
            for i,block in enumerate(book.blocks) if start<=i<end]


def validate_chapters(chapters: list[Chapter], block_count: int):
    cursor = 0
    for chapter in chapters:
        if not chapter.title.strip() or chapter.start != cursor or not chapter.start < chapter.end <= block_count:
            raise ValueError("Главы должны покрывать текст по порядку без пропусков и пересечений")
        cursor = chapter.end
    if cursor != block_count:
        raise ValueError("Не весь текст распределён по главам")


def split_chapter(book: Book, chapter_index: int, at: int, title: str):
    chapter = book.chapters[chapter_index]
    if not chapter.start < at < chapter.end or not title.strip():
        raise ValueError("Выберите границу внутри главы и укажите название")
    right = Chapter(title.strip(), at, chapter.end, chapter.language)
    chapter.end = at
    book.chapters.insert(chapter_index + 1, right)


def merge_chapters(book: Book, index: int):
    if not 0 <= index < len(book.chapters) - 1:
        raise ValueError("Нет следующей главы")
    book.chapters[index].end = book.chapters[index + 1].end
    del book.chapters[index + 1]


def clean_space(text: str) -> str:
    return re.sub(r"[\t\r\n\u00a0 ]+", " ", text).strip()


def _fb2(path: Path):
    root = ET.fromstring(path.read_bytes())
    ns = {"f": "http://www.gribuser.ru/xml/fictionbook/2.0"}
    info = root.find("f:description/f:title-info", ns)
    title = info.findtext("f:book-title", "", ns) if info is not None else ""
    authors = []
    if info is not None:
        for author in info.findall("f:author", ns):
            authors.append(" ".join((author.findtext(f"f:{part}", "", ns) or "").strip()
                                    for part in ("first-name", "middle-name", "last-name")).strip())
    blocks = []

    def walk(node, source, note=False):
        tag = node.tag.split("}")[-1]
        if tag in {"title", "subtitle", "p", "v", "text-author"}:
            text = clean_space(" ".join(''.join(child.itertext()) for child in node) if tag=='title' and len(node) else "".join(node.itertext()))
            if text:
                blocks.append(Block(text, "note" if note else "heading" if tag in {"title", "subtitle"} else "paragraph", source))
            return
        for child in node:
            walk(child, source, note)

    for body in root.findall("f:body", ns):
        is_note = body.get("name", "").lower() in {"notes", "comments"}
        walk(body, "fb2:" + body.get("name", "body"), is_note)
    return title, "; ".join(authors), blocks, []


def _epub(path: Path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 10000 or sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("EPUB слишком велик после распаковки")
        if len({e.filename for e in entries}) != len(entries):
            raise ValueError("EPUB содержит повторяющиеся пути")
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        package_path = next((n.get("full-path") for n in container.iter() if n.tag.endswith("}rootfile")), None)
        if not package_path:
            raise ValueError("В EPUB отсутствует пакет книги")
        package = ET.fromstring(archive.read(package_path))
        title = next((n.text for n in package.iter() if n.tag.endswith("}title")), "")
        author = "; ".join(n.text or "" for n in package.iter() if n.tag.endswith("}creator"))
        manifest = {n.get("id"): n for n in package.iter() if n.tag.endswith("}item")}
        blocks = []
        for ref in package.iter():
            if not ref.tag.endswith("}itemref") or ref.get("linear") == "no":
                continue
            item = manifest.get(ref.get("idref"))
            if item is None:
                raise ValueError("Повреждено оглавление EPUB")
            href = unquote(item.get("href", "").split("#")[0])
            name = posixpath.normpath(posixpath.join(posixpath.dirname(package_path), href))
            if name.startswith(("../", "/")) or ":" in name:
                raise ValueError("Недопустимый путь внутри EPUB")
            soup = BeautifulSoup(archive.read(name), "html.parser")
            for node in soup.select("script, style, nav"):
                node.decompose()
            for node in soup.find_all('br'):
                node.replace_with(' ')
            body = soup.body or soup
            def append(node,text,heading=False):
                text=clean_space(text)
                note = any("footnote" in str(a.get("epub:type", "")) or a.get("role") == "doc-footnote"
                           or re.fullmatch(r'note\d+', str(a.get('id','')), re.I)
                           for a in [node, *node.parents] if getattr(a, "attrs", None))
                if text:
                    blocks.append(Block(text, "note" if note else "heading" if heading else "paragraph", name))
            structural={'div','section','article','main','aside','header','footer','ul','ol','li','blockquote','table','thead','tbody','tr','td','th','pre','p','h1','h2','h3','h4','h5','h6'}
            def walk(node):
                if node.name in {'h1','h2','h3','h4','h5','h6','p','pre'}:
                    append(node,node.get_text('',strip=False),node.name.startswith('h'))
                    return
                pending=[]
                for child in node.children:
                    if getattr(child,'name',None) in structural:
                        append(node,''.join(pending));pending=[]
                        walk(child)
                    elif getattr(child,'name',None):
                        pending.append(child.get_text('',strip=False))
                    elif child.__class__.__name__!='Comment':
                        pending.append(str(child))
                append(node,''.join(pending))
            walk(body)
        return title, author, blocks, []


def _pdf(path: Path):
    reader = PdfReader(path)
    if reader.is_encrypted:
        raise ValueError("PDF защищён паролем. Импортируйте незашифрованную копию")
    blocks, warnings = [], ["Границы глав PDF определены эвристически; проверьте порядок текста и колонтитулы"]
    edges=[]
    edge_indices=set()
    for number, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if not text.strip():
            warnings.append(f"Страница {number}: нет текстового слоя; OCR не поддерживается")
        page_lines=[clean_space(line) for line in text.splitlines() if clean_space(line)]
        edges.extend(set(page_lines[:2]+page_lines[-2:]))
        for index,line in enumerate(page_lines):
            line = clean_space(line)
            if line:
                heading = bool(re.match(r"^(глава|часть|пролог|эпилог|chapter|part)\b", line, re.I)) and len(line) < 140
                page_number = (index<2 or index>=len(page_lines)-2) and bool(re.fullmatch(r'[-–— ]*\d{1,5}[-–— ]*',line))
                if index<2 or index>=len(page_lines)-2:edge_indices.add(len(blocks))
                blocks.append(Block(line, "page_number" if page_number else "heading" if heading else "paragraph", f"page:{number}"))
    from collections import Counter
    repeated={line for line,count in Counter(edges).items() if count>=3 and len(line)<120}
    for index,block in enumerate(blocks):
        if index in edge_indices and block.text in repeated and block.kind=='paragraph':block.kind='header'
    return (reader.metadata.title or "") if reader.metadata else "", (reader.metadata.author or "") if reader.metadata else "", blocks, warnings


def import_book(path: str | Path) -> Book:
    path = Path(path)
    if path.stat().st_size > MAX_INPUT:
        raise ValueError("Книга превышает лимит 100 МБ")
    parser = {".fb2": _fb2, ".epub": _epub, ".pdf": _pdf}.get(path.suffix.lower())
    if parser is None:
        raise ValueError("Поддерживаются EPUB, FB2 и PDF с текстовым слоем")
    title, author, blocks, warnings = parser(path)
    if not blocks:
        raise ValueError("В книге не найден текст. Для PDF нужен текстовый слой; OCR не поддерживается")
    return Book(title or path.stem, author, hashlib.sha256(path.read_bytes()).hexdigest(), blocks, detect_chapters(blocks), warnings)


def prepare_text(blocks: list[Block], *, remove_notes=False, numbers=False, dictionary=None, remove_page_furniture=False, language='ru', auto_stress=False) -> str:
    lines = [clean_space(b.text) for b in blocks if not (remove_notes and b.kind == "note") and not (remove_page_furniture and b.kind in {'header','page_number'})]
    text = "\n\n".join(lines)
    if numbers:
        text = normalize_numbers(text, language)
    rules = {k:v for k,v in (dictionary or {}).items() if k}
    saved = []
    if rules:
        # One pass: replacement text must not trigger another pronunciation rule.
        pattern = r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(rules,key=len,reverse=True)) + r")(?!\w)"
        def protect(match):
            saved.append(rules[match.group()])
            return '\ue200' + str(len(saved)-1) + '\ue201'
        text = re.sub(pattern, protect, text)
    text = normalize_symbols(text, language)
    text = re.sub('\ue200(\d+)\ue201', lambda m: saved[int(m[1])], text)
    if auto_stress:
        from .stress import automatic_stress
        text = automatic_stress(text, language)
    return text


def chunk_text(text: str, limit: int = 250) -> list[str]:
    result=[]
    for part in re.split(r'(\[\[pause:\d+\]\])',text):
        match=re.fullmatch(r'\[\[pause:(\d+)\]\]',part)
        if match:
            if not 50<=int(match[1])<=10000:raise ValueError('Пауза должна быть от 50 до 10000 мс')
            result.append(part)
        else:result.extend(_chunk_plain(part,limit))
    return result


def _chunk_plain(text: str, limit: int = 250) -> list[str]:
    if limit < 20:
        raise ValueError("Минимальный размер фрагмента — 20 символов")
    remaining = text.strip()
    result = []
    while remaining:
        if len(remaining) <= limit:
            result.append(remaining)
            break
        candidate = remaining[:limit + 1]
        boundaries = list(re.finditer(r"[.!?…][»\"]?\s+|\n\n", candidate))
        cut = boundaries[-1].end() if boundaries else candidate.rfind(" ")
        if cut < limit // 3:
            cut = limit
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    return result
