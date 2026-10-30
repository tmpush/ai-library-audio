"""Download two freely distributed complete classics and validate local preparation."""
import hashlib,json,sys,time,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from client.engine.books import import_book,validate_chapters,prepare_text,chunk_text,split_chapter,merge_chapters
from client.engine.storage import Library

root=Path(__file__).resolve().parents[1]
destination=root/'data/acceptance-books';destination.mkdir(parents=True,exist_ok=True)
report_dir=root/'artifacts/full-books';report_dir.mkdir(parents=True,exist_ok=True)
sources=[('war-and-peace.epub','https://tolstoy.ru/upload/iblock/dac/voina-i-mir.epub','https://tolstoy.ru/creativity/fiction/1071/'),
         ('crime-and-punishment.epub','https://cdn.bencrowder.net/downloads/CrimeAndPunishmentRussian.epub','https://bencrowder.net/crime-punishment-russian/')]
results=[]
for name,url,source in sources:
    path=destination/name
    if not path.exists():
        with urllib.request.urlopen(url,timeout=60) as response:blob=response.read(20*1024*1024+1)
        assert len(blob)<=20*1024*1024
        path.write_bytes(blob)
    started=time.perf_counter();book=import_book(path)
    validate_chapters(book.chapters,len(book.blocks))
    original=book.to_dict()
    total=0;chunks=0;languages={};first_probe=None
    for ch in book.chapters:
        languages[ch.language]=languages.get(ch.language,0)+1
        # Normalisation is only enabled for the languages supported by num2words.
        text=prepare_text(book.blocks[ch.start:ch.end],language=ch.language,numbers=ch.language in {'ru','en','de','fr','es','it','pt'})
        parts=chunk_text(text)
        assert all(0<len(x)<=250 for x in parts)
        assert ''.join(text.split())==''.join(''.join(parts).split())
        chunks+=len(parts);total+=len(text)
        if first_probe is None and ch.language=='ru' and len(text)>2000:
            first_probe=next((p for p in parts if len(p)>120 and not p.startswith('[[')),parts[0])
    assert original==book.to_dict()
    assert total>500000,'Not a complete long novel'
    lib=Library(report_dir/name.removesuffix('.epub'));ident=lib.save(book)
    loaded=lib.load(ident);assert loaded.to_dict()==original
    eligible=next(i for i,c in enumerate(loaded.chapters) if c.end-c.start>2)
    split_chapter(loaded,eligible,loaded.chapters[eligible].start+1,'Проверка границы')
    merge_chapters(loaded,eligible)
    assert loaded.to_dict()==original
    result={'file':str(path),'source':source,'download':url,'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'title':book.title,'blocks':len(book.blocks),'chapters':len(book.chapters),'characters':total,'chunks':chunks,'languages':languages,
            'warnings':book.warnings,'elapsed_seconds':time.perf_counter()-started,'roundtrip':True,'split_merge':True,'content_preserved':True}
    results.append(result)
    (report_dir/(name+'.probe.txt')).write_text(first_probe or '',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)
(report_dir/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
